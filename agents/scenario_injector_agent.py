"""
🎭 SCENARIO INJECTOR AGENT V1.0 — Pre-Discovery Polymarket Signals
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Inspiré de OHMO.AI / MiroFish — "The system injects scenarios and watches
how agents react before Polymarket prices it in."

CONCEPT CORE :
Cet agent simule des scénarios de marché hypothétiques (BTC +10%, ETH crash,
régulation majeure, annonce Fed, etc.) et évalue :
1. La probabilité INTERNE du scénario (signaux de nos 50 agents)
2. La probabilité POLYMARKET actuelle pour le même scénario
3. Si écart > 5% → signal "pre-discovery" — Polymarket n'a pas encore pricé ça

Inspiré par la stratégie OHMO.AI qui génère $7,358/semaine en trouvant
les marchés de prédiction AVANT qu'ils repricient les nouvelles.

SCÉNARIOS INJECTÉS (mis à jour dynamiquement selon contexte) :
- BTC UP/DOWN sur 24h, 7j
- ETH staking yield change
- Régulation majeure (SEC, EU MiCA, CFTC)
- Macro event (Fed, CPI, NFP)
- Exchange flow spike (gros retrait/dépôt)
- Whale movement detected
- Quantum threat escalation

DONNÉES :
- Polymarket Gamma API (public, sans auth)
- Contexte interne des 50 agents
- Signaux historiques (memory cache)
"""

import asyncio
import time
import math
import requests
from typing import Dict, Any, List, Optional, Tuple
from collections import deque

try:
    from agents.base_agent import BaseAgent
    from logging_config import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

    class BaseAgent:
        def __init__(self, name="scenario_injector", description="", role=""):
            self.name = name
            self.description = description
            self.role = role
            self.personality = "LEADER"
            self.personality_profile = {
                "label": "🟡 LEADER",
                "confidence_boost": 0.0,
                "min_threshold": 0.60,
                "behavior": "wait_confirm",
                "description": "Attente de confirmation, haute conviction requise",
            }

        async def safe_respond(self, question, context):
            return await self.respond(question, context)

        def _is_in_my_domain(self, question):
            return True

        def _apply_personality_bias(self, result):
            return result

        async def respond(self, question, context):
            raise NotImplementedError


# ── Configuration ─────────────────────────────────────────────────────────────
GAMMA_API            = "https://gamma-api.polymarket.com/markets"
EDGE_MIN_PCT         = 0.05    # Écart minimal signal/polymarket pour signaler (5%)
CACHE_TTL_S          = 120     # Cache scénarios 2 minutes
TIMEOUT_S            = 6       # Timeout API externe
MIN_VOLUME_USD       = 10_000  # Volume minimal Polymarket pour un signal valide
MAX_SCENARIOS_ACTIVE = 5       # Nombre max de scénarios actifs en parallèle


# ── Bibliothèque de scénarios ─────────────────────────────────────────────────
# Chaque scénario définit :
#   - id       : identifiant unique
#   - title    : description humaine
#   - keywords : mots-clés Polymarket pour trouver les marchés correspondants
#   - category : type d'événement (price_action, macro, regulatory, etc.)
#   - horizon  : horizon temporel (24h, 7d, 30d)

