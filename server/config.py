import json
import os

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 18400,
    "dest_dir": os.path.join(os.path.expanduser("~"), "Downloads", "PanRush"),
    "conns_per_task": 16,
    "min_segment_mb": 16,
    "global_speed_limit_kbps": 0,  # 0 = 不限
}


class Config:
    def __init__(self, path: str):
        self.path = path
        self.data = dict(DEFAULTS)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.data.update(json.load(f))
            except Exception:
                pass
        os.makedirs(self.dest_dir, exist_ok=True)

    def __getattr__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
