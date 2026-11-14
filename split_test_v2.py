#!/usr/bin/env python3
"""Split-test top-3 configs from grid search across 4 quarters. In-memory, no DB writes."""
import logging
from datetime import datetime
from types import SimpleNamespace
from sqlalchemy import select
from database import db
from models import KlineData, OscillatorData
from strategy import process_bar

logging.basicConfig(level=logging.ERROR)


class Conf:
    def __init__(self, buy_score, close_position, stop_loss_percent, profit_percent, position_size_usd=100.0):
        self.buy_score = buy_score
        self.close_position = close_position
        self.stop_loss_percent = stop_loss_percent
        self.profit_percent = profit_percent
        self.position_size_usd = position_size_usd


def load_data():
    print("Loading all klines...", flush=True)
    data = {}
    with db.get_session() as s:
        rows = s.execute(
            select(OscillatorData, KlineData)
            .join(KlineData, KlineData.id == OscillatorData.kline_id)
            .order_by(OscillatorData.symbol, OscillatorData.kline_id)
        ).all()
        for osc, k in rows:
            data.setdefault(osc.symbol, []).append({
                'score': osc.overall_score,
                'close': k.close_price,
                'low': k.low_price,
                'high': k.high_price,
                'open_time': k.open_time,
            })
    total = sum(len(v) for v in data.values())
    print(f"Loaded {total} bars for {len(data)} symbols\n", flush=True)
    return data


def run_config_period(data, conf, from_dt, to_dt):
    trades = closed = wins = 0
    pnl_pct_sum = 0.0
    pnl_usd_sum = 0.0

    for symbol, bars in data.items():
        filtered = [b for b in bars if from_dt <= b['open_time'] <= to_dt]
        if not filtered:
            continue
        open_trade = None
        for bar in filtered:
            if bar['score'] is None:
                continue
            osc_obj = SimpleNamespace(overall_score=bar['score'])
            kline_obj = SimpleNamespace(
                close_price=bar['close'],
                low_price=bar['low'],
                high_price=bar['high'],
            )
            decision = process_bar(conf, osc_obj, kline_obj, open_trade)

            if decision.action == 'OPEN' and open_trade is None:
                entry = decision.price
                open_trade = SimpleNamespace(
                    entry_price=entry,
                    stop_loss=entry * (1 - conf.stop_loss_percent),
                    take_profit=entry * (1 + conf.profit_percent),
                    quantity=conf.position_size_usd / entry if entry > 0 else 0,
                )
                trades += 1
            elif decision.action == 'CLOSE' and open_trade is not None:
                exit_price = decision.price
                pnl_pct = (exit_price - open_trade.entry_price) / open_trade.entry_price
                pnl_usd = (exit_price - open_trade.entry_price) * open_trade.quantity
                closed += 1
                pnl_pct_sum += pnl_pct
                pnl_usd_sum += pnl_usd
                if pnl_pct > 0:
                    wins += 1
                open_trade = None

    wr = (wins / closed * 100) if closed > 0 else 0.0
    return {
        'trades': trades, 'closed': closed, 'wins': wins, 'wr': wr,
        'pnl_pct': pnl_pct_sum * 100,
        'pnl_usd': pnl_usd_sum,
    }


PERIODS = [
    ('Q4-25 (Nov-Jan)', datetime(2025, 11, 7), datetime(2026, 2, 6, 23, 59, 59)),
    ('Q1-26 (Feb-Apr)', datetime(2026, 2, 7), datetime(2026, 5, 6, 23, 59, 59)),
    ('Q2-26 (May-Jul)', datetime(2026, 5, 7), datetime(2026, 8, 5, 23, 59, 59)),
    ('Q3-26 (Aug-Nov)', datetime(2026, 8, 6), datetime(2026, 11, 13, 23, 59, 59)),
]


CONFIGS = [
    ('H: buy=4 SL=1.5 TP=2.5',           Conf(4.0, -6.0, 0.015, 0.025)),
    ('J: buy=5 SL=1.5 TP=2.5 close=-4',  Conf(5.0, -4.0, 0.015, 0.025)),
    ('E: buy=5 SL=1.5 TP=3',             Conf(5.0, -6.0, 0.015, 0.030)),
    ('BASELINE: buy=6 SL=1 TP=1.5',      Conf(6.0, -6.0, 0.010, 0.015)),
]


def main():
    data = load_data()

    for conf_name, conf in CONFIGS:
        print(f"\n{'=' * 90}")
        print(f"Config: {conf_name}")
        print(f"{'=' * 90}")
        print(f"{'Period':<20} | {'Trades':>6} | {'Closed':>6} | {'WR%':>5} | {'PnL%':>10} | {'PnL$':>10}")
        print("-" * 75)

        total = {'closed': 0, 'wins': 0, 'pnl_pct': 0.0, 'pnl_usd': 0.0}

        for period_name, from_dt, to_dt in PERIODS:
            r = run_period_wrapper(data, conf, from_dt, to_dt)
            total['closed'] += r['closed']
            total['wins'] += r['wins']
            total['pnl_pct'] += r['pnl_pct']
            total['pnl_usd'] += r['pnl_usd']
            print(
                f"{period_name:<20} | {r['trades']:>6} | {r['closed']:>6} | {r['wr']:>5.1f} | "
                f"{r['pnl_pct']:>+10.2f} | {r['pnl_usd']:>+10.2f}",
                flush=True,
            )

        overall_wr = (total['wins'] / total['closed'] * 100) if total['closed'] > 0 else 0
        print("-" * 75)
        print(
            f"{'TOTAL':<20} | {'':>6} | {total['closed']:>6} | {overall_wr:>5.1f} | "
            f"{total['pnl_pct']:>+10.2f} | {total['pnl_usd']:>+10.2f}"
        )

    print("\n=== DONE ===")


def run_period_wrapper(data, conf, from_dt, to_dt):
    return run_config_period(data, conf, from_dt, to_dt)


if __name__ == '__main__':
    main()
