"""Leakage-safe recent-form / consistency residual audit for CFB margins.
No sportsbook input. Baseline is the fixed independent spread model.
2021-23 train, 2024 selects family/ridge/cap, 2025 confirms.
"""
from __future__ import annotations
import json
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
        if g.home_team==team: rows.append({'pf':float(g.home_score),'pa':float(g.away_score),'margin':float(g.actual_margin),'opp':str(g.away_team),'loc':1})
        elif g.away_team==team: rows.append({'pf':float(g.away_score),'pa':float(g.home_score),'margin':-float(g.actual_margin),'opp':str(g.home_team),'loc':-1})
    return rows

def avg(a):return float(np.mean(a)) if a else 0.
def std(a):return float(np.std(a)) if len(a)>1 else 0.
def profile(games,team,powers):
    r=team_rows(games,team);n=len(r);zero={'n':0,'recent_margin_delta':0.,'recent_off_delta':0.,'recent_def_delta':0.,'volatility':0.,'home_split':0.,'away_split':0.,'recent_sos':0.,'power_resid':0.}
    if not r:return zero
    pwr=lambda tm:float(getattr(powers.get(tm),'power',0.0))
    last=r[-3:];margins=[x['margin'] for x in r];pf=[x['pf'] for x in r];pa=[x['pa'] for x in r];rm=[x['margin'] for x in last];rpf=[x['pf'] for x in last];rpa=[x['pa'] for x in last]
    home=[x['margin'] for x in r if x['loc']==1];away=[x['margin'] for x in r if x['loc']==-1];overall=avg(margins)
    hs=(avg(home)-overall)*(len(home)/(len(home)+3.)) if home else 0.;aws=(avg(away)-overall)*(len(away)/(len(away)+3.)) if away else 0.
    sos=avg([pwr(x['opp']) for x in last]);own=pwr(team);res=[x['margin']-(own-pwr(x['opp'])+2.*x['loc']) for x in last]
    return {'n':n,'recent_margin_delta':avg(rm)-overall,'recent_off_delta':avg(rpf)-avg(pf),'recent_def_delta':avg(rpa)-avg(pa),'volatility':std(margins),'home_split':hs,'away_split':aws,'recent_sos':sos,'power_resid':avg(res)}
def blend(prior,current):
    n=current['n'];w=n/(n+4.) if n else 0.;return {'n':n,**{k:(1-w)*prior[k]+w*current[k] for k in prior if k!='n'}}
def build():
    cache={s:base.games_from_pbp(s) for s in range(min(TRAIN),HOLDOUT+1)};rows=[];zero={'n':0,'recent_margin_delta':0.,'recent_off_delta':0.,'recent_def_delta':0.,'volatility':0.,'home_split':0.,'away_split':0.,'recent_sos':0.,'power_resid':0.}
    for season in (*TRAIN,VALID,HOLDOUT):
        games=cache[season];prior_games=cache.get(season-1,pd.DataFrame());prior_stats=base.team_summary(prior_games);teams=set(games.home_team)|set(games.away_team);prior_prof={tm:profile(prior_games,tm,prior_stats) for tm in teams}
        for week in sorted(pd.to_numeric(games.week,errors='coerce').dropna().astype(int).unique()):
            before=games[games.week<int(week)];cur=base.team_summary(before);cur_prof={tm:profile(before,tm,cur) for tm in teams}
            for _,g in games[games.week==int(week)].iterrows():
                a,h=str(g.away_team),str(g.home_team);ap=blend(prior_prof.get(a,zero),cur_prof.get(a,zero));hp=blend(prior_prof.get(h,zero),cur_prof.get(h,zero));ascore,hscore=base.spread_scores(a,h,base.truthy(g.neutral),prior_stats,cur);pm=float(hscore-ascore);vol=.5*(hp['volatility']+ap['volatility']);sgn=1. if pm>=0 else -1.
                rows.append({'season':season,'pm':pm,'actual':float(g.actual_margin),'recent_margin_diff':hp['recent_margin_delta']-ap['recent_margin_delta'],'recent_off_diff':hp['recent_off_delta']-ap['recent_off_delta'],'recent_def_diff':hp['recent_def_delta']-ap['recent_def_delta'],'split_edge':hp['home_split']-ap['away_split'],'recent_sos_diff':hp['recent_sos']-ap['recent_sos'],'power_resid_diff':hp['power_resid']-ap['power_resid'],'signed_volatility':sgn*vol,'margin_x_volatility':pm*vol,'signed_margin_sq':sgn*pm*pm})
    return pd.DataFrame(rows)
