"""Check loaded per-game data for gaps, season by season.

Run from the project root:  python -m ingest.verify_games 2008 --to 2021
Makes no API calls. Prints one block per season and a list of problems at the end.

Checks:
  1. Row counts: games and player-game rows per season.
  2. game_type values: only the allowed ones, and the count of each.
  3. Coverage: games CBBD lists but has no player stats for (has_box_score = 0). Any conference
     tournament, NCAA or NIT game without one is reported; so is more than 2% of a season.
  4. Postseason: NCAA and NIT games with box scores against the real bracket sizes.
  5. Loading: do our per-game rows add up to CBBD's season totals? (Same source, so this only
     catches our mistakes, not CBBD's gaps.)
  6. stats_as_of: present on every row, and pulled after the season's last game.
"""

import argparse
import sqlite3
from collections import defaultdict

from app.db import connect, db_path

ALLOWED_TYPES = {"regular_season", "conference_tournament", "ncaa_tournament", "nit", "other_postseason"}


def expected_ncaa_games(season: int) -> int | None:
    if season == 2020:
        return 0  # cancelled (COVID)
    if season == 2021:
        return 66  # 67 scheduled; VCU-Oregon was declared a no-contest
    if 2002 <= season <= 2010:
        return 64  # 65 teams: 1 opening-round game + 63
    if season >= 2011:
        return 67  # 68 teams: 4 First Four games + 63
    return None


def expected_nit_games(season: int) -> int | None:
    if season == 2020:
        return 0  # cancelled
    if season == 2021:
        return 16  # 16-team field: 15 games plus a consolation game
    return 31  # 32-team field


def verify_season(conn: sqlite3.Connection, season: int) -> tuple[list[str], list[str]]:
    """Returns (report lines, problems)."""
    lines, problems = [], []
    q = lambda sql, *args: conn.execute(sql, (season, *args)).fetchall()  # noqa: E731

    # 1. row counts
    games = q("SELECT COUNT(*) FROM games WHERE league = 'ncaa_d1' AND season = ?")[0][0]
    rows = q("""SELECT COUNT(*) FROM player_game_stats s JOIN games g USING (league, game_id)
                WHERE g.league = 'ncaa_d1' AND g.season = ?""")[0][0]
    lines.append(f"games {games:,}, player-game rows {rows:,}")
    if games == 0:
        return lines, [f"{season}: no games loaded"]

    # 2. game types, and how many games of each type have a box score
    by_type = {t: (listed, boxed) for t, listed, boxed in q(
        """SELECT game_type, COUNT(*), SUM(has_box_score) FROM games
           WHERE league = 'ncaa_d1' AND season = ? GROUP BY 1""")}
    lines.append("types (with box score / listed): "
                 + ", ".join(f"{t} {b}/{n}" for t, (n, b) in sorted(by_type.items())))
    for bad in set(by_type) - ALLOWED_TYPES:
        problems.append(f"{season}: unexpected game_type {bad!r}")

    # 3. coverage: games the source lists but has no player stats for
    listed = sum(n for n, _ in by_type.values())
    missing = sum(n - b for n, b in by_type.values())
    lines.append(f"games without a box score: {missing} of {listed} ({100 * missing / listed:.1f}%)")
    if missing / listed > 0.02:
        problems.append(f"{season}: {missing} of {listed} games ({100 * missing / listed:.1f}%) have no box score")
    for t in ("conference_tournament", "ncaa_tournament", "nit"):
        n, b = by_type.get(t, (0, 0))
        if n - b and season != 2020:  # 2020: conference tournaments were cancelled mid-week
            problems.append(f"{season}: {n - b} of {n} {t} games have no box score")

    # 4. postseason games with box scores against the real bracket sizes
    for game_type, expected in (("ncaa_tournament", expected_ncaa_games(season)), ("nit", expected_nit_games(season))):
        got = by_type.get(game_type, (0, 0))[1]
        if expected is not None and got != expected:
            problems.append(f"{season}: {game_type} has {got} games with box scores, expected {expected}")

    # 5. per-game rows vs CBBD season totals (season totals run through the conference tournament).
    # CBBD builds its season totals from the same box scores, so this catches loading mistakes on
    # our side, NOT games missing from CBBD itself. Check 3 is the one for that.
    compare = q("""
        WITH box AS (
            SELECT s.player_id, s.team, SUM(s.pts) AS pts, SUM(s.minutes > 0) AS gp
            FROM player_game_stats s JOIN games g USING (league, game_id)
            WHERE g.league = 'ncaa_d1' AND g.season = ?
              AND g.game_type IN ('regular_season', 'conference_tournament')
            GROUP BY s.player_id, s.team)
        SELECT c.team, c.athlete_id, c.points, box.pts, c.games, box.gp
        FROM college_player_seasons c
        LEFT JOIN box ON box.player_id = c.athlete_id AND box.team = c.team
        WHERE c.season = ? AND c.points > 0""", season)
    # A season-total row with no box scores for that team at all is a CBBD stray row
    # (a player credited to his opponent for one game), not a loading problem.
    stray = [r for r in compare if r[3] is None]
    checked = [r for r in compare if r[3] is not None]
    match = sum(1 for _, _, sp, bp, _, _ in checked if sp == bp)
    short_by_team = defaultdict(int)
    for team, _, season_pts, box_pts, _, _ in checked:
        if box_pts < season_pts:
            short_by_team[team] += season_pts - box_pts
    pct = 100 * match / len(checked) if checked else 0
    lines.append(f"loaded box scores match CBBD season totals: {match:,}/{len(checked):,} player-team seasons ({pct:.1f}%)")
    if stray:
        lines.append(f"CBBD stray season-total rows (team he has no box scores for): {len(stray)}")
    worst = sorted(short_by_team.items(), key=lambda kv: -kv[1])[:5]
    if worst:
        lines.append("teams with the most missing points: " + ", ".join(f"{t} ({p})" for t, p in worst))
    if pct < 98:
        problems.append(f"{season}: only {pct:.1f}% of player seasons match season totals")

    # 6. stats_as_of
    nulls, first_pull, last_game = q("""
        SELECT SUM(s.stats_as_of IS NULL), MIN(s.stats_as_of), MAX(g.start_date)
        FROM player_game_stats s JOIN games g USING (league, game_id)
        WHERE g.league = 'ncaa_d1' AND g.season = ?""")[0]
    lines.append(f"stats_as_of: earliest pull {first_pull}, last game {last_game}, missing {nulls}")
    if nulls:
        problems.append(f"{season}: {nulls} rows have no stats_as_of")
    if first_pull and first_pull[:10] <= last_game:
        problems.append(f"{season}: some rows were pulled before the season ended ({first_pull[:10]})")
    return lines, problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("season", type=int)
    parser.add_argument("--to", type=int)
    args = parser.parse_args()
    conn = connect(db_path())
    all_problems = []
    for season in range(args.season, (args.to or args.season) + 1):
        lines, problems = verify_season(conn, season)
        print(f"== {season} ==")
        for line in lines:
            print("  " + line)
        all_problems += problems
    print("\nProblems:" if all_problems else "\nNo problems found.")
    for p in all_problems:
        print("  - " + p)
    conn.close()


if __name__ == "__main__":
    main()
