"""Replay the v3 NCAAM updater against the restored, fixed market-study inputs.

Inputs are the user's original NCAAM_Market_Adjustment_Data_2026-11-22.zip,
extracted without changing its internal paths. No model/database writes occur.
"""
from pathlib import Path
from datetime import timedelta
from collections import defaultdict, Counter
import argparse
import csv
import hashlib
import itertools
import json
import sys
from zoneinfo import ZoneInfo

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from builders.ncaam_model import team_key
from builders.ncaam_matchup_projection import from_captured_inputs
from builders.ncaam_market_flow import (
    POLICY, PROFILES, FEATURE_NAMES, flow_features, adjustment, MarketProjectionState, stamp, number)

read=lambda p:json.loads(p.read_text())

def dump(root,name,value):
    (root/name).write_text(json.dumps(value,indent=2)+'\n')

def pair_history(r,split_rows):
    rows=[x for x in split_rows if str(x['Game ID'])==r['gameId'] and x['Market']==r['market']]
    if r['market']=='Spread':
        primary=[x for x in rows if team_key(x['Selection'])==team_key(r['homeTeam'])]
        other=[x for x in rows if team_key(x['Selection'])==team_key(r['awayTeam'])]
    else:
        primary=[x for x in rows if (x.get('Side') or x['Selection']).lower()=='over']
        other=[x for x in rows if (x.get('Side') or x['Selection']).lower()=='under']
    if len(primary)!=1 or len(other)!=1:
        raise ValueError('Saved paired history no longer matches the fixed observation.')
    def decode(x):
        return {stamp(p['snapshotTime']):p for p in json.loads(x['Details JSON'])['movementHistory']}
    a,b=decode(primary[0]),decode(other[0]);end=stamp(r['decision']);tip=stamp(r['tipoff'])
    history=[]
    for t in sorted(a.keys()&b.keys()):
        if t>end or t>=tip:continue
        x,y=a[t],b[t]
        diff=number(x['line'])+number(y['line']) if r['market']=='Spread' else number(x['line'])-number(y['line'])
        assert abs(diff)<1e-7
        assert abs(number(x['betsPct'])+number(y['betsPct'])-100)<=1.01
        assert abs(number(x['moneyPct'])+number(y['moneyPct'])-100)<=1.01
        history.append({k:x[k] for k in ['snapshotTime','line','betsPct','moneyPct']})
    assert history and stamp(history[-1]['snapshotTime'])==end
    assert abs(number(history[-1]['line'])-r['quote'])<1e-9
    assert (end-stamp(history[0]['snapshotTime'])).total_seconds()>=30*60
    return history

def load_rows(input_root):
    original=read(input_root/'market_study/observations.json')['observations']
    audit=read(input_root/'market_study/audit.json')
    boards={}
    for source in audit['sources']:
        path=input_root/source['path'];data=path.read_bytes()
        assert hashlib.sha256(data).hexdigest()==source['sha256'],str(path)
        m=json.loads(data)['ncaamMarket'];boards[m['date']]=m['splits']
    rows=[]
    for old in original:
        r=dict(old);history=pair_history(r,boards[r['date']])
        f=flow_features(history,as_of=r['decision'],tipoff=r['tipoff'],market=r['market'])
        assert all(stamp(w['confirmation_observed_through'])<=stamp(r['decision']) for w in f['intervals'])
        net_price=(history[-1]['line']-history[0]['line'])*(-1 if r['market']=='Spread' else 1)
        assert abs(f['features'][0]-net_price)<1e-8
        available=[p['snapshotTime'] for p in history if stamp(p['snapshotTime'])>=stamp(r['baselineCapturedAt'])]
        assert available and stamp(r['ratingsObservedAt'])<=stamp(r['baselineCapturedAt'])<=stamp(r['decision'])<stamp(r['tipoff'])
        r.update(features=f['features'],flow=f,history=history,firstVisibleUpdateAt=min(available,key=stamp))
        rows.append(r)
    keys=[(r['date'],r['gameId'],r['market']) for r in rows]
    assert len(keys)==len(set(keys))
    return rows

