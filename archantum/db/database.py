"""Database operations."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from archantum.config import settings
from archantum.db.models import Base, Market, PriceSnapshot, VolumeSnapshot, Alert, Watchlist, Position
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

    # Watchlist operations
    async def add_to_watchlist(self, chat_id: str, market_id: str, notes: str | None = None) -> Watchlist | None:
        """Add a market to user's watchlist."""
        async with self.async_session() as session:
            # Check if already exists
            result = await session.execute(
                select(Watchlist)
                .where(Watchlist.chat_id == chat_id, Watchlist.market_id == market_id)
            )
            existing = result.scalar_one_or_none()
            if existing:
                return None  # Already in watchlist

            watchlist_item = Watchlist(
                chat_id=chat_id,
                market_id=market_id,
                notes=notes,
            )
            session.add(watchlist_item)
            await session.commit()
            await session.refresh(watchlist_item)
            return watchlist_item

    async def remove_from_watchlist(self, chat_id: str, market_id: str) -> bool:
        """Remove a market from user's watchlist."""
        async with self.async_session() as session:
            result = await session.execute(
                select(Watchlist)
                .where(Watchlist.chat_id == chat_id, Watchlist.market_id == market_id)
            )
            item = result.scalar_one_or_none()
            if item:
                await session.delete(item)
                await session.commit()
                return True
            return False

    async def get_watchlist(self, chat_id: str) -> list[Watchlist]:
        """Get user's watchlist."""
        async with self.async_session() as session:
            result = await session.execute(
                select(Watchlist)
                .where(Watchlist.chat_id == chat_id)
                .order_by(Watchlist.added_at.desc())
            )
            return list(result.scalars().all())

    async def is_in_watchlist(self, chat_id: str, market_id: str) -> bool:
        """Check if market is in user's watchlist."""
        async with self.async_session() as session:
            result = await session.execute(
                select(Watchlist)
                .where(Watchlist.chat_id == chat_id, Watchlist.market_id == market_id)
            )
            return result.scalar_one_or_none() is not None

    async def search_markets(self, query: str, limit: int = 10) -> list[Market]:
        """Search markets by question text."""
        async with self.async_session() as session:
            result = await session.execute(
                select(Market)
                .where(Market.question.ilike(f"%{query}%"))
                .where(Market.active == True, Market.closed == False)
                .limit(limit)
            )
            return list(result.scalars().all())

    async def get_top_markets_by_volume(self, limit: int = 10) -> list[tuple[Market, PriceSnapshot | None]]:
        """Get top markets with their latest prices."""
        async with self.async_session() as session:
            # Get markets
            result = await session.execute(
                select(Market)
                .where(Market.active == True, Market.closed == False)
                .limit(limit)
            )
            markets = list(result.scalars().all())

            # Get latest price for each market
            market_prices = []
            for market in markets:
                price_result = await session.execute(
                    select(PriceSnapshot)
                    .where(PriceSnapshot.market_id == market.id)
                    .order_by(PriceSnapshot.timestamp.desc())
                    .limit(1)
                )
                price = price_result.scalar_one_or_none()
                market_prices.append((market, price))

            return market_prices

    async def get_alert_stats(self, chat_id: str | None = None) -> dict[str, int]:
        """Get alert statistics."""
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = datetime.utcnow() - timedelta(days=7)

        async with self.async_session() as session:
            # Today's alerts
            today_result = await session.execute(
                select(func.count(Alert.id))
                .where(Alert.timestamp >= today_start)
            )
            today_count = today_result.scalar_one() or 0

            # This week's alerts
            week_result = await session.execute(
                select(func.count(Alert.id))
                .where(Alert.timestamp >= week_start)
            )
            week_count = week_result.scalar_one() or 0

            # By type today
            type_counts = {}
            for alert_type in ['arbitrage', 'volume_spike', 'price_move']:
                type_result = await session.execute(
                    select(func.count(Alert.id))
                    .where(Alert.timestamp >= today_start)
                    .where(Alert.alert_type == alert_type)
                )
                type_counts[alert_type] = type_result.scalar_one() or 0

            return {
                'today': today_count,
                'this_week': week_count,
                **type_counts
            }

    # Portfolio operations
    async def add_position(
        self,
        chat_id: str,
        market_id: str,
        outcome: str,
        shares: float,
        price: float,
    ) -> Position:
        """Add or update a position."""
        async with self.async_session() as session:
            # Check for existing position
            result = await session.execute(
                select(Position)
                .where(
                    Position.chat_id == chat_id,
                    Position.market_id == market_id,
                    Position.outcome == outcome.lower(),
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Update existing position (average in)
                new_total_shares = existing.shares + shares
                new_total_cost = existing.total_cost + (shares * price)
                existing.shares = new_total_shares
                existing.total_cost = new_total_cost
                existing.avg_price = new_total_cost / new_total_shares
                await session.commit()
                await session.refresh(existing)
                return existing
            else:
                # Create new position
                position = Position(
                    chat_id=chat_id,
                    market_id=market_id,
                    outcome=outcome.lower(),
                    shares=shares,
                    avg_price=price,
                    total_cost=shares * price,
                )
                session.add(position)
                await session.commit()
                await session.refresh(position)
                return position

    async def close_position(
        self,
        chat_id: str,
        market_id: str,
        outcome: str,
        shares: float | None = None,
    ) -> bool:
        """Close (sell) a position partially or fully."""
        async with self.async_session() as session:
            result = await session.execute(
                select(Position)
                .where(
                    Position.chat_id == chat_id,
                    Position.market_id == market_id,
                    Position.outcome == outcome.lower(),
                )
            )
            position = result.scalar_one_or_none()

            if not position:
                return False

            if shares is None or shares >= position.shares:
                # Close entire position
                await session.delete(position)
            else:
                # Partial close
                position.shares -= shares
                position.total_cost = position.shares * position.avg_price

            await session.commit()
            return True

    async def get_positions(self, chat_id: str) -> list[Position]:
        """Get all positions for a user."""
        async with self.async_session() as session:
            result = await session.execute(
                select(Position)
                .where(Position.chat_id == chat_id)
                .order_by(Position.created_at.desc())
            )
            return list(result.scalars().all())

    async def get_position(self, chat_id: str, market_id: str, outcome: str) -> Position | None:
        """Get a specific position."""
        async with self.async_session() as session:
            result = await session.execute(
                select(Position)
                .where(
                    Position.chat_id == chat_id,
                    Position.market_id == market_id,
                    Position.outcome == outcome.lower(),
                )
            )
            return result.scalar_one_or_none()

    async def calculate_portfolio_pnl(self, chat_id: str) -> dict[str, Any]:
        """Calculate portfolio P&L."""
        positions = await self.get_positions(chat_id)

        total_cost = 0.0
        total_value = 0.0
        position_details = []

        for pos in positions:
            # Get current price
            price_snapshot = await self.get_latest_price_snapshot(pos.market_id)
            market = await self.get_market(pos.market_id)

            if price_snapshot and market:
                current_price = price_snapshot.yes_price if pos.outcome == 'yes' else price_snapshot.no_price
                current_price = current_price or 0

                current_value = pos.shares * current_price
                pnl = current_value - pos.total_cost
                pnl_pct = (pnl / pos.total_cost * 100) if pos.total_cost > 0 else 0

                total_cost += pos.total_cost
                total_value += current_value

                position_details.append({
                    'market_id': pos.market_id,
                    'question': market.question,
                    'outcome': pos.outcome,
                    'shares': pos.shares,
                    'avg_price': pos.avg_price,
                    'current_price': current_price,
                    'cost': pos.total_cost,
                    'value': current_value,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                })

        total_pnl = total_value - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

        return {
            'total_cost': total_cost,
            'total_value': total_value,
            'total_pnl': total_pnl,
            'total_pnl_pct': total_pnl_pct,
            'positions': position_details,
        }
