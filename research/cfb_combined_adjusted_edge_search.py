"""Combine independently useful, leakage-safe CFB margin signal families.

This test deliberately combines only feature families that showed independent
out-of-sample edge-ordering signal in prior audits:
- competitive-state PBP efficiency (garbage-time reduced),
- weekly SportsDataverse opponent-adjusted EPA / FEI ratings,
- contemporaneous ESPN FPI Game Control / adjusted in-game WP.

Sportsbook spreads are evaluation-only and never enter the prediction formula.
Protocol: 2021-23 fit residuals, 2024 selects family/alpha/cap, 2025 confirms,
then 2026 confirms with the exact frozen specification and zero retuning.
"""
from __future__ import annotations
import io, json, math
from pathlib import Path
import numpy as np
import pandas as pd
import pyreadr
from research import cfb_weekly_fpi_edge_search as fpi
from research import cfb_competitive_state_edge_search as comp

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TRAIN=(2021,2022,2023);VALID=2024;HOLD=2025;FINAL=2026
TH=(2.,4.,6.,8.,10.);ALPHAS=(1.,4.,16.,64.,256.,1024.);CAPS=(2.,4.,6.,8.)

FPI_CONTROL=['gamecontrol_diff','adjavgingamewp_diff']
FPI_SOS=['accomplishment_diff','avgsosrank_diff','topsosrank_diff']
ADJ=['adj_off_epa_diff','adj_def_epa_diff','adj_st_epa_diff','adj_net_diff','fei_off_diff','fei_def_diff','fei_net_diff','off_pace_diff','net_z_diff']
ADJ_NET=['adj_net_diff','fei_net_diff','net_z_diff']
ADJ_OD=['adj_off_epa_diff','adj_def_epa_diff','fei_off_diff','fei_def_diff']
COMP=['off_epa_diff','def_epa_edge','off_success_diff','def_success_edge','pass_epa_diff','rush_epa_diff','def_pass_epa_edge','def_rush_epa_edge','early_epa_diff','def_early_epa_edge','explosive_diff','def_explosive_edge']
COMP_CORE=['off_epa_diff','def_epa_edge','off_success_diff','def_success_edge','pass_epa_diff','rush_epa_diff','early_epa_diff','def_early_epa_edge']

FAMILIES={
 'fpi_control_comp':FPI_CONTROL+COMP,
 'adjusted_comp':ADJ+COMP,
 'fpi_control_adjusted':FPI_CONTROL+ADJ,
 'fpi_control_adjusted_comp':FPI_CONTROL+ADJ+COMP,
 'fpi_control_adjnet_compcore':FPI_CONTROL+ADJ_NET+COMP_CORE,
 'fpi_control_adjod_compcore':FPI_CONTROL+ADJ_OD+COMP_CORE,
 'fpi_sos_control_adjnet':FPI_CONTROL+FPI_SOS+ADJ_NET,
}

ADJ_RAW=['adj_off_epa','adj_def_epa','adj_st_epa','adj_net','fei_off','fei_def','fei_net','off_pace','net_z']

def load_adjusted(season:int)->pd.DataFrame:
    url=f'{fpi.BASE_URL}/cfb_ratings_weekly/cfb_ratings_weekly_{season}.rds'
    data=pyreadr.read_r(io.BytesIO(fpi.download_bytes(url)));df=next(iter(data.values())).copy();df.columns=[str(c).lower() for c in df.columns]
    for c in ('team_id','through_week',*ADJ_RAW):
        if c in df.columns:df[c]=pd.to_numeric(df[c],errors='coerce')
    df=df[df.team_id.notna() & df.through_week.notna()].copy();df['week']=df.through_week
    return df.sort_values(['team_id','week']).drop_duplicates(['team_id','week'],keep='last')

def prior_rating(df,team_id,week):
    try:tid=float(team_id)
    except Exception:return None
    x=df[(df.team_id==tid)&(df.week<int(week))]
    return None if x.empty else x.sort_values('week').iloc[-1]

