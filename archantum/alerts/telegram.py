"""Telegram notifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from telegram import Bot
from telegram.error import TelegramError
from rich.console import Console

from archantum.config import settings
from archantum.db import Database
from archantum.analysis.arbitrage import ArbitrageOpportunity
from archantum.analysis.volume import VolumeSpike
from archantum.analysis.price import PriceMovement
from archantum.analysis.trends import TrendSignal


console = Console()


@dataclass
class AlertMessage:
    """Represents an alert message to be sent."""

    market_id: str
    alert_type: str
    message: str
    details: dict[str, Any]


class TelegramAlerter:
    """Handles sending alerts via Telegram with console fallback."""

    def __init__(self, db: Database):
        self.db = db
        self.bot: Bot | None = None

        if settings.telegram_configured:
            self.bot = Bot(token=settings.telegram_bot_token)
            self.chat_id = settings.telegram_chat_id

    @property
    def telegram_enabled(self) -> bool:
        """Check if Telegram is enabled."""
        return self.bot is not None

    async def send_alert(self, alert: AlertMessage) -> bool:
        """Send an alert via Telegram or console fallback."""
        # Save to database
        await self.db.save_alert(
            market_id=alert.market_id,
            alert_type=alert.alert_type,
            message=alert.message,
            details=alert.details,
            sent=False,
        )

        # Try Telegram
        if self.telegram_enabled:
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=alert.message,
                    parse_mode="HTML",
                )
                # Update alert as sent
                await self.db.save_alert(
                    market_id=alert.market_id,
                    alert_type=alert.alert_type,
                    message=alert.message,
                    details=alert.details,
                    sent=True,
                )
                return True
            except TelegramError as e:
                console.print(f"[red]Telegram error: {e}[/red]")

        # Console fallback
        self._console_alert(alert)
        return False

    def _console_alert(self, alert: AlertMessage):
        """Print alert to console."""
        console.print(f"\n[bold yellow]{'=' * 50}[/bold yellow]")
        console.print(f"[bold]{alert.alert_type.upper()} ALERT[/bold]")
        console.print(alert.message)
        console.print(f"[bold yellow]{'=' * 50}[/bold yellow]\n")

    def format_arbitrage_alert(self, opp: ArbitrageOpportunity) -> AlertMessage:
        """Format an arbitrage opportunity as an alert."""
        emoji = "🚨"
        direction_text = "underpriced" if opp.direction == "under" else "overpriced"
        link = f"https://polymarket.com/event/{opp.slug}" if opp.slug else "N/A"

        message = f"""{emoji} <b>ARBITRAGE OPPORTUNITY</b>

<b>Market:</b> {opp.question[:100]}...

<b>Yes Price:</b> ${opp.yes_price:.4f}
<b>No Price:</b> ${opp.no_price:.4f}
<b>Total:</b> ${opp.total_price:.4f} ({opp.arbitrage_pct:.1f}% gap)

<b>Direction:</b> Market is {direction_text}
<b>Potential profit:</b> {opp.potential_profit_pct:.1f}%

<b>Link:</b> {link}"""

        return AlertMessage(
            market_id=opp.market_id,
            alert_type="arbitrage",
            message=message,
            details=opp.to_dict(),
        )

    def format_volume_alert(self, spike: VolumeSpike) -> AlertMessage:
        """Format a volume spike as an alert."""
        emoji = "📈"
        link = f"https://polymarket.com/event/{spike.slug}" if spike.slug else "N/A"

        message = f"""{emoji} <b>VOLUME SPIKE DETECTED</b>

<b>Market:</b> {spike.question[:100]}...

<b>Current 24h Volume:</b> ${spike.current_volume:,.0f}
<b>Average Volume:</b> ${spike.average_volume:,.0f}
<b>Spike:</b> {spike.spike_multiplier:.1f}x normal

<b>Link:</b> {link}"""

        return AlertMessage(
            market_id=spike.market_id,
            alert_type="volume_spike",
            message=message,
            details=spike.to_dict(),
        )

    def format_price_move_alert(self, movement: PriceMovement) -> AlertMessage:
        """Format a price movement as an alert."""
        emoji = "⬆️" if movement.direction == "up" else "⬇️"
        link = f"https://polymarket.com/event/{movement.slug}" if movement.slug else "N/A"

        message = f"""{emoji} <b>SIGNIFICANT PRICE MOVEMENT</b>

<b>Market:</b> {movement.question[:100]}...

<b>Previous Price:</b> ${movement.previous_yes_price:.4f}
<b>Current Price:</b> ${movement.current_yes_price:.4f}
<b>Change:</b> {movement.price_change_pct:+.1f}%

<b>Time span:</b> {movement.time_span_minutes} minutes
<b>Direction:</b> {movement.direction.upper()}

<b>Link:</b> {link}"""

        return AlertMessage(
            market_id=movement.market_id,
            alert_type="price_move",
            message=message,
            details=movement.to_dict(),
        )

    def format_trend_alert(self, signal: TrendSignal) -> AlertMessage:
        """Format a trend signal as an alert."""
        emoji_map = {
            "bullish": "🟢",
            "bearish": "🔴",
            "reversal_up": "🔄⬆️",
            "reversal_down": "🔄⬇️",
        }
        emoji = emoji_map.get(signal.signal, "📊")
        link = f"https://polymarket.com/event/{signal.slug}" if signal.slug else "N/A"

        ma_text = []
        if signal.ma_1h:
            ma_text.append(f"1h MA: ${signal.ma_1h:.4f}")
        if signal.ma_4h:
            ma_text.append(f"4h MA: ${signal.ma_4h:.4f}")
        if signal.ma_24h:
            ma_text.append(f"24h MA: ${signal.ma_24h:.4f}")

        message = f"""{emoji} <b>TREND SIGNAL: {signal.signal.upper()}</b>

<b>Market:</b> {signal.question[:100]}...

<b>Current Price:</b> ${signal.current_price:.4f}
{chr(10).join(ma_text)}

<b>Momentum:</b> {signal.momentum:+.4f}

<b>Link:</b> {link}"""

        return AlertMessage(
            market_id=signal.market_id,
            alert_type="trend",
            message=message,
            details=signal.to_dict(),
        )

    async def send_test_alert(self) -> bool:
        """Send a test alert to verify configuration."""
        message = """🧪 <b>TEST ALERT</b>

This is a test message from Archantum.
Your Telegram integration is working correctly!

Configuration:
- Bot: Connected ✅
- Chat ID: Verified ✅"""

        alert = AlertMessage(
            market_id="test",
            alert_type="test",
            message=message,
            details={"test": True},
        )

        return await self.send_alert(alert)
