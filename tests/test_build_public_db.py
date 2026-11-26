"""The public snapshot: only allowed tables, no raw stats, no players from excluded programs."""

import sys

import pytest
from fastapi.testclient import TestClient

import scripts.build_public_db as snapshot
from app.db import connect, get_db, init_schema
from app.main import app
from scripts.build_public_db import ALLOWED_TABLES, build_public_db, normalize_name, read_excluded_teams

EXCLUDED = "Excluded U"  # a made-up program

FORBIDDEN_TABLES = {"player_season_stats", "player_game_stats", "games", "game_teams",
                    "college_player_seasons", "combine", "seasons", "teams", "sqlite_sequence"}

COLLEGE_COLUMNS = ("athlete_id, season, team_id, team, conference, name, position, games, minutes, points, fgm,"
                   " fga, fg3m, fg3a, ftm, fta, reb, ast, stl, blk, tov, usage, ortg, drtg, ts_pct")


def college_row(athlete_id, season, team, name):
    return (athlete_id, season, 1 if team == "Duke" else 2, team, "ACC" if team == "Duke" else "Other", name, "G",
            30, 1000, 500, 180, 400, 50, 140, 90, 110, 150, 100, 30, 10, 60, 24.0, 115.0, 100.0, 0.575)


@pytest.fixture
def source(tmp_path):
    """A full database with one ordinary prospect, two from an excluded program, and NBA seasons for each."""
    conn = connect(tmp_path / "source.db")
    init_schema(conn)
    with conn:
        conn.execute("INSERT INTO teams VALUES (1, 'GSW', 'Golden State Warriors', 'Golden State')")
        conn.executemany("INSERT INTO seasons VALUES (?, ?)", [("2019-20", 2019), ("2020-21", 2020)])
        conn.executemany("INSERT INTO players VALUES (?, ?)",
                         [(500, "Duke Guard"), (501, "Transfer Hidden"), (502, "Undrafted Hidden Jr.")])
        conn.executemany(
            "INSERT INTO player_season_stats (player_id, season_id, team_id, gp, min, pts, reb, ast)"
            " VALUES (?, ?, 1, ?, ?, ?, ?, ?)",
            [(pid, season, 60, 20.0, 10.0, 4.0, 2.0) for pid in (500, 501, 502) for season in ("2019-20", "2020-21")])
        conn.executemany(
            "INSERT INTO prospects (id, nba_person_id, name, draft_year, draft_pick, school, conference,"
            " cbbd_athlete_id, birth_date, position) VALUES (?, ?, ?, 2019, ?, ?, ?, ?, '1999-01-01', 'SG')",
            [(1, 500, "Duke Guard", 5, "Duke", "ACC", 77),
             # Started at the excluded program, then transferred: his draft school isn't it, his college data is.
             (2, 501, "Transfer Hidden", 6, "Duke", "ACC", 78)])
        conn.executemany(f"INSERT INTO college_player_seasons ({COLLEGE_COLUMNS})"
                         f" VALUES ({', '.join('?' * 25)})",
                         [college_row(77, 2019, "Duke", "Duke Guard"),
                          college_row(78, 2018, EXCLUDED, "Transfer Hidden"),
                          college_row(78, 2019, "Duke", "Transfer Hidden"),
                          college_row(79, 2018, EXCLUDED, "Undrafted Hidden")])
    yield conn
    conn.close()


@pytest.fixture
def built(source, tmp_path):
    out = tmp_path / "public.db"
    counts = build_public_db(source, out, {EXCLUDED})
    conn = connect(out)
    yield conn, counts
    conn.close()


def all_text(conn):
    """Every text value in every table of the snapshot."""
    values = []
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        for row in conn.execute(f"SELECT * FROM {table}"):
            values += [v for v in row if isinstance(v, str)]
    return values


def test_snapshot_has_only_allowed_tables(built):
    conn, counts = built
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == ALLOWED_TABLES
    assert not tables & FORBIDDEN_TABLES
    assert set(counts) == ALLOWED_TABLES


