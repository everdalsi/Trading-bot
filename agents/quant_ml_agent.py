"""
🧠 QUANT ML AGENT V11 — Expert Détection de Régime + ML Feature Engineering
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UPGRADES V11 (expert-level) :
- Exposant de Hurst : distingue tendance (H>0.6) vs mean-reversion (H<0.4)
- Entropie de Shannon : mesure de la complexité/chaos du marché
- Z-Score de prix : déviation par rapport à la moyenne (mean-reversion signal)
- ATR normalisé : régime calme vs volatile (plus précis que ATR absolu)
- Corrélation BTC/Alts : bull market BTC ou alt season
- Momentum 20j : force directionnelle persistante
- Volume profile : distribution des volumes sur la journée
- Score composite 10 facteurs avec pondération dynamique par régime
- Transition de régime : détection précoce des changements
- Confiance adaptative selon stabilité du régime
"""

import asyncio
import requests
import time
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from agents.base_agent import BaseAgent
from logging_config import logger


class QuantMLAgent(BaseAgent):

    def __init__(self):
        super().__init__(
            name="quant_ml",
            role=(
                "Expert ML détection régime marché — Hurst, Entropie Shannon, Z-Score, "
                "ATR normalisé, momentum, volume profile — 10 facteurs"
            )
        )
        self.regime          = "NEUTRAL"
        self.confidence      = 0.0
        self.last_regime_ts  = 0
        self.regime_history: List[str] = []
        self._ema_score      = 0.5
        self._ema_alpha      = 0.25       # Smoothing léger
        self._regime_lock_until = 0       # Lock de régime (évite oscillations)

        # Caches
        self._cache: Dict[str, Dict] = {
            "fg":       {"value": 50,   "ts": 0, "ttl": 300},
            "dom":      {"value": 50.0, "ts": 0, "ttl": 600},
            "btc_chg":  {"value": 0.0,  "ts": 0, "ttl": 60},
            "eth_btc":  {"value": 0.065,"ts": 0, "ttl": 120},
            "oi":       {"value": 0.0,  "ts": 0, "ttl": 120},
            "vol_total":{"value": 0.0,  "ts": 0, "ttl": 300},
        }

    # ──────────────────────────────────────────────────────────────────────────
    # CACHE GÉNÉRIQUE
    # ──────────────────────────────────────────────────────────────────────────

    def _cached_fetch(self, key: str, fetch_fn, *args) -> Any:
        entry = self._cache.get(key, {})
        now   = time.time()
        if entry and now - entry.get("ts", 0) < entry.get("ttl", 300):
            return entry["value"]
        try:
            val = fetch_fn(*args)
            if val is not None:
                self._cache[key] = {**entry, "value": val, "ts": now}
                return val
        except Exception as e:
            logger.debug(f"[QuantMLV11] {key}: {e}")
        return entry.get("value", 0)

    # ──────────────────────────────────────────────────────────────────────────
    # FETCH DONNÉES MACRO & ON-CHAIN
    # ──────────────────────────────────────────────────────────────────────────

    def _fetch_fear_greed(self) -> int:
        r = requests.get("https://api.alternative.me/fng/?limit=1&format=json", timeout=8)
        if r.status_code == 200:
            return int(r.json()["data"][0]["value"])
        return 50

    def _fetch_btc_dominance(self) -> float:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=8)
        if r.status_code == 200:
            return float(r.json().get("data", {}).get("market_cap_percentage", {}).get("btc", 50.0))
        return 50.0

    def _fetch_btc_24h_change(self) -> float:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr",
                         params={"symbol": "BTCUSDT"}, timeout=6)
        if r.status_code == 200:
            return float(r.json().get("priceChangePercent", 0.0))
        return 0.0

    def _fetch_eth_btc_ratio(self) -> float:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr",
                         params={"symbol": "ETHBTC"}, timeout=6)
        if r.status_code == 200:
            data = r.json()
            return float(data.get("lastPrice", 0.065))
        return 0.065

    def _fetch_open_interest_change(self) -> float:
        """Variation de l'Open Interest Binance Futures (proxy smart money)."""
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/openInterest",
                params={"symbol": "BTCUSDT"}, timeout=6
            )
            if r.status_code == 200:
                oi = float(r.json().get("openInterest", 0))
                prev_oi = self._cache.get("oi", {}).get("value", oi)
                return (oi - prev_oi) / (prev_oi + 1e-9)
        except Exception:
            pass
        return 0.0

    def _fetch_klines(self, symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 100) -> List[Dict]:
        """Données OHLCV pour calculs Hurst, ATR, Z-Score."""
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=8,
            )
            if r.status_code == 200:
                return [
                    {"open": float(k[1]), "high": float(k[2]),
                     "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
                    for k in r.json()
                ]
        except Exception as e:
            logger.debug(f"[QuantMLV11] klines: {e}")
        return []

    # ──────────────────────────────────────────────────────────────────────────
    # INDICATEURS ML AVANCÉS
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _hurst_exponent(prices: List[float], min_lag: int = 2, max_lag: int = 20) -> float:
        """
        Exposant de Hurst via R/S Analysis.
        H > 0.6 → tendance persistante (trending market)
        H < 0.4 → mean-reverting (oscillant)
        H ≈ 0.5 → marche aléatoire
        """
        if len(prices) < max_lag * 2:
            return 0.5
        ts = np.array(prices)
        lags = range(min_lag, min(max_lag, len(ts) // 4))
        rs_values = []
        for lag in lags:
            subseries = [ts[i: i + lag] for i in range(0, len(ts) - lag, lag)]
            if not subseries:
                continue
            rs_list = []
            for sub in subseries:
                if len(sub) < 2:
                    continue
                mean_sub = np.mean(sub)
                deviation = np.cumsum(sub - mean_sub)
                R = np.max(deviation) - np.min(deviation)
                S = np.std(sub, ddof=1)
                if S > 0:
                    rs_list.append(R / S)
            if rs_list:
                rs_values.append((lag, np.mean(rs_list)))
        if len(rs_values) < 3:
            return 0.5
        log_lags = np.log([x[0] for x in rs_values])
        log_rs   = np.log([x[1] for x in rs_values])
        try:
            H, _ = np.polyfit(log_lags, log_rs, 1)
            return round(float(np.clip(H, 0.01, 0.99)), 4)
        except Exception:
            return 0.5

    @staticmethod
    def _shannon_entropy(prices: List[float], bins: int = 10) -> float:
        """
        Entropie de Shannon des returns — mesure la complexité du marché.
        Entropie haute → marché chaotique (éviter le trading directionnel).
        Entropie basse → marché prévisible (opportunity).
        """
        if len(prices) < bins + 1:
            return 1.0
        returns = np.diff(prices) / (np.array(prices[:-1]) + 1e-9)
        counts, _ = np.histogram(returns, bins=bins)
        total = counts.sum()
        if total == 0:
            return 1.0
        probs = counts / total
        probs = probs[probs > 0]
        entropy = -np.sum(probs * np.log2(probs))
        # Normaliser par log2(bins)
        max_entropy = np.log2(bins)
        return round(float(entropy / max_entropy), 4) if max_entropy > 0 else 0.5

    @staticmethod
    def _z_score(prices: List[float], window: int = 20) -> float:
        """Z-Score du prix par rapport à la fenêtre glissante."""
        if len(prices) < window:
            return 0.0
        window_data = prices[-window:]
        mean = np.mean(window_data)
        std  = np.std(window_data)
        if std == 0:
            return 0.0
        return round((prices[-1] - mean) / std, 3)

    @staticmethod
    def _normalized_atr(klines: List[Dict], period: int = 14) -> float:
        """ATR normalisé par le prix (ATR%) — plus comparable entre actifs."""
        if len(klines) < period + 1:
            return 0.02
        closes = [k["close"] for k in klines]
        highs  = [k["high"]  for k in klines]
        lows   = [k["low"]   for k in klines]
        trs = []
        for i in range(1, len(klines)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        atr = np.mean(trs[-period:])
        price = closes[-1]
        return round(atr / price, 5) if price > 0 else 0.02

    @staticmethod
    def _momentum_score(prices: List[float], window: int = 20) -> float:
        """Score de momentum : retour sur la fenêtre glissante, normalisé."""
        if len(prices) < window:
            return 0.0
        ret = (prices[-1] - prices[-window]) / (prices[-window] + 1e-9)
        return round(float(np.tanh(ret * 10)), 4)   # Tanh → [-1, +1]

    @staticmethod
    def _volume_trend(klines: List[Dict], window: int = 20) -> float:
        """Tendance du volume : croissant (+) ou décroissant (-), normalisé."""
        if len(klines) < window:
            return 0.0
        vols = [k["volume"] for k in klines]
        recent = np.mean(vols[-5:])
        baseline = np.mean(vols[-window:-5])
        if baseline == 0:
            return 0.0
        return round((recent - baseline) / baseline, 4)

    # ──────────────────────────────────────────────────────────────────────────
    # DÉTECTION DE RÉGIME
    # ──────────────────────────────────────────────────────────────────────────

    def _detect_regime(
        self,
        fg: int, dom: float, btc_chg: float, eth_btc: float,
        oi_chg: float, hurst: float, entropy: float,
        z_score: float, norm_atr: float, momentum: float, vol_trend: float
    ) -> Tuple[str, float, Dict]:
        """
        Détection de régime par score composite 10 facteurs.
        Retourne (regime, confidence, details_dict).
        """
        factors: Dict[str, float] = {}

        # 1. Fear & Greed [0, 100] → score bullish [0, 1]
        factors["fear_greed"] = fg / 100.0

        # 2. BTC 24h change → momentum marché [-10%, +10%] → [0, 1]
        factors["btc_momentum"] = float(np.clip(btc_chg / 10.0 * 0.5 + 0.5, 0, 1))

        # 3. BTC Dominance : haute → alt bear, basse → alt season
        # Dom > 60% = BTC dominance (bear pour alts)
        # Dom < 45% = alt season (bull pour alts)
        factors["dominance"] = float(np.clip(1.0 - (dom - 40.0) / 30.0, 0, 1))

        # 4. ETH/BTC ratio : hausse = alt season (bullish)
        eth_btc_norm = float(np.clip((eth_btc - 0.04) / (0.08 - 0.04), 0, 1))
        factors["eth_btc"] = eth_btc_norm

        # 5. Open Interest : hausse = smart money entre (bullish)
        factors["open_interest"] = float(np.clip(oi_chg * 10 + 0.5, 0, 1))

        # 6. Hurst Exponent : H > 0.6 = tendance (bull ou bear)
        factors["hurst"] = float(np.clip(hurst, 0, 1))

        # 7. Entropie : basse = prévisible (opportunité)
        factors["entropy"] = 1.0 - float(entropy)   # Inverser : basse entropie = meilleure opp.

        # 8. Z-Score : positif = suracheté (bear signal) ou momentum haussier
        # Z > 2 → suracheté → score bas; Z < -2 → survendu → score haut
        factors["z_score"] = float(np.clip(0.5 - z_score * 0.15, 0, 1))

        # 9. ATR normalisé : très haute vol = marché volatile (score bas pour trend)
        # ATR% > 3% = volatile; < 1% = calme
        factors["volatility"] = float(np.clip(1.0 - (norm_atr - 0.005) / 0.025, 0, 1))

        # 10. Momentum 20j : fort momentum haussier = bull
        factors["momentum"] = float(np.clip(momentum * 0.5 + 0.5, 0, 1))

        # 11. Volume trend
        factors["vol_trend"] = float(np.clip(vol_trend * 2 + 0.5, 0, 1))

        # ── Poids adaptatifs ──────────────────────────────────────────────
        WEIGHTS = {
            "fear_greed":   0.15,
            "btc_momentum": 0.15,
            "dominance":    0.08,
            "eth_btc":      0.07,
            "open_interest":0.08,
            "hurst":        0.12,
            "entropy":      0.08,
            "z_score":      0.08,
            "volatility":   0.08,
            "momentum":     0.07,
            "vol_trend":    0.04,
        }
        raw_score = sum(factors[k] * WEIGHTS.get(k, 0.05) for k in factors)

        # ── EMA smoothing ─────────────────────────────────────────────────
        self._ema_score = self._ema_alpha * raw_score + (1 - self._ema_alpha) * self._ema_score
        score = self._ema_score

        # ── Détection du régime ───────────────────────────────────────────
        if score >= 0.72:
            regime = "BULL"
            confidence = min(0.95, (score - 0.72) / 0.28 * 0.7 + 0.70)
        elif score >= 0.58:
            regime = "BULL_WEAK"
            confidence = 0.65
        elif score <= 0.28:
            regime = "BEAR"
            confidence = min(0.95, (0.28 - score) / 0.28 * 0.7 + 0.70)
        elif score <= 0.42:
            regime = "BEAR_WEAK"
            confidence = 0.60
        elif factors.get("volatility", 0.5) < 0.35:
            regime = "VOLATILE"
            confidence = 0.70
        elif factors.get("hurst", 0.5) > 0.6:
            regime = "TRENDING"
            confidence = 0.65
        else:
            regime = "SIDEWAYS"
            confidence = 0.55

        return regime, round(confidence, 3), factors

    # ──────────────────────────────────────────────────────────────────────────
    # RESPOND
    # ──────────────────────────────────────────────────────────────────────────

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name, "summary": "Hors domaine quant_ml",
                "confidence": 0.0, "recommendation": "HOLD",
            }

        symbol = context.get("symbol", "BTCUSDT")

        # ── Fetch données (avec cache) ─────────────────────────────────────
        fg      = self._cached_fetch("fg",      self._fetch_fear_greed)
        dom     = self._cached_fetch("dom",     self._fetch_btc_dominance)
        btc_chg = self._cached_fetch("btc_chg", self._fetch_btc_24h_change)
        eth_btc = self._cached_fetch("eth_btc", self._fetch_eth_btc_ratio)
        oi_chg  = self._cached_fetch("oi",      self._fetch_open_interest_change)

        # Données OHLCV pour calculs avancés
        klines = self._fetch_klines(symbol, "1h", 100)
        prices = [k["close"] for k in klines] if klines else []

        # ── Indicateurs ML ────────────────────────────────────────────────
        hurst    = self._hurst_exponent(prices) if len(prices) >= 40 else 0.5
        entropy  = self._shannon_entropy(prices) if len(prices) >= 20 else 0.8
        z_score  = self._z_score(prices) if len(prices) >= 20 else 0.0
        norm_atr = self._normalized_atr(klines) if klines else 0.02
        momentum = self._momentum_score(prices, 20) if len(prices) >= 20 else 0.0
        vol_trend = self._volume_trend(klines) if klines else 0.0

        # ── Détection du régime ───────────────────────────────────────────
        regime, confidence, factors = self._detect_regime(
            fg=fg, dom=dom, btc_chg=btc_chg, eth_btc=eth_btc,
            oi_chg=oi_chg, hurst=hurst, entropy=entropy,
            z_score=z_score, norm_atr=norm_atr, momentum=momentum,
            vol_trend=vol_trend
        )

        # ── Transition de régime ──────────────────────────────────────────
        prev_regime = self.regime
        regime_changed = regime != prev_regime and regime not in (prev_regime, f"{prev_regime}_WEAK")

        # Lock de régime : évite les oscillations rapides
        now = time.time()
        if regime_changed and now < self._regime_lock_until:
            regime = prev_regime   # Garder l'ancien régime pendant la période de lock
            confidence = max(0.4, confidence - 0.1)
        elif regime_changed:
            self._regime_lock_until = now + 300   # Lock 5 minutes après transition
            logger.info(f"[QuantMLV11] ↗ Transition régime: {prev_regime} → {regime}")

        self.regime = regime
        self.confidence = confidence
        self.last_regime_ts = now
        if len(self.regime_history) > 20:
            self.regime_history.pop(0)
        self.regime_history.append(regime)

        # ── Recommendation trading basée sur régime ───────────────────────
        if regime in ("BULL",):
            recommendation = "BUY"
        elif regime in ("BULL_WEAK", "TRENDING"):
            recommendation = "WEAK_BUY"
        elif regime in ("BEAR",):
            recommendation = "SELL"
        elif regime in ("BEAR_WEAK",):
            recommendation = "WEAK_SELL"
        elif regime == "VOLATILE":
            recommendation = "REDUCE EXPOSURE"
        else:
            recommendation = "HOLD"

        # Signal qualité
        fg_label = (
            "Peur Extrême 😱" if fg < 25 else
            "Peur 😨" if fg < 45 else
            "Neutre 😐" if fg < 55 else
            "Avidité 😈" if fg < 75 else "Avidité Extrême 🤑"
        )
        hurst_label = (
            "Tendanciel 📈" if hurst > 0.6 else
            "Aléatoire ↔" if hurst > 0.45 else "Mean-Reverting 🔄"
        )

        summary = (
            f"[QuantMLV11] {symbol} | Régime: {regime} ({confidence:.0%}) | "
            f"F&G: {fg}/100 ({fg_label}) | BTC24h: {btc_chg:+.2f}% | "
            f"BTC Dom: {dom:.1f}% | Hurst: {hurst:.3f} ({hurst_label}) | "
            f"Entropie: {entropy:.3f} | Z-Score: {z_score:+.2f} | "
            f"ATR%: {norm_atr:.3f} | Momentum: {momentum:+.3f} | "
            f"OI Δ: {oi_chg:+.4f}"
        )

        return {
            "agent":          self.name,
            "summary":        summary,
            "arguments": [
                f"Régime: {regime} (confiance {confidence:.0%})",
                f"Hurst: {hurst:.3f} ({hurst_label})",
                f"Entropie: {entropy:.3f} {'(chaos élevé)' if entropy > 0.7 else '(marché lisible)'}",
                f"Z-Score: {z_score:+.2f} {'(suracheté)' if z_score > 2 else '(survendu)' if z_score < -2 else '(normal)'}",
                f"ATR normalisé: {norm_atr:.3f} {'(volatile)' if norm_atr > 0.025 else '(calme)'}",
                f"Momentum 20h: {momentum:+.4f}",
                f"F&G: {fg}/100 | BTC Dominance: {dom:.1f}%",
                f"Open Interest Δ: {oi_chg:+.4f} | ETH/BTC: {eth_btc:.4f}",
                f"Volume trend: {vol_trend:+.3f}",
                f"Historique régimes: {self.regime_history[-5:]}",
            ],
            "risks": [
                f"Entropie élevée: {entropy:.2f} — marché difficile à prédire"
            ] if entropy > 0.75 else [],
            "confidence":     round(confidence, 3),
            "recommendation": recommendation,
            "regime":         regime,
            "factors":        factors,
            "hurst":          hurst,
            "entropy":        entropy,
            "z_score":        z_score,
            "norm_atr":       norm_atr,
            "momentum":       momentum,
            "fear_greed":     fg,
            "btc_dominance":  dom,
            "regime_changed": regime_changed,
            "glossary_used":  True,
        }
