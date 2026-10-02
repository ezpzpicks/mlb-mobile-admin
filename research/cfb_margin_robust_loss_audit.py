"""Leakage-safe robust-loss audit using the current CFB margin information set.
No market predictors. 2021-23 train, 2024 selects method/tuning, 2025 confirms.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.regression.quantile_regression import QuantReg
from research import cfb_totals_efficiency_regression as base
OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TRAIN=(2021,2022,2023);VALID=2024;HOLDOUT=2025
FEATURES=['prior_ppg_diff','prior_papg_diff','prior_power_margin','current_scoring_diff','current_allowed_diff','current_power_margin','home_indicator']

def rows():
 cache={s:base.games_from_pbp(s) for s in range(2020,2026)};out=[]
 for season in (*TRAIN,VALID,HOLDOUT):
  games=cache[season];prior=base.team_summary(cache.get(season-1,pd.DataFrame()))
  for wk in sorted(pd.to_numeric(games.week,errors='coerce').dropna().astype(int).unique()):
   cur=base.team_summary(games[games.week<int(wk)])
   for _,g in games[games.week==int(wk)].iterrows():
    a,h=str(g.away_team),str(g.home_team);pa,ph=base.stat(prior,a),base.stat(prior,h);ca,ch=base.stat(cur,a),base.stat(cur,h);ba,bh=base.blend_stats(pa,ca),base.blend_stats(ph,ch);home_ind=0. if base.truthy(g.neutral) else 1.
    out.append({'season':season,'actual':float(g.actual_margin),
      'prior_ppg_diff':ph.ppg-pa.ppg,
      'prior_papg_diff':pa.papg-ph.papg,
      'prior_power_margin':2.*(ph.power-pa.power),
      'current_scoring_diff':(bh.ppg-ph.ppg)-(ba.ppg-pa.ppg),
      'current_allowed_diff':(ba.papg-pa.papg)-(bh.papg-ph.papg),
      'current_power_margin':2.*((bh.power-ba.power)-(ph.power-pa.power)),
      'home_indicator':home_ind})
 return pd.DataFrame(out)

def prep(train,test):
 mu=train[FEATURES].mean();sd=train[FEATURES].std(ddof=0).replace(0.,1.);return (train[FEATURES]-mu)/sd,(test[FEATURES]-mu)/sd,mu,sd
def fit_predict(train,test,method,tuning=None):
 xtr,xte,_,_=prep(train,test);X=sm.add_constant(xtr,has_constant='add');T=sm.add_constant(xte,has_constant='add');y=train.actual
 if method=='ols':m=sm.OLS(y,X).fit()
 elif method=='lad':m=QuantReg(y,X).fit(q=.5,max_iter=5000)
 elif method=='quantile':m=QuantReg(y,X).fit(q=float(tuning),max_iter=5000)
 elif method=='huber':m=sm.RLM(y,X,M=sm.robust.norms.HuberT(t=float(tuning))).fit(maxiter=500)
 elif method=='tukey':m=sm.RLM(y,X,M=sm.robust.norms.TukeyBiweight(c=float(tuning))).fit(maxiter=500)
 else:raise ValueError(method)
 return np.asarray(m.predict(T),dtype=float)
def stats(df,p):
 e=p-df.actual.to_numpy();return {'n':int(len(df)),'mae':float(np.mean(np.abs(e))),'rmse':float(np.sqrt(np.mean(e*e))),'bias':float(np.mean(e))}
def baseline_pred(df):
 # Algebraic margin implied by current team-score coefficients.
 c=base.spread_reg.TEAM_SCORE_COEFFICIENTS;b=0.;
 return (c['prior_own_ppg']*df.prior_ppg_diff+c['prior_opp_papg']*df.prior_papg_diff+c['prior_power_gap']*df.prior_power_margin+c['current_own_scoring_delta']*df.current_scoring_diff+c['current_opp_allowed_delta']*df.current_allowed_diff+c['current_power_delta']*df.current_power_margin+c['home_indicator']*df.home_indicator).to_numpy()
def main():
 df=rows();tr=df[df.season.isin(TRAIN)];va=df[df.season==VALID];ho=df[df.season==HOLDOUT];bv=stats(va,baseline_pred(va));bh=stats(ho,baseline_pred(ho));candidates=[('ols',None),('lad',None)]+[('quantile',q) for q in (.45,.475,.5,.525,.55)]+[('huber',t) for t in (.75,1.,1.345,1.75,2.5)]+[('tukey',c) for c in (3.,4.685,6.)];res=[]
 for method,tune in candidates:
  p=fit_predict(tr,va,method,tune);m=stats(va,p);res.append({'method':method,'tuning':tune,**m})
 res.sort(key=lambda x:(x['mae'],x['rmse']));chosen=res[0];tv=pd.concat([tr,va],ignore_index=True);hold=stats(ho,fit_predict(tv,ho,chosen['method'],chosen['tuning']));
 # Evaluate every method using its pre-holdout specification; no 2025 selection.
 families=[]
 for r in res:
  hm=stats(ho,fit_predict(tv,ho,r['method'],r['tuning']));families.append({**r,'valid_improvement':bv['mae']-r['mae'],'holdout':hm,'holdout_improvement':bh['mae']-hm['mae']})
 out={'baseline':{'validation':bv,'holdout':bh},'chosen_on_2024':chosen,'chosen_2025':hold,'chosen_holdout_improvement':bh['mae']-hold['mae'],'methods':families};(OUT/'cfb_margin_robust_loss_results.json').write_text(json.dumps(out,indent=2));lines=['# CFB Margin Robust Loss Audit','',f"Current fixed baseline 2024 MAE: {bv['mae']:.3f}",f"Current fixed baseline 2025 MAE: {bh['mae']:.3f}",'','| Method | Tuning | 2024 MAE | Δ | 2025 MAE | Δ |','|---|---:|---:|---:|---:|---:|'];lines += [f"| {r['method']} | {'' if r['tuning'] is None else r['tuning']} | {r['mae']:.3f} | {r['valid_improvement']:+.3f} | {r['holdout']['mae']:.3f} | {r['holdout_improvement']:+.3f} |" for r in families];lines += ['',f"Chosen on 2024: {chosen['method']} {chosen['tuning']}; 2025 MAE {hold['mae']:.3f}; Δ {bh['mae']-hold['mae']:+.3f}"];(OUT/'CFB_MARGIN_ROBUST_LOSS_RESULTS.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
