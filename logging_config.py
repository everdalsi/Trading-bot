import logging
import re
import sys
import os
from logging.handlers import RotatingFileHandler

# === 1. FILTRE DE SÉCURITÉ (ANTI-FUITE) ===
class SensitiveDataFilter(logging.Filter):
    """Masque les clés API et tokens dans les logs."""
    def filter(self, record):
        sensitive_pattern = r'(KEY|SECRET|TOKEN|PASSWORD|API_KEY)=([^&\s\']+)'
        if isinstance(record.msg, str):
            record.msg = re.sub(sensitive_pattern, r'\1=***REDACTED***', record.msg, flags=re.IGNORECASE)
        return True

def setup_logging():
    # Nom unique pour éviter les conflits avec les bibliothèques
    log_name = "trading_bot"
    logger = logging.getLogger(log_name)
    
    # ÉVITE LA DUPLICATION : Si le logger a déjà des handlers, on ne les rajoute pas
    if logger.hasHandlers():
        return logger

    logger.setLevel(logging.INFO)

    # === 2. FORMATTAGE (Précis pour Railway) ===
    # Format : Heure | Niveau | Nom du Module/Agent | Message
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S"
    )

    # === 3. HANDLER CONSOLE (Indispensable pour Railway) ===
    # Utilise sys.stdout pour éviter les délais de buffer
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(SensitiveDataFilter())
    logger.addHandler(console_handler)

    # === 4. HANDLER FICHIER (Pour debug interne) ===
    try:
        file_handler = RotatingFileHandler(
            "bot.log", maxBytes=5*1024*1024, backupCount=2, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(SensitiveDataFilter())
        logger.addHandler(file_handler)
    except Exception as e:
        # Si le fichier ne peut pas être créé (droits d'écriture), on continue en console
        print(f"⚠️ Impossible de créer le fichier log: {e}")

    # === 5. SILENCER LES LIBS TROP BAVARDES ===
    # Empêche CCXT ou HTTPX de polluer tes logs avec chaque requête
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("ccxt").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logger.info("🚀 Système de logging initialisé (Shield ON)")
    return logger

# Initialisation globale
logger = setup_logging()

def get_agent_logger(agent_name):
    """
    Retourne un logger spécifique pour un agent.
    Exemple: get_agent_logger("Trader") -> trading_bot.Trader
    """
    return logging.getLogger(f"trading_bot.{agent_name}")
