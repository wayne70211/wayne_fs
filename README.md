# Wayne File System

## Target

在這個專案中，使用 python 實作 file system 並且利用 macFUSE 掛載

分為以下幾階段實作

### 第一階段 (POSIX)
1. 建立 SuperBlock，裡面含有 patition 資訊
2. 建立 Inode Table，紀錄當前資料的資訊以及實體 offset，就是 LBA
3. 建立 Bitmap，紀錄哪些實體位置可以使用
4. 實作 getattr, readdir, mkdir, rmdir 功能，並且驗證
5. 實作 create, open, write, read 功能，並且驗證
6. 實作 truncate, rename, utimens 功能，並且驗證
7. 實作 link, chmod 功能，並且驗證

### 第二階段 
1. 實作 Ordered Journal 功能，並且驗證
2. 實作 Page Cache, D-entry Cache 功能，並且驗證

### 第三階段
1. 設計 Copy-on-Write 機制
2. 以 Device Driver 的 FTL 角度優化設計

