"""PanRush 打包脚本。

    python build_exe.py            # 完整版：内嵌无头浏览器，完全独立，双击即用
    python build_exe.py --lite     # 轻量版：不内嵌浏览器，运行时用系统 Edge / Chrome

产物：
    dist/PanRush.exe            （完整版或轻量版）
    ../PanRush-v<版本>[-lite].exe

完整版会把 Playwright 的 chrome-headless-shell 打进 exe（约 200MB 原始体积），
程序首次启动时释放到 exe 旁的 browsers/ 目录并复用，之后启动不再解压。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(ROOT, "assets")
BUILD = os.path.join(ROOT, "build")
PAYLOAD = os.path.join(BUILD, "browser-payload")
ICO = os.path.join(ASSETS, "panrush.ico")
BROWSER_NAME = "chromium_headless_shell-1243"
KEEP_LOCALES = {"en-US.pak", "zh-CN.pak"}


def version() -> str:
    src = open(os.path.join(ROOT, "server", "__init__.py"), encoding="utf-8").read()
    return re.search(r'__version__\s*=\s*"([^"]+)"', src).group(1)


# ---------- 图标 ----------

def make_icon() -> None:
    if os.path.exists(ICO):
        return
    from PIL import Image, ImageDraw, ImageFont

    os.makedirs(ASSETS, exist_ok=True)
    base = 256
    img = Image.new("RGBA", (base, base), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6, 6, base - 6, base - 6], radius=54, fill=(79, 140, 255, 255))
    d.rounded_rectangle([6, 6, base - 6, base - 6], radius=54, outline=(46, 96, 200, 255), width=6)
    try:
        font = ImageFont.truetype("arialbd.ttf", 150)
    except OSError:
        font = ImageFont.load_default()
    d.text((base / 2, base / 2 - 6), "P", font=font, fill="white", anchor="mm")
    # 右下角一个小箭头，暗示"下载"
    d.polygon([(base - 96, base - 96), (base - 40, base - 96), (base - 68, base - 40)],
              fill=(255, 255, 255, 235))
    img.save(ICO, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(f"[icon] 生成 {ICO}")


# ---------- 内嵌浏览器 ----------

def playwright_cache() -> str:
    """找到 Playwright 的浏览器缓存目录。"""
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0" and os.path.isdir(env):
        return env
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, "ms-playwright")


def stage_browser() -> bool:
    """把 chrome-headless-shell 复制到待打包目录。

    只挑需要的文件复制（顺手跳过用不到的语言包），不做「先全量拷贝再删除」——
    少 40MB 体积，也不会有误删风险。
    """
    src = os.path.join(playwright_cache(), BROWSER_NAME, "chrome-headless-shell-win64")
    if not os.path.isdir(src):
        print(f"[browser] 没找到 {src}")
        print("         先跑一次：python -m playwright install chromium-headless-shell")
        return False
    dst = os.path.join(PAYLOAD, BROWSER_NAME, "chrome-headless-shell-win64")
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        s = os.path.join(src, name)
        if name == "debug.log":
            continue
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(dst, name))
        elif name == "locales":
            os.makedirs(os.path.join(dst, "locales"), exist_ok=True)
            for pak in os.listdir(s):
                if pak in KEEP_LOCALES:
                    shutil.copy2(os.path.join(s, pak), os.path.join(dst, "locales", pak))
        else:
            shutil.copytree(s, os.path.join(dst, name), dirs_exist_ok=True)
    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(dst) for f in fs)
    print(f"[browser] 已备好 {BROWSER_NAME}（{size / 1048576:.0f}MB）")
    return True


# ---------- Windows 版本信息 ----------

VERSION_TMPL = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({v0}, {v1}, {v2}, 0), prodvers=({v0}, {v1}, {v2}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('080404b0', [
        StringStruct('CompanyName', 'PanRush'),
        StringStruct('FileDescription', 'PanRush — 多网盘聚合满速下载器'),
        StringStruct('FileVersion', '{ver}'),
        StringStruct('InternalName', 'PanRush'),
        StringStruct('LegalCopyright', 'MIT License'),
        StringStruct('OriginalFilename', 'PanRush.exe'),
        StringStruct('ProductName', 'PanRush'),
        StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def write_version_file(ver: str) -> str:
    parts = (ver.split(".") + ["0", "0", "0"])[:3]
    path = os.path.join(BUILD, "version_info.txt")
    os.makedirs(BUILD, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(VERSION_TMPL.format(ver=ver, v0=parts[0], v1=parts[1], v2=parts[2]))
    return path


# ---------- 打包 ----------

def build(lite: bool) -> str:
    ver = version()
    make_icon()
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile",
            "--noconsole", "--name", "PanRush",
            "--add-data", "web;web",
            "--icon", ICO,
            "--version-file", write_version_file(ver)]
    if not lite:
        if not stage_browser():
            return ""
        args += ["--add-data", f"{PAYLOAD};ms-playwright"]
    args.append("run_exe.py")
    print(f"[build] {'轻量版' if lite else '完整版'} v{ver}")
    subprocess.run(args, cwd=ROOT, check=True)

    out = os.path.join(ROOT, "dist", "PanRush.exe")
    suffix = "-lite" if lite else ""
    target = os.path.join(os.path.dirname(ROOT), f"PanRush-v{ver}{suffix}.exe")
    shutil.copy2(out, target)
    print(f"[done] {target}  ({os.path.getsize(target) / 1048576:.0f}MB)")
    return target


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lite", action="store_true", help="不内嵌浏览器，用系统 Edge/Chrome")
    build(ap.parse_args().lite)
