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
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from time import monotonic
from pathlib import Path
import sys
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from builders.ncaam_model import MODEL_VERSION, prepare_season_ratings, find_rating, parse_ratings, project, team_key, timestamp
from builders.ncaam_lineups import fetch_lineups, fingerprint
from builders.ncaam_player_impact import POLICY as PLAYER_POLICY, parse_player_bundle, lineup_adjustments, valid_lineup, indexed_player_bundle
from builders.ncaam_rosters import parse_roster, roster_url, valid_roster
from builders.ncaam_game_counts import POLICY as GAME_COUNT_POLICY, parse_game_counts
from builders.ncaam_home_court import POLICY as COURT_POLICY, LOOKBACK_SEASONS, bundled_history, fit_home_courts, home_court_for
from shared import ncaam_auto_storage as store
from shared.espn_ncaam_lines import parse_espn_ncaam_lines, response_observed_at
from shared.ncaam_division_one import division_one_matchup, division_one_aliases
from builders.ncaam_matchup_grades import load_policy, market_reference, policy_digest, population_benchmark
from builders.ncaam_matchup_live import capture_grade, grade_storage_row, load_reference_quotes, unavailable_grade
from builders.ncaam_spread_grades import load_spread_policy, capture_spread_grade, unavailable_spread_grade, spread_storage_row

ET = ZoneInfo("America/New_York")
MAX_SLATE_GAMES = 200


def emit(event, **details):
    print(json.dumps({"sport": "NCAAM", "event": event, **details}, default=str), flush=True)


def get(url, *, with_observation=False, timeout=20):
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; EZPZPicks/1.0)", "Accept": "application/json,text/csv"}), timeout=timeout) as response:
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
            game[side + "_aliases"] = list(dict.fromkeys([*(str(team[k]) for k in ("displayName", "shortDisplayName", "location", "abbreviation") if team.get(k)),
                                                        *division_one_aliases(team['id'], game['season'])]))
        if not game["game_id"].isdigit() or game["away_id"] == game["home_id"]:
            raise ValueError("Invalid college basketball game identity.")
        if not division_one_matchup(game["away_id"], game["home_id"], game["season"]):
            continue
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


def cached_input(db, dataset, key, url, parser, season, now, commit, max_hours=6, expected_policy=None):
    rows = store.rows(db, dataset, key)
    candidates = []
    for row in rows:
        try:
            candidate = json.loads(row['Details JSON'])
            if candidate['season'] == season and timestamp(candidate['observed_at']) <= now and (expected_policy is None or candidate.get('policy') == expected_policy):
                candidates.append(candidate)
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
    counts = None
    try:
        counts = cached_input(db, 'ncaam_d1_game_counts', day, f'https://barttorvik.com/getgamestats.php?year={season}&csv=1',
                              lambda text, year, observed: parse_game_counts(text, year, observed, current['teams']), season, now, commit,
                              expected_policy=GAME_COUNT_POLICY)
    except Exception as exc:
        emit('d1_game_counts_unavailable', error=str(exc))
    # Fresh downloads are observed after this function starts. Validate the
    # combined input snapshot at completion, before any forecast is locked.
    teams = prepare_season_ratings(current, prior, datetime.now(timezone.utc).isoformat(), d1_games=counts)
    try:
        teams['home_court_model'] = load_home_court_model(db, day, season, counts, datetime.now(timezone.utc), commit)
    except Exception as exc:
        # A source/cache failure cannot turn missing school history into a
        # fabricated team effect or block otherwise valid team projections.
        emit('home_court_unavailable', error=str(exc))
    # A sparse current-season export must never block the completed-season
    # profiles. Load and validate the two independent sources independently.
    prior_players = None
    try:
        prior_players = cached_input(db, 'ncaam_player_profiles', f'season-{season-1}', f'https://barttorvik.com/getadvstats.php?year={season-1}&csv=1',
                                     parse_player_bundle, season-1, now, commit, 720, expected_policy=PLAYER_POLICY)
    except Exception as exc:
        emit('prior_player_inputs_unavailable', error=str(exc))
    players = None
    try:
        players = cached_input(db, 'ncaam_player_profiles', day, f'https://barttorvik.com/getadvstats.php?year={season}&csv=1',
                               partial(parse_player_bundle, allow_partial=True), season, now, commit, expected_policy=PLAYER_POLICY)
    except Exception as exc:
        emit('current_player_inputs_unavailable', error=str(exc))
    if players or prior_players:
        players = {**(players or {'players': [], 'observed_at': prior_players['observed_at']}),
                   'season': season, 'policy': PLAYER_POLICY,
                   'source': 'Bart Torvik player profiles with completed-season fallback',
                   'source_url': f'https://barttorvik.com/getadvstats.php?year={season if players else season-1}&csv=1',
                   'profile_mode': 'current_with_prior_fallback' if players else 'prior_fallback',
                   'prior_players': prior_players['players'] if prior_players else [],
                   'prior_observed_at': prior_players['observed_at'] if prior_players else '',
                   'prior_source_url': prior_players['source_url'] if prior_players else ''}
    return teams, players


