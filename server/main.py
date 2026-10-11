"""PanRush 服务入口：FastAPI + REST API + 静态 Web UI。

启动: python -m uvicorn server.main:app --host 127.0.0.1 --port 18400
"""

import asyncio
import hashlib
import logging
import os
import secrets
import sys
import time
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .config import Config
from .cred_store import CredStore
from .dav import DavApp
from .engine.downloader import Engine
from .engine.task_store import TaskStore
from .providers import PanError, _create_all
from .providers.base import FileEntry

# 冻结模式(PyInstaller)：数据/配置放 exe 旁边，web 资源在包内 _MEIPASS
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    WEB_DIR = os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "web")
else:
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    WEB_DIR = os.path.join(APP_DIR, "web")
DATA_DIR = os.path.join(APP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# 日志：滚动文件 + 控制台
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        RotatingFileHandler(os.path.join(DATA_DIR, "panrush.log"),
                            maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"),
        logging.StreamHandler(),
    ],
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

config = Config(os.path.join(APP_DIR, "config.json"))
cred_store = CredStore(os.path.join(DATA_DIR, "panrush.db"))
providers = _create_all(cred_store)
task_store = TaskStore(os.path.join(DATA_DIR, "panrush.db"))
engine = Engine(task_store, providers, config)


@asynccontextmanager
async def lifespan(_: FastAPI):
    engine.start()
    yield
    await engine.close()


app = FastAPI(title="PanRush", lifespan=lifespan)


# ---------- providers ----------

@app.get("/api/providers")
async def list_providers():
    return [{
        "id": p.id, "name": p.name, "tier": p.tier, "root": p.root,
        "cred_saved": p.cred_saved(), "cred_fields": p.cred_fields, "help": p.help,
        "oauth": getattr(p, "oauth", None),
    } for p in providers.values()]


class CredBody(BaseModel):
    values: dict


@app.post("/api/providers/{pid}/cred")
async def save_cred(pid: str, body: CredBody):
    p = providers.get(pid)
    if not p:
        raise HTTPException(404, "provider 不存在")
    missing = [f["key"] for f in p.cred_fields
               if not body.values.get(f["key"]) and not f.get("optional")]
    if missing:
        raise HTTPException(400, f"缺少字段: {', '.join(missing)}")
    try:
        await p.login(body.values)
    except PanError as e:
        raise HTTPException(400, str(e))
    cred_store.set(pid, body.values)
    return {"ok": True}


@app.delete("/api/providers/{pid}/cred")
async def del_cred(pid: str):
    cred_store.delete(pid)
    return {"ok": True}


# ---------- 文件浏览 ----------

@app.get("/api/fs")
async def browse(provider: str, path: str = ""):
    p = providers.get(provider)
    if not p:
        raise HTTPException(404, "provider 不存在")
    if not p.cred_saved():
        raise HTTPException(400, f"{p.name} 未配置凭据")
    try:
        entries = await p.list_dir(path or p.root)
    except PanError as e:
        raise HTTPException(400, str(e))
    return {"path": path or p.root, "entries": [
        {"fsid": e.fsid, "name": e.name, "size": e.size, "is_dir": e.is_dir} for e in entries
    ]}


# ---------- 任务 ----------

class TaskBody(BaseModel):
    provider: str
    fsid: str
    name: str
    size: int = -1
    dest_dir: str = ""


class UrlBody(BaseModel):
    url: str
    dest_dir: str = ""
    filename: str = ""


@app.post("/api/tasks")
async def create_task(body: TaskBody, request: Request):
    if body.provider == "direct":
        raise HTTPException(400, "direct 驱动请用 /api/download/url")
    if body.provider not in providers:
        raise HTTPException(404, "provider 不存在")
    t = engine.create(body.provider, body.fsid, body.name or "download.bin", body.size,
                      body.dest_dir or "")
    return t.to_api()


@app.post("/api/download/url")
async def download_url(body: UrlBody, request: Request):
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(400, "仅支持 http(s) 直链")
    name = body.filename or os.path.basename(body.url.split("?")[0]) or "download.bin"
    t = engine.create("direct", body.url, name, -1, body.dest_dir or "")
    return t.to_api()


@app.get("/api/tasks")
async def list_tasks():
    return engine.list_api()


@app.post("/api/tasks/{task_id}/{action}")
async def task_action(task_id: str, action: str):
    if task_id not in engine.tasks:
        raise HTTPException(404, "任务不存在")
    if action == "pause":
        engine.pause(task_id)
    elif action == "resume":
        engine.resume(task_id)
    elif action == "retry":
        engine.retry(task_id)
    elif action == "delete":
        engine.delete(task_id)
    else:
        raise HTTPException(400, "未知操作")
    return {"ok": True}


@app.post("/api/config")
async def save_config(request: Request):
    body = await request.json()
    for k in ("dest_dir", "conns_per_task", "max_concurrent_tasks",
              "global_speed_limit_kbps", "auto_shutdown"):
        if k in body:
            config.data[k] = body[k]
    config.save()
    if "global_speed_limit_kbps" in body:
        engine.limiter.set_rate(int(body["global_speed_limit_kbps"]))
    if "max_concurrent_tasks" in body:
        engine.slots.set_limit(int(body["max_concurrent_tasks"]))
    if body.get("auto_shutdown") == "none":
        engine._shutdown_at = None
    return config.data


@app.get("/api/config")
async def get_config():
    return config.data


# ---------- 扩展操作 ----------

@app.get("/api/version")
async def version():
    return {"name": "PanRush", "version": __version__}


class OpenDirBody(BaseModel):
    path: str = ""


@app.get("/api/status")
async def status():
    """聚合状态：自动关机布防、任务统计。"""
    tasks = engine.tasks.values()
    return {
        **engine.shutdown_status(),
        "active": sum(1 for t in tasks if t.status in ("pending", "resolving", "downloading")),
        "done": sum(1 for t in tasks if t.status == "done"),
        "version": __version__,
    }


class AutostartBody(BaseModel):
    enable: bool


AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


@app.post("/api/autostart")
async def autostart(body: AutostartBody):
    """开机自启（Windows 注册表 HKCU Run；仅冻结版 exe 支持写入自身路径）。"""
    if sys.platform != "win32":
        raise HTTPException(400, "仅支持 Windows")
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if body.enable:
                if getattr(sys, "frozen", False):
                    target = f'"{sys.executable}"'
                else:
                    py = sys.executable.replace("python.exe", "pythonw.exe")
                    target = f'"{py}" "{os.path.join(APP_DIR, "run_exe.py")}"'
                winreg.SetValueEx(k, "PanRush", 0, winreg.REG_SZ, target)
            else:
                try:
                    winreg.DeleteValue(k, "PanRush")
                except FileNotFoundError:
                    pass
    except OSError as e:
        raise HTTPException(500, f"注册表写入失败: {e}")
    return {"ok": True, "enabled": body.enable}


@app.get("/api/autostart")
async def autostart_state():
    if sys.platform != "win32":
        return {"enabled": False}
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as k:
            winreg.QueryValueEx(k, "PanRush")
            return {"enabled": True}
    except OSError:
        return {"enabled": False}


@app.post("/api/open-dir")
async def open_dir(body: OpenDirBody):
    """在资源管理器中打开下载目录（Windows；其他平台用 xdg-open/open）。"""
    target = body.path or config.dest_dir
    if not os.path.isdir(target):
        raise HTTPException(400, f"目录不存在: {target}")
    try:
        if sys.platform == "win32":
            os.startfile(target)  # noqa: S606
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", target])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", target])
    except OSError as e:
        raise HTTPException(500, f"打开失败: {e}")
    return {"ok": True}


