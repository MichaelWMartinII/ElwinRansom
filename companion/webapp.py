"""aiohttp web server for Elwin Ransom.

Provides a self-hosted chat UI accessible via Tailscale. Runs as a separate
process alongside telegram_bot; SQLite WAL mode handles concurrent DB access.

Run with: python -m companion.webapp
"""

import asyncio
import base64
import json
import logging
import secrets
import sys
import tempfile
import threading
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

from aiohttp import web

from . import agent as agent_mod
from . import brave_search, camera, config, db, dory_bridge, embeddings, llm_client, pipeline, vision
from .controller import InputType, process_input

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Module state ─────────────────────────────────────────────
_conn = None
_llm_lock = asyncio.Lock()
_session_counter = 0
_SESSION_TOKENS: dict[str, float] = {}  # token -> expiry timestamp
_SESSION_TTL = 24 * 3600  # 24 hours

# ── Demo rate limiting ────────────────────────────────────────
DEMO_LIMIT = 10  # messages per IP per day
_demo_counts: dict[str, int] = defaultdict(int)
_demo_date: date = date.today()

def _get_client_ip(request: web.Request) -> str:
    # Cloudflare passes real IP here
    return (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.remote
        or "unknown"
    )

def _demo_remaining(ip: str) -> int:
    global _demo_counts, _demo_date
    today = date.today()
    if today != _demo_date:
        _demo_counts.clear()
        _demo_date = today
    return max(0, DEMO_LIMIT - _demo_counts[ip])

def _demo_consume(ip: str) -> bool:
    """Returns True if allowed, False if limit reached."""
    global _demo_counts, _demo_date
    today = date.today()
    if today != _demo_date:
        _demo_counts.clear()
        _demo_date = today
    if _demo_counts[ip] >= DEMO_LIMIT:
        return False
    _demo_counts[ip] += 1
    return True

_STATIC = Path(__file__).parent / "static"


# ── Helpers ──────────────────────────────────────────────────

def _current_session() -> str:
    return f"web_{_session_counter}"


# ── SSE helpers ──────────────────────────────────────────────

