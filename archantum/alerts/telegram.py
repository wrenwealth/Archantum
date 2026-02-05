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
from archantum.analysis.price import PriceMovement
from archantum.analysis.trends import TrendSignal
from archantum.analysis.whale import WhaleActivity
from archantum.analysis.new_market import NewMarket
from archantum.analysis.resolution import ResolutionAlert
from archantum.analysis.accuracy import AccuracyTracker
from archantum.analysis.smartmoney import SmartMoneyAlert


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

    # Alert types that should be tracked for accuracy
    TRACKED_ALERT_TYPES = {'arbitrage', 'volume_spike', 'price_move', 'trend', 'whale'}

    def __init__(self, db: Database):
        self.db = db
        self.bot: Bot | None = None
        self.accuracy_tracker = AccuracyTracker(db)

        if settings.telegram_configured:
            self.bot = Bot(token=settings.telegram_bot_token)
            self.chat_id = settings.telegram_chat_id

    @property
    def telegram_enabled(self) -> bool:
        """Check if Telegram is enabled."""
        return self.bot is not None

    async def send_alert(self, alert: AlertMessage) -> bool:
        """Send an alert via Telegram or console fallback."""
        from datetime import datetime

        # Save to database
        saved_alert = await self.db.save_alert(
            market_id=alert.market_id,
            alert_type=alert.alert_type,
            message=alert.message,
            details=alert.details,
            sent=False,
        )

        # Record for accuracy tracking if applicable
        if alert.alert_type in self.TRACKED_ALERT_TYPES:
            try:
                await self.accuracy_tracker.record_alert_for_tracking(
                    alert_id=saved_alert.id,
                    market_id=alert.market_id,
                    alert_type=alert.alert_type,
                    alert_timestamp=saved_alert.timestamp,
                )
            except Exception as e:
                console.print(f"[yellow]Could not record alert for tracking: {e}[/yellow]")

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
        link = opp.polymarket_url or "N/A"

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

    def format_price_move_alert(self, movement: PriceMovement) -> AlertMessage:
        """Format a price movement as an alert."""
        emoji = "⬆️" if movement.direction == "up" else "⬇️"
        link = movement.polymarket_url or "N/A"

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
        link = signal.polymarket_url or "N/A"

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

    def format_whale_alert(self, whale: WhaleActivity) -> AlertMessage:
        """Format a whale activity as an alert."""
        emoji = "🐋"
        direction_emoji = "🟢" if whale.direction == "buy" else "🔴" if whale.direction == "sell" else "⚪"
        link = whale.polymarket_url or "N/A"

        message = f"""{emoji} <b>WHALE ACTIVITY DETECTED</b>

<b>Market:</b> {whale.question[:100]}...

<b>Estimated Trade:</b> ${whale.estimated_trade_size:,.0f}
<b>Volume Change:</b> +{whale.volume_change_pct:.1f}%
<b>Direction:</b> {direction_emoji} {whale.direction.upper()}

<b>Previous 24h Vol:</b> ${whale.previous_volume:,.0f}
<b>Current 24h Vol:</b> ${whale.current_volume:,.0f}

<b>Link:</b> {link}"""

        return AlertMessage(
            market_id=whale.market_id,
            alert_type="whale",
            message=message,
            details=whale.to_dict(),
        )

    def format_new_market_alert(self, market: NewMarket) -> AlertMessage:
        """Format a new market as an alert."""
        emoji = "🆕"
        link = market.polymarket_url or "N/A"

        # Format prices
        prices_text = ""
        if market.outcomes and market.outcome_prices:
            for outcome, price in zip(market.outcomes, market.outcome_prices):
                prices_text += f"\n  {outcome}: ${float(price):.2f}"

        message = f"""{emoji} <b>NEW INTERESTING MARKET</b>

<b>Question:</b> {market.question[:150]}{'...' if len(market.question) > 150 else ''}

<b>24h Volume:</b> ${market.volume_24hr:,.0f}
<b>Liquidity:</b> ${market.liquidity:,.0f}
<b>Prices:</b>{prices_text}

<b>Link:</b> {link}"""

        return AlertMessage(
            market_id=market.market_id,
            alert_type="new_market",
            message=message,
            details=market.to_dict(),
        )

    def format_resolution_alert(self, resolution: ResolutionAlert) -> AlertMessage:
        """Format a resolution alert."""
        # Choose emoji based on urgency
        hours = resolution.hours_until_resolution
        if hours <= 1:
            emoji = "🔴"
            urgency = "RESOLVING SOON"
        elif hours <= 6:
            emoji = "🟠"
            urgency = "RESOLVING TODAY"
        elif hours <= 24:
            emoji = "🟡"
            urgency = "RESOLVING TOMORROW"
        else:
            emoji = "⏰"
            urgency = "RESOLUTION APPROACHING"

        link = resolution.polymarket_url or "N/A"

        # Format time remaining
        if hours < 1:
            time_str = f"{int(hours * 60)} minutes"
        elif hours < 24:
            time_str = f"{hours:.1f} hours"
        else:
            time_str = f"{hours / 24:.1f} days"

        # Format prices
        prices_text = ""
        if resolution.outcome_prices:
            for i, price in enumerate(resolution.outcome_prices):
                outcome = "Yes" if i == 0 else "No"
                prices_text += f"\n  {outcome}: ${float(price):.2f}"

        message = f"""{emoji} <b>{urgency}</b>

<b>Market:</b> {resolution.question[:120]}{'...' if len(resolution.question) > 120 else ''}

<b>Resolves in:</b> {time_str}
<b>End Date:</b> {resolution.end_date.strftime('%Y-%m-%d %H:%M UTC')}
<b>Current Prices:</b>{prices_text}

<b>24h Volume:</b> ${resolution.volume_24hr:,.0f}

<b>Link:</b> {link}"""

        return AlertMessage(
            market_id=resolution.market_id,
            alert_type="resolution",
            message=message,
            details=resolution.to_dict(),
        )

    def format_smart_money_alert(self, alert: SmartMoneyAlert) -> AlertMessage:
        """Format a smart money trade alert."""
        emoji = "🧠"
        side_emoji = "🟢" if alert.side == "BUY" else "🔴"
        link = alert.polymarket_url or "N/A"

        # Format PnL
        pnl_str = f"${alert.wallet_pnl:,.0f}"
        if alert.wallet_pnl >= 100000:
            pnl_str = f"${alert.wallet_pnl/1000:.0f}K"
        if alert.wallet_pnl >= 1000000:
            pnl_str = f"${alert.wallet_pnl/1000000:.1f}M"

        rank_str = f"#{alert.wallet_rank}" if alert.wallet_rank else "Unranked"

        message = f"""{emoji} <b>SMART MONEY ALERT</b>

<b>Trader:</b> {alert.username} ({rank_str})
<b>PnL:</b> {pnl_str}

{side_emoji} <b>{alert.side}</b> {alert.outcome} @ ${alert.price:.2f}
<b>Size:</b> ${alert.usdc_size:,.0f}

<b>Market:</b> {alert.market_title[:100]}{'...' if len(alert.market_title) > 100 else ''}

<b>Link:</b> {link}"""

        return AlertMessage(
            market_id=alert.event_slug or "smart_money",
            alert_type="smart_money",
            message=message,
            details=alert.to_dict(),
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
