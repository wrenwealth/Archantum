"""Gamma API client for market discovery."""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from archantum.config import settings


class GammaMarket(BaseModel):
    """Market data from Gamma API."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    condition_id: str | None = Field(default=None, alias="conditionId")
    question: str
    slug: str | None = None
    event_slug: str | None = Field(default=None, alias="eventSlug")
    outcomes: list[str] | None = None
    outcome_prices: list[str] | None = Field(default=None, alias="outcomePrices")
    volume: float | None = None
    volume_24hr: float | None = Field(default=None, alias="volume24hr")
    liquidity: float | None = None
    active: bool = True
    closed: bool = False
    clob_token_ids: list[str] | None = Field(default=None, alias="clobTokenIds")
    end_date: str | None = Field(default=None, alias="endDate")
    events: list[dict[str, Any]] | None = None

    @field_validator("outcomes", "outcome_prices", "clob_token_ids", mode="before")
    @classmethod
    def parse_json_string(cls, v):
        """Parse JSON string to list if needed."""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return None
        return v

    @property
    def yes_token_id(self) -> str | None:
        """Get the YES outcome token ID."""
        if self.clob_token_ids and self.outcomes:
            try:
                idx = self.outcomes.index("Yes")
                return self.clob_token_ids[idx]
            except (ValueError, IndexError):
                pass
        return None

    @property
    def no_token_id(self) -> str | None:
        """Get the NO outcome token ID."""
        if self.clob_token_ids and self.outcomes:
            try:
                idx = self.outcomes.index("No")
                return self.clob_token_ids[idx]
            except (ValueError, IndexError):
                pass
        return None

    @property
    def polymarket_url(self) -> str | None:
        """Get the Polymarket URL for this market."""
        # Use event slug if available (most accurate)
        if self.events and len(self.events) > 0:
            event_slug = self.events[0].get("slug")
            if event_slug:
                return f"https://polymarket.com/event/{event_slug}"
        # Fallback to market slug
        if self.slug:
            return f"https://polymarket.com/event/{self.slug}"
        return None


class GammaClient:
    """Client for the Gamma API (market discovery)."""

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or settings.gamma_api_base_url
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
        closed: bool = False,
        active: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[GammaMarket]:
        """Fetch markets from Gamma API with pagination."""
        params = {
            "closed": str(closed).lower(),
            "active": str(active).lower(),
            "limit": limit,
            "offset": offset,
        }

        response = await self.client.get("/markets", params=params)
        response.raise_for_status()
        data = response.json()

        return [GammaMarket.model_validate(m) for m in data]

    async def get_all_active_markets(self, limit_per_page: int = 100) -> list[GammaMarket]:
        """Fetch all active markets with automatic pagination."""
        all_markets: list[GammaMarket] = []
        offset = 0

        while True:
            markets = await self.get_markets(
                closed=False,
                active=True,
                limit=limit_per_page,
                offset=offset,
            )

            if not markets:
                break

            all_markets.extend(markets)
            offset += limit_per_page

            if len(markets) < limit_per_page:
                break

        return all_markets

    async def get_top_markets(
        self,
        min_volume_24hr: float | None = None,
        max_markets: int | None = None,
    ) -> list[GammaMarket]:
        """Fetch top active markets filtered by volume.

        Args:
            min_volume_24hr: Minimum 24h volume (default from settings)
            max_markets: Maximum markets to return (default from settings)
        """
        min_vol = min_volume_24hr if min_volume_24hr is not None else settings.min_volume_24hr
        max_count = max_markets if max_markets is not None else settings.max_markets

        all_markets: list[GammaMarket] = []
        offset = 0
        limit_per_page = 100

        # Fetch until we have enough high-volume markets
        while len(all_markets) < max_count * 2:  # Fetch extra to filter
            markets = await self.get_markets(
                closed=False,
                active=True,
                limit=limit_per_page,
                offset=offset,
            )

            if not markets:
                break

            # Filter by minimum volume
            for m in markets:
                vol = m.volume_24hr or 0
                if vol >= min_vol:
                    all_markets.append(m)

            offset += limit_per_page

            if len(markets) < limit_per_page:
                break

        # Sort by 24h volume descending and limit
        all_markets.sort(key=lambda m: m.volume_24hr or 0, reverse=True)
        return all_markets[:max_count]

    async def get_events(
        self,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Fetch events with associated markets."""
        params = {
            "closed": str(closed).lower(),
            "limit": limit,
            "offset": offset,
        }

        response = await self.client.get("/events", params=params)
        response.raise_for_status()
        return response.json()

    async def get_event_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Fetch a specific event by its slug."""
        try:
            response = await self.client.get("/events", params={"slug": slug})
            response.raise_for_status()
            events = response.json()
            if events and len(events) > 0:
                return events[0]
            return None
        except httpx.HTTPStatusError:
            return None

    async def get_markets_by_event_slug(self, event_slug: str) -> list[GammaMarket]:
        """Fetch all markets for a specific event."""
        event = await self.get_event_by_slug(event_slug)
        if not event:
            return []

        # Extract markets from event and add event info to each market
        markets_data = event.get("markets", [])
        event_info = {"slug": event.get("slug"), "title": event.get("title")}

        markets = []
        for m in markets_data:
            # Add events array so polymarket_url works correctly
            m["events"] = [event_info]
            markets.append(GammaMarket.model_validate(m))

        return markets

    async def search_markets(self, query: str, limit: int = 20) -> list[GammaMarket]:
        """Search markets by text query."""
        # Gamma API doesn't have a search endpoint, so we fetch and filter
        all_markets = await self.get_markets(limit=500)
        query_lower = query.lower()

        matching = [
            m for m in all_markets
            if query_lower in m.question.lower()
            or (m.slug and query_lower in m.slug.lower())
        ]

        return matching[:limit]
