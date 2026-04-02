"""Telegram bot bridge for Elwin Ransom.

Mirrors the CLI pipeline (save → embed → assemble context → LLM → save →
embed → extract facts) but fronted by a Telegram bot instead of a terminal.

Run with: python3 -m companion.telegram_bot
"""

import asyncio
import logging
import sys
import tempfile

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from . import brave_search, camera, config, db, dory_bridge, embeddings, llm_client, pipeline, schedule, tts, vision
from .controller import InputType, process_input

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Module state ─────────────────────────────────────────────
_conn = None
_llm_lock = asyncio.Lock()
_session_counters: dict[int, int] = {}


# ── Helpers ──────────────────────────────────────────────────

def _session_id(user_id: int) -> str:
    """Return the current session ID for a Telegram user."""
    counter = _session_counters.get(user_id, 0)
    return f"tg_{user_id}_{counter}"


def _is_owner(user_id: int) -> bool:
    return user_id == config.TELEGRAM_OWNER_ID


def _split_message(text: str, limit: int = 4096) -> list[str]:
    """Split text into chunks that fit within Telegram's message limit."""
    if not text.strip():
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break

        # Try to split at a newline
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            # Try a space
            cut = text.rfind(" ", 0, limit)
        if cut == -1:
            # Hard cut
            cut = limit

        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")

    return chunks


def _llm_pass1(session_id: str, user_text: str) -> tuple[str, list[dict]]:
    """Run prepare_context + LLM streaming pass 1. Returns (text, messages)."""
    _, messages = pipeline.prepare_context(_conn, session_id, user_text)
    response_parts: list[str] = []
    try:
        for token in pipeline.stream_llm(messages):
            response_parts.append(token)
    except Exception as e:
        return f"[LLM error: {e}]", messages

    assistant_text = "".join(response_parts)
    if not assistant_text.strip():
        assistant_text = "(empty response)"
    return assistant_text, messages


def _handle_reminder(response: str) -> tuple[str, str | None]:
    return pipeline.handle_reminder(_conn, response)


def _handle_butler_markers(response: str) -> tuple[str, list[str]]:
    return pipeline.handle_butler_markers(_conn, response)


# ── Shared LLM pipeline ──────────────────────────────────────

def _capture_and_describe(user_text: str) -> tuple[str, str | None]:
    """Capture a photo and describe it via the vision server.

    Returns (photo_path, caption_or_None). Runs in a thread.
    """
    path = camera.capture()
    caption = None
    if vision.health_check():
        try:
            caption = vision.describe_image(path, user_text)
        except Exception as e:
            logger.warning("Vision description failed: %s", e)
    return path, caption


async def _run_pipeline(
    update: Update,
    session: str,
    user_text: str,
    status_msg=None,
) -> tuple[str, str | None, list[str], str | None, str | None]:
    """Run LLM → search → reminder → butler → camera → save.

    Returns (response, remind_confirm, butler_confirms, photo_path, photo_caption).
    Deletes status_msg when done (whether passed in or created for search).
    """
    pass1_text, messages = await asyncio.to_thread(_llm_pass1, session, user_text)
    response = pass1_text

    search_query = brave_search.extract_search_query(pass1_text)
    if search_query:
        if status_msg:
            await status_msg.edit_text(f"Searching the web for: {search_query}")
        else:
            status_msg = await update.message.reply_text(
                f"Searching the web for: {search_query}"
            )
        search_results = await asyncio.to_thread(
            brave_search.search, _conn, search_query
        )
        if search_results:
            await status_msg.edit_text(
                f"Searching the web for: {search_query}\nThinking..."
            )
            response = await asyncio.to_thread(
                pipeline.llm_pass2, messages, pass1_text, search_query, search_results
            )
        else:
            await status_msg.edit_text(f"Searched for: {search_query} (no results)")

    response, remind_confirm = await asyncio.to_thread(_handle_reminder, response)
    response, butler_confirms = await asyncio.to_thread(_handle_butler_markers, response)

    # Camera capture
    photo_path = None
    photo_caption = None
    if camera.extract_marker(response):
        response = camera.strip_marker(response)
        if status_msg:
            await status_msg.edit_text("Taking a photo...")
        else:
            status_msg = await update.message.reply_text("Taking a photo...")
        try:
            photo_path, photo_caption = await asyncio.to_thread(
                _capture_and_describe, user_text
            )
        except Exception as e:
            logger.warning("Camera capture failed: %s", e)
            suffix = "\n(Camera capture failed.)"
            response = (response + suffix).strip() if response else suffix.strip()

    if status_msg:
        try:
            await status_msg.delete()
        except Exception:
            pass

    await asyncio.to_thread(pipeline.save_response, _conn, session, user_text, response)
    return response, remind_confirm, butler_confirms, photo_path, photo_caption


