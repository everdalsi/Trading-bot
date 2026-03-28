from typing import Dict, Any, List
import sqlite3
import redis
import pickle
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class Memory:
    def __init__(self):
        # === Code original ===
        self.data: Dict[str, Any] = {
            "lessons": [],
            "trades": [],
            "symbol_scores": {},
            "symbol_blacklist": {},
            "consecutive_losses": {},
            "total_wins": 0,
            "total_losses": 0,
            "confidence_threshold": 65
        }

        # === UPGRADE PHASE 1 : SQLite + Redis ===
        self.conn = sqlite3.connect("trading_memory.db", check_same_thread=False)
        self._init_db()
        try:
            self.redis = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
            self.redis.ping()
            self.use_redis = True
        except Exception as e:
            self.use_redis = False
            logger.warning(f"Redis non disponible → fallback SQLite only. Error: {e}")

    # --- MÉTHODES DE COMPATIBILITÉ DICTIONNAIRE (OBLIGATOIRE POUR BOT.PY) ---
    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def setdefault(self, key: str, default=None):
        return self.data.setdefault(key, default)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def __contains__(self, key):
        return key in self.data

    def items(self):
        return self.data.items()

    def update(self, other_dict):
        self.data.update(other_dict)

    # --- TES FONCTIONS ORIGINALES ---
    def _init_symbol(self, symbol: str):
        if symbol not in self.data:
            self.data[symbol] = {
                "trades": [],
                "wins": 0,
                "losses": 0,
                "mistakes": [],
                "total_confidence": 0.0
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
            self.data[symbol]["wins"] += 1
        elif result == "loss":
            self.data[symbol]["losses"] += 1

    def update_trade_result(self, symbol: str, index: int, result: str):
        self._init_symbol(symbol)
        try:
            trade = self.data[symbol]["trades"][index]
            trade["result"] = result
            if result == "win":
                self.data[symbol]["wins"] += 1
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
        resolved = data["wins"] + data["losses"]
        winrate = data["wins"] / resolved if resolved > 0 else 0.0
        avg_confidence = (
            data["total_confidence"] / len(data["trades"])
            if len(data["trades"]) > 0 else 0.0
        )
        return {
            "symbol": symbol,
            "total_trades": len(data["trades"]),
            "resolved_trades": resolved,
            "winrate": round(winrate, 4),
            "losses": data["losses"],
            "avg_confidence": round(avg_confidence, 4)
        }

    def is_bad_symbol(self, symbol: str) -> bool:
        stats = self.stats(symbol)
        return stats["resolved_trades"] > 10 and stats["winrate"] < 0.40

    def get_symbol_score(self, symbol: str) -> float:
        stats = self.stats(symbol)
        score = stats["winrate"] * 0.7 + stats["avg_confidence"] * 0.3
        return round(score, 2)

    def get_global_stats(self) -> dict:
        total_wins = 0
        total_losses = 0
        for key, val in self.data.items():
            if isinstance(val, dict) and "wins" in val:
                total_wins += val["wins"]
                total_losses += val["losses"]
        
        total = total_wins + total_losses
        winrate = total_wins / total if total > 0 else 0.0
        return {
            "total_trades": total,
            "wins": total_wins,
            "losses": total_losses,
            "winrate": round(winrate, 2)
        }

    def update_trade_results(self, current_price: float):
        for symbol in list(self.data.keys()):
            if not isinstance(self.data[symbol], dict) or "trades" not in self.data[symbol]:
                continue
            for i, trade in enumerate(self.data[symbol]["trades"]):
                if trade.get("result") == "pending":
                    entry = trade.get("price_in") or trade.get("entry_price")
                    if entry:
                        pnl_pct = (current_price - entry) / entry if trade.get("decision") == "BUY" else (entry - current_price) / entry
                        trade["pnl_pct"] = round(pnl_pct * 100, 2)
                        trade["result"] = "win" if pnl_pct > 0 else "loss"
                        self.update_trade_result(symbol, i, trade["result"])

    def log_trade(self, trade_data: dict):
        self.add_trade(trade_data)

    # =================================================================
    # === UPGRADE PHASE 1 : Méthodes SQLite + Redis ===================
    # =================================================================
    def _init_db(self):
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                symbol TEXT,
                action TEXT,
                outcome TEXT,
                pnl REAL,
                confidence REAL,
                lesson_text TEXT
            )
        ''')
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                side TEXT,
                amount REAL,
                entry_price REAL,
                timestamp TEXT
            )
        ''')
        self.conn.commit()

    def save_lesson(self, symbol: str, action: str, outcome: str, pnl: float, confidence: float, lesson: str):
        self.conn.execute(
            "INSERT INTO lessons (timestamp, symbol, action, outcome, pnl, confidence, lesson_text) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (datetime.utcnow().isoformat(), symbol, action, outcome, pnl, confidence, lesson)
        )
        self.conn.commit()

    def get_recent_lessons(self, limit: int = 50) -> List[Dict]:
        cursor = self.conn.execute("SELECT * FROM lessons ORDER BY timestamp DESC LIMIT ?", (limit,))
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def save_position(self, symbol: str, side: str, amount: float, entry_price: float):
        self.conn.execute(
            "REPLACE INTO positions (symbol, side, amount, entry_price, timestamp) VALUES (?, ?, ?, ?, ?)",
            (symbol, side, amount, entry_price, datetime.utcnow().isoformat())
        )
        self.conn.commit()

    def get_positions(self) -> Dict:
        cursor = self.conn.execute("SELECT * FROM positions")
        cols = ['symbol', 'side', 'amount', 'entry_price', 'timestamp']
        return {row[0]: dict(zip(cols, row)) for row in cursor.fetchall()}

    def cache_set(self, key: str, value: Any, expire: int = 300):
        if hasattr(self, 'use_redis') and self.use_redis:
            self.redis.setex(key, expire, pickle.dumps(value))

    def cache_get(self, key: str) -> Any:
        if hasattr(self, 'use_redis') and self.use_redis:
            data = self.redis.get(key)
            return pickle.loads(data) if data else None
        return None
