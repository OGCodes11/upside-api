import pandas as pd
import pytest

from app.db import connect
from ingest import load_nba, nba


def write_season_csv(raw_dir, season, rows):
    columns = ["PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "TEAM_COUNT", *load_nba.STAT_COLUMNS]
    df = pd.DataFrame(rows, columns=columns[: len(rows[0])]).reindex(columns=columns)
    raw_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(nba.csv_path(season, raw_dir), index=False)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "load.db")
    yield c
    c.close()


GSW = 1610612744  # real NBA.com id, so it matches the teams nba_api provides


def test_load_seasons_inserts_rows(tmp_path, conn):
    # PLAYER_ID, NAME, TEAM_ID, TEAM_COUNT, AGE, GP, MIN, PTS
    write_season_csv(tmp_path, "2025-26", [(1, "Test Player", GSW, 1, 24, 70, 30.5, 21.0)])

    load_nba.load_seasons(["2025-26"], conn, raw_dir=tmp_path)

    assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 30
    row = conn.execute("SELECT * FROM player_season_stats").fetchone()
    assert (row["player_id"], row["season_id"], row["team_id"], row["gp"], row["pts"]) == (1, "2025-26", GSW, 70, 21.0)
    assert row["reb"] is None  # missing values become NULL, not NaN


def test_load_seasons_is_safe_to_rerun(tmp_path, conn):
    write_season_csv(tmp_path, "2025-26", [(1, "Old Name", GSW, 1, 24, 70, 30.5, 21.0)])
    load_nba.load_seasons(["2025-26"], conn, raw_dir=tmp_path)
    write_season_csv(tmp_path, "2025-26", [(1, "New Name", GSW, 1, 24, 72, 30.5, 22.0)])
    load_nba.load_seasons(["2025-26"], conn, raw_dir=tmp_path)

    assert conn.execute("SELECT COUNT(*) FROM player_season_stats").fetchone()[0] == 1
    assert conn.execute("SELECT name FROM players").fetchone()[0] == "New Name"
    assert tuple(conn.execute("SELECT gp, pts FROM player_season_stats").fetchone()) == (72, 22.0)


def test_load_seasons_missing_csv_loads_nothing(tmp_path, conn):
    write_season_csv(tmp_path, "2024-25", [(1, "Test Player", GSW, 1, 24, 70, 30.5, 21.0)])

    with pytest.raises(FileNotFoundError):
        load_nba.load_seasons(["2024-25", "2025-26"], conn, raw_dir=tmp_path)

    # the whole load is one transaction, so 2024-25 was rolled back too
    assert conn.execute("SELECT COUNT(*) FROM player_season_stats").fetchone()[0] == 0
