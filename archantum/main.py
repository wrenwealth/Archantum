"""Main entry point and CLI for Archantum."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import click
from rich.console import Console

from archantum.config import settings
from archantum.api import GammaClient, CLOBClient
from archantum.api.clob import PriceData
from archantum.api.gamma import GammaMarket
from archantum.db import Database
from archantum.analysis import ArbitrageAnalyzer, VolumeAnalyzer, PriceAnalyzer, TrendAnalyzer, WhaleAnalyzer, NewMarketAnalyzer, ResolutionAnalyzer, AccuracyTracker
from archantum.alerts import TelegramAlerter, TelegramBot
from archantum.cli import Dashboard


console = Console()


def chunk_list(lst: list, chunk_size: int) -> list[list]:
    """Split a list into chunks."""
    return [lst[i : i + chunk_size] for i in range(0, len(lst), chunk_size)]


class PollingEngine:
    """Main polling engine for market data."""

    def __init__(self, with_bot: bool = True):
        self.db = Database()
        self.alerter = TelegramAlerter(self.db)
        self.dashboard = Dashboard(self.db)
        self.bot = TelegramBot(self.db) if with_bot else None

        # Analyzers
        self.arbitrage_analyzer = ArbitrageAnalyzer()
        self.volume_analyzer = VolumeAnalyzer(self.db)
        self.price_analyzer = PriceAnalyzer(self.db)
        self.trend_analyzer = TrendAnalyzer(self.db)
        self.whale_analyzer = WhaleAnalyzer(self.db)
        self.new_market_analyzer = NewMarketAnalyzer(self.db)
        self.resolution_analyzer = ResolutionAnalyzer(self.db)
        self.accuracy_tracker = AccuracyTracker(self.db)

        self.running = False

    async def init(self):
        """Initialize the engine."""
        await self.db.init_db()
        console.print("[green]Database initialized[/green]")

        # Start bot if enabled
        if self.bot:
            await self.bot.start()

    async def close(self):
        """Close connections."""
        if self.bot:
            await self.bot.stop()
        await self.db.close()

    async def fetch_prices(
        self,
        clob_client: CLOBClient,
        markets: list[GammaMarket],
    ) -> dict[str, PriceData]:
        """Fetch prices for all markets with rate limiting."""
        all_prices: dict[str, PriceData] = {}

        batches = chunk_list(markets, settings.batch_size)
        total_batches = len(batches)

        for i, batch in enumerate(batches, 1):
            console.print(f"[dim]Fetching prices batch {i}/{total_batches}...[/dim]")

            for market in batch:
                try:
                    price_data = await clob_client.get_price_for_market(
                        yes_token_id=market.yes_token_id,
                        no_token_id=market.no_token_id,
                        market_id=market.id,
                    )
                    all_prices[market.id] = price_data
                except Exception as e:
                    console.print(f"[yellow]Error fetching price for {market.id}: {e}[/yellow]")

            # Rate limit between batches
            if i < total_batches:
                await asyncio.sleep(settings.batch_delay)

        return all_prices

    async def poll_once(self) -> dict[str, Any]:
        """Run a single polling cycle."""
        results = {
            "markets_fetched": 0,
            "prices_fetched": 0,
            "arbitrage_opportunities": 0,
            "volume_spikes": 0,
            "price_movements": 0,
            "whale_activities": 0,
            "new_markets": 0,
            "resolution_alerts": 0,
            "accuracy_evaluated": 0,
            "alerts_sent": 0,
        }

        async with GammaClient() as gamma_client, CLOBClient() as clob_client:
            # 1. Fetch top active markets (filtered by volume)
            console.print("[cyan]Fetching top markets by volume...[/cyan]")
            markets = await gamma_client.get_top_markets()
            results["markets_fetched"] = len(markets)
            console.print(f"[green]Tracking {len(markets)} markets (min vol: ${settings.min_volume_24hr:,.0f})[/green]")

            # 2. Update markets in database
            await self.db.upsert_markets(markets)

            # 3. Fetch prices with rate limiting
            console.print("[cyan]Fetching prices...[/cyan]")
            all_prices = await self.fetch_prices(clob_client, markets)
            results["prices_fetched"] = len(all_prices)

            # 4. Save price snapshots
            for market_id, price_data in all_prices.items():
                await self.db.save_price_snapshot(price_data)

            # 5. Run analysis
            console.print("[cyan]Running analysis...[/cyan]")

            # Arbitrage detection
            arbitrage_opps = self.arbitrage_analyzer.analyze(markets, all_prices)
            results["arbitrage_opportunities"] = len(arbitrage_opps)

            # Volume spike detection
            volume_spikes = await self.volume_analyzer.analyze(markets)
            results["volume_spikes"] = len(volume_spikes)

            # Price movement detection
            price_moves = await self.price_analyzer.analyze(markets, all_prices)
            results["price_movements"] = len(price_moves)

            # Whale activity detection
            whale_activities = await self.whale_analyzer.analyze(markets)
            results["whale_activities"] = len(whale_activities)

            # New market detection
            new_markets = await self.new_market_analyzer.analyze(markets)
            results["new_markets"] = len(new_markets)

            # Resolution alerts
            resolution_alerts = await self.resolution_analyzer.analyze(markets)
            results["resolution_alerts"] = len(resolution_alerts)

            # Evaluate alert accuracy (24h+ old alerts)
            accuracy_results = await self.accuracy_tracker.evaluate_pending_alerts()
            results["accuracy_evaluated"] = len(accuracy_results)

            # 6. Send alerts
            for opp in arbitrage_opps:
                alert = self.alerter.format_arbitrage_alert(opp)
                await self.alerter.send_alert(alert)
                results["alerts_sent"] += 1

            for spike in volume_spikes:
                alert = self.alerter.format_volume_alert(spike)
                await self.alerter.send_alert(alert)
                results["alerts_sent"] += 1

            for move in price_moves:
                alert = self.alerter.format_price_move_alert(move)
                await self.alerter.send_alert(alert)
                results["alerts_sent"] += 1

            for whale in whale_activities:
                alert = self.alerter.format_whale_alert(whale)
                await self.alerter.send_alert(alert)
                results["alerts_sent"] += 1

            for new_market in new_markets:
                alert = self.alerter.format_new_market_alert(new_market)
                await self.alerter.send_alert(alert)
                results["alerts_sent"] += 1

            for resolution in resolution_alerts:
                alert = self.alerter.format_resolution_alert(resolution)
                await self.alerter.send_alert(alert)
                results["alerts_sent"] += 1

            # Update dashboard
            self.dashboard.set_last_poll(datetime.utcnow())

        return results

    async def run(self):
        """Run the main polling loop."""
        self.running = True
        console.print("[bold green]Starting Archantum polling engine...[/bold green]")

        if settings.telegram_configured:
            console.print("[green]Telegram alerts enabled[/green]")
        else:
            console.print("[yellow]Telegram not configured - using console alerts[/yellow]")

        console.print(f"[dim]Poll interval: {settings.poll_interval}s[/dim]")
        console.print(f"[dim]Arbitrage threshold: {settings.arbitrage_threshold * 100}%[/dim]")
        console.print(f"[dim]Volume spike multiplier: {settings.volume_spike_multiplier}x[/dim]")
        console.print(f"[dim]Price move threshold: {settings.price_move_threshold * 100}%[/dim]")
        console.print()

        while self.running:
            try:
                console.print(f"[bold]{'=' * 50}[/bold]")
                console.print(f"[bold]Poll started at {datetime.utcnow().isoformat()}[/bold]")

                results = await self.poll_once()

                console.print(f"[green]Poll complete:[/green]")
                console.print(f"  Markets: {results['markets_fetched']}")
                console.print(f"  Prices: {results['prices_fetched']}")
                console.print(f"  Arbitrage opps: {results['arbitrage_opportunities']}")
                console.print(f"  Volume spikes: {results['volume_spikes']}")
                console.print(f"  Price moves: {results['price_movements']}")
                console.print(f"  Whale activities: {results['whale_activities']}")
                console.print(f"  New markets: {results['new_markets']}")
                console.print(f"  Resolution alerts: {results['resolution_alerts']}")
                console.print(f"  Accuracy evaluated: {results['accuracy_evaluated']}")
                console.print(f"  Alerts sent: {results['alerts_sent']}")

                console.print(f"\n[dim]Sleeping for {settings.poll_interval}s...[/dim]\n")
                await asyncio.sleep(settings.poll_interval)

            except KeyboardInterrupt:
                console.print("\n[yellow]Shutting down...[/yellow]")
                self.running = False
            except Exception as e:
                console.print(f"[red]Error during poll: {e}[/red]")
                console.print(f"[dim]Retrying in {settings.poll_interval}s...[/dim]")
                await asyncio.sleep(settings.poll_interval)

    def stop(self):
        """Stop the polling loop."""
        self.running = False


async def run_engine():
    """Run the polling engine."""
    engine = PollingEngine()
    try:
        await engine.init()
        await engine.run()
    finally:
        await engine.close()


async def run_dashboard():
    """Run the dashboard."""
    db = Database()
    await db.init_db()

    dashboard = Dashboard(db)
    try:
        await dashboard.run()
    finally:
        await db.close()


async def run_test_alert():
    """Send a test alert."""
    db = Database()
    await db.init_db()

    alerter = TelegramAlerter(db)
    success = await alerter.send_test_alert()

    if success:
        console.print("[green]Test alert sent successfully![/green]")
    else:
        console.print("[yellow]Test alert printed to console (Telegram not configured)[/yellow]")

    await db.close()


@click.group()
@click.version_option(version="0.2.0", prog_name="archantum")
def cli():
    """Archantum - Polymarket Data Analysis Agent."""
    pass


@cli.command()
def run():
    """Start the polling engine."""
    asyncio.run(run_engine())


@cli.command()
def dashboard():
    """Show the CLI dashboard."""
    asyncio.run(run_dashboard())


@cli.command("test-alert")
def test_alert():
    """Send a test alert to verify Telegram configuration."""
    asyncio.run(run_test_alert())


@cli.command()
def status():
    """Show current status."""
    async def show_status():
        db = Database()
        await db.init_db()

        market_count = await db.get_market_count()
        alerts_today = await db.get_alerts_today()

        console.print("\n[bold cyan]Archantum Status[/bold cyan]\n")
        console.print(f"Database: {settings.database_path}")
        console.print(f"Active markets: {market_count}")
        console.print(f"Alerts today: {len(alerts_today)}")
        console.print(f"Telegram: {'Configured' if settings.telegram_configured else 'Not configured'}")
        console.print()

        await db.close()

    asyncio.run(show_status())


@cli.command()
def health():
    """Health check for Docker/monitoring."""
    import sys

    async def check_health():
        try:
            db = Database()
            await db.init_db()

            # Check database connection
            count = await db.get_market_count()

            await db.close()

            console.print(f"OK - Database connected, {count} markets")
            return 0
        except Exception as e:
            console.print(f"FAIL - {e}")
            return 1

    result = asyncio.run(check_health())
    sys.exit(result)


@cli.command()
def bot():
    """Run only the Telegram bot (without polling)."""
    async def run_bot_only():
        db = Database()
        await db.init_db()

        telegram_bot = TelegramBot(db)
        await telegram_bot.start()

        console.print("[bold green]Telegram bot running. Press Ctrl+C to stop.[/bold green]")

        try:
            # Keep running until interrupted
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping bot...[/yellow]")
        finally:
            await telegram_bot.stop()
            await db.close()

    asyncio.run(run_bot_only())


if __name__ == "__main__":
    cli()
