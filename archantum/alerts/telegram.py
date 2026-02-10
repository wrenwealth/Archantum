"""Telegram notifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from telegram import Bot
from telegram.error import TelegramError
from rich.console import Console

from archantum.config import settings
from archantum.db import Database
from archantum.analysis.arbitrage import (
    ArbitrageOpportunity,
    GuaranteedProfit,
    OpportunityReason,
    REASON_EXPLANATIONS,
)
from archantum.analysis.price import PriceMovement
from archantum.analysis.trends import TrendSignal
from archantum.analysis.whale import WhaleActivity
from archantum.analysis.new_market import NewMarket
from archantum.analysis.resolution import ResolutionAlert
from archantum.analysis.accuracy import AccuracyTracker
from archantum.analysis.smartmoney import SmartMoneyAlert
from archantum.analysis.confluence import ConfluenceSignal
from archantum.analysis.cross_platform import CrossPlatformArbitrage
from archantum.analysis.lp_rewards import LPOpportunity, LPSimulation
from archantum.analysis.liquidity import LiquidityAdjustedArbitrage
from archantum.analysis.risk_score import ExecutionRiskScore
from archantum.analysis.multi_outcome import MultiOutcomeArbitrage, MultiOutcomeTier
from archantum.analysis.dependency import DependencyArbitrage
from archantum.analysis.settlement import SettlementLagOpportunity
from archantum.analysis.certain_outcome import CertainOutcomeOpportunity, CertainOutcomeTier
from archantum.analysis.esports import EsportsOpportunity, EsportsGame, EsportsDetectionType, EsportsTier
from archantum.data.validator import ValidationResult


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

    def format_arbitrage_alert(
        self,
        opp: ArbitrageOpportunity,
        enrichment: LiquidityAdjustedArbitrage | None = None,
        risk: ExecutionRiskScore | None = None,
        guaranteed_profit: GuaranteedProfit | None = None,
        reason: OpportunityReason | None = None,
    ) -> AlertMessage:
        """Format an arbitrage opportunity as an alert with tiered formatting."""
        from archantum.analysis.arbitrage import ArbitrageTier

        link = opp.polymarket_url or "N/A"

        # Tiered formatting
        if opp.tier == ArbitrageTier.ALPHA:
            emoji = "🚨🚨🚨"
            title = "ALPHA DETECTED"
            warning = "\n\n⚠️ <b>VERIFY MARKET LEGITIMACY BEFORE TRADING</b> — gaps this large may indicate market error or low liquidity"
        elif opp.tier == ArbitrageTier.HIGH_VALUE:
            emoji = "🔥🔥"
            title = "HIGH VALUE ARBITRAGE"
            warning = ""
        else:
            emoji = "🔥"
            title = "ARBITRAGE DETECTED"
            warning = ""

        # Alpha capture badge
        if guaranteed_profit and guaranteed_profit.capture_ratio >= 0.90:
            emoji = "⚡ " + emoji
            title = "ALPHA CAPTURE — " + title

        # Calculate profits
        profit_100 = opp.calculate_profit(100)
        profit_500 = opp.calculate_profit(500)
        profit_1000 = opp.calculate_profit(1000)

        # Format prices in cents for clarity
        yes_cents = int(opp.yes_price * 100)
        no_cents = int(opp.no_price * 100)
        total_cents = int(opp.total_price * 100)
        profit_cents = 100 - total_cents

        # Resolution timing info
        resolution_text = ""
        if opp.days_until_resolution is not None:
            days = opp.days_until_resolution
            if days < 1:
                hours = days * 24
                time_str = f"{hours:.1f} hours"
                time_emoji = "⚡"  # Very soon
            elif days < 7:
                time_str = f"{days:.1f} days"
                time_emoji = "🔥"  # Soon
            elif days < 30:
                time_str = f"{days:.0f} days"
                time_emoji = "📅"
            else:
                time_str = f"{days/30:.1f} months"
                time_emoji = "📆"

            annual_return = opp.annualized_return_pct
            apy_text = f" ({annual_return:.0f}% APY)" if annual_return and annual_return < 10000 else ""

            resolution_text = f"\n{time_emoji} <b>Resolves in:</b> {time_str}{apy_text}"

        # Liquidity enrichment section
        liquidity_text = ""
        if enrichment:
            max_pos = enrichment.max_position_usd
            combined_depth = enrichment.combined_depth_usd
            yes_slip = enrichment.yes_liquidity.slippage_pct_1000
            no_slip = enrichment.no_liquidity.slippage_pct_1000

            liquidity_text = f"""

