"""Speed tracking for arbitrage opportunity detection and lifespan."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, func

from archantum.db import Database
from archantum.db.models import ArbitrageTracking, SpeedSummary
from archantum.analysis.arbitrage import ArbitrageOpportunity


class SpeedTracker:
    """Tracks detection speed and lifespan of arbitrage opportunities."""

    def __init__(self, db: Database):
        self.db = db

    async def record_detection(self, opp: ArbitrageOpportunity, detected_at: datetime) -> None:
        """Record that an arbitrage opportunity was detected.

        Creates a tracking row if one doesn't already exist for this market.
        """
        async with self.db.async_session() as session:
            # Check if we're already tracking this market (not yet disappeared)
            result = await session.execute(
                select(ArbitrageTracking)
                .where(ArbitrageTracking.market_id == opp.market_id)
                .where(ArbitrageTracking.disappeared_at == None)
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Update still_available timestamp
                existing.still_available_at = detected_at
                await session.commit()
                return

            # New detection
            tracking = ArbitrageTracking(
                market_id=opp.market_id,
                detected_at=detected_at,
                arbitrage_pct=opp.arbitrage_pct,
                tier=opp.tier.value,
            )
            session.add(tracking)
            await session.commit()

    async def record_alert_sent(self, market_id: str, sent_at: datetime) -> None:
        """Record when an alert was sent for a tracked opportunity."""
        async with self.db.async_session() as session:
            result = await session.execute(
                select(ArbitrageTracking)
                .where(ArbitrageTracking.market_id == market_id)
                .where(ArbitrageTracking.disappeared_at == None)
                .order_by(ArbitrageTracking.detected_at.desc())
                .limit(1)
            )
            tracking = result.scalar_one_or_none()

            if tracking and tracking.alert_sent_at is None:
                tracking.alert_sent_at = sent_at
                delta = (sent_at - tracking.detected_at).total_seconds() * 1000
                tracking.detection_to_alert_ms = delta
                await session.commit()

    async def check_still_available(self, current_market_ids: set[str]) -> None:
        """Mark opportunities that have disappeared.

        Args:
            current_market_ids: Set of market IDs with active arbitrage this poll.
        """
        now = datetime.utcnow()

        async with self.db.async_session() as session:
            # Get all active (non-disappeared) tracking records
            result = await session.execute(
                select(ArbitrageTracking)
                .where(ArbitrageTracking.disappeared_at == None)
            )
            active_records = list(result.scalars().all())

            for record in active_records:
                if record.market_id not in current_market_ids:
                    # Opportunity has disappeared
                    record.disappeared_at = now
                    lifespan = (now - record.detected_at).total_seconds()
                    record.lifespan_seconds = lifespan
                else:
                    # Still available
                    record.still_available_at = now

            await session.commit()

    async def generate_weekly_summary(self) -> SpeedSummary | None:
        """Generate a weekly summary of speed tracking stats.

        Returns the summary if enough data exists, None otherwise.
        """
        now = datetime.utcnow()
        week_start = now - timedelta(days=7)

        async with self.db.async_session() as session:
            # Total detected this week
            total_result = await session.execute(
                select(func.count(ArbitrageTracking.id))
                .where(ArbitrageTracking.detected_at >= week_start)
            )
            total_detected = total_result.scalar_one() or 0

            if total_detected == 0:
                return None

            # Average lifespan (only for disappeared opps)
            avg_lifespan_result = await session.execute(
                select(func.avg(ArbitrageTracking.lifespan_seconds))
                .where(ArbitrageTracking.detected_at >= week_start)
                .where(ArbitrageTracking.lifespan_seconds != None)
            )
            avg_lifespan = avg_lifespan_result.scalar_one()

            # Average detection-to-alert time
            avg_d2a_result = await session.execute(
                select(func.avg(ArbitrageTracking.detection_to_alert_ms))
                .where(ArbitrageTracking.detected_at >= week_start)
                .where(ArbitrageTracking.detection_to_alert_ms != None)
            )
            avg_detection_to_alert = avg_d2a_result.scalar_one()

            # Missed count (disappeared before alert sent)
            missed_result = await session.execute(
                select(func.count(ArbitrageTracking.id))
                .where(ArbitrageTracking.detected_at >= week_start)
                .where(ArbitrageTracking.disappeared_at != None)
                .where(ArbitrageTracking.alert_sent_at == None)
            )
            missed_count = missed_result.scalar_one() or 0

            # Still available rate (had still_available_at set at least once)
            still_available_result = await session.execute(
                select(func.count(ArbitrageTracking.id))
                .where(ArbitrageTracking.detected_at >= week_start)
                .where(ArbitrageTracking.still_available_at != None)
            )
            still_available_count = still_available_result.scalar_one() or 0
            still_available_rate = (still_available_count / total_detected * 100) if total_detected > 0 else None

            # Save summary
            summary = SpeedSummary(
                week_start=week_start,
                week_end=now,
                total_detected=total_detected,
                avg_lifespan_seconds=avg_lifespan,
                avg_detection_to_alert_ms=avg_detection_to_alert,
                missed_count=missed_count,
                still_available_rate=still_available_rate,
            )
            session.add(summary)
            await session.commit()
            await session.refresh(summary)
            return summary
