from abc import ABC, abstractmethod
from typing import Dict, Any
from knowledge_base import KnowledgeBase  # ← AJOUTÉ
import asyncio
import functools

# ======================== PATCH SILICON VALLEY V4.1 ========================
# Singleton KnowledgeBase : une seule instance dans tout le bot
# → Supprime les 15+ "ChromaDB initialisée" dans les logs
class _KnowledgeBaseSingleton:
    _instance = None

    @staticmethod
    def get_instance():
        if _KnowledgeBaseSingleton._instance is None:
            _KnowledgeBaseSingleton._instance = KnowledgeBase()
        return _KnowledgeBaseSingleton._instance
# ===========================================================================

class BaseAgent(ABC):
    """
    BASE AGENT V3.1 — Cerveau commun + Spécialisation stricte + Zéro malentendu
    """

    def __init__(self, name: str, role: str = None, description: str = None):
        self.name = name
        self.role = role or description
        self.description = description or role
        # ======================== PATCH SINGLETON KB ========================
        # Au lieu de créer une nouvelle KnowledgeBase() à chaque agent,
        # on utilise le singleton → plus de ChromaDB multiples
        self.kb = _KnowledgeBaseSingleton.get_instance()
        # ===========================================================================

    @abstractmethod
    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Chaque agent doit retourner EXACTEMENT ce format."""
        pass

    # ======================== FIX RECURSION V4.1 ========================
    async def safe_respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Version ultra-sécurisée avec timeout + garde-fou recursion"""
        try:
            # Timeout de 8 secondes pour éviter les blocages
            result = await asyncio.wait_for(
                self.respond(question, context),
                timeout=8.0
            )
            # Vérification stricte du format (zéro malentendu)
            required_keys = {"agent", "summary", "confidence", "recommendation"}
            if not all(k in result for k in required_keys):
                result = {
                    "agent": self.name,
                    "summary": f"⚠️ Format invalide par {self.name} → corrigé",
                    "confidence": 0.0,
                    "recommendation": "Vérifier rôle",
                    "error": "missing_keys"
                }
            # Vérification spécialisation
            if not self._is_in_my_domain(question):
                result["warning"] = f"{self.name} a répondu hors de sa spécialité → ignoré par orchestreur"
            return result
        except asyncio.TimeoutError:
            return {
                "agent": self.name,
                "summary": f"Timeout dans {self.name} (8s)",
                "confidence": 0.0,
                "recommendation": "HOLD - Timeout",
                "error": "timeout"
            }
        except Exception as e:
            return {
                "agent": self.name,
                "summary": f"Erreur interne {self.name}: {str(e)[:80]}",
                "confidence": 0.0,
                "recommendation": "Vérifier logs",
                "risks": ["Exception"]
            }
    # ===========================================================================

    def _is_in_my_domain(self, question: str) -> bool:
        """Spécialisation stricte mais plus souple pour le micro-cycle et le débat collectif."""
        domain_keywords = {
            "trader": ["buy", "sell", "hold", "trade", "position", "décision", "entry", "exit"],
            "risk": ["risk", "drawdown", "kelly", "veto", "perte", "stop", "position"],
            "analyst": ["pattern", "wyckoff", "vsa", "technique", "analyse"],
            "learning": ["leçon", "mistake", "amélioration", "lesson"],
            "quant_ml": ["regime", "backtest", "model"],
            "execution_engine": ["execute", "twap", "slice"],
            "yield_staking": ["stake", "lido", "marinade", "apy"],
            "portfolio_manager": ["portfolio", "savings", "allocation"],
            "research": ["analyse", "recherche", "KOL", "on-chain", "spoofing", "wash", "MEV", "order book", "sentiment", "klines", "fear greed"],
            # PATCH : on autorise les mots-clés de débat collectif pour que les agents se parlent
            "default": ["synthèse", "débat", "cerveau collectif", "final decision", "raffine", "trade ou no trade", "micro"]
        }
        q = question.lower()
        # Si l’agent n’a pas de domaine spécifique, on utilise le default
        keywords = domain_keywords.get(self.name, domain_keywords["default"])
        return any(kw in q for kw in keywords)

    def explain_term(self, term: str) -> str:
        """Tous les agents utilisent le même glossaire → zéro malentendu."""
        return self.kb.explain_term(term) or f"{term} (définition partagée)"
