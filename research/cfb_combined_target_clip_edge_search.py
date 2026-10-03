"""Test whether limiting blowout leverage improves the combined CFB edge ranking.

The predictor set is frozen to the combined adjusted challenger. Only the TRAINING
residual target (actual margin - baseline margin) is optionally clipped. This uses
football results only; sportsbook lines remain evaluation-only. 2024 selects the
clip/alpha/cap, then 2025 and 2026 are sequential holdouts.
"""
from __future__ import annotations
import json,math
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_combined_adjusted_edge_search as c

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TRAIN=(2021,2022,2023);VALID=2024;HOLD=2025;FINAL=2026
FEATURES=c.FPI_CONTROL+c.ADJ+c.COMP
CLIPS=(12.,16.,20.,24.,30.,None);ALPHAS=(16.,64.,256.);CAPS=(4.,6.,8.);TH=(2.,4.,6.,8.,10.)

def fit(train,alpha,target_clip):
    x=train[FEATURES].apply(pd.to_numeric,errors='coerce');mu=x.mean();x=x.fillna(mu);sd=x.std(ddof=0).replace(0.,1.);z=((x-mu)/sd).to_numpy(float);y=(train.actual-train.baseline).to_numpy(float)
    if target_clip is not None:y=np.clip(y,-target_clip,target_clip)
    X=np.column_stack([np.ones(len(z)),z]);P=np.eye(X.shape[1])*alpha;P[0,0]=0.;b=np.linalg.solve(X.T@X+P,X.T@y);return {'mu':mu,'sd':sd,'b':b}
def pred(df,m,cap):
    x=df[FEATURES].apply(pd.to_numeric,errors='coerce').fillna(m['mu']);z=((x-m['mu'])/m['sd']).to_numpy(float);corr=m['b'][0]+z@m['b'][1:];return df.baseline.to_numpy(float)+np.clip(corr,-cap,cap)
def met(df,p):return c.metrics(df,p)
def key(m):return c.key(m)
def rt(m,t):return c.rt(m,t)
def clean(v):
    if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
    if isinstance(v,list):return [clean(x) for x in v]
    if isinstance(v,(np.integer,)):return int(v)
    if isinstance(v,(np.floating,float)):return float(v) if math.isfinite(float(v)) else None
    return v

def main():
    fs={s:c.build_season(s) for s in (*TRAIN,VALID,HOLD,FINAL)};df=pd.concat(fs.values(),ignore_index=True);tr=df[df.season.isin(TRAIN)];va=df[df.season==VALID];ho=df[df.season==HOLD];fi=df[df.season==FINAL];base={2024:met(va,va.baseline),2025:met(ho,ho.baseline),2026:met(fi,fi.baseline)};cand=[]
    for clip in CLIPS:
        for alpha in ALPHAS:
            m=fit(tr,alpha,clip)
            for cap in CAPS:cand.append({'target_clip':clip,'alpha':alpha,'cap':cap,'validation':met(va,pred(va,m,cap))})
    cand.sort(key=lambda r:key(r['validation']),reverse=True);ch=cand[0];m25=fit(pd.concat([tr,va]),ch['alpha'],ch['target_clip']);r25=met(ho,pred(ho,m25,ch['cap']));m26=fit(pd.concat([tr,va,ho]),ch['alpha'],ch['target_clip']);r26=met(fi,pred(fi,m26,ch['cap']))
    out={'market_predictor':False,'chosen_on_2024':ch,'baseline':base,'holdout_2025':r25,'holdout_2026':r26,'candidates':cand};(OUT/'cfb_combined_target_clip_edge_search.json').write_text(json.dumps(clean(out),indent=2,allow_nan=False))
    clipname='none' if ch['target_clip'] is None else str(ch['target_clip']);lines=['# CFB Combined Target-Clip Edge Search','',f"Chosen on 2024: target_clip={clipname}, alpha={ch['alpha']}, cap={ch['cap']}",'','| Season | Model | Steps | slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |','|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for yr,b,q in [(2024,base[2024],ch['validation']),(2025,base[2025],r25),(2026,base[2026],r26)]:
        for label,m in [('Baseline',b),('Clipped combined',q)]:lines.append(f"| {yr} | {label} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} | {m['mae']:.3f} | "+' | '.join(rt(m,t) for t in TH)+' |')
    lines+=['','## Top 2024 candidates','','| Target clip | Alpha | Cap | Steps | slope | 10+ | AUC | MAE |','|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in cand[:20]:
        m=r['validation'];cn='none' if r['target_clip'] is None else f"{r['target_clip']:.0f}";lines.append(f"| {cn} | {r['alpha']:.0f} | {r['cap']:.0f} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {100*(m['high'] or 0):.1f}% | {m['auc']:.3f} | {m['mae']:.3f} |")
    (OUT/'CFB_COMBINED_TARGET_CLIP_EDGE_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
