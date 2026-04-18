"""
Analysis Engine -- R-Factor, Sector Performance, Institutional Zones
Options Analyzer Pro

Institutional Zone logic:
  Demand Zone  = bearish origin candle + LOOKBACK consecutive bullish candles after it
  Supply Zone  = bullish origin candle + LOOKBACK consecutive bearish candles after it
  (Pine Script / institutional zone scanner logic)
"""
import threading
import time
import random
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from sector_data import SECTOR_STOCKS

# Global storage for Fyers instance when connected
_fyers_instance = None
def set_fyers_instance(fyers):
    global _fyers_instance
    _fyers_instance = fyers

# ── Constants ──────────────────────────────────────────────────────────────────
LOOKBACK      = 5        # consecutive confirming candles
INTERVAL      = "5m"     # yfinance interval
PERIOD        = "5d"     # yfinance period
TOP_N         = 10       # top results per side
SCAN_EVERY_S  = 180      # re-scan every 3 minutes (Zones)

# ── Cached zone scan results (updated by background thread) ───────────────────
_zone_cache = {
    "demand":    [],   # BUY SIDE top N
    "supply":    [],   # SELL SIDE top N
    "scan_time": "--",
    "scanning":  False,
    "total":     0,
    "done":      0,
}
_zone_lock   = threading.Lock()
_scan_thread = None
_scan_started = False

# ── Cached ORB scan results (updated by background thread) ────────────────────
_orb_cache = {
    "orb5m":     [],   # 5m ORB stocks
    "orb15m":    [],   # 15m ORB stocks
    "scan_time": "--",
    "scanning":  False,
    "total":     0,
    "done":      0,
}
_orb_lock     = threading.Lock()
_orb_thread   = None
_orb_started  = False

# ── Cached Doji scan results (updated by background thread) ───────────────────
_doji_cache = {
    "doji5m":    [],   # 5m Doji stocks
    "doji15m":   [],   # 15m Doji stocks
    "scan_time": "--",
    "scanning":  False,
    "total":     0,
    "done":      0,
}
_doji_lock     = threading.Lock()
_doji_thread   = None
_doji_started  = False

_cpr_cache = {
    "stocks":    [],
    "scan_time": "--",
    "scanning":  False,
    "total":     0,
    "done":      0,
}
_cpr_lock     = threading.Lock()
_cpr_thread   = None
_cpr_started  = False

# ── Cached Reversal scan results (updated by background thread) ────────────────
_reversal_cache = {
    "rev5m":     {"bullish": [], "bearish": []},
    "rev15m":    {"bullish": [], "bearish": []},
    "scan_time": "--",
    "scanning":  False,
    "total":     0,
    "done":      0,
}
_reversal_lock     = threading.Lock()
_reversal_thread   = None
_reversal_started  = False


# ── Symbol conversion helpers ──────────────────────────────────────────────────
def fyers_to_short(fyers_sym):
    """'NSE:RELIANCE-EQ' -> 'RELIANCE'"""
    return fyers_sym.replace("NSE:", "").replace("-EQ", "").replace("-BE", "")


def fyers_to_yf(fyers_sym):
    """'NSE:RELIANCE-EQ' -> 'RELIANCE.NS'"""
    # 1. Remove Prefix/Suffix
    short = fyers_sym.replace("NSE:", "").replace("-EQ", "").replace("-BE", "")
    
    # 2. Specific Mappings
    if short == "M&M":
        return "M&M.NS"
    if short == "BAJAJ-AUTO":
        return "BAJAJ-AUTO.NS"
        
    return f"{short}.NS"


def get_sector_for_fyers_sym(fyers_sym):
    for sector, syms in SECTOR_STOCKS.items():
        if fyers_sym in syms:
            return sector
    return "Others"


# ── Pine Script-style zone detection ──────────────────────────────────────────
def detect_zones(df, lookback=LOOKBACK):
    """
    Given a DataFrame with Open/High/Low/Close/Volume columns,
    detect demand (BUY) and supply (SELL) institutional zones.

    Demand zone:
      - Origin candle = bearish (Close < Open)
      - Followed by `lookback` consecutive bullish candles
    Supply zone:
      - Origin candle = bullish (Close > Open)
      - Followed by `lookback` consecutive bearish candles

    Returns (demand_dict | None, supply_dict | None)
    """
    if df is None or len(df) < lookback + 10:
        return None, None

    C = df["Close"].values
    O = df["Open"].values
    H = df["High"].values
    L = df["Low"].values
    V = df["Volume"].values

    zb = lookback + 2        # offset: origin candle position from end
    n  = len(df) - 1         # index of latest candle

    if n < zb + 2:
        return None, None

    # Price shift from origin candle to most recent
    if C[n - zb] == 0:
        return None, None
    shift = abs(C[n - zb] - C[n - 2]) / C[n - zb] * 100.0

    zC, zO, zH, zL = C[n-zb], O[n-zb], H[n-zb], L[n-zb]
    avg_v = float(V[:-1].mean()) if len(V) > 1 else 1.0
    chg   = round((C[n] - C[n-1]) / C[n-1] * 100, 2) if C[n-1] != 0 else 0.0

    def pkg(ceiling, floor):
        return {
            "ceiling":   round(float(ceiling), 2),
            "floor":     round(float(floor), 2),
            "mid":       round(float((ceiling + floor) / 2), 2),
            "ltp":       round(float(C[n]), 2),
            "chg_pct":   chg,
            "move_pct":  round(float(shift), 2),
            "vol_ratio": round(float(V[n] / avg_v), 1) if avg_v > 0 else 0.0,
        }

    # ── Demand zone (bullish reversal from bearish origin) ──────────────────
    demand = None
    if zC < zO:   # origin candle is bearish
        bull_count = sum(
            1 for i in range(2, lookback + 2) if C[n - i] > O[n - i]
        )
        if bull_count == lookback:
            demand = pkg(ceiling=zO, floor=zL)   # zone: open → low of origin

    # ── Supply zone (bearish reversal from bullish origin) ──────────────────
    supply = None
    if zC > zO:   # origin candle is bullish
        bear_count = sum(
            1 for i in range(2, lookback + 2) if C[n - i] < O[n - i]
        )
        if bear_count == lookback:
            supply = pkg(ceiling=zH, floor=zO)   # zone: high → open of origin

    return demand, supply


