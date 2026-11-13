@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PORT=8765

if not exist ".venv\Scripts\python.exe" (
    echo 正在创建虚拟环境并安装依赖...
    python -m venv .venv
    .venv\Scripts\pip install -r requirements.txt
)

start "" "http://127.0.0.1:%PORT%"
.venv\Scripts\uvicorn.exe server:app --host 127.0.0.1 --port %PORT%
