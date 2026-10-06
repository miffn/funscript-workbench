"""Small immutable inventory snapshots: subsequent pages only slice memory."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
from threading import Lock
from time import monotonic
from typing import Callable
from uuid import uuid4


class InventoryCacheError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Snapshot:
    token: str
    query: tuple
    revision: int
    created_at: float
    items: tuple[str, ...]
    stats: str
    last_scan: str

    def page(self, number: int, size: int) -> dict:
        # JSON values keep snapshots immutable without copying all earlier pages.
        return {'items': [json.loads(item) for item in self.items[(number - 1) * size:number * size]],
                'total': len(self.items), 'page': number, 'page_size': size,
                'stats': json.loads(self.stats), 'last_scan': json.loads(self.last_scan),
                'snapshot_id': self.token, 'inventory_revision': self.revision}


class InventoryCache:
    def __init__(self, capacity: int = 16, ttl_seconds: float = 30 * 60,
                 clock: Callable[[], float] = monotonic):
        self.capacity, self.ttl_seconds, self.clock = capacity, ttl_seconds, clock
        self._snapshots: OrderedDict[str, Snapshot] = OrderedDict()
        self._lock = Lock()

    def _expire(self, current: float):
        for token, snapshot in list(self._snapshots.items()):
            if current - snapshot.created_at >= self.ttl_seconds:
                del self._snapshots[token]

    def get(self, token: str, query: tuple) -> Snapshot:
        with self._lock:
            self._expire(self.clock())
            snapshot = self._snapshots.get(token)
            if snapshot is None:
                raise InventoryCacheError(410, '库存快照已过期，请重新读取库存')
            if snapshot.query != query:
                raise InventoryCacheError(422, '库存快照与当前筛选或排序不一致')
            self._snapshots.move_to_end(token)
            return snapshot

    def get_or_create(self, query: tuple, revision: int, build: Callable[[], dict]) -> Snapshot:
        # ponytail: serialize builds under one lock; split per-query only if contention warrants it.
        with self._lock:
            current = self.clock()
            self._expire(current)
            for snapshot in self._snapshots.values():
                if snapshot.query == query and snapshot.revision == revision:
                    self._snapshots.move_to_end(snapshot.token)
                    return snapshot
            result = build()
            encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            snapshot = Snapshot(uuid4().hex, query, revision, self.clock(),
                                tuple(encode(item) for item in result['items']),
                                encode(result['stats']), encode(result['last_scan']))
            self._snapshots[snapshot.token] = snapshot
            while len(self._snapshots) > self.capacity:
                self._snapshots.popitem(last=False)
            return snapshot
