# NFL spread investigation — November 21, 2026

Research only. No production model, grade, saved pick or settlement is changed.

This specification is recorded before fitting the new candidates. Previous
research already examined 2025/2026 baseline results, so the later evaluations
are chronological checks of a new specification, not a pristine holdout for the
entire research program.

## Questions

1. Are saved spread selections, prices, probabilities and results consistent?
2. Do lagged team statistics add predictive information beyond the market?
3. Do a few explicit matchup interactions improve cover probabilities?
4. Does an earlier-period selection rule persist in later games?

## Data and scope

- NFL regular-season spreads only; no other sport or player-prop model.
- Reuse the hashed 2021–2026 pregame dataset from the team-weight study.
- Join the original schedule snapshot for market prices, rest and venue.
- Verify spread sign: nflverse `spread_line` is expected home margin; the
  corresponding home betting handicap is its negative.
- Use recorded home and away spread prices; exclude missing/invalid prices
  from bet returns. A push refunds the stake and is excluded from the binary
  probability score, but is retained in bet and ROI counts.
- Add prior/current blended passing-play shares from matching PBP snapshots.
  Current-season shares use earlier weeks only. No actual starting QB,
  game-time weather, current-game result or closing-line movement is a feature.
- The schedule's historical spread/price is a closing-market proxy. It is not
  proof that the same quote was available at an earlier decision time.

## Frozen candidate set

1. Market-only regularized logistic model: line, absolute line, total, integer
   line indicator, key-line indicators at absolute 3/7, and no-vig home probability.
2. Additive statistical logistic model: market variables plus own-unit offense
   and defense differences, rest, venue, pass-share difference and season weight.
3. Matchup logistic model: the additive features plus five predefined interactions:
   pass-share-weighted passing matchup, rush-share-weighted rushing matchup,
   pass pressure against vulnerable protection, explosive offense against
   explosive-defense weakness, and red-zone offense against red-zone weakness.
4. Ridge market-residual model: predict actual home margin minus market margin,
   using the matchup feature set. Convert the residual forecast to a raw cover
   probability using training residual volatility; calibration is separate.

Logistic C is chosen from 0.01, 0.1, 1.0. Ridge alpha is chosen from 8, 32, 128.
All scalers are fit on training data only.

## Chronology and decision rule

- Train initial candidates on 2021–2022; select each penalty and the primary
  family by 2023 binary log loss on non-push games.
- Refit each chosen base model on 2021–2023.
- Fit a monotone sigmoid recalibration on Weeks 1–9 of 2024 only. Its two
  parameters are weakly shrunk toward identity and slope is constrained to
  remain nonnegative. Record raw and calibrated probability diagnostics.
- On Weeks 10–18 of 2024, choose the primary family's bet threshold from
  probability advantages of 2, 4, 6 percentage points over the offered price.
  Require at least 30 selections and positive validation units. If none qualifies,
  the selected policy passes every game.
- Freeze the models, calibration and policy for the 2025 and completed 2026
  checks. Do not select a different family or threshold based on those outcomes.
- Report a fixed 2-point probability-edge policy for every candidate as a
  diagnostic, whether or not it qualifies as the selected policy. Do not promote
  a winning diagnostic after inspecting the later checks.

## Evaluation

Probability: log loss, Brier loss and calibration bins, with paired weekly
bootstrap differences versus the no-vig market probability. Economic: win/loss/
push counts, actual source price payouts, stake-one-unit profit and ROI, paired
week bootstrap intervals and selection volume. Forecast error is secondary.

Audit the actual saved ledger independently against official schedule scores.
Keep saved-record results separate from reconstructed historical model results,
unknown capture times separate from demonstrable pregame captures, and early
records through October 19 separate from later records. Descriptive groups are
grade, favorite/dog, home/away, probability band and model version. These are
diagnostics, not automatically deployable betting rules.

## Reproduction

Install `research/requirements-nfl-market-edges.txt` and run
`python -m unittest research.test_nfl_market_edges -v`.

The frozen data package can be extracted into `artifacts/nfl_market_edges`.
It includes the original inputs under `inputs/nfl_team_weights`, the captured
game-feature matrix, pass-share features, source manifests, filtered saved ledger
and saved slates. Replaying it requires no new PBP download:

```bash
python -m research.nfl_market_edges \
  --input-dir artifacts/nfl_market_edges/inputs/nfl_team_weights \
  --output-dir artifacts/nfl_market_edges
python -m research.nfl_saved_spread_audit \
  --schedule artifacts/nfl_market_edges/inputs/nfl_team_weights/data/games.csv \
  --output-dir artifacts/nfl_market_edges
```

To rebuild passing shares rather than replay their captured values, supply
`--pbp-cache` pointing to `pbp_YEAR.parquet` files for 2020–2026 matching the
manifest. The original raw PBP assets are intentionally not included in the
small data package.
