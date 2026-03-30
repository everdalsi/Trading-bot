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
        self.cache_ttl = 30

    def get_current_price(self, symbol: str) -> float | None:
        now = time.time()
        cached = self.price_cache.get(symbol.upper())
        if cached and now - cached["ts"] < self.cache_ttl:
            return cached["price"]

        try:
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/ticker/price",
                params={"symbol": symbol.upper()},
                timeout=5
            )
            r.raise_for_status()
            price = float(r.json()["price"])
            self.price_cache[symbol.upper()] = {"price": price, "ts": now}
            return price
        except Exception as e:
            logger.error(f"[DATA] Erreur prix {symbol}: {e}")
            return None

    def get_prices_batch(self, symbols: list = None) -> dict:
        now = time.time()
        result = {}

        for sym, cached in list(self.price_cache.items()):
            if now - cached["ts"] < self.cache_ttl:
                result[sym] = cached["price"]

        try:
            r = requests.get(f"{BINANCE_BASE}/api/v3/ticker/price", timeout=8)
            r.raise_for_status()
            for item in r.json():
                sym = item["symbol"]
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
        logger.info(f"[DATA] Pré-remplissage caches pour {len(symbols)} symboles")
        for sym in symbols[:8]:
            sym = sym.upper()
            try:
                r = requests.get(
                    f"{BINANCE_BASE}/api/v3/klines",
                    params={"symbol": sym, "interval": "1m", "limit": 60},
                    timeout=8
                )
                r.raise_for_status()
                closes = [float(c[4]) for c in r.json()]
                self.kline_cache_1m[sym] = deque(closes, maxlen=60)
            except Exception as e:
                logger.warning(f"[DATA] Impossible de pré-remplir {sym}: {e}")
            time.sleep(0.3)
        logger.info("[DATA] Pré-remplissage terminé ✅")

    def get_klines(self, symbol: str, interval: str = "1m", limit: int = 100) -> pd.Series:
        symbol = symbol.upper()

        try:
            from websocket_manager import ws_manager
            ws_data = ws_manager.get_klines(symbol, interval)
            if ws_data and len(ws_data) >= 14:
                return pd.Series(list(ws_data), dtype=float)
        except Exception:
            pass

        if interval == "1m" and symbol in self.kline_cache_1m:
            cached = list(self.kline_cache_1m[symbol])
            if len(cached) >= 14:
                return pd.Series(cached, dtype=float)
        if interval in ("5m", "5") and symbol in self.kline_cache_5m:
            cached = list(self.kline_cache_5m[symbol])
            if len(cached) >= 14:
                return pd.Series(cached, dtype=float)

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
            r.raise_for_status()
            closes = [float(c[4]) for c in r.json()]
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
        try:
            interval_map = {"1": "1m", "5": "5m", "15": "15m", "60": "1h"}
            binance_interval = interval_map.get(interval, interval)
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/klines",
                params={"symbol": symbol.upper(), "interval": binance_interval, "limit": limit},
                timeout=8
            )
            r.raise_for_status()
            return [float(c[5]) for c in r.json()]
        except Exception as e:
            logger.error(f"[DATA] Erreur volume {symbol}: {e}")
        return [1.0] * limit


# Instance globale
data_handler = DataHandler()


# ── Fonctions globales utilisées dans bot.py ─────────────────────
def get_prices_batch() -> dict:
    return data_handler.get_prices_batch()

def get_klines_1m_cached(symbol: str) -> pd.Series:
    return data_handler.get_klines_1m_cached(symbol)

def get_klines_5m_cached(symbol: str) -> pd.Series:
    return data_handler.get_klines_5m_cached(symbol)

def get_volume_data(symbol: str, interval: str, limit: int) -> list:
    return data_handler.get_volume_data(symbol, interval, limit)


# ======================== FONCTIONS MANQUANTES (stubs) ========================

def get_fear_greed_value() -> int:
    """Fear & Greed Index (valeur par défaut réaliste)"""
    return 55

def get_liquidations() -> dict:
    """Liquidations (vide pour l'instant)"""
    return {}

def get_order_book(symbol: str) -> dict:
    """Order book simplifié"""
    return {"pressure": "neutre", "ratio": 1.0, "wall_size": 0, "depth_ratio": 1.0}

def get_whale_alerts() -> list:
    """Alertes whales (vide)"""
    return []

def get_mev_alerts(symbol: str) -> list:
    """MEV alerts (vide)"""
    return []

def get_flashbots_alerts(symbol: str) -> list:
    """Flashbots alerts (vide)"""
    return []

def get_sandwich_alerts(symbol: str) -> list:
    """Sandwich alerts (vide)"""
    return []


# ======================== compute_indicators ========================
def compute_indicators(closes: list) -> dict:
    """Calcule RSI + MACD (utilisé par ResearchAgent)"""
    if len(closes) < 14:
        return {"rsi": 50.0, "macd_h": 0.0, "macd": 0.0}

    closes_series = pd.Series(closes, dtype=float)

    # RSI
    delta = closes_series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

    # MACD
    ema12 = closes_series.ewm(span=12, adjust=False).mean()
    ema26 = closes_series.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_histogram = macd_line - signal_line

    return {
        "rsi": round(float(rsi.iloc[-1]), 2),
        "macd": round(float(macd_line.iloc[-1]), 6),
        "macd_h": round(float(macd_histogram.iloc[-1]), 6)
    }
# ===========================================================================
