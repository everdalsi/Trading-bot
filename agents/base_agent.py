from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseAgent(ABC):
    """
    BaseAgent V2 - Compatible avec tous les agents améliorés
    Accepte 'description' en plus de 'role' pour la compatibilité.
    """

    def __init__(self, name: str, role: str = None, description: str = None):
        self.name = name
        self.role = role or description
        self.description = description or role

    @abstractmethod
    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Chaque agent doit implémenter cette méthode."""
        pass

    async def safe_respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Version sécurisée utilisée par l'orchestrator."""
        try:
            return await self.respond(question, context)
        except Exception as e:
            return {
                "agent": self.name,
                "summary": f"Erreur dans {self.name}: {str(e)[:100]}",
                "arguments": [],
                "risks": ["Exception interne"],
                "confidence": 0.0,
                "recommendation": "Vérifier les logs"
            }

    def __str__(self):
        return f"{self.name.capitalize()}Agent"

    def __repr__(self):
        return f"<{self.__class__.__name__} name={self.name}>"
