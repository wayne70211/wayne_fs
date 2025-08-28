#!/usr/bin/env python3
import os, struct, argparse, math, sys, time
from layout import Superblock, DictEnDecoder, Inode, InodeMode, inode_write
from disk import Disk
from bitmap import BlockBitmap

MAGIC = b"WAYNE_FS"
SB_FMT = "<8sIIIIIIIII"  # magic + 9 uint32
SB_SIZE = struct.calcsize(SB_FMT)
INODE_SIZE = 128

def ceil_div(a, b): return (a + b - 1) // b

def make_image(path, size_mb, block_size, inode_count):
    total_blocks = (size_mb * 1024 * 1024) // block_size
    if total_blocks < 1024:
        raise SystemExit("Image too small; give at least ~4MB")

    # Layout planning (very simple & static-ish for starter)
    # [0] superblock (1 block)
    sb_blocks = 1

    # Free-space bitmap: 1 bit per block
    bitmap_bits = total_blocks
    bitmap_bytes = ceil_div(bitmap_bits, 8)
    bitmap_blocks = ceil_div(bitmap_bytes, block_size)

    # inode table: fixed inode size (128B)
    INODE_SIZE = 128
    inode_bytes = inode_count * INODE_SIZE
    inode_blocks = ceil_div(inode_bytes, block_size)

    # Data region starts after sb + bitmap + inodes
    free_bitmap_start = 1  # block index after superblock
    inode_table_start  = free_bitmap_start + bitmap_blocks
    data_start         = inode_table_start + inode_blocks

    if data_start >= total_blocks:
        raise SystemExit("Layout exceeds image size; increase size or reduce inode_count")

    with open(path, "wb") as f:
        f.truncate(total_blocks * block_size)

    
    # Write superblock
    with open(path, "r+b") as f:
        sb = struct.pack(
            SB_FMT,
            MAGIC,
            block_size,
            total_blocks,
            inode_count,
            free_bitmap_start,
            bitmap_blocks,
            inode_table_start,
            inode_blocks,
            data_start,
            0,  # reserved
        )
        # pad to full block
        f.seek(0)
        f.write(sb)
        f.write(b"\x00" * (block_size - SB_SIZE))

        # Zero bitmap + inode table region explicitly (optional, but clear)
        f.seek(free_bitmap_start * block_size)
        f.write(b"\x00" * (bitmap_blocks * block_size))

        # inode table init
        f.seek(inode_table_start * block_size)
        print(inode_table_start * block_size)
        f.write(b"\x00" * (inode_blocks * block_size))

    # write first two node
    with open(path, "r+b") as f:
        root_entries = [(0, "."), (0, "..")]
        dir_bytes = DictEnDecoder.pack_dir(root_entries)
        f.seek(data_start * block_size)
        block = dir_bytes.ljust(block_size, b"\x00")
        f.write(block)

        # create root inode
        root = Inode.empty(mode=InodeMode.S_IFDIR)
        root.nlink = 2
        root.size = len(dir_bytes)
        root.direct[0] = data_start
        f.seek(inode_table_start * block_size)
        f.write(root.pack())
        # set using bitmap

        # update and write bitmap


    print(f"Created image: {path}")
    print(f"  size_mb={size_mb}, block_size={block_size}, total_blocks={total_blocks}")
    print(f"  free_bitmap_start={free_bitmap_start} blocks={bitmap_blocks}")
    print(f"  inode_table_start={inode_table_start} blocks={inode_blocks}")
    print(f"  data_start={data_start}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="waynefs.img")
    ap.add_argument("--size-mb", type=int, default=256)
    ap.add_argument("--block-size", type=int, default=4096)
    ap.add_argument("--inodes", type=int, default=4096)
    args = ap.parse_args()
    make_image(args.image, args.size_mb, args.block_size, args.inodes)

if __name__ == "__main__":
    main()
