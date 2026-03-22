"""
Trading Bot v4 — Simulation Pure sur Marché Réel
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Le bot observe les vrais prix en temps réel (Bybit API)
et simule ses propres trades avec $1000 virtuels.
Il prend ses décisions seul, apprend de chaque résultat,
et tourne en continu 24h/24.

Architecture :
  • 30s  → surveillance SL/TP/trailing sur positions ouvertes
  • 2min → scan + scalping sur 20 cryptos
  • 5min → analyse profonde + positions simulées long/short
  • 15min → bilan complet Telegram
"""

import os, time, threading, feedparser, requests, asyncio
import json, sqlite3, re, hashlib
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from collections import Counter, deque
from urllib.parse import quote_plus

from groq import Groq
from pybit.unified_trading import HTTP
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════
GROQ_KEY         = os.environ.get("ANTHROPIC_KEY")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
BYBIT_KEY        = os.environ.get("BINANCE_KEY", "")
BYBIT_SECRET     = os.environ.get("BINANCE_SECRET", "")
WEBHOOK_URL      = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_PATH     = "/webhook"
WEBHOOK_PORT     = 8000

# ── Simulation ─────────────────────────────────────────────────
CAPITAL_INITIAL   = 1000.0   # portefeuille de départ
MAX_POSITIONS     = 4        # max trades simultanés
MAX_PCT_PER_TRADE = 0.25     # max 25% du cash par trade
STOP_LOSS_PCT     = 0.025    # -2.5%
TAKE_PROFIT_PCT   = 0.04     # +4%
TRAILING_PCT      = 0.015    # trailing stop -1.5% du pic
LEVERAGE_SIM      = 2        # levier simulé x2 sur "futures"

# ── Seuils IA ─────────────────────────────────────────────────
CONFIDENCE_BASE = 65
CONFIDENCE_MIN  = 55
CONFIDENCE_MAX  = 82

# ── Mode apprentissage (voir config complète plus bas) ──────

# ── Fréquences ────────────────────────────────────────────────
CYCLE_MICRO   = 8     # micro-trades : toutes les 8s
CYCLE_MONITOR = 15    # surveillance SL/TP : toutes les 15s
CYCLE_SCALP   = 300   # scalping IA : toutes les 5min (économise tokens Groq)
CYCLE_DEEP    = 300   # analyse profonde : toutes les 5min
CYCLE_STATUS  = 900   # bilan : toutes les 15min

# ── Micro-trading : paramètres spéciaux ──────────────────────
MICRO_SL_PCT       = 0.008   # stop-loss micro : -0.8%
MICRO_TP_PCT       = 0.012   # take-profit micro : +1.2%
MICRO_TRAILING_PCT = 0.005   # trailing micro : -0.5% du pic
MICRO_MAX_DURATION = 90      # ferme le micro-trade après 90s max
MICRO_MAX_PCT      = 0.10    # max 10% du cash par micro-trade
MICRO_CONF_MIN     = 72      # seuil plus élevé (signal très fort requis)
MAX_MICRO_POSITIONS = 3      # max 3 micro-trades simultanés

# ── Indicateurs micro-trading (sur bougies 1min) ──────────────
# Signaux utilisés : EMA cross 1min, RSI divergence, spike volume,
# momentum 3 bougies, Bollinger squeeze, VWAP approximé

# ── Crypto majeurs + small caps Bybit ────────────────────────
CRYPTO_SYMBOLS = [
    # Majeurs
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "DOGEUSDT","ADAUSDT","AVAXUSDT","MATICUSDT","LINKUSDT",
    "DOTUSDT","UNIUSDT","ATOMUSDT","LTCUSDT","NEARUSDT",
    "APTUSDT","ARBUSDT","OPUSDT","INJUSDT","SUIUSDT",
    # Small caps
    "FETUSDT","RENDERUSDT","WLDUSDT","STRKUSDT","PYTHUSDT",
    "JUPUSDT","TIAUSDT","SEIUSDT","ALTUSDT","ZKUSDT",
    "EIGENUSDT","MNTUSDT","WUSDT","ONDOUSDT","ENAUSDT",
    "REZUSDT","BBUSDT","NOTUSDT","TURBOUSDT","CATIUSDT",
]

# Actions US — via Yahoo Finance (gratuit)
STOCKS_SYMBOLS = {
    "AAPL":  "Apple",   "TSLA":  "Tesla",   "NVDA":  "NVIDIA",
    "META":  "Meta",    "MSFT":  "Microsoft","GOOGL": "Google",
    "AMZN":  "Amazon",  "AMD":   "AMD",      "NFLX":  "Netflix",
    "COIN":  "Coinbase","MSTR":  "MicroStrategy",
}

# Forex — via Yahoo Finance
FOREX_SYMBOLS = {
    "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY", "AUDUSD=X": "AUD/USD",
    "USDCHF=X": "USD/CHF", "BTCUSD=X": "BTC/USD",
}

# Matières premières — via Yahoo Finance
COMMODITY_SYMBOLS = {
    "GC=F":  "Or",      "SI=F":  "Argent",
    "CL=F":  "Pétrole", "NG=F":  "Gaz naturel",
    "HG=F":  "Cuivre",
}

ALL_SYMBOLS = CRYPTO_SYMBOLS  # Bybit pour les prix temps réel

# Sous-ensemble pour micro-trading (les plus liquides)
MICRO_SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "DOGEUSDT","AVAXUSDT","LINKUSDT","ARBUSDT","APTUSDT",
    "FETUSDT","INJUSDT","NEARUSDT","SUIUSDT","OPUSDT",
]

# Mode apprentissage forcé
LEARN_MODE_ENABLED     = True   # trade même avec signal faible
LEARN_MODE_CONF_MIN = 45     # seuil minimal en mode apprentissage
LEARN_MODE_MAX_PCT  = 0.05   # max 5% du cash par trade en mode apprentissage

DB_FILE   = "sim_v4.db"
DATA_FILE = Path("sim_portfolio.json")

# Modèles Groq actifs en 2025 (mixtral + gemma2 désactivés)
AI_MODELS = [
    "llama-3.3-70b-versatile",   # principal — rapide et fiable
    "llama3-70b-8192",            # backup — même famille, quota séparé
    "llama-3.1-8b-instant",       # ultra-rapide — pour validation légère
]

# Limites Groq Free : 100k tokens/jour par modèle
# Stratégie : utiliser llama-3.1-8b-instant pour les analyses répétitives
# et réserver llama-3.3-70b pour les décisions importantes
GROQ_PRIMARY_MODEL   = "llama-3.3-70b-versatile"
GROQ_SECONDARY_MODEL = "llama3-70b-8192"
GROQ_FAST_MODEL      = "llama-3.1-8b-instant"  # 8B = 6x moins de tokens

# ── Surveillance traders en temps réel ───────────────────────
# Comptes Nitter (miroir gratuit Twitter) à surveiller
TRADER_TWITTER_ACCOUNTS = [
    "michael_saylor",    # MicroStrategy - BTC maximaliste
    "CathieDWood",       # ARK Invest - growth/innovation
    "APompliano",        # Anthony Pompliano - crypto macro
    "PeterSchiff",       # Bear crypto - bon pour contrarian
    "WClementeIII",      # On-chain analyst
    "DocumentingBTC",    # Bitcoin tracker
    "AltcoinDailyio",    # Altcoin analysis
    "CryptoKaleo",       # TA trader influent
    "RaoulGMI",          # Macro crypto
    "inversebrah",       # Crypto sentiment
]

# Chaînes YouTube à surveiller (ID de chaîne)
YOUTUBE_CHANNELS = {
    "Benjamin Cowen":    "UCRvqjQPSeaWn-uEx-w0XOIg",
    "Coin Bureau":       "UCqK_GSMbpiV8spgD3ZGloSw",
    "Andrei Jikh":       "UCGn_PEBIgFj_zB4Jnz3bnmA",
    "InvestAnswers":     "UCnMn36GT_H0X-w5_ckLtlgQ",
}

# Nitter instances publiques (miroir Twitter gratuit)
NITTER_INSTANCES = [
    "nitter.privacydev.net",
    "nitter.poast.org",
    "nitter.1d4.us",
]

# ── Philosophies des traders légendaires ──────────────────────
# Injectées dans le prompt IA pour guider les décisions
TRADER_PHILOSOPHIES = """
RÈGLES INSPIRÉES DES MEILLEURS TRADERS :

1. MICHAEL SAYLOR (accumulation) :
   - Fear&Greed < 20 = opportunité rare → augmenter la taille de position
   - Ne jamais vendre en panique, les baisses sont des cadeaux
   - BTC et grandes caps = conviction forte même en bear market

2. CATHIE WOOD (growth/innovation) :
   - Favoriser les tokens avec fort potentiel disruptif (AI, DeFi, L2)
   - Acheter les corrections sur les tendances de fond haussières
   - RSI bas sur un token innovant = point d'entrée, pas de danger

3. PAUL TUDOR JONES (macro/protection) :
   - JAMAIS perdre plus de 2% du capital sur un seul trade
   - Si Fear&Greed < 15 ET macro bearish → réduire les positions
   - Le risque de ruine prime sur le gain → toujours protéger le capital

4. JESSE LIVERMORE (momentum/scalping) :
   - "The trend is your friend" → trader dans le sens de la tendance
   - Ne jamais moyenner à la baisse sur un perdant
   - Quand un trade est gagnant, laisser courir → trailing stop large
   - Volume élevé confirme le mouvement → signal plus fiable

5. WARREN BUFFETT (valeur/patience) :
   - N'entrer que sur des signaux de très haute conviction (conf > 75%)
   - "Be fearful when others are greedy, greedy when others are fearful"
   - Fear&Greed < 25 → context d'achat exceptionnel
   - La patience est un avantage compétitif
"""

# ═══════════════════════════════════════════════════════════════
#  CLIENTS
# ═══════════════════════════════════════════════════════════════
groq_client = Groq(api_key=GROQ_KEY)
bybit       = HTTP(api_key=BYBIT_KEY, api_secret=BYBIT_SECRET)

# ═══════════════════════════════════════════════════════════════
#  ÉTAT GLOBAL
# ═══════════════════════════════════════════════════════════════
sim = {
    "cash":      CAPITAL_INITIAL,
    "initial":   CAPITAL_INITIAL,
    "positions": {},   # pos_key → position dict
    "trades":    [],
    "equity_history": [],  # [(timestamp, equity)]
}

memory = {
    "lessons":            [],
    "patterns_to_avoid":  [],
    "patterns_that_work": [],
    "confidence_threshold": CONFIDENCE_BASE,
    "total_wins":   0,
    "total_losses": 0,
}

bot_state = {
    "running":      False,
    "last_heartbeat": None,
    "cycle_count":  0,
    "trades_today": 0,
    "last_monitor": 0,
    "last_scalp":   0,
    "last_deep":    0,
    "last_status":  0,
    "nitter_idx":   0,
    "yt_idx":       0,
    "last_micro":   0,
}

_main_loop = None
_app       = None

