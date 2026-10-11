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

    dest = os.path.join(DL_DIR, "testdata.bin")
    if os.path.exists(dest):
        os.remove(dest)
    os.makedirs(DL_DIR, exist_ok=True)

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

    api(f"/api/tasks/{t['id']}/delete", method="POST")
    api(f"/api/tasks/{t2['id']}/delete", method="POST")
    print("\n全部通过 ✓")


if __name__ == "__main__":
    sys.exit(main())
