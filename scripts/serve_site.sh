#!/usr/bin/env bash
set -euo pipefail

# Serve the static website under site/ over HTTP, detached from the terminal.
#
# Port 9001 is this project's assigned port (see ~/claude/ports.md,
# llm-regression-observability => 9001). Override with PORT=xxxx if needed.
PORT="${PORT:-9001}"

cd "$(dirname "${BASH_SOURCE[0]}")/.."

LOG_DIR="logs"
LOG_FILE="${LOG_DIR}/serve_site.log"
PID_FILE="${LOG_DIR}/serve_site.pid"
mkdir -p "$LOG_DIR"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Already running (pid $(cat "$PID_FILE")) at http://localhost:${PORT}/index.html"
  exit 0
fi

nohup python3 -m http.server "$PORT" --directory site >"$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
disown

echo "Serving site/ at http://localhost:${PORT}/index.html"
echo "  pid: $(cat "$PID_FILE")  log: ${LOG_FILE}"
echo "  stop with: kill \$(cat ${PID_FILE})"
