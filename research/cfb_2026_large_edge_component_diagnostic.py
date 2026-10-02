"""Diagnose 2026 false large CFB spread edges using existing football components.

This is hypothesis generation only. It does not change grades or production.
For completed FBS-vs-FBS 2026 games, compare the seven production-style margin
component contributions between ATS winners and losers at fixed 8+ and 10+ edge.
Also measure whether component directional agreement separates reliable from
false large edges. Market spread is evaluation-only.
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

from research import cfb_margin_component_weight_edge_search as cw

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
SEASON=2026
TH=(8.0,10.0)
COLS=list(cw.COMP.keys())

def main():
    df=cw.ens.season_rows(SEASON).copy()
    pred=cw.reconstruct(df)
    market=df.market_home_spread.to_numpy(float)
    actual=df.actual.to_numpy(float)
    edge=pred+market
    miss=actual+market
    valid=np.isfinite(edge)&np.isfinite(miss)&(np.abs(edge)>1e-9)&(np.abs(miss)>1e-9)
    df=df.loc[valid].copy().reset_index(drop=True); edge=edge[valid]; miss=miss[valid]
    df['edge']=edge;df['edge_abs']=np.abs(edge);df['win']=(edge*miss>0).astype(int);df['edge_sign']=np.sign(edge)
    # Signed contribution in the direction of the model's selected side. Positive
    # means this component supports the side; negative means it pushes against it.
    for col,coef in cw.COMP.items():
        raw=pd.to_numeric(df[col],errors='coerce').fillna(0.).to_numpy(float)
        contrib=coef*raw
        df[f'{col}__contrib']=contrib
        df[f'{col}__support']=df.edge_sign.to_numpy(float)*contrib
    support_cols=[f'{c}__support' for c in COLS]
    # Ignore exact-zero terms when measuring directional consensus.
    support=df[support_cols].to_numpy(float)
    df['support_count']=(support>1e-9).sum(axis=1)
    df['oppose_count']=(support<-1e-9).sum(axis=1)
    df['net_component_support']=support.sum(axis=1)
    df['min_component_support']=support.min(axis=1)

    result={'season':SEASON,'n':len(df),'thresholds':{}}
    lines=['# 2026 Large-Edge Component Diagnostic','',f'Completed graded FBS-vs-FBS games: {len(df)}','',
           'Positive support means the component points toward the side selected by the model.','']
    for t in TH:
        sub=df[df.edge_abs>=t].copy();w=sub[sub.win==1];l=sub[sub.win==0]
        rows=[]
        for c in support_cols:
            allsd=float(sub[c].std(ddof=0)) or 1.;wm=float(w[c].mean()) if len(w) else np.nan;lm=float(l[c].mean()) if len(l) else np.nan
            rows.append({'feature':c.replace('__support',''),'winner_mean_support':wm,'loser_mean_support':lm,'std_diff':(wm-lm)/allsd})
        rows.sort(key=lambda r:abs(r['std_diff']),reverse=True)
        agree=[]
        for n in range(3,8):
            ss=sub[sub.support_count>=n];nn=len(ss);ww=int(ss.win.sum());agree.append({'min_supporting_components':n,'n':nn,'wins':ww,'losses':nn-ww,'win_rate':ww/nn if nn else None})
        result['thresholds'][str(int(t))]={'n':len(sub),'wins':int(sub.win.sum()),'losses':int((1-sub.win).sum()),'win_rate':float(sub.win.mean()),'feature_differences':rows,'agreement':agree}
        lines += [f'## {int(t)}+ model edge','',f"Record: {int(sub.win.sum())}-{int((1-sub.win).sum())} ({100*sub.win.mean():.1f}%)",'',
                  '| Component | Winner mean support | Loser mean support | Std difference |','|---|---:|---:|---:|']
        for r in rows:
            lines.append(f"| {r['feature']} | {r['winner_mean_support']:+.3f} | {r['loser_mean_support']:+.3f} | {r['std_diff']:+.3f} |")
        lines += ['', '| Supporting components | Record | Win rate |','|---:|---:|---:|']
        for a in agree:
            if a['n']:
                lines.append(f"| {a['min_supporting_components']}+ of 7 | {a['wins']}-{a['losses']} | {100*a['win_rate']:.1f}% |")
        lines.append('')
    (OUT/'cfb_2026_large_edge_component_diagnostic.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    (OUT/'CFB_2026_LARGE_EDGE_COMPONENT_DIAGNOSTIC.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
if __name__=='__main__':main()
