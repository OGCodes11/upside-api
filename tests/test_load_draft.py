import pandas as pd
import pytest

from app.db import connect
from ingest import load_draft, nba_draft

GSW = 1610612744  # real NBA.com id, so it matches the teams nba_api provides


def write_history(raw_dir, rows):
    cols = ["PERSON_ID", "PLAYER_NAME", "SEASON", "ROUND_NUMBER", "OVERALL_PICK", "TEAM_ID",
            "ORGANIZATION", "ORGANIZATION_TYPE"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=cols).to_csv(nba_draft.draft_history_path(raw_dir), index=False)


def write_combine(raw_dir, year, rows):
    # PLAYER_ID, PLAYER_NAME, POSITION, HEIGHT_WO_SHOES, WINGSPAN, MAX_VERTICAL_LEAP
    cols = ["PLAYER_ID", "PLAYER_NAME", "POSITION", "HEIGHT_WO_SHOES", "WINGSPAN", "MAX_VERTICAL_LEAP"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=cols).to_csv(nba_draft.combine_path(year, raw_dir), index=False)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "draft.db")
    yield c
    c.close()


@pytest.fixture
def raw(tmp_path):
    raw_dir = tmp_path / "raw"
    write_history(raw_dir, [
        (1, "Top Pick", 2025, 1, 1, GSW, "Duke", "College/University"),
        (2, "Old Pick", 2009, 1, 5, GSW, "Kansas", "College/University"),  # outside the years loaded
        (3, "Club Pick", 2025, 2, 40, 999, "Some Club", "Other Team/Club"),  # unknown team id
    ])
    write_combine(raw_dir, 2024, [(4, "Came Back", "G", 75.0, 80.0, 30.0)])
    write_combine(raw_dir, 2025, [
        (1, "Top Pick", "F", 80.0, 85.5, None),  # skipped the vertical
        (4, "Came Back", "G", 75.5, 80.0, 32.5),  # second combine
    ])
    return raw_dir


def prospects(conn):
    return {r["name"]: dict(r) for r in conn.execute("SELECT * FROM prospects")}


def test_load_draft_combines_picks_and_invitees(conn, raw):
    load_draft.load_draft(range(2024, 2026), conn, raw_dir=raw)
    p = prospects(conn)

    assert set(p) == {"Top Pick", "Club Pick", "Came Back"}  # 2009 pick left out
    assert (p["Top Pick"]["draft_pick"], p["Top Pick"]["school"], p["Top Pick"]["position"]) == (1, "Duke", "F")
    assert p["Top Pick"]["draft_team_id"] == GSW
    assert p["Club Pick"]["draft_team_id"] is None  # unknown team becomes NULL, not a broken link
    assert p["Came Back"]["draft_year"] is None  # invitee who wasn't drafted


def test_load_draft_keeps_repeat_combine_visits(conn, raw):
    load_draft.load_draft(range(2024, 2026), conn, raw_dir=raw)
    rows = conn.execute(
        "SELECT c.combine_year, c.height_in, c.max_vert_in FROM combine c "
        "JOIN prospects p ON p.id = c.prospect_id WHERE p.name = 'Came Back' ORDER BY c.combine_year"
    ).fetchall()
    assert [tuple(r) for r in rows] == [(2024, 75.0, 30.0), (2025, 75.5, 32.5)]


def test_load_draft_skipped_test_is_null(conn, raw):
    load_draft.load_draft(range(2024, 2026), conn, raw_dir=raw)
    row = conn.execute(
        "SELECT c.wingspan_in, c.max_vert_in, c.lane_agility_s FROM combine c "
        "JOIN prospects p ON p.id = c.prospect_id WHERE p.name = 'Top Pick'"
    ).fetchone()
    assert tuple(row) == (85.5, None, None)  # lane agility column isn't in the CSV at all


def test_load_draft_is_safe_to_rerun(conn, raw):
    load_draft.load_draft(range(2024, 2026), conn, raw_dir=raw)
    load_draft.load_draft(range(2024, 2026), conn, raw_dir=raw)
    assert conn.execute("SELECT COUNT(*) FROM prospects").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM combine").fetchone()[0] == 3
    # combine reload must not wipe draft details
    assert prospects(conn)["Top Pick"]["draft_pick"] == 1


def test_load_draft_missing_combine_loads_nothing(conn, raw):
    with pytest.raises(FileNotFoundError):
        load_draft.load_draft(range(2024, 2027), conn, raw_dir=raw)  # no 2026 file
    assert conn.execute("SELECT COUNT(*) FROM prospects").fetchone()[0] == 0


def test_pull_combine_uses_cache(tmp_path, monkeypatch):
    calls = []
    fake = pd.DataFrame([{"PLAYER_ID": 1, "PLAYER_NAME": "X"}])
    monkeypatch.setattr(nba_draft, "fetch_combine", lambda year: calls.append(year) or fake)
    nba_draft.pull_combine(2026, raw_dir=tmp_path)
    nba_draft.pull_combine(2026, raw_dir=tmp_path)
    assert calls == [2026]


def test_pull_draft_history_rejects_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(nba_draft, "fetch_draft_history", lambda: pd.DataFrame())
    with pytest.raises(RuntimeError):
        nba_draft.pull_draft_history(raw_dir=tmp_path)
    assert not nba_draft.draft_history_path(tmp_path).exists()
