"""Butler marker parsing, event scheduling, and launchd prep alerts."""

import plistlib
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

_LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"

# ── Marker regexes ────────────────────────────────────────────

# [EVENT_ADD: 2026-02-19 14:00 | 2026-02-19 15:00 | Title]  (end time optional)
_EVENT_RE = re.compile(
    r'\[EVENT_ADD:\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})'
    r'(?:\s*\|\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}))?'
    r'\s*\|\s*(.+?)\s*\]',
    re.IGNORECASE,
)

# [TODO_ADD: high | content]  (priority optional, defaults to medium)
_TODO_ADD_RE = re.compile(
    r'\[TODO_ADD:\s*(?:(high|medium|low)\s*\|\s*)?(.+?)\s*\]',
    re.IGNORECASE,
)

# [TODO_DONE: partial content]
_TODO_DONE_RE = re.compile(
    r'\[TODO_DONE:\s*(.+?)\s*\]',
    re.IGNORECASE,
)

# [NOTE: content]
_NOTE_RE = re.compile(
    r'\[NOTE:\s*(.+?)\s*\]',
    re.IGNORECASE,
)


# ── Event marker ──────────────────────────────────────────────

def extract_event(text: str) -> tuple[str, str | None, str] | None:
    """Parse an [EVENT_ADD: ...] marker from model output.

    Returns (start_utc, end_utc_or_None, title) or None.
    Times are stored as UTC ISO8601.
    """
    m = _EVENT_RE.search(text)
    if not m:
        return None
    start_date, start_time = m.group(1), m.group(2)
    end_date, end_time = m.group(3), m.group(4)
    title = m.group(5).strip()
    try:
        dt_start = datetime.strptime(
            f"{start_date} {start_time}", "%Y-%m-%d %H:%M"
        ).astimezone().astimezone(timezone.utc)
        start_utc = dt_start.isoformat()

        end_utc = None
        if end_date and end_time:
            dt_end = datetime.strptime(
                f"{end_date} {end_time}", "%Y-%m-%d %H:%M"
            ).astimezone().astimezone(timezone.utc)
            end_utc = dt_end.isoformat()

        return start_utc, end_utc, title
    except ValueError:
        return None


def strip_event_marker(text: str) -> str:
    """Remove any [EVENT_ADD: ...] marker from text."""
    return _EVENT_RE.sub("", text).strip()


# ── Todo markers ──────────────────────────────────────────────

def extract_todo_add(text: str) -> tuple[str, str] | None:
    """Parse a [TODO_ADD: ...] marker.

    Returns (priority, content) or None.
    Priority defaults to 'medium' if not specified.
    """
    m = _TODO_ADD_RE.search(text)
    if not m:
        return None
    priority = (m.group(1) or "medium").lower()
    content = m.group(2).strip()
    return priority, content


def strip_todo_add_marker(text: str) -> str:
    return _TODO_ADD_RE.sub("", text).strip()


def extract_todo_done(text: str) -> str | None:
    """Parse a [TODO_DONE: ...] marker. Returns the partial text or None."""
    m = _TODO_DONE_RE.search(text)
    if not m:
        return None
    return m.group(1).strip()


def strip_todo_done_marker(text: str) -> str:
    return _TODO_DONE_RE.sub("", text).strip()


# ── Note marker ───────────────────────────────────────────────

def extract_note(text: str) -> str | None:
    """Parse a [NOTE: ...] marker. Returns the note content or None."""
    m = _NOTE_RE.search(text)
    if not m:
        return None
    return m.group(1).strip()


def strip_note_marker(text: str) -> str:
    return _NOTE_RE.sub("", text).strip()


def strip_all_butler_markers(text: str) -> str:
    """Remove all butler markers (EVENT_ADD, TODO_ADD, TODO_DONE, NOTE)."""
    text = _EVENT_RE.sub("", text)
    text = _TODO_ADD_RE.sub("", text)
    text = _TODO_DONE_RE.sub("", text)
    text = _NOTE_RE.sub("", text)
    return text.strip()


# ── Event scheduling ──────────────────────────────────────────

def schedule_prep(event_id: str, start_utc: str) -> bool:
    """Write a launchd plist that fires 15 minutes before the event.

    Calls `python -m companion.fire_prep <event_id>`.
    Returns True on success, False if prep time is already past.
    """
    dt_utc = datetime.fromisoformat(start_utc)
    dt_prep_utc = dt_utc - timedelta(minutes=15)

    if dt_prep_utc < datetime.now(timezone.utc):
        return False

    dt_prep_local = dt_prep_utc.astimezone()
    label = f"com.elwin.prep.{event_id}"
    plist_file = _LAUNCH_AGENTS / f"{label}.plist"
    _LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)

    plist_data = {
        "Label": label,
        "ProgramArguments": [
            sys.executable,
            "-m", "companion.fire_prep",
            event_id,
        ],
        "WorkingDirectory": str(config._ROOT),
        "StartCalendarInterval": {
            "Month": dt_prep_local.month,
            "Day": dt_prep_local.day,
            "Hour": dt_prep_local.hour,
            "Minute": dt_prep_local.minute,
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


def unschedule_prep(event_id: str) -> None:
    """Unload and delete the launchd prep plist for an event."""
    label = f"com.elwin.prep.{event_id}"
    plist_file = _LAUNCH_AGENTS / f"{label}.plist"
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


# ── Display formatting ────────────────────────────────────────

def format_schedule(events: list[dict]) -> str:
    """Format a list of events as a compact display string."""
    if not events:
        return "No upcoming events."
    lines = []
    for ev in events:
        dt_start = datetime.fromisoformat(ev["start_at"]).astimezone()
        start_str = dt_start.strftime("%Y-%m-%d %-H:%M")
        if ev.get("end_at"):
            dt_end = datetime.fromisoformat(ev["end_at"]).astimezone()
            end_str = dt_end.strftime("%-H:%M")
            lines.append(f"• {start_str}–{end_str}  {ev['title']}")
        else:
            lines.append(f"• {start_str}  {ev['title']}")
    return "\n".join(lines)
