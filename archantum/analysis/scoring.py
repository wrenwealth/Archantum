"""Market scoring system for ranking and spike detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from archantum.api.clob import PriceData
from archantum.api.gamma import GammaMarket
from archantum.config import settings
from archantum.db import Database


@dataclass
class MarketScoreResult:
    """Result of scoring a market."""

    market_id: str
    question: str
    slug: str | None
    polymarket_url: str | None

    # Component scores (0-100)
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

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "market_id": self.market_id,
            "question": self.question,
            "slug": self.slug,
            "polymarket_url": self.polymarket_url,
            "volume_score": self.volume_score,
            "volume_trend_score": self.volume_trend_score,
            "liquidity_score": self.liquidity_score,
            "volatility_score": self.volatility_score,
            "spread_score": self.spread_score,
            "activity_score": self.activity_score,
            "total_score": self.total_score,
            "previous_score": self.previous_score,
            "score_change": self.score_change,
        }


@dataclass
class ScoreSpikeAlert:
    """Alert for a significant score increase."""

    market_id: str
    question: str
    slug: str | None
    polymarket_url: str | None
    previous_score: float
    current_score: float
    score_change: float
    top_factor: str  # Which component contributed most to increase

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "market_id": self.market_id,
            "question": self.question,
            "slug": self.slug,
            "polymarket_url": self.polymarket_url,
            "previous_score": self.previous_score,
            "current_score": self.current_score,
            "score_change": self.score_change,
            "top_factor": self.top_factor,
        }


class MarketScorer:
    """Scores markets 0-100 based on multiple factors."""

    # Scoring weights (must sum to 1.0)
    WEIGHTS = {
        "volume": 0.25,
        "volume_trend": 0.15,
        "liquidity": 0.20,
        "volatility": 0.15,
        "spread": 0.15,
        "activity": 0.10,
    }

    # Threshold for score spike alerts
    SPIKE_THRESHOLD = 15.0  # Points increase to trigger alert

    def __init__(self, db: Database, spike_threshold: float | None = None):
        self.db = db
        self.spike_threshold = spike_threshold or self.SPIKE_THRESHOLD

    async def score_markets(
        self,
        markets: list[GammaMarket],
        prices: dict[str, PriceData],
    ) -> tuple[list[MarketScoreResult], list[ScoreSpikeAlert]]:
        """Score all markets and detect spikes.

        Returns:
            tuple of (scores, spike_alerts)
        """
        scores = []
        spikes = []

        # Get all volumes and liquidities for percentile calculation
        all_volumes = await self.db.get_all_24h_volumes()
        all_liquidities = await self.db.get_all_liquidities()

        for market in markets:
            price_data = prices.get(market.id)

            result = await self._score_market(
                market, price_data, all_volumes, all_liquidities
            )

            if result:
                scores.append(result)

                # Save to database
                await self.db.save_market_score(
                    market_id=result.market_id,
                    volume_score=result.volume_score,
                    volume_trend_score=result.volume_trend_score,
                    liquidity_score=result.liquidity_score,
                    volatility_score=result.volatility_score,
                    spread_score=result.spread_score,
                    activity_score=result.activity_score,
                    total_score=result.total_score,
                    previous_score=result.previous_score,
                    score_change=result.score_change,
                )

                # Check for spike
                if result.score_change and result.score_change >= self.spike_threshold:
                    top_factor = self._get_top_factor(result)
                    spike = ScoreSpikeAlert(
                        market_id=result.market_id,
                        question=result.question,
                        slug=result.slug,
                        polymarket_url=result.polymarket_url,
                        previous_score=result.previous_score or 0,
                        current_score=result.total_score,
                        score_change=result.score_change,
                        top_factor=top_factor,
                    )
                    spikes.append(spike)

        # Sort by total score
        scores.sort(key=lambda x: x.total_score, reverse=True)

        return scores, spikes

    async def _score_market(
        self,
        market: GammaMarket,
        price_data: PriceData | None,
        all_volumes: list[float],
        all_liquidities: list[float],
    ) -> MarketScoreResult | None:
        """Score a single market."""
        # Get previous score for comparison
        prev_score_record = await self.db.get_latest_market_score(market.id)
        previous_score = prev_score_record.total_score if prev_score_record else None

        # Calculate component scores
        volume_score = self._calc_volume_score(market.volume_24hr, all_volumes)
        volume_trend_score = await self._calc_volume_trend_score(market)
        liquidity_score = self._calc_liquidity_score(market.liquidity, all_liquidities)
        volatility_score = await self._calc_volatility_score(market.id)
        spread_score = self._calc_spread_score(price_data)
        activity_score = await self._calc_activity_score(market.id)

        # Calculate weighted total
        total_score = (
            volume_score * self.WEIGHTS["volume"]
            + volume_trend_score * self.WEIGHTS["volume_trend"]
            + liquidity_score * self.WEIGHTS["liquidity"]
            + volatility_score * self.WEIGHTS["volatility"]
            + spread_score * self.WEIGHTS["spread"]
            + activity_score * self.WEIGHTS["activity"]
        )

        # Calculate change
        score_change = None
        if previous_score is not None:
            score_change = total_score - previous_score

        return MarketScoreResult(
            market_id=market.id,
            question=market.question,
            slug=market.slug,
            polymarket_url=market.polymarket_url,
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

    def _calc_volume_score(
        self,
        volume_24h: float | None,
        all_volumes: list[float],
    ) -> float:
        """Calculate volume score as percentile rank (0-100)."""
        if not volume_24h or not all_volumes:
            return 0.0

        # Percentile rank
        count_below = sum(1 for v in all_volumes if v < volume_24h)
        percentile = (count_below / len(all_volumes)) * 100

        return min(100.0, percentile)

    async def _calc_volume_trend_score(self, market: GammaMarket) -> float:
        """Calculate volume trend score: current vol / 7-day avg * 50.

        Score capped at 100.
        """
        if not market.volume_24hr:
            return 0.0

        avg_volume = await self.db.get_volume_rolling_average(
            market.id,
            days=settings.volume_rolling_days,
        )

        if not avg_volume or avg_volume == 0:
            return 50.0  # Neutral if no history

        ratio = market.volume_24hr / avg_volume
        score = ratio * 50.0

        return min(100.0, max(0.0, score))

    def _calc_liquidity_score(
        self,
        liquidity: float | None,
        all_liquidities: list[float],
    ) -> float:
        """Calculate liquidity score as percentile rank (0-100)."""
        if not liquidity or not all_liquidities:
            return 0.0

        # Percentile rank
        count_below = sum(1 for l in all_liquidities if l < liquidity)
        percentile = (count_below / len(all_liquidities)) * 100

        return min(100.0, percentile)

    async def _calc_volatility_score(self, market_id: str) -> float:
        """Calculate volatility score: (high - low) / avg * 1000.

        Based on 24h price range. Score capped at 100.
        """
        snapshots = await self.db.get_price_snapshots(
            market_id,
            since=datetime.utcnow() - timedelta(hours=24),
            limit=1000,
        )

        if not snapshots:
            return 0.0

        prices = [s.yes_price for s in snapshots if s.yes_price is not None]

        if len(prices) < 2:
            return 0.0

        high = max(prices)
        low = min(prices)
        avg = sum(prices) / len(prices)

        if avg == 0:
            return 0.0

        volatility = (high - low) / avg
        score = volatility * 1000

        return min(100.0, max(0.0, score))

    def _calc_spread_score(self, price_data: PriceData | None) -> float:
        """Calculate spread score: (1 - abs(1.0 - yes - no)) * 100.

        Low spread = high score.
        """
        if not price_data or price_data.yes_price is None or price_data.no_price is None:
            return 0.0

        total = price_data.yes_price + price_data.no_price
        deviation = abs(1.0 - total)

        # Invert so tight spread = high score
        score = (1.0 - deviation) * 100

        return min(100.0, max(0.0, score))

    async def _calc_activity_score(self, market_id: str) -> float:
        """Calculate activity score: price_updates_per_hour * 10.

        Score capped at 100.
        """
        updates = await self.db.get_price_updates_count(market_id, hours=1)

        score = updates * 10.0

        return min(100.0, score)

    def _get_top_factor(self, result: MarketScoreResult) -> str:
        """Determine which factor contributed most to the score."""
        factors = {
            "Volume": result.volume_score * self.WEIGHTS["volume"],
            "Volume Trend": result.volume_trend_score * self.WEIGHTS["volume_trend"],
            "Liquidity": result.liquidity_score * self.WEIGHTS["liquidity"],
            "Volatility": result.volatility_score * self.WEIGHTS["volatility"],
            "Spread": result.spread_score * self.WEIGHTS["spread"],
            "Activity": result.activity_score * self.WEIGHTS["activity"],
        }

        return max(factors, key=factors.get)

    async def get_top_markets(self, limit: int = 10) -> list[MarketScoreResult]:
        """Get top N markets by score."""
        market_scores = await self.db.get_top_scored_markets(limit=limit)

        results = []
        for market, score in market_scores:
            # Construct URL from event_id (which is the event slug)
            polymarket_url = f"https://polymarket.com/event/{market.event_id}" if market.event_id else None
            result = MarketScoreResult(
                market_id=market.id,
                question=market.question,
                slug=market.slug,
                polymarket_url=polymarket_url,
                volume_score=score.volume_score,
                volume_trend_score=score.volume_trend_score,
                liquidity_score=score.liquidity_score,
                volatility_score=score.volatility_score,
                spread_score=score.spread_score,
                activity_score=score.activity_score,
                total_score=score.total_score,
                previous_score=score.previous_score,
                score_change=score.score_change,
            )
            results.append(result)

        return results

    async def get_market_score(self, market_id: str) -> MarketScoreResult | None:
        """Get the latest score for a specific market."""
        score = await self.db.get_latest_market_score(market_id)
        market = await self.db.get_market(market_id)

        if not score or not market:
            return None

        # Construct URL from event_id (which is the event slug)
        polymarket_url = f"https://polymarket.com/event/{market.event_id}" if market.event_id else None

        return MarketScoreResult(
            market_id=market.id,
            question=market.question,
            slug=market.slug,
            polymarket_url=polymarket_url,
            volume_score=score.volume_score,
            volume_trend_score=score.volume_trend_score,
            liquidity_score=score.liquidity_score,
            volatility_score=score.volatility_score,
            spread_score=score.spread_score,
            activity_score=score.activity_score,
            total_score=score.total_score,
            previous_score=score.previous_score,
            score_change=score.score_change,
        )
