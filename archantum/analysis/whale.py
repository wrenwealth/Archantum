"""Whale activity detection."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from archantum.config import settings
from archantum.db import Database
from archantum.api.gamma import GammaMarket


@dataclass
class WhaleActivity:
    """Represents detected whale activity."""

    market_id: str
    question: str
    slug: str | None
    volume_change: float  # Dollar change in volume
    volume_change_pct: float  # Percentage change
    previous_volume: float
    current_volume: float
    estimated_trade_size: float
    direction: str  # 'buy' or 'sell' based on price movement

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WhaleAnalyzer:
    """Detects whale activity based on sudden volume changes."""

    def __init__(self, db: Database, min_trade_size: float = 10000.0, volume_jump_threshold: float = 0.1):
        """
        Initialize whale analyzer.

        Args:
            db: Database instance
            min_trade_size: Minimum estimated trade size to consider whale ($)
            volume_jump_threshold: Minimum volume jump percentage (0.1 = 10%)
        """
        self.db = db
        self.min_trade_size = min_trade_size
        self.volume_jump_threshold = volume_jump_threshold
        self._previous_volumes: dict[str, float] = {}

    async def analyze(
        self,
        markets: list[GammaMarket],
        price_changes: dict[str, float] | None = None,
    ) -> list[WhaleActivity]:
        """
        Detect whale activity by analyzing volume changes.

        Args:
            markets: List of markets to analyze
            price_changes: Optional dict of market_id -> price change direction

        Returns:
            List of detected whale activities
        """
        whale_activities: list[WhaleActivity] = []

        for market in markets:
            current_volume = market.volume_24hr or 0

            # Skip if no previous volume data
            if market.id not in self._previous_volumes:
                self._previous_volumes[market.id] = current_volume
                continue

            previous_volume = self._previous_volumes[market.id]

            # Calculate volume change
            if previous_volume > 0:
                volume_change = current_volume - previous_volume
                volume_change_pct = volume_change / previous_volume

                # Detect significant volume jump (potential whale)
                if (
                    volume_change > self.min_trade_size
                    and volume_change_pct > self.volume_jump_threshold
                ):
                    # Determine direction based on price change if available
                    direction = "unknown"
                    if price_changes and market.id in price_changes:
                        direction = "buy" if price_changes[market.id] > 0 else "sell"

                    whale = WhaleActivity(
                        market_id=market.id,
                        question=market.question,
                        slug=market.slug,
                        volume_change=volume_change,
                        volume_change_pct=volume_change_pct * 100,
                        previous_volume=previous_volume,
                        current_volume=current_volume,
                        estimated_trade_size=volume_change,
                        direction=direction,
                    )
                    whale_activities.append(whale)

            # Update previous volume
            self._previous_volumes[market.id] = current_volume

        return whale_activities

    def reset(self):
        """Reset stored volume data."""
        self._previous_volumes.clear()
