# Elwin Ransom

**A local-first AI companion with long-term memory, voice, vision, and proactive assistance**

Elwin Ransom is a personal AI assistant that runs entirely on your own hardware. It combines a locally served language model, semantic memory retrieval, automatic fact extraction, optional web search, speech recognition, text-to-speech synthesis, image understanding, timed reminders, a calendar, a todo list, and a daily briefing — without sending your data to the cloud.

Named after the protagonist of C.S. Lewis's *Space Trilogy*, Ransom is direct, honest, and professional. No roleplay, no flattery, no fluff.

---

## What it does

Ransom is a conversational assistant for a household. You talk to it through a terminal or Telegram, and it:

- **Remembers past conversations.** Every message is embedded into a vector space. When you ask a question, Ransom retrieves the most relevant past exchanges — even from weeks ago — and weaves them into context.

- **Learns about you over time.** After each conversation turn, a background process extracts facts and people from what *you* said (never from what the model assumed). These accumulate in a knowledge base that enriches every future interaction.

- **Searches the web when needed.** When a question requires current information — weather, news, scores, stock prices — the model signals a search intent, the system executes it via the Brave Search API, and a second LLM pass synthesizes the results into an answer.

- **Understands images.** Photos sent via Telegram (or loaded from disk in the CLI) are described by a local vision model (Qwen3-VL-2B). Ransom can also trigger its own camera to take a snapshot when asked.

- **Speaks and listens.** Voice messages in Telegram are transcribed locally (faster-whisper). Responses to voice messages are synthesized and returned as audio (Kokoro TTS). Voice-in gets voice-out.

- **Sets reminders.** Ask Ransom to remind you of something and it schedules a native macOS notification and a Telegram message at the specified time — powered by launchd, not a polling loop.

- **Manages your calendar, todos, and notes.** Ransom can add events, create and complete todo items, and capture quick notes. Scheduled events get a 15-minute Telegram prep alert that pulls in everything Ransom knows about the people involved.

- **Delivers a morning briefing.** Each day at a configured time, Ransom pushes a Telegram summary: today's date, local weather, calendar events, pending todos, and reminders due today.

- **Works offline.** Web search is optional. The core system — inference, memory, fact extraction, vision, voice — runs with no internet connection.

---

## Why it exists

Cloud AI services are powerful but come with trade-offs: your conversations live on someone else's servers, you pay per token, and you depend on uptime and policy decisions you don't control.

Ransom takes the opposite approach. The language model runs on your machine. The database is a SQLite file on your disk. The only external call is an optional, rate-limited web search. You own the entire stack.

This matters for a family assistant. Conversations touch on personal topics — health, finances, relationships, children, plans. Keeping that data local isn't paranoia; it's basic hygiene.

The project also explores what a small local model can do when given proper scaffolding. A model running quantized on a laptop can't match a frontier API model on raw capability, but with persistent memory, fact accumulation, web access, vision, voice, and proactive scheduling, it can be a genuinely useful long-running companion.

---

## How it works

### Architecture

```
User (terminal or Telegram)
  |
  ├── Text → pass through
  ├── Image → Vision server (Qwen3-VL-2B) → text description
  └── Voice → faster-whisper (base.en) → transcribed text
                          |
                          v
              Input saved to SQLite + embedded into vector space
                          |
                          v
              Context assembly:
                1. System prompt (persona + schedule + todos + known people + facts + tool instructions)
                2. Semantic memory (top-5 past messages by cosine similarity)
                3. Recent conversation turns (trimmed to fit token budget)
                4. Current user message
                          |
                          v
              LLM pass 1 (streamed to terminal / buffered for Telegram)
                          |
                          v
              Marker detection in model output:
                [SEARCH: query]   → Brave API → LLM pass 2 with results
                [REMIND: ...]     → save reminder + schedule launchd plist
                [EVENT_ADD: ...]  → save event + schedule 15-min prep plist
                [TODO_ADD: ...]   → save todo
                [TODO_DONE: ...]  → mark todo complete
                [NOTE: ...]       → save note
                [CAMERA]          → capture photo → vision description → reply with image
                          |
                          v
              Save final response, embed it, extract facts in background
                          |
                          v
              Telegram voice-in: synthesize TTS → reply_voice (Kokoro ONNX)
```

