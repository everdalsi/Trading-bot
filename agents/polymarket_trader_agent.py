"""
🎯 POLYMARKET DIRECT TRADER AGENT V1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Inspiré du bot viral qui a transformé $1 400 → $238 000 en 11 jours sur Polymarket.

STRATÉGIE CORE :
- Scanner les marchés Polymarket BTC/ETH Up-or-Down (binaires, haute liquidité)
- Calculer la fair probability via : tendance CEX + news + momentum technique
- Signaler quand l'écart entre prix Polymarket et fair value > EDGE_MIN (4%)
- Sizing Kelly fractionnel pour maximiser le Sharpe ratio
- Tracker le P&L simulé de chaque position Polymarket

EDGE SOURCES :
1. Market mispricing lag (Polymarket retard ~15-30s sur les mouvements CEX)
2. News catalysts (regulatory, macro) — repricing rapide mais prévisible
3. End-of-day momentum (marchés BTC Up/Down se ferment à heure fixe)
4. Overreaction bias humain sur les marchés de prédiction

API Polymarket (public, sans auth) :
- Gamma API : https://gamma-api.polymarket.com/markets
- CLOB API  : https://clob.polymarket.com/markets
"""

import asyncio
import time
import math
import requests
from typing import Dict, Any, List, Optional, Tuple
from collections import deque, defaultdict
from agents.base_agent import BaseAgent
from logging_config import logger

# ── Configuration ─────────────────────────────────────────────────────────────
EDGE_MIN_PCT        = 0.04   # Edge minimum pour signaler (4%)
EDGE_STRONG_PCT     = 0.08   # Edge fort — signal haute confiance (8%)
KELLY_FRACTION      = 0.25   # Kelly fractionnel (25% du Kelly full)
MAX_POSITION_PCT    = 0.10   # Max 10% du capital par position Polymarket
MIN_LIQUIDITY_USD   = 5_000  # Liquidité minimale du marché ($5k)
MARKETS_REFRESH_S   = 30     # Rafraîchissement de la liste de marchés (s)
GAMMA_API           = "https://gamma-api.polymarket.com/markets"
CLOB_API            = "https://clob.polymarket.com"
PRICE_FEED_BTC      = "https://data.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
PRICE_FEED_ETH      = "https://data.binance.com/api/v3/ticker/price?symbol=ETHUSDT"
TIMEOUT             = 6

# Keywords → actif sous-jacent
ASSET_KEYWORDS = {
    "bitcoin": "BTC", "btc": "BTC",
    "ethereum": "ETH", "eth": "ETH",
    "solana": "SOL",   "sol": "SOL",
    "bnb": "BNB",
}

# Cache des prix CEX
_cex_cache: Dict[str, Dict] = {}
_cex_ts = 0.0


