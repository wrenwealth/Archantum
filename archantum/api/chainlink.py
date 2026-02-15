"""Chainlink price feed client for BTC/USD."""

from __future__ import annotations

import time

import httpx

# Chainlink BTC/USD Price Feed contracts
# Polygon mainnet (used by Polymarket for resolution) - updates faster
CHAINLINK_BTC_USD_POLYGON = "0xc907E116054Ad103354f2D350FD2514433D57F6f"
# Ethereum mainnet (fallback)
CHAINLINK_BTC_USD_ETH = "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c"

# latestRoundData() function selector
LATEST_ROUND_DATA_SELECTOR = "0xfeaf968c"


class ChainlinkClient:
    """Client for Chainlink BTC/USD price feed via public RPC.

    Uses Polygon mainnet first (same as Polymarket resolution),
    falls back to Ethereum mainnet if Polygon fails.
    """

    # Polygon RPC endpoints (primary - used by Polymarket)
    POLYGON_RPC_ENDPOINTS = [
        "https://polygon-rpc.com",
        "https://rpc.ankr.com/polygon",
        "https://polygon.llamarpc.com",
        "https://1rpc.io/matic",
    ]

    # Ethereum RPC endpoints (fallback)
    ETH_RPC_ENDPOINTS = [
        "https://eth.llamarpc.com",
        "https://rpc.ankr.com/eth",
        "https://ethereum.publicnode.com",
        "https://1rpc.io/eth",
    ]

    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._polygon_rpc_index = 0
        self._eth_rpc_index = 0

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

        Tries Polygon mainnet first (same as Polymarket resolution),
        then falls back to Ethereum mainnet.

        Returns:
            BTC price as float, or None if all RPCs fail.
        """
        # Try Polygon first (Polymarket uses Polygon Chainlink for resolution)
        for i in range(len(self.POLYGON_RPC_ENDPOINTS)):
            rpc_url = self.POLYGON_RPC_ENDPOINTS[(self._polygon_rpc_index + i) % len(self.POLYGON_RPC_ENDPOINTS)]
            try:
                price, updated_at = await self._fetch_from_rpc(rpc_url, CHAINLINK_BTC_USD_POLYGON)
                if price and updated_at:
                    # Check if data is fresh (< 5 minutes old)
                    age = int(time.time()) - updated_at
                    if age < 300:  # 5 minutes
                        self._polygon_rpc_index = (self._polygon_rpc_index + i) % len(self.POLYGON_RPC_ENDPOINTS)
                        return price
            except Exception:
                continue

        # Fallback to Ethereum mainnet
        for i in range(len(self.ETH_RPC_ENDPOINTS)):
            rpc_url = self.ETH_RPC_ENDPOINTS[(self._eth_rpc_index + i) % len(self.ETH_RPC_ENDPOINTS)]
            try:
                price, updated_at = await self._fetch_from_rpc(rpc_url, CHAINLINK_BTC_USD_ETH)
                if price and updated_at:
                    age = int(time.time()) - updated_at
                    if age < 3600:  # 1 hour (Ethereum heartbeat)
                        self._eth_rpc_index = (self._eth_rpc_index + i) % len(self.ETH_RPC_ENDPOINTS)
                        return price
            except Exception:
                continue

        return None

    async def _fetch_from_rpc(self, rpc_url: str, contract: str) -> tuple[float | None, int | None]:
        """Fetch price from a specific RPC endpoint.

        Returns:
            (price, updated_at_timestamp) or (None, None) on failure.
        """
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [
                {
                    "to": contract,
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
            return None, None

        result = data.get("result")
        if not result or result == "0x":
            return None, None

        # latestRoundData returns: (roundId, answer, startedAt, updatedAt, answeredInRound)
        # answer is at bytes 32-64 (slot 1), 8 decimals
        # updatedAt is at bytes 96-128 (slot 3)
        hex_data = result[2:]  # Remove 0x prefix

        if len(hex_data) < 256:  # Need at least 4 slots
            return None, None

        # Extract answer (second 32-byte slot)
        answer_hex = hex_data[64:128]
        answer_int = int(answer_hex, 16)

        # Extract updatedAt (fourth 32-byte slot)
        updated_at_hex = hex_data[192:256]
        updated_at = int(updated_at_hex, 16)

        # Chainlink BTC/USD has 8 decimals
        price = answer_int / 1e8

        # Sanity check
        if price < 1000 or price > 1000000:
            return None, None

        return price, updated_at
