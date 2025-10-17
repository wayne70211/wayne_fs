#!/usr/bin/env python3
import struct, argparse
from journal import JournalSuperblock, JOURNAL_SB_MAGIC
from layout import MAGIC, SB_FMT, SB_SIZE, INODE_SIZE, Superblock, DictEnDecoder, Inode, InodeMode, InodeTable, ceil_div
from disk import Disk
from bitmap import InodeBitmap, BlockBitmap

ROOT_INO = 0 

def make_image(path, size_mb, block_size, inode_count, journal_size):
    total_blocks = (size_mb * 1024 * 1024) // block_size
    if total_blocks < 1024:
        raise SystemExit("Image too small; give at least ~4MB")

    sb_blocks = 1

    # Inode Bitmap Calculation
    inode_bitmap_bits = inode_count
    inode_bitmap_bytes = ceil_div(inode_bitmap_bits, 8)
    inode_bitmap_blocks = ceil_div(inode_bitmap_bytes, block_size)

    # Block Bitmap calculation
    block_bitmap_bits = total_blocks
    block_bitmap_bytes = ceil_div(block_bitmap_bits, 8)
    block_bitmap_blocks = ceil_div(block_bitmap_bytes, block_size)

    # inode table: fixed inode size (128B)
    inode_bytes = inode_count * INODE_SIZE
    inode_blocks = ceil_div(inode_bytes, block_size)

    # Data region starts after sb + bitmap + inodes
    inode_bitmap_start = sb_blocks
    block_bitmap_start = inode_bitmap_start + inode_bitmap_blocks
    inode_table_start  = block_bitmap_start + block_bitmap_blocks

    journal_area_start = inode_table_start + inode_blocks
    journal_area_blocks = ceil_div(journal_size, block_size)

    data_start         = journal_area_start + journal_area_blocks

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
            journal_area_start,
            journal_area_blocks,
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
    
    # Journal Superblock
    log_start_block = journal_area_start + 1
    log_num_blocks = journal_area_blocks - 1

    initial_journal_sb = JournalSuperblock(
        magic=JOURNAL_SB_MAGIC,
        start_block=log_start_block,
        num_blocks=log_num_blocks,
        head=log_start_block,  # head 和 tail 都從紀錄區的開頭開始
        tail=log_start_block,
        last_tid=0             # 初始 TID 為 0
    )

    raw_jsb = initial_journal_sb.pack()
    disk.write_block(journal_area_start, raw_jsb.ljust(sb.block_size, b'\x00'))
    
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
    print("=" * 50)
    print(f"{'Field':22} | {'Value':10} | {'Blocks'}")
    print("-" * 50)
    print(f"{'inode_bitmap_start':22} | {inode_bitmap_start:<10} | {inode_bitmap_blocks}")
    print(f"{'block_bitmap_start':22} | {block_bitmap_start:<10} | {block_bitmap_blocks}")
    print(f"{'inode_table_start':22} | {inode_table_start:<10} | {inode_blocks}")
    print(f"{'journal_area_start':22} | {journal_area_start:<10} | {journal_area_blocks}")
    print(f"{'data_start':22} | {data_start:<10} | {'-'}")
    print("=" * 50)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="waynefs.img")
    ap.add_argument("--size-mb", type=int, default=128)
    ap.add_argument("--block-size", type=int, default=4096)
    ap.add_argument("--inodes", type=int, default=1024)
    ap.add_argument("--journal-size", type=int, default=10*4096)
    args = ap.parse_args()
    make_image(args.image, args.size_mb, args.block_size, args.inodes, args.journal_size)

if __name__ == "__main__":
    main()
