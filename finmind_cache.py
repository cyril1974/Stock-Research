"""
FinMind 資料抓取器 (含本地 SQLite 快取)
==========================================
解決 402 配額問題的核心策略:
1. 所有抓過的資料存到本地 SQLite,下次同樣 (dataset, stock, date_range) 直接讀本地
2. 智能合併區間: 已快取 2024-01 ~ 2024-06, 再要 2024-04 ~ 2024-09 只抓 2024-07 ~ 2024-09
3. 遇到 402 配額用完: 顯示等待選項 (等一小時 / 中止)
4. 斷點續傳: 中斷後重跑會自動跳過已快取資料
5. 顯示 API 額度狀態
"""

from __future__ import annotations

import sqlite3
import time
import json
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

import pandas as pd
import requests


class FinMindCachedFetcher:
    """有本地 SQLite 快取的 FinMind 抓取器
    
    使用方式:
        fetcher = FinMindCachedFetcher(token="...", cache_path="cache.db")
        df = fetcher.get_price("2330", "2024-01-01", "2024-12-31")
        # 第二次同樣的呼叫不會打 API,直接從快取讀
    """
    
    BASE_URL = "https://api.finmindtrade.com/api/v4/data"
    USER_INFO_URL = "https://api.web.finmindtrade.com/v2/user_info"
    
    # 各 dataset 對應的快取表名
    DATASETS = {
        "TaiwanStockPrice": "price",
        "TaiwanStockInstitutionalInvestorsBuySell": "institutional",
        "TaiwanStockMarginPurchaseShortSale": "margin",
    }
    
    def __init__(
        self,
        token: str = "",
        cache_path: str = "finmind_cache.db",
        verbose: bool = True,
        on_quota_exceeded: str = "ask",  # 'ask' / 'wait' / 'abort'
        request_delay: float = 0.3,
    ):
        self.token = token
        self.verbose = verbose
        self.on_quota_exceeded = on_quota_exceeded
        self.request_delay = request_delay
        
        # 初始化 SQLite 快取
        self.cache_path = Path(cache_path)
        self._init_cache()
        
        # 統計
        self.cache_hits = 0
        self.api_calls = 0
        self.quota_hits = 0
    
    # ------------------------------------------------------------
    # SQLite 快取管理
    # ------------------------------------------------------------
    def _init_cache(self):
        """建立 SQLite 表格"""
        conn = sqlite3.connect(self.cache_path)
        c = conn.cursor()
        # 通用表: 一張表存所有 dataset 的原始 JSON
        c.execute("""
            CREATE TABLE IF NOT EXISTS api_cache (
                dataset TEXT NOT NULL,
                stock_id TEXT NOT NULL,
                date TEXT NOT NULL,
                data_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (dataset, stock_id, date)
            )
        """)
        # 記錄已抓取過的區間,避免重抓「實際上沒有資料」的日期
        c.execute("""
            CREATE TABLE IF NOT EXISTS fetched_ranges (
                dataset TEXT NOT NULL,
                stock_id TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (dataset, stock_id, start_date, end_date)
            )
        """)
        conn.commit()
        conn.close()
    
    def _query_cache(self, dataset: str, stock_id: str, start: str, end: str) -> pd.DataFrame:
        """從快取讀資料"""
        conn = sqlite3.connect(self.cache_path)
        df = pd.read_sql_query(
            "SELECT data_json FROM api_cache WHERE dataset=? AND stock_id=? AND date>=? AND date<=? ORDER BY date",
            conn, params=(dataset, stock_id, start, end),
        )
        conn.close()
        if df.empty:
            return pd.DataFrame()
        rows = [json.loads(j) for j in df["data_json"]]
        out = pd.DataFrame(rows)
        if "date" in out.columns:
            out["date"] = pd.to_datetime(out["date"])
        return out
    
    def _save_to_cache(self, dataset: str, stock_id: str, df: pd.DataFrame):
        """把資料存進快取"""
        if df.empty:
            return
        conn = sqlite3.connect(self.cache_path)
        c = conn.cursor()
        now = datetime.now().isoformat()
        rows = []
        for _, row in df.iterrows():
            date_str = str(row.get("date", ""))[:10]  # YYYY-MM-DD
            if not date_str:
                continue
            row_dict = row.to_dict()
            # 把 Timestamp 轉成字串
            for k, v in row_dict.items():
                if isinstance(v, pd.Timestamp):
                    row_dict[k] = v.strftime("%Y-%m-%d")
            rows.append((dataset, stock_id, date_str, json.dumps(row_dict, default=str), now))
        
        c.executemany(
            "INSERT OR REPLACE INTO api_cache VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        conn.close()
    
    def _mark_range_fetched(self, dataset: str, stock_id: str, start: str, end: str):
        """記錄這個區間已抓過 (即使無資料也記錄,避免重抓)"""
        conn = sqlite3.connect(self.cache_path)
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO fetched_ranges VALUES (?, ?, ?, ?, ?)",
            (dataset, stock_id, start, end, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
    
    def _is_range_fetched(self, dataset: str, stock_id: str, start: str, end: str) -> bool:
        """檢查指定區間是否完全被已抓過的區間覆蓋"""
        conn = sqlite3.connect(self.cache_path)
        c = conn.cursor()
        c.execute(
            "SELECT start_date, end_date FROM fetched_ranges WHERE dataset=? AND stock_id=?",
            (dataset, stock_id),
        )
        ranges = c.fetchall()
        conn.close()
        
        # 合併區間,看請求區間是否完全被覆蓋
        for s, e in ranges:
            if s <= start and e >= end:
                return True
        return False
    
    def _missing_ranges(self, dataset: str, stock_id: str, start: str, end: str) -> list:
        """找出需要實際從 API 抓的子區間"""
        conn = sqlite3.connect(self.cache_path)
        c = conn.cursor()
        c.execute(
            "SELECT start_date, end_date FROM fetched_ranges WHERE dataset=? AND stock_id=? ORDER BY start_date",
            (dataset, stock_id),
        )
        ranges = c.fetchall()
        conn.close()
        
        if not ranges:
            return [(start, end)]
        
        # 簡化版: 找最大已抓區間,看是否需要往前或往後補
        # 對於回測來說,通常都是擴展尾部,這個簡化版夠用
        cached_start = min(r[0] for r in ranges)
        cached_end = max(r[1] for r in ranges)
        
        missing = []
        if start < cached_start:
            missing.append((start, cached_start))
        if end > cached_end:
            missing.append((cached_end, end))
        if not missing and not (cached_start <= start and cached_end >= end):
            # 完全未覆蓋
            missing.append((start, end))
        return missing
    
    # ------------------------------------------------------------
    # 額度查詢
    # ------------------------------------------------------------
    def check_api_usage(self) -> Optional[dict]:
        """查詢目前 API 額度使用情況"""
        if not self.token:
            return None
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            resp = requests.get(self.USER_INFO_URL, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "used": data.get("user_count", "?"),
                    "limit": data.get("api_request_limit", "?"),
                }
        except Exception:
            pass
        return None
    
    # ------------------------------------------------------------
    # 配額耗盡處理
    # ------------------------------------------------------------
    def _handle_quota_exceeded(self) -> str:
        """碰到 402 時的處理策略
        
        Returns: 'retry' (繼續嘗試) / 'skip' (跳過此次) / 'abort' (整個中止)
        """
        self.quota_hits += 1
        
        if self.on_quota_exceeded == "abort":
            raise RuntimeError("FinMind 配額耗盡 (HTTP 402)")
        
        if self.on_quota_exceeded == "wait":
            wait_min = 60
            if self.verbose:
                print(f"\n[配額耗盡] 等待 {wait_min} 分鐘後重試 (Ctrl+C 中止)...")
            try:
                for remaining in range(wait_min * 60, 0, -1):
                    mins, secs = divmod(remaining, 60)
                    print(f"\r  剩餘 {mins:02d}:{secs:02d}   ", end="", flush=True)
                    time.sleep(1)
                print()
                return "retry"
            except KeyboardInterrupt:
                print("\n  使用者中止")
                raise
        
        # ask 模式: 互動式詢問
        print(f"\n[FinMind 配額耗盡] (HTTP 402)")
        print("選項:")
        print("  [w] 等 60 分鐘後重試 (配額每小時重置)")
        print("  [s] 跳過這檔股票,繼續下一檔 (該股票分數會用部分資料)")
        print("  [a] 中止整個流程,把已抓到的資料存起來下次繼續")
        while True:
            choice = input("選擇 [w/s/a]: ").strip().lower()
            if choice == "w":
                return self._wait_and_retry()
            if choice == "s":
                return "skip"
            if choice == "a":
                raise RuntimeError("使用者選擇中止 (資料已快取,下次重跑會接續)")
    
    def _wait_and_retry(self):
        wait_min = 60
        print(f"  等待 {wait_min} 分鐘 (可 Ctrl+C 中止)...")
        try:
            for remaining in range(wait_min * 60, 0, -1):
                mins, secs = divmod(remaining, 60)
                print(f"\r  剩餘 {mins:02d}:{secs:02d}   ", end="", flush=True)
                time.sleep(1)
            print()
            return "retry"
        except KeyboardInterrupt:
            print("\n  使用者中止")
            raise
    
    # ------------------------------------------------------------
    # 核心請求方法
    # ------------------------------------------------------------
    def _api_request(self, dataset: str, stock_id: str, start: str, end: str) -> tuple:
        """實際打 API
        
        Returns: (DataFrame, status)
            status: 'ok' / 'empty' / 'quota' / 'error'
        """
        params = {
            "dataset": dataset,
            "data_id": stock_id,
            "start_date": start,
            "end_date": end,
        }
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        
        try:
            self.api_calls += 1
            resp = requests.get(self.BASE_URL, headers=headers, params=params, timeout=20)
            
            # 402 = 配額耗盡
            if resp.status_code == 402:
                return pd.DataFrame(), "quota"
            
            if resp.status_code != 200:
                if self.verbose:
                    print(f"  [HTTP {resp.status_code}] {dataset} {stock_id}")
                return pd.DataFrame(), "error"
            
            data = resp.json()
            status = data.get("status")
            
            if status == 402:
                return pd.DataFrame(), "quota"
            
            if status != 200:
                if self.verbose:
                    msg = data.get("msg", "")
                    print(f"  [FinMind {status}] {dataset} {stock_id}: {msg}")
                return pd.DataFrame(), "error"
            
            payload = data.get("data", [])
            if not payload:
                return pd.DataFrame(), "empty"
            
            df = pd.DataFrame(payload)
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
            return df, "ok"
            
        except requests.exceptions.Timeout:
            if self.verbose:
                print(f"  [Timeout] {dataset} {stock_id}")
            return pd.DataFrame(), "error"
        except Exception as e:
            if self.verbose:
                print(f"  [Exception] {type(e).__name__}: {e}")
            return pd.DataFrame(), "error"
    
    def _fetch(self, dataset: str, stock_id: str, start: str, end: str) -> pd.DataFrame:
        """有快取的抓取主方法
        
        流程:
        1. 檢查請求區間是否已完全被快取覆蓋 → 直接讀快取
        2. 否則: 抓缺失部分,存入快取,再合併讀出
        3. 遇到 402: 依照 on_quota_exceeded 策略處理
        """
        # 步驟 1: 完全命中快取
        if self._is_range_fetched(dataset, stock_id, start, end):
            self.cache_hits += 1
            return self._query_cache(dataset, stock_id, start, end)
        
        # 步驟 2: 找出缺失子區間並抓取
        missing = self._missing_ranges(dataset, stock_id, start, end)
        
        for sub_start, sub_end in missing:
            while True:  # 配額重試迴圈
                df, status = self._api_request(dataset, stock_id, sub_start, sub_end)
                time.sleep(self.request_delay)
                
                if status == "ok":
                    self._save_to_cache(dataset, stock_id, df)
                    self._mark_range_fetched(dataset, stock_id, sub_start, sub_end)
                    break
                elif status == "empty":
                    # 該股票該區間真的沒資料,標記避免重抓
                    self._mark_range_fetched(dataset, stock_id, sub_start, sub_end)
                    break
                elif status == "quota":
                    action = self._handle_quota_exceeded()
                    if action == "retry":
                        continue  # 重試這個子區間
                    elif action == "skip":
                        return self._query_cache(dataset, stock_id, start, end)  # 用現有快取
                elif status == "error":
                    break  # 其他錯誤不重試
        
        # 步驟 3: 從快取讀回完整資料
        return self._query_cache(dataset, stock_id, start, end)
    
    # ------------------------------------------------------------
    # 公開介面 (跟原本相容)
    # ------------------------------------------------------------
    def get_price(self, stock_id, start, end):
        return self._fetch("TaiwanStockPrice", stock_id, start, end)

    def get_institutional(self, stock_id, start, end):
        return self._fetch("TaiwanStockInstitutionalInvestorsBuySell", stock_id, start, end)

    def get_margin(self, stock_id, start, end):
        return self._fetch("TaiwanStockMarginPurchaseShortSale", stock_id, start, end)

    def get_stock_names(self, stock_ids: list) -> dict:
        """取得股票中文名稱，結果快取 7 天避免重複請求

        Returns: {stock_id: stock_name}
        """
        conn = sqlite3.connect(self.cache_path)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS stock_info (
                stock_id TEXT PRIMARY KEY,
                stock_name TEXT,
                fetched_at TEXT
            )
        """)
        conn.commit()

        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        placeholders = ",".join("?" * len(stock_ids))
        rows = c.execute(
            f"SELECT stock_id, stock_name FROM stock_info WHERE stock_id IN ({placeholders}) AND fetched_at > ?",
            stock_ids + [cutoff],
        ).fetchall()
        conn.close()

        cached = {r[0]: r[1] for r in rows}
        missing = [sid for sid in stock_ids if sid not in cached]

        if missing:
            try:
                params = {"dataset": "TaiwanStockInfo"}
                if self.token:
                    params["token"] = self.token
                resp = requests.get(self.BASE_URL, params=params, timeout=20)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == 200:
                        info_map = {
                            str(r.get("stock_id", "")): r.get("company_name", "")
                            for r in data.get("data", [])
                        }
                        conn = sqlite3.connect(self.cache_path)
                        c = conn.cursor()
                        now = datetime.now().isoformat()
                        for sid in missing:
                            name = info_map.get(sid, "")
                            c.execute(
                                "INSERT OR REPLACE INTO stock_info VALUES (?, ?, ?)",
                                (sid, name, now),
                            )
                            cached[sid] = name
                        conn.commit()
                        conn.close()
            except Exception:
                pass

        return cached
    
    # ------------------------------------------------------------
    # 統計與管理
    # ------------------------------------------------------------
    def get_stats(self) -> dict:
        return {
            "cache_hits": self.cache_hits,
            "api_calls": self.api_calls,
            "quota_hits": self.quota_hits,
            "cache_db_size_kb": round(self.cache_path.stat().st_size / 1024, 1) if self.cache_path.exists() else 0,
        }
    
    def cache_info(self) -> pd.DataFrame:
        """顯示快取內容統計"""
        conn = sqlite3.connect(self.cache_path)
        df = pd.read_sql_query("""
            SELECT dataset, stock_id, COUNT(*) as rows, 
                   MIN(date) as start_date, MAX(date) as end_date
            FROM api_cache
            GROUP BY dataset, stock_id
            ORDER BY dataset, stock_id
        """, conn)
        conn.close()
        return df
    
    def clear_cache(self):
        """清除整個快取"""
        if self.cache_path.exists():
            self.cache_path.unlink()
        self._init_cache()
