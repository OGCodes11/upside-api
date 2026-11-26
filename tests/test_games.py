import json
from datetime import date

import pandas as pd
import pytest

from app.db import connect, init_schema
from app.game_stats import SMALL_POSTSEASON_SAMPLE, season_stats
from ingest import cbbd, gleague, load_games


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "games.db")
    init_schema(c)
    yield c
    c.close()


# --- classifying games ---

MAR, NOV, FEB_1, FEB_25 = date(2019, 3, 10), date(2018, 11, 20), date(2019, 2, 1), date(2019, 2, 25)


@pytest.mark.parametrize("args, expected", [
    (("regular", None, "STD", True, True, FEB_1), "regular_season"),
    (("regular", None, "TRNMNT", True, True, MAR), "conference_tournament"),
    (("regular", None, "TRNMNT", False, True, MAR), "conference_tournament"),  # the NEC case
    (("regular", None, "TRNMNT", False, False, NOV), "regular_season"),  # Maui Invitational
    (("regular", None, "TRNMNT", True, True, NOV), "regular_season"),  # same-conference MTE game
    # 2008: no TRNMNT label, so notes or a neutral site decide
    (("regular", None, "STD", True, True, MAR, "SEC Men's Tournament - Quarterfinals"), "conference_tournament"),
    (("regular", None, "STD", True, True, MAR, None, True), "conference_tournament"),
    (("regular", None, "STD", True, True, FEB_25, "OHIO VALLEY CONF TR"), "conference_tournament"),
    (("regular", None, "STD", True, True, FEB_1, None, True), "regular_season"),  # too early
    (("regular", None, "STD", False, False, MAR, None, True), "regular_season"),  # not same conference
    (("postseason", "NCAA", "TRNMNT", False, False, MAR), "ncaa_tournament"),
    (("postseason", "NIT", "TRNMNT", False, False, MAR), "nit"),
    (("postseason", "CBI", "TRNMNT", False, False, MAR), "other_postseason"),
    (("postseason", None, "STD", False, False, MAR, "MasterCard NIT - 1st Round"), "nit"),
    (("postseason", None, "STD", False, False, MAR, "MEN'S BASKETBALL CHAMPIONSHIP - EAST REGION"), "ncaa_tournament"),
    (("postseason", None, "STD", False, False, MAR, "CBI CHAMPIONSHIP SERIES - GAME 1"), "other_postseason"),
    (("postseason", None, "STD", False, False, MAR, "CIT - 1st ROUND"), "other_postseason"),
    (("preseason", None, "STD", False, False, NOV), None),
])
def test_classify_cbbd_game(args, expected):
    assert load_games.classify_cbbd_game(*args) == expected


def test_eastern_date_moves_late_games_back_a_day():
    assert load_games.eastern_date("2018-11-21T02:30:00.000Z") == date(2018, 11, 20)  # 9:30 pm ET
    assert load_games.eastern_date("2019-03-22T16:00:00.000Z") == date(2019, 3, 22)


# --- pulling CBBD windows ---

def test_season_windows_cover_the_season_without_gaps():
    windows = cbbd.season_windows(2019)
    assert windows[0][0] == date(2018, 11, 1) and windows[-1][1] == date(2019, 4, 15)
    for (_, end), (next_start, _) in zip(windows, windows[1:]):
        assert (next_start - end).days == 1


def fake_rows(n_games, day="2019-01-10"):
    return [{"gameId": g, "startDate": f"{day}T20:00:00.000Z"} for g in range(n_games) for _ in (0, 1)]


def test_full_window_is_split_instead_of_losing_games(tmp_path, monkeypatch):
    calls = []

    def fake_get_json(endpoint, params, cache_file, refresh=False):
        calls.append(params["startDateRange"][:10])
        rows = fake_rows(500) if len(calls) == 1 else fake_rows(10)  # first call hits 1,000 rows
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(rows))
        return rows

    monkeypatch.setattr(cbbd, "get_json", fake_get_json)
    rows = cbbd.game_boxscores_window(date(2019, 1, 10), date(2019, 1, 19), raw_dir=tmp_path)

    assert calls == ["2019-01-10", "2019-01-10", "2019-01-15"]  # full window, then its two halves
    assert len(rows) == 40
    assert not (tmp_path / "games" / "players_2019-01-10_2019-01-19.json").exists()  # truncated file removed


def test_full_single_day_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(cbbd, "get_json", lambda *a, **k: fake_rows(500))
    with pytest.raises(RuntimeError, match="cannot split"):
        cbbd.game_boxscores_window(date(2019, 1, 10), date(2019, 1, 10), raw_dir=tmp_path)


# --- loading ---

