"""Load cached draft history and combine CSVs (from ingest/nba_draft.py) into the database.

Run from the project root:  python -m ingest.load_draft 2010 --to 2026
Prospects are everyone drafted in those years plus every combine invitee.
Safe to run again: existing rows are updated, not duplicated.
"""

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

from app.db import connect, db_path, init_schema
from ingest.load_nba import load_teams
from ingest.nba import RAW_DIR
from ingest.nba_draft import combine_path, draft_history_path

# CSV column -> combine column
COMBINE_COLUMNS = {
    "HEIGHT_WO_SHOES": "height_in", "HEIGHT_W_SHOES": "height_shoes_in", "WEIGHT": "weight_lb",
    "WINGSPAN": "wingspan_in", "STANDING_REACH": "standing_reach_in", "BODY_FAT_PCT": "body_fat_pct",
    "HAND_LENGTH": "hand_length_in", "HAND_WIDTH": "hand_width_in",
    "STANDING_VERTICAL_LEAP": "standing_vert_in", "MAX_VERTICAL_LEAP": "max_vert_in",
    "LANE_AGILITY_TIME": "lane_agility_s", "MODIFIED_LANE_AGILITY_TIME": "modified_lane_agility_s",
    "THREE_QUARTER_SPRINT": "sprint_s", "BENCH_PRESS": "bench_press_reps",
}

# Drafted players. A later run fills in draft details for someone first added from the combine.
UPSERT_DRAFTED = """
    INSERT INTO prospects (nba_person_id, name, school, school_type,
                           draft_year, draft_round, draft_pick, draft_team_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (nba_person_id) DO UPDATE SET
        name = excluded.name, school = excluded.school, school_type = excluded.school_type,
        draft_year = excluded.draft_year, draft_round = excluded.draft_round,
        draft_pick = excluded.draft_pick, draft_team_id = excluded.draft_team_id
"""

# Combine invitees. Only sets position; never touches draft details.
UPSERT_INVITEE = """
    INSERT INTO prospects (nba_person_id, name, position) VALUES (?, ?, ?)
    ON CONFLICT (nba_person_id) DO UPDATE SET position = excluded.position
"""

_combine_names = list(COMBINE_COLUMNS.values())
UPSERT_COMBINE = f"""
    INSERT INTO combine (prospect_id, combine_year, {", ".join(_combine_names)})
    SELECT id, ?, {", ".join("?" for _ in _combine_names)} FROM prospects WHERE nba_person_id = ?
    ON CONFLICT (prospect_id, combine_year) DO UPDATE SET
        {", ".join(f"{c} = excluded.{c}" for c in _combine_names)}
"""


def _clean(value):
    """pandas NaN -> None (SQL NULL); numpy numbers -> plain Python numbers."""
    if pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def load_draft_history(conn: sqlite3.Connection, csv_file: Path, years: range) -> int:
    df = pd.read_csv(csv_file)
    df = df[df["SEASON"].astype(int).isin(years) & df["PERSON_ID"].notna()]
    known_teams = {row[0] for row in conn.execute("SELECT id FROM teams")}
    rows = [
        (
            int(r["PERSON_ID"]), r["PLAYER_NAME"], _clean(r["ORGANIZATION"]), _clean(r["ORGANIZATION_TYPE"]),
            int(r["SEASON"]), int(r["ROUND_NUMBER"]), int(r["OVERALL_PICK"]),
            int(r["TEAM_ID"]) if r["TEAM_ID"] in known_teams else None,
        )
        for _, r in df.iterrows()
    ]
    conn.executemany(UPSERT_DRAFTED, rows)
    return len(rows)


def load_combine_year(conn: sqlite3.Connection, year: int, csv_file: Path) -> int:
    df = pd.read_csv(csv_file)
    conn.executemany(
        UPSERT_INVITEE,
        [(int(r["PLAYER_ID"]), r["PLAYER_NAME"], _clean(r["POSITION"])) for _, r in df.iterrows()],
    )
    rows = []
    for _, r in df.iterrows():
        measures = [_clean(r[col]) if col in df else None for col in COMBINE_COLUMNS]
        rows.append((year, *measures, int(r["PLAYER_ID"])))
    conn.executemany(UPSERT_COMBINE, rows)
    return len(rows)


def load_draft(years: range, conn: sqlite3.Connection, raw_dir: Path = RAW_DIR) -> None:
    init_schema(conn)
    with conn:  # one transaction: everything loads, or nothing does
        load_teams(conn)
        history = draft_history_path(raw_dir)
        if not history.exists():
            raise FileNotFoundError(f"{history} is missing. Run: python -m ingest.nba_draft {years.start}")
        print(f"Draft picks {years.start}-{years.stop - 1}: {load_draft_history(conn, history, years)}")
        for year in years:
            path = combine_path(year, raw_dir)
            if not path.exists():
                raise FileNotFoundError(f"{path} is missing. Run: python -m ingest.nba_draft {year}")
            print(f"Combine {year}: {load_combine_year(conn, year, path)} players")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("year", type=int, help="Draft year, like 2026 (the first one if --to is given)")
    parser.add_argument("--to", type=int, help="Last draft year of a range")
    args = parser.parse_args()
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        load_draft(range(args.year, (args.to or args.year) + 1), conn)
    finally:
        conn.close()
    print(f"Database: {path}")


if __name__ == "__main__":
    main()
