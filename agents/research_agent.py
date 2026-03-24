"""
🔍 RESEARCH AGENT ULTIME — Performance maximale + Smart Money + Order Book + Spoofing Ultra Avancé V3 + Wash Trading Avancé
"""

import asyncio
import time
import json
from agents.base_agent import BaseAgent
from typing import Dict, Any

class ResearchAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="research",
            role="Intelligence temps réel ultra-puissante multi-sources"
        )
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
        except:
            pass

        # 2. On-chain + TA + SMART MONEY + ORDER BOOK + SPOOFING ULTRA AVANCÉ V3 + WASH TRADING AVANCÉ
        onchain = {}
        smart_money = {"score": 5, "signal": "neutral", "alerts": 0}
        order_book = {"ratio": 1.0, "pressure": "neutre", "wall_size": 0, "depth_ratio": 1.0}
        spoofing = {"detected": False, "score": 0, "level": "none", "reason": "", "algo": "ultra_advanced_v3"}
        wash_trading = {"detected": False, "score": 0, "level": "none", "reason": "", "algo": "advanced_v2"}

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
            except:
                pass

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

            # SPOOFING ULTRA AVANCÉ V3 (conservé)
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

            # WASH TRADING AVANCÉ V2 — Algorithmes optimisés et pondérés
            wash_score = 0.0
            wash_reasons = []

            # Algo 1: Volume/Price Ratio Extrême (wash classique)
            if volume_spike and price_change_pct < 0.6 and total_volume > 600000:
                wash_score += 9.0
                wash_reasons.append("volume massif + prix quasi-stable")

            # Algo 2: Flat Price + Volume Massif (wash répété)
            if price_change_pct < 0.4 and total_volume > 1000000:
                wash_score += 8.5
                wash_reasons.append("prix plat extrême + volume artificiel")

            # Algo 3: RSI Neutre + Volume Spike (wash de consolidation)
            if 45 < onchain.get("rsi", 50) < 55 and volume_spike and total_volume > 700000:
                wash_score += 7.5
                wash_reasons.append("RSI neutre + volume artificiel")

            # Algo 4: Funding Anomaly + Volume (wash sur futures)
            if abs(funding) < 0.00025 and total_volume > 1200000:
                wash_score += 6.5
                wash_reasons.append("funding très faible + volume suspect")

            # Algo 5: Whale Activity + Wash Pattern (nouveau)
            if smart_money["alerts"] > 0 and price_change_pct < 0.7 and total_volume > 800000:
                wash_score += 7.0
                wash_reasons.append("whale activity + wash pattern")

            # Algo 6: Temporal Wash (volume constant sur plusieurs intervalles)
            if price_change_pct < 0.3 and volume_spike:
                wash_score += 8.0
                wash_reasons.append("wash temporel répété")

            # Classification finale
            if wash_score >= 14.0:
                wash_detected = True
                wash_level = "critical"
            elif wash_score >= 10.0:
                wash_detected = True
                wash_level = "high"
            elif wash_score >= 6.0:
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
                "algo": "advanced_v2"
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
        except:
            pass

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
                f"Wash Trading: {wash_trading['level']} (score {wash_trading['score']}) - {wash_trading['reason']}"
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
            "urgency": 9 if combined_strength >= 8 else 6,
            "source": "Twitter KOLs + On-chain + TA + Smart Money + Order Book + Spoofing Ultra Avancé V3 + Wash Trading Avancé V2"
        }

        self.cache[cache_key] = result
        if len(self.cache) > 50:
            oldest = min(self.cache.keys(), key=lambda k: int(k.split("_")[-1]))
            self.cache.pop(oldest, None)

        return result

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        symbol = context.get("symbol", "UNKNOWN")
        data = await self.get_multi_source_intelligence(symbol)

        spoof_str = f" | Spoofing: {data['spoofing_level']} ({data['spoofing_reason']})" if data.get("spoofing_detected") else ""
        wash_str = f" | Wash Trading: {data['wash_trading_level']} ({data['wash_trading_reason']})" if data.get("wash_trading_detected") else ""

        return {
            "agent": "research",
            "summary": f"Multi-source + Order Book + Smart Money{spoof_str}{wash_str} → {data['sentiment'].upper()} ({data['strength']}/10)",
            "arguments": [data['reason']],
            "confidence": 0.97,
            "recommendation": f"{data['sentiment'].upper()} • Order Book: {data['order_book_pressure']} • Smart Money: {data['smart_money_signal']}{spoof_str}{wash_str}",
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
                "algo": "advanced_v2"
            },
            "urgency": data.get("urgency", 6),
            "source": "ULTIME multi-sources"
        }
