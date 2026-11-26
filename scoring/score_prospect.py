"""Score one prospect: run the six ported scorers on his inputs and combine them into PSI.

Backtest settings (docs/PHASE2_RESULT.md): Intangibles and Team Fit are 50 for everyone;
Physical is 50 when there are no combine size measurements.
"""

from dataclasses import dataclass

from scoring.inputs import ProspectInputs
from scoring.intangibles import calculate_intangibles
from scoring.physical import PhysicalInput, calculate_physical
from scoring.production import calculate_production
from scoring.psi import combine
from scoring.risk import RiskInput, calculate_risk
from scoring.skills import calculate_skills

NEUTRAL = 50.0


@dataclass
class ProspectScore:
    inputs: ProspectInputs
    psi: float
    components: dict[str, float]
    skill_dimensions: dict[str, float]
    physical_source: str  # combine or none
    missing_inputs: list[str]


def score_prospect(inp: ProspectInputs) -> ProspectScore:
    production = calculate_production(inp.per40, inp.advanced, inp.competition_level)
    skills = calculate_skills(inp.per40, inp.advanced, inp.position)
    intangibles = calculate_intangibles()  # all five missing -> 50

    if inp.combine:
        physical = calculate_physical(PhysicalInput(
            position=inp.position, height_in=inp.combine["height_in"], weight_lb=inp.combine["weight_lb"],
            wingspan_in=inp.combine["wingspan_in"], age=inp.age, max_vertical=inp.combine["max_vert_in"],
            lane_agility=inp.combine["lane_agility_s"], sprint=inp.combine["sprint_s"], per40=inp.per40,
        )).score
        physical_source = "combine"
    else:
        physical, physical_source = NEUTRAL, "none"

    dims = skills.details
    risk = calculate_risk(RiskInput(
        age=inp.age, position=inp.position, games_played_pct=inp.games_played_pct,
        is_one_dimensional=sum(v > 70 for v in dims.values()) == 1,
        competition_level=inp.competition_level, character_score=intangibles.breakdown["character"],
        has_character_red_flags=bool(intangibles.details["red_flags"]),
        athleticism_score=dims["athleticism"], production_score=production.score,
    ))

    components = {
        "production": production.score, "skills": skills.score, "intangibles": intangibles.score,
        "physical": physical, "team_fit": NEUTRAL, "risk": risk.score,
    }
    result = combine(components)
    return ProspectScore(
        inputs=inp, psi=result.psi, components=components, skill_dimensions=dims,
        physical_source=physical_source,
        missing_inputs=sorted(set(production.missing_inputs + skills.missing_inputs)),
    )
