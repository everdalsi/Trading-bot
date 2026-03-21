import os, time, schedule, feedparser
import anthropic
from binance.client import Client
from telegram import Bot
import asyncio, json
from datetime import datetime

# Config depuis variables d'environnement
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")
BINANCE_KEY = os.environ.get("BINANCE_KEY")
BINANCE_SECRET = os.environ.get("BINANCE_SECRET")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
PAPER_TRADING = True  # Mode simulation — aucun vrai trade

# Clients
claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
binance = Client(BINANCE_KEY, BINANCE_SECRET)
telegram = Bot(token=TELEGRAM_TOKEN)

# Mémoire des trades perdants
trade_history = []

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
]

def get_news():
    news = []
    for url in RSS_FEEDS:
        feed = feedparser.parse(url)
        for entry in feed.entries[:3]:
            news.append(f"{entry.title}: {entry.get('summary', '')[:200]}")
    return "\n".join(news[:10])

def get_price(symbol="BTCUSDT"):
    ticker = binance.get_ticker(symbol=symbol)
    return float(ticker['lastPrice'])

def get_losing_trades_context():
    losing = [t for t in trade_history if t['result'] == 'loss']
    if not losing:
        return "Aucun trade perdant pour l'instant."
    context = "Trades perdants précédents à éviter de répéter:\n"
    for t in losing[-5:]:
        context += f"- Signal: {t['signal'][:100]} | Raison perte: {t['reason']}\n"
    return context

def analyze_market():
    news = get_news()
    price = get_price()
    losing_context = get_losing_trades_context()
    
    prompt = f"""Tu es un expert en trading crypto court terme.

Prix actuel BTC/USDT: ${price:,.2f}

Actualités crypto récentes:
{news}

{losing_context}

Analyse ces informations et réponds UNIQUEMENT en JSON:
{{
  "signal": "BUY" ou "SELL" ou "HOLD",
  "confidence": nombre entre 0 et 100,
  "reason": "explication courte",
  "risk": "LOW" ou "MEDIUM" ou "HIGH"
}}

Ne prends une position que si confidence >= 70 et risk != HIGH."""

    response = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    
    try:
        text = response.content[0].text
        clean = text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except:
        return {"signal": "HOLD", "confidence": 0, "reason": "Erreur parsing", "risk": "HIGH"}

async def send_alert(message):
    await telegram.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)

def log_trade(signal, result, reason):
    trade_history.append({
        "timestamp": datetime.now().isoformat(),
        "signal": signal,
        "result": result,
        "reason": reason
    })
    if len(trade_history) > 100:
        trade_history.pop(0)

def run_bot():
    print(f"[{datetime.now()}] Analyse en cours...")
    
    analysis = analyze_market()
    price = get_price()
    
    signal = analysis.get("signal", "HOLD")
    confidence = analysis.get("confidence", 0)
    reason = analysis.get("reason", "")
    risk = analysis.get("risk", "HIGH")
    
    message = f"""🤖 TRADING BOT
━━━━━━━━━━━━━━
📊 BTC/USDT: ${price:,.2f}
📡 Signal: {signal}
💯 Confiance: {confidence}%
⚠️ Risque: {risk}
📝 Raison: {reason}
🔄 Mode: {'SIMULATION' if PAPER_TRADING else 'RÉEL'}
━━━━━━━━━━━━━━"""

    if signal != "HOLD" and confidence >= 70 and risk != "HIGH":
        if PAPER_TRADING:
            message += f"\n✅ TRADE SIMULÉ: {signal} BTC"
            log_trade(f"{signal} - {reason}", "pending", reason)
        
    asyncio.run(send_alert(message))
    print(message)

schedule.every(10).minutes.do(run_bot)

if __name__ == "__main__":
    print("🚀 Bot démarré en mode", "SIMULATION" if PAPER_TRADING else "RÉEL")
    run_bot()
    while True:
        schedule.run_pending()
        time.sleep(1)
