#!/usr/bin/env python3
"""Split-test: run Strategy 1 on 3 independent time windows. No DB writes."""
import logging
from datetime import datetime
from dataclasses import dataclass
from sqlalchemy import select, distinct
from database import db
from models import StrategyConf, KlineData, OscillatorData
from strategy import process_bar

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger(__name__)


@dataclass
class MockTrade:
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: float


def run_period(conf, from_dt, to_dt):
    with db.get_session() as session:
        symbols = session.execute(select(distinct(OscillatorData.symbol))).scalars().all()
        stats = dict(total=0, closed=0, open=0, wins=0, losses=0, pnl_pct_sum=0.0, pnl_usd_sum=0.0)
        open_positions = {}

        for symbol in symbols:
            stmt = (
                select(OscillatorData, KlineData)
                .join(KlineData, KlineData.id == OscillatorData.kline_id)
                .where(OscillatorData.symbol == symbol)
                .where(KlineData.open_time >= from_dt)
                .where(KlineData.open_time <= to_dt)
                .order_by(OscillatorData.kline_id.asc())
            )
            rows = session.execute(stmt).all()
            open_trade = None

            for osc, kline in rows:
                if osc.overall_score is None:
                    continue

                if open_trade is None:
                    decision = process_bar(conf, osc, kline, None)
                    if decision.action == 'OPEN':
                        if len(open_positions) >= conf.max_open_positions:
                            continue
                        entry = decision.price
                        open_trade = MockTrade(
                            entry_price=entry,
                            stop_loss=entry * (1 - conf.stop_loss_percent),
                            take_profit=entry * (1 + conf.profit_percent),
                            quantity=conf.position_size_usd / entry if entry > 0 else 0,
                        )
                        open_positions[symbol] = open_trade
                        stats['total'] += 1
                else:
                    decision = process_bar(conf, osc, kline, open_trade)
                    if decision.action == 'CLOSE':
                        exit_price = decision.price
                        pnl_pct = (exit_price - open_trade.entry_price) / open_trade.entry_price
                        pnl_usd = (exit_price - open_trade.entry_price) * open_trade.quantity
                        stats['closed'] += 1
                        stats['pnl_pct_sum'] += pnl_pct
                        stats['pnl_usd_sum'] += pnl_usd
                        if pnl_pct > 0:
                            stats['wins'] += 1
                        else:
                            stats['losses'] += 1
                        open_trade = None
                        del open_positions[symbol]

        stats['open'] = len(open_positions)
        stats['wr'] = (stats['wins'] / stats['closed'] * 100) if stats['closed'] > 0 else 0.0
        stats['pnl_pct'] = stats['pnl_pct_sum'] * 100
        return stats


PERIODS = [
    ('Q4-25 Nov-Jan', '2025-11-07', '2026-02-06 23:59:59'),
    ('Q1-26 Feb-Apr', '2026-02-07', '2026-05-06 23:59:59'),
    ('Q2-26 May-Jul', '2026-05-07', '2026-08-05 23:59:59'),
    ('Q3-26 Aug-Nov', '2026-08-06', '2026-11-13 23:59:59'),
]


def main():
    with db.get_session() as session:
        conf = session.execute(
            select(StrategyConf).where(StrategyConf.code == 's1_score_based')
        ).scalar_one_or_none()
        if conf is None:
            print("Config not found"); return

        print(f"=== Config: buy>{conf.buy_score}  SL={conf.stop_loss_percent*100}%  "
              f"TP={conf.profit_percent*100}%  size=${conf.position_size_usd} ===\n")

        header = f"{'Period':18} | {'Trades':>6} | {'Closed':>6} | {'Open':>4} | {'WR%':>5} | {'PnL%':>9} | {'PnL$':>9}"
        print(header)
        print("-" * len(header))

        totals = dict(closed=0, wins=0, pnl_pct=0.0, pnl_usd=0.0)

        for name, from_str, to_str in PERIODS:
            from_dt = datetime.strptime(from_str, '%Y-%m-%d')
            to_dt = datetime.strptime(to_str, '%Y-%m-%d %H:%M:%S')
            r = run_period(conf, from_dt, to_dt)
            totals['closed'] += r['closed']
            totals['wins'] += r['wins']
            totals['pnl_pct'] += r['pnl_pct']
            totals['pnl_usd'] += r['pnl_usd_sum']
            print(f"{name:18} | {r['total']:>6} | {r['closed']:>6} | {r['open']:>4} | "
                  f"{r['wr']:>5.1f} | {r['pnl_pct']:>+9.2f} | {r['pnl_usd_sum']:>+9.2f}")

        print("-" * len(header))
        overall_wr = (totals['wins'] / totals['closed'] * 100) if totals['closed'] > 0 else 0
        print(f"{'TOTAL':18} | {'':6} | {totals['closed']:>6} | {'':4} | "
              f"{overall_wr:>5.1f} | {totals['pnl_pct']:>+9.2f} | {totals['pnl_usd']:>+9.2f}")


if __name__ == '__main__':
    main()
