# NCAAM v0.5 opening-season margin calibration

The source efficiency formula compresses large strength differences. Its neutral
home-minus-away margin is now passed through the frozen mean calibration selected
in the November 2 chronological replay. A verified home court is added separately.
Neither betting lines nor the betting favorite enter the correction.

`home margin = 1.122098584690361 * neutral source margin + 3.881992388010041 * home indicator`

The coefficients were fitted on 600 independently score-verified games from
November 6–19, 2023. The model was selected on the next opening fortnight and
evaluated on the following season. No coefficients or betting thresholds were
retuned using those later periods or the current-season check.

## Predictive evaluation

| Independent sample | Games | Margin MAE before | Margin MAE after | Favorite 20+ bias before | Favorite 20+ bias after |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2024–25 opening fortnight | 606 | 9.73 | 9.66 | +5.16 | +2.11 |
| 2025–26 opening fortnight | 608 | 9.59 | 9.42 | +5.44 | +2.43 |
| Nov. 2–3, 2026 saved pregame replay | 129 | 10.79 | 10.42 | +5.73 | +2.54 |

Bias is actual minus projected margin oriented toward the market favorite.
Positive values mean the model underestimates that favorite's winning margin.
The market defines the evaluation subgroup only; it is not a model input.

The current-season replay uses one latest saved v0.4 pregame forecast per game,
with its original dated ratings, original quote and saved availability changes.
Four games with changed tipoff/team orientation are excluded, and five unfinished
games are not graded. The result cutoff is November 4 at 03:34 UTC. This is an
offline recomputation, not a replacement for any historical prediction.

### Betting limitation

The point correction has **not** established a betting improvement. At the fixed
three-point threshold, the final historical holdout was 114–110, −6.36 units,
−2.84% ROI. In the 129-game current-season replay the baseline was 27–29
(−4.45 units), and the corrected variant was 17–22 (−6.55 units). All prices
in these comparisons assume −110 and one unit risked. Full threshold sensitivity
and per-game comparisons are retained in the accompanying JSON result.

Use this as a research projection correction only. A/B grades, probability
claims, official EZPZ selection and posting eligibility remain disabled.

## Runtime behavior

- Version: `ncaam-v0.5-opening-margin-calibration-2026-11-03`.
- Evaluated live scope: 2026–27 games dated Nov. 1–14, 2026 in Eastern time.
  Later phases and unverified future-season windows retain the v0.4 source
  margin and 3.2-point home advantage; they are explicitly marked inactive.
  Do not extrapolate an opening-only fit to conference play without evaluation.
- Away-favored team strength is scaled in the same direction as home-favored
  strength. Neutral courts receive zero home advantage.
- The source total is preserved exactly. Team scores share the margin change
  equally and in opposite directions.
- Player availability deltas are added after the source-margin correction and
  are not multiplied by its coefficient. Calibration is never reported as an
  injury effect.
- Forecasts save the policy, applied coefficients, original source margin,
  correction and training provenance. New model versions use new immutable
  forecast identities. Old forecasts, signal captures and results are untouched.
- The existing builder cadence and date-indexed storage are reused. No new
  provider requests, cron service or public-page computation is introduced.

## Reproduce

The November 2 data package contains the independently verified `replay/games.json`
with SHA-256 `d5eee52cd17f4de52fb2601a3478c9a6729781ef775b6c3c3893574478eb5524`.
The offline evaluator checks this hash and independently reproduces the original
two-parameter fit from the oldest season before evaluating later games.

```sh
python research/evaluate_ncaam_margin_calibration.py \
  --historical /path/to/replay/games.json \
  --live /path/to/ncaam_yesterday.json \
  --live /path/to/ncaam_today.json \
  --output research/results/ncaam_v05_opening_margin_2026-11-03.json
python -m pytest research/test_ncaam_*.py research/test_espn_ncaam_lines.py -q
```
