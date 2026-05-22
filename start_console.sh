#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST="${HOST:-127.0.0.1}"
PORT="${1:-${PORT:-8765}}"

echo "项目目录: ${ROOT_DIR}"
echo "准备启动控制台: http://${HOST}:${PORT}"

PIDS="$(lsof -ti "tcp:${PORT}" -sTCP:LISTEN || true)"

if [[ -n "${PIDS}" ]]; then
  echo "发现端口 ${PORT} 被占用，准备关闭进程: ${PIDS}"
  kill ${PIDS} || true

  for _ in {1..10}; do
    sleep 0.5
    if ! lsof -ti "tcp:${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
      break
    fi
  done

  REMAINING_PIDS="$(lsof -ti "tcp:${PORT}" -sTCP:LISTEN || true)"
  if [[ -n "${REMAINING_PIDS}" ]]; then
    echo "端口 ${PORT} 仍被占用，强制关闭进程: ${REMAINING_PIDS}"
    kill -9 ${REMAINING_PIDS} || true
  fi
fi

echo "启动服务中..."
cd "${ROOT_DIR}"
exec python3 "${ROOT_DIR}/recruitment_console.py" --host "${HOST}" --port "${PORT}"
