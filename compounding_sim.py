#!/usr/bin/env python3
"""Simulate different position-sizing models on historical trades."""
import logging
from datetime import datetime
from types import SimpleNamespace
from sqlalchemy import select
from database import db
from models import KlineData, OscillatorData
from strategy import process_bar

logging.basicConfig(level=logging.ERROR)


class Conf:
    def __init__(self, buy_score, close_position, stop_loss_percent, profit_percent):
        self.buy_score = buy_score
        self.close_position = close_position
        self.stop_loss_percent = stop_loss_percent
        self.profit_percent = profit_percent


def load_data():
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
    return data


def extract_trades(data, conf):
    """Return list of (open_time, pnl_pct) for each closed trade."""
    trades = []
    for symbol, bars in data.items():
        open_trade = None
        for bar in bars:
            if bar['score'] is None:
                continue
            osc_obj = SimpleNamespace(overall_score=bar['score'])
            kline_obj = SimpleNamespace(close_price=bar['close'], low_price=bar['low'], high_price=bar['high'])
            decision = process_bar(conf, osc_obj, kline_obj, open_trade)

            if decision.action == 'OPEN' and open_trade is None:
                entry = decision.price
                open_trade = SimpleNamespace(
                    entry_price=entry,
                    stop_loss=entry * (1 - conf.stop_loss_percent),
                    take_profit=entry * (1 + conf.profit_percent),
                )
            elif decision.action == 'CLOSE' and open_trade is not None:
                exit_price = decision.price
                pnl_pct = (exit_price - open_trade.entry_price) / open_trade.entry_price
                trades.append((bar['open_time'], pnl_pct))
                open_trade = None
    trades.sort(key=lambda x: x[0])
    return trades


def simulate(trades, initial_bank, model, **kwargs):
    """
    model:
      - 'fixed': size_usd constant (kwargs['size_usd'])
      - 'pct': size_usd = bank * kwargs['fraction']
      - 'kelly': size_usd = bank * kwargs['fraction'] (fraction already fixed)
    Returns: final_bank, max_drawdown, peak_bank, history
    """
    bank = initial_bank
    peak = initial_bank
    max_dd = 0.0
    history = []

    for open_time, pnl_pct in trades:
        if model == 'fixed':
            size = kwargs['size_usd']
        elif model in ('pct', 'kelly'):
            size = bank * kwargs['fraction']
        else:
            raise ValueError(model)

        if size <= 0:
            history.append((open_time, bank, 0, 0))
            continue

        pnl_usd = size * pnl_pct
        bank += pnl_usd

        if bank > peak:
            peak = bank
        dd = (peak - bank) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
        history.append((open_time, bank, size, pnl_usd))

    return bank, max_dd, peak, history


def main():
    print("Loading...", flush=True)
    data = load_data()
    conf = Conf(buy_score=4.0, close_position=-6.0, stop_loss_percent=0.015, profit_percent=0.025)  # H config
    trades = extract_trades(data, conf)
    print(f"Extracted {len(trades)} closed trades\n", flush=True)

    initial = 5000.0

    print(f"{'Model':<35} | {'Final Bank':>12} | {'Net PnL':>12} | {'Return':>10} | {'Max DD':>10}")
    print("-" * 95)

    # A: fixed $100
    final, dd, peak, _ = simulate(trades, initial, 'fixed', size_usd=100.0)
    print(f"{'A: fixed $100':<35} | ${final:>10,.2f} | ${final-initial:>+11,.2f} | {(final/initial-1)*100:>+9.2f}% | {dd*100:>9.2f}%")

    # A2: fixed $200 (2x)
    final, dd, _, _ = simulate(trades, initial, 'fixed', size_usd=200.0)
    print(f"{'A2: fixed $200':<35} | ${final:>10,.2f} | ${final-initial:>+11,.2f} | {(final/initial-1)*100:>+9.2f}% | {dd*100:>9.2f}%")

    # B: fixed 2% of bank (same as $100 at start)
    final, dd, _, _ = simulate(trades, initial, 'pct', fraction=0.02)
    print(f"{'B: 2% of bank (half-Kelly-ish)':<35} | ${final:>10,.2f} | ${final-initial:>+11,.2f} | {(final/initial-1)*100:>+9.2f}% | {dd*100:>9.2f}%")

    # C: 5% of bank
    final, dd, _, _ = simulate(trades, initial, 'pct', fraction=0.05)
    print(f"{'C: 5% of bank':<35} | ${final:>10,.2f} | ${final-initial:>+11,.2f} | {(final/initial-1)*100:>+9.2f}% | {dd*100:>9.2f}%")

    # D: 10% of bank
    final, dd, _, _ = simulate(trades, initial, 'pct', fraction=0.10)
    print(f"{'D: 10% of bank':<35} | ${final:>10,.2f} | ${final-initial:>+11,.2f} | {(final/initial-1)*100:>+9.2f}% | {dd*100:>9.2f}%")

    # E: 20% of bank (aggressive)
    final, dd, _, _ = simulate(trades, initial, 'pct', fraction=0.20)
    print(f"{'E: 20% of bank (aggressive)':<35} | ${final:>10,.2f} | ${final-initial:>+11,.2f} | {(final/initial-1)*100:>+9.2f}% | {dd*100:>9.2f}%")

    # F: Kelly ~15% (half-Kelly at real WR=75%)
    final, dd, _, _ = simulate(trades, initial, 'kelly', fraction=0.15)
    print(f"{'F: 15% (half-Kelly)':<35} | ${final:>10,.2f} | ${final-initial:>+11,.2f} | {(final/initial-1)*100:>+9.2f}% | {dd*100:>9.2f}%")

    print("\nNote: this simulates H-config on 9 months of real data. Actual live WR < 95%.")

    # Реалистичная поправка: умножить все pnl_pct на 0.7 (WR 95% -> ~70%)
    print("\n=== REALISTIC (WR adjusted to ~70%: pnl_pct × 0.7) ===")
    trades_real = [(t, p * 0.7) for t, p in trades]
    print(f"{'Model':<35} | {'Final Bank':>12} | {'Net PnL':>12} | {'Return':>10} | {'Max DD':>10}")
    print("-" * 95)

    final, dd, _, _ = simulate(trades_real, initial, 'fixed', size_usd=100.0)
    print(f"{'A: fixed $100':<35} | ${final:>10,.2f} | ${final-initial:>+11,.2f} | {(final/initial-1)*100:>+9.2f}% | {dd*100:>9.2f}%")

    final, dd, _, _ = simulate(trades_real, initial, 'pct', fraction=0.02)
    print(f"{'B: 2% of bank':<35} | ${final:>10,.2f} | ${final-initial:>+11,.2f} | {(final/initial-1)*100:>+9.2f}% | {dd*100:>9.2f}%")

    final, dd, _, _ = simulate(trades_real, initial, 'pct', fraction=0.05)
    print(f"{'C: 5% of bank':<35} | ${final:>10,.2f} | ${final-initial:>+11,.2f} | {(final/initial-1)*100:>+9.2f}% | {dd*100:>9.2f}%")

    final, dd, _, _ = simulate(trades_real, initial, 'pct', fraction=0.10)
    print(f"{'D: 10% of bank':<35} | ${final:>10,.2f} | ${final-initial:>+11,.2f} | {(final/initial-1)*100:>+9.2f}% | {dd*100:>9.2f}%")

    print("\n=== DONE ===")


if __name__ == '__main__':
    main()
