from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseAgent(ABC):
    """
    Classe de base pour tous les agents du Trading Bot v7.1
    """

    def __init__(self, name: str, role: str):
        self.name = name
        self.role = role

    @abstractmethod
    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """
        Méthode que chaque agent DOIT implémenter.
        Doit toujours retourner un dictionnaire avec cette structure exacte.
        """
        pass

    def __str__(self):
        return f"{self.name.capitalize()}Agent (rôle: {self.role})"

    def __repr__(self):
        return f"<{self.__class__.__name__} name={self.name}>"

    # Méthode de secours au cas où un agent plante
    async def safe_respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Version sécurisée utilisée par l'orchestrator en cas d'erreur."""
        try:
            return await self.respond(question, context)
        except Exception as e:
            return {
                "agent": self.name,
                "summary": f"Erreur interne dans {self.name}: {str(e)[:80]}",
                "arguments": [],
                "risks": ["Exception dans l'agent"],
                "confidence": 0.0,
                "recommendation": "Vérifier les logs du bot"
            }
