"""
Trading Bot v5 — Simulation Pure sur Marché Réel
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Nouveautés v5 :
  • Kelly Criterion avancé (taille optimale des trades)
  • Liquidations en temps réel (CoinGlass)
  • Données on-chain (CoinGecko)
  • Détection d'arbitrage inter-exchanges
  • Whale alerts (gros mouvements détectés)
  • Scanner Polymarket (API publique, sans compte)
"""

import os, time, threading, feedparser, requests, asyncio
import json, sqlite3, re, hashlib
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

CYCLE_MONITOR = 15
CYCLE_SCALP   = 300
CYCLE_DEEP    = 300
CYCLE_STATUS  = 900
CYCLE_MICRO   = 8
CYCLE_MEME    = 45

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
LEARNING_MAX_PCT    = 0.05

AI_CALLS_PER_HOUR_MAX = 10
AI_CYCLE_MIN_INTERVAL = 360

GROQ_PRIMARY_MODEL   = "llama-3.1-8b-instant"
GROQ_SECONDARY_MODEL = "llama-3.1-8b-instant"
GROQ_FAST_MODEL      = "llama-3.1-8b-instant"

DB_FILE   = "sim_v5.db"
DATA_FILE = Path("sim_portfolio_v5.json")

CRYPTO_SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "DOGEUSDT","ADAUSDT","AVAXUSDT","MATICUSDT","LINKUSDT",
    "DOTUSDT","UNIUSDT","ATOMUSDT","LTCUSDT","NEARUSDT",
    "APTUSDT","ARBUSDT","OPUSDT","INJUSDT","SUIUSDT",
    "FETUSDT","RENDERUSDT","WLDUSDT","STRKUSDT","PYTHUSDT",
    "JUPUSDT","TIAUSDT","SEIUSDT","ENAUSDT","EIGENUSDT",
]

MICRO_SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "DOGEUSDT","AVAXUSDT","LINKUSDT","ARBUSDT","APTUSDT",
    "FETUSDT","INJUSDT","NEARUSDT","SUIUSDT","OPUSDT",
]

MEMECOIN_SYMBOLS = [
    "BONKUSDT","WIFUSDT","POPCATUSDT","SHIBUSDT",
    "FLOKIUSDT","PEPEUSDT","DOGEUSDT",
]

STOCKS_SYMBOLS = {
    "AAPL":"Apple","TSLA":"Tesla","NVDA":"NVIDIA",
    "META":"Meta","MSFT":"Microsoft","GOOGL":"Google",
    "AMZN":"Amazon","AMD":"AMD","COIN":"Coinbase","MSTR":"MicroStrategy",
}

FOREX_SYMBOLS = {
    "EURUSD=X":"EUR/USD","GBPUSD=X":"GBP/USD",
    "USDJPY=X":"USD/JPY","AUDUSD=X":"AUD/USD",
}

COMMODITY_SYMBOLS = {
    "GC=F":"Or","SI=F":"Argent","CL=F":"Pétrole","HG=F":"Cuivre",
}

ALL_SYMBOLS = CRYPTO_SYMBOLS

NITTER_INSTANCES = [
    "nitter.privacydev.net",
    "nitter.poast.org",
    "nitter.1d4.us",
]

TRADER_TWITTER_ACCOUNTS = [
    "michael_saylor","CathieDWood","APompliano",
    "WClementeIII","CryptoKaleo","RaoulGMI",
    "AlxCooks_off","MustStopMurad","blknoiz06",
    "Degentraland","CryptoGodJohn","solbigbrain",
]

YOUTUBE_CHANNELS = {
    "Benjamin Cowen":  "UCRvqjQPSeaWn-uEx-w0XOIg",
    "Coin Bureau":     "UCqK_GSMbpiV8spgD3ZGloSw",
    "InvestAnswers":   "UCnMn36GT_H0X-w5_ckLtlgQ",
}

