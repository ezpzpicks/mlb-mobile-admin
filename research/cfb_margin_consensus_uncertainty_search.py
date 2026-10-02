"""Use football-model disagreement as uncertainty to improve ATS edge ordering.

Alternative independent margin regressions are fit only to actual football margins.
The production-like baseline is unchanged when models agree; when their predictions
diverge, candidate rules shrink the baseline toward football-model consensus. 2024
selects a predeclared rule by fixed 2/4/6/8/10 monotonic edge quality; 2025 confirms.
Market spreads are evaluation-only and never enter the adjustment formula.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_margin_ensemble_edge_quality as ens
base=ens.base
OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TH=(2.,4.,6.,8.,10.); TRAIN=ens.TRAIN;VALID=ens.VALID;HOLD=ens.HOLD
ALT_FAMILIES=('prior_core','power_core','scoring_core','current_core','no_power')
DISAGREE=(2.,3.,4.,5.,6.,8.,10.)
BLENDS=(.15,.25,.35,.5,.65,.8,1.)

def auc(scores,labels):
    scores=np.asarray(scores,float);labels=np.asarray(labels,int);n1=int((labels==1).sum());n0=int((labels==0).sum())
    if not n1 or not n0:return None
    ranks=pd.Series(scores).rank(method='average').to_numpy();return float((ranks[labels==1].sum()-n1*(n1+1)/2)/(n1*n0))
def met(df,p):
    p=np.asarray(p,float);e=p+df.market_home_spread.to_numpy(float);miss=df.actual.to_numpy(float)+df.market_home_spread.to_numpy(float);ok=(np.abs(e)>1e-9)&(np.abs(miss)>1e-9);e=e[ok];miss=miss[ok];w=(e*miss>0).astype(int);rates=[]
    for t in TH:
        s=np.abs(e)>=t;n=int(s.sum());ww=int(w[s].sum());rates.append({'threshold':t,'n':n,'wins':ww,'losses':n-ww,'win_rate':ww/n if n else None})
    valid=[r for r in rates if r['n']>=25 and r['win_rate'] is not None];steps=sum(b['win_rate']>=a['win_rate'] for a,b in zip(valid,valid[1:]));viol=sum(max(0.,a['win_rate']-b['win_rate']) for a,b in zip(valid,valid[1:]));slope=valid[-1]['win_rate']-valid[0]['win_rate'] if len(valid)>=2 else -1.;high=next((r['win_rate'] for r in rates if r['threshold']==10. and r['n']>=25),None)
    return {'auc':auc(np.abs(e),w),'corr':float(np.corrcoef(e,miss)[0,1]) if len(e)>2 else None,'mae':float(np.mean(np.abs(p-df.actual.to_numpy(float)))),'thresholds':rates,'monotonic_steps':int(steps),'monotonic_possible':max(0,len(valid)-1),'violation_sum':float(viol),'slope_2_to_10':float(slope),'high_edge_win_rate':high}
def key(m):return (m['monotonic_steps'],-m['violation_sum'],m['slope_2_to_10'],m['high_edge_win_rate'] if m['high_edge_win_rate'] is not None else -1.,m['auc'] or -1.,m['corr'] or -1.)
def alt_predictions(train,test):
    out=[]
    for fam in ALT_FAMILIES:
        # Fixed moderate ridge; uncertainty test is about agreement, not tuning each submodel to ATS.
        m=ens.fit(train,ens.FEATURES[fam],16.);out.append(ens.pred(test,m))
    return np.column_stack(out)
def adjust(baseline,alts,threshold,blend,consensus_kind='median'):
    baseline=np.asarray(baseline,float);stack=np.column_stack([baseline,alts]);center=np.median(stack,axis=1) if consensus_kind=='median' else np.mean(stack,axis=1);dis=np.std(stack,axis=1,ddof=0);factor=np.clip((dis-threshold)/max(threshold,1e-6),0.,1.);return baseline+(center-baseline)*(blend*factor)
def main():
    frames=[ens.season_rows(s) for s in (*TRAIN,VALID,HOLD)];df=pd.concat(frames,ignore_index=True);tr=df[df.season.isin(TRAIN)];va=df[df.season==VALID];ho=df[df.season==HOLD];bv=met(va,va.baseline);bh=met(ho,ho.baseline);va_alt=alt_predictions(tr,va);tv=pd.concat([tr,va],ignore_index=True);ho_alt=alt_predictions(tv,ho);rows=[]
    for kind in ('median','mean'):
      for d in DISAGREE:
       for w in BLENDS:
        vm=met(va,adjust(va.baseline,va_alt,d,w,kind));rows.append({'kind':kind,'disagreement_threshold':d,'blend':w,'validation':vm})
    rows.sort(key=lambda r:key(r['validation']),reverse=True)
    for r in rows:
        hm=met(ho,adjust(ho.baseline,ho_alt,r['disagreement_threshold'],r['blend'],r['kind']));r['holdout']=hm;r['validation_auc_gain']=(r['validation']['auc'] or 0)-(bv['auc'] or 0);r['holdout_auc_gain']=(hm['auc'] or 0)-(bh['auc'] or 0)
    chosen=rows[0];out={'baseline':{'validation':bv,'holdout':bh},'chosen_on_2024':chosen,'candidates':rows};(OUT/'cfb_margin_consensus_uncertainty_search.json').write_text(json.dumps(out,indent=2,allow_nan=False))
    lines=['# CFB Consensus-Uncertainty Edge Search','',f"Baseline 2024: {bv['monotonic_steps']}/{bv['monotonic_possible']} steps, slope {100*bv['slope_2_to_10']:+.1f}pp, AUC {bv['auc']:.3f}",f"Baseline 2025: {bh['monotonic_steps']}/{bh['monotonic_possible']} steps, slope {100*bh['slope_2_to_10']:+.1f}pp, AUC {bh['auc']:.3f}",'','| Rule | 2024 steps | slope | 10+ | 2025 steps | slope | 10+ | 2025 AUC |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows[:20]:
        v,h=r['validation'],r['holdout'];lines.append(f"| {r['kind']} d>{r['disagreement_threshold']:g}, blend {r['blend']:.2f} | {v['monotonic_steps']}/{v['monotonic_possible']} | {100*v['slope_2_to_10']:+.1f}pp | {100*v['high_edge_win_rate']:.1f}% | {h['monotonic_steps']}/{h['monotonic_possible']} | {100*h['slope_2_to_10']:+.1f}pp | {100*h['high_edge_win_rate']:.1f}% | {h['auc']:.3f} |")
    lines+=['','## 2024-selected rule',f"- {chosen['kind']} consensus; disagreement>{chosen['disagreement_threshold']}; blend={chosen['blend']}",'','| Edge | Baseline 2025 | Candidate 2025 |','|---:|---:|---:|']
    for b,c in zip(bh['thresholds'],chosen['holdout']['thresholds']):lines.append(f"| {b['threshold']:.0f}+ | {b['wins']}-{b['losses']} ({100*b['win_rate']:.1f}%) | {c['wins']}-{c['losses']} ({100*c['win_rate']:.1f}%) |")
    (OUT/'CFB_MARGIN_CONSENSUS_UNCERTAINTY_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
