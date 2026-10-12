"""123云盘「分享链接」直取驱动（免登录，第 12 个驱动）。

2026 实测流程（web 端 b/api，无需账号）：
- 列目录: GET  https://api.123278.com/b/api/share/get?shareKey=&SharePwd=&ParentFileId=&Page=&next=
- 取直链: POST https://api.123278.com/b/api/v2/share/download/info?<签名>
          body = {ShareKey, FileID, S3keyFlag, Size, Etag}
          返回 dispatchList[*].prefix（多条 CDN 线路）+ downloadPath，拼起来即直链
- 请求需带 CRC32 签名串（encode123）：?<crc(时分串)>=<时间戳>-<随机数>-<总CRC>

已知限制（如实标注，非本工具可绕过）：
- 分享方「提取流量包」耗尽 → 5103/5112，此时任何工具都下不了，只能等分享方续流量包
- 带提取码的分享：把链接连 ?pwd=xxxx 一起粘贴即可
"""

import random
import re
import time
import zlib
from datetime import datetime

from . import HTTP
from .base import DirectLink, FileEntry, PanError, PanProvider, register

API = "https://api.123278.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 123 云盘的参数签名：时间串按字符映射表转字母，再套一层 CRC32
CHAR_MAP = "adefghlmyijnopkqrstubcvwsz"


def _crc32(s: str) -> int:
    return zlib.crc32(s.encode()) & 0xFFFFFFFF


def encode123(path: str, way: str, version: str, ts_ms: str) -> str:
    """生成 123 云盘接口的签名查询串（与 web 端 JS 行为一致）。"""
    a = (10000000 * random.randint(1, 10000000)) // 10000
    time_long = int(ts_ms) // 1000
    tstr = datetime.fromtimestamp(time_long).strftime("%Y%m%d%H%M")
    g = "".join(CHAR_MAP[0] if int(c) == 0 else CHAR_MAP[int(c) - 1] for c in tstr)
    y = _crc32(g)
    final_crc = _crc32(f"{time_long}|{a}|{path}|{way}|{version}|{y}")
    return f"?{y}={time_long}-{a}-{final_crc}"


def parse_share_url(url: str) -> tuple[str, str]:
    """从分享链接里取出 (shareKey, 提取码)。"""
    m = re.search(r"/(?:s|web)/?([A-Za-z0-9]+-[A-Za-z0-9]+)", url)
    if not m:
        m = re.search(r"/s/([A-Za-z0-9\-]+)", url)
    if not m:
        raise PanError("无法从链接中识别 123 云盘分享 ID")
    key = m.group(1).replace(".html", "")
    pwd = ""
    mp = re.search(r"(?:pwd|password)=([A-Za-z0-9]{1,8})", url, re.I)
    if mp:
        pwd = mp.group(1)
    return key, pwd


def _pack(key: str, pwd: str, *fields: str) -> str:
    return f"{key}#{pwd}|" + "|".join(str(f) for f in fields)


def _unpack(fsid: str) -> tuple[str, str, list[str]]:
    head, _, rest = fsid.partition("|")
    key, _, pwd = head.partition("#")
    return key, pwd, rest.split("|") if rest else []


