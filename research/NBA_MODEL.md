# NBA lineup research model — 2026-10-22

The model combines dated team ratings, regularized player impacts and explicit
full-game rotations to project NBA spreads and totals. It includes an offline
2025–26 data fit and a manual admin builder. The first historical lineup forecast
did not improve on the team-only control; this remains a research model.
Scheduled research builds now use the fixed policy in [NBA_AUTOMATION.md](NBA_AUTOMATION.md).
The manual prospective workflow is described in
[`NBA_LIVE_TRACKING.md`](NBA_LIVE_TRACKING.md): current rosters, checked recent
minutes, first pregame locks and result/error tracking.

## What is implemented

- A pure Python projection engine with no network, database or Streamlit import.
- Separate offensive and defensive changes from the entire expected rotation.
- Explicit player availability, starting five, minutes caps and replacement minutes.
- Conditional IN/OUT scenarios, with no invented participation probabilities.
- Joint scenario coverage checks for multiple unresolved rotation players.
- Dated inputs, staleness warnings, and prevention of post-cutoff information.
- A manual admin interface, fictional example, and input/output JSON snapshots.
- Opponent/venue-adjusted team ratings and offensive/defensive player RAPM.
- Separate team-box and ten-player event quality gates, with per-game exclusions.
- Dated ratings bundles, prior-minute rotation seeds and manual matchup creation.
- ESPN injury statuses, estimated return dates, identity checks and dated source snapshots.
- Chronological tuning and a separate 335-game held-out score evaluation.
- Explicit research saves and prospective locks with a separate results ledger.
- Scheduled early and near-tipoff research builds and automatic final-result collection.
  No public pick dataset, paid data service or posting automation is enabled.

## Rotation mathematics

Team offensive and defensive ratings are supplied as points per 100 possessions,
and pace is possessions per 48 minutes. The reference rotation must describe
the same sample used for those ratings. Do not use a nominal healthy starting
five as the reference if the team baseline already reflects missing players.

For each player:

```
minute_change = projected_minutes - reference_minutes
offense_change = minute_change / 48 * offensive_impact
defense_improvement = minute_change / 48 * defensive_impact
```

Sum those changes across the roster. Add the offensive change to the team
offensive rating. Subtract defensive improvement from the defensive rating,
because lower defensive rating means fewer points allowed. A missing player
with zero reference minutes does not create a second injury penalty. A player
traded between the two teams may remain in both historical reference samples,
but cannot receive positive current minutes for both sides.

Both reference and projected rotations total 240 regulation minutes, with every
player between zero and 48. OUT players receive zero minutes. Replacements must
be entered explicitly and stay within their minutes caps; the model does not
silently assign all vacated minutes to the remaining starters.
The explicit live-prepare operation offers a proportional minute suggestion
after removing OUT players; it requires review before comparison and locking.

The initial, uncalibrated matchup combination is:

```
possessions = away_pace * home_pace / league_pace
away_points = adjusted_away_offense * adjusted_home_defense / league_efficiency * possessions / 100
home_points = adjusted_home_offense * adjusted_away_defense / league_efficiency * possessions / 100
```

The user-supplied home-court margin adjustment is divided equally between the
two scores. Report the change in margin and total from the unchanged reference
rotation. These choices are transparent research assumptions and must be
compared with alternative combinations on held-out historical games.

Player impacts are regularized estimates, with a documented source and cutoff.
The offline fitter estimates separate offense and defense effects while adjusting
for the other nine players. The pure projection engine consumes those estimates;
it does not equate points/game with player value. A player without accepted lineup
evidence has an explicit prior-only value and zero fitted sample, which is flagged.

## Availability and unresolved news

- AVAILABLE and OUT are resolved participation statuses. A zero-minute AVAILABLE
  player is allowed as a coach's rotation decision.
- PROBABLE, QUESTIONABLE, DOUBTFUL, DAY_TO_DAY and UNKNOWN require explicit IN/OUT assumptions
  when they affect any entered rotation. Status labels do not supply calibrated
  participation probabilities.
- Scenarios preserve the same observed injury report. Vary the assumptions and
  replacement minutes, not the reported status.
- Show the fair-spread and total range across entered scenarios. If the market
  edge reverses, say so. Do not average the scenarios into a fabricated 50/50 pick.
- For N unresolved rotation players, complete binary participation coverage needs
  2^N distinct combinations. Partial coverage is flagged. Coverage of those
  combinations does not establish coverage of every possible minutes restriction.
