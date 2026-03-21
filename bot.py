import os
import time
import threading
import feedparser
import requests
import asyncio
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from groq import Groq
from pybit.unified_trading import HTTP
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)
from telegram.request import HTTPXRequest

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
GROQ_KEY          = os.environ.get("ANTHROPIC_KEY")
BINANCE_KEY       = os.environ.get("BINANCE_KEY")
BINANCE_SECRET    = os.environ.get("BINANCE_SECRET")
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID")
# URL publique de ton service Koyeb, ex: https://mon-bot-abc123.koyeb.app
# Ajoute cette variable d'environnement dans Koyeb → Settings → Environment
WEBHOOK_URL       = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_PATH      = "/webhook"
WEBHOOK_PORT      = 8000

# Seuils calibrés pour l'apprentissage :
# confidence >= 68 pour explorer plus de trades et accumuler des leçons rapidement
# stop-loss à -4% pour limiter les pertes unitaires, take-profit à +6%
CONFIDENCE_THRESHOLD = 68
STOP_LOSS_PCT        = 0.04   # -4%
TAKE_PROFIT_PCT      = 0.06   # +6%
WATCHDOG_TIMEOUT     = 900    # 15 min sans activité → alerte
DATA_FILE            = Path("trading_data.json")

# Symboles suivis
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# ─────────────────────────────────────────────
#  CLIENTS
# ─────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_KEY)
bybit = HTTP(api_key=BINANCE_KEY, api_secret=BINANCE_SECRET)

# ─────────────────────────────────────────────
#  ETAT GLOBAL
# ─────────────────────────────────────────────
DEFAULT_PORTFOLIO = {
    "cash":      1000.0,
    "initial":   1000.0,
    "positions": {},   # symbol -> {qty, amount_usd, price_in, trade_ref}
    "trades":    [],
}

DEFAULT_MEMORY = {
    "lessons":            [],
    "patterns_to_avoid":  [],
    "patterns_that_work": [],
    "analysis_history":   [],
}

portfolio: dict = {}
memory: dict    = {}

bot_state = {
    "running":        False,
    "thread":         None,
    "last_heartbeat": None,
}

# event loop asyncio principal + app telegram (initialisés dans run_telegram)
_main_loop = None
_app       = None

# ─────────────────────────────────────────────
#  PERSISTANCE
# ─────────────────────────────────────────────
def save_data():
    try:
        DATA_FILE.write_text(
            json.dumps({"portfolio": portfolio, "memory": memory}, indent=2, default=str)
        )
    except Exception as e:
        print(f"[SAVE] Erreur: {e}")


def load_data():
    global portfolio, memory
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text())
            portfolio = data.get("portfolio", {})
            memory    = data.get("memory",    {})
            for k, v in DEFAULT_PORTFOLIO.items():
                portfolio.setdefault(k, v)
            for k, v in DEFAULT_MEMORY.items():
                memory.setdefault(k, v)
            print(f"[LOAD] {len(portfolio['trades'])} trades | {len(memory['lessons'])} leçons")
            return
        except Exception as e:
            print(f"[LOAD] Erreur: {e} — initialisation par défaut")
    portfolio = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in DEFAULT_PORTFOLIO.items()}
    memory    = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in DEFAULT_MEMORY.items()}
    print("[LOAD] Nouveau portefeuille $1000")


# ─────────────────────────────────────────────
#  FLUX RSS / REDDIT / TRENDS
# ─────────────────────────────────────────────
RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
    "https://bitcoinmagazine.com/feed",
    "https://cryptonews.com/news/feed/",
    "https://cryptopotato.com/feed/",
    "https://ambcrypto.com/feed/",
]

REDDIT_FEEDS = [
    "https://www.reddit.com/r/Bitcoin/top/.rss?t=hour",
    "https://www.reddit.com/r/CryptoCurrency/top/.rss?t=hour",
]


def get_news():
    news = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:2]:
                news.append(f"[NEWS] {entry.title}: {entry.get('summary', '')[:150]}")
        except Exception:
            pass
    return news


def get_reddit():
    posts = []
    for url in REDDIT_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:2]:
                posts.append(f"[REDDIT] {entry.title}")
        except Exception:
            pass
    return posts


def get_google_trends():
    try:
        feed = feedparser.parse("https://trends.google.com/trending/rss?geo=US")
        trends = []
        for entry in feed.entries[:5]:
            if any(k in entry.title.lower() for k in ["bitcoin", "crypto", "btc", "eth", "solana"]):
                trends.append(f"[TREND] {entry.title}")
        return trends
    except Exception:
        return []


