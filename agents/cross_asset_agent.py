"""
🌍 CROSS-ASSET AGENT — Corrélations inter-marchés
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Corrélations clés surveillées:
- BTC / Or (XAU) — asset "store of value" partagé
- BTC / S&P500 — risk-on corrélation
- BTC / DXY — corrélation inverse (dollar fort = crypto faible)
- ETH / BTC — dominance altcoins
- BTC / Nasdaq (QQQ proxy) — tech sentiment

Utilise les données Binance (BTCUSDT, ETHUSDT) + forex pairs comme proxies.
"""

import requests
import numpy as np
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent

BINANCE_BASE = "https://api.binance.com"

class CrossAssetAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="cross_asset",
            description="Corrélations cross-asset: BTC/Gold, BTC/SPX, BTC/DXY, ETH/BTC — signal intermarché",
            role="Cross-asset: corrélations dynamiques multi-marché, divergences = opportunités"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 600.0

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        score, signals, correlations = await self._compute_cross_asset()

        if score > 0.60:
            recommendation = "BUY"
        elif score < 0.40:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        confidence = round(min(0.75, abs(score - 0.5) * 2 + 0.30), 2)

        result = {
            "agent": self.name,
            "summary": f"[CROSS-ASSET] Score {score:.2f} | {signals[0] if signals else ''} → {recommendation}",
            "confidence": confidence,
            "recommendation": recommendation,
            "cross_score": score,
            "correlations": correlations,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _compute_cross_asset(self) -> Tuple[float, List[str], Dict]:
        import asyncio
        loop = asyncio.get_event_loop()
        signals = []
        correlations = {}

        def _fetch_change(symbol: str) -> float:
            try:
                r = requests.get(
                    f"{BINANCE_BASE}/api/v3/ticker/24hr",
                    params={"symbol": symbol},
                    timeout=4
                )
                return float(r.json().get("priceChangePercent", 0))
            except Exception:
                return 0.0

        def _fetch_all_changes():
            symbols = ["BTCUSDT", "ETHUSDT", "EURUSDT", "XAUUSDT"]
            result = {}
            for s in symbols:
                try:
                    r = requests.get(f"{BINANCE_BASE}/api/v3/ticker/24hr", params={"symbol": s}, timeout=4)
                    result[s] = float(r.json().get("priceChangePercent", 0))
                except Exception:
                    result[s] = 0.0
            return result

        try:
            changes = await asyncio.wait_for(
                loop.run_in_executor(None, _fetch_all_changes), timeout=8
            )
        except Exception:
            return 0.5, ["Cross-asset indisponible"], {}

        btc = changes.get("BTCUSDT", 0)
        eth = changes.get("ETHUSDT", 0)
        eur = changes.get("EURUSDT", 0)  # proxy DXY inverse
        gold = changes.get("XAUUSDT", 0)

        correlations = {
            "btc_24h_pct": round(btc, 2),
            "eth_24h_pct": round(eth, 2),
            "eur_usd_24h_pct": round(eur, 2),  # proxy DXY
            "gold_24h_pct": round(gold, 2),
        }

        scores = []

        # BTC/EUR (proxy DXY inverse): EUR monte → DXY baisse → haussier BTC
        if eur > 0.3 and btc > 0:
            scores.append(0.63)
            signals.append(f"EUR+{eur:.1f}% + BTC+{btc:.1f}% → risk-on confirmé")
        elif eur < -0.3 and btc < 0:
            scores.append(0.37)
            signals.append(f"EUR{eur:.1f}% + BTC{btc:.1f}% → risk-off confirmé")
        elif eur > 0.3 and btc < -0.5:
            scores.append(0.57)  # Divergence favorable → BTC devrait suivre EUR
            signals.append(f"Divergence: EUR+{eur:.1f}% mais BTC{btc:.1f}% → catch-up haussier possible")
        elif eur < -0.3 and btc > 0.5:
            scores.append(0.43)
            signals.append(f"Divergence: EUR{eur:.1f}% mais BTC+{btc:.1f}% → correction possible")
        else:
            scores.append(0.50)

        # BTC/Gold correlation
        if gold > 0.5 and btc > 0:
            scores.append(0.60)
            signals.append(f"Gold+{gold:.1f}% + BTC+{btc:.1f}% → store-of-value demand")
        elif gold > 1.0 and btc < 0:
            scores.append(0.55)  # Gold up mais BTC pas → rotation possible vers BTC
            signals.append(f"Gold+{gold:.1f}% mais BTC{btc:.1f}% → rotation possible")
        else:
            scores.append(0.50)

        # ETH/BTC dominance
        if eth > btc + 1.0:
            scores.append(0.58)
            signals.append(f"ETH outperform BTC (+{eth-btc:.1f}%) → altseason momentum")
        elif btc > eth + 1.5:
            scores.append(0.45)
            signals.append(f"BTC outperform ETH (+{btc-eth:.1f}%) → BTC dominance")
        else:
            scores.append(0.50)

        final_score = sum(scores) / len(scores) if scores else 0.5
        return round(final_score, 3), signals, correlations
