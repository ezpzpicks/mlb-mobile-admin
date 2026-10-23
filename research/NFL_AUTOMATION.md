# NFL scheduled model builds

The existing half-hour Render service now supports isolated NBA, NFL and NCAAF
processes. NFL is enabled by `NFL_AUTO_BUILDER_ENABLED=1`. No additional service
or public-site polling loop is needed. All windows use America/New_York and the
stored kickoff, including international games and daylight-saving changes.

- **Morning:** first scheduled run at/after 8 AM ET (normally 8:07). Archive all
  player projections and show an ungraded game preview on Full Slate. Early
  props are not published as official picks or added to betting records.
- **Pregame:** first run with 10–75 minutes remaining, normally 45–75 minutes
  before kickoff. The lower bound permits a retry if an input was unavailable.
  Both teams' published game-day inactive lists are required. Save the official
  game and prop projections, graded trackers, lineups and completion marker in
  one transaction. Each matchup locks once; manual saves are protected.
- **Late check:** one run 15–45 minutes before kickoff, normally near 30 minutes.
  Re-fetch props, current depth/status and weather. Unchanged inputs skip model
  computation. Changed inputs create a separate review forecast; original
  official selections, lines and tracked results are never rewritten.
- **Results:** grade recent confirmed-final games and props on scheduled runs,
  using existing production grading. Missing player statistics remain pending
  until authoritative statistics are available. History outside the affected
  matchups is neither loaded into Python nor rewritten.

RotoWire's public NFL comparison tables supply actual sportsbook-specific
lines and prices. Default priority is DraftKings, FanDuel, Hard Rock, BetMGM,
Caesars, BetRivers, then theScore. Both prices must accompany the same book's
yardage line. Anytime TD uses the quoted single price. Missing prices stay
missing; no -110 prop odds are fabricated. Public source observation time,
response hash, source player/game IDs and exact matched quotes are archived.
The rolling page's week is checked against the schedule. Subscriber projections,
grades, table downloads and authenticated endpoints are not consumed.

Game spread/total lines still come from fresh retained ScoresAndOdds snapshots;
their prices retain the previously approved -110 assumption. ESPN Core supplies
current depth and player injury status, and RotoWire's public inactive lists
confirm game-day eligibility. Being active does not assert health or a normal
workload. Outdoor games require current Covers weather; enclosed games use the
checked roof designation. Missing or ambiguous inputs block the affected game.

`builders/nfl_production.py` loads the same complete production model used by
the admin. Its fitted coefficients, player tiers and grades are unchanged.
Historical ratings, profiles, checked statistics and prior-slot residuals are
cached in one compressed database row per model/season/week for up to 36 hours.
Dynamic odds, availability and weather are always rechecked. Database writes
touch only affected games; indexed manifests use per-game count differences.

Commands:

```
python scripts/run_nfl_builder.py                         # read-only plan
python scripts/run_nfl_builder.py --evaluate              # compute, no writes
python scripts/run_nfl_builder.py --evaluate --next-game  # off-day deployment trial
python scripts/run_nfl_builder.py --commit                # requires enabled flag
```

`NFL_AUTO_DIAGNOSTIC_UNTIL` temporarily runs the next-game read-only trial from
the existing cron, including outside build hours. It expires automatically and
must be cleared after deployment verification. Diagnostics never save forecasts
or modify current picks. Separate process timeouts keep one sport's failure from
preventing the remaining builders from running.
