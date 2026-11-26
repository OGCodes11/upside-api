"""Public, derived-data endpoints: /v1/prospects, /v1/prospects/{id}/psi, /v1/players/{id}/progression."""

import pytest

from app.db import connect, init_schema
from scoring.derived import build_prospect_scores, rank_within_class

# --- /v1/prospects ---


def test_prospects_list_with_scores_and_flags(client):
    body = client.get("/v1/prospects").json()
    assert body["total"] == 4
    assert [p["name"] for p in body["items"]] == ["Older Pick", "Alpha Guard", "Bravo Forward", "Unscored Invitee"]
    alpha = body["items"][1]
    assert alpha == {"id": 1, "name": "Alpha Guard", "draft_year": 2024, "draft_pick": 3, "school": "Duke",
                     "conference": "ACC", "psi": 58.2, "psi_rank": 1, "class_size": 2,
                     "missing_box_flag": False, "small_postseason_sample": True, "college_link": "name"}
    assert body["items"][3]["psi"] is None  # listed, but not scorable


def test_prospects_filter_by_draft_year(client):
    body = client.get("/v1/prospects", params={"draft_year": 2024}).json()
    assert body["total"] == 2
    assert [p["psi_rank"] for p in body["items"]] == [1, 2]
    assert client.get("/v1/prospects", params={"draft_year": 2030}).json()["total"] == 0


def test_prospects_paging(client):
    body = client.get("/v1/prospects", params={"limit": 2, "offset": 1}).json()
    assert (body["total"], body["limit"], body["offset"]) == (4, 2, 1)
    assert [p["name"] for p in body["items"]] == ["Alpha Guard", "Bravo Forward"]


def test_prospects_bad_input(client):
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"draft_year": 1999}, {"draft_year": "abc"}):
        assert client.get("/v1/prospects", params=params).status_code == 422, params


def test_prospects_serve_no_raw_stats(client):
    raw_fields = {"pts", "reb", "ast", "minutes", "games", "fgm", "fga", "height_in", "wingspan_in"}
    item = client.get("/v1/prospects").json()["items"][0]
    assert not raw_fields & set(item)


# --- /v1/prospects/{id}/psi ---


def test_psi_shows_every_component_with_neutral_parts_labeled(client):
    body = client.get("/v1/prospects/2/psi").json()
    assert body["psi"] == 54.0 and body["psi_version"] == "v1-neutral-hand-inputs"
    status = {c["name"]: c["status"] for c in body["components"]}
    assert status == {
        "production": "scored", "skills": "scored", "intangibles": "neutral: hand-entered, not scored",
        "physical": "neutral: no combine measurements", "team_fit": "neutral: hand-entered, not scored",
        "risk": "scored",
    }
    assert sum(c["weight"] for c in body["components"]) == pytest.approx(1.0)
    assert body["missing_inputs"] == ["BPM"]
    assert body["flags"]["missing_box_flag"] is True and body["flags"]["college_link"] == "cbbd_draft"
    assert body["tournament_split"]["postseason_games"] == 6
    assert body["tournament_split"]["small_postseason_sample"] is False
    assert set(body["skill_dimensions"]) == {"scoring", "playmaking", "defense", "shooting", "iq", "athleticism"}


def test_psi_unknown_prospect_404(client):
    response = client.get("/v1/prospects/999/psi")
    assert response.status_code == 404
    assert response.json() == {"detail": "Prospect not found"}


def test_psi_known_but_unscored_404(client):
    response = client.get("/v1/prospects/3/psi")
    assert response.status_code == 404
    assert response.json() == {"detail": "No PSI for this prospect"}


def test_psi_bad_id_422(client):
    assert client.get("/v1/prospects/abc/psi").status_code == 422


# --- /v1/players/{id}/progression ---


def test_progression_changes_and_trend(client):
    body = client.get("/v1/players/13/progression").json()
    assert body["player_id"] == 13 and body["total"] == 2
    first, second = body["items"]
    assert first == {"season": "2021-22", "previous_season": "2020-21", "seasons_apart": 1, "games_change": -45,
                     "minutes_change": 5.0, "pts_change": 4.0, "reb_change": 1.0, "ast_change": 1.0,
                     "traded": True, "small_sample": True}
    assert (second["season"], second["seasons_apart"], second["pts_change"]) == ("2023-24", 2, 6.0)
    assert body["trend"] == {"changes": 2, "small_sample_changes": 2, "minutes_per_season": 5.0,
                             "pts_per_season": 5.0, "reb_per_season": 1.0, "ast_per_season": 1.0}


def test_progression_serves_no_raw_season_lines(client):
    item = client.get("/v1/players/13/progression").json()["items"][0]
    assert not {"pts", "reb", "ast", "minutes", "gp", "min"} & set(item)


def test_progression_paging(client):
    body = client.get("/v1/players/13/progression", params={"limit": 1, "offset": 1}).json()
    assert (body["total"], body["limit"], body["offset"]) == (2, 1, 1)
    assert [c["season"] for c in body["items"]] == ["2023-24"]
    assert body["trend"]["changes"] == 2  # the trend covers every change, not just the page


def test_progression_single_season_player_is_empty(client):
    body = client.get("/v1/players/12/progression").json()
    assert body["total"] == 0 and body["items"] == []
    assert body["trend"]["pts_per_season"] is None


def test_progression_unknown_player_404(client):
    response = client.get("/v1/players/99/progression")
    assert response.status_code == 404
    assert response.json() == {"detail": "Player not found"}


def test_progression_bad_input_422(client):
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        assert client.get("/v1/players/13/progression", params=params).status_code == 422, params
    assert client.get("/v1/players/abc/progression").status_code == 422


# --- building the derived tables ---


def test_rank_within_class_shares_ties():
    ranks = rank_within_class([(1, 2024, 60.0), (2, 2024, 55.0), (3, 2024, 60.0), (4, 2013, 50.0)])
    assert ranks == {1: (1, 3), 3: (1, 3), 2: (3, 3), 4: (1, 1)}


def test_build_prospect_scores_from_college_data(tmp_path):
    conn = connect(tmp_path / "derived.db")
    init_schema(conn)
    conn.execute("INSERT INTO prospects (id, nba_person_id, name, draft_year, draft_pick, cbbd_athlete_id,"
                 " birth_date, position) VALUES (1, 500, 'Test Guard', 2018, 10, 77, '1999-06-25', 'SG')")
    conn.execute(
        "INSERT INTO college_player_seasons (athlete_id, season, team_id, team, conference, name, position, games,"
        " minutes, points, fgm, fga, fg3m, fg3a, ftm, fta, reb, ast, stl, blk, tov, usage, ortg, drtg, ts_pct)"
        " VALUES (77, 2018, 1, 'Duke', 'ACC', 'Test Guard', 'G', 30, 1000, 500, 180, 400, 50, 140, 90, 110,"
        " 150, 100, 30, 10, 60, 24.0, 115.0, 100.0, 0.575)")
    conn.execute("INSERT INTO prospects (id, nba_person_id, name) VALUES (2, 501, 'No College')")

    assert build_prospect_scores(conn, conn, [1, 2]) == 1  # the second has no college season
    row = conn.execute("SELECT * FROM prospect_scores").fetchone()
    assert (row["prospect_id"], row["psi_rank"], row["class_size"]) == (1, 1, 1)
    assert (row["intangibles"], row["team_fit"]) == (50.0, 50.0)
    assert row["postseason_games"] is None  # no per-game data loaded for that season
    conn.close()
