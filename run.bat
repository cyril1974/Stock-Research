@echo off
REM ==========================================
REM  股票評分系統 - Windows 一鍵執行
REM ==========================================

chcp 65001 > nul
setlocal

REM ===== 設定區 (請編輯) =====
set STOCKS=stocks.txt
set TOKEN=
set TOP_N=3
set OUTPUT=output
set CACHE=finmind_cache.db

REM 回測區間
set BT_START=2024-01-01
set BT_END=2024-12-31

REM 配額耗盡時的處理: ask / wait / abort
set ON_QUOTA=wait

REM ===== 檢查 Python =====
where python > nul 2>&1
if errorlevel 1 (
    echo [錯誤] 找不到 python,請先安裝 Python 3.9+ 並加入 PATH
    pause
    exit /b 1
)

REM ===== 檢查依賴 =====
python -c "import pandas, numpy, requests" 2>nul
if errorlevel 1 (
    echo [安裝] 安裝必要套件...
    pip install -r requirements.txt
)

REM ===== 建立輸出資料夾 =====
if not exist %OUTPUT% mkdir %OUTPUT%

REM ===== 執行 =====
echo.
echo ==========================================
echo  股票評分系統
echo ==========================================
echo  清單檔     : %STOCKS%
echo  快取檔     : %CACHE%
echo  輸出至     : %OUTPUT%\
echo  配額策略   : %ON_QUOTA%
echo.

REM 查詢額度 (有 token 才查)
if not "%TOKEN%"=="" (
    echo === 查詢 API 額度 ===
    python stock_scorer.py quota --token %TOKEN%
    echo.
)

if "%TOKEN%"=="" (
    python stock_scorer.py both --stocks %STOCKS% --top-n %TOP_N% ^
        --start %BT_START% --end %BT_END% --output %OUTPUT% ^
        --cache %CACHE% --on-quota %ON_QUOTA%
) else (
    python stock_scorer.py both --stocks %STOCKS% --token %TOKEN% --top-n %TOP_N% ^
        --start %BT_START% --end %BT_END% --output %OUTPUT% ^
        --cache %CACHE% --on-quota %ON_QUOTA%
)

echo.
echo ==========================================
echo  完成! 結果在 %OUTPUT%\ 資料夾
echo  快取在 %CACHE% (下次跑會自動使用)
echo ==========================================
pause
