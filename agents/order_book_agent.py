"""
📖 ORDER BOOK AGENT V1.0 — Analyse carnet d'ordres Binance temps réel
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Rôle : Analyse le bid/ask imbalance, détecte les gros murs d'ordres
       et émet des signaux BUY/SELL basés sur la pression réelle du marché.
Priorité : HAUTE — Signal basé sur le flux réel, pas les prix historiques.
"""

import os
import time
import asyncio
import requests
from typing import Dict, Any, Optional

from agents.base_agent import BaseAgent
from logging_config import logger

# Seuils d'imbalance
IMBALANCE_BUY_STRONG  = 0.60   # > 60% bids → signal BUY fort
IMBALANCE_BUY_WEAK    = 0.55   # 55-60% bids → signal BUY modéré
IMBALANCE_SELL_STRONG = 0.40   # < 40% bids → signal SELL fort
IMBALANCE_SELL_WEAK   = 0.45   # 40-45% bids → signal SELL modéré

# Seuil mur d'ordres (% du volume total)
WALL_THRESHOLD_PCT = 0.15   # Un ordre > 15% du volume total = "mur"

# Cache TTL
CACHE_TTL = 15.0   # 15 secondes (orderbook change vite)

BINANCE_BASE_URL = "https://api.binance.com"


