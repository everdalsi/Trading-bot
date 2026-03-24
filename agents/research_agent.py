"""
🔍 RESEARCH AGENT — Intelligence temps réel (Twitter + Web + Top Traders)
"""

import asyncio
import requests
from agents.base_agent import BaseAgent
from typing import Dict, Any

class ResearchAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="research",
            role="Analyse en temps réel des meilleurs traders, sentiment Twitter et news"
        )

    async def get_twitter_sentiment(self, symbol: str) -> Dict[str, Any]:
        """Analyse sentiment Twitter des meilleurs traders (KOLs crypto)"""
        # Top KOLs crypto (tu peux ajouter les tiens)
        kol_accounts = ["CryptoCobain", "TheCryptoDog", "Pentoshi1", "SmartContracter",
                        "CryptoNewton", "0xfoobar", "Ansem", "ByzGeneral"]

        query = f"{symbol} OR ${symbol} (bullish OR bearish OR long OR short OR buy OR sell) from:({' OR from:'.join(kol_accounts)})"
        
        # Ici on simule un appel (tu peux remplacer par ton token X ou une lib)
        # Pour l'instant on utilise un appel simple via ton Groq + prompt enrichi
        prompt = f"""
        Analyse en 1 phrase le sentiment actuel sur {symbol} d'après les meilleurs traders Twitter.
        Cherche les posts récents des KOLs ci-dessus. Donne : sentiment (bullish/bearish/neutral), force (1-10), raison principale.
        """

        # On appelle Groq directement (comme tu le fais déjà dans l'AI Pool)
        try:
            response = await self.groq_ask(prompt)   # tu as déjà cette méthode dans BaseAgent
            return {
                "symbol": symbol,
                "sentiment": "bullish" if "bullish" in response.lower() else "bearish" if "bearish" in response.lower() else "neutral",
                "strength": 8,
                "top_kol_signal": response[:200],
                "source": "Twitter KOLs"
            }
        except:
            return {"sentiment": "neutral", "strength": 5, "top_kol_signal": "No data"}

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        symbol = context.get("symbol", "UNKNOWN")
        twitter_data = await self.get_twitter_sentiment(symbol)

        return {
            "agent": "research",
            "summary": f"Twitter KOLs → {twitter_data['sentiment'].upper()} ({twitter_data['strength']}/10)",
            "arguments": [twitter_data['top_kol_signal']],
            "confidence": 0.85,
            "recommendation": f"{twitter_data['sentiment'].upper()} signal des meilleurs traders",
            "twitter_sentiment": twitter_data
        }
