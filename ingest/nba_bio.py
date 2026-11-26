"""Pull birth dates from NBA.com (one request per player) and store them on prospects.

Run from the project root:  python -m ingest.nba_bio --drafted 2012 --to 2021
Each response is cached in data/raw/nba_bio/<person id>.json, so a rerun makes no requests
for players already pulled. One request per player with a delay, so a few hundred take a while.
"""

import argparse
import json
import time
from pathlib import Path

from nba_api.stats.endpoints import commonplayerinfo

from app.db import connect, db_path, init_schema
from ingest.nba import REQUEST_DELAY_SECONDS

RAW_DIR = Path("data/raw/nba_bio")


def player_info(person_id: int, raw_dir: Path = RAW_DIR) -> dict | None:
    """NBA.com's CommonPlayerInfo row for one person, or None if NBA.com has none."""
    cache = raw_dir / f"{person_id}.json"
    if cache.exists():
        return json.loads(cache.read_text()) or None
    time.sleep(REQUEST_DELAY_SECONDS)
    try:
        frames = commonplayerinfo.CommonPlayerInfo(player_id=person_id, timeout=60).get_data_frames()
    except (KeyError, ValueError) as error:
        # NBA.com answered with an error instead of data. Not cached, so a later run retries it.
        print(f"No data from NBA.com for person {person_id} ({type(error).__name__})", flush=True)
        return None
    rows = frames[0].to_dict("records") if frames and not frames[0].empty else []
    info = rows[0] if rows else {}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(info, default=str))  # an empty dict is cached too
    return info or None


def birth_date(info: dict | None) -> str | None:
    value = (info or {}).get("BIRTHDATE")
    return str(value)[:10] if value else None  # "1993-03-11T00:00:00" -> "1993-03-11"


def update_birth_dates(conn, first_year: int, last_year: int, raw_dir: Path = RAW_DIR) -> dict[str, int]:
    people = conn.execute(
        """SELECT id, nba_person_id FROM prospects
           WHERE draft_year BETWEEN ? AND ? AND cbbd_athlete_id IS NOT NULL ORDER BY draft_year, draft_pick""",
        (first_year, last_year),
    ).fetchall()
    counts = {"found": 0, "missing": 0}
    for i, (prospect_id, person_id) in enumerate(people, 1):
        born = birth_date(player_info(person_id, raw_dir))
        with conn:
            conn.execute("UPDATE prospects SET birth_date = ? WHERE id = ?", (born, prospect_id))
        counts["found" if born else "missing"] += 1
        if i % 50 == 0:
            print(f"{i}/{len(people)} players", flush=True)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drafted", type=int, required=True, help="First draft year")
    parser.add_argument("--to", type=int, help="Last draft year")
    args = parser.parse_args()
    conn = connect(db_path())
    init_schema(conn)
    try:
        print(update_birth_dates(conn, args.drafted, args.to or args.drafted))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
