"""阿里云盘驱动（开放平台 OpenAPI，满速）。

凭据：openapi.alipan.com 创建应用（需开放平台授权）获得 client_id/secret，
再走 OAuth 拿 refresh_token 填入。
"""

import time

import httpx

from .base import DirectLink, FileEntry, PanError, PanProvider, register
from . import HTTP, HTTP_NO_REDIRECT

BASE = "https://openapi.alipan.com"


@register
class AliyunProvider(PanProvider):
    id = "aliyun"
    name = "阿里云盘"
    tier = "full"
    root = "root"
    cred_fields = [
        {"key": "client_id", "label": "client_id", "placeholder": "开放平台应用 ID", "secret": False},
        {"key": "client_secret", "label": "client_secret", "placeholder": "开放平台应用密钥", "secret": True},
        {"key": "refresh_token", "label": "refresh_token", "placeholder": "OAuth 授权后获得", "secret": True},
    ]
    help = "阿里云盘开放平台应用 + OAuth refresh_token。官方 API 满速，QPS 受开放平台配额限制。"

    def __init__(self, cred_store):
        super().__init__(cred_store)
        self._token: str = ""
        self._token_exp: float = 0
        self._drive_id: str = ""

    async def _auth_headers(self) -> dict:
        if not self._token or time.time() > self._token_exp:
            c = self.cred
            if not c:
                raise PanError("未配置凭据")
            async with HTTP as cli:
                r = await cli.post(f"{BASE}/oauth/access_token", json={
                    "client_id": c["client_id"], "client_secret": c["client_secret"],
                    "grant_type": "refresh_token", "refresh_token": c["refresh_token"],
                })
            data = r.json()
            if "access_token" not in data:
                raise PanError(f"刷新 token 失败: {data}")
            self._token = data["access_token"]
            self._token_exp = time.time() + int(data.get("expires_in", 7200)) - 300
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    async def _get_drive_id(self, headers: dict) -> str:
        if self._drive_id:
            return self._drive_id
        async with HTTP as cli:
            r = await cli.post(f"{BASE}/adrive/v1.0/user/getInfo", headers=headers, json={})
        data = r.json()
        self._drive_id = data.get("default_drive_id") or ""
        if not self._drive_id:
            raise PanError(f"获取 drive_id 失败: {data}")
        return self._drive_id

    async def login(self, cred: dict) -> dict:
        self._token, self._token_exp, self._drive_id = "", 0, ""
        self.cred_store.set(self.id, cred)
        await self._auth_headers()
        return cred

    async def list_dir(self, path: str) -> list[FileEntry]:
        headers = await self._auth_headers()
        drive_id = await self._get_drive_id(headers)
        parent = path or "root"
        entries, marker = [], ""
        async with HTTP as cli:
            for _ in range(100):
                body = {"drive_id": drive_id, "parent_file_id": parent, "limit": 100,
                        "order_by": "name", "order_direction": "ASC"}
                if marker:
                    body["marker"] = marker
                r = await cli.post(f"{BASE}/adrive/v1.0/openFile/list", headers=headers, json=body)
                data = r.json()
                if "items" not in data:
                    raise PanError(f"列目录失败: {data}")
                for f in data["items"]:
                    entries.append(FileEntry(
                        fsid=f["file_id"], name=f["name"],
                        size=int(f.get("size", 0)), is_dir=f.get("type") == "folder",
                    ))
                marker = data.get("next_marker", "")
                if not marker:
                    break
        return entries

    async def resolve(self, entry) -> DirectLink:
        headers = await self._auth_headers()
        drive_id = await self._get_drive_id(headers)
        async with HTTP as cli:
            r = await cli.post(f"{BASE}/adrive/v1.0/openFile/getDownloadUrl",
                               headers=headers, json={"drive_id": drive_id, "file_id": entry.fsid})
        data = r.json()
        if not data.get("url"):
            raise PanError(f"获取直链失败: {data}")
        return DirectLink(url=data["url"], headers={}, expires_in=4 * 3600)
