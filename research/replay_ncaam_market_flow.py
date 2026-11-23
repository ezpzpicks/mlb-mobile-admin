"""Chronological replay of the approved directional market-flow model.

Raw inputs are read-only public saved boards and independent ESPN scoreboards.
No production model, database, historical pick or scheduled task is mutated.
"""
from pathlib import Path
from datetime import timedelta
from collections import Counter, defaultdict
import hashlib
import json
import math
import csv
import sys
import os
import urllib.request
import concurrent.futures
REPO=Path(__file__).resolve().parents[1]
ROOT=Path(os.environ.get('NCAAM_MARKET_FLOW_WORKSPACE',str(REPO/'research/results/ncaam_market_flow')))
ROOT.mkdir(parents=True,exist_ok=True)
SOURCE=ROOT/'production' if (ROOT/'production/builders').exists() else REPO
sys.path.insert(0,str(SOURCE))
SPEC=REPO/'config/ncaam_market_flow_research.json'
if not SPEC.exists():SPEC=ROOT/'approved_specification.json'
from builders.ncaam_model import team_key
from builders.ncaam_matchup_projection import from_captured_inputs
from builders.ncaam_market_flow import flow_features,adjust_component,fit_nonnegative_ridge,stamp,number,combine_components

def read(p):return json.loads(p.read_text())
def write(name,obj):(ROOT/name).write_text(json.dumps(obj,indent=2)+'\n')
def benchmark(f):
 for key in ['matchup_projection','matchup_total','matchup_spread']:
  p=f.get(key)
  if isinstance(p,dict) and isinstance(p.get('national_benchmark'),dict):return p['national_benchmark']
 return None

def paired(s,market,rows):
 rows=[r for r in rows if r.get('Market')==market]
 if market=='Spread':
  a=[r for r in rows if team_key(r.get('Selection',''))==team_key(s['Home Team'])]
  b=[r for r in rows if team_key(r.get('Selection',''))==team_key(s['Away Team'])]
 else:
  a=[r for r in rows if (r.get('Side') or r.get('Selection','')).lower()=='over']
  b=[r for r in rows if (r.get('Side') or r.get('Selection','')).lower()=='under']
 if len(a)!=1 or len(b)!=1:raise ValueError('missing paired sides')
 a,b=a[0],b[0]
 for r in [a,b]:
  if r.get('Snapshot Status')!='FINAL_PREGAME':raise ValueError('not a frozen pregame market')
  if team_key(r['Home Team'])!=team_key(s['Home Team']) or team_key(r['Away Team'])!=team_key(s['Away Team']):raise ValueError('team orientation mismatch')
  if abs((stamp(r['Game Time'])-stamp(s['Game Time'])).total_seconds())>60:raise ValueError('tipoff mismatch')
 def hist(r):
  out={}
  for p in json.loads(r.get('Details JSON') or '{}').get('movementHistory',[]):
   t=stamp(p['snapshotTime']);v={k:number(p[k]) for k in ['line','moneyPct','betsPct']}
   if t in out and out[t]!=v:raise ValueError('conflicting market observation')
   out[t]=v
  return out
 ah,bh=hist(a),hist(b)
 frozen=min(stamp(a['Frozen At']),stamp(b['Frozen At']))
 tip=stamp(s['Game Time']);out=[]
 for t in sorted(ah.keys()&bh.keys()):
  if t>frozen or t>=tip:continue
  x,y=ah[t],bh[t]
  diff=x['line']+y['line'] if market=='Spread' else x['line']-y['line']
  if abs(diff)>1e-7 or abs(x['moneyPct']+y['moneyPct']-100)>1.01 or abs(x['betsPct']+y['betsPct']-100)>1.01:raise ValueError('inconsistent paired market')
  out.append({'snapshotTime':t.isoformat(),**x})
 if not out or abs((stamp(out[-1]['snapshotTime'])-frozen).total_seconds())>1:raise ValueError('missing exact frozen pair')
 if not 0<(tip-frozen).total_seconds()/60<=90:raise ValueError('not within 90 minutes of tipoff')
 return out,frozen

