"""LP (Liquidity Provider) Rewards analysis for Polymarket."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from archantum.api.gamma import GammaMarket
from archantum.api.clob import PriceData


@dataclass
class LPRewardConfig:
    """LP reward configuration for a market."""

    market_id: str
    question: str
    polymarket_url: str | None
    max_spread_cents: float  # Max spread from midpoint to qualify (in cents)
    min_size: float  # Minimum order size to qualify
    daily_reward_pool: float | None  # Estimated daily rewards (USD)
    midpoint: float  # Current midpoint price
    volume_24hr: float
    liquidity: float


@dataclass
class LPOpportunity:
    """Represents an LP opportunity."""

    market_id: str
    question: str
    polymarket_url: str | None

    # Market state
    midpoint: float
    max_spread_cents: float
    min_size: float

    # Reward metrics
    estimated_daily_reward: float
    estimated_apy: float  # Based on position size
    competition_score: float  # 0-100, lower = less competition

    # Recommended position
    recommended_spread: float  # cents from mid
    recommended_size: float
    requires_two_sided: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "question": self.question,
            "polymarket_url": self.polymarket_url,
            "midpoint": self.midpoint,
            "max_spread_cents": self.max_spread_cents,
            "min_size": self.min_size,
            "estimated_daily_reward": self.estimated_daily_reward,
            "estimated_apy": self.estimated_apy,
            "competition_score": self.competition_score,
            "recommended_spread": self.recommended_spread,
            "recommended_size": self.recommended_size,
            "requires_two_sided": self.requires_two_sided,
        }


@dataclass
class LPSimulation:
    """Simulation result for LP position."""

    market_id: str
    question: str

    # Position details
    position_size: float  # Total capital deployed
    bid_price: float
    ask_price: float
    spread_cents: float

    # Estimated rewards
    q_score: float
    estimated_daily_reward: float
    estimated_weekly_reward: float
    estimated_monthly_reward: float

    # Risk metrics
    execution_risk: str  # 'low', 'medium', 'high'
    impermanent_loss_risk: str

    # ROI
    estimated_daily_apy: float
    estimated_weekly_apy: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "question": self.question,
            "position_size": self.position_size,
            "bid_price": self.bid_price,
            "ask_price": self.ask_price,
            "spread_cents": self.spread_cents,
            "q_score": self.q_score,
            "estimated_daily_reward": self.estimated_daily_reward,
            "estimated_weekly_reward": self.estimated_weekly_reward,
            "estimated_monthly_reward": self.estimated_monthly_reward,
            "execution_risk": self.execution_risk,
            "impermanent_loss_risk": self.impermanent_loss_risk,
            "estimated_daily_apy": self.estimated_daily_apy,
            "estimated_weekly_apy": self.estimated_weekly_apy,
        }


class LPRewardsAnalyzer:
    """Analyzes LP reward opportunities on Polymarket."""

    # Single-sided penalty factor
    C_FACTOR = 3.0

    # Default values when market config not available
    DEFAULT_MAX_SPREAD = 3.0  # cents
    DEFAULT_MIN_SIZE = 200.0  # shares

    def __init__(self):
        pass

    def calculate_spread_score(
        self,
        max_spread: float,
        actual_spread: float,
        multiplier: float = 1.0,
    ) -> float:
        """Calculate spread scoring function S(v, s).

        Formula: S(v, s) = ((v - s) / v)² × b

        Args:
            max_spread: Maximum spread from midpoint (v)
            actual_spread: Order's spread from adjusted midpoint (s)
            multiplier: In-game multiplier (b)

        Returns:
            Spread score (0 to 1 × multiplier)
        """
        if actual_spread >= max_spread:
            return 0.0

        score = ((max_spread - actual_spread) / max_spread) ** 2
        return score * multiplier

    def calculate_q_score(
        self,
        bid_size: float,
        bid_spread: float,
        ask_size: float,
        ask_spread: float,
        max_spread: float,
        midpoint: float,
    ) -> float:
        """Calculate Q score for a two-sided LP position.

        Args:
            bid_size: Size of bid order
            bid_spread: Bid spread from midpoint (cents)
            ask_size: Size of ask order
            ask_spread: Ask spread from midpoint (cents)
            max_spread: Maximum qualifying spread
            midpoint: Current midpoint price

        Returns:
            Q_min score
        """
        # Calculate spread scores
        bid_score = self.calculate_spread_score(max_spread, bid_spread)
        ask_score = self.calculate_spread_score(max_spread, ask_spread)

        # Q_one (bid weighted) and Q_two (ask weighted)
        q_one = bid_size * bid_score
        q_two = ask_size * ask_score

        # Check if two-sided is required (midpoint < 10% or > 90%)
        requires_two_sided = midpoint < 0.10 or midpoint > 0.90

        if requires_two_sided:
            # Must have both sides
            q_min = min(q_one, q_two)
        else:
            # Single-sided allowed but penalized by factor c
            q_min = max(
                min(q_one, q_two),
                max(q_one / self.C_FACTOR, q_two / self.C_FACTOR)
            )

        return q_min

    def estimate_reward_share(
        self,
        your_q_score: float,
        estimated_total_q: float,
        daily_pool: float,
    ) -> float:
        """Estimate your share of daily rewards.

        Args:
            your_q_score: Your Q_min score
            estimated_total_q: Estimated total Q from all LPs
            daily_pool: Daily reward pool in USD

        Returns:
            Estimated daily reward in USD
        """
        if estimated_total_q <= 0:
            return 0.0

        share = your_q_score / estimated_total_q
        return share * daily_pool

    def find_opportunities(
        self,
        markets: list[GammaMarket],
        prices: dict[str, PriceData],
        min_daily_reward: float = 10.0,
    ) -> list[LPOpportunity]:
        """Find attractive LP opportunities.

        Args:
            markets: List of markets
            prices: Current prices
            min_daily_reward: Minimum estimated daily reward to include

        Returns:
            List of LP opportunities sorted by estimated APY
        """
        opportunities: list[LPOpportunity] = []

        for market in markets:
            price = prices.get(market.id)
            if not price or price.yes_price is None:
                continue

            midpoint = price.yes_price

            # Get reward config (use defaults if not available)
            max_spread = self.DEFAULT_MAX_SPREAD
            min_size = self.DEFAULT_MIN_SIZE

            # Estimate daily reward pool based on volume and liquidity
            # Higher volume markets typically have higher reward pools
            volume = market.volume_24hr or 0
            liquidity = market.liquidity or 0

            # Rough estimate: reward pool correlates with volume
            # This is an approximation - actual pools vary
            estimated_pool = self._estimate_reward_pool(volume, liquidity)

            if estimated_pool < min_daily_reward:
                continue

            # Estimate competition (based on liquidity depth)
            competition = self._estimate_competition(liquidity, volume)

            # Check if two-sided required
            requires_two_sided = midpoint < 0.10 or midpoint > 0.90

            # Recommended position
            recommended_spread = max_spread * 0.5  # 50% of max for good score
            recommended_size = max(min_size, 500)  # At least 500 shares

            # Estimate APY for a $1000 position
            position_value = 1000
            q_score = self.calculate_q_score(
                bid_size=position_value / 2 / midpoint,
                bid_spread=recommended_spread,
                ask_size=position_value / 2 / (1 - midpoint),
                ask_spread=recommended_spread,
                max_spread=max_spread,
                midpoint=midpoint,
            )

            # Assume you capture 1% of pool (conservative estimate)
            estimated_daily = estimated_pool * 0.01
            estimated_apy = (estimated_daily * 365 / position_value) * 100

            opportunities.append(LPOpportunity(
                market_id=market.id,
                question=market.question,
                polymarket_url=market.polymarket_url,
                midpoint=midpoint,
                max_spread_cents=max_spread,
                min_size=min_size,
                estimated_daily_reward=estimated_daily,
                estimated_apy=estimated_apy,
                competition_score=competition,
                recommended_spread=recommended_spread,
                recommended_size=recommended_size,
                requires_two_sided=requires_two_sided,
            ))

        # Sort by APY descending
        opportunities.sort(key=lambda x: x.estimated_apy, reverse=True)

        return opportunities

    def simulate_position(
        self,
        market: GammaMarket,
        price: PriceData,
        position_size: float,
        spread_cents: float,
    ) -> LPSimulation | None:
        """Simulate an LP position and estimate rewards.

        Args:
            market: Market to LP in
            price: Current price data
            position_size: Total capital to deploy (USD)
            spread_cents: Spread from midpoint (cents)

        Returns:
            Simulation result or None if invalid
        """
        if price.yes_price is None:
            return None

        midpoint = price.yes_price
        max_spread = self.DEFAULT_MAX_SPREAD

        if spread_cents > max_spread:
            return None  # Won't qualify for rewards

        # Split position 50/50 between bid and ask
        half_position = position_size / 2

        bid_price = midpoint - (spread_cents / 100)
        ask_price = midpoint + (spread_cents / 100)

        # Calculate sizes in shares
        bid_size = half_position / bid_price if bid_price > 0 else 0
        ask_size = half_position / (1 - ask_price) if ask_price < 1 else 0

        # Q score
        q_score = self.calculate_q_score(
            bid_size=bid_size,
            bid_spread=spread_cents,
            ask_size=ask_size,
            ask_spread=spread_cents,
            max_spread=max_spread,
            midpoint=midpoint,
        )

        # Estimate rewards
        volume = market.volume_24hr or 0
        liquidity = market.liquidity or 0
        estimated_pool = self._estimate_reward_pool(volume, liquidity)

        # Estimate market share (based on position size vs liquidity)
        market_share = min(0.10, position_size / max(liquidity, 1000))
        estimated_daily = estimated_pool * market_share

        estimated_weekly = estimated_daily * 7
        estimated_monthly = estimated_daily * 30

        # Calculate APY
        daily_apy = (estimated_daily * 365 / position_size) * 100
        weekly_apy = (estimated_weekly * 52 / position_size) * 100

        # Risk assessment
        execution_risk = self._assess_execution_risk(spread_cents, volume)
        il_risk = self._assess_il_risk(midpoint)

        return LPSimulation(
            market_id=market.id,
            question=market.question,
            position_size=position_size,
            bid_price=bid_price,
            ask_price=ask_price,
            spread_cents=spread_cents,
            q_score=q_score,
            estimated_daily_reward=estimated_daily,
            estimated_weekly_reward=estimated_weekly,
            estimated_monthly_reward=estimated_monthly,
            execution_risk=execution_risk,
            impermanent_loss_risk=il_risk,
            estimated_daily_apy=daily_apy,
            estimated_weekly_apy=weekly_apy,
        )

    def _estimate_reward_pool(self, volume_24h: float, liquidity: float) -> float:
        """Estimate daily reward pool based on market metrics.

        This is an approximation - actual pools are set by Polymarket.
        """
        # Higher volume = higher rewards (rough correlation)
        if volume_24h > 1_000_000:
            return 500.0  # High volume markets
        elif volume_24h > 100_000:
            return 100.0
        elif volume_24h > 10_000:
            return 25.0
        elif volume_24h > 1_000:
            return 10.0
        else:
            return 5.0

    def _estimate_competition(self, liquidity: float, volume: float) -> float:
        """Estimate competition level (0-100, lower = less competition).

        Based on liquidity depth - more liquidity = more competition.
        """
        if liquidity > 1_000_000:
            return 90.0  # Very competitive
        elif liquidity > 100_000:
            return 70.0
        elif liquidity > 10_000:
            return 50.0
        elif liquidity > 1_000:
            return 30.0
        else:
            return 10.0  # Low competition

    def _assess_execution_risk(self, spread_cents: float, volume: float) -> str:
        """Assess risk of orders getting filled unfavorably."""
        if spread_cents <= 1.0:
            return "high"  # Very tight spread, high fill risk
        elif spread_cents <= 2.0:
            if volume > 100_000:
                return "high"
            return "medium"
        else:
            return "low"

    def _assess_il_risk(self, midpoint: float) -> str:
        """Assess impermanent loss risk based on midpoint.

        Markets near 50% have highest IL risk (can move either way).
        Markets near 0% or 100% have lower IL risk.
        """
        distance_from_50 = abs(midpoint - 0.50)

        if distance_from_50 < 0.10:
            return "high"  # 40-60% range
        elif distance_from_50 < 0.25:
            return "medium"  # 25-40% or 60-75%
        else:
            return "low"  # <25% or >75%

    def get_top_opportunities(
        self,
        markets: list[GammaMarket],
        prices: dict[str, PriceData],
        top_n: int = 10,
    ) -> list[LPOpportunity]:
        """Get top LP opportunities by estimated APY.

        Args:
            markets: List of markets
            prices: Current prices
            top_n: Number of top opportunities to return

        Returns:
            Top N opportunities
        """
        all_opps = self.find_opportunities(markets, prices, min_daily_reward=5.0)
        return all_opps[:top_n]
