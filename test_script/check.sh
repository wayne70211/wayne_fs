#!/bin/bash
set -e  # 如果有錯誤立即停止
source .venv/bin/activate

MNT=./mnt     # 掛載點
TEST_DIR=$MNT/test_dir
SUB_DIR=sub_1
TEST_SUB_DIR=$TEST_DIR/$SUB_DIR

GREEN="\033[0;32m"
RED="\033[0;31m"
NC="\033[0m"

finalize() {
    echo "Unmounting..."
    umount mnt || diskutil unmount mnt || true
    rmdir mnt
}

trap finalize EXIT

# 確保掛載點存在
if [ ! -d "$MNT" ]; then
    mkdir -p $MNT
else
    umount mnt || diskutil unmount mnt || true
    rmdir mnt
    mkdir -p $MNT
fi

echo "Creating image..."
python mkwaynefs.py --image waynefs.img --size-mb 64 --block-size 4096 --inodes 1024
sleep 1   # 等待掛載完成
echo "Mounting..."
# 在指令結尾加上 '&'，讓 FUSE 在背景執行
python waynefs.py --image waynefs.img --mountpoint $MNT &

# 使用 '$!' 這個特殊變數來取得「最後一個」背景程式的 PID
PID=$!

# 等待 FUSE 完全準備好的時間可能需要久一點，增加到 2 秒比較保險
sleep 2
echo "FUSE process started with PID: $PID"

echo "=== 測試 mkdir ==="
# 嘗試建立目錄
mkdir $TEST_DIR

# 驗證目錄是否存在
if [ -d "$TEST_DIR" ]; then
    echo -e "${GREEN}✅ 測試通過：$TEST_DIR 已建立${NC}"
else
    echo -e "${RED}❌ 測試失敗：$TEST_DIR 沒有建立${NC}"
    exit 1
fi

OUTPUT=$(ls -la $TEST_DIR)

if echo "$OUTPUT" | grep -q " \.$" && echo "$OUTPUT" | grep -q " \.\.$"; then
    echo -e "${GREEN}✅ 測試通過：. 和 .. 存在${NC}"
    echo "$OUTPUT"
else
    echo -e "${RED}❌ 測試失敗：缺少 . 或 ..${NC}"
    echo "$OUTPUT"
    exit 1
fi

echo "=== 測試 rmdir ==="

mkdir $TEST_SUB_DIR

# 驗證目錄是否存在
if [ -d "$TEST_SUB_DIR" ]; then
    echo -e "${GREEN}✅ 測試通過：$TEST_SUB_DIR 已建立${NC}"
else
    echo -e "${RED}❌ 測試失敗：$TEST_SUB_DIR 沒有建立${NC}"
    exit 1
fi

OUTPUT=$(ls -la $TEST_DIR)

if echo "$OUTPUT" | grep -q " \.$" && echo "$OUTPUT" | grep -q " \.\.$" && echo "$OUTPUT" | grep -q $SUB_DIR"$"; then
    echo -e "${GREEN}✅ 測試通過：$TEST_SUB_DIR 存在${NC}"
    echo "$OUTPUT"
else
    echo -e "${RED}❌ 測試失敗：缺少 $TEST_SUB_DIR ${NC}"
    echo "$OUTPUT"
    exit 1
fi

rm -f $TEST_DIR/._* 
rmdir $TEST_SUB_DIR

OUTPUT=$(ls -la $TEST_DIR)

# 驗證目錄是否存在
if [ ! -d "$TEST_SUB_DIR" ] && echo "$OUTPUT" | grep -q " \.$" && echo "$OUTPUT" | grep -q " \.\.$" && ! echo "$OUTPUT" | grep -q $SUB_DIR"$"; then
    echo -e "${GREEN}✅ 測試通過：$TEST_SUB_DIR 已刪除${NC}"
    echo "$OUTPUT"
else
    echo -e "${RED}❌ 測試失敗：$TEST_SUB_DIR 沒有刪除${NC}"
    exit 1
fi


echo "=== 測試 create 與 open ==="
TEST_FILE=$MNT/my_new_file.txt

# 測試 create 功能
touch $TEST_FILE

# 驗證檔案是否存在、大小為 0、連結數為 1
if [ -f "$TEST_FILE" ]; then
    echo -e "${GREEN}✅ 測試通過：create - 檔案已建立 ($TEST_FILE)${NC}"
else
    echo -e "${RED}❌ 測試失敗：create - 檔案沒有建立${NC}"
    exit 1
fi

# 使用 ls -l 和 grep 來精確檢查屬性
FILE_INFO=$(ls -l "$TEST_FILE")
# 檢查 link count (第二欄) 是否為 1，和 size (第五欄) 是否為 0
if echo "$FILE_INFO" | grep -q "^-.* 1 .* 0 "; then
    echo -e "${GREEN}✅ 測試通過：檔案屬性正確 (nlink=1, size=0)${NC}"
