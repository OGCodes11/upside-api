import json
import os
import re
import secrets
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Generic, TypeVar

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Security
from fastapi.openapi.utils import get_openapi
from fastapi.responses import RedirectResponse
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


DESCRIPTION = """
Scores for NBA draft prospects and season-to-season changes for NBA players. **Derived data only**:
scores, ranks, flags and year-over-year changes. No raw stat lines from the sources.

**Read the scores with this result in mind.** In a pre-registered backtest of the 2012–2021 draft classes,
PSI ranked players by Year-3 NBA minutes far worse than draft slot did: rank correlation **0.15–0.18**
for PSI against **0.58–0.62** for draft slot.
[The one-page result](https://github.com/OGCodes11/upside-api/blob/main/docs/PHASE2_RESULT.md) ·
[Source code](https://github.com/OGCodes11/upside-api)

**How to use this page:** open an endpoint, fill in the fields, and press **Execute**.
A good first try: **List prospects** with `draft_year` 2017, then **PSI breakdown** with `prospect_id` 543.

The live service runs on a free plan that sleeps when idle, so the first request after a quiet spell
can take about a minute. Unofficial: not affiliated with the NBA, the NCAA, NBA.com or CollegeBasketballData.com.
"""

TAGS = [
    {"name": "Prospects", "description": "Draft prospects and their PSI (Prospect Strength Index) scores."},
    {"name": "Players", "description": "Year-over-year changes for NBA players."},
    {"name": "Raw data (local only)",
     "description": "Raw NBA season lines for local analysis. Hidden and locked on the public service."},
]
RAW_TAG = "Raw data (local only)"

app = FastAPI(
    title="Upside API",
    version="0.5",
    description=DESCRIPTION,
    openapi_tags=TAGS,
    lifespan=lifespan,
    swagger_ui_parameters={
        "tryItOutEnabled": True,  # fields are editable without clicking "Try it out" first
        "defaultModelsExpandDepth": -1,  # hide the "Schemas" list at the bottom
        "docExpansion": "list",
        "displayRequestDuration": True,
    },
)

LIMIT = Query(25, ge=1, le=100, description="Results per page, 1 to 100")
OFFSET = Query(0, ge=0, description="Results to skip. With limit=25: 0 for page 1, 25 for page 2, and so on")
NOT_FOUND = {404: {"description": "No record with that id"}}


def openapi_schema() -> dict:
    """The docs schema. In public mode it leaves out the raw endpoints, which are locked there anyway."""
    schema = get_openapi(title=app.title, version=app.version, description=app.description,
                         tags=app.openapi_tags, routes=app.routes)
    if public_mode():
        schema["tags"] = [t for t in schema.get("tags", []) if t["name"] != RAW_TAG]
        schema["paths"] = {path: ops for path, ops in schema["paths"].items()
                           if not any(RAW_TAG in op.get("tags", []) for op in ops.values())}
        components = schema.get("components", {})
        components.pop("securitySchemes", None)  # no "Authorize" button for endpoints that aren't shown
        text = json.dumps(schema["paths"])
        used, pending = set(), {m for m in re.findall(r"#/components/schemas/([\w-]+)", text)}
        while pending:  # keep only the models the remaining endpoints use
            name = pending.pop()
            if name not in used:
                used.add(name)
                pending |= set(re.findall(r"#/components/schemas/([\w-]+)", json.dumps(components["schemas"][name])))
        components["schemas"] = {k: v for k, v in components.get("schemas", {}).items() if k in used}
    return schema


app.openapi = openapi_schema


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """The bare URL opens the interactive docs instead of a 404."""
    return RedirectResponse("/docs")

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


@app.get("/v1/players", response_model=Page[PlayerSeason], dependencies=RAW_ACCESS, tags=[RAW_TAG],
         summary="List NBA season lines")
def list_players(
    season: str | None = Query(None, pattern=SEASON_PATTERN, description="Defaults to the latest season"),
    team: str | None = Query(None, description="Team abbreviation, e.g. GSW"),
    limit: int = LIMIT,
    offset: int = OFFSET,
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


@app.get("/v1/players/{player_id}/seasons", response_model=Page[SeasonLine], dependencies=RAW_ACCESS,
         tags=[RAW_TAG], summary="One player's NBA season lines", responses=NOT_FOUND)
def list_player_seasons(
    player_id: int = Path(description="NBA.com player id"),
    limit: int = LIMIT,
    offset: int = OFFSET,
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


@app.get("/v1/players/{player_id}/progression", response_model=Progression, tags=["Players"],
         summary="Season-to-season changes",
         description="How a player's per-game numbers changed from each NBA season to his next one, plus "
                     "`trend`: his average change per season across all of them. No raw season lines.\n\n"
                     "`small_sample` marks a change where either season had fewer than 20 games. "
                     "`seasons_apart` is more than 1 when he missed a season in between.", responses=NOT_FOUND)
def player_progression(
    player_id: int = Path(description="NBA.com player id, e.g. 1628369 (Jayson Tatum)"),
    limit: int = LIMIT,
    offset: int = OFFSET,
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


@app.get("/v1/prospects", response_model=Page[ProspectSummary], tags=["Prospects"], summary="List prospects",
         description="Every draft prospect with his PSI (0-100), his rank within his draft class (1 = highest) "
                     "and the data-quality flags. Sorted by class, then PSI. Prospects PSI can't score (no "
                     "Division I season, or undrafted) are listed with `psi: null`.\n\n"
                     "**Flags:** `missing_box_flag`: over 10% of his teams' pre-draft games have no box score. "
                     "`small_postseason_sample`: fewer than 5 postseason games. `college_link`: how he was "
                     "matched to his college record.")
def list_prospects(
    draft_year: int | None = Query(None, ge=2000, le=2100,
                                   description="Only this draft class, e.g. 2017. Leave empty for every class"),
    limit: int = LIMIT,
    offset: int = OFFSET,
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


@app.get("/v1/prospects/{prospect_id}/psi", response_model=ProspectPSI, tags=["Prospects"],
         summary="PSI breakdown",
         description="The six PSI components with their weights and scores, the six skill scores, the inputs "
                     "the scorer was missing, and the flags. **Intangibles and Team Fit are hand-entered by "
                     "design, so in v1 they are a neutral 50 for everyone**, and say so in `status`.\n\n"
                     "`tournament_split` compares his regular season with his postseason. It is kept apart from "
                     "PSI on purpose and never changes the score.", responses={404: {"description": "Unknown prospect, or one with no PSI"}})
def prospect_psi(
    prospect_id: int = Path(description="Prospect id from List prospects, e.g. 543"),
    db: sqlite3.Connection = Depends(get_db),
):
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


@app.get("/v1/players/{player_id}", response_model=PlayerProfile, dependencies=RAW_ACCESS, tags=[RAW_TAG],
         summary="One player's career summary", responses=NOT_FOUND)
def get_player(
    player_id: int = Path(description="NBA.com player id"),
    db: sqlite3.Connection = Depends(require_raw_tables),
):
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
