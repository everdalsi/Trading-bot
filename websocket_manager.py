import asyncio
import json
import websocket
import time
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
        self.symbols = symbols
        threading.Thread(target=self._run_forever, daemon=True).start()

    def _run_forever(self):
        while True:
            try:
                ws = websocket.WebSocketApp(
                    self._build_ws_url(),
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open,
                )
                ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                logger.error(f"[WS] Run error: {e}")
            self.connected = False
            logger.info("[WS] Reconnexion dans 10s...")
            time.sleep(10)

    def _build_ws_url(self) -> str:
        streams = []
        for sym in self.symbols:
            streams.append(f"{sym}@kline_1m")
            streams.append(f"{sym}@kline_5m")
        return f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            if "stream" not in data:
                return
            stream = data["stream"]
            kline = data["data"]["k"]
            symbol = kline["s"].upper()
            close = float(kline["c"])
            is_closed = kline["x"]

            with self.lock:
                if "1m" in stream:
                    if symbol not in self.klines_1m:
                        self.klines_1m[symbol] = deque(maxlen=60)
                    if is_closed or not self.klines_1m[symbol]:
                        self.klines_1m[symbol].append(close)
                    elif self.klines_1m[symbol]:
                        self.klines_1m[symbol][-1] = close
                elif "5m" in stream:
                    if symbol not in self.klines_5m:
                        self.klines_5m[symbol] = deque(maxlen=120)
                    if is_closed or not self.klines_5m[symbol]:
                        self.klines_5m[symbol].append(close)
                    elif self.klines_5m[symbol]:
                        self.klines_5m[symbol][-1] = close
        except Exception as e:
            logger.error(f"[WS-MSG] {e}")

    def _on_error(self, ws, error):
        self.connected = False
        logger.error(f"[WS] Erreur: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        self.connected = False
        logger.info(f"[WS] Fermé: {close_status_code}")

    def _on_open(self, ws):
        self.connected = True
        logger.info("[WS] Connecté à Binance WebSocket ✅")

    def get_klines(self, symbol: str, interval: str = "1m"):
        """Récupère les klines en mémoire (utilisé par le reste du bot)"""
        with self.lock:
            if interval == "1m":
                return self.klines_1m.get(symbol.upper(), deque(maxlen=60))
            else:
                return self.klines_5m.get(symbol.upper(), deque(maxlen=120))


# Instance globale utilisée partout dans le bot
ws_manager = WebSocketManager()
