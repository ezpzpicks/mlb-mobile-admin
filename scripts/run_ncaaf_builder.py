#!/usr/bin/env python3
"""Run today's unsaved NCAAF games with retained ScoresAndOdds lines.

Default: read-only plan. --evaluate computes without saving. --commit explicitly
enables atomic saves, and additionally requires NCAAF_AUTO_BUILDER_ENABLED=1.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import resource
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ncaaf_auto_builder import (
    ASSUMED_ODDS, AUTO_TAB, Database, ET, eligible_markets, match_schedule,
    protected_matchup, save_once,
)


def emit(event, **details):
    print(json.dumps({"event": event, **details}, default=str), flush=True)


def prepare_builder(db):
    # Scheduled jobs may synchronously prepare required assets. The admin keeps
    # its normal asynchronous behavior because it is a different process.
    os.environ["EZPZ_CFB_ALLOW_BLOCKING_OPEN_DATA"] = "1"
    import streamlit
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("streamlit"):
            logging.getLogger(name).setLevel(logging.ERROR)
    from shared.storage import set_storage_sport
    set_storage_sport("CFB")
    from builders.cfb_production import load_production_builder
    builder = load_production_builder()
    builder.st.session_state["selected_sport"] = "CFB"
    # Covers normally persists its history during evaluation. Buffer those rows
    # until the explicit atomic save, including during --evaluate trials.
    from builders import cfb_covers
    cfb_covers._flush_history = lambda *_args, **_kwargs: None
    return builder


def ratings_for_week(builder, season, week, db, commit):
    import pandas as pd
    existing = builder._get_cached_ratings(season, week)
    deadline = time.monotonic() + 600
    while True:
        ratings = builder._ensure_automatic_ratings(season, week)
        if ratings is not None and not ratings.empty:
            break
        warning = str(builder.st.session_state.get("cfb_auto_ratings_warning", ""))
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Complete model data is not ready: {warning}")
        emit("waiting_for_model_data", season=season, week=week, detail=warning)
        time.sleep(5)
    # All gates above are the production admin's gates. A newly built, verified
    # weekly snapshot is persisted once, rather than rebuilt for each matchup.
    saved_verified = not existing.empty and "Source" in existing and existing["Source"].astype(str).str.contains("full-input-verified", regex=False).all()
    if commit and not saved_verified:
        all_ratings = builder._sheet(builder.RATINGS_TAB, builder.RATING_COLUMNS)
        if not all_ratings.empty:
            keep = ~((all_ratings["Season"].astype(str) == str(season)) &
                     (all_ratings["Projection Week"].astype(str) == str(week)))
            all_ratings = all_ratings.loc[keep]
        combined = pd.concat([all_ratings, ratings], ignore_index=True)
        with db.writes():
            builder._write(builder.RATINGS_TAB, combined, builder.RATING_COLUMNS)
    return ratings


def compute(builder, schedule, market, ratings):
    import pandas as pd
    from builders import cfb_covers
    for kind in ("starters", "injuries"):
        cfb_covers._pending(builder, kind).clear()
    game = dict(schedule)
    game.update({"Game Date": market.date, "Game Time": market.kickoff.isoformat(),
                 "Home Spread": market.home_spread, "Total": market.total,
                 "Opening Home Spread": market.opening_home_spread,
                 "Opening Total": market.opening_total, "Away ML": 0, "Home ML": 0,
                 "Line Provider": "ScoresAndOdds; assumed -110 prices"})
    for column in ("Home Spread Odds", "Away Spread Odds", "Total Over Odds", "Total Under Odds"):
        game[column] = ASSUMED_ODDS
    game = pd.Series(game, dtype=object)
    season, week = int(float(game["Season"])), int(float(game["Week"]))
    personnel = {}
    for side in ("away", "home"):
        team = game[f"{side.title()} Team"]
        rating = builder._rating_row_for_game(ratings, team, game.get(f"{side.title()} Classification"))
        personnel[side] = builder.default_personnel(team, rating, season, week, str(game["Game ID"]), live_candidate=True)
    environment = builder.build_environment(game, season)
    return builder.evaluate_game(
        game, ratings, personnel["away"], personnel["home"], environment,
        market.home_spread, market.total, 0, 0,
        {"spread": True, "total": True, "moneyline": False},
        home_spread_odds=ASSUMED_ODDS, away_spread_odds=ASSUMED_ODDS,
        total_over_odds=ASSUMED_ODDS, total_under_odds=ASSUMED_ODDS,
    )


def run(args):
    now = datetime.now(timezone.utc)
    local = now.astimezone(ET)
    if not args.ignore_window and not (9 <= local.hour <= 23):
        emit("outside_build_window", local_time=local.isoformat())
        return 0
    if args.commit and os.getenv("NCAAF_AUTO_BUILDER_ENABLED", "").lower() not in {"1", "true", "yes"}:
        raise RuntimeError("Set NCAAF_AUTO_BUILDER_ENABLED=1 to enable scheduled saves.")
    from shared import turso_storage as turso
    if not turso.is_turso_ready():
        raise RuntimeError("Configure the existing Turso URL and auth token before running the builder.")
    db = Database(turso)
    started = time.monotonic()
    try:
        day = local.date().isoformat()
        markets, skipped = eligible_markets(db.rows("weekly_market_trends", day, compact=True), now)
        for item in skipped:
            emit("skipped", **item)
        if not markets:
            emit("no_eligible_markets", date=day)
            return 0
        schedule = db.rows("schedule")
        protected = [row for tab in (AUTO_TAB, "builder_completed", "bet_tracker") for row in db.rows(tab, day)]
        pending = []
        for market in markets:
            try:
                game = match_schedule(market, schedule)
                if protected_matchup(market, game, protected):
                    emit("already_saved", game=market.summary()["game"])
                    continue
                # Legacy manual saves may predate the completion marker. Match
                # personnel by indexed game ID even when its Date field is empty.
                q = turso._sql_text
                rows = db.query("SELECT row_index FROM dataset_rows WHERE sport='NCAAF' "
                                f"AND dataset='personnel_snapshots' AND game_key={q(str(game['Game ID']))} LIMIT 1")
                if rows:
                    emit("already_saved", game=market.summary()["game"], reason="existing personnel snapshot")
                    continue
                pending.append((market, game))
            except ValueError as exc:
                emit("skipped", game=market.summary()["game"], reason=str(exc))
        emit("plan", date=day, pending=len(pending), mode="commit" if args.commit else "evaluate" if args.evaluate else "plan",
             games=[market.summary() for market, _ in pending[:args.limit]])
        if not pending or not (args.evaluate or args.commit):
            return 0
        builder = prepare_builder(db)
        context = {}
        failures = 0
        for market, game in pending[:args.limit]:
            try:
                key = (int(float(game["Season"])), int(float(game["Week"])))
                if key not in context:
                    context[key] = ratings_for_week(builder, *key, db, args.commit)
                if market.kickoff <= datetime.now(timezone.utc):
                    emit("skipped", game=market.summary()["game"], reason="game started while preparing model inputs")
                    continue
                result = compute(builder, game, market, context[key])
                saved = save_once(db, builder, result, market) if args.commit else False
                slate = builder.slate_row(result).iloc[0]
                emit("built", game=market.summary()["game"], game_id=str(game["Game ID"]), saved=saved,
                     spread=slate["Spread Pick"], spread_grade=slate["Spread Grade"],
                     total=slate["Total Pick"], total_grade=slate["Total Grade"],
                     projected_away=slate["Projected Away"], projected_home=slate["Projected Home"],
                     model_version=builder.MODEL_VERSION, assumed_odds=ASSUMED_ODDS)
            except Exception as exc:
                failures += 1
                emit("game_failed", game=market.summary()["game"], error=f"{type(exc).__name__}: {exc}")
        return 1 if failures else 0
    finally:
        emit("usage", **db.usage(), elapsed_seconds=round(time.monotonic() - started, 2),
             peak_memory_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--evaluate", action="store_true")
    mode.add_argument("--commit", action="store_true")
    parser.add_argument("--ignore-window", action="store_true", help="Allow a supervised trial outside 9 AM–midnight Eastern.")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    try:
        return run(args)
    except Exception as exc:
        emit("failed", error=f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    # Compatibility entry point for the already deployed Render cron command.
    # The explicit NBA enable flag opts that service into the shared dispatcher;
    # supervised plan/evaluate invocations and child runs retain their behavior.
    if (sys.argv[1:] == ["--commit"] and not os.getenv("EZPZ_SCHEDULED_BUILDER_CHILD") and
            os.getenv("NBA_AUTO_BUILDER_ENABLED", "").lower() in {"1", "true", "yes"}):
        from scripts.run_scheduled_builders import main as scheduled_main
        raise SystemExit(scheduled_main())
    raise SystemExit(main())
