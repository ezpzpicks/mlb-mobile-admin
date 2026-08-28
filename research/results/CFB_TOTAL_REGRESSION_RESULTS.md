# CFB Totals Regression — 2025 Holdout

The spread/margin regression was held fixed. Sportsbook lines were not regression predictors.

- Training: 2021, 2022, 2023
- Validation/model selection: 2024
- Untouched holdout: 2025
- Holdout games: 808
- Holdout games with ESPN market total: 0
- Selected ridge alpha: 256.0

## Holdout accuracy

- Fixed spread-regression team-score sum: MAE 12.919, RMSE 15.992, corr 0.247
- Totals-specific residual regression: MAE 12.851, RMSE 15.921, corr 0.250
- MAE improvement: 0.52%

## 2025 O/U record by absolute model edge

| Edge | Bets | W-L-P | Win rate | ROI @ -110 | Over/Under count |
|---:|---:|---:|---:|---:|---:|
| 1+ | 0 | 0-0-0 | — | — | 0/0 |
| 2+ | 0 | 0-0-0 | — | — | 0/0 |
| 3+ | 0 | 0-0-0 | — | — | 0/0 |
| 4+ | 0 | 0-0-0 | — | — | 0/0 |
| 5+ | 0 | 0-0-0 | — | — | 0/0 |
| 6+ | 0 | 0-0-0 | — | — | 0/0 |
| 7+ | 0 | 0-0-0 | — | — | 0/0 |
| 8+ | 0 | 0-0-0 | — | — | 0/0 |

## Grade thresholds chosen on 2024 only

- B: no threshold met the pre-set validation standard.
- A: no threshold met the pre-set validation standard.

## Largest baseline-residual relationships in 2025

- reg_total: r=0.075
- prior_scoring_sum: r=0.040
- prior_allowed_sum: r=0.035
- reg_abs_margin_sq: r=0.031
- current_scoring_delta_sum: r=0.024
- reg_abs_margin: r=0.020

## Standardized final coefficients

- Intercept residual correction: 0.1362
- prior_allowed_sum: +1.5658
- reg_total: +1.5322
- neutral: +1.4648
- prior_scoring_sum: +0.8130
- current_allowed_delta_sum: +0.5124
- reg_abs_margin_sq: +0.3619
- week: +0.2947
- prior_power_gap_abs: +0.2050
- score_balance: +0.1155
- reg_abs_margin: -0.1111
- current_scoring_delta_sum: +0.1064
- week_sqrt: +0.0258
- current_power_delta_sum: +0.0000
