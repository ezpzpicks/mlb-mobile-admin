# NFL Game Regression v4

## Goal

Replace the NFL game engine's hand-tuned score, margin, and total weights with a leakage-safe historical regression while preserving the production data pipeline, progressive prior/current-season weighting, rolling home-field model, current lineup/injury overlays, manual overrides, grading UI, and player-prop models.

## Validation design

- Discovery: 2021-2023 regular seasons.
- Confirmation: 2024 regular season, used to reject sign-unstable variables.
- Holdout: 2025 regular season, untouched until final out-of-sample evaluation.
- Current-game market spread and total were never predictors. They were used only for post-projection edge evaluation.
- Production nflverse metric definitions and the same progressive season blending used by the live builder were used to construct pregame features.
- Rolling home-field advantage remains a separate pregame ridge-regression offset because the production builder already estimates it statistically and a simple home indicator is not separately identifiable from an intercept in an almost-all-home-site sample.

## Winning formulation

The final model predicts each team's score separately, then derives margin and total from the two projected scores. This mirrors the per-team scoring structure used by the MLB engine.

Final team-score equation:

`team_score = -5.882194 + 0.820611 * scoring_matchup + 22.972280 * success_matchup + 1.296898 * weather_adjustment`

Where:

- `scoring_matchup = 0.50 * own Points/Game + 0.50 * opponent Points Allowed/Game`
- `success_matchup = own Off Success Rate - opponent Def Success Edge`
- `weather_adjustment` is the existing production weather transformation.
- The rolling home-field value is applied after the regression as `+HFA/2` to the home team and `-HFA/2` to the away team.

## Variable selection

The three retained variables were statistically significant in the 2021-2023 discovery sample and kept the same coefficient direction in the 2024 confirmation sample.

Rejected as independent team-score predictors after backward significance testing and confirmation stability checks:

- Pass EPA matchup
- Rush EPA matchup
- Explosive-play matchup
- Turnover pressure
- Sack/pressure matchup
- Red-zone matchup
- Pace
- Rest/travel adjustment

This does not mean those concepts never matter in football. It means they did not add stable independent predictive value once the retained pregame variables were already in this formulation.

## 2025 out-of-sample results

| Metric | Previous heuristic engine | NFL v4 regression | Change |
| --- | ---: | ---: | ---: |
| Margin MAE | 10.6742 | 10.3370 | -3.16% |
| Margin RMSE | 13.4483 | 13.2300 | -1.62% |
| Total MAE | 10.8846 | 10.5105 | -3.44% |
| Total RMSE | 13.6789 | 13.3197 | -2.63% |

Raw holdout edge accuracy:

| Edge filter | NFL v4 | Previous engine |
| --- | ---: | ---: |
| Spread >= 1.5 pts | 48.37% (184) | 43.22% (199) |
| Spread >= 2.5 pts | 46.51% (129) | 42.95% (149) |
| Spread >= 3.5 pts | 46.07% (89) | 39.09% (110) |
| Total >= 1.75 pts | 52.48% (141) | 50.27% (185) |
| Total >= 3.0 pts | 54.84% (62) | 51.37% (146) |
| Total >= 4.0 pts | 56.67% (30) | 54.29% (105) |

The spread projection improves materially versus the previous heuristic model, but the raw spread edge groups remain below 50% on the 2025 holdout. Spread profitability is therefore not claimed from regression edge alone; production confluence, personnel, and reliability gates remain important safeguards.

## Simulation calibration

The former hand-set game simulation volatility was replaced with residual behavior observed on the 2025 holdout:

- Margin residual SD: 13.2515 points
- Total residual SD: 13.3384 points
- Margin/total residual correlation: -0.0296

Reliability now modestly scales those observed residual distributions rather than starting from arbitrary standard deviations.

## Production overlays retained

The base score projection is regression-selected. The following are applied afterward because they are current-game information that could not be reconstructed consistently across the historical training sample:

- Rolling home-field advantage
- Current QB/OL/skill manual adjustments
- Current front-seven/secondary manual adjustments
- Lineup and injury absence costs
- Special-teams manual override
- Manual margin and total adjustments

The old automatic rest coefficient is intentionally not applied to the v4 base game projection because rest did not survive the historical significance/stability screen.

## Isolation

NFL v4 is installed through an NFL-only integration layer. The MLB builder and the existing NFL player-prop regression code are not modified by the game-model replacement.

## Market-relative matchup support (November 15, 2026)

Spread and total confluence now compares scoring and success-rate support with
the actual quoted spread or total. Previously these checks compared the teams'
raw strength or used fixed scoring/success benchmarks, which could penalize an
underdog covering a large spread or an Over at a low total.

Each supporting check uses the existing team-score equation, retains its own
matchup input, and holds the other input at the scoring engine's existing
neutral value (22.5 scoring points or 0.43 success rate). The published forecast
minus its unadjusted regression base supplies the common venue, weather,
personnel, manual and score-bound offsets exactly once. The resulting point
margin is compared with the home spread; the point total is compared with the
quoted total. Exact pushes, missing quotes and non-finite data provide no
directional matchup support. These component estimates are grade checks rather
than additional score forecasts or independent probability estimates.

Both the interactive and scheduled builders pass the quote and saved projection
to the same functions. Forecast equations, simulations, probability/edge and
reliability requirements, the remaining supporting checks and player-prop
models retain their existing behavior. Game slate and tracker rows carry
`nfl-market-relative-confluence-v1-2026-11-15` separately from the unchanged
forecast identity. Existing saved games and historical grades are not rewritten.
The slate also freezes the two signed component edges per market. The website
rechecks both against a moved line, in addition to the existing model-edge gate,
instead of carrying forward the saved support count unchanged.
This is a grading policy change, not a new profitability claim.
