# CFB team-score regression v2

SportsDataverse/cfbfastR historical PBP; discovery 2021-23, confirmation 2024, untouched 2025 holdout.
Archived sportsbook lines are evaluation-only and never regression predictors.

Selected features: prior_own_ppg, prior_opp_papg, prior_power_gap, current_own_scoring_delta, current_opp_allowed_delta, current_power_delta, home_indicator

## 2025 holdout
- Games: 1657
- Regression margin MAE: 13.602
- Baseline margin MAE: 15.731
- Margin MAE improvement: 13.54%
- Regression margin RMSE: 17.608
- Baseline margin RMSE: 20.436
- Regression total MAE: 12.896
- Baseline total MAE: 12.902
- Total MAE improvement: 0.05%
- Regression total RMSE: 16.075
- Baseline total RMSE: 16.010

## 28+ favorites
- Games with archived spread: 159
- Regression margin MAE: 15.567
- ATS direction accuracy: 50.94%
- Mean model absolute margin: 26.856
- Mean actual absolute margin: 37.642
- Mean market spread: 36.101

## USC-San Jose State prior-only sanity case
```json
[
  {
    "away_team": "San Jos\u00e9 State",
    "home_team": "USC",
    "projected_away": 12.522991252260406,
    "projected_home": 42.68951797360008,
    "projected_home_margin": 30.16652672133968,
    "projected_total": 55.21250922586049,
    "note": "Prior-only sanity case using 2025 performance; no 2026 roster/portal/injury overlay."
  }
]
```
