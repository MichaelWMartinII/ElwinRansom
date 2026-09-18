"""Wake-up alarm: a scheduled Telegram alarm that carries the morning briefing.

Settings live in the settings table so they can be changed from chat:
  alarm_time     "HH:MM", local 24-hour
  alarm_days     "daily" | "weekdays" | "weekends" | comma list like "mon,wed,fri"
  alarm_enabled  "1" | "0"
  alarm_message  optional custom wake-up line ("" = default)
  alarm_sections which briefing sources to include (see briefing.SECTIONS)
  alarm_extras   JSON list of lines Michael asked to add

The model changes them by emitting a marker (see prompts._ALARM_INSTRUCTIONS):
  [ALARM: 06:30]   [ALARM: 07:15 | weekdays]   [ALARM: 06:45 | daily | Rise and shine]
  [ALARM: off]     [ALARM: on]
  [ALARM_ADD: todos]   [ALARM_ADD: Take your vitamins]   (a section name, or any line)
  [ALARM_REMOVE: weather]   [ALARM_REMOVE: vitamins]     (section, or part of a line)

Nothing is included that doesn't come from real data or from Michael.

launchd runs `python -m companion.alarm` at the set time.
"""

import json
import logging
import plistlib
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import briefing, calendar_sync, config, db, weather
from .reminder import send_telegram, send_telegram_voice

logger = logging.getLogger(__name__)

_LABEL = "com.elwin.alarm"
_LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
_PLIST = _LAUNCH_AGENTS / f"{_LABEL}.plist"
_LEGACY_PLIST = _LAUNCH_AGENTS / "com.elwin.briefing.plist"

_DAY_NUMS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
_DAY_SETS = {
    "daily": [],
    "weekdays": [1, 2, 3, 4, 5],
    "weekends": [0, 6],
}

# [ALARM: 06:30 | weekdays | message]  or  [ALARM: off] / [ALARM: on]
_MARKER_RE = re.compile(r"\[ALARM:\s*([^\]|]+?)\s*(?:\|\s*([^\]|]*?)\s*)?(?:\|\s*([^\]]*?)\s*)?\]", re.IGNORECASE)
_ADD_RE = re.compile(r"\[ALARM_ADD:\s*(.+?)\s*\]", re.IGNORECASE)
_REMOVE_RE = re.compile(r"\[ALARM_REMOVE:\s*(.+?)\s*\]", re.IGNORECASE)
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*(am|pm)?$", re.IGNORECASE)


# ── Settings ─────────────────────────────────────────────────

def get_settings(conn) -> dict:
    return {
        "time": db.get_setting(conn, "alarm_time", config.ALARM_TIME),
        "days": db.get_setting(conn, "alarm_days", "daily"),
        "enabled": db.get_setting(conn, "alarm_enabled", "1") == "1",
        "message": db.get_setting(conn, "alarm_message", ""),
        "sections": db.get_setting(
            conn, "alarm_sections", ",".join(briefing.DEFAULT_SECTIONS)
        ).split(","),
        "extras": json.loads(db.get_setting(conn, "alarm_extras", "[]")),
    }


def _parse_time(text: str) -> str | None:
    m = _TIME_RE.match(text.strip())
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _parse_days(text: str) -> str | None:
    text = text.strip().lower()
    if text in _DAY_SETS:
        return text
    days = [d.strip()[:3] for d in re.split(r"[,\s]+", text) if d.strip()]
    if days and all(d in _DAY_NUMS for d in days):
        return ",".join(sorted(set(days), key=lambda d: (_DAY_NUMS[d] - 1) % 7))
    return None


def _weekdays(days: str) -> list[int]:
    if days in _DAY_SETS:
        return _DAY_SETS[days]
    return [_DAY_NUMS[d] for d in days.split(",")]


