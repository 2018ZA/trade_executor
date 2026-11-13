"""Initialize tables and seed default Strategy 1 config."""
import logging
from sqlalchemy import select
from database import db
from models import StrategyConf

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


DEFAULT_CONF = {
    'code': 's1_score_based',
    'name': 'Strategy 1: Score-based (LONG only)',
    'is_active': True,
    'buy_score': 8.0,
    'close_position': -6.0,
    'stop_loss_percent': 0.05,    # 5% below entry
    'profit_percent': 0.10,       # 10% above entry
    'position_size_usd': 100.0,
    'max_open_positions': 10,
    'symbols': None,              # all available
}


def main():
    db.create_tables()
    logger.info("Tables created (or already exist)")

    with db.get_session() as session:
        existing = session.execute(
            select(StrategyConf).where(StrategyConf.code == DEFAULT_CONF['code'])
        ).scalar_one_or_none()

        if existing:
            logger.info(f"Config '{existing.code}' already exists (id={existing.id})")
            logger.info(
                f"  buy_score>{existing.buy_score}, close<{existing.close_position}, "
                f"SL={existing.stop_loss_percent * 100}%, TP={existing.profit_percent * 100}%"
            )
            return

        conf = StrategyConf(**DEFAULT_CONF)
        session.add(conf)
        session.flush()
        logger.info(f"Created config '{conf.code}' (id={conf.id})")


if __name__ == '__main__':
    main()