async def _sse_start(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse()
    resp.headers["Content-Type"] = "text/event-stream; charset=utf-8"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    await resp.prepare(request)
    return resp


async def _sse_send(resp: web.StreamResponse, data: dict) -> None:
    payload = f"data: {json.dumps(data)}\n\n"
    await resp.write(payload.encode())


# ── Auth middleware ───────────────────────────────────────────

_PUBLIC_PATHS = {"/", "/api/auth", "/demo"}


@web.middleware
async def _auth_middleware(request: web.Request, handler):
    path = request.path
    # Static files, the index page, and demo routes are always public
    if not path.startswith("/api/") or path in _PUBLIC_PATHS or path.startswith("/api/demo/"):
        return await handler(request)
    # API routes require a valid session token
    token = request.headers.get("X-Session-Token") or request.cookies.get("session")
    expiry = _SESSION_TOKENS.get(token) if token else None
    if not expiry or time.time() > expiry:
        if token and token in _SESSION_TOKENS:
            del _SESSION_TOKENS[token]
        raise web.HTTPUnauthorized(reason="Invalid or missing session token")
    return await handler(request)


# ── Auth endpoints ────────────────────────────────────────────

async def api_auth(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        raise web.HTTPBadRequest(reason="Invalid JSON")

    password = data.get("password", "")
    if not config.WEB_PASSWORD:
        raise web.HTTPForbidden(reason="WEB_PASSWORD not set in agent.conf")
    if not secrets.compare_digest(password, config.WEB_PASSWORD):
        raise web.HTTPUnauthorized(reason="Invalid password")

    token = secrets.token_hex(32)
    _SESSION_TOKENS[token] = time.time() + _SESSION_TTL

    resp = web.json_response({"ok": True, "token": token})
    secure = bool(config.TLS_CERT_PATH)
    resp.set_cookie("session", token, httponly=True, secure=secure, samesite="Strict")
    return resp


async def api_me(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


# ── Core streaming pipeline ───────────────────────────────────

async def _run_pipeline_streaming(sse_resp: web.StreamResponse, user_text: str) -> None:
    """Run the full LLM pipeline and stream results via SSE.

    Must be called with _llm_lock held. Sends 'done' event at end.
    """
    session = _current_session()

    try:
        await _sse_send(sse_resp, {"type": "status", "content": "Thinking…"})

        # prepare_context: save + embed + assemble messages
        _, messages = await asyncio.to_thread(
            pipeline.prepare_context, _conn, session, user_text
        )

        # Stream pass-1 tokens via thread → asyncio.Queue bridge
        loop = asyncio.get_running_loop()
        token_queue: asyncio.Queue = asyncio.Queue()
        pass1_tokens: list[str] = []

        def _produce_pass1():
            try:
                for tok in pipeline.stream_llm(messages):
                    asyncio.run_coroutine_threadsafe(token_queue.put(tok), loop)
            except Exception as exc:
                asyncio.run_coroutine_threadsafe(token_queue.put(("error", str(exc))), loop)
            asyncio.run_coroutine_threadsafe(token_queue.put(None), loop)

        threading.Thread(target=_produce_pass1, daemon=True).start()

        while True:
            try:
                item = await asyncio.wait_for(token_queue.get(), timeout=60.0)
            except asyncio.TimeoutError:
                await _sse_send(sse_resp, {"type": "error", "content": "Response timed out."})
                await _sse_send(sse_resp, {"type": "done"})
                return
            if item is None:
                break
            if isinstance(item, tuple):  # error sentinel
                await _sse_send(sse_resp, {"type": "error", "content": item[1]})
                await _sse_send(sse_resp, {"type": "done"})
                return
            pass1_tokens.append(item)
            await _sse_send(sse_resp, {"type": "token", "content": item})

        pass1_text = "".join(pass1_tokens) or "(empty response)"
        response = pass1_text

        # Web search (pass-2)
        search_query = brave_search.extract_search_query(pass1_text)
        if search_query:
            await _sse_send(sse_resp, {"type": "status", "content": f"Searching: {search_query}"})
            search_results = await asyncio.to_thread(
                brave_search.search, _conn, search_query
            )
            if search_results:
                # Signal client to discard the pass-1 draft
                await _sse_send(sse_resp, {"type": "clear"})
                await _sse_send(sse_resp, {"type": "status", "content": "Thinking…"})

                search_messages = pipeline.build_search_messages(
                    messages, pass1_text, search_query, search_results
                )
                pass2_queue: asyncio.Queue = asyncio.Queue()
                pass2_tokens: list[str] = []

                def _produce_pass2():
                    try:
                        for tok in pipeline.stream_llm(search_messages):
                            asyncio.run_coroutine_threadsafe(pass2_queue.put(tok), loop)
                    except Exception:
                        pass
                    asyncio.run_coroutine_threadsafe(pass2_queue.put(None), loop)

                threading.Thread(target=_produce_pass2, daemon=True).start()

                while True:
                    try:
                        tok = await asyncio.wait_for(pass2_queue.get(), timeout=60.0)
                    except asyncio.TimeoutError:
                        await _sse_send(sse_resp, {"type": "error", "content": "Search response timed out."})
                        await _sse_send(sse_resp, {"type": "done"})
                        return
                    if tok is None:
                        break
                    pass2_tokens.append(tok)
                    await _sse_send(sse_resp, {"type": "token", "content": tok})

                response = "".join(pass2_tokens) if pass2_tokens else pass1_text

        # Reminder
        response, remind_confirm = await asyncio.to_thread(
            pipeline.handle_reminder, _conn, response
        )
        if remind_confirm:
            await _sse_send(sse_resp, {"type": "reminder_set", "content": remind_confirm})

        # Butler markers (events / todos / notes)
        response, butler_confirms = await asyncio.to_thread(
            pipeline.handle_butler_markers, _conn, response
        )
        for confirm in butler_confirms:
            if "Event set:" in confirm:
                etype = "event_set"
            elif "Todo added:" in confirm:
                etype = "todo_added"
            elif "Todo done:" in confirm:
                etype = "todo_added"
            elif "Note saved" in confirm:
                etype = "note_saved"
            else:
                etype = "status"
            await _sse_send(sse_resp, {"type": etype, "content": confirm})

        # Camera capture
        if camera.extract_marker(response):
            response = camera.strip_marker(response)
            await _sse_send(sse_resp, {"type": "status", "content": "Taking photo…"})
            try:
                photo_path, photo_caption = await asyncio.to_thread(
                    _capture_and_describe, user_text
                )
                try:
                    with open(photo_path, "rb") as f:
                        photo_b64 = base64.b64encode(f.read()).decode()
                    await _sse_send(sse_resp, {
                        "type": "photo",
                        "src": f"data:image/jpeg;base64,{photo_b64}",
                        "caption": photo_caption or "",
                    })
                finally:
                    Path(photo_path).unlink(missing_ok=True)
            except Exception as e:
                logger.warning("Camera capture failed: %s", e)

        # Save final response
        await asyncio.to_thread(
            pipeline.save_response, _conn, session, user_text, response
        )

    except Exception as e:
        logger.exception("Pipeline error: %s", e)
        try:
            await _sse_send(sse_resp, {"type": "error", "content": str(e)})
        except Exception:
            pass

    await _sse_send(sse_resp, {"type": "done"})


def _capture_and_describe(user_text: str) -> tuple[str, str | None]:
    path = camera.capture()
    caption = None
    if vision.health_check():
        try:
            caption = vision.describe_image(path, user_text)
        except Exception as e:
            logger.warning("Vision description failed: %s", e)
    return path, caption


# ── Agent streaming helper ────────────────────────────────────

async def _run_agent_streaming(
    sse_resp: web.StreamResponse,
    user_text: str,
) -> None:
    """Run agent mode and stream tokens + tool events via SSE."""
    session = _current_session()

    _, messages = await asyncio.to_thread(
        pipeline.prepare_context, _conn, session, user_text
    )

    loop = asyncio.get_running_loop()
    token_queue: asyncio.Queue = asyncio.Queue()
    agent_tokens: list[str] = []

    def _on_tool_call(name: str, params: dict) -> None:
        import json as _json
        asyncio.run_coroutine_threadsafe(
            _sse_send(sse_resp, {"type": "tool_call", "name": name, "params": params}),
            loop,
        )

    def _on_tool_result(name: str, result: str) -> None:
        preview = result[:300] + ("…" if len(result) > 300 else "")
        asyncio.run_coroutine_threadsafe(
            _sse_send(sse_resp, {"type": "tool_result", "name": name, "content": preview}),
            loop,
        )

    def _produce():
        try:
            for tok in agent_mod.run_agent(messages, on_tool_call=_on_tool_call, on_tool_result=_on_tool_result):
                asyncio.run_coroutine_threadsafe(token_queue.put(tok), loop)
        except Exception as exc:
            asyncio.run_coroutine_threadsafe(token_queue.put(("error", str(exc))), loop)
        asyncio.run_coroutine_threadsafe(token_queue.put(None), loop)

    threading.Thread(target=_produce, daemon=True).start()

    while True:
        try:
            item = await asyncio.wait_for(token_queue.get(), timeout=120.0)
        except asyncio.TimeoutError:
            await _sse_send(sse_resp, {"type": "error", "content": "Agent timed out."})
            await _sse_send(sse_resp, {"type": "done"})
            return
        if item is None:
            break
        if isinstance(item, tuple):
            await _sse_send(sse_resp, {"type": "error", "content": item[1]})
            await _sse_send(sse_resp, {"type": "done"})
            return
        agent_tokens.append(item)
        await _sse_send(sse_resp, {"type": "token", "content": item})

    response = "".join(agent_tokens) or "(no response)"
    await asyncio.to_thread(pipeline.save_response, _conn, session, user_text, response)
    await _sse_send(sse_resp, {"type": "done"})


# ── Chat endpoint ─────────────────────────────────────────────

async def api_chat(request: web.Request) -> web.StreamResponse:
    try:
        data = await request.json()
    except Exception:
        raise web.HTTPBadRequest(reason="Invalid JSON")

    user_text = (data.get("text") or "").strip()
    if not user_text:
        raise web.HTTPBadRequest(reason="text field required")
    use_agent = bool(data.get("agent_mode", False))

    sse_resp = await _sse_start(request)

    async with _llm_lock:
        if use_agent:
            await _run_agent_streaming(sse_resp, user_text)
        else:
            await _run_pipeline_streaming(sse_resp, user_text)

    return sse_resp


# ── Voice upload ──────────────────────────────────────────────

async def api_upload_voice(request: web.Request) -> web.StreamResponse:
    sse_resp = await _sse_start(request)
    tmp_path = None

    try:
        reader = await request.multipart()
        field = await reader.next()

        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp_path = tmp.name
            while True:
                chunk = await field.read_chunk(16384)
                if not chunk:
                    break
                tmp.write(chunk)

        await _sse_send(sse_resp, {"type": "status", "content": "Transcribing…"})
        user_text = await asyncio.to_thread(
            process_input, InputType.VOICE, "", audio_path=tmp_path
        )

        if not user_text.strip():
            await _sse_send(sse_resp, {"type": "error", "content": "No speech detected."})
            await _sse_send(sse_resp, {"type": "done"})
            return sse_resp

        await _sse_send(sse_resp, {"type": "transcription", "content": user_text})

        async with _llm_lock:
            await _run_pipeline_streaming(sse_resp, user_text)

    except Exception as e:
        logger.exception("Voice upload error: %s", e)
        try:
            await _sse_send(sse_resp, {"type": "error", "content": str(e)})
            await _sse_send(sse_resp, {"type": "done"})
        except Exception:
            pass
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)

    return sse_resp


# ── Image upload ──────────────────────────────────────────────

async def api_upload_image(request: web.Request) -> web.StreamResponse:
    sse_resp = await _sse_start(request)
    image_path = None

    try:
        reader = await request.multipart()
        caption = ""

        async for field in reader:
            if field.name == "image":
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    image_path = tmp.name
                    while True:
                        chunk = await field.read_chunk(16384)
                        if not chunk:
                            break
                        tmp.write(chunk)
            elif field.name == "caption":
                caption = (await field.read(decode=True)).decode("utf-8", errors="replace")

        if not image_path:
            await _sse_send(sse_resp, {"type": "error", "content": "No image received."})
            await _sse_send(sse_resp, {"type": "done"})
            return sse_resp

        await _sse_send(sse_resp, {"type": "status", "content": "Looking at your image…"})
        user_text = await asyncio.to_thread(
            process_input, InputType.IMAGE, caption, image_path
        )

        async with _llm_lock:
            await _run_pipeline_streaming(sse_resp, user_text)

    except Exception as e:
        logger.exception("Image upload error: %s", e)
        try:
            await _sse_send(sse_resp, {"type": "error", "content": str(e)})
            await _sse_send(sse_resp, {"type": "done"})
        except Exception:
            pass
    finally:
        if image_path:
            Path(image_path).unlink(missing_ok=True)

    return sse_resp


# ── Document upload ───────────────────────────────────────────

async def api_upload_file(request: web.Request) -> web.StreamResponse:
    sse_resp = await _sse_start(request)
    tmp_path = None

    try:
        reader = await request.multipart()
        caption = ""
        filename = "document"

        async for field in reader:
            if field.name == "file":
                filename = field.filename or "document"
                suffix = Path(filename).suffix.lower() or ".txt"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp_path = tmp.name
                    while True:
                        chunk = await field.read_chunk(16384)
                        if not chunk:
                            break
                        tmp.write(chunk)
            elif field.name == "caption":
                caption = (await field.read(decode=True)).decode("utf-8", errors="replace")

        if not tmp_path:
            await _sse_send(sse_resp, {"type": "error", "content": "No file received."})
            await _sse_send(sse_resp, {"type": "done"})
            return sse_resp

        await _sse_send(sse_resp, {"type": "status", "content": f"Reading {filename}…"})

        suffix = Path(filename).suffix.lower()
        if suffix == ".pdf":
            import pypdf
            pdf = pypdf.PdfReader(tmp_path)
            file_text = "\n".join(p.extract_text() or "" for p in pdf.pages).strip()
        elif suffix == ".csv":
            import csv
            with open(tmp_path, newline="", encoding="utf-8", errors="replace") as f:
                rows = list(csv.reader(f))
            file_text = "\n".join(", ".join(row) for row in rows[:300])
        else:
            with open(tmp_path, encoding="utf-8", errors="replace") as f:
                file_text = f.read(60_000)

        if not file_text.strip():
            await _sse_send(sse_resp, {"type": "error", "content": "Could not extract text from document."})
            await _sse_send(sse_resp, {"type": "done"})
            return sse_resp

        if len(file_text) > 8000:
            file_text = file_text[:8000] + "\n…[truncated]"

        user_text = f"[Document: {filename}]\n{file_text}"
        if caption:
            user_text = f"{caption}\n\n{user_text}"

        async with _llm_lock:
            await _run_pipeline_streaming(sse_resp, user_text)

    except Exception as e:
        logger.exception("Document upload error: %s", e)
        try:
            await _sse_send(sse_resp, {"type": "error", "content": str(e)})
            await _sse_send(sse_resp, {"type": "done"})
        except Exception:
            pass
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)

    return sse_resp


# ── Data endpoints ────────────────────────────────────────────

async def api_new(request: web.Request) -> web.Response:
    global _session_counter
    _session_counter += 1
    return web.json_response({"ok": True, "session": _current_session()})


async def api_schedule(request: web.Request) -> web.Response:
    events = db.get_upcoming_events(_conn)
    return web.json_response(events)


async def api_todos(request: web.Request) -> web.Response:
    todos = db.get_pending_todos(_conn)
    return web.json_response(todos)


async def api_notes(request: web.Request) -> web.Response:
    notes = db.get_recent_notes(_conn)
    return web.json_response(notes)


async def api_usage(request: web.Request) -> web.Response:
    used = brave_search.get_monthly_usage(_conn)
    limit = brave_search._MONTHLY_LIMIT
    return web.json_response({"used": used, "limit": limit, "remaining": max(0, limit - used)})


async def api_status(request: web.Request) -> web.Response:
    from . import briefing

    events = db.get_upcoming_events(_conn)[:3]
    todos = db.get_pending_todos(_conn)[:5]
    notes = db.get_recent_notes(_conn)[:3]
    briefing_text = await asyncio.to_thread(briefing.assemble, _conn)
    dory = await asyncio.to_thread(dory_bridge.stats)

    return web.json_response({
        "session": _current_session(),
        "events": events,
        "todos": todos,
        "notes": notes,
        "briefing_preview": briefing_text[:500],
        "dory": dory,
    })


async def api_dory_memories(request: web.Request) -> web.Response:
    query = (request.query.get("q") or "").strip()
    node_type = (request.query.get("type") or "").strip()
    zone = (request.query.get("zone") or "").strip()
    limit_raw = (request.query.get("limit") or "40").strip()
    try:
        limit = max(1, min(200, int(limit_raw)))
    except ValueError:
        limit = 40

    payload = await asyncio.to_thread(
        dory_bridge.inspect_memories,
        query,
        node_type,
        zone,
        limit,
    )
    return web.json_response(payload)


async def api_conversations(request: web.Request) -> web.Response:
    rows = _conn.execute(
        """
        SELECT
            session_id,
            MIN(created_at) AS started_at,
            MAX(created_at) AS last_at,
            COUNT(*) AS msg_count,
            (SELECT content FROM messages m2
             WHERE m2.session_id = m.session_id AND m2.role = 'user'
             ORDER BY m2.created_at ASC LIMIT 1) AS preview
        FROM messages m
        GROUP BY session_id
        ORDER BY last_at DESC
        LIMIT 30
        """
    ).fetchall()
    return web.json_response([dict(r) for r in rows])


async def api_briefing(request: web.Request) -> web.StreamResponse:
    sse_resp = await _sse_start(request)
    from . import briefing
    text = await asyncio.to_thread(briefing.assemble, _conn)
    for i in range(0, len(text), 4):
        chunk = text[i:i + 4]
        await _sse_send(sse_resp, {"type": "token", "content": chunk})
        await asyncio.sleep(0.01)
    await _sse_send(sse_resp, {"type": "done"})
    return sse_resp


# ── Push endpoints ────────────────────────────────────────────

async def api_push_subscribe(request: web.Request) -> web.Response:
    try:
        sub_json = await request.json()
    except Exception:
        raise web.HTTPBadRequest(reason="Invalid JSON")

    endpoint = sub_json.get("endpoint", "")
    if not endpoint:
        raise web.HTTPBadRequest(reason="Missing endpoint")

    db.save_push_subscription(_conn, endpoint, json.dumps(sub_json))
    return web.json_response({"ok": True})


async def api_push_vapid_key(request: web.Request) -> web.Response:
    return web.json_response({"public_key": config.VAPID_PUBLIC_KEY})


# ── Demo endpoints ────────────────────────────────────────────

_DEMO_SYSTEM = (
    "You are Elwin, a local AI companion built by Michael Martin. "
    "You run entirely on local hardware — no cloud, no subscriptions. "
    "This is a portfolio demo. Be friendly, conversational, and concise. "
    "You can discuss your capabilities, answer questions, or just chat. "
    "Do not make up personal information about Michael or the user."
)

async def demo_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_STATIC / "demo.html")

