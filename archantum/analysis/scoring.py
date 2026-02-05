"""Market scoring and ranking system."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from archantum.api.gamma import GammaMarket
from archantum.api.clob import PriceData
from archantum.db.models import PriceSnapshot, VolumeSnapshot, MarketScore


@dataclass
class MarketScoreResult:
    """Result of market scoring."""

    market_id: str
    question: str
    slug: str | None

    # Individual scores (0-100)
    volume_score: float
    volume_trend_score: float
    liquidity_score: float
    volatility_score: float
    spread_score: float
    activity_score: float

    # Composite
    total_score: float
    previous_score: float | None
    score_change: float | None

    # Raw data for context
    volume_24hr: float
    liquidity: float
    spread: float

    @property
    def rank_tier(self) -> str:
        """Get tier based on total score."""
        if self.total_score >= 80:
            return "S"  # Top tier
        elif self.total_score >= 60:
            return "A"
        elif self.total_score >= 40:
            return "B"
        elif self.total_score >= 20:
            return "C"
        return "D"

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "market_id": self.market_id,
            "question": self.question,
            "slug": self.slug,
            "volume_score": round(self.volume_score, 1),
            "volume_trend_score": round(self.volume_trend_score, 1),
            "liquidity_score": round(self.liquidity_score, 1),
            "volatility_score": round(self.volatility_score, 1),
            "spread_score": round(self.spread_score, 1),
            "activity_score": round(self.activity_score, 1),
            "total_score": round(self.total_score, 1),
            "previous_score": round(self.previous_score, 1) if self.previous_score else None,
            "score_change": round(self.score_change, 1) if self.score_change else None,
            "rank_tier": self.rank_tier,
            "volume_24hr": self.volume_24hr,
            "liquidity": self.liquidity,
            "spread": self.spread,
        }


class MarketScorer:
    """Scores markets on a 0-100 scale based on multiple factors."""

    # Score weights (must sum to 1.0)
    WEIGHT_VOLUME = 0.25
    WEIGHT_VOLUME_TREND = 0.10
    WEIGHT_LIQUIDITY = 0.25
    WEIGHT_VOLATILITY = 0.15
    WEIGHT_SPREAD = 0.15
    WEIGHT_ACTIVITY = 0.10

    # Thresholds for scoring
    VOLUME_THRESHOLDS = [1000, 5000, 25000, 100000, 500000]  # $
    LIQUIDITY_THRESHOLDS = [1000, 5000, 25000, 100000, 500000]  # $
    VOLATILITY_THRESHOLDS = [0.01, 0.03, 0.05, 0.10, 0.20]  # 1-20%

    def __init__(self, db: AsyncSession):
        self.db = db
        self._volume_percentiles: dict[int, float] = {}
        self._liquidity_percentiles: dict[int, float] = {}

    async def score_markets(
        self,
        markets: list[GammaMarket],
        prices: dict[str, PriceData],
    ) -> list[MarketScoreResult]:
        """Score all markets and return ranked results."""
        results = []

        # Calculate percentile benchmarks from current batch
        volumes = [m.volume_24hr or 0 for m in markets if m.volume_24hr]
        liquidities = [m.liquidity or 0 for m in markets if m.liquidity]

        if volumes:
            self._volume_percentiles = {
                p: np.percentile(volumes, p) for p in [25, 50, 75, 90, 99]
            }
        if liquidities:
            self._liquidity_percentiles = {
                p: np.percentile(liquidities, p) for p in [25, 50, 75, 90, 99]
            }

        for market in markets:
            price_data = prices.get(market.id)
            if not price_data:
                continue

            score = await self._score_market(market, price_data)
            if score:
                results.append(score)

        # Sort by total score descending
        results.sort(key=lambda x: x.total_score, reverse=True)
        return results

    async def _score_market(
        self,
        market: GammaMarket,
        price_data: PriceData,
    ) -> MarketScoreResult | None:
        """Score a single market."""
        # Get historical data for trend/volatility calculations
        price_history = await self._get_price_history(market.id, hours=24)
        volume_history = await self._get_volume_history(market.id, hours=24)

        # Calculate individual scores
        volume_score = self._score_volume(market.volume_24hr or 0)
        volume_trend_score = self._score_volume_trend(volume_history)
        liquidity_score = self._score_liquidity(market.liquidity or 0)
        volatility_score = self._score_volatility(price_history)
        spread_score = self._score_spread(price_data)
        activity_score = self._score_activity(price_history)

        # Calculate weighted total
        total_score = (
            volume_score * self.WEIGHT_VOLUME
            + volume_trend_score * self.WEIGHT_VOLUME_TREND
            + liquidity_score * self.WEIGHT_LIQUIDITY
            + volatility_score * self.WEIGHT_VOLATILITY
            + spread_score * self.WEIGHT_SPREAD
            + activity_score * self.WEIGHT_ACTIVITY
        )

        # Get previous score for change tracking
        previous_score = await self._get_previous_score(market.id)
        score_change = total_score - previous_score if previous_score else None

        # Save to database
        await self._save_score(
            market.id,
            volume_score,
            volume_trend_score,
            liquidity_score,
            volatility_score,
            spread_score,
            activity_score,
            total_score,
            previous_score,
            score_change,
        )

        return MarketScoreResult(
            market_id=market.id,
            question=market.question,
            slug=market.slug,
            volume_score=volume_score,
            volume_trend_score=volume_trend_score,
            liquidity_score=liquidity_score,
            volatility_score=volatility_score,
            spread_score=spread_score,
            activity_score=activity_score,
            total_score=total_score,
            previous_score=previous_score,
            score_change=score_change,
            volume_24hr=market.volume_24hr or 0,
            liquidity=market.liquidity or 0,
            spread=price_data.spread or 0,
        )

    def _score_volume(self, volume: float) -> float:
        """Score based on 24h volume (0-100)."""
        if volume <= 0:
            return 0

        # Use percentile-based scoring if available
        if self._volume_percentiles:
            if volume >= self._volume_percentiles.get(99, float("inf")):
                return 100
            elif volume >= self._volume_percentiles.get(90, float("inf")):
                return 90
            elif volume >= self._volume_percentiles.get(75, float("inf")):
                return 75
            elif volume >= self._volume_percentiles.get(50, float("inf")):
                return 50
            elif volume >= self._volume_percentiles.get(25, float("inf")):
                return 25
            return 10

        # Fallback to absolute thresholds
        for i, threshold in enumerate(self.VOLUME_THRESHOLDS):
            if volume < threshold:
                return (i / len(self.VOLUME_THRESHOLDS)) * 100
        return 100

    def _score_volume_trend(self, volume_history: list[tuple[datetime, float]]) -> float:
        """Score based on volume trend (0-100). Higher = growing volume."""
        if len(volume_history) < 2:
            return 50  # Neutral

        # Compare recent vs earlier volumes
        recent = volume_history[-len(volume_history) // 2 :]
        earlier = volume_history[: len(volume_history) // 2]

        recent_avg = np.mean([v for _, v in recent]) if recent else 0
        earlier_avg = np.mean([v for _, v in earlier]) if earlier else 0

        if earlier_avg <= 0:
            return 50

        change_pct = (recent_avg - earlier_avg) / earlier_avg

        # Map change to 0-100 score
        # -50% or worse = 0, +100% or better = 100
        score = 50 + (change_pct * 50)
        return max(0, min(100, score))

    def _score_liquidity(self, liquidity: float) -> float:
        """Score based on liquidity depth (0-100)."""
        if liquidity <= 0:
            return 0

        # Use percentile-based scoring if available
        if self._liquidity_percentiles:
            if liquidity >= self._liquidity_percentiles.get(99, float("inf")):
                return 100
            elif liquidity >= self._liquidity_percentiles.get(90, float("inf")):
                return 90
            elif liquidity >= self._liquidity_percentiles.get(75, float("inf")):
                return 75
            elif liquidity >= self._liquidity_percentiles.get(50, float("inf")):
                return 50
            elif liquidity >= self._liquidity_percentiles.get(25, float("inf")):
                return 25
            return 10

        # Fallback to absolute thresholds
        for i, threshold in enumerate(self.LIQUIDITY_THRESHOLDS):
            if liquidity < threshold:
                return (i / len(self.LIQUIDITY_THRESHOLDS)) * 100
        return 100

    def _score_volatility(self, price_history: list[tuple[datetime, float]]) -> float:
        """Score based on price volatility (0-100). Higher = more volatile."""
        if len(price_history) < 5:
            return 50  # Neutral

        prices = [p for _, p in price_history]
        if not prices or max(prices) == min(prices):
            return 50

        # Calculate standard deviation as % of mean
        mean_price = np.mean(prices)
        if mean_price <= 0:
            return 50

        std_dev = np.std(prices)
        volatility = std_dev / mean_price

        # Map volatility to score
        # 0% = 0, 20%+ = 100
        for i, threshold in enumerate(self.VOLATILITY_THRESHOLDS):
            if volatility < threshold:
                return (i / len(self.VOLATILITY_THRESHOLDS)) * 100
        return 100

    def _score_spread(self, price_data: PriceData) -> float:
        """Score based on Yes/No spread tightness (0-100). Tighter = higher score."""
        if price_data.yes_price is None or price_data.no_price is None:
            return 50

        # Calculate how close to $1.00 total
        total = price_data.yes_price + price_data.no_price
        spread = abs(1.0 - total)

        # Tight spread (< 1%) = 100, wide spread (> 10%) = 0
        if spread <= 0.01:
            return 100
        elif spread <= 0.02:
            return 80
        elif spread <= 0.03:
            return 60
        elif spread <= 0.05:
            return 40
        elif spread <= 0.10:
            return 20
        return 0

    def _score_activity(self, price_history: list[tuple[datetime, float]]) -> float:
        """Score based on recent trading activity (0-100)."""
        if not price_history:
            return 0

        # Count data points in last hour vs last 24 hours
        now = datetime.utcnow()
        one_hour_ago = now - timedelta(hours=1)

        recent_count = sum(1 for ts, _ in price_history if ts >= one_hour_ago)
        total_count = len(price_history)

        if total_count == 0:
            return 0

        # Expected activity ratio for 1 hour out of 24 hours ≈ 4.2%
        expected_ratio = 1 / 24
        actual_ratio = recent_count / total_count

        # If activity is at expected level = 50, higher = better
        activity_ratio = actual_ratio / expected_ratio if expected_ratio > 0 else 0
        score = min(100, activity_ratio * 50)
        return score

    async def _get_price_history(
        self, market_id: str, hours: int = 24
    ) -> list[tuple[datetime, float]]:
        """Get price history for a market."""
        since = datetime.utcnow() - timedelta(hours=hours)

        result = await self.db.execute(
            select(PriceSnapshot.timestamp, PriceSnapshot.yes_price)
            .where(PriceSnapshot.market_id == market_id)
            .where(PriceSnapshot.timestamp >= since)
            .where(PriceSnapshot.yes_price.isnot(None))
            .order_by(PriceSnapshot.timestamp)
        )
        rows = result.all()
        return [(row.timestamp, row.yes_price) for row in rows]

    async def _get_volume_history(
        self, market_id: str, hours: int = 24
    ) -> list[tuple[datetime, float]]:
        """Get volume history for a market."""
        since = datetime.utcnow() - timedelta(hours=hours)

        result = await self.db.execute(
            select(VolumeSnapshot.timestamp, VolumeSnapshot.volume_24h)
            .where(VolumeSnapshot.market_id == market_id)
            .where(VolumeSnapshot.timestamp >= since)
            .where(VolumeSnapshot.volume_24h.isnot(None))
            .order_by(VolumeSnapshot.timestamp)
        )
        rows = result.all()
        return [(row.timestamp, row.volume_24h) for row in rows]

    async def _get_previous_score(self, market_id: str) -> float | None:
        """Get the previous score for a market."""
        result = await self.db.execute(
            select(MarketScore.total_score)
            .where(MarketScore.market_id == market_id)
            .order_by(MarketScore.timestamp.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return row

    async def _save_score(
        self,
        market_id: str,
        volume_score: float,
        volume_trend_score: float,
        liquidity_score: float,
        volatility_score: float,
        spread_score: float,
        activity_score: float,
        total_score: float,
        previous_score: float | None,
        score_change: float | None,
    ) -> None:
        """Save market score to database."""
        score = MarketScore(
            market_id=market_id,
            volume_score=volume_score,
            volume_trend_score=volume_trend_score,
            liquidity_score=liquidity_score,
            volatility_score=volatility_score,
            spread_score=spread_score,
            activity_score=activity_score,
            total_score=total_score,
            previous_score=previous_score,
            score_change=score_change,
        )
        self.db.add(score)


def get_top_markets(
    scores: list[MarketScoreResult], limit: int = 10
) -> list[MarketScoreResult]:
    """Get top-ranked markets."""
    return scores[:limit]


def get_improving_markets(
    scores: list[MarketScoreResult], min_change: float = 5.0
) -> list[MarketScoreResult]:
    """Get markets with improving scores."""
    return [
        s for s in scores if s.score_change is not None and s.score_change >= min_change
    ]


def get_declining_markets(
    scores: list[MarketScoreResult], min_change: float = 5.0
) -> list[MarketScoreResult]:
    """Get markets with declining scores."""
    return [
        s for s in scores if s.score_change is not None and s.score_change <= -min_change
    ]
