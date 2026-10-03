"""Ablate the locked four-variable 2026 CFB challenger and inspect coefficient stability.

Uses the same leave-one-week-out blocked CV over Weeks 2-4 as the anchor search.
Market spreads are evaluation-only.
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_2026_anchor_extension_search as ext

OUT=Path('research/results'); OUT.mkdir(parents=True,exist_ok=True)
ANCHOR=ext.ANCHOR
ALPHA=16.; CAP=6.

def clean(v):
    if isinstance(v,dict): return {k:clean(x) for k,x in v.items()}
    if isinstance(v,list): return [clean(x) for x in v]
    if isinstance(v,(np.integer,)): return int(v)
    if isinstance(v,(np.floating,float)): return float(v) if math.isfinite(float(v)) else None
    return v

def evaluate(df,features):
    m,folds=ext.blocked_oof(df,features,ALPHA,CAP)
    coefs=[]
    for w in ext.FOLDS:
        tr=df[df.week!=w].copy(); model=ext.fit(tr,features,ALPHA)
        coefs.append({'heldout_week':w,'intercept':float(model['b'][0]),**{f:float(b) for f,b in zip(features,model['b'][1:])}})
    return {'features':features,'metrics':m,'folds':folds,'fold_coefficients':coefs}

def rt(m,t): return ext.rt(m,t)

def main():
    df,_=ext.prepare()
    full=evaluate(df,ANCHOR)
    variants=[full]
    for drop in ANCHOR:
        variants.append(evaluate(df,[f for f in ANCHOR if f!=drop]) | {'dropped':drop})
    for f in ANCHOR:
        variants.append(evaluate(df,[f]) | {'single':f})
    # coefficient sign stability for full anchor
    sign={}
    for f in ANCHOR:
        vals=[x[f] for x in full['fold_coefficients'] if f in x]
        sign[f]={'values':vals,'same_sign':all(v>=0 for v in vals) or all(v<=0 for v in vals),'mean':float(np.mean(vals))}
    out={'season':2026,'market_predictor':False,'full_anchor':full,'variants':variants,'coefficient_sign_stability':sign}
    (OUT/'cfb_2026_anchor_ablation.json').write_text(json.dumps(clean(out),indent=2,allow_nan=False))
    lines=['# CFB 2026 Four-Variable Anchor Ablation','', '| Model | MAE | Steps | slope | 10+ | AUC |','|---|---:|---:|---:|---:|---:|']
    for v in variants[:5]:
        label='Full anchor' if v is full else f"Drop {v.get('dropped')}";m=v['metrics'];lines.append(f"| {label} | {m['mae']:.3f} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {100*(m['high'] or 0):.1f}% | {m['auc']:.3f} |")
    lines+=['','## Coefficient sign stability across held-out weeks','','| Feature | Fold coefficients | Same sign? | Mean |','|---|---|---|---:|']
    for f,s in sign.items(): lines.append(f"| {f} | {', '.join(f'{x:+.3f}' for x in s['values'])} | {'yes' if s['same_sign'] else 'no'} | {s['mean']:+.3f} |")
    (OUT/'CFB_2026_ANCHOR_ABLATION.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__': main()
