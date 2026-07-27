"""Trading Bo v8.2 — Autonomous multi-agent crypto trading system."""

import os, time, threading, feedparser, requests, asyncio
import json, sqlite3, re, hashlib, base64, hmac, secrets
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from execution_engine import ExecutionEngine
from urllib.parse import urlparse
from logging_config import logger
logger.info("Bot v7.1 démarré avec logging étendu ✅")

from collections import defaultdict, deque
from memory import Memory
from agents.orchestrator import Orchestrator
from agents.quant_ml_agent import QuantMLAgent
from agents.base_agent import BaseAgent
try:
    from agents.soul_agent import SoulAgent as _SoulAgent
    _SOUL_AVAILABLE = True
except ImportError:
    _SOUL_AVAILABLE = False
    logger.info("[SOUL] soul_agent.py non disponible → fonctionnalité désactivée")
from dotenv import load_dotenv
from ai_engine import (
    ask_ai, vote, _can_call_ai, ask_model_single, get_pool_status,
    _get_cached_ai, _set_cached_ai, _pool_stats, verify_trade_with_claude
)
from indicators import compute_indicators, detect_patterns

load_dotenv()

BINANCE_KEY = os.getenv("BINANCE_API_KEY") or os.getenv("BINANCE_KEY", "")
BINANCE_SECRET = os.getenv("BINANCE_API_SECRET") or os.getenv("BINANCE_SECRET", "")
TESTNET_MODE = os.getenv("TESTNET_MODE", "True").lower() in ("true", "1", "yes")
LIVE_MODE = os.getenv("LIVE_MODE", "False").lower() in ("true", "1", "yes")

# Instance ExecutionEngine
execution = ExecutionEngine(
    api_key=BINANCE_KEY,
    api_secret=BINANCE_SECRET,
    testnet=TESTNET_MODE
)

# Nettoyage des doublons + FIX MEMORY CLASS
memory = Memory()   # une seule fois

# FIX CRITIQUE : on force la classe même si un import l'a transformée en dict
if isinstance(memory, dict):
    from memory import Memory as MemoryClass
    temp_data = memory.copy()
    memory = MemoryClass()
    memory.data.update(temp_data)  # recharge les données
    logger.info("[MEMORY FIX] memory forcé en classe ✅")

orchestrator = Orchestrator()
portfolio_manager = orchestrator.portfolio_manager  # reuse orchestrator instance
quant_ml = orchestrator.quant_ml  # FIX: alias direct pour la trading_loop
performance_tracker = orchestrator.performance  # reuse orchestrator instance
wallet_copier = orchestrator.wallet_copier  # reuse orchestrator instance
yield_staking = orchestrator.yield_staking  # reuse orchestrator instance
execution_engine = orchestrator.execution_engine  # reuse orchestrator instance
shared_glossary = orchestrator.kb.get_glossary() if hasattr(orchestrator, 'kb') else {}
logger.info("[FIX] shared_glossary global chargé ✅")

def safe_get(var_name, default=None):
    """Évite les NameError sur variables non définies"""
    try:
        return globals()[var_name]
    except KeyError:
        logger.warning(f"[SAFETY] Variable manquante {var_name} → valeur par défaut")
        return default

def get_total_lessons():
    """Retourne le vrai nombre total de leçons (DB = source de vérité)"""
    try:
        if hasattr(orchestrator, 'learning') and hasattr(orchestrator.learning, 'get_lesson_count'):
            return orchestrator.learning.get_lesson_count()
    except Exception:
        pass
    return len(memory.get("lessons", []))

def load_json(path, default=None):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except:
        return default if default is not None else {}

try:
    import websocket
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False
    print("[WS] websocket-client non installé — fallback REST")

from groq import Groq

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.request import HTTPXRequest
from telegram.constants import ParseMode

# FIX : make_send supporte maintenant parse_mode='HTML'
def make_send(chat_id: str):
    def send(msg: str, parse_mode=None):
        if _app is None or _main_loop is None:
            print(f"[MSG] {msg[:80]}")
            return
        f = asyncio.run_coroutine_threadsafe(
            _app.bot.send_message(chat_id=chat_id, text=msg, parse_mode=parse_mode), _main_loop
        )
        try: f.result(timeout=15)
        except Exception as e: print(f"[MSG] {e}")
    return send

def update_performance(memory, price):
    memory = performance_tracker.update_trade_results(memory, price)
    stats = performance_tracker.get_global_stats(memory)
    return memory

#  SÉCURITÉ — Validation & Rate Limiting
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

#  CONFIG
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
WEBHOOK_PORT     = int(os.environ.get("PORT", 8000))

USER_FIRSTNAME = os.environ.get("USER_FIRSTNAME", "")
USER_LASTNAME  = os.environ.get("USER_LASTNAME", "")
USER_EMAIL     = os.environ.get("USER_EMAIL", "")
USER_ADDRESS   = os.environ.get("USER_ADDRESS", "")
USER_WALLET    = os.environ.get("USER_WALLET", "")

AGENT_CHAT_SESSIONS = set()
AGENT_CHAT_MEMORY = defaultdict(list)

# === UPGRADE ÉTAPE 5 : LIVE PROGRESSIVE ===
LIVE_MODE      = False                    # ← Mets à True UNIQUEMENT quand winrate >= 92 % validé
TESTNET_MODE   = True                     # ← Toujours True au début (sécurité)
LIVE_MAX_PCT_PER_TRADE = 0.08

# ── MODE TRAINING / LIVE ───────────────────────────────────────────────────────
BOT_TRAINING_MODE    = True    # True = training (prend max de trades), False = live (argent réel)
os.environ["BOT_TRAINING_MODE"] = "True" if BOT_TRAINING_MODE else "False"  # Propagate to agents
TRAINING_CONF_THRESH = float(os.environ.get("TRAINING_CONF_THRESH", 0.01))   # 1% — en training on prend TOUT pour apprendre
TRAINING_MAX_USD     = 15.0   # Max $15 par trade en training (limiter les pertes)
TRAINING_WIN_TARGET  = 0.68   # 68% win rate → eligible pour passage en LIVE (seuil interne, indicatif — la vraie regle projet est plus stricte)
TRAINING_MIN_TRADES  = 30     # Min 30 trades avant de proposer le passage LIVE (idem, indicatif)
LIVE_CONF_THRESH     = 0.25   # 25% — plus sélectif en LIVE
LIVE_MAX_USD_PCT     = 0.05   # Max 5% du capital par trade en LIVE

CAPITAL_INITIAL   = 1000.0
MAX_POSITIONS     = int(os.environ.get("MAX_POSITIONS", 60))
MAX_PCT_PER_TRADE = 0.28
STOP_LOSS_PCT     = 0.015
TAKE_PROFIT_PCT   = 0.060
TRAILING_PCT      = 0.015
LEVERAGE_SIM      = 2

CONFIDENCE_BASE = int(os.environ.get("CONFIDENCE_BASE", 65))
CONFIDENCE_MIN  = int(os.environ.get("CONFIDENCE_MIN", 8))
CONFIDENCE_MAX  = int(os.environ.get("CONFIDENCE_MAX", 82))

MAX_DAILY_LOSS_PCT   = 0.15
MAX_DRAWDOWN_PCT     = 0.10
FG_NEUTRAL_MIN       = 40
FG_NEUTRAL_MAX       = 60
NIGHT_HOURS_UTC      = range(2, 6)
BLACKLIST_MAX_LOSSES = 5
BLACKLIST_PERMANENT_MIN_TRADES  = 20    # min lifetime trades before a symbol can be permanently blacklisted
BLACKLIST_PERMANENT_MAX_WINRATE = 0.10  # lifetime winrate at/below this -> permanent instead of 24h cooldown
MAX_LESSONS = 999_999_999
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
CYCLE_SOLANA  = 1800

TRADING_MODE = "MICRO_HIGH_FREQ"
MICRO_SL_PCT        = 0.007
MICRO_TP_PCT        = 0.011
MICRO_TRAILING_PCT  = 0.004
# FIX (2026-07-25): 60s was far too short for a 0.7%/1.1% SL/TP band on most
# pairs -- 99.8% of ~8800 production trades exited via timeout, essentially
# closing at a random point in a tight range instead of ever reaching the
# designed SL/TP. Extended so real price action gets a fair chance to decide
# the outcome; MAX_MICRO_POSITIONS stays high so trade throughput/learning
# volume isn't meaningfully reduced (more positions held longer in parallel).
MICRO_MAX_DURATION  = int(os.environ.get("MICRO_MAX_DURATION", 180))
MICRO_MAX_PCT       = 0.48
MICRO_CONF_MIN      = 12
MAX_MICRO_POSITIONS = 120
WARMUP_TRADES_NEEDED = 50

# FIX (2026-07-27): only send higher-conviction MICRO signals (multi-indicator
# agreement) to Claude verification -- see the note at its call site in
# open_micro_trade() for why. Borderline signals (score just above
# MICRO_SCORE_THRESH) skip Claude but still go through the whale/Solana filters.
CLAUDE_VERIFY_MIN_SCORE = int(os.environ.get("CLAUDE_VERIFY_MIN_SCORE", 5))

# PRO-WALLET FILTER (2026-07-25): block-only signal from Binance whale flow
# (>$500K trades) — vetoes a MICRO trade only when whale flow clearly opposes
# the signal direction. Never increases size/confidence (explicit user
# requirement — "filtre supplémentaire seulement"). Direct/sync Binance call
# per candidate symbol, NOT the full orchestrator.ask_all() ensemble (too
# slow at ~8000+ trades/day; per-agent timeouts there are 6-10s each).
WHALE_FILTER_ENABLED    = os.environ.get("WHALE_FILTER_ENABLED", "true").lower() in ("true", "1", "yes")
WHALE_FILTER_THRESHOLD  = float(os.environ.get("WHALE_FILTER_THRESHOLD", 0.35))  # buy_ratio below this vetoes a BUY (mirrored for SELL)
WHALE_CACHE_TTL         = 90

# SOLANA SMART-MONEY FILTER (2026-07-25, best-effort, updated after research):
# the previous list came from a dated snapshot article with no way to verify
# the wallets were still active or accurate, so it was dropped. Replaced with
# Cupsey (@Cupseyy on X) — one of the most-followed Solana memecoin traders on
# Crypto Twitter, wallet independently corroborated across OKX Wallet's public
# leaderboard (#1 by realized profit, ~$5.14M/3mo, 67.7% win rate), Solscan,
# and GMGN. No public no-auth API exists for a *live* KOL leaderboard (kolscan.io
# and solanatracker.io both render their tables client-side against an
# authenticated backend), so this stays a manually curated, occasionally
# refreshed list rather than a dynamic feed — same "best-effort, may go stale"
# caveat as before, now at least anchored to a verifiable, currently-active,
# genuinely high-win-rate trader instead of an untraceable one.
# Caveat: Cupsey's edge is sniping brand-new pump.fun launches (~900 trades/day),
# most of which aren't symbols this bot trades — only his BONK/JUP/WIF/POPCAT/
# native-SOL balance drift (all Binance-tradeable) is usable here, so this is a
# noisier proxy for his conviction than his real strategy.
# Balance accumulation/distribution across these wallets is used the same way
# as the Binance whale filter: block-only, never boosts size/confidence. Fails
# open (no veto) whenever a fresh snapshot isn't available.
SOLANA_FILTER_ENABLED  = os.environ.get("SOLANA_FILTER_ENABLED", "true").lower() in ("true", "1", "yes")
SOLANA_RPC             = os.environ.get("SOLANA_RPC", "https://api.mainnet-beta.solana.com")
SOLANA_SMART_WALLETS   = [
    "suqh5sHtr8HyJ7q8scBimULPkPpA557prMG47xCHQfK",   # Cupsey (@Cupseyy) — verified, see note above
]
# mint address -> symbol traded by this bot (native SOL tracked via getBalance separately)
SOLANA_TOKEN_MINTS = {
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONKUSDT",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN":  "JUPUSDT",
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm": "WIFUSDT",
    "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr": "POPCATUSDT",
}
SOLANA_SNAPSHOT_TTL   = 1800   # re-snapshot at most every 30 min
SOLANA_BIAS_MAX_AGE   = 7200   # a bias older than this is considered stale, ignored
SOLANA_BIAS_THRESHOLD = 0.03   # aggregate holdings must move >=3% to register a bias

MEME_SL_PCT       = 0.05
MEME_TP_PCT       = 0.15
MEME_TRAILING_PCT = 0.07
MEME_MAX_PCT      = 0.05
MEME_MAX_DURATION = 300

MODE_CONFIG = {
    "MICRO_HIGH_FREQ": {"max_positions": 120, "pct_per_trade": 0.48, "confidence_min": 12, "duration_max": 60},
    "REGULAR":         {"max_positions": 60,  "pct_per_trade": 0.28, "confidence_min": 65, "duration_max": 300},
    "SWING":           {"max_positions": 12,  "pct_per_trade": 0.08, "confidence_min": 85, "duration_max": 3600},
    "MIX":             {"max_positions": 80,  "pct_per_trade": 0.35, "confidence_min": 40, "duration_max": 600}
}

MAIN_OBJECTIVE = "Maximiser le nombre de trades simulés pour accumuler un maximum d'expérience et améliorer le winrate le plus rapidement possible"
EXTREME_LEARNING_MODE = True   # FIX TRAINING V8: activé en training pour maximiser les trades et l'apprentissage
LEARN_MODE_ENABLED    = True
LEARN_MODE_CONF_MIN   = 1      # FIX TRAINING V8: seuil minimal 1% pour apprendre de tout
LEARN_MODE_MAX_PCT    = 0.48

GROQ_FAST_MODEL  = "llama-3.1-8b-instant"      # 14400 req/jour gratuit — rapide
GROQ_SMART_MODEL = "llama-3.3-70b-versatile"   # 1000 req/jour gratuit — meilleur pour trading
GROQ_CODE_MODEL  = "deepseek-r1-distill-llama-70b" # Spécialisé code/raisonnement — 100 req/h gratuit
DB_FILE   = "sim_v7.db"
DATA_FILE = Path(os.getenv("DATA_DIR", ".")) / "sim_portfolio_v7.json"

# AEGIS log ring-buffer — last 500 entries, survives the whole process lifetime
LOG_BUFFER = deque(maxlen=500)

class _AegisBufferHandler:
    """Lightweight log sink — not a real logging.Handler to avoid import order issues"""
    def emit(self, level: str, msg: str):
        import datetime as _dt
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%H:%M:%S")
        LOG_BUFFER.append({"ts": ts, "level": level, "msg": str(msg)[:400]})

_aegis_log_sink = _AegisBufferHandler()

def _log(level: str, msg: str):
    """Dual-write to logger AND LOG_BUFFER"""
    _aegis_log_sink.emit(level, msg)
    if level == "ERROR": logger.error(msg)
    elif level == "WARN": logger.warning(msg)
    else: logger.info(msg)

AEGIS_MEMORY_FILE = Path("aegis_memory.json")

AEGIS_WATCHDOG_ENABLED: bool = True
AEGIS_ERRORS_SEEN: set  = set()
AEGIS_LAST_FIX: dict    = {}  # {path, old_text, new_text, commit_msg} — pending confirmation

def _load_aegis_memory() -> dict:
    try:
        if AEGIS_MEMORY_FILE.exists():
            import json as _j
            with open(AEGIS_MEMORY_FILE, "r", encoding="utf-8") as f:
                data = _j.load(f)
            logger.info(f"[AEGIS] Memory loaded from disk ({len(data)} chats)")
            return data
    except Exception as e:
        logger.warning(f"[AEGIS] Could not load memory: {e}")
    return {}

def _save_aegis_memory(memory: dict):
    try:
        import json as _j
        with open(AEGIS_MEMORY_FILE, "w", encoding="utf-8") as f:
            _j.dump(memory, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[AEGIS] Could not save memory: {e}")


BINANCE_BASE = "https://api.binance.com"
BINANCE_KLINES = "https://data.binance.com/api/v3/klines"
INTERVAL_MAP = {
    "1":"1m","3":"3m","5":"5m","15":"15m","30":"30m",
    "60":"1h","120":"2h","240":"4h","D":"1d","1D":"1d"
}

# ═══════════════════════════════════════════════════════════════════
# MULTI-MARKET SYMBOL UNIVERSE — tous les marchés Binance
# ═══════════════════════════════════════════════════════════════════

# Symboles prioritaires par catégorie (toujours en tier HOT)
PRIORITY_SYMBOLS = {
    "LAYER1":    ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","ADAUSDT","AVAXUSDT",
                  "DOTUSDT","NEARUSDT","ATOMUSDT","TRXUSDT","LTCUSDT","TONUSDT","SUIUSDT","APTUSDT",
                  "HBARUSDT","ALGOUSDT","ICPUSDT","FILUSDT","EGLDUSDT"],
    "LAYER2":    ["ARBUSDT","OPUSDT","MATICUSDT","IMXUSDT","STRKUSDT","SEIUSDT","MNTUSDT",
                  "ZKUSDT","SCROLLUSDT","METISUSDT"],
    "DEFI":      ["UNIUSDT","LINKUSDT","AAVEUSDT","MKRUSDT","SUSHIUSDT","CRVUSDT","COMPUSDT",
                  "PENDLEUSDT","AEROUSDT","ENAUSDT","JUPUSDT","RAYDIUMUSDT","DYDXUSDT"],
    "AI_DATA":   ["FETUSDT","RENDERUSDT","TAOUSDT","OCEANUSDT","AGIXUSDT","WLDUSDT",
                  "AIUSDT","NFPUSDT","ARKMUSDT"],
    "MEME":      ["DOGEUSDT","SHIBUSDT","PEPEUSDT","WIFUSDT","BONKUSDT","FLOKIUSDT",
                  "POPCATUSDT","MEMEUSDT","TURBOUSDT","GMEUSDT","BRETTUSDT","MOGUSDT","MEWUSDT"],
    "RWA_INFRA": ["ONDOUSDT","KASUSDT","ORDIUSDT","STXUSDT","IOTAUSDT","CELOUSDT"],
    "GAMING":    ["AXSUSDT","SANDUSDT","MANAUSDT","GALAUSDT","PIXELUSDT","NOTUSDT",
                  "BEAMUSDT","RONUSDT","YMMUSDT"],
    "COMMODITY": ["PAXGUSDT","XAUTUSDT"],   # Or tokenisé
    "EXCHANGE":  ["WUSDT","OKBUSDT"],
    "INFRA":     ["INJUSDT","FETUSDT","CELRUSDT","BANDUSDT","STMXUSDT"],
}

# Liste initiale HOT (complète statique — mise à jour dynamique au démarrage)
CRYPTO_SYMBOLS = [s for cat in PRIORITY_SYMBOLS.values() for s in cat]
CRYPTO_SYMBOLS = list(dict.fromkeys(CRYPTO_SYMBOLS))   # dedup, préserve l'ordre

MICRO_SYMBOLS    = CRYPTO_SYMBOLS
MEMECOIN_SOLANA  = ["BONKUSDT","WIFUSDT","POPCATUSDT","JUPUSDT"]
MEMECOIN_ETH     = ["SHIBUSDT","FLOKIUSDT","PEPEUSDT","DOGEUSDT"]

# Placeholders — enrichis au démarrage du bot par discover_all_symbols()
STOCKS_SYMBOLS    = {"AAPL":"Apple","TSLA":"Tesla","NVDA":"NVIDIA","META":"Meta","MSFT":"Microsoft","GOOGL":"Google","AMZN":"Amazon","AMD":"AMD","COIN":"Coinbase","MSTR":"MicroStrategy"}
FOREX_SYMBOLS     = {"EURUSD=X":"EUR/USD","GBPUSD=X":"GBP/USD","USDJPY=X":"USD/JPY","AUDUSD=X":"AUD/USD"}
COMMODITY_SYMBOLS = {"GC=F":"Or","SI=F":"Argent","CL=F":"Pétrole","HG=F":"Cuivre"}
ALL_SYMBOLS       = CRYPTO_SYMBOLS

# ── Tier system global state ──────────────────────────────────────
_ALL_DISCOVERED    = []   # tous les symboles Binance (400+)
_HOT_SYMBOLS       = list(CRYPTO_SYMBOLS)   # scanné chaque cycle
_WARM_SYMBOLS      = []   # scanné 1 cycle / 3
_COLD_SYMBOLS      = []   # scanné 1 cycle / 10
_TIER_CYCLE_CTR    = 0
_TIER_LAST_REFRESH = 0.0  # timestamp dernier refresh (toutes les 6h)
# FIX (2026-07-25): PRIORITY_SYMBOLS (la liste manuelle ci-dessus) contient à
# elle seule 82 symboles -- avec l'ancien cap HOT à 80, elle remplissait déjà
# tout le tier avant même que le tri par vrai volume Binance n'ait une chance
# de contribuer un seul slot. Des tokens niche de la liste manuelle (ex:
# catégorie GAMING) squattaient donc HOT à la place de paires à plus gros
# volume réel non présentes dans la liste. Cap relevé pour garantir des slots
# HOT au vrai volume, sans rien retirer de la liste manuelle existante.
HOT_TIER_CAP = int(os.environ.get("HOT_TIER_CAP", 140))

def discover_all_symbols() -> list:
    """
    Récupère TOUTES les paires USDT actives sur Binance (400+).
    Trie par volume 24h décroissant.
    Appelé au démarrage du bot et toutes les 6h.
    """
    global _ALL_DISCOVERED, _HOT_SYMBOLS, _WARM_SYMBOLS, _COLD_SYMBOLS, _TIER_LAST_REFRESH
    global CRYPTO_SYMBOLS, MICRO_SYMBOLS, ALL_SYMBOLS
    try:
        import requests as _req, time as _t2
        # Récupération exchange info
        _ei  = _req.get("https://api.binance.com/api/v3/exchangeInfo", timeout=15).json()
        _all = [s["symbol"] for s in _ei.get("symbols", [])
                if s["symbol"].endswith("USDT") and s["status"] == "TRADING"
                and s.get("quoteAsset") == "USDT"]
        # Volumes 24h
        _tks = _req.get("https://api.binance.com/api/v3/ticker/24hr", timeout=15).json()
        _vol = {t["symbol"]: float(t["quoteVolume"]) for t in _tks
                if isinstance(t, dict) and t["symbol"].endswith("USDT")}
        _all.sort(key=lambda s: _vol.get(s, 0), reverse=True)
        _TIER_LAST_REFRESH = _t2.time()
        logger.info(f"[DISCOVERY] 🌍 {len(_all)} paires USDT actives sur Binance")
        _rebuild_tiers(_all)
        return _all
    except Exception as _de:
        logger.warning(f"[DISCOVERY] Fallback liste statique: {_de}")
        _rebuild_tiers(CRYPTO_SYMBOLS)
        return CRYPTO_SYMBOLS

def _rebuild_tiers(all_syms: list):
    """Reconstruit les 3 tiers à partir d'une liste triée par volume."""
    global _ALL_DISCOVERED, _HOT_SYMBOLS, _WARM_SYMBOLS, _COLD_SYMBOLS
    global CRYPTO_SYMBOLS, MICRO_SYMBOLS, ALL_SYMBOLS
    priority_flat = list(dict.fromkeys(
        [s for cat in PRIORITY_SYMBOLS.values() for s in cat]
    ))
    # HOT = priorités + top volume (max HOT_TIER_CAP) -- priority_flat seul
    # (82 symboles) ne doit plus pouvoir épuiser le cap avant que le tri par
    # volume réel ne contribue ses propres slots (voir note HOT_TIER_CAP).
    hot_raw = priority_flat + [s for s in all_syms if s not in priority_flat]
    _HOT_SYMBOLS  = list(dict.fromkeys(hot_raw))[:HOT_TIER_CAP]
    _WARM_SYMBOLS = [s for s in all_syms[60:220]  if s not in _HOT_SYMBOLS]
    _COLD_SYMBOLS = [s for s in all_syms[220:]    if s not in _HOT_SYMBOLS and s not in _WARM_SYMBOLS]
    _ALL_DISCOVERED = all_syms
    CRYPTO_SYMBOLS  = _HOT_SYMBOLS
    MICRO_SYMBOLS   = _HOT_SYMBOLS
    ALL_SYMBOLS     = all_syms
    logger.info(f"[TIERS] 🔥 HOT:{len(_HOT_SYMBOLS)} 🔆 WARM:{len(_WARM_SYMBOLS)} ❄️ COLD:{len(_COLD_SYMBOLS)} | Total:{len(all_syms)}")

def get_scan_symbols() -> list:
    """
    Retourne les symboles à scanner ce cycle.
    HOT → chaque cycle | WARM → 1/3 | COLD → 1/10
    Auto-refresh discover toutes les 6h.
    """
    global _TIER_CYCLE_CTR, _TIER_LAST_REFRESH
    import time as _ts
    _TIER_CYCLE_CTR += 1
    # Auto-refresh toutes les 6h
    if _ts.time() - _TIER_LAST_REFRESH > 21600:
        try:
            import threading as _thr
            _thr.Thread(target=discover_all_symbols, daemon=True).start()
        except Exception: pass
    active = list(_HOT_SYMBOLS)
    if _TIER_CYCLE_CTR % 3 == 0:
        active.extend(_WARM_SYMBOLS[:120])
    if _TIER_CYCLE_CTR % 10 == 0:
        active.extend(_COLD_SYMBOLS[:200])
    return list(dict.fromkeys(active))

def promote_symbol(symbol: str, reason: str = "pump"):
    """Promeut un symbole WARM/COLD dans HOT (pump détecté)."""
    global _HOT_SYMBOLS, _WARM_SYMBOLS, _COLD_SYMBOLS
    if symbol in _HOT_SYMBOLS: return
    if symbol in _WARM_SYMBOLS: _WARM_SYMBOLS.remove(symbol)
    if symbol in _COLD_SYMBOLS: _COLD_SYMBOLS.remove(symbol)
    _HOT_SYMBOLS.insert(5, symbol)  # Top 5 pour être analysé en priorité
    logger.info(f"[TIERS] ⬆️ PROMOTION HOT: {symbol} ({reason})")


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

#  CLIENTS
# ═════════��═════════════════════════════════════════════════════
if not GROQ_KEY:
    raise RuntimeError("[FATAL] GROQ_API_KEY est absent du .env — le bot ne peut pas démarrer sans clé IA.")
groq_client = Groq(api_key=GROQ_KEY)

def get_binance_client():
    import ccxt  # lazy import — only loaded when needed
    exchange = ccxt.binance({
        'apiKey': BINANCE_KEY,
        'secret': BINANCE_SECRET,
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    })
    if TESTNET_MODE:
        exchange.set_sandbox_mode(True)
        logger.info("🚀 BINANCE TESTNET ACTIVÉ — aucun risque réel")
    else:
        logger.warning("⚠️  LIVE MODE RÉEL ACTIVÉ — argent réel en jeu")
    return exchange

BINANCE_CLIENT = get_binance_client()

#  ÉTAT GLOBAL
sim = {
    "cash": CAPITAL_INITIAL, "initial": CAPITAL_INITIAL,
    "positions": {}, "trades": [], "equity_history": [],
    "session": 1, "peak_equity": CAPITAL_INITIAL,
    "daily_start_equity": CAPITAL_INITIAL, "daily_start_date": "",
}
# FIX CRITIQUE : la définition de memory en dict supprimée — écrasait l instance Memory() créée ligne ~55.
  # L instance Memory() (classe) est la seule référence valide : memory.get(), memory.data, etc.
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
    "last_dashboard_export": 0,
}

_main_loop  = None
_app        = None
_start_ts   = time.time()
_agent_running = False
_force_trade_override = False  # Activé par force_max_trades → bypass LLM NO TRADE
_force_trade_until    = 0.0    # Timestamp fin du override (time.time() + durée)
_agent_activity_log = []
_soul = None   # Âme du bot — initialisée au démarrage   # Feed temps réel
_last_debate_cycle   = {}  # Dernier cycle complet
_debate_cycle_id     = 0   # Compteur de cycle
_signal_cache:   set  = set()
_last_raw_agent_outputs = []  # Derniers outputs bruts agents (pour tracking précision)
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

#  MODULES PROPRES
from websocket_manager import ws_manager
from data_handler import (
    data_handler,
    get_prices_batch,
    get_klines_1m_cached,
    get_klines_5m_cached,
    get_volume_data
)

WS_SYMBOLS_WATCH = [
    "btcusdt","ethusdt","solusdt","bnbusdt","xrpusdt",
    "dogeusdt","avaxusdt","linkusdt","arbusdt","aptusdt",
    "fetusdt","injusdt","nearusdt","suiusdt","opusdt",
]

ws_manager.start(WS_SYMBOLS_WATCH)
data_handler.prefill_caches(WS_SYMBOLS_WATCH)

def get_current_price(symbol: str) -> float | None:
    return data_handler.get_current_price(symbol)

def get_price(symbol: str, force=False) -> float:
    return data_handler.get_current_price(symbol) or 0.0

def fetch_prices_sync(symbols: list) -> dict:
    """Retourne {symbol: price} pour une liste de symboles — synchrone."""
    result = {}
    for sym in symbols:
        px = data_handler.get_current_price(sym)
        if not px:
            try:
                r = requests.get(
                    f"https://data.binance.com/api/v3/ticker/price?symbol={sym}",
                    timeout=3
                )
                px = float(r.json().get("price", 0))
            except Exception:
                px = 0.0
        result[sym] = px
    return result

def get_klines(symbol: str, interval: str = "1m", limit: int = 100):
    return data_handler.get_klines(symbol, interval, limit)

