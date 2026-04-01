"""
⚙️ BLOCKCHAIN HEALTH AGENT — Santé du réseau Bitcoin
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Métriques de santé réseau:
- Mempool congestion (fees en satoshi/vbyte)
- Hash rate (sécurité réseau)
- Difficulté (ajustement tous les 2016 blocs)
- Temps de confirmation moyen
- Block fullness (congestion)

Un réseau congestionné = forte utilisation = haussier long terme
Fees trop élevés = frein à l'adoption = légèrement négatif court terme

Source: mempool.space API (gratuit)
"""

import requests
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent
from logging_config import logger

MEMPOOL_BASE = "https://mempool.space/api"

class BlockchainHealthAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="blockchain_health",
            description="Santé réseau Bitcoin: mempool fees, hash rate, difficulté, congestion — signal adoption",
            role="BTC network health: mempool/fees/hashrate — adoption forte = signal haussier long terme"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 300.0

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        score, signals, metrics = await self._check_network_health()

        if score > 0.60:
            recommendation = "BUY"
        elif score < 0.40:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        confidence = round(min(0.72, abs(score - 0.5) * 2 + 0.28), 2)
        fee_str = f"Fees={metrics.get('fastest_fee', '?')} sat/vB"

        result = {
            "agent": self.name,
            "summary": f"[BTC HEALTH] {fee_str} | Mempool={metrics.get('mempool_size', '?')} tx → {recommendation}",
            "confidence": confidence,
            "recommendation": recommendation,
            "health_score": score,
            "metrics": metrics,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _check_network_health(self) -> Tuple[float, List[str], Dict]:
        import asyncio
        loop = asyncio.get_event_loop()
        signals = []
        metrics = {}
        scores = []

        def _fetch_mempool():
            try:
                r = requests.get(f"{MEMPOOL_BASE}/mempool", timeout=5)
                return r.json()
            except Exception:
                return {}

        def _fetch_fees():
            try:
                r = requests.get(f"{MEMPOOL_BASE}/v1/fees/recommended", timeout=5)
                return r.json()
            except Exception:
                return {}

        def _fetch_hashrate():
            try:
                r = requests.get(f"{MEMPOOL_BASE}/v1/mining/hashrate/3d", timeout=5)
                data = r.json()
                rates = data.get("hashrates", [])
                if rates:
                    return float(rates[-1].get("avgHashrate", 0))
                return 0.0
            except Exception:
                return 0.0

        try:
            mempool_data, fees_data, hash_rate = await asyncio.gather(
                asyncio.wait_for(loop.run_in_executor(None, _fetch_mempool), timeout=6),
                asyncio.wait_for(loop.run_in_executor(None, _fetch_fees), timeout=6),
                asyncio.wait_for(loop.run_in_executor(None, _fetch_hashrate), timeout=6),
            )
        except Exception:
            return 0.5, ["Network data indisponible"], {}

        # Mempool
        mempool_size = mempool_data.get("count", 0)
        metrics["mempool_size"] = mempool_size

        if mempool_size > 100_000:
            scores.append(0.55)
            signals.append(f"Mempool congestionné ({mempool_size:,} tx) → forte demande réseau")
        elif mempool_size < 5_000:
            scores.append(0.52)
            signals.append(f"Mempool calme ({mempool_size:,} tx) → faible congestion")
        else:
            scores.append(0.50)

        # Fees
        fastest_fee = fees_data.get("fastestFee", 0)
        metrics["fastest_fee"] = fastest_fee
        if fastest_fee > 100:
            scores.append(0.42)
            signals.append(f"Fees très élevés: {fastest_fee} sat/vB → frein adoption court terme")
        elif fastest_fee < 5:
            scores.append(0.58)
            signals.append(f"Fees très faibles: {fastest_fee} sat/vB → réseau accessible")
        else:
            scores.append(0.52)

        # Hash rate
        if hash_rate > 0:
            hash_eh = hash_rate / 1e18
            metrics["hashrate_eh"] = round(hash_eh, 1)
            if hash_eh > 600:
                scores.append(0.60)
                signals.append(f"Hash rate record: {hash_eh:.0f} EH/s → réseau ultra-sécurisé")
            elif hash_eh > 400:
                scores.append(0.55)
                signals.append(f"Hash rate fort: {hash_eh:.0f} EH/s")
            else:
                scores.append(0.50)

        final_score = sum(scores) / len(scores) if scores else 0.5
        return round(final_score, 3), signals, metrics