@app.post("/api/tasks-all/pause")
async def pause_all():
    for t in engine.tasks.values():
        if t.status in ("downloading", "resolving", "pending"):
            engine.pause(t.id)
    return {"ok": True}


@app.post("/api/tasks-all/resume")
async def resume_all():
    for tid, t in list(engine.tasks.items()):
        if t.status in ("paused", "error"):
            engine.resume(tid)
    return {"ok": True}


@app.post("/api/tasks-all/clear-done")
async def clear_done():
    """清理已完成任务（保留已下载的文件）。"""
    n = 0
    for tid, t in list(engine.tasks.items()):
        if t.status == "done":
            engine.delete(tid)
            n += 1
    return {"ok": True, "cleared": n}


# ---------- 跨盘搜索 ----------

SEARCH_MAX_FILES = 400      # 每盘最多扫描条目数（防风控）
SEARCH_MAX_HITS = 50        # 每盘最多命中数
SEARCH_MAX_DEPTH = 3        # 目录深度


async def _scan_provider(p, q: str) -> list:
    out, seen = [], 0
    queue = [(p.root, "", 0)]  # (fsid, 父路径显示, 深度)
    while queue and seen < SEARCH_MAX_FILES and len(out) < SEARCH_MAX_HITS:
        path, parent, depth = queue.pop(0)
        try:
            entries = await p.list_dir(path)
        except Exception:
            break
        for e in entries:
            seen += 1
            full = f"{parent}/{e.name}" if parent else f"/{e.name}"
            if e.is_dir:
                if depth < SEARCH_MAX_DEPTH:
                    queue.append((e.fsid, full, depth + 1))
            elif q in e.name.lower():
                out.append({"provider": p.id, "provider_name": p.name,
                            "fsid": e.fsid, "name": e.name, "size": e.size,
                            "path": parent or "/"})
    return out


