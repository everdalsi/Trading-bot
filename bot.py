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
import json, sqlite3
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

# ── Fréquences ────────────────────────────────────────────────
CYCLE_MICRO   = 8     # micro-trades : toutes les 8s
CYCLE_MONITOR = 15    # surveillance SL/TP : toutes les 15s
CYCLE_SCALP   = 60    # scalping classique : toutes les 60s
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

# ── Univers de trading (données réelles Bybit) ────────────────
ALL_SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "DOGEUSDT","ADAUSDT","AVAXUSDT","MATICUSDT","LINKUSDT",
    "DOTUSDT","UNIUSDT","ATOMUSDT","LTCUSDT","NEARUSDT",
    "APTUSDT","ARBUSDT","OPUSDT","INJUSDT","SUIUSDT",
]

# Sous-ensemble pour micro-trading (les plus liquides)
MICRO_SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "DOGEUSDT","AVAXUSDT","LINKUSDT","ARBUSDT","APTUSDT",
]

DB_FILE   = "sim_v4.db"
DATA_FILE = Path("sim_portfolio.json")

AI_MODELS = [
    "llama-3.3-70b-versatile",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]

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
def ask_model(model: str, prompt: str) -> dict:
    try:
        r = groq_client.chat.completions.create(
            model=model, max_tokens=250, temperature=0.1,
            messages=[
                {"role":"system","content":"Tu es un expert trading. Réponds UNIQUEMENT en JSON valide, sans texte avant ou après, sans backticks."},
                {"role":"user","content":prompt}
            ],
        )
        t = r.choices[0].message.content.strip()
        # Nettoyage agressif
        t = t.replace("```json","").replace("```","").strip()
        # Extrait uniquement le JSON si du texte parasite précède
        start = t.find("{")
        end   = t.rfind("}") + 1
        if start >= 0 and end > start:
            t = t[start:end]
        return json.loads(t)
    except json.JSONDecodeError as e:
        print(f"[AI-JSON] {model}: {e}")
        return {"signal":"HOLD","confidence":0,"reason":"json_error","risk":"HIGH"}
    except Exception as e:
        print(f"[AI-ERR] {model}: {e}")
        return {"signal":"HOLD","confidence":0,"reason":"api_error","risk":"HIGH"}


