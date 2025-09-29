#!/usr/bin/env python3
import os, struct, argparse, math, sys, time
from layout import MAGIC, SB_FMT, SB_SIZE, INODE_SIZE, Superblock, DictEnDecoder, Inode, InodeMode, InodeTable
from disk import Disk
from bitmap import InodeBitmap, BlockBitmap

ROOT_INO = 0 

def ceil_div(a, b): return (a + b - 1) // b

def make_image(path, size_mb, block_size, inode_count):
    total_blocks = (size_mb * 1024 * 1024) // block_size
    if total_blocks < 1024:
        raise SystemExit("Image too small; give at least ~4MB")

    # Layout planning (very simple & static-ish for starter)
    # [0] superblock (1 block)
    sb_blocks = 1

    # <<< NEW: Inode Bitmap Calculation >>>
    inode_bitmap_bits = inode_count
    inode_bitmap_bytes = ceil_div(inode_bitmap_bits, 8)
    inode_bitmap_blocks = ceil_div(inode_bitmap_bytes, block_size)

    # Block Bitmap calculation (unchanged)
    block_bitmap_bits = total_blocks
    block_bitmap_bytes = ceil_div(block_bitmap_bits, 8)
    block_bitmap_blocks = ceil_div(block_bitmap_bytes, block_size)

    # inode table: fixed inode size (128B)
    INODE_SIZE = 128
    inode_bytes = inode_count * INODE_SIZE
    inode_blocks = ceil_div(inode_bytes, block_size)

    # Data region starts after sb + bitmap + inodes
    inode_bitmap_start = sb_blocks
    block_bitmap_start = inode_bitmap_start + inode_bitmap_blocks
    inode_table_start  = block_bitmap_start + block_bitmap_blocks
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
            inode_bitmap_start,
            inode_bitmap_blocks,
            block_bitmap_start,
            block_bitmap_blocks,
            inode_table_start,
            inode_blocks,
            data_start,
            0,  # reserved
        )
        f.seek(0)
        f.write(sb)
        f.write(b"\x00" * (block_size - SB_SIZE))

        f.seek(inode_bitmap_start * block_size)
        f.write(b"\x00" * (inode_bitmap_blocks * block_size))

        f.seek(block_bitmap_start * block_size)
        f.write(b"\x00" * (block_bitmap_blocks * block_size))
        f.seek(inode_table_start * block_size)
        f.write(b"\x00" * (inode_blocks * block_size))


    disk = Disk(path)
    sb = Superblock.load(disk)
    inode_bitmap = InodeBitmap(disk, sb) 
    block_bitmap = BlockBitmap(disk, sb)
    inode_table = InodeTable(disk, sb)

    # write first two node in data start
    root_blk = sb.data_start
    root_entries = [(0, "."), (0, "..")]
    raw_data = DictEnDecoder.pack_dir(root_entries)
    disk.write_block(root_blk, raw_data + b"\x00" * (sb.block_size - len(raw_data)))

    # write root inode into indoe table
    root_inode = Inode.empty(mode=InodeMode.S_IFDIR)
    root_inode.nlink = 2
    root_inode.size  = len(raw_data)
    root_inode.direct[0] = sb.data_start
    inode_table.write(ROOT_INO, root_inode)

    inode_bitmap.set_used(ROOT_INO)
    inode_bitmap.flush()

    # update the valid bitmap
    # set used for all blk before data_start 
    for ino in range(root_blk + 1):
        block_bitmap.set_used(ino)
    # set root data is used
    block_bitmap.flush()
    disk.fsync()

    print(f"Created image: {path}")
    print(f"  size_mb={size_mb}, block_size={block_size}, total_blocks={total_blocks}")
    print(f"  inode_bitmap_start={inode_bitmap_start} blocks={inode_bitmap_blocks}")
    print(f"  block_bitmap_start={block_bitmap_start} blocks={block_bitmap_blocks}")
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
