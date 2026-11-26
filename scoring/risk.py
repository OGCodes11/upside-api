"""Risk Adjustment, component 6 of PSI (5%). Port of lib/scoring/risk.ts; constants unchanged.

Starts at 75 and adds or subtracts points for injuries, durability, character, age,
skill set, competition and position. 100 = lowest risk.
"""

from dataclasses import dataclass, field

from scoring.common import Position, Result, clamp, js_round


@dataclass
class InjuryRecord:
    type: str
    severity: str  # MINOR, MODERATE, MAJOR
    recurring: bool = False


@dataclass
class RiskInput:
    age: float
    position: Position
    games_played_pct: float  # 0-100
    is_one_dimensional: bool
    competition_level: str
    character_score: float
    has_character_red_flags: bool
    athleticism_score: float  # from skills
    production_score: float
    injury_history: list[InjuryRecord] = field(default_factory=list)
    class_average_age: float = 19.5
    is_multi_sport_elite: bool = False


def calculate_risk(inp: RiskInput) -> Result:
    flags: list[tuple[str, str, str, float]] = []  # (category, severity, description, impact)
    total = 0.0

    injury_adj, injury_score = _injuries(inp.injury_history, flags)
    total += injury_adj
    durability_adj, durability_score = _durability(inp.games_played_pct, flags)
    total += durability_adj
    character_adj, character_score = _character(inp.character_score, inp.has_character_red_flags, flags)
    total += character_adj
    age_adj, age_score = _age(inp.age, inp.class_average_age, flags)
    total += age_adj
    skill_adj, skill_score = _skill_set(inp, flags)
    total += skill_adj

    if inp.competition_level == "NCAA_LOW":
        flags.append(("COMPETITION", "MAJOR", "Lower-level competition raises translation risk", -8))
        total -= 8
    if inp.position == "C" and inp.athleticism_score < 60:
        flags.append(("POSITIONAL", "MINOR", "Traditional center archetype in modern, switch-heavy NBA", -5))
        total -= 5
    if inp.is_multi_sport_elite:
        flags.append(("BONUS", "POSITIVE", "Multi-sport elite athletic background", 4))
        total += 4
    if not inp.injury_history and inp.games_played_pct >= 95:
        flags.append(("BONUS", "POSITIVE", "Clean injury history with elite durability", 3))
        total += 3
    if inp.competition_level == "NCAA_P5" and inp.production_score >= 80:
        flags.append(("BONUS", "POSITIVE", "Dominant production vs. top-level competition", 5))
        total += 5

    score = clamp(75 + total, 0, 100)
    return Result(
        score=js_round(score, 1),
        breakdown={"total_adjustment": js_round(total, 1), "durability_score": js_round(durability_score, 1),
                   "injury_risk_score": js_round(injury_score, 1), "character_risk_score": js_round(character_score, 1),
                   "age_score": js_round(age_score, 1), "skill_set_risk_score": js_round(skill_score, 1)},
        details={"flags": flags},
    )


def _injuries(injuries: list[InjuryRecord], flags) -> tuple[float, float]:
    if not injuries:
        return 2, 100
    adj, reduction = 0.0, 0.0
    for inj in injuries:
        if inj.severity == "MAJOR":
            adj -= 12
            reduction += 25
            flags.append(("INJURY", "CRITICAL" if inj.recurring else "MAJOR",
                          f"{'Recurring ' if inj.recurring else ''}major injury: {inj.type}",
                          -20 if inj.recurring else -12))
            if inj.recurring:
                adj -= 8
        elif inj.severity == "MODERATE":
            adj -= 6
            reduction += 12
            flags.append(("INJURY", "MAJOR", f"Moderate injury: {inj.type}", -6))
        else:
            adj -= 2
            reduction += 5
            flags.append(("INJURY", "MINOR", f"Minor injury history: {inj.type}", -2))
    return adj, clamp(100 - reduction, 0, 100)


def _durability(pct: float, flags) -> tuple[float, float]:
    if pct >= 95:
        return 0, 100
    if pct >= 85:
        return -2, 80
    if pct >= 70:
        flags.append(("INJURY", "MINOR", f"Missed ~{round(100 - pct)}% of available games", -5))
        return -5, 60
    flags.append(("INJURY", "MAJOR", f"Durability concern — missed {round(100 - pct)}% of games", -10))
    return -10, 30


def _character(score: float, red_flags: bool, flags) -> tuple[float, float]:
    if red_flags or score < 40:
        flags.append(("CHARACTER", "CRITICAL", "Significant character red flags", -20))
        return -20, 20
    if score < 55:
        flags.append(("CHARACTER", "MAJOR", "Below-average character profile", -8))
        return -8, 50
    if score >= 85:
        return 3, 100
    return 0, 75


def _age(age: float, class_age: float, flags) -> tuple[float, float]:
    diff = class_age - age
    if diff >= 1.5:
        flags.append(("BONUS", "POSITIVE", f"Significantly younger than draft class (age {age})", 6))
        return 6, 95
    if diff >= 0.5:
        flags.append(("BONUS", "POSITIVE", "Younger than draft class average", 3))
        return 3, 80
    if diff >= -0.5:
        return 0, 70
    if diff >= -2:
        flags.append(("AGE", "MINOR", f"Older than draft class average (age {age})", -4))
        return -4, 50
    flags.append(("AGE", "MAJOR", f"Significantly older than typical draft class (age {age})", -8))
    return -8, 30


def _skill_set(inp: RiskInput, flags) -> tuple[float, float]:
    adj, score = 0.0, 75.0
    if inp.is_one_dimensional:
        flags.append(("SKILL_SET", "MAJOR", "One-dimensional skill profile limits NBA role flexibility", -7))
        adj -= 7
        score -= 20
    if inp.athleticism_score >= 80 and inp.production_score < 60:
        flags.append(("SKILL_SET", "MAJOR", "Production trails athletic profile — may struggle at NBA speed", -8))
        adj -= 8
        score -= 20
    return adj, clamp(score, 0, 100)
