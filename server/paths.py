"""运行路径解析。

目标：让打包后的 exe 无论放在哪里都能跑——包括放在 `C:\\Program Files` 这类
只写不了的位置（数据自动退到 %LOCALAPPDATA%\\PanRush），以及把内嵌的
无头浏览器释放到磁盘后复用（避免 onefile 每次启动都解压 200MB）。
"""

import os
import sys

APP_NAME = "PanRush"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 冻结环境里。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """程序所在目录：冻结时是 exe 所在目录，源码运行时是项目根目录。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir() -> str:
    """打包资源目录：冻结时是 _MEIPASS 解压目录，源码运行时即项目根。"""
    return getattr(sys, "_MEIPASS", app_dir())


def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write-probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(probe)
        return True
    except OSError:
        return False


def data_dir() -> str:
    """数据/配置目录：优先 exe 同目录（绿色版习惯），不可写时退到 %LOCALAPPDATA%。"""
    beside = os.path.join(app_dir(), "data")
    if _writable(beside):
        return beside
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    fallback = os.path.join(local, APP_NAME, "data")
    os.makedirs(fallback, exist_ok=True)
    return fallback


def browsers_dir() -> str:
    """内嵌无头浏览器的释放目录（与 data 同策略，保证可写）。"""
    beside = os.path.join(app_dir(), "browsers")
    if _writable(beside):
        return beside
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    fallback = os.path.join(local, APP_NAME, "browsers")
    os.makedirs(fallback, exist_ok=True)
    return fallback


def config_path() -> str:
    """配置文件路径：绿色版放 exe 旁边，只读目录时跟着 data 走。"""
    beside = os.path.join(app_dir(), "config.json")
    if _writable(app_dir()):
        return beside
    return os.path.join(data_dir(), "config.json")
