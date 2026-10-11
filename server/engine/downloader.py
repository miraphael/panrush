"""分段下载引擎。

设计要点：
- 网盘直链往往带 Cookie/Referer 且有效期短（夸克几十秒），所以引擎在下载前一刻
  通过 provider.resolve() 取链；连接被拒(401/403/410)时自动重取，分段进度不丢。
- 大文件按 conns_per_task 分段并发，按偏移写同一文件（每段独立句柄）。
- 分段进度每秒落盘，重启/暂停后续传。
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from urllib.parse import unquote, urlparse

import httpx

from ..providers.base import FileEntry
from .task_store import Task, TaskStore

log = logging.getLogger("panrush.engine")

LINK_RETRIES = 6          # 直链失效后重取次数上限
SEGMENT_RETRIES = 5       # 单段网络错误重试次数
RETRY_BACKOFF = 1.5
SHUTDOWN_COUNTDOWN = 60   # 自动关机倒计时（秒），期间新增任务可撤销


class Paused(Exception):
    pass


class RateLimiter:
    """全局令牌桶限速（KB/s，0 = 不限）。所有任务共享。"""

    def __init__(self, kbps: int = 0):
        self.kbps = kbps
        self._tokens = float(kbps)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    def set_rate(self, kbps: int):
        self.kbps = kbps

    async def consume(self, n: int):
        if self.kbps <= 0:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self._tokens + (now - self._last) / 1000 * self.kbps, float(self.kbps)
                )
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                deficit = (n - self._tokens) / self.kbps
                await asyncio.sleep(min(deficit, 0.25))


class SlotPool:
    """可动态调整大小的并发槽位（限制同时下载的任务数）。"""

    def __init__(self, n: int):
        self.n = max(1, n)
        self.used = 0
        self._cond = asyncio.Condition()

    def set_limit(self, n: int):
        self.n = max(1, n)

    async def acquire(self):
        async with self._cond:
            while self.used >= self.n:
                await self._cond.wait()
            self.used += 1

    async def release(self):
        async with self._cond:
            self.used = max(0, self.used - 1)
            self._cond.notify_all()


def _sanitize(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "download.bin"


def _unique_path(dest: str) -> str:
    """目标文件已存在时自动改名: file (1).ext / file (2).ext …"""
    if not os.path.exists(dest):
        return dest
    base, ext = os.path.splitext(dest)
    for i in range(1, 1000):
        cand = f"{base} ({i}){ext}"
        if not os.path.exists(cand):
            return cand
    return dest


def _filename_from_headers(headers: dict, url: str) -> str:
    cd = headers.get("content-disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
    if m:
        return _sanitize(unquote(m.group(1)))
    path = unquote(urlparse(url).path)
    return _sanitize(os.path.basename(path) or "download.bin")


class Engine:
    def __init__(self, store: TaskStore, providers: dict, config):
        self.store = store
        self.providers = providers  # {provider_id: instance}
        self.config = config
        self.tasks: dict[str, Task] = {}
        self.http = httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(20, read=120))
        self.limiter = RateLimiter(getattr(config, "global_speed_limit_kbps", 0))
        self.slots = SlotPool(getattr(config, "max_concurrent_tasks", 3))
        self._link_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._running: dict[str, asyncio.Task] = {}
        self._reporter: asyncio.Task | None = None
        self._shutdown_at: float | None = None   # 自动关机布防时刻
        self._shutdown_fired: bool = False

    def shutdown_status(self) -> dict:
        return {
            "auto_shutdown": self.config.data.get("auto_shutdown", "none"),
            "shutdown_at": self._shutdown_at,
            "fired": self._shutdown_fired,
        }

    # ---------- 生命周期 ----------

    def start(self):
        for t in self.store.load_all():
            if t.status in ("downloading", "resolving", "pending"):
                t.status = "paused"  # 重启后一律转为暂停，用户手动续
            self.tasks[t.id] = t
        self._reporter = asyncio.create_task(self._report_loop())

    async def close(self):
        for rid in list(self._running):
            self._running[rid].cancel()
        if self._reporter:
            self._reporter.cancel()

    # ---------- 任务管理 ----------

    def create(self, provider_id: str, fsid: str, name: str, size: int, dest_dir: str = "") -> Task:
        dest_dir = dest_dir or self.config.dest_dir
        t = Task(provider_id=provider_id, fsid=str(fsid), name=_sanitize(name), size=size)
        t.dest = _unique_path(os.path.join(dest_dir, t.name))
        t.name = os.path.basename(t.dest)
        self.store.save(t)
        self.tasks[t.id] = t
        if self._shutdown_at:
            log.info("新增任务，撤销自动关机倒计时")
            self._shutdown_at = None
        log.info("任务创建 %s ← %s/%s (%s)", t.name, provider_id, t.fsid, t.dest)
        self._spawn(t)
        return t

    def _spawn(self, t: Task):
        if t.id in self._running and not self._running[t.id].done():
            return
        t.pause_event = asyncio.Event()
        self._running[t.id] = asyncio.create_task(self._worker(t))

    def pause(self, task_id: str):
        t = self.tasks[task_id]
        if t.pause_event:
            t.pause_event.set()

    def resume(self, task_id: str):
        t = self.tasks[task_id]
        if t.status not in ("paused", "error"):
            return
        t.status = "pending"
        t.error = ""
        t.link_expires = 0
        self.store.save(t)
        old = self._running.get(task_id)
        if old and not old.done():
            # 旧 worker 还在收尾（Paused 异常传播中），等它退出再拉起
            asyncio.create_task(self._spawn_after(old, t))
        else:
            self._spawn(t)

    async def _spawn_after(self, old: asyncio.Task, t: Task):
        try:
            await old
        except asyncio.CancelledError:
            pass
        if t.status == "pending":
            self._spawn(t)

    def retry(self, task_id: str):
        t = self.tasks[task_id]
        t.status = "pending"
        t.error = ""
        t.link_expires = 0
        self.store.save(t)
        old = self._running.get(task_id)
        if old and not old.done():
            asyncio.create_task(self._spawn_after(old, t))
        else:
            self._spawn(t)

    def delete(self, task_id: str):
        t = self.tasks.get(task_id)
        if t:
            self.pause(task_id)
            if t.status != "done" and os.path.exists(t.dest):
                try:
                    os.remove(t.dest)
                except OSError:
                    pass
            self.store.delete(task_id)
            self.tasks.pop(task_id, None)

    def list_api(self) -> list:
        return [t.to_api() for t in self.tasks.values()]

    # ---------- 直链获取（单飞 + 失效重取） ----------

    async def _get_link(self, t: Task, force: bool = False):
        if not force and t.link_url and time.time() < t.link_expires:
            return t.link_url, t.link_headers
        async with self._link_locks[t.id]:
            if not force and t.link_url and time.time() < t.link_expires:
                return t.link_url, t.link_headers
            provider = self.providers[t.provider_id]
            entry = FileEntry(fsid=t.fsid, name=t.name, size=t.size, is_dir=False)
            link = await provider.resolve(entry)
            t.link_url = link.url
            t.link_headers = link.headers
            t.link_expires = time.time() + max(link.expires_in - 30, 60)
        return t.link_url, t.link_headers

    # ---------- 主流程 ----------

    async def _worker(self, t: Task):
        try:
            await self.slots.acquire()  # 超出并发上限的任务在这里排队（状态保持 pending）
            try:
                await self._run_task(t)
            finally:
                await self.slots.release()
        except Paused:
            t.status = "paused"
            t.speed = 0
            self.store.save(t)
            log.info("任务暂停 %s (%d/%d)", t.name, t.done, t.size)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            t.status = "error"
            t.error = f"{type(e).__name__}: {e}"
            t.speed = 0
            self.store.save(t)
            log.error("任务失败 %s: %s", t.name, t.error)

    async def _run_task(self, t: Task):
        t.status = "resolving"
        self.store.save(t)
        url, headers = await self._get_link(t, force=True)

        # 探测：大小 / 是否支持 Range / 真实文件名
        probe_headers = dict(headers)
        probe_headers["Range"] = "bytes=0-"
        size, ranged = -1, False
        async with self.http.stream("GET", url, headers=probe_headers) as r:
            if r.status_code not in (200, 206):
                r.raise_for_status()
            cr = r.headers.get("content-range", "")
            cl = r.headers.get("content-length", "")
            if r.status_code == 206 and cr:
                size = int(cr.rsplit("/", 1)[-1])
                ranged = True
            elif cl:
                size = int(cl)
            if not t.name or t.name == "download.bin":
                t.name = _sanitize(_filename_from_headers(r.headers, str(r.url)))
                t.dest = os.path.join(os.path.dirname(t.dest), t.name)
        if size == 0:
            raise ValueError("文件大小为 0")
        if size > 0:
            t.size = size
        self.store.save(t)

        os.makedirs(os.path.dirname(t.dest) or ".", exist_ok=True)

        if ranged and size >= self.config.min_segment_mb * 1024 * 1024 // 2:
            await self._download_segmented(t)
        else:
            await self._download_single(t, url, headers, ranged)

        if t.size > 0 and t.done != t.size:
            raise ValueError(f"校验失败：已下 {t.done}/{t.size}")
        t.status = "done"
        t.speed = 0
        self.store.save(t)
        log.info("任务完成 %s (%s)", t.name, t.dest)

    # ---------- 分段并发下载 ----------

    def _split_segments(self, size: int) -> list:
        n = max(1, min(self.config.conns_per_task, size // (self.config.min_segment_mb * 1024 * 1024) or 1))
        step = size // n
        segs = []
        for i in range(n):
            start = i * step
            end = size - 1 if i == n - 1 else (i + 1) * step - 1
            segs.append({"start": start, "end": end, "pos": 0})
        return segs

    async def _download_segmented(self, t: Task):
        if not t.segments:
            t.segments = self._split_segments(t.size)
        fd = os.open(t.dest, os.O_RDWR | os.O_CREAT | os.O_BINARY)
        try:
            os.ftruncate(fd, t.size)
        finally:
            os.close(fd)
        # 每段一个独立句柄；seek+write 之间无 await，同事件循环内互不干扰
        workers = [asyncio.create_task(self._seg_worker(t, seg)) for seg in t.segments]
        await asyncio.gather(*workers)

    async def _seg_worker(self, t: Task, seg: dict):
        fd = os.open(t.dest, os.O_RDWR | os.O_CREAT | os.O_BINARY)
        try:
            await self._seg_loop(t, fd, seg)
        finally:
            os.close(fd)

    async def _seg_loop(self, t: Task, fd: int, seg: dict):
        link_fails = 0
        for attempt in range(SEGMENT_RETRIES + LINK_RETRIES):
            if t.pause_event.is_set():
                raise Paused()
            try:
                url, headers = await self._get_link(t)
                seg_headers = dict(headers)
                seg_headers["Range"] = f"bytes={seg['start'] + seg['pos']}-{seg['end']}"
                async with self.http.stream("GET", url, headers=seg_headers) as r:
                    if r.status_code in (401, 403, 410):
                        t.link_expires = 0  # 直链失效，强制重取
                        link_fails += 1
                        if link_fails > LINK_RETRIES:
                            raise RuntimeError(f"直链反复失效({r.status_code})")
                        await asyncio.sleep(RETRY_BACKOFF)
                        continue
                    if r.status_code not in (200, 206):
                        raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                    async for chunk in r.aiter_bytes(256 * 1024):
                        if t.pause_event.is_set():
                            raise Paused()
                        await self.limiter.consume(len(chunk))
                        os.lseek(fd, seg["start"] + seg["pos"], os.SEEK_SET)
                        os.write(fd, chunk)
                        seg["pos"] += len(chunk)
                        t.done += len(chunk)
                    if seg["start"] + seg["pos"] >= seg["end"] + 1:
                        return  # 本段完成
                    raise ValueError("响应提前结束")
            except Paused:
                raise
            except (httpx.HTTPError, ValueError, OSError) as e:
                if attempt >= SEGMENT_RETRIES + LINK_RETRIES - 1:
                    raise RuntimeError(f"分段重试耗尽: {e}") from e
                await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))

    # ---------- 单连接流式下载 ----------

    async def _download_single(self, t: Task, url: str, headers: dict, ranged: bool):
        mode = "r+b" if ranged and os.path.exists(t.dest) else "wb"
        if ranged and os.path.exists(t.dest) and t.done == 0:
            mode = "wb"
        with open(t.dest, mode) as f:
            if ranged and t.done > 0:
                f.seek(t.done)
            req_headers = dict(headers)
            if ranged and t.done > 0:
                req_headers["Range"] = f"bytes={t.done}-"
            async with self.http.stream("GET", url, headers=req_headers) as r:
                r.raise_for_status()
                if ranged and t.done > 0 and r.status_code != 206:
                    raise ValueError("服务器不支持断点续传")
                async for chunk in r.aiter_bytes(256 * 1024):
                    if t.pause_event.is_set():
                        raise Paused()
                    await self.limiter.consume(len(chunk))
                    f.write(chunk)
                    t.done += len(chunk)

    # ---------- 统计与落盘 ----------

    async def _report_loop(self):
        while True:
            await asyncio.sleep(1)
            active = 0
            for t in self.tasks.values():
                if t.status in ("downloading", "resolving"):
                    t.speed = max(0, t.done - t._last_done)
                    t._last_done = t.done
                    if t.status == "downloading":
                        self.store.save_progress(t)
                    active += 1
                else:
                    t.speed = 0
            await self._check_auto_shutdown(active)

    # ---------- 自动关机 ----------

    async def _check_auto_shutdown(self, active: int):
        mode = self.config.data.get("auto_shutdown", "none")
        if mode in ("none", None, ""):
            self._shutdown_at = None
            return
        if active > 0:
            self._shutdown_at = None  # 有任务在跑，撤销倒计时
            return
        if self._shutdown_fired:
            return
        has_done = any(t.status == "done" for t in self.tasks.values())
        if not has_done:
            return
        if self._shutdown_at is None:
            self._shutdown_at = time.time() + SHUTDOWN_COUNTDOWN
            log.info("全部任务完成，%d 秒后执行 %s（新增任务可撤销）", SHUTDOWN_COUNTDOWN, mode)
            return
        if time.time() >= self._shutdown_at:
            self._shutdown_fired = True
            self._shutdown_at = None
            await asyncio.to_thread(self._execute_shutdown, mode)

    def _execute_shutdown(self, mode: str):
        try:
            if sys.platform != "win32":
                log.warning("自动关机仅支持 Windows")
                return
            if mode == "shutdown":
                subprocess.run(["shutdown", "/s", "/t", "30"], check=False)
                log.info("已执行关机命令（30 秒缓冲）")
            elif mode == "sleep":
                subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=False)
                log.info("已执行睡眠命令")
        except OSError as e:
            log.error("自动关机执行失败: %s", e)
