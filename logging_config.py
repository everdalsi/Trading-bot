import logging
import re
from logging.handlers import RotatingFileHandler

# Configuration de base
logger = logging.getLogger("trading_bot")
logger.setLevel(logging.INFO)

# Handler console
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Handler fichier (rotation tous les 5 Mo)
file_handler = RotatingFileHandler(
    "bot.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
)
file_handler.setLevel(logging.INFO)

# Format
formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
console_handler.setFormatter(formatter)
file_handler.setFormatter(formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)

# === PROTECTION ANTI-FUITE DES CLÉS API ===
def sanitize_sensitive(record):
    sensitive_pattern = r'(GROQ_API_KEY|BINANCE_(KEY|SECRET)|TELEGRAM_TOKEN|HF_KEY|GITHUB_TOKEN)=[^&\s]+'
    if isinstance(record.msg, str):
        record.msg = re.sub(sensitive_pattern, r'\1=***REDACTED***', record.msg)
    return True

logger.addFilter(sanitize_sensitive)

logger.info("🚀 Logging étendu activé - tous les agents vont logger ici")
