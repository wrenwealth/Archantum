"""New market detection."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Any

from archantum.db import Database
from archantum.api.gamma import GammaMarket


@dataclass
class NewMarket:
    """Represents a newly detected interesting market."""

    market_id: str
    question: str
    slug: str | None
    polymarket_url: str | None
    volume_24hr: float
    liquidity: float
    outcomes: list[str]
    outcome_prices: list[str]
    created_hours_ago: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NewMarketAnalyzer:
    """Detects new interesting markets."""

    def __init__(
        self,
        db: Database,
        min_volume: float = 5000.0,
        min_liquidity: float = 1000.0,
        max_age_hours: int = 24,
    ):
        """
        Initialize new market analyzer.

        Args:
            db: Database instance
            min_volume: Minimum 24h volume to consider interesting ($)
            min_liquidity: Minimum liquidity to consider interesting ($)
            max_age_hours: Maximum age in hours to consider "new"
        """
        self.db = db
        self.min_volume = min_volume
        self.min_liquidity = min_liquidity
        self.max_age_hours = max_age_hours
        self._seen_markets: set[str] = set()

    async def analyze(self, markets: list[GammaMarket]) -> list[NewMarket]:
        """
        Detect new interesting markets.

        Args:
            markets: List of markets to analyze

        Returns:
            List of newly detected interesting markets
        """
        new_markets: list[NewMarket] = []

        for market in markets:
            # Skip if we've already seen this market
            if market.id in self._seen_markets:
                continue

            # Check if market exists in database (already tracked)
            existing = await self.db.get_market(market.id)
            if existing:
                self._seen_markets.add(market.id)
                continue

            # Check if market meets criteria for "interesting"
            volume = market.volume_24hr or 0
            liquidity = market.liquidity or 0

            if volume >= self.min_volume or liquidity >= self.min_liquidity:
                new_market = NewMarket(
                    market_id=market.id,
                    question=market.question,
                    slug=market.slug,
                    polymarket_url=market.polymarket_url,
                    volume_24hr=volume,
                    liquidity=liquidity,
                    outcomes=market.outcomes or [],
                    outcome_prices=market.outcome_prices or [],
                    created_hours_ago=0,  # We don't have exact creation time from API
                )
                new_markets.append(new_market)

            # Mark as seen
            self._seen_markets.add(market.id)

        return new_markets

    def reset(self):
        """Reset seen markets."""
        self._seen_markets.clear()
