# Elwin Ransom

A local-first personal AI companion. Runs entirely on your hardware — no cloud, no subscriptions, no data leaving the device.

Elwin handles long-term memory, voice I/O, vision, web search, reminders, calendar events, todos, and a daily morning briefing. Accessible via terminal REPL or Telegram.

---

## Features

**Conversation + Memory**
- Three-tier memory: current session → semantic recall (embedding similarity) → extracted long-term facts
- Background fact extraction after every exchange — people, relationships, preferences stored in SQLite
- Context budget scales automatically with model context window size
- Optional `Dory` integration for graph memory, long-term retrieval, and a memory inspector in the web UI

**Voice**
- Speech-to-text via `faster-whisper` (runs locally, base.en model)
- Text-to-speech via `kokoro-onnx` (American Male voice, outputs OGG Opus for Telegram)
- Send a voice message, get a voice reply

**Vision**
- Secondary vision model (Qwen3-VL-2B) on a separate server
- Analyze images from Telegram, CLI, or live webcam capture
- Model triggers camera with `[CAMERA]` marker

**Web Search**
- Optional Brave Search integration
- Model emits `[SEARCH: query]` — results fed back in a second LLM pass
- Quota tracked locally (1,000 req/month on $5 tier)

**Butler**
- `[REMIND: YYYY-MM-DD HH:MM | message]` — schedules launchd notification + Telegram alert
- `[EVENT_ADD: date time | end time | title]` — calendar event with 15-min prep alert
- `[TODO_ADD: priority | content]` / `[TODO_DONE: text]`
- `[NOTE: content]`

**Morning Briefing**
- Daily scheduled via launchd (configurable time)
- Weather, today's events, pending todos, due reminders
- Suggested first move plus Dory-derived memory signals
- Delivered via Telegram + web push

**Frontends**
- Terminal REPL (`python -m companion`)
- Telegram bot (single-owner, locked by user ID)
- Web UI with presence panel and Dory memory inspector
- Both use identical pipeline — just different I/O layers

---

## Models

| Role | Model | Notes |
|---|---|---|
| Primary LLM | Qwen3-14B-Q4_K_M | Dense, 14B params, Q4 quant |
| Vision | Qwen3-VL-2B-Instruct-Q8 | Multimodal, separate server |
| Embeddings | all-MiniLM-L6-v2 | 384-dim, local cosine similarity |
| STT | faster-whisper base.en | CTranslate2, int8 |
| TTS | kokoro-onnx v1.0 int8 | American Male voice |

All models run locally. No API calls for inference.

---

## Requirements

- Python 3.11+
- `llama-server` binary in PATH (from [llama.cpp](https://github.com/ggerganov/llama.cpp))
- ffmpeg (`brew install ffmpeg`) — for voice and camera
- GGUF model files in `./models/`
- macOS (launchd scheduling, Metal GPU acceleration)

---

## Setup

**1. Place models in `./models/`:**
```
models/
  Qwen3-14B-Q4_K_M.gguf
  Qwen3VL-2B-Instruct-Q8_0.gguf
  mmproj-Qwen3VL-2B-Instruct-F16.gguf
  voices-v1.0.bin
```

**2. Install Python deps:**
```bash
pip install -r requirements.txt
```

This now includes the published `dory-memory` package:
```bash
pip install 'dory-memory[openai]==0.6.1'
```

**3. Configure `agent.conf`:**
```bash
MODEL="Qwen3-14B-Q4_K_M.gguf"
HOST="127.0.0.1"
PORT="59086"
API_KEY="your-key-here"
CTX_SIZE="4096"
N_GPU_LAYERS="99"          # 0 for CPU-only
TELEGRAM_BOT_TOKEN="..."   # optional
TELEGRAM_OWNER_ID="..."    # your Telegram user ID
BRAVE_SEARCH_API_KEY="..." # optional
BRIEFING_HOUR="8"
LOCATION="Your City, ST"
DORY_ENABLED="1"
DORY_MODE="stable"
DORY_DB_PATH="./memories/dory_elwin.db"
```

`DORY_MODE` controls how aggressively Elwin asks Dory to extract and flush:
- `stable` — safer default for one local llama server
- `aggressive` — faster memory formation, higher chance of local model contention
- `manual` — log turns to Dory but avoid automatic flush/extraction pressure

**4. Run:**
```bash
# Terminal REPL
python -m companion

# Or start servers + Telegram bot together
./start-all.sh
python -m companion.telegram_bot

# Or double-click launcher (starts everything, stops on close)
./Elwin.command
```

If `DORY_ENABLED` is on and `dory-memory` is not installed, Elwin will start but
print a warning and fall back to its built-in SQLite memory path.

**5. Install as background service (starts at login):**
```bash
python install.py
# Uninstall:
python install.py --uninstall
```

---

## Architecture

```
Input (text / image / voice)
  └─▶ Controller — normalizes to text
        └─▶ SQLite — save turn + embed
              └─▶ Context assembly
                    • System prompt (persona, facts, people, todos)
                    • Semantic recall (top-5 similar past messages)
                    • Recent session turns
                    • Current message
                  └─▶ LLM (llama-server, streaming)
                        └─▶ Marker detection
                              [SEARCH]     → Brave API → second LLM pass
                              [REMIND]     → launchd plist + DB
                              [EVENT_ADD]  → DB + prep alert
                              [TODO_*]     → DB
                              [CAMERA]     → ffmpeg + vision server
                              [NOTE]       → DB
                        └─▶ Response saved + embedded
                              └─▶ Background fact extraction
```

**Storage:** Single SQLite file (`memories/companion.db`) with WAL mode. Tables: messages, embeddings, facts, people, reminders, events, todos, notes, search_usage.

**Dory:** Optional second memory layer stored separately at `memories/dory_elwin.db`.
Elwin mirrors conversation turns into Dory, injects Dory retrieval into prompt
assembly, and exposes Dory status plus a memory inspector in the web UI.

**Scheduling:** Native macOS launchd — no polling loops, no cron. Reminders and briefing run as proper system agents.

---

## CLI Commands

```
/image <path>     analyze a local image
/voice            record a voice message
/people           list known people
/facts            list extracted facts
/schedule         show upcoming events
/todos            show todo list
/notes            show saved notes
/reminders        show pending reminders
/stats            memory + usage stats
/usage            search API usage
```

---

## Privacy

- All inference runs locally
- No telemetry
- SQLite database stays on device
- Brave Search is the only optional external call (web search feature)
- Weather fetched from wttr.in for morning briefing (no account required)
