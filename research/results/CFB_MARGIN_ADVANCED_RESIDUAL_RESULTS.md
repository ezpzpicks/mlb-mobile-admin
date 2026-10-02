# CFB Advanced Margin Residual Research

Sportsbook lines are evaluation-only and never predictors.
- Train: (2021, 2022, 2023)
- Validation: 2024
- Holdout: 2025
- Baseline validation MAE: 13.356
- Baseline holdout MAE: 12.623

| Family | 2024 MAE | 2024 Δ | 2025 MAE | 2025 Δ | Alpha | Cap |
|---|---:|---:|---:|---:|---:|---:|
| finishing_plus_turnover | 13.288 | +0.068 | 12.682 | -0.059 | 1 | 2 |
| finishing_plus_trench | 13.293 | +0.063 | 12.657 | -0.034 | 1 | 2 |
| finish_trench_disruption | 13.298 | +0.058 | 12.661 | -0.038 | 256 | 3 |
| finishing_plus_pass | 13.312 | +0.044 | 12.726 | -0.103 | 1 | 2 |
| finishing_plus_explosive | 13.319 | +0.037 | 12.695 | -0.073 | 1 | 2 |
| finishing_defense | 13.322 | +0.034 | 12.700 | -0.078 | 256 | 4 |
| finishing_plus_ypp | 13.322 | +0.034 | 12.689 | -0.066 | 256 | 2 |
| finishing_plus_rush | 13.324 | +0.032 | 12.703 | -0.080 | 256 | 4 |
| scoring_defense_core | 13.324 | +0.032 | 12.711 | -0.088 | 256 | 2 |
| finishing_plus_third | 13.326 | +0.031 | 12.695 | -0.072 | 256 | 2 |
| finish_ypp_third | 13.326 | +0.030 | 12.691 | -0.068 | 256 | 2 |
| finishing_plus_disruption | 13.330 | +0.027 | 12.707 | -0.084 | 256 | 5 |
| finishing_matchup | 13.341 | +0.015 | 12.632 | -0.010 | 1 | 2 |

## Chosen on 2024

- Family: finishing_plus_turnover
- 2025 MAE: 12.682
- 2025 improvement: -0.059
