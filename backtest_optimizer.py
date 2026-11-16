#!/usr/bin/env python3
"""Grid search оптимизатор параметров стратегии S1 (backtest).

Что делает:
  1. Загружает klines + oscillators для символа за период
  2. Делит период на in-sample (70%) и out-of-sample (30%)
  3. Перебирает ~1080 конфигураций на IS
  4. Сортирует по total_pnl_pct
  5. Проверяет топ-3 конфига на OOS
  6. Показывает таблицу + рекомендацию

БЕЗОПАСНОСТЬ:
  - Только чтение из БД. Ничего не пишет.
  - Не отправляет ордера в ArenaGo.
  - Использует тот же process_bar, что live-цикл.
  - Учитывает комиссии (taker + слиппедж).

Использование:
  python backtest_optimizer.py
  python backtest_optimizer.py --symbol BTCUSDT --from 2026-10-01 --to 2026-11-15
  python backtest_optimizer.py --symbol BTCUSDT --from 2026-10-01 --to 2026-11-15 --commission-pct 0.2 --top 20

ВАЖНО:
  Оптимальный по PnL конфиг на одном периоде — почти всегда overfit.
  Реальный edge — конфиг, который выигрывает и на OOS.
  Если OOS-результат < 50% от IS — конфиг НЕ НАДЁЖЕН.
"""
import argparse
import itertools
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select

from database import db
from models import Kline, Oscillator
from strategy import process_bar


