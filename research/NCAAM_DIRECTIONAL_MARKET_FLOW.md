# NCAAM directional market-flow candidate — November 22, 2026

This candidate implements the eight movement interpretations approved by Ryan. It is research code and is not imported by the scheduled builder or site reader. Live projections and official records retain their current formula.

An ending money-versus-ticket gap can describe opposite market paths. This candidate compares money, tickets and signed line changes within adjacent, synchronized pregame observations. First-to-current money-share change fixes the allowed overall correction direction. Counterflow can damp or cancel support. A static ending gap adds no correction. Nonnegative fitted magnitudes cannot reverse an approved interpretation.

## Approved examples

Shares refer to Over for totals and Team A for spreads.

| Market | Tickets | Money | Line | Direction |
|---|---|---|---|---|
| Total | 50 → 26 | 50 → 66 | 151.5 → 149.5 | Up, with opposing-price damping |
| Total | 12 → 26 | 95 → 66 | 151.5 → 149.5 | Down, with line confirmation |
| Total | 45 → 60 | 50 → 75 | 149.5 → 152.5 | Up, with line confirmation |
| Total | 70 → 55 | 80 → 60 | 152.5 → 149.5 | Down, with line confirmation |
| Total | 40 → 60 | 70 → 45 | Unchanged | Down, without line confirmation |
| Total | 20 → 20 | 80 → 80 | Unchanged | No new correction |
| Spread | 45 → 35 | 50 → 75 | +6.5 → +4.5 | Improve A's margin |
| Spread | 20 → 40 | 90 → 65 | −6.5 → −4.5 | Reduce A's margin |

All eight examples are executable unit-test cases. Further tests check timing, future observations, duplicate snapshots, mirrored sides, invalid data, constrained fitting and coherent team scores.

## Implementation

The pure module is builders/ncaam_market_flow.py. Four continuously sized channels represent money-only flow, money/ticket alignment, money/ticket divergence and same-window line confirmation. Share changes use 10-percentage-point units. Channel proportions depend continuously on relative ticket movement. Opposing price damps that interval by 1/(1+log1p(abs(price move))); confirming price adds a nonnegative confirmation feature. A fixed three-hour half-life weights recent intervals.

Counterflow accumulates before each channel is clipped to the approved overall money-share direction. First and current money shares being equal produces no new correction. Missing data is distinct from observed unchanged percentages. At least two observations spanning 30 minutes are needed. An earlier price move cannot confirm a later share change. Postdecision and post-tipoff observations cannot enter the features.

Separate spread/total magnitudes use nonnegative ridge, alpha 100, no intercept, training-only scales and no hyperparameter search. Unsupported channels can receive zero weight. The candidate constructor applies both corrections once and combines the independent margin and total into coherent team scores. It does not duplicate the baseline overtime allowance. It returns research-only output with no official-pick eligibility.

## Chronological replay

Recaptured saved boards and independently agreeing ESPN finals yielded 432 eligible physical games: 160 warm-up, 265 main evaluation games from Nov 14–21, and seven separately reported Nov 22 games. The previous replay had 268 main games; these are different matched cohorts.

Both formulas use identical saved lines and decision timestamps. Spread B remains 5 to below 7 points of edge. Totals qualify at 8; A starts at 10. Returns assume one unit risked at −110. Daily fits need at least 150 earlier-date games and exclude games within six hours after tipoff. Exact settlement timestamps are unavailable, so outcome availability uses that conservative proxy.

| Nov 14–21 replay | Baseline | Directional candidate |
|---|---:|---:|
| All spreads | 26–12; +11.64 units; +30.6% ROI | 26–11; +12.64 units; +34.2% ROI |
| Market spreads below 20 | 15–8; +5.64 units; +24.5% ROI | 15–7; +6.64 units; +30.2% ROI |
| Totals | 9–5; +3.18 units; +22.7% ROI | 9–5; +3.18 units; +22.7% ROI |

The sole bet change was Hofstra +11.5 against Murray State on Nov 17. Lowering Murray State's projection moved the Hofstra edge from 6.46 to 7.07, outside the existing B-only band. The baseline bet lost. The one-unit record improvement comes from that upper eligibility boundary; it is not an added winning bet or evidence of improved point accuracy.

The latest spread fit, using 425 eligible games through Nov 21, sets all four coefficients to zero. It leaves subsequent spread projections unchanged. The latest total fit retains divergence and same-window confirmation channels. Neither formula qualified a bet among the seven matched Nov 22 games.

For UConn–Arizona, Over tickets fell 82 → 26 and Over money fell 85 → 66 while the total fell 148.5 → 146.5. The revised candidate lowers the baseline total from 149.0514 to 148.7873. The previous unconstrained research fit raised it to 155.4605. Neither the baseline nor the revised candidate qualifies at 146.5. The final score does not reverse the approved movement interpretation.

## Validation and limits

- 19 unit tests pass, including all eight approved cases.
- Twelve native v0.16 forecasts reproduce both baseline components exactly.
- All 544 game/market comparisons use identical quotes and timestamps; 1,088 prediction rows were checked.
- All fitted coefficients are nonnegative; no correction reverses its approved money-share direction.
- Source outcomes independently agree; training uses earlier dates only.
- No official records, production model, deployment or scheduled task is changed.

Results are exploratory. The baseline and B-only policy were selected using overlapping earlier research. One changed bet does not establish a durable advantage. Current fitted sizing does not justify large automatic point changes.

## Reproduce

Run from the repository root:

    python3 -m unittest discover -s research -p 'test_ncaam_market_flow.py' -v
    python3 research/replay_ncaam_market_flow.py --fetch

The replay writes source captures and outputs under research/results/ncaam_market_flow. Set NCAAM_MARKET_FLOW_WORKSPACE for another directory. The fetch flag reads saved boards and ESPN, reuses existing captures, and never runs the builder or writes to Turso.

The design is in config/ncaam_market_flow_research.json; aggregate results are in research/results/ncaam_directional_market_flow_2026-11-22.json. The baseline source revision is ddae33559a352fb1161460ea3fff7d50cddfa333.
