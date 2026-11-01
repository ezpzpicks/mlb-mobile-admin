#!/usr/bin/env python3
"""NCAAM scheduled efficiency forecasts. --evaluate never writes; --commit saves.

Reuses the existing Render job and ScoresAndOdds snapshots. ESPN supplies
model-only backup lines, followed by Covers. Research forecasts never imply A/B
eligibility or create an official EZPZ pick.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import hashlib
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from builders.ncaam_model import MODEL_VERSION, blend_season_ratings, find_rating, parse_ratings, project, team_key, timestamp
from builders.ncaam_lineups import fetch_lineups, fingerprint
from builders.ncaam_player_impact import parse_player_bundle, lineup_adjustments, valid_lineup
from shared import ncaam_auto_storage as store
from shared.espn_ncaam_lines import parse_espn_ncaam_lines, response_observed_at

ET = ZoneInfo("America/New_York")


def emit(event, **details):
    print(json.dumps({"sport": "NCAAM", "event": event, **details}, default=str), flush=True)


def get(url, *, with_observation=False):
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; EZPZPicks/1.0)", "Accept": "application/json,text/csv"}), timeout=20) as response:
        data = response.read(5_000_001)
        if len(data) > 5_000_000:
            raise ValueError("NCAAM provider response exceeds the size limit.")
        text = data.decode("utf-8-sig")
        return (text, response_observed_at(response.headers, datetime.now(timezone.utc))) if with_observation else text


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


def cached_input(db, dataset, key, url, parser, season, now, commit, max_hours=6):
    rows = store.rows(db, dataset, key)
    candidates = []
    for row in rows:
        try:
            candidate = json.loads(row['Details JSON'])
            if candidate['season'] == season and timestamp(candidate['observed_at']) <= now: candidates.append(candidate)
        except (KeyError, ValueError, TypeError): continue
    bundle = max(candidates, key=lambda value: timestamp(value['observed_at']), default=None)
    if not bundle or bundle['season'] != season or not timedelta(0) <= now-timestamp(bundle['observed_at']) < timedelta(hours=max_hours):
        raw, observed = get(url, with_observation=True)
        bundle = parser(raw, season, observed.isoformat())
        if commit:
            # Preserve the existing current-team cache identity across versions.
            game_key = f'ratings|{key}' if dataset == store.RATINGS else f'{dataset}|{key}'
            store.upsert(db, dataset, {'Date': key, 'Game Key': game_key, 'Market': 'Ratings' if dataset == store.RATINGS else 'Inputs', 'Selection': str(season),
                                      'Updated At': observed.isoformat(), 'Details JSON': json.dumps(bundle)})
    return bundle


def load_model_inputs(db, day, season, now, commit):
    current = cached_input(db, store.RATINGS, day, f'https://barttorvik.com/{season}_team_results.csv', parse_ratings, season, now, commit)
    prior = cached_input(db, 'ncaam_prior_rating_inputs', f'season-{season-1}', f'https://barttorvik.com/{season-1}_team_results.csv',
                         parse_ratings, season-1, now, commit, 720)
    teams = blend_season_ratings(current, prior, datetime.now(timezone.utc).isoformat())
    try:
        players = cached_input(db, 'ncaam_player_profiles', day, f'https://barttorvik.com/getadvstats.php?year={season}&csv=1',
                               parse_player_bundle, season, now, commit)
        prior_players = cached_input(db, 'ncaam_player_profiles', f'season-{season-1}', f'https://barttorvik.com/getadvstats.php?year={season-1}&csv=1',
                                     parse_player_bundle, season-1, now, commit, 720)
        players = {**players, 'prior_players': prior_players['players'], 'prior_observed_at': prior_players['observed_at']}
    except Exception as exc:
        players = None
        emit('player_inputs_unavailable', error=str(exc))
    return teams, players


def load_lineups(db, slate, day, now, commit):
    saved = store.rows(db, 'lineup_snapshots', day)
    previous = json.loads(saved[0]['Details JSON']) if saved else []
    latest = {lineup['gameId']: lineup for lineup in previous if lineup.get('sport') == 'NCAAM'}
    needed = [g for g in slate if g['status']=='STATUS_SCHEDULED' and timestamp(g['tipoff']) > now]
    fresh = lambda g: g['game_id'] in latest and timedelta(0) <= now-timestamp(latest[g['game_id']]['observedAt']) <= timedelta(minutes=10 if timestamp(g['tipoff'])-now<=timedelta(minutes=90) else 35)
    if any(not fresh(g) for g in needed):
        try:
            incoming, rejected = fetch_lineups(needed, now)
            for error in rejected: emit('lineup_rejected', reason=error)
            for lineup in incoming:
                old = latest.get(lineup['gameId'])
                if old is None or timestamp(lineup['observedAt']) > timestamp(old['observedAt']): latest[lineup['gameId']] = lineup
            if commit and incoming:
                store.upsert(db, 'lineup_snapshots', {'Date': day, 'Game Key': f'ncaam-lineups|{day}', 'Market':'Lineups', 'Selection':'NCAAM',
                    'Updated At': datetime.now(timezone.utc).isoformat(), 'Details JSON': json.dumps(list(latest.values()))})
        except Exception as exc: emit('lineups_unavailable', error=str(exc))
    return latest


def run(args):
    now = datetime.now(timezone.utc)
    if args.commit and os.getenv("NCAAM_AUTO_BUILDER_ENABLED", "").lower() not in {"1", "true", "yes"}:
        raise ValueError("NCAAM_AUTO_BUILDER_ENABLED=1 is required for scheduled saves.")
    from shared import turso_storage as db
    if not db.is_turso_ready():
        raise ValueError("The existing Turso connection is required.")
    day = str(now.astimezone(ET).date())
    source, observed_at = get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={day.replace('-', '')}&groups=50&limit=1000", with_observation=True)
    payload = json.loads(source)
    slate = parse_slate(payload, day)
    existing = store.rows(db, store.FORECASTS, day)
    history = [json.loads(row['Details JSON']) for row in existing]
    keys = {row['recordId'] for row in history}
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
    # The schedule request already contains current bookmaker lines. Reuse it
    # without extra source calls, and never turn these quotes into split rows.
    try:
        espn, rejected = parse_espn_ncaam_lines(payload, slate, datetime.now(timezone.utc), observed_at)
        for row in rejected:
            emit("espn_market_rejected", detail=row)
    except ValueError as exc:
        espn = {}
        emit("espn_unavailable", error=str(exc))
    fallback = []
    if any(bind_market(game, markets) is None and game["game_id"] not in espn for game, _, _ in pending):
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
    bundle, player_inputs = load_model_inputs(db, day, season, datetime.now(timezone.utc), args.commit)
    lineups = load_lineups(db, slate, day, datetime.now(timezone.utc), args.commit)
    failures = 0
    for game, phase, record_id in pending[:args.limit]:
        try:
            current = datetime.now(timezone.utc)
            market = bind_market(game, markets) or espn.get(game["game_id"]) or bind_market(game, fallback)
            if not market:
                emit("waiting_for_market", game_id=game["game_id"], game=f"{game['away']} @ {game['home']}")
                continue
            if timestamp(game["tipoff"]) - current < timedelta(minutes=10):
                continue
            lineup = lineups.get(game['game_id'])
            adjustment = None
            if player_inputs:
                weights = {side: find_rating(bundle, game[side+'_aliases']).get('current_weight', 1) for side in ('away','home')}
                adjustment = lineup_adjustments(game, lineup, {**player_inputs, 'team_current_weights':weights}, current)
            inputs = {'lineup': fingerprint(lineup) if lineup else 'unavailable',
                      'ratings': [find_rating(bundle, game[side+'_aliases']) for side in ('away','home')],
                      'league_efficiency': bundle['league_efficiency'],
                      'rotation': [{side: adjustment[side] for side in ('away','home')}] if adjustment else []}
            # Capture material revisions, preserving earlier forecasts. Observation
            # timestamps alone cannot create another projection or another record.
            digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:16]
            previous = max((f for f in history if f['gameId'] == game['game_id'] and f['modelVersion'] == MODEL_VERSION and f['phase'] == phase),
                           key=lambda f: (timestamp(f['lockedAt']), f['recordId']), default=None)
            if previous and previous.get('inputDigest') == digest:
                emit('already_saved', game_id=game['game_id'], phase=phase)
                continue
            record_id += '|' + digest
            if record_id in keys:
                # A -> B -> A must restore A as the newest forecast. Reusing A's
                # original key would leave B active after an availability reversal.
                record_id += '|r' + hashlib.sha256(previous['recordId'].encode()).hexdigest()[:12]
            forecast = project(game, bundle, market, current.isoformat(), lineup_adjustment=adjustment)
            if adjustment:
                scenarios = []
                for scenario in adjustment['scenarios']:
                    alternative = project(game, bundle, market, current.isoformat(), lineup_adjustment=scenario)
                    scenarios.append({'player':scenario['player'], 'team':scenario['team'], 'homeMarginChange':round(forecast['fair_home_spread']-alternative['fair_home_spread'],2),
                                      'totalChange':round(alternative['total']-forecast['total'],2)})
                forecast['availability_scenarios'] = scenarios
                forecast['lineup_adjustment']['scenarios'] = []
            fresh_lineup = valid_lineup(game, lineup, current)
            forecast.update(lineup=lineup, availability_mode='rotowire_html' if fresh_lineup else 'expected_rotation',
                            injury_verified=fresh_lineup, starters_confirmed=False,
                            availability_as_of=lineup['observedAt'] if lineup else '', availability_source=lineup['sourceUrl'] if lineup else '')
            forecast.update(recordId=record_id, gameId=game["game_id"], modelVersion=MODEL_VERSION,
                            lockedAt=current.isoformat(), tipoff=game["tipoff"], phase=phase, inputDigest=digest)
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
