"""Analysis modules for detecting trading opportunities."""

from .arbitrage import (
    ArbitrageAnalyzer,
    ArbitrageOpportunity,
    GuaranteedProfit,
    OpportunityReason,
    REASON_EXPLANATIONS,
    calculate_guaranteed_profit,
    classify_opportunity_reason,
)
from .price import PriceAnalyzer, PriceMovement
from .trends import TrendAnalyzer, TrendSignal
from .whale import WhaleAnalyzer, WhaleActivity
from .new_market import NewMarketAnalyzer, NewMarket
from .resolution import ResolutionAnalyzer, ResolutionAlert
from .historical import HistoricalAnalyzer, PriceHistory, BacktestResult
from .accuracy import AccuracyTracker, AccuracyResult
from .smartmoney import SmartMoneyTracker, SmartMoneyAlert
from .indicators import TechnicalIndicatorCalculator, IndicatorValues
from .confluence import ConfluenceAnalyzer, ConfluenceSignal
from .liquidity import LiquidityAnalyzer, LiquidityProfile, LiquidityAdjustedArbitrage
from .risk_score import ExecutionRiskScorer, ExecutionRiskScore
from .multi_outcome import MultiOutcomeAnalyzer, MultiOutcomeArbitrage, SumDeviationTracker
from .dependency import DependencyAnalyzer, DependencyArbitrage, DependencyType
from .speed_tracker import SpeedTracker
from .settlement import SettlementLagDetector, SettlementLagOpportunity
from .certain_outcome import CertainOutcomeDetector, CertainOutcomeOpportunity, CertainOutcomeTier
from .esports import EsportsArbitrageAnalyzer, EsportsOpportunity, EsportsTier, EsportsGame
from .wallet_strategy import WalletStrategyAnalyzer, WalletStrategyResult

__all__ = [
    "ArbitrageAnalyzer",
    "ArbitrageOpportunity",
    "GuaranteedProfit",
    "OpportunityReason",
    "REASON_EXPLANATIONS",
    "calculate_guaranteed_profit",
    "classify_opportunity_reason",
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
    "SmartMoneyTracker",
    "SmartMoneyAlert",
    "TechnicalIndicatorCalculator",
    "IndicatorValues",
    "ConfluenceAnalyzer",
    "ConfluenceSignal",
    "LiquidityAnalyzer",
    "LiquidityProfile",
    "LiquidityAdjustedArbitrage",
    "ExecutionRiskScorer",
    "ExecutionRiskScore",
    "MultiOutcomeAnalyzer",
    "MultiOutcomeArbitrage",
    "SumDeviationTracker",
    "DependencyAnalyzer",
    "DependencyArbitrage",
    "DependencyType",
    "SpeedTracker",
    "SettlementLagDetector",
    "SettlementLagOpportunity",
    "CertainOutcomeDetector",
    "CertainOutcomeOpportunity",
    "CertainOutcomeTier",
    "EsportsArbitrageAnalyzer",
    "EsportsOpportunity",
    "EsportsTier",
    "EsportsGame",
    "WalletStrategyAnalyzer",
    "WalletStrategyResult",
]