# ── Fetch OHLCV from yfinance ──────────────────────────────────────────────────
def fetch_ohlcv_yf(fyers_sym, interval="5m", period="5d"):
    """Fetch OHLCV for a Fyers symbol using yfinance (NSE suffix) with fallbacks."""
    try:
        import yfinance as yf
        from curl_cffi import requests as c_requests
        
        # Use curl_cffi session to bypass Yahoo bot block
        session = c_requests.Session(impersonate="chrome110")
        
        yf_sym = fyers_to_yf(fyers_sym)
        ticker = yf.Ticker(yf_sym, session=session)
        
        # Try primary period
        df = ticker.history(period=period, interval=interval, auto_adjust=True)
        
        # Robustness: If empty, try a longer period (yfinance can be flaky with 1d/5d)
        if df is None or df.empty or len(df) < 1:
            fallback_periods = ["7d", "1mo"] if period in ["1d", "5d"] else []
            for fb_p in fallback_periods:
                df = ticker.history(period=fb_p, interval=interval, auto_adjust=True)
                if df is not None and not df.empty:
                    break
        
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


def fetch_ohlcv_fyers(fyers_sym, interval="D", period_days=5):
    """Fetch OHLCV for a Fyers symbol using Fyers History API."""
    global _fyers_instance
    if not _fyers_instance:
        return None
        
    try:
        from datetime import datetime, timedelta
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=period_days+5)).strftime("%Y-%m-%d")
        
        # Fyers intervals: "D", "1", "5", "10", "15", "30", "60"
        data = {
            "symbol": fyers_sym,
            "resolution": interval,
            "date_format": "1",
            "range_from": start_date,
            "range_to": end_date,
            "cont_flag": "1"
        }
        resp = _fyers_instance.history(data=data)
        if resp.get("s") != "ok":
            return None
            
        candles = resp.get("candles", [])
        if not candles:
            return None
            
        # [epoch, open, high, low, close, volume]
        df = pd.DataFrame(candles, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], unit="s")
        df.set_index("Timestamp", inplace=True)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
            
        return df
    except Exception:
        return None


def fetch_ohlcv_smart(fyers_sym, interval="5m", period_days=5):
    """Hybrid fetcher: uses Fyers History API if available, else falls back to yfinance."""
    if _fyers_instance:
        # Convert yf-style to fyers-style resolution
        # yf: "1m", "5m", "15m", "1d" -> Fyers: "1", "5", "15", "D"
        f_int = "D" if interval == "1d" else interval.replace("m", "")
        df = fetch_ohlcv_fyers(fyers_sym, interval=f_int, period_days=period_days)
        if df is not None and not df.empty:
            return df
            
    # Fallback to yfinance
    yf_int = interval
    yf_period = f"{period_days}d"
    return fetch_ohlcv_yf(fyers_sym, interval=yf_int, period=yf_period)




def fetch_today_1m_yf(fyers_sym):
    """Fetch today's 1m candles (hybrid)."""
    return fetch_ohlcv_smart(fyers_sym, interval="1m", period_days=1)


# ── All-stock zone scanner ─────────────────────────────────────────────────────
def _run_zone_scan():
    """Background function: scan all stocks for demand/supply zones (Parallel)."""
    all_symbols = [sym for syms in SECTOR_STOCKS.values() for sym in syms]
    unique_symbols = sorted(list(set(all_symbols)))
    total = len(unique_symbols)

    with _zone_lock:
        _zone_cache["scanning"] = True
        _zone_cache["total"]    = total
        _zone_cache["done"]     = 0

    demand_list = []
    supply_list = []
    
    def process_sym(sym, idx):
        try:
            # Spread requests to avoid burst rate limiting
            time.sleep(random.uniform(0.05, 0.2))
            df = fetch_ohlcv_smart(sym, interval=INTERVAL, period_days=5)
            if df is not None:
                d, s = detect_zones(df)
                sector    = get_sector_for_fyers_sym(sym)
                short_sym = fyers_to_short(sym)
                return (short_sym, sector, d, s)
        except Exception as e:
            print(f"[Zones] Error processing {sym}: {e}")
        return None

    # Run up to 20 fetches in parallel
    # Reduced workers to avoid concurrent yfinance blocks
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_sym, sym, i): sym for i, sym in enumerate(unique_symbols)}
        
        for i, future in enumerate(as_completed(futures)):
            res = future.result()
            if res:
                short_sym, sector, d, s = res
                if d: demand_list.append({"symbol": short_sym, "sector": sector, **d})
                if s: supply_list.append({"symbol": short_sym, "sector": sector, **s})
            
            with _zone_lock:
                _zone_cache["done"] = i + 1

    # Sort by move_pct descending (highest momentum first)
    demand_list.sort(key=lambda x: x["move_pct"], reverse=True)
    supply_list.sort(key=lambda x: x["move_pct"], reverse=True)

    from datetime import datetime
    ts = pd.Timestamp.now(tz="Asia/Kolkata").strftime("%Y-%m-%d %H:%M")

    with _zone_lock:
        _zone_cache["demand"]    = demand_list[:TOP_N]
        _zone_cache["supply"]    = supply_list[:TOP_N]
        _zone_cache["scan_time"] = ts
        _zone_cache["scanning"]  = False

    print(f"[Zones] Parallel scan done: {len(demand_list)} demand, {len(supply_list)} supply zones found")


def _zone_scanner_loop():
    """Runs forever in a daemon thread, scanning every SCAN_EVERY_S seconds."""
    while True:
        try:
            _run_zone_scan()
        except Exception as e:
            print(f"[Zones] Scanner error: {e}")
            with _zone_lock:
                _zone_cache["scanning"] = False
        time.sleep(SCAN_EVERY_S)


