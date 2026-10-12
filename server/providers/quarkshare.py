"""夸克网盘「分享链接」直取驱动（第 13 个驱动）。

2026 实测流程：
- 免登录列目录: POST /share/sharepage/token  → stoken
                GET  /share/sharepage/detail  → 文件列表（pdir_fid 逐层进子目录）
- 免登录取直链: POST /file/download  body={fids, pwd_id, stoken, fids_token}
                → 直链域名形如 dl-guest-*.drive.quark.cn（guest = 官方给未登录访客的通道）
- 超过免登录体积上限（实测 43.8MB 可下、54.2MB 返回 code 23018 size limit）：
  走「转存到自己网盘再下载」，这一步需要登录态 —— 复用「夸克网盘」驱动已配置的
  Cookie（配一次即可），没有 Cookie 时给出明确提示。

如实标注：免登录浏览 + 免登录小文件直取是实测通过的；转存路径需要账号 Cookie，
没 Cookie 时无法验证，所以这里只在配置了 Cookie 后启用。
"""

import asyncio
import re

from . import HTTP
from .base import DirectLink, FileEntry, PanError, PanProvider, register

API = "https://drive-pc.quark.cn/1/clouddrive"
SAVE_API = "https://drive-h.quark.cn/1/clouddrive"
COMMON = {"pr": "ucpro", "fr": "pc"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

SIZE_LIMIT_HINT = ("该文件超过夸克免登录下载的体积上限（约 50MB）。"
                   "到「夸克网盘」驱动里配置一次 Cookie，PanRush 会自动把它转存到你自己的"
                   "网盘再下载（超大文件也能满速）。")


def parse_share_url(url: str) -> tuple[str, str]:
    """从分享链接取 (pwd_id, 提取码)。"""
    m = re.search(r"/s/([A-Za-z0-9]+)", url)
    if not m:
        raise PanError("无法从链接中识别夸克分享 ID")
    pid = m.group(1)
    mp = re.search(r"(?:pwd|password)=([A-Za-z0-9]{1,8})", url, re.I)
    return pid, (mp.group(1) if mp else "")


def _pack(pid: str, pwd: str, *fields) -> str:
    return f"{pid}#{pwd}|" + "|".join(str(f) for f in fields)


def _unpack(fsid: str) -> tuple[str, str, list[str]]:
    head, _, rest = fsid.partition("|")
    pid, _, pwd = head.partition("#")
    return pid, pwd, rest.split("|") if rest else []


@register
class QuarkShareProvider(PanProvider):
    id = "quarkshare"
    name = "夸克网盘(分享直取)"
    tier = "full"
    root = ""
    cred_optional = True          # 没配 Cookie 也能浏览 / 下小文件
    cred_fields = [
        {"key": "cookie", "label": "Cookie（可选）", "secret": True, "optional": True,
         "placeholder": "大文件转存需要；配过一次即可"},
    ]
    help = ("免登录吃夸克分享链接：粘贴 pan.quark.cn/s/xxx 到顶部输入框即可浏览、下载。"
            "约 50MB 以内免登录直取；更大的文件需要登录态转存——配一次夸克 Cookie"
            "（或先在「夸克网盘」里配好，这里会自动复用）。")

    # ---------- 凭据 ----------

    def _cookie(self) -> str:
        c = self.cred or {}
        if c.get("cookie"):
            return c["cookie"]
        # 复用「夸克网盘」驱动的 Cookie：同一个账号，配一次就够
        if self.cred_store:
            q = self.cred_store.get("quark") or {}
            if q.get("cookie"):
                return q["cookie"]
        return ""

    def _headers(self) -> dict:
        h = {"User-Agent": UA, "Referer": "https://pan.quark.cn/",
             "Origin": "https://pan.quark.cn", "Content-Type": "application/json"}
        ck = self._cookie()
        if ck:
            h["Cookie"] = ck
        return h

    def _sdid(self) -> str:
        """夸克访客下载通道（dl-guest-*.drive.quark.cn）要求带上接口下发的会话 Cookie，
        否则直链返回 412 Precondition Failed。实测需要 __sdid 和 __pugs 两个都带上；
        引擎下载时用的是自己的客户端，所以要把它们显式放进请求头。"""
        parts = []
        for c in HTTP.cookies.jar:
            if "quark" in (c.domain or ""):
                parts.append(f"{c.name}={c.value}")
        return "; ".join(parts)

    def _download_headers(self) -> dict:
        h = {"User-Agent": UA, "Referer": "https://pan.quark.cn/"}
        ck = self._cookie()
        session = self._sdid()
        if session:
            ck = f"{ck}; {session}" if ck else session
        if ck:
            h["Cookie"] = ck
        return h

    async def login(self, cred: dict) -> dict:
        cookie = (cred or {}).get("cookie", "")
        if not cookie:
            return cred            # Cookie 是可选项，留空即免登录模式
        r = await HTTP.get(f"{API}/member", params=COMMON,
                           headers={"Cookie": cookie, "Referer": "https://pan.quark.cn/",
                                    "User-Agent": UA})
        data = r.json()
        if not (data.get("data") or {}).get("member"):
            raise PanError("Cookie 无效或已过期")
        return cred

    # ---------- 分享接口 ----------

    async def _token(self, pid: str, pwd: str) -> str:
        r = await HTTP.post(f"{API}/share/sharepage/token", params=COMMON,
                            json={"pwd_id": pid, "passcode": pwd}, headers=self._headers())
        j = r.json()
        if j.get("code") != 0:
            msg = j.get("message") or str(j.get("code"))
            if "提取码" in msg:
                raise PanError("该分享需要提取码：把链接连 ?pwd=xxxx 一起粘贴即可")
            raise PanError(f"分享不可用：{msg}")
        return j["data"]["stoken"]

    async def _detail(self, pid: str, stoken: str, pdir: str = "0", page: int = 1) -> dict:
        params = {**COMMON, "pwd_id": pid, "stoken": stoken, "pdir_fid": pdir,
                  "_page": page, "_size": 100, "_fetch_banner": 1,
                  "_fetch_share": 1, "_fetch_total": 1}
        r = await HTTP.get(f"{API}/share/sharepage/detail", params=params,
                           headers=self._headers())
        j = r.json()
        if j.get("code") != 0:
            raise PanError(f"列目录失败：{j.get('message') or j.get('code')}")
        return j.get("data") or {}

    # ---------- 浏览 ----------

    async def list_dir(self, path: str) -> list[FileEntry]:
        if not path:
            raise PanError("把夸克网盘分享链接粘贴到顶部输入框即可浏览 / 下载")
        if path.startswith("http"):
            pid, pwd = parse_share_url(path)
            pdir = "0"
        else:
            pid, pwd, rest = _unpack(path)
            pdir = rest[0] if rest else "0"

        stoken = await self._token(pid, pwd)
        entries: list[FileEntry] = []
        page = 1
        while page <= 20:
            data = await self._detail(pid, stoken, pdir, page)
            lst = data.get("list") or []
            for f in lst:
                if f.get("dir"):
                    entries.append(FileEntry(fsid=_pack(pid, pwd, f["fid"]),
                                             name=f.get("file_name", ""), size=0, is_dir=True))
                else:
                    entries.append(FileEntry(
                        fsid=_pack(pid, pwd, pdir, f["fid"]), name=f.get("file_name", ""),
                        size=int(f.get("size", 0) or 0), is_dir=False))
            total = int(data.get("total") or 0)
            if not lst or len(lst) < 100 or (total and len(entries) >= total):
                break
            page += 1
        return entries

    # ---------- 取直链 ----------

    async def _download_url(self, pid: str, stoken: str, fid: str, fid_token: str) -> str:
        """返回直链；返回空串表示「超过免登录体积上限，需要转存」。"""
        body = {"fids": [fid], "pwd_id": pid, "stoken": stoken}
        if fid_token:
            body["fids_token"] = [fid_token]
        r = await HTTP.post(f"{API}/file/download", params=COMMON, json=body,
                            headers=self._headers())
        j = r.json()
        code = j.get("code")
        if code == 0:
            data = j.get("data") or []
            if data and data[0].get("download_url"):
                return data[0]["download_url"]
            raise PanError("接口未返回下载地址")
        msg = j.get("message") or ""
        if code == 23018 or "size limit" in msg:
            return ""
        if code == 31001:
            raise PanError("夸克要求登录（Cookie 已失效），请重新配置 Cookie")
        raise PanError(f"取直链失败：{msg or code}")

    async def resolve(self, entry: FileEntry) -> DirectLink:
        pid, pwd, rest = _unpack(entry.fsid)
        if len(rest) < 2:
            raise PanError("该条目不是文件，请重新浏览一次")
        pdir, fid = rest[0], rest[1]
        stoken = await self._token(pid, pwd)
        data = await self._detail(pid, stoken, pdir)
        item = next((x for x in (data.get("list") or []) if x.get("fid") == fid), None)
        if not item:
            raise PanError("分享里找不到这个文件（可能已被分享者删除）")
        fid_token = item.get("share_fid_token", "")

        url = await self._download_url(pid, stoken, fid, fid_token)
        if not url:
            url = await self._resolve_via_save(pid, stoken, fid, fid_token)
        return DirectLink(url=url, headers=self._download_headers(), expires_in=600)

    # ---------- 大文件：转存到自己网盘再取链（需要 Cookie） ----------

    async def _resolve_via_save(self, pid: str, stoken: str, fid: str, fid_token: str) -> str:
        if not self._cookie():
            raise PanError(SIZE_LIMIT_HINT)

        # 1) 取默认转存目录（通常是「来自：分享」）
        r = await HTTP.get(f"{API}/share/sharepage/dir",
                           params={**COMMON, "uc_param_str": "", "aver": "1"},
                           headers=self._headers())
        j = r.json()
        if j.get("code") != 0:
            raise PanError(f"获取转存目录失败：{j.get('message') or j.get('code')}")
        to_pdir = (j.get("data") or {}).get("pdir_fid")
        if not to_pdir:
            raise PanError("获取转存目录失败：pdir_fid 为空")

        # 2) 发起转存任务（服务端 search_exit 会去重，不会重复占空间）
        body = {"pwd_id": pid, "stoken": stoken, "pdir_fid": "0", "to_pdir_fid": to_pdir,
                "scene": "link", "fid_list": [fid], "pdir_save_all": False}
        if fid_token:
            body["fid_token_list"] = [fid_token]
        r = await HTTP.post(f"{SAVE_API}/share/sharepage/save",
                            params={**COMMON, "uc_param_str": ""}, json=body,
                            headers=self._headers())
        j = r.json()
        if j.get("code") != 0:
            raise PanError(f"转存失败：{j.get('message') or j.get('code')}")
        task_id = (j.get("data") or {}).get("task_id")
        if not task_id:
            raise PanError("转存响应里没有任务 ID")

        # 3) 轮询任务
        saved_fid = ""
        for i in range(20):
            await asyncio.sleep(1)
            r = await HTTP.get(f"{API}/task",
                               params={**COMMON, "uc_param_str": "", "task_id": task_id,
                                       "retry_index": i},
                               headers=self._headers())
            j = r.json()
            if j.get("code") != 0:
                raise PanError(f"查询转存任务失败：{j.get('message') or j.get('code')}")
            tdata = j.get("data") or {}
            status = tdata.get("status")
            if status == 2:
                fids = ((tdata.get("save_as") or {}).get("save_as_top_fids") or [])
                saved_fid = fids[0] if fids else ""
                break
            if status == 3:
                raise PanError("转存任务失败（网盘空间可能已满）")
        if not saved_fid:
            raise PanError("转存超时，请稍后重试")

        # 4) 用转存后的 fid 取直链
        url = await self._download_url(pid, stoken, saved_fid, "")
        if not url:
            raise PanError("转存成功但仍拿不到直链，请稍后重试")
        return url