else
    echo -e "${RED}❌ 測試失敗：檔案屬性不正確 (nlink/size 有誤)${NC}"
    echo "檔案資訊: $FILE_INFO"
    exit 1
fi

# 測試 create 錯誤處理 (檔案已存在)
# '!' 會反轉結束碼，如果 touch 失敗 (我們預期的)，則 if 條件為真
if ! mkdir $TEST_FILE 2>/dev/null; then
    echo -e "${GREEN}✅ 測試通過：create - 正確處理已存在路徑的錯誤${NC}"
else
    echo -e "${RED}❌ 測試失敗：create - 沒有處理已存在路徑的錯誤${NC}"
    exit 1
fi

# 測試 open 功能 (透過 cat 間接測試)
# 如果 open 失敗，'set -e' 會讓腳本在此停止
cat $TEST_FILE > /dev/null
echo -e "${GREEN}✅ 測試通過：open - 成功開啟並讀取空檔案${NC}"

# 測試後清理
rm $TEST_FILE
if [ ! -f "$TEST_FILE" ]; then
    echo -e "${GREEN}✅ 測試通過：檔案已成功刪除${NC}"
else
    echo -e "${RED}❌ 測試失敗：檔案刪除失敗${NC}"
    exit 1
fi

echo "=== 測試 write 與 read ==="
WRITE_FILE=$MNT/rw_test.txt
TEST_STRING="Hello from WayneFS in Banqiao, New Taipei City!"

# 1. 測試 write：使用 echo 寫入一個字串
echo -n "$TEST_STRING" > "$WRITE_FILE"