def start_zone_scanner():
    """Start the background zone scanner thread (call once at app startup)."""
    global _scan_thread, _scan_started
    if _scan_started:
        return
    _scan_started = True
    _scan_thread = threading.Thread(
        target=_zone_scanner_loop, daemon=True, name="zone-scanner"
    )
    _scan_thread.start()
    print("[Zones] Background zone scanner started")


def get_zone_scan_status():
    """Return current cached results + scanning status."""
    with _zone_lock:
        return dict(_zone_cache)


# ── ORB Scanner (Opening Range Breakout) ──────────────────────────────────────
# fetch_today_1m_yf is now a hybrid wrapper defined above


def calculate_orb(df):
    """
    Given today's 1m DataFrame, calculate the 5m and 15m ORB.
    Returns (orb5m_res, orb15m_res) where res is dict or None.
    """
    if df is None or len(df) < 5:
        return None, None

    
    # Ensure index is localized to IST
    if df.index.tz is None:
        df.index = df.index.tz_localize("Asia/Kolkata")
    elif str(df.index.tz) != "Asia/Kolkata":
        df.index = df.index.tz_convert("Asia/Kolkata")

    # Filter for today only
    today_str = pd.Timestamp.now(tz="Asia/Kolkata").strftime("%Y-%m-%d")
    try:
        df_today = df.loc[today_str]
    except KeyError:
        return None, None
    
    if len(df_today) < 5:
        return None, None

    ltp = round(float(df_today["Close"].iloc[-1]), 2)
    vol = int(df_today["Volume"].iloc[-1]) if "Volume" in df_today.columns else 0

    res5m = None
    res15m = None

    # ORB 5m: 09:15 to 09:19 (inclusive = 5 candles)
    df_5m = df_today.between_time("09:15", "09:19")
    if len(df_5m) >= 4:  # Allow slight missing data, but need a decent range
        high_5m = round(float(df_5m["High"].max()), 2)
        low_5m  = round(float(df_5m["Low"].min()), 2)
        if ltp > high_5m:
            res5m = {"signal": "Bullish ORB", "high": high_5m, "low": low_5m, 'ltp': ltp, 'diff': round(((ltp - high_5m)/high_5m)*100, 2), "vol": vol}
        elif ltp < low_5m:
            res5m = {"signal": "Bearish ORB", "high": high_5m, "low": low_5m, 'ltp': ltp, 'diff': round(((ltp - low_5m)/low_5m)*100, 2), "vol": vol}

    # ORB 15m: 09:15 to 09:29 (inclusive = 15 candles)
    df_15m = df_today.between_time("09:15", "09:29")
    if len(df_15m) >= 12: # Allow slight missing data
        high_15m = round(float(df_15m["High"].max()), 2)
        low_15m  = round(float(df_15m["Low"].min()), 2)
        if ltp > high_15m:
            res15m = {"signal": "Bullish ORB", "high": high_15m, "low": low_15m, 'ltp': ltp, 'diff': round(((ltp - high_15m)/high_15m)*100, 2), "vol": vol}
        elif ltp < low_15m:
            res15m = {"signal": "Bearish ORB", "high": high_15m, "low": low_15m, 'ltp': ltp, 'diff': round(((ltp - low_15m)/low_15m)*100, 2), "vol": vol}

    return res5m, res15m


def _run_orb_scan():
    """Background function: scan all stocks for 5m and 15m ORB (Parallel)."""
    all_symbols = [sym for syms in SECTOR_STOCKS.values() for sym in syms]
    unique_symbols = sorted(list(set(all_symbols)))
    total = len(unique_symbols)

    with _orb_lock:
        _orb_cache["scanning"] = True
        _orb_cache["total"]    = total
        _orb_cache["done"]     = 0

    orb5m_list = []
    orb15m_list = []

    def process_sym(sym):
        try:
            # Spread requests
            time.sleep(random.uniform(0.01, 0.05))
            df = fetch_today_1m_yf(sym)
            if df is not None:
                r5, r15 = calculate_orb(df)
                sector    = get_sector_for_fyers_sym(sym)
                short_sym = fyers_to_short(sym)
                return (short_sym, sector, r5, r15)
        except Exception as e:
            print(f"[ORB] Error processing {sym}: {e}")
        return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_sym, sym): sym for sym in unique_symbols}
        for i, future in enumerate(as_completed(futures)):
            res = future.result()
            if res:
                short_sym, sector, r5, r15 = res
                if r5: orb5m_list.append({"symbol": short_sym, "sector": sector, **r5})
                if r15: orb15m_list.append({"symbol": short_sym, "sector": sector, **r15})
            
            with _orb_lock:
                _orb_cache["done"] = i + 1

    # Sort by diff magnitude descending
    orb5m_list.sort(key=lambda x: abs(x["diff"]), reverse=True)
    orb15m_list.sort(key=lambda x: abs(x["diff"]), reverse=True)

    from datetime import datetime
    ts = pd.Timestamp.now(tz="Asia/Kolkata").strftime("%Y-%m-%d %H:%M")

    with _orb_lock:
        _orb_cache["orb5m"]     = orb5m_list
        _orb_cache["orb15m"]    = orb15m_list
        _orb_cache["scan_time"] = ts
        _orb_cache["scanning"]  = False

    print(f"[ORB] Parallel scan done: {len(orb5m_list)} 5m ORB, {len(orb15m_list)} 15m ORB found")


def _orb_scanner_loop():
    while True:
        try:
            _run_orb_scan()
        except Exception as e:
            print(f"[ORB] Scanner error: {e}")
            with _orb_lock:
                _orb_cache["scanning"] = False
        time.sleep(30)  # Re-scan ORB every 30 seconds


def start_orb_scanner():
    global _orb_thread, _orb_started
    if _orb_started:
        return
    _orb_started = True
    _orb_thread = threading.Thread(
        target=_orb_scanner_loop, daemon=True, name="orb-scanner"
    )
    _orb_thread.start()
    print("[ORB] Background ORB scanner started")


def get_orb_scan_status():
    with _orb_lock:
        return dict(_orb_cache)


