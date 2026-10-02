"""Sweep existing CFB margin-component weights for monotonic ATS edge quality.

The formula remains football-only. 2024 closing spreads select one predeclared
component multiplier using fixed 2/4/6/8/10 diagnostics; 2025 is untouched.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from builders import cfb_game_regression as gr
from research import cfb_margin_ensemble_edge_quality as ens
OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TH=(2.,4.,6.,8.,10.);MULT=(0.,.25,.5,.75,1.,1.25,1.5,1.75,2.)
COMP={
 'prior_ppg_diff':gr.TEAM_SCORE_COEFFICIENTS['prior_own_ppg'],
 'prior_papg_diff':gr.TEAM_SCORE_COEFFICIENTS['prior_opp_papg'],
 'prior_power_margin':gr.TEAM_SCORE_COEFFICIENTS['prior_power_gap'],
 'current_scoring_diff':gr.TEAM_SCORE_COEFFICIENTS['current_own_scoring_delta'],
 'current_allowed_diff':gr.TEAM_SCORE_COEFFICIENTS['current_opp_allowed_delta'],
 'current_power_margin':gr.TEAM_SCORE_COEFFICIENTS['current_power_delta'],
 'home_indicator':gr.TEAM_SCORE_COEFFICIENTS['home_indicator'],
}
def auc(s,l):
 l=np.asarray(l,int);s=np.asarray(s,float);n1=int((l==1).sum());n0=int((l==0).sum());
 if not n1 or not n0:return None
 r=pd.Series(s).rank(method='average').to_numpy();return float((r[l==1].sum()-n1*(n1+1)/2)/(n1*n0))
def met(df,p):
 p=np.asarray(p,float);e=p+df.market_home_spread.to_numpy(float);miss=df.actual.to_numpy(float)+df.market_home_spread.to_numpy(float);ok=(np.abs(e)>1e-9)&(np.abs(miss)>1e-9);e=e[ok];miss=miss[ok];w=(e*miss>0).astype(int);rates=[]
 for t in TH:
  s=np.abs(e)>=t;n=int(s.sum());ww=int(w[s].sum());rates.append({'threshold':t,'n':n,'wins':ww,'losses':n-ww,'win_rate':ww/n if n else None})
 valid=[r for r in rates if r['n']>=25];steps=sum(b['win_rate']>=a['win_rate'] for a,b in zip(valid,valid[1:]));viol=sum(max(0.,a['win_rate']-b['win_rate']) for a,b in zip(valid,valid[1:]));slope=valid[-1]['win_rate']-valid[0]['win_rate'] if len(valid)>1 else -1.;high=next((r['win_rate'] for r in rates if r['threshold']==10. and r['n']>=25),None)
 return {'auc':auc(np.abs(e),w),'corr':float(np.corrcoef(e,miss)[0,1]),'mae':float(np.mean(np.abs(p-df.actual.to_numpy(float)))),'thresholds':rates,'monotonic_steps':int(steps),'monotonic_possible':max(0,len(valid)-1),'violation_sum':float(viol),'slope_2_to_10':float(slope),'high_edge_win_rate':high}
def key(m):return (m['monotonic_steps'],-m['violation_sum'],m['slope_2_to_10'],m['high_edge_win_rate'] or -1.,m['auc'] or -1.)
def reconstruct(df,mults=None):
 mults=mults or {};p=np.zeros(len(df),float)
 for col,coef in COMP.items():p += coef*float(mults.get(col,1.))*df[col].to_numpy(float)
 return p
def main():
 va=ens.season_rows(2024);ho=ens.season_rows(2025);bv=met(va,reconstruct(va));bh=met(ho,reconstruct(ho));rows=[]
 for col in COMP:
  for m in MULT:
   mm=met(va,reconstruct(va,{col:m}));rows.append({'component':col,'multiplier':m,'validation':mm})
 rows.sort(key=lambda r:key(r['validation']),reverse=True)
 for r in rows:
  hm=met(ho,reconstruct(ho,{r['component']:r['multiplier']}));r['holdout']=hm
 chosen=rows[0];out={'baseline':{'validation':bv,'holdout':bh},'chosen_on_2024':chosen,'candidates':rows};(OUT/'cfb_margin_component_weight_edge_search.json').write_text(json.dumps(out,indent=2,allow_nan=False))
 lines=['# CFB Margin Component-Weight Edge Search','',f"Baseline 2024: {bv['monotonic_steps']}/{bv['monotonic_possible']} steps, slope {100*bv['slope_2_to_10']:+.1f}pp, AUC {bv['auc']:.3f}",f"Baseline 2025: {bh['monotonic_steps']}/{bh['monotonic_possible']} steps, slope {100*bh['slope_2_to_10']:+.1f}pp, AUC {bh['auc']:.3f}",'','| Component | Mult | 2024 steps | slope | 10+ | 2025 steps | slope | 10+ | 2025 AUC |','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
 for r in rows[:25]:
  v,h=r['validation'],r['holdout'];lines.append(f"| {r['component']} | {r['multiplier']:.2f} | {v['monotonic_steps']}/{v['monotonic_possible']} | {100*v['slope_2_to_10']:+.1f}pp | {100*v['high_edge_win_rate']:.1f}% | {h['monotonic_steps']}/{h['monotonic_possible']} | {100*h['slope_2_to_10']:+.1f}pp | {100*h['high_edge_win_rate']:.1f}% | {h['auc']:.3f} |")
 lines+=['','## 2024-selected change',f"- {chosen['component']} × {chosen['multiplier']}",'','| Edge | Baseline 2025 | Candidate 2025 |','|---:|---:|---:|']
 for b,c in zip(bh['thresholds'],chosen['holdout']['thresholds']):lines.append(f"| {b['threshold']:.0f}+ | {b['wins']}-{b['losses']} ({100*b['win_rate']:.1f}%) | {c['wins']}-{c['losses']} ({100*c['win_rate']:.1f}%) |")
 (OUT/'CFB_MARGIN_COMPONENT_WEIGHT_EDGE_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
