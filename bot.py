"""
Trading Bot v3 — Architecture continue
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Plateformes : Bybit Testnet (paper) + Bybit Live (real)
Types       : Spot + Futures (long/short) selon signal
Cycle       : Continu — 3 niveaux de fréquence
  • 30s  → surveillance positions (SL/TP/trailing)
  • 2min → scalping rapide sur signaux forts
  • 5min → analyse complète multi-TF + vote IA

Pour démarrer le testnet :
  1. Créer compte sur testnet.bybit.com
  2. Générer clés API testnet
  3. Ajouter variables Koyeb :
       BYBIT_TESTNET_KEY    = ta_clé_testnet
       BYBIT_TESTNET_SECRET = ton_secret_testnet
"""

import os, time, threading, feedparser, requests, asyncio
import json, sqlite3, math
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from collections import Counter, deque

from groq import Groq
from pybit.unified_trading import HTTP
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════
GROQ_KEY          = os.environ.get("ANTHROPIC_KEY")
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID")
WEBHOOK_URL       = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_PATH      = "/webhook"
WEBHOOK_PORT      = 8000

# Bybit Live (réel — positions simulées en interne, API pour les prix)
BYBIT_LIVE_KEY    = os.environ.get("BINANCE_KEY", "")
BYBIT_LIVE_SECRET = os.environ.get("BINANCE_SECRET", "")

# Bybit Testnet (paper trading officiel)
# → Créer compte sur testnet.bybit.com + générer clés API
BYBIT_TEST_KEY    = os.environ.get("BYBIT_TESTNET_KEY", "")
BYBIT_TEST_SECRET = os.environ.get("BYBIT_TESTNET_SECRET", "")
TESTNET_ENABLED   = bool(BYBIT_TEST_KEY and BYBIT_TEST_SECRET)

# ── Paramètres trading ─────────────────────────────────────────
CONFIDENCE_BASE   = 68
CONFIDENCE_MIN    = 58
CONFIDENCE_MAX    = 85
STOP_LOSS_PCT     = 0.025    # -2.5% (court terme = SL plus serré)
TAKE_PROFIT_PCT   = 0.04     # +4%
TRAILING_STOP_PCT = 0.015    # trailing stop à -1.5% du max atteint
MAX_POSITIONS     = 5        # max positions simultanées
MAX_PCT_PER_TRADE = 0.20     # max 20% du capital par trade
FUTURES_LEVERAGE  = 3        # levier x3 sur futures

# ── Fréquences des cycles (secondes) ──────────────────────────
CYCLE_MONITOR     = 30       # surveillance SL/TP
CYCLE_SCALP       = 120      # analyse rapide scalping
CYCLE_DEEP        = 300      # analyse complète 5min

# ── Univers de trading ─────────────────────────────────────────
SPOT_SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "DOGEUSDT","ADAUSDT","AVAXUSDT","MATICUSDT","LINKUSDT",
    "DOTUSDT","UNIUSDT","ATOMUSDT","LTCUSDT","NEARUSDT",
]
FUTURES_SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "DOGEUSDT","ADAUSDT","AVAXUSDT",
]

DB_FILE   = "memory_v3.db"
DATA_FILE = Path("portfolio_v3.json")

AI_MODELS = [
    "llama-3.3-70b-versatile",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]

# ═══════════════════════════════════════════════════════════════
#  CLIENTS BYBIT
# ═══════════════════════════════════════════════════════════════
groq_client  = Groq(api_key=GROQ_KEY)
bybit_live   = HTTP(api_key=BYBIT_LIVE_KEY,   api_secret=BYBIT_LIVE_SECRET)
bybit_test   = (HTTP(api_key=BYBIT_TEST_KEY,   api_secret=BYBIT_TEST_SECRET,
                     testnet=True)
                if TESTNET_ENABLED else None)

def bybit_client(testnet: bool = False):
    return bybit_test if (testnet and bybit_test) else bybit_live

# ═══════════════════════════════════════════════════════════════
#  ÉTAT GLOBAL
# ═══════════════════════════════════════════════════════════════
# Deux portefeuilles indépendants : paper (testnet) + sim (interne)
DEFAULT_PORTFOLIO = {
    "cash": 10000.0, "initial": 10000.0,
    "positions": {},   # key = "SPOT_BTCUSDT" ou "FUT_BTCUSDT_LONG"
    "trades": [],
    "mode": "SIM",     # SIM | PAPER (testnet) | LIVE
}
DEFAULT_MEMORY = {
    "lessons": [], "patterns_to_avoid": [], "patterns_that_work": [],
    "analysis_history": [], "confidence_threshold": CONFIDENCE_BASE,
    "scalp_wins": 0, "scalp_losses": 0,
}

portfolio: dict = {}
memory: dict    = {}

bot_state = {
    "running": False,
    "last_monitor": None,
    "last_scalp":   None,
    "last_deep":    None,
    "cycle_count":  0,
    "trades_today": 0,
    "alerts": deque(maxlen=50),   # file des dernières alertes
}

_main_loop = None
_app       = None

