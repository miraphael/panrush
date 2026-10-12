"""驱动注册表：import 本包即完成全部注册。

HTTP / HTTP_NO_REDIRECT 是全驱动共享的连接池（keep-alive），
避免每次列目录/取直链都重新建立 TCP+TLS——这是"浏览反应慢"的主要病根。
"""

import httpx

from .base import PanError, PanProvider, DirectLink, FileEntry, REGISTRY

class SharedClient(httpx.AsyncClient):
    """常驻共享客户端：允许 `async with HTTP as cli:` 写法但进入/退出都不改变状态。

    驱动代码原本都是 `async with httpx.AsyncClient(...)` 的临时客户端风格，
    统一改共享池时保留该写法，靠这个子类保证连接池不被误关/误重开。
    httpx 客户端本就支持不经上下文直接使用（连接按需建立、自动复用）。
    """

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type=None, exc=None, tb=None):
        return None


HTTP = SharedClient(follow_redirects=True, timeout=httpx.Timeout(15, read=120))
HTTP_NO_REDIRECT = SharedClient(follow_redirects=False, timeout=httpx.Timeout(15, read=120))


def _create_all(cred_store) -> dict:
    # noqa: F401 — import 副作用完成注册
    from . import direct as _d  # noqa: F401
    from . import webdav as _w  # noqa: F401
    from . import pan123 as _p  # noqa: F401
    from . import quark as _q  # noqa: F401
    from . import aliyun as _a  # noqa: F401
    from . import baidu as _b  # noqa: F401
    from . import tianyi as _t  # noqa: F401
    from . import onedrive as _od  # noqa: F401
    from . import gdrive as _gd  # noqa: F401
    from . import dropbox as _db  # noqa: F401
    from . import lanzou as _lz  # noqa: F401
    from . import pan123share as _p1s  # noqa: F401

    return {pid: cls(cred_store) for pid, cls in REGISTRY.items()}


__all__ = ["PanError", "PanProvider", "DirectLink", "FileEntry", "REGISTRY",
           "_create_all", "HTTP", "HTTP_NO_REDIRECT", "httpx"]
