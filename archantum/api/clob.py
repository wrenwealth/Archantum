"""CLOB API client for price and orderbook data."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from archantum.config import settings


class OrderbookLevel(BaseModel):
    """Single level in an orderbook."""

    price: float
    size: float


class Orderbook(BaseModel):
    """Orderbook data for a token."""

    bids: list[OrderbookLevel] = []
    asks: list[OrderbookLevel] = []

    @property
    def best_bid(self) -> float | None:
        """Get the best bid price."""
        if self.bids:
            return max(level.price for level in self.bids)
        return None

    @property
    def best_ask(self) -> float | None:
        """Get the best ask price."""
        if self.asks:
            return min(level.price for level in self.asks)
        return None

    @property
    def spread(self) -> float | None:
        """Calculate bid-ask spread."""
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None


class CLOBMarket(BaseModel):
    """Market data from CLOB API."""

    condition_id: str
    tokens: list[dict[str, Any]] = []


class PriceData(BaseModel):
    """Price data for a market."""

    market_id: str
    yes_price: float | None = None
    no_price: float | None = None
    yes_bid: float | None = None
    yes_ask: float | None = None
    no_bid: float | None = None
    no_ask: float | None = None

    @property
    def total_price(self) -> float | None:
        """Calculate total of yes + no prices."""
        if self.yes_price is not None and self.no_price is not None:
            return self.yes_price + self.no_price
        return None

    @property
    def spread(self) -> float | None:
        """Calculate arbitrage spread from 1.0."""
        total = self.total_price
        if total is not None:
            return abs(1.0 - total)
        return None


class CLOBClient:
    """Client for the CLOB API (price/orderbook data)."""

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or settings.clob_api_base_url
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

    async def get_markets(self) -> list[CLOBMarket]:
        """Get simplified markets with prices."""
        response = await self.client.get("/markets")
        response.raise_for_status()
        data = response.json()

        return [CLOBMarket.model_validate(m) for m in data]

    async def get_orderbook(self, token_id: str) -> Orderbook:
        """Get orderbook for a specific token."""
        response = await self.client.get("/book", params={"token_id": token_id})
        response.raise_for_status()
        data = response.json()

        bids = [
            OrderbookLevel(price=float(b["price"]), size=float(b["size"]))
            for b in data.get("bids", [])
        ]
        asks = [
            OrderbookLevel(price=float(a["price"]), size=float(a["size"]))
            for a in data.get("asks", [])
        ]

        return Orderbook(bids=bids, asks=asks)

    async def get_midpoint(self, token_id: str) -> float | None:
        """Get midpoint price for a token."""
        response = await self.client.get("/midpoint", params={"token_id": token_id})
        response.raise_for_status()
        data = response.json()

        mid = data.get("mid")
        if mid is not None:
            return float(mid)
        return None

    async def get_price_for_market(
        self,
        yes_token_id: str | None,
        no_token_id: str | None,
        market_id: str,
    ) -> PriceData:
        """Get complete price data for a market."""
        price_data = PriceData(market_id=market_id)

        if yes_token_id:
            try:
                yes_book = await self.get_orderbook(yes_token_id)
                price_data.yes_bid = yes_book.best_bid
                price_data.yes_ask = yes_book.best_ask
                midpoint = await self.get_midpoint(yes_token_id)
                price_data.yes_price = midpoint
            except httpx.HTTPError:
                pass

        if no_token_id:
            try:
                no_book = await self.get_orderbook(no_token_id)
                price_data.no_bid = no_book.best_bid
                price_data.no_ask = no_book.best_ask
                midpoint = await self.get_midpoint(no_token_id)
                price_data.no_price = midpoint
            except httpx.HTTPError:
                pass

        return price_data
