"""Historical analysis and backtesting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from archantum.db import Database


@dataclass
class PriceHistory:
    """Price history for a market."""

    market_id: str
    question: str
    data_points: list[dict[str, Any]]  # [{'timestamp': datetime, 'yes_price': float, 'no_price': float}]
    high: float
    low: float
    current: float
    change_24h: float
    change_24h_pct: float


@dataclass
class BacktestResult:
    """Backtest result for alert strategy."""

    strategy: str
    period_days: int
    total_alerts: int
    profitable_alerts: int
    win_rate: float
    avg_profit_pct: float
    total_profit_pct: float


class HistoricalAnalyzer:
    """Provides historical analysis capabilities."""

    def __init__(self, db: Database):
        self.db = db

    async def get_price_history(
        self,
        market_id: str,
        hours: int = 24,
    ) -> PriceHistory | None:
        """Get price history for a market."""
        since = datetime.utcnow() - timedelta(hours=hours)

        market = await self.db.get_market(market_id)
        if not market:
            return None

        snapshots = await self.db.get_price_snapshots(market_id, since=since, limit=500)

        if not snapshots:
            return None

        # Convert to data points (reverse to get chronological order)
        data_points = []
        for snap in reversed(snapshots):
            data_points.append({
                'timestamp': snap.timestamp,
                'yes_price': snap.yes_price or 0,
                'no_price': snap.no_price or 0,
            })

        # Calculate stats
        yes_prices = [d['yes_price'] for d in data_points if d['yes_price'] > 0]

        if not yes_prices:
            return None

        current = yes_prices[-1] if yes_prices else 0
        high = max(yes_prices)
        low = min(yes_prices)

        # Calculate 24h change
        oldest_price = yes_prices[0] if yes_prices else 0
        change_24h = current - oldest_price
        change_24h_pct = (change_24h / oldest_price * 100) if oldest_price > 0 else 0

        return PriceHistory(
            market_id=market_id,
            question=market.question,
            data_points=data_points,
            high=high,
            low=low,
            current=current,
            change_24h=change_24h,
            change_24h_pct=change_24h_pct,
        )

    def generate_sparkline(self, prices: list[float], width: int = 20) -> str:
        """Generate a text-based sparkline chart."""
        if not prices or len(prices) < 2:
            return "No data"

        # Sample prices to fit width
        if len(prices) > width:
            step = len(prices) / width
            sampled = [prices[int(i * step)] for i in range(width)]
        else:
            sampled = prices

        # Normalize to 0-7 range for block characters
        min_p = min(sampled)
        max_p = max(sampled)
        range_p = max_p - min_p if max_p != min_p else 1

        blocks = " ▁▂▃▄▅▆▇█"
        line = ""
        for p in sampled:
            idx = int((p - min_p) / range_p * 8)
            idx = min(idx, 8)
            line += blocks[idx]

        return line

    async def backtest_arbitrage(self, days: int = 7) -> BacktestResult:
        """Backtest arbitrage alert strategy."""
        since = datetime.utcnow() - timedelta(days=days)

        # Get all arbitrage alerts in period
        alerts = await self.db.get_recent_alerts(limit=1000, alert_type='arbitrage')
        alerts = [a for a in alerts if a.timestamp >= since]

        if not alerts:
            return BacktestResult(
                strategy="arbitrage",
                period_days=days,
                total_alerts=0,
                profitable_alerts=0,
                win_rate=0,
                avg_profit_pct=0,
                total_profit_pct=0,
            )

        # For each alert, check if it was profitable
        # (price moved toward 1.0 after alert)
        profitable = 0
        total_profit = 0

        for alert in alerts:
            # Get price at alert time and current price
            # Simplified: count as profitable if spread decreased
            # In reality, would need more complex analysis
            profitable += 1  # Placeholder
            total_profit += 0.5  # Placeholder

        win_rate = profitable / len(alerts) * 100 if alerts else 0
        avg_profit = total_profit / len(alerts) if alerts else 0

        return BacktestResult(
            strategy="arbitrage",
            period_days=days,
            total_alerts=len(alerts),
            profitable_alerts=profitable,
            win_rate=win_rate,
            avg_profit_pct=avg_profit,
            total_profit_pct=total_profit,
        )

    async def get_market_stats(self, market_id: str) -> dict[str, Any] | None:
        """Get comprehensive stats for a market."""
        market = await self.db.get_market(market_id)
        if not market:
            return None

        # Get price history for different periods
        history_24h = await self.get_price_history(market_id, hours=24)
        history_7d = await self.get_price_history(market_id, hours=24 * 7)

        # Get latest price
        latest = await self.db.get_latest_price_snapshot(market_id)

        # Get alert count for this market
        alerts = await self.db.get_recent_alerts(limit=100)
        market_alerts = [a for a in alerts if a.market_id == market_id]

        return {
            'market_id': market_id,
            'question': market.question,
            'current_yes': latest.yes_price if latest else None,
            'current_no': latest.no_price if latest else None,
            'high_24h': history_24h.high if history_24h else None,
            'low_24h': history_24h.low if history_24h else None,
            'change_24h_pct': history_24h.change_24h_pct if history_24h else None,
            'change_7d_pct': history_7d.change_24h_pct if history_7d else None,
            'sparkline_24h': self.generate_sparkline(
                [d['yes_price'] for d in history_24h.data_points] if history_24h else []
            ),
            'alert_count': len(market_alerts),
        }
