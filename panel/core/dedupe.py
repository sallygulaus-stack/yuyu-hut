"""Bounded event de-duplication for reconnecting connectors."""

from __future__ import annotations

from collections import OrderedDict
import threading
import time


class EventDeduper:
    def __init__(self, max_size: int = 4096, ttl: float = 24 * 3600):
        self.max_size = max(1, int(max_size))
        self.ttl = max(1.0, float(ttl))
        self._items: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.RLock()

    def _keys(self, event_id: str = "", message_id: str = "") -> list[str]:
        result = []
        for value in (event_id, message_id):
            key = str(value or "").strip()
            if key and key not in result:
                result.append(key)
        return result

    def seen(self, event_id: str = "", message_id: str = "") -> bool:
        keys = self._keys(event_id, message_id)
        if not keys:
            return False
        now = time.time()
        with self._lock:
            self._prune(now)
            if any(key in self._items for key in keys):
                for key in keys:
                    if key in self._items:
                        self._items.move_to_end(key)
                return True
            for key in keys:
                self._items[key] = now
                self._items.move_to_end(key)
            while len(self._items) > self.max_size:
                self._items.popitem(last=False)
            return False

    def remember(self, event_id: str = "", message_id: str = "") -> None:
        keys = self._keys(event_id, message_id)
        if not keys:
            return
        with self._lock:
            self._prune(time.time())
            for key in keys:
                self._items[key] = time.time()
                self._items.move_to_end(key)
            while len(self._items) > self.max_size:
                self._items.popitem(last=False)

    def _prune(self, now: float) -> None:
        cutoff = now - self.ttl
        while self._items:
            key, seen_at = next(iter(self._items.items()))
            if seen_at >= cutoff:
                break
            self._items.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