def get_fear_greed():
    try:
        r   = requests.get("https://api.alternative.me/fng/", timeout=5)
        d   = r.json()["data"][0]
        return f"Fear & Greed Index: {d['value']}/100 ({d['value_classification']})"
    except Exception:
        return "Fear & Greed: indisponible"


# ─────────────────────────────────────────────
#  INDICATEURS TECHNIQUES
# ─────────────────────────────────────────────
def get_technical_indicators(symbol: str) -> dict:
    """RSI(14), EMA20/50, MACD, Bollinger Bands sur bougies 15min."""
    try:
        raw    = bybit.get_kline(category="spot", symbol=symbol, interval="15", limit=200)
        closes = pd.Series(
            [float(c[4]) for c in reversed(raw["result"]["list"])], dtype=float
        )

        # RSI 14
        delta = closes.diff()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)
        rs    = gain.ewm(com=13, adjust=False).mean() / loss.ewm(com=13, adjust=False).mean().replace(0, np.nan)
        rsi   = float((100 - 100 / (1 + rs)).iloc[-1])

        # EMA
        ema20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])

        # MACD
        macd_line   = float((closes.ewm(span=12, adjust=False).mean() - closes.ewm(span=26, adjust=False).mean()).iloc[-1])
        signal_line = float((closes.ewm(span=12, adjust=False).mean() - closes.ewm(span=26, adjust=False).mean())
                            .ewm(span=9, adjust=False).mean().iloc[-1])

        # Bollinger Bands
        sma20  = closes.rolling(20).mean()
        std20  = closes.rolling(20).std()
        bb_up  = float((sma20 + 2 * std20).iloc[-1])
        bb_low = float((sma20 - 2 * std20).iloc[-1])

        return {
            "rsi":         round(rsi, 1),
            "ema20":       round(ema20, 2),
            "ema50":       round(ema50, 2),
            "macd":        round(macd_line, 2),
            "macd_signal": round(signal_line, 2),
            "macd_hist":   round(macd_line - signal_line, 2),
            "bb_upper":    round(bb_up, 2),
            "bb_lower":    round(bb_low, 2),
            "price":       round(float(closes.iloc[-1]), 2),
            "trend":       "haussier" if ema20 > ema50 else "baissier",
        }
    except Exception as e:
        print(f"[TECH] Erreur {symbol}: {e}")
        return {}


# ─────────────────────────────────────────────
#  PRIX & PORTFOLIO
# ─────────────────────────────────────────────
def get_price(symbol="BTCUSDT") -> float:
    return float(bybit.get_tickers(category="spot", symbol=symbol)["result"]["list"][0]["lastPrice"])


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
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "best": 0, "worst": 0, "total_pnl": 0}
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


# ─────────────────────────────────────────────
#  TRADING
# ─────────────────────────────────────────────
def execute_buy(symbol: str, price: float, reason: str, confidence: int) -> dict | None:
    if portfolio["cash"] < 10 or symbol in portfolio["positions"]:
        return None
    # Max 40% du cash par position pour diversifier
    amount_usd = portfolio["cash"] * min(0.40, 1.0 / len(SYMBOLS))
    qty        = amount_usd / price
    portfolio["cash"] -= amount_usd

    trade = {
        "id":           len(portfolio["trades"]) + 1,
        "symbol":       symbol,
        "type":         "BUY",
        "price_in":     price,
        "price_out":    None,
        "qty":          qty,
        "amount_usd":   amount_usd,
        "reason":       reason,
        "confidence":   confidence,
        "time_in":      datetime.now().strftime("%Y-%m-%d %H:%M"),
        "time_out":     None,
        "pnl":          None,
        "exit_reason":  None,
        "market_context": memory["analysis_history"][-1] if memory["analysis_history"] else {},
    }
    portfolio["trades"].append(trade)
    portfolio["positions"][symbol] = {
        "qty":       qty,
        "amount_usd": amount_usd,
        "price_in":  price,
        "trade_ref": trade["id"],
    }
    save_data()
    return trade


