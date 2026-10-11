"""直链驱动：粘贴任意 HTTP(S) 直链直接下载（也用于本地测试）。"""

from .base import DirectLink, PanProvider, register


@register
class DirectProvider(PanProvider):
    id = "direct"
    name = "直链下载"
    tier = "full"
    root = ""
    cred_fields = []
    help = "粘贴任意 HTTP(S) 下载直链。"

    async def resolve(self, entry) -> DirectLink:
        # fsid 即 URL
        return DirectLink(url=entry.fsid, headers={}, expires_in=3600, supports_range=True)
