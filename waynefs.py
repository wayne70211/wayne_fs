#!/usr/bin/env python3
import os, errno, time, argparse
from fuse import FUSE, Operations, LoggingMixIn
from disk import Disk
from bitmap import BlockBitmap
from layout import Superblock, DictEnDecoder, Inode, InodeMode, InodeTable, OpenFileState
from dataclasses import dataclass
from typing import List, Tuple

ROOT_INO = 0 
INODE_SIZE = 128

class WayneFS(LoggingMixIn, Operations):
    def __init__(self, image_path):
        self.disk = Disk(image_path)
        self.sb = Superblock.load(self.disk)
        self.bitmap = BlockBitmap(self.disk, self.sb)
        self.inode_table = InodeTable(self.disk, self.sb)
        self.start = time.time()

        # Open File Table
        self.open_file_table = {} # {file_handle (int): info}
        self.next_fh = 0

    # --- helpers ---
    def _iget(self, ino: int) -> Inode:
        return self.inode_table.read(ino)
    
    def _read_dir_entries(self, curr_inode: Inode):
        """
        curr_inode: Inode obj
        return: List[Tuple[int, str]]
        """
        blk_offset = curr_inode.direct[0]
        raw = self.disk.read_block(blk_offset)
        return DictEnDecoder.unpack_dir(raw)
    
    def _write_dir_entries(self, curr_inode: Inode,  entries: List[Tuple[str, int]]):
        """
        curr_inode: Inode obj
        entries: List[Tuple[int, str]]  e.g. [(0, "."), (0, "..")]
        """
        raw_data = DictEnDecoder.pack_dir(entries)
        if len(raw_data) > self.sb.block_size:
            raise OSError(errno.ENOSPC, "dir too large (limit: 1 block)")
        blk_offset = curr_inode.direct[0]
        if blk_offset == ROOT_INO:
            return
        self.disk.write_block(blk_offset, raw_data.ljust(self.sb.block_size, b"\x00"))
        curr_inode.size = len(raw_data)

    def _alloc_inode(self):
        # loop from 1, not 0 due to inode 0 is root
        for ino in range(1, self.sb.inode_count):
            if self._iget(ino).mode == InodeMode.S_INIT:
                return ino
        raise OSError(errno.ENOSPC, "No free inode") 
    
    def _free_inode(self, ino: int):
        self.inode_table.write(ino, Inode.empty(mode=InodeMode.S_INIT))
        return
    
    def _alloc_block(self):
        blk_idx = self.bitmap.find_free_block(self.sb.data_start)
        if blk_idx < 0:
            raise OSError(errno.ENOSPC, "No free inode") 
        self.bitmap.set_used(blk_idx)
        return blk_idx
    
    def _free_block(self, blk_idx: int):
        self.bitmap.clear_used(blk_idx)
        return

    def _lookup(self, path: str): 
        if path == "/" or path == "":
            return ROOT_INO
        
        all_path = [seg for seg in path.split("/") if seg]     
        curr_ino = ROOT_INO
        print("_lookup", all_path)
        for name in all_path:
            if name == ".":
                continue
            elif name == "..":
                curr_inode = self._iget(curr_ino)
                if curr_inode.mode != InodeMode.S_IFDIR:
                    raise OSError(errno.ENOENT, "[A] No such file or directory") 
                parent_ino = None
                for child_ino, child_name in self._read_dir_entries(curr_inode):
                    if child_name == name:
                        parent_ino = child_ino
                        break
                # not found
                if parent_ino is None:
                    parent_ino = ROOT_INO
                curr_ino = parent_ino
            else:
                curr_inode = self._iget(curr_ino)
                if curr_inode.mode != InodeMode.S_IFDIR:
                    raise OSError(errno.ENOENT, "[B] No such file or directory") 
                next_ino = None
                for child_ino, child_name in self._read_dir_entries(curr_inode):
                    if child_name == name:
                        next_ino = child_ino
                        break
                # not found
                if next_ino is None:
                    raise OSError(errno.ENOENT, "[C] No such file or directory") 
                
                curr_ino = next_ino

        return curr_ino
    
    def _split(self, path: str) -> Tuple[str, str]:
        if path == "/" or path == "":
            return path, None
        
        all_path = [seg for seg in path.split("/") if seg]

        return "/".join(all_path[:-1]),all_path[-1]
    # --- FUSE ops ---
    def getattr(self, path, fh=None):
        curr_ino = self._lookup(path)
        curr_inode = self._iget(curr_ino)
        print("getattr", path, "->", curr_inode.mode, curr_inode.nlink, curr_inode.size)
        return {
                "st_mode" : curr_inode.mode,
                "st_nlink": curr_inode.nlink,
                "st_size" : curr_inode.size,
                "st_ctime": curr_inode.ctime,
                "st_mtime": curr_inode.mtime,
                "st_atime": curr_inode.atime,
            }

    def readdir(self, path, fh):
        ino = self._lookup(path)  # validate path exists (root only)
        curr_inode = self._iget(ino)
        print("readdir", path, "entries:", [nm for _,nm in self._read_dir_entries(curr_inode)])
        yield "."
        yield ".."
        for _, name in self._read_dir_entries(curr_inode):
            if not name or name in [".", ".."]:
                continue
            yield name
    
    def mkdir(self, path, mode: int):
        parent_path, curr_dir_name = self._split(path)
        parent_ino = self._lookup(parent_path)
        parent_inode = self._iget(parent_ino)
        if parent_inode.mode != InodeMode.S_IFDIR:
            raise OSError(errno.ENOENT, "No such directory") 
        
        # check the curr_dir_name not in parent_inode entries
        parent_entries = self._read_dir_entries(parent_inode)
        for _, child_name in parent_entries:
            if child_name == curr_dir_name:
                raise OSError(errno.EEXIST, "Directory is existed") 
        
        child_ino = self._alloc_inode()
        child_blk = self._alloc_block()

        # Data
        child_entries = [(child_ino, "."), (parent_ino, "..")]
        raw_data = DictEnDecoder.pack_dir(child_entries)
        self.disk.write_block(child_blk, raw_data + b"\x00" * (self.sb.block_size - len(raw_data)))
        
        # Child Inode
        child_inode = Inode.empty(mode=InodeMode.S_IFDIR)
        child_inode.nlink = 2
        child_inode.size  = len(raw_data)
        child_inode.direct[0] = child_blk
        self.inode_table.write(child_ino, child_inode)

        # Parent Inode
        parent_entries.append((child_ino, curr_dir_name))
        self._write_dir_entries(parent_inode, parent_entries)
        parent_inode.nlink += 1
        parent_inode.ctime = parent_inode.mtime = child_inode.ctime
        self.inode_table.write(parent_ino, parent_inode)

        self.bitmap.flush()
        self.disk.fsync()


    def rmdir(self, path):
        parent_path, _ = self._split(path)
        parent_ino = self._lookup(parent_path)
        parent_inode = self._iget(parent_ino)

        curr_ino = self._lookup(path)
        if curr_ino == ROOT_INO:
            raise OSError(errno.EPERM, "Root directory can not be removed")
         
        curr_inode = self._iget(curr_ino)
        if curr_inode.mode != InodeMode.S_IFDIR:
            raise OSError(errno.ENOENT, "No such directory") 
        
        curr_entries = self._read_dir_entries(curr_inode)
        if len(curr_entries) > 2:
            raise OSError(errno.ENOTEMPTY, "Directory is not empty") 
        
        self._free_block(curr_inode.direct[0])
        self._free_inode(curr_ino)

        old_parent_entries = self._read_dir_entries(parent_inode)
        new_parent_entries = []
        for child_ino, child_name in old_parent_entries:
            if child_ino == curr_ino:
                continue
            new_parent_entries.append((child_ino, child_name))

        assert len(old_parent_entries) == len(new_parent_entries) + 1
        
        parent_inode.nlink -= 1
        assert parent_inode.nlink >= 2

        # write back entries of parent
        self._write_dir_entries(parent_inode, new_parent_entries)
        
        # write back of parent inode to inode table
        self.inode_table.write(parent_ino, parent_inode)
        
        self.bitmap.flush()
        self.disk.fsync()

    def open(self, path, flags):
        # Check file existed and get file ino
        ino = self._lookup(path)
        curr_fh = self.next_fh
        self.next_fh += 1
        self.open_file_table[curr_fh] = OpenFileState(ino, flags, 0)
        return curr_fh
    
    def create(self, path, mode):
        parent_path, curr_file_name = self._split(path)

        parent_ino = self._lookup(parent_path)
        parent_inode = self._iget(parent_ino)
        if parent_inode.mode != InodeMode.S_IFDIR:
            raise OSError(errno.ENOENT, "No such directory") 
        
        # check the curr_dir_name not in parent_inode entries
        parent_entries = self._read_dir_entries(parent_inode)
        for _, child_name in parent_entries:
            if child_name == curr_file_name:
                raise OSError(errno.EEXIST, "File is existed") 
        
        child_ino = self._alloc_inode()
        child_inode = Inode.empty(mode=(InodeMode.S_IFREG | mode))
        child_inode.nlink = 1
        child_inode.size = 0
        self.inode_table.write(child_ino, child_inode)

        parent_entries.append((child_ino, curr_file_name))
        self._write_dir_entries(parent_inode, parent_entries)
        parent_inode.ctime = parent_inode.mtime = child_inode.ctime
        self.inode_table.write(parent_ino, parent_inode)

        self.disk.fsync()

        curr_fh = self.next_fh
        self.next_fh += 1
        self.open_file_table[curr_fh] = OpenFileState(child_ino, mode, 0)
        return curr_fh

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="waynefs.img")
    ap.add_argument("--mountpoint", default="mrt")
    ap.add_argument("--foreground", default=False)
    ap.add_argument("--debug", default=False)
    args = ap.parse_args()
    FUSE(WayneFS(args.image), args.mountpoint, foreground=args.foreground, debug=args.debug)

if __name__ == "__main__":
    main()
