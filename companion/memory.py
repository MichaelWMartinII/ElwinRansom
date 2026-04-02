"""Context assembly pipeline.

Builds the full message list for each LLM call:
  1. System prompt (personality + people + facts)
  2. Retrieved memories (semantic search)
  3. Recent conversation turns
  4. Current user message
"""

from . import config, db, dory_bridge, embeddings
from .prompts import build_system_prompt


def _estimate_tokens(text: str) -> int:
    return db.estimate_tokens(text)


def _trim_messages(messages: list[dict], budget: int) -> list[dict]:
    """Keep the most recent messages that fit within the token budget."""
    result = []
    used = 0
    for msg in reversed(messages):
        cost = _estimate_tokens(msg["content"])
        if used + cost > budget:
            break
        result.append(msg)
        used += cost
    return list(reversed(result))


def assemble_context(
    conn,
    session_id: str,
    user_text: str,
) -> list[dict[str, str]]:
    """Build the full prompt message list for the LLM."""

    # 1. System prompt
    people = db.get_all_people(conn)
    facts = db.get_active_facts(conn)
    events = db.get_todays_events(conn)
    todos = db.get_pending_todos(conn)
    system_text = build_system_prompt(
        people, facts, events=events, todos=todos,
        search_enabled=bool(config.BRAVE_SEARCH_API_KEY)
    )
    system_tokens = _estimate_tokens(system_text)

    # 2. Semantic memory retrieval
    # Get current session message IDs to exclude from search
    session_msgs = db.get_session_messages(conn, session_id, limit=100)
    session_ids = set()
    for m in session_msgs:
        if "id" in m:
            session_ids.add(m["id"])

    memories = embeddings.search_similar(
        conn, user_text, top_k=config.TOP_K_MEMORIES, exclude_ids=session_ids
    )

    # Format memories into a single assistant-context block
    memory_lines = []
    memory_tokens = 0
    for mem in memories:
        line = f"[{mem['role']}] {mem['content']}"
        cost = _estimate_tokens(line)
        if memory_tokens + cost > config.MEMORY_TOKEN_BUDGET:
            break
        memory_lines.append(line)
        memory_tokens += cost

    dory_context = dory_bridge.query(user_text)
    dory_tokens = 0
    if dory_context:
        dory_tokens = min(_estimate_tokens(dory_context), config.MEMORY_TOKEN_BUDGET)
        available_chars = max(240, dory_tokens * 3)
        dory_context = dory_context[:available_chars].strip()

    # 3. Recent turns from this session
    remaining_budget = (
        config.PROMPT_BUDGET - system_tokens - memory_tokens - dory_tokens
        - _estimate_tokens(user_text) - 50  # safety margin
    )
    recent = _trim_messages(session_msgs, remaining_budget)

    # 4. Assemble
    result: list[dict[str, str]] = []

    # System message
    full_system = system_text
    if memory_lines:
        full_system += (
            "\n\nRelevant past conversations:\n" + "\n".join(memory_lines)
        )
    if dory_context:
        full_system += "\n\nDory long-term memory:\n" + dory_context
    result.append({"role": "system", "content": full_system})

    # Recent turns
    for msg in recent:
        result.append({"role": msg["role"], "content": msg["content"]})

    # Current user message
    result.append({"role": "user", "content": user_text})

    return result
