# 台股評分系統 (Windows / Linux)

## 核心特色

✅ **本地 SQLite 快取** — 抓過的資料存本地,下次不打 API
✅ **斷點續傳** — 跑到一半被擋,下次接著跑
✅ **402 配額耗盡的三種處理策略** — 互動詢問/自動等待/立即中止
✅ **API 額度查詢** — 隨時查目前用了多少

## 安裝

### Windows

1. 安裝 Python 3.9+,安裝時勾選 "Add Python to PATH"
2. 開 CMD 或 PowerShell:
   ```
   cd C:\path\to\stock_scorer
   pip install -r requirements.txt
   ```

### Linux / Ubuntu

1. 安裝 Python 3.9+:
   ```bash
   sudo apt update && sudo apt install python3 python3-pip
   ```
2. 安裝依賴:
   ```bash
   cd /path/to/stock_scorer
   pip3 install -r requirements.txt
   ```

## 檔案清單

```
stock_scorer.py      <- 主程式
finmind_cache.py     <- 快取版 fetcher (必須跟主程式同資料夾)
stocks.txt           <- 你的股票清單
run.bat              <- Windows 一鍵執行
run.sh               <- Linux/Ubuntu 一鍵執行
requirements.txt
```

## 解決 FinMind 402 配額問題

FinMind 免費版額度極有限:
- 未登入: 300 次/小時
- 註冊 + 帶 token: 600 次/小時

跑 10 檔股票一年的回測 (不含快取) 約需 400 次 API 呼叫,**很快會碰到 402**。

### 本系統的應對方式

**1. 本地快取 (核心解法)**

所有抓過的資料存到 `finmind_cache.db` (SQLite),下次跑同樣的資料完全不打 API。

第一次跑可能要 5-10 分鐘抓資料,**第二次跑只要幾秒** (純本地計算)。

**2. 一次性預抓**

回測模式會先把整個區間的價格、法人、融資一次抓完,後續所有月度評分都從快取讀。

**3. 402 處理策略 (`--on-quota`)**

| 模式 | 行為 | 用途 |
|---|---|---|
| `ask` (預設) | 互動詢問: w=等60分/s=跳過/a=中止 | 手動操作 |
| `wait` | 自動等 60 分鐘後重試 | 無人值守長時間跑 |
| `abort` | 立即中止 (已抓資料留快取) | 想盡快結束 |

### 申請 FinMind Token (強烈建議)

1. 註冊: https://finmindtrade.com (免費)
2. 會員中心取 token
3. 用 `--token YOUR_TOKEN` 帶入

## 快速開始

### 方式 1: 一鍵執行腳本

**Windows** — 編輯 `run.bat` 把 `TOKEN=` 填上，雙擊執行。

**Linux/Ubuntu** — 編輯 `run.sh` 把 `TOKEN=""` 填上，然後執行：
```bash
chmod +x run.sh   # 只需第一次
./run.sh
```

### 方式 2: 命令列

**查當前 API 額度**

Windows:
```
python stock_scorer.py quota --token YOUR_TOKEN
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py quota --token YOUR_TOKEN
```

**單次評分排序**

Windows:
```
python stock_scorer.py rank --stocks 2330,2317,2454 --token YOUR_TOKEN
python stock_scorer.py rank --stocks stocks.txt --token YOUR_TOKEN
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py rank --stocks 2330,2317,2454 --token YOUR_TOKEN
python3 stock_scorer.py rank --stocks stocks.txt --token YOUR_TOKEN
```

**月度回測 (建議用 --on-quota wait 自動等)**

Windows:
```
python stock_scorer.py backtest --stocks stocks.txt --token YOUR_TOKEN ^
    --start 2024-01-01 --end 2024-12-31 --top-n 3 --on-quota wait
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py backtest --stocks stocks.txt --token YOUR_TOKEN \
    --start 2024-01-01 --end 2024-12-31 --top-n 3 --on-quota wait
```

**兩種都跑**

