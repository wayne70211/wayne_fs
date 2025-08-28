#!/usr/bin/env python3
# Minimal bitmap helper (bytearray-based). You will extend this later.
from disk import Disk

class Bitmap:
    def __init__(self, data: bytearray):
        self.data = data

    def _byte_bit(self, idx):
        return idx // 8, idx % 8

    def test(self, idx):
        b, bit = self._byte_bit(idx)
        return (self.data[b] >> bit) & 1

    def set(self, idx):
        b, bit = self._byte_bit(idx)
        self.data[b] |= (1 << bit)

    def clear(self, idx):
        b, bit = self._byte_bit(idx)
        self.data[b] &= ~(1 << bit)

