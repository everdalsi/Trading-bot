"""
🔮 QUANTUM RISK AGENT — Surveillance de la menace quantique sur les crypto
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Contexte (Mars 2026) :
- Google Willow: estimation crack ECDSA secp256k1 en ~9 min via Shor's algorithm
- 1/3 des wallets BTC, 20.5M ETH d'user funds vulnérables (ECDSA)
- Mempool attacks théoriquement réalisables si un attaquant CRQC existe
- Migration post-quantique (PQC) urgente — deadline Google révisée à 2029

Rôle de cet agent :
- Surveiller les nouvelles liées à la menace quantique via flux RSS / NewsAPI
- Calculer un "threat level" entre 0 et 1 selon l'intensité des signaux
- Émettre un signal de risque macro (REDUCE / MONITOR / NORMAL) à l'orchestrateur
- Ajuster les poids de risque sur les assets ECDSA-based (BTC, ETH non-migré, SOL)

Intégration :
- L'orchestrateur intègre ce signal dans le score global (_compute_global_score)
- En cas de menace ELEVEE (>0.7), le RiskAgent peut déclencher un veto automatique
  sur les trades à fort effet de levier sur BTC/ETH
"""

import asyncio
import time
import os
import json
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta

try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False

try:
    from newsapi import NewsApiClient
    HAS_NEWSAPI = True
except ImportError:
    HAS_NEWSAPI = False

try:
    from agents.base_agent import BaseAgent
    from logging_config import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

    class BaseAgent:
        def __init__(self, name="quantum_risk", description="", role=""):
            self.name = name
            self.description = description
            self.role = role

        async def safe_respond(self, question, context):
            return await self.respond(question, context)

        def _is_in_my_domain(self, question):
            return True

        async def respond(self, question, context):
            raise NotImplementedError


# ── Assets vulnérables à la menace quantique ─────────────────────────────────
QUANTUM_VULNERABLE_ASSETS = {
    "BTC":  0.95,   # ECDSA secp256k1 — très exposé
    "ETH":  0.80,   # ECDSA + smart contracts
    "SOL":  0.70,   # Ed25519 — légèrement plus résistant
    "BNB":  0.75,   # ECDSA
    "XRP":  0.65,
    "AVAX": 0.70,
    "LINK": 0.75,
}

# ── Seuils de menace ──────────────────────────────────────────────────────────
THREAT_THRESHOLDS = {
    "NORMAL":   0.0,
    "MONITOR":  0.25,
    "ELEVATED": 0.50,
    "HIGH":     0.70,
    "CRITICAL": 0.85,
}

# ── Mots-clés de surveillance ─────────────────────────────────────────────────
QUANTUM_KEYWORDS = [
    "quantum computer", "quantum computing", "post-quantum", "pqc",
    "shor algorithm", "grover algorithm", "ecdsa", "elliptic curve",
    "quantum threat", "quantum attack", "willow", "crqc",
    "cryptography broken", "crypto vulnerability", "quantum supremacy",
    "bitcoin quantum", "ethereum quantum", "blockchain quantum",
    "ordinateur quantique", "menace quantique", "cryptographie quantique",
]

HIGH_SEVERITY_KEYWORDS = [
    "crack", "broken", "vulnerable", "attack", "threat", "danger",
    "9 minutes", "9 min", "secp256k1", "mempool attack", "private key",
    "willow chip", "google quantum", "ibm quantum", "urgency", "urgent",
]

# ── Sources RSS à surveiller ──────────────────────────────────────────────────
RSS_FEEDS = [
    "https://cointelegraph.com/rss/tag/quantum-computing",
    "https://decrypt.co/feed",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://feeds.feedburner.com/TheHackersNews",
]