def team_game(game_id, team, conference, opponent, start, season_type="regular", game_type="STD",
              tournament=None, players=()):
    return {"gameId": game_id, "season": 2019, "seasonType": season_type, "tournament": tournament,
            "gameType": game_type, "conferenceGame": False, "conference": conference,
            "opponentConference": "ACC", "team": team, "opponent": opponent, "startDate": start,
            "notes": None, "players": list(players)}


def player(athlete_id, name, minutes, points, fga=10, fta=4):
    return {"athleteId": athlete_id, "name": name, "starter": True, "minutes": minutes, "points": points,
            "fieldGoals": {"made": 5, "attempted": fga}, "threePointFieldGoals": {"made": 1, "attempted": 3},
            "freeThrows": {"made": 2, "attempted": fta}, "rebounds": {"offensive": 1, "defensive": 4, "total": 5},
            "assists": 2, "steals": 1, "blocks": 0, "turnovers": 1, "fouls": 2}


def test_load_cbbd_file_keeps_d1_and_records_pull_time(tmp_path, conn):
    path = tmp_path / "players.json"
    path.write_text(json.dumps([
        team_game(1, "Duke", "ACC", "Tiny College", "2018-11-21T02:30:00.000Z", players=[player(10, "Star", 30, 20)]),
        team_game(1, "Tiny College", None, "Duke", "2018-11-21T02:30:00.000Z", players=[player(99, "Walk On", 5, 0)]),
    ]))
    with conn:
        assert load_games.load_cbbd_file(conn, path) == (1, 1)  # the non-D1 team's player is dropped

    game = conn.execute("SELECT * FROM games").fetchone()
    assert (game["start_date"], game["game_type"], game["has_box_score"]) == ("2018-11-20", "regular_season", 1)
    row = conn.execute("SELECT * FROM player_game_stats").fetchone()
    assert (row["player_id"], row["pts"], row["fga"], row["oreb"], row["starter"]) == (10, 20, 10, 1, 1)
    assert row["stats_as_of"] == load_games.pulled_at(path)


def test_game_listed_without_players_is_flagged(tmp_path, conn):
    path = tmp_path / "players.json"
    path.write_text(json.dumps([
        team_game(2, "Kentucky", "SEC", "LSU", "2012-03-09T17:00:00.000Z"),  # no players on either side
        team_game(2, "LSU", "SEC", "Kentucky", "2012-03-09T17:00:00.000Z"),
    ]))
    with conn:
        assert load_games.load_cbbd_file(conn, path) == (1, 0)
    assert conn.execute("SELECT has_box_score FROM games").fetchone()[0] == 0


# --- missing box score share ---

def add_team_games(conn, season, team, n_with_box, n_without_box, prefix):
    for i in range(n_with_box + n_without_box):
        game_id = f"{prefix}{i}"
        conn.execute(
            "INSERT INTO games (league, game_id, season, start_date, game_type, has_box_score)"
            " VALUES ('ncaa_d1', ?, ?, ?, 'regular_season', ?)",
            (game_id, season, f"{season}-01-15", int(i < n_with_box)),
        )
        conn.execute("INSERT INTO game_teams (league, game_id, team) VALUES ('ncaa_d1', ?, ?)", (game_id, team))


def add_linked_prospect(conn, person_id, name, athlete_id, draft_year, seasons):
    """seasons: [(season, team, games)] rows for college_player_seasons."""
    conn.execute("INSERT INTO prospects (nba_person_id, name, draft_year, cbbd_athlete_id) VALUES (?, ?, ?, ?)",
                 (person_id, name, draft_year, athlete_id))
    for season, team, games in seasons:
        conn.execute(
            "INSERT INTO college_player_seasons (athlete_id, season, team_id, team, conference, name, games)"
            " VALUES (?, ?, ?, ?, 'X', ?, ?)",
            (athlete_id, season, abs(hash(team)) % 10_000, team, name, games),
        )


def missing_box(conn, name):
    return tuple(conn.execute(
        "SELECT missing_box_share, missing_box_flag FROM prospects WHERE name = ?", (name,)).fetchone())


def test_missing_box_share_and_flag(conn):
    add_team_games(conn, 2011, "Kentucky", 30, 0, "a")
    add_team_games(conn, 2012, "Kentucky", 27, 7, "b")
    add_team_games(conn, 2012, "TCU", 30, 0, "s")
    # 7 of 64 team games missing = 10.9%; the stray 1-game TCU row is ignored
    add_linked_prospect(conn, 1, "Gap Guy", 101, 2012, [(2011, "Kentucky", 30), (2012, "Kentucky", 34), (2012, "TCU", 1)])
    # 2 of 32 missing = 6.25%: under the line
    add_team_games(conn, 2013, "Duke", 30, 2, "c")
    add_linked_prospect(conn, 2, "Small Gap", 102, 2013, [(2013, "Duke", 32)])
    assert load_games.update_missing_box_scores(conn) == {"flagged": 1, "not_flagged": 1, "no_game_data": 0}
    assert missing_box(conn, "Gap Guy") == (0.1094, 1)
    assert missing_box(conn, "Small Gap") == (0.0625, 0)


