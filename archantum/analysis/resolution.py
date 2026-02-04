"""Resolution alert detection."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Any

from archantum.db import Database
from archantum.api.gamma import GammaMarket


@dataclass
class ResolutionAlert:
    """Represents a market approaching resolution."""

    market_id: str
    question: str
    slug: str | None
    end_date: datetime
    hours_until_resolution: float
    volume_24hr: float
    outcome_prices: list[str]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d['end_date'] = self.end_date.isoformat()
        return d


class ResolutionAnalyzer:
    """Detects markets approaching resolution."""

    def __init__(
        self,
        db: Database,
        alert_hours: list[int] | None = None,
    ):
        """
        Initialize resolution analyzer.

        Args:
            db: Database instance
            alert_hours: Hours before resolution to alert (default: [48, 24, 6, 1])
        """
        self.db = db
        self.alert_hours = alert_hours or [48, 24, 6, 1]
        self._alerted_markets: dict[str, set[int]] = {}  # market_id -> set of hours alerted

    async def analyze(self, markets: list[GammaMarket]) -> list[ResolutionAlert]:
        """
        Detect markets approaching resolution.

        Args:
            markets: List of markets to analyze

        Returns:
            List of resolution alerts
        """
        alerts: list[ResolutionAlert] = []
        now = datetime.utcnow()

        for market in markets:
            if not market.end_date:
                continue

            try:
                # Parse end date
                end_date = datetime.fromisoformat(market.end_date.replace('Z', '+00:00'))
                end_date = end_date.replace(tzinfo=None)  # Make naive for comparison

                # Calculate hours until resolution
                time_diff = end_date - now
                hours_until = time_diff.total_seconds() / 3600

                # Skip if already resolved or too far out
                if hours_until <= 0 or hours_until > max(self.alert_hours) + 1:
                    continue

                # Check which alert threshold we've crossed
                for alert_hour in sorted(self.alert_hours, reverse=True):
                    if hours_until <= alert_hour:
                        # Check if we've already alerted for this threshold
                        if market.id not in self._alerted_markets:
                            self._alerted_markets[market.id] = set()

                        if alert_hour not in self._alerted_markets[market.id]:
                            # New alert needed
                            alert = ResolutionAlert(
                                market_id=market.id,
                                question=market.question,
                                slug=market.slug,
                                end_date=end_date,
                                hours_until_resolution=hours_until,
                                volume_24hr=market.volume_24hr or 0,
                                outcome_prices=market.outcome_prices or [],
                            )
                            alerts.append(alert)
                            self._alerted_markets[market.id].add(alert_hour)
                        break  # Only alert for the nearest threshold

            except (ValueError, TypeError):
                # Invalid date format, skip
                continue

        return alerts

    def reset(self):
        """Reset alerted markets."""
        self._alerted_markets.clear()
