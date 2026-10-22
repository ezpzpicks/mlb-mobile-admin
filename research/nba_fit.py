"""Offline, reproducible NBA rating fit and chronological research evaluation.

Run with --help. Reads explicitly supplied local exports; no downloads,
database operations, scheduling, publishing or current injury inference.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform

import pandas as pd

from builders.nba_history import attach_box_history, prepare_event_history
from builders.nba_model import InputError, MODEL_VERSION, timestamp
from builders.nba_ratings import evaluate_games, fit_rating_bundle

PLAYER_PENALTIES = (150.0, 500.0, 1500.0, 5000.0, 15000.0)
MINUTE_WINDOWS = (5, 10)


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def evaluation_metadata(holdout):
    return {"kind": "Retrospective, date-blocked score forecast; no archived injury reports",
        "holdout_games": holdout["games"], "holdout_start": holdout["start"],
        "holdout_end_exclusive": holdout["end_exclusive"],
        "baseline_margin_rmse": holdout["metrics"]["baseline"]["margin_rmse"],
        "lineup_margin_rmse": holdout["metrics"]["lineup"]["margin_rmse"],
        "lineup_beats_team_margin_rmse": holdout["lineup_beats_team_margin_rmse"],
        "live_injury_feed_validated": False, "eligible_for_posting": False}


def load_exports(data_dir, season_end, expected_manifest=None):
    names = {"events": f"nba_play_by_play_{season_end}.parquet",
             "lineups": f"nba_lineups_{season_end}.parquet",
             "possessions": f"nba_possessions_{season_end}.parquet",
             "schedule": f"nba_schedule_{season_end}.csv.gz",
             "box": f"player_boxscores_{season_end}.parquet"}
    tags = {"events": "nba_stats_pbp", "lineups": "nba_stats_game_lineups",
            "possessions": "nba_stats_possessions", "schedule": "nba_stats_schedules",
            "box": "nba_stats_player_boxscores"}
    frames, manifest = {}, []
    expected = {item["file"]: item["sha256"] for item in expected_manifest["sources"]} if expected_manifest else None
    for key, name in names.items():
        path = data_dir / name
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if expected is not None and expected.get(name) != digest:
            raise InputError(f"Source fingerprint changed for {name}; this is not the frozen research input.")
        manifest.append({"file": name, "bytes": len(raw), "sha256": digest,
            "url": f"https://github.com/sportsdataverse/sportsdataverse-data/releases/download/{tags[key]}/{name}"})
        frames[key] = pd.read_csv(path, dtype={"game_id": str}) if key == "schedule" else pd.read_parquet(path)
    source = f"SportsDataverse NBA Stats {season_end-1}–{str(season_end)[-2:]} exports; local files fingerprinted in fit manifest"
    history = prepare_event_history(frames["events"], frames["lineups"], frames["possessions"],
        frames["schedule"], frames["box"], source=source)
    return attach_box_history(history, frames["schedule"], frames["box"]), manifest


def research_run(history, *, validation_start, holdout_start, holdout_end, ratings_cutoff,
                 player_penalties=PLAYER_PENALTIES, minute_windows=MINUTE_WINDOWS, team_ridge=100.0):
    """Choose on validation only; refit before evaluating the untouched test dates."""
    start, split, end = [datetime.fromisoformat(day).date() for day in (validation_start, holdout_start, holdout_end)]
    if not start < split < end:
        raise InputError("Require validation start < holdout start < exclusive holdout end.")
    final_cutoff = timestamp(ratings_cutoff, "final ratings cutoff")
    if final_cutoff.date() < end:
        raise InputError("Final ratings cutoff must follow the evaluated period.")
    games = history.team_games if history.team_games is not None else history.games
    validation_ids = games.loc[(games.game_date >= start) & (games.game_date < split)].game_id.tolist()
    holdout_ids = games.loc[(games.game_date >= split) & (games.game_date < end)].game_id.tolist()
    if not validation_ids or not holdout_ids:
        raise InputError("Validation and holdout periods must both contain checked games.")
    # Use the actual timezone rules rather than a fixed winter offset for a
    # user-specified date (DST may differ in other runs).
    from zoneinfo import ZoneInfo
    validation_cutoff = datetime.combine(start, datetime.min.time(), tzinfo=ZoneInfo("America/New_York"))
    holdout_cutoff = datetime.combine(split, datetime.min.time(), tzinfo=ZoneInfo("America/New_York"))
    candidates, prediction_ids = [], None
    for penalty in player_penalties:
        bundle = fit_rating_bundle(history, validation_cutoff, player_ridge=penalty, team_ridge=team_ridge)
        for window in minute_windows:
            result = evaluate_games(history, bundle, validation_ids, window=window)
            ids = {r["game_id"] for r in result["predictions"]}
            if prediction_ids is not None and ids != prediction_ids:
                raise InputError("Candidates have different validation coverage; compare on an explicitly shared cohort.")
            prediction_ids = ids
            item = {"player_ridge": penalty, "minutes_window": window, "games": result["games"],
                "skipped": result["skipped"], "metrics": result["metrics"]}
            candidates.append(item)
            print(f"Validation ridge={penalty:g} window={window}: margin RMSE={result['metrics']['lineup']['margin_rmse']:.4f}", flush=True)
    selected = min(candidates, key=lambda c: (c["metrics"]["lineup"]["margin_rmse"],
        c["metrics"]["lineup"]["total_rmse"], -c["player_ridge"], c["minutes_window"]))
    frozen = fit_rating_bundle(history, holdout_cutoff, player_ridge=selected["player_ridge"],
        team_ridge=team_ridge, minutes_window=selected["minutes_window"])
    holdout = evaluate_games(history, frozen, holdout_ids, window=selected["minutes_window"])
    final = fit_rating_bundle(history, final_cutoff, player_ridge=selected["player_ridge"],
        team_ridge=team_ridge, minutes_window=selected["minutes_window"])
    validation = {"start": str(start), "end_exclusive": str(split), "requested_games": len(validation_ids),
        "candidate_grid": candidates, "selected": selected,
        "selection_rule": "Lowest validation margin RMSE; ties use total RMSE, stronger shrinkage, then shorter window",
        "team_ridge": team_ridge}
    holdout.update(start=str(split), end_exclusive=str(end), requested_games=len(holdout_ids),
        fitted_as_of=frozen["as_of"], player_ridge=selected["player_ridge"], team_ridge=team_ridge,
        player_training_games=frozen["player_training_games"], team_training_games=frozen["training_games"],
        lineup_beats_team_margin_rmse=holdout["metrics"]["lineup"]["margin_rmse"] < holdout["metrics"]["baseline"]["margin_rmse"],
        live_injury_feed_validated=False, eligible_for_posting=False)
    final["research_evaluation"] = evaluation_metadata(holdout)
    return validation, holdout, frozen, final


def report_text(season_end, audit, validation, holdout, final):
    selected = validation["selected"]
    rows = []
    for label, display in (("baseline", "Team only"), ("lineup", "Team + past-minute lineup forecast")):
        m = holdout["metrics"][label]
        rows.append(f"| {display} | {m['margin_mae']:.3f} | {m['margin_rmse']:.3f} | {m['total_mae']:.3f} | {m['total_rmse']:.3f} |")
    outcome = ("improved" if holdout["lineup_beats_team_margin_rmse"] else "did not improve")
    return f"""# NBA chronological research check — {season_end-1}–{str(season_end)[-2:]}