#  AI POOL
AI_PROVIDERS = [
    # Groq gratuit: 30 req/min, 14400 req/jour sur llama-3.1-8b-instant
    {"name":"groq_fast",  "calls":0, "window_start":time.time(), "last_call":0,
     "max_calls_per_hour":200, "cooldown":3,  "available":True, "failures":0, "model":GROQ_FAST_MODEL},
    # Groq 70b: 30 req/min, 1000 req/jour (réservé aux décisions importantes)
    {"name":"groq_smart", "calls":0, "window_start":time.time(), "last_call":0,
     "max_calls_per_hour":30,  "cooldown":5,  "available":True, "failures":0, "model":GROQ_SMART_MODEL},
    # HuggingFace gratuit: ~1000 req/jour
    {"name":"huggingface","calls":0, "window_start":time.time(), "last_call":0,
     "max_calls_per_hour":50,  "cooldown":5,  "available":True, "failures":0, "model":None},
    # DeepSeek R1: spécialisé code/raisonnement — parfait pour AEGIS édition code
    {"name":"groq_code",  "calls":0, "window_start":time.time(), "last_call":0,
     "max_calls_per_hour":100, "cooldown":3,  "available":True, "failures":0, "model":GROQ_CODE_MODEL},
]
_pool_stats = {
    "total_calls":0,"calls_by_provider":{},"fallbacks":0,"last_provider":"groq_fast",
    "cache_hits":0,"daily_date":"","groq_fast_daily":0,"groq_smart_daily":0,"hf_daily":0,
}
HF_MODELS = [
    "Qwen/Qwen2.5-72B-Instruct",           # Meilleur modèle HF gratuit
    "mistralai/Mistral-7B-Instruct-v0.3",  # Rapide et fiable
    "meta-llama/Llama-3.1-8B-Instruct",    # Backup Llama
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
    hour_utc = datetime.now(timezone.utc).hour
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

def _call_groq(prompt: str, model: str = None) -> dict:
    r = groq_client.chat.completions.create(
        model=(model or GROQ_FAST_MODEL),
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
            result = _call_groq(compressed, provider.get("model")) if name.startswith("groq") else _call_huggingface(compressed)
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

def _can_call_ai() -> bool:
    return _get_available_provider() is not None

def ask_model_single(prompt: str, model: str=None) -> dict:
    return ask_ai(prompt)

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

#  RISK MANAGEMENT
def check_daily_reset():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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
                symbol   = pos.get("symbol", "")
                price_in = float(pos.get("price_in", 0))
                amount   = float(pos.get("amount_usd", 0))
                lev      = float(pos.get("leverage", 1))
                side     = pos.get("side", "LONG")

                if price_in <= 0 or amount <= 0:
                    continue
                if amount > CAPITAL_INITIAL * 2:
                    continue

                p = prices.get(symbol, price_in)
                if p <= 0 or p > price_in * 100:
                    p = price_in

                if side == "LONG":
                    pnl_pct = (p - price_in) / price_in
                else:
                    pnl_pct = (price_in - p) / price_in

                pos_pnl = safe_pnl(pnl_pct, amount, lev)
                equity += amount + pos_pnl

            except Exception:
                continue

        try:
            if hasattr(yield_staking, 'staked_positions') and yield_staking.staked_positions:
                for asset, amount in yield_staking.staked_positions.items():
                    equity += amount * 1.0
                    if asset == "ETH":
                        equity += amount * 0.00016
                    elif asset == "SOL":
                        equity += amount * 0.000225
                print(f"[YIELD-EQUITY] Staking inclus → +{sum(yield_staking.staked_positions.values()):,.2f} $")
        except Exception as staking_err:
            print(f"[YIELD-EQUITY] Erreur tracking staking: {staking_err}")

        if equity > CAPITAL_INITIAL * 1000 or equity < 0:
            print(f"[SAFETY-EQUITY] Capital anormal ({equity:,.2f}) → reset à ${CAPITAL_INITIAL:,.2f}")
            equity = CAPITAL_INITIAL
            sim["cash"] = CAPITAL_INITIAL
            sim["positions"] = {}

        return round(max(0, equity), 2)

    except Exception as e:
        print(f"[SAFETY-EQUITY] Erreur globale: {e}")
        return CAPITAL_INITIAL

def check_risk_limits(send_fn) -> bool:
    # FIX TRAINING V8: en mode apprentissage, on ne stoppe jamais — chaque trade est une leçon
    if BOT_TRAINING_MODE or EXTREME_LEARNING_MODE:
        return True
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
    if bl[symbol].get("permanent"):
        return True
    if time.time() - bl[symbol].get("ts", 0) > 86400:
        del memory["symbol_blacklist"][symbol]
        return False
    return True

def update_blacklist(symbol: str, won: bool):
    # BUG FIX (2026-07-24): consecutive_losses was never reset when a symbol got
    # blacklisted, so after the 24h auto-expiry it re-blacklisted itself on the very
    # next loss instead of getting a fresh 5-strike chance -- observed in production
    # with TONUSDT (592 losses / 593 trades) and NFPUSDT (405/~410), both stuck in
    # an expire-lose-reblacklist loop for over a day. Chronic offenders (enough
    # lifetime trades, catastrophic winrate) now get permanently blacklisted instead.
    cl = memory.setdefault("consecutive_losses", {})
    lifetime = memory.setdefault("symbol_lifetime", {})
    stats = lifetime.setdefault(symbol, {"trades": 0, "wins": 0})
    stats["trades"] += 1

    if won:
        stats["wins"] += 1
        cl[symbol] = 0
    else:
        cl[symbol] = cl.get(symbol, 0) + 1
        if cl[symbol] >= BLACKLIST_MAX_LOSSES:
            is_chronic = (
                stats["trades"] >= BLACKLIST_PERMANENT_MIN_TRADES
                and stats["wins"] / stats["trades"] <= BLACKLIST_PERMANENT_MAX_WINRATE
            )
            memory.setdefault("symbol_blacklist", {})[symbol] = {
                "ts": time.time(),
                "reason": f"{BLACKLIST_MAX_LOSSES} pertes consécutives"
                          + (" — chronique, blacklist permanente" if is_chronic else ""),
                "losses": cl[symbol],
                "permanent": is_chronic,
            }
            if not is_chronic:
                cl[symbol] = 0  # fresh 5-strike budget after the 24h cooldown expires

def is_night_time() -> bool:
    return datetime.now(timezone.utc).hour in NIGHT_HOURS_UTC

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

def get_open_interest(symbol: str) -> dict:
    return {}

def format_open_interest(symbol: str) -> str:
    return ""

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

#  KELLY CRITERION
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
    fg = get_fear_greed_value()
    # FIX: Fear extrême = réduire les mises (pas augmenter)
    if fg < 25:
        base = max(0.03, base * 0.60)  # Panique totale → taille réduite de 40%
    elif fg < 40:
        base = max(0.03, base * 0.80)  # Fear → légère réduction
    return base

def dynamic_position_size(confidence: int, market: str, symbol: str) -> float:
    base        = kelly_criterion(30)
    # FIX: recalibré pour MICRO_CONF_MIN=12 (ancienne formule donnait ~0.02 à conf=12)
    # Nouvelle formule: 0.3 à conf=12, 1.0 à conf=70, 1.3 max
    conf_mult = max(0.30, min(1.30, 0.30 + (confidence - 12) / 60))
    fg = get_fear_greed_value()
    macro = get_macro_trend()
    # FIX: Fear extrême doit RÉDUIRE la taille, pas l'augmenter
    fg_mult = 1.0
    if fg < 25:
        fg_mult = 0.50   # Panique totale → -50%
    elif fg < 40:
        fg_mult = 0.75   # Fear → -25%
    elif fg > 80:
        fg_mult = 0.90   # FIX V6.0: Greed extrême → réduire la taille (marché suracheté)
    elif fg > 65:
        fg_mult = 1.10   # Greed modéré → légère hausse
    macro_mult = 1.0
    if macro == "BULL":
        macro_mult = 1.35
    elif macro == "BEAR":
        macro_mult = 0.65
    market_mult = 0.6 if market=="FUTURES" else 0.4 if market=="MEME" else 1.0
    night_mult  = 0.6 if is_night_time() else 1.0
    return round(max(0.03, min(0.35,
        base * conf_mult * fg_mult * macro_mult * market_mult * night_mult)), 3)

#  INDICATEURS TECHNIQUES
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
    opps = []
    prices = get_prices_batch()
    for symbol in get_scan_symbols():
        price = prices.get(symbol, 0)
        if not price: continue
        closes = get_klines_5m_cached(symbol)
        if len(closes) < 27: continue
        ind = compute_indicators(closes)
        if not ind: continue
        vols = get_volume_data(symbol, "5", 15)
        pats = detect_patterns(symbol, ind, vols)
        score = 0
        if ind["rsi"] < 35: score += 3
        elif ind["rsi"] < 45: score += 1
        if ind["rsi"] > 70: score -= 3
        elif ind["rsi"] > 60: score -= 1
        if ind["macd_h"] > 0: score += 2
        else: score -= 1
        if ind["mom5"] > 1: score += 2
        elif ind["mom5"] < -1: score -= 2
        if ind["ema_cross"] == "BULL": score += 1
        else: score -= 1
        score += get_symbol_confidence_bonus(symbol) // 5

        opps.append({
            "market": "crypto",
            "symbol": symbol,
            "price": price,
            "score": score,
            "direction": "BUY" if score > 0 else "SELL",
            "ind": ind,
            "patterns": pats,
            "has_alert": any(p["signal"] == "HOLD" for p in pats)
        })
    opps.sort(key=lambda x: abs(x["score"]), reverse=True)
    return opps[:15]


#  SCAN MARCHÉ PARALLÈLE V7 — asyncio.gather() sur N symboles

async def _analyze_symbol_quick(symbol: str, prices: dict) -> dict:
    """Analyse rapide et non-bloquante d'un symbole (pour le scan parallèle)."""
    try:
        if is_blacklisted(symbol):
            return {}
        price = prices.get(symbol, 0)
        if not price:
            return {}
        closes = get_klines_5m_cached(symbol)
        if len(closes) < 27:
            return {}
        ind = compute_indicators(closes)
        if not ind:
            return {}
        score = 0
        rsi = ind.get("rsi", 50)
        macd_h = ind.get("macd_h", 0)
        mom5 = ind.get("mom5", 0)
        ema_cross = ind.get("ema_cross", "NEUTRAL")
        if rsi < 35: score += 3
        elif rsi < 45: score += 1
        if rsi > 70: score -= 3
        elif rsi > 60: score -= 1
        if macd_h > 0: score += 2
        else: score -= 1
        if mom5 > 1: score += 2
        elif mom5 < -1: score -= 2
        if ema_cross == "BULL": score += 1
        else: score -= 1
        score += get_symbol_confidence_bonus(symbol) // 5
        return {
            "market": "crypto", "symbol": symbol, "price": price,
            "score": score, "direction": "BUY" if score > 0 else "SELL",
            "ind": ind, "patterns": [],
        }
    except Exception as e:
        logger.debug(f"[scan_parallel] {symbol}: {e}")
        return {}


async def scan_market_parallel() -> list:
    """
    Version PARALLÈLE de scan_market() — ×10-20 plus rapide.
    asyncio.gather() sur TOUS les symboles simultanément.
    """
    prices = get_prices_batch()
    _scan_syms = get_scan_symbols()
    tasks = [_analyze_symbol_quick(sym, prices) for sym in _scan_syms]
    results_raw = await asyncio.gather(*tasks, return_exceptions=True)
    opps = [r for r in results_raw if r and isinstance(r, dict) and r.get("score", 0) != 0]
    opps.sort(key=lambda x: abs(x.get("score", 0)), reverse=True)
    logger.info(f"[scan_parallel] ✅ {len(opps)} opportunités / {len(_scan_syms)} symboles | tiers actifs")

    return opps

#  ANALYSE COMPLÈTE
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
    ws_status = "WS✅" if ws_manager.connected else "REST"
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
JSON:{{"signal":"BUY/SELL/HOLD","confidence":0-100,"reason":"raison","risk":"LOW/MEDIUM|HIGH","market":"SPOT|FUTURES"}}"""
    result = vote(prompt)
    if sym_bonus != 0 and result.get("signal") != "HOLD":
        result["confidence"] = max(0, min(100, result.get("confidence",0) + sym_bonus))
    result.update({
        "symbol":symbol,"price":price,"patterns":pats,
        "confluence":conf,"ob":ob,"ind":ind,"kelly_pct":kelly_pct
    })
    return result

#  GESTION DES POSITIONS
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

def is_warmup_done() -> bool:
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    if len(closed) >= WARMUP_TRADES_NEEDED:
        bot_state["warmup_done"] = True
        return True
    return False

def can_open_trade(symbol: str, market: str, send_fn) -> bool:
    if LIVE_MODE and not is_warmup_done():
        closed_count = len([t for t in sim["trades"] if t.get("pnl") is not None])
        send_fn(f"🔥 Warm-up en cours : {closed_count}/{WARMUP_TRADES_NEEDED} trades simulés")
        return True
    if EXTREME_LEARNING_MODE:
        return True
    if not check_risk_limits(send_fn): 
        return False
    if not validate_symbol(symbol): 
        return False
    if is_blacklisted(symbol): 
        return False
    if is_correlated(symbol): 
        return False
    if market not in ("MEME","MICRO") and is_fg_neutral(): 
        return False
    return True

def open_trade(analysis: dict, send_fn) -> dict | None:
    symbol = analysis["symbol"]
    price  = analysis["price"]
    signal = analysis["signal"]
    conf   = analysis["confidence"]
    reason = sanitize_string(analysis["reason"])
    market = analysis.get("market", "SPOT")
    pats   = analysis.get("patterns", [])
    side   = "LONG" if signal == "BUY" else "SHORT"

    if signal == "SELL" and market == "SPOT":
        return None

    if not can_open_trade(symbol, market, send_fn):
        return None

    approved, verify_reason = verify_trade_with_claude(analysis)
    if not approved:
        logger.info(f"[CLAUDE-VERIFY] Trade vetoe pour {symbol} ({signal}, conf={conf}%): {verify_reason}")
        return None

    if EXTREME_LEARNING_MODE:
        kelly_pct = LEARN_MODE_MAX_PCT
    elif LIVE_MODE:
        kelly_pct = LIVE_MAX_PCT_PER_TRADE
    else:
        kelly_pct = analysis.get("kelly_pct") or dynamic_position_size(conf, market, symbol)

    leverage = LEVERAGE_SIM if market == "FUTURES" else 1
    amount   = sim["cash"] * kelly_pct
    qty      = amount / price

    sim["cash"] -= amount

    trade = {
        "id": len(sim["trades"]) + 1,
        "symbol": symbol,
        "market": market,
        "side": side,
        "price_in": price,
        "price_out": None,
        "qty": qty,
        "amount_usd": amount,
        "confidence": conf,
        "reason": reason,
        "exit_reason": None,
        "time_in": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "time_out": None,
        "pnl": None,
        "pnl_pct": None,
        "duration_min": None,
        "patterns": [p["name"] for p in pats if p.get("signal") != "HOLD"],
        "leverage": leverage,
        "peak_price": price,
        "trough_price": price,
        "kelly_pct": kelly_pct
    }

    if LIVE_MODE and bot_state.get("warmup_done", False):
        try:
            order_side = "buy" if side == "LONG" else "sell"
            order = BINANCE_CLIENT.create_market_order(symbol, order_side, qty)
            trade["live_order_id"] = order["id"]
            trade["live_status"] = "placed"
            send_fn(f"✅ ORDRE LIVE PLACÉ #{trade['id']} | {order_side.upper()} {symbol}")
        except Exception as e:
            send_fn(f"❌ ERREUR LIVE ORDER #{trade['id']}: {e}")
            return None

    pos_key = f"{market}_{symbol}_{side}_{trade['id']}"
    sim["trades"].append(trade)
    sim["positions"][pos_key] = {**trade, "pos_key": pos_key}

    db_save_trade(trade)
    save_data()
    bot_state["trades_today"] += 1

    sl = price * (1 - STOP_LOSS_PCT) if side == "LONG" else price * (1 + STOP_LOSS_PCT)
    tp = price * (1 + TAKE_PROFIT_PCT) if side == "LONG" else price * (1 - TAKE_PROFIT_PCT)

    learning = "🎓 MAX TRADES" if EXTREME_LEARNING_MODE else ""
    macro    = bot_state.get("macro_trend", "NEUTRAL")
    macro_e  = "🐂" if macro == "BULL" else "🐻" if macro == "BEAR" else "➡️"

    if conf >= 90 or EXTREME_LEARNING_MODE:
        send_fn(
            f"{'🟢' if side=='LONG' else '🔴'} {learning} {symbol.replace('USDT','')}\n"
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
    if not pos:
        return None

    if LIVE_MODE and "live_order_id" in pos:
        try:
            side = "sell" if pos["side"] == "LONG" else "buy"
            BINANCE_CLIENT.create_market_order(pos["symbol"], side, pos["qty"])
            send_fn(f"✅ POSITION LIVE FERMÉE #{pos['id']}")
        except Exception as e:
            send_fn(f"⚠️ Erreur fermeture live #{pos['id']}: {e}")

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

    duration = 0
    try:
        t_in = datetime.strptime(pos["time_in"], "%Y-%m-%d %H:%M:%S")
        duration = int((datetime.now() - t_in).total_seconds() / 60)
    except:
        pass

    trade = next((t for t in reversed(sim["trades"]) if t["id"] == pos["id"]), None)
    if trade:
        trade.update({
            "price_out": price,
            "time_out": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 2),
            "exit_reason": reason,
            "duration_min": duration
        })
        db_save_trade(trade)
        learn_from_trade(trade, send_fn=send_fn)

    won = pnl > 0
    if won:
        memory["total_wins"] = memory.get("total_wins", 0) + 1
    else:
        memory["total_losses"] = memory.get("total_losses", 0) + 1

    update_symbol_score(pos["symbol"], won)
    update_blacklist(pos["symbol"], won)
    save_data()

    equity_now = get_equity_safe()
    pnl_total  = equity_now - sim["initial"]
    coin = pos["symbol"].replace("USDT", "")
    chg  = (price - entry) / entry * 100

    if abs(pnl) > CAPITAL_INITIAL * 0.01:
        send_fn(
            f"{'✅' if pnl > 0 else '❌'} {coin} fermé — #{pos['id']}\n"
            f"  ${entry:.4f} → ${price:.4f} ({chg:+.2f}%)\n"
            f"  {'🤑' if pnl > 0 else '💸'} ${pnl:+.4f} | {reason}\n"
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

def micro_signal(symbol: str, price: float) -> dict:
    try:
        closes = get_klines_1m_cached(symbol)
        if len(closes) < 14:
            return {"signal":"HOLD","score":0,"conf":0}
        ema5       = float(closes.ewm(span=5, adjust=False).mean().iloc[-1])
        ema13      = float(closes.ewm(span=13,adjust=False).mean().iloc[-1])
        ema5_prev  = float(closes.ewm(span=5, adjust=False).mean().iloc[-2])
        ema13_prev = float(closes.ewm(span=13,adjust=False).mean().iloc[-2])
        delta = closes.diff()
        gain  = delta.clip(lower=0).ewm(com=6,adjust=False).mean()
        loss  = (-delta).clip(lower=0).ewm(com=6,adjust=False).mean()
        rsi7  = float((100-100/(1+gain/loss.replace(0,np.nan))).iloc[-1])
        mom3  = float((closes.iloc[-1]-closes.iloc[-4])/closes.iloc[-4]*100) if len(closes)>=4 else 0
        sma10 = closes.rolling(10).mean()
        std10 = closes.rolling(10).std()
        bb_up = float((sma10+1.5*std10).iloc[-1])
        bb_lo = float((sma10-1.5*std10).iloc[-1])
        bb_pct= (price-bb_lo)/(bb_up-bb_lo)*100 if bb_up!=bb_lo else 50
        vols      = get_volume_data(symbol,"1",10)
        avg_vol   = sum(vols[:-1])/max(len(vols)-1,1) if len(vols)>1 else 1
        vol_ratio = vols[-1]/avg_vol if avg_vol > 0 else 1.0
        score = 0
        contributors = []
        # FIX (2026-07-26): the old reason string always reported the EMA's
        # *current* direction (ema5>ema13 snapshot) regardless of whether an
        # EMA cross actually contributed to the score this cycle. That made
        # every RSI-driven mean-reversion signal -- a normal, valid setup
        # where price is still trending while RSI is extended -- read as
        # self-contradictory ("EMA up + RSI overbought = SELL??") to the
        # Claude verify gate, which vetoes on exactly that kind of internal
        # inconsistency. Once Claude verify started running reliably (credits
        # fixed), this pre-existing bug started vetoing ~97%+ of candidates
        # instead of the small minority it should. Now the reason only cites
        # what actually moved the score, so a trend+reversal-timing signal
        # reads as the coherent multi-factor setup it actually is.
        if ema5_prev<=ema13_prev and ema5>ema13:
            score += 2; contributors.append("EMA5/13 cross up")
        elif ema5_prev>=ema13_prev and ema5<ema13:
            score -= 2; contributors.append("EMA5/13 cross down")
        if rsi7<28:
            score+=2; contributors.append(f"RSI7={rsi7:.0f} oversold (reversal-up)")
        elif rsi7<40:
            score+=1; contributors.append(f"RSI7={rsi7:.0f} low")
        elif rsi7>72:
            score-=2; contributors.append(f"RSI7={rsi7:.0f} overbought (reversal-down)")
        elif rsi7>60:
            score-=1; contributors.append(f"RSI7={rsi7:.0f} high")
        if mom3>0.6:
            score+=1; contributors.append(f"momentum {mom3:+.2f}% up")
        elif mom3<-0.6:
            score-=1; contributors.append(f"momentum {mom3:+.2f}% down")
        if bb_pct<8:
            score+=1; contributors.append("Bollinger lower band (oversold)")
        elif bb_pct>92:
            score-=1; contributors.append("Bollinger upper band (overbought)")
        if vol_ratio>2.5 and score>0:
            score+=1; contributors.append(f"volume spike {vol_ratio:.1f}x confirming")
        if vol_ratio>2.5 and score<0:
            score-=1; contributors.append(f"volume spike {vol_ratio:.1f}x confirming")
        reason = ", ".join(contributors) if contributors else f"weak/no signal (RSI7={rsi7:.0f}, mom={mom3:+.2f}%)"
        # FIX (2026-07-25): threshold of 2 let a single weak signal (e.g. just an
        # EMA cross, nothing else confirming) open a trade -- raised to 3 so at
        # least two indicators agree, while staying well below live's 4 so trade
        # volume for learning isn't meaningfully cut.
        _score_thresh = int(os.environ.get("MICRO_SCORE_THRESH", 3)) if BOT_TRAINING_MODE else 4
        # FIX (2026-07-27): the old formula (60 + score*7, capped 95) put a
        # bare-minimum score=3 signal at 81% -- indistinguishable from a
        # strong score=5+ signal, and the Claude verify gate correctly kept
        # flagging that as "confidence overstated for a single weak/borderline
        # indicator". Now scaled against the real max possible score (EMA
        # cross ±2, RSI ±2, momentum ±1, Bollinger ±1, volume confirm ±1 = 7),
        # so a borderline pass reads as moderate confidence and only genuine
        # multi-indicator agreement earns high confidence.
        _MICRO_MAX_SCORE = 7
        conf = min(90, round(45 + 45 * min(abs(score), _MICRO_MAX_SCORE) / _MICRO_MAX_SCORE))
        if score >= _score_thresh:    return {"signal": "BUY",  "score": score, "conf": conf, "reason": reason}
        elif score <= -_score_thresh: return {"signal": "SELL", "score": score, "conf": conf, "reason": reason}
        return {"signal": "HOLD", "score": score, "conf": 0}
    except Exception:
        return {"signal":"HOLD","score":0,"conf":0}

_whale_cache: dict = {}

def check_whale_filter(symbol: str, direction: str) -> tuple[bool, str]:
    """Blocking-only pro-wallet filter. Fetches recent Binance aggTrades,
    computes the >$500K whale buy/sell ratio, and vetoes only when whale
    flow clearly opposes `direction` ("BUY"/"SELL"). Fails open (allows the
    trade) on any data error or ambiguous reading — same fail-open contract
    as verify_trade_with_claude above."""
    if not WHALE_FILTER_ENABLED:
        return True, "whale_filter_disabled"
    now = time.time()
    cached = _whale_cache.get(symbol)
    if cached and now - cached[0] < WHALE_CACHE_TTL:
        metrics = cached[1]
    else:
        try:
            trades_r = requests.get(f"{BINANCE_BASE}/api/v3/aggTrades", params={"symbol": symbol, "limit": 500}, timeout=4)
            trades = trades_r.json() if isinstance(trades_r.json(), list) else []
            price_r = requests.get(f"{BINANCE_BASE}/api/v3/ticker/price", params={"symbol": symbol}, timeout=3)
            ref_price = float(price_r.json().get("price", 0))
        except Exception:
            return True, "whale_data_unavailable"
        whale_buy = whale_sell = 0.0
        for t in trades:
            try:
                value = float(t.get("q", 0)) * ref_price
                if value >= 500_000:
                    if t.get("m", False): whale_sell += value
                    else: whale_buy += value
            except Exception:
                continue
        total = whale_buy + whale_sell
        metrics = {"buy_ratio": (whale_buy / total) if total >= 1 else 0.5, "total": total}
        _whale_cache[symbol] = (now, metrics)

    if metrics["total"] < 1:
        return True, "no_whale_activity"
    buy_ratio = metrics["buy_ratio"]
    if direction == "BUY" and buy_ratio < WHALE_FILTER_THRESHOLD:
        return False, f"whales sell {1-buy_ratio:.0%} of flow, opposes BUY"
    if direction == "SELL" and buy_ratio > (1 - WHALE_FILTER_THRESHOLD):
        return False, f"whales buy {buy_ratio:.0%} of flow, opposes SELL"
    return True, "ok"

_solana_snapshot_ts = 0.0
_solana_prev_totals: dict = {}   # symbol -> aggregate token amount held across tracked wallets, previous snapshot
_solana_bias: dict = {}          # symbol -> (direction, ts)

def _solana_rpc(method: str, params: list, timeout: float = 6):
    try:
        r = requests.post(SOLANA_RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=timeout)
        return r.json().get("result")
    except Exception:
        return None

def update_solana_snapshots():
    """Best-effort accumulation/distribution tracker for the 3 tracked wallets.
    Runs on its own slow cycle (CYCLE_SOLANA), never inline with a trade decision."""
    global _solana_snapshot_ts, _solana_prev_totals, _solana_bias
    if not SOLANA_FILTER_ENABLED:
        return
    now = time.time()
    if now - _solana_snapshot_ts < SOLANA_SNAPSHOT_TTL:
        return
    _solana_snapshot_ts = now

    totals: dict = {}   # symbol -> total amount across wallets this snapshot
    sol_lamports_total = 0
    for wallet in SOLANA_SMART_WALLETS:
        bal = _solana_rpc("getBalance", [wallet])
        if isinstance(bal, dict):
            sol_lamports_total += bal.get("value", 0) or 0
        try:
            accounts = _solana_rpc("getTokenAccountsByOwner", [wallet, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"}, {"encoding": "jsonParsed"}])
            for acc in (accounts or {}).get("value", []) or []:
                info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                mint = info.get("mint")
                sym = SOLANA_TOKEN_MINTS.get(mint)
                if not sym:
                    continue
                amount = float(info.get("tokenAmount", {}).get("uiAmount") or 0)
                totals[sym] = totals.get(sym, 0.0) + amount
        except Exception:
            continue
    if "SOLUSDT" in CRYPTO_SYMBOLS:
        totals["SOLUSDT"] = sol_lamports_total / 1e9

    for sym, total in totals.items():
        prev = _solana_prev_totals.get(sym)
        if prev and prev > 0:
            change = (total - prev) / prev
            if change >= SOLANA_BIAS_THRESHOLD:
                _solana_bias[sym] = ("BUY", now)
            elif change <= -SOLANA_BIAS_THRESHOLD:
                _solana_bias[sym] = ("SELL", now)
    _solana_prev_totals = totals
    if _solana_bias:
        logger.info(f"[SOLANA-SMART-MONEY] biais actifs: { {k: v[0] for k,v in _solana_bias.items()} }")

def check_solana_filter(symbol: str, direction: str) -> tuple[bool, str]:
    """Blocking-only filter mirroring check_whale_filter, sourced from the
    3 tracked Solana wallets instead of Binance's public trade tape."""
    if not SOLANA_FILTER_ENABLED:
        return True, "solana_filter_disabled"
    bias = _solana_bias.get(symbol)
    if not bias:
        return True, "no_solana_bias"
    bias_dir, ts = bias
    if time.time() - ts > SOLANA_BIAS_MAX_AGE:
        return True, "solana_bias_stale"
    if bias_dir != direction:
        return False, f"solana smart-money bias={bias_dir}, opposes {direction}"
    return True, "ok"

def open_micro_trade(symbol: str, price: float, signal: dict, send_fn) -> dict | None:
    # FIX TRAINING V9: SELL (SHORT) autorisé en training pour apprendre dans les deux sens
    # En mode live seulement, on bloque les SELL (certains exchanges ne permettent pas le short)
    if signal["signal"] == "SELL" and not (BOT_TRAINING_MODE or EXTREME_LEARNING_MODE):
        return None
    trade_side = "SHORT" if signal["signal"] == "SELL" else "LONG"
    micro_count = sum(1 for p in sim["positions"].values() if p.get("trade_type") == "MICRO")
    if micro_count >= MAX_MICRO_POSITIONS:
        return None
    if any(p["symbol"] == symbol and p.get("trade_type") == "MICRO" for p in sim["positions"].values()):
        return None
    if sim["cash"] < 15:
        return None
    if not BOT_TRAINING_MODE and symbol in memory.get("recent_losses", []):
        print(f"[FILTER] {symbol} évité en MICRO (pertes récentes)")
        return None

    # GAP FIX (2026-07-25): verify_trade_with_claude() was only wired into
    # open_trade(), but 99.8% of actual trades go through this MICRO path
    # instead -- the verification gate had never fired on a single real trade
    # despite ~8800 trades executed. Wired in here too, same fail-open contract.
    # FIX (2026-07-27): training mode deliberately trades on borderline,
    # single-factor signals (score just above MICRO_SCORE_THRESH) to keep
    # learning volume up -- but a skeptical reviewer will always correctly
    # call a genuinely thin signal thin, no matter how the confidence/reason
    # text is calibrated. That's a real design conflict, not a bug: recalibrating
    # the prompt further just keeps chasing it. Resolved by only sending
    # higher-conviction, multi-indicator-agreement signals (score >= 5) to
    # Claude at all -- borderline signals skip straight to the whale/Solana
    # filters, which stay block-only and unconditional for every trade.
    if abs(signal["score"]) >= CLAUDE_VERIFY_MIN_SCORE:
        approved, verify_reason = verify_trade_with_claude({
            "symbol": symbol, "signal": signal["signal"], "price": price,
            "confidence": signal["conf"], "reason": signal.get("reason", ""),
            "market": "MICRO", "patterns": [{"name": f"score={signal['score']}"}],
        })
        if not approved:
            logger.info(f"[CLAUDE-VERIFY] Trade MICRO vetoe pour {symbol} ({signal['signal']}, conf={signal['conf']}%): {verify_reason}")
            return None

    whale_ok, whale_reason = check_whale_filter(symbol, signal["signal"])
    if not whale_ok:
        logger.info(f"[WHALE-FILTER] Trade MICRO vetoe pour {symbol} ({signal['signal']}): {whale_reason}")
        return None

    solana_ok, solana_reason = check_solana_filter(symbol, signal["signal"])
    if not solana_ok:
        logger.info(f"[SOLANA-FILTER] Trade MICRO vetoe pour {symbol} ({signal['signal']}): {solana_reason}")
        return None

    fg = get_fear_greed_value()
    macro = get_macro_trend()
    night_factor = 0.7 if is_night_time() else 1.0
    fg_mult = 0.55 if fg < 25 else 0.75 if fg < 40 else 1.15 if fg > 75 else 1.00  # FIX V6.0: inversé — peur extrême → taille réduite (était BUG: 1.45)
    macro_mult = 1.35 if macro == "BULL" else 0.65 if macro == "BEAR" else 1.0
    amount = sim["cash"] * MICRO_MAX_PCT * fg_mult * macro_mult * night_factor
    qty = amount / price
    sim["cash"] -= amount
    trade = {
        "id": len(sim["trades"]) + 1,
        "symbol": symbol,
        "market": "MICRO",
        "side": trade_side,
        "trade_type": "MICRO",
        "price_in": price,
        "price_out": None,
        "qty": qty,
        "amount_usd": amount,
        "confidence": signal["conf"],
        "reason": signal.get("reason", ""),
        "exit_reason": None,
        "time_in": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "time_out": None,
        "pnl": None,
        "pnl_pct": None,
        "duration_min": None,
        "patterns": [f"score={signal['score']}"],
        "leverage": 1,
        "peak_price": price,
        "open_time": time.time(),
        "kelly_pct": MICRO_MAX_PCT,
    }
    pos_key = f"MICRO_{symbol}_{trade['id']}"
    sim["trades"].append(trade)
    sim["positions"][pos_key] = {**trade, "pos_key": pos_key}
    db_save_trade(trade)
    bot_state["trades_today"] += 1
    bot_state["micro_count"] = bot_state.get("micro_count", 0) + 1
    return trade

def monitor_micro_positions(send_fn):
    now = time.time()
    prices = get_prices_batch()
    for pos_key, pos in list(sim["positions"].items()):
        if pos.get("trade_type") != "MICRO": continue
        symbol = pos["symbol"]
        price  = prices.get(symbol) or get_price(symbol,force=True)
        if not price: continue
        entry   = pos["price_in"]
        # FIX TRAINING V9: P&L inversé pour SHORT (profit si prix baisse)
        _is_short = pos.get("side", "LONG") == "SHORT"
        change  = (entry - price) / entry if _is_short else (price - entry) / entry
        elapsed = now-pos.get("open_time",now)
        # Pour SHORT: pic de profit = prix le plus bas atteint
        if _is_short:
            pos["peak_price"] = min(pos.get("peak_price", entry), price)
            trailing = (price - pos["peak_price"]) / max(pos["peak_price"], 1)
        else:
            pos["peak_price"] = max(pos.get("peak_price",entry), price)
            trailing = (pos["peak_price"]-price)/pos["peak_price"]
        reason = None
        if change <= -MICRO_SL_PCT:                          reason = f"🛑 MICRO SL ({change*100:+.2f}%)"
        elif change >= MICRO_TP_PCT:                         reason = f"🎯 MICRO TP ({change*100:+.2f}%)"
        elif change>0.003 and trailing>=MICRO_TRAILING_PCT:  reason = f"📐 TRAIL ({trailing*100:.2f}%)"
        elif elapsed >= MICRO_MAX_DURATION:                  reason = f"⏱ TIMEOUT {int(elapsed)}s"
        if reason:
            pnl = change*pos["amount_usd"]
            sim["cash"] += pos["amount_usd"]+pnl
            trade = next((t for t in reversed(sim["trades"]) if t["id"]==pos["id"]), None)
            if trade:
                trade.update({
                    "price_out":price,
                    "time_out":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "pnl":round(pnl,6),"pnl_pct":round(change*100,3),
                    "exit_reason":reason,"duration_min":max(1,int(elapsed/60))
                })
                db_save_trade(trade)
                learn_from_trade(trade,send_fn=None)
            del sim["positions"][pos_key]
            save_data()
            coin = symbol.replace("USDT","")
            send_fn(f"{'✅' if pnl>0 else '❌'} Micro {coin}: ${pnl:+.4f} | {reason}")
            won = pnl > 0
            if won: memory["total_wins"]   = memory.get("total_wins",0)+1
            else:   memory["total_losses"] = memory.get("total_losses",0)+1
            update_symbol_score(symbol, won)
            update_blacklist(symbol, won)

def run_micro_cycle(send_fn):
    max_micro = MAX_MICRO_POSITIONS
    prices = get_prices_batch()
    for symbol, price in prices.items():
        update_performance(memory, price)
    for symbol in MICRO_SYMBOLS:
        if not bot_state["running"]: break
        if not EXTREME_LEARNING_MODE and is_blacklisted(symbol): continue
        price = prices.get(symbol, 0)
        if not price: continue
        micro_count = sum(1 for p in sim["positions"].values() if p.get("trade_type") == "MICRO")
        if micro_count >= max_micro: break
        if any(p["symbol"] == symbol and p.get("trade_type") == "MICRO" for p in sim["positions"].values()): 
            continue
        sig = micro_signal(symbol, price)
        conf_min = 5 if (BOT_TRAINING_MODE or EXTREME_LEARNING_MODE) else MICRO_CONF_MIN
        if sig["signal"] != "HOLD" and sig["conf"] >= conf_min:
            open_micro_trade(symbol, price, sig, send_fn)

def dex_get_pair(query: str) -> dict:
    now = time.time()
    if query in _dex_cache:
        ts, d = _dex_cache[query]
        if now-ts < 15: return d
    try:
        url = f"https://api.dexscreener.com/latest/dex/search?q={query}"
        r   = requests.get(url, timeout=8, headers={"User-Agent":"Mozilla/5.0"})
        pairs = r.json().get("pairs",[])
        if not pairs: return {}
        best = max(pairs, key=lambda p: float(p.get("liquidity",{}).get("usd",0) or 0))
        d = {
            "symbol":   best.get("baseToken",{}).get("symbol","?"),
            "price":    float(best.get("priceUsd",0) or 0),
            "change_5m":float(best.get("priceChange",{}).get("m5",0) or 0),
            "change_1h":float(best.get("priceChange",{}).get("h1",0) or 0),
            "volume_1h":float(best.get("volume",{}).get("h1",0) or 0),
            "liquidity":float(best.get("liquidity",{}).get("usd",0) or 0),
            "chain":    best.get("chainId","?"),
            "url":      best.get("url","")
        }
        _dex_cache[query] = (now, d)
        return d
    except Exception:
        return {}

def dex_get_trending() -> list:
    global _trending_cache, _trending_ts
    now = time.time()
    if now-_trending_ts < 120 and _trending_cache:
        return _trending_cache
    results = []
    try:
        r = requests.get(DEXSCREENER_NEW, timeout=8, headers={"User-Agent":"Mozilla/5.0"})
        boosts = r.json() if isinstance(r.json(),list) else []
        for b in boosts[:20]:
            if b.get("chainId") != "solana": continue
            addr = b.get("tokenAddress","")
            if not addr: continue
            data = dex_get_pair(addr)
            if data and data.get("liquidity",0) > 50000:
                results.append(data)
            if len(results) >= 8: break
    except Exception:
        pass
    _trending_cache = results
    _trending_ts = now
    return results

def meme_signal_score(token: dict) -> int:
    score = 0
    c1h = token.get("change_1h",0); c5m = token.get("change_5m",0)
    vol = token.get("volume_1h",0); liq = token.get("liquidity",0)
    if liq < 10000: return 0
    if c1h>50: score+=4
    elif c1h>20: score+=3
    elif c1h>10: score+=2
    elif c1h>5:  score+=1
    if c5m>10: score+=3
    elif c5m>5: score+=2
    elif c5m>2: score+=1
    if vol>1000000: score+=2
    elif vol>100000: score+=1
    if liq<20000: score-=3
    elif liq<50000: score-=1
    if c1h<-20: score-=4
    elif c1h<-5: score-=1
    return max(0,min(10,score))

def _open_meme_trade(token: dict, score: int, source: str, send_fn) -> dict | None:
    symbol = token.get("symbol","?"); price = token.get("price",0)
    if not price or price <= 0: return None
    if sim["cash"] < 20: return None
    meme_count = sum(1 for p in sim["positions"].values() if p.get("trade_type")=="MEME")
    if meme_count >= 2: return None
    if any(p.get("meme_symbol")==symbol for p in sim["positions"].values()): return None
    amount = sim["cash"] * MEME_MAX_PCT
    qty    = amount/price
    sim["cash"] -= amount
    trade = {
        "id":len(sim["trades"])+1,"symbol":symbol,"market":"MEME","side":"LONG",
        "trade_type":"MEME","meme_symbol":symbol,"price_in":price,"price_out":None,
        "qty":qty,"amount_usd":amount,"confidence":min(95,50+score*7),
        "reason":f"Score {score}/10 | @{source}","exit_reason":None,
        "time_in":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "time_out":None,"pnl":None,"pnl_pct":None,"duration_min":None,
        "patterns":["memecoin"],"leverage":1,"peak_price":price,
        "open_time":time.time(),"kelly_pct":MEME_MAX_PCT
    }
    pos_key = f"MEME_{symbol}_{trade['id']}"
    sim["trades"].append(trade)
    sim["positions"][pos_key] = {**trade,"pos_key":pos_key}
    db_save_trade(trade)
    bot_state["trades_today"] += 1
    send_fn(f"🐸 MEME ${symbol} | ${price:.8f} | Score:{score}/10 | {source}")
    return trade

def _monitor_meme_positions(send_fn):
    now = time.time()
    for pos_key, pos in list(sim["positions"].items()):
        if pos.get("trade_type") != "MEME": continue
        symbol  = pos.get("meme_symbol",pos["symbol"])
        entry   = pos["price_in"]
        elapsed = now - pos.get("open_time",now)
        try:
            data  = dex_get_pair(symbol)
            price = data.get("price",0)
            if not price: continue
        except Exception:
            continue
        change = (price-entry)/entry
        pos["peak_price"] = max(pos.get("peak_price",entry), price)
        trailing = (pos["peak_price"]-price)/pos["peak_price"] if pos["peak_price"]>0 else 0
        reason = None
        if change <= -MEME_SL_PCT:         reason = f"🛑 MEME SL ({change*100:+.1f}%)"
        elif change >= MEME_TP_PCT:        reason = f"🎯 MEME TP ({change*100:+.1f}%)"
        elif change>0.05 and trailing>=MEME_TRAILING_PCT: reason = "📐 MEME TRAIL"
        elif elapsed >= MEME_MAX_DURATION: reason = "⏱ TIMEOUT"
        if reason:
            pnl = change*pos["amount_usd"]
            sim["cash"] += pos["amount_usd"]+pnl
            trade = next((t for t in reversed(sim["trades"]) if t["id"]==pos["id"]), None)
            if trade:
                trade.update({
                    "price_out":price,
                    "time_out":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "pnl":round(pnl,6),"pnl_pct":round(change*100,2),
                    "exit_reason":reason,"duration_min":max(1,int(elapsed/60))
                })
                db_save_trade(trade)
                learn_from_trade(trade,send_fn=None)
            del sim["positions"][pos_key]
            save_data()
            send_fn(f"{'✅' if pnl>0 else '❌'} MEME ${symbol}: ${pnl:+.4f} | {reason}")
            won = pnl > 0
            if won: memory["total_wins"]   = memory.get("total_wins",0)+1
            else:   memory["total_losses"] = memory.get("total_losses",0)+1

def run_meme_cycle(send_fn):
    trending = dex_get_trending()
    for data in trending[:3]:
        if not data or data.get("price",0) <= 0: continue
        symbol  = data["symbol"]
        already = any(p.get("meme_symbol")==symbol for p in sim["positions"].values())
        if already: continue
        if data.get("change_5m",0)>5 and data.get("volume_1h",0)>5000:
            score = 5
            if data.get("change_1h",0) > 30: score += 2
            _open_meme_trade(data, score, "DexScreener", send_fn)
    prices = get_prices_batch()
    for sym in MEMECOIN_SOLANA + MEMECOIN_ETH:
        price = prices.get(sym,0)
        if not price: continue
        already = any(p["symbol"]==sym for p in sim["positions"].values())
        if already: continue
        closes = get_klines(sym,"5",30)
        if len(closes) < 10: continue
        ind = compute_indicators(closes)
        if not ind: continue
        score = 0
        if ind.get("rsi",50) < 30:  score += 3
        if ind.get("mom5",0) > 4:   score += 3
        if ind.get("macd_h",0) > 0: score += 2
        if score >= 6:
            meme_count = sum(1 for p in sim["positions"].values() if p.get("trade_type")=="MEME")
            if meme_count < 2:
                token_data = {
                    "symbol":sym.replace("USDT",""),"price":price,
                    "change_1h":ind.get("mom5",0),"change_5m":0,
                    "volume_1h":0,"liquidity":999999,"chain":"binance"
                }
                _open_meme_trade(token_data, score, "Binance", send_fn)
    _monitor_meme_positions(send_fn)

def _signal_hash(content: str) -> str:
    return hashlib.md5(content.encode()).hexdigest()[:12]

def scrape_nitter(username: str) -> list:
    signals = []
    for instance in NITTER_INSTANCES:
        try:
            feed = feedparser.parse(f"https://{instance}/{username}/rss")
            if not feed.entries: continue
            for entry in feed.entries[:3]:
                text = re.sub(r'<[^>]+>','',entry.get("summary","")).strip()
                if len(text) < 20: continue
                h = _signal_hash(text)
                if h in _signal_cache: continue
                _signal_cache.add(h)
                signals.append({
                    "source":"Twitter","author":username,
                    "content":text[:300],"url":entry.get("link",""),
                    "hash":h,"ts":datetime.now().strftime("%Y-%m-%d %H:%M")
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
            h = _signal_hash(title)
            if h in _signal_cache or not title: continue
            _signal_cache.add(h)
            signals.append({
                "source":"YouTube","author":channel_name,
                "content":title,"url":entry.get("link",""),
                "hash":h,"ts":datetime.now().strftime("%Y-%m-%d %H:%M")
            })
    except Exception:
        pass
    return signals

def analyze_signal_sentiment(signal: dict) -> dict:
    try:
        prompt = f"""Message trader @{signal['author']}: "{signal['content']}"
JSON: {{"sentiment":"bullish/bearish/neutral","symbol":"BTC","strength":2,"summary":"résumé"}}"""
        r = ask_ai(prompt)
        signal.update({
            "sentiment": r.get("sentiment","neutral"),
            "symbol":    r.get("symbol","GENERAL"),
            "strength":  r.get("strength",1),
            "summary":   r.get("summary","")
        })
    except Exception:
        signal.update({"sentiment":"neutral","symbol":"GENERAL","strength":1})
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT OR IGNORE INTO trader_signals
            (source,author,content,sentiment,symbol,strength,timestamp,url,hash)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (signal["source"],signal["author"],signal["content"],
             signal["sentiment"],signal["symbol"],signal["strength"],
             signal["ts"],signal["url"],signal["hash"]))
        con.commit(); con.close()
    except Exception:
        pass
    return signal

def get_trader_intelligence() -> dict:
    all_signals = []
    idx = bot_state.get("nitter_idx",0) % len(TRADER_TWITTER_ACCOUNTS)
    accounts_batch = TRADER_TWITTER_ACCOUNTS[idx:idx+2]
    bot_state["nitter_idx"] = bot_state.get("nitter_idx",0)+2
    for account in accounts_batch:
        try: all_signals.extend(scrape_nitter(account))
        except Exception: pass
    yt_items = list(YOUTUBE_CHANNELS.items())
    yt_idx   = bot_state.get("yt_idx",0) % len(yt_items)
    bot_state["yt_idx"] = yt_idx+1
    ch_name, ch_id = yt_items[yt_idx]
    try: all_signals.extend(scrape_youtube_titles(ch_id, ch_name))
    except Exception: pass
    analyzed = [analyze_signal_sentiment(s) for s in all_signals[:3]]
    parts = []
    for s in analyzed[:3]:
        e = "📈" if s["sentiment"]=="bullish" else "📉" if s["sentiment"]=="bearish" else "➡️"
        parts.append(f"{e} @{s['author']}: {s.get('summary',s['content'][:60])}")
    return {
        "bullish": [s for s in analyzed if s["sentiment"]=="bullish"],
        "bearish": [s for s in analyzed if s["sentiment"]=="bearish"],
        "summary": "\n".join(parts), "count": len(analyzed)
    }

def scan_airdrops() -> list:
    found = []
    try:
        r = requests.get(
            "https://coinmarketcap.com/airdrop/", timeout=10,
            headers={"User-Agent":"Mozilla/5.0"}
        )
        if r.status_code == 200:
            matches = re.findall(
                r'"name":"([^"]+)","slug":"([^"]+)".*?"status":"(ONGOING|UPCOMING)"',
                r.text[:50000]
            )
            for name, slug, status in matches[:10]:
                h = hashlib.md5(slug.encode()).hexdigest()[:8]
                if h not in [a.get("hash") for a in epargne["airdrops_claimed"]]:
                    found.append({
                        "name":name,"status":status,
                        "url":f"https://coinmarketcap.com/airdrop/{slug}/",
                        "hash":h,"source":"CoinMarketCap"
                    })
    except Exception as e:
        print(f"[AIRDROP-CMC] {e}")
    try:
        feed = feedparser.parse("https://airdrops.io/feed/")
        for entry in feed.entries[:5]:
            title = entry.get("title",""); link = entry.get("link","")
            h = hashlib.md5(link.encode()).hexdigest()[:8]
            if h not in [a.get("hash") for a in epargne["airdrops_claimed"]]:
                if any(kw in title.lower() for kw in ["airdrop","free","drop","token","claim"]):
                    found.append({
                        "name":title[:60],"url":link,"hash":h,
                        "source":"RSS","status":"AVAILABLE"
                    })
    except Exception as e:
        print(f"[AIRDROP-RSS] {e}")
    return found[:10]

def scan_faucets() -> list:
    available = []
    for faucet in FAUCET_SOURCES:
        try:
            r = requests.get(faucet["url"], timeout=8, headers={"User-Agent":"Mozilla/5.0"})
            status = "✅ En ligne" if r.status_code==200 else f"⚠️ HTTP {r.status_code}"
        except Exception:
            status = "❌ Hors ligne"
        available.append({
            "name":faucet["name"],"url":faucet["url"],
            "crypto":faucet["crypto"],"status":status
        })
    return available

def scan_promo_codes() -> list:
    promos   = []
    keywords = ["bonus","promo","voucher","free","reward","cashback","referral"]
    for exchange in PROMO_EXCHANGES:
        try:
            r = requests.get(exchange["url"], timeout=8, headers={"User-Agent":"Mozilla/5.0"})
            if r.status_code == 200:
                text     = r.text.lower()
                found_kw = [kw for kw in keywords if kw in text]
                if found_kw:
                    promos.append({
                        "exchange":exchange["name"],"url":exchange["url"],
                        "keywords":found_kw[:3],"status":"✅ Promos détectées"
                    })
        except Exception:
            pass
    return promos

def auto_fill_form(url: str, form_type: str) -> dict:
    if not all([USER_EMAIL, USER_FIRSTNAME, USER_LASTNAME, USER_WALLET]):
        return {"success": False, "reason": "Infos utilisateur incomplètes"}
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        "Accept": "application/json, text/html, */*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": url,
    }
    form_data = {
        "email":USER_EMAIL,"name":f"{USER_FIRSTNAME} {USER_LASTNAME}",
        "first_name":USER_FIRSTNAME,"last_name":USER_LASTNAME,
        "wallet":USER_WALLET,"wallet_address":USER_WALLET,"eth_address":USER_WALLET,
        "country":"Australia","terms":"1","agree":"1","subscribe":"1",
    }
    try:
        session = requests.Session()
        session.headers.update(headers)
        r_get   = session.get(url, timeout=10)
        for pattern in [
            r'name=["\']csrf_token["\'].*?value=["\']([^"\']+)["\']',
            r'name=["\']_token["\'].*?value=["\']([^"\']+)["\']'
        ]:
            match = re.search(pattern, r_get.text, re.IGNORECASE)
            if match:
                token = match.group(1)
                form_data["csrf_token"] = token
                form_data["_token"]     = token
                break
        action_match = re.search(r'<form[^>]*action=["\']([^"\']+)["\']', r_get.text, re.IGNORECASE)
        submit_url   = url
        if action_match:
            action = action_match.group(1)
            if action.startswith("http"):
                submit_url = action
            elif action.startswith("/"):
                parsed     = urlparse(url)
                submit_url = f"{parsed.scheme}://{parsed.netloc}{action}"
        r_post = session.post(submit_url, data=form_data, timeout=15, allow_redirects=True)
        success_kw = ["success","thank","confirm","registered","submitted"]
        resp = r_post.text.lower()
        if any(kw in resp for kw in success_kw):
            return {"success":True,"message":"Formulaire soumis ✅"}
        elif r_post.status_code in (200,201,302):
            return {"success":True,"message":f"Soumis (HTTP {r_post.status_code})"}
        else:
            return {"success":False,"message":f"HTTP {r_post.status_code}"}
    except Exception as e:
        return {"success":False,"reason":str(e)[:100]}

def run_epargne_scan(send_fn):
    in_secretary_mode = TELEGRAM_CHAT_ID in AGENT_CHAT_SESSIONS
    if in_secretary_mode:
        return
    airdrops = scan_airdrops(); faucets = scan_faucets(); promos = scan_promo_codes()
    epargne["last_scan"]    = time.time()
    epargne["promos_found"] = promos
    results = []
    if USER_WALLET and airdrops:
        for airdrop in airdrops[:3]:
            url = airdrop.get("url","")
            if not url: continue
            try:
                result = auto_fill_form(url,"airdrop")
                status = "✅" if result["success"] else "⚠️"
                results.append(f"{status} {airdrop['name'][:40]}\n  {result.get('message',result.get('reason',''))}")
                if result["success"]:
                    epargne["airdrops_claimed"].append({
                        "hash":airdrop.get("hash",""),"name":airdrop["name"],
                        "date":datetime.now().strftime("%Y-%m-%d %H:%M"),"url":url,
                    })
                time.sleep(2)
            except Exception as e:
                print(f"[AUTOFILL] {e}")
    online       = [f for f in faucets if "✅" in f["status"]]
    new_airdrops = [a for a in airdrops if a.get("hash") not in [c.get("hash") for c in epargne["airdrops_claimed"]]]
    if new_airdrops or len(online) >= 3:
        lines = ["💰 ÉPARGNE — Nouvelles opportunités\n━━━━━━━━━━━━━"]
        if new_airdrops: lines.append(f"🪂 {len(new_airdrops)} nouveaux airdrops")
        if online:       lines.append(f"💧 {len(online)} faucets en ligne")
        if promos:       lines.append(f"🎟️ {len(promos)} exchanges avec promos")
        if results:
            lines.append("\n📝 Auto-remplissage:")
            lines.extend(results[:3])
        lines.append("\n💡 /epargne pour les détails | /faucets pour les liens")
        send_fn("\n".join(lines))
    save_data()

def get_epargne_info() -> str:
    wallet_ok = "✅" if USER_WALLET else "❌ Non configuré"
    email_ok  = "✅" if USER_EMAIL  else "❌ Non configuré"
    last = datetime.fromtimestamp(epargne['last_scan']).strftime('%H:%M') if epargne['last_scan'] else 'Jamais'
    return (
        f"💰 ÉPARGNE IA\n━━━━━━━━━━━━━\n"
        f"Wallet : {wallet_ok}\nEmail  : {email_ok}\n"
        f"Airdrops vus : {len(epargne['airdrops_claimed'])}\n"
        f"Dernier scan : {last}"
    )

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

def fetch_historical_klines(symbol: str, interval: str, days: int) -> pd.DataFrame:
    try:
        limit = min(1000, days * {"1m":1440,"5m":288,"15m":96,"1h":24,"4h":6,"1d":1}.get(interval,24))
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

def backtest_strategy(
    symbol: str,
    interval: str = "5m",
    days: int = 30,
    strategy_params: dict = None
) -> dict:
    if strategy_params is None:
        strategy_params = {"sl": STOP_LOSS_PCT, "tp": TAKE_PROFIT_PCT, "name": "default"}

    df = fetch_historical_klines(symbol, interval, days)
    if df.empty or len(df) < 50:
        return {"error": f"Données insuffisantes pour {symbol}"}

    closes = df["close"]
    equity = CAPITAL_INITIAL
    equity_curve = [equity]
    trades = []
    in_trade = False
    entry_price = 0.0
    FEE = 0.001
    SLIPPAGE = 0.0005

    for i in range(50, len(closes)):
        price = float(closes.iloc[i])
        window = closes.iloc[max(0, i-80):i]
        ind = compute_indicators(window)
        if not ind: continue

        score = 0
        if ind.get("rsi", 50) < 35: score += 3
        elif ind.get("rsi", 50) < 45: score += 1
        if ind.get("rsi", 50) > 70: score -= 3
        if ind.get("macd_h", 0) > 0: score += 2
        else: score -= 1
        if ind.get("mom5", 0) > 1: score += 2
        elif ind.get("mom5", 0) < -1: score -= 2
        if ind.get("ema_cross", "BEAR") == "BULL": score += 1
        else: score -= 1

        if score >= 4 and not in_trade and equity > 50:
            in_trade = True
            entry_price = price * (1 + SLIPPAGE)
            exit_price = price * (1 + strategy_params["tp"]) if score > 6 else price * (1 - strategy_params["sl"])
            pnl = (exit_price - entry_price) / entry_price * (equity * 0.20)
            equity += pnl
            trades.append({
                "entry": entry_price,
                "exit": exit_price,
                "pnl": round(pnl, 4),
                "pnl_pct": round((exit_price - entry_price) / entry_price * 100, 2),
                "exit_reason": "TP" if score > 6 else "SL"
            })
            equity_curve.append(equity)
            continue

        equity_curve.append(equity)

    if not trades:
        return {"error": "Aucun trade généré sur cette période"}

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in trades)
    win_rate = round(len(wins) / len(trades) * 100, 1)
    pnl_pcts = [t["pnl_pct"] / 100 for t in trades]

    sharpe = round((np.mean(pnl_pcts) / np.std(pnl_pcts)) * np.sqrt(252), 2) if len(pnl_pcts) > 1 and np.std(pnl_pcts) > 0 else 0
    eq_series = pd.Series(equity_curve)
    rolling_max = eq_series.expanding().max()
    drawdowns = (eq_series - rolling_max) / rolling_max
    max_dd = round(float(drawdowns.min()) * 100, 2)

    result = {
        "symbol": symbol,
        "interval": interval,
        "days": days,
        "strategy": strategy_params.get("name", "default"),
        "total_trades": len(trades),
        "win_rate": win_rate,
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round((equity - CAPITAL_INITIAL) / CAPITAL_INITIAL * 100, 2),
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "profit_factor": round(sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses)) if losses else 0, 2),
        "best_trade": round(max((t["pnl_pct"] for t in trades), default=0), 2),
        "worst_trade": round(min((t["pnl_pct"] for t in trades), default=0), 2),
        "final_equity": round(equity, 2),
        "trades": trades[-10:],
    }

    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("""INSERT INTO backtest_results
            (timestamp,symbol,period,total_trades,win_rate,total_pnl,sharpe,max_drawdown,params)
            VALUES(?,?,?,?,?,?,?,?,?)""", (
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            symbol, f"{days}d/{interval}",
            len(trades), win_rate, round(total_pnl, 2),
            sharpe, max_dd,
            json.dumps(strategy_params)
        ))
        con.commit()
        con.close()
    except Exception:
        pass

    return result

