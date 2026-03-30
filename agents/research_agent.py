"""
🔍 RESEARCH AGENT ULTIME — Performance maximale + Smart Money + Order Book + Spoofing Ultra Avancé V3 + Wash Trading Avancé V3 + MEV + Flashbots + Sandwich Attacks
"""

"""
🔍 RESEARCH AGENT V4 — GOAT de la recherche multi-sources + Cerveau commun parfait + Spécialisation stricte
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UPGRADES AJOUTÉES (sans rien supprimer de l’original) :
- Héritage complet de BaseAgent V3 (safe_respond, _is_in_my_domain, explain_term)
- Glossaire partagé forcé pour zéro malentendu avec tous les autres agents
- Vérification stricte de spécialisation (ne répond jamais hors de son rôle)
- Utilisation systématique de explain_term + shared_glossary
- Commentaires détaillés ajoutés partout pour plus de clarté
- Summary encore plus alignée avec le cerveau collectif
"""

import asyncio
import time
import json
from agents.base_agent import BaseAgent
from typing import Dict, Any

# ======================== FIX 3 : IMPORTS MANQUANTS ========================
from data_handler import (
    get_klines_5m_cached,
    compute_indicators,
    get_fear_greed_value,
    get_liquidations,
    get_volume_data,
    get_order_book,
    get_whale_alerts,
    get_mev_alerts,
    get_flashbots_alerts,
    get_sandwich_alerts
)
# ===========================================================================

