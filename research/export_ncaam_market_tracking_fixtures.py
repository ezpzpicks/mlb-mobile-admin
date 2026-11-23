"""Export cross-language rule examples, without betting outcomes or a backtest."""
from pathlib import Path
import argparse
import itertools
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from builders.ncaam_market_flow import POLICY,PROFILES,flow_features,adjustment

T0='2026-11-17T12:00:00Z'
T1='2026-11-17T13:00:00Z'
T2='2026-11-17T14:00:00Z'
TIP='2026-11-17T15:00:00Z'

def fixtures():
    parameters=json.loads((ROOT/'config/ncaam_market_tracking_live.json').read_text())
    assert parameters['flow_policy']==POLICY
    assert parameters['profile']=='balanced'
    assert parameters['weights']==PROFILES['balanced']['weights']
    cases=[]
    def add(name,history,market='Total',as_of=T1,tipoff=TIP,baseline=150):
        f=flow_features(history,as_of=as_of,tipoff=tipoff,market=market)
        a=adjustment(baseline,f,'balanced')
        cases.append(dict(name=name,history=history,market=market,asOf=as_of,tipoff=tipoff,baseline=baseline,
            expected=dict(features=f['features'],status=f['status'],adjustment=a['adjustment'],projection=a['projection'],
                          rawAdjustment=a['raw_adjustment'],contributions=a['contributions'],
                          multipliers=[w['split_strength_multiplier'] for w in f['intervals']])))
    def h(bets,money,lines,times=(T0,T1)):
        return [dict(snapshotTime=t,betsPct=b,moneyPct=m,line=l) for t,b,m,l in zip(times,bets,money,lines)]
    for market in ['Spread','Total']:
        for money,bets,line in itertools.product([-1,0,1],repeat=3):
            lines=[-3,-3-line] if market=='Spread' else [150,150+line]
            add(f'{market}-states-{money}-{bets}-{line}',h([50,50+bets*10],[50,50+money*10],lines),market,baseline=3 if market=='Spread' else 150)
    approved=[([50,26],[50,66],[151.5,149.5],'Total'),([12,26],[95,66],[151.5,149.5],'Total'),
        ([45,60],[50,75],[149.5,152.5],'Total'),([70,55],[80,60],[152.5,149.5],'Total'),
        ([40,60],[70,45],[150.5,150.5],'Total'),([20,20],[80,80],[150.5,150.5],'Total'),
        ([45,35],[50,75],[6.5,4.5],'Spread'),([20,40],[90,65],[-6.5,-4.5],'Spread')]
    for i,(b,m,l,market) in enumerate(approved,1):add(f'approved-{i}',h(b,m,l),market)
    for points in [0,.5,1,1.5,2,3]:add(f'confirmation-{points}',h([50,55],[50,70],[150,150+points]))
    for name,b,m,l in [('later-confirmation',[50,55,55],[50,70,70],[150,150,152]),
                       ('earlier-price',[50,50,55],[50,50,70],[150,152,152]),
                       ('new-splits-end-attribution',[50,55,60],[50,70,80],[150,150,152]),
                       ('late-flat-reversal',[50,45,49],[50,31,66],[150,148,148]),
                       ('equal-endpoints',[50,60,40],[50,80,50],[150,153,150])]:
        add(name,h(b,m,l,(T0,T1,T2)),as_of=T2)
    add('missing-money',h([50,70],[None,None],[150,150]))
    add('missing-splits-line-only',h([None,None],[None,None],[150,152]))
    uconn=json.loads((ROOT/'research/results/ncaam_directional_market_flow_v3_2026-11-22.json').read_text())['uconnArizonaFixedBalancedTimeline']
    history=[]
    for i,r in enumerate(uconn):
        history.append({k:r[k] for k in ['snapshotTime','line','betsPct','moneyPct']})
        add(f'uconn-update-{i+1}',list(history),as_of=r['snapshotTime'],tipoff='2026-11-19T02:00:00Z',baseline=r['baseline'])
    return dict(policy=POLICY,parameters=parameters,cases=cases)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'research/fixtures/ncaam_market_tracking_flow.json')
    args=parser.parse_args();result=fixtures();args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'cases':len(result['cases']),'output':str(args.output),'outcomesUsed':False}))
