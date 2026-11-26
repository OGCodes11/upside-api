"""Load cached per-game box scores into the games and player_game_stats tables.

Run from the project root:  python -m ingest.load_games 2019 --to 2026
  NCAA D1 games come from ingest/cbbd.py --games; G League Ignite games from ingest/gleague.py.
Makes no API calls. Safe to run again: rows are updated, not duplicated.

Every row records stats_as_of: when its data was pulled (the cache file's time, UTC).
"""

import argparse
import json
import re
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from app.db import connect, db_path, init_schema
from ingest import cbbd, gleague

EASTERN = ZoneInfo("America/New_York")

UPSERT_GAME = """
    INSERT INTO games (league, game_id, season, start_date, game_type, tournament, notes, has_box_score)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (league, game_id) DO UPDATE SET
        season = excluded.season, start_date = excluded.start_date, game_type = excluded.game_type,
        tournament = excluded.tournament, notes = excluded.notes, has_box_score = excluded.has_box_score
"""

UPSERT_GAME_TEAM = """
    INSERT INTO game_teams (league, game_id, team, conference) VALUES (?, ?, ?, ?)
    ON CONFLICT (league, game_id, team) DO UPDATE SET conference = excluded.conference
"""

_stat_cols = ["name", "team", "opponent", "starter", "minutes", "pts", "fgm", "fga", "fg3m", "fg3a",
              "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf", "stats_as_of", "source"]
MANUAL_DIR = Path(__file__).with_name("manual")
UPSERT_PLAYER_GAME = f"""
    INSERT INTO player_game_stats (league, game_id, player_id, {", ".join(_stat_cols)})
    VALUES (?, ?, ?, {", ".join("?" for _ in _stat_cols)})
    ON CONFLICT (league, game_id, player_id) DO UPDATE SET
        {", ".join(f"{c} = excluded.{c}" for c in _stat_cols)}
"""


TOURNAMENT_NOTE = re.compile(r"TOURNAMENT|TOURNEY|CHAMPIONSHIP|CONF TR\b", re.IGNORECASE)
NIT_NOTE = re.compile(r"\bNIT\b", re.IGNORECASE)
NCAA_NOTE = re.compile(r"MEN'S BASKETBALL CHAMPIONSHIP|\bNCAA\b|FIRST FOUR", re.IGNORECASE)


def classify_cbbd_game(season_type: str, tournament: str | None, game_type: str | None,
                       conference_game: bool, same_conference: bool, played_on: date,
                       notes: str | None = None, neutral_site: bool = False) -> str | None:
    """Map CBBD's labels to our game_type. Returns None for games we don't count (preseason).

    Postseason games carry a tournament label (NCAA, NIT, CBI, CIT) from 2009 on, but not always:
    the 2008-09 NIT and some later CBI/CIT games have none, so the game notes decide
    ("MasterCard NIT - 1st Round", "MEN'S BASKETBALL CHAMPIONSHIP - EAST REGION").

    Conference tournaments are seasonType 'regular'. From about 2010 CBBD marks them gameType
    'TRNMNT'; earlier seasons don't, so a same-conference game from Feb 20 on also counts when its
    notes name a tournament or it's at a neutral site. November events like the Maui Invitational
    are 'TRNMNT' too, but the date rule keeps them regular season.
    """
    notes = notes or ""
    if season_type == "postseason":
        label = (tournament or "").upper()
        if label == "NCAA" or (not label and NCAA_NOTE.search(notes)):
            return "ncaa_tournament"
        if label == "NIT" or (not label and NIT_NOTE.search(notes)):
            return "nit"
        return "other_postseason"
    if season_type == "regular":
        in_conference = conference_game or same_conference
        late_season = (played_on.month, played_on.day) >= (2, 20) and played_on.month <= 4
        if in_conference and played_on.month in (2, 3, 4):
            if game_type == "TRNMNT":
                return "conference_tournament"
            if late_season and (TOURNAMENT_NOTE.search(notes) or neutral_site):
                return "conference_tournament"
        return "regular_season"
    return None


