#!/usr/bin/env bash
# 服务器上起/停/查开发服务。用 pidfile，不要用 pkill -f——
# pkill -f 的 pattern 会匹配到正在执行它的那个 shell 自己的命令行，把自己杀掉。
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-18002}"
PIDFILE="data/uvicorn-${PORT}.pid"
LOGFILE="data/uvicorn-${PORT}.log"
mkdir -p data

alive() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "${1:-start}" in
  start)
    if alive; then echo "已在跑：pid=$(cat "$PIDFILE") port=$PORT"; exit 0; fi
    setsid nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$PORT" \
      > "$LOGFILE" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
    sleep 3
    if alive; then
      echo "已启动：pid=$(cat "$PIDFILE") port=$PORT log=$LOGFILE"
    else
      echo "启动失败，日志尾部："; tail -20 "$LOGFILE"; exit 1
    fi
    ;;
  stop)
    if alive; then kill "$(cat "$PIDFILE")"; sleep 1; echo "已停"; else echo "没在跑"; fi
    rm -f "$PIDFILE"
    ;;
  restart) "$0" stop; "$0" start ;;
  status)
    if alive; then echo "running pid=$(cat "$PIDFILE")"; else echo "stopped"; fi
    ss -tlnp 2>/dev/null | grep ":$PORT" || true
    ;;
  log) tail -n "${2:-30}" "$LOGFILE" ;;
  *) echo "用法: $0 {start|stop|restart|status|log [n]}"; exit 2 ;;
esac
