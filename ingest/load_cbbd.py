"""Load cached CBBD college stats into the database and link prospects to their college careers.

Run from the project root:  python -m ingest.load_cbbd 2006 --to 2026
Needs the JSON cached by ingest/cbbd.py; this script makes no API calls.
Safe to run again: rows are updated, and prospect links are recomputed each time.

How a prospect is linked to a college player (stored in prospects.college_link):
  cbbd_draft          CBBD's own draft record names his college id (same draft year and pick)
  name                exactly one D1 player with his name played in the 5 seasons up to his draft/combine
  name_school         several did; only one played for his school
  name_latest_season  several did and the school didn't settle it; he is the one with the latest final season
  first_last_school   no exact name match (CBBD added a middle name); one player at his school with
                      the same first and last name
Prospects with no D1 match (international, G League, never played) or a tie stay unlinked.
"""

import argparse
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

from app.db import connect, db_path, init_schema
from ingest.cbbd import RAW_DIR

SEASONS_BACK = 5  # how many seasons before the draft to look for his college career

UPSERT_SEASON = """
    INSERT INTO college_player_seasons (
        athlete_id, season, team_id, team, conference, name, position, games, starts, minutes,
        points, fgm, fga, fg3m, fg3a, ftm, fta, oreb, dreb, reb, ast, stl, blk, tov, pf,
        usage, ortg, drtg, porpag, ts_pct, efg_pct, win_shares)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (athlete_id, season, team_id) DO UPDATE SET
        team = excluded.team, conference = excluded.conference, name = excluded.name,
        position = excluded.position, games = excluded.games, starts = excluded.starts,
        minutes = excluded.minutes, points = excluded.points, fgm = excluded.fgm, fga = excluded.fga,
        fg3m = excluded.fg3m, fg3a = excluded.fg3a, ftm = excluded.ftm, fta = excluded.fta,
        oreb = excluded.oreb, dreb = excluded.dreb, reb = excluded.reb, ast = excluded.ast,
        stl = excluded.stl, blk = excluded.blk, tov = excluded.tov, pf = excluded.pf,
        usage = excluded.usage, ortg = excluded.ortg, drtg = excluded.drtg, porpag = excluded.porpag,
        ts_pct = excluded.ts_pct, efg_pct = excluded.efg_pct, win_shares = excluded.win_shares
"""


def normalize_name(name: str) -> str:
    """'Andre Jackson Jr.' and 'André Jackson' both become 'andre jackson'."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"[.'’]", "", name).replace("-", " ")
    name = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name)
    return " ".join(name.split())


def normalize_school(school: str) -> str:
    """Lowercase, no punctuation, no leading 'the' or 'university of'."""
    school = unicodedata.normalize("NFKD", school).encode("ascii", "ignore").decode().lower()
    school = re.sub(r"[^a-z0-9 ]", " ", school)
    school = re.sub(r"^(the |university of )", "", " ".join(school.split()))
    return school.strip()


def first_last(name: str) -> tuple[str, str]:
    """'Robbie John Hummel' -> ('robbie', 'hummel')"""
    parts = normalize_name(name).split()
    return (parts[0], parts[-1]) if parts else ("", "")


def school_matches(school: str, team: str, aliases: set[tuple[str, str]]) -> bool:
    """Does NBA.com's school name (e.g. 'California-Los Angeles') mean this CBBD team ('UCLA')?"""
    target, team = normalize_school(school), normalize_school(team)
    return target == team or (target, team) in aliases


class Season(NamedTuple):
    athlete_id: int
    season: int
    conference: str
    team: str
    games: int = 0


def match_by_name(window: list[Season], school: str | None, aliases: set[tuple[str, str]]):
    """Pick one athlete from the seasons that match a prospect's name. Returns (athlete_id, method).

    method is 'name' (only one athlete), 'name_school' (only one played for his school),
    'name_latest_season' (only one has the latest final season), 'tied', or None (no match).
    """
    last_season = defaultdict(int)
    for s in window:
        last_season[s.athlete_id] = max(last_season[s.athlete_id], s.season)
    if not last_season:
        return None, None
    if len(last_season) == 1:
        return next(iter(last_season)), "name"

    if school:
        at_school = {s.athlete_id for s in window if school_matches(school, s.team, aliases)}
        if len(at_school) == 1:
            return at_school.pop(), "name_school"

    latest = max(last_season.values())
    leaders = [athlete_id for athlete_id, s in last_season.items() if s == latest]
    if len(leaders) == 1:
        return leaders[0], "name_latest_season"
    return None, "tied"


def _part(stat: dict | None, key: str):
    return (stat or {}).get(key)


def season_row(r: dict) -> tuple:
    return (
        r["athleteId"], r["season"], r["teamId"], r["team"], r["conference"], r["name"],
        r.get("position") or None, r.get("games"), r.get("starts"), r.get("minutes"), r.get("points"),
        _part(r.get("fieldGoals"), "made"), _part(r.get("fieldGoals"), "attempted"),
        _part(r.get("threePointFieldGoals"), "made"), _part(r.get("threePointFieldGoals"), "attempted"),
        _part(r.get("freeThrows"), "made"), _part(r.get("freeThrows"), "attempted"),
        _part(r.get("rebounds"), "offensive"), _part(r.get("rebounds"), "defensive"),
        _part(r.get("rebounds"), "total"),
        r.get("assists"), r.get("steals"), r.get("blocks"), r.get("turnovers"), r.get("fouls"),
        r.get("usage"), r.get("offensiveRating"), r.get("defensiveRating"), r.get("PORPAG"),
        r.get("trueShootingPct"), r.get("effectiveFieldGoalPct"), _part(r.get("winShares"), "total"),
    )


