#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────
# Local LLM Inference Server — start.sh
# Serves a GGUF model via llama-server with auto-restart,
# log rotation, health checks, and graceful shutdown.
# Web UI / OpenAI-compatible API at http://$HOST:$PORT
# ──────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_FILE="$SCRIPT_DIR/agent.conf"
PID_FILE="$SCRIPT_DIR/.llama-server.pid"
MODEL_DIR="$SCRIPT_DIR/models"

# ── Load configuration ────────────────────────────────────────
if [[ ! -f "$CONF_FILE" ]]; then
  echo "[ERROR] Config file not found: $CONF_FILE"
  exit 1
fi
# shellcheck source=agent.conf
source "$CONF_FILE"

# ── Allow CLI overrides ───────────────────────────────────────
# Usage: ./start.sh [model_filename]
#   e.g. ./start.sh Josiefied-Qwen3-30B-A3B-abliterated-v2.Q3_K_M.gguf
if [[ ${1:-} ]]; then
  MODEL="$1"
fi

# ── Validate model file ──────────────────────────────────────
MODEL_PATH="$MODEL_DIR/$MODEL"
if [[ ! -f "$MODEL_PATH" ]]; then
  echo "[ERROR] Model file not found: $MODEL_PATH"
  echo ""
  echo "Available models in $MODEL_DIR/:"
  ls -1 "$MODEL_DIR"/*.gguf 2>/dev/null || echo "  (none)"
  exit 1
fi

# ── Kill stale llama-server processes ─────────────────────────
kill_stale() {
  # Check PID file first
  if [[ -f "$PID_FILE" ]]; then
    local old_pid
    old_pid=$(cat "$PID_FILE")
    if kill -0 "$old_pid" 2>/dev/null; then
      echo "[WARN] Killing existing llama-server from PID file (PID $old_pid)"
      kill -TERM "$old_pid" 2>/dev/null || true
      sleep 2
      kill -9 "$old_pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
  fi

  # Also kill any orphaned llama-server processes on our port
  local stale_pids
  stale_pids=$(pgrep -f "llama-server.*--port ${PORT}" 2>/dev/null || true)
  if [[ -n "$stale_pids" ]]; then
    echo "[WARN] Killing orphaned llama-server processes: $stale_pids"
    echo "$stale_pids" | xargs kill -TERM 2>/dev/null || true
    sleep 2
    for pid in $stale_pids; do
      kill -9 "$pid" 2>/dev/null || true
    done
  fi
}

kill_stale

# ── Setup logging ─────────────────────────────────────────────
LOG_PATH="$SCRIPT_DIR/$LOG_DIR"
mkdir -p "$LOG_PATH"

# Per-run timestamped log file
RUN_LOG="$LOG_PATH/llama-server-$(date +%Y%m%d-%H%M%S).log"

# Clean up old log files beyond LOG_MAX_FILES
cleanup_old_logs() {
  local log_files
  log_files=$(ls -1t "$LOG_PATH"/llama-server-*.log 2>/dev/null || true)
  [[ -z "$log_files" ]] && return 0
  local count
  count=$(echo "$log_files" | wc -l)
  if (( count > LOG_MAX_FILES )); then
    echo "$log_files" | tail -n +$((LOG_MAX_FILES + 1)) | xargs rm -f
    echo "[INFO] Cleaned up old log files (kept newest $LOG_MAX_FILES)"
  fi
}

cleanup_old_logs

# ── Build llama-server arguments ─────────────────────────────
LLAMA_ARGS=(
  --model "$MODEL_PATH"
  --host "$HOST"
  --port "$PORT"
)

# Only pass flags when explicitly set to non-default values
if [[ -n "$CTX_SIZE" && "$CTX_SIZE" != "0" ]]; then
  LLAMA_ARGS+=(--ctx-size "$CTX_SIZE")
fi
if [[ -n "$N_GPU_LAYERS" && "$N_GPU_LAYERS" != "auto" ]]; then
  LLAMA_ARGS+=(--n-gpu-layers "$N_GPU_LAYERS")
fi
if [[ -n "$PARALLEL" && "$PARALLEL" != "auto" ]]; then
  LLAMA_ARGS+=(--parallel "$PARALLEL")
fi
if [[ -n "$FLASH_ATTN" ]]; then
  LLAMA_ARGS+=(--flash-attn "$FLASH_ATTN")
fi

# Disable --fit for MoE models — it miscalculates Metal memory for sparse experts
LLAMA_ARGS+=(--fit off)
LLAMA_ARGS+=(--no-warmup)

if [[ "$THREADS" != "-1" ]]; then
  LLAMA_ARGS+=(--threads "$THREADS")
fi
if [[ "$THREADS_BATCH" != "-1" ]]; then
  LLAMA_ARGS+=(--threads-batch "$THREADS_BATCH")
fi
if [[ -n "$API_KEY" ]]; then
  LLAMA_ARGS+=(--api-key "$API_KEY")
fi

# ── Graceful shutdown handler ─────────────────────────────────
SERVER_PID=""

cleanup() {
  echo ""
  echo "[INFO] Shutting down llama-server..."
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill -TERM "$SERVER_PID" 2>/dev/null
    # Wait up to 10 seconds for graceful exit
    for i in $(seq 1 10); do
      kill -0 "$SERVER_PID" 2>/dev/null || break
      sleep 1
    done
    # Force kill if still running
    if kill -0 "$SERVER_PID" 2>/dev/null; then
      echo "[WARN] Force-killing llama-server (PID $SERVER_PID)"
      kill -9 "$SERVER_PID" 2>/dev/null || true
    fi
  fi
  rm -f "$PID_FILE"
  echo "[INFO] Server stopped."
  exit 0
}

trap cleanup SIGINT SIGTERM SIGHUP

# ── Health check function ────────────────────────────────────
wait_for_health() {
  local max_wait=180
  local interval=2
  local elapsed=0
  local url="http://${HOST}:${PORT}/health"

  echo "[INFO] Waiting for server health check at $url ..."
  while (( elapsed < max_wait )); do
    if curl -sf "$url" >/dev/null 2>&1; then
      echo "[INFO] Server is healthy (responded after ${elapsed}s)"
      return 0
    fi
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done
  echo "[WARN] Server did not become healthy within ${max_wait}s"
  return 1
}

# ── Start with auto-restart ──────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Local LLM Inference Server"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Model:    $MODEL"
echo "  Endpoint: http://${HOST}:${PORT}"
echo "  Context:  ${CTX_SIZE:-0} tokens (0=model default)"
echo "  GPU:      ${N_GPU_LAYERS} layers"
echo "  Parallel: ${PARALLEL} slots"
if [[ -n "$API_KEY" ]]; then
  echo "  Auth:     API key enabled"
else
  echo "  Auth:     none (set API_KEY in agent.conf)"
fi
echo "  Log:      $RUN_LOG"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

attempt=0
while (( attempt < MAX_RETRIES )); do
  attempt=$((attempt + 1))

  if (( attempt > 1 )); then
    echo "[INFO] Retry attempt $attempt/$MAX_RETRIES (waiting ${RETRY_DELAY}s)..."
    sleep "$RETRY_DELAY"
  fi

  echo "[INFO] Starting llama-server (attempt $attempt/$MAX_RETRIES)..."
  echo "[INFO] Args: ${LLAMA_ARGS[*]}"
  echo "[INFO] Press Ctrl+C to stop"
  echo ""

  # Launch server in background, log to per-run file
  llama-server "${LLAMA_ARGS[@]}" >> "$RUN_LOG" 2>&1 &
  SERVER_PID=$!
  echo "$SERVER_PID" > "$PID_FILE"

  # Wait for health
  if wait_for_health; then
    echo ""
    echo "[INFO] Server ready — OpenAI-compatible API:"
    echo "       POST http://${HOST}:${PORT}/v1/chat/completions"
    echo "       GET  http://${HOST}:${PORT}/health"
    echo ""

    # Keep running until process exits
    wait "$SERVER_PID" || true
    EXIT_CODE=$?

    # Check if it was our cleanup handler
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
      echo "[WARN] llama-server exited (code $EXIT_CODE)"
    fi
  else
    echo "[ERROR] Server failed to start. Check $RUN_LOG"
    if kill -0 "$SERVER_PID" 2>/dev/null; then
      kill -TERM "$SERVER_PID" 2>/dev/null || true
      sleep 1
      kill -9 "$SERVER_PID" 2>/dev/null || true
    fi
  fi

  rm -f "$PID_FILE"
  SERVER_PID=""
done

echo "[ERROR] All $MAX_RETRIES attempts failed. Giving up."
echo "[ERROR] Check logs: $RUN_LOG"
exit 1
