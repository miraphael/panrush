"""对外只读 WebDAV 出口：把已配置的网盘暴露成 DAV，供 rclone / 播放器 / 资源管理器挂载。

挂载地址: http://127.0.0.1:18400/dav/{provider_id}/
- PROPFIND 列目录（Depth 0/1），目录树按需遍历（fid 型网盘逐级 walk，带 60s 缓存）
- GET/HEAD 下载：provider.resolve 取直链后**代理流式转发**（网盘直链常需特定头，
  不能简单 302），上游支持 Range 则透传 Range/206。
只读实现，不支持写操作。
"""

import time
import xml.sax.saxutils as sx
from urllib.parse import quote

import httpx

from .providers.base import FileEntry

XML_HEAD = '<?xml version="1.0" encoding="utf-8"?>'
CHUNK = 256 * 1024


def _x(s: str) -> str:
    return sx.escape(s, {'"': "&quot;"})


class DavApp:
    """纯 ASGI 应用，FastAPI 中 app.mount("/dav", DavApp(providers))。"""

    def __init__(self, providers):
        self.providers = providers
        self._cache: dict = {}   # (pid, fsid) -> (ts, entries)
        self._ttl = 60

    # ---------- ASGI 入口 ----------

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        method = scope["method"].upper()
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        path = scope.get("path", "")
        # 兼容不同 Mount 实现：path 可能含 /dav 前缀（root_path 约定不一）
        if path.startswith("/dav/"):
            path = path[len("/dav"):]
        elif path == "/dav":
            path = "/"
        parts = path.strip("/").split("/", 1)
        pid = parts[0] if parts and parts[0] else ""
        sub = parts[1] if len(parts) > 1 else ""

        if method == "OPTIONS":
            await self._send(send, 200, [
                ("DAV", "1"), ("Allow", "OPTIONS, PROPFIND, GET, HEAD"),
                ("MS-Author-Via", "DAV")], b"")
            return
        provider = self.providers.get(pid)
        if not provider:
            await self._send(send, 404, [], b"unknown provider")
            return
        if not provider.cred_saved():
            await self._send(send, 502, [], f"{provider.name} 未配置凭据".encode())
            return
        try:
            if method == "PROPFIND":
                await self._propfind(provider, pid, sub, headers.get("depth", "1"), send)
            elif method in ("GET", "HEAD"):
                await self._get(provider, sub, headers, send, head_only=(method == "HEAD"))
            else:
                await self._send(send, 405, [], b"method not allowed")
        except Exception as e:
            try:
                await self._send(send, 502, [], f"{type(e).__name__}: {e}".encode())
            except Exception:
                pass

    # ---------- 路径解析与列目录缓存 ----------

    async def _list(self, provider, fsid: str) -> list:
        key = (provider.id, fsid)
        ts, val = self._cache.get(key, (0.0, None))
        if val is not None and time.time() - ts < self._ttl:
            return val
        val = await provider.list_dir(fsid)
        self._cache[key] = (time.time(), val)
        return val

    async def _resolve(self, provider, sub: str):
        """把 DAV 路径解析成 FileEntry。返回 (entry, relpath)；找不到返回 (None, sub)。"""
        rel = ""
        fsid = provider.root
        if not sub.strip("/"):
            return FileEntry(fsid=fsid, name=provider.name, size=0, is_dir=True), ""
        for name in sub.strip("/").split("/"):
            entries = await self._list(provider, fsid)
            match = next((e for e in entries if e.name == name), None)
            if match is None:
                return None, rel
            rel = f"{rel}/{name}".strip("/")
            if match.is_dir:
                fsid = match.fsid
            else:
                return match, rel
        return FileEntry(fsid=fsid, name=rel.rsplit("/", 1)[-1], size=0, is_dir=True), rel

    # ---------- PROPFIND ----------

    def _entry_xml(self, pid: str, rel: str, e: FileEntry) -> str:
        href = f"/dav/{pid}/{quote(rel)}" if rel else f"/dav/{pid}/"
        if e.is_dir and not href.endswith("/"):
            href += "/"
        cl = f"<D:getcontentlength>{e.size}</D:getcontentlength>" if not e.is_dir else ""
        rt = "<D:resourcetype><D:collection/></D:resourcetype>" if e.is_dir else "<D:resourcetype/>"
        return (f"<D:response><D:href>{_x(href)}</D:href>"
                f"<D:propstat><D:prop>"
                f"<D:displayname>{_x(e.name)}</D:displayname>{cl}{rt}"
                f"</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>")

    async def _propfind(self, provider, pid: str, sub: str, depth: str, send):
        entry, rel = await self._resolve(provider, sub)
        if entry is None:
            await self._send(send, 404, [], b"not found")
            return
        parts = [self._entry_xml(pid, rel, entry)]
        if entry.is_dir and depth != "0":
            base = f"{rel}/" if rel else ""
            for child in await self._list(provider, entry.fsid):
                parts.append(self._entry_xml(pid, base + child.name, child))
        body = (XML_HEAD + '<D:multistatus xmlns:D="DAV:">' + "".join(parts)
                + "</D:multistatus>").encode("utf-8")
        await self._send(send, 207, [("Content-Type", "application/xml; charset=utf-8")], body)

    # ---------- GET / HEAD（代理流式转发） ----------

    async def _get(self, provider, sub: str, req_headers: dict, send, head_only: bool):
        entry, _rel = await self._resolve(provider, sub)
        if entry is None or entry.is_dir:
            await self._send(send, 404, [], b"not found")
            return
        fe = FileEntry(fsid=entry.fsid, name=entry.name, size=entry.size, is_dir=False)
        link = await provider.resolve(fe)
        for attempt in (0, 1):
            upstream_headers = dict(link.headers)
            rng = req_headers.get("range")
            if rng and link.supports_range:
                upstream_headers["Range"] = rng
            async with httpx.AsyncClient(
                    follow_redirects=True, timeout=httpx.Timeout(20, read=600)) as cli:
                async with cli.stream("GET", link.url, headers=upstream_headers) as r:
                    if r.status_code in (401, 403, 410) and attempt == 0:
                        link = await provider.resolve(fe)  # 直链过期，重取一次
                        continue
                    out = [("Accept-Ranges", "bytes")]
                    for h in ("content-length", "content-range", "content-type",
                              "content-disposition"):
                        if h in r.headers:
                            out.append((h, r.headers[h]))
                    body_iter = (None if head_only else
                                 (chunk async for chunk in r.aiter_bytes(CHUNK)))
                    await self._send(send, r.status_code, out, None, body_iter)
                    return
        await self._send(send, 502, [], b"direct link keeps failing")

    # ---------- ASGI 响应工具 ----------

    async def _send(self, send, status: int, headers: list, body, body_iter=None):
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(k.encode("latin-1"), str(v).encode("latin-1")) for k, v in headers],
        })
        if body_iter is None:
            await send({"type": "http.response.body", "body": body or b""})
            return
        async for chunk in body_iter:
            if chunk:
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})
