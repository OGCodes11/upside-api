def test_list_players_defaults_to_latest_season(client):
    body = client.get("/v1/players").json()
    assert body["total"] == 3
    assert {p["season"] for p in body["items"]} == {"2025-26"}
    assert [p["name"] for p in body["items"]] == ["Alpha Guard", "Bravo Forward", "Charlie Center"]


def test_list_players_by_season(client):
    body = client.get("/v1/players", params={"season": "2024-25"}).json()
    assert body["total"] == 1
    assert body["items"][0]["team"] == "LAL"


def test_list_players_filters_by_team_case_insensitive(client):
    body = client.get("/v1/players", params={"team": "gsw"}).json()
    assert body["total"] == 2
    assert {p["team"] for p in body["items"]} == {"GSW"}


def test_list_players_flags_traded_players(client):
    items = client.get("/v1/players").json()["items"]
    assert {p["name"]: p["traded"] for p in items} == {
        "Alpha Guard": False, "Bravo Forward": True, "Charlie Center": False,
    }


def test_list_players_pages_with_limit_and_offset(client):
    body = client.get("/v1/players", params={"limit": 1, "offset": 1}).json()
    assert body["total"] == 3  # total ignores paging
    assert (body["limit"], body["offset"]) == (1, 1)
    assert [p["name"] for p in body["items"]] == ["Bravo Forward"]


def test_list_players_unknown_season_is_empty(client):
    body = client.get("/v1/players", params={"season": "1990-91"}).json()
    assert body == {"items": [], "total": 0, "limit": 25, "offset": 0}


def test_list_players_rejects_bad_input(client):
    assert client.get("/v1/players", params={"limit": 0}).status_code == 422
    assert client.get("/v1/players", params={"limit": 101}).status_code == 422
    assert client.get("/v1/players", params={"season": "2025"}).status_code == 422


def test_get_player_profile(client):
    response = client.get("/v1/players/10")
    assert response.status_code == 200
    assert response.json() == {
        "id": 10, "name": "Alpha Guard", "team": "GSW",
        "first_season": "2024-25", "last_season": "2025-26", "seasons_played": 2,
    }


def test_player_seasons_in_season_order(client):
    body = client.get("/v1/players/10/seasons").json()
    assert body["total"] == 2
    assert [(s["season"], s["team"]) for s in body["items"]] == [("2024-25", "LAL"), ("2025-26", "GSW")]
    assert body["items"][0] == {"season": "2024-25", "team": "LAL", "traded": False, "games": 70,
                                "minutes": 30.0, "pts": 15.0, "reb": 3.0, "ast": 6.0}


def test_player_seasons_traded_flag(client):
    items = client.get("/v1/players/11/seasons").json()["items"]
    assert [s["traded"] for s in items] == [True]


def test_player_seasons_pages_with_limit_and_offset(client):
    body = client.get("/v1/players/10/seasons", params={"limit": 1, "offset": 1}).json()
    assert (body["total"], body["limit"], body["offset"]) == (2, 1, 1)
    assert [s["season"] for s in body["items"]] == ["2025-26"]


def test_player_seasons_unknown_player_returns_404(client):
    response = client.get("/v1/players/99/seasons")
    assert response.status_code == 404
    assert response.json() == {"detail": "Player not found"}


def test_player_seasons_rejects_bad_limit(client):
    assert client.get("/v1/players/10/seasons", params={"limit": 0}).status_code == 422
    assert client.get("/v1/players/10/seasons", params={"limit": 101}).status_code == 422


def test_get_missing_player_returns_404(client):
    response = client.get("/v1/players/99")
    assert response.status_code == 404
    assert response.json() == {"detail": "Player not found"}
