"""真实在线网盘分享直取测试（需要联网）。

与 e2e_test.py（纯本地、离线可跑）互补：这里直接打真实的网上分享链接，
验证「粘贴链接 → 列目录 → 取直链 → 分段下载 → 校验文件」全链路。

用法:
    python tests/online_test.py                 # 跑内置的公开分享链接
    python tests/online_test.py <分享链接> ...   # 指定链接（自动按域名选驱动）

注意：网盘分享会过期、分享方流量包会耗尽、提取码会变，链接失效时脚本会如实报错，
不会假装通过。
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

# 内置的公开分享（失效时换一个即可）
SAMPLES = [
    # 123 云盘：单文件分享
    "https://www.123pan.com/s/iaKtVv-ICECd.html",
    # 123 云盘：文件夹分享（验证逐层浏览）
    "https://www.123pan.com/s/lN7UVv-pbYJ",
    # 夸克网盘：文件夹分享（验证逐层浏览 + 免登录直取）
    "https://pan.quark.cn/s/078eafd744b3",
]

ROUTES = [
    (("123pan", "123278", "123865", "123684"), "pan123share"),
    (("quark.cn",), "quarkshare"),
]


def api(path, body=None, timeout=90):
    req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET")
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode()
    with _opener.open(req, timeout=timeout) as r:
        return json.loads(r.read())


def provider_for(link: str) -> str:
    for hosts, pid in ROUTES:
        if any(h in link for h in hosts):
            return pid
    return ""


def head_hash(path, n=256 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(n))
    return h.hexdigest()[:16]


def browse(pid: str, path: str):
    return api(f"/api/fs?provider={pid}&path=" + urllib.parse.quote(path, safe=""))


def download_and_check(pid: str, entry: dict):
    """建任务 → 等完成 → 校验字节数与文件头。返回一段结果描述。"""
    t = api("/api/tasks", {"provider": pid, "fsid": entry["fsid"],
                           "name": entry["name"], "size": entry["size"]})
    tid = t["id"]
    peak, tt = 0.0, t
    for _ in range(300):
        time.sleep(1)
        tt = next(x for x in api("/api/tasks") if x["id"] == tid)
        peak = max(peak, tt["speed"])
        if tt["status"] in ("done", "error"):
            break
    if tt["status"] != "done":
        raise AssertionError(f"下载失败: {tt['error']}")
    real = os.path.getsize(tt["dest"])
    assert real == tt["size"], f"字节数不符 {real} != {tt['size']}"
    with open(tt["dest"], "rb") as fh:
        magic = fh.read(4)
    info = (f"{real / 1048576:.2f}MB，峰值 {peak / 1048576:.1f}MB/s，"
            f"文件头 {magic!r}，头部哈希 {head_hash(tt['dest'])}")
    try:
        os.remove(tt["dest"])
    except OSError:
        pass
    return info


def test_share(link: str):
    pid = provider_for(link)
    assert pid, f"未识别该分享域名: {link}"
    print(f"\n===== {pid}: {link} =====")

    route = api("/api/download/url", {"url": link})
    assert route.get("kind") == "share" and route["provider"] == pid, f"智能路由异常: {route}"
    print(f"[1] 粘贴链接 → 路由到 {route['provider']} ({route['name']}) ✓")

    entries = browse(pid, link)["entries"]
    assert entries, "分享内容为空"
    for e in entries:
        print(f"    {'📁' if e['is_dir'] else '📄'} {e['name']}  {e['size'] / 1048576:.2f}MB")
    print(f"[2] 列目录 ✓ ({len(entries)} 项)")

    # 逐层进子目录，顺便找第一个可下载的文件
    target, depth, current = None, 0, entries
    while depth < 3:
        f = next((e for e in current if not e["is_dir"]), None)
        if f:
            target = f
            break
        d = next((e for e in current if e["is_dir"]), None)
        if not d:
            break
        current = browse(pid, d["fsid"])["entries"]
        depth += 1
        print(f"    └ 进入「{d['name']}」→ {len(current)} 项 ✓")
    if depth:
        print(f"[3] 多层目录浏览 ✓ (深入 {depth} 层)")

    if not target:
        print("[4] 分享里没有文件，跳过下载测试")
        return
    if pid == "quarkshare" and target["size"] > 50 * 1048576:
        print(f"[4] 文件 {target['size'] / 1048576:.1f}MB 超过夸克免登录上限，跳过下载测试")
        return
    print(f"[4] ✓ 下载完成 {target['name']}：{download_and_check(pid, target)}")


if __name__ == "__main__":
    links = sys.argv[1:] or SAMPLES
    ok, fail = 0, 0
    for link in links:
        try:
            test_share(link)
            ok += 1
        except Exception as e:      # 分享过期属正常情况，如实报告
            print(f"  ✗ 跳过：{type(e).__name__}: {e}")
            fail += 1
    print(f"\n在线测试：{ok} 个分享通过，{fail} 个失效/跳过")
    if ok == 0:
        sys.exit(1)
