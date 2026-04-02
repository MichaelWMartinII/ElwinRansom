"""Shared LLM pipeline functions used by both telegram_bot and webapp.

All functions accept `conn` as an explicit first parameter so they can be
imported and called from any module without relying on module-level globals.
"""

import logging
from datetime import datetime

from . import db, dory_bridge, embeddings, extractor, llm_client, memory, reminder, schedule

logger = logging.getLogger(__name__)


def prepare_context(conn, session_id: str, user_text: str) -> tuple[str, list[dict]]:
    """Save user message, embed it, and assemble the full context.

    Returns (user_msg_id, messages) where messages is ready to pass to the LLM.
    """
    user_msg_id = db.save_message(conn, session_id, "user", user_text)
    try:
        embeddings.embed_and_store(conn, user_msg_id, user_text)
    except Exception:
        pass
    dory_bridge.add_turn("user", user_text)
    messages = memory.assemble_context(conn, session_id, user_text)
    return user_msg_id, messages


def stream_llm(messages: list[dict]):
    """Thin wrapper over llm_client.stream_chat(); yields token strings.

    This is a synchronous generator. In async contexts (webapp SSE), bridge
    with an asyncio.Queue — capture the event loop BEFORE spawning the thread:

        loop = asyncio.get_running_loop()
        q = asyncio.Queue()

        def _produce():
            for tok in pipeline.stream_llm(messages):
                asyncio.run_coroutine_threadsafe(q.put(tok), loop)
            asyncio.run_coroutine_threadsafe(q.put(None), loop)

        threading.Thread(target=_produce, daemon=True).start()
        while True:
            tok = await q.get()
            if tok is None: break
            # use tok
    """
    yield from llm_client.stream_chat(messages)


def build_search_messages(
    messages: list[dict], pass1_text: str, search_query: str, search_results: str
) -> list[dict]:
    """Return the augmented message list for a search-enhanced second LLM pass."""
    today = datetime.now().strftime("%B %d, %Y")
    search_messages = list(messages)
    search_messages.append({"role": "assistant", "content": pass1_text})
    search_messages.append({
        "role": "user",
        "content": (
            f"Today is {today}.\n\n"
            f'Web search results for "{search_query}":\n\n'
            f"{search_results}\n\n"
            "Answer my original question using only the information above. "
            "If the results describe an event that has not yet occurred as of today, "
            "say so clearly — do not present future events as completed. "
            "If a URL is relevant, quote it exactly as it appears in the results — "
            "never invent, paraphrase, or guess a URL. "
            "If the results do not contain enough information, say so plainly."
        ),
    })
    return search_messages


def llm_pass2(
    messages: list[dict], pass1_text: str, search_query: str, search_results: str
) -> str:
    """Inject search results and run a second LLM pass. Returns final text."""
    search_messages = build_search_messages(messages, pass1_text, search_query, search_results)
    pass2_parts: list[str] = []
    try:
        for token in llm_client.stream_chat(search_messages):
            pass2_parts.append(token)
    except Exception as e:
        logger.error("LLM pass-2 failed: %s", e)
    return "".join(pass2_parts) if pass2_parts else pass1_text


def handle_reminder(conn, response: str) -> tuple[str, str | None]:
    """Check response for a [REMIND: ...] marker.

    Returns (cleaned_response, confirmation_text_or_None). The marker is
    stripped from the response text shown to the user.
    """
    result = reminder.extract_reminder(response)
    if not result:
        return response, None
    due_at, msg = result
    rid = db.save_reminder(conn, due_at, msg)
    reminder.schedule(rid, due_at)
    dt_local = datetime.fromisoformat(due_at).astimezone()
    clean = reminder.strip_marker(response)
    confirm = f"Reminder set for {dt_local.strftime('%Y-%m-%d %H:%M')}: {msg}"
    return clean, confirm


def handle_butler_markers(conn, response: str) -> tuple[str, list[str]]:
    """Detect and handle EVENT_ADD, TODO_ADD, TODO_DONE, and NOTE markers.

    Returns (cleaned_response, list_of_confirmation_texts). Markers are
    stripped from the displayed response.
    """
    confirmations: list[str] = []

    event_result = schedule.extract_event(response)
    if event_result:
        start_utc, end_utc, title = event_result
        eid = db.save_event(conn, title, start_utc, end_at=end_utc)
        schedule.schedule_prep(eid, start_utc)
        dt_local = datetime.fromisoformat(start_utc).astimezone()
        confirmations.append(f"Event set: {dt_local.strftime('%Y-%m-%d %H:%M')} — {title}")
        response = schedule.strip_event_marker(response)

    todo_add = schedule.extract_todo_add(response)
    if todo_add:
        priority, content = todo_add
        db.save_todo(conn, content, priority)
        confirmations.append(f"Todo added: {content}")
        response = schedule.strip_todo_add_marker(response)

    todo_done = schedule.extract_todo_done(response)
    if todo_done:
        todo = db.find_todo(conn, todo_done)
        if todo:
            db.complete_todo(conn, todo["id"])
            confirmations.append(f"Todo done: {todo['content']}")
        response = schedule.strip_todo_done_marker(response)

    note = schedule.extract_note(response)
    if note:
        db.save_note(conn, note)
        confirmations.append("Note saved.")
        response = schedule.strip_note_marker(response)

    return response.strip(), confirmations


def save_response(conn, session_id: str, user_text: str, assistant_text: str) -> None:
    """Save, embed, and trigger background fact extraction for the final response."""
    asst_msg_id = db.save_message(conn, session_id, "assistant", assistant_text)
    try:
        embeddings.embed_and_store(conn, asst_msg_id, assistant_text)
    except Exception:
        pass
    dory_bridge.add_turn("assistant", assistant_text)
    dory_bridge.maybe_flush()
    extractor.extract_async(conn, user_text, assistant_text, source_msg_id=asst_msg_id)