def eastern_date(start: str) -> date:
    """CBBD start times are UTC. A 9 pm Eastern tip is the next day in UTC, so convert first."""
    return datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(EASTERN).date()


def pulled_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")


def _part(stat: dict | None, key: str):
    return (stat or {}).get(key)


def load_cbbd_file(conn: sqlite3.Connection, path: Path) -> tuple[int, int]:
    """Load one cached /games/players window. Only D1 teams' players are kept."""
    as_of = pulled_at(path)
    games, player_rows, teams = {}, [], []
    for team_game in json.loads(path.read_text()):
        if not team_game.get("conference"):
            continue  # a non-D1 team
        played_on = eastern_date(team_game["startDate"])
        game_type = classify_cbbd_game(
            team_game["seasonType"], team_game.get("tournament"), team_game.get("gameType"),
            bool(team_game.get("conferenceGame")),
            team_game["conference"] == team_game.get("opponentConference"), played_on,
            team_game.get("notes"), bool(team_game.get("neutralSite")),
        )
        if game_type is None:
            continue  # preseason
        game_id = str(team_game["gameId"])
        has_players = 1 if team_game.get("players") else 0
        if game_id in games:  # the other D1 team in the same game: box score if either side has one
            has_players = max(has_players, games[game_id][-1])
        games[game_id] = ("ncaa_d1", game_id, team_game["season"], played_on.isoformat(), game_type,
                          team_game.get("tournament"), team_game.get("notes"), has_players)
        teams.append(("ncaa_d1", game_id, team_game["team"], team_game["conference"]))
        for p in team_game.get("players") or []:
            if p.get("athleteId") is None:
                continue
            starter = p.get("starter")
            player_rows.append((
                "ncaa_d1", game_id, p["athleteId"], p["name"], team_game["team"], team_game.get("opponent"),
                None if starter is None else int(starter), p.get("minutes"), p.get("points"),
                _part(p.get("fieldGoals"), "made"), _part(p.get("fieldGoals"), "attempted"),
                _part(p.get("threePointFieldGoals"), "made"), _part(p.get("threePointFieldGoals"), "attempted"),
                _part(p.get("freeThrows"), "made"), _part(p.get("freeThrows"), "attempted"),
                _part(p.get("rebounds"), "offensive"), _part(p.get("rebounds"), "defensive"),
                _part(p.get("rebounds"), "total"), p.get("assists"), p.get("steals"), p.get("blocks"),
                p.get("turnovers"), p.get("fouls"), as_of, "cbbd",
            ))
    conn.executemany(UPSERT_GAME, games.values())
    conn.executemany(UPSERT_GAME_TEAM, teams)
    conn.executemany(UPSERT_PLAYER_GAME, player_rows)
    return len(games), len(player_rows)


def load_cbbd_season(conn: sqlite3.Connection, season: int, raw_dir: Path = cbbd.RAW_DIR) -> tuple[int, int]:
    games = players = 0
    for start, end in cbbd.season_windows(season):
        path = raw_dir / "games" / f"players_{start.isoformat()}_{end.isoformat()}.json"
        if not path.exists():
            # The window may have been split in two because it hit the per-call limit.
            halves = sorted((raw_dir / "games").glob("players_*.json"))
            parts = [p for p in halves if start.isoformat() <= p.stem.split("_")[1] <= end.isoformat()]
            if not parts:
                raise FileNotFoundError(f"{path} is missing. Run: python -m ingest.cbbd {season} --games")
        else:
            parts = [path]
        for part in parts:
            g, p = load_cbbd_file(conn, part)
            games, players = games + g, players + p
    return games, players


def _ignite_season(label: str) -> int:
    return int(label[:4]) + 1  # "2022-23" -> 2023


