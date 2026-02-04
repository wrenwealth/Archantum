"""Database operations."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from archantum.config import settings
from archantum.db.models import Base, Market, PriceSnapshot, VolumeSnapshot, Alert
from archantum.api.gamma import GammaMarket
from archantum.api.clob import PriceData


class Database:
    """Database operations manager."""

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or settings.database_url
        self.engine = create_async_engine(self.database_url, echo=False)
        self.async_session = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init_db(self):
        """Initialize database tables."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self):
        """Close database connections."""
        await self.engine.dispose()

    async def upsert_market(self, gamma_market: GammaMarket) -> Market:
        """Insert or update a market."""
        async with self.async_session() as session:
            result = await session.execute(
                select(Market).where(Market.id == gamma_market.id)
            )
            market = result.scalar_one_or_none()

            if market:
                market.question = gamma_market.question
                market.slug = gamma_market.slug
                market.condition_id = gamma_market.condition_id
                market.outcome_yes_token = gamma_market.yes_token_id
                market.outcome_no_token = gamma_market.no_token_id
                market.active = gamma_market.active
                market.closed = gamma_market.closed
                market.updated_at = datetime.utcnow()
            else:
                market = Market(
                    id=gamma_market.id,
                    condition_id=gamma_market.condition_id,
                    question=gamma_market.question,
                    slug=gamma_market.slug,
                    event_id=gamma_market.event_slug,
                    outcome_yes_token=gamma_market.yes_token_id,
                    outcome_no_token=gamma_market.no_token_id,
                    active=gamma_market.active,
                    closed=gamma_market.closed,
                )
                session.add(market)

            await session.commit()
            await session.refresh(market)
            return market

    async def upsert_markets(self, gamma_markets: list[GammaMarket]) -> list[Market]:
        """Bulk insert or update markets."""
        markets = []
        for gm in gamma_markets:
            market = await self.upsert_market(gm)
            markets.append(market)
        return markets

    async def get_market(self, market_id: str) -> Market | None:
        """Get a market by ID."""
        async with self.async_session() as session:
            result = await session.execute(
                select(Market).where(Market.id == market_id)
            )
            return result.scalar_one_or_none()

    async def get_active_markets(self) -> list[Market]:
        """Get all active markets."""
        async with self.async_session() as session:
            result = await session.execute(
                select(Market).where(Market.active == True, Market.closed == False)
            )
            return list(result.scalars().all())

    async def save_price_snapshot(self, price_data: PriceData) -> PriceSnapshot:
        """Save a price snapshot."""
        async with self.async_session() as session:
            snapshot = PriceSnapshot(
                market_id=price_data.market_id,
                yes_price=price_data.yes_price,
                no_price=price_data.no_price,
                yes_bid=price_data.yes_bid,
                yes_ask=price_data.yes_ask,
                no_bid=price_data.no_bid,
                no_ask=price_data.no_ask,
                spread=price_data.spread,
            )
            session.add(snapshot)
            await session.commit()
            await session.refresh(snapshot)
            return snapshot

    async def save_volume_snapshot(
        self,
        market_id: str,
        volume_24h: float | None,
        volume_total: float | None,
        liquidity: float | None,
    ) -> VolumeSnapshot:
        """Save a volume snapshot."""
        async with self.async_session() as session:
            snapshot = VolumeSnapshot(
                market_id=market_id,
                volume_24h=volume_24h,
                volume_total=volume_total,
                liquidity=liquidity,
            )
            session.add(snapshot)
            await session.commit()
            await session.refresh(snapshot)
            return snapshot

    async def get_latest_price_snapshot(self, market_id: str) -> PriceSnapshot | None:
        """Get the most recent price snapshot for a market."""
        async with self.async_session() as session:
            result = await session.execute(
                select(PriceSnapshot)
                .where(PriceSnapshot.market_id == market_id)
                .order_by(PriceSnapshot.timestamp.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def get_price_snapshots(
        self,
        market_id: str,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[PriceSnapshot]:
        """Get price snapshots for a market."""
        async with self.async_session() as session:
            query = select(PriceSnapshot).where(PriceSnapshot.market_id == market_id)

            if since:
                query = query.where(PriceSnapshot.timestamp >= since)

            query = query.order_by(PriceSnapshot.timestamp.desc()).limit(limit)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_volume_rolling_average(
        self,
        market_id: str,
        days: int = 7,
    ) -> float | None:
        """Calculate rolling average volume for a market."""
        since = datetime.utcnow() - timedelta(days=days)

        async with self.async_session() as session:
            result = await session.execute(
                select(func.avg(VolumeSnapshot.volume_24h))
                .where(VolumeSnapshot.market_id == market_id)
                .where(VolumeSnapshot.timestamp >= since)
            )
            return result.scalar_one_or_none()

    async def save_alert(
        self,
        market_id: str,
        alert_type: str,
        message: str,
        details: dict[str, Any] | None = None,
        sent: bool = False,
    ) -> Alert:
        """Save an alert."""
        async with self.async_session() as session:
            alert = Alert(
                market_id=market_id,
                alert_type=alert_type,
                message=message,
                details=json.dumps(details) if details else None,
                sent=sent,
            )
            session.add(alert)
            await session.commit()
            await session.refresh(alert)
            return alert

    async def get_recent_alerts(
        self,
        limit: int = 50,
        alert_type: str | None = None,
    ) -> list[Alert]:
        """Get recent alerts."""
        async with self.async_session() as session:
            query = select(Alert)

            if alert_type:
                query = query.where(Alert.alert_type == alert_type)

            query = query.order_by(Alert.timestamp.desc()).limit(limit)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_alerts_today(self) -> list[Alert]:
        """Get alerts from today."""
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        async with self.async_session() as session:
            result = await session.execute(
                select(Alert)
                .where(Alert.timestamp >= today_start)
                .order_by(Alert.timestamp.desc())
            )
            return list(result.scalars().all())

    async def get_market_count(self, active_only: bool = True) -> int:
        """Get count of markets."""
        async with self.async_session() as session:
            query = select(func.count(Market.id))
            if active_only:
                query = query.where(Market.active == True, Market.closed == False)
            result = await session.execute(query)
            return result.scalar_one() or 0
