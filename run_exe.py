"""PanRush 桌面版入口（PyInstaller 打包用）。

双击 exe：启动本地服务 + 托盘常驻 + 自动打开浏览器。
数据/配置保存在 exe 同目录（只读目录时自动退到 %LOCALAPPDATA%\\PanRush）。
参数：`--no-browser` 跳过浏览器；`--no-tray` 跳过托盘；`--no-provision` 跳过浏览器释放。
"""

import os
import shutil
import socket
import sys
import threading
import time
import webbrowser

HOST, PORT = "127.0.0.1", 18400
URL = f"http://{HOST}:{PORT}"


def _provision_browsers() -> None:
    """把打包进来的无头浏览器释放到磁盘（只做一次）并让 Playwright 用它。

    onefile 模式每次启动都会把包解压到临时目录，200MB 的浏览器要是每次重解压，
    启动会慢好几秒。所以这里改成「首次运行释放到 exe 旁（或 %LOCALAPPDATA%），
    之后直接复用」。轻量构建里没有内嵌浏览器，就交给系统 Edge / Chrome。
    """
    from server.paths import browsers_dir, resource_dir

    src = os.path.join(resource_dir(), "ms-playwright")
    if not os.path.isdir(src):
        return                      # 轻量构建：没内嵌浏览器
    dst = browsers_dir()
    marker = os.path.join(dst, ".ready")
    if not os.path.exists(marker):
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True)
            with open(marker, "w", encoding="utf-8") as f:
                f.write("ok")
        except OSError as e:
            print(f"[browser] 释放失败，改用系统浏览器: {e}", file=sys.stderr)
            return
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = dst


def _port_busy() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((HOST, PORT)) == 0


def _open_browser() -> None:
    time.sleep(1.5)
    webbrowser.open(URL)


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
            pystray.MenuItem("打开界面", lambda: webbrowser.open(URL), default=True),
            pystray.MenuItem("退出", _quit),
        )
        icon = pystray.Icon("PanRush", _make_tray_image(), "PanRush — 网盘聚合下载器", menu)
        icon.run()
    except Exception as e:  # 托盘失败不影响服务
        print(f"[tray] {e}", file=sys.stderr)


def _quit(icon=None, item=None):
    os._exit(0)


def main() -> None:
    # 已经有一个实例在跑：直接打开界面，不再抢端口
    if _port_busy():
        webbrowser.open(URL)
        return

    if "--no-provision" not in sys.argv:
        _provision_browsers()

    from server.main import app  # noqa: E402 — 触发路径初始化
    import uvicorn

    if "--no-browser" not in sys.argv:
        threading.Thread(target=_open_browser, daemon=True).start()
    if "--no-tray" not in sys.argv:
        threading.Thread(target=_start_tray, daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
