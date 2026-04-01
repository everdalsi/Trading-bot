"""
⚖️ REGULATORY MONITOR AGENT — Surveillance réglementaire crypto
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
La réglementation est le seul risque exogène qui peut faire -30% en 24h.
Surveille:
- Actualités SEC (ETF, XRP, enforcement actions)
- Décisions CFTC / Fed / Treasury
- Réglementation EU (MiCA compliance)
- Bans/restrictions Chine, Inde, Russie
- Actualités positives: approbation ETF, légalisation, adoption état

Stratégie:
- News réglementaire positive → renforcement position
- News réglementaire négative → réduction urgente + stop-loss serré
"""

import requests
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent
from logging_config import logger
import os

FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"

REGULATORY_NEGATIVE_KEYWORDS = [
    "sec lawsuit", "sec charges", "ban crypto", "crypto ban", "illegal",
    "crackdown", "enforcement action", "exchange shutdown", "freeze assets",
    "money laundering", "terrorist financing", "cftc charges", "restricted",
    "prohibited", "sanctions", "treasury action"
]

REGULATORY_POSITIVE_KEYWORDS = [
    "etf approved", "etf approval", "spot bitcoin etf", "legal tender",
    "regulation clarity", "crypto legal", "sec approved", "institutional adoption",
    "central bank buy", "sovereign fund", "strategic reserve", "mica approved",
    "crypto friendly", "regulated exchange launch"
]

class RegulatoryMonitorAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="regulatory_monitor",
            description="Surveillance réglementaire: SEC, CFTC, MiCA, bans — détection risque réglementaire",
            role="Regulatory: scan news réglementaire crypto, alerte urgente si enforcement/ban, boost si ETF/adoption"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 1800.0  # 30 min
        self._newsapi_key = os.getenv("NEWS_API_KEY") or os.getenv("NEWSAPI_KEY")

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        score, signals, alerts = await self._monitor_regulatory()

        if score > 0.60:
            recommendation = "BUY"
        elif score < 0.35:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        alert_level = "HIGH" if score < 0.35 else ("LOW" if score > 0.65 else "MEDIUM")
        confidence = round(min(0.88, abs(score - 0.5) * 2 + 0.40), 2)

        result = {
            "agent": self.name,
            "summary": f"[REGULATORY] Alert={alert_level} | {signals[0] if signals else 'Pas d alerte réglementaire'} → {recommendation}",
            "confidence": confidence,
            "recommendation": recommendation,
            "reg_score": score,
            "alert_level": alert_level,
            "alerts": alerts,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _monitor_regulatory(self) -> Tuple[float, List[str], List[str]]:
        import asyncio
        loop = asyncio.get_event_loop()
        signals = []
        alerts = []
        scores = [0.55]  # Default: environnement neutre légèrement favorable

        if not self._newsapi_key:
            # Fallback: scan RSS crypto sans clé
            def _fetch_rss():
                try:
                    r = requests.get(
                        "https://feeds.feedburner.com/CoinDesk",
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=5
                    )
                    text = r.text.lower()
                    neg_hits = sum(1 for kw in REGULATORY_NEGATIVE_KEYWORDS if kw in text)
                    pos_hits = sum(1 for kw in REGULATORY_POSITIVE_KEYWORDS if kw in text)
                    return neg_hits, pos_hits
                except Exception:
                    return 0, 0

            try:
                neg, pos = await asyncio.wait_for(
                    loop.run_in_executor(None, _fetch_rss), timeout=6
                )
                if neg > 2:
                    scores.append(0.20)
                    alerts.append(f"Mots-clés réglementaires négatifs détectés ({neg})")
                    signals.append(f"ALERTE RÉGLEMENTAIRE: {neg} signaux négatifs dans l'actu crypto")
                elif pos > 2:
                    scores.append(0.75)
                    alerts.append(f"News réglementaires positives ({pos})")
                    signals.append(f"Actualité réglementaire favorable: {pos} signaux positifs")
                else:
                    signals.append("Environnement réglementaire calme")
            except Exception:
                signals.append("Scan réglementaire: timeout — signal neutre")
        else:
            # NewsAPI disponible
            def _fetch_newsapi():
                try:
                    r = requests.get(
                        "https://newsapi.org/v2/everything",
                        params={
                            "q": "crypto regulation SEC CFTC bitcoin",
                            "language": "en",
                            "sortBy": "publishedAt",
                            "pageSize": 10,
                            "apiKey": self._newsapi_key,
                        },
                        timeout=6
                    )
                    return r.json().get("articles", [])
                except Exception:
                    return []

            try:
                articles = await asyncio.wait_for(
                    loop.run_in_executor(None, _fetch_newsapi), timeout=7
                )
                neg_count = 0
                pos_count = 0
                for art in articles:
                    title = (art.get("title", "") or "").lower()
                    desc = (art.get("description", "") or "").lower()
                    combined = title + " " + desc
                    if any(kw in combined for kw in REGULATORY_NEGATIVE_KEYWORDS):
                        neg_count += 1
                        alerts.append(art.get("title", "")[:80])
                    elif any(kw in combined for kw in REGULATORY_POSITIVE_KEYWORDS):
                        pos_count += 1

                if neg_count >= 2:
                    scores.append(0.18)
                    signals.append(f"ALERTE: {neg_count} articles réglementaires négatifs → réduction exposition urgente")
                elif pos_count >= 2:
                    scores.append(0.75)
                    signals.append(f"News positives: {pos_count} articles réglementaires favorables")
                else:
                    signals.append("Régulation: environnement neutre")
            except Exception:
                signals.append("NewsAPI timeout — signal neutre")

        final_score = sum(scores) / len(scores) if scores else 0.5
        return round(final_score, 3), signals, alerts
