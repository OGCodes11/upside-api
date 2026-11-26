-- Upside database schema (SQLite). Safe to run more than once.

CREATE TABLE IF NOT EXISTS teams (
    id            INTEGER PRIMARY KEY,          -- NBA.com team id, stable across relocations
    abbreviation  TEXT    NOT NULL UNIQUE,      -- current abbreviation, e.g. GSW
    name          TEXT    NOT NULL,             -- e.g. Golden State Warriors
    city          TEXT
);

CREATE TABLE IF NOT EXISTS seasons (
    id          TEXT    PRIMARY KEY,            -- e.g. 2025-26
    start_year  INTEGER NOT NULL UNIQUE         -- e.g. 2025
);

CREATE TABLE IF NOT EXISTS players (
    id    INTEGER PRIMARY KEY,                  -- NBA.com player id
    name  TEXT    NOT NULL
);

-- One row per player per regular season. Stats are per game.
-- A player traded mid-season has one combined row, listed under his last team (team_count > 1).
CREATE TABLE IF NOT EXISTS player_season_stats (
    player_id   INTEGER NOT NULL REFERENCES players(id),
    season_id   TEXT    NOT NULL REFERENCES seasons(id),
    team_id     INTEGER NOT NULL REFERENCES teams(id),
    team_count  INTEGER NOT NULL DEFAULT 1,
    age         REAL,
    gp          INTEGER NOT NULL,
    min         REAL,
    pts         REAL,
    reb         REAL,
    oreb        REAL,
    dreb        REAL,
    ast         REAL,
    stl         REAL,
    blk         REAL,
    tov         REAL,
    pf          REAL,
    fgm         REAL,
    fga         REAL,
    fg3m        REAL,
    fg3a        REAL,
    ftm         REAL,
    fta         REAL,
    plus_minus  REAL,
    PRIMARY KEY (player_id, season_id)
);

CREATE INDEX IF NOT EXISTS idx_pss_season_team ON player_season_stats (season_id, team_id);

-- Draft prospects: everyone drafted, plus every combine invitee who wasn't.
-- nba_person_id is NBA.com's id for the person. It equals players.id once he plays an NBA game,
-- but many prospects never do, so it is not a foreign key. Join with players ON players.id = nba_person_id.
CREATE TABLE IF NOT EXISTS prospects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nba_person_id   INTEGER NOT NULL UNIQUE,
    name            TEXT    NOT NULL,
    position        TEXT,                       -- from his latest combine
    school          TEXT,                       -- college, club or high school he was drafted from
    school_type     TEXT,                       -- e.g. College/University, Other Team/Club
    conference      TEXT,                       -- filled from college data later
    birth_date      TEXT,                       -- YYYY-MM-DD, filled from college data later
    draft_year      INTEGER,                    -- NULL if never drafted
    draft_round     INTEGER,
    draft_pick      INTEGER,                    -- overall pick
    draft_team_id   INTEGER REFERENCES teams(id),
    cbbd_athlete_id INTEGER,                    -- his college player id (college_player_seasons.athlete_id)
    college_link    TEXT,                       -- how that id was matched; see ingest/load_cbbd.py
    missing_box_share REAL,                     -- share of his teams' pre-draft games with no box score
                                                -- in the confirmed-gap seasons; NULL if not computable
    missing_box_flag  INTEGER                   -- 1 when missing_box_share > 0.10 (pre-registered Nov 22 2026; docs/PHASE2_RESULT.md)
);

