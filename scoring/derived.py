"""Build the derived tables the public endpoints serve: prospect_scores and player_progression.

Reads the full local database (source) and writes to a target database, which may be the same file
or a separate public snapshot (scripts/build_public_db.py). Nothing here writes a raw stat line.
"""

import json
import sqlite3
from datetime import UTC, datetime

from app.game_stats import season_stats
from scoring.inputs import build_inputs
from scoring.score_prospect import score_prospect

PSI_VERSION = "v1-neutral-hand-inputs"
SMALL_SEASON_GAMES = 20  # a season with fewer games gets small_sample on its year-over-year change


def season_index(season_id: str) -> int:
    return int(season_id[:4])  # "2014-15" -> 2014


def rank_within_class(scores: list[tuple[int, int, float]]) -> dict[int, tuple[int, int]]:
    """[(prospect_id, draft_year, psi)] -> {prospect_id: (rank, class_size)}. Ties share the better rank."""
    by_class: dict[int, list[tuple[int, float]]] = {}
    for prospect_id, year, psi in scores:
        by_class.setdefault(year, []).append((prospect_id, psi))
    ranks = {}
    for members in by_class.values():
        ordered = sorted(members, key=lambda m: -m[1])
        for prospect_id, psi in ordered:
            ranks[prospect_id] = (1 + sum(1 for _, other in ordered if other > psi), len(ordered))
    return ranks


def build_prospect_scores(source: sqlite3.Connection, target: sqlite3.Connection,
                          prospect_ids: list[int] | None = None) -> int:
    if prospect_ids is None:
        prospect_ids = [r[0] for r in source.execute(
            "SELECT id FROM prospects WHERE draft_year IS NOT NULL ORDER BY id")]
    scored_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for prospect_id in prospect_ids:
        inputs = build_inputs(source, prospect_id)
        if inputs is None:
            continue
        s = score_prospect(inputs)
        athlete = source.execute("SELECT cbbd_athlete_id FROM prospects WHERE id = ?", (prospect_id,)).fetchone()[0]
        lines = season_stats(source, "ncaa_d1", inputs.final_season, athlete)
        split = lines[0] if lines else None
        rows.append((inputs, s, split))

    ranks = rank_within_class([(i.prospect_id, i.draft_year, s.psi) for i, s, _ in rows])
    target.executemany(
        """INSERT OR REPLACE INTO prospect_scores VALUES
           (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(
            i.prospect_id, PSI_VERSION, s.psi, *ranks[i.prospect_id],
            s.components["production"], s.components["skills"], s.components["intangibles"],
            s.components["physical"], s.components["team_fit"], s.components["risk"],
            json.dumps(s.skill_dimensions), json.dumps(s.missing_inputs), json.dumps(i.notes),
            i.position, i.position_source, s.physical_source, i.age_source, i.competition_level, i.final_season,
            split.postseason_games if split else None,
            int(split.small_postseason_sample) if split else None,
            split.regular_pts_per40 if split else None, split.postseason_pts_per40 if split else None,
            split.regular_ts_pct if split else None, split.postseason_ts_pct if split else None,
            scored_at,
        ) for i, s, split in rows],
    )
    return len(rows)


def build_player_progression(source: sqlite3.Connection, target: sqlite3.Connection,
                             player_ids: list[int] | None = None) -> int:
    query = "SELECT player_id, season_id, gp, min, pts, reb, ast, team_count FROM player_season_stats"
    params: tuple = ()
    if player_ids is not None:
        query += f" WHERE player_id IN ({', '.join('?' for _ in player_ids)})"
        params = tuple(player_ids)
    seasons: dict[int, list] = {}
    for row in source.execute(query + " ORDER BY player_id, season_id", params):
        seasons.setdefault(row[0], []).append(row)

    changes = []
    for player_id, rows in seasons.items():
        for prev, cur in zip(rows, rows[1:]):
            def change(i):
                return None if cur[i] is None or prev[i] is None else round(cur[i] - prev[i], 1)
            changes.append((
                player_id, cur[1], prev[1], season_index(cur[1]) - season_index(prev[1]),
                None if cur[2] is None or prev[2] is None else cur[2] - prev[2],
                change(3), change(4), change(5), change(6),
                int((cur[7] or 1) > 1 or (prev[7] or 1) > 1),
                int((cur[2] or 0) < SMALL_SEASON_GAMES or (prev[2] or 0) < SMALL_SEASON_GAMES),
            ))
    target.executemany("INSERT OR REPLACE INTO player_progression VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", changes)
    return len(changes)
