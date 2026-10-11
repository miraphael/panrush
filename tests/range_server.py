"""测试用支持 Range 的静态文件服务器（python http.server 不支持 Range，专为此写）。"""

import os
import re
import sys
import time
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler


class RangeHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()
        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404)
            return None
        size = os.fstat(f.fileno()).st_size
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d+)-(\d*)$", rng or "")
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
            end = min(end, size - 1)
            f.seek(start)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self._range = (start, end)
            return f
        self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return f

    def copyfile(self, src, dst):
        rng = getattr(self, "_range", None)
        throttle = "throttle" in (self.path.split("?") + [""])[1]
        if not rng:
            return super().copyfile(src, dst)
        start, end = rng
        remaining = end - start + 1
        while remaining > 0:
            chunk = src.read(min(16384, remaining))
            if not chunk:
                break
            dst.write(chunk)
            remaining -= len(chunk)
            if throttle:
                time.sleep(0.008)  # ≈2 MB/s，给暂停测试留出窗口


def serve(port, directory):
    handler = partial(RangeHandler, directory=directory)
    ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


if __name__ == "__main__":
    serve(int(sys.argv[1]), sys.argv[2])
