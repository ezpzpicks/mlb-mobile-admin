"""Leakage-safe CFB margin scoring-context residual audit.

Question: should the same independent strength gap produce a different margin depending
on our own projected scoring environment? Market lines are never predictors.
Train 2021-23; choose family/ridge/cap on 2024; confirm on 2025.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_totals_efficiency_regression as base

OUT=Path('research/results'); OUT.mkdir(parents=True,exist_ok=True)
TRAIN=(2021,2022,2023);VALID=2024;HOLDOUT=2025
ALPHAS=(1.,4.,16.,64.,256.,1024.);CAPS=(1.,2.,3.,4.,5.,6.,8.)
_orig=base.build_feature_row

def build_row(game,prior_stats,current_stats,prior_metrics,current_metrics):
    row=_orig(game,prior_stats,current_stats,prior_metrics,current_metrics)
    away,home=str(game['away_team']),str(game['home_team']);neutral=base.truthy(game.get('neutral',False))
    a,h=base.spread_scores(away,home,neutral,prior_stats,current_stats);m=float(h-a);total=float(row['structural_total']);poss=float(row['expected_possessions']);ppd=float(row['expected_combined_ppd']);sgn=1. if m>=0 else -1.;ab=abs(m)
    row.update({'spread_margin':m,'actual_margin':float(base.num(game.get('actual_margin'),0.0)),'signed_margin_sq':sgn*ab*ab,'signed_margin_sqrt':sgn*np.sqrt(ab),'margin_x_total':m*total,'margin_x_possessions':m*poss,'margin_x_ppd':m*ppd,'sign_x_total':sgn*total,'sign_x_possessions':sgn*poss,'point_share':m/max(35.,total),'abs_margin_x_total':sgn*ab*total,'total_level':total,'poss_level':poss,'ppd_level':ppd})
    return row
base.build_feature_row=build_row

FAMILIES={
 'margin_recalibration':['spread_margin'],
 'margin_nonlinearity':['spread_margin','signed_margin_sq'],
 'margin_shape':['spread_margin','signed_margin_sq','signed_margin_sqrt'],
 'margin_total_interaction':['spread_margin','margin_x_total'],
 'margin_possession_interaction':['spread_margin','margin_x_possessions'],
 'margin_ppd_interaction':['spread_margin','margin_x_ppd'],
 'margin_total_poss':['spread_margin','margin_x_total','margin_x_possessions'],
 'margin_total_ppd':['spread_margin','margin_x_total','margin_x_ppd'],
 'all_scoring_context':['spread_margin','margin_x_total','margin_x_possessions','margin_x_ppd'],
 'favorite_environment':['sign_x_total','sign_x_possessions'],
 'point_share':['point_share'],
 'share_plus_total':['point_share','sign_x_total'],
 'total_level_with_margin':['spread_margin','total_level','sign_x_total'],
 'poss_level_with_margin':['spread_margin','poss_level','sign_x_possessions'],
}

def fit(df,fs,a):
    x=df[fs].astype(float).replace([np.inf,-np.inf],np.nan);mu=x.mean();x=x.fillna(mu);sd=x.std(ddof=0).replace(0.,1.);z=((x-mu)/sd).to_numpy();y=(df.actual_margin-df.spread_margin).to_numpy();X=np.column_stack([np.ones(len(z)),z]);P=np.eye(X.shape[1])*a;P[0,0]=0.;b=np.linalg.solve(X.T@X+P,X.T@y);return {'fs':fs,'i':float(b[0]),'b':b[1:],'mu':mu,'sd':sd}
def pred(df,m,cap):
    z=np.column_stack([((pd.to_numeric(df[f],errors='coerce').fillna(m['mu'][f])-m['mu'][f])/m['sd'][f]).to_numpy() for f in m['fs']]);return df.spread_margin.to_numpy()+np.clip(m['i']+z@m['b'],-cap,cap)
def stats(df,p):
    e=p-df.actual_margin.to_numpy();return {'n':int(len(df)),'mae':float(np.mean(np.abs(e))),'rmse':float(np.sqrt(np.mean(e*e))),'bias':float(np.mean(e))}
def main():
    df=base.build_dataset().copy();tr=df[df.season.isin(TRAIN)].copy();va=df[df.season==VALID].copy();ho=df[df.season==HOLDOUT].copy();bv=stats(va,va.spread_margin.to_numpy());bh=stats(ho,ho.spread_margin.to_numpy());bests=[]
    for name,fs in FAMILIES.items():
        best=None
        for a in ALPHAS:
            for cap in CAPS:
                mm=stats(va,pred(va,fit(tr,fs,a),cap));row={'family':name,'features':fs,'alpha':a,'cap':cap,**mm}
                if best is None or (row['mae'],row['rmse'])<(best['mae'],best['rmse']):best=row
        bests.append(best)
    bests.sort(key=lambda x:(x['mae'],x['rmse']));tv=pd.concat([tr,va],ignore_index=True);res=[]
    for r in bests:
        hm=stats(ho,pred(ho,fit(tv,r['features'],r['alpha']),r['cap']));res.append({**r,'valid_improvement':bv['mae']-r['mae'],'holdout':hm,'holdout_improvement':bh['mae']-hm['mae']})
    out={'protocol':{'train':list(TRAIN),'validation':VALID,'holdout':HOLDOUT,'market_input':False},'baseline':{'validation':bv,'holdout':bh},'families':res};(OUT/'cfb_margin_scoring_context_results.json').write_text(json.dumps(out,indent=2));lines=['# CFB Margin Scoring-Context Audit','',f"Baseline 2024 MAE: {bv['mae']:.3f}",f"Baseline 2025 MAE: {bh['mae']:.3f}",'','| Family | 2024 MAE | Δ | 2025 MAE | Δ | Alpha | Cap |','|---|---:|---:|---:|---:|---:|---:|'];lines += [f"| {r['family']} | {r['mae']:.3f} | {r['valid_improvement']:+.3f} | {r['holdout']['mae']:.3f} | {r['holdout_improvement']:+.3f} | {r['alpha']:.0f} | {r['cap']:.0f} |" for r in res];(OUT/'CFB_MARGIN_SCORING_CONTEXT_RESULTS.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
