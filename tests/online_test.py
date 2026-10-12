"""真实在线网盘分享直取测试（需要联网）。

与 e2e_test.py（纯本地、离线可跑）互补：这里直接打真实的网上分享链接，
验证「粘贴链接 → 列目录 → 取直链 → 分段下载 → 校验文件」全链路。

用法:
    python tests/online_test.py            # 跑默认的公开分享链接
    python tests/online_test.py <分享链接>  # 指定链接

注意：网盘分享会过期、分享方流量包会耗尽，链接失效时脚本会如实报错而不是假装通过。
"""

import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE = os.environ.get("PANRUSH_BASE", "http://127.0.0.1:18400")
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本机直连，绕开系统代理

# 123 云盘公开分享（ArduinoDroid APK，163MB）
PAN123_SHARE = "https://www.123pan.com/s/iaKtVv-ICECd.html"


def api(path, body=None):
    req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET")
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode()
    with _opener.open(req, timeout=60) as r:
        return json.loads(r.read())


def head_hash(path, n=256 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(n))
    return h.hexdigest()[:16]


def test_pan123share(share: str):
    print(f"\n===== 123云盘分享直取: {share} =====")
    route = api("/api/download/url", {"url": share})
    assert route.get("kind") == "share", f"未路由到分享驱动: {route}"
    print(f"[1] 粘贴链接 → 智能路由到 {route['provider']} ({route['name']}) ✓")

    fs = api("/api/fs?provider=pan123share&path=" + urllib.parse.quote(share, safe=""))
    entries = fs["entries"]
    assert entries, "分享内容为空"
    for e in entries:
        print(f"    {'📁' if e['is_dir'] else '📄'} {e['name']}  {e['size'] / 1048576:.1f}MB")
    print(f"[2] 列目录 ✓ ({len(entries)} 项)")

    f = next((e for e in entries if not e["is_dir"]), None)
    if not f:
        print("[3] 分享里只有文件夹，跳过单文件下载测试")
        return
    t = api("/api/tasks", {"provider": "pan123share", "fsid": f["fsid"],
                           "name": f["name"], "size": f["size"]})
    tid = t["id"]
    print(f"[3] 创建任务 {tid} → {t['dest']}")
    peak = 0.0
    for i in range(300):
        time.sleep(2)
        tt = next(x for x in api("/api/tasks") if x["id"] == tid)
        peak = max(peak, tt["speed"])
        if tt["status"] in ("done", "error"):
            break
    if tt["status"] != "done":
        raise SystemExit(f"[4] ✗ 下载失败: {tt['error']}")
    real = os.path.getsize(tt["dest"])
    with open(tt["dest"], "rb") as fh:
        magic = fh.read(4)
    assert real == tt["size"], f"字节数不符 {real} != {tt['size']}"
    print(f"[4] ✓ 下载完成 {real / 1048576:.1f}MB，峰值 {peak / 1048576:.1f}MB/s，"
          f"文件头 {magic!r}，头部哈希 {head_hash(tt['dest'])}")
    try:
        os.remove(tt["dest"])
    except OSError:
        pass


if __name__ == "__main__":
    links = sys.argv[1:] or [PAN123_SHARE]
    for link in links:
        if "123pan" in link or "123278" in link or "123865" in link or "123684" in link:
            test_pan123share(link)
        else:
            print(f"跳过未支持的链接: {link}")
    print("\n在线测试全部通过 ✓")
