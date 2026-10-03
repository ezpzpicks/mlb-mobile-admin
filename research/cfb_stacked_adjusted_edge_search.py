"""Stack two independently selected football-only CFB residual challengers.

Base challenger A was selected on 2024: FPI control + opponent-adjusted EPA/FEI
+ competitive-state efficiency, ridge 64, cap 8.
Base challenger B was selected on 2024: quality-confirmation interactions, ridge
64, cap 8. This script does NOT reselect those component models.

2025 is used only to choose a coarse blend weight; the chosen blend is then
refit through 2025 and evaluated on 2026 with zero retuning. Sportsbook lines
remain evaluation-only and never enter either projection model.
"""
from __future__ import annotations
import json,math
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_power_confirmation_edge_search as p
from research import cfb_combined_adjusted_edge_search as c

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TH=(2.,4.,6.,8.,10.);WEIGHTS=(0.,.25,.5,.75,1.)
COMBO_FEATURES=c.FPI_CONTROL+c.ADJ+c.COMP
POWER_FEATURES=p.FAMILIES['quality_confirmation_only']
ALPHA=64.;CAP=8.

def residual_predict(df,model,which):
    if which=='combo':q=c.predict(df,model,CAP)
    else:q=p.pred(df,model,CAP)
    return np.asarray(q,float)-df.baseline.to_numpy(float)
def metrics(df,pred):return c.metrics(df,np.asarray(pred,float))
def key(m):return (m['steps'],-m['violation'],m['slope'],m['high'] if m['high'] is not None else -1.,m['auc'] if m['auc'] is not None else -1.)
def rt(m,t):
    r=next(x for x in m['thresholds'] if x['threshold']==t);return f"{r['wins']}-{r['losses']} ({100*r['win_rate']:.1f}%)"
def clean(v):
    if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
    if isinstance(v,list):return [clean(x) for x in v]
    if isinstance(v,(np.integer,)):return int(v)
    if isinstance(v,(np.floating,float)):return float(v) if math.isfinite(float(v)) else None
    return v

def main():
    fs={s:p.build_season(s) for s in (2021,2022,2023,2024,2025,2026)}
    tr=pd.concat([fs[2021],fs[2022],fs[2023]],ignore_index=True);v=fs[2024];h=fs[2025];f=fs[2026]
    # 2024 sanity check only; no meta-selection here.
    cm24=c.fit(tr,COMBO_FEATURES,ALPHA);pm24=p.fit(tr,POWER_FEATURES,ALPHA)
    sanity={}
    for w in WEIGHTS:
        pp=v.baseline.to_numpy(float)+w*residual_predict(v,cm24,'combo')+(1-w)*residual_predict(v,pm24,'power');sanity[w]=metrics(v,pp)
    # Meta-selection happens on 2025 after refitting both frozen component models through 2024.
    t25=pd.concat([tr,v],ignore_index=True);cm25=c.fit(t25,COMBO_FEATURES,ALPHA);pm25=p.fit(t25,POWER_FEATURES,ALPHA);cands=[]
    for w in WEIGHTS:
        pp=h.baseline.to_numpy(float)+w*residual_predict(h,cm25,'combo')+(1-w)*residual_predict(h,pm25,'power');m=metrics(h,pp);cands.append({'combo_weight':w,'power_weight':1-w,'selection_2025':m})
    cands.sort(key=lambda r:key(r['selection_2025']),reverse=True);chosen=cands[0]
    # Final 2026 holdout: exact chosen weight, components refit through 2025.
    t26=pd.concat([t25,h],ignore_index=True);cm26=c.fit(t26,COMBO_FEATURES,ALPHA);pm26=p.fit(t26,POWER_FEATURES,ALPHA);w=chosen['combo_weight'];fp=f.baseline.to_numpy(float)+w*residual_predict(f,cm26,'combo')+(1-w)*residual_predict(f,pm26,'power');fm=metrics(f,fp)
    base={2024:metrics(v,v.baseline),2025:metrics(h,h.baseline),2026:metrics(f,f.baseline)}
    out={'market_predictor':False,'protocol':{'component_selection':'fixed from prior 2024 audits','blend_selection':2025,'final_holdout':2026},'baseline':base,'sanity_2024':sanity,'blend_candidates_2025':cands,'chosen':chosen,'holdout_2026':fm};(OUT/'cfb_stacked_adjusted_edge_search.json').write_text(json.dumps(clean(out),indent=2,allow_nan=False))
    lines=['# CFB Stacked Adjusted Edge Search','',f"2025-selected blend: combined={w:.2f}, power-confirmation={1-w:.2f}",'','| Season | Model | Steps | slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |','|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for yr,m in [(2025,chosen['selection_2025']),(2026,fm)]:
        b=base[yr]
        for label,x in [('Baseline',b),('Stacked',m)]:lines.append(f"| {yr} | {label} | {x['steps']}/{x['possible']} | {100*x['slope']:+.1f}pp | {x['auc']:.3f} | {x['mae']:.3f} | "+' | '.join(rt(x,t) for t in TH)+' |')
    lines+=['','## Fixed blend candidates on 2025','','| Combined weight | Power weight | Steps | slope | 10+ | AUC | MAE |','|---:|---:|---:|---:|---:|---:|---:|']
    for r in cands:
        m=r['selection_2025'];lines.append(f"| {r['combo_weight']:.2f} | {r['power_weight']:.2f} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {100*(m['high'] or 0):.1f}% | {m['auc']:.3f} | {m['mae']:.3f} |")
    (OUT/'CFB_STACKED_ADJUSTED_EDGE_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
