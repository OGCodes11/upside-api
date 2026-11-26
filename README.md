# Upside

**Live API:** [upside-api-5nat.onrender.com/docs](https://upside-api-5nat.onrender.com/docs) — interactive docs; try any endpoint in the browser. It runs on a free plan that sleeps when idle, so the first request after a quiet spell takes about a minute.

## The result first: PSI does not beat draft slot

Upside scores college prospects with a Prospect Strength Index (PSI). Before trusting it, I tested it: did PSI rank the 2012–2021 draft classes by their Year-3 NBA minutes better than the draft order did?

**It did not.** PSI's rank correlation with Year-3 minutes was **0.15–0.18**. Draft slot's was **0.58–0.62**. PSI picks up some signal: players in its top fifth averaged 1,109 Year-3 minutes, against 708 for its bottom fifth. But it is nowhere near the baseline.

| Run | Classes | Players | PSI | Draft slot |
| --- | --- | --- | --- | --- |
| A: all classes | 2012–2021 | 486 | 0.153 | 0.594 |
| B: 2012–2014 excluded (missing box scores) | 2015–2021 | 343 | 0.149 | 0.577 |
| C: classes the scoring benchmarks never saw | 2012–2017 | 284 | 0.181 | 0.619 |

The method, the outcome measure and all three runs were written down and committed before any score existed. Nothing was tuned after the results came in. The scores served by this API are the same v1 scores that were tested, so please read them with this result in mind.

- One-page write-up, with the limits: [docs/PHASE2_RESULT.md](docs/PHASE2_RESULT.md)
- Full output: [docs/BACKTEST_RESULTS.md](docs/BACKTEST_RESULTS.md)

## What Upside is

Upside is a basketball player-evaluation service, built as a FastAPI REST API over SQLite. It:

- collects NBA and Division I college stats,
- scores draft prospects with PSI: a weighted sum of Production 25%, Skills 25%, Intangibles 20%, Physical 15%, Team Fit 10% and Risk 5%,
- tracks how NBA players change from season to season,
- and serves **derived data only**: scores, ranks, flags and year-over-year changes. Raw stat lines from the sources are never served publicly.

**Intangibles and Team Fit are hand-entered by design, so in v1 they sit at a neutral 50 for every prospect.** The API shows them as neutral rather than hiding them.

**Some signals stay separate on purpose.** Tournament play is shown as its own split next to PSI, not mixed into it. Small samples and data gaps carry explicit flags:

- `small_postseason_sample`: fewer than 5 postseason games.
- `missing_box_flag`: more than 10% of his teams' pre-draft games have no box score.
- `small_sample`: either season of a year-over-year change had fewer than 20 games.

## Endpoints

Every path starts with `/v1`. List endpoints are paged with `limit` (1–100, default 25) and `offset`, and return `{items, total, limit, offset}`. Interactive docs are at `/docs` while the server is running.

The examples call the live API. To call a local server instead, replace `https://upside-api-5nat.onrender.com` with `http://127.0.0.1:8000`.

### `GET /v1/prospects`

Prospects with their PSI, rank within the draft class, and flags. Filter by `draft_year`. Prospects who can't be scored (no Division I season, or undrafted) are still listed, with `psi: null`.

```bash
curl "https://upside-api-5nat.onrender.com/v1/prospects?draft_year=2017&limit=2"
```

```json
{
  "items": [
    {"id": 543, "name": "Zach Collins", "draft_year": 2017, "draft_pick": 10, "school": "Gonzaga",
     "conference": "WCC", "psi": 63.5, "psi_rank": 1, "class_size": 51, "missing_box_flag": false,
     "small_postseason_sample": false, "college_link": "cbbd_draft"},
    {"id": 535, "name": "Lonzo Ball", "draft_year": 2017, "draft_pick": 2, "school": "California-Los Angeles",
     "conference": "Pac-12", "psi": 60.9, "psi_rank": 2, "class_size": 51, "missing_box_flag": false,
     "small_postseason_sample": true, "college_link": "cbbd_draft"}
  ],
  "total": 60, "limit": 2, "offset": 0
}
```

PSI ranks Zach Collins first in the 2017 class, ahead of Jayson Tatum and Donovan Mitchell. That is the Phase 2 result in one example.

### `GET /v1/prospects/{id}/psi`

The full breakdown for one prospect:

- the six components, each with its weight, score and status,
- the six skill scores,
- the inputs the scorer was missing,
- the flags,
- and the tournament split.

```bash
curl "https://upside-api-5nat.onrender.com/v1/prospects/543/psi"
```

```json
{
  "id": 543, "name": "Zach Collins", "draft_year": 2017, "psi_version": "v1-neutral-hand-inputs",
  "psi": 63.5, "psi_rank": 1, "class_size": 51,
  "components": [
    {"name": "production", "weight": 0.25, "score": 55.4, "status": "scored"},
    {"name": "skills", "weight": 0.25, "score": 81.0, "status": "scored"},
    {"name": "intangibles", "weight": 0.2, "score": 50.0, "status": "neutral: hand-entered, not scored"},
    {"name": "physical", "weight": 0.15, "score": 74.8, "status": "scored"},
    {"name": "team_fit", "weight": 0.1, "score": 50.0, "status": "neutral: hand-entered, not scored"},
    {"name": "risk", "weight": 0.05, "score": 64.0, "status": "scored"}
  ],
  "skill_dimensions": {"scoring": 85.9, "playmaking": 19.5, "defense": 97.9, "shooting": 84.1,
                       "iq": 51.6, "athleticism": 97.9},
  "missing_inputs": ["BPM"],
  "notes": [],
  "flags": {"missing_box_flag": false, "college_link": "cbbd_draft", "position": "PF",
            "position_source": "combine", "physical_source": "combine", "age_source": "nba",
            "competition_level": "NCAA_D1"},
  "tournament_split": {"final_season": 2017, "postseason_games": 6, "small_postseason_sample": false,
                       "regular_pts_per40": 23.8, "postseason_pts_per40": 20.0,
                       "regular_ts_pct": 0.72, "postseason_ts_pct": 0.694}
}
```

The endpoint returns 404 for an unknown id, and 404 "No PSI for this prospect" for a prospect who is listed but can't be scored.

### `GET /v1/players/{id}/progression`

Year-over-year changes between an NBA player's seasons, in per-game minutes, points, rebounds and assists, plus his average change per season. Player ids are NBA.com ids. The response contains no raw season lines.

```bash
curl "https://upside-api-5nat.onrender.com/v1/players/1628369/progression?limit=1"
```

```json
{
  "items": [
    {"season": "2018-19", "previous_season": "2017-18", "seasons_apart": 1, "games_change": -1,
     "minutes_change": 0.6, "pts_change": 1.8, "reb_change": 1.0, "ast_change": 0.5,
     "traded": false, "small_sample": false}
  ],
  "total": 8, "limit": 1, "offset": 0,
  "player_id": 1628369,
  "trend": {"changes": 8, "small_sample_changes": 1, "minutes_per_season": 0.26,
            "pts_per_season": 0.99, "reb_per_season": 0.63, "ast_per_season": 0.46}
}
```

The trend covers every change, not just the current page. `seasons_apart` is more than 1 when the player missed a season in between.

### Raw endpoints (local only)

`/v1/players`, `/v1/players/{id}` and `/v1/players/{id}/seasons` serve raw NBA season lines, for local analysis.

- **With `PUBLIC_MODE=1`,** they need an `X-API-Key` header that matches `UPSIDE_API_KEY`, and return 401 without it.
- **The public snapshot has no raw tables,** so even with the key they return 503 there.

### Errors

| Status | When |
| --- | --- |
| 404 | Unknown id |
| 422 | Bad input, e.g. `limit=0` or `draft_year=abc` |
| 401 | A raw endpoint in public mode without a valid key |
| 503 | A raw endpoint on a database without raw tables |

## Running it locally

You need Python 3.13. The repository has no data: you build the database yourself from the sources below.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # then add your CBBD API key
```

**Pull and load the data.** Every response is cached in `data/raw/` and never requested twice, and requests are spaced out. The CBBD free tier allows 1,000 calls a month.

```bash
python -m ingest.nba 2010-11 --to 2025-26
python -m ingest.load_nba 2010-11 --to 2025-26
python -m ingest.nba_draft 2010 --to 2026
python -m ingest.load_draft 2010 --to 2026
python -m ingest.cbbd 2006 --to 2026
python -m ingest.load_cbbd 2006 --to 2026
python -m ingest.cbbd 2008 --to 2026 --games
python -m ingest.gleague
python -m ingest.load_games 2008 --to 2026
python -m ingest.nba_bio --drafted 2012 --to 2021
```

**Build the public snapshot and run the API on it.** The snapshot holds only the four derived tables the public endpoints need. The builder leaves out every player from the college programs listed in `data/excluded_teams.txt`, one per line. Without that file it stops, unless you pass `--no-exclusions`.

```bash
python -m scripts.build_public_db --no-exclusions    # or list programs in data/excluded_teams.txt
UPSIDE_DB_PATH=data/public.db uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000/docs.

**Run the tests.** They build their own small databases and need no data.

```bash
pytest
```

**Rerun the Phase 2 backtest:**

```bash
python -m scoring.backtest
```

### Settings (`.env`)

| Variable | Purpose |
| --- | --- |
| `CBBD_API_KEY` | CollegeBasketballData.com key, for pulling college data only |
| `UPSIDE_DB_PATH` | Database the API reads. Defaults to `data/upside.db` |
| `PUBLIC_MODE` | Set to `1` on a public deployment to lock the raw endpoints |
| `UPSIDE_API_KEY` | The key the raw endpoints accept in public mode |

`.env` and `data/` are never committed.

## Data sources and credit

- **NBA.com:** NBA season stats, draft history, combine measurements, birth dates and G League game logs, pulled with the [nba_api](https://github.com/swar/nba_api) library.
- **[CollegeBasketballData.com](https://collegebasketballdata.com):** Division I player seasons and per-game box scores.

Thanks to both, and to the nba_api maintainers. Neither source's raw data is included in this repository or served by the public API. NBA.com's terms don't allow redistributing its data, and CBBD's terms don't allow republishing its raw data. Only derived scores, ranks, flags and changes are served.

## License

The code is released under the [MIT License](LICENSE). The license covers the code only: the data it pulls stays under each source's own terms (see above).

## Unofficial

Upside is an independent project. It is not affiliated with, endorsed by or connected to the NBA, the NCAA, NBA.com or CollegeBasketballData.com. The data is unofficial and may contain errors or gaps; known gaps are flagged, not filled. The scores are an experiment, and the result above says how well they did. Don't use them to make decisions about real players.
