from __future__ import annotations

import json

from .config import Config
from .store import Store


class ScanRootsError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


class ScanRoots:
    """SQLite-backed manual scan selection; configured roots remain the access allowlist."""

    def __init__(self, store: Store, config: Config):
        self.store = store
        self.config = config

    def state(self, db) -> dict:
        row = db.execute("SELECT value FROM settings WHERE key='scan_roots'").fetchone()
        if row:
            saved = json.loads(row[0])
            return {"enabled_paths": [str(root.path) for root in self.config.roots
                                      if str(root.path) in saved["enabled_paths"]],
                    "revision": saved["revision"]}
        return {"enabled_paths": [str(root.path) for root in self.config.roots], "revision": 0}

    def update(self, enabled_paths: list[str], expected_revision: int) -> dict:
        configured = {str(root.path) for root in self.config.roots}
        if not enabled_paths:
            raise ScanRootsError("请至少选择一个扫描目录")
        if any(path not in configured for path in enabled_paths):
            raise ScanRootsError("扫描目录必须是已配置的库存根目录")
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self.state(db)
            if current["revision"] != expected_revision:
                raise ScanRootsError("扫描目录设置已被其他页面修改，请刷新后重试", 409)
            state = {"enabled_paths": [str(root.path) for root in self.config.roots
                                       if str(root.path) in enabled_paths],
                     "revision": current["revision"] + 1}
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('scan_roots',?)",
                       (json.dumps(state, ensure_ascii=False),))
        return state
