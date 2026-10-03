# CFB 2026 Weeks 1-4 Regime Search

Game counts: {1: 51, 2: 49, 3: 57, 4: 58}

Chosen from W2-3 walk-forward only: **power_confirmation**, alpha=16, cap=6

| Window | Model | N | MAE | Steps | slope | AUC | 2+ | 4+ | 6+ | 8+ | 10+ |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| W2-3 selection | Baseline | 105 | 12.140 | 2/4 | -6.4pp | 0.523 | 41-49 (45.6%) | 32-34 (48.5%) | 22-23 (48.9%) | 15-16 (48.4%) | 9-14 (39.1%) |
| W2-3 selection | Challenger | 105 | 11.968 | 4/4 | +4.9pp | 0.546 | 41-40 (50.6%) | 34-32 (51.5%) | 28-25 (52.8%) | 20-17 (54.1%) | 15-12 (55.6%) |
| W4 hard holdout | Baseline | 57 | 13.719 | 0/3 | -14.7pp | 0.417 | 21-26 (44.7%) | 16-20 (44.4%) | 10-13 (43.5%) | 3-7 (30.0%) | 2-4 (33.3%) |
| W4 hard holdout | Challenger | 57 | 14.031 | 2/4 | -7.5pp | 0.491 | 21-20 (51.2%) | 17-16 (51.5%) | 14-14 (50.0%) | 9-12 (42.9%) | 7-9 (43.8%) |
| W2-4 pooled OOS | Baseline | 162 | 12.696 | 2/4 | -7.3pp | 0.491 | 62-75 (45.3%) | 48-54 (47.1%) | 32-36 (47.1%) | 18-23 (43.9%) | 11-18 (37.9%) |
| W2-4 pooled OOS | Challenger | 162 | 12.694 | 3/4 | +0.3pp | 0.525 | 62-60 (50.8%) | 51-48 (51.5%) | 42-39 (51.9%) | 29-29 (50.0%) | 22-21 (51.2%) |

## Top current-year candidates

| Family | Alpha | Cap | MAE | Steps | slope | 10+ | AUC |
|---|---:|---:|---:|---:|---:|---:|---:|
| power_confirmation | 16 | 6 | 11.968 | 4/4 | +4.9pp | 55.6% | 0.546 |
| power_confirmation | 4 | 6 | 12.046 | 3/4 | +3.8pp | 53.8% | 0.507 |
| quality_compact | 64 | 2 | 12.012 | 3/4 | +5.8pp | 52.6% | 0.484 |
| quality_turnover | 16 | 6 | 12.128 | 3/4 | +8.3pp | 57.7% | 0.511 |
| quality_returning | 256 | 2 | 12.105 | 3/4 | +3.3pp | 50.0% | 0.479 |
| power_confirmation | 16 | 2 | 11.995 | 3/4 | +3.6pp | 50.0% | 0.507 |
| power_confirmation | 16 | 8 | 12.095 | 3/4 | +2.1pp | 53.3% | 0.530 |
| quality_turnover | 16 | 4 | 12.088 | 3/4 | +5.9pp | 54.2% | 0.520 |
| quality_turnover | 16 | 2 | 12.031 | 3/4 | +4.3pp | 50.0% | 0.493 |
| power_confirmation | 64 | 4 | 11.991 | 3/4 | +0.0pp | 50.0% | 0.548 |
| quality_returning_turnover | 256 | 10 | 12.124 | 3/4 | +2.2pp | 52.2% | 0.536 |
| quality_compact | 64 | 6 | 12.121 | 3/4 | +4.8pp | 56.0% | 0.505 |
| power_confirmation | 64 | 2 | 12.022 | 3/4 | +4.0pp | 50.0% | 0.493 |
| quality_compact | 64 | 4 | 12.061 | 3/4 | +4.8pp | 54.2% | 0.499 |
| quality_full | 4 | 2 | 12.068 | 2/4 | +5.3pp | 52.6% | 0.521 |
| quality_compact | 16 | 6 | 12.049 | 2/4 | +5.4pp | 57.7% | 0.549 |
| quality_full | 4 | 4 | 12.139 | 2/4 | +5.4pp | 56.0% | 0.534 |
| quality_compact | 16 | 2 | 12.015 | 2/4 | +5.3pp | 52.6% | 0.499 |
| returning | 64 | 2 | 12.139 | 2/4 | +0.9pp | 47.6% | 0.524 |
| quality_compact | 4 | 6 | 12.042 | 2/4 | +4.8pp | 57.7% | 0.551 |