def load_college_seasons(conn: sqlite3.Connection, seasons: range, raw_dir: Path = RAW_DIR) -> None:
    for season in seasons:
        path = raw_dir / f"player_season_stats_{season}.json"
        if not path.exists():
            raise FileNotFoundError(f"{path} is missing. Run: python -m ingest.cbbd {season}")
        rows = [season_row(r) for r in json.loads(path.read_text()) if r.get("conference")]  # D1 only
        conn.executemany(UPSERT_SEASON, rows)
        print(f"College {season}: {len(rows)} D1 player seasons")


def _draft_ids(raw_dir: Path) -> dict[tuple[int, int], tuple[int, str]]:
    """(draft year, overall pick) -> (CBBD athlete id, name) from CBBD's draft records."""
    path = raw_dir / "draft_picks.json"
    if not path.exists():
        return {}
    return {
        (p["year"], p["overall"]): (p["athleteId"], p["name"])
        for p in json.loads(path.read_text())
        if p.get("athleteId")
    }


def link_prospects(conn: sqlite3.Connection, raw_dir: Path = RAW_DIR) -> dict[str, int]:
    """Match every prospect to a college athlete id, recomputed from scratch each run."""
    careers = defaultdict(list)  # normalized name -> [Season]
    careers_first_last = defaultdict(list)  # (first, last) -> [Season], for names with a middle name
    by_id = defaultdict(list)  # athlete id -> [Season]
    for row in conn.execute(
        "SELECT athlete_id, season, conference, team, COALESCE(games, 0), name FROM college_player_seasons"
    ):
        season = Season(*row[:5])
        careers[normalize_name(row[5])].append(season)
        careers_first_last[first_last(row[5])].append(season)
        by_id[season.athlete_id].append(season)
    draft_ids = _draft_ids(raw_dir)

    prospects = conn.execute(
        """SELECT p.id, p.name, p.school, p.draft_year, p.draft_pick,
                  (SELECT MAX(combine_year) FROM combine c WHERE c.prospect_id = p.id)
           FROM prospects p"""
    ).fetchall()

    # Pass 1: CBBD's own draft records. These also teach us how NBA.com spells each school
    # compared with CBBD ("California-Los Angeles" vs "UCLA"), for breaking name ties in pass 2.
    links = {}
    aliases = set()
    for prospect_id, name, school, draft_year, draft_pick, _ in prospects:
        drafted = draft_ids.get((draft_year, draft_pick))
        if drafted and drafted[0] in by_id and normalize_name(drafted[1]) == normalize_name(name):
            links[prospect_id] = (drafted[0], "cbbd_draft")
            if school:
                aliases |= {(normalize_school(school), normalize_school(s.team)) for s in by_id[drafted[0]]}

    # Pass 2: everyone else, by name.
    counts = defaultdict(int)
    updates = []
    for prospect_id, name, school, draft_year, draft_pick, combine_year in prospects:
        year = draft_year or combine_year
        athlete_id, method = links.get(prospect_id, (None, None))
        if athlete_id is None and year is not None:
            in_window = lambda seasons: [s for s in seasons if year - SEASONS_BACK <= s.season <= year]  # noqa: E731
            athlete_id, method = match_by_name(in_window(careers.get(normalize_name(name), [])), school, aliases)
            if method is None and school:
                # Last resort for "Robbie Hummel" vs "Robbie John Hummel": first and last name,
                # and only if exactly one such player was at his school.
                window = in_window(careers_first_last.get(first_last(name), []))
                at_school = {s.athlete_id for s in window if school_matches(school, s.team, aliases)}
                if len(at_school) == 1:
                    athlete_id, method = at_school.pop(), "first_last_school"

        conference = None
        counts[method or "unlinked"] += 1
        if athlete_id is None:
            method = None  # a tie is counted, but stored as unlinked
        else:
            in_range = [s for s in by_id[athlete_id] if year is None or s.season <= year]
            # His last season, and in it the team he played the most games for. CBBD sometimes adds a
            # stray 1-game row crediting a player to the opponent (Markelle Fultz on TCU in 2017).
            conference = max(in_range, key=lambda s: (s.season, s.games)).conference if in_range else None
        updates.append((athlete_id, method, conference, prospect_id))

    conn.executemany(
        "UPDATE prospects SET cbbd_athlete_id = ?, college_link = ?, conference = ? WHERE id = ?", updates
    )
    return dict(counts)


def load_cbbd(seasons: range, conn: sqlite3.Connection, raw_dir: Path = RAW_DIR) -> dict[str, int]:
    init_schema(conn)
    with conn:  # one transaction: everything loads, or nothing does
        load_college_seasons(conn, seasons, raw_dir)
        counts = link_prospects(conn, raw_dir)
    print(f"Prospect links: {counts}")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("season", type=int, help="Season's ending year, like 2026 (the first one if --to is given)")
    parser.add_argument("--to", type=int, help="Last season of a range")
    args = parser.parse_args()
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        load_cbbd(range(args.season, (args.to or args.season) + 1), conn)
    finally:
        conn.close()
    print(f"Database: {path}")


if __name__ == "__main__":
    main()
