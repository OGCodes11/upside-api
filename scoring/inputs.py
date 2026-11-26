"""Build PSI inputs for a prospect from the database, following the rules pre-registered
on Nov 23, 2026 (docs/PHASE2_RESULT.md). A rule change needs a new dated, pre-registered method first, never a code edit alone.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from scoring.common import AdvancedStats, Position, StatLine

P5_CONFERENCES = {"ACC", "Big 12", "Big Ten", "Pac-12", "SEC"}
CLASS_AGE = 19.5
DRAFT_DAY = (6, 25)  # age is measured on June 25 of the draft year
LAST_BROKEN_DRTG_SEASON = 2013  # CBBD defensive ratings are unusable for this season and earlier
PLAUSIBLE_DRTG = (60, 150)
CBBD_POSITION = {"G": "SG", "F": "SF", "C": "C", "PG": "PG", "SG": "SG", "SF": "SF", "PF": "PF"}


@dataclass
class ProspectInputs:
    prospect_id: int
    name: str
    draft_year: int
    draft_pick: int
    position: Position
    position_source: str  # combine or cbbd
    competition_level: str
    conference: str
    final_season: int
    team: str
    per40: StatLine
    advanced: AdvancedStats
    age: float
    age_source: str  # nba or none
    games_played_pct: float
    combine: dict | None  # height_in, weight_lb, wingspan_in, max_vert_in, lane_agility_s, sprint_s
    missing_box_flag: bool
    notes: list[str] = field(default_factory=list)


def position_from_label(label: str | None) -> Position | None:
    """First part of a combine or CBBD position: 'SG-SF' -> SG, 'G-F' -> SG, 'F-C' -> SF."""
    if not label:
        return None
    return CBBD_POSITION.get(label.split("-")[0].strip().upper())


def competition_level(conference: str) -> str:
    return "NCAA_P5" if conference in P5_CONFERENCES else "NCAA_D1"


def age_on_draft_day(birth_date: str | None, draft_year: int) -> float | None:
    if not birth_date:
        return None
    born = date.fromisoformat(birth_date)
    return round((date(draft_year, *DRAFT_DAY) - born).days / 365.25, 2)


def _pct(made, attempted) -> float:
    return 100 * made / attempted if attempted else 0.0


def build_inputs(conn: sqlite3.Connection, prospect_id: int) -> ProspectInputs | None:
    """None when the prospect has no linked college season up to his draft year."""
    p = conn.execute(
        "SELECT id, name, draft_year, draft_pick, cbbd_athlete_id, birth_date, missing_box_flag"
        " FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
    if p is None or p["cbbd_athlete_id"] is None or p["draft_year"] is None:
        return None
    s = conn.execute(
        """SELECT * FROM college_player_seasons WHERE athlete_id = ? AND season <= ?
           ORDER BY season DESC, games DESC LIMIT 1""", (p["cbbd_athlete_id"], p["draft_year"])).fetchone()
    if s is None or not s["minutes"]:
        return None
    notes = []

    combine = conn.execute(
        """SELECT height_in, weight_lb, wingspan_in, max_vert_in, lane_agility_s, sprint_s, c.combine_year,
                  pr.position AS combine_position
           FROM combine c JOIN prospects pr ON pr.id = c.prospect_id
           WHERE c.prospect_id = ? AND c.combine_year <= ? ORDER BY c.combine_year DESC LIMIT 1""",
        (prospect_id, p["draft_year"])).fetchone()
    position = position_from_label(combine["combine_position"]) if combine else None
    position_source = "combine"
    if position is None:
        position = position_from_label(s["position"]) or "SF"  # ATH, NA or blank -> SF
        position_source = "cbbd"
    has_size = combine is not None and None not in (combine["height_in"], combine["weight_lb"], combine["wingspan_in"])

    per40_factor = 40 / s["minutes"]
    per40 = StatLine(
        games_played=s["games"], minutes=40,
        pts=s["points"] * per40_factor, reb=s["reb"] * per40_factor, ast=s["ast"] * per40_factor,
        stl=s["stl"] * per40_factor, blk=s["blk"] * per40_factor, tov=s["tov"] * per40_factor,
        fg_pct=_pct(s["fgm"], s["fga"]), three_pct=_pct(s["fg3m"], s["fg3a"]), ft_pct=_pct(s["ftm"], s["fta"]),
        usg=s["usage"] or 0, position=position,
    )

    drtg = s["drtg"]
    if drtg is not None and (s["season"] <= LAST_BROKEN_DRTG_SEASON
                             or not PLAUSIBLE_DRTG[0] <= drtg <= PLAUSIBLE_DRTG[1]):
        notes.append(f"defensive rating {drtg} treated as missing")
        drtg = None
    advanced = AdvancedStats(
        ts=s["ts_pct"] * 100 if s["ts_pct"] is not None else None,
        ast_tov=s["ast"] / s["tov"] if s["tov"] else None,
        ortg=s["ortg"],
        drtg=drtg,
        net_rating=s["ortg"] - drtg if s["ortg"] is not None and drtg is not None else None,
        usg=s["usage"],
    )

    age = age_on_draft_day(p["birth_date"], p["draft_year"])
    age_source = "nba"
    if age is None:
        age, age_source = CLASS_AGE, "none"

    team_games = conn.execute(
        """SELECT COUNT(*) FROM game_teams gt JOIN games g ON g.league = gt.league AND g.game_id = gt.game_id
           WHERE gt.league = 'ncaa_d1' AND gt.team = ? AND g.season = ? AND g.has_box_score = 1
             AND g.game_type IN ('regular_season', 'conference_tournament')""",
        (s["team"], s["season"])).fetchone()[0]
    games_played_pct = min(100.0, 100 * s["games"] / team_games) if team_games else 100.0
    if not team_games:
        notes.append("no team game count; games-played share set to 100")

    return ProspectInputs(
        prospect_id=p["id"], name=p["name"], draft_year=p["draft_year"], draft_pick=p["draft_pick"],
        position=position, position_source=position_source,
        competition_level=competition_level(s["conference"]), conference=s["conference"],
        final_season=s["season"], team=s["team"], per40=per40, advanced=advanced,
        age=age, age_source=age_source, games_played_pct=games_played_pct,
        combine={k: combine[k] for k in ("height_in", "weight_lb", "wingspan_in", "max_vert_in",
                                         "lane_agility_s", "sprint_s")} if has_size else None,
        missing_box_flag=bool(p["missing_box_flag"]), notes=notes,
    )
