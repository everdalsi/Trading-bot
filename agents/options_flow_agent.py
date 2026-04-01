"""
🎯 OPTIONS FLOW AGENT — Flux d'options cryptos (signaux institutionnels)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Les options donnent des signaux directionnels des institutionnels:
- Achat massif de CALLS → anticipation haussière
- Achat massif de PUTS → hedging baissier
- Put/Call Ratio (PCR) > 1.2 → sur-hedging → potentiellement contrarian haussier
- Volatilité implicite (IV) spike → événement attendu

Sources: Deribit API (public data) + Binance options (BTCUSDT)
"""

import requests
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent
from logging_config import logger

DERIBIT_BASE = "https://www.deribit.com/api/v2/public"

class OptionsFlowAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="options_flow",
            description="Flux options: PCR Deribit, IV spike, OI puts vs calls — signaux institutionnels",
            role="Options flow: Put/Call Ratio, IV, OI options — smart money institutionnel directionnel"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 300.0

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        score, signals, metrics = await self._analyze_options_flow()

        if score > 0.60:
            recommendation = "BUY"
        elif score < 0.40:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        confidence = round(min(0.80, abs(score - 0.5) * 2 + 0.35), 2)
        pcr_str = f"PCR={metrics.get('put_call_ratio', 'N/A')}"

        result = {
            "agent": self.name,
            "summary": f"[OPTIONS] {pcr_str} | IV={metrics.get('iv_percent', 'N/A')}% → {recommendation}",
            "confidence": confidence,
            "recommendation": recommendation,
            "options_score": score,
            "metrics": metrics,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _analyze_options_flow(self) -> Tuple[float, List[str], Dict]:
        import asyncio
        loop = asyncio.get_event_loop()
        signals = []
        metrics = {}
        scores = []

        def _fetch_deribit_book_summary():
            try:
                r = requests.get(
                    f"{DERIBIT_BASE}/get_book_summary_by_currency",
                    params={"currency": "BTC", "kind": "option"},
                    timeout=6,
                )
                return r.json().get("result", [])
            except Exception:
                return []

        def _fetch_deribit_index():
            try:
                r = requests.get(
                    f"{DERIBIT_BASE}/get_index_price",
                    params={"index_name": "btc_usd"},
                    timeout=4,
                )
                return float(r.json().get("result", {}).get("index_price", 0))
            except Exception:
                return 0.0

        try:
            options_data, btc_price = await asyncio.gather(
                asyncio.wait_for(loop.run_in_executor(None, _fetch_deribit_book_summary), timeout=7),
                asyncio.wait_for(loop.run_in_executor(None, _fetch_deribit_index), timeout=5),
            )
        except Exception:
            return 0.5, ["Deribit options indisponible"], {}

        call_oi = 0.0
        put_oi = 0.0
        iv_values = []

        for option in options_data:
            try:
                instrument = option.get("instrument_name", "")
                oi = float(option.get("open_interest", 0))
                iv = option.get("mark_iv")
                if "-C" in instrument:
                    call_oi += oi
                elif "-P" in instrument:
                    put_oi += oi
                if iv:
                    iv_values.append(float(iv))
            except Exception:
                pass

        metrics["call_oi"] = round(call_oi, 0)
        metrics["put_oi"] = round(put_oi, 0)

        if call_oi + put_oi > 0:
            pcr = round(put_oi / (call_oi + 1), 3)
            metrics["put_call_ratio"] = pcr

            # PCR analysis — contrarian: trop de puts = sur-hedging = potentiellement haussier
            if pcr > 1.5:
                scores.append(0.63)  # Contrarian: over-hedging
                signals.append(f"PCR élevé {pcr:.2f} → sur-hedging institutionnel → contrarian haussier")
            elif pcr > 1.0:
                scores.append(0.52)
                signals.append(f"PCR modéré {pcr:.2f} → légère protection downside")
            elif pcr < 0.5:
                scores.append(0.38)  # Trop de calls = euphorie = prudence
                signals.append(f"PCR faible {pcr:.2f} → euphorie options CALL → prudence")
            else:
                scores.append(0.50)
                signals.append(f"PCR équilibré {pcr:.2f}")

        # IV analysis
        if iv_values:
            avg_iv = sum(iv_values) / len(iv_values)
            metrics["iv_percent"] = round(avg_iv, 1)
            if avg_iv > 100:
                scores.append(0.42)
                signals.append(f"IV très élevée ({avg_iv:.0f}%) → forte incertitude marché")
            elif avg_iv < 40:
                scores.append(0.60)
                signals.append(f"IV basse ({avg_iv:.0f}%) → marché calme, vol bon marché")
            else:
                scores.append(0.50)
                signals.append(f"IV normale ({avg_iv:.0f}%)")

        final_score = sum(scores) / len(scores) if scores else 0.5
        return round(final_score, 3), signals, metrics