@app.get("/api/search")
async def search(q: str):
    """跨盘搜索：在所有已配置网盘里按文件名子串查找（限速扫描，防风控）。
    同名同大小的结果自动做 256KB 头部哈希比对，命中则标 verified=True。"""
    q = q.strip().lower()
    if not q:
        return {"results": []}
    scans = [_scan_provider(p, q) for p in providers.values()
             if p.id != "direct" and p.cred_saved()]
    groups = await asyncio.gather(*scans, return_exceptions=True)
    results = [r for g in groups if isinstance(g, list) for r in g]
    results.sort(key=lambda r: (r["provider"], r["path"]))

    # 哈希级验证：同名字节 + 同大小 → 比对文件头部 256KB
    by_key = defaultdict(list)
    for r in results:
        if r["size"] > 0:
            by_key[(r["name"], r["size"])].append(r)
    for items in by_key.values():
        if len(items) < 2 or len(items) > 4:
            continue
        hashes = await asyncio.gather(
            *[_head_hash(providers[i["provider"]], i["fsid"], i["name"], i["size"])
              for i in items],
            return_exceptions=True,
        )
        ok = Counter(h for h in hashes if isinstance(h, str))
        for r, h in zip(items, hashes):
            if isinstance(h, str) and ok[h] >= 2:
                r["verified"] = True
    return {"results": results}


async def _head_hash(p, fsid: str, name: str, size: int) -> str:
    """取文件头部 256KB 算 SHA256（验证跨盘副本是否同内容）。"""
    link = await p.resolve(FileEntry(fsid=fsid, name=name, size=size, is_dir=False))
    h = hashlib.sha256()
    n = 0
    async with httpx.AsyncClient(
            follow_redirects=True, timeout=httpx.Timeout(15, read=30)) as cli:
        async with cli.stream("GET", link.url, headers=link.headers) as r:
            r.raise_for_status()
            async for chunk in r.aiter_bytes(65536):
                h.update(chunk)
                n += len(chunk)
                if n >= 256 * 1024:
                    break
    return h.hexdigest()


# ---------- 检查更新 ----------

