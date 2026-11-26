"""The raw endpoints are open locally and need X-API-Key when PUBLIC_MODE=1."""

import pytest
from fastapi.testclient import TestClient

from app.db import connect, get_db
from app.main import app

RAW_PATHS = ["/v1/players", "/v1/players/10", "/v1/players/10/seasons"]
PUBLIC_PATHS = ["/v1/prospects", "/v1/prospects/1/psi", "/v1/players/13/progression"]
TEST_KEY = "test-key-not-a-secret"


@pytest.fixture
def public_mode(monkeypatch):
    monkeypatch.setenv("PUBLIC_MODE", "1")
    monkeypatch.setenv("UPSIDE_API_KEY", TEST_KEY)


# --- local mode ---


@pytest.mark.parametrize("path", RAW_PATHS)
def test_local_mode_raw_endpoints_need_no_key(client, monkeypatch, path):
    monkeypatch.delenv("PUBLIC_MODE", raising=False)
    assert client.get(path).status_code == 200


def test_public_mode_must_be_exactly_1(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_MODE", "0")
    assert client.get("/v1/players").status_code == 200


# --- public mode ---


@pytest.mark.parametrize("path", RAW_PATHS)
def test_public_mode_without_key_is_401(client, public_mode, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.json() == {"detail": "Missing or invalid API key"}


@pytest.mark.parametrize("path", RAW_PATHS)
def test_public_mode_wrong_key_is_401(client, public_mode, path):
    assert client.get(path, headers={"X-API-Key": "wrong"}).status_code == 401


@pytest.mark.parametrize("path", RAW_PATHS)
def test_public_mode_right_key_is_200(client, public_mode, path):
    assert client.get(path, headers={"X-API-Key": TEST_KEY}).status_code == 200


def test_public_mode_with_no_key_configured_locks_everyone_out(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_MODE", "1")
    monkeypatch.delenv("UPSIDE_API_KEY", raising=False)
    assert client.get("/v1/players", headers={"X-API-Key": ""}).status_code == 401
    assert client.get("/v1/players").status_code == 401


def test_key_is_checked_before_bad_input(client, public_mode):
    # Without a key, a stranger learns nothing, not even which inputs are valid.
    assert client.get("/v1/players", params={"limit": 0}).status_code == 401
    assert client.get("/v1/players/abc").status_code == 401
    # With the key, bad input is a 422 as usual.
    headers = {"X-API-Key": TEST_KEY}
    assert client.get("/v1/players", params={"limit": 0}, headers=headers).status_code == 422
    assert client.get("/v1/players/abc", headers=headers).status_code == 422
    assert client.get("/v1/players/99", headers=headers).status_code == 404


@pytest.mark.parametrize("path", PUBLIC_PATHS)
def test_public_endpoints_stay_open_in_public_mode(client, public_mode, path):
    assert client.get(path).status_code == 200


# --- a snapshot without raw tables ---


@pytest.fixture
def snapshot_client(tmp_path):
    """A database like the public snapshot: players, but no player_season_stats."""
    conn = connect(tmp_path / "snapshot.db")
    conn.execute("CREATE TABLE players (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    conn.execute("INSERT INTO players VALUES (10, 'Alpha Guard')")
    app.dependency_overrides[get_db] = lambda: conn
    yield TestClient(app)
    app.dependency_overrides.clear()
    conn.close()


@pytest.mark.parametrize("path", RAW_PATHS)
def test_raw_endpoints_say_unavailable_when_tables_are_missing(snapshot_client, public_mode, path):
    response = snapshot_client.get(path, headers={"X-API-Key": TEST_KEY})
    assert response.status_code == 503
    assert response.json() == {"detail": "Raw stats are not available in this deployment"}


def test_missing_tables_still_hidden_behind_the_key(snapshot_client, public_mode):
    assert snapshot_client.get("/v1/players").status_code == 401