# ── Doji Scanner ──────────────────────────────────────────────────────────────
def calculate_doji(df):
    """
    Detect Doji candles in the first 5m and 15m of market open.
    Doji definition: abs(Open - Close) <= 0.1 * (High - Low)
    Returns (doji5m_res, doji15m_res)
    """
    if df is None or len(df) < 1:
        return None, None

    # Localize/Convert to IST
    if df.index.tz is None:
        df.index = df.index.tz_localize("Asia/Kolkata")
    else:
        df.index = df.index.tz_convert("Asia/Kolkata")

    today_str = pd.Timestamp.now(tz="Asia/Kolkata").strftime("%Y-%m-%d")
    try:
        df_today = df.loc[today_str]
    except KeyError:
        return None, None
    
    if df_today.empty:
        return None, None

    ltp = round(float(df_today["Close"].iloc[-1]), 2)
    vol = int(df_today["Volume"].sum()) # Total volume for the day so far

    def is_doji(o, h, l, c):
        body = abs(o - c)
        range_ = h - l
        if range_ <= 0: return False
        return body <= 0.1 * range_

    res5m = None
    res15m = None

    try:
        # Resample to 5m and 15m to get actual 5m/15m candles
        # Note: resample label='left', closed='left' is standard for 09:15 candle
        df_5m_resampled = df_today.resample('5min', label='left', closed='left').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        })
        df_15m_resampled = df_today.resample('15min', label='left', closed='left').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        })
        
        # Check first 5m candle (09:15)
        if not df_5m_resampled.empty:
            c5 = df_5m_resampled.iloc[0]
            if is_doji(c5['Open'], c5['High'], c5['Low'], c5['Close']):
                 res5m = {"signal": "Doji 5m", "ltp": ltp, "open": round(float(c5['Open']),2), "high": round(float(c5['High']),2), "low": round(float(c5['Low']),2), "close": round(float(c5['Close']),2), "vol": int(c5['Volume'])}

        # Check first 15m candle (09:15)
        if not df_15m_resampled.empty:
            c15 = df_15m_resampled.iloc[0]
            if is_doji(c15['Open'], c15['High'], c15['Low'], c15['Close']):
                 res15m = {"signal": "Doji 15m", "ltp": ltp, "open": round(float(c15['Open']),2), "high": round(float(c15['High']),2), "low": round(float(c15['Low']),2), "close": round(float(c15['Close']),2), "vol": int(c15['Volume'])}
    except Exception:
        pass

    return res5m, res15m


def _run_doji_scan():
    """Background function: scan all stocks for 5m and 15m Doji (Parallel)."""
    all_symbols = [sym for syms in SECTOR_STOCKS.values() for sym in syms]
    unique_symbols = sorted(list(set(all_symbols)))
    total = len(unique_symbols)

    with _doji_lock:
        _doji_cache["scanning"] = True
        _doji_cache["total"]    = total
        _doji_cache["done"]     = 0

    doji5m_list = []
    doji15m_list = []

    def process_sym(sym):
        try:
            # Spread requests
            time.sleep(random.uniform(0.01, 0.05))
            df = fetch_today_1m_yf(sym)
            if df is not None:
                r5, r15 = calculate_doji(df)
                sector    = get_sector_for_fyers_sym(sym)
                short_sym = fyers_to_short(sym)
                return (short_sym, sector, r5, r15)
        except Exception as e:
            print(f"[Doji] Error processing {sym}: {e}")
        return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_sym, sym): sym for sym in unique_symbols}
        for i, future in enumerate(as_completed(futures)):
            res = future.result()
            if res:
                short_sym, sector, r5, r15 = res
                if r5: doji5m_list.append({"symbol": short_sym, "sector": sector, **r5})
                if r15: doji15m_list.append({"symbol": short_sym, "sector": sector, **r15})

            with _doji_lock:
                _doji_cache["done"] = i + 1

    from datetime import datetime
    ts = pd.Timestamp.now(tz="Asia/Kolkata").strftime("%Y-%m-%d %H:%M")

    with _doji_lock:
        _doji_cache["doji5m"]    = doji5m_list
        _doji_cache["doji15m"]   = doji15m_list
        _doji_cache["scan_time"] = ts
        _doji_cache["scanning"]  = False

    print(f"[Doji] Parallel scan done: {len(doji5m_list)} 5m Doji, {len(doji15m_list)} 15m Doji found")


def _doji_scanner_loop():
    while True:
        try:
            _run_doji_scan()
        except Exception as e:
            print(f"[Doji] Scanner error: {e}")
            with _doji_lock:
                _doji_cache["scanning"] = False
        time.sleep(60) # Re-scan Doji every 60 seconds


def start_doji_scanner():
    global _doji_thread, _doji_started
    if _doji_started: return
    _doji_started = True
    _doji_thread = threading.Thread(target=_doji_scanner_loop, daemon=True, name="doji-scanner")
    _doji_thread.start()
    print("[Doji] Background Doji scanner started")


def get_doji_scan_status():
    with _doji_lock:
        return dict(_doji_cache)


# ── CPR Scanner ───────────────────────────────────────────────────────────────
def calculate_cpr(df):
    """
    Calculate Central Pivot Range (CPR) using previous day's OHLC.
    TC = (Pivot - BC) + Pivot
    Pivot = (H + L + C) / 3
    BC = (H + L) / 2
    """
    if df is None or len(df) < 2:
        return None

    try:
        # Get the most recent completed day
        row = df.iloc[-2] # Previous day
        h, l, c = float(row['High']), float(row['Low']), float(row['Close'])
        o = float(row['Open'])
        
        pivot = (h + l + c) / 3.0
        bc = (h + l) / 2.0
        tc = (pivot * 2) - bc
        
        # Ensure tc is always the higher one for width calc
        tc_final = max(tc, bc)
        bc_final = min(tc, bc)
        
        width = abs(tc_final - bc_final)
        width_pct = (width / pivot) * 100 if pivot > 0 else 0
        
        return {
            "p": round(pivot, 2),
            "bc": round(bc_final, 2),
            "tc": round(tc_final, 2),
            "width_pct": round(width_pct, 4),
            "prev_h": round(h, 2),
            "prev_l": round(l, 2),
            "prev_c": round(c, 2),
            "prev_o": round(o, 2)
        }
    except Exception:
        return None


