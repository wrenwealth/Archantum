"""Chainlink price feed client for BTC/USD."""

from __future__ import annotations

import httpx

# Chainlink BTC/USD Price Feed on Ethereum Mainnet
# Contract: 0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c
CHAINLINK_BTC_USD = "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c"

# latestRoundData() function selector
LATEST_ROUND_DATA_SELECTOR = "0xfeaf968c"


class ChainlinkClient:
    """Client for Chainlink BTC/USD price feed via public RPC."""

    # Free public Ethereum RPC endpoints (rotate if rate limited)
    RPC_ENDPOINTS = [
        "https://eth.llamarpc.com",
        "https://rpc.ankr.com/eth",
        "https://ethereum.publicnode.com",
        "https://1rpc.io/eth",
    ]

    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._rpc_index = 0

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

    async def get_btc_price(self) -> float | None:
        """Get BTC/USD price from Chainlink oracle.

        Returns:
            BTC price as float, or None if all RPCs fail.
        """
        # Try each RPC endpoint
        for i in range(len(self.RPC_ENDPOINTS)):
            rpc_url = self.RPC_ENDPOINTS[(self._rpc_index + i) % len(self.RPC_ENDPOINTS)]
            try:
                price = await self._fetch_from_rpc(rpc_url)
                if price:
                    self._rpc_index = (self._rpc_index + i) % len(self.RPC_ENDPOINTS)
                    return price
            except Exception:
                continue

        return None

    async def _fetch_from_rpc(self, rpc_url: str) -> float | None:
        """Fetch price from a specific RPC endpoint."""
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [
                {
                    "to": CHAINLINK_BTC_USD,
                    "data": LATEST_ROUND_DATA_SELECTOR,
                },
                "latest",
            ],
            "id": 1,
        }

        resp = await self.client.post(rpc_url, json=payload)
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            return None

        result = data.get("result")
        if not result or result == "0x":
            return None

        # latestRoundData returns: (roundId, answer, startedAt, updatedAt, answeredInRound)
        # answer is at bytes 32-64 (index 1), 8 decimals
        # Result format: 0x + 5 * 64 hex chars = 320 hex chars + 2 for 0x
        hex_data = result[2:]  # Remove 0x prefix

        if len(hex_data) < 128:  # Need at least 2 slots (64 hex chars each)
            return None

        # Extract answer (second 32-byte slot)
        answer_hex = hex_data[64:128]
        answer_int = int(answer_hex, 16)

        # Chainlink BTC/USD has 8 decimals
        price = answer_int / 1e8

        # Sanity check
        if price < 1000 or price > 1000000:
            return None

        return price