def test_snapshot_prospects_have_no_private_columns(built):
    conn, _ = built
    columns = {r[1] for r in conn.execute("PRAGMA table_info(prospects)")}
    assert not columns & {"birth_date", "cbbd_athlete_id", "nba_person_id", "missing_box_share"}


def test_build_fails_and_writes_nothing_if_a_forbidden_table_appears(source, tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "SNAPSHOT_TABLES",
                        snapshot.SNAPSHOT_TABLES + "CREATE TABLE player_season_stats (player_id INTEGER);")
    out = tmp_path / "public.db"
    with pytest.raises(RuntimeError, match="player_season_stats"):
        build_public_db(source, out, {EXCLUDED})
    assert not out.exists()
    assert not list(tmp_path.glob("public.db*"))


def test_excluded_program_players_are_left_out_everywhere(built):
    conn, counts = built
    assert [r[0] for r in conn.execute("SELECT name FROM prospects")] == ["Duke Guard"]
    assert [r[0] for r in conn.execute("SELECT name FROM players")] == ["Duke Guard"]
    assert {r[0] for r in conn.execute("SELECT player_id FROM player_progression")} == {500}
    assert [r[0] for r in conn.execute("SELECT prospect_id FROM prospect_scores")] == [1]
    assert not [v for v in all_text(conn) if "Hidden" in v or EXCLUDED in v]


def test_ranks_are_computed_without_excluded_players(built):
    conn, _ = built
    row = conn.execute("SELECT psi_rank, class_size FROM prospect_scores WHERE prospect_id = 1").fetchone()
    assert (row["psi_rank"], row["class_size"]) == (1, 1)  # not 1 of 2: the class shows no gap


def test_source_is_left_unchanged(source, built):
    assert source.execute("SELECT COUNT(*) FROM prospect_scores").fetchone()[0] == 0
    assert source.execute("SELECT COUNT(*) FROM player_progression").fetchone()[0] == 0


def test_normalize_name():
    assert normalize_name("A.J. Smith Jr.") == "aj smith"
    assert normalize_name("Karl-Anthony Towns III") == "karl anthony towns"


def test_api_runs_on_the_snapshot(built, monkeypatch):
    conn, _ = built
    monkeypatch.setenv("PUBLIC_MODE", "1")
    monkeypatch.setenv("UPSIDE_API_KEY", "test-key")
    app.dependency_overrides[get_db] = lambda: conn
    try:
        client = TestClient(app)
        assert client.get("/v1/prospects").json()["total"] == 1
        assert client.get("/v1/prospects/1/psi").status_code == 200
        assert client.get("/v1/prospects/2/psi").status_code == 404  # excluded: not in the snapshot
        assert client.get("/v1/players/500/progression").json()["total"] == 1
        assert client.get("/v1/players/501/progression").status_code == 404
        assert client.get("/v1/players", headers={"X-API-Key": "test-key"}).status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_no_exclusions_keeps_everyone(source, tmp_path):
    out = tmp_path / "everyone.db"
    build_public_db(source, out, set())
    conn = connect(out)
    assert conn.execute("SELECT COUNT(*) FROM prospects").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 3
    conn.close()


def test_read_excluded_teams_skips_blanks_and_comments(tmp_path):
    path = tmp_path / "teams.txt"
    path.write_text("# private list\nExcluded U\n\n  Another College  # trailing note\n")
    assert read_excluded_teams(path) == {"Excluded U", "Another College"}


def test_script_stops_without_an_exclusions_file(tmp_path, monkeypatch):
    out = tmp_path / "public.db"
    monkeypatch.setattr(sys, "argv", ["build_public_db", "--source", str(tmp_path / "source.db"), "--out", str(out),
                                      "--exclusions", str(tmp_path / "missing.txt")])
    with pytest.raises(SystemExit) as stopped:
        snapshot.main()
    assert stopped.value.code == 2
    assert not out.exists()
