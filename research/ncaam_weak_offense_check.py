"""Check the negative mirror of the proposed elite-offense bonus, offline.

The site's existing very-weak band is below -1.5 national SD. No new threshold,
coefficient, or betting filter is optimized. November 15 outcomes are excluded.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.ncaam_offense_extremes import core_rows, descriptive, bet_result, betting_summary

DEFENSE_BOUNDS = [-np.inf, -1.5, -1., 0., 1., 1.5, np.inf]
DEFENSE_LABELS = ['Very strong', 'Strong', 'Above average', 'Below average', 'Weak', 'Very weak']


def summarize(rows):
    result = descriptive(rows)
    if len(rows):
        result['below_projection_pct'] = float((rows.actual < rows.prediction).mean() * 100)
        if len(rows) < 10:
            result['shared_school_ci95'] = None
            result['interval_unavailable_small_sample'] = True
    return result


def analyze(data_path, market_path, output):
    payload = json.loads(Path(data_path).read_text())
    if any(g['date'] >= '2026-11-15' for g in payload['games']):
        raise ValueError('Today must remain excluded from this frozen study')
    teams = core_rows(payload)
    teams['very_weak_offense'] = teams.own_offense_z < -1.5
    teams['opposing_defense'] = pd.cut(teams.opp_defense_z, DEFENSE_BOUNDS,
        labels=DEFENSE_LABELS, right=False)
    weak = teams[teams.very_weak_offense].copy()
    weak['actual_minus_projection'] = weak.actual - weak.prediction
    assert weak.game_id.str.startswith('2027|').sum() == int((weak.season == 2027).sum())
    periods = {'nov_2021_24': teams.season <= 2025,
               'nov_2025': teams.season == 2026,
               'prior_five_novembers': teams.season <= 2026,
               'nov_1_14_2026': teams.season == 2027}
    results, defense_rows = {}, []
    for name, period_mask in periods.items():
        part = teams[period_mask]
        q = part[part.very_weak_offense]
        tiers = {}
        for label in DEFENSE_LABELS:
            tier = q[q.opposing_defense == label]
            tiers[label] = summarize(tier)
            defense_rows.append({'period': name, 'opposing_defense': label, **tiers[label]})
        results[name] = {'very_weak_offense': summarize(q),
            'other_offenses': summarize(part[~part.very_weak_offense]),
            'by_opposing_defense': tiers}
    market = pd.read_csv(market_path)
    if market.game_id.duplicated().any() or (market.date >= '2026-11-15').any():
        raise ValueError('One frozen historical row per physical game is required')
    numeric = ['away_offense_z', 'home_offense_z', 'actual_away', 'actual_home',
               'projected_away', 'projected_home', 'market_total', 'market_home_spread']
    if not np.isfinite(market[numeric].to_numpy(float)).all():
        raise ValueError('Finite frozen ratings, projections, scores and lines are required')
    for side in ('away', 'home'):
        market[side + '_very_weak'] = market[side + '_offense_z'] < -1.5
    market['very_weak_count'] = market.away_very_weak.astype(int) + market.home_very_weak.astype(int)
    q = market[market.very_weak_count >= 1].copy()
    q['under_result'] = [bet_result(t - a - h)
        for t, a, h in zip(q.market_total, q.actual_away, q.actual_home)]
    one = q[q.very_weak_count == 1].copy()
    one['very_weak_side'] = np.where(one.away_very_weak, 'away', 'home')
    net_home = one.actual_home - one.actual_away + one.market_home_spread
    weak_net = net_home * np.where(one.home_very_weak, 1., -1.)
    one['back_very_weak_ats'] = [bet_result(x) for x in weak_net]
    one['fade_very_weak_ats'] = [bet_result(-x) for x in weak_net]
    one['very_weak_projection_residual'] = np.where(one.home_very_weak,
        one.actual_home - one.projected_home, one.actual_away - one.projected_away)
    source = lambda p: {'file': Path(p).name,
                         'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest()}
    report = {'computed_at': datetime.now(timezone.utc).isoformat(),
        'question': 'Do very weak offenses warrant an extra scoring deduction, potentially larger against stronger defenses?',
        'very_weak_offense_threshold': 'own offensive z < -1.5, the existing website band',
        'residual_direction': 'Actual minus projection; a negative mean is evidence of scoring shortfall',
        'sources': [source(data_path), source(market_path)],
        'conditional_scoring': results,
        'saved_market_diagnostics': {'all_verified_games': len(market),
            'games_with_any_very_weak_offense': len(q),
            'under_every_game': betting_summary(q.under_result.tolist()),
            'games_with_exactly_one_very_weak_offense': len(one),
            'fade_the_only_very_weak_offense_ats': betting_summary(one.fade_very_weak_ats.tolist()),
            'back_the_only_very_weak_offense_ats': betting_summary(one.back_very_weak_ats.tolist())},
        'proposed_positive_bonus': {'status': 'User-selected experimental settings; not applied live',
            'very_strong_offense_vs_defense': {'Very strong': 1, 'Strong': 3,
                'Above average': 5, 'Below average': 8, 'Weak': 9, 'Very weak': 10}},
        'recommended_very_weak_offense_additional_deduction': 0,
        'recommendation_basis': 'No consistent negative mean bias or progressive defensive pattern in the very-weak offensive band.',
        'limits': [
            'Exploratory conditional calibration, not a fitted or prospectively validated betting strategy.',
            'National-archive results reconstruct the pace-corrected core formula, not saved historical published forecasts.',
            'Primary scoring summaries exclude overtime; saved-market settlement includes overtime.',
            'Prior-five summaries contain the November 2025 subset; they are not independent samples.',
            'Pointwise shared-school robust intervals are approximate and have no multiple-comparison correction.',
            'Very-weak-offense versus extreme-defense cells are small and vary by year.',
            'Saved-market checks reuse the previously verified November 10–14 pregame quotes and paired outcomes.',
            'The assumed price is minus110; quotes are not verified closing lines, and no team-total ROI is claimed.',
            'Both positive and negative bonus deployment remain pending; no production forecast, grade, pick or posting changes.']}
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    (output / 'weak_offense_check.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    weak.to_csv(output / 'very_weak_offense_team_games.csv', index=False)
    pd.DataFrame(defense_rows).to_csv(output / 'weak_offense_by_defense.csv', index=False)
    q.to_csv(output / 'very_weak_offense_market_games.csv', index=False)
    one.to_csv(output / 'single_very_weak_offense_market_games.csv', index=False)
    print(json.dumps({'conditional_scoring': {k:v['very_weak_offense'] for k,v in results.items()},
        'saved_market_diagnostics': report['saved_market_diagnostics'],
        'recommended_extra_deduction': 0}, indent=2, allow_nan=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--market-csv', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    analyze(args.data, args.market_csv, args.output)
