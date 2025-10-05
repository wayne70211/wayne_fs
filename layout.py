#!/usr/bin/env python3
import struct
from dataclasses import dataclass, field
from disk import Disk
from typing import List, Tuple
from enum import IntFlag
import time


MAGIC = b"WAYNE_FS"
SB_FMT = "<8sIIIIIIIIIII"
SB_SIZE = struct.calcsize(SB_FMT)
INODE_SIZE = 128

class InodeMode(IntFlag):
    # 檔案型別（高位 bits）
    S_IFMT   = 0xF000
    S_IFSOCK = 0xC000
    S_IFLNK  = 0xA000
    S_IFREG  = 0x8000
    S_IFBLK  = 0x6000
    S_IFDIR  = 0x4000
    S_IFCHR  = 0x2000
    S_IFIFO  = 0x1000
    S_INIT   = 0x0000

    # 權限位（低 9 bits，類似 0o755, 0o644）
    S_IRUSR = 0o400
    S_IWUSR = 0o200
    S_IXUSR = 0o100
    S_IRGRP = 0o040
    S_IWGRP = 0o020
    S_IXGRP = 0o010
    S_IROTH = 0o004
    S_IWOTH = 0o002
    S_IXOTH = 0o001

@dataclass
class Inode:
    mode: int = 0  # 4
    nlink: int = 0 # 4
    size: int = 0  # 8
    ctime: int = 0 # 8
    mtime: int = 0 # 8
    atime: int = 0 # 8
    direct: list = field(default_factory=lambda: [0]*12)

    def pack(self) -> bytearray:
        data = bytearray()
        data += struct.pack("<I", self.mode) 
        data += struct.pack("<I", self.nlink)
        data += struct.pack("<Q", self.size)
        data += struct.pack("<Q", self.ctime)
        data += struct.pack("<Q", self.mtime)
        data += struct.pack("<Q", self.atime)
        for idx in range(12):
            data += struct.pack("<I", self.direct[idx])

        return data

    @classmethod
    def empty(cls, mode: int):
        now = int(time.time())
        return cls(mode, 0, 0, now, now, now, [0]*12)

    @classmethod
    def unpack(cls, raw):
        off = 0
        mode = struct.unpack_from("<I", raw, off)[0]; off += 4
        nlink = struct.unpack_from("<I", raw, off)[0]; off += 4
        size = struct.unpack_from("<Q", raw, off)[0]; off += 8
        ctime = struct.unpack_from("<Q", raw, off)[0]; off += 8
        mtime = struct.unpack_from("<Q", raw, off)[0]; off += 8
        atime = struct.unpack_from("<Q", raw, off)[0]; off += 8
        direct = [0] * 12
        for idx in range(12):
            direct[idx] = struct.unpack_from("<I", raw, off)[0]; off += 4

        return cls(mode, nlink, size, ctime, mtime, atime, direct)


@dataclass
class Superblock:
    magic: int
    block_size: int
    total_blocks: int
    inode_count: int
    # --- Inode BMP ---
    inode_bitmap_start: int
    inode_bitmap_blocks: int
    # --- Block BMP ---
    block_bitmap_start: int
    block_bitmap_blocks: int
    # --- Inode Table ---
    inode_table_start: int
    inode_table_blocks: int
    # --- Data ---
    data_start: int

    @classmethod
    def load(cls, disk: Disk):
        raw = disk.read_block(0)
        fields = struct.unpack(SB_FMT, raw[:SB_SIZE])
        magic = fields[0]
        if magic != MAGIC:
            raise RuntimeError("Bad superblock magic; did you run mktoyfs.py?")
        ( magic,
          block_size,
          total_blocks,
          inode_count,
          inode_bitmap_start,
          inode_bitmap_blocks,
          block_bitmap_start,
          block_bitmap_blocks,
          inode_table_start,
          inode_table_blocks,
          data_start,
          _reserved) = fields
        disk.block_size = block_size  # sync disk view
        return cls(magic, block_size, total_blocks, inode_count,
                   inode_bitmap_start, inode_bitmap_blocks,
                   block_bitmap_start, block_bitmap_blocks,
                   inode_table_start, inode_table_blocks,
                   data_start)
      