def test_missing_box_ignores_non_gap_seasons_and_later_seasons(conn):
    add_team_games(conn, 2021, "Gonzaga", 25, 5, "d")  # 2021 empties look like COVID cancellations
    add_team_games(conn, 2012, "Gonzaga", 20, 10, "e")  # after his draft year, so not his
    add_linked_prospect(conn, 3, "Covid Year", 103, 2011, [(2021, "Gonzaga", 25)])
    add_linked_prospect(conn, 4, "Clean", 104, 2021, [(2021, "Gonzaga", 25)])
    load_games.update_missing_box_scores(conn)
    assert missing_box(conn, "Clean") == (0.0, 0)
    assert missing_box(conn, "Covid Year") == (None, None)  # his only season is after his draft year


def test_missing_box_null_without_game_data(conn):
    add_linked_prospect(conn, 5, "Old Timer", 105, 2008, [(2007, "Kansas", 30)])  # 2007 not loaded
    assert load_games.update_missing_box_scores(conn)["no_game_data"] == 1
    assert missing_box(conn, "Old Timer") == (None, None)


# --- hand-entered box scores ---

MANUAL_COLUMNS = ["team", "name", "starter", "minutes", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
                  "reb", "ast", "tov", "stl", "blk", "oreb", "dreb", "pf"]


def manual_setup(conn, tmp_path, home_pts=10, has_box=0):
    """Game 'g1' listed without a box score, plus one earlier game that gives each player his id."""
    add_game(conn, "g0", "2025-03-01", "regular_season", season=2025)
    add_game(conn, "g1", "2025-03-14", "conference_tournament", season=2025)
    conn.execute("UPDATE games SET has_box_score = ? WHERE game_id = 'g1'", (has_box,))
    for pid, name, team in [(1, "Home Star", "Home"), (2, "Away Star", "Away")]:
        conn.execute("INSERT INTO player_game_stats (league, game_id, player_id, name, team, stats_as_of)"
                     " VALUES ('ncaa_d1', 'g0', ?, ?, ?, 'x')", (pid, name, team))
    box = {"league": "ncaa_d1", "game_id": "g1", "entered_at": "2026-11-23T07:30:00+00:00",
           "final_score": {"Home": 10, "Away": 8}, "columns": MANUAL_COLUMNS,
           "players": [["Home", "Home Star", 1, 30, home_pts, 4, 9, 0, 1, 2, 2, 5, 1, 2, 0, 0, 1, 4, 2],
                       ["Away", "Away Star", 1, 30, 8, 3, 8, 1, 3, 1, 2, 4, 2, 1, 1, 0, 0, 4, 3]]}
    (tmp_path / "box.json").write_text(json.dumps(box))


def test_manual_box_score_fills_a_missing_game(conn, tmp_path):
    manual_setup(conn, tmp_path)
    assert load_games.load_manual_boxscores(conn, tmp_path) == 1
    rows = conn.execute("SELECT player_id, opponent, pts, source, stats_as_of FROM player_game_stats"
                        " WHERE game_id = 'g1' ORDER BY player_id").fetchall()
    assert [tuple(r) for r in rows] == [(1, "Away", 10, "manual", "2026-11-23T07:30:00+00:00"),
                                        (2, "Home", 8, "manual", "2026-11-23T07:30:00+00:00")]
    assert conn.execute("SELECT has_box_score FROM games WHERE game_id = 'g1'").fetchone()[0] == 1


def test_manual_box_score_never_overwrites_source_data(conn, tmp_path):
    manual_setup(conn, tmp_path, has_box=1)
    assert load_games.load_manual_boxscores(conn, tmp_path) == 0


def test_manual_box_score_must_add_up(conn, tmp_path):
    manual_setup(conn, tmp_path, home_pts=9)  # final score says 10
    with pytest.raises(ValueError, match="final score"):
        load_games.load_manual_boxscores(conn, tmp_path)


def write_ignite(raw_dir, rows_by_file):
    raw_dir.mkdir(parents=True, exist_ok=True)
    cols = ["PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION", "GAME_ID", "GAME_DATE", "MATCHUP", "MIN", "PTS",
            "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TOV", "PF"]
    for season in gleague.IGNITE_SEASONS:
        for season_type in gleague.SEASON_TYPES:
            rows = rows_by_file.get((season, season_type), [])
            pd.DataFrame(rows, columns=cols).to_csv(gleague.logs_path(season, season_type, raw_dir), index=False)


