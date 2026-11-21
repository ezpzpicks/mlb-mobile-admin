# Rolling team-specific NFL regression research

This is an isolated research runner. It does not install a production layer,
update a tracker, or publish picks.

## Question and model

Does allowing each NFL offense and defense to have its own variable weights
improve future score, margin, and total predictions?

Each observed team score enters one joint model, with separate inputs and
coefficient blocks for the scoring offense and opposing defense. Offense and
defense each receive a distinct intercept adjustment and nine possible slopes:
scoring, passing EPA, rushing EPA, success rate, explosive plays, turnovers,
sacks/pressure, red-zone conversion, and team offensive plays per game. The
last variable measures the corresponding team's tempo, including when used
in the opposing-team block. Defense EPA/success/explosive/red-zone fields retain
the production definition of an edge relative to the league.

The league baseline is ridge-fitted on earlier seasons only. Team adjustments
are ridge-fitted jointly on prior-season scoring residuals, with a separate
fit on completed earlier weeks of the current season. Both the rating inputs
and coefficient profiles use the existing production season curve, including
its actual-games sample cap. A Week 1 profile is entirely the prior season's
profile. Each team's sample count determines its own coefficient mixture.

For each unit, the standardized slope used in a matchup is its league slope
plus that team's rolling adjustment. The forecast sums the offense terms,
opposing-defense terms, and the global intercept once. Do not add two complete
independent scoring predictions. The diagnostic independent regressions each
predict a whole residual, so their predictions are averaged instead.

## Comparison and chronology

- League fitting starts with 2021–2023 games. Pregame rating inputs also use 2020.
- 2024 selects the penalty and whether to use team intercepts, two core slopes,
  all nine unit slopes, or no team adjustment. The selection loss is the mean
  of margin MAE and total MAE. Ridge penalties are fixed to 8, 32, and 128;
  the global penalty is 10.
- 2025 and completed 2026 games provide chronological evaluation. At each
  season start, the league model refits only on earlier dataset seasons.
  Team profiles update using only earlier weeks. All scores from an NFL game
  stay in the same forecast batch. No selection uses 2025 or 2026 outcomes.
- The active production regression core is a comparison, with neutral weather,
  personnel, special-teams, and manual overlays. Its rolling pregame home-field
  estimate is applied to every model, as are its score/margin/total bounds.
- Final game weather is not a historical pregame forecast and is not a regressor.
- Betting tables use retrospective nflverse lines, assumed -110 prices, explicit
  push accounting, and fixed edge thresholds. They do not reproduce live grade,
  lineup, or posting rules, or establish when a line was available.

## Run

```bash
python -m pip install -r research/requirements-nfl-team-weights.txt
python -m unittest research.test_nfl_team_weights -v
python -m research.nfl_team_weights --as-of 2026-11-21 --output-dir artifacts/nfl_team_weights
```

For a new snapshot, use a new output directory or `--rebuild-data` and the
desired `--as-of` date. `--rebuild-data` recomputes features from local raw
caches; to fetch new raw files too, remove that output directory's `data/games.csv`
and the relevant `/tmp/ezpz_nfl_pbp_YEAR.parquet` caches first. Retain old output
directories when comparing snapshots.

The captured dataset can be replayed without downloading PBP again. Preserve
`data/pregame_team_dataset.csv` and `data_manifest.json` together. The manifest
hashes the input dataset, raw sources and relevant code. Validation rejects
duplicate/incomplete game pairs, future inputs, nonfinite regressors and a
season-weight mismatch.

## Interpretation

The CSV exports all 32 offensive and 32 defensive profiles, including global
weights, team deviations, final unit weights, raw and standardized units, sample
sizes, and prior/current mixture. Intercept rows hold a team adjustment only;
the global intercept enters the equation once. Standardized weights are points
per one training-set standard deviation, not percentages that sum to 100%.

The final profile snapshot is frozen before that week's first game, so no
results from that week enter its coefficients, even if some games have finished
by the as-of date. Reconstructed inputs are subject to provider revisions and
are not exact archived intraday builder snapshots. Sparse team samples and
correlated statistics limit interpretation; coefficients describe predictive
associations, not causal sensitivities. Week-bootstrap intervals are approximate.
The 2025 season was used by earlier model research, so it is not a wholly fresh
holdout for the overall research program. No live model change follows from
running this study.
