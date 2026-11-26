import json

import pytest
from fastapi.testclient import TestClient

from app.db import connect, get_db, init_schema
from app.main import app
from scoring.derived import build_player_progression

TEAMS = [(1, "GSW", "Golden State Warriors", "Golden State"), (2, "LAL", "Los Angeles Lakers", "Los Angeles")]
SEASONS = [("2020-21", 2020), ("2021-22", 2021), ("2023-24", 2023), ("2024-25", 2024), ("2025-26", 2025)]
PLAYERS = [(10, "Alpha Guard"), (11, "Bravo Forward"), (12, "Charlie Center"), (13, "Delta Wing")]
# player_id, season, team_id, team_count, gp, min, pts, reb, ast
STATS = [
    (10, "2024-25", 2, 1, 70, 30.0, 15.0, 3.0, 6.0),
    (10, "2025-26", 1, 1, 75, 32.0, 20.5, 4.0, 7.0),
    (11, "2025-26", 1, 2, 60, 25.0, 12.0, 6.0, 2.0),
    (12, "2025-26", 2, 1, 5, 8.0, 2.0, 3.0, 0.5),
    # Delta Wing: a traded short season, then a missed season (2022-23)
    (13, "2020-21", 1, 1, 60, 20.0, 8.0, 3.0, 2.0),
    (13, "2021-22", 2, 2, 15, 25.0, 12.0, 4.0, 3.0),
    (13, "2023-24", 1, 1, 70, 30.0, 18.0, 5.0, 4.0),
]
# id, nba_person_id, name, draft_year, draft_pick, school, conference, missing_box_flag, college_link
PROSPECTS = [
    (1, 10, "Alpha Guard", 2024, 3, "Duke", "ACC", 0, "name"),
    (2, 11, "Bravo Forward", 2024, 20, "Gonzaga", "WCC", 1, "cbbd_draft"),
    (3, 900, "Unscored Invitee", None, None, None, None, None, None),
    (4, 901, "Older Pick", 2013, 40, "Kansas", "Big 12", 1, "name_school"),
]


def score_row(prospect_id, psi, rank, class_size, physical_source="combine", postseason_games=2):
    return (
        prospect_id, "v1-neutral-hand-inputs", psi, rank, class_size,
        60.0, 62.0, 50.0, 55.0 if physical_source == "combine" else 50.0, 50.0, 72.0,
        json.dumps({"scoring": 70.0, "playmaking": 60.0, "defense": 55.0, "shooting": 65.0, "iq": 58.0,
                    "athleticism": 61.0}),
        json.dumps(["BPM"]), json.dumps([]), "SG", "combine", physical_source, "nba", "NCAA_P5", 2024,
        postseason_games, int(postseason_games < 5), 24.1, 20.3, 0.58, 0.51, "2026-11-23T00:00:00+00:00",
    )


SCORES = [score_row(1, 58.2, 1, 2), score_row(2, 54.0, 2, 2, physical_source="none", postseason_games=6),
          score_row(4, 51.5, 1, 1)]


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "test.db")
    init_schema(conn)
    with conn:
        conn.executemany("INSERT INTO teams VALUES (?, ?, ?, ?)", TEAMS)
        conn.executemany("INSERT INTO seasons VALUES (?, ?)", SEASONS)
        conn.executemany("INSERT INTO players VALUES (?, ?)", PLAYERS)
        conn.executemany(
            "INSERT INTO player_season_stats (player_id, season_id, team_id, team_count, gp, min, pts, reb, ast)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            STATS,
        )
        conn.executemany(
            "INSERT INTO prospects (id, nba_person_id, name, draft_year, draft_pick, school, conference,"
            " missing_box_flag, college_link) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            PROSPECTS,
        )
        conn.executemany(f"INSERT INTO prospect_scores VALUES ({', '.join('?' * 27)})", SCORES)
        build_player_progression(conn, conn)
    yield conn
    conn.close()


@pytest.fixture
def client(db):
    """API client that reads the small test database instead of data/upside.db."""
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()
