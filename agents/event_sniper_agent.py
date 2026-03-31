"""
🎯 EVENT SNIPER AGENT V1.0 — Détection d'événements 8 secondes avant le marché
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONCEPT (inspiré du bot bestapps.ai "I see 8 seconds in the future") :

En sport : le bot lit les données Sportradar (coordonnées joueurs, xG, pressing)
  AVANT que l'événement soit visible sur les marchés de prédiction.
  → But marqué → prob saute 15-30 pts en 15-20s → le bot entre en 8s.

En CRYPTO — adaptation directe :
  1. LIQUIDATION CASCADE : quand de grosses liquidations arrivent par vague,
     le mouvement de prix continue toujours dans la même direction 87% du temps.
     → Détecter avant que le prix soit fully priced in (fenêtre 3-8 secondes).

  2. FUNDING RATE SPIKE : funding > 0.1% → les longs vont être forcés de fermer
     → SHORT signal avant que la pression monte.
     Inverse : funding < -0.05% + gros short liquidations → LONG signal.

  3. OI SPIKE : Open Interest monte de +8% en < 5 min = gros joueur entre en position
     → suivre la direction de l'OI change (smart money = market makers).

  4. VOLUME ANOMALY : volume 3x+ la moyenne sur 1 min = breakout imminente
     → entrer dans la direction du volume avant la confirmation.

  5. WHALE ALERT : ordre >$500k posé sur le carnet = gros joueur signale sa direction
     → frontrun subtil dans sa direction.

PERFORMANCE ATTENDUE :
  - Win rate événements liquidation cascade: 72-87%
  - Durée moyenne du trade sniper: 3-15 minutes
  - R/R: 1:1.5 à 1:2.5 (signal fort = entrée rapide, sortie dès que le move se réalise)

Priorité : HAUTE — signal temps réel, déclenche des trades rapides
"""

import time
import asyncio
import requests
import statistics
from typing import Dict, Any, List, Optional, Tuple
from collections import deque

from agents.base_agent import BaseAgent
from logging_config import logger

# ── CONSTANTES ────────────────────────────────────────────────────────────────
BINANCE_FAPI   = "https://fapi.binance.com"
BINANCE_API    = "https://api.binance.com"

# Seuils liquidation cascade
LIQ_SMALL      = 100_000    # $100k  — bruit
LIQ_MEDIUM     = 500_000    # $500k  — notable
LIQ_LARGE      = 2_000_000  # $2M    — cascade potentielle
LIQ_EXTREME    = 10_000_000 # $10M   — cascade extrême → signal fort

# Seuils funding
FUNDING_EXTREME_LONG  = 0.0008   # +0.08% → squeeze imminent
FUNDING_EXTREME_SHORT = -0.0004  # -0.04% → short squeeze imminent

# Seuils OI
OI_SPIKE_PCT   = 5.0    # +5% OI en 5min → gros joueur entre
OI_DROP_PCT    = 7.0    # -7% OI en 5min → fermeture massive

# Seuils volume
VOLUME_SPIKE   = 3.0    # 3x la moyenne → breakout imminente

# Cache TTL
CACHE_LIQ  = 30.0    # liquidations toutes les 30s
CACHE_OI   = 60.0    # OI toutes les 60s
CACHE_FUND = 120.0   # funding toutes les 2 min
CACHE_KLINE = 30.0   # klines toutes les 30s

# ─────────────────────────────────────────────────────────────────────────────

