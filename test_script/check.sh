#!/bin/bash
set -e  # 如果有錯誤立即停止
source .venv/bin/activate

MNT=./mnt     # 掛載點
TEST_DIR=$MNT/test_dir
SUB_DIR=sub_1
TEST_SUB_DIR=$TEST_DIR/$SUB_DIR

# --- 顏色定義 ---
GREEN="\033[0;32m"
RED="\033[0;31m"
YELLOW="\033[1;33m"
NC="\033[0m"

# --- 清理函式 ---
finalize() {
    echo -e "\n${YELLOW}--- Finalizing & Unmounting ---${NC}"
    umount $MNT || diskutil unmount $MNT || true
    rmdir $MNT || true
}
trap finalize EXIT

# --- 初始設定 ---
echo -e "${YELLOW}--- Initial Setup ---${NC}"
# 確保掛載點存在
if [ ! -d "$MNT" ]; then
    mkdir -p $MNT
else
    # 確保舊的掛載被清除
    umount $MNT || diskutil unmount $MNT || true
    rmdir $MNT
    mkdir -p $MNT
fi

echo "Creating new image..."
python mkwaynefs.py --image waynefs.img --size-mb 128 --block-size 4096 --inodes 1024
sleep 1
echo "Mounting waynefs..."
python waynefs.py --image waynefs.img --mountpoint $MNT &
PID=$!
sleep 2 # 等待 FUSE 完全啟動
echo "FUSE process started with PID: $PID"

# --- 功能測試 ---

echo -e "\n${YELLOW}=== 測試 1: mkdir (建立目錄) ===${NC}"
mkdir "$TEST_DIR"
if [ -d "$TEST_DIR" ]; then
    echo -e "${GREEN}✅ PASSED: Directory '$TEST_DIR' created.${NC}"
else
    echo -e "${RED}❌ FAILED: Directory '$TEST_DIR' not created.${NC}"
    exit 1
fi
OUTPUT=$(ls -la "$TEST_DIR")
if echo "$OUTPUT" | grep -q " \.$" && echo "$OUTPUT" | grep -q " \.\.$"; then
    echo -e "${GREEN}✅ PASSED: '.' and '..' entries exist.${NC}"
else
    echo -e "${RED}❌ FAILED: Missing '.' or '..' entries.${NC}"
    exit 1
fi

echo -e "\n${YELLOW}=== 測試 2: rmdir (移除目錄) ===${NC}"
mkdir "$TEST_SUB_DIR"
if [ ! -d "$TEST_SUB_DIR" ]; then
    echo -e "${RED}❌ FAILED: Sub-directory '$TEST_SUB_DIR' not created.${NC}"
    exit 1
fi
rmdir "$TEST_SUB_DIR"
if [ ! -d "$TEST_SUB_DIR" ]; then
    echo -e "${GREEN}✅ PASSED: Sub-directory '$TEST_SUB_DIR' removed.${NC}"
else
    echo -e "${RED}❌ FAILED: Sub-directory '$TEST_SUB_DIR' not removed.${NC}"
    exit 1
fi

echo -e "\n${YELLOW}=== 測試 3: create & open (建立與開啟檔案) ===${NC}"
TEST_FILE=$MNT/my_new_file.txt
touch "$TEST_FILE"
if [ ! -f "$TEST_FILE" ]; then
    echo -e "${RED}❌ FAILED: create - File not created.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ PASSED: create - File created successfully.${NC}"
FILE_INFO=$(ls -l "$TEST_FILE")
if echo "$FILE_INFO" | grep -q "^-.* 1 .* 0 "; then
    echo -e "${GREEN}✅ PASSED: File attributes are correct (nlink=1, size=0).${NC}"
else
    echo -e "${RED}❌ FAILED: Incorrect file attributes.${NC}"
    exit 1
fi
rm "$TEST_FILE"

