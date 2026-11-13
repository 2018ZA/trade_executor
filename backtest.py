"""Full historical backtest for Strategy 1 (rewrites te_trades and te_state)."""
import argparse
import logging
from sqlalchemy import select, delete, distinct
from database import db
from models import StrategyConf, Trade, TEState, KlineData, OscillatorData
from engine import (
    get_open_trade, count_open_trades,
    open_trade_from_decision, close_trade_from_decision,
)
from strategy import process_bar

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def load_conf(session, code):
    conf = session.execute(
        select(StrategyConf).where(StrategyConf.code == code)
    ).scalar_one_or_none()
    if conf is None:
        raise RuntimeError(f"Config '{code}' not found. Run init_db.py first.")
    return conf


def get_symbols(session, conf):
    if conf.symbols:
        return list(conf.symbols)
    return session.execute(select(distinct(OscillatorData.symbol))).scalars().all()


def reset_strategy(session, conf):
    session.execute(delete(Trade).where(Trade.strategy_id == conf.id))
    session.execute(delete(TEState).where(TEState.strategy_id == conf.id))
    session.flush()


def run_symbol(session, conf, symbol):
    open_trade = get_open_trade(session, conf.id, symbol)
    state = session.execute(
        select(TEState)
        .where(TEState.strategy_id == conf.id)
        .where(TEState.symbol == symbol)
    ).scalar_one_or_none()

    rows = session.execute(
        select(OscillatorData, KlineData)
        .join(KlineData, KlineData.id == OscillatorData.kline_id)
        .where(OscillatorData.symbol == symbol)
        .order_by(OscillatorData.kline_id.asc())
    ).all()

    for osc, kline in rows:
        decision = process_bar(conf, osc, kline, open_trade)

        if decision.action == 'OPEN' and open_trade is None:
            if count_open_trades(session, conf.id) >= conf.max_open_positions:
                pass
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--code', default='s1_score_based')
    parser.add_argument('--reset', action='store_true', help='Delete trades + state before running')
    args = parser.parse_args()

    with db.get_session() as session:
        conf = load_conf(session, args.code)
        logger.info(
            f"Config: buy>{conf.buy_score}, close<{conf.close_position}, "
            f"SL={conf.stop_loss_percent * 100}%, TP={conf.profit_percent * 100}%, "
            f"size=${conf.position_size_usd}, max_open={conf.max_open_positions}"
        )

        if args.reset:
            reset_strategy(session, conf)
            logger.info("Reset: deleted trades + state")

        symbols = get_symbols(session, conf)
        logger.info(f"Processing {len(symbols)} symbols")

        total_bars = 0
        for symbol in symbols:
            n = run_symbol(session, conf, symbol)
            total_bars += n
            logger.info(f"[{symbol:10}] processed {n} bars")

        # Summary
        trades = session.execute(
            select(Trade).where(Trade.strategy_id == conf.id)
        ).scalars().all()
        open_cnt = sum(1 for t in trades if t.status == '1')
        closed = [t for t in trades if t.status == '0']
        wins = sum(1 for t in closed if t.pnl_pct and t.pnl_pct > 0)
        losses = sum(1 for t in closed if t.pnl_pct is not None and t.pnl_pct <= 0)
        total_pct = sum(t.pnl_pct for t in closed if t.pnl_pct is not None)
        total_usd = sum(t.pnl_usd for t in closed if t.pnl_usd is not None)
        wr = wins / len(closed) * 100 if closed else 0.0

        logger.info("=" * 60)
        logger.info(f"Total bars processed: {total_bars}")
        logger.info(f"Trades: {len(trades)} (open={open_cnt}, closed={len(closed)})")
        logger.info(f"Wins: {wins}, Losses: {losses}, Win rate: {wr:.1f}%")
        logger.info(f"Total PnL: {total_pct * 100:+.2f}%  |  {total_usd:+.2f} USD")
        if closed:
            logger.info(f"Avg PnL per closed: {(total_pct / len(closed)) * 100:+.2f}%")
        logger.info("=" * 60)


if __name__ == '__main__':
    main()