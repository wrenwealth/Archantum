"""Rich CLI dashboard for monitoring."""

from __future__ import annotations

import asyncio
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.text import Text

from archantum.db import Database, Alert


class Dashboard:
    """Rich CLI dashboard for monitoring markets and alerts."""

    def __init__(self, db: Database):
        self.db = db
        self.console = Console()
        self.last_poll: datetime | None = None
        self.active_opportunities: list[dict] = []

    def set_last_poll(self, timestamp: datetime):
        """Update last poll timestamp."""
        self.last_poll = timestamp

    def set_opportunities(self, opportunities: list[dict]):
        """Update active opportunities."""
        self.active_opportunities = opportunities

    async def generate_layout(self) -> Layout:
        """Generate the dashboard layout."""
        layout = Layout()

        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=3),
        )

        layout["body"].split_row(
            Layout(name="overview", ratio=1),
            Layout(name="opportunities", ratio=2),
        )

        # Header
        layout["header"].update(
            Panel(
                Text("ARCHANTUM - Polymarket Analysis Agent", justify="center", style="bold cyan"),
                style="cyan",
            )
        )

        # Overview panel
        overview = await self._build_overview_panel()
        layout["overview"].update(overview)

        # Opportunities panel
        opportunities = await self._build_opportunities_panel()
        layout["opportunities"].update(opportunities)

        # Footer
        layout["footer"].update(
            Panel(
                Text("Press Ctrl+C to exit | Refresh: 5s", justify="center", style="dim"),
                style="dim",
            )
        )

        return layout

    async def _build_overview_panel(self) -> Panel:
        """Build the market overview panel."""
        market_count = await self.db.get_market_count()
        alerts_today = await self.db.get_alerts_today()

        last_poll_str = "Never"
        if self.last_poll:
            delta = datetime.utcnow() - self.last_poll
            last_poll_str = f"{int(delta.total_seconds())}s ago"

        content = Table.grid(padding=(0, 2))
        content.add_column(style="bold")
        content.add_column()

        content.add_row("Active Markets:", str(market_count))
        content.add_row("Last Poll:", last_poll_str)
        content.add_row("Alerts Today:", str(len(alerts_today)))

        # Count by type
        arb_count = sum(1 for a in alerts_today if a.alert_type == "arbitrage")
        vol_count = sum(1 for a in alerts_today if a.alert_type == "volume_spike")
        price_count = sum(1 for a in alerts_today if a.alert_type == "price_move")

        content.add_row("", "")
        content.add_row("  Arbitrage:", str(arb_count))
        content.add_row("  Volume:", str(vol_count))
        content.add_row("  Price Move:", str(price_count))

        return Panel(content, title="Market Overview", border_style="green")

    async def _build_opportunities_panel(self) -> Panel:
        """Build the active opportunities panel."""
        table = Table(
            title="Recent Alerts",
            expand=True,
            show_header=True,
            header_style="bold magenta",
        )

        table.add_column("Market", style="cyan", no_wrap=True, max_width=40)
        table.add_column("Type", style="yellow")
        table.add_column("Value", style="green")
        table.add_column("Time", style="dim")

        # Get recent alerts
        alerts = await self.db.get_recent_alerts(limit=10)

        for alert in alerts:
            market = await self.db.get_market(alert.market_id)
            market_name = market.question[:35] + "..." if market else alert.market_id[:35]

            # Format alert type
            type_colors = {
                "arbitrage": "[red]Arbitrage[/red]",
                "volume_spike": "[yellow]Vol Spike[/yellow]",
                "price_move": "[blue]Price Move[/blue]",
                "trend": "[magenta]Trend[/magenta]",
            }
            alert_type = type_colors.get(alert.alert_type, alert.alert_type)

            # Format value based on type
            value = self._format_alert_value(alert)

            # Format time
            delta = datetime.utcnow() - alert.timestamp
            if delta.total_seconds() < 60:
                time_str = f"{int(delta.total_seconds())}s ago"
            elif delta.total_seconds() < 3600:
                time_str = f"{int(delta.total_seconds() / 60)}m ago"
            else:
                time_str = f"{int(delta.total_seconds() / 3600)}h ago"

            table.add_row(market_name, alert_type, value, time_str)

        if not alerts:
            table.add_row("No alerts yet", "-", "-", "-")

        return Panel(table, title="Active Opportunities", border_style="blue")

    def _format_alert_value(self, alert: Alert) -> str:
        """Format the value column based on alert type."""
        import json

        if not alert.details:
            return "-"

        try:
            details = json.loads(alert.details)
        except json.JSONDecodeError:
            return "-"

        if alert.alert_type == "arbitrage":
            return f"{details.get('arbitrage_pct', 0):.1f}%"
        elif alert.alert_type == "volume_spike":
            return f"{details.get('spike_multiplier', 0):.1f}x"
        elif alert.alert_type == "price_move":
            return f"{details.get('price_change_pct', 0):+.1f}%"
        elif alert.alert_type == "trend":
            return details.get("signal", "-")

        return "-"

    async def run(self, refresh_rate: float = 5.0):
        """Run the dashboard with live updates."""
        with Live(await self.generate_layout(), refresh_per_second=1, console=self.console) as live:
            try:
                while True:
                    await asyncio.sleep(refresh_rate)
                    live.update(await self.generate_layout())
            except KeyboardInterrupt:
                pass

    def print_static(self):
        """Print a static snapshot of the dashboard."""
        asyncio.run(self._print_static_async())

    async def _print_static_async(self):
        """Async version of print_static."""
        layout = await self.generate_layout()
        self.console.print(layout)
