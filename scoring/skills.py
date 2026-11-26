"""Skills, component 3 of PSI (25%). Port of lib/scoring/skills.ts; constants unchanged.

Six dimensions (0-100), combined with position-specific weights.
"""

from scoring.common import AdvancedStats, Position, Result, StatLine, clamp, js_round

POSITION_SKILL_WEIGHTS = {
    "PG": {"scoring": 0.20, "playmaking": 0.25, "defense": 0.15, "shooting": 0.15, "iq": 0.15, "athleticism": 0.10},
    "SG": {"scoring": 0.25, "playmaking": 0.15, "defense": 0.15, "shooting": 0.20, "iq": 0.10, "athleticism": 0.15},
    "SF": {"scoring": 0.20, "playmaking": 0.15, "defense": 0.20, "shooting": 0.15, "iq": 0.10, "athleticism": 0.20},
    "PF": {"scoring": 0.20, "playmaking": 0.10, "defense": 0.25, "shooting": 0.15, "iq": 0.10, "athleticism": 0.20},
    "C": {"scoring": 0.15, "playmaking": 0.05, "defense": 0.30, "shooting": 0.10, "iq": 0.15, "athleticism": 0.25},
}

STOCK_BENCHMARKS = {"PG": (1.5, 3.5), "SG": (1.5, 3.5), "SF": (2.0, 4.0), "PF": (2.5, 4.5), "C": (3.0, 5.5)}


def calculate_skills(per40: StatLine, advanced: AdvancedStats | None, position: Position) -> Result:
    advanced = advanced or AdvancedStats()
    missing: list[str] = []
    dimensions = {
        "scoring": _scoring(per40, advanced, missing),
        "playmaking": _playmaking(per40, advanced, position, missing),
        "defense": _defense(per40, advanced, position, missing),
        "shooting": _shooting(per40),
        "iq": _iq(per40, advanced),
        "athleticism": _athleticism_from_stats(per40, position),
    }
    weights = POSITION_SKILL_WEIGHTS[position]
    score = sum(dimensions[k] * weights[k] for k in weights)
    return Result(score=js_round(score, 1), details={k: js_round(v, 1) for k, v in dimensions.items()},
                  missing_inputs=missing)


def _scoring(stats: StatLine, adv: AdvancedStats, missing: list[str]) -> float:
    volume = normalize(stats.pts, 12, 28)
    if adv.ts:
        efficiency = normalize(adv.ts, 48, 65)
    else:
        efficiency = normalize(stats.fg_pct, 42, 58)
        missing.append("TS% (for scoring)")
    usage_bonus = 0
    if stats.usg >= 25:
        usage_bonus = 5
    if stats.usg >= 30:
        usage_bonus = 10
    return clamp(volume * 0.55 + efficiency * 0.45 + usage_bonus, 0, 100)


def _playmaking(stats: StatLine, adv: AdvancedStats, position: Position, missing: list[str]) -> float:
    low, high = {"PG": (4, 9), "SG": (3, 7), "SF": (3, 6), "PF": (2, 5), "C": (1, 4)}[position]
    ast_score = normalize(stats.ast, low, high)
    ast_tov = 50.0
    if adv.ast_tov and adv.ast_tov > 0:
        ast_tov = normalize(adv.ast_tov, 1.0, 2.5)
    elif stats.tov > 0:
        ast_tov = normalize(stats.ast / stats.tov, 1.0, 2.5)
    else:
        missing.append("AST/TOV (for playmaking)")
    ast_pct = normalize(adv.ast_pct, 15, 35) if adv.ast_pct else 50.0
    return clamp(ast_score * 0.50 + ast_tov * 0.30 + ast_pct * 0.20, 0, 100)


def _defense(stats: StatLine, adv: AdvancedStats, position: Position, missing: list[str]) -> float:
    low, high = STOCK_BENCHMARKS[position]
    stocks_score = normalize(stats.stl + stats.blk, low, high)
    if adv.drtg:
        drtg_score = normalize_inverse(adv.drtg, 105, 95)
    else:
        drtg_score = 50.0
        missing.append("Defensive Rating")
    stl_bonus = 5 if adv.stl_pct and adv.stl_pct >= 3.0 else 0
    blk_bonus = 5 if adv.blk_pct and position in ("C", "PF") and adv.blk_pct >= 5.0 else 0
    return clamp(stocks_score * 0.60 + drtg_score * 0.40 + stl_bonus + blk_bonus, 0, 100)


def _shooting(stats: StatLine) -> float:
    three = normalize(stats.three_pct, 30, 42) if stats.three_pct > 0 else 50.0
    ft = normalize(stats.ft_pct, 65, 88) if stats.ft_pct > 0 else 50.0
    fg = normalize(stats.fg_pct, 42, 58)
    return clamp(three * 0.45 + ft * 0.35 + fg * 0.20, 0, 100)


def _iq(stats: StatLine, adv: AdvancedStats) -> float:
    tov_score = normalize_inverse(stats.tov, 4.0, 1.5)
    ast_tov = 50.0
    if adv.ast_tov and adv.ast_tov > 0:
        ast_tov = normalize(adv.ast_tov, 1.0, 2.5)
    elif stats.tov > 0:
        ast_tov = normalize(stats.ast / stats.tov, 1.0, 2.5)
    efficiency = normalize(adv.ts, 50, 62) if adv.ts else normalize(stats.fg_pct, 42, 56)
    return clamp(tov_score * 0.40 + ast_tov * 0.30 + efficiency * 0.30, 0, 100)


def _athleticism_from_stats(stats: StatLine, position: Position) -> float:
    low, high = STOCK_BENCHMARKS[position]
    stock_score = normalize(stats.stl + stats.blk, low, high)
    reb_low, reb_high = {"PG": (3, 7), "SG": (4, 8), "SF": (6, 10), "PF": (8, 12), "C": (10, 14)}[position]
    reb_score = normalize(stats.reb, reb_low, reb_high)
    return clamp(stock_score * 0.60 + reb_score * 0.40, 0, 100)


def normalize(value: float, low: float, high: float) -> float:
    """low -> 30, high -> 90, 25% of the range above high -> 100, below low scales to 0."""
    if value <= 0:
        return 0
    if value >= high:
        headroom = (high - low) * 0.25
        return clamp(90 + ((value - high) / headroom) * 10, 0, 100)
    if value <= low:
        return clamp((value / low) * 30, 0, 30)
    return 30 + ((value - low) / (high - low)) * 60


def normalize_inverse(value: float, bad: float, good: float) -> float:
    """For lower-is-better stats (TOV, DRTG)."""
    if value <= good:
        headroom = (bad - good) * 0.25
        return clamp(90 + ((good - value) / headroom) * 10, 0, 100)
    if value >= bad:
        return clamp(30 - ((value - bad) / bad) * 30, 0, 30)
    return 30 + ((bad - value) / (bad - good)) * 60
