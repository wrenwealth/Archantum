"""Kalshi API client for cross-platform arbitrage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field


@dataclass
class KalshiPriceData:
    """Price data from Kalshi."""

    ticker: str
    title: str
    yes_bid: float | None  # Best bid for YES
    yes_ask: float | None  # Best ask for YES
    no_bid: float | None   # Best bid for NO
    no_ask: float | None   # Best ask for NO
    last_price: float | None
    volume: int
    status: str

    @property
    def yes_price(self) -> float | None:
        """Mid price for YES (or last price as fallback)."""
        if self.yes_bid is not None and self.yes_ask is not None:
            return (self.yes_bid + self.yes_ask) / 2
        return self.last_price

    @property
    def no_price(self) -> float | None:
        """Mid price for NO (1 - yes_price for binary markets)."""
        yes = self.yes_price
        if yes is not None:
            return 1.0 - yes
        return None


class KalshiMarket(BaseModel):
    """Market data from Kalshi API."""

    ticker: str
    title: str
    subtitle: str | None = None
    event_ticker: str | None = None
    series_ticker: str | None = None
    status: str = "open"
    yes_bid: float | None = Field(default=None, alias="yes_bid_dollars")
    yes_ask: float | None = Field(default=None, alias="yes_ask_dollars")
    no_bid: float | None = Field(default=None, alias="no_bid_dollars")
    no_ask: float | None = Field(default=None, alias="no_ask_dollars")
    last_price: float | None = Field(default=None, alias="last_price_dollars")
    volume: int = 0
    open_interest: int = 0
    close_time: str | None = None
    expiration_time: str | None = None

    @property
    def kalshi_url(self) -> str:
        """Get the Kalshi URL for this market."""
        return f"https://kalshi.com/markets/{self.ticker}"

    def to_price_data(self) -> KalshiPriceData:
        """Convert to KalshiPriceData."""
        return KalshiPriceData(
            ticker=self.ticker,
            title=self.title,
            yes_bid=self.yes_bid,
            yes_ask=self.yes_ask,
            no_bid=self.no_bid,
            no_ask=self.no_ask,
            last_price=self.last_price,
            volume=self.volume,
            status=self.status,
        )


class KalshiClient:
    """Client for the Kalshi API (market data - no auth required)."""

    BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or self.BASE_URL
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=30.0,
            headers={"Accept": "application/json"},
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")
        return self._client

    async def get_markets(
        self,
        status: str = "open",
        limit: int = 200,
        cursor: str | None = None,
        event_ticker: str | None = None,
        series_ticker: str | None = None,
    ) -> tuple[list[KalshiMarket], str | None]:
        """Fetch markets from Kalshi API.

        Args:
            status: Market status filter (open, closed, settled)
            limit: Max results per page (max 1000)
            cursor: Pagination cursor
            event_ticker: Filter by event
            series_ticker: Filter by series

        Returns:
            Tuple of (markets list, next cursor or None)
        """
        params: dict[str, Any] = {
            "status": status,
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker

        response = await self.client.get("/markets", params=params)
        response.raise_for_status()
        data = response.json()

        markets = [KalshiMarket.model_validate(m) for m in data.get("markets", [])]
        next_cursor = data.get("cursor")

        return markets, next_cursor

    async def get_all_open_markets(self, max_markets: int = 500) -> list[KalshiMarket]:
        """Fetch all open markets with pagination.

        Args:
            max_markets: Maximum number of markets to fetch

        Returns:
            List of open markets
        """
        all_markets: list[KalshiMarket] = []
        cursor = None

        while len(all_markets) < max_markets:
            markets, cursor = await self.get_markets(
                status="open",
                limit=min(200, max_markets - len(all_markets)),
                cursor=cursor,
            )

            if not markets:
                break

            all_markets.extend(markets)

            if not cursor:
                break

        return all_markets[:max_markets]

    async def get_market(self, ticker: str) -> KalshiMarket | None:
        """Fetch a specific market by ticker."""
        try:
            response = await self.client.get(f"/markets/{ticker}")
            response.raise_for_status()
            data = response.json()
            return KalshiMarket.model_validate(data.get("market", data))
        except httpx.HTTPStatusError:
            return None

    async def get_orderbook(self, ticker: str) -> dict[str, Any] | None:
        """Fetch orderbook for a market."""
        try:
            response = await self.client.get(f"/markets/{ticker}/orderbook")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError:
            return None

    async def search_markets(self, query: str, limit: int = 20) -> list[KalshiMarket]:
        """Search markets by text (searches in title)."""
        all_markets = await self.get_all_open_markets(max_markets=500)
        query_lower = query.lower()

        matching = [
            m for m in all_markets
            if query_lower in m.title.lower()
            or (m.subtitle and query_lower in m.subtitle.lower())
        ]

        return matching[:limit]
