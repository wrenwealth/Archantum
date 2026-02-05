"""Confluence analyzer for multi-indicator alignment detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from rich.console import Console

from archantum.config import settings
from archantum.analysis.indicators import TechnicalIndicatorCalculator, IndicatorValues

if TYPE_CHECKING:
    from archantum.db import Database


console = Console()


@dataclass
class ConfluenceSignal:
    """Confluence signal from multiple aligned indicators."""

    market_id: str
    question: str
    timestamp: datetime

    # Current price
    current_price: float

    # Confluence score (0-100)
    confluence_score: float

    # Signal direction
    signal: str  # 'strong_buy', 'buy', 'neutral', 'sell', 'strong_sell'

    # Individual indicator signals
    rsi_signal: str  # 'oversold', 'neutral', 'overbought'
    rsi_value: float | None

    macd_signal: str  # 'bullish', 'bearish', 'bullish_cross', 'bearish_cross', 'neutral'
    macd_histogram: float | None

    ma_trend: str  # 'strong_bullish', 'bullish', 'neutral', 'bearish', 'strong_bearish'
    cross_signal: str | None  # 'golden_cross', 'death_cross', or None

    # URL
    polymarket_url: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "market_id": self.market_id,
            "question": self.question,
            "timestamp": self.timestamp.isoformat(),
            "current_price": self.current_price,
            "confluence_score": self.confluence_score,
            "signal": self.signal,
            "rsi_signal": self.rsi_signal,
            "rsi_value": self.rsi_value,
            "macd_signal": self.macd_signal,
            "macd_histogram": self.macd_histogram,
            "ma_trend": self.ma_trend,
            "cross_signal": self.cross_signal,
            "polymarket_url": self.polymarket_url,
        }


class ConfluenceAnalyzer:
    """Analyze indicator confluence for trading signals."""

    # Weights for confluence scoring
    RSI_WEIGHT = 30
    MACD_WEIGHT = 35
    MA_TREND_WEIGHT = 25
    CROSS_WEIGHT = 10

    def __init__(self, db: "Database"):
        self.db = db
        self.indicator_calculator = TechnicalIndicatorCalculator(db)

    async def analyze_market(
        self,
        market_id: str,
        current_price: float,
        question: str = "",
        polymarket_url: str | None = None,
    ) -> ConfluenceSignal | None:
        """Analyze a market for indicator confluence.

        Args:
            market_id: The market ID
            current_price: Current yes price
            question: Market question
            polymarket_url: URL to market

        Returns:
            ConfluenceSignal if analysis successful, None otherwise
        """
        # Calculate current indicators
        indicators = await self.indicator_calculator.calculate_indicators(market_id)
        if not indicators:
            return None

        # Get previous indicators for crossover detection
        prev_indicators = await self._get_previous_indicators(market_id)

        # Analyze each indicator
        rsi_signal = self.indicator_calculator.interpret_rsi(indicators.rsi_14)
        rsi_score = self._score_rsi(indicators.rsi_14, rsi_signal)

        macd_signal = self.indicator_calculator.interpret_macd(
            indicators.macd_line,
            indicators.macd_signal,
            prev_indicators.macd_line if prev_indicators else None,
            prev_indicators.macd_signal if prev_indicators else None,
        )
        macd_score = self._score_macd(macd_signal, indicators.macd_histogram)

        ma_trend = self.indicator_calculator.interpret_ma_trend(
            current_price,
            indicators.sma_10,
            indicators.sma_20,
            indicators.sma_50,
        )
        ma_score = self._score_ma_trend(ma_trend)

        cross_signal = self.indicator_calculator.detect_golden_death_cross(
            indicators.sma_50,
            indicators.sma_20,
            prev_indicators.sma_50 if prev_indicators else None,
            prev_indicators.sma_20 if prev_indicators else None,
        )
        cross_score = self._score_cross(cross_signal)

        # Calculate total confluence score
        total_score = rsi_score + macd_score + ma_score + cross_score

        # Determine overall signal
        signal = self._determine_signal(total_score, rsi_signal, macd_signal, ma_trend)

        # Save indicators with confluence info
        indicators.confluence_score = total_score
        indicators.confluence_signal = signal
        await self._save_indicators_with_confluence(indicators, total_score, signal)

        return ConfluenceSignal(
            market_id=market_id,
            question=question,
            timestamp=datetime.utcnow(),
            current_price=current_price,
            confluence_score=total_score,
            signal=signal,
            rsi_signal=rsi_signal,
            rsi_value=indicators.rsi_14,
            macd_signal=macd_signal,
            macd_histogram=indicators.macd_histogram,
            ma_trend=ma_trend,
            cross_signal=cross_signal,
            polymarket_url=polymarket_url,
        )

    async def analyze_markets(
        self,
        markets: list[dict],
        prices: dict[str, float],
    ) -> list[ConfluenceSignal]:
        """Analyze multiple markets for confluence signals.

        Args:
            markets: List of market dicts with 'id', 'question', 'event_id'
            prices: Dict of market_id -> yes_price

        Returns:
            List of ConfluenceSignal that meet the alert threshold
        """
        signals = []

        for market in markets:
            market_id = market.get("id", "")
            price = prices.get(market_id)

            if not price:
                continue

            try:
                signal = await self.analyze_market(
                    market_id=market_id,
                    current_price=price,
                    question=market.get("question", ""),
                    polymarket_url=self._build_url(market.get("event_id")),
                )

                if signal and signal.confluence_score >= settings.confluence_alert_threshold:
                    signals.append(signal)

            except Exception as e:
                console.print(f"[yellow]Confluence analysis error for {market_id}: {e}[/yellow]")

        return signals

    def _score_rsi(self, rsi: float | None, signal: str) -> float:
        """Score RSI contribution to confluence."""
        if rsi is None or signal == "unknown":
            return 0

        # Oversold is bullish, overbought is bearish
        if signal == "oversold":
            # More oversold = higher bullish score
            intensity = (settings.rsi_oversold - rsi) / settings.rsi_oversold
            return self.RSI_WEIGHT * (0.5 + 0.5 * min(intensity, 1))
        elif signal == "overbought":
            # More overbought = higher bearish score (negative)
            intensity = (rsi - settings.rsi_overbought) / (100 - settings.rsi_overbought)
            return -self.RSI_WEIGHT * (0.5 + 0.5 * min(intensity, 1))
        else:
            # Neutral - small contribution based on which side of 50
            if rsi < 50:
                return self.RSI_WEIGHT * 0.1  # Slightly bullish
            else:
                return -self.RSI_WEIGHT * 0.1  # Slightly bearish

    def _score_macd(self, signal: str, histogram: float | None) -> float:
        """Score MACD contribution to confluence."""
        if signal == "unknown":
            return 0

        base_scores = {
            "bullish_cross": self.MACD_WEIGHT,
            "bearish_cross": -self.MACD_WEIGHT,
            "bullish": self.MACD_WEIGHT * 0.6,
            "bearish": -self.MACD_WEIGHT * 0.6,
            "neutral": 0,
        }

        score = base_scores.get(signal, 0)

        # Adjust based on histogram magnitude
        if histogram is not None and score != 0:
            histogram_factor = min(abs(histogram) * 100, 1)  # Cap at 1
            score *= (0.7 + 0.3 * histogram_factor)

        return score

    def _score_ma_trend(self, trend: str) -> float:
        """Score MA trend contribution to confluence."""
        scores = {
            "strong_bullish": self.MA_TREND_WEIGHT,
            "bullish": self.MA_TREND_WEIGHT * 0.6,
            "neutral": 0,
            "bearish": -self.MA_TREND_WEIGHT * 0.6,
            "strong_bearish": -self.MA_TREND_WEIGHT,
            "unknown": 0,
        }
        return scores.get(trend, 0)

    def _score_cross(self, cross: str | None) -> float:
        """Score golden/death cross contribution."""
        if cross == "golden_cross":
            return self.CROSS_WEIGHT
        elif cross == "death_cross":
            return -self.CROSS_WEIGHT
        return 0

    def _determine_signal(
        self,
        score: float,
        rsi_signal: str,
        macd_signal: str,
        ma_trend: str,
    ) -> str:
        """Determine overall signal from confluence score and indicators."""
        # Strong signals require high score AND multiple aligned indicators
        bullish_count = 0
        bearish_count = 0

        if rsi_signal == "oversold":
            bullish_count += 1
        elif rsi_signal == "overbought":
            bearish_count += 1

        if macd_signal in ("bullish", "bullish_cross"):
            bullish_count += 1
        elif macd_signal in ("bearish", "bearish_cross"):
            bearish_count += 1

        if ma_trend in ("bullish", "strong_bullish"):
            bullish_count += 1
        elif ma_trend in ("bearish", "strong_bearish"):
            bearish_count += 1

        # Determine signal
        if score >= 60 and bullish_count >= 2:
            return "strong_buy"
        elif score >= 40 and bullish_count >= 1:
            return "buy"
        elif score <= -60 and bearish_count >= 2:
            return "strong_sell"
        elif score <= -40 and bearish_count >= 1:
            return "sell"
        else:
            return "neutral"

    async def _get_previous_indicators(self, market_id: str) -> IndicatorValues | None:
        """Get the previous indicator values for crossover detection."""
        from sqlalchemy import select
        from archantum.db.models import TechnicalIndicator

        async with self.db.async_session() as session:
            # Get second most recent record
            result = await session.execute(
                select(TechnicalIndicator)
                .where(TechnicalIndicator.market_id == market_id)
                .order_by(TechnicalIndicator.timestamp.desc())
                .offset(1)
                .limit(1)
            )
            record = result.scalar_one_or_none()

            if not record:
                return None

            return IndicatorValues(
                market_id=record.market_id,
                timestamp=record.timestamp,
                rsi_14=record.rsi_14,
                macd_line=record.macd_line,
                macd_signal=record.macd_signal,
                macd_histogram=record.macd_histogram,
                sma_10=record.sma_10,
                sma_20=record.sma_20,
                sma_50=record.sma_50,
                ema_12=record.ema_12,
                ema_26=record.ema_26,
            )

    async def _save_indicators_with_confluence(
        self,
        indicators: IndicatorValues,
        score: float,
        signal: str,
    ) -> None:
        """Save indicators with confluence score and signal."""
        from archantum.db.models import TechnicalIndicator

        async with self.db.async_session() as session:
            record = TechnicalIndicator(
                market_id=indicators.market_id,
                timestamp=indicators.timestamp,
                rsi_14=indicators.rsi_14,
                macd_line=indicators.macd_line,
                macd_signal=indicators.macd_signal,
                macd_histogram=indicators.macd_histogram,
                sma_10=indicators.sma_10,
                sma_20=indicators.sma_20,
                sma_50=indicators.sma_50,
                ema_12=indicators.ema_12,
                ema_26=indicators.ema_26,
                confluence_score=score,
                confluence_signal=signal,
            )
            session.add(record)
            await session.commit()

    def _build_url(self, event_id: str | None) -> str | None:
        """Build Polymarket URL from event ID."""
        if not event_id:
            return None
        return f"https://polymarket.com/event/{event_id}"
