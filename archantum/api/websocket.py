"""WebSocket client for real-time Polymarket price feeds."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Any

import websockets
from websockets.asyncio.client import ClientConnection
from rich.console import Console

from archantum.config import settings


console = Console()


@dataclass
class WebSocketStats:
    """Statistics for WebSocket connection."""

    connected_at: datetime | None = None
    disconnected_at: datetime | None = None
    messages_received: int = 0
    errors: int = 0
    reconnect_attempts: int = 0
    last_message_at: datetime | None = None

    @property
    def uptime_seconds(self) -> float:
        """Get uptime in seconds."""
        if not self.connected_at:
            return 0.0
        end_time = self.disconnected_at or datetime.utcnow()
        return (end_time - self.connected_at).total_seconds()

    @property
    def is_connected(self) -> bool:
        """Check if currently connected."""
        return self.connected_at is not None and self.disconnected_at is None


@dataclass
class PriceUpdate:
    """Real-time price update from WebSocket."""

    token_id: str
    market_id: str | None
    price: float
    timestamp: datetime
    outcome: str  # 'yes' or 'no'


@dataclass
class PolymarketWebSocket:
    """WebSocket client for Polymarket real-time data."""

    WS_URL: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    # Callbacks
    on_price_update: Callable[[PriceUpdate], Any] | None = None
    on_state_change: Callable[[str, dict], Any] | None = None

    # Internal state
    _ws: ClientConnection | None = field(default=None, repr=False)
    _token_to_market: dict[str, tuple[str, str]] = field(default_factory=dict)  # token_id -> (market_id, outcome)
    _subscribed_markets: set[str] = field(default_factory=set)
    _running: bool = False
    _reconnect_task: asyncio.Task | None = field(default=None, repr=False)
    _receive_task: asyncio.Task | None = field(default=None, repr=False)
    _ping_task: asyncio.Task | None = field(default=None, repr=False)
    stats: WebSocketStats = field(default_factory=WebSocketStats)

    # Reconnection settings
    _reconnect_delay: float = 5.0
    _max_reconnect_delay: float = 160.0
    _max_reconnect_attempts: int = 10

    # Price cache for quick access
    _price_cache: dict[str, PriceUpdate] = field(default_factory=dict)

    async def connect(self) -> None:
        """Connect to WebSocket server."""
        if not settings.ws_enabled:
            console.print("[yellow]WebSocket disabled in config[/yellow]")
            return

        self._running = True
        self._reconnect_delay = settings.ws_reconnect_delay

        try:
            console.print(f"[cyan]Connecting to WebSocket: {self.WS_URL}[/cyan]")
            self._ws = await websockets.connect(
                self.WS_URL,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            )
            self.stats.connected_at = datetime.utcnow()
            self.stats.disconnected_at = None
            self.stats.reconnect_attempts = 0
            console.print("[green]WebSocket connected[/green]")

            if self.on_state_change:
                await self._safe_callback(self.on_state_change, "connected", {})

            # Start receive loop
            self._receive_task = asyncio.create_task(self._receive_loop())

            # Start ping loop to keep connection alive
            self._ping_task = asyncio.create_task(self._ping_loop())

            # Resubscribe to markets if any
            if self._subscribed_markets:
                await self._resubscribe_all()

        except Exception as e:
            console.print(f"[red]WebSocket connection failed: {e}[/red]")
            self.stats.errors += 1
            if self._running:
                asyncio.create_task(self._reconnect())

    async def disconnect(self) -> None:
        """Disconnect from WebSocket server."""
        self._running = False

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
            self._ping_task = None

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        self.stats.disconnected_at = datetime.utcnow()
        console.print("[yellow]WebSocket disconnected[/yellow]")

        if self.on_state_change:
            await self._safe_callback(self.on_state_change, "disconnected", {})

    async def subscribe_market(
        self,
        market_id: str,
        yes_token: str | None,
        no_token: str | None,
    ) -> None:
        """Subscribe to price updates for a market."""
        if not self._ws or not self.stats.is_connected:
            # Store for later subscription
            self._subscribed_markets.add(market_id)
            if yes_token:
                self._token_to_market[yes_token] = (market_id, "yes")
            if no_token:
                self._token_to_market[no_token] = (market_id, "no")
            return

        assets = []
        if yes_token:
            assets.append({"asset_id": yes_token})
            self._token_to_market[yes_token] = (market_id, "yes")
        if no_token:
            assets.append({"asset_id": no_token})
            self._token_to_market[no_token] = (market_id, "no")

        if not assets:
            return

        self._subscribed_markets.add(market_id)

        # Subscribe message format for Polymarket CLOB WebSocket
        # See: https://docs.polymarket.com/developers/CLOB/websocket/market-channel
        subscribe_msg = {
            "assets_ids": [a["asset_id"] for a in assets],
            "type": "market",
        }

        try:
            await self._ws.send(json.dumps(subscribe_msg))
            console.print(f"[dim]Subscribed to market {market_id[:8]}...[/dim]")
        except Exception as e:
            console.print(f"[yellow]Failed to subscribe to {market_id}: {e}[/yellow]")
            self.stats.errors += 1

    async def subscribe_markets(self, markets: list[dict]) -> None:
        """Subscribe to multiple markets in a single batch.

        Args:
            markets: List of dicts with 'id', 'yes_token', 'no_token' keys
        """
        if not self._ws or not self.stats.is_connected:
            # Store for later subscription
            for market in markets:
                market_id = market.get("id", "")
                yes_token = market.get("yes_token")
                no_token = market.get("no_token")
                self._subscribed_markets.add(market_id)
                if yes_token:
                    self._token_to_market[yes_token] = (market_id, "yes")
                if no_token:
                    self._token_to_market[no_token] = (market_id, "no")
            return

        # Collect all token IDs for batch subscription
        all_tokens = []
        for market in markets:
            market_id = market.get("id", "")
            yes_token = market.get("yes_token")
            no_token = market.get("no_token")

            self._subscribed_markets.add(market_id)
            if yes_token:
                self._token_to_market[yes_token] = (market_id, "yes")
                all_tokens.append(yes_token)
            if no_token:
                self._token_to_market[no_token] = (market_id, "no")
                all_tokens.append(no_token)

        if not all_tokens:
            return

        # Batch subscribe (max 500 per message)
        batch_size = 500
        for i in range(0, len(all_tokens), batch_size):
            batch = all_tokens[i:i + batch_size]
            subscribe_msg = {
                "assets_ids": batch,
                "type": "market",
            }
            try:
                await self._ws.send(json.dumps(subscribe_msg))
                console.print(f"[dim]Subscribed to {len(batch)} tokens (batch {i // batch_size + 1})[/dim]")
            except Exception as e:
                console.print(f"[yellow]Failed to subscribe batch: {e}[/yellow]")
                self.stats.errors += 1

    def get_cached_price(self, market_id: str, outcome: str = "yes") -> PriceUpdate | None:
        """Get cached price for a market outcome."""
        cache_key = f"{market_id}_{outcome}"
        return self._price_cache.get(cache_key)

    def get_all_cached_prices(self) -> dict[str, PriceUpdate]:
        """Get all cached prices."""
        return self._price_cache.copy()

    async def _receive_loop(self) -> None:
        """Main loop for receiving WebSocket messages."""
        while self._running and self._ws:
            try:
                message = await self._ws.recv()
                await self._handle_message(message)
            except websockets.ConnectionClosed as e:
                console.print(f"[yellow]WebSocket connection closed: {e}[/yellow]")
                self.stats.disconnected_at = datetime.utcnow()
                if self._running:
                    asyncio.create_task(self._reconnect())
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                console.print(f"[red]WebSocket receive error: {e}[/red]")
                self.stats.errors += 1

    async def _ping_loop(self) -> None:
        """Send PING messages to keep connection alive."""
        while self._running and self._ws:
            try:
                await asyncio.sleep(10)  # Ping every 10 seconds
                if self._ws and self.stats.is_connected:
                    await self._ws.send("PING")
            except asyncio.CancelledError:
                break
            except Exception:
                pass  # Ignore ping errors

    async def _handle_message(self, raw_message: str) -> None:
        """Handle incoming WebSocket message."""
        # Handle text responses (PONG, errors, etc.)
        if raw_message == "PONG":
            return  # Ping acknowledgment
        if raw_message in ("INVALID OPERATION", "INVALID_OPERATION"):
            # Server rejected subscription - likely wrong format or invalid token
            self.stats.errors += 1
            return
        if not raw_message.startswith("{") and not raw_message.startswith("["):
            # Not JSON, ignore other text messages
            return

        try:
            data = json.loads(raw_message)
            self.stats.messages_received += 1
            self.stats.last_message_at = datetime.utcnow()

            # Handle array of messages
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        await self._process_single_message(item)
                return

            # Handle single message
            if isinstance(data, dict):
                await self._process_single_message(data)

        except json.JSONDecodeError:
            pass  # Silently ignore malformed JSON
        except Exception as e:
            console.print(f"[yellow]Error handling message: {e}[/yellow]")

    async def _process_single_message(self, data: dict) -> None:
        """Process a single WebSocket message."""
        # Handle different message types
        msg_type = data.get("type", data.get("event_type", ""))

        if msg_type == "price_change" or "price" in data:
            await self._handle_price_update(data)
        elif msg_type == "book":
            await self._handle_book_update(data)
        elif msg_type == "last_trade_price":
            await self._handle_trade_price(data)
        elif msg_type in ("subscribed", "unsubscribed"):
            pass  # Acknowledgment messages
        elif msg_type == "error":
            console.print(f"[red]WebSocket error: {data}[/red]")
            self.stats.errors += 1

    async def _handle_price_update(self, data: dict) -> None:
        """Handle price update message."""
        asset_id = data.get("asset_id") or data.get("token_id")
        if not asset_id:
            return

        market_info = self._token_to_market.get(asset_id)
        if not market_info:
            return

        market_id, outcome = market_info

        # Extract price - try different possible fields
        price = data.get("price") or data.get("mid") or data.get("last_price")
        if price is None:
            return

        try:
            price = float(price)
        except (ValueError, TypeError):
            return

        update = PriceUpdate(
            token_id=asset_id,
            market_id=market_id,
            price=price,
            timestamp=datetime.utcnow(),
            outcome=outcome,
        )

        # Cache the update
        cache_key = f"{market_id}_{outcome}"
        self._price_cache[cache_key] = update

        # Call callback if registered
        if self.on_price_update:
            await self._safe_callback(self.on_price_update, update)

    async def _handle_book_update(self, data: dict) -> None:
        """Handle orderbook update message."""
        asset_id = data.get("asset_id")
        if not asset_id:
            return

        market_info = self._token_to_market.get(asset_id)
        if not market_info:
            return

        market_id, outcome = market_info

        # Calculate midpoint from bids/asks
        bids = data.get("bids", [])
        asks = data.get("asks", [])

        best_bid = max((float(b.get("price", 0)) for b in bids), default=None)
        best_ask = min((float(a.get("price", 0)) for a in asks), default=None)

        if best_bid is not None and best_ask is not None:
            midpoint = (best_bid + best_ask) / 2

            update = PriceUpdate(
                token_id=asset_id,
                market_id=market_id,
                price=midpoint,
                timestamp=datetime.utcnow(),
                outcome=outcome,
            )

            cache_key = f"{market_id}_{outcome}"
            self._price_cache[cache_key] = update

            if self.on_price_update:
                await self._safe_callback(self.on_price_update, update)

    async def _handle_trade_price(self, data: dict) -> None:
        """Handle last trade price message."""
        asset_id = data.get("asset_id")
        price = data.get("price")

        if not asset_id or price is None:
            return

        market_info = self._token_to_market.get(asset_id)
        if not market_info:
            return

        market_id, outcome = market_info

        try:
            price = float(price)
        except (ValueError, TypeError):
            return

        update = PriceUpdate(
            token_id=asset_id,
            market_id=market_id,
            price=price,
            timestamp=datetime.utcnow(),
            outcome=outcome,
        )

        cache_key = f"{market_id}_{outcome}"
        self._price_cache[cache_key] = update

        if self.on_price_update:
            await self._safe_callback(self.on_price_update, update)

    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        if not self._running:
            return

        self.stats.reconnect_attempts += 1

        if self.stats.reconnect_attempts > self._max_reconnect_attempts:
            console.print("[red]Max reconnection attempts reached. Giving up.[/red]")
            if self.on_state_change:
                await self._safe_callback(
                    self.on_state_change,
                    "failed",
                    {"reason": "max_reconnect_attempts"},
                )
            return

        delay = min(
            self._reconnect_delay * (2 ** (self.stats.reconnect_attempts - 1)),
            self._max_reconnect_delay,
        )

        console.print(
            f"[yellow]Reconnecting in {delay:.1f}s "
            f"(attempt {self.stats.reconnect_attempts}/{self._max_reconnect_attempts})...[/yellow]"
        )

        await asyncio.sleep(delay)

        if self._running:
            await self.connect()

    async def _resubscribe_all(self) -> None:
        """Resubscribe to all markets after reconnection."""
        console.print(f"[cyan]Resubscribing to {len(self._subscribed_markets)} markets...[/cyan]")

        # Group tokens by market for efficient subscription
        markets_to_subscribe = []
        for market_id in self._subscribed_markets:
            yes_token = None
            no_token = None
            for token_id, (mid, outcome) in self._token_to_market.items():
                if mid == market_id:
                    if outcome == "yes":
                        yes_token = token_id
                    else:
                        no_token = token_id

            if yes_token or no_token:
                markets_to_subscribe.append({
                    "id": market_id,
                    "yes_token": yes_token,
                    "no_token": no_token,
                })

        for market in markets_to_subscribe:
            await self.subscribe_market(
                market_id=market["id"],
                yes_token=market.get("yes_token"),
                no_token=market.get("no_token"),
            )

    async def _safe_callback(self, callback: Callable, *args) -> None:
        """Safely call a callback, catching any exceptions."""
        try:
            result = callback(*args)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            console.print(f"[yellow]Callback error: {e}[/yellow]")

    def get_health_report(self) -> dict[str, Any]:
        """Get health report for the WebSocket connection."""
        return {
            "connected": self.stats.is_connected,
            "uptime_seconds": self.stats.uptime_seconds,
            "messages_received": self.stats.messages_received,
            "errors": self.stats.errors,
            "reconnect_attempts": self.stats.reconnect_attempts,
            "subscribed_markets": len(self._subscribed_markets),
            "cached_prices": len(self._price_cache),
            "last_message_at": self.stats.last_message_at.isoformat() if self.stats.last_message_at else None,
        }
