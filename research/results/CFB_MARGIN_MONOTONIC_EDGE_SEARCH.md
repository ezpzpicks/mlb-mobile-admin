# CFB Monotonic-First Margin Search

Baseline 2024: steps 2/4, slope +0.3pp, AUC 0.531
Baseline 2025: steps 3/4, slope +8.8pp, AUC 0.539

| Family | 2024 steps | slope | 10+ | 2025 steps | slope | 10+ | 2025 AUC |
|---|---:|---:|---:|---:|---:|---:|---:|
| advanced::scoring_defense_core | 4/4 | +4.6pp | 56.1% | 3/4 | +10.6pp | 63.6% | 0.512 |
| advanced::finish_trench_disruption | 4/4 | +4.6pp | 56.2% | 3/4 | +8.7pp | 61.2% | 0.499 |
| context::margin_possession_interaction | 4/4 | +4.3pp | 54.7% | 3/4 | +10.5pp | 63.0% | 0.510 |
| advanced::finishing_plus_trench | 3/4 | +2.8pp | 54.4% | 3/4 | +11.2pp | 63.8% | 0.505 |
| context::margin_nonlinearity | 3/4 | +4.0pp | 53.9% | 3/4 | +11.1pp | 64.2% | 0.509 |
| advanced::finishing_plus_pass | 3/4 | +3.3pp | 54.7% | 2/4 | +10.7pp | 63.4% | 0.515 |
| advanced::finishing_plus_rush | 3/4 | +1.8pp | 53.5% | 4/4 | +12.2pp | 64.9% | 0.518 |
| advanced::finishing_plus_turnover | 3/4 | +4.1pp | 55.6% | 3/4 | +11.8pp | 63.8% | 0.506 |
| balance::nonlinear_matchup | 3/4 | +5.6pp | 56.0% | 4/4 | +11.7pp | 62.9% | 0.498 |
| context::share_plus_total | 3/4 | +3.0pp | 53.3% | 3/4 | +9.4pp | 62.0% | 0.502 |
| context::margin_total_poss | 3/4 | +2.0pp | 52.5% | 4/4 | +12.0pp | 63.9% | 0.497 |
| balance::power_two_way | 3/4 | +2.8pp | 53.3% | 4/4 | +11.0pp | 62.7% | 0.500 |
| context::margin_recalibration | 3/4 | +2.4pp | 52.6% | 3/4 | +9.8pp | 62.5% | 0.507 |
| context::margin_shape | 3/4 | +2.3pp | 52.6% | 4/4 | +11.8pp | 64.2% | 0.501 |
| context::margin_total_interaction | 3/4 | +2.0pp | 52.5% | 4/4 | +12.0pp | 63.9% | 0.497 |
| balance::margin_two_way | 3/4 | +3.0pp | 53.3% | 4/4 | +9.0pp | 61.4% | 0.515 |
| context::all_scoring_context | 3/4 | +1.3pp | 51.9% | 4/4 | +11.4pp | 63.4% | 0.497 |
| context::point_share | 3/4 | +1.7pp | 51.9% | 4/4 | +9.5pp | 62.0% | 0.504 |
| context::margin_ppd_interaction | 3/4 | +2.4pp | 52.6% | 3/4 | +9.8pp | 62.5% | 0.507 |
| context::margin_total_ppd | 3/4 | +2.2pp | 52.6% | 3/4 | +9.7pp | 62.5% | 0.507 |

## 2024-selected candidate
- advanced::scoring_defense_core alpha=4.0 cap=3.0

| Edge | Baseline 2025 | Candidate 2025 |
|---:|---:|---:|
| 2+ | 307-261 (54.0%) | 284-251 (53.1%) |
| 4+ | 190-167 (53.2%) | 177-168 (51.3%) |
| 6+ | 124-99 (55.6%) | 105-97 (52.0%) |
| 8+ | 75-53 (58.6%) | 66-43 (60.6%) |
| 10+ | 44-26 (62.9%) | 42-24 (63.6%) |
