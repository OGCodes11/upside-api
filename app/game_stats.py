"""Season stats built from per-game box scores.

The two settings that matter:
  include_postseason  True counts NCAA/NIT/other postseason (and G League playoffs) in the main line;
                      False stops at the conference tournament. Nothing else changes.
  as_of               only games before this date (YYYY-MM-DD). Use it in backtests so a pre-draft
                      evaluation never sees games played after the cutoff, e.g. the NCAA tournament.

Postseason is always reported separately as a visible split (postseason_* and regular_* fields), with
small_postseason_sample = True when a player has fewer than 5 postseason games. These split fields are
for display and flags only; they are not blended into any score.
"""

import sqlite3
from dataclasses import dataclass

REGULAR_TYPES = ("regular_season", "conference_tournament")
POSTSEASON_TYPES = ("ncaa_tournament", "nit", "other_postseason", "g_league_playoffs")
SMALL_POSTSEASON_SAMPLE = 5  # fewer postseason games than this gets the flag


@dataclass
class SeasonLine:
    league: str
    player_id: int
    season: int
    name: str
    teams: str
    # main line (regular season + conference tournament, plus postseason if included)
    games: int
    minutes: float
    pts: int
    fgm: int
    fga: int
    fg3m: int
    fg3a: int
    ftm: int
    fta: int
    reb: int
    ast: int
    stl: int
    blk: int
    tov: int
    # split: always both, whatever include_postseason says
    regular_games: int
    regular_pts_per40: float | None
    regular_ts_pct: float | None
    postseason_games: int
    postseason_pts_per40: float | None
    postseason_ts_pct: float | None
    small_postseason_sample: bool
    # audit trail
    last_game: str
    stats_as_of: str  # most recent pull among the games counted

    @property
    def pts_per40(self) -> float | None:
        return per40(self.pts, self.minutes)

    @property
    def ts_pct(self) -> float | None:
        return true_shooting(self.pts, self.fga, self.fta)


def per40(value, minutes) -> float | None:
    return round(value * 40 / minutes, 1) if minutes else None


def true_shooting(pts, fga, fta) -> float | None:
    attempts = 2 * ((fga or 0) + 0.44 * (fta or 0))
    return round(pts / attempts, 3) if attempts else None


def _in(types: tuple[str, ...]) -> str:
    # Built only from the constants above, never from user input.
    return "(" + ", ".join(f"'{t}'" for t in types) + ")"


def season_stats(
    conn: sqlite3.Connection,
    league: str = "ncaa_d1",
    season: int | None = None,
    player_id: int | None = None,
    include_postseason: bool = True,
    as_of: str | None = None,
) -> list[SeasonLine]:
    counted = REGULAR_TYPES + (POSTSEASON_TYPES if include_postseason else ())
    main, reg, post = _in(counted), _in(REGULAR_TYPES), _in(POSTSEASON_TYPES)

    def total(col: str, types: str) -> str:
        return f"COALESCE(SUM(CASE WHEN g.game_type IN {types} THEN s.{col} END), 0)"

    def played(types: str) -> str:
        return f"SUM(CASE WHEN g.game_type IN {types} AND s.minutes > 0 THEN 1 ELSE 0 END)"

    stat_cols = ["minutes", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "reb", "ast", "stl", "blk", "tov"]
    rows = conn.execute(
        f"""
        SELECT s.league, s.player_id, g.season, MAX(s.name) AS name,
               GROUP_CONCAT(DISTINCT s.team) AS teams,
               {played(main)} AS games,
               {", ".join(f"{total(c, main)} AS {c}" for c in stat_cols)},
               {played(reg)} AS regular_games,
               {total("minutes", reg)} AS reg_min, {total("pts", reg)} AS reg_pts,
               {total("fga", reg)} AS reg_fga, {total("fta", reg)} AS reg_fta,
               {played(post)} AS postseason_games,
               {total("minutes", post)} AS post_min, {total("pts", post)} AS post_pts,
               {total("fga", post)} AS post_fga, {total("fta", post)} AS post_fta,
               MAX(CASE WHEN g.game_type IN {main} THEN g.start_date END) AS last_game,
               MAX(CASE WHEN g.game_type IN {main} THEN s.stats_as_of END) AS stats_as_of
        FROM player_game_stats s
        JOIN games g ON g.league = s.league AND g.game_id = s.game_id
        WHERE s.league = :league
          AND (:season IS NULL OR g.season = :season)
          AND (:player_id IS NULL OR s.player_id = :player_id)
          AND (:as_of IS NULL OR g.start_date < :as_of)
        GROUP BY s.league, s.player_id, g.season
        HAVING {played(main)} > 0
        ORDER BY g.season, s.player_id
        """,
        {"league": league, "season": season, "player_id": player_id, "as_of": as_of},
    ).fetchall()

    return [
        SeasonLine(
            league=r["league"], player_id=r["player_id"], season=r["season"], name=r["name"], teams=r["teams"],
            games=r["games"], **{c: r[c] for c in stat_cols},
            regular_games=r["regular_games"],
            regular_pts_per40=per40(r["reg_pts"], r["reg_min"]),
            regular_ts_pct=true_shooting(r["reg_pts"], r["reg_fga"], r["reg_fta"]),
            postseason_games=r["postseason_games"],
            postseason_pts_per40=per40(r["post_pts"], r["post_min"]),
            postseason_ts_pct=true_shooting(r["post_pts"], r["post_fga"], r["post_fta"]),
            small_postseason_sample=r["postseason_games"] < SMALL_POSTSEASON_SAMPLE,
            last_game=r["last_game"], stats_as_of=r["stats_as_of"],
        )
        for r in rows
    ]
