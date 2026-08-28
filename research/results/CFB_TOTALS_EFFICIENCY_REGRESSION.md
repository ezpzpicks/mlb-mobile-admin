# CFB totals-specific pace/efficiency regression

Sportsbook spreads/totals are evaluation-only and never predictors. The fixed spread model is used only for game-script/margin context.

- Training: 2021, 2022, 2023
- Validation/model selection: 2024
- Untouched holdout: 2025
- Holdout games: 934
- Holdout games with archived market total: 0
- Chosen variant: pace_efficiency_interactions
- Chosen ridge alpha: 0.25

## 2025 holdout accuracy

- Fixed spread-regression team-score sum: MAE 12.834, RMSE 15.915, corr 0.217
- Pace x points-per-drive structural baseline: MAE 13.322, RMSE 16.368, corr 0.176
- Totals-specific regression: MAE 12.732, RMSE 15.832, corr 0.241
- MAE improvement vs pace/PPD baseline: 4.43%
- MAE improvement vs spread-score sum: 0.79%

## 2025 O/U record by absolute model edge

| Edge | Bets | W-L-P | Win rate | ROI @ -110 | O/U count |
|---:|---:|---:|---:|---:|---:|
| 1+ | 0 | 0-0-0 | — | — | 0/0 |
| 2+ | 0 | 0-0-0 | — | — | 0/0 |
| 3+ | 0 | 0-0-0 | — | — | 0/0 |
| 4+ | 0 | 0-0-0 | — | — | 0/0 |
| 5+ | 0 | 0-0-0 | — | — | 0/0 |
| 6+ | 0 | 0-0-0 | — | — | 0/0 |
| 7+ | 0 | 0-0-0 | — | — | 0/0 |
| 8+ | 0 | 0-0-0 | — | — | 0/0 |
| 9+ | 0 | 0-0-0 | — | — | 0/0 |
| 10+ | 0 | 0-0-0 | — | — | 0/0 |
| 11+ | 0 | 0-0-0 | — | — | 0/0 |
| 12+ | 0 | 0-0-0 | — | — | 0/0 |

## Grade thresholds selected on 2024 only

- B: no edge threshold met the pre-set 2024 validation standard.
- A: no edge threshold met the pre-set 2024 validation standard.

## Standardized final coefficients

- Residual intercept: -1.0775
- expected_possessions: +19.4466
- poss_x_finishing: -15.2953
- poss_x_success: -14.7917
- poss_x_epa: +12.6711
- abs_spread_margin: +11.6528
- success_matchup: +10.9380
- margin_x_possessions: -10.2383
- finishing_matchup: +9.8217
- epa_matchup: -8.9148
- poss_x_explosive: +7.6730
- explosive_matchup: -6.7918
- spread_score_sum: +4.6432
- structural_total: -4.4436
- expected_combined_ppd: -3.8822
- ypp_matchup: +3.3064
- poss_x_ypp: -3.0632
- havoc_pressure: +2.7550
- neutral: +1.5662
- red_zone_matchup: +1.3487
- pass_ppa_matchup: -1.1873
- sack_pressure: -0.9399
- line_yards_matchup: +0.6731
- rush_ppa_matchup: +0.5920
- abs_spread_margin_sq: -0.5024
- turnover_pressure: -0.3070
- third_down_matchup: +0.2402
- plays_per_possession: +0.1804
- week: +0.1392
