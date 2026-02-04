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
