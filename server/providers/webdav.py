"""WebDAV 驱动：通用兜底，支持任意 WebDAV 服务器 / 挂载点。"""

import base64
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx

from .base import DirectLink, FileEntry, PanError, PanProvider, register

DAV = "{DAV:}"


def _basic_auth(user: str, pwd: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode()


@register
class WebDAVProvider(PanProvider):
    id = "webdav"
    name = "WebDAV"
    tier = "full"
    root = "/"
    cred_fields = [
        {"key": "base_url", "label": "服务器地址", "placeholder": "https://dav.example.com/dav", "secret": False},
        {"key": "username", "label": "用户名", "placeholder": "", "secret": False},
        {"key": "password", "label": "密码", "placeholder": "", "secret": True},
    ]
    help = "任意 WebDAV 服务（坚果云、InfiniCloud、自建等），满速。"

    def _url(self, path: str) -> str:
        base = self.cred["base_url"].rstrip("/")
        return base + quote(path or "/")

    def _headers(self) -> dict:
        c = self.cred
        return {"Authorization": _basic_auth(c.get("username", ""), c.get("password", ""))}

    async def login(self, cred: dict) -> dict:
        url = cred["base_url"].rstrip("/") + "/"
        async with httpx.AsyncClient(timeout=20) as cli:
            r = await cli.request(
                "PROPFIND", url,
                headers={"Authorization": _basic_auth(cred.get("username", ""), cred.get("password", "")),
                         "Depth": "0"},
            )
        if r.status_code in (401, 403):
            raise PanError("认证失败（401/403）")
        if r.status_code >= 400:
            raise PanError(f"服务器返回 HTTP {r.status_code}")
        return cred

    async def list_dir(self, path: str) -> list[FileEntry]:
        path = path or "/"
        body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
                "<d:displayname/><d:getcontentlength/><d:resourcetype/></d:prop></d:propfind>")
        async with httpx.AsyncClient(timeout=30) as cli:
            r = await cli.request("PROPFIND", self._url(path), headers={**self._headers(), "Depth": "1"}, content=body)
        if r.status_code >= 400:
            raise PanError(f"PROPFIND HTTP {r.status_code}")
        root = ET.fromstring(r.content)
        entries = []
        req_path = path.rstrip("/") + "/"
        for resp in root.iter(DAV + "response"):
            href = (resp.findtext(DAV + "href") or "").rstrip("/")
            name = resp.findtext(f"{DAV}propstat/{DAV}prop/{DAV}displayname") or unquote_name(href)
            if not name or quote(req_path.rstrip("/")) == href:
                continue
            coll = resp.find(f"{DAV}propstat/{DAV}prop/{DAV}resourcetype/{DAV}collection") is not None
            size = int(resp.findtext(f"{DAV}propstat/{DAV}prop/{DAV}getcontentlength") or 0)
            full = (path.rstrip("/") + "/" + name) if path != "/" else "/" + name
            entries.append(FileEntry(fsid=full, name=name, size=size, is_dir=coll))
        return entries

    async def resolve(self, entry) -> DirectLink:
        return DirectLink(url=self._url(entry.fsid), headers=self._headers(), expires_in=86400)


def unquote_name(href: str) -> str:
    from urllib.parse import unquote
    return unquote(href.rstrip("/").rsplit("/", 1)[-1]) if "/" in href else ""
