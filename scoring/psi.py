"""PSI (Prospect Strength Index), 0-100.

The original master file (lib/scoring/psi.ts) was not found, so PSI is the plain weighted sum of the
six components with the weights below (pre-registered Nov 23 2026; docs/PHASE2_RESULT.md).
Every component score is returned too, so split signals stay visible.
"""

from dataclasses import dataclass

from scoring.common import js_round

PSI_WEIGHTS = {
    "production": 0.25,
    "skills": 0.25,
    "intangibles": 0.20,
    "physical": 0.15,
    "team_fit": 0.10,
    "risk": 0.05,
}


@dataclass
class PSIResult:
    psi: float
    components: dict[str, float]


def combine(components: dict[str, float]) -> PSIResult:
    if set(components) != set(PSI_WEIGHTS):
        raise ValueError(f"PSI needs exactly these components: {sorted(PSI_WEIGHTS)}")
    psi = sum(components[name] * weight for name, weight in PSI_WEIGHTS.items())
    return PSIResult(psi=js_round(psi, 1), components=dict(components))