def load_ignite(conn: sqlite3.Connection, raw_dir: Path = gleague.RAW_DIR) -> tuple[int, int]:
    games, players = {}, []
    for season in gleague.IGNITE_SEASONS:
        for season_type, short in gleague.SEASON_TYPES.items():
            path = gleague.logs_path(season, season_type, raw_dir)
            if not path.exists():
                raise FileNotFoundError(f"{path} is missing. Run: python -m ingest.gleague")
            df = pd.read_csv(path, dtype={"GAME_ID": str})
            if df.empty:
                continue
            as_of = pulled_at(path)
            game_type = "regular_season" if short == "regular" else "g_league_playoffs"
            for _, r in df.iterrows():
                game_id = r["GAME_ID"]
                games[game_id] = ("g_league", game_id, _ignite_season(season), str(r["GAME_DATE"])[:10],
                                  game_type, None, None, 1)
                opponent = r["MATCHUP"].split()[-1]  # "GLI @ SBL" -> "SBL"
                players.append((
                    "g_league", game_id, int(r["PLAYER_ID"]), r["PLAYER_NAME"], r["TEAM_ABBREVIATION"], opponent,
                    None, r["MIN"], r["PTS"], r["FGM"], r["FGA"], r["FG3M"], r["FG3A"], r["FTM"], r["FTA"],
                    r["OREB"], r["DREB"], r["REB"], r["AST"], r["STL"], r["BLK"], r["TOV"], r["PF"], as_of,
                    "nba_stats",
                ))
    clean = [tuple(None if pd.isna(v) else (v.item() if hasattr(v, "item") else v) for v in row) for row in players]
    conn.executemany(UPSERT_GAME, games.values())
    conn.executemany(UPSERT_GAME_TEAM, {("g_league", row[1], row[4], None) for row in clean})
    conn.executemany(UPSERT_PLAYER_GAME, clean)
    return len(games), len(clean)


def load_manual_boxscores(conn: sqlite3.Connection, manual_dir: Path = MANUAL_DIR) -> int:
    """Load hand-entered box scores from ingest/manual/*.json, for games CBBD lists without one.

    Each file is a real box score from another source, never an estimate. Rows are tagged
    source = 'manual'. A file is skipped if the game is not loaded, or already has a box score
    (so it can never overwrite source data). Points must add up to the final score.
    """
    loaded = 0
    for path in sorted(manual_dir.glob("*.json")):
        box = json.loads(path.read_text())
        league, game_id = box["league"], box["game_id"]
        game = conn.execute("SELECT season, has_box_score FROM games WHERE league = ? AND game_id = ?",
                            (league, game_id)).fetchone()
        if game is None or game[1]:
            continue
        players = [dict(zip(box["columns"], row)) for row in box["players"]]
        for team, score in box["final_score"].items():
            total = sum(p["pts"] for p in players if p["team"] == team)
            if total != score:
                raise ValueError(f"{path.name}: {team} players total {total} points, final score says {score}")
        team_a, team_b = box["final_score"]
        opponent = {team_a: team_b, team_b: team_a}
        as_of = box["entered_at"]  # when the box score was entered, so reruns don't change it
        rows = []
        for p in players:
            ids = conn.execute(
                """SELECT DISTINCT s.player_id FROM player_game_stats s JOIN games g USING (league, game_id)
                   WHERE s.league = ? AND g.season = ? AND s.team = ? AND s.name = ?""",
                (league, game[0], p["team"], p["name"]),
            ).fetchall()
            if len(ids) != 1:
                raise ValueError(f"{path.name}: can't find one {p['team']} player named {p['name']!r} that season")
            rows.append((
                league, game_id, ids[0][0], p["name"], p["team"], opponent[p["team"]], p["starter"], p["minutes"],
                p["pts"], p["fgm"], p["fga"], p["fg3m"], p["fg3a"], p["ftm"], p["fta"], p["oreb"], p["dreb"],
                p["reb"], p["ast"], p["stl"], p["blk"], p["tov"], p["pf"], as_of, "manual",
            ))
        conn.executemany(UPSERT_PLAYER_GAME, rows)
        conn.execute("UPDATE games SET has_box_score = 1 WHERE league = ? AND game_id = ?", (league, game_id))
        loaded += 1
    return loaded