FAMILIES={'recent_margin':['recent_margin_diff'],'recent_scoring':['recent_off_diff','recent_def_diff'],'recent_form':['recent_margin_diff','recent_off_diff','recent_def_diff'],'home_away_split':['split_edge'],'recent_sos':['recent_sos_diff'],'power_residual':['power_resid_diff'],'recent_plus_split':['recent_margin_diff','split_edge'],'recent_plus_power_resid':['recent_margin_diff','power_resid_diff'],'recent_plus_sos':['recent_margin_diff','recent_sos_diff'],'volatility_scale':['signed_volatility','margin_x_volatility'],'margin_nonlinearity':['pm','signed_margin_sq'],'form_split_volatility':['recent_margin_diff','recent_off_diff','recent_def_diff','split_edge','signed_volatility'],'form_context':['recent_margin_diff','split_edge','recent_sos_diff','power_resid_diff','signed_volatility']}
def fit(df,fs,a):
    x=df[fs].astype(float).replace([np.inf,-np.inf],np.nan);mu=x.mean();x=x.fillna(mu);sd=x.std(ddof=0).replace(0.,1.);z=((x-mu)/sd).to_numpy();y=(df.actual-df.pm).to_numpy();X=np.column_stack([np.ones(len(z)),z]);P=np.eye(X.shape[1])*a;P[0,0]=0.;b=np.linalg.solve(X.T@X+P,X.T@y);return {'fs':fs,'i':float(b[0]),'b':b[1:],'mu':mu,'sd':sd}
def pred(df,m,cap):
    z=np.column_stack([((pd.to_numeric(df[f],errors='coerce').fillna(m['mu'][f])-m['mu'][f])/m['sd'][f]).to_numpy() for f in m['fs']]);return df.pm.to_numpy()+np.clip(m['i']+z@m['b'],-cap,cap)
def stats(df,p):
    e=p-df.actual.to_numpy();return {'n':len(df),'mae':float(np.mean(np.abs(e))),'rmse':float(np.sqrt(np.mean(e*e))),'bias':float(np.mean(e))}
def main():
    df=build();tr=df[df.season.isin(TRAIN)];va=df[df.season==VALID];ho=df[df.season==HOLDOUT];bv=stats(va,va.pm.to_numpy());bh=stats(ho,ho.pm.to_numpy());bests=[]
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
    (OUT/'cfb_margin_recent_form_results.json').write_text(json.dumps({'baseline':{'validation':bv,'holdout':bh},'families':res},indent=2));lines=['# CFB Recent Form Margin Residual Audit','',f"Baseline 2024 MAE: {bv['mae']:.3f}",f"Baseline 2025 MAE: {bh['mae']:.3f}",'','| Family | 2024 MAE | Δ | 2025 MAE | Δ | Alpha | Cap |','|---|---:|---:|---:|---:|---:|---:|'];lines += [f"| {r['family']} | {r['mae']:.3f} | {r['valid_improvement']:+.3f} | {r['holdout']['mae']:.3f} | {r['holdout_improvement']:+.3f} | {r['alpha']:.0f} | {r['cap']:.0f} |" for r in res];(OUT/'CFB_MARGIN_RECENT_FORM_RESULTS.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
