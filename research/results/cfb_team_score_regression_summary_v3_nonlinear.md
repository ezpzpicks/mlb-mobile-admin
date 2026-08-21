# CFB team-score regression v3 nonlinear refinement

Nonlinear form selected using 2021-23 discovery + 2024 confirmation only; 2025 remained untouched until selection.
Archived sportsbook lines are evaluation-only and never predictors.

Chosen nonlinear feature: none (linear base retained)
Discovery thresholds: q70=18.066, q80=22.411, q90=29.449

## 2025 holdout
- Linear v2 margin MAE: 13.602
- Chosen margin MAE: 13.602
- Margin MAE improvement vs v2 linear: 0.00%
- Linear v2 margin RMSE: 17.608
- Chosen margin RMSE: 17.608
- Linear v2 total MAE: 12.896
- Chosen total MAE: 12.896

## 21-27.5 favorites
- Games: 152
- Linear margin MAE: 15.635
- Chosen margin MAE: 15.635
- Linear mean abs margin: 18.323
- Chosen mean abs margin: 18.323
- Actual mean abs margin: 26.737

## 28+ favorites
- Games: 159
- Linear margin MAE: 15.567
- Chosen margin MAE: 15.567
- Linear mean abs margin: 26.856
- Chosen mean abs margin: 26.856
- Actual mean abs margin: 37.642
- Market mean spread: 36.101
- Chosen ATS direction accuracy: 50.94%

## USC-San Jose State prior-only sanity case
### Linear v2
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
### Chosen refinement
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
