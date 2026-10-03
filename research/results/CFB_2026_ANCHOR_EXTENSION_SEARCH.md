# CFB 2026 Four-Variable Anchor Extension Search

Anchor: **gamecontrol_diff, def_returning_diff, explosive_diff, current_power_margin**
Anchor alpha=16, cap=6

| Model | Features | MAE | Steps | slope | AUC | 2+ | 4+ | 6+ | 8+ | 10+ |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Anchor | gamecontrol_diff, def_returning_diff, explosive_diff, current_power_margin | 12.239 | 4/4 | +17.0pp | 0.523 | 54-69 (43.9%) | 41-48 (46.1%) | 29-33 (46.8%) | 22-20 (52.4%) | 14-9 (60.9%) |
| Extended | gamecontrol_diff, def_returning_diff, explosive_diff, current_power_margin, talent_composite_diff, prior_power_margin, stuff_def_diff | 11.680 | 1/4 | -18.3pp | 0.493 | 58-59 (49.6%) | 42-42 (50.0%) | 24-27 (47.1%) | 13-17 (43.3%) | 5-11 (31.2%) |

## Held-out week MAE

| Week | Anchor | Extended | Improvement |
|---:|---:|---:|---:|
| 2 | 13.519 | 12.212 | +1.307 |
| 3 | 10.345 | 10.345 | -0.001 |
| 4 | 12.999 | 12.533 | +0.466 |

## Best single additions to anchor

| Added | MAE | MAE gain | Weeks improved | Steps | slope | 10+ | AUC |
|---|---:|---:|---:|---:|---:|---:|---:|
| talent_composite_diff | 12.002 | +0.236 | 3/3 | 2/4 | -3.6pp | 41.2% | 0.457 |
| avgsosrank_diff | 12.008 | +0.230 | 3/3 | 3/4 | +10.4pp | 57.1% | 0.515 |
| havoc_off_diff | 12.016 | +0.223 | 3/3 | 4/4 | +19.0pp | 66.7% | 0.541 |
| blue_chip_ratio_diff | 12.090 | +0.149 | 3/3 | 1/4 | -12.3pp | 33.3% | 0.483 |
| line_yards_off_diff | 12.098 | +0.140 | 3/3 | 2/4 | +9.1pp | 56.0% | 0.526 |
| opportunity_off_diff | 12.117 | +0.121 | 3/3 | 3/4 | +14.0pp | 60.9% | 0.499 |
| def_rush_epa_edge | 12.150 | +0.088 | 3/3 | 3/4 | +14.1pp | 60.9% | 0.511 |
| power_x_explosive_2026 | 12.162 | +0.077 | 3/3 | 3/4 | +26.2pp | 70.8% | 0.454 |
| plays_game_off_diff | 12.175 | +0.064 | 3/3 | 3/4 | +12.9pp | 57.1% | 0.517 |
| third_down_def_diff | 12.178 | +0.061 | 3/3 | 4/4 | +17.5pp | 63.6% | 0.530 |
| adjavgingamewp_diff | 12.187 | +0.052 | 3/3 | 3/4 | +20.1pp | 65.0% | 0.526 |
| def_success_edge | 12.188 | +0.050 | 3/3 | 3/4 | +10.6pp | 57.1% | 0.538 |
| avg_talent_composite | 12.189 | +0.050 | 3/3 | 4/4 | +12.4pp | 55.6% | 0.546 |
| def_epa_edge | 12.192 | +0.047 | 3/3 | 3/4 | +16.6pp | 63.2% | 0.526 |
| avg_blue_chip_ratio | 12.194 | +0.044 | 3/3 | 3/4 | +7.7pp | 51.9% | 0.546 |
| havoc_def_diff | 12.202 | +0.037 | 3/3 | 3/4 | +10.8pp | 57.1% | 0.517 |
| nonexplosive_epa_def_diff | 12.202 | +0.037 | 3/3 | 3/4 | +17.6pp | 63.6% | 0.488 |
| adj_st_epa_diff | 12.203 | +0.036 | 3/3 | 3/4 | +20.5pp | 66.7% | 0.538 |
| late_down_def_diff | 12.213 | +0.026 | 3/3 | 4/4 | +16.2pp | 61.9% | 0.502 |
| gamecontrol_x_explosive | 12.214 | +0.024 | 3/3 | 3/4 | +20.2pp | 66.7% | 0.522 |

## Greedy extension path

| Step | Added | MAE | Steps | slope | 10+ |
|---:|---|---:|---:|---:|---:|
| 1 | talent_composite_diff | 12.002 | 2/4 | -3.6pp | 41.2% |
| 2 | prior_power_margin | 11.775 | 2/4 | -10.3pp | 38.5% |
| 3 | stuff_def_diff | 11.680 | 1/4 | -18.3pp | 31.2% |

## Final standardized coefficients

```json
{
  "intercept": 0.3321259743112894,
  "gamecontrol_diff": 2.3856937534301403,
  "def_returning_diff": 1.1255880101082654,
  "explosive_diff": 1.2514049946282568,
  "current_power_margin": -2.146795330565659,
  "talent_composite_diff": 5.902496969024427,
  "prior_power_margin": -4.1355106936143295,
  "stuff_def_diff": -1.871675904679817
}
```

## Weekly summary columns found

```json
{
  "third_down_off_diff": "third_down_success_off",
  "third_down_def_diff": "third_down_success_def",
  "red_zone_off_diff": "red_zone_success_off",
  "red_zone_def_diff": "red_zone_success_def",
  "late_down_off_diff": "late_down_success_off",
  "late_down_def_diff": "late_down_success_def",
  "line_yards_off_diff": "line_yards_off",
  "line_yards_def_diff": "line_yards_def",
  "opportunity_off_diff": "opportunity_rate_off",
  "opportunity_def_diff": "opportunity_rate_def",
  "stuff_off_diff": "play_stuffed_off",
  "stuff_def_diff": "play_stuffed_def",
  "havoc_off_diff": "havoc_off",
  "havoc_def_diff": "havoc_def",
  "nonexplosive_epa_off_diff": "nonexplosiveepaperplay_off",
  "nonexplosive_epa_def_diff": "nonexplosiveepaperplay_def",
  "epa_drive_off_diff": "epadrive_off",
  "epa_drive_def_diff": "epadrive_def",
  "epa_game_off_diff": "epagame_off",
  "epa_game_def_diff": "epagame_def",
  "yards_play_off_diff": "yardsplay_off",
  "yards_play_def_diff": "yardsplay_def",
  "plays_game_off_diff": "playsgame_off",
  "plays_game_def_diff": "playsgame_def",
  "drives_game_off_diff": "drivesgame_off",
  "drives_game_def_diff": "drivesgame_def"
}
```
