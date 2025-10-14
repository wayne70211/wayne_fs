from disk import Disk
from layout import Superblock
from transaction import Transaction
import struct
from dataclasses import dataclass
from enum import Enum
from contextlib import contextmanager

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
    num_blocks: int
    FORMAT = "<I"

    def pack(self) -> bytes:
        return struct.pack(self.FORMAT, self.num_blocks)
    


@dataclass
class CommitBlock:
    header: JournalHeader


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
    def __init__(self, disk: Disk, sb: Superblock):
        self.disk = disk
        self.main_sb = sb

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
                magic=JOURNAL_MAGIC, 
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

    def recover(self):
        print("Starting journal recovery")
        head = self.journal_sb.head
        tail = self.journal_sb.tail

        if head == tail:
            print("Journal is clean. No recovery needed.")
            return
        
        print(f"Scanning log from head={head} to tail={tail}")
        transactions_to_replay = {}
        
        current = head
        while current != tail:
            try:
                raw_block = self.disk.read_block(current)
                header = JournalHeader.unpack(raw_block)
                print(f"  - Reading block {current}: TID={header.tid}, Type={header.block_type}")
                if header.block_type == JournalBlockType.BLOCK_TYPE_DESCRIPTOR.value:
                    desc_content_offset = struct.calcsize(JournalHeader.FORMAT)
                    num_blocks, = struct.unpack_from("<I", raw_block, desc_content_offset)

                    final_addrs = []
                    addr_offset = desc_content_offset + struct.calcsize("<I")
                    for _ in range(num_blocks):
                        addr, = struct.unpack_from("<I", raw_block, addr_offset)
                        final_addrs.append(addr)
                        addr_offset += struct.calcsize("<I")

                    metadata = {}
                    log_ptr = self._get_next_log_block(current)
                    for addr in final_addrs:
                        metadata[addr] = self.disk.read_block(log_ptr)
                        log_ptr = self._get_next_log_block(log_ptr)
                    
                    transactions_to_replay[header.tid] = (final_addrs, metadata)
                elif header.block_type == JournalBlockType.BLOCK_TYPE_COMMIT.value:
                    if header.tid in transactions_to_replay:
                        print(f"  - Found commit for TID={header.tid}. Replaying transaction.")
                        final_addrs, metadata = transactions_to_replay[header.tid]

                        for addr in final_addrs:
                            print(f"    - REPLAY: Writing block for final addr {addr}")
                            self.disk.write_block(addr, metadata[addr])
                        
                        del transactions_to_replay[header.tid]
                        
                        new_head = current
                        new_head = self._get_next_log_block(new_head)
                        self.journal_sb.head = new_head
            except (ValueError, struct.error) as e:
                print(f"  - Error reading block {current}: {e}. Stopping recovery scan.")
                break

            current = self._get_next_log_block(current)
        
        print("Recovery finished. Cleaning journal by setting head = tail.")
        self.journal_sb.head = self.journal_sb.tail
        self.disk.write_block(self.journal_area_start, self.journal_sb.pack().ljust(self.main_sb.block_size, b'\x00'))
        
    def commit(self, tx: Transaction):
        if not tx.write_buffer:
            return
        
        # Descriptor Block
        print(f" Pack JournalHeader tid = {tx.tid}, block_type = {JournalBlockType.BLOCK_TYPE_DESCRIPTOR.value} magic = {JOURNAL_MAGIC}")

        desc_header = JournalHeader(magic=JOURNAL_MAGIC, block_type=JournalBlockType.BLOCK_TYPE_DESCRIPTOR.value, tid=tx.tid)
        final_addrs = list(tx.write_buffer.keys())
        desc_block_content = DescriptorBlock(num_blocks=len(final_addrs)).pack()

        for addr in final_addrs:
            desc_block_content += struct.pack("<I", addr)

        full_desc_block = desc_header.pack() + desc_block_content
        full_desc_block = full_desc_block.ljust(self.main_sb.block_size, b'\x00')
        current_log_tail = self.journal_sb.tail
        self.disk.write_block(current_log_tail, full_desc_block)


        current_log_tail = self._get_next_log_block(current_log_tail)
        # Metadata Block
        for addr in final_addrs:            
            block_type, block_data= tx.write_buffer[addr] 
            block_data = block_data.ljust(self.main_sb.block_size, b'\x00')
            print(f"[DEBUG] addr = {addr} block_type = {block_type} block_data = {len(block_data)}")
            self.disk.write_block(current_log_tail, block_data)
            current_log_tail = self._get_next_log_block(current_log_tail)


        # Commit Block
        commit_header = JournalHeader(magic=JOURNAL_MAGIC, block_type=JournalBlockType.BLOCK_TYPE_COMMIT.value, tid=tx.tid)
        full_commit_block = commit_header.pack().ljust(self.main_sb.block_size, b'\x00')

        self.disk.write_block(current_log_tail, full_commit_block)
        current_log_tail = self._get_next_log_block(current_log_tail)

        # Update Journal SuperBlock
        self.journal_sb.tail = current_log_tail
        self.journal_sb.last_tid = tx.tid
        self.disk.write_block(self.main_sb.journal_area_start, self.journal_sb.pack().ljust(self.main_sb.block_size, b'\x00'))

        # Checkpoint
        print(f"  - tx {tx.tid}: Checkpointing... Writing to final locations.")
        for final_addr, (block_type, block_data) in tx.write_buffer.items():
            print(f"    - Writing {block_type} to block {final_addr}")
            self.disk.write_block(final_addr, block_data)

        print(f"  - tx {tx.tid}: Checkpoint complete. Advancing journal head pointer.")
        self.journal_sb.head = self.journal_sb.tail
        self.disk.write_block(self.main_sb.journal_area_start, self.journal_sb.pack().ljust(self.main_sb.block_size, b'\x00'))

    