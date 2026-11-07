import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_current_time():
    """Returns the current time."""
    logger.info("Getting current time for user query")
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
