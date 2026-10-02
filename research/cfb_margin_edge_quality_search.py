"""Search football-only CFB margin corrections for better ATS edge ordering.

The sportsbook spread is evaluation/model-selection only, never a predictor.
Residual coefficients are fit to actual margin using 2021-23. Candidate family,
ridge and cap are selected on 2024 by predeclared edge-quality metrics, then
2025 remains untouched confirmation.
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd

# Chain the previously defined leakage-safe football feature builders.
from research import cfb_margin_advanced_residual_regression as adv
from research import cfb_margin_balance_interactions as bal
from research import cfb_margin_scoring_context_residual as ctx
base = adv.base
# Older historical research predates this live-builder field. Use the same
# neutral value as production so early/missing rows do not receive fake signal.
base.METRIC_DEFAULTS.setdefault('Power Success', 0.68)

TRAIN=(2021,2022,2023); VALID=2024; HOLDOUT=2025
ALPHAS=(1.0,4.0,16.0,64.0,256.0)
CAPS=(2.0,3.0,4.0,5.0,6.0,8.0)
THRESHOLDS=(2.0,4.0,6.0,8.0,10.0)
OUT=Path('research/results'); OUT.mkdir(parents=True,exist_ok=True)

# Preserve evaluation-only spread in the assembled feature row.
_orig=base.build_feature_row
def build_row(game,prior_stats,current_stats,prior_metrics,current_metrics):
    row=_orig(game,prior_stats,current_stats,prior_metrics,current_metrics)
    row['market_home_spread']=float(base.num(game.get('market_home_spread'),np.nan))
    return row
base.build_feature_row=build_row

FAMILIES={}
FAMILIES.update({f'advanced::{k}':v for k,v in adv.FAMILIES.items()})
FAMILIES.update({f'balance::{k}':v for k,v in bal.FAMILIES.items()})
FAMILIES.update({f'context::{k}':v for k,v in ctx.FAMILIES.items()})

def fbs_ids(season:int)->set[str]:
    try:
        g=base.cfb._parse_games(base.cfb._espn_games_payload(season),season)
        if g is None or g.empty:return set()
        m=g['Away Classification'].astype(str).str.lower().eq('fbs') & g['Home Classification'].astype(str).str.lower().eq('fbs')
        return set(g.loc[m,'Game ID'].astype(str))
    except Exception as exc:
        print('FBS filter failed',season,exc); return set()

def auc_rank(scores:np.ndarray, labels:np.ndarray):
    ok=np.isfinite(scores)&np.isfinite(labels)
    scores=scores[ok]; labels=labels[ok].astype(int)
    n1=int((labels==1).sum()); n0=int((labels==0).sum())
    if not n1 or not n0:return None
    ranks=pd.Series(scores).rank(method='average').to_numpy(float)
    return float((ranks[labels==1].sum()-n1*(n1+1)/2)/(n1*n0))

def edge_metrics(frame:pd.DataFrame,pred:np.ndarray):
    actual=frame.actual_margin.to_numpy(float); hs=frame.market_home_spread.to_numpy(float)
    market_margin=-hs; edge=pred-market_margin; miss=actual-market_margin
    valid=np.isfinite(edge)&np.isfinite(miss)&(np.abs(edge)>1e-9)&(np.abs(miss)>1e-9)
    e=edge[valid]; m=miss[valid]
    win=(e*m>0).astype(int)
    auc=auc_rank(np.abs(e),win)
    corr=float(np.corrcoef(e,m)[0,1]) if len(e)>2 else None
    rates=[]
    for t in THRESHOLDS:
        s=np.abs(e)>=t; n=int(s.sum()); w=int(win[s].sum()); l=n-w
        rates.append({'threshold':t,'n':n,'wins':w,'losses':l,'win_rate':float(w/n) if n else None})
    valid_rates=[r for r in rates if r['n']>=25]
    mono=sum(1 for a,b in zip(valid_rates,valid_rates[1:]) if b['win_rate']>=a['win_rate'])
    mae=float(np.mean(np.abs(pred-actual)))
    return {'n':int(len(e)),'auc':auc,'corr':corr,'mae':mae,'thresholds':rates,'monotonic_steps':mono,'monotonic_possible':max(0,len(valid_rates)-1)}

def selection_key(m):
    # Primary target is ordering: winning ATS calls should carry larger edge.
    # Fixed-threshold monotonicity and signed-edge correlation break ties.
    return (m['auc'] if m['auc'] is not None else -1.0,
            m['monotonic_steps'],
            m['corr'] if m['corr'] is not None else -1.0,
            next((r['win_rate'] for r in m['thresholds'] if r['threshold']==6.0 and r['win_rate'] is not None),0.0))

def main():
    df=base.build_dataset().copy()
    df['market_home_spread']=pd.to_numeric(df['market_home_spread'],errors='coerce')
    df['actual_margin']=pd.to_numeric(df['actual_margin'],errors='coerce')
    df['spread_margin']=pd.to_numeric(df['spread_margin'],errors='coerce')
    ids={s:fbs_ids(s) for s in (*TRAIN,VALID,HOLDOUT)}
    if all(ids.values()):
        df=df[df.apply(lambda r:str(r.game_id) in ids.get(int(r.season),set()),axis=1)].copy()
    df=df.dropna(subset=['market_home_spread','actual_margin','spread_margin']).reset_index(drop=True)
    tr=df[df.season.isin(TRAIN)].copy(); va=df[df.season==VALID].copy(); ho=df[df.season==HOLDOUT].copy()
    baseline_v=edge_metrics(va,va.spread_margin.to_numpy(float)); baseline_h=edge_metrics(ho,ho.spread_margin.to_numpy(float))
    print('rows',len(tr),len(va),len(ho)); print('baseline',baseline_v,baseline_h)
    selected=[]
    for name,fs in FAMILIES.items():
        if any(f not in df.columns for f in fs):
            continue
        best=None
        for alpha in ALPHAS:
            model=adv.fit(tr,fs,alpha)
            for cap in CAPS:
                pred=va.spread_margin.to_numpy(float)+adv.correction(va,model,cap)
                m=edge_metrics(va,pred)
                row={'family':name,'features':fs,'alpha':alpha,'cap':cap,'validation':m}
                if best is None or selection_key(m)>selection_key(best['validation']):best=row
        selected.append(best)
    selected.sort(key=lambda r:selection_key(r['validation']),reverse=True)
    tv=pd.concat([tr,va],ignore_index=True); results=[]
    for r in selected:
        model=adv.fit(tv,r['features'],r['alpha'])
        hp=ho.spread_margin.to_numpy(float)+adv.correction(ho,model,r['cap'])
        hm=edge_metrics(ho,hp)
        results.append({**r,'holdout':hm,
            'validation_auc_gain':(r['validation']['auc'] or 0)-(baseline_v['auc'] or 0),
            'holdout_auc_gain':(hm['auc'] or 0)-(baseline_h['auc'] or 0),
            'validation_corr_gain':(r['validation']['corr'] or 0)-(baseline_v['corr'] or 0),
            'holdout_corr_gain':(hm['corr'] or 0)-(baseline_h['corr'] or 0)})
    chosen=results[0] if results else None
    out={'protocol':{'train':list(TRAIN),'validation':VALID,'holdout':HOLDOUT,'market_predictor':False,'market_use':'2024 edge-quality model selection only; 2025 untouched','fixed_thresholds':list(THRESHOLDS),'primary_selection_metric':'ATS edge AUC'},'dataset':{'train':len(tr),'validation':len(va),'holdout':len(ho)},'baseline':{'validation':baseline_v,'holdout':baseline_h},'chosen_on_2024':chosen,'candidates':results}
    (OUT/'cfb_margin_edge_quality_search.json').write_text(json.dumps(out,indent=2,allow_nan=False))
    lines=['# CFB Margin Edge-Quality Search','',f"FBS rows: train {len(tr)}, validation {len(va)}, holdout {len(ho)}",'',f"Baseline 2024 AUC: {baseline_v['auc']:.3f}; corr {baseline_v['corr']:.3f}; MAE {baseline_v['mae']:.3f}",f"Baseline 2025 AUC: {baseline_h['auc']:.3f}; corr {baseline_h['corr']:.3f}; MAE {baseline_h['mae']:.3f}",'','| Family | 2024 AUC | Δ | 2024 corr | 2025 AUC | Δ | 2025 corr | 2025 MAE |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in results[:20]:
        v,h=r['validation'],r['holdout'];lines.append(f"| {r['family']} | {v['auc']:.3f} | {r['validation_auc_gain']:+.3f} | {v['corr']:.3f} | {h['auc']:.3f} | {r['holdout_auc_gain']:+.3f} | {h['corr']:.3f} | {h['mae']:.3f} |")
    if chosen:
        lines += ['','## 2024-selected candidate',f"- {chosen['family']} alpha={chosen['alpha']} cap={chosen['cap']}",'','### Fixed threshold records','', '| Edge | Baseline 2025 | Candidate 2025 |','|---:|---:|---:|']
        for b,c in zip(baseline_h['thresholds'],chosen['holdout']['thresholds']):
            lines.append(f"| {b['threshold']:.0f}+ | {b['wins']}-{b['losses']} ({100*b['win_rate']:.1f}%) | {c['wins']}-{c['losses']} ({100*c['win_rate']:.1f}%) |")
    (OUT/'CFB_MARGIN_EDGE_QUALITY_SEARCH.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
if __name__=='__main__':main()
