
class PageCache:
    def __init__(self):
        self._cache = {}

    def get(self, block_addr):
        return self._cache.get(block_addr)
        
    def put(self, block_addr, data):
        self._cache[block_addr] = data

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