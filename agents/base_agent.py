"""
🧠 BASE AGENT V4.0 — Cerveau commun + Personnalités OHMO.AI + Spécialisation
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX V3.2 :
- domain_keywords complété : supervisor, wallet_copier, social_listener,
  portfolio_manager, knowledge_specialist, hedging, self_improvement
  → plus aucun agent exclu du débat collectif par défaut
- Mots-clés "débat collectif" ajoutés à TOUS les agents via default_debate_keywords
- Timeout safe_respond étendu à 10s (8s trop court pour certaines analyses)

UPGRADE V4.0 — Système de personnalités (inspiré OHMO.AI) :
- RETAIL       : panic buy/sell, FOMO, sur-réaction aux news
- INSTITUTIONAL: fade moves, patience, contre-tendance, smart money
- LEADER       : attente de confirmation, haute conviction requise
Chaque personnalité biaise légèrement confidence + comportement rapporté.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any
from knowledge_base import KnowledgeBase
import asyncio


# ======================== PATCH SINGLETON KB V4.1 ========================
class _KnowledgeBaseSingleton:
    _instance = None

    @staticmethod
    def get_instance():
        if _KnowledgeBaseSingleton._instance is None:
            _KnowledgeBaseSingleton._instance = KnowledgeBase()
        return _KnowledgeBaseSingleton._instance
# =========================================================================


# ======================== SYSTÈME DE PERSONNALITÉS V4.0 ==================
PERSONALITY_RETAIL        = "RETAIL"
PERSONALITY_INSTITUTIONAL = "INSTITUTIONAL"
PERSONALITY_LEADER        = "LEADER"

# Mapping agent → personnalité (comportement naturel de chaque profil)
AGENT_PERSONALITY_MAP: Dict[str, str] = {
    # RETAIL : réactif aux news, sentiment, FOMO, sur-réaction
    "social_listener":      PERSONALITY_RETAIL,
    "fear_greed":           PERSONALITY_RETAIL,
    "news_event":           PERSONALITY_RETAIL,
    "sentiment_aggregator": PERSONALITY_RETAIL,
    "polymarket_arb":       PERSONALITY_RETAIL,
    "event_sniper":         PERSONALITY_RETAIL,
    "sports_arb":           PERSONALITY_RETAIL,

    # INSTITUTIONAL : smart money, patience, fade the move, contre-tendanciel
    "risk":                 PERSONALITY_INSTITUTIONAL,
    "hedging":              PERSONALITY_INSTITUTIONAL,
    "correlation_watcher":  PERSONALITY_INSTITUTIONAL,
    "wallet_copier":        PERSONALITY_INSTITUTIONAL,
    "whale_tracker":        PERSONALITY_INSTITUTIONAL,
    "drawdown_guard":       PERSONALITY_INSTITUTIONAL,
    "derivatives":          PERSONALITY_INSTITUTIONAL,
    "exchange_flow":        PERSONALITY_INSTITUTIONAL,
    "cross_asset":          PERSONALITY_INSTITUTIONAL,
    "regulatory_monitor":   PERSONALITY_INSTITUTIONAL,
    "on_chain":             PERSONALITY_INSTITUTIONAL,
    "blockchain_health":    PERSONALITY_INSTITUTIONAL,
    "token_unlock":         PERSONALITY_INSTITUTIONAL,
    "defi_monitor":         PERSONALITY_INSTITUTIONAL,

    # LEADER : attente de confirmation, haute conviction, décision finale
    "analyst":              PERSONALITY_LEADER,
    "trader":               PERSONALITY_LEADER,
    "supervisor":           PERSONALITY_LEADER,
    "quant_ml":             PERSONALITY_LEADER,
    "regime_detector":      PERSONALITY_LEADER,
    "macro_regime":         PERSONALITY_LEADER,
    "quantum_risk":         PERSONALITY_LEADER,
    "vol_regime":           PERSONALITY_LEADER,
    "portfolio_manager":    PERSONALITY_LEADER,
    "pattern_recognition":  PERSONALITY_LEADER,
    "macro_calendar":       PERSONALITY_LEADER,
    "arbitrage_scanner":    PERSONALITY_LEADER,
    "options_flow":         PERSONALITY_LEADER,
    "grid_strategy":        PERSONALITY_LEADER,
    "liquidation_tracker":  PERSONALITY_LEADER,
    "scenario_injector":    PERSONALITY_LEADER,
}

# Profils de comportement par personnalité
PERSONALITY_PROFILES: Dict[str, Dict] = {
    PERSONALITY_RETAIL: {
        "label":            "🔴 RETAIL",
        "confidence_boost": +0.05,   # Sur-confiant, amplifie les tendances
        "min_threshold":    0.30,    # Signale facilement (seuil bas)
        "behavior":         "panic_buy_sell",
        "description":      "Réactif aux news, FOMO, sur-réaction aux tendances",
    },
    PERSONALITY_INSTITUTIONAL: {
        "label":            "🔵 INSTITUTIONAL",
        "confidence_boost": -0.05,   # Conservateur, fade les moves
        "min_threshold":    0.55,    # Difficile à déclencher
        "behavior":         "fade_moves",
        "description":      "Smart money, patient, contre-tendanciel",
    },
    PERSONALITY_LEADER: {
        "label":            "🟡 LEADER",
        "confidence_boost": 0.0,     # Neutre — attend les données
        "min_threshold":    0.60,    # Haute conviction requise avant signal
        "behavior":         "wait_confirm",
        "description":      "Attente de confirmation, haute conviction requise",
    },
}
# =========================================================================


# Mots-clés de débat collectif → tous les agents y participent
_DEBATE_KEYWORDS = [
    # Orchestrateur → tous les agents participent TOUJOURS à toute question de trading
    "signal", "analyse", "trading", "trade",  # question standard: "analyse trading signal SYMBOL"
    "synthèse", "synthétise", "débat", "cerveau collectif",
    "final decision", "raffine", "trade ou no trade",
    "décision finale", "orchestrator", "ask_all", "round",
    "micro", "analyse collective",
]


class BaseAgent(ABC):
    """
    BASE AGENT V4.0 — Cerveau commun + Personnalités OHMO.AI + Spécialisation
    """

    def __init__(self, name: str, role: str = None, description: str = None):
        self.name        = name
        self.role        = role or description
        self.description = description or role
        self.kb          = _KnowledgeBaseSingleton.get_instance()

        # V4.0 : Personnalité assignée automatiquement selon le nom de l'agent
        _p_key                   = AGENT_PERSONALITY_MAP.get(name, PERSONALITY_INSTITUTIONAL)
        self.personality         = _p_key
        self.personality_profile = PERSONALITY_PROFILES[_p_key]

    @abstractmethod
    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Chaque agent doit retourner EXACTEMENT ce format."""
        pass

    def _apply_personality_bias(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        V4.0 : Applique le biais de personnalité sur la confiance du résultat.
        - RETAIL      : +0.05 confiance (sur-réactif, amplifie signal)
        - INSTITUTIONAL: -0.05 confiance (conservateur, fade the move)
        - LEADER      : neutre, exige haute conviction externe
        Ajoute les champs 'personality' et 'behavior_type' à la réponse.
        """
        profile   = self.personality_profile
        raw_conf  = result.get("confidence", 0.0)
        new_conf  = max(0.0, min(1.0, raw_conf + profile["confidence_boost"]))

        result["confidence"]    = new_conf
        result["personality"]   = profile["label"]
        result["behavior_type"] = profile["behavior"]
        return result

    async def safe_respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Version ultra-sécurisée avec timeout + garde-fou exception + biais personnalité."""
        try:
            result = await asyncio.wait_for(
                self.respond(question, context),
                timeout=10.0
            )
            required_keys = {"agent", "summary", "confidence", "recommendation"}
            if not all(k in result for k in required_keys):
                result = {
                    "agent":          self.name,
                    "summary":        f"⚠️ Format invalide par {self.name} → corrigé",
                    "confidence":     0.0,
                    "recommendation": "Vérifier rôle",
                    "error":          "missing_keys",
                }
            if not self._is_in_my_domain(question):
                result["warning"] = (
                    f"{self.name} a répondu hors de sa spécialité → ignoré par orchestreur"
                )
            # V4.0 : biais de personnalité appliqué sur chaque réponse valide
            result = self._apply_personality_bias(result)
            return result

        except asyncio.TimeoutError:
            return {
                "agent":          self.name,
                "summary":        f"Timeout dans {self.name} (10s)",
                "confidence":     0.0,
                "recommendation": "HOLD - Timeout",
                "error":          "timeout",
                "personality":    self.personality_profile["label"],
                "behavior_type":  self.personality_profile["behavior"],
            }
        except Exception as e:
            return {
                "agent":          self.name,
                "summary":        f"Erreur interne {self.name}: {str(e)[:80]}",
                "confidence":     0.0,
                "recommendation": "Vérifier logs",
                "risks":          ["Exception"],
                "personality":    self.personality_profile["label"],
                "behavior_type":  self.personality_profile["behavior"],
            }

    def _is_in_my_domain(self, question: str) -> bool:
        """
        FIX V3.2 : domain_keywords complété pour TOUS les agents.
        Chaque agent peut aussi participer au débat collectif via _DEBATE_KEYWORDS.
        """
        domain_keywords = {
            # ── Agents cœur ──────────────────────────────────────────────────
            "trader": [
                "buy", "sell", "hold", "trade", "position",
                "décision", "entry", "exit", "long", "short", "ordre",
            ],
            "risk": [
                "risk", "drawdown", "kelly", "veto", "perte", "stop",
                "position", "liquidation", "levier", "sizing",
            ],
            "analyst": [
                "pattern", "wyckoff", "vsa", "technique", "analyse",
                "indicateur", "rsi", "macd", "bollinger", "ema",
            ],
            "learning": [
                "leçon", "mistake", "amélioration", "lesson",
                "blacklist", "pattern", "historique", "mémoire",
            ],
            "research": [
                "analyse", "recherche", "kol", "on-chain", "spoofing",
                "wash", "mev", "order book", "sentiment", "klines",
                "fear greed", "whale", "liquidation", "data",
            ],
            # ── Agents spécialisés ───────────────────────────────────────────
            "quant_ml": [
                "regime", "backtest", "model", "bull", "bear",
                "sideways", "volatile", "trend", "ml", "quant", "macro",
            ],
            "execution_engine": [
                "execute", "twap", "slice", "vwap", "order", "fill",
                "slippage", "timing", "split", "exécution",
            ],
            "yield_staking": [
                "stake", "lido", "marinade", "apy", "yield",
                "staking", "rewards", "liquid", "farming",
            ],
            "hedging": [
                "hedge", "couverture", "protection", "short",
                "options", "futures", "delta", "neutral",
            ],
            "portfolio_manager": [
                "portfolio", "savings", "allocation", "rebalance",
                "diversification", "capital", "gestion",
            ],
            # ── Agents supervision / mémoire ─────────────────────────────────
            "supervisor": [
                "supervisor", "synthèse", "arbitre", "final",
                "décision finale", "vote", "consensus", "portfolio",
                "wallet", "savings", "staking", "transfer", "funding",
            ],
            "self_improvement": [
                "monitor", "health", "santé", "watchdog", "immune",
                "repair", "anomalie", "crash", "erreur système",
                "surveillance", "self_improvement",
            ],
            "evolution": [
                "évolution", "evolution", "amélioration", "upgrade",
                "améliorer", "modifier code", "auto-modif", "max trades",
                "monitor", "health", "santé", "watchdog", "immune",
            ],
            "wallet_copier": [
                "wallet", "copier", "copy", "smart money", "baleine",
                "whale wallet", "on-chain", "adresse", "follow",
            ],
            "social_listener": [
                "social", "twitter", "reddit", "sentiment", "fear",
                "greed", "news", "actualité", "trending", "buzz",
            ],
            "knowledge_specialist": [
                "knowledge", "connaissance", "pdf", "wyckoff",
                "livre", "stratégie", "méthode", "vsa", "cfa",
            ],
            "scenario_injector": [
                "scenario", "scénario", "inject", "simulate", "polymarket",
                "pre-price", "before price", "priced in", "opportunity",
            ],
            # ── Fallback ─────────────────────────────────────────────────────
            "default": list(_DEBATE_KEYWORDS),
        }

        q = question.lower()

        # 1. Vérifie si c'est une question de débat collectif → tout le monde participe
        if any(kw in q for kw in _DEBATE_KEYWORDS):
            return True

        # 2. Vérifie les keywords spécifiques de l'agent
        keywords = domain_keywords.get(self.name, domain_keywords["default"])
        return any(kw in q for kw in keywords)

    def explain_term(self, term: str) -> str:
        """Tous les agents utilisent le même glossaire → zéro malentendu."""
        try:
            explanation = self.kb.explain_term(term)
            return explanation or f"{term} (définition partagée)"
        except Exception:
            return f"{term} (glossaire indisponible)"
