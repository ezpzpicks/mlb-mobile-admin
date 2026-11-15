# Matchup-led NCAAF scoring model

Version: `cfb-v3.0-sp-matchup-projection-2026-11-14`.
Decision policy: `ncaaf-sp-projection-2026-11-14-v4`.
Status: provisional; no calibrated betting win probabilities.

The former point forecast came from scoring regressions while published SP+
offense/defense and pace mainly described matchup grades. That allowed a large
old-model Under edge even when the displayed matchup profile supported scoring.
The new production installer replaces the scoring baseline BEFORE simulation,
market pricing and matchup grading. Interactive and scheduled builders share it.

## Scoring construction

Let O and D be published SP+ offense and defensive points allowed, with national
means Obar and Dbar. Let B = (Obar + Dbar)/2. At national-average pace:

```
Away points = B + (Oaway - Obar) + (Dhome - Dbar)
Home points = B + (Ohome - Obar) + (Daway - Dbar)
Pace multiplier = mean(Taway, Thome) / Tbar
```

This is EZPZ's transparent additive construction from adjusted scoring ratings,
not the proprietary SP+ game-projection formula. One point of better offense
adds one neutral-pace point; one point of better opposing defense removes one.
Both teams' scores scale by the same pace multiplier. Pace uses the existing
captured possessions/plays blend, so it is an opportunity proxy rather than a
literal independently estimated count of drives. Negative scoring expectations
are floored at zero, with the floor explicitly recorded.

The home-minus-away special-teams rating difference scales by pace. Full live
home-field advantage is added once (zero at neutral sites). Existing incremental
personnel, continuity and weather point adjustments are applied once. The old
regression score, schedule/regime correction and old residual-mean adjustments
do not contribute to the new scoring baseline. The old forecast is saved for
comparison and its named live overlays are retained.

Spreads and totals are calculated from the two expected scores, then compared
with the available line. No spread, market total, actual result or preference
confidence score is an input to the new baseline. There is no favorite bonus,
Under bonus or forced equal count of betting directions. Slow pace can help an
underdog by shrinking the expected margin; it does not automatically make every
underdog valuable at every line.

Ryan's numbered 1–5 preferences remain ordinal judgments. They informed the
behavioral requirements (opposing defense can suppress offense; slow pace lowers
scoring opportunities), but are not treated as literal points or win percentages.
This version does not claim to reproduce every subjective confidence rating.

## Persistence and records

`Matchup Projection JSON` records the exact scoring decomposition, full-precision
scores, source population, observed/input clocks, schema/model identity and hash.
Spread/total grade captures reference the same projection hash and deterministic
scores. Slate and tracker score columns use the new deterministic projection;
the comparison layer and displayed point edges use it too. Simulation estimates
are still distribution approximations and explicitly remain uncalibrated for
this model. Old regression residual widths do not establish new-model accuracy.

The grade-qualification rules and entered-price checks are retained under a new
policy identity. A/B labels describe provisional matchup support, not proven win
probabilities. A projection failure is explicitly a legacy fallback and cannot
receive a new-model A/B grade. Old captures remain readable, while the new
point model starts its own forward record rather than inheriting old results.
Existing finalized snapshots, historical forecasts and official picks are not
rewritten or republished.

## Replay and verification

```
python -m research.replay_ncaaf_matchup_projection \
  --slate saved_slate.json --output replay.json
```

This command accepts original saved factors, paired quotes and pregame live
overlay captures. It never uses final scores. Its output is explicitly marked
`RETROSPECTIVE_FORMULA_REPLAY`: the formula is being computed now, not represented
as a model published before the historical game. It cannot enter the live record.

The November 14 replay processed 55 games without exclusions. The construction
was specified from rating units and directional requirements, not fitted to
these games' outcomes. Behavioral tests cover average pairings, offensive and
defensive monotonicity, above-average offense against strong defense, slow pace
and underdog margin, fast scoring pairings, weak offenses/strong defenses,
market/result independence, overlay accounting, team symmetry, provenance,
neutral venues, builder persistence and fallback grade exclusion.

Further validation needs complete, time-verified final scores and later games.
The separate chronological research tool remains available to compare the old
baseline and incremental factors without test-date tuning. None of this replay
establishes profitability, a calibrated confidence scale or an optimal threshold.
