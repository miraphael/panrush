"""任务持久化（SQLite）：重启后任务不丢，分段进度每秒落盘。"""

import json
import sqlite3
import threading
import time
import uuid


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Task:
    def __init__(self, **kw):
        self.id: str = kw.get("id") or _new_id()
        self.provider_id: str = kw.get("provider_id", "")
        self.fsid: str = kw.get("fsid", "")
        self.name: str = kw.get("name", "")
        self.dest: str = kw.get("dest", "")
        self.size: int = kw.get("size", -1)
        self.status: str = kw.get("status", "pending")  # pending/resolving/downloading/paused/done/error
        self.done: int = kw.get("done", 0)
        self.error: str = kw.get("error", "")
        self.segments: list = kw.get("segments") or []  # [{start,end,pos}]
        self.link_url: str = kw.get("link_url", "")
        self.link_headers: dict = kw.get("link_headers") or {}
        self.link_expires: float = kw.get("link_expires", 0)
        self.created_at: float = kw.get("created_at") or time.time()
        # 运行时字段（不落盘）
        self.speed: int = 0
        self._last_done: int = self.done
        self.pause_event = None

    def to_row(self) -> dict:
        return {
            "id": self.id, "provider_id": self.provider_id, "fsid": self.fsid,
            "name": self.name, "dest": self.dest, "size": self.size,
            "status": self.status, "done": self.done, "error": self.error,
            "segments": json.dumps(self.segments), "link_url": self.link_url,
            "link_headers": json.dumps(self.link_headers), "link_expires": self.link_expires,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, r) -> "Task":
        return cls(
            id=r["id"], provider_id=r["provider_id"], fsid=r["fsid"], name=r["name"],
            dest=r["dest"], size=r["size"], status=r["status"], done=r["done"],
            error=r["error"], segments=json.loads(r["segments"]), link_url=r["link_url"],
            link_headers=json.loads(r["link_headers"]), link_expires=r["link_expires"],
            created_at=r["created_at"],
        )

    def to_api(self) -> dict:
        pct = round(self.done * 100 / self.size, 1) if self.size > 0 else 0
        return {
            "id": self.id, "provider_id": self.provider_id, "name": self.name,
            "dest": self.dest, "size": self.size, "done": self.done,
            "status": self.status, "speed": self.speed, "error": self.error,
            "percent": pct, "segments": len(self.segments),
        }


class TaskStore:
    def __init__(self, db_path: str):
        self._lock = threading.Lock()
        self._con = sqlite3.connect(db_path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, provider_id TEXT, fsid TEXT, name TEXT, dest TEXT,
            size INTEGER, status TEXT, done INTEGER, error TEXT, segments TEXT,
            link_url TEXT, link_headers TEXT, link_expires REAL, created_at REAL)""")
        self._con.commit()

    def save(self, t: Task):
        r = t.to_row()
        with self._lock:
            self._con.execute(
                """INSERT INTO tasks(id, provider_id, fsid, name, dest, size, status, done, error,
                   segments, link_url, link_headers, link_expires, created_at)
                   VALUES(:id,:provider_id,:fsid,:name,:dest,:size,:status,:done,:error,
                   :segments,:link_url,:link_headers,:link_expires,:created_at)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status, done=excluded.done,
                   error=excluded.error, segments=excluded.segments, link_url=excluded.link_url,
                   link_headers=excluded.link_headers, link_expires=excluded.link_expires""",
                r,
            )
            self._con.commit()

    def save_progress(self, t: Task):
        with self._lock:
            self._con.execute(
                "UPDATE tasks SET done=?, segments=?, status=? WHERE id=?",
                (t.done, json.dumps(t.segments), t.status, t.id),
            )
            self._con.commit()

    def load_all(self) -> list:
        with self._lock:
            rows = self._con.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [Task.from_row(r) for r in rows]

    def delete(self, task_id: str):
        with self._lock:
            self._con.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            self._con.commit()
