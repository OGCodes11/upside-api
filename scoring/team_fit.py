"""Team Fit, component 5 of PSI (10%). Port of lib/scoring/teamFit.ts; constants unchanged.

TeamFit = (NeedAlignment x 0.40) + (StyleFit x 0.35) + (RosterBalance x 0.25)

Needs a hand-built team profile. The backtest has none and uses 50 (docs/PHASE2_RESULT.md);
the scorer is ported so the live app can use it later.
"""

from dataclasses import dataclass, field

from scoring.common import Position, Result, clamp, js_round

TEAM_FIT_WEIGHTS = {"need_alignment": 0.40, "style_fit": 0.35, "roster_balance": 0.25}


@dataclass
class TeamNeed:
    category: str  # SCORING, PLAYMAKING, DEFENSE, SHOOTING, REBOUNDING
    urgency: float  # 0-100
    position: Position | None = None


@dataclass
class TeamProfile:
    mode: str  # REBUILD, RETOOL, CONTEND
    roster_positions: list[Position]
    preferred_pace: str  # SLOW, AVERAGE, FAST
    preferred_spacing: str  # LOW, MEDIUM, HIGH
    has_ball_dominant_star: bool
    defensive_scheme: str  # SWITCH, DROP, BLITZ, MIXED
    prefers_high_intangibles: bool
    top_needs: list[TeamNeed] = field(default_factory=list)


@dataclass
class PlayerForFit:
    position: Position
    skills: dict[str, float]  # the six skill dimensions
    intangibles_score: float
    usage: float
    age: float


def calculate_team_fit(player: PlayerForFit, team: TeamProfile) -> Result:
    need_score, addressed = _need_alignment(player, team)
    style_score, conflicts, strengths, culture = _style_fit(player, team)
    balance_score, warnings = _roster_balance(player, team)
    score = clamp(need_score * TEAM_FIT_WEIGHTS["need_alignment"] + style_score * TEAM_FIT_WEIGHTS["style_fit"]
                  + balance_score * TEAM_FIT_WEIGHTS["roster_balance"], 0, 100)
    verdict = "STRONG_FIT" if score >= 80 else "GOOD_FIT" if score >= 65 else "NEUTRAL" if score >= 45 else "POOR_FIT"
    return Result(
        score=js_round(score, 1),
        breakdown={"need_alignment": js_round(need_score, 1), "style_fit": js_round(style_score, 1),
                   "roster_balance": js_round(balance_score, 1)},
        details={"addressed_needs": addressed, "style_conflicts": conflicts, "style_strengths": strengths,
                 "redundancy_warnings": warnings, "culture_alignment": js_round(culture, 1), "verdict": verdict},
    )


def _skill_for_need(category: str, skills: dict[str, float]) -> float:
    if category == "REBOUNDING":
        return (skills["defense"] + skills["athleticism"]) / 2
    return skills[category.lower()]


def _need_alignment(player: PlayerForFit, team: TeamProfile):
    addressed, total_addressed, total_urgency = [], 0.0, 0.0
    for need in team.top_needs:
        total_urgency += need.urgency
        strength = _skill_for_need(need.category, player.skills)
        if strength >= 70:
            addressed.append(need)
            total_addressed += need.urgency
        elif strength >= 55:
            addressed.append(need)
            total_addressed += need.urgency * 0.5
        if need.position and need.position == player.position:
            total_addressed += need.urgency * 0.25
    score = clamp((total_addressed / total_urgency) * 100, 0, 100) if total_urgency > 0 else 50
    return score, addressed


def _style_fit(player: PlayerForFit, team: TeamProfile):
    s, conflicts, strengths = 70.0, [], []
    sk = player.skills
    if team.preferred_pace == "FAST" and sk["athleticism"] >= 75:
        s += 8
        strengths.append("Athletic profile fits fast-pace system")
    elif team.preferred_pace == "SLOW" and sk["iq"] >= 75:
        s += 6
        strengths.append("High IQ fits methodical system")
    elif team.preferred_pace == "FAST" and sk["athleticism"] < 50:
        s -= 8
        conflicts.append("Below-average athleticism in fast-pace system")

    if team.preferred_spacing == "HIGH" and sk["shooting"] >= 75:
        s += 10
        strengths.append("Elite spacing in floor-spacing system")
    elif team.preferred_spacing == "HIGH" and sk["shooting"] < 50:
        s -= 12
        conflicts.append("Poor shooting limits floor-spacing system")

    if team.has_ball_dominant_star and player.usage >= 28:
        s -= 15
        conflicts.append("High usage conflicts with existing primary creator")
    elif team.has_ball_dominant_star and sk["shooting"] >= 75:
        s += 8
        strengths.append("Off-ball threat complements ball-dominant star")

    if team.defensive_scheme == "SWITCH" and sk["defense"] >= 75:
        s += 6
        strengths.append("Versatile defender fits switch-everything scheme")
    elif team.defensive_scheme == "DROP" and player.position == "C" and sk["defense"] >= 75:
        s += 6
        strengths.append("Rim-protecting big anchors drop coverage")

    if team.prefers_high_intangibles:
        culture = player.intangibles_score
        if player.intangibles_score >= 80:
            s += 5
            strengths.append("Strong intangibles align with team culture")
        elif player.intangibles_score < 55:
            s -= 8
            conflicts.append("Intangibles below team culture standards")
    else:
        culture = (player.intangibles_score + 50) / 2
        if player.intangibles_score < 45:
            s -= 4
            conflicts.append("Character concerns may not fit organization")
    return clamp(s, 0, 100), conflicts, strengths, culture


def _roster_balance(player: PlayerForFit, team: TeamProfile):
    s, warnings = 70.0, []
    same = sum(1 for p in team.roster_positions if p == player.position)
    if same >= 3:
        s -= 15
        warnings.append(f"Roster already has {same} players at {player.position}")
    elif same == 0:
        s += 10
    if team.mode == "CONTEND" and player.age <= 20:
        s -= 8
        warnings.append("Young prospect on contending team — may not contribute soon enough")
    elif team.mode == "REBUILD" and player.age >= 24:
        s -= 10
        warnings.append("Older prospect on rebuilding team — timeline misalignment")
    elif team.mode == "REBUILD" and player.age <= 20:
        s += 8
    if team.mode == "CONTEND" and player.intangibles_score >= 80:
        s += 5
    elif team.mode == "REBUILD" and player.skills["athleticism"] >= 80:
        s += 5
    return clamp(s, 0, 100), warnings