# Seasons where CBBD confirmed listing games without box scores (pre-registered Nov 22 2026; docs/PHASE2_RESULT.md).
# Games without box scores elsewhere (e.g. 2021) look like cancellations, so they don't count.
GAP_SEASONS = (2012, 2013)
MISSING_BOX_FLAG_OVER = 0.10


def update_missing_box_scores(conn: sqlite3.Connection) -> dict[str, int]:
    """Set prospects.missing_box_share and missing_box_flag for every linked prospect.

    share = his teams' games in his pre-draft seasons that have no box score and fall in GAP_SEASONS,
            divided by all of his teams' games in those pre-draft seasons.
    His team in a season is the one he played the most games for (ignores CBBD's stray 1-game rows).
    NULL when none of his seasons have per-game data loaded. Missing games are never estimated.
    """
    gap_list = ", ".join(str(s) for s in GAP_SEASONS)
    rows = conn.execute(f"""
        WITH target AS (
            SELECT p.id, p.cbbd_athlete_id,
                   COALESCE(p.draft_year, (SELECT MAX(combine_year) FROM combine c WHERE c.prospect_id = p.id)) AS year
            FROM prospects p WHERE p.cbbd_athlete_id IS NOT NULL
        ),
        ranked AS (
            SELECT t.id, c.season, c.team,
                   ROW_NUMBER() OVER (PARTITION BY t.id, c.season ORDER BY c.games DESC) AS rank
            FROM target t JOIN college_player_seasons c ON c.athlete_id = t.cbbd_athlete_id
            WHERE c.season <= t.year
        ),
        main_team AS (SELECT id, season, team FROM ranked WHERE rank = 1)
        SELECT m.id,
               COUNT(g.game_id) AS listed,
               SUM(CASE WHEN g.has_box_score = 0 AND g.season IN ({gap_list}) THEN 1 ELSE 0 END) AS missing
        FROM main_team m
        LEFT JOIN game_teams gt ON gt.league = 'ncaa_d1' AND gt.team = m.team
        LEFT JOIN games g ON g.league = gt.league AND g.game_id = gt.game_id AND g.season = m.season
        GROUP BY m.id
    """).fetchall()

    updates, counts = [], {"flagged": 0, "not_flagged": 0, "no_game_data": 0}
    for prospect_id, listed, missing in rows:
        if not listed:
            updates.append((None, None, prospect_id))
            counts["no_game_data"] += 1
            continue
        share = round(missing / listed, 4)
        flag = int(share > MISSING_BOX_FLAG_OVER)
        updates.append((share, flag, prospect_id))
        counts["flagged" if flag else "not_flagged"] += 1
    conn.execute("UPDATE prospects SET missing_box_share = NULL, missing_box_flag = NULL")
    conn.executemany("UPDATE prospects SET missing_box_share = ?, missing_box_flag = ? WHERE id = ?", updates)
    return counts


def load_games(seasons: range, conn: sqlite3.Connection, cbbd_dir: Path = cbbd.RAW_DIR,
               gleague_dir: Path | None = gleague.RAW_DIR) -> None:
    init_schema(conn)
    with conn:  # one transaction: everything loads, or nothing does
        for season in seasons:
            games, players = load_cbbd_season(conn, season, cbbd_dir)
            print(f"NCAA D1 {season}: {games} games, {players} player games")
        if gleague_dir is not None:
            games, players = load_ignite(conn, gleague_dir)
            print(f"G League Ignite: {games} games, {players} player games")
        print(f"Hand-entered box scores filled: {load_manual_boxscores(conn)}")
        print(f"Prospect missing-box-score flags: {update_missing_box_scores(conn)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("season", type=int, help="Season's ending year, like 2019 (the first one if --to is given)")
    parser.add_argument("--to", type=int, help="Last season of a range")
    args = parser.parse_args()
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        load_games(range(args.season, (args.to or args.season) + 1), conn)
    finally:
        conn.close()
    print(f"Database: {path}")


if __name__ == "__main__":
    main()
