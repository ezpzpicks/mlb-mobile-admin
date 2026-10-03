# CFB 2026 Weeks 1-4 Stable Greedy Search

Selected features: **gamecontrol_diff, def_returning_diff, explosive_diff, current_power_margin, abs_baseline, baseline**
alpha=16, cap=6

| Model | MAE | Steps | slope | AUC | 2+ | 4+ | 6+ | 8+ | 10+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 12.696 | 2/4 | -7.3pp | 0.491 | 62-75 (45.3%) | 48-54 (47.1%) | 32-36 (47.1%) | 18-23 (43.9%) | 11-18 (37.9%) |
| Stable challenger | 12.177 | 3/4 | +22.0pp | 0.551 | 55-64 (46.2%) | 38-47 (44.7%) | 33-32 (50.8%) | 26-16 (61.9%) | 15-7 (68.2%) |

## Held-out week MAE

| Week | Baseline | Challenger | Improvement |
|---:|---:|---:|---:|
| 2 | 13.806 | 13.322 | +0.484 |
| 3 | 10.683 | 10.275 | +0.408 |
| 4 | 13.719 | 13.062 | +0.656 |

## Greedy path

| Step | Added | Features | MAE | Steps | slope | AUC |
|---:|---|---|---:|---:|---:|---:|
| 1 | gamecontrol_diff | gamecontrol_diff | 12.589 | 2/4 | -0.4pp | 0.472 |
| 2 | def_returning_diff | gamecontrol_diff, def_returning_diff | 12.391 | 3/4 | +4.8pp | 0.461 |
| 3 | explosive_diff | gamecontrol_diff, def_returning_diff, explosive_diff | 12.310 | 3/4 | +10.3pp | 0.516 |
| 4 | current_power_margin | gamecontrol_diff, def_returning_diff, explosive_diff, current_power_margin | 12.239 | 4/4 | +17.0pp | 0.523 |
| 5 | abs_baseline | gamecontrol_diff, def_returning_diff, explosive_diff, current_power_margin, abs_baseline | 12.213 | 4/4 | +18.6pp | 0.501 |
| 6 | baseline | gamecontrol_diff, def_returning_diff, explosive_diff, current_power_margin, abs_baseline, baseline | 12.177 | 3/4 | +22.0pp | 0.551 |

## Final standardized coefficients (fit on W1-4)

```json
{
  "intercept": 0.33212597431128893,
  "gamecontrol_diff": 2.38215799102589,
  "def_returning_diff": 1.752122227468613,
  "explosive_diff": 1.6818564966662708,
  "current_power_margin": -1.691613278694596,
  "abs_baseline": 2.0489972644589174,
  "baseline": -2.593999808185213
}
```
