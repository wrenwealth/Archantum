"""Backtest: BTC 15-minute up/down strategy.

Strategy: 5 minutes before a 15-min window resolves, check BTC price direction
from the window start. If BTC is up → bet YES (up). If BTC is down → bet NO (down).
Hold until resolution.

Uses Binance public 1-minute kline API (no key needed).
"""

import asyncio
import httpx
import statistics
from datetime import datetime, timedelta
from dataclasses import dataclass, field


@dataclass
class TradeResult:
    window_start: datetime
    entry_time: datetime  # 10 min into window (5 min before resolve)
    resolve_time: datetime  # 15 min mark
    open_price: float  # price at window start
    entry_price: float  # price at 10-min mark
    close_price: float  # price at 15-min mark (resolution)
    direction_at_entry: str  # "up" or "down"
    resolved_direction: str  # "up" or "down"
    win: bool


@dataclass
class BacktestResult:
    total_windows: int = 0
    total_trades: int = 0  # windows where there was a clear direction at entry
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_price_move_pct: float = 0.0
    trades: list[TradeResult] = field(default_factory=list)

    # Breakdown by direction
    up_trades: int = 0
    up_wins: int = 0
    down_trades: int = 0
    down_wins: int = 0

    # Streaks
    max_win_streak: int = 0
    max_loss_streak: int = 0

    # By hour
    hourly_stats: dict = field(default_factory=dict)


