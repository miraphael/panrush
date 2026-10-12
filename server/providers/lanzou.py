"""蓝奏云分享直取驱动（无需登录，直接吃网上的分享链接）。

2026 新版结构适配：
- 域名归一化到 www.lanzoui.com（部分节点有 acw_sc__v2 反爬，镜像域名无）
- 单文件: 分享页 → /fn? 文件页 → wp_sign + ajaxdata → apifile ajaxfile.php → dom/file/url
- 文件夹: 分享页 → filemoreajax.php（fid/uid/puid/t/k 全部从页面 JS 提取）
"""

import re

from . import HTTP
from .base import DirectLink, FileEntry, PanError, PanProvider, register

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


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
        m_fid = re.search(r"filemoreajax\.php\?file=(\d+)", html)
        if not m_fid:
            raise PanError("不是文件夹分享链接（或已改版）。单文件链接请直接粘贴下载。")
        fid = m_fid.group(1)
        uid = (re.search(r"'uid':'(\d*)'", html) or [None, "-1"])[1]
        puid = (re.search(r"'puid':'([^']*)'", html) or [None, ""])[1]
        t = (re.search(r"ibd1ox = '(\d+)'", html) or [None, "0"])[1]
        k = (re.search(r"_hbdqq = '([0-9a-f]+)'", html) or [None, ""])[1]
        wfs = (re.search(r"'webfoldersign':'([^']*)'", html) or [None, ""])[1]

        entries = []
        for pg in range(1, 11):
            resp = await HTTP.post(
                f"https://www.lanzoui.com/filemoreajax.php?file={fid}",
                data={"lx": 2, "fid": fid, "uid": uid, "puid": puid, "pg": pg,
                      "rep": 0, "t": t, "k": k, "up": 1, "vip": 0, "webfoldersign": wfs},
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

        # 新版 CDN(2026) 对非浏览器客户端返回"行为验证"页，纯 HTTP 过不去：
        # 检测到验证页时回退无头浏览器（Playwright）过验证拿真实直链
        try:
            probe = await HTTP.get(link, headers={"User-Agent": UA, "Referer": page})
            ct = probe.headers.get("content-type", "")
            if ct.startswith("text/") and ("down_r" in probe.text
                                           or "acw_sc__v2" in probe.text[:4000]):
                return await self._resolve_via_browser(link)
        except PanError:
            raise
        except Exception:
            pass
        return DirectLink(url=link, headers={"User-Agent": UA, "Referer": page},
                          expires_in=600)

    async def _resolve_via_browser(self, link: str) -> DirectLink:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise PanError("蓝奏云 CDN 行为验证需要 Playwright："
                           "pip install playwright && playwright install chromium")
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(user_agent=UA)
            page = await ctx.new_page()
            try:
                await page.goto(link, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(4000)   # 等挑战 JS 自动解算重载
                try:
                    await page.evaluate("typeof down_r === 'function' && down_r(2)")
                except Exception:
                    pass
                await page.wait_for_timeout(4000)
                href = await page.evaluate(
                    "() => { const a = document.querySelector('#go a'); return a ? a.href : ''; }")
                if not href:
                    raise PanError("浏览器过验证后仍未拿到直链（蓝奏云改版？）")
                return DirectLink(url=href, headers={"User-Agent": UA}, expires_in=600)
            finally:
                await browser.close()