def build():
 pool={};conflicts=set();source_manifest=[]
 files=sorted((ROOT/'raw').glob('2026-11-*.json'))
 for path in files:
  b=path.read_bytes();source_manifest.append({'path':'raw/'+path.name,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
  m=json.loads(b)['ncaamMarket']
  for f in m['forecasts']:
   bench=benchmark(f)
   if not bench:continue
   key=stamp(bench['observed_at']);shape={k:bench[k] for k in ['metrics','team_count','season']}
   if key in pool and {k:pool[key][k] for k in shape}!=shape:conflicts.add(key)
   else:pool[key]=bench
 for k in conflicts:pool.pop(k,None)
 exclusions=Counter();rows=[];verified_games=set();eligible=set();baseline_diffs=[]
 for path in files:
  m=read(path)['ncaamMarket'];day=m['date'];espn=read(ROOT/'raw'/('espn_'+day+'.json'));finals={}
  for e in espn['events']:
   c=e['competitions'][0]
   if c.get('status',{}).get('type',{}).get('completed'):finals[str(e['id'])]={x['homeAway']:x for x in c['competitors']}
  grouped=defaultdict(list);forecasts=defaultdict(list)
  for r in m['splits']:grouped[str(r['Game ID'])].append(r)
  for f in m['forecasts']:forecasts[str(f['gameId'])].append(f)
  for s in m['games']:
   gid=str(s['Game ID']);key=(day,gid)
   if s.get('Completed')!='TRUE':continue
   final=finals.get(gid)
   if not final or any(str(final[k]['team']['id'])!=str(s[k.title()+' Team ID']) or float(final[k]['score'])!=number(s[k.title()+' Score']) for k in ['home','away']):
    exclusions['independent final missing or disagrees']+=1;continue
   verified_games.add(key)
   for market in ['Spread','Total']:
    try:
     history,decision=paired(s,market,grouped[gid])
     flow=flow_features(history,as_of=decision.isoformat(),tipoff=s['Game Time'],market=market)
     if flow['status']=='insufficient_history':raise ValueError('insufficient timed movement history')
     possible=sorted([f for f in forecasts[gid] if stamp(f['lockedAt'])<=decision and abs((stamp(f['tipoff'])-stamp(s['Game Time'])).total_seconds())<=60],
                     key=lambda f:(stamp(f['lockedAt']),str(f['recordId'])),reverse=True)
     p=None;chosen=None
     for f in possible:
      bench=benchmark(f) or pool.get(stamp(f.get('ratings_observed_at','1900-01-01T00:00:00Z')))
      if not bench:continue
      if stamp(bench['observed_at'])>stamp(f['lockedAt']) or (decision-stamp(bench['observed_at'])).total_seconds()>30*3600:continue
      direct=team_key(f['home_team'])==team_key(s['Home Team']) and team_key(f['away_team'])==team_key(s['Away Team'])
      reverse=team_key(f['home_team'])==team_key(s['Away Team']) and team_key(f['away_team'])==team_key(s['Home Team'])
      if not direct and not(reverse and f.get('neutral_site') is True):continue
      try:
       p=from_captured_inputs(f['away_rating'],f['home_rating'],bench,as_of=f['lockedAt'],tipoff=f['tipoff'],
           source_url=f.get('ratings_source_url','Captured Torvik export'),home_court_points=f['home_court_points'],
           neutral_site=f['neutral_site'],lineup_adjustment=f.get('lineup_adjustment'))
       if reverse:p['margin']=-p['margin']
       chosen=f;break
      except (KeyError,TypeError,ValueError):continue
     if p is None:raise ValueError('no reconstructible captured baseline benchmark before decision')
     assert stamp(bench['observed_at'])<=stamp(chosen['lockedAt'])<=decision<stamp(s['Game Time'])
     if chosen.get('modelVersion')=='ncaam-v0.16-calibrated-b-spreads-2026-11-22':
      old=chosen['matchup_projection'];baseline_diffs.append(max(abs(old['margin']-p['margin']),abs(old['total']-p['total'])))
     hs,aws=number(s['Home Score']),number(s['Away Score']);quote=history[-1]['line']
     row={'date':day,'gameId':gid,'game':s['Game'],'market':market,'homeTeam':s['Home Team'],'awayTeam':s['Away Team'],
          'tipoff':s['Game Time'],'decision':decision.isoformat(),'baselineRecordId':chosen['recordId'],
          'baselineCapturedAt':chosen['lockedAt'],'ratingsObservedAt':bench['observed_at'],'homeScore':hs,'awayScore':aws,
          'baselineMargin':p['margin'],'baselineTotal':p['total'],'baseline':p['margin'] if market=='Spread' else p['total'],
          'actual':hs-aws if market=='Spread' else hs+aws,'quote':quote,'marketValue':-quote if market=='Spread' else quote,
          'features':flow['features'],'flow':flow,'history':history}
     rows.append(row);eligible.add(key)
    except (KeyError,ValueError,TypeError) as e:exclusions[str(e)]+=1
 assert len(rows)==len(set((r['date'],r['gameId'],r['market']) for r in rows))
 assert not baseline_diffs or max(baseline_diffs)<1e-8
 write('observations.json',rows)
 write('coverage.json',{'independently_verified_completed_games':len(verified_games),'eligible_unique_games':len(eligible),
       'eligible_market_observations':len(rows),'by_date_market':dict(Counter(r['date']+'|'+r['market'] for r in rows)),
       'active_net_money_change':dict(Counter(r['market'] for r in rows if r['flow']['direction'])),
       'exclusions':dict(exclusions),'exact_native_v16_checks':len(baseline_diffs),'maximum_native_v16_difference':max(baseline_diffs,default=None),
       'benchmark_pool_observations':len(pool),'conflicting_benchmarks_excluded':len(conflicts),'source_manifest':source_manifest})
 return rows

def predict(r,projection):
 gap=projection-r['marketValue'];edge=abs(gap);qual=5<=edge<7 if r['market']=='Spread' else edge>=8
 side=('home' if gap>0 else 'away') if r['market']=='Spread' else ('Over' if gap>0 else 'Under')
 cover=(r['actual']-r['marketValue'])*(1 if gap>0 else -1);result='W' if cover>1e-8 else 'L' if cover< -1e-8 else 'P'
 profit=10/11 if result=='W' else -1 if result=='L' else 0
 pick=(r[side+'Team']+' '+format(r['quote']*(1 if side=='home' else -1),'+g')) if r['market']=='Spread' else side+' '+format(r['quote'],'g')
 return {'projection':projection,'adjustment':projection-r['baseline'],'edge':edge,'selected':qual,'side':side,'pick':pick,'result':result,'profit':profit if qual else 0.0}

def stats(rows):
 bets=[r for r in rows if r['selected']];w=sum(r['result']=='W' for r in bets);l=sum(r['result']=='L' for r in bets);p=len(bets)-w-l;units=sum(r['profit'] for r in bets)
 assert abs(units-(w*10/11-l))<1e-7
 return {'opportunities':len(rows),'bets':len(bets),'record':str(w)+'-'+str(l)+'-'+str(p),'wins':w,'losses':l,'pushes':p,'netUnits':units,'roiPct':100*units/len(bets) if bets else None,
         'meanAbsoluteAdjustment':sum(abs(r['adjustment']) for r in rows)/len(rows) if rows else None}

def evaluate(rows):
 predictions=[];fits=[];warmup=[]
 for market in ['Spread','Total']:
  available=[r for r in rows if r['market']==market]
  for day in sorted(set(r['date'] for r in available)):
   test=[r for r in available if r['date']==day];cutoff=min(stamp(r['decision']) for r in test)
   train=[r for r in available if r['date']<day and stamp(r['tipoff'])+timedelta(hours=6)<=cutoff]
   if len(train)<150:warmup.append({'date':day,'market':market,'trainingGames':len(train),'skippedGames':len(test)});continue
   assert all(stamp(r['decision'])<cutoff for r in train)
   fitted=fit_nonnegative_ridge(train,alpha=100.0)
   fit={**fitted,'date':day,'market':market,'trainingThrough':max(r['date'] for r in train),'evaluationStarts':cutoff.isoformat()};fits.append(fit)
   for r in test:
    for variant in ['baseline','directional']:
     value=r['baseline'] if variant=='baseline' else adjust_component(r['baseline'],r['flow'],fit['coefficients'])['projection']
     pred={k:v for k,v in r.items() if k not in ['flow','history']};pred.update(variant=variant,trainingGames=len(train),**predict(r,value))
     if variant=='directional':assert r['flow']['direction']*pred['adjustment']>=-1e-8
     predictions.append(pred)
 write('predictions.json',predictions);write('daily_fits.json',fits)
 periods={'historical':lambda r:r['date']<='2026-11-21','additional_day':lambda r:r['date']=='2026-11-22','all':lambda r:True}
 out={'specificationSha256':hashlib.sha256(SPEC.read_bytes()).hexdigest(),
      'evaluationDates':sorted(set(r['date'] for r in predictions)),'results':{},'diagnostics':{},'warmup':warmup}
 for name,condition in periods.items():
  out['results'][name]={}
  for market in ['Spread','Total']:
   selected=[r for r in predictions if r['market']==market and condition(r)]
   out['results'][name][market]={v:stats([r for r in selected if r['variant']==v]) for v in ['baseline','directional']}
   if market=='Spread':out['results'][name]['SpreadBelow20']={v:stats([r for r in selected if r['variant']==v and abs(r['quote'])<20]) for v in ['baseline','directional']}
 for market in ['Spread','Total']:
  selected=[r for r in predictions if r['market']==market and r['date']<='2026-11-21']
  dates=sorted(set(r['date'] for r in selected));half=dates[len(dates)//2] if dates else ''
  out['diagnostics'][market]={'daily':{d:{v:stats([r for r in selected if r['variant']==v and r['date']==d]) for v in ['baseline','directional']} for d in dates},
    'blocks':{label:{v:stats([r for r in selected if r['variant']==v and (r['date']<half if label=='earlier' else r['date']>=half)]) for v in ['baseline','directional']} for label in ['earlier','later']}}
 slim=[{k:v for k,v in r.items() if not isinstance(v,(list,dict))} for r in predictions]
 if slim:
  with (ROOT/'predictions.csv').open('w',newline='') as f:
   writer=csv.DictWriter(f,fieldnames=list(slim[0]));writer.writeheader();writer.writerows(slim)
  with (ROOT/'bets.csv').open('w',newline='') as f:
   writer=csv.DictWriter(f,fieldnames=list(slim[0]));writer.writeheader();writer.writerows(r for r in slim if r['selected'])
 examples=[]
 for gid in ['401909742','401911020','401911251','401921756','401909494']:
  for r in rows:
   if r['gameId']!=gid:continue
   relevant='Total' if gid in ['401909742','401909494'] else 'Spread'
   if r['market']!=relevant:continue
   pred=[p for p in predictions if p['gameId']==gid and p['market']==relevant]
   if pred:
    examples.append({'game':r['game'],'date':r['date'],'market':relevant,'opening':r['history'][0],'final':r['history'][-1],
      'moneyShareDirection':r['flow']['direction'],'baseline':r['baseline'],'actual':r['actual'],'quote':r['quote'],
      'predictions':[{k:p[k] for k in ['variant','projection','adjustment','selected','pick','result']} for p in pred]})
 write('examples.json',examples)
 for market in ['Spread','Total']:
  train=[r for r in rows if r['market']==market and r['date']<='2026-11-21']
  if len(train)>=150:write('frozen_'+market.lower()+'_weights_through_2026-11-21.json',{**fit_nonnegative_ridge(train),'status':'research_only_not_live','trainingThrough':'2026-11-21'})
 write('results.json',out)
 # Every counterpart has an identical saved quote and timestamp.
 indexed=defaultdict(dict)
 for p in predictions:indexed[(p['date'],p['gameId'],p['market'])][p['variant']]=p
 for pair in indexed.values():
  assert set(pair)=={'baseline','directional'}
  assert pair['baseline']['quote']==pair['directional']['quote'] and pair['baseline']['decision']==pair['directional']['decision']
 validate={'matchedMarketComparisons':len(indexed),'predictionRows':len(predictions),'sourceOutcomesIndependentlyMatched':True,
  'nonnegativeWeights':all(all(v>=0 for v in f['coefficients']) for f in fits),'directionViolations':0,
  'sameQuotesAndDecisions':True,'earlierDateTrainingOnly':True,'productionWrites':False}
 write('validation.json',validate)
 print(json.dumps({'coverage':{k:v for k,v in read(ROOT/'coverage.json').items() if k not in ['source_manifest','by_date_market']},
   'evaluationDates':out['evaluationDates'],'results':out['results'],'validation':validate,'examples':examples},indent=2))


def fetch_public_inputs():
 """Read saved snapshots only; these GETs do not run the builder or place picks."""
 (ROOT/'raw').mkdir(parents=True,exist_ok=True)
 def one(day):
  targets=[(day+'.json','https://ezpzpicks.com/api/public-data-v2?sport=NCAAM&compact=1&date='+day),
    ('espn_'+day+'.json','https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates='+day.replace('-','')+'&groups=50&limit=1000')]
  for name,url in targets:
   target=ROOT/'raw'/name
   if target.exists():continue
   with urllib.request.urlopen(url,timeout=60) as response:payload=response.read()
   json.loads(payload);target.write_bytes(payload)
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  list(pool.map(one,['2026-11-'+str(d).zfill(2) for d in range(8,23)]))

def main():
 if '--fetch' in sys.argv:fetch_public_inputs()
 rows=build();evaluate(rows)
if __name__=='__main__':main()
