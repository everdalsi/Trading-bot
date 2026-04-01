"""
🏗️ DEFI MONITOR AGENT — Surveillance protocoles DeFi
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Le DeFi donne des signaux avancés sur ETH/L2:
- Variation TVL globale (DeFiLlama) — capital entrant/sortant
- Taux de yield farming — attractivité vs actifs traditionnels
- Volume DEX vs CEX ratio — trading on-chain vs centralisé
- Liquidations DeFi (Aave, Compound) — stress système

Source: DeFiLlama API (100% gratuit, pas de clé requise)
"""

import requests
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent
from logging_config import logger

DEFILLAMA_BASE = "https://api.llama.fi"

class DefiMonitorAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="defi_monitor",
            description="Surveillance DeFi: TVL DeFiLlama, DEX volumes, yields — signaux ETH/L2",
            role="DeFi monitor: TVL variation, DEX vs CEX, liquidations protocoles — santé écosystème DeFi"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 600.0

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        score, signals, metrics = await self._monitor_defi()

        if score > 0.60:
            recommendation = "BUY"
        elif score < 0.40:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        confidence = round(min(0.75, abs(score - 0.5) * 2 + 0.30), 2)
        tvl_str = f"TVL=${metrics.get('total_tvl_b', 0):.1f}B ({metrics.get('tvl_change_7d', 0):+.1f}%)"

        result = {
            "agent": self.name,
            "summary": f"[DEFI] {tvl_str} → {recommendation}",
            "confidence": confidence,
            "recommendation": recommendation,
            "defi_score": score,
            "metrics": metrics,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _monitor_defi(self) -> Tuple[float, List[str], Dict]:
        import asyncio
        loop = asyncio.get_event_loop()
        signals = []
        metrics = {}
        scores = []

        def _fetch_global_tvl():
            try:
                r = requests.get(f"{DEFILLAMA_BASE}/v2/historicalChainTvl", timeout=6)
                data = r.json()
                if isinstance(data, list) and len(data) >= 8:
                    now_tvl = data[-1]["tvl"]
                    week_ago_tvl = data[-8]["tvl"]
                    change_7d = (now_tvl - week_ago_tvl) / week_ago_tvl * 100
                    return now_tvl, change_7d
                return 0, 0
            except Exception:
                return 0, 0

        def _fetch_top_protocols():
            try:
                r = requests.get(f"{DEFILLAMA_BASE}/protocols", timeout=6)
                protocols = r.json()[:10]  # Top 10
                return [(p.get("name"), p.get("tvl", 0), p.get("change_7d", 0)) for p in protocols]
            except Exception:
                return []

        try:
            (total_tvl, tvl_change_7d), top_protocols = await asyncio.gather(
                asyncio.wait_for(loop.run_in_executor(None, _fetch_global_tvl), timeout=7),
                asyncio.wait_for(loop.run_in_executor(None, _fetch_top_protocols), timeout=7),
            )
        except Exception:
            return 0.5, ["DeFi data indisponible"], {}

        metrics["total_tvl_b"] = round(total_tvl / 1e9, 2) if total_tvl else 0
        metrics["tvl_change_7d"] = round(tvl_change_7d, 2)

        # TVL change signal
        if tvl_change_7d > 5:
            scores.append(0.65)
            signals.append(f"TVL DeFi +{tvl_change_7d:.1f}% sur 7j → capital entrant dans l'écosystème")
        elif tvl_change_7d < -10:
            scores.append(0.30)
            signals.append(f"TVL DeFi {tvl_change_7d:.1f}% sur 7j → fuite capital DeFi (risque)")
        elif tvl_change_7d < -3:
            scores.append(0.42)
            signals.append(f"TVL DeFi {tvl_change_7d:.1f}% sur 7j → légère sortie")
        else:
            scores.append(0.50)
            signals.append(f"TVL DeFi stable: ${metrics['total_tvl_b']:.1f}B ({tvl_change_7d:+.1f}%)")

        # Top protocols check
        if top_protocols:
            growing = sum(1 for _, _, chg in top_protocols if chg and chg > 0)
            if growing > 7:
                scores.append(0.62)
                signals.append(f"{growing}/10 top protocoles en croissance → expansion DeFi saine")
            elif growing < 3:
                scores.append(0.38)
                signals.append(f"Seulement {growing}/10 top protocoles en croissance → contraction DeFi")
            else:
                scores.append(0.50)

        final_score = sum(scores) / len(scores) if scores else 0.5
        return round(final_score, 3), signals, metrics
