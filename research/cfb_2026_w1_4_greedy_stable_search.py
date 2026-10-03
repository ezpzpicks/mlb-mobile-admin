"""Find a small stable 2026 CFB correction using only Weeks 1-4.

This is a model-development CV for predicting Week 5+, not a replay of what was
known in Week 2. Pregame features remain leakage-safe for each held-out game, but
model coefficients are selected with week-blocked CV across held-out Weeks 2,3,4.
Market spreads remain evaluation-only.

Acceptance preference:
1) lower MAE than baseline in at least 2/3 held-out weeks,
2) lower pooled OOF MAE,
3) non-worse pooled edge slope/order,
4) keep the feature set small.
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_2026_w1_4_regime_search as reg

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
FOLDS=(2,3,4); ALPHAS=(16.,64.,256.); CAPS=(2.,4.,6.)
POOL=[]
for fam,fs in reg.FAMILIES.items():
    POOL.extend(fs)
# Structural, market-independent calibration terms.
POOL += ['baseline','abs_baseline','baseline_sq']
POOL=list(dict.fromkeys(POOL))

def prepare():
    df=reg.build_2026().copy()
    df['abs_baseline']=pd.to_numeric(df.baseline,errors='coerce').abs()
    df['baseline_sq']=pd.to_numeric(df.baseline,errors='coerce')**2
    return df

def fit(train,features,alpha):return reg.fit(train,features,alpha)
def pred(df,m,cap):return reg.pred(df,m,cap)
def metrics(df,p):return reg.metrics(df,p,min_bucket=8)

def blocked_oof(df,features,alpha,cap):
    parts=[]; fold=[]
    for w in FOLDS:
        tr=df[df.week!=w].copy(); te=df[df.week==w].copy()
        # Week 1 stays in every training fold as preseason/static evidence.
        if tr.empty or te.empty:continue
        m=fit(tr,features,alpha); q=te.copy(); q['_pred']=pred(te,m,cap); parts.append(q)
        bm=metrics(te,pd.to_numeric(te.baseline,errors='coerce').to_numpy(float)); cm=metrics(te,q._pred.to_numpy(float))
        fold.append({'week':w,'baseline':bm,'challenger':cm,'mae_improved':cm['mae']<bm['mae']})
    oof=pd.concat(parts,ignore_index=True); return metrics(oof,oof._pred.to_numpy(float)),fold,oof

def baseline_pool(df):
    q=df[df.week.isin(FOLDS)].copy();return metrics(q,pd.to_numeric(q.baseline,errors='coerce').to_numpy(float))

def score(m,folds,base_pool,nfeat):
    improved=sum(bool(x['mae_improved']) for x in folds)
    mae_gain=base_pool['mae']-m['mae']
    slope_gain=m['slope']-base_pool['slope']
    edge_ok=1 if slope_gain>=-1e-9 else 0
    return (improved,1 if mae_gain>0 else 0,edge_ok,mae_gain,m['steps'],-m['violation'],m['slope'],m['auc'] if m['auc'] is not None else -1.,-nfeat)

def clean(v):
    if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
    if isinstance(v,list):return [clean(x) for x in v]
    if isinstance(v,(np.integer,)):return int(v)
    if isinstance(v,(np.floating,float)):return float(v) if math.isfinite(float(v)) else None
    return v

def rt(m,t):return reg.rt(m,t)

def main():
    df=prepare(); base=baseline_pool(df)
    usable=[f for f in POOL if f in df.columns and df[f].notna().any()]
    selected=[]; remaining=usable.copy(); path=[]; best_global=None
    for step in range(1,7):
        trials=[]
        for f in remaining:
            feats=selected+[f]
            for a in ALPHAS:
                for c in CAPS:
                    try:m,folds,_=blocked_oof(df,feats,a,c)
                    except Exception:continue
                    trials.append({'features':feats.copy(),'added':f,'alpha':a,'cap':c,'metrics':m,'folds':folds,'score':score(m,folds,base,len(feats))})
        if not trials:break
        trials.sort(key=lambda x:x['score'],reverse=True); b=trials[0]
        # Stop unless the new step improves pooled MAE and succeeds in >=2 folds.
        if (base['mae']-b['metrics']['mae'])<=0 or sum(x['mae_improved'] for x in b['folds'])<2:break
        if best_global is not None and b['metrics']['mae'] >= best_global['metrics']['mae']-0.01 and b['metrics']['slope'] <= best_global['metrics']['slope']+0.005:
            break
        selected=b['features'].copy();remaining=[x for x in remaining if x!=b['added']];path.append(b);best_global=b
    if best_global is None:
        # fall back to best one-feature trial for diagnostics
        trials=[]
        for f in usable:
            for a in ALPHAS:
                for c in CAPS:
                    m,folds,_=blocked_oof(df,[f],a,c);trials.append({'features':[f],'added':f,'alpha':a,'cap':c,'metrics':m,'folds':folds,'score':score(m,folds,base,1)})
        trials.sort(key=lambda x:x['score'],reverse=True);best_global=trials[0];path=[best_global]
    # Fit final Week5+ candidate on all Weeks 1-4. Do not grade this in-sample.
    final_model=fit(df,best_global['features'],best_global['alpha'])
    coefs={'intercept':float(final_model['b'][0])}
    for f,b in zip(best_global['features'],final_model['b'][1:]):coefs[f]=float(b)
    out={'season':2026,'usage':'candidate for Week 5+; no production change','market_predictor':False,'baseline_oof':base,'selected':best_global,'selection_path':path,'final_fit_standardized_coefficients':coefs}
    (OUT/'cfb_2026_w1_4_greedy_stable_search.json').write_text(json.dumps(clean(out),indent=2,allow_nan=False))
    lines=['# CFB 2026 Weeks 1-4 Stable Greedy Search','',f"Selected features: **{', '.join(best_global['features'])}**",f"alpha={best_global['alpha']:.0f}, cap={best_global['cap']:.0f}",'','| Model | MAE | Steps | slope | AUC | 2+ | 4+ | 6+ | 8+ | 10+ |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for label,m in [('Baseline',base),('Stable challenger',best_global['metrics'])]:lines.append(f"| {label} | {m['mae']:.3f} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} | "+' | '.join(rt(m,t) for t in reg.TH)+' |')
    lines+=['','## Held-out week MAE','','| Week | Baseline | Challenger | Improvement |','|---:|---:|---:|---:|']
    for x in best_global['folds']:
        gain=x['baseline']['mae']-x['challenger']['mae'];lines.append(f"| {x['week']} | {x['baseline']['mae']:.3f} | {x['challenger']['mae']:.3f} | {gain:+.3f} |")
    lines+=['','## Greedy path','','| Step | Added | Features | MAE | Steps | slope | AUC |','|---:|---|---|---:|---:|---:|---:|']
    for i,x in enumerate(path,1):
        m=x['metrics'];lines.append(f"| {i} | {x['added']} | {', '.join(x['features'])} | {m['mae']:.3f} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} |")
    lines+=['','## Final standardized coefficients (fit on W1-4)','','```json',json.dumps(coefs,indent=2),'```']
    (OUT/'CFB_2026_W1_4_GREEDY_STABLE_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))

if __name__=='__main__':main()
