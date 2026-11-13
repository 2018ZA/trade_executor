"""Execution engine: create/close trades in DB."""
import logging
from sqlalchemy import select, desc, func
from sqlalchemy.orm import Session
from models import StrategyConf, Trade, KlineData, OscillatorData
from strategy import Decision

logger = logging.getLogger(__name__)


def get_open_trade(session: Session, strategy_id: int, symbol: str) -> Trade | None:
    return session.execute(
        select(Trade)
        .where(Trade.strategy_id == strategy_id)
        .where(Trade.symbol == symbol)
        .where(Trade.status == '1')
        .order_by(desc(Trade.open_kline_id))
        .limit(1)
    ).scalar_one_or_none()


def count_open_trades(session: Session, strategy_id: int) -> int:
    return session.execute(
        select(func.count()).select_from(Trade)
        .where(Trade.strategy_id == strategy_id)
        .where(Trade.status == '1')
    ).scalar_one()


def open_trade_from_decision(
    session: Session,
    conf: StrategyConf,
    symbol: str,
    osc: OscillatorData,
    kline: KlineData,
    decision: Decision,
) -> Trade:
    entry = decision.price
    sl_price = entry * (1.0 - conf.stop_loss_percent)
    tp_price = entry * (1.0 + conf.profit_percent)
    qty = conf.position_size_usd / entry if entry > 0 else 0.0

    trade = Trade(
        strategy_id=conf.id,
        symbol=symbol,
        direction='LONG',
        status='1',
        open_kline_id=kline.id,
        open_oscillator_id=osc.id,
        open_time=kline.open_time,
        entry_price=entry,
        quantity=qty,
        size_usd=conf.position_size_usd,
        stop_loss=sl_price,
        take_profit=tp_price,
        open_score=osc.overall_score,
        signal_snapshot={
            'overall_score': osc.overall_score,
            'overall_signal': osc.overall_signal,
            'buy_score': conf.buy_score,
            'stop_loss_percent': conf.stop_loss_percent,
            'profit_percent': conf.profit_percent,
            'kline_open_time': kline.open_time.isoformat(),
        },
    )
    session.add(trade)
    session.flush()
    logger.info(
        f"OPEN  {symbol:10} entry={entry:.6f} SL={sl_price:.6f} TP={tp_price:.6f} "
        f"qty={qty:.8f} score={osc.overall_score}"
    )
    return trade


def close_trade_from_decision(
    session: Session,
    trade: Trade,
    osc: OscillatorData,
    kline: KlineData,
    decision: Decision,
) -> None:
    exit_price = decision.price
    pnl_pct = (exit_price - trade.entry_price) / trade.entry_price
    pnl_usd = (exit_price - trade.entry_price) * trade.quantity

    trade.status = '0'
    trade.close_kline_id = kline.id
    trade.close_oscillator_id = osc.id
    trade.close_time = kline.open_time
    trade.exit_price = exit_price
    trade.close_reason = decision.reason
    trade.close_score = osc.overall_score
    trade.pnl_pct = pnl_pct
    trade.pnl_usd = pnl_usd
    session.flush()
    logger.info(
        f"CLOSE {trade.symbol:10} reason={decision.reason:12} "
        f"exit={exit_price:.6f} pnl={pnl_pct * 100:+.2f}%"
    )