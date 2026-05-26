"""Background LLM-based fact extraction.

Runs in a daemon thread after each assistant response. Sends the
user+assistant exchange to the LLM with a structured extraction prompt
and writes results to the database. Silent failure — never blocks chat.
"""

import json
import logging
import threading
from . import db
from .llm_client import complete_json

logger = logging.getLogger(__name__)

_EXTRACT_PROMPT = """\
Analyze this conversation exchange and extract structured information.
Return a JSON object with two arrays:

1. "people" — any people mentioned (not the AI):
   [{"name": "...", "relationship": "..."}]
   relationship examples: family member, friend, coworker, pet, etc.

2. "facts" — notable facts worth remembering:
   [{"entity": "...", "category": "...", "content": "..."}]
   entity: the person or topic this fact is about
   category: one of: preference, relationship, concern, project, event, trait
   content: a concise statement of the fact

CRITICAL RULES:
- ONLY extract facts explicitly stated by the USER. Never extract anything the \
assistant said, assumed, or embellished. The assistant may hallucinate details — \
ignore everything in the assistant's reply that was not confirmed by the user.
- Never extract facts about the AI assistant itself.
- If the user did not clearly state a fact, do not infer or guess it.
- Be liberal — if the user mentions something personal, a preference, a habit, a \
project, a person, or a life detail, capture it. Err on the side of extracting more.
- If nothing notable was explicitly stated by the user, return:
{"people": [], "facts": []}

You MUST return ONLY a raw JSON object. No markdown fences, no explanation, no \
preamble. Start your response with { and end with }."""


def _run_extraction(
    conn,
    user_text: str,
    assistant_text: str,
    source_msg_id: str | None,
):
    """Send exchange to LLM and store extracted facts."""
    try:
        messages = [
            {"role": "system", "content": _EXTRACT_PROMPT},
            {
                "role": "user",
                "content": (
                    f"User said: {user_text}\n\n"
                    f"Assistant replied: {assistant_text}"
                ),
            },
        ]

        raw = complete_json(messages, temperature=0.1, max_tokens=512)
        if not raw:
            logger.debug("Extraction returned empty response")
            return

        logger.debug("Extraction raw response: %s", raw[:500])

        # Try to parse JSON from the response (handle markdown fencing)
        text = raw.strip()
        if text.startswith("```"):
            # Strip markdown code fences
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        data = json.loads(text)

        # Process people
        for person in data.get("people", []):
            name = person.get("name", "").strip()
            if name:
                db.upsert_person(
                    conn,
                    name=name,
                    relationship=person.get("relationship"),
                )
                logger.info("Learned person: %s", name)

        # Process facts
        for fact in data.get("facts", []):
            entity = fact.get("entity", "").strip()
            content = fact.get("content", "").strip()
            category = fact.get("category", "general").strip()
            if entity and content:
                db.save_fact(
                    conn,
                    entity=entity,
                    category=category,
                    content=content,
                    source_msg_id=source_msg_id,
                )
                logger.info("Extracted fact: [%s] %s — %s", category, entity, content)

        if not data.get("people") and not data.get("facts"):
            logger.debug("Extraction found nothing notable")

    except json.JSONDecodeError as e:
        logger.warning("Extraction JSON parse failed: %s — raw: %s", e, raw[:300] if raw else "(empty)")
    except Exception as e:
        logger.warning("Extraction failed: %s", e)


def extract_async(
    conn,
    user_text: str,
    assistant_text: str,
    source_msg_id: str | None = None,
):
    """Launch fact extraction in a daemon thread."""
    t = threading.Thread(
        target=_run_extraction,
        args=(conn, user_text, assistant_text, source_msg_id),
        daemon=True,
    )
    t.start()
