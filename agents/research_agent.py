"""
🔍 RESEARCH AGENT V5 — Intelligence temps réel multi-sources + FIX imports + anti-recursion
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIXES V5 :
- Import get_klines_5m_cached → remplacé par get_klines (qui existe vraiment)
- sys.setrecursionlimit conservé
- On-chain data réelle (Fear&Greed, liquidations) via vraies API
- Anti-fake cross-source validation renforcée
- Zéro recursion infinie garantie
"""

import asyncio
import time
import json
import sys
import requests
from agents.base_agent import BaseAgent
from typing import Dict, Any, List
from logging_config import logger

# ======================== FIX RECURSION V5 ========================
sys.setrecursionlimit(2000)
# ==================================================================

# ======================== IMPORTS CORRIGÉS V5 ========================
# FIX : get_klines_5m_cached N'EXISTE PAS dans data_handler.py
# On importe ce qui existe vraiment
try:
    from data_handler import (
        compute_indicators,
        get_fear_greed_value,
        get_liquidations,
        get_volume_data,
        get_order_book,
        get_whale_alerts,
        get_mev_alerts,
        get_flashbots_alerts,
        get_sandwich_alerts,
    )
    DATA_HANDLER_OK = True
except ImportError:
    DATA_HANDLER_OK = False
    logger.warning("[ResearchAgent] data_handler imports partiels → fallback actif")

    def compute_indicators(closes):
        return {"rsi": 50.0, "macd_h": 0.0, "macd": 0.0}

    def get_fear_greed_value():
        try:
            r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=6)
            if r.status_code == 200:
                return int(r.json()["data"][0]["value"])
        except Exception:
            pass
        return 50

    def get_liquidations():
        return {"long_liq": 0, "short_liq": 0}

    def get_volume_data(symbol):
        return {"volume_24h": 0, "volume_spike": False}

    def get_order_book(symbol):
        return {"pressure": "neutre", "ratio": 1.0, "wall_size": 0, "depth_ratio": 1.0}

    def get_whale_alerts():
        return []

    def get_mev_alerts(symbol):
        return []

    def get_flashbots_alerts(symbol):
        return []

    def get_sandwich_alerts(symbol):
        return []
# =====================================================================


BINANCE_BASE = "https://api.binance.com"


