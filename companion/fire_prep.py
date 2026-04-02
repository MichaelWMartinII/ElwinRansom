"""Entry point called by launchd 15 minutes before a scheduled event.

Usage: python -m companion.fire_prep <event_id>
"""

import sys


def main(event_id: str) -> None:
    from . import db
    from .reminder import send_telegram
    from .schedule import unschedule_prep

    conn = db.init_db()
    event = db.get_event(conn, event_id)
    if not event:
        return

    lines = [f"Heads up — {event['title']} starts in 15 minutes."]

    # Look for people mentioned in the event title
    title_lower = event["title"].lower()
    people = db.get_all_people(conn)
    matched = [p for p in people if p["name"].lower() in title_lower]

    if matched:
        lines.append("")
        for person in matched:
            lines.append(f"What I know about {person['name']}:")
            facts = db.get_active_facts(conn, entity=person["name"])
            if facts:
                for f in facts:
                    lines.append(f"- {f['content']}")
            elif person.get("relationship"):
                lines.append(f"- {person['relationship']}")

    message = "\n".join(lines)
    send_telegram(message)
    try:
        from . import web_push
        web_push.send_push(
            conn,
            title=f"In 15 min: {event['title']}",
            body=lines[0],
        )
    except Exception:
        pass
    unschedule_prep(event_id)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m companion.fire_prep <event_id>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