The lineup version **{outcome} held-out margin RMSE** against the team-only control.
These are retrospective score predictions with dated inputs and a stated availability
lag. There is no historical injury-report archive, current-lineup validation,
market-line archive, ATS record, betting profit, probability calibration or A/B grade.
Every output remains research-only and ineligible for posting.

## Coverage and independent checks

- Team baselines and minutes forecasts: {audit['team_accepted_games']} of {audit['team_source_games']} scheduled regular-season games passed box-score and team-minute checks.
- Player fit: {audit['accepted_games']} of {audit['source_games']} games passed event-lineup checks. {audit['excluded_games']} were excluded; the audit JSON lists each reason and game ID.
- Accepted player games reproduce every player's box plus/minus exactly. Clock-derived minutes differ from boxes by at most {audit['maximum_accepted_player_minute_error_seconds']:.3f} seconds (limit {audit['minute_tolerance_seconds']:g} seconds).
- Free throws use the most recent same-clock foul lineup. This convention is checked against independent player box plus/minus before a game enters the fit.
- Possessions are an explicit box estimate: average both sides' FGA + 0.44×FTA − player OREB + TOV. Counted-possession exposure is shared across distinct event lineups and rescaled to that game estimate.
- The player sample excludes many source games and may be unrepresentative. Team baselines and held-out outcomes do not depend on those lineup exclusions.

## Date split and selection

