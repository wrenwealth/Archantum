"""Certain outcome detection — AI-verified markets ending soon with near-certain outcomes."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from sqlalchemy import select, func

from archantum.api.clob import PriceData
from archantum.api.gamma import GammaMarket
from archantum.config import settings


class CertainOutcomeTier(str, Enum):
    """Tier classification for certain outcome opportunities."""

    VERIFIED = "VERIFIED"  # >=0.90 combined score
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"  # >=0.80 combined score


@dataclass
class AIVerificationResult:
    """Result from Claude AI verification."""

    determined: bool  # Is the outcome factually determined?
    outcome: str  # "YES" or "NO"
    confidence: float  # 0.0-1.0
    reasoning: str
    error: str | None = None  # Non-None if API call failed

    def to_dict(self) -> dict:
        return {
            "determined": self.determined,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "error": self.error,
        }


@dataclass
class MultiSignalScore:
    """Score from multiple market data signals."""

    stability_score: float  # 0-1: how long price has been at extreme
    volume_score: float  # 0-1: log-scale volume scoring
    cross_platform_score: float  # 0-1: agreement with Kalshi
    combined: float  # Weighted combination

    def to_dict(self) -> dict:
        return {
            "stability_score": self.stability_score,
            "volume_score": self.volume_score,
            "cross_platform_score": self.cross_platform_score,
            "combined": self.combined,
        }


@dataclass
class CertainOutcomeOpportunity:
    """A market with a near-certain outcome detected and AI-verified."""

    market_id: str
    question: str
    polymarket_url: str | None
    current_price: float  # Price of the dominant side
    buy_side: str  # "YES" or "NO" — which side to buy
    buy_price: float  # Price to pay
    profit_per_share_cents: float  # (1.0 - buy_price) * 100
    hours_until_resolution: float
    end_date: datetime | None
    volume_24hr: float | None
    tier: CertainOutcomeTier
    combined_score: float  # 0-1, the final score
    ai_result: AIVerificationResult
    signal_score: MultiSignalScore

    def calculate_profit(self, investment: float) -> float:
        """Calculate profit for a given investment amount."""
        shares = investment / self.buy_price
        return shares * (1.0 - self.buy_price)

    def to_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "question": self.question,
            "polymarket_url": self.polymarket_url,
            "current_price": self.current_price,
            "buy_side": self.buy_side,
            "buy_price": self.buy_price,
            "profit_per_share_cents": self.profit_per_share_cents,
            "hours_until_resolution": self.hours_until_resolution,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "volume_24hr": self.volume_24hr,
            "tier": self.tier.value,
            "combined_score": self.combined_score,
            "ai_result": self.ai_result.to_dict(),
            "signal_score": self.signal_score.to_dict(),
        }


class AIVerifier:
    """Handles Claude API calls to verify if a market outcome is factually determined."""

    def __init__(self):
        self._client = None
        self._cache: dict[str, tuple[AIVerificationResult, datetime]] = {}

    def _get_client(self):
        """Lazy-initialize the Anthropic client."""
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        return self._client

    def _is_cached(self, market_id: str) -> bool:
        """Check if a cached result exists and is still valid."""
        if market_id not in self._cache:
            return False
        _, cached_at = self._cache[market_id]
        ttl = timedelta(hours=settings.certain_outcome_cache_hours)
        return datetime.utcnow() - cached_at < ttl

    def _get_cached(self, market_id: str) -> AIVerificationResult | None:
        """Get a cached result if valid."""
        if self._is_cached(market_id):
            return self._cache[market_id][0]
        return None

    async def verify(
        self,
        market_id: str,
        question: str,
        current_yes_price: float,
        hours_until_resolution: float,
    ) -> AIVerificationResult:
        """Ask Claude whether a market outcome is factually determined."""
        # Check cache first
        cached = self._get_cached(market_id)
        if cached is not None:
            return cached

        try:
            client = self._get_client()

            prompt = f"""You are analyzing a prediction market to determine if the outcome is already factually decided.