# ============ Параметры grid search ============
GRID = {
    'buy_score':          [3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
    'close_position':     [-3.0, -4.0, -5.0, -6.0, -7.0, -8.0],
    'stop_loss_percent':  [0.010, 0.015, 0.020, 0.025, 0.030],
    'profit_percent':     [0.015, 0.020, 0.025, 0.030, 0.040, 0.050],
}


# ============ Загрузка данных ============

def load_bars(symbol: str, from_dt: datetime, to_dt: datetime) -> list:
    """Загрузить (osc, kline) пары для символа и периода, старые первыми."""
    print(f"Загрузка данных: {symbol}  {from_dt.date()} → {to_dt.date()}")
    with db.get_session() as session:
        rows = session.execute(
            select(Oscillator, Kline)
            .join(Kline, Kline.id == Oscillator.kline_id)
            .where(Oscillator.symbol == symbol)
            .where(Kline.open_time >= from_dt)
            .where(Kline.open_time <= to_dt)
            .order_by(Kline.open_time.asc())
        ).all()

    bars = [{
        'score':     osc.overall_score,
        'open_time': kline.open_time,
        'close':     kline.close_price,
        'low':       kline.low_price,
        'high':      kline.high_price,
    } for osc, kline in rows if osc.overall_score is not None]

    if bars:
        print(f"  Загружено: {len(bars)} баров, "
              f"с {bars[0]['open_time']} по {bars[-1]['open_time']}\n")
    return bars


# ============ Прогон одной конфигурации ============

def run_backtest(bars: list, conf: dict, commission_pct: float) -> list:
    """Прогон backtest с заданным conf. Возвращает список закрытых сделок."""
    conf_obj = SimpleNamespace(
        buy_score=conf['buy_score'],
        close_position=conf['close_position'],
        stop_loss_percent=conf['stop_loss_percent'],
        profit_percent=conf['profit_percent'],
    )

    open_trade = None
    closed = []

    for bar in bars:
        osc = SimpleNamespace(overall_score=bar['score'])
        kline = SimpleNamespace(
            close_price=bar['close'],
            low_price=bar['low'],
            high_price=bar['high'],
        )

        trade_view = None
        if open_trade is not None:
            trade_view = SimpleNamespace(
                entry_price=open_trade.entry_price,
                stop_loss=open_trade.stop_loss,
                take_profit=open_trade.take_profit,
            )

        decision = process_bar(conf_obj, osc, kline, trade_view)

        if decision.action == 'OPEN' and open_trade is None:
            entry = decision.price
            open_trade = SimpleNamespace(
                entry_price=entry,
                stop_loss=entry * (1 - conf['stop_loss_percent']),
                take_profit=entry * (1 + conf['profit_percent']),
                open_time=bar['open_time'],
            )

        elif decision.action == 'CLOSE' and open_trade is not None:
            exit_price = decision.price
            gross = (exit_price - open_trade.entry_price) / open_trade.entry_price
            net = gross - commission_pct  # комиссия + слиппедж в долях
            closed.append({
                'gross':      gross,
                'net':        net,
                'reason':     decision.reason,
                'open_time':  open_trade.open_time,
                'close_time': bar['open_time'],
            })
            open_trade = None

    return closed


# ============ Метрики ============

def compute_metrics(closed: list) -> dict:
    n = len(closed)
    if n == 0:
        return {
            'trades': 0, 'win_rate': 0.0, 'total_pnl_pct': 0.0,
            'avg_pnl_pct': 0.0, 'profit_factor': 0.0,
            'max_dd_pct': 0.0, 'final_equity_pct': 0.0,
        }

    total_net = sum(t['net'] for t in closed)
    wins = [t for t in closed if t['net'] > 0]
    losses = [t for t in closed if t['net'] <= 0]

    gross_wins = sum(t['net'] for t in wins)
    gross_losses = abs(sum(t['net'] for t in losses))
    if gross_losses > 1e-9:
        pf = gross_wins / gross_losses
    else:
        pf = 999.0 if gross_wins > 0 else 0.0

    # Equity curve (compounding) для max drawdown
    bank = 1.0
    peak = 1.0
    max_dd = 0.0
    for t in closed:
        bank *= (1.0 + t['net'])
        if bank > peak:
            peak = bank
        dd = (peak - bank) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    return {
        'trades':           n,
        'win_rate':         len(wins) / n,
        'total_pnl_pct':    total_net * 100.0,
        'avg_pnl_pct':      (total_net / n) * 100.0,
        'profit_factor':    pf,
        'max_dd_pct':       max_dd * 100.0,
        'final_equity_pct': (bank - 1.0) * 100.0,
    }


# ============ Walk-forward ============

def walk_forward(
    bars: list,
    commission_pct: float,
    is_ratio: float,
    top_n: int,
) -> list:
    """Grid search на IS + проверка топ-3 на OOS."""
    split = int(len(bars) * is_ratio)
    is_bars = bars[:split]
    oos_bars = bars[split:]

    print(f"Split: IS = {len(is_bars)} баров "
          f"({is_bars[0]['open_time']} → {is_bars[-1]['open_time']})")
    print(f"       OOS = {len(oos_bars)} баров "
          f"({oos_bars[0]['open_time']} → {oos_bars[-1]['open_time']})\n")

    keys = list(GRID.keys())
    combos = list(itertools.product(*[GRID[k] for k in keys]))
    total = len(combos)
    print(f"Grid search: {total} конфигураций...\n")

    results = []
    for idx, combo in enumerate(combos, 1):
        conf = dict(zip(keys, combo))
        trades = run_backtest(is_bars, conf, commission_pct)
        metrics = compute_metrics(trades)
        results.append({**conf, **metrics})
        if idx % 200 == 0 or idx == total:
            print(f"  [{idx}/{total}] обработано", file=sys.stderr)

    results.sort(key=lambda r: r['total_pnl_pct'], reverse=True)
    top_results = results[:top_n]

    # OOS-проверка топ-3
    print("\n" + "=" * 90)
    print("  ПРОВЕРКА НА OUT-OF-SAMPLE (топ-3 конфига)")
    print("=" * 90)
    for i, r in enumerate(top_results[:3], 1):
        oos_trades = run_backtest(oos_bars, r, commission_pct)
        oos_m = compute_metrics(oos_trades)
        oos_pnl = oos_m['total_pnl_pct']
        ratio = oos_pnl / r['total_pnl_pct'] if abs(r['total_pnl_pct']) > 0.01 else 0.0

        if oos_pnl > 0 and ratio >= 0.5:
            verdict = "✅ устойчив"
        elif oos_pnl > 0:
            verdict = "⚠️  слабее"
        else:
            verdict = "❌ overfit"

        print(f"\n#{i}  buy>{r['buy_score']:.1f}  close<{r['close_position']:.1f}  "
              f"SL={r['stop_loss_percent']*100:.1f}%  TP={r['profit_percent']*100:.1f}%")
        print(f"    IS : {r['trades']:3d} сделок, WR {r['win_rate']*100:5.1f}%, "
              f"PnL {r['total_pnl_pct']:+7.2f}%, PF {r['profit_factor']:4.2f}, "
              f"DD {r['max_dd_pct']:5.2f}%")
        print(f"    OOS: {oos_m['trades']:3d} сделок, WR {oos_m['win_rate']*100:5.1f}%, "
              f"PnL {oos_pnl:+7.2f}%")
        print(f"    Вердикт: {verdict}  (OOS/IS = {ratio*100:+.0f}%)")

    return top_results


# ============ Вывод таблицы ============

def print_table(results: list, title: str):
    print("\n" + "=" * 110)
    print(f"  {title}")
    print("=" * 110)
    hdr = (f"{'#':<3} {'buy>':>5} {'close<':>7} {'SL%':>5} {'TP%':>5} "
           f"{'сделок':>7} {'WR%':>6} {'PnL%':>10} {'PF':>5} "
           f"{'DD%':>7} {'Equity%':>10}")
    print(hdr)
    print("-" * len(hdr))
    for i, r in enumerate(results, 1):
        print(f"{i:<3} {r['buy_score']:>5.1f} {r['close_position']:>7.1f} "
              f"{r['stop_loss_percent']*100:>5.1f} {r['profit_percent']*100:>5.1f} "
              f"{r['trades']:>7d} {r['win_rate']*100:>6.1f} "
              f"{r['total_pnl_pct']:>+10.2f} {r['profit_factor']:>5.2f} "
              f"{r['max_dd_pct']:>7.2f} {r['final_equity_pct']:>+10.2f}")


# ============ main ============

def prompt_input(label: str, default: str = None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or (default or "")


def main():
    ap = argparse.ArgumentParser(description="Grid search оптимизатор стратегии S1")
    ap.add_argument('--symbol', help='Тикер, например BTCUSDT')
    ap.add_argument('--from', dest='from_date', help='Начало периода (YYYY-MM-DD)')
    ap.add_argument('--to', dest='to_date', help='Конец периода (YYYY-MM-DD)')
    ap.add_argument('--commission-pct', type=float, default=0.2,
                    help='Комиссия + слиппедж за круг, %% (default 0.2)')
    ap.add_argument('--top', type=int, default=10, help='Сколько строк в таблице (default 10)')
    ap.add_argument('--is-ratio', type=float, default=0.7,
                    help='Доля in-sample (default 0.7)')
    ap.add_argument('--no-oos', action='store_true',
                    help='Не делать OOS-валидацию (только grid search)')
    args = ap.parse_args()

    # --- интерактивный ввод ---
    symbol = (args.symbol or prompt_input("Тикер (например BTCUSDT)")).strip().upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'

    from_str = args.from_date or prompt_input("Дата от (YYYY-MM-DD)")
    to_str = args.to_date or prompt_input("Дата до (YYYY-MM-DD)")

    try:
        from_dt = datetime.strptime(from_str, '%Y-%m-%d')
        to_dt = datetime.strptime(to_str, '%Y-%m-%d')
    except ValueError as e:
        print(f"❌ Неверный формат даты: {e}")
        return 1

    if from_dt >= to_dt:
        print("❌ 'от' должно быть меньше 'до'")
        return 1

    commission_frac = args.commission_pct / 100.0

    print(f"\nПараметры запуска:")
    print(f"  Символ:        {symbol}")
    print(f"  Период:        {from_dt.date()} → {to_dt.date()}")
    print(f"  Комиссия:      {args.commission_pct}% за круг")
    print(f"  Grid:          {len(list(itertools.product(*GRID.values())))} конфигураций")
    print(f"  In-sample:     {args.is_ratio*100:.0f}%")
    print()

    # --- загрузка ---
    bars = load_bars(symbol, from_dt, to_dt)

    if len(bars) < 100:
        print(f"❌ Недостаточно данных: {len(bars)} баров (нужно >= 100).")
        print("   Убедитесь, что crypto-analyzer собрал oscillator_data за этот период.")
        return 1

    # --- walk-forward + grid search ---
    results = walk_forward(bars, commission_frac, args.is_ratio, args.top)

    # --- итоговая таблица ---
    print_table(results, f"ТОП-{args.top} КОНФИГУРАЦИЙ (по PnL% на in-sample)")

    # --- предупреждения ---
    print("\n" + "=" * 110)
    print("  ВАЖНО")
    print("=" * 110)
    print(f"  • Комиссия учтена: {args.commission_pct}% за круг (taker×2 + слиппедж)")
    print(f"  • Funding costs НЕ учтены (при удержании >8h — вычесть отдельно)")
    print(f"  • Конфиг с макс. PnL ≠ робастный. Смотрите OOS-проверку выше.")
    print(f"  • Если все топ-3 OOS < 0 → период был медвежий, стратегия лонговая")
    print(f"  • Для live применяйте конфиг, у которого:")
    print(f"      - OOS PnL > 0")
    print(f"      - OOS/IS >= 50%")
    print(f"      - DD (max) < 20%")
    print(f"      - PF > 1.3")
    print("=" * 110)
    print()

    return 0


if __name__ == '__main__':
    sys.exit(main())