<b>Liquidity:</b>
  Orderbook depth: ${combined_depth:,.0f}
  Max position: ${max_pos:,.0f}
  Slippage @$1000: YES {yes_slip:.1f}% / NO {no_slip:.1f}%"""

            if enrichment.slippage_adjusted_profit_1000 is not None:
                liquidity_text += f"\n  Slippage-adjusted profit @$1000: ${enrichment.slippage_adjusted_profit_1000:.2f}"

        # Risk score section
        risk_text = ""
        if risk:
            risk_text = f"\n\n<b>Execution Score:</b> {risk.total_score:.1f}/10 — {risk.confidence} Confidence"

        # Guaranteed profit section
        gp_text = ""
        if guaranteed_profit:
            conf_emoji = "✅" if guaranteed_profit.confidence == "HIGH" else "🟡" if guaranteed_profit.confidence == "MEDIUM" else "🔴"
            gp_text = f"""

<b>Guaranteed Profit:</b> {guaranteed_profit.guaranteed_profit_cents:.1f}¢/share ({guaranteed_profit.capture_ratio:.0%} of theoretical)
Fees: ~{guaranteed_profit.estimated_fees_cents:.1f}¢ | Slippage: ~{guaranteed_profit.estimated_slippage_cents:.1f}¢
Confidence: {guaranteed_profit.confidence} {conf_emoji}"""

        # Reason explanation
        reason_text = ""
        if reason:
            reason_text = f"\n\n💡 <i>{REASON_EXPLANATIONS[reason]}</i>"

        message = f"""{emoji} <b>{title}</b>

<b>Market:</b> {opp.question[:100]}{'...' if len(opp.question) > 100 else ''}

<b>Yes:</b> {yes_cents}¢ + <b>No:</b> {no_cents}¢ = <b>{total_cents}¢</b>
<b>Profit:</b> {profit_cents}¢/share (guaranteed){resolution_text}

<b>Estimated Returns:</b>
  $100 → ${profit_100:.2f} profit
  $500 → ${profit_500:.2f} profit
  $1000 → ${profit_1000:.2f} profit{gp_text}{liquidity_text}{risk_text}{reason_text}{warning}

📊 <a href="{link}">View on Polymarket</a>"""

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

📊 <a href="{link}">View on Polymarket</a>"""

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

📊 <a href="{link}">View on Polymarket</a>"""

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

📊 <a href="{link}">View on Polymarket</a>"""

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