def _run_cpr_scan():
    """Background function: scan all stocks for CPR breakout signals (Parallel)."""
    all_symbols = [sym for syms in SECTOR_STOCKS.values() for sym in syms]
    unique_symbols = sorted(list(set(all_symbols)))
    total = len(unique_symbols)

    with _cpr_lock:
        _cpr_cache["scanning"] = True
        _cpr_cache["total"]    = total
        _cpr_cache["done"]     = 0

    cpr_list = []

    def process_sym(sym):
        try:
            # Spread requests
            time.sleep(random.uniform(0.05, 0.2))
            # Fetch daily data for the last few days
            df = fetch_ohlcv_smart(sym, interval="1d", period_days=5)
            if df is not None and len(df) >= 2:
                cpr = calculate_cpr(df)
                if cpr:
                    sector    = get_sector_for_fyers_sym(sym)
                    short_sym = fyers_to_short(sym)
                    ltp       = round(float(df["Close"].iloc[-1]), 2) # Today's LTP
                    today_o   = round(float(df["Open"].iloc[-1]), 2)
                    
                    # Rally Probability Logic (Score out of 5.0)
                    score = 0.0
                    signal = "Neutral"
                    
                    # 1. Narrowness
                    if cpr["width_pct"] < 0.15: score += 1.5
                    elif cpr["width_pct"] < 0.35: score += 1.0
                    elif cpr["width_pct"] < 0.6: score += 0.5
                    
                    # 2. Bullish Bias (LTP > TC)
                    if ltp > cpr["tc"]:
                        score += 1.0
                        signal = "Bullish"
                        
                    # 3. Pivot Support (LTP > Pivot)
                    if ltp > cpr["p"]:
                        score += 0.5
                    
                    # 4. Breakout (LTP > Prev Day High)
                    if ltp > cpr["prev_h"]:
                        score += 1.5
                        signal = "Bullish Breakout"
                        
                    # 5. Intraday Momentum (LTP > Open)
                    if ltp > today_o:
                        score += 0.5

                    probability = "Low"
                    if score >= 4.0:
                        probability = "Very High"
                        if signal == "Bullish Breakout": signal = "🚀 High Prob Rally"
                    elif score >= 2.5:
                        probability = "High"
                        if signal == "Bullish": signal = "High Prob Rally"
                    elif score >= 1.5:
                        probability = "Medium"

                    return {
                        "symbol": short_sym,
                        "sector": sector,
                        "ltp": ltp,
                        "open": today_o,
                        "score": score,
                        "prob": probability,
                        "signal": signal,
                        **cpr
                    }
        except Exception as e:
            print(f"[CPR] Error processing {sym}: {e}")
        return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_sym, sym): sym for sym in unique_symbols}
        for i, future in enumerate(as_completed(futures)):
            res = future.result()
            if res:
                cpr_list.append(res)
            
            with _cpr_lock:
                _cpr_cache["done"] = i + 1

    # Sort primarily by score descending (high prob first), 
    # secondarily by width_pct ascending (narrowest first)
    cpr_list.sort(key=lambda x: (-x["score"], x["width_pct"]))

    from datetime import datetime
    ts = pd.Timestamp.now(tz="Asia/Kolkata").strftime("%Y-%m-%d %H:%M")

    with _cpr_lock:
        _cpr_cache["stocks"]    = cpr_list
        _cpr_cache["scan_time"] = ts
        _cpr_cache["scanning"]  = False

    print(f"[CPR] Scan done: {len(cpr_list)} stocks processed. Top Rally: {cpr_list[0]['symbol'] if cpr_list and cpr_list[0]['score'] >= 2.5 else 'None'}")



def rescan_cpr():
    """Trigger a one-time CPR scan in a background thread."""
    with _cpr_lock:
        if _cpr_cache.get("scanning"):
            return False # already scanning
    t = threading.Thread(target=_run_cpr_scan, daemon=True, name="cpr-rescan")
    t.start()
    return True


def start_cpr_scanner():
    global _cpr_started
    if _cpr_started: return
    _cpr_started = True
    # Initial scan on startup
    rescan_cpr()
    print("[CPR] Initial CPR scan triggered on startup")


def get_cpr_scan_status():
    with _cpr_lock:
        return dict(_cpr_cache)


