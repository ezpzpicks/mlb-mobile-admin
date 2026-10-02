"""Leakage-safe robust power-rating transform audit for CFB margin.

The current internal power iteration uses opponent-adjusted actual margin clipped at 45.
This audit tests whether diminishing returns for blowout margins plus a power-weight
multiplier improve the fixed independent margin formula. No market predictors.
2024 selects transform/weight; 2025 confirms.
"""
from __future__ import annotations
import json, math
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_totals_efficiency_regression as base
OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
VALID=2024;HOLDOUT=2025;HFA=base.HFA
WEIGHTS=(0.,.25,.5,.75,1.,1.25,1.5,1.75,2.)

@dataclass
class S:
 power:float=0.;ppg:float=28.;papg:float=28.;games:int=0

def adj_margin(x,mode):
 s=1. if x>=0 else -1.;a=abs(float(x))
 if mode.startswith('cap'):
  return s*min(a,float(mode[3:]))
 if mode=='sqrt14': return s*(a if a<=14 else 14+math.sqrt(a-14)*3.5)
 if mode=='sqrt21': return s*(a if a<=21 else 21+math.sqrt(a-21)*3.0)
 if mode=='log14': return s*(a if a<=14 else 14+5.0*math.log1p(a-14))
 if mode=='tanh28': return 28.*math.tanh(x/28.)
 if mode=='tanh35': return 35.*math.tanh(x/35.)
 return float(np.clip(x,-45.,45.))
MODES=('cap14','cap21','cap28','cap35','cap45','cap55','sqrt14','sqrt21','log14','tanh28','tanh35')

def summary(games,mode):
 if games is None or games.empty:return {}
 teams=sorted(set(games.away_team.astype(str))|set(games.home_team.astype(str)));rows=games.to_dict('records');by={t:[] for t in teams}
 for g in rows:by[str(g['away_team'])].append(g);by[str(g['home_team'])].append(g)
 power={t:0. for t in teams}
 for _ in range(30):
  upd={}
  for tm in teams:
   vals=[]
   for g in by[tm]:
    away,home=str(g['away_team']),str(g['home_team']);m=float(g['actual_margin']);
    if not base.truthy(g.get('neutral',False)):m-=HFA
    m=adj_margin(m,mode)
    if tm==home:opp,tmarg=away,m
    else:opp,tmarg=home,-m
    vals.append(tmarg+power.get(opp,0.))
   upd[tm]=float(np.mean(vals)) if vals else 0.
  ctr=float(np.mean(list(upd.values()))) if upd else 0.;power={t:float(np.clip(v-ctr,-45.,45.)) for t,v in upd.items()}
 out={}
 for tm in teams:
  scored=[];allowed=[]
  for g in by[tm]:
   home=tm==str(g['home_team']);scored.append(float(g['home_score'] if home else g['away_score']));allowed.append(float(g['away_score'] if home else g['home_score']))
  out[tm]=S(power[tm],float(np.mean(scored)),float(np.mean(allowed)),len(scored))
 return out

def get(d,t):return d.get(t,S())
def blend(p,c):
 w=c.games/(c.games+4.) if c.games else 0.;return S((1-w)*p.power+w*c.power,(1-w)*p.ppg+w*c.ppg,(1-w)*p.papg+w*c.papg,c.games)
