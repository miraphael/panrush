"""百度网盘驱动（开放平台 xpan API）。

限速说明（设计文档 §0）：百度按账号在服务端限速，本驱动如实分级——
普通 access_token 下载受限（tier=limited）；SVIP 账号的 token 实测即满速。
凭据：开放平台 OAuth 的 access_token。
"""

import httpx

from .base import DirectLink, FileEntry, PanError, PanProvider, register
from . import HTTP, HTTP_NO_REDIRECT

BASE = "https://pan.baidu.com/rest/2.0/xpan"


@register
class BaiduProvider(PanProvider):
    id = "baidu"
    name = "百度网盘"
    tier = "limited"
    root = "/"
    cred_fields = [
        {"key": "access_token", "label": "access_token", "placeholder": "开放平台 OAuth access_token",
         "secret": True},
    ]
    help = "开放平台 access_token。速度随账号会员等级：普通账号受限(⚠)，SVIP 账号即满速。"

    def _params(self, **extra) -> dict:
        c = self.cred
        if not c or not c.get("access_token"):
            raise PanError("未配置 access_token")
        return {"access_token": c["access_token"], **extra}

    async def login(self, cred: dict) -> dict:
        async with HTTP as cli:
            r = await cli.get(f"{BASE}/quota",
                              params={"access_token": cred["access_token"], "method": "info"})
        data = r.json()
        if data.get("errno") != 0:
            raise PanError(f"token 无效: errno={data.get('errno')}")
        return cred

    async def list_dir(self, path: str) -> list[FileEntry]:
        dir_path = path or "/"
        params = self._params(method="list", dir=dir_path, limit=1000, web="1")
        async with HTTP as cli:
            r = await cli.get(f"{BASE}/file", params=params)
        data = r.json()
        if data.get("errno") != 0:
            raise PanError(f"列目录失败: errno={data.get('errno')}")
        return [FileEntry(
            fsid=str(f["fs_id"]), name=f["server_filename"],
            size=int(f.get("size", 0)), is_dir=bool(f.get("isdir")),
        ) for f in data.get("list", [])]

    async def resolve(self, entry) -> DirectLink:
        params = self._params(method="filemetas", fsids=f"[{entry.fsid}]", dlink="1")
        async with HTTP as cli:
            r = await cli.get(f"{BASE}/file", params=params)
        data = r.json()
        if data.get("errno") != 0 or not data.get("list"):
            raise PanError(f"获取直链失败: errno={data.get('errno')}")
        dlink = data["list"][0]["dlink"]
        c = self.cred
        url = dlink + ("&" if "?" in dlink else "?") + "access_token=" + c["access_token"]
        # 百度要求下载请求携带特定 UA，否则 403
        return DirectLink(url=url, headers={"User-Agent": "pan.baidu.com"}, expires_in=8 * 3600)
