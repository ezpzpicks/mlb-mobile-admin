# NCAAF matchup support and market-relative value

The original matchup-only rank rewarded strong teams without accounting for the
points they had to cover. Favorite-only bonuses and different tempo scales also
made the raw totals incomparable. Matchup fit is descriptive, not a calibrated
probability of covering a spread or beating a total.

This first implementation keeps the existing live A/B policy and point model,
and adds research capture for every evaluated game that is saved, regardless of
grade. It does not change official picks or publish recommendations.

## Captured comparison

`Market Relative Matchup JSON` preserves all four alternatives (both spread
sides, Over and Under) with their original paired lines, actual or explicitly
assumed American odds, full-precision existing forecasts, continuous SP+/pace
features, observation and decision clocks, source population and schema hash.
It is saved on daily-slate rows by the shared interactive/scheduled production
builder. It is not a new recurring job, and an unsaved evaluation is not retained.

For a home spread s and projected home margin m, home edge is m+s and away edge
is its negative. For total line L and forecast T, Over edge is T-L and Under edge
is L-T. These are point differences, not win probabilities or price-adjusted EV.
Each candidate retains its odds for subsequent outcome/ROI analysis.

Offense-versus-opposing-defense descriptions use the mean of national offense
and opposing defensive-points-allowed z scores. Defensive support negates the
opponent's scoring pairing. Both spread roles use the same mean-pace feature
with opposite role signs: slower pace describes underdog support. Totals use
exactly mirrored features. No favorite-only bonus, capped confidence sum, or
automatic very-weak-offense penalty is applied in this comparison. Ryan's 35
explicit preference ratings and existing numbered situations are not rewritten.

The primary comparison is the existing forecast versus the line. Matchup features
are NOT added to that forecast as a second arbitrary bonus. No probabilities,
automatic A/B upgrades or guarantees of profitability are produced.

## Historical replay

Run from the repository root:

```sh
python -m research.ncaaf_market_relative_audit \
  --slates saved_slate_1.json saved_slate_2.json \
  --outcomes final_scores.json --output audit.json
```

Each slate contains `slateToday` rows (a bare list is also accepted). Original
factor captures, population identity, season, team identity, publication times
and paired market lines are checked. Current ratings, later board quotes and
final results cannot replace a pregame input. Duplicate games use the earliest
valid capture. Historical comparisons are reconstructed research rows, not
backdated new decisions or grades.

Outcomes are supplied separately as a JSON array:

```json
[{"date":"2026-11-14","game_id":"example","away_team":"Away","home_team":"Home",
  "away_score":21,"home_score":28,"observed_at":"2026-11-15T01:00:00Z"}]
```

`observed_at` is when a final was first observed, never kickoff or an inferred
historical time. Results collected today are unavailable to earlier training
decisions. Final scores require matching date, game ID and both team names.
Missing games are reported; graded-only or partial result coverage must not be
used to establish a betting policy. Pushes are separated and ROI uses one unit
risked at each candidate's actual/assumed odds. Win rates include Wilson intervals.

## Incremental validation

The fixed existing point forecast is reported separately from the proposed
learned adjustment. Walk-forward analysis compares a market-aware baseline
(existing forecast edge plus line) with that same model plus the saved matchup
features. This tests incremental information rather than counting known strength
twice. One game is one training row per market, not four independent bets.

The initial protocol uses fixed ridge penalty 10, training-only standardization,
and at least 50 earlier settled games across three dates. These are research
training floors, not filters on existing live plays. Training requires both the
capture AND the observed final to precede the held-out decision. Same-game and
future results are excluded. No parameters are selected using held-out results.
Reports compare held-out point error and ATS/O/U results separately by role;
fixed-forecast results are additionally grouped by market-line bands. No fixed
favorite/underdog or Over/Under allocation is imposed.

Two days of saved matchup ratings cannot establish reliable thresholds or
probabilities. The module reports `Collecting pregame history` until chronological
training is possible. Even then the output is exploratory: no learned coefficients,
win probabilities or revised live A/B grades are promoted automatically. Further
validation needs adequate final-score coverage, multiple later test dates,
consistent model versions and stable results by line band and betting role.
