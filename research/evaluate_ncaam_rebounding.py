"""Read-only replay of the fixed zero-sum rebounding rule on frozen inputs.

Date-only prior-game features are retrospectively reconstructed. Original
download provenance and the assumed feature-availability clock are retained;
these are not claimed as forecasts issued before the historical games.
"""
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from builders.ncaam_model import team_key, timestamp
from builders.ncaam_rebounding import parse_rebounding_inputs
from builders.ncaam_matchup_projection import from_captured_inputs, MODEL_VERSION, PRE_REBOUND_MODEL_VERSION
from builders.ncaam_point_edges import capture_grades


def replay(snapshot, game_log_text, members, *, source_provenance=None):
    data = snapshot.get('ncaamMarket', snapshot); latest = {}
    for f in data['forecasts']:
        if not timestamp(f['lockedAt']) < timestamp(f['tipoff']): continue
        old = latest.get(str(f['gameId']))
        if old is None or timestamp(f['lockedAt']) > timestamp(old['lockedAt']): latest[str(f['gameId'])] = f
    dates = {timestamp(f['tipoff']).astimezone(ZoneInfo('America/New_York')).date().isoformat() for f in latest.values()}
    if len(dates) != 1: raise ValueError('Replay requires one Eastern calendar game date.')
    observed = min((f['lockedAt'] for f in latest.values()), key=timestamp)
    profiles = parse_rebounding_inputs(game_log_text, 2027, observed,
        {team_key(name): {'team': name} for name in members})
    games = []; computed = datetime.now(timezone.utc).isoformat()
    for source in sorted(latest.values(), key=lambda f: (timestamp(f['tipoff']), str(f['gameId']))):
        a, h = deepcopy(source['away_rating']), deepcopy(source['home_rating'])
        for rating in (a, h): rating['rebounding'] = deepcopy(profiles['teams'][team_key(rating['team'])])
        benchmark = source['matchup_projection']['national_benchmark']
        kwargs = {'as_of': source['lockedAt'], 'tipoff': source['tipoff'], 'source_url': source['ratings_source_url'],
            'home_court_points': source['home_court_points'], 'neutral_site': source['neutral_site'],
            'lineup_adjustment': source.get('lineup_adjustment')}
        before = from_captured_inputs(a, h, benchmark, model_version=PRE_REBOUND_MODEL_VERSION, **kwargs)
        after = from_captured_inputs(a, h, benchmark, model_version=MODEL_VERSION, **kwargs)
        captures = []
        for p, version in ((before, PRE_REBOUND_MODEL_VERSION), (after, MODEL_VERSION)):
            f = {**deepcopy(source), 'away_rating': a, 'home_rating': h,
                'modelVersion': version, 'projection_engine': version, 'matchup_projection': p,
                'away_score': round(p['away_points'], 1), 'home_score': round(p['home_points'], 1),
                'total': p['total'], 'fair_home_spread': -p['margin']}
            captures.append(capture_grades(f, [benchmark], mode='replay', published_at=computed))
        old_total, new_total = captures[0]['total'], captures[1]['total']
        assert before['total'] == after['total']
        assert all(old_total[k] == new_total[k] for k in ('edge', 'grade', 'projected_side', 'projected_total', 'line'))
        old_spread, new_spread = captures[0]['spread'], captures[1]['spread']
        games.append({'game_id': str(source['gameId']), 'away_team': source['away_team'], 'home_team': source['home_team'],
            'source_record_id': source['recordId'], 'source_model_version': source['modelVersion'],
            'source_projection': {k: source['matchup_projection'][k] for k in ('away_points', 'home_points', 'total', 'margin')},
            'baseline_model': PRE_REBOUND_MODEL_VERSION, 'new_model': MODEL_VERSION,
            'before': {k: before[k] for k in ('away_points', 'home_points', 'total', 'margin')},
            'after': {k: after[k] for k in ('away_points', 'home_points', 'total', 'margin')},
            'rebounding_adjustment': after['rebounding_adjustment'],
            'spread_before': {k: old_spread[k] for k in ('projected_side', 'grade', 'edge', 'line', 'selection_team')},
            'spread_after': {k: new_spread[k] for k in ('projected_side', 'grade', 'edge', 'line', 'selection_team')},
            'total_grade': old_total['grade'], 'total_edge': old_total['edge'], 'total_unchanged': True,
            'saved_market': deepcopy(source['market'])})
    return {'date': next(iter(dates)), 'record_mode': 'RETROSPECTIVE_PRIOR_DATE_FEATURE_RECONSTRUCTION',
        'computed_at': computed, 'uses_target_results': False, 'source_provenance': source_provenance,
        'reconstructed_feature_clock': observed,
        'feature_clock_note': 'Historical research availability clock; later raw download provenance is retained separately. Only earlier-date D1 boxscores contribute.',
        'games': games, 'games_count': len(games), 'total_changes': 0, 'total_grade_changes': 0,
        'rebound_applied': sum(g['rebounding_adjustment']['status'] == 'applied' for g in games),
        'unavailable': [g['game_id'] for g in games if g['rebounding_adjustment']['status'] != 'applied']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True); parser.add_argument('--game-logs', required=True)
    parser.add_argument('--members', required=True, help='JSON fixture with a dated members array')
    parser.add_argument('--output', required=True); parser.add_argument('--provenance')
    args = parser.parse_args()
    result = replay(json.loads(Path(args.snapshot).read_text()), Path(args.game_logs).read_text(),
        json.loads(Path(args.members).read_text())['members'],
        source_provenance=json.loads(Path(args.provenance).read_text()) if args.provenance else None)
    Path(args.output).write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps({k: result[k] for k in ('games_count', 'total_changes', 'total_grade_changes', 'rebound_applied', 'unavailable')}, indent=2))
