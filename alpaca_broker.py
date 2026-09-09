"""
Isolated Alpaca paper-trading broker module.

Imported by alpaca_stocks_sleeve.py (2026-09-08) for the new US-stocks paper
sleeve -- still standalone/dependency-free from bot.py itself (no import of
bot.py/agents/* here, same discipline as strategy_ledger.py). Run directly to
smoke-test the connection: `python alpaca_broker.py`.

Requires .env (in this directory) with:
  ALPACA_API_KEY=...
  ALPACA_SECRET_KEY=...
  ALPACA_BASE_URL=https://paper-api.alpaca.markets
"""
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

load_dotenv()

ALPACA_API_KEY = os.environ["ALPACA_API_KEY"]
ALPACA_SECRET_KEY = os.environ["ALPACA_SECRET_KEY"]
ALPACA_BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

if "paper-api" not in ALPACA_BASE_URL:
    raise RuntimeError(
        f"ALPACA_BASE_URL does not point to the paper environment: {ALPACA_BASE_URL!r}. "
        "Refusing to proceed — this module is paper-trading only."
    )


def get_trading_client() -> TradingClient:
    # paper=True forces the SDK's own paper endpoint regardless of base URL,
    # belt-and-suspenders on top of the explicit URL check above.
    return TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)


def get_account_balance() -> dict:
    client = get_trading_client()
    account = client.get_account()
    return {
        "cash": float(account.cash),
        "portfolio_value": float(account.portfolio_value),
        "buying_power": float(account.buying_power),
        "status": account.status.value,
    }


def get_quote(symbol: str) -> dict:
    data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
    quote = data_client.get_stock_latest_quote(req)[symbol]
    return {
        "symbol": symbol,
        "bid_price": quote.bid_price,
        "ask_price": quote.ask_price,
        "timestamp": str(quote.timestamp),
    }


def list_positions() -> list:
    client = get_trading_client()
    return [
        {"symbol": p.symbol, "qty": float(p.qty), "market_value": float(p.market_value)}
        for p in client.get_all_positions()
    ]


def is_market_open() -> bool:
    client = get_trading_client()
    return bool(client.get_clock().is_open)


def get_recent_closes(symbol: str, lookback_days: int = 90) -> list:
    """Daily close prices for `symbol`, oldest first, as plain floats.

    Uses an explicit start/end range rather than the SDK's implicit default
    window: a bare request with only `limit` was verified live (2026-09-08)
    to return just 1 bar instead of `limit` bars -- the free-tier IEX/SIP
    feed appears to reject an unbounded recent-data window. A 20-minute
    buffer before "now" as `end` avoids that; explicit `start` gets the
    full lookback. Verified live: 60-day lookback -> 41 daily bars for AAPL.
    """
    data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    end = datetime.now(timezone.utc) - timedelta(minutes=20)
    start = end - timedelta(days=lookback_days)
    req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day, start=start, end=end)
    bars = data_client.get_stock_bars(req)
    # NOTE (2026-09-08): `symbol in bars` is always False on BarSet (verified
    # live) even when the data is there -- go through .data directly instead.
    symbol_bars = bars.data.get(symbol, [])
    return [float(b.close) for b in symbol_bars]


def submit_market_order(symbol: str, side: str, qty: float) -> dict:
    """Submit a paper market order. side is 'buy' or 'sell'. Returns a plain
    dict (id, status, filled_avg_price if already filled) -- never the raw
    SDK object, so callers don't need the alpaca-py types."""
    client = get_trading_client()
    order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
    req = MarketOrderRequest(
        symbol=symbol, qty=qty, side=order_side, time_in_force=TimeInForce.DAY,
    )
    order = client.submit_order(req)
    return {
        "id": str(order.id),
        "symbol": order.symbol,
        "side": order.side.value,
        "qty": float(order.qty) if order.qty is not None else qty,
        "status": order.status.value,
        "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
    }


if __name__ == "__main__":
    print("[alpaca_broker] account balance:", get_account_balance())
    print("[alpaca_broker] AAPL quote:", get_quote("AAPL"))
    print("[alpaca_broker] positions:", list_positions())
    print("[alpaca_broker] market open:", is_market_open())
    print("[alpaca_broker] AAPL closes (last 5):", get_recent_closes("AAPL")[-5:])