async def api_demo_status(request: web.Request) -> web.Response:
    ip = _get_client_ip(request)
    return web.json_response({"remaining": _demo_remaining(ip), "limit": DEMO_LIMIT})

async def api_demo_chat(request: web.Request) -> web.StreamResponse:
    ip = _get_client_ip(request)

    if not _demo_consume(ip):
        sse_resp = await _sse_start(request)
        await _sse_send(sse_resp, {
            "type": "error",
            "content": f"Demo limit reached ({DEMO_LIMIT} messages per day). Come back tomorrow!"
        })
        await _sse_send(sse_resp, {"type": "done"})
        return sse_resp

    try:
        data = await request.json()
    except Exception:
        raise web.HTTPBadRequest(reason="Invalid JSON")

    user_text = (data.get("text") or "").strip()
    history = data.get("history") or []  # list of {role, content}
    if not user_text:
        raise web.HTTPBadRequest(reason="text required")

    # Build messages: system + recent history (last 10 turns) + new user message
    messages = [{"role": "system", "content": _DEMO_SYSTEM}]
    for turn in history[-10:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": user_text})

    sse_resp = await _sse_start(request)
    remaining = _demo_remaining(ip)
    await _sse_send(sse_resp, {"type": "remaining", "content": remaining})

    loop = asyncio.get_running_loop()
    token_queue: asyncio.Queue = asyncio.Queue()

    def _produce():
        try:
            for tok in llm_client.stream_chat(messages):
                asyncio.run_coroutine_threadsafe(token_queue.put(tok), loop)
        except Exception as exc:
            asyncio.run_coroutine_threadsafe(token_queue.put(("error", str(exc))), loop)
        asyncio.run_coroutine_threadsafe(token_queue.put(None), loop)

    threading.Thread(target=_produce, daemon=True).start()

    while True:
        try:
            item = await asyncio.wait_for(token_queue.get(), timeout=60.0)
        except asyncio.TimeoutError:
            await _sse_send(sse_resp, {"type": "error", "content": "Response timed out."})
            break
        if item is None:
            break
        if isinstance(item, tuple):
            await _sse_send(sse_resp, {"type": "error", "content": item[1]})
            break
        await _sse_send(sse_resp, {"type": "token", "content": item})

    await _sse_send(sse_resp, {"type": "done"})
    return sse_resp


# ── Static / index ────────────────────────────────────────────

async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_STATIC / "index.html")


