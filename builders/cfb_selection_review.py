"""Explicit NCAAF matchup preferences; no scores or outcomes are inputs."""
from copy import deepcopy
import math


def validate_review(policy):
    p = policy.get('selection_review')
    if p is None:
        return
    keys = ('minimum_attacking_pair_z', 'suppressed_attack_pair_z', 'strong_offense_z',
            'average_band_z', 'slow_tempo_z', 'good_defense_z', 'medium_favorite_points',
            'strong_attack_weak_opponent_maximum_points')
    if not isinstance(p, dict) or type(p.get('enabled')) is not bool or not p.get('version'):
        raise ValueError('Invalid NCAAF selection review policy')
    for key in keys:
        value = p.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError('NCAAF review thresholds must be positive finite numbers')


def spread_review(z, side, line, p):
    selected, opponent = z[side], z['away' if side == 'home' else 'home']
    attack = (selected['offense'] + opponent['defense']) / 2
    opposing_attack = (opponent['offense'] + selected['defense']) / 2
    blocked, paths, notes = [], [], []
    if line < 0:
        if attack < p['minimum_attacking_pair_z'] and opposing_attack > -p['suppressed_attack_pair_z']:
            blocked.append('Favorite lacks a clear attacking mismatch or a clear opposing-attack suppression case')
        average_defenses = max(abs(selected['defense']), abs(opponent['defense'])) < p['average_band_z']
        if (selected['offense'] >= p['strong_offense_z'] and opponent['offense'] <= -p['strong_offense_z']
                and average_defenses):
            paths.append('strong-attack-versus-weak-attack-with-average-defenses')
            if abs(line) > p['strong_attack_weak_opponent_maximum_points']:
                blocked.append('Strong-attack favorite with average defenses exceeds the configured point ceiling')
            if opponent['tempo'] > 0:
                notes.append('Fast opponent tempo is not an attacking-quality advantage when its offense is weak')
    return {'blocked_reasons': blocked, 'supporting_paths': paths, 'notes': notes,
            'attacking_pair_z': attack, 'opposing_attacking_pair_z': opposing_attack}


def apply_selection_review(output, policy):
    p = policy.get('selection_review')
    if not p or not p['enabled']:
        return output
    validate_review(policy)
    output = deepcopy(output)
    spread, total = output['spread'], output['total']
    for grade in (spread, total):
        grade['selection_review_policy'] = deepcopy(p)
        grade['selection_review'] = {'version': p['version'], 'base_grade': grade['grade'],
            'blocked_reasons': [], 'supporting_paths': [], 'notes': []}
    if spread['side']:
        spread['selection_review'].update(spread_review(spread['z'], spread['side'], spread['line'], p))
    z = total['z']
    pairs = {a: (z[a]['offense'] + z[b]['defense']) / 2
             for a, b in (('away', 'home'), ('home', 'away'))}
    review = total['selection_review']
    review['attacking_pair_z'] = pairs
    if total['side'] == 'Over' and max(pairs.values()) < p['minimum_attacking_pair_z']:
        review['blocked_reasons'].append('Neither offense has a clearly favorable scoring matchup; fast tempo alone cannot support the Over')
    if total['side'] == 'Under':
        home_line = spread['home_line']
        favorite = 'home' if home_line < 0 else 'away' if home_line > 0 else None
        underdog = 'away' if favorite == 'home' else 'home'
        if (favorite and abs(home_line) >= p['medium_favorite_points']
                and z[underdog]['tempo'] <= -p['slow_tempo_z']
                and z[favorite]['defense'] <= -p['good_defense_z']
                and abs(z[favorite]['tempo']) < p['average_band_z']
                and pairs[underdog] <= -p['suppressed_attack_pair_z']
                and pairs[favorite] <= p['minimum_attacking_pair_z']):
            review['supporting_paths'].append('slow-underdog-attack-versus-good-favorite-defense-with-moderate-favorite-tempo')
            review['notes'].append('Supports the Under profile without imposing a particular team score')
    for grade in (spread, total):
        reasons = grade['selection_review']['blocked_reasons']
        if grade['grade'] in ('A', 'B') and reasons:
            grade.update(grade='Research', reason='; '.join(reasons))
    return output
