import json
import os
import secrets
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Generic, TypeVar

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from app.db import check_public_snapshot, connect_readonly, get_db, public_mode
from scoring.psi import PSI_WEIGHTS


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """In public mode, refuse to start unless the database is a public snapshot."""
    if public_mode():
        conn = connect_readonly()
        try:
            check_public_snapshot(conn)
        finally:
            conn.close()
    yield


app = FastAPI(title="Upside API", version="0.3", lifespan=lifespan)

T = TypeVar("T")
SEASON_PATTERN = r"^\d{4}-\d{2}$"

# --- Raw-data lock ---
# Locally (PUBLIC_MODE unset) the raw endpoints are open. With PUBLIC_MODE=1 they need an
# X-API-Key header matching UPSIDE_API_KEY. Raw NBA.com stat lines are never served publicly.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_raw_access(key: str | None = Security(api_key_header)) -> None:
    if not public_mode():
        return
    expected = os.environ.get("UPSIDE_API_KEY")
    # No key configured means nobody gets in: fail closed rather than open.
    if not expected or key is None or not secrets.compare_digest(key, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid API key",
                            headers={"WWW-Authenticate": "API-Key"})


def require_raw_tables(db: sqlite3.Connection = Depends(get_db)) -> sqlite3.Connection:
    """The public snapshot has no raw stat tables. Say so instead of failing with a 500."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'player_season_stats'"
                  ).fetchone() is None:
        raise HTTPException(status_code=503, detail="Raw stats are not available in this deployment")
    return db


RAW_ACCESS = [Depends(require_raw_access)]


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int  # rows matching the filters, across all pages
    limit: int
    offset: int


class PlayerSeason(BaseModel):
    id: int
    name: str
    season: str
    team: str
    traded: bool  # played for more than one team; stats are combined, team is his last one
    gp: int
    min: float | None
    pts: float | None
    reb: float | None
    ast: float | None


class PlayerProfile(BaseModel):
    id: int
    name: str
    team: str  # team in his most recent season
    first_season: str
    last_season: str
    seasons_played: int


class SeasonLine(BaseModel):
    season: str
    team: str
    traded: bool  # played for more than one team; stats are combined, team is his last one
    games: int
    minutes: float | None  # per game
    pts: float | None
    reb: float | None
    ast: float | None


# --- Public, derived-data models: PSI, component scores, flags, ranks, year-over-year changes ---

class ProspectSummary(BaseModel):
    id: int
    name: str
    draft_year: int | None
    draft_pick: int | None
    school: str | None
    conference: str | None
    psi: float | None  # None when the prospect can't be scored (no college season, not drafted)
    psi_rank: int | None  # 1 = highest in his draft class
    class_size: int | None
    missing_box_flag: bool | None
    small_postseason_sample: bool | None
    college_link: str | None  # how he was matched to his college career


class Component(BaseModel):
    name: str
    weight: float
    score: float
    status: str  # scored, or why it is neutral


class TournamentSplit(BaseModel):
    """Kept apart from PSI on purpose: tournament play is a visible split, not part of the score."""
    final_season: int
    postseason_games: int | None
    small_postseason_sample: bool | None
    regular_pts_per40: float | None
    postseason_pts_per40: float | None
    regular_ts_pct: float | None
    postseason_ts_pct: float | None


class PSIFlags(BaseModel):
    missing_box_flag: bool | None
    college_link: str | None
    position: str
    position_source: str
    physical_source: str
    age_source: str
    competition_level: str


class ProspectPSI(BaseModel):
    id: int
    name: str
    draft_year: int | None
    psi_version: str
    psi: float
    psi_rank: int
    class_size: int
    components: list[Component]
    skill_dimensions: dict[str, float]
    missing_inputs: list[str]
    notes: list[str]
    flags: PSIFlags
    tournament_split: TournamentSplit


class SeasonChange(BaseModel):
    season: str
    previous_season: str
    seasons_apart: int
    games_change: int | None
    minutes_change: float | None  # per game
    pts_change: float | None
    reb_change: float | None
    ast_change: float | None
    traded: bool
    small_sample: bool  # either season had fewer than 20 games


class Trend(BaseModel):
    """Average change per season across all of his year-over-year changes."""
    changes: int
    small_sample_changes: int
    minutes_per_season: float | None
    pts_per_season: float | None
    reb_per_season: float | None
    ast_per_season: float | None


class Progression(Page[SeasonChange]):
    player_id: int
    trend: Trend


PLAYER_SEASON_FILTER = """
    FROM player_season_stats s
    JOIN players p ON p.id = s.player_id
    JOIN teams t ON t.id = s.team_id
    WHERE s.season_id = :season
      AND (:team IS NULL OR t.abbreviation = :team)
