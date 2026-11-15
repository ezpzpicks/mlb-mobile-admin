"""Direct offense/opposing-defense/tempo scores in basketball's own units.

This is EZPZ's provisional additive construction, not Torvik's proprietary
projection. The existing projector remains available for historical replay.
"""
from copy import deepcopy
import math

from builders.ncaam_model import project as legacy_project, team_key
from builders.ncaam_matchup_grades import number, policy_digest, population_benchmark, stamp

LEGACY_POINT_MODEL_VERSION = "ncaam-v0.7-matchup-projection-2026-11-14"
ADJUSTED_TEMPO_MODEL_VERSION = "ncaam-v0.8-adjusted-tempo-2026-11-15"
MODEL_VERSION = "ncaam-v0.9-progressive-offense-2026-11-15"
POINT_MODEL_VERSIONS = (LEGACY_POINT_MODEL_VERSION, ADJUSTED_TEMPO_MODEL_VERSION, MODEL_VERSION)
GRADE_POLICY = "ncaam-point-edge-2026-11-14-v1"
OFFENSE_BONUS_POLICY = "ncaam-very-strong-offense-progressive-2026-11-15-v1"
VERY_STRONG_OFFENSE_Z = 1.5
OFFENSE_BONUS_POINTS = {'very_strong': 1., 'strong': 3., 'above_average': 5.,
                        'below_average': 8., 'weak': 9., 'very_weak': 10.}


def progressive_offense_bonus(offense_z, opponent_defense_z):
    """User-selected trial points; lower adjusted defense means better defense.

    Apply only to a very strong offense. Weak offenses receive no extra
    deduction. These are final score points, not efficiency or pace changes.
    """
    offense_z, defense_z = number(offense_z), number(opponent_defense_z)
    band = ('very_strong' if defense_z <= -1.5 else 'strong' if defense_z <= -1.
            else 'above_average' if defense_z < 0. else 'below_average' if defense_z < 1.
            else 'weak' if defense_z < 1.5 else 'very_weak')
    qualified = offense_z >= VERY_STRONG_OFFENSE_Z
    return {'qualified': qualified, 'offense_z': offense_z,
            'opponent_defense_z': defense_z, 'opponent_defense_band': band,
            'points': OFFENSE_BONUS_POINTS[band] if qualified else 0.}


def from_captured_inputs(away, home, benchmark, *, as_of, tipoff, source_url,
                         home_court_points, neutral_site, lineup_adjustment=None,
                         model_version=MODEL_VERSION):
    if model_version not in POINT_MODEL_VERSIONS:
        raise ValueError('Unknown captured point model.')
    observed, clock, start = map(stamp, (benchmark['observed_at'], as_of, tipoff))
    season = int(benchmark['season'])
    if (not observed <= clock < start or (clock-observed).total_seconds() > 30*3600
        or int(benchmark['team_count']) < 300
        or source_url != f"https://barttorvik.com/{season}_team_results.csv"):
        raise ValueError('A fresh, captured Division I Torvik benchmark is required.')
    if type(neutral_site) is not bool:
        raise ValueError('Venue must be verified.')
    court = number(home_court_points)
    if not 0 <= court <= 8 or (neutral_site and court != 0):
        raise ValueError('Invalid home-court adjustment.')
    metrics = benchmark['metrics']
    for metric in ('offense', 'defense', 'tempo'):
        if number(metrics[metric]['sd']) <= 0:
            raise ValueError('Invalid national benchmark variability.')
        value = number(metrics[metric]['mean'])
        if not (45 < value < 95 if metric == 'tempo' else 60 < value < 150):
            raise ValueError('Invalid national benchmark scale.')
    ratings = {'away': away, 'home': home}
    if not team_key(away.get('team','')) or team_key(away['team']) == team_key(home.get('team','')):
        raise ValueError('Two distinct rated teams are required.')
    for rating in ratings.values():
        for metric in ('offense', 'defense', 'tempo'):
            value = number(rating[metric])
            if not (45 < value < 95 if metric == 'tempo' else 60 < value < 150):
                raise ValueError('Team rating outside its basketball scale.')
    center = (number(metrics['offense']['mean'])+number(metrics['defense']['mean']))/2
    national_tempo = number(metrics['tempo']['mean'])
    # Adjusted tempo describes a team's pace against an average opponent.
    # Combine both relative pace effects; averaging halves their departures
    # from the national baseline. Keep v0.7 reproducible for saved captures.
    possessions = ((number(away['tempo'])+number(home['tempo']))/2
        if model_version == LEGACY_POINT_MODEL_VERSION else
        number(away['tempo'])*number(home['tempo'])/national_tempo)
    if not 45 < possessions < 95:
        raise ValueError('Combined matchup pace outside the supported range.')
    pairings, points, adjustments = {}, {}, {}
    impact = lineup_adjustment or {}
    for side, opponent in (('away','home'), ('home','away')):
        offense = number(ratings[side]['offense'])-number(metrics['offense']['mean'])
        defense = number(ratings[opponent]['defense'])-number(metrics['defense']['mean'])
        efficiency = center+offense+defense
        offense_change = number(impact.get(side,{}).get('offense_change_per_100',0))
        defense_change = -number(impact.get(opponent,{}).get('defense_improvement_per_100',0))
        player_points = possessions*(offense_change+defense_change)/100
        venue_points = court/2*(1 if side == 'home' else -1)
        baseline = possessions*efficiency/100
        bonus = progressive_offense_bonus(offense/number(metrics['offense']['sd']),
            defense/number(metrics['defense']['sd'])) if model_version == MODEL_VERSION else None
        points[side] = baseline+player_points+venue_points+(bonus['points'] if bonus else 0.)
        if not 0 < points[side] < 200:
            raise ValueError('Implausible expected team score.')
        pairings[side] = {'baseline_efficiency':center, 'offense_deviation':offense,
            'opponent_defense_deviation':defense, 'expected_efficiency':efficiency,
            'neutral_points':baseline}
        adjustments[side] = {'player_offense_per_100':offense_change,
            'opponent_defense_per_100':defense_change, 'player_points':player_points,
            'home_court_points':venue_points}
        if bonus is not None:
            adjustments[side]['offense_bonus'] = bonus
    payload = {'model_version':model_version, 'status':'provisional', 'as_of':clock.isoformat(),
        'tipoff':start.isoformat(), 'season':season, 'ratings_source_url':source_url,
        'ratings_observed_at':observed.isoformat(), 'benchmark_sha256':policy_digest(benchmark),
        'national_benchmark':deepcopy(benchmark), 'team_inputs':{side:{k:ratings[side][k]
            for k in ('team','offense','defense','tempo')} for side in ('away','home')},
        'possessions':possessions, 'pairings':pairings, 'adjustments':adjustments,
        'away_points':points['away'], 'home_points':points['home'],
        'margin':points['home']-points['away'], 'total':points['home']+points['away'],
        'calibrated_probabilities':False}
    if model_version != LEGACY_POINT_MODEL_VERSION:
        payload['tempo_model'] = {'method':'adjusted-tempo-ratios-v1',
            'national_tempo':national_tempo,
            'away_ratio':number(away['tempo'])/national_tempo,
            'home_ratio':number(home['tempo'])/national_tempo}
    if model_version == MODEL_VERSION:
        payload['offense_bonus_policy'] = {'version': OFFENSE_BONUS_POLICY, 'status': 'experimental',
            'qualifying_offense_z': VERY_STRONG_OFFENSE_Z,
            'points_by_opponent_defense': deepcopy(OFFENSE_BONUS_POINTS)}
    payload['sha256'] = policy_digest(payload)
    return payload


