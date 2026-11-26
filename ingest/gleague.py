"""Pull G League Ignite per-game player logs from NBA.com into CSVs.

Run from the project root:  python -m ingest.gleague
Ignite was the G League's prospect team (2020-21 to 2023-24). Player ids are NBA.com person ids,
the same as prospects.nba_person_id. Files are cached in data/raw/gleague/.
"""

import argparse
import time
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import playergamelogs

from ingest.nba import REQUEST_DELAY_SECONDS

RAW_DIR = Path("data/raw/gleague")
G_LEAGUE_ID = "20"
IGNITE_TEAM_ID = 1612709930
# Known gap: NBA.com has no Ignite stats for 2021-22 (the team mostly played exhibitions that year),
# so Dyson Daniels, Jaden Hardy and MarJon Beauchamp have no pre-draft games from this source.
IGNITE_SEASONS = ["2020-21", "2021-22", "2022-23", "2023-24"]
SEASON_TYPES = {"Regular Season": "regular", "Playoffs": "playoffs"}


def logs_path(season: str, season_type: str, raw_dir: Path = RAW_DIR) -> Path:
    return raw_dir / f"ignite_logs_{season}_{SEASON_TYPES[season_type]}.csv"


def fetch_logs(season: str, season_type: str) -> pd.DataFrame:
    time.sleep(REQUEST_DELAY_SECONDS)
    response = playergamelogs.PlayerGameLogs(
        league_id_nullable=G_LEAGUE_ID,
        season_nullable=season,
        season_type_nullable=season_type,
        team_id_nullable=IGNITE_TEAM_ID,
        timeout=60,
    )
    return response.get_data_frames()[0]


def pull_logs(season: str, season_type: str, raw_dir: Path = RAW_DIR, refresh: bool = False) -> Path:
    """Cache one season's logs. An empty result is saved too: Ignite missed most playoffs."""
    path = logs_path(season, season_type, raw_dir)
    if path.exists() and not refresh:
        print(f"Using cached {path}")
        return path
    df = fetch_logs(season, season_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    print(f"Saved {len(df)} player games to {path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Pull again even if cached")
    args = parser.parse_args()
    for season in IGNITE_SEASONS:
        for season_type in SEASON_TYPES:
            pull_logs(season, season_type, refresh=args.refresh)


if __name__ == "__main__":
    main()
