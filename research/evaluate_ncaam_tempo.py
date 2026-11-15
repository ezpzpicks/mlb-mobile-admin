"""Compare pace constructions on saved pregame inputs; no fitting or live writes.

Only the pace calculation differs. Both candidates use the same captured
additive offense/opposing-defense efficiency and availability adjustments.
Markets and final scores never enter either score construction.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics

from builders.ncaam_model import team_key


def stamp(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Timezone required')
    return value


def totals(f, benchmark):
    metrics = benchmark['metrics']
    away, home = f['away_rating'], f['home_rating']
    national = float(metrics['tempo']['mean'])
    arithmetic = (away['tempo'] + home['tempo']) / 2
    combined = away['tempo'] * home['tempo'] / national
    center = (metrics['offense']['mean'] + metrics['defense']['mean']) / 2
    efficiency = 0.
    impact = f.get('lineup_adjustment') or {}
    for side, opponent in (('away', 'home'), ('home', 'away')):
        efficiency += (center + f[side + '_rating']['offense'] - metrics['offense']['mean']
                       + f[opponent + '_rating']['defense'] - metrics['defense']['mean']
                       + impact.get(side, {}).get('offense_change_per_100', 0)
                       - impact.get(opponent, {}).get('defense_improvement_per_100', 0))
    return {'arithmetic': arithmetic * efficiency / 100,
            'combined': combined * efficiency / 100,
            'old_pace': arithmetic, 'new_pace': combined,
            'profile': 'both_slow' if max(away['tempo'], home['tempo']) < national
            else 'both_fast' if min(away['tempo'], home['tempo']) > national else 'mixed'}


def summary(rows):
    result = {'games': len(rows)}
    for model in ('arithmetic', 'combined'):
        errors = [r[model] - r['actual'] for r in rows]
        result[model] = {
            'mae': statistics.mean(map(abs, errors)) if errors else None,
            'rmse': math.sqrt(statistics.mean(e * e for e in errors)) if errors else None,
            'bias': statistics.mean(errors) if errors else None,
        }
    return result


def evaluate(paths, game_stats_path=None):
    rows, excluded = [], Counter()
    pace_rows, logs = [], {}
    if game_stats_path:
        for row in csv.reader(Path(game_stats_path).read_text().splitlines()):
            if len(row) != 31:
                raise ValueError('Unsupported team-game log layout')
            day = datetime.strptime(row[0], '%m/%d/%y').date().isoformat()
            key = (day, tuple(sorted((team_key(row[2]), team_key(row[4])))))
            logs.setdefault(key, []).append(row)
    for path in paths:
        data = json.loads(Path(path).read_text())['ncaamMarket']
        games = {str(g['Game ID']): g for g in data['games']}
        latest = {}
        for f in data['forecasts']:
            if f.get('phase') not in ('early', 'pregame') or not stamp(f['ratings_observed_at']) <= stamp(f['lockedAt']) < stamp(f['tipoff']):
                continue
            key = str(f['gameId'])
            if key not in latest or (stamp(f['lockedAt']), f['recordId']) > (stamp(latest[key]['lockedAt']), latest[key]['recordId']):
                latest[key] = f
        for key, f in latest.items():
            b = (f.get('matchup_projection') or {}).get('national_benchmark')
            b = b or (f.get('matchup_total') or {}).get('national_benchmark')
            b = b or (f.get('matchup_spread') or {}).get('national_benchmark')
            if not b or stamp(b['observed_at']) != stamp(f['ratings_observed_at']) or b['team_count'] < 300:
                excluded['missing_matching_pregame_benchmark'] += 1
                continue
            if (stamp(f['lockedAt']) - stamp(b['observed_at'])).total_seconds() > 30 * 3600:
                excluded['stale_benchmark'] += 1
                continue
            projection = totals(f, b)
            if game_stats_path:
                log_key = (data.get('date', Path(path).stem), tuple(sorted((team_key(f['away_rating']['team']), team_key(f['home_rating']['team'])))))
                log = logs.get(log_key, [])
                if (len(log) == 2 and len({r[24] for r in log}) == 1 and len({r[23] for r in log}) == 1
                    and log[0][22] == str(b['season'])):
                    pace_rows.append({'date': log_key[0], 'game_id': str(f['gameId']),
                        'profile': projection['profile'], 'actual': float(log[0][23]),
                        'arithmetic': projection['old_pace'], 'combined': projection['new_pace']})
                else:
                    excluded['missing_paired_pace_outcome'] += 1
            g = games.get(key, {})
            if g.get('Completed') != 'TRUE' or g.get('Status') != 'STATUS_FINAL':
                excluded['unfinished'] += 1
                continue
            if (g.get('Source') != 'ESPN NCAAM schedule; ScoresAndOdds market lines'
                or (g.get('Away Team'), g.get('Home Team')) != (f['away_team'], f['home_team'])
                or stamp(g['Game Time']) != stamp(f['tipoff'])
                or stamp(g['Result Updated']) <= stamp(f['tipoff'])):
                excluded['unverified_result_identity'] += 1
                continue
            actual = float(g['Home Score']) + float(g['Away Score'])
            if not math.isfinite(actual) or actual <= 0:
                excluded['invalid_score'] += 1
                continue
            rows.append({'date': g['Date'], 'game_id': key,
                         'game': f['away_team'] + ' @ ' + f['home_team'],
                         'actual': actual, **projection})
    return {'hypothesis': 'Combine both adjusted-tempo ratios to the captured national mean',
            'coefficients_fitted': False, 'uses_market_to_project': False,
            'source_inputs': [{'file': Path(p).name, 'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest()} for p in paths],
            'pace_outcome_source': {'url': 'https://barttorvik.com/getgamestats.php?year=2027&csv=1',
                'sha256': hashlib.sha256(Path(game_stats_path).read_bytes()).hexdigest()} if game_stats_path else None,
            'all': summary(rows),
            'by_profile': {p: summary([r for r in rows if r['profile'] == p])
                           for p in ('both_slow', 'both_fast', 'mixed')},
            'by_date': {d: summary([r for r in rows if r['date'] == d])
                        for d in sorted({r['date'] for r in rows})},
            'pace': {'all': summary(pace_rows), 'by_profile': {p: summary([r for r in pace_rows if r['profile'] == p])
                for p in ('both_slow', 'both_fast', 'mixed')}, 'games': pace_rows} if game_stats_path else None,
            'exclusions': dict(excluded), 'games': rows,
            'limits': ['Recent, limited sample; not an independent season holdout.',
                       'Comparison isolates pace; it does not validate efficiency calibration.',
                       'Final totals include overtime; availability deltas are retained as captured.',
                       'Saved benchmark availability limits which games can be evaluated.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', nargs='+')
    parser.add_argument('--output', required=True)
    parser.add_argument('--game-stats')
    args = parser.parse_args()
    report = evaluate(args.inputs, args.game_stats)
    Path(args.output).write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: ({a: b for a, b in v.items() if a != 'games'} if k == 'pace' and v else v)
                      for k, v in report.items() if k not in ('games', 'source_inputs')}, indent=2))
