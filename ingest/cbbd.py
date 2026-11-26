"""Pull college data from the CollegeBasketballData.com (CBBD) API into cached JSON files.

Run from the project root:  python -m ingest.cbbd 2011 --to 2026
Per-game box scores too:    python -m ingest.cbbd 2019 --games
Seasons use the ending year (2026 = the 2025-26 season).

The free key allows 1,000 calls a month, so every response is cached in data/raw/cbbd/
and never requested twice. Needs CBBD_API_KEY in .env (never committed).
CBBD terms: data may be cached privately and used for derived analysis, but not
republished as a raw dataset or mirror.
"""

import argparse
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import requests

BASE_URL = "https://api.collegebasketballdata.com"
RAW_DIR = Path("data/raw/cbbd")
ENV_FILE = Path(".env")
REQUEST_DELAY_SECONDS = 1.0


def api_key(env_file: Path = ENV_FILE) -> str:
    """Read CBBD_API_KEY from the environment, or from .env."""
    if os.environ.get("CBBD_API_KEY"):
        return os.environ["CBBD_API_KEY"]
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "CBBD_API_KEY" and value.strip():
                return value.strip().strip('"').strip("'")
    raise RuntimeError("CBBD_API_KEY is missing. Add it to .env")


def get_json(endpoint: str, params: dict, cache_file: Path, refresh: bool = False):
    """GET an API endpoint, using the cached response if there is one. Costs one call on a miss."""
    if cache_file.exists() and not refresh:
        return json.loads(cache_file.read_text())
    time.sleep(REQUEST_DELAY_SECONDS)
    response = requests.get(
        f"{BASE_URL}{endpoint}",
        params=params,
        headers={"Authorization": f"Bearer {api_key()}"},
        timeout=120,
    )
    if response.status_code == 429:
        raise RuntimeError("CBBD rate limit hit (monthly quota or too many requests). Try again later.")
    response.raise_for_status()
    data = response.json()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data))
    return data


def player_season_stats(season: int, raw_dir: Path = RAW_DIR, refresh: bool = False) -> list[dict]:
    return get_json("/stats/player/season", {"season": season, "seasonType": "regular"},
                    raw_dir / f"player_season_stats_{season}.json", refresh)


def draft_picks(raw_dir: Path = RAW_DIR, refresh: bool = False) -> list[dict]:
    return get_json("/draft/picks", {}, raw_dir / "draft_picks.json", refresh)


# /games/players returns at most 1,000 rows per request. Each row is one TEAM in one game, so the
# real cap is 500 games (the docs say "1,000 games"; the 2018-19 pull showed otherwise).
ROWS_PER_CALL_LIMIT = 1000
WINDOW_DAYS = 10  # most 10-day windows fit; busy ones are split automatically


def season_windows(season: int, days: int = WINDOW_DAYS) -> list[tuple[date, date]]:
    """Date windows covering a season: Nov 1 of the starting year to Apr 15 of the ending year."""
    start, last = date(season - 1, 11, 1), date(season, 4, 15)
    windows = []
    while start <= last:
        end = min(start + timedelta(days=days - 1), last)
        windows.append((start, end))
        start = end + timedelta(days=1)
    return windows


def game_boxscores_window(start: date, end: date, raw_dir: Path = RAW_DIR, refresh: bool = False) -> list[dict]:
    """Per-game player box scores for every game starting between two dates (inclusive, UTC).

    If a window hits the per-call limit (so some games were cut off), it is split in half and
    each half is pulled instead. That costs extra calls but never silently loses games.
    """
    cache = raw_dir / "games" / f"players_{start.isoformat()}_{end.isoformat()}.json"
    rows = get_json(
        "/games/players",
        {"startDateRange": f"{start.isoformat()}T00:00:00Z", "endDateRange": f"{end.isoformat()}T23:59:59Z"},
        cache,
        refresh,
    )
    if len(rows) < ROWS_PER_CALL_LIMIT:
        return rows
    if start == end:
        raise RuntimeError(f"More than {ROWS_PER_CALL_LIMIT // 2} games on {start}; cannot split further")
    cache.unlink()  # truncated, so don't keep it
    middle = start + (end - start) // 2
    return (game_boxscores_window(start, middle, raw_dir, refresh)
            + game_boxscores_window(middle + timedelta(days=1), end, raw_dir, refresh))


def game_boxscores(season: int, raw_dir: Path = RAW_DIR, refresh: bool = False) -> int:
    """Pull (or read from cache) every window of a season. Returns the number of games."""
    game_ids = set()
    for start, end in season_windows(season):
        game_ids |= {r["gameId"] for r in game_boxscores_window(start, end, raw_dir, refresh)}
    return len(game_ids)


def calls_remaining() -> int:
    """Free: the /info endpoint does not count against the quota."""
    response = requests.get(f"{BASE_URL}/info", headers={"Authorization": f"Bearer {api_key()}"}, timeout=30)
    response.raise_for_status()
    return response.json()["remainingCalls"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("season", type=int, help="Season's ending year, like 2026 (the first one if --to is given)")
    parser.add_argument("--to", type=int, help="Last season of a range")
    parser.add_argument("--refresh", action="store_true", help="Pull again even if cached (uses calls)")
    parser.add_argument("--games", action="store_true",
                        help=f"Also pull per-game box scores (about {len(season_windows(2026))} calls per season)")
    parser.add_argument("--reserve", type=int, default=0,
                        help="Stop before a season if it could leave fewer than this many calls")
    args = parser.parse_args()

    print(f"Calls remaining before: {calls_remaining()}")
    print(f"Draft picks: {len(draft_picks(refresh=args.refresh))}")
    worst_case = 2 * len(season_windows(2026)) + 1  # every window split once, plus season stats
    for season in range(args.season, (args.to or args.season) + 1):
        if args.reserve and calls_remaining() - worst_case < args.reserve:
            print(f"Stopping before {season}: it could take the balance below the {args.reserve}-call reserve")
            break
        stats = player_season_stats(season, refresh=args.refresh)
        print(f"{season}: {len(stats)} player stat lines")
        if args.games:
            print(f"{season}: {game_boxscores(season, refresh=args.refresh)} games with box scores", flush=True)
    print(f"Calls remaining after: {calls_remaining()}")


if __name__ == "__main__":
    main()
