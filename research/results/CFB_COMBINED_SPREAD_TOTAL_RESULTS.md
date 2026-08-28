# CFB combined spread + independent totals evaluation

The spread model owns projected margin. The totals model owns projected total and does not receive the spread model's team-score sum. Projected margin is retained only as game-script context.

- Training: 2021, 2022, 2023
- Validation/model selection: 2024
- Untouched holdout: 2025
- Holdout games: 934
- Clean chosen variant: independent_pace_efficiency_interactions (alpha=0.25)
- Legacy chosen variant: pace_efficiency_interactions (alpha=0.25)

## 2025 total accuracy

- Spread-model team-score sum: MAE 12.834, RMSE 15.915, corr 0.217
- Legacy totals regression (includes spread score sum): MAE 12.732, RMSE 15.832, corr 0.241
- Independent totals regression: MAE 12.732, RMSE 15.857, corr 0.236
- Independent vs legacy MAE change: +0.01% (positive means independent is better)
- Independent vs spread-score-sum MAE improvement: 0.80%

## Fixed spread model margin accuracy

- Margin MAE: 12.623
- Margin RMSE: 16.073
- Margin correlation: 0.692

## Final algebraic team-score accuracy

Formula: Home = (Total + Margin)/2; Away = (Total - Margin)/2.

- Original spread-model team scores: team-score MAE 9.016, RMSE 11.309
- Legacy total + fixed margin: team-score MAE 8.983, RMSE 11.280
- Independent total + fixed margin: team-score MAE 8.978, RMSE 11.289
- Independent combined improvement vs original spread team scores: 0.42%
- Independent combined home-score MAE: 9.696
- Independent combined away-score MAE: 8.259
- Both projected team scores within 3 points: 3.4% of games
- Both projected team scores within 7 points: 23.2% of games

## Error independence

- Correlation between independent total error and fixed spread-margin error: 0.141

The low error correlation supports treating margin and scoring environment as separate prediction problems and combining them algebraically after each model produces its independent output.

## Independent totals standardized coefficients

- Residual intercept: -1.0775
- expected_possessions: +17.9802
- poss_x_success: -15.4436
- poss_x_finishing: -13.6346
- abs_spread_margin: +11.8383
- success_matchup: +11.4724
- poss_x_epa: +11.3470
- margin_x_possessions: -10.6226
- finishing_matchup: +8.8522
- epa_matchup: -7.8114
- poss_x_explosive: +6.4980
- explosive_matchup: -5.7251
- expected_combined_ppd: -4.0371
- havoc_pressure: +2.6456
- ypp_matchup: +1.5334
- red_zone_matchup: +1.2449
- plays_per_possession: -1.0838
- pass_ppa_matchup: -1.0329
- sack_pressure: -0.9795
- rush_ppa_matchup: +0.7812
- line_yards_matchup: +0.6352
- poss_x_ypp: -0.5906
- neutral: -0.3755
- turnover_pressure: -0.3455
- third_down_matchup: +0.2604
- structural_total: +0.0841
- abs_spread_margin_sq: +0.0719
- week: +0.0683
