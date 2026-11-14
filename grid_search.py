#!/usr/bin/env python3
"""Grid search: backtest Strategy 1 with varying config params. In-memory, no DB writes."""
import logging
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
    print("Loading all klines + oscillators into memory...", flush=True)
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
            })
    total = sum(len(v) for v in data.values())
    print(f"Loaded {total} bars for {len(data)} symbols\n", flush=True)
    return data


def run_config(data, conf):
    trades = closed = wins = 0
    pnl_pct_sum = 0.0
    pnl_usd_sum = 0.0
    open_count = 0

    for symbol, bars in data.items():
        open_trade = None
        for bar in bars:
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
                open_count += 1
            elif decision.action == 'CLOSE' and open_trade is not None:
                exit_price = decision.price
                pnl_pct = (exit_price - open_trade.entry_price) / open_trade.entry_price
                pnl_usd = (exit_price - open_trade.entry_price) * open_trade.quantity
                closed += 1
                open_count -= 1
                pnl_pct_sum += pnl_pct
                pnl_usd_sum += pnl_usd
                if pnl_pct > 0:
                    wins += 1
                open_trade = None

    wr = (wins / closed * 100) if closed > 0 else 0.0
    avg_pnl = (pnl_pct_sum / closed * 100) if closed > 0 else 0.0
    return {
        'trades': trades, 'closed': closed, 'open': open_count,
        'wins': wins, 'wr': wr,
        'pnl_pct': pnl_pct_sum * 100,
        'pnl_usd': pnl_usd_sum,
        'avg_pnl': avg_pnl,
    }


def main():
    data = load_data()

    configs = [
        ('BASELINE',         Conf(6.0, -6.0, 0.010, 0.015)),
        ('A: buy=5 TP=2',    Conf(5.0, -6.0, 0.010, 0.020)),
        ('B: buy=5 TP=3',    Conf(5.0, -6.0, 0.010, 0.030)),
        ('C: buy=5 SL=1.5',  Conf(5.0, -6.0, 0.015, 0.015)),
        ('D: buy=5 SL=1.5 TP=2.5', Conf(5.0, -6.0, 0.015, 0.025)),
        ('E: buy=5 SL=1.5 TP=3', Conf(5.0, -6.0, 0.015, 0.030)),
        ('F: buy=4 TP=2',    Conf(4.0, -6.0, 0.010, 0.020)),
        ('G: buy=4 TP=3',    Conf(4.0, -6.0, 0.010, 0.030)),
        ('H: buy=4 SL=1.5 TP=2.5', Conf(4.0, -6.0, 0.015, 0.025)),
        ('I: buy=5 close=-4',Conf(5.0, -4.0, 0.010, 0.015)),
        ('J: buy=5 SL=1.5 TP=2.5 close=-4', Conf(5.0, -4.0, 0.015, 0.025)),
        # повтор базовых для сравнения
        ('buy=5 (single)',   Conf(5.0, -6.0, 0.010, 0.015)),
        ('buy_score=7.0',    Conf(7.0, -6.0, 0.010, 0.015)),
        ('buy_score=8.0',    Conf(8.0, -6.0, 0.010, 0.015)),
        ('SL=0.5%',          Conf(6.0, -6.0, 0.005, 0.015)),
        ('SL=1.5%',          Conf(6.0, -6.0, 0.015, 0.015)),
        ('SL=2.0%',          Conf(6.0, -6.0, 0.020, 0.015)),
        ('TP=1.0%',          Conf(6.0, -6.0, 0.010, 0.010)),
        ('TP=2.0%',          Conf(6.0, -6.0, 0.010, 0.020)),
        ('TP=3.0%',          Conf(6.0, -6.0, 0.010, 0.030)),
        ('close_pos=-4',     Conf(6.0, -4.0, 0.010, 0.015)),
        ('close_pos=-8',     Conf(6.0, -8.0, 0.010, 0.015)),
    ]

    print(f"{'Config':<18} | {'Trades':>6} | {'Closed':>6} | {'WR%':>5} | {'PnL%':>10} | {'PnL$':>10} | {'Avg%':>7}")
    print("-" * 85)
    for name, conf in configs:
        r = run_config(data, conf)
        print(
            f"{name:<18} | {r['trades']:>6} | {r['closed']:>6} | {r['wr']:>5.1f} | "
            f"{r['pnl_pct']:>+10.2f} | {r['pnl_usd']:>+10.2f} | {r['avg_pnl']:>+7.2f}",
            flush=True,
        )
    print("\n=== DONE ===")


if __name__ == '__main__':
    main()
