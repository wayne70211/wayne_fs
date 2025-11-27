# Wayne File System

## Target

在這個專案中，使用 python 實作 file system 並且利用 macFUSE 掛載

分為以下幾階段實作

### 第一階段 (基本 CRUD 功能)
1. 建立 SuperBlock，裡面含有 patition 資訊
2. 建立 Inode Table，紀錄當前資料的資訊以及實體 offset，就是 LBA
3. 建立 Bitmap，紀錄哪些實體位置可以使用
4. 實作 getattr, readdir, mkdir, rmdir 功能，並且驗證
5. 實作 create, open, write, read 功能，並且驗證
6. 實作 truncate, rename, utimens 功能，並且驗證
7. 實作 link, chmod 功能，並且驗證

### 第二階段 (Journal 功能，擴充大小)
1. 實作 Ordered Journal 功能，並且驗證
2. 實作 Page Cache, D-entry Cache 功能，並且驗證
3. 實作 Indirect Blocks，使檔案大小可以突破原本 direct 指標只有 12 組的限制，並且驗證
    - 定義 direct[10] 為單層間接指標
    - 定義 direct[11] 為雙層間接指標
    - 最大檔案寫入可擴充至 (10 + 1024 + 1024 * 1024) * chunk_size = 40KB + 4MB + 4GB ~= 4GB
4. 實作 symlink, readlink 功能，並且驗證
5. 實作 statfs 功能，並且驗證

### 第三階段 (Cache 功能優化以及 JBD2 導入)
1. 修改 Cache 機制，從 Write-Through 機制 (每次寫入 page cache 後立刻寫入硬碟) 改成 Write-Back (標記 page cache dirty 等 VFS 下 fsync 才寫入硬碟)

### 第四階段 (CoW 機制) [branch: ]
1. 設計 Copy-on-Write 機制

