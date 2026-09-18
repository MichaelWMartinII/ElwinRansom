"""Morning briefing: assemble and deliver a daily summary.

Run directly with: python -m companion.briefing
"""

from datetime import date, datetime, timedelta, timezone

from . import calendar_sync, db, dory_bridge, weather
from .reminder import send_telegram


# Sections shown unless changed from chat; "todos" is opt-in.
SECTIONS = ("weather", "calendar", "reminders", "todos", "dory")
DEFAULT_SECTIONS = ("weather", "calendar", "reminders", "dory")


def assemble(
    conn,
    greeting: str = "",
    sections: tuple[str, ...] | list[str] = DEFAULT_SECTIONS,
    extras: tuple[str, ...] | list[str] = (),
) -> str:
    """Build the briefing from real data only, opening with *greeting* if given.

    *sections* picks which sources to include; *extras* are lines Michael
    asked to have added.
    """
    now_local = datetime.now().astimezone()
    date_str = now_local.strftime("%A, %B %-d")
    lines = [greeting or f"Good morning, Michael. {date_str}.", ""]

    if extras:
        lines.append("📌 Yours:")
        lines.extend(f"• {item}" for item in extras)
        lines.append("")

    # Weather
    forecast = weather.forecast() if "weather" in sections else []
    if forecast:
        lines.append("🌤 Weather:")
        lines.extend(f"• {line}" for line in weather.summary_lines(forecast))
        lines.append("")

    # Today's events
    events = calendar_sync.all_todays_events(conn) if "calendar" in sections else []
    if events:
        lines.append("📅 Today:")
        for ev in events:
            if ev.get("all_day"):
                lines.append(f"• All day  {ev['title']}")
                continue
            dt_start = datetime.fromisoformat(ev["start_at"]).astimezone()
            start_str = dt_start.strftime("%-H:%M")
            if ev.get("end_at"):
                dt_end = datetime.fromisoformat(ev["end_at"]).astimezone()
                end_str = dt_end.strftime("%-H:%M")
                lines.append(f"• {start_str}–{end_str}  {ev['title']}")
            else:
                lines.append(f"• {start_str}  {ev['title']}")
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

    pending = db.get_pending_reminders(conn) if "reminders" in sections else []
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

    todos = db.get_pending_todos(conn) if "todos" in sections else []
    if todos:
        lines.append(f"✅ Tasks ({len(todos)} pending):")
        lines.extend(f"• {t['content']}" for t in todos[:10])
        lines.append("")

    dory_signals = _dory_signals() if "dory" in sections else []
    if dory_signals:
        lines.append("🧠 Dory signals:")
        lines.extend(dory_signals)
        lines.append("")

    return "\n".join(lines).rstrip()


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


if __name__ == "__main__":
    _conn = db.init_db()
    send(_conn)