def describe(settings: dict) -> str:
    hour, minute = map(int, settings["time"].split(":"))
    time_str = datetime(2000, 1, 1, hour, minute).strftime("%-I:%M %p")
    days = settings["days"]
    days_str = {
        "daily": "every day",
        "weekdays": "weekdays",
        "weekends": "weekends",
    }.get(days, ", ".join(d.capitalize() for d in days.split(",")))
    state = "on" if settings["enabled"] else "OFF"
    text = f"{time_str}, {days_str} ({state})"
    if settings["message"]:
        text += f' — "{settings["message"]}"'
    sections = [s for s in settings["sections"] if s]
    text += f"; includes: {', '.join(sections) or 'nothing but the time'}"
    if settings["extras"]:
        text += "; your lines: " + " / ".join(settings["extras"])
    return text


def prompt_state(settings: dict) -> str:
    """The alarm's exact state, laid out so the model can't blur it."""
    hour, minute = map(int, settings["time"].split(":"))
    included = [s for s in briefing.SECTIONS if s in settings["sections"]]
    excluded = [s for s in briefing.SECTIONS if s not in included]
    days = settings["days"]
    return "\n".join([
        f"- Time: {datetime(2000, 1, 1, hour, minute).strftime('%-I:%M %p')}",
        f"- Days: {'every day' if days == 'daily' else days}",
        f"- Status: {'on' if settings['enabled'] else 'off'}",
        f"- Wake-up line: {settings['message'] or 'Time to get up. (default)'}",
        f"- Sections included: {', '.join(included) or 'none'}",
        f"- Sections NOT included: {', '.join(excluded) or 'none'}",
        f"- His own added lines: {' / '.join(settings['extras']) or 'none'}",
    ])


# ── Chat markers ─────────────────────────────────────────────

def _apply_add_remove(conn, response: str) -> list[str]:
    """Apply [ALARM_ADD: ...] / [ALARM_REMOVE: ...] markers; return notes on failures."""
    settings = get_settings(conn)
    sections = [s for s in settings["sections"] if s]
    extras = list(settings["extras"])
    notes = []
    for item in _ADD_RE.findall(response):
        key = item.strip().lower()
        if key in briefing.SECTIONS:
            if key not in sections:
                sections.append(key)
        elif item.strip() and item.strip() not in extras:
            extras.append(item.strip())
    for item in _REMOVE_RE.findall(response):
        key = item.strip().lower()
        if key in briefing.SECTIONS:
            sections = [s for s in sections if s != key]
            continue
        matches = [e for e in extras if key in e.lower()]
        if matches:
            extras = [e for e in extras if e not in matches]
        else:
            notes.append(f"Nothing in the alarm matched {item!r}.")
    ordered = [s for s in briefing.SECTIONS if s in sections]
    db.set_setting(conn, "alarm_sections", ",".join(ordered))
    db.set_setting(conn, "alarm_extras", json.dumps(extras))
    return notes


def handle_marker(conn, response: str) -> tuple[str, str | None]:
    """Apply [ALARM...] markers. Returns (cleaned_response, confirmation)."""
    notes = []
    if _ADD_RE.search(response) or _REMOVE_RE.search(response):
        notes = _apply_add_remove(conn, response)
        response = _REMOVE_RE.sub("", _ADD_RE.sub("", response))
        m = _MARKER_RE.search(response)
        if not m:
            confirm = f"Alarm: {describe(get_settings(conn))}"
            return response.strip(), "\n".join([confirm, *notes])
    m = _MARKER_RE.search(response)
    if not m:
        return response, None
    clean = _MARKER_RE.sub("", response).strip()
    first, days, message = m.group(1), m.group(2), m.group(3)

    if first.lower() in {"off", "on"}:
        db.set_setting(conn, "alarm_enabled", "1" if first.lower() == "on" else "0")
    else:
        time_str = _parse_time(first)
        if not time_str:
            return clean, f"Couldn't read alarm time {first!r}; alarm unchanged."
        parsed_days = _parse_days(days) if days else None
        if days and not parsed_days:
            return clean, f"Couldn't read alarm days {days!r}; alarm unchanged."
        db.set_setting(conn, "alarm_time", time_str)
        db.set_setting(conn, "alarm_enabled", "1")
        if parsed_days:
            db.set_setting(conn, "alarm_days", parsed_days)
        if message is not None:
            db.set_setting(conn, "alarm_message", message.strip())

    installed = install(conn)
    confirm = f"Alarm: {describe(get_settings(conn))}"
    if not installed:
        confirm += " (warning: couldn't load it into launchd)"
    return clean, "\n".join([confirm, *notes])


