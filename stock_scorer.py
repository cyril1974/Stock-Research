"""
台股技術面 + 籌碼面 評分排序系統 (Windows CLI 版)
======================================================
資料來源: FinMind (https://finmindtrade.com)
權重: 技術面 40% / 籌碼面 60%

使用範例:
  # 看說明
  python stock_scorer.py --help

  # 模式 A: 對清單做單次評分排序
  python stock_scorer.py rank --stocks stocks.txt
  python stock_scorer.py rank --stocks 2330,2317,2454 --token YOUR_TOKEN

  # 模式 B: 月度換股回測
  python stock_scorer.py backtest --stocks stocks.txt --start 2024-01-01 --end 2024-12-31 --top-n 3

  # 兩種都跑
  python stock_scorer.py both --stocks stocks.txt --token YOUR_TOKEN

股票清單檔案格式 (stocks.txt 或 stocks.csv):
  方式 1 — 一行一檔:
    2330
    2317
    2454
  方式 2 — 逗號分隔:
    2330,2317,2454
  方式 3 — CSV (第一欄為代號):
    stock_id,name
    2330,台積電
    2317,鴻海

安裝依賴:
  pip install pandas numpy requests tqdm colorama
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests

# 引入快取版 fetcher
from finmind_cache import FinMindCachedFetcher

warnings.filterwarnings("ignore")

# ----- Windows 終端機顏色支援 -----
try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=True)
    COLOR_OK = True
except ImportError:
    COLOR_OK = False
    class _NoColor:
        def __getattr__(self, _): return ""
    Fore = Style = _NoColor()

# ----- 進度條 (可選) -----
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    def tqdm(iterable, **kwargs):
        return iterable


# ============================================================
# 訊息輸出工具
# ============================================================
def info(msg: str): print(f"{Fore.CYAN}[INFO]{Style.RESET_ALL} {msg}")
def ok(msg: str): print(f"{Fore.GREEN}[OK]{Style.RESET_ALL} {msg}")
def warn(msg: str): print(f"{Fore.YELLOW}[WARN]{Style.RESET_ALL} {msg}")
def err(msg: str): print(f"{Fore.RED}[ERROR]{Style.RESET_ALL} {msg}")
def header(msg: str):
    bar = "=" * 70
    print(f"\n{Fore.MAGENTA}{bar}\n{msg}\n{bar}{Style.RESET_ALL}")


# ============================================================
# 1. FinMind 資料抓取 (改用快取版,API 介面相容)
# ============================================================
# 詳細實作在 finmind_cache.py
# 快取機制避免重複呼叫 API,大幅降低 402 配額耗盡的風險

FinMindFetcher = FinMindCachedFetcher  # 向後相容


# ============================================================
# 2. 技術指標
# ============================================================
class TechnicalIndicators:
    @staticmethod
    def ema(s, n): return s.ewm(span=n, adjust=False).mean()

    @staticmethod
    def rsi(close, n=14):
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(n).mean()
        loss = (-delta.clip(upper=0)).rolling(n).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def macd(close, fast=12, slow=26, signal=9):
        ema_f = close.ewm(span=fast, adjust=False).mean()
        ema_s = close.ewm(span=slow, adjust=False).mean()
        dif = ema_f - ema_s
        dem = dif.ewm(span=signal, adjust=False).mean()
        return dif, dem, dif - dem

    @staticmethod
    def kd(high, low, close, n=9):
        lowest = low.rolling(n).min()
        highest = high.rolling(n).max()
        rsv = (close - lowest) / (highest - lowest).replace(0, np.nan) * 100
        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        return k, d


# ============================================================
# 3. 評分引擎
# ============================================================
class ScoreEngine:
    TECH_WEIGHT = 0.40
    CHIP_WEIGHT = 0.60

    def __init__(self, fetcher: FinMindFetcher):
        self.fetcher = fetcher
        self.ti = TechnicalIndicators()

    # ---------- 技術面 ----------
    def _score_ma_alignment(self, close):
        if len(close) < 120:
            return 50.0
        ma5, ma20, ma60, ma120 = (close.rolling(n).mean().iloc[-1] for n in (5, 20, 60, 120))
        cond = [ma5 > ma20, ma20 > ma60, ma60 > ma120, close.iloc[-1] > ma5, close.iloc[-1] > ma60]
        return sum(cond) / len(cond) * 100

    def _score_ma_slope(self, close):
        if len(close) < 25:
            return 50.0
        ma20 = close.rolling(20).mean()
        slope = (ma20.iloc[-1] - ma20.iloc[-6]) / ma20.iloc[-6] * 100
        return float(np.clip((slope + 2) / 4 * 100, 0, 100))

    def _score_rsi(self, close):
        if len(close) < 15:
            return 50.0
        r = self.ti.rsi(close).iloc[-1]
        if pd.isna(r):
            return 50.0
        if 50 <= r <= 70: return 100.0
        if 40 <= r < 50: return 70 + (r - 40) * 3
        if 70 < r <= 80: return 100 - (r - 70) * 3
        if r < 40: return max(0, r)
        return max(0, 100 - (r - 80) * 5)

    def _score_macd(self, close):
        if len(close) < 35:
            return 50.0
        dif, dem, hist = self.ti.macd(close)
        score = 50.0
        if dif.iloc[-1] > 0: score += 15
        if hist.iloc[-1] > 0: score += 15
        if hist.iloc[-1] > hist.iloc[-2]: score += 10
        if any((dif.iloc[-i] > dem.iloc[-i]) and (dif.iloc[-i-1] <= dem.iloc[-i-1]) for i in range(1, 4)):
            score += 10
        return float(np.clip(score, 0, 100))

    def _score_kd(self, high, low, close):
        if len(close) < 15:
            return 50.0
        k, d = self.ti.kd(high, low, close)
        k_now, d_now = k.iloc[-1], d.iloc[-1]
        if pd.isna(k_now) or pd.isna(d_now):
            return 50.0
        score = 50.0
        if k_now > d_now: score += 20
        if k_now < 50 and k.iloc[-2] <= d.iloc[-2] and k_now > d_now: score += 30
        if k_now > 80 and k.iloc[-2] >= d.iloc[-2] and k_now < d_now: score -= 30
        return float(np.clip(score, 0, 100))

    def _score_volume(self, close, volume):
        if len(close) < 21:
            return 50.0
        ret_5 = (close.iloc[-1] / close.iloc[-6] - 1) * 100
        vol_ratio = volume.iloc[-5:].mean() / volume.iloc[-20:].mean()
        if ret_5 > 0 and vol_ratio > 1.2: return 85
        if ret_5 > 0 and vol_ratio > 1.0: return 70
        if ret_5 < 0 and vol_ratio < 0.8: return 65
        if ret_5 < 0 and vol_ratio > 1.2: return 20
        return 50.0

    def compute_tech_score(self, df_price: pd.DataFrame) -> dict:
        if df_price.empty or len(df_price) < 30:
            return {"tech_total": 0, "subscores": {}}
        df = df_price.sort_values("date").copy()
        close = df["close"].astype(float)
        high = df["max"].astype(float)
        low = df["min"].astype(float)
        volume = df["Trading_Volume"].astype(float)
        sub = {
            "ma_align": self._score_ma_alignment(close),
            "ma_slope": self._score_ma_slope(close),
            "rsi": self._score_rsi(close),
            "macd": self._score_macd(close),
            "kd": self._score_kd(high, low, close),
            "volume": self._score_volume(close, volume),
        }
        return {"tech_total": float(np.mean(list(sub.values()))), "subscores": sub}

    # ---------- 籌碼面 ----------
    def _score_foreign(self, df_inst):
        if df_inst.empty:
            return 50.0
        df = df_inst[df_inst["name"].isin(["Foreign_Investor", "Foreign_Dealer_Self"])]
        if df.empty:
            return 50.0
        daily = (df.groupby("date")["buy"].sum() - df.groupby("date")["sell"].sum()).sort_index()
        if len(daily) < 5:
            return 50.0
        net_5 = daily.iloc[-5:].sum() / 1000
        net_20 = daily.iloc[-20:].sum() / 1000 if len(daily) >= 20 else net_5 * 4
        score = 50.0
        score += np.clip(net_5 / 100, -25, 25)
        score += np.clip(net_20 / 500, -25, 25)
        return float(np.clip(score, 0, 100))

    def _score_trust(self, df_inst):
        if df_inst.empty:
            return 50.0
        df = df_inst[df_inst["name"] == "Investment_Trust"]
        if df.empty:
            return 50.0
        daily = (df.groupby("date")["buy"].sum() - df.groupby("date")["sell"].sum()).sort_index()
        if len(daily) < 5:
            return 50.0
        recent = daily.iloc[-5:]
        buy_days = (recent > 0).sum()
        net_5 = recent.sum() / 1000
        score = 50.0 + buy_days * 8
        score += np.clip(net_5 / 50, -20, 15)
        return float(np.clip(score, 0, 100))

    def _score_margin(self, df_margin, df_price):
        if df_margin.empty or df_price.empty or len(df_margin) < 5:
            return 50.0
        df_margin = df_margin.sort_values("date")
        col = "MarginPurchaseTodayBalance"
        if col not in df_margin.columns:
            return 50.0
        m_now = df_margin[col].iloc[-1]
        m_old = df_margin[col].iloc[-6] if len(df_margin) >= 6 else df_margin[col].iloc[0]
        m_chg = (m_now - m_old) / m_old * 100 if m_old > 0 else 0
        p = df_price.sort_values("date")
        p_chg = (p["close"].iloc[-1] - p["close"].iloc[-6]) / p["close"].iloc[-6] * 100 if len(p) >= 6 else 0

        if p_chg > 0 and m_chg < 0: return 90.0
        if p_chg > 0 and m_chg < 3: return 70.0
        if p_chg > 0 and m_chg > 5: return 30.0
        if p_chg < 0 and m_chg < -3: return 65.0
        if p_chg < 0 and m_chg > 0: return 20.0
        return 50.0

    def compute_chip_score(self, stock_id, start, end, df_price) -> dict:
        df_inst = self.fetcher.get_institutional(stock_id, start, end)
        time.sleep(0.3)
        df_margin = self.fetcher.get_margin(stock_id, start, end)
        time.sleep(0.3)
        available = not df_inst.empty or not df_margin.empty
        sub = {
            "foreign": self._score_foreign(df_inst),
            "trust": self._score_trust(df_inst),
            "margin": self._score_margin(df_margin, df_price),
        }
        return {"chip_total": float(np.mean(list(sub.values()))), "subscores": sub, "available": available}

    # ---------- 總分 ----------
    def score_stock(self, stock_id, start, end) -> Optional[dict]:
        df_price = self.fetcher.get_price(stock_id, start, end)
        time.sleep(0.3)
        if df_price.empty:
            return None
        tech = self.compute_tech_score(df_price)
        chip = self.compute_chip_score(stock_id, start, end, df_price)
        total = tech["tech_total"] * self.TECH_WEIGHT + chip["chip_total"] * self.CHIP_WEIGHT
        return {
            "stock_id": stock_id,
            "total_score": round(total, 2),
            "tech_score": round(tech["tech_total"], 2),
            "chip_score": round(chip["chip_total"], 2),
            "tech_sub": tech["subscores"],
            "chip_sub": chip["subscores"],
            "chip_available": chip["available"],
            "price_days": len(df_price),
        }


# ============================================================
# 4. 股票清單載入
# ============================================================
def load_stock_list(source: str) -> tuple:
    """從多種來源載入股票清單

    支援:
    - 逗號分隔字串: "2330,2317,2454"
    - 檔案路徑 (.txt / .csv)
        .txt: 一行一檔或逗號分隔，支援 # 註解
        .csv: 第一欄為代號，第二欄（若有）為名稱

    Returns:
        (stocks: list, names: dict)  names 僅從 CSV 第二欄取得
    """
    p = Path(source)
    if p.exists() and p.is_file():
        ext = p.suffix.lower()
        if ext == ".csv":
            df = pd.read_csv(p, dtype=str, encoding="utf-8-sig")
            stocks = [s.strip() for s in df.iloc[:, 0].dropna().tolist() if s.strip()]
            names = {}
            if len(df.columns) >= 2:
                for _, row in df.iterrows():
                    sid = str(row.iloc[0]).strip()
                    name = str(row.iloc[1]).strip() if pd.notna(row.iloc[1]) else ""
                    if sid and name:
                        names[sid] = name
            return stocks, names
        else:
            text = p.read_text(encoding="utf-8-sig")
            tokens = []
            for line in text.splitlines():
                if "#" in line:
                    line = line.split("#", 1)[0]
                line = line.strip()
                if not line:
                    continue
                tokens.extend([t.strip() for t in line.split(",") if t.strip()])
            return tokens, {}
    else:
        stocks = [s.strip() for s in source.split(",") if s.strip()]
        return stocks, {}


# ============================================================
# 5. 模式 A: 單次評分排序
# ============================================================
def cmd_rank(stocks, end_date, lookback_days, token, output_dir, cache_path, quota_policy, names=None):
    fetcher = FinMindCachedFetcher(
        token=token,
        cache_path=cache_path,
        on_quota_exceeded=quota_policy,
    )
    engine = ScoreEngine(fetcher)

    end_dt = pd.to_datetime(end_date)
    start = (end_dt - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    info(f"評分區間: {start} ~ {end_date}")
    info(f"FinMind Token: {'已設定 (' + token[:8] + '...)' if token else '無 (免費額度)'}")
    info(f"快取資料庫: {cache_path}")
    info(f"股票數: {len(stocks)}")
    
    # 顯示 API 額度
    usage = fetcher.check_api_usage()
    if usage:
        info(f"API 額度: 已用 {usage['used']}/{usage['limit']}")
    print()

    # 建立股票名稱對照表：優先用檔案解析的名稱，不足的從 FinMind 補
    name_map = dict(names) if names else {}
    missing_names = [sid for sid in stocks if sid not in name_map]
    if missing_names:
        fetched_names = fetcher.get_stock_names(missing_names)
        name_map.update(fetched_names)

    results = []
    iterator = tqdm(stocks, desc="評分中", unit="檔") if HAS_TQDM else stocks
    for sid in iterator:
        try:
            r = engine.score_stock(sid, start, end_date)
            if r:
                results.append(r)
                tag = "" if r["chip_available"] else " (籌碼資料缺)"
                if not HAS_TQDM:
                    print(f"  {sid}: 總分 {r['total_score']:.1f} | 技術 {r['tech_score']:.1f} | 籌碼 {r['chip_score']:.1f}{tag}")
            else:
                if not HAS_TQDM:
                    warn(f"{sid}: 無資料")
        except RuntimeError as e:
            err(f"中止: {e}")
            break
        except Exception as e:
            err(f"{sid}: {type(e).__name__}: {e}")

    # 顯示快取統計
    stats = fetcher.get_stats()
    print()
    info(f"快取命中: {stats['cache_hits']} | API 呼叫: {stats['api_calls']} | 配額耗盡次數: {stats['quota_hits']}")
    info(f"快取大小: {stats['cache_db_size_kb']} KB")

    if not results:
        err("\n無有效評分結果")
        print("可能原因:")
        print("  1. FinMind 免費額度已用完 → 等一小時或加 --token")
        print("  2. 網路問題 → 確認能連到 api.finmindtrade.com")
        print("  3. 股票代號錯誤 → 確認是台股代號")
        print("\n提示: 已抓到的資料都存在快取裡, 下次重跑會接續, 不會重抓")
        return None

    df = pd.DataFrame([{
        "stock_id": r["stock_id"],
        "stock_name": name_map.get(r["stock_id"], ""),
        "總分": r["total_score"],
        "技術分": r["tech_score"],
        "籌碼分": r["chip_score"],
        "籌碼資料": "v" if r["chip_available"] else "x",
        **{f"T_{k}": round(v, 1) for k, v in r["tech_sub"].items()},
        **{f"C_{k}": round(v, 1) for k, v in r["chip_sub"].items()},
    } for r in results])
    df = df.sort_values("總分", ascending=False).reset_index(drop=True)
    df.insert(0, "排名", df.index + 1)

    header("排序結果")
    print(df.to_string(index=False))

    out = Path(output_dir) / f"ranking_{end_date.replace('-', '')}.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    ok(f"已存檔: {out}")
    return df


# ============================================================
# 6. 模式 B: 月度換股回測
# ============================================================
def cmd_backtest(stocks, start_date, end_date, top_n, token, output_dir, cache_path, quota_policy):
    fetcher = FinMindCachedFetcher(
        token=token,
        cache_path=cache_path,
        on_quota_exceeded=quota_policy,
    )
    engine = ScoreEngine(fetcher)

    rebalance_dates = pd.date_range(start_date, end_date, freq="MS")
    if len(rebalance_dates) < 2:
        err("回測區間至少要橫跨 2 個月")
        return None

    info(f"回測區間: {start_date} ~ {end_date}")
    info(f"換股次數: {len(rebalance_dates) - 1}")
    info(f"每月持股: 前 {top_n} 名 (等權重)")
    info(f"快取資料庫: {cache_path}")
    
    usage = fetcher.check_api_usage()
    if usage:
        info(f"API 額度: 已用 {usage['used']}/{usage['limit']}")
    print()

    # ★ 關鍵優化: 一次性預抓所有股票、所有 dataset 的完整區間
    # 之後所有月度評分都從快取讀,完全不打 API
    extended_start = (pd.to_datetime(start_date) - timedelta(days=200)).strftime("%Y-%m-%d")
    
    header(f"步驟 1: 預抓 {len(stocks)} 檔股票完整區間資料")
    info(f"日期範圍: {extended_start} ~ {end_date}")
    info(f"預計 API 呼叫: {len(stocks)} × 3 = {len(stocks)*3} 次 (若無快取)")
    print()
    
    price_cache = {}
    iterator = tqdm(stocks, desc="預抓資料", unit="檔") if HAS_TQDM else stocks
    for sid in iterator:
        try:
            # 預抓三個 dataset 的完整區間,後續從快取讀
            df_p = fetcher.get_price(sid, extended_start, end_date)
            fetcher.get_institutional(sid, extended_start, end_date)
            fetcher.get_margin(sid, extended_start, end_date)
            
            if not df_p.empty:
                price_cache[sid] = df_p.sort_values("date").reset_index(drop=True)
                if not HAS_TQDM:
                    ok(f"{sid}: 價格 {len(df_p)} 筆")
        except RuntimeError as e:
            err(f"中止預抓: {e}")
            print("\n提示: 已抓到的資料都存在快取裡, 下次重跑會接續")
            break
    
    stats = fetcher.get_stats()
    print()
    ok(f"預抓完成: 價格 {len(price_cache)}/{len(stocks)} 檔")
    info(f"快取命中: {stats['cache_hits']} | API 呼叫: {stats['api_calls']} | 配額耗盡: {stats['quota_hits']}")

    if not price_cache:
        err("沒有任何價格資料,終止回測")
        print("\n提示: 已抓到的資料都存在快取裡, 下次重跑會接續")
        return None

    # 步驟 2: 跑回測 (純本地計算,不會再打 API,除非有少抓到的小區間)
    header("步驟 2: 月度評分與回測")
    
    monthly_results = []
    for idx, rb_date in enumerate(rebalance_dates[:-1]):
        next_rb = rebalance_dates[idx + 1]
        eval_date = rb_date.strftime("%Y-%m-%d")
        print(f"\n--- {eval_date} 換股 ---")

        scores = []
        eval_start = (rb_date - timedelta(days=180)).strftime("%Y-%m-%d")
        for sid in stocks:
            if sid not in price_cache:
                continue
            df_p = price_cache[sid][price_cache[sid]["date"] <= rb_date]
            if len(df_p) < 30:
                continue
            tech = engine.compute_tech_score(df_p)
            chip = engine.compute_chip_score(sid, eval_start, eval_date, df_p)
            total = tech["tech_total"] * 0.4 + chip["chip_total"] * 0.6
            scores.append({
                "stock_id": sid, "score": total,
                "tech": tech["tech_total"], "chip": chip["chip_total"],
            })

        if not scores:
            warn("該月無可評分股票")
            continue

        ranked = sorted(scores, key=lambda x: x["score"], reverse=True)[:top_n]
        picks = [r["stock_id"] for r in ranked]
        print(f"  選股: " + ", ".join(
            f"{r['stock_id']}={r['score']:.1f}" for r in ranked
        ))

        stock_returns = []
        for sid in picks:
            df_p = price_cache[sid]
            mask = (df_p["date"] >= rb_date) & (df_p["date"] < next_rb)
            df_month = df_p[mask]
            if len(df_month) < 2:
                continue
            entry = float(df_month.iloc[0]["open"])
            exit_ = float(df_month.iloc[-1]["close"])
            ret = (exit_ - entry) / entry
            stock_returns.append(ret)
            color = Fore.GREEN if ret > 0 else Fore.RED
            print(f"    {sid}: {entry:.2f} -> {exit_:.2f} {color}({ret*100:+.2f}%){Style.RESET_ALL}")

        if stock_returns:
            month_ret = float(np.mean(stock_returns))
            monthly_results.append({
                "date": eval_date,
                "picks": "|".join(picks),
                "return": month_ret,
            })
            color = Fore.GREEN if month_ret > 0 else Fore.RED
            print(f"  月報酬 (等權重): {color}{month_ret*100:+.2f}%{Style.RESET_ALL}")

    if not monthly_results:
        err("回測無結果")
        return None

    df_bt = pd.DataFrame(monthly_results)
    df_bt["cumulative"] = (1 + df_bt["return"]).cumprod() - 1

    total_return = df_bt["cumulative"].iloc[-1]
    win_rate = (df_bt["return"] > 0).mean()
    avg = df_bt["return"].mean()
    std = df_bt["return"].std()
    sharpe = avg / std * np.sqrt(12) if std > 0 else 0
    max_dd = (df_bt["cumulative"] - df_bt["cumulative"].cummax()).min()

    header("回測總結")
    print(f"  期間           : {start_date} ~ {end_date}")
    print(f"  換股次數       : {len(df_bt)}")
    print(f"  累積報酬       : {total_return*100:+.2f}%")
    print(f"  月均報酬       : {avg*100:+.2f}%")
    print(f"  勝率           : {win_rate*100:.1f}%")
    print(f"  月報酬標準差   : {std*100:.2f}%")
    print(f"  年化夏普比率   : {sharpe:.2f}")
    print(f"  最大回撤       : {max_dd*100:.2f}%")

    out = Path(output_dir) / f"backtest_{start_date.replace('-','')}_{end_date.replace('-','')}.csv"
    df_bt.to_csv(out, index=False, encoding="utf-8-sig")
    ok(f"已存檔: {out}")
    return df_bt


# ============================================================
# 7. CLI 入口
# ============================================================
def build_parser():
    p = argparse.ArgumentParser(
        prog="stock_scorer",
        description="台股技術面 + 籌碼面評分排序系統 (FinMind 資料源)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例:
  # 查詢當前 API 額度
  python stock_scorer.py quota --token YOUR_TOKEN
  
  # 單次評分排序
  python stock_scorer.py rank --stocks 2330,2317,2454 --token YOUR_TOKEN
  python stock_scorer.py rank --stocks stocks.txt --token YOUR_TOKEN
  
  # 月度回測 (碰到 402 自動等 60 分鐘)
  python stock_scorer.py backtest --stocks stocks.txt --token YOUR_TOKEN \\
      --start 2024-01-01 --end 2024-12-31 --on-quota wait
  
  # 兩種都跑
  python stock_scorer.py both --stocks stocks.csv --top-n 5 --token YOUR_TOKEN

  # 查看快取狀態 / 清空快取
  python stock_scorer.py cache info
  python stock_scorer.py cache clear

402 配額耗盡的處理 (--on-quota):
  ask    互動詢問 (預設)         - 跑到一半問你要等/跳過/中止
  wait   自動等 60 分鐘後重試     - 適合無人值守長時間跑
  abort  立即中止                  - 已抓資料留在快取,下次重跑會接續

★ 重要: 所有 API 資料都會存到本地 SQLite 快取 (finmind_cache.db),
       下次跑同樣的股票/日期區間時, 完全不打 API, 直接從本地讀.
       這是解決 402 配額限制最有效的方式.

清單檔案格式 (stocks.txt):
  # 註解
  2330
  2317
  2454, 2308   <- 可同行多檔以逗號分隔
""",
    )
    sub = p.add_subparsers(dest="mode", required=True, help="執行模式")

    # 共用參數
    def add_common(sp):
        sp.add_argument("--stocks", "-s", required=True,
                        help="股票清單: 逗號分隔字串 (2330,2317) 或檔案路徑 (.txt/.csv)")
        sp.add_argument("--token", "-t", default="",
                        help="FinMind API token (強烈建議申請以提高額度)")
        sp.add_argument("--output", "-o", default=".",
                        help="輸出資料夾 (預設當前目錄)")
        sp.add_argument("--cache", default="finmind_cache.db",
                        help="本地快取 SQLite 路徑 (預設 finmind_cache.db)")
        sp.add_argument("--on-quota", default="ask",
                        choices=["ask", "wait", "abort"],
                        help="碰到 402 配額耗盡時: ask=互動詢問(預設), wait=自動等60分鐘, abort=立即中止")

    # rank
    sp_r = sub.add_parser("rank", help="模式 A: 單次評分排序")
    add_common(sp_r)
    sp_r.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"),
                      help="評分截止日 YYYY-MM-DD (預設今天)")
    sp_r.add_argument("--lookback", type=int, default=180,
                      help="回看天數 (預設 180 天)")

    # backtest
    sp_b = sub.add_parser("backtest", help="模式 B: 月度換股回測")
    add_common(sp_b)
    default_end = datetime.now().strftime("%Y-%m-%d")
    default_start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    sp_b.add_argument("--start", default=default_start,
                      help=f"回測起始日 (預設 {default_start})")
    sp_b.add_argument("--end", default=default_end,
                      help=f"回測結束日 (預設 {default_end})")
    sp_b.add_argument("--top-n", type=int, default=3,
                      help="每月持有前 N 名 (預設 3)")

    # both
    sp_a = sub.add_parser("both", help="兩種模式都跑")
    add_common(sp_a)
    sp_a.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    sp_a.add_argument("--lookback", type=int, default=180)
    sp_a.add_argument("--start", default=(datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d"))
    sp_a.add_argument("--top-n", type=int, default=3)
    
    # cache 管理子命令
    sp_c = sub.add_parser("cache", help="快取管理")
    sp_c.add_argument("action", choices=["info", "clear"], help="info=顯示快取內容, clear=清空快取")
    sp_c.add_argument("--cache", default="finmind_cache.db", help="快取檔路徑")
    
    # quota 查詢 API 額度
    sp_q = sub.add_parser("quota", help="查詢 FinMind API 額度")
    sp_q.add_argument("--token", "-t", required=True, help="FinMind API token")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    # cache 管理
    if args.mode == "cache":
        from finmind_cache import FinMindCachedFetcher
        fetcher = FinMindCachedFetcher(cache_path=args.cache, verbose=False)
        if args.action == "info":
            info(f"快取檔: {args.cache}")
            df = fetcher.cache_info()
            if df.empty:
                warn("快取為空")
            else:
                print(df.to_string(index=False))
                stats = fetcher.get_stats()
                info(f"\n快取檔大小: {stats['cache_db_size_kb']} KB")
        elif args.action == "clear":
            confirm = input(f"確定要清空 {args.cache}? [y/N]: ").strip().lower()
            if confirm == "y":
                fetcher.clear_cache()
                ok("已清空")
            else:
                info("取消")
        return
    
    # quota 查詢
    if args.mode == "quota":
        from finmind_cache import FinMindCachedFetcher
        fetcher = FinMindCachedFetcher(token=args.token, verbose=False)
        usage = fetcher.check_api_usage()
        if usage:
            ok(f"API 額度: 已用 {usage['used']}/{usage['limit']}")
        else:
            err("無法查詢額度 (token 可能無效)")
        return

    # 載入股票清單
    try:
        stocks, names = load_stock_list(args.stocks)
    except Exception as e:
        err(f"無法讀取股票清單: {e}")
        sys.exit(1)

    if not stocks:
        err("股票清單為空")
        sys.exit(1)

    # 確保輸出資料夾存在
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    info(f"載入 {len(stocks)} 檔股票: {', '.join(stocks[:10])}{'...' if len(stocks) > 10 else ''}")

    if args.mode == "rank":
        header("模式 A - 單次評分排序")
        cmd_rank(stocks, args.end, args.lookback, args.token, out_dir,
                 args.cache, args.on_quota, names)

    elif args.mode == "backtest":
        header("模式 B - 月度換股回測")
        cmd_backtest(stocks, args.start, args.end, args.top_n, args.token, out_dir,
                     args.cache, args.on_quota)

    elif args.mode == "both":
        header("模式 A - 單次評分排序")
        cmd_rank(stocks, args.end, args.lookback, args.token, out_dir,
                 args.cache, args.on_quota, names)
        header("模式 B - 月度換股回測")
        cmd_backtest(stocks, args.start, args.end, args.top_n, args.token, out_dir,
                     args.cache, args.on_quota)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        warn("使用者中斷")
        sys.exit(130)