@app.get("/api/update")
async def check_update():
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.get("https://api.github.com/repos/miraphael/panrush/releases/latest",
                              headers={"Accept": "application/vnd.github+json"})
        if r.status_code == 404:
            return {"current": __version__, "latest": None, "update_available": False,
                    "url": "https://github.com/miraphael/panrush/releases"}
        tag = r.json().get("tag_name", "").lstrip("v")
        return {"current": __version__, "latest": tag,
                "update_available": tag > __version__,
                "url": r.json().get("html_url") or "https://github.com/miraphael/panrush/releases"}
    except Exception as e:
        raise HTTPException(502, f"检查更新失败: {e}")


# ---------- OAuth 一键授权向导 ----------

OAUTH_CONFIG = {
    "onedrive": {
        "auth": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scope": "offline_access Files.Read.All",
        "extra": {},
        "secret_required": False,
    },
    "gdrive": {
        "auth": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "https://www.googleapis.com/auth/drive.readonly",
        "extra": {"access_type": "offline", "prompt": "consent"},
        "secret_required": True,
    },
    "dropbox": {
        "auth": "https://www.dropbox.com/oauth2/authorize",
        "token": "https://api.dropboxapi.com/oauth2/token",
        "scope": "",
        "extra": {"token_access_type": "offline"},
        "secret_required": True,
    },
}
_oauth_pending: dict = {}  # state -> {pid, client_id, client_secret}


class OAuthStartBody(BaseModel):
    client_id: str
    client_secret: str = ""


@app.post("/api/oauth/{pid}/start")
async def oauth_start(pid: str, body: OAuthStartBody):
    cfg = OAUTH_CONFIG.get(pid)
    if not cfg:
        raise HTTPException(404, "该驱动不支持 OAuth 向导")
    if cfg["secret_required"] and not body.client_secret:
        raise HTTPException(400, "该平台需要 client_secret")
    state = secrets.token_urlsafe(16)
    _oauth_pending[state] = {
        "pid": pid, "client_id": body.client_id,
        "client_secret": body.client_secret, "ts": time.time(),
    }
    redirect_uri = f"http://127.0.0.1:{config.port}/oauth/callback"
    params = {"client_id": body.client_id, "redirect_uri": redirect_uri,
              "response_type": "code", "state": state, **cfg["extra"]}
    if cfg["scope"]:
        params["scope"] = cfg["scope"]
    return {"url": f"{cfg['auth']}?{urlencode(params)}"}


@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    params = dict(request.query_params)
    pend = _oauth_pending.pop(params.get("state", ""), None)
    if not pend or params.get("error"):
        return HTMLResponse("<h2>✗ 授权失败或已超时，请回 PanRush 重试</h2>", status_code=400)
    cfg = OAUTH_CONFIG[pend["pid"]]
    redirect_uri = f"http://127.0.0.1:{config.port}/oauth/callback"
    data = {"grant_type": "authorization_code", "code": params.get("code", ""),
            "redirect_uri": redirect_uri,
            "client_id": pend["client_id"], "client_secret": pend["client_secret"]}
    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.post(cfg["token"], data=data)
    tok = r.json()
    if "refresh_token" not in tok:
        return HTMLResponse(f"<h2>✗ 换取 token 失败</h2><pre>{tok}</pre>", status_code=400)
    cred = {"client_id": pend["client_id"], "client_secret": pend["client_secret"],
            "refresh_token": tok["refresh_token"]}
    p = providers[pend["pid"]]
    await p.login(cred)
    cred_store.set(pid := pend["pid"], cred)
    return HTMLResponse(
        f"<body style='font-family:sans-serif;text-align:center;padding-top:15vh'>"
        f"<h2>✓ {providers[pid].name} 授权成功</h2>"
        f"<p>refresh_token 已加密保存，回到 PanRush 刷新页面即可浏览。</p>"
        f"<p style='color:#888'>此窗口可以关闭</p></body>")


# ---------- 对外 WebDAV 出口（在兜底静态挂载之前） ----------
app.mount("/dav", DavApp(providers), name="dav")

# 静态 Web UI（必须放最后：兜底路由，先于它的 API 路由优先匹配）
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
