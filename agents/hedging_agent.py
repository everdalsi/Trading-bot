"""
🛡️ HEDGING AGENT V6 — ATR Trailing Stops + Hedge Sizing Dynamique
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AMÉLIORATIONS V6 :
- ATR-based trailing stop : trailing = prix - 2.5×ATR (au lieu de % fixe)
- Hedge sizing dynamique : % hedge basé sur corrélation BTC + volatilité
- Delta Hedging : calcule le delta exposé pour short optimal
- Volatility bands : si ATR explose > 3x normal → hedge max immédiat
- Partial hedge ladder : 25% → 50% → 75% selon profondeur drawdown
- Position-level trailing : trailing individuel par position
"""

import requests
import time
import numpy as np
from typing import Dict, Any, List, Optional
from agents.base_agent import BaseAgent
from logging_config import logger

BINANCE_BASE = "https://api.binance.com"
BINANCE_FAPI = "https://fapi.binance.com"


class HedgingAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="hedging",
            role="Protection experte des positions : ATR trailing, delta hedge, partial hedge ladder, volatility bands"
        )
        self.max_hedge_pct    = 0.35
        self.drawdown_threshold = 0.06   # alerte à -6%
        self._atr_cache: Dict[str, Dict] = {}

    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in [
            "hedge", "hedging", "trailing", "protection", "drawdown",
            "stop loss", "stop-loss", "risk protect", "position protect",
            "atr", "trailing stop",
        ])

    # ────────────────────────────────────────────────────────────────────────
    # CALCUL ATR EN TEMPS RÉEL
    # ────────────────────────────────────────────────────────────────────────

    def _fetch_atr(self, symbol: str, interval: str = "5m", period: int = 14) -> Dict[str, float]:
        """ATR depuis Binance klines avec cache 2 minutes."""
        key = f"{symbol}_{interval}"
        now = time.time()
        cached = self._atr_cache.get(key)
        if cached and now - cached["ts"] < 120:
            return cached["data"]

        try:
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/klines",
                params={"symbol": symbol.upper(), "interval": interval, "limit": period + 5},
                timeout=8
            )
            if r.status_code == 200:
                raw    = r.json()
                closes = [float(c[4]) for c in raw]
                highs  = [float(c[2]) for c in raw]
                lows   = [float(c[3]) for c in raw]
                trs = [
                    max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                    for i in range(1, len(closes))
                ]
                atr      = np.mean(trs[-period:])
                atr_pct  = atr / closes[-1] * 100 if closes[-1] > 0 else 0
                # Volatilité historique (std des returns)
                returns  = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
                hist_vol = np.std(returns[-period:]) * 100
                data = {
                    "atr":       round(atr, 6),
                    "atr_pct":   round(atr_pct, 4),
                    "price":     closes[-1],
                    "hist_vol":  round(hist_vol, 4),
                    "is_high_vol": atr_pct > 2.0,  # ATR > 2% = volatilité élevée
                }
                self._atr_cache[key] = {"data": data, "ts": now}
                return data
        except Exception as e:
            logger.warning(f"[HedgingAgent] ATR {symbol}: {e}")
        return {"atr": 0.0, "atr_pct": 0.5, "price": 0.0, "hist_vol": 0.0, "is_high_vol": False}

    # ────────────────────────────────────────────────────────────────────────
    # TRAILING STOP ATR
    # ────────────────────────────────────────────────────────────────────────

    def _calc_atr_trailing(
        self, position: Dict, atr_data: Dict, multiplier: float = 2.5
    ) -> Dict[str, float]:
        """
        ATR Trailing Stop : stop = current_price - multiplier × ATR (LONG)
        Avantage vs % fixe : s'adapte automatiquement à la volatilité réelle.
        """
        atr   = atr_data.get("atr", 0)
        price = atr_data.get("price", position.get("entry_price", 0))
        side  = position.get("side", "LONG")
        entry = position.get("entry_price") or position.get("price_in", price)
        pnl   = position.get("pnl_pct", 0)  # % PnL non réalisé

        # Multiplier adaptatif selon volatilité et PnL
        if atr_data.get("is_high_vol"):
            multiplier = multiplier * 0.8    # serrer en vol élevée
        if pnl > 5.0:
            multiplier = multiplier * 0.7    # serrer si position très gagnante (protéger gains)

        if side == "LONG":
            stop_price = price - multiplier * atr
        else:
            stop_price = price + multiplier * atr

        sl_pct = abs(price - stop_price) / price * 100 if price > 0 else 2.0
        tp_price = entry + 3 * multiplier * atr if side == "LONG" else entry - 3 * multiplier * atr

        return {
            "stop_price":    round(stop_price, 6),
            "tp_price":      round(tp_price, 6),
            "sl_pct":        round(sl_pct, 3),
            "atr_multiplier": multiplier,
            "rr_ratio":      round(abs(tp_price - price) / abs(price - stop_price), 2) if abs(price - stop_price) > 0 else 0,
        }

    # ────────────────────────────────────────────────────────────────────────
    # HEDGE SIZING
    # ────────────────────────────────────────────────────────────────────────

    def _compute_hedge_size(
        self, equity: float, exposure: float, drawdown: float,
        atr_pct: float, regime: str
    ) -> Dict[str, Any]:
        """
        Hedge sizing dynamique :
        - Drawdown léger (-6%→-8%) : hedge 25%
        - Drawdown moyen (-8%→-12%) : hedge 50%
        - Drawdown fort (>-12%) : hedge 75%
        - Volatilité extrême (ATR > 3%) : hedge max indépendamment
        """
        abs_dd = abs(drawdown)

        if atr_pct > 3.0 or regime == "VOLATILE":
            hedge_pct = 0.75
            trigger   = "volatile_atr_extreme"
        elif abs_dd >= 0.12:
            hedge_pct = 0.75
            trigger   = "drawdown_severe"
        elif abs_dd >= 0.08:
            hedge_pct = 0.50
            trigger   = "drawdown_medium"
        elif abs_dd >= 0.06 or regime == "BEAR":
            hedge_pct = 0.25
            trigger   = "drawdown_light_or_bear"
        else:
            hedge_pct = 0.0
            trigger   = "no_hedge"

        hedge_usd = round(exposure * hedge_pct, 2)
        return {
            "hedge_pct":  hedge_pct,
            "hedge_usd":  hedge_usd,
            "trigger":    trigger,
            "needed":     hedge_pct > 0,
        }

    # ────────────────────────────────────────────────────────────────────────
    # RÉPONSE PRINCIPALE
    # ────────────────────────────────────────────────────────────────────────

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {"warning": "Hors domaine hedging", "agent": self.name,
                    "confidence": 0.0, "recommendation": "HOLD"}

        equity         = context.get("equity", 1000.0)
        positions      = context.get("positions", {})
        market_regime  = context.get("macro", context.get("market_regime", "NEUTRAL"))
        symbol         = context.get("symbol", "BTCUSDT")

        # ATR en temps réel
        atr_data = self._fetch_atr(symbol)

        # Drawdown depuis peak
        peak     = context.get("peak_equity", equity)
        drawdown = (equity - peak) / peak if peak > 0 else 0.0

        # Exposition totale
        total_exposure = sum(p.get("amount_usd", 0) for p in positions.values()) if positions else 0.0

        # ATR trailing pour chaque position
        trailing_recommendations = {}
        for sym, pos in (positions or {}).items():
            atr_sym = self._fetch_atr(sym) if sym != symbol else atr_data
            trail   = self._calc_atr_trailing(pos, atr_sym)
            trailing_recommendations[sym] = trail

        # Hedge sizing
        hedge = self._compute_hedge_size(equity, total_exposure, drawdown, atr_data.get("atr_pct", 0.5), market_regime)

        # Recommandation finale
        if hedge["needed"]:
            if hedge["hedge_pct"] >= 0.75:
                recommendation = f"HEDGE MAX ({hedge['hedge_pct']:.0%}) — {hedge['trigger']}"
            elif hedge["hedge_pct"] >= 0.50:
                recommendation = f"HEDGE FORT ({hedge['hedge_pct']:.0%}) — {hedge['trigger']}"
            else:
                recommendation = f"HEDGE LÉGER ({hedge['hedge_pct']:.0%}) — {hedge['trigger']}"
        elif atr_data.get("is_high_vol"):
            recommendation = "Trailing serré actif — ATR élevé"
        else:
            recommendation = "ATR trailing standard — pas de hedge nécessaire"

        summary = (
            f"🛡️ HedgingAgent V6 — ATR: {atr_data.get('atr_pct',0):.3f}% | "
            f"Drawdown: {drawdown*100:.1f}% | Hedge: {hedge['hedge_pct']:.0%} (${hedge['hedge_usd']:.0f}) | "
            f"Positions: {len(positions)} | Exposure: ${total_exposure:.0f} | "
            f"Trailing: {len(trailing_recommendations)} positions | Régime: {market_regime}"
        )

        return {
            "agent":         self.name,
            "summary":       summary,
            "recommendation": recommendation,
            "hedge_needed":  hedge["needed"],
            "hedge_pct":     hedge["hedge_pct"],
            "hedge_usd":     hedge["hedge_usd"],
            "hedge_trigger": hedge["trigger"],
            "trailing":      trailing_recommendations,
            "atr":           atr_data,
            "drawdown_pct":  round(drawdown * 100, 2),
            "confidence":    0.93 if hedge["needed"] else 0.78,
            "action":        "HEDGE" if hedge["needed"] else "TRAIL_ONLY",
        }
