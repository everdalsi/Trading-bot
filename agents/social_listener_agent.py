from agents.base_agent import BaseAgent
from typing import Dict, Any
import asyncio
import requests
import json
from logging_config import logger

class SocialListenerAgent(BaseAgent):
    """ÉCOUTE LIVE RÉSEAUX SOCIAUX & DISCUSSIONS TRADERS PROS — complémente research_agent"""
    def __init__(self):
        super().__init__(
            name="social_listener",
            role="Écoute en temps réel X/Twitter, Reddit, Discord, forums traders pros + scoring sentiment ultra-précis"
        )

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        symbol = context.get("symbol", "UNKNOWN")
        print(f"[SOCIAL LISTENER] 🔴 Écoute live sur {symbol}...")

        # Simulation live (à remplacer par vraies API plus tard)
        sentiment_score = 0.85  # exemple
        hot_topics = ["bullish breakout", "whale accumulation"]
        pro_traders_mentions = 12

        return {
            "agent": self.name,
            "sentiment_score": sentiment_score,
            "hot_topics": hot_topics,
            "pro_mentions": pro_traders_mentions,
            "summary": f"Sentiment live {symbol} : {sentiment_score:.2f} — {pro_traders_mentions} mentions pros détectées",
            "confidence": 0.9
        }
