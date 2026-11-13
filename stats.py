"""Print statistics for Strategy 1 trades."""
import logging
from sqlalchemy import select
from database import db
from models import StrategyConf, Trade

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    with db.get_session() as session:
        conf = session.execute(
            select(StrategyConf).where(StrategyConf.code == 's1_score_based')
        ).scalar_one_or_none()
        if conf is None:
            print("No config 's1_score_based'")
            return

        trades = session.execute(
            select(Trade).where(Trade.strategy_id == conf.id).order_by(Trade.open_time)
        ).scalars().all()

        closed = [t for t in trades if t.status == '0']
        opened = [t for t in trades if t.status == '1']

        print(f"=== Strategy 1 ({conf.code}) ===")
        print(f"Total trades: {len(trades)}  (open={len(opened)}, closed={len(closed)})")

        if not closed:
            print("No closed trades yet")
            return

        wins = sum(1 for t in closed if t.pnl_pct and t.pnl_pct > 0)
        losses = sum(1 for t in closed if t.pnl_pct is not None and t.pnl_pct <= 0)
        total_pct = sum(t.pnl_pct for t in closed if t.pnl_pct is not None) * 100
        total_usd = sum(t.pnl_usd for t in closed if t.pnl_usd is not None)
        avg_pct = total_pct / len(closed)

        reasons = {}
        for t in closed:
            reasons[t.close_reason] = reasons.get(t.close_reason, 0) + 1

        print(f"Wins: {wins}  Losses: {losses}  Win rate: {wins / len(closed) * 100:.1f}%")
        print(f"Total PnL: {total_pct:+.2f}%  |  {total_usd:+.2f} USD")
        print(f"Avg PnL per closed: {avg_pct:+.2f}%")
        print(f"Close reasons: {reasons}")

        print("\nBy symbol:")
        by_sym = {}
        for t in closed:
            d = by_sym.setdefault(t.symbol, {'count': 0, 'win': 0, 'total_pct': 0.0})
            d['count'] += 1
            d['win'] += 1 if t.pnl_pct and t.pnl_pct > 0 else 0
            d['total_pct'] += (t.pnl_pct or 0) * 100
        for sym in sorted(by_sym):
            d = by_sym[sym]
            wr = d['win'] / d['count'] * 100 if d['count'] else 0
            print(f"  {sym:10} trades={d['count']:3}  win_rate={wr:5.1f}%  pnl={d['total_pct']:+7.2f}%")


if __name__ == '__main__':
    main()