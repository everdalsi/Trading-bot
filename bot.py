import os, time, threading, feedparser, requests
from groq import Groq
from pybit.unified_trading import HTTP
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
import asyncio, json
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

GROQ_KEY = os.environ.get("ANTHROPIC_KEY")
BINANCE_KEY = os.environ.get("BINANCE_KEY")
BINANCE_SECRET = os.environ.get("BINANCE_SECRET")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

groq_client = Groq(api_key=GROQ_KEY)
bybit = HTTP(api_key=BINANCE_KEY, api_secret=BINANCE_SECRET)

portfolio = {
    "cash": 1000.0,
    "btc": 0.0,
    "initial": 1000.0,
    "trades": [],
    "position": None
}

memory = {
    "lessons": [],
    "patterns_to_avoid": [],
    "patterns_that_work": [],
    "analysis_history": []
}

bot_state = {
    "running": False,
    "thread": None
}

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
        except:
            pass
    return news

def get_reddit():
    posts = []
    for url in REDDIT_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:2]:
                posts.append(f"[REDDIT] {entry.title}")
        except:
            pass
    return posts

def get_google_trends():
    try:
        url = "https://trends.google.com/trending/rss?geo=US"
        feed = feedparser.parse(url)
        trends = []
        for entry in feed.entries[:5]:
            title = entry.title.lower()
            if any(k in title for k in ["bitcoin", "crypto", "btc", "eth"]):
                trends.append(f"[TREND] {entry.title}")
        return trends
    except:
        return []

def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=5)
        data = r.json()
        value = data['data'][0]['value']
        classification = data['data'][0]['value_classification']
        return f"Fear & Greed Index: {value}/100 ({classification})"
    except:
        return "Fear & Greed: indisponible"

def get_price(symbol="BTCUSDT"):
    result = bybit.get_tickers(category="spot", symbol=symbol)
    return float(result['result']['list'][0]['lastPrice'])

def get_portfolio_value(price):
    return portfolio["cash"] + portfolio["btc"] * price

def get_stats():
    trades = portfolio["trades"]
    closed = [t for t in trades if t.get("pnl") is not None]
    if not closed:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "best": 0, "worst": 0, "total_pnl": 0}
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    pnls = [t["pnl"] for t in closed]
    return {
        "total": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "best": round(max(pnls), 2),
        "worst": round(min(pnls), 2),
        "total_pnl": round(sum(pnls), 2)
    }

def execute_buy(price, reason, confidence):
    if portfolio["cash"] < 10:
        return None
    amount_usd = portfolio["cash"] * 0.95
    btc_bought = amount_usd / price
    portfolio["cash"] -= amount_usd
    portfolio["btc"] += btc_bought
    trade = {
        "id": len(portfolio["trades"]) + 1,
        "type": "BUY",
        "price_in": price,
        "price_out": None,
        "btc": btc_bought,
        "amount_usd": amount_usd,
        "reason": reason,
        "confidence": confidence,
        "time_in": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "time_out": None,
        "pnl": None,
        "market_context": memory["analysis_history"][-1] if memory["analysis_history"] else {}
    }
    portfolio["trades"].append(trade)
    portfolio["position"] = trade
    return trade

