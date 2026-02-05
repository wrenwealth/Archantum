"""Arbitrage detection: Yes + No != 100%."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from archantum.config import settings
from archantum.api.clob import PriceData
from archantum.api.gamma import GammaMarket


class ArbitrageTier(Enum):
    """Arbitrage opportunity tiers based on gap size."""

    NONE = "none"
    STANDARD = "standard"  # 1-5% gap (Yes + No < 99¢)
    HIGH_VALUE = "high_value"  # 5-10% gap (Yes + No < 95¢)
    ALPHA = "alpha"  # 10%+ gap (Yes + No < 90¢)


@dataclass
class ArbitrageOpportunity:
    """Represents an arbitrage opportunity."""

    market_id: str
    question: str
    slug: str | None
    polymarket_url: str | None
    yes_price: float
    no_price: float
    total_price: float
    arbitrage_pct: float
    direction: str  # 'under' or 'over'
    tier: ArbitrageTier = ArbitrageTier.STANDARD

    @property
    def potential_profit_pct(self) -> float:
        """Calculate potential profit percentage."""
        return self.arbitrage_pct

    @property
    def profit_per_share(self) -> float:
        """Profit per share in cents (guaranteed if Yes + No < $1)."""
        if self.direction == "under":
            return (1.0 - self.total_price) * 100  # cents
        return 0.0  # Can't profit from overpriced markets directly

    def calculate_profit(self, position_size: float) -> float:
        """Calculate estimated profit for a given position size.

        Args:
            position_size: Investment amount in dollars

        Returns:
            Estimated profit in dollars
        """
        if self.direction != "under" or self.total_price >= 1.0:
            return 0.0

        # Buy both Yes and No shares
        # Cost per complete set = total_price
        # Payout per complete set = $1.00
        # Profit per complete set = 1.0 - total_price
        shares = position_size / self.total_price
        profit = shares * (1.0 - self.total_price)
        return profit

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "market_id": self.market_id,
            "question": self.question,
            "slug": self.slug,
            "polymarket_url": self.polymarket_url,
            "yes_price": self.yes_price,
            "no_price": self.no_price,
            "total_price": self.total_price,
            "arbitrage_pct": self.arbitrage_pct,
            "direction": self.direction,
            "potential_profit_pct": self.potential_profit_pct,
            "profit_per_share": self.profit_per_share,
            "tier": self.tier.value,
            "profit_100": self.calculate_profit(100),
            "profit_500": self.calculate_profit(500),
            "profit_1000": self.calculate_profit(1000),
        }


class ArbitrageAnalyzer:
    """Analyzes markets for arbitrage opportunities."""

    # Tier thresholds (total price below these values)
    TIER_ALPHA = 0.90  # 10%+ gap
    TIER_HIGH_VALUE = 0.95  # 5-10% gap
    TIER_STANDARD = 0.99  # 1-5% gap

    def __init__(self, threshold: float | None = None):
        # Default to 1% threshold (total < 0.99)
        self.threshold = threshold or settings.arbitrage_threshold

    def analyze(
        self,
        markets: list[GammaMarket],
        prices: dict[str, PriceData],
    ) -> list[ArbitrageOpportunity]:
        """Find arbitrage opportunities across markets."""
        opportunities = []

        for market in markets:
            price_data = prices.get(market.id)
            if not price_data:
                continue

            opp = self.check_market(market, price_data)
            if opp:
                opportunities.append(opp)

        # Sort by tier (alpha first), then by arbitrage percentage
        tier_order = {
            ArbitrageTier.ALPHA: 0,
            ArbitrageTier.HIGH_VALUE: 1,
            ArbitrageTier.STANDARD: 2,
        }
        opportunities.sort(
            key=lambda x: (tier_order.get(x.tier, 3), -x.arbitrage_pct)
        )
        return opportunities

    def check_market(
        self,
        market: GammaMarket,
        price_data: PriceData,
    ) -> ArbitrageOpportunity | None:
        """Check a single market for arbitrage."""
        if price_data.yes_price is None or price_data.no_price is None:
            return None

        total = price_data.yes_price + price_data.no_price

        # Only alert on underpriced markets (Yes + No < $1)
        # Overpriced markets (> $1) are not profitable arbitrage
        if total >= self.TIER_STANDARD:
            return None

        # Determine tier
        tier = self._get_tier(total)

        arbitrage_pct = (1.0 - total) * 100
        direction = "under"

        return ArbitrageOpportunity(
            market_id=market.id,
            question=market.question,
            slug=market.slug,
            polymarket_url=market.polymarket_url,
            yes_price=price_data.yes_price,
            no_price=price_data.no_price,
            total_price=total,
            arbitrage_pct=arbitrage_pct,
            direction=direction,
            tier=tier,
        )

    def _get_tier(self, total_price: float) -> ArbitrageTier:
        """Determine arbitrage tier based on total price."""
        if total_price < self.TIER_ALPHA:
            return ArbitrageTier.ALPHA
        elif total_price < self.TIER_HIGH_VALUE:
            return ArbitrageTier.HIGH_VALUE
        elif total_price < self.TIER_STANDARD:
            return ArbitrageTier.STANDARD
        return ArbitrageTier.NONE

    def check_multi_outcome_market(
        self,
        market_id: str,
        question: str,
        outcome_prices: list[float],
        polymarket_url: str | None = None,
    ) -> ArbitrageOpportunity | None:
        """Check a multi-outcome market for arbitrage.

        For multi-outcome markets, the sum of all probabilities should equal 100%.
        If sum < 99%, there's an arbitrage opportunity.
        """
        if not outcome_prices:
            return None

        total = sum(outcome_prices)

        if total >= self.TIER_STANDARD:
            return None

        tier = self._get_tier(total)
        arbitrage_pct = (1.0 - total) * 100

        # For multi-outcome, we represent as a simplified view
        return ArbitrageOpportunity(
            market_id=market_id,
            question=question,
            slug=None,
            polymarket_url=polymarket_url,
            yes_price=outcome_prices[0] if outcome_prices else 0,
            no_price=total - outcome_prices[0] if len(outcome_prices) > 1 else 0,
            total_price=total,
            arbitrage_pct=arbitrage_pct,
            direction="under",
            tier=tier,
        )
