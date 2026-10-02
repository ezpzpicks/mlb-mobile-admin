"""Compare CFB spread edge ordering in equivalent early-season windows.

Uses the frozen 2024-selected combined challenger (FPI control + adjusted EPA/FEI
+ competitive-state efficiency, ridge alpha 64, correction cap 8). No retuning
is performed by season or week. Market spreads remain evaluation-only.
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_combined_adjusted_edge_search as c

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
FEATURES=c.FPI_CONTROL+c.ADJ+c.COMP
ALPHA=64.;CAP=8.;TH=(2.,4.,6.,8.,10.)

def fit(train):return c.fit(train,FEATURES,ALPHA)
def pred(df,m):return c.predict(df,m,CAP)
def met(df,p):return c.metrics(df,p)
def rt(m,t):
    r=next(x for x in m['thresholds'] if x['threshold']==t);return f"{r['wins']}-{r['losses']} ({100*r['win_rate']:.1f}%)"
def clean(v):
    if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
    if isinstance(v,list):return [clean(x) for x in v]
    if isinstance(v,(np.integer,)):return int(v)
    if isinstance(v,(np.floating,float)):return float(v) if math.isfinite(float(v)) else None
    return v

def main():
    seasons={s:c.build_season(s) for s in (2021,2022,2023,2024,2025,2026)}
    train=pd.concat([seasons[2021],seasons[2022],seasons[2023]],ignore_index=True);m24=fit(train)
    train25=pd.concat([train,seasons[2024]],ignore_index=True);m25=fit(train25)
    train26=pd.concat([train25,seasons[2025]],ignore_index=True);m26=fit(train26)
    models={2024:m24,2025:m25,2026:m26};rows=[];out={}
    windows={'w1_3':(1,3),'w1_5':(1,5),'w4_5':(4,5),'w6_plus':(6,99),'all':(1,99)}
    for yr in (2024,2025,2026):
        df=seasons[yr];out[yr]={}
        cp=pred(df,models[yr])
        for name,(lo,hi) in windows.items():
            mask=pd.to_numeric(df.week,errors='coerce').between(lo,hi);sub=df[mask].copy();pp=cp[mask.to_numpy()]
            if len(sub)<10:continue
            b=met(sub,sub.baseline);q=met(sub,pp);out[yr][name]={'n':len(sub),'baseline':b,'combined':q}
            rows.append((yr,name,len(sub),b,q))
    (OUT/'cfb_early_season_edge_audit.json').write_text(json.dumps(clean(out),indent=2,allow_nan=False))
    lines=['# CFB Early-Season Edge Audit','','Frozen challenger: FPI control + opponent-adjusted EPA/FEI + competitive-state efficiency, alpha=64, cap=8.','','| Season | Window | N | Model | Steps | slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |','|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for yr,name,n,b,q in rows:
        for label,m in [('Baseline',b),('Combined',q)]:lines.append(f"| {yr} | {name} | {n} | {label} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} | {m['mae']:.3f} | "+' | '.join(rt(m,t) for t in TH)+' |')
    (OUT/'CFB_EARLY_SEASON_EDGE_AUDIT.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