# ── launchd ──────────────────────────────────────────────────

def install(conn) -> bool:
    """Write the alarm plist to match current settings and (re)load it.

    Also removes the legacy fixed-time briefing plist. When the alarm is off,
    the plist is unloaded and deleted. Returns True on success.
    """
    for plist in (_LEGACY_PLIST, _PLIST):
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, timeout=5)
    _LEGACY_PLIST.unlink(missing_ok=True)

    settings = get_settings(conn)
    if not settings["enabled"]:
        _PLIST.unlink(missing_ok=True)
        return True

    hour, minute = map(int, settings["time"].split(":"))
    weekdays = _weekdays(settings["days"])
    if weekdays:
        interval = [{"Weekday": d, "Hour": hour, "Minute": minute} for d in weekdays]
    else:
        interval = {"Hour": hour, "Minute": minute}

    _LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    with open(_PLIST, "wb") as f:
        plistlib.dump({
            "Label": _LABEL,
            "ProgramArguments": [sys.executable, "-m", "companion.alarm"],
            "WorkingDirectory": str(config._ROOT),
            # launchd's default PATH lacks Homebrew, where ffmpeg lives.
            "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
            "StartCalendarInterval": interval,
            "StandardOutPath": f"/tmp/{_LABEL}.log",
            "StandardErrorPath": f"/tmp/{_LABEL}.log",
        }, f)

    try:
        subprocess.run(
            ["launchctl", "load", str(_PLIST)],
            check=True, capture_output=True, timeout=5,
        )
        return True
    except Exception:
        return False


# ── Firing ───────────────────────────────────────────────────

def fire(conn) -> None:
    """Deliver the alarm: briefing text, then a short spoken wake-up voice note."""
    settings = get_settings(conn)
    if not settings["enabled"]:
        return

    now = datetime.now()
    time_str = now.strftime("%-I:%M")
    wake_line = settings["message"] or "Time to get up."
    header = f"⏰ {time_str} — Good morning, Michael. {wake_line}\n{now.strftime('%A, %B %-d')}"
    sections = settings["sections"]
    extras = settings["extras"]
    send_telegram(briefing.assemble(conn, greeting=header, sections=sections, extras=extras))

    # Spoken version: only real data and Michael's own lines.
    spoken = [f"Good morning, Michael. It's {time_str}.", wake_line]
    spoken.extend(line.rstrip(".") + "." for line in extras)
    forecast = weather.forecast() if "weather" in sections else []
    if forecast:
        spoken.append(weather.spoken(forecast))
    events = calendar_sync.all_todays_events(conn) if "calendar" in sections else []
    if events:
        spoken.append(f"You have {len(events)} thing{'s' if len(events) != 1 else ''} on the calendar today.")
        timed = [e for e in events if not e.get("all_day")]
        if timed:
            first = timed[0]
            at = datetime.fromisoformat(first["start_at"]).astimezone().strftime("%-I:%M")
            spoken.append(f"First is {first['title']} at {at}.")
    todos = db.get_pending_todos(conn) if "todos" in sections else []
    if todos:
        spoken.append(f"You have {len(todos)} open task{'s' if len(todos) != 1 else ''}. First: {todos[0]['content']}.")
    try:
        from . import tts
        ogg = tts.synthesize(" ".join(spoken))
        try:
            send_telegram_voice(ogg)
        finally:
            Path(ogg).unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Alarm voice note failed: %s", exc)

    try:
        from . import web_push
        web_push.send_push(conn, title=f"⏰ {time_str}", body=wake_line)
    except Exception:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fire(db.init_db())
