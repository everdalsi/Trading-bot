"""
Trading Bot v7.1 — Silence Telegram + Agent Conscience
Basé sur ton fichier bot 18.py
"""

import os, time, threading, feedparser, requests, asyncio, json, sqlite3, re, hashlib, base64, hmac, secrets
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
from collections import defaultdict, deque

try:
    import websocket
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False
    print("[WS] websocket-client non installé — fallback REST")

from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest

# ═══════════════════════════════════════════════════════════════
#  SÉCURITÉ — Validation & Rate Limiting
# ═══════════════════════════════════════════════════════════════
_rate_limits: dict = defaultdict(lambda: deque(maxlen=100))

def check_rate_limit(key: str, max_calls: int, window_sec: int) -> bool:
    now = time.time()
    dq = _rate_limits[key]
    while dq and now - dq[0] > window_sec:
        dq.popleft()
    if len(dq) >= max_calls:
        return False
    dq.append(now)
    return True

def validate_symbol(symbol: str) -> bool:
    return bool(re.match(r'^[A-Z]{2,10}USDT$', symbol))

def validate_amount(amount: float, min_v: float = 1.0, max_v: float = 100000.0) -> bool:
    return isinstance(amount, (int, float)) and min_v <= amount <= max_v

def sanitize_string(s: str, max_len: int = 500) -> str:
    if not isinstance(s, str):
        return ""
    return re.sub(r'[^\w\s\.\,\!\?\-\+\%\$\:\(\)\/]', '', s)[:max_len]

def secure_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(str(a), str(b))

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════
GROQ_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
BINANCE_KEY      = os.environ.get("BINANCE_KEY", "")
BINANCE_SECRET   = os.environ.get("BINANCE_SECRET", "")
WEBHOOK_URL      = os.environ.get("WEBHOOK_URL", "")
HF_KEY           = os.environ.get("HF_KEY", "")
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO      = os.environ.get("GITHUB_REPO", "")
WEBHOOK_PATH     = "/webhook"
WEBHOOK_PORT     = 8000

USER_FIRSTNAME = os.environ.get("USER_FIRSTNAME", "")
USER_LASTNAME  = os.environ.get("USER_LASTNAME", "")
USER_EMAIL     = os.environ.get("USER_EMAIL", "")
USER_ADDRESS   = os.environ.get("USER_ADDRESS", "")
USER_WALLET    = os.environ.get("USER_WALLET", "")

CAPITAL_INITIAL   = 1000.0
MAX_POSITIONS     = 4
MAX_PCT_PER_TRADE = 0.25
STOP_LOSS_PCT     = 0.025
TAKE_PROFIT_PCT   = 0.04
TRAILING_PCT      = 0.015
LEVERAGE_SIM      = 2

CONFIDENCE_BASE = 65
CONFIDENCE_MIN  = 55
CONFIDENCE_MAX  = 82

MAX_DAILY_LOSS_PCT   = 0.05
MAX_DRAWDOWN_PCT     = 0.10
FG_NEUTRAL_MIN       = 40
FG_NEUTRAL_MAX       = 60
NIGHT_HOURS_UTC      = range(2, 6)
BLACKLIST_MAX_LOSSES = 5
MAX_LESSONS          = 200
CORRELATED_PAIRS     = [
    {"BTCUSDT","ETHUSDT"},
    {"SOLUSDT","AVAXUSDT"},
    {"BNBUSDT","MATICUSDT"},
]

CYCLE_MONITOR = 15
CYCLE_SCALP   = 300
CYCLE_DEEP    = 300
CYCLE_STATUS  = 900
CYCLE_MICRO   = 8
CYCLE_MEME    = 45
CYCLE_EPARGNE = 3600

MICRO_SL_PCT        = 0.008
MICRO_TP_PCT        = 0.012
MICRO_TRAILING_PCT  = 0.005
MICRO_MAX_DURATION  = 90
MICRO_MAX_PCT       = 0.10
MICRO_CONF_MIN      = 72
MAX_MICRO_POSITIONS = 3

MEME_SL_PCT       = 0.05
MEME_TP_PCT       = 0.15
MEME_TRAILING_PCT = 0.07
MEME_MAX_PCT      = 0.05
MEME_MAX_DURATION = 300

LEARN_MODE_ENABLED  = True
LEARN_MODE_CONF_MIN = 45
LEARN_MODE_MAX_PCT  = 0.05

GROQ_FAST_MODEL = "llama-3.1-8b-instant"
DB_FILE   = "sim_v7.db"
DATA_FILE = Path("sim_portfolio_v7.json")

BINANCE_BASE = "https://api.binance.com"
BINANCE_KLINES = "https://data.binance.com/api/v3/klines"
INTERVAL_MAP = {
    "1":"1m","3":"3m","5":"5m","15":"15m","30":"30m",
    "60":"1h","120":"2h","240":"4h","D":"1d","1D":"1d"
}

CRYPTO_SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","ADAUSDT",
    "AVAXUSDT","LINKUSDT","DOTUSDT","UNIUSDT","LTCUSDT","NEARUSDT","APTUSDT",
    "ARBUSDT","OPUSDT","INJUSDT","SUIUSDT","FETUSDT","RENDERUSDT"
]
MICRO_SYMBOLS = CRYPTO_SYMBOLS[:12]
MEMECOIN_SOLANA = ["BONKUSDT","WIFUSDT","POPCATUSDT","JUPUSDT"]
MEMECOIN_ETH    = ["SHIBUSDT","FLOKIUSDT","PEPEUSDT","DOGEUSDT"]
STOCKS_SYMBOLS = {
    "AAPL":"Apple","TSLA":"Tesla","NVDA":"NVIDIA","META":"Meta",
    "MSFT":"Microsoft","GOOGL":"Google","AMZN":"Amazon",
    "AMD":"AMD","COIN":"Coinbase","MSTR":"MicroStrategy",
}
FOREX_SYMBOLS = {
    "EURUSD=X":"EUR/USD","GBPUSD=X":"GBP/USD",
    "USDJPY=X":"USD/JPY","AUDUSD=X":"AUD/USD",
}
COMMODITY_SYMBOLS = {
    "GC=F":"Or","SI=F":"Argent","CL=F":"Pétrole","HG=F":"Cuivre",
}
ALL_SYMBOLS = CRYPTO_SYMBOLS

NITTER_INSTANCES = ["nitter.privacydev.net","nitter.poast.org","nitter.1d4.us"]
TRADER_TWITTER_ACCOUNTS = [
    "michael_saylor","CathieDWood","APompliano","WClementeIII",
    "CryptoKaleo","RaoulGMI","AlxCooks_off","MustStopMurad",
    "blknoiz06","Degentraland","CryptoGodJohn","solbigbrain",
]
YOUTUBE_CHANNELS = {
    "Benjamin Cowen": "UCRvqjQPSeaWn-uEx-w0XOIg",
    "Coin Bureau":    "UCqK_GSMbpiV8spgD3ZGloSw",
    "InvestAnswers":  "UCnMn36GT_H0X-w5_ckLtlgQ",
}
DEXSCREENER_NEW = "https://api.dexscreener.com/token-boosts/latest/v1"
FAUCET_SOURCES = [
    {"name":"Cointiply",   "url":"https://cointiply.com",   "crypto":"BTC"},
    {"name":"FreeBitcoin", "url":"https://freebitco.in",    "crypto":"BTC"},
    {"name":"Firefaucet",  "url":"https://firefaucet.win",  "crypto":"Multi"},
    {"name":"Rollercoin",  "url":"https://rollercoin.com",  "crypto":"BTC/ETH/DOGE"},
    {"name":"StormGain",   "url":"https://stormgain.com",   "crypto":"BTC"},
]
PROMO_EXCHANGES = [
    {"name":"Binance","url":"https://www.binance.com/en/activity/","type":"exchange"},
    {"name":"KuCoin", "url":"https://www.kucoin.com/news/bonus",   "type":"exchange"},
    {"name":"OKX",    "url":"https://www.okx.com/earn/bonus",      "type":"exchange"},
]

# ═══════════════════════════════════════════════════════════════
#  CLIENTS
# ═══════════════════════════════════════════════════════════════
groq_client = Groq(api_key=GROQ_KEY)

# ═══════════════════════════════════════════════════════════════
#  ÉTAT GLOBAL
# ═══════════════════════════════════════════════════════════════
sim = {
    "cash": CAPITAL_INITIAL, "initial": CAPITAL_INITIAL,
    "positions": {}, "trades": [], "equity_history": [],
    "session": 1, "peak_equity": CAPITAL_INITIAL,
    "daily_start_equity": CAPITAL_INITIAL, "daily_start_date": "",
}
memory = {
    "lessons": [], "patterns_to_avoid": [], "patterns_that_work": [],
    "confidence_threshold": CONFIDENCE_BASE,
    "total_wins": 0, "total_losses": 0,
    "symbol_scores": {}, "symbol_blacklist": {}, "consecutive_losses": {},
}
epargne = {
    "total_earned": 0.0, "airdrops_claimed": [],
    "faucets_used": [], "promos_found": [], "last_scan": 0,
}
bot_state = {
    "running": False, "last_heartbeat": None,
    "cycle_count": 0, "trades_today": 0,
    "last_monitor": 0, "last_scalp": 0, "last_deep": 0,
    "last_status": 0, "last_micro": 0, "last_meme": 0,
    "last_epargne": 0, "nitter_idx": 0, "yt_idx": 0,
    "micro_count": 0, "daily_stopped": False,
    "fg_value": 50, "macro_trend": "NEUTRAL",
}

_main_loop = None
_app       = None
_signal_cache:   set  = set()
_price_cache:    dict = {}
_yahoo_cache:    dict = {}
_dex_cache:      dict = {}
_kline_cache_1m: dict = {}
_kline_cache_5m: dict = {}
_trending_cache: list = []
_trending_ts:   float = 0
_liquidations_cache = {"data": None, "ts": 0}
_coingecko_cache    = {"data": None, "ts": 0}
_whale_cache        = {"alerts": [], "ts": 0}
_polymarket_cache   = {"markets": [], "ts": 0}
_arb_cache          = {"opportunities": [], "ts": 0}
_options_cache      = {"data": None, "ts": 0}
_oi_cache           = {"data": {}, "ts": 0}
_fg_cache           = {"value": 50, "ts": 0}
_macro_cache        = {"trend": "NEUTRAL", "ts": 0}

# ═══════════════════════════════════════════════════════════════
#  WEBSOCKET BINANCE
# ═══════════════════════════════════════════════════════════════
_ws_klines_1m: dict = {}
_ws_klines_5m: dict = {}
_ws_connected: bool = False
_ws_thread = None
_ws_lock = threading.Lock()

WS_SYMBOLS_WATCH = [
    "btcusdt","ethusdt","solusdt","bnbusdt","xrpusdt",
    "dogeusdt","avaxusdt","linkusdt","arbusdt","aptusdt",
    "fetusdt","injusdt","nearusdt","suiusdt","opusdt",
]

def _ws_on_message(ws, message):
    try:
        data = json.loads(message)
        if "stream" not in data:
            return
        stream = data["stream"]
        kline  = data["data"]["k"]
        symbol = kline["s"].upper()
        close  = float(kline["c"])
        is_closed = kline["x"]
        with _ws_lock:
            if "1m" in stream:
                if symbol not in _ws_klines_1m:
                    _ws_klines_1m[symbol] = deque(maxlen=60)
                if is_closed or not _ws_klines_1m[symbol]:
                    _ws_klines_1m[symbol].append(close)
                elif _ws_klines_1m[symbol]:
                    _ws_klines_1m[symbol][-1] = close
            elif "5m" in stream:
                if symbol not in _ws_klines_5m:
                    _ws_klines_5m[symbol] = deque(maxlen=120)
                if is_closed or not _ws_klines_5m[symbol]:
                    _ws_klines_5m[symbol].append(close)
                elif _ws_klines_5m[symbol]:
                    _ws_klines_5m[symbol][-1] = close
    except Exception as e:
        print(f"[WS-MSG] {e}")

def _ws_on_error(ws, error):
    global _ws_connected
    print(f"[WS] Erreur: {error}")
    _ws_connected = False

def _ws_on_close(ws, close_status_code, close_msg):
    global _ws_connected
    print(f"[WS] Fermé: {close_status_code}")
    _ws_connected = False

def _ws_on_open(ws):
    global _ws_connected
    _ws_connected = True
    print("[WS] Connecté à Binance WebSocket ✅")

def _build_ws_url() -> str:
    streams = []
    for sym in WS_SYMBOLS_WATCH:
        streams.append(f"{sym}@kline_1m")
        streams.append(f"{sym}@kline_5m")
    return f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"

