"""Price movement detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from archantum.config import settings
from archantum.db import Database
from archantum.api.clob import PriceData
from archantum.api.gamma import GammaMarket


@dataclass
class PriceMovement:
    """Represents a significant price movement."""

    market_id: str
    question: str
    slug: str | None
    current_yes_price: float
    previous_yes_price: float
    price_change_pct: float
    direction: str  # 'up' or 'down'
    time_span_minutes: int

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "market_id": self.market_id,
            "question": self.question,
            "slug": self.slug,
            "current_yes_price": self.current_yes_price,
            "previous_yes_price": self.previous_yes_price,
            "price_change_pct": self.price_change_pct,
            "direction": self.direction,
            "time_span_minutes": self.time_span_minutes,
        }


class PriceAnalyzer:
    """Analyzes markets for significant price movements."""

    def __init__(
        self,
        db: Database,
        threshold: float | None = None,
        lookback_minutes: int = 60,
    ):
        self.db = db
        self.threshold = threshold or settings.price_move_threshold
        self.lookback_minutes = lookback_minutes

    async def analyze(
        self,
        markets: list[GammaMarket],
        prices: dict[str, PriceData],
    ) -> list[PriceMovement]:
        """Find significant price movements across markets."""
        movements = []

        for market in markets:
            price_data = prices.get(market.id)
            if not price_data:
                continue

            movement = await self.check_market(market, price_data)
            if movement:
                movements.append(movement)

        # Sort by absolute price change (highest first)
        movements.sort(key=lambda x: abs(x.price_change_pct), reverse=True)
        return movements

    async def check_market(
        self,
        market: GammaMarket,
        current_price: PriceData,
    ) -> PriceMovement | None:
        """Check a single market for significant price movement."""
        if current_price.yes_price is None:
            return None

        # Get historical price
        since = datetime.utcnow() - timedelta(minutes=self.lookback_minutes)
        snapshots = await self.db.get_price_snapshots(
            market.id,
            since=since,
            limit=1,
        )

        if not snapshots:
            return None

        old_snapshot = snapshots[-1]  # Oldest in the window
        if old_snapshot.yes_price is None:
            return None

        # Calculate price change
        price_diff = current_price.yes_price - old_snapshot.yes_price
        if old_snapshot.yes_price == 0:
            return None

        price_change_pct = price_diff / old_snapshot.yes_price

        if abs(price_change_pct) < self.threshold:
            return None

        direction = "up" if price_diff > 0 else "down"

        return PriceMovement(
            market_id=market.id,
            question=market.question,
            slug=market.slug,
            current_yes_price=current_price.yes_price,
            previous_yes_price=old_snapshot.yes_price,
            price_change_pct=price_change_pct * 100,
            direction=direction,
            time_span_minutes=self.lookback_minutes,
        )
