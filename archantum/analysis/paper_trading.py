"""Paper trading engine for BTC 15-minute markets on Polymarket."""

from __future__ import annotations

import asyncio
import html
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import httpx
from rich.console import Console

from archantum.api.chainlink import ChainlinkClient
from archantum.api.gamma import GammaClient, GammaMarket
from archantum.api.hyperliquid import HyperliquidClient
from archantum.config import settings
from archantum.db import Database

console = Console()

WINDOW_SECONDS = 900  # 15 minutes


class TradeDirection(Enum):
    UP = "UP"
    DOWN = "DOWN"


class Confidence(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    SKIP = "SKIP"


class HourZone(Enum):
    SAFE = "SAFE"
    CAUTION = "CAUTION"
    BLACKLIST = "BLACKLIST"


@dataclass
class WindowState:
    """Tracks the current 15-minute window."""
    window_ts: int  # Unix timestamp of window start (floored to 900)
    window_start: datetime
    window_end: datetime
    btc_price_at_open: float
    market: GammaMarket | None = None
    market_id: str | None = None
    price_to_beat: float | None = None
    traded: bool = False


@dataclass
class PaperTradeSignal:
    """Output of signal evaluation."""
    direction: TradeDirection | None = None
    confidence: Confidence = Confidence.SKIP
    gap_usd: float = 0.0
    hour_zone: HourZone = HourZone.SAFE
    btc_price_at_open: float = 0.0
    btc_price_now: float = 0.0
    chainlink_price: float | None = None  # Chainlink BTC/USD for confirmation
    price_to_beat: float | None = None
    poly_up_price: float | None = None
    poly_down_price: float | None = None
    minutes_remaining: float = 0.0
    skip_reason: str | None = None
    market: GammaMarket | None = None
    chainlink_confirms: bool | None = None  # True if Chainlink agrees with Hyper direction


class PaperTradingEngine:
    """Automated paper trading engine for BTC 15-min markets.

    Strategy: exploit Polymarket UI price lag vs Hyperliquid.
    Polymarket resolves using Chainlink BTC/USD (close to Hyperliquid).
    When Hyperliquid shows BTC clearly above/below open price but Polymarket
    odds haven't caught up, there's an edge.
    """

    def __init__(self, db: Database, alerter: Any):
        self.db = db
        self.alerter = alerter
        self._running = False

        # State
        self._current_window: WindowState | None = None
        self._consecutive_losses = 0
        self._daily_losses = 0
        self._daily_reset_date: str | None = None  # "YYYY-MM-DD" UTC

        # Running stats (loaded from DB on start)
        self._total_wins = 0
        self._total_losses = 0
        self._total_pnl = 0.0

        # Market discovery cache: window_ts -> GammaMarket
        self._market_cache: dict[int, GammaMarket | None] = {}

        # Telegram skip notification: only send once per window to avoid spam
        self._last_skip_window: int = 0

        # Track traded windows to prevent duplicate trades (robust dedup)
        self._traded_windows: set[int] = set()

    # ── Market Discovery ────────────────────────────────────────────

    async def _discover_btc_15m_market(self, window_ts: int) -> GammaMarket | None:
        """Find the BTC 15-min market for a specific window using slug pattern.

        Slug pattern: btc-updown-15m-{window_start_unix}
        Outcomes: ["Up", "Down"] — resolves "Up" if BTC close >= open.
        """
        if window_ts in self._market_cache:
            return self._market_cache[window_ts]

        slug = f"btc-updown-15m-{window_ts}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://gamma-api.polymarket.com/events",
                    params={"slug": slug},
                )
                resp.raise_for_status()
                data = resp.json()

            if not data:
                self._market_cache[window_ts] = None
                return None

            event = data[0]
            markets = event.get("markets", [])
            if not markets:
                self._market_cache[window_ts] = None
                return None

            market = GammaMarket.model_validate(markets[0])
            self._market_cache[window_ts] = market

            # Evict old cache entries (keep last 10)
            if len(self._market_cache) > 10:
                oldest = sorted(self._market_cache.keys())[:-10]
                for k in oldest:
                    del self._market_cache[k]

            return market

        except Exception as e:
            console.print(f"[yellow]Paper trading: market discovery error: {e}[/yellow]")
            return self._market_cache.get(window_ts)

    # ── Filters ─────────────────────────────────────────────────────

    @staticmethod
    def _classify_hour_zone(utc_hour: int, utc_minute: int) -> HourZone:
        """Classify UTC hour into trading zone.

        BLACKLIST = 17:00-18:30 UTC (00:00-01:30 WIB, US session overlap)
        CAUTION = 11:00-16:59 UTC (18:00-23:59 WIB, US session)
        SAFE = everything else (04:00-17:59 WIB, Asian session)
        """
        minutes_from_midnight = utc_hour * 60 + utc_minute
        # BLACKLIST: 17:00 - 18:30 UTC
        if 17 * 60 <= minutes_from_midnight <= 18 * 60 + 30:
            return HourZone.BLACKLIST
        # CAUTION: 11:00 - 16:59 UTC
        if 11 * 60 <= minutes_from_midnight < 17 * 60:
            return HourZone.CAUTION
        return HourZone.SAFE

    @staticmethod
    def _get_min_gap(minutes_remaining: float) -> float | None:
        """Get minimum BTC gap (USD) required based on time remaining.

        Returns None if too early to trade (>5.5 min remaining).
        """
        if minutes_remaining <= 1.5:
            return 25.0
        elif minutes_remaining <= 2.5:
            return 50.0
        elif minutes_remaining <= 3.5:
            return 50.0
        elif minutes_remaining <= 5.5:
            return 75.0
        else:
            return None  # Too early, SKIP

    @staticmethod
    def _evaluate_confidence(
        hyper_gap: float,
        poly_up_price: float | None,
        chainlink_gap: float | None = None,
        btc_open: float = 0.0,
    ) -> tuple[Confidence, str, bool | None]:
        """Evaluate confidence based on Hyperliquid vs Polymarket price lag.

        The "money glitch": Polymarket UI lags behind real BTC price.
        Resolution uses Chainlink (close to Hyperliquid), so when Hyper
        shows a clear gap but Poly odds are still lagging, that's our edge.

        hyper_gap: BTC_now (Hyperliquid) - btc_open (positive = UP)
        poly_up_price: Polymarket "Up" outcome price (>0.50 = market leans UP)
        chainlink_gap: BTC_now (Chainlink) - btc_open (for confirmation)
        btc_open: BTC price at window open

        Returns (confidence, reasoning, chainlink_confirms).
        """
        hyper_dir = "UP" if hyper_gap > 0 else "DOWN"

        # Check if Chainlink confirms Hyperliquid direction
        chainlink_confirms = None
        chainlink_info = ""
        if chainlink_gap is not None:
            chainlink_dir = "UP" if chainlink_gap > 0 else "DOWN"
            chainlink_confirms = (chainlink_dir == hyper_dir)
            if chainlink_confirms:
                chainlink_info = f" [Chainlink confirms: {chainlink_dir} ${chainlink_gap:+.0f}]"
            else:
                # Chainlink disagrees with Hyperliquid — reduce confidence or skip
                return (
                    Confidence.SKIP,
                    f"Chainlink disagrees: Hyper={hyper_dir} (${hyper_gap:+.0f}) vs Chainlink={chainlink_dir} (${chainlink_gap:+.0f})",
                    False,
                )

        if poly_up_price is None:
            conf = Confidence.MEDIUM if abs(hyper_gap) > 0 else Confidence.SKIP
            # Boost to HIGH if Chainlink confirms
            if chainlink_confirms and conf == Confidence.MEDIUM:
                conf = Confidence.HIGH
            return (
                conf,
                f"No Poly data — Hyper-only signal{chainlink_info}",
                chainlink_confirms,
            )

        # Poly implied confidence strength (how far from 50/50)
        poly_strength = abs(poly_up_price - 0.50)

        # KEY: When Poly is near 50/50 (strength < 0.10), it means Poly
        # hasn't moved yet. This IS the lag scenario — follow Hyper direction
        # with HIGH confidence, regardless of which side of 50% Poly is on.
        if poly_strength < 0.10:
            return (
                Confidence.HIGH,
                f"Poly lagging: Hyper {hyper_dir} ${hyper_gap:+.0f} but Poly still ~50/50 (Up@{poly_up_price:.0%}){chainlink_info}",
                chainlink_confirms,
            )

        # Poly has moved enough to have a directional opinion
        poly_dir = "UP" if poly_up_price > 0.50 else "DOWN"

        if hyper_dir != poly_dir:
            # Hyper and Poly clearly disagree — SKIP
            return (
                Confidence.SKIP,
                f"Conflicting: Hyper={hyper_dir} (${hyper_gap:+.0f}) vs Poly={poly_dir} (Up@{poly_up_price:.0%})",
                chainlink_confirms,
            )

        # Same direction — check how much Poly has caught up
        if poly_strength < 0.30:
            # Poly moved somewhat but Hyper leads
            return (
                Confidence.HIGH,
                f"Hyper leads: gap ${hyper_gap:+.0f}, Poly catching up (Up@{poly_up_price:.0%}){chainlink_info}",
                chainlink_confirms,
            )
        else:
            # Poly already moved strongly — consensus, less edge but still valid
            # Boost to HIGH if Chainlink confirms
            conf = Confidence.HIGH if chainlink_confirms else Confidence.MEDIUM
            return (
                conf,
                f"Consensus: both agree {hyper_dir}, Poly at Up@{poly_up_price:.0%}{chainlink_info}",
                chainlink_confirms,
            )

    def _evaluate_signal(
        self,
        btc_now: float,
        price_to_beat: float,
        minutes_remaining: float,
        utc_hour: int,
        utc_minute: int,
        poly_up_price: float | None = None,
        btc_at_open: float = 0.0,
        market: GammaMarket | None = None,
        chainlink_price: float | None = None,
    ) -> PaperTradeSignal:
        """Evaluate whether to place a paper trade."""
        signal = PaperTradeSignal(
            btc_price_at_open=btc_at_open,
            btc_price_now=btc_now,
            chainlink_price=chainlink_price,
            price_to_beat=price_to_beat,
            poly_up_price=poly_up_price,
            poly_down_price=(1.0 - poly_up_price) if poly_up_price is not None else None,
            minutes_remaining=minutes_remaining,
            market=market,
        )

        # 1. Hour zone check
        zone = self._classify_hour_zone(utc_hour, utc_minute)
        signal.hour_zone = zone
        if zone == HourZone.BLACKLIST:
            signal.skip_reason = f"BLACKLIST zone ({utc_hour}:{utc_minute:02d} UTC / {utc_hour+7}:{utc_minute:02d} WIB)"
            return signal

        # 2. Time filter — only trade in last 5.5 minutes
        min_gap = self._get_min_gap(minutes_remaining)
        if min_gap is None:
            signal.skip_reason = f"Too early ({minutes_remaining:.1f}min remaining, need <=5.5min)"
            return signal

        # 3. Gap calculation
        hyper_gap = btc_now - price_to_beat
        abs_gap = abs(hyper_gap)
        signal.gap_usd = hyper_gap

        if abs_gap < min_gap:
            signal.skip_reason = f"Gap too small: ${abs_gap:.0f} < required ${min_gap:.0f} (at {minutes_remaining:.1f}min left)"
            return signal

        # 4. Direction
        direction = TradeDirection.UP if hyper_gap > 0 else TradeDirection.DOWN
        signal.direction = direction

        # 5. Confidence (Hyper vs Poly lag detection + Chainlink confirmation)
        chainlink_gap = (chainlink_price - price_to_beat) if chainlink_price else None
        confidence, reason, chainlink_confirms = self._evaluate_confidence(
            hyper_gap, poly_up_price, chainlink_gap, price_to_beat
        )
        signal.confidence = confidence
        signal.chainlink_confirms = chainlink_confirms
        if confidence == Confidence.SKIP:
            signal.skip_reason = reason
            return signal

        # 6. CAUTION zone: only allow HIGH confidence
        if zone == HourZone.CAUTION and confidence != Confidence.HIGH:
            signal.skip_reason = f"CAUTION zone needs HIGH confidence, got {confidence.value}: {reason}"
            return signal

        # 7. Daily loss limits
        self._check_daily_reset()
        if self._consecutive_losses >= settings.paper_trading_max_consecutive_losses:
            signal.skip_reason = f"Consecutive loss limit ({self._consecutive_losses}/{settings.paper_trading_max_consecutive_losses})"
            signal.confidence = Confidence.SKIP
            return signal
        if self._daily_losses >= settings.paper_trading_max_daily_losses:
            signal.skip_reason = f"Daily loss limit ({self._daily_losses}/{settings.paper_trading_max_daily_losses})"
            signal.confidence = Confidence.SKIP
            return signal

        # Attach reasoning to skip_reason field (used for display even on trade)
        signal.skip_reason = reason
        return signal

    # ── Trade Execution ─────────────────────────────────────────────

    async def _place_paper_trade(self, signal: PaperTradeSignal) -> int | None:
        """Save paper trade to DB and send entry alert."""
        if not signal.direction or signal.confidence == Confidence.SKIP:
            return None

        window = self._current_window
        if not window:
            return None

        # Use actual Polymarket price for entry, fallback to config if unavailable
        if signal.direction == TradeDirection.UP:
            actual_entry_price = signal.poly_up_price or settings.paper_trading_entry_price
        else:
            actual_entry_price = signal.poly_down_price or settings.paper_trading_entry_price

        trade_data = {
            "window_id": str(window.window_ts),
            "window_start": window.window_start,
            "window_end": window.window_end,
            "market_id": window.market_id,
            "btc_price_at_open": signal.btc_price_at_open,
            "btc_price_at_entry": signal.btc_price_now,
            "gap_usd": signal.gap_usd,
            "direction": signal.direction.value,
            "confidence": signal.confidence.value,
            "entry_price": actual_entry_price,
            "trade_size_usd": settings.paper_trading_trade_size,
            "minutes_to_resolve": signal.minutes_remaining,
            "hour_zone": signal.hour_zone.value,
            "entry_at": datetime.utcnow(),
        }

        trade = await self.db.save_paper_trade(trade_data)

        # Send entry alert
        stats = await self.db.get_paper_trade_stats()
        msg = self._format_entry_alert(signal, trade.id, stats, actual_entry_price)
        await self.alerter.send_raw_message(msg)

        window.traded = True
        self._traded_windows.add(window.window_ts)  # Robust dedup
        console.print(f"[bold green]Paper trade #{trade.id} placed: {signal.direction.value} (gap ${signal.gap_usd:.0f})[/bold green]")
        return trade.id

    # ── Resolution ──────────────────────────────────────────────────

    async def _check_resolution(self) -> None:
        """Check and resolve pending paper trades via Gamma API market state."""
        pending = await self.db.get_pending_paper_trades()
        if not pending:
            return

        now = datetime.utcnow()

        # Get current BTC price once for all resolutions
        btc_close: float | None = None
        try:
            async with HyperliquidClient() as hl:
                btc_close = await hl.get_btc_mid_price()
        except Exception:
            pass

        for trade in pending:
            # Only check trades whose window has ended
            if now < trade.window_end:
                continue

            # Timeout: mark inconclusive after 30 min past window end
            if (now - trade.window_end).total_seconds() > 1800:
                await self._resolve_trade(trade, None, btc_close=btc_close, inconclusive=True)
                continue

            # Fetch market from Gamma API to check resolution
            # window_id is the unix timestamp string we stored at creation
            window_ts = int(trade.window_id)
            # Invalidate cache so we get fresh closed/outcomePrices state
            self._market_cache.pop(window_ts, None)
            market = await self._discover_btc_15m_market(window_ts)
            if not market:
                # No market found — fall back to BTC price comparison
                if btc_close is not None:
                    price_to_beat = trade.btc_price_at_entry - trade.gap_usd
                    if btc_close > price_to_beat:
                        await self._resolve_trade(trade, TradeDirection.UP, btc_close=btc_close)
                    elif btc_close < price_to_beat:
                        await self._resolve_trade(trade, TradeDirection.DOWN, btc_close=btc_close)
                continue

            # Check if market is closed (resolved)
            if not market.closed:
                continue  # Not resolved yet, try again next tick

            # Parse outcomePrices to determine winner
            # Outcomes: ["Up", "Down"], resolved prices e.g. ["1", "0"] or ["0", "1"]
            resolved_dir = self._parse_market_resolution(market)
            if resolved_dir is None:
                continue

            # VALIDATION: Check if Polymarket resolution matches Chainlink prediction
            chainlink_expected = await self._get_chainlink_expected_resolution(trade)
            if chainlink_expected and resolved_dir != chainlink_expected:
                await self._alert_resolution_mismatch(trade, resolved_dir, chainlink_expected)

            await self._resolve_trade(trade, resolved_dir, btc_close=btc_close)

    @staticmethod
    def _parse_market_resolution(market: GammaMarket) -> TradeDirection | None:
        """Determine UP/DOWN winner from resolved market outcomePrices."""
        if not market.outcome_prices or len(market.outcome_prices) < 2:
            return None
        try:
            up_price = float(market.outcome_prices[0])
            down_price = float(market.outcome_prices[1])
        except (ValueError, TypeError):
            return None

        if up_price > down_price:
            return TradeDirection.UP
        elif down_price > up_price:
            return TradeDirection.DOWN
        return None  # Tie / not yet resolved

    async def _get_chainlink_expected_resolution(self, trade) -> TradeDirection | None:
        """Get what Chainlink says the resolution should be based on current price vs open."""
        try:
            async with ChainlinkClient() as cl:
                chainlink_close = await cl.get_btc_price()
                if chainlink_close is None:
                    return None

                # price_to_beat = btc_price_at_entry - gap_usd (reconstructed open price)
                price_to_beat = trade.btc_price_at_open

                if chainlink_close >= price_to_beat:
                    return TradeDirection.UP
                else:
                    return TradeDirection.DOWN
        except Exception as e:
            console.print(f"[yellow]Chainlink validation check failed: {e}[/yellow]")
            return None

    async def _alert_resolution_mismatch(
        self,
        trade,
        poly_resolved: TradeDirection,
        chainlink_expected: TradeDirection,
    ) -> None:
        """Alert when Polymarket resolution doesn't match Chainlink expectation."""
        console.print(
            f"[bold red]RESOLUTION MISMATCH Trade #{trade.id}: "
            f"Poly={poly_resolved.value} vs Chainlink={chainlink_expected.value}[/bold red]"
        )

        msg = f"""⚠️ <b>RESOLUTION MISMATCH DETECTED</b>

<b>Trade #{trade.id}</b>
<b>Polymarket resolved:</b> {poly_resolved.value}
<b>Chainlink expected:</b> {chainlink_expected.value}

<b>BTC Open (our record):</b> ${trade.btc_price_at_open:,.2f}
<b>Gap at entry:</b> ${trade.gap_usd:+.0f}

⚠️ Polymarket may have UI/data bug. Resolution might be incorrect.
Consider pausing paper trading until resolved."""

        try:
            await self.alerter.send_raw_message(msg)
        except Exception as e:
            console.print(f"[red]Failed to send mismatch alert: {e}[/red]")

    async def _resolve_trade(
        self,
        trade,
        resolved_dir: TradeDirection | None,
        btc_close: float | None = None,
        inconclusive: bool = False,
    ) -> None:
        """Resolve a single paper trade."""
        if inconclusive:
            await self.db.resolve_paper_trade(
                trade_id=trade.id,
                resolved_direction="INCONCLUSIVE",
                win=False,
                pnl_usd=0.0,
                btc_price_at_close=btc_close or 0.0,
            )
            console.print(f"[yellow]Paper trade #{trade.id} inconclusive (timeout)[/yellow]")
            return

        win = (resolved_dir.value == trade.direction)
        entry_price = trade.entry_price
        trade_size = trade.trade_size_usd

        if win:
            # Win: bought at entry_price, payout is $1 per share
            # shares = trade_size / entry_price
            # profit = shares * (1 - entry_price)
            pnl = trade_size * (1.0 - entry_price) / entry_price
            self._total_wins += 1
            self._consecutive_losses = 0
        else:
            # Loss: lose the trade size
            pnl = -trade_size
            self._total_losses += 1
            self._consecutive_losses += 1
            self._daily_losses += 1

        self._total_pnl += pnl

        await self.db.resolve_paper_trade(
            trade_id=trade.id,
            resolved_direction=resolved_dir.value,
            win=win,
            pnl_usd=pnl,
            btc_price_at_close=btc_close or 0.0,
            running_pnl=self._total_pnl,
            running_wins=self._total_wins,
            running_losses=self._total_losses,
        )

        # Send result alert
        stats = await self.db.get_paper_trade_stats()
        msg = self._format_result_alert(trade, resolved_dir, win, pnl, btc_close, stats)
        await self.alerter.send_raw_message(msg)

        result_str = "WIN" if win else "LOSS"
        console.print(f"[{'green' if win else 'red'}]Paper trade #{trade.id} {result_str}: ${pnl:+.2f}[/{'green' if win else 'red'}]")

    # ── Alert Formatting ────────────────────────────────────────────

    def _format_skip_alert(self, signal: PaperTradeSignal) -> str:
        """Format a Telegram notification when a trade is skipped."""
        now_utc = datetime.utcnow()
        now_et = now_utc - timedelta(hours=5)  # ET = UTC-5
        now_wib = now_utc + timedelta(hours=7)

        poly_text = "N/A"
        if signal.poly_up_price is not None:
            poly_text = f"Up {signal.poly_up_price:.0%} / Down {signal.poly_down_price:.0%}"

        gap_text = f"${signal.gap_usd:+.0f}" if signal.gap_usd != 0 else "N/A"
        dir_text = signal.direction.value if signal.direction else "N/A"

        # Chainlink confirmation status
        chainlink_text = ""
        if signal.chainlink_price is not None:
            cl_status = "✓" if signal.chainlink_confirms else "✗"
            chainlink_text = f"\n<b>Chainlink:</b> ${signal.chainlink_price:,.2f} {cl_status}"

        # HTML-escape the reason string (may contain < > from gap comparisons)
        reason = html.escape(signal.skip_reason or "Unknown")

        return f"""⏭ <b>PAPER TRADE — SKIP</b>

<b>Reason:</b> {reason}

<b>BTC Open:</b> ${signal.btc_price_at_open:,.2f}
<b>BTC Now (Hyper):</b> ${signal.btc_price_now:,.2f}{chainlink_text}
<b>Gap:</b> {gap_text} → {dir_text}
<b>Poly:</b> {poly_text}
<b>Zone:</b> {signal.hour_zone.value} | <b>Time left:</b> {signal.minutes_remaining:.1f}min
<b>Time:</b> {now_et.strftime('%H:%M:%S')} ET / {now_wib.strftime('%H:%M:%S')} WIB"""

    def _format_entry_alert(self, signal: PaperTradeSignal, trade_id: int, stats: dict, entry_price: float) -> str:
        """Format paper trade entry alert."""
        dir_emoji = "🟢" if signal.direction == TradeDirection.UP else "🔴"
        conf_emoji = "⚡" if signal.confidence == Confidence.HIGH else "🟡"
        zone_emoji = {"SAFE": "🟢", "CAUTION": "🟡", "BLACKLIST": "🔴"}.get(signal.hour_zone.value, "⚪")

        # Time in ET and WIB
        now_utc = datetime.utcnow()
        now_et = now_utc - timedelta(hours=5)  # ET = UTC-5
        now_wib = now_utc + timedelta(hours=7)

        poly_text = ""
        if signal.poly_up_price is not None:
            poly_text = f"\n<b>Poly:</b> Up {signal.poly_up_price:.0%} / Down {signal.poly_down_price:.0%}"

        # Chainlink confirmation
        chainlink_text = ""
        if signal.chainlink_price is not None:
            cl_emoji = "✅" if signal.chainlink_confirms else "⚠️"
            chainlink_text = f"\n<b>Chainlink:</b> ${signal.chainlink_price:,.2f} {cl_emoji}"

        record = f"{stats['wins']}W {stats['losses']}L"
        pnl_emoji = "📈" if stats['total_pnl'] >= 0 else "📉"

        # Reasoning from confidence evaluation (HTML-escaped)
        reasoning = html.escape(signal.skip_reason or "")

        return f"""📝 <b>PAPER TRADE #{trade_id} — ENTRY</b>

{dir_emoji} <b>Direction:</b> {signal.direction.value}
{conf_emoji} <b>Confidence:</b> {signal.confidence.value}
{zone_emoji} <b>Zone:</b> {signal.hour_zone.value}

<b>Why:</b> {reasoning}

<b>BTC Open:</b> ${signal.btc_price_at_open:,.2f}
<b>BTC Now (Hyper):</b> ${signal.btc_price_now:,.2f}{chainlink_text}{poly_text}
<b>Gap:</b> ${signal.gap_usd:+.0f}

<b>Entry Price:</b> {entry_price:.0%} (actual Poly)
<b>Trade Size:</b> ${settings.paper_trading_trade_size:.0f}
<b>Time Left:</b> {signal.minutes_remaining:.1f} min

<b>Time:</b> {now_et.strftime('%H:%M:%S')} ET / {now_wib.strftime('%H:%M:%S')} WIB

{pnl_emoji} <b>Running:</b> {record} | PnL ${stats['total_pnl']:+.2f}"""

    def _format_result_alert(
        self,
        trade,
        resolved_dir: TradeDirection,
        win: bool,
        pnl: float,
        btc_close: float | None,
        stats: dict,
    ) -> str:
        """Format paper trade result alert."""
        result_emoji = "✅" if win else "❌"
        dir_emoji = "🟢" if trade.direction == "UP" else "🔴"
        resolved_emoji = "🟢" if resolved_dir.value == "UP" else "🔴"

        pnl_emoji = "📈" if stats['total_pnl'] >= 0 else "📉"
        record = f"{stats['wins']}W {stats['losses']}L"
        win_rate = f"{stats['win_rate']:.0f}%"

        btc_close_str = f"${btc_close:,.2f}" if btc_close else "N/A"

        return f"""{result_emoji} <b>PAPER TRADE #{trade.id} — {"WIN" if win else "LOSS"}</b>

{dir_emoji} <b>Bet:</b> {trade.direction}
{resolved_emoji} <b>Result:</b> {resolved_dir.value}
<b>P&L:</b> ${pnl:+.2f}

<b>BTC Open:</b> ${trade.btc_price_at_open:,.2f}
<b>BTC Entry:</b> ${trade.btc_price_at_entry:,.2f}
<b>BTC Close:</b> {btc_close_str}
<b>Gap at Entry:</b> ${trade.gap_usd:+.0f}

{pnl_emoji} <b>Running:</b> {record} ({win_rate}) | PnL ${stats['total_pnl']:+.2f}
<b>Today:</b> {stats['today_wins']}W {stats['today_losses']}L | ${stats['today_pnl']:+.2f}"""

    # ── Daily Reset ─────────────────────────────────────────────────

    def _check_daily_reset(self) -> None:
        """Reset daily counters at midnight UTC."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self._daily_reset_date != today:
            self._daily_losses = 0
            self._consecutive_losses = 0
            self._daily_reset_date = today

    # ── Main Loop ───────────────────────────────────────────────────

    async def _tick(self) -> None:
        """Single tick of the paper trading engine."""
        now = datetime.utcnow()
        unix_now = int(time.time())  # Use time.time() — utcnow().timestamp() is local tz

        # Current window boundaries
        window_ts = (unix_now // WINDOW_SECONDS) * WINDOW_SECONDS
        window_start = datetime.utcfromtimestamp(window_ts)
        window_end = datetime.utcfromtimestamp(window_ts + WINDOW_SECONDS)

        # Detect window transition
        if self._current_window is None or self._current_window.window_ts != window_ts:
            # New window — capture opening price from Hyperliquid
            try:
                async with HyperliquidClient() as hl:
                    btc_open = await hl.get_btc_mid_price()
            except Exception as e:
                console.print(f"[yellow]Paper trading: cannot get BTC open price: {e}[/yellow]")
                return

            # Discover market for this window via slug pattern
            market = await self._discover_btc_15m_market(window_ts)
            market_id = market.id if market else None

            # These markets resolve "Up" if BTC close >= open.
            # price_to_beat = BTC price at window open (captured by Hyperliquid)
            price_to_beat = btc_open

            self._current_window = WindowState(
                window_ts=window_ts,
                window_start=window_start,
                window_end=window_end,
                btc_price_at_open=btc_open,
                market=market,
                market_id=market_id,
                price_to_beat=price_to_beat,
            )

            wib_start = window_start + timedelta(hours=7)
            console.print(
                f"[dim]Paper trading: new window {wib_start.strftime('%H:%M')} WIB "
                f"| BTC open ${btc_open:,.2f} "
                f"| market: {market_id or 'none'}[/dim]"
            )

            # Cleanup old traded windows (keep last 1 hour = 4 windows)
            cutoff_ts = window_ts - 3600
            self._traded_windows = {ts for ts in self._traded_windows if ts > cutoff_ts}

        window = self._current_window

        # Already traded this window (check both flags for robustness)
        if window.traded or window.window_ts in self._traded_windows:
            return

        # Calculate minutes remaining
        seconds_remaining = (window.window_end - now).total_seconds()
        minutes_remaining = seconds_remaining / 60.0

        # Only evaluate in last 5.5 minutes
        if minutes_remaining > 5.5:
            return

        # Get current BTC price from Hyperliquid
        try:
            async with HyperliquidClient() as hl:
                btc_now = await hl.get_btc_mid_price()
        except Exception as e:
            console.print(f"[yellow]Paper trading: cannot get BTC price: {e}[/yellow]")
            return

        # Get Chainlink BTC price for confirmation (resolution oracle)
        chainlink_price = None
        try:
            async with ChainlinkClient() as cl:
                chainlink_price = await cl.get_btc_price()
                if chainlink_price:
                    console.print(
                        f"[dim]Paper trading: Hyper ${btc_now:,.2f} | Chainlink ${chainlink_price:,.2f} "
                        f"| diff ${abs(btc_now - chainlink_price):,.2f}[/dim]"
                    )
        except Exception as e:
            console.print(f"[yellow]Paper trading: Chainlink unavailable: {e}[/yellow]")

        # Fetch fresh Polymarket "Up" price for this window's market
        poly_up = None
        if window.market_id:
            # Invalidate cache first to get fresh prices
            self._market_cache.pop(window.window_ts, None)
            fresh_market = await self._discover_btc_15m_market(window.window_ts)
            if fresh_market and fresh_market.outcome_prices:
                try:
                    # outcomes: ["Up", "Down"], outcomePrices: ["0.6", "0.4"]
                    poly_up = float(fresh_market.outcome_prices[0])
                except (ValueError, TypeError, IndexError):
                    pass

        # Evaluate signal
        signal = self._evaluate_signal(
            btc_now=btc_now,
            price_to_beat=window.price_to_beat,
            minutes_remaining=minutes_remaining,
            utc_hour=now.hour,
            utc_minute=now.minute,
            poly_up_price=poly_up,
            btc_at_open=window.btc_price_at_open,
            market=window.market,
            chainlink_price=chainlink_price,
        )

        if signal.confidence == Confidence.SKIP:
            console.print(f"[dim]Paper trading: SKIP — {signal.skip_reason}[/dim]")
            # Send skip notification to Telegram (once per window)
            if window.window_ts != self._last_skip_window:
                self._last_skip_window = window.window_ts
                try:
                    msg = self._format_skip_alert(signal)
                    await self.alerter.send_raw_message(msg)
                except Exception as e:
                    console.print(f"[red]Paper trading: skip alert error: {e}[/red]")
            return

        # Place trade
        await self._place_paper_trade(signal)

    async def run(self) -> None:
        """Run the paper trading loop."""
        self._running = True
        console.print("[bold green]Paper Trading Engine started[/bold green]")

        # Load running stats from DB
        stats = await self.db.get_paper_trade_stats()
        self._total_wins = stats["wins"]
        self._total_losses = stats["losses"]
        self._total_pnl = stats["total_pnl"]
        self._check_daily_reset()

        while self._running:
            try:
                await self._tick()
                await self._check_resolution()
            except Exception as e:
                console.print(f"[red]Paper trading tick error: {e}[/red]")

            await asyncio.sleep(settings.paper_trading_poll_interval)

    def stop(self) -> None:
        """Stop the paper trading loop."""
        self._running = False
        console.print("[yellow]Paper Trading Engine stopped[/yellow]")
