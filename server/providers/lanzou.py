"""蓝奏云分享直取驱动（无需登录，直接吃网上的分享链接）。

2026 新版结构适配：
- 域名归一化到 www.lanzoui.com（部分节点有 acw_sc__v2 反爬，镜像域名无）
- 单文件: 分享页 → /fn? 文件页 → wp_sign + ajaxdata → apifile ajaxfile.php → dom/file/url
- 文件夹: 分享页 → filemoreajax.php（fid/uid/puid/t/k 全部从页面 JS 提取）
"""

import logging
import os
import re

from . import HTTP
from .base import DirectLink, FileEntry, PanError, PanProvider, register

log = logging.getLogger("panrush.lanzou")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _browser_attempts() -> list:
    """无头浏览器的启动顺序：真 Chromium 优先，系统 Edge/Chrome 兜底。

    顺序很重要：实测系统 Edge 能"启动成功"但过不了蓝奏云 CDN 的 acw 挑战
    （页面会卡在挑战页），而 Playwright 自带的 chromium 能过。所以必须把
    Chromium 排在最前面，Edge/Chrome 只作为「机器上完全没有 Chromium」时的兜底。
    """
    attempts = [{}]                             # 内嵌 / Playwright 缓存的 Chromium
    attempts.append({"channel": "msedge"})      # 系统 Edge（Windows 自带）
    attempts.append({"channel": "chrome"})      # 系统 Chrome
    return attempts


def _normalize(url: str) -> str:
    """域名归一化到 www.lanzoui.com（该镜像无 acw 反爬挑战）。"""
    return re.sub(r"^(https?://)[^/]+", r"\1www.lanzoui.com", url, count=1)


def _parse_size(text: str) -> int:
    m = re.match(r"([\d.]+)\s*([KMG]?B?)", (text or "").strip(), re.I)
    if not m:
        return 0
    val = float(m.group(1))
    unit = m.group(2).upper()
    mult = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}.get(unit[:1], 1) if unit else 1
    return int(val * mult)


# acw_sc__v2 反爬：把 arg1 按位置表重排，再和固定掩码逐字节异或
_ACW_POS = [15, 35, 29, 24, 33, 16, 1, 38, 10, 9, 19, 31, 40, 27, 22, 23, 25,
            13, 6, 11, 39, 18, 20, 8, 14, 21, 32, 26, 2, 30, 7, 4, 17, 5, 3,
            28, 34, 37, 12, 36]
_ACW_MASK = "3000176000856006061501533003690027800375"


def _solve_acw(arg1: str) -> str:
    """解出 acw_sc__v2 的 cookie 值（等价于 out[z] = arg1[POS[z]-1]，再与掩码异或）。"""
    s = "".join(arg1[p - 1] for p in _ACW_POS)
    return "".join(format(int(s[i:i + 2], 16) ^ int(_ACW_MASK[i:i + 2], 16), "02x")
                   for i in range(0, 40, 2))


def _is_challenge(resp) -> bool:
    """判断响应是不是反爬/行为验证页，而不是文件本体。"""
    ct = (resp.headers.get("content-type") or "").lower()
    if not ct.startswith("text/"):
        return False
    head = resp.text[:6000]
    return "acw_sc__v2" in head or "down_r" in head or "arg1='" in head


def _parse_filemore_data(html: str) -> dict:
    """从分享页里抠出 filemoreajax 的请求体。

    蓝奏云会不定期改页面变量名（t/k 对应的 ibd1ox、_hbdqq 后来变成了 ibhc0h、_hfo2a），
    所以这里不硬编码变量名：直接把 `data : { ... }` 的键值对读出来——值是字面量就用字面量，
    是标识符就回页面里找 `var <标识符> = '<值>'`。
    """
    m = re.search(r"filemoreajax\.php\?file=(\d+)[^,]*,\s*data\s*:\s*\{(.*?)\}", html, re.S)
    if not m:
        return {}
    fid, body = m.group(1), m.group(2)
    out: dict = {"fid": fid}
    for key, val in re.findall(r"'([^']+)'\s*:\s*('[^']*'|[A-Za-z_$][\w$]*|\d+)", body):
        val = val.strip()
        if val.startswith("'"):
            out[key] = val.strip("'")
        elif val.isdigit():
            out[key] = val
        else:
            mv = (re.search(rf"\b{re.escape(val)}\s*=\s*'([^']*)'", html)
                  or re.search(rf"\b{re.escape(val)}\s*=\s*(\d+)", html))
            out[key] = mv.group(1) if mv else ""
    return out


