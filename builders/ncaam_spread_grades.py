"""Saved-projection NCAAM spread grades; no scores or betting results as inputs."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from builders.ncaam_matchup_grades import number, policy_digest, select_benchmark, stamp

from builders.ncaam_selection_review import apply_spread_review, validate_review

POLICY_PATH = Path(__file__).resolve().parents[1]/'config/ncaam_spread_grades.json'


def load_spread_policy(path=POLICY_PATH):
    policy=json.loads(Path(path).read_text())
    validate_review(policy)
    low,high=(number(policy['edge_window'][k]) for k in ('minimum_inclusive','maximum_exclusive'))
    if not policy['version'].startswith('ncaam-spread-grades-') or not 0<low<high<=100:
        raise ValueError('Invalid spread policy or edge window.')
    expected={'defense-supported','three-factor-confluence'}
    if {p['id'] for p in policy['A_profiles']}!=expected or len(policy['A_profiles'])!=2:
        raise ValueError('Unknown or duplicate spread profile.')
    for profile in policy['A_profiles']:
        if not low<=number(profile['minimum_edge'])<high:
            raise ValueError('Spread profile edge must fall within the eligible window.')
    defense=next(p for p in policy['A_profiles'] if p['id']=='defense-supported')
    if number(defense['picked_defense_z_maximum'])>=0 or number(defense['maximum_opposing_matchup_tempo_z_exclusive'])<=0:
        raise ValueError('Invalid defensive strength or opposing tempo threshold.')
    if not low<=number(policy['B_profile']['minimum_edge'])<high:
        raise ValueError('Invalid B spread threshold.')
    confluence=next(p for p in policy['A_profiles'] if p['id']=='three-factor-confluence')
    if confluence.get('picked_offense_higher_than_opponent') is not True or confluence.get('picked_defense_lower_than_opponent') is not True:
        raise ValueError('Three-factor spread grades require both relative efficiency advantages.')
    if policy['B_profile'].get('require_any')!=['Picked offense higher than opponent','Picked defense lower than opponent'] or policy['B_profile'].get('exclude_A') is not True:
        raise ValueError('B spread grades require one relative efficiency advantage and exclude A.')
    if any(number(v)<=0 for v in policy['input_limits'].values()):
        raise ValueError('Spread input age limits must be positive.')
    return policy


def grade_saved_spread(forecast, benchmarks, policy=None):
    policy=load_spread_policy() if policy is None else deepcopy(policy)
    lock,tip=stamp(forecast['lockedAt']),stamp(forecast['tipoff'])
    market=forecast['market'];quote=stamp(market['as_of']);ratings=stamp(forecast['ratings_observed_at'])
    if not quote<=lock<tip or (lock-quote).total_seconds()>60*policy['input_limits']['max_market_age_minutes']:
        raise ValueError('Spread quote is future-dated, stale or post-tipoff.')
    if ratings>lock or (lock-ratings).total_seconds()>3600*policy['input_limits']['max_benchmark_age_hours']:
        raise ValueError('Spread ratings are future-dated or stale.')
    benchmark=select_benchmark(benchmarks,forecast['lockedAt'],int(forecast['home_court']['season']))
    if int(benchmark['team_count'])<300 or (lock-stamp(benchmark['observed_at'])).total_seconds()>3600*policy['input_limits']['max_benchmark_age_hours']:
        raise ValueError('Incomplete or stale national spread benchmark.')
    home_line,fair=number(market['home_spread']),number(forecast['fair_home_spread'])
    if abs(home_line)>100 or abs(fair)>150:
        raise ValueError('Implausible spread or fair line.')
    signed_edge=float(Decimal(str(home_line))-Decimal(str(fair)))
    edge=abs(signed_edge)
    location='home' if signed_edge>0 else 'away' if signed_edge<0 else None
    sign=1 if location=='home' else -1
    line=sign*home_line if location else 0.0
    projected_margin=-sign*fair if location else 0.0
    role='Favorite' if line<0 else 'Underdog' if line>0 else 'Pickem'
    z={}
    for loc in ('away','home'):
        z[loc]={}
        for metric in ('offense','defense','tempo'):
            value=number(forecast[loc+'_rating'][metric])
            if not (45<value<95 if metric=='tempo' else 60<value<150):
                raise ValueError('Spread team rating outside plausible bounds.')
            mean,sd=(number(benchmark['metrics'][metric][k]) for k in ('mean','sd'))
            if sd<=0:raise ValueError('Invalid spread population standard deviation.')
            z[loc][metric]=(value-mean)/sd
    picked=forecast[(location or 'home')+'_rating'];opponent=forecast[('away' if location=='home' else 'home')+'_rating']
    offense_adv=number(picked['offense'])-number(opponent['offense'])
    defense_adv=number(opponent['defense'])-number(picked['defense'])
    mean_tempo_z=(z['away']['tempo']+z['home']['tempo'])/2
    tempo_support=mean_tempo_z if role=='Favorite' else -mean_tempo_z if role=='Underdog' else 0
    defense_z=z[location or 'home']['defense']
    profiles=[];grade='Research';reason='No supporting relative offense/defense advantage'
    window=policy['edge_window']
    if not location or not window['minimum_inclusive']<=edge<window['maximum_exclusive']:
        reason=f"Projected cover edge is outside {window['minimum_inclusive']:g} to under {window['maximum_exclusive']:g} points"
    else:
        for profile in policy['A_profiles']:
            if edge<profile['minimum_edge']:continue
            if profile['id']=='defense-supported':
                if defense_z<=profile['picked_defense_z_maximum'] and -tempo_support<profile['maximum_opposing_matchup_tempo_z_exclusive']:
                    profiles.append(profile['id'])
            elif offense_adv>0 and defense_adv>0 and tempo_support>0:
                profiles.append(profile['id'])
        if profiles:
            grade='A';reason='Strong national defense with projection support' if profiles[0]=='defense-supported' else 'Offense, defense and tempo support the projected cover'
        elif edge>=policy['B_profile']['minimum_edge'] and (offense_adv>0 or defense_adv>0):
            grade='B';reason='Projected cover with a relative offense or defense advantage'
    result = {'policy_version':policy['version'],'policy_sha256':policy_digest(policy),'policy':policy,
        'grade':grade,'side':location if grade in ('A','B') else None,'projected_side':location,
        'selection_team':forecast[location+'_team'] if location else None,
        'line':line,'home_line':home_line,'fair_home_spread':fair,'projected_margin':projected_margin,
        'edge':edge,'role':role,'reason':reason,'profile_id':profiles[0] if profiles else None,'matched_profiles':profiles,
        'offense_advantage':offense_adv,'defense_advantage':defense_adv,'picked_defense_z':defense_z,
        'mean_tempo_z':mean_tempo_z,'tempo_support_z':tempo_support,'z':z,
        'as_of':forecast['lockedAt'],'tipoff':forecast['tipoff'],
        'benchmark_observed_at':benchmark['observed_at'],'benchmark_exact_rating_timestamp':stamp(benchmark['observed_at'])==ratings,
        'benchmark_age_hours':(lock-stamp(benchmark['observed_at'])).total_seconds()/3600,
        'national_benchmark':deepcopy(benchmark)}
    return apply_spread_review(result, policy)


def capture_spread_grade(forecast, benchmarks, *, mode='live', policy=None, published_at=None):
    published_at=published_at or datetime.now(timezone.utc).isoformat()
    if mode not in ('live','replay') or stamp(published_at)<stamp(forecast['lockedAt']):
        raise ValueError('Invalid spread capture publication mode or time.')
    if mode=='live' and stamp(published_at)>=stamp(forecast['tipoff']):
        raise ValueError('Live spread grade must be published before tipoff.')
    grade=grade_saved_spread(forecast,benchmarks,policy)
    return {**grade,'forecast_record_id':forecast['recordId'],'game_id':str(forecast['gameId']),
        'record_mode':mode,'published_at':published_at}


def unavailable_spread_grade(forecast, error, policy):
    return {'policy_version':policy['version'],'policy_sha256':policy_digest(policy),'policy':policy,
        'forecast_record_id':forecast['recordId'],'game_id':str(forecast['gameId']),
        'grade':'Research','side':None,'projected_side':None,'selection_team':None,
        'home_line':forecast['market']['home_spread'],'fair_home_spread':forecast['fair_home_spread'],
        'line':0,'edge':0,'projected_margin':0,'role':'Pickem',
        'as_of':forecast['lockedAt'],'tipoff':forecast['tipoff'],'record_mode':'live',
        'published_at':datetime.now(timezone.utc).isoformat(),'reason':'Spread grading inputs unavailable: '+str(error)}


def spread_storage_row(forecast, grade, day):
    return {'Date':day,'Game Key':f"{day}|ncaam|{forecast['gameId']}",'Game ID':str(forecast['gameId']),
        'Game':forecast['away_team']+' @ '+forecast['home_team'],'Market':'Spread',
        'Selection':forecast['recordId']+'|'+grade['policy_sha256'],'Updated At':grade['published_at'],
        'Details JSON':json.dumps(grade,sort_keys=True)}
