"""SQLAlchemy models for the database."""

from datetime import datetime
from sqlalchemy import (
    Column,
    String,
    Float,
    Integer,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class Market(Base):
    """Markets table."""

    __tablename__ = "markets"

    id = Column(String, primary_key=True)
    condition_id = Column(String, nullable=True)
    question = Column(Text, nullable=False)
    slug = Column(String, nullable=True)
    event_id = Column(String, nullable=True)
    outcome_yes_token = Column(String, nullable=True)
    outcome_no_token = Column(String, nullable=True)
    active = Column(Boolean, default=True)
    closed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    price_snapshots = relationship("PriceSnapshot", back_populates="market")
    volume_snapshots = relationship("VolumeSnapshot", back_populates="market")
    alerts = relationship("Alert", back_populates="market")


class PriceSnapshot(Base):
    """Price snapshots table."""

    __tablename__ = "price_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(String, ForeignKey("markets.id"), nullable=False)
    yes_price = Column(Float, nullable=True)
    no_price = Column(Float, nullable=True)
    yes_bid = Column(Float, nullable=True)
    yes_ask = Column(Float, nullable=True)
    no_bid = Column(Float, nullable=True)
    no_ask = Column(Float, nullable=True)
    spread = Column(Float, nullable=True)
    source = Column(String, nullable=True)  # 'websocket', 'rest', 'cache'
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Relationship
    market = relationship("Market", back_populates="price_snapshots")


class VolumeSnapshot(Base):
    """Volume snapshots table."""

    __tablename__ = "volume_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(String, ForeignKey("markets.id"), nullable=False)
    volume_24h = Column(Float, nullable=True)
    volume_total = Column(Float, nullable=True)
    liquidity = Column(Float, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Relationship
    market = relationship("Market", back_populates="volume_snapshots")


class Alert(Base):
    """Alerts history table."""

    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(String, ForeignKey("markets.id"), nullable=False)
    alert_type = Column(String, nullable=False)  # 'arbitrage', 'volume_spike', 'price_move'
    message = Column(Text, nullable=False)
    details = Column(Text, nullable=True)  # JSON blob
    sent = Column(Boolean, default=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Relationship
    market = relationship("Market", back_populates="alerts")


class Watchlist(Base):
    """User watchlist table."""

    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, nullable=False)  # Telegram chat ID
    market_id = Column(String, ForeignKey("markets.id"), nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text, nullable=True)

    # Relationship
    market = relationship("Market")


class Position(Base):
    """User positions/portfolio table."""

    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, nullable=False)  # Telegram chat ID
    market_id = Column(String, ForeignKey("markets.id"), nullable=False)
    outcome = Column(String, nullable=False)  # 'yes' or 'no'
    shares = Column(Float, nullable=False)  # Number of shares
    avg_price = Column(Float, nullable=False)  # Average entry price
    total_cost = Column(Float, nullable=False)  # Total cost basis
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    market = relationship("Market")


class AlertOutcome(Base):
    """Alert outcome tracking for accuracy measurement."""

    __tablename__ = "alert_outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_id = Column(Integer, ForeignKey("alerts.id"), unique=True, nullable=False)
    market_id = Column(String, ForeignKey("markets.id"), nullable=False)

    # State at alert time
    alert_type = Column(String, nullable=False)
    alert_timestamp = Column(DateTime, nullable=False)
    signal_price_yes = Column(Float, nullable=True)
    signal_price_no = Column(Float, nullable=True)

    # Outcome evaluation (after 24h or market resolution)
    evaluated_at = Column(DateTime, nullable=True)
    evaluation_type = Column(String, nullable=True)  # 'resolution' or '24h_check'
    outcome_price_yes = Column(Float, nullable=True)
    outcome_price_no = Column(Float, nullable=True)

    # Result
    profitable = Column(Boolean, nullable=True)
    profit_pct = Column(Float, nullable=True)

    # Relationships
    alert = relationship("Alert")
    market = relationship("Market")


class SmartWallet(Base):
    """Tracked smart money wallets."""

    __tablename__ = "smart_wallets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String, unique=True, nullable=False)
    username = Column(String, nullable=True)
    x_username = Column(String, nullable=True)

    # Performance stats (updated periodically)
    total_pnl = Column(Float, default=0.0)
    total_volume = Column(Float, default=0.0)
    win_rate = Column(Float, nullable=True)
    total_trades = Column(Integer, default=0)

    # Tracking metadata
    is_tracked = Column(Boolean, default=True)  # Actively tracking
    leaderboard_rank = Column(Integer, nullable=True)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_trade_at = Column(DateTime, nullable=True)

    # Relationships
    trades = relationship("SmartTrade", back_populates="wallet")


class SmartTrade(Base):
    """Trades from tracked smart wallets."""

    __tablename__ = "smart_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_id = Column(Integer, ForeignKey("smart_wallets.id"), nullable=False)
    transaction_hash = Column(String, unique=True, nullable=False)

    # Trade details
    condition_id = Column(String, nullable=False)
    market_title = Column(Text, nullable=False)
    event_slug = Column(String, nullable=True)
    side = Column(String, nullable=False)  # BUY or SELL
    outcome = Column(String, nullable=False)  # Yes or No
    size = Column(Float, nullable=False)  # Token amount
    usdc_size = Column(Float, nullable=False)  # USD value
    price = Column(Float, nullable=False)

    # Timestamps
    timestamp = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Alert tracking
    alert_sent = Column(Boolean, default=False)

    # Relationship
    wallet = relationship("SmartWallet", back_populates="trades")


class DataSourceLog(Base):
    """Logs for data source requests and reliability tracking."""

    __tablename__ = "data_source_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String, nullable=False)  # 'websocket', 'rest', 'cache'
    market_id = Column(String, ForeignKey("markets.id"), nullable=True)
    request_timestamp = Column(DateTime, default=datetime.utcnow)
    response_time_ms = Column(Float, nullable=True)
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)


class TechnicalIndicator(Base):
    """Technical analysis indicators for markets."""

    __tablename__ = "technical_indicators"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(String, ForeignKey("markets.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    # RSI
    rsi_14 = Column(Float, nullable=True)

    # MACD
    macd_line = Column(Float, nullable=True)
    macd_signal = Column(Float, nullable=True)
    macd_histogram = Column(Float, nullable=True)

    # Simple Moving Averages
    sma_10 = Column(Float, nullable=True)
    sma_20 = Column(Float, nullable=True)
    sma_50 = Column(Float, nullable=True)

    # Exponential Moving Averages
    ema_12 = Column(Float, nullable=True)
    ema_26 = Column(Float, nullable=True)

    # Confluence
    confluence_score = Column(Float, nullable=True)
    confluence_signal = Column(String, nullable=True)  # 'strong_buy', 'buy', 'neutral', 'sell', 'strong_sell'


class PriceDiscrepancy(Base):
    """Price discrepancies between data sources."""

    __tablename__ = "price_discrepancies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(String, ForeignKey("markets.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    # WebSocket prices
    websocket_yes = Column(Float, nullable=True)
    websocket_no = Column(Float, nullable=True)

    # REST API prices
    rest_yes = Column(Float, nullable=True)
    rest_no = Column(Float, nullable=True)

    # Analysis
    max_diff_pct = Column(Float, nullable=True)
    is_significant = Column(Boolean, default=False)  # > 2%
    potential_arbitrage = Column(Boolean, default=False)  # > 3%
