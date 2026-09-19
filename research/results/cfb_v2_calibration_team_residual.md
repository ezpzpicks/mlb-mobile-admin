# CFB v2 calibration + team-specific residual research

Selection discipline: 2024 was used for out-of-sample calibration/confirmation; 2025 was benchmark-only. Sportsbook lines were evaluation targets and never score predictors.

## Margin residual calibration

- 2024 out-of-sample games: 1,609
- 2024 margin MAE: 13.8486
- 2024 margin RMSE: 17.7945
- 2024 model-minus-actual bias: -1.2020 points
- Margin residual SD: **17.7594 points**
- Robust MAD-equivalent sigma: **16.8719 points**
- Distribution probability formula tested: Normal CDF of absolute model-vs-market point edge divided by 17.7594.

## Historical ATS calibration

No probability + point-edge gate met the predefined 2024 stability requirements. The conservative gates therefore remain fallbacks rather than empirically validated ATS thresholds:

- B fallback: probability >= 0.55 and point edge >= 2.5
- A fallback: probability >= 0.58 and point edge >= 4.0

Fallback-gate benchmark results:

- 2024 B-like sample: n=975, ATS win rate 50.56%
- 2024 A-like sample: n=726, ATS win rate 50.96%
- 2025 B-like benchmark: n=1,036, ATS win rate 49.81%
- 2025 A-like benchmark: n=762, ATS win rate 49.08%

Probability bins were not monotonically calibrated to ATS results. The 0.65+ model-distribution probability bucket was 49.09% ATS in 2024 and 50.12% in 2025. These probabilities should be interpreted as model-distribution uncertainty, not historically proven cover frequencies.

## Team-specific residual layer

Candidate factors were returning production, QB continuity, returning receiving, returning rushing, talent proxy, recruiting proxy, and portal proxy. Discovery had useful roster coverage in 2021-22, but the historical roster feed did not provide usable 2024 confirmation coverage.

Backward selection left talent as the strongest discovery factor, but it could not pass 2024 sign/availability confirmation. Final selected residual features: **none**.

- 2024 base margin MAE: 13.8486
- 2024 with confirmed residual overlay: 13.8486
- Improvement: 0.00%
- 2025 base benchmark MAE: 13.6020
- 2025 with residual overlay benchmark: 13.6020
- Production residual coefficients: none

Conclusion: use the calibrated 17.7594-point margin residual distribution, keep conservative grading gates plus market-price/EV vetoes, and do not add a new historical roster/talent/portal residual coefficient until a complete multi-season roster source can be validated.
