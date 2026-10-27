# Scheduled NCAAF builder

The scheduled job uses the same `load_production_builder()` as the admin. It
builds only spreads and totals, using retained ScoresAndOdds market rows first
and fresh Covers scoreboard lines for missing unsaved scheduled games. Prices
are **-110 for all four sides**. Moneylines and player props are excluded.

## Schedule and cost

The Render job checks at minutes 7 and 37 of each hour. The script returns
immediately outside **9 AM–midnight America/New_York**, so daylight-saving
changes do not shift the build window. Each game is saved once, when both
spread sides and both total sides are present and fresh. Later ticks retry
missing games and skip completed builds. Each run processes at most eight
games in kickoff order. No automatic line-change rebuilds are enabled.

Render cron services bill for active runtime, with a $1 monthly minimum per
service. That minimum is not a spending cap. This service is independent of
the main admin's compute plan. Turso reads and writes use the existing plan.
The cold-start computation trial used 19,216 Turso row reads, zero row writes,
185 seconds, and 506 MiB peak memory. The job therefore selects 1 CPU / 2 GB
instead of leaving only a few MiB of headroom on the 512 MB compute plan.

## Run and inspect

Use the existing Turso connection as `TURSO_DATABASE_URL` and
`TURSO_AUTH_TOKEN`. Never commit either value.

```bash
python scripts/run_ncaaf_builder.py                       # Read-only plan
python scripts/run_ncaaf_builder.py --evaluate            # Compute, no saves
NCAAF_AUTO_BUILDER_ENABLED=1 python scripts/run_ncaaf_builder.py --commit
```

`--ignore-window` permits a supervised trial outside the normal build window.
`--limit 1` restricts that trial to one game. A successful compute trial must
report zero `rows_written`. Logs include actual Turso `rows_read`,
`rows_written`, elapsed time, peak memory, skipped games, and saved projections.

## Save behavior

- The college-only Covers adapter supports NCAAF and NCAAB/NCAAM. No professional
  sport routes through it. NCAAF uses the existing scheduled job; the current
  manual NCAAB builder offers an explicit load-lines button. This does not enable
  unattended runs of the unfinished NCAAB foundation model.
- Covers is fetched once per run only if unsaved scheduled games lack eligible
  ScoresAndOdds lines. Completed/manual games remain protected. Headers, game
  date, ET kickoff, opposite spreads and a consistent total must validate.
- Covers source URLs and observation times are saved with model results. Opening
  lines are left absent. User consensus percentages are ignored. No fallback rows
  are inserted into split history, and no public-betting signals are fabricated.

- Date-filtered compact `weekly_market_trends` reads; no `odds_snapshot` scan.
- Existing saved games, tracked bets, settled results, and personnel snapshots
  are protected before calculation and again inside the save transaction.
- Initial background slate previews can be completed with verified SAO lines.
- Each saved matchup gets a completion marker and an audit row in
  `ncaaf_auto_builder_runs`. Repeated runs do not rewrite that matchup.
- Required model-data readiness checks match the admin. A missing source can
  delay a build; no neutral replacement ratings are invented.
- Covers histories are buffered during calculation and saved only in the
  successful matchup transaction. Complete weekly team ratings are reused.

Disable scheduled saves by setting `NCAAF_AUTO_BUILDER_ENABLED=0` (runs fail
closed) or suspend the cron service. The existing admin remains available.

