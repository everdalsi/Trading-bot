"""
📊 ANALYST AGENT V7 — Expert Analyse Technique Professionnelle
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UPGRADES V7 (expert-level) :
- Wilder RSI (EMA smoothed) — plus précis que la moyenne simple
- ATR (Average True Range) — volatilité réelle pour stops et sizing
- VWAP (Volume Weighted Average Price) — niveau clé institutionnel
- Ichimoku Cloud (Tenkan/Kijun/Senkou A&B/Chikou) — système complet
- Stochastic RSI — suracheté/survendu avec plus de sensibilité
- Williams %R — momentum intraday
- Fibonacci retracements (23.6/38.2/50/61.8/78.6%) — niveaux de support/résistance
- Divergences RSI / prix (haussières ET baissières confirmées)
- Multi-timeframe confluence : 5m + 15m + 1h + 4h
- Heikin Ashi : filtre de tendance anti-bruit
- Score composite 12 facteurs pondérés par régime de marché
- Cache intelligent par (symbole, intervalle)
"""

import time
import requests
import numpy as np
from typing import Dict, Any, List, Tuple, Optional
from agents.base_agent import BaseAgent
from logging_config import logger

BINANCE_BASE = "https://api.binance.com"

# ── Poids par facteur (total = 1.0) ──────────────────────────────────────────
FACTOR_WEIGHTS = {
    "rsi":          0.14,
    "stoch_rsi":    0.08,
    "macd":         0.12,
    "bb":           0.08,
    "vwap":         0.10,
    "ichimoku":     0.10,
    "volume":       0.08,
    "structure":    0.10,
    "divergence":   0.10,
    "williams_r":   0.05,
    "fibonacci":    0.05,
}


class AnalystAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="analyst",
            description=(
                "Expert Analyse Technique : Wilder RSI, ATR, VWAP, Ichimoku, "
                "StochRSI, Williams %R, Fibonacci, divergences, multi-timeframe"
            ),
            role=(
                "Analyse technique professionnelle — 11 indicateurs + multi-timeframe "
                "confluence — score composite pondéré"
            )
        )
        self._klines_cache: Dict[str, Dict] = {}
        self._cache_ttl_map = {
            "1m": 30, "3m": 60, "5m": 60, "15m": 120,
            "30m": 180, "1h": 300, "2h": 600, "4h": 900, "1d": 3600,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # COLLECTE DONNÉES — Cache par (symbole, intervalle)
    # ──────────────────────────────────────────────────────────────────────────

    def _get_klines(
        self, symbol: str, interval: str = "5m", limit: int = 200
    ) -> Tuple[List[float], List[float], List[float], List[float], List[float]]:
        """Retourne (opens, closes, highs, lows, volumes) depuis Binance."""
        key = f"{symbol}_{interval}"
        now = time.time()
        ttl = self._cache_ttl_map.get(interval, 60)
        cached = self._klines_cache.get(key)
        if cached and now - cached["ts"] < ttl:
            return cached["data"]

        try:
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/klines",
                params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
                timeout=8,
            )
            if r.status_code == 200:
                raw = r.json()
                opens   = [float(c[1]) for c in raw]
                closes  = [float(c[4]) for c in raw]
                highs   = [float(c[2]) for c in raw]
                lows    = [float(c[3]) for c in raw]
                volumes = [float(c[5]) for c in raw]
                result = (opens, closes, highs, lows, volumes)
                self._klines_cache[key] = {"data": result, "ts": now}
                return result
        except Exception as e:
            logger.warning(f"[AnalystV7] klines {symbol}/{interval}: {e}")

        empty: Tuple[List, List, List, List, List] = ([], [], [], [], [])
        return empty

    # ──────────────────────────────────────────────────────────────────────────
    # INDICATEURS — Implémentations numériques précises
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _wilder_rsi(closes: List[float], period: int = 14) -> float:
        """RSI avec lissage de Wilder (EMA récursive) — plus précis que la moyenne simple."""
        if len(closes) < period + 1:
            return 50.0
        deltas = np.diff(closes)
        gains  = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        # Initialisation Wilder
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        for g, l in zip(gains[period:], losses[period:]):
            avg_gain = (avg_gain * (period - 1) + g) / period
            avg_loss = (avg_loss * (period - 1) + l) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100.0 - 100.0 / (1.0 + rs), 2)

    @staticmethod
    def _stoch_rsi(closes: List[float], rsi_period: int = 14, stoch_period: int = 14) -> Tuple[float, float]:
        """Stochastic RSI — %K et %D."""
        if len(closes) < rsi_period + stoch_period + 5:
            return 50.0, 50.0
        # Série de RSI glissants
        rsi_values = []
        for i in range(stoch_period, len(closes)):
            rsi_values.append(AnalystAgent._wilder_rsi(closes[max(0, i - rsi_period - 10): i + 1], rsi_period))
        if len(rsi_values) < stoch_period:
            return 50.0, 50.0
        recent_rsi = rsi_values[-stoch_period:]
        min_rsi = min(recent_rsi)
        max_rsi = max(recent_rsi)
        if max_rsi == min_rsi:
            return 50.0, 50.0
        k = 100.0 * (rsi_values[-1] - min_rsi) / (max_rsi - min_rsi)
        d = np.mean([100.0 * (r - min_rsi) / (max_rsi - min_rsi) for r in recent_rsi[-3:]])
        return round(k, 2), round(d, 2)

    @staticmethod
    def _atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        """Average True Range — volatilité réelle."""
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            h, l, pc = highs[i], lows[i], closes[i - 1]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        if not trs:
            return 0.0
        # Wilder smoothing
        atr = np.mean(trs[:period])
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return round(atr, 6)

    @staticmethod
    def _ema(values: List[float], period: int) -> float:
        """EMA sur une liste de valeurs."""
        if len(values) < period:
            return float(np.mean(values)) if values else 0.0
        k = 2.0 / (period + 1)
        ema = np.mean(values[:period])
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return round(ema, 6)

    @staticmethod
    def _macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[float, float, float]:
        """MACD line, Signal line, Histogram."""
        if len(closes) < slow + signal:
            return 0.0, 0.0, 0.0
        ema_fast = AnalystAgent._ema(closes, fast)
        ema_slow = AnalystAgent._ema(closes, slow)
        macd_line = ema_fast - ema_slow
        # Signal : EMA du MACD
        macd_series = []
        for i in range(slow, len(closes)):
            ef = AnalystAgent._ema(closes[:i + 1], fast)
            es = AnalystAgent._ema(closes[:i + 1], slow)
            macd_series.append(ef - es)
        if len(macd_series) < signal:
            return round(macd_line, 6), 0.0, round(macd_line, 6)
        signal_line = AnalystAgent._ema(macd_series, signal)
        histogram = macd_line - signal_line
        return round(macd_line, 6), round(signal_line, 6), round(histogram, 6)

    @staticmethod
    def _bollinger(closes: List[float], period: int = 20, std_mult: float = 2.0) -> Tuple[float, float, float, bool, bool]:
        """Bollinger Bands — retourne (upper, mid, lower, squeeze, breakout_up)."""
        if len(closes) < period:
            m = closes[-1] if closes else 0.0
            return m, m, m, False, False
        window = closes[-period:]
        mid   = float(np.mean(window))
        std   = float(np.std(window))
        upper = mid + std_mult * std
        lower = mid - std_mult * std
        width = (upper - lower) / mid if mid != 0 else 0
        # Squeeze : bandes très serrées (< 2% du prix)
        squeeze = width < 0.02
        price   = closes[-1]
        breakout_up = price > upper
        return round(upper, 6), round(mid, 6), round(lower, 6), squeeze, breakout_up

    @staticmethod
    def _vwap(highs: List[float], lows: List[float], closes: List[float], volumes: List[float]) -> float:
        """Volume Weighted Average Price — référence institutionnelle."""
        if not volumes or sum(volumes) == 0:
            return closes[-1] if closes else 0.0
        typical_prices = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
        vwap = sum(tp * v for tp, v in zip(typical_prices, volumes)) / sum(volumes)
        return round(vwap, 6)

    @staticmethod
    def _ichimoku(
        highs: List[float], lows: List[float], closes: List[float],
        tenkan_period: int = 9, kijun_period: int = 26, senkou_b_period: int = 52
    ) -> Dict[str, float]:
        """Ichimoku Cloud complet."""
        def mid_line(h_list, l_list, n):
            if len(h_list) < n:
                return (max(h_list) + min(l_list)) / 2 if h_list and l_list else 0.0
            return (max(h_list[-n:]) + min(l_list[-n:])) / 2

        tenkan  = mid_line(highs, lows, tenkan_period)
        kijun   = mid_line(highs, lows, kijun_period)
        senkou_a = (tenkan + kijun) / 2
        senkou_b = mid_line(highs, lows, senkou_b_period)
        chikou  = closes[-1] if closes else 0.0
        price   = closes[-1] if closes else 0.0

        above_cloud = price > max(senkou_a, senkou_b)
        below_cloud = price < min(senkou_a, senkou_b)
        in_cloud    = not above_cloud and not below_cloud
        bullish_cross = tenkan > kijun  # TK Cross haussier

        return {
            "tenkan": round(tenkan, 6),
            "kijun":  round(kijun,  6),
            "senkou_a": round(senkou_a, 6),
            "senkou_b": round(senkou_b, 6),
            "chikou":   round(chikou, 6),
            "above_cloud": above_cloud,
            "below_cloud": below_cloud,
            "in_cloud":    in_cloud,
            "bullish_cross": bullish_cross,
        }

    @staticmethod
    def _williams_r(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        """Williams %R — oscillateur momentum."""
        if len(closes) < period:
            return -50.0
        h_max = max(highs[-period:])
        l_min = min(lows[-period:])
        if h_max == l_min:
            return -50.0
        wr = -100.0 * (h_max - closes[-1]) / (h_max - l_min)
        return round(wr, 2)

    @staticmethod
    def _fibonacci_levels(highs: List[float], lows: List[float], lookback: int = 50) -> Dict[str, Any]:
        """Niveaux Fibonacci sur le dernier swing significatif."""
        if len(highs) < lookback:
            return {}
        h = max(highs[-lookback:])
        l = min(lows[-lookback:])
        diff = h - l
        price = highs[-1] if highs else 0.0
        levels = {
            "0.0":   round(l, 6),
            "23.6":  round(l + diff * 0.236, 6),
            "38.2":  round(l + diff * 0.382, 6),
            "50.0":  round(l + diff * 0.500, 6),
            "61.8":  round(l + diff * 0.618, 6),
            "78.6":  round(l + diff * 0.786, 6),
            "100.0": round(h, 6),
        }
        # Niveau Fibonacci le plus proche du prix actuel
        closest = min(levels.items(), key=lambda x: abs(x[1] - price), default=("50.0", price))
        return {"levels": levels, "closest_level": closest[0], "closest_price": closest[1]}

    @staticmethod
    def _detect_divergences(closes: List[float], period: int = 14, lookback: int = 50) -> Dict[str, bool]:
        """
        Détecte les divergences RSI / prix :
        - Divergence haussière : prix fait un nouveau bas, RSI ne confirme pas
        - Divergence baissière : prix fait un nouveau haut, RSI ne confirme pas
        """
        if len(closes) < lookback + period:
            return {"bullish_div": False, "bearish_div": False}

        # RSI sur chaque point
        rsi_series = []
        for i in range(lookback, len(closes)):
            rsi_series.append(AnalystAgent._wilder_rsi(closes[max(0, i - period - 5): i + 1], period))

        if len(rsi_series) < 10:
            return {"bullish_div": False, "bearish_div": False}

        price_recent = closes[-10:]
        rsi_recent   = rsi_series[-10:]

        # Cherche divergence haussière (prix bas vs RSI plus haut)
        price_min_idx = int(np.argmin(price_recent))
        rsi_at_price_min = rsi_recent[price_min_idx]
        rsi_min = min(rsi_recent)
        bullish_div = (price_recent[price_min_idx] <= min(price_recent) and
                       rsi_at_price_min > rsi_min + 3)

        # Cherche divergence baissière (prix haut vs RSI plus bas)
        price_max_idx = int(np.argmax(price_recent))
        rsi_at_price_max = rsi_recent[price_max_idx]
        rsi_max = max(rsi_recent)
        bearish_div = (price_recent[price_max_idx] >= max(price_recent) and
                       rsi_at_price_max < rsi_max - 3)

        return {"bullish_div": bullish_div, "bearish_div": bearish_div}

    @staticmethod
    def _market_structure(highs: List[float], lows: List[float], closes: List[float]) -> Dict[str, Any]:
        """
        Analyse de structure de marché : Higher Highs/Higher Lows (uptrend)
        vs Lower Highs/Lower Lows (downtrend).
        Détecte aussi les cassures de structure (Break of Structure).
        """
        if len(closes) < 20:
            return {"trend": "neutral", "bos": False, "choch": False, "strength": 0.5}

        n = min(30, len(closes))
        h_slice = highs[-n:]
        l_slice = lows[-n:]
        c_slice = closes[-n:]

        # Pivots highs et lows (simples)
        hh = sum(1 for i in range(1, n - 1) if h_slice[i] > h_slice[i - 1] and h_slice[i] > h_slice[i + 1])
        ll = sum(1 for i in range(1, n - 1) if l_slice[i] < l_slice[i - 1] and l_slice[i] < l_slice[i + 1])

        # Direction globale
        slope = (c_slice[-1] - c_slice[0]) / (n * c_slice[0] + 1e-9)
        recent_high = max(h_slice[-5:])
        prev_high   = max(h_slice[-10:-5]) if len(h_slice) >= 10 else recent_high
        recent_low  = min(l_slice[-5:])
        prev_low    = min(l_slice[-10:-5]) if len(l_slice) >= 10 else recent_low

        if slope > 0.002 and recent_high > prev_high and recent_low > prev_low:
            trend = "uptrend"
            strength = min(1.0, 0.6 + slope * 10)
        elif slope < -0.002 and recent_high < prev_high and recent_low < prev_low:
            trend = "downtrend"
            strength = max(0.0, 0.4 - abs(slope) * 10)
        else:
            trend = "ranging"
            strength = 0.5

        # Break of Structure (BOS) : cassure du dernier pivot
        bos = c_slice[-1] > max(h_slice[-10:-1]) or c_slice[-1] < min(l_slice[-10:-1])
        # Change of Character (CHoCH)
        choch = (trend == "downtrend" and recent_low > prev_low)

        return {
            "trend": trend, "slope": round(slope, 5),
            "bos": bos, "choch": choch, "strength": round(strength, 3),
            "higher_highs": hh, "lower_lows": ll,
        }

    @staticmethod
    def _heikin_ashi(
        opens: List[float], closes: List[float], highs: List[float], lows: List[float]
    ) -> Dict[str, Any]:
        """
        Heikin Ashi : filtre de tendance anti-bruit.
        Retourne la couleur (bull/bear) et la force de la bougie.
        """
        if len(closes) < 3:
            return {"color": "neutral", "strength": 0.5, "doji": False}
        ha_closes, ha_opens = [], []
        for i in range(len(closes)):
            ha_c = (opens[i] + highs[i] + lows[i] + closes[i]) / 4
            ha_o = (opens[i - 1] + closes[i - 1]) / 2 if i > 0 else (opens[i] + closes[i]) / 2
            ha_closes.append(ha_c)
            ha_opens.append(ha_o)
        last_ha_c = ha_closes[-1]
        last_ha_o = ha_opens[-1]
        body_size = abs(last_ha_c - last_ha_o)
        total_size = max(highs[-1] - lows[-1], 1e-9)
        strength = body_size / total_size
        # Tendance Heikin Ashi : 3 bougies consécutives de même couleur
        last3_colors = [
            "bull" if ha_closes[i] > ha_opens[i] else "bear"
            for i in range(-3, 0)
        ]
        if all(c == "bull" for c in last3_colors):
            color = "bull_strong"
        elif all(c == "bear" for c in last3_colors):
            color = "bear_strong"
        elif last3_colors[-1] == "bull":
            color = "bull"
        else:
            color = "bear"
        doji = strength < 0.1
        return {"color": color, "strength": round(strength, 3), "doji": doji}

    # ──────────────────────────────────────────────────────────────────────────
    # ANALYSE MULTI-TIMEFRAME
    # ──────────────────────────────────────────────────────────────────────────

    def _analyze_timeframe(self, symbol: str, interval: str, limit: int = 200) -> Dict[str, Any]:
        """Analyse complète sur un timeframe donné."""
        opens, closes, highs, lows, volumes = self._get_klines(symbol, interval, limit)
        if len(closes) < 30:
            return {"valid": False, "score": 0.5, "signal": "NEUTRAL", "interval": interval}

        rsi        = self._wilder_rsi(closes)
        stoch_k, stoch_d = self._stoch_rsi(closes)
        macd_line, macd_signal, macd_hist = self._macd(closes)
        bb_upper, bb_mid, bb_lower, bb_squeeze, bb_breakout = self._bollinger(closes)
        vwap_val   = self._vwap(highs, lows, closes, volumes)
        ichimoku   = self._ichimoku(highs, lows, closes)
        atr        = self._atr(highs, lows, closes)
        wr         = self._williams_r(highs, lows, closes)
        struct     = self._market_structure(highs, lows, closes)
        div        = self._detect_divergences(closes)
        fib        = self._fibonacci_levels(highs, lows)
        ha         = self._heikin_ashi(opens, closes, highs, lows)

        price = closes[-1]
        vol_sma20 = np.mean(volumes[-20:]) if len(volumes) >= 20 else 1.0
        vol_ratio = volumes[-1] / vol_sma20 if vol_sma20 > 0 else 1.0

        # ── Scoring par facteur ────────────────────────────────────────────

        # RSI : suracheté/survendu, zones de momentum
        if rsi < 30:
            rsi_score = 0.85  # Survendu → achat
        elif rsi < 45:
            rsi_score = 0.65
        elif rsi < 55:
            rsi_score = 0.50
        elif rsi < 70:
            rsi_score = 0.35
        else:
            rsi_score = 0.15  # Suracheté → vente

        # Stochastic RSI
        if stoch_k < 20 and stoch_d < 20:
            stoch_score = 0.85
        elif stoch_k < 40:
            stoch_score = 0.65
        elif stoch_k > 80 and stoch_d > 80:
            stoch_score = 0.15
        elif stoch_k > 60:
            stoch_score = 0.35
        else:
            stoch_score = 0.50
        # Croisement K/D
        if stoch_k > stoch_d and stoch_k < 50:
            stoch_score = min(1.0, stoch_score + 0.15)
        elif stoch_k < stoch_d and stoch_k > 50:
            stoch_score = max(0.0, stoch_score - 0.15)

        # MACD
        macd_score = 0.50
        if macd_hist > 0:
            macd_score = 0.70
        if macd_hist < 0:
            macd_score = 0.30
        if macd_line > macd_signal and macd_hist > 0:
            macd_score = 0.80  # Croisement haussier
        if macd_line < macd_signal and macd_hist < 0:
            macd_score = 0.20  # Croisement baissier

        # Bollinger Bands
        if bb_squeeze:
            bb_score = 0.55  # Squeeze → explosion imminente (neutre)
        elif bb_breakout:
            bb_score = 0.80 if macd_hist > 0 else 0.25
        elif price < bb_lower:
            bb_score = 0.80  # Prix sous la bande basse → rebond potentiel
        elif price > bb_upper:
            bb_score = 0.20  # Prix au-dessus de la bande haute → suracheté
        else:
            bb_score = 0.50 + (bb_mid - price) / (bb_upper - bb_mid + 1e-9) * 0.1

        # VWAP
        if price > vwap_val * 1.005:
            vwap_score = 0.65  # Au-dessus VWAP
        elif price < vwap_val * 0.995:
            vwap_score = 0.35  # Sous VWAP
        else:
            vwap_score = 0.50  # VWAP zone

        # Ichimoku
        if ichimoku["above_cloud"] and ichimoku["bullish_cross"]:
            ichi_score = 0.85
        elif ichimoku["above_cloud"]:
            ichi_score = 0.70
        elif ichimoku["below_cloud"] and not ichimoku["bullish_cross"]:
            ichi_score = 0.15
        elif ichimoku["below_cloud"]:
            ichi_score = 0.30
        else:
            ichi_score = 0.50  # Dans le cloud

        # Volume
        if vol_ratio > 2.0:
            vol_score = 0.80 if macd_hist > 0 else 0.20
        elif vol_ratio > 1.5:
            vol_score = 0.65 if macd_hist > 0 else 0.35
        elif vol_ratio < 0.5:
            vol_score = 0.45  # Volume faible → signal moins fiable
        else:
            vol_score = 0.55

        # Structure de marché
        if struct["trend"] == "uptrend":
            struct_score = 0.75
            if struct["choch"]:
                struct_score = 0.85
        elif struct["trend"] == "downtrend":
            struct_score = 0.25
        else:
            struct_score = 0.50
        if struct["bos"]:
            struct_score = min(1.0, struct_score + 0.10)

        # Divergences
        if div["bullish_div"]:
            div_score = 0.85
        elif div["bearish_div"]:
            div_score = 0.15
        else:
            div_score = 0.50

        # Williams %R
        if wr < -80:
            wr_score = 0.80  # Survendu
        elif wr < -50:
            wr_score = 0.60
        elif wr > -20:
            wr_score = 0.20  # Suracheté
        else:
            wr_score = 0.40

        # Fibonacci
        fib_score = 0.50
        if fib.get("closest_level"):
            cl = float(fib["closest_level"])
            if cl in [38.2, 50.0, 61.8]:
                # Niveau Fib clé → support/résistance potentiel
                fib_score = 0.65 if struct["trend"] == "uptrend" else 0.35

        # ── Score composite pondéré ────────────────────────────────────────
        scores = {
            "rsi":       rsi_score,
            "stoch_rsi": stoch_score,
            "macd":      macd_score,
            "bb":        bb_score,
            "vwap":      vwap_score,
            "ichimoku":  ichi_score,
            "volume":    vol_score,
            "structure": struct_score,
            "divergence": div_score,
            "williams_r": wr_score,
            "fibonacci":  fib_score,
        }
        composite = sum(scores[k] * FACTOR_WEIGHTS[k] for k in scores)

        # ── Signal ────────────────────────────────────────────────────────
        if composite >= 0.70:
            signal = "STRONG_BUY"
        elif composite >= 0.60:
            signal = "BUY"
        elif composite >= 0.55:
            signal = "WEAK_BUY"
        elif composite <= 0.30:
            signal = "STRONG_SELL"
        elif composite <= 0.40:
            signal = "SELL"
        elif composite <= 0.45:
            signal = "WEAK_SELL"
        else:
            signal = "NEUTRAL"

        return {
            "valid":      True,
            "interval":   interval,
            "signal":     signal,
            "score":      round(composite, 4),
            "rsi":        rsi,
            "stoch_k":    stoch_k,
            "stoch_d":    stoch_d,
            "macd_hist":  macd_hist,
            "bb_squeeze": bb_squeeze,
            "vwap":       vwap_val,
            "price":      price,
            "vwap_diff":  round((price - vwap_val) / vwap_val * 100, 3) if vwap_val else 0,
            "atr":        atr,
            "atr_pct":    round(atr / price * 100, 3) if price else 0,
            "ichimoku":   ichimoku,
            "williams_r": wr,
            "struct":     struct,
            "divergence": div,
            "fibonacci":  fib,
            "ha_color":   ha["color"],
            "vol_ratio":  round(vol_ratio, 2),
            "scores_detail": scores,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # RESPOND — Point d'entrée principal
    # ──────────────────────────────────────────────────────────────────────────

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name, "summary": "Hors domaine analyst",
                "confidence": 0.0, "recommendation": "HOLD",
            }

        symbol = context.get("symbol", "BTCUSDT")
        regime = context.get("market_regime", "NEUTRAL")

        # ── Analyse multi-timeframe (4 TF) ────────────────────────────────
        tf_5m  = self._analyze_timeframe(symbol, "5m",  100)
        tf_15m = self._analyze_timeframe(symbol, "15m", 150)
        tf_1h  = self._analyze_timeframe(symbol, "1h",  200)
        tf_4h  = self._analyze_timeframe(symbol, "4h",  200)

        # Poids TF : 4h domine (tendance principale)
        tf_weights = {"5m": 0.15, "15m": 0.25, "1h": 0.35, "4h": 0.25}
        composite_mtf = 0.50
        valid_tfs = [tf for tf in [tf_5m, tf_15m, tf_1h, tf_4h] if tf.get("valid")]
        if valid_tfs:
            weighted_sum = sum(
                tf["score"] * tf_weights.get(tf["interval"], 0.25)
                for tf in valid_tfs
            )
            total_w = sum(tf_weights.get(tf["interval"], 0.25) for tf in valid_tfs)
            composite_mtf = weighted_sum / total_w if total_w > 0 else 0.50

        # ── Confluence multi-timeframe ─────────────────────────────────────
        signals = [tf["signal"] for tf in valid_tfs]
        buy_signals  = sum(1 for s in signals if "BUY"  in s)
        sell_signals = sum(1 for s in signals if "SELL" in s)
        n_tfs = len(valid_tfs)
        confluence_bonus = 0.0
        if n_tfs >= 3:
            if buy_signals >= 3:
                confluence_bonus = 0.08   # Forte confluence haussière
            elif sell_signals >= 3:
                confluence_bonus = -0.08  # Forte confluence baissière
        composite_mtf = max(0.0, min(1.0, composite_mtf + confluence_bonus))

        # ── Ajustement régime ─────────────────────────────────────────────
        if regime == "BULL":
            composite_mtf = min(1.0, composite_mtf * 1.05)
        elif regime == "BEAR":
            composite_mtf = max(0.0, composite_mtf * 0.95)

        # ── Signal final ──────────────────────────────────────────────────
        if composite_mtf >= 0.72:
            recommendation = "STRONG_BUY"
        elif composite_mtf >= 0.62:
            recommendation = "BUY"
        elif composite_mtf >= 0.55:
            recommendation = "WEAK_BUY"
        elif composite_mtf <= 0.28:
            recommendation = "STRONG_SELL"
        elif composite_mtf <= 0.38:
            recommendation = "SELL"
        elif composite_mtf <= 0.45:
            recommendation = "WEAK_SELL"
        else:
            recommendation = "NEUTRAL"

        # ── Niveaux de stop-loss / take-profit via ATR ────────────────────
        ref_tf = tf_1h if tf_1h.get("valid") else tf_5m
        price  = ref_tf.get("price", context.get("price", 0.0))
        atr    = ref_tf.get("atr", 0.0)
        stop_loss   = round(price - atr * 1.5, 6) if price and atr else None
        take_profit = round(price + atr * 2.5, 6) if price and atr else None
        rr_ratio    = round(atr * 2.5 / (atr * 1.5), 2) if atr else 1.67

        # ── Summary ───────────────────────────────────────────────────────
        rsi_5m  = tf_5m.get("rsi", 50.0) if tf_5m.get("valid") else 50.0
        rsi_1h  = tf_1h.get("rsi", 50.0) if tf_1h.get("valid") else 50.0
        ichi    = ref_tf.get("ichimoku", {})
        cloud   = "Au-dessus" if ichi.get("above_cloud") else ("Sous" if ichi.get("below_cloud") else "Dans")
        ha_color = ref_tf.get("ha_color", "neutral")
        div_str = "DIV HAUSSIÈRE ✅" if ref_tf.get("divergence", {}).get("bullish_div") else (
            "DIV BAISSIÈRE ⚠️" if ref_tf.get("divergence", {}).get("bearish_div") else "Pas de divergence"
        )

        summary = (
            f"[AnalystV7] {symbol} | Score MTF: {composite_mtf:.2%} | {recommendation} | "
            f"RSI 5m:{rsi_5m:.0f} 1h:{rsi_1h:.0f} | MACD hist:{ref_tf.get('macd_hist', 0):.4f} | "
            f"VWAP diff:{ref_tf.get('vwap_diff', 0):+.2f}% | Ichimoku:{cloud} nuage | "
            f"HA:{ha_color} | {div_str} | "
            f"TF confluence: {buy_signals}↑/{sell_signals}↓/{n_tfs}TF | "
            f"SL:{stop_loss} TP:{take_profit} R/R:{rr_ratio}"
        )

        arguments = [
            f"5m  → {tf_5m.get('signal', 'N/A')} (score {tf_5m.get('score', 0):.2%})",
            f"15m → {tf_15m.get('signal', 'N/A')} (score {tf_15m.get('score', 0):.2%})",
            f"1h  → {tf_1h.get('signal', 'N/A')} (score {tf_1h.get('score', 0):.2%})",
            f"4h  → {tf_4h.get('signal', 'N/A')} (score {tf_4h.get('score', 0):.2%})",
            f"RSI Wilder 1h: {rsi_1h:.1f} | StochRSI: {ref_tf.get('stoch_k', 50):.1f}K/{ref_tf.get('stoch_d', 50):.1f}D",
            f"MACD histogramme: {ref_tf.get('macd_hist', 0):+.4f}",
            f"BB: {'Squeeze' if ref_tf.get('bb_squeeze') else 'Normal'} | VWAP: {ref_tf.get('vwap', 0):.4f}",
            f"Ichimoku: {cloud} nuage | Tenkan/Kijun cross: {'↑' if ichi.get('bullish_cross') else '↓'}",
            f"Williams %R: {ref_tf.get('williams_r', -50):.1f}",
            f"Structure: {ref_tf.get('struct', {}).get('trend', 'N/A')} | ATR: {atr:.4f} ({ref_tf.get('atr_pct', 0):.2f}%)",
            f"{div_str}",
            f"Fibonacci: niveau proche {ref_tf.get('fibonacci', {}).get('closest_level', 'N/A')}%",
        ]

        return {
            "agent":          self.name,
            "summary":        summary,
            "arguments":      arguments,
            "risks":          (
                ["RSI suracheté > 70"] if rsi_1h > 70 else
                (["RSI survendu < 30"] if rsi_1h < 30 else [])
            ),
            "confidence":     round(abs(composite_mtf - 0.5) * 2, 3),
            "recommendation": recommendation,
            "score":          round(composite_mtf, 4),
            "symbol_score":   round(composite_mtf, 4),
            "analysis": {
                "composite_mtf":  round(composite_mtf, 4),
                "tf_5m":          tf_5m,
                "tf_15m":         tf_15m,
                "tf_1h":          tf_1h,
                "tf_4h":          tf_4h,
                "confluence":     {"buy": buy_signals, "sell": sell_signals, "total": n_tfs},
                "price":          price,
                "stop_loss":      stop_loss,
                "take_profit":    take_profit,
                "rr_ratio":       rr_ratio,
            },
            "glossary_used": True,
        }
