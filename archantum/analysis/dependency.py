"""Dependency-based arbitrage detection between related markets."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from archantum.api.clob import PriceData
from archantum.api.gamma import GammaMarket
from archantum.config import settings


class DependencyType(Enum):
    """Type of logical dependency between markets."""

    TIME_BASED = "time_based"        # "by March" implies "by June"
    SUBSET = "subset"                # "win by 5+" implies "win"
    MUTUALLY_EXCLUSIVE = "mutually_exclusive"  # Different entities, same contest


@dataclass
class DependencyArbitrage:
    """A dependency-based arbitrage opportunity."""

    market_a_id: str
    market_a_question: str
    market_a_yes_price: float
    market_a_url: str | None

    market_b_id: str
    market_b_question: str
    market_b_yes_price: float
    market_b_url: str | None

    dependency_type: DependencyType
    violation: str  # Human-readable explanation of the violation
    estimated_profit_pct: float

    def to_dict(self) -> dict:
        return {
            "market_a_id": self.market_a_id,
            "market_a_question": self.market_a_question,
            "market_a_yes_price": self.market_a_yes_price,
            "market_a_url": self.market_a_url,
            "market_b_id": self.market_b_id,
            "market_b_question": self.market_b_question,
            "market_b_yes_price": self.market_b_yes_price,
            "market_b_url": self.market_b_url,
            "dependency_type": self.dependency_type.value,
            "violation": self.violation,
            "estimated_profit_pct": self.estimated_profit_pct,
        }


# Month order for time-based comparison
MONTH_ORDER = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "oct": 10, "nov": 11, "dec": 12,
}

# Patterns for detecting time references
MONTH_PATTERN = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(r"\b(\d{1,2})[/-](\d{1,2})\b")  # e.g., 3/15, 6-30

# Pattern for numeric thresholds
NUMBER_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*\+|(?:over|above|more than|at least|exceed)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


class DependencyAnalyzer:
    """Detects logical dependencies between related markets and finds violations."""

    # Minimum profit threshold to report
    MIN_PROFIT_PCT = 1.0

    def analyze(
        self,
        markets: list[GammaMarket],
        prices: dict[str, PriceData],
    ) -> list[DependencyArbitrage]:
        """Find dependency-based arbitrage opportunities.

        Groups markets by event, then checks all pairs within each event
        for logical dependencies and price violations.
        """
        max_days = settings.dependency_max_days_to_resolution
        now = datetime.utcnow()
        max_resolution = now + timedelta(days=max_days)

        # Group by event slug
        event_groups: dict[str, list[GammaMarket]] = {}
        for market in markets:
            slug = self._get_event_slug(market)
            if slug:
                event_groups.setdefault(slug, []).append(market)

        opportunities = []

        for event_slug, event_markets in event_groups.items():
            if len(event_markets) < 2:
                continue

            # Skip events resolving too far out
            earliest = self._get_earliest_end_date(event_markets)
            if earliest is None or earliest > max_resolution:
                continue

            # Check all pairs within the event
            for i in range(len(event_markets)):
                for j in range(i + 1, len(event_markets)):
                    m_a = event_markets[i]
                    m_b = event_markets[j]

                    price_a = prices.get(m_a.id)
                    price_b = prices.get(m_b.id)
                    if not price_a or not price_b:
                        continue
                    if price_a.yes_price is None or price_b.yes_price is None:
                        continue

                    # Check all dependency types
                    dep = self._check_time_dependency(m_a, m_b, price_a, price_b)
                    if dep:
                        opportunities.append(dep)
                        continue

                    dep = self._check_subset_dependency(m_a, m_b, price_a, price_b)
                    if dep:
                        opportunities.append(dep)
                        continue

                    dep = self._check_mutual_exclusion(m_a, m_b, price_a, price_b)
                    if dep:
                        opportunities.append(dep)

        # Sort by profit
        opportunities.sort(key=lambda x: -x.estimated_profit_pct)
        return opportunities

    def _check_time_dependency(
        self,
        m_a: GammaMarket,
        m_b: GammaMarket,
        price_a: PriceData,
        price_b: PriceData,
    ) -> DependencyArbitrage | None:
        """Check time-based dependency: 'by March' implies 'by June'.

        If event A happening by an earlier date implies event B happening by a later date,
        then P(A) must be <= P(B).
        """
        q_a = m_a.question.lower()
        q_b = m_b.question.lower()

        # Extract months
        months_a = MONTH_PATTERN.findall(q_a)
        months_b = MONTH_PATTERN.findall(q_b)

        if not months_a or not months_b:
            return None

        # Check if questions share a common structure (same event, different dates)
        # Strip month references and compare core question
        core_a = MONTH_PATTERN.sub("___", q_a).strip()
        core_b = MONTH_PATTERN.sub("___", q_b).strip()

        # Questions must be structurally similar
        if not self._are_similar(core_a, core_b):
            return None

        # Get month ordinals
        month_a = MONTH_ORDER.get(months_a[0].lower())
        month_b = MONTH_ORDER.get(months_b[0].lower())
        if month_a is None or month_b is None:
            return None

        # Earlier deadline implies later deadline
        # If A is "by March" and B is "by June", then A=True implies B=True
        # So P(A) <= P(B) must hold
        if month_a < month_b:
            # A is earlier, so P(A) should be <= P(B)
            if price_a.yes_price > price_b.yes_price + 0.01:
                profit = (price_a.yes_price - price_b.yes_price) * 100
                if profit >= self.MIN_PROFIT_PCT:
                    return DependencyArbitrage(
                        market_a_id=m_a.id,
                        market_a_question=m_a.question,
                        market_a_yes_price=price_a.yes_price,
                        market_a_url=m_a.polymarket_url,
                        market_b_id=m_b.id,
                        market_b_question=m_b.question,
                        market_b_yes_price=price_b.yes_price,
                        market_b_url=m_b.polymarket_url,
                        dependency_type=DependencyType.TIME_BASED,
                        violation=(
                            f"Earlier deadline ({months_a[0]}) priced higher than "
                            f"later deadline ({months_b[0]}): "
                            f"{price_a.yes_price:.0%} > {price_b.yes_price:.0%}"
                        ),
                        estimated_profit_pct=profit,
                    )
        elif month_b < month_a:
            # B is earlier, so P(B) should be <= P(A)
            if price_b.yes_price > price_a.yes_price + 0.01:
                profit = (price_b.yes_price - price_a.yes_price) * 100
                if profit >= self.MIN_PROFIT_PCT:
                    return DependencyArbitrage(
                        market_a_id=m_b.id,
                        market_a_question=m_b.question,
                        market_a_yes_price=price_b.yes_price,
                        market_a_url=m_b.polymarket_url,
                        market_b_id=m_a.id,
                        market_b_question=m_a.question,
                        market_b_yes_price=price_a.yes_price,
                        market_b_url=m_a.polymarket_url,
                        dependency_type=DependencyType.TIME_BASED,
                        violation=(
                            f"Earlier deadline ({months_b[0]}) priced higher than "
                            f"later deadline ({months_a[0]}): "
                            f"{price_b.yes_price:.0%} > {price_a.yes_price:.0%}"
                        ),
                        estimated_profit_pct=profit,
                    )

        return None

    def _check_subset_dependency(
        self,
        m_a: GammaMarket,
        m_b: GammaMarket,
        price_a: PriceData,
        price_b: PriceData,
    ) -> DependencyArbitrage | None:
        """Check subset dependency: 'win by 5+' implies 'win'.

        If A is a strict subset of B (more specific condition), then P(A) <= P(B).
        """
        q_a = m_a.question.lower()
        q_b = m_b.question.lower()

        # Extract numeric thresholds
        nums_a = NUMBER_PATTERN.findall(q_a)
        nums_b = NUMBER_PATTERN.findall(q_b)

        # Flatten tuples (regex groups)
        threshold_a = self._extract_threshold(nums_a)
        threshold_b = self._extract_threshold(nums_b)

        if threshold_a is not None and threshold_b is None:
            # A has a threshold, B doesn't — A might be subset of B
            # Strip numeric conditions and compare
            core_a = NUMBER_PATTERN.sub("___", q_a).strip()
            core_b = q_b.strip()
            if self._are_similar(core_a, core_b):
                # A is more specific (e.g., "win by 5+"), B is general ("win")
                # P(A) should be <= P(B)
                if price_a.yes_price > price_b.yes_price + 0.01:
                    profit = (price_a.yes_price - price_b.yes_price) * 100
                    if profit >= self.MIN_PROFIT_PCT:
                        return DependencyArbitrage(
                            market_a_id=m_a.id,
                            market_a_question=m_a.question,
                            market_a_yes_price=price_a.yes_price,
                            market_a_url=m_a.polymarket_url,
                            market_b_id=m_b.id,
                            market_b_question=m_b.question,
                            market_b_yes_price=price_b.yes_price,
                            market_b_url=m_b.polymarket_url,
                            dependency_type=DependencyType.SUBSET,
                            violation=(
                                f"Stricter condition priced higher: "
                                f"{price_a.yes_price:.0%} > {price_b.yes_price:.0%}"
                            ),
                            estimated_profit_pct=profit,
                        )

        elif threshold_b is not None and threshold_a is None:
            # B has a threshold, A doesn't — B might be subset of A
            core_b = NUMBER_PATTERN.sub("___", q_b).strip()
            core_a = q_a.strip()
            if self._are_similar(core_a, core_b):
                if price_b.yes_price > price_a.yes_price + 0.01:
                    profit = (price_b.yes_price - price_a.yes_price) * 100
                    if profit >= self.MIN_PROFIT_PCT:
                        return DependencyArbitrage(
                            market_a_id=m_b.id,
                            market_a_question=m_b.question,
                            market_a_yes_price=price_b.yes_price,
                            market_a_url=m_b.polymarket_url,
                            market_b_id=m_a.id,
                            market_b_question=m_a.question,
                            market_b_yes_price=price_a.yes_price,
                            market_b_url=m_a.polymarket_url,
                            dependency_type=DependencyType.SUBSET,
                            violation=(
                                f"Stricter condition priced higher: "
                                f"{price_b.yes_price:.0%} > {price_a.yes_price:.0%}"
                            ),
                            estimated_profit_pct=profit,
                        )

        elif threshold_a is not None and threshold_b is not None:
            # Both have thresholds — higher threshold is subset
            core_a = NUMBER_PATTERN.sub("___", q_a).strip()
            core_b = NUMBER_PATTERN.sub("___", q_b).strip()
            if self._are_similar(core_a, core_b):
                if threshold_a > threshold_b:
                    # A is stricter, P(A) <= P(B)
                    if price_a.yes_price > price_b.yes_price + 0.01:
                        profit = (price_a.yes_price - price_b.yes_price) * 100
                        if profit >= self.MIN_PROFIT_PCT:
                            return DependencyArbitrage(
                                market_a_id=m_a.id,
                                market_a_question=m_a.question,
                                market_a_yes_price=price_a.yes_price,
                                market_a_url=m_a.polymarket_url,
                                market_b_id=m_b.id,
                                market_b_question=m_b.question,
                                market_b_yes_price=price_b.yes_price,
                                market_b_url=m_b.polymarket_url,
                                dependency_type=DependencyType.SUBSET,
                                violation=(
                                    f"Higher threshold ({threshold_a}) priced above "
                                    f"lower threshold ({threshold_b}): "
                                    f"{price_a.yes_price:.0%} > {price_b.yes_price:.0%}"
                                ),
                                estimated_profit_pct=profit,
                            )
                elif threshold_b > threshold_a:
                    if price_b.yes_price > price_a.yes_price + 0.01:
                        profit = (price_b.yes_price - price_a.yes_price) * 100
                        if profit >= self.MIN_PROFIT_PCT:
                            return DependencyArbitrage(
                                market_a_id=m_b.id,
                                market_a_question=m_b.question,
                                market_a_yes_price=price_b.yes_price,
                                market_a_url=m_b.polymarket_url,
                                market_b_id=m_a.id,
                                market_b_question=m_a.question,
                                market_b_yes_price=price_a.yes_price,
                                market_b_url=m_a.polymarket_url,
                                dependency_type=DependencyType.SUBSET,
                                violation=(
                                    f"Higher threshold ({threshold_b}) priced above "
                                    f"lower threshold ({threshold_a}): "
                                    f"{price_b.yes_price:.0%} > {price_a.yes_price:.0%}"
                                ),
                                estimated_profit_pct=profit,
                            )

        return None

    def _check_mutual_exclusion(
        self,
        m_a: GammaMarket,
        m_b: GammaMarket,
        price_a: PriceData,
        price_b: PriceData,
    ) -> DependencyArbitrage | None:
        """Check mutual exclusion: different entities in same contest.

        If A and B are mutually exclusive, P(A) + P(B) <= 1.0.
        """
        q_a = m_a.question.lower()
        q_b = m_b.question.lower()

        # Simple heuristic: if questions differ only in a named entity
        # (e.g., "Will X win?" vs "Will Y win?"), they may be mutually exclusive
        words_a = set(q_a.split())
        words_b = set(q_b.split())

        common = words_a & words_b
        diff_a = words_a - words_b
        diff_b = words_b - words_a

        # If 70%+ words are shared and only 1-3 words differ, likely same contest
        if len(common) < 3:
            return None
        overlap_ratio = len(common) / max(len(words_a), len(words_b))
        if overlap_ratio < 0.6:
            return None
        if len(diff_a) > 3 or len(diff_b) > 3:
            return None

        # Check if P(A) + P(B) > 1.0 (violation of mutual exclusion)
        combined = price_a.yes_price + price_b.yes_price
        if combined > 1.01:
            profit = (combined - 1.0) * 100
            if profit >= self.MIN_PROFIT_PCT:
                return DependencyArbitrage(
                    market_a_id=m_a.id,
                    market_a_question=m_a.question,
                    market_a_yes_price=price_a.yes_price,
                    market_a_url=m_a.polymarket_url,
                    market_b_id=m_b.id,
                    market_b_question=m_b.question,
                    market_b_yes_price=price_b.yes_price,
                    market_b_url=m_b.polymarket_url,
                    dependency_type=DependencyType.MUTUALLY_EXCLUSIVE,
                    violation=(
                        f"Mutually exclusive markets sum to {combined:.0%} "
                        f"(should be ≤ 100%): "
                        f"{price_a.yes_price:.0%} + {price_b.yes_price:.0%}"
                    ),
                    estimated_profit_pct=profit,
                )

        return None

    def _get_earliest_end_date(self, event_markets: list[GammaMarket]) -> datetime | None:
        """Get the earliest end date across all markets in the event."""
        earliest = None
        for m in event_markets:
            if not m.end_date:
                continue
            try:
                end_dt = datetime.fromisoformat(m.end_date.replace("Z", "+00:00")).replace(tzinfo=None)
                if earliest is None or end_dt < earliest:
                    earliest = end_dt
            except (ValueError, AttributeError):
                continue
        return earliest

    def _get_event_slug(self, market: GammaMarket) -> str | None:
        """Get event slug from market."""
        if market.events and len(market.events) > 0:
            return market.events[0].get("slug")
        return market.event_slug

    def _are_similar(self, text_a: str, text_b: str) -> bool:
        """Check if two texts are structurally similar (60%+ word overlap)."""
        words_a = set(text_a.split())
        words_b = set(text_b.split())
        if not words_a or not words_b:
            return False
        overlap = len(words_a & words_b)
        return overlap / max(len(words_a), len(words_b)) >= 0.6

    def _extract_threshold(self, matches: list[tuple]) -> float | None:
        """Extract a numeric threshold from regex matches."""
        for groups in matches:
            for g in groups:
                if g:
                    try:
                        return float(g)
                    except ValueError:
                        continue
        return None
