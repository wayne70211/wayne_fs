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
echo "Creating a 40KB file..."
dd if=/dev/zero of="$BIG_FILE" bs=1K count=40 &>/dev/null
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

echo "Cleaning up..."
rm -rf "$MNT/dir1"

echo -e "${GREEN}✅ Dentry Cache test complete. Compare the 'real' times above.${NC}"

echo -e "\n${GREEN}=== 所有測試結束 ===${NC}"