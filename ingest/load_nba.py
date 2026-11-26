"""Load cached NBA season CSVs (from ingest/nba.py) into the SQLite database.

Run from the project root:  python -m ingest.load_nba 2010-11 --to 2025-26
Safe to run again: existing rows are updated, not duplicated.
"""

import argparse
import sqlite3
from pathlib import Path

import pandas as pd
from nba_api.stats.static import teams as static_teams

from app.db import connect, db_path, init_schema
from ingest.nba import RAW_DIR, csv_path, season_range

# CSV column -> player_season_stats column
STAT_COLUMNS = {
    "AGE": "age", "GP": "gp", "MIN": "min", "PTS": "pts", "REB": "reb", "OREB": "oreb",
    "DREB": "dreb", "AST": "ast", "STL": "stl", "BLK": "blk", "TOV": "tov", "PF": "pf",
    "FGM": "fgm", "FGA": "fga", "FG3M": "fg3m", "FG3A": "fg3a", "FTM": "ftm", "FTA": "fta",
    "PLUS_MINUS": "plus_minus",
}

UPSERT_TEAM = """
    INSERT INTO teams (id, abbreviation, name, city) VALUES (?, ?, ?, ?)
    ON CONFLICT (id) DO UPDATE SET
        abbreviation = excluded.abbreviation, name = excluded.name, city = excluded.city
"""

UPSERT_SEASON = """
    INSERT INTO seasons (id, start_year) VALUES (?, ?)
    ON CONFLICT (id) DO NOTHING
"""

UPSERT_PLAYER = """
    INSERT INTO players (id, name) VALUES (?, ?)
    ON CONFLICT (id) DO UPDATE SET name = excluded.name
"""

_stat_names = list(STAT_COLUMNS.values())
UPSERT_STATS = f"""
    INSERT INTO player_season_stats (player_id, season_id, team_id, team_count, {", ".join(_stat_names)})
    VALUES (?, ?, ?, ?, {", ".join("?" for _ in _stat_names)})
    ON CONFLICT (player_id, season_id) DO UPDATE SET
        team_id = excluded.team_id, team_count = excluded.team_count,
        {", ".join(f"{c} = excluded.{c}" for c in _stat_names)}
"""


def load_teams(conn: sqlite3.Connection) -> None:
    rows = [(t["id"], t["abbreviation"], t["full_name"], t["city"]) for t in static_teams.get_teams()]
    conn.executemany(UPSERT_TEAM, rows)


def _none_if_missing(value):
    return None if pd.isna(value) else value.item() if hasattr(value, "item") else value


def load_season(conn: sqlite3.Connection, season: str, csv_file: Path) -> int:
    df = pd.read_csv(csv_file)
    conn.execute(UPSERT_SEASON, (season, int(season[:4])))
    conn.executemany(UPSERT_PLAYER, zip(df["PLAYER_ID"].astype(int), df["PLAYER_NAME"]))

    team_count = df["TEAM_COUNT"] if "TEAM_COUNT" in df else pd.Series(1, index=df.index)
    rows = []
    for i, r in df.iterrows():
        stats = [_none_if_missing(r[col]) for col in STAT_COLUMNS]
        rows.append((int(r["PLAYER_ID"]), season, int(r["TEAM_ID"]), int(team_count[i]), *stats))
    conn.executemany(UPSERT_STATS, rows)
    return len(rows)


def load_seasons(seasons: list[str], conn: sqlite3.Connection, raw_dir: Path = RAW_DIR) -> None:
    init_schema(conn)
    with conn:  # one transaction: all seasons load, or none do
        load_teams(conn)
        for season in seasons:
            path = csv_path(season, raw_dir)
            if not path.exists():
                raise FileNotFoundError(f"{path} is missing. Run: python -m ingest.nba {season}")
            print(f"{season}: {load_season(conn, season, path)} player rows")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("season", help='Season like "2025-26" (the first one if --to is given)')
    parser.add_argument("--to", help='Last season of a range, like "2025-26"')
    args = parser.parse_args()
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        load_seasons(season_range(args.season, args.to or args.season), conn)
    finally:
        conn.close()
    print(f"Database: {path}")


if __name__ == "__main__":
    main()
