# NFL directional tier candidate — October 8, 2026

Status: v4.28 approved for production release on October 8, 2026.
The comparison below and the JSON release-status field describe the evaluation
performed while v4.27 was deployed and v4.28 was a candidate.

Evaluation target: correct Over/Under calls against saved market lines. Primary
comparisons are win/loss records, Over and Under separately, unchanged Strong and
Regular selection rules, and calls changed by the tier policy. MAE remains a
secondary calibration diagnostic rather than the release criterion.

The proposed policy preserves skill protection against negative matchup adjustments
and reverses the weights for positive adjustments. Every input is still graded
against players in the same exact lineup spot, such as WR1 or WR2.

| Input tier | Negative adjustment weight | Positive adjustment weight |
|---|---:|---:|
| Tier 1 | 0% | 200% |
| Tier 2 | 50% | 150% |
| Tier 3 | 100% | 100% |
| Tier 4 | 150% | 50% |
| Tier 5 | 200% | 0% |

Weights scale only the input's assigned share of the matchup deviation. They are
not multipliers on the player's baseline projection. The existing component
bounds of 0.65–1.35 and WR1/WR2 combined bounds of 0.62–1.38 remain in place.
Direction is evaluated independently for opportunity and efficiency. Opposing
component signs can therefore occur even when the net yardage residual is negative.

## Approximate replay of the saved Dallas inputs

| Receiver | Current v4.27 weighting | Directional candidate |
|---|---:|---:|
| CeeDee Lamb, WR1 | 69.89 yards | 69.89 yards |
| George Pickens, WR2 | 60.00 yards | 70.32 yards |

Lamb's two components are negative, so his weights do not change. Pickens's
favorable components receive larger weights for his stronger inputs; his final
matchup multiplier changes from 1.152 to approximately 1.350. These are saved-input
replays using rounded regression values, not fresh live projections. No records
were rewritten.

## Over/under comparison — primary evaluation

The policy was specified before scoring. No weights or coefficients were optimized.
Both versions use identical baselines, player profiles, slot histories and outcomes.
Projection above the saved line calls Over; projection below calls Under. An exact
projection tie is a no-pick, and an actual result equal to the line is a push.
Pushes and no-picks are excluded from hit-rate denominators.

Week 4 has 57 usable saved lines among 58 receiver replays. Stefon Diggs has no
saved line. There are no pushes or projection ties in this sample.

| Week 4 receiving-yard calls | Current v4.27 weights | Directional candidate |
|---|---:|---:|
| All calls, same 57 lines | 35–22 (61.40%) | 33–24 (57.89%) |
| Over calls | 20–10 (66.67%) | 19–11 (63.33%) |
| Under calls | 15–12 (55.56%) | 14–13 (51.85%) |
| WR1 calls | 19–11 | 18–12 |
| WR2 calls | 16–11 | 15–12 |
| Positive net slot-yardage residuals | 19–12 | 16–15 |
| Strong selections, repriced | 1–0 | 1–0 |
| Regular selections, repriced | 2–1 | 2–1 |
| Strong + Regular selections | 3–1 | 3–1 |

Six Week 4 calls change direction. The candidate fixes two losses and converts
four wins to losses. On these cases, current weights go 4–2 and the candidate 2–4.

| Receiver | Saved line | Current projection / call | Candidate projection / call | Actual yards | Outcome change |
|---|---:|---|---|---:|---|
| Marvin Harrison Jr., WR2 | 34.5 | 35.81 / Over | 34.24 / Under | 12 | Loss → Win |
| Matthew Golden, WR2 | 57.5 | 53.61 / Under | 62.43 / Over | 21 | Win → Loss |
| Davante Adams, WR2 | 66.5 | 65.67 / Under | 72.73 / Over | 32 | Win → Loss |
| Malik Washington, WR1 | 41.5 | 43.85 / Over | 40.00 / Under | 67 | Win → Loss |
| Jaylen Waddle, WR1 | 57.5 | 58.97 / Over | 55.04 / Under | 35 | Loss → Win |
| Jaxon Smith-Njigba, WR1 | 95.5 | 94.71 / Under | 96.63 / Over | 76 | Win → Loss |

The unchanged Strong/Regular gates require a 30% projection gap, with probability
edge of at least 30 percentage points for Strong or 16 for Regular. Both versions
are repriced with the same installed production simulator and grading wrapper.
Historical grades and probability edges are not reused. These are counterfactual
replay selections rather than the original published record. Four selections per
version are too few to establish a grading improvement. The three retained
selections all win; the candidate removes one losing London Under and adds one
losing Wan'Dale Robinson Over.

### Weeks 2–3 sensitivity check

The wider replay has 85 usable lines; two of 87 matched cases have no line. The
candidate projects exactly 31.5 against Wicks's 31.5 line in Week 2, which is
scored as a no-pick in the directional comparison.

