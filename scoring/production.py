"""Production, component 1 of PSI (25%). Port of lib/scoring/production.ts; constants unchanged.

Production = (StatScore x 0.60) + (EfficiencyScore x 0.25), times the competition multiplier
(the 0.15 competition weight is handled through the multiplier, as in the original).
"""

from scoring.common import COMPETITION_LEVEL_MULTIPLIERS, AdvancedStats, Position, Result, StatLine, clamp, js_round

PRODUCTION_WEIGHTS = {"stat_score": 0.60, "efficiency": 0.25, "competition": 0.15}
STAT_SCORE_WEIGHTS = {"pts": 0.35, "reb": 0.20, "ast": 0.20, "stocks": 0.25}
EFFICIENCY_WEIGHTS = {"ts": 0.40, "ast_tov": 0.25, "net_rating": 0.20, "bpm": 0.15}

# Per-40 benchmarks. Original note: "aggregated from top-10 NBA draft picks 2018-2024".
POSITION_BENCHMARKS = {
    "PG": {"pts": (18, 25), "reb": (5, 8), "ast": (5, 8), "stocks": (2, 4)},
    "SG": {"pts": (18, 25), "reb": (5, 8), "ast": (3, 6), "stocks": (2, 4)},
    "SF": {"pts": (18, 24), "reb": (7, 10), "ast": (3, 6), "stocks": (2.5, 4.5)},
    "PF": {"pts": (17, 22), "reb": (9, 12), "ast": (2.5, 5), "stocks": (2.5, 5)},
    "C": {"pts": (16, 22), "reb": (11, 14), "ast": (1.5, 3.5), "stocks": (3, 6)},
}


def calculate_production(per40: StatLine, advanced: AdvancedStats | None, competition_level: str) -> Result:
    advanced = advanced or AdvancedStats()
    missing: list[str] = []

    bench = POSITION_BENCHMARKS[per40.position]
    stocks = per40.stl + per40.blk
    pts_score = normalize_against_benchmark(per40.pts, bench["pts"])
    reb_score = normalize_against_benchmark(per40.reb, bench["reb"])
    ast_score = normalize_against_benchmark(per40.ast, bench["ast"])
    stocks_score = normalize_against_benchmark(stocks, bench["stocks"])
    stat_score = (pts_score * STAT_SCORE_WEIGHTS["pts"] + reb_score * STAT_SCORE_WEIGHTS["reb"]
                  + ast_score * STAT_SCORE_WEIGHTS["ast"] + stocks_score * STAT_SCORE_WEIGHTS["stocks"])

    ts_score = ast_tov_score = net_rating_score = bpm_score = 50.0  # neutral if missing
    if advanced.ts is not None and advanced.ts > 0:
        ts_score = normalize_ts(advanced.ts)
    else:
        missing.append("TS%")
    if advanced.ast_tov is not None and advanced.ast_tov > 0:
        ast_tov_score = normalize_ast_tov(advanced.ast_tov, per40.position)
    elif per40.tov > 0:
        ast_tov_score = normalize_ast_tov(per40.ast / per40.tov, per40.position)
    else:
        missing.append("AST/TOV")
    if advanced.net_rating is not None:
        net_rating_score = normalize_net_rating(advanced.net_rating)
    else:
        missing.append("Net Rating")
    if advanced.bpm is not None:
        bpm_score = normalize_bpm(advanced.bpm)
    else:
        missing.append("BPM")

    efficiency_score = (ts_score * EFFICIENCY_WEIGHTS["ts"] + ast_tov_score * EFFICIENCY_WEIGHTS["ast_tov"]
                        + net_rating_score * EFFICIENCY_WEIGHTS["net_rating"] + bpm_score * EFFICIENCY_WEIGHTS["bpm"])

    multiplier = COMPETITION_LEVEL_MULTIPLIERS.get(competition_level, 1.0)
    raw = stat_score * PRODUCTION_WEIGHTS["stat_score"] + efficiency_score * PRODUCTION_WEIGHTS["efficiency"]
    score = clamp(raw * multiplier, 0, 100)

    return Result(
        score=js_round(score, 1),
        breakdown={"stat_score": js_round(stat_score, 1), "efficiency_score": js_round(efficiency_score, 1),
                   "competition_multiplier": multiplier, "raw_production": js_round(raw, 1)},
        details={k: js_round(v, 1) for k, v in {
            "pts_score": pts_score, "reb_score": reb_score, "ast_score": ast_score, "stocks_score": stocks_score,
            "ts_score": ts_score, "ast_tov_score": ast_tov_score, "net_rating_score": net_rating_score,
            "bpm_score": bpm_score}.items()},
        missing_inputs=missing,
    )


def normalize_against_benchmark(value: float, benchmark: tuple[float, float]) -> float:
    """mean -> 50, elite -> 90, 30% above elite -> 100, below mean linear toward 0."""
    mean, elite = benchmark
    if value <= 0:
        return 0
    if value >= elite:
        headroom = elite * 0.3
        return clamp(90 + ((value - elite) / headroom) * 10, 0, 100)
    if value >= mean:
        return 50 + ((value - mean) / (elite - mean)) * 40
    return (value / mean) * 50


def normalize_ts(ts: float) -> float:
    if ts <= 40:
        return 0
    if ts >= 70:
        return 100
    if ts >= 60:
        return 90 + ((ts - 60) / 10) * 10
    if ts >= 50:
        return 50 + ((ts - 50) / 10) * 40
    return ((ts - 40) / 10) * 50


def normalize_ast_tov(ratio: float, position: Position) -> float:
    guard = position in ("PG", "SG")
    elite = 3.0 if guard else 2.0
    mean = 1.5 if guard else 1.0
    if ratio <= 0.3:
        return 0
    if ratio >= elite * 1.5:
        return 100
    if ratio >= elite:
        return 90 + ((ratio - elite) / (elite * 0.5)) * 10
    if ratio >= mean:
        return 50 + ((ratio - mean) / (elite - mean)) * 40
    return ((ratio - 0.3) / (mean - 0.3)) * 50


def normalize_net_rating(net_rating: float) -> float:
    if net_rating <= -20:
        return 0
    if net_rating >= 20:
        return 100
    return clamp(((net_rating + 20) / 40) * 100, 0, 100)


def normalize_bpm(bpm: float) -> float:
    if bpm <= -10:
        return 0
    if bpm >= 15:
        return 100
    if bpm >= 8:
        return 90 + ((bpm - 8) / 7) * 10
    if bpm >= 0:
        return 50 + (bpm / 8) * 40
    return ((bpm + 10) / 10) * 50
