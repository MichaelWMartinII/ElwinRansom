# Elwin Ransom Release Checklist

Use this before pushing the Dory integration as a polished release.

## Dependency Setup

1. Install fresh dependencies in the Elwin environment:
   ```bash
   pip install -r requirements.txt
   ```
2. Confirm the published Dory package is present:
   ```bash
   python -c "from dory import DoryMemory; print('dory ok')"
   ```
3. Confirm the local LLM server is reachable:
   ```bash
   curl -s http://127.0.0.1:59086/health
   ```
4. For single-server local release testing, keep:
   ```bash
   DORY_MODE="stable"
   ```

## Static Verification

1. Run a syntax pass:
   ```bash
   python3 -m compileall companion
   ```
2. Start the web app and confirm there is no startup warning about missing `dory-memory` unless intentional.

## Runtime Smoke Tests

### CLI

1. Run:
   ```bash
   python -m companion
   ```
2. Send a normal text message.
3. Run `/stats` and confirm Dory is reported as enabled with node counts.
4. Start a new conversation with `/new` and send another message.

### Web UI

1. Run:
   ```bash
   python -m companion.webapp
   ```
2. Log in successfully.
3. Send a chat message.
4. Open `Presence` and confirm Dory status appears.
5. Open `Memory Inspector` and confirm active memories are shown.
6. Confirm the presence panel updates after a new message.

### Telegram

1. Run:
   ```bash
   python -m companion.telegram_bot
   ```
2. Send one text message to the bot.
3. Confirm reply succeeds and no Dory-related runtime errors appear in logs.

## Feature Validation

1. Trigger a morning briefing from web or Telegram.
2. Confirm briefing includes:
   - weather if available
   - events/todos/reminders when present
   - a “First move” suggestion when relevant
   - Dory signals when Dory has usable memory
3. Ask something that should benefit from long-term memory and verify the answer reflects prior context.

## Search / Butler Validation

1. Trigger one web search query and confirm second-pass answer still works.
2. Create one reminder and confirm confirmation text is clean.
3. Create one todo and one note and confirm they appear in web sidebar views.

## Privacy / UX Review

1. Inspect `Memory Inspector` for anything too sensitive or too raw for a public demo.
2. Confirm archived memories are not shown in a confusing way.
3. Confirm the UI does not look broken on mobile width.

## Release Decision

Ship only if all of the following are true:

- `dory-memory[openai]==0.6.1` installs cleanly
- CLI, web, and Telegram all run without Dory import/runtime failures
- Dory node counts increase after real conversations
- Presence panel and memory inspector both work
- Briefing output is coherent
- No obvious sensitive-memory UX issue remains
- Dory mode is intentionally chosen (`stable` recommended for default local release)