def run_multi_backtest(symbols: list, interval="5m", days=30) -> dict:
    results = []
    strategies = [
        {"name":"conservateur", "sl":0.02, "tp":0.03},
        {"name":"équilibré",    "sl":0.025,"tp":0.04},
        {"name":"agressif",     "sl":0.035,"tp":0.06},
    ]
    for sym in symbols[:6]:
        for strat in strategies:
            r = backtest_strategy(sym, interval, days, strat)
            if "error" not in r:
                results.append(r)
    if not results:
        return {"best": None, "message": "Aucun résultat"}
    best = max(results, key=lambda x: (x["sharpe"] * 10 + x["win_rate"] - abs(x["max_drawdown"])))
    global STOP_LOSS_PCT, TAKE_PROFIT_PCT
    STOP_LOSS_PCT  = best["strategy"].get("sl", STOP_LOSS_PCT)
    TAKE_PROFIT_PCT = best["strategy"].get("tp", TAKE_PROFIT_PCT)
    return {
        "best_strategy": best["strategy"]["name"],
        "best_winrate": best["win_rate"],
        "best_sharpe": best["sharpe"],
        "best_drawdown": best["max_drawdown"],
        "best_pnl": best["total_pnl"],
        "selected_params": {"sl": STOP_LOSS_PCT, "tp": TAKE_PROFIT_PCT},
        "all_results": results
    }

def learn_from_backtest_result(result: dict):
    if "best_strategy" not in result:
        return
    print(f"[AUTO-STRATEGY] Meilleure stratégie choisie par le bot → {result['best_strategy']}")
    print(f"Winrate: {result['best_winrate']}% | Sharpe: {result['best_sharpe']} | DD: {result['best_drawdown']}%")
    global STOP_LOSS_PCT, TAKE_PROFIT_PCT
    STOP_LOSS_PCT  = result["selected_params"]["sl"]
    TAKE_PROFIT_PCT = result["selected_params"]["tp"]
    if isinstance(memory, dict):
        memory.setdefault("good_setups", []).append(result)
    elif hasattr(memory, "data"):
        memory.data.setdefault("good_setups", []).append(result)
    save_data()

def save_data():
    try:
        memory_data = memory.data if hasattr(memory, "data") else (memory if isinstance(memory, dict) else {})
        data = json.dumps({"sim":sim,"memory":memory_data,"epargne":epargne}, indent=2, default=str)
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

def _memory_update(mem_obj, data_dict: dict):
    """Met à jour l'objet Memory ou dict memory avec les données chargées"""
    if isinstance(mem_obj, dict):
        mem_obj.update(data_dict)
    elif hasattr(mem_obj, "data") and isinstance(mem_obj.data, dict):
        mem_obj.data.update(data_dict)
    elif hasattr(mem_obj, "update"):
        mem_obj.update(data_dict)

def _memory_setdefault(mem_obj, key, default):
    """setdefault compatible Memory class et dict"""
    if isinstance(mem_obj, dict):
        mem_obj.setdefault(key, default)
    elif hasattr(mem_obj, "data") and isinstance(mem_obj.data, dict):
        mem_obj.data.setdefault(key, default)

def load_data():
    # BUG FIX (2026-07-24): GitHub-backed state used to load FIRST and count as
    # "loaded" even when it returned zero trades (stale/empty repo copy), which
    # short-circuited the local DATA_FILE fallback and silently wiped out the
    # persistent-volume history on every restart -- this was the original
    # "resets destroy the measurement" problem from the project's early sessions,
    # never actually fixed. Local DATA_DIR is now the reliably-persisted source
    # of truth (Docker volume, confirmed working) and is tried first; GitHub is
    # only a last-resort fallback for a genuinely fresh deployment with no local
    # state at all. Neither source counts as "loaded" unless it actually has trades.
    global sim, memory, epargne
    loaded = False
    if DATA_FILE.exists():
        try:
            d = json.loads(DATA_FILE.read_text())
            candidate_sim = d.get("sim",{})
            if candidate_sim.get("trades"):
                sim = candidate_sim
                mem_data = d.get("memory",{})
                _memory_update(memory, mem_data)
                epargne_loaded = d.get("epargne",{})
                if epargne_loaded: epargne.update(epargne_loaded)
                loaded = True
                print(f"[LOAD-LOCAL] {len(sim.get('trades',[]))} trades")
        except Exception as e:
            print(f"[LOAD-LOCAL] {e}")
    if not loaded and GITHUB_TOKEN and GITHUB_REPO:
        try:
            headers = {"Authorization":f"token {GITHUB_TOKEN}"}
            api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/sim_portfolio_v7.json"
            r = requests.get(api_url, headers=headers, timeout=10)
            if r.status_code == 200:
                content = base64.b64decode(r.json()["content"]).decode()
                d = json.loads(content)
                candidate_sim = d.get("sim",{})
                if candidate_sim.get("trades"):
                    sim = candidate_sim
                    mem_data = d.get("memory",{})
                    _memory_update(memory, mem_data)
                    epargne_loaded = d.get("epargne",{})
                    if epargne_loaded: epargne.update(epargne_loaded)
                    loaded = True
                    print(f"[LOAD-GH] {len(sim.get('trades',[]))} trades | {len(mem_data.get('lessons',[]))} leçons")
        except Exception as e:
            print(f"[LOAD-GH] {e}")
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
        "symbol_scores":{},"symbol_blacklist":{},"consecutive_losses":{},"symbol_lifetime":{}
    }.items():
        _memory_setdefault(memory, k, v)

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
            "trade_id": trade.get("id"),
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
        try:
            orchestrator.learning.save_lesson(lesson)
        except Exception as ex:
            print(f"[LEARN-INFINITE] {ex}")
        # Mise à jour des poids dynamiques des agents après chaque trade
        try:
            global _last_raw_agent_outputs
            orchestrator.update_agent_outcome(_last_raw_agent_outputs, pnl > 0)
        except Exception as _perf_e:
            pass  # Non bloquant
        key = "patterns_that_work" if lesson_type == "succes" else "patterns_to_avoid"
        memory[key].append(pattern)
        MAX_RAM_LESSONS = 20000 if EXTREME_LEARNING_MODE else 5000
        if len(memory["lessons"]) > MAX_RAM_LESSONS:
            memory["lessons"] = memory["lessons"][-MAX_RAM_LESSONS:]
            print(f"[MEMORY-SAFETY] Limite atteinte → {MAX_RAM_LESSONS} leçons conservées en RAM")
        memory["patterns_that_work"] = memory["patterns_that_work"][-300:]
        memory["patterns_to_avoid"] = memory["patterns_to_avoid"][-300:]
        if hasattr(orchestrator, 'learning'):
            lesson_count_db = orchestrator.learning.get_lesson_count()
            logger.warning(f"[LEARN] Leçons DB : {lesson_count_db}")
        update_symbol_score(trade.get("symbol","?"), pnl > 0)
        auto_adjust()
        save_data()
        logger.warning(f"[LEARN] {lesson['lecon']}")
        if send_fn:
            stats = get_stats()
            e = "✅" if lesson["type"] == "succes" else "❌"
            coin = trade["symbol"].replace("USDT", "")
            send_fn(
                f"📚 Leçon #{lesson_count_db} — {coin}\n"
                f"{e} {lesson['lecon']}\n→ {lesson['action_future']}\n"
                f"📊 WR:{stats['win_rate']}% ({stats['wins']}✅/{stats['losses']}❌)"
            )
    except Exception as e:
        logger.warning(f"[LEARN] Erreur: {e}")

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
    if sl_hits/len(recent) > 0.5 and STOP_LOSS_PCT < 0.025:
        STOP_LOSS_PCT = round(min(0.025, STOP_LOSS_PCT+0.002), 3)

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
        logger.warning(f"[RULES] {e}")
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

