"""
台股技術面 + 籌碼面 評分排序系統
==================================
資料來源: FinMind (https://finmindtrade.com)
權重: 技術面 40% / 籌碼面 60%

功能:
1. 給定股票清單,計算每檔的技術面/籌碼面分數並排序
2. 兩種回測:
   (A) 單次評分排序 (用某個截止日的分數排出推薦清單)
   (B) 月度換股回測 (每月第一個交易日選前 N 名持有一個月)

使用方式:
    python stock_scoring_system.py

需要安裝:
    pip install pandas numpy requests matplotlib
"""

import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
import time
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# 1. FinMind 資料抓取
# ============================================================
class FinMindFetcher:
    """FinMind API 資料抓取器
    
    免費版每小時 600 次請求, 註冊後可申請 token 提高額度
    https://finmindtrade.com
    """
    BASE_URL = "https://api.finmindtrade.com/api/v4/data"
    
    def __init__(self, token: str = "", verbose: bool = True):
        self.token = token  # 可留空使用免費額度
        self.verbose = verbose
        self._first_call = True
    
    def _request(self, dataset: str, stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
        params = {
            "dataset": dataset,
            "data_id": stock_id,
            "start_date": start_date,
            "end_date": end_date,
        }
        if self.token:
            params["token"] = self.token
        
        try:
            resp = requests.get(self.BASE_URL, params=params, timeout=20)
            
            # HTTP 錯誤
            if resp.status_code != 200:
                if self.verbose:
                    print(f"  [HTTP {resp.status_code}] {dataset} {stock_id}: {resp.text[:200]}")
                return pd.DataFrame()
            
            data = resp.json()
            status = data.get("status")
            msg = data.get("msg", "")
            payload = data.get("data", [])
            
            # FinMind 業務層錯誤 (例如需要 token / 超過額度)
            if status != 200:
                if self.verbose:
                    print(f"  [FinMind status={status}] {dataset} {stock_id}: msg='{msg}'")
                return pd.DataFrame()
            
            # 成功但無資料
            if not payload:
                if self.verbose:
                    print(f"  [空資料] {dataset} {stock_id} ({start_date}~{end_date}): msg='{msg}'")
                return pd.DataFrame()
            
            df = pd.DataFrame(payload)
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
            return df
            
        except requests.exceptions.Timeout:
            if self.verbose:
                print(f"  [Timeout] {dataset} {stock_id}")
            return pd.DataFrame()
        except Exception as e:
            if self.verbose:
                print(f"  [Exception] {dataset} {stock_id}: {type(e).__name__}: {e}")
            return pd.DataFrame()
    
    def get_price(self, stock_id: str, start: str, end: str) -> pd.DataFrame:
        """股價 (開高低收量)"""
        return self._request("TaiwanStockPrice", stock_id, start, end)
    
    def get_institutional(self, stock_id: str, start: str, end: str) -> pd.DataFrame:
        """三大法人買賣超"""
        return self._request("TaiwanStockInstitutionalInvestorsBuySell", stock_id, start, end)
    
    def get_margin(self, stock_id: str, start: str, end: str) -> pd.DataFrame:
        """融資融券"""
        return self._request("TaiwanStockMarginPurchaseShortSale", stock_id, start, end)
    
    def get_shareholding(self, stock_id: str, start: str, end: str) -> pd.DataFrame:
        """股權分散 (大戶持股)"""
        return self._request("TaiwanStockShareholding", stock_id, start, end)


# ============================================================
# 2. 技術面指標計算
# ============================================================
class TechnicalIndicators:
    """計算各種技術指標"""
    
    @staticmethod
    def sma(s: pd.Series, n: int) -> pd.Series:
        return s.rolling(n, min_periods=1).mean()
    
    @staticmethod
    def ema(s: pd.Series, n: int) -> pd.Series:
        return s.ewm(span=n, adjust=False).mean()
    
    @staticmethod
    def rsi(close: pd.Series, n: int = 14) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(n).mean()
        loss = (-delta.clip(upper=0)).rolling(n).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))
    
    @staticmethod
    def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
        ema_fast = TechnicalIndicators.ema(close, fast)
        ema_slow = TechnicalIndicators.ema(close, slow)
        dif = ema_fast - ema_slow
        dem = TechnicalIndicators.ema(dif, signal)
        hist = dif - dem
        return dif, dem, hist
    
    @staticmethod
    def kd(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9):
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
    """技術面 + 籌碼面綜合評分
    
    所有子分數標準化到 0-100, 最後加權平均
    技術面權重 40%, 籌碼面權重 60%
    """
    
    TECH_WEIGHT = 0.40
    CHIP_WEIGHT = 0.60
    
    def __init__(self, fetcher: FinMindFetcher):
        self.fetcher = fetcher
        self.ti = TechnicalIndicators()
    
    # ---------- 技術面子分數 ----------
    def _score_ma_alignment(self, close: pd.Series) -> float:
        """均線多頭排列: 5 > 20 > 60 > 120"""
        if len(close) < 120:
            return 50.0
        ma5 = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1]
        ma120 = close.rolling(120).mean().iloc[-1]
        
        conditions = [ma5 > ma20, ma20 > ma60, ma60 > ma120,
                      close.iloc[-1] > ma5, close.iloc[-1] > ma60]
        return sum(conditions) / len(conditions) * 100
    
    def _score_ma_slope(self, close: pd.Series) -> float:
        """均線斜率: 看 20 日均線最近 5 日斜率"""
        if len(close) < 25:
            return 50.0
        ma20 = close.rolling(20).mean()
        slope = (ma20.iloc[-1] - ma20.iloc[-6]) / ma20.iloc[-6] * 100
        # slope > 2% 給 100, < -2% 給 0
        return float(np.clip((slope + 2) / 4 * 100, 0, 100))
    
    def _score_rsi(self, close: pd.Series) -> float:
        """RSI: 50-70 區間最理想"""
        if len(close) < 15:
            return 50.0
        rsi = self.ti.rsi(close).iloc[-1]
        if pd.isna(rsi):
            return 50.0
        # 50-70 給滿分, 30 以下和 80 以上扣分
        if 50 <= rsi <= 70:
            return 100.0
        elif 40 <= rsi < 50:
            return 70 + (rsi - 40) * 3
        elif 70 < rsi <= 80:
            return 100 - (rsi - 70) * 3
        elif rsi < 40:
            return max(0, rsi)
        else:  # > 80
            return max(0, 100 - (rsi - 80) * 5)
    
    def _score_macd(self, close: pd.Series) -> float:
        """MACD 柱狀體由負轉正、DIF 上穿"""
        if len(close) < 35:
            return 50.0
        dif, dem, hist = self.ti.macd(close)
        score = 50.0
        # DIF > 0 加分
        if dif.iloc[-1] > 0:
            score += 15
        # 柱狀體為正
        if hist.iloc[-1] > 0:
            score += 15
        # 柱狀體擴張中 (動能加強)
        if hist.iloc[-1] > hist.iloc[-2]:
            score += 10
        # 黃金交叉 (近 3 日內 DIF 上穿 DEM)
        if any((dif.iloc[-i] > dem.iloc[-i]) and (dif.iloc[-i-1] <= dem.iloc[-i-1]) 
               for i in range(1, 4)):
            score += 10
        return float(np.clip(score, 0, 100))
    
    def _score_kd(self, high: pd.Series, low: pd.Series, close: pd.Series) -> float:
        """KD: 低檔黃金交叉加分,高檔死亡交叉扣分"""
        if len(close) < 15:
            return 50.0
        k, d = self.ti.kd(high, low, close)
        score = 50.0
        k_now, d_now = k.iloc[-1], d.iloc[-1]
        if pd.isna(k_now) or pd.isna(d_now):
            return 50.0
        # K > D 加分
        if k_now > d_now:
            score += 20
        # 低檔黃金交叉
        if k_now < 50 and k.iloc[-2] <= d.iloc[-2] and k_now > d_now:
            score += 30
        # 高檔死亡交叉扣分
        if k_now > 80 and k.iloc[-2] >= d.iloc[-2] and k_now < d_now:
            score -= 30
        return float(np.clip(score, 0, 100))
    
    def _score_volume(self, close: pd.Series, volume: pd.Series) -> float:
        """量價配合: 上漲帶量 / 下跌縮量加分"""
        if len(close) < 21:
            return 50.0
        ret_5 = (close.iloc[-1] / close.iloc[-6] - 1) * 100
        vol_ratio = volume.iloc[-5:].mean() / volume.iloc[-20:].mean()
        
        score = 50.0
        if ret_5 > 0 and vol_ratio > 1.2:  # 上漲帶量
            score = 85
        elif ret_5 > 0 and vol_ratio > 1.0:
            score = 70
        elif ret_5 < 0 and vol_ratio < 0.8:  # 下跌縮量
            score = 65
        elif ret_5 < 0 and vol_ratio > 1.2:  # 下跌爆量 (壞)
            score = 20
        return score
    
    def compute_tech_score(self, df_price: pd.DataFrame) -> dict:
        """計算技術面總分"""
        if df_price.empty or len(df_price) < 30:
            return {"tech_total": 0, "subscores": {}}
        
        df = df_price.sort_values("date").copy()
        close = df["close"].astype(float)
        high = df["max"].astype(float)
        low = df["min"].astype(float)
        volume = df["Trading_Volume"].astype(float)
        
        sub = {
            "ma_alignment": self._score_ma_alignment(close),
            "ma_slope": self._score_ma_slope(close),
            "rsi": self._score_rsi(close),
            "macd": self._score_macd(close),
            "kd": self._score_kd(high, low, close),
            "volume": self._score_volume(close, volume),
        }
        # 子項權重 (簡單平均)
        tech_total = np.mean(list(sub.values()))
        return {"tech_total": tech_total, "subscores": sub}
    
    # ---------- 籌碼面子分數 ----------
    def _score_foreign(self, df_inst: pd.DataFrame) -> float:
        """外資買賣超: 近 5/20 日累計"""
        if df_inst.empty:
            return 50.0
        df = df_inst[df_inst["name"].isin(["Foreign_Investor", "Foreign_Dealer_Self"])]
        if df.empty:
            return 50.0
        daily = df.groupby("date")["buy"].sum() - df.groupby("date")["sell"].sum()
        daily = daily.sort_index()
        
        if len(daily) < 5:
            return 50.0
        
        # 近 5 日累計買超(張)
        net_5 = daily.iloc[-5:].sum() / 1000
        net_20 = daily.iloc[-20:].sum() / 1000 if len(daily) >= 20 else net_5 * 4
        
        score = 50.0
        if net_5 > 0:
            score += min(25, net_5 / 100)  # 每 100 張 +1, 最多 +25
        else:
            score += max(-25, net_5 / 100)
        if net_20 > 0:
            score += min(25, net_20 / 500)
        else:
            score += max(-25, net_20 / 500)
        return float(np.clip(score, 0, 100))
    
    def _score_trust(self, df_inst: pd.DataFrame) -> float:
        """投信買賣超 + 連續性"""
        if df_inst.empty:
            return 50.0
        df = df_inst[df_inst["name"] == "Investment_Trust"]
        if df.empty:
            return 50.0
        daily = (df.groupby("date")["buy"].sum() - df.groupby("date")["sell"].sum()).sort_index()
        
        if len(daily) < 5:
            return 50.0
        
        # 近 5 日連續買超天數
        recent = daily.iloc[-5:]
        buy_days = (recent > 0).sum()
        net_5 = recent.sum() / 1000
        
        score = 50.0
        score += buy_days * 8  # 每天連續買超 +8
        if net_5 > 0:
            score += min(15, net_5 / 50)
        else:
            score += max(-20, net_5 / 50)
        return float(np.clip(score, 0, 100))
    
    def _score_margin(self, df_margin: pd.DataFrame, df_price: pd.DataFrame) -> float:
        """融資減少 + 股價上漲 = 籌碼乾淨"""
        if df_margin.empty or df_price.empty:
            return 50.0
        df_margin = df_margin.sort_values("date")
        if len(df_margin) < 5:
            return 50.0
        
        # 近 5 日融資餘額變化率
        margin_now = df_margin["MarginPurchaseTodayBalance"].iloc[-1]
        margin_5d_ago = df_margin["MarginPurchaseTodayBalance"].iloc[-6] if len(df_margin) >= 6 else df_margin["MarginPurchaseTodayBalance"].iloc[0]
        margin_change = (margin_now - margin_5d_ago) / margin_5d_ago * 100 if margin_5d_ago > 0 else 0
        
        # 同期股價變化
        df_p = df_price.sort_values("date")
        price_now = df_p["close"].iloc[-1]
        price_5d_ago = df_p["close"].iloc[-6] if len(df_p) >= 6 else df_p["close"].iloc[0]
        price_change = (price_now - price_5d_ago) / price_5d_ago * 100
        
        score = 50.0
        # 漲價且融資減 = 最佳
        if price_change > 0 and margin_change < 0:
            score = 90
        elif price_change > 0 and margin_change < 3:
            score = 70
        elif price_change > 0 and margin_change > 5:
            score = 30  # 漲價但融資大增,籌碼變散
        elif price_change < 0 and margin_change < -3:
            score = 65  # 跌但融資也減,賣壓宣洩
        elif price_change < 0 and margin_change > 0:
            score = 20  # 跌且融資增,套牢加深
        return score
    
    def _score_shareholding(self, df_share: pd.DataFrame) -> float:
        """大戶持股集中度變化"""
        if df_share.empty or len(df_share) < 2:
            return 50.0
        df = df_share.sort_values("date")
        # 大戶 = HoldingSharesLevel 較大的級距, 簡化為看千張大戶比例變化
        # FinMind 欄位: HoldingSharesLevel, percent
        # 取 >1000 張 (level >= 15 通常)
        latest = df.iloc[-1] if "percent" not in df.columns else None
        # 為避免欄位不一致風險, 用簡化邏輯: 比較第一筆與最後一筆
        try:
            first_high = df.iloc[:5]["percent"].sum() if "percent" in df.columns else 50
            last_high = df.iloc[-5:]["percent"].sum() if "percent" in df.columns else 50
            change = last_high - first_high
            score = 50 + change * 5  # 每增加 1% 籌碼集中 +5
            return float(np.clip(score, 0, 100))
        except:
            return 50.0
    
    def compute_chip_score(self, stock_id: str, start: str, end: str, df_price: pd.DataFrame) -> dict:
        """計算籌碼面總分"""
        df_inst = self.fetcher.get_institutional(stock_id, start, end)
        time.sleep(0.3)  # 避免打太快
        df_margin = self.fetcher.get_margin(stock_id, start, end)
        time.sleep(0.3)
        
        available = not df_inst.empty or not df_margin.empty
        
        sub = {
            "foreign": self._score_foreign(df_inst),
            "trust": self._score_trust(df_inst),
            "margin": self._score_margin(df_margin, df_price),
        }
        chip_total = np.mean(list(sub.values()))
        return {"chip_total": chip_total, "subscores": sub, "available": available}
    
    # ---------- 總分 ----------
    def score_stock(self, stock_id: str, start: str, end: str) -> dict:
        """完整評分一檔股票
        
        策略:
        - 若價格資料抓不到 → 整檔失敗,回傳 None
        - 若籌碼資料抓不到 → 籌碼分用 50 中性值,標記 chip_available=False
        """
        df_price = self.fetcher.get_price(stock_id, start, end)
        time.sleep(0.3)
        if df_price.empty:
            print(f"  ⚠️  {stock_id} 無價格資料,跳過")
            return None
        
        print(f"  ✓ {stock_id} 價格資料 {len(df_price)} 筆 ({df_price['date'].min().date()} ~ {df_price['date'].max().date()})")
        
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
            "chip_available": chip.get("available", True),
        }


