import json

import pytest
import requests

from app.db import connect, init_schema
from ingest import cbbd, load_cbbd


def stat(athlete_id, name, season, team, conference="ACC", **extra):
    row = {
        "athleteId": athlete_id, "name": name, "season": season, "teamId": hash(team) % 10_000,
        "team": team, "conference": conference, "position": "G", "games": 30, "starts": 30,
        "minutes": 900, "points": 450, "assists": 60, "steals": 30, "blocks": 10, "turnovers": 40,
        "fouls": 50, "usage": 25.0, "offensiveRating": 110.0, "defensiveRating": 100.0, "PORPAG": 3.1,
        "trueShootingPct": 58.0, "effectiveFieldGoalPct": 54.0,
        "fieldGoals": {"made": 150, "attempted": 300}, "threePointFieldGoals": {"made": 50, "attempted": 140},
        "freeThrows": {"made": 100, "attempted": 120}, "rebounds": {"offensive": 20, "defensive": 80, "total": 100},
        "winShares": {"total": 4.5},
    }
    row.update(extra)
    return row


def write_raw(raw_dir, seasons: dict[int, list], picks: list | None = None):
    raw_dir.mkdir(parents=True, exist_ok=True)
    for season in range(min(seasons), max(seasons) + 1):  # seasons in between get an empty file
        (raw_dir / f"player_season_stats_{season}.json").write_text(json.dumps(seasons.get(season, [])))
    (raw_dir / "draft_picks.json").write_text(json.dumps(picks or []))


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "cbbd.db")
    init_schema(c)
    yield c
    c.close()


def add_prospect(conn, person_id, name, school=None, draft_year=None, pick=None, combine_year=None):
    conn.execute(
        "INSERT INTO prospects (nba_person_id, name, school, draft_year, draft_pick) VALUES (?, ?, ?, ?, ?)",
        (person_id, name, school, draft_year, pick),
    )
    if combine_year:
        conn.execute(
            "INSERT INTO combine (prospect_id, combine_year) SELECT id, ? FROM prospects WHERE nba_person_id = ?",
            (combine_year, person_id),
        )


def links(conn):
    return {r["name"]: (r["cbbd_athlete_id"], r["college_link"], r["conference"])
            for r in conn.execute("SELECT * FROM prospects")}


# --- loading seasons ---

def test_load_keeps_only_d1_and_flattens_stats(tmp_path, conn):
    write_raw(tmp_path, {2026: [stat(1, "D1 Player", 2026, "Duke"), stat(2, "Small School", 2026, "Tiny", None)]})
    load_cbbd.load_cbbd(range(2026, 2027), conn, raw_dir=tmp_path)

    rows = conn.execute("SELECT * FROM college_player_seasons").fetchall()
    assert len(rows) == 1  # the non-D1 row (no conference) is dropped
    row = rows[0]
    assert (row["fgm"], row["fga"], row["fg3a"], row["ftm"], row["oreb"], row["reb"]) == (150, 300, 140, 100, 20, 100)
    assert (row["porpag"], row["win_shares"], row["ts_pct"]) == (3.1, 4.5, 58.0)


def test_load_is_safe_to_rerun(tmp_path, conn):
    write_raw(tmp_path, {2026: [stat(1, "D1 Player", 2026, "Duke")]})
    load_cbbd.load_cbbd(range(2026, 2027), conn, raw_dir=tmp_path)
    write_raw(tmp_path, {2026: [stat(1, "D1 Player", 2026, "Duke", points=500)]})
    load_cbbd.load_cbbd(range(2026, 2027), conn, raw_dir=tmp_path)
    assert tuple(conn.execute("SELECT COUNT(*), MAX(points) FROM college_player_seasons").fetchone()) == (1, 500)


