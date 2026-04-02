#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────
# start-all.sh — Start both the main LLM server and vision server.
# Ctrl-C (or SIGTERM) stops both cleanly.
# ──────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Starting all servers"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Start vision server in background; it manages its own PID file.
"$SCRIPT_DIR/start-vision.sh" &
VISION_BG=$!

cleanup() {
  echo ""
  echo "[INFO] Shutting down vision server..."
  kill -TERM "$VISION_BG" 2>/dev/null || true
  wait "$VISION_BG" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Start main server in foreground — Ctrl-C lands here first,
# then the trap above cleans up the vision server.
"$SCRIPT_DIR/start.sh" "$@"
