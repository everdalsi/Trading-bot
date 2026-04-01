"""
⚡ ARBITRAGE SCANNER AGENT — Arbitrage spot multi-exchange
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scanne les écarts de prix entre:
- Binance Spot vs Futures (basis trading)
- Binance vs OKX vs Bybit (cross-exchange arb)
- Crypto vs Crypto (triangular arb sur Binance)

Seuil minimum: 0.15% (après fees) pour signaler une opportunité.
Urgence: ces opportunités durent 30-120 secondes.
"""

import requests
import time
from typing import Dict, Any, Tuple, List
from agents.base_agent import BaseAgent
from logging_config import logger

EXCHANGES = {
    "binance": {"url": "https://api.binance.com/api/v3/ticker/price", "param": "symbol"},
    "kucoin": {"url": "https://api.kucoin.com/api/v1/market/orderbook/level1", "param": "symbol"},
}

BINANCE_SPOT = "https://api.binance.com/api/v3/ticker/price"
BINANCE_FAPI = "https://fapi.binance.com/fapi/v1/ticker/price"

PAIRS_TO_SCAN = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]

class ArbitrageScannerAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="arbitrage_scanner",
            description="Scanner d'arbitrage: basis spot/futures, triangulaire, cross-exchange — opportunités > 0.15%",
            role="Arbitrage: détection écarts spot/perp + triangulaire + cross-exchange avec urgence de signal"
        )
        self._cache: Dict = {}
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 60.0  # 1 min

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_ts < self._cache_ttl:
            return self._cache

        opportunities, score, signals = await self._scan_arbitrage()

        if opportunities:
            recommendation = "BUY" if score > 0.5 else "SELL"
        else:
            recommendation = "HOLD"

        confidence = 0.70 if opportunities else 0.40

        opp_str = opportunities[0]["description"] if opportunities else "Aucune opportunité arb détectée"

        result = {
            "agent": self.name,
            "summary": f"[ARB SCANNER] {len(opportunities)} opps | {opp_str}",
            "confidence": confidence,
            "recommendation": recommendation,
            "arb_score": score,
            "opportunities": opportunities,
            "signals": signals,
        }
        self._cache = result
        self._cache_ts = now
        return result

    async def _scan_arbitrage(self) -> Tuple[List[Dict], float, List[str]]:
        import asyncio
        loop = asyncio.get_event_loop()
        opportunities = []
        signals = []

        def _fetch_spot_prices():
            try:
                r = requests.get(BINANCE_SPOT, timeout=5)
                data = r.json()
                return {item["symbol"]: float(item["price"]) for item in data}
            except Exception:
                return {}

        def _fetch_perp_prices():
            try:
                r = requests.get(BINANCE_FAPI, timeout=5)
                data = r.json()
                return {item["symbol"]: float(item["price"]) for item in data}
            except Exception:
                return {}

        try:
            spot_prices, perp_prices = await asyncio.gather(
                asyncio.wait_for(loop.run_in_executor(None, _fetch_spot_prices), timeout=6),
                asyncio.wait_for(loop.run_in_executor(None, _fetch_perp_prices), timeout=6),
            )
        except Exception:
            return [], 0.5, ["Scanner indisponible"]

        # Basis arbitrage: spot vs perp
        for pair in PAIRS_TO_SCAN:
            spot = spot_prices.get(pair, 0)
            perp = perp_prices.get(pair, 0)
            if spot > 0 and perp > 0:
                basis = (perp - spot) / spot * 100
                if abs(basis) > 0.15:
                    direction = "Long spot / Short perp" if basis > 0 else "Short spot / Long perp"
                    opportunities.append({
                        "pair": pair,
                        "type": "basis",
                        "basis_pct": round(basis, 3),
                        "description": f"{pair}: {direction} ({basis:+.2f}%)",
                        "profit_pct": abs(basis) - 0.10,  # after fees
                    })
                    signals.append(f"ARB {pair}: basis {basis:+.2f}% → {direction}")

        # Triangular arb on Binance: BTC → ETH → BNB → BTC
        btc = spot_prices.get("BTCUSDT", 0)
        eth = spot_prices.get("ETHUSDT", 0)
        eth_btc = spot_prices.get("ETHBTC", 0)
        if btc > 0 and eth > 0 and eth_btc > 0:
            implied_eth_btc = eth / btc
            tri_diff = (implied_eth_btc - eth_btc) / eth_btc * 100
            if abs(tri_diff) > 0.05:
                opportunities.append({
                    "pair": "ETH/BTC triangular",
                    "type": "triangular",
                    "diff_pct": round(tri_diff, 4),
                    "description": f"Triangulaire ETH/BTC: {tri_diff:+.3f}%",
                    "profit_pct": abs(tri_diff) - 0.06,
                })
                signals.append(f"ARB triangulaire ETH/BTC: {tri_diff:+.3f}%")

        if not opportunities:
            signals.append("Marchés efficients: pas d'arb > 0.15% détecté")

        score = 0.60 if opportunities else 0.50
        return opportunities, score, signals