"""


@app.get("/v1/players", response_model=Page[PlayerSeason], dependencies=RAW_ACCESS)
def list_players(
    season: str | None = Query(None, pattern=SEASON_PATTERN, description="Defaults to the latest season"),
    team: str | None = Query(None, description="Team abbreviation, e.g. GSW"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(require_raw_tables),
):
    if season is None:
        season = db.execute("SELECT MAX(id) FROM seasons").fetchone()[0]
    params = {"season": season, "team": team.upper() if team else None, "limit": limit, "offset": offset}

    total = db.execute(f"SELECT COUNT(*) {PLAYER_SEASON_FILTER}", params).fetchone()[0]
    rows = db.execute(
        f"""
        SELECT p.id, p.name, s.season_id AS season, t.abbreviation AS team,
               s.team_count > 1 AS traded, s.gp, s.min, s.pts, s.reb, s.ast
        {PLAYER_SEASON_FILTER}
        ORDER BY p.name, p.id
        LIMIT :limit OFFSET :offset
        """,
        params,
    ).fetchall()
    return Page(items=[PlayerSeason(**dict(r)) for r in rows], total=total, limit=limit, offset=offset)


@app.get("/v1/players/{player_id}/seasons", response_model=Page[SeasonLine], dependencies=RAW_ACCESS)
def list_player_seasons(
    player_id: int,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(require_raw_tables),
):
    if db.execute("SELECT 1 FROM players WHERE id = ?", (player_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Player not found")
    total = db.execute("SELECT COUNT(*) FROM player_season_stats WHERE player_id = ?", (player_id,)).fetchone()[0]
    rows = db.execute(
        """
        SELECT s.season_id AS season, t.abbreviation AS team, s.team_count > 1 AS traded,
               s.gp AS games, s.min AS minutes, s.pts, s.reb, s.ast
        FROM player_season_stats s
        JOIN teams t ON t.id = s.team_id
        WHERE s.player_id = :player_id
        ORDER BY s.season_id
        LIMIT :limit OFFSET :offset
        """,
        {"player_id": player_id, "limit": limit, "offset": offset},
    ).fetchall()
    return Page(items=[SeasonLine(**dict(r)) for r in rows], total=total, limit=limit, offset=offset)


@app.get("/v1/players/{player_id}/progression", response_model=Progression)
def player_progression(
    player_id: int,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    if db.execute("SELECT 1 FROM players WHERE id = ?", (player_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Player not found")
    trend = db.execute(
        """SELECT COUNT(*) AS changes, COALESCE(SUM(small_sample), 0) AS small_sample_changes,
                  ROUND(AVG(minutes_change), 2) AS minutes_per_season, ROUND(AVG(pts_change), 2) AS pts_per_season,
                  ROUND(AVG(reb_change), 2) AS reb_per_season, ROUND(AVG(ast_change), 2) AS ast_per_season
           FROM player_progression WHERE player_id = ?""", (player_id,)).fetchone()
    rows = db.execute(
        """SELECT season, previous_season, seasons_apart, games_change, minutes_change, pts_change,
                  reb_change, ast_change, traded, small_sample
           FROM player_progression WHERE player_id = ? ORDER BY season LIMIT ? OFFSET ?""",
        (player_id, limit, offset)).fetchall()
    return Progression(
        player_id=player_id, items=[SeasonChange(**dict(r)) for r in rows], total=trend["changes"],
        limit=limit, offset=offset, trend=Trend(**dict(trend)),
    )


@app.get("/v1/prospects", response_model=Page[ProspectSummary])
def list_prospects(
    draft_year: int | None = Query(None, ge=2000, le=2100),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    where, params = ("WHERE p.draft_year = :year", {"year": draft_year}) if draft_year else ("", {})
    total = db.execute(f"SELECT COUNT(*) FROM prospects p {where}", params).fetchone()[0]
    rows = db.execute(
        f"""SELECT p.id, p.name, p.draft_year, p.draft_pick, p.school, p.conference,
                   s.psi, s.psi_rank, s.class_size, p.missing_box_flag, s.small_postseason_sample, p.college_link
            FROM prospects p LEFT JOIN prospect_scores s ON s.prospect_id = p.id
            {where}
            ORDER BY p.draft_year IS NULL, p.draft_year, s.psi IS NULL, s.psi DESC, p.name, p.id
            LIMIT :limit OFFSET :offset""",
        {**params, "limit": limit, "offset": offset}).fetchall()
    return Page(items=[ProspectSummary(**dict(r)) for r in rows], total=total, limit=limit, offset=offset)


@app.get("/v1/prospects/{prospect_id}/psi", response_model=ProspectPSI)
def prospect_psi(prospect_id: int, db: sqlite3.Connection = Depends(get_db)):
    p = db.execute("SELECT id, name, draft_year, missing_box_flag, college_link FROM prospects WHERE id = ?",
                   (prospect_id,)).fetchone()
    if p is None:
        raise HTTPException(status_code=404, detail="Prospect not found")
    s = db.execute("SELECT * FROM prospect_scores WHERE prospect_id = ?", (prospect_id,)).fetchone()
    if s is None:
        raise HTTPException(status_code=404, detail="No PSI for this prospect")

    def status(name: str) -> str:
        if name in ("intangibles", "team_fit"):
            return "neutral: hand-entered, not scored"
        if name == "physical" and s["physical_source"] == "none":
            return "neutral: no combine measurements"
        return "scored"

    return ProspectPSI(
        id=p["id"], name=p["name"], draft_year=p["draft_year"], psi_version=s["psi_version"], psi=s["psi"],
        psi_rank=s["psi_rank"], class_size=s["class_size"],
        components=[Component(name=n, weight=w, score=s[n], status=status(n)) for n, w in PSI_WEIGHTS.items()],
        skill_dimensions=json.loads(s["skill_dimensions"]), missing_inputs=json.loads(s["missing_inputs"]),
        notes=json.loads(s["notes"]),
        flags=PSIFlags(missing_box_flag=None if p["missing_box_flag"] is None else bool(p["missing_box_flag"]),
                       college_link=p["college_link"], position=s["position"],
                       position_source=s["position_source"], physical_source=s["physical_source"],
                       age_source=s["age_source"], competition_level=s["competition_level"]),
        tournament_split=TournamentSplit(
            final_season=s["final_season"], postseason_games=s["postseason_games"],
            small_postseason_sample=None if s["small_postseason_sample"] is None else bool(s["small_postseason_sample"]),
            regular_pts_per40=s["regular_pts_per40"], postseason_pts_per40=s["postseason_pts_per40"],
            regular_ts_pct=s["regular_ts_pct"], postseason_ts_pct=s["postseason_ts_pct"]),
    )


@app.get("/v1/players/{player_id}", response_model=PlayerProfile, dependencies=RAW_ACCESS)
def get_player(player_id: int, db: sqlite3.Connection = Depends(require_raw_tables)):
    row = db.execute(
        """
        SELECT p.id, p.name,
               (SELECT t.abbreviation FROM player_season_stats s2
                  JOIN teams t ON t.id = s2.team_id
                 WHERE s2.player_id = p.id
                 ORDER BY s2.season_id DESC LIMIT 1) AS team,
               MIN(s.season_id) AS first_season,
               MAX(s.season_id) AS last_season,
               COUNT(*) AS seasons_played
        FROM players p
        JOIN player_season_stats s ON s.player_id = p.id
        WHERE p.id = ?
        GROUP BY p.id
        """,
        (player_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return PlayerProfile(**dict(row))