SCENARIO_LIBRARY = [
    # ── Price action BTC ──────────────────────────────────────────────────────
    {
        "id":       "btc_up_24h",
        "title":    "BTC monte >5% dans les 24h",
        "keywords": ["bitcoin", "btc", "above", "higher", "up", "24"],
        "category": "price_action",
        "horizon":  "24h",
        "asset":    "BTC",
        "direction": "UP",
    },
    {
        "id":       "btc_down_24h",
        "title":    "BTC baisse >5% dans les 24h",
        "keywords": ["bitcoin", "btc", "below", "lower", "down", "24"],
        "category": "price_action",
        "horizon":  "24h",
        "asset":    "BTC",
        "direction": "DOWN",
    },
    {
        "id":       "btc_ath_2025",
        "title":    "BTC atteindra un nouvel ATH en 2025",
        "keywords": ["bitcoin", "btc", "all-time high", "ath", "record", "2025"],
        "category": "price_action",
        "horizon":  "30d",
        "asset":    "BTC",
        "direction": "UP",
    },
    # ── Price action ETH ──────────────────────────────────────────────────────
    {
        "id":       "eth_up_24h",
        "title":    "ETH monte >5% dans les 24h",
        "keywords": ["ethereum", "eth", "above", "higher", "up", "24"],
        "category": "price_action",
        "horizon":  "24h",
        "asset":    "ETH",
        "direction": "UP",
    },
    {
        "id":       "eth_down_24h",
        "title":    "ETH baisse >5% dans les 24h",
        "keywords": ["ethereum", "eth", "below", "lower", "drop", "24"],
        "category": "price_action",
        "horizon":  "24h",
        "asset":    "ETH",
        "direction": "DOWN",
    },
    # ── Macro ─────────────────────────────────────────────────────────────────
    {
        "id":       "fed_cut",
        "title":    "La Fed baissera les taux lors de la prochaine réunion",
        "keywords": ["fed", "federal reserve", "rate cut", "interest rate", "fomc"],
        "category": "macro",
        "horizon":  "30d",
        "asset":    "MACRO",
        "direction": "BULLISH",
    },
    {
        "id":       "inflation_surprise",
        "title":    "CPI > 0.4% MoM (inflation surprise)",
        "keywords": ["cpi", "inflation", "consumer price", "surprise"],
        "category": "macro",
        "horizon":  "7d",
        "asset":    "MACRO",
        "direction": "BEARISH",
    },
    # ── Régulation ───────────────────────────────────────────────────────────
    {
        "id":       "sec_action",
        "title":    "SEC prend une action réglementaire majeure crypto",
        "keywords": ["sec", "securities", "regulation", "crypto", "enforcement"],
        "category": "regulatory",
        "horizon":  "7d",
        "asset":    "CRYPTO",
        "direction": "BEARISH",
    },
    {
        "id":       "etf_approval",
        "title":    "Un ETF crypto majeur approuvé",
        "keywords": ["etf", "approved", "bitcoin etf", "ethereum etf", "approval"],
        "category": "regulatory",
        "horizon":  "30d",
        "asset":    "CRYPTO",
        "direction": "BULLISH",
    },
    # ── On-chain / Whale ──────────────────────────────────────────────────────
    {
        "id":       "whale_accumulation",
        "title":    "Accumulation whale BTC massive détectée",
        "keywords": ["whale", "large", "accumulation", "bitcoin", "wallet"],
        "category": "onchain",
        "horizon":  "24h",
        "asset":    "BTC",
        "direction": "UP",
    },
    # ── Quantum ───────────────────────────────────────────────────────────────
    {
        "id":       "quantum_escalation",
        "title":    "Escalade menace quantique ECDSA crypto",
        "keywords": ["quantum", "ecdsa", "cryptography", "security", "threat"],
        "category": "quantum",
        "horizon":  "30d",
        "asset":    "CRYPTO",
        "direction": "BEARISH",
    },
]