- A confirmed starting lineup names exactly five positive-minute players.
- Incomplete reports, unconfirmed starters, assumed participation, or old news
  keep lineup status conditional. The default 60-minute freshness limit is an
  editable operational policy, not a fitted accuracy threshold.

Even a lineup marked ready for review remains research-only: this version has
no fitted win/cover probabilities, A/B grades or posting eligibility.

## Fictional example

The built-in example uses made-up teams and player values. Its questionable
away scorer has 36 reference minutes, a +6 offensive impact and a +1 defensive
impact per 100 possessions. Three reserve players split the vacated minutes.

| Scenario | Fair home spread | Total |
| --- | ---: | ---: |
| Scorer plays | -2.00 | 230.00 |
| Scorer sits | -7.25 | 226.25 |

The example market is home -4 / total 228, so both spread and total direction
change with the player's participation. This illustrates model behavior; it is
not a real pick, an estimate of any real player's value, or a backtest.

## ESPN injury source

ESPN is the implemented injury input provider. The manual source panel fetches
one league response, displays status, injury, last news update and estimated return,
then applies it to the selected matchup. The verification record and detailed rules
are in [ESPN_INJURY_SOURCE.md](ESPN_INJURY_SOURCE.md). Ordinary widget reruns and
matchup application reuse the fetched snapshot rather than making new requests.

OUT removes that player's minutes in every scenario; replacement minutes remain
unassigned until edited. DAY_TO_DAY is unresolved and preserves explicit IN/OUT
hypotheses. Return estimates never turn a player AVAILABLE or confirm a starter.
Unlisted players remain unverified. The import resets report completeness and
starter confirmation, and a refresh failure preserves the old source timestamp.

ESPN athlete IDs differ from NBA Stats IDs. Matching uses previously verified
identity or a unique normalized full name, with explicit overrides supported.
Unmatched rookies, ambiguous names and conflicting team fields require review.
A known traded player's fitted rating can be reused with zero new-team reference
minutes; a new player without evidence retains the zero-prior rule. An ESPN
snapshot cannot be applied before its observation time or across NBA seasons.

This is a current-data input integration. It does not add archived injuries to the
saved 2025–26 backtest, change its result, or validate stronger lineup adjustments.

## Offline data and fitted ratings

The saved [research report](nba_results_2025_26/NBA_VALIDATION.md) and its JSON
artifacts contain the exact results and parameters. All 1,230 regular-season
games pass the team box-score checks. Only 471 pass the stricter event-lineup
checks: final scores and each player's box plus/minus match exactly, and clock
minutes agree within two seconds. The actual maximum accepted error is 0.5 seconds.
The 759 rejected games do not supply player-fit observations. They can still
supply independently checked team scores and prior box minutes.

Possession units are explicitly estimated as the average of each team's
`FGA + 0.44*FTA - player OREB + TOV`. Counted possessions are shared across their
distinct event lineups, then rescaled to that game estimate. This avoids treating
a possession's opening lineup as unchanged through mid-trip substitutions.
Free-throw point credit uses the most recent same-clock foul lineup, validated
against every accepted player's box plus/minus. These are documented conventions,
not exact identification of a causal player effect.

Team and player coefficients use weighted ridge regressions. Team baselines use
all checked boxes; player coefficients use only event-verified games. The reference
rotation describes the **same box-game sample as the team baseline**, with minutes
normalized for overtime and weighted by game possessions. Both fitted baseline
and player exposure use the same possession scale. Historical minutes forecasts
use only boxes available before the cutoff, restrict players to their latest
known box roster, and normalize to 240 with a 48-minute cap.

Validation selects from five player penalties and two minutes windows. The
selected version uses penalty 15,000 and a 10-game window. January–February
validation chooses the settings; the refitted model is then frozen before the
March–April test. The 335-game test gives margin RMSE **15.262 team-only versus
15.280 with past-minute lineup forecasts**. No target-game minutes or later
results enter a forecast. Historical injury reports are not available for this
check. Final scores include overtime. This test does not establish the value of
verified injury reports.

Source game dates use a stated next-day-noon ET availability rule. The source
does not provide historical publication timestamps, and later stat corrections
cannot be removed retrospectively. The player subset may be unrepresentative.
These limitations preclude treating this as a live-feed or betting validation.

The saved final bundle covers 30 teams through **2026-04-12**. Load it with
**Load checked 2025–26 ratings**, choose the teams, enter the game ID and scheduled
tipoff, then review current rosters, injury statuses and replacement minutes.
Historical seeds are always UNKNOWN, incomplete and unconfirmed; no current news
is inferred from last season's box scores. The builder displays the failed
team-control comparison. A separate frozen pre-test bundle preserves the model
used for the reported test; the final future-research refit includes later games.
The roster-addition form can reuse a traded player's dated estimate. A rookie
without fitted evidence receives a zero prior, zero reference minutes and zero
projected minutes; the user assigns replacement minutes in each scenario.

