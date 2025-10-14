#!/bin/bash
set -e
source .venv/bin/activate

MNT=./mnt
IMG=waynefs.img
TEST_DIR=$MNT/recovery_test

GREEN="\033[0;32m"
RED="\033[0;31m"
NC="\033[0m"

finalize() {
    echo "--- Finalizing ---"
    # 檢查 FUSE 行程是否還在，如果在就殺掉
    if [ ! -z "$FUSE_PID" ] && ps -p $FUSE_PID > /dev/null; then
        echo "Killing FUSE process $FUSE_PID..."
        kill -9 $FUSE_PID || true
    fi
    # 解除掛載
    echo "Unmounting $MNT..."
    umount $MNT || diskutil unmount $MNT || true
    # 刪除掛載點
    rmdir $MNT || true
    echo "Cleanup complete."
}
# 無論腳本如何結束（正常或錯誤），都執行 finalize 函式
trap finalize EXIT

# --- 準備階段 ---
echo "--- Preparing environment ---"
# 清理舊的掛載點
if [ -d "$MNT" ]; then
    umount $MNT || diskutil unmount $MNT || true
    rmdir $MNT
fi
mkdir -p $MNT

# 建立一個全新的、乾淨的映像檔
echo "Creating fresh image: $IMG..."
python mkwaynefs.py --image $IMG

# --- 測試階段 ---
echo -e "\n--- Starting Test: Crash after mkdir ---"

# 1. 在背景掛載檔案系統
echo "Mounting filesystem in background..."
python waynefs.py --image $IMG --mountpoint $MNT --foreground 1 &
FUSE_PID=$!
sleep 2 # 等待 FUSE 完全啟動
echo "Filesystem mounted with PID: $FUSE_PID"

# 2. 執行一個會寫入中繼資料的操作 (mkdir)
echo "Performing mkdir operation..."
mkdir $TEST_DIR
echo "mkdir command finished."

# 3. 立刻模擬斷電！
echo "!!! Simulating crash: Killing filesystem process !!!"
kill -9 $FUSE_PID
# 將 FUSE_PID 設為空，避免 finalize 函式再次 kill
FUSE_PID=""
sleep 1

# 4. 解除掛載 (此時 FUSE 行程已死，解除掛載是為了讓系統釋放掛載點)
echo "Unmounting stale mount point..."
umount $MNT || diskutil unmount $MNT || true

# 5. 重新掛載檔案系統，觸發 Journal Recovery
echo -e "\n--- Restarting Filesystem to trigger recovery ---"
python waynefs.py --image $IMG --mountpoint $MNT --foreground 1 &
FUSE_PID=$!
sleep 2 # 等待 FUSE 完全啟動
echo "Filesystem remounted with PID: $FUSE_PID"

# 6. 驗證結果
echo "Verifying recovery..."
if [ -d "$TEST_DIR" ]; then
    echo -e "${GREEN}✅ PASSED: Directory '$TEST_DIR' exists after recovery.${NC}"
    echo -e "${GREEN}✅ Your journal system successfully recovered the state!${NC}"
else
    echo -e "${RED}❌ FAILED: Directory '$TEST_DIR' does NOT exist after recovery.${NC}"
    ls -la $MNT # 顯示根目錄內容以供除錯
    exit 1
fi

echo "--- Test finished successfully ---"