# ============================================================
# 4. 回測 (A) 單次評分排序
# ============================================================
def rank_stocks(stock_list: list, end_date: str, lookback_days: int = 180, token: str = "") -> pd.DataFrame:
    """對一份股票清單做單次評分排序
    
    Args:
        stock_list: 股票代號清單 e.g. ["2330", "2317", "2454"]
        end_date: 評分截止日 (YYYY-MM-DD)
        lookback_days: 取多少天歷史資料計算指標
        token: FinMind API token (可空)
    
    Returns:
        排序後的 DataFrame
    """
    fetcher = FinMindFetcher(token)
    engine = ScoreEngine(fetcher)
    
    end_dt = pd.to_datetime(end_date)
    start_dt = end_dt - timedelta(days=lookback_days)
    start = start_dt.strftime("%Y-%m-%d")
    
    print(f"評分區間: {start} ~ {end_date}")
    print(f"FinMind Token: {'有設定 (' + token[:8] + '...)' if token else '無 (使用免費額度)'}\n")
    
    results = []
    for i, sid in enumerate(stock_list, 1):
        print(f"[{i}/{len(stock_list)}] 評分 {sid}...")
        try:
            r = engine.score_stock(sid, start, end_date)
            if r:
                results.append(r)
                chip_note = "" if r["chip_available"] else "  (⚠️ 籌碼資料缺失,籌碼分為預設中性值)"
                print(f"      → 總分 {r['total_score']:.1f} | 技術 {r['tech_score']:.1f} | 籌碼 {r['chip_score']:.1f}{chip_note}")
        except Exception as e:
            import traceback
            print(f"  失敗: {type(e).__name__}: {e}")
            traceback.print_exc()
    
    if not results:
        print("\n❌ 無有效評分結果")
        print("可能原因:")
        print("  1. FinMind 免費額度已用完 → 註冊取得 token 填入 FINMIND_TOKEN")
        print("  2. 網路問題 → 檢查能否連到 api.finmindtrade.com")
        print("  3. 股票代號錯誤 → 確認是台股代號")
        return pd.DataFrame()
    
    df = pd.DataFrame([{
        "stock_id": r["stock_id"],
        "總分": r["total_score"],
        "技術分": r["tech_score"],
        "籌碼分": r["chip_score"],
        **{f"T_{k}": round(v, 1) for k, v in r["tech_sub"].items()},
        **{f"C_{k}": round(v, 1) for k, v in r["chip_sub"].items()},
    } for r in results])
    
    df = df.sort_values("總分", ascending=False).reset_index(drop=True)
    df.insert(0, "排名", df.index + 1)
    return df


