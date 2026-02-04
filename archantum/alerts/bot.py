"""Interactive Telegram bot with commands."""

from __future__ import annotations

import asyncio
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import TelegramError
from rich.console import Console

from archantum.config import settings
from archantum.db import Database
from archantum.api import GammaClient
from archantum.analysis.historical import HistoricalAnalyzer


console = Console()


class TelegramBot:
    """Interactive Telegram bot for Archantum."""

    def __init__(self, db: Database):
        self.db = db
        self.application: Application | None = None
        self.historical = HistoricalAnalyzer(db)

    async def start(self):
        """Start the bot."""
        if not settings.telegram_configured:
            console.print("[yellow]Telegram not configured - bot disabled[/yellow]")
            return

        self.application = (
            Application.builder()
            .token(settings.telegram_bot_token)
            .build()
        )

        # Register command handlers
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("help", self.cmd_help))
        self.application.add_handler(CommandHandler("markets", self.cmd_markets))
        self.application.add_handler(CommandHandler("price", self.cmd_price))
        self.application.add_handler(CommandHandler("search", self.cmd_search))
        self.application.add_handler(CommandHandler("watch", self.cmd_watch))
        self.application.add_handler(CommandHandler("unwatch", self.cmd_unwatch))
        self.application.add_handler(CommandHandler("watchlist", self.cmd_watchlist))
        self.application.add_handler(CommandHandler("stats", self.cmd_stats))
        self.application.add_handler(CommandHandler("status", self.cmd_status))

        # Portfolio commands
        self.application.add_handler(CommandHandler("buy", self.cmd_buy))
        self.application.add_handler(CommandHandler("sell", self.cmd_sell))
        self.application.add_handler(CommandHandler("portfolio", self.cmd_portfolio))
        self.application.add_handler(CommandHandler("pnl", self.cmd_pnl))

        # Historical analysis
        self.application.add_handler(CommandHandler("history", self.cmd_history))
        self.application.add_handler(CommandHandler("chart", self.cmd_chart))

        # Initialize and start
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(drop_pending_updates=True)

        console.print("[green]Telegram bot started[/green]")

    async def stop(self):
        """Stop the bot."""
        if self.application:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()

    async def send_message(self, text: str, chat_id: str | None = None):
        """Send a message to the configured chat."""
        if not settings.telegram_configured:
            return

        target_chat = chat_id or settings.telegram_chat_id
        bot = Bot(token=settings.telegram_bot_token)
        try:
            await bot.send_message(
                chat_id=target_chat,
                text=text,
                parse_mode="HTML",
            )
        except TelegramError as e:
            console.print(f"[red]Telegram error: {e}[/red]")

    # Command handlers
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        welcome = """Welcome to <b>Archantum</b>!

I'm your Polymarket analysis bot. I monitor markets and alert you on:
- Arbitrage opportunities
- Volume spikes
- Price movements
- And more!

Use /help to see all available commands."""

        await update.message.reply_text(welcome, parse_mode="HTML")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        help_text = """<b>Available Commands</b>

<b>Market Info:</b>
/markets - Top 10 markets by activity
/search &lt;query&gt; - Search markets
/price &lt;market_id&gt; - Get price for a market

<b>Watchlist:</b>
/watch &lt;market_id&gt; - Add to watchlist
/unwatch &lt;market_id&gt; - Remove from watchlist
/watchlist - View your watchlist

<b>Portfolio:</b>
/buy &lt;market_id&gt; &lt;yes/no&gt; &lt;shares&gt; &lt;price&gt;
/sell &lt;market_id&gt; &lt;yes/no&gt; [shares]
/portfolio - View your positions
/pnl - View P&amp;L summary

<b>Analysis:</b>
/history &lt;market_id&gt; - Price history &amp; stats
/chart &lt;market_id&gt; - Mini price chart

<b>Stats:</b>
/stats - Alert statistics
/status - Bot status

<b>Tips:</b>
- Market IDs can be found using /markets or /search
- You'll receive automatic alerts for your watched markets"""

        await update.message.reply_text(help_text, parse_mode="HTML")

    async def cmd_markets(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /markets command - show top markets."""
        await update.message.reply_text("Fetching top markets...")

        try:
            async with GammaClient() as client:
                markets = await client.get_top_markets(max_markets=10)

            if not markets:
                await update.message.reply_text("No active markets found.")
                return

            text = "<b>Top 10 Markets by Volume</b>\n\n"
            for i, m in enumerate(markets, 1):
                vol = m.volume_24hr or 0
                prices = m.outcome_prices or []
                yes_price = float(prices[0]) if prices else 0

                text += f"{i}. <b>{m.question[:50]}{'...' if len(m.question) > 50 else ''}</b>\n"
                text += f"   Yes: ${yes_price:.2f} | Vol: ${vol:,.0f}\n"
                text += f"   ID: <code>{m.id}</code>\n\n"

            await update.message.reply_text(text, parse_mode="HTML")

        except Exception as e:
            await update.message.reply_text(f"Error fetching markets: {e}")

    async def cmd_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /search command."""
        if not context.args:
            await update.message.reply_text("Usage: /search <query>\nExample: /search Trump")
            return

        query = " ".join(context.args)
        await update.message.reply_text(f"Searching for '{query}'...")

        try:
            markets = await self.db.search_markets(query, limit=5)

            if not markets:
                await update.message.reply_text(f"No markets found for '{query}'")
                return

            text = f"<b>Search Results for '{query}'</b>\n\n"
            for i, m in enumerate(markets, 1):
                text += f"{i}. <b>{m.question[:60]}{'...' if len(m.question) > 60 else ''}</b>\n"
                text += f"   ID: <code>{m.id}</code>\n\n"

            await update.message.reply_text(text, parse_mode="HTML")

        except Exception as e:
            await update.message.reply_text(f"Error searching: {e}")

    async def cmd_price(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /price command."""
        if not context.args:
            await update.message.reply_text("Usage: /price <market_id>\nGet market ID from /markets or /search")
            return

        market_id = context.args[0]

        try:
            # Get market from database
            market = await self.db.get_market(market_id)
            if not market:
                await update.message.reply_text(f"Market {market_id} not found in database.")
                return

            # Get latest price
            price = await self.db.get_latest_price_snapshot(market_id)

            text = f"<b>{market.question[:80]}{'...' if len(market.question) > 80 else ''}</b>\n\n"

            if price:
                text += f"<b>Yes:</b> ${price.yes_price:.4f}\n"
                text += f"<b>No:</b> ${price.no_price:.4f}\n"
                text += f"<b>Spread:</b> ${price.spread:.4f}\n"
                text += f"\n<i>Updated: {price.timestamp.strftime('%Y-%m-%d %H:%M UTC')}</i>"
            else:
                text += "No price data available yet."

            # Check if in watchlist
            chat_id = str(update.effective_chat.id)
            if await self.db.is_in_watchlist(chat_id, market_id):
                text += "\n\nIn your watchlist"

            link = f"https://polymarket.com/event/{market.slug}" if market.slug else None
            if link:
                text += f"\n\n<a href='{link}'>View on Polymarket</a>"

            await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)

        except Exception as e:
            await update.message.reply_text(f"Error fetching price: {e}")

    async def cmd_watch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /watch command."""
        if not context.args:
            await update.message.reply_text("Usage: /watch <market_id>\nGet market ID from /markets or /search")
            return

        market_id = context.args[0]
        chat_id = str(update.effective_chat.id)

        try:
            # Check if market exists
            market = await self.db.get_market(market_id)
            if not market:
                await update.message.reply_text(f"Market {market_id} not found.")
                return

            # Add to watchlist
            result = await self.db.add_to_watchlist(chat_id, market_id)
            if result:
                await update.message.reply_text(
                    f"Added to watchlist:\n<b>{market.question[:60]}...</b>",
                    parse_mode="HTML"
                )
            else:
                await update.message.reply_text("Market is already in your watchlist.")

        except Exception as e:
            await update.message.reply_text(f"Error adding to watchlist: {e}")

    async def cmd_unwatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /unwatch command."""
        if not context.args:
            await update.message.reply_text("Usage: /unwatch <market_id>")
            return

        market_id = context.args[0]
        chat_id = str(update.effective_chat.id)

        try:
            removed = await self.db.remove_from_watchlist(chat_id, market_id)
            if removed:
                await update.message.reply_text("Removed from watchlist.")
            else:
                await update.message.reply_text("Market not in your watchlist.")

        except Exception as e:
            await update.message.reply_text(f"Error removing from watchlist: {e}")

    async def cmd_watchlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /watchlist command."""
        chat_id = str(update.effective_chat.id)

        try:
            watchlist = await self.db.get_watchlist(chat_id)

            if not watchlist:
                await update.message.reply_text(
                    "Your watchlist is empty.\nUse /watch <market_id> to add markets."
                )
                return

            text = "<b>Your Watchlist</b>\n\n"
            for i, item in enumerate(watchlist, 1):
                market = await self.db.get_market(item.market_id)
                if market:
                    # Get latest price
                    price = await self.db.get_latest_price_snapshot(item.market_id)
                    price_str = f"${price.yes_price:.2f}" if price else "N/A"

                    text += f"{i}. <b>{market.question[:45]}...</b>\n"
                    text += f"   Yes: {price_str} | ID: <code>{market.id}</code>\n\n"

            text += "Use /unwatch <id> to remove"
            await update.message.reply_text(text, parse_mode="HTML")

        except Exception as e:
            await update.message.reply_text(f"Error fetching watchlist: {e}")

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command."""
        try:
            stats = await self.db.get_alert_stats()
            market_count = await self.db.get_market_count()

            text = "<b>Alert Statistics</b>\n\n"
            text += f"<b>Today:</b> {stats['today']} alerts\n"
            text += f"<b>This week:</b> {stats['this_week']} alerts\n\n"
            text += "<b>By Type (today):</b>\n"
            text += f"  Arbitrage: {stats.get('arbitrage', 0)}\n"
            text += f"  Volume Spike: {stats.get('volume_spike', 0)}\n"
            text += f"  Price Move: {stats.get('price_move', 0)}\n\n"
            text += f"<b>Tracking:</b> {market_count} markets"

            await update.message.reply_text(text, parse_mode="HTML")

        except Exception as e:
            await update.message.reply_text(f"Error fetching stats: {e}")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        try:
            market_count = await self.db.get_market_count()
            alerts_today = await self.db.get_alerts_today()

            text = "<b>Archantum Status</b>\n\n"
            text += f"<b>Bot:</b> Online\n"
            text += f"<b>Markets tracked:</b> {market_count}\n"
            text += f"<b>Alerts today:</b> {len(alerts_today)}\n\n"
            text += "<b>Thresholds:</b>\n"
            text += f"  Arbitrage: {settings.arbitrage_threshold * 100:.1f}%\n"
            text += f"  Volume spike: {settings.volume_spike_multiplier}x\n"
            text += f"  Price move: {settings.price_move_threshold * 100:.1f}%\n"

            await update.message.reply_text(text, parse_mode="HTML")

        except Exception as e:
            await update.message.reply_text(f"Error fetching status: {e}")

    # Portfolio commands
    async def cmd_buy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /buy command - record a buy position."""
        if len(context.args) < 4:
            await update.message.reply_text(
                "Usage: /buy <market_id> <yes/no> <shares> <price>\n"
                "Example: /buy 12345 yes 100 0.65"
            )
            return

        chat_id = str(update.effective_chat.id)
        market_id = context.args[0]
        outcome = context.args[1].lower()

        try:
            shares = float(context.args[2])
            price = float(context.args[3])
        except ValueError:
            await update.message.reply_text("Invalid shares or price. Must be numbers.")
            return

        if outcome not in ['yes', 'no']:
            await update.message.reply_text("Outcome must be 'yes' or 'no'")
            return

        try:
            # Verify market exists
            market = await self.db.get_market(market_id)
            if not market:
                await update.message.reply_text(f"Market {market_id} not found.")
                return

            # Add position
            position = await self.db.add_position(chat_id, market_id, outcome, shares, price)
            cost = shares * price

            text = f"Position recorded:\n\n"
            text += f"<b>{market.question[:50]}...</b>\n"
            text += f"Outcome: {outcome.upper()}\n"
            text += f"Shares: {shares}\n"
            text += f"Price: ${price:.4f}\n"
            text += f"Cost: ${cost:.2f}\n\n"
            text += f"Total position: {position.shares} shares @ ${position.avg_price:.4f}"

            await update.message.reply_text(text, parse_mode="HTML")

        except Exception as e:
            await update.message.reply_text(f"Error recording position: {e}")

    async def cmd_sell(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /sell command - close a position."""
        if len(context.args) < 2:
            await update.message.reply_text(
                "Usage: /sell <market_id> <yes/no> [shares]\n"
                "Omit shares to close entire position."
            )
            return

        chat_id = str(update.effective_chat.id)
        market_id = context.args[0]
        outcome = context.args[1].lower()
        shares = None

        if len(context.args) > 2:
            try:
                shares = float(context.args[2])
            except ValueError:
                await update.message.reply_text("Invalid shares. Must be a number.")
                return

        if outcome not in ['yes', 'no']:
            await update.message.reply_text("Outcome must be 'yes' or 'no'")
            return

        try:
            # Get existing position for P&L calculation
            position = await self.db.get_position(chat_id, market_id, outcome)
            if not position:
                await update.message.reply_text("Position not found.")
                return

            # Get current price
            price_snapshot = await self.db.get_latest_price_snapshot(market_id)
            current_price = 0
            if price_snapshot:
                current_price = price_snapshot.yes_price if outcome == 'yes' else price_snapshot.no_price
                current_price = current_price or 0

            # Calculate P&L for sold shares
            sold_shares = shares if shares else position.shares
            sold_shares = min(sold_shares, position.shares)
            cost_basis = sold_shares * position.avg_price
            proceeds = sold_shares * current_price
            pnl = proceeds - cost_basis

            # Close position
            closed = await self.db.close_position(chat_id, market_id, outcome, shares)

            market = await self.db.get_market(market_id)
            market_name = market.question[:50] if market else market_id

            text = f"Position closed:\n\n"
            text += f"<b>{market_name}...</b>\n"
            text += f"Outcome: {outcome.upper()}\n"
            text += f"Shares sold: {sold_shares}\n"
            text += f"Avg cost: ${position.avg_price:.4f}\n"
            text += f"Exit price: ${current_price:.4f}\n"
            text += f"<b>P&L: ${pnl:+.2f}</b>"

            await update.message.reply_text(text, parse_mode="HTML")

        except Exception as e:
            await update.message.reply_text(f"Error closing position: {e}")

    async def cmd_portfolio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /portfolio command - show all positions."""
        chat_id = str(update.effective_chat.id)

        try:
            positions = await self.db.get_positions(chat_id)

            if not positions:
                await update.message.reply_text(
                    "No positions found.\n"
                    "Use /buy <market_id> <yes/no> <shares> <price> to record a position."
                )
                return

            text = "<b>Your Portfolio</b>\n\n"

            for pos in positions:
                market = await self.db.get_market(pos.market_id)
                price_snapshot = await self.db.get_latest_price_snapshot(pos.market_id)

                market_name = market.question[:40] if market else pos.market_id
                current_price = 0
                if price_snapshot:
                    current_price = price_snapshot.yes_price if pos.outcome == 'yes' else price_snapshot.no_price
                    current_price = current_price or 0

                current_value = pos.shares * current_price
                pnl = current_value - pos.total_cost
                pnl_emoji = "" if pnl >= 0 else ""

                text += f"<b>{market_name}...</b>\n"
                text += f"  {pos.outcome.upper()}: {pos.shares} @ ${pos.avg_price:.4f}\n"
                text += f"  Now: ${current_price:.4f} | {pnl_emoji} ${pnl:+.2f}\n\n"

            text += "Use /pnl for detailed P&L"
            await update.message.reply_text(text, parse_mode="HTML")

        except Exception as e:
            await update.message.reply_text(f"Error fetching portfolio: {e}")

    async def cmd_pnl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /pnl command - show P&L summary."""
        chat_id = str(update.effective_chat.id)

        try:
            pnl_data = await self.db.calculate_portfolio_pnl(chat_id)

            if not pnl_data['positions']:
                await update.message.reply_text("No positions to calculate P&L.")
                return

            # Summary
            total_pnl = pnl_data['total_pnl']
            pnl_emoji = "" if total_pnl >= 0 else ""

            text = "<b>Portfolio P&amp;L Summary</b>\n\n"
            text += f"<b>Total Cost:</b> ${pnl_data['total_cost']:,.2f}\n"
            text += f"<b>Current Value:</b> ${pnl_data['total_value']:,.2f}\n"
            text += f"<b>Total P&L:</b> {pnl_emoji} ${total_pnl:+,.2f} ({pnl_data['total_pnl_pct']:+.1f}%)\n\n"

            text += "<b>By Position:</b>\n"
            for pos in pnl_data['positions']:
                pnl_emoji = "" if pos['pnl'] >= 0 else ""
                text += f"• {pos['question'][:35]}...\n"
                text += f"  {pos['outcome'].upper()}: ${pos['pnl']:+.2f} ({pos['pnl_pct']:+.1f}%)\n"

            await update.message.reply_text(text, parse_mode="HTML")

        except Exception as e:
            await update.message.reply_text(f"Error calculating P&L: {e}")

    # Historical analysis commands
    async def cmd_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /history command - show price history and stats."""
        if not context.args:
            await update.message.reply_text(
                "Usage: /history <market_id>\n"
                "Get market ID from /markets or /search"
            )
            return

        market_id = context.args[0]

        try:
            stats = await self.historical.get_market_stats(market_id)

            if not stats:
                await update.message.reply_text(f"No data found for market {market_id}")
                return

            # Format change with emoji
            change_24h = stats.get('change_24h_pct') or 0
            change_emoji = "" if change_24h >= 0 else ""

            text = f"<b>Market Analysis</b>\n\n"
            text += f"<b>{stats['question'][:80]}...</b>\n\n"

            text += f"<b>Current:</b>\n"
            text += f"  Yes: ${stats['current_yes']:.4f}\n" if stats['current_yes'] else ""
            text += f"  No: ${stats['current_no']:.4f}\n" if stats['current_no'] else ""

            text += f"\n<b>24h Range:</b>\n"
            if stats['high_24h']:
                text += f"  High: ${stats['high_24h']:.4f}\n"
            if stats['low_24h']:
                text += f"  Low: ${stats['low_24h']:.4f}\n"

            text += f"\n<b>Change:</b>\n"
            text += f"  24h: {change_emoji} {change_24h:+.2f}%\n"
            if stats.get('change_7d_pct'):
                text += f"  7d: {stats['change_7d_pct']:+.2f}%\n"

            text += f"\n<b>24h Chart:</b>\n"
            text += f"<code>{stats['sparkline_24h']}</code>\n"

            text += f"\n<b>Alerts:</b> {stats['alert_count']} recent"

            await update.message.reply_text(text, parse_mode="HTML")

        except Exception as e:
            await update.message.reply_text(f"Error fetching history: {e}")

    async def cmd_chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /chart command - show price chart."""
        if not context.args:
            await update.message.reply_text("Usage: /chart <market_id>")
            return

        market_id = context.args[0]
        hours = 24
        if len(context.args) > 1:
            try:
                hours = int(context.args[1])
                hours = min(hours, 168)  # Max 1 week
            except ValueError:
                pass

        try:
            history = await self.historical.get_price_history(market_id, hours=hours)

            if not history:
                await update.message.reply_text(f"No price data for market {market_id}")
                return

            prices = [d['yes_price'] for d in history.data_points if d['yes_price'] > 0]
            sparkline = self.historical.generate_sparkline(prices, width=30)

            text = f"<b>{history.question[:60]}...</b>\n\n"
            text += f"<b>{hours}h Price Chart (Yes):</b>\n"
            text += f"<code>{sparkline}</code>\n\n"
            text += f"High: ${history.high:.4f}  Low: ${history.low:.4f}\n"
            text += f"Current: ${history.current:.4f}\n"
            text += f"Change: {history.change_24h_pct:+.2f}%"

            await update.message.reply_text(text, parse_mode="HTML")

        except Exception as e:
            await update.message.reply_text(f"Error generating chart: {e}")