TRADER_PHILOSOPHIES = """
RÈGLES DES MEILLEURS TRADERS :
1. SAYLOR : Fear&Greed<20 = opportunité rare. Ne jamais vendre en panique.
2. CATHIE WOOD : Favoriser tokens innovants (AI, DeFi, L2). RSI bas = entrée.
3. PAUL TUDOR JONES : JAMAIS perdre >2% du capital par trade.
4. LIVERMORE : Trader dans le sens de la tendance. Volume confirme.
5. BUFFETT : N'entrer que sur signaux haute conviction (conf>75%).
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
    "cash": CAPITAL_INITIAL, "initial": CAPITAL_INITIAL,
    "positions": {}, "trades": [], "equity_history": [],
    "session": 1,
}

memory = {
    "lessons": [], "patterns_to_avoid": [],
    "patterns_that_work": [],
    "confidence_threshold": CONFIDENCE_BASE,
    "total_wins": 0, "total_losses": 0,
}

bot_state = {
    "running": False, "last_heartbeat": None,
    "cycle_count": 0, "trades_today": 0,
    "last_monitor": 0, "last_scalp": 0,
    "last_deep": 0, "last_status": 0,
    "last_micro": 0, "last_meme": 0,
    "nitter_idx": 0, "yt_idx": 0,
    "micro_count": 0,
}

_main_loop = None
_app       = None
_signal_cache: set = set()
_price_cache: dict = {}
_yahoo_cache: dict = {}
_dex_cache:   dict = {}
_mentioned_tokens: dict = {}

# ═══════════════════════════════════════════════════════════════
#  KELLY CRITERION AVANCÉ
# ═══════════════════════════════════════════════════════════════
def kelly_criterion(n_recent: int = 30) -> float:
    """
    Kelly Criterion complet basé sur l'historique réel.
    f* = (p * b - q) / b
    p = probabilité de gain, q = 1-p, b = ratio gain/perte moyen
    Retourne la fraction Kelly divisée par 4 (quart-Kelly = plus sûr)
    """
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    if len(closed) < 5:
        return 0.10  # défaut conservateur si pas assez d'historique

    recent = closed[-n_recent:]
    wins   = [t for t in recent if t["pnl"] > 0]
    losses = [t for t in recent if t["pnl"] <= 0]

    if not wins or not losses:
        return 0.08

    p = len(wins) / len(recent)
    q = 1 - p

    avg_win  = sum(t["pnl_pct"] for t in wins) / len(wins) / 100
    avg_loss = abs(sum(t["pnl_pct"] for t in losses) / len(losses)) / 100

    if avg_loss == 0:
        return MAX_PCT_PER_TRADE

    b = avg_win / avg_loss  # ratio gain/perte

    kelly = (p * b - q) / b

    # Quart-Kelly pour limiter le risque (plus conservateur)
    quarter_kelly = kelly / 4

    return round(max(0.03, min(MAX_PCT_PER_TRADE, quarter_kelly)), 3)


def dynamic_position_size(confidence: int, market: str, symbol: str) -> float:
    """
    Taille de position dynamique combinant Kelly + confiance IA + contexte marché.
    """
    base_kelly = kelly_criterion(30)

    # Ajustement selon la confiance IA
    conf_multiplier = 0.5 + (confidence - 55) / 90

    # Ajustement selon le marché
    market_multiplier = 0.6 if market == "FUTURES" else 1.0
    if market == "MEME":
        market_multiplier = 0.4

    # Ajustement selon la volatilité du symbole
    vol_multiplier = 1.0
    try:
        closes = get_klines(symbol, "5", 30)
        if not closes.empty:
            vol = float(closes.pct_change().dropna().std() * 100)
            if vol > 3:
                vol_multiplier = 0.7
            elif vol < 1:
                vol_multiplier = 1.2
    except Exception:
        pass

    size = base_kelly * conf_multiplier * market_multiplier * vol_multiplier
    return round(max(0.03, min(MAX_PCT_PER_TRADE, size)), 3)


# ═══════════════════════════════════════════════════════════════
#  NOUVELLES SOURCES DE DONNÉES
# ═══════════════════════════════════════════════════════════════

# ── CoinGlass : Liquidations en temps réel ──────────────────
_liquidations_cache = {"data": None, "ts": 0}

def get_liquidations() -> dict:
    """
    Récupère les liquidations récentes via CoinGlass API publique.
    Grosse liquidation SHORT = signal haussier (shorts squeezés).
    Grosse liquidation LONG  = signal baissier (longs liquidés).
    """
    now = time.time()
    if now - _liquidations_cache["ts"] < 60:
        return _liquidations_cache["data"] or {}

    try:
        # API publique CoinGlass (pas de clé requise pour les données basiques)
        url = "https://open-api.coinglass.com/public/v2/liquidation_history"
        r   = requests.get(url, timeout=8, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json"
        })
        if r.status_code == 200:
            data = r.json().get("data", {})
            _liquidations_cache["data"] = data
            _liquidations_cache["ts"]   = now
            return data
    except Exception:
        pass

    # Fallback : estimation via Bybit funding rates
    try:
        result = {}
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            r = bybit.get_funding_rate_history(
                category="linear", symbol=sym, limit=1)
            items = r.get("result", {}).get("list", [])
            if items:
                rate = float(items[0].get("fundingRate", 0))
                result[sym] = {
                    "funding_rate": rate,
                    "signal": "bearish" if rate > 0.001 else "bullish" if rate < -0.001 else "neutral"
                }
        _liquidations_cache["data"] = result
        _liquidations_cache["ts"]   = now
        return result
    except Exception as e:
        print(f"[LIQ] {e}")
        return {}


def interpret_liquidations(liq_data: dict) -> str:
    """Traduit les données de liquidation en signal texte pour le prompt IA."""
    if not liq_data:
        return "Liquidations: données indisponibles"

    lines = ["💥 Liquidations récentes:"]
    for sym, data in list(liq_data.items())[:3]:
        if isinstance(data, dict):
            rate    = data.get("funding_rate", 0)
            signal  = data.get("signal", "neutral")
            emoji   = "🟢" if signal == "bullish" else "🔴" if signal == "bearish" else "⚪"
            coin    = sym.replace("USDT","")
            lines.append(f"  {emoji} {coin}: funding={rate*100:.4f}% ({signal})")

    return "\n".join(lines)


# ── CoinGecko : Données on-chain ────────────────────────────
_coingecko_cache = {"data": None, "ts": 0}

def get_onchain_data() -> dict:
    """
    Données on-chain via CoinGecko API gratuite.
    - Dominance BTC
    - Volume global 24h
    - Market cap total
    - Tendance DeFi/NFT
    """
    now = time.time()
    if now - _coingecko_cache["ts"] < 300:  # cache 5min
        return _coingecko_cache["data"] or {}

    try:
        # Global market data
        r = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200:
            d = r.json().get("data", {})
            result = {
                "btc_dominance":    round(d.get("market_cap_percentage", {}).get("btc", 0), 1),
                "eth_dominance":    round(d.get("market_cap_percentage", {}).get("eth", 0), 1),
                "total_mcap":       d.get("total_market_cap", {}).get("usd", 0),
                "total_volume_24h": d.get("total_volume", {}).get("usd", 0),
                "mcap_change_24h":  round(d.get("market_cap_change_percentage_24h_usd", 0), 2),
                "active_cryptos":   d.get("active_cryptocurrencies", 0),
            }
            _coingecko_cache["data"] = result
            _coingecko_cache["ts"]   = now
            return result
    except Exception as e:
        print(f"[GECKO] {e}")

    return {}


def format_onchain(data: dict) -> str:
    if not data:
        return "On-chain: données indisponibles"
    mcap_b = data.get("total_mcap", 0) / 1e9
    vol_b  = data.get("total_volume_24h", 0) / 1e9
    chg    = data.get("mcap_change_24h", 0)
    emoji  = "📈" if chg > 0 else "📉"
    return (
        f"🔗 On-chain: MCap=${mcap_b:.0f}B ({chg:+.1f}%) {emoji} "
        f"Vol=${vol_b:.0f}B | BTC dom={data.get('btc_dominance',0)}%"
    )


# ── Whale Alert : Gros mouvements ───────────────────────────
_whale_cache = {"alerts": [], "ts": 0}

def get_whale_alerts() -> list:
    """
    Détecte les gros mouvements de fonds via des sources publiques.
    Utilise Whale Alert RSS et CryptoQuant signaux publics.
    """
    now = time.time()
    if now - _whale_cache["ts"] < 120:
        return _whale_cache["alerts"]

    alerts = []
    try:
        # Whale Alert via leur flux RSS public
        feed = feedparser.parse("https://api.whale-alert.io/v1/transactions?api_key=&min_value=1000000")
        for entry in feed.entries[:5]:
            alerts.append({
                "title":   entry.get("title",""),
                "summary": entry.get("summary","")[:100],
                "ts":      datetime.now().strftime("%H:%M"),
            })
    except Exception:
        pass

    # Fallback : gros volumes détectés sur Bybit
    try:
        prices = get_prices_batch()
        for sym in ["BTCUSDT","ETHUSDT"]:
            vols = get_volume_data(sym, "5", 5)
            if len(vols) >= 2:
                ratio = vols[-1] / (sum(vols[:-1])/max(len(vols)-1,1))
                if ratio > 3:
                    coin = sym.replace("USDT","")
                    alerts.append({
                        "title":   f"Volume spike {coin}",
                        "summary": f"Volume x{ratio:.1f} la moyenne sur {coin}",
                        "ts":      datetime.now().strftime("%H:%M"),
                        "symbol":  sym,
                        "type":    "volume_spike",
                    })
    except Exception as e:
        print(f"[WHALE] {e}")

    _whale_cache["alerts"] = alerts
    _whale_cache["ts"]     = now
    return alerts


def format_whale_alerts(alerts: list) -> str:
    if not alerts:
        return "🐋 Whales: aucun mouvement détecté"
    lines = ["🐋 Mouvements whale:"]
    for a in alerts[:3]:
        lines.append(f"  ⚠️ {a.get('summary','')[:60]} ({a.get('ts','')})")
    return "\n".join(lines)


# ── Polymarket : Scanner les marchés de prédiction ──────────
_polymarket_cache = {"markets": [], "ts": 0}

def get_polymarket_markets() -> list:
    """
    Scanne les marchés Polymarket via leur API publique (pas de compte requis).
    Détecte les marchés avec des prix inefficaces vs probabilité réelle.
    Note : lecture seule, aucun trade réel sur Polymarket.
    """
    now = time.time()
    if now - _polymarket_cache["ts"] < 300:
        return _polymarket_cache["markets"]

    try:
        # API CLOB publique Polymarket
        r = requests.get(
            "https://clob.polymarket.com/markets",
            timeout=10,
            params={"active": "true", "limit": 20},
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200:
            markets = r.json().get("data", [])
            processed = []
            for m in markets[:20]:
                tokens = m.get("tokens", [])
                if len(tokens) < 2:
                    continue
                question = m.get("question","")
                yes_price = float(tokens[0].get("price", 0.5))
                no_price  = float(tokens[1].get("price", 0.5))

                # Détecte inefficacité : sum ≠ 1 ou écart vs probabilité évidente
                total = yes_price + no_price
                if abs(total - 1.0) > 0.02:  # inefficacité détectée
                    processed.append({
                        "question":   question[:80],
                        "yes_price":  yes_price,
                        "no_price":   no_price,
                        "total":      round(total, 3),
                        "inefficiency": round(abs(total - 1.0) * 100, 2),
                        "volume":     m.get("volume", 0),
                        "end_date":   m.get("end_date_iso",""),
                    })

            # Trie par inefficacité
            processed.sort(key=lambda x: x["inefficiency"], reverse=True)
            _polymarket_cache["markets"] = processed
            _polymarket_cache["ts"]      = now
            return processed[:5]
    except Exception as e:
        print(f"[POLY] {e}")

    return []


def format_polymarket(markets: list) -> str:
    if not markets:
        return "Polymarket: aucune inefficacité détectée"
    lines = ["🎯 Polymarket — Inefficacités:"]
    for m in markets[:3]:
        ineff = m["inefficiency"]
        lines.append(
            f"  ⚡ {m['question']}\n"
            f"    YES:{m['yes_price']:.2f} NO:{m['no_price']:.2f} "
            f"(ineff:{ineff:.1f}%)"
        )
    return "\n".join(lines)


# ── Arbitrage inter-exchanges ────────────────────────────────
_arb_cache = {"opportunities": [], "ts": 0}

def detect_arbitrage() -> list:
    """
    Détecte les opportunités d'arbitrage en comparant les prix
    entre Bybit et d'autres sources (Binance public API, Kraken).
    """
    now = time.time()
    if now - _arb_cache["ts"] < 30:
        return _arb_cache["opportunities"]

    opportunities = []
    bybit_prices  = get_prices_batch()

    # Compare avec Binance (API publique, pas de clé)
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200:
            binance_prices = {item["symbol"]: float(item["price"])
                              for item in r.json()}

            for sym in ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT"]:
                bybit_p   = bybit_prices.get(sym, 0)
                binance_p = binance_prices.get(sym, 0)

                if not bybit_p or not binance_p:
                    continue

                spread_pct = abs(bybit_p - binance_p) / bybit_p * 100

                if spread_pct > 0.15:  # spread > 0.15% = opportunité
                    opportunities.append({
                        "symbol":     sym,
                        "bybit":      bybit_p,
                        "binance":    binance_p,
                        "spread_pct": round(spread_pct, 3),
                        "direction":  "BUY_BYBIT" if bybit_p < binance_p else "BUY_BINANCE",
                        "profit_est": round(spread_pct - 0.10, 3),  # moins les frais
                    })

    except Exception as e:
        print(f"[ARB] Binance: {e}")

    # Compare avec Kraken (API publique)
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker?pair=XBTUSD,ETHUSD",
            timeout=8
        )
        if r.status_code == 200:
            kraken_data = r.json().get("result", {})
            kraken_btc  = float(kraken_data.get("XXBTZUSD", {}).get("c", [0])[0])
            bybit_btc   = bybit_prices.get("BTCUSDT", 0)

            if kraken_btc and bybit_btc:
                spread = abs(bybit_btc - kraken_btc) / bybit_btc * 100
                if spread > 0.2:
                    opportunities.append({
                        "symbol":     "BTCUSDT",
                        "bybit":      bybit_btc,
                        "kraken":     kraken_btc,
                        "spread_pct": round(spread, 3),
                        "direction":  "BUY_BYBIT" if bybit_btc < kraken_btc else "BUY_KRAKEN",
                        "profit_est": round(spread - 0.15, 3),
                    })
    except Exception as e:
        print(f"[ARB] Kraken: {e}")

    opportunities.sort(key=lambda x: x["spread_pct"], reverse=True)
    _arb_cache["opportunities"] = opportunities
    _arb_cache["ts"]            = now
    return opportunities


def format_arbitrage(opps: list) -> str:
    if not opps:
        return "Arbitrage: aucune opportunité détectée"
    lines = ["⚡ Arbitrage détecté:"]
    for o in opps[:3]:
        coin = o["symbol"].replace("USDT","")
        lines.append(
            f"  💰 {coin}: spread {o['spread_pct']:.2f}% "
            f"→ profit net ~{o['profit_est']:.2f}%"
        )
    return "\n".join(lines)


# ── Options : Put/Call Ratio ─────────────────────────────────
_options_cache = {"data": None, "ts": 0}

def get_options_data() -> dict:
    """
    Récupère le put/call ratio via Deribit API publique.
    Put/Call > 1 = sentiment bearish (plus de puts que de calls).
    Put/Call < 0.7 = sentiment bullish.
    """
    now = time.time()
    if now - _options_cache["ts"] < 300:
        return _options_cache["data"] or {}

    try:
        # Deribit API publique (pas de clé requise pour les données de marché)
        r = requests.get(
            "https://deribit.com/api/v2/public/get_book_summary_by_currency",
            params={"currency": "BTC", "kind": "option"},
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200:
            instruments = r.json().get("result", [])
            calls = sum(1 for i in instruments if "-C" in i.get("instrument_name",""))
            puts  = sum(1 for i in instruments if "-P" in i.get("instrument_name",""))

            put_call_ratio = round(puts / calls, 2) if calls > 0 else 1.0
            total_oi = sum(i.get("open_interest",0) for i in instruments)

            result = {
                "put_call_ratio": put_call_ratio,
                "calls":          calls,
                "puts":           puts,
                "total_oi":       total_oi,
                "sentiment":      "bearish" if put_call_ratio > 1.2
                                  else "bullish" if put_call_ratio < 0.7
                                  else "neutral",
            }
            _options_cache["data"] = result
            _options_cache["ts"]   = now
            return result
    except Exception as e:
        print(f"[OPT] {e}")

    return {}


def format_options(data: dict) -> str:
    if not data:
        return "Options: données indisponibles"
    pcr = data.get("put_call_ratio", 1)
    sent= data.get("sentiment","neutral")
    e   = "🐻" if sent=="bearish" else "🐂" if sent=="bullish" else "➡️"
    return f"📊 Options BTC: P/C={pcr} {e} ({sent})"


# ═══════════════════════════════════════════════════════════════
#  BASE DE DONNÉES
# ═══════════════════════════════════════════════════════════════
def init_db():
    con = sqlite3.connect(DB_FILE)
    c   = con.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS trades(
        id INTEGER PRIMARY KEY, symbol TEXT, market TEXT, side TEXT,
        price_in REAL, price_out REAL, qty REAL, amount_usd REAL,
        pnl REAL, pnl_pct REAL, confidence INTEGER, reason TEXT,
        exit_reason TEXT, duration_min INTEGER, time_in TEXT, time_out TEXT,
        patterns TEXT, leverage INTEGER, kelly_pct REAL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS lessons(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER, symbol TEXT, market TEXT, pnl REAL,
        lecon TEXT, pattern TEXT, action_future TEXT, type TEXT, date TEXT
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
        created_date TEXT, last_updated TEXT, active INTEGER DEFAULT 1
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS trader_signals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT, author TEXT, content TEXT, sentiment TEXT,
        symbol TEXT, strength INTEGER, timestamp TEXT, url TEXT, hash TEXT UNIQUE
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS arbitrage_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, symbol TEXT, exchange1 TEXT, price1 REAL,
        exchange2 TEXT, price2 REAL, spread_pct REAL, profit_est REAL
    )""")
    con.commit()
    con.close()


def db_save_trade(t: dict):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT OR REPLACE INTO trades VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            t["id"], t["symbol"], t["market"], t["side"],
            t["price_in"], t.get("price_out"), t["qty"], t["amount_usd"],
            t.get("pnl"), t.get("pnl_pct"), t["confidence"],
            t["reason"], t.get("exit_reason"), t.get("duration_min"),
            t["time_in"], t.get("time_out"),
            json.dumps(t.get("patterns", [])),
            t.get("leverage", 1), t.get("kelly_pct", 0),
        ))
        con.commit()
        con.close()
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
        con.commit()
        con.close()
    except Exception as e:
        print(f"[DB-L] {e}")


def db_save_arb(opp: dict):
    try:
        con = sqlite3.connect(DB_FILE)
        ex2 = "binance" if "binance" in opp else "kraken"
        con.execute("""INSERT INTO arbitrage_log
            (timestamp,symbol,exchange1,price1,exchange2,price2,spread_pct,profit_est)
            VALUES(?,?,?,?,?,?,?,?)""", (
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            opp["symbol"], "bybit", opp["bybit"],
            ex2, opp.get("binance", opp.get("kraken",0)),
            opp["spread_pct"], opp["profit_est"],
        ))
        con.commit()
        con.close()
    except Exception as e:
        print(f"[ARB-DB] {e}")


