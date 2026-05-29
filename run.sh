#!/usr/bin/env bash
# ==========================================
#  股票評分系統 - Linux/Ubuntu 一鍵執行
# ==========================================

set -euo pipefail

# ===== 設定區 (請編輯) =====
STOCKS="stocks.txt"
TOKEN=""
TOP_N=3
OUTPUT="output"
CACHE="finmind_cache.db"

# 回測區間
BT_START="2024-01-01"
BT_END=$(date +%Y-%m-%d)

# 配額耗盡時的處理: ask / wait / abort
ON_QUOTA="wait"

# ===== 檢查 Python =====
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(sys.version_info >= (3,9))" 2>/dev/null || echo "False")
        if [ "$VER" = "True" ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "[錯誤] 找不到 Python 3.9+，請先安裝："
    echo "  sudo apt update && sudo apt install python3 python3-pip"
    exit 1
fi

# ===== 檢查依賴 =====
if ! "$PYTHON" -c "import pandas, numpy, requests" 2>/dev/null; then
    echo "[安裝] 安裝必要套件..."
    "$PYTHON" -m pip install -r requirements.txt
fi

# ===== 建立輸出資料夾 =====
mkdir -p "$OUTPUT"

# ===== 執行 =====
echo ""
echo "=========================================="
echo " 股票評分系統"
echo "=========================================="
echo " 清單檔     : $STOCKS"
echo " 快取檔     : $CACHE"
echo " 輸出至     : $OUTPUT/"
echo " 配額策略   : $ON_QUOTA"
echo ""

# 查詢額度 (有 token 才查)
if [ -n "$TOKEN" ]; then
    echo "=== 查詢 API 額度 ==="
    "$PYTHON" stock_scorer.py quota --token "$TOKEN"
    echo ""
fi

# 執行主程式
if [ -z "$TOKEN" ]; then
    "$PYTHON" stock_scorer.py both \
        --stocks "$STOCKS" \
        --top-n "$TOP_N" \
        --start "$BT_START" \
        --end "$BT_END" \
        --output "$OUTPUT" \
        --cache "$CACHE" \
        --on-quota "$ON_QUOTA"
else
    "$PYTHON" stock_scorer.py both \
        --stocks "$STOCKS" \
        --token "$TOKEN" \
        --top-n "$TOP_N" \
        --start "$BT_START" \
        --end "$BT_END" \
        --output "$OUTPUT" \
        --cache "$CACHE" \
        --on-quota "$ON_QUOTA"
fi

echo ""
echo "=========================================="
echo " 完成! 結果在 $OUTPUT/ 資料夾"
echo " 快取在 $CACHE (下次跑會自動使用)"
echo "=========================================="
