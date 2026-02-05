"""Cross-source price validation and discrepancy detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from rich.console import Console

from archantum.config import settings
from archantum.data.source_manager import PriceResult

if TYPE_CHECKING:
    from archantum.db import Database


console = Console()


@dataclass
class ValidationResult:
    """Result of cross-source price validation."""

    market_id: str
    timestamp: datetime

    # WebSocket prices
    websocket_yes: float | None
    websocket_no: float | None

    # REST API prices
    rest_yes: float | None
    rest_no: float | None

    # Discrepancy analysis
    yes_diff_pct: float | None
    no_diff_pct: float | None
    max_diff_pct: float | None

    # Flags
    is_significant: bool  # > 2%
    potential_arbitrage: bool  # > 3%

    # Market info for alerts
    question: str = ""
    polymarket_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "market_id": self.market_id,
            "timestamp": self.timestamp.isoformat(),
            "websocket_yes": self.websocket_yes,
            "websocket_no": self.websocket_no,
            "rest_yes": self.rest_yes,
            "rest_no": self.rest_no,
            "yes_diff_pct": self.yes_diff_pct,
            "no_diff_pct": self.no_diff_pct,
            "max_diff_pct": self.max_diff_pct,
            "is_significant": self.is_significant,
            "potential_arbitrage": self.potential_arbitrage,
            "question": self.question,
            "polymarket_url": self.polymarket_url,
        }


class PriceValidator:
    """Validate prices across multiple data sources."""

    def __init__(self, db: "Database"):
        self.db = db
        self._significant_threshold = settings.price_discrepancy_threshold  # 2%
        self._arbitrage_threshold = 0.03  # 3%

    async def validate_prices(
        self,
        websocket_price: PriceResult | None,
        rest_price: PriceResult | None,
        market_id: str,
        question: str = "",
        polymarket_url: str | None = None,
    ) -> ValidationResult | None:
        """Validate prices from different sources.

        Args:
            websocket_price: Price from WebSocket
            rest_price: Price from REST API
            market_id: The market ID
            question: Market question for alerts
            polymarket_url: URL to market

        Returns:
            ValidationResult if comparison possible, None otherwise
        """
        # Need at least one price from each source
        if not websocket_price or not rest_price:
            return None

        # Need at least yes prices from both
        if websocket_price.yes_price is None or rest_price.yes_price is None:
            return None

        # Calculate discrepancies
        yes_diff_pct = self._calculate_diff_pct(
            websocket_price.yes_price,
            rest_price.yes_price,
        )

        no_diff_pct = None
        if websocket_price.no_price is not None and rest_price.no_price is not None:
            no_diff_pct = self._calculate_diff_pct(
                websocket_price.no_price,
                rest_price.no_price,
            )

        # Determine max discrepancy
        max_diff_pct = yes_diff_pct
        if no_diff_pct is not None and no_diff_pct > yes_diff_pct:
            max_diff_pct = no_diff_pct

        # Check thresholds
        is_significant = max_diff_pct >= self._significant_threshold * 100
        potential_arbitrage = max_diff_pct >= self._arbitrage_threshold * 100

        result = ValidationResult(
            market_id=market_id,
            timestamp=datetime.utcnow(),
            websocket_yes=websocket_price.yes_price,
            websocket_no=websocket_price.no_price,
            rest_yes=rest_price.yes_price,
            rest_no=rest_price.no_price,
            yes_diff_pct=yes_diff_pct,
            no_diff_pct=no_diff_pct,
            max_diff_pct=max_diff_pct,
            is_significant=is_significant,
            potential_arbitrage=potential_arbitrage,
            question=question,
            polymarket_url=polymarket_url,
        )

        # Save to database if significant
        if is_significant:
            await self._save_discrepancy(result)

        return result

    async def validate_batch(
        self,
        websocket_prices: dict[str, PriceResult],
        rest_prices: dict[str, PriceResult],
        market_info: dict[str, dict],
    ) -> list[ValidationResult]:
        """Validate prices for multiple markets.

        Args:
            websocket_prices: Dict of market_id -> PriceResult from WebSocket
            rest_prices: Dict of market_id -> PriceResult from REST
            market_info: Dict of market_id -> {'question', 'polymarket_url'}

        Returns:
            List of ValidationResults with significant discrepancies
        """
        significant_results = []

        # Find markets with prices from both sources
        common_markets = set(websocket_prices.keys()) & set(rest_prices.keys())

        for market_id in common_markets:
            ws_price = websocket_prices.get(market_id)
            rest_price = rest_prices.get(market_id)
            info = market_info.get(market_id, {})

            result = await self.validate_prices(
                websocket_price=ws_price,
                rest_price=rest_price,
                market_id=market_id,
                question=info.get("question", ""),
                polymarket_url=info.get("polymarket_url"),
            )

            if result and result.is_significant:
                significant_results.append(result)

        return significant_results

    def _calculate_diff_pct(self, price1: float, price2: float) -> float:
        """Calculate percentage difference between two prices."""
        if price1 == 0 and price2 == 0:
            return 0.0

        avg = (price1 + price2) / 2
        if avg == 0:
            return 0.0

        diff = abs(price1 - price2)
        return (diff / avg) * 100

    async def _save_discrepancy(self, result: ValidationResult) -> None:
        """Save price discrepancy to database."""
        from archantum.db.models import PriceDiscrepancy

        try:
            async with self.db.async_session() as session:
                record = PriceDiscrepancy(
                    market_id=result.market_id,
                    timestamp=result.timestamp,
                    websocket_yes=result.websocket_yes,
                    websocket_no=result.websocket_no,
                    rest_yes=result.rest_yes,
                    rest_no=result.rest_no,
                    max_diff_pct=result.max_diff_pct,
                    is_significant=result.is_significant,
                    potential_arbitrage=result.potential_arbitrage,
                )
                session.add(record)
                await session.commit()
        except Exception as e:
            console.print(f"[yellow]Failed to save discrepancy: {e}[/yellow]")

    async def get_recent_discrepancies(
        self,
        limit: int = 50,
        significant_only: bool = True,
    ) -> list[ValidationResult]:
        """Get recent price discrepancies from database."""
        from sqlalchemy import select
        from archantum.db.models import PriceDiscrepancy

        async with self.db.async_session() as session:
            query = select(PriceDiscrepancy)

            if significant_only:
                query = query.where(PriceDiscrepancy.is_significant == True)

            query = query.order_by(PriceDiscrepancy.timestamp.desc()).limit(limit)

            result = await session.execute(query)
            records = result.scalars().all()

            return [
                ValidationResult(
                    market_id=r.market_id,
                    timestamp=r.timestamp,
                    websocket_yes=r.websocket_yes,
                    websocket_no=r.websocket_no,
                    rest_yes=r.rest_yes,
                    rest_no=r.rest_no,
                    yes_diff_pct=None,  # Not stored
                    no_diff_pct=None,  # Not stored
                    max_diff_pct=r.max_diff_pct,
                    is_significant=r.is_significant,
                    potential_arbitrage=r.potential_arbitrage,
                )
                for r in records
            ]

    async def get_discrepancy_stats(self) -> dict[str, Any]:
        """Get statistics about price discrepancies."""
        from sqlalchemy import select, func
        from datetime import timedelta
        from archantum.db.models import PriceDiscrepancy

        async with self.db.async_session() as session:
            # Total count
            total_result = await session.execute(
                select(func.count(PriceDiscrepancy.id))
            )
            total = total_result.scalar_one() or 0

            # Significant count
            significant_result = await session.execute(
                select(func.count(PriceDiscrepancy.id))
                .where(PriceDiscrepancy.is_significant == True)
            )
            significant = significant_result.scalar_one() or 0

            # Arbitrage opportunities count
            arbitrage_result = await session.execute(
                select(func.count(PriceDiscrepancy.id))
                .where(PriceDiscrepancy.potential_arbitrage == True)
            )
            arbitrage = arbitrage_result.scalar_one() or 0

            # Average discrepancy
            avg_result = await session.execute(
                select(func.avg(PriceDiscrepancy.max_diff_pct))
                .where(PriceDiscrepancy.is_significant == True)
            )
            avg_discrepancy = avg_result.scalar_one() or 0

            # Last 24h stats
            cutoff = datetime.utcnow() - timedelta(hours=24)
            recent_result = await session.execute(
                select(func.count(PriceDiscrepancy.id))
                .where(PriceDiscrepancy.timestamp >= cutoff)
                .where(PriceDiscrepancy.is_significant == True)
            )
            recent_significant = recent_result.scalar_one() or 0

            return {
                "total_discrepancies": total,
                "significant_discrepancies": significant,
                "potential_arbitrage": arbitrage,
                "average_discrepancy_pct": round(avg_discrepancy, 2),
                "last_24h_significant": recent_significant,
            }
