"""
Options Analyzer Pro -- Flask Main Application
"""
import sys, os, json, time, threading
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
from flask_compress import Compress
from werkzeug.exceptions import HTTPException
from dotenv import load_dotenv

load_dotenv()

# Force UTF-8 output so Unicode in print() never crashes on Windows CP1252 terminal
for _stream_name in ('stdout', 'stderr'):
    _stream = getattr(sys, _stream_name, None)
    if _stream and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

os.environ["PYTHONIOENCODING"] = "utf-8"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "options-analyzer-pro-secret-2024")

# Configure Compression & Static Caching for High Concurrency (1000+ users)
app.config['COMPRESS_ALGORITHM'] = ['gzip']
app.config['COMPRESS_LEVEL']    = 6
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 3600  # 1 hour browser cache for static files
Compress(app)

@app.errorhandler(Exception)
def handle_exception(e):
    try:
        import traceback, datetime
        tb_str = traceback.format_exc()
        with open("flask_error_log.txt", "a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.datetime.now()} ---\n{request.path}\n{tb_str}\n")
    except Exception:
        tb_str = "Error formatting traceback"

    if request.path.startswith("/api/"):
        err_msg = str(e).encode('ascii', errors='replace').decode('ascii')
        return jsonify({"success": False, "error": err_msg, "traceback": tb_str[-1000:]}), 500
    if isinstance(e, HTTPException):
        return e
    return e

# ─── Heartbeat & Session Monitoring ───────────────────────────────────────────
_active_tabs = {}  # { session_id: { tab_id: last_heartbeat_time } }
_tabs_lock = threading.Lock()

def _session_monitor_loop():
    while True:
        try:
            time.sleep(10)
            now = time.time()
            with _tabs_lock:
                to_remove_sessions = []
                for sess_id, tabs in list(_active_tabs.items()):
                    stale_tabs = [tid for tid, ts in tabs.items() if (now - ts) > 20]
                    for tid in stale_tabs: del tabs[tid]
                    if not tabs: to_remove_sessions.append(sess_id)
                for sess_id in to_remove_sessions: del _active_tabs[sess_id]
        except Exception as e:
            print(f"[Monitor] Error: {e}")

threading.Thread(target=_session_monitor_loop, daemon=True, name="session-monitor").start()

_quote_cache = {}    # { "key": (timestamp, data) }
_cache_lock  = threading.Lock()
CACHE_TTL    = 5     # seconds

# ─── Firebase Init (optional) ─────────────────────────────────────────────────
try:
    from firebase_config import init_firebase
    FIREBASE_URL = os.environ.get("FIREBASE_DB_URL", "")
    _firebase_ok = init_firebase(FIREBASE_URL)
    print(f"[Firebase] Initialized successfully.")
except Exception as _fe:
    print(f"[App] Firebase not available: {_fe}")

# ─── Background Scanners Startup ───────────────────────────────────────────────
try:
    from analysis_engine import (
        start_zone_scanner, start_orb_scanner, start_doji_scanner, start_cpr_scanner, start_reversal_scanner
    )
    from strategy_scanner import start_strategy_scanner
    from results_data import refresh_upcoming_dates
    def _startup_scanners():
        start_zone_scanner()
        time.sleep(2)
        start_orb_scanner()
        time.sleep(2)
        start_doji_scanner()
        time.sleep(2)
        start_cpr_scanner()
        time.sleep(2)
        start_reversal_scanner()
        time.sleep(2)
        start_strategy_scanner()
        # Initial results refresh
        threading.Thread(target=refresh_upcoming_dates, daemon=True).start()
    
    threading.Thread(target=_startup_scanners, daemon=True).start()
except Exception as _ze:
    print(f"[App] Scanner startup error: {_ze}")

# ─── Dashboard Data Pre-Compute Cache ───────────────────────────────────────
_dashboard_cache = {
    "sector_performance": {"data": [], "ts": 0},
    "r_factor":           {"data": {}, "ts": 0},
    "heatmap":            {"data": [], "ts": 0},
}
_dashboard_lock = threading.Lock()
DASHBOARD_REFRESH_S = 60 

def _get_quotes(symbols):
    global _quote_cache
    if not symbols: return {}
    cache_key = ",".join(sorted(symbols))
    now = time.time()
    with _cache_lock:
        if cache_key in _quote_cache:
            ts, cached_data = _quote_cache[cache_key]
            if (now - ts) < CACHE_TTL: return cached_data

    from nse_fetcher import get_nse_bulk_quotes
    data = {}
    try:
        bulk = get_nse_bulk_quotes()
        for sym in symbols:
            if sym in bulk: data[sym] = bulk[sym]
    except Exception as e:
        print(f"[App] NSE bulk fetch error: {e}")

    with _cache_lock:
        _quote_cache[cache_key] = (now, data)
    return data

