"""
🧠 BASE AGENT V3.2 — Cerveau commun + Spécialisation stricte + Zéro malentendu
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX V3.2 :
- domain_keywords complété : supervisor, wallet_copier, social_listener,
  portfolio_manager, knowledge_specialist, hedging, self_improvement
  → plus aucun agent exclu du débat collectif par défaut
- Mots-clés "débat collectif" ajoutés à TOUS les agents via default_debate_keywords
- Timeout safe_respond étendu à 10s (8s trop court pour certaines analyses)
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


# Mots-clés de débat collectif → tous les agents y participent
_DEBATE_KEYWORDS = [
    "synthèse", "synthétise", "débat", "cerveau collectif",
    "final decision", "raffine", "trade ou no trade",
    "décision finale", "orchestrator", "ask_all", "round",
    "micro", "analyse collective",
]


class BaseAgent(ABC):
    """
    BASE AGENT V3.2 — Cerveau commun + Spécialisation stricte + Zéro malentendu
    """

    def __init__(self, name: str, role: str = None, description: str = None):
        self.name        = name
        self.role        = role or description
        self.description = description or role
        self.kb          = _KnowledgeBaseSingleton.get_instance()

    @abstractmethod
    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Chaque agent doit retourner EXACTEMENT ce format."""
        pass

    async def safe_respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Version ultra-sécurisée avec timeout + garde-fou exception."""
        try:
            result = await asyncio.wait_for(
                self.respond(question, context),
                timeout=10.0   # FIX V3.2 : 10s au lieu de 8s
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
            return result
        except asyncio.TimeoutError:
            return {
                "agent":          self.name,
                "summary":        f"Timeout dans {self.name} (10s)",
                "confidence":     0.0,
                "recommendation": "HOLD - Timeout",
                "error":          "timeout",
            }
        except Exception as e:
            return {
                "agent":          self.name,
                "summary":        f"Erreur interne {self.name}: {str(e)[:80]}",
                "confidence":     0.0,
                "recommendation": "Vérifier logs",
                "risks":          ["Exception"],
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
