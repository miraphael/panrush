"""PanRush 桌面版入口（PyInstaller 打包用）。

双击 exe：启动本地服务 + 自动打开浏览器。数据/配置保存在 exe 同目录。
"""

import sys
import threading
import time
import webbrowser


def _open_browser() -> None:
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:18400")


def main() -> None:
    from server.main import app  # noqa: E402 — 触发路径初始化
    import uvicorn

    if "--no-browser" not in sys.argv:
        threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=18400, log_level="warning")


if __name__ == "__main__":
    main()
