"""Market data fetching and caching utilities."""

import requests
import time
import pandas as pd
import numpy as np
from collections import deque
from datetime import datetime
from logging_config import logger

BINANCE_BASE = "https://api.binance.com"
BINANCE_FAPI = "https://fapi.binance.com"

class DataHandler:

    def __init__(self):
        self.price_cache    = {}
        self.kline_cache_1m = {}
        self.kline_cache_5m = {}
        self.cache_ttl      = 30

    def get_current_price(self, symbol: str) -> float | None:
        now    = time.time()
        sym    = symbol.upper()
        cached = self.price_cache.get(sym)
        if cached and now - cached["ts"] < self.cache_ttl:
            return cached["price"]
        try:
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/ticker/price",
                params={"symbol": sym}, timeout=5
            )
            r.raise_for_status()
            price = float(r.json()["price"])
            self.price_cache[sym] = {"price": price, "ts": now}
            return price
        except Exception as e:
            logger.debug(f"[DATA] Prix {sym}: {e}")
            return None

    def get_prices_batch(self, symbols: list = None) -> dict:
        now    = time.time()
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
            logger.debug(f"[DATA] Batch prices: {e}")
        return result

    def prefill_caches(self, symbols):
        logger.info(f"[DATA] Pre-remplissage caches pour {len(symbols)} symboles")
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
                logger.debug(f"[DATA] Prefill {sym}: {e}")
            time.sleep(0.3)
        logger.info("[DATA] Pre-remplissage termine")

    def get_klines(self, symbol: str, interval: str = "1m", limit: int = 100) -> pd.Series:
        symbol = symbol.upper()
        # Essai WebSocket d'abord
        try:
            from websocket_manager import ws_manager
            ws_data = ws_manager.get_klines(symbol, interval)
            if ws_data and len(ws_data) >= 14:
                return pd.Series(list(ws_data), dtype=float)
        except Exception:
            pass
        # Cache 1m
        if interval == "1m" and symbol in self.kline_cache_1m:
            cached = list(self.kline_cache_1m[symbol])
            if len(cached) >= 14:
                return pd.Series(cached, dtype=float)
        # Cache 5m
        if interval in ("5m", "5") and symbol in self.kline_cache_5m:
            cached = list(self.kline_cache_5m[symbol])
            if len(cached) >= 14:
                return pd.Series(cached, dtype=float)
        # Fallback API
        try:
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=8
            )
            if r.status_code == 200:
                closes = [float(c[4]) for c in r.json()]
                series = pd.Series(closes, dtype=float)
                if interval == "1m":
                    self.kline_cache_1m[symbol] = deque(closes, maxlen=60)
                elif interval == "5m":
                    self.kline_cache_5m[symbol] = deque(closes, maxlen=60)
                return series
        except Exception as e:
            logger.debug(f"[DATA] Klines {symbol}/{interval}: {e}")
        return pd.Series([], dtype=float)

    def update_kline(self, symbol: str, close: float, interval: str = "1m"):
        symbol = symbol.upper()
        if interval == "1m":
            if symbol not in self.kline_cache_1m:
                self.kline_cache_1m[symbol] = deque(maxlen=60)
            self.kline_cache_1m[symbol].append(close)
        elif interval == "5m":
            if symbol not in self.kline_cache_5m:
                self.kline_cache_5m[symbol] = deque(maxlen=60)
            self.kline_cache_5m[symbol].append(close)

# SINGLETON MODULE-LEVEL (bot.py fait: from data_handler import data_handler)

data_handler = DataHandler()  # Instance exportee au niveau module

# FONCTIONS MODULE-LEVEL (importees directement par bot.py)

def get_prices_batch(symbols: list = None) -> dict:
    """Retourne les prix batch depuis le singleton."""
    return data_handler.get_prices_batch(symbols)

def get_klines_1m_cached(symbol: str) -> pd.Series:
    """Retourne les klines 1m depuis le cache."""
    return data_handler.get_klines(symbol, "1m", 60)

def get_klines_5m_cached(symbol: str, limit: int = 100) -> pd.Series:
    """Alias legacy pour compatibilite imports."""
    return data_handler.get_klines(symbol, "5m", limit)

def get_volume_data(symbol: str, interval: str = "1", count: int = 10) -> list:
    """
    Retourne les volumes des dernieres `count` bougies sur l'intervalle donne.
    Signature: get_volume_data(symbol, interval, count) -> list[float]
    Compatible avec l'appel: get_volume_data(symbol, "1", 10)
    """
    interval_map = {
        "1": "1m", "3": "3m", "5": "5m", "15": "15m",
        "30": "30m", "60": "1h", "120": "2h", "240": "4h",
        "1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h",
    }
    bi_interval = interval_map.get(str(interval), "1m")
    try:
        r = requests.get(
            f"{BINANCE_BASE}/api/v3/klines",
            params={"symbol": symbol.upper(), "interval": bi_interval, "limit": count},
            timeout=6
        )
        if r.status_code == 200:
            return [float(c[5]) for c in r.json()]  # volume en base asset
    except Exception as e:
        logger.debug(f"[DATA] Volume data {symbol}: {e}")
    return [0.0] * count

def get_current_price(symbol: str) -> float | None:
    """Wrapper module-level pour compatibilite."""
    return data_handler.get_current_price(symbol)

def get_fear_greed_value() -> int:
    """Fear & Greed Index en temps reel (Alternative.me)."""
    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=1&format=json",
            timeout=8
        )
        if r.status_code == 200:
            return int(r.json()["data"][0]["value"])
    except Exception as e:
        logger.debug(f"[DATA] Fear&Greed: {e}")
    return 50

