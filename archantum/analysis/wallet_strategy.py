"""Wallet strategy analyzer — reverse-engineers trading patterns from trade history."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rich.console import Console

from archantum.db.database import Database
from archantum.db.models import SmartTrade
from archantum.api.data import DataAPIClient

console = Console()


# ---------------------------------------------------------------------------
# Category inference
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "politics": [
        "trump", "biden", "election", "president", "congress", "senate",
        "democrat", "republican", "vote", "governor", "political", "gop",
        "primary", "nominee", "inauguration", "impeach", "cabinet",
    ],
    "crypto": [
        "bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol",
        "token", "defi", "nft", "blockchain", "altcoin", "memecoin",
        "doge", "xrp", "binance", "coinbase",
    ],
    "sports": [
        "nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball",
        "baseball", "tennis", "golf", "f1", "formula", "ufc", "boxing",
        "super bowl", "world series", "world cup", "olympics",
    ],
    "entertainment": [
        "oscar", "grammy", "emmy", "movie", "film", "album", "spotify",
        "netflix", "disney", "box office", "celebrity", "kardashian",
        "taylor swift", "music", "tv show",
    ],
    "economics": [
        "fed", "interest rate", "inflation", "gdp", "unemployment",
        "recession", "stock", "s&p", "nasdaq", "dow", "treasury",
        "cpi", "jobs report", "tariff", "trade war",
    ],
    "tech": [
        "ai", "artificial intelligence", "openai", "google", "apple",
        "microsoft", "meta", "tesla", "spacex", "launch", "ipo",
        "startup", "semiconductor", "chip",
    ],
    "world": [
        "ukraine", "russia", "china", "israel", "gaza", "nato",
        "war", "ceasefire", "sanctions", "un ", "united nations",
        "north korea", "iran", "peace deal",
    ],
    "esports": [
        "valorant", "counter-strike", "cs2", "league of legends",
        "dota", "overwatch", "esport", "vct", "major",
    ],
}


def infer_category(title: str) -> str:
    """Infer market category from title keywords."""
    title_lower = title.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in title_lower:
                return category
    return "other"


# ---------------------------------------------------------------------------
# Position state — groups trades by (condition_id, outcome)
# ---------------------------------------------------------------------------

@dataclass
class PositionState:
    """Tracks accumulated state for a single position."""

    condition_id: str
    outcome: str
    market_title: str = ""
    total_bought: float = 0.0
    total_buy_cost: float = 0.0
    total_sold: float = 0.0
    total_sell_proceeds: float = 0.0
    buy_prices: list[float] = field(default_factory=list)
    sell_prices: list[float] = field(default_factory=list)
    first_buy_at: datetime | None = None
    last_action_at: datetime | None = None

    @property
    def is_closed(self) -> bool:
        """Position is closed if >= 95% of shares sold."""
        if self.total_bought == 0:
            return False
        return self.total_sold >= self.total_bought * 0.95

    @property
    def realized_pnl(self) -> float:
        """Realized P&L for closed positions."""
        if self.total_bought == 0:
            return 0.0
        avg_buy = self.total_buy_cost / self.total_bought
        sold_shares = min(self.total_sold, self.total_bought)
        return (self.total_sell_proceeds - (sold_shares * avg_buy)) if sold_shares > 0 else 0.0

    @property
    def is_winner(self) -> bool:
        return self.realized_pnl > 0

    @property
    def hold_duration_hours(self) -> float | None:
        if self.first_buy_at and self.last_action_at and self.first_buy_at != self.last_action_at:
            delta = (self.last_action_at - self.first_buy_at).total_seconds() / 3600
            return max(delta, 0.0)
        return None


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class WalletStrategyResult:
    """Full analysis result for a wallet."""

    # Basic stats
    total_trades: int = 0
    total_buys: int = 0
    total_sells: int = 0
    total_usdc_volume: float = 0.0
    realized_pnl: float = 0.0
    roi_pct: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    open_positions: int = 0
    win_rate: float = 0.0

    # Entry analysis
    avg_entry_price: float | None = None
    median_entry_price: float | None = None
    yes_preference_pct: float | None = None
    avg_position_usdc: float | None = None
    median_position_usdc: float | None = None

    # Exit analysis
    hold_to_settlement_pct: float | None = None
    early_exit_pct: float | None = None
    avg_hold_hours: float | None = None

    # Category breakdown (JSON string)
    category_breakdown: str | None = None

    # Pattern detection
    most_active_hours: str | None = None
    avg_trades_per_day: float | None = None
    contrarian_score: float | None = None

    # Risk analysis
    max_position_usdc: float | None = None
    max_drawdown_pct: float | None = None
    unique_markets_traded: int | None = None
    diversification_score: float | None = None

    # Meta
    trades_analyzed: int = 0
    first_trade_at: datetime | None = None
    last_trade_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for DB storage."""
        d: dict[str, Any] = {}
        for k, v in self.__dict__.items():
            if isinstance(v, datetime):
                d[k] = v
            else:
                d[k] = v
        return d


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------

