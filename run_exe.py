"""PanRush 桌面版入口（PyInstaller 打包用）。

双击 exe：启动本地服务 + 托盘常驻 + 自动打开浏览器。
数据/配置保存在 exe 同目录。`--no-browser` 跳过浏览器；`--no-tray` 跳过托盘。
"""

import sys
import threading
import time
import webbrowser


def _open_browser() -> None:
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:18400")


def _make_tray_image():
    """程序化生成托盘图标：圆角方块 + P 字。"""
    from PIL import Image, ImageDraw, ImageFont

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 2, size - 2], radius=14, fill=(79, 140, 255, 255))
    try:
        font = ImageFont.truetype("arialbd.ttf", 38)
    except OSError:
        font = ImageFont.load_default()
    d.text((size / 2, size / 2), "P", font=font, fill="white", anchor="mm")
    return img


def _start_tray() -> None:
    try:
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("打开界面", lambda: webbrowser.open("http://127.0.0.1:18400"), default=True),
            pystray.MenuItem("退出", _quit),
        )
        icon = pystray.Icon("PanRush", _make_tray_image(), "PanRush — 网盘聚合下载器", menu)
        icon.run()
    except Exception as e:  # 托盘失败不影响服务
        print(f"[tray] {e}", file=sys.stderr)


def _quit(icon=None, item=None):
    import os
    os._exit(0)


def main() -> None:
    from server.main import app  # noqa: E402 — 触发路径初始化
    import uvicorn

    if "--no-browser" not in sys.argv:
        threading.Thread(target=_open_browser, daemon=True).start()
    if "--no-tray" not in sys.argv:
        threading.Thread(target=_start_tray, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=18400, log_level="warning")


if __name__ == "__main__":
    main()
