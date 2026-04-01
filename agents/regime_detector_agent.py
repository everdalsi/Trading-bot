"""
🔍 REGIME DETECTOR AGENT — Détection régime marché (Trending/Ranging/Breakout)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Régimes identifiés:
1. TRENDING UP   → momentum fort → stratégie trend following
2. TRENDING DOWN → momentum baissier fort → short ou cash
3. RANGING       → oscillation → stratégie mean-reversion / grid
4. BREAKOUT      → sortie de range → entrée directionnelle forte
5. VOLATILE      → incertitude → réduction taille position

Méthodes:
- ADX (Average Directional Index) > 25 → trending
- Choppiness Index < 38.2 → trending, > 61.8 → ranging
- BB Width → compression = range, expansion = breakout
"""

import requests
import numpy as np
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent

BINANCE_BASE = "https://api.binance.com"

class RegimeDetectorAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="regime_detector",
            description="Détection régime marché: Trending/Ranging/Breakout via ADX, Choppiness Index, BB Width",
            role="Régime marché: ADX+Choppiness+BBWidth → stratégie adaptée au régime actuel"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 180.0

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        symbol = context.get("symbol", "BTCUSDT")
        regime, score, signals, metrics = await self._detect_regime(symbol)

        if score > 0.60:
            recommendation = "BUY"
        elif score < 0.40:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        confidence = round(min(0.85, abs(score - 0.5) * 2 + 0.38), 2)

        result = {
            "agent": self.name,
            "symbol": symbol,
            "summary": f"[REGIME] {symbol}: {regime} | ADX={metrics.get('adx',0):.1f} | "
                       f"Chop={metrics.get('choppiness',0):.1f} → {recommendation}",
            "confidence": confidence,
            "recommendation": recommendation,
            "regime": regime,
            "regime_score": score,
            "metrics": metrics,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _detect_regime(self, symbol: str) -> Tuple[str, float, List[str], Dict]:
        import asyncio
        loop = asyncio.get_event_loop()

        def _fetch_klines():
            try:
                r = requests.get(
                    f"{BINANCE_BASE}/api/v3/klines",
                    params={"symbol": symbol, "interval": "4h", "limit": 30},
                    timeout=5
                )
                data = r.json()
                h = np.array([float(k[2]) for k in data])
                l = np.array([float(k[3]) for k in data])
                c = np.array([float(k[4]) for k in data])
                return h, l, c
            except Exception:
                return np.array([]), np.array([]), np.array([])

        try:
            h, l, c = await asyncio.wait_for(loop.run_in_executor(None, _fetch_klines), timeout=6)
        except Exception:
            return "UNKNOWN", 0.5, [], {}

        if len(c) < 15:
            return "UNKNOWN", 0.5, ["Données insuffisantes"], {}

        # ADX calculation
        def calc_adx(period=14):
            tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
            plus_dm = np.where((h[1:] - h[:-1]) > (l[:-1] - l[1:]), np.maximum(h[1:] - h[:-1], 0), 0)
            minus_dm = np.where((l[:-1] - l[1:]) > (h[1:] - h[:-1]), np.maximum(l[:-1] - l[1:], 0), 0)
            atr = np.convolve(tr, np.ones(period)/period, mode='valid')[-1]
            plus_di = 100 * np.convolve(plus_dm, np.ones(period)/period, mode='valid')[-1] / (atr + 1e-8)
            minus_di = 100 * np.convolve(minus_dm, np.ones(period)/period, mode='valid')[-1] / (atr + 1e-8)
            dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-8)
            return float(dx), float(plus_di), float(minus_di)

        # Choppiness Index
        def calc_choppiness(period=14):
            if len(c) < period:
                return 50.0
            recent_h = h[-period:]
            recent_l = l[-period:]
            recent_c = c[-period:]
            tr = np.maximum(recent_h[1:] - recent_l[1:],
                            np.maximum(np.abs(recent_h[1:] - recent_c[:-1]),
                                       np.abs(recent_l[1:] - recent_c[:-1])))
            atr_sum = np.sum(tr)
            high_low = np.max(recent_h) - np.min(recent_l)
            if high_low == 0:
                return 50.0
            return float(100 * np.log10(atr_sum / high_low) / np.log10(period))

        # BB Width
        def calc_bb_width(period=20):
            if len(c) < period:
                return 0.0
            recent = c[-period:]
            mean = np.mean(recent)
            std = np.std(recent)
            return float((4 * std) / mean * 100)

        adx, plus_di, minus_di = calc_adx()
        chop = calc_choppiness()
        bb_width = calc_bb_width()

        metrics = {
            "adx": round(adx, 1),
            "plus_di": round(plus_di, 1),
            "minus_di": round(minus_di, 1),
            "choppiness": round(chop, 1),
            "bb_width": round(bb_width, 2),
        }

        signals = []

        # Régime determination
        if bb_width < 2.0 and chop > 55:
            regime = "RANGE COMPRESSION"
            score = 0.50
            signals.append(f"BB comprimé ({bb_width:.1f}%) + Chop={chop:.0f} → breakout imminent")
        elif adx > 30 and plus_di > minus_di:
            regime = "TRENDING UP"
            score = 0.68
            signals.append(f"Tendance haussière forte: ADX={adx:.0f}, +DI={plus_di:.0f} > -DI={minus_di:.0f}")
        elif adx > 30 and minus_di > plus_di:
            regime = "TRENDING DOWN"
            score = 0.32
            signals.append(f"Tendance baissière forte: ADX={adx:.0f}, -DI={minus_di:.0f} > +DI={plus_di:.0f}")
        elif chop < 38.2:
            regime = "TRENDING"
            score = 0.60 if plus_di > minus_di else 0.40
            signals.append(f"Marché directionnel (Chop={chop:.0f}<38.2)")
        elif chop > 61.8:
            regime = "RANGING"
            score = 0.50
            signals.append(f"Marché en range (Chop={chop:.0f}>61.8) → mean reversion")
        else:
            regime = "TRANSITIONAL"
            score = 0.50
            signals.append(f"Régime de transition: ADX={adx:.0f}, Chop={chop:.0f}")

        metrics["regime"] = regime
        return regime, round(score, 3), signals, metrics
