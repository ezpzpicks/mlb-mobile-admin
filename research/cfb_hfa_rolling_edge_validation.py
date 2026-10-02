"""Rolling-year validation of the generic CFB home-indicator weight.

For each fold, select one HFA multiplier using only the prior season's fixed
2/4/6/8/10 edge progression, then apply it unchanged to the next season.
Market spreads are evaluation/selection targets only, never model inputs.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_margin_component_weight_edge_search as cw
OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
GRID=(0.,.125,.25,.375,.5,.75,1.,1.25,1.5)
TH=(2.,4.,6.,8.,10.)
FOLDS=((2022,2023),(2023,2024),(2024,2025))

def auc(scores,labels):
    scores=np.asarray(scores,float);labels=np.asarray(labels,int);n1=int((labels==1).sum());n0=int((labels==0).sum())
    if not n1 or not n0:return None
    ranks=pd.Series(scores).rank(method='average').to_numpy();return float((ranks[labels==1].sum()-n1*(n1+1)/2)/(n1*n0))

def metrics(df,pred):
    pred=np.asarray(pred,float);actual=df.actual.to_numpy(float);edge=pred+df.market_home_spread.to_numpy(float);miss=actual+df.market_home_spread.to_numpy(float)
    ok=np.isfinite(edge)&np.isfinite(miss)&(np.abs(edge)>1e-9)&(np.abs(miss)>1e-9);edge=edge[ok];miss=miss[ok];win=(edge*miss>0).astype(int)
    rates=[]
    for t in TH:
        s=np.abs(edge)>=t;n=int(s.sum());w=int(win[s].sum());rates.append({'threshold':t,'n':n,'wins':w,'losses':n-w,'win_rate':w/n if n else None})
    valid=[r for r in rates if r['n']>=25 and r['win_rate'] is not None]
    steps=sum(b['win_rate']>=a['win_rate'] for a,b in zip(valid,valid[1:]));viol=sum(max(0.,a['win_rate']-b['win_rate']) for a,b in zip(valid,valid[1:]));slope=valid[-1]['win_rate']-valid[0]['win_rate'] if len(valid)>1 else -1.;high=next((r['win_rate'] for r in rates if r['threshold']==10. and r['n']>=25),None)
    return {'auc':auc(np.abs(edge),win),'corr':float(np.corrcoef(edge,miss)[0,1]) if len(edge)>2 else None,'mae':float(np.mean(np.abs(pred-actual))),'monotonic_steps':int(steps),'monotonic_possible':max(0,len(valid)-1),'violation_sum':float(viol),'slope_2_to_10':float(slope),'high_edge_win_rate':high,'thresholds':rates}

def key(m):
    return (m['monotonic_steps'],-m['violation_sum'],m['slope_2_to_10'],m['high_edge_win_rate'] if m['high_edge_win_rate'] is not None else -1.,m['auc'] if m['auc'] is not None else -1.)

def predict(df,mult):return cw.reconstruct(df,{'home_indicator':float(mult)})

def main():
    cache={season:cw.ens.season_rows(season) for season in range(2022,2026)};folds=[]
    for select_season,test_season in FOLDS:
        s=cache[select_season];t=cache[test_season];candidates=[]
        for mult in GRID:candidates.append({'multiplier':mult,'selection':metrics(s,predict(s,mult))})
        candidates.sort(key=lambda r:key(r['selection']),reverse=True);chosen=candidates[0];chosen['test']=metrics(t,predict(t,chosen['multiplier']));baseline={'selection':metrics(s,predict(s,1.0)),'test':metrics(t,predict(t,1.0))};folds.append({'select_season':select_season,'test_season':test_season,'chosen':chosen,'baseline':baseline,'all_candidates':candidates})
    out={'grid':list(GRID),'selection_rule':'monotonic steps, violation size, 2-to-10 slope, 10+ win rate, AUC','folds':folds};(OUT/'cfb_hfa_rolling_edge_validation.json').write_text(json.dumps(out,indent=2,allow_nan=False))
    lines=['# Rolling-Year HFA Edge Validation','',f"Production home coefficient: {cw.COMP['home_indicator']:.4f}",'','| Select→Test | Chosen mult | Select steps | Test steps | Base test steps | Chosen test slope | Base test slope | Chosen 10+ | Base 10+ | AUC chosen/base |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for f in folds:
        c=f['chosen'];b=f['baseline'];ct=c['test'];bt=b['test'];c10=next(r for r in ct['thresholds'] if r['threshold']==10);b10=next(r for r in bt['thresholds'] if r['threshold']==10)
        lines.append(f"| {f['select_season']}→{f['test_season']} | {c['multiplier']:.3f} | {c['selection']['monotonic_steps']}/{c['selection']['monotonic_possible']} | {ct['monotonic_steps']}/{ct['monotonic_possible']} | {bt['monotonic_steps']}/{bt['monotonic_possible']} | {100*ct['slope_2_to_10']:+.1f}pp | {100*bt['slope_2_to_10']:+.1f}pp | {100*c10['win_rate']:.1f}% | {100*b10['win_rate']:.1f}% | {ct['auc']:.3f}/{bt['auc']:.3f} |")
    lines+=['','## Fixed threshold detail']
    for f in folds:
        lines+=['',f"### {f['select_season']} selected → {f['test_season']} test (mult={f['chosen']['multiplier']})",'','| Edge | Production | Selected HFA |','|---:|---:|---:|']
        for b,c in zip(f['baseline']['test']['thresholds'],f['chosen']['test']['thresholds']):lines.append(f"| {b['threshold']:.0f}+ | {b['wins']}-{b['losses']} ({100*b['win_rate']:.1f}%) | {c['wins']}-{c['losses']} ({100*c['win_rate']:.1f}%) |")
    (OUT/'CFB_HFA_ROLLING_EDGE_VALIDATION.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