class WalletStrategyAnalyzer:
    """Analyzes wallet trading strategy from complete trade history."""

    def __init__(self, db: Database):
        self.db = db

    async def fetch_all_trades(
        self,
        wallet_address: str,
        max_pages: int = 10,
        progress_callback: Any | None = None,
    ) -> int:
        """Fetch trades via paginated API calls with batch DB saves.

        Args:
            wallet_address: Wallet to fetch trades for.
            max_pages: Max pages to fetch (500 trades/page). Default 10 = 5000 trades.
            progress_callback: Optional async callable(fetched_so_far, page_num) for progress updates.

        Returns:
            Count of new trades saved.
        """
        wallet = await self.db.get_smart_wallet(wallet_address)
        if not wallet:
            return 0

        new_count = 0
        offset = 0
        page_size = 500
        total_fetched = 0

        async with DataAPIClient() as client:
            for page_num in range(1, max_pages + 1):
                try:
                    activities = await client.get_wallet_activity(
                        wallet=wallet_address,
                        limit=page_size,
                        offset=offset,
                        activity_type="TRADE",
                    )
                except Exception as e:
                    console.print(f"[yellow]Error fetching trades at offset {offset}: {e}[/yellow]")
                    break

                if not activities:
                    break

                total_fetched += len(activities)

                # Report progress
                if progress_callback:
                    try:
                        await progress_callback(total_fetched, page_num)
                    except Exception:
                        pass

                # Convert to dicts for batch save
                trades_data = []
                for act in activities:
                    try:
                        trades_data.append({
                            "transaction_hash": act.transaction_hash,
                            "condition_id": act.condition_id,
                            "market_title": act.title or "",
                            "event_slug": act.event_slug,
                            "side": act.side,
                            "outcome": act.outcome or f"outcome_{act.outcome_index}",
                            "size": act.size,
                            "usdc_size": act.usdc_size,
                            "price": act.price,
                            "timestamp": datetime.utcfromtimestamp(act.timestamp),
                        })
                    except Exception:
                        pass

                # Batch save this page
                page_new = await self.db.save_smart_trades_batch(wallet.id, trades_data)
                new_count += page_new

                if len(activities) < page_size:
                    break
                offset += page_size

        return new_count

    async def analyze(self, wallet_address: str) -> WalletStrategyResult | None:
        """Run full strategy analysis on a wallet."""
        trades = await self.db.get_all_wallet_trades(wallet_address)
        if not trades:
            return None

        positions = self._build_positions(trades)
        result = WalletStrategyResult()

        self._compute_basic_stats(result, trades, positions)
        self._compute_entry_analysis(result, trades, positions)
        self._compute_exit_analysis(result, positions)
        self._compute_category_analysis(result, trades, positions)
        self._compute_pattern_detection(result, trades)
        self._compute_risk_analysis(result, trades, positions)

        result.trades_analyzed = len(trades)
        result.first_trade_at = trades[0].timestamp
        result.last_trade_at = trades[-1].timestamp

        # Save to DB
        wallet = await self.db.get_smart_wallet(wallet_address)
        if wallet:
            await self.db.save_wallet_analysis(wallet.id, result.to_dict())

        return result

    async def auto_refresh_analyses(self) -> dict[str, int]:
        """Auto-refresh stale analyses. Returns {address: new_trade_count}."""
        from archantum.config import settings

        wallets = await self.db.get_wallets_needing_analysis_refresh(
            max_age_hours=settings.wallet_analysis_refresh_hours
        )
        refreshed: dict[str, int] = {}

        for wallet in wallets:
            try:
                new_trades = await self.fetch_all_trades(wallet.wallet_address)
                await self.analyze(wallet.wallet_address)
                if new_trades > 0:
                    refreshed[wallet.wallet_address] = new_trades
            except Exception as e:
                console.print(f"[yellow]Auto-refresh error for {wallet.wallet_address[:10]}...: {e}[/yellow]")

        return refreshed

    # ------------------------------------------------------------------
    # Position building
    # ------------------------------------------------------------------

    @staticmethod
    def _build_positions(trades: list[SmartTrade]) -> list[PositionState]:
        """Group trades into positions by (condition_id, outcome)."""
        pos_map: dict[tuple[str, str], PositionState] = {}

        for t in trades:
            key = (t.condition_id, t.outcome)
            if key not in pos_map:
                pos_map[key] = PositionState(
                    condition_id=t.condition_id,
                    outcome=t.outcome,
                    market_title=t.market_title,
                )
            pos = pos_map[key]

            if t.side == "BUY":
                pos.total_bought += t.size
                pos.total_buy_cost += t.usdc_size
                pos.buy_prices.append(t.price)
                if pos.first_buy_at is None:
                    pos.first_buy_at = t.timestamp
            elif t.side == "SELL":
                pos.total_sold += t.size
                pos.total_sell_proceeds += t.usdc_size
                pos.sell_prices.append(t.price)

            pos.last_action_at = t.timestamp

        return list(pos_map.values())

    # ------------------------------------------------------------------
    # Analysis modules
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_basic_stats(
        result: WalletStrategyResult,
        trades: list[SmartTrade],
        positions: list[PositionState],
    ) -> None:
        buys = [t for t in trades if t.side == "BUY"]
        sells = [t for t in trades if t.side == "SELL"]
        result.total_trades = len(trades)
        result.total_buys = len(buys)
        result.total_sells = len(sells)
        result.total_usdc_volume = sum(t.usdc_size for t in trades)

        closed = [p for p in positions if p.is_closed]
        result.win_count = sum(1 for p in closed if p.is_winner)
        result.loss_count = len(closed) - result.win_count
        result.open_positions = sum(1 for p in positions if not p.is_closed)
        result.win_rate = (result.win_count / len(closed) * 100) if closed else 0.0
        result.realized_pnl = sum(p.realized_pnl for p in closed)

        total_cost = sum(p.total_buy_cost for p in closed)
        result.roi_pct = (result.realized_pnl / total_cost * 100) if total_cost > 0 else 0.0

    @staticmethod
    def _compute_entry_analysis(
        result: WalletStrategyResult,
        trades: list[SmartTrade],
        positions: list[PositionState],
    ) -> None:
        buy_trades = [t for t in trades if t.side == "BUY"]
        if not buy_trades:
            return

        prices = [t.price for t in buy_trades]
        result.avg_entry_price = statistics.mean(prices)
        result.median_entry_price = statistics.median(prices)

        yes_buys = sum(1 for t in buy_trades if t.outcome.lower() == "yes")
        result.yes_preference_pct = (yes_buys / len(buy_trades) * 100) if buy_trades else 0.0

        position_sizes = [p.total_buy_cost for p in positions if p.total_buy_cost > 0]
        if position_sizes:
            result.avg_position_usdc = statistics.mean(position_sizes)
            result.median_position_usdc = statistics.median(position_sizes)

    @staticmethod
    def _compute_exit_analysis(
        result: WalletStrategyResult,
        positions: list[PositionState],
    ) -> None:
        closed = [p for p in positions if p.is_closed]
        if not closed:
            return

        # Settlement detection: if max sell price > 0.95, likely held to settlement
        settlement_count = 0
        early_exit_count = 0
        hold_hours_list: list[float] = []

        for p in closed:
            max_sell = max(p.sell_prices) if p.sell_prices else 0
            if max_sell > 0.95:
                settlement_count += 1
            else:
                early_exit_count += 1

            hours = p.hold_duration_hours
            if hours is not None:
                hold_hours_list.append(hours)

        result.hold_to_settlement_pct = (settlement_count / len(closed) * 100)
        result.early_exit_pct = (early_exit_count / len(closed) * 100)
        result.avg_hold_hours = statistics.mean(hold_hours_list) if hold_hours_list else None

    @staticmethod
    def _compute_category_analysis(
        result: WalletStrategyResult,
        trades: list[SmartTrade],
        positions: list[PositionState],
    ) -> None:
        # Group positions by category
        cat_data: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"trades": 0, "volume": 0.0, "wins": 0, "losses": 0}
        )

        for p in positions:
            cat = infer_category(p.market_title)
            cat_data[cat]["trades"] += len(p.buy_prices) + len(p.sell_prices)
            cat_data[cat]["volume"] += p.total_buy_cost + p.total_sell_proceeds
            if p.is_closed:
                if p.is_winner:
                    cat_data[cat]["wins"] += 1
                else:
                    cat_data[cat]["losses"] += 1

        # Add win_rate per category
        breakdown = {}
        for cat, data in cat_data.items():
            total = data["wins"] + data["losses"]
            data["win_rate"] = (data["wins"] / total * 100) if total > 0 else 0.0
            breakdown[cat] = data

        # Sort by volume descending
        breakdown = dict(sorted(breakdown.items(), key=lambda x: x[1]["volume"], reverse=True))
        result.category_breakdown = json.dumps(breakdown)

    @staticmethod
    def _compute_pattern_detection(
        result: WalletStrategyResult,
        trades: list[SmartTrade],
    ) -> None:
        if not trades:
            return

        # Hour distribution
        hour_counts: dict[int, int] = defaultdict(int)
        for t in trades:
            hour_counts[t.timestamp.hour] += 1

        # Top 5 most active hours
        sorted_hours = sorted(hour_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        result.most_active_hours = json.dumps([{"hour": h, "count": c} for h, c in sorted_hours])

        # Trades per day
        if len(trades) >= 2:
            first = trades[0].timestamp
            last = trades[-1].timestamp
            days = max((last - first).total_seconds() / 86400, 1.0)
            result.avg_trades_per_day = len(trades) / days
        else:
            result.avg_trades_per_day = 0.0

        # Contrarian score: buys at < 0.30, sells at > 0.70
        buy_trades = [t for t in trades if t.side == "BUY"]
        sell_trades = [t for t in trades if t.side == "SELL"]

        contrarian_buys = sum(1 for t in buy_trades if t.price < 0.30) if buy_trades else 0
        contrarian_sells = sum(1 for t in sell_trades if t.price > 0.70) if sell_trades else 0
        total_contrarian = contrarian_buys + contrarian_sells
        total_relevant = len(buy_trades) + len(sell_trades)
        result.contrarian_score = (total_contrarian / total_relevant * 100) if total_relevant > 0 else 0.0

    @staticmethod
    def _compute_risk_analysis(
        result: WalletStrategyResult,
        trades: list[SmartTrade],
        positions: list[PositionState],
    ) -> None:
        if not positions:
            return

        # Max single position
        position_sizes = [p.total_buy_cost for p in positions]
        result.max_position_usdc = max(position_sizes) if position_sizes else 0.0

        # Unique markets
        unique_conditions = {p.condition_id for p in positions}
        result.unique_markets_traded = len(unique_conditions)

        # Diversification score (1 - HHI)
        total_vol = sum(p.total_buy_cost for p in positions)
        if total_vol > 0:
            shares = [(p.total_buy_cost / total_vol) for p in positions if p.total_buy_cost > 0]
            hhi = sum(s ** 2 for s in shares)
            result.diversification_score = round((1 - hhi) * 100, 1)
        else:
            result.diversification_score = 0.0

        # Max drawdown from cumulative P&L curve
        closed = [p for p in positions if p.is_closed]
        if closed:
            # Sort closed positions by last_action_at
            closed_sorted = sorted(closed, key=lambda p: p.last_action_at or datetime.min)
            cumulative = 0.0
            peak = 0.0
            max_dd = 0.0

            for p in closed_sorted:
                cumulative += p.realized_pnl
                if cumulative > peak:
                    peak = cumulative
                dd = peak - cumulative
                if dd > max_dd:
                    max_dd = dd

            result.max_drawdown_pct = (max_dd / peak * 100) if peak > 0 else 0.0
        else:
            result.max_drawdown_pct = 0.0
