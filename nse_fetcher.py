"""
NSE Data Fetcher Module
Provides cached fetching of Option Chains and Quotes from NSE Direct API.
Bypasses Fyers completely.
"""
import time
import threading
from curl_cffi import requests as c_requests

class NSEFetcher:
    def __init__(self):
        self.session = c_requests.Session(impersonate="chrome110")
        self.session.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        })
        self.cookies_set = False
        
        # Cache dictionaries
        self.cache = {}
        self.cache_lock = threading.Lock()
        self.CACHE_TTL = 60  # seconds

    def _setup_session(self):
        """Hit the homepage to get the required cookies before API calls."""
        if self.cookies_set:
            return
        try:
            print("[NSE Fetcher] Initializing NSE Session (fetching cookies)...")
            self.session.get("https://www.nseindia.com", timeout=10)
            # No sleep needed — NSE sets cookies immediately
            self.session.headers.update({
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': 'https://www.nseindia.com',
            })
            self.cookies_set = True
        except Exception as e:
            print(f"[NSE Fetcher] Failed to set cookies: {e}")

    def get_option_chain(self, symbol, is_index=True):
        """
        Fetch Option Chain data. Cached for 60 seconds.
        """
        cache_key = f"opt_{symbol}"
        
        # Check Cache
        with self.cache_lock:
            if cache_key in self.cache:
                timestamp, data = self.cache[cache_key]
                if (time.time() - timestamp) < self.CACHE_TTL:
                    return data
                    
        # If cache expired or empty, fetch fresh data
        self._setup_session()
        
        if is_index:
            url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
        else:
            url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"

        data = {}
        try:
            r = self.session.get(url, timeout=10)
            if r.status_code == 200:
                raw_data = r.json()
                if "records" in raw_data:
                    data = raw_data
                else:
                    print(f"[NSE Fetcher] API returned empty or invalid data for {symbol}.")
            elif r.status_code == 401:
                # Session expired, clear cookies constraint and retry
                print("[NSE Fetcher] Session blocked (401). Refreshing cookies...")
                self.cookies_set = False
                self._setup_session()
                r = self.session.get(url, timeout=10)
                if r.status_code == 200:
                    data = r.json()
            else:
                print(f"[NSE Fetcher] API failed. Status: {r.status_code}")
                
        except Exception as e:
            print(f"[NSE Fetcher] Request error for {symbol}: {e}")

        # Save to Cache regardless (even if empty, to prevent hammering the server on failure)
        with self.cache_lock:
            self.cache[cache_key] = (time.time(), data)
            
        return data

    def get_bulk_quotes(self):
        """
        Fetch bulk live prices for both F&O stocks and NIFTY 500 at once directly from NSE.
        Extremely fast and cached for CACHE_TTL.
        Returns: { 'NSE:RELIANCE-EQ': {'lp': 2900, 'open_price': 2880, ...}, ... }
        """
        cache_key = "bulk_quotes_merged"
        with self.cache_lock:
            if cache_key in self.cache:
                timestamp, data = self.cache[cache_key]
                if (time.time() - timestamp) < self.CACHE_TTL:
                    return data

        self._setup_session()

        def _float(val):
            if isinstance(val, (int, float)): return float(val)
            if isinstance(val, str):
                val = val.replace(',', '')
                try: return float(val)
                except: return 0.0
            return 0.0

        equity_urls = [
            "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O",
            "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20500",
            "https://www.nseindia.com/api/allIndices"
        ]

        # ── Fetch all 3 endpoints SIMULTANEOUSLY in parallel ─────────────────
        import concurrent.futures

        def _fetch_url(url):
            try:
                r = self.session.get(url, timeout=10)
                if r.status_code == 401:
                    self.cookies_set = False
                    self._setup_session()
                    r = self.session.get(url, timeout=10)
                if r.status_code == 200:
                    return url, r.json()
            except Exception as e:
                print(f"[NSE Fetcher] Error fetching {url}: {e}")
            return url, {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            results = dict(ex.map(_fetch_url, equity_urls))

        quotes = {}

        # ── Process equity index results ──────────────────────────────────────
        for url in equity_urls[:2]:
            payload = results.get(url, {})
            for stock in payload.get('data', []):
                sym = stock.get("symbol")
                if not sym: continue
                fyers_sym = f"NSE:{sym}-EQ"
                if fyers_sym in quotes:
                    continue
                quotes[fyers_sym] = {
                    "lp": _float(stock.get("lastPrice", 0)),
                    "open_price": _float(stock.get("open", 0)),
                    "high_price": _float(stock.get("dayHigh", 0)),
                    "low_price": _float(stock.get("dayLow", 0)),
                    "prev_close_price": _float(stock.get("previousClose", 0)),
                    "volume": int(_float(stock.get("totalTradedVolume", 0)))
                }

        # ── Process allIndices for INDEX symbols ──────────────────────────────
        idx_data = results.get(equity_urls[2], {})
        for idx in idx_data.get('data', []):
            sym = idx.get("indexSymbol")
            name_map = {
                "NIFTY 50":          "NSE:NIFTY50-INDEX",
                "NIFTY BANK":        "NSE:NIFTYBANK-INDEX",
                "NIFTY FIN SERVICE": "NSE:FINNIFTY-INDEX",
            }
            map_name = name_map.get(sym)
            if map_name:
                quotes[map_name] = {
                    "lp":             float(idx.get("last", 0) or 0),
                    "open_price":     float(idx.get("open", 0) or 0),
                    "high_price":     float(idx.get("high", 0) or 0),
                    "low_price":      float(idx.get("low", 0) or 0),
                    "prev_close_price": float(idx.get("previousClose", 0) or 0),
                    "volume": 0
                }

        with self.cache_lock:
            self.cache[cache_key] = (time.time(), quotes)

        return quotes

# Global singleton
nse = NSEFetcher()

def get_nse_option_chain(symbol, is_index=True):
    return nse.get_option_chain(symbol, is_index)

def get_nse_bulk_quotes():
    return nse.get_bulk_quotes()
