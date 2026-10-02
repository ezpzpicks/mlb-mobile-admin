"""Validate the fixed 25% home-indicator hypothesis across multiple seasons.

This script does not tune anything. It compares production HFA weight (1.0) to
one fixed challenger (0.25), chosen previously on 2024. Closing spreads are used
only after independent predictions to grade fixed 2/4/6/8/10 edge checkpoints.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_margin_component_weight_edge_search as cw

OUT=Path('research/results'); OUT.mkdir(parents=True,exist_ok=True)
SEASONS=(2022,2023,2024,2025)
TH=(2.,4.,6.,8.,10.)
HFA_MULT=.25

def auc(scores,labels):
    scores=np.asarray(scores,float); labels=np.asarray(labels,int)
    n1=int((labels==1).sum()); n0=int((labels==0).sum())
    if not n1 or not n0:return None
    ranks=pd.Series(scores).rank(method='average').to_numpy()
    return float((ranks[labels==1].sum()-n1*(n1+1)/2)/(n1*n0))

def metrics(df,pred):
    pred=np.asarray(pred,float); actual=df.actual.to_numpy(float); edge=pred+df.market_home_spread.to_numpy(float); miss=actual+df.market_home_spread.to_numpy(float)
    ok=np.isfinite(edge)&np.isfinite(miss)&(np.abs(edge)>1e-9)&(np.abs(miss)>1e-9)
    edge=edge[ok]; miss=miss[ok]; win=(edge*miss>0).astype(int)
    rates=[]
    for t in TH:
        s=np.abs(edge)>=t; n=int(s.sum()); w=int(win[s].sum())
        rates.append({'threshold':t,'n':n,'wins':w,'losses':n-w,'win_rate':w/n if n else None})
    valid=[r for r in rates if r['n']>=25]
    steps=sum(b['win_rate']>=a['win_rate'] for a,b in zip(valid,valid[1:]))
    slope=valid[-1]['win_rate']-valid[0]['win_rate'] if len(valid)>1 else None
    return {'n':int(len(edge)),'auc':auc(np.abs(edge),win),'corr':float(np.corrcoef(edge,miss)[0,1]) if len(edge)>2 else None,'mae':float(np.mean(np.abs(pred-actual))),'monotonic_steps':int(steps),'monotonic_possible':max(0,len(valid)-1),'slope_2_to_10':slope,'thresholds':rates}

def main():
    rows=[]
    for season in SEASONS:
        df=cw.ens.season_rows(season)
        base=cw.reconstruct(df)
        challenger=cw.reconstruct(df,{'home_indicator':HFA_MULT})
        b=metrics(df,base); c=metrics(df,challenger)
        rows.append({'season':season,'baseline':b,'hfa25':c})
    # Pooled report is descriptive only; annual consistency is the primary test.
    all_frames=[]
    for season in SEASONS:
        d=cw.ens.season_rows(season).copy();d['season']=season;all_frames.append(d)
    pooled=pd.concat(all_frames,ignore_index=True)
    pb=metrics(pooled,cw.reconstruct(pooled));pc=metrics(pooled,cw.reconstruct(pooled,{'home_indicator':HFA_MULT}))
    out={'hypothesis':{'home_indicator_multiplier':HFA_MULT,'production_multiplier':1.0,'retuned':False},'seasons':rows,'pooled':{'baseline':pb,'hfa25':pc}}
    (OUT/'cfb_hfa25_multiyear_edge_validation.json').write_text(json.dumps(out,indent=2,allow_nan=False))
    lines=['# Fixed 25% HFA Multi-Year Edge Validation','',f"Production home-indicator coefficient: {cw.COMP['home_indicator']:.4f}",f"Fixed challenger coefficient: {cw.COMP['home_indicator']*HFA_MULT:.4f}",'','| Season | Baseline steps | HFA25 steps | Baseline slope | HFA25 slope | Baseline 10+ | HFA25 10+ | Base AUC | HFA25 AUC |','|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        b,c=r['baseline'],r['hfa25']; b10=next(x for x in b['thresholds'] if x['threshold']==10); c10=next(x for x in c['thresholds'] if x['threshold']==10)
        lines.append(f"| {r['season']} | {b['monotonic_steps']}/{b['monotonic_possible']} | {c['monotonic_steps']}/{c['monotonic_possible']} | {100*b['slope_2_to_10']:+.1f}pp | {100*c['slope_2_to_10']:+.1f}pp | {100*b10['win_rate']:.1f}% ({b10['wins']}-{b10['losses']}) | {100*c10['win_rate']:.1f}% ({c10['wins']}-{c10['losses']}) | {b['auc']:.3f} | {c['auc']:.3f} |")
    lines+=['','## Fixed thresholds by season']
    for r in rows:
        lines+=['',f"### {r['season']}",'','| Edge | Baseline | HFA25 |','|---:|---:|---:|']
        for b,c in zip(r['baseline']['thresholds'],r['hfa25']['thresholds']):
            lines.append(f"| {b['threshold']:.0f}+ | {b['wins']}-{b['losses']} ({100*b['win_rate']:.1f}%) | {c['wins']}-{c['losses']} ({100*c['win_rate']:.1f}%) |")
    lines+=['','## Pooled 2022-2025',f"- Baseline AUC: {pb['auc']:.3f}; HFA25 AUC: {pc['auc']:.3f}",f"- Baseline monotonic steps: {pb['monotonic_steps']}/{pb['monotonic_possible']}; HFA25: {pc['monotonic_steps']}/{pc['monotonic_possible']}",f"- Baseline 2→10 slope: {100*pb['slope_2_to_10']:+.1f}pp; HFA25: {100*pc['slope_2_to_10']:+.1f}pp"]
    (OUT/'CFB_HFA25_MULTIYEAR_EDGE_VALIDATION.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