# ═══════════════════════════════════════════════════════════════
#  SQLITE — MÉMOIRE LONGUE TERME
# ═══════════════════════════════════════════════════════════════
def init_db():
    con = sqlite3.connect(DB_FILE)
    c   = con.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS trades(
        id INTEGER PRIMARY KEY, symbol TEXT, market TEXT, side TEXT,
        price_in REAL, price_out REAL, qty REAL, amount_usd REAL,
        pnl REAL, pnl_pct REAL, confidence INTEGER,
        reason TEXT, exit_reason TEXT, duration_min INTEGER,
        time_in TEXT, time_out TEXT, patterns TEXT, leverage INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS lessons(
        id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id INTEGER,
        symbol TEXT, market TEXT, pnl REAL, lecon TEXT, pattern TEXT,
        action_future TEXT, type TEXT, date TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS signals(
        id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, market TEXT,
        signal TEXT, confidence INTEGER, votes TEXT, confluence INTEGER,
        timestamp TEXT
    )""")
    con.commit(); con.close()


def db_save_trade(t: dict):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT OR REPLACE INTO trades VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            t["id"], t.get("symbol"), t.get("market","SPOT"),
            t.get("side","LONG"),
            t["price_in"], t.get("price_out"),
            t["qty"], t["amount_usd"],
            t.get("pnl"), t.get("pnl_pct"),
            t["confidence"], t["reason"], t.get("exit_reason"),
            t.get("duration_min"),
            t["time_in"], t.get("time_out"),
            json.dumps(t.get("patterns", [])),
            t.get("leverage", 1),
        ))
        con.commit(); con.close()
    except Exception as e:
        print(f"[DB] {e}")


def db_save_lesson(l: dict):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT INTO lessons
            (trade_id,symbol,market,pnl,lecon,pattern,action_future,type,date)
            VALUES(?,?,?,?,?,?,?,?,?)""", (
            l.get("trade_id"), l.get("symbol"), l.get("market","SPOT"),
            l.get("pnl"), l.get("lecon"), l.get("pattern"),
            l.get("action_future"), l.get("type"), l.get("date"),
        ))
        con.commit(); con.close()
    except Exception as e:
        print(f"[DB] lesson {e}")


def db_log_signal(symbol, market, signal, confidence, votes, confluence):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT INTO signals
            (symbol,market,signal,confidence,votes,confluence,timestamp)
            VALUES(?,?,?,?,?,?,?)""", (
            symbol, market, signal, confidence,
            "/".join(votes), confluence,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))
        con.commit(); con.close()
    except Exception:
        pass


def db_win_rate(n=30) -> float:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute(
            "SELECT pnl FROM trades WHERE pnl IS NOT NULL ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall(); con.close()
        if not rows: return 50.0
        return round(sum(1 for r in rows if r[0] > 0) / len(rows) * 100, 1)
    except Exception:
        return 50.0


def db_best_symbols(limit=5) -> list:
    """Symboles avec le meilleur PnL moyen."""
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute("""
            SELECT symbol, COUNT(*) as n, AVG(pnl) as avg_pnl, AVG(pnl_pct) as avg_pct
            FROM trades WHERE pnl IS NOT NULL
            GROUP BY symbol ORDER BY avg_pnl DESC LIMIT ?
        """, (limit,)).fetchall(); con.close()
        return [{"symbol": r[0], "n": r[1],
                 "avg_pnl": round(r[2],2), "avg_pct": round(r[3] or 0, 2)}
                for r in rows]
    except Exception:
        return []


def db_get_patterns(symbol, type_="succes", limit=5) -> list:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute("""
            SELECT pattern, COUNT(*), AVG(pnl) FROM lessons
            WHERE symbol=? AND type=? GROUP BY pattern
            ORDER BY AVG(pnl) DESC LIMIT ?
        """, (symbol, type_, limit)).fetchall(); con.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
#  PERSISTANCE JSON
# ═══════════════════════════════════════════════════════════════
def save_data():
    try:
        DATA_FILE.write_text(
            json.dumps({"portfolio": portfolio, "memory": memory},
                       indent=2, default=str))
    except Exception as e:
        print(f"[SAVE] {e}")


def load_data():
    global portfolio, memory
    if DATA_FILE.exists():
        try:
            d         = json.loads(DATA_FILE.read_text())
            portfolio = d.get("portfolio", {})
            memory    = d.get("memory", {})
            for k, v in DEFAULT_PORTFOLIO.items():
                portfolio.setdefault(k, v)
            for k, v in DEFAULT_MEMORY.items():
                memory.setdefault(k, v)
            n = len(portfolio["trades"])
            print(f"[LOAD] {n} trades | {len(memory['lessons'])} leçons")
            return
        except Exception as e:
            print(f"[LOAD] {e}")
    portfolio = {k: (v.copy() if isinstance(v,(dict,list)) else v)
                 for k,v in DEFAULT_PORTFOLIO.items()}
    memory    = {k: (v.copy() if isinstance(v,(dict,list)) else v)
                 for k,v in DEFAULT_MEMORY.items()}
    print("[LOAD] Nouveau portefeuille $10,000")


# ═══════════════════════════════════════════════════════════════
#  PRIX EN TEMPS RÉEL
# ═══════════════════════════════════════════════════════════════
_price_cache: dict = {}   # cache 10s pour éviter trop d'appels API

def get_price(symbol: str, testnet=False, force=False) -> float:
    now = time.time()
    if not force and symbol in _price_cache:
        ts, price = _price_cache[symbol]
        if now - ts < 10:
            return price
    try:
        r = bybit_client(testnet).get_tickers(
            category="spot", symbol=symbol)
        price = float(r["result"]["list"][0]["lastPrice"])
        _price_cache[symbol] = (now, price)
        return price
    except Exception:
        return _price_cache.get(symbol, (0, 0))[1]


def get_prices_batch(symbols: list) -> dict:
    """Récupère tous les prix spot en un seul appel."""
    prices = {}
    try:
        r = bybit_live.get_tickers(category="spot")
        for item in r["result"]["list"]:
            if item["symbol"] in symbols:
                prices[item["symbol"]] = float(item["lastPrice"])
                _price_cache[item["symbol"]] = (time.time(), prices[item["symbol"]])
    except Exception as e:
        print(f"[PRICE] {e}")
    return prices


def get_futures_price(symbol: str) -> float:
    try:
        r = bybit_live.get_tickers(category="linear", symbol=symbol)
        return float(r["result"]["list"][0]["lastPrice"])
    except Exception:
        return get_price(symbol)


def get_portfolio_value() -> float:
    total = portfolio["cash"]
    prices = get_prices_batch(SPOT_SYMBOLS)
    for pos_key, pos in portfolio["positions"].items():
        sym   = pos["symbol"]
        price = prices.get(sym) or get_price(sym)
        if pos["side"] == "LONG":
            total += pos["qty"] * price * pos.get("leverage", 1)
        else:  # SHORT
            entry  = pos["price_in"]
            change = (entry - price) / entry
            total += pos["amount_usd"] * (1 + change * pos.get("leverage", 1))
    return total


def get_stats() -> dict:
    closed = [t for t in portfolio["trades"] if t.get("pnl") is not None]
    if not closed:
        return {"total":0,"wins":0,"losses":0,"win_rate":0,
                "best":0,"worst":0,"total_pnl":0,"avg_duration":0}
    pnls  = [t["pnl"] for t in closed]
    wins  = [p for p in pnls if p > 0]
    durs  = [t.get("duration_min",0) for t in closed if t.get("duration_min")]
    return {
        "total":        len(closed),
        "wins":         len(wins),
        "losses":       len(closed)-len(wins),
        "win_rate":     round(len(wins)/len(closed)*100, 1),
        "best":         round(max(pnls), 2),
        "worst":        round(min(pnls), 2),
        "total_pnl":    round(sum(pnls), 2),
        "avg_duration": round(sum(durs)/len(durs), 1) if durs else 0,
    }


# ═══════════════════════════════════════════════════════════════
#  INDICATEURS TECHNIQUES
# ═══════════════════════════════════════════════════════════════
def compute_indicators(closes: pd.Series) -> dict:
    if len(closes) < 26:
        return {}
    try:
        # RSI
        delta  = closes.diff()
        gain   = delta.clip(lower=0)
        loss   = (-delta).clip(lower=0)
        rs     = (gain.ewm(com=13,adjust=False).mean() /
                  loss.ewm(com=13,adjust=False).mean().replace(0,np.nan))
        rsi    = float((100 - 100/(1+rs)).iloc[-1])

        # EMA
        ema9   = float(closes.ewm(span=9,  adjust=False).mean().iloc[-1])
        ema20  = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50  = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])

        # MACD
        macd   = float((closes.ewm(span=12,adjust=False).mean() -
                        closes.ewm(span=26,adjust=False).mean()).iloc[-1])
        sig    = float((closes.ewm(span=12,adjust=False).mean() -
                        closes.ewm(span=26,adjust=False).mean())
                       .ewm(span=9,adjust=False).mean().iloc[-1])

        # Bollinger
        sma20  = closes.rolling(20).mean()
        std20  = closes.rolling(20).std()
        bb_up  = float((sma20 + 2*std20).iloc[-1])
        bb_low = float((sma20 - 2*std20).iloc[-1])
        bb_pct = round((float(closes.iloc[-1])-bb_low)/(bb_up-bb_low)*100, 1) \
                 if bb_up != bb_low else 50.0

        # Momentum & volatilité
        returns   = closes.pct_change().dropna()
        vol_1h    = float(returns.iloc[-4:].std() * 100) if len(returns)>=4 else 0
        momentum  = float((closes.iloc[-1]-closes.iloc[-5])/closes.iloc[-5]*100) \
                    if len(closes)>=5 else 0

        return {
            "rsi": round(rsi,1), "ema9": round(ema9,4),
            "ema20": round(ema20,4), "ema50": round(ema50,4),
            "macd": round(macd,4), "macd_sig": round(sig,4),
            "macd_hist": round(macd-sig, 4),
            "bb_upper": round(bb_up,4), "bb_lower": round(bb_low,4),
            "bb_pct": bb_pct,
            "vol_1h": round(vol_1h, 3),
            "momentum": round(momentum, 2),
            "trend": "haussier" if ema20>ema50 else "baissier",
            "price": round(float(closes.iloc[-1]), 6),
        }
    except Exception as e:
        print(f"[IND] {e}")
        return {}


def get_klines(symbol: str, interval: str, limit=100) -> pd.Series:
    """Récupère les klines et retourne une série de closes."""
    try:
        r = bybit_live.get_kline(
            category="spot", symbol=symbol,
            interval=interval, limit=limit)
        closes = pd.Series(
            [float(c[4]) for c in reversed(r["result"]["list"])],
            dtype=float)
        return closes
    except Exception:
        return pd.Series(dtype=float)


def get_volumes(symbol: str, interval="15", limit=20) -> list:
    try:
        r = bybit_live.get_kline(
            category="spot", symbol=symbol,
            interval=interval, limit=limit)
        return [float(c[5]) for c in reversed(r["result"]["list"])]
    except Exception:
        return []


def get_multi_tf(symbol: str) -> dict:
    """Indicateurs sur 3 timeframes : 1min, 5min, 15min (court terme)."""
    result = {}
    for interval, label in [("1","1m"), ("5","5m"), ("15","15m")]:
        closes = get_klines(symbol, interval, 100)
        if not closes.empty:
            result[label] = compute_indicators(closes)
    return result


def tf_confluence(mtf: dict) -> dict:
    score   = 0
    signals = []
    for tf, data in mtf.items():
        if not data:
            continue
        rsi  = data.get("rsi", 50)
        hist = data.get("macd_hist", 0)
        mom  = data.get("momentum", 0)
        if rsi < 35:   score += 1; signals.append(f"{tf}:RSI_survente")
        elif rsi > 70: score -= 1; signals.append(f"{tf}:RSI_surachat")
        if hist > 0:   score += 1; signals.append(f"{tf}:MACD↑")
        else:          score -= 1
        if mom > 0.5:  score += 1; signals.append(f"{tf}:momentum+")
        elif mom < -0.5: score -= 1
    direction = "LONG" if score >= 3 else "SHORT" if score <= -3 else "HOLD"
    return {"score": score, "direction": direction, "signals": signals[:6]}


# ═══════════════════════════════════════════════════════════════
#  DÉTECTION DE PATTERNS RAPIDE (court terme)
# ═══════════════════════════════════════════════════════════════
def detect_patterns_fast(symbol: str) -> list:
    """Patterns rapides sur bougies 1min et 5min."""
    patterns = []
    try:
        closes_1m = get_klines(symbol, "1", 30)
        vols      = get_volumes(symbol, "1", 20)
        if closes_1m.empty or not vols:
            return []

        price     = float(closes_1m.iloc[-1])
        avg_vol   = sum(vols[:-1]) / max(len(vols)-1, 1)
        last_vol  = vols[-1]
        vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1

        # Spike de volume = intérêt soudain
        if vol_ratio > 2.5:
            mom = (price - float(closes_1m.iloc[-4])) / float(closes_1m.iloc[-4]) * 100
            if mom > 0.5:
                patterns.append({"name":"Volume Spike Bullish","signal":"BUY",
                                  "strength":"fort","desc":f"Vol x{vol_ratio:.1f}, +{mom:.2f}%"})
            elif mom < -0.5:
                patterns.append({"name":"Volume Spike Bearish","signal":"SELL",
                                  "strength":"fort","desc":f"Vol x{vol_ratio:.1f}, {mom:.2f}%"})

        # Momentum 5 bougies
        if len(closes_1m) >= 6:
            mom_5 = (float(closes_1m.iloc[-1]) - float(closes_1m.iloc[-6])) \
                    / float(closes_1m.iloc[-6]) * 100
            if mom_5 > 1.5:
                patterns.append({"name":"Momentum Haussier","signal":"BUY",
                                  "strength":"modéré","desc":f"+{mom_5:.2f}% sur 5 bougies"})
            elif mom_5 < -1.5:
                patterns.append({"name":"Momentum Baissier","signal":"SELL",
                                  "strength":"modéré","desc":f"{mom_5:.2f}% sur 5 bougies"})

        # Pump & Dump détection
        pct_2m = (float(closes_1m.iloc[-1]) - float(closes_1m.iloc[-3])) \
                 / float(closes_1m.iloc[-3]) * 100 if len(closes_1m) >= 3 else 0
        if abs(pct_2m) > 3 and vol_ratio > 4:
            patterns.append({"name":"⚠️ Pump/Dump suspect","signal":"HOLD",
                              "strength":"ALERTE",
                              "desc":f"{pct_2m:+.2f}% en 2min, vol x{vol_ratio:.1f}"})

        # EMA cross rapide (9/20 sur 1min)
        ind = compute_indicators(closes_1m)
        if ind:
            ema9  = ind.get("ema9", 0)
            ema20 = ind.get("ema20", 0)
            prev_closes = closes_1m.iloc[:-1]
            prev_ind    = compute_indicators(prev_closes) if len(prev_closes) >= 26 else {}
            if prev_ind:
                prev9  = prev_ind.get("ema9", 0)
                prev20 = prev_ind.get("ema20", 0)
                if prev9 < prev20 and ema9 > ema20:
                    patterns.append({"name":"EMA Cross Bullish (1m)","signal":"BUY",
                                      "strength":"fort","desc":"EMA9 croise EMA20 à la hausse"})
                elif prev9 > prev20 and ema9 < ema20:
                    patterns.append({"name":"EMA Cross Bearish (1m)","signal":"SELL",
                                      "strength":"fort","desc":"EMA9 croise EMA20 à la baisse"})

    except Exception as e:
        print(f"[PAT] {e}")
    return patterns


def get_order_book_imbalance(symbol: str) -> dict:
    """Déséquilibre bid/ask pour détecter la pression acheteur/vendeur."""
    try:
        ob    = bybit_live.get_orderbook(category="spot", symbol=symbol, limit=25)
        bids  = sum(float(b[1]) for b in ob["result"]["b"])
        asks  = sum(float(a[1]) for a in ob["result"]["a"])
        ratio = bids / asks if asks > 0 else 1.0
        return {
            "ratio":   round(ratio, 2),
            "signal":  "BUY" if ratio > 1.4 else "SELL" if ratio < 0.7 else "NEUTRAL",
            "pressure": f"{'acheteur fort' if ratio>1.4 else 'vendeur fort' if ratio<0.7 else 'équilibré'}",
        }
    except Exception:
        return {"ratio": 1.0, "signal": "NEUTRAL", "pressure": "indisponible"}


def get_fear_greed() -> str:
    try:
        d = requests.get("https://api.alternative.me/fng/", timeout=5).json()["data"][0]
        return f"Fear&Greed: {d['value']}/100 ({d['value_classification']})"
    except Exception:
        return "Fear&Greed: N/A"


# ═══════════════════════════════════════════════════════════════
#  SÉLECTION DYNAMIQUE DES MEILLEURES OPPORTUNITÉS
# ═══════════════════════════════════════════════════════════════
def scan_opportunities(mode="scalp") -> list:
    """
    Scanne tous les symboles et retourne les top opportunités.
    Trie par score de momentum + volume + RSI.
    """
    opportunities = []
    prices = get_prices_batch(SPOT_SYMBOLS)

    for symbol in SPOT_SYMBOLS:
        try:
            price  = prices.get(symbol, 0)
            if not price:
                continue
            closes = get_klines(symbol, "5" if mode=="scalp" else "15", 50)
            if len(closes) < 26:
                continue
            ind = compute_indicators(closes)
            if not ind:
                continue

            # Score d'opportunité
            score = 0
            direction = "LONG"

            rsi  = ind.get("rsi", 50)
            hist = ind.get("macd_hist", 0)
            mom  = ind.get("momentum", 0)
            vol  = ind.get("vol_1h", 0)

            # Signaux haussiers
            if rsi < 35:   score += 3
            elif rsi < 45: score += 1
            if hist > 0:   score += 2
            if mom > 1:    score += 2
            elif mom > 0:  score += 1
            if vol > 0.5:  score += 1  # volatilité = opportunité

            # Signaux baissiers
            if rsi > 70:   score -= 3; direction = "SHORT"
            elif rsi > 60: score -= 1
            if hist < 0:   score -= 1
            if mom < -1:   score -= 2; direction = "SHORT"

            opportunities.append({
                "symbol": symbol, "price": price,
                "score": score, "direction": direction,
                "rsi": rsi, "momentum": mom, "vol": vol,
                "indicators": ind,
            })
        except Exception:
            pass

    # Trie par score absolu (prendre les plus forts signaux dans les 2 sens)
    opportunities.sort(key=lambda x: abs(x["score"]), reverse=True)
    return opportunities[:8]   # top 8


# ═══════════════════════════════════════════════════════════════
#  VOTE MAJORITAIRE IA (optimisé pour la rapidité)
# ═══════════════════════════════════════════════════════════════
def ask_model_fast(model: str, prompt: str) -> dict:
    try:
        resp  = groq_client.chat.completions.create(
            model=model, max_tokens=200, temperature=0.2,
            messages=[{"role":"user","content":prompt}],
        )
        text  = resp.choices[0].message.content
        clean = text.replace("```json","").replace("```","").strip()
        return json.loads(clean)
    except Exception:
        return {"signal":"HOLD","confidence":0,"reason":"err","risk":"HIGH"}


def majority_vote_fast(prompt: str) -> dict:
    results = []
    lock    = threading.Lock()

    def worker(m):
        r = ask_model_fast(m, prompt)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker, args=(m,), daemon=True)
               for m in AI_MODELS]
    for t in threads: t.start()
    for t in threads: t.join(timeout=15)

    if not results:
        return {"signal":"HOLD","confidence":0,"reason":"timeout","risk":"HIGH","votes":[]}

    signals    = [r.get("signal","HOLD") for r in results]
    vote_count = Counter(signals)
    winner, n  = vote_count.most_common(1)[0]

    if n < 2:
        return {"signal":"HOLD","confidence":0,
                "reason":f"Pas de consensus ({'/'.join(signals)})",
                "risk":"HIGH","votes":signals}

    concordant = [r for r in results if r.get("signal")==winner]
    avg_conf   = round(sum(r.get("confidence",0) for r in concordant)/len(concordant))
    if n == 3: avg_conf = min(95, avg_conf+5)
    best       = max(concordant, key=lambda r: r.get("confidence",0))
    return {
        "signal":     winner,
        "confidence": avg_conf,
        "reason":     best.get("reason",""),
        "risk":       best.get("risk","MEDIUM"),
        "votes":      signals,
        "consensus":  f"{n}/3",
    }


# ═══════════════════════════════════════════════════════════════
#  ANALYSE RAPIDE (scalping 2min)
# ═══════════════════════════════════════════════════════════════
def analyze_scalp(opp: dict) -> dict:
    """Analyse rapide pour scalping — utilise les données déjà calculées."""
    symbol    = opp["symbol"]
    price     = opp["price"]
    ind       = opp["indicators"]
    direction = opp["direction"]
    patterns  = detect_patterns_fast(symbol)
    ob        = get_order_book_imbalance(symbol)
    in_pos    = any(p["symbol"]==symbol for p in portfolio["positions"].values())
    threshold = memory.get("confidence_threshold", CONFIDENCE_BASE)

    pat_buy  = [p for p in patterns if p["signal"]=="BUY"]
    pat_sell = [p for p in patterns if p["signal"]=="SELL"]
    pat_alert= [p for p in patterns if p["signal"]=="HOLD"]

    best_p  = db_get_patterns(symbol, "succes")
    worst_p = db_get_patterns(symbol, "erreur")

    prompt = f"""Expert scalping crypto ultra court terme. Décide MAINTENANT.

{symbol} | ${price:.4f} | {'EN POSITION' if in_pos else 'PAS EN POSITION'}
RSI: {ind.get('rsi','?')} | MACD hist: {ind.get('macd_hist','?')} | Momentum: {ind.get('momentum','?')}%
BB%: {ind.get('bb_pct','?')} | Volatilité: {ind.get('vol_1h','?')}%
Trend: {ind.get('trend','?')} | OrderBook: {ob['pressure']} (ratio={ob['ratio']})

Patterns BUY: {[p['desc'] for p in pat_buy]}
Patterns SELL: {[p['desc'] for p in pat_sell]}
ALERTES: {[p['desc'] for p in pat_alert]}
Patterns gagnants historiques: {best_p}
Patterns perdants historiques: {worst_p}

Score opportunité: {opp['score']}/9 vers {direction}
Seuil actuel: {threshold}%

Scalping court terme = entrée/sortie rapide, RR minimum 1:1.5
Si en position → cherche sortie. Sinon → cherche entrée si signal fort.

JSON strict (sans backticks):
{{"signal":"BUY ou SELL ou HOLD","confidence":0-100,"reason":"raison courte","risk":"LOW ou MEDIUM ou HIGH","market":"SPOT ou FUTURES"}}"""

    result = majority_vote_fast(prompt)
    result["symbol"]   = symbol
    result["price"]    = price
    result["patterns"] = patterns
    result["ob"]       = ob
    result["indicators"] = ind
    db_log_signal(symbol, result.get("market","SPOT"),
                  result["signal"], result["confidence"],
                  result.get("votes",[]), opp["score"])
    return result


# ═══════════════════════════════════════════════════════════════
#  GESTION DES POSITIONS
# ═══════════════════════════════════════════════════════════════
def position_size(symbol: str, confidence: int, market: str) -> float:
    """Kelly simplifié — retourne % du cash."""
    wr   = db_win_rate(20) / 100
    wr   = max(0.4, wr)
    r    = TAKE_PROFIT_PCT / STOP_LOSS_PCT
    kelly = max(0.05, min(MAX_PCT_PER_TRADE, wr - (1-wr)/r))
    kelly *= (0.6 + 0.4 * (confidence - 60) / 40)
    if market == "FUTURES":
        kelly *= 0.5  # plus prudent sur futures
    return round(min(MAX_PCT_PER_TRADE, max(0.05, kelly)), 2)


def open_position(analysis: dict, send_fn) -> dict | None:
    symbol     = analysis["symbol"]
    price      = analysis["price"]
    signal     = analysis["signal"]
    confidence = analysis["confidence"]
    reason     = analysis["reason"]
    market     = analysis.get("market", "SPOT")
    patterns   = analysis.get("patterns", [])

    # Détermine le sens : BUY=LONG, SELL=SHORT (uniquement futures)
    if signal == "BUY":
        side = "LONG"
    elif signal == "SELL" and market == "FUTURES":
        side = "SHORT"
    else:
        return None  # pas de short sur spot

    pos_key = f"{market}_{symbol}_{side}"
    if pos_key in portfolio["positions"]:
        return None
    if len(portfolio["positions"]) >= MAX_POSITIONS:
        return None
    if portfolio["cash"] < 50:
        return None

    leverage   = FUTURES_LEVERAGE if market == "FUTURES" else 1
    size_pct   = position_size(symbol, confidence, market)
    amount_usd = portfolio["cash"] * size_pct
    qty        = amount_usd / price

    portfolio["cash"] -= amount_usd

    trade = {
        "id":          len(portfolio["trades"]) + 1,
        "symbol":      symbol,
        "market":      market,
        "side":        side,
        "price_in":    price,
        "price_out":   None,
        "qty":         qty,
        "amount_usd":  amount_usd,
        "confidence":  confidence,
        "reason":      reason,
        "exit_reason": None,
        "time_in":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "time_out":    None,
        "pnl":         None,
        "pnl_pct":     None,
        "duration_min": None,
        "patterns":    [p["name"] for p in patterns if p["signal"] != "HOLD"],
        "leverage":    leverage,
        "max_price":   price,   # pour trailing stop
        "min_price":   price,
    }
    portfolio["trades"].append(trade)
    portfolio["positions"][pos_key] = {
        **trade,
        "pos_key": pos_key,
    }
    portfolio["positions"][pos_key]["pos_key"] = pos_key
    db_save_trade(trade)
    save_data()
    bot_state["trades_today"] += 1

    # ── Notification live ─────────────────────────────────────
    sl    = price * (1 - STOP_LOSS_PCT) if side=="LONG" else price * (1 + STOP_LOSS_PCT)
    tp    = price * (1 + TAKE_PROFIT_PCT) if side=="LONG" else price * (1 - TAKE_PROFIT_PCT)
    emoji = "🟢" if side=="LONG" else "🔴"
    m_tag = "📊 FUTURES x3" if market=="FUTURES" else "💱 SPOT"
    send_fn(
        f"{emoji} POSITION OUVERTE #{trade['id']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 {symbol.replace('USDT','')} | {m_tag} | {side}\n"
        f"💵 Entrée  : ${price:.4f}\n"
        f"📦 Quantité: {qty:.6f}\n"
        f"💰 Investi : ${amount_usd:.2f} ({size_pct*100:.0f}% cash)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 Stop-Loss   : ${sl:.4f}\n"
        f"🎯 Take-Profit : ${tp:.4f}\n"
        f"📐 Trailing SL : -{TRAILING_STOP_PCT*100:.1f}% du max\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 Raison  : {reason[:100]}\n"
        f"📊 Patterns: {', '.join(trade['patterns'][:2]) or 'Aucun'}\n"
        f"🔒 Conf.   : {confidence}%"
    )
    return trade


def close_position(pos_key: str, price: float, reason: str, send_fn) -> dict | None:
    pos = portfolio["positions"].pop(pos_key, None)
    if not pos:
        return None

    side   = pos["side"]
    entry  = pos["price_in"]
    qty    = pos["qty"]
    amt    = pos["amount_usd"]
    lev    = pos.get("leverage", 1)

    if side == "LONG":
        pnl     = (price - entry) / entry * amt * lev
        pnl_pct = (price - entry) / entry * 100 * lev
    else:  # SHORT
        pnl     = (entry - price) / entry * amt * lev
        pnl_pct = (entry - price) / entry * 100 * lev

    portfolio["cash"] += amt + pnl

    # Durée
    duration = 0
    try:
        t_in     = datetime.strptime(pos["time_in"], "%Y-%m-%d %H:%M:%S")
        duration = int((datetime.now() - t_in).total_seconds() / 60)
    except Exception:
        pass

    # Mise à jour du trade dans la liste
    trade = next((t for t in reversed(portfolio["trades"])
                  if t["id"] == pos["id"]), None)
    if trade:
        trade.update({
            "price_out":    price,
            "time_out":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pnl":          round(pnl, 4),
            "pnl_pct":      round(pnl_pct, 2),
            "exit_reason":  reason,
            "duration_min": duration,
        })
        db_save_trade(trade)
        learn_from_trade(trade, send_fn=send_fn)

    save_data()

    # ── Notification live ─────────────────────────────────────
    emoji  = "✅" if pnl > 0 else "❌"
    e_cash = "🤑" if pnl > 0 else "💸"
    send_fn(
        f"{emoji} POSITION FERMÉE #{pos['id']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 {pos['symbol'].replace('USDT','')} | {pos['market']} | {side}\n"
        f"💵 Entrée  : ${entry:.4f}\n"
        f"💵 Sortie  : ${price:.4f} ({pnl_pct:+.2f}%)\n"
        f"⏱ Durée   : {duration} min\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{e_cash} PnL       : ${pnl:+.4f}\n"
        f"💰 Cash    : ${portfolio['cash']:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Raison  : {reason}\n"
        f"🆔 Trade #{pos['id']} | ⏳ Analyse..."
    )
    return trade


# ═══════════════════════════════════════════════════════════════
#  SURVEILLANCE DES POSITIONS (30s)
# ═══════════════════════════════════════════════════════════════
def monitor_positions(send_fn):
    """Vérifie SL, TP et trailing stop sur toutes les positions."""
    if not portfolio["positions"]:
        return
    prices = get_prices_batch(SPOT_SYMBOLS + FUTURES_SYMBOLS)

    for pos_key, pos in list(portfolio["positions"].items()):
        symbol = pos["symbol"]
        side   = pos["side"]
        entry  = pos["price_in"]
        lev    = pos.get("leverage", 1)
        price  = prices.get(symbol) or get_futures_price(symbol)
        if not price:
            continue

        change = (price - entry) / entry if side == "LONG" else (entry - price) / entry

        # Mise à jour du max/min pour trailing stop
        if side == "LONG":
            pos["max_price"] = max(pos.get("max_price", entry), price)
            trailing_trigger = (pos["max_price"] - price) / pos["max_price"]
        else:
            pos["min_price"] = min(pos.get("min_price", entry), price)
            trailing_trigger = (price - pos["min_price"]) / pos["min_price"]

        reason = None

        # Stop-Loss
        if change * lev <= -STOP_LOSS_PCT * lev:
            reason = f"🛑 STOP-LOSS ({change*100*lev:+.2f}%)"

        # Take-Profit
        elif change * lev >= TAKE_PROFIT_PCT * lev:
            reason = f"🎯 TAKE-PROFIT ({change*100*lev:+.2f}%)"

        # Trailing Stop
        elif change * lev > 0.01 and trailing_trigger >= TRAILING_STOP_PCT:
            reason = f"📐 TRAILING STOP ({trailing_trigger*100:.2f}% du max)"

        if reason:
            close_position(pos_key, price, reason, send_fn)


# ═══════════════════════════════════════════════════════════════
#  AUTO-AJUSTEMENT DU SEUIL
# ═══════════════════════════════════════════════════════════════
def auto_adjust_threshold():
    wr      = db_win_rate(20)
    current = memory.get("confidence_threshold", CONFIDENCE_BASE)
    if wr > 62 and current > CONFIDENCE_MIN:
        new = max(CONFIDENCE_MIN, current - 2)
    elif wr < 42 and current < CONFIDENCE_MAX:
        new = min(CONFIDENCE_MAX, current + 3)
    else:
        new = current
    memory["confidence_threshold"] = new
    return new


# ═══════════════════════════════════════════════════════════════
#  APPRENTISSAGE
# ═══════════════════════════════════════════════════════════════
def learn_from_trade(trade: dict, send_fn=None):
    if trade.get("pnl") is None:
        return
    try:
        verdict = ("PERDANT — analyse précisément pourquoi."
                   if trade["pnl"] < 0 else "GAGNANT — identifie ce qui a marché.")
        prompt  = f"""Expert scalping crypto. Analyse ce trade court terme.

{trade['symbol']} {trade['market']} {trade['side']}
${trade['price_in']:.4f} → ${trade['price_out']:.4f}
PnL: ${trade['pnl']:+.4f} ({trade.get('pnl_pct',0):+.2f}%)
Durée: {trade.get('duration_min',0)} min
Raison entrée: {trade['reason']}
Raison sortie: {trade.get('exit_reason','')}
Patterns: {trade.get('patterns',[])}
Confiance: {trade['confidence']}%

{verdict}

JSON strict (sans backticks):
{{"lecon":"leçon très courte","pattern":"pattern identifié","action_future":"règle concrète","type":"erreur ou succes"}}"""

        resp   = groq_client.chat.completions.create(
            model=AI_MODELS[0], max_tokens=250, temperature=0.2,
            messages=[{"role":"user","content":prompt}],
        )
        lesson = json.loads(
            resp.choices[0].message.content
            .replace("```json","").replace("```","").strip()
        )
        lesson.update({
            "trade_id": trade["id"], "pnl": trade["pnl"],
            "symbol": trade.get("symbol"), "market": trade.get("market","SPOT"),
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        memory["lessons"].append(lesson)
        db_save_lesson(lesson)

        if lesson["type"] == "erreur":
            memory["patterns_to_avoid"].append(lesson["pattern"])
        else:
            memory["patterns_that_work"].append(lesson["pattern"])
            memory["scalp_wins"] = memory.get("scalp_wins",0) + 1

        memory["lessons"]            = memory["lessons"][-60:]
        memory["patterns_to_avoid"]  = memory["patterns_to_avoid"][-25:]
        memory["patterns_that_work"] = memory["patterns_that_work"][-25:]

        new_threshold = auto_adjust_threshold()
        save_data()
        print(f"[LEARN] {lesson['lecon']}")

        if send_fn:
            stats = get_stats()
            e     = "✅" if lesson["type"] == "succes" else "❌"
            send_fn(
                f"📚 LEÇON #{trade['id']}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{e} {lesson['type'].upper()} | ${trade['pnl']:+.4f}\n"
                f"💡 {lesson['lecon']}\n"
                f"🔍 Pattern : {lesson['pattern']}\n"
                f"📌 Règle   : {lesson['action_future']}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 Win Rate: {stats['win_rate']}% ({stats['total']} trades)\n"
                f"⚙️  Seuil   : {new_threshold}%\n"
                f"🧠 Leçons  : {len(memory['lessons'])}"
            )
    except Exception as e:
        print(f"[LEARN] {e}")


# ═══════════════════════════════════════════════════════════════
#  BOUCLE CONTINUE — 3 NIVEAUX DE FRÉQUENCE
# ═══════════════════════════════════════════════════════════════
def trading_loop(send_fn):
    """
    Boucle principale sans pause fixe.
    Chaque itération prend ~2-5s selon les appels API.
    3 niveaux déclenchés par elapsed time.
    """
    last_monitor = 0
    last_scalp   = 0
    last_deep    = 0
    last_status  = 0
    cycle        = 0

    send_fn(
        f"🚀 BOUCLE CONTINUE DÉMARRÉE\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📡 Surveillance  : toutes les {CYCLE_MONITOR}s\n"
        f"⚡ Scalping      : toutes les {CYCLE_SCALP}s\n"
        f"🔬 Analyse prof. : toutes les {CYCLE_DEEP}s\n"
        f"🪙 Spot          : {len(SPOT_SYMBOLS)} cryptos\n"
        f"📊 Futures       : {len(FUTURES_SYMBOLS)} cryptos\n"
        f"💰 Capital       : ${portfolio['cash']:,.2f}\n"
        f"⚙️  Seuil conf.   : {memory.get('confidence_threshold', CONFIDENCE_BASE)}%\n"
        f"{'✅ Testnet Bybit connecté' if TESTNET_ENABLED else '⚠️  Testnet non configuré (simulation interne)'}"
    )

    while bot_state["running"]:
        now = time.time()

        # ══ NIVEAU 1 : Surveillance SL/TP (30s) ══════════════
        if now - last_monitor >= CYCLE_MONITOR:
            try:
                monitor_positions(send_fn)
                bot_state["last_monitor"] = datetime.now()
            except Exception as e:
                print(f"[MON] {e}")
            last_monitor = now

        # ══ NIVEAU 2 : Scalping rapide (2min) ════════════════
        if now - last_scalp >= CYCLE_SCALP:
            cycle += 1
            bot_state["cycle_count"] = cycle
            try:
                threshold = memory.get("confidence_threshold", CONFIDENCE_BASE)
                send_fn(
                    f"⚡ SCAN SCALPING #{cycle}\n"
                    f"Recherche opportunités sur {len(SPOT_SYMBOLS)} cryptos...\n"
                    f"Positions: {len(portfolio['positions'])}/{MAX_POSITIONS} | "
                    f"Cash: ${portfolio['cash']:.0f}"
                )

                opportunities = scan_opportunities("scalp")
                if not opportunities:
                    send_fn("📭 Aucune opportunité claire ce cycle.")
                else:
                    top = opportunities[:4]
                    opp_str = "\n".join(
                        f"  {'🟢' if o['direction']=='LONG' else '🔴'} "
                        f"{o['symbol'].replace('USDT','')} "
                        f"score={o['score']} RSI={o['rsi']:.0f} mom={o['momentum']:+.2f}%"
                        for o in top
                    )
                    send_fn(f"🎯 Top opportunités:\n{opp_str}")

                    for opp in top:
                        if not bot_state["running"]:
                            break
                        symbol = opp["symbol"]
                        coin   = symbol.replace("USDT","")

                        # Pas d'analyse si déjà en position sur ce symbole
                        already = any(p["symbol"]==symbol
                                      for p in portfolio["positions"].values())

                        send_fn(
                            f"🔍 Analyse {coin}...\n"
                            f"  RSI={opp['rsi']:.0f} | "
                            f"Momentum={opp['momentum']:+.2f}% | "
                            f"Score={opp['score']}"
                        )

                        analysis = analyze_scalp(opp)
                        signal   = analysis["signal"]
                        conf     = analysis["confidence"]
                        risk     = analysis["risk"]
                        votes    = analysis.get("votes", [])
                        pat_alert = [p for p in analysis.get("patterns",[])
                                     if p["signal"]=="HOLD"]

                        # Bloque si Pump/Dump
                        if pat_alert:
                            send_fn(
                                f"🚨 {coin} BLOQUÉ — Manipulation détectée\n"
                                f"{pat_alert[0]['desc']}"
                            )
                            continue

                        sig_e = {"BUY":"🟢","SELL":"🔴","HOLD":"⚪"}.get(signal,"⚪")
                        send_fn(
                            f"{sig_e} {coin}: {signal} {conf}% "
                            f"[{'/'.join(votes)}] | {risk}\n"
                            f"  {analysis.get('reason','')[:80]}"
                        )

                        if signal in ("BUY","SELL") and conf >= threshold \
                                and risk in ("LOW","MEDIUM") and not already:
                            market = analysis.get("market","SPOT")
                            if signal == "SELL" and market != "FUTURES":
                                send_fn(f"⚠️ Short {coin} ignoré — pas de short sur SPOT")
                                continue
                            open_position(analysis, send_fn)

                        elif signal in ("BUY","SELL") and conf < threshold:
                            send_fn(
                                f"📉 {coin} ignoré — conf {conf}% < seuil {threshold}%"
                            )

                        elif already and signal == "SELL":
                            # Clôture si signal inverse
                            for pk, pos in list(portfolio["positions"].items()):
                                if pos["symbol"] == symbol:
                                    price = opp["price"]
                                    close_position(pk, price,
                                                   f"Signal SELL {conf}%", send_fn)

                bot_state["last_scalp"] = datetime.now()

            except Exception as e:
                print(f"[SCALP] {e}")
                send_fn(f"⚠️ Erreur cycle #{cycle}: {e}")

            last_scalp = now

        # ══ NIVEAU 3 : Analyse profonde (5min) ═══════════════
        if now - last_deep >= CYCLE_DEEP:
            try:
                _deep_analysis(send_fn)
                bot_state["last_deep"] = datetime.now()
            except Exception as e:
                print(f"[DEEP] {e}")
            last_deep = now

        # ══ Bilan toutes les 15min ════════════════════════════
        if now - last_status >= 900:
            try:
                _send_status(send_fn)
            except Exception as e:
                print(f"[STATUS] {e}")
            last_status = now

        bot_state["last_heartbeat"] = datetime.now()
        time.sleep(2)  # petite pause pour ne pas saturer l'API


def _deep_analysis(send_fn):
    """Analyse profonde multi-TF sur les futures toutes les 5min."""
    fear_greed = get_fear_greed()
    send_fn(
        f"🔬 ANALYSE PROFONDE (5min)\n"
        f"{fear_greed}\n"
        f"Analyse futures sur {len(FUTURES_SYMBOLS)} cryptos..."
    )
    threshold = memory.get("confidence_threshold", CONFIDENCE_BASE)

    for symbol in FUTURES_SYMBOLS[:4]:  # top 4 pour limiter les appels
        try:
            coin  = symbol.replace("USDT","")
            mtf   = get_multi_tf(symbol)
            conf  = tf_confluence(mtf)
            price = get_futures_price(symbol)

            # Analyse uniquement si confluence forte
            if abs(conf["score"]) < 4:
                continue

            direction = "BUY" if conf["direction"]=="LONG" else "SELL"
            ind_5m    = mtf.get("5m", {})
            ob        = get_order_book_imbalance(symbol)

            prompt = f"""Expert trading futures court terme.

{symbol} FUTURES | ${price:.2f} | Levier x{FUTURES_LEVERAGE}
Confluence TF: {conf['score']}/9 → {conf['direction']}
Signaux: {', '.join(conf['signals'][:4])}
RSI 5m: {ind_5m.get('rsi','?')} | MACD hist: {ind_5m.get('macd_hist','?')}
OrderBook: {ob['pressure']} (ratio={ob['ratio']})
{fear_greed}

Signal ciblé: {direction}
Objectif: scalp court terme avec levier x{FUTURES_LEVERAGE}

JSON strict (sans backticks):
{{"signal":"{direction} ou HOLD","confidence":0-100,"reason":"raison","risk":"LOW ou MEDIUM ou HIGH","market":"FUTURES"}}"""

            result = majority_vote_fast(prompt)
            result.update({"symbol":symbol,"price":price,
                            "patterns":[],"ob":ob,"indicators":ind_5m})
            result["market"] = "FUTURES"

            sig_e = {"BUY":"🟢","SELL":"🔴","HOLD":"⚪"}.get(result["signal"],"⚪")
            send_fn(
                f"{sig_e} FUTURES {coin}: {result['signal']} "
                f"{result['confidence']}% [{'/'.join(result.get('votes',[]))}]\n"
                f"  Conf TF: {conf['score']}/9 | {result.get('reason','')[:70]}"
            )

            if (result["signal"] in ("BUY","SELL")
                    and result["confidence"] >= threshold
                    and result["risk"] in ("LOW","MEDIUM")):
                already = any(p["symbol"]==symbol for p in portfolio["positions"].values())
                if not already:
                    open_position(result, send_fn)

        except Exception as e:
            print(f"[DEEP] {symbol}: {e}")


def _send_status(send_fn):
    """Envoie un bilan toutes les 15 minutes."""
    pv    = get_portfolio_value()
    stats = get_stats()
    wr_db = db_win_rate(30)
    best  = db_best_symbols(3)
    threshold = memory.get("confidence_threshold", CONFIDENCE_BASE)

    pos_lines = ""
    if portfolio["positions"]:
        prices = get_prices_batch(SPOT_SYMBOLS + FUTURES_SYMBOLS)
        for pk, pos in portfolio["positions"].items():
            price = prices.get(pos["symbol"], pos["price_in"])
            chg   = (price-pos["price_in"])/pos["price_in"]*100
            chg  *= pos.get("leverage",1)
            e     = "📈" if chg > 0 else "📉"
            pos_lines += f"  {e} {pos['symbol'].replace('USDT','')} {pos['side']}: {chg:+.2f}%\n"

    best_str = " | ".join(
        f"{b['symbol'].replace('USDT','')} +${b['avg_pnl']:.2f}" for b in best
    ) or "Aucun encore"

    send_fn(
        f"📋 BILAN 15min\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Portfolio : ${pv:,.2f} ({((pv/portfolio['initial'])-1)*100:+.1f}%)\n"
        f"💵 Cash      : ${portfolio['cash']:,.2f}\n"
        f"📍 Positions : {len(portfolio['positions'])}/{MAX_POSITIONS}\n"
        f"{pos_lines}"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 Win Rate  : {stats['win_rate']}% | DB(30): {wr_db}%\n"
        f"📊 Trades    : {stats['total']} | Aujourd'hui: {bot_state['trades_today']}\n"
        f"⏱ Durée moy : {stats['avg_duration']} min\n"
        f"🥇 Top coins : {best_str}\n"
        f"⚙️  Seuil     : {threshold}% | 🔄 Cycle #{bot_state['cycle_count']}\n"
        f"📚 Leçons    : {len(memory['lessons'])}"
    )


# ═══════════════════════════════════════════════════════════════
#  WATCHDOG + RÉSUMÉ JOURNALIER
# ═══════════════════════════════════════════════════════════════
def bot_watchdog(send_fn):
    time.sleep(180)
    alerted = False
    while True:
        time.sleep(60)
        if not bot_state["running"]:
            alerted = False; continue
        last    = bot_state.get("last_heartbeat")
        if not last: continue
        elapsed = (datetime.now()-last).total_seconds()
        if elapsed > 300 and not alerted:
            send_fn(
                f"⚠️ WATCHDOG: Bot inactif {int(elapsed//60)} min\n"
                f"Dernier heartbeat: {last.strftime('%H:%M:%S')}"
            )
            alerted = True
        elif elapsed <= 300:
            alerted = False


def daily_summary(send_fn):
    while True:
        now      = datetime.now()
        midnight = (now+timedelta(days=1)).replace(hour=0,minute=0,second=5,microsecond=0)
        time.sleep((midnight-now).total_seconds())
        try:
            pv    = get_portfolio_value()
            pnl   = pv - portfolio["initial"]
            stats = get_stats()
            today = now.strftime("%Y-%m-%d")
            today_trades = [t for t in portfolio["trades"]
                            if t.get("time_in","").startswith(today)]
            today_pnl    = sum(t["pnl"] for t in today_trades if t.get("pnl"))
            best_sym     = db_best_symbols(3)
            threshold    = memory.get("confidence_threshold", CONFIDENCE_BASE)
            lessons      = "\n".join(
                f"  {'✅' if l['type']=='succes' else '❌'} {l['lecon']}"
                for l in memory["lessons"][-3:]
            ) or "  Aucune"
            send_fn(
                f"📊 RÉSUMÉ JOURNALIER — {now.strftime('%d/%m/%Y')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Portfolio : ${pv:,.2f} ({pnl/portfolio['initial']*100:+.1f}%)\n"
                f"📈 PnL total : ${pnl:+.2f}\n"
                f"📅 PnL aujourd'hui : ${today_pnl:+.2f}\n"
                f"📊 Trades du jour : {len(today_trades)}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🏆 Win Rate : {stats['win_rate']}% ({stats['total']} trades)\n"
                f"⏱ Durée moy: {stats['avg_duration']} min\n"
                f"⚙️  Seuil    : {threshold}%\n"
                f"🧠 Leçons   : {len(memory['lessons'])}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Dernières leçons:\n{lessons}"
            )
        except Exception as e:
            print(f"[DAILY] {e}")


# ═══════════════════════════════════════════════════════════════
#  SELF-PING (anti-sleep Koyeb Free)
# ═══════════════════════════════════════════════════════════════
def self_ping():
    time.sleep(60)
    while True:
        try:
            requests.get("https://junior-tick-1ever-6bf9cee7.koyeb.app/health",
                         timeout=10)
        except Exception:
            pass
        time.sleep(270)


# ═══════════════════════════════════════════════════════════════
#  DASHBOARD HTML
# ═══════════════════════════════════════════════════════════════
def generate_dashboard() -> str:
    stats    = get_stats()
    pv       = get_portfolio_value()
    pnl      = pv - portfolio["initial"]
    pnl_pct  = pnl / portfolio["initial"] * 100
    status   = "🟢 EN MARCHE" if bot_state["running"] else "🔴 ARRÊTÉ"
    last     = bot_state.get("last_heartbeat")
    hb_str   = last.strftime("%H:%M:%S") if last else "—"
    threshold = memory.get("confidence_threshold", CONFIDENCE_BASE)
    wr_db    = db_win_rate(30)
    best_sym = db_best_symbols(3)

    prices = get_prices_batch(SPOT_SYMBOLS)

    pos_html = ""
    for pk, pos in portfolio["positions"].items():
        price = prices.get(pos["symbol"], pos["price_in"])
        chg   = (price-pos["price_in"])/pos["price_in"]*100 * pos.get("leverage",1)
        color = "#2ecc71" if chg>=0 else "#e74c3c"
        m_tag = "⚡FUT" if pos["market"]=="FUTURES" else "💱SPT"
        pos_html += (
            f"<tr><td>{pos['symbol'].replace('USDT','')}</td>"
            f"<td>{m_tag} {pos['side']}</td>"
            f"<td>${pos['price_in']:.4f}</td><td>${price:.4f}</td>"
            f'<td style="color:{color}">{chg:+.2f}%</td>'
            f"<td>${pos['qty']*price:.2f}</td></tr>"
        )

    trades_html = ""
    for t in reversed(portfolio["trades"][-25:]):
        if t.get("pnl") is not None:
            color   = "#2ecc71" if t["pnl"]>0 else "#e74c3c"
            pnl_str = f'<span style="color:{color}">${t["pnl"]:+.4f} ({t.get("pnl_pct",0):+.2f}%)</span>'
        else:
            pnl_str = '<span style="color:#f39c12">En cours</span>'
        po  = f"${t['price_out']:.4f}" if t.get("price_out") else "—"
        dur = f"{t.get('duration_min','—')}min"
        trades_html += (
            f"<tr><td>{t['id']}</td>"
            f"<td>{t.get('symbol','').replace('USDT','')}</td>"
            f"<td>{t.get('market','SPT')}</td>"
            f"<td>{t.get('side','L')}</td>"
            f"<td>${t['price_in']:.4f}</td><td>{po}</td>"
            f"<td>{pnl_str}</td><td>{t['confidence']}%</td>"
            f"<td>{dur}</td><td>{t['time_in']}</td></tr>"
        )

    lessons_html = ""
    for l in reversed(memory["lessons"][-10:]):
        color = "#e74c3c" if l["type"]=="erreur" else "#2ecc71"
        e     = "❌" if l["type"]=="erreur" else "✅"
        lessons_html += (
            f'<tr><td style="color:{color}">{e}</td>'
            f"<td>{l.get('symbol','')}</td>"
            f'<td style="color:{color}">${l.get("pnl",0):+.4f}</td>'
            f"<td>{l['lecon'][:55]}</td>"
            f"<td>{l['action_future'][:55]}</td>"
            f"<td>{l['date']}</td></tr>"
        )

    best_html = "".join(
        f"<span style='background:#21262d;padding:3px 8px;border-radius:6px;margin:2px;font-size:.8em'>"
        f"{'🥇' if i==0 else '🥈' if i==1 else '🥉'} "
        f"{b['symbol'].replace('USDT','')} +${b['avg_pnl']:.2f} ({b['n']} trades)</span>"
        for i, b in enumerate(best_sym)
    ) or "<span style='color:#8b949e'>Aucun encore</span>"

    return f"""<!DOCTYPE html><html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Bot v3</title>
<style>
body{{font-family:Arial,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:16px}}
h1{{color:#58a6ff;text-align:center;font-size:1.3em}}
h2{{color:#58a6ff;font-size:.9em;margin:14px 0 5px}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:14px}}
.card{{background:#161b22;border-radius:8px;padding:10px;text-align:center}}
.label{{font-size:.7em;color:#8b949e;margin-bottom:3px}}
.value{{font-size:1.15em;font-weight:bold}}
.green{{color:#2ecc71}}.red{{color:#e74c3c}}.blue{{color:#58a6ff}}.yellow{{color:#f39c12}}
.status,.sub{{text-align:center;font-size:.82em;color:#8b949e;margin-bottom:3px}}
table{{width:100%;border-collapse:collapse;font-size:.71em;margin-bottom:18px}}
th{{background:#21262d;padding:5px;text-align:left;color:#8b949e}}
td{{padding:4px 5px;border-bottom:1px solid #21262d}}
.badge{{background:#21262d;border-radius:5px;padding:2px 5px;font-size:.72em;margin:1px;display:inline-block}}
</style>
<meta http-equiv="refresh" content="30">
</head><body>
<h1>🤖 Trading Bot v3 — Continu</h1>
<div class="status">{status} | Dernier heartbeat: {hb_str}</div>
<div class="sub">
  Seuil: {threshold}% (auto) | WR(30): {wr_db}% | 
  Cycle: #{bot_state['cycle_count']} | 
  Trades/jour: {bot_state['trades_today']} |
  SL: {STOP_LOSS_PCT*100:.1f}% | TP: {TAKE_PROFIT_PCT*100:.1f}% | Trailing: {TRAILING_STOP_PCT*100:.1f}%
</div>
<div class="sub">Top coins: {best_html}</div>
<div class="grid">
  <div class="card"><div class="label">Portfolio</div>
    <div class="value blue">${pv:,.2f}</div></div>
  <div class="card"><div class="label">PnL Total</div>
    <div class="value {'green' if pnl>=0 else 'red'}">${pnl:+.2f} ({pnl_pct:+.1f}%)</div></div>
  <div class="card"><div class="label">Cash</div>
    <div class="value">${portfolio['cash']:,.2f}</div></div>
  <div class="card"><div class="label">Positions</div>
    <div class="value yellow">{len(portfolio['positions'])}/{MAX_POSITIONS}</div></div>
  <div class="card"><div class="label">Win Rate</div>
    <div class="value yellow">{stats['win_rate']}%</div></div>
  <div class="card"><div class="label">Trades | Leçons</div>
    <div class="value">{stats['total']} | {len(memory['lessons'])}</div></div>
</div>
<h2>Positions Ouvertes</h2>
<table><thead><tr>
  <th>Coin</th><th>Marché/Sens</th><th>Entrée</th><th>Actuel</th>
  <th>PnL%</th><th>Valeur</th>
</tr></thead><tbody>
{pos_html or '<tr><td colspan="6" style="text-align:center;color:#8b949e">Aucune position</td></tr>'}
</tbody></table>
<h2>Historique Trades</h2>
<table><thead><tr>
  <th>#</th><th>Coin</th><th>Mkt</th><th>Sens</th>
  <th>Entrée</th><th>Sortie</th><th>PnL</th>
  <th>Conf</th><th>Durée</th><th>Heure</th>
</tr></thead><tbody>
{trades_html or '<tr><td colspan="10" style="text-align:center;color:#8b949e">Aucun</td></tr>'}
</tbody></table>
<h2>Mémoire & Leçons</h2>
<table><thead><tr>
  <th>Type</th><th>Coin</th><th>PnL</th><th>Leçon</th><th>Action</th><th>Date</th>
</tr></thead><tbody>
{lessons_html or '<tr><td colspan="6" style="text-align:center;color:#8b949e">Aucune</td></tr>'}
</tbody></table>
</body></html>"""


# ═══════════════════════════════════════════════════════════════
#  SERVEUR HTTP + WEBHOOK
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

    def log_message(self, fmt, *args): pass


async def _process_update(body: bytes):
    try:
        update = Update.de_json(json.loads(body), _app.bot)
        await _app.process_update(update)
    except Exception as e:
        print(f"[WH] {e}")


def run_server():
    HTTPServer(("0.0.0.0", WEBHOOK_PORT), BotHandler).serve_forever()


# ═══════════════════════════════════════════════════════════════
#  TELEGRAM HELPER
# ═══════════════════════════════════════════════════════════════
def make_send(chat_id: str):
    def send(msg: str):
        if _app is None or _main_loop is None:
            print(f"[MSG] {msg[:80]}")
            return
        future = asyncio.run_coroutine_threadsafe(
            _app.bot.send_message(chat_id=chat_id, text=msg), _main_loop)
        try:
            future.result(timeout=15)
        except Exception as e:
            print(f"[MSG] {e}")
    return send


def _auth(update: Update) -> bool:
    return str(update.effective_chat.id) == TELEGRAM_CHAT_ID


# ═══════════════════════════════════════════════════════════════
#  COMMANDES TELEGRAM
# ═══════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    if bot_state["running"]:
        await update.message.reply_text("Bot déjà en marche !")
        return
    bot_state.update({"running":True, "trades_today":0,
                      "cycle_count":0, "last_heartbeat":None})
    send = make_send(TELEGRAM_CHAT_ID)
    threading.Thread(target=trading_loop,  args=(send,), daemon=True).start()
    threading.Thread(target=bot_watchdog,  args=(send,), daemon=True).start()
    threading.Thread(target=daily_summary, args=(send,), daemon=True).start()


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    bot_state["running"] = False
    await update.message.reply_text(
        f"🛑 Bot arrêté.\n"
        f"Positions ouvertes: {len(portfolio['positions'])}\n"
        f"(elles restent en mémoire, surveillées au prochain /start)"
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    pv    = get_portfolio_value()
    pnl   = pv - portfolio["initial"]
    stats = get_stats()
    last  = bot_state.get("last_heartbeat")
    threshold = memory.get("confidence_threshold", CONFIDENCE_BASE)
    wr_db = db_win_rate(30)

    pos_lines = ""
    if portfolio["positions"]:
        prices = get_prices_batch(SPOT_SYMBOLS)
        for pk, pos in portfolio["positions"].items():
            price = prices.get(pos["symbol"], pos["price_in"])
            chg   = (price-pos["price_in"])/pos["price_in"]*100
            pos_lines += f"\n  📍 {pos['symbol'].replace('USDT','')} {pos['side']}: {chg:+.2f}%"

    await update.message.reply_text(
        f"{'🟢' if bot_state['running'] else '🔴'} "
        f"{'EN MARCHE' if bot_state['running'] else 'ARRÊTÉ'}\n"
        f"Heartbeat: {last.strftime('%H:%M:%S') if last else '—'}\n"
        f"Cycle #{bot_state['cycle_count']}\n"
        f"━━━━━━━━━━━━━\n"
        f"💰 ${pv:,.2f} ({pnl:+.2f})\n"
        f"💵 Cash: ${portfolio['cash']:,.2f}\n"
        f"📍 Positions: {len(portfolio['positions'])}{pos_lines}\n"
        f"━━━━━━━━━━━━━\n"
        f"📊 Trades: {stats['total']} | WR: {stats['win_rate']}%\n"
        f"WR DB(30): {wr_db}% | Seuil: {threshold}%\n"
        f"📚 Leçons: {len(memory['lessons'])}"
    )


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    await update.message.reply_text("🔍 Scan en cours...")
    try:
        opps = scan_opportunities("scalp")
        lines = ["🎯 Top opportunités du marché\n━━━━━━━━━━━━━"]
        for o in opps[:6]:
            e = "🟢" if o["direction"]=="LONG" else "🔴"
            lines.append(
                f"{e} {o['symbol'].replace('USDT','')} | "
                f"score={o['score']} | RSI={o['rsi']:.0f} | "
                f"mom={o['momentum']:+.2f}% | vol={o['vol']:.2f}%"
            )
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Erreur: {e}")


async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    pv    = get_portfolio_value()
    pnl   = pv - portfolio["initial"]
    stats = get_stats()
    best  = db_best_symbols(3)
    best_str = " | ".join(
        f"{b['symbol'].replace('USDT','')}+${b['avg_pnl']:.2f}"
        for b in best) or "Aucun"
    pos_str = "\n".join(
        f"  {pos['symbol'].replace('USDT','')} {pos['market']} {pos['side']}"
        for pos in portfolio["positions"].values()
    ) or "  Aucune"
    await update.message.reply_text(
        f"💼 Portfolio\n"
        f"Capital: $10,000 → ${pv:,.2f} ({pnl:+.2f})\n"
        f"Cash: ${portfolio['cash']:,.2f}\n"
        f"━━━━━━━━━━━━━\n"
        f"Positions:\n{pos_str}\n"
        f"━━━━━━━━━━━━━\n"
        f"Trades: {stats['total']} | WR: {stats['win_rate']}%\n"
        f"Durée moy: {stats['avg_duration']} min\n"
        f"Top coins: {best_str}"
    )


async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    if not portfolio["positions"]:
        await update.message.reply_text("Aucune position ouverte."); return
    prices = get_prices_batch(SPOT_SYMBOLS + FUTURES_SYMBOLS)
    lines  = ["📍 Positions ouvertes\n━━━━━━━━━━━━━"]
    for pk, pos in portfolio["positions"].items():
        price = prices.get(pos["symbol"], pos["price_in"])
        chg   = (price-pos["price_in"])/pos["price_in"]*100 * pos.get("leverage",1)
        e     = "📈" if chg>0 else "📉"
        sl    = pos["price_in"]*(1-STOP_LOSS_PCT) if pos["side"]=="LONG" \
                else pos["price_in"]*(1+STOP_LOSS_PCT)
        tp    = pos["price_in"]*(1+TAKE_PROFIT_PCT) if pos["side"]=="LONG" \
                else pos["price_in"]*(1-TAKE_PROFIT_PCT)
        lines.append(
            f"{e} {pos['symbol'].replace('USDT','')} "
            f"{pos['market']} {pos['side']}: {chg:+.2f}%\n"
            f"  ${pos['price_in']:.4f} → ${price:.4f}\n"
            f"  🛑${sl:.4f} | 🎯${tp:.4f}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_lecons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    if not memory["lessons"]:
        await update.message.reply_text("Aucune leçon encore."); return
    msg = f"📚 Leçons ({len(memory['lessons'])}):\n\n"
    for l in memory["lessons"][-5:]:
        e = "✅" if l["type"]=="succes" else "❌"
        msg += f"{e} [{l.get('symbol','')}] ${l.get('pnl',0):+.4f}\n"
        msg += f"{l['lecon']}\n→ {l['action_future']}\n\n"
    await update.message.reply_text(msg)


async def cmd_fermer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ferme toutes les positions manuellement."""
    if not _auth(update): return
    if not portfolio["positions"]:
        await update.message.reply_text("Aucune position à fermer."); return
    send = make_send(TELEGRAM_CHAT_ID)
    prices = get_prices_batch(SPOT_SYMBOLS + FUTURES_SYMBOLS)
    count  = 0
    for pk in list(portfolio["positions"].keys()):
        pos   = portfolio["positions"].get(pk)
        if not pos: continue
        price = prices.get(pos["symbol"], pos["price_in"])
        close_position(pk, price, "Fermeture manuelle /fermer", send)
        count += 1
    await update.message.reply_text(f"✅ {count} position(s) fermée(s).")


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    bot_state.update({"running":False, "cycle_count":0, "trades_today":0})
    portfolio.update({"cash":10000.0, "positions":{}, "trades":[]})
    memory.update({
        "lessons":[], "patterns_to_avoid":[], "patterns_that_work":[],
        "analysis_history":[], "confidence_threshold": CONFIDENCE_BASE,
        "scalp_wins":0, "scalp_losses":0,
    })
    save_data()
    await update.message.reply_text(
        "🔄 Reset complet — capital $10,000.\n"
        "(Base SQLite conservée pour l'historique long terme.)"
    )


async def cmd_testnet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    if TESTNET_ENABLED:
        try:
            balance = bybit_test.get_wallet_balance(accountType="UNIFIED")
            coins   = balance["result"]["list"][0]["coin"]
            usdt    = next((c for c in coins if c["coin"]=="USDT"), None)
            bal     = usdt["walletBalance"] if usdt else "?"
            await update.message.reply_text(
                f"✅ Bybit Testnet connecté\n"
                f"Balance USDT: ${bal}\n"
                f"URL: testnet.bybit.com"
            )
        except Exception as e:
            await update.message.reply_text(f"⚠️ Testnet erreur: {e}")
    else:
        await update.message.reply_text(
            "⚠️ Testnet non configuré\n\n"
            "Pour activer:\n"
            "1. Créer compte sur testnet.bybit.com\n"
            "2. Générer clés API\n"
            "3. Ajouter dans Koyeb:\n"
            "   BYBIT_TESTNET_KEY = ta_clé\n"
            "   BYBIT_TESTNET_SECRET = ton_secret\n"
            "4. Redéployer\n\n"
            "En attendant, le bot tourne en simulation interne ($10,000 fictifs)."
        )


# ═══════════════════════════════════════════════════════════════
#  APPLICATION TELEGRAM
# ═══════════════════════════════════════════════════════════════
async def run_telegram():
    global _app, _main_loop
    _main_loop = asyncio.get_event_loop()

    _app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .request(HTTPXRequest(
            connection_pool_size=8, pool_timeout=30.0,
            connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0,
        ))
        .updater(None)
        .build()
    )

    for cmd, fn in [
        ("start",     cmd_start),
        ("stop",      cmd_stop),
        ("status",    cmd_status),
        ("scan",      cmd_scan),
        ("portfolio", cmd_portfolio),
        ("positions", cmd_positions),
        ("lecons",    cmd_lecons),
        ("fermer",    cmd_fermer),
        ("testnet",   cmd_testnet),
        ("reset",     cmd_reset),
    ]:
        _app.add_handler(CommandHandler(cmd, fn))

    await _app.initialize()
    await _app.start()

    if WEBHOOK_URL:
        full = WEBHOOK_URL.rstrip("/") + WEBHOOK_PATH
        await _app.bot.set_webhook(url=full, drop_pending_updates=True,
                                   allowed_updates=["message"])
        print(f"Webhook: {full}")
    else:
        print("⚠️  WEBHOOK_URL non définie")

    print("Bot v3 prêt — boucle continue activée")

    try:
        while True:
            await asyncio.sleep(1)
    finally:
        if WEBHOOK_URL:
            await _app.bot.delete_webhook()
        await _app.stop()
        await _app.shutdown()


# ═══════════════════════════════════════════════════════════════
#  ENTRYPOINT
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("🚀 Trading Bot v3 — Démarrage...")
    init_db()
    load_data()
    threading.Thread(target=run_server, daemon=True).start()
    threading.Thread(target=self_ping,  daemon=True).start()
    print("Serveur HTTP port 8000")
    asyncio.run(run_telegram())
