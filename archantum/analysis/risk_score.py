"""Execution risk scoring for arbitrage opportunities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from archantum.db import Database
from archantum.analysis.arbitrage import ArbitrageOpportunity
from archantum.analysis.liquidity import LiquidityProfile


@dataclass
class ExecutionRiskScore:
    """Execution risk assessment for an arbitrage opportunity."""

    # Individual component scores (1-10)
    liquidity_score: float = 5.0  # 35% weight — depth of orderbook
    stability_score: float = 5.0  # 25% weight — price volatility (low = stable = good)
    time_score: float = 5.0       # 20% weight — time to resolution
    complexity_score: float = 5.0  # 20% weight — number of legs / complexity

    @property
    def total_score(self) -> float:
        """Weighted composite score (1-10)."""
        return (
            self.liquidity_score * 0.35
            + self.stability_score * 0.25
            + self.time_score * 0.20
            + self.complexity_score * 0.20
        )

    @property
    def confidence(self) -> str:
        """Confidence label based on total score."""
        score = self.total_score
        if score >= 7:
            return "High"
        elif score >= 4:
            return "Medium"
        return "Low"


class ExecutionRiskScorer:
    """Scores execution risk for arbitrage opportunities."""

    def __init__(self, db: Database):
        self.db = db

    async def score(
        self,
        opp: ArbitrageOpportunity,
        liquidity: LiquidityProfile | None = None,
    ) -> ExecutionRiskScore:
        """Calculate execution risk score for an arbitrage opportunity.

        Args:
            opp: The arbitrage opportunity
            liquidity: Optional liquidity profile (YES side)

        Returns:
            ExecutionRiskScore with all component scores
        """
        risk = ExecutionRiskScore()

        # 1. Liquidity depth score (1-10)
        risk.liquidity_score = self._score_liquidity(liquidity)

        # 2. Price stability score (1-10) — based on 24h price stddev
        risk.stability_score = await self._score_stability(opp.market_id)

        # 3. Time to resolution score (1-10)
        risk.time_score = self._score_time(opp)

        # 4. Complexity score (1-10) — simple Yes+No arb = high score
        risk.complexity_score = self._score_complexity(opp)

        return risk

    def _score_liquidity(self, liquidity: LiquidityProfile | None) -> float:
        """Score liquidity depth (1-10). More depth = higher score."""
        if liquidity is None:
            return 3.0  # Unknown, assume below average

        depth = liquidity.ask_depth_usd
        if depth >= 50000:
            return 10.0
        elif depth >= 20000:
            return 9.0
        elif depth >= 10000:
            return 8.0
        elif depth >= 5000:
            return 7.0
        elif depth >= 2000:
            return 6.0
        elif depth >= 1000:
            return 5.0
        elif depth >= 500:
            return 4.0
        elif depth >= 100:
            return 3.0
        elif depth > 0:
            return 2.0
        return 1.0

    async def _score_stability(self, market_id: str) -> float:
        """Score price stability (1-10). Low volatility = higher score."""
        since = datetime.utcnow() - timedelta(hours=24)
        snapshots = await self.db.get_price_snapshots(market_id, since=since, limit=200)

        if len(snapshots) < 5:
            return 5.0  # Not enough data, assume average

        prices = [s.yes_price for s in snapshots if s.yes_price is not None]
        if len(prices) < 5:
            return 5.0

        # Calculate standard deviation as % of mean
        mean_price = sum(prices) / len(prices)
        if mean_price <= 0:
            return 5.0

        variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
        stddev = variance ** 0.5
        stddev_pct = (stddev / mean_price) * 100

        # Lower stddev = more stable = higher score
        if stddev_pct < 0.5:
            return 10.0
        elif stddev_pct < 1.0:
            return 9.0
        elif stddev_pct < 2.0:
            return 8.0
        elif stddev_pct < 3.0:
            return 7.0
        elif stddev_pct < 5.0:
            return 6.0
        elif stddev_pct < 8.0:
            return 5.0
        elif stddev_pct < 12.0:
            return 4.0
        elif stddev_pct < 20.0:
            return 3.0
        elif stddev_pct < 30.0:
            return 2.0
        return 1.0

    def _score_time(self, opp: ArbitrageOpportunity) -> float:
        """Score time to resolution (1-10).

        Shorter time = money locked for less time = higher score.
        """
        days = opp.days_until_resolution
        if days is None:
            return 4.0  # Unknown, penalize slightly

        if days <= 1:
            return 10.0
        elif days <= 3:
            return 9.0
        elif days <= 7:
            return 8.0
        elif days <= 14:
            return 7.0
        elif days <= 30:
            return 6.0
        elif days <= 60:
            return 5.0
        elif days <= 90:
            return 4.0
        elif days <= 180:
            return 3.0
        elif days <= 365:
            return 2.0
        return 1.0

    def _score_complexity(self, opp: ArbitrageOpportunity) -> float:
        """Score complexity (1-10). Simple = higher score.

        Standard Yes+No arbitrage is 2 legs = simplest.
        """
        # Standard Yes+No arbitrage: 2 legs, very simple
        return 9.0
