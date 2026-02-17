"""Accuracy tracking for alert profitability evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from archantum.db import Database


@dataclass
class AccuracyResult:
    """Result of evaluating an alert's accuracy."""

    alert_id: int
    market_id: str
    alert_type: str
    signal_price_yes: float | None
    signal_price_no: float | None
    outcome_price_yes: float | None
    outcome_price_no: float | None
    profitable: bool
    profit_pct: float
    evaluation_type: str

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "alert_id": self.alert_id,
            "market_id": self.market_id,
            "alert_type": self.alert_type,
            "signal_price_yes": self.signal_price_yes,
            "signal_price_no": self.signal_price_no,
            "outcome_price_yes": self.outcome_price_yes,
            "outcome_price_no": self.outcome_price_no,
            "profitable": self.profitable,
            "profit_pct": self.profit_pct,
            "evaluation_type": self.evaluation_type,
        }


class AccuracyTracker:
    """Tracks and evaluates alert profitability after 24 hours."""

    # Profitability thresholds by alert type
    THRESHOLDS = {
        "arbitrage": {"gap_closed_pct": 50.0},  # Gap closed by >= 50%
        "volume_spike": {"price_move_pct": 5.0},  # Price moved >= 5% either direction
        "price_move": {"continue_pct": 2.0},  # Price continued >= 2% same direction
        "trend": {"bullish_pct": 3.0, "bearish_pct": 3.0},  # >= 3% in signaled direction
    }

    def __init__(self, db: Database):
        self.db = db

    async def record_alert_for_tracking(
        self,
        alert_id: int,
        market_id: str,
        alert_type: str,
        alert_timestamp: datetime,
    ) -> None:
        """Record a new alert for future accuracy evaluation."""
        # Get current price at alert time
        price = await self.db.get_latest_price_snapshot(market_id)

        await self.db.save_alert_outcome(
            alert_id=alert_id,
            market_id=market_id,
            alert_type=alert_type,
            alert_timestamp=alert_timestamp,
            signal_price_yes=price.yes_price if price else None,
            signal_price_no=price.no_price if price else None,
        )

    async def evaluate_pending_alerts(self) -> list[AccuracyResult]:
        """Evaluate all alerts that are 24h+ old and not yet evaluated."""
        results = []

        pending = await self.db.get_pending_alert_outcomes(min_age_hours=24)

        for outcome in pending:
            result = await self._evaluate_alert(outcome)
            if result:
                results.append(result)

        return results

    async def _evaluate_alert(self, outcome) -> AccuracyResult | None:
        """Evaluate a single alert outcome."""
        # Get current price for comparison
        price = await self.db.get_latest_price_snapshot(outcome.market_id)

        if not price:
            return None

        outcome_price_yes = price.yes_price
        outcome_price_no = price.no_price

        # Get alert details for direction info
        alert_data = await self.db.get_alert_with_prices(outcome.alert_id)
        alert_details = {}
        if alert_data:
            alert, _ = alert_data
            if alert.details:
                try:
                    alert_details = json.loads(alert.details)
                except json.JSONDecodeError:
                    pass

        # Calculate profitability based on alert type
        profitable, profit_pct = self._calculate_profitability(
            alert_type=outcome.alert_type,
            signal_yes=outcome.signal_price_yes,
            signal_no=outcome.signal_price_no,
            outcome_yes=outcome_price_yes,
            outcome_no=outcome_price_no,
            alert_details=alert_details,
        )

        # Update the outcome in database
        await self.db.update_alert_outcome(
            outcome_id=outcome.id,
            evaluation_type="24h_check",
            outcome_price_yes=outcome_price_yes,
            outcome_price_no=outcome_price_no,
            profitable=profitable,
            profit_pct=profit_pct,
        )

        return AccuracyResult(
            alert_id=outcome.alert_id,
            market_id=outcome.market_id,
            alert_type=outcome.alert_type,
            signal_price_yes=outcome.signal_price_yes,
            signal_price_no=outcome.signal_price_no,
            outcome_price_yes=outcome_price_yes,
            outcome_price_no=outcome_price_no,
            profitable=profitable,
            profit_pct=profit_pct,
            evaluation_type="24h_check",
        )

    def _calculate_profitability(
        self,
        alert_type: str,
        signal_yes: float | None,
        signal_no: float | None,
        outcome_yes: float | None,
        outcome_no: float | None,
        alert_details: dict[str, Any],
    ) -> tuple[bool, float]:
        """Calculate if an alert was profitable based on alert type logic.

        Returns:
            tuple of (profitable: bool, profit_pct: float)
        """
        if signal_yes is None or outcome_yes is None:
            return False, 0.0

        signal_yes = signal_yes or 0.0
        signal_no = signal_no or 0.0
        outcome_yes = outcome_yes or 0.0
        outcome_no = outcome_no or 0.0

        if alert_type == "arbitrage":
            return self._eval_arbitrage(
                signal_yes, signal_no, outcome_yes, outcome_no, alert_details
            )
        elif alert_type == "volume_spike":
            return self._eval_volume_spike(signal_yes, outcome_yes)
        elif alert_type == "price_move":
            return self._eval_price_move(signal_yes, outcome_yes, alert_details)
        elif alert_type == "trend":
            return self._eval_trend(signal_yes, outcome_yes, alert_details)
        else:
            # Unknown alert type - default evaluation
            price_change = outcome_yes - signal_yes
            profit_pct = (price_change / signal_yes * 100) if signal_yes > 0 else 0.0
            return abs(profit_pct) >= 2.0, profit_pct

    def _eval_arbitrage(
        self,
        signal_yes: float,
        signal_no: float,
        outcome_yes: float,
        outcome_no: float,
        details: dict,
    ) -> tuple[bool, float]:
        """Arbitrage: Profitable if price gap closed by >= 50% within 24h."""
        signal_total = signal_yes + signal_no
        outcome_total = outcome_yes + outcome_no

        signal_gap = abs(1.0 - signal_total)
        outcome_gap = abs(1.0 - outcome_total)

        if signal_gap == 0:
            return False, 0.0

        gap_closed_pct = ((signal_gap - outcome_gap) / signal_gap) * 100

        threshold = self.THRESHOLDS["arbitrage"]["gap_closed_pct"]
        profitable = gap_closed_pct >= threshold

        # Profit is the gap that closed
        profit_pct = gap_closed_pct

        return profitable, profit_pct

    def _eval_volume_spike(
        self,
        signal_yes: float,
        outcome_yes: float,
    ) -> tuple[bool, float]:
        """Volume Spike: Profitable if price moved >= 5% in either direction."""
        if signal_yes == 0:
            return False, 0.0

        price_change_pct = abs((outcome_yes - signal_yes) / signal_yes * 100)

        threshold = self.THRESHOLDS["volume_spike"]["price_move_pct"]
        profitable = price_change_pct >= threshold

        # Profit is the actual price change (absolute)
        profit_pct = price_change_pct

        return profitable, profit_pct

    def _eval_price_move(
        self,
        signal_yes: float,
        outcome_yes: float,
        details: dict,
    ) -> tuple[bool, float]:
        """Price Move: Profitable if price continued in same direction >= 2%."""
        if signal_yes == 0:
            return False, 0.0

        direction = details.get("direction", "up")

        price_change = outcome_yes - signal_yes
        price_change_pct = (price_change / signal_yes) * 100

        threshold = self.THRESHOLDS["price_move"]["continue_pct"]

        if direction == "up":
            profitable = price_change_pct >= threshold
        else:
            profitable = price_change_pct <= -threshold

        return profitable, price_change_pct

    def _eval_trend(
        self,
        signal_yes: float,
        outcome_yes: float,
        details: dict,
    ) -> tuple[bool, float]:
        """Trend: Profitable if price moved >= 3% in signaled direction."""
        if signal_yes == 0:
            return False, 0.0

        signal = details.get("signal", "bullish")

        price_change = outcome_yes - signal_yes
        price_change_pct = (price_change / signal_yes) * 100

        if signal in ("bullish", "reversal_up"):
            threshold = self.THRESHOLDS["trend"]["bullish_pct"]
            profitable = price_change_pct >= threshold
        else:  # bearish, reversal_down
            threshold = self.THRESHOLDS["trend"]["bearish_pct"]
            profitable = price_change_pct <= -threshold

        return profitable, price_change_pct

    async def get_accuracy_summary(self) -> dict[str, Any]:
        """Get accuracy stats for dashboard display."""
        return await self.db.get_accuracy_stats()
