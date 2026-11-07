import datetime
import logging

logger = logging.getLogger(__name__)


def get_current_time():
    """Returns the current time."""
    logger.info("Getting current time for user query")
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