@register
class LanzouProvider(PanProvider):
    id = "lanzou"
    name = "蓝奏云(分享直取)"
    tier = "experimental"
    root = ""
    cred_fields = []
    help = ("直接粘贴蓝奏云分享链接（单文件如 lanzouw.com/xxx，文件夹 /s/xxx 或新版短链）。"
            "无需登录；蓝奏云本身不限速。")

    async def _get_html(self, url: str, referer: str = ""):
        headers = {"User-Agent": UA}
        if referer:
            headers["Referer"] = referer
        r = await HTTP.get(url, headers=headers)
        if "acw_sc__v2" in r.text:
            # 该节点挂了反爬挑战：镜像域名重试（算法已验证与页面一致，但掩码常量随版本变，
            # 用镜像域名绕开比追算法更稳）
            alt = _normalize(url)
            if alt != url:
                r = await HTTP.get(alt, headers=headers)
        r.raise_for_status()
        return r

    # ---------- 文件夹 ----------

    async def list_dir(self, path: str) -> list[FileEntry]:
        url = _normalize(path)
        r = await self._get_html(url)
        html = r.text
        params = _parse_filemore_data(html)
        fid = str(params.get("fid") or "")
        if not fid:
            raise PanError("不是文件夹分享链接（或已改版）。单文件链接请直接粘贴下载。")

        entries = []
        for pg in range(1, 11):
            body = dict(params)
            body["pg"] = pg
            resp = await HTTP.post(
                f"https://www.lanzoui.com/filemoreajax.php?file={fid}",
                data=body,
                headers={"User-Agent": UA, "Referer": url,
                         "X-Requested-With": "XMLHttpRequest"})
            j = resp.json()
            if j.get("zt") != 1:
                if pg == 1:
                    raise PanError(f"文件夹列表失败: {j.get('info')}")
                break
            batch = j.get("text") or []
            for f in batch:
                entries.append(FileEntry(
                    fsid=f"https://www.lanzoui.com/{f['id']}",
                    name=f.get("name_all", f["id"]),
                    size=_parse_size(f.get("size", "")),
                ))
            if len(batch) < 60:
                break
        return entries

    # ---------- 单文件 ----------

    async def resolve(self, entry) -> DirectLink:
        url = _normalize(entry.fsid)
        base = "https://www.lanzoui.com"

        r = await self._get_html(url)
        html = r.text
        if "/fn?" not in url:
            m = re.search(r'<iframe[^>]+src="(/fn\?[^"]+)"', html)
            if not m:
                raise PanError("不是有效的单文件分享链接（或已失效/需要密码）")
            page = base + m.group(1)
            r = await self._get_html(page, referer=url)
            html = r.text
        else:
            page = url

        m = re.search(r"wp_sign = '([^']+)'", html)
        if m:  # 新版 ajaxfile 流程
            wp_sign = m.group(1)
            ajaxdata = (re.search(r"ajaxdata = '([^']*)'", html) or [None, ""])[1]
            kdns = (re.search(r"kdns =(\d+)", html) or [None, "1"])[1]
            doms = re.findall(r"domain\d = '([^']+)'", html)
            if not doms:
                raise PanError("文件页解析失败（ajaxfile 地址未找到）")
            resp = await HTTP.post(doms[0], data={
                "action": "downprocess", "websignkey": ajaxdata, "signs": ajaxdata,
                "sign": wp_sign, "websign": "", "kd": kdns, "ves": 1,
            }, headers={"User-Agent": UA, "Referer": page})
        else:  # 旧版 ajaxm 流程
            ms = re.search(r"'sign':'([^']+)'", html) or re.search(r"'sign':([^,}]+)", html)
            if not ms:
                raise PanError("文件页解析失败（sign 未找到，蓝奏云可能已改版）")
            resp = await HTTP.post(f"{base}/ajaxm.php",
                                   data={"action": "downprocess",
                                         "sign": ms.group(1).strip().strip("'\""), "ves": 1},
                                   headers={"User-Agent": UA, "Referer": page})

        try:
            j = resp.json()
        except Exception:
            raise PanError(f"取链接口响应异常: {resp.text[:120]}")
        if str(j.get("zt")) != "1" or not j.get("dom") or not j.get("url"):
            raise PanError(f"取直链失败: {j.get('inf') or j}")

        dom = j["dom"]
        if re.search(r"kdns =0", html):  # 无 killdns 时 dom 固定
            dom = "https://developer2oss.lanzouc.com:661"
        link = dom + "/file/" + j["url"]

        # 新版 CDN 对脚本客户端有层层拦截：acw 挑战 → 行为验证页 → ajax.php 签名校验。
        # 按「纯算法 → 无头浏览器」的顺序尝试，拿到直链后必须实测能下到文件才算数。
        probe = None
        try:
            probe = await HTTP.get(link, headers={"User-Agent": UA, "Referer": page})
        except Exception:
            probe = None
        if probe is None or not _is_challenge(probe):
            return DirectLink(url=link, headers={"User-Agent": UA, "Referer": page},
                              expires_in=600)

        url = await self._cdn_via_http(link, page)
        if not url:
            url = await self._cdn_via_browser(link, page)
        if not url:
            raise PanError(
                "蓝奏云 CDN 的下载验证没能通过：它需要真正的 Chromium 内核，"
                "系统自带的 Edge / Chrome 会卡在反爬挑战页过不去。"
                "用完整版 exe（已内嵌 Chromium），或先执行 `playwright install chromium` 再试。")
        return DirectLink(url=url, headers={"User-Agent": UA, "Referer": page}, expires_in=600)

    # ---------- CDN 验证：纯算法解 acw ----------

    async def _cdn_via_http(self, link: str, page: str) -> str:
        """用算法解 acw 挑战 + 提交 ajax.php，全程不开浏览器。

        失败返回空串（例如服务端签名校验不过），交给浏览器兜底。
        """
        host = link.split("/")[2]
        try:
            r = await HTTP.get(link, headers={"User-Agent": UA, "Referer": page})
            m = re.search(r"arg1='([0-9A-Fa-f]{40})'", r.text)
            if m:
                HTTP.cookies.set("acw_sc__v2", _solve_acw(m.group(1)), domain=host)
                r = await HTTP.get(link, headers={"User-Agent": UA, "Referer": page})
            if "down_r" not in r.text:
                return ""
            ajax_url = link.split("?")[0].rsplit("/", 1)[0] + "/ajax.php"
            for el in ("1", "2", "3"):
                # file/sign 是一次性的，每次都要重新取页面
                r = await HTTP.get(link, headers={"User-Agent": UA, "Referer": page})
                mm = re.search(
                    r"'file'\s*:\s*'([^']+)'\s*,\s*'el'\s*:\s*el\s*,\s*'sign'\s*:\s*'([^']+)'",
                    r.text)
                if not mm:
                    return ""
                resp = await HTTP.post(ajax_url,
                                       data={"file": mm.group(1), "el": el, "sign": mm.group(2)},
                                       headers={"User-Agent": UA, "Referer": link,
                                                "X-Requested-With": "XMLHttpRequest"})
                try:
                    j = resp.json()
                except Exception:
                    continue
                if str(j.get("zt")) != "1":
                    continue
                url = str(j.get("url") or "")
                if not url or url.startswith("?") or "Error" in url:
                    continue            # 例如 ?SignError：服务端签名校验没过
                full = url if url.startswith("http") else f"https://{host}/file/{url.lstrip('/')}"
                if await self._is_real_file(full, page):
                    return full
        except Exception:
            return ""
        return ""

    async def _is_real_file(self, url: str, page: str) -> bool:
        """探测是不是真文件（而不是验证页）。"""
        try:
            host = url.split("/")[2]
            r = await HTTP.get(url, headers={"User-Agent": UA, "Referer": page, "Range": "bytes=0-0"})
            if _is_challenge(r):
                m = re.search(r"arg1='([0-9A-Fa-f]{40})'", r.text)
                if m:
                    HTTP.cookies.set("acw_sc__v2", _solve_acw(m.group(1)), domain=host)
                    r = await HTTP.get(url, headers={"User-Agent": UA, "Referer": page,
                                                     "Range": "bytes=0-0"})
            return r.status_code in (200, 206) and not _is_challenge(r)
        except Exception:
            return False

    # ---------- CDN 验证：无头浏览器兜底 ----------

    async def _cdn_via_browser(self, link: str, page: str) -> str:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return ""
        last_err = None
        async with async_playwright() as pw:
            browser = None
            for kw in _browser_attempts():
                try:
                    browser = await pw.chromium.launch(headless=True, **kw)
                    log.info("蓝奏云：用 %s 过 CDN 验证", kw.get("channel", "Chromium"))
                    break
                except Exception as e:
                    last_err = e
            if browser is None:
                log.warning("蓝奏云：找不到可用的浏览器（%s）", last_err)
                return ""
            ctx = await browser.new_context(user_agent=UA)
            pg = await ctx.new_page()
            try:
                await pg.goto(link, wait_until="commit", timeout=60000)
                await pg.wait_for_timeout(6000)     # 等 acw 挑战自动解算并重载
                try:
                    await pg.evaluate("typeof down_r === 'function' && down_r(2)")
                except Exception:
                    pass
                await pg.wait_for_timeout(5000)
                href = await pg.evaluate(
                    "() => { const a = document.querySelector('#go a'); return a ? a.href : ''; }")
                if not href:
                    return ""
                # 拿到的还得是「真文件」才算成功（可能仍是验证页 URL）
                if href.split("/file/")[0] == link.split("/file/")[0] and "/file/?" in href:
                    # 仍是 /file/?token 形式：用浏览器会话再探一次
                    try:
                        resp = await ctx.request.get(href, headers={"Referer": link})
                        ct = (resp.headers.get("content-type") or "")
                        if not ct.startswith("text/"):
                            return href
                    except Exception:
                        pass
                    return ""
                return href
            finally:
                await browser.close()
