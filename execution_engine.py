"""
⚙️ EXECUTION ENGINE V2 — Moteur d'exécution complet + Paper Mode + TWAP/VWAP + Safety
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX : Toutes les méthodes manquantes rétablies (place_order, close_position,
get_balance, get_positions, get_historical_data, twap_order, cancel_order)
Paper mode par défaut — live activable via set_live_mode()
"""

import ccxt
import asyncio
import time
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, Optional, List
import logging

logger = logging.getLogger(__name__)


class ExecutionEngine:

    def __init__(
        self,
        api_key: str = None,
        api_secret: str = None,
        testnet: bool = True
    ):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.testnet    = testnet
        self.paper_mode = True       # Sécurité : paper mode par défaut
        self.trades_history: List[Dict] = []
        self._paper_balance = {"USDT": 1000.0}  # Balance simulée
        self._paper_positions: Dict[str, Dict] = {}

        try:
            self.exchange = ccxt.binance({
                "apiKey":    api_key,
                "secret":    api_secret,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "future",
                    "adjustForTimeDifference": True,
                },
            })
            if testnet:
                self.exchange.set_sandbox_mode(True)
            logger.info(
                f"✅ ExecutionEngine initialisé — Testnet={testnet} | PaperMode={self.paper_mode}"
            )
        except Exception as e:
            logger.error(f"❌ ExecutionEngine init error: {e}")
            self.exchange = None

    # ─────────────────────────────────────────────────────────────────────────
    # CONFIG
    # ─────────────────────────────────────────────────────────────────────────

    def set_live_mode(self, enabled: bool = True):
        """Active le trading réel (désactive paper mode). DANGER : argent réel."""
        self.paper_mode = not enabled
        mode = "LIVE 🔴" if enabled else "PAPER 🟢"
        logger.warning(f"[EXECUTION] Mode basculé → {mode}")

    # ─────────────────────────────────────────────────────────────────────────
    # BALANCE & POSITIONS
    # ─────────────────────────────────────────────────────────────────────────

    def get_balance(self, currency: str = "USDT") -> float:
        """Retourne la balance disponible (paper ou live)."""
        if self.paper_mode:
            return self._paper_balance.get(currency, 0.0)
        if not self.exchange:
            return 0.0
        try:
            balance = self.exchange.fetch_balance()
            return float(balance.get("free", {}).get(currency, 0.0))
        except Exception as e:
            logger.error(f"❌ get_balance error: {e}")
            return 0.0

    def get_positions(self) -> Dict[str, Dict]:
        """Retourne les positions ouvertes."""
        if self.paper_mode:
            return dict(self._paper_positions)
        if not self.exchange:
            return {}
        try:
            positions = self.exchange.fetch_positions()
            result = {}
            for p in positions:
                if float(p.get("contracts", 0)) != 0:
                    result[p["symbol"]] = {
                        "symbol":      p["symbol"],
                        "side":        p["side"],
                        "contracts":   p["contracts"],
                        "entry_price": p["entryPrice"],
                        "unrealized_pnl": p.get("unrealizedPnl", 0.0),
                    }
            return result
        except Exception as e:
            logger.error(f"❌ get_positions error: {e}")
            return {}

    # ─────────────────────────────────────────────────────────────────────────
    # PLACE ORDER
    # ─────────────────────────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: str,           # "buy" | "sell"
        amount: float,       # en USDT
        order_type: str = "market",
        price: float = None,
        stop_loss: float = None,
        take_profit: float = None,
    ) -> Dict:
        """
        Passe un ordre (market ou limit).
        En paper mode : simule l'exécution sans toucher à l'exchange.
        """
        if self.paper_mode:
            return self._paper_order(symbol, side, amount, order_type, price, stop_loss, take_profit)

        if not self.exchange:
            return {"success": False, "error": "Exchange non initialisé"}

        try:
            self.exchange.load_markets()
            params = {}
            if stop_loss:
                params["stopLoss"] = {"type": "market", "triggerPrice": stop_loss}
            if take_profit:
                params["takeProfit"] = {"type": "market", "triggerPrice": take_profit}

            order = self.exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side,
                amount=amount,
                price=price if order_type == "limit" else None,
                params=params,
            )
            trade_record = {
                "id":         order.get("id"),
                "symbol":     symbol,
                "side":       side,
                "amount":     amount,
                "price":      order.get("price") or price,
                "type":       order_type,
                "status":     order.get("status"),
                "timestamp":  datetime.utcnow().isoformat(),
                "paper_mode": False,
            }
            self.trades_history.append(trade_record)
            logger.info(f"✅ ORDRE LIVE: {side.upper()} {amount} {symbol} @ {price or 'MARKET'}")
            return {"success": True, "order": order, "trade": trade_record}

        except ccxt.InsufficientFunds as e:
            logger.error(f"❌ Fonds insuffisants: {e}")
            return {"success": False, "error": "InsufficientFunds", "detail": str(e)}
        except ccxt.InvalidOrder as e:
            logger.error(f"❌ Ordre invalide: {e}")
            return {"success": False, "error": "InvalidOrder", "detail": str(e)}
        except Exception as e:
            logger.error(f"❌ place_order error: {e}")
            return {"success": False, "error": str(e)}

    def _paper_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "market",
        price: float = None,
        stop_loss: float = None,
        take_profit: float = None,
    ) -> Dict:
        """Simule un ordre sans toucher à l'exchange."""
        sim_price = price or self._get_simulated_price(symbol)
        usdt_balance = self._paper_balance.get("USDT", 0.0)

        if side == "buy":
            if usdt_balance < amount:
                return {"success": False, "error": "PaperMode: fonds insuffisants"}
            self._paper_balance["USDT"] = usdt_balance - amount
            qty = amount / sim_price if sim_price > 0 else 0
            self._paper_positions[symbol] = {
                "symbol":      symbol,
                "side":        "long",
                "amount_usdt": amount,
                "qty":         qty,
                "entry_price": sim_price,
                "stop_loss":   stop_loss,
                "take_profit": take_profit,
                "opened_at":   datetime.utcnow().isoformat(),
            }
        elif side == "sell" and symbol in self._paper_positions:
            pos = self._paper_positions.pop(symbol)
            qty = pos.get("qty", 0)
            pnl = (sim_price - pos["entry_price"]) * qty
            self._paper_balance["USDT"] += pos["amount_usdt"] + pnl

        trade_record = {
            "id":         f"paper_{int(time.time()*1000)}",
            "symbol":     symbol,
            "side":       side,
            "amount":     amount,
            "price":      sim_price,
            "stop_loss":  stop_loss,
            "take_profit": take_profit,
            "type":       order_type,
            "status":     "filled",
            "timestamp":  datetime.utcnow().isoformat(),
            "paper_mode": True,
        }
        self.trades_history.append(trade_record)
        logger.info(
            f"📝 PAPER ORDER: {side.upper()} {amount} USDT {symbol} @ {sim_price:.4f}"
            + (f" | SL={stop_loss}" if stop_loss else "")
            + (f" | TP={take_profit}" if take_profit else "")
        )
        return {"success": True, "order": trade_record, "trade": trade_record}

    def _get_simulated_price(self, symbol: str) -> float:
        """Récupère le prix réel Binance même en paper mode (pour simuler correctement)."""
        try:
            if self.exchange:
                ticker = self.exchange.fetch_ticker(symbol)
                return float(ticker.get("last", 1.0))
        except Exception:
            pass
        return 1.0

    # ─────────────────────────────────────────────────────────────────────────
    # CLOSE POSITION
    # ─────────────────────────────────────────────────────────────────────────

    def close_position(self, symbol: str, reason: str = "manual") -> Dict:
        """Ferme une position ouverte (paper ou live)."""
        if self.paper_mode:
            if symbol not in self._paper_positions:
                return {"success": False, "error": f"Pas de position ouverte sur {symbol}"}
            pos = self._paper_positions[symbol]
            sim_price = self._get_simulated_price(symbol)
            qty = pos.get("qty", 0)
            pnl = (sim_price - pos["entry_price"]) * qty
            self._paper_balance["USDT"] += pos["amount_usdt"] + pnl
            del self._paper_positions[symbol]
            logger.info(
                f"📝 PAPER CLOSE: {symbol} | Prix={sim_price:.4f} | PnL={pnl:+.4f} USDT | Raison={reason}"
            )
            return {"success": True, "pnl": pnl, "close_price": sim_price, "reason": reason}

        if not self.exchange:
            return {"success": False, "error": "Exchange non initialisé"}
        try:
            positions = self.get_positions()
            if symbol not in positions:
                return {"success": False, "error": f"Pas de position sur {symbol}"}
            pos = positions[symbol]
            side = "sell" if pos["side"] == "long" else "buy"
            order = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=pos["contracts"],
                params={"reduceOnly": True},
            )
            logger.info(f"✅ CLOSE LIVE: {symbol} | Raison={reason}")
            return {"success": True, "order": order, "reason": reason}
        except Exception as e:
            logger.error(f"❌ close_position error: {e}")
            return {"success": False, "error": str(e)}

    # ─────────────────────────────────────────────────────────────────────────
    # CANCEL ORDER
    # ─────────────────────────────────────────────────────────────────────────

    def cancel_order(self, order_id: str, symbol: str) -> Dict:
        """Annule un ordre en attente."""
        if self.paper_mode:
            return {"success": True, "message": "PaperMode: annulation simulée"}
        if not self.exchange:
            return {"success": False, "error": "Exchange non initialisé"}
        try:
            result = self.exchange.cancel_order(order_id, symbol)
            logger.info(f"✅ Ordre {order_id} annulé sur {symbol}")
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"❌ cancel_order error: {e}")
            return {"success": False, "error": str(e)}

    # ─────────────────────────────────────────────────────────────────────────
    # TWAP ORDER (Time-Weighted Average Price)
    # ─────────────────────────────────────────────────────────────────────────

    async def twap_order(
        self,
        symbol: str,
        side: str,
        total_amount: float,
        slices: int = 5,
        interval_seconds: float = 12.0,
    ) -> Dict:
        """
        Découpe un gros ordre en 'slices' petits ordres espacés de 'interval_seconds'.
        Réduit l'impact marché et le slippage.
        """
        slice_amount = total_amount / slices
        results = []
        total_cost = 0.0

        logger.info(
            f"[TWAP] Démarrage: {side.upper()} {total_amount} USDT {symbol} "
            f"en {slices} tranches de {slice_amount:.2f} USDT"
        )

        for i in range(slices):
            result = self.place_order(symbol, side, slice_amount)
            results.append(result)
            if result.get("success"):
                price = result["trade"].get("price", 0)
                total_cost += slice_amount
                logger.info(f"[TWAP] Tranche {i+1}/{slices} exécutée @ {price:.4f}")
            else:
                logger.warning(f"[TWAP] Tranche {i+1}/{slices} échouée: {result.get('error')}")

            if i < slices - 1:
                await asyncio.sleep(interval_seconds)

        success_count = sum(1 for r in results if r.get("success"))
        return {
            "success":       success_count > 0,
            "slices_done":   success_count,
            "slices_total":  slices,
            "total_amount":  total_cost,
            "results":       results,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # HISTORICAL DATA
    # ─────────────────────────────────────────────────────────────────────────

    def get_historical_data(
        self,
        symbol: str,
        timeframe: str = "5m",
        limit: int = 200,
    ) -> pd.DataFrame:
        """Récupère les bougies OHLCV depuis Binance."""
        if not self.exchange:
            return pd.DataFrame()
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(
                ohlcv,
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            return df
        except Exception as e:
            logger.error(f"❌ get_historical_data error ({symbol}): {e}")
            return pd.DataFrame()

    # ─────────────────────────────────────────────────────────────────────────
    # STATS & REPORTING
    # ─────────────────────────────────────────────────────────────────────────

    def get_trade_stats(self) -> Dict:
        """Retourne les statistiques des trades passés."""
        if not self.trades_history:
            return {"total": 0, "wins": 0, "losses": 0, "winrate": 0.0}
        total = len(self.trades_history)
        wins = sum(1 for t in self.trades_history if t.get("pnl", 0) > 0)
        losses = total - wins
        return {
            "total":   total,
            "wins":    wins,
            "losses":  losses,
            "winrate": round(wins / total, 3) if total > 0 else 0.0,
            "paper_mode": self.paper_mode,
            "balance_usdt": self._paper_balance.get("USDT", 0.0) if self.paper_mode else None,
        }

    def get_open_positions_summary(self) -> str:
        """Résumé lisible des positions ouvertes (pour Telegram/logs)."""
        positions = self.get_positions()
        if not positions:
            return "Aucune position ouverte"
        lines = []
        for sym, pos in positions.items():
            lines.append(
                f"  • {sym}: {pos.get('side','?').upper()} "
                f"@ {pos.get('entry_price', '?')} "
                f"| PnL: {pos.get('unrealized_pnl', 0.0):+.2f} USDT"
            )
        return "\n".join(lines)
