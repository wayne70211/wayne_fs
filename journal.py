from disk import Disk
from layout import Superblock
from transaction import Transaction
import struct
from typing import List
from dataclasses import dataclass
from enum import Enum
from contextlib import contextmanager
from cache import PageCache

JOURNAL_SB_MAGIC = b"WAYNE_JOURNAL_SB"
JOURNAL_MAGIC = b"WAYNE_JOURNAL"
JOURNAL_SIZE = 1024

class JournalBlockType(Enum):
    BLOCK_TYPE_DESCRIPTOR = 1
    BLOCK_TYPE_METADATA = 2
    BLOCK_TYPE_COMMIT = 3

@dataclass
class JournalSuperblock:
    magic: int
    start_block: int
    num_blocks: int
    head: int
    tail: int
    last_tid: int

    FORMAT = "<16sIIIII"

    def pack(self) -> bytes:
        return struct.pack(self.FORMAT, self.magic, self.start_block, self.num_blocks, self.head, self.tail, self.last_tid)
    
    @classmethod
    def unpack(cls, data: bytes) -> "JournalSuperblock":
        magic, start_block, num_blocks, head, tail, last_tid = struct.unpack(cls.FORMAT, data[:struct.calcsize(cls.FORMAT)])

        if magic != JOURNAL_SB_MAGIC:
            raise ValueError("Invalid journal superblock magic")

        return cls(magic, start_block, num_blocks, head, tail, last_tid)

@dataclass
class JournalHeader:
    magic: int
    block_type: int
    tid: int

    FORMAT = "<13sII"

    def pack(self) -> bytes:
        return struct.pack(self.FORMAT, self.magic, self.block_type, self.tid)
    
    @classmethod
    def unpack(cls, data: bytes) -> "JournalHeader":
        magic, block_type, tid = struct.unpack(cls.FORMAT, data[:struct.calcsize(cls.FORMAT)])

        if magic != JOURNAL_MAGIC:
            raise ValueError("Invalid journal header magic")
        
        return cls(magic, block_type, tid)

@dataclass
class DescriptorBlock:
    header: JournalHeader
    num_blocks: int
    final_block_addr: List[int]

    FORMAT = "<I" 
    ADDR_FORMAT = "<I"

    def pack(self) -> bytes:
        data = bytearray()
        data += self.header.pack()
        data += struct.pack(self.FORMAT, self.num_blocks)
        for addr in self.final_block_addr:
            data += struct.pack(self.ADDR_FORMAT, addr)
        return bytes(data)
    
    @classmethod
    def unpack(cls, data: bytes) -> "DescriptorBlock":
        data_ptr = 0
        header_size = struct.calcsize(JournalHeader.FORMAT)
        header = struct.unpack(JournalHeader.FORMAT, data[data_ptr:data_ptr+header_size])
        data_ptr += header_size
        num_blocks = struct.unpack(cls.FORMAT, data[data_ptr:data_ptr+struct.calcsize(cls.FORMAT)])
        data_ptr += struct.calcsize(cls.FORMAT)
        final_block_addr = []
        addr_size = struct.calcsize(cls.ADDR_FORMAT)

        for addr in range(num_blocks):
            final_block_addr.append(struct.unpack(cls.ADDR_FORMAT, data[data_ptr:data_ptr+addr_size]))
            data_ptr += addr_size

        return cls(header, num_blocks, final_block_addr)
    
@dataclass
class CommitBlock:
    header: JournalHeader

    def pack(self) -> bytes:
        return self.header.pack()

    @classmethod
    def unpack(cls, data: bytes) -> "CommitBlock":
        return cls(JournalHeader.unpack(data))


"""
Journal

One Transaction
-------------------------
|  Descriptor Block.    |
|        Data Block.    |
|        Data Block.    |
|        Data Block.    |
|      Commit Block.    |
-------------------------
"""


