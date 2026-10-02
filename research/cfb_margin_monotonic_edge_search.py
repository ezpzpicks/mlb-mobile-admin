"""Select football-only CFB margin corrections for monotonic ATS edge quality.

Fixed checkpoints 2/4/6/8/10 are declared before selection. Football-only
residual coefficients fit 2021-23 actual margins. 2024 selects family/ridge/cap
by monotonic edge progression; 2025 remains untouched confirmation. Sportsbook
spread is evaluation-only and is never a prediction feature.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_margin_advanced_residual_regression as adv
from research import cfb_margin_balance_interactions as bal
from research import cfb_margin_scoring_context_residual as ctx
from research.cfb_historical_markets import attach_market_spread
base=adv.base
base.METRIC_DEFAULTS.setdefault('Power Success',0.68)
TRAIN=(2021,2022,2023);VALID=2024;HOLD=2025
ALPHAS=(1.,4.,16.,64.,256.);CAPS=(2.,3.,4.,5.,6.,8.);TH=(2.,4.,6.,8.,10.)
OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
FAMILIES={}
FAMILIES.update({f'advanced::{k}':v for k,v in adv.FAMILIES.items()})
FAMILIES.update({f'balance::{k}':v for k,v in bal.FAMILIES.items()})
FAMILIES.update({f'context::{k}':v for k,v in ctx.FAMILIES.items()})
_orig=base.build_feature_row
def build_row(game,prior_stats,current_stats,prior_metrics,current_metrics):
    row=_orig(game,prior_stats,current_stats,prior_metrics,current_metrics)
    row['market_home_spread']=float(base.num(game.get('market_home_spread'),np.nan));return row
base.build_feature_row=build_row

def fbs_ids(season):
    g=base.cfb._parse_games(base.cfb._espn_games_payload(int(season)),int(season))
    if g is None or g.empty:return set()
    m=g['Away Classification'].astype(str).str.lower().eq('fbs')&g['Home Classification'].astype(str).str.lower().eq('fbs')
    return set(g.loc[m,'Game ID'].astype(str))
def auc(scores,labels):
    scores=np.asarray(scores,float);labels=np.asarray(labels,int);n1=int((labels==1).sum());n0=int((labels==0).sum())
    if not n1 or not n0:return None
    ranks=pd.Series(scores).rank(method='average').to_numpy();return float((ranks[labels==1].sum()-n1*(n1+1)/2)/(n1*n0))
def met(df,p):
    p=np.asarray(p,float);actual=df.actual_margin.to_numpy(float);hs=df.market_home_spread.to_numpy(float);e=p+hs;miss=actual+hs
    ok=np.isfinite(e)&np.isfinite(miss)&(np.abs(e)>1e-9)&(np.abs(miss)>1e-9);e=e[ok];miss=miss[ok];win=(e*miss>0).astype(int)
    rates=[]
    for t in TH:
        s=np.abs(e)>=t;n=int(s.sum());w=int(win[s].sum());rates.append({'threshold':t,'n':n,'wins':w,'losses':n-w,'win_rate':w/n if n else None})
    valid=[r for r in rates if r['n']>=25 and r['win_rate'] is not None]
    steps=sum(b['win_rate']>=a['win_rate'] for a,b in zip(valid,valid[1:]));viol=sum(max(0.,a['win_rate']-b['win_rate']) for a,b in zip(valid,valid[1:]))
    slope=(valid[-1]['win_rate']-valid[0]['win_rate']) if len(valid)>=2 else -1.
    high=next((r['win_rate'] for r in rates if r['threshold']==10. and r['n']>=25),None)
    return {'auc':auc(np.abs(e),win),'corr':float(np.corrcoef(e,miss)[0,1]) if len(e)>2 else None,'mae':float(np.mean(np.abs(p-actual))),'thresholds':rates,'monotonic_steps':int(steps),'monotonic_possible':max(0,len(valid)-1),'violation_sum':float(viol),'slope_2_to_10':float(slope),'high_edge_win_rate':high}
def key(m):
    return (m['monotonic_steps'],-m['violation_sum'],m['slope_2_to_10'],m['high_edge_win_rate'] if m['high_edge_win_rate'] is not None else -1.,m['auc'] if m['auc'] is not None else -1.,m['corr'] if m['corr'] is not None else -1.)
def main():
    df=attach_market_spread(base.build_dataset().copy(),base.cfb)
    for c in ['market_home_spread','actual_margin','spread_margin']:df[c]=pd.to_numeric(df[c],errors='coerce')
    ids={s:fbs_ids(s) for s in (*TRAIN,VALID,HOLD)}
    if all(ids.values()):df=df[df.apply(lambda r:str(r.game_id) in ids.get(int(r.season),set()),axis=1)].copy()
    df=df.dropna(subset=['market_home_spread','actual_margin','spread_margin']).reset_index(drop=True)
    tr=df[df.season.isin(TRAIN)];va=df[df.season==VALID];ho=df[df.season==HOLD]
    bv=met(va,va.spread_margin);bh=met(ho,ho.spread_margin);selected=[]
    for name,fs in FAMILIES.items():
        if any(f not in df.columns for f in fs):continue
        best=None
        for alpha in ALPHAS:
            model=adv.fit(tr,fs,alpha)
            for cap in CAPS:
                m=met(va,va.spread_margin.to_numpy(float)+adv.correction(va,model,cap));r={'family':name,'features':fs,'alpha':alpha,'cap':cap,'validation':m}
                if best is None or key(m)>key(best['validation']):best=r
        selected.append(best)
    selected.sort(key=lambda r:key(r['validation']),reverse=True);tv=pd.concat([tr,va],ignore_index=True);res=[]
    for r in selected:
        model=adv.fit(tv,r['features'],r['alpha']);hp=ho.spread_margin.to_numpy(float)+adv.correction(ho,model,r['cap']);hm=met(ho,hp)
        res.append({**r,'holdout':hm,'validation_auc_gain':(r['validation']['auc'] or 0)-(bv['auc'] or 0),'holdout_auc_gain':(hm['auc'] or 0)-(bh['auc'] or 0)})
    chosen=res[0]
    out={'protocol':{'train':list(TRAIN),'validation':VALID,'holdout':HOLD,'market_predictor':False,'fixed_thresholds':list(TH),'selection':'monotonic steps, minimum violations, 2-to-10 slope, 10+ win rate, AUC, correlation'},'baseline':{'validation':bv,'holdout':bh},'chosen_on_2024':chosen,'candidates':res}
    (OUT/'cfb_margin_monotonic_edge_search.json').write_text(json.dumps(out,indent=2,allow_nan=False))
    lines=['# CFB Monotonic-First Margin Search','',f"Baseline 2024: steps {bv['monotonic_steps']}/{bv['monotonic_possible']}, slope {100*bv['slope_2_to_10']:+.1f}pp, AUC {bv['auc']:.3f}",f"Baseline 2025: steps {bh['monotonic_steps']}/{bh['monotonic_possible']}, slope {100*bh['slope_2_to_10']:+.1f}pp, AUC {bh['auc']:.3f}",'','| Family | 2024 steps | slope | 10+ | 2025 steps | slope | 10+ | 2025 AUC |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in res[:20]:
        v,h=r['validation'],r['holdout'];lines.append(f"| {r['family']} | {v['monotonic_steps']}/{v['monotonic_possible']} | {100*v['slope_2_to_10']:+.1f}pp | {100*v['high_edge_win_rate']:.1f}% | {h['monotonic_steps']}/{h['monotonic_possible']} | {100*h['slope_2_to_10']:+.1f}pp | {100*h['high_edge_win_rate']:.1f}% | {h['auc']:.3f} |")
    lines+=['','## 2024-selected candidate',f"- {chosen['family']} alpha={chosen['alpha']} cap={chosen['cap']}",'','| Edge | Baseline 2025 | Candidate 2025 |','|---:|---:|---:|']
    for b,c in zip(bh['thresholds'],chosen['holdout']['thresholds']):lines.append(f"| {b['threshold']:.0f}+ | {b['wins']}-{b['losses']} ({100*b['win_rate']:.1f}%) | {c['wins']}-{c['losses']} ({100*c['win_rate']:.1f}%) |")
    (OUT/'CFB_MARGIN_MONOTONIC_EDGE_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
