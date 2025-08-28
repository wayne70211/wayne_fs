## Wayne File System

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

