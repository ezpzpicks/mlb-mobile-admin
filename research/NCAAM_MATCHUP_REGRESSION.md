# NCAAM matchup-weight study — November 15, 2026

This offline study estimates scoring-efficiency and tempo weights from dated
pregame ratings. It does not change the builder, grading thresholds, saved
forecasts, official EZPZ Picks, or posting behavior.

## Findings

The data contain 6,316 qualifying Division I games from six November periods.
Final coefficient estimates use 5,412 non-overtime games from November 2021–25.
The external current-season check contains 575 non-overtime games from
November 1–14, 2026. Prior-day national ratings are the predictors; retrospective
game-log adjusted ratings are never predictors.

The pooled linear scoring-efficiency slopes are 1.053 for offense and 0.989 for
opposing defense. Their equality test gives p = 0.081, so the difference is not
conclusive at the 5% level. The tempo slope is 0.921. These slopes have different
units and are not shares of a fixed importance total.

The level model detects differences across the scale. Strong defense has an
estimated local slope of 1.650; very strong offense has a slope of 1.105 with a
wide interval. Very fast tempo has a slope of 0.583. The weakest offensive tail
flattens rather than receiving a larger penalty. These are continuous local
slopes within each rating band, not multipliers on a team's whole rating or score.

The existing fixed model won the November 2024 validation comparison:
total-score MAE 12.810 versus 12.862 for the best regression and 12.826 for the
best shrunken school-adjustment candidate. Linear regression improves November
2025 held-out MAE by only 0.050 points and the current-season check by 0.102
points. School adjustments do not generalize in the later checks. The study
does not support replacing the live model or enabling a blanket extreme bonus.

## Chronological design

| Role | November period | Non-overtime games |
| --- | --- | ---: |
| Initial fit | 2021–2023 | 3,053 |
| Family and penalty selection | 2024 | 1,169 |
| Untouched historical check | 2025 | 1,190 |
| External current check | November 1–14, 2026 | 575 |

The historical check refits the chosen regression specification on November
2021–24. The current check refits that frozen specification on November
2021–25. Current-season outcomes never fit the reported coefficients.
The current fixed baseline is included in selection and wins it. Reporting
later candidate results is a research comparison, not a retroactive selection.

Efficiency is points per 100 possessions, fitted to own adjusted offense and
opposing adjusted defense. Pace is possessions per 40 minutes, fitted to both
teams' adjusted tempo departures and a cross term. Home/away and opening-week
controls are included. Overtime games are excluded from fitting and the main
scoring comparison. Estimated efficiencies and pace are combined into scores.

The level model uses continuous integrated slopes at exactly -1.5, -1, 0, 1,
and 1.5 national standard deviations. Unit slopes reproduce the original raw
rating deviations. Lower defensive ratings indicate better defense; report
labels reverse the raw defensive scale accordingly.

Coefficient uncertainty uses a sandwich covariance for rows sharing either
school, including paired team scores and repeated matchups. Eighteen tests
against the current unit slopes use Benjamini–Hochberg false-discovery control.
MAE differences use paired circular seven-date moving-block bootstraps, not
independent row bootstraps. The limited number of November periods still
limits inference. Predictive associations are not causal effects or betting ROI.

Optional school effects fit intercept and slope deviations for 367 schools
with substantial ridge shrinkage toward the pooled model. They are not 367
unrestricted independent regressions. Rosters and coaching change between years.

## Reproduction

Use an isolated Python 3.11+ environment. These requirements are for offline
research and must not alter the production environment. After extracting the
accompanying data package, set `NCAAM_STUDY` to that extracted directory.

```bash
python -m pip install -r research/requirements-ncaam-regression.txt

# Rebuild the joined data from the frozen source cache. Cached files avoid
# downloading current, potentially revised provider responses.
PYTHONPATH=. python research/ncaam_regression_data.py \
  --cache "$NCAAM_STUDY" --output "$NCAAM_STUDY/dataset-rebuilt.json"

# Fit, select on validation, and evaluate the later periods.
PYTHONPATH=. python research/ncaam_matchup_regression.py \
  --data "$NCAAM_STUDY/dataset.json" --output "$NCAAM_STUDY/results-rebuilt"

# Render figures and the report. Requires DejaVu Sans system fonts.
PYTHONPATH=. python research/ncaam_regression_report.py \
  --data "$NCAAM_STUDY/dataset.json" \
  --results "$NCAAM_STUDY/results-rebuilt" \
  --examples research/fixtures/ncaam_tempo_examples_2026_11_15.json \
  --output "$NCAAM_STUDY/output-rebuilt"

python -m pytest research/test_ncaam_*.py research/test_espn_ncaam_lines.py -q
```

The rebuilt acquisition timestamp and source-manifest order can differ from
the frozen dataset bytes. Compare game rows and source hashes, not that new
timestamp. Solver and plotting differences across library versions can cause
small floating-point differences. The package includes original raw source
files, frozen joined rows, source hashes, coefficients, level weights, school
adjustments, holdout predictions, example replays, and figures.

The example replays use the captured November 15 inputs and saved court/player
adjustments once. They do not use the games' outcomes, publish revised picks,
or overwrite their original forecasts.

## Primary sources

- Bart Torvik data notes: https://adamcwisports.blogspot.com/p/data.html
- Dated national ratings: `https://barttorvik.com/timemachine/team_results/YYYYMMDD_team_results.json.gz`
- Raw logs: `https://barttorvik.com/getgamestats.php?year=YYYY&csv=1`
- Rating units: https://kenpom.com/blog/ratings-glossary/
- Shared-member covariance: https://arxiv.org/abs/1312.3398

Validation in the study environment: 140 tests and 119 subtests passed,
including six new data-integrity, continuity, and dependence tests. The final
six-page PDF was rendered and visually checked. No production write is made
by any study command.