# ── Typing indicator ─────────────────────────────────────────

async def _keep_typing(chat_id: int, bot) -> None:
    """Send 'typing' action every 5 seconds until cancelled."""
    try:
        while True:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        pass


# ── Handlers ─────────────────────────────────────────────────

async def cmd_start(update: Update, context) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("This bot is private.")
        return
    await update.message.reply_text(
        "Hello, Michael. I'm Elwin Ransom — your AI companion.\n"
        "Send me a message to chat. Use /new to start a fresh conversation."
    )


async def cmd_usage(update: Update, context) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("This bot is private.")
        return
    used = brave_search.get_monthly_usage(_conn)
    limit = brave_search._MONTHLY_LIMIT
    remaining = max(0, limit - used)
    await update.message.reply_text(
        f"Web searches this month: {used} / {limit}  ({remaining} remaining)"
    )


async def cmd_new(update: Update, context) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("This bot is private.")
        return
    uid = update.effective_user.id
    _session_counters[uid] = _session_counters.get(uid, 0) + 1
    await update.message.reply_text("New conversation started.")


async def handle_text(update: Update, context) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("This bot is private.")
        return

    user_text = update.message.text
    if not user_text or not user_text.strip():
        return

    if _llm_lock.locked():
        await update.message.reply_text(
            "Still thinking about your last message — please wait."
        )
        return

    session = _session_id(update.effective_user.id)

    async with _llm_lock:
        typing_task = asyncio.create_task(
            _keep_typing(update.effective_chat.id, context.bot)
        )
        try:
            response, remind_confirm, butler_confirms, photo_path, photo_caption = (
                await _run_pipeline(update, session, user_text)
            )
        finally:
            typing_task.cancel()

    for chunk in _split_message(response):
        await update.message.reply_text(chunk)
    if photo_path:
        with open(photo_path, "rb") as f:
            await update.message.reply_photo(photo=f, caption=photo_caption)
    if remind_confirm:
        await update.message.reply_text(f"✓ {remind_confirm}")
    for confirm in butler_confirms:
        await update.message.reply_text(f"✓ {confirm}")


async def handle_voice(update: Update, context) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("This bot is private.")
        return

    if _llm_lock.locked():
        await update.message.reply_text(
            "Still thinking about your last message — please wait."
        )
        return

    session = _session_id(update.effective_user.id)

    async with _llm_lock:
        typing_task = asyncio.create_task(
            _keep_typing(update.effective_chat.id, context.bot)
        )
        try:
            voice = update.message.voice or update.message.audio
            file = await voice.get_file()
            suffix = ".ogg" if update.message.voice else ".mp3"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = tmp.name
            await file.download_to_drive(tmp_path)

            status_msg = await update.message.reply_text("Transcribing...")
            user_text = await asyncio.to_thread(
                process_input, InputType.VOICE, "", audio_path=tmp_path
            )

            if not user_text.strip():
                await status_msg.edit_text(
                    "I couldn't make out any words in that voice message."
                )
                return

            await status_msg.edit_text("Thinking...")
            response, remind_confirm, butler_confirms, photo_path, photo_caption = (
                await _run_pipeline(update, session, user_text, status_msg=status_msg)
            )
        finally:
            typing_task.cancel()

    # Voice-in, voice-out: synthesize TTS and reply with audio.
    # Include vision description so Elwin actually says what he sees.
    tts_text = response
    if photo_caption:
        tts_text = f"{response} {photo_caption}".strip() if response.strip() else photo_caption
    if not tts_text.strip() and photo_path:
        tts_text = "Here's the photo."

    voice_path = None
    if tts_text.strip():
        try:
            voice_path = await asyncio.to_thread(tts.synthesize, tts_text)
        except Exception as e:
            logger.warning("TTS failed: %s", e)

    if voice_path:
        with open(voice_path, "rb") as f:
            await update.message.reply_voice(voice=f)
    else:
        # TTS unavailable — fall back to text
        for chunk in _split_message(response):
            await update.message.reply_text(chunk)

    if photo_path:
        with open(photo_path, "rb") as f:
            await update.message.reply_photo(photo=f, caption=photo_caption)
    if remind_confirm:
        await update.message.reply_text(f"✓ {remind_confirm}")
    for confirm in butler_confirms:
        await update.message.reply_text(f"✓ {confirm}")


