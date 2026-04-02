#!/bin/bash
set -euo pipefail

# Health check / status script for the local LLM server

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_FILE="$SCRIPT_DIR/agent.conf"
PID_FILE="$SCRIPT_DIR/.llama-server.pid"

# Load config for host/port
if [[ -f "$CONF_FILE" ]]; then
  source "$CONF_FILE"
else
  HOST="127.0.0.1"
  PORT="8080"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Server Health Check"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check PID
if [[ -f "$PID_FILE" ]]; then
  PID=$(cat "$PID_FILE")
  if kill -0 "$PID" 2>/dev/null; then
    echo "  Process:  running (PID $PID)"
    # Show memory usage
    RSS=$(ps -o rss= -p "$PID" 2>/dev/null || echo "0")
    RSS_MB=$((RSS / 1024))
    echo "  Memory:   ${RSS_MB} MB resident"
  else
    echo "  Process:  NOT running (stale PID $PID)"
  fi
else
  echo "  Process:  unknown (no PID file)"
fi

# HTTP health check
URL="http://${HOST}:${PORT}/health"
echo ""
echo "  Endpoint: $URL"

RESPONSE=$(curl -sf -w "\n%{http_code}" "$URL" 2>/dev/null || echo -e "\n000")
HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -n -1)

if [[ "$HTTP_CODE" == "200" ]]; then
  echo "  Status:   healthy (HTTP $HTTP_CODE)"
  if [[ -n "$BODY" ]]; then
    echo "  Response: $BODY"
  fi
else
  echo "  Status:   UNHEALTHY (HTTP $HTTP_CODE)"
fi

# Show slots info if available
echo ""
SLOTS=$(curl -sf "http://${HOST}:${PORT}/slots" 2>/dev/null || true)
if [[ -n "$SLOTS" ]]; then
  echo "  Slots:"
  echo "$SLOTS" | python3 -c "
import sys, json
try:
    slots = json.load(sys.stdin)
    for s in slots:
        state = s.get('state', 'unknown')
        sid = s.get('id', '?')
        prompt_n = s.get('n_past', 0)
        print(f'    [{sid}] {state} (tokens processed: {prompt_n})')
except: print('    (unable to parse)')
" 2>/dev/null || echo "    (raw: ${SLOTS:0:200})"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
