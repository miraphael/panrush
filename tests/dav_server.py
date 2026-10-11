"""测试用 WebDAV 服务器：在 range_server 之上加 PROPFIND（Depth 0/1），只读。

供 PanRush webdav 驱动端到端测试：
  python tests/dav_server.py 8766 tests
"""

import os
import sys
import xml.sax.saxutils as sx
from functools import partial
from http.server import ThreadingHTTPServer

from range_server import RangeHandler


def xml_escape(s: str) -> str:
    return sx.escape(s, {'"': "&quot;"})


class DAVHandler(RangeHandler):
    def _entry_xml(self, full: str, name: str, size, is_dir: bool) -> str:
        rt = "<D:resourcetype><D:collection/></D:resourcetype>" if is_dir else "<D:resourcetype/>"
        cl = f"<D:getcontentlength>{size}</D:getcontentlength>" if size is not None else ""
        return (f"<D:response><D:href>{xml_escape(full)}</D:href>"
                f"<D:propstat><D:prop><D:displayname>{xml_escape(name)}</D:displayname>{cl}{rt}</D:prop>"
                f"<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>")

    def do_PROPFIND(self):
        path = self.translate_path(self.path.split("?")[0])
        if not os.path.isdir(path):
            self.send_error(404)
            return
        depth = self.headers.get("Depth", "1")
        parts = []
        # 目录自身（href 以 / 结尾）
        req = self.path.split("?")[0].rstrip("/")
        own_name = req.rsplit("/", 1)[-1] or ""
        parts.append(self._entry_xml(req + "/", own_name, None, True))
        if depth == "1":
            for name in sorted(os.listdir(path)):
                sub = os.path.join(path, name)
                href = (req + "/" + name) if req else "/" + name
                if os.path.isdir(sub):
                    parts.append(self._entry_xml(href, name, None, True))
                else:
                    parts.append(self._entry_xml(href, name, os.path.getsize(sub), False))
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                '<D:multistatus xmlns:D="DAV:">' + "".join(parts) + "</D:multistatus>").encode("utf-8")
        self.send_response(207)
        self.send_header("Content-Type", 'application/xml; charset="utf-8"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):  # 支持 HEAD 探测
        path = self.translate_path(self.path.split("?")[0])
        if os.path.isfile(path):
            self.send_response(200)
            self.send_header("Content-Length", str(os.path.getsize(path)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
        elif os.path.isdir(path):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error(404)


if __name__ == "__main__":
    handler = partial(DAVHandler, directory=sys.argv[2])
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), handler).serve_forever()
