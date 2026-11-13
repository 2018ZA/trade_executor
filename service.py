"""Daemon: run incremental processing every SERVICE_INTERVAL_SEC seconds."""
import logging
import time
from config import config
import run as runner

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def main():
    logger.info(f"Trade executor service started (interval={config.SERVICE_INTERVAL_SEC}s)")
    while True:
        try:
            runner.main()
        except Exception as e:
            logger.exception(f"Cycle failed: {e}")
        time.sleep(config.SERVICE_INTERVAL_SEC)


if __name__ == '__main__':
    main()