def execute_sell(symbol: str, price: float, reason: str, confidence: int) -> dict | None:
    pos = portfolio["positions"].get(symbol)
    if not pos:
        return None
    amount_out = pos["qty"] * price
    portfolio["cash"] += amount_out
    del portfolio["positions"][symbol]

    trade = next((t for t in reversed(portfolio["trades"]) if t["id"] == pos["trade_ref"]), None)
    if trade:
        trade["price_out"]  = price
        trade["time_out"]   = datetime.now().strftime("%Y-%m-%d %H:%M")
        trade["pnl"]        = round(amount_out - pos["amount_usd"], 2)
        trade["exit_reason"] = reason
        learn_from_trade(trade)
    save_data()
    return trade


# ─────────────────────────────────────────────
#  STOP-LOSS / TAKE-PROFIT (thread séparé)
# ─────────────────────────────────────────────
def stoploss_watchdog(send_fn_sync):
    while True:
        time.sleep(60)
        if not portfolio.get("positions"):
            continue
        try:
            prices = get_all_prices()
            for symbol, pos in list(portfolio["positions"].items()):
                price    = prices.get(symbol)
                if not price:
                    continue
                change   = (price - pos["price_in"]) / pos["price_in"]
                reason   = None
                if change <= -STOP_LOSS_PCT:
                    reason = f"STOP-LOSS ({change*100:.1f}%)"
                elif change >= TAKE_PROFIT_PCT:
                    reason = f"TAKE-PROFIT ({change*100:.1f}%)"
                if reason:
                    trade = execute_sell(symbol, price, reason, 100)
                    if trade:
                        emoji = "🛑" if "STOP" in reason else "🎯"
                        send_fn_sync(
                            f"{emoji} {reason} — {symbol}\n"
                            f"Entrée: ${pos['price_in']:,.2f} → Sortie: ${price:,.2f}\n"
                            f"PnL: ${trade['pnl']:+.2f}"
                        )
        except Exception as e:
            print(f"[SL/TP] Erreur: {e}")


# ─────────────────────────────────────────────
#  WATCHDOG BOT (détecte boucle morte)
# ─────────────────────────────────────────────
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
                f"Dernière activité: {last.strftime('%H:%M:%S')}\n"
                f"Utilisez /status pour vérifier."
            )
            alerted = True
        elif elapsed <= WATCHDOG_TIMEOUT:
            alerted = False


# ─────────────────────────────────────────────
#  RÉSUMÉ JOURNALIER (minuit)
# ─────────────────────────────────────────────
def daily_summary_scheduler(send_fn_sync):
    while True:
        now      = datetime.now()
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        time.sleep((midnight - now).total_seconds())
        try:
            prices    = get_all_prices()
            pv        = get_portfolio_value(prices)
            pnl       = pv - portfolio["initial"]
            pct       = pnl / portfolio["initial"] * 100
            stats     = get_stats()
            today     = datetime.now().strftime("%Y-%m-%d")
            today_t   = [t for t in portfolio["trades"] if t.get("time_in", "").startswith(today)]
            today_pnl = sum(t["pnl"] for t in today_t if t.get("pnl") is not None)
            lessons   = "".join(
                f"  {'❌' if l['type']=='erreur' else '✅'} {l['lecon']}\n"
                for l in memory["lessons"][-3:]
            ) or "  Aucune"
            send_fn_sync(
                f"📊 RÉSUMÉ JOURNALIER — {datetime.now().strftime('%d/%m/%Y')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Portefeuille: ${pv:,.2f} ({pct:+.1f}%)\n"
                f"PnL total: ${pnl:+.2f}\n"
                f"PnL du jour: ${today_pnl:+.2f}\n"
                f"Trades aujourd'hui: {len(today_t)}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Win Rate: {stats['win_rate']}% ({stats['total']} trades)\n"
                f"Leçons apprises: {len(memory['lessons'])}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Dernières leçons:\n{lessons}"
            )
        except Exception as e:
            print(f"[DAILY] Erreur: {e}")