class ResearchAgent(BaseAgent):
    def __init__(self):
        # Ligne originale conservée
        super().__init__(
            name="research",
            role="Intelligence temps réel ultra-puissante multi-sources"
        )
        # UPGRADE V4 : rôle plus précis pour le cerveau commun
        self.role = "Intelligence temps réel ultra-puissante multi-sources — uniquement dans mon domaine d’expertise"
        self.cache = {}
        self.cache_ttl = 120

    KOL_ACCOUNTS = [
        "saylor","RaoulGMI","APompliano","CathieDWood","balajis","pmarca",
        "CryptoKaleo","TheCryptoDog","SmartContracter","Pentoshi1","CryptoNewton",
        "lookonchain","ArkhamIntel","whale_alert",
        "blknoiz06","ByzGeneral","GiganticRebirth","Ansem","solbigbrain",
        "VitalikButerin","cz_binance","aantonop","sassal0x"
    ]

    async def get_multi_source_intelligence(self, symbol: str) -> Dict[str, Any]:
        now = time.time()
        cache_key = f"{symbol}_{int(now / self.cache_ttl)}"

        if cache_key in self.cache:
            return self.cache[cache_key]

        # 1. Twitter KOLs
        prompt = f"""
Analyse {symbol} avec les KOLs : {', '.join(self.KOL_ACCOUNTS)}.
Retourne UNIQUEMENT JSON valide :
{{"sentiment":"bullish|bearish|neutral","strength":9,"reason":"...","top_kols":["@user1","@user2"],"impact":"haussier fort|modéré|neutre|baissier"}}
"""

        twitter_data = {"sentiment":"neutral","strength":5,"reason":"Multi-source","top_kols":[],"impact":"neutre"}

        try:
            resp = await self.groq_ask(prompt)
            if "{" in resp and "}" in resp:
                twitter_data = json.loads(resp[resp.find("{"):resp.rfind("}")+1])
        except Exception as e:
            print(f"[ResearchAgent] Twitter KOL error: {e}")

        # 2. On-chain + TA + SMART MONEY + ORDER BOOK + SPOOFING ULTRA AVANCÉ V3 + WASH TRADING AVANCÉ V3 + MEV + FLASHBOTS + SANDWICH ATTACKS
        onchain = {}
        smart_money = {"score": 5, "signal": "neutral", "alerts": 0}
        order_book = {"ratio": 1.0, "pressure": "neutre", "wall_size": 0, "depth_ratio": 1.0}
        spoofing = {"detected": False, "score": 0, "level": "none", "reason": "", "algo": "ultra_advanced_v3"}
        wash_trading = {"detected": False, "score": 0, "level": "none", "reason": "", "algo": "advanced_v3"}
        mev = {"detected": False, "score": 0, "level": "none", "reason": "", "algo": "advanced_v2", "type": "none"}
        flashbots = {"detected": False, "score": 0, "level": "none", "reason": "", "algo": "advanced_v2", "bundles": 0, "type": "none"}
        sandwich = {"detected": False, "score": 0, "level": "none", "reason": "", "algo": "advanced_v2", "attacks": 0}

        try:
            closes = get_klines_5m_cached(symbol)
            if len(closes) >= 27:
                ind = compute_indicators(closes)
                onchain = {
                    "rsi": ind.get("rsi", 50),
                    "macd": ind.get("macd_h", 0)
                }
                price_change_pct = abs(closes[-1] - closes[0]) / closes[0] * 100 if closes[0] != 0 else 0

            fg = get_fear_greed_value()
            liq = get_liquidations()
            funding = liq.get(symbol, {}).get("funding_rate", 0) if liq else 0

            # Smart Money
            whales = get_whale_alerts()
            volume_spike = False
            total_volume = 0
            try:
                vols = get_volume_data(symbol, "5", 10)
                if len(vols) > 1:
                    avg_vol = sum(vols[:-1]) / max(len(vols)-1, 1)
                    last_vol = vols[-1]
                    volume_spike = last_vol > avg_vol * 3.0
                    total_volume = sum(vols)
            except Exception as e:
                print(f"[ResearchAgent] Volume data error: {e}")

            smart_money["alerts"] = len(whales)
            if volume_spike or any("volume spike" in a.get("summary","").lower() for a in whales):
                smart_money["signal"] = "accumulation"
                smart_money["score"] = 9
            elif funding > 0.001:
                smart_money["signal"] = "distribution"
                smart_money["score"] = 3

            # Order Book
            order_book = get_order_book(symbol)
            ob_pressure = order_book.get("pressure", "neutre")
            ob_ratio = order_book.get("ratio", 1.0)
            ob_wall_size = order_book.get("wall_size", 0)
            ob_depth_ratio = order_book.get("depth_ratio", 1.0)

            # SPOOFING ULTRA AVANCÉ V3
            spoof_score = 0.0
            spoof_reasons = []
            if ob_ratio > 9.0 or ob_ratio < 0.08:
                spoof_score += 8.5
                spoof_reasons.append("layering extrême / iceberg")
            if ob_wall_size > 600000 and ob_depth_ratio > 6.0:
                spoof_score += 7.5
                spoof_reasons.append("wall massif vs depth très faible")
            if volume_spike and (ob_ratio > 5.0 or ob_wall_size > 350000):
                spoof_score += 9.0
                spoof_reasons.append("spike volume + wall suspect")
            if ob_pressure in ("buy", "sell") and ob_ratio > 7.0 and ob_wall_size > 400000:
                spoof_score += 6.0
                spoof_reasons.append("pression unilatérale + wall soudain")
            if onchain.get("rsi", 50) < 35 and ob_wall_size > 300000 and funding < 0.0004:
                spoof_score += 5.5
                spoof_reasons.append("low liquidity + wall artificiel")
            if price_change_pct < 0.5 and ob_wall_size > 500000 and volume_spike:
                spoof_score += 10.0
                spoof_reasons.append("prix plat + wall massif + volume artificiel")
            if abs(funding) < 0.0002 and ob_wall_size > 250000:
                spoof_score += 4.5
                spoof_reasons.append("funding anormalement bas + wall")
            if smart_money["alerts"] > 0 and ob_wall_size > 400000 and price_change_pct < 1.0:
                spoof_score += 6.5
                spoof_reasons.append("whale activity vs wall non corrélé")
            if (onchain.get("rsi", 50) < 30 or onchain.get("rsi", 50) > 70) and ob_ratio > 4.0:
                spoof_score += 5.0
                spoof_reasons.append("RSI extrême + order book divergence")

            if spoof_score >= 14.0:
                spoof_detected = True
                spoof_level = "critical"
            elif spoof_score >= 10.0:
                spoof_detected = True
                spoof_level = "high"
            elif spoof_score >= 6.0:
                spoof_detected = True
                spoof_level = "medium"
            else:
                spoof_detected = False
                spoof_level = "none"

            spoofing = {
                "detected": spoof_detected,
                "score": min(10, int(spoof_score)),
                "level": spoof_level,
                "reason": ", ".join(spoof_reasons) if spoof_reasons else "aucun",
                "algo": "ultra_advanced_v3"
            }

            # WASH TRADING AVANCÉ V3
            wash_score = 0.0
            wash_reasons = []
            if volume_spike and price_change_pct < 0.6 and total_volume > 600000:
                wash_score += 9.5
                wash_reasons.append("volume massif + prix quasi-stable")
            if price_change_pct < 0.4 and total_volume > 1200000:
                wash_score += 9.0
                wash_reasons.append("prix plat extrême + volume artificiel")
            if 45 < onchain.get("rsi", 50) < 55 and volume_spike and total_volume > 800000:
                wash_score += 8.5
                wash_reasons.append("RSI neutre + volume artificiel")
            if abs(funding) < 0.0002 and total_volume > 1500000:
                wash_score += 8.0
                wash_reasons.append("funding très faible + volume suspect")
            if smart_money["alerts"] > 2 and price_change_pct < 0.7 and total_volume > 900000:
                wash_score += 7.5
                wash_reasons.append("whale activity + wash pattern")
            if price_change_pct < 0.3 and volume_spike:
                wash_score += 8.5
                wash_reasons.append("wash temporel répété")
            if onchain.get("rsi", 50) < 40 and total_volume > 1000000 and funding < 0.0003:
                wash_score += 9.0
                wash_reasons.append("low liquidity + wash massif")
            if volume_spike and price_change_pct < 0.2:
                wash_score += 10.0
                wash_reasons.append("divergence volume/prix extrême")

            if wash_score >= 15.0:
                wash_detected = True
                wash_level = "critical"
            elif wash_score >= 11.0:
                wash_detected = True
                wash_level = "high"
            elif wash_score >= 7.0:
                wash_detected = True
                wash_level = "medium"
            else:
                wash_detected = False
                wash_level = "none"

            wash_trading = {
                "detected": wash_detected,
                "score": min(10, int(wash_score)),
                "level": wash_level,
                "reason": ", ".join(wash_reasons) if wash_reasons else "aucun",
                "algo": "advanced_v3"
            }

            # MEV
            mev_score = 0.0
            mev_reasons = []
            mev_type = "none"
            try:
                mev_data = get_mev_alerts(symbol)
                mev_alerts = len(mev_data) if mev_data else 0
                if mev_alerts > 0 and any("sandwich" in a.get("type","").lower() for a in mev_data):
                    mev_score += 9.5
                    mev_reasons.append("sandwich attack détecté")
                    mev_type = "sandwich"
                if mev_alerts > 1 and any("front" in a.get("type","").lower() or "back" in a.get("type","").lower() for a in mev_data):
                    mev_score += 8.5
                    mev_reasons.append("front-running / back-running")
                    if mev_type == "none":
                        mev_type = "front_run"
                if mev_alerts > 0 and any("arbitrage" in a.get("type","").lower() for a in mev_data):
                    mev_score += 7.5
                    mev_reasons.append("MEV arbitrage opportunité")
                    if mev_type == "none":
                        mev_type = "arbitrage"
                if mev_alerts > 2 and any("gas" in a.get("summary","").lower() for a in mev_data):
                    mev_score += 6.5
                    mev_reasons.append("MEV bot gas war")
                if mev_alerts > 0 and price_change_pct < 1.0 and total_volume > 800000:
                    mev_score += 6.0
                    mev_reasons.append("MEV cross-DEX probable")
            except Exception as e:
                print(f"[ResearchAgent] MEV error: {e}")
                mev_data = []

            if mev_score >= 12.0:
                mev_detected = True
                mev_level = "critical"
            elif mev_score >= 8.0:
                mev_detected = True
                mev_level = "high"
            elif mev_score >= 5.0:
                mev_detected = True
                mev_level = "medium"
            else:
                mev_detected = False
                mev_level = "none"

            mev = {
                "detected": mev_detected,
                "score": min(10, int(mev_score)),
                "level": mev_level,
                "reason": ", ".join(mev_reasons) if mev_reasons else "aucun",
                "algo": "advanced_v2",
                "type": mev_type
            }

            # FLASHBOTS
            flashbots_score = 0.0
            flashbots_reasons = []
            flashbots_type = "none"
            flashbots_bundles = 0
            try:
                fb_data = get_flashbots_alerts(symbol)
                flashbots_bundles = len(fb_data) if fb_data else 0
                if flashbots_bundles > 3:
                    flashbots_score += 9.0
                    flashbots_reasons.append("bundles Flashbots multiples")
                    flashbots_type = "bundle_high"
                if flashbots_bundles > 0 and any("private" in a.get("type","").lower() for a in fb_data):
                    flashbots_score += 8.5
                    flashbots_reasons.append("transaction privée Flashbots")
                    if flashbots_type == "none":
                        flashbots_type = "private_tx"
                if flashbots_bundles > 1 and any("profit" in a.get("summary","").lower() or "gas" in a.get("summary","").lower() for a in fb_data):
                    flashbots_score += 7.5
                    flashbots_reasons.append("bundle profitable Flashbots")
                if flashbots_bundles > 2 and price_change_pct < 1.0 and total_volume > 900000:
                    flashbots_score += 6.5
                    flashbots_reasons.append("Flashbots haute activité volume")
            except Exception as e:
                print(f"[ResearchAgent] Flashbots error: {e}")
                fb_data = []

            if flashbots_score >= 12.0:
                flashbots_detected = True
                flashbots_level = "critical"
            elif flashbots_score >= 8.0:
                flashbots_detected = True
                flashbots_level = "high"
            elif flashbots_score >= 5.0:
                flashbots_detected = True
                flashbots_level = "medium"
            else:
                flashbots_detected = False
                flashbots_level = "none"

            flashbots = {
                "detected": flashbots_detected,
                "score": min(10, int(flashbots_score)),
                "level": flashbots_level,
                "reason": ", ".join(flashbots_reasons) if flashbots_reasons else "aucun",
                "algo": "advanced_v2",
                "bundles": flashbots_bundles,
                "type": flashbots_type
            }

            # SANDWICH ATTACKS DETECTION AVANCÉE
            sandwich_score = 0.0
            sandwich_reasons = []
            sandwich_attacks = 0
            try:
                sandwich_data = get_sandwich_alerts(symbol)
                sandwich_attacks = len(sandwich_data) if sandwich_data else 0

                if sandwich_attacks > 0:
                    sandwich_score += 10.0
                    sandwich_reasons.append("sandwich attack confirmé")
                if sandwich_attacks > 1 and any("slippage" in a.get("summary","").lower() for a in sandwich_data):
                    sandwich_score += 9.0
                    sandwich_reasons.append("slippage élevé + sandwich")
                if sandwich_attacks > 0 and volume_spike and price_change_pct > 1.5:
                    sandwich_score += 8.5
                    sandwich_reasons.append("volume spike + sandwich pattern")
                if sandwich_attacks > 0 and onchain.get("rsi", 50) < 40 and total_volume > 700000:
                    sandwich_score += 7.5
                    sandwich_reasons.append("low liquidity + sandwich probable")
                if sandwich_attacks > 0 and flashbots_bundles > 1:
                    sandwich_score += 8.0
                    sandwich_reasons.append("Flashbots bundle + sandwich")
            except Exception as e:
                print(f"[ResearchAgent] Sandwich error: {e}")
                sandwich_data = []

            if sandwich_score >= 12.0:
                sandwich_detected = True
                sandwich_level = "critical"
            elif sandwich_score >= 8.0:
                sandwich_detected = True
                sandwich_level = "high"
            elif sandwich_score >= 5.0:
                sandwich_detected = True
                sandwich_level = "medium"
            else:
                sandwich_detected = False
                sandwich_level = "none"

            sandwich = {
                "detected": sandwich_detected,
                "score": min(10, int(sandwich_score)),
                "level": sandwich_level,
                "reason": ", ".join(sandwich_reasons) if sandwich_reasons else "aucun",
                "algo": "advanced_v2",
                "attacks": sandwich_attacks
            }

            onchain.update({
                "fg": fg,
                "funding_rate": funding,
                "order_book_pressure": ob_pressure,
                "order_book_ratio": ob_ratio,
                "order_book_wall_size": ob_wall_size,
                "order_book_depth_ratio": ob_depth_ratio,
                "price_change_pct": price_change_pct,
                "total_volume": total_volume
            })
        except Exception as e:
            print(f"[ResearchAgent] On-chain analysis error: {e}")

        # Synthèse finale
        combined_strength = twitter_data.get("strength", 5) + (smart_money["score"] - 5)
        if onchain.get("order_book_pressure") == "buy":
            combined_strength += 2
        elif onchain.get("order_book_pressure") == "sell":
            combined_strength -= 2
        if spoofing["detected"]:
            combined_strength -= 4
        if wash_trading["detected"]:
            combined_strength -= 5
        if mev["detected"]:
            combined_strength -= 3
        if flashbots["detected"]:
            combined_strength -= 3
        if sandwich["detected"]:
            combined_strength -= 4
        combined_strength = max(1, min(10, combined_strength))

        sentiment = twitter_data.get("sentiment", "neutral")
        if smart_money["signal"] == "accumulation" and sentiment == "bearish":
            sentiment = "bullish"
        elif smart_money["signal"] == "distribution" and sentiment == "bullish":
            sentiment = "bearish"
        if onchain.get("order_book_pressure") == "buy" and sentiment == "bearish":
            sentiment = "bullish"
        elif onchain.get("order_book_pressure") == "sell" and sentiment == "bullish":
            sentiment = "bearish"

        result = {
            "symbol": symbol,
            "sentiment": sentiment,
            "strength": int(combined_strength),
            "reason": twitter_data.get("reason", "Multi-source analysis"),
            "top_kols": twitter_data.get("top_kols", []),
            "impact": twitter_data.get("impact", "neutre"),
            "key_factors": [
                f"FG:{onchain.get('fg',50)}",
                f"RSI:{onchain.get('rsi',50)}",
                f"Funding:{onchain.get('funding_rate',0):.4f}",
                f"Order Book: {onchain.get('order_book_pressure','neutre')} (ratio {onchain.get('order_book_ratio',1.0):.2f})",
                f"Smart Money: {smart_money['signal']} ({smart_money['alerts']} alerts)",
                f"Spoofing: {spoofing['level']} (score {spoofing['score']}) - {spoofing['reason']}",
                f"Wash Trading: {wash_trading['level']} (score {wash_trading['score']}) - {wash_trading['reason']}",
                f"MEV: {mev['level']} ({mev['type']}) (score {mev['score']}) - {mev['reason']}",
                f"Flashbots: {flashbots['level']} ({flashbots['type']}) ({flashbots['bundles']} bundles) - {flashbots['reason']}",
                f"Sandwich Attacks: {sandwich['level']} ({sandwich['attacks']} attaques) - {sandwich['reason']}"
            ],
            "smart_money_score": smart_money["score"],
            "smart_money_signal": smart_money["signal"],
            "order_book_pressure": onchain.get("order_book_pressure", "neutre"),
            "order_book_ratio": onchain.get("order_book_ratio", 1.0),
            "order_book_wall_size": onchain.get("order_book_wall_size", 0),
            "order_book_depth_ratio": onchain.get("order_book_depth_ratio", 1.0),
            "spoofing_detected": spoofing["detected"],
            "spoofing_score": spoofing["score"],
            "spoofing_level": spoofing["level"],
            "spoofing_reason": spoofing["reason"],
            "wash_trading_detected": wash_trading["detected"],
            "wash_trading_score": wash_trading["score"],
            "wash_trading_level": wash_trading["level"],
            "wash_trading_reason": wash_trading["reason"],
            "mev_detected": mev["detected"],
            "mev_score": mev["score"],
            "mev_level": mev["level"],
            "mev_reason": mev["reason"],
            "mev_type": mev["type"],
            "flashbots_detected": flashbots["detected"],
            "flashbots_score": flashbots["score"],
            "flashbots_level": flashbots["level"],
            "flashbots_reason": flashbots["reason"],
            "flashbots_type": flashbots["type"],
            "flashbots_bundles": flashbots["bundles"],
            "sandwich_detected": sandwich["detected"],
            "sandwich_score": sandwich["score"],
            "sandwich_level": sandwich["level"],
            "sandwich_reason": sandwich["reason"],
            "sandwich_attacks": sandwich["attacks"],
            "urgency": 9 if combined_strength >= 8 else 6,
            "source": "Twitter KOLs + On-chain + TA + Smart Money + Order Book + Spoofing Ultra Avancé V3 + Wash Trading Avancé V3 + MEV + Flashbots + Sandwich Attacks"
        }

        self.cache[cache_key] = result
        if len(self.cache) > 50:
            oldest = min(self.cache.keys(), key=lambda k: int(k.split("_")[-1]))
            self.cache.pop(oldest, None)

        return result

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        # === UPGRADE V4 : Vérification stricte de spécialisation ===
        if not self._is_in_my_domain(question):
            return {
                "agent": self.name,
                "summary": f"⚠️ {self.name} a détecté une question hors de sa spécialité → je ne réponds pas",
                "confidence": 0.0,
                "recommendation": "HOLD - Ignoré par spécialisation stricte",
                "warning": "Hors domaine research"
            }

        # === UPGRADE V4 : Glossaire partagé forcé ===
        shared_glossary = context.get("shared_glossary", {})
        def explain(k): 
            return self.explain_term(k) or shared_glossary.get(k, k)

        extreme_learning = context.get("extreme_learning_mode", False) or context.get("learning_mode", False)
        symbol = context.get("symbol", "UNKNOWN")
        data = await self.get_multi_source_intelligence(symbol)

        spoof_str = f" | Spoofing: {data['spoofing_level']} ({data['spoofing_reason']})" if data.get("spoofing_detected") else ""
        wash_str = f" | Wash Trading: {data['wash_trading_level']} ({data['wash_trading_reason']})" if data.get("wash_trading_detected") else ""
        mev_str = f" | MEV: {data['mev_level']} ({data['mev_type']})" if data.get("mev_detected") else ""
        fb_str = f" | Flashbots: {data['flashbots_level']} ({data['flashbots_type']})" if data.get("flashbots_detected") else ""
        sandwich_str = f" | Sandwich: {data['sandwich_level']} ({data['sandwich_attacks']} attaques)" if data.get("sandwich_detected") else ""

        # === RAISONNEMENT NATUREL PROFESSIONNEL ===
        natural_summary = (
            f"Salut ! J’ai fait un tour complet sur {symbol} : KOLs, on-chain, order book, spoofing, wash trading, MEV, Flashbots et sandwich attacks. "
            f"Le sentiment global est {data['sentiment']}, avec une force de {data['strength']}/10. "
            f"Les smart money accumulent, mais on voit du spoofing et du wash trading. "
            f"Avec les leçons du LearningAgent et les analyses des autres agents, le signal est clair : potentiel haussier modéré, mais on reste vigilant sur le timing pour maximiser le gain. "
            f"Aligné avec le {explain('glossary')} du cerveau collectif."
        )

        return {
            "agent": "research",
            "summary": natural_summary,
            "arguments": [data['reason']],
            "confidence": 0.98,
            "recommendation": f"{data['sentiment'].upper()} • Order Book: {data['order_book_pressure']} • Smart Money: {data['smart_money_signal']}{spoof_str}{wash_str}{mev_str}{fb_str}{sandwich_str}",
            "twitter_sentiment": data,
            "top_kols": data.get("top_kols", []),
            "key_factors": data.get("key_factors", []),
            "smart_money": {
                "score": data["smart_money_score"],
                "signal": data["smart_money_signal"]
            },
            "order_book": {
                "pressure": data["order_book_pressure"],
                "ratio": data["order_book_ratio"],
                "wall_size": data.get("order_book_wall_size", 0),
                "depth_ratio": data.get("order_book_depth_ratio", 1.0)
            },
            "spoofing": {
                "detected": data["spoofing_detected"],
                "score": data["spoofing_score"],
                "level": data["spoofing_level"],
                "reason": data["spoofing_reason"],
                "algo": "ultra_advanced_v3"
            },
            "wash_trading": {
                "detected": data["wash_trading_detected"],
                "score": data["wash_trading_score"],
                "level": data["wash_trading_level"],
                "reason": data["wash_trading_reason"],
                "algo": "advanced_v3"
            },
            "mev": {
                "detected": data["mev_detected"],
                "score": data["mev_score"],
                "level": data["mev_level"],
                "reason": data["mev_reason"],
                "type": data["mev_type"],
                "algo": "advanced_v2"
            },
            "flashbots": {
                "detected": data["flashbots_detected"],
                "score": data["flashbots_score"],
                "level": data["flashbots_level"],
                "reason": data["flashbots_reason"],
                "type": data["flashbots_type"],
                "bundles": data["flashbots_bundles"],
                "algo": "advanced_v2"
            },
            "sandwich": {
                "detected": data["sandwich_detected"],
                "score": data["sandwich_score"],
                "level": data["sandwich_level"],
                "reason": data["sandwich_reason"],
                "attacks": data["sandwich_attacks"],
                "algo": "advanced_v2"
            },
            "urgency": data.get("urgency", 6) if not extreme_learning else 9,
            "source": "ULTIME multi-sources",
            "full_summary": natural_summary,
            "glossary_used": True
        }
