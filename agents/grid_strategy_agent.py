"""
🔲 GRID STRATEGY AGENT — Stratégie de grid trading pour marchés en range
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Le grid trading capture la volatilité interne d'un range:
- Divise la range en N niveaux équidistants
- Place des ordres d'achat sous le prix et de vente au-dessus
- Profit sur chaque oscillation dans la grille

Paramètres calculés automatiquement:
- Range détection (Bollinger Bands + support/résistance)
- Grid levels (N = volatilité / 0.5%)
- Profit par grid = range_size / N - fees

Activé uniquement si le régime est RANGING (pas en trend fort).
"""

import requests
import numpy as np
import time
from typing import Dict, Any, Tuple, List, Optional
from agents.base_agent import BaseAgent

BINANCE_BASE = "https://api.binance.com"

class GridStrategyAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="grid_strategy",
            description="Grid trading: détection range + calcul grille optimale + profit/grid — régime ranging seulement",
            role="Grid strategy: setup grille de trading automatique quand marché en range avec paramètres optimaux"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 300.0

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        symbol = context.get("symbol", "BTCUSDT")
        score, signals, grid_params = await self._compute_grid_params(symbol)

        if grid_params.get("grid_active"):
            recommendation = "BUY"  # Grid strategy → placez vos ordres
        else:
            recommendation = "HOLD"

        confidence = round(min(0.75, abs(score - 0.5) * 2 + 0.30), 2)
        grid_str = (f"Grid: ${grid_params.get('grid_low', 0):,.0f}-${grid_params.get('grid_high', 0):,.0f} "
                    f"x{grid_params.get('num_grids', 0)} niveaux"
                    if grid_params.get("grid_active") else "Grid: marché non en range")

        result = {
            "agent": self.name,
            "symbol": symbol,
            "summary": f"[GRID] {symbol}: {grid_str} | Profit/grid={grid_params.get('profit_per_grid_pct', 0):.2f}%",
            "confidence": confidence,
            "recommendation": recommendation,
            "grid_score": score,
            "grid_params": grid_params,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _compute_grid_params(self, symbol: str) -> Tuple[float, List[str], Dict]:
        import asyncio
        loop = asyncio.get_event_loop()
        signals = []
        params = {"grid_active": False}

        def _fetch_klines():
            try:
                r = requests.get(
                    f"{BINANCE_BASE}/api/v3/klines",
                    params={"symbol": symbol, "interval": "1h", "limit": 48},
                    timeout=5,
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
            return 0.5, ["Grid: données indisponibles"], params

        if len(c) < 20:
            return 0.5, ["Grid: données insuffisantes"], params

        # BB Width pour détection range
        period = 20
        mean = np.mean(c[-period:])
        std = np.std(c[-period:])
        bb_upper = mean + 2 * std
        bb_lower = mean - 2 * std
        bb_width_pct = (bb_upper - bb_lower) / mean * 100

        # Choppiness Index
        tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
        atr_sum = np.sum(tr[-14:])
        hl_range = np.max(h[-14:]) - np.min(l[-14:])
        chop = 100 * np.log10(atr_sum / (hl_range + 1e-8)) / np.log10(14) if hl_range > 0 else 50

        current_price = float(c[-1])

        if chop > 55 and bb_width_pct < 8:
            # Marché en range → grid profitable
            grid_high = float(np.max(h[-24:]))
            grid_low = float(np.min(l[-24:]))
            range_pct = (grid_high - grid_low) / grid_low * 100

            num_grids = max(5, min(20, int(range_pct / 0.5)))
            profit_per_grid = range_pct / num_grids - 0.10  # 0.10% = fees estimés

            params = {
                "grid_active": True,
                "grid_high": round(grid_high, 2),
                "grid_low": round(grid_low, 2),
                "current_price": round(current_price, 2),
                "num_grids": num_grids,
                "range_pct": round(range_pct, 2),
                "profit_per_grid_pct": round(profit_per_grid, 3),
                "choppiness": round(chop, 1),
                "bb_width_pct": round(bb_width_pct, 1),
            }

            signals.append(
                f"GRID ACTIVÉ: Range ${grid_low:,.0f}-${grid_high:,.0f} ({range_pct:.1f}%) "
                f"| {num_grids} grilles | {profit_per_grid:.2f}%/grid"
            )
            score = 0.62  # Grid active → signal buy (place les ordres)
        else:
            params["grid_active"] = False
            params["choppiness"] = round(chop, 1)
            params["bb_width_pct"] = round(bb_width_pct, 1)
            params["current_price"] = round(current_price, 2)

            if chop < 38:
                signals.append(f"Marché trop directionnel (Chop={chop:.0f}) → grid non recommandé")
                score = 0.48
            else:
                signals.append(f"BB Width trop large ({bb_width_pct:.1f}%) → range trop volatile pour grid")
                score = 0.50

        return round(score, 3), signals, params
