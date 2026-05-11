#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────
# Vision Server — start-vision.sh
# Runs Qwen3-VL-2B via llama-server for image understanding.
# Port and API key are read from agent.conf.
# ──────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_FILE="$SCRIPT_DIR/agent.conf"
MODEL_DIR="$SCRIPT_DIR/models"
PID_FILE="$SCRIPT_DIR/.vision-server.pid"

# ── Load configuration ────────────────────────────────────────
if [[ ! -f "$CONF_FILE" ]]; then
  echo "[ERROR] Config file not found: $CONF_FILE"
  exit 1
fi
source "$CONF_FILE"

VISION_MODEL="Qwen3VL-2B-Instruct-Q8_0.gguf"
VISION_MMPROJ="mmproj-Qwen3VL-2B-Instruct-F16.gguf"

# ── Validate model files ─────────────────────────────────────
if [[ ! -f "$MODEL_DIR/$VISION_MODEL" ]]; then
  echo "[ERROR] Vision model not found: $MODEL_DIR/$VISION_MODEL"
  exit 1
fi
if [[ ! -f "$MODEL_DIR/$VISION_MMPROJ" ]]; then
  echo "[ERROR] Vision mmproj not found: $MODEL_DIR/$VISION_MMPROJ"
  exit 1
fi

# ── Kill stale vision server ─────────────────────────────────
if [[ -f "$PID_FILE" ]]; then
  old_pid=$(cat "$PID_FILE")
  if kill -0 "$old_pid" 2>/dev/null; then
    echo "[WARN] Killing existing vision server (PID $old_pid)"
    kill -TERM "$old_pid" 2>/dev/null || true
    sleep 2
    kill -9 "$old_pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi

# ── Setup logging ─────────────────────────────────────────────
mkdir -p "$SCRIPT_DIR/$LOG_DIR"
RUN_LOG="$SCRIPT_DIR/$LOG_DIR/vision-server-$(date +%Y%m%d-%H%M%S).log"

# ── Build arguments ───────────────────────────────────────────
LLAMA_ARGS=(
  --model "$MODEL_DIR/$VISION_MODEL"
  --mmproj "$MODEL_DIR/$VISION_MMPROJ"
  --host "$VISION_HOST"
  --port "$VISION_PORT"
  --ctx-size 4096
  --n-gpu-layers 99
  --flash-attn on
  --parallel 1
)

if [[ -n "$VISION_API_KEY" ]]; then
  LLAMA_ARGS+=(--api-key "$VISION_API_KEY")
fi

# ── Graceful shutdown ─────────────────────────────────────────
SERVER_PID=""

cleanup() {
  echo ""
  echo "[INFO] Shutting down vision server..."
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill -TERM "$SERVER_PID" 2>/dev/null
    for i in $(seq 1 10); do
      kill -0 "$SERVER_PID" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$SERVER_PID" 2>/dev/null; then
      kill -9 "$SERVER_PID" 2>/dev/null || true
    fi
  fi
  rm -f "$PID_FILE"
  echo "[INFO] Vision server stopped."
  exit 0
}

trap cleanup SIGINT SIGTERM SIGHUP

# ── Health check ──────────────────────────────────────────────
wait_for_health() {
  local max_wait=60
  local interval=2
  local elapsed=0
  local url="http://${VISION_HOST}:${VISION_PORT}/health"

  echo "[INFO] Waiting for vision server at $url ..."
  while (( elapsed < max_wait )); do
    if curl -sf "$url" >/dev/null 2>&1; then
      echo "[INFO] Vision server is healthy (${elapsed}s)"
      return 0
    fi
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done
  echo "[WARN] Vision server did not start within ${max_wait}s"
  return 1
}

# ── Start ─────────────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Vision Server (Qwen3-VL-2B)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Model:     $VISION_MODEL"
echo "  Projector: $VISION_MMPROJ"
echo "  Endpoint:  http://${VISION_HOST}:${VISION_PORT}"
if [[ -n "$VISION_API_KEY" ]]; then
  echo "  Auth:      API key enabled"
fi
echo "  Log:       $RUN_LOG"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

echo "[INFO] Starting vision server..."
llama-server "${LLAMA_ARGS[@]}" >> "$RUN_LOG" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

if wait_for_health; then
  echo "[INFO] Vision server ready."
  echo "       POST http://${VISION_HOST}:${VISION_PORT}/v1/chat/completions"
  echo ""
  wait "$SERVER_PID" || true
else
  echo "[ERROR] Vision server failed to start. Check $RUN_LOG"
  if kill -0 "$SERVER_PID" 2>/dev/null; then
    kill -TERM "$SERVER_PID" 2>/dev/null || true
    sleep 1
    kill -9 "$SERVER_PID" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  exit 1
fi