# ═══════════════════════════════════════════════════════════════
#  BASE DE DONNÉES SQLite
# ═══════════════════════════════════════════════════════════════
def init_db():
    con = sqlite3.connect(DB_FILE)
    c   = con.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS trades(
        id INTEGER PRIMARY KEY,
        symbol TEXT, market TEXT, side TEXT,
        price_in REAL, price_out REAL,
        qty REAL, amount_usd REAL,
        pnl REAL, pnl_pct REAL,
        confidence INTEGER, reason TEXT, exit_reason TEXT,
        duration_min INTEGER, time_in TEXT, time_out TEXT,
        patterns TEXT, leverage INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS lessons(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER, symbol TEXT, market TEXT,
        pnl REAL, lecon TEXT, pattern TEXT,
        action_future TEXT, type TEXT, date TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS equity(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, equity REAL, cash REAL,
        open_positions INTEGER, daily_pnl REAL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS trading_rules(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule TEXT, condition TEXT, action TEXT,
        win_rate REAL, sample_size INTEGER,
        created_date TEXT, last_updated TEXT,
        active INTEGER DEFAULT 1
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS trader_signals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT, author TEXT, content TEXT,
        sentiment TEXT, symbol TEXT, strength INTEGER,
        timestamp TEXT, url TEXT, hash TEXT UNIQUE
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS strategy_tests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_name TEXT, params TEXT,
        trades INTEGER, win_rate REAL, total_pnl REAL,
        sharpe REAL, tested_date TEXT, active INTEGER DEFAULT 0
    )""")
    con.commit(); con.close()


def db_save_trade(t: dict):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT OR REPLACE INTO trades VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            t["id"], t["symbol"], t["market"], t["side"],
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
        print(f"[DB-L] {e}")


def db_save_equity(equity, cash, open_pos, daily_pnl):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT INTO equity
            (timestamp,equity,cash,open_positions,daily_pnl)
            VALUES(?,?,?,?,?)""", (
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            round(equity,2), round(cash,2),
            open_pos, round(daily_pnl,2),
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
        return round(sum(1 for r in rows if r[0]>0)/len(rows)*100, 1)
    except Exception:
        return 50.0


def db_symbol_stats() -> list:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute("""
            SELECT symbol, COUNT(*) n, AVG(pnl) avg_pnl,
                   SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)*100.0/COUNT(*) wr
            FROM trades WHERE pnl IS NOT NULL
            GROUP BY symbol ORDER BY avg_pnl DESC LIMIT 5
        """).fetchall(); con.close()
        return [{"s": r[0].replace("USDT",""),
                 "n": r[1], "pnl": round(r[2],2),
                 "wr": round(r[3],0)} for r in rows]
    except Exception:
        return []


def db_best_patterns(symbol: str) -> list:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute("""
            SELECT pattern FROM lessons
            WHERE symbol=? AND type='succes'
            GROUP BY pattern ORDER BY COUNT(*) DESC LIMIT 5
        """, (symbol,)).fetchall(); con.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


def db_worst_patterns(symbol: str) -> list:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute("""
            SELECT pattern FROM lessons
            WHERE symbol=? AND type='erreur'
            GROUP BY pattern ORDER BY COUNT(*) DESC LIMIT 5
        """, (symbol,)).fetchall(); con.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
#  PERSISTANCE JSON
# ═══════════════════════════════════════════════════════════════
def save_data():
    try:
        DATA_FILE.write_text(
            json.dumps({"sim": sim, "memory": memory},
                       indent=2, default=str))
    except Exception as e:
        print(f"[SAVE] {e}")


def load_data():
    global sim, memory
    if DATA_FILE.exists():
        try:
            d = json.loads(DATA_FILE.read_text())
            sim    = d.get("sim", {})
            memory = d.get("memory", {})
            for k, v in {
                "cash": CAPITAL_INITIAL, "initial": CAPITAL_INITIAL,
                "positions": {}, "trades": [], "equity_history": []
            }.items():
                sim.setdefault(k, v)
            for k, v in {
                "lessons": [], "patterns_to_avoid": [],
                "patterns_that_work": [],
                "confidence_threshold": CONFIDENCE_BASE,
                "total_wins": 0, "total_losses": 0,
            }.items():
                memory.setdefault(k, v)
            n = len(sim["trades"])
            print(f"[LOAD] {n} trades | {len(memory['lessons'])} leçons")
            return
        except Exception as e:
            print(f"[LOAD] {e}")
    sim    = {"cash": CAPITAL_INITIAL, "initial": CAPITAL_INITIAL,
              "positions": {}, "trades": [], "equity_history": []}
    memory = {"lessons": [], "patterns_to_avoid": [],
              "patterns_that_work": [],
              "confidence_threshold": CONFIDENCE_BASE,
              "total_wins": 0, "total_losses": 0}
    print(f"[LOAD] Nouveau portefeuille ${CAPITAL_INITIAL:,.0f}")


# ═══════════════════════════════════════════════════════════════
#  DONNÉES DE MARCHÉ EN TEMPS RÉEL
# ═══════════════════════════════════════════════════════════════
_price_cache: dict = {}

def get_price(symbol: str, force=False) -> float:
    now = time.time()
    if not force and symbol in _price_cache:
        ts, p = _price_cache[symbol]
        if now - ts < 8:
            return p
    try:
        r = bybit.get_tickers(category="spot", symbol=symbol)
        p = float(r["result"]["list"][0]["lastPrice"])
        _price_cache[symbol] = (now, p)
        return p
    except Exception:
        return _price_cache.get(symbol, (0, 0.0))[1]


def get_prices_batch() -> dict:
    prices = {}
    try:
        r = bybit.get_tickers(category="spot")
        for item in r["result"]["list"]:
            if item["symbol"] in ALL_SYMBOLS:
                p = float(item["lastPrice"])
                prices[item["symbol"]] = p
                _price_cache[item["symbol"]] = (time.time(), p)
    except Exception as e:
        print(f"[PRICE] {e}")
    return prices


def get_klines(symbol: str, interval: str, limit=100) -> pd.Series:
    try:
        r = bybit.get_kline(category="spot", symbol=symbol,
                            interval=interval, limit=limit)
        return pd.Series(
            [float(c[4]) for c in reversed(r["result"]["list"])],
            dtype=float)
    except Exception:
        return pd.Series(dtype=float)


def get_volume_data(symbol: str, interval="5", limit=20) -> list:
    try:
        r = bybit.get_kline(category="spot", symbol=symbol,
                            interval=interval, limit=limit)
        return [float(c[5]) for c in reversed(r["result"]["list"])]
    except Exception:
        return []


def get_fear_greed() -> str:
    try:
        d = requests.get("https://api.alternative.me/fng/", timeout=5).json()["data"][0]
        return f"Fear&Greed: {d['value']}/100 ({d['value_classification']})"
    except Exception:
        return "Fear&Greed: N/A"


def get_order_book(symbol: str) -> dict:
    try:
        ob    = bybit.get_orderbook(category="spot", symbol=symbol, limit=20)
        bids  = sum(float(b[1]) for b in ob["result"]["b"])
        asks  = sum(float(a[1]) for a in ob["result"]["a"])
        ratio = round(bids/asks, 2) if asks > 0 else 1.0
        return {
            "ratio":    ratio,
            "pressure": "acheteurs" if ratio>1.3 else "vendeurs" if ratio<0.77 else "neutre"
        }
    except Exception:
        return {"ratio": 1.0, "pressure": "N/A"}


# ═══════════════════════════════════════════════════════════════
#  PRIX YAHOO FINANCE (Actions US, Forex, Matières premières)
# ═══════════════════════════════════════════════════════════════
_yahoo_cache: dict = {}

def get_yahoo_price(ticker: str) -> float:
    """Prix temps réel via Yahoo Finance (gratuit, pas d'API key)."""
    now = time.time()
    if ticker in _yahoo_cache:
        ts, p = _yahoo_cache[ticker]
        if now - ts < 30:  # cache 30s
            return p
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d"
        r   = requests.get(url, timeout=8,
                           headers={"User-Agent": "Mozilla/5.0"})
        d   = r.json()
        p   = float(d["chart"]["result"][0]["meta"]["regularMarketPrice"])
        _yahoo_cache[ticker] = (now, p)
        return p
    except Exception:
        return _yahoo_cache.get(ticker, (0, 0.0))[1]


def get_yahoo_closes(ticker: str, interval="1m", range_="1d") -> pd.Series:
    """Historique de prix Yahoo Finance pour calcul d'indicateurs."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval={interval}&range={range_}"
        r   = requests.get(url, timeout=10,
                           headers={"User-Agent": "Mozilla/5.0"})
        d   = r.json()
        closes = d["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        return pd.Series([c for c in closes if c is not None], dtype=float)
    except Exception:
        return pd.Series(dtype=float)


def scan_yahoo_market(market_dict: dict, market_name: str) -> list:
    """Scanne actions/forex/commodités et retourne les opportunités."""
    opps = []
    for ticker, name in market_dict.items():
        try:
            closes = get_yahoo_closes(ticker, "5m", "1d")
            if len(closes) < 27:
                continue
            ind   = compute_indicators(closes)
            if not ind:
                continue
            price = get_yahoo_price(ticker)
            if not price:
                continue

            score = 0
            if ind["rsi"] < 35:   score += 3
            elif ind["rsi"] < 45: score += 1
            if ind["rsi"] > 70:   score -= 3
            if ind["macd_h"] > 0: score += 2
            else:                 score -= 1
            if ind["mom5"] > 0.5: score += 2
            elif ind["mom5"] < -0.5: score -= 2

            if abs(score) >= 2:
                opps.append({
                    "symbol":    ticker,
                    "name":      name,
                    "market_type": market_name,
                    "price":     price,
                    "score":     score,
                    "direction": "BUY" if score > 0 else "SELL",
                    "ind":       ind,
                    "patterns":  [],
                    "has_alert": False,
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
        # RSI
        delta = closes.diff()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)
        rs    = (gain.ewm(com=13, adjust=False).mean() /
                 loss.ewm(com=13, adjust=False).mean().replace(0, np.nan))
        rsi   = float((100 - 100/(1+rs)).iloc[-1])

        # EMAs
        ema9  = float(closes.ewm(span=9,  adjust=False).mean().iloc[-1])
        ema20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])

        # MACD
        macd_l = float((closes.ewm(span=12,adjust=False).mean() -
                        closes.ewm(span=26,adjust=False).mean()).iloc[-1])
        macd_s = float((closes.ewm(span=12,adjust=False).mean() -
                        closes.ewm(span=26,adjust=False).mean())
                       .ewm(span=9,adjust=False).mean().iloc[-1])
        macd_h = round(macd_l - macd_s, 6)

        # Bollinger
        sma20  = closes.rolling(20).mean()
        std20  = closes.rolling(20).std()
        bb_up  = float((sma20 + 2*std20).iloc[-1])
        bb_low = float((sma20 - 2*std20).iloc[-1])
        bb_pct = round((float(closes.iloc[-1])-bb_low)/(bb_up-bb_low)*100, 1) \
                 if bb_up != bb_low else 50.0

        # Momentum
        mom5  = float((closes.iloc[-1]-closes.iloc[-6])/closes.iloc[-6]*100) \
                if len(closes)>=6 else 0.0
        mom15 = float((closes.iloc[-1]-closes.iloc[-16])/closes.iloc[-16]*100) \
                if len(closes)>=16 else 0.0

        # Volatilité
        vol = float(closes.pct_change().dropna().iloc[-10:].std()*100) \
              if len(closes)>=10 else 0.0

        price = float(closes.iloc[-1])
        return {
            "rsi":      round(rsi, 1),
            "ema9":     round(ema9, 6),
            "ema20":    round(ema20, 6),
            "ema50":    round(ema50, 6),
            "macd_h":   macd_h,
            "bb_pct":   bb_pct,
            "mom5":     round(mom5, 3),
            "mom15":    round(mom15, 3),
            "vol":      round(vol, 3),
            "trend":    "↑" if ema20>ema50 else "↓",
            "ema_cross": "BULL" if ema9>ema20 else "BEAR",
            "price":    price,
        }
    except Exception as e:
        print(f"[IND] {e}")
        return {}


def get_multi_tf(symbol: str) -> dict:
    """Indicateurs sur 1min, 5min, 15min."""
    result = {}
    for interval, label in [("1","1m"), ("5","5m"), ("15","15m")]:
        closes = get_klines(symbol, interval, 80)
        if not closes.empty:
            ind = compute_indicators(closes)
            if ind:
                result[label] = ind
    return result


def tf_score(mtf: dict) -> dict:
    """Score de confluence -9 à +9."""
    score = 0
    sigs  = []
    for tf, ind in mtf.items():
        rsi   = ind.get("rsi", 50)
        macd  = ind.get("macd_h", 0)
        mom5  = ind.get("mom5", 0)
        cross = ind.get("ema_cross", "BEAR")

        if rsi < 32:   score += 2; sigs.append(f"{tf}:RSI_survente")
        elif rsi < 45: score += 1
        elif rsi > 68: score -= 2; sigs.append(f"{tf}:RSI_surachat")
        elif rsi > 55: score -= 1

        if macd > 0:   score += 1; sigs.append(f"{tf}:MACD↑")
        else:          score -= 1

        if mom5 > 0.5: score += 1
        elif mom5 < -0.5: score -= 1

        if cross == "BULL": score += 1; sigs.append(f"{tf}:EMA_bull")
        else:               score -= 1

    direction = "LONG" if score >= 4 else "SHORT" if score <= -4 else "NEUTRE"
    return {"score": score, "direction": direction, "signals": sigs[:6]}


# ═══════════════════════════════════════════════════════════════
#  DÉTECTION DE PATTERNS
# ═══════════════════════════════════════════════════════════════
def detect_patterns(symbol: str, ind: dict, vols: list) -> list:
    patterns = []
    try:
        price    = ind.get("price", 0)
        rsi      = ind.get("rsi", 50)
        mom5     = ind.get("mom5", 0)
        mom15    = ind.get("mom15", 0)
        bb_pct   = ind.get("bb_pct", 50)
        macd_h   = ind.get("macd_h", 0)
        ema_cross= ind.get("ema_cross", "BEAR")

        avg_vol  = sum(vols[:-1])/max(len(vols)-1,1) if vols else 0
        last_vol = vols[-1] if vols else 0
        vol_ratio= last_vol/avg_vol if avg_vol>0 else 1

        # Survente extrême
        if rsi < 28 and bb_pct < 5:
            patterns.append({"name":"Survente extrême","signal":"BUY",
                              "strength":"fort","score":3})

        # Surachat extrême
        elif rsi > 72 and bb_pct > 95:
            patterns.append({"name":"Surachat extrême","signal":"SELL",
                              "strength":"fort","score":3})

        # Breakout haussier
        if mom5 > 1.2 and vol_ratio > 2.0 and macd_h > 0:
            patterns.append({"name":"Breakout haussier","signal":"BUY",
                              "strength":"fort","score":3})

        # Breakdown baissier
        elif mom5 < -1.2 and vol_ratio > 2.0 and macd_h < 0:
            patterns.append({"name":"Breakdown baissier","signal":"SELL",
                              "strength":"fort","score":3})

        # EMA cross haussier
        if ema_cross == "BULL" and macd_h > 0 and rsi < 60:
            patterns.append({"name":"EMA Cross Bull","signal":"BUY",
                              "strength":"modéré","score":2})

        # EMA cross baissier
        elif ema_cross == "BEAR" and macd_h < 0 and rsi > 40:
            patterns.append({"name":"EMA Cross Bear","signal":"SELL",
                              "strength":"modéré","score":2})

        # Momentum continu
        if mom5 > 0.8 and mom15 > 2.0:
            patterns.append({"name":"Momentum haussier continu","signal":"BUY",
                              "strength":"modéré","score":2})
        elif mom5 < -0.8 and mom15 < -2.0:
            patterns.append({"name":"Momentum baissier continu","signal":"SELL",
                              "strength":"modéré","score":2})

        # ⚠️ Pump & Dump
        if abs(mom5) > 4 and vol_ratio > 5:
            patterns.append({"name":"⚠️ Pump/Dump","signal":"HOLD",
                              "strength":"ALERTE",
                              "desc":f"{mom5:+.1f}% en 5min, vol x{vol_ratio:.1f}"})

    except Exception as e:
        print(f"[PAT] {e}")
    return patterns


# ═══════════════════════════════════════════════════════════════
#  SCAN D'OPPORTUNITÉS
# ═══════════════════════════════════════════════════════════════
def scan_market() -> list:
    """
    Scanne tous les symboles, retourne les top opportunités
    triées par score de signal.
    """
    opps   = []
    prices = get_prices_batch()

    for symbol in ALL_SYMBOLS:
        try:
            price  = prices.get(symbol, 0)
            if not price:
                continue
            closes = get_klines(symbol, "5", 60)
            if len(closes) < 27:
                continue
            ind  = compute_indicators(closes)
            if not ind:
                continue
            vols = get_volume_data(symbol, "5", 15)
            pats = detect_patterns(symbol, ind, vols)

            # Score total
            score = 0
            if ind["rsi"] < 35:   score += 3
            elif ind["rsi"] < 45: score += 1
            if ind["rsi"] > 70:   score -= 3
            elif ind["rsi"] > 60: score -= 1
            if ind["macd_h"] > 0: score += 2
            else:                 score -= 1
            if ind["mom5"] > 1:   score += 2
            elif ind["mom5"] < -1:score -= 2
            if ind["ema_cross"] == "BULL": score += 1
            else:                          score -= 1

            direction = "BUY" if score > 0 else "SELL"

            # Bloque si alerte P&D
            has_alert = any(p["signal"]=="HOLD" for p in pats)

            opps.append({
                "symbol":    symbol,
                "price":     price,
                "score":     score,
                "direction": direction,
                "ind":       ind,
                "patterns":  pats,
                "has_alert": has_alert,
            })
        except Exception:
            pass

    opps.sort(key=lambda x: abs(x["score"]), reverse=True)
    return opps[:10]


# ═══════════════════════════════════════════════════════════════
#  VOTE MAJORITAIRE IA
# ═══════════════════════════════════════════════════════════════
# Compteur de tokens pour éviter le rate limit
_token_usage = {"count": 0, "reset_time": time.time()}

def _check_token_budget() -> bool:
    """Vérifie si on peut encore faire un appel IA aujourd'hui."""
    now = time.time()
    # Reset quotidien
    if now - _token_usage["reset_time"] > 86400:
        _token_usage["count"]      = 0
        _token_usage["reset_time"] = now
    # Budget conservateur : max 80k tokens/jour (marge de sécurité)
    return _token_usage["count"] < 80000


def ask_model_single(prompt: str, model: str = None) -> dict:
    """
    1 appel IA avec gestion intelligente du quota.
    - Utilise le modèle rapide (8B) pour économiser les tokens
    - Bascule sur le backup si rate limit atteint
    - Retourne HOLD si budget épuisé
    """
    if not _check_token_budget():
        print("[AI] Budget tokens épuisé — HOLD forcé jusqu'à minuit")
        return {"signal":"HOLD","confidence":0,"reason":"budget_epuise","risk":"HIGH"}

    # Utilise le modèle rapide par défaut pour économiser les tokens
    if model is None:
        model = GROQ_FAST_MODEL

    # Essaie 2 modèles en cas d'erreur
    models_to_try = [model, GROQ_PRIMARY_MODEL if model != GROQ_PRIMARY_MODEL else GROQ_SECONDARY_MODEL]

    for m in models_to_try:
        try:
            r = groq_client.chat.completions.create(
                model=m,
                max_tokens=80,   # réduit à 80 pour économiser tokens
                temperature=0.1,
                messages=[
                    {"role":"system",
                     "content":"Trading expert. JSON only: {signal,confidence,reason,risk,market}"},
                    {"role":"user","content":prompt[:600]}  # limite prompt à 600 chars
                ],
            )
            # Estime l'usage tokens
            tokens_used = len(prompt[:600].split()) * 1.3 + 80
            _token_usage["count"] += int(tokens_used)

            t = r.choices[0].message.content.strip()
            t = t.replace("```json","").replace("```","").strip()
            s = t.find("{")
            e = t.rfind("}") + 1
            if s >= 0 and e > s:
                t = t[s:e]
            result = json.loads(t)
            if result.get("signal") not in ("BUY","SELL","HOLD"):
                result["signal"] = "HOLD"
            return result
        except json.JSONDecodeError:
            return {"signal":"HOLD","confidence":0,"reason":"json_error","risk":"HIGH"}
        except Exception as e:
            err = str(e)
            if "rate_limit" in err or "429" in err:
                print(f"[AI-LIMIT] {m} rate limit — essai modèle suivant")
                continue
            if "decommissioned" in err or "400" in err:
                print(f"[AI-OLD] {m} désactivé — essai modèle suivant")
                continue
            print(f"[AI-ERR] {m}: {err[:60]}")
            return {"signal":"HOLD","confidence":0,"reason":"api_error","risk":"HIGH"}

    return {"signal":"HOLD","confidence":0,"reason":"all_models_failed","risk":"HIGH"}


def vote(prompt: str) -> dict:
    """
    1 seul appel IA avec le modèle rapide 8B.
    Économise ~6x les tokens vs llama-70b.
    Signal fort (>60%) → validation par le 70b.
    """
    r1 = ask_model_single(prompt, GROQ_FAST_MODEL)

    if r1.get("reason") in ("budget_epuise", "all_models_failed"):
        return {**r1, "votes": [r1["signal"]], "consensus": "0/1"}

    if r1["signal"] == "HOLD" or r1.get("confidence", 0) < 60:
        return {**r1, "votes": [r1["signal"]], "consensus": "1/1"}

    # Signal fort → validation par le modèle puissant
    r2 = ask_model_single(prompt, GROQ_PRIMARY_MODEL)

    if r2["signal"] == r1["signal"]:
        conf = min(95, round((r1.get("confidence",0) + r2.get("confidence",0))/2) + 5)
        return {
            "signal":     r1["signal"],
            "confidence": conf,
            "reason":     r2.get("reason", r1.get("reason","")),
            "risk":       r1.get("risk","MEDIUM"),
            "market":     r1.get("market","SPOT"),
            "votes":      [r1["signal"], r2["signal"]],
            "consensus":  "2/2",
        }
    return {
        "signal":"HOLD","confidence":0,
        "reason":f"Désaccord ({r1['signal']}/{r2['signal']})",
        "risk":"HIGH",
        "votes":[r1["signal"],r2["signal"]],
        "consensus":"0/2",
    }


# ═══════════════════════════════════════════════════════════════
#  ANALYSE COMPLÈTE D'UN SYMBOLE
# ═══════════════════════════════════════════════════════════════
def analyze(opp: dict, fear_greed: str) -> dict:
    symbol = opp["symbol"]
    price  = opp["price"]
    ind    = opp["ind"]
    pats   = opp["patterns"]
    score  = opp["score"]

    mtf    = get_multi_tf(symbol)
    conf   = tf_score(mtf)
    ob     = get_order_book(symbol)
    in_pos = any(p["symbol"]==symbol for p in sim["positions"].values())

    best_p  = db_best_patterns(symbol)
    worst_p = db_worst_patterns(symbol)
    thresh  = memory.get("confidence_threshold", CONFIDENCE_BASE)

    pat_names_buy  = [p["name"] for p in pats if p["signal"]=="BUY"]
    pat_names_sell = [p["name"] for p in pats if p["signal"]=="SELL"]
    pat_alerts     = [p for p in pats if p["signal"]=="HOLD"]

    # Règles auto-générées par le bot
    my_rules = get_active_rules()

    # Extraire valeur Fear&Greed pour appliquer règles Saylor/Buffett
    fg_value = 50
    try:
        fg_value = int(fear_greed.split(":")[1].split("/")[0].strip())
    except Exception:
        pass

    # Règle Saylor/Buffett : Fear&Greed bas = opportunité
    fg_context = ""
    if fg_value < 20:
        fg_context = "⚠️ EXTREME FEAR → Saylor/Buffett disent : C'EST LE MOMENT D'ACHETER"
    elif fg_value < 35:
        fg_context = "Fear élevé → opportunité selon Buffett (sois avide quand les autres ont peur)"

    # Signaux traders récents depuis la DB
    trader_sigs = get_db_trader_signals_summary()

    prompt = f"""{symbol} ${price:.4f}
RSI:{ind.get('rsi','?')} MACD:{ind.get('macd_h','?')} mom5:{ind.get('mom5','?')}% BB:{ind.get('bb_pct','?')}% trend:{ind.get('trend','?')}
OB:{ob['pressure']} TFscore:{conf['score']}/9
{fear_greed} {fg_context}
Historique gains:{best_p[:2]} erreurs:{worst_p[:2]}

SIGNAUX TRADERS EN TEMPS RÉEL:
{trader_sigs[:400] if trader_sigs else 'Aucun signal collecté'}

{my_rules}

Règles de trading universelles: {TRADER_PHILOSOPHIES[:300]}

Décide BUY/SELL/HOLD. BUY=spot, SELL=futures short.
JSON: {{"signal":"BUY/SELL/HOLD","confidence":0-100,"reason":"raison courte","risk":"LOW/MEDIUM/HIGH","market":"SPOT/FUTURES"}}"""

    result = vote(prompt)
    result["symbol"]   = symbol
    result["price"]    = price
    result["patterns"] = pats
    result["confluence"] = conf
    result["ob"]       = ob
    result["ind"]      = ind
    return result


# ═══════════════════════════════════════════════════════════════
#  GESTION DES POSITIONS SIMULÉES
# ═══════════════════════════════════════════════════════════════
def calc_position_size(confidence: int, market: str) -> float:
    """Kelly criterion simplifié → % du cash."""
    wr  = db_win_rate(20) / 100
    wr  = max(0.4, wr)
    r   = TAKE_PROFIT_PCT / STOP_LOSS_PCT
    k   = max(0.05, min(MAX_PCT_PER_TRADE, wr - (1-wr)/r))
    k  *= (0.5 + 0.5 * (confidence-55)/30)
    if market == "FUTURES": k *= 0.6
    return round(min(MAX_PCT_PER_TRADE, max(0.05, k)), 2)


def open_trade(analysis: dict, send_fn) -> dict | None:
    symbol    = analysis["symbol"]
    price     = analysis["price"]
    signal    = analysis["signal"]
    conf      = analysis["confidence"]
    reason    = analysis["reason"]
    market    = analysis.get("market", "SPOT")
    pats      = analysis.get("patterns", [])
    side      = "LONG" if signal=="BUY" else "SHORT"

    # Vérifications
    if signal == "SELL" and market == "SPOT":
        return None  # pas de short sur spot
    if any(p["symbol"]==symbol for p in sim["positions"].values()):
        return None
    if len(sim["positions"]) >= MAX_POSITIONS:
        return None
    if sim["cash"] < 20:
        return None

    leverage = LEVERAGE_SIM if market=="FUTURES" else 1
    # Mode apprentissage forcé → taille réduite
    if analysis.get("_forced_pct"):
        pct = analysis["_forced_pct"]
    else:
        pct = calc_position_size(conf, market)
    amount   = sim["cash"] * pct
    qty      = amount / price

    sim["cash"] -= amount

    trade = {
        "id":          len(sim["trades"]) + 1,
        "symbol":      symbol,
        "market":      market,
        "side":        side,
        "price_in":    price,
        "price_out":   None,
        "qty":         qty,
        "amount_usd":  amount,
        "confidence":  conf,
        "reason":      reason,
        "exit_reason": None,
        "time_in":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "time_out":    None,
        "pnl":         None,
        "pnl_pct":     None,
        "duration_min": None,
        "patterns":    [p["name"] for p in pats if p.get("signal")!="HOLD"],
        "leverage":    leverage,
        "peak_price":  price,  # pour trailing stop LONG
        "trough_price": price, # pour trailing stop SHORT
    }

    pos_key = f"{market}_{symbol}_{side}_{trade['id']}"
    sim["trades"].append(trade)
    sim["positions"][pos_key] = {**trade, "pos_key": pos_key}
    db_save_trade(trade)
    save_data()
    bot_state["trades_today"] += 1

    # Calcul niveaux
    sl = price*(1-STOP_LOSS_PCT)    if side=="LONG" else price*(1+STOP_LOSS_PCT)
    tp = price*(1+TAKE_PROFIT_PCT)  if side=="LONG" else price*(1-TAKE_PROFIT_PCT)
    m_emoji = "📊" if market=="FUTURES" else "💱"
    s_emoji = "🟢" if side=="LONG" else "🔴"

    coin     = symbol.replace("USDT","")
    name     = analysis.get("name", coin)   # nom lisible si dispo
    learning = "🎓 APPRENTISSAGE" if analysis.get("_forced_pct") else ""
    mtype    = analysis.get("market_type", market)

    if mtype in ("STOCK","FOREX","COMMODITY"):
        asset_label = f"{name} ({mtype})"
    else:
        asset_label = f"{coin} (Crypto)"

    send_fn(
        f"{s_emoji} J'achète {asset_label} {learning}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Prix actuel    : ${price:.4f}\n"
        f"💰 Mise simulée   : ${amount:.2f} sur ${sim['cash']+amount:.2f} dispo\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 Je coupe si ça descend à ${sl:.4f} (-{STOP_LOSS_PCT*100:.1f}%)\n"
        f"🎯 Je prends mes gains à ${tp:.4f} (+{TAKE_PROFIT_PCT*100:.1f}%)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 Pourquoi      : {reason[:120]}\n"
        f"🔒 Niveau de confiance : {conf}%\n"
        f"🆔 Trade #{trade['id']}"
    )
    return trade


def close_trade(pos_key: str, price: float, reason: str, send_fn) -> dict | None:
    pos = sim["positions"].pop(pos_key, None)
    if not pos:
        return None

    side   = pos["side"]
    entry  = pos["price_in"]
    amt    = pos["amount_usd"]
    lev    = pos.get("leverage", 1)

    if side == "LONG":
        pnl     = (price - entry) / entry * amt * lev
        pnl_pct = (price - entry) / entry * 100 * lev
    else:
        pnl     = (entry - price) / entry * amt * lev
        pnl_pct = (entry - price) / entry * 100 * lev

    sim["cash"] += amt + pnl

    # Durée
    duration = 0
    try:
        t_in     = datetime.strptime(pos["time_in"], "%Y-%m-%d %H:%M:%S")
        duration = int((datetime.now()-t_in).total_seconds()/60)
    except Exception:
        pass

    # Màj trade
    trade = next((t for t in reversed(sim["trades"])
                  if t["id"]==pos["id"]), None)
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

    if pnl > 0:
        memory["total_wins"] = memory.get("total_wins",0) + 1
    else:
        memory["total_losses"] = memory.get("total_losses",0) + 1

    save_data()

    e_pnl  = "🤑" if pnl>0 else "💸"
    e_main = "✅" if pnl>0 else "❌"
    chg    = (price-entry)/entry*100

    coin       = pos["symbol"].replace("USDT","")
    name       = pos.get("name", coin)
    dur_str    = f"{duration} min" if duration >= 1 else f"{int((datetime.now()-datetime.strptime(pos['time_in'],'%Y-%m-%d %H:%M:%S')).total_seconds())}s"
    equity_now = get_equity()
    pnl_total  = equity_now - sim["initial"]

    verdict = (
        f"Excellent ! +${pnl:.2f} en {dur_str} 🚀" if pnl_pct > 3 else
        f"Bon trade ! +${pnl:.2f} en {dur_str} 👍" if pnl > 0 else
        f"Petit gain +${pnl:.2f} en {dur_str} 🙂" if pnl > 0 else
        f"Petite perte ${pnl:.2f} — j'apprends 📚" if pnl > -5 else
        f"Stop-loss déclenché ${pnl:.2f} — capital protégé 🛡️"
    )

    send_fn(
        f"{e_main} {name} vendu — Trade #{pos['id']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  Acheté à  : ${entry:.4f}\n"
        f"  Vendu à   : ${price:.4f} ({chg:+.2f}%)\n"
        f"  Durée     : {dur_str}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{e_pnl} Résultat   : ${pnl:+.4f}\n"
        f"  {verdict}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Capital total : ${equity_now:.2f} ({pnl_total:+.2f} depuis le début)\n"
        f"📌 Raison sortie : {reason}\n"
        f"⏳ J'analyse ce trade pour m'améliorer..."
    )
    return trade


# ═══════════════════════════════════════════════════════════════
#  SURVEILLANCE POSITIONS (30s)
# ═══════════════════════════════════════════════════════════════
def monitor_positions(send_fn):
    if not sim["positions"]:
        return
    prices = get_prices_batch()

    for pos_key, pos in list(sim["positions"].items()):
        symbol = pos["symbol"]
        side   = pos["side"]
        entry  = pos["price_in"]
        lev    = pos.get("leverage", 1)
        price  = prices.get(symbol) or get_price(symbol)
        if not price:
            continue

        # Màj trailing
        if side == "LONG":
            pos["peak_price"] = max(pos.get("peak_price", entry), price)
            change = (price - entry) / entry
            trailing = (pos["peak_price"] - price) / pos["peak_price"]
        else:
            pos["trough_price"] = min(pos.get("trough_price", entry), price)
            change = (entry - price) / entry
            trailing = (price - pos["trough_price"]) / pos["trough_price"]

        reason = None
        if change * lev <= -STOP_LOSS_PCT:
            reason = f"🛑 STOP-LOSS ({change*100*lev:+.2f}%)"
        elif change * lev >= TAKE_PROFIT_PCT:
            reason = f"🎯 TAKE-PROFIT ({change*100*lev:+.2f}%)"
        elif change * lev > 0.008 and trailing >= TRAILING_PCT:
            reason = f"📐 TRAILING ({trailing*100:.2f}% du pic)"

        if reason:
            close_trade(pos_key, price, reason, send_fn)


# ═══════════════════════════════════════════════════════════════
#  ÉQUITÉ EN TEMPS RÉEL
# ═══════════════════════════════════════════════════════════════
def get_equity() -> float:
    prices = get_prices_batch()
    equity = sim["cash"]
    for pos in sim["positions"].values():
        p = prices.get(pos["symbol"], pos["price_in"])
        if pos["side"] == "LONG":
            equity += pos["amount_usd"] + (p-pos["price_in"])/pos["price_in"] * pos["amount_usd"] * pos.get("leverage",1)
        else:
            equity += pos["amount_usd"] + (pos["price_in"]-p)/pos["price_in"] * pos["amount_usd"] * pos.get("leverage",1)
    return equity


def get_stats() -> dict:
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    if not closed:
        return {"total":0,"wins":0,"losses":0,"win_rate":0,
                "best":0,"worst":0,"total_pnl":0,"avg_dur":0}
    pnls = [t["pnl"] for t in closed]
    wins = [p for p in pnls if p>0]
    durs = [t.get("duration_min",0) for t in closed if t.get("duration_min")]
    return {
        "total":    len(closed),
        "wins":     len(wins),
        "losses":   len(closed)-len(wins),
        "win_rate": round(len(wins)/len(closed)*100, 1),
        "best":     round(max(pnls), 4),
        "worst":    round(min(pnls), 4),
        "total_pnl":round(sum(pnls), 4),
        "avg_dur":  round(sum(durs)/len(durs), 1) if durs else 0,
    }


# ═══════════════════════════════════════════════════════════════
#  AUTO-AJUSTEMENT DU SEUIL
# ═══════════════════════════════════════════════════════════════
def auto_adjust():
    wr  = db_win_rate(20)
    cur = memory.get("confidence_threshold", CONFIDENCE_BASE)
    if wr > 62 and cur > CONFIDENCE_MIN:
        new = max(CONFIDENCE_MIN, cur-2)
    elif wr < 40 and cur < CONFIDENCE_MAX:
        new = min(CONFIDENCE_MAX, cur+3)
    else:
        new = cur
    memory["confidence_threshold"] = new
    return new


# ═══════════════════════════════════════════════════════════════
#  APPRENTISSAGE
# ═══════════════════════════════════════════════════════════════
def learn_from_trade(trade: dict, send_fn=None):
    if trade.get("pnl") is None:
        return
    try:
        verdict = "PERDANT" if trade["pnl"]<0 else "GAGNANT"
        prompt  = f"""Analyse ce trade simulé et tire une leçon précise.

{trade['symbol']} {trade['market']} {trade['side']}
${trade['price_in']:.6f} → ${trade['price_out']:.6f}
PnL: ${trade['pnl']:+.4f} ({trade.get('pnl_pct',0):+.2f}%) — {verdict}
Durée: {trade.get('duration_min',0)} min
Raison entrée: {trade['reason']}
Raison sortie: {trade.get('exit_reason','')}
Patterns: {trade.get('patterns',[])}
Confiance: {trade['confidence']}%

JSON strict (sans backticks):
{{"lecon":"leçon courte et actionnable","pattern":"pattern clé","action_future":"règle concrète","type":"erreur ou succes"}}"""

        r = groq_client.chat.completions.create(
            model=GROQ_FAST_MODEL, max_tokens=100, temperature=0.2,
            messages=[{"role":"user","content":prompt}],
        )
        lesson = json.loads(
            r.choices[0].message.content
            .replace("```json","").replace("```","").strip()
        )
        lesson.update({
            "trade_id": trade["id"],
            "pnl":      trade["pnl"],
            "symbol":   trade["symbol"],
            "market":   trade.get("market","SPOT"),
            "date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        memory["lessons"].append(lesson)
        db_save_lesson(lesson)

        key = "patterns_that_work" if lesson["type"]=="succes" else "patterns_to_avoid"
        memory[key].append(lesson["pattern"])
        memory["lessons"]           = memory["lessons"][-60:]
        memory["patterns_that_work"]= memory["patterns_that_work"][-25:]
        memory["patterns_to_avoid"] = memory["patterns_to_avoid"][-25:]

        new_thresh = auto_adjust()
        save_data()
        print(f"[LEARN] {lesson['lecon']}")

        if send_fn:
            stats = get_stats()
            e     = "✅" if lesson["type"]=="succes" else "❌"
            coin  = trade["symbol"].replace("USDT","")
            send_fn(
                f"📚 Leçon #{len(memory['lessons'])} — {coin}\n"
                f"{e} Ce que j'ai appris :\n"
                f"  {lesson['lecon']}\n"
                f"📌 Prochaine fois :\n"
                f"  {lesson['action_future']}\n"
                f"📊 Score global : {stats['win_rate']}% de trades gagnants "
                f"({stats['wins']}✅ / {stats['losses']}❌)"
            )
    except Exception as e:
        print(f"[LEARN] {e}")



# ═══════════════════════════════════════════════════════════════
#  MOTEUR MICRO-TRADING (sub-minute)
#  Logique purement algorithmique — pas d'appel IA pour la vitesse
#  Décision en <500ms sur signaux purs : tick, EMA, RSI, volume
# ═══════════════════════════════════════════════════════════════

# Cache des klines 1min pour éviter les appels répétés
_kline_cache_1m: dict = {}   # symbol → (timestamp, closes)

def get_klines_1m_cached(symbol: str) -> pd.Series:
    """Klines 1min avec cache de 5s."""
    now = time.time()
    if symbol in _kline_cache_1m:
        ts, closes = _kline_cache_1m[symbol]
        if now - ts < 5:
            return closes
    closes = get_klines(symbol, "1", 30)
    _kline_cache_1m[symbol] = (now, closes)
    return closes


def micro_signal(symbol: str, price: float) -> dict:
    """
    Signal micro-trading purement algorithmique — décision en <200ms.
    Aucun appel IA. Basé sur :
      1. EMA cross 1min (EMA5 vs EMA13)
      2. RSI 7 périodes (réactif)
      3. Spike de volume (x2.5 avg)
      4. Momentum 3 bougies
      5. Bollinger squeeze (prix près d'une bande)
      6. VWAP approximé (prix vs moyenne pondérée)
    Score de -6 à +6 → seuil ±4 pour trader
    """
    try:
        closes = get_klines_1m_cached(symbol)
        if len(closes) < 14:
            return {"signal": "HOLD", "score": 0, "conf": 0}

        # EMA5 / EMA13 cross
        ema5  = float(closes.ewm(span=5,  adjust=False).mean().iloc[-1])
        ema13 = float(closes.ewm(span=13, adjust=False).mean().iloc[-1])
        ema5_prev  = float(closes.ewm(span=5,  adjust=False).mean().iloc[-2])
        ema13_prev = float(closes.ewm(span=13, adjust=False).mean().iloc[-2])

        # RSI 7
        delta = closes.diff()
        gain  = delta.clip(lower=0).ewm(com=6, adjust=False).mean()
        loss  = (-delta).clip(lower=0).ewm(com=6, adjust=False).mean()
        rsi7  = float((100 - 100/(1 + gain/loss.replace(0, np.nan))).iloc[-1])

        # Momentum 3 bougies
        mom3 = (float(closes.iloc[-1]) - float(closes.iloc[-4]))                / float(closes.iloc[-4]) * 100

        # Bollinger 10 périodes
        sma10 = closes.rolling(10).mean()
        std10 = closes.rolling(10).std()
        bb_up = float((sma10 + 1.5*std10).iloc[-1])
        bb_lo = float((sma10 - 1.5*std10).iloc[-1])
        bb_pct = (price - bb_lo) / (bb_up - bb_lo) * 100 if bb_up != bb_lo else 50

        # Volumes (on utilise le cache 1min pour dériver une estimate)
        vols = get_volume_data(symbol, "1", 10)
        avg_vol   = sum(vols[:-1])/max(len(vols)-1,1) if len(vols)>1 else 1
        vol_ratio = vols[-1]/avg_vol if avg_vol>0 else 1.0

        # ── Scoring ─────────────────────────────────────────────
        score = 0

        # 1. EMA cross (signal le plus fort)
        if ema5_prev <= ema13_prev and ema5 > ema13:
            score += 2   # golden cross 1min
        elif ema5_prev >= ema13_prev and ema5 < ema13:
            score -= 2   # death cross 1min

        # 2. RSI7 zone
        if rsi7 < 28:   score += 2
        elif rsi7 < 40: score += 1
        elif rsi7 > 72: score -= 2
        elif rsi7 > 60: score -= 1

        # 3. Momentum 3 bougies
        if mom3 > 0.6:    score += 1
        elif mom3 < -0.6: score -= 1

        # 4. Position dans les Bollinger
        if bb_pct < 8:    score += 1   # près de la bande basse → rebond probable
        elif bb_pct > 92: score -= 1   # près de la bande haute → recul probable

        # 5. Volume spike confirme le signal
        if vol_ratio > 2.5 and score > 0: score += 1
        if vol_ratio > 2.5 and score < 0: score -= 1

        # ── Décision ────────────────────────────────────────────
        if score >= 4:
            signal = "BUY"
            conf   = min(95, 60 + score * 7)
        elif score <= -4:
            signal = "SELL"
            conf   = min(95, 60 + abs(score) * 7)
        else:
            signal = "HOLD"
            conf   = 0

        return {
            "signal":   signal,
            "score":    score,
            "conf":     conf,
            "rsi7":     round(rsi7, 1),
            "ema_cross": "BULL" if ema5>ema13 else "BEAR",
            "mom3":     round(mom3, 3),
            "bb_pct":   round(bb_pct, 1),
            "vol_ratio": round(vol_ratio, 2),
            "reason":   (f"EMA{'↑' if ema5>ema13 else '↓'} RSI7={rsi7:.0f} "
                         f"mom={mom3:+.2f}% bb={bb_pct:.0f}% vol={vol_ratio:.1f}x"),
        }

    except Exception as e:
        print(f"[MICRO] {symbol}: {e}")
        return {"signal": "HOLD", "score": 0, "conf": 0}


def open_micro_trade(symbol: str, price: float, signal: dict, send_fn) -> dict | None:
    """Ouvre un micro-trade simulé avec SL/TP ultra-serrés."""
    side    = "LONG" if signal["signal"]=="BUY" else "SHORT"
    conf    = signal["conf"]
    reason  = signal.get("reason","")
    score   = signal.get("score",0)

    # Pas de short sur spot en micro (trop risqué sans levier)
    if side == "SHORT":
        return None

    # Vérifications rapides
    micro_count = sum(1 for p in sim["positions"].values()
                      if p.get("trade_type")=="MICRO")
    if micro_count >= MAX_MICRO_POSITIONS:
        return None
    if any(p["symbol"]==symbol and p.get("trade_type")=="MICRO"
           for p in sim["positions"].values()):
        return None
    if sim["cash"] < 15:
        return None

    # Mode apprentissage : petit capital même à faible confiance
    is_learn = LEARN_MODE_ENABLED and signal["conf"] < MICRO_CONF_MIN
    amount_pct = LEARN_MODE_MAX_PCT if is_learn else MICRO_MAX_PCT
    amount = sim["cash"] * amount_pct
    qty    = amount / price
    sim["cash"] -= amount

    trade = {
        "id":           len(sim["trades"]) + 1,
        "symbol":       symbol,
        "market":       "MICRO",
        "side":         side,
        "trade_type":   "MICRO",
        "price_in":     price,
        "price_out":    None,
        "qty":          qty,
        "amount_usd":   amount,
        "confidence":   conf,
        "reason":       reason,
        "exit_reason":  None,
        "time_in":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "time_out":     None,
        "pnl":          None,
        "pnl_pct":      None,
        "duration_min": None,
        "patterns":     [f"score={score}"],
        "leverage":     1,
        "peak_price":   price,
        "trough_price": price,
        "micro_score":  score,
        "open_time":    time.time(),  # pour timeout 90s
    }

    pos_key = f"MICRO_{symbol}_{side}_{trade['id']}"
    sim["trades"].append(trade)
    sim["positions"][pos_key] = {**trade, "pos_key": pos_key}
    db_save_trade(trade)
    bot_state["trades_today"] += 1
    bot_state["micro_count"]  = bot_state.get("micro_count", 0) + 1

    sl = price * (1 - MICRO_SL_PCT)
    tp = price * (1 + MICRO_TP_PCT)

    coin = symbol.replace("USDT","")
    send_fn(
        f"⚡ Micro-trade {coin} — #{trade['id']}\n"
        f"  J'achète à ${price:.4f}\n"
        f"  Mise: ${amount:.2f} | Durée max: {MICRO_MAX_DURATION}s\n"
        f"  Signal algo: {reason[:70]}"
    )
    return trade


def monitor_micro_positions(send_fn):
    """
    Surveillance ultra-rapide des micro-trades.
    - SL -0.8% / TP +1.2% / Trailing -0.5%
    - Timeout 90s : ferme automatiquement si trop long
    - Fermeture sur signal contraire
    """
    now = time.time()
    prices = get_prices_batch()

    for pos_key, pos in list(sim["positions"].items()):
        if pos.get("trade_type") != "MICRO":
            continue

        symbol = pos["symbol"]
        price  = prices.get(symbol) or get_price(symbol, force=True)
        if not price:
            continue

        entry   = pos["price_in"]
        change  = (price - entry) / entry
        elapsed = now - pos.get("open_time", now)

        # Trailing stop
        pos["peak_price"] = max(pos.get("peak_price", entry), price)
        trailing = (pos["peak_price"] - price) / pos["peak_price"]

        reason = None

        # SL
        if change <= -MICRO_SL_PCT:
            reason = f"🛑 MICRO SL ({change*100:+.2f}%)"

        # TP
        elif change >= MICRO_TP_PCT:
            reason = f"🎯 MICRO TP ({change*100:+.2f}%)"

        # Trailing stop (si déjà en profit)
        elif change > 0.003 and trailing >= MICRO_TRAILING_PCT:
            reason = f"📐 MICRO TRAIL ({trailing*100:.2f}% du pic)"

        # Timeout
        elif elapsed >= MICRO_MAX_DURATION:
            pnl_now = change * pos["amount_usd"]
            reason  = f"⏱ TIMEOUT {int(elapsed)}s (PnL: ${pnl_now:+.4f})"

        # Signal contraire rapide
        elif elapsed > 20:
            sig = micro_signal(symbol, price)
            if sig["signal"] == "SELL" and pos["side"] == "LONG" and sig["score"] <= -4:
                reason = f"🔄 Signal contraire (score={sig['score']})"

        if reason:
            # Fermeture rapide
            side   = pos["side"]
            amt    = pos["amount_usd"]
            pnl    = (price - entry) / entry * amt
            pnl_pct= (price - entry) / entry * 100
            sim["cash"] += amt + pnl

            trade = next((t for t in reversed(sim["trades"])
                          if t["id"] == pos["id"]), None)
            if trade:
                dur = int(elapsed / 60 * 10) / 10  # durée en minutes
                trade.update({
                    "price_out":    price,
                    "time_out":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "pnl":          round(pnl, 6),
                    "pnl_pct":      round(pnl_pct, 3),
                    "exit_reason":  reason,
                    "duration_min": max(1, int(elapsed / 60)),
                })
                db_save_trade(trade)
                learn_from_trade(trade, send_fn=None)  # apprentissage silencieux

            del sim["positions"][pos_key]
            save_data()

            e     = "✅" if pnl>0 else "❌"
            coin  = symbol.replace("USDT","")
            chg   = (price-entry)/entry*100
            result_str = f"Gagné ${pnl:+.4f}" if pnl>0 else f"Perdu ${pnl:.4f}"
            send_fn(
                f"{e} Micro {coin} fermé — {result_str}\n"
                f"  ${entry:.4f} → ${price:.4f} ({chg:+.3f}%) | {reason}"
            )

            if pnl > 0:
                memory["total_wins"]   = memory.get("total_wins",0) + 1
            else:
                memory["total_losses"] = memory.get("total_losses",0) + 1


def run_micro_cycle(send_fn):
    """
    Cycle micro-trading : toutes les 8 secondes.
    Scanne MICRO_SYMBOLS avec l'algo pur (pas d'IA).
    Ultra-rapide, décision en <500ms par symbole.
    """
    prices = get_prices_batch()

    for symbol in MICRO_SYMBOLS:
        if not bot_state["running"]:
            break
        price = prices.get(symbol, 0)
        if not price:
            continue

        # Skip si déjà trop de positions micro
        micro_count = sum(1 for p in sim["positions"].values()
                          if p.get("trade_type")=="MICRO")
        if micro_count >= MAX_MICRO_POSITIONS:
            break

        # Skip si déjà une position micro sur ce symbole
        if any(p["symbol"]==symbol and p.get("trade_type")=="MICRO"
               for p in sim["positions"].values()):
            continue

        # Signal algorithmique pur
        sig = micro_signal(symbol, price)

        if sig["signal"] != "HOLD" and sig["conf"] >= MICRO_CONF_MIN:
            open_micro_trade(symbol, price, sig, send_fn)


# ═══════════════════════════════════════════════════════════════
#  SURVEILLANCE TRADERS EN TEMPS RÉEL
# ═══════════════════════════════════════════════════════════════

_signal_cache: set = set()  # hashes déjà vus

def _signal_hash(content: str) -> str:
    return hashlib.md5(content.encode()).hexdigest()[:12]


def scrape_nitter(username: str) -> list:
    """Scrape les derniers tweets via Nitter (miroir gratuit Twitter)."""
    signals = []
    for instance in NITTER_INSTANCES:
        try:
            url  = f"https://{instance}/{username}/rss"
            feed = feedparser.parse(url)
            if not feed.entries:
                continue
            for entry in feed.entries[:3]:
                text = entry.get("summary", entry.get("title", ""))
                # Nettoie le HTML
                text = re.sub(r'<[^>]+>', '', text).strip()
                if len(text) < 20:
                    continue
                h = _signal_hash(text)
                if h in _signal_cache:
                    continue
                _signal_cache.add(h)
                signals.append({
                    "source":  "Twitter",
                    "author":  username,
                    "content": text[:300],
                    "url":     entry.get("link",""),
                    "hash":    h,
                    "ts":      datetime.now().strftime("%Y-%m-%d %H:%M"),
                })
            break  # succès, pas besoin d'essayer d'autres instances
        except Exception:
            continue
    return signals


def scrape_youtube_titles(channel_id: str, channel_name: str) -> list:
    """Récupère les derniers titres de vidéos YouTube via RSS public."""
    signals = []
    try:
        url  = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        feed = feedparser.parse(url)
        for entry in feed.entries[:2]:
            title = entry.get("title", "")
            h     = _signal_hash(title)
            if h in _signal_cache or not title:
                continue
            _signal_cache.add(h)
            signals.append({
                "source":  "YouTube",
                "author":  channel_name,
                "content": title,
                "url":     entry.get("link",""),
                "hash":    h,
                "ts":      datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
    except Exception:
        pass
    return signals


def analyze_signal_sentiment(signal: dict) -> dict:
    """Analyse le sentiment d'un signal trader avec l'IA."""
    try:
        text = signal["content"]
        prompt = f"""Analyse ce message d'un trader crypto ({signal['author']}).

Message: "{text}"

Détermine:
1. Le sentiment (bullish/bearish/neutral)
2. Le symbole mentionné (BTC/ETH/etc ou GENERAL)
3. La force du signal (1=faible, 2=modéré, 3=fort)

JSON strict: {{"sentiment":"bullish/bearish/neutral","symbol":"BTC","strength":2,"summary":"résumé 1 phrase"}}"""

        r = ask_model_single(prompt)
        signal.update({
            "sentiment": r.get("sentiment","neutral"),
            "symbol":    r.get("symbol","GENERAL"),
            "strength":  r.get("strength",1),
            "summary":   r.get("summary",""),
        })
    except Exception:
        signal.update({"sentiment":"neutral","symbol":"GENERAL","strength":1})

    # Sauvegarde en DB
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT OR IGNORE INTO trader_signals
            (source,author,content,sentiment,symbol,strength,timestamp,url,hash)
            VALUES(?,?,?,?,?,?,?,?,?)""", (
            signal["source"], signal["author"], signal["content"],
            signal["sentiment"], signal["symbol"], signal["strength"],
            signal["ts"], signal["url"], signal["hash"],
        ))
        con.commit(); con.close()
    except Exception:
        pass

    return signal


def get_trader_intelligence() -> dict:
    """
    Collecte les signaux des traders en temps réel.
    Retourne un résumé utilisable dans le prompt d'analyse.
    """
    all_signals = []

    # Twitter via Nitter (2 comptes par cycle pour limiter les requêtes)
    accounts_batch = TRADER_TWITTER_ACCOUNTS[
        bot_state.get("nitter_idx", 0) % len(TRADER_TWITTER_ACCOUNTS):
        bot_state.get("nitter_idx", 0) % len(TRADER_TWITTER_ACCOUNTS) + 2
    ]
    bot_state["nitter_idx"] = bot_state.get("nitter_idx", 0) + 2

    for account in accounts_batch:
        try:
            signals = scrape_nitter(account)
            all_signals.extend(signals)
        except Exception:
            pass

    # YouTube (1 chaîne par cycle)
    yt_items = list(YOUTUBE_CHANNELS.items())
    yt_idx   = bot_state.get("yt_idx", 0) % len(yt_items)
    bot_state["yt_idx"] = yt_idx + 1
    ch_name, ch_id = yt_items[yt_idx]
    try:
        yt_signals = scrape_youtube_titles(ch_id, ch_name)
        all_signals.extend(yt_signals)
    except Exception:
        pass

    # Analyse sentiment sur les nouveaux signaux
    analyzed = []
    for s in all_signals[:4]:  # max 4 analyses IA par cycle
        analyzed.append(analyze_signal_sentiment(s))

    if not analyzed:
        return {"bullish": [], "bearish": [], "summary": ""}

    bullish = [s for s in analyzed if s["sentiment"]=="bullish"]
    bearish = [s for s in analyzed if s["sentiment"]=="bearish"]

    # Résumé texte pour le prompt
    parts = []
    for s in analyzed[:3]:
        e = "📈" if s["sentiment"]=="bullish" else "📉" if s["sentiment"]=="bearish" else "➡️"
        parts.append(f"{e} @{s['author']}: {s.get('summary', s['content'][:80])}")

    return {
        "bullish":  bullish,
        "bearish":  bearish,
        "summary":  "\n".join(parts),
        "count":    len(analyzed),
    }


def get_db_trader_signals_summary() -> str:
    """Résumé des derniers signaux traders depuis la DB."""
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute("""
            SELECT author, sentiment, symbol, summary, timestamp
            FROM trader_signals
            ORDER BY id DESC LIMIT 10
        """).fetchall()
        con.close()
        if not rows:
            return "Aucun signal collecté"
        lines = []
        for r in rows:
            e = "📈" if r[1]=="bullish" else "📉" if r[1]=="bearish" else "➡️"
            lines.append(f"{e} @{r[0]} [{r[2]}]: {r[3] or '...'} ({r[4][11:16]})")
        return "\n".join(lines)
    except Exception:
        return "Erreur DB signaux"


# ═══════════════════════════════════════════════════════════════
#  AUTO-APPRENTISSAGE AVANCÉ
# ═══════════════════════════════════════════════════════════════

def generate_trading_rules():
    """
    Après chaque 10 trades, l'IA analyse les résultats et génère
    ses propres règles de trading basées sur ce qui a marché.
    """
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    if len(closed) < 10 or len(closed) % 10 != 0:
        return None

    try:
        # Prépare un résumé des trades récents
        recent = closed[-20:]
        wins   = [t for t in recent if t["pnl"] > 0]
        losses = [t for t in recent if t["pnl"] <= 0]

        win_patterns  = Counter([p for t in wins   for p in t.get("patterns",[])])
        loss_patterns = Counter([p for t in losses  for p in t.get("patterns",[])])

        avg_win_conf   = round(sum(t["confidence"] for t in wins)/max(len(wins),1),1)
        avg_loss_conf  = round(sum(t["confidence"] for t in losses)/max(len(losses),1),1)
        avg_win_dur    = round(sum(t.get("duration_min",0) for t in wins)/max(len(wins),1),1)
        avg_loss_dur   = round(sum(t.get("duration_min",0) for t in losses)/max(len(losses),1),1)

        prompt = f"""Tu es un expert en trading algorithmique. Analyse ces {len(recent)} trades simulés et génère des règles précises.

RÉSULTATS :
- Win Rate: {len(wins)}/{len(recent)} ({len(wins)/len(recent)*100:.0f}%)
- Confiance moyenne gagnants: {avg_win_conf}% | perdants: {avg_loss_conf}%
- Durée moyenne gagnants: {avg_win_dur}min | perdants: {avg_loss_dur}min
- Patterns gagnants fréquents: {dict(win_patterns.most_common(3))}
- Patterns perdants fréquents: {dict(loss_patterns.most_common(3))}

RÈGLES ACTUELLES SL/TP: SL={STOP_LOSS_PCT*100:.1f}% TP={TAKE_PROFIT_PCT*100:.1f}%

Génère 3 règles concrètes et 1 recommandation SL/TP optimale.
JSON strict: {{"rules":["règle1","règle2","règle3"],"sl_pct":2.5,"tp_pct":4.0,"insight":"insight principal"}}"""

        r = ask_model_single(prompt, "llama-3.3-70b-versatile")
        rules   = r.get("rules", [])
        sl_new  = float(r.get("sl_pct", STOP_LOSS_PCT*100)) / 100
        tp_new  = float(r.get("tp_pct", TAKE_PROFIT_PCT*100)) / 100
        insight = r.get("insight","")

        # Sauvegarde les règles en DB
        for rule in rules:
            try:
                con = sqlite3.connect(DB_FILE)
                con.execute("""INSERT INTO trading_rules
                    (rule, condition, action, win_rate, sample_size, created_date, last_updated)
                    VALUES(?,?,?,?,?,?,?)""", (
                    rule, "auto-générée", "appliquer",
                    len(wins)/len(recent)*100, len(recent),
                    datetime.now().strftime("%Y-%m-%d"),
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                ))
                con.commit(); con.close()
            except Exception:
                pass

        # Mise à jour des paramètres si amélioration significative
        global STOP_LOSS_PCT, TAKE_PROFIT_PCT
        if 0.01 <= sl_new <= 0.05 and sl_new != STOP_LOSS_PCT:
            old_sl = STOP_LOSS_PCT
            STOP_LOSS_PCT = sl_new
            print(f"[AUTO-LEARN] SL ajusté: {old_sl*100:.1f}% → {sl_new*100:.1f}%")
        if 0.02 <= tp_new <= 0.08 and tp_new != TAKE_PROFIT_PCT:
            old_tp = TAKE_PROFIT_PCT
            TAKE_PROFIT_PCT = tp_new
            print(f"[AUTO-LEARN] TP ajusté: {old_tp*100:.1f}% → {tp_new*100:.1f}%")

        return {"rules": rules, "sl": sl_new, "tp": tp_new, "insight": insight}

    except Exception as e:
        print(f"[RULES] {e}")
        return None


def auto_adjust_sl_tp():
    """
    Ajuste SL/TP automatiquement selon les statistiques récentes.
    - Trop de SL déclenchés → élargir SL
    - Trop de TP manqués (reversal) → resserrer TP
    """
    global STOP_LOSS_PCT, TAKE_PROFIT_PCT
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    if len(closed) < 15:
        return

    recent = closed[-15:]
    sl_hits = sum(1 for t in recent if "STOP-LOSS" in (t.get("exit_reason","") or ""))
    tp_hits = sum(1 for t in recent if "TAKE-PROFIT" in (t.get("exit_reason","") or ""))
    total   = len(recent)

    # Trop de SL → le SL est trop serré
    if sl_hits / total > 0.5 and STOP_LOSS_PCT < 0.04:
        STOP_LOSS_PCT = round(min(0.04, STOP_LOSS_PCT + 0.003), 3)
        print(f"[AUTO-SL] SL élargi → {STOP_LOSS_PCT*100:.1f}%")

    # Peu de TP mais beaucoup de profits → TP trop serré, laisser courir
    avg_pnl_pct = sum(t.get("pnl_pct",0) for t in recent if t["pnl"]>0) / max(tp_hits,1)
    if tp_hits/total < 0.2 and avg_pnl_pct > TAKE_PROFIT_PCT*100*1.5:
        TAKE_PROFIT_PCT = round(min(0.07, TAKE_PROFIT_PCT + 0.005), 3)
        print(f"[AUTO-TP] TP élargi → {TAKE_PROFIT_PCT*100:.1f}%")


def get_active_rules() -> str:
    """Récupère les règles actives depuis la DB pour les injecter dans le prompt."""
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute("""
            SELECT rule FROM trading_rules
            WHERE active=1 ORDER BY win_rate DESC LIMIT 5
        """).fetchall()
        con.close()
        if not rows:
            return ""
        return "MES RÈGLES AUTO-GÉNÉRÉES:\n" + "\n".join(f"• {r[0]}" for r in rows)
    except Exception:
        return ""


def test_strategy_variation(send_fn):
    """
    Teste périodiquement des variations de stratégie en simulation
    et garde celle qui performe le mieux.
    """
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    if len(closed) < 20:
        return

    # Test : la stratégie actuelle vs une variation plus agressive
    current_wr = db_win_rate(20)

    strategies = [
        {"name": "conservateur", "sl": 0.02, "tp": 0.03, "conf": 75},
        {"name": "équilibré",    "sl": 0.025,"tp": 0.04, "conf": 65},
        {"name": "agressif",     "sl": 0.035,"tp": 0.06, "conf": 55},
    ]

    recent = closed[-20:]
    best_wr  = 0
    best_strat = None

    for strat in strategies:
        # Simule le résultat avec ces paramètres sur les trades passés
        simulated_wins = 0
        for t in recent:
            pct = t.get("pnl_pct", 0)
            if pct >= strat["tp"]*100:
                simulated_wins += 1
            elif pct > -strat["sl"]*100:
                simulated_wins += 0.5  # neutre
        wr = simulated_wins / len(recent) * 100
        if wr > best_wr:
            best_wr    = wr
            best_strat = strat

    if best_strat and best_wr > current_wr + 5:
        # Applique la meilleure stratégie trouvée
        global STOP_LOSS_PCT, TAKE_PROFIT_PCT, CONFIDENCE_BASE
        STOP_LOSS_PCT   = best_strat["sl"]
        TAKE_PROFIT_PCT = best_strat["tp"]
        memory["confidence_threshold"] = best_strat["conf"]
        print(f"[STRAT] Stratégie '{best_strat['name']}' adoptée (WR simulé {best_wr:.0f}%)")

        # Sauvegarde en DB
        try:
            con = sqlite3.connect(DB_FILE)
            con.execute("""INSERT INTO strategy_tests
                (strategy_name,params,trades,win_rate,total_pnl,tested_date,active)
                VALUES(?,?,?,?,?,?,1)""", (
                best_strat["name"], json.dumps(best_strat),
                len(recent), best_wr,
                sum(t.get("pnl",0) for t in recent),
                datetime.now().strftime("%Y-%m-%d %H:%M"),
            ))
            con.commit(); con.close()
        except Exception:
            pass

        send_fn(
            f"🧬 ÉVOLUTION STRATÉGIE\n"
            f"Nouvelle stratégie : {best_strat['name']}\n"
            f"SL:{best_strat['sl']*100:.1f}% TP:{best_strat['tp']*100:.1f}%\n"
            f"WR simulé:{best_wr:.0f}% vs {current_wr:.0f}%"
        )


# ═══════════════════════════════════════════════════════════════
#  BOUCLE CONTINUE
# ═══════════════════════════════════════════════════════════════
def trading_loop(send_fn):
    equity = get_equity()
    bot_state["micro_count"] = 0
    send_fn(
        f"🚀 SIMULATION DÉMARRÉE — Mode Micro-Trading\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Capital      : ${CAPITAL_INITIAL:,.2f} (virtuel)\n"
        f"🪙 Cryptos total: {len(ALL_SYMBOLS)} | Micro: {len(MICRO_SYMBOLS)}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ MICRO-TRADES  (algo pur, <1min)\n"
        f"  Cycle : {CYCLE_MICRO}s | SL: {MICRO_SL_PCT*100:.1f}% | TP: {MICRO_TP_PCT*100:.1f}%\n"
        f"  Trailing: {MICRO_TRAILING_PCT*100:.1f}% | Timeout: {MICRO_MAX_DURATION}s\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 SCALP CLASSIQUE (IA, ~1-5min)\n"
        f"  Cycle : {CYCLE_SCALP}s | SL: {STOP_LOSS_PCT*100:.1f}% | TP: {TAKE_PROFIT_PCT*100:.1f}%\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔬 ANALYSE PROFONDE (IA multi-TF, ~5-30min)\n"
        f"  Cycle : {CYCLE_DEEP}s | Levier x{LEVERAGE_SIM}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 3 niveaux actifs en parallèle — décisions autonomes."
    )

    fear_greed = get_fear_greed()

    while bot_state["running"]:
        now = time.time()

        # ══ 8s : MICRO-TRADING (algo pur, sub-minute) ═════════
        if now - bot_state.get("last_micro", 0) >= CYCLE_MICRO:
            try:
                monitor_micro_positions(send_fn)
                run_micro_cycle(send_fn)
            except Exception as e:
                print(f"[MICRO] {e}")
            bot_state["last_micro"] = now

        # ══ 15s : Surveillance SL/TP classique ════════════════
        if now - bot_state["last_monitor"] >= CYCLE_MONITOR:
            try:
                monitor_positions(send_fn)
            except Exception as e:
                print(f"[MON] {e}")
            bot_state["last_monitor"] = now

        # ══ 2min : Scan + Scalping ════════════════════════════
        if now - bot_state["last_scalp"] >= CYCLE_SCALP:
            bot_state["cycle_count"] += 1
            cycle = bot_state["cycle_count"]
            try:
                fear_greed = get_fear_greed()
                threshold  = memory.get("confidence_threshold", CONFIDENCE_BASE)
                equity     = get_equity()
                pnl_tot    = equity - sim["initial"]

                # Scan silencieux — pas de message sauf trade ou bilan
                opps = scan_market()

                if opps:
                    # Analyse silencieuse — on n'envoie rien sauf si trade réel
                    for opp in opps[:3]:
                        if not bot_state["running"]: break
                        if opp["has_alert"]: continue

                        result = analyze(opp, fear_greed)
                        signal = result["signal"]
                        conf   = result["confidence"]
                        risk   = result["risk"]
                        reason = result.get("reason","")
                        in_pos = any(p["symbol"]==opp["symbol"]
                                     for p in sim["positions"].values())

                        if signal == "HOLD": continue

                        if in_pos and signal in ("BUY","SELL"):
                            for pk, pos in list(sim["positions"].items()):
                                if (pos["symbol"]==opp["symbol"] and
                                        ((pos["side"]=="LONG" and signal=="SELL") or
                                         (pos["side"]=="SHORT" and signal=="BUY"))):
                                    close_trade(pk, opp["price"],
                                                f"Signal contraire {conf}%", send_fn)
                            continue

                        if not in_pos:
                            if conf >= threshold and risk in ("LOW","MEDIUM"):
                                open_trade(result, send_fn)
                            elif LEARN_MODE_ENABLED and conf >= LEARN_MODE_CONF_MIN:
                                result["_learning"] = True
                                result["_forced_pct"] = LEARNING_MAX_PCT
                                open_trade(result, send_fn)
                            else:
                                send_fn(
                                    f"⏸ {coin} ignoré — conf={conf}% "
                                    f"(seuil={threshold}%, min={LEARN_MODE_CONF_MIN}%)"
                                )

            except Exception as e:
                print(f"[SCALP] {e}")
                send_fn(f"⚠️ Erreur cycle #{cycle}: {str(e)[:80]}")

            bot_state["last_scalp"] = now

        # ══ 5min : Analyse profonde + traders + auto-learning ═══
        if now - bot_state["last_deep"] >= CYCLE_DEEP:
            try:
                _deep_futures(send_fn, fear_greed)
                # Collecte traders en arrière-plan
                threading.Thread(target=get_trader_intelligence, daemon=True).start()
                # Auto-ajustement SL/TP
                auto_adjust_sl_tp()
                # Règles auto-générées tous les 10 trades
                rules = generate_trading_rules()
                if rules:
                    send_fn(
                        f"🧠 RÈGLES AUTO-GÉNÉRÉES ({len(sim['trades'])} trades)\n"
                        + "\n".join(f"• {r}" for r in rules.get("rules",[])[:3]) +
                        f"\nSL:{rules['sl']*100:.1f}% TP:{rules['tp']*100:.1f}% | {rules.get('insight','')[:80]}"
                    )
                # Test stratégie tous les 50 trades
                closed_n = len([t for t in sim["trades"] if t.get("pnl")])
                if closed_n >= 20 and closed_n % 50 == 0:
                    test_strategy_variation(send_fn)
            except Exception as e:
                print(f"[DEEP] {e}")
            bot_state["last_deep"] = now

        # ══ 15min : Bilan ══════════════════════════════════════
        if now - bot_state["last_status"] >= CYCLE_STATUS:
            try:
                _send_bilan(send_fn)
                equity = get_equity()
                db_save_equity(equity, sim["cash"],
                               len(sim["positions"]),
                               equity - sim["initial"])
            except Exception as e:
                print(f"[STATUS] {e}")
            bot_state["last_status"] = now

        bot_state["last_heartbeat"] = datetime.now()
        time.sleep(3)


def _deep_futures(send_fn, fear_greed: str):
    """Analyse profonde pour positions simulées FUTURES (levier x2)."""
    thresh = memory.get("confidence_threshold", CONFIDENCE_BASE)
    targets = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT"]

    send_fn(
        f"🔬 ANALYSE PROFONDE FUTURES\n"
        f"{fear_greed} | {len(sim['positions'])} positions ouvertes"
    )

    # ── Scan Yahoo : actions, forex, commodités ─────────────────
    all_yahoo_opps = []
    for market_dict, market_name in [
        (STOCKS_SYMBOLS,    "STOCK"),
        (FOREX_SYMBOLS,     "FOREX"),
        (COMMODITY_SYMBOLS, "COMMODITY"),
    ]:
        try:
            yahoo_opps = scan_yahoo_market(market_dict, market_name)
            all_yahoo_opps.extend(yahoo_opps)
        except Exception as e:
            print(f"[YAHOO-SCAN] {market_name}: {e}")

    if all_yahoo_opps:
        thresh = memory.get("confidence_threshold", CONFIDENCE_BASE)
        lines  = [f"📈 Marchés externes ({len(all_yahoo_opps)} signaux):"]
        for o in all_yahoo_opps[:5]:
            e = "🟢" if o["direction"]=="BUY" else "🔴"
            lines.append(
                f"  {e} {o['name']} ({o['market_type']}) "
                f"score={o['score']:+d} RSI={o['ind'].get('rsi',0):.0f}"
            )
        send_fn("\n".join(lines))

        for o in all_yahoo_opps[:3]:
            if not bot_state["running"]: break
            in_pos = any(p["symbol"]==o["symbol"] for p in sim["positions"].values())
            if in_pos or len(sim["positions"]) >= MAX_POSITIONS:
                continue
            prompt = f"""Simulation trading {o['market_type']}.
{o['name']} ({o['symbol']}) | ${o['price']:.4f}
RSI: {o['ind'].get('rsi','?')} | Mom5: {o['ind'].get('mom5','?')}% | MACD: {o['ind'].get('macd_h','?')}
Score opportunité: {o['score']:+d} vers {o['direction']}
{fear_greed}
Marché: {'ouvert' if o['market_type'] in ('STOCK','FOREX') else 'continu'}
JSON strict: {{"signal":"{o['direction']} ou HOLD","confidence":0-100,"reason":"raison","risk":"LOW ou MEDIUM ou HIGH","market":"SPOT"}}"""
            result = vote(prompt)
            result.update({"symbol": o["symbol"], "price": o["price"],
                            "patterns": [], "market": "SPOT",
                            "name": o["name"], "market_type": o["market_type"]})
            if (result["signal"] in ("BUY","SELL")
                    and result["confidence"] >= thresh
                    and result["risk"] in ("LOW","MEDIUM")):
                send_fn(
                    f"🎯 Signal {o['market_type']}: {o['name']}\n"
                    f"  {result['signal']} {result['confidence']}% | {result.get('reason','')[:70]}"
                )
                open_trade(result, send_fn)

    for symbol in targets:
        try:
            coin  = symbol.replace("USDT","")
            mtf   = get_multi_tf(symbol)
            conf  = tf_score(mtf)

            if abs(conf["score"]) < 5:
                continue  # confluence insuffisante

            price = get_price(symbol)
            ind5m = mtf.get("5m", {})
            ob    = get_order_book(symbol)
            in_pos= any(p["symbol"]==symbol for p in sim["positions"].values())

            direction = "BUY" if conf["direction"]=="LONG" else "SELL"
            prompt = f"""Simulation trading FUTURES court terme.

{symbol} FUTURES sim (levier x{LEVERAGE_SIM}) | ${price:.2f}
Confluence TF: {conf['score']}/9 → {conf['direction']}
Signaux: {', '.join(conf['signals'][:4])}
RSI 5m: {ind5m.get('rsi','?')} | MACD hist: {ind5m.get('macd_h','?')}
Mom 5m: {ind5m.get('mom5','?')}% | BB%: {ind5m.get('bb_pct','?')}
OrderBook: {ob['pressure']}
{fear_greed}
En position: {'OUI' if in_pos else 'NON'}

Simulation pure — décide BUY (long) ou SELL (short) ou HOLD.
SL={STOP_LOSS_PCT*100:.1f}% TP={TAKE_PROFIT_PCT*100:.1f}% lev={LEVERAGE_SIM}x

JSON strict (sans backticks):
{{"signal":"{direction} ou HOLD","confidence":0-100,"reason":"raison","risk":"LOW ou MEDIUM ou HIGH","market":"FUTURES"}}"""

            result = vote(prompt)
            result.update({"symbol":symbol,"price":price,
                            "patterns":[],"confluence":conf,
                            "ob":ob,"ind":ind5m,"market":"FUTURES"})

            sig_e = {"BUY":"🟢","SELL":"🔴","HOLD":"⚪"}.get(result["signal"],"⚪")
            send_fn(
                f"{sig_e} FUTURES {coin}: {result['signal']} "
                f"{result['confidence']}% [{result.get('consensus','?')}]\n"
                f"  TF: {conf['score']}/9 | {result.get('reason','')[:70]}"
            )

            if (result["signal"] in ("BUY","SELL")
                    and result["confidence"] >= thresh
                    and result["risk"] in ("LOW","MEDIUM")
                    and not in_pos):
                open_trade(result, send_fn)

        except Exception as e:
            print(f"[DEEP] {symbol}: {e}")


def _send_bilan(send_fn):
    equity        = get_equity()
    pnl           = equity - sim["initial"]
    stats         = get_stats()
    wr_db         = db_win_rate(30)
    sym_stats     = db_symbol_stats()
    thresh        = memory.get("confidence_threshold", CONFIDENCE_BASE)
    fear_greed_str = get_fear_greed()

    pos_lines = ""
    if sim["positions"]:
        prices = get_prices_batch()
        for pos in sim["positions"].values():
            p    = prices.get(pos["symbol"], pos["price_in"])
            chg  = (p-pos["price_in"])/pos["price_in"]*100 * pos.get("leverage",1)
            e    = "📈" if chg>0 else "📉"
            pos_lines += (f"\n  {e} {pos['symbol'].replace('USDT',''):6s} "
                          f"{pos['side']} {chg:+.2f}%")

    sym_s   = sym_stats
    sym_str = " | ".join(
        f"{s['s']}:{s['wr']:.0f}%WR" for s in sym_stats
    ) or "Aucun encore"

    # Conseil inspiré des traders selon le contexte
    fg_val = 50
    try:
        fg_val = int(fear_greed_str.split(":")[1].split("/")[0].strip())
    except Exception:
        pass
    if fg_val < 20:
        trader_tip = "💡 Saylor & Buffett : Fear extrême = opportunité rare. Accumule."
    elif fg_val < 35:
        trader_tip = "💡 Buffett : 'Sois avide quand les autres ont peur.'"
    elif fg_val > 75:
        trader_tip = "💡 Paul Tudor Jones : Marché euphorique → protège le capital."
    elif stats['win_rate'] > 60:
        trader_tip = "💡 Livermore : Tu es en forme, laisse courir les gagnants."
    else:
        trader_tip = "💡 Cathie Wood : Focus sur les tokens à fort momentum."

    micro_count = bot_state.get("micro_count", 0)
    send_fn(
        f"📊 BILAN — {datetime.now().strftime('%H:%M')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Capital  : ${equity:.2f} ({pnl/sim['initial']*100:+.1f}%)\n"
        f"📍 Positions: {len(sim['positions'])}/{MAX_POSITIONS}{pos_lines}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 Win Rate : {stats['win_rate']}% ({stats['wins']}✅/{stats['losses']}❌)\n"
        f"📊 Trades   : {stats['total']} | ⚡{micro_count} micro\n"
        f"🥇 Top coins: {sym_str}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{trader_tip}"
    )


# ═══════════════════════════════════════════════════════════════
#  WATCHDOG + RÉSUMÉ JOURNALIER
# ═══════════════════════════════════════════════════════════════
def watchdog(send_fn):
    time.sleep(180)
    alerted = False
    while True:
        time.sleep(60)
        if not bot_state["running"]:
            alerted = False; continue
        last = bot_state.get("last_heartbeat")
        if not last: continue
        elapsed = (datetime.now()-last).total_seconds()
        if elapsed > 300 and not alerted:
            send_fn(
                f"⚠️ WATCHDOG: Inactif {int(elapsed//60)} min\n"
                f"Dernier signal: {last.strftime('%H:%M:%S')}"
            )
            alerted = True
        elif elapsed <= 300:
            alerted = False


def daily_summary(send_fn):
    while True:
        now = datetime.now()
        midnight = (now+timedelta(days=1)).replace(
            hour=0, minute=0, second=5, microsecond=0)
        time.sleep((midnight-now).total_seconds())
        try:
            equity = get_equity()
            pnl    = equity - sim["initial"]
            stats  = get_stats()
            today  = now.strftime("%Y-%m-%d")
            t_day  = [t for t in sim["trades"]
                      if t.get("time_in","").startswith(today)]
            pnl_day= sum(t["pnl"] for t in t_day if t.get("pnl"))
            sym_s  = db_symbol_stats()
            best3  = "\n".join(f"  🏅 {s['s']}: WR {s['wr']:.0f}% ({s['n']} trades)"
                               for s in sym_s[:3]) or "  Aucun"
            lessons= "\n".join(
                f"  {'✅' if l['type']=='succes' else '❌'} {l['lecon']}"
                for l in memory["lessons"][-3:]
            ) or "  Aucune"
            send_fn(
                f"📊 RÉSUMÉ JOURNALIER — {now.strftime('%d/%m/%Y')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Capital  : ${equity:.2f} ({pnl/sim['initial']*100:+.1f}%)\n"
                f"📈 PnL total: ${pnl:+.2f}\n"
                f"📅 PnL jour : ${pnl_day:+.2f} ({len(t_day)} trades)\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🏆 Win Rate : {stats['win_rate']}% ({stats['total']} trades)\n"
                f"⏱ Durée moy: {stats['avg_dur']} min\n"
                f"📚 Leçons   : {len(memory['lessons'])}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🥇 Top coins:\n{best3}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💡 Dernières leçons:\n{lessons}"
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
            requests.get(
                "https://junior-tick-1ever-6bf9cee7.koyeb.app/health",
                timeout=10)
        except Exception:
            pass
        time.sleep(270)


# ═══════════════════════════════════════════════════════════════
#  DASHBOARD HTML
# ═══════════════════════════════════════════════════════════════
def generate_dashboard() -> str:
    stats  = get_stats()
    equity = get_equity()
    pnl    = equity - sim["initial"]
    pct    = pnl/sim["initial"]*100
    status = "🟢 EN MARCHE" if bot_state["running"] else "🔴 ARRÊTÉ"
    last   = bot_state.get("last_heartbeat")
    hb     = last.strftime("%H:%M:%S") if last else "—"
    thresh = memory.get("confidence_threshold", CONFIDENCE_BASE)
    wr_db  = db_win_rate(30)
    sym_s  = db_symbol_stats()

    prices = get_prices_batch()

    pos_html = ""
    for pk, pos in sim["positions"].items():
        p     = prices.get(pos["symbol"], pos["price_in"])
        chg   = (p-pos["price_in"])/pos["price_in"]*100 * pos.get("leverage",1)
        color = "#2ecc71" if chg>=0 else "#e74c3c"
        lev   = f" x{pos['leverage']}" if pos.get("leverage",1)>1 else ""
        pos_html += (
            f"<tr><td>{pos['symbol'].replace('USDT','')}</td>"
            f"<td>{pos['market']}{lev}</td><td>{pos['side']}</td>"
            f"<td>${pos['price_in']:.6f}</td><td>${p:.6f}</td>"
            f'<td style="color:{color}">{chg:+.2f}%</td>'
            f"<td>${pos['qty']*p:.2f}</td></tr>"
        )

    trades_html = ""
    for t in reversed(sim["trades"][-25:]):
        if t.get("pnl") is not None:
            c = "#2ecc71" if t["pnl"]>0 else "#e74c3c"
            ps = f'<span style="color:{c}">${t["pnl"]:+.4f} ({t.get("pnl_pct",0):+.2f}%)</span>'
        else:
            ps = '<span style="color:#f39c12">ouvert</span>'
        po  = f"${t['price_out']:.6f}" if t.get("price_out") else "—"
        dur = f"{t.get('duration_min','—')}m"
        trades_html += (
            f"<tr><td>{t['id']}</td>"
            f"<td>{t['symbol'].replace('USDT','')}</td>"
            f"<td>{t['market']}</td><td>{t['side']}</td>"
            f"<td>${t['price_in']:.6f}</td><td>{po}</td>"
            f"<td>{ps}</td><td>{t['confidence']}%</td>"
            f"<td>{dur}</td><td>{t['time_in'][11:16]}</td></tr>"
        )

    lessons_html = ""
    for l in reversed(memory["lessons"][-12:]):
        c = "#2ecc71" if l["type"]=="succes" else "#e74c3c"
        e = "✅" if l["type"]=="succes" else "❌"
        lessons_html += (
            f'<tr><td style="color:{c}">{e}</td>'
            f"<td>{l.get('symbol','').replace('USDT','')}</td>"
            f'<td style="color:{c}">${l.get("pnl",0):+.4f}</td>'
            f"<td>{l['lecon'][:55]}</td>"
            f"<td>{l['action_future'][:50]}</td>"
            f"<td>{l['date']}</td></tr>"
        )

    sym_html = "".join(
        f"<span class='badge' style='color:#2ecc71'>"
        f"{s['s']} WR:{s['wr']:.0f}% ({s['n']})</span>"
        for s in sym_s
    ) or "<span style='color:#8b949e'>Données insuffisantes</span>"

    return f"""<!DOCTYPE html><html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sim Trading Bot v4</title>
<style>
body{{font-family:Arial,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:14px}}
h1{{color:#58a6ff;text-align:center;font-size:1.25em;margin-bottom:2px}}
h2{{color:#58a6ff;font-size:.88em;margin:12px 0 5px}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:12px}}
.card{{background:#161b22;border-radius:8px;padding:10px;text-align:center}}
.label{{font-size:.68em;color:#8b949e;margin-bottom:2px}}
.value{{font-size:1.1em;font-weight:bold}}
.green{{color:#2ecc71}}.red{{color:#e74c3c}}.blue{{color:#58a6ff}}.yellow{{color:#f39c12}}
.center{{text-align:center;font-size:.8em;color:#8b949e;margin:2px 0}}
table{{width:100%;border-collapse:collapse;font-size:.7em;margin-bottom:16px}}
th{{background:#21262d;padding:5px;text-align:left;color:#8b949e}}
td{{padding:4px 5px;border-bottom:1px solid #21262d}}
.badge{{background:#161b22;border-radius:5px;padding:2px 7px;font-size:.75em;margin:2px;display:inline-block}}
</style>
<meta http-equiv="refresh" content="20">
</head><body>
<h1>🤖 Simulation Trading Bot v4</h1>
<div class="center">{status} | Heartbeat: {hb} | Cycle #{bot_state['cycle_count']}</div>
<div class="center">
  Seuil: {thresh}% | WR(30): {wr_db}% | 
  SL:{STOP_LOSS_PCT*100:.1f}% TP:{TAKE_PROFIT_PCT*100:.1f}% Trail:{TRAILING_PCT*100:.1f}% |
  {len(ALL_SYMBOLS)} cryptos | Trades/jour: {bot_state['trades_today']}
</div>
<div class="center" style="margin:4px 0">{sym_html}</div>
<div class="grid">
  <div class="card"><div class="label">Capital simulé</div>
    <div class="value blue">${equity:.2f}</div></div>
  <div class="card"><div class="label">PnL simulation</div>
    <div class="value {'green' if pnl>=0 else 'red'}">${pnl:+.2f} ({pct:+.1f}%)</div></div>
  <div class="card"><div class="label">Cash disponible</div>
    <div class="value">${sim['cash']:.2f}</div></div>
  <div class="card"><div class="label">Positions ouvertes</div>
    <div class="value yellow">{len(sim['positions'])}/{MAX_POSITIONS}</div></div>
  <div class="card"><div class="label">Win Rate</div>
    <div class="value yellow">{stats['win_rate']}%</div></div>
  <div class="card"><div class="label">Trades | Leçons</div>
    <div class="value">{stats['total']} | {len(memory['lessons'])}</div></div>
</div>
<h2>Positions Simulées Ouvertes</h2>
<table><thead><tr>
  <th>Coin</th><th>Marché</th><th>Sens</th>
  <th>Entrée</th><th>Prix actuel</th><th>PnL%</th><th>Valeur</th>
</tr></thead><tbody>
{pos_html or '<tr><td colspan="7" style="text-align:center;color:#8b949e">Aucune position ouverte</td></tr>'}
</tbody></table>
<h2>Historique des Simulations</h2>
<table><thead><tr>
  <th>#</th><th>Coin</th><th>Mkt</th><th>Sens</th>
  <th>Entrée</th><th>Sortie</th><th>PnL</th>
  <th>Conf</th><th>Durée</th><th>Heure</th>
</tr></thead><tbody>
{trades_html or '<tr><td colspan="10" style="text-align:center;color:#8b949e">Aucun trade encore</td></tr>'}
</tbody></table>
<h2>Mémoire & Apprentissage</h2>
<table><thead><tr>
  <th>Type</th><th>Coin</th><th>PnL</th>
  <th>Leçon</th><th>Action Future</th><th>Date</th>
</tr></thead><tbody>
{lessons_html or '<tr><td colspan="6" style="text-align:center;color:#8b949e">Aucune leçon encore</td></tr>'}
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
        f = asyncio.run_coroutine_threadsafe(
            _app.bot.send_message(chat_id=chat_id, text=msg), _main_loop)
        try: f.result(timeout=15)
        except Exception as e: print(f"[MSG] {e}")
    return send


def _auth(update: Update) -> bool:
    return str(update.effective_chat.id) == TELEGRAM_CHAT_ID


# ═══════════════════════════════════════════════════════════════
#  COMMANDES TELEGRAM
# ═══════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    if bot_state["running"]:
        await update.message.reply_text("Simulation déjà en cours !"); return
    bot_state.update({
        "running": True, "trades_today": 0, "cycle_count": 0,
        "last_heartbeat": None, "last_monitor": 0, "last_micro": 0,
        "last_scalp": 0, "last_deep": 0, "last_status": 0,
    })
    send = make_send(TELEGRAM_CHAT_ID)
    threading.Thread(target=trading_loop,  args=(send,), daemon=True).start()
    threading.Thread(target=watchdog,      args=(send,), daemon=True).start()
    threading.Thread(target=daily_summary, args=(send,), daemon=True).start()


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    bot_state["running"] = False
    equity = get_equity()
    stats  = get_stats()
    await update.message.reply_text(
        f"🛑 Simulation arrêtée.\n"
        f"Capital: ${equity:.2f} | PnL: ${equity-sim['initial']:+.2f}\n"
        f"Trades: {stats['total']} | WR: {stats['win_rate']}%\n"
        f"Positions ouvertes conservées en mémoire."
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    equity = get_equity()
    pnl    = equity - sim["initial"]
    stats  = get_stats()
    wr_db  = db_win_rate(30)
    thresh = memory.get("confidence_threshold", CONFIDENCE_BASE)
    last   = bot_state.get("last_heartbeat")

    pos_lines = ""
    if sim["positions"]:
        prices = get_prices_batch()
        for pos in sim["positions"].values():
            p   = prices.get(pos["symbol"], pos["price_in"])
            chg = (p-pos["price_in"])/pos["price_in"]*100 * pos.get("leverage",1)
            pos_lines += f"\n  {'📈' if chg>0 else '📉'} {pos['symbol'].replace('USDT','')} {pos['side']}: {chg:+.2f}%"

    await update.message.reply_text(
        f"{'🟢' if bot_state['running'] else '🔴'} "
        f"{'EN MARCHE' if bot_state['running'] else 'ARRÊTÉ'}\n"
        f"Heartbeat: {last.strftime('%H:%M:%S') if last else '—'} | Cycle #{bot_state['cycle_count']}\n"
        f"━━━━━━━━━━━━━\n"
        f"💰 Capital sim: ${equity:.2f} ({pnl:+.2f})\n"
        f"💵 Cash: ${sim['cash']:.2f}\n"
        f"📍 Positions: {len(sim['positions'])}{pos_lines}\n"
        f"━━━━━━━━━━━━━\n"
        f"📊 Trades: {stats['total']} | WR: {stats['win_rate']}%\n"
        f"WR DB(30): {wr_db}% | Seuil: {thresh}%\n"
        f"📚 Leçons: {len(memory['lessons'])}\n"
        f"⏱ Durée moy: {stats['avg_dur']} min"
    )


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    await update.message.reply_text("🔍 Scan en cours...")
    try:
        opps  = scan_market()
        lines = ["🎯 Scan marché — Top opportunités\n━━━━━━━━━━━━━"]
        for o in opps[:7]:
            e     = "🟢" if o["direction"]=="BUY" else "🔴"
            alert = " ⚠️" if o["has_alert"] else ""
            lines.append(
                f"{e}{alert} {o['symbol'].replace('USDT',''):6s} | "
                f"score={o['score']:+d} | RSI={o['ind'].get('rsi',0):.0f} | "
                f"mom={o['ind'].get('mom5',0):+.1f}% | "
                f"vol={o['ind'].get('vol',0):.2f}%"
            )
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Erreur: {e}")


async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    equity = get_equity()
    pnl    = equity - sim["initial"]
    stats  = get_stats()
    sym_s  = db_symbol_stats()
    sym_str= " | ".join(f"{s['s']}:{s['wr']:.0f}%WR" for s in sym_s) or "Aucun"
    pos_str= "\n".join(
        f"  {p['symbol'].replace('USDT','')} {p['market']} {p['side']}"
        for p in sim["positions"].values()
    ) or "  Aucune"
    await update.message.reply_text(
        f"💼 Portefeuille Simulation\n"
        f"Capital initial : ${sim['initial']:,.2f}\n"
        f"Capital actuel  : ${equity:.2f} ({pnl:+.2f})\n"
        f"Cash disponible : ${sim['cash']:.2f}\n"
        f"━━━━━━━━━━━━━\n"
        f"Positions:\n{pos_str}\n"
        f"━━━━━━━━━━━━━\n"
        f"Trades: {stats['total']} ({stats['wins']}W/{stats['losses']}L)\n"
        f"Win Rate: {stats['win_rate']}% | Durée moy: {stats['avg_dur']}min\n"
        f"Meilleur: +${stats['best']} | Pire: ${stats['worst']}\n"
        f"Top coins: {sym_str}"
    )


async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    if not sim["positions"]:
        await update.message.reply_text("Aucune position simulée ouverte."); return
    prices = get_prices_batch()
    lines  = ["📍 Positions simulées ouvertes\n━━━━━━━━━━━━━"]
    for pk, pos in sim["positions"].items():
        p    = prices.get(pos["symbol"], pos["price_in"])
        chg  = (p-pos["price_in"])/pos["price_in"]*100 * pos.get("leverage",1)
        e    = "📈" if chg>0 else "📉"
        sl   = pos["price_in"]*(1-STOP_LOSS_PCT) if pos["side"]=="LONG" \
               else pos["price_in"]*(1+STOP_LOSS_PCT)
        tp   = pos["price_in"]*(1+TAKE_PROFIT_PCT) if pos["side"]=="LONG" \
               else pos["price_in"]*(1-TAKE_PROFIT_PCT)
        lev  = f" x{pos['leverage']}" if pos.get("leverage",1)>1 else ""
        lines.append(
            f"{e} {pos['symbol'].replace('USDT','')}{lev} {pos['market']} {pos['side']}\n"
            f"  Entrée: ${pos['price_in']:.6f} → Actuel: ${p:.6f}\n"
            f"  PnL sim: {chg:+.2f}%\n"
            f"  🛑${sl:.6f} | 🎯${tp:.6f}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_lecons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    if not memory["lessons"]:
        await update.message.reply_text("Aucune leçon encore — laisse le bot trader !"); return
    msg = f"📚 Leçons ({len(memory['lessons'])}):\n\n"
    for l in memory["lessons"][-5:]:
        e = "✅" if l["type"]=="succes" else "❌"
        msg += f"{e} [{l.get('symbol','').replace('USDT','')}] ${l.get('pnl',0):+.4f}\n"
        msg += f"  {l['lecon']}\n→ {l['action_future']}\n\n"
    await update.message.reply_text(msg)


async def cmd_fermer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    if not sim["positions"]:
        await update.message.reply_text("Aucune position à fermer."); return
    send  = make_send(TELEGRAM_CHAT_ID)
    prices= get_prices_batch()
    count = 0
    for pk in list(sim["positions"].keys()):
        pos   = sim["positions"].get(pk)
        if not pos: continue
        price = prices.get(pos["symbol"], pos["price_in"])
        close_trade(pk, price, "Fermeture manuelle /fermer", send)
        count += 1
    await update.message.reply_text(f"✅ {count} position(s) fermée(s) manuellement.")


async def cmd_apprentissage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Active/désactive le mode apprentissage forcé."""
    if not _auth(update): return
    global LEARN_MODE_ENABLED
    LEARN_MODE_ENABLED = not LEARN_MODE_ENABLED
    status = "✅ ACTIVÉ" if LEARN_MODE_ENABLED else "⏸ DÉSACTIVÉ"
    stats  = get_stats()
    await update.message.reply_text(
        f"🎓 MODE APPRENTISSAGE : {status}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Seuil normal   : {memory.get('confidence_threshold', CONFIDENCE_BASE)}%\n"
        f"Seuil apprenti.: {LEARN_MODE_CONF_MIN}% (signal faible accepté)\n"
        f"Capital/trade  : {LEARN_MODE_MAX_PCT*100:.0f}% (réduit)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{'Activé : le bot trade même avec signaux faibles pour apprendre plus vite.' if LEARN_MODE_ENABLED else 'Désactivé : le bot trade uniquement avec signaux forts.'}"
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Stats complètes avec répartition par marché."""
    if not _auth(update): return
    stats   = get_stats()
    wr_db   = db_win_rate(30)
    sym_s   = db_symbol_stats()
    equity  = get_equity()
    pnl     = equity - sim["initial"]
    session = sim.get("session", 1)
    thresh  = memory.get("confidence_threshold", CONFIDENCE_BASE)

    # Stats par type de marché
    all_closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    learn_trades  = [t for t in all_closed if t.get("market")=="LEARN"]
    micro_trades  = [t for t in all_closed if t.get("market")=="MICRO"]
    normal_trades = [t for t in all_closed if t.get("market") not in ("LEARN","MICRO")]

    def wr(trades):
        if not trades: return 0
        return round(sum(1 for t in trades if t["pnl"]>0)/len(trades)*100, 1)

    sym_lines = "\n".join(
        f"  {s['s']:8s} WR:{s['wr']:.0f}% ({s['n']} trades) avg:${s['pnl']:+.4f}"
        for s in sym_s[:5]
    ) or "  Données insuffisantes"

    await update.message.reply_text(
        f"📊 STATISTIQUES COMPLÈTES\n"
        f"Session #{session} | Capital: ${equity:.2f} ({pnl:+.2f})\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Global: {stats['total']} trades | WR: {stats['win_rate']}%\n"
        f"   DB(30): {wr_db}% | Seuil: {thresh}%\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ Micro    : {len(micro_trades)} trades | WR: {wr(micro_trades)}%\n"
        f"🎓 Apprenti.: {len(learn_trades)} trades | WR: {wr(learn_trades)}%\n"
        f"🔍 Classique: {len(normal_trades)} trades | WR: {wr(normal_trades)}%\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🥇 Top coins:\n{sym_lines}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📚 Leçons: {len(memory['lessons'])} | "
        f"✅ {len(memory['patterns_that_work'])} bons patterns\n"
        f"❌ {len(memory['patterns_to_avoid'])} patterns à éviter"
    )


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    bot_state.update({
        "running": False, "cycle_count": 0, "trades_today": 0,
        "last_monitor": 0, "last_scalp": 0, "last_deep": 0,
        "last_status": 0, "last_micro": 0,
    })
    # ✅ Conserve TOUTES les leçons — depuis la RAM ET la base SQLite
    # Recharge depuis DB pour récupérer les leçons des sessions précédentes
    try:
        con = sqlite3.connect(DB_FILE)
        db_lessons = con.execute("""
            SELECT trade_id, symbol, market, pnl, lecon, pattern,
                   action_future, type, date
            FROM lessons ORDER BY id DESC LIMIT 200
        """).fetchall()
        con.close()
        lessons_from_db = [
            {"trade_id": r[0], "symbol": r[1], "market": r[2],
             "pnl": r[3], "lecon": r[4], "pattern": r[5],
             "action_future": r[6], "type": r[7], "date": r[8]}
            for r in db_lessons
        ]
    except Exception:
        lessons_from_db = []

    # Fusionne les leçons RAM + DB (dédupliquées)
    lessons_ram   = memory.get("lessons", [])
    all_lessons   = lessons_from_db if lessons_from_db else lessons_ram
    patterns_work = memory.get("patterns_that_work", [])
    patterns_avoid= memory.get("patterns_to_avoid", [])
    threshold     = memory.get("confidence_threshold", CONFIDENCE_BASE)

    sim.update({
        "cash": CAPITAL_INITIAL, "initial": CAPITAL_INITIAL,
        "positions": {}, "trades": [], "equity_history": [],
        "session": sim.get("session", 0) + 1,
    })
    memory.update({
        "lessons":             all_lessons,
        "patterns_to_avoid":   patterns_avoid,
        "patterns_that_work":  patterns_work,
        "confidence_threshold": threshold,
        "total_wins": 0, "total_losses": 0,
        "analysis_history": [],
    })
    save_data()
    session = sim.get("session", 1)
    await update.message.reply_text(
        f"🔄 SESSION #{session} DÉMARRÉE\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Capital réinitialisé : ${CAPITAL_INITIAL:,.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 Leçons chargées (DB) : {len(all_lessons)}\n"
        f"✅ Patterns gagnants    : {len(patterns_work)}\n"
        f"❌ Patterns à éviter    : {len(patterns_avoid)}\n"
        f"⚙️  Seuil confiance      : {threshold}%\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Le bot repart avec TOUTE son expérience acquise.\n"
        f"Mode apprentissage : {'✅ ACTIF' if LEARN_MODE_ENABLED else '⏸ INACTIF'}"
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    learning_status = "✅ ON" if LEARN_MODE_ENABLED else "⏸ OFF"
    thresh = memory.get("confidence_threshold", CONFIDENCE_BASE)
    await update.message.reply_text(
        f"🤖 Tradbot — Toutes les commandes\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"▶️  /start      — Lance la simulation\n"
        f"⏹  /stop       — Arrête la simulation\n"
        f"🔄 /reset      — Nouveau capital $1000\n"
        f"               (leçons conservées)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 /status     — État rapide du bot\n"
        f"💼 /portfolio  — Capital, PnL, stats\n"
        f"📍 /positions  — Trades en cours\n"
        f"📚 /lecons     — Ce que j'ai appris\n"
        f"🔍 /scan       — Meilleures opportunités\n"
        f"📈 /marches    — Prix actions/forex/crypto\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ /fermer     — Ferme tous les trades\n"
        f"🎓 /apprendre  — Mode apprentissage {learning_status}\n"
        f"               (trade même sur signaux faibles)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️  Paramètres actuels :\n"
        f"  Seuil IA     : {thresh}% (auto-ajusté)\n"
        f"  Stop-Loss    : -{STOP_LOSS_PCT*100:.1f}%\n"
        f"  Take-Profit  : +{TAKE_PROFIT_PCT*100:.1f}%\n"
        f"  Trailing SL  : -{TRAILING_PCT*100:.1f}%\n"
        f"  Micro SL/TP  : -{MICRO_SL_PCT*100:.1f}% / +{MICRO_TP_PCT*100:.1f}%\n"
        f"  Capital      : ${sim['cash']:.2f} dispo\n"
        f"  Marchés      : {len(ALL_SYMBOLS)} cryptos + actions + forex + commodités"
        f"\n━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Surveillance & apprentissage :\n"
        f"  /signaux   — Derniers signaux des traders Twitter/YouTube\n"
        f"  /regles    — Règles de trading auto-générées par le bot"
    )


async def cmd_apprendre(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Active/désactive le mode apprentissage forcé."""
    if not _auth(update): return
    global LEARN_MODE_ENABLED
    LEARN_MODE_ENABLED = not LEARN_MODE_ENABLED
    status = "✅ ACTIVÉ" if LEARN_MODE_ENABLED else "⏸ DÉSACTIVÉ"
    await update.message.reply_text(
        f"🎓 Mode apprentissage forcé: {status}\n"
        f"Seuil min: {LEARN_MODE_CONF_MIN}% (vs {memory.get('confidence_threshold', CONFIDENCE_BASE)}% normal)\n"
        f"Taille trade: {LEARN_MODE_MAX_PCT*100:.0f}% du cash (vs {MAX_PCT_PER_TRADE*100:.0f}% normal)\n"
        f"{'Le bot trade même avec signal faible pour apprendre.' if LEARN_MODE_ENABLED else 'Le bot trade uniquement sur signaux forts.'}"
    )


async def cmd_marches(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Affiche un aperçu des marchés disponibles et prix actuels."""
    if not _auth(update): return
    await update.message.reply_text("📊 Récupération des prix...")
    try:
        lines = ["📊 MARCHÉS DISPONIBLES\n━━━━━━━━━━━━━"]

        # Crypto top 5
        prices = get_prices_batch()
        lines.append("🪙 CRYPTO (Bybit)")
        for sym in ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT"]:
            p = prices.get(sym, 0)
            lines.append(f"  {sym.replace('USDT',''): <6} ${p:,.4f}")

        # Actions top 5
        lines.append("\n📈 ACTIONS US (Yahoo)")
        for ticker, name in list(STOCKS_SYMBOLS.items())[:5]:
            p = get_yahoo_price(ticker)
            lines.append(f"  {name: <12} ${p:,.2f}")

        # Forex
        lines.append("\n💱 FOREX (Yahoo)")
        for ticker, name in list(FOREX_SYMBOLS.items())[:4]:
            p = get_yahoo_price(ticker)
            lines.append(f"  {name: <10} {p:.4f}")

        # Commodités
        lines.append("\n🏅 MATIÈRES PREMIÈRES")
        for ticker, name in COMMODITY_SYMBOLS.items():
            p = get_yahoo_price(ticker)
            lines.append(f"  {name: <12} ${p:,.2f}")

        lines.append(f"\n📊 Total: {len(ALL_SYMBOLS)} cryptos + "
                     f"{len(STOCKS_SYMBOLS)} actions + "
                     f"{len(FOREX_SYMBOLS)} forex + "
                     f"{len(COMMODITY_SYMBOLS)} commodités")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Erreur: {e}")


async def cmd_signaux(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Affiche les derniers signaux collectés des traders."""
    if not _auth(update): return
    summary = get_db_trader_signals_summary()
    await update.message.reply_text(
        f"📡 SIGNAUX TRADERS EN TEMPS RÉEL\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{summary}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Sources: Twitter/Nitter + YouTube RSS\n"
        f"Collecte: toutes les 5 min automatiquement"
    )


async def cmd_regles(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Affiche les règles de trading auto-générées par le bot."""
    if not _auth(update): return
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute("""
            SELECT rule, win_rate, sample_size, created_date
            FROM trading_rules WHERE active=1
            ORDER BY win_rate DESC LIMIT 10
        """).fetchall()
        # Récupère aussi la meilleure stratégie testée
        strats = con.execute("""
            SELECT strategy_name, win_rate, total_pnl
            FROM strategy_tests ORDER BY win_rate DESC LIMIT 3
        """).fetchall()
        con.close()

        if not rows:
            await update.message.reply_text(
                "🧠 Aucune règle auto-générée encore.\n"
                f"Le bot génère ses règles tous les 10 trades.\n"
                f"Trades actuels: {len([t for t in sim['trades'] if t.get('pnl')])}/10"
            )
            return

        lines = [f"🧠 MES RÈGLES AUTO-GÉNÉRÉES ({len(rows)})\n━━━━━━━━━━━━━━━━━━━"]
        for r in rows:
            lines.append(f"• {r[0]}\n  WR: {r[1]:.0f}% sur {r[2]} trades ({r[3]})")

        if strats:
            lines.append("\n📊 STRATÉGIES TESTÉES:")
            for s in strats:
                lines.append(f"  {s[0]}: WR {s[1]:.0f}% | PnL ${s[2]:+.2f}")

        lines.append(f"\n⚙️  SL actuel: {STOP_LOSS_PCT*100:.1f}% | TP: {TAKE_PROFIT_PCT*100:.1f}%")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Erreur: {e}")


# ═══════════════════════════════════════════════════════════════
#  APPLICATION TELEGRAM (webhook)
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
        ("reset",     cmd_reset),
        ("apprendre", cmd_apprendre),
        ("marches",   cmd_marches),
        ("help",      cmd_help),
        ("signaux",   cmd_signaux),
        ("regles",    cmd_regles),
    ]:
        _app.add_handler(CommandHandler(cmd, fn))

    await _app.initialize()
    await _app.start()

    if WEBHOOK_URL:
        full = WEBHOOK_URL.rstrip("/") + WEBHOOK_PATH
        await _app.bot.set_webhook(
            url=full, drop_pending_updates=True,
            allowed_updates=["message"])
        print(f"Webhook: {full}")
    else:
        print("⚠️  WEBHOOK_URL non définie")

    print("Simulation Bot v4 prêt — /start pour lancer")

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
def auto_start():
    """Lance automatiquement la simulation au démarrage de Koyeb.
    Plus besoin de /start manuellement après chaque redéploiement."""
    time.sleep(5)  # attend que le webhook soit enregistré
    send = make_send(TELEGRAM_CHAT_ID)
    if bot_state["running"]:
        return
    bot_state.update({
        "running": True, "trades_today": 0, "cycle_count": 0,
        "last_heartbeat": None, "last_monitor": 0, "last_micro": 0,
        "last_scalp": 0, "last_deep": 0, "last_status": 0,
    })
    send(
        "🔄 Redémarrage automatique détecté\n"
        "La simulation reprend automatiquement...\n"
        "Tape /stop pour l'arrêter."
    )
    threading.Thread(target=trading_loop,  args=(send,), daemon=True).start()
    threading.Thread(target=watchdog,      args=(send,), daemon=True).start()
    threading.Thread(target=daily_summary, args=(send,), daemon=True).start()


if __name__ == "__main__":
    print("🚀 Simulation Trading Bot v4")
    init_db()
    load_data()
    threading.Thread(target=run_server,  daemon=True).start()
    threading.Thread(target=self_ping,   daemon=True).start()
    threading.Thread(target=auto_start,  daemon=True).start()
    print("Serveur HTTP port 8000")
    asyncio.run(run_telegram())
