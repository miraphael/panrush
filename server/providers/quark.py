"""夸克网盘驱动（Web API，Cookie 凭据，基本满速）。

直链时效很短（分钟级），引擎的"即取即下 + 失效重取"机制专门适配这种盘。
路径导航用夸克的 fid（根目录 fid = 0）。
"""

import httpx

from .base import DirectLink, FileEntry, PanError, PanProvider, register

BASE = "https://drive-pc.quark.cn/1/clouddrive"
COMMON = {"pr": "ucpro", "fr": "pc"}


@register
class QuarkProvider(PanProvider):
    id = "quark"
    name = "夸克网盘"
    tier = "full"
    root = "0"
    cred_fields = [
        {"key": "cookie", "label": "Cookie", "placeholder": "从 pan.quark.cn 登录后 F12 复制整串 Cookie",
         "secret": True},
    ]
    help = "浏览器登录 pan.quark.cn → F12 → Network → 复制请求里的 Cookie 填入。"

    def _headers(self) -> dict:
        c = self.cred
        if not c or not c.get("cookie"):
            raise PanError("未配置 Cookie")
        return {"Cookie": c["cookie"], "Referer": "https://pan.quark.cn/"}

    async def login(self, cred: dict) -> dict:
        async with httpx.AsyncClient(timeout=20) as cli:
            r = await cli.get(f"{BASE}/member", params=COMMON,
                              headers={"Cookie": cred["cookie"], "Referer": "https://pan.quark.cn/"})
        data = r.json()
        if not data.get("data", {}).get("member"):
            raise PanError("Cookie 无效或已过期")
        return cred

    async def list_dir(self, path: str) -> list[FileEntry]:
        fid = path or "0"
        headers = self._headers()
        entries = []
        async with httpx.AsyncClient(timeout=30) as cli:
            for page in range(1, 100):
                params = {**COMMON, "p": "0", "fid": fid, "_page": page, "_size": 200,
                          "_fetch_total": "1", "_sort": "file_type:asc,file_name:asc"}
                r = await cli.get(f"{BASE}/file/sort", params=params, headers=headers)
                data = r.json()
                if data.get("status") != 200:
                    raise PanError(f"列目录失败: {data.get('message') or data.get('status')}")
                items = data["data"].get("list", [])
                for f in items:
                    entries.append(FileEntry(
                        fsid=f["fid"], name=f["file_name"],
                        size=int(f.get("size", 0)), is_dir=bool(f.get("dir")),
                    ))
                if len(items) < 200:
                    break
        return entries

    async def resolve(self, entry) -> DirectLink:
        headers = self._headers()
        async with httpx.AsyncClient(timeout=30) as cli:
            r = await cli.post(f"{BASE}/file/download", params=COMMON,
                               json={"fids": [entry.fsid]}, headers=headers)
        data = r.json()
        urls = (data.get("data") or [])
        if not urls or not urls[0].get("download_url"):
            raise PanError(f"获取直链失败: {data.get('message')}")
        return DirectLink(url=urls[0]["download_url"], headers=headers, expires_in=300)
