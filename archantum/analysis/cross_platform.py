"""Cross-platform arbitrage detection between Polymarket and Kalshi."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from archantum.api.gamma import GammaMarket
from archantum.api.kalshi import KalshiMarket, KalshiPriceData
from archantum.api.clob import PriceData


class CrossPlatformTier(Enum):
    """Cross-platform arbitrage opportunity tiers."""

    NONE = "none"
    STANDARD = "standard"      # 2-5% edge
    HIGH_VALUE = "high_value"  # 5-10% edge
    ALPHA = "alpha"            # 10%+ edge


@dataclass
class MarketMatch:
    """Represents a matched market between platforms."""

    polymarket: GammaMarket
    kalshi: KalshiMarket
    match_score: float  # 0-1, how confident we are in the match
    match_method: str   # How the match was made


@dataclass
class CrossPlatformArbitrage:
    """Represents a cross-platform arbitrage opportunity."""

    # Market info
    polymarket_id: str
    polymarket_question: str
    polymarket_url: str | None
    kalshi_ticker: str
    kalshi_title: str
    kalshi_url: str

    # Prices
    poly_yes_price: float
    poly_no_price: float
    kalshi_yes_price: float
    kalshi_no_price: float

    # Arbitrage details
    arbitrage_type: str  # 'buy_poly_yes_sell_kalshi_yes', etc.
    spread_pct: float
    tier: CrossPlatformTier

    # Execution details
    buy_platform: str   # 'polymarket' or 'kalshi'
    buy_side: str       # 'yes' or 'no'
    buy_price: float
    sell_platform: str
    sell_side: str
    sell_price: float

    # Match confidence
    match_score: float

    def calculate_profit(self, position_size: float) -> float:
        """Calculate estimated profit for a given position size.

        Note: This is simplified - actual profit depends on:
        - Execution slippage
        - Platform fees (Polymarket ~2%, Kalshi ~1%)
        - Withdrawal/transfer costs
        """
        # Gross profit from spread
        gross_profit = position_size * (self.spread_pct / 100)

        # Estimate fees (~3% total for both platforms)
        estimated_fees = position_size * 0.03

        return max(0, gross_profit - estimated_fees)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "polymarket_id": self.polymarket_id,
            "polymarket_question": self.polymarket_question,
            "polymarket_url": self.polymarket_url,
            "kalshi_ticker": self.kalshi_ticker,
            "kalshi_title": self.kalshi_title,
            "kalshi_url": self.kalshi_url,
            "poly_yes_price": self.poly_yes_price,
            "poly_no_price": self.poly_no_price,
            "kalshi_yes_price": self.kalshi_yes_price,
            "kalshi_no_price": self.kalshi_no_price,
            "arbitrage_type": self.arbitrage_type,
            "spread_pct": self.spread_pct,
            "tier": self.tier.value,
            "buy_platform": self.buy_platform,
            "buy_side": self.buy_side,
            "buy_price": self.buy_price,
            "sell_platform": self.sell_platform,
            "sell_side": self.sell_side,
            "sell_price": self.sell_price,
            "match_score": self.match_score,
            "profit_100": self.calculate_profit(100),
            "profit_500": self.calculate_profit(500),
            "profit_1000": self.calculate_profit(1000),
        }


class CrossPlatformAnalyzer:
    """Analyzes arbitrage opportunities between Polymarket and Kalshi."""

    # Minimum spread to consider (after fees ~3%, need >3% to profit)
    MIN_SPREAD_PCT = 3.0

    # Tier thresholds
    TIER_ALPHA = 10.0       # 10%+ spread
    TIER_HIGH_VALUE = 5.0   # 5-10% spread
    TIER_STANDARD = 3.0     # 3-5% spread

    def __init__(self, min_spread_pct: float | None = None, min_match_score: float = 0.7):
        self.min_spread_pct = min_spread_pct or self.MIN_SPREAD_PCT
        self.min_match_score = min_match_score

    def match_markets(
        self,
        polymarkets: list[GammaMarket],
        kalshi_markets: list[KalshiMarket],
    ) -> list[MarketMatch]:
        """Match markets between platforms based on title similarity.

        Uses multiple matching strategies:
        1. Keyword extraction and matching
        2. Fuzzy string similarity
        3. Event/category matching
        """
        matches: list[MarketMatch] = []

        for poly in polymarkets:
            best_match: KalshiMarket | None = None
            best_score = 0.0
            best_method = ""

            poly_normalized = self._normalize_text(poly.question)
            poly_keywords = self._extract_keywords(poly.question)

            for kalshi in kalshi_markets:
                kalshi_normalized = self._normalize_text(kalshi.title)
                kalshi_keywords = self._extract_keywords(kalshi.title)

                # Method 1: Exact keyword match
                keyword_overlap = len(poly_keywords & kalshi_keywords)
                keyword_score = keyword_overlap / max(len(poly_keywords | kalshi_keywords), 1)

                # Method 2: Substring containment
                containment_score = 0.0
                if poly_normalized in kalshi_normalized or kalshi_normalized in poly_normalized:
                    containment_score = 0.8

                # Method 3: Word overlap ratio
                poly_words = set(poly_normalized.split())
                kalshi_words = set(kalshi_normalized.split())
                word_overlap = len(poly_words & kalshi_words)
                word_score = word_overlap / max(len(poly_words | kalshi_words), 1)

                # Combined score (weighted)
                combined_score = (
                    keyword_score * 0.5 +
                    containment_score * 0.3 +
                    word_score * 0.2
                )

                if combined_score > best_score:
                    best_score = combined_score
                    best_match = kalshi
                    if keyword_score > 0.5:
                        best_method = "keyword"
                    elif containment_score > 0:
                        best_method = "containment"
                    else:
                        best_method = "word_overlap"

            if best_match and best_score >= self.min_match_score:
                matches.append(MarketMatch(
                    polymarket=poly,
                    kalshi=best_match,
                    match_score=best_score,
                    match_method=best_method,
                ))

        return matches

    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison."""
        # Lowercase
        text = text.lower()
        # Remove punctuation except apostrophes
        text = re.sub(r"[^\w\s']", " ", text)
        # Normalize whitespace
        text = " ".join(text.split())
        return text

    def _extract_keywords(self, text: str) -> set[str]:
        """Extract important keywords from text."""
        # Common words to ignore
        stopwords = {
            "will", "the", "a", "an", "be", "to", "of", "in", "on", "at", "by",
            "for", "is", "are", "was", "were", "been", "being", "have", "has",
            "had", "do", "does", "did", "this", "that", "these", "those", "it",
            "its", "and", "or", "but", "if", "then", "than", "so", "as", "with",
            "from", "into", "during", "before", "after", "above", "below",
            "between", "under", "again", "further", "once", "here", "there",
            "when", "where", "why", "how", "all", "each", "few", "more", "most",
            "other", "some", "such", "no", "not", "only", "own", "same", "too",
            "very", "can", "just", "should", "now", "yes", "market", "what",
        }

        normalized = self._normalize_text(text)
        words = normalized.split()

        # Filter stopwords and short words
        keywords = {w for w in words if w not in stopwords and len(w) > 2}

        # Also extract potential named entities (capitalized sequences in original)
        entities = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)
        for entity in entities:
            keywords.add(entity.lower())

        return keywords

    def analyze(
        self,
        matches: list[MarketMatch],
        poly_prices: dict[str, PriceData],
        kalshi_prices: dict[str, KalshiPriceData],
    ) -> list[CrossPlatformArbitrage]:
        """Find cross-platform arbitrage opportunities.

        Args:
            matches: List of matched markets
            poly_prices: Polymarket prices by market ID
            kalshi_prices: Kalshi prices by ticker

        Returns:
            List of arbitrage opportunities sorted by spread
        """
        opportunities: list[CrossPlatformArbitrage] = []

        for match in matches:
            poly_price = poly_prices.get(match.polymarket.id)
            kalshi_price = kalshi_prices.get(match.kalshi.ticker)

            if not poly_price or not kalshi_price:
                continue

            if poly_price.yes_price is None or kalshi_price.yes_price is None:
                continue

            opp = self._check_arbitrage(match, poly_price, kalshi_price)
            if opp:
                opportunities.append(opp)

        # Sort by tier then spread
        tier_order = {
            CrossPlatformTier.ALPHA: 0,
            CrossPlatformTier.HIGH_VALUE: 1,
            CrossPlatformTier.STANDARD: 2,
        }
        opportunities.sort(
            key=lambda x: (tier_order.get(x.tier, 3), -x.spread_pct)
        )

        return opportunities

    def _check_arbitrage(
        self,
        match: MarketMatch,
        poly_price: PriceData,
        kalshi_price: KalshiPriceData,
    ) -> CrossPlatformArbitrage | None:
        """Check for arbitrage between matched markets.

        Cross-platform arbitrage exists when:
        - Polymarket YES price < Kalshi NO price (buy Poly YES, sell Kalshi NO)
        - Polymarket NO price < Kalshi YES price (buy Poly NO, sell Kalshi YES)
        - Or vice versa

        Since YES + NO should = $1 on each platform, we're looking for
        price disagreements between platforms.
        """
        poly_yes = poly_price.yes_price
        poly_no = poly_price.no_price or (1.0 - poly_yes if poly_yes else None)

        kalshi_yes = kalshi_price.yes_price
        kalshi_no = kalshi_price.no_price

        if None in (poly_yes, poly_no, kalshi_yes, kalshi_no):
            return None

        # Check all arbitrage combinations
        best_spread = 0.0
        best_type = ""
        buy_platform = ""
        buy_side = ""
        buy_price = 0.0
        sell_platform = ""
        sell_side = ""
        sell_price = 0.0

        # Strategy 1: Buy Polymarket YES, sell Kalshi YES
        # Profit if Kalshi YES > Polymarket YES
        spread1 = (kalshi_yes - poly_yes) * 100
        if spread1 > best_spread:
            best_spread = spread1
            best_type = "buy_poly_yes_sell_kalshi_yes"
            buy_platform, buy_side, buy_price = "polymarket", "yes", poly_yes
            sell_platform, sell_side, sell_price = "kalshi", "yes", kalshi_yes

        # Strategy 2: Buy Kalshi YES, sell Polymarket YES
        spread2 = (poly_yes - kalshi_yes) * 100
        if spread2 > best_spread:
            best_spread = spread2
            best_type = "buy_kalshi_yes_sell_poly_yes"
            buy_platform, buy_side, buy_price = "kalshi", "yes", kalshi_yes
            sell_platform, sell_side, sell_price = "polymarket", "yes", poly_yes

        # Strategy 3: Buy Polymarket NO, sell Kalshi NO
        spread3 = (kalshi_no - poly_no) * 100
        if spread3 > best_spread:
            best_spread = spread3
            best_type = "buy_poly_no_sell_kalshi_no"
            buy_platform, buy_side, buy_price = "polymarket", "no", poly_no
            sell_platform, sell_side, sell_price = "kalshi", "no", kalshi_no

        # Strategy 4: Buy Kalshi NO, sell Polymarket NO
        spread4 = (poly_no - kalshi_no) * 100
        if spread4 > best_spread:
            best_spread = spread4
            best_type = "buy_kalshi_no_sell_poly_no"
            buy_platform, buy_side, buy_price = "kalshi", "no", kalshi_no
            sell_platform, sell_side, sell_price = "polymarket", "no", poly_no

        # Check minimum spread threshold
        if best_spread < self.min_spread_pct:
            return None

        # Determine tier
        tier = self._get_tier(best_spread)

        return CrossPlatformArbitrage(
            polymarket_id=match.polymarket.id,
            polymarket_question=match.polymarket.question,
            polymarket_url=match.polymarket.polymarket_url,
            kalshi_ticker=match.kalshi.ticker,
            kalshi_title=match.kalshi.title,
            kalshi_url=match.kalshi.kalshi_url,
            poly_yes_price=poly_yes,
            poly_no_price=poly_no,
            kalshi_yes_price=kalshi_yes,
            kalshi_no_price=kalshi_no,
            arbitrage_type=best_type,
            spread_pct=best_spread,
            tier=tier,
            buy_platform=buy_platform,
            buy_side=buy_side,
            buy_price=buy_price,
            sell_platform=sell_platform,
            sell_side=sell_side,
            sell_price=sell_price,
            match_score=match.match_score,
        )

    def _get_tier(self, spread_pct: float) -> CrossPlatformTier:
        """Determine tier based on spread percentage."""
        if spread_pct >= self.TIER_ALPHA:
            return CrossPlatformTier.ALPHA
        elif spread_pct >= self.TIER_HIGH_VALUE:
            return CrossPlatformTier.HIGH_VALUE
        elif spread_pct >= self.TIER_STANDARD:
            return CrossPlatformTier.STANDARD
        return CrossPlatformTier.NONE
