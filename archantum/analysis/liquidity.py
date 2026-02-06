"""Liquidity analysis and VWAP-adjusted arbitrage enrichment."""

from __future__ import annotations

from dataclasses import dataclass, field

from archantum.api.clob import CLOBClient, Orderbook, OrderbookLevel
from archantum.analysis.arbitrage import ArbitrageOpportunity


@dataclass
class LiquidityProfile:
    """Liquidity profile for one side (YES or NO) of a market."""

    token_id: str | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    bid_depth_usd: float = 0.0  # Total USD on bid side
    ask_depth_usd: float = 0.0  # Total USD on ask side
    vwap_100: float | None = None  # VWAP for $100 order
    vwap_500: float | None = None  # VWAP for $500 order
    vwap_1000: float | None = None  # VWAP for $1000 order
    slippage_pct_100: float = 0.0  # Slippage % at $100
    slippage_pct_500: float = 0.0  # Slippage % at $500
    slippage_pct_1000: float = 0.0  # Slippage % at $1000
    max_executable_usd: float = 0.0  # Max order before exhausting book


@dataclass
class LiquidityAdjustedArbitrage:
    """Arbitrage opportunity enriched with liquidity data."""

    opportunity: ArbitrageOpportunity
    yes_liquidity: LiquidityProfile
    no_liquidity: LiquidityProfile

    @property
    def combined_depth_usd(self) -> float:
        """Total liquidity depth across both sides."""
        return self.yes_liquidity.ask_depth_usd + self.no_liquidity.ask_depth_usd

    @property
    def max_position_usd(self) -> float:
        """Max position limited by thinnest side."""
        # Need to buy both YES and NO, so limited by the smaller side
        return min(
            self.yes_liquidity.max_executable_usd,
            self.no_liquidity.max_executable_usd,
        )

    @property
    def slippage_adjusted_profit_1000(self) -> float | None:
        """Profit for $1000 position after slippage."""
        if self.yes_liquidity.vwap_1000 is None or self.no_liquidity.vwap_1000 is None:
            return None
        total_vwap = self.yes_liquidity.vwap_1000 + self.no_liquidity.vwap_1000
        if total_vwap >= 1.0:
            return 0.0
        profit_pct = 1.0 - total_vwap
        shares = 1000 / total_vwap
        return shares * profit_pct


class LiquidityAnalyzer:
    """Analyzes orderbook liquidity for arbitrage opportunities."""

    def calculate_vwap(self, levels: list[OrderbookLevel], target_usd: float) -> tuple[float | None, float]:
        """Calculate volume-weighted average price for a target USD size.

        Walks through orderbook levels accumulating price*size until target is reached.

        Returns:
            (vwap, filled_usd): The VWAP and actual USD filled.
        """
        if not levels or target_usd <= 0:
            return None, 0.0

        total_cost = 0.0
        total_size = 0.0

        for level in levels:
            level_usd = level.price * level.size
            if total_cost + level_usd >= target_usd:
                # Partial fill of this level
                remaining_usd = target_usd - total_cost
                partial_size = remaining_usd / level.price if level.price > 0 else 0
                total_size += partial_size
                total_cost += remaining_usd
                break
            else:
                total_cost += level_usd
                total_size += level.size

        if total_size <= 0:
            return None, 0.0

        vwap = total_cost / total_size
        return vwap, total_cost

    def build_liquidity_profile(
        self,
        orderbook: Orderbook,
        midpoint: float | None = None,
        token_id: str | None = None,
    ) -> LiquidityProfile:
        """Build a liquidity profile from an orderbook."""
        profile = LiquidityProfile(token_id=token_id)

        profile.best_bid = orderbook.best_bid
        profile.best_ask = orderbook.best_ask

        # Calculate depth in USD
        profile.bid_depth_usd = sum(l.price * l.size for l in orderbook.bids)
        profile.ask_depth_usd = sum(l.price * l.size for l in orderbook.asks)

        # Sort asks ascending (cheapest first) for buy-side VWAP
        sorted_asks = sorted(orderbook.asks, key=lambda l: l.price)

        # VWAP at different sizes
        for target, attr_vwap, attr_slip in [
            (100, "vwap_100", "slippage_pct_100"),
            (500, "vwap_500", "slippage_pct_500"),
            (1000, "vwap_1000", "slippage_pct_1000"),
        ]:
            vwap, filled = self.calculate_vwap(sorted_asks, target)
            setattr(profile, attr_vwap, vwap)
            if vwap is not None and midpoint and midpoint > 0:
                slippage = ((vwap - midpoint) / midpoint) * 100
                setattr(profile, attr_slip, max(0, slippage))

        # Max executable = total ask depth
        profile.max_executable_usd = profile.ask_depth_usd

        return profile

    async def enrich_arbitrage(
        self,
        clob_client: CLOBClient,
        opp: ArbitrageOpportunity,
        market: "GammaMarket",
    ) -> LiquidityAdjustedArbitrage:
        """Fetch orderbooks and build liquidity-adjusted arbitrage data.

        Args:
            clob_client: Active CLOB client
            opp: The arbitrage opportunity to enrich
            market: The GammaMarket with token IDs

        Returns:
            LiquidityAdjustedArbitrage with full liquidity profiles
        """
        yes_profile = LiquidityProfile(token_id=market.yes_token_id)
        no_profile = LiquidityProfile(token_id=market.no_token_id)

        # Fetch YES orderbook
        if market.yes_token_id:
            try:
                yes_book = await clob_client.get_orderbook(market.yes_token_id)
                yes_profile = self.build_liquidity_profile(
                    yes_book, midpoint=opp.yes_price, token_id=market.yes_token_id
                )
            except Exception:
                pass

        # Fetch NO orderbook
        if market.no_token_id:
            try:
                no_book = await clob_client.get_orderbook(market.no_token_id)
                no_profile = self.build_liquidity_profile(
                    no_book, midpoint=opp.no_price, token_id=market.no_token_id
                )
            except Exception:
                pass

        return LiquidityAdjustedArbitrage(
            opportunity=opp,
            yes_liquidity=yes_profile,
            no_liquidity=no_profile,
        )
