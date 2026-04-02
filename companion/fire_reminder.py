"""Entry point invoked by launchd when a reminder comes due.

Usage (set in plist ProgramArguments):
    python -m companion.fire_reminder <reminder_id>

Delivers the reminder, marks it fired in the DB, then unloads and deletes
its own plist so it doesn't re-fire next year (StartCalendarInterval repeats
annually if not cleaned up).
"""

import sys

from . import db
from .reminder import deliver_macos, deliver_telegram, deliver_web_push, unschedule


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m companion.fire_reminder <reminder_id>", file=sys.stderr)
        sys.exit(1)

    reminder_id = sys.argv[1]
    conn = db.init_db()

    row = db.get_reminder(conn, reminder_id)

    if not row or row["fired"]:
        # Already delivered or doesn't exist — just clean up the plist.
        unschedule(reminder_id)
        return

    msg = row["message"]

    deliver_macos(msg)
    deliver_telegram(msg)
    deliver_web_push(msg)

    db.mark_reminder_fired(conn, reminder_id)
    unschedule(reminder_id)


if __name__ == "__main__":
    main()
