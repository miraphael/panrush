"""OneDrive 驱动（Microsoft Graph OAuth，满速）。

凭据：在 Azure 门户注册应用（personal Microsoft account + delegated Files.Read），
或使用自建 client_id + refresh_token。Graph items/content 端点 302 到预签名 CDN，
支持 Range 多线程。
"""

import time
from urllib.parse import quote

import httpx

from .base import DirectLink, FileEntry, PanError, PanProvider, register

GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"


@register
class OneDriveProvider(PanProvider):
    id = "onedrive"
    name = "OneDrive"
    tier = "full"
    root = ""
    cred_fields = [
        {"key": "client_id", "label": "client_id", "placeholder": "Azure 应用 ID", "secret": False},
        {"key": "client_secret", "label": "client_secret（可选）", "placeholder": "", "secret": True},
        {"key": "refresh_token", "label": "refresh_token", "placeholder": "OAuth 授权后获得", "secret": True},
    ]
    help = "Microsoft Graph API。Azure 注册应用（Files.Read 委托权限）后填 client_id 与 refresh_token。"

    def __init__(self, cred_store):
        super().__init__(cred_store)
        self._token: str = ""
        self._token_exp: float = 0
        self._drive_id: str = ""

    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp:
            return self._token
        c = self.cred
        if not c:
            raise PanError("未配置凭据")
        body = {
            "grant_type": "refresh_token",
            "refresh_token": c["refresh_token"],
            "client_id": c["client_id"],
        }
        if c.get("client_secret"):
            body["client_secret"] = c["client_secret"]
        async with httpx.AsyncClient(timeout=20) as cli:
            r = await cli.post(TOKEN_URL, data=body)
        data = r.json()
        if "access_token" not in data:
            raise PanError(f"刷新 token 失败: {data.get('error_description', data)}")
        self._token = data["access_token"]
        self._token_exp = time.time() + int(data.get("expires_in", 3600)) - 120
        return self._token

    async def _headers(self) -> dict:
        return {"Authorization": f"Bearer {await self._access_token()}"}

    async def _drive_id(self) -> str:
        if self._drive_id:
            return self._drive_id
        async with httpx.AsyncClient(timeout=20) as cli:
            r = await cli.get(f"{GRAPH}/me/drive", headers=await self._headers())
        if r.status_code == 401:
            self._token, self._token_exp = "", 0
            raise PanError("token 无效（401），请重新授权")
        self._drive_id = r.json()["id"]
        return self._drive_id

    async def login(self, cred: dict) -> dict:
        self._token, self._token_exp, self._drive_id = "", 0, ""
        self.cred_store.set(self.id, cred)
        await self._access_token()
        await self._drive_id()
        return cred

    async def list_dir(self, path: str) -> list[FileEntry]:
        headers = await self._headers()
        drive = await self._drive_id()
        base = f"{GRAPH}/drives/{drive}/root"
        url = f"{base}/children" if not path else f"{base}:/{quote(path)}:/children"
        entries = []
        async with httpx.AsyncClient(timeout=30) as cli:
            while url:
                r = await cli.get(url, headers=headers)
                if r.status_code == 401:
                    raise PanError("token 失效，请重新授权")
                if r.status_code >= 400:
                    raise PanError(f"列目录失败: HTTP {r.status_code} {r.json().get('error', {}).get('message', '')}")
                for item in r.json().get("value", []):
                    entries.append(FileEntry(
                        fsid=item["id"], name=item["name"],
                        size=int(item.get("size", 0)),
                        is_dir="folder" in item,
                    ))
                url = r.json().get("@odata.nextLink")
        return entries

    async def resolve(self, entry) -> DirectLink:
        headers = await self._headers()
        drive = await self._drive_id()
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as cli:
            r = await cli.get(f"{GRAPH}/drives/{drive}/items/{entry.fsid}/content", headers=headers)
        if r.status_code in (301, 302, 307):
            url = r.headers["location"]
        elif r.status_code == 200:
            url = str(r.url)
        elif r.status_code == 401:
            raise PanError("token 失效，请重新授权")
        else:
            raise PanError(f"获取直链失败: HTTP {r.status_code}")
        return DirectLink(url=url, headers={}, expires_in=3600)
