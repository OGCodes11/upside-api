"""Build data/public.db: the snapshot a public deployment serves.

It holds only what the public endpoints need: prospect names and flags, PSI scores, player names and
year-over-year changes. No raw NBA.com or CBBD stat lines and no game data.

Players from excluded college programs are left out entirely. The programs are listed one per line in a
private file, data/excluded_teams.txt by default, which is never committed. If that file is missing the
build stops, so a forgotten setting can't publish everyone; pass --no-exclusions to build without one.

    python -m scripts.build_public_db                 # data/upside.db -> data/public.db
    python -m scripts.build_public_db --out other.db
    python -m scripts.build_public_db --exclusions path/to/teams.txt

The source database is opened read-only. The snapshot is built in a temporary file and only replaces
the output once it has passed the table check.
"""

import argparse
import re
import sqlite3
from pathlib import Path

from app.db import SCHEMA_PATH, SNAPSHOT_TABLES
from scoring.derived import build_player_progression, build_prospect_scores

# The only tables a snapshot may contain (shared with the server's startup check). Anything else fails the build.
ALLOWED_TABLES = SNAPSHOT_TABLES

EXCLUSIONS_PATH = Path("data/excluded_teams.txt")

SNAPSHOT_TABLES = """
CREATE TABLE players (
    id    INTEGER PRIMARY KEY,                  -- NBA.com player id
    name  TEXT    NOT NULL
);

-- Prospects without birth dates, college ids or school types: just what /v1/prospects lists.
CREATE TABLE prospects (
    id                INTEGER PRIMARY KEY,
    name              TEXT    NOT NULL,
    draft_year        INTEGER,
    draft_pick        INTEGER,
    school            TEXT,
    conference        TEXT,
    missing_box_flag  INTEGER,
    college_link      TEXT
);
"""


def open_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def derived_table_sql() -> list[str]:
    """CREATE statements for prospect_scores and player_progression, taken from app/schema.sql."""
    scratch = sqlite3.connect(":memory:")
    scratch.executescript(SCHEMA_PATH.read_text())
    rows = scratch.execute("SELECT sql FROM sqlite_master WHERE type = 'table'"
                           " AND name IN ('prospect_scores', 'player_progression') ORDER BY name").fetchall()
    scratch.close()
    return [r[0] for r in rows]


def normalize_name(name: str) -> str:
    """'Jr.', punctuation and case don't count: 'A.J. Smith Jr.' -> 'aj smith'."""
    words = re.sub(r"[^a-z ]", "", name.lower().replace("-", " ")).split()
    return " ".join(w for w in words if w not in {"jr", "sr", "ii", "iii", "iv"})


def read_excluded_teams(path: Path) -> set[str]:
    """One program per line, as named in the college data and draft records. Blank lines and # comments skipped."""
    lines = (line.split("#", 1)[0].strip() for line in path.read_text().splitlines())
    return {line for line in lines if line}


def placeholders(values) -> str:
    return ", ".join("?" * len(values))


def excluded_prospect_ids(source: sqlite3.Connection, teams: set[str]) -> set[int]:
    """Prospects drafted from an excluded program, or with any season there in the college data."""
    if not teams:
        return set()
    t = tuple(teams)
    rows = source.execute(
        f"""SELECT id FROM prospects
            WHERE school IN ({placeholders(t)})
               OR cbbd_athlete_id IN (SELECT athlete_id FROM college_player_seasons
                                      WHERE team IN ({placeholders(t)}))""", t + t).fetchall()
    return {r[0] for r in rows}


def excluded_player_ids(source: sqlite3.Connection, teams: set[str], prospect_ids: set[int]) -> set[int]:
    """NBA players who are excluded prospects, or whose name matches a college player from an excluded program.

    The name match catches players who went undrafted. It errs toward leaving someone out:
    anyone who shares such a player's full name is excluded too.
    """
    if not teams:
        return set()
    ids = {r[0] for r in source.execute(
        f"SELECT nba_person_id FROM prospects WHERE id IN ({placeholders(prospect_ids)})", tuple(prospect_ids))}
    names = {normalize_name(r[0]) for r in source.execute(
        f"SELECT DISTINCT name FROM college_player_seasons WHERE team IN ({placeholders(teams)})", tuple(teams))}
    ids |= {pid for pid, name in source.execute("SELECT id, name FROM players") if normalize_name(name) in names}
    return ids


def snapshot_tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def build_public_db(source: sqlite3.Connection, out_path: Path, excluded_teams: set[str]) -> dict[str, int]:
    """Write the snapshot to out_path and return {table: row count}. excluded_teams is required: pass set()
    to exclude no one, so that leaving nobody out is always a choice."""
    tmp_path = out_path.with_name(out_path.name + ".building")
    tmp_path.unlink(missing_ok=True)
    target = sqlite3.connect(tmp_path)
    try:
        target.executescript(SNAPSHOT_TABLES)
        for sql in derived_table_sql():
            target.execute(sql)

        excluded_prospects = excluded_prospect_ids(source, excluded_teams)
        excluded_players = excluded_player_ids(source, excluded_teams, excluded_prospects)

        prospects = [tuple(r) for r in source.execute(
            "SELECT id, name, draft_year, draft_pick, school, conference, missing_box_flag, college_link"
            " FROM prospects ORDER BY id") if r["id"] not in excluded_prospects]
        target.executemany("INSERT INTO prospects VALUES (?, ?, ?, ?, ?, ?, ?, ?)", prospects)

        players = [tuple(r) for r in source.execute("SELECT id, name FROM players ORDER BY id")
                   if r["id"] not in excluded_players]
        target.executemany("INSERT INTO players VALUES (?, ?)", players)

        # Ranks are computed over the kept prospects only, so a removed prospect leaves no gap.
        drafted = [r[0] for r in source.execute(
            "SELECT id FROM prospects WHERE draft_year IS NOT NULL ORDER BY id")
            if r[0] not in excluded_prospects]
        build_prospect_scores(source, target, drafted)
        build_player_progression(source, target, [p[0] for p in players])
        target.commit()

        tables = snapshot_tables(target)
        if tables != ALLOWED_TABLES:
            raise RuntimeError(f"Snapshot tables {sorted(tables)} differ from the allowed {sorted(ALLOWED_TABLES)}")
        counts = {t: target.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in sorted(tables)}
        target.execute("VACUUM")
    except BaseException:
        target.close()
        tmp_path.unlink(missing_ok=True)
        raise
    target.close()
    tmp_path.replace(out_path)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", type=Path, default=Path("data/upside.db"))
    parser.add_argument("--out", type=Path, default=Path("data/public.db"))
    parser.add_argument("--exclusions", type=Path, default=EXCLUSIONS_PATH,
                        help="Private file of excluded programs, one per line")
    parser.add_argument("--no-exclusions", action="store_true", help="Build without an exclusions file")
    args = parser.parse_args()
    if args.out.resolve() == args.source.resolve():
        parser.error("--out must not be the source database")
    if args.no_exclusions:
        teams: set[str] = set()
    elif args.exclusions.is_file():
        teams = read_excluded_teams(args.exclusions)
    else:
        parser.error(f"{args.exclusions} not found. Create it, or pass --no-exclusions to exclude no one.")

    source = open_readonly(args.source)
    try:
        counts = build_public_db(source, args.out, teams)
    finally:
        source.close()
    print(f"Wrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")
    for table, n in counts.items():
        print(f"  {table:20} {n:>6} rows")


if __name__ == "__main__":
    main()