# ── R-Factor ──────────────────────────────────────────────────────────────────
def calculate_r_factor(symbol, quote_data):
    """
    R-Factor — 4-Component Momentum Score (NSE F&O stocks only)
    ────────────────────────────────────────────────────────────
    Combines four market signals to produce a single momentum score:

      Component 1 — Intraday Momentum (weight 45%)
        = (LTP - Open) / Open × 100
        Measures how much the stock has moved from today's open.
        Positive = buying pressure; negative = selling pressure.

      Component 2 — Gap Direction (weight 25%)
        = (Open - PrevClose) / PrevClose × 100
        Captures pre-market sentiment. A gap-up with follow-through
        adds to bullish conviction; gap-down signals weakness.

      Component 3 — Range Score (weight 20%)
        = ((LTP - Low) / (High - Low)) × 2 - 1   →  range [-1, +1]
        Scaled by today's range as % of prev close.
        +1 means price at the top of today's range (strong), -1 means bottom (weak).

      Component 4 — Volume Confirmation (weight 10%)
        = sign(Intraday%) × log1p(vol / avg_vol)
        Above-average volume on an up move adds confidence;
        high volume on a down move deepens the negative score.

    Final:
        R = 0.45 × Intraday% + 0.25 × Gap% + 0.20 × RangeScore×range_factor + 0.10 × VolScore

    Interpretation:
        High positive R  →  HIGH MOMENTUM  (Buy side candidate)
        High negative R  →  LOW MOMENTUM   (Sell / Weak side candidate)
    """
    try:
        ltp    = float(quote_data.get('lp',  quote_data.get('last_price', 0)))
        open_  = float(quote_data.get('open_price', ltp))
        high   = float(quote_data.get('high_price', ltp))
        low    = float(quote_data.get('low_price',  ltp))
        prev   = float(quote_data.get('prev_close_price', quote_data.get('close_price', ltp)))
        vol    = float(quote_data.get('volume', 1))
        avg_vol = float(quote_data.get('avg_trade_val', vol))

        if open_ == 0 or prev == 0:
            return 0.0

        # Component 1: Intraday Momentum (Modified: Peak at 1.2%, Decay after 2.0%)
        intraday_pct = ((ltp - open_) / open_) * 100.0 if open_ > 0 else 0
        
        # Predictive Logic: If already moved too much, "Reward" part of R-Factor is lower
        momentum_multiplier = 1.0
        if intraday_pct > 2.0:
            momentum_multiplier = 0.4  # Penalize "already done" momentum
        elif intraday_pct > 1.2:
            momentum_multiplier = 0.8  # Starting to top out
            
        # Component 2: Gap Direction
        gap_pct = ((open_ - prev) / prev) * 100.0 if prev > 0 else 0
        
        # Component 3: Breakout Proximity (Predictive Bonus)
        # Close to Day High = High conviction for breakout
        dist_from_high = (high - ltp) / ltp * 100.0 if ltp > 0 else 100
        breakout_bonus = 0.0
        if dist_from_high <= 0.15: # Within 0.15% of high
            breakout_bonus = 2.5   # Strong boost for potential immediate breakout
            
        # Component 4: Volatility Compression (Coiling)
        # Narrow range = potential explosive move
        range_pct = ((high - low) / prev) * 100.0 if prev > 0 else 100
        coiling_bonus = 0.0
        if range_pct < 0.6: # Very tight range
            coiling_bonus = 1.5
        # Component 5: Volume Confirmation
        vol_ratio   = vol / avg_vol if avg_vol > 0 else 1.0
        vol_score   = float(np.log1p(vol_ratio))
        
        # Final Weighted R-Factor (Predictive Version)
        r_factor = (
            (0.35 * intraday_pct * momentum_multiplier) # Reward early moves, penalize late ones
            + (0.15 * gap_pct)
            + (0.30 * breakout_bonus) # Heavily reward proximity to high
            + (0.15 * coiling_bonus)  # Reward low-volatility coiling
            + (0.05 * vol_score)
        )
        return round(float(r_factor), 2)
    except Exception:
        return 0.0


# ── Sector Performance ─────────────────────────────────────────────────────────
def get_sector_performance(quotes_by_symbol):
    """
    Rich sector performance aggregation:
    - Simple equal-weighted average % change per sector
    - Advance / Decline / Unchanged counts
    - Top 3 gainers and top 3 losers within each sector
    - Sorted sector ranking (top gainer sector first)

    Returns list of dicts sorted by performance descending:
    [
        {
            "sector":      "Banking",
            "performance": 0.54,        # avg intraday % change
            "advances":    8,           # stocks > 0
            "declines":    4,           # stocks < 0
            "unchanged":   2,           # stocks == 0
            "total":       14,          # total stocks with data
            "top_gainers": [{"symbol":"HDFCBANK","pct":1.2,"ltp":1750.0}, ...],
            "top_losers":  [{"symbol":"SBIN","pct":-0.5,"ltp":820.0}, ...],
        },
        ...
    ]
    """
    results = []

    for sector, symbols in SECTOR_STOCKS.items():
        stock_perfs = []   # list of (short_sym, pct_change, ltp)

        for sym in symbols:
            q = quotes_by_symbol.get(sym)
            if not q:
                continue
            try:
                ltp  = float(q.get('lp', q.get('last_price', 0)))
                prev = float(q.get('prev_close_price', q.get('close_price', ltp)))
                if prev <= 0 or ltp <= 0:
                    continue
                pct = round(((ltp - prev) / prev) * 100, 2)
                short = fyers_to_short(sym)
                stock_perfs.append((short, pct, round(ltp, 2)))
            except Exception:
                continue

        if not stock_perfs:
            continue

        pcts      = [p for _, p, _ in stock_perfs]
        avg_pct   = round(float(np.mean(pcts)), 3)
        advances  = sum(1 for p in pcts if p > 0)
        declines  = sum(1 for p in pcts if p < 0)
        unchanged = sum(1 for p in pcts if p == 0)

        sorted_stocks = sorted(stock_perfs, key=lambda x: x[1], reverse=True)
        top_gainers   = [{"symbol": s, "pct": p, "ltp": l} for s, p, l in sorted_stocks[:3]]
        top_losers    = [{"symbol": s, "pct": p, "ltp": l} for s, p, l in sorted_stocks[-3:][::-1]]

        results.append({
            "sector":      sector,
            "performance": avg_pct,
            "advances":    advances,
            "declines":    declines,
            "unchanged":   unchanged,
            "total":       len(stock_perfs),
            "top_gainers": top_gainers,
            "top_losers":  top_losers,
        })

    results.sort(key=lambda x: x["performance"], reverse=True)
    return results


