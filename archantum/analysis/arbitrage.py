"""Arbitrage detection: Yes + No != 100%."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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


class OpportunityReason(Enum):
    """Why an arbitrage opportunity likely exists."""

    LOW_LIQUIDITY = "low_liquidity"
    SETTLEMENT_LAG = "settlement_lag"
    MARKET_STRUCTURE = "market_structure"
    MULTI_OUTCOME_MISPRICING = "multi_outcome_mispricing"
    DEPENDENCY_VIOLATION = "dependency_violation"
    NEW_INFORMATION = "new_information"
    UNKNOWN = "unknown"


REASON_EXPLANATIONS = {
    OpportunityReason.LOW_LIQUIDITY: "Low orderbook depth allows prices to deviate — may close quickly once liquidity returns",
    OpportunityReason.SETTLEMENT_LAG: "Market outcome appears decided but price hasn't fully converged to 0/100¢ yet",
    OpportunityReason.MARKET_STRUCTURE: "Yes + No prices don't sum to $1 due to market maker spread or fee structure",
    OpportunityReason.MULTI_OUTCOME_MISPRICING: "Outcome probabilities in this event don't sum to 100% — structural mispricing",
    OpportunityReason.DEPENDENCY_VIOLATION: "Logically related markets have inconsistent pricing",
    OpportunityReason.NEW_INFORMATION: "Recent rapid price movement suggests new information not yet fully priced in",
    OpportunityReason.UNKNOWN: "Opportunity source unclear — verify manually before trading",
}


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
    end_date: datetime | None = None
    volume_24hr: float | None = None

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

    @property
    def days_until_resolution(self) -> float | None:
        """Days until market resolves."""
        if not self.end_date:
            return None
        now = datetime.utcnow()
        delta = self.end_date - now
        days = delta.total_seconds() / 86400  # seconds in a day
        return max(0, days)

    @property
    def annualized_return_pct(self) -> float | None:
        """Calculate annualized return based on resolution date.

        Formula: (profit_pct / days) * 365
        Higher = better capital efficiency
        """
        days = self.days_until_resolution
        if days is None or days <= 0:
            return None
        # Profit percentage
        profit_pct = self.arbitrage_pct
        # Annualized: (profit / days) * 365
        return (profit_pct / days) * 365

    @property
    def capital_efficiency_score(self) -> float:
        """Score combining profit % and time to resolution.

        Higher score = better opportunity
        - Considers both raw profit and time value
        - Markets without end_date get base score from profit only
        """
        base_score = self.arbitrage_pct * 10  # Base from profit %

        annual_return = self.annualized_return_pct
        if annual_return is not None:
            # Bonus for high annualized returns (capped at 1000% APY contribution)
            time_bonus = min(annual_return / 10, 100)
            return base_score + time_bonus

        return base_score

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
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "days_until_resolution": self.days_until_resolution,
            "annualized_return_pct": self.annualized_return_pct,
            "capital_efficiency_score": self.capital_efficiency_score,
            "volume_24hr": self.volume_24hr,
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

        # Sort by tier first, then by capital efficiency score (considers both profit % and time to resolution)
        # This prioritizes:
        # 1. Higher tier opportunities (ALPHA > HIGH_VALUE > STANDARD)
        # 2. Within same tier: higher capital efficiency (profit % + time value)
        tier_order = {
            ArbitrageTier.ALPHA: 0,
            ArbitrageTier.HIGH_VALUE: 1,
            ArbitrageTier.STANDARD: 2,
        }
        opportunities.sort(
            key=lambda x: (tier_order.get(x.tier, 3), -x.capital_efficiency_score)
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

        # Parse end_date if available
        end_date = None
        if market.end_date:
            try:
                # Handle ISO format with or without timezone
                end_date_str = market.end_date.replace("Z", "+00:00")
                end_date = datetime.fromisoformat(end_date_str).replace(tzinfo=None)
            except (ValueError, AttributeError):
                pass

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
            end_date=end_date,
            volume_24hr=market.volume_24hr,
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


@dataclass
class GuaranteedProfit:
    """Profit guarantee calculation accounting for fees and slippage."""

    theoretical_profit_cents: float   # Raw gap: (1.0 - total_price) * 100
    estimated_fees_cents: float       # ~2% per side on Polymarket
    estimated_slippage_cents: float   # From liquidity profile (0 if no enrichment)
    guaranteed_profit_cents: float    # theoretical - fees - slippage
    capture_ratio: float              # guaranteed / theoretical (0.0-1.0)
    confidence: str                   # "HIGH" (>=75%), "MEDIUM" (>=50%), "LOW" (<50%)


def calculate_guaranteed_profit(
    opp: ArbitrageOpportunity,
    enrichment: "LiquidityAdjustedArbitrage | None" = None,
    fee_rate: float = 0.02,
) -> GuaranteedProfit:
    """Calculate guaranteed profit after fees and slippage."""
    theoretical = (1.0 - opp.total_price) * 100  # cents

    # Fees: buying YES + NO = 2 transactions
    fees = opp.total_price * fee_rate * 2 * 100  # cents

    # Slippage from liquidity enrichment
    slippage = 0.0
    if enrichment:
        yes_slip = enrichment.yes_liquidity.slippage_pct_1000 or 0
        no_slip = enrichment.no_liquidity.slippage_pct_1000 or 0
        slippage = (yes_slip + no_slip) / 2 * opp.total_price * 100  # cents

    guaranteed = max(0, theoretical - fees - slippage)
    ratio = guaranteed / theoretical if theoretical > 0 else 0

    if ratio >= 0.75:
        confidence = "HIGH"
    elif ratio >= 0.50:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return GuaranteedProfit(
        theoretical_profit_cents=theoretical,
        estimated_fees_cents=fees,
        estimated_slippage_cents=slippage,
        guaranteed_profit_cents=guaranteed,
        capture_ratio=ratio,
        confidence=confidence,
    )


def classify_opportunity_reason(
    opp,
    enrichment=None,
    price_movement_pct: float | None = None,
) -> OpportunityReason:
    """Classify why an arbitrage opportunity likely exists."""
    # Check liquidity first — low depth is most common cause
    if enrichment and enrichment.combined_depth_usd < 500:
        return OpportunityReason.LOW_LIQUIDITY

    # Check for settlement lag (extreme prices)
    if hasattr(opp, 'yes_price'):
        if opp.yes_price > 0.95 or opp.yes_price < 0.05:
            return OpportunityReason.SETTLEMENT_LAG

    # Check for recent rapid price movement
    if price_movement_pct is not None and abs(price_movement_pct) > 10:
        return OpportunityReason.NEW_INFORMATION

    # Default: market structure (spread/fee related)
    return OpportunityReason.MARKET_STRUCTURE
