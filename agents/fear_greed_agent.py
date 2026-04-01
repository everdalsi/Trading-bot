"""
😱 FEAR & GREED AGENT — Index sentiment crypto global
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Source: alternative.me/crypto/fear-and-greed-index/
Stratégie contrarian classique:
- Extrême Peur (0-25)   → Opportunité d'achat (Warren Buffett)
- Peur (26-46)          → Léger biais haussier
- Neutre (47-53)        → HOLD
- Cupidité (54-75)      → Réduction de position
- Extrême Cupidité (76+) → Signal de vente fort

Aussi intègre le Google Trends BTC (si disponible) comme signal secondaire.
"""

import requests
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent
from logging_config import logger

FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=3"

class FearGreedAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="fear_greed",
            description="Crypto Fear & Greed Index — stratégie contrarian: achat en peur extrême, vente en cupidité",
            role="Sentiment macro: Fear & Greed Index [0-100], stratégie contrarian Warren Buffett"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 3600.0  # 1h (index mis à jour 1x/jour)

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        fg_value, fg_label, fg_yesterday, signals = await self._fetch_fear_greed()

        score, recommendation = self._score_from_fg(fg_value, fg_yesterday)
        confidence = round(min(0.85, abs(score - 0.5) * 2.2), 2)

        result = {
            "agent": self.name,
            "summary": f"[F&G] {fg_value}/100 — {fg_label} | Hier: {fg_yesterday} | → {recommendation}",
            "confidence": confidence,
            "recommendation": recommendation,
            "fg_value": fg_value,
            "fg_label": fg_label,
            "fg_yesterday": fg_yesterday,
            "fear_greed_score": score,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _fetch_fear_greed(self) -> Tuple[int, str, int, List[str]]:
        import asyncio
        loop = asyncio.get_event_loop()
        signals = []

        def _fetch():
            try:
                r = requests.get(FEAR_GREED_URL, timeout=5)
                return r.json()
            except Exception:
                return {}

        try:
            data = await asyncio.wait_for(loop.run_in_executor(None, _fetch), timeout=6)
            items = data.get("data", [])
            if not items:
                return 50, "Neutre", 50, ["F&G indisponible"]

            fg_today = int(items[0].get("value", 50))
            fg_yest = int(items[1].get("value", 50)) if len(items) > 1 else fg_today
            fg_label = items[0].get("value_classification", "Neutral")

            momentum = fg_today - fg_yest
            if momentum > 5:
                signals.append(f"F&G en hausse de {momentum} pts → momentum positif")
            elif momentum < -5:
                signals.append(f"F&G en baisse de {abs(momentum)} pts → détérioration sentiment")
            else:
                signals.append(f"F&G stable à {fg_today}")

            return fg_today, fg_label, fg_yest, signals
        except Exception as e:
            logger.warning(f"[F&G] Erreur: {e}")
            return 50, "Neutre", 50, ["F&G indisponible"]

    def _score_from_fg(self, fg: int, fg_yesterday: int) -> Tuple[float, str]:
        """Stratégie contrarian: peur → acheter, cupidité → vendre."""
        if fg <= 20:
            return 0.80, "BUY"    # Extrême peur — forte opportunité
        elif fg <= 35:
            return 0.65, "BUY"    # Peur — bon moment d'entrée
        elif fg <= 46:
            return 0.55, "BUY"    # Légère peur — légèrement haussier
        elif fg <= 54:
            return 0.50, "HOLD"   # Neutre
        elif fg <= 65:
            return 0.45, "HOLD"   # Légère cupidité — prudence
        elif fg <= 80:
            return 0.35, "SELL"   # Cupidité — réduire
        else:
            return 0.20, "SELL"   # Extrême cupidité — sortir