# ============================================================
# 5. 回測 (B) 月度換股回測
# ============================================================
def monthly_rebalance_backtest(
    stock_list: list,
    start_date: str,
    end_date: str,
    top_n: int = 5,
    token: str = "",
) -> dict:
    """每月第一個交易日選總分前 N 名,持有一個月
    
    Args:
        stock_list: 候選股票清單
        start_date: 回測起始 (YYYY-MM-DD)
        end_date: 回測結束 (YYYY-MM-DD)
        top_n: 每月持有幾檔
    
    Returns:
        {monthly_returns, cumulative, holdings}
    """
    fetcher = FinMindFetcher(token)
    engine = ScoreEngine(fetcher)
    
    # 產生月度換股日期
    rebalance_dates = pd.date_range(start_date, end_date, freq="MS")  # 每月第一天
    
    # 先把所有股票的價格資料抓下來 (一次抓完整區間)
    print(f"\n=== 預先抓取 {len(stock_list)} 檔股票價格 (節省 API 呼叫) ===")
    extended_start = (pd.to_datetime(start_date) - timedelta(days=200)).strftime("%Y-%m-%d")
    price_cache = {}
    for i, sid in enumerate(stock_list, 1):
        print(f"  [{i}/{len(stock_list)}] 抓 {sid} 價格...")
        df = fetcher.get_price(sid, extended_start, end_date)
        if not df.empty:
            price_cache[sid] = df.sort_values("date").reset_index(drop=True)
            print(f"      ✓ {len(df)} 筆")
        else:
            print(f"      ✗ 無資料")
        time.sleep(0.4)
    
    print(f"\n價格快取完成: {len(price_cache)}/{len(stock_list)} 檔有資料")
    
    if not price_cache:
        return {"summary": "❌ 沒有任何價格資料,請檢查 FinMind 額度或網路"}
    
    monthly_results = []
    
    for idx, rb_date in enumerate(rebalance_dates[:-1]):
        next_rb = rebalance_dates[idx + 1]
        eval_date = rb_date.strftime("%Y-%m-%d")
        
        print(f"\n=== {eval_date} 換股 ===")
        
        # 在 rb_date 為截止日做評分
        scores = []
        eval_start = (rb_date - timedelta(days=180)).strftime("%Y-%m-%d")
        for sid in stock_list:
            if sid not in price_cache:
                continue
            # 截取到 rb_date 為止的價格
            df_p = price_cache[sid][price_cache[sid]["date"] <= rb_date].copy()
            if len(df_p) < 30:
                continue
            
            tech = engine.compute_tech_score(df_p)
            chip = engine.compute_chip_score(sid, eval_start, eval_date, df_p)
            total = tech["tech_total"] * 0.4 + chip["chip_total"] * 0.6
            scores.append({"stock_id": sid, "score": total, "tech": tech["tech_total"], "chip": chip["chip_total"]})
        
        if not scores:
            print(f"  ⚠️ 無法評分 (可能該日期前資料不足 30 天)")
            continue
        
        # 選前 N 名
        ranked = sorted(scores, key=lambda x: x["score"], reverse=True)[:top_n]
        picks = [r["stock_id"] for r in ranked]
        print(f"  評分結果 (前 {top_n}): " + ", ".join(
            f"{r['stock_id']}={r['score']:.1f}(T{r['tech']:.0f}/C{r['chip']:.0f})" for r in ranked
        ))
        
        # 計算這個月的等權重報酬
        stock_returns = []
        for sid in picks:
            df_p = price_cache[sid]
            # 找 rb_date 之後第一個交易日的開盤價, next_rb 之前最後一個交易日的收盤價
            mask = (df_p["date"] >= rb_date) & (df_p["date"] < next_rb)
            df_month = df_p[mask]
            if len(df_month) < 2:
                continue
            entry = df_month.iloc[0]["open"]
            exit_ = df_month.iloc[-1]["close"]
            ret = (exit_ - entry) / entry
            stock_returns.append(ret)
            print(f"    {sid}: {entry:.2f} -> {exit_:.2f} ({ret*100:+.2f}%)")
        
        if stock_returns:
            month_ret = np.mean(stock_returns)
            monthly_results.append({
                "date": eval_date,
                "picks": picks,
                "return": month_ret,
            })
            print(f"  月報酬 (等權重): {month_ret*100:+.2f}%")
    
    if not monthly_results:
        return {"summary": "無回測結果"}
    
    df_bt = pd.DataFrame(monthly_results)
    df_bt["cumulative"] = (1 + df_bt["return"]).cumprod() - 1
    
    total_return = df_bt["cumulative"].iloc[-1]
    win_rate = (df_bt["return"] > 0).mean()
    avg_return = df_bt["return"].mean()
    std_return = df_bt["return"].std()
    sharpe = avg_return / std_return * np.sqrt(12) if std_return > 0 else 0
    
    summary = {
        "期間": f"{start_date} ~ {end_date}",
        "換股次數": len(df_bt),
        "累積報酬": f"{total_return*100:.2f}%",
        "月均報酬": f"{avg_return*100:.2f}%",
        "勝率": f"{win_rate*100:.1f}%",
        "月報酬標準差": f"{std_return*100:.2f}%",
        "年化夏普比率": f"{sharpe:.2f}",
    }
    
    return {"summary": summary, "monthly": df_bt}