# ── App factory ───────────────────────────────────────────────

def _make_app() -> web.Application:
    app = web.Application(middlewares=[_auth_middleware])

    app.router.add_get("/", index)
    app.router.add_get("/demo",                    demo_index)
    app.router.add_static("/static", _STATIC)

    app.router.add_get( "/api/demo/status",        api_demo_status)
    app.router.add_post("/api/demo/chat",          api_demo_chat)

    app.router.add_post("/api/auth",               api_auth)
    app.router.add_get( "/api/me",                 api_me)
    app.router.add_post("/api/chat",               api_chat)
    app.router.add_post("/api/upload/voice",       api_upload_voice)
    app.router.add_post("/api/upload/image",       api_upload_image)
    app.router.add_post("/api/upload/file",        api_upload_file)
    app.router.add_post("/api/new",                api_new)
    app.router.add_get( "/api/schedule",           api_schedule)
    app.router.add_get( "/api/todos",              api_todos)
    app.router.add_get( "/api/notes",              api_notes)
    app.router.add_get( "/api/usage",              api_usage)
    app.router.add_get( "/api/status",             api_status)
    app.router.add_get( "/api/dory/memories",      api_dory_memories)
    app.router.add_get( "/api/conversations",      api_conversations)
    app.router.add_get( "/api/briefing",           api_briefing)
    app.router.add_post("/api/push/subscribe",     api_push_subscribe)
    app.router.add_get( "/api/push/vapid-public-key", api_push_vapid_key)

    return app