def _refresh_dashboard_cache():
    from analysis_engine import get_sector_performance, get_r_factor_stocks, fyers_to_short
    from sector_data import ALL_SYMBOLS, SECTOR_STOCKS
    while True:
        try:
            quotes = _get_quotes(ALL_SYMBOLS)
            # 1. Sector Performance
            try:
                sector_data = get_sector_performance(quotes)
                with _dashboard_lock:
                    _dashboard_cache["sector_performance"] = {"data": sector_data, "ts": time.time()}
            except Exception as e: print(f"[Cache] sector_performance error: {e}")
            # 2. R-Factor
            try:
                r_data = get_r_factor_stocks(quotes, top_n=10)
                with _dashboard_lock:
                    _dashboard_cache["r_factor"] = {"data": r_data, "ts": time.time()}
            except Exception as e: print(f"[Cache] r_factor error: {e}")
            # 3. Heatmap
            try:
                sectors = []
                for sector, symbols in SECTOR_STOCKS.items():
                    stocks = []
                    for sym in symbols:
                        q = quotes.get(sym); short = fyers_to_short(sym)
                        if not q: stocks.append({"symbol": short, "ltp": 0, "pct": 0}); continue
                        try:
                            ltp  = float(q.get("lp", 0)); prev = float(q.get("prev_close_price", ltp))
                            pct  = round(((ltp - prev) / prev) * 100, 2) if prev > 0 else 0.0
                            stocks.append({"symbol": short, "ltp": round(ltp,2), "pct": pct})
                        except Exception: stocks.append({"symbol": short, "ltp": 0, "pct": 0})
                    avg_pct = round(sum(s["pct"] for s in stocks) / len(stocks), 3) if stocks else 0
                    sectors.append({"sector": sector, "avg_pct": avg_pct, "stocks": stocks})
                sectors.sort(key=lambda x: x["avg_pct"], reverse=True)
                with _dashboard_lock:
                    _dashboard_cache["heatmap"] = {"data": sectors, "ts": time.time()}
            except Exception as e: print(f"[Cache] heatmap error: {e}")
            print(f"[Cache] Dashboard data refreshed at {time.strftime('%H:%M:%S')}")
        except Exception as e: print(f"[Cache] Refresh error: {e}")
        time.sleep(DASHBOARD_REFRESH_S)

# Start dashboard refresher thread
threading.Thread(target=_refresh_dashboard_cache, daemon=True, name="dashboard-refresher").start()

# ─── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return redirect(url_for("dashboard"))

@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    data = request.get_json(); tab_id = data.get("tab_id")
    if not tab_id: return jsonify({"success": False, "error": "Missing tab_id"})
    sess_id = session.get("app_id", "default_session") 
    with _tabs_lock:
        if sess_id not in _active_tabs: _active_tabs[sess_id] = {}
        _active_tabs[sess_id][tab_id] = time.time()
    return jsonify({"success": True})

@app.route('/favicon.ico')
def favicon(): return '', 204

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html", user_name="Administrator")

@app.route("/api/nse-data", methods=["GET"])
def api_nse_data():
    from nse_fetcher import get_nse_option_chain
    symbol = request.args.get("symbol", "NIFTY")
    is_index = request.args.get("is_index", "true").lower() == "true"
    data = get_nse_option_chain(symbol, is_index)
    return jsonify({"success": bool(data), "data": data})

@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({"connected": True, "data_source": "NSE DATA", "user": "Administrator"})

@app.route("/api/cpr-status", methods=["GET"])
def api_cpr_status():
    from analysis_engine import get_cpr_scan_status
    return jsonify(get_cpr_scan_status())

@app.route("/api/cpr-rescan", methods=["POST"])
def api_cpr_rescan():
    from analysis_engine import rescan_cpr
    return jsonify({"success": rescan_cpr()})

@app.route("/api/sector-performance", methods=["GET"])
def api_sector_performance():
    with _dashboard_lock: cache = _dashboard_cache["sector_performance"]
    if cache["data"]: return jsonify({"success": True, "data": cache["data"], "cached_at": cache["ts"]})
    from analysis_engine import get_sector_performance; from sector_data import ALL_SYMBOLS
    return jsonify({"success": True, "data": get_sector_performance(_get_quotes(ALL_SYMBOLS))})

@app.route("/api/r-factor", methods=["GET"])
def api_r_factor():
    with _dashboard_lock: cache = _dashboard_cache["r_factor"]
    if cache["data"]: return jsonify({"success": True, "data": cache["data"], "cached_at": cache["ts"]})
    from analysis_engine import get_r_factor_stocks; from sector_data import ALL_SYMBOLS
    return jsonify({"success": True, "data": get_r_factor_stocks(_get_quotes(ALL_SYMBOLS), top_n=10)})

@app.route("/api/institutional-zones", methods=["GET"])
def api_institutional_zones():
    from analysis_engine import get_zone_scan_status
    cache = get_zone_scan_status()
    db_dem = _safe_numpy(cache["demand"])
    db_sup = _safe_numpy(cache["supply"])
    return jsonify({"success": True, "demand": db_dem, "supply": db_sup, "scan_time": cache["scan_time"], "scanning": cache["scanning"], "progress": {"done": cache["done"], "total": cache["total"]}})

