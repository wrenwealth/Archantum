"""Multi-source data manager with failover support."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from rich.console import Console

from archantum.config import settings
from archantum.api.websocket import PolymarketWebSocket, PriceUpdate
from archantum.api.clob import CLOBClient, PriceData

if TYPE_CHECKING:
    from archantum.db import Database


console = Console()


@dataclass
class SourceHealth:
    """Health metrics for a data source."""

    name: str
    success_count: int = 0
    failure_count: int = 0
    total_latency_ms: float = 0.0
    last_success: datetime | None = None
    last_failure: datetime | None = None
    last_error: str | None = None

    @property
    def total_requests(self) -> int:
        return self.success_count + self.failure_count

    @property
    def reliability_score(self) -> float:
        """Calculate reliability score (0-100)."""
        if self.total_requests == 0:
            return 100.0
        return (self.success_count / self.total_requests) * 100

    @property
    def average_latency_ms(self) -> float:
        """Calculate average latency in milliseconds."""
        if self.success_count == 0:
            return 0.0
        return self.total_latency_ms / self.success_count

    def record_success(self, latency_ms: float) -> None:
        """Record a successful request."""
        self.success_count += 1
        self.total_latency_ms += latency_ms
        self.last_success = datetime.utcnow()

    def record_failure(self, error: str) -> None:
        """Record a failed request."""
        self.failure_count += 1
        self.last_failure = datetime.utcnow()
        self.last_error = error

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "reliability_score": round(self.reliability_score, 2),
            "average_latency_ms": round(self.average_latency_ms, 2),
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "last_failure": self.last_failure.isoformat() if self.last_failure else None,
            "last_error": self.last_error,
        }


@dataclass
class PriceResult:
    """Result from price fetch with source information."""

    market_id: str
    yes_price: float | None
    no_price: float | None
    yes_bid: float | None = None
    yes_ask: float | None = None
    no_bid: float | None = None
    no_ask: float | None = None
    source: str = "unknown"  # 'websocket', 'rest', 'cache'
    latency_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def spread(self) -> float | None:
        """Calculate arbitrage spread from 1.0."""
        if self.yes_price is not None and self.no_price is not None:
            return abs(1.0 - (self.yes_price + self.no_price))
        return None

    def to_price_data(self) -> PriceData:
        """Convert to PriceData for compatibility."""
        return PriceData(
            market_id=self.market_id,
            yes_price=self.yes_price,
            no_price=self.no_price,
            yes_bid=self.yes_bid,
            yes_ask=self.yes_ask,
            no_bid=self.no_bid,
            no_ask=self.no_ask,
        )


@dataclass
class CachedPrice:
    """Cached price data with timestamp."""

    price_result: PriceResult
    cached_at: datetime

    def is_stale(self, max_age_seconds: float) -> bool:
        """Check if cache entry is stale."""
        age = (datetime.utcnow() - self.cached_at).total_seconds()
        return age > max_age_seconds


class DataSourceManager:
    """Manages multiple data sources with failover."""

    def __init__(self, db: "Database"):
        self.db = db
        self.websocket = PolymarketWebSocket()
        self._clob_client: CLOBClient | None = None
        self._cache: dict[str, CachedPrice] = {}

        # Health tracking
        self._source_health: dict[str, SourceHealth] = {
            "websocket": SourceHealth(name="websocket"),
            "rest": SourceHealth(name="rest"),
            "cache": SourceHealth(name="cache"),
        }

        # Callback registration
        self.websocket.on_price_update = self._on_websocket_price
        self.websocket.on_state_change = self._on_websocket_state_change

    async def initialize(self) -> None:
        """Initialize data sources."""
        # Connect WebSocket if enabled
        if settings.ws_enabled:
            try:
                await self.websocket.connect()
            except Exception as e:
                console.print(f"[yellow]WebSocket initialization failed: {e}[/yellow]")
                self._source_health["websocket"].record_failure(str(e))

        # Initialize CLOB client
        self._clob_client = CLOBClient()

    async def close(self) -> None:
        """Close all data source connections."""
        await self.websocket.disconnect()

    async def subscribe_market(
        self,
        market_id: str,
        yes_token: str | None,
        no_token: str | None,
    ) -> None:
        """Subscribe to real-time updates for a market."""
        await self.websocket.subscribe_market(market_id, yes_token, no_token)

    async def subscribe_markets(self, markets: list[dict]) -> None:
        """Subscribe to multiple markets.

        Args:
            markets: List of dicts with 'id', 'yes_token', 'no_token' keys
        """
        await self.websocket.subscribe_markets(markets)

    async def get_price(
        self,
        market_id: str,
        yes_token: str | None = None,
        no_token: str | None = None,
    ) -> PriceResult:
        """Get price for a market using failover chain.

        Failover order: WebSocket -> REST API -> Cache

        Args:
            market_id: The market ID
            yes_token: Yes outcome token ID
            no_token: No outcome token ID

        Returns:
            PriceResult with source information
        """
        # 1. Try WebSocket (cached prices)
        result = await self._try_websocket(market_id)
        if result:
            return result

        # 2. Try REST API
        result = await self._try_rest(market_id, yes_token, no_token)
        if result:
            # Update cache
            self._cache[market_id] = CachedPrice(
                price_result=result,
                cached_at=datetime.utcnow(),
            )
            return result

        # 3. Fall back to cache
        result = self._try_cache(market_id)
        if result:
            return result

        # No data available
        return PriceResult(
            market_id=market_id,
            yes_price=None,
            no_price=None,
            source="none",
        )

    async def get_prices_batch(
        self,
        markets: list[dict],
    ) -> dict[str, PriceResult]:
        """Get prices for multiple markets.

        Args:
            markets: List of dicts with 'id', 'yes_token', 'no_token' keys

        Returns:
            Dict of market_id -> PriceResult
        """
        results = {}

        for market in markets:
            market_id = market.get("id", "")
            if not market_id:
                continue

            result = await self.get_price(
                market_id=market_id,
                yes_token=market.get("yes_token"),
                no_token=market.get("no_token"),
            )
            results[market_id] = result

        return results

    async def _try_websocket(self, market_id: str) -> PriceResult | None:
        """Try to get price from WebSocket cache."""
        if not self.websocket.stats.is_connected:
            return None

        start_time = time.time()

        yes_update = self.websocket.get_cached_price(market_id, "yes")
        no_update = self.websocket.get_cached_price(market_id, "no")

        if not yes_update and not no_update:
            return None

        # Check if data is too old
        max_age = timedelta(seconds=settings.cache_max_age_seconds)
        now = datetime.utcnow()

        if yes_update and (now - yes_update.timestamp) > max_age:
            yes_update = None
        if no_update and (now - no_update.timestamp) > max_age:
            no_update = None

        if not yes_update and not no_update:
            return None

        latency_ms = (time.time() - start_time) * 1000
        self._source_health["websocket"].record_success(latency_ms)

        result = PriceResult(
            market_id=market_id,
            yes_price=yes_update.price if yes_update else None,
            no_price=no_update.price if no_update else None,
            source="websocket",
            latency_ms=latency_ms,
            timestamp=max(
                yes_update.timestamp if yes_update else datetime.min,
                no_update.timestamp if no_update else datetime.min,
            ),
        )

        # Log to database
        await self._log_request("websocket", market_id, latency_ms, True)

        return result

    async def _try_rest(
        self,
        market_id: str,
        yes_token: str | None,
        no_token: str | None,
    ) -> PriceResult | None:
        """Try to get price from REST API."""
        if not yes_token and not no_token:
            return None

        start_time = time.time()

        try:
            async with CLOBClient() as client:
                price_data = await client.get_price_for_market(
                    yes_token_id=yes_token,
                    no_token_id=no_token,
                    market_id=market_id,
                )

            latency_ms = (time.time() - start_time) * 1000
            self._source_health["rest"].record_success(latency_ms)

            result = PriceResult(
                market_id=market_id,
                yes_price=price_data.yes_price,
                no_price=price_data.no_price,
                yes_bid=price_data.yes_bid,
                yes_ask=price_data.yes_ask,
                no_bid=price_data.no_bid,
                no_ask=price_data.no_ask,
                source="rest",
                latency_ms=latency_ms,
            )

            # Log to database
            await self._log_request("rest", market_id, latency_ms, True)

            return result

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._source_health["rest"].record_failure(str(e))
            await self._log_request("rest", market_id, latency_ms, False, str(e))
            return None

    def _try_cache(self, market_id: str) -> PriceResult | None:
        """Try to get price from local cache."""
        cached = self._cache.get(market_id)
        if not cached:
            return None

        if cached.is_stale(settings.cache_max_age_seconds):
            del self._cache[market_id]
            return None

        self._source_health["cache"].record_success(0)

        # Return with updated source
        result = cached.price_result
        return PriceResult(
            market_id=result.market_id,
            yes_price=result.yes_price,
            no_price=result.no_price,
            yes_bid=result.yes_bid,
            yes_ask=result.yes_ask,
            no_bid=result.no_bid,
            no_ask=result.no_ask,
            source="cache",
            latency_ms=0,
            timestamp=cached.cached_at,
        )

    async def _on_websocket_price(self, update: PriceUpdate) -> None:
        """Handle WebSocket price update callback."""
        # Price is automatically cached by WebSocket class
        # We could log or process updates here if needed
        pass

    async def _on_websocket_state_change(self, state: str, details: dict) -> None:
        """Handle WebSocket state change callback."""
        if state == "connected":
            console.print("[green]Data source: WebSocket connected[/green]")
        elif state == "disconnected":
            console.print("[yellow]Data source: WebSocket disconnected[/yellow]")
            self._source_health["websocket"].record_failure("disconnected")
        elif state == "failed":
            console.print(f"[red]Data source: WebSocket failed - {details}[/red]")
            self._source_health["websocket"].record_failure(str(details))

    async def _log_request(
        self,
        source_type: str,
        market_id: str | None,
        latency_ms: float,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Log a data source request to database."""
        from archantum.db.models import DataSourceLog

        try:
            async with self.db.async_session() as session:
                log = DataSourceLog(
                    source_type=source_type,
                    market_id=market_id,
                    request_timestamp=datetime.utcnow(),
                    response_time_ms=latency_ms,
                    success=success,
                    error_message=error,
                )
                session.add(log)
                await session.commit()
        except Exception:
            pass  # Don't fail on logging errors

    def get_health_report(self) -> dict[str, Any]:
        """Get health report for all data sources."""
        return {
            "websocket": {
                **self._source_health["websocket"].to_dict(),
                "connection": self.websocket.get_health_report(),
            },
            "rest": self._source_health["rest"].to_dict(),
            "cache": {
                **self._source_health["cache"].to_dict(),
                "entries": len(self._cache),
            },
            "summary": {
                "primary_source": self._get_primary_source(),
                "total_requests": sum(
                    h.total_requests for h in self._source_health.values()
                ),
                "overall_reliability": self._calculate_overall_reliability(),
            },
        }

    def _get_primary_source(self) -> str:
        """Determine which source is currently primary."""
        if self.websocket.stats.is_connected:
            return "websocket"
        if self._source_health["rest"].reliability_score > 50:
            return "rest"
        return "cache"

    def _calculate_overall_reliability(self) -> float:
        """Calculate overall reliability across all sources."""
        total_success = sum(h.success_count for h in self._source_health.values())
        total_requests = sum(h.total_requests for h in self._source_health.values())
        if total_requests == 0:
            return 100.0
        return (total_success / total_requests) * 100
