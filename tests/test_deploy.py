"""Public-mode safety: the database is opened read-only, and only a public snapshot can be served."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import check_public_snapshot, connect, connect_readonly, get_db, init_schema
from app.main import app
from scripts.build_public_db import build_public_db


@pytest.fixture
def full_db(tmp_path):
    """A database with the full schema, like data/upside.db."""
    path = tmp_path / "upside.db"
    conn = connect(path)
    init_schema(conn)
    conn.execute("INSERT INTO prospects (id, nba_person_id, name) VALUES (1, 500, 'Some Prospect')")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def snapshot_db(full_db, tmp_path):
    path = tmp_path / "public.db"
    source = connect(full_db)
    build_public_db(source, path, set())
    source.close()
    return path


@pytest.fixture
def public_env(monkeypatch):
    monkeypatch.setenv("PUBLIC_MODE", "1")
    monkeypatch.setenv("UPSIDE_API_KEY", "test-key")


def test_readonly_missing_file_is_an_error_and_creates_nothing(tmp_path):
    missing = tmp_path / "nope.db"
    with pytest.raises(FileNotFoundError):
        connect_readonly(missing)
    assert not missing.exists()


def test_readonly_connection_cannot_write(snapshot_db):
    conn = connect_readonly(snapshot_db)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("INSERT INTO players VALUES (1, 'Someone')")
    conn.close()


def test_snapshot_check_accepts_a_snapshot(snapshot_db):
    conn = connect_readonly(snapshot_db)
    check_public_snapshot(conn)
    conn.close()


def test_snapshot_check_rejects_the_full_database(full_db):
    conn = connect_readonly(full_db)
    with pytest.raises(RuntimeError, match="player_season_stats"):
        check_public_snapshot(conn)
    conn.close()


def test_get_db_is_readonly_in_public_mode(snapshot_db, public_env, monkeypatch):
    monkeypatch.setenv("UPSIDE_DB_PATH", str(snapshot_db))
    dependency = get_db()  # keep the generator alive, or it closes the connection
    conn = next(dependency)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM players")
    dependency.close()


def test_public_server_refuses_to_start_on_the_full_database(full_db, public_env, monkeypatch):
    monkeypatch.setenv("UPSIDE_DB_PATH", str(full_db))
    with pytest.raises(RuntimeError, match="Not a public snapshot"):
        with TestClient(app):
            pass


def test_public_server_refuses_to_start_without_a_database(tmp_path, public_env, monkeypatch):
    monkeypatch.setenv("UPSIDE_DB_PATH", str(tmp_path / "missing.db"))
    with pytest.raises(FileNotFoundError):
        with TestClient(app):
            pass


def test_public_server_runs_on_the_snapshot(snapshot_db, public_env, monkeypatch):
    monkeypatch.setenv("UPSIDE_DB_PATH", str(snapshot_db))
    with TestClient(app) as client:
        assert client.get("/v1/prospects").json()["items"][0]["name"] == "Some Prospect"
        assert client.get("/v1/players").status_code == 401
        assert client.get("/v1/players/1/seasons", headers={"X-API-Key": "test-key"}).status_code == 503
        assert client.get("/docs").status_code == 200


def test_local_mode_starts_on_the_full_database(full_db, monkeypatch):
    monkeypatch.delenv("PUBLIC_MODE", raising=False)
    monkeypatch.setenv("UPSIDE_DB_PATH", str(full_db))
    with TestClient(app) as client:
        assert client.get("/v1/prospects").status_code == 200
