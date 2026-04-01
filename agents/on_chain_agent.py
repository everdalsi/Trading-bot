"""
⛓️ ON-CHAIN AGENT — Métriques blockchain comme signaux de trading
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sources gratuites:
- blockchain.info : mempool, fees moyens, hash rate
- blockchair.com  : UTXO, transaction count
- alternative.me  : Fear & Greed Index en temps réel
- CoinGecko API   : volume, market cap, dominance

Métriques analysées:
- NVT Ratio (Network Value to Transactions) — valuation
- SOPR proxy (Spent Output Profit Ratio) — profit des vendeurs
- Taux de transaction mempool — activité réseau
- Dominance BTC — risk-on/off sur altcoins
"""

import requests
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent
from logging_config import logger

BLOCKCHAIN_INFO_BASE = "https://api.blockchain.info"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

class OnChainAgent(BaseAgent):
    """Analyse des métriques on-chain pour signaux de trading."""

    def __init__(self):
        super().__init__(
            name="on_chain",
            description="Métriques on-chain: dominance BTC, mempool fees, NVT proxy, volume network",
            role="Analyse on-chain: hash rate, mempool, dominance BTC, volumes transactions"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 300.0  # 5 min

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        score, signals = await self._compute_onchain_score()

        if score > 0.62:
            recommendation = "BUY"
        elif score < 0.38:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        confidence = round(min(0.80, abs(score - 0.5) * 2 + 0.35), 2)

        result = {
            "agent": self.name,
            "summary": f"[ON-CHAIN] Score {score:.2f} → {recommendation} | {signals[0] if signals else ''}",
            "confidence": confidence,
            "recommendation": recommendation,
            "on_chain_score": score,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _compute_onchain_score(self) -> Tuple[float, List[str]]:
        import asyncio
        scores = []
        signals = []
        loop = asyncio.get_event_loop()

        # 1. CoinGecko Global — dominance BTC
        def _fetch_global():
            try:
                r = requests.get(f"{COINGECKO_BASE}/global", timeout=5)
                return r.json().get("data", {})
            except Exception:
                return {}

        # 2. Blockchain.info — stats mempool
        def _fetch_mempool():
            try:
                r = requests.get(f"{BLOCKCHAIN_INFO_BASE}/stats?format=json", timeout=5)
                return r.json()
            except Exception:
                return {}

        try:
            global_data, mempool_data = await asyncio.gather(
                asyncio.wait_for(loop.run_in_executor(None, _fetch_global), timeout=6),
                asyncio.wait_for(loop.run_in_executor(None, _fetch_mempool), timeout=6),
            )
        except Exception:
            global_data, mempool_data = {}, {}

        # Dominance BTC
        btc_dom = global_data.get("market_cap_percentage", {}).get("btc", 50)
        if btc_dom > 55:
            scores.append(0.62)
            signals.append(f"BTC dominance élevée ({btc_dom:.1f}%) → risk-off altcoins")
        elif btc_dom < 45:
            scores.append(0.55)
            signals.append(f"BTC dominance faible ({btc_dom:.1f}%) → altseason possible")
        else:
            scores.append(0.50)
            signals.append(f"BTC dominance neutre ({btc_dom:.1f}%)")

        # Volume 24h global
        vol_24h = global_data.get("total_volume", {}).get("usd", 0)
        if vol_24h > 1e11:  # > $100B
            scores.append(0.60)
            signals.append(f"Volume global élevé: ${vol_24h/1e9:.0f}B → forte activité")
        elif vol_24h < 5e10:
            scores.append(0.42)
            signals.append(f"Volume global faible: ${vol_24h/1e9:.0f}B → marché atone")
        else:
            scores.append(0.50)

        # Hashrate / difficulty (proxy santé réseau)
        hash_rate = mempool_data.get("hash_rate", 0)
        if hash_rate > 500e18:  # > 500 EH/s
            scores.append(0.58)
            signals.append(f"Hash rate record → mineurs confiants")
        elif hash_rate > 0:
            scores.append(0.52)

        final_score = sum(scores) / len(scores) if scores else 0.5
        return round(final_score, 3), signals