# ============================================================
# 6. 主程式
# ============================================================
if __name__ == "__main__":
    # ===== 在這裡修改你的設定 =====
    
    # 你篩選出來的股票清單 (台股代號)
    MY_STOCK_LIST = [
        "2330",  # 台積電
        "2317",  # 鴻海
        "2454",  # 聯發科
        "2308",  # 台達電
        "2412",  # 中華電
        "2882",  # 國泰金
        "1303",  # 南亞
        "2002",  # 中鋼
        "3008",  # 大立光
        "2603",  # 長榮
    ]
    
    FINMIND_TOKEN = ""  # 留空使用免費額度,或填你的 token
    
    # ----- 模式 A: 單次評分排序 -----
    print("=" * 70)
    print("【模式 A】單次評分排序")
    print("=" * 70)
    
    end_date = datetime.now().strftime("%Y-%m-%d")
    ranking = rank_stocks(MY_STOCK_LIST, end_date, lookback_days=180, token=FINMIND_TOKEN)
    
    if not ranking.empty:
        print("\n推薦排序:")
        print(ranking.to_string(index=False))
        ranking.to_csv("/mnt/user-data/outputs/ranking_result.csv", index=False, encoding="utf-8-sig")
        print(f"\n✓ 已存檔: ranking_result.csv")
    
    # ----- 模式 B: 月度換股回測 -----
    print("\n" + "=" * 70)
    print("【模式 B】月度換股回測")
    print("=" * 70)
    
    # 回測過去一年
    bt_end = datetime.now().strftime("%Y-%m-%d")
    bt_start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    
    bt_result = monthly_rebalance_backtest(
        MY_STOCK_LIST,
        start_date=bt_start,
        end_date=bt_end,
        top_n=3,
        token=FINMIND_TOKEN,
    )
    
    print("\n=== 回測總結 ===")
    if isinstance(bt_result.get("summary"), dict):
        for k, v in bt_result["summary"].items():
            print(f"  {k}: {v}")
        bt_result["monthly"].to_csv(
            "/mnt/user-data/outputs/backtest_result.csv",
            index=False, encoding="utf-8-sig"
        )
        print("\n✓ 已存檔: backtest_result.csv")
    else:
        print(bt_result.get("summary"))
