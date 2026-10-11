"""Google Drive 驱动（官方 OAuth，满速）。

凭据：Google Cloud Console 创建 OAuth 客户端（Desktop app），
生成 refresh_token 后填入。API 直链支持 Range。
"""

import time
from urllib.parse import quote

import httpx

from .base import DirectLink, FileEntry, PanError, PanProvider, register

FOLDER_MIME = "application/vnd.google-apps.folder"


@register
class GDriveProvider(PanProvider):
    id = "gdrive"
    name = "Google Drive"
    tier = "full"
    oauth = True   # 支持一键授权向导
    root = "root"
    cred_fields = [
        {"key": "client_id", "label": "client_id", "placeholder": "Google OAuth 客户端 ID", "secret": False},
        {"key": "client_secret", "label": "client_secret", "placeholder": "", "secret": True},
        {"key": "refresh_token", "label": "refresh_token", "placeholder": "OAuth 授权后获得", "secret": True},
    ]
    help = "Google Cloud Console 建 OAuth Desktop 客户端 + refresh_token（scope: drive.readonly 即可）。"

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
            r = await cli.post("https://oauth2.googleapis.com/token", data={
                "grant_type": "refresh_token",
                "refresh_token": c["refresh_token"],
                "client_id": c["client_id"],
                "client_secret": c["client_secret"],
            })
        data = r.json()
        if "access_token" not in data:
            raise PanError(f"刷新 token 失败: {data.get('error_description', data)}")
        self._token = data["access_token"]
        self._token_exp = time.time() + int(data.get("expires_in", 3600)) - 120
        return self._token

    async def login(self, cred: dict) -> dict:
        self._token, self._token_exp = "", 0
        self.cred_store.set(self.id, cred)
        await self._access_token()
        return cred

    async def list_dir(self, path: str) -> list[FileEntry]:
        token = await self._access_token()
        headers = {"Authorization": f"Bearer {token}"}
        folder = path or "root"
        q = quote(f"'{folder}' in parents and trashed = false")
        entries = []
        url = f"https://www.googleapis.com/drive/v3/files?q={q}&pageSize=200" \
              "&fields=nextPageToken,files(id,name,size,mimeType)&orderBy=folder,name"
        async with httpx.AsyncClient(timeout=30) as cli:
            while url:
                r = await cli.get(url, headers=headers)
                if r.status_code == 401:
                    raise PanError("token 失效，请重新授权")
                if r.status_code >= 400:
                    raise PanError(f"列目录失败: {r.json().get('error', {}).get('message', r.status_code)}")
                data = r.json()
                for f in data.get("files", []):
                    entries.append(FileEntry(
                        fsid=f["id"], name=f["name"],
                        size=int(f.get("size", 0)),
                        is_dir=f.get("mimeType") == FOLDER_MIME,
                    ))
                page_token = data.get("nextPageToken")
                url = (f"https://www.googleapis.com/drive/v3/files?q={q}&pageSize=200"
                       f"&pageToken={page_token}&fields=nextPageToken,files(id,name,size,mimeType)"
                       "&orderBy=folder,name") if page_token else None
        return entries

    async def resolve(self, entry) -> DirectLink:
        token = await self._access_token()
        url = f"https://www.googleapis.com/drive/v3/files/{entry.fsid}?alt=media"
        return DirectLink(url=url, headers={"Authorization": f"Bearer {token}"}, expires_in=3600)