# 驗證 write 是否成功 (檢查檔案大小是否正確)
FILE_SIZE=$(stat -c %s "$WRITE_FILE" 2>/dev/null || stat -f %z "$WRITE_FILE")
EXPECTED_SIZE=${#TEST_STRING}

if [ "$FILE_SIZE" -eq "$EXPECTED_SIZE" ]; then
    echo -e "${GREEN}✅ 測試通過：write - 檔案大小正確 ($FILE_SIZE bytes)${NC}"
else
    echo -e "${RED}❌ 測試失敗：write - 檔案大小錯誤 (應為 $EXPECTED_SIZE, 實際為 $FILE_SIZE)${NC}"
    exit 1
fi

# 2. 測試 read：使用 cat 讀出內容並比較
READ_CONTENT=$(cat "$WRITE_FILE")

if [ "$READ_CONTENT" == "$TEST_STRING" ]; then
    echo -e "${GREEN}✅ 測試通過：read - 檔案內容正確${NC}"
else
    echo -e "${RED}❌ 測試失敗：read - 檔案內容不符${NC}"
    echo "應為: $TEST_STRING"
    echo "讀到: $READ_CONTENT"
    exit 1
fi

# 3. 清理測試檔案
rm "$WRITE_FILE"
echo -e "${GREEN}✅ 測試通過：讀寫測試檔案已刪除${NC}"

# 3: 測試 truncate
echo -e "\n${YELLOW}--- 測試 3: 檔案截斷 (truncate) ---${NC}"
TRUNC_FILE="$MNT/trunc.txt"
echo -n "1234567890" > "$TRUNC_FILE"

truncate -s 5 "$TRUNC_FILE"
# 再次取得檔案大小
if [[ "$OSTYPE" == "darwin"* ]]; then
    FILE_SIZE=$(stat -f %z "$TRUNC_FILE")
else
    FILE_SIZE=$(stat -c %s "$TRUNC_FILE")
fi

READ_CONTENT=$(cat "$TRUNC_FILE")
if [ "$FILE_SIZE" -eq 5 ] && [ "$READ_CONTENT" == "12345" ]; then
    echo -e "${GREEN}✅ 測試通過：'truncate' 縮小檔案成功。${NC}"
else
    echo -e "${RED}❌ 測試失敗：'truncate' 縮小檔案失敗。大小: $FILE_SIZE, 內容: $READ_CONTENT。${NC}"
    exit 1
fi

truncate -s 12 "$TRUNC_FILE"
# 再次取得檔案大小
if [[ "$OSTYPE" == "darwin"* ]]; then
    FILE_SIZE=$(stat -f %z "$TRUNC_FILE")
else
    FILE_SIZE=$(stat -c %s "$TRUNC_FILE")
fi

if [ "$FILE_SIZE" -eq 12 ]; then
    echo -e "${GREEN}✅ 測試通過：'truncate' 擴大檔案成功。${NC}"
else
    echo -e "${RED}❌ 測試失敗：'truncate' 擴大檔案失敗。大小: $FILE_SIZE。${NC}"
    exit 1
fi
rm "$TRUNC_FILE"

# 測試 4: rename
echo -e "\n${YELLOW}--- 測試 4: 重新命名與移動 (rename/mv) ---${NC}"
# 檔案重新命名
touch "$MNT/old_name.txt"
mv "$MNT/old_name.txt" "$MNT/new_name.txt"
if [ ! -f "$MNT/old_name.txt" ] && [ -f "$MNT/new_name.txt" ]; then
    echo -e "${GREEN}✅ 測試通過：檔案重新命名成功。${NC}"
else
    ls -la $MNT
    echo -e "${RED}❌ 測試失敗：檔案重新命名失敗。${NC}"
    exit 1
fi

# 檔案移動
mv "$MNT/new_name.txt" "$TEST_DIR/"
if [ ! -f "$MNT/new_name.txt" ] && [ -f "$TEST_DIR/new_name.txt" ]; then
    echo -e "${GREEN}✅ 測試通過：檔案移動至目錄成功。${NC}"
else
    echo -e "${RED}❌ 測試失敗：檔案移動失敗。${NC}"
    exit 1
fi

# 目錄移動
mkdir "$MNT/d_to_move"
mv "$MNT/d_to_move" "$TEST_DIR/"
if [ ! -d "$MNT/d_to_move" ] && [ -d "$TEST_DIR/d_to_move" ]; then
    echo -e "${GREEN}✅ 測試通過：目錄移動成功。${NC}"
else
    echo -e "${RED}❌ 測試失敗：目錄移動失敗。${NC}"
    exit 1
fi

# 測試 5: utimens (更新時間戳)
echo -e "\n${YELLOW}--- 測試 5: 時間戳更新 (utimens/touch) ---${NC}"
TIME_TEST_FILE="$MNT/time_test.txt"

# 1. 建立一個新檔案
touch "$TIME_TEST_FILE"
echo -e "已建立測試檔案 '$TIME_TEST_FILE'。"

# 2. 取得原始的修改時間 (mtime)
#    stat 指令在 macOS 和 Linux 上的參數不同，這裡做了相容性處理
if [[ "$OSTYPE" == "darwin"* ]]; then
    INITIAL_MTIME=$(stat -f %m "$TIME_TEST_FILE")
else
    INITIAL_MTIME=$(stat -c %Y "$TIME_TEST_FILE")
fi
echo "初始修改時間 (mtime): $INITIAL_MTIME"

# 3. 等待 2 秒，以確保時間戳會有明顯的變化
echo "等待 2 秒..."
sleep 2

# 4. 再次 touch 同一個檔案，這將會觸發 utimens
touch "$TIME_TEST_FILE"

# 5. 取得新的修改時間
if [[ "$OSTYPE" == "darwin"* ]]; then
    NEW_MTIME=$(stat -f %m "$TIME_TEST_FILE")
else
    NEW_MTIME=$(stat -c %Y "$TIME_TEST_FILE")
fi
echo "新的修改時間 (mtime): $NEW_MTIME"

# 6. 比較兩個時間戳
if [ "$NEW_MTIME" -gt "$INITIAL_MTIME" ]; then
    echo -e "${GREEN}✅ 測試通過：'touch' 一個已存在的檔案成功更新了時間戳。${NC}"
else
    echo -e "${RED}❌ 測試失敗：時間戳沒有被更新。初始: $INITIAL_MTIME, 新的: $NEW_MTIME。${NC}"
    exit 1
fi

# 7. 清理
rm "$TIME_TEST_FILE"

# --- 測試 6: 權限變更 (chmod) ---
echo -e "\n${YELLOW}--- 測試 6: 權限變更 (chmod) ---${NC}"
CHMOD_FILE="$MNT/chmod_test.txt"
touch "$CHMOD_FILE"

# 預設權限通常是 0644 (-rw-r--r--)
# 我們把它改成 0755 (-rwxr-xr-x)
chmod 755 "$CHMOD_FILE"

# 檢查權限是否變更
if [[ "$OSTYPE" == "darwin"* ]]; then
    PERMS=$(stat -f "%Sp" "$CHMOD_FILE" | cut -c 2-10) # macOS 格式
else
    PERMS=$(stat -c "%A" "$CHMOD_FILE" | cut -c 2-10) # Linux 格式
fi

if [ "$PERMS" == "rwxr-xr-x" ]; then
    echo -e "${GREEN}✅ 測試通過：'chmod 755' 成功。${NC}"
else
    echo -e "${RED}❌ 測試失敗：'chmod 755' 後權限不符。應為 rwxr-xr-x, 實際為 $PERMS。${NC}"
    exit 1
fi
rm "$CHMOD_FILE"

# 清理
echo "=== 測試結束 ==="