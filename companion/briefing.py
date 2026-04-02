"""Morning briefing: assemble and deliver a daily summary.

Run directly with: python -m companion.briefing
"""

import plistlib
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config, db, dory_bridge
from .reminder import send_telegram

_LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
_BRIEFING_PLIST = _LAUNCH_AGENTS / "com.elwin.briefing.plist"


def _fetch_weather() -> str:
    location = (config.LOCATION or "Murfreesboro, TN").replace(" ", "+").replace(",", "")
    url = f"https://wttr.in/{location}?format=3"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def assemble(conn) -> str:
    """Build the morning briefing string."""
    now_local = datetime.now().astimezone()
    date_str = now_local.strftime("%A, %B %-d")
    lines = [f"Good morning, Michael. {date_str}.", ""]

    # Weather
    weather = _fetch_weather()
    if weather:
        lines.append(f"🌤 {weather}")
        lines.append("")

    # Today's events
    events = db.get_todays_events(conn)
    if events:
        lines.append("📅 Today:")
        for ev in events:
            dt_start = datetime.fromisoformat(ev["start_at"]).astimezone()
            start_str = dt_start.strftime("%-H:%M")
            if ev.get("end_at"):
                dt_end = datetime.fromisoformat(ev["end_at"]).astimezone()
                end_str = dt_end.strftime("%-H:%M")
                lines.append(f"• {start_str}–{end_str}  {ev['title']}")
            else:
                lines.append(f"• {start_str}  {ev['title']}")
        lines.append("")

    # Pending todos
    todos = db.get_pending_todos(conn)
    if todos:
        priority_map = {"high": "H", "medium": "M", "low": "L"}
        lines.append(f"✅ Tasks ({len(todos)} pending):")
        for t in todos[:10]:
            p = priority_map.get(t["priority"], "M")
            lines.append(f"• [{p}] {t['content']}")
        lines.append("")

    # Pending reminders due today (in local timezone)
    local_tz = now_local.tzinfo
    today_date = now_local.date()
    tomorrow_date = today_date + timedelta(days=1)
    today_start_utc = datetime(
        today_date.year, today_date.month, today_date.day, tzinfo=local_tz
    ).astimezone(timezone.utc).isoformat()
    tomorrow_start_utc = datetime(
        tomorrow_date.year, tomorrow_date.month, tomorrow_date.day, tzinfo=local_tz
    ).astimezone(timezone.utc).isoformat()

    pending = db.get_pending_reminders(conn)
    todays_reminders = [
        r for r in pending
        if today_start_utc <= r["due_at"] < tomorrow_start_utc
    ]
    if todays_reminders:
        lines.append("⏰ Reminders today:")
        for r in todays_reminders:
            dt_local = datetime.fromisoformat(r["due_at"]).astimezone()
            time_str = dt_local.strftime("%-H:%M")
            lines.append(f"• {time_str}  {r['message']}")
        lines.append("")

    focus = _focus_suggestion(events or [], todos or [], todays_reminders)
    if focus:
        lines.append("🎯 First move:")
        lines.append(focus)
        lines.append("")

    dory_signals = _dory_signals()
    if dory_signals:
        lines.append("🧠 Dory signals:")
        lines.extend(dory_signals)
        lines.append("")

    return "\n".join(lines).rstrip()


def _focus_suggestion(events: list[dict], todos: list[dict], reminders: list[dict]) -> str:
    now_local = datetime.now().astimezone()
    for ev in events:
        dt_start = datetime.fromisoformat(ev["start_at"]).astimezone()
        delta_min = int((dt_start - now_local).total_seconds() / 60)
        if 0 <= delta_min <= 90:
            return f"Prep for {ev['title']} at {dt_start.strftime('%-H:%M')}."
    for reminder in reminders:
        dt_local = datetime.fromisoformat(reminder["due_at"]).astimezone()
        delta_min = int((dt_local - now_local).total_seconds() / 60)
        if 0 <= delta_min <= 180:
            return f"Handle reminder by {dt_local.strftime('%-H:%M')}: {reminder['message']}"
    for todo in todos:
        if todo.get("priority") == "high":
            return f"Take one concrete step on: {todo['content']}"
    if todos:
        return f"Clear one medium-friction task early: {todos[0]['content']}"
    return ""


def _dory_signals() -> list[str]:
    summary = dory_bridge.query("What seems most important for Michael today?")
    if not summary:
        return []
    lines = [line.strip() for line in summary.splitlines() if line.strip()]
    return [f"• {line.lstrip('- ').strip()}" for line in lines[:4]]


def send(conn) -> None:
    """Assemble and deliver the briefing via Telegram and Web Push."""
    text = assemble(conn)
    if not text:
        return
    send_telegram(text)
    try:
        from . import web_push
        web_push.send_push(conn, title="Morning Briefing", body=text[:200].rstrip() + "...")
    except Exception:
        pass


def install() -> bool:
    """Write and load the daily briefing launchd plist if not already present.

    Fires at BRIEFING_HOUR:BRIEFING_MINUTE daily (no date = repeats every day).
    Returns True if installed or already present.
    """
    if _BRIEFING_PLIST.exists():
        return True

    _LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    label = "com.elwin.briefing"
    plist_data = {
        "Label": label,
        "ProgramArguments": [
            sys.executable,
            "-m", "companion.briefing",
        ],
        "WorkingDirectory": str(config._ROOT),
        "StartCalendarInterval": {
            "Hour": config.BRIEFING_HOUR,
            "Minute": config.BRIEFING_MINUTE,
        },
        "StandardOutPath": f"/tmp/{label}.log",
        "StandardErrorPath": f"/tmp/{label}.log",
    }

    with open(_BRIEFING_PLIST, "wb") as f:
        plistlib.dump(plist_data, f)

    try:
        subprocess.run(
            ["launchctl", "load", str(_BRIEFING_PLIST)],
            check=True, capture_output=True, timeout=5,
        )
        return True
    except Exception:
        return False


if __name__ == "__main__":
    _conn = db.init_db()
    send(_conn)
