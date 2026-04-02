"""Terminal chat loop with slash commands."""

import os
import sys
import uuid

from . import brave_search, config, db, dory_bridge, embeddings, llm_client, pipeline, schedule
from .controller import InputType, process_input


def _print_header(conn):
    msg_count = db.count_messages(conn)
    session_count = db.count_sessions(conn)
    fact_count = len(db.get_active_facts(conn))
    people_count = len(db.get_all_people(conn))

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Elwin Ransom")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Messages: {msg_count}  Sessions: {session_count}")
    print(f"  Facts: {fact_count}  People: {people_count}")
    print("  Type /help for commands, /quit to exit")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()


def _cmd_help():
    print("  /new           — start a fresh conversation")
    print("  /image <path> [question] — describe an image or ask about it")
    print("  /voice <path>  — transcribe audio and chat about it")
    print("  /help          — show this help")
    print("  /people        — list known people")
    print("  /facts [name]  — show extracted facts (optionally for a person)")
    print("  /reminders     — list pending reminders")
    print("  /schedule      — list upcoming 7 days of events")
    print("  /todos         — list pending to-dos")
    print("  /notes         — list recent notes")
    print("  /stats         — show database stats")
    print("  /usage         — show web search usage this month")
    print("  /quit          — exit companion")
    print()


def _cmd_usage(conn):
    used = brave_search.get_monthly_usage(conn)
    limit = brave_search._MONTHLY_LIMIT
    remaining = max(0, limit - used)
    print(f"  Web searches this month: {used} / {limit}  ({remaining} remaining)")
    print()


def _cmd_people(conn):
    people = db.get_all_people(conn)
    if not people:
        print("  No people learned yet.\n")
        return
    for p in people:
        rel = f" — {p['relationship']}" if p.get("relationship") else ""
        print(f"  {p['name']}{rel}")
    print()


def _cmd_facts(conn, args: str):
    entity = args.strip() or None
    facts = db.get_active_facts(conn, entity=entity)
    if not facts:
        label = f" about {entity}" if entity else ""
        print(f"  No facts{label} yet.\n")
        return
    for f in facts:
        print(f"  [{f['category']}] {f['entity']}: {f['content']}")
    print()


def _cmd_reminders(conn):
    from datetime import datetime, timezone
    pending = db.get_pending_reminders(conn)
    if not pending:
        print("  No pending reminders.\n")
        return
    for r in pending:
        # Convert stored UTC back to local for display
        dt_utc = datetime.fromisoformat(r["due_at"])
        dt_local = dt_utc.astimezone()
        print(f"  {dt_local.strftime('%Y-%m-%d %H:%M')}  {r['message']}")
    print()


def _cmd_schedule(conn):
    events = db.get_upcoming_events(conn)
    if not events:
        print("  No upcoming events.\n")
        return
    print("  Upcoming schedule:")
    print(schedule.format_schedule(events))
    print()


def _cmd_todos(conn):
    todos = db.get_pending_todos(conn)
    priority_map = {"high": "H", "medium": "M", "low": "L"}
    if not todos:
        print("  No pending todos.\n")
        return
    for t in todos:
        p = priority_map.get(t["priority"], "M")
        print(f"  [{p}] {t['content']}")
    print()


def _cmd_notes(conn):
    notes = db.get_recent_notes(conn)
    if not notes:
        print("  No recent notes.\n")
        return
    for n in notes:
        print(f"  {n['content']}")
    print()


def _cmd_stats(conn):
    print(f"  Messages:  {db.count_messages(conn)}")
    print(f"  Sessions:  {db.count_sessions(conn)}")
    print(f"  People:    {len(db.get_all_people(conn))}")
    print(f"  Facts:     {len(db.get_active_facts(conn))}")
    emb_count = len(db.load_all_embeddings(conn))
    print(f"  Embeddings: {emb_count}")
    dory = dory_bridge.stats()
    if dory.get("enabled"):
        graph = dory.get("graph", {})
        print(f"  Dory:      {graph.get('nodes', 0)} nodes  {graph.get('core_nodes', 0)} core")
    elif config.DORY_ENABLED:
        print(f"  Dory:      unavailable ({dory.get('reason', 'unknown')})")
    print()


