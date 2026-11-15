# NCAAF grades based on the available number

Policy: `ncaaf-sp-edge-2026-11-14-v5`.
The score formula and model version `cfb-v3.0-sp-matchup-projection-2026-11-14`
are unchanged. All 55 frozen November 14 score forecasts reproduce exactly.

| Numerical edge grade | Spread cover edge | Total Over/Under edge |
|---|---:|---:|
| A | 6.0+ points | 6.0+ points |
| B | 3.0 to less than 6.0 | 3.0 to less than 6.0 |
| Research | Less than 3.0 | Less than 3.0 |

These are provisional starting point-edge labels, not fitted win probabilities.
The spread and total parameters are independent even though their initial values
match. The absolute size of a betting line is not a measure of prediction error.
Future calibration should compare margin and total errors separately; neither a
percentage of the spread nor arbitrary opposite thresholds establishes equal
confidence. No cutoff was selected using today's winning results.

The forecast already includes offense versus opposing defense, defensive
suppression, tempo and live adjustments. The grader does not give those factors
another bonus or veto. A weaker underdog can receive an A when its points create
at least a six-point cover edge. Overs and Unders use the same scale, as do
favorites and underdogs. Exact full-precision edges determine boundaries.

Numerical grade and eligibility are separate. Captures preserve `edge_grade`,
the effective `grade`, `eligible`, the exact thresholds and `confidence_flags`.
The existing 20+ point spread restriction remains projection-only: its numerical
edge can be A while its effective grade remains Research. A confirmed market,
projection agreement, valid pregame provenance and positive estimated value at
the entered price remain requirements for an eligible model play. An unavailable
market has no valid numerical edge grade. The uncalibrated price simulation is
not a new proof of win probability.

Existing data-quality reasons (unconfirmed/incomplete/missing inputs) are shown
as confidence flags and do not add or subtract grading points. Below-threshold
forecasts remain available in Full Slate. Official EZPZ Picks still require an
explicit manual selection; grading does not approve or publish them.

## Quotes and history

The website recalculates grades against each current quote from the saved score
forecast and captured thresholds. An A can become B or Research; a previously
Research forecast can qualify at a better number. The opposing side can qualify
when a line moves past the model's fair line. Both alternatives use their own
line and price. Changed-price probabilities remain estimates based on the saved
simulation width/probability and are labeled accordingly; missing probability
evidence does not manufacture an active price signal.

The original v1–v4 captures retain their original grading/selection rules and
remain readable. New v5 grade records do not inherit earlier A/B outcomes.
Saved/finalized historical decisions and the score formula are not rewritten.

The archived v4 policy is `config/ncaaf_matchup_grades_v4.json`. Regression tests
cover exact grade boundaries, weaker underdogs, factor non-double-counting,
symmetry, quote upgrades/downgrades and side changes, actual-odds checks, source
clocks, unavailable markets and old-policy isolation.
