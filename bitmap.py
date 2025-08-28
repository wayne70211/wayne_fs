#!/usr/bin/env python3
# Minimal bitmap helper (bytearray-based). You will extend this later.
from disk import Disk
from layout import Superblock

class BlockBitmap:
    def __init__(self, disk: Disk, sb: Superblock):
        self.disk = disk
        self.sb = sb
        self.nblocks = self.sb.total_blocks
        total_bytes = (self.nblocks + 7)//8
        buf = bytearray()
        for i in range(sb.free_bitmap_blocks):
            buf += self.disk.read_block(sb.free_bitmap_start + i)
        self.buf = bytearray(buf[:total_bytes])  # 截到有效長度

    def _byte_bit(self, idx):
        return idx // 8, idx % 8

    def test(self, idx):
        b, bit = self._byte_bit(idx)
        return (self.buf[b] >> bit) & 1

    def set(self, idx):
        b, bit = self._byte_bit(idx)
        self.buf[b] |= (1 << bit)

    def clear(self, idx):
        b, bit = self._byte_bit(idx)
        self.buf[b] &= ~(1 << bit)

    def find_free(self, start_idx: int = 0):
        n = self.nblocks
        i = start_idx
        while i < n:
            if not self.test(i):
                return i
            i += 1
        return -1

        

