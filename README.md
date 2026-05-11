# Elwin Ransom

A local-first personal AI companion. Runs entirely on your hardware — no cloud inference, no subscriptions, no data leaving the device.

Elwin handles long-term memory, voice I/O, vision, web search, reminders, calendar events, todos, and a daily morning briefing. Three frontends: terminal REPL, Telegram bot, and a browser-based web UI (PWA).

---

## Features

**Memory**
- Three-tier memory: current session → semantic recall (embedding similarity) → extracted long-term facts
- Background fact extraction after every exchange — people, relationships, preferences, and projects stored in SQLite
- Facts are superseded on update, never silently overwritten
- Optional [Dory](https://github.com/MichaelWMartinII/Dory) graph memory integration with a built-in memory inspector in the web UI

**Voice**
- Speech-to-text via `faster-whisper` (local, base.en, int8)
- Text-to-speech via `kokoro-onnx` (American Male voice, OGG Opus output)
- Send a voice message in Telegram, get a voice reply

**Vision**
- Image understanding via any multimodal Ollama model
- `[CAMERA]` marker triggers webcam capture and description
- Works in CLI, Telegram, and web UI

**Web Search**
- Brave Search integration — model emits `[SEARCH: query]`, results fed into a second LLM pass
- Monthly quota tracked locally (1,000 req / $5 tier)

**Scheduling**
- `[REMIND: YYYY-MM-DD HH:MM | message]` — one-shot launchd plist, fires at exact time via macOS notification + Telegram + Web Push
- `[EVENT_ADD: date time | end-time | title]` — calendar event with automatic 15-minute prep alert
- `[TODO_ADD: priority | content]` and `[TODO_DONE: partial text]` — task management
- `[NOTE: content]` — quick capture

**Morning Briefing**
- Delivered daily at a configured time via Telegram and Web Push
- Contents: date, weather, today's events, pending todos, due reminders, focus suggestion, Dory signals

**Frontends**
- **Terminal REPL** — full feature set, slash commands, streaming output
- **Telegram bot** — single-owner, voice replies, photo analysis, all markers
- **Web UI** — aiohttp server, SSE streaming, password-protected, PWA-enabled, Dory memory inspector

---

## Stack

| Component | Library / Tool |
|-----------|----------------|
| LLM inference | [Ollama](https://ollama.com) (any compatible model) |
| Embeddings | `sentence-transformers` — `all-MiniLM-L6-v2` (384-dim, local) |
| STT | `faster-whisper` (base.en, int8, CTranslate2) |
| TTS | `kokoro-onnx` (v1.0 int8, American Male) |
| Vision | Ollama multimodal model |
| Storage | SQLite (WAL mode, 9 tables) |
| Web server | `aiohttp` |
| Telegram | `python-telegram-bot` |
| Web Push | `pywebpush` (VAPID) |
| Scheduling | macOS launchd |
| Search | Brave Search API |

No vector database. Similarity search is brute-force cosine via NumPy — simple, dependency-free, fast enough for personal use.

---

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) — `brew install ollama` or download from ollama.com
- ffmpeg — `brew install ffmpeg`
- macOS (launchd scheduling, avfoundation camera; core chat works on Linux)

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/MichaelWMartinII/ElwinRansom.git
cd ElwinRansom

# 2. Pull a model
ollama pull gemma3:12b   # or any model you prefer

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Configure
cp agent.conf.example agent.conf
# Edit agent.conf — set OLLAMA_MODEL and any optional integrations

# 5. Run
ollama serve &
python -m companion        # terminal REPL
```

---

## Configuration

All settings live in `agent.conf`:

```bash
# LLM — any model installed in Ollama
OLLAMA_MODEL="gemma3:12b"
HOST="127.0.0.1"
PORT="11434"
CTX_SIZE="32768"

# Telegram (optional)
TELEGRAM_BOT_TOKEN="..."
TELEGRAM_OWNER_ID="..."        # your Telegram user ID — all others rejected

# Brave Search (optional)
BRAVE_SEARCH_API_KEY="..."     # $5/month for 1,000 searches

# Vision (optional — uses same Ollama if your model is multimodal)
VISION_HOST="127.0.0.1"
VISION_PORT="11434"

# Web UI
WEB_HOST="0.0.0.0"
WEB_PORT="7272"
WEB_PASSWORD="changeme"

# Morning briefing
BRIEFING_HOUR="8"
BRIEFING_MINUTE="0"
LOCATION="Your City, State"

# Dory graph memory (optional — pip install dory-memory)
DORY_ENABLED="0"
DORY_DB_PATH="./memories/dory_elwin.db"
DORY_MODE="stable"             # stable | aggressive | manual
```

---

## Running

```bash
# Terminal REPL
python -m companion

# Web UI at http://localhost:7272
python -m companion.webapp

# Telegram bot
python -m companion.telegram_bot

# All at once (servers + bot)
./start-all.sh

# Double-click launcher (macOS — starts everything, stops on window close)
open Elwin.command
```

**Install as persistent background service (starts at login):**
```bash
python install.py
python install.py --uninstall   # remove
```

Check status:
```bash
./health.sh
launchctl list | grep elwin
tail -f /tmp/com.elwin.bot.log
```

---

## How Memory Works

```
Every turn
│
├── User message → embed (384-dim) → store in SQLite
│
├── Context assembly
│   ├── System prompt (persona + people + facts + today's schedule + todos)
│   ├── Semantic recall — top-5 cosine-similar messages from past sessions
│   ├── Dory long-term memory (optional graph retrieval)
│   └── Recent session turns (trimmed oldest-first to fit token budget)
│
├── LLM response → stream to frontend → store + embed
│
└── Background thread: extract facts + people → store in SQLite
```

Facts are only extracted from things the user explicitly says. Nothing the assistant generates gets stored as ground truth.

**Token budgeting** scales with `CTX_SIZE`:
- ~25% reserved for response
- ~17% for system prompt
- ~12% for semantic memories
- Remainder for recent conversation turns

---

## Marker Reference

Elwin emits structured markers in its responses to trigger actions. All markers are stripped before display — the user sees only the natural response.

| Marker | Action |
|--------|--------|
| `[SEARCH: query]` | Brave web search → second LLM pass with results injected |
| `[REMIND: YYYY-MM-DD HH:MM \| message]` | Schedule launchd one-shot reminder |
| `[EVENT_ADD: date time \| end-time \| title]` | Add calendar event + 15-min prep alert |
| `[TODO_ADD: priority \| content]` | Add task (high / medium / low) |
| `[TODO_DONE: partial text]` | Mark task done by fuzzy match |
| `[NOTE: content]` | Save a note |
| `[CAMERA]` | Capture webcam frame + describe via vision model |

---

## CLI Commands

```
/new                       Start a new session
/image <path> [question]   Describe an image or ask about it
/voice <path>              Transcribe audio file and chat
/people                    List known people
/facts [name]              Show extracted facts
/reminders                 Pending reminders
/schedule                  7-day event list
/todos                     Pending tasks
/notes                     Recent notes
/stats                     DB stats + Dory status
/usage                     Brave Search quota
/help                      Command list
/quit                      Exit
```

---

## Project Structure

```
companion/
├── config.py           Agent.conf → constants, token budgets
├── db.py               SQLite schema + CRUD (9 tables, WAL mode)
├── embeddings.py       all-MiniLM-L6-v2 + brute-force cosine search
├── memory.py           Context assembly pipeline
├── prompts.py          System prompt builder
├── extractor.py        Background fact extraction (daemon thread)
├── pipeline.py         Shared LLM pipeline used by all frontends
├── controller.py       Input normalizer (text / image / voice → text)
├── llm_client.py       Streaming HTTP to Ollama (pure stdlib urllib)
├── audio.py            STT via faster-whisper
├── tts.py              TTS via kokoro-onnx → OGG Opus
├── vision.py           Image understanding via Ollama multimodal
├── camera.py           Webcam capture via ffmpeg / avfoundation
├── brave_search.py     Brave Search API + monthly quota tracking
├── dory_bridge.py      Optional Dory graph memory integration
├── reminder.py         [REMIND:] → launchd plist + delivery
├── schedule.py         [EVENT_ADD / TODO_ADD / NOTE:] handlers
├── briefing.py         Morning briefing assembly + delivery
├── fire_reminder.py    launchd entry: deliver reminder at due time
├── fire_prep.py        launchd entry: 15-min event prep alert
├── cli.py              Terminal REPL
├── telegram_bot.py     Telegram bot frontend
└── webapp.py           aiohttp web UI + SSE streaming
```

---

## Privacy

- All inference runs locally via Ollama
- No telemetry, no analytics
- SQLite database stays on device
- Brave Search is the only optional external call (web search feature)
- Weather fetched from wttr.in for morning briefing (no account required)

---

## License

MIT
