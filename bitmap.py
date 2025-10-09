#!/usr/bin/env python3
from disk import Disk
from layout import Superblock
from transaction import Transaction
from typing import Optional

class Bitmap:
    def __init__(self, disk: Disk, sb: Superblock, start_block: int, num_blocks: int, total_items: int, bitmap_type: str = "Default"):
        self.disk = disk
        self.sb = sb
        self.start_block = start_block
        self.num_blocks = num_blocks
        self.total_items = total_items
        self.bitmap_type = bitmap_type

        total_bytes = (total_items + 7) // 8
        buf = bytearray()
        for i in range(num_blocks):
            buf += self.disk.read_block(start_block + i)
        
        self.buf = bytearray(buf[:total_bytes])

    def _byte_bit(self, idx):
        if not 0 <= idx < self.total_items:
            raise IndexError(f"Bitmap index {idx} out of range (0-{self.total_items}")
        return idx // 8, idx % 8

    def is_set(self, idx: int) -> bool:
        b, bit = self._byte_bit(idx)
        return (self.buf[b] >> bit) & 1 == 1

    def set(self, idx: int):
        b, bit = self._byte_bit(idx)
        self.buf[b] |= (1 << bit)

    def clear(self, idx: int):
        b, bit = self._byte_bit(idx)
        self.buf[b] &= ~(1 << bit)

    def find_free_entry(self, start_idx: int = 0) -> int:
        i = start_idx
        while i < self.total_items:
            if not self.is_set(i):
                return i
            i += 1
        return -1
    
    def flush(self, tx: Optional[Transaction] = None):
        if tx:
            data_ptr = 0
            for i in range(self.num_blocks):
                print(f"[DEBUG] flush {self.bitmap_type} {i} buf size = {len(bytes(self.buf[data_ptr:data_ptr+self.sb.block_size]))}")
                tx.write(self.start_block + i, bytes(self.buf[data_ptr:data_ptr+self.sb.block_size]), self.bitmap_type)
                data_ptr += self.sb.block_size
        else:
            self.disk.write_at(self.start_block*self.sb.block_size, self.buf)

        
class InodeBitmap(Bitmap):
    def __init__(self, disk: Disk, sb: Superblock):
        super().__init__(disk, sb, sb.inode_bitmap_start, sb.inode_bitmap_blocks, sb.total_blocks, "Inode Bitmap")

    def find_free_inode(self, start_idx: int = 0) -> int:
        return self.find_free_entry(max(1, start_idx))
    
    def set_used(self, ino: int):
        self.set(ino)

    def clear_used(self, ino: int):
        self.clear(ino)


class BlockBitmap(Bitmap):
    def __init__(self, disk: Disk, sb: Superblock):
        super().__init__(disk, sb, sb.block_bitmap_start, sb.block_bitmap_blocks, sb.total_blocks, "Block Bitmap")

    def find_free_block(self, start_idx: int = 0) -> int:
        return self.find_free_entry(max(1, start_idx))
    
    def set_used(self, blk_idx: int):
        self.set(blk_idx)

    def clear_used(self, blk_idx: int):
        self.clear(blk_idx)