-- Division I player seasons from CollegeBasketballData.com. Stats are season TOTALS, not per game.
-- Only D1 teams are kept: non-D1 rows cover just their games against D1 opponents.
-- A player who changed teams mid-season has one row per team.
CREATE TABLE IF NOT EXISTS college_player_seasons (
    athlete_id   INTEGER NOT NULL,              -- CBBD athlete id
    season       INTEGER NOT NULL,              -- ending year: 2026 = 2025-26
    team_id      INTEGER NOT NULL,
    team         TEXT    NOT NULL,
    conference   TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    position     TEXT,
    games        INTEGER,
    starts       INTEGER,
    minutes      REAL,
    points       INTEGER,
    fgm          INTEGER,
    fga          INTEGER,
    fg3m         INTEGER,
    fg3a         INTEGER,
    ftm          INTEGER,
    fta          INTEGER,
    oreb         INTEGER,
    dreb         INTEGER,
    reb          INTEGER,
    ast          INTEGER,
    stl          INTEGER,
    blk          INTEGER,
    tov          INTEGER,
    pf           INTEGER,
    usage        REAL,
    ortg         REAL,                          -- offensive rating
    drtg         REAL,                          -- defensive rating
    porpag       REAL,                          -- points over replacement per adjusted game
    ts_pct       REAL,
    efg_pct      REAL,
    win_shares   REAL,
    PRIMARY KEY (athlete_id, season, team_id)
);

CREATE INDEX IF NOT EXISTS idx_cps_season_team ON college_player_seasons (season, team);

-- One row per game, for every pre-NBA league we track. Season stats are built from these,
-- so postseason can be included or left out with one filter (see app/game_stats.py).
CREATE TABLE IF NOT EXISTS games (
    league      TEXT    NOT NULL CHECK (league IN ('ncaa_d1', 'g_league')),  -- OTE, international later
    game_id     TEXT    NOT NULL,               -- the source's own game id
    season      INTEGER NOT NULL,               -- ending year: 2026 = 2025-26
    start_date  TEXT    NOT NULL,               -- YYYY-MM-DD
    game_type   TEXT    NOT NULL CHECK (game_type IN (
                    'regular_season', 'conference_tournament',
                    'ncaa_tournament', 'nit', 'other_postseason',  -- other: CBI, CIT and similar
                    'g_league_playoffs')),
    tournament  TEXT,                           -- source label, e.g. NCAA, NIT, CBI
    notes       TEXT,                           -- e.g. "ACC TOURNAMENT - SEMIFINAL"
    has_box_score INTEGER NOT NULL DEFAULT 1,   -- 0: the source lists the game but has no player stats
                                                -- (a cancelled game, or a gap in the source; see verify_games)
    PRIMARY KEY (league, game_id)
);