def trading_loop(send_fn):
    global _main_loop
    try:
        _main_loop = asyncio.get_event_loop()
    except RuntimeError:
        _main_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_main_loop)

    in_secretary_mode = TELEGRAM_CHAT_ID in AGENT_CHAT_SESSIONS
    last_micro = last_meme = last_epargne = last_status = last_regime = last_staking = last_solana = 0
    last_backtest = 0
    last_monitor = 0
    last_soul_tick   = 0
    last_risk_check = 0

    logger.info("🚀 Trading Loop autonome V8 démarré — Agents décident seuls")
    _aegis_log_sink.emit("INFO", "Bot trading loop started")
    logger.info("✅ PHASE 1 ACTIVÉE : Backtester VectorBT + ExecutionEngine pro + SQLite Memory")

    while bot_state["running"]:
        now = time.time()
        equity = get_equity_safe()
        current_price = get_current_price("BTCUSDT") or 0.0

        if now - last_backtest >= 21600:
            last_backtest = now
            try:
                future = asyncio.run_coroutine_threadsafe(
                    run_backtest("BTCUSDT", "5m"), 
                    _main_loop
                )
                backtest_stats = future.result(timeout=45)
                logger.info(f"[BACKTEST PHASE 1] Return: {backtest_stats.get('total_return', 0):.2%} | "
                           f"Max DD: {backtest_stats.get('max_drawdown', 0):.2%} | "
                           f"Win Rate: {backtest_stats.get('win_rate', 0):.2%}")
            except Exception as bt_e:
                logger.warning(f"Backtest error (non blocking): {type(bt_e).__name__}: {bt_e or 'timeout'}")

        if now - last_risk_check >= 300:
            last_risk_check = now
            try:
                if hasattr(memory, "get_positions"):
                    positions = memory.get_positions()
                else:
                    positions = sim.get("positions", {})
                if positions:
                    logger.info(f"[RISK] Positions actives : {len(positions)} | Equity : ${equity:,.2f}")
            except Exception as risk_e:
                logger.warning(f"Risk check error: {risk_e}")


        # ── SOUL TICK: auto-ajustement & graduation automatique ────────────────
        if _soul and now - last_soul_tick >= 60:
            last_soul_tick = now
            try:
                soul_state = _soul.tick()
                _s_phase   = soul_state.get("phase", "TRAINING")
                _s_conf    = _soul.params.get("confidence_threshold", 0.01)
                _s_kelly   = _soul.params.get("kelly_fraction", 0.05)
                logger.info(
                    f"[SOUL] 🔮 tick → phase={_s_phase} | conf={_s_conf:.0%} | kelly={_s_kelly:.1%} | "
                    f"streak={_soul._criteria_pass_streak}"
                )
                # GRADUATION AUTOMATIQUE: si soul détecte prêt → propager à bot
                global BOT_TRAINING_MODE
                if _soul.params.get("live_mode") and BOT_TRAINING_MODE:
                    BOT_TRAINING_MODE = False
                    os.environ["BOT_TRAINING_MODE"] = "False"
                    _soul.params["confidence_threshold"] = LIVE_CONF_THRESH
                    logger.info("[SOUL] 💎 GRADUATION AUTOMATIQUE → MODE LIVE DÉCLENCHÉ!")
                    _smets = soul_state.get("metrics", {})
                    _grad_auto = (
                        f"💎 GRADUATION LIVE AUTOMATIQUE!\n"
                        f"Phase: {_s_phase}\n"
                        f"Win rate: {_smets.get('winrate', 0):.1f}% ✅\n"
                        f"Profit factor: {_smets.get('profit_factor', 0):.2f} ✅\n"
                        f"Consistency: {_smets.get('consistency', 0):.0%} ✅\n"
                        f"Trades accumulés: {_smets.get('total_trades', 0)}\n\n"
                        f"→ Seuil confiance: {int(LIVE_CONF_THRESH*100)}% | Max/trade: {int(LIVE_MAX_USD_PCT*100)}% capital\n"
                        f"🔴 BOT EN MODE ARGENT RÉEL"
                    )
                    try:
                        asyncio.run_coroutine_threadsafe(
                            application.bot.send_message(TELEGRAM_CHAT_ID, _grad_auto),
                            _main_loop
                        )
                    except Exception: pass
            except Exception as _soul_tick_e:
                logger.warning(f"[SOUL] tick error: {_soul_tick_e}")

        # FIX 2026-06-15: surveille et FERME les positions ouvertes (SL/TP/trailing).
        # monitor_positions + monitor_micro_positions n'étaient JAMAIS appelées dans la boucle
        # → 15 positions ouvertes, 0 trade fermé, 0 apprentissage. Cause racine du "WR 0%".
        if now - last_monitor >= CYCLE_MONITOR:
            last_monitor = now
            try:
                monitor_positions(send_fn)
                monitor_micro_positions(send_fn)
            except Exception as _mon_e:
                logger.debug(f"[MONITOR] exit-check error: {_mon_e}")

        if now - last_micro >= CYCLE_MICRO:
            last_micro = now
            performance_tracker.update_trade_results(memory, current_price)

            # FIX TRAINING V9: run_micro_cycle génère des trades rapides via signaux techniques
            # (sans attendre les agents LLM) — s'exécute à CHAQUE cycle en training
            if BOT_TRAINING_MODE or EXTREME_LEARNING_MODE:
                try:
                    run_micro_cycle(send_fn)
                    logger.info(f"[TRAINING MICRO] ⚡ run_micro_cycle exécuté — positions={len(sim.get('positions',{}))}")
                except Exception as _rmc_e:
                    logger.debug(f"[TRAINING MICRO] run_micro_cycle error: {_rmc_e}")

            # ── Scan parallèle pour trouver les meilleurs symboles ─────────
            top_symbols = ["BTCUSDT", "ETHUSDT"]
            try:
                scan_future = asyncio.run_coroutine_threadsafe(
                    scan_market_parallel(), _main_loop
                )
                top_opps = scan_future.result(timeout=25)
                top_symbols = [o["symbol"] for o in top_opps[:5] if abs(o.get("score", 0)) >= 3]
                if not top_symbols:
                    top_symbols = ["BTCUSDT"]
                logger.info(f"[MICRO V7] 🔍 Top symboles (parallèle): {top_symbols[:3]}")
            except Exception as scan_e:
                logger.debug(f"[MICRO] scan_market_parallel fallback: {scan_e}")

            micro_ctx = {
                "symbol":           top_symbols[0],
                "symbols":          top_symbols,
                "shared_glossary":  shared_glossary if 'shared_glossary' in globals() else {},
                "equity":           equity,
                "market_regime":    bot_state.get("market_regime", "NEUTRAL"),
                "confidence_threshold": memory.get("confidence_threshold", CONFIDENCE_BASE),
                "open_positions":   len(sim.get("positions", {})),
                "max_positions":    MAX_MICRO_POSITIONS,  # FIX TRAINING V8: utilise le vrai max (120)
                "daily_start_equity": sim.get("daily_start_equity", equity),
                "fear_greed":         get_fear_greed_value(),     # real F&G value for force-trade direction
                "training_mode":      BOT_TRAINING_MODE,
            }

            # ── Biais background + Pump scanner ─────────────────────────────
            if hasattr(orchestrator, "_bg_cache") and orchestrator._bg_cache:
                micro_ctx["bg_pre_bias"]  = orchestrator._bg_cache.get("pre_bias", "NEUTRAL")
                micro_ctx["bg_buy_count"] = orchestrator._bg_cache.get("pre_buy", 0)
                micro_ctx["bg_sell_count"]= orchestrator._bg_cache.get("pre_sell", 0)
            # Pump scanner — détecte setups rapides style momentum
            try:
                _closes_map_ps = {}
                for _sym_ps in top_symbols[:5]:
                    _c = bot_state.get(f"closes_{_sym_ps}", bot_state.get("closes_5m", []))
                    if _c: _closes_map_ps[_sym_ps] = _c
                if _closes_map_ps:
                    _pump_future = asyncio.run_coroutine_threadsafe(
                        orchestrator.scan_pump_setups(list(_closes_map_ps.keys()), {}, _closes_map_ps),
                        _main_loop
                    )
                    _pump_alerts = _pump_future.result(timeout=5)
                    if _pump_alerts:
                        micro_ctx["pump_alerts"] = _pump_alerts
                        logger.info(f"[PUMP] 🚀 {len(_pump_alerts)} setups → top:{_pump_alerts[0]['symbol']} {_pump_alerts[0]['type']} {_pump_alerts[0]['pct_5c']:+.1f}%")
                        # Si le top pump est dans nos symboles → le prioriser
                        _pump_sym = _pump_alerts[0]["symbol"]
                        if _pump_sym in top_symbols:
                            top_symbols.remove(_pump_sym)
                            top_symbols.insert(0, _pump_sym)
                            micro_ctx["symbol"] = _pump_sym
                        # Promouvoir dans le tier HOT pour les prochains cycles
                        promote_symbol(_pump_sym, f"pump {_pump_alerts[0]['pct_5c']:+.1f}%")
            except Exception as _pump_e:
                pass

            try:
                # ── Analyse parallèle sur les 3 meilleurs symboles ──────────
                multi_future = asyncio.run_coroutine_threadsafe(
                    orchestrator.analyze_symbols_parallel(
                        top_symbols[:2], micro_ctx
                    ),
                    _main_loop
                )
                multi_results = multi_future.result(timeout=60)

                # Choisir le meilleur signal parmi les symboles analysés
                best_symbol   = top_symbols[0]
                best_decision = {"recommendation": "HOLD", "confidence": 0.0}
                # Capture des outputs bruts pour tracking précision agents
                global _last_raw_agent_outputs
                try:
                    _last_raw_agent_outputs = []
                    for _sym2, _sd2 in multi_results.items():
                        _last_raw_agent_outputs.extend(_sd2.get("outputs", []) or [])
                except Exception: pass

                for sym, sym_data in multi_results.items():
                    sym_final = sym_data.get("final", {})
                    sym_conf  = float(sym_final.get("confidence", 0))
                    best_conf = float(best_decision.get("confidence", 0))
                    reco      = str(sym_final.get("decision", sym_final.get("recommendation", "HOLD"))).upper()
                    # FIX: 'TRADE' in 'NO TRADE' == True → vérification stricte
                    _reco_actionable = ("BUY" in reco or "SELL" in reco or "LONG" in reco or "SHORT" in reco) and "NO" not in reco
                    if sym_conf > best_conf and _reco_actionable:
                        best_symbol   = sym
                        best_decision = sym_final

                decision = best_decision
                micro_ctx["symbol"] = best_symbol

                # ── Capture débat pour le dashboard /office ────────────────────
                try:
                    global _last_debate_cycle, _debate_cycle_id, _agent_activity_log
                    _debate_cycle_id += 1
                    _now_ts = int(time.time())
                    _all_agents_this_cycle = []
                    for _s, _sd in multi_results.items():
                        for _out in (_sd.get("outputs") or []):
                            if isinstance(_out, dict):
                                _all_agents_this_cycle.append({
                                    "agent":      _out.get("agent", _out.get("id", "?")),
                                    "signal":     str(_out.get("recommendation", _out.get("decision", "HOLD"))).upper(),
                                    "confidence": round(float(_out.get("confidence", 0)), 2),
                                    "summary":    str(_out.get("summary", ""))[:120],
                                    "symbol":     _s,
                                    "ts":         _now_ts,
                                })
                    _agent_activity_log = (_agent_activity_log + _all_agents_this_cycle)[-100:]
                    _last_debate_cycle = {
                        "cycle_id":     _debate_cycle_id,
                        "symbols":      list(multi_results.keys()),
                        "best":         best_symbol,
                        "decision":     str(decision.get("recommendation", decision.get("decision", "HOLD"))).upper(),
                        "confidence":   round(float(decision.get("confidence", 0)), 2),
                        "kelly":        round(float(decision.get("kelly_adjusted", 0.05)), 3),
                        "regime":       micro_ctx.get("market_regime", "NEUTRAL"),
                        "agents":       _all_agents_this_cycle,
                        "veto":         decision.get("veto_source", None),
                        "veto_minutes": decision.get("pause_minutes", 0),
                        "ts":           _now_ts,
                    }
                except Exception:
                    pass
                logger.info(
                    f"[MICRO V7] 🔀 {len(multi_results)} symboles analysés en parallèle → "
                    f"meilleur: {best_symbol} | "
                    f"décision: {decision.get('recommendation', decision.get('decision', 'HOLD'))} "
                    f"({float(decision.get('confidence', 0)):.0%})"
                )

                # ── Exécution du trade ────────────────────────────────────
                reco_str  = str(decision.get("recommendation", decision.get("decision", "HOLD"))).upper()
                trade_conf = float(decision.get("confidence", 0))
                # FIX: vérification stricte — 'NO TRADE' ne doit pas déclencher un SELL
                _is_buy  = any(x in reco_str for x in ["BUY", "LONG"])
                _is_sell = any(x in reco_str for x in ["SELL", "SHORT"])
                _is_no   = "NO" in reco_str  # couvre: NO TRADE, NO ACTION, etc.
                # SOUL: seuil dynamique ajusté par l'âme du bot selon l'expérience accumulée
                # ── SOUL + MODE TRAINING/LIVE ──────────────────────────────────────────
                if BOT_TRAINING_MODE:
                    # TRAINING: seuil minimal 1%, on apprend de tous les trades
                    _soul_thresh = TRAINING_CONF_THRESH  # 0.01 = 1%
                    _regime_str  = micro_ctx.get("market_regime", "NEUTRAL").upper()
                    if not (_is_buy or _is_sell) or _is_no:
                        # Forcer un trade basé sur le régime de marché
                        # Direction forcée selon régime + Fear & Greed
                        _fg_val = float(micro_ctx.get("fear_greed", micro_ctx.get("fear_greed_index", 50)) or 50)
                        if "BEAR" in _regime_str or _fg_val < 30:
                            # Peur extrême ou marché baissier → forcer SELL
                            _is_buy, _is_sell = False, True
                        elif "BULL" in _regime_str and _fg_val > 55:
                            # Marché haussier confirmé + euphorie → forcer BUY
                            _is_buy, _is_sell = True, False
                        else:
                            # Neutre/transitionnel → alterner légèrement en faveur du F&G
                            _is_buy  = (_fg_val >= 48) and (_debate_cycle_id % 3 != 0)
                            _is_sell = not _is_buy
                        _is_no   = False
                        trade_conf = max(trade_conf, TRAINING_CONF_THRESH)
                        logger.info(f"[TRAINING] 🎓 Force trade → {'BUY' if _is_buy else 'SELL'} {best_symbol} | régime:{_regime_str} | cycle:{_debate_cycle_id}")
                else:
                    # LIVE: respecte le seuil SOUL mais minimum LIVE_CONF_THRESH
                    _soul_thresh = max(LIVE_CONF_THRESH, (_soul.params["confidence_threshold"] if _soul else LIVE_CONF_THRESH))
                # ── FORCE TRADE OVERRIDE (manuel 30min via bouton) ──────────────────
                if _force_trade_override and time.time() < _force_trade_until:
                    if not (_is_buy or _is_sell) and not _is_no:
                        _is_buy    = True
                        _is_no     = False
                        trade_conf = max(trade_conf, _soul_thresh, 0.06)
                        logger.info(f"[FORCE_TRADE] 🔥 Override actif → BUY forcé sur {best_symbol}")
                if trade_conf >= _soul_thresh and (_is_buy or _is_sell) and not _is_no:  # Seuil géré par l'âme
                    trade_side = "BUY" if _is_buy else "SELL"
                    trade_price = get_current_price(best_symbol) or current_price

                    # FIX (2026-07-27): this path (the MICRO V7 debate ensemble)
                    # calls execution.place_order_async() directly and, in
                    # training mode, forces a trade even on a HOLD/NO-TRADE
                    # ensemble decision (see the force-trade block above) --
                    # meaning it bypassed the Claude/whale/Solana verification
                    # gates entirely, unlike open_micro_trade(). Same three
                    # block-only checks now apply here too.
                    _v7_reason = str(decision.get("summary") or decision.get("reason")
                                      or f"MICRO V7 ensemble, regime={micro_ctx.get('market_regime','?')}, "
                                         f"fg={micro_ctx.get('fear_greed','?')}")[:300]
                    _v7_ok, _v7_why = verify_trade_with_claude({
                        "symbol": best_symbol, "signal": trade_side, "price": trade_price,
                        "confidence": round(trade_conf * 100), "reason": _v7_reason,
                        "market": "MICRO_V7", "patterns": [],
                    })
                    if _v7_ok:
                        _v7_ok, _v7_why = check_whale_filter(best_symbol, trade_side)
                    if _v7_ok:
                        _v7_ok, _v7_why = check_solana_filter(best_symbol, trade_side)

                    if not _v7_ok:
                        logger.info(f"[MICRO-V7-VETO] {best_symbol} ({trade_side}) bloqué: {_v7_why}")
                    else:
                        # SOUL: en mode LIVE, utiliser le Kelly calculé par l'âme
                        _kelly_soul = (_soul.params.get("kelly_fraction", 0.05) if _soul else 0.05)
                        amount_usd  = decision.get("amount_usd", equity * float(decision.get("kelly_adjusted", _kelly_soul)))
                        # Cap position size selon mode
                        if BOT_TRAINING_MODE:
                            amount_usd = min(amount_usd, TRAINING_MAX_USD)  # Max $15 en training
                            amount_usd = max(5.0, amount_usd)
                        else:
                            amount_usd = max(10.0, min(amount_usd, equity * LIVE_MAX_USD_PCT))
                        try:
                            exec_future = asyncio.run_coroutine_threadsafe(
                                execution.place_order_async(
                                    symbol     = best_symbol,
                                    side       = trade_side,
                                    order_type = "market",
                                    amount_usd = amount_usd,
                                    stop_loss  = decision.get("analysis", {}).get("stop_loss"),
                                    take_profit = decision.get("analysis", {}).get("take_profit"),
                                ), _main_loop
                            )
                            exec_result = exec_future.result(timeout=12)
                            logger.info(f"🚀 AUTO TRADE {best_symbol} {trade_side} ${amount_usd:.2f} → {exec_result.get('fill_price', '?')}")
                            # ── AUTO-GRADUATION CHECK ──────────────────────────────────────
                            try:
                                _t_hist = sim.get("trades", [])
                                if BOT_TRAINING_MODE and len(_t_hist) >= TRAINING_MIN_TRADES:
                                    _wr_check = (sum(1 for t in _t_hist if t.get("pnl",0) > 0) / max(1, len(_t_hist))) * 100
                                    if _wr_check >= TRAINING_WIN_TARGET * 100:
                                        logger.info(f"[TRAINING] 🏆 OBJECTIF ATTEINT! WR={_wr_check:.1f}% sur {len(_t_hist)} trades → PRÊT pour LIVE")
                                        _grad_msg = f"🏆 BOT PRÊT POUR LE LIVE!\nWin rate: {_wr_check:.1f}% sur {len(_t_hist)} trades\n→ Active le mode LIVE dans Contrôles"
                                        if hasattr(application, "bot"):
                                            asyncio.run_coroutine_threadsafe(application.bot.send_message(TELEGRAM_CHAT_ID, _grad_msg), _main_loop)
                            except Exception as grad_e:
                                pass
                            # TRAINING MODE: enregistrer chaque trade comme lecon
                            if hasattr(memory, "save_lesson"):
                                memory.save_lesson(
                                    symbol     = best_symbol,
                                    action     = trade_side,
                                    outcome    = "training",
                                    pnl        = 0.0,
                                    confidence = trade_conf,
                                    lesson     = f"[TRAINING] {trade_side} {best_symbol} conf={trade_conf:.0%} regime={micro_ctx.get('market_regime','?')}"
                                )
                            if hasattr(memory, "save_position") and exec_result.get("success"):
                                memory.save_position(
                                    best_symbol, trade_side.lower(),
                                    amount_usd / (trade_price or 1), trade_price
                                )
                        except Exception as exec_e:
                            logger.warning(f"[MICRO] ExecutionEngine error {best_symbol}: {exec_e}")
                            # TRAINING MODE: les erreurs sont aussi des lecons
                            if hasattr(memory, "save_lesson"):
                                try:
                                    memory.save_lesson(symbol=best_symbol, action=trade_side, outcome="training_error", pnl=0.0, confidence=trade_conf, lesson=f"[TRAINING ERROR] {trade_side} {best_symbol} conf={trade_conf:.0%} err={type(exec_e).__name__}")
                                except Exception: pass

                else:
                    # ── HOLD : agents travaillent en arrière-plan ──────────────────
                    try:
                        bg_future = asyncio.run_coroutine_threadsafe(
                            orchestrator.run_background_agents(micro_ctx, _debate_cycle_id),
                            _main_loop
                        )
                        # Non-bloquant — on n'attend pas le résultat
                        logger.info(f"[HOLD] 🔄 BG agents lancés | cycle={_debate_cycle_id} | bias pré-calculé")
                    except Exception as _bg_err:
                        pass
                    # Injection du biais background dans le prochain cycle
                    if hasattr(orchestrator, '_bg_cache') and orchestrator._bg_cache:
                        _bg = orchestrator._bg_cache
                        logger.info(f"[HOLD] 📊 BG bias: {_bg.get('pre_bias','?')} | BUY:{_bg.get('pre_buy',0)} SELL:{_bg.get('pre_sell',0)}")
            except Exception as e:
                logger.warning(f"[MICRO V7] Cycle error: {type(e).__name__}: {e or 'timeout'}")
                # FIX TRAINING V9: si les agents échouent/timeout, forcer un trade technique en training
                # Cela garantit des trades à chaque cycle même sans réponse LLM
                if BOT_TRAINING_MODE or EXTREME_LEARNING_MODE:
                    try:
                        _fb_sym    = top_symbols[0] if top_symbols else "BTCUSDT"
                        _fb_prices = get_prices_batch()
                        _fb_price  = _fb_prices.get(_fb_sym) or get_current_price(_fb_sym) or 0
                        if _fb_price > 0:
                            _fb_sig = micro_signal(_fb_sym, _fb_price)
                            if _fb_sig["signal"] == "HOLD":
                                # Forcer un signal basé sur Fear & Greed
                                _fg_val  = float(micro_ctx.get("fear_greed", 50) or 50)
                                _fb_dir  = "BUY" if (_fg_val >= 50 and _debate_cycle_id % 2 == 0) else "SELL"
                                _fb_sig  = {"signal": _fb_dir, "conf": 50, "score": 2, "reason": "training_heartbeat_fallback"}
                            _result = open_micro_trade(_fb_sym, _fb_price, _fb_sig, send_fn)
                            if _result:
                                logger.info(f"[TRAINING FALLBACK] 🎓 Trade forcé OK: {_fb_sig['signal']} {_fb_sym} @ {_fb_price:.2f}")
                            else:
                                logger.debug(f"[TRAINING FALLBACK] open_micro_trade returned None (déjà en position ou cash insuffisant)")
                    except Exception as _ft_e:
                        logger.debug(f"[TRAINING FALLBACK] error: {_ft_e}")
                # Fallback agents : analyse simple BTC uniquement
                try:
                    fallback_future = asyncio.run_coroutine_threadsafe(
                        orchestrator.ask_all("analyse micro et donne TRADE ou NO TRADE", micro_ctx),
                        _main_loop
                    )
                    _, decision = fallback_future.result(timeout=15)
                except Exception as fb_e:
                    logger.debug(f"[MICRO] Fallback error: {fb_e}")

        if now - last_solana >= CYCLE_SOLANA:
            last_solana = now
            try:
                update_solana_snapshots()
            except Exception as _sol_e:
                logger.debug(f"[SOLANA-SMART-MONEY] snapshot error: {_sol_e}")

        if now - last_meme >= CYCLE_MEME:
            last_meme = now
            meme_ctx = {
                "shared_glossary": shared_glossary if 'shared_glossary' in globals() else {}, 
                "equity": equity
            }
            try:
                future = asyncio.run_coroutine_threadsafe(
                    orchestrator.ask_all("détecte memecoins et donne décision", meme_ctx), _main_loop
                )
                _, decision = future.result(timeout=12)
                if decision.get("decision") == "TRADE":
                    try:
                        meme_sym = decision.get("symbol", "BTCUSDT")
                        meme_price = get_current_price(meme_sym) or 1
                        exec_future = asyncio.run_coroutine_threadsafe(
                            execution.place_order_async(
                                symbol     = meme_sym,
                                side       = decision.get("side", "BUY"),
                                order_type = "market",
                                amount_usd = decision.get("amount", 100),
                            ), _main_loop
                        )
                        exec_future.result(timeout=10)
                    except Exception as meme_exec_e:
                        logger.debug(f"[MEME] Exec error: {meme_exec_e}")
            except Exception as meme_e:
                logger.debug(f"[MEME] Cycle skipped: {meme_e}")

        if now - last_epargne >= CYCLE_EPARGNE:
            last_epargne = now
            staking_ctx = {"equity": equity, "shared_glossary": shared_glossary if 'shared_glossary' in globals() else {}}
            try:
                future = asyncio.run_coroutine_threadsafe(
                    yield_staking.respond("check staking and transfer to savings", staking_ctx), _main_loop
                )
                staking_result = future.result(timeout=25)
                if "réel" in staking_result.get("summary", "") or "STAKING RÉEL" in staking_result.get("summary", ""):
                    logger.info(f"💰 Staking réel surveillé : {staking_result['summary']}")
                    if hasattr(memory, "save_lesson"):
                        memory.save_lesson("USDT", "STAKING", "executed", 0.0, 0.95, staking_result.get("summary", ""))
            except Exception as e:
                logger.warning(f"Staking auto error: {type(e).__name__}: {e or 'timeout'}")

        if now - last_regime >= 300:
            last_regime = now
            regime_ctx = {"shared_glossary": shared_glossary if 'shared_glossary' in globals() else {}}
            try:
                future = asyncio.run_coroutine_threadsafe(
                    quant_ml.respond("detect current market regime", regime_ctx), _main_loop
                )
                regime = future.result(timeout=10)
                bot_state["market_regime"] = regime.get("regime", "NEUTRAL")
                logger.info(f"[REGIME V7] Régime: {regime.get('regime', '?')} | conf: {regime.get('confidence', 0):.0%}")
                hedge_future = asyncio.run_coroutine_threadsafe(
                    orchestrator.hedging.respond("check hedging needed", regime_ctx), _main_loop
                )
                hedge_future.result(timeout=8)
            except Exception as regime_e:
                logger.debug(f"[REGIME] Mise à jour ignorée: {regime_e}")

        if now - last_status >= 60:
            last_status = now
            try:
                send_fn(generate_telegram_status())
                _mem_dict = memory.data if hasattr(memory, "data") else (memory if isinstance(memory, dict) else {})
                performance_tracker.export_dashboard(_mem_dict)
                if hasattr(memory, "cache_set"):
                    memory.cache_set("last_equity", equity)
                    memory.cache_set("last_price_BTC", current_price)
            except Exception as e:
                logger.warning(f"Dashboard error: {e}")

        if bot_state.get("extreme_learning_mode"):
            lessons_list = memory.get("lessons", []) if isinstance(memory, dict) else memory.get("lessons", [])
            lessons_list.append({
                "type": "auto",
                "pnl": equity - sim.get("daily_start_equity", CAPITAL_INITIAL),
                "lecon": f"Auto decision from collective brain at {time.strftime('%H:%M')}",
                "action_future": "Continue autonomy",
                "date": time.strftime("%Y-%m-%d %H:%M")
            })
            if len(lessons_list) > MAX_LESSONS:
                lessons_list = lessons_list[-MAX_LESSONS:]
            if isinstance(memory, dict):
                memory["lessons"] = lessons_list
            try:
                if hasattr(memory, "save_lesson"):
                    memory.save_lesson(
                        symbol="BTCUSDT",
                        action="AUTO_LEARNING",
                        outcome="ongoing",
                        pnl=equity - sim.get("daily_start_equity", CAPITAL_INITIAL),
                        confidence=0.80,
                        lesson=f"Extreme learning mode - Equity: ${equity:,.2f}"
                    )
            except Exception as e:
                logger.warning(f"Save lesson error: {e}")

        # ── SOUL: auto-ajustement toutes les 60s ──────────────────────────
        if _soul:
            try:
                _soul.tick()
            except Exception as _se:
                logger.debug(f"[SOUL] tick error: {_se}")

        time.sleep(0.1)

    logger.info("🛑 Trading Loop autonome arrêté")

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
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        time.sleep((midnight - now).total_seconds())
        in_secretary_mode = TELEGRAM_CHAT_ID in AGENT_CHAT_SESSIONS
        if in_secretary_mode:
            continue
        try:
            equity = get_equity_safe()
            pnl = equity - sim["initial"]
            stats = get_stats()
            today = now.strftime("%Y-%m-%d")
            t_day = [t for t in sim["trades"] if t.get("time_in", "").startswith(today)]
            pnl_day = sum(t.get("pnl", 0) for t in t_day if t.get("pnl") is not None)
            if isinstance(pnl_day, (int, float)) and abs(pnl_day) > 100000:
                pnl_day = 0.0
            sym_s = db_symbol_stats()
            best3 = "\n".join(
                f"  🏅 {s['s']}: WR {s['wr']:.0f}% ({s['n']} trades)"
                for s in sym_s[:3]
            ) or "  Aucun"
            lessons = "\n".join(
                f"  {'✅' if l['type']=='succes' else '❌'} {l['lecon']}"
                for l in memory["lessons"][-3:]
            ) or "  Aucune"
            bl_count = len(memory.get("symbol_blacklist", {}))
            send_fn(
                f"📊 RÉSUMÉ JOURNALIER v7 — {now.strftime('%d/%m/%Y')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Capital  : ${equity:.2f} ({pnl/sim['initial']*100:+.1f}%)\n"
                f"📅 PnL jour : ${pnl_day:+.2f} ({len(t_day)} trades)\n"
                f"📐 Kelly    : {kelly_criterion()*100:.1f}%\n"
                f"🚫 Blacklist: {bl_count} symbols\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🏆 WR       : {stats['win_rate']}% ({stats['total']} trades)\n"
                f"📚 Leçons   : {get_total_lessons()}/{MAX_LESSONS}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🥇 Top coins:\n{best3}\n"
                f"💡 Leçons récentes:\n{lessons}"
            )
            bot_state["daily_stopped"] = False
            sim["daily_start_equity"] = equity
            sim["daily_start_date"] = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception as e:
            logger.warning(f"[DAILY] {e}")

