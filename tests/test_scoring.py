"""The ported PSI scorers. Expected values are worked out by hand from the original TypeScript formulas."""

import pytest

from scoring.common import AdvancedStats, StatLine, js_round
from scoring.intangibles import calculate_intangibles
from scoring.physical import PhysicalInput, calculate_physical
from scoring.production import calculate_production, normalize_against_benchmark, normalize_ast_tov, normalize_ts
from scoring.psi import PSI_WEIGHTS, combine
from scoring.risk import InjuryRecord, RiskInput, calculate_risk
from scoring.skills import calculate_skills, normalize, normalize_inverse
from scoring.team_fit import PlayerForFit, TeamNeed, TeamProfile, calculate_team_fit


def line(position="PG", **overrides):
    values = dict(games_played=30, minutes=40, pts=25, reb=8, ast=8, stl=2, blk=2, tov=2.5,
                  fg_pct=50, three_pct=36, ft_pct=80, usg=25, position=position)
    values.update(overrides)
    return StatLine(**values)


def test_js_round_rounds_halves_up_like_javascript():
    assert js_round(0.25, 1) == 0.3  # Python's round(0.25, 1) gives 0.2
    assert js_round(72.45, 1) == 72.5


# --- production ---

def test_production_benchmark_anchors():
    assert normalize_against_benchmark(18, (18, 25)) == 50  # mean
    assert normalize_against_benchmark(25, (18, 25)) == 90  # elite
    assert normalize_against_benchmark(32.5, (18, 25)) == 100  # 30% above elite
    assert normalize_against_benchmark(9, (18, 25)) == 25  # half the mean
    assert normalize_ts(55) == 70
    assert normalize_ast_tov(3.0, "PG") == 90 and normalize_ast_tov(2.0, "C") == 90


def test_production_full_calculation():
    # stat score 90 (every category at elite); efficiency = 90*.40 + 90*.25 + 100*.20 + 50*.15 = 86
    # raw = 90*.60 + 86*.25 = 75.5
    adv = AdvancedStats(ts=60, ast_tov=3.0, net_rating=20)
    p5 = calculate_production(line(), adv, "NCAA_P5")
    assert p5.score == 75.5
    assert p5.breakdown["efficiency_score"] == 86
    assert p5.missing_inputs == ["BPM"]
    assert calculate_production(line(), adv, "NCAA_D1").score == js_round(75.5 * 0.88, 1)  # 66.4


def test_production_falls_back_to_per40_ast_tov():
    result = calculate_production(line(ast=6, tov=2), AdvancedStats(ts=60, net_rating=0), "NCAA_P5")
    assert "AST/TOV" not in result.missing_inputs
    assert result.details["ast_tov_score"] == 90  # 6/2 = 3.0 for a guard


# --- skills ---

def test_skills_normalizers():
    assert normalize(12, 12, 28) == 30 and normalize(28, 12, 28) == 90 and normalize(20, 12, 28) == 60
    assert normalize_inverse(1.5, 4.0, 1.5) == 90 and normalize_inverse(4.0, 4.0, 1.5) == 30


def test_skills_weights_by_position():
    result = calculate_skills(line(), AdvancedStats(ts=56.5, ast_tov=3.2, drtg=100), "PG")
    assert set(result.details) == {"scoring", "playmaking", "defense", "shooting", "iq", "athleticism"}
    assert 0 <= result.score <= 100
    assert result.missing_inputs == []


def test_skills_missing_drtg_is_neutral():
    assert "Defensive Rating" in calculate_skills(line(), AdvancedStats(ts=55), "PG").missing_inputs


# --- physical ---

def physical(**overrides):
    values = dict(position="PG", height_in=74, weight_lb=190, wingspan_in=78, age=19.5,
                  max_vertical=35, lane_agility=11.5, sprint=3.3)
    values.update(overrides)
    return PhysicalInput(**values)