@app.route("/api/institutional-zones/rescan", methods=["POST"])
def api_zones_rescan():
    from analysis_engine import _run_zone_scan
    threading.Thread(target=_run_zone_scan, daemon=True).start()
    return jsonify({"success": True, "message": "Re-scan started"})

@app.route("/api/reversal-stocks", methods=["GET"])
def api_reversal_stocks():
    from analysis_engine import get_reversal_scan_status
    cache = get_reversal_scan_status()
    db_5m = _safe_numpy(cache["rev5m"])
    db_15m = _safe_numpy(cache["rev15m"])
    return jsonify({"success": True, "rev5m": db_5m, "rev15m": db_15m, "scan_time": cache["scan_time"], "scanning": cache["scanning"], "progress": {"done": cache["done"], "total": cache["total"]}})

@app.route("/api/reversal-stocks/rescan", methods=["POST"])
def api_reversal_rescan():
    from analysis_engine import _run_reversal_scan
    threading.Thread(target=_run_reversal_scan, daemon=True).start()
    return jsonify({"success": True, "message": "Reversal re-scan started"})

def _safe_numpy(obj):
    import numpy as np
    if isinstance(obj, (np.integer,)): return int(obj)
    if isinstance(obj, (np.floating,)): return float(obj)
    if isinstance(obj, (np.ndarray,)): return obj.tolist()
    if isinstance(obj, list): return [_safe_numpy(i) for i in obj]
    if isinstance(obj, dict): return {k: _safe_numpy(v) for k, v in obj.items()}
    return obj

@app.route("/api/strategy-scanner", methods=["GET"])
def api_strategy_scanner():
    try:
        from strategy_scanner import get_strategy_status
        cache = get_strategy_status()
        return jsonify({"success": True, "signals": _safe_numpy(cache["signals"]), "scan_time": cache["last_scan_time"], "scanning": bool(cache["scanning"]), "progress": {"done": int(cache.get("done", 0)), "total": int(cache.get("total", 0))}})
    except Exception as e: return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/strategy-scanner/rescan", methods=["POST"])
def api_strategy_rescan():
    from strategy_scanner import _run_strategy_scan
    threading.Thread(target=_run_strategy_scan, daemon=True).start()
    return jsonify({"success": True, "message": "Strategy re-scan started"})

@app.route("/api/orb", methods=["GET"])
def api_orb():
    try:
        from analysis_engine import get_orb_scan_status
        cache = get_orb_scan_status()
        return jsonify({"success": True, "orb5m": _safe_numpy(cache["orb5m"]), "orb15m": _safe_numpy(cache["orb15m"]), "scan_time": cache["scan_time"], "scanning": bool(cache["scanning"]), "progress": {"done": int(cache["done"]), "total": int(cache["total"])}})
    except Exception as e: return jsonify({"success": False, "error": str(e)})

@app.route("/api/doji-scanner", methods=["GET"])
def api_doji_scanner():
    try:
        from analysis_engine import get_doji_scan_status
        cache = get_doji_scan_status()
        return jsonify({"success": True, "doji5m": _safe_numpy(cache["doji5m"]), "doji15m": _safe_numpy(cache["doji15m"]), "scan_time": cache["scan_time"], "scanning": bool(cache["scanning"]), "progress": {"done": int(cache.get("done", 0)), "total": int(cache.get("total", 0))}})
    except Exception as e: return jsonify({"success": False, "error": str(e)})

@app.route("/api/heatmap", methods=["GET"])
def api_heatmap():
    with _dashboard_lock: cache = _dashboard_cache["heatmap"]
    if cache["data"]: return jsonify({"success": True, "sectors": cache["data"], "cached_at": cache["ts"]})
    return jsonify({"success": False, "error": "Heatmap not ready"})

@app.route("/api/results-calendar", methods=["GET"])
def api_results_calendar():
    from results_data import get_results_data
    return jsonify(get_results_data())

@app.route("/api/results-refresh", methods=["POST"])
def api_results_refresh():
    from results_data import refresh_upcoming_dates
    success = refresh_upcoming_dates()
    return jsonify({"success": success})

@app.route("/api/sector-stocks/<sector_name>", methods=["GET"])
def api_sector_stocks(sector_name):
    from analysis_engine import get_sector_stocks_data; from sector_data import SECTOR_STOCKS
    sector_name = sector_name.replace("-", " ").replace("%20", " ")
    for s in SECTOR_STOCKS:
        if s.lower() == sector_name.lower(): sector_name = s; break
    else: return jsonify({"success": False, "error": f"Sector '{sector_name}' not found"})
    symbols = SECTOR_STOCKS[sector_name]
    return jsonify({"success": True, "sector": sector_name, "data": get_sector_stocks_data(_get_quotes(symbols), sector_name)})

if __name__ == "__main__":
    import webbrowser
    port = int(os.environ.get("PORT", 5001))
    print("=======================================================")
    print(f"  Options Analyzer Pro - LOCAL DEV MODE")
    print(f"  Open browser: http://localhost:{port}")
    print("=======================================================")
    threading.Timer(1.5, lambda: webbrowser.open_new_tab(f"http://localhost:{port}")).start()
    app.run(host="0.0.0.0", port=port, threaded=True)