def get_liquidations() -> dict:
    """Donnees de liquidation Binance Futures."""
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/forceOrders",
            params={"symbol": "BTCUSDT", "limit": 100},
            timeout=8
        )
        if r.status_code == 200:
            orders = r.json()
            long_liq  = sum(float(o.get("origQty", 0)) * float(o.get("price", 0))
                           for o in orders if o.get("side") == "BUY")
            short_liq = sum(float(o.get("origQty", 0)) * float(o.get("price", 0))
                           for o in orders if o.get("side") == "SELL")
            return {"long_liq": long_liq, "short_liq": short_liq}
    except Exception as e:
        logger.debug(f"[DATA] Liquidations: {e}")
    return {"long_liq": 0, "short_liq": 0}

def get_order_book(symbol: str) -> dict:
    """Order book simplifie depuis Binance."""
    try:
        r = requests.get(
            f"{BINANCE_BASE}/api/v3/depth",
            params={"symbol": symbol.upper(), "limit": 20},
            timeout=6
        )
        if r.status_code == 200:
            d = r.json()
            bids_vol = sum(float(b[1]) for b in d.get("bids", [])[:10])
            asks_vol = sum(float(a[1]) for a in d.get("asks", [])[:10])
            ratio    = bids_vol / (asks_vol + 1e-9)
            return {
                "pressure":    "buy" if ratio > 1.2 else "sell" if ratio < 0.8 else "neutre",
                "ratio":       round(ratio, 3),
                "wall_size":   max(bids_vol, asks_vol),
                "depth_ratio": round(ratio, 3),
            }
    except Exception as e:
        logger.debug(f"[DATA] Order book {symbol}: {e}")
    return {"pressure": "neutre", "ratio": 1.0, "wall_size": 0, "depth_ratio": 1.0}

def get_whale_alerts(symbol: str = "BTCUSDT") -> list:
    """FIX (2026-08-14): was a permanent no-op stub (`return []`), silently
    starving ResearchAgent's anomaly detection of any real whale signal.
    Reuses the same proven method as bot.py's check_whale_filter() (percentile
    of trade size among the symbol's own recent aggTrades, not a fixed dollar
    cutoff) instead of introducing a second, divergent whale-detection logic."""
    try:
        symbol = symbol.upper()
        r = requests.get(
            f"{BINANCE_BASE}/api/v3/aggTrades",
            params={"symbol": symbol, "limit": 200},
            timeout=5
        )
        price_r = requests.get(
            f"{BINANCE_BASE}/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=4
        )
        if r.status_code != 200 or price_r.status_code != 200:
            return []
        trades = r.json()
        ref_price = float(price_r.json().get("price", 0))
        if not ref_price or not isinstance(trades, list) or len(trades) < 20:
            return []
        values = sorted(float(t.get("q", 0)) * ref_price for t in trades)
        threshold = values[int(len(values) * 0.95)]  # top 5% by size in this window = "whale"
        alerts = []
        for t in trades:
            value = float(t.get("q", 0)) * ref_price
            if value >= threshold and value > 5000:  # ignore illiquid symbols where "top 5%" is trivially small
                alerts.append({
                    "symbol":    symbol,
                    "side":      "SELL" if t.get("m") else "BUY",
                    "value_usd": round(value, 0),
                    "price":     float(t.get("p", 0)),
                    "ts":        t.get("T", 0),
                })
        return alerts
    except Exception as e:
        logger.debug(f"[DATA] Whale alerts {symbol}: {e}")
        return []

# NOTE (2026-08-14): get_mev_alerts/get_flashbots_alerts/get_sandwich_alerts
# were also permanent no-op stubs. Deliberately left as no-ops rather than
# faking a detector: MEV/flashbots/sandwich-attack detection are on-chain/DEX
# concepts (mempool front-running) that don't apply to trades executed on a
# centralized exchange (Binance) the way this bot trades -- there's no real
# signal to compute here, unlike whale_alerts above which had a genuine,
# already-proven Binance-based method just sitting unused elsewhere in the
# codebase. Don't build fake detectors for these; if MEV-relevant signals are
# ever wanted, they'd need real on-chain infra (not in scope here).
def get_mev_alerts(symbol: str) -> list:
    return []

def get_flashbots_alerts(symbol: str) -> list:
    return []

def get_sandwich_alerts(symbol: str) -> list:
    return []

def compute_indicators(closes: list) -> dict:
    """Calcule RSI + MACD + Bollinger Bands."""
    if len(closes) < 14:
        return {"rsi": 50.0, "macd_h": 0.0, "macd": 0.0, "bb_upper": 0.0, "bb_lower": 0.0}

    s = pd.Series(closes, dtype=float)

    # RSI
    delta = s.diff()
    gain  = delta.where(delta > 0, 0).rolling(14).mean()
    loss  = -delta.where(delta < 0, 0).rolling(14).mean()
    rsi   = 100 - (100 / (1 + gain / loss))

    # MACD
    ema12      = s.ewm(span=12, adjust=False).mean()
    ema26      = s.ewm(span=26, adjust=False).mean()
    macd_line  = ema12 - ema26
    signal     = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist  = macd_line - signal

    # Bollinger Bands 20
    ma20  = s.rolling(20).mean()
    std20 = s.rolling(20).std()
    bb_up = ma20 + 2 * std20
    bb_dn = ma20 - 2 * std20

    def safe(v):
        try:
            return round(float(v.iloc[-1]), 6) if not np.isnan(float(v.iloc[-1])) else 0.0
        except Exception:
            return 0.0

    return {
        "rsi":      round(float(rsi.iloc[-1]), 2),
        "macd":     safe(macd_line),
        "macd_h":   safe(macd_hist),
        "bb_upper": safe(bb_up),
        "bb_lower": safe(bb_dn),
        "ma20":     safe(ma20),
    }
