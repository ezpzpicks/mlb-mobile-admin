# 2026 Large-Edge Component Diagnostic

Completed graded FBS-vs-FBS games: 211

Positive support means the component points toward the side selected by the model. Internal alignment categories use only football-model inputs.

## 8+ model edge

Record: 22-32 (40.7%)

| Component | Winner mean support | Loser mean support | Std difference |
|---|---:|---:|---:|
| current_power_margin | +1.175 | +2.480 | -0.315 |
| home_indicator | +0.437 | -0.901 | +0.279 |
| prior_power_margin | -3.463 | -6.530 | +0.270 |
| prior_ppg_diff | +0.390 | -0.115 | +0.203 |
| current_allowed_diff | -0.152 | +0.088 | -0.196 |
| prior_papg_diff | -0.079 | -0.132 | +0.026 |
| current_scoring_diff | -0.128 | -0.121 | -0.004 |

| Supporting components | Record | Win rate |
|---:|---:|---:|
| 3+ of 7 | 13-24 | 35.1% |
| 4+ of 7 | 12-12 | 50.0% |
| 5+ of 7 | 6-6 | 50.0% |
| 6+ of 7 | 1-3 | 25.0% |

### Internal football-input alignment

| Relationship | Aligned | Not aligned |
|---|---:|---:|
| Current power ↔ current scoring | 8-13 (38.1%) | 14-19 (42.4%) |
| Current power ↔ defensive change | 9-15 (37.5%) | 13-17 (43.3%) |
| Prior power ↔ current power shift | 9-4 (69.2%) | 13-28 (31.7%) |
| Current scoring ↔ defensive change | 13-15 (46.4%) | 9-17 (34.6%) |

## 10+ model edge

Record: 15-24 (38.5%)

| Component | Winner mean support | Loser mean support | Std difference |
|---|---:|---:|---:|
| current_allowed_diff | -0.525 | -0.110 | -0.415 |
| current_scoring_diff | +0.294 | -0.422 | +0.362 |
| prior_power_margin | -4.401 | -6.833 | +0.226 |
| current_power_margin | +1.217 | +1.828 | -0.187 |
| home_indicator | -0.320 | -1.201 | +0.186 |
| prior_papg_diff | +0.109 | -0.152 | +0.133 |
| prior_ppg_diff | -0.131 | -0.147 | +0.007 |

| Supporting components | Record | Win rate |
|---:|---:|---:|
| 3+ of 7 | 8-17 | 32.0% |
| 4+ of 7 | 7-7 | 50.0% |
| 5+ of 7 | 2-4 | 33.3% |
| 6+ of 7 | 1-2 | 33.3% |

### Internal football-input alignment

| Relationship | Aligned | Not aligned |
|---|---:|---:|
| Current power ↔ current scoring | 6-9 (40.0%) | 9-15 (37.5%) |
| Current power ↔ defensive change | 6-11 (35.3%) | 9-13 (40.9%) |
| Prior power ↔ current power shift | 4-2 (66.7%) | 11-22 (33.3%) |
| Current scoring ↔ defensive change | 9-12 (42.9%) | 6-12 (33.3%) |