def _ws_run_forever():
    global _ws_connected
    while True:
        try:
            ws = websocket.WebSocketApp(
                _build_ws_url(),
                on_message=_ws_on_message,
                on_error=_ws_on_error,
                on_close=_ws_on_close,
                on_open=_ws_on_open,
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            print(f"[WS] Run error: {e}")
        _ws_connected = False
        print("[WS] Reconnexion dans 10s...")
        time.sleep(10)

def start_websocket():
    global _ws_thread
    if not WS_AVAILABLE:
        print("[WS] Module websocket-client absent — utilise REST")
        return
    threading.Thread(target=_ws_prefill_from_rest, daemon=True).start()
    _ws_thread = threading.Thread(target=_ws_run_forever, daemon=True)
    _ws_thread.start()
    print("[WS] Thread WebSocket démarré")

def _ws_prefill_from_rest():
    print("[WS] Pré-remplissage buffers depuis Binance REST...")
    for sym_lower in WS_SYMBOLS_WATCH[:8]:
        sym = sym_lower.upper()
        try:
            r1 = requests.get(
                f"{BINANCE_BASE}/api/v3/klines",
                params={"symbol": sym, "interval": "1m", "limit": 60},
                timeout=8, headers={"User-Agent": "Mozilla/5.0"}
            )
            if r1.status_code == 200:
                closes = [float(c[4]) for c in r1.json()]
                with _ws_lock:
                    _ws_klines_1m[sym] = deque(closes, maxlen=60)
        except Exception:
            pass
        try:
            r5 = requests.get(
                f"{BINANCE_BASE}/api/v3/klines",
                params={"symbol": sym, "interval": "5m", "limit": 120},
                timeout=8, headers={"User-Agent": "Mozilla/5.0"}
            )
            if r5.status_code == 200:
                closes = [float(c[4]) for c in r5.json()]
                with _ws_lock:
                    _ws_klines_5m[sym] = deque(closes, maxlen=120)
        except Exception:
            pass
        time.sleep(0.3)
    print("[WS] Pré-remplissage terminé ✅")

# ═══════════════════════════════════════════════════════════════
#  AI POOL — Optimisé avec cache 
AI_PROVIDERS = [
    {"name":"groq","calls":0,"window_start":time.time(),"last_call":0,
     "max_calls_per_hour":10,"cooldown":360,"available":True,"failures":0},
]
_pool_stats = {
    "total_calls":0,"calls_by_provider":{},"fallbacks":0,"last_provider":"groq",
    "cache_hits":0,
}
HF_MODELS = [
    "mistralai/Mistral-7B-Instruct-v0.3",
    "HuggingFaceH4/zephyr-7b-beta",
    "tiiuae/falcon-7b-instruct",
]
_hf_model_idx = 0
_ai_cache: dict = {}
_AI_CACHE_TTL = 60

def _ai_cache_key(prompt: str) -> str:
    return hashlib.md5(prompt[:200].encode()).hexdigest()

def _get_cached_ai(prompt: str) -> dict | None:
    key = _ai_cache_key(prompt)
    if key in _ai_cache:
        ts, result = _ai_cache[key]
        if time.time() - ts < _AI_CACHE_TTL:
            _pool_stats["cache_hits"] += 1
            return result
    return None

def _set_cached_ai(prompt: str, result: dict):
    key = _ai_cache_key(prompt)
    _ai_cache[key] = (time.time(), result)
    if len(_ai_cache) > 200:
        cutoff = time.time() - _AI_CACHE_TTL * 2
        for k in [k for k, (ts, _) in list(_ai_cache.items()) if ts < cutoff]:
            del _ai_cache[k]

def _compress_prompt(prompt: str) -> str:
    lines = prompt.strip().split('\n')
    compressed = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("Traders:") and len(line) > 100:
            line = line[:100]
        if line.startswith("Gains:") or line.startswith("Erreurs:"):
            line = line[:80]
        compressed.append(line)
    return '\n'.join(compressed)[:450]

def _get_available_provider() -> dict | None:
    now = time.time()
    hour_utc = datetime.utcnow().hour
    is_night  = hour_utc in NIGHT_HOURS_UTC
    for p in AI_PROVIDERS:
        if not p["available"]:
            if now - p.get("disabled_at", 0) > 1800:
                p["available"] = True
                p["failures"] = 0
            else:
                continue
        if now - p["window_start"] > 3600:
            p["calls"] = 0
            p["window_start"] = now
        limit = p["max_calls_per_hour"] // 2 if is_night else p["max_calls_per_hour"]
        if p["calls"] >= limit:
            continue
        if now - p["last_call"] < p["cooldown"]:
            continue
        return p
    return None

def _call_groq(prompt: str) -> dict:
    r = groq_client.chat.completions.create(
        model=GROQ_FAST_MODEL,
        max_tokens=120,
        temperature=0.1,
        messages=[
            {
                "role": "system",
                "content": (
                    'Réponds UNIQUEMENT avec un objet JSON valide, sans texte autour. '
                    'Format exact: {"signal":"BUY|SELL|HOLD","confidence":0,"reason":"...","risk":"LOW|MEDIUM|HIGH","market":"SPOT|FUTURES"}'
                )
            },
            {"role": "user", "content": prompt[:500]}
        ],
    )

    text = (r.choices[0].message.content or "").strip()
    text = text.replace("```json", "").replace("```", "").strip()

    s = text.find("{")
    e = text.rfind("}") + 1

    if s >= 0 and e > s:
        text = text[s:e]
    else:
        raise Exception(f"Groq non-JSON response: {text[:200]}")

    try:
        result = json.loads(text)
    except Exception:
        raise Exception(f"Groq invalid JSON: {text[:200]}")

    if result.get("signal") not in ("BUY", "SELL", "HOLD"):
        result["signal"] = "HOLD"

    if "confidence" not in result:
        result["confidence"] = 0
    if "reason" not in result:
        result["reason"] = "missing_reason"
    if "risk" not in result:
        result["risk"] = "HIGH"
    if "market" not in result:
        result["market"] = "SPOT"

    return result

def _call_huggingface(prompt: str) -> dict:
    if not HF_KEY:
        raise Exception("HF_KEY manquante")

    model = HF_MODELS[_hf_model_idx % len(HF_MODELS)]

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": 'Réponds UNIQUEMENT en JSON: {"signal":"BUY|SELL|HOLD","confidence":70,"reason":"...","risk":"LOW|MEDIUM|HIGH","market":"SPOT|FUTURES"}'},
            {"role": "user", "content": prompt[:500]}
        ],
        "temperature": 0.1,
        "max_tokens": 120
    }

    r = requests.post(
        "https://router.huggingface.co/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {HF_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=20,
    )

    if r.status_code != 200:
        raise Exception(f"HF HTTP {r.status_code}: {r.text[:200]}")

    raw = r.json()
    text = raw["choices"][0]["message"]["content"].strip()
    text = text.replace("```json", "").replace("```", "").strip()

    s = text.find("{")
    e = text.rfind("}") + 1
    if s >= 0 and e > s:
        result = json.loads(text[s:e])
        if result.get("signal") not in ("BUY", "SELL", "HOLD"):
            result["signal"] = "HOLD"
        return result

    return {"signal":"HOLD","confidence":0,"reason":"hf_no_json","risk":"HIGH","market":"SPOT"}

def ask_ai(prompt: str) -> dict:
    cached = _get_cached_ai(prompt)
    if cached is not None:
        return cached
    if not check_rate_limit("ask_ai_global", 200, 3600):
        return {"signal":"HOLD","confidence":0,"reason":"rate_limit_global","risk":"HIGH"}
    compressed = _compress_prompt(prompt)
    _pool_stats["total_calls"] += 1
    for _ in range(len(AI_PROVIDERS)+1):
        provider = _get_available_provider()
        if provider is None:
            return {"signal":"HOLD","confidence":0,"reason":"pool_epuise","risk":"HIGH"}
        name = provider["name"]
        try:
            provider["calls"] += 1
            provider["last_call"] = time.time()
            result = _call_groq(compressed) if name == "groq" else _call_huggingface(compressed)
            provider["failures"] = max(0, provider["failures"]-1)
            _pool_stats["last_provider"] = name
            _pool_stats["calls_by_provider"][name] = \
                _pool_stats["calls_by_provider"].get(name, 0)+1
            _set_cached_ai(prompt, result)
            print(f"[AI] {name} ✅ {result.get('signal')} {result.get('confidence')}%")
            return result
        except Exception as e:
            err = str(e)
            provider["failures"] += 1
            print(f"[AI] {name} err: {err[:50]}")
            if "rate_limit" in err or "429" in err:
                provider["calls"] = provider["max_calls_per_hour"]
            if provider["failures"] >= 3:
                provider["available"] = False
                provider["disabled_at"] = time.time()
            _pool_stats["fallbacks"] += 1
    return {"signal":"HOLD","confidence":0,"reason":"all_failed","risk":"HIGH"}

def vote(prompt: str) -> dict:
    r1 = ask_ai(prompt)
    if r1.get("reason") in ("pool_epuise","all_failed","rate_limit_global"):
        return {**r1,"votes":[r1["signal"]],"consensus":"0/1"}
    if r1["signal"] == "HOLD" or r1.get("confidence",0) < 60:
        return {**r1,"votes":[r1["signal"]],"consensus":"1/1"}
    r2 = ask_ai(prompt)
    if r2["signal"] == r1["signal"]:
        conf = min(95, round((r1.get("confidence",0)+r2.get("confidence",0))/2)+5)
        return {"signal":r1["signal"],"confidence":conf,
                "reason":r2.get("reason",r1.get("reason","")),
                "risk":r1.get("risk","MEDIUM"),"market":r1.get("market","SPOT"),
                "votes":[r1["signal"],r2["signal"]],"consensus":"2/2"}
    return {"signal":"HOLD","confidence":0,
            "reason":f"Désaccord ({r1['signal']}/{r2['signal']})",
            "risk":"HIGH","votes":[r1["signal"],r2["signal"]],"consensus":"0/2"}

def get_pool_status() -> str:
    now = time.time()
    lines = ["🧠 AI POOL STATUS\n━━━━━━━━━━━━━"]
    for p in AI_PROVIDERS:
        cd = max(0, int(p["cooldown"]-(now-p["last_call"])))
        st = "✅" if p["available"] and p["calls"] < p["max_calls_per_hour"] else "⏸"
        total = _pool_stats["calls_by_provider"].get(p["name"], 0)
        lines.append(f"  {st} {p['name']:12s} {p['calls']}/{p['max_calls_per_hour']}/h "
                     f"({total} total) cd:{cd}s")
    lines.append(f"  Total: {_pool_stats['total_calls']} | Fallbacks: {_pool_stats['fallbacks']}")
    lines.append(f"  Cache hits: {_pool_stats['cache_hits']} | Dernier: {_pool_stats['last_provider']}")
    return "\n".join(lines)

# ═══════════════════════════════════════════════════════════════
#  RISK MANAGEMENT
# ═══════════════════════════════════════════════════════════════
def check_daily_reset():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if sim.get("daily_start_date") != today:
        sim["daily_start_date"]   = today
        sim["daily_start_equity"] = get_equity_safe()
        bot_state["daily_stopped"] = False

def get_equity_safe() -> float:
    try:
        prices = get_prices_batch()
        equity = float(sim.get("cash", CAPITAL_INITIAL))
        if not (0 < equity < 1_000_000):
            equity = CAPITAL_INITIAL
        for pos in list(sim.get("positions", {}).values()):
            try:
                symbol   = pos.get("symbol","")
                price_in = float(pos.get("price_in", 0))
                amount   = float(pos.get("amount_usd", 0))
                lev      = float(pos.get("leverage", 1))
                side     = pos.get("side","LONG")
                if price_in <= 0 or amount <= 0:
                    continue
                if amount > CAPITAL_INITIAL * 2:
                    continue
                p = prices.get(symbol, price_in)
                if p <= 0 or p > price_in * 100:
                    p = price_in
                if side == "LONG":
                    pos_pnl = (p - price_in) / price_in * amount * lev
                else:
                    pos_pnl = (price_in - p) / price_in * amount * lev
                pos_pnl = max(-amount, min(amount * 10, pos_pnl))
                equity += amount + pos_pnl
            except Exception:
                continue
        return round(max(0, min(equity, CAPITAL_INITIAL * 100)), 2)
    except Exception:
        return CAPITAL_INITIAL

def check_risk_limits(send_fn) -> bool:
    if bot_state.get("daily_stopped"):
        return False
    check_daily_reset()
    equity = get_equity_safe()
    if equity > sim.get("peak_equity", equity):
        sim["peak_equity"] = equity
    daily_start = sim.get("daily_start_equity", CAPITAL_INITIAL)
    if daily_start > 0:
        daily_loss = (equity - daily_start) / daily_start
        if daily_loss <= -MAX_DAILY_LOSS_PCT:
            bot_state["daily_stopped"] = True
            send_fn(f"🛑 STOP JOURNALIER ACTIVÉ\nPerte: {daily_loss*100:.1f}%\nCapital: ${equity:.2f}\nTrading suspendu jusqu'à demain UTC.")
            return False
    peak = sim.get("peak_equity", CAPITAL_INITIAL)
    if peak > 0:
        drawdown = (equity - peak) / peak
        if drawdown <= -MAX_DRAWDOWN_PCT:
            bot_state["daily_stopped"] = True
            send_fn(f"⚠️ DRAWDOWN MAX ATTEINT\nDrawdown: {drawdown*100:.1f}%\nCapital: ${equity:.2f}")
            return False
    return True

def is_correlated(symbol: str) -> bool:
    open_symbols = {pos["symbol"] for pos in sim["positions"].values()}
    for group in CORRELATED_PAIRS:
        if symbol in group:
            if open_symbols & (group - {symbol}):
                return True
    return False

def is_blacklisted(symbol: str) -> bool:
    bl = memory.get("symbol_blacklist", {})
    if symbol not in bl:
        return False
    if time.time() - bl[symbol].get("ts", 0) > 86400:
        del memory["symbol_blacklist"][symbol]
        return False
    return True

def update_blacklist(symbol: str, won: bool):
    cl = memory.setdefault("consecutive_losses", {})
    if won:
        cl[symbol] = 0
    else:
        cl[symbol] = cl.get(symbol, 0) + 1
        if cl[symbol] >= BLACKLIST_MAX_LOSSES:
            memory.setdefault("symbol_blacklist", {})[symbol] = {
                "ts": time.time(),
                "reason": f"{BLACKLIST_MAX_LOSSES} pertes consécutives",
                "losses": cl[symbol],
            }

def is_night_time() -> bool:
    return datetime.utcnow().hour in NIGHT_HOURS_UTC

def get_fear_greed_value() -> int:
    now = time.time()
    if now - _fg_cache["ts"] < 600:
        return _fg_cache["value"]
    try:
        d = requests.get("https://api.alternative.me/fng/", timeout=5).json()["data"][0]
        val = int(d["value"])
        _fg_cache["value"] = val
        _fg_cache["ts"] = now
        bot_state["fg_value"] = val
        return val
    except Exception:
        return _fg_cache.get("value", 50)

def is_fg_neutral() -> bool:
    fg = get_fear_greed_value()
    return FG_NEUTRAL_MIN <= fg <= FG_NEUTRAL_MAX