def test_physical_at_every_mean_with_full_combine():
    result = calculate_physical(physical())
    assert result.breakdown["athleticism_source"] == "COMBINE"
    assert result.breakdown["athleticism_score"] == 50  # every test at the mean
    assert result.details["age_score"] == 50  # exactly class age
    assert result.missing_inputs == []


def test_physical_wingspan_bonus_quirk_is_kept():
    # 8 inches over height is 3 above the PG elite (5); the original's branch order still gives 5, not 8
    assert calculate_physical(physical(wingspan_in=82)).details["reach_bonus"] == 5


def test_physical_younger_scores_higher_and_infers_missing_tests():
    young = calculate_physical(physical(age=18.5, max_vertical=None, lane_agility=None, sprint=None, per40=line()))
    assert young.details["age_score"] == 62  # one year under class age = +12
    assert young.breakdown["athleticism_source"] == "INFERRED"
    assert young.missing_inputs == ["Max Vertical", "Lane Agility", "Sprint Time"]


# --- intangibles, team fit, risk ---

def test_intangibles_all_missing_is_neutral():
    result = calculate_intangibles()
    assert result.score == 50 and result.details["confidence"] == "MINIMAL"


def test_team_fit_no_needs_is_neutral_alignment():
    team = TeamProfile(mode="RETOOL", roster_positions=["PG", "SG"], preferred_pace="AVERAGE",
                       preferred_spacing="MEDIUM", has_ball_dominant_star=False, defensive_scheme="MIXED",
                       prefers_high_intangibles=False)
    skills = dict(scoring=60, playmaking=60, defense=60, shooting=60, iq=60, athleticism=60)
    result = calculate_team_fit(PlayerForFit("SF", skills, 50, 20, 20), team)
    assert result.breakdown == {"need_alignment": 50, "style_fit": 70, "roster_balance": 80}  # SF fills a gap: +10
    assert result.score == 64.5


def test_team_fit_need_matched():
    team = TeamProfile(mode="REBUILD", roster_positions=[], preferred_pace="FAST", preferred_spacing="HIGH",
                       has_ball_dominant_star=False, defensive_scheme="SWITCH", prefers_high_intangibles=False,
                       top_needs=[TeamNeed("SHOOTING", 80)])
    skills = dict(scoring=60, playmaking=60, defense=60, shooting=80, iq=60, athleticism=60)
    assert calculate_team_fit(PlayerForFit("SG", skills, 50, 20, 19), team).breakdown["need_alignment"] == 100


def risk_input(**overrides):
    values = dict(age=19.5, position="SF", games_played_pct=100, is_one_dimensional=False,
                  competition_level="NCAA_P5", character_score=50, has_character_red_flags=False,
                  athleticism_score=60, production_score=60)
    values.update(overrides)
    return RiskInput(**values)


def test_risk_backtest_defaults():
    # 75 base, +2 no injuries, +3 clean & durable, -8 character 50 (below 55), age at class average 0
    assert calculate_risk(risk_input()).score == 72


def test_risk_adjustments():
    assert calculate_risk(risk_input(age=18)).score == 78  # 1.5 years younger: +6
    assert calculate_risk(risk_input(games_played_pct=80)).score == 64  # -5, and no durability bonus
    assert calculate_risk(risk_input(is_one_dimensional=True)).score == 65
    assert calculate_risk(risk_input(production_score=85)).score == 77  # P5 dominance +5
    major = calculate_risk(risk_input(injury_history=[InjuryRecord("ACL", "MAJOR", recurring=True)]))
    assert major.score == 75 - 20 - 8  # no +2/+3, -20 recurring major, -8 character


# --- PSI ---

def test_psi_weights_sum_to_one_and_combine():
    assert sum(PSI_WEIGHTS.values()) == pytest.approx(1.0)
    assert combine({k: 50 for k in PSI_WEIGHTS}).psi == 50
    assert combine({**{k: 50 for k in PSI_WEIGHTS}, "production": 90}).psi == 60  # +40 x 0.25


def test_psi_needs_all_six():
    with pytest.raises(ValueError):
        combine({"production": 50})
