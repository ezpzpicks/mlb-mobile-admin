"""Captured point-edge grades, independent of results and factor bonuses."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from builders.ncaam_matchup_grades import number, policy_digest, select_benchmark, stamp
from builders.ncaam_matchup_projection import MODEL_VERSION, POINT_MODEL_VERSIONS, GRADE_POLICY

POLICY_PATH = Path(__file__).resolve().parents[1]/'config/ncaam_point_edge_grades.json'


def load_policy(path=POLICY_PATH):
    policy=json.loads(Path(path).read_text());t=policy['thresholds']
    if policy['version']!=GRADE_POLICY or policy.get('grading_basis')!='market_point_edge':
        raise ValueError('Unknown point-edge policy.')
    for market in ('spread','total'):
        if not 0<number(t[market+'_b_points'])<number(t[market+'_a_points']):
            raise ValueError('Invalid point-edge cutoffs.')
    if not 0<number(t['spread_max_market_line'])<=20:
        raise ValueError('Invalid projection-only spread limit.')
    return policy


def capture_grades(f, benchmarks, *, mode='live', policy=None, published_at=None):
    policy=deepcopy(policy or load_policy());t=policy['thresholds']
    if policy.get('version')!=GRADE_POLICY or policy.get('grading_basis')!='market_point_edge':
        raise ValueError('The point model requires its point-edge grading policy.')
    lock,tip=map(stamp,(f['lockedAt'],f['tipoff']))
    published_at=published_at or datetime.now(timezone.utc).isoformat()
    published=stamp(published_at)
    if mode not in ('live','replay') or published<lock or (mode=='live' and published>=tip) or not lock<tip:
        raise ValueError('Grade publication must respect its live/replay clock.')
    p=f['matchup_projection']
    model=f['modelVersion']
    if (model not in POINT_MODEL_VERSIONS or f.get('projection_engine')!=model
        or p['model_version']!=model or p['sha256']!=policy_digest({k:v for k,v in p.items() if k!='sha256'})
        or stamp(p['as_of'])!=lock or stamp(p['tipoff'])!=tip
        or abs(number(f['fair_home_spread'])+number(p['margin']))>1e-8
        or abs(number(f['total'])-number(p['total']))>1e-8):
        raise ValueError('Grades require the matching saved matchup-led score proof.')
    if (f['ratings_source_url']!=p['ratings_source_url']
        or any(any(f[side+'_rating'][k]!=p['team_inputs'][side][k] for k in ('team','offense','defense','tempo'))
               for side in ('away','home'))):
        raise ValueError('Forecast ratings do not match the scoring inputs.')
    benchmark=select_benchmark(benchmarks,f['lockedAt'],p['season'])
    if (policy_digest(benchmark)!=p['benchmark_sha256'] or benchmark!=p['national_benchmark']
        or stamp(benchmark['observed_at'])!=stamp(f['ratings_observed_at'])
        or (lock-stamp(benchmark['observed_at'])).total_seconds()>3600*t['max_benchmark_age_hours']):
        raise ValueError('The grade benchmark must match the captured score inputs.')
    away_tempo,home_tempo=(number(f[s+'_rating']['tempo']) for s in ('away','home'))
    national_tempo=number(benchmark['metrics']['tempo']['mean'])
    pace=away_tempo*home_tempo/national_tempo if model==MODEL_VERSION else (away_tempo+home_tempo)/2
    if not 45<pace<95 or not math.isclose(number(p['possessions']),pace,abs_tol=1e-8,rel_tol=0):
        raise ValueError('Saved pace does not match its projection model.')
    if model==MODEL_VERSION:
        tempo=p.get('tempo_model',{})
        if (tempo.get('method')!='adjusted-tempo-ratios-v1' or tempo.get('national_tempo')!=national_tempo
            or not math.isclose(number(tempo.get('away_ratio')),away_tempo/national_tempo,abs_tol=1e-8,rel_tol=0)
            or not math.isclose(number(tempo.get('home_ratio')),home_tempo/national_tempo,abs_tol=1e-8,rel_tol=0)):
            raise ValueError('Saved adjusted-tempo proof is unavailable.')
    market=f['market'];quote=stamp(market['as_of'])
    if not quote<=lock or (lock-quote).total_seconds()>60*t['max_market_age_minutes']:
        raise ValueError('Quote is stale or was unavailable before capture.')
    z={side:{metric:(number(f[side+'_rating'][metric])-number(benchmark['metrics'][metric]['mean']))
        /number(benchmark['metrics'][metric]['sd']) for metric in ('offense','defense','tempo')}
        for side in ('away','home')}
    flags=['Provisional point model and edge thresholds','Cover probabilities are not calibrated']
    if f.get('home_court',{}).get('fallback'):flags.append('Home court uses the league prior')
    if f.get('injury_verified') is not True:flags.append('Player availability unchecked')
    if f.get('starters_confirmed') is not True:flags.append('Starting fives unconfirmed')
    odds=number(market.get('odds',-110))
    price_ok=abs(odds)>=100 and (100/(odds+100) if odds>0 else -odds/(100-odds))<=110/210+1e-12
    output={}
    for name,gap in (('spread',number(p['margin'])+number(market['home_spread'])),('total',number(p['total'])-number(market['total']))):
        edge=abs(gap);tier='A' if edge>=t[name+'_a_points'] else 'B' if edge>=t[name+'_b_points'] else 'Research'
        side=('home' if gap>0 else 'away' if gap<0 else None) if name=='spread' else ('Over' if gap>0 else 'Under' if gap<0 else None)
        large=name=='spread' and abs(number(market['home_spread']))>=t['spread_max_market_line']
        grade=tier if price_ok and not large else 'Research'
        reason=f"{edge:.2f}-point edge; A >= {t[name+'_a_points']:g}, B >= {t[name+'_b_points']:g}"
        if large:reason='20+ point spreads remain projection-only'
        elif not price_ok:reason='This quote needs separate probability evidence at its price'
        common={'policy_version':policy['version'],'policy_sha256':policy_digest(policy),'policy':deepcopy(policy),
            'grading_basis':'market_point_edge','grade':grade,'edge_grade':tier,'eligible':grade in ('A','B'),
            'side':side if grade in ('A','B') else None,'projected_side':side,'edge':edge,'reason':reason,
            'confidence_flags':[*flags,*(['20+ point market spread; projection-only'] if large else [])],
            'forecast_record_id':f['recordId'],'game_id':str(f['gameId']),'as_of':f['lockedAt'],'tipoff':f['tipoff'],
            'published_at':published_at,'record_mode':mode,'projection_model_version':model,'projection_sha256':p['sha256'],
            'thresholds':deepcopy(t),'national_benchmark':deepcopy(benchmark),'benchmark_observed_at':benchmark['observed_at'],
            'benchmark_exact_rating_timestamp':True,'z':z}
        if name=='spread':
            sign=1 if side=='home' else -1;line=sign*number(market['home_spread'])
            picked=side or 'home';other='away' if picked=='home' else 'home'
            common.update(home_line=number(market['home_spread']),line=line,fair_home_spread=-number(p['margin']),
                projected_margin=sign*number(p['margin']),selection_team=f[picked+'_team'],
                role='Favorite' if line<0 else 'Underdog' if line>0 else 'Pickem',
                offense_advantage=number(f[picked+'_rating']['offense'])-number(f[other+'_rating']['offense']),
                defense_advantage=number(f[other+'_rating']['defense'])-number(f[picked+'_rating']['defense']),
                picked_defense_z=z[picked]['defense'],mean_tempo_z=(z['away']['tempo']+z['home']['tempo'])/2,
                tempo_support_z=0.)
        else:
            common.update(line=number(market['total']),projected_total=number(p['total']),
                directions={s:{'grade':grade if s==side else 'Research','base_grade':tier if s==side else 'Research',
                    'profile_id':None,'profile_label':'Projected point edge','matched_profiles':[],'conflicts':[]} for s in ('Over','Under')})
        output[name]=common
    return output