-- One row per player per game. player_id is the CBBD athlete id for ncaa_d1
-- and the NBA.com person id for g_league (the same id as prospects.nba_person_id).
CREATE TABLE IF NOT EXISTS player_game_stats (
    league       TEXT    NOT NULL,
    game_id      TEXT    NOT NULL,
    player_id    INTEGER NOT NULL,
    name         TEXT    NOT NULL,
    team         TEXT    NOT NULL,
    opponent     TEXT,
    starter      INTEGER,                       -- 1/0, NULL if the source doesn't say
    minutes      REAL,
    pts          INTEGER,
    fgm          INTEGER,
    fga          INTEGER,
    fg3m         INTEGER,
    fg3a         INTEGER,
    ftm          INTEGER,
    fta          INTEGER,
    oreb         INTEGER,
    dreb         INTEGER,
    reb          INTEGER,
    ast          INTEGER,
    stl          INTEGER,
    blk          INTEGER,
    tov          INTEGER,
    pf           INTEGER,
    stats_as_of  TEXT    NOT NULL,              -- UTC time this row's data was pulled from the source
    source       TEXT,                          -- cbbd, nba_stats, or manual (ingest/manual/*.json)
    PRIMARY KEY (league, game_id, player_id),
    FOREIGN KEY (league, game_id) REFERENCES games (league, game_id)
);

-- Which teams played in each game. Needed for games with no box score, which have no player rows
-- to say who played. Only D1 teams are listed for ncaa_d1.
CREATE TABLE IF NOT EXISTS game_teams (
    league      TEXT NOT NULL,
    game_id     TEXT NOT NULL,
    team        TEXT NOT NULL,
    conference  TEXT,
    PRIMARY KEY (league, game_id, team),
    FOREIGN KEY (league, game_id) REFERENCES games (league, game_id)
);

CREATE INDEX IF NOT EXISTS idx_game_teams_team ON game_teams (league, team);

-- Derived tables: what the public endpoints serve. Built by scoring/derived.py; no raw stat lines.

-- One row per scored prospect. Same rules as the Phase 2 backtest (docs/PHASE2_RESULT.md).
CREATE TABLE IF NOT EXISTS prospect_scores (
    prospect_id        INTEGER PRIMARY KEY REFERENCES prospects(id),
    psi_version        TEXT    NOT NULL,         -- e.g. v1-neutral-hand-inputs
    psi                REAL    NOT NULL,
    psi_rank           INTEGER NOT NULL,         -- 1 = highest PSI in his draft class
    class_size         INTEGER NOT NULL,         -- scored prospects in that class
    production         REAL    NOT NULL,
    skills             REAL    NOT NULL,
    intangibles        REAL    NOT NULL,         -- 50: hand-entered, not scored
    physical           REAL    NOT NULL,
    team_fit           REAL    NOT NULL,         -- 50: hand-entered, not scored
    risk               REAL    NOT NULL,
    skill_dimensions   TEXT    NOT NULL,         -- JSON: the six skill scores
    missing_inputs     TEXT    NOT NULL,         -- JSON list
    notes              TEXT    NOT NULL,         -- JSON list, e.g. a rating treated as missing
    position           TEXT    NOT NULL,
    position_source    TEXT    NOT NULL,         -- combine or cbbd
    physical_source    TEXT    NOT NULL,         -- combine or none
    age_source         TEXT    NOT NULL,         -- nba or none
    competition_level  TEXT    NOT NULL,
    final_season       INTEGER NOT NULL,
    postseason_games   INTEGER,                  -- NULL when no per-game data for that season
    small_postseason_sample INTEGER,             -- 1 when postseason_games < 5; NULL as above
    regular_pts_per40  REAL,                     -- tournament split, kept apart from PSI
    postseason_pts_per40 REAL,
    regular_ts_pct     REAL,
    postseason_ts_pct  REAL,
    scored_at          TEXT    NOT NULL
);

-- Year-over-year change between a player's NBA seasons. Per-game differences only.
CREATE TABLE IF NOT EXISTS player_progression (
    player_id        INTEGER NOT NULL,
    season           TEXT    NOT NULL,
    previous_season  TEXT    NOT NULL,
    seasons_apart    INTEGER NOT NULL,           -- more than 1 when he missed a season in between
    games_change     INTEGER,
    minutes_change   REAL,
    pts_change       REAL,
    reb_change       REAL,
    ast_change       REAL,
    traded           INTEGER NOT NULL,           -- 1 if he was traded in either season
    small_sample     INTEGER NOT NULL,           -- 1 if either season had fewer than 20 games
    PRIMARY KEY (player_id, season)
);
CREATE INDEX IF NOT EXISTS idx_pgs_player ON player_game_stats (league, player_id);
CREATE INDEX IF NOT EXISTS idx_games_season_type ON games (league, season, game_type);

-- Official NBA Draft Combine measurements and testing. One row per prospect per combine
-- (some players attend twice). NULL means he skipped that test, not a zero.
CREATE TABLE IF NOT EXISTS combine (
    prospect_id          INTEGER NOT NULL REFERENCES prospects(id),
    combine_year         INTEGER NOT NULL,
    height_in            REAL,                  -- without shoes
    height_shoes_in      REAL,
    weight_lb            REAL,
    wingspan_in          REAL,
    standing_reach_in    REAL,
    body_fat_pct         REAL,
    hand_length_in       REAL,
    hand_width_in        REAL,
    standing_vert_in     REAL,
    max_vert_in          REAL,
    lane_agility_s       REAL,
    modified_lane_agility_s REAL,
    sprint_s             REAL,                  -- three-quarter court sprint
    bench_press_reps     INTEGER,
    PRIMARY KEY (prospect_id, combine_year)
);