- Candidate ratings use only games available before midnight ET on {validation['start']}.
- Validation: {validation['start']} through the day before {validation['end_exclusive']}; {selected['games']} evaluated games.
- Five player penalties (150, 500, 1500, 5000, 15000) × two past-minute windows (5, 10 games); fixed team penalty {validation['team_ridge']:g}.
- Selection uses validation margin RMSE, then total RMSE, stronger shrinkage, and shorter window. Chosen player penalty {selected['player_ridge']:g}; window {selected['minutes_window']}.
- The selected model is refit before midnight ET on {holdout['start']} using {holdout['team_training_games']} team games and {holdout['player_training_games']} event-verified player games.
- Holdout: {holdout['start']} through the day before {holdout['end_exclusive']}; {holdout['games']} of {holdout['requested_games']} evaluated games. Hyperparameters are not chosen from this period.

| Model | Margin MAE | Margin RMSE | Total MAE | Total RMSE |
| --- | ---: | ---: | ---: | ---: |
{chr(10).join(rows)}

## What the evaluation can establish

The control uses opponent/venue-adjusted team ratings. The lineup model adds
regularized offensive and defensive player effects relative to the exact box
sample's reference rotation. Forecast minutes are the prior 5/10-game mean,
restricted to the latest known box roster, normalized to 240, with a 48-minute cap.
No target-game minutes, target-game roster or later results enter its prediction.
An unseen player has an explicit prior-only estimate and zero fitted sample.

Dates in the source are game dates, not publication times or tipoff timestamps.
The explicit availability rule is **next-day noon America/New_York**. Each
prediction uses midnight ET at the start of its game date; the engine receives
a placeholder noon tipoff only to enforce pregame input order. It is not an
archived game-start time. Later stat corrections in today's archive cannot be
dated retrospectively. Final scores include overtime; minutes/pace are normalized.
Report statuses are UNKNOWN and lineups remain conditional. This does not test
the benefit of verified injury reports or confirmed starting lineups.

## Saved ratings

The final manual-research bundle uses {final['training_games']} team games and
{final['player_training_games']} player games through **{final['data_through']}**,
with {len(final['teams'])} teams. Its final refit includes holdout outcomes for
future research; the reported holdout metrics come from the separate frozen
pre-holdout bundle. These are prior-season inputs, not current roster, injury or
lineup assertions. Verify and edit the full rotation before using a matchup.

No collector, refresh schedule, paid feed, deployment or public pick writer is added.
The manifest records source URLs, SHA-256 file hashes, cutoffs and parameters.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--season-end", type=int, default=2026)
    parser.add_argument("--validation-start", default="2026-01-01")
    parser.add_argument("--holdout-start", default="2026-03-01")
    parser.add_argument("--holdout-end", default="2026-04-13", help="Exclusive game date")
    parser.add_argument("--ratings-cutoff", default="2026-04-14T00:00:00-04:00")
    parser.add_argument("--expected-manifest", type=Path, help="Require source hashes to match an earlier frozen run")
    args = parser.parse_args()
    expected = json.loads(args.expected_manifest.read_text()) if args.expected_manifest else None
    history, sources = load_exports(args.data_dir, args.season_end, expected_manifest=expected)
    print(f"Checked team games: {len(history.team_games)}; player games: {len(history.games)}", flush=True)
    validation, holdout, frozen, final = research_run(history, validation_start=args.validation_start,
        holdout_start=args.holdout_start, holdout_end=args.holdout_end, ratings_cutoff=args.ratings_cutoff)
    destination = args.output_dir
    destination.mkdir(parents=True, exist_ok=True)
    _write(destination / "nba_history_audit.json", history.audit)
    _write(destination / "nba_validation.json", validation)
    _write(destination / "nba_holdout_predictions.json", holdout)
    _write(destination / "nba_holdout_ratings.json", frozen)
    _write(destination / "nba_ratings.json", final)
    manifest = {"model_version": MODEL_VERSION, "season_end": args.season_end,
        "produced_at": datetime.now(timezone.utc).isoformat(), "sources": sources,
        "validation_start": args.validation_start, "holdout_start": args.holdout_start,
        "holdout_end_exclusive": args.holdout_end, "ratings_cutoff": args.ratings_cutoff,
        "selected_player_ridge": validation["selected"]["player_ridge"],
        "selected_minutes_window": validation["selected"]["minutes_window"], "team_ridge": 100.0,
        "runtime": {"python": platform.python_version(), **{p: version(p) for p in ("numpy", "pandas", "scipy", "pyarrow")}},
        "research_only": True, "eligible_for_posting": False}
    _write(destination / "nba_fit_manifest.json", manifest)
    (destination / "NBA_VALIDATION.md").write_text(report_text(args.season_end, history.audit,
        validation, holdout, final), encoding="utf-8")
    print(json.dumps({"holdout_games": holdout["games"], "metrics": holdout["metrics"],
        "selected": {k: validation['selected'][k] for k in ('player_ridge', 'minutes_window')}}, indent=2))


if __name__ == "__main__":
    main()
