#!/usr/bin/env python3
"""Chronological, market-independent team-home-court replay; no tuning or writes to live data."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from builders.ncaam_home_court import POLICY, bundled_history, fit_home_courts, home_court_for
from builders.ncaam_margin_calibration import NEUTRAL_MARGIN_SCALE, HOME_COURT_POINTS
from builders.ncaam_model import MODEL_VERSION, project, team_key
from research.evaluate_ncaam_margin_calibration import evaluate as evaluate_v05, summarize, stamp


def evaluate(historical_path, live_paths):
    baseline = evaluate_v05(historical_path, live_paths)
    historical = json.loads(historical_path.read_bytes())
    history = bundled_history()
    models = {}
    now = datetime.now(timezone.utc).isoformat()
    def model(season, day):
        key = (season, day)
        if key not in models:
            models[key] = fit_home_courts(history['games'], season, day, now)
        return models[key]
    comparisons, missing = {}, []
    for year in (2025, 2026):
        rows = []
        for row in historical:
            if row['season'] != year:
                continue
            fitted = model(year, row['date'])
            court = home_court_for(fitted, row['home_key'], season=year, game_date=row['date'], neutral_site=row['neutral_site'])
            if court['fallback']:
                missing.append({'game_id':row['game_id'], 'team':row['home_key'], 'season':year})
            scaled = NEUTRAL_MARGIN_SCALE * row['neutral_margin']
            rows.append({'actual':row['actual_margin'], 'line':row['home_spread'], 'baseline':row['baseline_margin'],
                         'v05':scaled + (0 if row['neutral_site'] else HOME_COURT_POINTS),
                         'league_only':scaled + (0 if row['neutral_site'] else fitted['league_points']),
                         'team_home_court':scaled + court['value']})
        comparisons[str(year)] = {label:summarize(rows, label) for label in ('baseline','v05','league_only','team_home_court')}
    accepted = {r['game_id']:r for r in baseline['live_game_comparisons']}
    live, team_examples = [], {}
    for path in live_paths:
        data = json.loads(path.read_bytes())['ncaamMarket']
        latest = {}
        for f in data['forecasts']:
            if f['gameId'] not in accepted or f['phase'] != 'pregame':
                continue
            if f['gameId'] not in latest or stamp(f['lockedAt']) > stamp(latest[f['gameId']]['lockedAt']):
                latest[f['gameId']] = f
        for game_id, forecast in latest.items():
            prior = accepted[game_id]
            # This opening-season check has fewer than five current games per
            # team, so the runtime's minimum-sample rule uses completed seasons.
            fitted = model(2027, data['date'])
            # Recreate the estimator's pregame availability timestamp, rather
            # than claiming today's retrospective fit was saved before tipoff.
            fitted_at_decision = {**fitted, 'observed_at':forecast['lockedAt']}
            court = home_court_for(fitted_at_decision, forecast['home_rating']['team'], season=2027,
                                   game_date=data['date'], neutral_site=forecast['neutral_site'])
            away, home = forecast['away_rating'], forecast['home_rating']
            game = {'away':forecast['away_team'], 'home':forecast['home_team'], 'away_aliases':[away['team']],
                    'home_aliases':[home['team']], 'tipoff':forecast['tipoff'], 'season':2027, 'neutral_site':forecast['neutral_site']}
            bundle = {'season':2027, 'observed_at':forecast['ratings_observed_at'], 'source':forecast['ratings_source'],
                      'source_url':forecast['ratings_source_url'], 'league_efficiency':forecast['league_efficiency'],
                      'teams':{team_key(away['team']):away,team_key(home['team']):home}}
            revised = project(game,bundle,forecast['market'],forecast['lockedAt'],
                              lineup_adjustment=forecast.get('lineup_adjustment'),home_court=court)
            if (revised['total'] != forecast['total'] or revised['lineup_margin_change'] != forecast['lineup_margin_change']
                or revised['lineup_total_change'] != forecast['lineup_total_change']):
                raise ValueError('Home-court replay changed the total or amplified availability.')
            live.append({**prior, 'v05':prior['calibrated'], 'team_home_court':-revised['fair_home_spread'],
                         'home_court_points':court['value'], 'home_court_team':court['team_key']})
            team_examples[home['team']] = {'value':court['value'], 'league_points':court['league_points'],
                                          'venue_adjustment':court['venue_adjustment'], 'home_games':court['actual_home_games']}
    live_results = {day:{label:summarize([r for r in live if r['date']==day],label)
                        for label in ('baseline','v05','team_home_court')} for day in sorted({r['date'] for r in live})}
    live_results['combined'] = {label:summarize(live,label) for label in ('baseline','v05','team_home_court')}
    return {'policy':POLICY,'model_version':MODEL_VERSION,'historical':comparisons,'live':live_results,
            'historical_unknown_home_samples':missing,'live_exclusions':baseline['live_exclusions'],
            'live_inputs':baseline['live_inputs'],'team_examples':team_examples,
            'history_sha256':history['sha256'],'history_sources':history['sources'],
            'historical_replay_sha256':hashlib.sha256(historical_path.read_bytes()).hexdigest(),
            'live_comparisons':live,'limits':['Retrospective research replay, not saved historical v0.6 selections.',
              'Every fit filters game dates strictly before the evaluated day; same-day finals are excluded.',
              'Fixed settings; no model or threshold selection on validation, holdout or current-season results.',
              'Historical margin scaling is the original frozen opening-season coefficient.',
              'Sample support measures data volume, not a calibrated win probability.',
              'The team estimate pools a school\'s home games; arena moves are not separately identified.',
              'Official selection and A/B grades remain on hold.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--historical',type=Path,required=True)
    parser.add_argument('--live',type=Path,action='append',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=evaluate(args.historical,args.live)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'historical':{y:{k:v['all']['margin_mae'] for k,v in x.items()} for y,x in report['historical'].items()},
                      'live_mae':{k:v['all']['margin_mae'] for k,v in report['live']['combined'].items()},
                      'historical_fallbacks':len(report['historical_unknown_home_samples'])},indent=2))


if __name__=='__main__':
    main()