Reproduce the fit from the five source exports listed in the saved manifest:

```bash
python -m pip install -r research/requirements-nba.txt
python -m research.nba_fit --data-dir /path/to/local/exports \
  --output-dir /path/to/new/results \
  --expected-manifest research/nba_results_2025_26/nba_fit_manifest.json
```

This is an explicit offline research command. It reads local files, checks their
SHA-256 fingerprints, fits ratings and writes local results. The Streamlit builder
only consumes saved JSON and does not require SciPy or Arrow. No season fitting,
download or database operation runs on an ordinary form edit.

## Data work still required

1. Resolve more of the rejected source lineups, quantify selection bias, and test
   multi-season or box-informed player priors. More observed names or raw on/off
   samples do not establish a reliable injury adjustment.
2. Demonstrate improvement over the team-only control using pregame information
   on a new evaluation period. Do not repeatedly tune against the saved holdout.
3. Add historical pregame injury-report snapshots to a new evaluation. Reconcile
   current ESPN news with official report completeness and separate starting-lineup
   evidence. NBA.com's daily lineup page labels lineups projected; that alone is
   not confirmation. An unsubmitted or empty report does not establish availability.
4. Bring in current ScoresAndOdds spreads and totals with default -110 pricing,
   recording the line timestamp. Historical evaluation requires lines available
   at the prediction cutoff, not a later line chosen after injury news.
5. Test rest, back-to-backs, travel, recent form, projected shot distribution and
   lineup interaction effects as additional features. This foundation does not
   yet fit these effects or change pace automatically when a player sits.
6. Use chronological training/validation/test periods. Reconstruct reports,
   minutes forecasts, ratings and player impacts available before each game;
   final box-score minutes are outcomes, not usable pregame forecasts.
7. Evaluate margin/total error, calibration, ATS/total results, pushes and -110
   profit with sample sizes. Separate games with settled lineups from games with
   unresolved high-impact news. Choose grades only after held-out evaluation.

## Cost and integration

NBA appears as a separate admin sport. It uses the existing storage layer's
NBA namespace, which passes through without changing other sports. Data entry,
form edits and scenario calculations trigger no database reads or writes.
Saving appends one bounded research snapshot with the exact inputs and outputs;
repeat saves of the same snapshot are disabled within the session. This older diagnostic snapshot API retains its shared-storage limitations. The automated writer uses the separate unique-key prospective tracker described below.
The separate prospective tracker uses append-only NBA research tables and a
database unique constraint for its first game/version lock. This removes
cross-session/concurrent duplication for primary tracking records without
changing the older diagnostic snapshot API.

Future ingestion should refresh shared team/player inputs once and reuse them
across all matchups, update changed game/player IDs incrementally, and serve
saved projections to the public site. Do not run season-history calculations on
public Vercel requests. Scheduled NBA research builds are documented in [NBA_AUTOMATION.md](NBA_AUTOMATION.md); the public-site collector cadence is unchanged.

## Validation

```bash
python -m unittest discover -s research -p 'test_nba_*.py' -v
```

Behavioral tests check the effect of offense, defense, replacements, minutes
caps and existing absences; timestamps, news flags, scenario coverage and archive
identity; event scores, foul-time credit, minute checks, rookies and trades;
future-outcome perturbation tests for date leakage; and headless manual-builder
flows with mocked storage. These tests
establish implementation behavior, not betting accuracy. The interactive
Streamlit flow has also been checked with a real AppTest and SQLite storage.
A live authenticated NBA/Turso round trip is tracked separately from these
implementation checks.

## Primary references checked

- [NBA official 2026–27 injury report](https://official.nba.com/nba-injury-report-2026-27-season/)
- [ESPN NBA injuries](https://www.espn.com/nba/injuries)
- [ESPN NBA injury feed](https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries?limit=1000)
- [NBA projected daily lineups](https://www.nba.com/players/todays-lineups)
- [NBA explanation of possession-based ratings and on/off](https://jr.nba.com/basictraditional-stats-vs-advanced-stats/)
- [SportsDataverse NBA Stats export schemas](https://py.sportsdataverse.org/docs/0.1.5/nba/reference/loaders/stats)
- [Independent stint reconstruction and validation study](https://github.com/ismayc/lineup-valuation-study/tree/878bbc8bb6fa7541df39637a50aa73cca0627030)
