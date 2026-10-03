"""Conservative nonlinear residual models over leakage-safe adjusted CFB features.

Features are football-only: contemporaneous FPI Game Control/in-game WP,
opponent-adjusted EPA/FEI, and competitive-state PBP efficiency. Models predict
actual-margin residuals relative to the existing independent baseline. Sportsbook
lines are evaluation-only. 2021-23 train, 2024 model selection, 2025 and 2026
sequential holdouts with the exact selected architecture.
"""
from __future__ import annotations
import json,math
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, ExtraTreesRegressor, RandomForestRegressor
from research import cfb_combined_adjusted_edge_search as c

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TRAIN=(2021,2022,2023);VALID=2024;HOLD=2025;FINAL=2026
CAPS=(2.,4.,6.,8.);TH=(2.,4.,6.,8.,10.)
FEATURES=c.FPI_CONTROL+c.ADJ+c.COMP
SPECS=[
 ('hist_d2_l20',lambda:HistGradientBoostingRegressor(max_iter=150,learning_rate=.04,max_leaf_nodes=7,max_depth=2,min_samples_leaf=20,l2_regularization=4.,random_state=17)),
 ('hist_d2_l40',lambda:HistGradientBoostingRegressor(max_iter=180,learning_rate=.035,max_leaf_nodes=7,max_depth=2,min_samples_leaf=40,l2_regularization=8.,random_state=17)),
 ('hist_d3_l30',lambda:HistGradientBoostingRegressor(max_iter=150,learning_rate=.035,max_leaf_nodes=11,max_depth=3,min_samples_leaf=30,l2_regularization=8.,random_state=17)),
 ('extra_d4_l20',lambda:ExtraTreesRegressor(n_estimators=350,max_depth=4,min_samples_leaf=20,max_features=.7,random_state=17,n_jobs=-1)),
 ('rf_d4_l20',lambda:RandomForestRegressor(n_estimators=350,max_depth=4,min_samples_leaf=20,max_features=.7,random_state=17,n_jobs=-1)),
]

def prep_fit(df):
    x=df[FEATURES].apply(pd.to_numeric,errors='coerce');med=x.median();return x.fillna(med).to_numpy(float),med
def prep_pred(df,med):return df[FEATURES].apply(pd.to_numeric,errors='coerce').fillna(med).to_numpy(float)
def fit_model(train,factory):
    x,med=prep_fit(train);y=(train.actual-train.baseline).to_numpy(float);m=factory();m.fit(x,y);return m,med
def predict(df,m,med,cap):return df.baseline.to_numpy(float)+np.clip(np.asarray(m.predict(prep_pred(df,med)),float),-cap,cap)
def metrics(df,p):return c.metrics(df,p)
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
    fs={s:c.build_season(s) for s in (*TRAIN,VALID,HOLD,FINAL)};df=pd.concat(fs.values(),ignore_index=True);tr=df[df.season.isin(TRAIN)].copy();va=df[df.season==VALID].copy();ho=df[df.season==HOLD].copy();fi=df[df.season==FINAL].copy();base={2024:metrics(va,va.baseline),2025:metrics(ho,ho.baseline),2026:metrics(fi,fi.baseline)};cand=[]
    for name,factory in SPECS:
        m,med=fit_model(tr,factory)
        for cap in CAPS:cand.append({'model':name,'cap':cap,'validation':metrics(va,predict(va,m,med,cap))})
    cand.sort(key=lambda r:key(r['validation']),reverse=True);ch=cand[0];factory=dict(SPECS)[ch['model']]
    m25,med25=fit_model(pd.concat([tr,va]),factory);r25=metrics(ho,predict(ho,m25,med25,ch['cap']));m26,med26=fit_model(pd.concat([tr,va,ho]),factory);r26=metrics(fi,predict(fi,m26,med26,ch['cap']))
    out={'market_predictor':False,'features':FEATURES,'chosen_on_2024':ch,'baseline':base,'holdout_2025':r25,'holdout_2026':r26,'candidates':cand};(OUT/'cfb_nonlinear_adjusted_edge_search.json').write_text(json.dumps(clean(out),indent=2,allow_nan=False))
    lines=['# CFB Nonlinear Adjusted Edge Search','',f"Chosen on 2024: {ch['model']} cap={ch['cap']}",'','| Season | Model | Steps | slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |','|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for yr,b,q in [(2024,base[2024],ch['validation']),(2025,base[2025],r25),(2026,base[2026],r26)]:
        for label,m in [('Baseline',b),('Nonlinear adjusted',q)]:lines.append(f"| {yr} | {label} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} | {m['mae']:.3f} | "+' | '.join(rt(m,t) for t in TH)+' |')
    lines+=['','## 2024 candidates','','| Model | Cap | Steps | slope | 10+ | AUC | MAE |','|---|---:|---:|---:|---:|---:|---:|']
    for r in cand:
        m=r['validation'];lines.append(f"| {r['model']} | {r['cap']:.0f} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {100*(m['high'] or 0):.1f}% | {m['auc']:.3f} | {m['mae']:.3f} |")
    (OUT/'CFB_NONLINEAR_ADJUSTED_EDGE_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