# ── R-Factor top stocks ────────────────────────────────────────────────────────
def get_r_factor_stocks(quotes_by_symbol, top_n=10):
    """
    Calculate R-Factor for all F&O stocks and return:
      - Top N HIGH MOMENTUM stocks  (buy side  — highest positive R-Factor)
      - Top N LOW  MOMENTUM stocks  (sell side — lowest/most-negative R-Factor)

    Only processes symbols that are in SECTOR_STOCKS (NSE F&O eligible).
    Returns: {'buy': [...], 'sell': [...]}
    """
    scores = []

    # Build a set of all F&O symbols for fast lookup
    fo_symbols = {sym for syms in SECTOR_STOCKS.values() for sym in syms}

    for sym, q in quotes_by_symbol.items():
        # Skip non-F&O symbols
        if sym not in fo_symbols:
            continue

        r = calculate_r_factor(sym, q)
        try:
            ltp   = float(q.get('lp', q.get('last_price', 0)))
            open_ = float(q.get('open_price', ltp))
            high  = float(q.get('high_price', ltp))
            low   = float(q.get('low_price',  ltp))
            prev  = float(q.get('prev_close_price', q.get('close_price', ltp)))
            pct   = round(((ltp - prev) / prev) * 100, 2) if prev > 0 else 0.0
        except Exception:
            ltp = open_ = high = low = prev = 0.0
            pct = 0.0

        short_sym = fyers_to_short(sym)
        sector    = get_sector_for_fyers_sym(sym)

        # Identify Momentum Status
        status = 'Neutral'
        if ltp > 0:
            intraday_pct = ((ltp - open_) / open_) * 100.0 if open_ > 0 else 0
            dist_from_high = (high - ltp) / ltp * 100.0
            range_pct = ((high - low) / prev) * 100.0 if prev > 0 else 100

            if intraday_pct > 2.5:
                status = 'Done' # Momentum already completed
            elif dist_from_high < 0.15 and intraday_pct > 0.4:
                status = 'Breakout' # At high, ready to go
            elif range_pct < 0.6 and abs(intraday_pct) < 0.8:
                status = 'Coiling' # Tight range, potential breakout candidate
            elif r > 1.5:
                status = 'Strong'
        
        scores.append({
            'symbol':         short_sym,
            'sector':         sector,
            'r_factor':       r,
            'ltp':            round(ltp, 2),
            'open':           round(open_, 2),
            'high':           round(high, 2),
            'low':            round(low, 2),
            'pct_change':     pct,
            'status':         status
        })

    # Sort descending → best predictive momentum at top
    sorted_scores = sorted(scores, key=lambda x: x['r_factor'], reverse=True)

    # Label and slice
    buy_side  = sorted_scores[:top_n]
    sell_side = sorted_scores[-top_n:][::-1]   # reverse so weakest is first

    for s in buy_side:
        s['momentum_label'] = 'High Momentum'
    for s in sell_side:
        s['momentum_label'] = 'Low Momentum'

    return {
        'buy':  buy_side,
        'sell': sell_side,
    }


# ── Sector Stocks Detail ───────────────────────────────────────────────────────
def get_sector_stocks_data(quotes_by_symbol, sector_name):
    """Return detailed quote data for all stocks in a given sector."""
    from sector_data import SECTOR_STOCKS
    symbols = SECTOR_STOCKS.get(sector_name, [])
    results = []

    for sym in symbols:
        q = quotes_by_symbol.get(sym)
        short_sym = fyers_to_short(sym)
        if not q:
            results.append({
                'symbol': short_sym, 'ltp': 'N/A', 'open': 'N/A',
                'high': 'N/A', 'low': 'N/A', 'prev_close': 'N/A',
                'pct_change': 'N/A', 'volume': 'N/A', 'r_factor': 'N/A'
            })
            continue
        try:
            ltp   = float(q.get('lp', q.get('last_price', 0)))
            prev  = float(q.get('prev_close_price', q.get('close_price', ltp)))
            pct   = round(((ltp - prev) / prev) * 100, 2) if prev > 0 else 0.0
            rf    = calculate_r_factor(sym, q)
            vol   = int(q.get('volume', 0))

            results.append({
                'symbol':     short_sym,
                'ltp':        round(ltp, 2),
                'open':       round(float(q.get('open_price', ltp)), 2),
                'high':       round(float(q.get('high_price', ltp)), 2),
                'low':        round(float(q.get('low_price', ltp)), 2),
                'prev_close': round(prev, 2),
                'pct_change': pct,
                'volume':     f"{vol:,}",
                'r_factor':   rf
            })
        except Exception:
            results.append({'symbol': short_sym, 'ltp': 'ERR', 'pct_change': 0})

    results.sort(
        key=lambda x: float(x['pct_change']) if x['pct_change'] != 'N/A' else 0,
        reverse=True
    )
    return results


# ── Reversal Detection Scanner (Non-Repainting ZigZag Logic) ───────────────────
def calculate_reversal(df):
    """
    Python implementation of 'Reversal Detection Pro v3.0' Pine Script.
    Uses ZigZag logic for reversal points and EMA triple system for trend.
    """
    if df is None or len(df) < 50:
        return None

    try:
        # 1. Technical Indicators
        # ATR(5)
        df = df.copy()
        high, low, close = df["High"], df["Low"], df["Close"]
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs()
        ], axis=1).max(axis=1)
        df["ATR"] = tr.rolling(window=5).mean()
        
        # EMA(9, 14, 21)
        df["EMA9"]  = close.ewm(span=9,  adjust=False).mean()
        df["EMA14"] = close.ewm(span=14, adjust=False).mean()
        df["EMA21"] = close.ewm(span=21, adjust=False).mean()
        
        # 2. Reversal Amount (Threshold)
        # Medium sensitivity: 1.0% pct or 2.0*ATR
        df["RevAmount"] = df.apply(lambda r: max(r["Close"] * 0.01 / 100, max(0.05, 2.0 * r["ATR"])), axis=1)

        # 3. ZigZag simulation for the last 50 bars (Efficient)
        # We simulate the stateful nature of ZigZag
        zhigh, zlow = None, None
        direction = 0 # 1=Up, -1=Down
        pivots = [] # list of (index, type, price)

        # Initialize
        start_idx = max(0, len(df) - 50)
        df_scan = df.iloc[start_idx:]
        
        zhigh = df_scan.iloc[0]["High"]
        zlow = df_scan.iloc[0]["Low"]
        direction = 1

        for i in range(len(df_scan)):
            row = df_scan.iloc[i]
            curr_high, curr_low = row["High"], row["Low"]
            rev_amt = row["RevAmount"]
            
            if direction == 1:
                if curr_high > zhigh:
                    zhigh = curr_high
                if zhigh - curr_low >= rev_amt:
                    pivots.append({"index": i + start_idx, "type": "High", "price": zhigh})
                    direction = -1
                    zlow = curr_low
            else:
                if curr_low < zlow:
                    zlow = curr_low
                if curr_high - zlow >= rev_amt:
                    pivots.append({"index": i + start_idx, "type": "Low", "price": zlow})
                    direction = 1
                    zhigh = curr_high

        # 4. Final Signal Check
        latest_pivot = pivots[-1] if pivots else None
        curr_row = df.iloc[-1]
        
        # Trend Detection
        # Trend Detection (Relaxed for better responsiveness in reversals)
        if curr_row["EMA9"] > curr_row["EMA21"]:
            trend = "Bullish"
        elif curr_row["EMA9"] < curr_row["EMA21"]:
            trend = "Bearish"
        else:
            trend = "Neutral"
        
        # print(f"[Reversal] {curr_row.name} Trend: {trend} (EMA9: {curr_row['EMA9']:.2f}, EMA21: {curr_row['EMA21']:.2f})")

        # Signal Logic: If latest pivot matches current movement
        signal = "Neutral"
        if latest_pivot:
            # If latest pivot was a Low, and we are currently moving up (direction=1)
            # Or if latest pivot was a High, and we are currently moving down (direction=-1)
            if direction == 1 and latest_pivot["type"] == "Low":
                # Check if this "Low" is recent enough (within last 10 bars) to call it a fresh signal
                if (len(df) - 1 - latest_pivot["index"]) <= 10:
                    signal = "Bullish Reversal"
            elif direction == -1 and latest_pivot["type"] == "High":
                if (len(df) - 1 - latest_pivot["index"]) <= 10:
                    signal = "Bearish Reversal"

        if signal == "Neutral":
            return None

        # Format result
        return {
            "signal": signal,
            "trend":  trend,
            "ltp":    round(float(curr_row["Close"]), 2),
            "pivot":  round(float(latest_pivot["price"]), 2),
            "dist":   round(abs(curr_row["Close"] - latest_pivot["price"]) / latest_pivot["price"] * 100, 2),
            "ema9":   round(float(curr_row["EMA9"]), 2),
            "pivot_age": int(len(df) - 1 - latest_pivot["index"])
        }
    except Exception as e:
        return None