def test_missing_season_loads_nothing(tmp_path, conn):
    write_raw(tmp_path, {2025: [stat(1, "D1 Player", 2025, "Duke")]})
    with pytest.raises(FileNotFoundError):
        load_cbbd.load_cbbd(range(2025, 2027), conn, raw_dir=tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM college_player_seasons").fetchone()[0] == 0


# --- linking prospects ---

def test_link_uses_cbbd_draft_record_first(tmp_path, conn):
    add_prospect(conn, 100, "Same Name", "Duke", 2026, 1)
    write_raw(
        tmp_path,
        {2026: [stat(1, "Same Name", 2026, "Duke"), stat(2, "Same Name", 2026, "Duke")]},
        picks=[{"year": 2026, "overall": 1, "athleteId": 2, "name": "Same Name"}],
    )
    load_cbbd.load_cbbd(range(2026, 2027), conn, raw_dir=tmp_path)
    assert links(conn)["Same Name"] == (2, "cbbd_draft", "ACC")


def test_link_by_unique_name_ignores_suffix_and_accents(tmp_path, conn):
    add_prospect(conn, 100, "André Jackson Jr.", "Connecticut", 2023, 36)
    write_raw(tmp_path, {2023: [stat(7, "Andre Jackson", 2023, "UConn", "Big East")]})
    load_cbbd.load_cbbd(range(2023, 2024), conn, raw_dir=tmp_path)
    assert links(conn)["André Jackson Jr."] == (7, "name", "Big East")


def test_link_breaks_name_tie_with_learned_school_alias(tmp_path, conn):
    # A pick CBBD already links teaches that NBA.com's "California-Los Angeles" is CBBD's "UCLA".
    add_prospect(conn, 100, "Known Bruin", "California-Los Angeles", 2014, 10)
    add_prospect(conn, 101, "Kyle Anderson", "California-Los Angeles", 2014, 30)
    write_raw(
        tmp_path,
        {2014: [stat(1, "Known Bruin", 2014, "UCLA", "Pac-12"),
                stat(2, "Kyle Anderson", 2014, "UCLA", "Pac-12"),
                stat(3, "Kyle Anderson", 2014, "Somewhere Else", "MAC")]},
        picks=[{"year": 2014, "overall": 10, "athleteId": 1, "name": "Known Bruin"}],
    )
    load_cbbd.load_cbbd(range(2014, 2015), conn, raw_dir=tmp_path)
    assert links(conn)["Kyle Anderson"] == (2, "name_school", "Pac-12")


def test_link_prefers_latest_final_season(tmp_path, conn):
    add_prospect(conn, 100, "Brandon Miller", None, 2023, 2)  # no school to break the tie
    write_raw(tmp_path, {2021: [stat(5, "Brandon Miller", 2021, "Alabama A&M", "SWAC")],
                         2023: [stat(6, "Brandon Miller", 2023, "Alabama", "SEC")]})
    load_cbbd.load_cbbd(range(2021, 2024), conn, raw_dir=tmp_path)
    assert links(conn)["Brandon Miller"] == (6, "name_latest_season", "SEC")


def test_link_matches_middle_name_only_at_same_school(tmp_path, conn):
    add_prospect(conn, 100, "Robbie Hummel", "Purdue", 2012, 58)
    add_prospect(conn, 101, "Other Hummel", "Purdue", 2012, 59)
    write_raw(tmp_path, {2012: [stat(9, "Robbie John Hummel", 2012, "Purdue", "Big Ten"),
                                stat(10, "Other James Hummel", 2012, "Indiana", "Big Ten")]})
    load_cbbd.load_cbbd(range(2012, 2013), conn, raw_dir=tmp_path)
    found = links(conn)
    assert found["Robbie Hummel"] == (9, "first_last_school", "Big Ten")
    assert found["Other Hummel"] == (None, None, None)  # different school, so no guess


def test_link_leaves_true_ties_and_out_of_window_unlinked(tmp_path, conn):
    add_prospect(conn, 100, "Jalen Harris", None, 2020, 59)
    add_prospect(conn, 101, "Old Timer", None, None, None, combine_year=2020)
    write_raw(tmp_path, {2014: [stat(4, "Old Timer", 2014, "Duke")],  # 6 seasons before: too old
                         2020: [stat(2, "Jalen Harris", 2020, "Nevada"), stat(3, "Jalen Harris", 2020, "Louisiana Tech")]})
    load_cbbd.load_cbbd(range(2014, 2021), conn, raw_dir=tmp_path)
    found = links(conn)
    assert found["Jalen Harris"] == (None, None, None)
    assert found["Old Timer"] == (None, None, None)


def test_conference_comes_from_last_season_before_draft(tmp_path, conn):
    add_prospect(conn, 100, "Transfer Guy", None, 2025, 40)
    write_raw(tmp_path, {2024: [stat(8, "Transfer Guy", 2024, "Butler", "Big East")],
                         2025: [stat(8, "Transfer Guy", 2025, "Gonzaga", "WCC")]})
    load_cbbd.load_cbbd(range(2024, 2026), conn, raw_dir=tmp_path)
    assert links(conn)["Transfer Guy"] == (8, "name", "WCC")


# --- the API client ---

def test_api_key_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CBBD_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text('OTHER=1\nCBBD_API_KEY="abc123"\n')
    assert cbbd.api_key(env) == "abc123"


def test_api_key_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("CBBD_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CBBD_API_KEY"):
        cbbd.api_key(tmp_path / ".env")


def test_get_json_uses_cache_without_calling(tmp_path, monkeypatch):
    cache = tmp_path / "x.json"
    cache.write_text('[{"cached": true}]')
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("should not call the API"))
    assert cbbd.get_json("/anything", {}, cache) == [{"cached": True}]


def test_get_json_quota_error_saves_nothing(tmp_path, monkeypatch):
    class Response:
        status_code = 429

    monkeypatch.setenv("CBBD_API_KEY", "test")
    monkeypatch.setattr(cbbd, "REQUEST_DELAY_SECONDS", 0)
    monkeypatch.setattr(requests, "get", lambda *a, **k: Response())
    with pytest.raises(RuntimeError, match="rate limit"):
        cbbd.get_json("/stats", {}, tmp_path / "x.json")
    assert not (tmp_path / "x.json").exists()