def bet(r,projection):
    gap=projection-r['marketValue'];edge=abs(gap)
    eligible=5<=edge<7 if r['market']=='Spread' else edge>=8
    side=('home' if gap>0 else 'away') if r['market']=='Spread' else ('Over' if gap>0 else 'Under')
    cover=(r['actual']-r['marketValue'])*(1 if gap>0 else -1)
    result='W' if cover>1e-8 else 'L' if cover< -1e-8 else 'P'
    profit=10/11 if result=='W' else -1 if result=='L' else 0.
    pick=(r[side+'Team']+' '+format(r['quote']*(1 if side=='home' else -1),'+g')) if r['market']=='Spread' else side+' '+format(r['quote'],'g')
    return {'projection':projection,'adjustment':projection-r['baseline'],'edge':edge,'selected':eligible,
            'pick':pick,'side':side,'result':result,'profit':profit if eligible else 0.}

def stats(rows):
    bets=[r for r in rows if r['selected']]
    w=sum(r['result']=='W' for r in bets);l=sum(r['result']=='L' for r in bets);p=len(bets)-w-l
    units=sum(r['profit'] for r in bets)
    assert abs(units-(w*10/11-l))<1e-8
    return {'opportunities':len(rows),'bets':len(bets),'record':f'{w}-{l}-{p}',
            'wins':w,'losses':l,'pushes':p,'netUnits':units,
            'roiPct':100*units/len(bets) if bets else None,
            'meanAbsoluteAdjustment':sum(abs(r['adjustment']) for r in rows)/len(rows) if rows else None,
            'maxAbsoluteAdjustment':max((abs(r['adjustment']) for r in rows),default=None)}

def select_profile(train,minimum_bets=20):
    reports={}
    for profile in PROFILES:
        reports[profile]=stats([bet(r,adjustment(r['baseline'],r['flow'],profile)['projection']) for r in train])
    qualifying=[p for p in PROFILES if reports[p]['bets']>=minimum_bets]
    chosen=max(qualifying,key=lambda p:reports[p]['netUnits']) if qualifying else 'balanced'
    return chosen,reports,'prior_net_units' if qualifying else 'insufficient_qualifying_bets_balanced_fallback'

def write_csv(path,rows):
    if not rows:return
    slim=[{k:v for k,v in r.items() if not isinstance(v,(list,dict))} for r in rows]
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(slim[0]));writer.writeheader();writer.writerows(slim)

