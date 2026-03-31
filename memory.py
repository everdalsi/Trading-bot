"""
🧠 MEMORY V2 — Mémoire hybride SQLite + Redis (fallback automatique)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX V2 :
- Redis : decode_responses=False (incompatible avec pickle si True)
- cache_set / cache_get : utilise json.dumps/loads au lieu de pickle
  pour être compatible avec decode_responses=True si souhaité
- Ajout de REDIS_HOST fallback : "localhost" si non défini (Docker: "redis")
- Correction minor : get_global_stats cohérent avec Memory.data structure
"""

from typing import Dict, Any, List
import sqlite3
import json
import os
import threading
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Import redis avec fallback propre
try:
    import redis as redis_lib
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("⚠️ [MEMORY] Module redis non installé → SQLite only")


class Memory:

    def __init__(self):
        # === Thread safety — RLock (réentrant, supporte les appels imbriqués) ===
        self._lock = threading.RLock()

        # === Dictionnaire de compatibilité (requis par bot.py) ===
        self.data: Dict[str, Any] = {
            "lessons":             [],
            "trades":              [],
            "symbol_scores":       {},
            "symbol_blacklist":    {},
            "consecutive_losses":  {},
            "total_wins":          0,
            "total_losses":        0,
            "confidence_threshold": 65,
        }

        # === SQLite — check_same_thread=False + RLock pour la sécurité thread ===
        self.conn = sqlite3.connect("trading_memory.db", check_same_thread=False)
        self._init_db()

        # === Redis (optionnel, fallback SQLite si indisponible) ===
        self.use_redis = False
        self.redis = None

        if REDIS_AVAILABLE:
            # FIX V2 : host dynamique, fallback localhost si REDIS_HOST non défini
            redis_host = os.getenv("REDIS_HOST", "localhost")
            try:
                # FIX V2 : decode_responses=False pour compatibilité avec json.dumps
                self.redis = redis_lib.Redis(
                    host=redis_host,
                    port=6379,
                    db=0,
                    decode_responses=False,   # ← FIX : False pour éviter crash pickle/json
                    socket_connect_timeout=3,
                    socket_timeout=3,
                )
                self.redis.ping()
                self.use_redis = True
                logger.info(f"✅ [MEMORY] Redis connecté (host={redis_host})")
            except Exception as e:
                self.use_redis = False
                logger.warning(
                    f"[MEMORY] Redis non disponible (host={redis_host}) "
                    f"→ fallback SQLite only. ({e})"
                )

    # ── Compatibilité dictionnaire (requis par bot.py) ────────────────────────

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def setdefault(self, key: str, default=None):
        return self.data.setdefault(key, default)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        with self._lock:
            self.data[key] = value

    def __contains__(self, key):
        return key in self.data

    def items(self):
        with self._lock:
            return list(self.data.items())

    def update(self, other_dict):
        with self._lock:
            self.data.update(other_dict)

    # ── Gestion des symboles ──────────────────────────────────────────────────

    def _init_symbol(self, symbol: str):
        if symbol not in self.data:
            self.data[symbol] = {
                "trades":           [],
                "wins":             0,
                "losses":           0,
                "mistakes":         [],
                "total_confidence": 0.0,
            }

    def add_trade(self, trade: dict):
        symbol = trade.get("symbol")
        if not symbol:
            return
        self._init_symbol(symbol)
        self.data[symbol]["trades"].append(trade)
        self.data[symbol]["total_confidence"] += trade.get("confidence", 0.0)
        result = trade.get("result")
        if result == "win":
            self.data[symbol]["wins"]   += 1
        elif result == "loss":
            self.data[symbol]["losses"] += 1

    def update_trade_result(self, symbol: str, index: int, result: str):
        self._init_symbol(symbol)
        try:
            trade = self.data[symbol]["trades"][index]
            trade["result"] = result
            if result == "win":
                self.data[symbol]["wins"]   += 1
            elif result == "loss":
                self.data[symbol]["losses"] += 1
        except (IndexError, KeyError):
            pass

    def add_mistake(self, symbol: str, mistake: str):
        self._init_symbol(symbol)
        self.data[symbol]["mistakes"].append(mistake)

    def stats(self, symbol: str) -> dict:
        self._init_symbol(symbol)
        data = self.data[symbol]
        resolved   = data["wins"] + data["losses"]
        winrate    = data["wins"] / resolved if resolved > 0 else 0.0
        avg_conf   = (
            data["total_confidence"] / len(data["trades"])
            if len(data["trades"]) > 0 else 0.0
        )
        return {
            "symbol":         symbol,
            "total_trades":   len(data["trades"]),
            "resolved_trades": resolved,
            "winrate":        round(winrate, 4),
            "losses":         data["losses"],
            "avg_confidence": round(avg_conf, 4),
        }

    def is_bad_symbol(self, symbol: str) -> bool:
        s = self.stats(symbol)
        return s["resolved_trades"] > 10 and s["winrate"] < 0.40

    def get_symbol_score(self, symbol: str) -> float:
        s = self.stats(symbol)
        return round(s["winrate"] * 0.7 + s["avg_confidence"] * 0.3, 2)

    def get_global_stats(self) -> dict:
        total_wins   = 0
        total_losses = 0
        for key, val in self.data.items():
            if isinstance(val, dict) and "wins" in val:
                total_wins   += val["wins"]
                total_losses += val["losses"]
        total   = total_wins + total_losses
        winrate = total_wins / total if total > 0 else 0.0
        return {
            "total_trades": total,
            "wins":         total_wins,
            "losses":       total_losses,
            "winrate":      round(winrate, 2),
        }

    def update_trade_results(self, current_price: float):
        for symbol in list(self.data.keys()):
            if not isinstance(self.data[symbol], dict) or "trades" not in self.data[symbol]:
                continue
            for i, trade in enumerate(self.data[symbol]["trades"]):
                if trade.get("result") == "pending":
                    entry = trade.get("price_in") or trade.get("entry_price")
                    if entry:
                        pnl_pct = (
                            (current_price - entry) / entry
                            if trade.get("decision") == "BUY"
                            else (entry - current_price) / entry
                        )
                        trade["pnl_pct"] = round(pnl_pct * 100, 2)
                        trade["result"]  = "win" if pnl_pct > 0 else "loss"
                        self.update_trade_result(symbol, i, trade["result"])

    def log_trade(self, trade_data: dict):
        self.add_trade(trade_data)

    # ── SQLite ────────────────────────────────────────────────────────────────

    def _init_db(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS lessons (
                id          INTEGER PRIMARY KEY,
                timestamp   TEXT,
                symbol      TEXT,
                action      TEXT,
                outcome     TEXT,
                pnl         REAL,
                confidence  REAL,
                lesson_text TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                symbol      TEXT PRIMARY KEY,
                side        TEXT,
                amount      REAL,
                entry_price REAL,
                timestamp   TEXT
            )
        """)
        self.conn.commit()

    def save_lesson(
        self,
        symbol: str,
        action: str,
        outcome: str,
        pnl: float,
        confidence: float,
        lesson: str,
    ):
        with self._lock:
            try:
                self.conn.execute(
                    """INSERT INTO lessons
                       (timestamp, symbol, action, outcome, pnl, confidence, lesson_text)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (datetime.utcnow().isoformat(), symbol, action, outcome, pnl, confidence, lesson),
                )
                self.conn.commit()
            except Exception as e:
                logger.warning(f"[MEMORY] save_lesson error: {e}")

    def get_recent_lessons(self, limit: int = 50) -> List[Dict]:
        with self._lock:
            try:
                cursor = self.conn.execute(
                    "SELECT * FROM lessons ORDER BY timestamp DESC LIMIT ?", (limit,)
                )
                cols = [col[0] for col in cursor.description]
                return [dict(zip(cols, row)) for row in cursor.fetchall()]
            except Exception as e:
                logger.warning(f"[MEMORY] get_recent_lessons error: {e}")
                return []

    def save_position(
        self, symbol: str, side: str, amount: float, entry_price: float
    ):
        with self._lock:
            try:
                self.conn.execute(
                    """REPLACE INTO positions (symbol, side, amount, entry_price, timestamp)
                       VALUES (?, ?, ?, ?, ?)""",
                    (symbol, side, amount, entry_price, datetime.utcnow().isoformat()),
                )
                self.conn.commit()
            except Exception as e:
                logger.warning(f"[MEMORY] save_position error: {e}")

    def get_positions(self) -> Dict:
        with self._lock:
            try:
                cursor = self.conn.execute("SELECT * FROM positions")
                cols   = ["symbol", "side", "amount", "entry_price", "timestamp"]
                return {row[0]: dict(zip(cols, row)) for row in cursor.fetchall()}
            except Exception as e:
                logger.warning(f"[MEMORY] get_positions error: {e}")
                return {}

    # ── Redis cache ───────────────────────────────────────────────────────────

    def cache_set(self, key: str, value: Any, expire: int = 300):
        """
        FIX V2 : utilise json.dumps au lieu de pickle.
        Compatible avec decode_responses=False (bytes stockés/récupérés).
        """
        if self.use_redis and self.redis:
            try:
                serialized = json.dumps(value).encode("utf-8")
                self.redis.setex(key, expire, serialized)
            except Exception as e:
                logger.debug(f"[MEMORY] cache_set error: {e}")

    def cache_get(self, key: str) -> Any:
        """
        FIX V2 : utilise json.loads au lieu de pickle.loads.
        """
        if self.use_redis and self.redis:
            try:
                data = self.redis.get(key)
                if data:
                    return json.loads(data.decode("utf-8"))
            except Exception as e:
                logger.debug(f"[MEMORY] cache_get error: {e}")
        return None

    def cache_delete(self, key: str):
        if self.use_redis and self.redis:
            try:
                self.redis.delete(key)
            except Exception:
                pass