def load_home_court_model(db, day, season, counts, now, commit):
    """One compact indexed cache read and no additional provider request.

    The same bulk log already used for D1 counts supplies current-season finals.
    A fingerprint changes only for result/venue evidence, not download times.
    """
    history = bundled_history()
    available = {source['season'] for source in history['sources']}
    if not set(range(season - LOOKBACK_SEASONS, season)).issubset(available):
        raise ValueError('The completed-season home-court snapshot needs its annual update.')
    current = counts.get('home_court_games', []) if counts and counts.get('policy') == GAME_COUNT_POLICY else []
    digest = hashlib.sha256(json.dumps({'policy':COURT_POLICY, 'history':history['sha256'], 'season':season,
                                       'date':day, 'current':current}, sort_keys=True).encode()).hexdigest()
    saved = store.rows(db, 'ncaam_home_court_models', day) if db is not None else []
    for row in saved:
        try:
            model = json.loads(row['Details JSON'])
            if (model.get('policy') == COURT_POLICY and model.get('input_digest') == digest
                and model['season'] == season and model['cutoff_date'] == day
                and timestamp(model['observed_at']) <= now):
                return model
        except (KeyError, TypeError, ValueError):
            continue
    model = fit_home_courts([*history['games'], *current], season, day, now.isoformat())
    model.update(input_digest=digest, historical_source_sha256=history['sha256'])
    if commit:
        store.upsert(db, 'ncaam_home_court_models', {'Date':day, 'Game Key':f'home-court|{day}',
                     'Market':'Home Court', 'Selection':COURT_POLICY, 'Updated At':now.isoformat(),
                     'Details JSON':json.dumps(model)})
    return model