def fresh_baseline_case(input_root,rows):
    """Illustrate the full UConn path using baseline inputs known at each tick.

    Balanced sizing is declared and fixed for this illustration. It is not a
    claim that a profile chosen later in the day was available at earlier ticks.
    """
    market=next(r for r in rows if r['gameId']=='401909742' and r['market']=='Total')
    board=read(input_root/'analysis_inputs/analysis/ncaam_2026-11-18_final.json')['ncaamMarket']
    forecasts=[f for f in board['forecasts'] if str(f['gameId'])=='401909742']
    result=[]
    for i,point in enumerate(market['history']):
        clock=stamp(point['snapshotTime'])
        candidates=sorted([f for f in forecasts if stamp(f['lockedAt'])<=clock],key=lambda f:stamp(f['lockedAt']),reverse=True)
        projection=None
        for f in candidates:
            bench=next((f[k]['national_benchmark'] for k in ['matchup_projection','matchup_total','matchup_spread']
                        if isinstance(f.get(k),dict) and isinstance(f[k].get('national_benchmark'),dict)),None)
            if not bench:continue
            try:
                projection=from_captured_inputs(f['away_rating'],f['home_rating'],bench,
                    as_of=f['lockedAt'],tipoff=f['tipoff'],source_url=f.get('ratings_source_url','Captured Torvik export'),
                    home_court_points=f['home_court_points'],neutral_site=f['neutral_site'],lineup_adjustment=f.get('lineup_adjustment'))
                if (clock-stamp(bench['observed_at'])).total_seconds()>30*3600:projection=None;continue
                break
            except (KeyError,ValueError,TypeError):projection=None
        if projection is None:continue
        assert stamp(bench['observed_at'])<=stamp(f['lockedAt'])<=clock<stamp(market['tipoff'])
        features=flow_features(market['history'][:i+1],as_of=point['snapshotTime'],tipoff=market['tipoff'],market='Total')
        adjusted=adjustment(projection['total'],features,'balanced')
        split_windows=[w for w in features['intervals'] if w['directional_split_pressure']]
        result.append({'game':'UConn @ Arizona','snapshotTime':point['snapshotTime'],
            'timeET':clock.astimezone(ZoneInfo('America/New_York')).strftime('%b %d %I:%M %p ET'),
            'baselineRecordId':f['recordId'],'baselineCapturedAt':f['lockedAt'],
            'ratingsObservedAt':bench['observed_at'],'profile':'balanced',
            'sizing':'Declared fixed profile for this timeline illustration',
            'line':point['line'],'betsPct':point['betsPct'],'moneyPct':point['moneyPct'],
            'baseline':projection['total'],'projection':adjusted['projection'],
            'adjustment':adjusted['adjustment'],'contributions':adjusted['contributions'],
            'latestSplitWindow':split_windows[-1] if split_windows else None})
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input-root',type=Path,default=REPO.parent)
    parser.add_argument('--output-root',type=Path,default=REPO/'research/results/ncaam_market_flow_v3')
    args=parser.parse_args();out=args.output_root;out.mkdir(parents=True,exist_ok=True)
    spec_path=REPO/'config/ncaam_market_flow_research.json';spec_hash=hashlib.sha256(spec_path.read_bytes()).hexdigest()
    rows=load_rows(args.input_root)
    variants=['baseline']+list(PROFILES)+['selected']
    predictions=[];fits=[];warmup=[]
    for market in ['Spread','Total']:
        available=[r for r in rows if r['market']==market]
        for day in sorted(set(r['date'] for r in available)):
            test=[r for r in available if r['date']==day]
            cutoff=min(stamp(r['firstVisibleUpdateAt']) for r in test)
            train=[r for r in available if r['date']<day and stamp(r['tipoff'])+timedelta(hours=6)<=cutoff]
            assert all(stamp(r['decision'])<cutoff for r in train)
            if len(train)<150:
                warmup.append({'date':day,'market':market,'trainingGames':len(train),'evaluationGamesSkipped':len(test)})
                continue
            chosen,scores,reason=select_profile(train)
            fits.append({'date':day,'market':market,'profile':chosen,'trainingGames':len(train),
                         'trainingThrough':max(r['date'] for r in train),'cutoff':cutoff.isoformat(),
                         'selectionReason':reason,'priorProfileResults':scores})
            for r in test:
                for variant in variants:
                    profile=chosen if variant=='selected' else variant
                    detail=None if variant=='baseline' else adjustment(r['baseline'],r['flow'],profile)
                    projection=r['baseline'] if detail is None else detail['projection']
                    p={k:v for k,v in r.items() if k not in ['flow','history']}
                    p.update(variant=variant,profile=profile,trainingGames=len(train),**bet(r,projection))
                    if detail:
                        assert p['adjustment']==0 or abs(p['adjustment'])>=1-1e-9
                    predictions.append(p)
    periods={'historical':lambda r:r['date']<='2026-11-21','additional_day':lambda r:r['date']=='2026-11-22','all':lambda r:True}
    result={'policy':POLICY,'specificationSha256':spec_hash,
            'cohort':{'uniqueEligibleGames':len(set((r['date'],r['gameId']) for r in rows)),
                      'marketObservations':len(rows),'dates':sorted(set(r['date'] for r in rows))},
            'evaluationDates':sorted(set(r['date'] for r in predictions)),'results':{},'diagnostics':{},'warmup':warmup}
    for period,keep in periods.items():
        result['results'][period]={}
        for market in ['Spread','Total']:
            selected=[r for r in predictions if r['market']==market and keep(r)]
            result['results'][period][market]={v:stats([r for r in selected if r['variant']==v]) for v in variants}
            if market=='Spread':
                result['results'][period]['SpreadBelow20']={v:stats([r for r in selected if r['variant']==v and abs(r['quote'])<20]) for v in variants}
    for market in ['Spread','Total']:
        selected=[r for r in predictions if r['market']==market and r['date']<='2026-11-21']
        dates=sorted(set(r['date'] for r in selected));half=dates[len(dates)//2]
        result['diagnostics'][market]={'daily':{d:{v:stats([r for r in selected if r['date']==d and r['variant']==v]) for v in variants} for d in dates},
            'blocks':{label:{v:stats([r for r in selected if r['variant']==v and (r['date']<half if label=='earlier' else r['date']>=half)]) for v in variants} for label in ['earlier','later']}}
    indexed=defaultdict(dict)
    for r in predictions:indexed[(r['date'],r['gameId'],r['market'])][r['variant']]=r
    changes=[]
    for key,pair in indexed.items():
        assert set(pair)==set(variants)
        b,n=pair['baseline'],pair['selected']
        assert b['quote']==n['quote'] and b['decision']==n['decision']
        if b['selected']!=n['selected'] or (b['selected'] and b['side']!=n['side']):
            changes.append({k:b[k] for k in ['date','gameId','game','market','quote']}|
                 {'baseline':{k:b[k] for k in ['projection','edge','selected','pick','result','profit']},
                  'selected':{k:n[k] for k in ['profile','projection','adjustment','edge','selected','pick','result','profit']}})
    fits_by={(f['date'],f['market']):f['profile'] for f in fits}
    # Store visible updates only after each captured baseline was available.
    trajectories=[]
    for r in rows:
        profile=fits_by.get((r['date'],r['market']))
        if profile is None:continue
        for i,p in enumerate(r['history']):
            if stamp(p['snapshotTime'])<stamp(r['baselineCapturedAt']):continue
            f=flow_features(r['history'][:i+1],as_of=p['snapshotTime'],tipoff=r['tipoff'],market=r['market'])
            a=adjustment(r['baseline'],f,profile)
            trajectories.append({'date':r['date'],'gameId':r['gameId'],'game':r['game'],'market':r['market'],
                'snapshotTime':p['snapshotTime'],'profile':profile,'baseline':r['baseline'],
                'line':p['line'],'betsPct':p['betsPct'],'moneyPct':p['moneyPct'],
                'projection':a['projection'],'adjustment':a['adjustment'],'contributions':a['contributions']})
    examples=[r for r in trajectories if r['gameId']=='401909742' and r['market']=='Total']
    fresh_case=fresh_baseline_case(args.input_root,rows)
    latest={}
    for market in ['Spread','Total']:
        train=[r for r in rows if r['market']==market and r['date']<='2026-11-21']
        profile,scores,reason=select_profile(train)
        latest[market]={'profile':profile,'parameters':PROFILES[profile],'trainingGames':len(train),
                        'trainingThrough':'2026-11-21','selectionReason':reason,'priorProfileResults':scores}
    validation={'matchedMarketComparisons':len(indexed),'predictionRows':len(predictions),'visibleSnapshotProjections':len(trajectories),
        'sameQuotesAcrossVariants':True,'earlierDateSizingOnly':True,'futureHistoryExcluded':True,
        'allConfirmationEvidenceVisibleByDecision':True,'independentPriceEqualsObservedNetMove':True,
        'allNonzeroAdjustmentsAtLeastOnePoint':all(r['adjustment']==0 or abs(r['adjustment'])>=1-1e-9 for r in predictions),
        'originalInputHashesVerified':True,'officialRecordsOrProductionWritten':False}
    assert hashlib.sha256(spec_path.read_bytes()).hexdigest()==spec_hash
    for name,value in [('results.json',result),('daily_calibration.json',fits),('predictions.json',predictions),
        ('bet_changes.json',changes),('snapshot_trajectories.json',trajectories),('uconn_arizona_updates.json',examples),
        ('uconn_arizona_fresh_baseline_updates.json',fresh_case),
        ('latest_profiles_through_2026-11-21.json',latest),('validation.json',validation)]:
        dump(out,name,value)
    write_csv(out/'bets.csv',[r for r in predictions if r['selected']])
    write_csv(out/'snapshot_trajectories.csv',trajectories)
    compact={period:{m:{v:{k:x[k] for k in ['opportunities','bets','record','netUnits','roiPct','meanAbsoluteAdjustment']} for v,x in z.items()} for m,z in data.items()} for period,data in result['results'].items() if period!='all'}
    print(json.dumps({'evaluationDates':result['evaluationDates'],'results':compact,'validation':validation,
          'latestProfiles':{m:{k:v for k,v in x.items() if k!='priorProfileResults'} for m,x in latest.items()},
          'uconnArizonaUpdates':examples},indent=2))

if __name__=='__main__':main()
