import ccxt
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class ExecutionEngine:
    def __init__(self, api_key: str = None, api_secret: str = None, testnet: bool = True):
        self.exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'future', 'adjustForTimeDifference': True},
        })
        if testnet:
            self.exchange.set_sandbox_mode(True)
        self.paper_mode = True   # Shadow mode par défaut
        self.trades_history = []

    async def place_order(self, symbol: str, side: str, order_type: str, amount: float, 
                         price: Optional[float] = None, params: Dict = None) -> Dict:
        if params is None:
            params = {}
        try:
            if self.paper_mode:
                slippage = np.random.uniform(0.0005, 0.003)
                executed_price = price * (1 + slippage) if side.lower() == 'buy' else price * (1 - slippage)
                order = {
                    'id': f"paper_{datetime.utcnow().timestamp()}",
                    'symbol': symbol,
                    'side': side,
                    'type': order_type,
                    'amount': amount,
                    'price': executed_price,
                    'status': 'closed',
                    'filled': amount,
                    'fee': {'cost': amount * executed_price * 0.0004}
                }
                self.trades_history.append(order)
                logger.info(f"PAPER ORDER: {side} {amount} {symbol} @ {executed_price}")
                return order
            # Live (CCXT full)
            if order_type == 'limit' and price:
                return await self.exchange.create_limit_order(symbol, side, amount, price, params)
            else:
                return await self.exchange.create_market_order(symbol, side, amount, params)
        except Exception as e:
            logger.error(f"Order failed: {e}")
            raise

    async def close_position(self, symbol: str, side: str, amount: float):
        params = {'reduceOnly': True}
        return await self.place_order(symbol, 'sell' if side == 'long' else 'buy', 'market', amount, params=params)

    def get_historical_data(self, symbol: str, timeframe: str = '5m', limit: int = 2000) -> pd.DataFrame:
        ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df
