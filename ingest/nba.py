"""Pull one NBA season of per-game player stats from NBA.com into a CSV.

Run from the project root:  python -m ingest.nba 2025-26
A range of seasons:         python -m ingest.nba 2010-11 --to 2025-26
Each file lands in data/raw/ and is reused on later runs (use --refresh to pull again).
"""

import argparse
import time
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import leaguedashplayerstats

RAW_DIR = Path("data/raw")
REQUEST_DELAY_SECONDS = 1.0  # be polite to NBA.com between requests


def csv_path(season: str, raw_dir: Path = RAW_DIR) -> Path:
    return raw_dir / f"nba_player_stats_{season}.csv"


def fetch_season(season: str) -> pd.DataFrame:
    """One request to NBA.com: every player's per-game regular-season stats."""
    time.sleep(REQUEST_DELAY_SECONDS)
    response = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season,
        season_type_all_star="Regular Season",
        per_mode_detailed="PerGame",
        timeout=60,
    )
    return response.get_data_frames()[0]


def pull_season(season: str, raw_dir: Path = RAW_DIR, refresh: bool = False) -> Path:
    """Save the season to CSV, skipping the request if we already have it."""
    path = csv_path(season, raw_dir)
    if path.exists() and not refresh:
        print(f"Using cached {path}")
        return path

    df = fetch_season(season)
    if df.empty:
        raise RuntimeError(f"NBA.com returned no rows for season {season}")

    raw_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    print(f"Saved {len(df)} players to {path}")
    return path


def season_range(first: str, last: str) -> list[str]:
    """season_range("2010-11", "2012-13") -> ["2010-11", "2011-12", "2012-13"]"""
    start, end = int(first[:4]), int(last[:4])
    if end < start:
        raise ValueError(f"{last} comes before {first}")
    return [f"{year}-{str(year + 1)[-2:]}" for year in range(start, end + 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("season", help='Season like "2025-26" (the first one if --to is given)')
    parser.add_argument("--to", help='Last season of a range, like "2025-26"')
    parser.add_argument("--refresh", action="store_true", help="Pull again even if cached")
    args = parser.parse_args()
    for season in season_range(args.season, args.to or args.season):
        pull_season(season, refresh=args.refresh)


if __name__ == "__main__":
    main()
