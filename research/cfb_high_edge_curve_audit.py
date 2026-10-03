"""Diagnostic-only extended edge curve for the frozen combined CFB challenger.

No grade thresholds are changed here. Fixed cumulative checkpoints 2/4/6/8/10/
12/14/16 are reported to test whether stronger model-market disagreement remains
monotonic. The projection remains sportsbook-independent.
"""
from __future__ import annotations
import json,math
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_combined_adjusted_edge_search as c

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TH=(2.,4.,6.,8.,10.,12.,14.,16.)
FEATURES=c.FPI_CONTROL+c.ADJ+c.COMP;ALPHA=64.;CAP=8.

def metric(df,p):
    p=np.asarray(p,float);edge=p+df.market_home_spread.to_numpy(float);miss=df.actual.to_numpy(float)+df.market_home_spread.to_numpy(float);ok=np.isfinite(edge)&np.isfinite(miss)&(np.abs(edge)>1e-9)&(np.abs(miss)>1e-9);edge=edge[ok];miss=miss[ok];w=(edge*miss>0).astype(int);out=[]
    for t in TH:
        s=np.abs(edge)>=t;n=int(s.sum());ww=int(w[s].sum());out.append({'threshold':t,'n':n,'wins':ww,'losses':n-ww,'win_rate':ww/n if n else None})
    return out
def fmt(r):return f"{r['wins']}-{r['losses']} ({100*r['win_rate']:.1f}%)" if r['n'] else '-'
def clean(v):
    if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
    if isinstance(v,list):return [clean(x) for x in v]
    if isinstance(v,(np.integer,)):return int(v)
    if isinstance(v,(np.floating,float)):return float(v) if math.isfinite(float(v)) else None
    return v

def main():
    fs={s:c.build_season(s) for s in (2021,2022,2023,2024,2025,2026)};tr=pd.concat([fs[2021],fs[2022],fs[2023]],ignore_index=True);m24=c.fit(tr,FEATURES,ALPHA);t25=pd.concat([tr,fs[2024]],ignore_index=True);m25=c.fit(t25,FEATURES,ALPHA);t26=pd.concat([t25,fs[2025]],ignore_index=True);m26=c.fit(t26,FEATURES,ALPHA);models={2024:m24,2025:m25,2026:m26};rows=[];data={}
    for yr in (2024,2025,2026):
        df=fs[yr];pred=c.predict(df,models[yr],CAP);data[yr]={}
        for window,mask in [('w1_5',pd.to_numeric(df.week,errors='coerce').between(1,5)),('all',pd.Series(True,index=df.index))]:
            d=df[mask].copy();pp=pred[mask.to_numpy()];base=metric(d,d.baseline);chall=metric(d,pp);data[yr][window]={'baseline':base,'combined':chall};rows.append((yr,window,base,chall))
    # pooled post-selection holdout early windows: 2025+2026 only.
    pooled=pd.concat([fs[2025][pd.to_numeric(fs[2025].week,errors='coerce').between(1,5)],fs[2026][pd.to_numeric(fs[2026].week,errors='coerce').between(1,5)]],ignore_index=True)
    # Need predictions built separately because coefficients refit between seasons.
    p25=c.predict(fs[2025],m25,CAP);mask25=pd.to_numeric(fs[2025].week,errors='coerce').between(1,5).to_numpy();p26=c.predict(fs[2026],m26,CAP);mask26=pd.to_numeric(fs[2026].week,errors='coerce').between(1,5).to_numpy();pp=np.concatenate([p25[mask25],p26[mask26]])
    pooled_base=metric(pooled,pooled.baseline);pooled_comb=metric(pooled,pp);data['holdout_pooled_w1_5']={'baseline':pooled_base,'combined':pooled_comb}
    (OUT/'cfb_high_edge_curve_audit.json').write_text(json.dumps(clean(data),indent=2,allow_nan=False))
    lines=['# CFB Extended High-Edge Curve Audit','','Frozen combined challenger; thresholds are diagnostic only.','','| Season | Window | Model | '+' | '.join(f'{int(t)}+' for t in TH)+' |','|---:|---|---|'+'|'.join(['---:']*len(TH))+'|']
    for yr,win,b,q in rows:
        lines.append(f"| {yr} | {win} | Baseline | "+' | '.join(fmt(r) for r in b)+' |');lines.append(f"| {yr} | {win} | Combined | "+' | '.join(fmt(r) for r in q)+' |')
    lines.append('| 2025+2026 | w1_5 pooled holdout | Baseline | '+' | '.join(fmt(r) for r in pooled_base)+' |');lines.append('| 2025+2026 | w1_5 pooled holdout | Combined | '+' | '.join(fmt(r) for r in pooled_comb)+' |')
    (OUT/'CFB_HIGH_EDGE_CURVE_AUDIT.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