@register
class Pan123ShareProvider(PanProvider):
    id = "pan123share"
    name = "123云盘(分享直取)"
    tier = "full"
    root = ""
    cred_fields = []
    help = ("免登录直取 123 云盘分享链接：把分享地址（如 www.123pan.com/s/xxxx）粘到顶部输入框，"
            "或点「浏览」后粘贴。带提取码就一起粘贴（?pwd=xxxx）。")

    # ---------- 内部请求 ----------

    def _headers(self, key: str, pwd: str = "") -> dict:
        ref = f"https://www.123pan.com/s/{key}" + (f"?pwd={pwd}" if pwd else "")
        return {"User-Agent": UA, "Referer": ref, "Origin": "https://www.123pan.com",
                "App-Version": "3", "platform": "web",
                "Content-Type": "application/json;charset=UTF-8", "Accept": "*/*"}

    async def _share_get(self, key: str, pwd: str, parent: str, cursor: str = "1",
                         page: int = 1) -> dict:
        params = {
            "limit": 100, "next": cursor, "orderBy": "share_id", "orderDirection": "desc",
            "shareKey": key, "SharePwd": pwd, "ParentFileId": parent, "Page": page,
        }
        r = await HTTP.get(f"{API}/b/api/share/get", params=params,
                           headers=self._headers(key, pwd))
        try:
            j = r.json()
        except Exception:
            raise PanError(f"分享信息接口返回异常: {r.text[:120]}")
        code = j.get("code")
        if code == 5103:
            raise PanError("提取码错误，或分享已失效/被删除")
        if code != 0:
            raise PanError(f"获取分享信息失败: {j.get('message') or code}")
        return j.get("data") or {}

    # ---------- 浏览 ----------

    async def list_dir(self, path: str) -> list[FileEntry]:
        if not path:
            raise PanError("把 123 云盘分享链接粘贴到顶部输入框即可浏览/下载")
        if path.startswith("http"):
            key, pwd = parse_share_url(path)
            parent = "0"
        else:
            key, pwd, rest = _unpack(path)
            parent = rest[0] if rest else "0"

        entries: list[FileEntry] = []
        cursor, page = "1", 1
        while page <= 20:
            data = await self._share_get(key, pwd, parent, cursor, page)
            items = data.get("InfoList") or []
            for f in items:
                fid = f.get("FileId")
                if f.get("Type") == 1:      # 目录
                    entries.append(FileEntry(fsid=_pack(key, pwd, fid), name=f.get("FileName", ""),
                                             size=0, is_dir=True))
                else:                        # 文件：把下载所需元数据一并塞进 fsid
                    entries.append(FileEntry(
                        fsid=_pack(key, pwd, fid, f.get("Size", 0), f.get("Etag", ""),
                                   f.get("S3KeyFlag", "")),
                        name=f.get("FileName", ""), size=int(f.get("Size", 0) or 0), is_dir=False))
            cursor = str(data.get("Next", "-1"))
            if cursor in ("-1", "", "None") or not items:
                break
            page += 1
        return entries

    # ---------- 取直链 ----------

    async def resolve(self, entry: FileEntry) -> DirectLink:
        key, pwd, fields = _unpack(entry.fsid)
        if len(fields) < 4:
            raise PanError("该条目不是文件（或分享信息不完整），请重新浏览一次")
        fid, size, etag, s3 = fields[0], fields[1], fields[2], fields[3]
        body = {"ShareKey": key, "FileID": int(fid), "S3keyFlag": s3,
                "Size": int(size or 0), "Etag": etag}
        ts = str(int(time.time() * 1000))
        signed = encode123("/b/api/v2/share/download/info", "web", "3", ts)
        r = await HTTP.post(f"{API}/b/api/v2/share/download/info{signed}", json=body,
                            headers=self._headers(key, pwd))
        try:
            j = r.json()
        except Exception:
            raise PanError(f"取直链接口返回异常: {r.text[:120]}")
        code = j.get("code")
        if code == 5112:
            raise PanError("分享方「提取流量包」已耗尽——123 云盘对免费分享的下载流量限制，"
                           "任何工具都下不了，需分享方续流量包")
        if code == 20101:
            raise PanError("该分享要求登录后才能下载（在 123 云盘里配 token 后再试）")
        if code != 0:
            raise PanError(f"取直链失败: {j.get('message') or code}")

        data = j.get("data") or {}
        path = data.get("downloadPath") or ""
        if not path:
            raise PanError("接口未返回下载地址")
        prefixes = [d.get("prefix", "").rstrip("/") for d in (data.get("dispatchList") or [])]
        prefixes = [p for p in prefixes if p] or ["https://www.123pan.com"]
        headers = {"User-Agent": UA, "Referer": f"https://www.123pan.com/s/{key}"}

        # 多条 CDN 线路：逐条探一下（1 字节 Range），挑第一条通的，避免用户下到死线路
        for pfx in prefixes[:3]:
            url = pfx + (path if path.startswith("/") else "/" + path)
            try:
                probe = await HTTP.get(url, headers={**headers, "Range": "bytes=0-0"})
                if probe.status_code in (200, 206):
                    return DirectLink(url=url, headers=headers, expires_in=3600)
            except Exception:
                continue
        url = prefixes[0] + (path if path.startswith("/") else "/" + path)
        return DirectLink(url=url, headers=headers, expires_in=3600)
