"""Shared types and helpers for the PSI scorers (ported from DraftScoutApp/reference/legacy-modules)."""

import math
from dataclasses import dataclass, field
from typing import Literal

Position = Literal["PG", "SG", "SF", "PF", "C"]
POSITIONS: tuple[Position, ...] = ("PG", "SG", "SF", "PF", "C")

# From types/stats.ts
COMPETITION_LEVEL_MULTIPLIERS = {
    "NBA": 1.05,
    "NCAA_P5": 1.0,  # ACC, Big Ten, SEC, Big 12, Pac-12
    "NCAA_D1": 0.88,  # other Division 1
    "GLEAGUE": 0.92,
    "EURO_TOP": 0.95,
    "NCAA_LOW": 0.75,
    "HS": 0.60,
}


@dataclass
class StatLine:
    """types/stats.ts StatLine. Percentages are 0-100, not 0-1."""
    games_played: int
    minutes: float  # 40 when this line is per-40
    pts: float
    reb: float
    ast: float
    stl: float
    blk: float
    tov: float
    fg_pct: float
    three_pct: float
    ft_pct: float
    usg: float  # 0 if unavailable
    position: Position


@dataclass
class AdvancedStats:
    """types/stats.ts AdvancedStats. Any field left as None counts as missing."""
    ts: float | None = None
    efg: float | None = None
    bpm: float | None = None
    ast_tov: float | None = None
    ast_pct: float | None = None
    reb_pct: float | None = None
    stl_pct: float | None = None
    blk_pct: float | None = None
    ortg: float | None = None
    drtg: float | None = None
    usg: float | None = None
    net_rating: float | None = None


@dataclass
class Result:
    score: float
    breakdown: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)
    missing_inputs: list[str] = field(default_factory=list)


def clamp(n: float, low: float, high: float) -> float:
    return max(low, min(high, n))


def js_round(n: float, decimals: int) -> float:
    """JavaScript's Math.round(n * 10^d) / 10^d: halves round up (Python's round() rounds to even)."""
    factor = 10 ** decimals
    return math.floor(n * factor + 0.5) / factor
