# CFB Margin Edge-Quality Search

FBS rows: train 2257, validation 798, holdout 807

Baseline 2024 AUC: 0.531; corr 0.095; MAE 13.036
Baseline 2025 AUC: 0.539; corr 0.102; MAE 12.395

| Family | 2024 AUC | Δ | 2024 corr | 2025 AUC | Δ | 2025 corr | 2025 MAE |
|---|---:|---:|---:|---:|---:|---:|---:|
| balance::balance_context | 0.541 | +0.010 | 0.097 | 0.502 | -0.036 | 0.105 | 12.437 |
| advanced::finishing_plus_turnover | 0.539 | +0.008 | 0.108 | 0.513 | -0.026 | 0.098 | 12.425 |
| balance::balance_product | 0.531 | +0.000 | 0.096 | 0.507 | -0.032 | 0.105 | 12.440 |
| balance::nonlinear_matchup | 0.524 | -0.006 | 0.096 | 0.501 | -0.038 | 0.100 | 12.437 |
| balance::balance_gap | 0.521 | -0.010 | 0.100 | 0.495 | -0.044 | 0.102 | 12.449 |
| balance::two_way_strength | 0.521 | -0.010 | 0.096 | 0.496 | -0.043 | 0.099 | 12.440 |
| balance::two_way_plus_cross | 0.521 | -0.010 | 0.096 | 0.496 | -0.043 | 0.100 | 12.440 |
| advanced::finishing_plus_trench | 0.517 | -0.014 | 0.103 | 0.500 | -0.039 | 0.111 | 12.401 |
| advanced::finishing_defense | 0.516 | -0.015 | 0.104 | 0.503 | -0.036 | 0.101 | 12.447 |
| balance::power_balance | 0.515 | -0.015 | 0.094 | 0.502 | -0.037 | 0.102 | 12.429 |
| balance::offense_defense_diff | 0.515 | -0.016 | 0.096 | 0.498 | -0.041 | 0.101 | 12.433 |
| balance::off_def_product | 0.515 | -0.016 | 0.094 | 0.504 | -0.035 | 0.101 | 12.433 |
| context::all_scoring_context | 0.514 | -0.017 | 0.092 | 0.497 | -0.042 | 0.104 | 12.429 |
| context::margin_total_ppd | 0.513 | -0.017 | 0.092 | 0.495 | -0.044 | 0.104 | 12.431 |
| balance::margin_balance | 0.513 | -0.018 | 0.094 | 0.501 | -0.037 | 0.102 | 12.430 |
| advanced::finishing_plus_disruption | 0.512 | -0.018 | 0.103 | 0.496 | -0.043 | 0.100 | 12.445 |
| context::margin_total_poss | 0.511 | -0.019 | 0.092 | 0.497 | -0.042 | 0.104 | 12.429 |
| context::margin_total_interaction | 0.511 | -0.020 | 0.092 | 0.497 | -0.042 | 0.104 | 12.429 |
| advanced::finish_ypp_third | 0.510 | -0.020 | 0.100 | 0.502 | -0.037 | 0.103 | 12.431 |
| advanced::finishing_plus_pass | 0.510 | -0.021 | 0.108 | 0.523 | -0.015 | 0.097 | 12.468 |

## 2024-selected candidate
- balance::balance_context alpha=64.0 cap=5.0

### Fixed threshold records

| Edge | Baseline 2025 | Candidate 2025 |
|---:|---:|---:|
| 2+ | 307-261 (54.0%) | 269-260 (50.9%) |
| 4+ | 190-167 (53.2%) | 181-161 (52.9%) |
| 6+ | 124-99 (55.6%) | 115-91 (55.8%) |
| 8+ | 75-53 (58.6%) | 65-43 (60.2%) |
| 10+ | 44-26 (62.9%) | 42-23 (64.6%) |
