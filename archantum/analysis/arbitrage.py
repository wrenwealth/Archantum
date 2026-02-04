"""Arbitrage detection: Yes + No != 100%."""

from __future__ import annotations

from dataclasses import dataclass
from archantum.config import settings
from archantum.api.clob import PriceData
from archantum.api.gamma import GammaMarket


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

    @property
    def potential_profit_pct(self) -> float:
        """Calculate potential profit percentage."""
        return self.arbitrage_pct

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
        }


class ArbitrageAnalyzer:
    """Analyzes markets for arbitrage opportunities."""

    def __init__(self, threshold: float | None = None):
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

        # Sort by arbitrage percentage (highest first)
        opportunities.sort(key=lambda x: x.arbitrage_pct, reverse=True)
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
        deviation = abs(1.0 - total)

        if deviation < self.threshold:
            return None

        arbitrage_pct = deviation * 100
        direction = "under" if total < 1.0 else "over"

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
        )