# ─────────────────────────────────────────────
#  APPRENTISSAGE
# ─────────────────────────────────────────────
def learn_from_trade(trade: dict):
    if trade["pnl"] is None:
        return
    try:
        context = json.dumps(trade.get("market_context", {}))[:500]
        verdict = "TRADE PERDANT. Explique pourquoi et comment éviter ça." if trade["pnl"] < 0 \
                  else "TRADE GAGNANT. Explique pourquoi ça a marché."
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=400,
            messages=[{"role": "user", "content": f"""Tu es un expert en trading crypto. Analyse ce trade:
Symbol: {trade.get('symbol', 'BTC')}
Prix entrée: ${trade['price_in']:,.2f} | Prix sortie: ${trade['price_out']:,.2f}
PnL: ${trade['pnl']:+.2f} | Confiance: {trade['confidence']}%
Raison entrée: {trade['reason']}
Raison sortie: {trade.get('exit_reason', 'signal SELL')}
Contexte: {context}

{verdict}

Réponds en JSON strict (sans backticks):
{{"lecon": "leçon courte", "pattern": "pattern identifié", "action_future": "quoi faire différemment", "type": "erreur ou succes"}}"""}],
        )
        text   = response.choices[0].message.content
        lesson = json.loads(text.replace("```json", "").replace("```", "").strip())
        lesson.update({"trade_id": trade["id"], "pnl": trade["pnl"],
                       "symbol": trade.get("symbol", "BTC"),
                       "date": datetime.now().strftime("%Y-%m-%d %H:%M")})
        memory["lessons"].append(lesson)
        if lesson["type"] == "erreur":
            memory["patterns_to_avoid"].append(lesson["pattern"])
        else:
            memory["patterns_that_work"].append(lesson["pattern"])
        memory["lessons"]            = memory["lessons"][-50:]
        memory["patterns_to_avoid"]  = memory["patterns_to_avoid"][-20:]
        memory["patterns_that_work"] = memory["patterns_that_work"][-20:]
        print(f"[LEARN] {lesson['lecon']}")
        save_data()
    except Exception as e:
        print(f"[LEARN] Erreur: {e}")


# ─────────────────────────────────────────────
#  ANALYSE DE MARCHÉ (multi-crypto)
# ─────────────────────────────────────────────
def analyze_symbol(symbol: str, news_text: str, fear_greed: str) -> tuple:
    tech     = get_technical_indicators(symbol)
    price    = tech.get("price") or get_price(symbol)
    in_pos   = symbol in portfolio["positions"]

    patterns_to_avoid  = "\n".join(memory["patterns_to_avoid"][-5:])  or "Aucun"
    patterns_that_work = "\n".join(memory["patterns_that_work"][-5:]) or "Aucun"
    recent_lessons     = "".join(f"- {l['lecon']} → {l['action_future']}\n" for l in memory["lessons"][-3:]) \
                         or "Aucune leçon encore"

    tech_summary = ""
    if tech:
        tech_summary = (
            f"RSI(14): {tech.get('rsi')} ({'suracheté' if tech.get('rsi', 50) > 70 else 'survendu' if tech.get('rsi', 50) < 30 else 'neutre'})\n"
            f"EMA20: {tech.get('ema20')} | EMA50: {tech.get('ema50')} | Tendance: {tech.get('trend')}\n"
            f"MACD: {tech.get('macd')} (signal: {tech.get('macd_signal')}, hist: {tech.get('macd_hist')})\n"
            f"Bollinger: [{tech.get('bb_lower')} — {tech.get('bb_upper')}]"
        )

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=400,
            messages=[{"role": "user", "content": f"""Tu es un expert en trading crypto court terme.

SYMBOLE: {symbol} | Prix: ${price:,.2f}
{fear_greed}
Position: {'EN POSITION' if in_pos else 'PAS EN POSITION'}

INDICATEURS TECHNIQUES:
{tech_summary or 'Indisponibles'}

SIGNAUX MARCHÉ:
{news_text}

CE QUI A MARCHÉ: {patterns_that_work}
ERREURS À ÉVITER: {patterns_to_avoid}
LEÇONS: {recent_lessons}

Règles techniques:
- RSI > 70 et en position → fort signal SELL
- RSI < 30 et pas en position → fort signal BUY
- EMA20 > EMA50 et MACD haussier → favorable au BUY
- Utilise les leçons pour affiner la décision

Réponds UNIQUEMENT en JSON strict (sans backticks):
{{"signal": "BUY ou SELL ou HOLD", "confidence": 75, "reason": "raison précise", "risk": "LOW ou MEDIUM ou HIGH", "sentiment": "bullish ou bearish ou neutral", "key_signal": "signal principal"}}
Critère: confidence >= {CONFIDENCE_THRESHOLD} ET risk = LOW"""}],
        )
        text  = response.choices[0].message.content
        clean = text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean), price
    except Exception as e:
        print(f"[ANALYZE] Erreur {symbol}: {e}")
        return {"signal": "HOLD", "confidence": 0, "reason": "Erreur", "risk": "HIGH",
                "sentiment": "neutral", "key_signal": ""}, price


