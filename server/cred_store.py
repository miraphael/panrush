"""凭据存储：Windows DPAPI 加密后落 SQLite，非 Windows 退化为 base64 明文标记。"""

import base64
import ctypes
import json
import os
import sqlite3
import threading

_ENTROPY = b"PanRush-cred-v1"
_PLAIN_PREFIX = "plain:"


class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_protect(data: bytes) -> bytes:
    bin_ = ctypes.create_string_buffer(data, len(data))
    bin_blob = _BLOB(len(data), ctypes.cast(bin_, ctypes.POINTER(ctypes.c_char)))
    out_blob = _BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(bin_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    in_blob = _BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out_blob = _BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


class CredStore:
    def __init__(self, db_path: str):
        self._lock = threading.Lock()
        self._con = sqlite3.connect(db_path, check_same_thread=False)
        self._con.execute("CREATE TABLE IF NOT EXISTS credentials (pid TEXT PRIMARY KEY, blob TEXT NOT NULL)")
        self._con.commit()

    def set(self, pid: str, cred: dict):
        raw = json.dumps(cred, ensure_ascii=False).encode("utf-8")
        try:
            stored = _dpapi_protect(_ENTROPY + raw).hex()
        except Exception:
            stored = _PLAIN_PREFIX + base64.b64encode(raw).decode()
        with self._lock:
            self._con.execute(
                "INSERT INTO credentials(pid, blob) VALUES(?, ?) ON CONFLICT(pid) DO UPDATE SET blob=excluded.blob",
                (pid, stored),
            )
            self._con.commit()

    def get(self, pid: str):
        with self._lock:
            row = self._con.execute("SELECT blob FROM credentials WHERE pid=?", (pid,)).fetchone()
        if not row:
            return None
        stored = row[0]
        if stored.startswith(_PLAIN_PREFIX):
            raw = base64.b64decode(stored[len(_PLAIN_PREFIX):])
            return json.loads(raw.decode("utf-8"))
        raw = _dpapi_unprotect(bytes.fromhex(stored))
        if not raw.startswith(_ENTROPY):
            raise ValueError("credential entropy mismatch")
        return json.loads(raw[len(_ENTROPY):].decode("utf-8"))

    def delete(self, pid: str):
        with self._lock:
            self._con.execute("DELETE FROM credentials WHERE pid=?", (pid,))
            self._con.commit()

    def has(self, pid: str) -> bool:
        with self._lock:
            row = self._con.execute("SELECT 1 FROM credentials WHERE pid=?", (pid,)).fetchone()
        return row is not None
