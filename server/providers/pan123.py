"""123云盘开放平台驱动（官方 API，满速）。

凭据：在 open-api.123pan.com 创建应用获得 clientID/clientSecret。
"""

import time

import httpx

from .base import DirectLink, FileEntry, PanError, PanProvider, register
from . import HTTP, HTTP_NO_REDIRECT

BASE = "https://open-api.123pan.com"


@register
class Pan123Provider(PanProvider):
    id = "pan123"
    name = "123云盘"
    tier = "full"
    root = "0"
    cred_fields = [
        {"key": "client_id", "label": "clientID", "placeholder": "开放平台应用 ID", "secret": False},
        {"key": "client_secret", "label": "clientSecret", "placeholder": "开放平台应用密钥", "secret": True},
    ]
    help = "123云盘开放平台（open-api.123pan.com）创建应用后填入，官方 API 满速。"

    def __init__(self, cred_store):
        super().__init__(cred_store)
        self._token: str = ""
        self._token_exp: float = 0

    async def _auth_headers(self) -> dict:
        if not self._token or time.time() > self._token_exp:
            c = self.cred
            if not c:
                raise PanError("未配置凭据")
            async with HTTP as cli:
                r = await cli.post(f"{BASE}/api/v1/access_token",
                                   json={"clientID": c["client_id"], "clientSecret": c["client_secret"]})
            data = r.json()
            if data.get("code") not in (0, 200):
                raise PanError(f"获取 token 失败: {data.get('message')}")
            self._token = data["data"]["accessToken"]
            self._token_exp = time.time() + data["data"].get("expiresIn", 30 * 86400) - 3600
        return {"Platform": "open_platform", "Authorization": f"Bearer {self._token}"}

    async def login(self, cred: dict) -> dict:
        async with HTTP as cli:
            r = await cli.post(f"{BASE}/api/v1/access_token",
                               json={"clientID": cred["client_id"], "clientSecret": cred["client_secret"]})
        data = r.json()
        if data.get("code") not in (0, 200):
            raise PanError(f"凭据无效: {data.get('message')}")
        return cred

    async def list_dir(self, path: str) -> list[FileEntry]:
        headers = await self._auth_headers()
        parent = int(path or 0)
        entries, last_file_id = [], None
        async with HTTP as cli:
            for _ in range(50):  # 防御性分页上限
                params = {"parentFileId": parent, "limit": 100}
                if last_file_id is not None:
                    params["lastFileId"] = last_file_id
                r = await cli.get(f"{BASE}/upload/v1/file/list", params=params, headers=headers)
                data = r.json()
                if data.get("code") not in (0, 200):
                    raise PanError(f"列目录失败: {data.get('message')}")
                for f in data["data"].get("fileList", []):
                    entries.append(FileEntry(
                        fsid=str(f["fileId"]), name=f["filename"],
                        size=int(f.get("size", 0)), is_dir=f.get("type") == 1,
                    ))
                last_file_id = data["data"].get("lastFileId", -1)
                if last_file_id in (-1, None):
                    break
        return entries

    async def resolve(self, entry) -> DirectLink:
        headers = await self._auth_headers()
        async with HTTP as cli:
            r = await cli.post(f"{BASE}/upload/v1/file/download_info",
                               json={"fileId": int(entry.fsid)}, headers=headers)
        data = r.json()
        if data.get("code") not in (0, 200) or not data["data"].get("downloadUrl"):
            raise PanError(f"获取直链失败: {data.get('message')}")
        return DirectLink(url=data["data"]["downloadUrl"], headers={}, expires_in=3600)
