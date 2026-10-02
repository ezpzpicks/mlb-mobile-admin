# CFB Power Confirmation Interaction Edge Search

Chosen on 2024: quality_confirmation_only alpha=64.0 cap=8.0

| Season | Model | Steps | slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2024 | Baseline | 2/4 | +0.3pp | 0.531 | 13.036 | 285-261 (52.2%) | 186-173 (51.8%) | 114-104 (52.3%) | 75-60 (55.6%) | 42-38 (52.5%) |
| 2024 | Power confirmation | 4/4 | +8.9pp | 0.520 | 12.995 | 259-261 (49.8%) | 170-168 (50.3%) | 104-99 (51.2%) | 70-54 (56.5%) | 37-26 (58.7%) |
| 2025 | Baseline | 3/4 | +8.8pp | 0.539 | 12.395 | 307-261 (54.0%) | 190-167 (53.2%) | 124-99 (55.6%) | 75-53 (58.6%) | 44-26 (62.9%) |
| 2025 | Power confirmation | 4/4 | +8.4pp | 0.508 | 12.324 | 277-244 (53.2%) | 176-141 (55.5%) | 107-84 (56.0%) | 70-45 (60.9%) | 40-25 (61.5%) |
| 2026 | Baseline | 2/4 | -5.5pp | 0.495 | 13.485 | 77-98 (44.0%) | 59-73 (44.7%) | 41-47 (46.6%) | 22-32 (40.7%) | 15-24 (38.5%) |
| 2026 | Power confirmation | 3/4 | +4.9pp | 0.532 | 13.639 | 76-97 (43.9%) | 59-70 (45.7%) | 49-48 (50.5%) | 36-33 (52.2%) | 21-22 (48.8%) |

## Top 2024 candidates

| Family | Alpha | Cap | Steps | slope | 10+ | AUC | MAE |
|---|---:|---:|---:|---:|---:|---:|---:|
| quality_confirmation_only | 64 | 8 | 4/4 | +8.9pp | 58.7% | 0.520 | 12.995 |
| quality_confirmation_only | 64 | 6 | 4/4 | +8.3pp | 58.1% | 0.522 | 12.990 |
| quality_confirmation_only | 16 | 8 | 4/4 | +7.6pp | 57.6% | 0.511 | 13.002 |
| power_all_confirmation | 64 | 6 | 4/4 | +7.1pp | 56.7% | 0.525 | 12.970 |
| power_all_confirmation | 256 | 6 | 4/4 | +6.5pp | 55.4% | 0.510 | 12.973 |
| power_all_confirmation | 64 | 8 | 4/4 | +6.4pp | 55.7% | 0.522 | 12.972 |
| quality_confirmation_only | 16 | 6 | 4/4 | +6.4pp | 56.5% | 0.513 | 12.993 |
| quality_confirmation_only | 64 | 4 | 4/4 | +6.2pp | 56.2% | 0.521 | 12.987 |
| power_all_confirmation | 256 | 8 | 4/4 | +5.8pp | 54.7% | 0.509 | 12.968 |
| power_fpi_confirmation | 16 | 6 | 4/4 | +5.2pp | 56.5% | 0.508 | 12.853 |
| power_fpi_confirmation | 16 | 8 | 4/4 | +5.2pp | 56.5% | 0.506 | 12.845 |
| power_all_confirmation | 1 | 6 | 4/4 | +5.0pp | 55.2% | 0.532 | 12.968 |
| power_fpi_confirmation | 64 | 6 | 4/4 | +4.9pp | 55.7% | 0.511 | 12.856 |
| power_fpi_confirmation | 64 | 8 | 4/4 | +4.9pp | 55.7% | 0.508 | 12.851 |
| power_all_confirmation | 64 | 4 | 4/4 | +4.5pp | 54.1% | 0.514 | 12.967 |
| power_all_confirmation | 4 | 6 | 4/4 | +4.1pp | 54.2% | 0.527 | 12.968 |
| quality_confirmation_only | 1024 | 6 | 3/4 | +5.7pp | 55.6% | 0.513 | 12.960 |
| quality_confirmation_only | 16 | 4 | 3/4 | +5.3pp | 55.6% | 0.516 | 12.985 |
| power_all_confirmation | 1 | 8 | 3/4 | +3.5pp | 53.3% | 0.526 | 12.979 |
| power_all_confirmation | 16 | 6 | 3/4 | +3.1pp | 53.3% | 0.523 | 12.968 |
