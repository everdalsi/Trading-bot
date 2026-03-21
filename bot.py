import os
import time
import threading
import feedparser
import requests
import asyncio
import json
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from collections import Counter

from groq import Groq
from pybit.unified_trading import HTTP
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════
GROQ_KEY         = os.environ.get("ANTHROPIC_KEY")
BINANCE_KEY      = os.environ.get("BINANCE_KEY")
BINANCE_SECRET   = os.environ.get("BINANCE_SECRET")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
WEBHOOK_URL      = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_PATH     = "/webhook"
WEBHOOK_PORT     = 8000

# Seuils — auto-ajustés selon le win rate
CONFIDENCE_BASE      = 68     # seuil de départ
CONFIDENCE_MIN       = 60     # jamais en dessous
CONFIDENCE_MAX       = 82     # jamais au dessus
STOP_LOSS_PCT        = 0.04   # -4%
TAKE_PROFIT_PCT      = 0.06   # +6%
WATCHDOG_TIMEOUT     = 900
DATA_FILE            = Path("trading_data.json")
DB_FILE              = "memory.db"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# Modèles IA pour le vote majoritaire
AI_MODELS = [
    "llama-3.3-70b-versatile",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]

# ═══════════════════════════════════════════════════════════════
#  CLIENTS
# ═══════════════════════════════════════════════════════════════
groq_client = Groq(api_key=GROQ_KEY)
bybit       = HTTP(api_key=BINANCE_KEY, api_secret=BINANCE_SECRET)

# ═══════════════════════════════════════════════════════════════
#  ETAT GLOBAL
# ═══════════════════════════════════════════════════════════════
DEFAULT_PORTFOLIO = {
    "cash": 1000.0, "initial": 1000.0,
    "positions": {}, "trades": [],
}
DEFAULT_MEMORY = {
    "lessons": [], "patterns_to_avoid": [],
    "patterns_that_work": [], "analysis_history": [],
    "confidence_threshold": CONFIDENCE_BASE,  # auto-ajusté
}

portfolio: dict = {}
memory: dict    = {}
bot_state = {"running": False, "thread": None, "last_heartbeat": None}
_main_loop = None
_app       = None


# ═══════════════════════════════════════════════════════════════
#  MÉMOIRE LONGUE TERME — SQLite
# ═══════════════════════════════════════════════════════════════
def init_db():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY, symbol TEXT, type TEXT,
        price_in REAL, price_out REAL, qty REAL, amount_usd REAL,
        pnl REAL, confidence INTEGER, reason TEXT, exit_reason TEXT,
        time_in TEXT, time_out TEXT, patterns TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS lessons (
        id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id INTEGER,
        symbol TEXT, pnl REAL, lecon TEXT, pattern TEXT,
        action_future TEXT, type TEXT, date TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS backtest_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT,
        symbol TEXT, strategy TEXT, win_rate REAL,
        total_trades INTEGER, total_pnl REAL, notes TEXT
    )""")
    con.commit()
    con.close()


def db_save_trade(trade: dict):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT OR REPLACE INTO trades VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            trade["id"], trade.get("symbol"), trade["type"],
            trade["price_in"], trade.get("price_out"),
            trade["qty"], trade["amount_usd"],
            trade.get("pnl"), trade["confidence"],
            trade["reason"], trade.get("exit_reason"),
            trade["time_in"], trade.get("time_out"),
            json.dumps(trade.get("patterns_detected", [])),
        ))
        con.commit()
        con.close()
    except Exception as e:
        print(f"[DB] Erreur save trade: {e}")


def db_save_lesson(lesson: dict):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT INTO lessons
            (trade_id, symbol, pnl, lecon, pattern, action_future, type, date)
            VALUES (?,?,?,?,?,?,?,?)""", (
            lesson.get("trade_id"), lesson.get("symbol"),
            lesson.get("pnl"), lesson.get("lecon"),
            lesson.get("pattern"), lesson.get("action_future"),
            lesson.get("type"), lesson.get("date"),
        ))
        con.commit()
        con.close()
    except Exception as e:
        print(f"[DB] Erreur save lesson: {e}")


def db_get_best_patterns(symbol: str, limit=10) -> list:
    """Récupère les patterns gagnants historiques pour ce symbole."""
    try:
        con = sqlite3.connect(DB_FILE)
        rows = con.execute("""
            SELECT pattern, COUNT(*) as cnt, AVG(pnl) as avg_pnl
            FROM lessons WHERE symbol=? AND type='succes'
            GROUP BY pattern ORDER BY avg_pnl DESC LIMIT ?
        """, (symbol, limit)).fetchall()
        con.close()
        return [{"pattern": r[0], "count": r[1], "avg_pnl": r[2]} for r in rows]
    except Exception:
        return []


def db_get_worst_patterns(symbol: str, limit=10) -> list:
    try:
        con = sqlite3.connect(DB_FILE)
        rows = con.execute("""
            SELECT pattern, COUNT(*) as cnt, AVG(pnl) as avg_pnl
            FROM lessons WHERE symbol=? AND type='erreur'
            GROUP BY pattern ORDER BY avg_pnl ASC LIMIT ?
        """, (symbol, limit)).fetchall()
        con.close()
        return [{"pattern": r[0], "count": r[1], "avg_pnl": r[2]} for r in rows]
    except Exception:
        return []


