"""
🎯 PORTFOLIO MANAGER V8.2 — PRO MODE
Gestion multi-portefeuilles + funding automatique du trading depuis rewards staking
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any

class PortfolioManager(BaseAgent):
    def __init__(self):
        super().__init__(
            name="portfolio_manager",
            role="Gestion multi-portefeuilles + transfert auto rewards staking vers trading"
        )
        self.wallets = {
            "trading": {"balance": 1000.0, "currency": "USDT", "assets": {}},
            "staking_savings": {"balance": 0.0, "currency": "USDT", "assets": {}},
        }

    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in ["portfolio", "wallet", "savings", "staking", "transfer", "funding"])

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        equity = context.get("equity", 1000.0)
        rewards = context.get("staking_rewards_usd", 0.0)

        if "transfer" in question.lower() or "funding" in question.lower():
            # Transfert rewards staking vers trading wallet
            transfer_amount = min(rewards * 0.7, equity * 0.15)  # 70% des rewards, max 15% du capital
            self.wallets["trading"]["balance"] += transfer_amount
            self.wallets["staking_savings"]["balance"] += rewards - transfer_amount

            return {
                "agent": self.name,
                "summary": f"✅ {transfer_amount:.2f}$ de rewards staking transférés automatiquement vers trading wallet",
                "transferred": round(transfer_amount, 2),
                "recommendation": "AUTO_FUND_TRADING",   # ← FIX 2
                "confidence": 0.96
            }

        # Status normal
        total = sum(w["balance"] for w in self.wallets.values())
        return {
            "agent": self.name,
            "summary": f"Portefeuilles : Trading ${self.wallets['trading']['balance']:.2f} | Savings ${self.wallets['staking_savings']['balance']:.2f} | Total ${total:.2f}",
            "all_wallets": self.wallets,
            "recommendation": "SHOW_ALL",   # ← FIX 2
            "confidence": 1.0
        }