def db_win_rate(n=30) -> float:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute(
            "SELECT pnl FROM trades WHERE pnl IS NOT NULL ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        con.close()
        if not rows:
            return 50.0
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
        """).fetchall()
        con.close()
        return [{"s": r[0].replace("USDT",""), "n": r[1],
                 "pnl": round(r[2],2), "wr": round(r[3],0)} for r in rows]
    except Exception:
        return []


def db_best_patterns(symbol: str) -> list:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute("""
            SELECT pattern FROM lessons WHERE symbol=? AND type='succes'
            GROUP BY pattern ORDER BY COUNT(*) DESC LIMIT 5
        """, (symbol,)).fetchall()
        con.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


def db_worst_patterns(symbol: str) -> list:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute("""
            SELECT pattern FROM lessons WHERE symbol=? AND type='erreur'
            GROUP BY pattern ORDER BY COUNT(*) DESC LIMIT 5
        """, (symbol,)).fetchall()
        con.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


def get_active_rules() -> str:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute("""
            SELECT rule FROM trading_rules WHERE active=1
            ORDER BY win_rate DESC LIMIT 5
        """).fetchall()
        con.close()
        if not rows:
            return ""
        return "MES RÈGLES:\n" + "\n".join(f"• {r[0]}" for r in rows)
    except Exception:
        return ""


def get_db_trader_signals_summary() -> str:
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute("""
            SELECT author, sentiment, symbol, timestamp
            FROM trader_signals ORDER BY id DESC LIMIT 8
        """).fetchall()
        con.close()
        if not rows:
            return "Aucun signal collecté"
        lines = []
        for r in rows:
            e = "📈" if r[1]=="bullish" else "📉" if r[1]=="bearish" else "➡️"
            lines.append(f"{e} @{r[0]} [{r[2]}] ({r[3][11:16]})")
        return "\n".join(lines)
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════
#  PERSISTANCE JSON
# ═══════════════════════════════════════════════════════════════
def save_data():
    try:
        DATA_FILE.write_text(
            json.dumps({"sim": sim, "memory": memory}, indent=2, default=str))
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
                "positions": {}, "trades": [], "equity_history": [], "session": 1,
            }.items():
                sim.setdefault(k, v)
            for k, v in {
                "lessons": [], "patterns_to_avoid": [],
                "patterns_that_work": [],
                "confidence_threshold": CONFIDENCE_BASE,
                "total_wins": 0, "total_losses": 0,
            }.items():
                memory.setdefault(k, v)
            print(f"[LOAD] {len(sim['trades'])} trades | {len(memory['lessons'])} leçons")
            return
        except Exception as e:
            print(f"[LOAD] {e}")
    sim    = {"cash": CAPITAL_INITIAL, "initial": CAPITAL_INITIAL,
              "positions": {}, "trades": [], "equity_history": [], "session": 1}
    memory = {"lessons": [], "patterns_to_avoid": [],
              "patterns_that_work": [],
              "confidence_threshold": CONFIDENCE_BASE,
              "total_wins": 0, "total_losses": 0}


# ═══════════════════════════════════════════════════════════════
#  DONNÉES DE MARCHÉ
# ═══════════════════════════════════════════════════════════════
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
            [float(c[4]) for c in reversed(r["result"]["list"])], dtype=float)
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
        return {"ratio": ratio,
                "pressure": "acheteurs" if ratio>1.3 else "vendeurs" if ratio<0.77 else "neutre"}
    except Exception:
        return {"ratio": 1.0, "pressure": "N/A"}


def get_yahoo_price(ticker: str) -> float:
    now = time.time()
    if ticker in _yahoo_cache:
        ts, p = _yahoo_cache[ticker]
        if now - ts < 30:
            return p
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d"
        r   = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        p   = float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])
        _yahoo_cache[ticker] = (now, p)
        return p
    except Exception:
        return _yahoo_cache.get(ticker, (0, 0.0))[1]


def get_yahoo_closes(ticker: str, interval="1m", range_="1d") -> pd.Series:
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval={interval}&range={range_}"
        r   = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        return pd.Series([c for c in closes if c is not None], dtype=float)
    except Exception:
        return pd.Series(dtype=float)


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
        rs    = (gain.ewm(com=13, adjust=False).mean() /
                 loss.ewm(com=13, adjust=False).mean().replace(0, np.nan))
        rsi   = float((100 - 100/(1+rs)).iloc[-1])

        ema9  = float(closes.ewm(span=9,  adjust=False).mean().iloc[-1])
        ema20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])

        macd_l = float((closes.ewm(span=12,adjust=False).mean() -
                        closes.ewm(span=26,adjust=False).mean()).iloc[-1])
        macd_s = float((closes.ewm(span=12,adjust=False).mean() -
                        closes.ewm(span=26,adjust=False).mean())
                       .ewm(span=9,adjust=False).mean().iloc[-1])
        macd_h = round(macd_l - macd_s, 6)

        sma20  = closes.rolling(20).mean()
        std20  = closes.rolling(20).std()
        bb_up  = float((sma20 + 2*std20).iloc[-1])
        bb_low = float((sma20 - 2*std20).iloc[-1])
        bb_pct = round((float(closes.iloc[-1])-bb_low)/(bb_up-bb_low)*100, 1) \
                 if bb_up != bb_low else 50.0

        mom5  = float((closes.iloc[-1]-closes.iloc[-6])/closes.iloc[-6]*100) \
                if len(closes)>=6 else 0.0
        mom15 = float((closes.iloc[-1]-closes.iloc[-16])/closes.iloc[-16]*100) \
                if len(closes)>=16 else 0.0
        vol   = float(closes.pct_change().dropna().iloc[-10:].std()*100) \
                if len(closes)>=10 else 0.0

        return {
            "rsi": round(rsi, 1), "ema9": round(ema9, 6),
            "ema20": round(ema20, 6), "ema50": round(ema50, 6),
            "macd_h": macd_h, "bb_pct": bb_pct,
            "mom5": round(mom5, 3), "mom15": round(mom15, 3),
            "vol": round(vol, 3),
            "trend": "↑" if ema20>ema50 else "↓",
            "ema_cross": "BULL" if ema9>ema20 else "BEAR",
            "price": float(closes.iloc[-1]),
        }
    except Exception as e:
        print(f"[IND] {e}")
        return {}


def get_multi_tf(symbol: str) -> dict:
    result = {}
    for interval, label in [("1","1m"), ("5","5m"), ("15","15m")]:
        closes = get_klines(symbol, interval, 80)
        if not closes.empty:
            ind = compute_indicators(closes)
            if ind:
                result[label] = ind
    return result


def tf_score(mtf: dict) -> dict:
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


def detect_patterns(symbol: str, ind: dict, vols: list) -> list:
    patterns = []
    try:
        rsi      = ind.get("rsi", 50)
        mom5     = ind.get("mom5", 0)
        mom15    = ind.get("mom15", 0)
        bb_pct   = ind.get("bb_pct", 50)
        macd_h   = ind.get("macd_h", 0)
        ema_cross= ind.get("ema_cross", "BEAR")

        avg_vol  = sum(vols[:-1])/max(len(vols)-1,1) if vols else 0
        last_vol = vols[-1] if vols else 0
        vol_ratio= last_vol/avg_vol if avg_vol>0 else 1

        if rsi < 28 and bb_pct < 5:
            patterns.append({"name":"Survente extrême","signal":"BUY","strength":"fort","score":3})
        elif rsi > 72 and bb_pct > 95:
            patterns.append({"name":"Surachat extrême","signal":"SELL","strength":"fort","score":3})

        if mom5 > 1.2 and vol_ratio > 2.0 and macd_h > 0:
            patterns.append({"name":"Breakout haussier","signal":"BUY","strength":"fort","score":3})
        elif mom5 < -1.2 and vol_ratio > 2.0 and macd_h < 0:
            patterns.append({"name":"Breakdown baissier","signal":"SELL","strength":"fort","score":3})

        if ema_cross == "BULL" and macd_h > 0 and rsi < 60:
            patterns.append({"name":"EMA Cross Bull","signal":"BUY","strength":"modéré","score":2})
        elif ema_cross == "BEAR" and macd_h < 0 and rsi > 40:
            patterns.append({"name":"EMA Cross Bear","signal":"SELL","strength":"modéré","score":2})

        if abs(mom5) > 4 and vol_ratio > 5:
            patterns.append({"name":"⚠️ Pump/Dump","signal":"HOLD","strength":"ALERTE",
                              "desc":f"{mom5:+.1f}% en 5min, vol x{vol_ratio:.1f}"})
    except Exception as e:
        print(f"[PAT] {e}")
    return patterns


def scan_market() -> list:
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

            score = 0
            if ind["rsi"] < 35:    score += 3
            elif ind["rsi"] < 45:  score += 1
            if ind["rsi"] > 70:    score -= 3
            elif ind["rsi"] > 60:  score -= 1
            if ind["macd_h"] > 0:  score += 2
            else:                  score -= 1
            if ind["mom5"] > 1:    score += 2
            elif ind["mom5"] < -1: score -= 2
            if ind["ema_cross"] == "BULL": score += 1
            else:                          score -= 1

            has_alert = any(p["signal"]=="HOLD" for p in pats)
            opps.append({
                "symbol": symbol, "price": price, "score": score,
                "direction": "BUY" if score > 0 else "SELL",
                "ind": ind, "patterns": pats, "has_alert": has_alert,
            })
        except Exception:
            pass

    opps.sort(key=lambda x: abs(x["score"]), reverse=True)
    return opps[:10]
# ═══════════════════════════════════════════════════════════════
#  IA — VOTE MAJORITAIRE
# ═══════════════════════════════════════════════════════════════
_ai_calls = {"count": 0, "window_start": time.time(), "last_call": 0}

def _can_call_ai() -> bool:
    now = time.time()
    if now - _ai_calls["window_start"] > 3600:
        _ai_calls["count"]        = 0
        _ai_calls["window_start"] = now
    if _ai_calls["count"] >= AI_CALLS_PER_HOUR_MAX:
        return False
    if now - _ai_calls["last_call"] < AI_CYCLE_MIN_INTERVAL:
        return False
    return True

def _register_ai_call():
    _ai_calls["count"]    += 1
    _ai_calls["last_call"] = time.time()


def ask_model_single(prompt: str, model: str = None) -> dict:
    if not _can_call_ai():
        return {"signal":"HOLD","confidence":0,"reason":"quota_horaire","risk":"HIGH"}
    if model is None:
        model = GROQ_FAST_MODEL
    for m in [model, GROQ_SECONDARY_MODEL]:
        try:
            _register_ai_call()
            r = groq_client.chat.completions.create(
                model=m, max_tokens=80, temperature=0.1,
                messages=[
                    {"role":"system","content":"JSON: {signal,confidence,reason,risk,market}"},
                    {"role":"user","content":prompt[:500]}
                ],
            )
            t = r.choices[0].message.content.strip()
            t = t.replace("```json","").replace("```","").strip()
            s = t.find("{"); e = t.rfind("}")+1
            if s >= 0 and e > s:
                t = t[s:e]
            result = json.loads(t)
            if result.get("signal") not in ("BUY","SELL","HOLD"):
                result["signal"] = "HOLD"
            return result
        except json.JSONDecodeError:
            return {"signal":"HOLD","confidence":0,"reason":"json_error","risk":"HIGH"}
        except Exception as ex:
            err = str(ex)
            if "rate_limit" in err or "429" in err:
                _ai_calls["count"] = AI_CALLS_PER_HOUR_MAX
                return {"signal":"HOLD","confidence":0,"reason":"rate_limit","risk":"HIGH"}
            if "decommissioned" in err or "400" in err:
                continue
            return {"signal":"HOLD","confidence":0,"reason":"api_error","risk":"HIGH"}
    return {"signal":"HOLD","confidence":0,"reason":"all_failed","risk":"HIGH"}


def vote(prompt: str) -> dict:
    r1 = ask_model_single(prompt, GROQ_FAST_MODEL)
    if r1.get("reason") in ("quota_horaire","all_failed"):
        return {**r1, "votes":[r1["signal"]],"consensus":"0/1"}
    if r1["signal"] == "HOLD" or r1.get("confidence",0) < 60:
        return {**r1, "votes":[r1["signal"]],"consensus":"1/1"}
    r2 = ask_model_single(prompt, GROQ_PRIMARY_MODEL)
    if r2["signal"] == r1["signal"]:
        conf = min(95, round((r1.get("confidence",0)+r2.get("confidence",0))/2)+5)
        return {
            "signal": r1["signal"], "confidence": conf,
            "reason": r2.get("reason", r1.get("reason","")),
            "risk":   r1.get("risk","MEDIUM"), "market": r1.get("market","SPOT"),
            "votes":  [r1["signal"],r2["signal"]], "consensus": "2/2",
        }
    return {
        "signal":"HOLD","confidence":0,
        "reason":f"Désaccord ({r1['signal']}/{r2['signal']})",
        "risk":"HIGH","votes":[r1["signal"],r2["signal"]],"consensus":"0/2",
    }


# ═══════════════════════════════════════════════════════════════
#  ANALYSE COMPLÈTE AVEC NOUVELLES SOURCES
# ═══════════════════════════════════════════════════════════════
def analyze(opp: dict, fear_greed: str) -> dict:
    symbol = opp["symbol"]
    price  = opp["price"]
    ind    = opp["ind"]
    pats   = opp["patterns"]

    mtf    = get_multi_tf(symbol)
    conf   = tf_score(mtf)
    ob     = get_order_book(symbol)
    in_pos = any(p["symbol"]==symbol for p in sim["positions"].values())

    best_p  = db_best_patterns(symbol)
    worst_p = db_worst_patterns(symbol)
    thresh  = memory.get("confidence_threshold", CONFIDENCE_BASE)

    # Nouvelles sources de données
    liq_data   = get_liquidations()
    onchain    = get_onchain_data()
    whales     = get_whale_alerts()
    arb_opps   = detect_arbitrage()
    options    = get_options_data()
    poly_mkts  = get_polymarket_markets()

    liq_str  = interpret_liquidations(liq_data)
    chain_str= format_onchain(onchain)
    whale_str= format_whale_alerts(whales)
    arb_str  = format_arbitrage(arb_opps)
    opt_str  = format_options(options)
    poly_str = format_polymarket(poly_mkts)

    # Kelly pour ce symbole
    kelly_pct = dynamic_position_size(70, "SPOT", symbol)

    # Signaux traders DB
    trader_sigs = get_db_trader_signals_summary()
    my_rules    = get_active_rules()

    # Fear & Greed value
    fg_value = 50
    try:
        fg_value = int(fear_greed.split(":")[1].split("/")[0].strip())
    except Exception:
        pass

    fg_context = ""
    if fg_value < 20:
        fg_context = "⚠️ EXTREME FEAR → Opportunité rare selon Saylor/Buffett"
    elif fg_value < 35:
        fg_context = "Fear élevé → opportunité selon Buffett"

    prompt = f"""{symbol} ${price:.4f}
RSI:{ind.get('rsi','?')} MACD:{ind.get('macd_h','?')} mom5:{ind.get('mom5','?')}% trend:{ind.get('trend','?')}
OB:{ob['pressure']} TFscore:{conf['score']}/9
{fear_greed} {fg_context}

NOUVELLES SOURCES:
{liq_str}
{chain_str}
{whale_str}
{arb_str}
{opt_str}
{poly_str}

SIGNAUX TRADERS: {trader_sigs[:200]}
HISTORIQUE: gains:{best_p[:2]} erreurs:{worst_p[:2]}
{my_rules}
{TRADER_PHILOSOPHIES[:200]}

Kelly calculé: {kelly_pct*100:.1f}% du capital
En position: {'OUI' if in_pos else 'NON'}

BUY=spot long, SELL=futures short.
JSON: {{"signal":"BUY/SELL/HOLD","confidence":0-100,"reason":"raison","risk":"LOW/MEDIUM/HIGH","market":"SPOT/FUTURES"}}"""

    result = vote(prompt)
    result.update({
        "symbol": symbol, "price": price, "patterns": pats,
        "confluence": conf, "ob": ob, "ind": ind, "kelly_pct": kelly_pct,
        "arb_opportunities": arb_opps,
    })
    return result


# ═══════════════════════════════════════════════════════════════
#  GESTION DES POSITIONS
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
        return {"total":0,"wins":0,"losses":0,"win_rate":0,"best":0,"worst":0,"total_pnl":0,"avg_dur":0}
    pnls = [t["pnl"] for t in closed]
    wins = [p for p in pnls if p>0]
    durs = [t.get("duration_min",0) for t in closed if t.get("duration_min")]
    return {
        "total":     len(closed),
        "wins":      len(wins),
        "losses":    len(closed)-len(wins),
        "win_rate":  round(len(wins)/len(closed)*100, 1),
        "best":      round(max(pnls), 4),
        "worst":     round(min(pnls), 4),
        "total_pnl": round(sum(pnls), 4),
        "avg_dur":   round(sum(durs)/len(durs), 1) if durs else 0,
    }


def open_trade(analysis: dict, send_fn) -> dict | None:
    symbol  = analysis["symbol"]
    price   = analysis["price"]
    signal  = analysis["signal"]
    conf    = analysis["confidence"]
    reason  = analysis["reason"]
    market  = analysis.get("market", "SPOT")
    pats    = analysis.get("patterns", [])
    side    = "LONG" if signal=="BUY" else "SHORT"

    if signal == "SELL" and market == "SPOT":
        return None
    if any(p["symbol"]==symbol for p in sim["positions"].values()):
        return None
    if len(sim["positions"]) >= MAX_POSITIONS:
        return None
    if sim["cash"] < 20:
        return None

    # Utilise Kelly Criterion pour la taille
    kelly_pct = analysis.get("kelly_pct") or dynamic_position_size(conf, market, symbol)
    if analysis.get("_forced_pct"):
        kelly_pct = analysis["_forced_pct"]

    leverage = LEVERAGE_SIM if market=="FUTURES" else 1
    amount   = sim["cash"] * kelly_pct
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
        "peak_price":  price,
        "trough_price": price,
        "kelly_pct":   kelly_pct,
    }

    pos_key = f"{market}_{symbol}_{side}_{trade['id']}"
    sim["trades"].append(trade)
    sim["positions"][pos_key] = {**trade, "pos_key": pos_key}
    db_save_trade(trade)
    save_data()
    bot_state["trades_today"] += 1

    sl   = price*(1-STOP_LOSS_PCT)   if side=="LONG" else price*(1+STOP_LOSS_PCT)
    tp   = price*(1+TAKE_PROFIT_PCT) if side=="LONG" else price*(1-TAKE_PROFIT_PCT)
    coin = symbol.replace("USDT","")
    learning = "🎓" if analysis.get("_forced_pct") else ""

    send_fn(
        f"{'🟢' if side=='LONG' else '🔴'} {learning} J'achète {coin} ({market})\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Prix     : ${price:.4f}\n"
        f"💰 Mise     : ${amount:.2f} (Kelly {kelly_pct*100:.1f}%)\n"
        f"🛑 SL       : ${sl:.4f} (-{STOP_LOSS_PCT*100:.1f}%)\n"
        f"🎯 TP       : ${tp:.4f} (+{TAKE_PROFIT_PCT*100:.1f}%)\n"
        f"🧠 Raison   : {reason[:100]}\n"
        f"🔒 Confiance: {conf}% | Trade #{trade['id']}"
    )
    return trade


def close_trade(pos_key: str, price: float, reason: str, send_fn) -> dict | None:
    pos = sim["positions"].pop(pos_key, None)
    if not pos:
        return None

    side  = pos["side"]
    entry = pos["price_in"]
    amt   = pos["amount_usd"]
    lev   = pos.get("leverage", 1)

    if side == "LONG":
        pnl     = (price - entry) / entry * amt * lev
        pnl_pct = (price - entry) / entry * 100 * lev
    else:
        pnl     = (entry - price) / entry * amt * lev
        pnl_pct = (entry - price) / entry * 100 * lev

    sim["cash"] += amt + pnl

    duration = 0
    try:
        t_in     = datetime.strptime(pos["time_in"], "%Y-%m-%d %H:%M:%S")
        duration = int((datetime.now()-t_in).total_seconds()/60)
    except Exception:
        pass

    trade = next((t for t in reversed(sim["trades"]) if t["id"]==pos["id"]), None)
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
        memory["total_wins"]   = memory.get("total_wins",0) + 1
    else:
        memory["total_losses"] = memory.get("total_losses",0) + 1

    save_data()

    equity_now = get_equity()
    pnl_total  = equity_now - sim["initial"]
    coin = pos["symbol"].replace("USDT","")
    chg  = (price-entry)/entry*100

    send_fn(
        f"{'✅' if pnl>0 else '❌'} {coin} fermé — Trade #{pos['id']}\n"
        f"  ${entry:.4f} → ${price:.4f} ({chg:+.2f}%)\n"
        f"  {'🤑' if pnl>0 else '💸'} ${pnl:+.4f} | {reason}\n"
        f"  Capital: ${equity_now:.2f} (total: ${pnl_total:+.2f})"
    )
    return trade


def monitor_positions(send_fn):
    if not sim["positions"]:
        return
    prices = get_prices_batch()
    for pos_key, pos in list(sim["positions"].items()):
        if pos.get("trade_type") in ("MICRO","MEME"):
            continue
        symbol = pos["symbol"]
        side   = pos["side"]
        entry  = pos["price_in"]
        lev    = pos.get("leverage", 1)
        price  = prices.get(symbol) or get_price(symbol)
        if not price:
            continue

        if side == "LONG":
            pos["peak_price"] = max(pos.get("peak_price", entry), price)
            change   = (price - entry) / entry
            trailing = (pos["peak_price"] - price) / pos["peak_price"]
        else:
            pos["trough_price"] = min(pos.get("trough_price", entry), price)
            change   = (entry - price) / entry
            trailing = (price - pos["trough_price"]) / pos["trough_price"]

        reason = None
        if change * lev <= -STOP_LOSS_PCT:
            reason = f"🛑 SL ({change*100*lev:+.2f}%)"
        elif change * lev >= TAKE_PROFIT_PCT:
            reason = f"🎯 TP ({change*100*lev:+.2f}%)"
        elif change * lev > 0.008 and trailing >= TRAILING_PCT:
            reason = f"📐 TRAIL ({trailing*100:.2f}%)"

        if reason:
            close_trade(pos_key, price, reason, send_fn)


# ═══════════════════════════════════════════════════════════════
#  MICRO-TRADING
# ═══════════════════════════════════════════════════════════════
_kline_cache_1m: dict = {}

def get_klines_1m_cached(symbol: str) -> pd.Series:
    now = time.time()
    if symbol in _kline_cache_1m:
        ts, closes = _kline_cache_1m[symbol]
        if now - ts < 5:
            return closes
    closes = get_klines(symbol, "1", 30)
    _kline_cache_1m[symbol] = (now, closes)
    return closes


def micro_signal(symbol: str, price: float) -> dict:
    try:
        closes = get_klines_1m_cached(symbol)
        if len(closes) < 14:
            return {"signal":"HOLD","score":0,"conf":0}

        ema5  = float(closes.ewm(span=5,  adjust=False).mean().iloc[-1])
        ema13 = float(closes.ewm(span=13, adjust=False).mean().iloc[-1])
        ema5_prev  = float(closes.ewm(span=5,  adjust=False).mean().iloc[-2])
        ema13_prev = float(closes.ewm(span=13, adjust=False).mean().iloc[-2])

        delta = closes.diff()
        gain  = delta.clip(lower=0).ewm(com=6, adjust=False).mean()
        loss  = (-delta).clip(lower=0).ewm(com=6, adjust=False).mean()
        rsi7  = float((100 - 100/(1+gain/loss.replace(0,np.nan))).iloc[-1])

        mom3  = float((closes.iloc[-1]-closes.iloc[-4])/closes.iloc[-4]*100) \
                if len(closes)>=4 else 0

        sma10 = closes.rolling(10).mean()
        std10 = closes.rolling(10).std()
        bb_up = float((sma10+1.5*std10).iloc[-1])
        bb_lo = float((sma10-1.5*std10).iloc[-1])
        bb_pct= (price-bb_lo)/(bb_up-bb_lo)*100 if bb_up!=bb_lo else 50

        vols      = get_volume_data(symbol, "1", 10)
        avg_vol   = sum(vols[:-1])/max(len(vols)-1,1) if len(vols)>1 else 1
        vol_ratio = vols[-1]/avg_vol if avg_vol>0 else 1.0

        score = 0
        if ema5_prev<=ema13_prev and ema5>ema13:   score += 2
        elif ema5_prev>=ema13_prev and ema5<ema13: score -= 2

        if rsi7 < 28:   score += 2
        elif rsi7 < 40: score += 1
        elif rsi7 > 72: score -= 2
        elif rsi7 > 60: score -= 1

        if mom3 > 0.6:    score += 1
        elif mom3 < -0.6: score -= 1

        if bb_pct < 8:    score += 1
        elif bb_pct > 92: score -= 1

        if vol_ratio > 2.5 and score > 0: score += 1
        if vol_ratio > 2.5 and score < 0: score -= 1

        if score >= 4:
            return {"signal":"BUY","score":score,"conf":min(95,60+score*7),
                    "rsi7":round(rsi7,1),"mom3":round(mom3,3),
                    "reason":f"EMA{'↑' if ema5>ema13 else '↓'} RSI7={rsi7:.0f} mom={mom3:+.2f}% vol={vol_ratio:.1f}x"}
        elif score <= -4:
            return {"signal":"SELL","score":score,"conf":min(95,60+abs(score)*7),
                    "rsi7":round(rsi7,1),"mom3":round(mom3,3),
                    "reason":f"EMA{'↑' if ema5>ema13 else '↓'} RSI7={rsi7:.0f} mom={mom3:+.2f}%"}
        return {"signal":"HOLD","score":score,"conf":0}
    except Exception as e:
        return {"signal":"HOLD","score":0,"conf":0}


def open_micro_trade(symbol: str, price: float, signal: dict, send_fn) -> dict | None:
    if signal["signal"] == "SHORT":
        return None
    micro_count = sum(1 for p in sim["positions"].values() if p.get("trade_type")=="MICRO")
    if micro_count >= MAX_MICRO_POSITIONS:
        return None
    if any(p["symbol"]==symbol and p.get("trade_type")=="MICRO" for p in sim["positions"].values()):
        return None
    if sim["cash"] < 15:
        return None

    amount = sim["cash"] * MICRO_MAX_PCT
    qty    = amount / price
    sim["cash"] -= amount

    trade = {
        "id": len(sim["trades"]) + 1, "symbol": symbol,
        "market": "MICRO", "side": "LONG", "trade_type": "MICRO",
        "price_in": price, "price_out": None, "qty": qty,
        "amount_usd": amount, "confidence": signal["conf"],
        "reason": signal.get("reason",""), "exit_reason": None,
        "time_in": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "time_out": None, "pnl": None, "pnl_pct": None,
        "duration_min": None, "patterns": [f"score={signal['score']}"],
        "leverage": 1, "peak_price": price, "open_time": time.time(),
        "kelly_pct": MICRO_MAX_PCT,
    }

    pos_key = f"MICRO_{symbol}_{trade['id']}"
    sim["trades"].append(trade)
    sim["positions"][pos_key] = {**trade, "pos_key": pos_key}
    db_save_trade(trade)
    bot_state["trades_today"] += 1
    bot_state["micro_count"] = bot_state.get("micro_count",0) + 1

    coin = symbol.replace("USDT","")
    send_fn(
        f"⚡ Micro {coin} #{trade['id']}\n"
        f"  ${price:.4f} | ${amount:.2f} | {signal.get('reason','')[:60]}"
    )
    return trade


def monitor_micro_positions(send_fn):
    now    = time.time()
    prices = get_prices_batch()
    for pos_key, pos in list(sim["positions"].items()):
        if pos.get("trade_type") != "MICRO":
            continue
        symbol  = pos["symbol"]
        price   = prices.get(symbol) or get_price(symbol, force=True)
        if not price:
            continue
        entry   = pos["price_in"]
        change  = (price - entry) / entry
        elapsed = now - pos.get("open_time", now)
        pos["peak_price"] = max(pos.get("peak_price",entry), price)
        trailing = (pos["peak_price"]-price)/pos["peak_price"]

        reason = None
        if change <= -MICRO_SL_PCT:
            reason = f"🛑 MICRO SL ({change*100:+.2f}%)"
        elif change >= MICRO_TP_PCT:
            reason = f"🎯 MICRO TP ({change*100:+.2f}%)"
        elif change > 0.003 and trailing >= MICRO_TRAILING_PCT:
            reason = f"📐 MICRO TRAIL ({trailing*100:.2f}%)"
        elif elapsed >= MICRO_MAX_DURATION:
            reason = f"⏱ TIMEOUT {int(elapsed)}s"

        if reason:
            pnl     = change * pos["amount_usd"]
            pnl_pct = change * 100
            sim["cash"] += pos["amount_usd"] + pnl
            trade = next((t for t in reversed(sim["trades"]) if t["id"]==pos["id"]), None)
            if trade:
                trade.update({
                    "price_out":    price,
                    "time_out":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "pnl":          round(pnl,6),
                    "pnl_pct":      round(pnl_pct,3),
                    "exit_reason":  reason,
                    "duration_min": max(1,int(elapsed/60)),
                })
                db_save_trade(trade)
                learn_from_trade(trade, send_fn=None)
            del sim["positions"][pos_key]
            save_data()
            coin = symbol.replace("USDT","")
            send_fn(f"{'✅' if pnl>0 else '❌'} Micro {coin}: ${pnl:+.4f} | {reason}")
            if pnl > 0: memory["total_wins"]   = memory.get("total_wins",0)+1
            else:        memory["total_losses"] = memory.get("total_losses",0)+1


def run_micro_cycle(send_fn):
    prices = get_prices_batch()
    for symbol in MICRO_SYMBOLS:
        if not bot_state["running"]:
            break
        price = prices.get(symbol, 0)
        if not price:
            continue
        micro_count = sum(1 for p in sim["positions"].values() if p.get("trade_type")=="MICRO")
        if micro_count >= MAX_MICRO_POSITIONS:
            break
        if any(p["symbol"]==symbol and p.get("trade_type")=="MICRO" for p in sim["positions"].values()):
            continue
        sig = micro_signal(symbol, price)
        if sig["signal"] != "HOLD" and sig["conf"] >= MICRO_CONF_MIN:
            open_micro_trade(symbol, price, sig, send_fn)


# ═══════════════════════════════════════════════════════════════
#  MEMECOINS
# ═══════════════════════════════════════════════════════════════
def get_dex_pair(query: str) -> dict:
    now = time.time()
    if query in _dex_cache:
        ts, d = _dex_cache[query]
        if now - ts < 15:
            return d
    try:
        url = f"https://api.dexscreener.com/latest/dex/search?q={query}"
        r   = requests.get(url, timeout=8, headers={"User-Agent":"Mozilla/5.0"})
        pairs = r.json().get("pairs",[])
        if not pairs:
            return {}
        best = max(pairs, key=lambda p: float(p.get("liquidity",{}).get("usd",0) or 0))
        d = {
            "symbol":    best.get("baseToken",{}).get("symbol","?"),
            "price":     float(best.get("priceUsd",0) or 0),
            "change_5m": float(best.get("priceChange",{}).get("m5",0) or 0),
            "change_1h": float(best.get("priceChange",{}).get("h1",0) or 0),
            "volume_1h": float(best.get("volume",{}).get("h1",0) or 0),
            "liquidity": float(best.get("liquidity",{}).get("usd",0) or 0),
            "chain":     best.get("chainId","?"),
            "address":   best.get("baseToken",{}).get("address",""),
            "url":       best.get("url",""),
        }
        _dex_cache[query] = (now, d)
        return d
    except Exception:
        return {}


def run_meme_cycle(send_fn):
    """Cycle memecoin simplifié."""
    prices = get_prices_batch()
    for sym in MEMECOIN_SYMBOLS:
        price = prices.get(sym, 0)
        if not price:
            continue
        already = any(p["symbol"]==sym for p in sim["positions"].values())
        if already:
            continue
        closes = get_klines(sym, "5", 30)
        if len(closes) < 10:
            continue
        ind = compute_indicators(closes)
        if not ind:
            continue
        score = 0
        if ind.get("rsi",50) < 30: score += 3
        if ind.get("mom5",0) > 4:  score += 3
        if ind.get("macd_h",0) > 0: score += 2
        if score >= 6 and sim["cash"] > 20:
            meme_count = sum(1 for p in sim["positions"].values() if p.get("trade_type")=="MEME")
            if meme_count < 2:
                amount = sim["cash"] * MEME_MAX_PCT
                qty    = amount / price
                sim["cash"] -= amount
                trade = {
                    "id": len(sim["trades"])+1, "symbol": sym,
                    "market": "MEME", "side": "LONG", "trade_type": "MEME",
                    "price_in": price, "price_out": None, "qty": qty,
                    "amount_usd": amount, "confidence": min(85, 50+score*5),
                    "reason": f"Meme score={score} RSI={ind.get('rsi',0):.0f}",
                    "exit_reason": None,
                    "time_in": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "time_out": None, "pnl": None, "pnl_pct": None,
                    "duration_min": None, "patterns": ["memecoin"],
                    "leverage": 1, "peak_price": price, "open_time": time.time(),
                    "kelly_pct": MEME_MAX_PCT,
                }
                pos_key = f"MEME_{sym}_{trade['id']}"
                sim["trades"].append(trade)
                sim["positions"][pos_key] = {**trade, "pos_key": pos_key}
                db_save_trade(trade)
                coin = sym.replace("USDT","")
                send_fn(f"🎰 Meme {coin} #{trade['id']} | ${price:.6f} | score={score}")

    # Surveille les positions meme
    now    = time.time()
    prices2 = get_prices_batch()
    for pos_key, pos in list(sim["positions"].items()):
        if pos.get("trade_type") != "MEME":
            continue
        symbol  = pos["symbol"]
        price   = prices2.get(symbol, pos["price_in"])
        entry   = pos["price_in"]
        elapsed = now - pos.get("open_time", now)
        change  = (price-entry)/entry
        pos["peak_price"] = max(pos.get("peak_price",entry), price)
        trailing = (pos["peak_price"]-price)/pos["peak_price"]

        reason = None
        if change <= -MEME_SL_PCT:     reason = f"🛑 MEME SL ({change*100:+.1f}%)"
        elif change >= MEME_TP_PCT:    reason = f"🎯 MEME TP ({change*100:+.1f}%)"
        elif elapsed >= MEME_MAX_DURATION: reason = f"⏱ MEME TIMEOUT"

        if reason:
            pnl = change * pos["amount_usd"]
            sim["cash"] += pos["amount_usd"] + pnl
            trade = next((t for t in reversed(sim["trades"]) if t["id"]==pos["id"]), None)
            if trade:
                trade.update({
                    "price_out": price,
                    "time_out":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "pnl":       round(pnl,4),
                    "pnl_pct":   round(change*100,2),
                    "exit_reason": reason,
                    "duration_min": max(1,int(elapsed/60)),
                })
                db_save_trade(trade)
                learn_from_trade(trade, send_fn=None)
            del sim["positions"][pos_key]
            save_data()
            coin = symbol.replace("USDT","")
            send_fn(f"{'✅' if pnl>0 else '❌'} Meme {coin}: ${pnl:+.4f} | {reason}")


# ═══════════════════════════════════════════════════════════════
#  APPRENTISSAGE
# ═══════════════════════════════════════════════════════════════
def learn_from_trade(trade: dict, send_fn=None):
    if trade.get("pnl") is None:
        return
    try:
        verdict = "PERDANT" if trade["pnl"]<0 else "GAGNANT"
        prompt  = f"""Trade simulé {trade['symbol']} {trade['market']}
${trade['price_in']:.6f}→${trade.get('price_out',0):.6f}
PnL: ${trade['pnl']:+.4f} ({trade.get('pnl_pct',0):+.2f}%) — {verdict}
Durée: {trade.get('duration_min',0)}min
Kelly utilisé: {trade.get('kelly_pct',0)*100:.1f}%
Raison entrée: {trade['reason']}
Raison sortie: {trade.get('exit_reason','')}

JSON: {{"lecon":"leçon courte","pattern":"pattern clé","action_future":"règle concrète","type":"erreur ou succes"}}"""

        r = groq_client.chat.completions.create(
            model=GROQ_FAST_MODEL, max_tokens=100, temperature=0.2,
            messages=[{"role":"user","content":prompt}],
        )
        lesson = json.loads(
            r.choices[0].message.content.replace("```json","").replace("```","").strip()
        )
        lesson.update({
            "trade_id": trade["id"], "pnl": trade["pnl"],
            "symbol":   trade["symbol"], "market": trade.get("market","SPOT"),
            "date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        memory["lessons"].append(lesson)
        db_save_lesson(lesson)

        key = "patterns_that_work" if lesson["type"]=="succes" else "patterns_to_avoid"
        memory[key].append(lesson["pattern"])
        memory["lessons"]           = memory["lessons"][-60:]
        memory["patterns_that_work"]= memory["patterns_that_work"][-25:]
        memory["patterns_to_avoid"] = memory["patterns_to_avoid"][-25:]

        auto_adjust()
        save_data()
        print(f"[LEARN] {lesson['lecon']}")

        if send_fn:
            stats = get_stats()
            e     = "✅" if lesson["type"]=="succes" else "❌"
            coin  = trade["symbol"].replace("USDT","")
            send_fn(
                f"📚 Leçon #{len(memory['lessons'])} — {coin}\n"
                f"{e} {lesson['lecon']}\n"
                f"→ {lesson['action_future']}\n"
                f"📊 WR global: {stats['win_rate']}% ({stats['wins']}✅/{stats['losses']}❌)"
            )
    except Exception as e:
        print(f"[LEARN] {e}")


def auto_adjust():
    wr  = db_win_rate(20)
    cur = memory.get("confidence_threshold", CONFIDENCE_BASE)
    if wr > 62 and cur > CONFIDENCE_MIN:
        memory["confidence_threshold"] = max(CONFIDENCE_MIN, cur-2)
    elif wr < 40 and cur < CONFIDENCE_MAX:
        memory["confidence_threshold"] = min(CONFIDENCE_MAX, cur+3)


def generate_trading_rules():
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    if len(closed) < 10 or len(closed) % 10 != 0:
        return None
    try:
        recent = closed[-20:]
        wins   = [t for t in recent if t["pnl"] > 0]
        losses = [t for t in recent if t["pnl"] <= 0]
        avg_win_conf  = round(sum(t["confidence"] for t in wins)/max(len(wins),1),1)
        avg_loss_conf = round(sum(t["confidence"] for t in losses)/max(len(losses),1),1)
        kelly_vals    = [t.get("kelly_pct",0)*100 for t in recent if t.get("kelly_pct")]
        avg_kelly     = round(sum(kelly_vals)/len(kelly_vals),1) if kelly_vals else 0

        prompt = f"""Analyse {len(recent)} trades simulés et génère 3 règles.
WR: {len(wins)}/{len(recent)} ({len(wins)/len(recent)*100:.0f}%)
Conf gagnants: {avg_win_conf}% | perdants: {avg_loss_conf}%
Kelly moyen utilisé: {avg_kelly}%
SL={STOP_LOSS_PCT*100:.1f}% TP={TAKE_PROFIT_PCT*100:.1f}%

JSON: {{"rules":["règle1","règle2","règle3"],"sl_pct":2.5,"tp_pct":4.0,"insight":"insight"}}"""

        r = ask_model_single(prompt)
        rules = r.get("rules",[])
        for rule in rules:
            try:
                con = sqlite3.connect(DB_FILE)
                con.execute("""INSERT INTO trading_rules
                    (rule,condition,action,win_rate,sample_size,created_date,last_updated)
                    VALUES(?,?,?,?,?,?,?)""", (
                    rule, "auto", "appliquer",
                    len(wins)/len(recent)*100, len(recent),
                    datetime.now().strftime("%Y-%m-%d"),
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                ))
                con.commit(); con.close()
            except Exception:
                pass
        return r
    except Exception as e:
        print(f"[RULES] {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  SURVEILLANCE TRADERS
# ═══════════════════════════════════════════════════════════════
def _signal_hash(content: str) -> str:
    return hashlib.md5(content.encode()).hexdigest()[:12]


def scrape_nitter(username: str) -> list:
    signals = []
    for instance in NITTER_INSTANCES:
        try:
            feed = feedparser.parse(f"https://{instance}/{username}/rss")
            if not feed.entries:
                continue
            for entry in feed.entries[:3]:
                text = re.sub(r'<[^>]+>', '', entry.get("summary","")).strip()
                if len(text) < 20:
                    continue
                h = _signal_hash(text)
                if h in _signal_cache:
                    continue
                _signal_cache.add(h)
                signals.append({
                    "source": "Twitter", "author": username,
                    "content": text[:300], "url": entry.get("link",""),
                    "hash": h, "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
                })
            break
        except Exception:
            continue
    return signals


def scrape_youtube_titles(channel_id: str, channel_name: str) -> list:
    signals = []
    try:
        feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
        for entry in feed.entries[:2]:
            title = entry.get("title","")
            h     = _signal_hash(title)
            if h in _signal_cache or not title:
                continue
            _signal_cache.add(h)
            signals.append({
                "source": "YouTube", "author": channel_name,
                "content": title, "url": entry.get("link",""),
                "hash": h, "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
    except Exception:
        pass
    return signals


def analyze_signal_sentiment(signal: dict) -> dict:
    try:
        prompt = f"""Analyse ce message d'un trader crypto (@{signal['author']}).
"{signal['content']}"
JSON: {{"sentiment":"bullish/bearish/neutral","symbol":"BTC","strength":2,"summary":"résumé"}}"""
        r = ask_model_single(prompt)
        signal.update({
            "sentiment": r.get("sentiment","neutral"),
            "symbol":    r.get("symbol","GENERAL"),
            "strength":  r.get("strength",1),
            "summary":   r.get("summary",""),
        })
    except Exception:
        signal.update({"sentiment":"neutral","symbol":"GENERAL","strength":1})
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
    all_signals = []
    accounts_batch = TRADER_TWITTER_ACCOUNTS[
        bot_state.get("nitter_idx",0)%len(TRADER_TWITTER_ACCOUNTS):
        bot_state.get("nitter_idx",0)%len(TRADER_TWITTER_ACCOUNTS)+2
    ]
    bot_state["nitter_idx"] = bot_state.get("nitter_idx",0)+2
    for account in accounts_batch:
        try:
            all_signals.extend(scrape_nitter(account))
        except Exception:
            pass
    yt_items = list(YOUTUBE_CHANNELS.items())
    yt_idx   = bot_state.get("yt_idx",0) % len(yt_items)
    bot_state["yt_idx"] = yt_idx+1
    ch_name, ch_id = yt_items[yt_idx]
    try:
        all_signals.extend(scrape_youtube_titles(ch_id, ch_name))
    except Exception:
        pass
    analyzed = []
    for s in all_signals[:3]:
        analyzed.append(analyze_signal_sentiment(s))
    bullish = [s for s in analyzed if s["sentiment"]=="bullish"]
    bearish = [s for s in analyzed if s["sentiment"]=="bearish"]
    parts   = []
    for s in analyzed[:3]:
        e = "📈" if s["sentiment"]=="bullish" else "📉" if s["sentiment"]=="bearish" else "➡️"
        parts.append(f"{e} @{s['author']}: {s.get('summary',s['content'][:60])}")
    return {"bullish":bullish,"bearish":bearish,"summary":"\n".join(parts),"count":len(analyzed)}


# ═══════════════════════════════════════════════════════════════
#  BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════════════
def trading_loop(send_fn):
    kelly_init = kelly_criterion()
    send_fn(
        f"🚀 BOT v5 DÉMARRÉ\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Capital    : ${CAPITAL_INITIAL:,.2f} (virtuel)\n"
        f"📊 Kelly init : {kelly_init*100:.1f}% par trade\n"
        f"⚡ Micro SL/TP: -{MICRO_SL_PCT*100:.1f}%/+{MICRO_TP_PCT*100:.1f}%\n"
        f"🔍 Scalp SL/TP: -{STOP_LOSS_PCT*100:.1f}%/+{TAKE_PROFIT_PCT*100:.1f}%\n"
        f"🎯 Polymarket  : scanner actif\n"
        f"🔗 Arbitrage   : Bybit vs Binance vs Kraken\n"
        f"💥 Liquidations: CoinGlass actif\n"
        f"📊 On-chain    : CoinGecko actif\n"
        f"📈 Options     : Deribit P/C ratio actif"
    )

    fear_greed = get_fear_greed()

    while bot_state["running"]:
        now = time.time()

        # ══ 8s : MICRO-TRADING ═══════════════════════════════
        if now - bot_state.get("last_micro",0) >= CYCLE_MICRO:
            try:
                monitor_micro_positions(send_fn)
                run_micro_cycle(send_fn)
            except Exception as e:
                print(f"[MICRO] {e}")
            bot_state["last_micro"] = now

        # ══ 45s : MEMECOINS ══════════════════════════════════
        if now - bot_state.get("last_meme",0) >= CYCLE_MEME:
            try:
                run_meme_cycle(send_fn)
            except Exception as e:
                print(f"[MEME] {e}")
            bot_state["last_meme"] = now

        # ══ 15s : SURVEILLANCE SL/TP ════════════════════════
        if now - bot_state["last_monitor"] >= CYCLE_MONITOR:
            try:
                monitor_positions(send_fn)
            except Exception as e:
                print(f"[MON] {e}")
            bot_state["last_monitor"] = now

        # ══ 5min : SCAN + SCALP ══════════════════════════════
        if now - bot_state["last_scalp"] >= CYCLE_SCALP:
            bot_state["cycle_count"] += 1
            try:
                fear_greed = get_fear_greed()
                threshold  = memory.get("confidence_threshold", CONFIDENCE_BASE)

                # Arbitrage — log si détecté
                arb_opps = detect_arbitrage()
                for opp in arb_opps:
                    if opp["profit_est"] > 0.05:
                        db_save_arb(opp)
                        coin = opp["symbol"].replace("USDT","")
                        send_fn(
                            f"⚡ ARBITRAGE {coin}\n"
                            f"  Bybit: ${opp['bybit']:.2f} vs "
                            f"autre: ${opp.get('binance',opp.get('kraken',0)):.2f}\n"
                            f"  Spread: {opp['spread_pct']:.3f}% | "
                            f"Profit net: ~{opp['profit_est']:.3f}%"
                        )

                # Polymarket — affiche inefficacités
                poly_mkts = get_polymarket_markets()
                if poly_mkts:
                    best = poly_mkts[0]
                    if best["inefficiency"] > 2:
                        send_fn(
                            f"🎯 Polymarket inefficacité\n"
                            f"  {best['question']}\n"
                            f"  YES:{best['yes_price']:.2f} NO:{best['no_price']:.2f} "
                            f"(écart {best['inefficiency']:.1f}%)"
                        )

                opps = scan_market()
                if opps:
                    top_opps = [o for o in opps[:5] if abs(o["score"]) >= 4]
                    if top_opps and _can_call_ai():
                        for opp in top_opps[:2]:
                            if not bot_state["running"]: break
                            if opp["has_alert"]: continue
                            result = analyze(opp, fear_greed)
                            signal = result["signal"]
                            conf   = result["confidence"]
                            risk   = result["risk"]
                            in_pos = any(p["symbol"]==opp["symbol"] for p in sim["positions"].values())
                            if signal == "HOLD" or in_pos: continue
                            if conf >= threshold and risk in ("LOW","MEDIUM"):
                                open_trade(result, send_fn)
                            elif LEARN_MODE_ENABLED and conf >= LEARN_MODE_CONF_MIN:
                                result["_forced_pct"] = LEARN_MODE_MAX_PCT
                                open_trade(result, send_fn)
            except Exception as e:
                print(f"[SCALP] {e}")
            bot_state["last_scalp"] = now

        # ══ 5min : ANALYSE PROFONDE ══════════════════════════
        if now - bot_state["last_deep"] >= CYCLE_DEEP:
            try:
                fear_greed = get_fear_greed()
                thresh     = memory.get("confidence_threshold", CONFIDENCE_BASE)

                # On-chain summary
                onchain = get_onchain_data()
                options = get_options_data()
                liq     = get_liquidations()
                whales  = get_whale_alerts()

                info_lines = [
                    format_onchain(onchain),
                    format_options(options),
                    interpret_liquidations(liq),
                    format_whale_alerts(whales),
                ]
                send_fn("🔬 Analyse profonde\n" + "\n".join(info_lines))

                # Futures sur top tokens
                for symbol in ["BTCUSDT","ETHUSDT","SOLUSDT"]:
                    try:
                        mtf  = get_multi_tf(symbol)
                        conf = tf_score(mtf)
                        if abs(conf["score"]) < 5:
                            continue
                        price = get_price(symbol)
                        ind5m = mtf.get("5m", {})
                        ob    = get_order_book(symbol)
                        in_pos= any(p["symbol"]==symbol for p in sim["positions"].values())
                        if in_pos: continue

                        kelly_pct = dynamic_position_size(70, "FUTURES", symbol)
                        direction = "BUY" if conf["direction"]=="LONG" else "SELL"

                        prompt = f"""{symbol} FUTURES x{LEVERAGE_SIM} ${price:.2f}
TF:{conf['score']}/9→{conf['direction']} RSI:{ind5m.get('rsi','?')}
OB:{ob['pressure']} {fear_greed}
Kelly:{kelly_pct*100:.1f}%
JSON: {{"signal":"{direction}/HOLD","confidence":0-100,"reason":"raison","risk":"LOW/MEDIUM/HIGH","market":"FUTURES"}}"""

                        result = vote(prompt)
                        result.update({
                            "symbol": symbol, "price": price,
                            "patterns": [], "confluence": conf,
                            "ob": ob, "ind": ind5m, "market": "FUTURES",
                            "kelly_pct": kelly_pct,
                        })
                        if (result["signal"] in ("BUY","SELL") and
                                result["confidence"] >= thresh and
                                result["risk"] in ("LOW","MEDIUM")):
                            open_trade(result, send_fn)
                    except Exception as e:
                        print(f"[DEEP] {symbol}: {e}")

                # Collecte traders
                def _collect():
                    get_trader_intelligence()
                threading.Thread(target=_collect, daemon=True).start()

                # Auto-règles
                rules = generate_trading_rules()
                if rules:
                    rule_list = rules.get("rules",[])
                    send_fn(
                        f"🧠 Règles auto ({len(sim['trades'])} trades)\n"
                        + "\n".join(f"• {r}" for r in rule_list[:3])
                    )
            except Exception as e:
                print(f"[DEEP] {e}")
            bot_state["last_deep"] = now

        # ══ 15min : BILAN ════════════════════════════════════
        if now - bot_state["last_status"] >= CYCLE_STATUS:
            try:
                equity = get_equity()
                pnl    = equity - sim["initial"]
                stats  = get_stats()
                kelly  = kelly_criterion()
                sym_s  = db_symbol_stats()
                sym_str= " | ".join(f"{s['s']}:{s['wr']:.0f}%WR" for s in sym_s) or "Aucun"
                micro_c= bot_state.get("micro_count",0)

                pos_lines = ""
                if sim["positions"]:
                    prices = get_prices_batch()
                    for pos in sim["positions"].values():
                        p   = prices.get(pos["symbol"], pos["price_in"])
                        chg = (p-pos["price_in"])/pos["price_in"]*100 * pos.get("leverage",1)
                        pos_lines += f"\n  {'📈' if chg>0 else '📉'} {pos['symbol'].replace('USDT',''):6s} {chg:+.2f}%"

                send_fn(
                    f"📊 BILAN — {datetime.now().strftime('%H:%M')}\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 Capital  : ${equity:.2f} ({pnl/sim['initial']*100:+.1f}%)\n"
                    f"📍 Positions: {len(sim['positions'])}/{MAX_POSITIONS}{pos_lines}\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"🏆 Win Rate : {stats['win_rate']}% ({stats['wins']}✅/{stats['losses']}❌)\n"
                    f"⚡ Micro    : {micro_c} trades\n"
                    f"📊 Kelly    : {kelly*100:.1f}% par trade (auto-ajusté)\n"
                    f"🥇 Top coins: {sym_str}\n"
                    f"📚 Leçons   : {len(memory['lessons'])}"
                )
                db_save_equity(equity, sim["cash"], len(sim["positions"]), pnl)
            except Exception as e:
                print(f"[STATUS] {e}")
            bot_state["last_status"] = now

        bot_state["last_heartbeat"] = datetime.now()
        time.sleep(3)


def db_save_equity(equity, cash, open_pos, daily_pnl):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT INTO equity
            (timestamp,equity,cash,open_positions,daily_pnl)
            VALUES(?,?,?,?,?)""", (
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            round(equity,2), round(cash,2), open_pos, round(daily_pnl,2),
        ))
        con.commit(); con.close()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
