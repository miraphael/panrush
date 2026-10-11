#!/usr/bin/env bash
# PanRush 启动脚本（Linux/macOS）
cd "$(dirname "$0")"
(sleep 2; xdg-open http://127.0.0.1:18400 2>/dev/null || open http://127.0.0.1:18400) &
exec python3 -m uvicorn server.main:app --host 127.0.0.1 --port 18400