def get_macro_trend() -> str:
    now = time.time()
    if now - _macro_cache["ts"] < 300:
        return _macro_cache["trend"]
    try:
        onchain  = get_onchain_data()
        mcap_chg = onchain.get("mcap_change_24h", 0)
        closes   = get_klines("BTCUSDT", "60", 24)
        if len(closes) >= 5:
            trend_up = closes.iloc[-1] > closes.iloc[-5]
        else:
            trend_up = None
        if mcap_chg > 2 and (trend_up or trend_up is None):
            trend = "BULL"
        elif mcap_chg < -2 and (not trend_up or trend_up is None):
            trend = "BEAR"
        else:
            trend = "NEUTRAL"
        _macro_cache["trend"] = trend
        _macro_cache["ts"] = now
        bot_state["macro_trend"] = trend
        return trend
    except Exception:
        return _macro_cache.get("trend", "NEUTRAL")

def get_symbol_confidence_bonus(symbol: str) -> int:
    return memory.get("symbol_scores", {}).get(symbol, 0)

def update_symbol_score(symbol: str, won: bool):
    scores = memory.setdefault("symbol_scores", {})
    current = scores.get(symbol, 0)
    scores[symbol] = max(-15, min(15, current + (2 if won else -3)))

def get_symbol_confidence(symbol: str) -> float:
    stats = memory.get("symbol_stats", {}).get(symbol, {})
    wins = stats.get("wins", 0)
    losses = stats.get("losses", 0)

    total = wins + losses
    if total == 0:
        return 0.5

    return wins / total

def get_volume_profile(symbol: str) -> dict:
    try:
        closes = get_klines(symbol, "60", 48)
        if len(closes) < 10:
            return {}
        vols = get_volume_data(symbol, "60", 48)
        if len(vols) != len(closes):
            return {}
        poc_idx   = vols.index(max(vols))
        poc_price = float(closes.iloc[poc_idx]) if poc_idx < len(closes) else float(closes.iloc[-1])
        current   = float(closes.iloc[-1])
        support   = round(poc_price * 0.98, 6)
        resistance= round(poc_price * 1.02, 6)
        return {
            "poc": round(poc_price,6), "support": support, "resistance": resistance,
            "near_support": abs(current-support)/current < 0.015,
            "near_resistance": abs(current-resistance)/current < 0.015,
        }
    except Exception:
        return {}

# ═══════════════════════════════════════════════════════════════
#  KELLY CRITERION
# ═══════════════════════════════════════════════════════════════
def kelly_criterion(n_recent: int=30) -> float:
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    if len(closed) < 5:
        return 0.10
    recent = closed[-n_recent:]
    wins   = [t for t in recent if t["pnl"] > 0]
    losses = [t for t in recent if t["pnl"] <= 0]
    if not wins or not losses:
        return 0.08
    p = len(wins)/len(recent); q = 1-p
    avg_win  = sum(t.get("pnl_pct",0) for t in wins)  / len(wins)  / 100
    avg_loss = abs(sum(t.get("pnl_pct",0) for t in losses) / len(losses)) / 100
    if avg_loss == 0:
        return MAX_PCT_PER_TRADE
    b = avg_win/avg_loss
    kelly = (p*b - q) / b
    base = round(max(0.03, min(MAX_PCT_PER_TRADE, kelly/4)), 3)

    # Boost Fear extrême
    fg = get_fear_greed_value()
    if fg < 25:
        base = min(0.25, base * 1.3)
    return base

def dynamic_position_size(confidence: int, market: str, symbol: str) -> float:
    base        = kelly_criterion(30)
    conf_mult   = 0.5 + (confidence-55)/90
    market_mult = 0.6 if market=="FUTURES" else 0.4 if market=="MEME" else 1.0
    vol_mult    = 1.0
    night_mult  = 0.6 if is_night_time() else 1.0
    macro       = get_macro_trend()
    macro_mult  = 0.7 if macro=="BEAR" else 1.1 if macro=="BULL" else 1.0
    try:
        closes = get_klines_5m_cached(symbol)
        if not closes.empty:
            vol = float(closes.pct_change().dropna().std()*100)
            if vol > 3:   vol_mult = 0.7
            elif vol < 1: vol_mult = 1.2
    except Exception:
        pass
    return round(max(0.03, min(MAX_PCT_PER_TRADE,
        base * conf_mult * market_mult * vol_mult * night_mult * macro_mult)), 3)

# ═══════════════════════════════════════════════════════════════
#  DONNÉES DE MARCHÉ (inchangé)
# ═══════════════════════════════════════════════════════════════
COINGECKO_IDS = {
    "BTCUSDT":"bitcoin","ETHUSDT":"ethereum","SOLUSDT":"solana",
    "BNBUSDT":"binancecoin","XRPUSDT":"ripple","DOGEUSDT":"dogecoin",
    "ADAUSDT":"cardano","AVAXUSDT":"avalanche-2","MATICUSDT":"matic-network",
    "LINKUSDT":"chainlink","DOTUSDT":"polkadot","UNIUSDT":"uniswap",
    "ATOMUSDT":"cosmos","LTCUSDT":"litecoin","NEARUSDT":"near",
    "APTUSDT":"aptos","ARBUSDT":"arbitrum","OPUSDT":"optimism",
    "INJUSDT":"injective-protocol","SUIUSDT":"sui","FETUSDT":"fetch-ai",
    "RENDERUSDT":"render-token","WLDUSDT":"worldcoin-wld","JUPUSDT":"jupiter-exchange-solana",
    "TIAUSDT":"celestia","SEIUSDT":"sei-network","ENAUSDT":"ethena",
    "BONKUSDT":"bonk","WIFUSDT":"dogwifcoin","SHIBUSDT":"shiba-inu",
    "PEPEUSDT":"pepe","FLOKIUSDT":"floki",
}

_prices_cache_ts   = 0
_prices_cache_data = {}
_price_cache = {}

def get_prices_batch() -> dict:
    global _prices_cache_ts, _prices_cache_data
    now = time.time()
    if now - _prices_cache_ts < 120 and _prices_cache_data:
        return _prices_cache_data
    try:
        ids = ",".join(COINGECKO_IDS.values())
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ids, "vs_currencies": "usd"},
            timeout=15, headers={"User-Agent":"Mozilla/5.0"}
        )
        if r.status_code == 200:
            data = r.json()
            prices = {}
            for symbol, cg_id in COINGECKO_IDS.items():
                if cg_id in data and "usd" in data[cg_id]:
                    p = float(data[cg_id]["usd"])
                    prices[symbol] = p
                    _price_cache[symbol] = (now, p)
            _prices_cache_data = prices
            _prices_cache_ts = now
            return prices
        elif r.status_code == 429:
            print("[CG] Rate limit — utilise cache")
            return _prices_cache_data
    except Exception as e:
        print(f"[PRICE] {e}")
    return _prices_cache_data

def get_price(symbol: str, force=False) -> float:
    now = time.time()
    if not force and symbol in _price_cache:
        ts, p = _price_cache[symbol]
        if now-ts < 15:
            return p
    prices = get_prices_batch()
    return prices.get(symbol, _price_cache.get(symbol, (0, 0.0))[1])

def get_klines(symbol: str, interval: str, limit=100) -> pd.Series:
    cg_id = COINGECKO_IDS.get(symbol)
    if not cg_id:
        return pd.Series(dtype=float)
    days_map = {"1":"1","5":"1","15":"1","60":"7","240":"14","D":"30","1D":"30"}
    days = days_map.get(interval, "1")
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart",
            params={"vs_currency":"usd","days":days},
            timeout=15, headers={"User-Agent":"Mozilla/5.0"}
        )
        if r.status_code == 200:
            prices_list = r.json().get("prices",[])
            closes = pd.Series([float(p[1]) for p in prices_list], dtype=float)
            return closes.tail(limit)
    except Exception as e:
        print(f"[KLINE] {e}")
    return pd.Series(dtype=float)

def get_klines_5m_cached(symbol: str) -> pd.Series:
    now = time.time()
    with _ws_lock:
        ws_data = _ws_klines_5m.get(symbol)
    if ws_data and len(ws_data) >= 27:
        return pd.Series(list(ws_data), dtype=float)
    if symbol in _kline_cache_5m:
        ts, closes = _kline_cache_5m[symbol]
        if now-ts < 30:
            return closes
    closes = get_klines(symbol, "5", 60)
    _kline_cache_5m[symbol] = (now, closes)
    return closes

def get_klines_1m_cached(symbol: str) -> pd.Series:
    now = time.time()
    with _ws_lock:
        ws_data = _ws_klines_1m.get(symbol)
    if ws_data and len(ws_data) >= 14:
        return pd.Series(list(ws_data), dtype=float)
    if symbol in _kline_cache_1m:
        ts, closes = _kline_cache_1m[symbol]
        if now-ts < 5:
            return closes
    closes = get_klines(symbol, "1", 30)
    _kline_cache_1m[symbol] = (now, closes)
    return closes

def get_volume_data(symbol: str, interval="5", limit=20) -> list:
    cg_id = COINGECKO_IDS.get(symbol)
    if not cg_id:
        return []
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart",
            params={"vs_currency":"usd","days":"1"},
            timeout=15, headers={"User-Agent":"Mozilla/5.0"}
        )
        if r.status_code == 200:
            vols = r.json().get("total_volumes",[])
            return [float(v[1]) for v in vols[-limit:]]
    except Exception as e:
        print(f"[VOL] {e}")
    return []

def get_order_book(symbol: str) -> dict:
    return {"ratio": 1.0, "pressure": "neutre"}

def get_liquidations() -> dict:
    now = time.time()
    if now - _liquidations_cache["ts"] < 60:
        return _liquidations_cache["data"] or {}
    try:
        result = {}
        for sym in ["BTCUSDT","ETHUSDT","SOLUSDT"]:
            r = requests.get(
                f"{BINANCE_BASE}/fapi/v1/fundingRate",
                params={"symbol":sym,"limit":1}, timeout=8,
                headers={"User-Agent":"Mozilla/5.0"}
            )
            if r.status_code == 200 and r.json():
                rate = float(r.json()[0].get("fundingRate", 0))
                result[sym] = {
                    "funding_rate": rate,
                    "signal": "bearish" if rate>0.001 else "bullish" if rate<-0.001 else "neutral"
                }
        _liquidations_cache["data"] = result
        _liquidations_cache["ts"] = now
        return result
    except Exception as e:
        print(f"[LIQ] {e}")
        return {}

def interpret_liquidations(liq: dict) -> str:
    if not liq:
        return "Liquidations: N/A"
    lines = ["💥 Funding:"]
    for sym, data in list(liq.items())[:3]:
        rate = data.get("funding_rate",0)
        signal = data.get("signal","neutral")
        e = "🟢" if signal=="bullish" else "🔴" if signal=="bearish" else "⚪"
        lines.append(f"  {e} {sym.replace('USDT','')}: {rate*100:.4f}% ({signal})")
    return "\n".join(lines)

def get_onchain_data() -> dict:
    now = time.time()
    if now - _coingecko_cache["ts"] < 300:
        return _coingecko_cache["data"] or {}
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=10, headers={"User-Agent":"Mozilla/5.0"}
        )
        if r.status_code == 200:
            d = r.json().get("data",{})
            result = {
                "btc_dominance":  round(d.get("market_cap_percentage",{}).get("btc",0),1),
                "total_mcap":     d.get("total_market_cap",{}).get("usd",0),
                "mcap_change_24h":round(d.get("market_cap_change_percentage_24h_usd",0),2)
            }
            _coingecko_cache["data"] = result
            _coingecko_cache["ts"] = now
            return result
    except Exception as e:
        print(f"[GECKO] {e}")
    return {}

def format_onchain(data: dict) -> str:
    if not data:
        return "On-chain: N/A"
    mcap_b = data.get("total_mcap",0)/1e9
    chg    = data.get("mcap_change_24h",0)
    return f"🔗 MCap=${mcap_b:.0f}B ({chg:+.1f}%) | BTC dom={data.get('btc_dominance',0)}%"

def get_whale_alerts() -> list:
    now = time.time()
    if now - _whale_cache["ts"] < 120:
        return _whale_cache["alerts"]
    alerts = []
    try:
        for sym in ["BTCUSDT","ETHUSDT"]:
            vols = get_volume_data(sym,"5",5)
            if len(vols) >= 2:
                avg   = sum(vols[:-1]) / max(len(vols)-1,1)
                ratio = vols[-1]/avg if avg > 0 else 1
                if ratio > 3:
                    alerts.append({
                        "summary": f"Volume spike {sym.replace('USDT','')} x{ratio:.1f}",
                        "ts": datetime.now().strftime("%H:%M")
                    })
    except Exception as e:
        print(f"[WHALE] {e}")
    _whale_cache["alerts"] = alerts
    _whale_cache["ts"] = now
    return alerts

def format_whale_alerts(alerts: list) -> str:
    if not alerts:
        return "🐋 Whales: aucun mouvement"
    return "🐋 " + " | ".join(a.get("summary","")[:40] for a in alerts[:2])

def get_polymarket_markets() -> list:
    now = time.time()
    if now - _polymarket_cache["ts"] < 300:
        return _polymarket_cache["markets"]
    try:
        r = requests.get(
            "https://clob.polymarket.com/markets", timeout=10,
            params={"active":"true","limit":20},
            headers={"User-Agent":"Mozilla/5.0"}
        )
        if r.status_code == 200:
            markets = r.json().get("data",[]); processed = []
            for m in markets[:20]:
                tokens = m.get("tokens",[])
                if len(tokens) < 2: continue
                yes_price = float(tokens[0].get("price",0.5))
                no_price  = float(tokens[1].get("price",0.5))
                if yes_price < 0.01 or no_price < 0.01: continue
                if m.get("volume",0) < 100: continue
                total = yes_price + no_price
                if abs(total-1.0) > 0.02:
                    processed.append({
                        "question": m.get("question","")[:80],
                        "yes_price": yes_price, "no_price": no_price,
                        "inefficiency": round(abs(total-1.0)*100,2),
                        "volume": m.get("volume",0)
                    })
            processed.sort(key=lambda x: x["inefficiency"], reverse=True)
            _polymarket_cache["markets"] = processed
            _polymarket_cache["ts"] = now
            return processed[:5]
    except Exception as e:
        print(f"[POLY] {e}")
    return []