def db_get_win_rate_last_n(n=20) -> float:
    try:
        con = sqlite3.connect(DB_FILE)
        rows = con.execute(
            "SELECT pnl FROM trades WHERE pnl IS NOT NULL ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        con.close()
        if not rows:
            return 50.0
        wins = sum(1 for r in rows if r[0] > 0)
        return round(wins / len(rows) * 100, 1)
    except Exception:
        return 50.0


def db_save_backtest(symbol, strategy, win_rate, total_trades, total_pnl, notes):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT INTO backtest_results
            (date, symbol, strategy, win_rate, total_trades, total_pnl, notes)
            VALUES (?,?,?,?,?,?,?)""", (
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            symbol, strategy, win_rate, total_trades, total_pnl, notes,
        ))
        con.commit()
        con.close()
    except Exception as e:
        print(f"[DB] Erreur backtest: {e}")


# ═══════════════════════════════════════════════════════════════
#  PERSISTANCE JSON (portfolio + mémoire courte)
# ═══════════════════════════════════════════════════════════════
def save_data():
    try:
        DATA_FILE.write_text(
            json.dumps({"portfolio": portfolio, "memory": memory},
                       indent=2, default=str)
        )
    except Exception as e:
        print(f"[SAVE] {e}")


def load_data():
    global portfolio, memory
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text())
            portfolio = data.get("portfolio", {})
            memory    = data.get("memory", {})
            for k, v in DEFAULT_PORTFOLIO.items():
                portfolio.setdefault(k, v)
            for k, v in DEFAULT_MEMORY.items():
                memory.setdefault(k, v)
            print(f"[LOAD] {len(portfolio['trades'])} trades | {len(memory['lessons'])} leçons")
            return
        except Exception as e:
            print(f"[LOAD] Erreur: {e}")
    portfolio = {k: (v.copy() if isinstance(v, (dict, list)) else v)
                 for k, v in DEFAULT_PORTFOLIO.items()}
    memory    = {k: (v.copy() if isinstance(v, (dict, list)) else v)
                 for k, v in DEFAULT_MEMORY.items()}
    print("[LOAD] Nouveau portefeuille $1000")


# ═══════════════════════════════════════════════════════════════
#  SOURCES DE DONNÉES MARCHÉ
# ═══════════════════════════════════════════════════════════════
RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
    "https://bitcoinmagazine.com/feed",
    "https://cryptonews.com/news/feed/",
]
REDDIT_FEEDS = [
    "https://www.reddit.com/r/Bitcoin/top/.rss?t=hour",
    "https://www.reddit.com/r/CryptoCurrency/top/.rss?t=hour",
    "https://www.reddit.com/r/ethtrader/top/.rss?t=hour",
]


def get_news() -> list:
    news = []
    for url in RSS_FEEDS:
        try:
            for e in feedparser.parse(url).entries[:2]:
                news.append(f"[NEWS] {e.title}: {e.get('summary','')[:150]}")
        except Exception:
            pass
    return news


def get_reddit() -> list:
    posts = []
    for url in REDDIT_FEEDS:
        try:
            for e in feedparser.parse(url).entries[:2]:
                posts.append(f"[REDDIT] {e.title}")
        except Exception:
            pass
    return posts


def get_google_trends() -> list:
    try:
        feed = feedparser.parse("https://trends.google.com/trending/rss?geo=US")
        return [f"[TREND] {e.title}" for e in feed.entries[:5]
                if any(k in e.title.lower()
                       for k in ["bitcoin", "crypto", "btc", "eth", "solana"])]
    except Exception:
        return []


def get_fear_greed() -> str:
    try:
        d = requests.get("https://api.alternative.me/fng/", timeout=5).json()["data"][0]
        return f"Fear & Greed: {d['value']}/100 ({d['value_classification']})"
    except Exception:
        return "Fear & Greed: indisponible"


def get_sp500_trend() -> str:
    """Corrélation avec S&P500 via Yahoo Finance (gratuit)."""
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC"
            "?interval=1d&range=5d", timeout=5,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c]
        if len(closes) >= 2:
            chg = (closes[-1] - closes[-2]) / closes[-2] * 100
            trend = "haussier" if chg > 0.3 else "baissier" if chg < -0.3 else "neutre"
            return f"S&P500: {trend} ({chg:+.2f}% hier)"
    except Exception:
        pass
    return "S&P500: indisponible"


def get_order_book_signal(symbol: str) -> str:
    """Analyse bid/ask ratio + liquidations Bybit."""
    try:
        ob = bybit.get_orderbook(category="spot", symbol=symbol, limit=50)
        bids = sum(float(b[1]) for b in ob["result"]["b"])
        asks = sum(float(a[1]) for a in ob["result"]["a"])
        ratio = bids / asks if asks > 0 else 1.0
        signal = "fort achat" if ratio > 1.5 else "fort vente" if ratio < 0.67 else "équilibré"
        return f"OrderBook {symbol.replace('USDT','')}: {signal} (bid/ask={ratio:.2f})"
    except Exception:
        return ""


def get_onchain_signals() -> str:
    """Données on-chain gratuites via CryptoQuant public API."""
    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=7", timeout=5
        ).json()["data"]
        values = [int(x["value"]) for x in r]
        trend = "amélioration" if values[0] > values[-1] else "dégradation"
        return f"Sentiment 7j: {trend} ({values[-1]}→{values[0]})"
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════
#  INDICATEURS TECHNIQUES MULTI-TIMEFRAME
# ═══════════════════════════════════════════════════════════════
def compute_indicators(closes: pd.Series) -> dict:
    """Calcule RSI, EMA, MACD, Bollinger, Volume sur une série."""
    rsi_val = 50.0
    try:
        delta = closes.diff()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)
        rs    = (gain.ewm(com=13, adjust=False).mean() /
                 loss.ewm(com=13, adjust=False).mean().replace(0, np.nan))
        rsi_val = float((100 - 100 / (1 + rs)).iloc[-1])
    except Exception:
        pass

    ema20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200 = float(closes.ewm(span=min(200, len(closes)), adjust=False).mean().iloc[-1])

    macd_line   = float((closes.ewm(span=12, adjust=False).mean() -
                         closes.ewm(span=26, adjust=False).mean()).iloc[-1])
    signal_line = float((closes.ewm(span=12, adjust=False).mean() -
                         closes.ewm(span=26, adjust=False).mean())
                        .ewm(span=9, adjust=False).mean().iloc[-1])

    sma20   = closes.rolling(20).mean()
    std20   = closes.rolling(20).std()
    bb_up   = float((sma20 + 2 * std20).iloc[-1])
    bb_low  = float((sma20 - 2 * std20).iloc[-1])
    bb_pct  = round((float(closes.iloc[-1]) - bb_low) / (bb_up - bb_low) * 100, 1) \
              if (bb_up - bb_low) > 0 else 50.0

    return {
        "rsi":         round(rsi_val, 1),
        "ema20":       round(ema20, 2),
        "ema50":       round(ema50, 2),
        "ema200":      round(ema200, 2),
        "macd":        round(macd_line, 4),
        "macd_signal": round(signal_line, 4),
        "macd_hist":   round(macd_line - signal_line, 4),
        "bb_upper":    round(bb_up, 2),
        "bb_lower":    round(bb_low, 2),
        "bb_pct":      bb_pct,   # 0=bas BB, 100=haut BB
        "trend":       "haussier" if ema20 > ema50 else "baissier",
        "above_200":   ema20 > ema200,
    }


def get_multi_timeframe(symbol: str) -> dict:
    """RSI/EMA/MACD/BB sur 3 timeframes : 15min, 1h, 4h."""
    result = {}
    for interval, label in [("15", "15m"), ("60", "1h"), ("240", "4h")]:
        try:
            raw    = bybit.get_kline(category="spot", symbol=symbol,
                                     interval=interval, limit=200)
            closes = pd.Series(
                [float(c[4]) for c in reversed(raw["result"]["list"])],
                dtype=float
            )
            result[label] = compute_indicators(closes)
            result[label]["price"] = round(float(closes.iloc[-1]), 2)
        except Exception as e:
            print(f"[MTF] Erreur {symbol} {label}: {e}")
            result[label] = {}
    return result


def get_timeframe_confluence(mtf: dict) -> dict:
    """
    Confluence entre les 3 timeframes.
    Retourne un score de -3 (très baissier) à +3 (très haussier).
    """
    score = 0
    signals = []
    for tf, data in mtf.items():
        if not data:
            continue
        tf_score = 0
        if data.get("rsi", 50) < 35:
            tf_score += 1
            signals.append(f"{tf}:RSI_survente")
        elif data.get("rsi", 50) > 70:
            tf_score -= 1
            signals.append(f"{tf}:RSI_surachat")
        if data.get("trend") == "haussier":
            tf_score += 1
            signals.append(f"{tf}:trend_haussier")
        else:
            tf_score -= 1
        if data.get("macd_hist", 0) > 0:
            tf_score += 1
            signals.append(f"{tf}:MACD_bullish")
        else:
            tf_score -= 1
        score += tf_score

    direction = "BUY" if score >= 2 else "SELL" if score <= -2 else "HOLD"
    return {"score": score, "direction": direction, "signals": signals}


# ═══════════════════════════════════════════════════════════════
#  DÉTECTION DE PATTERNS CHARTISTES
# ═══════════════════════════════════════════════════════════════
def detect_chart_patterns(symbol: str) -> list:
    """
    Détecte: Double Top/Bottom, Head & Shoulders, Triangle,
    Breakout, Pump & Dump.
    """
    patterns = []
    try:
        raw    = bybit.get_kline(category="spot", symbol=symbol,
                                 interval="60", limit=100)
        candles = list(reversed(raw["result"]["list"]))
        closes  = pd.Series([float(c[4]) for c in candles], dtype=float)
        highs   = pd.Series([float(c[2]) for c in candles], dtype=float)
        lows    = pd.Series([float(c[3]) for c in candles], dtype=float)
        volumes = pd.Series([float(c[5]) for c in candles], dtype=float)

        # ── Double Top ──────────────────────────────────────────
        recent_highs = highs[-30:]
        max1_idx = recent_highs[:15].idxmax()
        max2_idx = recent_highs[15:].idxmax()
        max1 = recent_highs[max1_idx]
        max2 = recent_highs[max2_idx]
        if abs(max1 - max2) / max1 < 0.015:  # < 1.5% d'écart
            patterns.append({
                "name": "Double Top",
                "signal": "SELL",
                "strength": "fort",
                "desc": f"Résistance double @ ${max1:,.0f}",
            })

        # ── Double Bottom ────────────────────────────────────────
        recent_lows = lows[-30:]
        min1_idx = recent_lows[:15].idxmin()
        min2_idx = recent_lows[15:].idxmin()
        min1 = recent_lows[min1_idx]
        min2 = recent_lows[min2_idx]
        if abs(min1 - min2) / min1 < 0.015:
            patterns.append({
                "name": "Double Bottom",
                "signal": "BUY",
                "strength": "fort",
                "desc": f"Support double @ ${min1:,.0f}",
            })

        # ── Head & Shoulders ─────────────────────────────────────
        h = highs[-20:].values
        if len(h) >= 5:
            left  = max(h[:5])
            head  = max(h[5:15])
            right = max(h[15:])
            if head > left * 1.02 and head > right * 1.02 and abs(left - right) / left < 0.03:
                patterns.append({
                    "name": "Head & Shoulders",
                    "signal": "SELL",
                    "strength": "très fort",
                    "desc": "Retournement baissier probable",
                })

        # ── Breakout haussier ────────────────────────────────────
        resistance = highs[-20:-5].max()
        last_close = float(closes.iloc[-1])
        avg_vol    = float(volumes[-20:].mean())
        last_vol   = float(volumes.iloc[-1])
        if last_close > resistance * 1.01 and last_vol > avg_vol * 1.5:
            patterns.append({
                "name": "Breakout haussier",
                "signal": "BUY",
                "strength": "fort",
                "desc": f"Cassure de ${resistance:,.0f} avec volume x{last_vol/avg_vol:.1f}",
            })

        # ── Pump & Dump (manipulation) ───────────────────────────
        pct_1h  = (float(closes.iloc[-1]) - float(closes.iloc[-4]))  / float(closes.iloc[-4])  * 100
        pct_4h  = (float(closes.iloc[-1]) - float(closes.iloc[-16])) / float(closes.iloc[-16]) * 100
        vol_spike = last_vol / avg_vol
        if pct_1h > 5 and vol_spike > 3:
            patterns.append({
                "name": "⚠️ Pump & Dump suspect",
                "signal": "HOLD",
                "strength": "ALERTE",
                "desc": f"+{pct_1h:.1f}% en 1h, volume x{vol_spike:.1f} — manipulation probable",
            })
        elif pct_1h < -5 and vol_spike > 3:
            patterns.append({
                "name": "⚠️ Dump brutal",
                "signal": "HOLD",
                "strength": "ALERTE",
                "desc": f"{pct_1h:.1f}% en 1h — vente panique ou manipulation",
            })

        # ── Triangle ascendant ───────────────────────────────────
        recent_close = closes[-15:]
        higher_lows  = all(lows.iloc[-i] > lows.iloc[-i-1] for i in range(1, 4))
        flat_highs   = highs[-10:].std() / highs[-10:].mean() < 0.005
        if higher_lows and flat_highs:
            patterns.append({
                "name": "Triangle ascendant",
                "signal": "BUY",
                "strength": "modéré",
                "desc": "Compression haussière avant breakout potentiel",
            })

    except Exception as e:
        print(f"[PATTERNS] Erreur {symbol}: {e}")

    return patterns


# ═══════════════════════════════════════════════════════════════
#  GESTION DYNAMIQUE DU RISQUE (Position Sizing)
# ═══════════════════════════════════════════════════════════════
def compute_position_size(symbol: str, confidence: int, mtf: dict) -> float:
    """
    Kelly Criterion simplifié + ajustement selon confluence.
    Retourne le % du cash à allouer (entre 10% et 45%).
    """
    win_rate = db_get_win_rate_last_n(20) / 100
    if win_rate < 0.4:
        win_rate = 0.4  # prudent par défaut

    # Kelly = W - (1-W)/R  avec R = TP/SL ratio
    r = TAKE_PROFIT_PCT / STOP_LOSS_PCT
    kelly = win_rate - (1 - win_rate) / r
    kelly = max(0.05, min(0.45, kelly))  # cap entre 5% et 45%

    # Ajustement selon la confluence multi-timeframe
    confluence = get_timeframe_confluence(mtf)
    if confluence["score"] >= 3:
        kelly *= 1.3
    elif confluence["score"] <= 1:
        kelly *= 0.7

    # Ajustement selon la confiance du signal
    conf_factor = (confidence - 60) / 40  # 0 à 1
    kelly *= (0.7 + 0.3 * conf_factor)

    final = round(min(0.45, max(0.08, kelly)), 2)
    print(f"[SIZING] {symbol}: kelly={kelly:.2f} conf={confidence}% → {final*100:.0f}% du cash")
    return final


# ═══════════════════════════════════════════════════════════════
#  AUTO-AJUSTEMENT DU SEUIL DE CONFIANCE
# ═══════════════════════════════════════════════════════════════
def auto_adjust_threshold():
    """
    Ajuste le seuil de confiance selon le win rate des 20 derniers trades.
    Win rate > 60% → baisse seuil (plus de trades)
    Win rate < 45% → hausse seuil (plus sélectif)
    """
    wr = db_get_win_rate_last_n(20)
    current = memory.get("confidence_threshold", CONFIDENCE_BASE)

    if wr > 60 and current > CONFIDENCE_MIN:
        new = max(CONFIDENCE_MIN, current - 2)
        print(f"[AUTO] Win rate {wr}% → seuil baissé {current}→{new}%")
    elif wr < 45 and current < CONFIDENCE_MAX:
        new = min(CONFIDENCE_MAX, current + 3)
        print(f"[AUTO] Win rate {wr}% → seuil monté {current}→{new}%")
    else:
        new = current

    memory["confidence_threshold"] = new
    return new


# ═══════════════════════════════════════════════════════════════
#  VOTE MAJORITAIRE — 3 MODÈLES IA
# ═══════════════════════════════════════════════════════════════
def ask_one_model(model: str, prompt: str) -> dict:
    try:
        resp = groq_client.chat.completions.create(
            model=model, max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        text  = resp.choices[0].message.content
        clean = text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except Exception as e:
        print(f"[AI] Erreur {model}: {e}")
        return {"signal": "HOLD", "confidence": 0, "reason": "erreur",
                "risk": "HIGH", "sentiment": "neutral", "key_signal": ""}


def majority_vote(prompt: str) -> dict:
    """
    Lance 3 modèles en parallèle et retourne le consensus.
    Signal retenu uniquement si ≥ 2/3 modèles sont d'accord.
    La confiance finale est la moyenne des confidences concordantes.
    """
    results = []
    threads = []
    lock    = threading.Lock()

    def worker(model):
        res = ask_one_model(model, prompt)
        with lock:
            results.append({"model": model, **res})

    for m in AI_MODELS:
        t = threading.Thread(target=worker, args=(m,))
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=20)

    if not results:
        return {"signal": "HOLD", "confidence": 0, "reason": "Pas de réponse",
                "risk": "HIGH", "sentiment": "neutral", "key_signal": "",
                "votes": []}

    # Compte les votes par signal
    signals   = [r["signal"] for r in results]
    vote_count = Counter(signals)
    winner    = vote_count.most_common(1)[0][0]
    count     = vote_count.most_common(1)[0][1]

    if count < 2:
        # Pas de consensus → HOLD
        return {"signal": "HOLD", "confidence": 0,
                "reason": f"Pas de consensus ({'/'.join(signals)})",
                "risk": "HIGH", "sentiment": "neutral", "key_signal": "",
                "votes": signals}

    # Moyenne des confidences des modèles qui ont voté pour le winner
    concordant  = [r for r in results if r["signal"] == winner]
    avg_conf    = round(sum(r.get("confidence", 0) for r in concordant) / len(concordant))
    best        = max(concordant, key=lambda r: r.get("confidence", 0))

    # Bonus de confiance si unanimité
    if count == 3:
        avg_conf = min(95, avg_conf + 5)

    return {
        "signal":     winner,
        "confidence": avg_conf,
        "reason":     best.get("reason", ""),
        "risk":       best.get("risk", "MEDIUM"),
        "sentiment":  best.get("sentiment", "neutral"),
        "key_signal": best.get("key_signal", ""),
        "votes":      signals,
        "consensus":  f"{count}/3",
    }


# ═══════════════════════════════════════════════════════════════
#  BACKTESTING AUTOMATIQUE
# ═══════════════════════════════════════════════════════════════
def run_backtest(symbol: str, strategy: str = "standard") -> dict:
    """
    Backtest simplifié sur les 30 derniers jours (données 4h).
    Simule la stratégie actuelle et retourne les métriques.
    """
    try:
        raw    = bybit.get_kline(category="spot", symbol=symbol,
                                 interval="240", limit=180)
        candles = list(reversed(raw["result"]["list"]))
        closes  = [float(c[4]) for c in candles]
        highs   = [float(c[2]) for c in candles]
        lows    = [float(c[3]) for c in candles]

        cash   = 1000.0
        pos    = None
        trades = []

        for i in range(50, len(closes)):
            c_series = pd.Series(closes[:i+1], dtype=float)
            ind      = compute_indicators(c_series)
            price    = closes[i]

            # Signal simple basé sur RSI + EMA
            rsi   = ind["rsi"]
            trend = ind["trend"]

            if pos is None:
                if rsi < 35 and trend == "haussier":
                    amount = cash * 0.35
                    qty    = amount / price
                    pos    = {"price_in": price, "qty": qty, "amount": amount}
                    cash  -= amount
            else:
                change = (price - pos["price_in"]) / pos["price_in"]
                if change >= TAKE_PROFIT_PCT or change <= -STOP_LOSS_PCT or rsi > 70:
                    pnl  = pos["qty"] * price - pos["amount"]
                    cash += pos["qty"] * price
                    trades.append(pnl)
                    pos = None

        if not trades:
            return {"symbol": symbol, "strategy": strategy,
                    "total_trades": 0, "win_rate": 0, "total_pnl": 0}

        wins     = [t for t in trades if t > 0]
        win_rate = round(len(wins) / len(trades) * 100, 1)
        total_pnl = round(sum(trades), 2)

        result = {
            "symbol":        symbol,
            "strategy":      strategy,
            "total_trades":  len(trades),
            "win_rate":      win_rate,
            "total_pnl":     total_pnl,
            "best":          round(max(trades), 2),
            "worst":         round(min(trades), 2),
        }
        db_save_backtest(symbol, strategy, win_rate, len(trades), total_pnl,
                         f"best={result['best']} worst={result['worst']}")
        return result
    except Exception as e:
        print(f"[BACKTEST] Erreur {symbol}: {e}")
        return {"symbol": symbol, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
#  PORTFOLIO & STATS
# ═══════════════════════════════════════════════════════════════
def get_price(symbol="BTCUSDT") -> float:
    return float(bybit.get_tickers(category="spot", symbol=symbol
                                   )["result"]["list"][0]["lastPrice"])


def get_all_prices() -> dict:
    prices = {}
    for sym in SYMBOLS:
        try:
            prices[sym] = get_price(sym)
        except Exception:
            pass
    return prices


def get_portfolio_value(prices: dict) -> float:
    total = portfolio["cash"]
    for sym, pos in portfolio["positions"].items():
        total += pos["qty"] * prices.get(sym, pos["price_in"])
    return total


def get_stats() -> dict:
    closed = [t for t in portfolio["trades"] if t.get("pnl") is not None]
    if not closed:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "best": 0, "worst": 0, "total_pnl": 0}
    pnls = [t["pnl"] for t in closed]
    wins = [p for p in pnls if p > 0]
    return {
        "total":     len(closed),
        "wins":      len(wins),
        "losses":    len(closed) - len(wins),
        "win_rate":  round(len(wins) / len(closed) * 100, 1),
        "best":      round(max(pnls), 2),
        "worst":     round(min(pnls), 2),
        "total_pnl": round(sum(pnls), 2),
    }


# ═══════════════════════════════════════════════════════════════
#  TRADING
# ═══════════════════════════════════════════════════════════════
def execute_buy(symbol: str, price: float, reason: str,
                confidence: int, size_pct: float = 0.33,
                patterns: list = None,
                send_fn=None) -> dict | None:
    if portfolio["cash"] < 10 or symbol in portfolio["positions"]:
        return None
    amount_usd = portfolio["cash"] * size_pct
    qty        = amount_usd / price
    portfolio["cash"] -= amount_usd

    trade = {
        "id":               len(portfolio["trades"]) + 1,
        "symbol":           symbol,
        "type":             "BUY",
        "price_in":         price,
        "price_out":        None,
        "qty":              qty,
        "amount_usd":       amount_usd,
        "reason":           reason,
        "confidence":       confidence,
        "time_in":          datetime.now().strftime("%Y-%m-%d %H:%M"),
        "time_out":         None,
        "pnl":              None,
        "exit_reason":      None,
        "patterns_detected": [p["name"] for p in (patterns or [])],
        "market_context":   memory["analysis_history"][-1]
                            if memory["analysis_history"] else {},
    }
    portfolio["trades"].append(trade)
    portfolio["positions"][symbol] = {
        "qty": qty, "amount_usd": amount_usd,
        "price_in": price, "trade_ref": trade["id"],
    }
    db_save_trade(trade)
    save_data()

    # ── Notification live achat ──────────────────────────────────
    if send_fn:
        sl_price = price * (1 - STOP_LOSS_PCT)
        tp_price = price * (1 + TAKE_PROFIT_PCT)
        coin     = symbol.replace("USDT", "")
        pat_str  = ", ".join(trade["patterns_detected"][:3]) or "Aucun"
        send_fn(
            f"🟢 ACHAT EXÉCUTÉ — {coin}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Prix entrée    : ${price:,.2f}\n"
            f"📦 Quantité       : {qty:.6f} {coin}\n"
            f"💰 Montant investi: ${amount_usd:,.2f} ({size_pct*100:.0f}% du cash)\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🛑 Stop-Loss      : ${sl_price:,.2f} (-{STOP_LOSS_PCT*100:.0f}%)\n"
            f"🎯 Take-Profit    : ${tp_price:,.2f} (+{TAKE_PROFIT_PCT*100:.0f}%)\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 Raison         : {reason}\n"
            f"📊 Patterns       : {pat_str}\n"
            f"🔒 Confiance      : {confidence}%\n"
            f"🆔 Trade #{trade['id']}"
        )
    return trade


def execute_sell(symbol: str, price: float, reason: str,
                 confidence: int, send_fn=None) -> dict | None:
    pos = portfolio["positions"].get(symbol)
    if not pos:
        return None
    amount_out = pos["qty"] * price
    portfolio["cash"] += amount_out
    del portfolio["positions"][symbol]

    trade = next((t for t in reversed(portfolio["trades"])
                  if t["id"] == pos["trade_ref"]), None)
    if trade:
        trade["price_out"]   = price
        trade["time_out"]    = datetime.now().strftime("%Y-%m-%d %H:%M")
        trade["pnl"]         = round(amount_out - pos["amount_usd"], 2)
        trade["exit_reason"] = reason
        db_save_trade(trade)

        # ── Notification live vente ──────────────────────────────
        if send_fn:
            coin    = symbol.replace("USDT", "")
            pnl     = trade["pnl"]
            chg_pct = (price - pos["price_in"]) / pos["price_in"] * 100
            emoji   = "✅" if pnl > 0 else "❌"
            duration = ""
            try:
                t_in  = datetime.strptime(trade["time_in"], "%Y-%m-%d %H:%M")
                mins  = int((datetime.now() - t_in).total_seconds() / 60)
                duration = f"⏱ Durée          : {mins} min\n"
            except Exception:
                pass
            send_fn(
                f"{emoji} VENTE EXÉCUTÉE — {coin}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💵 Prix entrée    : ${pos['price_in']:,.2f}\n"
                f"💵 Prix sortie    : ${price:,.2f} ({chg_pct:+.2f}%)\n"
                f"{duration}"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{'🤑' if pnl > 0 else '💸'} PnL             : ${pnl:+.2f}\n"
                f"💰 Cash restant   : ${portfolio['cash']:,.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📌 Raison sortie  : {reason}\n"
                f"🆔 Trade #{trade['id']}\n"
                f"⏳ Analyse en cours..."
            )
        learn_from_trade(trade, send_fn=send_fn)
    save_data()
    return trade


# ═══════════════════════════════════════════════════════════════
#  STOP-LOSS / TAKE-PROFIT DYNAMIQUE
# ═══════════════════════════════════════════════════════════════
def stoploss_watchdog(send_fn_sync):
    while True:
        time.sleep(60)
        if not portfolio.get("positions"):
            continue
        try:
            prices = get_all_prices()
            for symbol, pos in list(portfolio["positions"].items()):
                price  = prices.get(symbol)
                if not price:
                    continue
                change = (price - pos["price_in"]) / pos["price_in"]
                reason = None
                if change <= -STOP_LOSS_PCT:
                    reason = f"STOP-LOSS ({change*100:.1f}%)"
                elif change >= TAKE_PROFIT_PCT:
                    reason = f"TAKE-PROFIT ({change*100:.1f}%)"
                if reason:
                    trade = execute_sell(symbol, price, reason, 100,
                                         send_fn=send_fn_sync)
        except Exception as e:
            print(f"[SL/TP] {e}")


# ═══════════════════════════════════════════════════════════════
#  APPRENTISSAGE
# ═══════════════════════════════════════════════════════════════
def learn_from_trade(trade: dict, send_fn=None):
    if trade.get("pnl") is None:
        return
    try:
        best_p = db_get_best_patterns(trade.get("symbol", "BTC"), 5)
        worst_p = db_get_worst_patterns(trade.get("symbol", "BTC"), 5)
        verdict = ("TRADE PERDANT. Analyse précisément pourquoi."
                   if trade["pnl"] < 0
                   else "TRADE GAGNANT. Identifie ce qui a bien marché.")
        prompt = f"""Expert trading crypto. Analyse ce trade et extrais une leçon actionnable.

Symbol: {trade.get('symbol')} | PnL: ${trade['pnl']:+.2f}
Prix: ${trade['price_in']:,.2f} → ${trade['price_out']:,.2f}
Patterns détectés: {trade.get('patterns_detected', [])}
Raison entrée: {trade['reason']}
Raison sortie: {trade.get('exit_reason', '?')}
Confiance signal: {trade['confidence']}%

Meilleurs patterns historiques: {[p['pattern'] for p in best_p]}
Pires patterns historiques: {[p['pattern'] for p in worst_p]}

{verdict}

JSON strict (sans backticks):
{{"lecon": "leçon courte et actionnable", "pattern": "pattern précis identifié",
"action_future": "règle concrète pour le prochain trade", "type": "erreur ou succes"}}"""

        resp   = groq_client.chat.completions.create(
            model=AI_MODELS[0], max_tokens=350,
            messages=[{"role": "user", "content": prompt}],
        )
        lesson = json.loads(
            resp.choices[0].message.content
            .replace("```json", "").replace("```", "").strip()
        )
        lesson.update({
            "trade_id": trade["id"],
            "pnl":      trade["pnl"],
            "symbol":   trade.get("symbol", "BTC"),
            "date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        memory["lessons"].append(lesson)
        db_save_lesson(lesson)

        if lesson["type"] == "erreur":
            memory["patterns_to_avoid"].append(lesson["pattern"])
        else:
            memory["patterns_that_work"].append(lesson["pattern"])

        memory["lessons"]            = memory["lessons"][-50:]
        memory["patterns_to_avoid"]  = memory["patterns_to_avoid"][-20:]
        memory["patterns_that_work"] = memory["patterns_that_work"][-20:]

        # Auto-ajustement du seuil après chaque trade
        new_threshold = auto_adjust_threshold()
        save_data()
        print(f"[LEARN] {lesson['lecon']}")

        # ── Notification live leçon apprise ─────────────────────
        if send_fn:
            emoji_t = "❌" if lesson["type"] == "erreur" else "✅"
            emoji_p = "🤑" if trade["pnl"] > 0 else "💸"
            stats   = get_stats()
            send_fn(
                f"📚 LEÇON APPRISE — Trade #{trade['id']}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{emoji_t} Type           : {lesson['type'].upper()}\n"
                f"{emoji_p} PnL ce trade   : ${trade['pnl']:+.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💡 Leçon         : {lesson['lecon']}\n"
                f"🔍 Pattern       : {lesson['pattern']}\n"
                f"📌 Règle future  : {lesson['action_future']}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 Win Rate      : {stats['win_rate']}% ({stats['total']} trades)\n"
                f"⚙️  Seuil auto    : {new_threshold}%\n"
                f"🧠 Total leçons  : {len(memory['lessons'])}"
            )
    except Exception as e:
        print(f"[LEARN] {e}")


# ═══════════════════════════════════════════════════════════════
#  ANALYSE COMPLÈTE D'UN SYMBOLE
# ═══════════════════════════════════════════════════════════════
def analyze_symbol(symbol: str, news_text: str, fear_greed: str,
                   sp500: str, onchain: str) -> tuple:
    # 1. Données techniques multi-timeframe
    mtf        = get_multi_timeframe(symbol)
    confluence = get_timeframe_confluence(mtf)
    tech_15m   = mtf.get("15m", {})
    tech_1h    = mtf.get("1h", {})
    tech_4h    = mtf.get("4h", {})
    price      = tech_15m.get("price") or get_price(symbol)

    # 2. Patterns chartistes
    patterns   = detect_chart_patterns(symbol)
    pattern_names = [p["name"] for p in patterns]
    buy_patterns  = [p for p in patterns if p["signal"] == "BUY"]
    sell_patterns = [p for p in patterns if p["signal"] == "SELL"]
    alert_patterns = [p for p in patterns if p["signal"] == "HOLD"]

    # 3. Order book
    ob_signal  = get_order_book_signal(symbol)

    # 4. Mémoire longue terme
    best_p  = db_get_best_patterns(symbol, 5)
    worst_p = db_get_worst_patterns(symbol, 5)

    in_pos     = symbol in portfolio["positions"]
    threshold  = memory.get("confidence_threshold", CONFIDENCE_BASE)

    patterns_to_avoid  = "\n".join(memory["patterns_to_avoid"][-5:])  or "Aucun"
    patterns_that_work = "\n".join(memory["patterns_that_work"][-5:]) or "Aucun"
    recent_lessons     = "".join(
        f"- {l['lecon']} → {l['action_future']}\n"
        for l in memory["lessons"][-3:]
    ) or "Aucune leçon encore"

    def fmt_tech(data: dict, label: str) -> str:
        if not data:
            return f"{label}: indisponible"
        rsi = data.get('rsi', '?')
        rsi_note = ('⬆️ survente' if isinstance(rsi, float) and rsi < 35
                    else '⬇️ surachat' if isinstance(rsi, float) and rsi > 70
                    else '')
        return (f"{label}: RSI={rsi}{rsi_note} | EMA {data.get('trend','')} | "
                f"MACD_hist={data.get('macd_hist',0):.4f} | BB%={data.get('bb_pct',50):.0f}%")

    prompt = f"""Tu es un expert en trading crypto avec accès à des données complètes.
Décide BUY / SELL / HOLD pour {symbol} en te basant sur TOUTES les données.

━━ DONNÉES MARCHÉ ━━
Prix: ${price:,.2f} | {fear_greed} | {sp500}
{onchain}
{ob_signal}
Position actuelle: {'EN POSITION' if in_pos else 'PAS EN POSITION'}

━━ ANALYSE TECHNIQUE MULTI-TIMEFRAME ━━
{fmt_tech(tech_15m, '15min')}
{fmt_tech(tech_1h, '1h')}
{fmt_tech(tech_4h, '4h')}
Confluence: score={confluence['score']}/3 → {confluence['direction']}
Signaux: {', '.join(confluence['signals'][:5])}

━━ PATTERNS CHARTISTES DÉTECTÉS ━━
Haussiers: {[p['name']+' ('+p['strength']+')' for p in buy_patterns] or 'Aucun'}
Baissiers: {[p['name']+' ('+p['strength']+')' for p in sell_patterns] or 'Aucun'}
Alertes: {[p['desc'] for p in alert_patterns] or 'Aucune'}

━━ MÉMOIRE HISTORIQUE ({symbol}) ━━
Patterns gagnants: {[p['pattern'] for p in best_p] or 'Aucun'}
Patterns perdants: {[p['pattern'] for p in worst_p] or 'Aucun'}
Leçons récentes: {recent_lessons}
Patterns à éviter: {patterns_to_avoid}

━━ SIGNAUX ACTUALITÉ ━━
{news_text[:800]}

━━ RÈGLES ━━
- Alerte Pump&Dump détectée → HOLD obligatoire
- Confluence score ≥ 2 ET pattern chartiste concordant → signal renforcé
- Ne JAMAIS aller contre 3 timeframes alignés
- Seuil actuel: {threshold}% (auto-ajusté selon performance)

Réponds UNIQUEMENT en JSON strict (sans backticks):
{{"signal":"BUY ou SELL ou HOLD","confidence":0-100,"reason":"raison précise","risk":"LOW ou MEDIUM ou HIGH","sentiment":"bullish ou bearish ou neutral","key_signal":"signal principal"}}"""

    result = majority_vote(prompt)
    result["price"]    = price
    result["patterns"] = pattern_names
    result["confluence"] = confluence
    return result, price, patterns, mtf


def analyze_all_markets() -> list:
    news_text = "\n".join((get_news() + get_reddit() + get_google_trends())[:20])
    fear_greed = get_fear_greed()
    sp500      = get_sp500_trend()
    onchain    = get_onchain_signals()

    context = {"fear_greed": fear_greed, "sp500": sp500,
                "timestamp": datetime.now().isoformat()}
    memory["analysis_history"].append(context)
    memory["analysis_history"] = memory["analysis_history"][-100:]

    results = []
    for symbol in SYMBOLS:
        try:
            analysis, price, patterns, mtf = analyze_symbol(
                symbol, news_text, fear_greed, sp500, onchain
            )
            results.append((symbol, analysis, price, patterns, mtf))
        except Exception as e:
            print(f"[ANALYZE] Échec {symbol}: {e}")
    return results


# ═══════════════════════════════════════════════════════════════
#  WATCHDOG BOT
# ═══════════════════════════════════════════════════════════════
def bot_watchdog(send_fn_sync):
    time.sleep(120)
    alerted = False
    while True:
        time.sleep(60)
        if not bot_state["running"]:
            alerted = False
            continue
        last = bot_state.get("last_heartbeat")
        if not last:
            continue
        elapsed = (datetime.now() - last).total_seconds()
        if elapsed > WATCHDOG_TIMEOUT and not alerted:
            send_fn_sync(
                f"⚠️ ALERTE: Bot inactif depuis {int(elapsed//60)} min\n"
                f"Dernière activité: {last.strftime('%H:%M:%S')}"
            )
            alerted = True
        elif elapsed <= WATCHDOG_TIMEOUT:
            alerted = False


# ═══════════════════════════════════════════════════════════════
#  RÉSUMÉ JOURNALIER
# ═══════════════════════════════════════════════════════════════
def daily_summary_scheduler(send_fn_sync):
    while True:
        now      = datetime.now()
        midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=5, microsecond=0)
        time.sleep((midnight - now).total_seconds())
        try:
            prices    = get_all_prices()
            pv        = get_portfolio_value(prices)
            pnl       = pv - portfolio["initial"]
            stats     = get_stats()
            today     = datetime.now().strftime("%Y-%m-%d")
            today_t   = [t for t in portfolio["trades"]
                         if t.get("time_in", "").startswith(today)]
            today_pnl = sum(t["pnl"] for t in today_t if t.get("pnl") is not None)
            threshold = memory.get("confidence_threshold", CONFIDENCE_BASE)
            wr_last20 = db_get_win_rate_last_n(20)
            lessons   = "".join(
                f"  {'❌' if l['type']=='erreur' else '✅'} {l['lecon']}\n"
                for l in memory["lessons"][-3:]
            ) or "  Aucune"
            send_fn_sync(
                f"📊 RÉSUMÉ — {now.strftime('%d/%m/%Y')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Portfolio: ${pv:,.2f} ({(pnl/portfolio['initial']*100):+.1f}%)\n"
                f"PnL total: ${pnl:+.2f} | Aujourd'hui: ${today_pnl:+.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Win Rate global: {stats['win_rate']}% | 20 derniers: {wr_last20}%\n"
                f"Seuil confiance (auto): {threshold}%\n"
                f"Trades: {stats['total']} | Leçons: {len(memory['lessons'])}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Dernières leçons:\n{lessons}"
            )
        except Exception as e:
            print(f"[DAILY] {e}")


# ═══════════════════════════════════════════════════════════════
#  BOUCLE PRINCIPALE — avec narration live Telegram
# ═══════════════════════════════════════════════════════════════
def bot_loop(send_fn_sync):
    cycle = 0
    while bot_state["running"]:
        try:
            cycle += 1
            now = datetime.now().strftime("%H:%M:%S")
            print(f"[{now}] Cycle #{cycle}")

            # ── ÉTAPE 1 : Annonce du début d'analyse ────────────
            send_fn_sync(
                f"🔍 ANALYSE #{cycle} — {now}\n"
                f"Collecte des données marché pour {len(SYMBOLS)} cryptos...\n"
                f"(News + Reddit + OrderBook + Indicateurs multi-TF)"
            )

            # ── ÉTAPE 2 : Collecte des données communes ──────────
            news_text  = "\n".join((get_news() + get_reddit() + get_google_trends())[:20])
            fear_greed = get_fear_greed()
            sp500      = get_sp500_trend()
            onchain    = get_onchain_signals()

            context = {"fear_greed": fear_greed, "sp500": sp500,
                       "timestamp": datetime.now().isoformat()}
            memory["analysis_history"].append(context)
            memory["analysis_history"] = memory["analysis_history"][-100:]

            # ── ÉTAPE 3 : Analyse par symbole avec narration ─────
            all_results = []
            for symbol in SYMBOLS:
                coin = symbol.replace("USDT", "")
                try:
                    send_fn_sync(
                        f"📡 Analyse {coin} en cours...\n"
                        f"  RSI / MACD / Bollinger (15m, 1h, 4h)\n"
                        f"  Détection patterns + vote 3 modèles IA"
                    )
                    analysis, price, patterns, mtf = analyze_symbol(
                        symbol, news_text, fear_greed, sp500, onchain
                    )
                    all_results.append((symbol, analysis, price, patterns, mtf))

                    # Résultat de l'analyse pour ce coin
                    signal     = analysis.get("signal", "HOLD")
                    confidence = analysis.get("confidence", 0)
                    risk       = analysis.get("risk", "HIGH")
                    votes      = analysis.get("votes", [])
                    consensus  = analysis.get("consensus", "?")
                    confluence = analysis.get("confluence", {})
                    pat_names  = analysis.get("patterns", [])
                    key_signal = analysis.get("key_signal", "")
                    reason     = analysis.get("reason", "")
                    sentiment  = analysis.get("sentiment", "neutral")

                    sig_e   = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(signal, "⚪")
                    sent_e  = {"bullish": "📈", "bearish": "📉"}.get(sentiment, "➡️")
                    in_pos  = symbol in portfolio["positions"]
                    pos_tag = " 📍 EN POSITION" if in_pos else ""

                    send_fn_sync(
                        f"{sig_e} RÉSULTAT {coin}{pos_tag}\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"💵 Prix           : ${price:,.2f}\n"
                        f"📊 Signal         : {signal} — {confidence}% [{consensus}]\n"
                        f"🗳 Votes IA       : {' / '.join(votes)}\n"
                        f"🔀 Confluence TF  : {confluence.get('score',0)}/3 → {confluence.get('direction','?')}\n"
                        f"{sent_e} Sentiment      : {sentiment}\n"
                        f"⚠️  Risque         : {risk}\n"
                        f"📊 Patterns       : {', '.join(pat_names[:3]) or 'Aucun'}\n"
                        f"🔑 Signal clé     : {key_signal[:80]}\n"
                        f"💬 Raison         : {reason[:100]}"
                    )
                except Exception as e:
                    print(f"[LOOP] Erreur {symbol}: {e}")
                    send_fn_sync(f"⚠️ Erreur analyse {coin}: {e}")

            # ── ÉTAPE 4 : Décisions de trading ───────────────────
            threshold = memory.get("confidence_threshold", CONFIDENCE_BASE)
            prices    = {sym: price for sym, _, price, _, _ in all_results}
            traded    = False

            for symbol, analysis, price, patterns, mtf in all_results:
                signal     = analysis.get("signal", "HOLD")
                confidence = analysis.get("confidence", 0)
                risk       = analysis.get("risk", "HIGH")
                reason     = analysis.get("reason", "")
                coin       = symbol.replace("USDT", "")
                in_pos     = symbol in portfolio["positions"]

                has_alert = any("Pump" in p.get("name","") or "Dump" in p.get("name","")
                                for p in patterns)
                if has_alert:
                    send_fn_sync(
                        f"🚨 TRADE BLOQUÉ — {coin}\n"
                        f"Manipulation de marché détectée.\n"
                        f"{[p['desc'] for p in patterns if 'Pump' in p.get('name','') or 'Dump' in p.get('name','')]}"
                    )
                    continue

                if signal == "BUY" and confidence >= threshold and risk in ("LOW","MEDIUM"):
                    if not in_pos:
                        send_fn_sync(
                            f"⚡ SIGNAL D'ACHAT VALIDÉ — {coin}\n"
                            f"Confiance: {confidence}% (seuil: {threshold}%)\n"
                            f"Calcul de la taille de position (Kelly)..."
                        )
                        size_pct = compute_position_size(symbol, confidence, mtf)
                        execute_buy(symbol, price, reason, confidence,
                                    size_pct, patterns, send_fn=send_fn_sync)
                        traded = True

                    else:
                        send_fn_sync(
                            f"💡 Signal BUY {coin} ignoré\n"
                            f"Raison: déjà en position sur ce symbole."
                        )

                elif signal == "SELL" and confidence >= threshold - 5:
                    if in_pos:
                        send_fn_sync(
                            f"⚡ SIGNAL DE VENTE VALIDÉ — {coin}\n"
                            f"Confiance: {confidence}% | Raison: {reason[:80]}"
                        )
                        execute_sell(symbol, price, reason, confidence,
                                     send_fn=send_fn_sync)
                        traded = True

                elif signal == "HOLD":
                    pos_info = ""
                    if in_pos:
                        pos = portfolio["positions"][symbol]
                        chg = (price - pos["price_in"]) / pos["price_in"] * 100
                        pos_info = f"\n  Position actuelle: {chg:+.2f}% | SL: ${pos['price_in']*(1-STOP_LOSS_PCT):,.0f} | TP: ${pos['price_in']*(1+TAKE_PROFIT_PCT):,.0f}"
                    send_fn_sync(
                        f"⚪ HOLD — {coin} — rien à faire{pos_info}"
                    )

                elif signal in ("BUY","SELL") and confidence < threshold:
                    send_fn_sync(
                        f"📉 Signal {signal} {coin} ignoré\n"
                        f"Confiance {confidence}% < seuil {threshold}%\n"
                        f"Le bot attend un signal plus fort."
                    )

            # ── ÉTAPE 5 : Résumé du cycle ─────────────────────────
            pv    = get_portfolio_value(prices)
            stats = get_stats()
            wr_db = db_get_win_rate_last_n(20)
            send_fn_sync(
                f"📋 FIN DU CYCLE #{cycle}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Portfolio      : ${pv:,.2f} ({((pv/portfolio['initial'])-1)*100:+.1f}%)\n"
                f"💵 Cash disponible: ${portfolio['cash']:,.2f}\n"
                f"📍 Positions      : {len(portfolio['positions'])}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🏆 Win Rate       : {stats['win_rate']}% (global) | {wr_db}% (20 derniers)\n"
                f"📊 Trades total   : {stats['total']}\n"
                f"📚 Leçons         : {len(memory['lessons'])}\n"
                f"⚙️  Seuil auto     : {threshold}%\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"⏰ Prochain cycle  : dans 10 min"
            )

            bot_state["last_heartbeat"] = datetime.now()

            # Backtest automatique toutes les 10 cycles
            if cycle % 10 == 0:
                send_fn_sync("🔬 Lancement du backtest automatique (30 jours)...")
                threading.Thread(
                    target=lambda: [run_backtest(s) for s in SYMBOLS],
                    daemon=True
                ).start()

        except Exception as e:
            print(f"[LOOP] {e}")
            send_fn_sync(f"⚠️ Erreur cycle #{cycle}: {e}")

        time.sleep(600)
# ═══════════════════════════════════════════════════════════════
#  DASHBOARD HTML
# ═══════════════════════════════════════════════════════════════
def generate_dashboard() -> str:
    stats = get_stats()
    try:
        prices = get_all_prices()
    except Exception:
        prices = {}
    pv      = get_portfolio_value(prices)
    pnl     = pv - portfolio["initial"]
    pnl_pct = pnl / portfolio["initial"] * 100
    status  = "🟢 EN MARCHE" if bot_state["running"] else "🔴 ARRÊTÉ"
    last    = bot_state.get("last_heartbeat")
    hb_str  = last.strftime("%H:%M:%S") if last else "—"
    threshold = memory.get("confidence_threshold", CONFIDENCE_BASE)
    wr_db   = db_get_win_rate_last_n(20)

    pos_html = ""
    for sym, pos in portfolio["positions"].items():
        price = prices.get(sym, pos["price_in"])
        upnl  = (price - pos["price_in"]) / pos["price_in"] * 100
        color = "#2ecc71" if upnl >= 0 else "#e74c3c"
        pos_html += (
            f"<tr><td>{sym}</td><td>${pos['price_in']:,.2f}</td>"
            f"<td>${price:,.2f}</td>"
            f'<td style="color:{color}">{upnl:+.2f}%</td>'
            f"<td>${pos['qty']*price:,.2f}</td>"
            f"<td>${pos['price_in']*(1-STOP_LOSS_PCT):,.2f}</td>"
            f"<td>${pos['price_in']*(1+TAKE_PROFIT_PCT):,.2f}</td></tr>"
        )

    trades_html = ""
    for t in reversed(portfolio["trades"][-20:]):
        pnl_str = (f'<span style="color:{"#2ecc71" if t["pnl"]>0 else "#e74c3c"}">'
                   f'${t["pnl"]:+.2f}</span>'
                   if t.get("pnl") is not None
                   else '<span style="color:#f39c12">En cours</span>')
        po  = f"${t['price_out']:,.2f}" if t.get("price_out") else "-"
        pat = ", ".join(t.get("patterns_detected", []))[:30] or "-"
        trades_html += (
            f"<tr><td>{t['id']}</td><td>{t.get('symbol','')}</td>"
            f"<td>{t['type']}</td><td>${t['price_in']:,.2f}</td>"
            f"<td>{po}</td><td>{pnl_str}</td>"
            f"<td>{t['confidence']}%</td><td>{pat}</td>"
            f"<td>{t['time_in']}</td></tr>"
        )

    lessons_html = ""
    for l in reversed(memory["lessons"][-10:]):
        color = "#e74c3c" if l["type"] == "erreur" else "#2ecc71"
        e     = "❌" if l["type"] == "erreur" else "✅"
        lessons_html += (
            f'<tr><td style="color:{color}">{e}</td>'
            f"<td>{l.get('symbol','')}</td>"
            f'<td style="color:{color}">${l.get("pnl",0):+.2f}</td>'
            f"<td>{l['lecon'][:55]}</td>"
            f"<td>{l['action_future'][:55]}</td>"
            f"<td>{l['date']}</td></tr>"
        )

    return f"""<!DOCTYPE html><html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Bot v2</title>
<style>
body{{font-family:Arial,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:16px}}
h1{{color:#58a6ff;text-align:center;font-size:1.4em}}
h2{{color:#58a6ff;font-size:.95em;margin:16px 0 6px}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px}}
.card{{background:#161b22;border-radius:10px;padding:12px;text-align:center}}
.label{{font-size:.72em;color:#8b949e;margin-bottom:4px}}
.value{{font-size:1.2em;font-weight:bold}}
.green{{color:#2ecc71}}.red{{color:#e74c3c}}.blue{{color:#58a6ff}}.yellow{{color:#f39c12}}
.status,.sub{{text-align:center;font-size:.85em;color:#8b949e;margin-bottom:4px}}
table{{width:100%;border-collapse:collapse;font-size:.73em;margin-bottom:20px}}
th{{background:#21262d;padding:6px;text-align:left;color:#8b949e}}
td{{padding:5px 6px;border-bottom:1px solid #21262d}}
.badge{{background:#21262d;border-radius:6px;padding:2px 6px;font-size:.75em;margin:2px}}
</style>
<meta http-equiv="refresh" content="60">
</head><body>
<h1>🤖 Trading Bot v2 — Multi-IA</h1>
<div class="status">{status} | Dernière analyse: {hb_str}</div>
<div class="sub">
  Seuil auto: {threshold}% | WR(20): {wr_db}% |
  SL: {STOP_LOSS_PCT*100:.0f}% | TP: {TAKE_PROFIT_PCT*100:.0f}% |
  Modèles: {len(AI_MODELS)} (vote majoritaire)
</div>
<div class="grid">
  <div class="card"><div class="label">Portefeuille</div>
    <div class="value blue">${pv:,.2f}</div></div>
  <div class="card"><div class="label">PnL Total</div>
    <div class="value {'green' if pnl>=0 else 'red'}">${pnl:+.2f} ({pnl_pct:+.1f}%)</div></div>
  <div class="card"><div class="label">Cash</div>
    <div class="value">${portfolio['cash']:,.2f}</div></div>
  <div class="card"><div class="label">Positions</div>
    <div class="value yellow">{len(portfolio['positions'])}</div></div>
  <div class="card"><div class="label">Win Rate</div>
    <div class="value yellow">{stats['win_rate']}%</div></div>
  <div class="card"><div class="label">Trades | Leçons DB</div>
    <div class="value">{stats['total']} | {len(memory['lessons'])}</div></div>
</div>
<h2>Positions Ouvertes</h2>
<table><thead><tr>
  <th>Symbol</th><th>Entrée</th><th>Actuel</th>
  <th>PnL%</th><th>Valeur</th><th>Stop-Loss</th><th>Take-Profit</th>
</tr></thead><tbody>
{pos_html or '<tr><td colspan="7" style="text-align:center;color:#8b949e">Aucune</td></tr>'}
</tbody></table>
<h2>Historique Trades</h2>
<table><thead><tr>
  <th>#</th><th>Symbol</th><th>Type</th><th>Entrée</th><th>Sortie</th>
  <th>PnL</th><th>Conf.</th><th>Patterns</th><th>Heure</th>
</tr></thead><tbody>
{trades_html or '<tr><td colspan="9" style="text-align:center;color:#8b949e">Aucun</td></tr>'}
</tbody></table>
<h2>Mémoire & Leçons</h2>
<table><thead><tr>
  <th>Type</th><th>Symbol</th><th>PnL</th>
  <th>Leçon</th><th>Action Future</th><th>Date</th>
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
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(generate_dashboard().encode("utf-8"))

    def do_POST(self):
        if self.path != WEBHOOK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        if _app and _main_loop:
            asyncio.run_coroutine_threadsafe(
                _process_update(body), _main_loop
            )
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


async def _process_update(body: bytes):
    try:
        data   = json.loads(body)
        update = Update.de_json(data, _app.bot)
        await _app.process_update(update)
    except Exception as e:
        print(f"[WEBHOOK] {e}")


def run_server():
    HTTPServer(("0.0.0.0", WEBHOOK_PORT), BotHandler).serve_forever()


# ═══════════════════════════════════════════════════════════════
#  HELPER TELEGRAM
# ═══════════════════════════════════════════════════════════════
def make_send_fn_sync(chat_id: str):
    def send_fn_sync(msg: str):
        if _app is None or _main_loop is None:
            print(f"[MSG] {msg}")
            return
        future = asyncio.run_coroutine_threadsafe(
            _app.bot.send_message(chat_id=chat_id, text=msg), _main_loop
        )
        try:
            future.result(timeout=15)
        except Exception as e:
            print(f"[MSG] {e}")
    return send_fn_sync


def _authorized(update: Update) -> bool:
    return str(update.effective_chat.id) == TELEGRAM_CHAT_ID


# ═══════════════════════════════════════════════════════════════
#  COMMANDES TELEGRAM
# ═══════════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if bot_state["running"]:
        await update.message.reply_text("Le bot tourne déjà !")
        return
    bot_state["running"]        = True
    bot_state["last_heartbeat"] = None
    send_fn_sync = make_send_fn_sync(TELEGRAM_CHAT_ID)
    threading.Thread(target=bot_loop,          args=(send_fn_sync,), daemon=True).start()
    threading.Thread(target=stoploss_watchdog, args=(send_fn_sync,), daemon=True).start()
    threshold = memory.get("confidence_threshold", CONFIDENCE_BASE)
    await update.message.reply_text(
        f"✅ Bot v2 démarré !\n"
        f"Symboles: {', '.join(SYMBOLS)}\n"
        f"Seuil confiance (auto): {threshold}%\n"
        f"SL: {STOP_LOSS_PCT*100:.0f}% | TP: {TAKE_PROFIT_PCT*100:.0f}%\n"
        f"Vote majoritaire: {len(AI_MODELS)} modèles\n"
        f"Analyse toutes les 10 min..."
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    bot_state["running"] = False
    await update.message.reply_text("🛑 Bot arrêté.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    last    = bot_state.get("last_heartbeat")
    hb_str  = last.strftime("%H:%M:%S") if last else "—"
    status  = "🟢 EN MARCHE" if bot_state["running"] else "🔴 ARRÊTÉ"
    threshold = memory.get("confidence_threshold", CONFIDENCE_BASE)
    wr_db   = db_get_win_rate_last_n(20)
    try:
        prices = get_all_prices()
    except Exception:
        prices = {}
    pv    = get_portfolio_value(prices)
    pnl   = pv - portfolio["initial"]
    stats = get_stats()
    await update.message.reply_text(
        f"Statut: {status}\n"
        f"Dernière analyse: {hb_str}\n"
        f"━━━━━━━━━━━━━\n"
        f"Portfolio: ${pv:,.2f} ({pnl:+.2f})\n"
        f"Positions: {len(portfolio['positions'])}\n"
        f"━━━━━━━━━━━━━\n"
        f"Trades: {stats['total']} | Win Rate: {stats['win_rate']}%\n"
        f"Win Rate 20 derniers: {wr_db}%\n"
        f"Seuil auto: {threshold}%\n"
        f"Leçons: {len(memory['lessons'])}"
    )


async def cmd_analyse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text("🔍 Analyse multi-IA en cours (3 modèles)...")
    try:
        results = analyze_all_markets()
        lines   = ["📊 Analyse instantanée\n━━━━━━━━━━━━━"]
        for symbol, analysis, price, patterns, mtf in results:
            votes     = analysis.get("votes", [])
            consensus = analysis.get("consensus", "?")
            conf      = analysis.get("confluence", {})
            sig_e     = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(
                            analysis.get("signal"), "⚪")
            lines.append(
                f"{sig_e} {symbol}: ${price:,.2f}\n"
                f"  Signal: {analysis.get('signal')} {analysis.get('confidence')}% "
                f"[{consensus}] | {analysis.get('risk')}\n"
                f"  Votes: {'/'.join(votes)}\n"
                f"  Confluence: {conf.get('score',0)}/3 → {conf.get('direction','?')}\n"
                f"  Patterns: {', '.join(analysis.get('patterns',[])[:3]) or 'Aucun'}"
            )
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Erreur: {e}")


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    try:
        prices = get_all_prices()
    except Exception:
        prices = {}
    pv    = get_portfolio_value(prices)
    pnl   = pv - portfolio["initial"]
    stats = get_stats()
    pos_lines = "".join(
        f"  {sym}: ${prices.get(sym, pos['price_in']):,.2f} "
        f"({((prices.get(sym, pos['price_in'])-pos['price_in'])/pos['price_in']*100):+.1f}%)\n"
        for sym, pos in portfolio["positions"].items()
    ) or "  Aucune\n"
    await update.message.reply_text(
        f"💼 Portefeuille\n"
        f"Capital: $1,000 → ${pv:,.2f} ({pnl:+.2f})\n"
        f"Cash: ${portfolio['cash']:,.2f}\n"
        f"━━━━━━━━━━━━━\n"
        f"Positions:\n{pos_lines}"
        f"━━━━━━━━━━━━━\n"
        f"Trades: {stats['total']} ({stats['wins']}W/{stats['losses']}L)\n"
        f"Win Rate: {stats['win_rate']}% | Best: +${stats['best']}"
    )


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if not portfolio["positions"]:
        await update.message.reply_text("Aucune position ouverte.")
        return
    try:
        prices = get_all_prices()
    except Exception:
        prices = {}
    lines = ["📍 Positions ouvertes\n━━━━━━━━━━━━━"]
    for sym, pos in portfolio["positions"].items():
        price = prices.get(sym, pos["price_in"])
        upnl  = (price - pos["price_in"]) / pos["price_in"] * 100
        e     = "✅" if upnl >= 0 else "🔻"
        lines.append(
            f"{e} {sym}: {upnl:+.2f}%\n"
            f"  ${pos['price_in']:,.2f} → ${price:,.2f}\n"
            f"  🛑 ${pos['price_in']*(1-STOP_LOSS_PCT):,.2f} | "
            f"🎯 ${pos['price_in']*(1+TAKE_PROFIT_PCT):,.2f}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_lecons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if not memory["lessons"]:
        await update.message.reply_text("Aucune leçon apprise encore.")
        return
    msg = f"📚 Leçons ({len(memory['lessons'])}):\n\n"
    for l in memory["lessons"][-5:]:
        e    = "❌" if l["type"] == "erreur" else "✅"
        msg += f"{e} [{l.get('symbol','')}] ${l['pnl']:+.2f}\n"
        msg += f"{l['lecon']}\n→ {l['action_future']}\n\n"
    await update.message.reply_text(msg)


async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text("🔬 Backtest en cours sur 30 jours...")
    lines = ["📈 Résultats Backtest (30j)\n━━━━━━━━━━━━━"]
    for symbol in SYMBOLS:
        r = run_backtest(symbol)
        if "error" in r:
            lines.append(f"❌ {symbol}: erreur")
        else:
            e = "✅" if r["total_pnl"] > 0 else "❌"
            lines.append(
                f"{e} {symbol}: {r['win_rate']}% WR | "
                f"{r['total_trades']} trades | PnL ${r['total_pnl']:+.2f}"
            )
    await update.message.reply_text("\n".join(lines))


async def cmd_seuil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    threshold = memory.get("confidence_threshold", CONFIDENCE_BASE)
    wr_db     = db_get_win_rate_last_n(20)
    await update.message.reply_text(
        f"⚙️ Seuil de confiance\n"
        f"Actuel (auto): {threshold}%\n"
        f"Win Rate 20 derniers: {wr_db}%\n"
        f"Min: {CONFIDENCE_MIN}% | Max: {CONFIDENCE_MAX}%\n"
        f"Le seuil s'ajuste automatiquement après chaque trade."
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    bot_state.update({"running": False, "last_heartbeat": None})
    portfolio.update({"cash": 1000.0, "positions": {}, "trades": []})
    memory.update({
        "lessons": [], "patterns_to_avoid": [],
        "patterns_that_work": [], "analysis_history": [],
        "confidence_threshold": CONFIDENCE_BASE,
    })
    save_data()
    await update.message.reply_text(
        "🔄 Portfolio réinitialisé à $1000. Mémoire RAM effacée.\n"
        "(La base SQLite est conservée pour l'historique long terme.)"
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
        ("start",    cmd_start),
        ("stop",     cmd_stop),
        ("status",   cmd_status),
        ("analyse",  cmd_analyse),
        ("portfolio",cmd_portfolio),
        ("positions",cmd_positions),
        ("lecons",   cmd_lecons),
        ("backtest", cmd_backtest),
        ("seuil",    cmd_seuil),
        ("reset",    cmd_reset),
    ]:
        _app.add_handler(CommandHandler(cmd, fn))

    await _app.initialize()
    await _app.start()

    if WEBHOOK_URL:
        full_url = WEBHOOK_URL.rstrip("/") + WEBHOOK_PATH
        await _app.bot.set_webhook(
            url=full_url, drop_pending_updates=True,
            allowed_updates=["message"],
        )
        print(f"Webhook enregistré: {full_url}")
    else:
        print("⚠️  WEBHOOK_URL non définie.")

    print("Bot Telegram v2 prêt (webhook + vote majoritaire)...")

    send_fn_sync = make_send_fn_sync(TELEGRAM_CHAT_ID)
    threading.Thread(target=bot_watchdog,            args=(send_fn_sync,), daemon=True).start()
    threading.Thread(target=daily_summary_scheduler, args=(send_fn_sync,), daemon=True).start()

    try:
        while True:
            await asyncio.sleep(1)
    finally:
        if WEBHOOK_URL:
            await _app.bot.delete_webhook()
        await _app.stop()
        await _app.shutdown()


# ═══════════════════════════════════════════════════════════════
#  SELF-PING (anti-sleep plan Free Koyeb)
# ═══════════════════════════════════════════════════════════════
def self_ping():
    time.sleep(60)
    while True:
        try:
            requests.get(
                "https://junior-tick-1ever-6bf9cee7.koyeb.app/health",
                timeout=10,
            )
        except Exception:
            pass
        time.sleep(270)


# ═══════════════════════════════════════════════════════════════
#  ENTRYPOINT
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("Démarrage Trading Bot v2...")
    init_db()
    load_data()
    threading.Thread(target=run_server, daemon=True).start()
    threading.Thread(target=self_ping,  daemon=True).start()
    print("Serveur HTTP démarré sur port 8000")
    asyncio.run(run_telegram())
