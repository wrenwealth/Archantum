"""Main entry point and CLI for Archantum."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import click
from rich.console import Console

from archantum.config import settings
from archantum.api import GammaClient, CLOBClient, KalshiClient
from archantum.api.clob import PriceData
from archantum.api.gamma import GammaMarket
from archantum.db import Database
from archantum.analysis import ArbitrageAnalyzer, PriceAnalyzer, TrendAnalyzer, WhaleAnalyzer, NewMarketAnalyzer, ResolutionAnalyzer, AccuracyTracker, SmartMoneyTracker
from archantum.analysis.indicators import TechnicalIndicatorCalculator
from archantum.analysis.confluence import ConfluenceAnalyzer
from archantum.analysis.scoring import MarketScorer
from archantum.analysis.cross_platform import CrossPlatformAnalyzer
from archantum.analysis.lp_rewards import LPRewardsAnalyzer
from archantum.analysis.liquidity import LiquidityAnalyzer
from archantum.analysis.risk_score import ExecutionRiskScorer
from archantum.analysis.multi_outcome import MultiOutcomeAnalyzer, SumDeviationTracker
from archantum.analysis.dependency import DependencyAnalyzer
from archantum.analysis.speed_tracker import SpeedTracker
from archantum.analysis.settlement import SettlementLagDetector
from archantum.analysis.arbitrage import (
    calculate_guaranteed_profit,
    classify_opportunity_reason,
    OpportunityReason,
)
from archantum.data import DataSourceManager, PriceValidator
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
        self.price_analyzer = PriceAnalyzer(self.db)
        self.trend_analyzer = TrendAnalyzer(self.db)
        self.whale_analyzer = WhaleAnalyzer(self.db)
        self.new_market_analyzer = NewMarketAnalyzer(self.db)
        self.resolution_analyzer = ResolutionAnalyzer(self.db)
        self.accuracy_tracker = AccuracyTracker(self.db)
        self.smart_money_tracker = SmartMoneyTracker(
            self.db,
            min_trade_usdc=settings.smart_money_min_trade_usdc,
            top_wallets_count=settings.smart_money_top_wallets,
        )

        # NEW: Data engine components
        self.source_manager = DataSourceManager(self.db)
        self.indicator_calculator = TechnicalIndicatorCalculator(self.db)
        self.confluence_analyzer = ConfluenceAnalyzer(self.db)
        self.price_validator = PriceValidator(self.db)

        # Market scorer
        self.market_scorer: MarketScorer | None = None  # Initialized in init()

        # Cross-platform arbitrage
        self.cross_platform_analyzer = CrossPlatformAnalyzer()

        # LP rewards analyzer
        self.lp_rewards_analyzer = LPRewardsAnalyzer()

        # Advanced arbitrage analyzers
        self.liquidity_analyzer = LiquidityAnalyzer()
        self.risk_scorer = ExecutionRiskScorer(self.db)
        self.multi_outcome_analyzer = MultiOutcomeAnalyzer()
        self.dependency_analyzer = DependencyAnalyzer()
        self.speed_tracker = SpeedTracker(self.db)
        self.settlement_detector = SettlementLagDetector(self.db)
        self.deviation_tracker = SumDeviationTracker(self.db)

        self.running = False
        self._smart_money_poll_count = 0  # Track polls for less frequent smart money sync
        self._ta_poll_count = 0  # Track polls for TA calculation
        self._scoring_poll_count = 0  # Track polls for market scoring
        self._cross_platform_poll_count = 0  # Track polls for cross-platform arbitrage
        self._advanced_arb_poll_count = 0  # Track polls for multi-outcome + dependency

    async def init(self):
        """Initialize the engine."""
        await self.db.init_db()
        console.print("[green]Database initialized[/green]")

        # Initialize market scorer (needs async session)
        async with self.db.async_session() as session:
            self.market_scorer = MarketScorer(session)

        # Initialize data source manager (WebSocket + REST fallback)
        await self.source_manager.initialize()
        if settings.ws_enabled:
            console.print("[green]WebSocket data source initialized[/green]")

        # Start bot if enabled
        if self.bot:
            await self.bot.start()

    async def close(self):
        """Close connections."""
        if self.bot:
            await self.bot.stop()
        await self.source_manager.close()
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
            "price_movements": 0,
            "whale_activities": 0,
            "new_markets": 0,
            "resolution_alerts": 0,
            "accuracy_evaluated": 0,
            "smart_money_alerts": 0,
            "confluence_signals": 0,
            "price_discrepancies": 0,
            "market_scores": 0,
            "cross_platform_arbs": 0,
            "lp_opportunities": 0,
            "multi_outcome_arbs": 0,
            "dependency_arbs": 0,
            "arb_enriched": 0,
            "settlement_lag_opps": 0,
            "alerts_sent": 0,
            "data_source": "unknown",
        }

        async with GammaClient() as gamma_client:
            # 1. Fetch top active markets (filtered by volume)
            console.print("[cyan]Fetching top markets by volume...[/cyan]")
            markets = await gamma_client.get_top_markets()
            results["markets_fetched"] = len(markets)
            console.print(f"[green]Tracking {len(markets)} markets (min vol: ${settings.min_volume_24hr:,.0f})[/green]")

            # 2. Update markets in database
            await self.db.upsert_markets(markets)

            # 3. Subscribe to WebSocket for new markets
            if settings.ws_enabled:
                market_tokens = [
                    {
                        "id": m.id,
                        "yes_token": m.yes_token_id,
                        "no_token": m.no_token_id,
                    }
                    for m in markets
                ]
                await self.source_manager.subscribe_markets(market_tokens)

            # 4. Fetch prices using data source manager (WebSocket -> REST -> Cache)
            console.print("[cyan]Fetching prices...[/cyan]")
            all_price_results = await self._fetch_prices_with_failover(markets)
            results["prices_fetched"] = len(all_price_results)

            # Track primary data source
            source_counts = {}
            for pr in all_price_results.values():
                source_counts[pr.source] = source_counts.get(pr.source, 0) + 1
            results["data_source"] = max(source_counts.keys(), key=lambda k: source_counts[k]) if source_counts else "none"

            # Convert to PriceData for compatibility with existing analyzers
            all_prices: dict[str, PriceData] = {}
            for market_id, price_result in all_price_results.items():
                all_prices[market_id] = price_result.to_price_data()

            # 5. Save price snapshots with source tracking
            for market_id, price_result in all_price_results.items():
                await self._save_price_snapshot_with_source(price_result)

            # 6. Run analysis
            console.print("[cyan]Running analysis...[/cyan]")

            # Arbitrage detection
            arbitrage_opps = self.arbitrage_analyzer.analyze(markets, all_prices)
            results["arbitrage_opportunities"] = len(arbitrage_opps)

            # Speed tracking: record detections
            poll_time = datetime.utcnow()
            for opp in arbitrage_opps:
                try:
                    await self.speed_tracker.record_detection(opp, poll_time)
                except Exception:
                    pass

            # Liquidity enrichment + risk scoring for top arbitrage opportunities
            arb_enrichments: dict[str, tuple] = {}  # market_id -> (enrichment, risk)
            if arbitrage_opps:
                max_enrich = settings.liquidity_enrichment_max
                console.print(f"[cyan]Enriching top {min(len(arbitrage_opps), max_enrich)} arbitrage opps with liquidity...[/cyan]")
                market_lookup = {m.id: m for m in markets}
                try:
                    async with CLOBClient() as clob_client:
                        for opp in arbitrage_opps[:max_enrich]:
                            market = market_lookup.get(opp.market_id)
                            if not market:
                                continue
                            try:
                                enriched = await self.liquidity_analyzer.enrich_arbitrage(
                                    clob_client, opp, market
                                )
                                risk = await self.risk_scorer.score(opp, enriched.yes_liquidity)
                                arb_enrichments[opp.market_id] = (enriched, risk)
                                results["arb_enriched"] += 1
                            except Exception as e:
                                console.print(f"[yellow]Enrichment error for {opp.market_id}: {e}[/yellow]")
                except Exception as e:
                    console.print(f"[yellow]Liquidity enrichment error: {e}[/yellow]")

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

            # Smart money tracking (sync every 5 polls to avoid API spam)
            self._smart_money_poll_count += 1
            if self._smart_money_poll_count >= 5:
                self._smart_money_poll_count = 0
                try:
                    console.print("[cyan]Syncing smart money wallets...[/cyan]")
                    await self.smart_money_tracker.sync_leaderboard()
                    await self.smart_money_tracker.sync_all_tracked_wallets()
                except Exception as e:
                    console.print(f"[yellow]Smart money sync error: {e}[/yellow]")

            # Get pending smart money alerts
            smart_money_alerts = await self.smart_money_tracker.get_pending_alerts()
            results["smart_money_alerts"] = len(smart_money_alerts)

            # 7. Technical Analysis (every ta_poll_frequency polls)
            confluence_signals = []
            if settings.ta_enabled:
                self._ta_poll_count += 1
                if self._ta_poll_count >= settings.ta_poll_frequency:
                    self._ta_poll_count = 0
                    console.print("[cyan]Running technical analysis...[/cyan]")

                    # Build market info and prices for confluence analyzer
                    market_dicts = [
                        {
                            "id": m.id,
                            "question": m.question,
                            "event_id": self._get_event_slug(m),
                        }
                        for m in markets
                    ]
                    prices_for_ta = {
                        mid: pr.yes_price
                        for mid, pr in all_price_results.items()
                        if pr.yes_price is not None
                    }

                    try:
                        confluence_signals = await self.confluence_analyzer.analyze_markets(
                            market_dicts, prices_for_ta
                        )
                        results["confluence_signals"] = len(confluence_signals)
                    except Exception as e:
                        console.print(f"[yellow]TA analysis error: {e}[/yellow]")

            # 8. Market scoring (every 5 polls, same as smart money)
            self._scoring_poll_count += 1
            if self._scoring_poll_count >= 5:
                self._scoring_poll_count = 0
                console.print("[cyan]Scoring markets...[/cyan]")
                try:
                    async with self.db.async_session() as session:
                        scorer = MarketScorer(session)
                        market_scores = await scorer.score_markets(markets, all_prices)
                        await session.commit()
                        results["market_scores"] = len(market_scores)

                        # Log top 5 markets
                        if market_scores:
                            top5 = market_scores[:5]
                            console.print("[dim]Top 5 markets by score:[/dim]")
                            for i, score in enumerate(top5, 1):
                                console.print(
                                    f"[dim]  {i}. [{score.rank_tier}] {score.total_score:.0f}/100 - "
                                    f"{score.question[:40]}...[/dim]"
                                )
                except Exception as e:
                    console.print(f"[yellow]Market scoring error: {e}[/yellow]")

            # 9. Cross-platform arbitrage (Polymarket vs Kalshi) - every 5 polls
            cross_platform_opps = []
            self._cross_platform_poll_count += 1
            if self._cross_platform_poll_count >= 5:
                self._cross_platform_poll_count = 0
                console.print("[cyan]Checking cross-platform arbitrage (Kalshi)...[/cyan]")
                try:
                    async with KalshiClient() as kalshi_client:
                        # Fetch Kalshi markets
                        kalshi_markets = await kalshi_client.get_all_open_markets(max_markets=300)
                        console.print(f"[dim]Fetched {len(kalshi_markets)} Kalshi markets[/dim]")

                        if kalshi_markets:
                            # Match markets between platforms
                            matches = self.cross_platform_analyzer.match_markets(markets, kalshi_markets)
                            console.print(f"[dim]Found {len(matches)} potential market matches[/dim]")

                            if matches:
                                # Get Kalshi prices
                                kalshi_prices = {
                                    m.ticker: m.to_price_data()
                                    for m in kalshi_markets
                                }

                                # Check for arbitrage
                                cross_platform_opps = self.cross_platform_analyzer.analyze(
                                    matches, all_prices, kalshi_prices
                                )
                                results["cross_platform_arbs"] = len(cross_platform_opps)

                                if cross_platform_opps:
                                    console.print(f"[bold green]Found {len(cross_platform_opps)} cross-platform opportunities![/bold green]")
                except Exception as e:
                    console.print(f"[yellow]Cross-platform analysis error: {e}[/yellow]")

            # 10. LP Rewards opportunities (every 5 polls, same as scoring)
            lp_opportunities = []
            if self._scoring_poll_count == 0:  # Runs when scoring runs (counter just reset)
                console.print("[cyan]Finding LP opportunities...[/cyan]")
                try:
                    lp_opportunities = self.lp_rewards_analyzer.get_top_opportunities(
                        markets, all_prices, top_n=5
                    )
                    results["lp_opportunities"] = len(lp_opportunities)

                    if lp_opportunities:
                        console.print(f"[dim]Top LP opportunities by APY:[/dim]")
                        for i, lp_opp in enumerate(lp_opportunities[:3], 1):
                            console.print(
                                f"[dim]  {i}. ~{lp_opp.estimated_apy:.0f}% APY - "
                                f"{lp_opp.question[:40]}...[/dim]"
                            )
                except Exception as e:
                    console.print(f"[yellow]LP analysis error: {e}[/yellow]")

            # 11. Multi-outcome + Dependency arbitrage (every 5 polls)
            multi_outcome_opps = []
            dependency_opps = []
            self._advanced_arb_poll_count += 1
            if self._advanced_arb_poll_count >= 5:
                self._advanced_arb_poll_count = 0

                # Multi-outcome arbitrage
                console.print("[cyan]Checking multi-outcome arbitrage...[/cyan]")
                try:
                    multi_outcome_opps = self.multi_outcome_analyzer.analyze(markets, all_prices)
                    results["multi_outcome_arbs"] = len(multi_outcome_opps)
                    if multi_outcome_opps:
                        console.print(f"[bold green]Found {len(multi_outcome_opps)} multi-outcome opportunities![/bold green]")
                except Exception as e:
                    console.print(f"[yellow]Multi-outcome analysis error: {e}[/yellow]")

                # Dependency arbitrage
                console.print("[cyan]Checking dependency arbitrage...[/cyan]")
                try:
                    dependency_opps = self.dependency_analyzer.analyze(markets, all_prices)
                    results["dependency_arbs"] = len(dependency_opps)
                    if dependency_opps:
                        console.print(f"[bold green]Found {len(dependency_opps)} dependency opportunities![/bold green]")
                except Exception as e:
                    console.print(f"[yellow]Dependency analysis error: {e}[/yellow]")

            # 12. Price validation across sources (WebSocket vs REST)
            price_discrepancies = []
            if settings.ws_enabled and self.source_manager.websocket.stats.is_connected:
                # Get fresh REST prices for validation
                ws_prices = {
                    mid: pr for mid, pr in all_price_results.items()
                    if pr.source == "websocket"
                }
                if ws_prices:
                    rest_prices = await self._fetch_rest_prices_for_validation(markets, ws_prices.keys())
                    market_info = {
                        m.id: {
                            "question": m.question,
                            "polymarket_url": self._build_polymarket_url(m),
                        }
                        for m in markets
                    }
                    try:
                        price_discrepancies = await self.price_validator.validate_batch(
                            ws_prices, rest_prices, market_info
                        )
                        results["price_discrepancies"] = len(price_discrepancies)
                    except Exception as e:
                        console.print(f"[yellow]Price validation error: {e}[/yellow]")

            # Settlement lag detection (every poll — lightweight)
            settlement_opps = []
            try:
                console.print("[cyan]Checking settlement lag...[/cyan]")
                settlement_opps = await self.settlement_detector.analyze(markets, all_prices)
                results["settlement_lag_opps"] = len(settlement_opps)
                if settlement_opps:
                    console.print(f"[bold green]Found {len(settlement_opps)} settlement lag opportunities![/bold green]")
            except Exception as e:
                console.print(f"[yellow]Settlement lag error: {e}[/yellow]")

            # Send alerts
            for opp in arbitrage_opps:
                enrichment_data = arb_enrichments.get(opp.market_id)
                enriched, risk = enrichment_data if enrichment_data else (None, None)

                # Guaranteed profit calculation + alpha filtering
                gp = calculate_guaranteed_profit(opp, enriched)
                if gp.guaranteed_profit_cents < settings.guaranteed_profit_min_cents:
                    continue
                if gp.capture_ratio < settings.alpha_capture_min_pct:
                    continue

                # Classify reason
                reason = classify_opportunity_reason(opp, enriched)

                alert = self.alerter.format_arbitrage_alert(
                    opp, enrichment=enriched, risk=risk,
                    guaranteed_profit=gp, reason=reason,
                )
                await self.alerter.send_alert(alert)
                results["alerts_sent"] += 1

                # Speed tracking: record alert sent
                try:
                    await self.speed_tracker.record_alert_sent(opp.market_id, datetime.utcnow())
                except Exception:
                    pass

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

            for smart_alert in smart_money_alerts:
                alert = self.alerter.format_smart_money_alert(smart_alert)
                await self.alerter.send_alert(alert)
                results["alerts_sent"] += 1

            # Send confluence alerts
            for signal in confluence_signals:
                alert = self.alerter.format_confluence_alert(signal)
                await self.alerter.send_alert(alert)
                results["alerts_sent"] += 1

            # Send cross-platform arbitrage alerts
            for cross_opp in cross_platform_opps:
                alert = self.alerter.format_cross_platform_alert(cross_opp)
                await self.alerter.send_alert(alert)
                results["alerts_sent"] += 1

            # Deviation tracking + enrichment for multi-outcome opps
            if multi_outcome_opps:
                try:
                    for mo_opp in multi_outcome_opps:
                        await self.deviation_tracker.record_deviation(
                            event_slug=mo_opp.event_slug,
                            outcome_count=mo_opp.outcome_count,
                            sum_deviation=abs(mo_opp.total_probability - 1.0),
                            total_probability=mo_opp.total_probability,
                        )
                    await self.deviation_tracker.enrich_opportunities(multi_outcome_opps)
                except Exception as e:
                    console.print(f"[yellow]Deviation tracking error: {e}[/yellow]")

            # Send multi-outcome arbitrage alerts
            for mo_opp in multi_outcome_opps:
                alert = self.alerter.format_multi_outcome_alert(mo_opp)
                await self.alerter.send_alert(alert)
                results["alerts_sent"] += 1

            # Send dependency arbitrage alerts
            for dep_opp in dependency_opps:
                alert = self.alerter.format_dependency_alert(dep_opp)
                await self.alerter.send_alert(alert)
                results["alerts_sent"] += 1

            # Send settlement lag alerts
            for s_opp in settlement_opps:
                alert = self.alerter.format_settlement_lag_alert(s_opp)
                await self.alerter.send_alert(alert)
                results["alerts_sent"] += 1

            # Speed tracking: check which opportunities are still available
            current_arb_ids = {opp.market_id for opp in arbitrage_opps}
            try:
                await self.speed_tracker.check_still_available(current_arb_ids)
            except Exception:
                pass

            # Log price discrepancies to console only (data sync warning, not real arbitrage)
            for discrepancy in price_discrepancies:
                if discrepancy.is_significant:
                    console.print(
                        f"[yellow]DATA SYNC WARNING: {discrepancy.question[:50]}... "
                        f"WS={discrepancy.websocket_yes:.2f} vs REST={discrepancy.rest_yes:.2f} "
                        f"({discrepancy.max_diff_pct:.1f}% diff)[/yellow]"
                    )

            # Update dashboard
            self.dashboard.set_last_poll(datetime.utcnow())

        return results

    async def _fetch_prices_with_failover(
        self,
        markets: list[GammaMarket],
    ) -> dict[str, "PriceResult"]:
        """Fetch prices using data source manager with failover."""
        from archantum.data import PriceResult

        all_prices: dict[str, PriceResult] = {}

        batches = chunk_list(markets, settings.batch_size)
        total_batches = len(batches)

        for i, batch in enumerate(batches, 1):
            console.print(f"[dim]Fetching prices batch {i}/{total_batches}...[/dim]")

            for market in batch:
                try:
                    price_result = await self.source_manager.get_price(
                        market_id=market.id,
                        yes_token=market.yes_token_id,
                        no_token=market.no_token_id,
                    )
                    all_prices[market.id] = price_result
                except Exception as e:
                    console.print(f"[yellow]Error fetching price for {market.id}: {e}[/yellow]")

            # Rate limit between batches
            if i < total_batches:
                await asyncio.sleep(settings.batch_delay)

        return all_prices

    async def _save_price_snapshot_with_source(self, price_result: "PriceResult") -> None:
        """Save price snapshot with source tracking."""
        from archantum.db.models import PriceSnapshot

        async with self.db.async_session() as session:
            snapshot = PriceSnapshot(
                market_id=price_result.market_id,
                yes_price=price_result.yes_price,
                no_price=price_result.no_price,
                yes_bid=price_result.yes_bid,
                yes_ask=price_result.yes_ask,
                no_bid=price_result.no_bid,
                no_ask=price_result.no_ask,
                spread=price_result.spread,
                source=price_result.source,
            )
            session.add(snapshot)
            await session.commit()

    async def _fetch_rest_prices_for_validation(
        self,
        markets: list[GammaMarket],
        market_ids: set[str],
        max_samples: int = 10,
    ) -> dict[str, "PriceResult"]:
        """Fetch REST prices for a SAMPLE of markets for validation.

        Only validates a small sample to avoid slow API calls.
        """
        import random
        from archantum.data import PriceResult

        rest_prices = {}

        # Only validate a random sample to keep polling fast
        markets_to_validate = [m for m in markets if m.id in market_ids]
        if len(markets_to_validate) > max_samples:
            markets_to_validate = random.sample(markets_to_validate, max_samples)

        async with CLOBClient() as clob_client:
            for market in markets_to_validate:
                try:
                    price_data = await clob_client.get_price_for_market(
                        yes_token_id=market.yes_token_id,
                        no_token_id=market.no_token_id,
                        market_id=market.id,
                    )
                    rest_prices[market.id] = PriceResult(
                        market_id=market.id,
                        yes_price=price_data.yes_price,
                        no_price=price_data.no_price,
                        yes_bid=price_data.yes_bid,
                        yes_ask=price_data.yes_ask,
                        no_bid=price_data.no_bid,
                        no_ask=price_data.no_ask,
                        source="rest",
                    )
                except Exception:
                    pass

        return rest_prices

    def _get_event_slug(self, market: GammaMarket) -> str | None:
        """Get event slug from market."""
        if market.events and len(market.events) > 0:
            return market.events[0].get("slug")
        return market.event_slug

    def _build_polymarket_url(self, market: GammaMarket) -> str | None:
        """Build Polymarket URL for a market."""
        event_slug = self._get_event_slug(market)
        if event_slug:
            return f"https://polymarket.com/event/{event_slug}"
        return None

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
        console.print(f"[dim]Price move threshold: {settings.price_move_threshold * 100}%[/dim]")

        # Show data engine status
        if settings.ws_enabled:
            console.print(f"[dim]WebSocket: Enabled[/dim]")
        else:
            console.print(f"[dim]WebSocket: Disabled[/dim]")
        if settings.ta_enabled:
            console.print(f"[dim]Technical Analysis: Enabled (every {settings.ta_poll_frequency} polls)[/dim]")
        else:
            console.print(f"[dim]Technical Analysis: Disabled[/dim]")
        console.print()

        while self.running:
            try:
                console.print(f"[bold]{'=' * 50}[/bold]")
                console.print(f"[bold]Poll started at {datetime.utcnow().isoformat()}[/bold]")

                results = await self.poll_once()

                console.print(f"[green]Poll complete:[/green]")
                console.print(f"  Markets: {results['markets_fetched']}")
                console.print(f"  Prices: {results['prices_fetched']} (source: {results['data_source']})")
                console.print(f"  Arbitrage opps: {results['arbitrage_opportunities']}")
                console.print(f"  Price moves: {results['price_movements']}")
                console.print(f"  Whale activities: {results['whale_activities']}")
                console.print(f"  New markets: {results['new_markets']}")
                console.print(f"  Resolution alerts: {results['resolution_alerts']}")
                console.print(f"  Accuracy evaluated: {results['accuracy_evaluated']}")
                console.print(f"  Smart money alerts: {results['smart_money_alerts']}")
                console.print(f"  Confluence signals: {results['confluence_signals']}")
                console.print(f"  Price discrepancies: {results['price_discrepancies']}")
                console.print(f"  Market scores: {results['market_scores']}")
                console.print(f"  Cross-platform arbs: {results['cross_platform_arbs']}")
                console.print(f"  LP opportunities: {results['lp_opportunities']}")
                console.print(f"  Multi-outcome arbs: {results['multi_outcome_arbs']}")
                console.print(f"  Dependency arbs: {results['dependency_arbs']}")
                console.print(f"  Arb enriched (liquidity): {results['arb_enriched']}")
                console.print(f"  Settlement lag opps: {results['settlement_lag_opps']}")
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

        # Data engine status
        console.print("\n[bold cyan]Data Engine[/bold cyan]")
        console.print(f"WebSocket: {'Enabled' if settings.ws_enabled else 'Disabled'}")
        console.print(f"Technical Analysis: {'Enabled' if settings.ta_enabled else 'Disabled'}")
        console.print(f"TA Frequency: Every {settings.ta_poll_frequency} polls")
        console.print(f"Confluence Threshold: {settings.confluence_alert_threshold}")
        console.print(f"RSI Oversold/Overbought: {settings.rsi_oversold}/{settings.rsi_overbought}")

        # Get price discrepancy stats if available
        try:
            validator = PriceValidator(db)
            disc_stats = await validator.get_discrepancy_stats()
            console.print("\n[bold cyan]Price Discrepancies (24h)[/bold cyan]")
            console.print(f"Significant: {disc_stats['last_24h_significant']}")
            console.print(f"Potential Arbitrage: {disc_stats['potential_arbitrage']}")
        except Exception:
            pass

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