def test_load_ignite_keeps_ids_and_handles_empty_seasons(tmp_path, conn):
    row = [1630703, "Scoot Henderson", "GLI", "0022200478", "2023-03-25T00:00:00", "GLI @ SBL",
           30.5, 21, 8, 15, 1, 4, 4, 5, 1, 4, 5, 7, 1, 0, 3, 2]
    write_ignite(tmp_path, {("2022-23", "Regular Season"): [row]})  # every other file is empty
    with conn:
        assert load_games.load_ignite(conn, tmp_path) == (1, 1)
    game = conn.execute("SELECT * FROM games").fetchone()
    assert (game["league"], game["game_id"], game["season"], game["start_date"]) == ("g_league", "0022200478", 2023, "2023-03-25")
    stats = conn.execute("SELECT * FROM player_game_stats").fetchone()
    assert (stats["player_id"], stats["opponent"], stats["pts"]) == (1630703, "SBL", 21)


# --- season stats ---

def add_game(conn, game_id, start_date, game_type, league="ncaa_d1", season=2019):
    conn.execute("INSERT INTO games (league, game_id, season, start_date, game_type) VALUES (?, ?, ?, ?, ?)",
                 (league, game_id, season, start_date, game_type))


def add_line(conn, game_id, player_id, minutes, pts, fga=10, fta=0, league="ncaa_d1", pulled="2019-04-20T00:00:00+00:00"):
    conn.execute(
        "INSERT INTO player_game_stats (league, game_id, player_id, name, team, minutes, pts, fgm, fga, fg3m,"
        " fg3a, ftm, fta, reb, ast, stl, blk, tov, stats_as_of) VALUES (?, ?, ?, 'P', 'T', ?, ?, 0, ?, 0, 0, 0, ?,"
        " 1, 1, 0, 0, 0, ?)",
        (league, game_id, player_id, minutes, pts, fga, fta, pulled),
    )


@pytest.fixture
def season(conn):
    """Player 1: 3 regular, 1 conference tournament, 2 NCAA games, and one game he didn't play."""
    games = [("r1", "2018-12-01", "regular_season"), ("r2", "2019-01-05", "regular_season"),
             ("r3", "2019-02-02", "regular_season"), ("c1", "2019-03-14", "conference_tournament"),
             ("n1", "2019-03-22", "ncaa_tournament"), ("n2", "2019-03-24", "ncaa_tournament"),
             ("dnp", "2019-02-09", "regular_season")]
    for game_id, day, game_type in games:
        add_game(conn, game_id, day, game_type)
    for game_id in ("r1", "r2", "r3", "c1"):
        add_line(conn, game_id, 1, 30, 20)
    add_line(conn, "n1", 1, 40, 10)
    add_line(conn, "n2", 1, 40, 10)
    add_line(conn, "dnp", 1, 0, 0)  # on the box score, 0 minutes
    return conn


def test_include_postseason_is_the_only_switch(season):
    with_post = season_stats(season, include_postseason=True)[0]
    without = season_stats(season, include_postseason=False)[0]
    assert (with_post.games, with_post.pts, with_post.minutes) == (6, 100, 200)
    assert (without.games, without.pts, without.minutes) == (4, 80, 120)  # conference tournament stays in


def test_split_is_reported_either_way(season):
    for include in (True, False):
        line = season_stats(season, include_postseason=include)[0]
        assert (line.regular_games, line.regular_pts_per40) == (4, 26.7)
        assert (line.postseason_games, line.postseason_pts_per40) == (2, 10.0)  # 20 pts in 80 min
        assert line.postseason_ts_pct == 0.5  # 20 pts / (2 * 20 fga)


def test_small_postseason_sample_flag(season, conn):
    assert season_stats(season)[0].small_postseason_sample is True  # 2 games
    for i in range(SMALL_POSTSEASON_SAMPLE - 2):  # bring him to exactly 5
        add_game(conn, f"x{i}", "2019-03-30", "nit")
        add_line(conn, f"x{i}", 1, 30, 10)
    assert season_stats(conn)[0].postseason_games == SMALL_POSTSEASON_SAMPLE
    assert season_stats(conn)[0].small_postseason_sample is False


def test_as_of_cuts_off_later_games(season):
    line = season_stats(season, as_of="2019-03-22")[0]  # the day the NCAA tournament starts
    assert (line.games, line.postseason_games, line.last_game) == (4, 0, "2019-03-14")


def test_zero_minute_games_dont_count(season):
    assert season_stats(season, include_postseason=False)[0].games == 4  # not 5


def test_leagues_stay_separate(season, conn):
    add_game(conn, "r1", "2019-01-01", "regular_season", league="g_league")
    add_line(conn, "r1", 1, 30, 40, league="g_league")
    assert season_stats(conn, league="g_league")[0].pts == 40
    assert season_stats(conn, league="ncaa_d1")[0].pts == 100
