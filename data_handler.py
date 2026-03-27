import requests
import time
import pandas as pd
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
        cached = self.price_cache.get(symbol)
        if cached and now - cached["ts"] < self.cache_ttl:
            return cached["price"]
        try:
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/ticker/price",
                params={"symbol": symbol.upper()},
                timeout=5
            )
            if r.status_code == 200:
                price = float(r.json()["price"])
                self.price_cache[symbol.upper()] = {"price": price, "ts": now}
                return price
        except Exception as e:
            logger.error(f"[DATA] Erreur prix {symbol}: {e}")
        return None

    def get_prices_batch(self, symbols: list = None) -> dict:
        """
        Récupère tous les prix en une seule requête Binance (bookTicker).
        Retourne un dict {SYMBOL: float}.
        """
        now = time.time()
        result = {}

        # D'abord on remplit depuis le cache existant
        for sym, cached in self.price_cache.items():
            if now - cached["ts"] < self.cache_ttl:
                result[sym] = cached["price"]

        # Puis on rafraîchit via l'endpoint batch Binance
        try:
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/ticker/price",
                timeout=8
            )
            if r.status_code == 200:
                for item in r.json():
                    sym = item["symbol"]
                    # Filtre optionnel sur la liste demandée
                    if symbols and sym not in [s.upper() for s in symbols]:
                        continue
                    try:
                        price = float(item["price"])
                        if price > 0:
                            result[sym] = price
                            self.price_cache[sym] = {"price": price, "ts": now}
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"[DATA] Erreur batch prices: {e}")

        return result

    def prefill_caches(self, symbols):
        """Pré-remplissage au démarrage"""
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

    def get_klines(self, symbol: str, interval: str = "1m", limit: int = 100) -> pd.Series:
        """
        Retourne les klines sous forme de pd.Series (fermetures).
        Priorité : cache WS → cache local → REST Binance.
        """
        symbol = symbol.upper()

        # 1. Essai via ws_manager (WebSocket en mémoire)
        try:
            from websocket_manager import ws_manager
            ws_data = ws_manager.get_klines(symbol, interval)
            if ws_data and len(ws_data) >= 14:
                return pd.Series(list(ws_data), dtype=float)
        except Exception:
            pass

        # 2. Cache local 1m / 5m
        if interval == "1m" and symbol in self.kline_cache_1m:
            cached = list(self.kline_cache_1m[symbol])
            if len(cached) >= 14:
                return pd.Series(cached, dtype=float)
        if interval in ("5m", "5") and symbol in self.kline_cache_5m:
            cached = list(self.kline_cache_5m[symbol])
            if len(cached) >= 14:
                return pd.Series(cached, dtype=float)

        # 3. Fallback REST Binance
        interval_map = {
            "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
            "60": "1h", "120": "2h", "240": "4h", "D": "1d", "1D": "1d"
        }
        binance_interval = interval_map.get(interval, interval)
        try:
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/klines",
                params={"symbol": symbol, "interval": binance_interval, "limit": limit},
                timeout=10
            )
            if r.status_code == 200:
                closes = [float(c[4]) for c in r.json()]
                # Mise en cache local
                if binance_interval == "1m":
                    self.kline_cache_1m[symbol] = deque(closes, maxlen=60)
                elif binance_interval == "5m":
                    self.kline_cache_5m[symbol] = deque(closes, maxlen=120)
                return pd.Series(closes, dtype=float)
        except Exception as e:
            logger.error(f"[DATA] Erreur klines {symbol} {interval}: {e}")

        return pd.Series(dtype=float)

    def get_klines_1m_cached(self, symbol: str) -> pd.Series:
        return self.get_klines(symbol, "1m", 60)

    def get_klines_5m_cached(self, symbol: str) -> pd.Series:
        return self.get_klines(symbol, "5m", 120)

    def get_volume_data(self, symbol: str, interval: str, limit: int) -> list:
        """Retourne les volumes (fallback neutres si indisponible)"""
        try:
            interval_map = {
                "1": "1m", "5": "5m", "15": "15m", "60": "1h"
            }
            binance_interval = interval_map.get(interval, interval)
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/klines",
                params={"symbol": symbol.upper(), "interval": binance_interval, "limit": limit},
                timeout=8
            )
            if r.status_code == 200:
                return [float(c[5]) for c in r.json()]
        except Exception:
            pass
        return [1.0] * limit


# Instance globale
data_handler = DataHandler()


# ── Fonctions globales utilisées dans bot.py ─────────────────────
def get_prices_batch() -> dict:
    """Proxy global vers data_handler.get_prices_batch()"""
    return data_handler.get_prices_batch()

def get_klines_1m_cached(symbol: str) -> pd.Series:
    return data_handler.get_klines_1m_cached(symbol)

def get_klines_5m_cached(symbol: str) -> pd.Series:
    return data_handler.get_klines_5m_cached(symbol)

def get_volume_data(symbol: str, interval: str, limit: int) -> list:
    return data_handler.get_volume_data(symbol, interval, limit)
