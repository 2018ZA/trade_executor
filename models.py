from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, DateTime, Boolean, JSON,
    ForeignKey, UniqueConstraint, Index, func,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


# ===== Read-only tables from crypto_allert =====

class KlineData(Base):
    __tablename__ = 'kline_data'
    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), nullable=False)
    open_time = Column(DateTime, nullable=False)
    close_time = Column(DateTime, nullable=False)
    open_price = Column(Float, nullable=False)
    high_price = Column(Float, nullable=False)
    low_price = Column(Float, nullable=False)
    close_price = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)


class OscillatorData(Base):
    __tablename__ = 'oscillator_data'
    id = Column(Integer, primary_key=True)
    kline_id = Column(Integer, nullable=False, index=True)
    symbol = Column(String(20), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    overall_score = Column(Float, nullable=True)
    overall_signal = Column(String(20), nullable=True)


# ===== Own tables =====

class StrategyConf(Base):
    __tablename__ = 'strategy_confs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(32), unique=True, nullable=False)
    name = Column(String(128), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)

    # Strategy 1 parameters (all editable via DB)
    buy_score = Column(Float, nullable=False, default=8.0)          # OPEN when score > this
    close_position = Column(Float, nullable=False, default=-6.0)    # CLOSE when score < this
    stop_loss_percent = Column(Float, nullable=False, default=0.05) # SL = entry * (1 - this)
    profit_percent = Column(Float, nullable=False, default=0.10)    # TP = entry * (1 + this)

    position_size_usd = Column(Float, nullable=False, default=100.0)
    max_open_positions = Column(Integer, nullable=False, default=10)
    symbols = Column(JSON, nullable=True)                           # null = all available

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<StrategyConf {self.code} buy>{self.buy_score} close<{self.close_position}>"


class Trade(Base):
    __tablename__ = 'te_trades'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey('strategy_confs.id'), nullable=False)
    symbol = Column(String(20), nullable=False)
    direction = Column(String(8), nullable=False, default='LONG')
    status = Column(String(8), nullable=False, default='1')  # '1'=open, '0'=closed

    # Open
    open_kline_id = Column(BigInteger, nullable=False)
    open_oscillator_id = Column(BigInteger, nullable=False)
    open_time = Column(DateTime, nullable=False)
    entry_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    size_usd = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    take_profit = Column(Float, nullable=False)
    open_score = Column(Float, nullable=False)

    # Close
    close_kline_id = Column(BigInteger, nullable=True)
    close_oscillator_id = Column(BigInteger, nullable=True)
    close_time = Column(DateTime, nullable=True)
    exit_price = Column(Float, nullable=True)
    close_reason = Column(String(32), nullable=True)  # STOP_LOSS / TAKE_PROFIT / SIGNAL
    close_score = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    pnl_usd = Column(Float, nullable=True)

    signal_snapshot = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('ix_te_trades_strat_sym_status', 'strategy_id', 'symbol', 'status'),
        Index('ix_te_trades_open_time', 'open_time'),
        Index('ix_te_trades_close_time', 'close_time'),
        UniqueConstraint('strategy_id', 'symbol', 'open_kline_id', name='uq_te_trades_open'),
    )


class TEState(Base):
    """Tracks the last processed kline_id per (strategy, symbol)."""
    __tablename__ = 'te_state'

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey('strategy_confs.id'), nullable=False)
    symbol = Column(String(20), nullable=False)
    last_kline_id = Column(BigInteger, nullable=False, default=0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('strategy_id', 'symbol', name='uq_te_state'),
    )