class ScenarioInjectorAgent(BaseAgent):
    """
    Agent d'injection de scénarios — detects Polymarket mispricings
    AVANT que le marché price les nouvelles informations.
    Personnalité LEADER : attend signal multi-source avant d'émettre.
    """

    def __init__(self):
        super().__init__(
            name="scenario_injector",
            description="Injecte des scénarios macro/crypto et détecte les mispricings Polymarket avant que le marché price l'information",
            role="Pre-discovery: identifie les marchés Polymarket non encore pricés selon nos 50 agents",
        )
        self._cache: Optional[Dict]   = None
        self._cache_ts: float         = 0.0
        self._signal_history: deque   = deque(maxlen=50)
        self._session_pnl: float      = 0.0
        self._signals_emitted: int    = 0

    # ── Méthodes d'évaluation interne des scénarios ───────────────────────────

    def _evaluate_scenario_probability(self, scenario: Dict, context: dict) -> float:
        """
        Estime la probabilité interne du scénario basée sur les signaux de
        nos 50 agents déjà présents dans le contexte.
        Retourne une probabilité [0, 1].
        """
        base_prob = 0.50   # Probabilité neutre par défaut
        adjustments = 0.0

        direction    = scenario.get("direction", "")
        category     = scenario.get("category", "")
        asset        = scenario.get("asset", "BTC")

        # ── Signaux de prix ────────────────────────────────────────────────
        symbol_score = context.get("symbol_score", 0.5)
        trend = context.get("trend_1h", 0.0)

        if direction == "UP":
            adjustments += (symbol_score - 0.5) * 0.3
            adjustments += trend * 0.15
        elif direction == "DOWN":
            adjustments -= (symbol_score - 0.5) * 0.3
            adjustments -= trend * 0.15

        # ── Signaux macro ──────────────────────────────────────────────────
        macro_bias = context.get("macro_bias", "NEUTRAL")
        if category == "macro":
            if direction == "BULLISH" and macro_bias in ("BULLISH", "RISK_ON"):
                adjustments += 0.10
            elif direction == "BEARISH" and macro_bias in ("BEARISH", "RISK_OFF"):
                adjustments += 0.10

        # ── Fear & Greed ───────────────────────────────────────────────────
        fg_value = context.get("fear_greed_value", 50)
        if direction in ("UP", "BULLISH") and fg_value > 65:
            adjustments += 0.08
        elif direction in ("DOWN", "BEARISH") and fg_value < 35:
            adjustments += 0.08

        # ── Whale signal ───────────────────────────────────────────────────
        whale_signal = context.get("whale_signal", "HOLD")
        if category == "onchain":
            if direction == "UP" and whale_signal == "BUY":
                adjustments += 0.12
            elif direction == "DOWN" and whale_signal == "SELL":
                adjustments += 0.12

        # ── Quantum threat ─────────────────────────────────────────────────
        quantum_threat = context.get("quantum_threat", 0.0)
        if category == "quantum":
            adjustments += quantum_threat * 0.20

        # ── Alerte réglementaire ───────────────────────────────────────────
        reg_alert = context.get("regulatory_alert", "LOW")
        if category == "regulatory":
            alert_map = {"LOW": -0.05, "MEDIUM": 0.0, "HIGH": 0.15}
            adjustments += alert_map.get(reg_alert, 0.0)

        # ── Sentiment global ───────────────────────────────────────────────
        sentiment_score = context.get("sentiment_score", 0.5)
        if direction in ("UP", "BULLISH"):
            adjustments += (sentiment_score - 0.5) * 0.10
        elif direction in ("DOWN", "BEARISH"):
            adjustments -= (sentiment_score - 0.5) * 0.10

        # ── Regime detector ────────────────────────────────────────────────
        regime = context.get("market_regime_adx", "TRANSITIONAL")
        if direction in ("UP", "BULLISH") and regime == "TRENDING_BULL":
            adjustments += 0.08
        elif direction in ("DOWN", "BEARISH") and regime == "TRENDING_BEAR":
            adjustments += 0.08

        internal_prob = max(0.05, min(0.95, base_prob + adjustments))
        return round(internal_prob, 3)

    def _fetch_polymarket_price(self, scenario: Dict) -> Optional[float]:
        """
        Cherche sur Polymarket un marché correspondant au scénario.
        Retourne la probabilité YES actuelle [0, 1] ou None si introuvable.
        """
        try:
            keywords = scenario.get("keywords", [])
            search_term = keywords[0] if keywords else ""

            resp = requests.get(
                GAMMA_API,
                params={
                    "search": search_term,
                    "closed": "false",
                    "limit": 20,
                },
                timeout=TIMEOUT_S,
            )
            if resp.status_code != 200:
                return None

            markets = resp.json()
            if not markets:
                return None

            # Cherche le marché le plus pertinent par correspondance de keywords
            best_match = None
            best_score = 0

            for market in markets:
                title    = (market.get("question", "") or "").lower()
                vol      = float(market.get("volume", 0) or 0)

                if vol < MIN_VOLUME_USD:
                    continue

                score = sum(1 for kw in keywords if kw.lower() in title)
                if score > best_score:
                    best_score = score
                    best_match = market

            if not best_match or best_score < 2:
                return None

            # Extraire le prix YES
            outcomes_prices = best_match.get("outcomePrices", "")
            if outcomes_prices:
                if isinstance(outcomes_prices, str):
                    prices = [float(p) for p in outcomes_prices.strip("[]").split(",") if p.strip()]
                elif isinstance(outcomes_prices, list):
                    prices = [float(p) for p in outcomes_prices]
                else:
                    return None
                if prices:
                    return round(prices[0], 3)

            return None

        except Exception as e:
            logger.debug(f"[ScenarioInjector] Polymarket fetch error: {e}")
            return None

    def _analyze_scenarios(self, context: dict) -> List[Dict]:
        """
        Pour chaque scénario, calcule l'écart entre probabilité interne
        et prix Polymarket. Retourne la liste des opportunités pré-discovery.
        """
        opportunities = []

        for scenario in SCENARIO_LIBRARY[:MAX_SCENARIOS_ACTIVE * 2]:
            internal_prob  = self._evaluate_scenario_probability(scenario, context)
            polymarket_prob = self._fetch_polymarket_price(scenario)

            if polymarket_prob is None:
                continue

            edge_pct = abs(internal_prob - polymarket_prob) * 100

            if edge_pct < EDGE_MIN_PCT * 100:
                continue

            # Détermine la position recommandée
            if internal_prob > polymarket_prob:
                position = "BUY YES"
                fair_desc = f"nos agents estiment {internal_prob:.0%} > Poly {polymarket_prob:.0%}"
            else:
                position = "BUY NO"
                fair_desc = f"nos agents estiment {internal_prob:.0%} < Poly {polymarket_prob:.0%}"

            # Confidence basée sur l'edge et le nombre de signaux concordants
            confidence = min(0.9, 0.5 + (edge_pct / 100) * 2)

            opp = {
                "scenario_id":    scenario["id"],
                "title":          scenario["title"],
                "category":       scenario["category"],
                "horizon":        scenario["horizon"],
                "asset":          scenario["asset"],
                "internal_prob":  internal_prob,
                "polymarket_prob": polymarket_prob,
                "edge_pct":       round(edge_pct, 1),
                "position":       position,
                "fair_desc":      fair_desc,
                "confidence":     round(confidence, 2),
            }
            opportunities.append(opp)

        # Trier par edge décroissant
        opportunities.sort(key=lambda x: x["edge_pct"], reverse=True)
        return opportunities[:MAX_SCENARIOS_ACTIVE]

    # ── Interface principale ──────────────────────────────────────────────────

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """
        Analyse les scénarios et retourne les mispricings Polymarket détectés.
        Cache 2 minutes pour ne pas spammer Polymarket API.
        """
        now = time.time()

        # Retourner le cache si encore frais
        if self._cache and (now - self._cache_ts) < CACHE_TTL_S:
            return self._cache

        # Analyser les scénarios en parallèle de manière asynchrone
        loop = asyncio.get_event_loop()
        opportunities = await loop.run_in_executor(
            None, self._analyze_scenarios, context
        )

        # Résumé et signal global
        n_opps = len(opportunities)
        if n_opps == 0:
            signal        = "HOLD"
            confidence    = 0.30
            summary       = "Aucun mispricing Polymarket détecté. Marchés correctement pricés."
            recommendation = "HOLD — Pas d'opportunité pré-discovery actuellement"
        elif n_opps == 1:
            best = opportunities[0]
            signal        = "SIGNAL"
            confidence    = best["confidence"]
            summary       = (
                f"1 mispricing détecté : {best['title']} "
                f"(edge {best['edge_pct']:.1f}%) — {best['position']}"
            )
            recommendation = f"PRE-DISCOVERY: {best['position']} sur '{best['title'][:60]}'"
        else:
            best = opportunities[0]
            signal        = "STRONG_SIGNAL"
            confidence    = min(0.90, best["confidence"] + 0.05)
            summary       = (
                f"{n_opps} mispricings Polymarket détectés. "
                f"Meilleure: {best['title']} (edge {best['edge_pct']:.1f}%)"
            )
            recommendation = (
                f"PRE-DISCOVERY x{n_opps}: meilleur edge {best['edge_pct']:.1f}% "
                f"→ {best['position']}"
            )

        self._signals_emitted += (1 if n_opps > 0 else 0)

        result = {
            "agent":              "scenario_injector",
            "summary":            summary,
            "confidence":         confidence,
            "recommendation":     recommendation,
            "signal":             signal,
            "opportunities":      opportunities,
            "scenarios_analyzed": len(SCENARIO_LIBRARY),
            "scenarios_with_edge": n_opps,
            "session_signals":    self._signals_emitted,
            "timestamp":          int(now),
        }

        if n_opps > 0:
            logger.info(
                f"[ScenarioInjector] 🎭 {n_opps} pre-discovery signals | "
                f"Best: {opportunities[0]['title'][:50]} edge={opportunities[0]['edge_pct']:.1f}%"
            )

        self._cache    = result
        self._cache_ts = now
        return result

    def get_opportunities(self) -> List[Dict]:
        """Accès direct aux dernières opportunités (pour le dashboard)."""
        if self._cache:
            return self._cache.get("opportunities", [])
        return []