echo -e "\n${YELLOW}=== 測試 4: write & read (讀寫檔案) ===${NC}"
WRITE_FILE=$MNT/rw_test.txt
TEST_STRING="Hello from WayneFS!"
echo -n "$TEST_STRING" > "$WRITE_FILE"
FILE_SIZE=$(stat -c %s "$WRITE_FILE" 2>/dev/null || stat -f %z "$WRITE_FILE")
EXPECTED_SIZE=${#TEST_STRING}
if [ "$FILE_SIZE" -ne "$EXPECTED_SIZE" ]; then
    echo -e "${RED}❌ FAILED: write - Incorrect file size.${NC}"
    exit 1
fi
READ_CONTENT=$(cat "$WRITE_FILE")
if [ "$READ_CONTENT" != "$TEST_STRING" ]; then
    echo -e "${RED}❌ FAILED: read - File content mismatch.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ PASSED: Write and read operations successful.${NC}"
rm "$WRITE_FILE"

echo -e "\n${YELLOW}=== 測試 5: truncate (截斷檔案) ===${NC}"
TRUNC_FILE="$MNT/trunc.txt"
echo -n "1234567890" > "$TRUNC_FILE"
truncate -s 5 "$TRUNC_FILE"
FILE_SIZE=$(stat -c %s "$TRUNC_FILE" 2>/dev/null || stat -f %z "$TRUNC_FILE")
READ_CONTENT=$(cat "$TRUNC_FILE")
if [ "$FILE_SIZE" -eq 5 ] && [ "$READ_CONTENT" == "12345" ]; then
    echo -e "${GREEN}✅ PASSED: Truncate to smaller size successful.${NC}"
else
    echo -e "${RED}❌ FAILED: Truncate to smaller size failed.${NC}"
    exit 1
fi
truncate -s 12 "$TRUNC_FILE"
FILE_SIZE=$(stat -c %s "$TRUNC_FILE" 2>/dev/null || stat -f %z "$TRUNC_FILE")
if [ "$FILE_SIZE" -eq 12 ]; then
    echo -e "${GREEN}✅ PASSED: Truncate to larger size successful.${NC}"
else
    echo -e "${RED}❌ FAILED: Truncate to larger size failed.${NC}"
    exit 1
fi
rm "$TRUNC_FILE"

echo -e "\n${YELLOW}=== 測試 6: rename (重新命名與移動) ===${NC}"
touch "$MNT/old_name.txt"
mv "$MNT/old_name.txt" "$MNT/new_name.txt"
if [ ! -f "$MNT/old_name.txt" ] && [ -f "$MNT/new_name.txt" ]; then
    echo -e "${GREEN}✅ PASSED: File rename successful.${NC}"
else
    echo -e "${RED}❌ FAILED: File rename failed.${NC}"
    exit 1
fi
mv "$MNT/new_name.txt" "$TEST_DIR/"
if [ ! -f "$MNT/new_name.txt" ] && [ -f "$TEST_DIR/new_name.txt" ]; then
    echo -e "${GREEN}✅ PASSED: File move successful.${NC}"
else
    echo -e "${RED}❌ FAILED: File move failed.${NC}"
    exit 1
fi

echo -e "\n${YELLOW}=== 測試 7: utimens (更新時間戳) ===${NC}"
TIME_TEST_FILE="$MNT/time_test.txt"
touch "$TIME_TEST_FILE"
INITIAL_MTIME=$(stat -c %Y "$TIME_TEST_FILE" 2>/dev/null || stat -f %m "$TIME_TEST_FILE")
sleep 2
touch "$TIME_TEST_FILE"
NEW_MTIME=$(stat -c %Y "$TIME_TEST_FILE" 2>/dev/null || stat -f %m "$TIME_TEST_FILE")
if [ "$NEW_MTIME" -gt "$INITIAL_MTIME" ]; then
    echo -e "${GREEN}✅ PASSED: Timestamp updated successfully.${NC}"
else
    echo -e "${RED}❌ FAILED: Timestamp not updated.${NC}"
    exit 1
fi
rm "$TIME_TEST_FILE"

echo -e "\n${YELLOW}=== 測試 8: chmod (權限變更) ===${NC}"
CHMOD_FILE="$MNT/chmod_test.txt"
touch "$CHMOD_FILE"
chmod 755 "$CHMOD_FILE"
PERMS=$(stat -c "%A" "$CHMOD_FILE" 2>/dev/null || stat -f "%Sp" "$CHMOD_FILE" | cut -c 2-10)
EXPECTED_PERMS="rwxr-xr-x"
if [[ "$PERMS" == *"$EXPECTED_PERMS"* ]]; then
    echo -e "${GREEN}✅ PASSED: chmod 755 successful.${NC}"
else
    echo -e "${RED}❌ FAILED: chmod 755 failed. Got '$PERMS'.${NC}"
    exit 1
fi
rm "$CHMOD_FILE"

echo -e "\n${YELLOW}=== 測試 9: link (硬連結) ===${NC}"
LINK_A="$MNT/link_a.txt"
LINK_B="$MNT/link_b.txt"
echo "original" > "$LINK_A"
ln "$LINK_A" "$LINK_B"
NLINK_A=$(stat -c %h "$LINK_A" 2>/dev/null || stat -f %l "$LINK_A")
if [ "$NLINK_A" -eq 2 ]; then
    echo -e "${GREEN}✅ PASSED: Hard link created, nlink is 2.${NC}"
else
    echo -e "${RED}❌ FAILED: Hard link creation failed, nlink is $NLINK_A.${NC}"
    exit 1
fi
rm "$LINK_A" "$LINK_B"

# --- 效能測試 ---

echo -e "\n${YELLOW}=== 測試 10: Page Cache Performance ===${NC}"
BIG_FILE="$MNT/big_file_for_cache_test.dat"
echo "Creating a 32MB file..."
dd if=/dev/zero of="$BIG_FILE" bs=1M count=32 &>/dev/null
echo "Performing first read (cold read from disk)..."
time cat "$BIG_FILE" > /dev/null
echo "Performing second read (warm read from cache)..."
time cat "$BIG_FILE" > /dev/null
rm "$BIG_FILE"
echo -e "${GREEN}✅ Page Cache test complete. Compare the 'real' times above.${NC}"

# --- 測試 11: Dentry Cache Performance ---
echo -e "\n${YELLOW}=== 測試 11: Dentry Cache Performance ===${NC}"
DEEP_PATH_PARTS=$(seq -f "dir%g" 1 100 | tr '\n' '/')
DEEP_PATH="$MNT/${DEEP_PATH_PARTS%/}" 
DEEP_FILE="$DEEP_PATH/final.txt"

echo "Creating a 100-level deep directory structure..."
mkdir -p "$DEEP_PATH"
touch "$DEEP_FILE"

echo "Performing first lookup (cold path)..."
time ls "$DEEP_FILE" > /dev/null
echo "Performing second lookup (warm path)..."
time ls "$DEEP_FILE" > /dev/null

echo -e "${GREEN}✅ Dentry Cache test complete. Compare the 'real' times above.${NC}"

# --- 測試 12: 間接指標 (Indirect Blocks) ---
echo -e "\n${YELLOW}=== 測試 12: 間接指標 (Indirect Blocks) ===${NC}"
BIG_FILE="$MNT/indirect_test_file.dat"
TEST_SIZE_KB=60
TEST_SIZE_BYTES=$((TEST_SIZE_KB * 1024))

echo "Creating a ${TEST_SIZE_KB}KB file to test singly indirect blocks..."
# 建立一個 60KB 的檔案 (bs=1K count=60)
dd if=/dev/zero of="$BIG_FILE" bs=1K count=${TEST_SIZE_KB} &>/dev/null

# 1. 驗證檔案大小
echo "Verifying file size..."
FILE_SIZE=$(stat -c %s "$BIG_FILE" 2>/dev/null || stat -f %z "$BIG_FILE")
if [ "$FILE_SIZE" -eq "$TEST_SIZE_BYTES" ]; then
    echo -e "${GREEN}✅ PASSED: File size is correct (${FILE_SIZE} bytes).${NC}"
else
    echo -e "${RED}❌ FAILED: File size is incorrect. Expected ${TEST_SIZE_BYTES}, Got ${FILE_SIZE}.${NC}"
    exit 1
fi

# 2. 驗證檔案內容 (透過讀取並檢查 md5/shasum)
echo "Verifying file content..."
# 計算原始 /dev/zero 區塊的校驗和
ORIG_SUM=$(dd if=/dev/zero bs=1K count=${TEST_SIZE_KB} 2>/dev/null | shasum -a 256 | cut -d' ' -f1)
# 計算讀取檔案的校驗和
READ_SUM=$(cat "$BIG_FILE" | shasum -a 256 | cut -d' ' -f1)

if [ "$ORIG_SUM" == "$READ_SUM" ]; then
    echo -e "${GREEN}✅ PASSED: File content is correct (checksum match).${NC}"
else
    echo -e "${RED}❌ FAILED: File content mismatch.${NC}"
    echo "Expected Checksum: $ORIG_SUM"
    echo "Got Checksum: $READ_SUM"
    exit 1
fi

# 3. 清理
rm "$BIG_FILE"
echo -e "${GREEN}✅ Indirect block test complete.${NC}"

# --- 測試 13: 間接指標 (縮減與釋放) ---
echo -e "\n${YELLOW}=== 測試 13: 間接指標 (縮減與釋放) ===${NC}"
TRUNC_FILE="$MNT/truncate_test_file.dat"
SIZE_BEFORE_KB=60
SIZE_AFTER_KB=20
SIZE_BEFORE_BYTES=$((SIZE_BEFORE_KB * 1024))
SIZE_AFTER_BYTES=$((SIZE_AFTER_KB * 1024))

echo "Creating a ${SIZE_BEFORE_KB}KB file..."
dd if=/dev/zero of="$TRUNC_FILE" bs=1K count=${SIZE_BEFORE_KB} &>/dev/null

# 1. 測試 truncate (縮減)
echo "Truncating file from ${SIZE_BEFORE_KB}KB down to ${SIZE_AFTER_KB}KB..."
truncate -s ${SIZE_AFTER_BYTES} "$TRUNC_FILE"

# 1a. 驗證縮減後的大小
FILE_SIZE=$(stat -c %s "$TRUNC_FILE" 2>/dev/null || stat -f %z "$TRUNC_FILE")
if [ "$FILE_SIZE" -eq "$SIZE_AFTER_BYTES" ]; then
    echo -e "${GREEN}✅ PASSED: Truncate (shrink) size is correct (${FILE_SIZE} bytes).${NC}"
else
    echo -e "${RED}❌ FAILED: TruncTATE (shrink) size is incorrect. Expected ${SIZE_AFTER_BYTES}, Got ${FILE_SIZE}.${NC}"
    exit 1
fi

# 1b. 驗證縮減後的內容
ORIG_SUM=$(dd if=/dev/zero bs=1K count=${SIZE_AFTER_KB} 2>/dev/null | shasum -a 256 | cut -d' ' -f1)
READ_SUM=$(cat "$TRUNC_FILE" | shasum -a 256 | cut -d' ' -f1)
if [ "$ORIG_SUM" == "$READ_SUM" ]; then
    echo -e "${GREEN}✅ PASSED: Truncated file content is correct.${NC}"
else
    echo -e "${RED}❌ FAILED: Truncated file content mismatch.${NC}"
    exit 1
fi

# 2. 測試 unlink (已縮減的檔案)
rm "$TRUNC_FILE"
if [ ! -f "$TRUNC_FILE" ]; then
    echo -e "${GREEN}✅ PASSED: unlink (after truncate) successful.${NC}"
else
    echo -e "${RED}❌ FAILED: unlink (after truncate) failed.${NC}"
    exit 1
fi

# 3. 測試 unlink (直接刪除大檔案)
echo "Creating another ${SIZE_BEFORE_KB}KB file..."
dd if=/dev/zero of="$TRUNC_FILE" bs=1K count=${SIZE_BEFORE_KB} &>/dev/null
rm "$TRUNC_FILE"
if [ ! -f "$TRUNC_FILE" ]; then
    echo -e "${GREEN}✅ PASSED: unlink (large file) successful.${NC}"
else
    echo -e "${RED}❌ FAILED: unlink (large file) failed.${NC}"
    exit 1
fi

# 4. 測試空間回收 (最重要)
echo "Verifying space reclamation by creating a new large file..."
dd if=/dev/zero of="$TRUNC_FILE" bs=1K count=${SIZE_BEFORE_KB} &>/dev/null
FILE_SIZE=$(stat -c %s "$TRUNC_FILE" 2>/dev/null || stat -f %z "$TRUNC_FILE")
if [ "$FILE_SIZE" -eq "$SIZE_BEFORE_BYTES" ]; then
    echo -e "${GREEN}✅ PASSED: Space reclamation successful.${NC}"
else
    echo -e "${RED}❌ FAILED: Space reclamation failed. Could not create new file.${NC}"
    exit 1
fi
rm "$TRUNC_FILE"

echo -e "${GREEN}✅ Indirect block shrinking and freeing tests complete.${NC}"

# --- 測試 14: 符號連結 (Symbolic Links) ---
echo -e "\n${YELLOW}=== 測試 14: 符號連結 (Symbolic Links) ===${NC}"
TARGET_FILE="$MNT/target_file.txt"
LINK_NAME="$MNT/link_to_target1"
echo "I am the target" > "$TARGET_FILE"

# 1. 測試 symlink 建立
echo "Creating symbolic link..."
# 確保連結不存在
rm -f "$LINK_NAME"
# 使用相對於連結位置的 target 路徑
ln -s "target_file.txt" "$LINK_NAME" # <-- 正確的相對路徑！
if [ ! -L "$LINK_NAME" ]; then
    echo -e "${RED}❌ FAILED: Symbolic link not created.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ PASSED: Symbolic link created.${NC}"

# 2. 測試 readlink
echo "Testing readlink..."
LINK_PATH=$(readlink "$LINK_NAME")
# 預期 readlink 回傳我們儲存的路徑字串
EXPECTED_LINK_PATH="target_file.txt" # <-- 預期相對路徑
if [ "$LINK_PATH" == "$EXPECTED_LINK_PATH" ]; then
    echo -e "${GREEN}✅ PASSED: readlink returns correct path ('$LINK_PATH').${NC}"
else
    echo -e "${RED}❌ FAILED: readlink returned '$LINK_PATH', expected '$EXPECTED_LINK_PATH'.${NC}"
    exit 1
fi

# 3. 測試跟隨 (Following) 連結
echo "Testing following link (cat)..."
CONTENT=$(cat "$LINK_NAME")
if [ "$CONTENT" == "I am the target" ]; then
    echo -e "${GREEN}✅ PASSED: Following link to read content successful.${NC}"
else
    echo -e "${RED}❌ FAILED: Content mismatch when following link. Got '$CONTENT'.${NC}"
    exit 1
fi

# 4. 測試 unlink 連結
echo "Testing unlink of the link..."
rm "$LINK_NAME"
if [ -L "$LINK_NAME" ]; then # 檢查連結是否已被移除
    echo -e "${RED}❌ FAILED: unlink did not remove the link.${NC}"
    exit 1
fi
if [ ! -f "$TARGET_FILE" ]; then # 檢查目標檔案是否還在
    echo -e "${RED}❌ FAILED: unlink incorrectly removed the target file.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ PASSED: unlink successful (target file remains).${NC}"

# 清理目標檔案
rm "$TARGET_FILE"
echo -e "${GREEN}✅ Symbolic link tests complete.${NC}"

# --- 測試 15: 檔案系統統計 (statfs / df) ---
echo -e "\n${YELLOW}=== 測試 15: 檔案系統統計 (statfs / df) ===${NC}"

echo "Running df -h on the mount point..."
# 執行 df 指令並將輸出存到變數
DF_OUTPUT=$(df -h "$MNT")

# 檢查 df 指令是否成功
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ PASSED: 'df -h' command executed successfully.${NC}"
    echo "--- df output ---"
    echo "$DF_OUTPUT" # 印出 df 的結果
    echo "-----------------"
else
    echo -e "${RED}❌ FAILED: 'df -h' command failed.${NC}"
    exit 1
fi

# (可選) 檢查輸出是否包含掛載點路徑
if echo "$DF_OUTPUT" | grep -q "$MNT"; then
    echo -e "${GREEN}✅ PASSED: df output includes the mount point path.${NC}"
else
    echo -e "${RED}❌ FAILED: df output does not seem to include the mount point path.${NC}"
    # 我們不 exit 1，因為不同系統的 df 格式可能略有差異
fi

echo -e "${GREEN}✅ statfs tests complete.${NC}"


# --- 測試 16: statfs 內部計數驗證 ---
echo -e "\n${YELLOW}=== 測試 16: statfs 內部計數驗證 ===${NC}"
TEST_FILE_STATFS="$MNT/statfs_test.txt"
TEST_DIR_STATFS="$MNT/statfs_dir"
FUSE_LOG="fuse_output.log" # 將 FUSE log 導向檔案

# 0. 重啟 FUSE 並將 log 導向檔案
echo "Restarting FUSE with log redirection..."
if [ ! -z "$PID" ] && ps -p $PID > /dev/null; then # 檢查 PID 是否存在
    echo "Killing previous FUSE process $PID..."
    kill -9 $PID || true # 強制 kill
fi
umount "$MNT" || diskutil unmount force "$MNT" || true # 同時嘗試 umount 和 diskutil
sleep 1
# 這次不加 &，讓 FUSE 在前景執行，但把 stdout/stderr 導到 log 檔
python waynefs.py --image waynefs.img --mountpoint $MNT --foreground 1 > "$FUSE_LOG" 2>&1 &
PID=$!
sleep 4 # 等待 FUSE 啟動

# 1. 觸發 statfs 並獲取初始值
echo "Getting initial statfs values..."
df "$MNT" > /dev/null # <-- 修改：使用 df 觸發 statfs
sleep 3 # 等待 log 寫入
INITIAL_FFREE=$(grep 'statfs: f_files=' "$FUSE_LOG" | tail -1 | sed -n 's/.*f_ffree=\([0-9]*\).*/\1/p')
INITIAL_BFREE=$(grep 'statfs: f_blocks=' "$FUSE_LOG" | tail -1 | sed -n 's/.*f_bfree=\([0-9]*\).*/\1/p')

if [ -z "$INITIAL_FFREE" ] || [ -z "$INITIAL_BFREE" ]; then
    echo -e "${RED}❌ FAILED: Could not parse initial statfs values from log.${NC}"
    echo "--- FUSE Log ---" # 提供更多除錯資訊
    cat "$FUSE_LOG"
    echo "----------------"
    exit 1
fi
echo "Initial values: f_ffree=$INITIAL_FFREE, f_bfree=$INITIAL_BFREE"

# 2. 建立目錄和檔案 (預計使用 2 inodes, 2 blocks)
echo "Creating 1 directory and 1 file (1 block)..."
mkdir "$TEST_DIR_STATFS"
# 建立一個剛好 4KB 的檔案
dd if=/dev/zero of="$TEST_FILE_STATFS" bs=4K count=1 &>/dev/null

# 3. 觸發 statfs 並獲取最終值
echo "Getting final statfs values..."
df "$MNT" > /dev/null # <-- 修改：再次使用 df 觸發 statfs
sleep 3 # 等待 log 寫入
FINAL_FFREE=$(grep 'statfs: f_files=' "$FUSE_LOG" | tail -1 | sed -n 's/.*f_ffree=\([0-9]*\).*/\1/p')
FINAL_BFREE=$(grep 'statfs: f_blocks=' "$FUSE_LOG" | tail -1 | sed -n 's/.*f_bfree=\([0-9]*\).*/\1/p')

if [ -z "$FINAL_FFREE" ] || [ -z "$FINAL_BFREE" ]; then
    echo -e "${RED}❌ FAILED: Could not parse final statfs values from log.${NC}"
    echo "--- FUSE Log ---"
    cat "$FUSE_LOG"
    echo "----------------"
    exit 1
fi
echo "Final values: f_ffree=$FINAL_FFREE, f_bfree=$FINAL_BFREE"

# 4. 驗證變化量
EXPECTED_FFREE_CHANGE=4
EXPECTED_BFREE_CHANGE=4
ACTUAL_FFREE_CHANGE=$((INITIAL_FFREE - FINAL_FFREE))
ACTUAL_BFREE_CHANGE=$((INITIAL_BFREE - FINAL_BFREE))

echo "Verifying changes..."
FFREE_PASSED=false
BFREE_PASSED=false
if [ "$ACTUAL_FFREE_CHANGE" -eq "$EXPECTED_FFREE_CHANGE" ]; then
    echo -e "${GREEN}✅ PASSED: Inode free count decreased correctly by $ACTUAL_FFREE_CHANGE.${NC}"
    FFREE_PASSED=true
else
    echo -e "${RED}❌ FAILED: Inode free count decreased by $ACTUAL_FFREE_CHANGE, expected $EXPECTED_FFREE_CHANGE.${NC}"
fi

if [ "$ACTUAL_BFREE_CHANGE" -eq "$EXPECTED_BFREE_CHANGE" ]; then
    echo -e "${GREEN}✅ PASSED: Block free count decreased correctly by $ACTUAL_BFREE_CHANGE.${NC}"
    BFREE_PASSED=true
else
    echo -e "${RED}❌ FAILED: Block free count decreased by $ACTUAL_BFREE_CHANGE, expected $EXPECTED_BFREE_CHANGE.${NC}"
fi

# 清理
rm "$TEST_FILE_STATFS"
rmdir "$TEST_DIR_STATFS"
rm "$FUSE_LOG" # 清理 log 檔案

if ! $FFREE_PASSED || ! $BFREE_PASSED ; then
    exit 1
fi

echo -e "${GREEN}✅ statfs internal count verification complete.${NC}"

echo "Cleaning up..."
rm -rf "$MNT/dir1"

echo -e "\n${GREEN}=== 所有測試結束 ===${NC}"