"""Hyperliquid API client for BTC price data."""

from __future__ import annotations

import httpx


class HyperliquidClient:
    """Client for Hyperliquid API (BTC mid price)."""

    API_URL = "https://api.hyperliquid.xyz/info"

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=10.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")
        return self._client

    async def get_btc_mid_price(self) -> float:
        """Get BTC mid price from Hyperliquid.

        Returns:
            BTC mid price as float.

        Raises:
            httpx.HTTPError: On network/API errors.
            KeyError: If BTC not found in response.
            ValueError: If price cannot be parsed.
        """
        resp = await self.client.post(
            self.API_URL,
            json={"type": "allMids"},
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data["BTC"])
