"""NBA-style minute deltas using college OBPM/DBPM and 40-minute games.

These are research player-profile estimates, not NBA RAPM coefficients or
validated causal injury values. Unknown player ratings remain explicit gaps.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import csv
import io
import math
import re
import unicodedata

from builders.ncaam_model import team_key, timestamp
from builders.ncaam_lineups import valid_source

POLICY = 'ncaam-torvik-bpm-minute-deltas-v1'


def name_key(value):
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().lower()
    value = re.sub(r'[^a-z0-9]+', ' ', value).strip()
    return re.sub(r'\s+(jr|sr|ii|iii|iv|v)$', '', value)


def parse_player_bundle(text, season, observed_at, *, minimum_players=2000):
    timestamp(observed_at)
    players, seen, excluded = [], set(), 0
    for row in csv.reader(io.StringIO(text)):
        if not row: continue
        if len(row) < 65:
            raise ValueError('Torvik player export has an unsupported column layout.')
        if str(row[31]) != str(season):
            raise ValueError('Torvik player profile season does not match the game.')
        try:
            games, minutes = float(row[3]), float(row[4])
            bpm, offense, defense = float(row[53]), float(row[55]), float(row[56])
            pid = 'torvik:' + str(int(row[32]))
        except (ValueError, TypeError):
            continue
        if not all(math.isfinite(v) for v in (games, minutes, bpm, offense, defense)) or not 0 <= minutes <= 100 or games < 0:
            raise ValueError('Invalid Torvik player minute or impact profile.')
        # Tiny walk-on samples can have hundreds of BPM points. They are not
        # evidence for replacement minutes or a usable injury coefficient.
        if games == 0 or minutes < 1:
            excluded += 1
            continue
        if abs(bpm - offense - defense) > .05 or max(abs(offense), abs(defense)) > 50:
            raise ValueError('Torvik offensive/defensive BPM fields do not reconcile.')
        key = (team_key(row[1]), pid)
        if key in seen: raise ValueError('Duplicate Torvik college player identity.')
        seen.add(key)
        players.append({'id': pid, 'name': row[0], 'team': row[1], 'weight': minutes * .4,
                        'offense': offense, 'defense': defense, 'games': games, 'position': row[64]})
    if len(players) < minimum_players:
        raise ValueError('Torvik college player profiles are incomplete.')
    if len({p['id'] for p in players}) != len(players):
        raise ValueError('College player IDs are ambiguous across schools.')
    return {'season': int(season), 'observed_at': observed_at, 'players': players, 'excluded_small_samples': excluded,
            'source': 'Bart Torvik college player OBPM/DBPM profiles',
            'source_url': f'https://barttorvik.com/getadvstats.php?year={season}&csv=1', 'policy': POLICY}


def allocate_minutes(weights):
    """Same proportional replacement allocation as NBA, with 200 total and cap 40."""
    active = {pid: float(w) for pid, w in weights.items() if math.isfinite(float(w)) and float(w) > 0}
    if len(active) * 40 < 200:
        raise ValueError('Not enough evidenced college players to fill 200 minutes.')
    result = {pid: 0.0 for pid in weights}; remaining = 200.0
    while active:
        total = sum(active.values())
        capped = [pid for pid, weight in active.items() if remaining * weight / total > 40 + 1e-9]
        if not capped:
            for pid, weight in active.items(): result[pid] = remaining * weight / total
            break
        for pid in capped:
            result[pid] = 40.0; remaining -= 40.0; active.pop(pid)
    if abs(sum(result.values()) - 200) > 1e-6:
        raise ValueError('College replacement minutes do not sum to 200.')
    return result


def _match(name, profiles):
    key = name_key(name)
    exact = [p for p in profiles if name_key(p['name']) == key]
    if len(exact) == 1: return exact[0]
    if exact: return None
    # Source full names can use Matt/ Matthew while keeping a unique family
    # name on this exact team. Initial-only and surname-only matches are rejected.
    words = key.split()
    if len(words) < 2: return None
    matches = []
    for p in profiles:
        other = name_key(p['name']).split()
        if len(other) >= 2 and words[1:] == other[1:] and min(len(words[0]), len(other[0])) >= 4 and (words[0].startswith(other[0]) or other[0].startswith(words[0])):
            matches.append(p)
    return matches[0] if len(matches) == 1 else None


def team_adjustment(game, side, lineup, bundle, *, forced_out=()):
    empty = {'applied': False, 'offense_change_per_100': 0.0, 'defense_improvement_per_100': 0.0, 'players': [], 'flags': []}
    aliases = {team_key(a) for a in game[side + '_aliases']}
    current_profiles = [p for p in bundle['players'] if team_key(p['team']) in aliases]
    old_profiles = [p for p in bundle.get('prior_players', []) if team_key(p['team']) in aliases]
    prior_by_id = {p['id']:p for p in bundle.get('prior_players', [])}
    all_profiles = {p['id']: p for p in old_profiles + current_profiles}
    weight = float(bundle.get('team_current_weights', {}).get(side, 1.0))
    profiles = []
    for pid, player in all_profiles.items():
        old = prior_by_id.get(pid)
        current = next((p for p in bundle['players'] if p['id']==pid), player)
        profiles.append({**player, 'offense': (1-weight)*old['offense']+weight*current['offense'] if old else current['offense'],
                         'defense': (1-weight)*old['defense']+weight*current['defense'] if old else current['defense']})
    if len({p['id'] for p in profiles}) != len(profiles):
        return {**empty, 'flags': ['College player identity is ambiguous; team baseline retained.']}
    listed = lineup[side]['starters'] + lineup[side]['injuries'] if lineup else []
    statuses, mapping, flags = {}, {}, []
    if not lineup: flags.append('RotoWire unavailable; expected rotation with availability unchecked.')
    if lineup and not lineup[side].get('complete'):
        flags.append('Source projected starting five is incomplete; no missing starter was invented.')
    for player in listed:
        profile = _match(player['name'], current_profiles)
        if profile is None:
            flags.append(player['name'] + ': no unique dated player profile; impact unavailable.')
            continue
        if profile['id'] in statuses:
            return {**empty, 'flags': ['Two source players match the same college profile; team baseline retained.']}
        statuses[profile['id']] = 'OUT' if player['id'] in forced_out else player['status']
        mapping[profile['id']] = player
    try:
        current_ids = {p['id'] for p in current_profiles}
        weights = {p['id']: p['weight'] if p['id'] in current_ids and p['games'] > 0 else 0 for p in profiles}
        current_reference = allocate_minutes(weights)
        if old_profiles and weight < 1:
            old_weights = {p['id']: p['weight'] if p['games'] > 0 else 0 for p in old_profiles}
            old_reference = allocate_minutes(old_weights)
            reference = {pid: (1-weight)*old_reference.get(pid,0)+weight*current_reference[pid] for pid in weights}
        else: reference = current_reference
        current = allocate_minutes({pid: weight if statuses.get(pid) not in {'OUT', 'DOUBTFUL'} else 0 for pid, weight in weights.items()})
    except ValueError as exc:
        return {**empty, 'flags': flags + [str(exc) + ' Team baseline retained.']}
    changes = []
    for profile in profiles:
        pid = profile['id']; change = current[pid] - reference[pid]
        status = statuses.get(pid, 'UNKNOWN')
        changes.append({'id': mapping.get(pid, {}).get('id', pid), 'ratingId': pid, 'name': profile['name'],
                        'referenceMinutes': reference[pid], 'minutes': current[pid], 'status': status,
                        'assumption': 'OUT' if status in {'OUT', 'DOUBTFUL'} else 'IN',
                        'offensiveImpact': profile['offense'], 'defensiveImpact': profile['defense'],
                        'offenseChangePer100': change / 40 * profile['offense'],
                        'defenseImprovementPer100': change / 40 * profile['defense']})
    return {'applied': True, 'offense_change_per_100': sum(p['offenseChangePer100'] for p in changes),
            'defense_improvement_per_100': sum(p['defenseImprovementPer100'] for p in changes),
            'players': changes, 'flags': flags, 'complete': not flags}


def valid_lineup(game, lineup, now):
    try:
        max_age = timedelta(minutes=10 if timestamp(game['tipoff']) - now <= timedelta(minutes=90) else 35)
        return bool(lineup and lineup['sport'] == 'NCAAM' and lineup['gameId'] == game['game_id'] and lineup['date'] == game['date']
                    and timestamp(lineup['tipoff']) == timestamp(game['tipoff']) and valid_source(lineup['sourceUrl'])
                    and timedelta(0) <= now-timestamp(lineup['observedAt']) <= max_age
                    and all(lineup[side]['team'] == game[side] and lineup[side]['confirmed'] is False for side in ('away','home')))
    except (KeyError, TypeError, ValueError): return False


def lineup_adjustments(game, lineup, bundle, now):
    if int(bundle['season']) != int(game['season']) or bundle.get('policy') != POLICY:
        raise ValueError('College impact profile season or policy is invalid.')
    age = now - timestamp(bundle['observed_at'])
    if not timedelta(0) <= age <= timedelta(hours=30):
        raise ValueError('College player profiles are stale or future-dated.')
    if bundle.get('prior_players') and not timedelta(0) <= now - timestamp(bundle['prior_observed_at']) <= timedelta(days=30):
        raise ValueError('Completed-season player profiles are stale or future-dated.')
    if not valid_lineup(game, lineup, now): lineup = None
    result = {'policy': POLICY, 'as_of': now.isoformat(), 'ratingsSource': bundle['source'], 'ratingsSourceUrl': bundle['source_url'],
              'reference': 'Prior-season exposure blended with current college rotation; 200 regulation minutes', 'scenarios': []}
    for side in ('away', 'home'): result[side] = team_adjustment(game, side, lineup, bundle)
    if lineup:
        for side in ('away', 'home'):
            for player in lineup[side]['starters'] + lineup[side]['injuries']:
                if player['status'] != 'QUESTIONABLE' or len(result['scenarios']) >= 4: continue
                alternative = {s: deepcopy(result[s]) for s in ('away', 'home')}
                alternative[side] = team_adjustment(game, side, lineup, bundle, forced_out=[player['id']])
                result['scenarios'].append({'player': player['name'], 'team': game[side], **alternative})
    return result
