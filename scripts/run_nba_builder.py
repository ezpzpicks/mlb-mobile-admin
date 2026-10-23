#!/usr/bin/env python3
"""Build NBA research forecasts and settle tracked games; default is read-only.

--evaluate fetches current inputs and computes without saving. --commit also
requires NBA_AUTO_BUILDER_ENABLED=1. The scheduled window is 9 AM–midnight ET.
No ScoresAndOdds scraper, public slate, signals or pick writer is invoked.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from builders.nba_automation import EXPECTED_VERSIONS, ROTOWIRE_VERSIONS, VERSIONS, automated_package, phase_for_game
from builders.nba_injuries import fetch_espn_injuries
from builders.nba_live import checked_box_minutes, fetch_game, fetch_matchup_inputs, fetch_slate, prepare_live_package
from builders.nba_model import InputError, timestamp
from builders.nba_market import apply_market, market_for_game, read_market_rows
from builders.nba_ratings import validate_bundle
from builders.nba_tracking import lock_prediction, result_observation
from builders.nba_rotowire import fetch_rotowire_lineups, prepare_rotowire_package
from shared import nba_tracking_storage as store

ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")


def clock():
    return datetime.now(timezone.utc)


def emit(event, **details):
    print(json.dumps({"sport": "NBA", "event": event, **details}, default=str), flush=True)


def seeds():
    path = ROOT / "research/nba_live_bootstrap.json"
    return json.loads(path.read_text())["predictions"] if path.is_file() else []


def run(args):
    now = clock()
    if getattr(args, "check_rotowire", False) or os.getenv("NBA_ROTOWIRE_DIAGNOSTIC") == "1":
        report = fetch_rotowire_lineups(on_event=emit)
        if not report["games"]:
            raise InputError("RotoWire HTML was fetched but no valid lineups were parsed.")
        emit("rotowire_diagnostic_ok", date=report["date"], games=len(report["games"]),
             teams=[g[s]["team"] for g in report["games"] for s in ("away", "home")], writes=0)
        return 0
    availability_mode = os.getenv("NBA_AVAILABILITY_MODE", "rotowire_html").strip().lower()
    if availability_mode not in {"injury_feed", "expected_rotation", "rotowire_html"}:
        raise InputError("NBA_AVAILABILITY_MODE must be rotowire_html, injury_feed or expected_rotation.")
    versions = {"rotowire_html": ROTOWIRE_VERSIONS, "expected_rotation": EXPECTED_VERSIONS, "injury_feed": VERSIONS}[availability_mode]
    local = now.astimezone(ET)
    if not args.ignore_window and not 9 <= local.hour <= 23:
        emit("outside_build_window", local_time=local.isoformat())
        return 0
    if args.commit and os.getenv("NBA_AUTO_BUILDER_ENABLED", "").lower() not in {"1", "true", "yes"}:
        raise InputError("NBA_AUTO_BUILDER_ENABLED=1 is required for scheduled saves.")
    from shared import turso_storage as turso
    if not turso.is_turso_ready():
        raise InputError("The existing Turso connection must be configured before building NBA games.")
    started = time.monotonic()
    usage = {"rows_read": 0, "rows_written": 0, "statements": 0}
    original_pipeline = turso._pipeline
    def counted(requests, **kwargs):
        # Enforce dry-run behavior at the actual SQL boundary as well as callers.
        sql = [r.get("stmt", {}).get("sql", "").strip() for r in requests if r.get("type") == "execute"]
        if not args.commit and any(not q.upper().startswith(("SELECT ", "PRAGMA ", "EXPLAIN ")) for q in sql):
            raise InputError("NBA dry runs cannot write to the database.")
        result = original_pipeline(requests, **kwargs)
        usage["statements"] += len(sql)
        for item in result:
            counters = item.get("response", {}).get("result", {})
            for key in ("rows_read", "rows_written"):
                usage[key] += int(counters.get(key, 0) or 0)
        return result
    turso._pipeline = counted
    failures = 0
    try:
        if args.commit:
            store.initialize_tracking()
        slate = fetch_slate(str(local.date()))
        for excluded in slate["excluded"]:
            emit("skipped", **excluded)
        keys = store.prediction_keys([g["game_id"] for g in slate["games"]], list(versions.values()))
        market_rows = read_market_rows(str(local.date()))
        pending = []
        for game in sorted(slate["games"], key=lambda g: g["tipoff"]):
            phase = phase_for_game(game, now)
            if phase is None:
                emit("skipped", game_id=game["game_id"], reason="not a scheduled pregame with at least 3 minutes remaining")
            elif (game["game_id"], versions[phase]) in keys:
                emit("already_saved", game_id=game["game_id"], phase=phase)
            else:
                try:
                    market_for_game(market_rows, game, now)
                    pending.append((game, phase))
                except InputError as exc:
                    emit("waiting_for_market", game_id=game["game_id"], reason=str(exc))
        emit("plan", date=str(local.date()), pending=len(pending), availability_mode=availability_mode,
             mode="commit" if args.commit else "evaluate" if args.evaluate else "plan",
             games=[{"game_id": g["game_id"], "game": f"{g['away']['name']} @ {g['home']['name']}",
                     "tipoff": g["tipoff"], "phase": phase} for g, phase in pending])
        if not (args.commit or args.evaluate):
            return 0
        if pending:
            bundle = json.loads((ROOT / "research/nba_results_2025_26/nba_ratings.json").read_text())
            validate_bundle(bundle)
            injury, rotowire = None, None
            if availability_mode == "expected_rotation":
                emit("injury_feed_bypassed", availability_mode=availability_mode,
                     note="Temporary expected rotation; availability unchecked and starters unconfirmed.")
            else:
                try:
                    if availability_mode == "rotowire_html":
                        rotowire = fetch_rotowire_lineups(day=str(local.date()), on_event=emit)
                    else:
                        injury = fetch_espn_injuries(on_event=emit)
                except InputError as exc:
                    failures += 1
                    emit("injuries_unavailable", error=str(exc),
                         blocked_games=[game["game_id"] for game, _ in pending[:args.limit]])
            team_cache, box_cache = {}, {}
            for game, planned_phase in (pending[:args.limit] if injury is not None or rotowire is not None or availability_mode == "expected_rotation" else []):
                try:
                    inputs = fetch_matchup_inputs(game, team_cache=team_cache, box_cache=box_cache)
                    package = (prepare_rotowire_package(bundle, inputs, rotowire, as_of=clock()) if availability_mode == "rotowire_html" else
                        prepare_live_package(bundle, inputs, injury, as_of=clock(), availability_mode=availability_mode))
                    package = apply_market(package, market_rows, game, clock())
                    current, _ = fetch_game(game["game_id"])
                    phase = phase_for_game(current, clock())
                    if phase is None:
                        emit("skipped", game_id=game["game_id"], reason="game started or minimum lead elapsed during preparation")
                        continue
                    package = automated_package(package, phase)
                    record = lock_prediction(package, package["automation"]["primary_scenario"], current,
                        locked_at=clock(), roster_reviewed=False, minutes_reviewed=False,
                        review_note=package["automation"]["assumptions"], automation_policy=package["automation"]["policy"])
                    inserted = False
                    if args.commit:
                        record, inserted = store.save_prediction(record)
                    emit("built", game_id=game["game_id"], phase=phase, saved=inserted,
                        availability_mode=availability_mode, injury_verified=availability_mode in {"injury_feed", "rotowire_html"},
                        model_version=record["model_version"], locked_at=record["locked_at"],
                        projected_away=record["selected"]["away_score"], projected_home=record["selected"]["home_score"],
                        fair_home_spread=record["selected"]["fair_home_spread"], total=record["selected"]["total"],
                        market_home_spread=record["selected"]["market"]["home_spread"], market_total=record["selected"]["market"]["total"],
                        spread_pick=record["selected"]["spread_pick"], spread_grade=record["selected"]["spread_grade"],
                        total_pick=record["selected"]["total_pick"], total_grade=record["selected"]["total_grade"],
                        scenarios=len(record["projection"]["scenarios"]), conditional=True,
                        public_posting=False)
                except Exception as exc:
                    failures += 1
                    emit("game_failed", game_id=game["game_id"], phase=planned_phase,
                         error=f"{type(exc).__name__}: {exc}")
        # Result reads are limited to recent pending games. No in-progress score
        # snapshots are written every tick and settled games stop being polled.
        for gid in store.pending_game_ids((now - timedelta(days=7)).isoformat(), seed_records=seeds()):
            try:
                game, raw = fetch_game(gid)
                if not game["completed"]:
                    emit("result_pending", game_id=gid, status=game["status"])
                    continue
                boxes, error = None, ""
                try:
                    boxes = checked_box_minutes(raw, game)
                except (InputError, KeyError, TypeError, ValueError, IndexError) as exc:
                    error = str(exc)
                observation = result_observation(game, boxes, box_error=error)
                saved = store.save_observation_if_changed(observation) if args.commit else False
                emit("result_final", game_id=gid, saved=saved, box_check_error=error,
                     away_score=game["away"]["score"], home_score=game["home"]["score"])
            except Exception as exc:
                failures += 1
                emit("result_failed", game_id=gid, error=f"{type(exc).__name__}: {exc}")
        return 1 if failures else 0
    finally:
        turso._pipeline = original_pipeline
        emit("usage", **usage, elapsed_seconds=round(time.monotonic() - started, 2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--evaluate", action="store_true")
    mode.add_argument("--commit", action="store_true")
    parser.add_argument("--ignore-window", action="store_true")
    parser.add_argument("--check-rotowire", action="store_true", help="Fetch and validate public lineup HTML only; no database reads or writes.")
    parser.add_argument("--limit", type=int, default=16)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    try:
        return run(args)
    except Exception as exc:
        emit("failed", error=f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
