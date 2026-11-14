# NCAAM spread matchup grades — November 12, 2026

November 13 update: the core profiles below pass through the explicit
[selection preferences](NCAAM_SELECTION_PREFERENCES.md). Exclusions can remove
an A/B grade; the prior policy is retained in `config/ncaam_spread_grades_v1.json`.

The approved policy is `config/ncaam_spread_grades.json`. Its thresholds are versioned and hashed with each capture. It grades the model's projected **cover** side at the paired market spread. A projected losing underdog can qualify. It never switches to the opponent to obtain a grade.

Eligible edge is **1.00 inclusive to 4.00 exclusive**. A qualifies through either:

- **Defense-supported:** edge at least 2, picked adjusted defense at least one population standard deviation below the dated national mean, and matchup average tempo does not strongly oppose the role (favorite tempo z > −1; underdog tempo z < +1).
- **Three-factor confluence:** edge at least 3, picked offense higher than opponent, picked defensive rating lower than opponent, and average tempo above national mean for favorites or below it for underdogs.

B requires an eligible edge, at least one relative offense or defense advantage, and no A profile. Everything else is Research. Lower adjusted defense is better. Pick'em receives neutral tempo support.

National benchmarks must be complete (at least 300 Division I teams), same season, observed before the projection lock, and no older than 30 hours. Paired spread quotes must be observed before lock and no older than 90 minutes. The existing point projection and total policy are unchanged.

The builder atomically saves the original forecast, total grade and spread grade. Spread annotations use `ncaam_matchup_spread_grades`, keyed by source capture and policy digest. `scripts/refresh_ncaam_spread_grades.py` can annotate only the latest unstarted captures for a date, after checking the reviewed policy hash. Original forecasts and historical selections are immutable.

Live records begin with grades published before tipoff. Favorite/underdog A/B subtotals partition spread plays; aggregate counts include each game-market once. Replay grades cannot create live model signals. Changed signal quotes are evaluated again using the saved projection, factors and policy; crossing either edge boundary removes eligibility. An unpublished Research selection cannot become a model signal merely through repricing.

The November 8–11 analysis is exploratory: A 9–1, B 26–13, combined 35–14 from 167 completed game projections. These results informed the thresholds and are not added to live records. The original totals replay stays separate.