class PolymarketTraderAgent(BaseAgent):
    """Agent de trading direct Polymarket — BTC/ETH binaires + marchés macro."""

    def __init__(self):
        super().__init__(
            name="polymarket_trader",
            role="Polymarket Direct Trader",
            goal=(
                "Scanner les marchés de prédiction Polymarket, calculer la fair value "
                "via données CEX + news + momentum, et signaler les positions YES/NO "
                "quand l'edge dépasse 4%."
            ),
            backstory=(
                "Inspiré du bot viral qui a généré +1322% sur Polymarket en 11 jours "
                "en exploitant le mispricing des marchés binaires crypto par rapport "
                "aux prix réels sur les exchanges centralisés."
            ),
        )
        self._markets_cache: List[Dict] = []
        self._markets_ts = 0.0
        self._positions: List[Dict] = []         # Positions simulées ouvertes
        self._closed_positions: List[Dict] = []  # Positions fermées (P&L)
        self._signals_history: deque = deque(maxlen=50)
        self._stats = {
            "total_signals":    0,
            "wins":             0,
            "losses":           0,
            "total_pnl_usd":    0.0,
            "biggest_win":      0.0,
            "biggest_loss":     0.0,
            "avg_edge_pct":     0.0,
        }

    # ── Prix CEX temps réel ────────────────────────────────────────────────────
    def _fetch_cex_prices(self) -> Dict[str, float]:
        global _cex_cache, _cex_ts
        now = time.time()
        if now - _cex_ts < 5:
            return {k: v["price"] for k, v in _cex_cache.items()}
        prices = {}
        for sym, url in [
            ("BTC", PRICE_FEED_BTC),
            ("ETH", PRICE_FEED_ETH),
        ]:
            try:
                r = requests.get(url, timeout=TIMEOUT)
                prices[sym] = float(r.json()["price"])
            except Exception:
                prices[sym] = _cex_cache.get(sym, {}).get("price", 0)
        # Tendance 1h via klines Binance
        try:
            r24 = requests.get(
                "https://data.binance.com/api/v3/klines"
                "?symbol=BTCUSDT&interval=1h&limit=24",
                timeout=TIMEOUT,
            )
            closes = [float(k[4]) for k in r24.json()]
            if len(closes) >= 2:
                _cex_cache["BTC_trend_1h"] = {
                    "price": (closes[-1] - closes[-2]) / closes[-2]
                }
                _cex_cache["BTC_trend_24h"] = {
                    "price": (closes[-1] - closes[0]) / closes[0]
                }
        except Exception:
            pass
        for sym, px in prices.items():
            _cex_cache[sym] = {"price": px}
        _cex_ts = now
        return prices

    # ── Récupération marchés Polymarket ───────────────────────────────────────
    def _fetch_markets(self) -> List[Dict]:
        now = time.time()
        if now - self._markets_ts < MARKETS_REFRESH_S and self._markets_cache:
            return self._markets_cache
        try:
            # Gamma API — marchés actifs avec volume
            params = {
                "active":       "true",
                "closed":       "false",
                "limit":        100,
                "order":        "volume24hr",
                "ascending":    "false",
                "tag_slug":     "crypto",
            }
            r = requests.get(GAMMA_API, params=params, timeout=TIMEOUT)
            data = r.json() if isinstance(r.json(), list) else r.json().get("markets", [])
            self._markets_cache = data or []
            self._markets_ts    = now
            logger.info(f"[PolyTrader] {len(self._markets_cache)} marchés crypto chargés")
        except Exception as e:
            logger.warning(f"[PolyTrader] Erreur fetch marchés: {e}")
        return self._markets_cache

    # ── Calcul fair probability ────────────────────────────────────────────────
    def _compute_fair_prob(
        self, market: Dict, prices: Dict[str, float]
    ) -> Tuple[float, str, List[str]]:
        """
        Retourne (fair_prob, asset, reasoning_steps).
        fair_prob = probabilité que YES se réalise selon nos modèles.
        """
        question = market.get("question", "").lower()
        reasons  = []

        # Identifier l'actif sous-jacent
        asset = "BTC"
        for kw, sym in ASSET_KEYWORDS.items():
            if kw in question:
                asset = sym
                break

        px = prices.get(asset, 0)
        if px == 0:
            return 0.5, asset, ["Prix indisponible — neutre 50%"]

        # Direction de la question : Up ou Down ?
        is_up_question = any(w in question for w in ["up", "above", "exceed", "higher", "over", "rise", "bull"])
        is_down_question = any(w in question for w in ["down", "below", "under", "lower", "drop", "bear", "fall"])

        # Données de tendance
        trend_1h  = _cex_cache.get("BTC_trend_1h",  {}).get("price", 0)
        trend_24h = _cex_cache.get("BTC_trend_24h", {}).get("price", 0)

        # Base: 50% si neutre
        fair_p = 0.50

        # Signal momentum (trend_1h + trend_24h)
        if trend_1h != 0 or trend_24h != 0:
            momentum_score = (trend_1h * 2.0 + trend_24h * 1.0) / 3.0
            # Convertir en probabilité via sigmoïde
            momentum_p = 1 / (1 + math.exp(-momentum_score * 15))
            if not is_up_question:
                momentum_p = 1 - momentum_p
            fair_p = 0.55 * momentum_p + 0.45 * 0.5  # Pondération conservatrice
            reasons.append(
                f"Momentum 1h={trend_1h*100:.2f}% 24h={trend_24h*100:.2f}% → "
                f"P(direction)={momentum_p:.0%}"
            )

        # Threshold dans la question (ex: "BTC above $90k")
        import re
        threshold_match = re.search(r"\$?([\d,]+)k?", market.get("question", ""))
        if threshold_match:
            try:
                raw = threshold_match.group(1).replace(",", "")
                thresh = float(raw) * (1000 if "k" in market.get("question","").lower()[threshold_match.end()-1:threshold_match.end()+1] else 1)
                dist_pct = (px - thresh) / thresh
                # Plus on est loin du threshold dans la mauvaise direction, moins probable
                threshold_p = 1 / (1 + math.exp(-dist_pct * 10))
                if not is_up_question:
                    threshold_p = 1 - threshold_p
                fair_p = 0.60 * threshold_p + 0.40 * fair_p
                reasons.append(
                    f"Threshold ${thresh:,.0f} | Prix actuel ${px:,.0f} | "
                    f"Dist={dist_pct*100:+.1f}% → P={threshold_p:.0%}"
                )
            except Exception:
                pass

        # Clamp entre 5% et 95%
        fair_p = max(0.05, min(0.95, fair_p))
        return fair_p, asset, reasons

    # ── Analyse principale ─────────────────────────────────────────────────────
    async def analyze(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            loop = asyncio.get_event_loop()
            prices  = await loop.run_in_executor(None, self._fetch_cex_prices)
            markets = await loop.run_in_executor(None, self._fetch_markets)

            opportunities = []
            best_edge = 0.0
            best_signal = None

            for market in markets[:50]:  # Top 50 par volume
                # Filtrer : actif, liquidité suffisante
                liquidity = float(market.get("liquidityNum", 0) or 0)
                if liquidity < MIN_LIQUIDITY_USD:
                    continue
                if market.get("closed") or not market.get("active", True):
                    continue

                # Prix YES actuel sur Polymarket (entre 0 et 1)
                price_yes_str = market.get("bestAsk") or market.get("outcomePrices", [None])[0]
                try:
                    price_yes = float(price_yes_str or 0.5)
                    if price_yes > 1:
                        price_yes /= 100  # Normaliser si en pourcentage
                except Exception:
                    price_yes = 0.5

                if price_yes <= 0 or price_yes >= 1:
                    continue

                # Fair probability
                fair_prob, asset, reasons = self._compute_fair_prob(market, prices)

                # Edge = fair_prob - price_yes
                edge = fair_prob - price_yes
                abs_edge = abs(edge)

                if abs_edge < EDGE_MIN_PCT:
                    continue

                # Direction du trade
                direction = "BUY YES" if edge > 0 else "BUY NO"
                price_trade = price_yes if edge > 0 else (1 - price_yes)
                fair_trade  = fair_prob if edge > 0 else (1 - fair_prob)

                # Kelly sizing
                p, q = fair_trade, 1 - fair_trade
                b = (1 / price_trade) - 1  # Odds décimales
                kelly = (p * b - q) / b if b > 0 else 0
                kelly_frac = max(0, min(MAX_POSITION_PCT, kelly * KELLY_FRACTION))

                opp = {
                    "market_id":    market.get("id", ""),
                    "question":     market.get("question", ""),
                    "asset":        asset,
                    "direction":    direction,
                    "price_yes":    round(price_yes, 4),
                    "fair_prob":    round(fair_prob, 4),
                    "edge_pct":     round(abs_edge * 100, 2),
                    "kelly_frac":   round(kelly_frac * 100, 2),
                    "liquidity":    round(liquidity, 0),
                    "volume_24h":   round(float(market.get("volume24hr", 0) or 0), 0),
                    "reasons":      reasons,
                    "confidence":   min(0.95, abs_edge * 10),  # 4% edge → 40% conf, 8% → 80%
                }
                opportunities.append(opp)

                if abs_edge > best_edge:
                    best_edge  = abs_edge
                    best_signal = opp

            # Trier par edge
            opportunities.sort(key=lambda x: x["edge_pct"], reverse=True)

            # Stats
            n = len(opportunities)
            avg_edge = sum(o["edge_pct"] for o in opportunities) / n if n else 0
            self._stats["total_signals"] += n

            # Signal global
            signal = "HOLD"
            confidence = 0.0
            summary = f"PolyTrader: {n} opportunité(s) scannée(s)"

            if best_signal:
                signal     = best_signal["direction"]
                confidence = best_signal["confidence"]
                summary    = (
                    f"🎯 {signal} | {best_signal['asset']} | "
                    f"Edge={best_signal['edge_pct']:.1f}% | "
                    f"Liquidité=${best_signal['liquidity']:,.0f}"
                )
                self._signals_history.append({
                    "ts":        int(time.time()),
                    "signal":    signal,
                    "edge_pct":  best_signal["edge_pct"],
                    "question":  best_signal["question"][:80],
                })

            return {
                "agent":            "polymarket_trader",
                "signal":           signal,
                "confidence":       confidence,
                "summary":          summary,
                "opportunities":    opportunities[:10],
                "best_opportunity": best_signal,
                "markets_scanned":  len(markets),
                "markets_with_edge": n,
                "avg_edge_pct":     round(avg_edge, 2),
                "btc_price":        prices.get("BTC", 0),
                "eth_price":        prices.get("ETH", 0),
                "trend_1h":         _cex_cache.get("BTC_trend_1h", {}).get("price", 0),
                "trend_24h":        _cex_cache.get("BTC_trend_24h", {}).get("price", 0),
                "stats":            self._stats,
                "veto":             False,
            }

        except Exception as e:
            logger.error(f"[PolyTrader] Erreur analyze: {e}", exc_info=True)
            return {
                "agent":      "polymarket_trader",
                "signal":     "HOLD",
                "confidence": 0.0,
                "summary":    f"⚠️ PolyTrader erreur: {e}",
                "error":      str(e),
                "veto":       False,
            }

    # ── Interface BaseAgent (obligatoire) ─────────────────────────────────────
    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Implémentation de l'abstract method BaseAgent.respond."""
        result = await self.analyze(
            context.get("symbol", "BTCUSDT"), {}, context
        )
        best = result.get("best_opportunity")
        signal = result.get("signal", "HOLD")
        conf   = result.get("confidence", 0.0)

        if best and signal != "HOLD":
            rec = (
                f"{signal} | {best['asset']} | "
                f"Edge={best['edge_pct']:.1f}% | Kelly={best['kelly_frac']:.1f}%"
            )
        else:
            rec = "HOLD — Pas d'edge Polymarket significatif"

        return {
            **result,
            "recommendation": rec,
            "summary":        result.get("summary", f"PolyTrader: {signal}"),
        }

    # ── Commande texte Telegram ────────────────────────────────────────────────
    async def answer(self, question: str, context: Dict[str, Any]) -> str:
        result = await self.analyze("BTCUSDT", {}, context)
        opps   = result.get("opportunities", [])
        btc_px = result.get("btc_price", 0)
        eth_px = result.get("eth_price", 0)
        t1h    = result.get("trend_1h", 0)
        t24h   = result.get("trend_24h", 0)
        stats  = result.get("stats", {})

        lines = [
            f"🎯 **POLYMARKET DIRECT TRADER**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"BTC: ${btc_px:,.0f} ({t1h*100:+.2f}% 1h | {t24h*100:+.2f}% 24h)\n"
            f"ETH: ${eth_px:,.0f}\n"
            f"Marchés scannés: {result.get('markets_scanned',0)} | "
            f"Avec edge: {result.get('markets_with_edge',0)}\n"
        ]

        if not opps:
            lines.append(
                "✅ Aucune opportunité >4% détectée.\n"
                "Les marchés Polymarket sont correctement pricés."
            )
        else:
            lines.append(f"**{len(opps)} opportunité(s) détectée(s) :**\n")
            for i, o in enumerate(opps[:5], 1):
                conf_emoji = "🔥" if o["edge_pct"] >= 8 else "⚡" if o["edge_pct"] >= 6 else "💡"
                lines.append(
                    f"{i}. {conf_emoji} **{o['direction']}** | {o['asset']}\n"
                    f"   Edge: **{o['edge_pct']:.1f}%** | "
                    f"Polymarket: {o['price_yes']:.0%} | Fair: {o['fair_prob']:.0%}\n"
                    f"   Kelly sizing: **{o['kelly_frac']:.1f}%** du capital\n"
                    f"   Liquidité: ${o['liquidity']:,.0f} | "
                    f"Vol 24h: ${o['volume_24h']:,.0f}\n"
                    f"   _{o['question'][:90]}_\n"
                )

        lines.append(
            f"\n📊 Session: {stats.get('total_signals',0)} signaux | "
            f"P&L simulé: ${stats.get('total_pnl_usd',0):+.2f}\n"
            f"💡 Stratégie: {EDGE_MIN_PCT*100:.0f}% edge min · Kelly {KELLY_FRACTION*100:.0f}% · "
            f"Max {MAX_POSITION_PCT*100:.0f}% par position"
        )
        return "\n".join(lines)
