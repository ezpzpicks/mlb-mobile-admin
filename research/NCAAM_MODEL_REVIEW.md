# NCAAM model first review — 2026-10-22

The current NCAAM builder is `builders/cbb_builder.py`. The admin routes its CBB
menu to this builder and maps its storage to the NCAAM Turso namespace. It is a
manual foundation model, not yet an automatically sourced or validated model.

## First correction prepared

The defensive shooting term has the wrong sign. `eFG Def` represents opponent
effective field-goal percentage allowed: lower values mean better defense.
The original term rewards the opposing offense for facing a lower value.

Holding every other input fixed at the draft's defaults, on a neutral court:

| Opponent eFG allowed | Original projected score | Corrected projected score |
| --- | ---: | ---: |
| 48% (better defense) | 72.2 | 70.6 |
| 52% (baseline) | 71.4 | 71.4 |
| 56% (worse defense) | 70.6 | 72.2 |

This branch corrects both home and away terms, clarifies defensive input labels,
and updates the model version to distinguish new projections from old ones.
Ten behavioral tests cover both shooting-defense directions, adjusted defense,
neutral-site symmetry, home court, rotation losses, pace, turnovers, offensive
shooting and unchanged inputs. The two shooting-defense tests fail on the
original code; all ten pass after the correction.

This is a directional math correction. It does not establish predictive
accuracy or validate the existing coefficients and grades.

## What the draft already does

- Projects possessions from the mean of two team tempos, plus a manual offset.
- Combines adjusted offensive and defensive efficiencies with shooting,
  turnovers, offensive rebounds and free-throw factors.
- Supports manual rotation, home-court, margin and total adjustments.
- Produces projected scores, spreads, totals, moneylines and a reliability score.
- Saves projections and tracked plays through the shared Turso storage layer.

## Gaps to address before treating picks as validated

1. **Team data is manual.** There is CSV import and a ratings editor, but no
   schedule, results, ratings, or roster synchronization in this builder. Missing
   teams default to identical generic ratings. An automated builder must require
   actual dated inputs and skip missing teams instead of publishing those defaults.
2. **Efficiency and four-factor adjustments overlap.** Adjusted efficiency
   already summarizes scoring. Adding raw shooting/rebounding/turnover effects
   may double count team strength. Test a plain adjusted-efficiency baseline
   against incremental matchup adjustments fitted only on prior games.
3. **Probabilities and grades are assumptions.** Spread, total and win
   probabilities use fixed normal-distribution scales (10.8, 12.5, 10.5). Shared
   A/B rules do not establish a historical NCAAM hit rate. Fit uncertainty and
   thresholds separately for spreads and totals, then evaluate on held-out dates.
4. **Reliability is not calibrated accuracy.** Data and rotation confidence are
   manual inputs, and larger model-market differences mechanically increase the
   reliability score. A bigger disagreement can also indicate bad inputs.
5. **Early-season and venue context is incomplete.** Home court can be set to
   zero manually; there is no automatic neutral-site handling, sample-size
   weighting, roster continuity, rest, or dated availability pipeline.
6. **Tracking needs an evaluation pipeline.** The CBB tracker can save selected
   bets, but this builder has no historical pregame replay or automated grading.
   Historical end-of-season ratings must not be used to predict earlier games.

## Recommended next model: spreads and totals first

1. Select and verify a historical team/game data source. The draft already
   identifies CollegeBasketballData as its intended next source. Its current
   documentation lists adjusted efficiency from 2014 and historical game/team
   data, but the adjusted-ratings endpoint only exposes season/team/conference
   filters. Do not assume it provides a historical pregame snapshot. Rebuild
   dated ratings from earlier games or obtain a genuinely dated archive. Check
   actual access, completeness, and quota before wiring an integration.
   CBBD seasons use the ending year: the upcoming 2026–27 season is `2027`.
2. Build a simple opponent-adjusted efficiency/tempo baseline. Record neutral
   courts and use only information available before each game. Estimate a
   season baseline and early-season weighting from training data rather than
   choosing permanent weights without a test.
3. Compare adding shooting, turnovers, rebounding, free throws, recent form and
   rotation information one group at a time. Retain additions only when they
   improve later held-out periods, not merely the fitting sample.
4. Use ScoresAndOdds for current spread/total inputs and default -110 pricing,
   matching the existing project decision. Keep the prediction independent of
   the market when measuring its point edge. Archive the market timestamp.
5. Run a chronological backtest across multiple seasons when coverage permits.
   Report score/margin/total error, ATS and total records, pushes, -110 profit,
   sample sizes and calibration. Separate early season, conference play, neutral
   sites and large model-market discrepancies. Tune on training/validation
   periods and leave the final test period untouched.
6. Select NCAAM-specific grades only after evaluation, then save shadow
   predictions for every eligible game to assess the new season prospectively.

## Keep the existing cost improvements

- Ingest completed games incrementally and persist game IDs and update times.
- Calculate team ratings once per refresh and reuse them for all matchups.
- Keep historical replay offline/on the backend; serve saved results to Vercel.
- Cache daily rating inputs, avoid full-season database reads per matchup, and
  append/upsert only new or changed rows.
- When a basketball market collector is added, reuse the existing 30-minute
  collection before the final 90 minutes and five-minute collection inside that
  window, respecting the final lock. No basketball collector is enabled by this
  review branch.

## Validation command

```bash
python -m unittest discover -s research -p 'test_cbb_projection.py' -v
```

## Data references checked

- [CBBD ratings reference](https://api.collegebasketballdata.com/api/ratings)
- [CBBD historical coverage](https://api.collegebasketballdata.com/data-availability)
- [CBBD access and usage guidance](https://api.collegebasketballdata.com/usage-and-access)
