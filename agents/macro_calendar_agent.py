"""
📅 MACRO CALENDAR AGENT — Impact des événements macroéconomiques
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Surveille et score les événements macro clés:
- FOMC (Fed meetings) — impact très élevé
- CPI / PPI — inflation — impact élevé
- NFP (Non Farm Payrolls) — emploi US — impact élevé
- PIB / GDP — impact modéré
- PMI / ISM — activité économique

Stratégie: réduction de position AVANT les events à fort impact,
rétablissement POST-event selon la surprise vs consensus.

Source: Calendrier fixe connu + NewsAPI pour détection automatique
"""

import requests
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple
from agents.base_agent import BaseAgent
from logging_config import logger

# Mots-clés événements macro dans les news
HIGH_IMPACT_KEYWORDS = ["fomc", "federal reserve", "rate decision", "cpi", "inflation report",
                        "non-farm payroll", "nfp", "jobs report", "gdp", "pce"]
MEDIUM_IMPACT_KEYWORDS = ["pmi", "ism", "retail sales", "ppi", "jobless claims",
                          "consumer confidence", "housing starts"]

class MacroCalendarAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="macro_calendar",
            description="Calendrier macro: FOMC, CPI, NFP — réduction expo avant events, repositionnement post-surprise",
            role="Macro calendar: détection events US à fort impact + scoring surprise vs consensus"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 1800.0  # 30 min
        self._newsapi_key = None
        import os
        self._newsapi_key = os.getenv("NEWS_API_KEY") or os.getenv("NEWSAPI_KEY")

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        score, signals, events = await self._analyze_macro_calendar()

        if score > 0.60:
            recommendation = "BUY"
        elif score < 0.40:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        confidence = round(min(0.75, abs(score - 0.5) * 2 + 0.30), 2)
        event_str = events[0] if events else "Aucun event majeur imminent"

        result = {
            "agent": self.name,
            "summary": f"[MACRO CAL] {event_str} → {recommendation}",
            "confidence": confidence,
            "recommendation": recommendation,
            "calendar_score": score,
            "upcoming_events": events,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _analyze_macro_calendar(self) -> Tuple[float, List[str], List[str]]:
        import asyncio
        loop = asyncio.get_event_loop()
        signals = []
        events = []
        scores = []

        # Vérifier si on est dans une période sensible (lundi/mercredi/vendredi)
        now = datetime.now(timezone.utc)
        weekday = now.weekday()  # 0=Lundi, 4=Vendredi
        hour = now.hour

        # FOMC généralement le mercredi à 18h UTC
        if weekday == 2 and 16 <= hour <= 20:
            events.append("FOMC meeting probable (mercredi 18h UTC)")
            scores.append(0.45)  # Prudence avant FOMC
            signals.append("FOMC window: réduire exposition 30%")

        # NFP premier vendredi du mois à 12:30 UTC
        elif weekday == 4 and 12 <= hour <= 14 and now.day <= 7:
            events.append("NFP probable (premier vendredi)")
            scores.append(0.45)
            signals.append("NFP window: réduire exposition 20%")

        # CPI généralement 2e semaine du mois
        elif 8 <= now.day <= 12 and weekday == 1 and 12 <= hour <= 14:
            events.append("CPI report possible (mardi 2e semaine)")
            scores.append(0.45)
            signals.append("CPI window: prudence 15%")

        # Scan NewsAPI si disponible
        if self._newsapi_key:
            def _fetch_news():
                try:
                    from newsapi import NewsApiClient
                    client = NewsApiClient(api_key=self._newsapi_key)
                    articles = client.get_top_headlines(q="federal reserve inflation", language="en", page_size=5)
                    return articles.get("articles", [])
                except Exception:
                    return []

            try:
                articles = await asyncio.wait_for(
                    loop.run_in_executor(None, _fetch_news), timeout=6
                )
                for art in articles:
                    title = (art.get("title", "") or "").lower()
                    if any(kw in title for kw in HIGH_IMPACT_KEYWORDS):
                        events.append(f"NEWS: {art.get('title', '')[:60]}")
                        scores.append(0.45)
                        signals.append(f"Event macro détecté: {art.get('title', '')[:60]}")
                        break
            except Exception:
                pass

        if not scores:
            scores.append(0.55)  # Pas d'event → légèrement haussier (période calme)
            signals.append("Aucun event macro majeur identifié — fenêtre favorable")
            events.append("Calendrier calme")

        final_score = sum(scores) / len(scores)
        return round(final_score, 3), signals, events
