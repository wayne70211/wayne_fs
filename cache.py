
from typing import Dict

class CachedPage:
    def __init__(self, data: bytes):
        self.data = bytearray(data)
        self.dirty = False


class PageCache:
    def __init__(self):
        self._cache: Dict[int, CachedPage] = {}

    def get(self, block_addr) -> CachedPage:
        return self._cache[block_addr]
        
    def put(self, block_addr, data: bytes):
        self._cache[block_addr] = CachedPage(data)

    def is_cached(self, block_addr) -> bool:
        return block_addr in self._cache
    
    def get_dirty_pages(self):
        return [(block_addr, page) for block_addr, page in self._cache.items() if page.dirty]

class DentryCache:
    def __init__(self):
        self._cache = {}

    def get(self, path: str):
        return self._cache.get(path)
        
    def put(self, path: str, ino: int):
        self._cache[path] = ino

    def remove(self, path: str):
        if self.get(path) is None:
            return
        del self._cache[path]