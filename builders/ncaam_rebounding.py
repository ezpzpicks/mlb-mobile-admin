"""Pregame D1 glass inputs and versioned, total-neutral score transfers.

Experimental user-authorized margin modifier. Rates are opportunity-weighted;
the research coefficient is deliberately not represented as a fitted edge.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime
import csv
import hashlib
import io
import json
import math
import re
from zoneinfo import ZoneInfo

from builders.ncaam_model import team_key, timestamp

INPUT_POLICY = 'ncaam-d1-rebound-inputs-2026-11-16-v1'
# Retained verbatim for archived v0.11 forecasts and their proof hashes.
POLICY = 'ncaam-rebound-zero-sum-2026-11-16-v1'
POINTS_PER_EDGE_PP = .2
MAX_TRANSFER_POINTS = 3.
FULL_WEIGHT_GAMES = 3
# Archived v0.12 curve: +20 edge percentage points gives a 15% boost.
PERCENTAGE_POLICY = 'ncaam-rebound-percentage-zero-sum-2026-11-17-v2'
BOOST_FRACTION_PER_EDGE_PP = .15 / 20
ET = ZoneInfo('America/New_York')


def parse_rebounding_inputs(text, season, observed_at, teams):
    """Accept reciprocal D1 boxes only; never use a same-day result."""
    cutoff = timestamp(observed_at).astimezone(ET).date()
    groups, audit, seen = defaultdict(list), Counter(), set()
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        if len(row) != 31 or row[22] != str(season):
            raise ValueError('Unsupported Torvik rebounding log layout or season.')
        day = datetime.strptime(row[0], '%m/%d/%y').date()
        a, b, gid = team_key(row[2]), team_key(row[4]), row[24]
        if day >= cutoff:
            audit['same_day_or_future_rows'] += 1
            continue
        if a not in teams or b not in teams:
            audit['outside_d1_rows'] += 1
            continue
        if not gid or a == b or (gid, a) in seen:
            raise ValueError('Ambiguous Torvik rebounding game identity.')
        seen.add((gid, a))
        try:
            box = json.loads(row[29])
            if len(box) < 35 or sorted(map(team_key, box[2:4])) != sorted((a, b)):
                raise ValueError('Boxscore schools do not match the log.')
            box_day = None
            for form in ('%m/%d/%y', '%m/%d/%Y'):
                try:
                    box_day = datetime.strptime(str(box[0]), form).date()
                    break
                except ValueError:
                    continue
            if box_day != day:
                raise ValueError('Boxscore date does not match the log.')
            i, j = (4, 19) if team_key(box[2]) == a else (19, 4)
            own, opp = float(box[i+14]), float(box[j+14])
            score = re.fullmatch(r'([WL]),\s*(\d+)\s*[-–]\s*(\d+)(?:\s.*)?', row[6])
            if (not score or not min(own, opp) > 0 or own == opp
                or sorted((own, opp)) != sorted(map(float, score.groups()[1:]))
                or (score[1] == 'W') != (own > opp)):
                raise ValueError('Boxscore points do not match the result.')
            values = [float(box[k]) for k in (i+6, i+7, j+6, j+7)]
            if any(not math.isfinite(v) or v < 0 or not v.is_integer() for v in values):
                raise ValueError('Invalid rebound counts.')
            orb, drb, opp_orb, opp_drb = map(int, values)
            for n, d, col in ((orb, orb+opp_drb, 12), (opp_orb, opp_orb+drb, 17)):
                if d <= 0 or not abs(100*n/d-float(row[col])) <= .21:
                    raise ValueError('Provider rebound percentage disagrees with the box.')
            groups[gid].append({'team': a, 'opponent': b, 'date': day.isoformat(),
                'orb': orb, 'drb': drb, 'opp_orb': opp_orb, 'opp_drb': opp_drb,
                'points': own, 'opp_points': opp})
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            audit['invalid_boxscore_rows'] += 1
    totals = defaultdict(Counter)
    for pair in groups.values():
        if len(pair) != 2:
            audit['unpaired_games'] += 1
            continue
        a, b = pair
        if (a['team'] != b['opponent'] or a['opponent'] != b['team'] or a['date'] != b['date']
            or any(a[x] != b[y] for x, y in (('orb', 'opp_orb'), ('drb', 'opp_drb'),
                                            ('opp_orb', 'orb'), ('opp_drb', 'drb'),
                                            ('points', 'opp_points'), ('opp_points', 'points')))):
            audit['conflicting_games'] += 1
            continue
        audit['accepted_games'] += 1
        for row in pair:
            c = totals[row['team']]
            c['d1_games'] += 1
            for field in ('orb', 'drb', 'opp_orb', 'opp_drb'):
                c[field] += row[field]
            c['last_game_date'] = max(c.get('last_game_date', ''), row['date'])
    source = {'policy': INPUT_POLICY, 'season': int(season), 'observed_at': observed_at,
        'cutoff_date': cutoff.isoformat(), 'source_url': f'https://barttorvik.com/getgamestats.php?year={season}&csv=1',
        'source_sha256': hashlib.sha256(text.encode()).hexdigest()}
    profiles = {}
    for school, rating in teams.items():
        c = totals[school]
        profiles[school] = {**source, 'team': rating.get('team', school), 'd1_games': c['d1_games'],
            'last_game_date': c.get('last_game_date'),
            **{field: c[field] for field in ('orb', 'drb', 'opp_orb', 'opp_drb')}}
    return {**source, 'teams': profiles, 'audit': dict(audit)}


def _profile(rating, season, clock):
    p = rating.get('rebounding')
    if not isinstance(p, dict):
        raise ValueError('Pregame rebounding inputs unavailable')
    if (p.get('policy') != INPUT_POLICY or p.get('season') != season
        or team_key(p.get('team', '')) != team_key(rating['team'])
        or p.get('source_url') != f'https://barttorvik.com/getgamestats.php?year={season}&csv=1'
        or not re.fullmatch(r'[a-f0-9]{64}', str(p.get('source_sha256', '')))):
        raise ValueError('Rebounding source or school identity is invalid')
    observed = timestamp(p['observed_at'])
    cutoff = date.fromisoformat(p['cutoff_date'])
    if (not 0 <= (clock-observed).total_seconds() <= 30*3600
        or cutoff != observed.astimezone(ET).date() or cutoff > clock.astimezone(ET).date()):
        raise ValueError('Rebounding inputs are stale or future-dated')
    n = p['d1_games']
    if type(n) is not int or n < 1:
        raise ValueError('No verified prior D1 rebound games')
    if not date.fromisoformat(p['last_game_date']) < cutoff:
        raise ValueError('Same-day or future rebounds cannot enter a forecast')
    for field in ('orb', 'drb', 'opp_orb', 'opp_drb'):
        value = p[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or int(value) != value:
            raise ValueError('Invalid captured rebound counts')
    a, b = p['orb']+p['opp_drb'], p['opp_orb']+p['drb']
    if a <= 0 or b <= 0:
        raise ValueError('No verified rebound opportunities')
    return {**deepcopy(p), 'orb_pct': p['orb']/a, 'allowed_pct': p['opp_orb']/b}


def matchup_transfer(away, home, season, as_of):
    """Frozen v0.11 rule: a capped point transfer attenuated by prior GP."""
    payload = {'policy': POLICY, 'status': 'unavailable', 'experimental': True,
        'points_per_edge_pp': POINTS_PER_EDGE_PP, 'max_transfer_points': MAX_TRANSFER_POINTS,
        'full_weight_games': FULL_WEIGHT_GAMES, 'away_points': 0., 'home_points': 0.,
        'sample_weight': 0., 'home_edge_pp': None, 'inputs': None}
    try:
        clock = timestamp(as_of)
        a, h = _profile(away, season, clock), _profile(home, season, clock)
        if any(a[k] != h[k] for k in ('source_sha256', 'observed_at', 'cutoff_date')):
            raise ValueError('Opposing rebound profiles must share one captured snapshot')
        away_score = (a['orb_pct']+h['allowed_pct'])/2
        home_score = (h['orb_pct']+a['allowed_pct'])/2
        edge = 100*(home_score-away_score)
        weight = min(1., min(a['d1_games'], h['d1_games'])/FULL_WEIGHT_GAMES)
        raw = POINTS_PER_EDGE_PP*edge
        capped = max(-MAX_TRANSFER_POINTS, min(MAX_TRANSFER_POINTS, raw))
        transfer = capped*weight
        payload.update(status='applied', inputs={'away': a, 'home': h},
            away_matchup_score=away_score, home_matchup_score=home_score,
            home_edge_pp=edge, sample_weight=weight, uncapped_home_points=raw,
            capped_home_points=capped, away_points=-transfer, home_points=transfer)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        payload['reason'] = str(exc)
    return payload


def percentage_matchup_transfer(away, home, season, as_of, *, away_points, home_points):
    """The favored side gains 0.75% of its baseline per edge percentage point.

    Transfer the identical scoring amount from the opponent. The curve has no
    edge tiers, fixed point cap or sample-size attenuation. Its only boundary
    is the opponent's available score, preserving nonnegative team scores.
    """
    payload = {'policy': PERCENTAGE_POLICY, 'status': 'unavailable', 'experimental': True,
        'boost_fraction_per_edge_pp': BOOST_FRACTION_PER_EDGE_PP,
        'away_points': 0., 'home_points': 0., 'sample_weight': 0.,
        'home_edge_pp': None, 'inputs': None, 'favored_side': None,
        'requested_boost_fraction': 0., 'requested_transfer_points': 0.,
        'applied_transfer_points': 0., 'score_floor_applied': False}
    try:
        clock = timestamp(as_of)
        a, h = _profile(away, season, clock), _profile(home, season, clock)
        if any(a[k] != h[k] for k in ('source_sha256', 'observed_at', 'cutoff_date')):
            raise ValueError('Opposing rebound profiles must share one captured snapshot')
        baseline = {'away': away_points, 'home': home_points}
        for side, value in baseline.items():
            if isinstance(value, bool):
                raise ValueError('Invalid baseline score for percentage rebounding')
            baseline[side] = float(value)
            if not math.isfinite(baseline[side]) or baseline[side] < 0:
                raise ValueError('Invalid baseline score for percentage rebounding')
        total = baseline['away']+baseline['home']
        if not math.isfinite(total) or total <= 0:
            raise ValueError('Percentage rebounding requires a positive game total')
        away_score = (a['orb_pct']+h['allowed_pct'])/2
        home_score = (h['orb_pct']+a['allowed_pct'])/2
        edge = 100*(home_score-away_score)
        favored = 'home' if edge > 0 else 'away' if edge < 0 else None
        boost = abs(edge)*BOOST_FRACTION_PER_EDGE_PP
        requested = applied = 0.
        floor = False
        if favored:
            other = 'away' if favored == 'home' else 'home'
            requested = baseline[favored]*boost
            applied = min(requested, baseline[other])
            floor = requested > baseline[other]
        home_delta = applied if favored == 'home' else -applied if favored == 'away' else 0.
        payload.update(status='applied', inputs={'away': a, 'home': h},
            away_matchup_score=away_score, home_matchup_score=home_score,
            home_edge_pp=edge, sample_weight=1., favored_side=favored,
            favored_baseline_points=baseline[favored] if favored else None,
            requested_boost_fraction=boost, requested_transfer_points=requested,
            applied_transfer_points=applied, score_floor_applied=floor,
            away_points=-home_delta, home_points=home_delta)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        payload['reason'] = str(exc)
    return payload