class QuantumRiskAgent(BaseAgent):
    """
    Agent de surveillance de la menace quantique.
    Émet un score de risque [0, 1] mis à jour toutes les 5 minutes.
    """

    def __init__(self):
        super().__init__(
            name="quantum_risk",
            description=(
                "Surveillance de la menace quantique sur les cryptomonnaies. "
                "Analyse les nouvelles, calcule un threat level [0-1] et émet "
                "des recommandations de réduction d'exposition sur les assets ECDSA."
            ),
            role=(
                "Risk macro — menace quantique : ECDSA secp256k1 vulnérabilité, "
                "signaux de réduction de risque sur BTC/ETH, conseils PQC migration"
            ),
        )
        self._cache: Dict[str, Any] = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 300.0  # 5 minutes

        # Baseline threat level (basé sur l'état connu en Mars 2026)
        # Google a confirmé que Willow peut théoriquement casser ECDSA en ~9 min
        # mais aucune attaque réelle n'a encore été réalisée
        self._baseline_threat: float = 0.38

        self._newsapi_key: Optional[str] = os.getenv("NEWS_API_KEY") or os.getenv("NEWSAPI_KEY")

        logger.info("[QUANTUM RISK] 🔮 Agent initialisé — surveillance menace quantique active")

    # ─────────────────────────────────────────────────────────────────────────
    # ANALYSE PRINCIPALE
    # ─────────────────────────────────────────────────────────────────────────

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Point d'entrée principal — retourne le score de risque quantique."""

        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        threat_level, signals = await self._compute_threat_level()
        threat_label = self._label_from_level(threat_level)
        recommendation = self._build_recommendation(threat_level, context)
        impacted = self._get_impacted_assets(threat_level)

        summary = (
            f"[QUANTUM RISK] Menace: {threat_label} ({threat_level:.0%}) | "
            f"Assets exposés: {', '.join(impacted)} | {recommendation['action']}"
        )

        result = {
            "agent": self.name,
            "summary": summary,
            "confidence": min(0.85, 0.4 + threat_level * 0.5),
            "recommendation": "HOLD" if threat_level < 0.5 else "SELL",
            "threat_level": threat_level,
            "threat_label": threat_label,
            "impacted_assets": impacted,
            "signals_found": signals,
            "action": recommendation["action"],
            "risk_multiplier": recommendation["risk_multiplier"],
            "pqc_advisory": recommendation["pqc_advisory"],
            "timestamp": datetime.utcnow().isoformat(),
        }

        self._cache = result
        self._cache_ts = now
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # CALCUL DU THREAT LEVEL
    # ─────────────────────────────────────────────────────────────────────────

    async def _compute_threat_level(self) -> Tuple[float, List[str]]:
        """
        Agrège les signaux de menace depuis plusieurs sources.
        Retourne (threat_level: float, signals: List[str]).
        """
        signals: List[str] = []
        score_components: List[float] = []

        # 1. Baseline basé sur l'état connu
        score_components.append(self._baseline_threat)

        # 2. Scan RSS (si feedparser disponible)
        if HAS_FEEDPARSER:
            rss_score, rss_signals = await self._scan_rss_feeds()
            if rss_score > 0:
                score_components.append(rss_score)
                signals.extend(rss_signals[:3])

        # 3. NewsAPI (si clé disponible)
        if HAS_NEWSAPI and self._newsapi_key:
            news_score, news_signals = await self._scan_newsapi()
            if news_score > 0:
                score_components.append(news_score)
                signals.extend(news_signals[:2])

        # 4. Agrégation (moyenne pondérée avec decay temporel)
        if len(score_components) == 1:
            final_score = score_components[0]
        else:
            weights = [0.4] + [0.6 / (len(score_components) - 1)] * (len(score_components) - 1)
            final_score = sum(w * s for w, s in zip(weights, score_components))

        # Clamp [0, 1]
        final_score = max(0.0, min(1.0, final_score))

        if not signals:
            signals = [
                "Baseline: Google Willow paper — ECDSA secp256k1 en ~9 min (Mars 2026)",
                "Aucun nouveau signal détecté — surveillance continue",
            ]

        return round(final_score, 3), signals

    async def _scan_rss_feeds(self) -> Tuple[float, List[str]]:
        """Scanne les flux RSS pour des mentions de menace quantique."""
        found_signals: List[str] = []
        high_severity_count = 0
        keyword_count = 0

        loop = asyncio.get_event_loop()

        def _parse_feeds():
            results = []
            for url in RSS_FEEDS:
                try:
                    feed = feedparser.parse(url)
                    for entry in feed.entries[:10]:
                        title = (entry.get("title", "") or "").lower()
                        summary = (entry.get("summary", "") or "").lower()
                        text = f"{title} {summary}"
                        results.append((entry.get("title", ""), text))
                except Exception:
                    pass
            return results

        try:
            entries = await asyncio.wait_for(
                loop.run_in_executor(None, _parse_feeds),
                timeout=8.0,
            )

            for title, text in entries:
                has_quantum = any(kw in text for kw in QUANTUM_KEYWORDS)
                has_severity = any(kw in text for kw in HIGH_SEVERITY_KEYWORDS)

                if has_quantum:
                    keyword_count += 1
                    if has_severity:
                        high_severity_count += 1
                        found_signals.append(f"RSS HIGH: {title[:80]}")

        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.warning(f"[QUANTUM RISK] Erreur scan RSS: {e}")

        # Score RSS: augmente avec le volume de mentions
        rss_score = min(0.3, keyword_count * 0.04 + high_severity_count * 0.08)
        return rss_score, found_signals

    async def _scan_newsapi(self) -> Tuple[float, List[str]]:
        """Scanne NewsAPI pour des articles récents sur la menace quantique."""
        if not self._newsapi_key:
            return 0.0, []

        found_signals: List[str] = []
        loop = asyncio.get_event_loop()

        def _fetch_news():
            try:
                client = NewsApiClient(api_key=self._newsapi_key)
                articles = client.get_everything(
                    q="quantum computing cryptocurrency OR bitcoin",
                    language="en",
                    sort_by="publishedAt",
                    page_size=10,
                )
                return articles.get("articles", [])
            except Exception as e:
                logger.warning(f"[QUANTUM RISK] NewsAPI erreur: {e}")
                return []

        try:
            articles = await asyncio.wait_for(
                loop.run_in_executor(None, _fetch_news),
                timeout=8.0,
            )

            high_severity = 0
            for article in articles:
                title = (article.get("title", "") or "").lower()
                desc = (article.get("description", "") or "").lower()
                text = f"{title} {desc}"

                if any(kw in text for kw in HIGH_SEVERITY_KEYWORDS):
                    high_severity += 1
                    found_signals.append(f"NEWS: {article.get('title', '')[:80]}")

            news_score = min(0.4, high_severity * 0.1)
            return news_score, found_signals

        except asyncio.TimeoutError:
            return 0.0, []

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _label_from_level(self, level: float) -> str:
        if level >= 0.85:
            return "CRITICAL"
        elif level >= 0.70:
            return "HIGH"
        elif level >= 0.50:
            return "ELEVATED"
        elif level >= 0.25:
            return "MODERATE"
        else:
            return "LOW"

    def _get_impacted_assets(self, threat_level: float) -> List[str]:
        """Retourne les assets impactés selon le niveau de menace."""
        threshold = 0.5 if threat_level < 0.5 else (0.7 if threat_level < 0.7 else 0.3)
        return [
            asset for asset, vuln_score in QUANTUM_VULNERABLE_ASSETS.items()
            if vuln_score >= threshold
        ]

    def _build_recommendation(self, threat_level: float, context: dict) -> Dict[str, Any]:
        """Construit la recommandation adaptée au niveau de menace."""
        if threat_level >= 0.85:
            return {
                "action": "REDUCE_MAX: Réduction immédiate de 50% de l'exposition BTC/ETH. Stopper les positions long leverage.",
                "risk_multiplier": 0.25,
                "pqc_advisory": "Migration PQC urgente. Utiliser uniquement des wallets cold storage hardware récents.",
            }
        elif threat_level >= 0.70:
            return {
                "action": "REDUCE: Diminuer l'exposition ECDSA de 30%. Éviter les transactions mempool.",
                "risk_multiplier": 0.5,
                "pqc_advisory": "Prioriser les assets sur chains avec roadmap PQC confirmée.",
            }
        elif threat_level >= 0.50:
            return {
                "action": "HEDGE: Couvrir une partie de l'exposition BTC/ETH via options puts.",
                "risk_multiplier": 0.75,
                "pqc_advisory": "Surveiller les annonces Google/IBM sur les capacités CRQCs.",
            }
        elif threat_level >= 0.25:
            return {
                "action": "MONITOR: Exposition normale autorisée. Surveillance renforcée.",
                "risk_multiplier": 1.0,
                "pqc_advisory": "Intégrer la migration PQC dans la stratégie long terme.",
            }
        else:
            return {
                "action": "NORMAL: Aucun ajustement requis. Risque quantique faible.",
                "risk_multiplier": 1.0,
                "pqc_advisory": "Risque quantique négligeable à court terme.",
            }

    def get_risk_multiplier(self) -> float:
        """Retourne le multiplicateur de risque actuel (pour l'orchestrateur)."""
        if not self._cache:
            return 1.0
        return self._cache.get("risk_multiplier", 1.0)

    def get_threat_level(self) -> float:
        """Retourne le niveau de menace actuel [0-1]."""
        if not self._cache:
            return self._baseline_threat
        return self._cache.get("threat_level", self._baseline_threat)
