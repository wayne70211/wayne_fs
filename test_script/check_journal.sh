#!/bin/bash
#
# Comprehensive Journaling Crash Recovery Test Suite for WayneFS
#
set -e
source .venv/bin/activate

# --- 設定 ---
MNT=./mnt
IMG=waynefs.img
FUSE_PID="" # 全域變數，用於追蹤 FUSE 行程

# --- 顏色與格式 ---
GREEN="\033[0;32m"
RED="\033[0;31m"
YELLOW="\033[1;33m"
NC="\033[0m"

# --- 清理函式 ---
# 無論腳本如何結束，都會執行此函式來確保環境乾淨
finalize() {
    echo -e "\n${YELLOW}--- Finalizing ---${NC}"
    if [ ! -z "$FUSE_PID" ] && ps -p $FUSE_PID > /dev/null; then
        echo "Killing leftover FUSE process $FUSE_PID..."
        kill -9 $FUSE_PID || true
    fi
    echo "Unmounting $MNT..."
    umount $MNT || diskutil unmount $MNT || true
    echo "Removing mount point $MNT..."
    rmdir $MNT || true
    echo "Cleanup complete."
}
trap finalize EXIT

# --- 核心測試函式 ---
# 參數:
# $1: 測試名稱 (例如 "mkdir")
# $2: 執行崩潰前的「準備」指令 (可為空)
# $3: 觸發崩潰的「執行」指令
# $4: 恢復後用來「驗證」的指令
run_crash_test() {
    TEST_NAME=$1
    PREPARE_CMD=$2
    EXECUTE_CMD=$3
    VERIFY_CMD=$4

    echo -e "\n${YELLOW}===== Starting Test: Crash Recovery for [$TEST_NAME] =====${NC}"

    # 1. 準備階段：掛載檔案系統並執行準備指令
    echo "  [Phase 1] Preparing state for the test..."
    python waynefs.py --image $IMG --mountpoint $MNT --foreground 1 &
    FUSE_PID=$! && sleep 2
    
    if [ ! -z "$PREPARE_CMD" ]; then
        eval $PREPARE_CMD
        echo "    - Preparation command executed."
    fi
    
    # 為了讓準備狀態被寫入磁碟，我們先乾淨地重啟一次
    kill -9 $FUSE_PID && FUSE_PID=""
    umount $MNT || diskutil unmount $MNT || true
    sleep 1

    # 2. 執行並崩潰
    echo "  [Phase 2] Executing command and simulating crash..."
    python waynefs.py --image $IMG --mountpoint $MNT --foreground 1 &
    FUSE_PID=$! && sleep 2

    eval $EXECUTE_CMD
    echo "    - Command executed. Simulating power failure..."
    kill -9 $FUSE_PID && FUSE_PID=""
    umount $MNT || diskutil unmount $MNT || true
    sleep 1

    # 3. 重啟並恢復
    echo "  [Phase 3] Remounting to trigger journal recovery..."
    python waynefs.py --image $IMG --mountpoint $MNT --foreground 1 &
    FUSE_PID=$! && sleep 2

    # 4. 驗證結果
    echo -n "  [Phase 4] Verifying result... "
    if eval $VERIFY_CMD; then
        echo -e "${GREEN}✅ PASSED${NC}"
    else
        echo -e "${RED}❌ FAILED${NC}"
        echo "      - Verification command failed: '$VERIFY_CMD'"
        ls -la $MNT # 顯示根目錄內容以供除錯
        exit 1
    fi

    # 5. 清理本次測試的 FUSE 程序
    kill -9 $FUSE_PID && FUSE_PID=""
    umount $MNT || diskutil unmount $MNT || true
    sleep 1
}

# --- 主測試流程 ---

# 0. 初始環境設定
echo "--- Initializing Test Environment ---"
if [ -d "$MNT" ]; then
    umount $MNT || diskutil unmount $MNT || true
    rmdir $MNT
fi
mkdir -p $MNT
echo "Creating fresh disk image..."
python mkwaynefs.py --image $IMG

# --- 依序執行所有崩潰恢復測試 ---

# 測試 1: mkdir (建立目錄)
run_crash_test \
    "mkdir" \
    "" \
    "mkdir $MNT/test_dir" \
    "[ -d $MNT/test_dir ]"

# 測試 2: unlink (刪除檔案)
run_crash_test \
    "unlink" \
    "touch $MNT/file_to_delete.txt" \
    "rm $MNT/file_to_delete.txt" \
    "[ ! -f $MNT/file_to_delete.txt ]"

# 測試 3: rmdir (刪除目錄)
run_crash_test \
    "rmdir" \
    "mkdir $MNT/dir_to_delete" \
    "rmdir $MNT/dir_to_delete" \
    "[ ! -d $MNT/dir_to_delete ]"

# 測試 4: write (寫入檔案內容)
run_crash_test \
    "write" \
    "" \
    "echo -n 'recovered content' > $MNT/write_test.txt" \
    "[ \"\$(cat $MNT/write_test.txt)\" == 'recovered content' ]"

# 測試 5: rename (重命名檔案)
run_crash_test \
    "rename" \
    "touch $MNT/old_name.txt" \
    "mv $MNT/old_name.txt $MNT/new_name.txt" \
    "[ ! -f $MNT/old_name.txt ] && [ -f $MNT/new_name.txt ]"

echo -e "\n${GREEN}🎉🎉🎉 ALL JOURNAL RECOVERY TESTS PASSED! 🎉🎉🎉${NC}"
echo -e "${GREEN}Your filesystem's journal is robust.${NC}"