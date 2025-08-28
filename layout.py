#!/usr/bin/env python3
import struct
from dataclasses import dataclass, field
from disk import Disk
from typing import List
from enum import IntFlag


MAGIC = b"WAYNE_FS"
SB_FMT = "<8sIIIIIIIII"
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
    type: int = 0  # 4
    nlink: int = 0 # 4
    size: int = 0  # 8
    ctime: int = 0 # 8
    mtime: int = 0 # 8
    atime: int = 0 # 8
    reserved: int = 0
    direct = [0] * 12

    #def __post_init__(self):
    #    self.directed = [0] * 12

    def pack(self) -> bytes:
        data = bytearray()
        print("atime", self.atime)
        data += struct.pack("<I", self.type)
        data += struct.pack("<I", self.nlink)
        data += struct.pack("<Q", self.size)
        data += struct.pack("<Q", self.ctime)
        data += struct.pack("<Q", self.mtime)
        data += struct.pack("<Q", self.atime)
        for idx in range(12):
            data += struct.pack("<I", self.direct[idx])

        return data

    @classmethod
    def unpack(cls, raw):
        off = 0
        cls.type = struct.unpack_from("<I", raw, off)[0]; off += 4
        cls.nlink = struct.unpack_from("<I", raw, off)[0]; off += 4
        cls.size = struct.unpack_from("<Q", raw, off)[0]; off += 8
        cls.ctime = struct.unpack_from("<Q", raw, off)[0]; off += 8
        cls.mtime = struct.unpack_from("<Q", raw, off)[0]; off += 8
        cls.atime = struct.unpack_from("<Q", raw, off)[0]; off += 8
        for idx in range(12):
            cls.direct[idx] = struct.unpack_from("<I", raw, off)[0]; off += 4

        return cls


@dataclass
class Superblock:
    block_size: int
    total_blocks: int
    inode_count: int
    free_bitmap_start: int
    free_bitmap_blocks: int
    inode_table_start: int
    inode_table_blocks: int
    data_start: int

    @classmethod
    def load(cls, disk: Disk):
        raw = disk.read_block(0)
        fields = struct.unpack(SB_FMT, raw[:SB_SIZE])
        magic = fields[0]
        if magic != MAGIC:
            raise RuntimeError("Bad superblock magic; did you run mktoyfs.py?")
        ( _magic,
          block_size,
          total_blocks,
          inode_count,
          free_bitmap_start,
          free_bitmap_blocks,
          inode_table_start,
          inode_table_blocks,
          data_start,
          _reserved) = fields
        disk.block_size = block_size  # sync disk view
        return cls(block_size, total_blocks, inode_count,
                   free_bitmap_start, free_bitmap_blocks,
                   inode_table_start, inode_table_blocks,
                   data_start)
      

class DictEnDecoder:
  def pack_dir(entries):
      """
      entries: List[Tuple[int, str]]  e.g. [(0, "."), (0, "..")]
      return: bytes
      """
      data = bytearray()
      data += struct.pack("<I", len(entries))  # u32 count
      for ino, name in entries:
          name_b = name.encode("utf-8")
          data += struct.pack("<IH", ino, len(name_b))  # u32 inode, u16 len
          data += name_b
      return bytes(data)

  def unpack_dir(raw):
      """
      raw: bytes of a directory file
      return: List[Tuple[int, str]]
      """
      out = []
      off = 0
      if len(raw) < 4:
          return out
      (cnt,) = struct.unpack_from("<I", raw, off)
      off += 4
      for _ in range(cnt):
          if off + 6 > len(raw): 
              break
          ino, nlen = struct.unpack_from("<IH", raw, off)
          off += 6
          if off + nlen > len(raw): 
              break
          name = raw[off:off+nlen].decode("utf-8", errors="ignore")
          off += nlen
          out.append((ino, name))
      return out
      
def inode_offset(sb: Superblock, idx: int) -> int:
    return sb.inode_table_start * sb.block_size + idx * INODE_SIZE

def inode_read(disk: Disk, sb: Superblock, idx: int) -> Inode:
    off = inode_offset(sb, idx)
    raw = disk.read_at(off, INODE_SIZE)
    return Inode.unpack(raw)

def inode_write(disk: Disk, sb: Superblock, idx: int, inode: Inode):
    off = inode_offset(sb, idx)
    print(off)
    print(inode.pack())
    disk.write_at(off, inode.pack())