# CFB Nonlinear Margin Edge Search

Rows: train=2257, 2024=798, 2025=807, 2026=215
Football-only feature count: 81
Chosen on 2024: `extra,depth=6,leaf=30,max_features=1.0,trees=350`, correction cap=2

Sportsbook spreads were evaluation-only and never entered the prediction feature matrix.

| Season | Model | Steps | 2→10 slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2024 | Baseline | 2/4 | +0.3pp | 0.531 | 13.036 | 285-261 (52.2%) | 186-173 (51.8%) | 114-104 (52.3%) | 75-60 (55.6%) | 42-38 (52.5%) |
| 2024 | Nonlinear | 4/4 | +5.0pp | 0.504 | 12.975 | 267-258 (50.9%) | 166-157 (51.4%) | 106-91 (53.8%) | 66-54 (55.0%) | 38-30 (55.9%) |
| 2025 | Baseline | 3/4 | +8.8pp | 0.539 | 12.395 | 307-261 (54.0%) | 190-167 (53.2%) | 124-99 (55.6%) | 75-53 (58.6%) | 44-26 (62.9%) |
| 2025 | Nonlinear | 4/4 | +9.6pp | 0.494 | 12.392 | 273-249 (52.3%) | 176-160 (52.4%) | 103-84 (55.1%) | 63-45 (58.3%) | 39-24 (61.9%) |
| 2026 | Baseline | 2/4 | -5.5pp | 0.495 | 13.485 | 77-98 (44.0%) | 59-73 (44.7%) | 41-47 (46.6%) | 22-32 (40.7%) | 15-24 (38.5%) |
| 2026 | Nonlinear | 3/4 | +0.9pp | 0.511 | 13.372 | 69-92 (42.9%) | 56-68 (45.2%) | 39-43 (47.6%) | 25-27 (48.1%) | 14-18 (43.8%) |

## Top 2024-selected candidates

| Model | Cap | Steps | slope | 10+ | AUC | MAE |
|---|---:|---:|---:|---:|---:|---:|
| extra,depth=6,leaf=30,max_features=1.0,trees=350 | 2 | 4/4 | +5.0pp | 55.9% | 0.504 | 12.975 |
| extra,depth=7,leaf=20,max_features=0.7,trees=350 | 2 | 3/4 | +2.8pp | 53.6% | 0.510 | 12.972 |
| extra,depth=6,leaf=30,max_features=1.0,trees=350 | 4 | 3/4 | +1.8pp | 53.5% | 0.495 | 13.000 |
| extra,depth=5,leaf=20,max_features=0.7,trees=350 | 2 | 3/4 | +1.9pp | 52.9% | 0.511 | 12.978 |
| hgb,depth=2,iters=120,l2=20.0,leaf=20,lr=0.05 | 6 | 3/4 | -0.8pp | 50.0% | 0.496 | 13.074 |
| hgb,depth=2,iters=120,l2=20.0,leaf=20,lr=0.05 | 8 | 3/4 | -1.6pp | 49.3% | 0.496 | 13.085 |
| hgb,depth=2,iters=120,l2=20.0,leaf=20,lr=0.05 | 4 | 3/4 | -0.3pp | 50.0% | 0.500 | 13.053 |
| hgb,depth=2,iters=140,l2=10.0,leaf=30,lr=0.035 | 6 | 3/4 | -1.0pp | 50.0% | 0.508 | 13.039 |
| hgb,depth=2,iters=140,l2=10.0,leaf=30,lr=0.035 | 8 | 3/4 | -1.6pp | 49.3% | 0.508 | 13.046 |
| hgb,depth=2,iters=140,l2=10.0,leaf=30,lr=0.035 | 4 | 3/4 | -0.7pp | 50.0% | 0.510 | 13.020 |
| gbr,depth=1,iters=160,leaf=30,loss=huber,lr=0.04 | 2 | 3/4 | -3.0pp | 47.7% | 0.505 | 13.035 |
| gbr,depth=1,iters=160,leaf=30,loss=huber,lr=0.04 | 8 | 3/4 | -3.7pp | 47.1% | 0.494 | 13.082 |