#  DASHBOARD HTML
# ═══════════════════════════════════════════════════════════════
def generate_dashboard() -> str:
    stats  = get_stats()
    equity = get_equity()
    pnl    = equity - sim["initial"]
    pct    = pnl/sim["initial"]*100
    status = "🟢 EN MARCHE" if bot_state["running"] else "🔴 ARRÊTÉ"
    kelly  = kelly_criterion()
    wr_db  = db_win_rate(30)
    sym_s  = db_symbol_stats()
    last   = bot_state.get("last_heartbeat")
    hb     = last.strftime("%H:%M:%S") if last else "—"
    thresh = memory.get("confidence_threshold", CONFIDENCE_BASE)

    # Nouvelles données pour le dashboard
    arb_opps  = detect_arbitrage()
    poly_mkts = get_polymarket_markets()
    options   = get_options_data()
    onchain   = get_onchain_data()

    prices = get_prices_batch()
    pos_html = ""
    for pk, pos in sim["positions"].items():
        p     = prices.get(pos["symbol"], pos["price_in"])
        chg   = (p-pos["price_in"])/pos["price_in"]*100 * pos.get("leverage",1)
        color = "#2ecc71" if chg>=0 else "#e74c3c"
        kelly_used = pos.get("kelly_pct",0)*100
        pos_html += (
            f"<tr><td>{pos['symbol'].replace('USDT','')}</td>"
            f"<td>{pos['market']}</td><td>{pos['side']}</td>"
            f"<td>${pos['price_in']:.4f}</td><td>${p:.4f}</td>"
            f'<td style="color:{color}">{chg:+.2f}%</td>'
            f"<td>{kelly_used:.1f}%</td></tr>"
        )

    trades_html = ""
    for t in reversed(sim["trades"][-20:]):
        if t.get("pnl") is not None:
            c  = "#2ecc71" if t["pnl"]>0 else "#e74c3c"
            ps = f'<span style="color:{c}">${t["pnl"]:+.4f}</span>'
        else:
            ps = '<span style="color:#f39c12">ouvert</span>'
        trades_html += (
            f"<tr><td>{t['id']}</td>"
            f"<td>{t['symbol'].replace('USDT','')}</td>"
            f"<td>{t['market']}</td><td>{t['side']}</td>"
            f"<td>${t['price_in']:.4f}</td>"
            f"<td>{ps}</td><td>{t['confidence']}%</td>"
            f"<td>{t.get('kelly_pct',0)*100:.1f}%</td>"
            f"<td>{t['time_in'][11:16]}</td></tr>"
        )

    lessons_html = ""
    for l in reversed(memory["lessons"][-10:]):
        c = "#2ecc71" if l["type"]=="succes" else "#e74c3c"
        lessons_html += (
            f'<tr><td style="color:{c}">{"✅" if l["type"]=="succes" else "❌"}</td>'
            f'<td style="color:{c}">${l.get("pnl",0):+.4f}</td>'
            f"<td>{l['lecon'][:55]}</td>"
            f"<td>{l['action_future'][:50]}</td>"
            f"<td>{l['date']}</td></tr>"
        )

    arb_html = ""
    for o in arb_opps[:3]:
        coin = o["symbol"].replace("USDT","")
        color = "#2ecc71" if o["profit_est"] > 0 else "#e74c3c"
        arb_html += (
            f"<tr><td>{coin}</td>"
            f"<td>${o['bybit']:.2f}</td>"
            f"<td>${o.get('binance',o.get('kraken',0)):.2f}</td>"
            f"<td>{o['spread_pct']:.3f}%</td>"
            f'<td style="color:{color}">{o["profit_est"]:.3f}%</td></tr>'
        )

    poly_html = ""
    for m in poly_mkts[:3]:
        color = "#2ecc71" if m["inefficiency"] > 2 else "#f39c12"
        poly_html += (
            f"<tr><td>{m['question'][:50]}</td>"
            f"<td>{m['yes_price']:.2f}</td>"
            f"<td>{m['no_price']:.2f}</td>"
            f'<td style="color:{color}">{m["inefficiency"]:.1f}%</td></tr>'
        )

    pcr     = options.get("put_call_ratio","N/A")
    opt_sent= options.get("sentiment","N/A")
    btc_dom = onchain.get("btc_dominance","N/A")
    mcap_chg= onchain.get("mcap_change_24h",0)
    sym_str = "".join(
        f"<span style='background:#161b22;border-radius:5px;padding:2px 7px;"
        f"font-size:.75em;margin:2px;display:inline-block;color:#2ecc71'>"
        f"{s['s']} WR:{s['wr']:.0f}%</span>"
        for s in sym_s
    ) or "<span style='color:#8b949e'>Aucun</span>"

    return f"""<!DOCTYPE html><html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Bot v5</title>
<style>
body{{font-family:Arial,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:14px}}
h1{{color:#58a6ff;text-align:center;font-size:1.2em;margin-bottom:2px}}
h2{{color:#58a6ff;font-size:.85em;margin:10px 0 4px}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:10px}}
.grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px}}
.card{{background:#161b22;border-radius:8px;padding:10px;text-align:center}}
.label{{font-size:.68em;color:#8b949e;margin-bottom:2px}}
.value{{font-size:1.05em;font-weight:bold}}
.green{{color:#2ecc71}}.red{{color:#e74c3c}}.blue{{color:#58a6ff}}.yellow{{color:#f39c12}}
.center{{text-align:center;font-size:.78em;color:#8b949e;margin:2px 0}}
table{{width:100%;border-collapse:collapse;font-size:.7em;margin-bottom:12px}}
th{{background:#21262d;padding:5px;text-align:left;color:#8b949e}}
td{{padding:4px 5px;border-bottom:1px solid #21262d}}
.cmds{{background:#161b22;border-radius:8px;padding:10px;margin-bottom:10px;font-size:.78em;line-height:1.8}}
code{{color:#58a6ff}}
</style>
<meta http-equiv="refresh" content="20">
</head><body>
<h1>🤖 Trading Bot v5 — Kelly + Arbitrage + Polymarket</h1>
<div class="center">{status} | {hb} | Cycle #{bot_state['cycle_count']} | Kelly:{kelly*100:.1f}%</div>
<div class="center">{sym_str}</div>

<div class="grid">
  <div class="card"><div class="label">Capital simulé</div>
    <div class="value blue">${equity:.2f}</div></div>
  <div class="card"><div class="label">PnL simulation</div>
    <div class="value {'green' if pnl>=0 else 'red'}">${pnl:+.2f} ({pct:+.1f}%)</div></div>
  <div class="card"><div class="label">Win Rate</div>
    <div class="value yellow">{stats['win_rate']}%</div></div>
  <div class="card"><div class="label">Trades | Leçons</div>
    <div class="value">{stats['total']} | {len(memory['lessons'])}</div></div>
  <div class="card"><div class="label">Kelly actuel</div>
    <div class="value blue">{kelly*100:.1f}% / trade</div></div>
  <div class="card"><div class="label">Options P/C | Sentiment</div>
    <div class="value yellow">{pcr} | {opt_sent}</div></div>
</div>

<div class="grid3">
  <div class="card"><div class="label">BTC Dominance</div>
    <div class="value">{btc_dom}%</div></div>
  <div class="card"><div class="label">MCap 24h</div>
    <div class="value {'green' if mcap_chg>=0 else 'red'}">{mcap_chg:+.1f}%</div></div>
  <div class="card"><div class="label">Seuil IA</div>
    <div class="value">{thresh}%</div></div>
</div>

<div class="cmds">
<b>Commandes Telegram:</b> 
<code>/start</code> <code>/stop</code> <code>/status</code> 
<code>/portfolio</code> <code>/positions</code> <code>/lecons</code>
<code>/scan</code> <code>/arbitrage</code> <code>/polymarket</code>
<code>/kelly</code> <code>/fermer</code> <code>/reset</code> <code>/help</code>
</div>

<h2>Positions Ouvertes</h2>
<table><thead><tr>
  <th>Coin</th><th>Marché</th><th>Sens</th>
  <th>Entrée</th><th>Actuel</th><th>PnL%</th><th>Kelly%</th>
</tr></thead><tbody>
{pos_html or '<tr><td colspan="7" style="text-align:center;color:#8b949e">Aucune position</td></tr>'}
</tbody></table>

<h2>Opportunités Arbitrage</h2>
<table><thead><tr>
  <th>Coin</th><th>Bybit</th><th>Autre</th><th>Spread</th><th>Profit net</th>
</tr></thead><tbody>
{arb_html or '<tr><td colspan="5" style="text-align:center;color:#8b949e">Aucun arbitrage détecté</td></tr>'}
</tbody></table>

<h2>Polymarket — Inefficacités</h2>
<table><thead><tr>
  <th>Question</th><th>YES</th><th>NO</th><th>Inefficacité</th>
</tr></thead><tbody>
{poly_html or '<tr><td colspan="4" style="text-align:center;color:#8b949e">Aucune inefficacité</td></tr>'}
</tbody></table>

<h2>Historique Trades</h2>
<table><thead><tr>
  <th>#</th><th>Coin</th><th>Mkt</th><th>Sens</th>
  <th>Entrée</th><th>PnL</th><th>Conf</th><th>Kelly%</th><th>Heure</th>
</tr></thead><tbody>
{trades_html or '<tr><td colspan="9" style="text-align:center;color:#8b949e">Aucun trade</td></tr>'}
</tbody></table>

<h2>Mémoire & Apprentissage</h2>
<table><thead><tr>
  <th>Type</th><th>PnL</th><th>Leçon</th><th>Action Future</th><th>Date</th>
</tr></thead><tbody>
{lessons_html or '<tr><td colspan="5" style="text-align:center;color:#8b949e">Aucune leçon</td></tr>'}
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
#  TELEGRAM
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


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    if bot_state["running"]:
        await update.message.reply_text("Simulation déjà en cours !"); return
    bot_state.update({
        "running": True, "trades_today": 0, "cycle_count": 0,
        "last_heartbeat": None, "last_monitor": 0, "last_micro": 0,
        "last_scalp": 0, "last_deep": 0, "last_status": 0, "last_meme": 0,
    })
    send = make_send(TELEGRAM_CHAT_ID)
    threading.Thread(target=trading_loop, args=(send,), daemon=True).start()


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    bot_state["running"] = False
    equity = get_equity()
    stats  = get_stats()
    await update.message.reply_text(
        f"🛑 Arrêté.\n"
        f"Capital: ${equity:.2f} | PnL: ${equity-sim['initial']:+.2f}\n"
        f"Trades: {stats['total']} | WR: {stats['win_rate']}%"
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    equity = get_equity()
    pnl    = equity - sim["initial"]
    stats  = get_stats()
    kelly  = kelly_criterion()
    thresh = memory.get("confidence_threshold", CONFIDENCE_BASE)
    last   = bot_state.get("last_heartbeat")
    arb    = detect_arbitrage()
    options= get_options_data()

    await update.message.reply_text(
        f"{'🟢' if bot_state['running'] else '🔴'} "
        f"{'EN MARCHE' if bot_state['running'] else 'ARRÊTÉ'}\n"
        f"━━━━━━━━━━━━━\n"
        f"💰 Capital: ${equity:.2f} ({pnl:+.2f})\n"
        f"📊 Kelly  : {kelly*100:.1f}% par trade\n"
        f"🏆 WR     : {stats['win_rate']}% ({stats['total']} trades)\n"
        f"⚙️  Seuil  : {thresh}%\n"
        f"━━━━━━━━━━━━━\n"
        f"⚡ Arbitrage: {len(arb)} opportunité(s)\n"
        f"📊 Options P/C: {options.get('put_call_ratio','N/A')} ({options.get('sentiment','N/A')})\n"
        f"📚 Leçons: {len(memory['lessons'])}"
    )


async def cmd_kelly(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    kelly  = kelly_criterion(30)
    kelly10= kelly_criterion(10)
    kelly50= kelly_criterion(50)
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    wins   = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    await update.message.reply_text(
        f"📐 KELLY CRITERION\n"
        f"━━━━━━━━━━━━━\n"
        f"Kelly (10 trades) : {kelly10*100:.1f}%\n"
        f"Kelly (30 trades) : {kelly*100:.1f}% ← utilisé\n"
        f"Kelly (50 trades) : {kelly50*100:.1f}%\n"
        f"━━━━━━━━━━━━━\n"
        f"Trades analysés   : {len(closed)}\n"
        f"Wins: {len(wins)} | Losses: {len(losses)}\n"
        f"Win Rate          : {len(wins)/max(len(closed),1)*100:.1f}%\n"
        f"━━━━━━━━━━━━━\n"
        f"Le Kelly s'ajuste automatiquement à chaque trade."
    )


async def cmd_arbitrage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    await update.message.reply_text("🔍 Scan arbitrage en cours...")
    opps = detect_arbitrage()
    if not opps:
        await update.message.reply_text("Aucune opportunité d'arbitrage détectée."); return
    lines = ["⚡ ARBITRAGE DÉTECTÉ\n━━━━━━━━━━━━━"]
    for o in opps:
        coin = o["symbol"].replace("USDT","")
        ex2  = "Binance" if "binance" in o else "Kraken"
        lines.append(
            f"💰 {coin}\n"
            f"  Bybit: ${o['bybit']:.2f} | {ex2}: ${o.get('binance',o.get('kraken',0)):.2f}\n"
            f"  Spread: {o['spread_pct']:.3f}% | Profit net: ~{o['profit_est']:.3f}%\n"
            f"  Direction: {o['direction']}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_polymarket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    await update.message.reply_text("🎯 Scan Polymarket en cours...")
    markets = get_polymarket_markets()
    if not markets:
        await update.message.reply_text("Aucune inefficacité Polymarket détectée."); return
    lines = ["🎯 POLYMARKET — Inefficacités\n━━━━━━━━━━━━━"]
    for m in markets:
        lines.append(
            f"❓ {m['question']}\n"
            f"  YES:{m['yes_price']:.2f} NO:{m['no_price']:.2f}\n"
            f"  Inefficacité: {m['inefficiency']:.1f}%"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    equity = get_equity()
    pnl    = equity - sim["initial"]
    stats  = get_stats()
    kelly  = kelly_criterion()
    sym_s  = db_symbol_stats()
    sym_str= " | ".join(f"{s['s']}:{s['wr']:.0f}%WR" for s in sym_s) or "Aucun"
    await update.message.reply_text(
        f"💼 Portefeuille\n"
        f"Initial : ${sim['initial']:,.2f}\n"
        f"Actuel  : ${equity:.2f} ({pnl:+.2f})\n"
        f"Cash    : ${sim['cash']:.2f}\n"
        f"━━━━━━━━━━━━━\n"
        f"Kelly   : {kelly*100:.1f}% par trade\n"
        f"Trades  : {stats['total']} ({stats['wins']}W/{stats['losses']}L)\n"
        f"Win Rate: {stats['win_rate']}% | Durée moy: {stats['avg_dur']}min\n"
        f"Meilleur: +${stats['best']} | Pire: ${stats['worst']}\n"
        f"Top coins: {sym_str}"
    )


async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    if not sim["positions"]:
        await update.message.reply_text("Aucune position ouverte."); return
    prices = get_prices_batch()
    lines  = ["📍 Positions ouvertes\n━━━━━━━━━━━━━"]
    for pk, pos in sim["positions"].items():
        p    = prices.get(pos["symbol"], pos["price_in"])
        chg  = (p-pos["price_in"])/pos["price_in"]*100 * pos.get("leverage",1)
        e    = "📈" if chg>0 else "📉"
        kelly_used = pos.get("kelly_pct",0)*100
        lines.append(
            f"{e} {pos['symbol'].replace('USDT','')} {pos['market']} {pos['side']}\n"
            f"  ${pos['price_in']:.4f}→${p:.4f} ({chg:+.2f}%)\n"
            f"  Kelly utilisé: {kelly_used:.1f}%"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_lecons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    if not memory["lessons"]:
        await update.message.reply_text("Aucune leçon encore."); return
    msg = f"📚 Leçons ({len(memory['lessons'])}):\n\n"
    for l in memory["lessons"][-5:]:
        e = "✅" if l["type"]=="succes" else "❌"
        msg += f"{e} ${l.get('pnl',0):+.4f}\n{l['lecon']}\n→ {l['action_future']}\n\n"
    await update.message.reply_text(msg)


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    await update.message.reply_text("🔍 Scan en cours...")
    opps  = scan_market()
    lines = ["🎯 Top opportunités\n━━━━━━━━━━━━━"]
    for o in opps[:7]:
        e     = "🟢" if o["direction"]=="BUY" else "🔴"
        alert = " ⚠️" if o["has_alert"] else ""
        lines.append(
            f"{e}{alert} {o['symbol'].replace('USDT',''):6s} "
            f"score={o['score']:+d} RSI={o['ind'].get('rsi',0):.0f} "
            f"mom={o['ind'].get('mom5',0):+.1f}%"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_fermer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    if not sim["positions"]:
        await update.message.reply_text("Aucune position à fermer."); return
    send   = make_send(TELEGRAM_CHAT_ID)
    prices = get_prices_batch()
    count  = 0
    for pk in list(sim["positions"].keys()):
        pos   = sim["positions"].get(pk)
        if not pos: continue
        price = prices.get(pos["symbol"], pos["price_in"])
        close_trade(pk, price, "Fermeture manuelle", send)
        count += 1
    await update.message.reply_text(f"✅ {count} position(s) fermée(s).")


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    bot_state["running"] = False
    lessons_saved = memory.get("lessons",[])
    patterns_work = memory.get("patterns_that_work",[])
    patterns_avoid= memory.get("patterns_to_avoid",[])
    threshold     = memory.get("confidence_threshold", CONFIDENCE_BASE)
    sim.update({
        "cash": CAPITAL_INITIAL, "initial": CAPITAL_INITIAL,
        "positions": {}, "trades": [], "equity_history": [],
        "session": sim.get("session",0)+1,
    })
    memory.update({
        "lessons": lessons_saved,
        "patterns_to_avoid": patterns_avoid,
        "patterns_that_work": patterns_work,
        "confidence_threshold": threshold,
        "total_wins": 0, "total_losses": 0,
    })
    save_data()
    kelly = kelly_criterion()
    await update.message.reply_text(
        f"🔄 Session #{sim['session']} — ${CAPITAL_INITIAL:,.2f}\n"
        f"📚 Leçons conservées : {len(lessons_saved)}\n"
        f"📐 Kelly initial      : {kelly*100:.1f}%"
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    kelly = kelly_criterion()
    await update.message.reply_text(
        f"🤖 Trading Bot v5\n"
        f"━━━━━━━━━━━━━\n"
        f"▶️  /start       — Démarrer\n"
        f"⏹  /stop        — Arrêter\n"
        f"🔄 /reset       — Nouveau capital\n"
        f"━━━━━━━━━━━━━\n"
        f"📊 /status      — État rapide\n"
        f"💼 /portfolio   — Capital & stats\n"
        f"📍 /positions   — Trades en cours\n"
        f"📚 /lecons      — Apprentissage\n"
        f"🔍 /scan        — Opportunités\n"
        f"━━━━━━━━━━━━━\n"
        f"⚡ /arbitrage   — Opportunités arb\n"
        f"🎯 /polymarket  — Inefficacités\n"
        f"📐 /kelly       — Kelly Criterion\n"
        f"⚡ /fermer      — Ferme tout\n"
        f"━━━━━━━━━━━━━\n"
        f"⚙️  SL:{STOP_LOSS_PCT*100:.1f}% TP:{TAKE_PROFIT_PCT*100:.1f}%\n"
        f"📐 Kelly actuel: {kelly*100:.1f}%"
    )


# ═══════════════════════════════════════════════════════════════
#  AUTO-START + WATCHDOG
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
            send_fn(f"⚠️ WATCHDOG: Inactif {int(elapsed//60)} min")
            alerted = True
        elif elapsed <= 300:
            alerted = False


def self_ping():
    time.sleep(60)
    while True:
        try:
            requests.get("https://junior-tick-1ever-6bf9cee7.koyeb.app/health", timeout=10)
        except Exception:
            pass
        time.sleep(270)


def auto_start():
    time.sleep(5)
    send = make_send(TELEGRAM_CHAT_ID)
    if bot_state["running"]:
        return
    bot_state.update({
        "running": True, "trades_today": 0, "cycle_count": 0,
        "last_heartbeat": None, "last_monitor": 0, "last_micro": 0,
        "last_scalp": 0, "last_deep": 0, "last_status": 0, "last_meme": 0,
    })
    kelly = kelly_criterion()
    send(
        f"🔄 Bot v5 redémarré automatiquement\n"
        f"Kelly initial: {kelly*100:.1f}%\n"
        f"/stop pour arrêter"
    )
    threading.Thread(target=trading_loop, args=(send,), daemon=True).start()
    threading.Thread(target=watchdog,     args=(send,), daemon=True).start()


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
        ("start",      cmd_start),
        ("stop",       cmd_stop),
        ("status",     cmd_status),
        ("scan",       cmd_scan),
        ("portfolio",  cmd_portfolio),
        ("positions",  cmd_positions),
        ("lecons",     cmd_lecons),
        ("fermer",     cmd_fermer),
        ("reset",      cmd_reset),
        ("kelly",      cmd_kelly),
        ("arbitrage",  cmd_arbitrage),
        ("polymarket", cmd_polymarket),
        ("help",       cmd_help),
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
        print("⚠️ WEBHOOK_URL non définie")

    print("Bot v5 prêt")
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
    print("🚀 Trading Bot v5")
    init_db()
    load_data()
    threading.Thread(target=run_server, daemon=True).start()
    threading.Thread(target=self_ping,  daemon=True).start()
    threading.Thread(target=auto_start, daemon=True).start()
    print("Serveur HTTP port 8000")
    asyncio.run(run_telegram())
