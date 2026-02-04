"""Trend analysis with moving averages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from archantum.db import Database
from archantum.api.gamma import GammaMarket


@dataclass
class TrendSignal:
    """Represents a trend signal."""

    market_id: str
    question: str
    slug: str | None
    current_price: float
    ma_1h: float | None
    ma_4h: float | None
    ma_24h: float | None
    signal: str  # 'bullish', 'bearish', 'neutral', 'reversal_up', 'reversal_down'
    momentum: float  # Positive = bullish momentum, negative = bearish

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "market_id": self.market_id,
            "question": self.question,
            "slug": self.slug,
            "current_price": self.current_price,
            "ma_1h": self.ma_1h,
            "ma_4h": self.ma_4h,
            "ma_24h": self.ma_24h,
            "signal": self.signal,
            "momentum": self.momentum,
        }


class TrendAnalyzer:
    """Analyzes markets for trends using moving averages."""

    def __init__(self, db: Database):
        self.db = db

    async def analyze(self, markets: list[GammaMarket]) -> list[TrendSignal]:
        """Analyze trends across markets."""
        signals = []

        for market in markets:
            signal = await self.check_market(market)
            if signal and signal.signal != "neutral":
                signals.append(signal)

        # Sort by absolute momentum (highest first)
        signals.sort(key=lambda x: abs(x.momentum), reverse=True)
        return signals

    async def check_market(self, market: GammaMarket) -> TrendSignal | None:
        """Check a single market for trend signals."""
        # Get latest price
        latest = await self.db.get_latest_price_snapshot(market.id)
        if not latest or latest.yes_price is None:
            return None

        current_price = latest.yes_price

        # Calculate moving averages
        ma_1h = await self._calculate_ma(market.id, hours=1)
        ma_4h = await self._calculate_ma(market.id, hours=4)
        ma_24h = await self._calculate_ma(market.id, hours=24)

        # Determine signal
        signal, momentum = self._determine_signal(current_price, ma_1h, ma_4h, ma_24h)

        return TrendSignal(
            market_id=market.id,
            question=market.question,
            slug=market.slug,
            current_price=current_price,
            ma_1h=ma_1h,
            ma_4h=ma_4h,
            ma_24h=ma_24h,
            signal=signal,
            momentum=momentum,
        )

    async def _calculate_ma(self, market_id: str, hours: int) -> float | None:
        """Calculate moving average for a given time period."""
        since = datetime.utcnow() - timedelta(hours=hours)
        snapshots = await self.db.get_price_snapshots(market_id, since=since, limit=1000)

        if not snapshots:
            return None

        prices = [s.yes_price for s in snapshots if s.yes_price is not None]
        if not prices:
            return None

        return sum(prices) / len(prices)

    def _determine_signal(
        self,
        current: float,
        ma_1h: float | None,
        ma_4h: float | None,
        ma_24h: float | None,
    ) -> tuple[str, float]:
        """Determine trend signal based on moving averages."""
        if ma_1h is None or ma_4h is None:
            return "neutral", 0.0

        # Calculate momentum
        momentum = 0.0
        if ma_1h:
            momentum += (current - ma_1h) / ma_1h if ma_1h != 0 else 0
        if ma_4h:
            momentum += (current - ma_4h) / ma_4h if ma_4h != 0 else 0
        if ma_24h:
            momentum += (current - ma_24h) / ma_24h if ma_24h != 0 else 0

        # Detect trend reversal
        if ma_24h:
            # Price was below 24h MA but above 1h MA = potential reversal up
            if current > ma_1h and current < ma_24h and ma_1h < ma_24h:
                return "reversal_up", momentum

            # Price was above 24h MA but below 1h MA = potential reversal down
            if current < ma_1h and current > ma_24h and ma_1h > ma_24h:
                return "reversal_down", momentum

        # Simple trend detection
        bullish_count = 0
        bearish_count = 0

        if current > ma_1h:
            bullish_count += 1
        else:
            bearish_count += 1

        if current > ma_4h:
            bullish_count += 1
        else:
            bearish_count += 1

        if ma_24h:
            if current > ma_24h:
                bullish_count += 1
            else:
                bearish_count += 1

        if bullish_count >= 2:
            return "bullish", momentum
        elif bearish_count >= 2:
            return "bearish", momentum
        else:
            return "neutral", momentum
