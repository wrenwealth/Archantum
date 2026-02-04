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


class MarketScore(Base):
    """Market scoring for ranking and spike detection."""

    __tablename__ = "market_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(String, ForeignKey("markets.id"), nullable=False)

    # Component scores (0-100)
    volume_score = Column(Float, nullable=False)        # 25% weight
    volume_trend_score = Column(Float, nullable=False)  # 15% weight
    liquidity_score = Column(Float, nullable=False)     # 20% weight
    volatility_score = Column(Float, nullable=False)    # 15% weight
    spread_score = Column(Float, nullable=False)        # 15% weight
    activity_score = Column(Float, nullable=False)      # 10% weight

    # Composite
    total_score = Column(Float, nullable=False)
    previous_score = Column(Float, nullable=True)
    score_change = Column(Float, nullable=True)

    timestamp = Column(DateTime, default=datetime.utcnow)

    # Relationship
    market = relationship("Market")
