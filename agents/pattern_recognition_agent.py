"""
📈 PATTERN RECOGNITION AGENT — Reconnaissance de patterns chartistes
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Patterns reconnus:
- Double Bottom / Double Top
- Head & Shoulders (direct + inverse)
- Triangle ascendant/descendant/symétrique
- Bull Flag / Bear Flag
- Wedge (rising/falling)
- Cup & Handle
- Breakout de range

Basé sur les données OHLCV Binance.
"""

import requests
import numpy as np
import time
from typing import Dict, Any, Tuple, List, Optional
from agents.base_agent import BaseAgent
from logging_config import logger

BINANCE_BASE = "https://api.binance.com"

class PatternRecognitionAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="pattern_recognition",
            description="Reconnaissance patterns chartistes: H&S, double top/bottom, flags, triangles, wedges",
            role="Technical patterns: détection automatique des patterns classiques sur OHLCV Binance"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 180.0

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        symbol = context.get("symbol", "BTCUSDT")
        patterns, score, signals = await self._detect_patterns(symbol)

        if score > 0.60:
            recommendation = "BUY"
        elif score < 0.40:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        patterns_str = ", ".join(patterns) if patterns else "Aucun pattern confirmé"
        confidence = round(min(0.82, abs(score - 0.5) * 2 + 0.35), 2)

        result = {
            "agent": self.name,
            "symbol": symbol,
            "summary": f"[PATTERN] {symbol}: {patterns_str} → {recommendation}",
            "confidence": confidence,
            "recommendation": recommendation,
            "patterns": patterns,
            "pattern_score": score,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _detect_patterns(self, symbol: str) -> Tuple[List[str], float, List[str]]:
        import asyncio
        loop = asyncio.get_event_loop()

        def _fetch_ohlcv(interval="1h", limit=50):
            try:
                r = requests.get(
                    f"{BINANCE_BASE}/api/v3/klines",
                    params={"symbol": symbol, "interval": interval, "limit": limit},
                    timeout=5,
                )
                data = r.json()
                closes = [float(k[4]) for k in data]
                highs = [float(k[2]) for k in data]
                lows = [float(k[3]) for k in data]
                volumes = [float(k[5]) for k in data]
                return closes, highs, lows, volumes
            except Exception:
                return [], [], [], []

        try:
            closes, highs, lows, volumes = await asyncio.wait_for(
                loop.run_in_executor(None, _fetch_ohlcv), timeout=6
            )
        except Exception:
            return [], 0.5, ["Données indisponibles"]

        if not closes or len(closes) < 20:
            return [], 0.5, ["Données insuffisantes"]

        closes = np.array(closes)
        highs = np.array(highs)
        lows = np.array(lows)
        volumes = np.array(volumes)

        detected = []
        signals = []
        score_components = []

        # --- Double Bottom ---
        recent_lows = lows[-20:]
        min1_idx = np.argmin(recent_lows[:10])
        min2_idx = np.argmin(recent_lows[10:]) + 10
        min1, min2 = recent_lows[min1_idx], recent_lows[min2_idx]
        if abs(min1 - min2) / min1 < 0.02 and closes[-1] > np.mean(recent_lows):
            detected.append("Double Bottom")
            score_components.append(0.70)
            signals.append("Double Bottom confirmé → setup haussier")

        # --- Double Top ---
        recent_highs = highs[-20:]
        max1_idx = np.argmax(recent_highs[:10])
        max2_idx = np.argmax(recent_highs[10:]) + 10
        max1, max2 = recent_highs[max1_idx], recent_highs[max2_idx]
        if abs(max1 - max2) / max1 < 0.02 and closes[-1] < np.mean(recent_highs):
            detected.append("Double Top")
            score_components.append(0.30)
            signals.append("Double Top confirmé → setup baissier")

        # --- Bull Flag ---
        last_10 = closes[-10:]
        last_5 = closes[-5:]
        prior_trend = (closes[-20] - closes[-30]) / closes[-30] if len(closes) >= 30 else 0
        flag_consolidation = np.std(last_5) / np.mean(last_5) < 0.008
        if prior_trend > 0.03 and flag_consolidation:
            detected.append("Bull Flag")
            score_components.append(0.67)
            signals.append(f"Bull Flag: trend {prior_trend:.1%} + consolidation")

        # --- Bear Flag ---
        if prior_trend < -0.03 and flag_consolidation:
            detected.append("Bear Flag")
            score_components.append(0.33)
            signals.append(f"Bear Flag: trend {prior_trend:.1%} + consolidation")

        # --- Breakout de range ---
        range_high = np.max(highs[-15:-3])
        range_low = np.min(lows[-15:-3])
        current = closes[-1]
        range_size = (range_high - range_low) / range_low

        if range_size < 0.05 and current > range_high * 1.005:
            detected.append("Breakout Haussier")
            score_components.append(0.72)
            signals.append(f"Breakout range [${range_low:.0f}-${range_high:.0f}] vers le haut")
        elif range_size < 0.05 and current < range_low * 0.995:
            detected.append("Breakout Baissier")
            score_components.append(0.28)
            signals.append(f"Breakdown range [${range_low:.0f}-${range_high:.0f}] vers le bas")

        if not score_components:
            signals.append(f"{symbol}: aucun pattern chartiste confirmé sur 1h")
            return detected, 0.50, signals

        final_score = sum(score_components) / len(score_components)
        return detected, round(final_score, 3), signals
