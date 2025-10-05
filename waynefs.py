#!/usr/bin/env python3
import os, errno, time, argparse
from fuse import FUSE, Operations, LoggingMixIn
from disk import Disk
from bitmap import InodeBitmap, BlockBitmap
from layout import INODE_SIZE, Superblock, DictEnDecoder, Inode, InodeMode, InodeTable, OpenFileState, ceil_div
from dataclasses import dataclass
from typing import List, Tuple, Dict

ROOT_INO = 0 

class WayneFS(LoggingMixIn, Operations):
    def __init__(self, image_path):
        self.disk = Disk(image_path)
        self.sb = Superblock.load(self.disk)
        self.inode_bitmap = InodeBitmap(self.disk, self.sb)
        self.block_bitmap = BlockBitmap(self.disk, self.sb)
        self.inode_table = InodeTable(self.disk, self.sb)
        self.start = time.time()

        # Open File Table
        self.open_file_table: Dict[int, OpenFileState] = {}  # {fh(int): OpenFileState}
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
        ino = self.inode_bitmap.find_free_inode(1)
        if ino < 0:
            raise OSError(errno.ENOSPC, "No free inode") 
        self.inode_bitmap.set_used(ino)
        return ino
        
    def _free_inode(self, ino: int):
        self.inode_bitmap.clear_used(ino)
        return
    
    def _alloc_block(self):
        blk_idx = self.block_bitmap.find_free_block(self.sb.data_start)
        if blk_idx < 0:
            raise OSError(errno.ENOSPC, "No free block") 
        self.block_bitmap.set_used(blk_idx)
        return blk_idx
    
    def _free_block(self, blk_idx: int):
        self.block_bitmap.clear_used(blk_idx)
        return

    def _lookup(self, path: str): 
        if path == "/" or path == "":
            return ROOT_INO
        
        all_path = [seg for seg in path.split("/") if seg]     
        curr_ino = ROOT_INO
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

        self.block_bitmap.flush()
        self.inode_bitmap.flush()
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
        
        self.block_bitmap.flush()
        self.inode_bitmap.flush()
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

        self.inode_bitmap.flush()
        self.disk.fsync()

        curr_fh = self.next_fh
        self.next_fh += 1
        self.open_file_table[curr_fh] = OpenFileState(child_ino, mode, 0)
        return curr_fh
    
    def write(self, path, data, offset, fh):
        if fh not in self.open_file_table:
            raise OSError(errno.EBADF, "Bad file descriptor")
    
        curr_file_state = self.open_file_table[fh]
        curr_inode = self._iget(curr_file_state.ino)

        length = len(data)
        if length == 0:
            return 0
        
        start_block_idx = offset // self.sb.block_size
        end_block_idx = (offset + length - 1) // self.sb.block_size

        # Check file size constrain (12 direct link)
        if end_block_idx >= len(curr_inode.direct):
            raise OSError(errno.EFBIG, "File too large")
        
        # Generate the link from inode direct to disk offset 
        for curr_block_idx in range(start_block_idx, end_block_idx+1):
            if curr_inode.direct[curr_block_idx] == 0:
                proc_block_addr = self._alloc_block()
                curr_inode.direct[curr_block_idx] = proc_block_addr


        data_cursor = 0
        # Write buffer
        for curr_block_idx in range(start_block_idx, end_block_idx+1):

            curr_start_offset = offset % self.sb.block_size if curr_block_idx == start_block_idx else 0
            curr_end_offset = (offset + length - 1) % self.sb.block_size + 1 if curr_block_idx == end_block_idx else self.sb.block_size

            is_partial_write = curr_start_offset != 0 or curr_end_offset != self.sb.block_size

            if is_partial_write:
                curr_block = bytearray(self.disk.read_block(curr_inode.direct[curr_block_idx]))
            else:
                curr_block = bytearray(self.sb.block_size)

            need_write_data_len = curr_end_offset - curr_start_offset
            curr_block[curr_start_offset: curr_end_offset] = data[data_cursor:data_cursor+need_write_data_len]
            data_cursor += need_write_data_len

            self.disk.write_block(curr_inode.direct[curr_block_idx], bytes(curr_block))


        curr_inode.size = max(curr_inode.size, offset+length)
        curr_inode.mtime = int(time.time())
        self.inode_table.write(curr_file_state.ino, curr_inode)
        self.block_bitmap.flush()

        return length
    
    def read(self, path, size, offset, fh):
        if fh not in self.open_file_table:
            raise OSError(errno.EBADF, "Bad file descriptor")
    
        curr_file_state = self.open_file_table[fh]
        curr_inode = self._iget(curr_file_state.ino)

        if offset >= curr_inode.size:
            return b""
        
        size = min(size, curr_inode.size - offset)
        if size == 0:
            return b""

        start_block_idx = offset // self.sb.block_size
        end_block_idx = (offset + size - 1) // self.sb.block_size

        data = bytearray(size)

        data_cursor = 0

        # Read buffer
        for curr_block_idx in range(start_block_idx, end_block_idx+1):

            curr_start_offset = offset % self.sb.block_size if curr_block_idx == start_block_idx else 0
            curr_end_offset = (offset + size - 1) % self.sb.block_size + 1 if curr_block_idx == end_block_idx else self.sb.block_size

            curr_block = self.disk.read_block(curr_inode.direct[curr_block_idx])
            need_read_data_len = curr_end_offset - curr_start_offset
            data[data_cursor:data_cursor+need_read_data_len] = curr_block[curr_start_offset: curr_end_offset]
            data_cursor += need_read_data_len


        return bytes(data)
    
    def unlink(self, path):
        parent_path, _ = self._split(path)
        parent_ino = self._lookup(parent_path)
        parent_inode = self._iget(parent_ino)

        curr_ino = self._lookup(path)
        curr_inode = self._iget(curr_ino)

        old_parent_entries = self._read_dir_entries(parent_inode)
        new_parent_entries = []
        for child_ino, child_name in old_parent_entries:
            if child_ino == curr_ino:
                continue
            new_parent_entries.append((child_ino, child_name))

        assert len(old_parent_entries) == len(new_parent_entries) + 1

        # write back entries of parent
        self._write_dir_entries(parent_inode, new_parent_entries)
        if curr_inode.mode == InodeMode.S_IFDIR:
            raise OSError(errno.EISDIR, "Is a directory")

        curr_inode.nlink -= 1

        if curr_inode.nlink == 0:
            # Remove all block and set free
            for block_no in curr_inode.direct:
                if block_no != 0:
                    self._free_block(block_no)
            self._free_inode(curr_ino)
        else:
            self.inode_table.write(curr_ino, curr_inode)

        parent_inode.mtime = parent_inode.ctime = int(time.time())
        # write back of parent inode to inode table
        self.inode_table.write(parent_ino, parent_inode)

        
        self.block_bitmap.flush()
        self.inode_bitmap.flush()
        self.disk.fsync()
    
    def truncate(self, path, length, fh=None):
        ino = self._lookup(path)
        inode = self._iget(ino)

        original_blks = ceil_div(inode.size, self.sb.block_size)
        need_blks = ceil_div(length, self.sb.block_size)

        # extend
        if length > inode.size:
            if need_blks > len(inode.direct):
                raise OSError(errno.EFBIG, "File too large for direct blocks")
            
            for i in range(original_blks, need_blks):
                if inode.direct[i] == 0:
                    new_blk = self._alloc_block()
                    inode.direct[i] = new_blk

                    # write all 0 into new_blk
                    self.disk.write_block(new_blk,  b'\x00' * self.sb.block_size)
                    
        else:
            for i in range(need_blks + 1, original_blks):
                if inode.direct[i] != 0:
                    self._free_block(inode.direct[i])
                    inode.direct[i] = 0

        inode.size = length
        inode.mtime = inode.ctime = int(time.time())
        self.inode_table.write(ino, inode)
        self.block_bitmap.flush()
    
    def rename(self, old, new):
        if old == new:
            return

        old_parent_path, old_name = self._split(old)
        old_parent_ino = self._lookup(old_parent_path)
        old_parent_inode = self._iget(old_parent_ino)

        new_parent_path, new_name = self._split(new)
        new_parent_ino = self._lookup(new_parent_path)
        new_parent_inode = self._iget(new_parent_ino)

        if old_parent_inode.mode != InodeMode.S_IFDIR:
            raise OSError(errno.ENOENT, "No such directory") 
        
        if new_parent_inode.mode != InodeMode.S_IFDIR:
            raise OSError(errno.ENOENT, "No such directory") 
        
        old_parent_dentry = self._read_dir_entries(old_parent_inode)
        curr_ino = None
        for ino, name in old_parent_dentry:
            if name == old_name:
                curr_ino = ino
                break
        
        if curr_ino is None:
            raise OSError(errno.ENOENT, "Source path does not exist")
        
        curr_inode = self._iget(curr_ino)

        old_parent_dentry = [entry for entry in old_parent_dentry if entry[0] != curr_ino]
        self._write_dir_entries(old_parent_inode, old_parent_dentry)

        new_parent_dentry = self._read_dir_entries(new_parent_inode)
        new_parent_dentry.append((curr_ino, new_name))

        if curr_inode.mode == InodeMode.S_IFDIR:
            if old_parent_ino != new_parent_ino:
                old_parent_inode.nlink -= 1
                new_parent_inode.nlink += 1
                curr_inode_dentry = self._read_dir_entries(curr_inode)
                curr_inode_dentry.remove((old_parent_ino, ".."))
                curr_inode_dentry.append((new_parent_ino, ".."))
                self._write_dir_entries(curr_inode, curr_inode_dentry)
        
        # Write old and new parent d-entry
        self._write_dir_entries(old_parent_inode, old_parent_dentry)
        self._write_dir_entries(new_parent_inode, new_parent_dentry)

        current_time = int(time.time())
        old_parent_inode.mtime = old_parent_inode.ctime = current_time
        new_parent_inode.mtime = new_parent_inode.ctime = current_time
        curr_inode.ctime = current_time

        # Update Inode Table
        self.inode_table.write(old_parent_ino, old_parent_inode)
        self.inode_table.write(new_parent_ino, new_parent_inode)
        self.inode_table.write(curr_ino, curr_inode)
        self.disk.fsync()
    
    def utimens(self, path, times=None):
        ino = self._lookup(path)
        inode = self._iget(ino)

        if times == None:
            now = time.time()
            times = (now, now)

        inode.atime = int(times[0])
        inode.mtime = int(times[1])
        inode.ctime = int(time.time())
        self.inode_table.write(ino, inode)

        return 0

    

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="waynefs.img")
    ap.add_argument("--mountpoint", default="mnt")
    ap.add_argument("--foreground", default=False)
    ap.add_argument("--debug", default=False)
    args = ap.parse_args()
    FUSE(WayneFS(args.image), args.mountpoint, foreground=args.foreground, debug=args.debug)

if __name__ == "__main__":
    main()
