"""Polymarket Data API client for leaderboard and activity."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field


class LeaderboardEntry(BaseModel):
    """Trader entry from leaderboard."""

    rank: str
    proxy_wallet: str = Field(alias="proxyWallet")
    username: str = Field(alias="userName")
    x_username: str = Field(default="", alias="xUsername")
    volume: float = Field(alias="vol")
    pnl: float
    profile_image: str = Field(default="", alias="profileImage")

    @property
    def wallet_short(self) -> str:
        """Get shortened wallet address."""
        return f"{self.proxy_wallet[:6]}...{self.proxy_wallet[-4:]}"


class TradeActivity(BaseModel):
    """Trade activity for a wallet."""

    proxy_wallet: str = Field(alias="proxyWallet")
    timestamp: int
    condition_id: str = Field(alias="conditionId")
    activity_type: str = Field(alias="type")
    size: float  # Token amount
    usdc_size: float = Field(alias="usdcSize")  # USD value
    price: float
    side: str  # BUY or SELL
    outcome_index: int = Field(alias="outcomeIndex")
    title: str  # Market question
    slug: str
    event_slug: str = Field(alias="eventSlug")
    outcome: str  # Yes or No
    transaction_hash: str = Field(alias="transactionHash")
    username: str = Field(default="", alias="name")

    @property
    def polymarket_url(self) -> str:
        """Get Polymarket URL for the market."""
        return f"https://polymarket.com/event/{self.event_slug}"

    @property
    def direction(self) -> str:
        """Get human readable direction."""
        if self.side == "BUY":
            return f"bought {self.outcome}"
        else:
            return f"sold {self.outcome}"


class DataAPIClient:
    """Client for Polymarket Data API."""

    BASE_URL = "https://data-api.polymarket.com"

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
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

    async def get_leaderboard(
        self,
        time_period: str = "WEEK",
        order_by: str = "PNL",
        category: str = "OVERALL",
        limit: int = 50,
        offset: int = 0,
    ) -> list[LeaderboardEntry]:
        """Get top traders from leaderboard.

        Args:
            time_period: DAY, WEEK, MONTH, or ALL
            order_by: PNL or VOL
            category: OVERALL, POLITICS, SPORTS, CRYPTO, etc.
            limit: Max 50
            offset: For pagination

        Returns:
            List of leaderboard entries
        """
        params = {
            "timePeriod": time_period,
            "orderBy": order_by,
            "category": category,
            "limit": min(limit, 50),
            "offset": offset,
        }

        response = await self.client.get("/v1/leaderboard", params=params)
        response.raise_for_status()
        data = response.json()

        return [LeaderboardEntry.model_validate(entry) for entry in data]

    async def get_wallet_activity(
        self,
        wallet: str,
        limit: int = 100,
        offset: int = 0,
        activity_type: str = "TRADE",
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[TradeActivity]:
        """Get trading activity for a wallet.

        Args:
            wallet: Wallet address (0x...)
            limit: Max 500
            offset: For pagination
            activity_type: TRADE, SPLIT, MERGE, REDEEM, etc.
            start_time: Unix timestamp filter
            end_time: Unix timestamp filter

        Returns:
            List of trade activities
        """
        params = {
            "user": wallet,
            "limit": min(limit, 500),
            "offset": offset,
            "type": activity_type,
        }

        if start_time:
            params["start"] = start_time
        if end_time:
            params["end"] = end_time

        response = await self.client.get("/activity", params=params)
        response.raise_for_status()
        data = response.json()

        return [TradeActivity.model_validate(trade) for trade in data]

    async def get_wallet_stats(self, wallet: str) -> dict[str, Any]:
        """Get aggregated stats for a wallet.

        This fetches the leaderboard entry for a specific user.
        """
        params = {
            "user": wallet,
            "timePeriod": "ALL",
        }

        response = await self.client.get("/v1/leaderboard", params=params)
        response.raise_for_status()
        data = response.json()

        if data:
            return data[0]
        return {}