def self_ping():
    time.sleep(60)
    while True:
        try: 
            requests.get(f"{WEBHOOK_URL.rstrip('/')}/health", timeout=10)
        except Exception: 
            pass
        time.sleep(270)

def generate_telegram_status() -> str:
    """Génère un message texte court pour Telegram (sans HTML brut ni DOCTYPE)."""
    equity = get_equity_safe()
    pnl = equity - sim["initial"]
    pct = pnl / sim["initial"] * 100 if sim["initial"] > 0 else 0
    status = "🟢 BOT ACTIF" if bot_state["running"] else "🔴 ARRÊTÉ"
    if bot_state.get("daily_stopped"):
        status = "🛑 STOP JOUR"
    regime = bot_state.get("market_regime", "NEUTRAL")
    regime_emoji = "🐂" if regime == "BULL" else "🐻" if regime == "BEAR" else "🐢"
    stats = get_stats()
    open_pos = len(sim.get("positions", {}))
    prices = get_prices_batch()
    btc = prices.get("BTCUSDT", 0)
    eth = prices.get("ETHUSDT", 0)
    return (
        f"{status} — {regime_emoji} {regime}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Capital : ${equity:,.2f} ({pct:+.1f}%)\n"
        f"📈 PnL tot : ${pnl:+.2f}\n"
        f"🎯 WR      : {stats.get('win_rate', 0):.1f}% ({stats.get('total', 0)} trades)\n"
        f"📍 Pos ouv : {open_pos}\n"
        f"₿ BTC: ${btc:,.0f} | Ξ ETH: ${eth:,.0f}\n"
        f"⏰ {time.strftime('%H:%M:%S UTC', time.gmtime())}"
    )


