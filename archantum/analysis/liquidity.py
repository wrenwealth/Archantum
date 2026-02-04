"""Liquidity change detection."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from archantum.db import Database
from archantum.api.gamma import GammaMarket


@dataclass
class LiquidityChange:
    """Represents a significant liquidity change."""

    market_id: str
    question: str
    slug: str | None
    previous_liquidity: float
    current_liquidity: float
    change_amount: float
    change_pct: float
    direction: str  # 'added' or 'removed'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LiquidityAnalyzer:
    """Detects significant liquidity changes."""

    def __init__(
        self,
        db: Database,
        min_change_pct: float = 0.2,  # 20% change
        min_change_amount: float = 5000.0,  # $5000 minimum change
    ):
        """
        Initialize liquidity analyzer.

        Args:
            db: Database instance
            min_change_pct: Minimum percentage change to alert (0.2 = 20%)
            min_change_amount: Minimum dollar amount change to alert
        """
        self.db = db
        self.min_change_pct = min_change_pct
        self.min_change_amount = min_change_amount
        self._previous_liquidity: dict[str, float] = {}

    async def analyze(self, markets: list[GammaMarket]) -> list[LiquidityChange]:
        """
        Detect significant liquidity changes.

        Args:
            markets: List of markets to analyze

        Returns:
            List of significant liquidity changes
        """
        changes: list[LiquidityChange] = []

        for market in markets:
            current_liquidity = market.liquidity or 0

            # Skip if no previous data
            if market.id not in self._previous_liquidity:
                self._previous_liquidity[market.id] = current_liquidity
                continue

            previous_liquidity = self._previous_liquidity[market.id]

            # Calculate change
            if previous_liquidity > 0:
                change_amount = current_liquidity - previous_liquidity
                change_pct = change_amount / previous_liquidity

                # Check if change is significant
                if (
                    abs(change_pct) >= self.min_change_pct
                    and abs(change_amount) >= self.min_change_amount
                ):
                    direction = "added" if change_amount > 0 else "removed"

                    change = LiquidityChange(
                        market_id=market.id,
                        question=market.question,
                        slug=market.slug,
                        previous_liquidity=previous_liquidity,
                        current_liquidity=current_liquidity,
                        change_amount=abs(change_amount),
                        change_pct=abs(change_pct) * 100,
                        direction=direction,
                    )
                    changes.append(change)

            # Update previous liquidity
            self._previous_liquidity[market.id] = current_liquidity

        return changes

    def reset(self):
        """Reset stored liquidity data."""
        self._previous_liquidity.clear()
