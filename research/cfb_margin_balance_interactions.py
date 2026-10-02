"""Leakage-safe offense/defense balance interaction audit for CFB margin.

Tests nonlinear team-comparison information not represented by the fixed linear
PPG/PAPG/power margin regression. No sportsbook predictors.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_totals_efficiency_regression as base
OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TRAIN=(2021,2022,2023);VALID=2024;HOLDOUT=2025
ALPHAS=(1.,4.,16.,64.,256.,1024.);CAPS=(1.,2.,3.,4.,5.,6.,8.)
_orig=base.build_feature_row

def build_row(game,prior_stats,current_stats,prior_metrics,current_metrics):
    row=_orig(game,prior_stats,current_stats,prior_metrics,current_metrics);a,h=str(game.away_team),str(game.home_team);neu=base.truthy(game.get('neutral',False));ascore,hscore=base.spread_scores(a,h,neu,prior_stats,current_stats);pm=float(hscore-ascore)
    ast=base.blend_stats(base.stat(prior_stats,a),base.stat(current_stats,a));hst=base.blend_stats(base.stat(prior_stats,h),base.stat(current_stats,h));avg_pts=28.
    ao=ast.ppg-avg_pts;ad=avg_pts-ast.papg;ho=hst.ppg-avg_pts;hd=avg_pts-hst.papg
    # Positive = home advantage.
    matchup_linear=(hst.ppg-ast.papg)-(ast.ppg-hst.papg)
    matchup_synergy=(ho*max(-10.,min(10.,-(-ad))))-(ao*max(-10.,min(10.,-(-hd))))
    # Offense x opposing defensive weakness, centered around neutral scoring.
    home_cross=ho*(ast.papg-avg_pts);away_cross=ao*(hst.papg-avg_pts)
    two_way=min(ho,hd)-min(ao,ad);balance_gap=abs(ho-hd)-abs(ao-ad);product=(ho*hd)-(ao*ad)
    power_gap=hst.power-ast.power
    row.update({'spread_margin':pm,'actual_margin':float(base.num(game.actual_margin,0.0)),'matchup_linear':matchup_linear,'cross_matchup':home_cross-away_cross,'two_way_strength':two_way,'balance_gap':balance_gap,'off_def_product':product,'offense_diff':ho-ao,'defense_diff':hd-ad,'power_gap':power_gap,'power_x_two_way':power_gap*two_way,'power_x_balance':power_gap*balance_gap,'margin_x_balance':pm*balance_gap,'margin_x_two_way':pm*two_way})
    return row
base.build_feature_row=build_row
FAMILIES={
 'two_way_strength':['two_way_strength'],
 'balance_gap':['balance_gap'],
 'off_def_product':['off_def_product'],
 'cross_matchup':['cross_matchup'],
 'offense_defense_diff':['offense_diff','defense_diff'],
 'two_way_plus_cross':['two_way_strength','cross_matchup'],
 'balance_product':['balance_gap','off_def_product'],
 'power_two_way':['power_x_two_way'],
 'power_balance':['power_x_balance'],
 'margin_balance':['margin_x_balance'],
 'margin_two_way':['margin_x_two_way'],
 'nonlinear_matchup':['cross_matchup','off_def_product','two_way_strength'],
 'balance_context':['two_way_strength','balance_gap','off_def_product','power_x_two_way'],
}
def fit(df,fs,a):
 x=df[fs].astype(float).replace([np.inf,-np.inf],np.nan);mu=x.mean();x=x.fillna(mu);sd=x.std(ddof=0).replace(0.,1.);z=((x-mu)/sd).to_numpy();y=(df.actual_margin-df.spread_margin).to_numpy();X=np.column_stack([np.ones(len(z)),z]);P=np.eye(X.shape[1])*a;P[0,0]=0.;b=np.linalg.solve(X.T@X+P,X.T@y);return {'fs':fs,'i':float(b[0]),'b':b[1:],'mu':mu,'sd':sd}
def pred(df,m,cap):
 z=np.column_stack([((pd.to_numeric(df[f],errors='coerce').fillna(m['mu'][f])-m['mu'][f])/m['sd'][f]).to_numpy() for f in m['fs']]);return df.spread_margin.to_numpy()+np.clip(m['i']+z@m['b'],-cap,cap)
def stats(df,p):
 e=p-df.actual_margin.to_numpy();return {'n':int(len(df)),'mae':float(np.mean(np.abs(e))),'rmse':float(np.sqrt(np.mean(e*e))),'bias':float(np.mean(e))}
def main():
 df=base.build_dataset().copy();tr=df[df.season.isin(TRAIN)];va=df[df.season==VALID];ho=df[df.season==HOLDOUT];bv=stats(va,va.spread_margin.to_numpy());bh=stats(ho,ho.spread_margin.to_numpy());bests=[]
 for name,fs in FAMILIES.items():
  best=None
  for a in ALPHAS:
   for cap in CAPS:
    mm=stats(va,pred(va,fit(tr,fs,a),cap));r={'family':name,'features':fs,'alpha':a,'cap':cap,**mm}
    if best is None or (r['mae'],r['rmse'])<(best['mae'],best['rmse']):best=r
  bests.append(best)
 bests.sort(key=lambda x:(x['mae'],x['rmse']));tv=pd.concat([tr,va],ignore_index=True);res=[]
 for r in bests:
  hm=stats(ho,pred(ho,fit(tv,r['features'],r['alpha']),r['cap']));res.append({**r,'valid_improvement':bv['mae']-r['mae'],'holdout':hm,'holdout_improvement':bh['mae']-hm['mae']})
 (OUT/'cfb_margin_balance_interactions_results.json').write_text(json.dumps({'baseline':{'validation':bv,'holdout':bh},'families':res},indent=2));lines=['# CFB Margin Balance Interaction Audit','',f"Baseline 2024 MAE: {bv['mae']:.3f}",f"Baseline 2025 MAE: {bh['mae']:.3f}",'','| Family | 2024 MAE | Δ | 2025 MAE | Δ | Alpha | Cap |','|---|---:|---:|---:|---:|---:|---:|'];lines += [f"| {r['family']} | {r['mae']:.3f} | {r['valid_improvement']:+.3f} | {r['holdout']['mae']:.3f} | {r['holdout_improvement']:+.3f} | {r['alpha']:.0f} | {r['cap']:.0f} |" for r in res];(OUT/'CFB_MARGIN_BALANCE_INTERACTIONS_RESULTS.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
