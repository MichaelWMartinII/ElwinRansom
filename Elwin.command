#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────
# Elwin Ransom — one-click launcher
# Double-click to start. Close this window to stop everything.
# ──────────────────────────────────────────────────────────────

cd "/Users/michael/Repo/Agent"

CONF_FILE="./agent.conf"
source "$CONF_FILE"

PIDS=()

cleanup() {
  echo ""
  echo "[Elwin] Shutting down..."
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  sleep 2
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
  rm -f .llama-server.pid .vision-server.pid
  echo "[Elwin] Stopped."
  exit 0
}

trap cleanup SIGINT SIGTERM SIGHUP EXIT

MODEL_DIR="./models"
LOG_DIR_PATH="./$LOG_DIR"
mkdir -p "$LOG_DIR_PATH"

# ── Start LLM server ─────────────────────────────────────────
MODEL_PATH="$MODEL_DIR/$MODEL"
if [[ ! -f "$MODEL_PATH" ]]; then
  echo "[ERROR] Model not found: $MODEL_PATH"
  exit 1
fi

LLM_LOG="$LOG_DIR_PATH/llama-server-$(date +%Y%m%d-%H%M%S).log"
LLM_ARGS=(
  --model "$MODEL_PATH"
  --host "$HOST" --port "$PORT"
  --ctx-size "$CTX_SIZE"
  --n-gpu-layers "$N_GPU_LAYERS"
  --fit off
)
[[ -n "$API_KEY" ]] && LLM_ARGS+=(--api-key "$API_KEY")
[[ "$PARALLEL" != "auto" ]] && LLM_ARGS+=(--parallel "$PARALLEL")
[[ "$THREADS" != "-1" ]] && LLM_ARGS+=(--threads "$THREADS")
[[ "$THREADS_BATCH" != "-1" ]] && LLM_ARGS+=(--threads-batch "$THREADS_BATCH")
[[ -n "$FLASH_ATTN" ]] && LLM_ARGS+=(--flash-attn "$FLASH_ATTN")

echo "[Elwin] Starting LLM server..."
llama-server "${LLM_ARGS[@]}" >> "$LLM_LOG" 2>&1 &
PIDS+=($!)
echo "$!" > .llama-server.pid

# ── Start vision server ──────────────────────────────────────
VISION_MODEL="Qwen3VL-2B-Instruct-Q8_0.gguf"
VISION_MMPROJ="mmproj-Qwen3VL-2B-Instruct-F16.gguf"
VISION_LOG="$LOG_DIR_PATH/vision-server-$(date +%Y%m%d-%H%M%S).log"

VISION_ARGS=(
  --model "$MODEL_DIR/$VISION_MODEL"
  --mmproj "$MODEL_DIR/$VISION_MMPROJ"
  --host "$VISION_HOST" --port "$VISION_PORT"
  --ctx-size 4096 --n-gpu-layers 0 --parallel 1
)
[[ -n "$VISION_API_KEY" ]] && VISION_ARGS+=(--api-key "$VISION_API_KEY")

echo "[Elwin] Starting vision server..."
llama-server "${VISION_ARGS[@]}" >> "$VISION_LOG" 2>&1 &
PIDS+=($!)
echo "$!" > .vision-server.pid

# ── Wait for both servers ─────────────────────────────────────
echo "[Elwin] Waiting for servers..."

wait_for() {
  local name="$1" url="$2" max=180 elapsed=0
  while (( elapsed < max )); do
    if curl -sf "$url" >/dev/null 2>&1; then
      echo "[Elwin] $name ready (${elapsed}s)"
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  echo "[ERROR] $name failed to start"
  return 1
}

wait_for "LLM"    "http://${HOST}:${PORT}/health"
wait_for "Vision" "http://${VISION_HOST}:${VISION_PORT}/health"

# ── Start Telegram bot ────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Elwin Ransom — running"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  LLM:    http://${HOST}:${PORT}"
echo "  Vision: http://${VISION_HOST}:${VISION_PORT}"
echo "  Bot:    Telegram"
echo "  Web:    http://${WEB_HOST}:${WEB_PORT}"
echo ""
echo "  Close this window to stop everything."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

.venv/bin/python -m companion.telegram_bot &
PIDS+=($!)

.venv/bin/python -m companion.webapp &
PIDS+=($!)

# Keep alive until window closes
wait