def load_rosters(db, slate, lineups, season, now, commit):
    """One indexed cache read; refresh current membership at most daily/team.

    Roster requests are bounded to four in flight and a one-minute budget.
    Missing membership keeps that team's forecast at the source baseline.
    """
    requested = {game[side+'_id'] for game in slate if valid_lineup(game, lineups.get(game['game_id']), now) for side in ('away', 'home')}
    if not requested: return {}
    key = lambda tid: f'season-{season}|team-{tid}'
    saved = store.rows_for_keys(db, 'ncaam_roster_inputs', [key(tid) for tid in requested])
    rosters, retained, attempts = {}, {}, {}
    for row in saved:
        try:
            tid = row.get('Selection', '')
            attempt = timestamp(row['Last Attempt At']) if row.get('Last Attempt At') else None
            if tid in requested and attempt and attempt <= now: attempts[tid] = attempt
            roster = json.loads(row['Details JSON'])
            if isinstance(roster, dict) and roster.get('team_id') in requested: retained[roster['team_id']] = roster
            if roster['team_id'] in requested and valid_roster(roster, roster['team_id'], season, now): rosters[roster['team_id']] = roster
        except (KeyError, TypeError, ValueError): continue
    pending = [tid for tid in sorted(requested)
               if (tid not in rosters or now-timestamp(rosters[tid]['observed_at']) >= timedelta(days=1))
               and (tid not in attempts or now-attempts[tid] >= timedelta(hours=1))]
    def fetch_one(tid):
        try:
            remaining = deadline-monotonic()
            if remaining < 1: return tid, None, 'Roster refresh budget exhausted.'
            raw, observed = get(roster_url(tid, season), with_observation=True, timeout=min(15, remaining))
            return tid, parse_roster(json.loads(raw), tid, season, observed.isoformat()), None
        except Exception as exc: return tid, None, str(exc)
    deadline = monotonic()+60
    with ThreadPoolExecutor(max_workers=4) as pool:
        for offset in range(0, len(pending), 4):
            if monotonic() >= deadline:
                emit('roster_refresh_deferred', remaining=len(pending)-offset)
                break
            for tid, roster, error in pool.map(fetch_one, pending[offset:offset+4]):
                if error:
                    emit('roster_unavailable', team_id=tid, error=error)
                elif not valid_roster(roster, tid, season, datetime.now(timezone.utc)):
                    emit('roster_rejected', team_id=tid)
                    roster = None
                if roster:
                    previous = retained.get(tid)
                    if not previous or timestamp(roster['observed_at']) >= timestamp(previous['observed_at']): retained[tid] = roster
                    if valid_roster(retained[tid], tid, season, datetime.now(timezone.utc)): rosters[tid] = retained[tid]
                if commit:
                    # Attempt time is a retry clock, never evidence that old
                    # membership became fresh. Preserve the last source row.
                    checked_at = datetime.now(timezone.utc).isoformat()
                    store.upsert(db, 'ncaam_roster_inputs', {'Date':key(tid), 'Game Key':'roster|'+key(tid), 'Market':'Roster',
                        'Selection':tid, 'Updated At':retained.get(tid, {}).get('observed_at', ''),
                        'Last Attempt At':checked_at, 'Details JSON':json.dumps(retained.get(tid))})
    return rosters


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
    emit("plan", date=day, pending=len(pending), existing=len(existing), limit=args.limit, model_version=MODEL_VERSION,
         mode="commit" if args.commit else "evaluate" if args.evaluate else "plan")
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
    game_markets = {game['game_id']: bind_market(game, markets) or espn.get(game['game_id']) or bind_market(game, fallback) for game, _, _ in pending}
    modeled_slate = [game for game, _, _ in pending if game_markets[game['game_id']]]
    if not modeled_slate:
        for game, _, _ in pending: emit('waiting_for_market', game_id=game['game_id'], game=f"{game['away']} @ {game['home']}")
        return 0
    bundle, player_inputs = load_model_inputs(db, day, season, datetime.now(timezone.utc), args.commit)
    matchup_policy = load_policy()
    spread_policy = load_spread_policy()
    matchup_benchmark = population_benchmark(bundle)
    try:
        matchup_references = load_reference_quotes(db, day, datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        matchup_references = []
        emit('matchup_reference_unavailable', error=str(exc))
    try:
        reference = market_reference(matchup_references, datetime.now(timezone.utc).isoformat(), matchup_policy)
        matchup_reference_signature = {k:reference[k] for k in ('low_total','high_total','quote_count')}
    except ValueError:
        matchup_reference_signature = 'unavailable'
    if player_inputs: player_inputs = indexed_player_bundle(player_inputs)
    lineups = load_lineups(db, modeled_slate, day, datetime.now(timezone.utc), args.commit)
    rosters = load_rosters(db, modeled_slate, lineups, season, datetime.now(timezone.utc), args.commit) if player_inputs else {}
    failures = 0
    for game, phase, record_id in pending[:args.limit]:
        try:
            current = datetime.now(timezone.utc)
            market = game_markets[game['game_id']]
            if not market:
                emit("waiting_for_market", game_id=game["game_id"], game=f"{game['away']} @ {game['home']}")
                continue
            if timestamp(game["tipoff"]) - current < timedelta(minutes=10):
                continue
            lineup = lineups.get(game['game_id'])
            adjustment = None
            if player_inputs:
                counts = {side: find_rating(bundle, game[side+'_aliases']).get('d1_games_played') for side in ('away','home')}
                adjustment = lineup_adjustments(game, lineup, {**player_inputs, 'team_d1_games':counts, 'rosters':rosters}, current)
            inputs = {'lineup': fingerprint(lineup) if lineup else 'unavailable',
                      'ratings': [find_rating(bundle, game[side+'_aliases']) for side in ('away','home')],
                      'league_efficiency': bundle['league_efficiency'],
                      'rotation': [{side: adjustment[side] for side in ('away','home')}] if adjustment else []}
            court_model = bundle.get('home_court_model')
            home_court = home_court_for(court_model, find_rating(bundle, game['home_aliases'])['team'],
                                       season=season, game_date=game['date'], neutral_site=game['neutral_site']) if court_model else None
            # Actual point/venue evidence affects forecast identity. Merely
            # refreshing an observation timestamp cannot append another row.
            inputs['home_court'] = {key:home_court.get(key) for key in ('policy','team_key','value','cutoff_date','last_game_date')} if home_court else 'unavailable'
            inputs['matchup_grade_policy'] = policy_digest(matchup_policy)
            inputs['spread_grade_policy'] = policy_digest(spread_policy)
            inputs['matchup_population'] = matchup_benchmark['metrics']
            inputs['matchup_price_context'] = matchup_reference_signature
            # A quote change can change the factor grade's price context even
            # when the independent point projection has not changed.
            inputs['market_quote'] = {'home_spread':market['home_spread'], 'total':market['total'], 'source':market['source']}
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
            forecast = project(game, bundle, market, current.isoformat(), lineup_adjustment=adjustment, home_court=home_court)
            if adjustment:
                scenarios = []
                for scenario in adjustment['scenarios']:
                    alternative = project(game, bundle, market, current.isoformat(), lineup_adjustment=scenario, home_court=home_court)
                    scenarios.append({'player':scenario['player'], 'team':scenario['team'], 'homeMarginChange':round(forecast['fair_home_spread']-alternative['fair_home_spread'],2),
                                      'totalChange':round(alternative['total']-forecast['total'],2)})
                forecast['availability_scenarios'] = scenarios
                forecast['lineup_adjustment']['scenarios'] = []
            fresh_lineup = valid_lineup(game, lineup, current)
            forecast.update(lineup=lineup, availability_mode='rotowire_html' if fresh_lineup else 'expected_rotation',
                            injury_verified=fresh_lineup, starters_confirmed=False,
                            player_profile_mode=player_inputs.get('profile_mode', 'unavailable') if player_inputs else 'unavailable',
                            availability_as_of=lineup['observedAt'] if lineup else '', availability_source=lineup['sourceUrl'] if lineup else '')
            forecast.update(recordId=record_id, gameId=game["game_id"], modelVersion=MODEL_VERSION,
                            lockedAt=current.isoformat(), tipoff=game["tipoff"], phase=phase, inputDigest=digest)
            try:
                matchup_grade = capture_grade(forecast, [matchup_benchmark], matchup_references, policy=matchup_policy)
            except Exception as exc:
                matchup_grade = unavailable_grade(forecast, exc, matchup_policy)
                emit('matchup_grade_unavailable', game_id=game['game_id'], error=str(exc))
            forecast['matchup_total'] = matchup_grade
            try:
                spread_grade = capture_spread_grade(forecast, [matchup_benchmark], policy=spread_policy)
            except Exception as exc:
                spread_grade = unavailable_spread_grade(forecast, exc, spread_policy)
                emit('spread_grade_unavailable', game_id=game['game_id'], error=str(exc))
            forecast['matchup_spread'] = spread_grade
            if args.commit:
                if datetime.now(timezone.utc) >= timestamp(game['tipoff']):
                    emit('tipoff_reached_before_save', game_id=game['game_id'])
                    continue
                row = {"Date": day, "Game Key": f"{day}|ncaam|{game['game_id']}", "Game": f"{game['away']} @ {game['home']}",
                       "Market": "Forecast", "Selection": record_id, "Updated At": current.isoformat(), "Details JSON": json.dumps(forecast)}
                store.upsert_many(db, [(store.FORECASTS,row,True),
                                      (store.MATCHUP_GRADES,grade_storage_row(forecast,matchup_grade,day),True),
                                      (store.SPREAD_GRADES,spread_storage_row(forecast,spread_grade,day),True)])
            emit("built", game_id=game["game_id"], phase=phase, saved=args.commit, model_version=MODEL_VERSION,
                 projected_away=forecast["away_score"], projected_home=forecast["home_score"],
                 line_source=market["source"], neutral_site=game["neutral_site"], grade_policy=forecast["grade_policy"],
                 player_profile_mode=forecast['player_profile_mode'],
                 home_court_points=forecast['home_court_points'], home_court_source=forecast['home_court']['source'],
                 matchup_total_grade=matchup_grade['grade'], matchup_total_side=matchup_grade['side'],
                 matchup_grade_policy=matchup_grade['policy_version'],
                 matchup_spread_grade=spread_grade['grade'], matchup_spread_side=spread_grade['side'],
                 spread_grade_policy=spread_grade['policy_version'],
                 adjusted_teams=[side for side in ('away','home') if adjustment and adjustment[side]['applied']])
        except Exception as exc:
            failures += 1
            emit("game_failed", game_id=game["game_id"], error=str(exc))
    return int(failures > 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--evaluate", action="store_true")
    mode.add_argument("--commit", action="store_true")
    parser.add_argument("--limit", type=int, default=MAX_SLATE_GAMES)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= MAX_SLATE_GAMES:
        parser.error("--limit must be 1–200")
    try:
        return run(args)
    except Exception as exc:
        emit("failed", error=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
