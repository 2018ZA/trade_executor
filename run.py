"""Incremental runner: process only new bars since last run."""
import argparse
import logging
from sqlalchemy import select, distinct, func
from database import db
from models import StrategyConf, TEState, KlineData, OscillatorData
from engine import (
    get_open_trade, count_open_trades,
    open_trade_from_decision, close_trade_from_decision,
)
from strategy import process_bar

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def get_state(session, strategy_id, symbol):
    return session.execute(
        select(TEState)
        .where(TEState.strategy_id == strategy_id)
        .where(TEState.symbol == symbol)
    ).scalar_one_or_none()


def init_state_from_latest(session, strategy_id, symbol):
    """For --from-latest: set last_kline_id to max_id - 1 (so only newest bar is processed)."""
    max_id = session.execute(
        select(func.max(OscillatorData.kline_id))
        .where(OscillatorData.symbol == symbol)
    ).scalar_one()
    if max_id is None:
        return None
    state = TEState(strategy_id=strategy_id, symbol=symbol, last_kline_id=max_id - 1)
    session.add(state)
    session.flush()
    return state


def run_symbol(session, conf, symbol, from_latest=False):
    open_trade = get_open_trade(session, conf.id, symbol)
    state = get_state(session, conf.id, symbol)
    if state is None and from_latest:
        state = init_state_from_latest(session, conf.id, symbol)
    last_id = state.last_kline_id if state else 0

    rows = session.execute(
        select(OscillatorData, KlineData)
        .join(KlineData, KlineData.id == OscillatorData.kline_id)
        .where(OscillatorData.symbol == symbol)
        .where(OscillatorData.kline_id > last_id)
        .order_by(OscillatorData.kline_id.asc())
    ).all()

    for osc, kline in rows:
        decision = process_bar(conf, osc, kline, open_trade)

        if decision.action == 'OPEN' and open_trade is None:
            if count_open_trades(session, conf.id) >= conf.max_open_positions:
                logger.info(f"[{symbol:10}] skip open: max positions")
            else:
                open_trade = open_trade_from_decision(session, conf, symbol, osc, kline, decision)
        elif decision.action == 'CLOSE' and open_trade is not None:
            close_trade_from_decision(session, open_trade, osc, kline, decision)
            open_trade = None

        if state is None:
            state = TEState(strategy_id=conf.id, symbol=symbol, last_kline_id=kline.id)
            session.add(state)
        else:
            state.last_kline_id = kline.id

    return len(rows)


def main(from_latest=False):
    with db.get_session() as session:
        conf = session.execute(
            select(StrategyConf).where(StrategyConf.is_active.is_(True)).limit(1)
        ).scalar_one_or_none()
        if conf is None:
            logger.error("No active strategy config found (run init_db.py)")
            return

        symbols = conf.symbols or session.execute(select(distinct(OscillatorData.symbol))).scalars().all()
        for symbol in symbols:
            n = run_symbol(session, conf, symbol, from_latest=from_latest)
            if n > 0:
                logger.info(f"[{symbol:10}] processed {n} new bars")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--from-latest', action='store_true',
                        help='If no state exists, start from the newest bar instead of full history')
    args = parser.parse_args()
    main(from_latest=args.from_latest)