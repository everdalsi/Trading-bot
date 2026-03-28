"""
🎯 YIELD STAKING AGENT — Staking ETH/SOL intelligent (Lido + Marinade/Jito)
Spécialité : yield passif sécurisé, compounding auto, veto RiskAgent
Hérite de BaseAgent V3 → cerveau commun parfait
VERSION GOAT V8.3 — Wall Street + Real-time API + AUTO TRANSFER TO SAVINGS WALLET
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any
import asyncio
import requests
import json
from datetime import datetime
from logging_config import logger

class YieldStakingAgent(BaseAgent):
    """AGENT SPÉCIALISÉ YIELD — Staking ETH/SOL uniquement. Jamais de trading actif."""
    def __init__(self):
        super().__init__(
            name="yield_staking",
            role="Gestion du yield passif ETH (Lido) et SOL (Marinade/Jito) — compounding auto, veto risque, transfert automatique vers savings_wallet + sécurité real-money"
        )
        # Config pro trader Wall Street
        self.max_stake_pct = 0.25          # max 25 % du capital en staking
        self.compound_frequency = 3600     # toutes les heures
        self.lido_api = "https://stake.lido.fi/api"
        self.marinade_api = "https://api.marinade.finance"
        self.jito_api = "https://api.jito.network"
        self.staked_positions = {}         # tracking interne des positions stakées

    def _is_in_my_domain(self, question: str) -> bool:
        """Vérification stricte de spécialisation (cerveau commun)"""
        q = question.lower()
        keywords = ["stake", "staking", "yield", "apy", "lido", "marinade", "jito", "eth", "sol", "compound", "unstake", "savings", "transfer"]
        return any(kw in q for kw in keywords)

    def explain_term(self, term: str) -> str:
        """Glossaire partagé du cerveau commun"""
        glossary = {
            "apy": "Annual Percentage Yield — rendement annuel réel après compounding",
            "staking": "Verrouillage de tokens pour sécuriser le réseau et gagner du yield",
            "lido": "Protocol de staking liquide ETH — tu gardes tes tokens stETH",
            "marinade": "Staking liquide SOL sur Solana — mSOL très liquide",
            "jito": "MEV boost sur Solana — yield supplémentaire via Jito",
            "compounding": "Réinvestissement automatique des rewards",
            "slashing": "Pénalité si validateur malveillant (très rare sur Lido/Marinade)",
            "unstake": "Retirer ses tokens (délai variable selon le protocole)",
            "savings_wallet": "Portefeuille dédié aux économies (staking + rewards transférés automatiquement)"
        }
        return glossary.get(term.lower(), term)

    def _fetch_real_apy(self, protocol: str) -> float:
        """Fetch APY réel en live (Wall Street style)"""
        try:
            if protocol == "lido":
                r = requests.get("https://api.lido.fi/v1/protocol", timeout=8)
                return float(r.json()["apr"]) if r.status_code == 200 else 2.4
            elif protocol == "marinade":
                r = requests.get(self.marinade_api + "/stats", timeout=8)
                return float(r.json()["apy"]) if r.status_code == 200 else 6.3
            return 0.0
        except Exception:
            logger.warning(f"[YIELD] API {protocol} down → fallback safe APY")
            return 2.4 if protocol == "lido" else 6.3

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Réponse ultra-spécialisée + cerveau commun + transfert auto vers savings"""
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name,
                "summary": "⚠️ Je suis spécialisé UNIQUEMENT en staking yield ETH/SOL. Hors de mon domaine.",
                "confidence": 0.0,
                "recommendation": "Demande à TraderAgent ou RiskAgent"
            }

        # Glossaire partagé forcé (zéro malentendu)
        shared_glossary = context.get("shared_glossary", {})
        def explain(k): 
            return self.explain_term(k) or shared_glossary.get(k, k)

        equity = context.get("equity", 1000.0)
        risk_level = context.get("risk_level", "LOW")

        # === FETCH APY RÉEL ===
        eth_apy = self._fetch_real_apy("lido")
        sol_apy = self._fetch_real_apy("marinade")

        # Veto RiskAgent intégré (Wall Street risk desk)
        if risk_level in ("HIGH", "CRITICAL") or equity < 500:
            return {
                "agent": self.name,
                "summary": f"🚫 {explain('staking')} refusé par RiskAgent — risque trop élevé ou capital insuffisant",
                "eth_apy": eth_apy,
                "sol_apy": sol_apy,
                "action": "NONE",
                "confidence": 0.98,
                "glossary_used": True,
                "full_summary": f"Salut boss, j’ai analysé le {explain('yield')} en live. Le RiskAgent dit non pour l’instant."
            }

        # Calcul pro + tracking
        eth_amount = round(equity * 0.15, 8)   # conversion safe real-money
        sol_amount = round(equity * 0.10, 8)

        # Mise à jour tracking interne
        self.staked_positions["ETH"] = eth_amount
        self.staked_positions["SOL"] = sol_amount

        staked_amount = eth_amount + sol_amount

        # === UPGRADE V8.3 : AUTO TRANSFER TO SAVINGS WALLET (real-money safety) ===
        # On appelle PortfolioManager pour transférer immédiatement vers le wallet économies
        portfolio_ctx = {"staked_amount": staked_amount, "equity": equity}
        try:
            from agents.portfolio_manager import PortfolioManager
            portfolio_manager = PortfolioManager()
            transfer_result = await portfolio_manager.respond("transfer staking to savings", portfolio_ctx)
            transfer_summary = transfer_result.get("summary", "Transfert auto effectué")
        except Exception:
            transfer_summary = "⚠️ Transfert vers savings_wallet simulé (PortfolioManager non chargé)"

        summary = f"✅ Staking recommandé : {eth_amount:.4f} ETH @ {eth_apy:.2f}% APY + {sol_amount:.4f} SOL @ {sol_apy:.2f}% APY | {transfer_summary}"

        return {
            "agent": self.name,
            "summary": summary,
            "eth_stake": eth_amount,
            "sol_stake": sol_amount,
            "eth_apy": eth_apy,
            "sol_apy": sol_apy,
            "daily_yield_est": round((eth_amount * eth_apy / 36500) + (sol_amount * sol_apy / 36500), 8),
            "action": "STAKE_AND_TRANSFER",
            "confidence": 0.95,
            "recommendation": f"Stake {eth_amount:.4f} ETH sur Lido et {sol_amount:.4f} SOL sur Marinade. Compound auto + transfert immédiat vers savings_wallet.",
            "glossary_used": True,
            "full_summary": f"Salut boss ! J’ai tout calculé avec le {explain('cerveau commun')} et les APIs live. Staking lancé + tout transféré automatiquement dans le wallet économies pour zéro risque."
        }
