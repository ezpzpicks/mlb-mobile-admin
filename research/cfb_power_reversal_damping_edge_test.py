"""Test football-only damping of contradictory current-power reversals.

2026 diagnostics suggested false large edges often occur when the current-season
power-gap adjustment points opposite the prior-season power gap.  This script
changes the model itself: when prior_power_margin * current_power_margin < 0,
scale only the current-power contribution. No sportsbook input is used in the
prediction formula.

2024 selects one predeclared damping multiplier using fixed 2/4/6/8/10 edge
progression. 2025 is confirmation. The frozen choice is then shown on 2026.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_margin_component_weight_edge_search as cw

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TH=(2.,4.,6.,8.,10.)
MULTS=(0.,.25,.5,.75,1.)


def auc(scores,labels):
    scores=np.asarray(scores,float);labels=np.asarray(labels,int);n1=int((labels==1).sum());n0=int((labels==0).sum())
    if not n1 or not n0:return None
    r=pd.Series(scores).rank(method='average').to_numpy();return float((r[labels==1].sum()-n1*(n1+1)/2)/(n1*n0))


def metrics(df,pred):
    pred=np.asarray(pred,float);actual=df.actual.to_numpy(float);edge=pred+df.market_home_spread.to_numpy(float);miss=actual+df.market_home_spread.to_numpy(float)
    ok=np.isfinite(edge)&np.isfinite(miss)&(np.abs(edge)>1e-9)&(np.abs(miss)>1e-9);edge=edge[ok];miss=miss[ok];win=(edge*miss>0).astype(int);rates=[]
    for t in TH:
        s=np.abs(edge)>=t;n=int(s.sum());w=int(win[s].sum());rates.append({'threshold':t,'n':n,'wins':w,'losses':n-w,'win_rate':w/n if n else None})
    valid=[x for x in rates if x['n']>=25];steps=sum(b['win_rate']>=a['win_rate'] for a,b in zip(valid,valid[1:]));viol=sum(max(0.,a['win_rate']-b['win_rate']) for a,b in zip(valid,valid[1:]));slope=valid[-1]['win_rate']-valid[0]['win_rate'] if len(valid)>1 else -1.;high=next((x['win_rate'] for x in rates if x['threshold']==10 and x['n']>=25),None)
    return {'n':len(edge),'auc':auc(np.abs(edge),win),'corr':float(np.corrcoef(edge,miss)[0,1]) if len(edge)>2 else None,'mae':float(np.mean(np.abs(pred-actual))),'steps':int(steps),'possible':max(0,len(valid)-1),'violation':float(viol),'slope':float(slope),'high':high,'thresholds':rates}


def key(m):
    return (m['steps'],-m['violation'],m['slope'],m['high'] if m['high'] is not None else -1.,m['auc'] if m['auc'] is not None else -1.)


def reconstruct(df,reversal_mult=1.0):
    p=np.zeros(len(df),float)
    for col,coef in cw.COMP.items():
        vals=pd.to_numeric(df[col],errors='coerce').fillna(0.).to_numpy(float)
        if col=='current_power_margin':
            prior=pd.to_numeric(df['prior_power_margin'],errors='coerce').fillna(0.).to_numpy(float)
            reversal=(prior*vals)<0
            vals=vals*np.where(reversal,float(reversal_mult),1.0)
        p += float(coef)*vals
    return p


def rec(m,t):
    r=next(x for x in m['thresholds'] if x['threshold']==t);return f"{r['wins']}-{r['losses']} ({100*r['win_rate']:.1f}%)"


def main():
    frames={s:cw.ens.season_rows(s) for s in (2024,2025,2026)}
    baseline={s:metrics(df,reconstruct(df,1.0)) for s,df in frames.items()}
    candidates=[]
    for mult in MULTS:
        m=metrics(frames[2024],reconstruct(frames[2024],mult));candidates.append({'reversal_multiplier':mult,'validation_2024':m})
    candidates.sort(key=lambda r:key(r['validation_2024']),reverse=True);chosen=candidates[0];mult=chosen['reversal_multiplier']
    test25=metrics(frames[2025],reconstruct(frames[2025],mult));test26=metrics(frames[2026],reconstruct(frames[2026],mult))
    out={'hypothesis':'scale current_power_margin only when it reverses prior_power_margin','market_predictor':False,'multipliers_tested':list(MULTS),'chosen_on_2024':chosen,'baseline':baseline,'confirmation_2025':test25,'descriptive_2026':test26}
    (OUT/'cfb_power_reversal_damping_edge_test.json').write_text(json.dumps(out,indent=2,allow_nan=False))
    lines=['# CFB Power-Reversal Damping Edge Test','',f"Chosen on 2024: current-power reversal × {mult:.2f}",'','| Season | Model | Steps | 2→10 slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |','|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for s,cm in [(2024,chosen['validation_2024']),(2025,test25),(2026,test26)]:
        for label,m in [('Baseline',baseline[s]),('Damped reversal',cm)]:
            lines.append(f"| {s} | {label} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} | {m['mae']:.3f} | "+' | '.join(rec(m,t) for t in TH)+' |')
    lines+=['','## 2024 candidate sweep','', '| Reversal multiplier | Steps | slope | 10+ | AUC | MAE |','|---:|---:|---:|---:|---:|---:|']
    for r in candidates:
        m=r['validation_2024'];lines.append(f"| {r['reversal_multiplier']:.2f} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {100*(m['high'] or 0):.1f}% | {m['auc']:.3f} | {m['mae']:.3f} |")
    (OUT/'CFB_POWER_REVERSAL_DAMPING_EDGE_TEST.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