Windows:
```
python stock_scorer.py both --stocks stocks.txt --token YOUR_TOKEN
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py both --stocks stocks.txt --token YOUR_TOKEN
```

### 快取管理

**查看快取內容**

Windows: `python stock_scorer.py cache info`
Linux/Ubuntu: `python3 stock_scorer.py cache info`

**清空快取**

Windows: `python stock_scorer.py cache clear`
Linux/Ubuntu: `python3 stock_scorer.py cache clear`

**用自訂快取檔 (分開不同投資組合)**

Windows:
```
python stock_scorer.py rank --stocks tech.txt --cache tech_cache.db --token YOUR_TOKEN
python stock_scorer.py rank --stocks finance.txt --cache finance_cache.db --token YOUR_TOKEN
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py rank --stocks tech.txt --cache tech_cache.db --token YOUR_TOKEN
python3 stock_scorer.py rank --stocks finance.txt --cache finance_cache.db --token YOUR_TOKEN
```

## 配額耗盡的實戰建議

**情境 1: 第一次跑 10 檔股票的回測**

Windows:
```
python stock_scorer.py backtest --stocks stocks.txt --token YOUR_TOKEN ^
    --start 2024-01-01 --end 2024-12-31 --on-quota wait
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py backtest --stocks stocks.txt --token YOUR_TOKEN \
    --start 2024-01-01 --end 2024-12-31 --on-quota wait
```

預抓階段需要 30 次 API 呼叫 (10 檔 × 3 dataset),在配額內。
若碰到 402,自動等 60 分鐘繼續。

**情境 2: 跑完想換個區間再跑**

只要日期區間在 `finmind_cache.db` 已涵蓋範圍內,**完全不打 API**:

Windows:
```
python stock_scorer.py backtest --stocks stocks.txt --token YOUR_TOKEN ^
    --start 2024-03-01 --end 2024-09-30 --top-n 5
```
Linux/Ubuntu:
```bash
python3 stock_scorer.py backtest --stocks stocks.txt --token YOUR_TOKEN \
    --start 2024-03-01 --end 2024-09-30 --top-n 5
```

**情境 3: 想加新股票進清單**

只會抓新股票的資料,既有股票仍從快取讀。

**情境 4: 想跑更早期的資料**

只會抓「快取沒涵蓋」的部分。原本快取 2024-01 ~ 2024-12,現在要 2023-01 ~ 2024-12,只會抓 2023-01 ~ 2024-01 這段。

## 參數一覽

| 參數 | 說明 | 預設 |
|------|------|------|
| `--stocks` / `-s` | 股票清單 | 必填 |
| `--token` / `-t` | FinMind API token | 空 |
| `--cache` | 本地快取檔路徑 | `finmind_cache.db` |
| `--on-quota` | 402 處理: ask/wait/abort | `ask` |
| `--output` / `-o` | 輸出資料夾 | 當前目錄 |
| `--end` | 評分/回測結束日 | 今天 |
| `--start` | 回測起始日 | 一年前 |
| `--lookback` | 評分回看天數 | 180 |
| `--top-n` | 回測每月持股數 | 3 |

## 常見問題

**Q: 一直碰到 402, 連預抓都打不完?**
A: 申請 token 後就有 600/hr,跑 10 檔股票預抓只要 30 次完全夠。
   或者把清單拆成小批,分多次跑 (快取會記得已抓過的)。

**Q: 快取會佔很多空間嗎?**
A: 10 檔股票一年大約 1-5 MB,影響不大。

**Q: 想強制重新抓最新資料?**
A: 改用不同的 `--cache` 檔名,或先 `cache clear` 再跑。

**Q: 籌碼資料顯示 x?**
A: 該股票該日的法人/融資資料 FinMind 沒給。技術分仍正常,籌碼分用 50 中性值。

**Q: 回測時間範圍要怎麼選?**
A: 至少 6 個月,建議 1-3 年看穩定性。記得保留至少 200 天的前置期。