Market question: "{question}"
Current YES price: ${current_yes_price:.2f} (meaning the market thinks there's a {current_yes_price*100:.0f}% chance of YES)
Time until market resolves: {hours_until_resolution:.1f} hours

Based on your knowledge, is the outcome of this market already factually determined? Consider:
1. Has the event already occurred or been officially announced?
2. Is there overwhelming evidence that makes the outcome virtually certain?
3. Could anything realistically change the outcome before resolution?

Respond in EXACTLY this format (4 lines):
DETERMINED: YES or NO
OUTCOME: YES or NO (which outcome is determined/likely)
CONFIDENCE: a number between 0.0 and 1.0
REASONING: one sentence explaining your assessment"""

            response = await client.messages.create(
                model=settings.certain_outcome_ai_model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )

            # Parse response
            text = response.content[0].text.strip()
            result = self._parse_response(text)

            # Cache the result
            self._cache[market_id] = (result, datetime.utcnow())
            return result

        except Exception as e:
            # Graceful fallback — return error result
            return AIVerificationResult(
                determined=False,
                outcome="UNKNOWN",
                confidence=0.0,
                reasoning="AI verification unavailable",
                error=str(e),
            )

    def _parse_response(self, text: str) -> AIVerificationResult:
        """Parse the structured response from Claude."""
        lines = text.strip().split("\n")

        determined = False
        outcome = "UNKNOWN"
        confidence = 0.0
        reasoning = ""

        for line in lines:
            line = line.strip()
            if line.upper().startswith("DETERMINED:"):
                val = line.split(":", 1)[1].strip().upper()
                determined = val == "YES"
            elif line.upper().startswith("OUTCOME:"):
                outcome = line.split(":", 1)[1].strip().upper()
            elif line.upper().startswith("CONFIDENCE:"):
                try:
                    confidence = float(line.split(":", 1)[1].strip())
                    confidence = max(0.0, min(1.0, confidence))
                except ValueError:
                    confidence = 0.0
            elif line.upper().startswith("REASONING:"):
                reasoning = line.split(":", 1)[1].strip()

        return AIVerificationResult(
            determined=determined,
            outcome=outcome,
            confidence=confidence,
            reasoning=reasoning,
        )


class MultiSignalScorer:
    """Calculates confidence from market data signals."""

    def __init__(self, db):
        self.db = db

    async def score(
        self,
        market: GammaMarket,
        price_data: PriceData,
        kalshi_price: float | None = None,
    ) -> MultiSignalScore:
        """Calculate multi-signal score for a market."""
        stability = await self._score_stability(market.id, price_data.yes_price)
        volume = self._score_volume(market.volume_24hr)
        cross_platform = self._score_cross_platform(price_data.yes_price, kalshi_price)

        # Weighted combination: stability 50%, volume 30%, cross-platform 20%
        combined = (stability * 0.50) + (volume * 0.30) + (cross_platform * 0.20)

        return MultiSignalScore(
            stability_score=stability,
            volume_score=volume,
            cross_platform_score=cross_platform,
            combined=combined,
        )

    async def _score_stability(self, market_id: str, current_price: float | None) -> float:
        """Score based on how long price has been at extreme level.

        24h+ stable = 1.0, 12h = 0.8, 3h = 0.5, <1h = 0.2
        """
        if current_price is None:
            return 0.0

        from archantum.db.models import PriceSnapshot

        threshold = settings.certain_outcome_price_threshold

        try:
            async with self.db.async_session() as session:
                # Find how far back price has been above threshold (or below 1-threshold)
                is_high = current_price >= threshold
                cutoff = datetime.utcnow() - timedelta(hours=48)

                result = await session.execute(
                    select(PriceSnapshot.yes_price, PriceSnapshot.timestamp)
                    .where(PriceSnapshot.market_id == market_id)
                    .where(PriceSnapshot.timestamp >= cutoff)
                    .order_by(PriceSnapshot.timestamp.desc())
                )
                rows = result.all()

                if not rows:
                    return 0.2  # No history, low confidence

                # Walk backwards to find when price first entered extreme zone
                stable_since = datetime.utcnow()
                for price, ts in rows:
                    if price is None:
                        continue
                    if is_high and price >= threshold:
                        stable_since = ts
                    elif not is_high and price <= (1.0 - threshold):
                        stable_since = ts
                    else:
                        break

                hours_stable = (datetime.utcnow() - stable_since).total_seconds() / 3600

                if hours_stable >= 24:
                    return 1.0
                elif hours_stable >= 12:
                    return 0.8
                elif hours_stable >= 3:
                    return 0.5
                elif hours_stable >= 1:
                    return 0.3
                else:
                    return 0.2

        except Exception:
            return 0.2

    def _score_volume(self, volume_24hr: float | None) -> float:
        """Score based on 24h volume using log scale.

        >$500K = ~1.0, $50K = ~0.7, <$5K = ~0.2
        """
        if volume_24hr is None or volume_24hr <= 0:
            return 0.1

        # Log-scale scoring: log10(volume) mapped to 0-1 range
        # $5K = 3.7, $50K = 4.7, $500K = 5.7
        log_vol = math.log10(max(volume_24hr, 1))

        if log_vol >= 5.7:  # >= $500K
            return 1.0
        elif log_vol >= 4.7:  # >= $50K
            return 0.6 + (log_vol - 4.7) * 0.4  # 0.7 - 1.0
        elif log_vol >= 3.7:  # >= $5K
            return 0.2 + (log_vol - 3.7) * 0.5  # 0.2 - 0.7
        else:
            return 0.1

    def _score_cross_platform(
        self,
        poly_yes_price: float | None,
        kalshi_yes_price: float | None,
    ) -> float:
        """Score based on cross-platform price agreement.

        Within 3¢ = 1.0, 5¢ = 0.8, >15¢ = 0.1. Default 0.5 if no Kalshi data.
        """
        if poly_yes_price is None or kalshi_yes_price is None:
            return 0.5  # No cross-platform data, neutral

        diff = abs(poly_yes_price - kalshi_yes_price)

        if diff <= 0.03:
            return 1.0
        elif diff <= 0.05:
            return 0.8
        elif diff <= 0.10:
            return 0.5
        elif diff <= 0.15:
            return 0.3
        else:
            return 0.1


class CertainOutcomeDetector:
    """Main orchestrator for certain outcome detection."""

    def __init__(self, db):
        self.db = db
        self.ai_verifier = AIVerifier()
        self.signal_scorer = MultiSignalScorer(db)
        self._alerted_markets: set[str] = set()

    async def analyze(
        self,
        markets: list[GammaMarket],
        prices: dict[str, PriceData],
        kalshi_matches: dict[str, float] | None = None,
    ) -> list[CertainOutcomeOpportunity]:
        """Detect markets with certain outcomes, verified by AI.

        Args:
            markets: List of tracked markets
            prices: Current price data keyed by market_id
            kalshi_matches: Optional dict of market_id -> Kalshi YES price

        Returns:
            List of verified certain outcome opportunities
        """
        if kalshi_matches is None:
            kalshi_matches = {}

        # Phase 1: Filter candidates (no API calls)
        candidates = self._filter_candidates(markets, prices)

        if not candidates:
            return []

        # Phase 2: Score each candidate (AI verify + multi-signal)
        opportunities = []
        ai_calls_made = 0
        max_ai_calls = settings.certain_outcome_max_ai_calls_per_poll

        for market, price_data, hours_left, end_dt in candidates:
            if market.id in self._alerted_markets:
                continue

            yes_price = price_data.yes_price
            is_yes_certain = yes_price >= settings.certain_outcome_price_threshold
            is_no_certain = yes_price <= (1.0 - settings.certain_outcome_price_threshold)

            # Determine dominant side
            if is_yes_certain:
                buy_side = "YES"
                buy_price = yes_price
                dominant_price = yes_price
            else:
                buy_side = "NO"
                buy_price = price_data.no_price or (1.0 - yes_price)
                dominant_price = buy_price

            # AI verification (rate limited)
            if ai_calls_made < max_ai_calls:
                ai_result = await self.ai_verifier.verify(
                    market_id=market.id,
                    question=market.question,
                    current_yes_price=yes_price,
                    hours_until_resolution=hours_left,
                )
                if ai_result.error is None:
                    ai_calls_made += 1
            else:
                # Skip AI, use signal-only fallback
                ai_result = AIVerificationResult(
                    determined=False,
                    outcome="UNKNOWN",
                    confidence=0.0,
                    reasoning="Rate limited — signal-only scoring",
                    error="rate_limited",
                )

            # Multi-signal scoring
            kalshi_price = kalshi_matches.get(market.id)
            signal_score = await self.signal_scorer.score(market, price_data, kalshi_price)

            # Calculate AI score
            if ai_result.error is not None:
                # Fallback: use signal-only
                ai_score = signal_score.combined
                combined = signal_score.combined
            else:
                if ai_result.determined:
                    ai_score = ai_result.confidence
                else:
                    ai_score = ai_result.confidence * 0.3

                combined = (
                    settings.certain_outcome_ai_weight * ai_score
                    + settings.certain_outcome_signal_weight * signal_score.combined
                )

            # Check minimum score threshold
            if combined < settings.certain_outcome_min_score:
                continue

            # Tier assignment
            if combined >= 0.90:
                tier = CertainOutcomeTier.VERIFIED
            else:
                tier = CertainOutcomeTier.HIGH_CONFIDENCE

            profit_per_share = (1.0 - buy_price) * 100

            # Build polymarket URL
            polymarket_url = None
            if market.events and len(market.events) > 0:
                slug = market.events[0].get("slug")
                if slug:
                    polymarket_url = f"https://polymarket.com/event/{slug}"

            opp = CertainOutcomeOpportunity(
                market_id=market.id,
                question=market.question,
                polymarket_url=polymarket_url,
                current_price=dominant_price,
                buy_side=buy_side,
                buy_price=buy_price,
                profit_per_share_cents=profit_per_share,
                hours_until_resolution=hours_left,
                end_date=end_dt,
                volume_24hr=market.volume_24hr,
                tier=tier,
                combined_score=combined,
                ai_result=ai_result,
                signal_score=signal_score,
            )
            opportunities.append(opp)

        # Sort by combined score descending
        opportunities.sort(key=lambda x: -x.combined_score)

        # Mark alerted to avoid duplicates
        for opp in opportunities:
            self._alerted_markets.add(opp.market_id)

        return opportunities

    def _filter_candidates(
        self,
        markets: list[GammaMarket],
        prices: dict[str, PriceData],
    ) -> list[tuple[GammaMarket, PriceData, float, datetime | None]]:
        """Phase 1: Filter markets ending within window with extreme prices.

        Returns list of (market, price_data, hours_until_resolution, end_datetime).
        Sorted by soonest resolution first (prioritize for AI calls).
        """
        candidates = []
        now = datetime.utcnow()
        window_hours = settings.certain_outcome_hours_window
        threshold = settings.certain_outcome_price_threshold

        for market in markets:
            # Must have price data
            price_data = prices.get(market.id)
            if not price_data or price_data.yes_price is None:
                continue

            # Must have end_date
            if not market.end_date:
                continue

            # Parse end date
            try:
                end_dt = datetime.fromisoformat(market.end_date.replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, AttributeError):
                continue

            hours_left = (end_dt - now).total_seconds() / 3600
            if hours_left <= 0 or hours_left > window_hours:
                continue

            # Price must be extreme
            yes_price = price_data.yes_price
            is_extreme = yes_price >= threshold or yes_price <= (1.0 - threshold)
            if not is_extreme:
                continue

            candidates.append((market, price_data, hours_left, end_dt))

        # Sort by soonest resolution first
        candidates.sort(key=lambda x: x[2])
        return candidates
