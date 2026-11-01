#!/usr/bin/env python3
"""NCAAM scheduled efficiency forecasts. --evaluate never writes; --commit saves.

Reuses the existing Render job and ScoresAndOdds snapshots. Covers supplies
model-only lines when splits are absent. Research forecasts never imply A/B
eligibility or create an official EZPZ pick.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from builders.ncaam_model import MODEL_VERSION, parse_ratings, project, team_key, timestamp
from shared import ncaam_auto_storage as store

ET = ZoneInfo("America/New_York")


def emit(event, **details):
    print(json.dumps({"sport": "NCAAM", "event": event, **details}, default=str), flush=True)


def get(url):
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; EZPZPicks/1.0)", "Accept": "application/json,text/csv"}), timeout=20) as response:
        data = response.read(5_000_001)
        if len(data) > 5_000_000:
            raise ValueError("NCAAM provider response exceeds the size limit.")
        return data.decode("utf-8-sig")


def parse_slate(payload, day):
    if not isinstance(payload.get("events"), list):
        raise ValueError("ESPN NCAAM schedule is missing events.")
    games = []
    for event in payload["events"]:
        if event.get("season", {}).get("type") not in (2, 3):
            continue
        competitions = event.get("competitions", [])
        if len(competitions) != 1:
            raise ValueError("Ambiguous college basketball competition.")
        comp = competitions[0]
        tipoff = timestamp(comp.get("date") or event.get("date"))
        if str(tipoff.astimezone(ET).date()) != day:
            continue
        people = comp.get("competitors", [])
        if len(people) != 2 or {p.get("homeAway") for p in people} != {"away", "home"}:
            raise ValueError("Missing college basketball teams.")
        sides = {p["homeAway"]: p["team"] for p in people}
        status = comp.get("status", event.get("status", {})).get("type", {})
        valid_time = comp.get("timeValid") is True or event.get("timeValid") is True
        game = {"game_id": str(event["id"]), "tipoff": tipoff.isoformat(), "date": day,
                "season": int(event["season"]["year"]), "neutral_site": comp.get("neutralSite"),
                "status": status.get("name") if valid_time else "UNKNOWN_TIME"}
        for side, team in sides.items():
            game[side] = team["displayName"]
            game[side + "_id"] = str(team["id"])
            game[side + "_aliases"] = list(dict.fromkeys(str(team[k]) for k in ("displayName", "shortDisplayName", "location", "abbreviation") if team.get(k)))
        if not game["game_id"].isdigit() or game["away_id"] == game["home_id"]:
            raise ValueError("Invalid college basketball game identity.")
        games.append(game)
    if len({g["game_id"] for g in games}) != len(games):
        raise ValueError("Duplicate college basketball games.")
    return sorted(games, key=lambda g: g["tipoff"])


def bind_market(game, markets):
    matches = []
    for market in markets:
        away = lambda name: any(team_key(a) == team_key(name) for a in game["away_aliases"])
        home = lambda name: any(team_key(h) == team_key(name) for h in game["home_aliases"])
        forward = away(market.away) and home(market.home)
        reverse = game["neutral_site"] is True and home(market.away) and away(market.home)
        if (forward or reverse) and abs((timestamp(game["tipoff"]) - market.kickoff).total_seconds()) <= 300:
            matches.append((market, reverse))
    if len(matches) != 1:
        return None
    m, reverse = matches[0]
    return {"home_spread": -m.home_spread if reverse else m.home_spread, "total": m.total, "as_of": m.captured.isoformat(),
            "source": m.source, "source_url": m.source_url, "odds": -110}


def run(args):
    now = datetime.now(timezone.utc)
    if args.commit and os.getenv("NCAAM_AUTO_BUILDER_ENABLED", "").lower() not in {"1", "true", "yes"}:
        raise ValueError("NCAAM_AUTO_BUILDER_ENABLED=1 is required for scheduled saves.")
    from shared import turso_storage as db
    if not db.is_turso_ready():
        raise ValueError("The existing Turso connection is required.")
    day = str(now.astimezone(ET).date())
    slate = parse_slate(json.loads(get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={day.replace('-', '')}&groups=50&limit=1000")), day)
    existing = store.rows(db, store.FORECASTS, day)
    keys = {json.loads(row["Details JSON"])["recordId"] for row in existing}
    pending = []
    for game in slate:
        lead = (timestamp(game["tipoff"]) - now).total_seconds() / 60
        if game["status"] != "STATUS_SCHEDULED" or lead < 10:
            continue
        phase = "pregame" if lead <= 90 else "early"
        record_id = f"{game['game_id']}|{MODEL_VERSION}|{phase}"
        if record_id not in keys:
            pending.append((game, phase, record_id))
    emit("plan", date=day, pending=len(pending), existing=len(existing), mode="commit" if args.commit else "evaluate" if args.evaluate else "plan")
    if not pending or not (args.commit or args.evaluate):
        return 0
    from shared.ncaaf_auto_builder import eligible_markets
    markets, rejected = eligible_markets(store.rows(db, "weekly_market_trends", day), now)
    for row in rejected:
        emit("market_rejected", detail=row)
    fallback = []
    if any(bind_market(game, markets) is None for game, _, _ in pending):
        try:
            from shared.college_market_lines import fetch_covers_markets
            fallback, rejected = fetch_covers_markets("NCAAM", now)
            # SAO remains primary. Covers percentages never enter split records.
            for row in rejected:
                emit("covers_rejected", detail=row)
        except Exception as exc:
            emit("covers_unavailable", error=str(exc))
    seasons = {g["season"] for g, _, _ in pending}
    if len(seasons) != 1:
        raise ValueError("Ambiguous NCAAM ratings season.")
    season = seasons.pop()
    cached = store.rows(db, store.RATINGS, day)
    bundle = json.loads(cached[0]["Details JSON"]) if cached else None
    if not bundle or bundle["season"] != season or not timedelta(0) <= now - timestamp(bundle["observed_at"]) < timedelta(hours=6):
        bundle = parse_ratings(get(f"https://barttorvik.com/{season}_team_results.csv"), season, datetime.now(timezone.utc).isoformat())
        if args.commit:
            store.upsert(db, store.RATINGS, {"Date": day, "Game Key": f"ratings|{day}", "Market": "Ratings", "Selection": str(season),
                "Updated At": bundle["observed_at"], "Details JSON": json.dumps(bundle)})
    failures = 0
    for game, phase, record_id in pending[:args.limit]:
        try:
            current = datetime.now(timezone.utc)
            market = bind_market(game, markets) or bind_market(game, fallback)
            if not market:
                emit("waiting_for_market", game_id=game["game_id"], game=f"{game['away']} @ {game['home']}")
                continue
            if timestamp(game["tipoff"]) - current < timedelta(minutes=10):
                continue
            forecast = project(game, bundle, market, current.isoformat())
            forecast.update(recordId=record_id, gameId=game["game_id"], modelVersion=MODEL_VERSION,
                            lockedAt=current.isoformat(), tipoff=game["tipoff"], phase=phase)
            if args.commit:
                row = {"Date": day, "Game Key": f"{day}|ncaam|{game['game_id']}", "Game": f"{game['away']} @ {game['home']}",
                       "Market": "Forecast", "Selection": record_id, "Updated At": current.isoformat(), "Details JSON": json.dumps(forecast)}
                store.upsert(db, store.FORECASTS, row, immutable=True)
            emit("built", game_id=game["game_id"], phase=phase, saved=args.commit, model_version=MODEL_VERSION,
                 projected_away=forecast["away_score"], projected_home=forecast["home_score"],
                 line_source=market["source"], neutral_site=game["neutral_site"], grade_policy=forecast["grade_policy"])
        except Exception as exc:
            failures += 1
            emit("game_failed", game_id=game["game_id"], error=str(exc))
    return int(failures > 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--evaluate", action="store_true")
    mode.add_argument("--commit", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 200:
        parser.error("--limit must be 1–200")
    try:
        return run(args)
    except Exception as exc:
        emit("failed", error=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
