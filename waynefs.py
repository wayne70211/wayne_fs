#!/usr/bin/env python3
import os, errno, time, argparse
from fuse import FUSE, Operations, LoggingMixIn
from disk import Disk
from layout import Superblock, DictEnDecoder, Inode, InodeMode, inode_read
from dataclasses import dataclass

ROOT_INO = 0 
INODE_SIZE = 128

class WayneFS(LoggingMixIn, Operations):
    def __init__(self, image_path):
        self.disk = Disk(image_path)
        self.sb = Superblock.load(self.disk)
        self.start = time.time()

    # --- helpers ---
    def _iget(self, ino: int):
        return inode_read(self.disk, self.sb, ino)
    
    def _read_dir_entries(self, curr_inode: Inode):
        blk_addr = curr_inode.direct[0]
        return DictEnDecoder.unpack_dir(blk_addr)
    
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
                if curr_inode.type is not InodeMode.S_IFDIR:
                    raise OSError(errno.ENOENT, "No such file or directory") 
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
                if curr_inode.type is not InodeMode.S_IFDIR:
                    raise OSError(errno.ENOENT, "No such file or directory") 
                next_ino = None
                for child_ino, child_name in self._read_dir_entries(curr_inode):
                    if child_name == name:
                        next_ino = child_ino
                        break
                # not found
                if next_ino is None:
                    raise OSError(errno.ENOENT, "No such file or directory") 
                
                curr_ino = next_ino

        return curr_ino
    
    # --- FUSE ops ---
    def getattr(self, path, fh=None):
        curr_ino = self._lookup(path)
        curr_inode = self._iget(curr_ino)
        return {
                "st_mode" : curr_inode.type,
                "st_nlink": curr_inode.nlink,
                "st_size" : curr_inode.size,
                "st_ctime": curr_inode.ctime,
                "st_mtime": curr_inode.mtime,
                "st_atime": curr_inode.atime,
            }

    def readdir(self, path, fh):
        self._lookup(path)  # validate path exists (root only)
        file_list = DictEnDecoder.unpack_dir(path)
        yield "."
        yield ".."
        for _, name in file_list:
            if name in [".", ".."]:
                continue
            yield name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="waynefs.img")
    ap.add_argument("--mountpoint", default="mrt")
    args = ap.parse_args()
    FUSE(WayneFS(args.image), args.mountpoint, foreground=False)

if __name__ == "__main__":
    main()