📊 <a href="{link}">View on Polymarket</a>"""

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

📊 <a href="{link}">View on Polymarket</a>"""

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
        market_link = alert.polymarket_url or "N/A"
        profile_link = f"https://polymarket.com/profile/{alert.wallet_address}"

        # Format PnL
        pnl_str = f"${alert.wallet_pnl:,.0f}"
        if alert.wallet_pnl >= 100000:
            pnl_str = f"${alert.wallet_pnl/1000:.0f}K"
        if alert.wallet_pnl >= 1000000:
            pnl_str = f"${alert.wallet_pnl/1000000:.1f}M"

        rank_str = f"#{alert.wallet_rank}" if alert.wallet_rank else "Unranked"

        message = f"""{emoji} <b>SMART MONEY ALERT</b>

<b>Trader:</b> <a href='{profile_link}'>{alert.username}</a> ({rank_str})
<b>PnL:</b> {pnl_str}
<b>Wallet:</b> <code>{alert.wallet_address}</code>

{side_emoji} <b>{alert.side}</b> {alert.outcome} @ ${alert.price:.2f}
<b>Size:</b> ${alert.usdc_size:,.0f}

<b>Market:</b> {alert.market_title[:100]}{'...' if len(alert.market_title) > 100 else ''}

📊 <a href="{market_link}">View on Polymarket</a>"""

        return AlertMessage(
            market_id=alert.event_slug or "smart_money",
            alert_type="smart_money",
            message=message,
            details=alert.to_dict(),
        )

    def format_confluence_alert(self, signal: ConfluenceSignal) -> AlertMessage:
        """Format a confluence signal as an alert."""
        # Choose emoji based on signal
        signal_emojis = {
            "strong_buy": "🟢🟢",
            "buy": "🟢",
            "neutral": "⚪",
            "sell": "🔴",
            "strong_sell": "🔴🔴",
        }
        emoji = signal_emojis.get(signal.signal, "📊")

        # Format individual indicators
        rsi_text = f"RSI(14): {signal.rsi_value:.1f}" if signal.rsi_value else "RSI(14): N/A"
        if signal.rsi_signal == "oversold":
            rsi_text += " - OVERSOLD"
        elif signal.rsi_signal == "overbought":
            rsi_text += " - OVERBOUGHT"

        macd_text = signal.macd_signal.upper().replace("_", " ")
        ma_text = signal.ma_trend.upper().replace("_", " ")

        cross_text = ""
        if signal.cross_signal:
            cross_text = f"\nMA Cross: {signal.cross_signal.upper().replace('_', ' ')}"

        link = signal.polymarket_url or "N/A"

        message = f"""{emoji} <b>CONFLUENCE SIGNAL: {signal.signal.upper().replace('_', ' ')}</b>

<b>Market:</b> {signal.question[:100]}{'...' if len(signal.question) > 100 else ''}
<b>Current Price:</b> ${signal.current_price:.2f}
<b>Confluence Score:</b> {signal.confluence_score:.0f}/100

<b>Indicators:</b>
  {rsi_text}
  MACD: {macd_text}
  MA Trend: {ma_text}{cross_text}

📊 <a href="{link}">View on Polymarket</a>"""

        return AlertMessage(
            market_id=signal.market_id,
            alert_type="confluence",
            message=message,
            details=signal.to_dict(),
        )

    def format_price_discrepancy_alert(self, validation: ValidationResult) -> AlertMessage:
        """Format a price discrepancy alert."""
        emoji = "⚠️"
        if validation.potential_arbitrage:
            emoji = "🚨"

        ws_yes = f"${validation.websocket_yes:.2f}" if validation.websocket_yes else "N/A"
        ws_no = f"${validation.websocket_no:.2f}" if validation.websocket_no else "N/A"
        rest_yes = f"${validation.rest_yes:.2f}" if validation.rest_yes else "N/A"
        rest_no = f"${validation.rest_no:.2f}" if validation.rest_no else "N/A"

        arbitrage_text = "Yes" if validation.potential_arbitrage else "No"
        link = validation.polymarket_url or "N/A"

        message = f"""{emoji} <b>PRICE DISCREPANCY DETECTED</b>

<b>Market:</b> {validation.question[:100]}{'...' if len(validation.question) > 100 else ''}

<b>WebSocket:</b> Yes {ws_yes} / No {ws_no}
<b>REST API:</b>  Yes {rest_yes} / No {rest_no}

<b>Discrepancy:</b> {validation.max_diff_pct:.1f}%
<b>Potential Arbitrage:</b> {arbitrage_text}

📊 <a href="{link}">View on Polymarket</a>"""

        return AlertMessage(
            market_id=validation.market_id,
            alert_type="price_discrepancy",
            message=message,
            details=validation.to_dict(),
        )

    def format_data_source_alert(
        self,
        source: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> AlertMessage:
        """Format a data source status alert."""
        status_emojis = {
            "connected": "🟢",
            "disconnected": "🟡",
            "failed": "🔴",
            "degraded": "🟠",
        }
        emoji = status_emojis.get(status, "⚪")

        details_text = ""
        if details:
            if "reason" in details:
                details_text = f"\n<b>Reason:</b> {details['reason']}"
            if "reliability" in details:
                details_text += f"\n<b>Reliability:</b> {details['reliability']:.1f}%"

        message = f"""{emoji} <b>DATA SOURCE {status.upper()}</b>

<b>Source:</b> {source}
<b>Status:</b> {status}{details_text}"""

        return AlertMessage(
            market_id="system",
            alert_type="data_source",
            message=message,
            details={"source": source, "status": status, **(details or {})},
        )

    def format_lp_opportunity_alert(self, opp: LPOpportunity) -> AlertMessage:
        """Format an LP opportunity as an alert."""
        # Tier based on APY
        if opp.estimated_apy >= 100:
            emoji = "💰💰💰"
            title = "HIGH YIELD LP OPPORTUNITY"
        elif opp.estimated_apy >= 50:
            emoji = "💰💰"
            title = "LP OPPORTUNITY"
        else:
            emoji = "💰"
            title = "LP OPPORTUNITY"

        # Competition indicator
        if opp.competition_score <= 30:
            comp_text = "🟢 Low"
        elif opp.competition_score <= 60:
            comp_text = "🟡 Medium"
        else:
            comp_text = "🔴 High"

        # Two-sided requirement
        sided_text = "⚠️ Two-sided required" if opp.requires_two_sided else "✅ Single-sided OK"

        midpoint_cents = int(opp.midpoint * 100)
        link = opp.polymarket_url or "N/A"

        # Calculate earnings for different capital amounts
        # APY is based on $1000 position, so we scale accordingly
        base_daily = opp.estimated_daily_reward  # This is for ~$1000 position
        daily_500 = base_daily * 0.5
        daily_1000 = base_daily
        daily_5000 = base_daily * 5

        message = f"""{emoji} <b>{title}</b>

<b>Market:</b> {opp.question[:100]}{'...' if len(opp.question) > 100 else ''}

<b>Midpoint:</b> {midpoint_cents}¢
<b>Max Spread:</b> ±{opp.max_spread_cents:.1f}¢

<b>Estimasi Penghasilan (per hari):</b>
  Modal $500 → ~${daily_500:.2f}/hari
  Modal $1000 → ~${daily_1000:.2f}/hari
  Modal $5000 → ~${daily_5000:.2f}/hari

<b>APY:</b> ~{opp.estimated_apy:.0f}% (berdasarkan modal $1000)

<b>Competition:</b> {comp_text}
<b>Requirement:</b> {sided_text}

<b>Cara LP:</b>
  1. Pasang bid di {midpoint_cents - opp.recommended_spread:.0f}¢
  2. Pasang ask di {midpoint_cents + opp.recommended_spread:.0f}¢
  3. Spread: ±{opp.recommended_spread:.1f}¢ dari midpoint

📊 <a href="{link}">View on Polymarket</a>"""

        return AlertMessage(
            market_id=opp.market_id,
            alert_type="lp_opportunity",
            message=message,
            details=opp.to_dict(),
        )

    def format_cross_platform_alert(self, opp: CrossPlatformArbitrage) -> AlertMessage:
        """Format a cross-platform arbitrage opportunity as an alert."""
        from archantum.analysis.cross_platform import CrossPlatformTier

        # Tiered formatting
        if opp.tier == CrossPlatformTier.ALPHA:
            emoji = "🌐🚨🚨"
            title = "CROSS-PLATFORM ALPHA"
            warning = "\n\n⚠️ <b>HIGH SPREAD - verify market match accuracy before trading</b>"
        elif opp.tier == CrossPlatformTier.HIGH_VALUE:
            emoji = "🌐🔥🔥"
            title = "CROSS-PLATFORM HIGH VALUE"
            warning = ""
        else:
            emoji = "🌐🔥"
            title = "CROSS-PLATFORM ARBITRAGE"
            warning = ""

        # Format prices
        poly_yes_cents = int(opp.poly_yes_price * 100)
        poly_no_cents = int(opp.poly_no_price * 100)
        kalshi_yes_cents = int(opp.kalshi_yes_price * 100)
        kalshi_no_cents = int(opp.kalshi_no_price * 100)

        buy_price_cents = int(opp.buy_price * 100)
        sell_price_cents = int(opp.sell_price * 100)

        # Calculate profits
        profit_100 = opp.calculate_profit(100)
        profit_500 = opp.calculate_profit(500)
        profit_1000 = opp.calculate_profit(1000)

        # Match confidence indicator
        if opp.match_score >= 0.9:
            match_indicator = "✅ High"
        elif opp.match_score >= 0.8:
            match_indicator = "🟡 Medium"
        else:
            match_indicator = "⚠️ Low"

        message = f"""{emoji} <b>{title}</b>

<b>Polymarket:</b> {opp.polymarket_question[:80]}{'...' if len(opp.polymarket_question) > 80 else ''}
Yes: {poly_yes_cents}¢ / No: {poly_no_cents}¢

<b>Kalshi:</b> {opp.kalshi_title[:80]}{'...' if len(opp.kalshi_title) > 80 else ''}
Yes: {kalshi_yes_cents}¢ / No: {kalshi_no_cents}¢

<b>Strategy:</b>
📥 BUY {opp.buy_side.upper()} on {opp.buy_platform.title()} @ {buy_price_cents}¢
📤 SELL {opp.sell_side.upper()} on {opp.sell_platform.title()} @ {sell_price_cents}¢

<b>Spread:</b> {opp.spread_pct:.1f}% (after ~3% fees)
<b>Match Confidence:</b> {match_indicator} ({opp.match_score:.0%})

<b>Est. Profit (net of fees):</b>
  $100 → ${profit_100:.2f}
  $500 → ${profit_500:.2f}
  $1000 → ${profit_1000:.2f}{warning}

<b>Links:</b>
• <a href="{opp.polymarket_url}">Polymarket</a>
• <a href="{opp.kalshi_url}">Kalshi</a>"""

        return AlertMessage(
            market_id=f"cross_{opp.polymarket_id}_{opp.kalshi_ticker}",
            alert_type="cross_platform",
            message=message,
            details=opp.to_dict(),
        )

    def format_multi_outcome_alert(
        self,
        opp: MultiOutcomeArbitrage,
        reason: OpportunityReason | None = None,
    ) -> AlertMessage:
        """Format a multi-outcome arbitrage opportunity as an alert."""
        if reason is None:
            reason = OpportunityReason.MULTI_OUTCOME_MISPRICING

        if opp.tier == MultiOutcomeTier.ALPHA:
            emoji = "🎯🚨🚨"
            title = "MULTI-OUTCOME ALPHA"
        elif opp.tier == MultiOutcomeTier.HIGH_VALUE:
            emoji = "🎯🔥🔥"
            title = "MULTI-OUTCOME HIGH VALUE"
        else:
            emoji = "🎯"
            title = "MULTI-OUTCOME ARBITRAGE"

        total_pct = opp.total_probability * 100
        strategy = "Buy all outcomes" if opp.strategy == "buy_all" else "Sell all outcomes"

        profit_1000 = opp.calculate_profit(1000)

        # Resolution timing
        resolves_text = ""
        if opp.end_date:
            from datetime import datetime
            hours = (opp.end_date - datetime.utcnow()).total_seconds() / 3600
            if hours < 1:
                time_str = f"{max(int(hours * 60), 1)} minutes"
                time_emoji = "⚡"
            elif hours < 24:
                time_str = f"{hours:.1f} hours"
                time_emoji = "🔥"
            else:
                time_str = f"{hours / 24:.1f} days"
                time_emoji = "📅"
            resolves_text = f"\n{time_emoji} <b>Resolves in:</b> {time_str}"

        # Build outcome list
        outcome_lines = ""
        for o in opp.outcomes[:10]:  # Cap at 10 to avoid huge messages
            price_cents = int(o.yes_price * 100)
            outcome_lines += f"\n  • {o.question[:60]}{'...' if len(o.question) > 60 else ''}: {price_cents}¢"

        # Deviation history
        deviation_text = ""
        if opp.deviation_multiplier is not None and opp.deviation_multiplier > 1.5:
            deviation_text = f"\n\n📊 <b>Deviation is {opp.deviation_multiplier:.1f}x historical average</b> (7-day avg: {opp.historical_avg_deviation:.1f}%)"

        link = opp.outcomes[0].polymarket_url if opp.outcomes else "N/A"

        reason_text = f"\n\n💡 <i>{REASON_EXPLANATIONS[reason]}</i>"

        message = f"""{emoji} <b>{title}</b>

<b>Event:</b> {opp.event_name[:100]}{'...' if len(opp.event_name) > 100 else ''}{resolves_text}
<b>Outcomes:</b> {opp.outcome_count}
<b>Total probability:</b> {total_pct:.1f}%
<b>Gap:</b> {opp.gap_pct:.1f}%
<b>Strategy:</b> {strategy}

<b>Outcomes:</b>{outcome_lines}

<b>Profit:</b> {opp.gap_pct:.1f}¢ per $1 | $1000 → ${profit_1000:.2f}{deviation_text}{reason_text}

📊 <a href="{link}">View on Polymarket</a>"""

        return AlertMessage(
            market_id=f"multi_{opp.event_slug}",
            alert_type="multi_outcome",
            message=message,
            details=opp.to_dict(),
        )

    def format_dependency_alert(
        self,
        dep: DependencyArbitrage,
        reason: OpportunityReason | None = None,
    ) -> AlertMessage:
        """Format a dependency-based arbitrage alert."""
        import html as html_lib

        if reason is None:
            reason = OpportunityReason.DEPENDENCY_VIOLATION

        emoji = "🔗"

        dep_type_labels = {
            "time_based": "Time-Based",
            "subset": "Subset/Threshold",
            "mutually_exclusive": "Mutually Exclusive",
        }
        dep_label = dep_type_labels.get(dep.dependency_type.value, dep.dependency_type.value)

        price_a_cents = int(dep.market_a_yes_price * 100)
        price_b_cents = int(dep.market_b_yes_price * 100)

        link_a = dep.market_a_url or "N/A"
        link_b = dep.market_b_url or "N/A"

        # Escape violation text to avoid HTML parse errors (< > & chars)
        violation_safe = html_lib.escape(dep.violation)

        reason_text = f"\n\n💡 <i>{REASON_EXPLANATIONS[reason]}</i>"

        message = f"""{emoji} <b>DEPENDENCY ARBITRAGE DETECTED</b>

<b>Market A:</b> {dep.market_a_question[:80]}{'...' if len(dep.market_a_question) > 80 else ''}
Yes: {price_a_cents}¢

<b>Market B:</b> {dep.market_b_question[:80]}{'...' if len(dep.market_b_question) > 80 else ''}
Yes: {price_b_cents}¢

<b>Dependency:</b> {dep_label}
<b>Violation:</b> {violation_safe}
<b>Estimated profit:</b> {dep.estimated_profit_pct:.1f}%{reason_text}

<b>Links:</b>
• <a href="{link_a}">📊 Market A</a>
• <a href="{link_b}">📊 Market B</a>"""

        return AlertMessage(
            market_id=f"dep_{dep.market_a_id}_{dep.market_b_id}",
            alert_type="dependency",
            message=message,
            details=dep.to_dict(),
        )

    def format_settlement_lag_alert(self, opp: SettlementLagOpportunity) -> AlertMessage:
        """Format a settlement lag opportunity as an alert."""
        from datetime import datetime

        price_cents = int(opp.current_yes_price * 100)
        expected_cents = int(opp.expected_settlement * 100)
        profit_cents = opp.potential_profit_cents

        # Strategy description
        if opp.expected_settlement == 0.0:
            strategy = f"Buy NO at {100 - price_cents}¢, receive 100¢ at settlement"
            settle_label = "NO"
        else:
            strategy = f"Buy YES at {price_cents}¢, receive 100¢ at settlement"
            settle_label = "YES"

        # Resolution timing
        resolves_text = ""
        if opp.end_date:
            hours = (opp.end_date - datetime.utcnow()).total_seconds() / 3600
            if hours < 1:
                time_str = f"{max(int(hours * 60), 1)} minutes"
                time_emoji = "⚡"
            elif hours < 24:
                time_str = f"{hours:.1f} hours"
                time_emoji = "🔥"
            else:
                time_str = f"{hours / 24:.1f} days"
                time_emoji = "📅"
            resolves_text = f"\n{time_emoji} <b>Resolves in:</b> {time_str}"

        # Estimated returns
        profit_100 = (profit_cents / 100) * 100  # on $100
        profit_500 = (profit_cents / 100) * 500

        volume_text = ""
        if opp.volume_24hr is not None:
            volume_text = f"\n<b>24h Volume:</b> ${opp.volume_24hr:,.0f}"

        link = opp.polymarket_url or "N/A"

        message = f"""⏳ <b>SETTLEMENT LAG DETECTED</b>

<b>Market:</b> {opp.question[:100]}{'...' if len(opp.question) > 100 else ''}

<b>Current:</b> {price_cents}¢ → <b>Expected:</b> {expected_cents}¢
<b>Profit if settles to {settle_label}:</b> {profit_cents:.1f}¢/share
<b>Strategy:</b> {strategy}{resolves_text}

<b>Estimated Returns:</b>
  $100 → ${profit_100:.2f} profit
  $500 → ${profit_500:.2f} profit{volume_text}

💡 <i>{REASON_EXPLANATIONS[OpportunityReason.SETTLEMENT_LAG]}</i>

📊 <a href="{link}">View on Polymarket</a>"""

        return AlertMessage(
            market_id=opp.market_id,
            alert_type="settlement_lag",
            message=message,
            details=opp.to_dict(),
        )

    def format_certain_outcome_alert(self, opp: CertainOutcomeOpportunity) -> AlertMessage:
        """Format a certain outcome opportunity as an alert."""
        if opp.tier == CertainOutcomeTier.VERIFIED:
            emoji = "🎯✅✅"
            title = "AI-VERIFIED CERTAIN OUTCOME"
        else:
            emoji = "🎯✅"
            title = "HIGH CONFIDENCE OUTCOME"

        buy_price_cents = int(opp.buy_price * 100)
        profit_cents = opp.profit_per_share_cents

        # Time until resolution
        hours = opp.hours_until_resolution
        if hours < 1:
            time_str = f"{int(hours * 60)} minutes"
        elif hours < 24:
            time_str = f"{hours:.1f} hours"
        else:
            time_str = f"{hours / 24:.1f} days"

        # AI verification section
        ai = opp.ai_result
        if ai.error:
            ai_section = "\n<b>AI Verification:</b> Unavailable (signal-only)"
        else:
            det_emoji = "✅" if ai.determined else "❌"
            ai_section = f"""
<b>AI Verification:</b>
  Determined: {det_emoji} {"Yes" if ai.determined else "No"}
  Confidence: {ai.confidence:.0%}
  {ai.reasoning}"""

        # Signal scores
        sig = opp.signal_score
        signal_section = f"""
<b>Signal Scores:</b>
  Stability: {sig.stability_score:.0%}
  Volume: {sig.volume_score:.0%}
  Cross-platform: {sig.cross_platform_score:.0%}"""

        # Estimated returns
        profit_100 = opp.calculate_profit(100)
        profit_500 = opp.calculate_profit(500)
        profit_1000 = opp.calculate_profit(1000)

        link = opp.polymarket_url or "N/A"

        end_date_text = ""
        if opp.end_date:
            end_date_text = f"\n<b>End Date:</b> {opp.end_date.strftime('%Y-%m-%d %H:%M UTC')}"

        message = f"""{emoji} <b>{title}</b>

<b>Market:</b> {opp.question[:100]}{'...' if len(opp.question) > 100 else ''}

📥 <b>BUY {opp.buy_side}</b> @ {buy_price_cents}¢
💰 <b>Profit:</b> {profit_cents:.1f}¢/share on resolution
⏰ <b>Resolves in:</b> {time_str}{end_date_text}
📊 <b>Combined Score:</b> {opp.combined_score:.0%}
{ai_section}
{signal_section}

<b>Estimated Returns:</b>
  $100 → ${profit_100:.2f} profit
  $500 → ${profit_500:.2f} profit
  $1000 → ${profit_1000:.2f} profit

📊 <a href="{link}">View on Polymarket</a>"""

        return AlertMessage(
            market_id=opp.market_id,
            alert_type="certain_outcome",
            message=message,
            details=opp.to_dict(),
        )

    def format_esports_alert(self, opp: EsportsOpportunity) -> AlertMessage:
        """Format an esports arbitrage opportunity as an alert."""
        # Game emoji
        game_emojis = {
            EsportsGame.VALORANT: "🎯",
            EsportsGame.COUNTER_STRIKE: "💣",
            EsportsGame.UNKNOWN: "🎮",
        }
        game_emoji = game_emojis.get(opp.game, "🎮")

        game_labels = {
            EsportsGame.VALORANT: "Valorant",
            EsportsGame.COUNTER_STRIKE: "Counter-Strike",
            EsportsGame.UNKNOWN: "Esports",
        }
        game_label = game_labels.get(opp.game, "Esports")

        # Tier formatting
        if opp.tier == EsportsTier.ALPHA:
            tier_emoji = "🚨🚨🚨"
            tier_label = "ALPHA"
        elif opp.tier == EsportsTier.HIGH_VALUE:
            tier_emoji = "🔥🔥"
            tier_label = "HIGH VALUE"
        else:
            tier_emoji = "🔥"
            tier_label = "STANDARD"

        # Detection type label
        detection_labels = {
            EsportsDetectionType.MATCH_WINNER: "MATCH WINNER",
            EsportsDetectionType.TOURNAMENT_WINNER: "TOURNAMENT",
            EsportsDetectionType.MAP_VS_MATCH: "MAP vs MATCH",
        }
        detection_label = detection_labels.get(opp.detection_type, "ESPORTS")

        # Profit estimates
        profit_100 = opp.calculate_profit(100)
        profit_500 = opp.calculate_profit(500)
        profit_1000 = opp.calculate_profit(1000)

        # Tournament/teams info
        context_text = ""
        if opp.tournament:
            context_text += f"\n<b>Tournament:</b> {opp.tournament}"
        if opp.teams:
            context_text += f"\n<b>Teams:</b> {', '.join(opp.teams)}"

        # Multi-outcome info
        multi_text = ""
        if opp.detection_type == EsportsDetectionType.TOURNAMENT_WINNER and opp.outcome_count > 0:
            multi_text = f"\n<b>Outcomes:</b> {opp.outcome_count} | <b>Total probability:</b> {opp.total_probability*100:.1f}%"

        link = opp.polymarket_url or "N/A"

        message = f"""{game_emoji} {tier_emoji} <b>ESPORTS {tier_label} — {detection_label}</b>

<b>Game:</b> {game_label}
<b>Market:</b> {opp.question[:100]}{'...' if len(opp.question) > 100 else ''}
<b>Edge:</b> {opp.edge_pct:.1f}%
<b>Detail:</b> {opp.description}{context_text}{multi_text}

<b>Estimated Returns:</b>
  $100 → ${profit_100:.2f} profit
  $500 → ${profit_500:.2f} profit
  $1000 → ${profit_1000:.2f} profit

📊 <a href="{link}">View on Polymarket</a>"""

        return AlertMessage(
            market_id=f"esports_{opp.market_id}",
            alert_type="esports",
            message=message,
            details=opp.to_dict(),
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
