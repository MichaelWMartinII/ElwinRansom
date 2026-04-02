"""Reminder parsing, launchd scheduling, and delivery helpers."""

import json
import plistlib
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import config

# Matches: [REMIND: 2026-02-19 15:30 | Take medication]
_MARKER_RE = re.compile(
    r'\[REMIND:\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*\|\s*(.+?)\s*\]',
    re.IGNORECASE,
)

_LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"


# ── Marker parsing ───────────────────────────────────────────

def extract_reminder(text: str) -> tuple[str, str] | None:
    """Parse a [REMIND: ...] marker from model output.

    Returns (due_at_utc_iso, message) or None if no marker found.
    Stores due_at as UTC ISO8601 for reliable comparison.
    """
    m = _MARKER_RE.search(text)
    if not m:
        return None
    date_str, time_str, message = m.group(1), m.group(2), m.group(3)
    try:
        dt_naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        dt_local = dt_naive.astimezone()           # attach local tzinfo
        dt_utc = dt_local.astimezone(timezone.utc)
        return dt_utc.isoformat(), message.strip()
    except ValueError:
        return None


def strip_marker(text: str) -> str:
    """Remove any [REMIND: ...] marker from text (for clean Telegram display)."""
    return _MARKER_RE.sub("", text).strip()


# ── Delivery (called by fire_reminder) ──────────────────────

def deliver_macos(message: str) -> None:
    script = f'display notification "{message}" with title "Elwin Ransom"'
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except Exception:
        pass


def send_telegram(text: str) -> None:
    """Send raw text to the owner via the Telegram Bot API (no prefix added)."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_OWNER_ID:
        return
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": config.TELEGRAM_OWNER_ID,
        "text": text,
    }).encode()
    try:
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def deliver_telegram(message: str) -> None:
    """Send a reminder message to the owner (prepends 'Reminder: ')."""
    send_telegram(f"Reminder: {message}")


def deliver_web_push(message: str) -> None:
    """Send a reminder via Web Push to all subscribed browsers."""
    try:
        from . import web_push, db as _db
        conn = _db.init_db()
        web_push.send_push(conn, title="Elwin Ransom", body=f"Reminder: {message}")
    except Exception:
        pass


# ── launchd scheduling ───────────────────────────────────────

def _plist_label(reminder_id: str) -> str:
    return f"com.elwin.reminder.{reminder_id}"


def _plist_path(reminder_id: str) -> Path:
    return _LAUNCH_AGENTS / f"com.elwin.reminder.{reminder_id}.plist"


def schedule(reminder_id: str, due_at_utc: str) -> bool:
    """Write a one-shot launchd plist and load it.

    The plist calls `python -m companion.fire_reminder <id>` at the due time.
    Returns True on success.
    """
    dt_utc = datetime.fromisoformat(due_at_utc)
    dt_local = dt_utc.astimezone()

    label = _plist_label(reminder_id)
    plist_file = _plist_path(reminder_id)
    _LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)

    plist_data = {
        "Label": label,
        "ProgramArguments": [
            sys.executable,
            "-m", "companion.fire_reminder",
            reminder_id,
        ],
        "WorkingDirectory": str(config._ROOT),
        "StartCalendarInterval": {
            "Month": dt_local.month,
            "Day": dt_local.day,
            "Hour": dt_local.hour,
            "Minute": dt_local.minute,
        },
        "StandardOutPath": f"/tmp/{label}.log",
        "StandardErrorPath": f"/tmp/{label}.log",
    }

    with open(plist_file, "wb") as f:
        plistlib.dump(plist_data, f)

    try:
        subprocess.run(
            ["launchctl", "load", str(plist_file)],
            check=True, capture_output=True, timeout=5,
        )
        return True
    except Exception:
        return False


def unschedule(reminder_id: str) -> None:
    """Unload and delete the launchd plist for a reminder."""
    plist_file = _plist_path(reminder_id)
    try:
        subprocess.run(
            ["launchctl", "unload", str(plist_file)],
            check=False, capture_output=True, timeout=5,
        )
    except Exception:
        pass
    try:
        plist_file.unlink(missing_ok=True)
    except Exception:
        pass
