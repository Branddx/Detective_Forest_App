"""Caché en memoria con tiempo de vida (TTL), segura para hilos."""

import threading
import time


class TTLCache:
    def __init__(self, maxsize: int = 1024):
        self._data = {}
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def get(self, key):
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            expires, value = item
            if expires < time.time():
                self._data.pop(key, None)
                return None
            return value

    def set(self, key, value, ttl_seconds: float):
        with self._lock:
            if len(self._data) >= self._maxsize:
                # descarta las entradas más antiguas
                for k in sorted(self._data, key=lambda k: self._data[k][0])[: self._maxsize // 4]:
                    self._data.pop(k, None)
            self._data[key] = (time.time() + ttl_seconds, value)
        return value

    def clear(self):
        with self._lock:
            self._data.clear()


cache = TTLCache()