async def fetch_btc_klines(days: int = 30) -> list[dict]:
    """Fetch BTC/USD price data and interpolate to 1-minute resolution.

    Uses CoinGecko (1-day chunks → ~5 min intervals) for long ranges,
    CryptoCompare (~1 min intervals) for the most recent ~7 days.
    Interpolates everything to 1-minute resolution.
    """
    raw_prices: list[tuple[int, float]] = []  # (timestamp_ms, price)
    end_ts = int(datetime.utcnow().timestamp())
    start_ts = end_ts - days * 86400

    print(f"Fetching BTC price data for {days} days...")

    async with httpx.AsyncClient(timeout=30, verify=False, follow_redirects=True) as client:
        # Phase 1: CoinGecko per-day chunks (~5 min intervals)
        print("  [CoinGecko] Fetching daily chunks...")
        for day_offset in range(days, 0, -1):
            day_start = end_ts - day_offset * 86400
            day_end = day_start + 86400

            try:
                resp = await client.get(
                    "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart/range",
                    params={"vs_currency": "usd", "from": day_start, "to": day_end},
                )
                if resp.status_code == 429:
                    print(f"  Rate limited, waiting 60s...")
                    await asyncio.sleep(60)
                    resp = await client.get(
                        "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart/range",
                        params={"vs_currency": "usd", "from": day_start, "to": day_end},
                    )
                resp.raise_for_status()
                data = resp.json()
                prices = data.get("prices", [])
                for ts_ms, price in prices:
                    if price and price > 0:
                        raw_prices.append((int(ts_ms), price))
            except Exception as e:
                print(f"  Day {day_offset}: error {type(e).__name__}: {e}")

            if day_offset % 5 == 0:
                print(f"  ...{days - day_offset}/{days} days fetched ({len(raw_prices)} points)")

            # CoinGecko free: ~10-30 req/min
            await asyncio.sleep(2.5)

        # Phase 2: CryptoCompare for recent data (~1 min intervals, ~7 days)
        print("  [CryptoCompare] Fetching recent 1-min data...")
        cc_end = end_ts
        for _ in range(5):  # ~5 batches × 2000 = ~7 days
            try:
                resp = await client.get(
                    "https://min-api.cryptocompare.com/data/v2/histominute",
                    params={"fsym": "BTC", "tsym": "USD", "limit": 2000, "toTs": cc_end},
                )
                resp.raise_for_status()
                data = resp.json()
                rows = data.get("Data", {}).get("Data", [])
                if not rows:
                    break
                for k in rows:
                    if k["close"] > 0:
                        raw_prices.append((k["time"] * 1000, float(k["close"])))
                cc_end = rows[0]["time"] - 1
                await asyncio.sleep(0.3)
            except Exception:
                break

    # Sort and deduplicate
    raw_prices.sort(key=lambda x: x[0])
    deduped: list[tuple[int, float]] = []
    seen_ts = set()
    for ts_ms, price in raw_prices:
        rounded = (ts_ms // 60_000) * 60_000
        if rounded not in seen_ts:
            seen_ts.add(rounded)
            deduped.append((rounded, price))

    if len(deduped) < 100:
        print(f"  Not enough data: {len(deduped)} points")
        return []

    print(f"  Raw data: {len(deduped)} unique points")
    print("  Interpolating to 1-minute resolution...")

    # Interpolate to 1-minute resolution
    all_klines = []
    for i in range(len(deduped) - 1):
        ts1, p1 = deduped[i]
        ts2, p2 = deduped[i + 1]

        # Fill each minute between ts1 and ts2
        gap_minutes = (ts2 - ts1) // 60_000
        if gap_minutes <= 0:
            continue
        if gap_minutes > 120:
            # Skip large gaps (>2 hours — likely missing data)
            continue

        for m in range(gap_minutes):
            interp_ts = ts1 + m * 60_000
            frac = m / gap_minutes
            interp_price = p1 + (p2 - p1) * frac
            all_klines.append({
                "timestamp": interp_ts,
                "open": interp_price,
                "high": interp_price,
                "low": interp_price,
                "close": interp_price,
                "volume": 0,
            })

    print(f"  Total: {len(all_klines)} 1-min candles ({len(all_klines) / 1440:.1f} days)")
    return all_klines


def build_price_map(klines: list[dict]) -> dict[int, float]:
    """Build minute-resolution timestamp -> close price map."""
    price_map = {}
    for k in klines:
        # Round to minute
        minute_ts = (k["timestamp"] // 60_000) * 60_000
        price_map[minute_ts] = k["close"]
    return price_map


def run_backtest(klines: list[dict]) -> BacktestResult:
    """Run the 15-minute window backtest."""
    price_map = build_price_map(klines)
    result = BacktestResult()

    # Get time range
    min_ts = min(price_map.keys())
    max_ts = max(price_map.keys())

    # Align to 15-minute boundaries (Polymarket style)
    # Start from the first 15-min aligned timestamp
    first_aligned = ((min_ts // (15 * 60_000)) + 1) * (15 * 60_000)

    current = first_aligned
    win_streak = 0
    loss_streak = 0

    while current + 15 * 60_000 <= max_ts:
        window_start_ts = current
        entry_ts = current + 10 * 60_000  # 10 min in (5 min before resolve)
        resolve_ts = current + 15 * 60_000  # resolution

        # Get prices at key moments
        open_price = price_map.get(window_start_ts)
        entry_price = price_map.get(entry_ts)
        close_price = price_map.get(resolve_ts)

        if open_price and entry_price and close_price:
            result.total_windows += 1

            # Direction at entry (10-min mark vs start)
            if entry_price == open_price:
                # Flat — skip, no clear signal
                current += 15 * 60_000
                continue

            direction_at_entry = "up" if entry_price > open_price else "down"
            resolved_direction = "up" if close_price > open_price else "down"

            # Also skip if resolution is exactly flat (rare but possible)
            if close_price == open_price:
                current += 15 * 60_000
                continue

            win = direction_at_entry == resolved_direction

            trade = TradeResult(
                window_start=datetime.utcfromtimestamp(window_start_ts / 1000),
                entry_time=datetime.utcfromtimestamp(entry_ts / 1000),
                resolve_time=datetime.utcfromtimestamp(resolve_ts / 1000),
                open_price=open_price,
                entry_price=entry_price,
                close_price=close_price,
                direction_at_entry=direction_at_entry,
                resolved_direction=resolved_direction,
                win=win,
            )
            result.trades.append(trade)
            result.total_trades += 1

            if win:
                result.wins += 1
                win_streak += 1
                loss_streak = 0
                result.max_win_streak = max(result.max_win_streak, win_streak)
            else:
                result.losses += 1
                loss_streak += 1
                win_streak = 0
                result.max_loss_streak = max(result.max_loss_streak, loss_streak)

            # Direction breakdown
            if direction_at_entry == "up":
                result.up_trades += 1
                if win:
                    result.up_wins += 1
            else:
                result.down_trades += 1
                if win:
                    result.down_wins += 1

            # Hourly stats
            hour = trade.entry_time.hour
            if hour not in result.hourly_stats:
                result.hourly_stats[hour] = {"trades": 0, "wins": 0}
            result.hourly_stats[hour]["trades"] += 1
            if win:
                result.hourly_stats[hour]["wins"] += 1

        current += 15 * 60_000

    # Calculate final stats
    if result.total_trades > 0:
        result.win_rate = result.wins / result.total_trades * 100
        moves = []
        for t in result.trades:
            move = abs(t.close_price - t.open_price) / t.open_price * 100
            moves.append(move)
        result.avg_price_move_pct = statistics.mean(moves) if moves else 0

    return result


def print_report(result: BacktestResult, days: int):
    """Print formatted backtest report."""
    print("\n" + "=" * 60)
    print(f"  BTC 15-MIN UP/DOWN STRATEGY BACKTEST ({days} days)")
    print("=" * 60)

    print(f"\n  Strategy: Enter 5 min before resolution based on")
    print(f"  price direction from window start")

    print(f"\n{'─' * 60}")
    print(f"  OVERALL RESULTS")
    print(f"{'─' * 60}")
    print(f"  Total 15-min windows:  {result.total_windows}")
    print(f"  Tradeable windows:     {result.total_trades}")
    print(f"  Wins:                  {result.wins}")
    print(f"  Losses:                {result.losses}")
    print(f"  WIN RATE:              {result.win_rate:.1f}%")
    print(f"  Avg price move:        {result.avg_price_move_pct:.4f}%")
    print(f"  Max win streak:        {result.max_win_streak}")
    print(f"  Max loss streak:       {result.max_loss_streak}")

    print(f"\n{'─' * 60}")
    print(f"  DIRECTION BREAKDOWN")
    print(f"{'─' * 60}")
    up_wr = (result.up_wins / result.up_trades * 100) if result.up_trades > 0 else 0
    down_wr = (result.down_wins / result.down_trades * 100) if result.down_trades > 0 else 0
    print(f"  UP signals:    {result.up_trades:>5} trades | {result.up_wins:>5} wins | WR: {up_wr:.1f}%")
    print(f"  DOWN signals:  {result.down_trades:>5} trades | {result.down_wins:>5} wins | WR: {down_wr:.1f}%")

    print(f"\n{'─' * 60}")
    print(f"  HOURLY BREAKDOWN (UTC)")
    print(f"{'─' * 60}")
    for hour in sorted(result.hourly_stats.keys()):
        stats = result.hourly_stats[hour]
        wr = (stats["wins"] / stats["trades"] * 100) if stats["trades"] > 0 else 0
        bar = "█" * int(wr / 5)
        print(f"  {hour:02d}:00  {stats['trades']:>4} trades  WR: {wr:5.1f}%  {bar}")

    # Best/worst hours
    if result.hourly_stats:
        best_hour = max(
            result.hourly_stats.items(),
            key=lambda x: (x[1]["wins"] / x[1]["trades"]) if x[1]["trades"] >= 10 else 0,
        )
        worst_hour = min(
            result.hourly_stats.items(),
            key=lambda x: (x[1]["wins"] / x[1]["trades"]) if x[1]["trades"] >= 10 else 1,
        )
        best_wr = best_hour[1]["wins"] / best_hour[1]["trades"] * 100 if best_hour[1]["trades"] >= 10 else 0
        worst_wr = worst_hour[1]["wins"] / worst_hour[1]["trades"] * 100 if worst_hour[1]["trades"] >= 10 else 0
        print(f"\n  Best hour:   {best_hour[0]:02d}:00 ({best_wr:.1f}% WR, {best_hour[1]['trades']} trades)")
        print(f"  Worst hour:  {worst_hour[0]:02d}:00 ({worst_wr:.1f}% WR, {worst_hour[1]['trades']} trades)")

    # Simulated P&L (assuming $10 per trade on Polymarket, ~95c buy price for the winning side)
    print(f"\n{'─' * 60}")
    print(f"  SIMULATED P&L (assuming $10/trade)")
    print(f"{'─' * 60}")
    # On Polymarket, if you buy at ~5min before resolve, the price is typically 85-95c
    # Win: pay ~92c, get $1 → profit ~$0.08 per share
    # Lose: pay ~92c, get $0 → loss ~$0.92 per share
    # Using $10 position = ~10.87 shares at 92c
    buy_price = 0.92  # typical price 5 min before resolve
    shares_per_trade = 10 / buy_price
    win_profit = shares_per_trade * (1.0 - buy_price)
    loss_cost = shares_per_trade * buy_price

    total_pnl = (result.wins * win_profit) - (result.losses * loss_cost)
    print(f"  Assumed entry price:   ${buy_price:.2f}")
    print(f"  Per win profit:        ${win_profit:.2f}")
    print(f"  Per loss cost:         -${loss_cost:.2f}")
    print(f"  Total P&L:             ${total_pnl:+,.2f}")
    print(f"  ROI:                   {total_pnl / (result.total_trades * 10) * 100:+.1f}%")

    # Break-even win rate
    breakeven_wr = loss_cost / (win_profit + loss_cost) * 100
    print(f"\n  Break-even win rate:   {breakeven_wr:.1f}%")
    edge = result.win_rate - breakeven_wr
    print(f"  Your edge:             {edge:+.1f}%")

    print(f"\n{'=' * 60}")
    if edge > 0:
        print(f"  VERDICT: Strategy has a +{edge:.1f}% edge — potentially profitable")
    elif edge > -2:
        print(f"  VERDICT: Marginal — edge is near zero, fees/slippage likely eat it")
    else:
        print(f"  VERDICT: Negative edge — strategy loses money at typical prices")
    print(f"{'=' * 60}\n")


def print_threshold_analysis(result: BacktestResult):
    """Analyze: what $ gap between entry price and open price gives 90%+ WR?"""
    if not result.trades:
        return

    print(f"\n{'=' * 60}")
    print(f"  PRICE GAP THRESHOLD ANALYSIS")
    print(f"  (Min $ diff from open needed for target win rates)")
    print(f"{'=' * 60}")

    # Calculate absolute $ difference for each trade
    gaps = []
    for t in result.trades:
        gap = abs(t.entry_price - t.open_price)
        gaps.append((gap, t.win))

    # Sort by gap ascending
    gaps.sort(key=lambda x: x[0])

    # Analyze at various $ thresholds
    thresholds = [0, 10, 20, 30, 50, 75, 100, 125, 150, 175, 200, 250, 300, 400, 500, 750, 1000]

    print(f"\n  {'Gap ≥':>10}  {'Trades':>7}  {'Wins':>6}  {'WR':>7}  {'Skip%':>7}")
    print(f"  {'─' * 50}")

    target_90 = None
    target_92 = None
    target_95 = None

    for threshold in thresholds:
        filtered = [(g, w) for g, w in gaps if g >= threshold]
        if not filtered:
            break

        trades = len(filtered)
        wins = sum(1 for _, w in filtered if w)
        wr = wins / trades * 100
        skip_pct = (1 - trades / len(gaps)) * 100

        marker = ""
        if target_90 is None and wr >= 90:
            target_90 = threshold
            marker = " ← 90% WR"
        if target_92 is None and wr >= 92:
            target_92 = threshold
            marker = " ← 92% WR (break-even)"
        if target_95 is None and wr >= 95:
            target_95 = threshold
            marker = " ← 95% WR"

        print(f"  ${threshold:>8}  {trades:>7}  {wins:>6}  {wr:>6.1f}%  {skip_pct:>6.1f}%{marker}")

    # Also do percentage-based thresholds
    print(f"\n  {'Gap ≥ %':>10}  {'Trades':>7}  {'Wins':>6}  {'WR':>7}  {'Skip%':>7}")
    print(f"  {'─' * 50}")

    pct_thresholds = [0.0, 0.01, 0.02, 0.03, 0.05, 0.07, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5]

    for pct_thresh in pct_thresholds:
        filtered = []
        for t in result.trades:
            gap_pct = abs(t.entry_price - t.open_price) / t.open_price * 100
            if gap_pct >= pct_thresh:
                filtered.append(t.win)
        if not filtered:
            break

        trades = len(filtered)
        wins = sum(1 for w in filtered if w)
        wr = wins / trades * 100
        skip_pct = (1 - trades / len(result.trades)) * 100

        print(f"  {pct_thresh:>9.2f}%  {trades:>7}  {wins:>6}  {wr:>6.1f}%  {skip_pct:>6.1f}%")

    # Summary
    print(f"\n{'─' * 60}")
    print(f"  SUMMARY")
    print(f"{'─' * 60}")
    if target_90:
        filtered_90 = [(g, w) for g, w in gaps if g >= target_90]
        trades_90 = len(filtered_90)
        skip_90 = (1 - trades_90 / len(gaps)) * 100
        print(f"  For 90%+ WR: need ≥${target_90} gap ({trades_90} trades, skip {skip_90:.0f}%)")
    else:
        print(f"  90% WR: not achievable with any threshold in this data")

    if target_92:
        filtered_92 = [(g, w) for g, w in gaps if g >= target_92]
        trades_92 = len(filtered_92)
        skip_92 = (1 - trades_92 / len(gaps)) * 100
        print(f"  For 92%+ WR (break-even): need ≥${target_92} gap ({trades_92} trades, skip {skip_92:.0f}%)")
    else:
        print(f"  92% WR (break-even): not achievable with any threshold")

    if target_95:
        filtered_95 = [(g, w) for g, w in gaps if g >= target_95]
        trades_95 = len(filtered_95)
        skip_95 = (1 - trades_95 / len(gaps)) * 100
        print(f"  For 95%+ WR: need ≥${target_95} gap ({trades_95} trades, skip {skip_95:.0f}%)")
    else:
        print(f"  95% WR: not achievable with any threshold")

    # P&L simulation at 92% break-even threshold
    if target_92:
        buy_price = 0.92
        shares = 10 / buy_price
        win_profit = shares * (1.0 - buy_price)
        loss_cost = shares * buy_price

        filtered_trades = [(g, w) for g, w in gaps if g >= target_92]
        wins = sum(1 for _, w in filtered_trades if w)
        losses = len(filtered_trades) - wins
        pnl = (wins * win_profit) - (losses * loss_cost)
        roi = pnl / (len(filtered_trades) * 10) * 100 if filtered_trades else 0

        print(f"\n  Filtered P&L at ≥${target_92} threshold:")
        print(f"  {wins}W / {losses}L = ${pnl:+,.2f} ({roi:+.1f}% ROI)")
    print(f"{'=' * 60}\n")


def run_multi_timing_analysis(klines: list[dict]):
    """Analyze gap thresholds across different entry timings (1-12 min before resolve)."""
    price_map = build_price_map(klines)
    min_ts = min(price_map.keys())
    max_ts = max(price_map.keys())
    first_aligned = ((min_ts // (15 * 60_000)) + 1) * (15 * 60_000)

    # Entry timings: X minutes before resolve = entry at minute (15-X)
    entry_offsets = [1, 2, 3, 4, 5, 6, 7, 8, 10, 12]

    # Collect trades for each timing
    timing_trades: dict[int, list[tuple[float, bool]]] = {}

    for mins_before in entry_offsets:
        entry_minute = 15 - mins_before
        trades = []

        current = first_aligned
        while current + 15 * 60_000 <= max_ts:
            window_start_ts = current
            entry_ts = current + entry_minute * 60_000
            resolve_ts = current + 15 * 60_000

            open_price = price_map.get(window_start_ts)
            entry_price = price_map.get(entry_ts)
            close_price = price_map.get(resolve_ts)

            if open_price and entry_price and close_price:
                if entry_price != open_price and close_price != open_price:
                    gap = abs(entry_price - open_price)
                    direction_at_entry = "up" if entry_price > open_price else "down"
                    resolved_direction = "up" if close_price > open_price else "down"
                    win = direction_at_entry == resolved_direction
                    trades.append((gap, win))

            current += 15 * 60_000

        timing_trades[mins_before] = trades

    # Print comparison table
    print(f"\n{'=' * 70}")
    print(f"  ENTRY TIMING vs GAP THRESHOLD ANALYSIS")
    print(f"  (Which timing + gap combo gives 90%/92%/95% WR?)")
    print(f"{'=' * 70}")

    # Header
    gap_targets = [0, 25, 50, 75, 100, 150, 200, 300]
    print(f"\n  Win rates by entry timing and minimum $ gap:")
    print(f"  {'Entry':>8} | {'Total':>5}", end="")
    for g in gap_targets:
        print(f" | ${g:>4}", end="")
    print()
    print(f"  {'─' * 8}-+-{'─' * 5}", end="")
    for _ in gap_targets:
        print(f"-+-{'─' * 5}", end="")
    print()

    for mins_before in entry_offsets:
        trades = timing_trades[mins_before]
        label = f"{mins_before}m bfr"
        total = len(trades)
        print(f"  {label:>8} | {total:>5}", end="")

        for threshold in gap_targets:
            filtered = [(g, w) for g, w in trades if g >= threshold]
            if filtered:
                wr = sum(1 for _, w in filtered if w) / len(filtered) * 100
                n = len(filtered)
                # Color coding via markers
                if wr >= 95:
                    marker = "**"
                elif wr >= 92:
                    marker = "* "
                elif wr >= 90:
                    marker = "+ "
                else:
                    marker = "  "
                print(f" |{marker}{wr:4.0f}%", end="")
            else:
                print(f" |   N/A", end="")
        print()

    print(f"\n  Legend: ** = 95%+  * = 92%+ (break-even)  + = 90%+")

    # Find the sweet spots
    print(f"\n{'─' * 70}")
    print(f"  SWEET SPOTS (92%+ WR with most trades)")
    print(f"{'─' * 70}")

    sweet_spots = []
    for mins_before in entry_offsets:
        trades = timing_trades[mins_before]
        for threshold in [0, 10, 20, 30, 40, 50, 60, 75, 100, 125, 150, 200]:
            filtered = [(g, w) for g, w in trades if g >= threshold]
            if len(filtered) >= 20:  # min sample
                wr = sum(1 for _, w in filtered if w) / len(filtered) * 100
                if wr >= 92:
                    sweet_spots.append((mins_before, threshold, len(filtered), wr))

    # Sort by trades desc (most frequent first), then WR
    sweet_spots.sort(key=lambda x: (-x[2], -x[3]))
    seen = set()
    for mins_before, threshold, n_trades, wr in sweet_spots[:10]:
        key = (mins_before, threshold)
        if key in seen:
            continue
        seen.add(key)

        # Simulate P&L
        trades = timing_trades[mins_before]
        filtered = [(g, w) for g, w in trades if g >= threshold]
        wins = sum(1 for _, w in filtered if w)
        losses = len(filtered) - wins

        buy_price = 0.92
        shares = 10 / buy_price
        pnl = (wins * shares * 0.08) - (losses * shares * 0.92)
        roi = pnl / (len(filtered) * 10) * 100

        print(f"  {mins_before}m before + ${threshold} gap → {wr:.1f}% WR | {n_trades} trades | P&L: ${pnl:+.0f} ({roi:+.1f}%)")

    print(f"{'=' * 70}\n")


async def main():
    import sys

    days = 30
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except ValueError:
            pass

    klines = await fetch_btc_klines(days=days)
    if not klines:
        print("Failed to fetch BTC data")
        return

    result = run_backtest(klines)
    print_report(result, days)
    print_threshold_analysis(result)
    run_multi_timing_analysis(klines)


if __name__ == "__main__":
    asyncio.run(main())