def add_adjusted(df,season):
    ratings=load_adjusted(season);out=df.copy()
    for idx,row in out.iterrows():
        a=prior_rating(ratings,row.away_team_id,row.week);h=prior_rating(ratings,row.home_team_id,row.week)
        if a is None or h is None:continue
        for col in ADJ_RAW:
            av=float(a[col]) if col in a.index and pd.notna(a[col]) else np.nan;hv=float(h[col]) if col in h.index and pd.notna(h[col]) else np.nan
            out.loc[idx,f'{col}_diff']=hv-av if np.isfinite(av) and np.isfinite(hv) else np.nan
    return out

def add_competitive(df,season):
    pbp=comp.load_pbp(season);out=df.copy()
    for wk in sorted(pd.to_numeric(out.week,errors='coerce').dropna().astype(int).unique()):
        tm=comp.team_metrics(pbp,wk);mask=out.week.astype(int)==int(wk)
        for idx,row in out[mask].iterrows():
            am=tm.get(comp.norm(row.away_team),{});hm=tm.get(comp.norm(row.home_team),{})
            def v(m,k):
                try:return float(m.get(k,np.nan))
                except Exception:return np.nan
            vals={
              'off_epa_diff':v(hm,'off_epa')-v(am,'off_epa'),'def_epa_edge':v(am,'def_epa')-v(hm,'def_epa'),
              'off_success_diff':v(hm,'off_success')-v(am,'off_success'),'def_success_edge':v(am,'def_success')-v(hm,'def_success'),
              'pass_epa_diff':v(hm,'pass_epa')-v(am,'pass_epa'),'rush_epa_diff':v(hm,'rush_epa')-v(am,'rush_epa'),
              'def_pass_epa_edge':v(am,'def_pass_epa')-v(hm,'def_pass_epa'),'def_rush_epa_edge':v(am,'def_rush_epa')-v(hm,'def_rush_epa'),
              'early_epa_diff':v(hm,'early_epa')-v(am,'early_epa'),'def_early_epa_edge':v(am,'def_early_epa')-v(hm,'def_early_epa'),
              'explosive_diff':v(hm,'explosive')-v(am,'explosive'),'def_explosive_edge':v(am,'def_explosive')-v(hm,'def_explosive')}
            for k,val in vals.items():out.loc[idx,k]=val
    return out

def build_season(season):
    # fpi.build_season already supplies baseline, matchup identity, ESPN ids and leakage-safe weekly FPI differences.
    df=fpi.build_season(season);df=add_adjusted(df,season);df=add_competitive(df,season)
    print(season,'rows',len(df),'adj coverage',int(df.adj_net_diff.notna().sum()),'competitive coverage',int(df.off_epa_diff.notna().sum()))
    return df

def fit(train,features,alpha):
    x=train[features].apply(pd.to_numeric,errors='coerce');mu=x.mean();x=x.fillna(mu);sd=x.std(ddof=0).replace(0.,1.);z=((x-mu)/sd).to_numpy(float)
    y=(train.actual-train.baseline).to_numpy(float);X=np.column_stack([np.ones(len(z)),z]);P=np.eye(X.shape[1])*alpha;P[0,0]=0.;b=np.linalg.solve(X.T@X+P,X.T@y)
    return {'features':features,'mu':mu,'sd':sd,'b':b}
def predict(df,m,cap):
    x=df[m['features']].apply(pd.to_numeric,errors='coerce').fillna(m['mu']);z=((x-m['mu'])/m['sd']).to_numpy(float);c=m['b'][0]+z@m['b'][1:];return df.baseline.to_numpy(float)+np.clip(c,-cap,cap)
def auc(s,l):
    s=np.asarray(s,float);l=np.asarray(l,int);n1=int((l==1).sum());n0=int((l==0).sum())
    if not n1 or not n0:return None
    r=pd.Series(s).rank(method='average').to_numpy();return float((r[l==1].sum()-n1*(n1+1)/2)/(n1*n0))
