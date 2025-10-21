#!/usr/bin/env python3
import os, errno, time, argparse
from fuse import FUSE, Operations, LoggingMixIn
from disk import Disk
from bitmap import InodeBitmap, BlockBitmap
from layout import Superblock, DictEnDecoder, Inode, InodeMode, InodeTable, OpenFileState, ceil_div
from journal import Journal
from cache import PageCache, DentryCache
from typing import List, Tuple, Dict
import struct

ROOT_INO = 0 

class WayneFS(LoggingMixIn, Operations):
    def __init__(self, image_path):
        self.disk = Disk(image_path)
        self.sb = Superblock.load(self.disk)
        self.inode_bitmap = InodeBitmap(self.disk, self.sb)
        self.block_bitmap = BlockBitmap(self.disk, self.sb)
        self.inode_table = InodeTable(self.disk, self.sb)
        self.journal = Journal(self.disk, self.sb)

        # Cache
        self.page_cache = PageCache()
        self.dentry_cache = DentryCache()
        self.start = time.time()

        # Open File Table
        self.open_file_table: Dict[int, OpenFileState] = {}  # {fh(int): OpenFileState}
        self.next_fh = 0

        ADDRS_PER_BLOCK = self.sb.block_size // 4
        self.MAX_BLOCKS = 10 + ADDRS_PER_BLOCK + (ADDRS_PER_BLOCK * ADDRS_PER_BLOCK)

    # --- helpers ---
    def _iget(self, ino: int) -> Inode:
        return self.inode_table.read(ino)
    
    def _read_dir_entries(self, curr_inode: Inode):
        """
        curr_inode: Inode obj
        return: List[Tuple[int, str]]
        """
        blk_offset = curr_inode.direct[0]
        raw = self._read_block_cached(blk_offset)
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
        self._write_block_cached(blk_offset, raw_data.ljust(self.sb.block_size, b"\x00"))
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
        
        cached_ino = self.dentry_cache.get(path)
        if cached_ino is not None:
            return cached_ino
            
        all_path_stack = [seg for seg in path.split("/") if seg][::-1]
        curr_ino = ROOT_INO
        while all_path_stack:
            name = all_path_stack.pop()
            if name == ".":
                continue
            elif name == "..":
                curr_inode = self._iget(curr_ino)
                if (curr_inode.mode & InodeMode.S_IFMT) != InodeMode.S_IFDIR:
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
                if (curr_inode.mode & InodeMode.S_IFMT) != InodeMode.S_IFDIR:
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
                curr_inode = self._iget(curr_ino)
                if (curr_inode.mode & InodeMode.S_IFMT) == InodeMode.S_IFLNK and all_path_stack:
                    target_len = curr_inode.size
                    target = bytearray()

                    if target_len <= 48:
                        target = struct.pack("<12I", *curr_inode.direct)
                    else:
                        start_block_idx = 0
                        end_block_idx = (target_len - 1) // self.sb.block_size
                        for curr_block_idx in range(start_block_idx, end_block_idx+1):
                            addr = self._get_data_block_addr(curr_inode, curr_block_idx)
                            target += self._read_block_cached(addr)
                    
                    symlink_name = target[:target_len].decode("utf-8")
                    if symlink_name.startswith('/'):
                        curr_ino = ROOT_INO
                        target_path = [seg for seg in symlink_name.split("/") if seg][::-1]
                    else:
                        all_path_stack.append(name)
                        target_path = [seg for seg in symlink_name.split("/") if seg][::-1]

                    all_path_stack.extend(target_path)
                    continue

        self.dentry_cache.put(path, curr_ino)
        return curr_ino
    
    def _split(self, path: str) -> Tuple[str, str]:
        if path == "/" or path == "":
            return path, None
        
        all_path = [seg for seg in path.split("/") if seg]

        return "/".join(all_path[:-1]),all_path[-1]
    
    def _get_data_block_addr(self, inode: Inode, logical_block_idx: int) -> int:
        ADDRS_PER_BLOCK = self.sb.block_size // 4     # 1024
        SINGLY_LIMIT = 10
        DOUBLY_LIMIT = SINGLY_LIMIT + ADDRS_PER_BLOCK # 10 + 1024 = 1034

        # direct[0] ~ direct[9]
        if logical_block_idx < SINGLY_LIMIT:
            return inode.direct[logical_block_idx]

        # direct[10]
        elif logical_block_idx < DOUBLY_LIMIT:
            l1_block_content = self._read_block_cached(inode.direct[10])
            
            l1_index = logical_block_idx - SINGLY_LIMIT
            ptr_offset = l1_index * 4
            
            addr_bytes = l1_block_content[ptr_offset : ptr_offset + 4]
            return struct.unpack("<I", addr_bytes)[0]

        # direct[11]
        else:
            l1_block_content = self._read_block_cached(inode.direct[11])
            
            l1_index = (logical_block_idx - DOUBLY_LIMIT) // ADDRS_PER_BLOCK
            ptr_offset_l1 = l1_index * 4

            l2_addr_bytes = l1_block_content[ptr_offset_l1 : ptr_offset_l1 + 4]
            l2_addr = struct.unpack("<I", l2_addr_bytes)[0]

            l2_block_content = self._read_block_cached(l2_addr)
            
            l2_index = (logical_block_idx - DOUBLY_LIMIT) % ADDRS_PER_BLOCK
            ptr_offset_l2 = l2_index * 4
            
            addr_bytes = l2_block_content[ptr_offset_l2 : ptr_offset_l2 + 4]
            return struct.unpack("<I", addr_bytes)[0]
        
    def _get_or_alloc_data_block_addr(self, inode: Inode, logical_block_idx: int) -> int:
        ADDRS_PER_BLOCK = self.sb.block_size // 4     # 1024
        SINGLY_LIMIT = 10
        DOUBLY_LIMIT = SINGLY_LIMIT + ADDRS_PER_BLOCK # 10 + 1024 = 1034

        # direct[0] ~ direct[9]
        if logical_block_idx < SINGLY_LIMIT:
            if inode.direct[logical_block_idx] == 0:
                proc_block_addr = self._alloc_block()
                inode.direct[logical_block_idx] = proc_block_addr
            return inode.direct[logical_block_idx]
        
        # direct[10]
        elif logical_block_idx < DOUBLY_LIMIT:
            if inode.direct[10] == 0:
                inode.direct[10] = self._alloc_block()

            l1_block_content = bytearray(self._read_block_cached(inode.direct[10]))
            
            l1_index = logical_block_idx - SINGLY_LIMIT
            ptr_offset = l1_index * 4
            
            addr = struct.unpack("<I", l1_block_content[ptr_offset : ptr_offset + 4])[0]

            # allocate
            if addr == 0:
                addr = self._alloc_block()
                l1_block_content[ptr_offset : ptr_offset + 4] = struct.pack("<I", addr)
                self._write_block_cached(inode.direct[10], bytes(l1_block_content).ljust(self.sb.block_size, b"\x00"))
            return addr
        
        # direct[11]
        else:
            if inode.direct[11] == 0:
                inode.direct[11] = self._alloc_block()

            l1_block_content = bytearray(self._read_block_cached(inode.direct[11]))
            
            l1_index = (logical_block_idx - DOUBLY_LIMIT) // ADDRS_PER_BLOCK
            ptr_offset_l1 = l1_index * 4

            l2_addr_bytes = l1_block_content[ptr_offset_l1 : ptr_offset_l1 + 4]
            l2_addr = struct.unpack("<I", l2_addr_bytes)[0]

            if l2_addr == 0:
                l2_addr = self._alloc_block()
                l1_block_content[ptr_offset_l1 : ptr_offset_l1 + 4] = struct.pack("<I", l2_addr)
                self._write_block_cached(inode.direct[11], bytes(l1_block_content).ljust(self.sb.block_size, b"\x00"))

            l2_block_content = bytearray(self._read_block_cached(l2_addr))
            
            l2_index = (logical_block_idx - DOUBLY_LIMIT) % ADDRS_PER_BLOCK
            ptr_offset_l2 = l2_index * 4
            
            addr = struct.unpack("<I", l2_block_content[ptr_offset_l2 : ptr_offset_l2 + 4])[0]

            # allocate
            if addr == 0:
                addr = self._alloc_block()
                l2_block_content[ptr_offset_l2 : ptr_offset_l2 + 4] = struct.pack("<I", addr)
                self._write_block_cached(l2_addr, bytes(l2_block_content).ljust(self.sb.block_size, b"\x00"))
            return addr
        
    def _free_data_blocks(self, inode: Inode, start_block: int, end_block: int):
        ADDRS_PER_BLOCK = self.sb.block_size // 4     # 1024
        SINGLY_LIMIT = 10
        DOUBLY_LIMIT = SINGLY_LIMIT + ADDRS_PER_BLOCK # 10 + 1024 = 1034

        # get addr => free block (addr) => set value 0
        for logical_block_idx in range(start_block, end_block+1):
            
            if logical_block_idx < SINGLY_LIMIT:
                if inode.direct[logical_block_idx] != 0:
                    self._free_block(inode.direct[logical_block_idx])
                    inode.direct[logical_block_idx] = 0

            elif logical_block_idx < DOUBLY_LIMIT:
                if inode.direct[10] == 0:
                    continue

                l1_block_content = bytearray(self._read_block_cached(inode.direct[10]))
                l1_index = logical_block_idx - SINGLY_LIMIT
                ptr_offset = l1_index * 4

                addr = struct.unpack("<I", l1_block_content[ptr_offset:ptr_offset+4])[0]

                if addr != 0:
                    self._free_block(addr)
                    l1_block_content[ptr_offset:ptr_offset+4] = struct.pack("<I", 0)
                    self._write_block_cached(inode.direct[10], bytes(l1_block_content).ljust(self.sb.block_size, b"\x00"))

            else:
                if inode.direct[11] == 0:
                    break

                l1_block_content = bytearray(self._read_block_cached(inode.direct[11]))
                l1_index = (logical_block_idx - DOUBLY_LIMIT) // ADDRS_PER_BLOCK
                ptr_offset_l1 = l1_index * 4

                l2_addr_bytes = l1_block_content[ptr_offset_l1 : ptr_offset_l1 + 4]
                l2_addr = struct.unpack("<I", l2_addr_bytes)[0]

                if l2_addr == 0:
                    continue

                l2_block_content = bytearray(self._read_block_cached(l2_addr))
                
                l2_index = (logical_block_idx - DOUBLY_LIMIT) % ADDRS_PER_BLOCK
                ptr_offset_l2 = l2_index * 4

                addr = struct.unpack("<I", l2_block_content[ptr_offset_l2 : ptr_offset_l2 + 4])[0]

                if addr != 0:
                    self._free_block(addr)
                    l2_block_content[ptr_offset_l2 : ptr_offset_l2 + 4] = struct.pack("<I", 0)
                    self._write_block_cached(l2_addr, bytes(l2_block_content).ljust(self.sb.block_size, b"\x00"))

    # --- cache helper ---
    def _read_block_cached(self, block_addr: int) -> bytes:
        cached_data = self.page_cache.get(block_addr)
        if cached_data is not None:
            return cached_data
        
        data = self.disk.read_block(block_addr)
        self.page_cache.put(block_addr, data)
        return data
    
    def _write_block_cached(self, block_addr: int, data: bytes):
        self.disk.write_block(block_addr, data)
        self.page_cache.put(block_addr, data)

    # --- FUSE ops ---    
    def getattr(self, path, fh=None):
        curr_ino = self._lookup(path)
        curr_inode = self._iget(curr_ino)
        print("getattr", path, "->", curr_ino, curr_inode.mode, curr_inode.nlink, curr_inode.size)
        return {
                "st_mode" : curr_inode.mode,
                "st_nlink": curr_inode.nlink,
                "st_size" : curr_inode.size,
                "st_ctime": curr_inode.ctime,
                "st_mtime": curr_inode.mtime,
                "st_atime": curr_inode.atime,
                "st_ino": curr_ino,
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
        if (parent_inode.mode & InodeMode.S_IFMT) != InodeMode.S_IFDIR:
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
        self._write_block_cached(child_blk, raw_data + b"\x00" * (self.sb.block_size - len(raw_data)))
        
        parent_entries.append((child_ino, curr_dir_name))
        self._write_dir_entries(parent_inode, parent_entries)

        with self.journal.begin() as tx:
            # Child Inode
            child_inode = Inode.empty(mode=(InodeMode.S_IFDIR | mode))
            child_inode.nlink = 2
            child_inode.size  = len(raw_data)
            child_inode.direct[0] = child_blk
            self.inode_table.write(child_ino, child_inode, tx)

            # Parent Inode
            parent_inode.nlink += 1
            parent_inode.ctime = parent_inode.mtime = child_inode.ctime
            self.inode_table.write(parent_ino, parent_inode, tx)

            self.inode_bitmap.flush(tx)
            self.block_bitmap.flush(tx)


    def rmdir(self, path):
        parent_path, _ = self._split(path)
        parent_ino = self._lookup(parent_path)
        parent_inode = self._iget(parent_ino)

        curr_ino = self._lookup(path)
        if curr_ino == ROOT_INO:
            raise OSError(errno.EPERM, "Root directory can not be removed")
         
        curr_inode = self._iget(curr_ino)
        if (curr_inode.mode & InodeMode.S_IFMT) != InodeMode.S_IFDIR:
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
        with self.journal.begin() as tx:
            self.inode_table.write(parent_ino, parent_inode, tx)
            self.block_bitmap.flush(tx)
            self.inode_bitmap.flush(tx)
        
        self.dentry_cache.remove(path)

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
        if (parent_inode.mode & InodeMode.S_IFMT) != InodeMode.S_IFDIR:
            raise OSError(errno.ENOENT, "No such directory") 
        
        # check the curr_dir_name not in parent_inode entries
        parent_entries = self._read_dir_entries(parent_inode)
        for _, child_name in parent_entries:
            if child_name == curr_file_name:
                raise OSError(errno.EEXIST, "File is existed") 
        
        child_ino = self._alloc_inode()
        parent_entries.append((child_ino, curr_file_name))
        self._write_dir_entries(parent_inode, parent_entries)

        with self.journal.begin() as tx:
            child_inode = Inode.empty(mode=(InodeMode.S_IFREG | mode))
            child_inode.nlink = 1
            child_inode.size = 0
            self.inode_table.write(child_ino, child_inode, tx)
            parent_inode.ctime = parent_inode.mtime = child_inode.ctime
            self.inode_table.write(parent_ino, parent_inode, tx)
            self.inode_bitmap.flush(tx)

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
        if end_block_idx >= self.MAX_BLOCKS:
            raise OSError(errno.EFBIG, "File too large")
        
        # Generate the link from inode direct to disk offset 
        for curr_block_idx in range(start_block_idx, end_block_idx+1):
            self._get_or_alloc_data_block_addr(curr_inode, curr_block_idx)


        data_cursor = 0
        # Write buffer
        for curr_block_idx in range(start_block_idx, end_block_idx+1):

            curr_start_offset = offset % self.sb.block_size if curr_block_idx == start_block_idx else 0
            curr_end_offset = (offset + length - 1) % self.sb.block_size + 1 if curr_block_idx == end_block_idx else self.sb.block_size

            is_partial_write = curr_start_offset != 0 or curr_end_offset != self.sb.block_size

            addr = self._get_data_block_addr(curr_inode, curr_block_idx)
            if is_partial_write:
                curr_block = bytearray(self._read_block_cached(addr))
            else:
                curr_block = bytearray(self.sb.block_size)

            need_write_data_len = curr_end_offset - curr_start_offset
            curr_block[curr_start_offset: curr_end_offset] = data[data_cursor:data_cursor+need_write_data_len]
            data_cursor += need_write_data_len

            self._write_block_cached(addr, bytes(curr_block))

        # Add Journal record metadata
        with self.journal.begin() as tx:
            curr_inode.size = max(curr_inode.size, offset+length)
            curr_inode.mtime = int(time.time())
            self.inode_table.write(curr_file_state.ino, curr_inode, tx)
            self.block_bitmap.flush(tx)

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

            addr = self._get_data_block_addr(curr_inode, curr_block_idx)
            curr_block = self._read_block_cached(addr)
            need_read_data_len = curr_end_offset - curr_start_offset
            data[data_cursor:data_cursor+need_read_data_len] = curr_block[curr_start_offset: curr_end_offset]
            data_cursor += need_read_data_len

        # Add Journal record metadata (access time)
        with self.journal.begin() as tx:
            curr_inode.atime = int(time.time())
            self.inode_table.write(curr_file_state.ino, curr_inode, tx)

        return bytes(data)
    
    def unlink(self, path):
        print(f"--- unlink called for: {path} ---")
        parent_path, curr_name = self._split(path)
        parent_ino = self._lookup(parent_path)
        parent_inode = self._iget(parent_ino)

        curr_ino = self._lookup(path)
        curr_inode = self._iget(curr_ino)

        old_parent_entries = self._read_dir_entries(parent_inode)
        print(f"unlink: old_parent_entries = {old_parent_entries}")
        new_parent_entries = []
        for child_ino, child_name in old_parent_entries:
            if child_name == curr_name:
                continue
            new_parent_entries.append((child_ino, child_name))

        assert len(old_parent_entries) == len(new_parent_entries) + 1

        # write back entries of parent
        self._write_dir_entries(parent_inode, new_parent_entries)
        if (curr_inode.mode & InodeMode.S_IFMT) == InodeMode.S_IFDIR:
            raise OSError(errno.EISDIR, "Is a directory")

        curr_inode.nlink -= 1
        print(f"unlink: new_parent_entries = {new_parent_entries}")
        with self.journal.begin() as tx:
            if curr_inode.nlink == 0:
                # Remove all block and set free
                
                is_symlink = (curr_inode.mode & InodeMode.S_IFMT) == InodeMode.S_IFLNK
                is_slow_link = is_symlink and curr_inode.size > 48
                is_regular_or_slow_link = not is_symlink or is_slow_link

                if is_regular_or_slow_link:
                    ADDRS_PER_BLOCK = self.sb.block_size // 4
                    SINGLY_LIMIT = 10
                    DOUBLY_LIMIT = SINGLY_LIMIT + ADDRS_PER_BLOCK
                    original_blks = ceil_div(curr_inode.size, self.sb.block_size)
                    self._free_data_blocks(curr_inode, 0, original_blks) 

                    if original_blks > SINGLY_LIMIT:
                        self._free_block(curr_inode.direct[10])

                    if original_blks > DOUBLY_LIMIT:
                        l1_block_content = bytearray(self._read_block_cached(curr_inode.direct[11]))
                        ptr = 0
                        for i in range(ADDRS_PER_BLOCK):
                            l2_addr = struct.unpack("<I", l1_block_content[ptr:ptr+4])[0]
                            if l2_addr != 0:
                                self._free_block(l2_addr)
                            else:
                                break
                            ptr += 4
                        self._free_block(curr_inode.direct[11])

                self._free_inode(curr_ino)
            else:
                self.inode_table.write(curr_ino, curr_inode, tx)

            parent_inode.mtime = parent_inode.ctime = int(time.time())
            # write back of parent inode to inode table
            self.inode_table.write(parent_ino, parent_inode, tx)

            self.block_bitmap.flush(tx)
            self.inode_bitmap.flush(tx)

        self.dentry_cache.remove(path)
    
    def truncate(self, path, length, fh=None):
        ino = self._lookup(path)
        inode = self._iget(ino)

        ADDRS_PER_BLOCK = self.sb.block_size // 4
        SINGLY_LIMIT = 10
        DOUBLY_LIMIT = SINGLY_LIMIT + ADDRS_PER_BLOCK

        original_blks = ceil_div(inode.size, self.sb.block_size)
        need_blks = ceil_div(length, self.sb.block_size)

        if need_blks > self.MAX_BLOCKS:
            raise OSError(errno.EFBIG, "File too large for direct blocks")

        # extend
        if length > inode.size:
            for i in range(original_blks, need_blks):
                addr = self._get_or_alloc_data_block_addr(inode, i)
                # write all 0 into new_blk
                self._write_block_cached(addr,  b'\x00' * self.sb.block_size)
                    
        else:
            self._free_data_blocks(inode, need_blks, original_blks-1)

            if need_blks < DOUBLY_LIMIT and inode.direct[11] != 0:
                l1_block_content = bytearray(self._read_block_cached(inode.direct[11]))
                ptr = 0
                for i in range(ADDRS_PER_BLOCK):
                    l2_addr = struct.unpack("<I", l1_block_content[ptr:ptr+4])[0]
                    if l2_addr != 0:
                        self._free_block(l2_addr)
                    else:
                        break
                    ptr += 4
                self._free_block(inode.direct[11])
                inode.direct[11] = 0

            if need_blks < SINGLY_LIMIT and inode.direct[10] != 0:
                self._free_block(inode.direct[10])
                inode.direct[10] = 0

        with self.journal.begin() as tx:
            inode.size = length
            inode.mtime = inode.ctime = int(time.time())
            self.inode_table.write(ino, inode, tx)
            self.block_bitmap.flush(tx)
    
    def rename(self, old, new):
        if old == new:
            return
        
        try:
            ino = self._lookup(new)
            inode = self.inode_table.read(ino)
            if (inode.mode & InodeMode.S_IFMT) == InodeMode.S_IFDIR:
                self.rmdir(new)
            else:
                self.unlink(new)
            self.dentry_cache.remove(new)
        except OSError:
            pass

        old_parent_path, old_name = self._split(old)
        old_parent_ino = self._lookup(old_parent_path)
        old_parent_inode = self._iget(old_parent_ino)

        new_parent_path, new_name = self._split(new)
        new_parent_ino = self._lookup(new_parent_path)
        new_parent_inode = self._iget(new_parent_ino)

        if (old_parent_inode.mode & InodeMode.S_IFMT) != InodeMode.S_IFDIR:
            raise OSError(errno.ENOENT, "No such directory") 
        
        if (new_parent_inode.mode & InodeMode.S_IFMT) != InodeMode.S_IFDIR:
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

        old_parent_dentry = [entry for entry in old_parent_dentry if entry[1] != old_name]
        self._write_dir_entries(old_parent_inode, old_parent_dentry)

        new_parent_dentry = self._read_dir_entries(new_parent_inode)
        new_parent_dentry.append((curr_ino, new_name))

        if (curr_inode.mode & InodeMode.S_IFMT) == InodeMode.S_IFDIR:
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

        # Update Inode Table
        with self.journal.begin() as tx:
            current_time = int(time.time())
            old_parent_inode.mtime = old_parent_inode.ctime = current_time
            new_parent_inode.mtime = new_parent_inode.ctime = current_time
            curr_inode.ctime = current_time
            self.inode_table.write(old_parent_ino, old_parent_inode, tx)
            self.inode_table.write(new_parent_ino, new_parent_inode, tx)
            self.inode_table.write(curr_ino, curr_inode, tx)

        self.dentry_cache.remove(old)
    
    def utimens(self, path, times=None):
        ino = self._lookup(path)
        inode = self._iget(ino)

        if times == None:
            now = time.time()
            times = (now, now)

        with self.journal.begin() as tx:
            inode.atime = int(times[0])
            inode.mtime = int(times[1])
            inode.ctime = int(time.time())
            self.inode_table.write(ino, inode, tx)

        return 0
    
    def chmod(self, path, mode):
        ino = self._lookup(path)
        inode = self._iget(ino)

        with self.journal.begin() as tx:
            inode.mode = (inode.mode & InodeMode.S_IFMT) | (mode & 0o777)
            inode.ctime = int(time.time())
            self.inode_table.write(ino, inode, tx)

        return 0
    
    def link(self, target, source):
        print(f"--- link called: source='{source}', target='{target}' ---")
        src_parent_path, src_name = self._split(source)
        src_parent_ino = self._lookup(src_parent_path)
        src_parent_inode = self._iget(src_parent_ino)

        curr_ino = None
        for child_ino, child_name in self._read_dir_entries(src_parent_inode):
            if child_name == src_name:
                curr_ino = child_ino
                break

        if curr_ino == None:
            raise OSError(errno.ENOENT, "Source path does not exist")
        
        curr_inode = self._iget(curr_ino)

        if (curr_inode.mode & InodeMode.S_IFMT) == InodeMode.S_IFDIR:
            raise OSError(errno.EPERM, "Hard link not allowed for directory")
        
        trg_parent_path, trg_name = self._split(target)
        trg_parent_ino = self._lookup(trg_parent_path)
        trg_parent_inode = self._iget(trg_parent_ino)

        # Check trg_name is not existed
        trg_dentry = self._read_dir_entries(trg_parent_inode)
        for child_ino, child_name in trg_dentry:
            if child_name == trg_name:
                raise OSError(errno.EEXIST, "File is existed")
            
        trg_dentry.append((curr_ino, trg_name))
        self._write_dir_entries(trg_parent_inode, trg_dentry)
        
        

        with self.journal.begin() as tx:
            curr_time = int(time.time())
            curr_inode.ctime = curr_time
            curr_inode.nlink += 1
            trg_parent_inode.ctime = trg_parent_inode.mtime = curr_time
            self.inode_table.write(curr_ino, curr_inode, tx)
            self.inode_table.write(trg_parent_ino, trg_parent_inode, tx)

        print("link ()", source, "->" ,target)
        print("curr_ino = ", curr_ino)
        print("trg_dentry = ", trg_dentry)
        print("trg_parent_ino = ", trg_parent_ino)

        return 0
    
    def symlink(self, target: str, source: str):
        print(f"--- symlink called: source='{source}', target='{target}' ---")
        link_parent_path, link_name = self._split(target)
        self.dentry_cache.remove(link_parent_path)

        link_parent_ino = self._lookup(link_parent_path)
        link_parent_inode = self._iget(link_parent_ino)
    
        if (link_parent_inode.mode & InodeMode.S_IFMT) != InodeMode.S_IFDIR:
            raise OSError(errno.ENOENT, "Parent directory does not exist")
        
        parent_entries = self._read_dir_entries(link_parent_inode)
        curr_ino = self._alloc_inode()
        parent_entries.append((curr_ino, link_name))
        self._write_dir_entries(link_parent_inode, parent_entries)

        target_bytes = source.encode('utf-8')
        target_len = len(target_bytes)

        is_slow = target_len > 48

        with self.journal.begin() as tx:
            curr_inode = Inode.empty(mode=(InodeMode.S_IFLNK | 0o777))
            curr_inode.nlink = 1
            curr_inode.size = target_len

            if is_slow:
                start_block_idx = 0
                end_block_idx = (target_len - 1) // self.sb.block_size
                ptr = 0
                for curr_block_idx in range(start_block_idx, end_block_idx+1):
                    addr = self._get_or_alloc_data_block_addr(curr_inode, curr_block_idx)
                    data = target_bytes[ptr:ptr+self.sb.block_size]
                    self._write_block_cached(addr, data.ljust(self.sb.block_size, b'\x00'))
                    ptr += self.sb.block_size
            else:
                padded_target = target_bytes.ljust(48, b'\x00')
                curr_inode.direct = list(struct.unpack("<12I", padded_target))

            self.inode_table.write(curr_ino, curr_inode, tx)
            link_parent_inode.ctime = link_parent_inode.mtime = curr_inode.ctime
            self.inode_table.write(link_parent_ino, link_parent_inode, tx)
            self.inode_bitmap.flush(tx)

            if is_slow:
                self.block_bitmap.flush(tx)
        print(f"--- symlink called end ---")
        return 0


    def readlink(self, path: str):
        ino = self._lookup(path)
        inode = self._iget(ino)

        if (inode.mode & InodeMode.S_IFMT) != InodeMode.S_IFLNK:
            raise OSError(errno.EINVAL, "Not a symbolic link")
    
        target_len = inode.size
        target = bytearray()

        if target_len <= 48:
            target = struct.pack("<12I", *inode.direct)
        else:
            start_block_idx = 0
            end_block_idx = (target_len - 1) // self.sb.block_size
            for curr_block_idx in range(start_block_idx, end_block_idx+1):
                addr = self._get_data_block_addr(inode, curr_block_idx)
                data = self._read_block_cached(addr)
                target += data

        return target[:target_len].decode('utf-8')


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