# ── Entry point ───────────────────────────────────────────────

def main() -> None:
    global _conn

    if not config.WEB_PASSWORD:
        print("WARNING: WEB_PASSWORD is not set in agent.conf — server will reject all logins")
    if config.DORY_ENABLED:
        reason = dory_bridge.status_reason()
        if reason:
            print(f"WARNING: Dory integration unavailable — {reason}")
            print("Install with: pip install 'dory-memory[openai]==0.6.1'")

    if not llm_client.health_check():
        print(
            f"ERROR: Cannot reach llama-server at {config.LLM_BASE_URL}/health"
        )
        print("Start it with: ./start.sh")
        sys.exit(1)

    _conn = db.init_db()

    print("Loading embedding model...", end=" ", flush=True)
    embeddings.get_model()
    print("done.")

    ssl_ctx = None
    if config.TLS_CERT_PATH and config.TLS_KEY_PATH:
        import ssl
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(config.TLS_CERT_PATH, config.TLS_KEY_PATH)
        print(f"TLS enabled ({config.TLS_CERT_PATH})")

    scheme = "https" if ssl_ctx else "http"
    print(f"Web UI: {scheme}://{config.WEB_HOST}:{config.WEB_PORT}")

    app = _make_app()
    web.run_app(
        app,
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        ssl_context=ssl_ctx,
        print=None,  # suppress aiohttp's own startup banner
    )


if __name__ == "__main__":
    main()
