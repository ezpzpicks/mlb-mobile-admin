# Saved NCAAF forecast audit

`ncaaf_edge_audit.py` analyzes exported, frozen model forecasts and recorded
side-specific prices. It makes no network requests and performs no database writes.

Install `research/requirements-ncaaf-audit.txt`, then run from the repository root:

```sh
python research/ncaaf_edge_audit.py --inputs /path/to/inputs --output /path/to/results
PYTHONPATH=. python research/test_ncaaf_edge_audit.py
PYTHONPATH=. python research/test_cfb_schedule_adjustment_window.py
PYTHONPATH=. python research/test_cfb_research_snapshot.py
```

The input directory contains JSON arrays named `daily_slate.json`,
`bet_tracker.json`, `schedule.json`, `personnel_snapshots.json`, and
`team_season_history.json`. Retain the export manifest and original files.

The script preserves the public model record's existing 20-point spread exclusion,
keeps the current model version separate, and checks saved results against scores.
Fixed edge bands include forecasts marked No Play. Unit risk is one per selection.
Saved total forecast bands use the exact saved selection. Sensitivity replays
choose direction from the changed projection; near-zero mean gaps can disagree
with the saved simulation's selected side, so these are kept distinct.

Nine additive total-model sensitivity candidates use the same 3-point gate.
Selection uses weeks 6–7 only; weeks 8–9 are reported separately. Add-one total
and spread comparisons are descriptive and are outside that selection family.
No candidate or threshold is automatically promoted.

These are retrospective chronological checks. Earlier overall outcomes had
already been reviewed, so the later period is not a new untouched experiment.
The component analysis subtracts known additive effects from saved simulation
means. The five-game schedule network is reconstructed using dates strictly
before each target. It does not rerun historical simulations, their score floors,
the unarchived spread-regime inputs, or individual pace/efficiency coefficients.
Refreshed weekly ratings are excluded because their update times can follow the
games being reviewed. Use the new pregame Research Snapshot JSON for future exact
component and individual-feature experiments.

Existing ESPN season-history lines are labeled historical reference quotes in the
audit. New tracker closing capture accepts explicit provider closing fields only.
All model thresholds and predictions remain controlled by the production builder.
