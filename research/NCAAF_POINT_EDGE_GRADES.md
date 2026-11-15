# NCAAF grades based on the available number

Policy: `ncaaf-sp-edge-2026-11-14-v6`.
The score formula and model version `cfb-v3.0-sp-matchup-projection-2026-11-14`
are unchanged. All 55 frozen November 14 score forecasts reproduce exactly.

| Numerical edge grade | Spread cover edge | Total Over/Under edge |
|---|---:|---:|
| A | 10.0+ points | 10.0+ points |
| B | 8.0 to less than 10.0 | 8.0 to less than 10.0 |
| Research | Less than 8.0 | Less than 8.0 |

These are provisional starting point-edge labels, not fitted win probabilities.
The spread and total parameters are independent even though their initial values
match. The absolute size of a betting line is not a measure of prediction error.
Future calibration should compare margin and total errors separately; neither a
percentage of the spread nor arbitrary opposite thresholds establishes equal
confidence. No cutoff was selected using today's winning results.

The forecast already includes offense versus opposing defense, defensive
suppression, tempo and live adjustments. The grader does not give those factors
another bonus or veto. A weaker underdog can receive an A when its points create
at least a ten-point cover edge. Overs and Unders use the same scale, as do
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

## Slate volume and play mix

Ryan's target is roughly 10–15 plays on a full Saturday slate. This is a volume
target, not a minimum count or a cap. The unchanged November 14 frozen-input
replay has 10 numerical candidates at the original pregame lines after the
20+ spread restriction: 4 A and 6 B. The mix is 5 favorites, 1 underdog, 1 Over
and 3 Unders. These counts do not use final results or demonstrate profitability.

The website shows current-policy eligible A/B counts, their shares, A/B totals,
and forecast coverage. Each game/market is counted once at its latest capture.
Price holds, projection-only spreads and older-policy captures do not inflate
the current mix. Pick'em and unclassified selections remain visible separately.
Neither forecasts nor grades change to fill category quotas or reach the volume
target. Uneven counts reveal concentration to investigate across future slates;
one Saturday does not establish systematic bias. Other Saturdays can yield fewer
or more than the target.

## Quotes and history

The website recalculates grades against each current quote from the saved score
forecast and captured thresholds. An A can become B or Research; a previously
Research forecast can qualify at a better number. The opposing side can qualify
when a line moves past the model's fair line. Both alternatives use their own
line and price. Changed-price probabilities remain estimates based on the saved
simulation width/probability and are labeled accordingly; missing probability
evidence does not manufacture an active price signal.

The original v1–v5 captures retain their original grading/selection rules and
remain readable. New v6 grade records do not inherit earlier A/B outcomes.
Saved/finalized historical decisions and the score formula are not rewritten.

The archived v4 policy is `config/ncaaf_matchup_grades_v4.json`; the original
6/3-point policy is `config/ncaaf_matchup_grades_v5.json`. Regression tests
cover exact grade boundaries, weaker underdogs, factor non-double-counting,
symmetry, quote upgrades/downgrades and side changes, actual-odds checks, source
clocks, unavailable markets, old-policy isolation and play-mix counting.
