# Wayne File System

## Target

在這個專案中，使用 python 建立 file system 並且串接 VFS 介面，利用 FUSE 可以成功掛載 image 成 disk 

### 第一階段
1. 建立 SuperBlock，裡面含有 patition 資訊
2. 建立 Inode Table，紀錄當前資料的資訊以及實體 offset，就是 LBA
3. 建立 Bitmap，紀錄哪些實體位置可以使用

### 第二階段
1. 實作 getattr, readdir, mkdir, rmdir 等功能，並且利用 `ls -la` 驗證
2. 實際 write file 並且根據 `hexdump -C` 確認 SuperBlock 正確性
3. 確認 bitmap 正確性

### 第三階段
1. 思考是否能以 FTL 角度優化此設計

### Doc

#### SuperBlock
- Info Area
- Bitmap
- Inode Table


#### Inode Table
- Inode
  - type
  - nlink
  - size
  - ctime
  - mtime
  - atime
  - direct
  - reserved

#### Need Support Function
- getattr
  - input: path
  - output: inode property
  1. Split path by "/"
  2. Search name from root inode by Inode Table
  3. Return inode property
- readdir
  - input: path
  - output: subtree file name
  1. Split path by "/"
  2. Read entries by inode
  3. Return all name of entries
- mkdir
  - input: path
  1. Split the path as parent_path and curr dir name
  2. Check the mode of parent inode of parent_path is DIR or not
  3. Check if curr dir name is existed in entries of parent inode
  4. Allocate a new block and create a new inode for curr dir
  5. Write init entries to new block, "." for curr level, ".." for parent level
  6. Update curr inode property and insert curr inode to **Inode Table**
  7. Add curr inode to entries of parent inode and update it
  8. Flush disk

