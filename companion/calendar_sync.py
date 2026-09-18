"""Read-only calendar sync from private iCal (ICS) links, e.g. Google Calendar.

Set GOOGLE_CALENDAR_URLS / APPLE_CALENDAR_URLS in agent.conf.
Google: Settings → your calendar → Integrate calendar →
"Secret address in iCal format".
Apple: Calendar app → right-click the calendar → Share Calendar… →
"Public Calendar" → copy the webcal:// link.

Events come back in the same shape as db.get_todays_events rows
(title, start_at, end_at as ISO strings) plus all_day, so callers can
merge them with Elwin's own events.
"""

import logging
import threading
import time
import urllib.request
from datetime import date, datetime, time as dtime, timedelta

from . import config

logger = logging.getLogger(__name__)

_CACHE_SECONDS = 600
_cache: dict[str, tuple[float, bytes]] = {}
_lock = threading.Lock()


def enabled() -> bool:
    return bool(config.CALENDAR_ICS_URLS)


def _fetch(url: str) -> bytes | None:
    with _lock:
        hit = _cache.get(url)
        if hit and time.time() - hit[0] < _CACHE_SECONDS:
            return hit[1]
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = resp.read()
    except Exception as exc:
        # Don't log the URL: it's a secret.
        logger.warning("Calendar fetch failed: %s", type(exc).__name__)
        return hit[1] if hit else None
    with _lock:
        _cache[url] = (time.time(), data)
    return data


def _as_local(value) -> tuple[datetime, bool]:
    """Return (local aware datetime, is_all_day) for an ICS DTSTART/DTEND value."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.astimezone()
        return value.astimezone(), False
    return datetime.combine(value, dtime.min).astimezone(), True


def _sort_key(event: dict) -> tuple[bool, datetime]:
    return (not event["all_day"], datetime.fromisoformat(event["start_at"]))


def events_between(start: datetime, end: datetime) -> list[dict]:
    """Events overlapping [start, end), sorted with all-day events first."""
    if not enabled():
        return []
    import icalendar
    import recurring_ical_events

    events = []
    for url in config.CALENDAR_ICS_URLS:
        data = _fetch(url)
        if not data:
            continue
        try:
            cal = icalendar.Calendar.from_ical(data)
            occurrences = recurring_ical_events.of(cal).between(start, end)
        except Exception as exc:
            logger.warning("Calendar parse failed: %s", exc)
            continue
        for ev in occurrences:
            if str(ev.get("STATUS", "")).upper() == "CANCELLED":
                continue
            dt_start, all_day = _as_local(ev.decoded("DTSTART"))
            dt_end = _as_local(ev.decoded("DTEND"))[0] if ev.get("DTEND") else None
            events.append({
                "title": str(ev.get("SUMMARY", "(no title)")),
                "start_at": dt_start.isoformat(),
                "end_at": dt_end.isoformat() if dt_end and not all_day else None,
                "all_day": all_day,
                "location": str(ev.get("LOCATION", "")),
            })
    events.sort(key=_sort_key)
    return events


def todays_events() -> list[dict]:
    today = date.today()
    start = datetime.combine(today, dtime.min).astimezone()
    return events_between(start, start + timedelta(days=1))


def all_todays_events(conn) -> list[dict]:
    """Elwin's own events for today plus synced calendar events."""
    from . import db

    own = [dict(e, all_day=False) for e in db.get_todays_events(conn)]
    events = own + todays_events()
    events.sort(key=_sort_key)
    return events
