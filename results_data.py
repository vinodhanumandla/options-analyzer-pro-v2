import os
import json
import threading
import time
from datetime import datetime
import yfinance as yf
from sector_data import ALL_SYMBOLS

# Cache path for the earnings data
CACHE_PATH = os.path.join(os.path.dirname(__file__), "results_cache.json")

# Helper to convert NSE:XXX-EQ to XXX.NS
def _to_yf_sym(s):
    short = s.replace("NSE:", "").replace("-EQ", "").replace("-BE", "").replace("-INDEX", "")
    if short == "M&M": return "M&M.NS"
    if short == "BAJAJ-AUTO": return "BAJAJ-AUTO.NS"
    return f"{short}.NS" 

# Initial static results data (History)
INITIAL_STOCKS = [
    { "sym": "BANKBARODA", "name": "Bank of Baroda", "sec": "Banking", "date": "2026-02-25", "rev": None, "pat": None, "ry": None, "py": None, "beat": "not_released", "sent": "not_released", "r": "Results expected today, Feb 25 — awaiting official filing" },
    { "sym": "BANKINDIA", "name": "Bank of India", "sec": "Banking", "date": "2026-02-24", "rev": 19052, "pat": None, "ry": 4.0, "py": 7.47, "beat": "inline", "sent": "neutral", "r": "Interest income up 4% YoY; modest profit growth from non-interest income" },
    { "sym": "SCHAEFFLER", "name": "Schaeffler India", "sec": "Auto Ancil.", "date": "2026-02-24", "rev": None, "pat": None, "ry": None, "py": None, "beat": "not_released", "sent": "not_released", "r": "Declared Feb 24 — full financials not yet confirmed in sources" },
    { "sym": "TITAN", "name": "Titan Company", "sec": "Consumer", "date": "2026-02-10", "rev": 25416, "pat": 1684, "ry": 43.3, "py": 60.8, "beat": "beat", "sent": "positive", "r": "Jewellery demand surged in festive Q3; record quarterly profit" },
    { "sym": "EICHERMOT", "name": "Eicher Motors", "sec": "Auto", "date": "2026-02-10", "rev": 6114, "pat": 1421, "ry": 22.9, "py": 21.4, "beat": "beat", "sent": "positive", "r": "Royal Enfield volumes hit all-time high; margin expansion strong" },
    { "sym": "GRASIM", "name": "Grasim Industries", "sec": "Diversified", "date": "2026-02-10", "rev": 44312, "pat": 1037, "ry": 25.3, "py": 26.4, "beat": "beat", "sent": "positive", "r": "Paints ramp-up + cement volumes drove broad-based growth" },
    { "sym": "APOLLOHOSP", "name": "Apollo Hospitals", "sec": "Healthcare", "date": "2026-02-10", "rev": 6477, "pat": 502, "ry": 17.2, "py": 34.9, "beat": "beat", "sent": "positive", "r": "Hospital occupancy at all-time high; pharmacy division outperformed" },
    { "sym": "ITC", "name": "ITC Limited", "sec": "FMCG", "date": "2026-01-29", "rev": 22081, "pat": 5018, "ry": 1.8, "py": -3.2, "beat": "miss", "sent": "negative", "r": "Cigarettes volume flattish; other FMCG divisions not enough to offset" },
    { "sym": "BAJFINANCE", "name": "Bajaj Finance", "sec": "NBFC", "date": "2026-02-03", "rev": 18950, "pat": 4308, "ry": 26.4, "py": 18.1, "beat": "beat", "sent": "positive", "r": "AUM +26% YoY; credit costs stable; NIM healthy" },
    { "sym": "SBI", "name": "State Bank of India", "sec": "Banking", "date": "2026-02-07", "rev": None, "pat": 19660, "ry": None, "py": 14.8, "beat": "beat", "sent": "positive", "r": "NII growth steady; slippage ratio contained; retail loans growing" },
    { "sym": "IRFC", "name": "IRFC", "sec": "NBFC / Infra", "date": "2026-03-10", "rev": 6842, "pat": 1620, "ry": 16.2, "py": 15.8, "beat": "beat", "sent": "positive", "r": "Disbursements to Indian Railways strong; NIM stable; asset quality pristine — beat PAT estimates by ~3%" },
    { "sym": "HFCL", "name": "HFCL Ltd", "sec": "Telecom Infra", "date": "2026-03-10", "rev": 1812, "pat": 148, "ry": 24.1, "py": 23.6, "beat": "beat", "sent": "positive", "r": "BharatNet optical fibre orders driving revenue; defence segment scaling; margins improving QoQ" },
]

