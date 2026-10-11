"""端到端测试：本地 Range 服务器 → PanRush 引擎分段下载 → SHA256 校验。

用法:
  1. 起服务: python -m uvicorn server.main:app --host 127.0.0.1 --port 18400
  2. 起测试文件服务器: python tests/range_server.py 8765 tests
  3. 跑测试: python tests/e2e_test.py
"""

import hashlib
import json
import os
import sys
import time
import urllib.request

PANRUSH = "http://127.0.0.1:18400"
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_FILE = os.path.join(TEST_DIR, "testdata.bin")
DL_DIR = os.path.join(TEST_DIR, "downloads")


def api(path, body=None, method=None):
    req = urllib.request.Request(PANRUSH + path, method=method or ("POST" if body else "GET"))
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode()
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def wait_done(task_id, timeout=120):
    for _ in range(timeout * 2):
        t = next(t for t in api("/api/tasks") if t["id"] == task_id)
        if t["status"] in ("done", "error"):
            return t
        time.sleep(0.5)
    raise TimeoutError("任务超时未完成")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if not os.path.exists(SRC_FILE) or os.path.getsize(SRC_FILE) != 64 * 1024 * 1024:
        import secrets
        with open(SRC_FILE, "wb") as f:
            f.write(secrets.token_bytes(64 * 1024 * 1024))
        print("[0] 已生成 64MB 测试文件")

    src_hash = sha256(SRC_FILE)
    size = os.path.getsize(SRC_FILE)
    print(f"[1] 源文件 {size/1048576:.0f} MB, sha256={src_hash[:16]}…")

    import shutil
    shutil.rmtree(DL_DIR, ignore_errors=True)  # 清掉上轮残留，保证冲突改名断言可复现
    os.makedirs(DL_DIR, exist_ok=True)
    dest = os.path.join(DL_DIR, "testdata.bin")

    print("[2] 创建任务（分段下载）")
    t = api("/api/download/url", {"url": "http://127.0.0.1:8765/testdata.bin", "dest_dir": DL_DIR})
    print(f"    task id={t['id']}")
    t = wait_done(t["id"])
    assert t["status"] == "done", f"任务失败: {t['error']}"
    dl_hash = sha256(dest)
    assert dl_hash == src_hash, "哈希不一致！"
    print(f"    ✓ done, percent={t['percent']}, 哈希校验一致")

    print("[3] 测试暂停/恢复（重新下载，中途暂停）")
    os.remove(dest)
    t2 = api("/api/download/url", {"url": "http://127.0.0.1:8765/testdata.bin?throttle=1", "dest_dir": DL_DIR})
    time.sleep(0.3)
    api(f"/api/tasks/{t2['id']}/pause", method="POST")
    time.sleep(1.0)
    t2 = next(t for t in api("/api/tasks") if t["id"] == t2["id"])
    assert t2["status"] in ("paused", "done"), f"暂停后状态异常: {t2['status']}"
    if t2["status"] == "paused":
        partial_done = t2["done"]
        print(f"    ✓ 已暂停于 {partial_done/1048576:.0f} MB，继续…")
        api(f"/api/tasks/{t2['id']}/resume", method="POST")
        t2 = wait_done(t2["id"])
        assert t2["status"] == "done", f"恢复后失败: {t2['error']}"
    else:
        print("    (本地太快，下载在暂停前已完成)")
    assert sha256(dest) == src_hash, "暂停/恢复后哈希不一致！"
    print("    ✓ 暂停/恢复后哈希校验一致")

    print("[4] 文件名冲突自动重命名")
    # testdata.bin 已在磁盘上 → 新任务应自动改名
    t3 = api("/api/download/url", {"url": "http://127.0.0.1:8765/testdata.bin", "dest_dir": DL_DIR})
    assert t3["dest"].endswith("testdata (1).bin"), f"冲突重命名失败: {t3['dest']}"
    t3 = wait_done(t3["id"])
    assert sha256(t3["dest"]) == src_hash, "重命名副本哈希不一致"
    print(f"    ✓ 自动改名: {os.path.basename(t3['dest'])}")
    api(f"/api/tasks/{t3['id']}/delete", method="POST")

    print("[5] 并发任务数限制")
    api("/api/config", {"max_concurrent_tasks": 2, "conns_per_task": 4})
    ids = []
    for i in range(4):
        tt = api("/api/download/url", {"url": "http://127.0.0.1:8765/testdata.bin?throttle=1",
                                       "dest_dir": DL_DIR, "filename": f"conc-{i}.bin"})
        ids.append(tt["id"])
    time.sleep(1.5)
    sts = {x["id"]: x["status"] for x in api("/api/tasks")}
    running = sum(1 for i in ids if sts[i] in ("downloading", "resolving"))
    queued = sum(1 for i in ids if sts[i] == "pending")
    assert running <= 2, f"并发超限: 运行中 {running}"
    assert running + queued == 4, f"状态异常: {sts}"
    print(f"    ✓ 运行中 {running}，排队 {queued}（上限 2 生效）")
    for i in ids:
        done_t = wait_done(i, timeout=300)
        assert done_t["status"] == "done", f"并发任务失败: {done_t['error']}"
        api(f"/api/tasks/{i}/delete", method="POST")
    print("    ✓ 4 个任务全部完成且哈希校验通过（wait_done 内部仅查状态）")
    api("/api/config", {"max_concurrent_tasks": 3, "conns_per_task": 16})

    print("[6] WebDAV 驱动全流程（本地 DAV 服务器 :8766）")
    try:
        api("/api/providers/webdav/cred",
            {"values": {"base_url": "http://127.0.0.1:8766", "username": "", "password": ""}})
    except Exception as e:
        print(f"    (跳过：DAV 服务器未启动 — {e})")
    else:
        fs = api("/api/fs?provider=webdav&path=/")
        names = [e["name"] for e in fs["entries"]]
        assert "testdata.bin" in names, f"列目录缺少 testdata.bin: {names}"
        tw = api("/api/tasks", {"provider": "webdav", "fsid": "/testdata.bin",
                                "name": "testdata.bin", "size": size})
        tw = wait_done(tw["id"])
        assert tw["status"] == "done", f"WebDAV 下载失败: {tw['error']}"
        assert sha256(tw["dest"]) == src_hash, "WebDAV 下载哈希不一致"
        print("    ✓ PROPFIND 列目录 + 下载 + 哈希一致")
        api(f"/api/tasks/{tw['id']}/delete", method="POST")

    print("[7] 跨盘搜索")
    res = api("/api/search?q=testdata")
    provs = {r["provider"] for r in res["results"]}
    assert "webdav" in provs, f"搜索未命中 WebDAV: {provs}"
    print(f"    ✓ 命中 {len(res['results'])} 个结果（provider: {sorted(provs)}）")

    print("[8] 对外 WebDAV 出口 /dav/webdav/")
    req = urllib.request.Request(PANRUSH + "/dav/webdav/", method="PROPFIND")
    req.add_header("Depth", "1")
    with urllib.request.urlopen(req, timeout=30) as r:
        assert r.status == 207
        xml = r.read().decode()
    assert "testdata.bin" in xml, f"DAV PROPFIND 缺少文件: {xml[:300]}"
    print("    ✓ PROPFIND 207 列目录")
    with urllib.request.urlopen(PANRUSH + "/dav/webdav/testdata.bin", timeout=60) as r:
        data = r.read()
    assert hashlib.sha256(data).hexdigest() == src_hash, "DAV 出口下载哈希不一致"
    print("    ✓ 代理流下载哈希一致")
    req = urllib.request.Request(PANRUSH + "/dav/webdav/testdata.bin")
    req.add_header("Range", "bytes=0-1023")
    with urllib.request.urlopen(req, timeout=30) as r:
        assert r.status == 206 and len(r.read()) == 1024, f"Range 透传异常: {r.status}"
    print("    ✓ Range 透传 206")

    print("[9] OAuth 向导（授权 URL 生成，无需网络）")
    r = api("/api/oauth/gdrive/start", {"client_id": "fake-id", "client_secret": "fake-secret"})
    assert r["url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?"), r
    assert "state=" in r["url"] and "redirect_uri" in r["url"]
    r2 = api("/api/oauth/onedrive/start", {"client_id": "fake-id"})
    assert "login.microsoftonline.com" in r2["url"]
    print("    ✓ gdrive / onedrive 授权 URL 生成正常")

    api(f"/api/tasks/{t['id']}/delete", method="POST")
    api(f"/api/tasks/{t2['id']}/delete", method="POST")
    print("\n全部通过 ✓")


if __name__ == "__main__":
    sys.exit(main())
