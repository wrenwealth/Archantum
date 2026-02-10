"""Database operations."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from archantum.config import settings
from archantum.db.models import Base, Market, PriceSnapshot, VolumeSnapshot, Alert, Watchlist, Position, AlertOutcome, SmartWallet, SmartTrade, SystemState
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

    async def get_system_state(self, key: str) -> str | None:
        """Get a system state value by key."""
        async with self.async_session() as session:
            result = await session.execute(
                select(SystemState).where(SystemState.key == key)
            )
            state = result.scalar_one_or_none()
            return state.value if state else None

    async def set_system_state(self, key: str, value: str) -> None:
        """Set a system state value (upsert)."""
        async with self.async_session() as session:
            result = await session.execute(
                select(SystemState).where(SystemState.key == key)
            )
            state = result.scalar_one_or_none()

            if state:
                state.value = value
                state.updated_at = datetime.utcnow()
            else:
                state = SystemState(key=key, value=value)
                session.add(state)

            await session.commit()

    async def close(self):
        """Close database connections."""
        await self.engine.dispose()

    async def upsert_market(self, gamma_market: GammaMarket) -> Market:
        """Insert or update a market."""
        # Extract event slug from events list (this is the correct slug for URLs)
        event_slug = None
        if gamma_market.events and len(gamma_market.events) > 0:
            event_slug = gamma_market.events[0].get("slug")
        if not event_slug:
            event_slug = gamma_market.event_slug

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
                # Always update event_id if we have a better one
                if event_slug:
                    market.event_id = event_slug
            else:
                market = Market(
                    id=gamma_market.id,
                    condition_id=gamma_market.condition_id,
                    question=gamma_market.question,
                    slug=gamma_market.slug,
                    event_id=event_slug,
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

    async def get_market_by_slug(self, slug: str) -> Market | None:
        """Get a market by slug or event_id."""
        async with self.async_session() as session:
            # Try exact slug match first
            result = await session.execute(
                select(Market).where(Market.slug == slug)
            )
            market = result.scalar_one_or_none()
            if market:
                return market

            # Try event_id match
            result = await session.execute(
                select(Market).where(Market.event_id == slug)
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

    # Alert Outcome operations
    async def save_alert_outcome(
        self,
        alert_id: int,
        market_id: str,
        alert_type: str,
        alert_timestamp: datetime,
        signal_price_yes: float | None,
        signal_price_no: float | None,
    ) -> AlertOutcome:
        """Save an alert outcome record for tracking."""
        async with self.async_session() as session:
            outcome = AlertOutcome(
                alert_id=alert_id,
                market_id=market_id,
                alert_type=alert_type,
                alert_timestamp=alert_timestamp,
                signal_price_yes=signal_price_yes,
                signal_price_no=signal_price_no,
            )
            session.add(outcome)
            await session.commit()
            await session.refresh(outcome)
            return outcome

    async def get_pending_alert_outcomes(self, min_age_hours: int = 24) -> list[AlertOutcome]:
        """Get alert outcomes that are old enough to evaluate but not yet evaluated."""
        cutoff = datetime.utcnow() - timedelta(hours=min_age_hours)

        async with self.async_session() as session:
            result = await session.execute(
                select(AlertOutcome)
                .where(AlertOutcome.evaluated_at == None)
                .where(AlertOutcome.alert_timestamp <= cutoff)
            )
            return list(result.scalars().all())

    async def update_alert_outcome(
        self,
        outcome_id: int,
        evaluation_type: str,
        outcome_price_yes: float | None,
        outcome_price_no: float | None,
        profitable: bool,
        profit_pct: float,
    ) -> AlertOutcome | None:
        """Update an alert outcome with evaluation results."""
        async with self.async_session() as session:
            result = await session.execute(
                select(AlertOutcome).where(AlertOutcome.id == outcome_id)
            )
            outcome = result.scalar_one_or_none()

            if outcome:
                outcome.evaluated_at = datetime.utcnow()
                outcome.evaluation_type = evaluation_type
                outcome.outcome_price_yes = outcome_price_yes
                outcome.outcome_price_no = outcome_price_no
                outcome.profitable = profitable
                outcome.profit_pct = profit_pct
                await session.commit()
                await session.refresh(outcome)

            return outcome

    async def get_accuracy_stats(self) -> dict[str, Any]:
        """Get accuracy statistics for all evaluated alerts."""
        async with self.async_session() as session:
            # Overall stats
            total_result = await session.execute(
                select(func.count(AlertOutcome.id))
                .where(AlertOutcome.evaluated_at != None)
            )
            total_evaluated = total_result.scalar_one() or 0

            profitable_result = await session.execute(
                select(func.count(AlertOutcome.id))
                .where(AlertOutcome.evaluated_at != None)
                .where(AlertOutcome.profitable == True)
            )
            total_profitable = profitable_result.scalar_one() or 0

            overall_pct = (total_profitable / total_evaluated * 100) if total_evaluated > 0 else 0

            # Stats by type
            by_type = {}
            alert_types = ['arbitrage', 'volume_spike', 'price_move', 'trend', 'whale']

            for alert_type in alert_types:
                type_total_result = await session.execute(
                    select(func.count(AlertOutcome.id))
                    .where(AlertOutcome.evaluated_at != None)
                    .where(AlertOutcome.alert_type == alert_type)
                )
                type_total = type_total_result.scalar_one() or 0

                type_profitable_result = await session.execute(
                    select(func.count(AlertOutcome.id))
                    .where(AlertOutcome.evaluated_at != None)
                    .where(AlertOutcome.alert_type == alert_type)
                    .where(AlertOutcome.profitable == True)
                )
                type_profitable = type_profitable_result.scalar_one() or 0

                type_pct = (type_profitable / type_total * 100) if type_total > 0 else 0

                by_type[alert_type] = {
                    'total': type_total,
                    'profitable': type_profitable,
                    'accuracy_pct': type_pct,
                }

            # Average profit percentage
            avg_profit_result = await session.execute(
                select(func.avg(AlertOutcome.profit_pct))
                .where(AlertOutcome.evaluated_at != None)
            )
            avg_profit = avg_profit_result.scalar_one() or 0

            return {
                'total_evaluated': total_evaluated,
                'total_profitable': total_profitable,
                'overall_accuracy_pct': overall_pct,
                'avg_profit_pct': avg_profit,
                'by_type': by_type,
            }

    async def get_alert_with_prices(self, alert_id: int) -> tuple[Alert, PriceSnapshot | None] | None:
        """Get an alert and its associated price snapshot."""
        async with self.async_session() as session:
            result = await session.execute(
                select(Alert).where(Alert.id == alert_id)
            )
            alert = result.scalar_one_or_none()

            if not alert:
                return None

            # Get price snapshot closest to alert timestamp
            price_result = await session.execute(
                select(PriceSnapshot)
                .where(PriceSnapshot.market_id == alert.market_id)
                .where(PriceSnapshot.timestamp <= alert.timestamp)
                .order_by(PriceSnapshot.timestamp.desc())
                .limit(1)
            )
            price = price_result.scalar_one_or_none()

            return (alert, price)

    async def get_latest_volume_snapshot(self, market_id: str) -> VolumeSnapshot | None:
        """Get the most recent volume snapshot for a market."""
        async with self.async_session() as session:
            result = await session.execute(
                select(VolumeSnapshot)
                .where(VolumeSnapshot.market_id == market_id)
                .order_by(VolumeSnapshot.timestamp.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def get_markets_by_event_id(self, event_id: str) -> list[Market]:
        """Get all markets for a given event_id (event slug)."""
        async with self.async_session() as session:
            result = await session.execute(
                select(Market).where(Market.event_id == event_id)
            )
            return list(result.scalars().all())

    # Smart Wallet operations
    async def upsert_smart_wallet(
        self,
        wallet_address: str,
        username: str | None = None,
        x_username: str | None = None,
        total_pnl: float = 0.0,
        total_volume: float = 0.0,
        leaderboard_rank: int | None = None,
    ) -> SmartWallet:
        """Insert or update a smart wallet."""
        async with self.async_session() as session:
            result = await session.execute(
                select(SmartWallet).where(SmartWallet.wallet_address == wallet_address)
            )
            wallet = result.scalar_one_or_none()

            if wallet:
                if username:
                    wallet.username = username
                if x_username:
                    wallet.x_username = x_username
                wallet.total_pnl = total_pnl
                wallet.total_volume = total_volume
                if leaderboard_rank:
                    wallet.leaderboard_rank = leaderboard_rank
                wallet.last_updated = datetime.utcnow()
            else:
                wallet = SmartWallet(
                    wallet_address=wallet_address,
                    username=username,
                    x_username=x_username,
                    total_pnl=total_pnl,
                    total_volume=total_volume,
                    leaderboard_rank=leaderboard_rank,
                )
                session.add(wallet)

            await session.commit()
            await session.refresh(wallet)
            return wallet

    async def get_smart_wallet(self, wallet_address: str) -> SmartWallet | None:
        """Get a smart wallet by address."""
        async with self.async_session() as session:
            result = await session.execute(
                select(SmartWallet).where(SmartWallet.wallet_address == wallet_address)
            )
            return result.scalar_one_or_none()

    async def get_tracked_wallets(self, limit: int = 50) -> list[SmartWallet]:
        """Get all actively tracked wallets ordered by PnL."""
        async with self.async_session() as session:
            result = await session.execute(
                select(SmartWallet)
                .where(SmartWallet.is_tracked == True)
                .order_by(SmartWallet.total_pnl.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def save_smart_trade(
        self,
        wallet_id: int,
        transaction_hash: str,
        condition_id: str,
        market_title: str,
        event_slug: str | None,
        side: str,
        outcome: str,
        size: float,
        usdc_size: float,
        price: float,
        timestamp: datetime,
    ) -> SmartTrade | None:
        """Save a smart trade. Returns None if trade already exists."""
        async with self.async_session() as session:
            # Check if trade already exists
            result = await session.execute(
                select(SmartTrade).where(SmartTrade.transaction_hash == transaction_hash)
            )
            existing = result.scalar_one_or_none()
            if existing:
                return None

            trade = SmartTrade(
                wallet_id=wallet_id,
                transaction_hash=transaction_hash,
                condition_id=condition_id,
                market_title=market_title,
                event_slug=event_slug,
                side=side,
                outcome=outcome,
                size=size,
                usdc_size=usdc_size,
                price=price,
                timestamp=timestamp,
            )
            session.add(trade)
            await session.commit()
            await session.refresh(trade)

            # Update wallet last_trade_at
            wallet_result = await session.execute(
                select(SmartWallet).where(SmartWallet.id == wallet_id)
            )
            wallet = wallet_result.scalar_one_or_none()
            if wallet:
                wallet.last_trade_at = timestamp
                wallet.total_trades = (wallet.total_trades or 0) + 1
                await session.commit()

            return trade

    async def get_recent_smart_trades(
        self,
        limit: int = 50,
        min_usdc: float = 100.0,
    ) -> list[tuple[SmartTrade, SmartWallet]]:
        """Get recent trades from smart wallets."""
        async with self.async_session() as session:
            result = await session.execute(
                select(SmartTrade, SmartWallet)
                .join(SmartWallet)
                .where(SmartTrade.usdc_size >= min_usdc)
                .order_by(SmartTrade.timestamp.desc())
                .limit(limit)
            )
            return list(result.all())

    async def get_unsent_smart_trades(self, min_usdc: float = 500.0, max_age_minutes: int = 10) -> list[tuple[SmartTrade, SmartWallet]]:
        """Get trades that haven't been alerted yet.

        Only returns trades from the last max_age_minutes to avoid
        spamming alerts for historical trades when first syncing.
        """
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)

        async with self.async_session() as session:
            result = await session.execute(
                select(SmartTrade, SmartWallet)
                .join(SmartWallet)
                .where(SmartTrade.alert_sent == False)
                .where(SmartTrade.usdc_size >= min_usdc)
                .where(SmartTrade.timestamp >= cutoff)
                .order_by(SmartTrade.timestamp.desc())
            )
            return list(result.all())

    async def mark_trade_alerted(self, trade_id: int) -> None:
        """Mark a trade as alerted."""
        async with self.async_session() as session:
            result = await session.execute(
                select(SmartTrade).where(SmartTrade.id == trade_id)
            )
            trade = result.scalar_one_or_none()
            if trade:
                trade.alert_sent = True
                await session.commit()

    async def get_wallet_trades(
        self,
        wallet_address: str,
        limit: int = 20,
    ) -> list[SmartTrade]:
        """Get recent trades for a specific wallet."""
        async with self.async_session() as session:
            result = await session.execute(
                select(SmartTrade)
                .join(SmartWallet)
                .where(SmartWallet.wallet_address == wallet_address)
                .order_by(SmartTrade.timestamp.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def clear_smart_wallets(self) -> int:
        """Clear all smart wallets and their trades.

        Returns number of wallets deleted.
        """
        async with self.async_session() as session:
            # First delete all trades
            await session.execute(delete(SmartTrade))
            # Then delete all wallets
            result = await session.execute(delete(SmartWallet))
            await session.commit()
            return result.rowcount

    async def has_recent_resolution_alert(
        self,
        market_id: str,
        hours_threshold: int,
        cooldown_hours: int = 12,
    ) -> bool:
        """Check if a resolution alert was sent for this market+threshold recently.

        Args:
            market_id: The market ID
            hours_threshold: The resolution hour threshold (e.g., 48, 24, 6, 1)
            cooldown_hours: Don't alert again within this many hours

        Returns:
            True if alert was recently sent, False otherwise
        """
        cutoff = datetime.utcnow() - timedelta(hours=cooldown_hours)

        async with self.async_session() as session:
            # Check for resolution alerts for this market since cutoff
            # We store the threshold in details JSON
            result = await session.execute(
                select(Alert)
                .where(Alert.market_id == market_id)
                .where(Alert.alert_type == "resolution")
                .where(Alert.timestamp >= cutoff)
            )
            alerts = list(result.scalars().all())

            # Check if any alert has this threshold or a smaller one
            for alert in alerts:
                if alert.details:
                    try:
                        details = json.loads(alert.details)
                        alert_hours = details.get("hours_until_resolution", 999)
                        # If we already alerted for same or closer resolution, skip
                        if alert_hours <= hours_threshold + 1:  # +1 for small timing differences
                            return True
                    except (json.JSONDecodeError, TypeError):
                        pass

            return False
