"""Multi-outcome arbitrage detection for events with 3+ markets."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from archantum.api.clob import PriceData
from archantum.api.gamma import GammaMarket


class MultiOutcomeTier(Enum):
    """Tier based on probability gap size."""

    NONE = "none"
    STANDARD = "standard"    # Sum < 0.99 (or > 1.01)
    HIGH_VALUE = "high_value"  # Sum < 0.95 (or > 1.05)
    ALPHA = "alpha"           # Sum < 0.90 (or > 1.10)


@dataclass
class OutcomeInfo:
    """Info about a single outcome in a multi-outcome event."""

    market_id: str
    question: str
    yes_price: float
    polymarket_url: str | None = None


@dataclass
class MultiOutcomeArbitrage:
    """Multi-outcome arbitrage opportunity."""

    event_slug: str
    event_name: str
    outcomes: list[OutcomeInfo]
    total_probability: float  # Sum of all YES prices
    gap_pct: float            # Distance from 1.0 as percentage
    strategy: str             # 'buy_all' or 'sell_all'
    profit_per_dollar: float  # Profit per $1 invested
    tier: MultiOutcomeTier = MultiOutcomeTier.STANDARD

    @property
    def outcome_count(self) -> int:
        return len(self.outcomes)

    def calculate_profit(self, position_size: float) -> float:
        """Calculate estimated profit for a given position size."""
        return position_size * self.profit_per_dollar

    def to_dict(self) -> dict:
        return {
            "event_slug": self.event_slug,
            "event_name": self.event_name,
            "outcome_count": self.outcome_count,
            "total_probability": self.total_probability,
            "gap_pct": self.gap_pct,
            "strategy": self.strategy,
            "profit_per_dollar": self.profit_per_dollar,
            "tier": self.tier.value,
            "outcomes": [
                {
                    "market_id": o.market_id,
                    "question": o.question,
                    "yes_price": o.yes_price,
                }
                for o in self.outcomes
            ],
        }


class MultiOutcomeAnalyzer:
    """Detects arbitrage in multi-outcome events (3+ markets per event)."""

    # Tier thresholds
    TIER_ALPHA = 0.10    # 10%+ gap
    TIER_HIGH_VALUE = 0.05  # 5-10% gap
    TIER_STANDARD = 0.01    # 1-5% gap

    def analyze(
        self,
        markets: list[GammaMarket],
        prices: dict[str, PriceData],
    ) -> list[MultiOutcomeArbitrage]:
        """Find multi-outcome arbitrage opportunities.

        Groups markets by event slug, filters to events with 3+ markets,
        and checks if sum of YES prices deviates from 1.0.
        """
        # Group markets by event slug
        event_groups: dict[str, list[GammaMarket]] = {}
        for market in markets:
            slug = self._get_event_slug(market)
            if slug:
                event_groups.setdefault(slug, []).append(market)

        opportunities = []

        for event_slug, event_markets in event_groups.items():
            # Only consider multi-outcome events (3+ markets)
            if len(event_markets) < 3:
                continue

            # Build outcome list with prices
            outcomes: list[OutcomeInfo] = []
            for m in event_markets:
                price_data = prices.get(m.id)
                if not price_data or price_data.yes_price is None:
                    continue
                outcomes.append(
                    OutcomeInfo(
                        market_id=m.id,
                        question=m.question,
                        yes_price=price_data.yes_price,
                        polymarket_url=m.polymarket_url,
                    )
                )

            if len(outcomes) < 3:
                continue

            total_prob = sum(o.yes_price for o in outcomes)

            # Check for buy-all arbitrage (sum < 0.99)
            if total_prob < 0.99:
                gap = 1.0 - total_prob
                gap_pct = gap * 100
                tier = self._get_tier(gap)
                if tier == MultiOutcomeTier.NONE:
                    continue

                profit_per_dollar = gap / total_prob  # Buy all at total_prob, receive $1

                event_name = self._get_event_name(event_markets)
                opportunities.append(
                    MultiOutcomeArbitrage(
                        event_slug=event_slug,
                        event_name=event_name,
                        outcomes=outcomes,
                        total_probability=total_prob,
                        gap_pct=gap_pct,
                        strategy="buy_all",
                        profit_per_dollar=profit_per_dollar,
                        tier=tier,
                    )
                )

            # Check for sell-all arbitrage (sum > 1.01)
            elif total_prob > 1.01:
                gap = total_prob - 1.0
                gap_pct = gap * 100
                tier = self._get_tier(gap)
                if tier == MultiOutcomeTier.NONE:
                    continue

                profit_per_dollar = gap / total_prob

                event_name = self._get_event_name(event_markets)
                opportunities.append(
                    MultiOutcomeArbitrage(
                        event_slug=event_slug,
                        event_name=event_name,
                        outcomes=outcomes,
                        total_probability=total_prob,
                        gap_pct=gap_pct,
                        strategy="sell_all",
                        profit_per_dollar=profit_per_dollar,
                        tier=tier,
                    )
                )

        # Sort by tier then gap size
        tier_order = {
            MultiOutcomeTier.ALPHA: 0,
            MultiOutcomeTier.HIGH_VALUE: 1,
            MultiOutcomeTier.STANDARD: 2,
        }
        opportunities.sort(key=lambda x: (tier_order.get(x.tier, 3), -x.gap_pct))
        return opportunities

    def _get_tier(self, gap: float) -> MultiOutcomeTier:
        """Determine tier based on gap from 1.0."""
        if gap >= self.TIER_ALPHA:
            return MultiOutcomeTier.ALPHA
        elif gap >= self.TIER_HIGH_VALUE:
            return MultiOutcomeTier.HIGH_VALUE
        elif gap >= self.TIER_STANDARD:
            return MultiOutcomeTier.STANDARD
        return MultiOutcomeTier.NONE

    def _get_event_slug(self, market: GammaMarket) -> str | None:
        """Get event slug from market (same logic as main.py)."""
        if market.events and len(market.events) > 0:
            return market.events[0].get("slug")
        return market.event_slug

    def _get_event_name(self, event_markets: list[GammaMarket]) -> str:
        """Extract event name from the first market's events data."""
        for m in event_markets:
            if m.events and len(m.events) > 0:
                title = m.events[0].get("title")
                if title:
                    return title
        # Fallback: use common prefix of questions
        questions = [m.question for m in event_markets]
        if questions:
            return questions[0][:80]
        return "Unknown Event"
