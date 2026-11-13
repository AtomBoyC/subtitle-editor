#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PORT:-8765}"

if [ ! -x ".venv/bin/python" ]; then
  echo "📦 正在初始化 Python 虚拟环境 (.venv)..."
  python3 -m venv .venv
  ./.venv/bin/pip install -r requirements.txt
fi

echo "🚀 听见 Studio 2.0 正在启动：http://127.0.0.1:${PORT}"
if command -v open >/dev/null 2>&1; then
  (sleep 1.2 && open "http://127.0.0.1:${PORT}") &
fi

exec ./.venv/bin/uvicorn server:app --host 127.0.0.1 --port "${PORT}"