async def handle_photo(update: Update, context) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("This bot is private.")
        return

    if _llm_lock.locked():
        await update.message.reply_text(
            "Still thinking about your last message — please wait."
        )
        return

    caption = update.message.caption or ""
    session = _session_id(update.effective_user.id)

    async with _llm_lock:
        typing_task = asyncio.create_task(
            _keep_typing(update.effective_chat.id, context.bot)
        )
        try:
            photo = update.message.photo[-1]
            file = await photo.get_file()
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
            await file.download_to_drive(tmp_path)

            status_msg = await update.message.reply_text("Looking at your image...")
            user_text = await asyncio.to_thread(
                process_input, InputType.IMAGE, caption, tmp_path
            )

            await status_msg.edit_text("Thinking...")
            response, remind_confirm, butler_confirms, photo_path, photo_caption = (
                await _run_pipeline(update, session, user_text, status_msg=status_msg)
            )
        finally:
            typing_task.cancel()

    for chunk in _split_message(response):
        await update.message.reply_text(chunk)
    if photo_path:
        with open(photo_path, "rb") as f:
            await update.message.reply_photo(photo=f, caption=photo_caption)
    if remind_confirm:
        await update.message.reply_text(f"✓ {remind_confirm}")
    for confirm in butler_confirms:
        await update.message.reply_text(f"✓ {confirm}")


async def cmd_schedule(update: Update, context) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("This bot is private.")
        return
    events = db.get_upcoming_events(_conn)
    if not events:
        await update.message.reply_text("No upcoming events.")
        return
    await update.message.reply_text(schedule.format_schedule(events))


async def cmd_todos(update: Update, context) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("This bot is private.")
        return
    todos = db.get_pending_todos(_conn)
    if not todos:
        await update.message.reply_text("No pending todos.")
        return
    priority_map = {"high": "H", "medium": "M", "low": "L"}
    lines = [
        f"[{priority_map.get(t['priority'], 'M')}] {t['content']}"
        for t in todos
    ]
    await update.message.reply_text("\n".join(lines))


async def cmd_notes(update: Update, context) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("This bot is private.")
        return
    notes = db.get_recent_notes(_conn)
    if not notes:
        await update.message.reply_text("No recent notes.")
        return
    await update.message.reply_text("\n\n".join(n["content"] for n in notes))


async def cmd_briefing(update: Update, context) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("This bot is private.")
        return
    from . import briefing
    text = await asyncio.to_thread(briefing.assemble, _conn)
    for chunk in _split_message(text):
        await update.message.reply_text(chunk)


# ── Entry point ──────────────────────────────────────────────

def main() -> None:
    global _conn

    # Validate config
    if not config.TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in agent.conf")
        sys.exit(1)
    if not config.TELEGRAM_OWNER_ID:
        print("ERROR: TELEGRAM_OWNER_ID not set in agent.conf")
        sys.exit(1)
    if config.DORY_ENABLED:
        reason = dory_bridge.status_reason()
        if reason:
            print(f"WARNING: Dory integration unavailable — {reason}")
            print("Install with: pip install 'dory-memory[openai]==0.6.1'")

    # Health check
    if not llm_client.health_check():
        print(
            f"ERROR: Cannot reach llama-server at "
            f"{config.LLM_BASE_URL}/health"
        )
        print("Start it with: ./start.sh")
        sys.exit(1)

    # Initialize database
    _conn = db.init_db()

    # Load embedding model
    print("Loading embedding model...", end=" ", flush=True)
    embeddings.get_model()
    print("done.")

    # Install daily briefing plist if not already present
    from . import briefing as _briefing
    if _briefing.install():
        print("Briefing plist installed.")

    # Build Telegram app
    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("usage", cmd_usage))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("todos", cmd_todos))
    app.add_handler(CommandHandler("notes", cmd_notes))
    app.add_handler(CommandHandler("briefing", cmd_briefing))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))

    print("Telegram bot started. Polling for updates...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