def metrics(df,p):
    p=np.asarray(p,float);e=p+df.market_home_spread.to_numpy(float);miss=df.actual.to_numpy(float)+df.market_home_spread.to_numpy(float);ok=np.isfinite(e)&np.isfinite(miss)&(np.abs(e)>1e-9)&(np.abs(miss)>1e-9);e=e[ok];miss=miss[ok];w=(e*miss>0).astype(int);rates=[]
    for t in TH:
        s=np.abs(e)>=t;n=int(s.sum());ww=int(w[s].sum());rates.append({'threshold':t,'n':n,'wins':ww,'losses':n-ww,'win_rate':ww/n if n else None})
    v=[r for r in rates if r['n']>=25];steps=sum(b['win_rate']>=a['win_rate'] for a,b in zip(v,v[1:]));viol=sum(max(0.,a['win_rate']-b['win_rate']) for a,b in zip(v,v[1:]));slope=v[-1]['win_rate']-v[0]['win_rate'] if len(v)>1 else -1.;high=next((r['win_rate'] for r in rates if r['threshold']==10 and r['n']>=25),None)
    return {'steps':steps,'possible':max(0,len(v)-1),'violation':viol,'slope':slope,'high':high,'auc':auc(np.abs(e),w),'corr':float(np.corrcoef(e,miss)[0,1]) if len(e)>2 else None,'mae':float(np.mean(np.abs(p-df.actual.to_numpy(float)))),'thresholds':rates}
def key(m):return (m['steps'],-m['violation'],m['slope'],m['high'] if m['high'] is not None else -1.,m['auc'] if m['auc'] is not None else -1.)
def rt(m,t):
    r=next(x for x in m['thresholds'] if x['threshold']==t);return f"{r['wins']}-{r['losses']} ({100*r['win_rate']:.1f}%)"
def clean(v):
    if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
    if isinstance(v,list):return [clean(x) for x in v]
    if isinstance(v,(np.integer,)):return int(v)
    if isinstance(v,(np.floating,float)):return float(v) if math.isfinite(float(v)) else None
    return v

def main():
    fs={s:build_season(s) for s in (*TRAIN,VALID,HOLD,FINAL)};df=pd.concat(fs.values(),ignore_index=True);tr=df[df.season.isin(TRAIN)].copy();va=df[df.season==VALID].copy();ho=df[df.season==HOLD].copy();fi=df[df.season==FINAL].copy()
    base={2024:metrics(va,va.baseline),2025:metrics(ho,ho.baseline),2026:metrics(fi,fi.baseline)};cand=[]
    for name,features in FAMILIES.items():
        usable=[x for x in features if x in df.columns and df[x].notna().any()]
        if not usable:continue
        for alpha in ALPHAS:
            model=fit(tr,usable,alpha)
            for cap in CAPS:cand.append({'family':name,'features':usable,'alpha':alpha,'cap':cap,'validation':metrics(va,predict(va,model,cap))})
    cand.sort(key=lambda r:key(r['validation']),reverse=True);ch=cand[0]
    m25=fit(pd.concat([tr,va]),ch['features'],ch['alpha']);r25=metrics(ho,predict(ho,m25,ch['cap']))
    m26=fit(pd.concat([tr,va,ho]),ch['features'],ch['alpha']);r26=metrics(fi,predict(fi,m26,ch['cap']))
    out={'market_predictor':False,'protocol':{'train':list(TRAIN),'select':VALID,'holdout':HOLD,'final':FINAL},'baseline':base,'chosen_on_2024':ch,'holdout_2025':r25,'holdout_2026':r26,'candidates':cand};(OUT/'cfb_combined_adjusted_edge_search.json').write_text(json.dumps(clean(out),indent=2,allow_nan=False))
    lines=['# CFB Combined Adjusted Edge Search','',f"Chosen on 2024: {ch['family']} alpha={ch['alpha']} cap={ch['cap']}",'','| Season | Model | Steps | slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |','|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for yr,b,c in [(2024,base[2024],ch['validation']),(2025,base[2025],r25),(2026,base[2026],r26)]:
        for label,m in [('Baseline',b),('Combined',c)]:lines.append(f"| {yr} | {label} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} | {m['mae']:.3f} | "+' | '.join(rt(m,t) for t in TH)+' |')
    lines+=['','## Top 2024 candidates','','| Family | Alpha | Cap | Steps | slope | 10+ | AUC | MAE |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in cand[:20]:
        mm=r['validation'];lines.append(f"| {r['family']} | {r['alpha']:.0f} | {r['cap']:.0f} | {mm['steps']}/{mm['possible']} | {100*mm['slope']:+.1f}pp | {100*(mm['high'] or 0):.1f}% | {mm['auc']:.3f} | {mm['mae']:.3f} |")
    (OUT/'CFB_COMBINED_ADJUSTED_EDGE_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