def vote(prompt: str) -> dict:
    results = []
    lock    = threading.Lock()

    def worker(m):
        r = ask_model(m, prompt)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker, args=(m,), daemon=True)
               for m in AI_MODELS]
    for t in threads: t.start()
    for t in threads: t.join(timeout=18)

    if not results:
        return {"signal":"HOLD","confidence":0,"reason":"timeout",
                "risk":"HIGH","votes":[],"consensus":"0/3"}

    signals    = [r.get("signal","HOLD") for r in results]
    vote_count = Counter(signals)
    winner, n  = vote_count.most_common(1)[0]

    if n < 2:
        return {"signal":"HOLD","confidence":0,
                "reason":f"Désaccord IA ({'/'.join(signals)})",
                "risk":"HIGH","votes":signals,"consensus":"0/3"}

    concordant = [r for r in results if r.get("signal")==winner]
    conf = round(sum(r.get("confidence",0) for r in concordant)/len(concordant))
    if n==3: conf = min(95, conf+5)
    best = max(concordant, key=lambda r: r.get("confidence",0))

    return {
        "signal":    winner,
        "confidence":conf,
        "reason":    best.get("reason",""),
        "risk":      best.get("risk","MEDIUM"),
        "votes":     signals,
        "consensus": f"{n}/3",
        "market":    best.get("market","SPOT"),
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

    prompt = f"""Tu es un trader algorithmique expert en simulation.
Tu dois décider si simuler un trade sur {symbol} en ce moment.

━━ DONNÉES TEMPS RÉEL ━━
Prix: ${price:.6f}
{fear_greed}
OrderBook: {ob['pressure']} (ratio={ob['ratio']})
Déjà en position: {'OUI' if in_pos else 'NON'}

━━ INDICATEURS TECHNIQUES ━━
RSI: {ind.get('rsi','?')} | MACD_hist: {ind.get('macd_h','?')}
Momentum 5min: {ind.get('mom5','?')}% | 15min: {ind.get('mom15','?')}%
BB%: {ind.get('bb_pct','?')} | Volatilité: {ind.get('vol','?')}%
Trend EMA: {ind.get('trend','?')} | EMA Cross: {ind.get('ema_cross','?')}

━━ CONFLUENCE MULTI-TF ━━
Score: {conf['score']}/9 → {conf['direction']}
Signaux: {', '.join(conf['signals'][:5])}

━━ PATTERNS DÉTECTÉS ━━
Haussiers: {pat_names_buy or 'Aucun'}
Baissiers: {pat_names_sell or 'Aucun'}

━━ MÉMOIRE HISTORIQUE ━━
Patterns gagnants {symbol}: {best_p or 'Aucun encore'}
Patterns perdants {symbol}: {worst_p or 'Aucun encore'}

━━ RÈGLES SIMULATION ━━
- Simulation pure: pas de vrai argent, apprentissage par l'exécution
- BUY = simuler achat SPOT (prix monte → profit)
- SELL+FUTURES = simuler short (prix baisse → profit, levier x{LEVERAGE_SIM})
- Seuil minimum: {thresh}% de confiance ET risk LOW ou MEDIUM
- JAMAIS trader si Pump/Dump détecté
- Cherche RR minimum 1.5:1 (TP {TAKE_PROFIT_PCT*100:.0f}% vs SL {STOP_LOSS_PCT*100:.0f}%)

JSON strict (sans backticks):
{{"signal":"BUY ou SELL ou HOLD","confidence":0-100,"reason":"raison précise courte","risk":"LOW ou MEDIUM ou HIGH","market":"SPOT ou FUTURES"}}"""

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
    pct      = calc_position_size(conf, market)
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

    send_fn(
        f"{s_emoji} TRADE SIMULÉ OUVERT #{trade['id']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 {symbol.replace('USDT','')} | {m_emoji}{market}"
        f"{' x'+str(leverage) if leverage>1 else ''} | {side}\n"
        f"💵 Prix entrée  : ${price:.6f}\n"
        f"📦 Quantité sim : {qty:.6f}\n"
        f"💰 Capital engagé: ${amount:.2f} ({pct*100:.0f}% du cash)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 Stop-Loss    : ${sl:.6f} (-{STOP_LOSS_PCT*100:.1f}%)\n"
        f"🎯 Take-Profit  : ${tp:.6f} (+{TAKE_PROFIT_PCT*100:.1f}%)\n"
        f"📐 Trailing SL  : -{TRAILING_PCT*100:.1f}% du pic\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 Raison       : {reason[:100]}\n"
        f"📊 Patterns     : {', '.join(trade['patterns'][:2]) or 'Aucun'}\n"
        f"🔒 Confiance    : {conf}% | Cash restant: ${sim['cash']:.2f}"
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

    send_fn(
        f"{e_main} TRADE SIMULÉ FERMÉ #{pos['id']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 {pos['symbol'].replace('USDT','')} | {pos['market']} | {side}\n"
        f"💵 Entrée  : ${entry:.6f}\n"
        f"💵 Sortie  : ${price:.6f} ({chg:+.2f}%)\n"
        f"⏱ Durée   : {duration} min\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{e_pnl} PnL sim   : ${pnl:+.4f} ({pnl_pct:+.2f}%)\n"
        f"💰 Cash sim : ${sim['cash']:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Raison   : {reason}\n"
        f"🆔 Trade #{pos['id']} | ⏳ Analyse en cours..."
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
            model=AI_MODELS[0], max_tokens=200, temperature=0.2,
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
            e = "✅" if lesson["type"]=="succes" else "❌"
            send_fn(
                f"📚 LEÇON APPRISE #{trade['id']}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{e} {lesson['type'].upper()} | {trade['symbol'].replace('USDT','')} "
                f"${trade['pnl']:+.4f}\n"
                f"💡 Leçon  : {lesson['lecon']}\n"
                f"🔍 Pattern: {lesson['pattern']}\n"
                f"📌 Règle  : {lesson['action_future']}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 Win Rate: {stats['win_rate']}% ({stats['total']} trades)\n"
                f"⚙️  Seuil   : {new_thresh}% | 🧠 {len(memory['lessons'])} leçons"
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

    amount = min(sim["cash"] * MICRO_MAX_PCT, sim["cash"] * 0.10)
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
        f"⚡ MICRO #{trade['id']} {coin} {side}\n"
        f"💵 ${price:.6f} score={score:+d} conf={conf}%\n"
        f"💰 ${amount:.2f} | SL:-{MICRO_SL_PCT*100:.1f}% TP:+{MICRO_TP_PCT*100:.1f}%\n"
        f"⏱ Timeout {MICRO_MAX_DURATION}s | {reason[:60]}"
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
            e_pnl = "🤑" if pnl>0 else "💸"
            coin  = symbol.replace("USDT","")
            chg   = (price-entry)/entry*100
            send_fn(
                f"{e} MICRO #{pos['id']} {coin}\n"
                f"${entry:.6f}→${price:.6f} ({chg:+.3f}%)\n"
                f"{e_pnl} PnL: ${pnl:+.6f} | {reason}"
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

                send_fn(
                    f"⚡ CYCLE #{cycle} — "
                    f"{datetime.now().strftime('%H:%M:%S')}\n"
                    f"💰 ${equity:.2f} ({pnl_tot:+.2f}) | "
                    f"Cash: ${sim['cash']:.2f}\n"
                    f"📍 {len(sim['positions'])}/{MAX_POSITIONS} positions | "
                    f"Seuil: {threshold}%\n"
                    f"Scan de {len(ALL_SYMBOLS)} cryptos..."
                )

                opps = scan_market()

                if not opps:
                    send_fn("📭 Marché calme — aucune opportunité claire.")
                else:
                    # Résumé du scan
                    scan_lines = []
                    for o in opps[:5]:
                        e = "🟢" if o["direction"]=="BUY" else "🔴"
                        alert = " ⚠️" if o["has_alert"] else ""
                        scan_lines.append(
                            f"  {e}{alert} {o['symbol'].replace('USDT',''):6s} "
                            f"score={o['score']:+d} RSI={o['ind'].get('rsi',0):.0f} "
                            f"mom={o['ind'].get('mom5',0):+.1f}%"
                        )
                    send_fn("🎯 Top opportunités:\n" + "\n".join(scan_lines))

                    # Analyse + décision sur les meilleures
                    for opp in opps[:4]:
                        if not bot_state["running"]: break
                        if opp["has_alert"]:
                            send_fn(
                                f"🚨 {opp['symbol'].replace('USDT','')} ignoré\n"
                                f"Manipulation détectée — sécurité avant tout."
                            )
                            continue

                        coin = opp["symbol"].replace("USDT","")
                        send_fn(f"🔍 Analyse {coin} (3 modèles IA en vote)...")

                        result = analyze(opp, fear_greed)
                        signal = result["signal"]
                        conf   = result["confidence"]
                        risk   = result["risk"]
                        votes  = result.get("votes", [])
                        cns    = result.get("consensus","?")
                        reason = result.get("reason","")
                        conf_tf= result.get("confluence",{})

                        sig_e  = {"BUY":"🟢","SELL":"🔴","HOLD":"⚪"}.get(signal,"⚪")
                        in_pos = any(p["symbol"]==opp["symbol"]
                                     for p in sim["positions"].values())

                        send_fn(
                            f"{sig_e} {coin}: {signal} {conf}% [{cns}]\n"
                            f"  Votes: {' / '.join(votes)}\n"
                            f"  Conf TF: {conf_tf.get('score',0)}/9 → "
                            f"{conf_tf.get('direction','?')}\n"
                            f"  Risque: {risk}\n"
                            f"  {reason[:90]}"
                        )

                        # ── Décision ────────────────────────────
                        if signal == "HOLD" or conf < threshold or risk == "HIGH":
                            if signal != "HOLD":
                                send_fn(
                                    f"⏸ {coin} ignoré\n"
                                    f"  conf={conf}% < seuil={threshold}% "
                                    f"ou risque={risk}"
                                )
                            continue

                        if in_pos and signal in ("BUY","SELL"):
                            # Fermeture si signal contraire
                            for pk, pos in list(sim["positions"].items()):
                                if (pos["symbol"]==opp["symbol"] and
                                        ((pos["side"]=="LONG" and signal=="SELL") or
                                         (pos["side"]=="SHORT" and signal=="BUY"))):
                                    close_trade(pk, opp["price"],
                                                f"Signal contraire {conf}%", send_fn)
                            continue

                        if not in_pos:
                            open_trade(result, send_fn)

            except Exception as e:
                print(f"[SCALP] {e}")
                send_fn(f"⚠️ Erreur cycle #{cycle}: {str(e)[:80]}")

            bot_state["last_scalp"] = now

        # ══ 5min : Analyse profonde futures ═══════════════════
        if now - bot_state["last_deep"] >= CYCLE_DEEP:
            try:
                _deep_futures(send_fn, fear_greed)
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
    equity = get_equity()
    pnl    = equity - sim["initial"]
    stats  = get_stats()
    wr_db  = db_win_rate(30)
    sym_stats = db_symbol_stats()
    thresh = memory.get("confidence_threshold", CONFIDENCE_BASE)

    pos_lines = ""
    if sim["positions"]:
        prices = get_prices_batch()
        for pos in sim["positions"].values():
            p    = prices.get(pos["symbol"], pos["price_in"])
            chg  = (p-pos["price_in"])/pos["price_in"]*100 * pos.get("leverage",1)
            e    = "📈" if chg>0 else "📉"
            pos_lines += (f"\n  {e} {pos['symbol'].replace('USDT',''):6s} "
                          f"{pos['side']} {chg:+.2f}%")

    sym_str = " | ".join(
        f"{s['s']}:{s['wr']:.0f}%WR" for s in sym_stats
    ) or "Aucun encore"

    micro_count = bot_state.get("micro_count", 0)
    micro_pos   = sum(1 for p in sim["positions"].values()
                      if p.get("trade_type")=="MICRO")
    classic_pos = len(sim["positions"]) - micro_pos
    send_fn(
        f"📋 BILAN 15min — {datetime.now().strftime('%H:%M:%S')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Capital   : ${equity:.2f} ({pnl/sim['initial']*100:+.1f}%)\n"
        f"📈 PnL total : ${pnl:+.2f}\n"
        f"💵 Cash libre: ${sim['cash']:.2f}\n"
        f"📍 Positions : {len(sim['positions'])} "
        f"(⚡{micro_pos} micro | 🔍{classic_pos} classique){pos_lines}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 Win Rate  : {stats['win_rate']}% | DB(30): {wr_db}%\n"
        f"📊 Trades    : {stats['total']} total | {bot_state['trades_today']} aujourd'hui\n"
        f"⚡ Micro-trades session: {micro_count}\n"
        f"⏱ Durée moy : {stats['avg_dur']} min\n"
        f"🥇 Meilleurs : {sym_str}\n"
        f"⚙️  Seuil auto : {thresh}%\n"
        f"📚 Leçons    : {len(memory['lessons'])} | 🔄 Cycle #{bot_state['cycle_count']}"
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


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    bot_state.update({
        "running": False, "cycle_count": 0, "trades_today": 0,
        "last_monitor": 0, "last_scalp": 0, "last_deep": 0, "last_status": 0,
    })
    sim.update({
        "cash": CAPITAL_INITIAL, "initial": CAPITAL_INITIAL,
        "positions": {}, "trades": [], "equity_history": [],
    })
    memory.update({
        "lessons": [], "patterns_to_avoid": [], "patterns_that_work": [],
        "confidence_threshold": CONFIDENCE_BASE,
        "total_wins": 0, "total_losses": 0,
    })
    save_data()
    await update.message.reply_text(
        f"🔄 Simulation réinitialisée.\n"
        f"Capital virtuel: ${CAPITAL_INITIAL:,.2f}\n"
        f"Mémoire RAM effacée. Base SQLite conservée."
    )


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
