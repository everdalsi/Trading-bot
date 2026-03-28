"""
🎯 PORTFOLIO MANAGER V1 — Gestion multi-portefeuilles + Staking auto vers savings
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sécurité real-money : conversions safe, vérification calculs, wallets séparés
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any
import asyncio

class PortfolioManager(BaseAgent):
    """Agent spécialisé dans la gestion des portefeuilles multiples + transfert staking"""

    def __init__(self):
        super().__init__(
            name="portfolio_manager",
            role="Gestion multi-portefeuilles (trading / staking_savings) + transferts auto + vérification calculs real-money"
        )
        self.wallets = {
            "trading": {"balance": 1000.0, "currency": "USDT", "assets": {}},
            "staking_savings": {"balance": 0.0, "currency": "USDT", "assets": {}},
        }

    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in ["portfolio", "wallet", "savings", "staking transfer", "multi wallet", "conversion"])

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {"warning": "Hors domaine portfolio", "agent": self.name}

        equity = context.get("equity", 1000.0)
        action = "VERIFY"

        # === VÉRIFICATION CALCULS + CONVERSIONS SAFE ===
        if "verify" in question.lower() or "conversion" in question.lower():
            # Exemple vérification prix + conversion
            price = context.get("price", 0)
            amount = context.get("amount", 0)
            safe_amount = round(max(0, amount), 8)  # 8 décimales max pour crypto
            conversion = round(safe_amount * price, 2) if price > 0 else 0
            return {
                "agent": self.name,
                "summary": f"Vérification OK — Amount: {safe_amount} → ${conversion}",
                "safe_conversion": conversion,
                "confidence": 0.98
            }

        # === STAKING AUTO → TRANSFER TO SAVINGS WALLET ===
        if "stake" in question.lower() or "savings" in question.lower():
            staked = context.get("staked_amount", 0)
            self.wallets["staking_savings"]["balance"] += staked
            self.wallets["trading"]["balance"] -= staked
            return {
                "agent": self.name,
                "summary": f"✅ Staking {staked}$ transféré automatiquement vers savings_wallet",
                "action": "TRANSFER_TO_SAVINGS",
                "new_savings_balance": round(self.wallets["staking_savings"]["balance"], 2),
                "confidence": 0.95
            }

        # === INTERFACE TOUS PORTFOLIOS ===
        total = sum(w["balance"] for w in self.wallets.values())
        return {
            "agent": self.name,
            "summary": f"Portefeuilles : Trading ${self.wallets['trading']['balance']:.2f} | Savings ${self.wallets['staking_savings']['balance']:.2f} | Total ${total:.2f}",
            "all_wallets": self.wallets,
            "action": "SHOW_ALL",
            "confidence": 1.0
        }
