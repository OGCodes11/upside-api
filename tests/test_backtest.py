"""Input rules (docs/PHASE2_RESULT.md) and the backtest's outcome and measure."""

import pandas as pd
import pytest

from app.db import connect, init_schema
from scoring import backtest
from scoring.inputs import age_on_draft_day, build_inputs, competition_level, position_from_label


@pytest.mark.parametrize("label, expected", [
    ("SG-SF", "SG"), ("PF-C", "PF"), ("PG", "PG"), ("G", "SG"), ("F", "SF"), ("C", "C"),
    ("G-F", "SG"), ("F-C", "SF"), ("ATH", None), ("NA", None), ("", None), (None, None),
])
def test_position_rule(label, expected):
    assert position_from_label(label) == expected


def test_competition_rule():
    assert competition_level("Big Ten") == "NCAA_P5"
    assert competition_level("Big East") == "NCAA_D1"  # as the scorer defines it
    assert competition_level("WCC") == "NCAA_D1"


def test_age_on_draft_day():
    assert age_on_draft_day("1993-03-11", 2012) == 19.29
    assert age_on_draft_day(None, 2012) is None


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "bt.db")
    init_schema(c)
    yield c
    c.close()


def add_prospect(conn, season=2018, drtg=100.0, birth="1999-06-25", position_label="G", combine=True):
    conn.execute("INSERT INTO prospects (id, nba_person_id, name, draft_year, draft_pick, cbbd_athlete_id,"
                 " birth_date, position) VALUES (1, 500, 'Test Guard', ?, 10, 77, ?, ?)",
                 (season, birth, "SG-SF" if combine else None))
    conn.execute(
        "INSERT INTO college_player_seasons (athlete_id, season, team_id, team, conference, name, position, games,"
        " minutes, points, fgm, fga, fg3m, fg3a, ftm, fta, reb, ast, stl, blk, tov, usage, ortg, drtg, ts_pct)"
        " VALUES (77, ?, 1, 'Duke', 'ACC', 'Test Guard', ?, 30, 1000, 500, 180, 400, 50, 140, 90, 110,"
        " 150, 100, 30, 10, 60, 24.0, 115.0, ?, 0.575)", (season, position_label, drtg))
    if combine:
        conn.execute("INSERT INTO combine (prospect_id, combine_year, height_in, weight_lb, wingspan_in)"
                     " VALUES (1, ?, 77, 205, 81)", (season,))


def test_build_inputs_applies_the_rules(conn):
    add_prospect(conn)
    inp = build_inputs(conn, 1)
    assert (inp.position, inp.position_source) == ("SG", "combine")
    assert inp.competition_level == "NCAA_P5"
    assert inp.per40.pts == 20  # 500 points in 1000 minutes
    assert inp.advanced.ts == pytest.approx(57.5)  # fraction -> 0-100
    assert inp.advanced.net_rating == 15  # 115 - 100
    assert inp.advanced.ast_tov == pytest.approx(100 / 60)
    assert (inp.age, inp.age_source) == (19.0, "nba")
    assert inp.combine["wingspan_in"] == 81


def test_build_inputs_drops_old_or_impossible_defensive_ratings(conn):
    add_prospect(conn, season=2013, drtg=105.0)  # plausible value, but 2013 and earlier is broken
    inp = build_inputs(conn, 1)
    assert inp.advanced.drtg is None and inp.advanced.net_rating is None
    conn.execute("UPDATE college_player_seasons SET season = 2018, drtg = 813.4")
    conn.execute("UPDATE prospects SET draft_year = 2018")
    assert build_inputs(conn, 1).advanced.drtg is None


def test_build_inputs_fallbacks(conn):
    add_prospect(conn, birth=None, position_label="F-C", combine=False)
    inp = build_inputs(conn, 1)
    assert (inp.position, inp.position_source) == ("SF", "cbbd")
    assert (inp.age, inp.age_source) == (19.5, "none")
    assert inp.combine is None


def test_year3_season():
    assert backtest.year3_season(2012) == "2014-15"
    assert backtest.year3_season(2021) == "2023-24"


def test_run_measures_against_slot():
    df = pd.DataFrame({"draft_year": [2015] * 4, "pick": [1, 2, 3, 4], "psi": [60, 70, 50, 40],
                       "year3_minutes": [2000, 1500, 800, 0], "missing_box_flag": [False] * 4,
                       "physical_source": ["combine"] * 4})
    result = backtest.run(df, 2012, 2021)
    assert result["slot_rho"] == 1.0  # picks in exactly the outcome's order
    assert result["psi_rho"] == 0.8
