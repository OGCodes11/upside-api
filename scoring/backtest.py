"""Phase 2 backtest: does PSI rank 2012-2021 draftees by Year-3 NBA minutes better than draft slot?

Run from the project root:  python -m scoring.backtest
Method pre-registered Nov 23, 2026, before any score existed (docs/PHASE2_RESULT.md). Writes:
  docs/BACKTEST_RESULTS.md       the one-page result
  data/backtest_scores.csv       every scored prospect (not committed)
"""

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

from app.db import connect, db_path
from scoring.inputs import build_inputs
from scoring.score_prospect import score_prospect

RUNS = {
    "A": ("All classes", 2012, 2021),
    "B": ("2012-2014 excluded", 2015, 2021),
    "C": ("Classes the Production benchmarks never saw", 2012, 2017),
}
RESULTS_PATH = Path("docs/BACKTEST_RESULTS.md")
SCORES_PATH = Path("data/backtest_scores.csv")


def year3_season(draft_year: int) -> str:
    """Third NBA season: a 2012 pick's is 2014-15."""
    start = draft_year + 2
    return f"{start}-{str(start + 1)[-2:]}"


def year3_minutes(conn: sqlite3.Connection, nba_person_id: int, draft_year: int) -> float:
    row = conn.execute(
        "SELECT gp * min FROM player_season_stats WHERE player_id = ? AND season_id = ?",
        (nba_person_id, year3_season(draft_year))).fetchone()
    return round(row[0], 1) if row and row[0] is not None else 0.0


def score_population(conn: sqlite3.Connection, first: int = 2012, last: int = 2021) -> pd.DataFrame:
    rows = []
    for prospect_id, person_id, draft_year in conn.execute(
        "SELECT id, nba_person_id, draft_year FROM prospects WHERE draft_year BETWEEN ? AND ?"
        " AND draft_pick IS NOT NULL ORDER BY draft_year, draft_pick", (first, last)).fetchall():
        inputs = build_inputs(conn, prospect_id)
        if inputs is None:
            continue
        s = score_prospect(inputs)
        rows.append({
            "draft_year": draft_year, "pick": inputs.draft_pick, "name": inputs.name, "psi": s.psi,
            **{f"c_{k}": v for k, v in s.components.items()},
            "year3_minutes": year3_minutes(conn, person_id, draft_year),
            "position": inputs.position, "position_source": inputs.position_source,
            "conference": inputs.conference, "final_season": inputs.final_season,
            "physical_source": s.physical_source, "age": inputs.age, "age_source": inputs.age_source,
            "missing_box_flag": inputs.missing_box_flag, "missing_inputs": "; ".join(s.missing_inputs),
            "notes": "; ".join(inputs.notes),
        })
    return pd.DataFrame(rows)


def spearman(a: pd.Series, b: pd.Series) -> float:
    return round(a.rank().corr(b.rank()), 3)  # Spearman = Pearson on ranks (ties get average ranks)


def run(df: pd.DataFrame, first: int, last: int) -> dict:
    sub = df[df["draft_year"].between(first, last)]
    return {
        "n": len(sub),
        "psi_rho": spearman(sub["psi"], sub["year3_minutes"]),
        "slot_rho": spearman(-sub["pick"], sub["year3_minutes"]),  # earlier pick = higher
        "flagged": int(sub["missing_box_flag"].sum()),
        "no_combine": int((sub["physical_source"] == "none").sum()),
    }


def quintiles(df: pd.DataFrame, first: int, last: int) -> pd.DataFrame:
    sub = df[df["draft_year"].between(first, last)].copy()
    sub["psi_quintile"] = pd.qcut(sub["psi"].rank(method="first"), 5, labels=["1 (lowest)", "2", "3", "4", "5 (highest)"])
    return sub.groupby("psi_quintile", observed=True).agg(
        players=("psi", "size"), psi_range=("psi", lambda s: f"{s.min():.1f}-{s.max():.1f}"),
        avg_pick=("pick", "mean"), avg_year3_minutes=("year3_minutes", "mean"),
        share_with_0_minutes=("year3_minutes", lambda s: (s == 0).mean()),
    ).round(1)


def markdown_table(table: pd.DataFrame) -> str:
    header = [table.index.name or ""] + [str(c) for c in table.columns]
    rows = [[str(i)] + [str(v) for v in row] for i, row in zip(table.index, table.itertuples(index=False))]
    return "\n".join(["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
                     + ["| " + " | ".join(r) + " |" for r in rows])


def write_results(df: pd.DataFrame, results: dict, path: Path = RESULTS_PATH) -> None:
    lines = [
        "# Phase 2 backtest: PSI vs draft slot",
        "",
        "Method pre-registered Nov 23, 2026, before any score existed: see `docs/PHASE2_RESULT.md`.",
        "**PSI here has neutral hand inputs**: Intangibles and Team Fit are 50 for everyone, so rankings come from",
        "Production, Skills, Physical and Risk.",
        "",
        "**Outcome:** total regular-season NBA minutes in the player's third season (0 if he didn't play).",
        "**Measure:** Spearman rank correlation with that outcome. Higher is better; 0 means no relationship.",
        "",
        "| Run | Classes | Players | PSI | Draft slot | Flagged (>10% missing box scores) | No combine size |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, (label, first, last) in RUNS.items():
        r = results[key]
        lines.append(f"| {key}: {label} | {first}-{last} | {r['n']} | {r['psi_rho']} | {r['slot_rho']} "
                     f"| {r['flagged']} | {r['no_combine']} |")
    lines += ["", "## Year-3 minutes by PSI quintile (Run A)", "",
              markdown_table(quintiles(df, 2012, 2021)), "",
              "## Known limits", "",
              "- Intangibles (20%) and Team Fit (10%) are constants here; this tests the other 70% of PSI.",
              "- BPM, AST%, STL% and BLK% are unavailable, so those parts sit at the scorers' neutral defaults.",
              "- CBBD's defensive rating is treated as missing for seasons 2013 and earlier (it is broken there).",
              "- The Production benchmarks came from 2018-2024 top-10 picks; Run C is the clean check.",
              "- Draft slot already contains everything teams knew, including film, interviews and medicals.",
              ""]
    path.write_text("\n".join(lines))


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    conn = connect(db_path())
    try:
        df = score_population(conn)
    finally:
        conn.close()
    SCORES_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(SCORES_PATH, index=False)
    results = {key: run(df, first, last) for key, (_, first, last) in RUNS.items()}
    write_results(df, results)
    for key, r in results.items():
        print(key, r)
    print(f"Wrote {RESULTS_PATH} and {SCORES_PATH}")


if __name__ == "__main__":
    main()
