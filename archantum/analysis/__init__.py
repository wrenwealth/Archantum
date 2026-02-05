"""Analysis modules for detecting trading opportunities."""

from .arbitrage import ArbitrageAnalyzer, ArbitrageOpportunity
from .price import PriceAnalyzer, PriceMovement
from .trends import TrendAnalyzer, TrendSignal
from .whale import WhaleAnalyzer, WhaleActivity
from .new_market import NewMarketAnalyzer, NewMarket
from .resolution import ResolutionAnalyzer, ResolutionAlert
from .historical import HistoricalAnalyzer, PriceHistory, BacktestResult
from .accuracy import AccuracyTracker, AccuracyResult

__all__ = [
    "ArbitrageAnalyzer",
    "ArbitrageOpportunity",
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
    "HistoricalAnalyzer",
    "PriceHistory",
    "BacktestResult",
    "AccuracyTracker",
    "AccuracyResult",
]