class OrderBookAgent(BaseAgent):
    """
    Analyse le carnet d'ordres Binance en temps réel pour détecter :
    - Bid/Ask imbalance (déséquilibre acheteurs/vendeurs)
    - Gros murs d'ordres (résistances/supports cachés)
    - Absorption (gros vendeur absorbé par les bids)
    """

    def __init__(self):
        super().__init__(
            name="order_book",
            role=(
                "Analyse carnet d'ordres Binance — bid/ask imbalance, "
                "murs d'ordres, absorption — signal BUY/SELL/HOLD temps réel"
            )
        )
        self._cache: Dict[str, dict]       = {}   # symbol → data
        self._cache_ts: Dict[str, float]   = {}   # symbol → timestamp
        self._api_key    = os.getenv("BINANCE_API_KEY", "")
        self._api_secret = os.getenv("BINANCE_SECRET_KEY", "")

    # ── Domaine ────────────────────────────────────────────────────────────
    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in [
            "order book", "orderbook", "carnet", "bid", "ask",
            "imbalance", "mur", "wall", "order_book",
        ]) or super()._is_in_my_domain(question)

    # ── Fetch orderbook Binance ─────────────────────────────────────────────
    def _fetch_orderbook(self, symbol: str, limit: int = 20) -> Optional[dict]:
        """Récupère le top N du carnet d'ordres via REST API Binance."""
        now = time.time()
        sym_key = symbol.upper()

        # Cache valide
        if sym_key in self._cache and now - self._cache_ts.get(sym_key, 0) < CACHE_TTL:
            return self._cache[sym_key]

        try:
            url = f"{BINANCE_BASE_URL}/api/v3/depth"
            params = {"symbol": sym_key, "limit": limit}
            resp = requests.get(url, params=params, timeout=6)
            if resp.status_code != 200:
                logger.warning(f"[ORDER_BOOK] Binance HTTP {resp.status_code} for {sym_key}")
                return None
            data = resp.json()
            self._cache[sym_key]    = data
            self._cache_ts[sym_key] = now
            return data
        except Exception as e:
            logger.warning(f"[ORDER_BOOK] Fetch error {sym_key}: {e}")
            return None

    # ── Analyse imbalance ───────────────────────────────────────────────────
    def _analyze_imbalance(self, book: dict) -> dict:
        """
        Calcule le bid/ask imbalance sur les N premiers niveaux.
        Retourne: imbalance_ratio, signal, bid_volume, ask_volume, walls.
        """
        bids = book.get("bids", [])   # [[price, qty], ...]
        asks = book.get("asks", [])

        bid_volume = sum(float(b[1]) * float(b[0]) for b in bids[:10])  # USD
        ask_volume = sum(float(a[1]) * float(a[0]) for a in asks[:10])  # USD
        total      = bid_volume + ask_volume

        if total == 0:
            return {"imbalance": 0.5, "signal": "NEUTRAL", "bid_vol": 0, "ask_vol": 0, "walls": []}

        imbalance = bid_volume / total  # 0.0 → 1.0 (> 0.5 = plus de bids)

        # Détection des murs
        walls = []
        for side, orders in [("BID", bids[:10]), ("ASK", asks[:10])]:
            for price, qty in orders:
                notional = float(price) * float(qty)
                if notional / total > WALL_THRESHOLD_PCT:
                    walls.append({
                        "side": side,
                        "price": float(price),
                        "notional_usd": round(notional, 0),
                        "pct_of_total": round(notional / total * 100, 1),
                    })

        # Signal
        if imbalance >= IMBALANCE_BUY_STRONG:
            signal = "BUY_STRONG"
        elif imbalance >= IMBALANCE_BUY_WEAK:
            signal = "BUY_WEAK"
        elif imbalance <= IMBALANCE_SELL_STRONG:
            signal = "SELL_STRONG"
        elif imbalance <= IMBALANCE_SELL_WEAK:
            signal = "SELL_WEAK"
        else:
            signal = "NEUTRAL"

        return {
            "imbalance":      round(imbalance, 3),
            "bid_vol_usd":    round(bid_volume, 0),
            "ask_vol_usd":    round(ask_volume, 0),
            "signal":         signal,
            "walls":          walls[:4],              # Max 4 murs reportés
        }

    # ── Respond ─────────────────────────────────────────────────────────────
    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        symbol  = context.get("symbol", "BTCUSDT").upper()
        if not symbol.endswith("USDT"):
            symbol = symbol.replace("/", "") + "USDT"

        # Fetch en executor pour ne pas bloquer l'event loop
        loop = asyncio.get_event_loop()
        book = await loop.run_in_executor(None, lambda: self._fetch_orderbook(symbol))

        if not book:
            return {
                "agent":          self.name,
                "summary":        f"⚠️ Orderbook {symbol} indisponible — API Binance",
                "confidence":     0.0,
                "recommendation": "HOLD - Données orderbook manquantes",
                "imbalance":      0.5,
                "signal":         "NEUTRAL",
            }

        analysis = self._analyze_imbalance(book)
        imb      = analysis["imbalance"]
        signal   = analysis["signal"]
        walls    = analysis["walls"]

        # Formulation de la recommandation
        if signal == "BUY_STRONG":
            recommendation = "BUY — Pression acheteurs très forte (imbalance {:.0%})".format(imb)
            confidence     = 0.80
        elif signal == "BUY_WEAK":
            recommendation = "BUY FAIBLE — Légère pression acheteuse ({:.0%})".format(imb)
            confidence     = 0.60
        elif signal == "SELL_STRONG":
            recommendation = "NO TRADE / SELL — Pression vendeurs dominante ({:.0%})".format(1 - imb)
            confidence     = 0.80
        elif signal == "SELL_WEAK":
            recommendation = "HOLD — Légère pression vendeuse ({:.0%})".format(1 - imb)
            confidence     = 0.55
        else:
            recommendation = "HOLD — Marché équilibré (imbalance {:.0%})".format(imb)
            confidence     = 0.50

        wall_text = ""
        if walls:
            wall_text = " | Murs: " + ", ".join(
                f"{w['side']}@{w['price']:.2f}(${w['notional_usd']:.0f})"
                for w in walls[:2]
            )

        return {
            "agent":          self.name,
            "summary":        (
                f"📖 OrderBook {symbol} | Imbalance: {imb:.0%} bids | "
                f"Signal: {signal}{wall_text}"
            ),
            "arguments":      [
                f"Bid volume: ${analysis['bid_vol_usd']:,.0f}",
                f"Ask volume: ${analysis['ask_vol_usd']:,.0f}",
                f"Imbalance: {imb:.1%} côté acheteurs",
            ] + [f"Mur {w['side']} @{w['price']:.2f} (${w['notional_usd']:,.0f})" for w in walls[:2]],
            "risks":          ["Mur vendeur proche" if any(w["side"] == "ASK" for w in walls) else ""],
            "confidence":     confidence,
            "recommendation": recommendation,
            "imbalance":      imb,
            "signal":         signal,
            "walls":          walls,
            "bid_vol_usd":    analysis["bid_vol_usd"],
            "ask_vol_usd":    analysis["ask_vol_usd"],
        }

    # ── API publique ────────────────────────────────────────────────────────
    def get_imbalance(self, symbol: str) -> float:
        """Retourne l'imbalance (0.0-1.0) directement depuis bot.py."""
        book = self._fetch_orderbook(symbol)
        if not book:
            return 0.5
        return self._analyze_imbalance(book)["imbalance"]