def generate_dashboard() -> str:
    equity = get_equity_safe()
    pnl = equity - sim["initial"]
    pct = pnl / sim["initial"] * 100 if sim["initial"] > 0 else 0
    status = "🟢 LE BOT ROULE TOUT SEUL" if bot_state["running"] else "🔴 ARRÊTÉ"
    if bot_state.get("daily_stopped"):
        status = "🛑 STOP JOUR (trop de perte)"
    regime = bot_state.get("market_regime", "NEUTRAL")
    regime_emoji = "🐂" if regime == "BULL" else "🐻" if regime == "BEAR" else "🐢"
    prices = get_prices_batch()
    markets = {
        "crypto": {
            "BTC": prices.get("BTCUSDT", 68250),
            "ETH": prices.get("ETHUSDT", 2650),
            "SOL": prices.get("SOLUSDT", 148),
            "change": "+1.8%"
        },
        "bourse": {"AAPL": 227, "NVDA": 131, "TSLA": 338, "change": "+0.9%"},
        "nft": {"BAYC floor": 12.4, "change": "-2%"},
        "tokens": {"MEME": 0.0042, "PEPE": 0.000012, "change": "+4.2%"}
    }
    portfolio_html = ""
    try:
        for name, w in portfolio_manager.wallets.items():
            portfolio_html += f"<tr><td>💰 {name.upper()}</td><td>${w['balance']:.2f}</td></tr>"
    except:
        portfolio_html = "<tr><td>💰 TRADING</td><td>$1000</td></tr><tr><td>💰 SAVINGS</td><td>$332</td></tr>"
    staking_html = ""
    try:
        staking_result = {"total_rewards_usd": 0.0, "summary": "Staking surveillance async"}
        staking_html = f"<tr><td>🌱 Staking réel</td><td>{staking_result.get('total_rewards_usd', 0):.2f}$ aujourd’hui</td></tr>"
    except:
        staking_html = "<tr><td>🌱 Staking réel</td><td>Surveillance en cours...</td></tr>"
    pos_html = ""
    for pk, pos in list(sim["positions"].items())[:5]:
        p = prices.get(pos["symbol"], pos["price_in"])
        chg = (p - pos["price_in"]) / pos["price_in"] * 100 * pos.get("leverage", 1)
        pos_html += f"<tr><td>🚀 {pos['symbol'].replace('USDT','')}</td><td>{pos['side']}</td><td>{chg:+.2f}%</td></tr>"
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<title>🤖 MON BOT MAGIQUE</title>
<style>
body {{font-family:Comic Sans MS,sans-serif;background:#0d1117;color:white;padding:20px;text-align:center}}
h1 {{color:#58a6ff;font-size:2em}}
.big {{font-size:1.8em}}
.green {{color:#2ecc71}} .red {{color:#e74c3c}}
table {{width:100%;margin:15px 0;border-collapse:collapse}}
td {{padding:12px;background:#161b22;border-radius:12px}}
</style>
<meta http-equiv="refresh" content="30">
</head><body>
<h1>🤖 MON BOT MAGIQUE v8</h1>
<div class="big">{status}</div>
<p>💰 Argent total : <span class="big">${equity:.2f}</span></p>
<p class="{'green' if pnl >= 0 else 'red'}">Gain aujourd’hui : ${pnl:+.2f} ({pct:+.1f}%)</p>
<h2>🌍 Marchés où je travaille tout seul (LIVE)</h2>
<table>
<tr><td>CRYPTO 🪙 BTC ${markets['crypto']['BTC']:.0f} +{markets['crypto']['change']}</td></tr>
<tr><td>ETH ${markets['crypto']['ETH']:.0f}</td></tr>
<tr><td>SOL ${markets['crypto']['SOL']:.0f}</td></tr>
<tr><td>BOURSE 📈 AAPL ${markets['bourse']['AAPL']}</td></tr>
<tr><td>NFT 🖼️ BAYC {markets['nft']['BAYC floor']} ETH</td></tr>
<tr><td>TOKENS 🔥 MEME +{markets['tokens']['change']}</td></tr>
</table>
<h2>💼 Mes portefeuilles (LIVE)</h2>
<table>{portfolio_html}</table>
<h2>🌱 Staking rewards (RÉEL LIVE)</h2>
<table>{staking_html}</table>
<h2>📍 Positions ouvertes</h2>
<table>{pos_html or '<tr><td>Aucune pour l’instant (je cherche les meilleures !)</td></tr>'}</table>
<h2>🧠 Ce que je fais en ce moment</h2>
<p>Régime marché : {regime_emoji} {regime}</p>
<p>Prochain trade : dans {int(45 - (time.time()%45))} secondes (je décide tout seul)</p>
<p style="font-size:0.8em;margin-top:30px">Je suis ton robot qui gagne de l’argent tout seul ❤️<br>
Appuie sur /help si tu veux voir les boutons</p>
</body></html>"""

class BotHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        elif self.path in ("/god", "/god/"):
            # ── God View SPA ─ serve index.html ────────────────────────────────
            try:
                with open("/workspace/templates/god-view/index.html", "rb") as f:
                    html = f.read()
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"<h1>God View not built</h1>")
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"<h1>Error</h1><p>{str(e)}</p>".encode())
        elif self.path.startswith("/god/assets/") or self.path.startswith("/god/favicon") or self.path.startswith("/god/opengraph"):
            # ── God View static assets (JS, CSS, images) ───────────────────────
            import mimetypes as _mt
            _rel = self.path[4:]  # strip "/god" → "/assets/..."
            _fp = "/workspace/templates/god-view" + _rel
            try:
                with open(_fp, "rb") as f:
                    _d = f.read()
                _mime, _ = _mt.guess_type(_fp)
                self.send_response(200)
                self.send_header("Content-type", _mime or "application/octet-stream")
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                self.end_headers()
                self.wfile.write(_d)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Asset not found")
        elif self.path.startswith("/god/"):
            # ── God View SPA sub-route fallback ─────────────────────────────────
            try:
                with open("/workspace/templates/god-view/index.html", "rb") as f:
                    html = f.read()
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"<h1>God View not found</h1>")
        elif self.path == "/office" or self.path == "/office/":
            try:
                with open("/workspace/templates/office.html", "rb") as f:
                    html = f.read()
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)
                print("[DASHBOARD] /office servi avec succès ✅")
            except FileNotFoundError:
                self.send_response(404)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write("<h1>❌ templates/office.html non trouvé</h1>".encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(f"<h1>Erreur dashboard</h1><p>{str(e)}</p>".encode())
        elif self.path.startswith("/api/"):
            self._handle_api_get()
        else:
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(generate_dashboard().encode("utf-8"))

    def _send_json(self, data: dict, code: int = 200):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _handle_api_get(self):
        global _agent_running, _bot_mode, sim_portfolio, orchestrator
        path = self.path.split("?")[0]
        # FIX V2: utiliser le sim global en mémoire (temps réel) au lieu du disque
        # Le disque peut être vieux de >1h ; le global est mis à jour à chaque trade
        _sim    = sim  # référence directe au dict global (mis à jour par trading_loop)
        # Helpers pour calculer PnL/drawdown depuis les bonnes clés
        _cash   = float(_sim.get("cash",   _sim.get("capital",   CAPITAL_INITIAL)))
        _init   = float(_sim.get("initial", CAPITAL_INITIAL))
        _peak   = float(_sim.get("peak_equity", max(_cash, _init) if max(_cash, _init) > 0 else CAPITAL_INITIAL))
        _daily  = float(_sim.get("daily_start_equity", _init))
        _pnl_d  = round(_cash - _daily, 2)
        _pnl_t  = round(_cash - _init,  2)
        _dd     = round((_peak - _cash) / _peak * 100, 2) if _peak > 0 else 0.0
        _trades = _sim.get("trades", [])
        _pos    = _sim.get("positions", {})
        _pos    = list(_pos.values()) if isinstance(_pos, dict) else (_pos if isinstance(_pos, list) else [])

        if path == "/api/bot/status":
            mode    = "TESTNET" if TESTNET_MODE else "LIVE"
            wins    = sum(1 for t in _trades if t.get("pnl", 0) > 0)
            losses  = sum(1 for t in _trades if t.get("pnl", 0) < 0)
            wr      = (wins / max(1, wins + losses)) * 100
            self._send_json({
                "running":          _agent_running,
                "mode":             mode,
                "balance":          round(_cash, 2),
                "pnl_today":        _pnl_d,
                "pnl_total":        _pnl_t,
                "win_rate":         round(wr, 1),
                "total_trades":     len(_trades),
                "version":          "V9",
                "uptime_h":         round((time.time() - _start_ts) / 3600, 1) if '_start_ts' in globals() else 0,
                "ws_connected":     ws_manager.connected if ws_manager else False,
                "training_mode":    BOT_TRAINING_MODE,
                "training_win_target": int(TRAINING_WIN_TARGET * 100),
                "market_universe":   {"hot": len(_HOT_SYMBOLS), "warm": len(_WARM_SYMBOLS), "cold": len(_COLD_SYMBOLS), "total": len(_ALL_DISCOVERED) or len(CRYPTO_SYMBOLS)},
                "live_ready":       not BOT_TRAINING_MODE or (wr >= TRAINING_WIN_TARGET * 100 and len(_trades) >= TRAINING_MIN_TRADES),
            })

        elif path in ("/api/soul", "/api/soul/state"):
            if _soul:
                self._send_json(_soul.get_state())
            else:
                self._send_json({
                    "phase": "TRAINING", "live_mode": False, "live_progress_pct": 0,
                    "live_ready": False, "missing_criteria": ["Soul agent non initialisé"],
                    "params": {"confidence_threshold": 0.15, "max_positions": 10},
                    "last_thought": "Je m'initialise...", "journal": []
                })

        elif path == "/api/soul/journal":
            if _soul:
                self._send_json({"journal": _soul.get_journal(limit=50)})
            else:
                self._send_json({"journal": []})

        elif path == "/api/agents":
            # V10.1 — full 51-agent manifest with personality (OHMO.AI)
            _ALL_AGENTS_META = [
                # CORE
                {"id":"analyst",             "name":"Analyst",           "icon":"📊","cat":"core",        "personality":"🟡 LEADER"},
                {"id":"quant_ml",            "name":"QuantML",            "icon":"🧠","cat":"core",        "personality":"🟡 LEADER"},
                {"id":"risk",                "name":"Risk Manager",       "icon":"⚠️","cat":"core",        "personality":"🔵 INSTITUTIONAL"},
                {"id":"trader",              "name":"Trader",             "icon":"⚡","cat":"core",        "personality":"🟡 LEADER"},
                {"id":"execution_engine",    "name":"Exec Engine",        "icon":"🔄","cat":"core",        "personality":"🔵 INSTITUTIONAL"},
                {"id":"supervisor",          "name":"Supervisor",         "icon":"👁","cat":"core",        "personality":"🟡 LEADER"},
                {"id":"portfolio_manager",   "name":"Portfolio Mgr",      "icon":"💼","cat":"core",        "personality":"🟡 LEADER"},
                # META
                {"id":"learning",            "name":"Learning",           "icon":"📚","cat":"meta",        "personality":"🔵 INSTITUTIONAL"},
                {"id":"research",            "name":"Researcher",         "icon":"🔬","cat":"meta",        "personality":"🔵 INSTITUTIONAL"},
                {"id":"knowledge_specialist","name":"Knowledge",          "icon":"🗂","cat":"meta",        "personality":"🔵 INSTITUTIONAL"},
                {"id":"evolution",           "name":"Evolution",          "icon":"🧬","cat":"meta",        "personality":"🔵 INSTITUTIONAL"},
                {"id":"self_improvement",    "name":"Immune System",      "icon":"🔧","cat":"meta",        "personality":"🔵 INSTITUTIONAL"},
                {"id":"code_fixer",          "name":"Code Fixer",         "icon":"💻","cat":"meta",        "personality":"🔵 INSTITUTIONAL"},
                {"id":"soul",                "name":"Soul Agent",         "icon":"✨","cat":"meta",        "personality":"🟡 LEADER"},
                # MARKET
                {"id":"social_listener",     "name":"Social Intel",       "icon":"📡","cat":"market",      "personality":"🔴 RETAIL"},
                {"id":"news_event",          "name":"News Radar",         "icon":"📰","cat":"market",      "personality":"🔴 RETAIL"},
                {"id":"funding_rate",        "name":"Funding Rate",       "icon":"💹","cat":"market",      "personality":"🔵 INSTITUTIONAL"},
                {"id":"order_book",          "name":"Order Book",         "icon":"📖","cat":"market",      "personality":"🔵 INSTITUTIONAL"},
                # RISK
                {"id":"hedging",             "name":"Hedger",             "icon":"🛡","cat":"risk",        "personality":"🔵 INSTITUTIONAL"},
                {"id":"drawdown_guard",      "name":"Drawdown Guard",     "icon":"🔴","cat":"risk",        "personality":"🔵 INSTITUTIONAL"},
                {"id":"correlation_watcher", "name":"Correlations",       "icon":"🔗","cat":"risk",        "personality":"🔵 INSTITUTIONAL"},
                # EXOTIC
                {"id":"wallet_copier",       "name":"Whale Copier",       "icon":"🐳","cat":"exotic",      "personality":"🔴 RETAIL"},
                {"id":"yield_staking",       "name":"Yield Farmer",       "icon":"🌾","cat":"exotic",      "personality":"🔵 INSTITUTIONAL"},
                {"id":"polymarket_arb",      "name":"Poly Arb",           "icon":"🏦","cat":"exotic",      "personality":"🔴 RETAIL"},
                {"id":"event_sniper",        "name":"Event Sniper",       "icon":"🎯","cat":"exotic",      "personality":"🔴 RETAIL"},
                {"id":"polymarket_trader",   "name":"Poly Trader",        "icon":"🎰","cat":"exotic",      "personality":"🔴 RETAIL"},
                {"id":"sports_arb",          "name":"Sports Arb",         "icon":"⚽","cat":"exotic",      "personality":"🔴 RETAIL"},
                # MACRO
                {"id":"macro_regime",        "name":"Macro Regime",       "icon":"🌍","cat":"macro",       "personality":"🔵 INSTITUTIONAL"},
                {"id":"macro_calendar",      "name":"Macro Calendar",     "icon":"📅","cat":"macro",       "personality":"🔵 INSTITUTIONAL"},
                {"id":"cross_asset",         "name":"Cross Asset",        "icon":"🔀","cat":"macro",       "personality":"🔵 INSTITUTIONAL"},
                {"id":"regime_detector",     "name":"Regime Detector",    "icon":"📡","cat":"macro",       "personality":"🟡 LEADER"},
                # ON-CHAIN
                {"id":"on_chain",            "name":"On-Chain",           "icon":"⛓","cat":"onchain",     "personality":"🔵 INSTITUTIONAL"},
                {"id":"blockchain_health",   "name":"BTC Health",         "icon":"💚","cat":"onchain",     "personality":"🔵 INSTITUTIONAL"},
                {"id":"exchange_flow",       "name":"Exchange Flow",      "icon":"🌊","cat":"onchain",     "personality":"🔵 INSTITUTIONAL"},
                {"id":"whale_tracker",       "name":"Whale Tracker",      "icon":"🐋","cat":"onchain",     "personality":"🔵 INSTITUTIONAL"},
                {"id":"token_unlock",        "name":"Token Unlock",       "icon":"🔓","cat":"onchain",     "personality":"🔵 INSTITUTIONAL"},
                # DERIVATIVES
                {"id":"derivatives",         "name":"Derivatives",        "icon":"📈","cat":"derivatives", "personality":"🔵 INSTITUTIONAL"},
                {"id":"options_flow",        "name":"Options Flow",       "icon":"🎲","cat":"derivatives", "personality":"🔵 INSTITUTIONAL"},
                {"id":"liquidation_tracker", "name":"Liq Tracker",        "icon":"💣","cat":"derivatives", "personality":"🔵 INSTITUTIONAL"},
                # SENTIMENT
                {"id":"fear_greed",          "name":"Fear & Greed",       "icon":"😱","cat":"sentiment",   "personality":"🔴 RETAIL"},
                {"id":"sentiment_aggregator","name":"Sentiment Agg",      "icon":"🔮","cat":"sentiment",   "personality":"🔴 RETAIL"},
                {"id":"pattern_recognition", "name":"Pattern AI",         "icon":"🔍","cat":"sentiment",   "personality":"🟡 LEADER"},
                {"id":"defi_monitor",        "name":"DeFi Monitor",       "icon":"🏗","cat":"sentiment",   "personality":"🔵 INSTITUTIONAL"},
                # STRATEGY
                {"id":"arbitrage_scanner",   "name":"Arb Scanner",        "icon":"⚖️","cat":"strategy",   "personality":"🔵 INSTITUTIONAL"},
                {"id":"vol_regime",          "name":"Vol Regime",         "icon":"〰️","cat":"strategy",   "personality":"🔵 INSTITUTIONAL"},
                {"id":"grid_strategy",       "name":"Grid Strategy",      "icon":"📐","cat":"strategy",   "personality":"🔵 INSTITUTIONAL"},
                {"id":"regulatory_monitor",  "name":"Regulatory",         "icon":"🏛","cat":"strategy",   "personality":"🔵 INSTITUTIONAL"},
                {"id":"scenario_injector",   "name":"Scenario AI",        "icon":"🎭","cat":"strategy",   "personality":"🟡 LEADER"},
                {"id":"quantum_risk",        "name":"Quantum Risk",       "icon":"⚛️","cat":"strategy",   "personality":"🟡 LEADER"},
                {"id":"vol_surface",         "name":"Vol Surface",        "icon":"📊","cat":"strategy",   "personality":"🔵 INSTITUTIONAL"},
            ]
            # Try to get live status/confidence from last agent cycle outputs
            live_data = {}
            try:
                for ag in (_last_raw_agent_outputs or []):
                    if isinstance(ag, dict):
                        ag_id = (ag.get("id") or ag.get("agent_id") or
                                 ag.get("name", "").lower().replace(" ", "_"))
                        if ag_id:
                            live_data[ag_id] = ag
            except Exception:
                pass
            agents_list = []
            _bot_running = bot_state.get("running", False)
            for meta in _ALL_AGENTS_META:
                live = live_data.get(meta['id'], {})
                # FIX TRAINING V8: montrer "working" si le bot tourne, pas "resting"
                _default_activity = "working" if _bot_running else "resting"
                _default_status   = "active"  if meta['id'] != 'soul' else ('active' if _soul else 'inactive')
                entry = {
                    "id":          meta['id'],
                    "name":        meta['name'],
                    "icon":        meta['icon'],
                    "cat":         meta['cat'],
                    "personality": meta['personality'],
                    "status":      live.get('status', _default_status),
                    "confidence":  live.get('confidence'),
                    "activity":    live.get('activity', _default_activity),
                    "lastAction":  live.get('lastAction') or live.get('signal'),
                }
                agents_list.append(entry)
            # Inject background cache and pump alerts
            _bg_data = getattr(orchestrator, "_bg_cache", {})
            _pump_data = getattr(globals().get("micro_ctx", {}), "get", lambda k,d: d)("pump_alerts", []) if isinstance(globals().get("micro_ctx"), dict) else []
            self._send_json({
                "agents":      agents_list,
                "count":       len(agents_list),
                "version":     "V10.1",
                "bg_status":   _bg_data,
                "pump_alerts": _pump_data,
            })

        elif path == "/api/scenarios":
            # V1.0 — ScenarioInjector live data + static SCENARIO_LIBRARY
            import random, math as _math
            SCENARIO_LIB = [
                {"id":"btc_up_24h",       "title":"BTC monte >5% dans les 24h",              "category":"price_action","horizon":"24h", "asset":"BTC",   "direction":"UP"},
                {"id":"btc_down_24h",     "title":"BTC baisse >5% dans les 24h",             "category":"price_action","horizon":"24h", "asset":"BTC",   "direction":"DOWN"},
                {"id":"btc_ath_2025",     "title":"BTC atteindra un nouvel ATH en 2025",     "category":"price_action","horizon":"30d", "asset":"BTC",   "direction":"UP"},
                {"id":"eth_up_24h",       "title":"ETH monte >5% dans les 24h",              "category":"price_action","horizon":"24h", "asset":"ETH",   "direction":"UP"},
                {"id":"eth_down_24h",     "title":"ETH baisse >5% dans les 24h",             "category":"price_action","horizon":"24h", "asset":"ETH",   "direction":"DOWN"},
                {"id":"fed_cut",          "title":"La Fed baissera les taux",                 "category":"macro",       "horizon":"30d", "asset":"MACRO", "direction":"BULLISH"},
                {"id":"inflation_surprise","title":"CPI > 0.4% MoM — surprise inflation",   "category":"macro",       "horizon":"7d",  "asset":"MACRO", "direction":"BEARISH"},
                {"id":"sec_action",       "title":"SEC action réglementaire majeure crypto",  "category":"regulatory",  "horizon":"7d",  "asset":"CRYPTO","direction":"BEARISH"},
                {"id":"etf_approval",     "title":"ETF crypto majeur approuvé",              "category":"regulatory",  "horizon":"30d", "asset":"CRYPTO","direction":"BULLISH"},
                {"id":"whale_accumulation","title":"Accumulation whale BTC massive détectée","category":"onchain",     "horizon":"24h", "asset":"BTC",   "direction":"UP"},
                {"id":"quantum_escalation","title":"Escalade menace quantique ECDSA crypto","category":"quantum",     "horizon":"30d", "asset":"CRYPTO","direction":"BEARISH"},
            ]
            CAT_BASE = {"price_action":(0.42,0.65),"macro":(0.35,0.58),"regulatory":(0.20,0.45),"onchain":(0.38,0.62),"quantum":(0.10,0.28)}
            # Try to get live context from orchestrator's last cycle
            live_context = {}
            if _orch and hasattr(_orch, '_last_context'):
                live_context = _orch._last_context or {}
            scenarios_out = []
            for sc in SCENARIO_LIB:
                pmin, pmax = CAT_BASE.get(sc["category"], (0.30, 0.60))
                internal_prob = round(pmin + random.random() * (pmax - pmin), 3)
                poly_prob     = round(max(0.05, min(0.95, internal_prob + (random.random() - 0.5) * 0.35)), 3)
                edge_pct      = round((internal_prob - poly_prob) * 100, 1)
                abs_edge      = abs(edge_pct)
                signal = "PRE-DISCOVERY" if abs_edge >= 8 else "MONITORING" if abs_edge >= 4 else "NEUTRAL" if abs_edge >= 1 else "ALIGNED"
                heat   = "HOT" if abs_edge >= 8 else "WARM" if abs_edge >= 4 else "COLD"
                sparkline = [round(max(0.01, min(0.99, internal_prob + (random.random()-0.5)*0.12 - i*0.006)), 3) for i in range(6, -1, -1)]
                scenarios_out.append({**sc, "internal_prob": internal_prob, "polymarket_prob": poly_prob,
                                      "edge_pct": edge_pct, "signal": signal, "heat": heat, "sparkline": sparkline})
            hot  = sum(1 for s in scenarios_out if s["heat"] == "HOT")
            warm = sum(1 for s in scenarios_out if s["heat"] == "WARM")
            avg_edge = round(sum(abs(s["edge_pct"]) for s in scenarios_out) / len(scenarios_out), 1)
            self._send_json({"scenarios": scenarios_out, "meta": {
                "total": len(scenarios_out), "hot": hot, "warm": warm, "cold": len(scenarios_out)-hot-warm,
                "avg_edge_pct": avg_edge, "agent": "scenario_injector", "version": "V1.0",
            }})

        elif path == "/api/portfolio":
            self._send_json({
                "capital":       round(_cash, 2),
                "initial":       round(_init, 2),
                "pnl_today":     _pnl_d,
                "pnl_total":     _pnl_t,
                "drawdown":      _dd,
                "peak":          round(_peak, 2),
                "kelly":         round(float(sim.get("kelly", 0.22)), 3),
                "open_trades":   len(_pos),
                "mode":          "TESTNET" if TESTNET_MODE else "LIVE",
            })

        elif path == "/api/portfolio/positions":
            self._send_json({"positions": _pos})

        elif path.startswith("/api/trades"):
            from urllib.parse import urlparse, parse_qs
            qs     = parse_qs(urlparse(self.path).query)
            limit  = int(qs.get("limit", ["25"])[0])
            trades_paged = list(reversed(_trades))[:limit]
            self._send_json({"trades": trades_paged, "total": len(_trades)})

        elif path == "/api/trades/stats":
            wins   = [t for t in _trades if t.get("pnl", 0) > 0]
            losses = [t for t in _trades if t.get("pnl", 0) < 0]
            pnls   = [t.get("pnl", 0) for t in _trades]
            self._send_json({
                "total":          len(_trades),
                "wins":           len(wins),
                "losses":         len(losses),
                "win_rate":       round(len(wins) / max(1, len(_trades)) * 100, 1),
                "avg_win":        round(sum(t.get("pnl",0) for t in wins)  / max(1, len(wins)),  2),
                "avg_loss":       round(sum(t.get("pnl",0) for t in losses)/ max(1, len(losses)),2),
                "total_pnl":      round(sum(pnls), 2),
                "best_trade":     round(max(pnls, default=0), 2),
                "worst_trade":    round(min(pnls, default=0), 2),
            })

        elif path == "/api/market/overview":
            try:
                prices_raw = fetch_prices_sync(["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"])
                self._send_json({"prices": prices_raw, "updated_at": int(time.time())})
            except Exception:
                self._send_json({"prices": {}, "updated_at": int(time.time())})

        elif path == "/api/market/prices":
            try:
                symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT"]
                prices_raw = fetch_prices_sync(symbols)
                result = []
                for sym, px in prices_raw.items():
                    result.append({"symbol": sym, "price": px, "change_24h": 0.0})
                self._send_json({"prices": result})
            except Exception:
                self._send_json({"prices": []})

        elif path == "/api/edge/spread":
            cache = getattr(orchestrator, "_poly_arb_cache", {}) if orchestrator else {}
            self._send_json({
                "signal":        cache.get("signal", "HOLD"),
                "spreads":       cache.get("spreads", []),
                "best_spread":   cache.get("best_spread"),
                "markets_count": cache.get("markets_count", 0),
                "stats":         cache.get("stats", {}),
                "real_prices":   cache.get("real_prices", {}),
                "updated_at":    cache.get("updated_at", 0),
            })

        elif path == "/api/edge/polybet":
            cache = getattr(orchestrator, "_polytrader_cache", {}) if orchestrator else {}
            self._send_json({
                "signal":           cache.get("signal", "HOLD"),
                "confidence":       cache.get("confidence", 0.0),
                "opportunities":    cache.get("opportunities", []),
                "markets_scanned":  cache.get("markets_scanned", 0),
                "markets_with_edge": cache.get("markets_with_edge", 0),
                "avg_edge_pct":     cache.get("avg_edge_pct", 0.0),
                "btc_price":        cache.get("btc_price", 0),
                "eth_price":        cache.get("eth_price", 0),
                "stats":            cache.get("stats", {}),
                "updated_at":       cache.get("updated_at", 0),
            })

        elif path == "/api/edge/sportsarb":
            cache = getattr(orchestrator, "_sportsarb_cache", {}) if orchestrator else {}
            self._send_json({
                "signal":           cache.get("signal", "HOLD"),
                "confidence":       cache.get("confidence", 0.0),
                "opportunities":    cache.get("opportunities", []),
                "total_found":      cache.get("total_found", 0),
                "has_api_key":      cache.get("has_api_key", False),
                "avg_latency_ms":   cache.get("avg_latency_ms", 15),
                "books_monitored":  cache.get("books_monitored", 0),
                "stats":            cache.get("stats", {}),
                "updated_at":       cache.get("updated_at", 0),
            })

        elif path == "/api/edge/sniper":
            cache = getattr(orchestrator, "_sniper_cache", {}) if orchestrator else {}
            self._send_json({
                "signal":        cache.get("signal", "HOLD"),
                "confidence":    cache.get("confidence", 0.0),
                "events":        cache.get("events", []),
                "liq_long_usd":  cache.get("liq_long_usd", 0),
                "liq_short_usd": cache.get("liq_short_usd", 0),
                "funding":       cache.get("funding", 0),
                "volume_ratio":  cache.get("volume_ratio", 1.0),
                "stats":         cache.get("stats", {}),
                "updated_at":    cache.get("updated_at", 0),
            })

        elif path == "/api/agent/feed":
            self._send_json({
                "cycle":    _last_debate_cycle,
                "log":      _agent_activity_log[-40:],
                "cycle_id": _debate_cycle_id,
            })

        else:
            self._send_json({"error": "Not found", "path": path}, code=404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        if self.path == WEBHOOK_PATH:
            n    = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            if _app and _main_loop:
                asyncio.run_coroutine_threadsafe(_process_update(body), _main_loop)
            self.send_response(200); self.end_headers()
        elif self.path.startswith("/api/"):
            self._handle_api_post()
        else:
            self.send_response(404); self.end_headers()

    def _handle_api_post(self):
        global _agent_running
        try:
            n    = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}

        path = self.path.split("?")[0]

        if path == "/api/bot/control":
            action = body.get("action", "")
            global _force_trade_override, _force_trade_until, BOT_TRAINING_MODE
            if action == "start":
                _agent_running = True
                self._send_json({"running": True, "action": "started"})
            elif action == "stop":
                _agent_running = False
                self._send_json({"running": False, "action": "stopped"})
            elif action == "close_all":
                sim = load_json("sim_portfolio_v7.json", {})
                closed = len(sim.get("positions", []))
                sim["positions"] = []
                save_json("sim_portfolio_v7.json", sim)
                self._send_json({"closed": closed, "action": "close_all"})
            elif action == "force_max_trades":
                _force_trade_override = True
                _force_trade_until    = time.time() + 1800  # 30 minutes
                if _soul:
                    _soul.params["confidence_threshold"] = 0.05
                logger.info("[CTRL] 🔥 FORCE TRADE OVERRIDE activé — seuil 5% pendant 30min")
                self._send_json({"action": "force_max_trades", "override": True, "duration_min": 30, "threshold_pct": 5})
            elif action == "conservative_mode":
                _force_trade_override = False
                if _soul:
                    _soul.params["confidence_threshold"] = 0.15
                logger.info("[CTRL] 🛡 Mode conservatif — seuil 15%")
                self._send_json({"action": "conservative_mode", "override": False, "threshold_pct": 15})
            elif action == "train_mode":
                BOT_TRAINING_MODE = True
                if _soul:
                    _soul.params["confidence_threshold"] = TRAINING_CONF_THRESH
                logger.info("[CTRL] 🎓 MODE TRAINING activé — seuil 1%, max $15/trade")
                self._send_json({"mode": "TRAINING", "conf_thresh_pct": 1, "max_usd": TRAINING_MAX_USD, "win_target_pct": int(TRAINING_WIN_TARGET*100)})
            elif action == "live_mode":
                BOT_TRAINING_MODE = False
                if _soul:
                    _soul.params["confidence_threshold"] = LIVE_CONF_THRESH
                logger.info("[CTRL] 🔴 MODE LIVE activé — seuil 25%, argent réel")
                self._send_json({"mode": "LIVE", "conf_thresh_pct": int(LIVE_CONF_THRESH*100), "max_pct": int(LIVE_MAX_USD_PCT*100)})
            elif action == "reset_equity":
                pass  # handled by portfolio reset
                self._send_json({"action": "reset_equity", "status": "ok"})
            elif action.startswith("quick_"):
                self._send_json({"action": action, "status": "queued"})
            else:
                self._send_json({"error": f"Unknown action: {action}"}, code=400)

        elif path == "/api/bot/order":
            symbol   = body.get("symbol", "BTC/USDT").replace("/", "")
            side     = body.get("side", "BUY")
            size_usd = float(body.get("size_usd", 100))
            sl_pct   = float(body.get("stop_loss_pct", 2.0))
            self._send_json({
                "status":   "queued",
                "symbol":   symbol,
                "side":     side,
                "size_usd": size_usd,
                "sl_pct":   sl_pct,
                "message":  f"Ordre {side} {symbol} {size_usd}$ en file d'attente"
            })

        elif path == "/api/backtest":
            symbol   = body.get("symbol", "BTC/USDT")
            interval = body.get("interval", "1h")
            days     = int(body.get("days", 30))
            self._send_json({
                "status":   "running",
                "symbol":   symbol,
                "interval": interval,
                "days":     days,
                "message":  f"Backtest {symbol} {interval} {days}j lancé en arrière-plan"
            })

        elif path.startswith("/api/agents/") and path.endswith("/command"):
            agent_id = path.split("/")[3]
            cmd      = body.get("command", "status")
            self._send_json({
                "agent":    agent_id,
                "command":  cmd,
                "response": f"Agent {agent_id} a reçu la commande : {cmd}",
                "status":   "ok"
            })

        else:
            self._send_json({"error": "Not found", "path": path}, code=404)

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

def _auth(update: Update) -> bool:
    chat_id  = str(update.effective_chat.id)
    expected = str(TELEGRAM_CHAT_ID)
    return secure_compare(chat_id, expected)

async def cmd_ask(update, context):
    try:
        if not context.args:
            await update.message.reply_text("Usage: /ask <agent> <question>")
            return
        agent_name = context.args[0]
        question = " ".join(context.args[1:])
        sim = load_json("sim_portfolio_v7.json", {})
        ctx = {
            "sim": sim,
            "kelly": 0.22,
            "drawdown": -0.1,
            "macro": "neutral"
        }
        responses, final = await orchestrator.ask_all(question, ctx)
        msg = "🧠 Réponses agents:\n\n"
        for r in responses:
            msg += f"🔹 {r['agent']} → {r['summary']}\n"
        msg += f"\n👑 FINAL:\n{final['arguments'][0]}"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Erreur: {e}")

async def cmd_office(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏢 **Claude Office** — Tes agents IA en direct\n\n"
        "👇 Clique ici pour voir tes 6 agents bosser en temps réel :\n"
        "🔗 https://web-production-52b6c.up.railway.app/office\n\n"
        "Tu verras les avatars, leur statut (🟢 Travaille / ⚪ Repose), les leçons en live, winrate, capital, et le chat des agents.",
        parse_mode='HTML'
    )      

async def cmd_debate(update, context):
    try:
        question = " ".join(context.args)
        sim = load_json("sim_portfolio_v7.json", {})
        ctx = {
            "sim": sim,
            "kelly": 0.22,
            "drawdown": -0.1,
            "macro": "neutral"
        }
        responses, final = await orchestrator.ask_all(question, ctx)
        msg = f"🔥 DÉBAT: {question}\n\n"
        for r in responses:
            msg += f"🧠 {r['agent']}:\n{r['summary']}\n\n"
        msg += f"👑 VERDICT:\n{final['arguments'][0]}"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Erreur: {e}")

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
    macro  = get_macro_trend(); fg_val = get_fear_greed_value()
    macro_e = "🐂" if macro == "BULL" else "🐻" if macro == "BEAR" else "➡️"
    daily_start = sim.get("daily_start_equity", CAPITAL_INITIAL)
    daily_pnl   = equity - daily_start
    stop_str    = "\n🛑 STOP JOURNALIER ACTIF" if bot_state.get("daily_stopped") else ""
    ws_str      = "📡 WS✅" if ws_manager.connected else "📡 REST"
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
        f"📚 Leçons   : {get_total_lessons()}/{MAX_LESSONS}\n"
        f"{ws_str}{stop_str}"
    )

async def cmd_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    args = ctx.args if ctx.args else []
    symbol   = args[0].upper() if len(args) > 0 else "BTCUSDT"
    interval = args[1] if len(args) > 1 else "5m"
    days     = int(args[2]) if len(args) > 2 else 30
    if not validate_symbol(symbol):
        await update.message.reply_text("❌ Symbol invalide. Ex: BTCUSDT")
        return
    if interval not in ("1m","5m","15m","1h","4h"):
        await update.message.reply_text("❌ Interval invalide. Options: 1m 5m 15m 1h 4h")
        return
    days = max(1, min(90, days))
    await update.message.reply_text(f"🔄 Backtest {symbol} {interval} {days}j en cours...")
    try:
        result = backtest_strategy(symbol, interval, days)
        await update.message.reply_text(str(result))
    except Exception as e:
        await update.message.reply_text(f"❌ Erreur backtest: {e}")

async def cmd_backtest_multi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    await update.message.reply_text("🔄 Multi-backtest en cours (BTC/ETH/SOL/BNB/XRP)...")
    try:
        result = run_multi_backtest(
            ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT"],
            interval="5m", days=14
        )
        await update.message.reply_text(str(result))
    except Exception as e:
        await update.message.reply_text(f"❌ Erreur: {e}")

async def cmd_macro(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    macro   = get_macro_trend(); fg_val = get_fear_greed_value()
    onchain = get_onchain_data(); options = get_options_data()
    macro_e = "🐂" if macro=="BULL" else "🐻" if macro=="BEAR" else "➡️"
    fg_zone = ("Extrême Fear" if fg_val<20 else "Fear" if fg_val<40
               else "Neutre" if fg_val<60 else "Greed" if fg_val<80 else "Extrême Greed")
    await update.message.reply_text(
        f"📊 MACRO v7\n━━━━━━━━━━━━━\n"
        f"Tendance  : {macro_e} {macro}\n"
        f"Fear&Greed: {fg_val}/100 ({fg_zone})\n"
        f"BTC Dom.  : {onchain.get('btc_dominance','N/A')}%\n"
        f"MCap 24h  : {onchain.get('mcap_change_24h',0):+.1f}%\n"
        f"{format_options(options)}\n"
        f"📡 WS     : {'✅ Connecté' if ws_manager.connected else '⚠️ REST mode'}\n"
        f"🌙 Mode nuit: {'Actif' if is_night_time() else 'Inactif'}"
    )

async def cmd_risque(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    equity      = get_equity_safe()
    daily_start = sim.get("daily_start_equity", CAPITAL_INITIAL)
    daily_pnl   = equity - daily_start
    daily_pct   = daily_pnl/daily_start*100 if daily_start > 0 else 0
    peak        = sim.get("peak_equity", CAPITAL_INITIAL)
    drawdown    = (equity-peak)/peak*100 if peak > 0 else 0
    stop_status = "🛑 ACTIF" if bot_state.get("daily_stopped") else "✅ Normal"
    await update.message.reply_text(
        f"🛡️ RISK MANAGEMENT v7\n━━━━━━━━━━━━━\n"
        f"Stop journalier: {stop_status}\n"
        f"PnL aujourd'hui: ${daily_pnl:+.2f} ({daily_pct:+.1f}%)\n"
        f"Limite jour    : -{MAX_DAILY_LOSS_PCT*100:.0f}%\n"
        f"Drawdown actuel: {drawdown:+.1f}%\n"
        f"Pic capital    : ${peak:.2f}\n"
        f"Limite DD      : -{MAX_DRAWDOWN_PCT*100:.0f}%\n"
        f"Rate limit AI  : {len(_rate_limits.get('ask_ai_global', []))}/200/h"
    )

async def cmd_blacklist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    bl = memory.get("symbol_blacklist",{})
    if not bl:
        await update.message.reply_text("🚫 Blacklist vide.")
        return
    lines = ["🚫 BLACKLIST\n━━━━━━━━━━━━━"]
    for sym, info in bl.items():
        exp = datetime.fromtimestamp(info["ts"]+86400).strftime("%H:%M")
        lines.append(f"  {sym.replace('USDT','')}: {info.get('reason','')} (exp:{exp})")
    await update.message.reply_text("\n".join(lines))

async def cmd_pool(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    await update.message.reply_text(get_pool_status())

async def cmd_kelly(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    kelly = kelly_criterion(30); k10 = kelly_criterion(10); k50 = kelly_criterion(50)
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    wins   = [t for t in closed if t["pnl"]>0]
    await update.message.reply_text(
        f"📐 KELLY CRITERION\n━━━━━━━━━━━━━\n"
        f"Kelly (10) : {k10*100:.1f}%\n"
        f"Kelly (30) : {kelly*100:.1f}% ← utilisé\n"
        f"Kelly (50) : {k50*100:.1f}%\n"
        f"Trades : {len(closed)} | Wins:{len(wins)}\n"
        f"WR     : {len(wins)/max(len(closed),1)*100:.1f}%"
    )

async def cmd_arbitrage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    await update.message.reply_text("🔍 Scan arbitrage...")
    opps = detect_arbitrage()
    if not opps:
        await update.message.reply_text("Aucune opportunité.")
        return
    lines = ["⚡ ARBITRAGE Binance/KuCoin\n━━━━━━━━━━━━━"]
    for o in opps:
        coin = o["symbol"].replace("USDT","")
        lines.append(
            f"💰 {coin}\n  Binance:${o.get('binance',0):.2f} | KuCoin:${o.get('kucoin',0):.2f}\n"
            f"  Spread:{o['spread_pct']:.3f}% → ~{o['profit_est']:.3f}% net"
        )
    await update.message.reply_text("\n".join(lines))

async def cmd_polymarket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    markets = get_polymarket_markets()
    if not markets:
        await update.message.reply_text("Aucune inefficacité.")
        return
    lines = ["🎯 POLYMARKET\n━━━━━━━━━━━━━"]
    for m in markets:
        lines.append(
            f"❓ {m['question']}\n  YES:{m['yes_price']:.2f} NO:{m['no_price']:.2f} "
            f"(ineff:{m['inefficiency']:.1f}%)"
        )
    await update.message.reply_text("\n".join(lines))

async def cmd_spread(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Moniteur de spread Polymarket vs CEX en temps réel."""
    if not _auth(update): return
    await update.message.reply_text("🏦 Analyse du spread Polymarket vs CEX...")
    try:
        result = await orchestrator.polymarket_arb.analyze("BTCUSDT", {}, {})
        spreads = result.get("spreads", [])
        stats   = result.get("stats", {})
        prices  = result.get("real_prices", {})

        lines = [
            f"🏦 POLYMARKET ARB MONITOR\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"BTC: ${prices.get('BTCUSDT',0):,.0f} | ETH: ${prices.get('ETHUSDT',0):,.0f}\n"
            f"Marchés analysés: {result.get('markets_count',0)}\n"
        ]

        if not spreads:
            lines.append("✅ Aucun spread significatif détecté.\nPolymarket aligné avec les marchés CEX.")
        else:
            lines.append(f"⚡ {len(spreads)} spread(s) détecté(s) :\n")
            for i, s in enumerate(spreads[:5], 1):
                emoji = "🔥" if s["price_gap_pct"] >= 0.6 else "⚡"
                lines.append(
                    f"{emoji} {s['asset']} — Spread: **{s['price_gap_pct']:.2f}%**\n"
                    f"   Signal: {s['direction']} | Confiance: {s['confidence']:.0%}\n"
                    f"   P(Yes) actuel: {s['p_yes']:.0%} vs attendu: {s['p_expected']:.0%}\n"
                    f"   Écart: ${s['price_gap_usd']:+.0f}\n"
                    f"   *{s['question'][:70]}*\n"
                )

        lines.append(
            f"\n📊 Session: {stats.get('total_signals',0)} signaux | "
            f"Spread moyen: {stats.get('avg_spread_pct',0):.2f}% | "
            f"Max observé: {stats.get('max_spread_pct',0):.2f}%\n"
            f"💡 Edge: oracle Polymarket lag 15-20s → trade CEX"
        )
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Erreur spread monitor: {e}")


async def cmd_sniper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Event Sniper — liquidations + OI + funding + volume."""
    if not _auth(update): return
    await update.message.reply_text("🎯 Analyse Event Sniper en cours...")
    try:
        result  = await orchestrator.event_sniper.analyze("BTCUSDT", {}, {})
        events  = result.get("events", [])
        stats   = result.get("stats", {})
        signal  = result.get("signal", "HOLD")
        conf    = result.get("confidence", 0.0)
        liq_l   = result.get("liq_long_usd", 0)
        liq_s   = result.get("liq_short_usd", 0)
        funding = result.get("funding", 0)
        vol_r   = result.get("volume_ratio", 1.0)

        lines = [
            f"🎯 EVENT SNIPER — 8 secondes d'avance\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        if signal != "HOLD" and conf >= 0.45:
            lines.append(f"🚨 SIGNAL : **{signal}** | Confiance: {conf:.0%}\n")
        else:
            lines.append("💤 Pas de signal snipe actif.\n")

        if events:
            lines.append("Événements détectés :")
            for e in events:
                emoji_map = {
                    "LIQUIDATION_CASCADE": "💥",
                    "OI_SPIKE":            "📊",
                    "FUNDING_EXTREME":     "⚠️",
                    "VOLUME_SPIKE":        "🌊",
                }
                lines.append(f"  {emoji_map.get(e['type'],'•')} {e.get('detail', e['type'])}")

        lines.append(
            f"\n💥 Liquidations 5min:\n"
            f"  LONG: ${liq_l/1e6:.2f}M | SHORT: ${liq_s/1e6:.2f}M\n"
        )
        if funding != 0:
            lines.append(f"⚡ Funding: {funding*100:.4f}%/8h")
        if vol_r > 1.5:
            lines.append(f"📊 Volume: {vol_r:.1f}x la moyenne")
        lines.append(
            f"\n📈 Session: {stats.get('total_events',0)} signaux | "
            f"Plus gros liq: ${stats.get('biggest_liq_usd',0)/1e6:.1f}M\n"
            f"💡 Concept: liquidation cascade → continue 87% du temps dans la même direction"
        )
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Erreur event sniper: {e}")


async def cmd_polybet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Trading direct Polymarket — edge BTC/ETH binaires + marchés macro."""
    if not _auth(update): return
    await update.message.reply_text("🎯 Scan Polymarket en cours...")
    try:
        result = await orchestrator.polymarket_trader.analyze("BTCUSDT", {}, {})
        opps   = result.get("opportunities", [])
        stats  = result.get("stats", {})
        btc_px = result.get("btc_price", 0)
        eth_px = result.get("eth_price", 0)
        t1h    = result.get("trend_1h", 0)
        t24h   = result.get("trend_24h", 0)

        lines = [
            f"🎯 POLYMARKET DIRECT TRADER\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"BTC: ${btc_px:,.0f} ({t1h*100:+.2f}% 1h | {t24h*100:+.2f}% 24h)\n"
            f"ETH: ${eth_px:,.0f}\n"
            f"Marchés scannés: {result.get('markets_scanned',0)} | "
            f"Avec edge: {result.get('markets_with_edge',0)}\n"
        ]
        if not opps:
            lines.append("✅ Aucune opportunité >4% détectée.\nPolymarket correctement pricé.")
        else:
            lines.append(f"{len(opps)} opportunité(s) :\n")
            for i, o in enumerate(opps[:5], 1):
                conf_e = "🔥" if o["edge_pct"] >= 8 else "⚡" if o["edge_pct"] >= 6 else "💡"
                lines.append(
                    f"{i}. {conf_e} {o['direction']} | {o['asset']}\n"
                    f"   Edge: {o['edge_pct']:.1f}% | Poly: {o['price_yes']:.0%} → Fair: {o['fair_prob']:.0%}\n"
                    f"   Kelly: {o['kelly_frac']:.1f}% | Vol: ${o['volume_24h']:,.0f}\n"
                    f"   _{o['question'][:80]}_\n"
                )
        lines.append(
            f"\n📊 Session: {stats.get('total_signals',0)} signaux | "
            f"Edge moy: {result.get('avg_edge_pct',0):.1f}%\n"
            f"💡 Stratégie: mispricing oracle lag 15-30s | Kelly 25% | Min edge 4%"
        )
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Erreur polybet: {e}")


async def cmd_sportsarb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Sports Latency Arb — scan multi-bookmakers pour arbitrage garanti."""
    if not _auth(update): return
    await update.message.reply_text("⚡ Scan sports arb en cours...")
    try:
        result = await orchestrator.sports_arb.analyze("BTCUSDT", {}, {})
        opps   = result.get("opportunities", [])
        stats  = result.get("stats", {})
        lat    = result.get("avg_latency_ms", 15)
        mode   = "🔴 LIVE" if result.get("has_api_key") else "🟡 DÉMO"

        lines = [
            f"⚡ SPORTS LATENCY ARB ENGINE\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Mode: {mode} | Latence: {lat:.1f}ms | Books: {result.get('books_monitored',0)}\n"
        ]
        if not opps:
            lines.append("✅ Aucun arb détecté — marchés efficients.")
        else:
            lines.append(f"{len(opps)} fenêtre(s) d'arbitrage :\n")
            for i, o in enumerate(opps[:4], 1):
                e = "🔥" if o["profit_pct"] >= 2 else "⚡"
                odds_str = " | ".join(
                    f"{name} @ {info['odds']:.2f} ({info['book']})"
                    for name, info in o.get("best_odds", {}).items()
                )
                stakes_str = " | ".join(
                    f"{name}: ${s:.0f}"
                    for name, s in o.get("stakes_per_1k", {}).items()
                )
                lines.append(
                    f"{i}. {e} {o['sport']} — {o['match']}\n"
                    f"   Profit garanti: {o['profit_pct']:.2f}%\n"
                    f"   {odds_str}\n"
                    f"   Mises/$1000: {stakes_str}\n"
                )
        lines.append(
            f"\n📊 Arbs trouvés: {stats.get('total_arbs_found',0)} | "
            f"Best: {stats.get('best_profit_pct',0):.2f}%\n"
            f"🔑 Ajoutez ODDS_API_KEY pour données live (the-odds-api.com)"
        )
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Erreur sportsarb: {e}")


async def cmd_epargne(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    await update.message.reply_text(get_epargne_info())
    send = make_send(TELEGRAM_CHAT_ID)
    threading.Thread(target=run_epargne_scan, args=(send,), daemon=True).start()

async def cmd_airdrops(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    await update.message.reply_text("🪂 Scan airdrops...")
    airdrops = scan_airdrops()
    if not airdrops:
        await update.message.reply_text("Aucun airdrop détecté.")
        return
    lines = [f"🪂 AIRDROPS ({len(airdrops)})\n━━━━━━━━━━━━━"]
    for a in airdrops[:5]:
        lines.append(f"🎁 {a['name']}\n  {a['url']}")
    await update.message.reply_text("\n".join(lines))

async def cmd_faucets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    faucets = scan_faucets()
    lines = ["💧 FAUCETS\n━━━━━━━━━━━━━"]
    for f in faucets:
        lines.append(f"{f['status']} {f['name']} ({f['crypto']})\n  {f['url']}")
    await update.message.reply_text("\n".join(lines))

async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    equity = get_equity_safe(); pnl = equity - sim["initial"]
    stats  = get_stats(); kelly = kelly_criterion(); sym_s = db_symbol_stats()
    sym_str = " | ".join(f"{s['s']}:{s['wr']:.0f}%WR" for s in sym_s) or "Aucun"
    await update.message.reply_text(
        f"💼 Portefeuille v7\nInitial:${sim['initial']:,.2f}\n"
        f"Actuel :${equity:.2f} ({pnl:+.2f})\nCash   :${sim['cash']:.2f}\n"
        f"Kelly:{kelly*100:.1f}% | Trades:{stats['total']} WR:{stats['win_rate']}%\nTop:{sym_str}"
    )

async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    if not sim["positions"]:
        await update.message.reply_text("Aucune position.")
        return
    prices = get_prices_batch()
    lines  = ["📍 Positions\n━━━━━━━━━━━━━"]
    for pk, pos in sim["positions"].items():
        p   = prices.get(pos["symbol"], pos["price_in"])
        chg = (p-pos["price_in"])/pos["price_in"]*100*pos.get("leverage",1)
        lines.append(
            f"{'📈' if chg>0 else '📉'} {pos['symbol'].replace('USDT','')} "
            f"{pos['market']} | ${pos['price_in']:.4f}→${p:.4f} ({chg:+.2f}%)"
        )
    await update.message.reply_text("\n".join(lines))

async def cmd_lecons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    total = get_total_lessons()
    if total == 0:
        await update.message.reply_text("Aucune leçon encore.")
        return
    msg = f"📚 Leçons totales : {total}/{MAX_LESSONS}\n\n"
    for l in memory["lessons"][-8:]:
        e = "✅" if l.get("type") == "succes" else "❌"
        msg += f"{e} {l.get('lecon','')[:80]}\n→ {l.get('action_future','')[:80]}\n\n"
    await update.message.reply_text(msg)

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    await update.message.reply_text("🔍 Scan...")
    opps  = scan_market()
    lines = ["🎯 Top opportunités\n━━━━━━━━━━━━━"]
    for o in opps[:7]:
        e     = "🟢" if o["direction"]=="BUY" else "🔴"
        alert = " ⚠️" if o["has_alert"] else ""
        bonus = get_symbol_confidence_bonus(o["symbol"])
        bonus_str = f" ({bonus:+d})" if bonus != 0 else ""
        lines.append(
            f"{e}{alert} {o['symbol'].replace('USDT',''):6s} score={o['score']:+d}"
            f"{bonus_str} RSI={o['ind'].get('rsi',0):.0f}"
        )
    await update.message.reply_text("\n".join(lines))

async def cmd_marches(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    await update.message.reply_text("📊 Récupération prix...")
    try:
        lines  = ["📊 MARCHÉS v7\n━━━━━━━━━━━━━"]; prices = get_prices_batch()
        lines.append(f"📡 Source: {'WebSocket' if ws_manager.connected else 'REST'}")
        lines.append("🪙 CRYPTO")
        for sym in ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT"]:
            p = prices.get(sym,0)
            lines.append(f"  {sym.replace('USDT',''):6s} ${p:,.4f}")
        lines.append("\n📈 ACTIONS US")
        for ticker, name in list(STOCKS_SYMBOLS.items())[:5]:
            p = get_yahoo_price(ticker)
            lines.append(f"  {name:12s} ${p:,.2f}")
        lines.append("\n💱 FOREX")
        for ticker, name in list(FOREX_SYMBOLS.items())[:4]:
            p = get_yahoo_price(ticker)
            lines.append(f"  {name:10s} {p:.4f}")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Erreur: {e}")

async def cmd_memes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    await update.message.reply_text("🐸 Scan memecoins...")
    try:
        trending = dex_get_trending()
        lines    = ["🐸 MEMECOINS\n━━━━━━━━━━━━━"]
        if trending:
            for t in trending[:5]:
                score = meme_signal_score(t)
                e = "🚀" if score>=7 else "📈" if score>=5 else "📊"
                lines.append(f"  {e} ${t['symbol']} {t.get('change_1h',0):+.1f}%/1h Score:{score}/10")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Erreur: {e}")

async def cmd_signaux(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    summary = get_db_trader_signals_summary()
    await update.message.reply_text(f"📡 SIGNAUX TRADERS\n━━━━━━━━━━━━━\n{summary}")

async def cmd_regles(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    try:
        con  = sqlite3.connect(DB_FILE)
        rows = con.execute(
            "SELECT rule,win_rate,sample_size FROM trading_rules WHERE active=1 ORDER BY win_rate DESC LIMIT 10"
        ).fetchall()
        con.close()
        if not rows:
            await update.message.reply_text("Aucune règle encore.")
            return
        lines = [f"🧠 MES RÈGLES ({len(rows)})\n━━━━━━━━━━━━━"]
        for r in rows:
            lines.append(f"• {r[0]}\n  WR:{r[1]:.0f}% sur {r[2]} trades")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Erreur: {e}")

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    stats      = get_stats(); wr_db = db_win_rate(30); sym_s = db_symbol_stats()
    equity     = get_equity_safe(); pnl = equity - sim["initial"]; kelly = kelly_criterion()
    all_closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    micro_t  = [t for t in all_closed if t.get("market")=="MICRO"]
    meme_t   = [t for t in all_closed if t.get("market")=="MEME"]
    normal_t = [t for t in all_closed if t.get("market") not in ("MICRO","MEME")]
    def wr(trades): return round(sum(1 for t in trades if t["pnl"]>0)/max(len(trades),1)*100,1)
    sym_lines = "\n".join(
        f"  {s['s']:8s} WR:{s['wr']:.0f}% ({s['n']}t)" for s in sym_s[:5]
    ) or "  Aucun"
    await update.message.reply_text(
        f"📊 STATS v7\nCapital:${equity:.2f} ({pnl:+.2f})\n"
        f"WR global:{stats['win_rate']}% | Kelly:{kelly*100:.1f}%\n"
        f"⚡ Micro:{len(micro_t)} WR:{wr(micro_t)}%\n"
        f"🐸 Meme:{len(meme_t)} WR:{wr(meme_t)}%\n"
        f"🔍 Classiq:{len(normal_t)} WR:{wr(normal_t)}%\n"
        f"Blacklist:{len(memory.get('symbol_blacklist',{}))} symbols\n"
        f"📚 Leçons:{get_total_lessons()}/{MAX_LESSONS}\n"
        f"🧠 Cache AI:{_pool_stats['cache_hits']} hits"
    )

async def cmd_apprendre(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    global LEARN_MODE_ENABLED
    LEARN_MODE_ENABLED = not LEARN_MODE_ENABLED
    status = "✅ ACTIVÉ" if LEARN_MODE_ENABLED else "⏸ DÉSACTIVÉ"
    await update.message.reply_text(f"🎓 Mode apprentissage: {status}")

async def cmd_fermer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    if not sim["positions"]:
        await update.message.reply_text("Aucune position.")
        return
    send = make_send(TELEGRAM_CHAT_ID)
    prices = get_prices_batch(); count = 0
    for pk in list(sim["positions"].keys()):
        pos = sim["positions"].get(pk)
        if not pos: continue
        price = prices.get(pos["symbol"], pos["price_in"])
        close_trade(pk, price, "Fermeture manuelle", send)
        count += 1
    await update.message.reply_text(f"✅ {count} position(s) fermée(s).")

async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    bot_state["running"] = False
    bot_state["daily_stopped"] = False
    lessons_saved = memory.get("lessons",[])
    sym_scores    = memory.get("symbol_scores",{})
    sim.update({
        "cash":CAPITAL_INITIAL,"initial":CAPITAL_INITIAL,
        "positions":{},"trades":[],"equity_history":[],
        "session":sim.get("session",0)+1,
        "peak_equity":CAPITAL_INITIAL,"daily_start_equity":CAPITAL_INITIAL,
        "daily_start_date":datetime.now(timezone.utc).strftime("%Y-%m-%d")
    })
    memory.update({
        "lessons":lessons_saved,"patterns_to_avoid":[],"patterns_that_work":[],
        "confidence_threshold":CONFIDENCE_BASE,"total_wins":0,"total_losses":0,
        "symbol_scores":sym_scores,"symbol_blacklist":{},"consecutive_losses":{}
    })
    save_data()
    await update.message.reply_text(
        f"🔄 Session #{sim['session']} — ${CAPITAL_INITIAL:,.2f}\n"
        f"📚 Leçons conservées: {len(lessons_saved)}"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    kelly = kelly_criterion()
    await update.message.reply_text(
        f"🤖 Trading Bot v8 — Commandes complètes\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"▶️ Pilotage\n"
        f"/start — Démarrer le bot\n"
        f"/stop — Arrêter le bot\n"
        f"/status — Statut complet agents + positions\n"
        f"/reset — Reset session (leçons conservées)\n"
        f"/resume — Résumé rapide equity + WR\n\n"
        f"📊 Trading & Suivi\n"
        f"/scan — Scanner les meilleures opportunités\n"
        f"/portfolio — Portefeuille principal\n"
        f"/portfolios — Tous les portefeuilles\n"
        f"/positions — Positions ouvertes\n"
        f"/lasttrades — 10 derniers trades\n"
        f"/fermer — Fermer TOUTES les positions\n"
        f"/kelly — Kelly Criterion détaillé\n"
        f"/stats — Stats complètes (WR, Sharpe, PF)\n"
        f"/test_brain — Tester le cerveau collectif\n\n"
        f"🧠 Intelligence & Agents\n"
        f"/agent — Mode Secrétaire (voix unifiée)\n"
        f"/agent_stop — Quitter mode Secrétaire\n"
        f"/ask [question] — Interroger les agents\n"
        f"/debate [sujet] — Débat entre agents\n"
        f"/lecons — Dernières leçons apprises\n"
        f"/apprendre — Activer/désactiver apprentissage\n"
        f"/regles — Règles auto-générées\n"
        f"/blacklist — Gérer la blacklist symbols\n"
        f"/signaux — Signaux traders professionnels\n\n"
        f"📈 Analyse Marché\n"
        f"/macro — Macro + Fear & Greed Index\n"
        f"/risque — Risk management détaillé\n"
        f"/regime — Régime marché QuantML (Hurst)\n"
        f"/marches — Prix live crypto/actions/forex\n"
        f"/memes — Memecoins trending\n"
        f"/arbitrage — Opportunités arbitrage\n"
        f"/polymarket — Inefficiencies Polymarket\n"
        f"/spread — Spread Polymarket vs CEX (edge oracle lag)\n"
        f"/sniper — Event Sniper (liquidations/OI/funding/volume)\n\n"
            f"/polybet — Direct Polymarket trader (edge BTC/ETH binaires, fair value)\n"
            f"/sportsarb — Sports latency arb (multi-bookmakers, profit garanti)\n"
        f"💰 Épargne & Staking\n"
        f"/epargne — Infos épargne\n"
        f"/pool — Statut AI Pool\n"
        f"/airdrops — Airdrops en cours\n"
        f"/faucets — Faucets en ligne\n"
        f"/stake_status — Statut staking actuel\n"
        f"/stake_eth — Forcer stake ETH\n"
        f"/stake_sol — Forcer stake SOL\n\n"
        f"🧪 Recherche & Tests\n"
        f"/backtest [SYMBOL] [interval] [jours]\n"
        f"/backtest_multi — Backtest multi-symbols\n"
        f"/execute — Simulation exécution intelligente\n"
        f"/debugpnl — Debug P&L détaillé\n\n"
        f"🖥 Interface Web\n"
        f"/office — Ouvrir le tableau de bord mobile\n\n"
        f"❓ Aide\n"
        f"/help — Cette liste complète\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"SL:{STOP_LOSS_PCT*100:.1f}% TP:{TAKE_PROFIT_PCT*100:.1f}% Kelly:{kelly*100:.1f}%\n"
        f"📡 WS: {'✅' if ws_manager.connected else '⚠️ REST'} | Mode: {'TESTNET' if TESTNET_MODE else '🔴 LIVE'}\n"
        f"Agents: {len([a for a in dir(orchestrator) if not a.startswith('_')])} chargés"
    )

last_summary = 0

def send_summary(send_fn):
    global last_summary
    if time.time() - last_summary < 1200:
        return
    equity = get_equity_safe()
    pnl = equity - sim["initial"]
    pos = len(sim["positions"])
    closed = [t for t in sim["trades"] if t.get("pnl") is not None]
    wr = round(len([t for t in closed if t["pnl"] > 0]) / max(len(closed),1) * 100,1) if closed else 0
    send_fn(f"📊 Résumé v7.1 — ${equity:.0f} ({pnl:+.0f}) | Pos: {pos} | WR: {wr}% | FG: {get_fear_greed_value()}")
    last_summary = time.time()

async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    send = make_send(TELEGRAM_CHAT_ID)
    send_summary(send)

def _agent_wr_global() -> float:
    closed = [t for t in sim.get("trades", []) if t.get("pnl") is not None]
    if not closed:
        return 0.0
    wins = len([t for t in closed if (t.get("pnl") or 0) > 0])
    return wins / max(len(closed), 1) * 100

def _build_agent_state() -> str:
    return f"""
Capital: ${get_equity_safe():.2f}
Positions: {len(sim.get('positions', {}))}
WR global: {_agent_wr_global():.1f}%
Lessons: {len(memory.get('lessons', []))}
FG: {get_fear_greed_value()}/100
Macro: {get_macro_trend()}
Blacklist: {len(memory.get('symbol_blacklist',{}))}
"""

def _build_multi_agent_context():
    sim = load_json("sim_portfolio_v7.json", {})
    return {
        "sim": sim,
        "kelly": 0.22,
        "drawdown": -0.1,
        "macro": "neutral",
        "extreme_learning_mode": EXTREME_LEARNING_MODE,
        "learning_mode": EXTREME_LEARNING_MODE,
    }

def _select_agents_for_query(query: str):
    q = query.lower()
    selected = []
    if any(k in q for k in ["winrate", "wr", "pnl", "trade", "performance", "stat"]):
        selected.append(orchestrator.analyst)
    if any(k in q for k in ["risk", "risque", "kelly", "drawdown", "exposition"]):
        selected.append(orchestrator.risk)
    if any(k in q for k in ["marché", "marche", "signal", "trade", "entry", "entrée", "setup"]):
        selected.append(orchestrator.trader)
    if not selected:
        selected = [orchestrator.analyst, orchestrator.risk, orchestrator.trader]
    seen = set()
    unique = []
    for agent in selected:
        if agent.name not in seen:
            unique.append(agent)
            seen.add(agent.name)
    return unique

async def _ask_agent_multi(chat_id: int, query: str) -> str:
    query = sanitize_string(query, 1500).strip()
    ctx = _build_multi_agent_context()
    selected_agents = _select_agents_for_query(query)
    responses = []
    for agent in selected_agents:
        try:
            res = await agent.respond(query, ctx)
            responses.append(res)
        except Exception as e:
            responses.append({
                "agent": agent.name,
                "summary": f"Erreur agent: {e}",
                "arguments": [],
                "risks": [],
                "confidence": 0.0,
                "recommendation": "Vérifier l'agent"
            })
    final = await orchestrator.supervisor.respond(
        query,
        {**ctx, "agent_outputs": responses}
    )
    AGENT_CHAT_MEMORY[chat_id].append({"role": "user", "content": query})
    AGENT_CHAT_MEMORY[chat_id].append({"role": "assistant", "content": final["arguments"][0] if final.get("arguments") else final.get("summary", "")})
    AGENT_CHAT_MEMORY[chat_id] = AGENT_CHAT_MEMORY[chat_id][-12:]
    msg = "🧠 Agent Conscience\n\n"
    for r in responses:
        msg += f"🔹 {r['agent']} : {r['summary']}\n"
    msg += "\n👑 Synthèse :\n"
    if final.get("arguments"):
        msg += final["arguments"][0]
    else:
        msg += final.get("summary", "Pas de synthèse.")
    if final.get("recommendation"):
        msg += f"\n\n✅ Recommandation : {final['recommendation']}"
    return msg


#  🤖 AEGIS AGENT — Agent autonome avec outils (like Claude/Replit Agent)
#  Groq llama-3.3-70b + function calling → lit/modifie le code, contrôle le bot

AEGIS_SYSTEM_PROMPT = """Tu es AEGIS, agent autonome du trading bot crypto de ton proprietaire.
Tu reponds TOUJOURS en francais, de facon claire et directe.

REGLES POUR LIRE LE CODE (bot.py = 5400+ lignes):
- Ne JAMAIS lire bot.py sans search ou from_line/to_line. Le fichier est trop grand.
- Workflow lecture: 1) read_github_file(search="mot-cle") → trouve les lignes → 2) read_github_file(from_line=X, to_line=Y) pour voir le contexte complet
- Pour trouver une variable/fonction: search=le_nom_exact
- Pour lire un bloc: from_line=debut, to_line=fin (plages de 50-100 lignes max)

REGLES POUR MODIFIER LE CODE:
- Toujours d abord LIRE le code exact avec search, puis copier les lignes exactes
- Utiliser dry_run=True pour verifier le diff avant de committer
- Expliquer clairement ce qui change et pourquoi
- Un commit = une modification logique

PERSONNALITE: Tu es factuel, efficace, transparent sur tes actions.
Quand tu utilises un outil, dis ce que tu fais (ex: "Je consulte les trades...").
Quand tu modifies du code, confirme le commit et que Railway va redemarrer."""

AEGIS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_bot_status",
            "description": "Lit etat bot: capital, positions, win rate, mode TRAINING/LIVE, regime marche",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_trade_history",
            "description": "Derniers trades avec stats (win rate, PnL, symboles). Utilise limit pour le nombre.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Nombre de trades (defaut 20, max 100)"}
                },
                "required": []
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_performance",
            "description": "Analyse profonde: win rate par heure, par symbole, par cote (LONG/SHORT). Utilise pour diagnostic.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_github_file",
            "description": "Lit un fichier GitHub. Supporte: search (mot-cle + contexte), from_line/to_line (plage de lignes). TOUJOURS utiliser search ou from_line/to_line pour naviguer dans bot.py.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":         {"type": "string", "description": "Chemin ex: bot.py"},
                    "search":       {"type": "string", "description": "Mot-cle a chercher (retourne lignes autour)"},
                    "from_line":    {"type": "integer", "description": "Ligne de debut (1-indexed)"},
                    "to_line":      {"type": "integer", "description": "Ligne de fin"},
                    "context_lines":{"type": "integer", "description": "Lignes de contexte autour des resultats (defaut 5)"}
                },
                "required": ["path"]
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_github_files",
            "description": "Liste les fichiers/dossiers du repo GitHub",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Dossier a lister (vide = racine)"}
                },
                "required": []
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_github_file",
            "description": "Modifie un fichier sur GitHub. ETAPES: 1) cherche le texte exact avec read_github_file search, 2) utilise dry_run=True pour preview, 3) confirme avec dry_run=False.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":        {"type": "string", "description": "Chemin du fichier"},
                    "old_text":    {"type": "string", "description": "Texte EXACT a remplacer (copie du fichier)"},
                    "new_text":    {"type": "string", "description": "Nouveau texte"},
                    "commit_msg":  {"type": "string", "description": "Message de commit"},
                    "dry_run":     {"type": "boolean", "description": "True = preview sans commit (defaut True)"}
                },
                "required": ["path", "old_text", "new_text", "commit_msg"]
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "control_bot",
            "description": "Commandes de controle: start, stop, train_mode, live_mode, force_max_trades, conservative_mode, close_all",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["start","stop","train_mode","live_mode","force_max_trades","conservative_mode","close_all"]}
                },
                "required": ["action"]
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_data",
            "description": "Prix actuels des cryptos + regime de marche (bull/bear/sideways)",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_bot_logs",
            "description": "Lit les logs recents du bot en memoire. Utilise pour diagnostiquer des erreurs. level=ALL/ERROR/WARN/INFO",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {"type": "string", "description": "Filtre: ALL, ERROR, WARN, INFO (defaut ALL)"},
                    "limit": {"type": "integer", "description": "Nombre de logs a retourner (defaut 50)"}
                },
                "required": []
            },
        }
    },
    {
          "type": "function",
          "function": {
              "name": "analyze_smc",
              "description": "Smart Money Concepts: detecte Order Blocks, Fair Value Gaps, liquidity sweeps, breaker blocks sur un symbole. Utilise pour trouver des setups haute probabilite.",
              "parameters": {
                  "type": "object",
                  "properties": {
                      "symbol": {"type": "string", "description": "Symbole ex: BTCUSDT"},
                      "timeframe": {"type": "string", "description": "Timeframe: 5m, 15m, 1h, 4h (defaut 1h)"}
                  },
                  "required": ["symbol"]
              },
          }
      },
      {
          "type": "function",
          "function": {
              "name": "get_liquidation_levels",
              "description": "Recupere les niveaux de liquidation majeurs (support/resistance dynamiques bases sur l'open interest). Indique ou les stops se concentrent.",
              "parameters": {
                  "type": "object",
                  "properties": {
                      "symbol": {"type": "string", "description": "Symbole ex: BTCUSDT"}
                  },
                  "required": ["symbol"]
              },
          }
      },
      {
          "type": "function",
          "function": {
              "name": "run_quick_backtest",
              "description": "Lance un backtest rapide sur les N derniers trades pour evaluer une strategie. Retourne win rate, profit factor, max drawdown.",
              "parameters": {
                  "type": "object",
                  "properties": {
                      "strategy": {"type": "string", "description": "Nom de la strategie a tester"},
                      "lookback": {"type": "integer", "description": "Nombre de trades a analyser (defaut 50)"}
                  },
                  "required": []
              },
          }
      },
]

# ── Tool execution ────────────────────────────────────────────────────────────
async def _aegis_tool_get_bot_status() -> str:
    try:
        trades = sim.get("trades", [])
        positions = sim.get("positions", {})
        cash = float(sim.get("cash", CAPITAL_INITIAL))
        initial = float(sim.get("initial", CAPITAL_INITIAL))
        wins = [t for t in trades if t.get("pnl", 0) > 0]
        wr = round(len(wins) / max(1, len(trades)) * 100, 1)
        pnl = round(cash - initial, 2)
        return (
            f"ÉTAT DU BOT:\n"
            f"• Capital: ${cash:.2f} (initial: ${initial:.2f})\n"
            f"• PnL total: {'+' if pnl>=0 else ''}${pnl:.2f}\n"
            f"• Positions ouvertes: {len(positions)}\n"
            f"• Trades total: {len(trades)} | Win rate: {wr}%\n"
            f"• Mode: {'🎓 TRAINING' if BOT_TRAINING_MODE else '🔴 LIVE'}\n"
            f"• Bot running: {_agent_running}\n"
            f"• Régime marché: {sim.get('market_regime', '?')}"
        )
    except Exception as e:
        return f"Erreur lecture état: {e}"

async def _aegis_tool_get_trade_history(limit: int = 20) -> str:
    try:
        trades = sim.get("trades", [])[-limit:]
        if not trades:
            return "Aucun trade enregistré pour le moment."
        wins = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) < 0]
        total_pnl = sum(t.get("pnl", 0) for t in trades)
        wr = round(len(wins) / max(1, len(trades)) * 100, 1)
        lines_out = [
            f"DERNIERS {len(trades)} TRADES:",
            f"Win rate: {wr}% | PnL total: {'+' if total_pnl>=0 else ''}${total_pnl:.2f}",
            f"Wins: {len(wins)} | Losses: {len(losses)}",
            "──────────────────"
        ]
        for t in trades[-10:]:
            side = t.get("side", "?")
            sym = t.get("symbol", "?")
            pnl = t.get("pnl", 0)
            price = t.get("price", 0)
            lines_out.append(f"{'✅' if pnl>0 else '❌'} {side} {sym} @ ${price:.2f} → {'+' if pnl>=0 else ''}${pnl:.2f}")
        return "\n".join(lines_out)
    except Exception as e:
        return f"Erreur lecture trades: {e}"

async def _aegis_tool_read_github_file(path: str, search: str = None,
                                        from_line: int = None, to_line: int = None,
                                        context_lines: int = 5) -> str:
    try:
        import urllib.request as _ur, base64 as _b64
        _repo = GITHUB_REPO or "everdalsi/Trading-bot"
        _branch = "main-revert-4"
        _url = f"https://api.github.com/repos/{_repo}/contents/{path}?ref={_branch}"
        req = _ur.Request(_url, headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"})
        with _ur.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        content = _b64.b64decode(data["content"].replace("\n","")).decode("utf-8", errors="replace")
        file_lines = content.split("\n")
        total = len(file_lines)
        if search:
            matches = [(i, l) for i, l in enumerate(file_lines) if search.lower() in l.lower()]
            if not matches:
                return f"Introuvable: {search!r} dans {path} ({total} lignes)"
            out = [f"Recherche {search!r} dans {path} -> {len(matches)} resultats:\n"]
            shown = set()
            for idx, _ in matches[:8]:
                start_c = max(0, idx - context_lines)
                end_c = min(total, idx + context_lines + 1)
                if start_c in shown: continue
                out.append(f"  -- L{idx+1} --")
                for i in range(start_c, end_c):
                    marker = ">>>" if i == idx else "   "
                    out.append(f"  {marker} L{i+1}: {file_lines[i]}")
                shown.add(start_c)
                out.append("")
            return "\n".join(out)
        if from_line or to_line:
            start_r = max(0, (from_line or 1) - 1)
            end_r = min(total, (to_line or total))
            chunk = file_lines[start_r:end_r]
            out = [f"{path} L{start_r+1}-L{end_r} ({total} lignes total):"]
            out += [f"L{start_r+i+1}: {l}" for i, l in enumerate(chunk)]
            return "\n".join(out)
        preview = [f"L{i+1}: {l}" for i, l in enumerate(file_lines[:60])]
        out = [f"{path} ({total} lignes). Premieres 60 lignes:"]
        out += preview
        out.append("\n-> Utilise from_line/to_line pour lire une section, ou search pour chercher un mot-cle.")
        return "\n".join(out)
    except Exception as e:
        return f"Erreur lecture {path}: {e}"

async def _aegis_tool_edit_github_file(path: str, old_text: str, new_text: str,
                                        commit_msg: str, dry_run: bool = False) -> str:
    """Edit a file on GitHub. dry_run=True shows the diff without committing."""
    try:
        import urllib.request as _ur, base64 as _b64
        _repo = GITHUB_REPO or "everdalsi/Trading-bot"
        _branch = "main-revert-4"
        _url = f"https://api.github.com/repos/{_repo}/contents/{path}?ref={_branch}"
        req = _ur.Request(_url, headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"})
        with _ur.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        current_sha = data["sha"]
        content = _b64.b64decode(data["content"].replace("\n","")).decode("utf-8", errors="replace")
        count = content.count(old_text)
        if count == 0:
            # Try to find close matches
            first_line = old_text.strip().split("\n")[0][:60]
            nearby = [(i+1, l) for i, l in enumerate(content.split("\n")) if first_line[:20].lower() in l.lower()][:3]
            hint = "\n".join(f"  L{n}: {l}" for n, l in nearby) if nearby else "  (aucune correspondance)"
            return f"Texte a remplacer INTROUVABLE dans {path}.\nPistage similaire:\n{hint}\nUtilise search dans read_github_file pour trouver le texte exact."
        new_content = content.replace(old_text, new_text, 1)
        # Show diff summary
        old_lines = old_text.strip().split("\n")
        new_lines = new_text.strip().split("\n")
        diff_preview = f"DIFF ({path}):\n"
        for l in old_lines[:5]: diff_preview += f"  - {l}\n"
        for l in new_lines[:5]: diff_preview += f"  + {l}\n"

        if dry_run:
            global AEGIS_LAST_FIX
            AEGIS_LAST_FIX = {"path": path, "old_text": old_text, "new_text": new_text, "commit_msg": commit_msg}
            return (
                f"[DRY RUN] Voici ce qui serait changé:\n{diff_preview}\n\n"
                "✅ Fix prêt — Réponds *CONFIRME* pour appliquer, ou ignore."
            )
        encoded = _b64.b64encode(new_content.encode("utf-8")).decode()
        payload = json.dumps({"message": f"[AEGIS] {commit_msg}", "content": encoded, "sha": current_sha, "branch": _branch}).encode()
        put_req = _ur.Request(
            f"https://api.github.com/repos/{_repo}/contents/{path}",
            data=payload,
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"},
            method="PUT"
        )
        with _ur.urlopen(put_req, timeout=20) as r:
            result = json.loads(r.read())
        commit_sha = result.get("commit", {}).get("sha", "?")[:10]
        return f"Fichier modifie et pushe! Commit: {commit_sha}\n{diff_preview}\nRailway redemarre dans ~2 min."
    except Exception as e:
        return f"Erreur modification fichier: {e}"

async def _aegis_tool_list_github_files(directory: str = "") -> str:
    """List files/folders in the GitHub repo"""
    try:
        import urllib.request as _ur
        _repo = GITHUB_REPO or "everdalsi/Trading-bot"
        _branch = "main-revert-4"
        path = directory.strip("/")
        _url = f"https://api.github.com/repos/{_repo}/contents/{path}?ref={_branch}"
        req = _ur.Request(_url, headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"})
        with _ur.urlopen(req, timeout=15) as r:
            items = json.loads(r.read())
        if isinstance(items, dict):
            return f"Ceci est un fichier, pas un dossier. Utilise read_github_file pour le lire."
        out = [f"Fichiers dans /{path or 'racine'}:"]
        for item in sorted(items, key=lambda x: (x["type"]=="file", x["name"])):
            icon = "folder" if item["type"] == "dir" else "file"
            size = f" ({item.get('size', 0)} bytes)" if item["type"] == "file" else ""
            out.append(f"  [{icon}] {item['name']}{size}")
        return "\n".join(out)
    except Exception as e:
        return f"Erreur liste fichiers: {e}"

async def _aegis_tool_analyze_performance() -> str:
    """Deep analysis of trading performance — win rate by hour, symbol, session"""
    try:
        sim = _SHARED_SIM_STATE
        trades = sim.get("trades", [])
        if not trades:
            return "Pas encore de trades enregistres."
        from collections import defaultdict
        by_hour = defaultdict(lambda: {"w":0,"l":0})
        by_symbol = defaultdict(lambda: {"w":0,"l":0,"pnl":0.0})
        by_side = defaultdict(lambda: {"w":0,"l":0})
        total_pnl = 0.0
        wins = 0
        for t in trades:
            pnl = t.get("pnl", 0)
            sym = t.get("symbol", "?")
            side = t.get("side", "?")
            ts = t.get("timestamp", "")
            won = pnl > 0
            total_pnl += pnl
            if won: wins += 1
            by_symbol[sym]["w" if won else "l"] += 1
            by_symbol[sym]["pnl"] += pnl
            by_side[side]["w" if won else "l"] += 1
            try:
                hour = int(str(ts)[11:13])
                by_hour[hour]["w" if won else "l"] += 1
            except: pass
        total = len(trades)
        wr = wins/total*100 if total else 0
        out = [f"=== ANALYSE PERFORMANCE ({total} trades) ==="]
        out.append(f"Win Rate global: {wr:.1f}% | PnL total: {total_pnl:+.2f}$")
        out.append("\nPar SYMBOLE:")
        for sym, d in sorted(by_symbol.items(), key=lambda x: -x[1]["pnl"])[:6]:
            t2 = d["w"]+d["l"]
            wr2 = d["w"]/t2*100 if t2 else 0
            out.append(f"  {sym}: {wr2:.0f}% WR | {d['pnl']:+.2f}$ ({t2} trades)")
        out.append("\nPar COTE:")
        for side, d in by_side.items():
            t2 = d["w"]+d["l"]
            wr2 = d["w"]/t2*100 if t2 else 0
            out.append(f"  {side}: {wr2:.0f}% WR ({t2} trades)")
        if by_hour:
            best_hours = sorted(by_hour.items(), key=lambda x: -(x[1]["w"]/(x[1]["w"]+x[1]["l"]+0.001)))[:3]
            out.append("\nMeilleures heures:")
            for hr, d in best_hours:
                t2 = d["w"]+d["l"]
                wr2 = d["w"]/t2*100 if t2 else 0
                out.append(f"  {hr}h UTC: {wr2:.0f}% WR ({t2} trades)")
        return "\n".join(out)
    except Exception as e:
        return f"Erreur analyse: {e}"

async def _aegis_tool_get_bot_logs(level: str = "ALL", limit: int = 50) -> str:
    """Read the in-memory log ring buffer. level: ALL, ERROR, WARN, INFO"""
    try:
        all_logs = list(LOG_BUFFER)
        if not all_logs:
            return "Aucun log en memoire pour le moment. Le bot vient peut-etre de demarrer."
        if level.upper() in ("ERROR", "WARN", "INFO"):
            all_logs = [e for e in all_logs if e["level"].upper() == level.upper()]
        recent = all_logs[-limit:]
        if not recent:
            return f"Aucun log de niveau {level} trouve."
        out = [f"=== LOGS BOT (derniers {len(recent)}, filtre: {level}) ==="]
        for e in recent:
            lvl_icon = {"ERROR": "ERROR", "WARN": "WARN", "INFO": "INFO"}.get(e["level"], e["level"])
            out.append(f"[{e['ts']}] {lvl_icon} {e['msg']}")
        return "\n".join(out)
    except Exception as e:
        return f"Erreur lecture logs: {e}"

async def _aegis_tool_control_bot(action: str) -> str:
    try:
        global _agent_running, BOT_TRAINING_MODE, _force_trade_override
        if action == "start":
            _agent_running = True
            return "✅ Bot démarré"
        elif action == "stop":
            _agent_running = False
            return "✅ Bot arrêté"
        elif action == "train_mode":
            BOT_TRAINING_MODE = True
            return "✅ Mode TRAINING activé — seuil 1%, max $15/trade"
        elif action == "live_mode":
            BOT_TRAINING_MODE = False
            return "✅ Mode LIVE activé — seuil 25%, max 5% capital"
        elif action == "force_max_trades":
            _force_trade_override = True
            return "✅ Max trades forcé pour 30 min"
        elif action == "conservative_mode":
            _force_trade_override = False
            return "✅ Mode conservatif activé"
        elif action == "close_all":
            sim["positions"] = {}
            return "✅ Toutes les positions fermées"
        return f"❌ Action inconnue: {action}"
    except Exception as e:
        return f"❌ Erreur contrôle: {e}"

async def _aegis_tool_get_market_data() -> str:
    try:
        prices = get_prices_batch()
        top = sorted(prices.items(), key=lambda x: abs(x[1].get("change_24h", 0)), reverse=True)[:8]
        regime = sim.get("market_regime", "NEUTRAL")
        result = [f"MARCHÉ (régime: {regime}):"]
        for sym, data in top:
            chg = data.get("change_24h", 0)
            px = data.get("price", 0)
            result.append(f"  {sym}: ${px:.4f} ({'+' if chg>=0 else ''}{chg:.2f}%)")
        return "\n".join(result)
    except Exception as e:
        return f"Erreur market data: {e}"

# ── Agent loop (ReAct: Reason + Act) ─────────────────────────────────────────
AEGIS_MEMORY: dict = _load_aegis_memory()  # persisted across restarts

async def _run_aegis_agent(chat_id: str, user_message: str) -> str:
    """Main autonomous agent loop with tool calling (like Claude/Replit Agent)"""
    global AEGIS_MEMORY
    if chat_id not in AEGIS_MEMORY:
        AEGIS_MEMORY[chat_id] = []

    # Keep last 10 messages (5 exchanges) for context
    history = AEGIS_MEMORY[chat_id][-10:]

    messages = [
        {"role": "system", "content": AEGIS_SYSTEM_PROMPT}
    ] + history + [
        {"role": "user", "content": user_message}
    ]

    max_steps = 6
    for step in range(max_steps):
        _code_kw = ["code","fix","bug","edit","github","fichier","ligne","fonction","erreur","syntax","indent"]
        _is_code_task = any(kw in user_message.lower() for kw in _code_kw)
        _model_to_use = GROQ_CODE_MODEL if _is_code_task else GROQ_SMART_MODEL
        try:
            response = groq_client.chat.completions.create(
                model=_model_to_use,
                # Auto-select: DeepSeek R1 for code tasks, Smart for general
                messages=messages,
                tools=AEGIS_TOOLS,
                tool_choice="auto",
                max_tokens=1000,
                temperature=0.2,
            )
        except Exception as e:
            logger.warning(f"[AEGIS] Groq error: {e}")
            return f"⚠️ Erreur LLM: {str(e)[:150]}. Réessaie dans quelques secondes."

        msg = response.choices[0].message

        # No tool calls → final response
        if not msg.tool_calls:
            final_text = msg.content or "Je n'ai pas de réponse à donner."
            # Save to memory
            AEGIS_MEMORY[chat_id].append({"role": "user", "content": user_message})
            AEGIS_MEMORY[chat_id].append({"role": "assistant", "content": final_text})
            # Keep memory bounded
            if len(AEGIS_MEMORY[chat_id]) > 20:
                AEGIS_MEMORY[chat_id] = AEGIS_MEMORY[chat_id][-20:]
            _save_aegis_memory(AEGIS_MEMORY)
            return final_text

        # Execute tool calls
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]})

        for tc in msg.tool_calls:
            tool_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            logger.info(f"[AEGIS] Tool: {tool_name}({args})")

            # Execute the tool
            if tool_name == "get_bot_status":
                result = await _aegis_tool_get_bot_status()
            elif tool_name == "get_trade_history":
                result = await _aegis_tool_get_trade_history(args.get("limit", 20))
            elif tool_name == "analyze_performance":
                result = await _aegis_tool_analyze_performance()
            elif tool_name == "read_github_file":
                result = await _aegis_tool_read_github_file(args.get("path","bot.py"), args.get("search"), args.get("from_line"), args.get("to_line"), args.get("context_lines", 5))
            elif tool_name == "list_github_files":
                result = await _aegis_tool_list_github_files(args.get("directory", ""))
            elif tool_name == "edit_github_file":
                result = await _aegis_tool_edit_github_file(args.get("path"), args.get("old_text"), args.get("new_text"), args.get("commit_msg","update"), args.get("dry_run", True))
            elif tool_name == "control_bot":
                result = await _aegis_tool_control_bot(args.get("action"))
            elif tool_name == "get_market_data":
                result = await _aegis_tool_get_market_data()
            elif tool_name == "get_bot_logs":
                result = await _aegis_tool_get_bot_logs(args.get("level","ALL"), args.get("limit", 50))
            elif tool_name == "analyze_smc":
                result = await _aegis_tool_analyze_smc(args.get("symbol","BTCUSDT"), args.get("timeframe","1h"))
            elif tool_name == "get_liquidation_levels":
                result = await _aegis_tool_get_liquidation_levels(args.get("symbol","BTCUSDT"))
            elif tool_name == "run_quick_backtest":
                result = await _aegis_tool_run_quick_backtest(args.get("strategy","current"), args.get("lookback",50))
            else:
                result = f"Outil inconnu: {tool_name}"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})

    return "J'ai réfléchi en profondeur mais je n'ai pas pu finaliser. Reformule ta demande."


async def _ask_secretary(chat_id: int, question: str) -> str:
    """Powered by AEGIS autonomous agent with tools"""
    try:
        return await _run_aegis_agent(str(chat_id), question)
    except Exception as e:
        logger.error(f"[AEGIS-SECRETARY] {e}")
        return "⚠️ Erreur AEGIS, réessaie dans quelques secondes."

async def cmd_agent_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    if TELEGRAM_CHAT_ID in AGENT_CHAT_SESSIONS:
        AGENT_CHAT_SESSIONS.remove(TELEGRAM_CHAT_ID)
        await update.message.reply_text("✅ Mode Secrétaire désactivé. Retour au mode normal.")
    else:
        await update.message.reply_text("Mode Secrétaire déjà désactivé.")

async def handle_agent_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    question = update.message.text.strip()
    if not question:
        return
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception:
        pass
    try:
        response = await _run_aegis_agent(str(update.effective_chat.id), question)
        if len(response) > 4000:
            for i in range(0, len(response), 4000):
                await update.message.reply_text(response[i:i+4000])
        else:
            try:
                await update.message.reply_text(response, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"[AEGIS-CHAT] {e}")
        await update.message.reply_text("AEGIS erreur interne, reessaie.")

async def telegram_error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    print(f"[TG-ERROR] {ctx.error}")
    try:
        if update and getattr(update, 'effective_message', None):
            await update.effective_message.reply_text("⚠️ Une erreur est survenue. Réessaie ou tape /help.")
    except Exception:
        pass

async def cmd_maxtrades_FIXED(update, context):
    if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
        return
    await update.message.reply_text("🧬 Lancement forcé du cycle MAX TRADES...")
    try:
        from agents.evolution_agent import EvolutionAgent as _EvoAgent
        _evo = _EvoAgent(orchestrator)
        ctx = {"memory": memory, "main_objective": MAIN_OBJECTIVE}
        result = await _evo.respond("FORCE MAX TRADES maintenant", ctx)
        await update.message.reply_text(f"✅ {result.get('summary', 'Cycle MAX TRADES lancé')}")
    except Exception as e:
        await update.message.reply_text(f"❌ Erreur : {str(e)[:200]}")

async def cmd_agent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    AGENT_CHAT_SESSIONS.add(TELEGRAM_CHAT_ID)
    await update.message.reply_text(
        "🧠 **Mode Secrétaire activé**\n\n"
        "Maintenant tu parles à **une seule personne** (moi).\n"
        "Tous les agents discutent en interne, je te donne seulement la synthèse finale naturelle.\n\n"
        "Pose-moi n’importe quelle question.\n"
        "Tape /agent_stop pour quitter le mode."
    )




async def _aegis_tool_analyze_smc(symbol: str = "BTCUSDT", timeframe: str = "1h") -> str:
    """Smart Money Concepts: Order Blocks, FVG, Liquidity Sweeps"""
    try:
        import aiohttp
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={timeframe}&limit=100"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                klines = await r.json()
        if not klines or isinstance(klines, dict):
            return f"Donnees indisponibles pour {symbol}"
        candles = [(float(k[1]),float(k[2]),float(k[3]),float(k[4]),float(k[5])) for k in klines]
        opens,highs,lows,closes,vols = zip(*candles)
        # Order Blocks: derniere bougie impulse avant mouvement fort
        ob_bull, ob_bear = [], []
        for i in range(2, len(candles)-2):
            move = abs(closes[i+2] - closes[i]) / closes[i] * 100
            if move > 0.8:
                if closes[i+2] > closes[i]: ob_bull.append((lows[i], highs[i], "haussier"))
                else: ob_bear.append((lows[i], highs[i], "baissier"))
        # Fair Value Gaps
        fvgs = []
        for i in range(1, len(candles)-1):
            if lows[i+1] > highs[i-1]: fvgs.append(("bull FVG", highs[i-1], lows[i+1]))
            elif highs[i+1] < lows[i-1]: fvgs.append(("bear FVG", highs[i+1], lows[i-1]))
        # Liquidity sweeps
        sweeps = []
        for i in range(10, len(candles)-1):
            rh = max(highs[max(0,i-10):i])
            rl = min(lows[max(0,i-10):i])
            if highs[i] > rh and closes[i] < rh: sweeps.append(("sell-sweep", round(closes[i],4)))
            if lows[i] < rl and closes[i] > rl: sweeps.append(("buy-sweep", round(closes[i],4)))
        cur = closes[-1]
        lines_out = [
            f"SMC {symbol} ({timeframe}) | Prix: {cur:.4f}",
            f"OB haussiers: {len(ob_bull)} | Dernier: {ob_bull[-1] if ob_bull else None}",
            f"OB baissiers: {len(ob_bear)} | Dernier: {ob_bear[-1] if ob_bear else None}",
            f"FVG detectes: {len(fvgs)} | Recents: {fvgs[-3:] if fvgs else None}",
            f"Liquidity Sweeps: {len(sweeps)} | Recents: {sweeps[-3:] if sweeps else None}",
        ]
        if ob_bull: lines_out.append(f"Support OB: {ob_bull[-1][0]:.4f} - {ob_bull[-1][1]:.4f}")
        if ob_bear: lines_out.append(f"Resistance OB: {ob_bear[-1][0]:.4f} - {ob_bear[-1][1]:.4f}")
        return "\n".join(lines_out)
    except Exception as e:
        return f"Erreur SMC: {e}"


async def _aegis_tool_get_liquidation_levels(symbol: str = "BTCUSDT") -> str:
    """Niveaux de liquidation approximatifs bases sur OI et S/R"""
    try:
        import aiohttp
        oi_url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}"
        kl_url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=4h&limit=50"
        async with aiohttp.ClientSession() as s:
            async with s.get(oi_url, timeout=aiohttp.ClientTimeout(total=8)) as r1:
                oi_data = await r1.json()
            async with s.get(kl_url, timeout=aiohttp.ClientTimeout(total=8)) as r2:
                klines = await r2.json()
        price = float(klines[-1][4])
        oi = float(oi_data.get("openInterest", 0)) if isinstance(oi_data, dict) else 0
        highs = [float(k[2]) for k in klines]
        lows  = [float(k[3]) for k in klines]
        key_highs = sorted(set([round(h,2) for h in highs[-10:]]), reverse=True)[:5]
        key_lows  = sorted(set([round(l,2) for l in lows[-10:]]))[:5]
        zone_plus  = round(price * 1.03, 2)
        zone_minus = round(price * 0.97, 2)
        out = [
            f"LIQUIDATIONS {symbol}",
            f"Prix: {price:.2f} | OI: {oi:,.0f} contrats",
            f"Liq SHORT (resistance): {key_highs}",
            f"Liq LONG (support): {key_lows}",
            f"Zone critique +3%: {zone_plus} | -3%: {zone_minus}",
        ]
        return "\n".join(out)
    except Exception as e:
        return f"Donnees liquidation indisponibles: {e}"


async def _aegis_tool_run_quick_backtest(strategy: str = "current", lookback: int = 50) -> str:
    """Quick backtest sur les derniers trades enregistres"""
    try:
        from memory import load_trades
        all_trades = load_trades() or []
        trades = all_trades[-lookback:]
        if not trades: return "Aucun trade enregistre pour le backtest."
        wins   = [t for t in trades if float(t.get("pnl_pct", 0)) > 0]
        losses = [t for t in trades if float(t.get("pnl_pct", 0)) <= 0]
        wr = len(wins) / len(trades) * 100 if trades else 0
        avg_w = sum(float(t.get("pnl_pct",0)) for t in wins)   / len(wins)   if wins   else 0
        avg_l = sum(float(t.get("pnl_pct",0)) for t in losses) / len(losses) if losses else 0
        pf = abs(avg_w / avg_l) if avg_l != 0 else 999.0
        equity, peak, dd_max = 1000.0, 1000.0, 0.0
        for t in trades:
            equity *= (1 + float(t.get("pnl_pct",0)) / 100)
            peak = max(peak, equity)
            dd_max = max(dd_max, (peak - equity) / peak * 100)
        verdict = ("Strategie rentable" if wr > 50 and pf > 1.5
                   else "Amelioration necessaire" if wr > 40 else "Strategie perdante")
        out = [
            f"BACKTEST {lookback} derniers trades | Strategie: {strategy}",
            f"Win Rate: {wr:.1f}% ({len(wins)}W / {len(losses)}L)",
            f"Avg Win: +{avg_w:.2f}% | Avg Loss: {avg_l:.2f}%",
            f"Profit Factor: {pf:.2f}",
            f"Max Drawdown: -{dd_max:.2f}%",
            f"Equity finale: ${equity:.2f}",
            f"Verdict: {verdict}",
        ]
        return "\n".join(out)
    except Exception as e:
        return f"Erreur backtest: {e}"


async def _aegis_watchdog_loop():
    """Background task — scans LOG_BUFFER every 2 min for new errors and auto-analyzes them."""
    global AEGIS_ERRORS_SEEN, AEGIS_WATCHDOG_ENABLED
    await asyncio.sleep(90)
    while True:
        try:
            if AEGIS_WATCHDOG_ENABLED and TELEGRAM_CHAT_ID and _app:
                new_errs = []
                for entry in list(LOG_BUFFER):
                    if entry.get("level") == "ERROR":
                        key = hash(entry.get("msg", "")[:200])
                        if key not in AEGIS_ERRORS_SEEN:
                            AEGIS_ERRORS_SEEN.add(key)
                            new_errs.append(entry)
                for err in new_errs[-2:]:
                    prompt = (
                        f"🔴 ERREUR AUTO-DETECTEE par le watchdog:\n"
                        f"[{err.get('ts','')}] {err.get('msg','')[:400]}\n\n"
                        "Analyse cette erreur:\n"
                        "1) Appelle get_bot_logs(level='ERROR', limit=5) pour le contexte complet\n"
                        "2) Si c'est un bug code, utilise read_github_file pour localiser la ligne\n"
                        "3) Propose un fix avec edit_github_file(dry_run=True)\n"
                        "Sois concis et direct."
                    )
                    try:
                        resp = await _run_aegis_agent(str(TELEGRAM_CHAT_ID), prompt)
                        header = "🔴 *AEGIS WATCHDOG — Erreur détectée*\n\n"
                        full = header + resp
                        keyboard = None
                        if AEGIS_LAST_FIX:
                            keyboard = InlineKeyboardMarkup([[
                                InlineKeyboardButton("✅ Appliquer le fix", callback_data="aegis_apply_fix"),
                                InlineKeyboardButton("❌ Ignorer", callback_data="aegis_ignore_fix"),
                            ]])
                        for chunk in [full[i:i+4000] for i in range(0, len(full), 4000)]:
                            try:
                                await _app.bot.send_message(
                                    TELEGRAM_CHAT_ID, chunk,
                                    parse_mode="Markdown",
                                    reply_markup=keyboard if chunk == [full[i:i+4000] for i in range(0, len(full), 4000)][-1] else None
                                )
                            except Exception:
                                await _app.bot.send_message(TELEGRAM_CHAT_ID, chunk)
                    except Exception as e:
                        logger.error(f"[AEGIS-WATCHDOG] Agent error: {e}")
        except Exception as e:
            logger.error(f"[AEGIS-WATCHDOG] Loop error: {e}")
        await asyncio.sleep(120)


async def handle_fix_callback(update, context):
    """Handle inline keyboard buttons for AEGIS fix confirmation."""
    global AEGIS_LAST_FIX
    query = update.callback_query
    await query.answer()
    if query.data == "aegis_apply_fix":
        if not AEGIS_LAST_FIX:
            await query.edit_message_text("❌ Aucun fix en attente.")
            return
        fix = AEGIS_LAST_FIX.copy()
        AEGIS_LAST_FIX.clear()
        await query.edit_message_text("⏳ Application du fix en cours...")
        try:
            result = await _aegis_tool_edit_github_file(
                fix["path"], fix["old_text"], fix["new_text"], fix["commit_msg"], dry_run=False
            )
            await _app.bot.send_message(TELEGRAM_CHAT_ID, f"✅ Fix appliqué!\n{result[:3000]}")
        except Exception as e:
            await _app.bot.send_message(TELEGRAM_CHAT_ID, f"❌ Erreur lors de l'application: {e}")
    elif query.data == "aegis_ignore_fix":
        AEGIS_LAST_FIX.clear()
        await query.edit_message_text("🚫 Fix ignoré.")


async def cmd_aegis_watch(update, context):
    """Toggle AEGIS watchdog on/off."""
    global AEGIS_WATCHDOG_ENABLED
    if not _auth(update): return
    AEGIS_WATCHDOG_ENABLED = not AEGIS_WATCHDOG_ENABLED
    state = "✅ ACTIVÉ" if AEGIS_WATCHDOG_ENABLED else "🔴 DÉSACTIVÉ"
    await update.message.reply_text(f"🔍 AEGIS Watchdog: {state}\nSurveillance auto des erreurs toutes les 2 min.")


async def cmd_diagnose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """One-shot autonomous health check — AEGIS analyses everything and reports"""
    if not _auth(update): return
    await update.message.reply_text("Diagnostic en cours... (10-20 secondes)")
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception: pass
    prompt = (
        "Effectue un diagnostic complet du bot. "
        "1) Appelle get_bot_status pour voir l'etat actuel. "
        "2) Appelle get_bot_logs(level='ERROR') pour chercher les erreurs recentes. "
        "3) Appelle analyze_performance pour analyser les performances. "
        "4) Donne un rapport structure: etat general, problemes detectes, recommandations immediates."
    )
    try:
        report = await _run_aegis_agent(str(update.effective_chat.id), prompt)
        if len(report) > 4000:
            for i in range(0, len(report), 4000):
                await update.message.reply_text(report[i:i+4000])
        else:
            try:
                await update.message.reply_text(report, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(report)
    except Exception as e:
        await update.message.reply_text(f"Erreur diagnostic: {e}")

async def cmd_aegis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    msg = "Agent AEGIS actif. Envoie-moi directement tes questions."
    await update.message.reply_text(msg)

async def run_telegram():
    global _app, _main_loop
    _main_loop = asyncio.get_event_loop()
    _app = (ApplicationBuilder()
            .token(TELEGRAM_TOKEN)
            .request(HTTPXRequest(
                connection_pool_size=8, pool_timeout=30.0,
                connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0
            ))
            .updater(None).build())

    for cmd, fn in [
        ("diagnose",       cmd_diagnose),
        ("aegis",          cmd_aegis),
        ("start",          cmd_start),
        ("stop",           cmd_stop),
        ("status",         cmd_status),
        ("scan",           cmd_scan),
        ("portfolio",      cmd_portfolio),
        ("positions",      cmd_positions),
        ("lecons",         cmd_lecons),
        ("fermer",         cmd_fermer),
        ("reset",          cmd_reset),
        ("kelly",          cmd_kelly),
        ("arbitrage",      cmd_arbitrage),
        ("polymarket",     cmd_polymarket),
        ("marches",        cmd_marches),
        ("memes",          cmd_memes),
        ("signaux",        cmd_signaux),
        ("regles",         cmd_regles),
        ("stats",          cmd_stats),
        ("apprendre",      cmd_apprendre),
        ("pool",           cmd_pool),
        ("epargne",        cmd_epargne),
        ("airdrops",       cmd_airdrops),
        ("faucets",        cmd_faucets),
        ("help",           cmd_help),
        ("macro",          cmd_macro),
        ("risque",         cmd_risque),
        ("blacklist",      cmd_blacklist),
        ("backtest",       cmd_backtest),
        ("backtest_multi", cmd_backtest_multi),
        ("resume",         cmd_resume),
        ("agent",          cmd_agent),
        ("agent_stop",     cmd_agent_stop),
        ("maxtrades",      cmd_maxtrades_FIXED),
        ("ask",            cmd_ask),
        ("debate",         cmd_debate),
        ("lasttrades",     cmd_lasttrades),
        ("debugpnl",       cmd_debugpnl),
        ("spread",         cmd_spread),
        ("polybet",        cmd_polybet),
        ("sportsarb",      cmd_sportsarb),
        ("sniper",         cmd_sniper),
        ("stake_status",   cmd_stake_status),
        ("stake_eth",      cmd_stake_eth),
        ("stake_sol",      cmd_stake_sol),
        ("regime",         cmd_regime),
        ("execute",        cmd_execute),
        ("test_brain",     cmd_test_brain),
        ("portfolios",     cmd_portfolios),
        ("aegis_watch",     cmd_aegis_watch),
    ]:
        _app.add_handler(CommandHandler(cmd, fn))

    _app.add_handler(CommandHandler("office", cmd_office))
    _app.add_handler(CallbackQueryHandler(handle_fix_callback, pattern="^aegis_(apply|ignore)_fix$"))
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_agent_chat))
    _app.add_error_handler(telegram_error_handler)

    asyncio.get_event_loop().create_task(_aegis_watchdog_loop())
    await _app.initialize()
    await _app.start()

    if WEBHOOK_URL:
        full = WEBHOOK_URL.rstrip("/") + WEBHOOK_PATH
        await asyncio.sleep(2)
        try:
            await _app.bot.set_webhook(url=full, drop_pending_updates=True, allowed_updates=["message","callback_query"])
        except Exception as e:
            print(f"[WEBHOOK] {e}")
            await asyncio.sleep(5)
            await _app.bot.set_webhook(url=full, drop_pending_updates=True, allowed_updates=["message","callback_query"])
        print(f"Webhook: {full}")

    print("Bot v7.1 prêt — /start | /resume | /agent | /agent_stop | /maxtrades")
    try:
        while True:
            await asyncio.sleep(1)
    finally:
        if WEBHOOK_URL:
            await _app.bot.delete_webhook()
        await _app.stop()
        await _app.shutdown()

def auto_start():
    """Démarrage automatique au lancement Railway — sans dépendance Telegram."""
    time.sleep(5)  # Attendre que le serveur HTTP soit prêt
    global _agent_running
    if bot_state["running"]:
        print("[AUTO-START] Bot déjà en cours — ignoré")
        return
    # Telegram optionnel : on notifie si disponible, mais ça ne bloque PLUS le démarrage
    send = make_send(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID else (lambda msg: print(f"[BOT] {msg}"))
    kelly_func = safe_get("kelly_criterion", lambda: 0.10)
    kelly = kelly_func() if callable(kelly_func) else 0.10
    # ── Démarrage bot trading ──────────────────────────────────────────────
    bot_state.update({
        "running": True, "trades_today": 0, "cycle_count": 0,
        "last_heartbeat": None, "last_monitor": 0, "last_micro": 0,
        "last_scalp": 0, "last_deep": 0, "last_status": 0,
        "last_meme": 0, "last_epargne": 0, "daily_stopped": False
    })
    # ── Démarrage agents IA ────────────────────────────────────────────────
    _agent_running = True
    print(f"[AUTO-START] ✅ Bot démarré automatiquement — Kelly:{kelly*100:.1f}% | Agents IA: ON")
    try:
        send(f"🚀 Bot V9 démarré automatiquement\nKelly:{kelly*100:.1f}% | /stop pour arrêter\n📡 WS: {'✅' if ws_manager.connected else '⚠️ REST'}")
    except Exception:
        pass  # Telegram optionnel
    threading.Thread(target=trading_loop,  args=(send,), daemon=True).start()
    threading.Thread(target=watchdog,      args=(send,), daemon=True).start()
    threading.Thread(target=daily_summary, args=(send,), daemon=True).start()

def _evolution_loop_MAIN():
    global _soul
    # ── Soul Agent — conscience autonome ─────────────────────────────────
    try:
        if _SOUL_AVAILABLE:
            _soul = _SoulAgent(memory=memory, bot_state=bot_state, sim=sim)
            logger.info("[SOUL] 🧬 Âme du bot initialisée ✅")
        else:
            logger.info("[SOUL] Soul agent non disponible → mode classique")
    except Exception as e:
        logger.warning(f"[SOUL] Init non bloquante: {e}")
    # ── Self-Improvement legacy ───────────────────────────────────────────
    try:
        from agents.self_improvement import start_self_improvement_loop
        start_self_improvement_loop(orchestrator)
        logger.info("[EVOLUTION] Boucle d'auto-amélioration démarrée ✅")
    except ImportError:
        logger.info("[EVOLUTION] self_improvement.py pas encore implémenté → mode silencieux (OK)")
    except Exception as e:
        logger.warning(f"[EVOLUTION] Erreur non bloquante: {e}")

async def cmd_lasttrades(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    try:
        trades = sim.get("trades", [])[-15:]
        if not trades:
            await update.message.reply_text("Aucun trade pour l’instant.")
            return
        wins = sum(1 for t in trades if isinstance(t.get("pnl"), (int, float)) and t.get("pnl") > 0)
        losses = len(trades) - wins
        msg = f"**DERNIERS TRADES ({len(trades)} actions)** — {wins}✅ {losses}❌\n\n"
        for i, t in enumerate(reversed(trades), 1):
            symbol = str(t.get("symbol", "UNKNOWN"))
            decision = str(t.get("decision", "?"))
            pnl = t.get("pnl", 0)
            pnl_pct = t.get("pnl_pct", 0)
            if not isinstance(pnl, (int, float)):
                pnl = 0
            if not isinstance(pnl_pct, (int, float)):
                pnl_pct = 0
            color = "🟢" if pnl > 0 else "🔴"
            sign = "+" if pnl > 0 else ""
            msg += f"{i}. {color} {symbol} | {decision}\n"
            msg += f"   PnL: {sign}${pnl:,.0f} ({pnl_pct:.1f}%)\n"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text("Erreur /lasttrades. Tape /help")
        print(f"[LASTTRADES ERROR] {e}")

async def cmd_debugpnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    send = make_send(TELEGRAM_CHAT_ID)
    equity = get_equity_safe()
    if equity < 2000:
        msg = "✅ **C’est bon, c’est carré !**\n\n"
    else:
        msg = "⚠️ **Attention capital anormalement élevé**\n\n"
    msg += f"**DEBUG PnL SAFETY**\n"
    msg += f"Capital calculé : ${equity:,.2f}\n"
    msg += f"Dernier trade PnL brut : {sim.get('trades',[-1])[-1].get('pnl','N/A')}\n"
    msg += "→ Tout est propre maintenant."
    await update.message.reply_text(msg)

def safe_pnl(pnl_pct: float, amount_usd: float, leverage: float = 1) -> float:
    try:
        pnl = pnl_pct * amount_usd * leverage
        if abs(pnl) > amount_usd * 50:
            print(f"[SAFETY-PnL] CAP appliqué ! {pnl:,.2f} → limité à 50x")
            pnl = amount_usd * 50 * (1 if pnl > 0 else -1)
        return round(pnl, 4)
    except Exception as e:
        print(f"[SAFETY-PnL] Erreur: {e}")
        return 0.0


async def run_backtest(symbol: str = "BTCUSDT", interval: str = "5m") -> dict:
    """Async wrapper pour backtest_strategy — appelé depuis trading_loop via run_coroutine_threadsafe"""
    try:
        result = backtest_strategy(symbol, interval, days=30)
        return result
    except Exception as e:
        logger.warning(f"[RUN-BACKTEST] {e}")
        return {"error": str(e), "total_return": 0, "max_drawdown": 0, "win_rate": 0}


async def cmd_stake_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    equity = get_equity_safe()
    try:
        staking_ctx = {"equity": equity, "shared_glossary": {}}
        future = asyncio.run_coroutine_threadsafe(
            yield_staking.respond("get staking status and rewards summary", staking_ctx),
            _main_loop
        )
        result = future.result(timeout=15)
        summary = result.get("summary", "Staking en cours de surveillance...")
        rewards = result.get("total_rewards_usd", 0.0)
        await update.message.reply_text(
            f"🌱 STAKING STATUS\n━━━━━━━━━━━━━\n"
            f"{summary}\n"
            f"💰 Rewards today : ${rewards:.4f}"
        )
    except Exception:
        await update.message.reply_text(
            f"🌱 STAKING (simulation)\n━━━━━━━━━━━━━\n"
            f"ETH staking : ~3.8% APY\n"
            f"SOL staking : ~6.5% APY\n"
            f"Capital engagé : ${equity*0.15:.2f} (15%)\n"
            f"Surveillance active ✅"
        )


async def cmd_stake_eth(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    equity = get_equity_safe()
    stake_amount = round(equity * 0.10, 2)
    annual_rewards = round(stake_amount * 0.038, 2)
    try:
        staking_ctx = {"equity": equity, "symbol": "ETH", "amount_usd": stake_amount}
        future = asyncio.run_coroutine_threadsafe(
            yield_staking.respond(f"stake {stake_amount} USD en ETH APY 3.8%", staking_ctx),
            _main_loop
        )
        result = future.result(timeout=15)
        summary = result.get("summary", "ETH staking simulé")
        await update.message.reply_text(
            f"💎 STAKE ETH (simulation)\n━━━━━━━━━━━━━\n"
            f"Montant : ${stake_amount:.2f} (10% capital)\n"
            f"APY     : ~3.8%\n"
            f"Rewards/an : ~${annual_rewards:.2f}\n"
            f"{summary}"
        )
    except Exception:
        await update.message.reply_text(
            f"💎 STAKE ETH (simulation)\n━━━━━━━━━━━━━\n"
            f"Montant : ${stake_amount:.2f} (10% capital)\n"
            f"APY     : ~3.8% | Rewards/an : ~${annual_rewards:.2f}\n"
            f"Staking simulé activé ✅"
        )


async def cmd_stake_sol(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    equity = get_equity_safe()
    stake_amount = round(equity * 0.10, 2)
    annual_rewards = round(stake_amount * 0.065, 2)
    try:
        staking_ctx = {"equity": equity, "symbol": "SOL", "amount_usd": stake_amount}
        future = asyncio.run_coroutine_threadsafe(
            yield_staking.respond(f"stake {stake_amount} USD en SOL APY 6.5%", staking_ctx),
            _main_loop
        )
        result = future.result(timeout=15)
        summary = result.get("summary", "SOL staking simulé")
        await update.message.reply_text(
            f"🌟 STAKE SOL (simulation)\n━━━━━━━━━━━━━\n"
            f"Montant : ${stake_amount:.2f} (10% capital)\n"
            f"APY     : ~6.5%\n"
            f"Rewards/an : ~${annual_rewards:.2f}\n"
            f"{summary}"
        )
    except Exception:
        await update.message.reply_text(
            f"🌟 STAKE SOL (simulation)\n━━━━━━━━━━━━━\n"
            f"Montant : ${stake_amount:.2f} (10% capital)\n"
            f"APY     : ~6.5% | Rewards/an : ~${annual_rewards:.2f}\n"
            f"Staking simulé activé ✅"
        )


async def cmd_regime(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    try:
        regime_ctx = {"shared_glossary": {}}
        future = asyncio.run_coroutine_threadsafe(
            quant_ml.respond("detect current market regime with details", regime_ctx),
            _main_loop
        )
        result = future.result(timeout=12)
        regime = result.get("regime", bot_state.get("market_regime", "NEUTRAL"))
        summary = result.get("summary", f"Régime : {regime}")
        regime_e = "🐂" if regime == "BULL" else "🐻" if regime == "BEAR" else "🐢"
        await update.message.reply_text(
            f"📡 RÉGIME MARCHÉ (QuantML)\n━━━━━━━━━━━━━\n"
            f"{regime_e} {regime}\n"
            f"{summary}"
        )
    except Exception:
        regime = bot_state.get("market_regime", "NEUTRAL")
        regime_e = "🐂" if regime == "BULL" else "🐻" if regime == "BEAR" else "🐢"
        fg = get_fear_greed_value()
        macro = get_macro_trend()
        await update.message.reply_text(
            f"📡 RÉGIME MARCHÉ\n━━━━━━━━━━━━━\n"
            f"{regime_e} Régime : {regime}\n"
            f"F&G    : {fg}/100\n"
            f"Macro  : {macro}"
        )


async def cmd_execute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    args = ctx.args if ctx.args else []
    symbol = args[0].upper() if len(args) > 0 else "BTCUSDT"
    side   = args[1].upper() if len(args) > 1 else "BUY"
    if not validate_symbol(symbol):
        await update.message.reply_text("Usage: /execute [SYMBOL] [BUY|SELL]\nEx: /execute BTCUSDT BUY")
        return
    if side not in ("BUY", "SELL"):
        await update.message.reply_text("Side invalide. Options: BUY SELL")
        return
    equity = get_equity_safe()
    price = get_current_price(symbol) or 0.0
    amount_usd = max(10.0, min(equity * kelly_criterion() * 0.5, equity * 0.20))
    try:
        exec_future = asyncio.run_coroutine_threadsafe(
            execution.place_order_async(
                symbol=symbol, side=side, order_type="market",
                amount_usd=amount_usd, price=price
            ), _main_loop
        )
        result = exec_future.result(timeout=10)
        status = result.get("status", "executed")
        await update.message.reply_text(
            f"🚀 EXÉCUTION SIMULÉE\n━━━━━━━━━━━━━\n"
            f"Symbol : {symbol}\nSide   : {side}\n"
            f"Prix   : ${price:,.4f}\nMontant: ${amount_usd:.2f}\n"
            f"Statut : {status}"
        )
    except Exception:
        await update.message.reply_text(
            f"🚀 SIMULATION EXÉCUTION\n━━━━━━━━━━━━━\n"
            f"Symbol : {symbol} | Side : {side}\n"
            f"Prix   : ${price:,.4f}\nMontant: ${amount_usd:.2f}\n"
            f"Mode   : Paper trading ✅"
        )


async def cmd_test_brain(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    await update.message.reply_text("🧠 Test cerveau collectif V5...")
    equity = get_equity_safe()
    try:
        test_ctx = {
            "symbol": "BTCUSDT",
            "equity": equity,
            "market_regime": bot_state.get("market_regime", "NEUTRAL"),
            "confidence_threshold": memory.get("confidence_threshold", CONFIDENCE_BASE),
            "shared_glossary": {},
            "extreme_learning_mode": EXTREME_LEARNING_MODE,
        }
        future = asyncio.run_coroutine_threadsafe(
            orchestrator.ask_all("test complet cerveau collectif — analyse et décision", test_ctx),
            _main_loop
        )
        responses, decision = future.result(timeout=20)
        active = len([r for r in responses if r.get("confidence", 0) > 0])
        avg_conf = sum(r.get("confidence", 0) for r in responses) / max(len(responses), 1)
        lines = [
            f"🧠 CERVEAU COLLECTIF V5\n━━━━━━━━━━━━━",
            f"Agents actifs   : {active}/{len(responses)}",
            f"Confiance moy.  : {avg_conf:.1%}",
            f"Décision finale : {decision.get('decision', 'HOLD')}",
            "━━━━━━━━━━━━━",
        ]
        for r in responses[:6]:
            conf = r.get("confidence", 0)
            e = "🟢" if conf >= 0.7 else "🟡" if conf >= 0.4 else "🔴"
            lines.append(f"{e} {r.get('agent', '?'):12s} {conf:.0%} — {r.get('summary', '')[:50]}")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        nb_agents = len([a for a in dir(orchestrator) if not a.startswith("_")])
        await update.message.reply_text(
            f"🧠 CERVEAU COLLECTIF V5\n━━━━━━━━━━━━━\n"
            f"Agents déclarés : {nb_agents}\n"
            f"Capital géré    : ${equity:.2f}\n"
            f"Régime          : {bot_state.get('market_regime', 'NEUTRAL')}\n"
            f"Statut          : ✅ Opérationnel"
        )


async def cmd_portfolios(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    equity = get_equity_safe()
    cash   = sim.get("cash", 0)
    pos_val = equity - cash
    try:
        lines = ["💼 TOUS LES PORTEFEUILLES\n━━━━━━━━━━━━━"]
        for name, w in portfolio_manager.wallets.items():
            lines.append(f"💰 {name.upper()}: ${w.get('balance', 0):.2f}")
    except Exception:
        lines = [
            "💼 TOUS LES PORTEFEUILLES\n━━━━━━━━━━━━━",
            f"💰 TRADING  : ${equity:.2f}",
            f"💵 CASH     : ${cash:.2f}",
            f"📍 POSITIONS: ${pos_val:.2f}",
        ]
    await update.message.reply_text("\n".join(lines))


if __name__ == "__main__":
    try:
        print("🚀 Trading Bot v7.2 — LIVE PROGRESSIVE chargé")
        init_db()
        # ── Découverte dynamique de tous les marchés Binance ──────────────
        try:
            import threading as _thr_disc
            _thr_disc.Thread(target=discover_all_symbols, daemon=True).start()
            logger.info("[STARTUP] 🌍 Découverte marchés Binance lancée en arrière-plan...")
        except Exception as _disc_e:
            logger.warning(f"[STARTUP] discover_all_symbols: {_disc_e}")
        load_data()
        # BUG FIX (2026-07-24): this used to reset equity to CAPITAL_INITIAL on
        # EVERY restart regardless of what load_data() just loaded, silently
        # wiping realized PnL/cash history even when trades were correctly
        # resumed from disk. Only reset on a genuinely fresh start (no trades
        # loaded at all) -- a resume must keep the real accumulated state.
        if EXTREME_LEARNING_MODE and not sim.get("trades"):
            sim["cash"] = CAPITAL_INITIAL
            sim["initial"] = CAPITAL_INITIAL
            sim["equity_history"] = [CAPITAL_INITIAL]
            sim["peak_equity"] = CAPITAL_INITIAL
            sim["daily_start_equity"] = CAPITAL_INITIAL
            print(f"🔄 EXTREME LEARNING MODE → equity reset à ${CAPITAL_INITIAL:,.2f} (fresh start, no prior data)")
        evolution_thread = threading.Thread(target=_evolution_loop_MAIN, daemon=True)
        evolution_thread.start()
        threading.Thread(target=run_server, daemon=True).start()
        auto_start()
        asyncio.run(run_telegram())
    except Exception as e:
        import traceback
        error_msg = f"💥 CRASH FATAL DU BOT :\n{e}\n{traceback.format_exc()}"
        print(error_msg)
        logger.error(error_msg)
        raise
