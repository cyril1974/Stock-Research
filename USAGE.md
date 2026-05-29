# 台股評分系統 — 完整使用手冊

## 目錄

1. [系統架構](#系統架構)
2. [安裝與環境設定](#安裝與環境設定)
3. [FinMind Token 申請](#finmind-token-申請)
4. [股票清單格式](#股票清單格式)
5. [指令總覽](#指令總覽)
6. [模式 A：單次評分排序 (rank)](#模式-a單次評分排序-rank)
7. [模式 B：月度換股回測 (backtest)](#模式-b月度換股回測-backtest)
8. [兩種模式同時執行 (both)](#兩種模式同時執行-both)
9. [查詢 API 額度 (quota)](#查詢-api-額度-quota)
10. [快取管理 (cache)](#快取管理-cache)
11. [一鍵執行腳本](#一鍵執行腳本)
12. [評分系統說明](#評分系統說明)
13. [輸出檔案說明](#輸出檔案說明)
14. [配額耗盡處理策略](#配額耗盡處理策略)
15. [常見錯誤排查](#常見錯誤排查)

---

## 系統架構

```
stock_scorer.py        主 CLI 入口，含評分引擎
stock_scoring_system.py 評分核心模組（可獨立 import）
finmind_cache.py       本地 SQLite 快取層
finmind_cache.db       自動建立的快取資料庫（首次執行後產生）
stocks.txt             你的股票清單
run.bat                Windows 一鍵執行
run.sh                 Linux/Ubuntu 一鍵執行
output/                輸出資料夾（預設）
```

**資料流程：**

```
CLI 指令
  └─► ScoreEngine
        ├─► FinMindCachedFetcher ──► 本地快取 (finmind_cache.db)
        │                        └─► FinMind API（快取沒有才打）
        ├─► 技術面評分（40%）
        └─► 籌碼面評分（60%）
              └─► 輸出 CSV + 終端機報表
```

---

## 安裝與環境設定

### Windows

```bat
REM 1. 安裝 Python 3.9+（安裝時勾選 "Add Python to PATH"）
REM 2. 開啟 CMD 或 PowerShell
cd C:\path\to\Stock-Research
pip install -r requirements.txt
```

### Linux / Ubuntu

```bash
# 1. 安裝 Python 3.9+
sudo apt update && sudo apt install python3 python3-pip

# 2. 安裝依賴
cd /path/to/Stock-Research
pip3 install -r requirements.txt
```

### 依賴套件清單

| 套件 | 版本需求 | 用途 |
|------|---------|------|
| pandas | ≥ 2.0.0 | 資料處理 |
| numpy | ≥ 1.24.0 | 數值計算 |
| requests | ≥ 2.28.0 | API 呼叫 |
| tqdm | ≥ 4.64.0 | 進度條 |
| colorama | ≥ 0.4.6 | 終端機彩色輸出 |

---

## FinMind Token 申請

FinMind 免費額度限制：

| 狀態 | 每小時上限 |
|------|-----------|
| 未帶 token | 300 次 |
| 帶有效 token | 600 次 |

**強烈建議申請 token**，否則跑 10 檔股票回測就容易超額。

**申請步驟：**

1. 前往 [https://finmindtrade.com](https://finmindtrade.com)（免費）
2. 註冊帳號並登入
3. 進入「會員中心」→ 複製 API Token
4. 在執行時用 `--token YOUR_TOKEN` 帶入，或填入 `run.bat` / `run.sh` 的 `TOKEN=` 欄位

---

## 股票清單格式

`--stocks` 支援以下三種輸入方式：

### 方式 1：直接輸入代號（逗號分隔）

```bash
python3 stock_scorer.py rank --stocks 2330,2317,2454
```

### 方式 2：純文字檔（.txt）

一行一檔，支援 `#` 註解，支援同行多檔逗號分隔：

```
# 半導體
2330    # 台積電
2454    # 聯發科

# 電子製造
2317, 2308   # 鴻海、台達電

# 金融
2882
```

### 方式 3：CSV 檔（.csv）

第一欄為股票代號，其他欄位忽略：

```csv
stock_id,name,sector
2330,台積電,半導體
2317,鴻海,電子製造
2454,聯發科,半導體
```

---

## 指令總覽

```
python stock_scorer.py <模式> [選項]

模式：
  rank       模式 A：對清單做單次評分排序
  backtest   模式 B：月度換股回測
  both       兩種模式都跑
  quota      查詢 FinMind API 額度
  cache      快取管理（info / clear）
```

### 通用選項（rank / backtest / both 皆可用）

| 選項 | 縮寫 | 說明 | 預設值 |
|------|------|------|--------|
| `--stocks` | `-s` | 股票清單（必填） | — |
| `--token` | `-t` | FinMind API token | 空（免費額度） |
| `--output` | `-o` | 輸出資料夾 | `.`（當前目錄） |
| `--cache` | | 快取 SQLite 檔路徑 | `finmind_cache.db` |
| `--on-quota` | | 402 配額耗盡處理 | `ask` |

---

## 模式 A：單次評分排序 (rank)

對股票清單在指定截止日計算技術面 + 籌碼面分數，由高到低排序。

### 語法

```
rank --stocks <來源> [--end YYYY-MM-DD] [--lookback 天數] [通用選項]
```

### 專屬選項

| 選項 | 說明 | 預設值 |
|------|------|--------|
| `--end` | 評分截止日（用這一天之前的資料評分） | 今天 |
| `--lookback` | 回看幾天的歷史資料來計算指標 | 180 天 |

### 範例

**最基本的評分（今天為截止日）：**

Windows:
```bat
python stock_scorer.py rank --stocks stocks.txt --token YOUR_TOKEN
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py rank --stocks stocks.txt --token YOUR_TOKEN
```

**指定截止日：**

Windows:
```bat
python stock_scorer.py rank --stocks stocks.txt --token YOUR_TOKEN --end 2024-12-31
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py rank --stocks stocks.txt --token YOUR_TOKEN --end 2024-12-31
```

**直接指定股票代號，輸出到特定資料夾：**

Windows:
```bat
python stock_scorer.py rank --stocks 2330,2317,2454 --token YOUR_TOKEN --output results
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py rank --stocks 2330,2317,2454 --token YOUR_TOKEN --output results
```

**加長回看天數（指標計算更穩定）：**

Windows:
```bat
python stock_scorer.py rank --stocks stocks.txt --token YOUR_TOKEN --lookback 365
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py rank --stocks stocks.txt --token YOUR_TOKEN --lookback 365
```

### 輸出範例

```
======================================================================
排序結果
======================================================================
 排名 stock_id   總分  技術分  籌碼分 籌碼資料  T_ma_align  T_ma_slope  T_rsi  ...
    1     2330  78.50   72.30   82.80        v       100.0        85.2   95.0  ...
    2     2454  71.20   68.40   73.10        v        80.0        72.1   88.0  ...
    3     2317  65.80   60.50   69.40        v        60.0        55.3   72.0  ...
```

---

## 模式 B：月度換股回測 (backtest)

在指定日期區間內，每月第一天用評分選出前 N 名，持有一個月，計算累積報酬。

### 語法

```
backtest --stocks <來源> [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--top-n N] [通用選項]
```

### 專屬選項

| 選項 | 說明 | 預設值 |
|------|------|--------|
| `--start` | 回測起始日 | 一年前 |
| `--end` | 回測結束日 | 今天 |
| `--top-n` | 每月持有前幾名（等權重） | 3 |

### 範例

**基本回測（過去一年）：**

Windows:
```bat
python stock_scorer.py backtest --stocks stocks.txt --token YOUR_TOKEN ^
    --start 2024-01-01 --end 2024-12-31
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py backtest --stocks stocks.txt --token YOUR_TOKEN \
    --start 2024-01-01 --end 2024-12-31
```

**每月持有前 5 名，配額耗盡自動等待：**

Windows:
```bat
python stock_scorer.py backtest --stocks stocks.txt --token YOUR_TOKEN ^
    --start 2023-01-01 --end 2024-12-31 --top-n 5 --on-quota wait
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py backtest --stocks stocks.txt --token YOUR_TOKEN \
    --start 2023-01-01 --end 2024-12-31 --top-n 5 --on-quota wait
```

**用獨立快取跑不同投資組合：**

Windows:
```bat
python stock_scorer.py backtest --stocks tech.txt --cache tech_cache.db ^
    --token YOUR_TOKEN --start 2024-01-01 --end 2024-12-31
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py backtest --stocks tech.txt --cache tech_cache.db \
    --token YOUR_TOKEN --start 2024-01-01 --end 2024-12-31
```

### 執行流程

回測分兩個步驟：

**步驟 1：預抓資料**
- 一次性抓取所有股票、完整日期區間的三種資料（價格、法人、融資）
- 需要 `股票數 × 3` 次 API 呼叫（有快取則跳過）
- 後續所有月度評分完全從本地快取讀，不再打 API

**步驟 2：月度評分與回測**
- 每個換股日計算所有股票分數，選前 N 名
- 計算該月等權重報酬（開盤買入 → 月底收盤賣出）
- 輸出累積報酬、勝率、夏普比率等統計

### 輸出範例

```
======================================================================
回測總結
======================================================================
  期間           : 2024-01-01 ~ 2024-12-31
  換股次數       : 11
  累積報酬       : +23.45%
  月均報酬       : +1.94%
  勝率           : 72.7%
  月報酬標準差   : 4.12%
  年化夏普比率   : 1.63
  最大回撤       : -8.23%
```

---

## 兩種模式同時執行 (both)

先執行模式 A（單次評分排序），再執行模式 B（月度回測），選項為兩者的聯集。

### 語法

```
both --stocks <來源> [--end YYYY-MM-DD] [--lookback 天數]
     [--start YYYY-MM-DD] [--top-n N] [通用選項]
```

### 範例

Windows:
```bat
python stock_scorer.py both --stocks stocks.txt --token YOUR_TOKEN ^
    --start 2024-01-01 --end 2024-12-31 --top-n 3
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py both --stocks stocks.txt --token YOUR_TOKEN \
    --start 2024-01-01 --end 2024-12-31 --top-n 3
```

---

## 查詢 API 額度 (quota)

顯示當前 FinMind token 已使用多少額度。

### 語法

```
quota --token YOUR_TOKEN
```

### 範例

Windows:
```bat
python stock_scorer.py quota --token YOUR_TOKEN
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py quota --token YOUR_TOKEN
```

### 輸出範例

```
[OK] API 額度: 已用 127/600
```

---

## 快取管理 (cache)

### 查看快取內容

顯示本地快取中已存有哪些股票、哪些日期區間的資料。

Windows:
```bat
python stock_scorer.py cache info
python stock_scorer.py cache info --cache my_cache.db
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py cache info
python3 stock_scorer.py cache info --cache my_cache.db
```

**輸出範例：**

```
[INFO] 快取檔: finmind_cache.db
 stock_id          dataset   start_date     end_date  rows
     2330  TaiwanStockPrice   2023-01-01   2024-12-31  4821
     2330   ...Institutional   2023-01-01   2024-12-31  3210
     2454  TaiwanStockPrice   2023-01-01   2024-12-31  4798
...
[INFO] 快取檔大小: 3842 KB
```

### 清空快取

刪除所有快取資料（需手動確認）：

Windows:
```bat
python stock_scorer.py cache clear
python stock_scorer.py cache clear --cache my_cache.db
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py cache clear
python3 stock_scorer.py cache clear --cache my_cache.db
```

### 快取機制說明

- 快取使用 SQLite 儲存（`finmind_cache.db`），首次執行時自動建立
- **智慧區間合併**：若快取已有 2024-01 ~ 2024-06，再請求 2024-01 ~ 2024-12，只會補抓 2024-07 ~ 2024-12
- 快取不會過期（歷史資料不變），只有「今天」的資料可能需要強制更新
- 想強制重新抓：換一個新的 `--cache` 路徑，或先 `cache clear`

---

## 一鍵執行腳本

### Windows（run.bat）

1. 用文字編輯器打開 `run.bat`
2. 填入設定：

```bat
set STOCKS=stocks.txt     ← 股票清單檔案
set TOKEN=                ← 填入你的 FinMind token（可留空）
set TOP_N=3               ← 每月持股數
set OUTPUT=output         ← 輸出資料夾
set CACHE=finmind_cache.db
set BT_START=2024-01-01   ← 回測起始日
set BT_END=2024-12-31     ← 回測結束日
set ON_QUOTA=wait         ← 配額耗盡時自動等待
```

3. 雙擊 `run.bat` 執行

### Linux/Ubuntu（run.sh）

1. 用文字編輯器打開 `run.sh`
2. 填入設定：

```bash
STOCKS="stocks.txt"
TOKEN=""              # 填入你的 FinMind token（可留空）
TOP_N=3
OUTPUT="output"
CACHE="finmind_cache.db"
BT_START="2024-01-01"
BT_END="2024-12-31"
ON_QUOTA="wait"
```

3. 給予執行權限（只需第一次）並執行：

```bash
chmod +x run.sh
./run.sh
```

---

## 評分系統說明

所有子分數標準化到 **0–100**，最後加權：

```
總分 = 技術分 × 40% + 籌碼分 × 60%
```

### 技術面（6 項，各佔 1/6）

| 指標 | 欄位名 | 說明 |
|------|--------|------|
| 均線多頭排列 | `T_ma_align` | MA5 > MA20 > MA60 > MA120，越多層滿足分數越高 |
| 均線斜率 | `T_ma_slope` | MA20 近 5 日斜率，>+2% 滿分，<-2% 零分 |
| RSI | `T_rsi` | RSI 50–70 最佳（滿分），過熱（>80）或過冷（<30）扣分 |
| MACD | `T_macd` | DIF>0、柱狀體>0、柱狀體擴張、黃金交叉各加分 |
| KD | `T_kd` | K>D 加分，低檔黃金交叉大加分，高檔死亡交叉扣分 |
| 量價配合 | `T_volume` | 上漲帶量最佳，下跌爆量最差 |

### 籌碼面（3 項，各佔 1/3）

| 指標 | 欄位名 | 說明 |
|------|--------|------|
| 外資買賣超 | `C_foreign` | 近 5 日 / 20 日外資累計買超（張），越多越高分 |
| 投信買賣超 | `C_trust` | 近 5 日連續買超天數 + 累計買超量 |
| 融資增減 | `C_margin` | 股價漲 + 融資減 = 最佳（籌碼乾淨），股價跌 + 融資增 = 最差 |

> **籌碼資料缺失時**：若 FinMind 當日無法人或融資資料，該子項目以 50 中性分計算，欄位 `籌碼資料` 顯示 `x`。

---

## 輸出檔案說明

### 評分排序結果（rank / both）

**檔名格式：** `ranking_YYYYMMDD.csv`

| 欄位 | 說明 |
|------|------|
| 排名 | 依總分排序 |
| stock_id | 股票代號 |
| 總分 | 技術分×40% + 籌碼分×60% |
| 技術分 | 技術面六項平均 |
| 籌碼分 | 籌碼面三項平均 |
| 籌碼資料 | `v`=有資料，`x`=缺失（用中性值） |
| T_ma_align | 均線多頭排列子分 |
| T_ma_slope | 均線斜率子分 |
| T_rsi | RSI 子分 |
| T_macd | MACD 子分 |
| T_kd | KD 子分 |
| T_volume | 量價配合子分 |
| C_foreign | 外資買賣超子分 |
| C_trust | 投信買賣超子分 |
| C_margin | 融資增減子分 |

### 回測結果（backtest / both）

**檔名格式：** `backtest_YYYYMMDD_YYYYMMDD.csv`

| 欄位 | 說明 |
|------|------|
| date | 換股日期 |
| picks | 當月選中的股票（`\|` 分隔） |
| return | 當月等權重報酬（小數，如 0.05 = +5%） |
| cumulative | 截至該月的累積報酬 |

---

## 配額耗盡處理策略

`--on-quota` 控制碰到 FinMind 402 時的行為：

| 值 | 行為 | 適用情境 |
|----|------|----------|
| `ask`（預設） | 互動詢問：`w`=等60分鐘 / `s`=跳過該股 / `a`=中止 | 手動盯著看 |
| `wait` | 自動等 60 分鐘後重試，無需人工介入 | 無人值守長時間跑 |
| `abort` | 立即中止，已抓到的資料留在快取 | 想快速結束再手動重跑 |

**最佳實踐：**

- 首次執行長回測用 `--on-quota wait`，讓它跑過夜
- 已有快取後幾乎不會再碰到 402
- 用 `quota` 指令在跑之前先確認額度是否充足

---

## 常見錯誤排查

### 問題：一直碰到 402 配額耗盡

```
[WARN] 402 配額耗盡 (stock_id=2330, dataset=TaiwanStockPrice)
```

**解法：**
1. 申請 FinMind token → 額度從 300 升至 600/hr
2. 加 `--on-quota wait` 讓系統自動等待
3. 已有快取後不會再打同樣的 API，第二次跑幾乎不會碰到

---

### 問題：找不到 Python

Windows:
```
[錯誤] 找不到 python，請先安裝 Python 3.9+ 並加入 PATH
```

**解法：**
- 重新安裝 Python，勾選「Add Python to PATH」
- 或在 CMD 輸入 `where python` 確認路徑

Linux/Ubuntu:
```
[錯誤] 找不到 Python 3.9+
```

**解法：**
```bash
sudo apt update && sudo apt install python3 python3-pip
```

---

### 問題：套件未安裝

```
ModuleNotFoundError: No module named 'pandas'
```

**解法：**

Windows: `pip install -r requirements.txt`
Linux/Ubuntu: `pip3 install -r requirements.txt`

---

### 問題：籌碼資料顯示 `x`

**原因：** FinMind 沒有該股票該日期的法人/融資資料（部分小型股或特定日期缺資料屬正常現象）

**影響：** 技術分仍正常計算；籌碼面三個子項目各以 50 中性分代入，總分仍有參考價值

---

### 問題：回測結果為空或換股次數不足

```
[WARN] 該月無可評分股票
```

**原因：** 日期區間內某些月份股票資料不足 30 天（例如區間太短、股票掛牌較晚）

**解法：** 確認 `--start` 至少比最早上市的股票早 200 天（系統會自動往前延伸 200 天抓前置資料）

---

### 問題：想強制重新抓最新資料

快取不會自動更新已存的歷史資料。如需強制重新抓：

```bash
# 方法 1：換一個新的快取檔名
python3 stock_scorer.py rank --stocks stocks.txt --cache new_cache.db

# 方法 2：清空舊快取
python3 stock_scorer.py cache clear
python3 stock_scorer.py rank --stocks stocks.txt
```

---

### 問題：網路連線逾時

```
[Timeout] TaiwanStockPrice 2330
```

**解法：**
- 確認可以連到 `api.finmindtrade.com`
- 如在企業網路，確認防火牆沒有封鎖外部 HTTPS
- 重新執行（快取機制會接續未完成的部分）