def format_polymarket(markets: list) -> str:
    if not markets:
        return "Polymarket: aucune inefficacité"
    best = markets[0]
    return f"🎯 Poly: {best['question'][:50]} (ineff:{best['inefficiency']:.1f}%)"

def detect_arbitrage() -> list:
    now = time.time()
    if now - _arb_cache["ts"] < 30:
        return _arb_cache["opportunities"]
    opportunities = []
    binance_prices = get_prices_batch()
    try:
        r = requests.get(
            "https://api.kucoin.com/api/v1/prices", timeout=8,
            headers={"User-Agent":"Mozilla/5.0"}
        )
        if r.status_code == 200:
            kucoin_data = r.json().get("data",{})
            for sym in ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT"]:
                bp   = binance_prices.get(sym,0)
                coin = sym.replace("USDT","")
                kp   = float(kucoin_data.get(coin,0))
                if not bp or not kp: continue
                spread = abs(bp-kp)/bp*100
                if spread > 0.15:
                    opportunities.append({
                        "symbol": sym, "binance": bp, "kucoin": kp,
                        "spread_pct": round(spread,3),
                        "profit_est": round(spread-0.10,3)
                    })
    except Exception as e:
        print(f"[ARB] {e}")
    opportunities.sort(key=lambda x: x["spread_pct"], reverse=True)
    _arb_cache["opportunities"] = opportunities
    _arb_cache["ts"] = now
    return opportunities

def format_arbitrage(opps: list) -> str:
    if not opps:
        return "Arbitrage: aucune opportunité"
    lines = ["⚡ Arbitrage:"]
    for o in opps[:2]:
        coin = o["symbol"].replace("USDT","")
        lines.append(f"  💰 {coin}: {o['spread_pct']:.3f}% → ~{o['profit_est']:.3f}% net")
    return "\n".join(lines)

def get_options_data() -> dict:
    now = time.time()
    if now - _options_cache["ts"] < 300:
        return _options_cache["data"] or {}
    try:
        r = requests.get(
            "https://deribit.com/api/v2/public/get_book_summary_by_currency",
            params={"currency":"BTC","kind":"option"},
            timeout=10, headers={"User-Agent":"Mozilla/5.0"}
        )
        if r.status_code == 200:
            instruments = r.json().get("result",[])
            calls = sum(1 for i in instruments if "-C" in i.get("instrument_name",""))
            puts  = sum(1 for i in instruments if "-P" in i.get("instrument_name",""))
            pcr   = round(puts/calls,2) if calls > 0 else 1.0
            result = {
                "put_call_ratio": pcr, "calls": calls, "puts": puts,
                "sentiment": "bearish" if pcr>1.2 else "bullish" if pcr<0.7 else "neutral"
            }
            _options_cache["data"] = result
            _options_cache["ts"] = now
            return result
    except Exception as e:
        print(f"[OPT] {e}")
    return {}

def format_options(data: dict) -> str:
    if not data:
        return "Options: N/A"
    pcr  = data.get("put_call_ratio","N/A")
    sent = data.get("sentiment","neutral")
    e    = "🐻" if sent=="bearish" else "🐂" if sent=="bullish" else "➡️"
    return f"📊 Options P/C={pcr} {e} ({sent})"

def get_fear_greed() -> str:
    val = get_fear_greed_value()
    try:
        d = requests.get("https://api.alternative.me/fng/", timeout=5).json()["data"][0]
        return f"Fear&Greed: {d['value']}/100 ({d['value_classification']})"
    except Exception:
        return f"Fear&Greed: {val}/100"

def get_yahoo_price(ticker: str) -> float:
    now = time.time()
    if ticker in _yahoo_cache:
        ts, p = _yahoo_cache[ticker]
        if now-ts < 30:
            return p
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d"
        r   = requests.get(url, timeout=8, headers={"User-Agent":"Mozilla/5.0"})
        p   = float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])
        _yahoo_cache[ticker] = (now, p)
        return p
    except Exception:
        return _yahoo_cache.get(ticker,(0,0.0))[1]

def get_yahoo_closes(ticker: str, interval="5m", range_="1d") -> pd.Series:
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval={interval}&range={range_}"
        r   = requests.get(url, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        return pd.Series([c for c in closes if c is not None], dtype=float)
    except Exception:
        return pd.Series(dtype=float)

def scan_yahoo_market(market_dict: dict, market_name: str) -> list:
    opps = []
    for ticker, name in market_dict.items():
        try:
            closes = get_yahoo_closes(ticker,"5m","1d")
            if len(closes) < 27: continue
            ind = compute_indicators(closes)
            if not ind: continue
            price = get_yahoo_price(ticker)
            if not price: continue
            score = 0
            if ind["rsi"] < 35: score += 3
            elif ind["rsi"] < 45: score += 1
            if ind["rsi"] > 70: score -= 3
            if ind["macd_h"] > 0: score += 2
            else: score -= 1
            if ind["mom5"] > 0.5: score += 2
            elif ind["mom5"] < -0.5: score -= 2
            if abs(score) >= 2:
                opps.append({
                    "symbol":ticker,"name":name,"market_type":market_name,
                    "price":price,"score":score,
                    "direction":"BUY" if score>0 else "SELL",
                    "ind":ind,"patterns":[],"has_alert":False
                })
        except Exception as e:
            print(f"[YAHOO] {ticker}: {e}")
    opps.sort(key=lambda x: abs(x["score"]), reverse=True)
    return opps[:3]

# ═══════════════════════════════════════════════════════════════
#  INDICATEURS TECHNIQUES
# ═══════════════════════════════════════════════════════════════
def compute_indicators(closes: pd.Series) -> dict:
    if len(closes) < 27:
        return {}
    try:
        delta = closes.diff()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)
        rs    = (gain.ewm(com=13,adjust=False).mean() /
                 loss.ewm(com=13,adjust=False).mean().replace(0,np.nan))
        rsi   = float((100-100/(1+rs)).iloc[-1])
        ema9  = float(closes.ewm(span=9, adjust=False).mean().iloc[-1])
        ema20 = float(closes.ewm(span=20,adjust=False).mean().iloc[-1])
        ema50 = float(closes.ewm(span=50,adjust=False).mean().iloc[-1])
        macd_line   = closes.ewm(span=12,adjust=False).mean() - closes.ewm(span=26,adjust=False).mean()
        macd_signal = macd_line.ewm(span=9,adjust=False).mean()
        macd_l = float(macd_line.iloc[-1])
        macd_s = float(macd_signal.iloc[-1])
        macd_h = round(macd_l-macd_s, 6)
        sma20  = closes.rolling(20).mean()
        std20  = closes.rolling(20).std()
        bb_up  = float((sma20+2*std20).iloc[-1])
        bb_lo  = float((sma20-2*std20).iloc[-1])
        bb_pct = round((float(closes.iloc[-1])-bb_lo)/(bb_up-bb_lo)*100,1) if bb_up!=bb_lo else 50.0
        mom5   = float((closes.iloc[-1]-closes.iloc[-6])/closes.iloc[-6]*100)  if len(closes)>=6  else 0.0
        mom15  = float((closes.iloc[-1]-closes.iloc[-16])/closes.iloc[-16]*100) if len(closes)>=16 else 0.0
        vol    = float(closes.pct_change().dropna().iloc[-10:].std()*100) if len(closes)>=10 else 0.0
        return {
            "rsi":round(rsi,1),"ema9":round(ema9,6),"ema20":round(ema20,6),
            "ema50":round(ema50,6),"macd_h":macd_h,"bb_pct":bb_pct,
            "mom5":round(mom5,3),"mom15":round(mom15,3),"vol":round(vol,3),
            "trend":"↑" if ema20>ema50 else "↓",
            "ema_cross":"BULL" if ema9>ema20 else "BEAR",
            "price":float(closes.iloc[-1])
        }
    except Exception as e:
        print(f"[IND] {e}")
        return {}

def get_multi_tf(symbol: str) -> dict:
    result = {}
    for interval, label in [("1","1m"),("5","5m"),("15","15m")]:
        closes = get_klines(symbol, interval, 80)
        if not closes.empty:
            ind = compute_indicators(closes)
            if ind:
                result[label] = ind
    return result

def tf_score(mtf: dict) -> dict:
    score = 0; sigs = []
    for tf, ind in mtf.items():
        rsi   = ind.get("rsi",50)
        macd  = ind.get("macd_h",0)
        mom5  = ind.get("mom5",0)
        cross = ind.get("ema_cross","BEAR")
        if rsi < 32:   score += 2; sigs.append(f"{tf}:RSI_survente")
        elif rsi < 45: score += 1
        elif rsi > 68: score -= 2; sigs.append(f"{tf}:RSI_surachat")
        elif rsi > 55: score -= 1
        if macd > 0:  score += 1; sigs.append(f"{tf}:MACD↑")
        else:         score -= 1
        if mom5 > 0.5:  score += 1
        elif mom5 < -0.5: score -= 1
        if cross == "BULL": score += 1; sigs.append(f"{tf}:EMA_bull")
        else:               score -= 1
    direction = "LONG" if score>=4 else "SHORT" if score<=-4 else "NEUTRE"
    return {"score":score,"direction":direction,"signals":sigs[:6]}

def detect_patterns(symbol: str, ind: dict, vols: list) -> list:
    patterns = []
    try:
        rsi      = ind.get("rsi",50)
        mom5     = ind.get("mom5",0)
        mom15    = ind.get("mom15",0)
        bb_pct   = ind.get("bb_pct",50)
        macd_h   = ind.get("macd_h",0)
        ema_cross= ind.get("ema_cross","BEAR")
        avg_vol   = sum(vols[:-1])/max(len(vols)-1,1) if vols else 0
        last_vol  = vols[-1] if vols else 0
        vol_ratio = last_vol/avg_vol if avg_vol > 0 else 1
        if rsi<28 and bb_pct<5:
            patterns.append({"name":"Survente extrême","signal":"BUY","strength":"fort","score":3})
        elif rsi>72 and bb_pct>95:
            patterns.append({"name":"Surachat extrême","signal":"SELL","strength":"fort","score":3})
        if mom5>1.2 and vol_ratio>2.0 and macd_h>0:
            patterns.append({"name":"Breakout haussier","signal":"BUY","strength":"fort","score":3})
        elif mom5<-1.2 and vol_ratio>2.0 and macd_h<0:
            patterns.append({"name":"Breakdown baissier","signal":"SELL","strength":"fort","score":3})
        if ema_cross=="BULL" and macd_h>0 and rsi<60:
            patterns.append({"name":"EMA Cross Bull","signal":"BUY","strength":"modéré","score":2})
        elif ema_cross=="BEAR" and macd_h<0 and rsi>40:
            patterns.append({"name":"EMA Cross Bear","signal":"SELL","strength":"modéré","score":2})
        if mom5>0.8 and mom15>2.0:
            patterns.append({"name":"Momentum haussier","signal":"BUY","strength":"modéré","score":2})
        elif mom5<-0.8 and mom15<-2.0:
            patterns.append({"name":"Momentum baissier","signal":"SELL","strength":"modéré","score":2})
        if abs(mom5)>4 and vol_ratio>5:
            patterns.append({"name":"⚠️ Pump/Dump","signal":"HOLD","strength":"ALERTE"})
    except Exception as e:
        print(f"[PAT] {e}")
    return patterns

def scan_market() -> list:
    opps = []; prices = get_prices_batch()
    for symbol in ALL_SYMBOLS:
        if is_blacklisted(symbol): continue
        try:
            price = prices.get(symbol,0)
            if not price: continue
            closes = get_klines_5m_cached(symbol)
            if len(closes) < 27: continue
            ind = compute_indicators(closes)
            if not ind: continue
            vols = get_volume_data(symbol,"5",15)
            pats = detect_patterns(symbol,ind,vols)
            score = 0
            if ind["rsi"]<35: score+=3
            elif ind["rsi"]<45: score+=1
            if ind["rsi"]>70: score-=3
            elif ind["rsi"]>60: score-=1
            if ind["macd_h"]>0: score+=2
            else: score-=1
            if ind["mom5"]>1: score+=2
            elif ind["mom5"]<-1: score-=2
            if ind["ema_cross"]=="BULL": score+=1
            else: score-=1
            score += get_symbol_confidence_bonus(symbol) // 5
            has_alert = any(p["signal"]=="HOLD" for p in pats)
            opps.append({
                "symbol":symbol,"price":price,"score":score,
                "direction":"BUY" if score>0 else "SELL",
                "ind":ind,"patterns":pats,"has_alert":has_alert
            })
        except Exception:
            pass
    opps.sort(key=lambda x: abs(x["score"]), reverse=True)
    return opps[:10]

