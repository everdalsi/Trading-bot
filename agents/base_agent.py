from abc import ABC, abstractmethod
from typing import Dict, Any
from knowledge_base import KnowledgeBase  # ← AJOUTÉ

class BaseAgent(ABC):
    """
    BASE AGENT V3 — Cerveau commun + Spécialisation stricte + Zéro malentendu
    """

    def __init__(self, name: str, role: str = None, description: str = None):
        self.name = name
        self.role = role or description
        self.description = description or role
        self.kb = KnowledgeBase()  # Chaque agent a accès direct au glossaire

    @abstractmethod
    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Chaque agent doit retourner EXACTEMENT ce format."""
        pass

    async def safe_respond(self, question: str, context: dict) -> Dict[str, Any]:
        try:
            result = await self.respond(question, context)
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
        except Exception as e:
            return {
                "agent": self.name,
                "summary": f"Erreur interne {self.name}: {str(e)[:80]}",
                "confidence": 0.0,
                "recommendation": "Vérifier logs",
                "risks": ["Exception"]
            }

    def _is_in_my_domain(self, question: str) -> bool:
        """Vérifie que la question correspond au rôle (spécialisation stricte)."""
        domain_keywords = {
            "trader": ["buy", "sell", "hold", "décision", "position"],
            "risk": ["risk", "drawdown", "kelly", "veto", "perte"],
            "analyst": ["pattern", "wyckoff", "vsa", "technique"],
            "evolution": ["amélioration", "code", "edit", "push"],
            # etc. (on ajoutera pour tous plus tard)
        }
        return any(kw in question.lower() for kw in domain_keywords.get(self.name, []))

    def explain_term(self, term: str) -> str:
        """Tous les agents utilisent le même glossaire → zéro malentendu."""
        return self.kb.explain_term(term) or f"{term} (définition partagée)"
