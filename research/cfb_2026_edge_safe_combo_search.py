"""Targeted 2026 CFB combination search around the four-variable anchor + offensive havoc.

Pure-MAE greedy additions are intentionally excluded when they destroy edge ordering.
We require football-only candidates to preserve a monotonic/high-quality edge curve
while improving leave-one-week-out MAE over Weeks 2-4.
"""
from __future__ import annotations
import itertools, json, math
from pathlib import Path
import numpy as np
from research import cfb_2026_anchor_extension_search as ext

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
BASE5=ext.ANCHOR+['havoc_off_diff']
POOL=[
 'avgsosrank_diff','third_down_def_diff','opportunity_off_diff','def_rush_epa_edge',
 'adjavgingamewp_diff','def_success_edge','avg_talent_composite','def_epa_edge',
 'havoc_def_diff','nonexplosive_epa_def_diff','adj_st_epa_diff','late_down_def_diff',
 'gamecontrol_x_explosive','line_yards_off_diff','plays_game_off_diff'
]
ALPHAS=(4.,16.,64.,256.);CAPS=(4.,6.,8.)

def clean(v):
    if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
    if isinstance(v,list):return [clean(x) for x in v]
    if isinstance(v,(np.integer,)):return int(v)
    if isinstance(v,(np.floating,float)):return float(v) if math.isfinite(float(v)) else None
    return v

def eval_grid(df,features):
    out=[]
    for a in ALPHAS:
      for c in CAPS:
        try:m,folds=ext.blocked_oof(df,features,a,c)
        except Exception:continue
        out.append({'features':features,'alpha':a,'cap':c,'metrics':m,'folds':folds})
    return out

def best_anchor(df):
    trials=eval_grid(df,BASE5)
    # must retain 4/4; then choose MAE, slope, AUC
    good=[r for r in trials if r['metrics']['steps']==r['metrics']['possible']==4]
    if not good:good=trials
    return min(good,key=lambda r:(r['metrics']['mae'],-r['metrics']['slope'],-(r['metrics']['auc'] or -1)))

def fold_maes(r):return {x['week']:x['challenger']['mae'] for x in r['folds']}

def qualifies(r,base):
    m=r['metrics'];b=base['metrics'];fm=fold_maes(r);fb=fold_maes(base)
    # Preserve the intended edge hierarchy and improve prediction accuracy.
    edge_ok=(m['steps']==m['possible']==4 and m['slope']>=0.15 and (m['high'] or 0)>=0.60)
    mae_ok=m['mae']<b['mae']-0.01
    week_ok=sum(fm[w] < fb[w]-1e-6 for w in fm)>=2
    return edge_ok and mae_ok and week_ok

def key(r):
    m=r['metrics'];return (m['mae'],-m['slope'],-(m['high'] or 0),-(m['auc'] or 0),len(r['features']))

def rt(m,t):return ext.rt(m,t)

def main():
    df,_=ext.prepare();pool=[f for f in POOL if f in df.columns and df[f].notna().any()]
    base=best_anchor(df);cands=[]
    # one and two further additions around anchor+havoc
    for k in (1,2):
      for add in itertools.combinations(pool,k):
        feats=BASE5+list(add)
        trials=eval_grid(df,feats)
        if not trials:continue
        valid=[r for r in trials if qualifies(r,base)]
        if valid:
          b=min(valid,key=key);b['added']=list(add);cands.append(b)
    cands.sort(key=key)
    chosen=cands[0] if cands else base
    out={'season':2026,'market_predictor':False,'base5':base,'qualified_candidates':cands,'chosen':chosen}
    (OUT/'cfb_2026_edge_safe_combo_search.json').write_text(json.dumps(clean(out),indent=2,allow_nan=False))
    lines=['# CFB 2026 Edge-Safe Combination Search','',f"Base five: **{', '.join(BASE5)}**",'',
      '| Model | Features | MAE | Steps | slope | AUC | 2+ | 4+ | 6+ | 8+ | 10+ |','|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for label,r in [('Anchor + havoc',base),('Chosen',chosen)]:
      m=r['metrics'];lines.append(f"| {label} | {', '.join(r['features'])} | {m['mae']:.3f} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} | "+' | '.join(rt(m,t) for t in ext.reg.TH)+' |')
    lines+=['','## Qualified edge-safe additions','','| Added | MAE | slope | 10+ | AUC |','|---|---:|---:|---:|---:|']
    for r in cands[:20]:
      m=r['metrics'];lines.append(f"| {', '.join(r['added'])} | {m['mae']:.3f} | {100*m['slope']:+.1f}pp | {100*(m['high'] or 0):.1f}% | {m['auc']:.3f} |")
    (OUT/'CFB_2026_EDGE_SAFE_COMBO_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
