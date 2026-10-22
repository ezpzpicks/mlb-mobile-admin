# Scheduled NBA research builds

NBA shares the existing `ezpz-ncaaf-auto-builder` Render cron and its Turso connection. The service still runs `python -u scripts/run_ncaaf_builder.py --commit`. With `NBA_AUTO_BUILDER_ENABLED=1`, that compatibility entry point invokes `run_scheduled_builders.py`: NBA runs first in its own bounded process, followed by NCAAF in another process. Failures and timeouts are logged independently; neither skips the other. Child processes cannot recursively invoke the dispatcher. The NBA timeout is eight minutes and NCAAF timeout is fifteen minutes.

## Schedule and fixed tracking policy

The existing UTC schedule `7,37 * * * *` is unchanged. Each script enforces **9 AM–midnight America/New_York**, including daylight-saving changes. Only the current Eastern date's regular-season NBA slate is built.

- **Early:** the first eligible scheduled run more than 45 minutes before tipoff saves one automatic forecast.
- **Pregame:** the first eligible scheduled run within 45 minutes, with at least three minutes remaining, saves a separate refresh. A normal 30-minute tick gives this a roughly 15–45 minute lead. A late or failed run can miss the window; failures remain visible in logs and can be reviewed manually.
- The two phases use distinct `nba-auto-rotation-v1` tracking versions. Each is insert-once per experiment/game/version. Manual forecasts keep their existing version. Early, pregame and manual records are compared separately; they are not pooled into a doubled game count.
- No retroactive prediction, confirmed starter, human acknowledgement, sportsbook line or calibrated probability is invented. Public posting stays disabled.

The automatic primary excludes OUT and DOUBTFUL players. Other unresolved roster players with minute evidence are explicitly assumed IN. Unlisted players remain UNKNOWN. Minutes use up to ten checked current-season games, falling back to dated historical player minute seeds. Vacated minutes are allocated proportionally among evidenced roles with individual caps and a 240-minute team total. Players without minute evidence receive zero suggested minutes. Identity conflicts, insufficient rotation capacity, stale sources and changed start times block saving and produce `game_failed` logs; they do not silently produce a healthy or complete roster.

Up to six named PROBABLE/QUESTIONABLE/DAY_TO_DAY/DOUBTFUL players receive individual opposite-participation sensitivity scenarios, ranked by their minute evidence. Capacity failures and truncation are archived. These alternatives do not select the primary forecast or imply complete joint-scenario coverage. UNKNOWN players, unresolved injuries and unconfirmed starters keep every forecast conditional. Estimated return dates never establish availability.

## Results and cost

Each run checks recent unsettled games from the last seven days, including the two source-sealed opening manual forecasts. Final scores and checked minutes are appended separately. A failed minute check retains the verified final score with an explicit error. Identical result facts are not rewritten merely because the retrieval timestamp changed; final games stop being polled. Overnight results are collected by the next run in the operating window. Postponements older than seven days need manual result refresh.

Turso reads use indexed game/version keys and a recent pending-game time filter, rather than loading the whole season's archives. A repeat build tick fetches no rosters, injuries or historical boxes for phases already locked. One league injury response and in-process team/box caches are shared across pending matchups. There are at most two automatic forecast rows per game/version of this policy plus a final result observation; database indexes also incur their normal write accounting. No season fitting occurs in the cron or on Vercel requests. No second cron service or separate monthly minimum is added; extra active runtime remains billable on the existing plan.

## Operation and verification

`NBA_AUTO_BUILDER_ENABLED=1` enables committed NBA builds and the shared dispatcher. Set it to `0` on the cron to restore NCAAF-only behavior without changing its schedule or source. Existing Turso credentials stay in Render; none are committed.

Commands:

```bash
python scripts/run_nba_builder.py                 # read-only plan
python scripts/run_nba_builder.py --evaluate      # source fetch + calculation, no writes
python scripts/run_nba_builder.py --commit        # enable flag also required
python scripts/run_scheduled_builders.py          # NBA then NCAAF, committed runs
python -m unittest discover -s research -p 'test_nba_*.py' -q
python -m pytest research/test_ncaaf_auto_builder.py -q
```

The standalone NBA commands require the existing Turso connection even for plans so already-built games are skipped accurately. Dry runs prohibit SQL writes at the actual Turso request boundary and do not initialize tables. Usage logs include actual Turso rows read/written and elapsed time; model/build/result failures return a failing process exit code.

Implementation checks cover separate human/automatic review, early/pregame version identity, rotation assumptions, source and pregame guards, duplicate prevention, isolated subprocess failures/timeouts, read-only operation, DST windows, indexed reads, result settlement and corrections. These checks establish implementation behavior, not predictive accuracy. The existing 335-game historical comparison did not establish a lineup gain; prospective results remain research-only.
