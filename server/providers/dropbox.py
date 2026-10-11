"""Dropbox 驱动（官方 OAuth，满速）。

凭据：Dropbox App Console 创建应用（files.metadata.read + files.content.read），
生成 refresh_token（access_type=offline）后填入。
"""

import json
import time

import httpx

from .base import DirectLink, FileEntry, PanError, PanProvider, register

API = "https://api.dropboxapi.com/2/files"
CONTENT = "https://content.dropboxapi.com/2/files"


@register
class DropboxProvider(PanProvider):
    id = "dropbox"
    name = "Dropbox"
    tier = "full"
    root = ""
    cred_fields = [
        {"key": "client_id", "label": "app key", "placeholder": "Dropbox 应用 key", "secret": False},
        {"key": "client_secret", "label": "app secret", "placeholder": "", "secret": True},
        {"key": "refresh_token", "label": "refresh_token", "placeholder": "OAuth(offline) 后获得", "secret": True},
    ]
    help = "Dropbox App Console 建应用 + refresh_token（access_type=offline）。"

    def __init__(self, cred_store):
        super().__init__(cred_store)
        self._token: str = ""
        self._token_exp: float = 0

    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp:
            return self._token
        c = self.cred
        if not c:
            raise PanError("未配置凭据")
        async with httpx.AsyncClient(timeout=20) as cli:
            r = await cli.post("https://api.dropboxapi.com/oauth2/token", data={
                "grant_type": "refresh_token",
                "refresh_token": c["refresh_token"],
                "client_id": c["client_id"],
                "client_secret": c["client_secret"],
            })
        data = r.json()
        if "access_token" not in data:
            raise PanError(f"刷新 token 失败: {data.get('error_description', data)}")
        self._token = data["access_token"]
        self._token_exp = time.time() + int(data.get("expires_in", 14400)) - 300
        return self._token

    async def login(self, cred: dict) -> dict:
        self._token, self._token_exp = "", 0
        self.cred_store.set(self.id, cred)
        await self._access_token()
        return cred

    async def list_dir(self, path: str) -> list[FileEntry]:
        token = await self._access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        entries = []
        async with httpx.AsyncClient(timeout=30) as cli:
            body = {"path": path or "", "limit": 500}
            while body is not None:
                endpoint = f"{API}/list_folder" if "path" in body else f"{API}/list_folder/continue"
                r = await cli.post(endpoint, headers=headers, json=body)
                if r.status_code == 401:
                    raise PanError("token 失效，请重新授权")
                if r.status_code == 409:
                    raise PanError(f"路径不存在: {body.get('path')}")
                if r.status_code >= 400:
                    raise PanError(f"列目录失败: HTTP {r.status_code}")
                data = r.json()
                for item in data.get("entries", []):
                    entries.append(FileEntry(
                        fsid=item["id"], name=item["name"],
                        size=int(item.get("size", 0)),
                        is_dir=item[".tag"] == "folder",
                    ))
                body = {"cursor": data["cursor"]} if data.get("has_more") else None
        return entries

    async def resolve(self, entry) -> DirectLink:
        token = await self._access_token()
        arg = json.dumps({"path": entry.fsid})
        return DirectLink(
            url=f"{CONTENT}/download",
            headers={"Authorization": f"Bearer {token}", "Dropbox-API-Arg": arg},
            expires_in=4 * 3600,
        )
