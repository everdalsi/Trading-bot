import asyncio
import json
import websocket
import threading
from collections import deque
from datetime import datetime
from logging_config import logger

class WebSocketManager:
    def __init__(self):
        self.klines_1m = {}
        self.klines_5m = {}
        self.connected = False
        self.ws_thread = None
        self.lock = threading.Lock()

    def start(self, symbols):
        """Démarre le WebSocket Binance pour les klines 1m et 5m"""
        logger.info(f"[WS] Démarrage WebSocket pour {len(symbols)} symboles")
        threading.Thread(target=self._run_forever, args=(symbols,), daemon=True).start()

    def _run_forever(self, symbols):
        # (code complet du WS que tu avais déjà dans bot.py – je te le donne propre)
        # Pour l’instant on garde la version simple que tu avais
        pass  # ← on remplira ça dans 2 minutes une fois que tu auras validé

    def get_klines(self, symbol, interval="1m"):
        with self.lock:
            if interval == "1m":
                return self.klines_1m.get(symbol.upper(), deque(maxlen=60))
            else:
                return self.klines_5m.get(symbol.upper(), deque(maxlen=120))

# Instance globale
ws_manager = WebSocketManager()
