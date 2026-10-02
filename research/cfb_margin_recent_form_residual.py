"""Leakage-safe recent-form / consistency residual audit for CFB margins.

No sportsbook input. Baseline is the fixed independent spread model.
2021-23 train, 2024 selects family/ridge/cap, 2025 confirms.
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_totals_efficiency_regression as base

OUT=Path('research/results'); OUT.mkdir(parents=True,exist_ok=True)
TRAIN=(2021,2022,2023); VALID=2024; HOLDOUT=2025
ALPHAS=(1.,4.,16.,64.,256.); CAPS=(2.,3.,4.,5.,6.,8.)

def team_rows(games,team):
    rows=[]
    if games is None or games.empty:return rows
    for _,g in games.sort_values(['week','game_id']).iterrows():
        if g.home_team==team:
            rows.append({'week':int(g.week),'pf':float(g.home_score),'pa':float(g.away_score),'margin':float(g.actual_margin),'opp':str(g.away_team),'loc':1})
        elif g.away_team==team:
            rows.append({'week':int(g.week),'pf':float(g.away_score),'pa':float(g.home_score),'margin':-float(g.actual_margin),'opp':str(g.home_team),'loc':-1})
    return rows

def avg(a): return float(np.mean(a)) if a else 0.0
def std(a): return float(np.std(a)) if len(a)>1 else 0.0

def profile(games,team,powers):
    r=team_rows(games,team); n=len(r)
    if not r:return {'n':0,'recent_margin_delta':0.,'recent_off_delta':0.,'recent_def_delta':0.,'volatility':0.,'home_split':0.,'away_split':0.,'recent_sos':0.,'power_resid':0.}
    last=r[-3:]; margins=[x['margin'] for x in r]; pf=[x['pf'] for x in r]; pa=[x['pa'] for x in r]
    recent_m=[x['margin'] for x in last]; recent_pf=[x['pf'] for x in last]; recent_pa=[x['pa'] for x in last]
    home=[x['margin'] for x in r if x['loc']==1]; away=[x['margin'] for x in r if x['loc']==-1]; overall=avg(margins)
    home_split=(avg(home)-overall)*(len(home)/(len(home)+3.0)) if home else 0.; away_split=(avg(away)-overall)*(len(away)/(len(away)+3.0)) if away else 0.
    sos=avg([float(powers.get(x['opp'],{}).get('power',0.0)) for x in last])
    own_power=float(powers.get(team,{}).get('power',0.0)); residuals=[]
    for x in last:
        op=float(powers.get(x['opp'],{}).get('power',0.0)); loc_hfa=2.0*x['loc']; residuals.append(x['margin']-(own_power-op+loc_hfa))
    return {'n':n,'recent_margin_delta':avg(recent_m)-overall,'recent_off_delta':avg(recent_pf)-avg(pf),'recent_def_delta':avg(recent_pa)-avg(pa),'volatility':std(margins),'home_split':home_split,'away_split':away_split,'recent_sos':sos,'power_resid':avg(residuals)}

def blend(prior,current):
    n=current['n']; w=n/(n+4.0) if n>0 else 0.0; out={'n':n}
    for k in prior:
        if k!='n':out[k]=(1-w)*prior[k]+w*current[k]
    return out

def build():
    seasons=range(min(TRAIN),HOLDOUT+1); cache={s:base.games_from_pbp(s) for s in seasons}; rows=[]
    neutral_profile={'n':0,'recent_margin_delta':0.,'recent_off_delta':0.,'recent_def_delta':0.,'volatility':0.,'home_split':0.,'away_split':0.,'recent_sos':0.,'power_resid':0.}
    for season in (*TRAIN,VALID,HOLDOUT):
        games=cache[season]; prior_games=cache.get(season-1,pd.DataFrame()); prior_stats=base.team_summary(prior_games)
        teams=set(games.home_team)|set(games.away_team); prior_prof={tm:profile(prior_games,tm,prior_stats) for tm in teams}
        for week in sorted(pd.to_numeric(games.week,errors='coerce').dropna().astype(int).unique()):
            before=games[games.week<int(week)]; current_stats=base.team_summary(before); subset=games[games.week==int(week)]
            current_prof={tm:profile(before,tm,current_stats) for tm in teams}
            for _,g in subset.iterrows():
                a,h=str(g.away_team),str(g.home_team); ap=blend(prior_prof.get(a,neutral_profile),current_prof.get(a,neutral_profile)); hp=blend(prior_prof.get(h,neutral_profile),current_prof.get(h,neutral_profile))
                a_score,h_score=base.spread_scores(a,h,base.truthy(g.neutral),prior_stats,current_stats); pm=float(h_score-a_score)
                vol_avg=.5*(hp['volatility']+ap['volatility']); sign=1.0 if pm>=0 else -1.0
                rows.append({'season':season,'week':week,'pm':pm,'actual':float(g.actual_margin),
                  'recent_margin_diff':hp['recent_margin_delta']-ap['recent_margin_delta'],
                  'recent_off_diff':hp['recent_off_delta']-ap['recent_off_delta'],
                  'recent_def_diff':hp['recent_def_delta']-ap['recent_def_delta'],
                  'split_edge':hp['home_split']-ap['away_split'],
                  'recent_sos_diff':hp['recent_sos']-ap['recent_sos'],
                  'power_resid_diff':hp['power_resid']-ap['power_resid'],
                  'volatility_avg':vol_avg,'signed_volatility':sign*vol_avg,
                  'margin_x_volatility':pm*vol_avg,
                  'margin_abs':abs(pm),'signed_margin_sq':sign*(pm*pm),
                })
    return pd.DataFrame(rows)

FAMILIES={
 'recent_margin':['recent_margin_diff'],
 'recent_scoring':['recent_off_diff','recent_def_diff'],
 'recent_form':['recent_margin_diff','recent_off_diff','recent_def_diff'],
 'home_away_split':['split_edge'],
 'recent_sos':['recent_sos_diff'],
 'power_residual':['power_resid_diff'],
 'recent_plus_split':['recent_margin_diff','split_edge'],
 'recent_plus_power_resid':['recent_margin_diff','power_resid_diff'],
 'recent_plus_sos':['recent_margin_diff','recent_sos_diff'],
 'volatility_scale':['signed_volatility','margin_x_volatility'],
 'margin_nonlinearity':['pm','signed_margin_sq'],
 'form_split_volatility':['recent_margin_diff','recent_off_diff','recent_def_diff','split_edge','signed_volatility'],
 'form_context':['recent_margin_diff','split_edge','recent_sos_diff','power_resid_diff','signed_volatility'],
}

def fit(df,features,alpha):
    x=df[features].astype(float).replace([np.inf,-np.inf],np.nan); mu=x.mean(); x=x.fillna(mu); sd=x.std(ddof=0).replace(0.,1.); z=((x-mu)/sd).to_numpy(); y=(df.actual-df.pm).to_numpy(); X=np.column_stack([np.ones(len(z)),z]); P=np.eye(X.shape[1])*alpha;P[0,0]=0.; b=np.linalg.solve(X.T@X+P,X.T@y); return {'features':features,'intercept':float(b[0]),'coef':b[1:],'mu':mu,'sd':sd}
def pred(df,m,cap):
    z=np.column_stack([((pd.to_numeric(df[f],errors='coerce').fillna(m['mu'][f])-m['mu'][f])/m['sd'][f]).to_numpy() for f in m['features']]); c=np.clip(m['intercept']+z@m['coef'],-cap,cap); return df.pm.to_numpy()+c
def mae(df,p):return float(np.mean(np.abs(p-df.actual.to_numpy())))
def stats(df,p):
    e=p-df.actual.to_numpy();return {'n':len(df),'mae':float(np.mean(np.abs(e))),'rmse':float(np.sqrt(np.mean(e*e))),'bias':float(np.mean(e))}

def main():
    df=build(); tr=df[df.season.isin(TRAIN)].copy();va=df[df.season==VALID].copy();ho=df[df.season==HOLDOUT].copy(); bv=stats(va,va.pm.to_numpy());bh=stats(ho,ho.pm.to_numpy()); bests=[]
    for name,features in FAMILIES.items():
        best=None
        for a in ALPHAS:
            for cap in CAPS:
                m=fit(tr,features,a); mm=stats(va,pred(va,m,cap)); row={'family':name,'features':features,'alpha':a,'cap':cap,**mm}
                if best is None or (row['mae'],row['rmse'])<(best['mae'],best['rmse']):best=row
        bests.append(best)
    bests.sort(key=lambda x:(x['mae'],x['rmse'])); tv=pd.concat([tr,va],ignore_index=True); results=[]
    for r in bests:
        m=fit(tv,r['features'],r['alpha']); hm=stats(ho,pred(ho,m,r['cap'])); results.append({**r,'valid_improvement':bv['mae']-r['mae'],'holdout':hm,'holdout_improvement':bh['mae']-hm['mae']})
    out={'protocol':{'train':list(TRAIN),'validation':VALID,'holdout':HOLDOUT,'market_input':False},'baseline':{'validation':bv,'holdout':bh},'families':results}
    (OUT/'cfb_margin_recent_form_results.json').write_text(json.dumps(out,indent=2))
    lines=['# CFB Recent Form Margin Residual Audit','',f"Baseline 2024 MAE: {bv['mae']:.3f}",f"Baseline 2025 MAE: {bh['mae']:.3f}",'','| Family | 2024 MAE | Δ | 2025 MAE | Δ | Alpha | Cap |','|---|---:|---:|---:|---:|---:|---:|']
    for r in results:lines.append(f"| {r['family']} | {r['mae']:.3f} | {r['valid_improvement']:+.3f} | {r['holdout']['mae']:.3f} | {r['holdout_improvement']:+.3f} | {r['alpha']:.0f} | {r['cap']:.0f} |")
    (OUT/'CFB_MARGIN_RECENT_FORM_RESULTS.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