# ═══════════════════════════════════════════════════════════════
#  ANALYSE COMPLÈTE
# ═══════════════════════════════════════════════════════════════
def analyze(opp: dict, fear_greed: str) -> dict:
    symbol = opp["symbol"]; price = opp["price"]
    ind = opp["ind"]; pats = opp["patterns"]
    mtf = get_multi_tf(symbol); conf = tf_score(mtf)
    ob  = get_order_book(symbol)
    in_pos    = any(p["symbol"]==symbol for p in sim["positions"].values())
    best_p    = db_best_patterns(symbol)
    worst_p   = db_worst_patterns(symbol)
    liq_data  = get_liquidations()
    onchain   = get_onchain_data()
    whales    = get_whale_alerts()
    arb_opps  = detect_arbitrage()
    options   = get_options_data()
    poly_mkts = get_polymarket_markets()
    kelly_pct = dynamic_position_size(70,"SPOT",symbol)
    trader_sigs = get_db_trader_signals_summary()
    my_rules  = get_active_rules()
    macro     = get_macro_trend()
    oi_str    = format_open_interest(symbol)
    vp        = get_volume_profile(symbol)
    vp_str    = ""
    if vp:
        if vp.get("near_support"):
            vp_str = f"📍 Proche support ${vp['support']:.4f}"
        elif vp.get("near_resistance"):
            vp_str = f"📍 Proche résistance ${vp['resistance']:.4f}"
    sym_bonus  = get_symbol_confidence_bonus(symbol)
    fg_value   = get_fear_greed_value()
    fg_context = ""
    if fg_value < 20:   fg_context = "⚠️ EXTREME FEAR → Opportunité"
    elif fg_value < 35: fg_context = "Fear élevé → potentielle opportunité"
    ws_status = "WS✅" if _ws_connected else "REST"
    prompt = f"""{symbol} ${price:.4f} [{ws_status}]
RSI:{ind.get('rsi','?')} MACD:{ind.get('macd_h','?')} mom5:{ind.get('mom5','?')}% trend:{ind.get('trend','?')}
OB:{ob['pressure']} TFscore:{conf['score']}/9
Fear&Greed:{fg_value}/100 {fg_context}
Macro:{macro} | Sym bonus:{sym_bonus:+d}
{interpret_liquidations(liq_data)}
{format_onchain(onchain)}
{format_whale_alerts(whales)}
{format_arbitrage(arb_opps)}
{format_options(options)}
{oi_str}
{vp_str}
{format_polymarket(poly_mkts)}
Traders:{trader_sigs[:150]}
Gains:{best_p[:2]} Erreurs:{worst_p[:2]}
{my_rules}
Kelly:{kelly_pct*100:.1f}% En pos:{'OUI' if in_pos else 'NON'}
JSON:{{"signal":"BUY/SELL/HOLD","confidence":0-100,"reason":"raison","risk":"LOW/MEDIUM/HIGH","market":"SPOT/FUTURES"}}"""
    result = vote(prompt)
    if sym_bonus != 0 and result.get("signal") != "HOLD":
        result["confidence"] = max(0, min(100, result.get("confidence",0) + sym_bonus))
    result.update({
        "symbol":symbol,"price":price,"patterns":pats,
        "confluence":conf,"ob":ob,"ind":ind,"kelly_pct":kelly_pct
    })
    return result

# ═══════════════════════════════════════════════════════════════
#  GESTION DES POSITIONS — VERSION SILENCIEUSE
# ═══════════════════════════════════════════════════════════════
def get_equity() -> float:
    return get_equity_safe()

def get_stats() -> dict:
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    if not closed:
        return {"total":0,"wins":0,"losses":0,"win_rate":0,"best":0,"worst":0,"total_pnl":0,"avg_dur":0}
    pnls = [t["pnl"] for t in closed]
    wins = [p for p in pnls if p>0]
    durs = [t.get("duration_min",0) for t in closed if t.get("duration_min")]
    return {
        "total":len(closed),"wins":len(wins),"losses":len(closed)-len(wins),
        "win_rate":round(len(wins)/len(closed)*100,1),
        "best":round(max(pnls),4),"worst":round(min(pnls),4),
        "total_pnl":round(sum(pnls),4),
        "avg_dur":round(sum(durs)/len(durs),1) if durs else 0
    }

def can_open_trade(symbol: str, market: str, send_fn) -> bool:
    if not check_risk_limits(send_fn): return False
    if not validate_symbol(symbol): return False
    if is_blacklisted(symbol): return False
    if is_correlated(symbol): return False
    if market not in ("MEME","MICRO") and is_fg_neutral(): return False
    return True

def open_trade(analysis: dict, send_fn) -> dict | None:
    symbol = analysis["symbol"]; price = analysis["price"]
    signal = analysis["signal"]; conf  = analysis["confidence"]
    reason = sanitize_string(analysis["reason"])
    market = analysis.get("market","SPOT")
    pats   = analysis.get("patterns",[])
    side   = "LONG" if signal=="BUY" else "SHORT"

    if signal == "SELL" and market == "SPOT":
        return None

    if not can_open_trade(symbol, market, send_fn):
        return None

    confidence = get_symbol_confidence(symbol)

    if confidence < 0.4:
        print(f"[FILTER] {symbol} ignoré (confidence {confidence:.2f})")
        return None

    if symbol in memory.get("recent_losses", []):
        print(f"[FILTER] {symbol} évité (pertes récentes)")
        return None

    if any(p["symbol"] == symbol for p in sim["positions"].values()):
        return None

    if len(sim["positions"]) >= MAX_POSITIONS:
        return None

    if sim["cash"] < 20:
        return None

    if not validate_amount(price, 0.000001, 1_000_000):
        return None

    kelly_pct = analysis.get("kelly_pct") or dynamic_position_size(conf, market, symbol)
    if analysis.get("_forced_pct"):
        kelly_pct = analysis["_forced_pct"]

    leverage  = LEVERAGE_SIM if market=="FUTURES" else 1
    amount    = sim["cash"] * kelly_pct
    qty       = amount / price

    sim["cash"] -= amount

    trade = {
        "id":len(sim["trades"])+1,"symbol":symbol,"market":market,"side":side,
        "price_in":price,"price_out":None,"qty":qty,"amount_usd":amount,
        "confidence":conf,"reason":reason,"exit_reason":None,
        "time_in":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "time_out":None,"pnl":None,"pnl_pct":None,"duration_min":None,
        "patterns":[p["name"] for p in pats if p.get("signal")!="HOLD"],
        "leverage":leverage,"peak_price":price,"trough_price":price,"kelly_pct":kelly_pct
    }

    pos_key = f"{market}_{symbol}_{side}_{trade['id']}"

    sim["trades"].append(trade)
    sim["positions"][pos_key] = {**trade,"pos_key":pos_key}

    db_save_trade(trade)
    save_data()

    bot_state["trades_today"] += 1

    sl = price*(1-STOP_LOSS_PCT) if side=="LONG" else price*(1+STOP_LOSS_PCT)
    tp = price*(1+TAKE_PROFIT_PCT) if side=="LONG" else price*(1-TAKE_PROFIT_PCT)

    coin  = symbol.replace("USDT","")
    mtype = analysis.get("market_type",market)
    name  = analysis.get("name",coin)

    asset_label = f"{name} ({mtype})" if mtype in ("STOCK","FOREX","COMMODITY") else f"{coin} (Crypto)"

    learning = "🎓" if analysis.get("_forced_pct") else ""

    macro  = bot_state.get("macro_trend","NEUTRAL")
    macro_e = "🐂" if macro=="BULL" else "🐻" if macro=="BEAR" else "➡️"

    # Silence : on n'envoie plus de message à chaque trade
    # On envoie seulement si confiance très haute
    if conf >= 90:
        send_fn(
            f"{'🟢' if side=='LONG' else '🔴'} {learning} {asset_label}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Prix     : ${price:.4f}\n"
            f"💰 Mise     : ${amount:.2f} (Kelly {kelly_pct*100:.1f}%)\n"
            f"🛑 SL       : ${sl:.4f}\n"
            f"🎯 TP       : ${tp:.4f}\n"
            f"🧠 Raison   : {reason[:100]}\n"
            f"🔒 Confiance: {conf}% | #{trade['id']}\n"
            f"📊 Macro    : {macro_e} {macro}"
        )

    return trade

def close_trade(pos_key: str, price: float, reason: str, send_fn) -> dict | None:
    pos = sim["positions"].pop(pos_key, None)
    if not pos: return None
    side  = pos["side"]; entry = pos["price_in"]
    amt   = pos["amount_usd"]; lev = pos.get("leverage",1)
    if side == "LONG":
        pnl     = (price-entry)/entry*amt*lev
        pnl_pct = (price-entry)/entry*100*lev
    else:
        pnl     = (entry-price)/entry*amt*lev
        pnl_pct = (entry-price)/entry*100*lev
    sim["cash"] += amt + pnl
    duration = 0
    try:
        t_in = datetime.strptime(pos["time_in"],"%Y-%m-%d %H:%M:%S")
        duration = int((datetime.now()-t_in).total_seconds()/60)
    except Exception:
        pass
    trade = next((t for t in reversed(sim["trades"]) if t["id"]==pos["id"]), None)
    if trade:
        trade.update({
            "price_out":price,
            "time_out":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pnl":round(pnl,4),"pnl_pct":round(pnl_pct,2),
            "exit_reason":reason,"duration_min":duration
        })
        db_save_trade(trade); learn_from_trade(trade,send_fn=send_fn)
    won = pnl > 0
    if won: memory["total_wins"]   = memory.get("total_wins",0)+1
    else:   memory["total_losses"] = memory.get("total_losses",0)+1
    update_symbol_score(pos["symbol"], won)
    update_blacklist(pos["symbol"], won)
    save_data()
    equity_now = get_equity_safe()
    pnl_total  = equity_now - sim["initial"]
    coin = pos["symbol"].replace("USDT","")
    chg  = (price-entry)/entry*100

    # Silence : on n'envoie plus de message à chaque fermeture
    # On envoie seulement si le PnL est important (>1% du capital)
    if abs(pnl) > CAPITAL_INITIAL * 0.01:
        send_fn(
            f"{'✅' if pnl>0 else '❌'} {coin} fermé — #{pos['id']}\n"
            f"  ${entry:.4f}→${price:.4f} ({chg:+.2f}%)\n"
            f"  {'🤑' if pnl>0 else '💸'} ${pnl:+.4f} | {reason}\n"
            f"  Capital: ${equity_now:.2f} (total: ${pnl_total:+.2f})"
        )
    return trade

def monitor_positions(send_fn):
    if not sim["positions"]: return
    prices = get_prices_batch()
    for pos_key, pos in list(sim["positions"].items()):
        if pos.get("trade_type") in ("MICRO","MEME"): continue
        symbol = pos["symbol"]; side = pos["side"]
        entry  = pos["price_in"]; lev = pos.get("leverage",1)
        price  = prices.get(symbol) or get_price(symbol)
        if not price: continue
        if side == "LONG":
            pos["peak_price"] = max(pos.get("peak_price",entry), price)
            change   = (price-entry)/entry
            trailing = (pos["peak_price"]-price)/pos["peak_price"]
        else:
            pos["trough_price"] = min(pos.get("trough_price",entry), price)
            change   = (entry-price)/entry
            trailing = (price-pos["trough_price"])/pos["trough_price"]
        reason = None
        if change*lev <= -STOP_LOSS_PCT:    reason = f"🛑 SL ({change*100*lev:+.2f}%)"
        elif change*lev >= TAKE_PROFIT_PCT: reason = f"🎯 TP ({change*100*lev:+.2f}%)"
        elif change*lev > 0.008 and trailing >= TRAILING_PCT: reason = f"📐 TRAIL ({trailing*100:.2f}%)"
        if reason: close_trade(pos_key, price, reason, send_fn)

# ═══════════════════════════════════════════════════════════════
#  MICRO-TRADING (inchangé)
# ═══════════════════════════════════════════════════════════════
# ... tout ton code micro_signal, open_micro_trade, monitor_micro_positions, run_micro_cycle reste identique

# ═══════════════════════════════════════════════════════════════
#  MEMECOINS (inchangé)
# ═══════════════════════════════════════════════════════════════
# ... tout ton code dex_get_pair, dex_get_trending, meme_signal_score, _open_meme_trade, _monitor_meme_positions, run_meme_cycle reste identique

# ═══════════════════════════════════════════════════════════════
#  SURVEILLANCE TRADERS (inchangé)
# ═══════════════════════════════════════════════════════════════
# ... tout ton code scrape_nitter, scrape_youtube_titles, analyze_signal_sentiment, get_trader_intelligence reste identique

# ═══════════════════════════════════════════════════════════════
#  ÉPARGNE (inchangé)
# ═══════════════════════════════════════════════════════════════
# ... tout ton code scan_airdrops, scan_faucets, scan_promo_codes, auto_fill_form, run_epargne_scan, get_epargne_info reste identique

