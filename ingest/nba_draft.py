"""Pull NBA Draft Combine results and NBA draft history from NBA.com into CSVs.

Run from the project root:  python -m ingest.nba_draft 2010 --to 2026
Years are draft years (2026 = the June 2026 draft and the May 2026 combine).
Files land in data/raw/ and are reused on later runs (use --refresh to pull again).
"""

import argparse
import time
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import draftcombinestats, drafthistory

from ingest.nba import RAW_DIR, REQUEST_DELAY_SECONDS


def combine_path(year: int, raw_dir: Path = RAW_DIR) -> Path:
    return raw_dir / f"nba_combine_{year}.csv"


def draft_history_path(raw_dir: Path = RAW_DIR) -> Path:
    return raw_dir / "nba_draft_history.csv"


def fetch_combine(year: int) -> pd.DataFrame:
    """One request: every combine participant's measurements and drill results for one year."""
    time.sleep(REQUEST_DELAY_SECONDS)
    label = f"{year}-{str(year + 1)[-2:]}"  # NBA.com files the 2026 combine under "2026-27"
    response = draftcombinestats.DraftCombineStats(season_all_time=label, league_id="00", timeout=60)
    return response.get_data_frames()[0]


def fetch_draft_history() -> pd.DataFrame:
    """One request: every NBA draft pick ever made."""
    time.sleep(REQUEST_DELAY_SECONDS)
    return drafthistory.DraftHistory(league_id="00", timeout=60).get_data_frames()[0]


def _save(df: pd.DataFrame, path: Path, what: str) -> Path:
    if df.empty:
        raise RuntimeError(f"NBA.com returned no rows for {what}")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    print(f"Saved {len(df)} rows to {path}")
    return path


def pull_combine(year: int, raw_dir: Path = RAW_DIR, refresh: bool = False) -> Path:
    path = combine_path(year, raw_dir)
    if path.exists() and not refresh:
        print(f"Using cached {path}")
        return path
    return _save(fetch_combine(year), path, f"the {year} combine")


def pull_draft_history(raw_dir: Path = RAW_DIR, refresh: bool = False) -> Path:
    path = draft_history_path(raw_dir)
    if path.exists() and not refresh:
        print(f"Using cached {path}")
        return path
    return _save(fetch_draft_history(), path, "draft history")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("year", type=int, help="Draft year, like 2026 (the first one if --to is given)")
    parser.add_argument("--to", type=int, help="Last draft year of a range")
    parser.add_argument("--refresh", action="store_true", help="Pull again even if cached")
    args = parser.parse_args()
    pull_draft_history(refresh=args.refresh)
    for year in range(args.year, (args.to or args.year) + 1):
        pull_combine(year, refresh=args.refresh)


if __name__ == "__main__":
    main()
