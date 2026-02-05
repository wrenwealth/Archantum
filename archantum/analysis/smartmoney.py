"""Smart Money tracking and analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from archantum.db import Database
from archantum.api.data import DataAPIClient, LeaderboardEntry, TradeActivity


@dataclass
class SmartMoneyAlert:
    """Alert when a smart wallet makes a trade."""

    wallet_address: str
    username: str
    wallet_pnl: float
    wallet_rank: int | None

    # Trade details
    market_title: str
    side: str  # BUY or SELL
    outcome: str  # Yes or No
    usdc_size: float
    price: float
    event_slug: str | None

    @property
    def polymarket_url(self) -> str | None:
        """Get Polymarket URL."""
        if self.event_slug:
            return f"https://polymarket.com/event/{self.event_slug}"
        return None

    @property
    def direction_text(self) -> str:
        """Human readable direction."""
        return f"{self.side.lower()} {self.outcome}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet_address": self.wallet_address,
            "username": self.username,
            "wallet_pnl": self.wallet_pnl,
            "wallet_rank": self.wallet_rank,
            "market_title": self.market_title,
            "side": self.side,
            "outcome": self.outcome,
            "usdc_size": self.usdc_size,
            "price": self.price,
            "event_slug": self.event_slug,
        }


class SmartMoneyTracker:
    """Tracks and analyzes smart money wallets."""

    def __init__(
        self,
        db: Database,
        min_trade_usdc: float = 500.0,  # Minimum trade size to alert
        top_wallets_count: int = 20,  # Number of top wallets to track
    ):
        self.db = db
        self.min_trade_usdc = min_trade_usdc
        self.top_wallets_count = top_wallets_count

    async def sync_leaderboard(self, time_period: str = "DAY") -> int:
        """Sync top wallets from leaderboard.

        Uses DAY time period by default to ensure wallets are active.
        Returns number of wallets synced.
        """
        async with DataAPIClient() as client:
            entries = await client.get_leaderboard(
                time_period=time_period,
                order_by="PNL",
                limit=self.top_wallets_count,
            )

            count = 0
            for entry in entries:
                await self.db.upsert_smart_wallet(
                    wallet_address=entry.proxy_wallet,
                    username=entry.username,
                    x_username=entry.x_username,
                    total_pnl=entry.pnl,
                    total_volume=entry.volume,
                    leaderboard_rank=int(entry.rank),
                )
                count += 1

            return count

    async def fetch_wallet_trades(self, wallet_address: str, limit: int = 50) -> int:
        """Fetch recent trades for a wallet.

        Returns number of new trades saved.
        """
        # Get wallet from DB
        wallet = await self.db.get_smart_wallet(wallet_address)
        if not wallet:
            return 0

        async with DataAPIClient() as client:
            trades = await client.get_wallet_activity(
                wallet=wallet_address,
                limit=limit,
                activity_type="TRADE",
            )

            count = 0
            for trade in trades:
                result = await self.db.save_smart_trade(
                    wallet_id=wallet.id,
                    transaction_hash=trade.transaction_hash,
                    condition_id=trade.condition_id,
                    market_title=trade.title,
                    event_slug=trade.event_slug,
                    side=trade.side,
                    outcome=trade.outcome,
                    size=trade.size,
                    usdc_size=trade.usdc_size,
                    price=trade.price,
                    timestamp=datetime.fromtimestamp(trade.timestamp),
                )
                if result:  # New trade saved
                    count += 1

            return count

    async def sync_all_tracked_wallets(self) -> dict[str, int]:
        """Sync trades for all tracked wallets.

        Returns dict of wallet -> new trades count.
        """
        wallets = await self.db.get_tracked_wallets()
        results = {}

        for wallet in wallets:
            count = await self.fetch_wallet_trades(wallet.wallet_address)
            if count > 0:
                results[wallet.wallet_address] = count

        return results

    async def get_pending_alerts(self) -> list[SmartMoneyAlert]:
        """Get trades that should be alerted."""
        unsent = await self.db.get_unsent_smart_trades(min_usdc=self.min_trade_usdc)

        alerts = []
        for trade, wallet in unsent:
            alert = SmartMoneyAlert(
                wallet_address=wallet.wallet_address,
                username=wallet.username or wallet.wallet_address[:10],
                wallet_pnl=wallet.total_pnl,
                wallet_rank=wallet.leaderboard_rank,
                market_title=trade.market_title,
                side=trade.side,
                outcome=trade.outcome,
                usdc_size=trade.usdc_size,
                price=trade.price,
                event_slug=trade.event_slug,
            )
            alerts.append(alert)

            # Mark as alerted
            await self.db.mark_trade_alerted(trade.id)

        return alerts

    async def get_top_wallets(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get top tracked wallets with stats."""
        wallets = await self.db.get_tracked_wallets(limit=limit)

        results = []
        for w in wallets:
            results.append({
                "address": w.wallet_address,
                "address_short": f"{w.wallet_address[:6]}...{w.wallet_address[-4:]}",
                "username": w.username or "Anonymous",
                "pnl": w.total_pnl,
                "volume": w.total_volume,
                "rank": w.leaderboard_rank,
                "total_trades": w.total_trades,
                "last_trade": w.last_trade_at,
            })

        return results

    async def get_wallet_stats(self, wallet_address: str) -> dict[str, Any] | None:
        """Get detailed stats for a wallet."""
        wallet = await self.db.get_smart_wallet(wallet_address)
        if not wallet:
            return None

        trades = await self.db.get_wallet_trades(wallet_address, limit=10)

        return {
            "address": wallet.wallet_address,
            "username": wallet.username or "Anonymous",
            "pnl": wallet.total_pnl,
            "volume": wallet.total_volume,
            "rank": wallet.leaderboard_rank,
            "total_trades": wallet.total_trades,
            "last_trade": wallet.last_trade_at,
            "recent_trades": [
                {
                    "market": t.market_title[:50],
                    "side": t.side,
                    "outcome": t.outcome,
                    "usdc": t.usdc_size,
                    "price": t.price,
                    "time": t.timestamp,
                }
                for t in trades
            ],
        }