def analyze_all_markets() -> list:
    news_text  = "\n".join((get_news() + get_reddit() + get_google_trends())[:20])
    fear_greed = get_fear_greed()
    context    = {"fear_greed": fear_greed, "timestamp": datetime.now().isoformat()}
    memory["analysis_history"].append(context)
    memory["analysis_history"] = memory["analysis_history"][-100:]

    results = []
    for symbol in SYMBOLS:
        try:
            analysis, price = analyze_symbol(symbol, news_text, fear_greed)
            results.append((symbol, analysis, price))
        except Exception as e:
            print(f"[ANALYZE] Échec {symbol}: {e}")
    return results


# ─────────────────────────────────────────────
#  BOUCLE PRINCIPALE
# ─────────────────────────────────────────────
def bot_loop(send_fn_sync):
    while bot_state["running"]:
        try:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Analyse en cours...")
            results = analyze_all_markets()
            prices  = {sym: price for sym, _, price in results}
            pv      = get_portfolio_value(prices)
            stats   = get_stats()

            lines = [
                "🤖 Rapport Trading Bot",
                f"━━━━━━━━━━━━━━━━━━━",
                f"💰 Portfolio: ${pv:,.2f} ({((pv/portfolio['initial'])-1)*100:+.1f}%)",
                f"💵 Cash: ${portfolio['cash']:,.2f} | Positions: {len(portfolio['positions'])}",
                f"🏆 Win Rate: {stats['win_rate']}% ({stats['total']} trades) | 📚 {len(memory['lessons'])} leçons",
                f"━━━━━━━━━━━━━━━━━━━",
            ]

            for symbol, analysis, price in results:
                signal     = analysis.get("signal",     "HOLD")
                confidence = analysis.get("confidence", 0)
                risk       = analysis.get("risk",        "HIGH")
                reason     = analysis.get("reason",     "")
                sentiment  = analysis.get("sentiment",  "neutral")
                key_signal = analysis.get("key_signal", "")
                in_pos     = symbol in portfolio["positions"]

                sig_emoji  = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(signal, "⚪")
                sent_emoji = {"bullish": "📈", "bearish": "📉"}.get(sentiment, "➡️")
                pos_tag    = " 📍" if in_pos else ""

                lines.append(f"{sig_emoji} {symbol}{pos_tag}: {signal} {confidence}% | {sent_emoji} {sentiment}")

                if signal == "BUY" and confidence >= CONFIDENCE_THRESHOLD and risk == "LOW":
                    if not in_pos:
                        trade = execute_buy(symbol, price, reason, confidence)
                        if trade:
                            lines.append(f"  📈 ACHAT {symbol}: {trade['qty']:.6f} @ ${price:,.2f}")
                elif signal == "SELL" and confidence >= CONFIDENCE_THRESHOLD:
                    if in_pos:
                        trade = execute_sell(symbol, price, reason, confidence)
                        if trade and trade.get("pnl") is not None:
                            e = "✅" if trade["pnl"] > 0 else "❌"
                            lines.append(f"  {e} VENTE {symbol}: PnL ${trade['pnl']:+.2f}")

            send_fn_sync("\n".join(lines))
            print("\n".join(lines))
            bot_state["last_heartbeat"] = datetime.now()

        except Exception as e:
            print(f"[LOOP] Erreur: {e}")

        time.sleep(600)


