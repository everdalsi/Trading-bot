"""
📰 NEWS EVENT AGENT V1.0 — Détection événements macro majeurs
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Rôle : Détecte les événements macro critiques (Fed, CPI, ETF, hack exchange)
       via NewsAPI + RSS et impose une pause trading 30min avant/après.
Priorité : HAUTE — Évite les liquidations sur annonces macro.
"""

import os
import time
import asyncio
import feedparser
import requests
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from agents.base_agent import BaseAgent
from logging_config import logger

# Mots-clés d'événements macro CRITIQUES → pause obligatoire
MACRO_CRITICAL_KEYWORDS = [
    # Banques centrales
    "federal reserve", "fed rate", "fomc", "powell", "interest rate decision",
    "rate hike", "rate cut", "bce", "ecb rate", "bank of japan",
    # Données économiques
    "cpi", "inflation", "pce", "nonfarm payroll", "unemployment rate",
    "gdp", "retail sales",
    # Crypto spécifique
    "etf approval", "etf rejected", "sec crypto", "bitcoin etf",
    "exchange hack", "exchange down", "ftx", "binance ban",
    "usdt depeg", "usdc depeg", "stablecoin",
    # Systémique
    "bank collapse", "contagion", "liquidity crisis", "market halt",
    "circuit breaker", "flash crash",
]

# Flux RSS cryptos fiables
RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cryptonews.com/news/feed/",
]

# Cooldown après détection d'un événement (secondes)
PRE_EVENT_PAUSE  = 30 * 60   # 30 min avant
POST_EVENT_PAUSE = 30 * 60   # 30 min après


class NewsEventAgent(BaseAgent):
    """
    Surveille le flux d'actualités en temps réel et impose un veto de trading
    lors de la détection d'événements macro/crypto critiques.
    """

    def __init__(self):
        super().__init__(
            name="news_event",
            role=(
                "Surveillance événements macro-critiques (Fed, CPI, ETF, hack) "
                "— veto trading 30min avant/après une annonce majeure"
            )
        )
        self._last_check: float = 0.0
        self._cache_ttl: float  = 300.0          # Re-fetch toutes les 5 min
        self._detected_events: List[Dict]  = []
        self._pause_until: Optional[float] = None
        self._newsapi_key: str = os.getenv("NEWS_API_KEY", "")

    # ── Domaine ────────────────────────────────────────────────────────────
    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in [
            "news", "event", "actualité", "annonce", "fed", "cpi",
            "macro", "hack", "etf", "pause", "news_event",
        ]) or super()._is_in_my_domain(question)

    # ── Fetch RSS ───────────────────────────────────────────────────────────
    def _fetch_rss_headlines(self) -> List[str]:
        headlines = []
        for url in RSS_FEEDS:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:10]:
                    headlines.append(entry.get("title", "").lower())
            except Exception as e:
                logger.warning(f"[NEWS_EVENT] RSS fetch error ({url}): {e}")
        return headlines

    # ── Fetch NewsAPI ───────────────────────────────────────────────────────
    def _fetch_newsapi_headlines(self) -> List[str]:
        if not self._newsapi_key:
            return []
        try:
            url = (
                "https://newsapi.org/v2/everything"
                "?q=bitcoin+OR+crypto+OR+federal+reserve+OR+CPI"
                "&language=en&sortBy=publishedAt&pageSize=20"
                f"&apiKey={self._newsapi_key}"
            )
            resp = requests.get(url, timeout=8)
            if resp.status_code == 200:
                articles = resp.json().get("articles", [])
                return [a.get("title", "").lower() for a in articles]
        except Exception as e:
            logger.warning(f"[NEWS_EVENT] NewsAPI error: {e}")
        return []

    # ── Analyse headlines ───────────────────────────────────────────────────
    def _detect_critical_events(self, headlines: List[str]) -> List[str]:
        found = []
        for headline in headlines:
            for kw in MACRO_CRITICAL_KEYWORDS:
                if kw in headline:
                    found.append(headline[:120])
                    break
        return found

    # ── Check si on est en période de pause ────────────────────────────────
    def _is_paused(self) -> bool:
        if self._pause_until and time.time() < self._pause_until:
            return True
        return False

    def _remaining_pause(self) -> int:
        if self._pause_until:
            remaining = self._pause_until - time.time()
            return max(0, int(remaining // 60))
        return 0

    # ── Refresh cache ───────────────────────────────────────────────────────
    def _refresh_if_needed(self) -> None:
        now = time.time()
        if now - self._last_check < self._cache_ttl:
            return
        self._last_check = now

        headlines = self._fetch_rss_headlines() + self._fetch_newsapi_headlines()
        critical  = self._detect_critical_events(headlines)

        if critical:
            self._detected_events = critical[:5]
            # Si pas déjà en pause, déclencher la pause
            if not self._is_paused():
                self._pause_until = now + POST_EVENT_PAUSE
                logger.warning(
                    f"[NEWS_EVENT] 🚨 Événement critique détecté → pause {POST_EVENT_PAUSE//60}min | "
                    f"{critical[0][:80]}"
                )
        else:
            self._detected_events = []

    # ── Respond ─────────────────────────────────────────────────────────────
    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        # Refresh asynchrone (thread-friendly)
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._refresh_if_needed)
        except Exception as e:
            logger.warning(f"[NEWS_EVENT] Refresh error: {e}")

        paused   = self._is_paused()
        events   = self._detected_events
        rem_min  = self._remaining_pause()

        if paused:
            return {
                "agent":          self.name,
                "summary":        (
                    f"🚨 PAUSE ÉVÉNEMENT MACRO — {rem_min}min restantes\n"
                    f"Événement: {events[0][:80] if events else 'Annonce critique'}"
                ),
                "arguments":      [f"Événement détecté: {e[:60]}" for e in events[:3]],
                "risks":          ["Annonce macro majeure — liquidation possible"],
                "confidence":     1.0,
                "recommendation": f"NO TRADE — Pause {rem_min}min (événement macro critique)",
                "veto":           True,
                "veto_reason":    "macro_news_event",
                "pause_minutes":  rem_min,
                "events":         events,
            }

        if events:
            return {
                "agent":          self.name,
                "summary":        f"⚠️ News sensibles détectées — trading prudent | {events[0][:60]}",
                "arguments":      ["Actualités macro actives — réduire taille positions"],
                "risks":          ["Volatilité accrue sur news"],
                "confidence":     0.7,
                "recommendation": "TRADE RÉDUIT — réduire taille de 40%",
                "veto":           False,
                "size_reduction": 0.40,
                "events":         events,
            }

        return {
            "agent":          self.name,
            "summary":        "✅ Aucun événement macro critique détecté — trading libre",
            "arguments":      ["Flux RSS + NewsAPI : aucune annonce critique"],
            "risks":          [],
            "confidence":     0.9,
            "recommendation": "TRADE AUTORISÉ",
            "veto":           False,
            "events":         [],
        }

    # ── API publique pour bot.py ────────────────────────────────────────────
    def is_trading_paused(self) -> bool:
        """Appelable directement depuis bot.py pour veto instantané."""
        self._refresh_if_needed()
        return self._is_paused()

    def get_status(self) -> dict:
        self._refresh_if_needed()
        return {
            "paused":        self._is_paused(),
            "pause_minutes": self._remaining_pause(),
            "events":        self._detected_events,
        }
