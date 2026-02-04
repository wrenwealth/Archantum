"""Volume spike detection."""

from __future__ import annotations

from dataclasses import dataclass
from archantum.config import settings
from archantum.db import Database
from archantum.api.gamma import GammaMarket


@dataclass
class VolumeSpike:
    """Represents a volume spike event."""

    market_id: str
    question: str
    slug: str | None
    current_volume: float
    average_volume: float
    spike_multiplier: float

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "market_id": self.market_id,
            "question": self.question,
            "slug": self.slug,
            "current_volume": self.current_volume,
            "average_volume": self.average_volume,
            "spike_multiplier": self.spike_multiplier,
        }


class VolumeAnalyzer:
    """Analyzes markets for volume spikes."""

    def __init__(
        self,
        db: Database,
        multiplier_threshold: float | None = None,
    ):
        self.db = db
        self.multiplier_threshold = multiplier_threshold or settings.volume_spike_multiplier

    async def analyze(self, markets: list[GammaMarket]) -> list[VolumeSpike]:
        """Find volume spikes across markets."""
        spikes = []

        for market in markets:
            spike = await self.check_market(market)
            if spike:
                spikes.append(spike)

        # Sort by spike multiplier (highest first)
        spikes.sort(key=lambda x: x.spike_multiplier, reverse=True)
        return spikes

    async def check_market(self, market: GammaMarket) -> VolumeSpike | None:
        """Check a single market for volume spike."""
        if market.volume_24hr is None:
            return None

        current_volume = market.volume_24hr

        # Get rolling average from database
        avg_volume = await self.db.get_volume_rolling_average(
            market.id,
            days=settings.volume_rolling_days,
        )

        if avg_volume is None or avg_volume == 0:
            # First time seeing this market, save volume snapshot
            await self.db.save_volume_snapshot(
                market_id=market.id,
                volume_24h=market.volume_24hr,
                volume_total=market.volume,
                liquidity=market.liquidity,
            )
            return None

        multiplier = current_volume / avg_volume

        # Save current volume snapshot
        await self.db.save_volume_snapshot(
            market_id=market.id,
            volume_24h=market.volume_24hr,
            volume_total=market.volume,
            liquidity=market.liquidity,
        )

        if multiplier < self.multiplier_threshold:
            return None

        return VolumeSpike(
            market_id=market.id,
            question=market.question,
            slug=market.slug,
            current_volume=current_volume,
            average_volume=avg_volume,
            spike_multiplier=multiplier,
        )
