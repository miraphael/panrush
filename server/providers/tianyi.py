"""天翼云盘驱动（Web API，Cookie 凭据）。

直链需带 Referer 且有效期短，tier 暂标 experimental，待真机 Cookie 验证后升级。
凭据：浏览器登录 cloud.189.cn 后复制 Cookie。
"""

import httpx

from .base import DirectLink, FileEntry, PanError, PanProvider, register

BASE = "https://cloud.189.cn"


@register
class TianyiProvider(PanProvider):
    id = "tianyi"
    name = "天翼云盘"
    tier = "experimental"
    root = "-11"
    cred_fields = [
        {"key": "cookie", "label": "Cookie",
         "placeholder": "登录 cloud.189.cn 后 F12 复制整串 Cookie", "secret": True},
    ]
    help = "浏览器登录 cloud.189.cn → F12 → Network → 复制请求 Cookie。个人盘满速。"

    def _headers(self) -> dict:
        c = self.cred
        if not c or not c.get("cookie"):
            raise PanError("未配置 Cookie")
        return {"Cookie": c["cookie"], "Referer": f"{BASE}/", "User-Agent": "Mozilla/5.0"}

    async def login(self, cred: dict) -> dict:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as cli:
            r = await cli.get(f"{BASE}/api/portal/getUserinfo.action",
                              headers={"Cookie": cred["cookie"], "Referer": f"{BASE}/",
                                       "User-Agent": "Mozilla/5.0"})
        if r.status_code in (401, 403) or "login" in str(r.url):
            raise PanError("Cookie 无效或已过期")
        try:
            data = r.json()
        except Exception:
            raise PanError("Cookie 无效（响应非 JSON，可能未登录）")
        if data.get("res_code") not in (0, None):
            raise PanError(f"天翼接口错误: {data.get('res_message')}")
        return cred

    async def list_dir(self, path: str) -> list[FileEntry]:
        folder_id = path or self.root
        entries = []
        headers = self._headers()
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cli:
            for page in range(1, 100):
                r = await cli.get(f"{BASE}/api/portal/listFiles.action", headers=headers, params={
                    "folderId": folder_id, "pageNum": page, "pageSize": 100,
                    "mediaType": 0, "iconType": 1,
                    "orderBy": "lastOpTime", "descending": True,
                })
                try:
                    data = r.json()
                except Exception:
                    raise PanError(f"列目录失败（HTTP {r.status_code}，可能 Cookie 过期）")
                if data.get("res_code") not in (0, None):
                    raise PanError(f"列目录失败: {data.get('res_message')}")
                vo = data.get("fileListVO") or {}
                for f in vo.get("folderList") or []:
                    entries.append(FileEntry(fsid=str(f["id"]), name=f["name"], size=0, is_dir=True))
                for f in vo.get("fileList") or []:
                    entries.append(FileEntry(
                        fsid=str(f["id"]), name=f["name"],
                        size=int(f.get("size", 0)), is_dir=bool(f.get("dir")),
                    ))
                total = int(vo.get("count") or 0)
                if page * 100 >= total:
                    break
        return entries

    async def resolve(self, entry) -> DirectLink:
        headers = self._headers()
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cli:
            r = await cli.get(f"{BASE}/api/portal/getFileDownloadUrl.action",
                              headers=headers, params={"fileId": entry.fsid})
        data = r.json()
        url = data.get("fileDownloadUrl") or ""
        if data.get("res_code") not in (0, None) or not url:
            raise PanError(f"获取直链失败: {data.get('res_message')}")
        return DirectLink(url=url, headers=headers, expires_in=600)
