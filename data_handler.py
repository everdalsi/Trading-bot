import requests
import time
from collections import deque
from datetime import datetime
from logging_config import logger

BINANCE_BASE = "https://api.binance.com"

class DataHandler:
    def __init__(self):
        self.price_cache = {}
        self.kline_cache_1m = {}
        self.kline_cache_5m = {}
        self.cache_ttl = 30  # secondes

    def get_current_price(self, symbol: str) -> float | None:
        """Prix actuel avec cache court"""
        now = time.time()
        if symbol in self.price_cache and now - self.price_cache[symbol]["ts"] < self.cache_ttl:
            return self.price_cache[symbol]["price"]

        try:
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/ticker/price",
                params={"symbol": symbol},
                timeout=5
            )
            if r.status_code == 200:
                price = float(r.json()["price"])
                self.price_cache[symbol] = {"price": price, "ts": now}
                return price
        except Exception as e:
            logger.error(f"[DATA] Erreur prix {symbol}: {e}")
        return None

    def prefill_caches(self, symbols):
        """Pré-remplissage au démarrage (utilisé par ws_manager)"""
        logger.info(f"[DATA] Pré-remplissage caches pour {len(symbols)} symboles")
        for sym in symbols[:8]:
            sym = sym.upper()
            try:
                r = requests.get(
                    f"{BINANCE_BASE}/api/v3/klines",
                    params={"symbol": sym, "interval": "1m", "limit": 60},
                    timeout=8
                )
                if r.status_code == 200:
                    closes = [float(c[4]) for c in r.json()]
                    self.kline_cache_1m[sym] = deque(closes, maxlen=60)
            except Exception:
                pass
            time.sleep(0.3)
        logger.info("[DATA] Pré-remplissage terminé ✅")

    def get_klines(self, symbol: str, interval: str = "1m", limit: int = 100):
        """Récupère les klines (priorité WS via ws_manager, fallback REST)"""
        # On garde simple pour l’instant, le WS est déjà géré par ws_manager
        return ws_manager.get_klines(symbol, interval) if hasattr(ws_manager, 'get_klines') else []


# Instance globale utilisée partout
data_handler = DataHandler()
