"""
📊 DATA HANDLER V3 — Données marché temps réel + FIX fonctions manquantes
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIXES V3 :
- Ajout get_fear_greed_value() réelle (Alternative.me)
- Ajout get_liquidations() réelle (Coinglass/Binance)
- Ajout get_volume_data() réelle (Binance 24h ticker)
- Ajout get_klines_5m_cached() alias pour compatibilité imports legacy
- compute_indicators() complété avec Bollinger Bands
"""

import requests
import time
import pandas as pd
import numpy as np
from collections import deque
from datetime import datetime
from logging_config import logger

BINANCE_BASE   = "https://api.binance.com"
BINANCE_FAPI   = "https://fapi.binance.com"


class DataHandler:

    def __init__(self):
        self.price_cache     = {}
        self.kline_cache_1m  = {}
        self.kline_cache_5m  = {}
        self.cache_ttl       = 30

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
        # Fallback API directe
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
            logger.warning(f"[DATA] Klines fallback error {symbol}/{interval}: {e}")
        return pd.Series([], dtype=float)


# ────────────────────────────────────────────────────────────────────────────
# FONCTIONS MODULE-LEVEL (utilisées par les agents via import direct)
# ────────────────────────────────────────────────────────────────────────────

_dh_instance = None

def _get_dh() -> DataHandler:
    global _dh_instance
    if _dh_instance is None:
        _dh_instance = DataHandler()
    return _dh_instance


def get_klines_5m_cached(symbol: str, limit: int = 100) -> pd.Series:
    """Alias pour compatibilité legacy — certains agents importaient cette fonction."""
    return _get_dh().get_klines(symbol, "5m", limit)


def get_fear_greed_value() -> int:
    """Récupère le Fear & Greed Index en temps réel (Alternative.me)."""
    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=1&format=json",
            timeout=8
        )
        if r.status_code == 200:
            return int(r.json()["data"][0]["value"])
    except Exception as e:
        logger.warning(f"[DATA] Fear&Greed error: {e}")
    return 50


def get_liquidations() -> dict:
    """
    Récupère les données de liquidation depuis Binance Futures.
    Retourne les sommes longues/courtes liquidées dans les dernières 24h.
    """
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
        logger.debug(f"[DATA] Liquidations fetch error: {e}")
    return {"long_liq": 0, "short_liq": 0}


def get_volume_data(symbol: str) -> dict:
    """Récupère les données de volume 24h depuis Binance."""
    try:
        r = requests.get(
            f"{BINANCE_BASE}/api/v3/ticker/24hr",
            params={"symbol": symbol.upper()},
            timeout=6
        )
        if r.status_code == 200:
            d = r.json()
            vol   = float(d.get("volume", 0))
            qvol  = float(d.get("quoteVolume", 0))
            # Volume spike = volume 24h > moyenne estimée (heuristique)
            spike = qvol > 5_000_000  # > 5M USDT de volume → spike
            return {
                "volume_24h":    vol,
                "quote_volume":  qvol,
                "volume_spike":  spike,
                "change_pct":    float(d.get("priceChangePercent", 0)),
                "trades_count":  int(d.get("count", 0)),
            }
    except Exception as e:
        logger.debug(f"[DATA] Volume data error {symbol}: {e}")
    return {"volume_24h": 0, "quote_volume": 0, "volume_spike": False, "change_pct": 0}


def get_order_book(symbol: str) -> dict:
    """Order book simplifié depuis Binance."""
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
        logger.debug(f"[DATA] Order book error {symbol}: {e}")
    return {"pressure": "neutre", "ratio": 1.0, "wall_size": 0, "depth_ratio": 1.0}


def get_whale_alerts() -> list:
    """Alertes whales — placeholder (API Whale Alert nécessite clé payante)."""
    return []


def get_mev_alerts(symbol: str) -> list:
    """MEV alerts — placeholder."""
    return []


def get_flashbots_alerts(symbol: str) -> list:
    """Flashbots alerts — placeholder."""
    return []


def get_sandwich_alerts(symbol: str) -> list:
    """Sandwich alerts — placeholder."""
    return []


# ────────────────────────────────────────────────────────────────────────────
# COMPUTE INDICATORS
# ────────────────────────────────────────────────────────────────────────────

def compute_indicators(closes: list) -> dict:
    """Calcule RSI + MACD + Bollinger Bands."""
    if len(closes) < 14:
        return {"rsi": 50.0, "macd_h": 0.0, "macd": 0.0, "bb_upper": 0.0, "bb_lower": 0.0}

    closes_series = pd.Series(closes, dtype=float)

    # RSI
    delta = closes_series.diff()
    gain  = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss  = -delta.where(delta < 0, 0).rolling(window=14).mean()
    rs    = gain / loss
    rsi   = 100 - (100 / (1 + rs))

    # MACD
    ema12 = closes_series.ewm(span=12, adjust=False).mean()
    ema26 = closes_series.ewm(span=26, adjust=False).mean()
    macd_line   = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist   = macd_line - signal_line

    # Bollinger Bands (20, 2)
    ma20    = closes_series.rolling(window=20).mean()
    std20   = closes_series.rolling(window=20).std()
    bb_up   = ma20 + 2 * std20
    bb_down = ma20 - 2 * std20

    return {
        "rsi":      round(float(rsi.iloc[-1]), 2),
        "macd":     round(float(macd_line.iloc[-1]), 6),
        "macd_h":   round(float(macd_hist.iloc[-1]), 6),
        "bb_upper": round(float(bb_up.iloc[-1]), 6) if not np.isnan(bb_up.iloc[-1]) else 0.0,
        "bb_lower": round(float(bb_down.iloc[-1]), 6) if not np.isnan(bb_down.iloc[-1]) else 0.0,
        "ma20":     round(float(ma20.iloc[-1]), 6) if not np.isnan(ma20.iloc[-1]) else 0.0,
    }