def execute_sell(price, reason, confidence):
    if portfolio["btc"] < 0.00001:
        return None
    btc_sold = portfolio["btc"]
    amount_usd = btc_sold * price
    portfolio["cash"] += amount_usd
    portfolio["btc"] = 0.0
    current_trade = portfolio["position"]
    if current_trade:
        pnl = amount_usd - current_trade["amount_usd"]
        current_trade["price_out"] = price
        current_trade["time_out"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        current_trade["pnl"] = round(pnl, 2)
        learn_from_trade(current_trade)
    portfolio["position"] = None
    return current_trade

def learn_from_trade(trade):
    if trade["pnl"] is None:
        return
    try:
        context = json.dumps(trade.get("market_context", {}))[:500]
        prompt = f"""Tu es un expert en trading. Analyse ce trade et tire une lecon:
Trade: {trade['type']} BTC
Prix entree: ${trade['price_in']:,.2f}
Prix sortie: ${trade['price_out']:,.2f}
PnL: ${trade['pnl']:+.2f}
Raison originale: {trade['reason']}
Confiance: {trade['confidence']}%
Contexte: {context}

{'TRADE PERDANT. Explique pourquoi et comment eviter ca.' if trade['pnl'] < 0 else 'TRADE GAGNANT. Explique pourquoi ca a marche.'}

Reponds en JSON:
{{"lecon": "lecon courte", "pattern": "pattern identifie", "action_future": "quoi faire differemment", "type": "erreur" ou "succes"}}"""

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.choices[0].message.content
        clean = text.replace("```json", "").replace("```", "").strip()
        lesson = json.loads(clean)
        lesson["trade_id"] = trade["id"]
        lesson["pnl"] = trade["pnl"]
        lesson["date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        memory["lessons"].append(lesson)
        if lesson["type"] == "erreur":
            memory["patterns_to_avoid"].append(lesson["pattern"])
        else:
            memory["patterns_that_work"].append(lesson["pattern"])
        if len(memory["lessons"]) > 50:
            memory["lessons"] = memory["lessons"][-50:]
        print(f"Lecon apprise: {lesson['lecon']}")
    except Exception as e:
        print(f"Erreur apprentissage: {e}")

def analyze_market():
    news = get_news()
    reddit = get_reddit()
    trends = get_google_trends()
    fear_greed = get_fear_greed()
    price = get_price()
    has_position = portfolio["btc"] > 0.00001
    all_signals = news + reddit + trends
    signals_text = "\n".join(all_signals[:20])
    patterns_to_avoid = "\n".join(memory["patterns_to_avoid"][-5:]) if memory["patterns_to_avoid"] else "Aucun"
    patterns_that_work = "\n".join(memory["patterns_that_work"][-5:]) if memory["patterns_that_work"] else "Aucun"
    recent_lessons = ""
    if memory["lessons"]:
        for l in memory["lessons"][-3:]:
            recent_lessons += f"- {l['lecon']} → {l['action_future']}\n"
    context = {"price": price, "fear_greed": fear_greed, "timestamp": datetime.now().isoformat()}
    memory["analysis_history"].append(context)
    if len(memory["analysis_history"]) > 100:
        memory["analysis_history"] = memory["analysis_history"][-100:]

    prompt = f"""Tu es un expert en trading crypto court terme avec memoire et apprentissage.

DONNEES MARCHE:
Prix BTC/USDT: ${price:,.2f}
{fear_greed}
Position actuelle: {'EN POSITION' if has_position else 'PAS EN POSITION'}

SIGNAUX EN TEMPS REEL:
{signals_text}

CE QUI A MARCHE:
{patterns_that_work}

ERREURS A EVITER:
{patterns_to_avoid}

LECONS RECENTES:
{recent_lessons if recent_lessons else 'Aucune lecon encore'}

Reponds UNIQUEMENT en JSON:
{{"signal": "BUY", "confidence": 75, "reason": "raison precise", "risk": "LOW", "sentiment": "bullish/bearish/neutral", "key_signal": "signal principal"}}
Ne prends position que si confidence >= 72 et risk = LOW."""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.choices[0].message.content
        clean = text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean), price
    except Exception as e:
        print(f"Erreur analyse: {e}")
        return {"signal": "HOLD", "confidence": 0, "reason": "Erreur", "risk": "HIGH", "sentiment": "neutral", "key_signal": ""}, price

def bot_loop(send_fn):
    while bot_state["running"]:
        try:
            print(f"[{datetime.now()}] Analyse en cours...")
            analysis, price = analyze_market()
            signal = analysis.get("signal", "HOLD")
            confidence = analysis.get("confidence", 0)
            reason = analysis.get("reason", "")
            risk = analysis.get("risk", "HIGH")
            sentiment = analysis.get("sentiment", "neutral")
            key_signal = analysis.get("key_signal", "")
            portfolio_value = get_portfolio_value(price)
            pnl_total = portfolio_value - portfolio["initial"]
            trade_executed = None

            if signal == "BUY" and confidence >= 72 and risk == "LOW":
                if portfolio["btc"] < 0.00001:
                    trade_executed = execute_buy(price, reason, confidence)
            elif signal == "SELL" and confidence >= 72:
                if portfolio["btc"] > 0.00001:
                    trade_executed = execute_sell(price, reason, confidence)

            stats = get_stats()
            message = f"""Trading Bot
━━━━━━━━━━━━━
BTC: ${price:,.2f}
Sentiment: {sentiment}
Signal: {signal} ({confidence}%)
Risque: {risk}
Signal cle: {key_signal[:60]}
━━━━━━━━━━━━━
Portefeuille: ${portfolio_value:,.2f}
PnL Total: ${pnl_total:+.2f}
Cash: ${portfolio['cash']:,.2f}
BTC: {portfolio['btc']:.6f}
━━━━━━━━━━━━━
Trades: {stats['total']} | Win: {stats['win_rate']}%
Meilleur: +${stats['best']} | Pire: ${stats['worst']}
Lecons: {len(memory['lessons'])}"""

            if trade_executed:
                if trade_executed.get("pnl") is not None:
                    emoji = "✅" if trade_executed["pnl"] > 0 else "❌"
                    message += f"\n{emoji} VENDU: PnL ${trade_executed['pnl']:+.2f}"
                else:
                    message += f"\n📈 ACHAT: {trade_executed['btc']:.6f} BTC @ ${price:,.2f}"

            asyncio.run(send_fn(message))
            print(message)
        except Exception as e:
            print(f"Erreur: {e}")
        time.sleep(600)

def generate_dashboard():
    stats = get_stats()
    try:
        price = get_price()
    except:
        price = 0
    pv = get_portfolio_value(price)
    pnl = pv - portfolio["initial"]
    pnl_pct = (pnl / portfolio["initial"]) * 100

    trades_html = ""
    for t in reversed(portfolio["trades"][-20:]):
        if t.get("pnl") is not None:
            color = "#2ecc71" if t["pnl"] > 0 else "#e74c3c"
            pnl_str = f'<span style="color:{color}">${t["pnl"]:+.2f}</span>'
        else:
            pnl_str = '<span style="color:#f39c12">En cours</span>'
        trades_html += f"""<tr>
            <td>{t['id']}</td><td>{t['type']}</td>
            <td>${t['price_in']:,.2f}</td>
            <td>{t['price_out'] and f"${t['price_out']:,.2f}" or "-"}</td>
            <td>{pnl_str}</td><td>{t['confidence']}%</td>
            <td>{t['time_in']}</td><td>{t['reason'][:40]}</td>
        </tr>"""

    lessons_html = ""
    for l in reversed(memory["lessons"][-10:]):
        color = "#e74c3c" if l["type"] == "erreur" else "#2ecc71"
        pnl_val = l.get('pnl', 0)
        lessons_html += f"""<tr>
            <td style="color:{color}">{"❌" if l['type'] == 'erreur' else '✅'}</td>
            <td style="color:{color}">${pnl_val:+.2f}</td>
            <td>{l['lecon'][:60]}</td>
            <td>{l['action_future'][:60]}</td>
            <td>{l['date']}</td>
        </tr>"""

    status = "🟢 EN MARCHE" if bot_state["running"] else "🔴 ARRETE"

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading Bot</title>
<style>
body{{font-family:Arial,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:16px}}
h1{{color:#58a6ff;text-align:center;font-size:1.4em}}
h2{{color:#58a6ff;font-size:1em;margin:16px 0 8px}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px}}
.card{{background:#161b22;border-radius:10px;padding:12px;text-align:center}}
.label{{font-size:0.72em;color:#8b949e;margin-bottom:4px}}
.value{{font-size:1.2em;font-weight:bold}}
.green{{color:#2ecc71}}.red{{color:#e74c3c}}.blue{{color:#58a6ff}}.yellow{{color:#f39c12}}
.status{{text-align:center;margin-bottom:12px;font-size:0.9em}}
table{{width:100%;border-collapse:collapse;font-size:0.75em;margin-bottom:20px}}
th{{background:#21262d;padding:7px;text-align:left;color:#8b949e}}
td{{padding:6px;border-bottom:1px solid #21262d}}
.cmds{{background:#161b22;border-radius:10px;padding:12px;margin-bottom:16px;font-size:0.82em}}
.cmds code{{color:#58a6ff}}
</style>
<meta http-equiv="refresh" content="60">
</head><body>
<h1>Trading Bot Dashboard</h1>
<div class="status">{status}</div>

<div class="cmds">
<b>Commandes Telegram:</b><br>
<code>/start</code> — Demarrer le bot<br>
<code>/stop</code> — Arreter le bot<br>
<code>/status</code> — Voir l'etat<br>
<code>/analyse</code> — Lancer une analyse maintenant<br>
<code>/portfolio</code> — Voir le portefeuille<br>
<code>/lecons</code> — Voir les lecons apprises<br>
<code>/reset</code> — Reinitialiser le portefeuille
</div>

<div class="grid">
    <div class="card"><div class="label">Portefeuille</div>
    <div class="value blue">${pv:,.2f}</div></div>
    <div class="card"><div class="label">PnL Total</div>
    <div class="value {'green' if pnl >= 0 else 'red'}">${pnl:+.2f} ({pnl_pct:+.1f}%)</div></div>
    <div class="card"><div class="label">Win Rate</div>
    <div class="value yellow">{stats['win_rate']}%</div></div>
    <div class="card"><div class="label">Trades</div>
    <div class="value">{stats['total']} ({stats['wins']}W/{stats['losses']}L)</div></div>
    <div class="card"><div class="label">Meilleur</div>
    <div class="value green">+${stats['best']}</div></div>
    <div class="card"><div class="label">Lecons</div>
    <div class="value blue">{len(memory['lessons'])}</div></div>
</div>

<h2>Historique Trades</h2>
<table><thead><tr>
<th>#</th><th>Type</th><th>Entree</th><th>Sortie</th>
<th>PnL</th><th>Conf.</th><th>Heure</th><th>Raison</th>
</tr></thead><tbody>
{trades_html or '<tr><td colspan="8" style="text-align:center;color:#8b949e">Aucun trade</td></tr>'}
</tbody></table>

<h2>Memoire & Apprentissage</h2>
<table><thead><tr>
<th>Type</th><th>PnL</th><th>Lecon</th><th>Action Future</th><th>Date</th>
</tr></thead><tbody>
{lessons_html or '<tr><td colspan="5" style="text-align:center;color:#8b949e">Aucune lecon</td></tr>'}
</tbody></table>
</body></html>"""

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(generate_dashboard().encode())
    def log_message(self, format, *args):
        pass

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != TELEGRAM_CHAT_ID:
        return
    if bot_state["running"]:
        await update.message.reply_text("Le bot tourne deja !")
        return
    bot_state["running"] = True

    async def send_fn(msg):
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)

    t = threading.Thread(target=bot_loop, args=(send_fn,), daemon=True)
    t.start()
    bot_state["thread"] = t
    await update.message.reply_text("Bot demarre ! Premiere analyse dans quelques instants...")

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != TELEGRAM_CHAT_ID:
        return
    bot_state["running"] = False
    await update.message.reply_text("Bot arrete.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != TELEGRAM_CHAT_ID:
        return
    status = "EN MARCHE" if bot_state["running"] else "ARRETE"
    try:
        price = get_price()
    except:
        price = 0
    pv = get_portfolio_value(price)
    pnl = pv - portfolio["initial"]
    stats = get_stats()
    msg = f"""Statut: {status}
BTC: ${price:,.2f}
Portefeuille: ${pv:,.2f}
PnL: ${pnl:+.2f}
Trades: {stats['total']} | Win Rate: {stats['win_rate']}%
Lecons: {len(memory['lessons'])}"""
    await update.message.reply_text(msg)

async def cmd_analyse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != TELEGRAM_CHAT_ID:
        return
    await update.message.reply_text("Analyse en cours...")
    try:
        analysis, price = analyze_market()
        signal = analysis.get("signal", "HOLD")
        confidence = analysis.get("confidence", 0)
        reason = analysis.get("reason", "")
        risk = analysis.get("risk", "HIGH")
        sentiment = analysis.get("sentiment", "neutral")
        key_signal = analysis.get("key_signal", "")
        msg = f"""Analyse instantanee
BTC: ${price:,.2f}
Sentiment: {sentiment}
Signal: {signal} ({confidence}%)
Risque: {risk}
Signal cle: {key_signal[:80]}
Raison: {reason}"""
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Erreur: {e}")

async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != TELEGRAM_CHAT_ID:
        return
    try:
        price = get_price()
    except:
        price = 0
    pv = get_portfolio_value(price)
    pnl = pv - portfolio["initial"]
    stats = get_stats()
    msg = f"""Portefeuille Virtuel
Capital initial: $1000.00
Valeur actuelle: ${pv:,.2f}
PnL Total: ${pnl:+.2f}
Cash: ${portfolio['cash']:,.2f}
BTC: {portfolio['btc']:.6f}
Position: {'EN POSITION' if portfolio['btc'] > 0.00001 else 'CASH'}
Trades fermes: {stats['total']}
Wins: {stats['wins']} | Losses: {stats['losses']}
Win Rate: {stats['win_rate']}%
Meilleur: +${stats['best']}
Pire: ${stats['worst']}"""
    await update.message.reply_text(msg)

async def cmd_lecons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != TELEGRAM_CHAT_ID:
        return
    if not memory["lessons"]:
        await update.message.reply_text("Aucune lecon apprise encore.")
        return
    msg = f"Lecons apprises ({len(memory['lessons'])}):\n\n"
    for l in memory["lessons"][-5:]:
        emoji = "❌" if l["type"] == "erreur" else "✅"
        msg += f"{emoji} PnL: ${l['pnl']:+.2f}\n"
        msg += f"Lecon: {l['lecon']}\n"
        msg += f"Action: {l['action_future']}\n\n"
    await update.message.reply_text(msg)

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != TELEGRAM_CHAT_ID:
        return
    bot_state["running"] = False
    portfolio["cash"] = 1000.0
    portfolio["btc"] = 0.0
    portfolio["trades"] = []
    portfolio["position"] = None
    memory["lessons"] = []
    memory["patterns_to_avoid"] = []
    memory["patterns_that_work"] = []
    memory["analysis_history"] = []
    await update.message.reply_text("Portefeuille reinitialise a $1000. Memoire effacee.")

async def run_telegram():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("analyse", cmd_analyse))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("lecons", cmd_lecons))
    app.add_handler(CommandHandler("reset", cmd_reset))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    print("Bot Telegram en ecoute...")
    while True:
        await asyncio.sleep(1)

def run_server():
    server = HTTPServer(('0.0.0.0', 8000), HealthHandler)
    server.serve_forever()

if __name__ == "__main__":
    print("Demarrage...")
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    print("Serveur HTTP demarre sur port 8000")
    asyncio.run(run_telegram())
