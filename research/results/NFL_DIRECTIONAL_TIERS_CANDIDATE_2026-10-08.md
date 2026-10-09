# NFL directional tier candidate — October 8, 2026

Status: candidate only. The deployed v4.27 model remains unchanged.

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

## Retrospective accuracy comparison

The policy was specified before scoring. No weights or coefficients were optimized.
Both versions use identical baselines, player profiles, slot histories and outcomes.
Positive bias means projections exceed actual receiving yards; negative bias means
they fall below actual receiving yards.

| Sample | Matched receivers | Current mean absolute error | Candidate mean absolute error | Current bias | Candidate bias |
|---|---:|---:|---:|---:|---:|
| Week 4, recovered saved receiving calibration | 58 | 33.445 yards | 33.629 yards | -10.573 yards | -10.862 yards |
| Weeks 2–3, regression-core sensitivity replay | 87 | 25.999 yards | 26.231 yards | -5.150 yards | -5.152 yards |

For Week 4, RMSE was 43.844 yards with current weights and 44.223 with the candidate.
Among its 31 positive yardage-residual matchups, MAE was 35.959 versus 36.403 yards.
The difference is small and this sample does not establish that either policy is
superior. It does not support claiming an accuracy improvement from the reversal.

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

## Validation and reproducibility

Passed the full production-wrapper test for all five directional tier weights,
WR1/WR2 exact-slot grading, independent input shares, opposing component signs,
neutral matchups, final WR caps and target/reception/efficiency consistency.
Existing NFL slot-matchup, skill-prop regression and matchup-amplification tests
also pass. Python compilation and whitespace checks pass.

`research/nfl_directional_tier_replay.py` runs the comparison against prepared
read-only snapshots. Its inputs directory contains the historical projection and
lineup export, weekly pregame profile pickles, official weekly player stats,
saved NFL schedule and public game-forecast export. Input snapshots and credentials
are not committed. The script never writes production storage.

The candidate is prepared for review. The current production weighting should be
retained while prospective results and other tiered markets are evaluated.
