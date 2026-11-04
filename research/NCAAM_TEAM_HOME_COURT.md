# NCAAM team-specific home court — v0.6

NCAAM now estimates a separate home-court value for each school. It follows the
NFL model's principle of recency-weighted, opponent-adjusted home advantage with
small samples pulled toward a league prior. College coefficients are estimated
from college results; NFL point values are not copied.

## Model

The joint ridge fit separates three terms: each season's team strengths, a league
home-court mean, and each school's additional home effect. Team strengths have
separate coefficients by season to handle roster turnover. Neutral games help
identify team strength but contribute no home or venue indicator.

- Three preceding seasons plus eligible current-season results; older seasons
  receive weight `0.78 ** age`.
- A 40-game-equivalent venue prior limits the influence of small home samples.
  The league prior is 3.2 points with 48 game equivalents. Team-strength nuisance
  regularization is 0.25; copying NFL's 12-game penalty would over-shrink college
  strength and misattribute strong teams' home winning margins to venue.
- Each school in a season needs five completed D1 games before that season's
  observations can enter. Early-season scores cannot overwhelm mature history.
- Both teams' game-log rows must agree on identity, scores and H/A or N/N venue.
  Membership comes from that season's D1 ratings group. Exact source aliases
  handle verified school name changes; no fuzzy matching is used.
- Date-only results enter on the following Eastern day. The fit cannot include
  the game being predicted or any later game. Future season phases are not used
  to estimate an earlier historical forecast.
- Missing school history uses the fitted league mean with an explicit fallback.
  If the whole estimator is unavailable, the source baseline's 3.2-point prior
  remains explicit. Neutral games always use zero points.
- Applied values have a broad 0–8-point sanity guard. This is not a betting
  threshold. Sample support describes data volume, not win probability.

The bundled, independently paired corpus has **28,431 D1 games** from seasons
2022–2026. The current 2027 fit uses **17,242** games from 2024–2026 and produces
365 school estimates. The source URL and SHA-256 for every results and membership
file are retained inside the compact snapshot.

| School | Current home-court points | Actual home games |
| --- | ---: | ---: |
| Kansas | 4.93 | 49 |
| Tennessee | 3.79 | 50 |
| Alabama | 3.74 | 46 |
| Duke | 2.77 | 50 |
| LSU | 2.24 | 53 |

The current league mean is 2.94 points. These are learned school-level estimates,
not rankings of overall team quality. Arena relocations are not separately
identified in the source and should not be described as venue-specific evidence.

## Projection and runtime

Version: `ncaam-v0.6-team-home-court-2026-11-03`.

The fixed v0.5 3.88-point intercept is replaced by the school estimate. The
opening-fortnight neutral-strength scaling stays at its original frozen 1.1221
coefficient. Home-court estimation works outside that opening window as well;
only the separately tested strength multiplier expires after November 14.

Totals are preserved. Home court is added half to the home team's points and
subtracted half from the away team's points. Availability deltas remain separate
and unscaled. Forecasts retain school, league and incremental venue values,
sample sizes, source policy and pregame cutoff metadata. New immutable version
IDs preserve all earlier saved forecasts and signal captures.

Current results reuse the bulk log already downloaded for D1 game counts. A
compact indexed model cache refits only when the date or result evidence changes,
not when timestamps alone refresh. No new provider request, cron or public-page
computation is added. Fits take about 0.1–0.2 seconds in the local single-thread
evaluation and use season-block elimination to bound matrix size near 365 teams.

## Evaluation

All settings are fixed before comparisons; later games do not tune the model.

| Sample | Games | Original v0.4 margin MAE | Fixed-court v0.5 | Team-court v0.6 |
| --- | ---: | ---: | ---: | ---: |
| 2024–25 opening fortnight | 606 | 9.73 | 9.66 | 9.65 |
| 2025–26 opening fortnight | 608 | 9.59 | 9.42 | 9.47 |
| Nov. 2–3, 2026 saved pregame replay | 129 | 10.79 | 10.42 | 10.43 |

Team-specific home court has similar overall point accuracy to v0.5 in these
samples; it does not establish an accuracy or betting improvement. At a fixed
three-point edge, the historical holdout is 118–114, and the current-season
replay is 18–25, both negative at assumed -110. Official selections, A/B grades
and calibrated probability claims remain on hold.

The live-period check is retrospective and reuses saved pregame ratings and
availability. It does not claim that v0.6 estimates were historically captured.
Four changed-identity/time games and five unfinished games remain excluded.
Four newly promoted D1 home teams in the historical check use the explicit league
fallback rather than invented school history. The original v0.5 evaluator now
reconstructs its frozen formula independently of the live model, and reproduces
all original historical/live results exactly.

Reproduce with the original November 2 verified replay and the two saved public
responses:

```sh
OPENBLAS_NUM_THREADS=1 python research/evaluate_ncaam_home_court.py \
  --historical /path/to/replay/games.json \
  --live /path/to/ncaam_yesterday.json --live /path/to/ncaam_today.json \
  --output research/results/ncaam_v06_team_home_court_2026-11-03.json
python -m pytest research/test_ncaam_*.py research/test_espn_ncaam_lines.py -q
```

`research/build_ncaam_home_court_history.py` regenerates the source corpus from
explicit `--gamelog SEASON=PATH` and `--ratings SEASON=PATH` arguments. It requires
a complete matching D1 membership export for every season. The runtime snapshot
must be updated with each completed season; never silently treat incomplete
future-season coverage as a complete rolling history.
