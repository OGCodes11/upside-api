# Phase 2 backtest: PSI vs draft slot

Method pre-registered Nov 23, 2026, before any score existed: see `docs/PHASE2_RESULT.md`.
**PSI here has neutral hand inputs**: Intangibles and Team Fit are 50 for everyone, so rankings come from
Production, Skills, Physical and Risk.

**Outcome:** total regular-season NBA minutes in the player's third season (0 if he didn't play).
**Measure:** Spearman rank correlation with that outcome. Higher is better; 0 means no relationship.

| Run | Classes | Players | PSI | Draft slot | Flagged (>10% missing box scores) | No combine size |
| --- | --- | --- | --- | --- | --- | --- |
| A: All classes | 2012-2021 | 486 | 0.153 | 0.594 | 55 | 97 |
| B: 2012-2014 excluded | 2015-2021 | 343 | 0.149 | 0.577 | 3 | 85 |
| C: Classes the Production benchmarks never saw | 2012-2017 | 284 | 0.181 | 0.619 | 55 | 41 |

## Year-3 minutes by PSI quintile (Run A)

| psi_quintile | players | psi_range | avg_pick | avg_year3_minutes | share_with_0_minutes |
| --- | --- | --- | --- | --- | --- |
| 1 (lowest) | 98 | 43.8-50.5 | 35.1 | 708.2 | 0.3 |
| 2 | 97 | 50.5-52.5 | 33.1 | 827.0 | 0.3 |
| 3 | 97 | 52.6-54.5 | 29.2 | 915.7 | 0.3 |
| 4 | 97 | 54.5-56.9 | 28.7 | 915.7 | 0.2 |
| 5 (highest) | 97 | 56.9-67.1 | 21.9 | 1109.1 | 0.1 |

## Known limits

- Intangibles (20%) and Team Fit (10%) are constants here; this tests the other 70% of PSI.
- BPM, AST%, STL% and BLK% are unavailable, so those parts sit at the scorers' neutral defaults.
- CBBD's defensive rating is treated as missing for seasons 2013 and earlier (it is broken there).
- The Production benchmarks came from 2018-2024 top-10 picks; Run C is the clean check.
- Draft slot already contains everything teams knew, including film, interviews and medicals.
