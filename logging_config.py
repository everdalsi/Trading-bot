import logging
import sys
from datetime import datetime

def setup_logging():
    log_filename = f"bot_logs_{datetime.now().strftime('%Y%m%d_%H%M')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
        handlers=[
            logging.FileHandler(log_filename),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logger = logging.getLogger("trading_bot")
    logger.info("🚀 Logging étendu activé - tous les agents vont logger ici")
    return logger

logger = setup_logging()
