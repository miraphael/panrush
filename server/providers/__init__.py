"""驱动注册表：import 本包即完成全部注册。"""

import httpx

from .base import PanError, PanProvider, DirectLink, FileEntry, REGISTRY


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

    return {pid: cls(cred_store) for pid, cls in REGISTRY.items()}


__all__ = ["PanError", "PanProvider", "DirectLink", "FileEntry", "REGISTRY", "_create_all", "httpx"]
