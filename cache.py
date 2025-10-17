
class PageCache:
    def __init__(self):
        self._cache = {}

    def get(self, block_addr):
        return self._cache.get(block_addr)
        
    def put(self, block_addr, data):
        self._cache[block_addr] = data
