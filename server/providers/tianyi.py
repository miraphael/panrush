"""天翼云盘驱动骨架（Web API，Cookie 凭据）。

天翼直链需带 Referer 且签名参数随前端版本变动，M3 阶段按 alist 天翼驱动补全。
"""

from .base import DirectLink, FileEntry, PanError, PanProvider, register


@register
class TianyiProvider(PanProvider):
    id = "tianyi"
    name = "天翼云盘"
    tier = "experimental"
    root = "-11"
    cred_fields = [
        {"key": "cookie", "label": "Cookie", "placeholder": "M3 接入", "secret": True},
    ]
    help = "计划 M3 阶段接入（驱动骨架已就位）。"

    async def list_dir(self, path: str) -> list[FileEntry]:
        raise PanError("天翼云盘驱动尚未完成，计划 M3 接入")

    async def resolve(self, entry) -> DirectLink:
        raise PanError("天翼云盘驱动尚未完成，计划 M3 接入")
