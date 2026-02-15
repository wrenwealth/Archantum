"""Esports arbitrage scanner for Valorant and Counter-Strike markets."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from archantum.api.clob import PriceData
from archantum.api.gamma import GammaMarket
from archantum.config import settings


# --- Keyword lists for market discovery ---

VALORANT_KEYWORDS = [
    "valorant", "vct", "champions tour",
    "sentinels", "cloud9", "100 thieves", "100t",
    "loud", "fnatic", "drx", "nrg", "evil geniuses",
    "paper rex", "prx", "edward gaming", "edg",
    "gen.g", "t1", "team liquid", "bilibili gaming",
    "fut esports", "leviatán", "leviatan", "karmine corp",
    "team heretics", "giants gaming", "trace esports",
    "global esports", "talon esports", "rex regum qeon",
]

CS_KEYWORDS = [
    "counter-strike", "counter strike", "cs2", "csgo", "cs:go", "cs 2",
    "iem", "intel extreme masters", "esl pro league",
    "blast premier", "blast pro", "pgl major", "dreamhack",
    "navi", "natus vincere", "g2 esports", "g2",
    "faze clan", "faze", "vitality", "team vitality",
    "heroic", "astralis", "mouz", "mousesports",
    "spirit", "team spirit", "virtus.pro", "virtuspro",
    "cloud9", "complexity", "eternal fire", "ence",
    "monte", "9z", "imperial", "furia", "liquid",
    "big clan", "falcons", "3dmax", "saw",
]

GENERIC_ESPORTS_KEYWORDS = [
    "esports", "esport", "e-sports", "e-sport",
]

MAP_KEYWORDS = [
    "map 1", "map 2", "map 3", "map 4", "map 5",
    "map1", "map2", "map3", "map4", "map5",
    "first map", "second map", "third map",
    # Valorant maps
    "ascent", "bind", "haven", "split", "icebox",
    "breeze", "fracture", "pearl", "lotus", "sunset", "abyss",
    # CS2 maps
    "mirage", "inferno", "nuke", "overpass", "ancient",
    "anubis", "vertigo", "dust2", "dust 2",
]

# Exclusion keywords - markets containing these are NOT esports
EXCLUSION_KEYWORDS = [
    # Traditional sports
    "nba", "nfl", "nhl", "mlb", "mls",
    "basketball", "football", "hockey", "baseball", "soccer",
    "premier league", "la liga", "bundesliga", "serie a", "ligue 1",
    "champions league", "world cup", "fifa",
    "western conference", "eastern conference",
    "playoffs", "super bowl", "stanley cup", "world series",
    # Politics/other
    "trump", "biden", "election", "president", "congress",
    "bitcoin", "btc", "ethereum", "crypto",
]


class EsportsGame(Enum):
    """Esports game classification."""

    VALORANT = "valorant"
    COUNTER_STRIKE = "counter_strike"
    UNKNOWN = "unknown"


class EsportsDetectionType(Enum):
    """Type of esports arbitrage detected."""

    MATCH_WINNER = "match_winner"
    TOURNAMENT_WINNER = "tournament_winner"
    MAP_VS_MATCH = "map_vs_match"


class EsportsTier(Enum):
    """Tier of esports opportunity."""

    STANDARD = "standard"
    HIGH_VALUE = "high_value"
    ALPHA = "alpha"


@dataclass
class EsportsMarketInfo:
    """Enriched info about an esports market."""

    market: GammaMarket
    game: EsportsGame
    is_map_market: bool = False
    matched_keywords: list[str] = field(default_factory=list)

    @property
    def market_id(self) -> str:
        return self.market.id

    @property
    def question(self) -> str:
        return self.market.question

    @property
    def event_slug(self) -> str | None:
        if self.market.events and len(self.market.events) > 0:
            return self.market.events[0].get("slug")
        return self.market.event_slug


@dataclass
class EsportsOpportunity:
    """An esports arbitrage opportunity."""

    detection_type: EsportsDetectionType
    tier: EsportsTier
    game: EsportsGame
    edge_pct: float
    profit_per_dollar: float
    description: str

    # Primary market info
    market_id: str
    question: str
    polymarket_url: str | None = None

    # Pricing
    yes_price: float = 0.0
    no_price: float = 0.0

    # Optional context
    tournament: str | None = None
    teams: list[str] = field(default_factory=list)
    related_markets: list[str] = field(default_factory=list)

    # Multi-outcome (tournament winner)
    outcome_count: int = 0
    total_probability: float = 0.0

    def calculate_profit(self, position_size: float) -> float:
        """Calculate estimated profit for a given position size."""
        return position_size * self.profit_per_dollar

    def to_dict(self) -> dict:
        return {
            "detection_type": self.detection_type.value,
            "tier": self.tier.value,
            "game": self.game.value,
            "edge_pct": self.edge_pct,
            "profit_per_dollar": self.profit_per_dollar,
            "market_id": self.market_id,
            "question": self.question,
            "yes_price": self.yes_price,
            "no_price": self.no_price,
            "tournament": self.tournament,
            "teams": self.teams,
            "outcome_count": self.outcome_count,
            "total_probability": self.total_probability,
        }


def _classify_tier(edge_pct: float) -> EsportsTier:
    """Classify tier based on edge percentage."""
    if edge_pct >= 10.0:
        return EsportsTier.ALPHA
    elif edge_pct >= 5.0:
        return EsportsTier.HIGH_VALUE
    return EsportsTier.STANDARD


def _detect_game(text: str) -> EsportsGame:
    """Detect which game a market relates to."""
    lower = text.lower()
    for kw in VALORANT_KEYWORDS:
        if kw in lower:
            return EsportsGame.VALORANT
    for kw in CS_KEYWORDS:
        if kw in lower:
            return EsportsGame.COUNTER_STRIKE
    return EsportsGame.UNKNOWN


def _is_map_market(text: str) -> bool:
    """Check if a market is about an individual map."""
    lower = text.lower()
    return any(kw in lower for kw in MAP_KEYWORDS)


def _extract_keywords(text: str) -> list[str]:
    """Return which esports keywords matched in the text."""
    lower = text.lower()
    matched = []
    for kw in VALORANT_KEYWORDS + CS_KEYWORDS + GENERIC_ESPORTS_KEYWORDS:
        if kw in lower:
            matched.append(kw)
    return matched


class EsportsArbitrageAnalyzer:
    """Analyzes esports markets for arbitrage opportunities."""

    def discover_esports_markets(
        self, markets: list[GammaMarket]
    ) -> list[EsportsMarketInfo]:
        """Filter the market list for esports-related markets using keywords."""
        all_keywords = (
            VALORANT_KEYWORDS + CS_KEYWORDS + GENERIC_ESPORTS_KEYWORDS
        )
        esports_markets = []

        for market in markets:
            text = (market.question or "").lower()
            # Also check event slug
            slug = ""
            if market.events and len(market.events) > 0:
                slug = (market.events[0].get("slug") or "").lower()

            combined = f"{text} {slug}"

            # Skip if contains exclusion keywords (NBA, NFL, etc.)
            if any(exc in combined for exc in EXCLUSION_KEYWORDS):
                continue

            if any(kw in combined for kw in all_keywords):
                info = EsportsMarketInfo(
                    market=market,
                    game=_detect_game(combined),
                    is_map_market=_is_map_market(combined),
                    matched_keywords=_extract_keywords(combined),
                )
                esports_markets.append(info)

        return esports_markets

    def analyze(
        self,
        markets: list[GammaMarket],
        prices: dict[str, PriceData],
    ) -> list[EsportsOpportunity]:
        """Run all esports arbitrage checks. Returns opportunities above min edge."""
        esports_markets = self.discover_esports_markets(markets)
        if not esports_markets:
            return []

        # Only keep Valorant and Counter-Strike markets (skip UNKNOWN)
        esports_markets = [
            m for m in esports_markets
            if m.game in (EsportsGame.VALORANT, EsportsGame.COUNTER_STRIKE)
        ]
        if not esports_markets:
            return []

        opportunities: list[EsportsOpportunity] = []
        min_edge = settings.esports_min_edge_pct

        # 1. Match winner mispricing (Yes + No < 99%)
        opportunities.extend(
            self._check_match_winner(esports_markets, prices, min_edge)
        )

        # 2. Tournament winner mispricing (multi-outcome sum != 100%)
        opportunities.extend(
            self._check_tournament_winner(esports_markets, prices, min_edge)
        )

        # 3. Map vs match inconsistency
        opportunities.extend(
            self._check_map_vs_match(esports_markets, prices, min_edge)
        )

        return opportunities

    def _check_match_winner(
        self,
        esports_markets: list[EsportsMarketInfo],
        prices: dict[str, PriceData],
        min_edge: float,
    ) -> list[EsportsOpportunity]:
        """Check for Yes+No < 99% on individual esports markets."""
        opps = []

        for info in esports_markets:
            price = prices.get(info.market_id)
            if not price or price.yes_price is None or price.no_price is None:
                continue

            total = price.yes_price + price.no_price
            if total >= 1.0:
                continue

            edge_pct = (1.0 - total) * 100
            if edge_pct < min_edge:
                continue

            profit_per_dollar = (1.0 - total) / total if total > 0 else 0

            url = None
            if info.event_slug:
                url = f"https://polymarket.com/event/{info.event_slug}"

            opp = EsportsOpportunity(
                detection_type=EsportsDetectionType.MATCH_WINNER,
                tier=_classify_tier(edge_pct),
                game=info.game,
                edge_pct=edge_pct,
                profit_per_dollar=profit_per_dollar,
                description=f"Yes+No = {total*100:.1f}¢ (gap: {edge_pct:.1f}%)",
                market_id=info.market_id,
                question=info.question,
                polymarket_url=url,
                yes_price=price.yes_price,
                no_price=price.no_price,
            )
            opps.append(opp)

        return opps

    def _check_tournament_winner(
        self,
        esports_markets: list[EsportsMarketInfo],
        prices: dict[str, PriceData],
        min_edge: float,
    ) -> list[EsportsOpportunity]:
        """Check multi-outcome events where sum of YES prices != 100%."""
        # Group markets by event slug
        events: dict[str, list[EsportsMarketInfo]] = {}
        for info in esports_markets:
            slug = info.event_slug
            if slug:
                events.setdefault(slug, []).append(info)

        opps = []
        for slug, event_markets in events.items():
            if len(event_markets) < 3:
                continue

            total_yes = 0.0
            valid = True
            for info in event_markets:
                price = prices.get(info.market_id)
                if not price or price.yes_price is None:
                    valid = False
                    break
                total_yes += price.yes_price

            if not valid:
                continue

            deviation = abs(total_yes - 1.0)
            edge_pct = deviation * 100
            if edge_pct < min_edge:
                continue

            strategy = "buy_all" if total_yes < 1.0 else "sell_all"
            profit_per_dollar = deviation / total_yes if total_yes > 0 else 0

            # Use the first market's game as the event game
            game = event_markets[0].game
            first_info = event_markets[0]
            url = None
            if first_info.event_slug:
                url = f"https://polymarket.com/event/{first_info.event_slug}"

            event_name = slug.replace("-", " ").title()

            opp = EsportsOpportunity(
                detection_type=EsportsDetectionType.TOURNAMENT_WINNER,
                tier=_classify_tier(edge_pct),
                game=game,
                edge_pct=edge_pct,
                profit_per_dollar=profit_per_dollar,
                description=f"Sum of {len(event_markets)} outcomes = {total_yes*100:.1f}% ({strategy})",
                market_id=first_info.market_id,
                question=event_name,
                polymarket_url=url,
                outcome_count=len(event_markets),
                total_probability=total_yes,
                related_markets=[m.market_id for m in event_markets],
            )
            opps.append(opp)

        return opps

    def _check_map_vs_match(
        self,
        esports_markets: list[EsportsMarketInfo],
        prices: dict[str, PriceData],
        min_edge: float,
    ) -> list[EsportsOpportunity]:
        """Check if map winner is priced higher than match winner in a best-of series."""
        # Group by event slug
        events: dict[str, list[EsportsMarketInfo]] = {}
        for info in esports_markets:
            slug = info.event_slug
            if slug:
                events.setdefault(slug, []).append(info)

        opps = []
        for slug, event_markets in events.items():
            map_markets = [m for m in event_markets if m.is_map_market]
            match_markets = [m for m in event_markets if not m.is_map_market]

            if not map_markets or not match_markets:
                continue

            # For each match market, check if any map market has higher YES price
            for match_info in match_markets:
                match_price = prices.get(match_info.market_id)
                if not match_price or match_price.yes_price is None:
                    continue

                for map_info in map_markets:
                    map_price = prices.get(map_info.market_id)
                    if not map_price or map_price.yes_price is None:
                        continue

                    # Map winner can't be more likely than match winner
                    # (winning a map doesn't guarantee winning the match)
                    if map_price.yes_price <= match_price.yes_price:
                        continue

                    edge_pct = (map_price.yes_price - match_price.yes_price) * 100
                    if edge_pct < min_edge:
                        continue

                    profit_per_dollar = edge_pct / 100

                    url = None
                    if match_info.event_slug:
                        url = f"https://polymarket.com/event/{match_info.event_slug}"

                    opp = EsportsOpportunity(
                        detection_type=EsportsDetectionType.MAP_VS_MATCH,
                        tier=_classify_tier(edge_pct),
                        game=match_info.game,
                        edge_pct=edge_pct,
                        profit_per_dollar=profit_per_dollar,
                        description=(
                            f"Map ({map_price.yes_price*100:.0f}¢) priced higher than "
                            f"match ({match_price.yes_price*100:.0f}¢)"
                        ),
                        market_id=match_info.market_id,
                        question=match_info.question,
                        polymarket_url=url,
                        yes_price=match_price.yes_price,
                        no_price=match_price.no_price or 0,
                        related_markets=[map_info.market_id],
                    )
                    opps.append(opp)

        return opps
