"""Technical analysis indicators for price data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd
from rich.console import Console

from archantum.config import settings

if TYPE_CHECKING:
    from archantum.db import Database


console = Console()


@dataclass
class IndicatorValues:
    """Calculated indicator values for a market."""

    market_id: str
    timestamp: datetime

    # RSI
    rsi_14: float | None = None

    # MACD
    macd_line: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None

    # Simple Moving Averages
    sma_10: float | None = None
    sma_20: float | None = None
    sma_50: float | None = None

    # Exponential Moving Averages
    ema_12: float | None = None
    ema_26: float | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "market_id": self.market_id,
            "timestamp": self.timestamp.isoformat(),
            "rsi_14": self.rsi_14,
            "macd_line": self.macd_line,
            "macd_signal": self.macd_signal,
            "macd_histogram": self.macd_histogram,
            "sma_10": self.sma_10,
            "sma_20": self.sma_20,
            "sma_50": self.sma_50,
            "ema_12": self.ema_12,
            "ema_26": self.ema_26,
        }


class TechnicalIndicatorCalculator:
    """Calculate technical indicators from historical price data."""

    # Minimum data points required for each indicator
    MIN_PERIODS_RSI = 15  # RSI(14) needs at least 15 periods
    MIN_PERIODS_MACD = 35  # MACD needs EMA(26) + EMA(9) signal
    MIN_PERIODS_SMA_10 = 10
    MIN_PERIODS_SMA_20 = 20
    MIN_PERIODS_SMA_50 = 50
    MIN_PERIODS_EMA_12 = 12
    MIN_PERIODS_EMA_26 = 26

    def __init__(self, db: "Database"):
        self.db = db

    async def calculate_indicators(
        self,
        market_id: str,
        lookback_hours: int = 24,
    ) -> IndicatorValues | None:
        """Calculate all indicators for a market.

        Args:
            market_id: The market ID
            lookback_hours: Hours of data to use

        Returns:
            IndicatorValues or None if insufficient data
        """
        # Fetch price history
        since = datetime.utcnow() - timedelta(hours=lookback_hours)
        snapshots = await self.db.get_price_snapshots(
            market_id=market_id,
            since=since,
            limit=500,  # Should be plenty for 24h of 30s intervals
        )

        if len(snapshots) < self.MIN_PERIODS_RSI:
            return None

        # Convert to pandas DataFrame
        df = self._snapshots_to_dataframe(snapshots)
        if df.empty:
            return None

        # Calculate indicators
        indicators = IndicatorValues(
            market_id=market_id,
            timestamp=datetime.utcnow(),
        )

        # RSI
        if len(df) >= self.MIN_PERIODS_RSI:
            indicators.rsi_14 = self._calculate_rsi(df["price"], period=14)

        # MACD
        if len(df) >= self.MIN_PERIODS_MACD:
            macd_result = self._calculate_macd(df["price"])
            indicators.macd_line = macd_result.get("macd_line")
            indicators.macd_signal = macd_result.get("macd_signal")
            indicators.macd_histogram = macd_result.get("macd_histogram")

        # SMAs
        if len(df) >= self.MIN_PERIODS_SMA_10:
            indicators.sma_10 = self._calculate_sma(df["price"], period=10)
        if len(df) >= self.MIN_PERIODS_SMA_20:
            indicators.sma_20 = self._calculate_sma(df["price"], period=20)
        if len(df) >= self.MIN_PERIODS_SMA_50:
            indicators.sma_50 = self._calculate_sma(df["price"], period=50)

        # EMAs
        if len(df) >= self.MIN_PERIODS_EMA_12:
            indicators.ema_12 = self._calculate_ema(df["price"], period=12)
        if len(df) >= self.MIN_PERIODS_EMA_26:
            indicators.ema_26 = self._calculate_ema(df["price"], period=26)

        return indicators

    async def save_indicators(self, indicators: IndicatorValues) -> None:
        """Save indicator values to database."""
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
            )
            session.add(record)
            await session.commit()

    async def get_latest_indicators(self, market_id: str) -> IndicatorValues | None:
        """Get the most recent indicator values for a market."""
        from sqlalchemy import select
        from archantum.db.models import TechnicalIndicator

        async with self.db.async_session() as session:
            result = await session.execute(
                select(TechnicalIndicator)
                .where(TechnicalIndicator.market_id == market_id)
                .order_by(TechnicalIndicator.timestamp.desc())
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

    def _snapshots_to_dataframe(self, snapshots: list) -> pd.DataFrame:
        """Convert price snapshots to pandas DataFrame."""
        if not snapshots:
            return pd.DataFrame()

        data = []
        for s in snapshots:
            price = s.yes_price
            if price is not None:
                data.append({
                    "timestamp": s.timestamp,
                    "price": price,
                })

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> float | None:
        """Calculate RSI (Relative Strength Index).

        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss
        """
        if len(prices) < period + 1:
            return None

        # Calculate price changes
        delta = prices.diff()

        # Separate gains and losses
        gains = delta.copy()
        losses = delta.copy()
        gains[gains < 0] = 0
        losses[losses > 0] = 0
        losses = abs(losses)

        # Use exponential moving average for smoothing
        avg_gain = gains.ewm(span=period, min_periods=period).mean()
        avg_loss = losses.ewm(span=period, min_periods=period).mean()

        # Calculate RS and RSI
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        # Return the latest RSI value
        latest_rsi = rsi.iloc[-1]
        if pd.isna(latest_rsi):
            return None
        return round(float(latest_rsi), 2)

    def _calculate_macd(
        self,
        prices: pd.Series,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ) -> dict[str, float | None]:
        """Calculate MACD (Moving Average Convergence Divergence).

        MACD Line = EMA(12) - EMA(26)
        Signal Line = EMA(9) of MACD Line
        Histogram = MACD Line - Signal Line
        """
        result = {
            "macd_line": None,
            "macd_signal": None,
            "macd_histogram": None,
        }

        if len(prices) < slow_period + signal_period:
            return result

        # Calculate EMAs
        ema_fast = prices.ewm(span=fast_period, min_periods=fast_period).mean()
        ema_slow = prices.ewm(span=slow_period, min_periods=slow_period).mean()

        # MACD Line
        macd_line = ema_fast - ema_slow

        # Signal Line
        signal_line = macd_line.ewm(span=signal_period, min_periods=signal_period).mean()

        # Histogram
        histogram = macd_line - signal_line

        # Get latest values
        latest_macd = macd_line.iloc[-1]
        latest_signal = signal_line.iloc[-1]
        latest_histogram = histogram.iloc[-1]

        if not pd.isna(latest_macd):
            result["macd_line"] = round(float(latest_macd), 6)
        if not pd.isna(latest_signal):
            result["macd_signal"] = round(float(latest_signal), 6)
        if not pd.isna(latest_histogram):
            result["macd_histogram"] = round(float(latest_histogram), 6)

        return result

    def _calculate_sma(self, prices: pd.Series, period: int) -> float | None:
        """Calculate Simple Moving Average."""
        if len(prices) < period:
            return None

        sma = prices.rolling(window=period).mean()
        latest = sma.iloc[-1]

        if pd.isna(latest):
            return None
        return round(float(latest), 6)

    def _calculate_ema(self, prices: pd.Series, period: int) -> float | None:
        """Calculate Exponential Moving Average."""
        if len(prices) < period:
            return None

        ema = prices.ewm(span=period, min_periods=period).mean()
        latest = ema.iloc[-1]

        if pd.isna(latest):
            return None
        return round(float(latest), 6)

    @staticmethod
    def interpret_rsi(rsi: float | None) -> str:
        """Interpret RSI value."""
        if rsi is None:
            return "unknown"
        if rsi < settings.rsi_oversold:
            return "oversold"
        if rsi > settings.rsi_overbought:
            return "overbought"
        return "neutral"

    @staticmethod
    def interpret_macd(
        macd_line: float | None,
        macd_signal: float | None,
        prev_macd_line: float | None = None,
        prev_macd_signal: float | None = None,
    ) -> str:
        """Interpret MACD values."""
        if macd_line is None or macd_signal is None:
            return "unknown"

        # Check for crossovers if previous values available
        if prev_macd_line is not None and prev_macd_signal is not None:
            # Bullish crossover: MACD crosses above signal
            if prev_macd_line <= prev_macd_signal and macd_line > macd_signal:
                return "bullish_cross"
            # Bearish crossover: MACD crosses below signal
            if prev_macd_line >= prev_macd_signal and macd_line < macd_signal:
                return "bearish_cross"

        # Basic interpretation
        if macd_line > macd_signal:
            return "bullish"
        if macd_line < macd_signal:
            return "bearish"
        return "neutral"

    @staticmethod
    def interpret_ma_trend(
        price: float | None,
        sma_10: float | None,
        sma_20: float | None,
        sma_50: float | None,
    ) -> str:
        """Interpret moving average trend."""
        if price is None:
            return "unknown"

        above_count = 0
        below_count = 0
        total = 0

        for ma in [sma_10, sma_20, sma_50]:
            if ma is not None:
                total += 1
                if price > ma:
                    above_count += 1
                else:
                    below_count += 1

        if total == 0:
            return "unknown"

        if above_count == total:
            return "strong_bullish"
        if below_count == total:
            return "strong_bearish"
        if above_count > below_count:
            return "bullish"
        if below_count > above_count:
            return "bearish"
        return "neutral"

    @staticmethod
    def detect_golden_death_cross(
        sma_50: float | None,
        sma_20: float | None,
        prev_sma_50: float | None = None,
        prev_sma_20: float | None = None,
    ) -> str | None:
        """Detect golden cross or death cross.

        Golden Cross: Short-term MA crosses above long-term MA (bullish)
        Death Cross: Short-term MA crosses below long-term MA (bearish)
        """
        if sma_50 is None or sma_20 is None:
            return None

        if prev_sma_50 is not None and prev_sma_20 is not None:
            # Golden Cross
            if prev_sma_20 <= prev_sma_50 and sma_20 > sma_50:
                return "golden_cross"
            # Death Cross
            if prev_sma_20 >= prev_sma_50 and sma_20 < sma_50:
                return "death_cross"

        return None
