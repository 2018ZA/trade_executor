"""Strategy 1: score-based (LONG only)."""
import logging
from dataclasses import dataclass
from typing import Optional
from models import StrategyConf, KlineData, OscillatorData, Trade

logger = logging.getLogger(__name__)


@dataclass
class Decision:
    action: str      # 'OPEN' | 'CLOSE' | 'HOLD'
    reason: str = ''
    price: float = 0.0


def process_bar(
    conf: StrategyConf,
    osc: OscillatorData,
    kline: KlineData,
    open_trade: Optional[Trade],
) -> Decision:
    """
    Evaluate a single closed bar for one symbol.

    Open rule:      osc.overall_score > conf.buy_score  → OPEN at kline.close_price
    Close rules (priority):
      1. Intrabar SL: kline.low_price  <= open_trade.stop_loss   → CLOSE at SL price
      2. Intrabar TP: kline.high_price >= open_trade.take_profit → CLOSE at TP price
      3. Signal:      osc.overall_score < conf.close_position     → CLOSE at kline.close_price

    When both SL and TP are hit in the same bar, SL takes priority (conservative).
    """
    if osc.overall_score is None:
        return Decision(action='HOLD', reason='no_score')

    score = float(osc.overall_score)

    if open_trade is None:
        if score > conf.buy_score:
            return Decision(action='OPEN', reason='buy_score', price=kline.close_price)
        return Decision(action='HOLD')

    # Position open
    if kline.low_price <= open_trade.stop_loss:
        return Decision(action='CLOSE', reason='STOP_LOSS', price=open_trade.stop_loss)

    if kline.high_price >= open_trade.take_profit:
        return Decision(action='CLOSE', reason='TAKE_PROFIT', price=open_trade.take_profit)

    if score < conf.close_position:
        return Decision(action='CLOSE', reason='SIGNAL', price=kline.close_price)

    return Decision(action='HOLD')