| Weeks 2–3 sensitivity replay | Current weights | Candidate |
|---|---:|---:|
| Same 84 lines where both call a side | 45–39 (53.57%) | 43–41 (51.19%) |
| All usable lines | 46–39 | 43–41, plus 1 no-pick |
| Strong + Regular selections | 3–5 | 4–5 |

The eight retained Strong/Regular selections keep the same 3–5 record. The
candidate adds one winning Jordan Addison Under in Week 2. This replay omits
historical live efficiency overlays and is not a full production reconstruction;
its results should not be pooled with Week 4 into one production backtest.

Saved manual lines match their tracker copies in all 102 matched WR1/WR2 rows.
Official actual yards match all 53 available tracker actuals (15 in Week 4 and 38
in Weeks 2–3). All 256 saved receiver projection dates are on or before their
scheduled game date. The export has no immutable intraday line history, so these
are saved input lines rather than independently verified closing lines.

## Yardage-error diagnostics — secondary

Positive bias means projections exceed actual receiving yards; negative bias means
they fall below actual receiving yards. These statistics do not decide which
version better predicts Over/Under outcomes.

| Sample | Matched receivers | Current mean absolute error | Candidate mean absolute error | Current bias | Candidate bias |
|---|---:|---:|---:|---:|---:|
| Week 4, recovered saved receiving calibration | 58 | 33.445 yards | 33.629 yards | -10.573 yards | -10.862 yards |
| Weeks 2–3, regression-core sensitivity replay | 87 | 25.999 yards | 26.231 yards | -5.150 yards | -5.152 yards |

For Week 4, RMSE was 43.844 yards with current weights and 44.223 with the candidate.
Among its 31 positive yardage-residual matchups, MAE was 35.959 versus 36.403 yards.
The small yardage-error differences do not establish which betting policy is better.

Week 4 contained 64 saved WR1/WR2 receiving projections. Six were excluded because
there was no matching official player/team/week stat record. Missing records were
not silently interpreted as zero yards. In Weeks 2–3, 41 of 128 candidates were
excluded for unmatched stats, missing saved game forecasts or insufficient history.
The two samples use different baseline reconstruction methods and should not be
pooled into one claimed production backtest.

Nineteen of the 58 Week 4 cases had opposing opportunity and efficiency signs.
Two had negative net yardage residuals but positive final multipliers under the
candidate; both already had positive final multipliers under current weighting.
The candidate introduced no additional negative-to-positive sign changes in that
sample. This distinction should remain visible in future evaluations.

## Pregame data controls and limits

- 2026 player profiles and slot defense history use only weeks before the projected
  week. The previous season supplies prior-year information.
- Current-season Next Gen week-0 rows summarize the available season and were
  excluded from historical profiles. Only weekly rows were allowed, preventing
  later games from leaking into earlier-week tier assignments.
- Week 4 baselines are recovered from the saved v4.19 receiving calibration: saved
  regression targets and the recorded pre-shrink YPT. Stored values are rounded.
- The Weeks 2–3 sensitivity check rebuilds the current regression core with saved
  game-score forecasts. It omits historical live efficiency overlays and is not
  a complete production rebuild.
- This is retrospective evidence, not untouched prospective validation. Accuracy
  coverage is WR1/WR2 receiving yards; the shared helper also serves other tiered
  yardage paths, which require their own accuracy evaluation before a broad release.
- The NGS summary exclusion is a historical replay control; it does not change the
  production profile builder in this candidate.
- Every usable WR1/WR2 line is scored, with publishable selections reported
  separately. Grades were not tuned on outcomes. Both arms use the same installed
  simulation version; probability edges and grade boundaries are approximate.

## Validation and reproducibility

Passed the full production-wrapper test for all five directional tier weights,
WR1/WR2 exact-slot grading, independent input shares, opposing component signs,
neutral matchups, final WR caps and target/reception/efficiency consistency.
Existing NFL slot-matchup, skill-prop regression and matchup-amplification tests
also pass. Python compilation and whitespace checks pass.
Directional settlement checks pass for wins/losses, actual pushes, projection
ties, missing lines, matched-sample denominators, saved-price payouts, and
added/removed graded selections. Repricing leaves all 145 previous replay
projections and actual outcomes unchanged.

`research/nfl_directional_tier_replay.py` runs the comparison against prepared
read-only snapshots. Its inputs directory contains the historical projection and
lineup export, weekly pregame profile pickles, official weekly player stats,
saved NFL schedule and public game-forecast export. Input snapshots and credentials
are not committed. The script never writes production storage.

`research/nfl_prop_directional_scoring.py` supplies the outcome comparison. Full
receiver rows and directional summaries are saved in
`research/results/nfl_directional_tier_bet_records_2026-10-08.json`.

The directional tier policy is approved for release as v4.28. Directional
performance remains the evaluation criterion. Week 4 all-call results decline
and Strong/Regular records tie; the sensitivity replay adds one winning graded
selection. These results do not establish an empirical advantage for the full
reversal. They provide a baseline for prospective results and evaluation of
other tiered markets under the unchanged grading rules.