# Initial upcoming results (Pending)
INITIAL_UPCOMING = [
    { "sym": "LT", "name": "Larsen & Toubro", "sec": "Capital Goods", "exp": "2026-02-27", "est_pat": 3550, "est_rev": 67000, "ep_yoy": 11.5, "note": "Order inflows and execution pace will be key; strong infra pipeline" },
    { "sym": "DMART", "name": "Avenue Supermarts", "sec": "Retail", "exp": "2026-03-01", "est_pat": 740, "est_rev": 15800, "ep_yoy": 6.8, "note": "Same-store-sales and footfall recovery; margin outlook key concern" },
    { "sym": "ZOMATO", "name": "Zomato Ltd", "sec": "Tech / QSR", "exp": "2026-03-03", "est_pat": 195, "est_rev": 5800, "ep_yoy": 42.0, "note": "Order frequency growth, Blinkit unit economics turning positive" },
    { "sym": "PAYTM", "name": "One97 Communications", "sec": "FinTech", "exp": "2026-03-05", "est_pat": -180, "est_rev": 1900, "ep_yoy": 18.0, "note": "Path to profitability watched closely; GMV and merchant loan growth" },
    { "sym": "IREDA", "name": "IREDA", "sec": "Renewable Fin.", "exp": "2026-03-15", "est_pat": 490, "est_rev": 1650, "ep_yoy": 31.0, "note": "Green energy loan book growth; asset quality under scrutiny" },
    { "sym": "MAZAGON", "name": "Mazagon Dock", "sec": "Defence", "exp": "2026-03-17", "est_pat": 640, "est_rev": 2850, "ep_yoy": 21.0, "note": "Submarine and delivery schedule; order book visibility strong" },
]

_data_lock = threading.Lock()
_results_cache = {
    "stocks": INITIAL_STOCKS,
    "upcoming": INITIAL_UPCOMING,
    "last_refresh": None,
    "refreshing": False
}

def load_cache():
    global _results_cache
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r") as f:
                _results_cache = json.load(f)
        except Exception:
            pass

def save_cache():
    with open(CACHE_PATH, "w") as f:
        json.dump(_results_cache, f, indent=4)

def get_results_data():
    with _data_lock:
        return _results_cache

def refresh_upcoming_dates(symbols=None):
    """
    Background task to refresh upcoming earnings dates using yfinance.
    Focuses on Nifty 50 or a provided list of symbols.
    """
    global _results_cache
    if _results_cache["refreshing"]: return False
    
    def _run(target_symbols):
        with _data_lock:
            _results_cache["refreshing"] = True
            
        print("[Results] Refreshing upcoming dates...")
        if target_symbols is None:
            # Revert to Nifty 50 for automatic scans to avoid 429 Rate Limiting
            from sector_data import SECTOR_STOCKS
            nifty50_fyers = SECTOR_STOCKS.get("Banking", [])[:5] + \
                           SECTOR_STOCKS.get("Technology", [])[:5] + \
                           SECTOR_STOCKS.get("Oil & Gas", [])[:3] + \
                           SECTOR_STOCKS.get("FMCG", [])[:3]
            target_symbols = [_to_yf_sym(s) for s in nifty50_fyers]
            
        new_upcoming = []
        for sym_in in target_symbols:
            try:
                ticker_sym = sym_in if sym_in.endswith(".NS") or sym_in.endswith(".BO") else sym_in + ".NS"
                short_sym = ticker_sym.replace(".NS", "").replace(".BO", "")
                
                ticker = yf.Ticker(ticker_sym)
                # Avoid rate limiting: small sleep between requests
                time.sleep(1.5)
                # calendar is a dictionary in some versions, and attribute in others
                cal = getattr(ticker, 'calendar', None)
                if cal is not None:
                    # calendar might be a DataFrame or Dict
                    if hasattr(cal, 'get') and 'Earnings Date' in cal:
                        ed = cal['Earnings Date']
                    elif isinstance(cal, dict) and 'Earnings Date' in cal:
                        ed = cal['Earnings Date']
                    elif hasattr(cal, 'iloc'): # DataFrame
                        # In latest yfinance, calendar is a DataFrame
                        # index might be 'Earnings Date' or 'Value'
                        try:
                            ed = cal.loc['Earnings Date'].values
                        except:
                            ed = []
                    else:
                        ed = []

                    if ed is not None and len(ed) > 0:
                        # yfinance returns a list of dates
                        date_val = ed[0]
                        if hasattr(date_val, 'strftime'):
                             date_str = date_val.strftime("%Y-%m-%d")
                        else:
                             date_str = str(date_val).split(' ')[0]
                        
                        new_upcoming.append({
                            "sym": short_sym,
                            "name": short_sym,
                            "sec": "Auto Detected",
                            "exp": date_str,
                            "est_pat": None,
                            "est_rev": None,
                            "ep_yoy": None,
                            "note": f"Auto-detected earnings date from yfinance: {date_str}"
                        })
            except Exception as e:
                print(f"[Results] Error for {sym_in}: {e}")

        with _data_lock:
            # Merge logic: update if sym exists, else append
            current_upcoming_map = {u["sym"]: u for u in new_upcoming}
            
            # Keep manual entries if not in new_upcoming
            for old_u in INITIAL_UPCOMING:
                if old_u["sym"] not in current_upcoming_map:
                    new_upcoming.append(old_u)
            
            # Filter out dates that are too old (already happened)
            # Promote to historical if needed? For now just filter.
            today_str = datetime.now().strftime("%Y-%m-%d")
            _results_cache["upcoming"] = new_upcoming
            _results_cache["last_refresh"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            _results_cache["refreshing"] = False
            save_cache()
        print("[Results] Refresh done.")

    threading.Thread(target=_run, args=(symbols,), daemon=True).start()
    return True

# Initial load
load_cache()
