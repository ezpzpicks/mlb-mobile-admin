# NBA lineup foundation — 2026-10-22

This first version creates an NBA engine and a manual admin builder for spreads
and totals. It is a research model with imported inputs, not a live NBA data
feed or a validated betting system. Automation is intentionally deferred.

## What is implemented

- A pure Python projection engine with no network, database or Streamlit import.
- Separate offensive and defensive changes from the entire expected rotation.
- Explicit player availability, starting five, minutes caps and replacement minutes.
- Conditional IN/OUT scenarios, with no invented participation probabilities.
- Joint scenario coverage checks for multiple unresolved rotation players.
- Dated inputs, staleness warnings, and prevention of post-cutoff information.
- A manual admin interface, fictional example, and input/output JSON snapshots.
- Explicit Save writes only an NBA research snapshot. No public pick dataset,
  collector, schedule, paid data service or posting automation is enabled.

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

Player impacts must already be regularized estimates, with a documented source
and cutoff date. The engine does not estimate causal impact from raw on/off
ratings, equate points/game with player value, or infer replacement quality from
a player's fame. Estimating and calibrating those inputs is the next data phase.

## Availability and unresolved news

- AVAILABLE and OUT are resolved participation statuses. A zero-minute AVAILABLE
  player is allowed as a coach's rotation decision.
- PROBABLE, QUESTIONABLE, DOUBTFUL and UNKNOWN require explicit IN/OUT assumptions
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

## Data work still required

1. Verify access to historical team efficiency, possessions, player minutes and
   roster IDs. Fit opponent/venue-adjusted team baselines and a dated reference
   rotation. Normalize historical minutes for overtime before constructing the
   240-minute reference.
2. Estimate regularized offensive/defensive player impacts, with sensible priors
   for rookies, trades and small samples. Align the reference minutes with the
   actual baseline sample. Validate the improvement over the team-only baseline.
3. Attach current official injury reports and separate starting-lineup evidence.
   The official NBA report says it updates throughout the day. NBA.com's daily
   lineup page labels its lineups projected; that label alone is not confirmation.
   A report not yet submitted must remain incomplete, not an empty injury list.
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
repeat saves of the same snapshot are disabled within the session. Cross-session
deduplication and concurrent append behavior retain the shared storage layer's
existing limitations and should be addressed before an automated writer is added.

Future ingestion should refresh shared team/player inputs once and reuse them
across all matchups, update changed game/player IDs incrementally, and serve
saved projections to the public site. Do not run season-history calculations on
public Vercel requests. There are no automation or refresh-schedule changes here.

## Validation

```bash
python -m unittest discover -s research -p 'test_nba_*.py' -v
```

Behavioral tests check the effect of offense, defense, replacements, minutes
caps and existing absences; timestamps, news flags, scenario coverage and archive
identity; and headless manual-builder flows with mocked storage. These tests
establish implementation behavior, not betting accuracy. The interactive
Streamlit UI and a real NBA/Turso data round trip still need live validation.

## Primary references checked

- [NBA official 2026–27 injury report](https://official.nba.com/nba-injury-report-2026-27-season/)
- [NBA projected daily lineups](https://www.nba.com/players/todays-lineups)
- [NBA explanation of possession-based ratings and on/off](https://jr.nba.com/basictraditional-stats-vs-advanced-stats/)
