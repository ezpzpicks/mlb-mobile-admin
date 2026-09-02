# V16.5 Top-2 Whiff Validation

V16.5 jointly refits the existing pitcher K-rate mean equation with usage-weighted season-to-date Whiff% across the starter's two most-used pitches. Each primary pitch requires at least 85 live Savant pitches; insufficient history falls back to the locked 2025 Top-2 training mean, making the Top-2 term neutral.

## Validation

2025 training / untouched 2026 holdout through September 1, 2026:

- Current six-variable refit: R² 0.248175, RMSE 2.110572, MAE 1.673890.
- Seven-variable + Top-2 refit: R² 0.252511, RMSE 2.104478, MAE 1.669341.
- Delta: R² +0.004335, RMSE -0.006094, MAE -0.004549.
- Top-2 coefficient: +0.055147 logit units per 1 training SD; clustered p = 9.58e-08.
- Top-2 mean 21.30%; SD 4.94 percentage points.
- Approximate local effect near 23 BF: +0.044 K per +1 percentage point of Top-2 Whiff.

Direction also replicated from a 2024 fit into 2025: R² +0.004073, RMSE -0.005691, MAE -0.004937.

A separate test that simply bolted the Top-2 coefficient onto the frozen V16.4 equation worsened 2026 results, so production uses the full joint seven-variable refit rather than double-counting overlapping pitcher K/Whiff/velocity information.

## Preserved behavior

V16.5 does not add lineup overall Whiff or exact batter-vs-pitch Whiff to the numeric projection. It preserves the V16.4 locked-mean behavior, workload/early-exit BF mixture, mean-preserving multi-K tail, -5 point Under decision calibration, symmetric publication handling, current full-workload starter eligibility, and persistent pitcher-history safeguards.