### The LLM

Ransom uses [llama.cpp](https://github.com/ggerganov/llama.cpp)'s `llama-server` to serve a GGUF-quantized model locally. Multiple models are available in `models/`; the current default configured in `agent.conf` is **Qwen3-14B-Q4_K_M** (a dense 14B model). Also present: Qwen3-30B-A3B-Instruct (MoE, 3B active parameters) and an abliterated variant.

The server exposes an OpenAI-compatible API on localhost. The Python code communicates with it using `urllib` from the standard library — no HTTP client dependencies.

A managed shell script (`start.sh`) handles process lifecycle: health checks, auto-restart on crash, graceful shutdown, log rotation, and stale process cleanup.

### Vision

A second `llama-server` instance runs **Qwen3-VL-2B** (with multimodal projector) on a separate port. When an image arrives — from a Telegram photo, a CLI `/image` command, or a `[CAMERA]` trigger — `vision.py` base64-encodes it and sends it to this server, which returns a text description. The description is injected into the conversation as `[Image: ...]` so the main LLM can reason about it.

The camera module (`camera.py`) captures a single JPEG frame from the laptop's built-in camera using `ffmpeg` / `avfoundation`. The model can request this autonomously by emitting `[CAMERA]` in its response.

### Speech recognition and TTS

**Speech-to-text** (`audio.py`): Uses `faster-whisper` with the `base.en` model (CTranslate2, int8, ~150 MB). Audio is piped through `ffmpeg` to 16 kHz mono PCM, then transcribed. Supports any audio format ffmpeg can read (`.ogg`, `.mp3`, `.wav`, etc.). Lazy-loaded on first use.

**Text-to-speech** (`tts.py`): Uses `kokoro-onnx` (v1.0, int8) with the `am_liam` voice (American Male). Produces OGG Opus audio for Telegram's `reply_voice`. Synthesized in a thread; lazy-loaded on first use.

In the Telegram bot, sending a voice message triggers the full pipeline and returns a voice reply. Sending text continues to return text.

### Memory system

Memory operates on three timescales:

**Short-term** — the current conversation session. Recent turns are included directly in the prompt, trimmed from oldest first to fit the token budget.

**Medium-term** — semantic recall. Every message (user and assistant) is embedded using `all-MiniLM-L6-v2` (a 384-dimensional sentence transformer that runs locally). At each turn, the current user query is compared against all stored embeddings by cosine similarity, and the top 5 most relevant past messages are injected into the system prompt. This lets Ransom reference conversations from days or weeks ago when they're relevant.

**Long-term** — extracted facts and people. After each exchange, a background thread sends the conversation to the LLM with an extraction prompt. The model identifies people mentioned (with relationships) and discrete facts stated by the user (preferences, events, concerns, projects). These are stored in structured tables and included in every system prompt going forward. Facts can be superseded as information changes.

A critical design choice: the extractor only records facts explicitly stated by the *user*. Anything the assistant said, assumed, or embellished is ignored. This prevents hallucinated information from polluting the knowledge base.

### Web search

Since `llama-server` runs without `--jinja`, native tool calling isn't available. Instead, Ransom uses a prompt-based approach:

1. The system prompt instructs the model: "You can look up current or real-time information by writing `[SEARCH: your query]` on its own line."
2. After the model generates a response, a regex checks for the `[SEARCH: ...]` marker.
3. If found, the Brave Search API is called, results are formatted, and a second LLM pass generates the final answer incorporating the search results.
4. One search per turn maximum — no recursive loops.

In the terminal, both passes stream transparently so you can see the model decide to search. In Telegram, only the final answer is delivered; a typing indicator covers the processing time.

Usage is tracked in the database against a monthly quota (1,000 requests on the $5/month Brave Search tier).

### Reminders

When the user asks Ransom to remind them of something, the model emits a `[REMIND: YYYY-MM-DD HH:MM | message]` marker. The system:

1. Saves the reminder to the database with a UTC timestamp.
2. Writes a one-shot macOS launchd plist (`~/Library/LaunchAgents/com.elwin.reminder.<id>.plist`) and loads it.
3. At the due time, launchd calls `python -m companion.fire_reminder <id>`, which delivers a macOS notification and a Telegram message, marks the reminder fired, then unloads and deletes its own plist.

### Calendar and butler

The model can manage a calendar, todo list, and notes by emitting structured markers:

| Marker | Effect |
|---|---|
| `[EVENT_ADD: date time \| end time \| title]` | Saves event; schedules a 15-min prep alert via launchd |
| `[TODO_ADD: priority \| content]` | Adds a todo (priority: high/medium/low) |
| `[TODO_DONE: partial text]` | Marks matching pending todo as complete |
| `[NOTE: content]` | Saves a quick note |

The prep alert (`fire_prep.py`) fires 15 minutes before an event and sends a Telegram message with the event title, start time, and any facts Ransom knows about people mentioned in the title.

Today's schedule (upcoming events) and top pending todos are included in the system prompt so Ransom is contextually aware of the day.

### Morning briefing

`briefing.py` assembles a daily summary:

- Current date and greeting
- Local weather (via `wttr.in`)
- Today's calendar events
- Up to 10 pending todos by priority
- Reminders due today

The briefing is delivered via Telegram. The bot installs a daily launchd plist (`com.elwin.briefing`) at startup that fires at `BRIEFING_HOUR:BRIEFING_MINUTE` (default 8:00 AM) configured in `agent.conf`.

### Context budgeting

The token budget scales with `CTX_SIZE` from `agent.conf` (currently 2,048 tokens):

| Segment | Share |
|---|---|
| Response headroom | 25% |
| System prompt (persona + schedule + todos + people + facts) | ~17% |
| Semantic memories | ~12% |
| Recent turns + current message | remainder |

Token counts are estimated with a simple `len(text) // 3` heuristic. Messages and memories are trimmed to fit — recent turns are dropped oldest first, memory results are cut when the budget is exhausted.

### Database

All state lives in a single SQLite database (`memories/companion.db`) running in WAL mode for safe concurrent reads. Nine tables:

- **messages** — every conversation turn with session ID, role, content, timestamp, and estimated token count.
- **embeddings** — raw float32 vector blobs keyed to message IDs.
- **facts** — extracted knowledge with entity, category, content, source message, and a `superseded_by` pointer for fact evolution.
- **people** — known individuals with name, relationship, and notes.
- **search_usage** — logged web searches with query, result count, and timestamp for quota tracking.
- **reminders** — timed reminders with due time (UTC), message, and fired flag.
- **events** — calendar events with title, start time, and optional end time (UTC).
- **todos** — task items with content, priority (high/medium/low), and done flag.
- **notes** — quick freeform notes with archive flag.

---

## Frontends

### Terminal CLI

Run with `python -m companion`. Provides a REPL with streaming output and slash commands:

| Command | Description |
|---|---|
| `/new` | Start a fresh conversation |
| `/image <path> [question]` | Describe an image, or ask a question about it |
| `/voice <path>` | Transcribe audio and send as a message |
| `/people` | List known people |
| `/facts [name]` | Show extracted facts |
| `/reminders` | List pending reminders |
| `/schedule` | List upcoming 7 days of events |
| `/todos` | List pending to-dos |
| `/notes` | List recent notes |
| `/stats` | Database statistics |
| `/usage` | Web search quota |
| `/help` | Command list |
| `/quit` | Exit |

LLM responses stream token-by-token. Search passes, reminders set, events added, and todos created are all printed inline as they are detected.

### Telegram bot

Run with `python -m companion.telegram_bot`. Mirrors the full CLI pipeline behind a Telegram interface:

- Locked to a single owner by Telegram user ID. All other users get "This bot is private."
- A typing indicator pulses every 5 seconds while processing.
- A concurrency lock prevents overlapping requests.
- Long responses are automatically split at natural boundaries to respect Telegram's 4,096-character limit.
- **Text messages** → text reply.
- **Photos** → analyzed by vision model; caption (if any) treated as a question.
- **Voice/audio messages** → transcribed by faster-whisper → LLM → synthesized by Kokoro → voice reply.
- **[CAMERA] trigger** → captures a photo from the laptop camera, describes it via vision model, sends photo + description.
- Action markers (search, reminders, events, todos, notes) handled silently; confirmations sent as follow-up messages.

Telegram commands:

| Command | Description |
|---|---|
| `/start` | Greeting |
| `/new` | Start a fresh conversation |
| `/schedule` | Show upcoming events |
| `/todos` | List pending to-dos |
| `/notes` | Show recent notes |
| `/briefing` | Request the morning briefing on demand |
| `/usage` | Web search quota |

---

## Design principles

**Local-first.** Inference, embedding, vision, speech recognition, TTS, fact extraction, and storage all happen on your machine. The only optional external call is web search.

**Stdlib-first.** The Python code avoids external libraries wherever possible. All HTTP is `urllib`. JSON, threading, regex, gzip, UUID generation, SQLite, plistlib, and subprocess are all standard library.

**Simple over clever.** No framework layers, no dependency injection, no abstract base classes. The embedding search is brute-force cosine similarity over a numpy matrix — no vector database, no approximate nearest neighbors. This is the right choice at personal scale.

**Graceful degradation.** Embedding failures, extraction errors, search failures, vision server unavailability, and TTS failures are caught and swallowed. The chat always continues.

**Honest memory.** Facts are only extracted from what the user explicitly says. The model's assumptions, elaborations, and hallucinations are never stored as ground truth.

**macOS-native scheduling.** Reminders, event prep alerts, and the daily briefing use launchd plists — the system-native job scheduler — rather than a persistent polling process. They fire even if the bot isn't running.

---

## Dependencies

| Dependency | Purpose | Required by |
|---|---|---|
| `llama.cpp` (llama-server) | Local LLM inference and vision inference | Core |
| `sentence-transformers` | Local sentence embeddings | Core |
| `faster-whisper` | Local speech-to-text (base.en, CTranslate2) | Voice input |
| `kokoro-onnx` | Local text-to-speech (Kokoro v1.0 ONNX) | Voice output |
| `soundfile` | WAV file writing for TTS pipeline | Voice output |
| `python-telegram-bot` | Telegram frontend | Telegram bot only |
| `ffmpeg` | Audio conversion (STT pipeline) + camera capture | Voice + camera |
| Python 3.11+ | Runtime | Core |
| SQLite (built into Python) | Persistent storage | Core |
| Brave Search API key | Web search | Optional |

No cloud LLM API. No vector database. No Redis. No Docker.

---

## File structure

```
Agent/
  agent.conf              # All configuration (shell-sourceable, parsed by Python)
  start.sh                # Launch main llama-server with process management
  start-vision.sh         # Launch vision llama-server (Qwen3-VL-2B)
  start-all.sh            # Launch both servers (main + vision)
  stop.sh                 # Gracefully stop main llama-server
  health.sh               # Server diagnostics
  install.py              # Install/uninstall launchd agents (server, vision, bot)
  Elwin.command           # Double-click launcher: both servers + Telegram bot
  requirements.txt        # Five pip packages
  companion/
    __init__.py
    __main__.py            # Entry: python -m companion
    config.py              # Parses agent.conf, derives constants
    llm_client.py          # Streaming HTTP client (stdlib urllib)
    embeddings.py          # Local embeddings + similarity search
    db.py                  # SQLite schema and CRUD (9 tables)
    memory.py              # Context assembly pipeline
    prompts.py             # System prompt template (persona + tool instructions)
    extractor.py           # Background fact extraction
    brave_search.py        # Web search integration (stdlib urllib)
    controller.py          # Input normalizer: TEXT / IMAGE / VOICE → text
    audio.py               # STT via faster-whisper (base.en)
    tts.py                 # TTS via Kokoro ONNX → OGG Opus
    vision.py              # Image understanding via vision llama-server
    camera.py              # Laptop camera capture via ffmpeg/avfoundation
    reminder.py            # [REMIND:] marker parsing + launchd scheduling
    schedule.py            # [EVENT_ADD/TODO_ADD/TODO_DONE/NOTE:] markers + launchd prep
    briefing.py            # Morning briefing assembly + launchd install
    fire_reminder.py       # launchd entry: deliver + clean up a reminder
    fire_prep.py           # launchd entry: 15-min event prep alert
    cli.py                 # Terminal REPL
    telegram_bot.py        # Telegram bot bridge
  memories/
    companion.db           # SQLite database (WAL mode)
  models/
    Qwen3-14B-Q4_K_M.gguf                    # Main LLM (current default)
    Qwen3-30B-A3B-Instruct-2507-Q3_K_M.gguf  # MoE alternative
    Josiefied-Qwen3-30B-A3B-*.gguf           # Abliterated MoE variant
    Qwen3VL-2B-Instruct-Q8_0.gguf            # Vision model
    mmproj-Qwen3VL-2B-Instruct-F16.gguf      # Vision multimodal projector
    kokoro-v1.0.int8.onnx                    # TTS model
    voices-v1.0.bin                           # TTS voice data
```

---

## Limitations

- **Context window.** The current `CTX_SIZE` of 2,048 tokens is small. Long conversations within a single session lose early turns. Semantic memory compensates by surfacing relevant past exchanges, but it's not the same as having the full history in context.

- **Model capability.** A quantized model running on CPU is slower and less capable than frontier cloud models. It works well for factual questions, recommendations, and conversation. It struggles with complex multi-step reasoning.

- **Single user at a time.** The Telegram bot serializes requests behind a lock. The CLI is inherently single-user. This is a personal assistant, not a service.

- **Vision requires a second server.** Image understanding needs the vision llama-server running. Start it with `./start-vision.sh` or use `./start-all.sh` to launch both.

- **Fact extraction quality.** The extraction prompt does a reasonable job, but small local models occasionally miss facts or miscategorize them. The system tolerates this — bad facts are noise in the prompt, not corrupted state, and can be superseded.

- **macOS-only scheduling.** Reminders, event prep alerts, and the daily briefing use macOS launchd. The rest of the system runs on any platform, but these features are macOS-specific.

---

## Getting started

1. Place GGUF model files in `models/` and set the `MODEL` field in `agent.conf`.
2. Install dependencies: `pip install -r requirements.txt`
3. **Quick start:** Double-click `Elwin.command` — starts both servers and the Telegram bot, stops everything on window close.
4. **Manual start:** Run `./start-all.sh` (both servers) or `./start.sh` (LLM only), then `python -m companion` (CLI) or `python -m companion.telegram_bot` (Telegram).
5. **Persistent background service:** Run `python install.py` to register launchd agents for the LLM server, vision server, and Telegram bot. They will start at login and restart on crash.
6. Optionally, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_OWNER_ID` in `agent.conf` for Telegram.
7. Optionally, set `BRAVE_SEARCH_API_KEY` in `agent.conf` to enable web search.
8. Optionally, install `ffmpeg` (via Homebrew) to enable voice transcription and camera capture.