def project(game, bundle, market, now, *, lineup_adjustment=None, home_court=None):
    # Reuse the established venue, source-clock, alias and availability checks.
    # Its previous scores are comparison metadata, not terms in the new score.
    legacy = legacy_project(game,bundle,market,now,lineup_adjustment=lineup_adjustment,home_court=home_court)
    benchmark = population_benchmark(bundle)
    p = from_captured_inputs(legacy['away_rating'],legacy['home_rating'],benchmark,
        as_of=now,tipoff=game['tipoff'],source_url=bundle['source_url'],
        home_court_points=legacy['home_court_points'],neutral_site=game['neutral_site'],
        lineup_adjustment=lineup_adjustment)
    result = deepcopy(legacy)
    result['legacy_forecast'] = {k:legacy[k] for k in
        ('away_score','home_score','fair_home_spread','total','margin_calibration')}
    margin, total = p['margin'], p['total']
    spread_gap, total_gap = margin+number(market['home_spread']), total-number(market['total'])
    signed = lambda n:f'{n:+g}' if n else '0'
    result.update(away_score=round(p['away_points'],1),home_score=round(p['home_points'],1),
        possessions=p['possessions'],fair_home_spread=-margin,total=total,
        home_spread_edge=spread_gap,total_edge=total_gap,
        spread_pick=f"{game['home']} {signed(market['home_spread'])}" if spread_gap>=0 else f"{game['away']} {signed(-market['home_spread'])}",
        total_pick=f"{'Over' if total_gap>=0 else 'Under'} {market['total']:g}",
        spread_grade='Research',total_grade='Research',grade_policy=GRADE_POLICY,
        projection_engine=MODEL_VERSION,matchup_projection=p,
        baseline_away_score=p['away_points']-p['adjustments']['away']['player_points'],
        baseline_home_score=p['home_points']-p['adjustments']['home']['player_points'],
        lineup_margin_change=p['adjustments']['home']['player_points']-p['adjustments']['away']['player_points'],
        lineup_total_change=p['adjustments']['home']['player_points']+p['adjustments']['away']['player_points'],
        margin_calibration={'policy':'direct-matchup-points-v1','active':False,'neutral_margin_scale':1.,
            'home_court_points':legacy['home_court_points'],'calibrated_home_margin':margin,
            'reason':'Direct matchup scoring plus the captured home-court adjustment'},
        model_status='Provisional matchup-led scores; point-edge grades and input confidence are separate')
    return result
