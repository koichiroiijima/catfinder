#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Keep the SQLite database, WAL sidecars, logs and PID files owner-only.
umask 077

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
PID_FILE="${PID_FILE:-$ROOT_DIR/.server.pid}"
LOG_FILE="${LOG_FILE:-$ROOT_DIR/.server.log}"

is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

start() {
  if is_running; then
    echo "API server is already running (PID $(cat "$PID_FILE"))."
    return 0
  fi

  rm -f "$PID_FILE"
  nohup uv run uvicorn app.main:app --host "$HOST" --port "$PORT" \
    >"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"

  sleep 1
  if is_running; then
    echo "API server started at http://$HOST:$PORT (PID $(cat "$PID_FILE"))."
    echo "Log: $LOG_FILE"
  else
    echo "API server failed to start. See $LOG_FILE." >&2
    rm -f "$PID_FILE"
    return 1
  fi
}

stop() {
  if ! is_running; then
    rm -f "$PID_FILE"
    echo "API server is not running."
    return 0
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid" 2>/dev/null || true
  for _ in {1..50}; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  echo "API server stopped."
}

status() {
  if is_running; then
    echo "API server is running at http://$HOST:$PORT (PID $(cat "$PID_FILE"))."
  else
    echo "API server is not running."
    return 3
  fi
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart)
    stop
    start
    ;;
  status) status ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}" >&2
    exit 2
    ;;
esac
