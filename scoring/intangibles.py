"""Intangibles, component 4 of PSI (20%). Port of lib/scoring/intangibles.ts; constants unchanged.

Hand-entered by design: this only validates the five 0-100 inputs and combines them.
A missing input defaults to 50 (neutral). The backtest leaves all five missing (docs/PHASE2_RESULT.md).
"""

from scoring.common import Result, clamp, js_round

INTANGIBLES_WEIGHTS = {"work_ethic": 0.25, "coachability": 0.25, "leadership": 0.20,
                       "mental_strength": 0.15, "character": 0.15}


def calculate_intangibles(**inputs: float | None) -> Result:
    missing: list[str] = []
    values = {}
    for name in INTANGIBLES_WEIGHTS:
        value = inputs.get(name)
        if value is None:
            missing.append(name)
            values[name] = 50.0
        else:
            values[name] = clamp(value, 0, 100)

    red_flags = []
    if values["work_ethic"] < 40:
        red_flags.append("Work ethic concerns")
    if values["coachability"] < 40:
        red_flags.append("Coachability concerns")
    if values["character"] < 40:
        red_flags.append("Character red flags")
    if values["mental_strength"] < 35:
        red_flags.append("Mental strength concerns")

    score = sum(values[k] * w for k, w in INTANGIBLES_WEIGHTS.items())
    filled = 5 - len(missing)
    confidence = "COMPLETE" if filled == 5 else "PARTIAL" if filled >= 3 else "MINIMAL"
    return Result(score=js_round(score, 1), breakdown=values, missing_inputs=missing,
                  details={"confidence": confidence, "red_flags": red_flags})