# ─────────────────────────────────────────────
#  DASHBOARD HTML
# ─────────────────────────────────────────────
def generate_dashboard() -> str:
    stats = get_stats()
    try:
        prices = get_all_prices()
    except Exception:
        prices = {}
    pv      = get_portfolio_value(prices)
    pnl     = pv - portfolio["initial"]
    pnl_pct = pnl / portfolio["initial"] * 100

    pos_html = ""
    for sym, pos in portfolio["positions"].items():
        price = prices.get(sym, pos["price_in"])
        upnl  = (price - pos["price_in"]) / pos["price_in"] * 100
        color = "#2ecc71" if upnl >= 0 else "#e74c3c"
        pos_html += (f"<tr><td>{sym}</td><td>${pos['price_in']:,.2f}</td><td>${price:,.2f}</td>"
                     f'<td style="color:{color}">{upnl:+.2f}%</td>'
                     f"<td>${pos['qty']*price:,.2f}</td></tr>")

    trades_html = ""
    for t in reversed(portfolio["trades"][-20:]):
        if t.get("pnl") is not None:
            color   = "#2ecc71" if t["pnl"] > 0 else "#e74c3c"
            pnl_str = f'<span style="color:{color}">${t["pnl"]:+.2f}</span>'
        else:
            pnl_str = '<span style="color:#f39c12">En cours</span>'
        po = f"${t['price_out']:,.2f}" if t.get("price_out") else "-"
        trades_html += (f"<tr><td>{t['id']}</td><td>{t.get('symbol','')}</td><td>{t['type']}</td>"
                        f"<td>${t['price_in']:,.2f}</td><td>{po}</td><td>{pnl_str}</td>"
                        f"<td>{t['confidence']}%</td><td>{t['time_in']}</td>"
                        f"<td>{t.get('exit_reason', t['reason'])[:35]}</td></tr>")

    lessons_html = ""
    for l in reversed(memory["lessons"][-10:]):
        color = "#e74c3c" if l["type"] == "erreur" else "#2ecc71"
        e     = "❌" if l["type"] == "erreur" else "✅"
        pv2   = l.get("pnl", 0)
        lessons_html += (f'<tr><td style="color:{color}">{e}</td><td>{l.get("symbol","")}</td>'
                         f'<td style="color:{color}">${pv2:+.2f}</td>'
                         f"<td>{l['lecon'][:55]}</td><td>{l['action_future'][:55]}</td>"
                         f"<td>{l['date']}</td></tr>")

    status = "🟢 EN MARCHE" if bot_state["running"] else "🔴 ARRÊTÉ"
    last   = bot_state.get("last_heartbeat")
    hb_str = last.strftime("%H:%M:%S") if last else "—"

    return f"""<!DOCTYPE html><html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Bot</title>
<style>
body{{font-family:Arial,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:16px}}
h1{{color:#58a6ff;text-align:center;font-size:1.4em;margin-bottom:4px}}
h2{{color:#58a6ff;font-size:1em;margin:16px 0 6px}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px}}
.card{{background:#161b22;border-radius:10px;padding:12px;text-align:center}}
.label{{font-size:0.72em;color:#8b949e;margin-bottom:4px}}
.value{{font-size:1.2em;font-weight:bold}}
.green{{color:#2ecc71}}.red{{color:#e74c3c}}.blue{{color:#58a6ff}}.yellow{{color:#f39c12}}
.status{{text-align:center;margin-bottom:4px;font-size:0.9em}}
.sub{{text-align:center;color:#8b949e;font-size:0.75em;margin-bottom:12px}}
table{{width:100%;border-collapse:collapse;font-size:0.73em;margin-bottom:20px}}
th{{background:#21262d;padding:6px;text-align:left;color:#8b949e}}
td{{padding:5px 6px;border-bottom:1px solid #21262d}}
.cmds{{background:#161b22;border-radius:10px;padding:12px;margin-bottom:16px;font-size:0.82em;line-height:1.7}}
.cmds code{{color:#58a6ff}}
</style>
<meta http-equiv="refresh" content="60">
</head><body>
<h1>🤖 Trading Bot</h1>
<div class="status">{status}</div>
<div class="sub">Dernière analyse: {hb_str} | Données: {DATA_FILE.name} | SL: {STOP_LOSS_PCT*100:.0f}% | TP: {TAKE_PROFIT_PCT*100:.0f}%</div>
<div class="cmds">
<b>Commandes:</b>
<code>/start</code> Démarrer &nbsp;|&nbsp; <code>/stop</code> Arrêter &nbsp;|&nbsp;
<code>/status</code> État &nbsp;|&nbsp; <code>/analyse</code> Analyser maintenant<br>
<code>/portfolio</code> Portfolio &nbsp;|&nbsp; <code>/positions</code> Positions ouvertes &nbsp;|&nbsp;
<code>/lecons</code> Leçons &nbsp;|&nbsp; <code>/reset</code> Réinitialiser
</div>
<div class="grid">
  <div class="card"><div class="label">Portefeuille</div><div class="value blue">${pv:,.2f}</div></div>
  <div class="card"><div class="label">PnL Total</div>
    <div class="value {'green' if pnl >= 0 else 'red'}">${pnl:+.2f} ({pnl_pct:+.1f}%)</div></div>
  <div class="card"><div class="label">Cash disponible</div><div class="value">${portfolio['cash']:,.2f}</div></div>
  <div class="card"><div class="label">Positions ouvertes</div><div class="value yellow">{len(portfolio['positions'])}</div></div>
  <div class="card"><div class="label">Win Rate</div><div class="value yellow">{stats['win_rate']}%</div></div>
  <div class="card"><div class="label">Trades | Leçons</div><div class="value">{stats['total']} | {len(memory['lessons'])}</div></div>
</div>
<h2>Positions Ouvertes</h2>
<table><thead><tr><th>Symbol</th><th>Entrée</th><th>Actuel</th><th>PnL %</th><th>Valeur</th></tr></thead><tbody>
{pos_html or '<tr><td colspan="5" style="text-align:center;color:#8b949e">Aucune position</td></tr>'}
</tbody></table>
<h2>Historique Trades</h2>
<table><thead><tr><th>#</th><th>Symbol</th><th>Type</th><th>Entrée</th><th>Sortie</th>
<th>PnL</th><th>Conf.</th><th>Heure</th><th>Raison sortie</th></tr></thead><tbody>
{trades_html or '<tr><td colspan="9" style="text-align:center;color:#8b949e">Aucun trade</td></tr>'}
</tbody></table>
<h2>Mémoire & Apprentissage</h2>
<table><thead><tr><th>Type</th><th>Symbol</th><th>PnL</th><th>Leçon</th><th>Action Future</th><th>Date</th></tr></thead><tbody>
{lessons_html or '<tr><td colspan="6" style="text-align:center;color:#8b949e">Aucune leçon</td></tr>'}
</tbody></table>
</body></html>"""


