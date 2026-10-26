#!/usr/bin/env bash
# Usage: ./run.sh [start|stop|restart|status|logs]
cd "$(dirname "$0")" || exit 1
PID_FILE=bot.pid
LOG_FILE=bot.log

is_running() { [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; }

start() {
  if is_running; then echo "already running (pid $(cat $PID_FILE))"; return 0; fi
  if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
  fi
  nohup setsid .venv/bin/python -u bot.py >> "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  echo "started (pid $(cat $PID_FILE)), logging to $LOG_FILE"
}

stop() {
  if is_running; then
    pid=$(cat "$PID_FILE"); kill "$pid"
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
    echo "stopped (pid $pid)"
  else
    echo "not running"
  fi
  rm -f "$PID_FILE"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) if is_running; then echo "running (pid $(cat $PID_FILE))"; else echo "not running"; fi ;;
  logs) tail -n 50 -f "$LOG_FILE" ;;
  *) echo "usage: $0 [start|stop|restart|status|logs]"; exit 1 ;;
esac
