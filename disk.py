#!/usr/bin/env python3
import os

class Disk:
    def __init__(self, path, block_size=None):
        self.path = path
        self.fd = os.open(path, os.O_RDWR)
        self.block_size = block_size or 4096  # may be updated by layout.Superblock.load()

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def read_at(self, offset, length):
        return os.pread(self.fd, length, offset)

    def write_at(self, offset, data):
        return os.pwrite(self.fd, data, offset)

    def read_block(self, blkno):
        off = blkno * self.block_size
        return self.read_at(off, self.block_size)

    def write_block(self, blkno, data):
        assert len(data) == self.block_size, "must write full block"
        off = blkno * self.block_size
        return self.write_at(off, data)

    def fsync(self):
        os.fsync(self.fd)
