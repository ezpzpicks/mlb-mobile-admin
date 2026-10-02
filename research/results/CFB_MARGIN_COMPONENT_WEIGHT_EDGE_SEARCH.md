# CFB Margin Component-Weight Edge Search

Baseline 2024: 2/4 steps, slope +0.3pp, AUC 0.531
Baseline 2025: 3/4 steps, slope +8.8pp, AUC 0.539

| Component | Mult | 2024 steps | slope | 10+ | 2025 steps | slope | 10+ | 2025 AUC |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| home_indicator | 0.25 | 4/4 | +4.9pp | 55.3% | 4/4 | +14.7pp | 66.7% | 0.542 |
| home_indicator | 0.00 | 4/4 | +4.2pp | 53.7% | 3/4 | +14.4pp | 65.6% | 0.528 |
| prior_ppg_diff | 0.00 | 3/4 | +5.6pp | 58.0% | 3/4 | +4.2pp | 57.1% | 0.529 |
| prior_power_margin | 1.50 | 3/4 | +2.3pp | 52.1% | 4/4 | +7.3pp | 59.8% | 0.519 |
| prior_power_margin | 0.00 | 3/4 | +4.3pp | 54.9% | 2/4 | -0.6pp | 53.1% | 0.518 |
| current_power_margin | 1.50 | 3/4 | +1.5pp | 52.8% | 2/4 | +4.1pp | 57.7% | 0.524 |
| prior_power_margin | 1.75 | 3/4 | +2.1pp | 50.8% | 4/4 | +1.8pp | 53.7% | 0.507 |
| prior_power_margin | 2.00 | 3/4 | +3.5pp | 51.4% | 2/4 | +0.1pp | 52.4% | 0.509 |
| current_power_margin | 1.75 | 3/4 | +0.2pp | 52.6% | 3/4 | +6.6pp | 60.7% | 0.544 |
| prior_papg_diff | 1.50 | 3/4 | +1.8pp | 54.0% | 3/4 | +6.1pp | 60.3% | 0.544 |
| prior_papg_diff | 1.75 | 3/4 | +2.6pp | 54.0% | 4/4 | +6.0pp | 60.0% | 0.526 |
| prior_power_margin | 0.25 | 2/4 | +5.0pp | 56.3% | 2/4 | +0.1pp | 53.5% | 0.516 |
| current_power_margin | 1.25 | 2/4 | +1.9pp | 54.8% | 3/4 | +9.9pp | 64.3% | 0.530 |
| prior_power_margin | 0.75 | 2/4 | +3.3pp | 54.6% | 3/4 | +7.0pp | 60.0% | 0.542 |
| current_allowed_diff | 2.00 | 2/4 | +0.5pp | 52.3% | 3/4 | +8.2pp | 62.7% | 0.518 |
| home_indicator | 1.25 | 2/4 | +1.5pp | 53.6% | 3/4 | +5.3pp | 58.0% | 0.519 |
| current_allowed_diff | 0.50 | 2/4 | +2.0pp | 54.3% | 4/4 | +9.0pp | 62.0% | 0.538 |
| current_power_margin | 0.25 | 2/4 | +1.7pp | 50.9% | 3/4 | +3.5pp | 54.5% | 0.531 |
| current_allowed_diff | 0.75 | 2/4 | +0.4pp | 53.1% | 3/4 | +10.8pp | 64.7% | 0.541 |
| current_power_margin | 2.00 | 2/4 | -0.0pp | 52.3% | 4/4 | +3.4pp | 57.5% | 0.535 |
| current_scoring_diff | 1.75 | 2/4 | +1.3pp | 54.2% | 3/4 | +5.2pp | 58.2% | 0.526 |
| home_indicator | 0.50 | 2/4 | +2.9pp | 53.3% | 3/4 | +17.0pp | 68.6% | 0.505 |
| current_scoring_diff | 0.75 | 2/4 | -0.9pp | 51.3% | 3/4 | +7.6pp | 62.0% | 0.535 |
| current_scoring_diff | 1.25 | 2/4 | -0.2pp | 52.4% | 3/4 | +4.8pp | 58.6% | 0.526 |
| current_power_margin | 0.50 | 2/4 | +0.7pp | 51.2% | 3/4 | +9.7pp | 61.4% | 0.521 |

## 2024-selected change
- home_indicator × 0.25

| Edge | Baseline 2025 | Candidate 2025 |
|---:|---:|---:|
| 2+ | 307-261 (54.0%) | 285-263 (52.0%) |
| 4+ | 190-167 (53.2%) | 188-170 (52.5%) |
| 6+ | 124-99 (55.6%) | 113-98 (53.6%) |
| 8+ | 75-53 (58.6%) | 72-55 (56.7%) |
| 10+ | 44-26 (62.9%) | 50-25 (66.7%) |
