"""Analysis modules for detecting trading opportunities."""

from .arbitrage import ArbitrageAnalyzer, ArbitrageOpportunity
from .volume import VolumeAnalyzer, VolumeSpike
from .price import PriceAnalyzer, PriceMovement
from .trends import TrendAnalyzer, TrendSignal
from .whale import WhaleAnalyzer, WhaleActivity
from .new_market import NewMarketAnalyzer, NewMarket
from .resolution import ResolutionAnalyzer, ResolutionAlert
from .liquidity import LiquidityAnalyzer, LiquidityChange
from .historical import HistoricalAnalyzer, PriceHistory, BacktestResult

__all__ = [
    "ArbitrageAnalyzer",
    "ArbitrageOpportunity",
    "VolumeAnalyzer",
    "VolumeSpike",
    "PriceAnalyzer",
    "PriceMovement",
    "TrendAnalyzer",
    "TrendSignal",
    "WhaleAnalyzer",
    "WhaleActivity",
    "NewMarketAnalyzer",
    "NewMarket",
    "ResolutionAnalyzer",
    "ResolutionAlert",
    "LiquidityAnalyzer",
    "LiquidityChange",
    "HistoricalAnalyzer",
    "PriceHistory",
    "BacktestResult",
]
