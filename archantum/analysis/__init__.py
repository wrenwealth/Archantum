"""Analysis modules for detecting trading opportunities."""

from .arbitrage import ArbitrageAnalyzer, ArbitrageOpportunity
from .volume import VolumeAnalyzer, VolumeSpike
from .price import PriceAnalyzer, PriceMovement
from .trends import TrendAnalyzer, TrendSignal

__all__ = [
    "ArbitrageAnalyzer",
    "ArbitrageOpportunity",
    "VolumeAnalyzer",
    "VolumeSpike",
    "PriceAnalyzer",
    "PriceMovement",
    "TrendAnalyzer",
    "TrendSignal",
]