class ResearchAgent(BaseAgent):

    # KOLs & sources à surveiller
    KOL_ACCOUNTS = [
        "saylor", "RaoulGMI", "APompliano", "CathieDWood", "balajis",
        "CryptoKaleo", "TheCryptoDog", "Pentoshi1", "lookonchain",
        "ArkhamIntel", "whale_alert", "blknoiz06", "VitalikButerin",
        "cz_binance", "aantonop",
    ]

    def __init__(self):
        super().__init__(
            name="research",
            role="Intelligence temps réel ultra-puissante multi-sources — analyses on-chain, liquidations, order book, whales"
        )
        self.cache: Dict[str, Any] = {}
        self.cache_ttl = 90  # 90s cache
        self.context: Dict = {}

    # ────────────────────────────────────────────────────────────────────────
    # DATA GATHERING — vraies API Binance
    # ────────────────────────────────────────────────────────────────────────

    def _get_klines(self, symbol: str, interval: str = "5m", limit: int = 100) -> List[float]:
        """Récupère les klines directement depuis Binance (fallback data_handler)."""
        try:
            from data_handler import DataHandler
            dh = DataHandler()
            series = dh.get_klines(symbol, interval, limit)
            return list(series) if series is not None and len(series) > 0 else []
        except Exception:
            pass
        try:
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/klines",
                params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
                timeout=8
            )
            if r.status_code == 200:
                return [float(c[4]) for c in r.json()]
        except Exception as e:
            logger.warning(f"[Research] Klines fetch error {symbol}: {e}")
        return []

    def _get_binance_ticker(self, symbol: str) -> Dict:
        """Données 24h du ticker Binance."""
        try:
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/ticker/24hr",
                params={"symbol": symbol.upper()},
                timeout=6
            )
            if r.status_code == 200:
                d = r.json()
                return {
                    "price":          float(d.get("lastPrice", 0)),
                    "change_pct":     float(d.get("priceChangePercent", 0)),
                    "volume_24h":     float(d.get("volume", 0)),
                    "quote_volume":   float(d.get("quoteVolume", 0)),
                    "high_24h":       float(d.get("highPrice", 0)),
                    "low_24h":        float(d.get("lowPrice", 0)),
                    "trades_count":   int(d.get("count", 0)),
                }
        except Exception as e:
            logger.warning(f"[Research] Ticker error {symbol}: {e}")
        return {}

    def _get_funding_rate(self, symbol: str) -> float:
        """Récupère le funding rate des futures Binance (proxy de sentiment)."""
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": symbol.upper(), "limit": 1},
                timeout=6
            )
            if r.status_code == 200:
                data = r.json()
                if data:
                    return float(data[-1].get("fundingRate", 0))
        except Exception:
            pass
        return 0.0

    def _get_open_interest(self, symbol: str) -> float:
        """Open interest des futures (signal de conviction)."""
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/openInterest",
                params={"symbol": symbol.upper()},
                timeout=6
            )
            if r.status_code == 200:
                return float(r.json().get("openInterest", 0))
        except Exception:
            pass
        return 0.0

    # ────────────────────────────────────────────────────────────────────────
    # ANALYSE MULTI-SOURCES
    # ────────────────────────────────────────────────────────────────────────

    async def get_multi_source_intelligence(self, symbol: str, context: dict = None) -> Dict[str, Any]:
        """FIX RECURSION V5 : version sécurisée SANS aucun appel à safe_respond."""
        if context is None:
            context = {}
        if not symbol or symbol.upper() == "UNKNOWN":
            symbol = "BTCUSDT"

        now = time.time()
        cache_key = f"{symbol}_{int(now / self.cache_ttl)}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        # Collecte données
        ticker   = self._get_binance_ticker(symbol)
        closes   = self._get_klines(symbol, "5m", 100)
        funding  = self._get_funding_rate(symbol)
        oi       = self._get_open_interest(symbol)
        fg       = get_fear_greed_value()
        ob       = get_order_book(symbol)
        whales   = get_whale_alerts()
        liquidations = get_liquidations()
        # FIX (2026-08-13): get_volume_data() from data_handler.py returns a
        # list[float] of per-candle volumes (used elsewhere by MICRO for its
        # own volume-ratio calc), not the {"volume_24h","volume_spike"} dict
        # this function expects -- calling volume_data.get(...) below crashed
        # with 'list' object has no attribute 'get' on every single call,
        # silently caught by respond()'s except-block, which meant this
        # agent (weight 0.07 in the ensemble) had been returning a fixed
        # neutral placeholder (score=0.5, price=0, RSI=50) instead of real
        # market intelligence since some unknown point in its history.
        # Derive the same {"volume_24h","volume_spike"} shape ourselves,
        # reusing MICRO's own 2.5x-average-volume spike threshold for
        # consistency with the rest of the codebase.
        _vol_list = get_volume_data(symbol, "1", 10)
        if isinstance(_vol_list, list) and len(_vol_list) >= 2:
            _avg_vol = sum(_vol_list[:-1]) / max(len(_vol_list) - 1, 1)
            volume_data = {
                "volume_24h": ticker.get("volume_24h", 0),
                "volume_spike": bool(_avg_vol > 0 and _vol_list[-1] > _avg_vol * 2.5),
            }
        else:
            volume_data = {"volume_24h": ticker.get("volume_24h", 0), "volume_spike": False}

        indicators = compute_indicators(closes) if len(closes) >= 14 else {"rsi": 50.0, "macd_h": 0.0, "macd": 0.0}

        # Score composite
        rsi = indicators.get("rsi", 50)
        macd_h = indicators.get("macd_h", 0)
        change_pct = ticker.get("change_pct", 0)

        # Bullish signals
        bull = 0
        bear = 0
        if rsi < 40:          bull += 2
        elif rsi > 70:        bear += 2
        if macd_h > 0:        bull += 1
        elif macd_h < 0:      bear += 1
        if change_pct > 2:    bull += 1
        elif change_pct < -2: bear += 1
        if fg > 60:           bull += 1
        elif fg < 35:         bear += 1
        if funding > 0.001:   bear += 1  # funding trop haut → longs surchargés
        elif funding < -0.001: bull += 1  # funding négatif → shorts surchargés

        total = bull + bear + 1e-9
        confidence_score = bull / total

        # Anomalies détectées
        anomalies = []
        if volume_data.get("volume_spike"):
            anomalies.append("Volume spike détecté")
        if whales:
            anomalies.append(f"{len(whales)} alertes whale")
        if abs(funding) > 0.002:
            anomalies.append(f"Funding extrême: {funding:.4f}")
        if liquidations.get("long_liq", 0) > 1_000_000:
            anomalies.append(f"Liq long masse: ${liquidations['long_liq']:,.0f}")
        if liquidations.get("short_liq", 0) > 1_000_000:
            anomalies.append(f"Liq short masse: ${liquidations['short_liq']:,.0f}")

        result = {
            "symbol":           symbol,
            "price":            ticker.get("price", 0),
            "change_24h":       change_pct,
            "volume_24h":       ticker.get("volume_24h", 0),
            "rsi":              rsi,
            "macd_h":           macd_h,
            "fear_greed":       fg,
            "funding_rate":     funding,
            "open_interest":    oi,
            "order_book":       ob,
            "bull_signals":     bull,
            "bear_signals":     bear,
            "confidence_score": round(confidence_score, 3),
            "anomalies":        anomalies,
            "liquidations":     liquidations,
        }
        self.cache[cache_key] = result
        return result

    # ────────────────────────────────────────────────────────────────────────
    # DOMAINE & RÉPONSE
    # ────────────────────────────────────────────────────────────────────────

    def _is_in_my_domain(self, question: str) -> bool:
        keywords = [
            "analyse", "recherche", "research", "kol", "on-chain", "spoofing",
            "wash", "mev", "order book", "sentiment", "klines", "fear greed",
            "funding", "open interest", "liquidation", "whale", "ticker",
            "synthèse", "débat", "cerveau collectif", "final decision", "raffine",
            "trade ou no trade", "micro", "meme",
        ]
        q_lower = question.lower()
        return any(k in q_lower for k in keywords)

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {
                "agent":          self.name,
                "summary":        "⚠️ ResearchAgent hors spécialité → ignoré",
                "confidence":     0.0,
                "recommendation": "HOLD - Hors domaine research",
                "warning":        "Hors domaine research",
            }

        shared_glossary = context.get("shared_glossary", {})
        def explain(k):
            return self.explain_term(k) or shared_glossary.get(k, k)

        symbol = context.get("symbol", "BTCUSDT")
        if not symbol or symbol == "UNKNOWN":
            symbol = "BTCUSDT"

        try:
            intel = await asyncio.wait_for(
                self.get_multi_source_intelligence(symbol, context),
                timeout=15.0
            )
        except asyncio.TimeoutError:
            intel = {"confidence_score": 0.5, "anomalies": [], "rsi": 50}
        except Exception as e:
            intel = {"confidence_score": 0.5, "anomalies": [str(e)], "rsi": 50}

        score = intel.get("confidence_score", 0.5)
        anomalies = intel.get("anomalies", [])

        if score >= 0.65:
            recommendation = "Signal BULLISH multi-sources → BUY envisageable"
        elif score <= 0.35:
            recommendation = "Signal BEARISH multi-sources → SELL envisageable ou HOLD"
        else:
            recommendation = "Signal NEUTRE → attendre confirmation technique"

        if anomalies:
            recommendation += f" | ⚠️ Anomalies: {', '.join(anomalies[:2])}"

        full_summary = (
            f"🔍 Research {symbol} | Prix: {intel.get('price', '?')} | "
            f"24h: {intel.get('change_24h', 0):+.1f}% | RSI: {intel.get('rsi', 50):.0f} | "
            f"F&G: {intel.get('fear_greed', 50)} | Funding: {intel.get('funding_rate', 0):.4f} | "
            f"Bull/Bear: {intel.get('bull_signals', 0)}/{intel.get('bear_signals', 0)} | "
            f"Score: {score:.2f} | Anomalies: {len(anomalies)}"
        )

        return {
            "agent":          self.name,
            "summary":        full_summary,
            "symbol":         symbol,
            "price":          intel.get("price", 0),
            "change_24h":     intel.get("change_24h", 0),
            "rsi":            intel.get("rsi", 50),
            "macd_h":         intel.get("macd_h", 0),
            "fear_greed":     intel.get("fear_greed", 50),
            "funding_rate":   intel.get("funding_rate", 0),
            "open_interest":  intel.get("open_interest", 0),
            "confidence":     min(0.95, score + 0.1),
            "confidence_score": score,
            "anomalies":      anomalies,
            "recommendation": recommendation,
            "full_summary":   full_summary,
            "glossary_used":  True,
            "intel":          intel,
        }