# ─────────────────────────────────────────────
#  SERVEUR HTTP (dashboard + webhook Telegram)
#
#  On sert TOUT sur le port 8000 :
#  GET  /          → dashboard HTML
#  GET  /health    → health check Koyeb (200 OK)
#  POST /webhook   → updates Telegram
#
#  Pourquoi webhook et non polling ?
#  Koyeb fait du rolling deploy : la nouvelle instance démarre
#  AVANT que l'ancienne soit arrêtée. En polling, les deux
#  instances appellent getUpdates en même temps → Conflict.
#  En webhook, Telegram pousse les updates sur une URL unique ;
#  une seule instance reçoit chaque update, sans conflit.
# ─────────────────────────────────────────────
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
        # Pousse le body brut dans la queue de l'app Telegram (thread-safe)
        if _app and _main_loop:
            asyncio.run_coroutine_threadsafe(
                _process_update(body), _main_loop
            )
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


async def _process_update(body: bytes):
    """Désérialise et traite un update Telegram reçu par webhook."""
    try:
        import json as _json
        data   = _json.loads(body)
        update = Update.de_json(data, _app.bot)
        await _app.process_update(update)
    except Exception as e:
        print(f"Erreur traitement update webhook: {e}")


def run_server():
    HTTPServer(("0.0.0.0", WEBHOOK_PORT), BotHandler).serve_forever()


# ─────────────────────────────────────────────
#  HELPER : envoi Telegram thread-safe
# ─────────────────────────────────────────────
def make_send_fn_sync(chat_id: str):
    def send_fn_sync(msg: str):
        if _app is None or _main_loop is None:
            print(f"[MSG] {msg}")
            return
        future = asyncio.run_coroutine_threadsafe(
            _app.bot.send_message(chat_id=chat_id, text=msg),
            _main_loop,
        )
        try:
            future.result(timeout=15)
        except Exception as e:
            print(f"[MSG] Erreur envoi: {e}")
    return send_fn_sync


# ─────────────────────────────────────────────
#  AUTORISATION
# ─────────────────────────────────────────────
def _authorized(update: Update) -> bool:
    return str(update.effective_chat.id) == TELEGRAM_CHAT_ID


# ─────────────────────────────────────────────
#  COMMANDES TELEGRAM
# ─────────────────────────────────────────────
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
    await update.message.reply_text(
        f"✅ Bot démarré !\n"
        f"Symboles: {', '.join(SYMBOLS)}\n"
        f"Confiance min: {CONFIDENCE_THRESHOLD}% | SL: {STOP_LOSS_PCT*100:.0f}% | TP: {TAKE_PROFIT_PCT*100:.0f}%\n"
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
    last   = bot_state.get("last_heartbeat")
    hb_str = last.strftime("%H:%M:%S") if last else "—"
    status = "🟢 EN MARCHE" if bot_state["running"] else "🔴 ARRÊTÉ"
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
        f"Trades: {stats['total']} | Win Rate: {stats['win_rate']}%\n"
        f"Leçons: {len(memory['lessons'])}"
    )