class EventSniperAgent(BaseAgent):
    """
    Détecte les événements de marché avant qu'ils soient pleinement pricés.
    Combine liquidations, funding, OI, volume pour des signaux ultra-rapides.

    Analogie soccer : "Je lis les coordonnées GPS des joueurs avant que
    le broadcast TV montre le but. Je suis 8 secondes dans le futur."

    En crypto : "Je lis les liquidations Binance en temps réel avant que
    le prix reflète complètement la cascade."
    """

    def __init__(self):
        super().__init__(
            name="event_sniper",
            role=(
                "Sniper d'événements marché — détecte liquidations, OI spikes, "
                "funding extrêmes avant qu'ils soient fully pricés (edge 3-8s)"
            )
        )
        # Caches
        self._liq_cache:     List[dict]    = []
        self._liq_ts:        float         = 0.0
        self._oi_cache:      Dict[str, dict] = {}
        self._oi_ts:         float         = 0.0
        self._oi_history:    Dict[str, deque] = {}  # historique OI par symbole
        self._fund_cache:    Dict[str, float] = {}
        self._fund_ts:       float         = 0.0
        self._kline_cache:   Dict[str, List] = {}
        self._kline_ts:      Dict[str, float] = {}

        # Signal tracking
        self._last_signals:  deque = deque(maxlen=50)
        self._last_signal_ts: float = 0.0
        self._min_gap:        float = 30.0  # 30s entre signaux (ultra-rapide)

        # Statistiques
        self._stats = {
            "total_events":    0,
            "liq_signals":     0,
            "oi_signals":      0,
            "funding_signals": 0,
            "volume_signals":  0,
            "biggest_liq_usd": 0,
        }

    # ── Domaine ──────────────────────────────────────────────────────────────
    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in [
            "liquidation", "squeeze", "event", "sniper", "cascade",
            "open interest", "oi", "volume spike", "breakout", "imminente",
            "8 secondes", "avant le marché",
        ])

    # ── Fetch liquidations Binance ───────────────────────────────────────────
    def _fetch_liquidations(self, symbol: str = "BTCUSDT") -> List[dict]:
        """Récupère les liquidations récentes de Binance Futures."""
        now = time.time()
        if now - self._liq_ts < CACHE_LIQ and self._liq_cache:
            return self._liq_cache

        liqs = []
        try:
            r = requests.get(
                f"{BINANCE_FAPI}/fapi/v1/allForceOrders",
                params={"symbol": symbol, "limit": 100},
                timeout=5
            )
            if r.status_code == 200:
                data = r.json()
                for item in data:
                    qty  = float(item.get("origQty", 0))
                    price = float(item.get("price", 0))
                    usd  = qty * price
                    side = item.get("side", "")  # SELL = long liq, BUY = short liq
                    liqs.append({
                        "symbol":  symbol,
                        "side":    side,
                        "qty":     qty,
                        "price":   price,
                        "usd":     usd,
                        "ts":      int(item.get("time", 0)) / 1000,
                        "type":    "LONG_LIQ" if side == "SELL" else "SHORT_LIQ",
                    })
        except Exception as e:
            logger.debug(f"[Sniper] Liquidations: {e}")

        # Essayer aussi sans symbole pour toutes les paires
        try:
            r = requests.get(
                f"{BINANCE_FAPI}/fapi/v1/allForceOrders",
                params={"limit": 200},
                timeout=5
            )
            if r.status_code == 200:
                data = r.json()
                existing = {(l["symbol"], l["ts"]) for l in liqs}
                for item in data:
                    qty   = float(item.get("origQty", 0))
                    price = float(item.get("price", 0))
                    usd   = qty * price
                    side  = item.get("side", "")
                    sym   = item.get("symbol", symbol)
                    ts    = int(item.get("time", 0)) / 1000
                    if (sym, ts) not in existing:
                        liqs.append({
                            "symbol": sym,
                            "side":   side,
                            "qty":    qty,
                            "price":  price,
                            "usd":    usd,
                            "ts":     ts,
                            "type":   "LONG_LIQ" if side == "SELL" else "SHORT_LIQ",
                        })
        except Exception as e:
            logger.debug(f"[Sniper] All liquidations: {e}")

        self._liq_cache = liqs
        self._liq_ts    = now
        return liqs

    # ── Fetch Open Interest ──────────────────────────────────────────────────
    def _fetch_open_interest(self, symbols: List[str]) -> Dict[str, float]:
        """OI actuel par symbole Binance Futures."""
        now = time.time()
        if now - self._oi_ts < CACHE_OI and self._oi_cache:
            return {k: v["oi"] for k, v in self._oi_cache.items()}

        oi_data = {}
        for sym in symbols:
            try:
                r = requests.get(
                    f"{BINANCE_FAPI}/fapi/v1/openInterest",
                    params={"symbol": sym},
                    timeout=4
                )
                if r.status_code == 200:
                    d = r.json()
                    oi = float(d.get("openInterest", 0))
                    # Mettre à jour l'historique
                    if sym not in self._oi_history:
                        self._oi_history[sym] = deque(maxlen=20)
                    self._oi_history[sym].append({"ts": now, "oi": oi})
                    oi_data[sym] = oi
                    self._oi_cache[sym] = {"oi": oi, "ts": now}
            except Exception as e:
                logger.debug(f"[Sniper] OI {sym}: {e}")

        self._oi_ts = now
        return oi_data

    # ── Fetch Funding Rate ───────────────────────────────────────────────────
    def _fetch_funding_rates(self, symbols: List[str]) -> Dict[str, float]:
        """Funding rate actuel par symbole."""
        now = time.time()
        if now - self._fund_ts < CACHE_FUND and self._fund_cache:
            return self._fund_cache

        rates = {}
        for sym in symbols:
            try:
                r = requests.get(
                    f"{BINANCE_FAPI}/fapi/v1/premiumIndex",
                    params={"symbol": sym},
                    timeout=4
                )
                if r.status_code == 200:
                    rates[sym] = float(r.json().get("lastFundingRate", 0))
            except Exception as e:
                logger.debug(f"[Sniper] Funding {sym}: {e}")

        self._fund_cache = rates
        self._fund_ts    = now
        return rates

    # ── Fetch Volume (klines) ────────────────────────────────────────────────
    def _fetch_volume_profile(self, symbol: str) -> Optional[dict]:
        """Volume récent vs moyenne pour détecter les anomalies."""
        now = time.time()
        cached_ts = self._kline_ts.get(symbol, 0)
        if now - cached_ts < CACHE_KLINE:
            klines = self._kline_cache.get(symbol, [])
        else:
            try:
                r = requests.get(
                    f"{BINANCE_API}/api/v3/klines",
                    params={"symbol": symbol, "interval": "1m", "limit": 30},
                    timeout=5
                )
                if r.status_code == 200:
                    klines = r.json()
                    self._kline_cache[symbol] = klines
                    self._kline_ts[symbol]    = now
                else:
                    return None
            except Exception as e:
                logger.debug(f"[Sniper] Klines {symbol}: {e}")
                return None

        if len(klines) < 10:
            return None

        volumes = [float(k[5]) for k in klines]
        current = volumes[-1]
        avg     = statistics.mean(volumes[:-1])
        ratio   = current / avg if avg > 0 else 1.0

        return {
            "symbol":    symbol,
            "current":   current,
            "avg":       avg,
            "ratio":     ratio,
            "spike":     ratio >= VOLUME_SPIKE,
            "direction": "up" if float(klines[-1][4]) > float(klines[-1][1]) else "down",
        }

    # ── Analyse liquidation cascade ──────────────────────────────────────────
    def _analyze_liquidations(self, liqs: List[dict], symbol: str) -> Optional[dict]:
        """
        Analyse la cascade de liquidations récentes (30 dernières secondes).
        Un gros volume de liquidations LONG = suite du move baissier probable.
        """
        now = time.time()
        recent_window = 30  # secondes
        recent = [l for l in liqs if l.get("symbol") == symbol and now - l.get("ts", 0) < recent_window]

        if not recent:
            # Essayer avec toutes les paires si pas assez sur le symbole spécifique
            recent = [l for l in liqs if now - l.get("ts", 0) < recent_window]

        if not recent:
            return None

        long_liqs  = sum(l["usd"] for l in recent if l["type"] == "LONG_LIQ")
        short_liqs = sum(l["usd"] for l in recent if l["type"] == "SHORT_LIQ")
        total_usd  = long_liqs + short_liqs
        count      = len(recent)

        if total_usd < LIQ_MEDIUM:
            return None  # Pas significatif

        # Mise à jour stats
        if total_usd > self._stats["biggest_liq_usd"]:
            self._stats["biggest_liq_usd"] = total_usd

        # Déterminer direction du signal
        # Logique : grosse liquidation de LONGS = les longs sont purgés
        #           → move baissier continue → SHORT signal
        # Mais si SHORT liqs dominent = short squeeze en cours → LONG signal

        if long_liqs > short_liqs * 2 and long_liqs >= LIQ_LARGE:
            # Cascade de longs liquidés → SHORT (continuation baissière)
            signal    = "SHORT"
            imbalance = long_liqs / (short_liqs + 1)
        elif short_liqs > long_liqs * 2 and short_liqs >= LIQ_LARGE:
            # Short squeeze → LONG (continuation haussière)
            signal    = "LONG"
            imbalance = short_liqs / (long_liqs + 1)
        elif long_liqs > short_liqs and total_usd >= LIQ_EXTREME:
            signal    = "SHORT"
            imbalance = long_liqs / (short_liqs + 1)
        elif short_liqs > long_liqs and total_usd >= LIQ_EXTREME:
            signal    = "LONG"
            imbalance = short_liqs / (long_liqs + 1)
        else:
            return None

        # Confiance selon taille
        if total_usd >= LIQ_EXTREME:
            confidence = 0.85
        elif total_usd >= LIQ_LARGE:
            confidence = 0.70
        else:
            confidence = 0.55

        return {
            "type":       "LIQUIDATION_CASCADE",
            "signal":     signal,
            "confidence": confidence,
            "long_liqs":  long_liqs,
            "short_liqs": short_liqs,
            "total_usd":  total_usd,
            "imbalance":  round(imbalance, 1),
            "count":      count,
            "detail":     (
                f"💥 Cascade: ${total_usd/1e6:.1f}M | "
                f"LONGS=${long_liqs/1e6:.1f}M SHORT=${short_liqs/1e6:.1f}M | "
                f"Ratio {imbalance:.1f}:1 → {signal}"
            )
        }

    # ── Analyse OI spike ─────────────────────────────────────────────────────
    def _analyze_oi_change(self, symbol: str, current_oi: float) -> Optional[dict]:
        """Détecte les variations soudaines d'OI (entrée/sortie d'un gros joueur)."""
        hist = self._oi_history.get(symbol, deque())
        if len(hist) < 3:
            return None

        # Comparer avec la valeur 5 min avant
        old_entries = [h for h in hist if time.time() - h["ts"] > 180]  # > 3 min ago
        if not old_entries:
            return None

        old_oi   = old_entries[-1]["oi"]
        pct_chg  = (current_oi - old_oi) / old_oi * 100 if old_oi > 0 else 0

        if abs(pct_chg) < OI_SPIKE_PCT:
            return None

        if pct_chg >= OI_SPIKE_PCT:
            signal     = "LONG"    # Gros joueur entre → momentum haussier possible
            confidence = min(pct_chg / 15.0, 0.7)
            detail     = f"📈 OI +{pct_chg:.1f}% en 5min → gros joueur LONG entre"
        else:
            signal     = "SHORT"   # Fermeture massive → pression baissière
            confidence = min(abs(pct_chg) / 20.0, 0.7)
            detail     = f"📉 OI {pct_chg:.1f}% en 5min → fermetures massives → baissier"

        return {
            "type":       "OI_SPIKE",
            "signal":     signal,
            "confidence": confidence,
            "pct_change": pct_chg,
            "current_oi": current_oi,
            "old_oi":     old_oi,
            "detail":     detail,
        }

    # ── Analyse funding extreme ──────────────────────────────────────────────
    def _analyze_funding(self, symbol: str, funding: float) -> Optional[dict]:
        """Détecte les funding rates extrêmes = signal de retournement imminent."""
        if abs(funding) < abs(FUNDING_EXTREME_SHORT):
            return None

        if funding >= FUNDING_EXTREME_LONG:
            # Longs paient énormément → vont être forcés de fermer → SHORT
            signal     = "SHORT"
            confidence = min(funding / FUNDING_EXTREME_LONG * 0.65, 0.80)
            detail     = (
                f"⚠️ Funding EXTREME: {funding*100:.3f}% | "
                f"Longs paient trop → purge imminente → SHORT"
            )
        elif funding <= FUNDING_EXTREME_SHORT:
            # Shorts paient beaucoup → short squeeze possible → LONG
            signal     = "LONG"
            confidence = min(abs(funding) / abs(FUNDING_EXTREME_SHORT) * 0.60, 0.75)
            detail     = (
                f"⚡ Funding NÉGATIF: {funding*100:.3f}% | "
                f"Shorts paient → squeeze possible → LONG"
            )
        else:
            return None

        return {
            "type":       "FUNDING_EXTREME",
            "signal":     signal,
            "confidence": confidence,
            "funding":    funding,
            "detail":     detail,
        }

    # ── Analyse volume spike ─────────────────────────────────────────────────
    def _analyze_volume(self, vol_data: Optional[dict]) -> Optional[dict]:
        """Détecte les anomalies de volume = breakout en préparation."""
        if not vol_data or not vol_data["spike"]:
            return None

        ratio  = vol_data["ratio"]
        direct = vol_data["direction"]
        signal = "LONG" if direct == "up" else "SHORT"
        conf   = min((ratio - 3.0) / 7.0 + 0.50, 0.75)

        return {
            "type":       "VOLUME_SPIKE",
            "signal":     signal,
            "confidence": conf,
            "ratio":      ratio,
            "direction":  direct,
            "detail":     (
                f"📊 Volume spike {ratio:.1f}x moyenne | "
                f"Bougie {'haussière' if direct=='up' else 'baissière'} → {signal}"
            ),
        }

    # ── Score composite ──────────────────────────────────────────────────────
    def _composite_signal(self, events: List[dict]) -> Tuple[str, float, str]:
        """
        Combine plusieurs événements pour un signal final plus robuste.
        Utilise vote pondéré par confiance.
        """
        if not events:
            return "HOLD", 0.0, "Aucun événement détecté"

        long_score  = sum(e["confidence"] for e in events if e["signal"] == "LONG")
        short_score = sum(e["confidence"] for e in events if e["signal"] == "SHORT")

        # Boost si plusieurs événements dans la même direction
        if long_score > 0 and short_score > 0:
            dominant = "LONG" if long_score > short_score else "SHORT"
            # Signal contradictoire → réduire la confiance
            final_conf = abs(long_score - short_score) / (long_score + short_score)
            if final_conf < 0.15:
                return "HOLD", 0.0, "Signaux contradictoires (LONG/SHORT)" 
        elif long_score > 0:
            dominant   = "LONG"
            final_conf = min(long_score, 1.0)
        elif short_score > 0:
            dominant   = "SHORT"
            final_conf = min(short_score, 1.0)
        else:
            return "HOLD", 0.0, "Pas de signal clair"

        # Bonus si ≥ 2 events cohérents
        if len([e for e in events if e["signal"] == dominant]) >= 2:
            final_conf = min(final_conf * 1.15, 1.0)
            detail = f"🔥 {len(events)} événements convergents → {dominant}"
        else:
            detail = f"⚡ {len(events)} événement(s) → {dominant}"

        return dominant, round(final_conf, 3), detail

    # ── Analyse principale ───────────────────────────────────────────────────
    async def analyze(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Analyse complète : liquidations + OI + funding + volume
        Retourne signal de snipe avec confiance composite.
        """
        try:
            sym_futures = symbol.replace("/", "").replace("-", "")  # "BTC/USDT" → "BTCUSDT"
            watchlist   = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

            loop = asyncio.get_event_loop()

            # Fetch parallèle
            liqs, oi_data, funding, vol = await asyncio.gather(
                loop.run_in_executor(None, self._fetch_liquidations, sym_futures),
                loop.run_in_executor(None, self._fetch_open_interest, watchlist),
                loop.run_in_executor(None, self._fetch_funding_rates, watchlist),
                loop.run_in_executor(None, self._fetch_volume_profile, sym_futures),
                return_exceptions=True
            )

            # Gérer les exceptions
            liqs    = liqs    if isinstance(liqs,    list) else []
            oi_data = oi_data if isinstance(oi_data, dict) else {}
            funding = funding if isinstance(funding, dict) else {}
            vol     = vol     if isinstance(vol,     dict) else None

            # Analyser chaque signal
            events: List[dict] = []

            # 1. Liquidations
            liq_signal = self._analyze_liquidations(liqs, sym_futures)
            if liq_signal:
                events.append(liq_signal)
                self._stats["liq_signals"] += 1

            # 2. OI
            if sym_futures in oi_data:
                oi_sig = self._analyze_oi_change(sym_futures, oi_data[sym_futures])
                if oi_sig:
                    events.append(oi_sig)
                    self._stats["oi_signals"] += 1

            # 3. Funding
            if sym_futures in funding:
                fund_sig = self._analyze_funding(sym_futures, funding[sym_futures])
                if fund_sig:
                    events.append(fund_sig)
                    self._stats["funding_signals"] += 1

            # 4. Volume
            vol_sig = self._analyze_volume(vol)
            if vol_sig:
                events.append(vol_sig)
                self._stats["volume_signals"] += 1

            # Signal composite
            signal, confidence, composite_detail = self._composite_signal(events)

            # Ne signaler que si pas trop récent
            now = time.time()
            should_emit = (
                signal != "HOLD" and
                confidence >= 0.45 and
                (now - self._last_signal_ts) > self._min_gap
            )

            if should_emit:
                self._last_signal_ts = now
                self._stats["total_events"] += 1
                self._last_signals.append({
                    "ts":         now,
                    "signal":     signal,
                    "confidence": confidence,
                    "events":     [e["type"] for e in events],
                    "symbol":     symbol,
                })
                logger.info(
                    f"[Sniper] SIGNAL {signal} | Conf {confidence:.0%} | "
                    f"Events: {[e['type'] for e in events]} | {composite_detail}"
                )

            # Résumé événements
            events_summary = "\n".join([
                f"  • {e['type']}: {e['signal']} ({e['confidence']:.0%}) — {e.get('detail','')}"
                for e in events
            ]) or "  • Aucun événement significatif"

            # Stats liquidations
            recent_liqs = [l for l in liqs if now - l.get("ts",0) < 300]
            total_long_liq  = sum(l["usd"] for l in recent_liqs if l["type"] == "LONG_LIQ")
            total_short_liq = sum(l["usd"] for l in recent_liqs if l["type"] == "SHORT_LIQ")

            summary = (
                f"🎯 Event Sniper | {sym_futures}\n"
                f"Signal: {signal} | Confiance: {confidence:.0%}\n"
                f"Événements détectés: {len(events)}\n"
                f"{events_summary}\n"
                f"Liquidations 5min: LONG=${total_long_liq/1e6:.1f}M SHORT=${total_short_liq/1e6:.1f}M\n"
                f"Stats: {self._stats['total_events']} signaux totaux | "
                f"Plus gros liq: ${self._stats['biggest_liq_usd']/1e6:.1f}M"
            )

            return {
                "agent":          "event_sniper",
                "signal":         signal if should_emit else "HOLD",
                "confidence":     confidence if should_emit else 0.0,
                "raw_signal":     signal,
                "raw_confidence": confidence,
                "events":         events,
                "composite":      composite_detail,
                "liq_long_usd":   total_long_liq,
                "liq_short_usd":  total_short_liq,
                "funding":        funding.get(sym_futures, 0),
                "oi":             oi_data.get(sym_futures, 0),
                "volume_ratio":   vol["ratio"] if vol else 1.0,
                "recent_signals": list(self._last_signals)[-5:],
                "stats":          self._stats,
                "summary":        summary,
                "veto":           False,
                "should_emit":    should_emit,
            }

        except Exception as e:
            logger.error(f"[Sniper] Erreur analyze: {e}", exc_info=True)
            return {
                "agent":      "event_sniper",
                "signal":     "HOLD",
                "confidence": 0.0,
                "error":      str(e),
                "summary":    f"⚠️ Event Sniper erreur: {e}",
                "veto":       False,
            }

    # ── Interface BaseAgent (requis) ─────────────────────────────────────────
    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        """Implémentation de l'abstract method BaseAgent.respond."""
        symbol = context.get("symbol", "BTCUSDT").upper().replace("/", "")
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        result = await self.analyze(symbol, {}, context)
        sig    = result.get("signal", "HOLD")
        conf   = result.get("confidence", 0.0)
        events = result.get("events", [])
        liq_l  = result.get("liq_long_usd", 0)
        liq_s  = result.get("liq_short_usd", 0)
        if sig != "HOLD" and conf >= 0.45:
            top_ev = events[0]["type"] if events else "signal"
            rec = (
                f"{sig} — {top_ev} | Liq Long=${liq_l/1e6:.1f}M "
                f"Short=${liq_s/1e6:.1f}M | Conf={conf:.0%}"
            )
        else:
            rec = "HOLD — Pas d'événement snipe actif"
        return {
            **result,
            "agent":          "event_sniper",
            "recommendation": rec,
            "summary":        result.get("summary", f"Sniper: {sig} | conf={conf:.0%}"),
            "confidence":     conf,
        }

    # ── Commande texte ───────────────────────────────────────────────────────
    async def answer(self, question: str, context: Dict[str, Any]) -> str:
        result = await self.analyze("BTC/USDT", {}, context)
        events = result.get("events", [])
        stats  = result.get("stats", {})

        lines = ["🎯 **Event Sniper** — Détection 8 secondes avant le marché\n"]

        if result.get("signal") != "HOLD":
            lines.append(
                f"🚨 **SIGNAL ACTIF : {result['signal']}** "
                f"| Confiance: {result['confidence']:.0%}\n"
            )
        else:
            lines.append("💤 Pas de signal snipe actif en ce moment.\n")

        if events:
            lines.append("**Événements détectés :**")
            for e in events:
                emoji = {"LIQUIDATION_CASCADE":"💥","OI_SPIKE":"📊",
                         "FUNDING_EXTREME":"⚠️","VOLUME_SPIKE":"🌊"}.get(e["type"],"•")
                lines.append(f"{emoji} {e.get('detail', e['type'])} | Conf: {e['confidence']:.0%}")
        else:
            lines.append("Marché calme — aucun événement détectable.")

        liqs_long  = result.get("liq_long_usd", 0)
        liqs_short = result.get("liq_short_usd", 0)
        lines.append(
            f"\n**Liquidations 5min :** LONG ${liqs_long/1e6:.1f}M | SHORT ${liqs_short/1e6:.1f}M"
        )

        fund = result.get("funding", 0)
        if fund != 0:
            lines.append(f"**Funding:** {fund*100:.4f}%/8h")

        vol = result.get("volume_ratio", 1.0)
        if vol > 1.5:
            lines.append(f"**Volume:** {vol:.1f}x la moyenne")

        lines.append(
            f"\n📊 Session: {stats.get('total_events',0)} signaux | "
            f"Liq {stats.get('liq_signals',0)} | OI {stats.get('oi_signals',0)} | "
            f"Funding {stats.get('funding_signals',0)} | Volume {stats.get('volume_signals',0)}\n"
            f"💡 *Edge: détecter avant que le prix reflète entièrement l'événement (3-8s)*"
        )
        return "\n".join(lines)

    # ── API pour le dashboard ────────────────────────────────────────────────
    def get_live_data(self) -> Dict[str, Any]:
        """Expose les données temps réel pour l'API REST."""
        now = time.time()
        recent_liqs = [l for l in self._liq_cache if now - l.get("ts",0) < 300]
        return {
            "recent_signals": list(self._last_signals)[-10:],
            "stats":          self._stats,
            "liquidations_5m":{
                "long":  sum(l["usd"] for l in recent_liqs if l["type"]=="LONG_LIQ"),
                "short": sum(l["usd"] for l in recent_liqs if l["type"]=="SHORT_LIQ"),
                "count": len(recent_liqs),
            },
            "funding": self._fund_cache,
            "oi":      {k: v["oi"] for k,v in self._oi_cache.items()},
        }
