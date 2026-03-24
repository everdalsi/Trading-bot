from typing import Dict, Any

class PerformanceTracker:
    """
    PerformanceTracker V2 - Mise à jour des résultats de trades + stats globales/symboles
    Compatible avec la structure réelle de sim["trades"] dans bot.py
    """

    def update_trade_results(self, memory: dict, current_price: float) -> dict:
        """Met à jour les trades 'pending' avec win/loss en fonction du prix actuel."""
        try:
            trades = memory.get("sim", {}).get("trades", []) or memory.get("trades", [])

            for trade in trades:
                if trade.get("result") != "pending" and trade.get("pnl") is not None:
                    continue  # déjà clôturé

                entry = trade.get("price_in") or trade.get("entry_price")
                if entry is None:
                    continue

                # Calcul du PnL réel (comme dans bot.py)
                if trade.get("side", "LONG") == "LONG" or trade.get("decision") == "BUY":
                    pnl_pct = (current_price - entry) / entry
                else:
                    pnl_pct = (entry - current_price) / entry

                trade["pnl_pct"] = round(pnl_pct * 100, 2)
                trade["pnl"] = round(pnl_pct * trade.get("amount_usd", 100), 4)

                if pnl_pct > 0:
                    trade["result"] = "win"
                elif pnl_pct < 0:
                    trade["result"] = "loss"
                else:
                    trade["result"] = "neutral"

            return memory

        except Exception as e:
            print(f"[PERFORMANCE TRACKER] Erreur update: {e}")
            return memory

    def log_trade(self, trade_data: dict) -> None:
        """Ajoute un nouveau trade en pending (appelé par l'orchestrator)."""
        try:
            sim = trade_data.get("sim") or {}
            trades = sim.setdefault("trades", [])

            new_trade = {
                "symbol": trade_data.get("symbol"),
                "decision": trade_data.get("decision"),
                "confidence": trade_data.get("confidence", 0.0),
                "result": "pending",
                "price_in": trade_data.get("price_in"),
                "amount_usd": trade_data.get("amount_usd", 100),
                "timestamp": "now"
            }
            trades.append(new_trade)
        except Exception as e:
            print(f"[PERFORMANCE TRACKER] Erreur log_trade: {e}")

    def get_global_stats(self, memory: dict) -> Dict[str, Any]:
        """Statistiques globales sur tous les trades clôturés."""
        try:
            trades = memory.get("sim", {}).get("trades", []) or memory.get("trades", [])
            closed = [t for t in trades if t.get("pnl") is not None]

            wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
            losses = len(closed) - wins
            total = len(closed)

            winrate = round(wins / total * 100, 2) if total > 0 else 0.0

            return {
                "total_trades": total,
                "wins": wins,
                "losses": losses,
                "winrate": winrate
            }
        except Exception:
            return {"total_trades": 0, "wins": 0, "losses": 0, "winrate": 0.0}

    def get_symbol_stats(self, memory: dict, symbol: str) -> Dict[str, Any]:
        """Statistiques pour un symbole spécifique."""
        try:
            trades = memory.get("sim", {}).get("trades", []) or memory.get("trades", [])
            closed = [t for t in trades if t.get("symbol") == symbol and t.get("pnl") is not None]

            wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
            losses = len(closed) - wins
            total = len(closed)

            winrate = round(wins / total * 100, 2) if total > 0 else 0.0

            return {
                "symbol": symbol,
                "wins": wins,
                "losses": losses,
                "winrate": winrate
            }
        except Exception:
            return {"symbol": symbol, "wins": 0, "losses": 0, "winrate": 0.0}
