"""Diagnose 2026 false large CFB spread edges using existing football components.

Hypothesis generation only; no grade/production changes. Market spread is used
only after the independent projection for fixed 8+/10+ ATS evaluation.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_margin_component_weight_edge_search as cw

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
SEASON=2026;TH=(8.0,10.0);COLS=list(cw.COMP.keys())

def signed_agree(a,b):
    a=np.asarray(a,float);b=np.asarray(b,float)
    return (np.abs(a)>1e-9)&(np.abs(b)>1e-9)&(np.sign(a)==np.sign(b))

def record(frame):
    n=len(frame);w=int(frame.win.sum()) if n else 0
    return {'n':n,'wins':w,'losses':n-w,'win_rate':w/n if n else None}

def main():
    df=cw.ens.season_rows(SEASON).copy();pred=cw.reconstruct(df);market=df.market_home_spread.to_numpy(float);actual=df.actual.to_numpy(float)
    edge=pred+market;miss=actual+market;valid=np.isfinite(edge)&np.isfinite(miss)&(np.abs(edge)>1e-9)&(np.abs(miss)>1e-9)
    df=df.loc[valid].copy().reset_index(drop=True);edge=edge[valid];miss=miss[valid]
    df['edge']=edge;df['edge_abs']=np.abs(edge);df['win']=(edge*miss>0).astype(int);df['edge_sign']=np.sign(edge)
    for col,coef in cw.COMP.items():
        raw=pd.to_numeric(df[col],errors='coerce').fillna(0.).to_numpy(float);contrib=coef*raw
        df[f'{col}__contrib']=contrib;df[f'{col}__support']=df.edge_sign.to_numpy(float)*contrib
    support_cols=[f'{c}__support' for c in COLS];support=df[support_cols].to_numpy(float)
    df['support_count']=(support>1e-9).sum(axis=1);df['oppose_count']=(support<-1e-9).sum(axis=1)
    cp=pd.to_numeric(df.current_power_margin,errors='coerce').fillna(0.).to_numpy(float)
    cs=pd.to_numeric(df.current_scoring_diff,errors='coerce').fillna(0.).to_numpy(float)
    ca=pd.to_numeric(df.current_allowed_diff,errors='coerce').fillna(0.).to_numpy(float)
    pp=pd.to_numeric(df.prior_power_margin,errors='coerce').fillna(0.).to_numpy(float)
    df['current_power_scoring_align']=signed_agree(cp,cs)
    df['current_power_allowed_align']=signed_agree(cp,ca)
    df['prior_current_power_align']=signed_agree(pp,cp)
    df['current_scoring_allowed_align']=signed_agree(cs,ca)

    result={'season':SEASON,'n':len(df),'thresholds':{}}
    lines=['# 2026 Large-Edge Component Diagnostic','',f'Completed graded FBS-vs-FBS games: {len(df)}','',
           'Positive support means the component points toward the side selected by the model. Internal alignment categories use only football-model inputs.','']
    for t in TH:
        sub=df[df.edge_abs>=t].copy();w=sub[sub.win==1];l=sub[sub.win==0];rows=[]
        for c in support_cols:
            allsd=float(sub[c].std(ddof=0)) or 1.;wm=float(w[c].mean()) if len(w) else np.nan;lm=float(l[c].mean()) if len(l) else np.nan
            rows.append({'feature':c.replace('__support',''),'winner_mean_support':wm,'loser_mean_support':lm,'std_diff':(wm-lm)/allsd})
        rows.sort(key=lambda r:abs(r['std_diff']),reverse=True)
        agree=[]
        for n in range(3,8):
            rr=record(sub[sub.support_count>=n]);rr['min_supporting_components']=n;agree.append(rr)
        alignments={}
        for col,label in [('current_power_scoring_align','Current power ↔ current scoring'),('current_power_allowed_align','Current power ↔ defensive change'),('prior_current_power_align','Prior power ↔ current power shift'),('current_scoring_allowed_align','Current scoring ↔ defensive change')]:
            yes=record(sub[sub[col]]);no=record(sub[~sub[col]]);alignments[col]={'label':label,'aligned':yes,'not_aligned':no}
        result['thresholds'][str(int(t))]={'overall':record(sub),'feature_differences':rows,'agreement':agree,'internal_alignments':alignments}
        lines += [f'## {int(t)}+ model edge','',f"Record: {int(sub.win.sum())}-{int((1-sub.win).sum())} ({100*sub.win.mean():.1f}%)",'',
                  '| Component | Winner mean support | Loser mean support | Std difference |','|---|---:|---:|---:|']
        for r in rows:lines.append(f"| {r['feature']} | {r['winner_mean_support']:+.3f} | {r['loser_mean_support']:+.3f} | {r['std_diff']:+.3f} |")
        lines += ['','| Supporting components | Record | Win rate |','|---:|---:|---:|']
        for a in agree:
            if a['n']:lines.append(f"| {a['min_supporting_components']}+ of 7 | {a['wins']}-{a['losses']} | {100*a['win_rate']:.1f}% |")
        lines += ['','### Internal football-input alignment','', '| Relationship | Aligned | Not aligned |','|---|---:|---:|']
        for a in alignments.values():
            y,n=a['aligned'],a['not_aligned'];yt='n/a' if not y['n'] else f"{y['wins']}-{y['losses']} ({100*y['win_rate']:.1f}%)";nt='n/a' if not n['n'] else f"{n['wins']}-{n['losses']} ({100*n['win_rate']:.1f}%)"
            lines.append(f"| {a['label']} | {yt} | {nt} |")
        lines.append('')
    (OUT/'cfb_2026_large_edge_component_diagnostic.json').write_text(json.dumps(result,indent=2,allow_nan=False));(OUT/'CFB_2026_LARGE_EDGE_COMPONENT_DIAGNOSTIC.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