async def cmd_analyse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text("🔍 Analyse en cours...")
    try:
        results = analyze_all_markets()
        lines   = ["📊 Analyse instantanée\n━━━━━━━━━━━━━"]
        for symbol, analysis, price in results:
            sig_e = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(analysis.get("signal"), "⚪")
            lines.append(
                f"{sig_e} {symbol}: ${price:,.2f}\n"
                f"  {analysis.get('signal')} {analysis.get('confidence')}% | {analysis.get('risk')}\n"
                f"  {analysis.get('key_signal','')[:70]}"
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
        f"Capital initial: $1,000.00\n"
        f"Valeur actuelle: ${pv:,.2f} ({pnl:+.2f})\n"
        f"Cash: ${portfolio['cash']:,.2f}\n"
        f"━━━━━━━━━━━━━\n"
        f"Positions:\n{pos_lines}"
        f"━━━━━━━━━━━━━\n"
        f"Trades: {stats['total']} ({stats['wins']}W/{stats['losses']}L)\n"
        f"Win Rate: {stats['win_rate']}% | Meilleur: +${stats['best']}"
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
        sl    = pos["price_in"] * (1 - STOP_LOSS_PCT)
        tp    = pos["price_in"] * (1 + TAKE_PROFIT_PCT)
        e     = "✅" if upnl >= 0 else "🔻"
        lines.append(
            f"{e} {sym}: {upnl:+.2f}%\n"
            f"  Entrée: ${pos['price_in']:,.2f} → Actuel: ${price:,.2f}\n"
            f"  🛑 SL: ${sl:,.2f} | 🎯 TP: ${tp:,.2f}"
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
        msg += f"{e} [{l.get('symbol','')}] ${l['pnl']:+.2f}\n{l['lecon']}\n→ {l['action_future']}\n\n"
    await update.message.reply_text(msg)


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    bot_state.update({"running": False, "last_heartbeat": None})
    portfolio.update({"cash": 1000.0, "positions": {}, "trades": []})
    memory.update({"lessons": [], "patterns_to_avoid": [], "patterns_that_work": [], "analysis_history": []})
    save_data()
    await update.message.reply_text("🔄 Portfolio réinitialisé à $1000. Mémoire effacée.")


# ─────────────────────────────────────────────
#  APPLICATION TELEGRAM (mode webhook)
# ─────────────────────────────────────────────
async def run_telegram():
    global _app, _main_loop
    _main_loop = asyncio.get_event_loop()

    _app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .request(HTTPXRequest(
            connection_pool_size=8,
            pool_timeout=30.0,
            connect_timeout=30.0,
            read_timeout=30.0,
            write_timeout=30.0,
        ))
        .updater(None)   # ← désactive le polling interne, on gère nous-mêmes
        .build()
    )

    for cmd, fn in [
        ("start",     cmd_start),
        ("stop",      cmd_stop),
        ("status",    cmd_status),
        ("analyse",   cmd_analyse),
        ("portfolio", cmd_portfolio),
        ("positions", cmd_positions),
        ("lecons",    cmd_lecons),
        ("reset",     cmd_reset),
    ]:
        _app.add_handler(CommandHandler(cmd, fn))

    await _app.initialize()
    await _app.start()

    # Enregistre le webhook auprès de Telegram
    if WEBHOOK_URL:
        full_url = WEBHOOK_URL.rstrip("/") + WEBHOOK_PATH
        await _app.bot.set_webhook(
            url=full_url,
            drop_pending_updates=True,   # ignore les updates accumulées
            allowed_updates=["message"],
        )
        print(f"Webhook enregistré: {full_url}")
    else:
        print("⚠️  WEBHOOK_URL non définie — commandes Telegram inactives.")
        print("    Ajoute WEBHOOK_URL=https://ton-service.koyeb.app dans les variables d'env.")

    print("Bot Telegram prêt (mode webhook)...")

    # Watchdog + résumé journalier
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


# ─────────────────────────────────────────────
#  SELF-PING (empêche Koyeb de mettre en veille sur plan Free)
#  Ping toutes les 4m30 pour rester sous le idle period de 3900s
# ─────────────────────────────────────────────
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


# ─────────────────────────────────────────────
#  ENTRYPOINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("Démarrage...")
    load_data()
    threading.Thread(target=run_server, daemon=True).start()
    threading.Thread(target=self_ping,  daemon=True).start()
    print("Serveur HTTP démarré sur port 8000")
    asyncio.run(run_telegram())