class DictEnDecoder:
    def pack_dir(entries):
        """
        entries: List[Tuple[int, str]]  e.g. [(0, "."), (0, "..")]
        return: bytes
        """
        data = bytearray()
        for ino, name in entries:
            name_b = name.encode("utf-8")
            # Packed：ino, name_length, name
            entry_packed = struct.pack(f"<IH{len(name_b)}s", ino, len(name_b), name_b)
            data.extend(entry_packed)
        
        # Add all length
        header = struct.pack("<I", len(data))
        
        # DEBUG print
        print(f"[pack_dir] Total entries: {len(entries)}. Total data length: {len(data)}. Packed size: {len(header) + len(data)}")
        
        return bytes(header + data)

    @staticmethod
    def unpack_dir(raw: bytes) -> list[tuple[int, str]]:
        """
        raw: bytes of a directory file
        return: List[Tuple[int, str]]
        """
        print("\n--- [unpack_dir] Starting Directory Unpack ---")
        if not raw or len(raw) < 4:
            print("[unpack_dir] Error: Raw data is empty or too short for header.")
            return []

        try:
            # 1. Read all data length
            total_len, = struct.unpack_from("<I", raw, 0)
            print(f"[unpack_dir] Header reports total data length: {total_len} bytes.")
            
            if total_len == 0:
                print("[unpack_dir] Header reports zero length. Directory is empty.")
                return []
            
            # 2. Data handle
            effective_data_end = 4 + total_len
            if effective_data_end > len(raw):
                print(f"[unpack_dir] Warning: Reported length ({total_len}) is greater than available raw data ({len(raw)-4}). Truncating.")
                effective_data_end = len(raw)

            data_slice = raw[4:effective_data_end]
            print(f"[unpack_dir] Sliced effective data of length: {len(data_slice)} bytes.")

        except struct.error as e:
            print(f"[unpack_dir] Error reading header: {e}")
            return []


        out = []
        offset = 0
        entry_count = 1
        while offset < len(data_slice):
            print(f"\n[unpack_dir] --- Parsing Entry #{entry_count} at offset {offset} ---")
            try:
                header_size = struct.calcsize("<IH") # 6 bytes
                if offset + header_size > len(data_slice):
                    print(f"[unpack_dir] Error: Not enough data left for entry header. Remaining: {len(data_slice)-offset} bytes.")
                    break

                # 3. Read (inode, name length)
                ino, nlen = struct.unpack_from("<IH", data_slice, offset)
                print(f"[unpack_dir] Entry Header: ino={ino}, name_length={nlen}")
                
                # 4. Check valid
                if offset + header_size + nlen > len(data_slice):
                    print(f"[unpack_dir] Error: Name length ({nlen}) exceeds remaining data boundary.")
                    break

                # 5. Read file name
                name_bytes, = struct.unpack_from(f"<{nlen}s", data_slice, offset + header_size)
                name = name_bytes.decode("utf-8")
                print(f"[unpack_dir] Entry Data: name='{name}'")
                
                out.append((ino, name))
                
                # 6. Move next
                offset += header_size + nlen
                entry_count += 1
                
            except (struct.error, UnicodeDecodeError) as e:
                print(f"[unpack_dir] CRITICAL ERROR during entry parsing: {e}. Aborting.")
                break
        
        print(f"--- [unpack_dir] Finished Unpack. Found {len(out)} entries: {out} ---\n")
        return out

class InodeTable:
    def __init__(self, disk: Disk, sb: Superblock, inode_size: int = 128):
        self.disk = disk
        self.sb = sb
        self.inode_size = inode_size
        
    def __inode_offset(self, idx: int) -> int:
        return self.sb.inode_table_start * self.sb.block_size + idx * self.inode_size

    def read(self, ino: int) -> Inode:
        off = self.__inode_offset(ino)
        raw = self.disk.read_at(off, self.inode_size)
        return Inode.unpack(raw)

    def write(self, ino: int, inode: Inode):
        off = self.__inode_offset(ino)
        self.disk.write_at(off, inode.pack())    

@dataclass
class OpenFileState:
    ino: int
    flags: int
    offset: int

def ceil_div(a, b): return (a + b - 1) // b
