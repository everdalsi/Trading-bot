"""Logging configuration for the trading bot."""

import logging
import logging.handlers
import os
import sys

LOG_LEVEL  = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE   = os.getenv("LOG_FILE", "trading_bot.log")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
DATE_FMT   = "%Y-%m-%d %H:%M:%S"

def _setup_logger():
    root = logging.getLogger()

    # Evite la duplication de handlers
    if root.handlers:
        return root

    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FMT))
    root.addHandler(console_handler)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FMT))
        root.addHandler(file_handler)
    except Exception as e:
        # Peut arriver si les permissions fichier sont refusées en Docker
        root.warning(f"[LOGGING] Impossible d'ouvrir le fichier log {LOG_FILE}: {e}")

    return root

# Instance partagée exportée
logger = _setup_logger()
