"""PanRush 服务入口：FastAPI + REST API + 静态 Web UI。

启动: python -m uvicorn server.main:app --host 127.0.0.1 --port 18400
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Config
from .cred_store import CredStore
from .engine.downloader import Engine
from .engine.task_store import TaskStore
from .providers import PanError, _create_all

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # panhub/
DATA_DIR = os.path.join(ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

config = Config(os.path.join(ROOT, "server", "config.json"))
cred_store = CredStore(os.path.join(DATA_DIR, "panhub.db"))
providers = _create_all(cred_store)
task_store = TaskStore(os.path.join(DATA_DIR, "panhub.db"))
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
    } for p in providers.values()]


class CredBody(BaseModel):
    values: dict


@app.post("/api/providers/{pid}/cred")
async def save_cred(pid: str, body: CredBody):
    p = providers.get(pid)
    if not p:
        raise HTTPException(404, "provider 不存在")
    missing = [f["key"] for f in p.cred_fields if not body.values.get(f["key"])]
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
    for k in ("dest_dir", "conns_per_task", "global_speed_limit_kbps"):
        if k in body:
            config.data[k] = body[k]
    config.save()
    return config.data


@app.get("/api/config")
async def get_config():
    return config.data


# 静态 Web UI（放最后，兜底路由）
app.mount("/", StaticFiles(directory=os.path.join(ROOT, "web"), html=True), name="web")