class Journal():
    def __init__(self, disk: Disk, sb: Superblock, page_cache: PageCache):
        self.disk = disk
        self.main_sb = sb
        self.page_cache = page_cache

        self.journal_area_start = sb.journal_area_start
        self.journal_area_total_blocks = sb.journal_area_total_blocks
        
        try:
            raw_journal_sb = self.disk.read_block(self.journal_area_start)
            self.journal_sb = JournalSuperblock.unpack(raw_journal_sb)
            print("Journal loaded successfully.")
        except (ValueError, struct.error):
            print("Failed to load journal, initializing a new one.")
            log_start_block = self.journal_area_start + 1

            self.journal_sb = JournalSuperblock(
                magic=JOURNAL_SB_MAGIC, 
                start_block=log_start_block, 
                num_blocks=self.journal_area_total_blocks - 1,  # 1 block is used as superblock
                head=log_start_block, 
                tail=log_start_block,
                last_tid=0)
            raw_journal_sb = self.journal_sb.pack()
            self.disk.write_block(self.journal_area_start, raw_journal_sb.ljust(self.main_sb.block_size, b'\x00'))

        self.next_tid = self.journal_sb.last_tid + 1

    def _get_next_log_block(self, current_block: int) -> int:
        # (日誌紀錄區的相對位置 + 1) % 總數量
        next_relative_pos = ((current_block - self.journal_sb.start_block) + 1) % self.journal_sb.num_blocks
        return self.journal_sb.start_block + next_relative_pos

    @contextmanager
    def begin(self):
        self.next_tid += 1
        tx = Transaction(self, self.next_tid)

        try:
            yield tx
        finally:
            print(f"Transaction {tx.tid} finished, committing")
            self.commit(tx)

    def commit(self, tx: Transaction):
        # if no write buffer, return
        if not tx.write_buffer and not tx.ordered_data_blocks:
            return
        
        # JBD2
        if tx.ordered_data_blocks:
            for block_addr in tx.ordered_data_blocks:
                print(f"[Ordered Mode] Flushing {len(tx.ordered_data_blocks)} dependent data blocks...")
                if self.page_cache.is_cached(block_addr):
                    page = self.page_cache.get(block_addr)
                    if page.dirty:
                        self.disk.write_block(block_addr, bytes(page.data))
                        page.dirty = False

            self.disk.fsync()
        
        num_blocks = len(tx.write_buffer)

        # descriptor block [header, num_blocks, final_block_addr[:]]
        curr_block_no = self.journal_sb.tail
        desc_header = JournalHeader(magic=JOURNAL_MAGIC, block_type=JournalBlockType.BLOCK_TYPE_DESCRIPTOR.value, tid=tx.tid)
        desc_block = DescriptorBlock(header=desc_header, num_blocks=num_blocks, final_block_addr=[key for key in tx.write_buffer])        
        self.disk.write_block(curr_block_no, desc_block.pack().ljust(self.main_sb.block_size, b'\x00'))

        # data block
        for final_block_addr in tx.write_buffer:
            curr_block_no = self._get_next_log_block(curr_block_no)
            block_type, block_data = tx.write_buffer[final_block_addr]
            self.disk.write_block(curr_block_no, block_data)
        
        # commit block
        curr_block_no = self._get_next_log_block(curr_block_no)
        commit_header = JournalHeader(magic=JOURNAL_MAGIC, block_type=JournalBlockType.BLOCK_TYPE_COMMIT.value, tid=tx.tid)
        commit_block = CommitBlock(header=commit_header)
        self.disk.write_block(curr_block_no, commit_block.pack().ljust(self.main_sb.block_size, b'\x00'))

        # update superblock
        curr_block_no = self._get_next_log_block(curr_block_no)
        self.journal_sb.tail = curr_block_no
        self.journal_sb.last_tid = tx.tid
        self.disk.write_block(self.main_sb.journal_area_start, self.journal_sb.pack().ljust(self.main_sb.block_size, b'\x00'))

        # replay
        for final_block_addr in tx.write_buffer:
            block_type, block_data = tx.write_buffer[final_block_addr]
            self.disk.write_block(final_block_addr, block_data)

        # update superblock
        self.journal_sb.head = self.journal_sb.tail
        self.disk.write_block(self.main_sb.journal_area_start, self.journal_sb.pack().ljust(self.main_sb.block_size, b'\x00'))


    def recover(self):
        print("Starting journal recovery")
        head = self.journal_sb.head
        tail = self.journal_sb.tail

        if head == tail:
            print("Journal is clean. No recovery needed.")
            return
        
        curr_block_no = head
        transactions_to_replay = {}

        while curr_block_no != tail:
            try:
                data = self.disk.read_block(curr_block_no)
                header = JournalHeader.unpack(data)
                if header.block_type == JournalBlockType.BLOCK_TYPE_DESCRIPTOR.value:
                    desc_block = DescriptorBlock.unpack(data)
                    
                    for final_addr in desc_block.final_block_addr:
                        curr_block_no = self._get_next_log_block(curr_block_no)
                        transactions_to_replay[desc_block.header.tid] = (final_addr, self.disk.read_block(curr_block_no))

                elif header.block_type == JournalBlockType.BLOCK_TYPE_COMMIT.value:
                    commit_block = CommitBlock(header)
                    if commit_block.header.tid in transactions_to_replay:
                        print(f"  - Found commit for TID={commit_block.header.tid}. Replaying transaction.")
                        for final_addr, data in transactions_to_replay[commit_block.header.tid]:
                            self.disk.write_block(final_addr, data)

                        del transactions_to_replay[commit_block.header.tid]
                        self.journal_sb.head = self._get_next_log_block(curr_block_no)

            except (ValueError, struct.error) as e:
                print(f"  - Error reading block {curr_block_no}: {e}. Stopping recovery scan.")
                break

            curr_block_no = self._get_next_log_block(curr_block_no)
        
        print("Recovery finished. Cleaning journal by setting head = tail.")
        self.journal_sb.head = self.journal_sb.tail
        self.disk.write_block(self.journal_area_start, self.journal_sb.pack().ljust(self.main_sb.block_size, b'\x00'))