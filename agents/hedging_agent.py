"""
🎯 HEDGING AGENT V5 — Protection dynamique + Trailing intelligent + Hedging futures
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Spécialisation stricte : uniquement hedging, trailing, protection drawdown.
Hérite BaseAgent V3 + safe_respond + glossaire partagé + _is_in_my_domain
"""

from agents.base_agent import BaseAgent
from typing import Dict, Any
import asyncio

class HedgingAgent(BaseAgent):
    """Agent spécialisé dans la protection des positions (trailing, hedging, stop dynamique)"""

    def __init__(self):
        super().__init__(
            name="hedging",
            role="Protection dynamique des positions : trailing intelligent, hedging futures, stop-loss adaptatif, drawdown control"
        )
        self.max_hedge_pct = 0.35          # max 35 % du capital en hedge
        self.trailing_multiplier = 1.8     # trailing ultra-sensible
        self.drawdown_threshold = 0.08     # alerte à -8 %

    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in [
            "hedge", "hedging", "trailing", "protection", "drawdown",
            "stop loss", "stop-loss", "risk protect", "position protect"
        ])

    def explain_term(self, term: str) -> str:
        glossary = {
            "trailing": "Stop-loss qui suit le prix pour sécuriser les gains",
            "hedging": "Position opposée pour neutraliser le risque",
            "drawdown": "Perte maximale depuis le pic de capital",
        }
        return glossary.get(term.lower(), f"{term} : terme de protection de position")

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {"warning": "Hors domaine hedging", "agent": self.name}

        equity = context.get("equity", 1000.0)
        positions = context.get("positions", {})
        market_regime = context.get("market_regime", "NEUTRAL")
        shared_glossary = context.get("shared_glossary", {})

        # === Analyse hedging ===
        open_positions = len(positions)
        total_exposure = sum(p.get("amount_usd", 0) for p in positions.values())

        recommendation = "HOLD"
        hedge_needed = False
        trailing_active = False

        # Drawdown check
        peak = context.get("peak_equity", equity)
        drawdown = (equity - peak) / peak if peak > 0 else 0
        if drawdown <= -self.drawdown_threshold:
            hedge_needed = True
            recommendation = "HEDGE ACTIVÉ"

        # Trailing sur positions gagnantes
        for pos in positions.values():
            if pos.get("pnl", 0) > 0 and pos.get("side") == "LONG":
                trailing_active = True

        # Décision finale selon régime
        if market_regime == "BEAR" and open_positions > 0:
            hedge_needed = True
            recommendation = "HEDGE FUTURES (short) pour protéger"

        elif market_regime == "VOLATILE" and total_exposure > equity * 0.6:
            hedge_needed = True
            recommendation = "TRAILING + PARTIAL HEDGE"

        summary = f"HedgingAgent → {recommendation} | Drawdown: {drawdown*100:.1f}% | Positions: {open_positions} | Exposure: ${total_exposure:.0f}"

        return {
            "agent": self.name,
            "summary": summary,
            "recommendation": recommendation,
            "hedge_needed": hedge_needed,
            "trailing_active": trailing_active,
            "confidence": 0.92 if hedge_needed else 0.75,
            "action": "HEDGE" if hedge_needed else "TRAIL_ONLY",
            "details": {
                "drawdown_pct": round(drawdown*100, 2),
                "hedge_pct": self.max_hedge_pct if hedge_needed else 0,
                "regime": market_regime
            }
        }
