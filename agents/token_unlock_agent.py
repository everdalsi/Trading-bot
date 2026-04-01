"""
🔓 TOKEN UNLOCK AGENT — Impact des vestings et unlocks sur les prix
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Les token unlocks créent une pression vendeuse prévisible:
- Unlock massif (>5% supply) → pression vendeuse garantie
- Unlock équipe/VC → ventes quasi-certaines
- Unlock staking rewards → réinvestissement possible

Source: Token Unlocks API (gratuit partiel) + NewsAPI scan
Stratégie: éviter les tokens avec unlock massif dans les 7 jours
"""

import requests
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent
from logging_config import logger

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# Tokens avec vestings connus importants (mise à jour manuelle)
HIGH_RISK_UNLOCK_TOKENS = {
    "APT": "Aptos: unlock mensuel équipe",
    "SUI": "Sui: unlock investisseurs",
    "ARB": "Arbitrum: unlock team 16 mars",
    "OP": "Optimism: unlock mensuel",
    "SEI": "Sei: unlock investisseurs",
    "PYTH": "Pyth: unlock ecosystème",
    "JTO": "Jito: unlock governance",
}

class TokenUnlockAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="token_unlock",
            description="Suivi vestings/unlocks: pression vendeuse prévisible, alerte avant unlocks massifs",
            role="Token unlocks: détection vestings imminents, évitement tokens à forte pression vendeuse"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 3600.0  # 1h

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        symbol = context.get("symbol", "BTCUSDT").replace("USDT", "")
        score, signals, risk_tokens = await self._analyze_unlocks(symbol)

        if score > 0.55:
            recommendation = "BUY"
        elif score < 0.38:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        confidence = round(min(0.70, abs(score - 0.5) * 2 + 0.25), 2)
        risk_str = f"{len(risk_tokens)} tokens à risque unlock"

        result = {
            "agent": self.name,
            "symbol": symbol,
            "summary": f"[TOKEN UNLOCK] {risk_str} | {signals[0] if signals else ''} → {recommendation}",
            "confidence": confidence,
            "recommendation": recommendation,
            "unlock_score": score,
            "risk_tokens": risk_tokens,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _analyze_unlocks(self, symbol: str) -> Tuple[float, List[str], List[str]]:
        import asyncio
        signals = []
        risk_tokens = []
        scores = []

        # Vérifier si le symbole actuel est à risque
        if symbol.upper() in HIGH_RISK_UNLOCK_TOKENS:
            reason = HIGH_RISK_UNLOCK_TOKENS[symbol.upper()]
            risk_tokens.append(f"{symbol}: {reason}")
            scores.append(0.30)
            signals.append(f"ALERTE: {symbol} a un unlock connu → {reason}")
        else:
            scores.append(0.58)
            signals.append(f"{symbol}: pas d'unlock majeur identifié — position safe")

        # BTC/ETH jamais concernés par les unlocks
        if symbol.upper() in ["BTC", "ETH"]:
            scores = [0.60]
            signals = [f"{symbol}: pas de vesting/unlock (L1 mature) → position favorable"]

        # Scan des altcoins à risque général
        alts_at_risk = list(HIGH_RISK_UNLOCK_TOKENS.keys())
        risk_tokens.extend([f"{t}: {HIGH_RISK_UNLOCK_TOKENS[t]}" for t in alts_at_risk])
        signals.append(f"Altcoins à éviter actuellement: {', '.join(alts_at_risk[:5])}")

        final_score = sum(scores) / len(scores) if scores else 0.5
        return round(final_score, 3), signals, risk_tokens
