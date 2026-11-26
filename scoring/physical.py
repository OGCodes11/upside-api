"""Physical, component 2 of PSI (15%). Port of lib/scoring/physical.ts; constants unchanged.

Physical = (Size x 0.35) + (Athleticism x 0.40) + (Projectability x 0.25)

Ported as written, including one quirk: the original checks the wingspan bonus with
`>= elite` before `>= elite + 2`, so the 8-point bonus can never be reached; it always gives 5.
"""

from dataclasses import dataclass

from scoring.common import Position, Result, StatLine, clamp, js_round

PHYSICAL_WEIGHTS = {"size": 0.35, "athleticism": 0.40, "projectability": 0.25}

# (mean, elite), inches and pounds. Original note: "NBA average measurements by position, 2010-2024 draftees".
PHYSICAL_BENCHMARKS = {
    "PG": {"height": (74, 78), "weight": (190, 215), "wingspan": (78, 82), "reach_over_height": (4, 5)},
    "SG": {"height": (77, 80), "weight": (205, 220), "wingspan": (80, 84), "reach_over_height": (4, 6)},
    "SF": {"height": (80, 82), "weight": (215, 230), "wingspan": (83, 86), "reach_over_height": (4, 6)},
    "PF": {"height": (81, 83), "weight": (230, 245), "wingspan": (85, 88), "reach_over_height": (4, 6)},
    "C": {"height": (83, 85), "weight": (245, 265), "wingspan": (87, 91), "reach_over_height": (4, 7)},
}
COMBINE_BENCHMARKS = {"max_vertical": (35, 42), "lane_agility": (11.5, 10.8), "sprint": (3.3, 3.05)}
IDEAL_BMI = {"PG": (22, 26, 24), "SG": (22, 27, 25), "SF": (23, 27, 25), "PF": (24, 28, 26), "C": (24, 29, 27)}


@dataclass
class PhysicalInput:
    position: Position
    height_in: float
    weight_lb: float
    wingspan_in: float
    age: float
    max_vertical: float | None = None
    lane_agility: float | None = None
    sprint: float | None = None
    class_age: float = 19.5
    per40: StatLine | None = None


def calculate_physical(inp: PhysicalInput) -> Result:
    missing: list[str] = []
    bench = PHYSICAL_BENCHMARKS[inp.position]

    height_score = normalize_against_benchmark(inp.height_in, bench["height"])
    weight_score = normalize_against_benchmark(inp.weight_lb, bench["weight"])
    wingspan_score = normalize_against_benchmark(inp.wingspan_in, bench["wingspan"])
    reach_over_height = inp.wingspan_in - inp.height_in
    elite_reach = bench["reach_over_height"][1]
    reach_bonus = 0
    if reach_over_height >= elite_reach:
        reach_bonus = 5
    elif reach_over_height >= elite_reach + 2:  # unreachable, as in the original
        reach_bonus = 8
    size_score = clamp(height_score * 0.35 + wingspan_score * 0.40 + weight_score * 0.25 + reach_bonus, 0, 100)

    vertical_score = agility_score = sprint_score = inferred = 50.0
    if inp.max_vertical is not None:
        vertical_score = normalize_against_benchmark(inp.max_vertical, COMBINE_BENCHMARKS["max_vertical"])
    else:
        missing.append("Max Vertical")
    if inp.lane_agility is not None:
        agility_score = normalize_against_benchmark(inp.lane_agility, COMBINE_BENCHMARKS["lane_agility"], True)
    else:
        missing.append("Lane Agility")
    if inp.sprint is not None:
        sprint_score = normalize_against_benchmark(inp.sprint, COMBINE_BENCHMARKS["sprint"], True)
    else:
        missing.append("Sprint Time")
    if inp.per40 is not None:
        inferred = _infer_athleticism(inp.per40, inp.position)

    tests = [inp.max_vertical, inp.lane_agility, inp.sprint]
    count = sum(t is not None for t in tests)
    if count == 3:
        source = "COMBINE"
        athleticism = vertical_score * 0.50 + agility_score * 0.30 + sprint_score * 0.20
    elif count > 0:
        source = "PARTIAL"
        athleticism = ((vertical_score if inp.max_vertical is not None else inferred) * 0.50
                       + (agility_score if inp.lane_agility is not None else inferred) * 0.30
                       + (sprint_score if inp.sprint is not None else inferred) * 0.20)
    else:
        source = "INFERRED"
        athleticism = inferred

    age_score = clamp(50 + (inp.class_age - inp.age) * 12, 0, 100)
    body_score = _body_projectability(inp)
    projectability = age_score * 0.55 + body_score * 0.45

    score = clamp(size_score * PHYSICAL_WEIGHTS["size"] + athleticism * PHYSICAL_WEIGHTS["athleticism"]
                  + projectability * PHYSICAL_WEIGHTS["projectability"], 0, 100)
    return Result(
        score=js_round(score, 1),
        breakdown={"size_score": js_round(size_score, 1), "athleticism_score": js_round(athleticism, 1),
                   "projectability_score": js_round(projectability, 1), "athleticism_source": source},
        details={"reach_bonus": reach_bonus, **{k: js_round(v, 1) for k, v in {
            "height_score": height_score, "weight_score": weight_score, "wingspan_score": wingspan_score,
            "vertical_score": vertical_score, "agility_score": agility_score, "sprint_score": sprint_score,
            "inferred_athleticism_score": inferred, "age_score": age_score,
            "body_projectability_score": body_score}.items()}},
        missing_inputs=missing,
    )


def _infer_athleticism(stats: StatLine, position: Position) -> float:
    bench = {"PG": (1.5, 3.0), "SG": (1.7, 3.2), "SF": (2.0, 3.5), "PF": (2.5, 4.5), "C": (3.0, 5.5)}[position]
    return normalize_against_benchmark(stats.stl + stats.blk, bench)


def normalize_against_benchmark(value: float, benchmark: tuple[float, float], lower_is_better: bool = False) -> float:
    mean, elite = benchmark
    if value <= 0:
        return 0
    if lower_is_better:
        if value <= elite:
            headroom = elite * 0.05
            return clamp(90 + ((elite - value) / headroom) * 10, 0, 100)
        if value <= mean:
            return 50 + ((mean - value) / (mean - elite)) * 40
        return clamp(50 - ((value - mean) / mean) * 100, 0, 50)
    if value >= elite:
        headroom = elite * 0.10
        return clamp(90 + ((value - elite) / headroom) * 10, 0, 100)
    if value >= mean:
        return 50 + ((value - mean) / (elite - mean)) * 40
    return (value / mean) * 50


def _body_projectability(inp: PhysicalInput) -> float:
    height_m = inp.height_in * 0.0254
    weight_kg = inp.weight_lb * 0.453592
    bmi = weight_kg / (height_m * height_m)
    low, high, ideal = IDEAL_BMI[inp.position]
    if low <= bmi <= high:
        bmi_score = 100 - (abs(bmi - ideal) / ((high - low) / 2)) * 30
    elif bmi < low:
        bmi_score = clamp(70 - (low - bmi) * 5, 40, 70)
    else:
        bmi_score = clamp(60 - (bmi - high) * 10, 20, 60)
    if inp.age <= 19 and bmi < ideal:
        bmi_score = clamp(bmi_score + 10, 0, 100)
    return bmi_score
