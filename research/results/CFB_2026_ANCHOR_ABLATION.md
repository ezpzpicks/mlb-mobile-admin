# CFB 2026 Four-Variable Anchor Ablation

| Model | MAE | Steps | slope | 10+ | AUC |
|---|---:|---:|---:|---:|---:|
| Full anchor | 12.239 | 4/4 | +17.0pp | 60.9% | 0.523 |
| Drop gamecontrol_diff | 12.402 | 3/4 | +8.9pp | 53.8% | 0.504 |
| Drop def_returning_diff | 12.446 | 3/4 | +9.8pp | 55.6% | 0.524 |
| Drop explosive_diff | 12.333 | 2/4 | +4.0pp | 50.0% | 0.523 |
| Drop current_power_margin | 12.310 | 3/4 | +10.3pp | 56.0% | 0.516 |

## Coefficient sign stability across held-out weeks

| Feature | Fold coefficients | Same sign? | Mean |
|---|---|---|---:|
| gamecontrol_diff | +1.970, +2.004, +0.897 | yes | +1.624 |
| def_returning_diff | +1.290, +1.790, +1.409 | yes | +1.497 |
| explosive_diff | +1.108, +1.514, +1.861 | yes | +1.495 |
| current_power_margin | -1.825, -1.275, -1.377 | yes | -1.492 |