# ═══════════════════════════════════════════════════════════════
#  BASE DE DONNÉES (inchangé)
# ═══════════════════════════════════════════════════════════════
def init_db():
    con = sqlite3.connect(DB_FILE); c = con.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS trades(
        id INTEGER PRIMARY KEY, symbol TEXT, market TEXT, side TEXT,
        price_in REAL, price_out REAL, qty REAL, amount_usd REAL,
        pnl REAL, pnl_pct REAL, confidence INTEGER, reason TEXT,
        exit_reason TEXT, duration_min INTEGER, time_in TEXT, time_out TEXT,
        patterns TEXT, leverage INTEGER, kelly_pct REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS lessons(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER, symbol TEXT, market TEXT, pnl REAL,
        lecon TEXT, pattern TEXT, action_future TEXT, type TEXT, date TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS equity(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, equity REAL, cash REAL,
        open_positions INTEGER, daily_pnl REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS trading_rules(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule TEXT, condition TEXT, action TEXT, win_rate REAL,
        sample_size INTEGER, created_date TEXT, last_updated TEXT,
        active INTEGER DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS trader_signals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT, author TEXT, content TEXT, sentiment TEXT,
        symbol TEXT, strength INTEGER, timestamp TEXT, url TEXT, hash TEXT UNIQUE)""")
    c.execute("""CREATE TABLE IF NOT EXISTS arbitrage_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, symbol TEXT, exchange1 TEXT, price1 REAL,
        exchange2 TEXT, price2 REAL, spread_pct REAL, profit_est REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS epargne_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, type TEXT, name TEXT, url TEXT,
        amount REAL, status TEXT, notes TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS backtest_results(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, symbol TEXT, period TEXT,
        total_trades INTEGER, win_rate REAL, total_pnl REAL,
        sharpe REAL, max_drawdown REAL, params TEXT)""")
    con.commit(); con.close()

def db_save_trade(t: dict):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT OR REPLACE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            t["id"],t["symbol"],t["market"],t["side"],
            t["price_in"],t.get("price_out"),t["qty"],t["amount_usd"],
            t.get("pnl"),t.get("pnl_pct"),t["confidence"],t["reason"],
            t.get("exit_reason"),t.get("duration_min"),
            t["time_in"],t.get("time_out"),
            json.dumps(t.get("patterns",[])),t.get("leverage",1),t.get("kelly_pct",0),
        ))
        con.commit(); con.close()
    except Exception as e:
        print(f"[DB] {e}")

def db_save_lesson(l: dict):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT INTO lessons (trade_id,symbol,market,pnl,lecon,pattern,action_future,type,date)
            VALUES(?,?,?,?,?,?,?,?,?)""", (
            l.get("trade_id"),l.get("symbol"),l.get("market","SPOT"),l.get("pnl"),
            l.get("lecon"),l.get("pattern"),l.get("action_future"),l.get("type"),l.get("date"),
        ))
        con.commit(); con.close()
    except Exception as e:
        print(f"[DB-L] {e}")

def db_save_equity(equity, cash, open_pos, daily_pnl):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT INTO equity (timestamp,equity,cash,open_positions,daily_pnl)
            VALUES(?,?,?,?,?)""", (
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            round(equity,2),round(cash,2),open_pos,round(daily_pnl,2),
        ))
        con.commit(); con.close()
    except Exception:
        pass

def db_win_rate(n=30) -> float:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute(
            "SELECT pnl FROM trades WHERE pnl IS NOT NULL ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        con.close()
        if not rows: return 50.0
        return round(sum(1 for r in rows if r[0]>0)/len(rows)*100, 1)
    except Exception:
        return 50.0

def db_symbol_stats() -> list:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute("""
            SELECT symbol,COUNT(*) n,AVG(pnl) avg_pnl,
                   SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)*100.0/COUNT(*) wr
            FROM trades WHERE pnl IS NOT NULL
            GROUP BY symbol ORDER BY avg_pnl DESC LIMIT 5""").fetchall()
        con.close()
        return [{"s":r[0].replace("USDT",""),"n":r[1],"pnl":round(r[2],2),"wr":round(r[3],0)} for r in rows]
    except Exception:
        return []

def db_best_patterns(symbol: str) -> list:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute("""SELECT pattern FROM lessons WHERE symbol=? AND type='succes'
            GROUP BY pattern ORDER BY COUNT(*) DESC LIMIT 5""", (symbol,)).fetchall()
        con.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []

def db_worst_patterns(symbol: str) -> list:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute("""SELECT pattern FROM lessons WHERE symbol=? AND type='erreur'
            GROUP BY pattern ORDER BY COUNT(*) DESC LIMIT 5""", (symbol,)).fetchall()
        con.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []

def get_active_rules() -> str:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute(
            "SELECT rule FROM trading_rules WHERE active=1 ORDER BY win_rate DESC LIMIT 5"
        ).fetchall()
        con.close()
        if not rows: return ""
        return "MES RÈGLES:\n" + "".join(f"• {r[0]}\n" for r in rows)
    except Exception:
        return ""

def get_db_trader_signals_summary() -> str:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute(
            "SELECT author,sentiment,symbol,timestamp FROM trader_signals ORDER BY id DESC LIMIT 8"
        ).fetchall()
        con.close()
        if not rows: return "Aucun signal"
        lines = []
        for r in rows:
            e = "📈" if r[1]=="bullish" else "📉" if r[1]=="bearish" else "➡️"
            lines.append(f"{e} @{r[0]} [{r[2]}] ({r[3][11:16]})")
        return "\n".join(lines)
    except Exception:
        return ""

# ═══════════════════════════════════════════════════════════════
#  BACKTESTING HISTORIQUE (inchangé pour l'instant)
# ═══════════════════════════════════════════════════════════════
def fetch_historical_klines(symbol: str, interval: str, days: int) -> pd.DataFrame:
    try:
        limit  = min(1000, days * {"1m":1440,"5m":288,"15m":96,"1h":24,"4h":6,"1d":1}.get(interval,24))
        r = requests.get(
            f"{BINANCE_BASE}/api/v3/klines",
            params={"symbol":symbol,"interval":interval,"limit":limit},
            timeout=15, headers={"User-Agent":"Mozilla/5.0"}
        )
        if r.status_code == 200:
            data = r.json()
            df = pd.DataFrame(data, columns=[
                "open_time","open","high","low","close","volume",
                "close_time","quote_vol","trades","taker_base","taker_quote","ignore"
            ])
            df["close"]  = df["close"].astype(float)
            df["open"]   = df["open"].astype(float)
            df["high"]   = df["high"].astype(float)
            df["low"]    = df["low"].astype(float)
            df["volume"] = df["volume"].astype(float)
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
            return df
    except Exception as e:
        print(f"[BT-FETCH] {e}")
    return pd.DataFrame()

# (la fonction backtest_strategy améliorée est déjà dans ton fichier, on la garde telle quelle pour l'instant)

# ═══════════════════════════════════════════════════════════════
#  PERSISTANCE JSON + GITHUB
# ═══════════════════════════════════════════════════════════════
def save_data():
    try:
        data = json.dumps({"sim":sim,"memory":memory,"epargne":epargne}, indent=2, default=str)
        DATA_FILE.write_text(data)
        if GITHUB_TOKEN and GITHUB_REPO:
            headers = {"Authorization":f"token {GITHUB_TOKEN}","Content-Type":"application/json"}
            api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/sim_portfolio_v7.json"
            r   = requests.get(api_url, headers=headers, timeout=10)
            sha = r.json().get("sha","") if r.status_code==200 else ""
            payload = {
                "message": "auto: save bot state v7",
                "content": base64.b64encode(data.encode()).decode(),
                "branch":  "main"
            }
            if sha: payload["sha"] = sha
            requests.put(api_url, headers=headers, json=payload, timeout=15)
    except Exception as e:
        print(f"[SAVE] {e}")

def load_data():
    global sim, memory, epargne
    loaded = False
    if GITHUB_TOKEN and GITHUB_REPO:
        try:
            headers = {"Authorization":f"token {GITHUB_TOKEN}"}
            api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/sim_portfolio_v7.json"
            r = requests.get(api_url, headers=headers, timeout=10)
            if r.status_code == 200:
                content = base64.b64decode(r.json()["content"]).decode()
                d = json.loads(content)
                sim = d.get("sim",{})
                memory = d.get("memory",{})
                epargne_loaded = d.get("epargne",{})
                if epargne_loaded: epargne.update(epargne_loaded)
                loaded = True
                print(f"[LOAD-GH] {len(sim.get('trades',[]))} trades | {len(memory.get('lessons',[]))} leçons")
        except Exception as e:
            print(f"[LOAD-GH] {e}")
    if not loaded and DATA_FILE.exists():
        try:
            d = json.loads(DATA_FILE.read_text())
            sim = d.get("sim",{})
            memory = d.get("memory",{})
            epargne_loaded = d.get("epargne",{})
            if epargne_loaded: epargne.update(epargne_loaded)
            loaded = True
            print(f"[LOAD-LOCAL] {len(sim.get('trades',[]))} trades")
        except Exception as e:
            print(f"[LOAD] {e}")
    for k,v in {
        "cash":CAPITAL_INITIAL,"initial":CAPITAL_INITIAL,"positions":{},
        "trades":[],"equity_history":[],"session":1,
        "peak_equity":CAPITAL_INITIAL,"daily_start_equity":CAPITAL_INITIAL,
        "daily_start_date":""
    }.items():
        sim.setdefault(k,v)
    for k,v in {
        "lessons":[],"patterns_to_avoid":[],"patterns_that_work":[],
        "confidence_threshold":CONFIDENCE_BASE,"total_wins":0,"total_losses":0,
        "symbol_scores":{},"symbol_blacklist":{},"consecutive_losses":{}
    }.items():
        memory.setdefault(k,v)

# ═══════════════════════════════════════════════════════════════
#  APPRENTISSAGE (inchangé)
# ═══════════════════════════════════════════════════════════════
def learn_from_trade(trade: dict, send_fn=None):
    if trade.get("pnl") is None:
        return

    try:
        pnl = float(trade.get("pnl", 0))
        pnl_pct = float(trade.get("pnl_pct", 0))
        duration = int(trade.get("duration_min", 0) or 0)
        pattern = ", ".join(trade.get("patterns", [])[:3]) or "aucun_pattern"

        if pnl > 0:
            lesson_type = "succes"
            if duration <= 5:
                lecon = "Scalp rapide gagnant"
                action_future = "Conserver ce setup pour micro-trading"
            elif pnl_pct > 2:
                lecon = "Momentum rentable détecté"
                action_future = "Renforcer la priorité de ce pattern"
            else:
                lecon = "Trade gagnant exploitable"
                action_future = "Rejouer ce setup avec prudence"
        else:
            lesson_type = "erreur"
            if duration <= 5:
                lecon = "Entrée trop agressive"
                action_future = "Exiger plus de confirmation avant entrée"
            elif "SL" in str(trade.get("exit_reason", "")):
                lecon = "Stop touché rapidement"
                action_future = "Réduire taille ou éviter ce pattern"
            else:
                lecon = "Setup peu performant"
                action_future = "Diminuer la priorité de ce setup"

        lesson = {
            "trade_id": trade["id"],
            "pnl": pnl,
            "symbol": trade["symbol"],
            "market": trade.get("market", "SPOT"),
            "lecon": lecon,
            "pattern": pattern,
            "action_future": action_future,
            "type": lesson_type,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M")
        }

        memory["lessons"].append(lesson)
        db_save_lesson(lesson)

        key = "patterns_that_work" if lesson_type == "succes" else "patterns_to_avoid"
        memory[key].append(pattern)

        memory["lessons"] = memory["lessons"][-MAX_LESSONS:]
        memory["patterns_that_work"] = memory["patterns_that_work"][-100:]
        memory["patterns_to_avoid"] = memory["patterns_to_avoid"][-100:]

        update_symbol_score(trade["symbol"], pnl > 0)
        auto_adjust()
        save_data()

        print(f"[LEARN] {lesson['lecon']}")

        if send_fn and abs(pnl) > CAPITAL_INITIAL * 0.01:  # silence sauf gros move
            stats = get_stats()
            e = "✅" if lesson["type"] == "succes" else "❌"
            coin = trade["symbol"].replace("USDT", "")
            send_fn(
                f"📚 Leçon #{len(memory['lessons'])} — {coin}\n"
                f"{e} {lesson['lecon']}\n→ {lesson['action_future']}\n"
                f"📊 WR:{stats['win_rate']}% ({stats['wins']}✅/{stats['losses']}❌)"
            )

    except Exception as e:
        print(f"[LEARN] {e}")

def auto_adjust():
    wr  = db_win_rate(20)
    cur = memory.get("confidence_threshold",CONFIDENCE_BASE)
    if wr > 62 and cur > CONFIDENCE_MIN:
        memory["confidence_threshold"] = max(CONFIDENCE_MIN,cur-2)
    elif wr < 40 and cur < CONFIDENCE_MAX:
        memory["confidence_threshold"] = min(CONFIDENCE_MAX,cur+3)

def auto_adjust_sl_tp():
    global STOP_LOSS_PCT, TAKE_PROFIT_PCT
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    if len(closed) < 15: return
    recent  = closed[-15:]
    sl_hits = sum(1 for t in recent if "STOP-LOSS" in (t.get("exit_reason","") or ""))
    if sl_hits/len(recent) > 0.5 and STOP_LOSS_PCT < 0.04:
        STOP_LOSS_PCT = round(min(0.04, STOP_LOSS_PCT+0.003), 3)

def generate_trading_rules():
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    if len(closed) < 10 or len(closed)%10 != 0: return None
    try:
        recent = closed[-20:]; wins = [t for t in recent if t["pnl"]>0]
        kelly_vals = [t.get("kelly_pct",0)*100 for t in recent if t.get("kelly_pct")]
        avg_kelly  = round(sum(kelly_vals)/len(kelly_vals),1) if kelly_vals else 0
        prompt = f"""Analyse {len(recent)} trades. WR:{len(wins)}/{len(recent)}
Conf gagnants:{round(sum(t['confidence'] for t in wins)/max(len(wins),1),1)}%
Kelly:{avg_kelly}% SL={STOP_LOSS_PCT*100:.1f}% TP={TAKE_PROFIT_PCT*100:.1f}%
JSON:{{"rules":["règle1","règle2","règle3"],"insight":"insight"}}"""
        r = ask_ai(prompt)
        rules = r.get("rules",[])
        for rule in rules:
            try:
                con = sqlite3.connect(DB_FILE)
                con.execute("""INSERT INTO trading_rules
                    (rule,condition,action,win_rate,sample_size,created_date,last_updated)
                    VALUES(?,?,?,?,?,?,?)""",
                    (rule,"auto","appliquer",len(wins)/len(recent)*100,len(recent),
                     datetime.now().strftime("%Y-%m-%d"),
                     datetime.now().strftime("%Y-%m-%d %H:%M")))
                con.commit(); con.close()
            except Exception:
                pass
        return r
    except Exception as e:
        print(f"[RULES] {e}")
        return None

def test_strategy_variation(send_fn):
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    if len(closed) < 20: return
    current_wr = db_win_rate(20)
    strategies = [
        {"name":"conservateur","sl":0.02,"tp":0.03,"conf":75},
        {"name":"équilibré",   "sl":0.025,"tp":0.04,"conf":65},
        {"name":"agressif",    "sl":0.035,"tp":0.06,"conf":55},
    ]
    recent = closed[-20:]; best_wr = 0; best_strat = None
    for strat in strategies:
        sw = sum(1 for t in recent if t.get("pnl_pct",0) >= strat["tp"]*100)
        wr = sw/len(recent)*100
        if wr > best_wr:
            best_wr = wr; best_strat = strat
    if best_strat and best_wr > current_wr+5:
        global STOP_LOSS_PCT, TAKE_PROFIT_PCT
        STOP_LOSS_PCT  = best_strat["sl"]
        TAKE_PROFIT_PCT= best_strat["tp"]
        memory["confidence_threshold"] = best_strat["conf"]
        send_fn(f"🧬 ÉVOLUTION: {best_strat['name']}\nWR:{best_wr:.0f}% vs {current_wr:.0f}%")

# ═══════════════════════════════════════════════════════════════
#  BOUCLE PRINCIPALE — AJOUT DU RÉSUMÉ SILENCIEUX
# ═══════════════════════════════════════════════════════════════
def trading_loop(send_fn):
    kelly_init = kelly_criterion()
    gh_status  = "✅" if GITHUB_TOKEN else "❌ Non configuré"
    ws_status  = "✅ WebSocket" if WS_AVAILABLE else "⚠️ REST fallback"

    hf_enabled = any(p["name"] == "huggingface" for p in AI_PROVIDERS)
    hf_status  = "✅" if hf_enabled and HF_KEY else "❌ Désactivé"

    equity     = get_equity_safe()
    sim["peak_equity"]        = equity
    sim["daily_start_equity"] = equity
    sim["daily_start_date"]   = datetime.utcnow().strftime("%Y-%m-%d")

    send_fn(
        f"🚀 BOT v7.1 DÉMARRÉ (Silencieux + Agent)\n━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Capital     : ${CAPITAL_INITIAL:,.2f} (virtuel)\n"
        f"📐 Kelly init  : {kelly_init*100:.1f}% / trade\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 Groq        : ✅ (priorité)\n"
        f"🤗 HuggingFace : {hf_status}\n"
        f"💾 GitHub sync : {gh_status}\n"
        f"📡 Data source : {ws_status}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️  Stop jour.  : -{MAX_DAILY_LOSS_PCT*100:.0f}%\n"
        f"📉 Drawdown max: -{MAX_DRAWDOWN_PCT*100:.0f}%\n"
        f"🌙 Mode nuit   : Actif (2h-6h UTC réduit)\n"
        f"🔗 Corrélation : Protection activée\n"
        f"━━━━━━━━━━━━━\n"
        f"🎯 Polymarket  : actif\n"
        f"⚡ Arbitrage   : Binance/KuCoin\n"
        f"🐸 Memecoins   : DexScreener actif\n"
        f"💰 Épargne     : scan toutes les heures\n"
        f"📊 Backtest    : /backtest disponible\n"
        f"🧠 Agent       : /agent disponible"
    )
    fear_greed = get_fear_greed()

    while bot_state["running"]:
        now = time.time()
        check_daily_reset()

        if now - bot_state.get("last_micro",0) >= CYCLE_MICRO:
            try: monitor_micro_positions(send_fn); run_micro_cycle(send_fn)
            except Exception as e: print(f"[MICRO] {e}")
            bot_state["last_micro"] = now

        if now - bot_state.get("last_meme",0) >= CYCLE_MEME:
            try: run_meme_cycle(send_fn)
            except Exception as e: print(f"[MEME] {e}")
            bot_state["last_meme"] = now

        if now - bot_state["last_monitor"] >= CYCLE_MONITOR:
            try: monitor_positions(send_fn)
            except Exception as e: print(f"[MON] {e}")
            bot_state["last_monitor"] = now

        if now - bot_state["last_scalp"] >= CYCLE_SCALP:
            bot_state["cycle_count"] += 1
            try:
                fear_greed = get_fear_greed()
                threshold  = memory.get("confidence_threshold", CONFIDENCE_BASE)
                macro      = get_macro_trend()

                arb_opps = detect_arbitrage()
                for opp in arb_opps:
                    if opp["profit_est"] > 0.05:
                        coin = opp["symbol"].replace("USDT","")
                        send_fn(f"⚡ ARBITRAGE {coin}\n  Spread:{opp['spread_pct']:.3f}% → ~{opp['profit_est']:.3f}% net")

                poly_mkts = get_polymarket_markets()
                if poly_mkts and poly_mkts[0]["inefficiency"] > 2:
                    best = poly_mkts[0]
                    send_fn(f"🎯 Polymarket\n  {best['question']}\n  YES:{best['yes_price']:.2f} NO:{best['no_price']:.2f}")

                if not bot_state.get("daily_stopped"):
                    opps = scan_market()
                    if opps:
                        top_opps = [o for o in opps[:5] if abs(o["score"]) >= 4]
                        if top_opps and _can_call_ai():
                            for opp in top_opps[:2]:
                                if not bot_state["running"]: break
                                if opp["has_alert"]: continue
                                if macro == "BEAR" and opp["direction"] == "BUY":
                                    if opp["ind"].get("rsi",50) > 30: continue
                                result = analyze(opp, fear_greed)
                                signal = result["signal"]; conf = result["confidence"]
                                risk   = result["risk"]
                                in_pos = any(p["symbol"]==opp["symbol"] for p in sim["positions"].values())
                                if signal == "HOLD" or in_pos: continue
                                if conf >= threshold and risk in ("LOW","MEDIUM"):
                                    open_trade(result, send_fn)
                                elif LEARN_MODE_ENABLED and conf >= LEARN_MODE_CONF_MIN:
                                    result["_forced_pct"] = LEARN_MODE_MAX_PCT
                                    open_trade(result, send_fn)
                        elif not _can_call_ai():
                            for opp in opps[:3]:
                                if abs(opp["score"]) < 5 or opp["has_alert"]: continue
                                in_pos = any(p["symbol"]==opp["symbol"] for p in sim["positions"].values())
                                if not in_pos and len(sim["positions"]) < MAX_POSITIONS:
                                    fake = {
                                        "signal":"BUY" if opp["score"]>0 else "SELL",
                                        "confidence":min(80,50+abs(opp["score"])*5),
                                        "reason":f"Algo pur score={opp['score']}",
                                        "risk":"MEDIUM","market":"SPOT",
                                        "symbol":opp["symbol"],"price":opp["price"],
                                        "patterns":opp["patterns"],"ind":opp["ind"],
                                        "kelly_pct":kelly_criterion()
                                    }
                                    open_trade(fake, send_fn)

                    for market_dict, market_name in [
                        (STOCKS_SYMBOLS,"STOCK"),
                        (FOREX_SYMBOLS,"FOREX"),
                        (COMMODITY_SYMBOLS,"COMMODITY")
                    ]:
                        try:
                            yahoo_opps = scan_yahoo_market(market_dict, market_name)
                            for o in yahoo_opps[:1]:
                                in_pos = any(p["symbol"]==o["symbol"] for p in sim["positions"].values())
                                if in_pos or len(sim["positions"]) >= MAX_POSITIONS: continue
                                if not _can_call_ai(): continue
                                prompt = (
                                    f"{o['name']} ({o['symbol']}) ${o['price']:.4f}\n"
                                    f"RSI:{o['ind'].get('rsi','?')} mom5:{o['ind'].get('mom5','?')}% "
                                    f"score:{o['score']:+d} {fear_greed}\n"
                                    f"JSON:{{\"signal\":\"{o['direction']}/HOLD\","
                                    f"\"confidence\":0-100,\"reason\":\"raison\","
                                    f"\"risk\":\"LOW/MEDIUM/HIGH\",\"market\":\"SPOT\"}}"
                                )
                                result = vote(prompt)
                                result.update({
                                    "symbol":o["symbol"],"price":o["price"],"patterns":[],
                                    "market":"SPOT","name":o["name"],"market_type":market_name,
                                    "kelly_pct":kelly_criterion()
                                })
                                if (result["signal"] in ("BUY","SELL") and
                                        result["confidence"] >= threshold and
                                        result["risk"] in ("LOW","MEDIUM")):
                                    open_trade(result, send_fn)
                        except Exception as e:
                            print(f"[YAHOO] {e}")

            except Exception as e:
                print(f"[SCALP] {e}")
            bot_state["last_scalp"] = now

        if now - bot_state["last_deep"] >= CYCLE_DEEP:
            try:
                fear_greed = get_fear_greed()
                thresh     = memory.get("confidence_threshold", CONFIDENCE_BASE)
                onchain    = get_onchain_data(); options = get_options_data()
                liq        = get_liquidations(); whales = get_whale_alerts()
                macro      = get_macro_trend(); fg_val = get_fear_greed_value()
                macro_e    = "🐂" if macro=="BULL" else "🐻" if macro=="BEAR" else "➡️"
                send_fn("🔬 Analyse profonde\n" + "\n".join([
                    format_onchain(onchain), format_options(options),
                    interpret_liquidations(liq), format_whale_alerts(whales),
                    f"📊 Macro: {macro_e} {macro} | F&G:{fg_val}/100",
                    f"📡 WS: {'✅ Connecté' if _ws_connected else '⚠️ REST mode'}"
                ]))
                for symbol in ["BTCUSDT","ETHUSDT","SOLUSDT"]:
                    try:
                        mtf  = get_multi_tf(symbol); conf = tf_score(mtf)
                        if abs(conf["score"]) < 5: continue
                        price  = get_price(symbol); ind5m = mtf.get("5m",{})
                        ob     = get_order_book(symbol)
                        in_pos = any(p["symbol"]==symbol for p in sim["positions"].values())
                        if in_pos or not _can_call_ai(): continue
                        if bot_state.get("daily_stopped"): continue
                        kelly_pct = dynamic_position_size(70,"FUTURES",symbol)
                        direction = "BUY" if conf["direction"]=="LONG" else "SELL"
                        if macro == "BEAR" and direction == "BUY" and ind5m.get("rsi",50) > 35: continue
                        if macro == "BULL" and direction == "SELL" and ind5m.get("rsi",50) < 65: continue
                        prompt = (
                            f"{symbol} FUTURES x{LEVERAGE_SIM} ${price:.2f}\n"
                            f"TF:{conf['score']}/9→{conf['direction']} RSI:{ind5m.get('rsi','?')} OB:{ob['pressure']}\n"
                            f"{fear_greed} Macro:{macro}\nKelly:{kelly_pct*100:.1f}%\n"
                            f"JSON:{{\"signal\":\"{direction}/HOLD\","
                            f"\"confidence\":0-100,\"reason\":\"raison\","
                            f"\"risk\":\"LOW/MEDIUM/HIGH\",\"market\":\"FUTURES\"}}"
                        )
                        result = vote(prompt)
                        result.update({"symbol":symbol,"price":price,"patterns":[],
                                       "market":"FUTURES","kelly_pct":kelly_pct})
                        if (result["signal"] in ("BUY","SELL") and
                                result["confidence"] >= thresh and
                                result["risk"] in ("LOW","MEDIUM")):
                            open_trade(result, send_fn)
                    except Exception as e:
                        print(f"[DEEP] {symbol}: {e}")
                threading.Thread(target=get_trader_intelligence, daemon=True).start()
                auto_adjust_sl_tp()
                rules = generate_trading_rules()
                if rules:
                    send_fn("🧠 Règles auto\n" + "\n".join(f"• {r}" for r in rules.get("rules",[])[:3]))
                closed_n = len([t for t in sim["trades"] if t.get("pnl")])
                if closed_n >= 20 and closed_n%50 == 0:
                    test_strategy_variation(send_fn)
            except Exception as e:
                print(f"[DEEP] {e}")
            bot_state["last_deep"] = now

        if now - bot_state.get("last_epargne",0) >= CYCLE_EPARGNE:
            try: run_epargne_scan(send_fn)
            except Exception as e: print(f"[EPARGNE] {e}")
            bot_state["last_epargne"] = now

        if now - bot_state["last_status"] >= CYCLE_STATUS:
            try:
                equity = get_equity_safe(); pnl = equity - sim["initial"]
                stats  = get_stats(); kelly = kelly_criterion()
                sym_s  = db_symbol_stats()
                sym_str = " | ".join(f"{s['s']}:{s['wr']:.0f}%WR" for s in sym_s) or "Aucun"
                micro_c = bot_state.get("micro_count",0); wr_db = db_win_rate(30)
                fg_val  = get_fear_greed_value(); macro = get_macro_trend()
                daily_start = sim.get("daily_start_equity", CAPITAL_INITIAL)
                daily_pnl   = equity - daily_start
                daily_pct   = daily_pnl/daily_start*100 if daily_start>0 else 0
                bl_count    = len(memory.get("symbol_blacklist",{}))
                macro_e     = "🐂" if macro=="BULL" else "🐻" if macro=="BEAR" else "➡️"
                if fg_val < 20:        trader_tip = "💡 Saylor/Buffett : Fear extrême = opportunité"
                elif fg_val < 35:      trader_tip = "💡 Buffett : Sois avide quand les autres ont peur"
                elif fg_val > 75:      trader_tip = "💡 Tudor Jones : Protège le capital"
                elif stats["win_rate"] > 60: trader_tip = "💡 Livermore : Laisse courir les gagnants"
                else:                  trader_tip = "💡 Cathie Wood : Focus momentum"
                pos_lines = ""
                if sim["positions"]:
                    prices = get_prices_batch()
                    for pos in sim["positions"].values():
                        p   = prices.get(pos["symbol"], pos["price_in"])
                        chg = (p-pos["price_in"])/pos["price_in"]*100*pos.get("leverage",1)
                        pos_lines += f"\n  {'📈' if chg>0 else '📉'} {pos['symbol'].replace('USDT',''):6s} {chg:+.2f}%"
                stop_str = "🛑 STOP JOUR ACTIF" if bot_state.get("daily_stopped") else ""
                ws_str   = "📡 WS✅" if _ws_connected else "📡 REST"
                send_fn(
                    f"📊 BILAN v7 — {datetime.now().strftime('%H:%M')}\n━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 Capital  : ${equity:.2f} ({pnl/sim['initial']*100:+.1f}%)\n"
                    f"📅 Auj.     : ${daily_pnl:+.2f} ({daily_pct:+.1f}%)\n"
                    f"📍 Positions: {len(sim['positions'])}/{MAX_POSITIONS}{pos_lines}\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"🏆 WR       : {stats['win_rate']}% ({stats['wins']}✅/{stats['losses']}❌)\n"
                    f"📐 Kelly    : {kelly*100:.1f}% / trade\n"
                    f"⚡ Micro    : {micro_c} trades\n"
                    f"📊 WR DB(30): {wr_db}%\n"
                    f"🧠 AI Pool  : {_pool_stats['last_provider']} ({_pool_stats['total_calls']} appels | cache:{_pool_stats['cache_hits']})\n"
                    f"📊 Macro    : {macro_e} {macro} | F&G:{fg_val}/100\n"
                    f"🚫 Blacklist: {bl_count} symbols\n"
                    f"🥇 Top      : {sym_str}\n"
                    f"📚 Leçons   : {len(memory['lessons'])}/{MAX_LESSONS}\n"
                    f"{ws_str}\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"{stop_str}\n{trader_tip}"
                )
                db_save_equity(equity, sim["cash"], len(sim["positions"]), pnl)
                send_summary(send_fn)  # ← résumé silencieux toutes les 15-20 min
            except Exception as e:
                print(f"[STATUS] {e}")
            bot_state["last_status"] = now

        bot_state["last_heartbeat"] = datetime.now()
        time.sleep(3)

# ═══════════════════════════════════════════════════════════════
#  WATCHDOG + RÉSUMÉ JOURNALIER (inchangé)
# ═══════════════════════════════════════════════════════════════
def watchdog(send_fn):
    time.sleep(180); alerted = False
    while True:
        time.sleep(60)
        if not bot_state["running"]: alerted = False; continue
        last = bot_state.get("last_heartbeat")
        if not last: continue
        elapsed = (datetime.now()-last).total_seconds()
        if elapsed > 300 and not alerted:
            send_fn(f"⚠️ WATCHDOG: Inactif {int(elapsed//60)} min")
            alerted = True
        elif elapsed <= 300:
            alerted = False

def daily_summary(send_fn):
    while True:
        now = datetime.now()
        midnight = (now+timedelta(days=1)).replace(hour=0,minute=0,second=5,microsecond=0)
        time.sleep((midnight-now).total_seconds())
        try:
            equity = get_equity_safe(); pnl = equity - sim["initial"]
            stats  = get_stats(); today = now.strftime("%Y-%m-%d")
            t_day  = [t for t in sim["trades"] if t.get("time_in","").startswith(today)]
            pnl_day= sum(t["pnl"] for t in t_day if t.get("pnl"))
            sym_s  = db_symbol_stats()
            best3  = "\n".join(
                f"  🏅 {s['s']}: WR {s['wr']:.0f}% ({s['n']} trades)"
                for s in sym_s[:3]
            ) or "  Aucun"
            lessons = "\n".join(
                f"  {'✅' if l['type']=='succes' else '❌'} {l['lecon']}"
                for l in memory["lessons"][-3:]
            ) or "  Aucune"
            bl_count = len(memory.get("symbol_blacklist",{}))
            send_fn(
                f"📊 RÉSUMÉ JOURNALIER v7 — {now.strftime('%d/%m/%Y')}\n━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Capital  : ${equity:.2f} ({pnl/sim['initial']*100:+.1f}%)\n"
                f"📅 PnL jour : ${pnl_day:+.2f} ({len(t_day)} trades)\n"
                f"📐 Kelly    : {kelly_criterion()*100:.1f}%\n"
                f"🚫 Blacklist: {bl_count} symbols\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🏆 WR       : {stats['win_rate']}% ({stats['total']} trades)\n"
                f"📚 Leçons   : {len(memory['lessons'])}/{MAX_LESSONS}\n━━━━━━━━━━━━━━━━━━━\n"
                f"🥇 Top coins:\n{best3}\n💡 Leçons récentes:\n{lessons}"
            )
            bot_state["daily_stopped"]    = False
            sim["daily_start_equity"]     = equity
            sim["daily_start_date"]       = (now+timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception as e:
            print(f"[DAILY] {e}")

def self_ping():
    time.sleep(60)
    while True:
        try: requests.get(f"{WEBHOOK_URL.rstrip('/')}/health", timeout=10)
        except Exception: pass
        time.sleep(270)

# ═══════════════════════════════════════════════════════════════
#  DASHBOARD HTML (inchangé)
# ═══════════════════════════════════════════════════════════════
def generate_dashboard() -> str:
    # ton code original complet pour le dashboard reste identique
    # (je ne le recopie pas ici pour ne pas faire 400 lignes, mais il est gardé tel quel dans ton fichier)
    pass  # ← remplace par ton code original de generate_dashboard

# ═══════════════════════════════════════════════════════════════
#  SERVEUR HTTP + WEBHOOK (inchangé)
# ═══════════════════════════════════════════════════════════════
class BotHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-type","text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(200)
            self.send_header("Content-type","text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(generate_dashboard().encode("utf-8"))

    def do_POST(self):
        if self.path != WEBHOOK_PATH:
            self.send_response(404); self.end_headers(); return
        n    = int(self.headers.get("Content-Length",0))
        body = self.rfile.read(n)
        if _app and _main_loop:
            asyncio.run_coroutine_threadsafe(_process_update(body), _main_loop)
        self.send_response(200); self.end_headers()

    def log_message(self, fmt, *args):
        pass

async def _process_update(body: bytes):
    try:
        update = Update.de_json(json.loads(body), _app.bot)
        await _app.process_update(update)
    except Exception as e:
        print(f"[WH] {e}")

def run_server():
    HTTPServer(("0.0.0.0", WEBHOOK_PORT), BotHandler).serve_forever()

# ═══════════════════════════════════════════════════════════════
#  TELEGRAM COMMANDS — AJOUT DE /resume et /agent
# ═══════════════════════════════════════════════════════════════
def make_send(chat_id: str):
    def send(msg: str):
        if _app is None or _main_loop is None:
            print(f"[MSG] {msg[:80]}")
            return
        f = asyncio.run_coroutine_threadsafe(
            _app.bot.send_message(chat_id=chat_id, text=msg), _main_loop
        )
        try: f.result(timeout=15)
        except Exception as e: print(f"[MSG] {e}")
    return send

def _auth(update: Update) -> bool:
    chat_id  = str(update.effective_chat.id)
    expected = str(TELEGRAM_CHAT_ID)
    return secure_compare(chat_id, expected)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    if not check_rate_limit(f"cmd_{update.effective_chat.id}", 10, 60):
        await update.message.reply_text("⚠️ Trop de commandes, attends un peu.")
        return
    if bot_state["running"]:
        await update.message.reply_text("Déjà en cours !")
        return
    bot_state.update({
        "running":True,"trades_today":0,"cycle_count":0,
        "last_heartbeat":None,"last_monitor":0,"last_micro":0,
        "last_scalp":0,"last_deep":0,"last_status":0,
        "last_meme":0,"last_epargne":0,"daily_stopped":False
    })
    send = make_send(TELEGRAM_CHAT_ID)
    threading.Thread(target=trading_loop,  args=(send,), daemon=True).start()
    threading.Thread(target=watchdog,      args=(send,), daemon=True).start()
    threading.Thread(target=daily_summary, args=(send,), daemon=True).start()

async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    bot_state["running"] = False
    equity = get_equity_safe(); stats = get_stats()
    await update.message.reply_text(
        f"🛑 Arrêté.\nCapital:${equity:.2f} | PnL:${equity-sim['initial']:+.2f}\n"
        f"Trades:{stats['total']} | WR:{stats['win_rate']}%"
    )

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    equity = get_equity_safe(); pnl = equity - sim["initial"]
    stats  = get_stats(); kelly = kelly_criterion()
    thresh = memory.get("confidence_threshold",CONFIDENCE_BASE)
    macro  = get_macro_trend(); fg_val = get_fear_greed_value()
    macro_e = "🐂" if macro=="BULL" else "🐻" if macro=="BEAR" else "➡️"
    daily_start = sim.get("daily_start_equity", CAPITAL_INITIAL)
    daily_pnl   = equity - daily_start
    stop_str    = "\n🛑 STOP JOURNALIER ACTIF" if bot_state.get("daily_stopped") else ""
    ws_str      = "📡 WS✅" if _ws_connected else "📡 REST"
    pos_lines   = ""
    if sim["positions"]:
        prices = get_prices_batch()
        for pos in sim["positions"].values():
            p   = prices.get(pos["symbol"], pos["price_in"])
            chg = (p-pos["price_in"])/pos["price_in"]*100*pos.get("leverage",1)
            pos_lines += f"\n  {'📈' if chg>0 else '📉'} {pos['symbol'].replace('USDT',''):6s} {chg:+.2f}%"
    await update.message.reply_text(
        f"{'🟢' if bot_state['running'] else '🔴'} {'EN MARCHE' if bot_state['running'] else 'ARRÊTÉ'}\n"
        f"💰 Capital  : ${equity:.2f} ({pnl:+.2f})\n"
        f"📅 Auj.     : ${daily_pnl:+.2f}\n"
        f"📐 Kelly    : {kelly*100:.1f}%\n"
        f"🏆 WR       : {stats['win_rate']}% ({stats['total']} trades)\n"
        f"📍 Positions: {len(sim['positions'])}{pos_lines}\n"
        f"📊 Macro    : {macro_e} {macro} | F&G:{fg_val}/100\n"
        f"{ws_str}{stop_str}"
    )

# Ajout du résumé silencieux
last_summary = 0
def send_summary(send_fn):
    global last_summary
    if time.time() - last_summary < 1200:  # toutes les 20 minutes
        return
    equity = get_equity_safe()
    pnl = equity - sim["initial"]
    pos = len(sim["positions"])
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    wr = round(len([t for t in closed if t["pnl"] > 0]) / max(len(closed),1) * 100,1) if closed else 0
    send_fn(f"📊 Résumé v7.1 — ${equity:.0f} ({pnl:+.0f}) | Pos: {pos} | WR: {wr}% | FG: {get_fear_greed_value()}")
    last_summary = time.time()

async def cmd_resume(update: Update, ctx):
    if not _auth(update): return
    send = make_send(TELEGRAM_CHAT_ID)
    send_summary(send)

# Agent Conscience
async def cmd_agent(update: Update, ctx):
    if not _auth(update): return
    query = ' '.join(ctx.args) if ctx.args else update.message.text.replace("/agent", "").strip()
    if not query:
        await update.message.reply_text("Pose ta question à l'Agent Conscience :\n/agent analyse mon winrate\n/agent modifie le Kelly\n/agent montre le code de open_trade")
        return

    state = f"""
Capital: ${get_equity_safe():.2f}
Positions: {len(sim['positions'])}
WR global: {len([t for t in sim['trades'] if t.get('pnl',0)>0])/max(len([t for t in sim['trades'] if t.get('pnl') is not None]),1)*100:.1f}%
Lessons: {len(memory['lessons'])}
FG: {get_fear_greed_value()}/100
Macro: {get_macro_trend()}
Blacklist: {len(memory.get('symbol_blacklist',{}))}
    """

    prompt = f"""Tu es la Conscience du Trading Bot v7.1. Tu as accès à tout l'état et au code.
Réponds en français, clair et précis.
Si tu proposes une modification, donne le code exact à remplacer.

État actuel :
{state}

Question : {query}
"""

    response = ask_ai(prompt)
    await update.message.reply_text(response.get("reason", "Je réfléchis..."))

# ═══════════════════════════════════════════════════════════════
#  APPLICATION TELEGRAM — Ajout des nouvelles commandes
# ═══════════════════════════════════════════════════════════════
async def run_telegram():
    global _app, _main_loop
    _main_loop = asyncio.get_event_loop()
    _app = (ApplicationBuilder()
            .token(TELEGRAM_TOKEN)
            .request(HTTPXRequest(
                connection_pool_size=8,pool_timeout=30.0,
                connect_timeout=30.0,read_timeout=30.0,write_timeout=30.0
            ))
            .updater(None).build())
    for cmd, fn in [
        ("start",cmd_start),("stop",cmd_stop),("status",cmd_status),
        ("scan",cmd_scan),("portfolio",cmd_portfolio),("positions",cmd_positions),
        ("lecons",cmd_lecons),("fermer",cmd_fermer),("reset",cmd_reset),
        ("kelly",cmd_kelly),("arbitrage",cmd_arbitrage),("polymarket",cmd_polymarket),
        ("marches",cmd_marches),("memes",cmd_memes),("signaux",cmd_signaux),
        ("regles",cmd_regles),("stats",cmd_stats),("apprendre",cmd_apprendre),
        ("pool",cmd_pool),("epargne",cmd_epargne),
        ("airdrops",cmd_airdrops),("faucets",cmd_faucets),("help",cmd_help),
        ("macro",cmd_macro),("risque",cmd_risque),("blacklist",cmd_blacklist),
        ("backtest",cmd_backtest),("backtest_multi",cmd_backtest_multi),
        ("resume", cmd_resume),      # ← résumé silencieux
        ("agent", cmd_agent),        # ← Agent Conscience
    ]:
        _app.add_handler(CommandHandler(cmd, fn))
    await _app.initialize()
    await _app.start()
    if WEBHOOK_URL:
        full = WEBHOOK_URL.rstrip("/")+WEBHOOK_PATH
        await asyncio.sleep(2)
        try:
            await _app.bot.set_webhook(url=full,drop_pending_updates=True,allowed_updates=["message"])
        except Exception as e:
            print(f"[WEBHOOK] {e}")
            await asyncio.sleep(5)
            await _app.bot.set_webhook(url=full,drop_pending_updates=True,allowed_updates=["message"])
        print(f"Webhook: {full}")
    print("Bot v7.1 prêt — /start pour lancer | /resume pour résumé | /agent pour parler à la Conscience")
    try:
        while True: await asyncio.sleep(1)
    finally:
        if WEBHOOK_URL: await _app.bot.delete_webhook()
        await _app.stop()
        await _app.shutdown()

# ═══════════════════════════════════════════════════════════════
#  AUTO-START + ENTRYPOINT
# ═══════════════════════════════════════════════════════════════
def auto_start():
    time.sleep(5)
    send = make_send(TELEGRAM_CHAT_ID)
    if bot_state["running"]: return
    bot_state.update({
        "running":True,"trades_today":0,"cycle_count":0,
        "last_heartbeat":None,"last_monitor":0,"last_micro":0,
        "last_scalp":0,"last_deep":0,"last_status":0,
        "last_meme":0,"last_epargne":0,"daily_stopped":False
    })
    kelly = kelly_criterion()
    send(f"🔄 Bot v7.1 redémarré\nKelly:{kelly*100:.1f}% | /stop pour arrêter\n📡 WS: {'✅' if _ws_connected else '⚠️ REST'}")
    threading.Thread(target=trading_loop,  args=(send,), daemon=True).start()
    threading.Thread(target=watchdog,      args=(send,), daemon=True).start()
    threading.Thread(target=daily_summary, args=(send,), daemon=True).start()

if __name__ == "__main__":
    print("🚀 Trading Bot v7.1 — Silence + Agent Conscience")

    init_db()
    load_data()

    start_websocket()

    threading.Thread(target=run_server, daemon=True).start()
    threading.Thread(target=self_ping, daemon=True).start()

    asyncio.run(run_telegram())
