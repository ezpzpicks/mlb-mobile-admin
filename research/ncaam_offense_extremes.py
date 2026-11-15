"""Signed offensive-tail calibration and saved-line betting diagnostics.

The offensive extreme is fixed at the site's existing +1.5 SD threshold.
Today's illustrative games never fit a coefficient or enter the history.
This study makes no production or official-pick writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from builders.ncaam_matchup_projection import from_captured_inputs
from builders.ncaam_model import team_key
from research.ncaam_matchup_regression import prepare, subset, predict_current, dyadic_covariance

ELITE_Z = 1.5


def stamp(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('A timezone is required')
    return d


def descriptive(rows):
    if rows.empty:
        return {'team_games': 0}
    error = rows.actual.to_numpy() - rows.prediction.to_numpy()
    owners = rows[['team', 'opponent']].to_numpy()
    mean = float(error.mean())
    covariance, info = dyadic_covariance(np.ones((len(rows), 1)), error - mean, owners)
    se = float(np.sqrt(covariance[0, 0]))
    return {'team_games': len(rows), 'schools': int(rows.team.nunique()),
            'mean_actual_minus_projection': mean,
            'shared_school_ci95': [mean - 1.96 * se, mean + 1.96 * se],
            'mean_actual': float(rows.actual.mean()),
            'mean_projection': float(rows.prediction.mean()),
            'above_projection': int((error > 0).sum()),
            'below_projection': int((error < 0).sum()),
            'ties': int((error == 0).sum()),
            'above_projection_pct': float((error > 0).mean() * 100),
            'at_least_10_above_pct': float((error >= 10).mean() * 100),
            'covariance': info}


def core_rows(payload):
    g, t = prepare(payload)
    g, t = subset(g, t, list(g.season.unique()))
    _, scored = predict_current(g, t)
    out = t.merge(scored[['game_id', 'side', 'prediction']], on=['game_id', 'side'], validate='one_to_one')
    out = out.rename(columns={'points': 'actual'})
    out['elite_offense'] = out.own_offense_z >= ELITE_Z
    return out


def bet_result(net):
    return 'W' if net > 1e-7 else 'L' if net < -1e-7 else 'P'


def betting_summary(records):
    counts = Counter(records)
    w, l, p = (counts.get(k, 0) for k in ('W', 'L', 'P'))
    risk = w + l + p
    profit = w * (100 / 110) - l
    return {'wins': w, 'losses': l, 'pushes': p,
            'win_pct_excluding_pushes': 100 * w / (w + l) if w + l else None,
            'profit_units_at_assumed_minus110': profit,
            'roi_pct_at_assumed_minus110': 100 * profit / risk if risk else None}


def saved_market_rows(paths, payload):
    outcome = {}
    for g in payload['games']:
        key = (g['date'], tuple(sorted((g['away_team'], g['home_team']))))
        if key in outcome:
            raise ValueError('Duplicate physical outcome')
        outcome[key] = g
    rows, excluded, seen = [], Counter(), set()
    for path in sorted(paths):
        data = json.loads(Path(path).read_text())['ncaamMarket']
        schedule = {str(g['Game ID']): g for g in data['games']}
        latest = {}
        for f in data['forecasts']:
            if f.get('phase') not in ('early', 'pregame'):
                continue
            if not stamp(f['ratings_observed_at']) <= stamp(f['lockedAt']) < stamp(f['tipoff']):
                continue
            k = str(f['gameId'])
            if k not in latest or (stamp(f['lockedAt']), f['recordId']) > (stamp(latest[k]['lockedAt']), latest[k]['recordId']):
                latest[k] = f
        for game_id, f in latest.items():
            s = schedule.get(game_id, {})
            if s.get('Completed') != 'TRUE' or s.get('Status') != 'STATUS_FINAL':
                excluded['not_final_in_saved_schedule'] += 1
                continue
            if (s.get('Source') != 'ESPN NCAAM schedule; ScoresAndOdds market lines'
                    or (s.get('Away Team'), s.get('Home Team')) != (f['away_team'], f['home_team'])
                    or stamp(s['Game Time']) != stamp(f['tipoff'])
                    or stamp(s['Result Updated']) <= stamp(f['tipoff'])):
                excluded['unverified_saved_result_identity'] += 1
                continue
            b = ((f.get('matchup_projection') or {}).get('national_benchmark')
                 or (f.get('matchup_total') or {}).get('national_benchmark')
                 or (f.get('matchup_spread') or {}).get('national_benchmark'))
            if (not b or b['team_count'] < 300
                    or stamp(b['observed_at']) != stamp(f['ratings_observed_at'])
                    or (stamp(f['lockedAt']) - stamp(b['observed_at'])).total_seconds() > 30 * 3600):
                excluded['missing_or_stale_matched_benchmark'] += 1
                continue
            market = f.get('market') or {}
            if (market.get('home_spread') is None or market.get('total') is None
                    or not market.get('as_of')
                    or stamp(market['as_of']) > stamp(f['lockedAt'])
                    or (stamp(f['lockedAt']) - stamp(market['as_of'])).total_seconds() > 30 * 3600):
                excluded['missing_or_unverified_pregame_line'] += 1
                continue
            date = s['Date']
            if date >= '2026-11-15':
                excluded['today_excluded'] += 1
                continue
            keys = {side: team_key(f[side + '_rating']['team']) for side in ('away', 'home')}
            physical = (date, tuple(sorted(keys.values())))
            if physical in seen:
                raise ValueError('Duplicate physical game across saved snapshots')
            actual = outcome.get(physical)
            if actual is None:
                excluded['no_paired_provider_outcome'] += 1
                continue
            points = {actual[side + '_team']: actual[side + '_points'] for side in ('away', 'home')}
            if (points[keys['away']] != float(s['Away Score'])
                    or points[keys['home']] != float(s['Home Score'])):
                excluded['provider_saved_score_disagreement'] += 1
                continue
            seen.add(physical)
            projected = from_captured_inputs(f['away_rating'], f['home_rating'], b,
                as_of=f['lockedAt'], tipoff=f['tipoff'], source_url=f['ratings_source_url'],
                home_court_points=f['home_court_points'], neutral_site=f['neutral_site'],
                lineup_adjustment=f.get('lineup_adjustment'))
            az, hz = ((f[side + '_rating']['offense'] - b['metrics']['offense']['mean'])
                      / b['metrics']['offense']['sd'] for side in ('away', 'home'))
            ap, hp = points[keys['away']], points[keys['home']]
            total, line = float(market['total']), float(market['home_spread'])
            edge = projected['total'] - total
            r = {'date': date, 'game_id': game_id, 'game': f['away_team'] + ' @ ' + f['home_team'],
                 'away_team': keys['away'], 'home_team': keys['home'],
                 'away_offense_z': az, 'home_offense_z': hz,
                 'elite_count': int(az >= ELITE_Z) + int(hz >= ELITE_Z),
                 'actual_away': ap, 'actual_home': hp,
                 'projected_away': projected['away_points'], 'projected_home': projected['home_points'],
                 'market_total': total, 'market_home_spread': line,
                 'total_edge': edge, 'over_result': bet_result(ap + hp - total),
                 'baseline_total_side': 'Over' if edge > 0 else 'Under',
                 'baseline_total_result': bet_result((ap + hp - total) * np.sign(edge)),
                 'baseline_total_graded': abs(edge) >= 8,
                 'overtime': actual['overtime'], 'locked_at': f['lockedAt'],
                 'line_as_of': market['as_of'], 'source_record': f['recordId']}
            if r['elite_count'] == 1:
                side = 'away' if az >= ELITE_Z else 'home'
                r['elite_team'] = keys[side]
                r['elite_ats_result'] = bet_result((hp - ap + line) * (1 if side == 'home' else -1))
                r['elite_team_projection_residual'] = points[keys[side]] - projected[side + '_points']
            rows.append(r)
    return pd.DataFrame(rows), dict(excluded)


def run(args):
    payload = json.loads(Path(args.data).read_text())
    t = core_rows(payload)
    periods = [('prior_2021_24', t.season <= 2025), ('nov_2025', t.season == 2026),
               ('nov_1_14_2026', t.season == 2027)]
    summaries = {}
    for name, mask in periods:
        part = t[mask]
        summaries[name] = {
            'elite_offense': descriptive(part[part.elite_offense]),
            'other_offenses': descriptive(part[~part.elite_offense]),
            'elite_vs_top_defense': descriptive(part[part.elite_offense & (part.opp_defense_z < -1)]),
            'elite_vs_other_defense': descriptive(part[part.elite_offense & (part.opp_defense_z >= -1)]),
            'scores_against_very_strong_defense': descriptive(part[part.opp_defense_z <= -1.5]),
        }
    market_rows, exclusions = saved_market_rows(args.snapshots, payload)
    market = {}
    for label, q in [('any_elite_offense', market_rows[market_rows.elite_count >= 1]),
                     ('no_elite_offense', market_rows[market_rows.elite_count == 0])]:
        only_one = q[q.elite_count == 1]
        unders = q[q.baseline_total_graded & (q.baseline_total_side == 'Under')]
        market[label] = {
            'games': len(q), 'over_every_game': betting_summary(q.over_result.tolist()),
            'graded_baseline_unders': betting_summary(unders.baseline_total_result.tolist()),
            'back_the_only_elite_offense_ats': betting_summary(only_one.get('elite_ats_result', pd.Series(dtype=str)).tolist()),
        }
    result = {'computed_at': datetime.now(timezone.utc).isoformat(),
        'hypothesis': 'Elite offensive scoring may be understated even when overall forecast error changes little',
        'elite_offense_threshold_z': ELITE_Z,
        'threshold_basis': 'Existing website very-strong band; not selected on these betting results',
        'residual_direction': 'Positive means actual scoring exceeds the reconstructed pace-corrected baseline',
        'dataset_sha256': hashlib.sha256(Path(args.data).read_bytes()).hexdigest(),
        'core_conditional_scoring': summaries, 'saved_market_diagnostics': market,
        'saved_market_exclusions': exclusions,
        'sources': [{'file': Path(p).name, 'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest()} for p in args.snapshots],
        'limits': [
            'Exploratory subgroup analysis motivated by today; no prospective betting edge is established.',
            'Historical national-archive results reconstruct the same core formula; they are not actual saved published forecasts.',
            'Historical scoring summaries exclude overtime. Saved-market diagnostics use full-game settlement including overtime.',
            'Saved betting quotes are the captured pregame numbers, not verified closing lines or tradable team-total quotes.',
            'All betting calculations assume minus110 as previously requested; realized book prices may differ.',
            'No rating, coefficient, projection, grade, official pick or posting is changed.',
        ]}
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    (out / 'offense_extremes.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    t.to_csv(out / 'offense_team_games.csv', index=False)
    market_rows.to_csv(out / 'offense_saved_market_games.csv', index=False)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--snapshots', nargs='+', required=True)
    parser.add_argument('--output', required=True)
    run(parser.parse_args())
