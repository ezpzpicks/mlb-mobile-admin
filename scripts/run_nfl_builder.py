#!/usr/bin/env python3
"""Run NFL morning previews, pregame locks and bounded late-change checks.

Default is a read-only plan. --evaluate computes without database writes;
--commit additionally requires NFL_AUTO_BUILDER_ENABLED=1. --next-game is a
read-only deployment trial using the next upcoming matchup, including off-days.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from builders.nfl_automation import ET, checked_depth, compute, environment, fetch_depth, game_market, input_identity, normalize_game, phase_for_game, weekly_inputs
from builders.nfl_rotowire_props import SourceError, apply_quotes, fetch_inactives, fetch_props
from shared.ncaaf_auto_builder import timestamp
from shared.nfl_auto_storage import NflDatabase, POLICY, records, save_run, upsert_results


def clock():
    return datetime.now(timezone.utc)


def emit(event, **details):
    print(json.dumps({"sport": "NFL", "event": event, **details}, default=str), flush=True)


def prepare_builder(db):
    from shared.storage import set_storage_sport
    set_storage_sport("NFL")
    from builders.nfl_production import load_production_builder
    builder = load_production_builder()
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("streamlit"):
            logging.getLogger(name).setLevel(logging.ERROR)
    # Slot residuals require prior slots only; never load every season from Turso.
    original_read = builder.read_sheet
    def bounded_read(dataset, columns):
        import pandas as pd
        if dataset == builder.LINEUP_TAB:
            where = f"CAST(json_extract(payload_json,'$.\"Season\"') AS INTEGER)={getattr(builder, '_AUTO_SEASON', builder.DEFAULT_SEASON)} AND CAST(json_extract(payload_json,'$.\"Week\"') AS INTEGER)<={getattr(builder, '_AUTO_THROUGH_WEEK', 0)}"
            return pd.DataFrame(db.rows(dataset, where=where), columns=columns)
        if dataset == builder.PROP_CALIBRATION_TAB:
            raise SourceError("The production NFL model must not load residual calibration during automatic evaluation.")
        return original_read(dataset, columns)
    builder.read_sheet = bounded_read
    return builder


def settle_recent(db, builder, now):
    """Reuse production grading for recent, confirmed-final games only."""
    import pandas as pd
    q = db.turso._sql_text
    cutoff = str(now.astimezone(ET).date() - timedelta(days=14))
    pending = {}
    for dataset in (builder.TRACKER_TAB, builder.PROP_TRACKER_TAB):
        pending[dataset] = db.rows(dataset, where=f"date_key>={q(cutoff)} AND (result='' OR lower(result)='pending')")
    if not any(pending.values()):
        return
    season = builder.DEFAULT_SEASON
    schedule = builder._schedule_for_season(season, refresh=True)
    final_ids = set()
    for row in records(schedule):
        if all(str(row.get(k, "")).strip() not in {"", "None", "nan"} for k in ("Away Score", "Home Score")):
            final_ids.add(str(row["Game ID"]))
    wanted = {str(row["Game ID"]) for rows in pending.values() for row in rows} & final_ids
    if not wanted:
        return
    where = "game_key IN (" + ",".join(q(x) for x in sorted(wanted)) + ")"
    tables = {name: db.rows(name, where=where) for name in (builder.PROP_SLATE_TAB, builder.PROP_TRACKER_TAB, builder.TRACKER_TAB, builder.PROP_CALIBRATION_TAB)}
    original_read, original_write = builder.read_sheet, builder.write_sheet
    builder.read_sheet = lambda name, columns: pd.DataFrame(tables.get(name, []), columns=columns)
    builder.write_sheet = lambda name, frame, columns: upsert_results(db, name, records(frame), columns, now)
    try:
        prop_count, _ = builder._auto_update_prop_tracker()
        game_count, _ = builder._auto_update_game_tracker()
        emit("results_settled", games=len(wanted), prop_updates=prop_count, game_updates=game_count)
    finally:
        builder.read_sheet, builder.write_sheet = original_read, original_write


def run(args):
    now = clock()
    expiry = timestamp(os.getenv("NFL_AUTO_DIAGNOSTIC_UNTIL", ""))
    diagnostic = expiry is not None and now < expiry
    enabled = os.getenv("NFL_AUTO_BUILDER_ENABLED", "").lower() in {"1", "true", "yes"}
    if diagnostic:
        args.commit, args.evaluate, args.next_game = False, True, True
    if args.commit and not enabled:
        emit("disabled")
        return 0
    if args.commit and args.next_game:
        raise SourceError("--next-game is read-only and cannot be used with --commit.")
    local = now.astimezone(ET)
    if not args.next_game and not 8 <= local.hour <= 23:
        emit("outside_build_window", local_time=local.isoformat())
        return 0
    from shared import turso_storage as turso
    if not turso.is_turso_ready():
        raise SourceError("The existing Turso connection is required for NFL automation.")
    db = NflDatabase(turso)
    started = time.monotonic()
    try:
        if args.commit:
            db.initialize()
        rows = db.schedule(str(local.date()))
        if args.next_game:
            q = turso._sql_text
            day = str(local.date())
            rows = db.rows("schedule", where=f"COALESCE(NULLIF(date_key,''),json_extract(payload_json,'$.\"Game Date\"'))>={q(day)}")
        games = []
        for row in rows:
            try:
                game = normalize_game(row)
                if timestamp(game["Game Time"]) > now:
                    games.append(game)
            except (SourceError, KeyError, ValueError) as exc:
                emit("schedule_rejected", reason=str(exc))
        if args.next_game:
            games = sorted(games, key=lambda g: timestamp(g["Game Time"]))[:1]
            if not games:
                raise SourceError("No upcoming NFL schedule matchup was available for the deployment trial.")
        previous = db.runs(str(local.date()))
        plan = [(g, "early" if args.next_game else phase_for_game(g, now, previous)) for g in games]
        plan = [(g, phase) for g, phase in plan if phase]
        emit("plan", mode="evaluate" if args.evaluate else "commit" if args.commit else "plan", games=[{"game_id": g["Game ID"], "game": f"{g['Away Team']} @ {g['Home Team']}", "kickoff": g["Game Time"], "phase": phase} for g, phase in plan])
        if not (args.evaluate or args.commit):
            return 0
        builder = prepare_builder(db)
        if args.commit:
            settle_recent(db, builder, now)
        if not plan:
            emit("no_due_games")
            return 0
        market_rows, ready = {}, []
        for game, phase in plan[:args.limit]:
            try:
                if game["Date"] not in market_rows:
                    market_rows[game["Date"]] = db.rows("weekly_market_trends", game["Date"])
                ready.append((game, phase, game_market(market_rows[game["Date"]], game, clock())))
            except SourceError as exc:
                emit("waiting_for_market", game_id=game["Game ID"], reason=str(exc))
        if not ready:
            emit("no_complete_markets")
            return 1 if args.next_game else 0
        props = fetch_props(on_event=emit)
        inactives = fetch_inactives(on_event=emit) if any(phase != "early" for _, phase, _ in ready) else None
        teams = {g[side] for g, _, _ in ready for side in ("Away Team", "Home Team")}
        seasons = {g["Season"] for g, _, _ in ready}
        if len(seasons) != 1:
            raise SourceError("NFL slate spans multiple seasons.")
        depth, depth_at = fetch_depth(builder, teams, seasons.pop(), on_event=emit)
        weekly_cache, failures = {}, 0
        for game, phase, market in ready:
            try:
                baseline = previous.get((game["Game ID"], "pregame"))
                if phase == "late" and baseline:
                    import pandas as pd
                    locked = json.loads(baseline["payload_json"])
                    latest_depth = checked_depth(depth, game, inactives, clock())
                    latest_depth = latest_depth[latest_depth["team_norm"].isin([game["Away Team"], game["Home Team"]])]
                    _, latest_quotes = apply_quotes(pd.DataFrame(locked["props"]), game, props, now=clock())
                    fingerprint = input_identity(game, market, latest_quotes, latest_depth, environment(builder, game), inactives)
                    if fingerprint == baseline["input_hash"]:
                        result = {"input_hash": fingerprint, "unchanged": True, "provenance": {"checked_at": clock().isoformat(), "props_observed_at": props["observed_at"]}}
                        saved = save_run(db, builder, result, game, phase, clock()) if args.commit else False
                        emit("late_inputs_unchanged", game_id=game["Game ID"], saved=saved, model_recomputed=False)
                        continue
                key = game["Season"], game["Week"]
                builder._AUTO_THROUGH_WEEK = game["Week"] - 1
                builder._AUTO_SEASON = game["Season"]
                if key not in weekly_cache:
                    weekly_cache[key] = weekly_inputs(builder, db, *key, now=clock(), commit=args.commit, on_event=emit)
                result = compute(builder, game, market, weekly_cache[key], depth, props, now=clock(), phase=phase, depth_at=depth_at, inactives=inactives)
                changed = not baseline or baseline["input_hash"] != result["input_hash"]
                # Recheck the schedule and kickoff after cold input preparation.
                check = next((normalize_game(r) for r in db.schedule(game["Date"]) if str(r.get("Game ID")) == game["Game ID"]), None)
                if not check or check["Game Time"] != game["Game Time"] or timestamp(check["Game Time"]) <= clock():
                    raise SourceError("NFL kickoff changed or elapsed while preparing the forecast.")
                saved = save_run(db, builder, result, game, phase, clock()) if args.commit else False
                emit("evaluated", game_id=game["Game ID"], phase=phase, saved=saved, changed=changed,
                     props=len(result.get("props", [])), tracked_props=len(result.get("prop_tracker", [])),
                     model_version=builder.MODEL_VERSION, public_lock=phase == "pregame" and saved,
                     projected_away=result.get("slate", {}).get("Projected Away"), projected_home=result.get("slate", {}).get("Projected Home"))
            except Exception as exc:
                failures += 1
                emit("game_failed", game_id=game["Game ID"], phase=phase, error=f"{type(exc).__name__}: {exc}")
        emit("diagnostic_finished" if diagnostic else "finished", failures=failures, forecast_writes_allowed=args.commit)
        return int(failures > 0)
    finally:
        turso._pipeline = db.original_pipeline
        emit("usage", **db.usage(), elapsed_seconds=round(time.monotonic() - started, 2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--evaluate", action="store_true")
    group.add_argument("--commit", action="store_true")
    parser.add_argument("--next-game", action="store_true")
    parser.add_argument("--limit", type=int, default=16)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 16:
        parser.error("--limit must be 1–16")
    try:
        return run(args)
    except Exception as exc:
        emit("failed", error=f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
