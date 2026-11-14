"""Ryan's November 13 matchup preferences, independent of game outcomes.

These are configurable selection constraints, not calibrated win probabilities.
They can remove an existing grade; tempo alone never creates a new grade.
"""
from copy import deepcopy
import math


def validate_review(policy):
    review = policy.get('selection_review')
    if review is None:
        return
    if type(review.get('enabled')) is not bool or not review.get('version'):
        raise ValueError('Selection review requires an enabled flag and version.')
    for name, value in review.items():
        if name in ('enabled', 'version', 'basis'):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError('Selection review thresholds must be positive finite numbers.')
    if review['matched_tempo_points'] >= review['near_even_dog_points']:
        raise ValueError('Matched-tempo dog threshold must be below the default dog threshold.')
    if review['large_favorite_points'] >= review['very_large_favorite_points']:
        raise ValueError('Favorite spread thresholds must be ordered.')


def spread_review(z, side, line, review):
    """Return exclusions and context for a fixed selected team at this quote."""
    picked = z[side]
    other = z['away' if side == 'home' else 'home']
    offense = picked['offense'] - other['offense']
    defense = other['defense'] - picked['defense']
    tempo_gap = picked['tempo'] - other['tempo']
    meaningful = review['meaningful_efficiency_z']
    even = abs(offense) < meaningful and abs(defense) < meaningful
    blocked, notes = [], []
    if line < 0:
        points = abs(line)
        if max(offense, defense) < meaningful and not (offense > 0 and defense > 0):
            blocked.append('A small advantage in only one efficiency is insufficient for a favorite')
        if points > review['weak_favorite_maximum_points'] and picked['offense'] < 0 and picked['defense'] > 0:
            blocked.append('A below-average offense and weak defense cannot lay more than the configured modest spread')
        if (points >= review['medium_favorite_points'] and max(picked['tempo'], other['tempo']) < 0
                and other['defense'] < 0 and picked['defense'] > -review['strong_defense_z']):
            blocked.append('A slow matchup without a strong favorite defense is unsuitable for this spread')
        if points >= review['large_favorite_points']:
            required = review['very_strong_defense_z'] if points >= review['very_large_favorite_points'] else review['strong_defense_z']
            if picked['defense'] > -required:
                blocked.append('A large favorite needs a stronger national defense; fast tempo is insufficient')
            if other['offense'] > 0 and other['tempo'] > 0:
                blocked.append('The large favorite faces an above-average offense with above-average tempo')
    elif line > 0 and even and line < review['near_even_dog_points']:
        fast_dog = tempo_gap >= review['tempo_gap_z']
        matched = abs(tempo_gap) <= review['matched_tempo_z'] and line >= review['matched_tempo_points']
        if fast_dog:
            notes.append('Preferred faster-underdog style; tempo adds no points or efficiency bonus')
        elif not matched:
            blocked.append('Near-even efficiencies need more underdog points or the specified tempo fit')
    return {'version': review['version'], 'blocked_reasons': blocked, 'notes': notes,
            'offense_gap_z': offense, 'defense_gap_z': defense, 'tempo_gap_z': tempo_gap}


def apply_spread_review(result, policy):
    review = policy.get('selection_review')
    if not review or not review['enabled'] or not result.get('projected_side'):
        return result
    result = deepcopy(result)
    assessment = spread_review(result['z'], result['projected_side'], result['line'], review)
    assessment.update(base_grade=result['grade'], base_side=result['side'])
    result['selection_review'] = assessment
    if result['grade'] in ('A', 'B') and assessment['blocked_reasons']:
        result.update(grade='Research', side=None, reason='; '.join(assessment['blocked_reasons']))
    return result


def apply_total_review(result, forecast, policy):
    review = policy.get('selection_review')
    if not review or not review['enabled']:
        return result
    result = deepcopy(result)
    z = result['z']
    side = result['side']
    paths, blocked, notes = [], [], []
    strong, weak = review['strong_defense_z'], review['weak_attack_z']
    if side == 'Under':
        # A weak attack must face the other team's strong defense. Do not mix
        # unrelated teams' offense/defense labels to manufacture suppression.
        if result['line'] >= review['high_total_points'] and any(
            z[a]['offense'] <= -weak and z[b]['defense'] <= -strong
            for a, b in (('away', 'home'), ('home', 'away'))):
            paths.append('weak-attack-suppression-at-high-total')
        if any(z[a]['offense'] <= -weak and z[a]['tempo'] < 0
               and z[a]['defense'] <= -review['solid_defense_z'] and z[b]['defense'] < 0
               for a, b in (('away', 'home'), ('home', 'away'))):
            paths.append('slow-weak-attack-with-two-supporting-defenses')
        if (max(z[a]['tempo'] for a in z) <= 0
                and min(z[a]['offense'] for a in z) <= -weak
                and min(z[a]['defense'] for a in z) <= -review['solid_defense_z']):
            paths.append('slow-matchup-with-low-execution-support')
        home_line = forecast.get('market', {}).get('home_spread')
        if isinstance(home_line, (int, float)) and not isinstance(home_line, bool) and math.isfinite(home_line):
            favorite = 'home' if home_line < 0 else 'away' if home_line > 0 else None
            if (favorite and abs(home_line) >= review['large_favorite_points']
                    and z[favorite]['tempo'] <= -review['slow_controller_tempo_z']
                    and z[favorite]['defense'] <= -strong):
                paths.append('slow-strong-defense-heavy-favorite')
                notes.append('Favorite pace control is a preference hypothesis, not a guarantee')
        fast = max(z[a]['tempo'] for a in z) > 0
        if fast:
            notes.append('An above-average-tempo team can threaten the Under')
            # A generic slow matchup is not an exception to a fast-team risk.
            paths = [p for p in paths if p != 'slow-matchup-with-low-execution-support']
        if not paths:
            blocked.append('No distinct suppression, defensive-execution or slow-favorite case supports this Under')
    elif side == 'Over' and result['line'] >= review['high_total_points']:
        fast, slow = sorted(z, key=lambda a: z[a]['tempo'], reverse=True)
        if z[fast]['tempo'] > 0 and z[fast]['offense'] <= 0 and z[slow]['tempo'] < 0:
            blocked.append('High total: the faster team has below-average offense while its opponent plays slower')
    result['selection_review'] = {'version': review['version'], 'base_grade': result['grade'],
        'base_side': side, 'supporting_paths': paths, 'blocked_reasons': blocked, 'notes': notes}
    if result['grade'] in ('A', 'B') and blocked:
        result.update(grade='Research', side=None, reason='; '.join(blocked))
    return result
