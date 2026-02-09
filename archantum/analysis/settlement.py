"""Settlement lag detection — markets near resolution with incomplete price convergence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, func

from archantum.api.clob import PriceData
from archantum.api.gamma import GammaMarket
from archantum.config import settings


@dataclass
class SettlementLagOpportunity:
    """A market where the outcome appears decided but price hasn't converged."""

    market_id: str
    question: str
    polymarket_url: str | None
    current_yes_price: float
    price_1h_ago: float | None
    price_change_pct: float  # movement in last hour
    direction: str  # "converging_yes" or "converging_no"
    expected_settlement: float  # 1.0 or 0.0
    potential_profit_cents: float
    volume_24hr: float | None

    def to_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "question": self.question,
            "polymarket_url": self.polymarket_url,
            "current_yes_price": self.current_yes_price,
            "price_1h_ago": self.price_1h_ago,
            "price_change_pct": self.price_change_pct,
            "direction": self.direction,
            "expected_settlement": self.expected_settlement,
            "potential_profit_cents": self.potential_profit_cents,
            "volume_24hr": self.volume_24hr,
        }


class SettlementLagDetector:
    """Detects markets where outcome appears decided but price hasn't fully converged."""

    def __init__(self, db):
        self.db = db
        self.extreme_threshold = settings.settlement_extreme_threshold
        self.min_movement_pct = settings.settlement_min_movement_pct
        self.min_profit_cents = settings.settlement_min_profit_cents
        self.max_days_to_resolution = settings.settlement_max_days_to_resolution

    async def analyze(
        self,
        markets: list[GammaMarket],
        prices: dict[str, PriceData],
    ) -> list[SettlementLagOpportunity]:
        """Find settlement lag opportunities."""
        from archantum.db.models import PriceSnapshot

        opportunities = []
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)

        now = datetime.utcnow()
        max_resolution = now + timedelta(days=self.max_days_to_resolution)

        for market in markets:
            price_data = prices.get(market.id)
            if not price_data or price_data.yes_price is None:
                continue

            # Skip markets without a resolution date or resolving too far out
            if not market.end_date:
                continue
            try:
                end_dt = datetime.fromisoformat(market.end_date.replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, AttributeError):
                continue
            if end_dt > max_resolution:
                continue

            yes_price = price_data.yes_price

            # Check if price is extreme (near 0 or 1)
            is_high = yes_price >= self.extreme_threshold
            is_low = yes_price <= (1.0 - self.extreme_threshold)
            if not is_high and not is_low:
                continue

            # Query price from ~1 hour ago
            try:
                async with self.db.async_session() as session:
                    result = await session.execute(
                        select(PriceSnapshot.yes_price)
                        .where(PriceSnapshot.market_id == market.id)
                        .where(PriceSnapshot.timestamp <= one_hour_ago)
                        .order_by(PriceSnapshot.timestamp.desc())
                        .limit(1)
                    )
                    row = result.scalar()
                    price_1h_ago = float(row) if row is not None else None
            except Exception:
                price_1h_ago = None

            # Calculate movement
            if price_1h_ago is not None and price_1h_ago > 0:
                change_pct = abs(yes_price - price_1h_ago) / price_1h_ago * 100
            else:
                change_pct = 0.0

            # Only alert if there's been meaningful recent movement toward the extreme
            if change_pct < self.min_movement_pct:
                continue

            if is_high:
                direction = "converging_yes"
                expected = 1.0
                profit_cents = (1.0 - yes_price) * 100
            else:
                direction = "converging_no"
                expected = 0.0
                profit_cents = yes_price * 100  # Buy NO at (1-yes_price), settles to 100¢

            # Skip if no meaningful profit gap
            if profit_cents < self.min_profit_cents:
                continue

            # Build polymarket URL
            polymarket_url = None
            if market.events and len(market.events) > 0:
                slug = market.events[0].get("slug")
                if slug:
                    polymarket_url = f"https://polymarket.com/event/{slug}"

            opportunities.append(
                SettlementLagOpportunity(
                    market_id=market.id,
                    question=market.question,
                    polymarket_url=polymarket_url,
                    current_yes_price=yes_price,
                    price_1h_ago=price_1h_ago,
                    price_change_pct=change_pct,
                    direction=direction,
                    expected_settlement=expected,
                    potential_profit_cents=profit_cents,
                    volume_24hr=market.volume_24hr,
                )
            )

        # Sort by potential profit descending
        opportunities.sort(key=lambda x: -x.potential_profit_cents)
        return opportunities