def run():
    """Main chat loop."""
    # Initialize
    conn = db.init_db()
    session_id = uuid.uuid4().hex[:12]

    # Health check
    if config.DORY_ENABLED:
        reason = dory_bridge.status_reason()
        if reason:
            print(f"[WARNING] Dory integration unavailable — {reason}")
            print("Install with: pip install 'dory-memory[openai]==0.6.1'\n")
    if not llm_client.health_check():
        print("[ERROR] Cannot reach llama-server at", end=" ")
        print(f"{llm_client.config.LLM_BASE_URL}/health")
        print("Start it with: ./start.sh")
        sys.exit(1)

    # Load embedding model
    print("Loading embedding model...", end=" ", flush=True)
    embeddings.get_model()
    print("done.\n")

    _print_header(conn)

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        # Slash commands
        if user_input.startswith("/"):
            parts = user_input.split(None, 1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if cmd == "/quit" or cmd == "/exit":
                print("Goodbye!")
                break
            elif cmd == "/new":
                session_id = uuid.uuid4().hex[:12]
                print("  New conversation started.\n")
                continue
            elif cmd == "/help":
                _cmd_help()
                continue
            elif cmd == "/people":
                _cmd_people(conn)
                continue
            elif cmd == "/facts":
                _cmd_facts(conn, args)
                continue
            elif cmd == "/reminders":
                _cmd_reminders(conn)
                continue
            elif cmd == "/schedule":
                _cmd_schedule(conn)
                continue
            elif cmd == "/todos":
                _cmd_todos(conn)
                continue
            elif cmd == "/notes":
                _cmd_notes(conn)
                continue
            elif cmd == "/stats":
                _cmd_stats(conn)
                continue
            elif cmd == "/usage":
                _cmd_usage(conn)
                continue
            elif cmd == "/image":
                if not args.strip():
                    print("  Usage: /image <path> [question]\n")
                    continue
                tokens = args.strip().split(None, 1)
                img_path = os.path.expanduser(tokens[0])
                question = tokens[1] if len(tokens) > 1 else ""
                if not os.path.isfile(img_path):
                    print(f"  File not found: {img_path}\n")
                    continue
                print("  [analyzing image...]")
                try:
                    user_input = process_input(
                        InputType.IMAGE, question, img_path
                    )
                except Exception as e:
                    print(f"  [ERROR] Vision failed: {e}\n")
                    continue
                print(f"  {user_input.splitlines()[0]}\n")
                # Fall through to LLM pipeline below
            elif cmd == "/voice":
                if not args.strip():
                    print("  Usage: /voice <path>\n")
                    continue
                voice_path = os.path.expanduser(args.strip())
                if not os.path.isfile(voice_path):
                    print(f"  File not found: {voice_path}\n")
                    continue
                print("  [transcribing...]")
                try:
                    user_input = process_input(
                        InputType.VOICE, "", audio_path=voice_path
                    )
                except Exception as e:
                    print(f"  [ERROR] Transcription failed: {e}\n")
                    continue
                print(f"  \"{user_input}\"\n")
                # Fall through to LLM pipeline below
            else:
                print(f"  Unknown command: {cmd}. Type /help for options.\n")
                continue

        _, messages = pipeline.prepare_context(conn, session_id, user_input)

        # Stream response
        print("ransom> ", end="", flush=True)
        response_parts = []
        try:
            for token in llm_client.stream_chat(messages):
                print(token, end="", flush=True)
                response_parts.append(token)
        except Exception as e:
            print(f"\n[ERROR] LLM request failed: {e}\n")
            continue

        print("\n")
        assistant_text = "".join(response_parts)

        # Search detection — check for [SEARCH: query] in pass-1 output
        search_query = brave_search.extract_search_query(assistant_text)
        if search_query:
            print(f"  [searching: {search_query}]")
            search_results = brave_search.search(conn, search_query)
            if search_results:
                # Build augmented context for pass 2
                search_messages = pipeline.build_search_messages(
                    messages, assistant_text, search_query, search_results
                )

                # Stream pass 2
                print("ransom> ", end="", flush=True)
                response_parts = []
                try:
                    for token in llm_client.stream_chat(search_messages):
                        print(token, end="", flush=True)
                        response_parts.append(token)
                except Exception as e:
                    print(f"\n[ERROR] LLM request failed on pass 2: {e}\n")
                    continue
                print("\n")
                assistant_text = "".join(response_parts)

        clean_text, remind_confirm = pipeline.handle_reminder(conn, assistant_text)
        clean_text, butler_confirms = pipeline.handle_butler_markers(conn, clean_text)
        if remind_confirm:
            print(f"  [{remind_confirm.lower()}]\n")
        for confirm in butler_confirms:
            print(f"  [{confirm.lower()}]\n")

        pipeline.save_response(conn, session_id, user_input, clean_text)
