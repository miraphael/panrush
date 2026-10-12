"""驱动插件协议。新增网盘 = 继承 PanProvider 并用 @register 注册。"""

from dataclasses import dataclass, field


@dataclass
class FileEntry:
    fsid: str          # 提供方内部 ID（可能是数字 fid、路径等）
    name: str
    size: int = 0
    is_dir: bool = False


@dataclass
class DirectLink:
    url: str
    headers: dict = field(default_factory=dict)
    expires_in: int = 3600        # 秒；短时效直链引擎会"即取即下"
    supports_range: bool = True


class PanError(Exception):
    pass


class PanProvider:
    id: str = ""
    name: str = ""
    tier: str = "experimental"    # full(满速) / limited(受限) / experimental
    root: str = "0"
    cred_fields: list = []        # [{key,label,placeholder,secret}]
    cred_optional: bool = False   # True = 凭据可选（分享直取类：没配也能用）
    help: str = ""

    def __init__(self, cred_store):
        self.cred_store = cred_store

    # 凭据
    @property
    def cred(self) -> dict | None:
        return self.cred_store.get(self.id) if self.cred_store else None

    def cred_saved(self) -> bool:
        return bool(self.cred_store) and self.cred_store.has(self.id)

    async def login(self, cred: dict) -> dict:
        """校验凭据；可在这里换取/缓存 token。校验失败抛 PanError。"""
        return cred

    # 文件系统
    async def list_dir(self, path: str) -> list[FileEntry]:
        raise PanError("该驱动不支持浏览")

    async def resolve(self, entry: FileEntry) -> DirectLink:
        raise NotImplementedError


REGISTRY: dict[str, type] = {}


def register(cls):
    REGISTRY[cls.id] = cls
    return cls