def _run_reversal_scan():
    """Background function: scan all stocks for reversals on 5m and 15m (Parallel)."""
    global _reversal_cache
    all_symbols = [sym for syms in SECTOR_STOCKS.values() for sym in syms]
    unique_symbols = sorted(list(set(all_symbols)))
    
    with _reversal_lock:
        _reversal_cache["scanning"] = True
        _reversal_cache["total"]    = len(unique_symbols)
        _reversal_cache["done"]     = 0

    results_5m = {"bullish": [], "bearish": []}
    results_15m = {"bullish": [], "bearish": []}
    
    def process_sym(sym):
        try:
            # Spread requests to avoid rate limits
            time.sleep(random.uniform(0.02, 0.1))
            
            # Fetch 5m data once
            df_5m = fetch_ohlcv_smart(sym, interval="5m", period_days=5)
            if df_5m is None or df_5m.empty:
                return None
            
            res_pkg = {}
            
            # Calculate for 5m
            res_5m = calculate_reversal(df_5m)
            if res_5m:
                res_pkg["5m"] = res_5m
            
            # Locally resample to 15m (saves 1 network request per stock)
            try:
                df_15m = df_5m.resample('15min').agg({
                    'Open': 'first',
                    'High': 'max',
                    'Low': 'min',
                    'Close': 'last',
                    'Volume': 'sum'
                }).dropna()
                
                if len(df_15m) > 20:
                    res_15m = calculate_reversal(df_15m)
                    if res_15m:
                        res_pkg["15m"] = res_15m
            except Exception:
                pass
            
            if res_pkg:
                sector    = get_sector_for_fyers_sym(sym)
                short_sym = fyers_to_short(sym)
                return (short_sym, sector, res_pkg)
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = {executor.submit(process_sym, sym): sym for sym in unique_symbols}
        for i, future in enumerate(as_completed(futures)):
            res = future.result()
            if res:
                short_sym, sector, data = res
                for tf, rdata in data.items():
                    pkg = {"symbol": short_sym, "sector": sector, **rdata}
                    target = results_5m if tf == "5m" else results_15m
                    if rdata["signal"] == "Bullish Reversal":
                        target["bullish"].append(pkg)
                    else:
                        target["bearish"].append(pkg)
            
            with _reversal_lock:
                _reversal_cache["done"] = i + 1

    # Sort and slice
    for results in [results_5m, results_15m]:
        # Primary sort: Recent Detection First (pivot_age asc)
        # Secondary sort: Closest to Pivot (dist asc)
        results["bullish"].sort(key=lambda x: (x["pivot_age"], x["dist"]))
        results["bearish"].sort(key=lambda x: (x["pivot_age"], x["dist"]))
        results["bullish"] = results["bullish"][:20]
        results["bearish"] = results["bearish"][:20]

    from datetime import datetime
    ts = pd.Timestamp.now(tz="Asia/Kolkata").strftime("%Y-%m-%d %H:%M")

    with _reversal_lock:
        _reversal_cache["rev5m"]     = results_5m
        _reversal_cache["rev15m"]    = results_15m
        _reversal_cache["scan_time"] = ts
        _reversal_cache["scanning"]  = False

    print(f"[Reversal] 5m: {len(results_5m['bullish'])}B/{len(results_5m['bearish'])}S, 15m: {len(results_15m['bullish'])}B/{len(results_15m['bearish'])}S found.")


def _reversal_scanner_loop():
    while True:
        try:
            _run_reversal_scan()
        except Exception as e:
            print(f"[Reversal] Scanner error: {e}")
            with _reversal_lock:
                _reversal_cache["scanning"] = False
        time.sleep(180) # Re-scan every 3 minutes


def start_reversal_scanner():
    global _reversal_thread, _reversal_started
    if _reversal_started: return
    _reversal_started = True
    _reversal_thread = threading.Thread(target=_reversal_scanner_loop, daemon=True, name="reversal-scanner")
    _reversal_thread.start()
    print("[Reversal] Background Reversal scanner started")


def get_reversal_scan_status():
    with _reversal_lock:
        return dict(_reversal_cache)
