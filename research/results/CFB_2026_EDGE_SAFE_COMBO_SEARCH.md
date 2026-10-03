# CFB 2026 Edge-Safe Combination Search

Base five: **gamecontrol_diff, def_returning_diff, explosive_diff, current_power_margin, havoc_off_diff**

| Model | Features | MAE | Steps | slope | AUC | 2+ | 4+ | 6+ | 8+ | 10+ |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Anchor + havoc | gamecontrol_diff, def_returning_diff, explosive_diff, current_power_margin, havoc_off_diff | 12.016 | 4/4 | +19.0pp | 0.541 | 61-67 (47.7%) | 43-44 (49.4%) | 33-32 (50.8%) | 25-19 (56.8%) | 14-7 (66.7%) |
| Chosen | gamecontrol_diff, def_returning_diff, explosive_diff, current_power_margin, havoc_off_diff, gamecontrol_x_explosive, plays_game_off_diff | 11.973 | 4/4 | +15.9pp | 0.545 | 62-68 (47.7%) | 44-41 (51.8%) | 31-28 (52.5%) | 24-19 (55.8%) | 14-8 (63.6%) |

## Qualified edge-safe additions

| Added | MAE | slope | 10+ | AUC |
|---|---:|---:|---:|---:|
| gamecontrol_x_explosive, plays_game_off_diff | 11.973 | +15.9pp | 63.6% | 0.545 |
| adjavgingamewp_diff, plays_game_off_diff | 11.989 | +15.5pp | 63.2% | 0.543 |
| adj_st_epa_diff, plays_game_off_diff | 12.005 | +16.0pp | 65.2% | 0.513 |
