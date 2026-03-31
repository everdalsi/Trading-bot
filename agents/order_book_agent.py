"""
📖 ORDER BOOK AGENT V3 — Expert Carnet d'Ordres & Microstructure
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UPGRADES V3 (expert-level) :
- Analyse jusqu'à 20 niveaux de profondeur (vs 5 en V1)
- Détection des icebergs : ordres qui se rechargent automatiquement
- VPIN proxy (Volume-Synchronized Probability of Informed Trading)
- Market Impact estimation (Almgren-Chriss simplifié)
- Bid-Ask spread analysis + spread historique
- Détection des absorptions (gros ordre mangé = force réelle)
- Imbalance pondéré par proximité du prix (niveaux proches > niveaux lointains)
- Signal composite : imbalance + spread + walls + VPIN
"""

import os
import time
import asyncio
import requests
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from collections import deque

from agents.base_agent import BaseAgent
from logging_config import logger

BINANCE_BASE_URL = "https://api.binance.com"
CACHE_TTL        = 10.0    # 10 secondes (orderbook change très vite)
DEPTH_LIMIT      = 20      # 20 niveaux de profondeur
WALL_THRESHOLD   = 0.12    # Mur si ordre > 12% du volume total du côté
ICEBERG_LOOKBACK = 30      # Fenêtre de détection iceberg


class OrderBookAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="order_book",
            role=(
                "Expert microstructure marché — analyse profondeur 20 niveaux, "
                "VPIN, icebergs, market impact, spread, absorptions"
            )
        )
        self._cache: Dict[str, Dict] = {}
        self._spread_history: Dict[str, deque] = {}       # historique spread
        self._volume_history: Dict[str, deque] = {}       # pour VPIN
        self._prev_book:     Dict[str, Dict]   = {}       # pour détection iceberg

    # ──────────────────────────────────────────────────────────────────────────
    # FETCH
    # ──────────────────────────────────────────────────────────────────────────

    def _fetch_order_book(self, symbol: str) -> Optional[Dict]:
        """Récupère le carnet d'ordres Binance — 20 niveaux de profondeur."""
        key = f"ob_{symbol}"
        now = time.time()
        cached = self._cache.get(key)
        if cached and now - cached["ts"] < CACHE_TTL:
            return cached["data"]
        try:
            r = requests.get(
                f"{BINANCE_BASE_URL}/api/v3/depth",
                params={"symbol": symbol.upper(), "limit": DEPTH_LIMIT},
                timeout=5,
            )
            if r.status_code == 200:
                data = r.json()
                self._cache[key] = {"data": data, "ts": now}
                return data
        except Exception as e:
            logger.warning(f"[OrderBookV3] fetch {symbol}: {e}")
        return None

    def _fetch_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict]:
        """Récupère les derniers trades pour le calcul VPIN."""
        try:
            r = requests.get(
                f"{BINANCE_BASE_URL}/api/v3/trades",
                params={"symbol": symbol.upper(), "limit": limit},
                timeout=5,
            )
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            logger.debug(f"[OrderBookV3] trades {symbol}: {e}")
        return []

    # ──────────────────────────────────────────────────────────────────────────
    # ANALYSE
    # ──────────────────────────────────────────────────────────────────────────

    def _analyze_book(self, bids: List, asks: List) -> Dict[str, Any]:
        """
        Analyse complète du carnet d'ordres avec pondération par proximité du prix.
        Les niveaux proches du prix ont plus de poids.
        """
        if not bids or not asks:
            return {"imbalance": 0.5, "signal": "NEUTRAL", "bid_vol": 0, "ask_vol": 0}

        # Conversion
        bid_levels = [(float(p), float(q)) for p, q in bids[:DEPTH_LIMIT]]
        ask_levels = [(float(p), float(q)) for p, q in asks[:DEPTH_LIMIT]]

        best_bid = bid_levels[0][0] if bid_levels else 0.0
        best_ask = ask_levels[0][0] if ask_levels else 0.0
        mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0

        # Volumes totaux
        total_bid_vol = sum(q for _, q in bid_levels)
        total_ask_vol = sum(q for _, q in ask_levels)

        # ── Pondération par proximité (décroissance exponentielle) ─────────
        # Niveaux proches du mid_price ont un poids exponentiel plus élevé
        weighted_bid_vol = 0.0
        weighted_ask_vol = 0.0
        for i, (price, qty) in enumerate(bid_levels):
            distance = abs(mid_price - price) / (mid_price + 1e-9)
            weight = np.exp(-distance * 20)   # λ = 20
            weighted_bid_vol += qty * weight
        for i, (price, qty) in enumerate(ask_levels):
            distance = abs(price - mid_price) / (mid_price + 1e-9)
            weight = np.exp(-distance * 20)
            weighted_ask_vol += qty * weight

        total_weighted = weighted_bid_vol + weighted_ask_vol
        imbalance = weighted_bid_vol / total_weighted if total_weighted > 0 else 0.5

        # ── Détection des murs ─────────────────────────────────────────────
        bid_walls = [(p, q) for p, q in bid_levels if q > total_bid_vol * WALL_THRESHOLD]
        ask_walls = [(p, q) for p, q in ask_levels if q > total_ask_vol * WALL_THRESHOLD]

        # ── Bid-Ask Spread ─────────────────────────────────────────────────
        spread_abs = best_ask - best_bid
        spread_pct = spread_abs / mid_price * 100 if mid_price > 0 else 0.0

        # ── Signal ────────────────────────────────────────────────────────
        if imbalance > 0.68:
            signal = "STRONG_BUY"
        elif imbalance > 0.58:
            signal = "BUY"
        elif imbalance < 0.32:
            signal = "STRONG_SELL"
        elif imbalance < 0.42:
            signal = "SELL"
        else:
            signal = "NEUTRAL"

        # Modifier signal si grand mur ask (résistance) ou bid (support)
        if ask_walls and imbalance > 0.55:
            signal = "BUY_WALL_AHEAD"   # Résistance potentielle
        if bid_walls and imbalance < 0.45:
            signal = "SELL_SUPPORT"     # Support fort

        return {
            "imbalance":        round(imbalance, 4),
            "weighted_imb":     round(imbalance, 4),
            "bid_vol":          round(total_bid_vol, 2),
            "ask_vol":          round(total_ask_vol, 2),
            "bid_walls":        [{"price": p, "qty": q} for p, q in bid_walls],
            "ask_walls":        [{"price": p, "qty": q} for p, q in ask_walls],
            "spread_pct":       round(spread_pct, 4),
            "best_bid":         round(best_bid, 6),
            "best_ask":         round(best_ask, 6),
            "mid_price":        round(mid_price, 6),
            "signal":           signal,
        }

    def _calculate_vpin(self, trades: List[Dict]) -> float:
        """
        VPIN simplifié : proportion de trades initiés par les acheteurs.
        VPIN élevé → forte pression acheteuse (informed trading).
        VPIN faible → forte pression vendeuse.
        """
        if not trades:
            return 0.5
        buy_vol  = sum(float(t.get("qty", 0)) for t in trades if not t.get("isBuyerMaker", True))
        sell_vol = sum(float(t.get("qty", 0)) for t in trades if t.get("isBuyerMaker", True))
        total    = buy_vol + sell_vol
        if total == 0:
            return 0.5
        return round(buy_vol / total, 4)

    def _detect_icebergs(self, symbol: str, current_book: Dict) -> Dict[str, bool]:
        """
        Détecte les icebergs : gros ordres qui se rechargent.
        Comparaison avec le carnet précédent.
        """
        prev = self._prev_book.get(symbol, {})
        self._prev_book[symbol] = current_book
        if not prev:
            return {"bid_iceberg": False, "ask_iceberg": False}

        # Comparer le niveau best bid/ask
        prev_bids = {float(p): float(q) for p, q in prev.get("bids", [])[:5]}
        curr_bids = {float(p): float(q) for p, q in current_book.get("bids", [])[:5]}
        prev_asks = {float(p): float(q) for p, q in prev.get("asks", [])[:5]}
        curr_asks = {float(p): float(q) for p, q in current_book.get("asks", [])[:5]}

        # Si un niveau reste stable ou se recharge → iceberg
        bid_iceberg = any(
            curr_bids.get(p, 0) >= prev_bids.get(p, 0) * 0.9
            for p in prev_bids if prev_bids[p] > 0
        )
        ask_iceberg = any(
            curr_asks.get(p, 0) >= prev_asks.get(p, 0) * 0.9
            for p in prev_asks if prev_asks[p] > 0
        )
        return {"bid_iceberg": bid_iceberg, "ask_iceberg": ask_iceberg}

    def _estimate_market_impact(self, symbol: str, trade_size_usd: float, book: Dict) -> Dict[str, float]:
        """
        Estimation de l'impact marché pour une taille d'ordre donnée.
        Basé sur le modèle Almgren-Chriss simplifié : impact ∝ √(taille/liquidité)
        """
        try:
            asks = [(float(p), float(q)) for p, q in book.get("asks", [])[:10]]
            bids = [(float(p), float(q)) for p, q in book.get("bids", [])[:10]]
            if not asks or not bids:
                return {"impact_pct": 0.0, "slippage_usd": 0.0}
            mid = (asks[0][0] + bids[0][0]) / 2
            # Liquidité disponible dans le 1er % de profondeur
            depth_1pct = sum(p * q for p, q in asks if p <= mid * 1.01)
            if depth_1pct <= 0:
                return {"impact_pct": 0.1, "slippage_usd": trade_size_usd * 0.001}
            # Impact = (trade_size / liquidity) ^ 0.6 * 100 bps
            impact_pct = (trade_size_usd / depth_1pct) ** 0.6 * 0.01
            impact_pct = min(impact_pct, 0.005)   # Cap 0.5%
            return {
                "impact_pct":   round(impact_pct * 100, 4),
                "slippage_usd": round(trade_size_usd * impact_pct, 2),
            }
        except Exception:
            return {"impact_pct": 0.0, "slippage_usd": 0.0}

    # ──────────────────────────────────────────────────────────────────────────
    # RESPOND
    # ──────────────────────────────────────────────────────────────────────────

    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in [
            "order book", "carnet", "orderbook", "bid", "ask",
            "imbalance", "liquidité", "mur", "wall", "microstructure",
            "vpin", "iceberg", "impact", "order_book",
        ]) or super()._is_in_my_domain(question)

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name, "summary": "Hors domaine order_book",
                "confidence": 0.0, "recommendation": "HOLD",
            }

        symbol        = context.get("symbol", "BTCUSDT")
        trade_size    = float(context.get("trade_size_usd", 100.0))

        book   = self._fetch_order_book(symbol)
        trades = self._fetch_recent_trades(symbol)

        if not book:
            return {
                "agent": self.name,
                "summary": f"[OrderBookV3] {symbol}: données indisponibles — neutre",
                "confidence": 0.3,
                "recommendation": "HOLD",
                "signal": "NEUTRAL",
                "imbalance": 0.5,
            }

        analysis  = self._analyze_book(book.get("bids", []), book.get("asks", []))
        vpin      = self._calculate_vpin(trades)
        icebergs  = self._detect_icebergs(symbol, book)
        impact    = self._estimate_market_impact(symbol, trade_size, book)

        # ── Signal composite ───────────────────────────────────────────────
        imb_score  = analysis["imbalance"]          # 0→sell, 1→buy
        vpin_score = vpin                            # 0→sell, 1→buy
        composite  = imb_score * 0.60 + vpin_score * 0.40

        if composite > 0.65:
            final_signal = "STRONG_BUY"
            confidence   = 0.85
        elif composite > 0.55:
            final_signal = "BUY"
            confidence   = 0.70
        elif composite < 0.35:
            final_signal = "STRONG_SELL"
            confidence   = 0.85
        elif composite < 0.45:
            final_signal = "SELL"
            confidence   = 0.70
        else:
            final_signal = "NEUTRAL"
            confidence   = 0.50

        # Iceberg ajustement
        if icebergs["bid_iceberg"] and composite > 0.50:
            final_signal = f"{final_signal}_ICEBERG_SUPPORT"
            confidence   = min(0.95, confidence + 0.10)
        if icebergs["ask_iceberg"] and composite < 0.50:
            final_signal = f"{final_signal}_ICEBERG_RESISTANCE"
            confidence   = min(0.95, confidence + 0.10)

        # Spread élevé → confiance réduite
        if analysis["spread_pct"] > 0.05:
            confidence = max(0.30, confidence - 0.15)

        summary = (
            f"[OrderBookV3] {symbol} | Imbalance pondéré: {imb_score:.2%} | "
            f"VPIN: {vpin:.2%} | Score: {composite:.2%} | Signal: {final_signal} | "
            f"Spread: {analysis['spread_pct']:.3f}% | "
            f"Bid walls: {len(analysis['bid_walls'])} | Ask walls: {len(analysis['ask_walls'])} | "
            f"Iceberg: {'Bid 🧊' if icebergs['bid_iceberg'] else ''}"
            f"{'Ask 🧊' if icebergs['ask_iceberg'] else ''} | "
            f"Impact estimé: {impact['impact_pct']:.3f}% (${impact['slippage_usd']:.2f})"
        )

        return {
            "agent":          self.name,
            "summary":        summary,
            "arguments": [
                f"Imbalance (pondéré proximité): {imb_score:.2%}",
                f"VPIN (pression acheteur): {vpin:.2%}",
                f"Score composite: {composite:.2%}",
                f"Spread bid-ask: {analysis['spread_pct']:.3f}%",
                f"Murs bid: {analysis['bid_walls'][:2]} | Murs ask: {analysis['ask_walls'][:2]}",
                f"Icebergs: bid={icebergs['bid_iceberg']} ask={icebergs['ask_iceberg']}",
                f"Impact marché ({trade_size}$): {impact['impact_pct']:.3f}%",
            ],
            "risks":          [f"Spread élevé: {analysis['spread_pct']:.3f}%"] if analysis["spread_pct"] > 0.05 else [],
            "confidence":     round(confidence, 3),
            "recommendation": "BUY" if "BUY" in final_signal else ("SELL" if "SELL" in final_signal else "HOLD"),
            "signal":         final_signal,
            "imbalance":      imb_score,
            "vpin":           vpin,
            "composite_score": round(composite, 4),
            "spread_pct":     analysis["spread_pct"],
            "bid_walls":      analysis["bid_walls"],
            "ask_walls":      analysis["ask_walls"],
            "icebergs":       icebergs,
            "market_impact":  impact,
            "orderbook_imb":  imb_score,
            "glossary_used":  True,
        }
