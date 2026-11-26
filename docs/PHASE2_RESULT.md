# Phase 2 result: PSI does not beat draft slot

**PSI ranked 2012–2021 draftees by Year-3 NBA minutes far worse than draft slot did, in every run.** Its rank correlation with the outcome was 0.15–0.18; draft slot's was 0.58–0.62. PSI is not empty (players in its top fifth averaged 1,109 Year-3 minutes against 708 for its bottom fifth), but it is nowhere near the baseline.

**The method was logged before any scoring.** Every rule below (players, inputs, outcome, measure, the three runs, and the defensive-rating rule) was written into the project's decision log and committed on Nov 23, 2026 in commits `1167e2e` (10:42 PT) and `f0aa49d` (10:46 PT), before the scoring code was committed (`c606178`, 10:48 PT) and before the result (`7fac2a7`, 10:54 PT). The plan to run with and without the 2012–2014 classes was committed the night before, Nov 22, 2026, in `b5442a1`. These hashes are from the private development repository. Full output: [BACKTEST_RESULTS.md](BACKTEST_RESULTS.md).

## What was tested

- **PSI as designed in May 2026**, with the six scorers (Production, Skills, Intangibles, Physical, Team Fit, Risk) ported line for line from the original TypeScript, constants and weights unchanged.
- **Players:** everyone drafted 2012–2021 who is linked to a Division I college career and has a final pre-draft season. International, G League and high-school picks have no college line and were not scored.
- **Inputs:** each player's final college season before the draft, regular season through the conference tournament (NCAA and NIT games are kept out of PSI by the method rules), plus his combine measurements where he had them.

## Outcome measure

**Year-3 minutes:** total regular-season NBA minutes in the player's third season (a 2012 pick's is 2014-15), and 0 if he did not play. Both PSI and the baseline are scored by **Spearman rank correlation** with that number: 1 is a perfect ranking, 0 is no relationship. The baseline ranks players by draft slot, earlier pick higher.

## Results

| Run | Classes | Players | PSI | Draft slot |
| --- | --- | --- | --- | --- |
| A: all classes | 2012–2021 | 486 | 0.153 | 0.594 |
| B: 2012–2014 excluded | 2015–2021 | 343 | 0.149 | 0.577 |
| C: classes the Production benchmarks never saw | 2012–2017 | 284 | 0.181 | 0.619 |

Excluding the classes with missing box scores (Run B) barely changes PSI, and the run free of benchmark overlap (Run C) is no worse. Neither problem explains the gap. No single component did well on its own either: Production 0.14, Risk 0.09, Skills 0.08, Physical 0.08 (Run A).

## Limits

- **Intangibles (20%) and Team Fit (10%) were neutral at 50 for everyone.** Both are hand-entered by design, and scoring 2012 prospects today would leak what we know about their careers. This tests the other 70% of PSI.
- **No BPM.** It isn't available from our sources, so that part of Production sat at the scorer's neutral default, as did AST%, STL% and BLK%.
- **Missing box scores in 2012–2014.** CBBD has no box scores for most 2012–2013 conference tournament games and hundreds of regular-season games. Prospects with more than 10% of their teams' pre-draft games missing carry `missing_box_flag`: 55 in Run A, 3 in Run B, 55 in Run C. CBBD's defensive rating is also broken for seasons through 2013 and was treated as missing there.
- **Benchmark overlap.** The Production scorer's "elite prospect" benchmarks were built from 2018–2024 top-10 picks, which overlaps the 2018–2021 test classes. Run C (2012–2017) is the check that avoids it.
- **The baseline knows more.** Draft slot reflects everything teams knew, including film, interviews and medicals.
- **Position rule.** Players with no combine position and a CBBD "F" were scored as small forwards, including Karl-Anthony Towns, a center. It was left as logged.

## Next

A PSI v2 should test whether PSI adds anything on top of draft slot, for example by ranking players within the same pick range, with any changes to inputs or rules logged in a new dated entry before it runs.
