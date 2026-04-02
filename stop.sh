#!/bin/bash
set -euo pipefail

# Stop the running llama-server instance

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_FILE="$SCRIPT_DIR/agent.conf"
PID_FILE="$SCRIPT_DIR/.llama-server.pid"
VISION_PID_FILE="$SCRIPT_DIR/.vision-server.pid"

# Load config to get PORT for fallback matching
PORT="8080"
if [[ -f "$CONF_FILE" ]]; then
  # shellcheck source=agent.conf
  source "$CONF_FILE"
fi

kill_pid() {
  local pid="$1"
  local label="$2"
  if kill -0 "$pid" 2>/dev/null; then
    echo "[INFO] Stopping $label (PID $pid)..."
    kill -TERM "$pid" 2>/dev/null || true
    for i in $(seq 1 10); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo "[WARN] Force-killing (PID $pid)"
      kill -9 "$pid" 2>/dev/null || true
    fi
    return 0
  fi
  return 1
}

# If the launchd agent is installed, delegate to launchctl.
# Killing llama-server directly while start.sh is launchd-managed would
# just cause start.sh to restart it. launchctl stop is the right call.
SERVER_PLIST="$HOME/Library/LaunchAgents/com.elwin.server.plist"
VISION_PLIST_AGENT="$HOME/Library/LaunchAgents/com.elwin.vision.plist"
if [[ -f "$SERVER_PLIST" ]]; then
  echo "[INFO] Servers are managed by launchd — stopping via launchctl..."
  launchctl stop com.elwin.server
  [[ -f "$VISION_PLIST_AGENT" ]] && launchctl stop com.elwin.vision || true
  echo "[INFO] Servers stopped. launchd will restart them automatically (KeepAlive is on)."
  echo "[INFO] To disable auto-restart: python install.py --uninstall"
  exit 0
fi

stopped=false

# Try PID file first
if [[ -f "$PID_FILE" ]]; then
  PID=$(cat "$PID_FILE")
  if kill_pid "$PID" "llama-server from PID file"; then
    stopped=true
  else
    echo "[INFO] Process $PID is not running (stale PID file)."
  fi
  rm -f "$PID_FILE"
fi

# Fallback: find any llama-server on our port
STALE_PIDS=$(pgrep -f "llama-server.*--port ${PORT}" 2>/dev/null || true)
if [[ -n "$STALE_PIDS" ]]; then
  echo "[INFO] Found llama-server processes on port ${PORT}: $STALE_PIDS"
  for pid in $STALE_PIDS; do
    kill_pid "$pid" "orphaned llama-server" && stopped=true
  done
fi

if $stopped; then
  echo "[INFO] Server stopped."
else
  echo "[INFO] No llama-server processes found."
fi

# Stop vision server
if [[ -f "$VISION_PID_FILE" ]]; then
  VPID=$(cat "$VISION_PID_FILE")
  if kill_pid "$VPID" "vision server"; then
    echo "[INFO] Vision server stopped."
  else
    echo "[INFO] Vision server was not running (stale PID $VPID)."
  fi
  rm -f "$VISION_PID_FILE"
fi
