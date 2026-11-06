"""Market context tags for one coin, logged next to entry decisions (measurement only, for the IC monitor).

Reads Desktop's market_context.json (refreshed every 5 min by market_context.start): retail long % and
crowded side (MarginPad), whale radar direction (CoinLobster). Never raises; {} when there is no data.
"""


def tags(symbol: str) -> dict:
    try:
        import market_context
        ctx = market_context.read()
        if not ctx:
            return {}
        coin = symbol.removesuffix("USDT").removeprefix("1000")
        if coin.startswith("k") and coin[1:].isupper():  # Hyperliquid 1000x tickers (kPEPE -> PEPE)
            coin = coin[1:]
        coin = coin.upper()
        crowd = ctx.get("crowd") or {}
        row = next((c for c in crowd.get("coins") or [] if str(c.get("coin", "")).upper() == coin), None)
        side = "long" if coin in (crowd.get("crowded_long") or []) else "short" if coin in (crowd.get("crowded_short") or []) else None
        radar = next((r for r in (ctx.get("whales") or {}).get("radar") or [] if str(r.get("coin", "")).upper() == coin), None)
        out = {"crowd_long_pct": row.get("long_pct") if row else None, "crowded": side,
               "whale_dir": radar.get("direction") if radar else None,
               "whale_unusual": radar.get("unusual") if radar else None}
        return {k: v for k, v in out.items() if v is not None}
    except Exception:
        return {}