def scores(away,home,neutral,prior,current,pw):
 pa,ph=get(prior,away),get(prior,home);ca,ch=get(current,away),get(current,home);ba,bh=blend(pa,ca),blend(ph,ch)
 af={'prior_own_ppg':pa.ppg,'prior_opp_papg':ph.papg,'prior_power_gap':pw*(pa.power-ph.power),'current_own_scoring_delta':ba.ppg-pa.ppg,'current_opp_allowed_delta':bh.papg-ph.papg,'current_power_delta':pw*((ba.power-bh.power)-(pa.power-ph.power)),'home_indicator':0.}
 hf={'prior_own_ppg':ph.ppg,'prior_opp_papg':pa.papg,'prior_power_gap':pw*(ph.power-pa.power),'current_own_scoring_delta':bh.ppg-ph.ppg,'current_opp_allowed_delta':ba.papg-pa.papg,'current_power_delta':pw*((bh.power-ba.power)-(ph.power-pa.power)),'home_indicator':0. if neutral else 1.}
 def sc(f):return float(base.spread_reg.TEAM_SCORE_INTERCEPT+sum(base.spread_reg.TEAM_SCORE_COEFFICIENTS[n]*float(f.get(n,0.)) for n in base.spread_reg.TEAM_SCORE_COEFFICIENTS))
 return sc(af),sc(hf)
def season_preds(games,prior_games,mode,pw):
 prior=summary(prior_games,mode);out=[]
 for wk in sorted(pd.to_numeric(games.week,errors='coerce').dropna().astype(int).unique()):
  cur=summary(games[games.week<int(wk)],mode)
  for _,g in games[games.week==int(wk)].iterrows():
   a,h=scores(str(g.away_team),str(g.home_team),base.truthy(g.neutral),prior,cur,pw);out.append({'actual':float(g.actual_margin),'pred':h-a})
 return pd.DataFrame(out)
def stats(df):
 e=df.pred-df.actual;return {'n':int(len(df)),'mae':float(np.mean(np.abs(e))),'rmse':float(np.sqrt(np.mean(e*e))),'bias':float(np.mean(e))}
def main():
 cache={s:base.games_from_pbp(s) for s in (2023,2024,2025)};rows=[]
 for mode in MODES:
  for w in WEIGHTS:
   va=season_preds(cache[2024],cache[2023],mode,w);m=stats(va);rows.append({'mode':mode,'power_weight':w,**m})
 rows.sort(key=lambda x:(x['mae'],x['rmse']));chosen=rows[0];hold=stats(season_preds(cache[2025],cache[2024],chosen['mode'],chosen['power_weight']));control_v=next(r for r in rows if r['mode']=='cap45' and r['power_weight']==1.);control_h=stats(season_preds(cache[2025],cache[2024],'cap45',1.));
 # Also show each transform's validation-selected weight and its 2025 result, without using 2025 for selection.
 families=[]
 for mode in MODES:
  best=min((r for r in rows if r['mode']==mode),key=lambda x:(x['mae'],x['rmse']));hm=stats(season_preds(cache[2025],cache[2024],mode,best['power_weight']));families.append({**best,'valid_improvement':control_v['mae']-best['mae'],'holdout':hm,'holdout_improvement':control_h['mae']-hm['mae']})
 out={'baseline':{'validation':control_v,'holdout':control_h},'chosen_on_2024':chosen,'chosen_2025':hold,'chosen_holdout_improvement':control_h['mae']-hold['mae'],'transforms':families};(OUT/'cfb_margin_power_transform_results.json').write_text(json.dumps(out,indent=2));lines=['# CFB Margin Robust Power Transform Audit','',f"Baseline 2024 MAE: {control_v['mae']:.3f}",f"Baseline 2025 MAE: {control_h['mae']:.3f}",'','| Transform | Weight | 2024 MAE | Δ | 2025 MAE | Δ |','|---|---:|---:|---:|---:|---:|'];lines += [f"| {r['mode']} | {r['power_weight']:.2f} | {r['mae']:.3f} | {r['valid_improvement']:+.3f} | {r['holdout']['mae']:.3f} | {r['holdout_improvement']:+.3f} |" for r in families];lines += ['',f"Chosen on 2024: {chosen['mode']} @ {chosen['power_weight']:.2f}; 2025 MAE {hold['mae']:.3f}; Δ {control_h['mae']-hold['mae']:+.3f}"];(OUT/'CFB_MARGIN_POWER_TRANSFORM_RESULTS.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
