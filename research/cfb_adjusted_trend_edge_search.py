"""Test leakage-safe opponent-adjusted weekly trajectory as CFB margin signal.

Unlike raw recent form, these features use changes in contemporaneous weekly FPI,
Game Control, adjusted EPA and FEI ratings, all measured before the game. Market
spreads are evaluation-only.
"""
from __future__ import annotations
import io,json,math
from pathlib import Path
import numpy as np
import pandas as pd
import pyreadr
from research import cfb_weekly_fpi_edge_search as fpi

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TRAIN=(2021,2022,2023);VALID=2024;HOLD=2025;FINAL=2026
TH=(2.,4.,6.,8.,10.);ALPHAS=(1.,4.,16.,64.,256.);CAPS=(2.,4.,6.)
FPI_COLS=['fpi','gamecontrol','adjavgingamewp']
ADJ_COLS=['adj_off_epa','adj_def_epa','adj_net','fei_off','fei_def','fei_net','net_z']
FAMILIES={
 'fpi_trend':['fpi_trend_diff','gamecontrol_trend_diff','adjavgingamewp_trend_diff'],
 'adjusted_trend':['adj_off_epa_trend_diff','adj_def_epa_trend_edge','adj_net_trend_diff','fei_off_trend_diff','fei_def_trend_edge','fei_net_trend_diff','net_z_trend_diff'],
 'net_trend':['fpi_trend_diff','adj_net_trend_diff','fei_net_trend_diff','net_z_trend_diff'],
 'control_net_trend':['gamecontrol_diff','adjavgingamewp_diff','fpi_trend_diff','gamecontrol_trend_diff','adjavgingamewp_trend_diff','adj_net_trend_diff','fei_net_trend_diff'],
 'all_trend':['fpi_trend_diff','gamecontrol_trend_diff','adjavgingamewp_trend_diff','adj_off_epa_trend_diff','adj_def_epa_trend_edge','adj_net_trend_diff','fei_off_trend_diff','fei_def_trend_edge','fei_net_trend_diff','net_z_trend_diff'],
}

def load_adjusted(season):
    url=f'{fpi.BASE_URL}/cfb_ratings_weekly/cfb_ratings_weekly_{season}.rds';data=pyreadr.read_r(io.BytesIO(fpi.download_bytes(url)));df=next(iter(data.values())).copy();df.columns=[str(c).lower() for c in df.columns]
    for c in ('team_id','through_week',*ADJ_COLS):
        if c in df.columns:df[c]=pd.to_numeric(df[c],errors='coerce')
    df=df[df.team_id.notna()&df.through_week.notna()].copy();df['week']=df.through_week
    return df.sort_values(['team_id','week']).drop_duplicates(['team_id','week'],keep='last')

def last_two(df,tid,week):
    try:tid=float(tid)
    except:return None,None
    x=df[(df.team_id==tid)&(df.week<int(week))].sort_values('week')
    if x.empty:return None,None
    latest=x.iloc[-1];prev=x.iloc[-2] if len(x)>=2 else None
    return latest,prev

def delta(latest,prev,col):
    if latest is None or prev is None or col not in latest.index or col not in prev.index:return np.nan
    try:a=float(latest[col]);b=float(prev[col]);return a-b if np.isfinite(a) and np.isfinite(b) else np.nan
    except:return np.nan

def build_season(season):
    df=fpi.build_season(season).copy();fp=fpi.fpi_lookup(season);ad=load_adjusted(season)
    for idx,row in df.iterrows():
        ha,hp=last_two(fp,row.home_team_id,row.week);aa,ap=last_two(fp,row.away_team_id,row.week)
        for c in FPI_COLS:
            hd=delta(ha,hp,c);adlt=delta(aa,ap,c);df.loc[idx,f'{c}_trend_diff']=hd-adlt if np.isfinite(hd) and np.isfinite(adlt) else np.nan
        hr,hpr=last_two(ad,row.home_team_id,row.week);ar,apr=last_two(ad,row.away_team_id,row.week)
        for c in ADJ_COLS:
            hd=delta(hr,hpr,c);adlt=delta(ar,apr,c)
            key=f'{c}_trend_edge' if c in ('adj_def_epa','fei_def') else f'{c}_trend_diff'
            # defense lower is better, so reverse the difference for defensive trend.
            df.loc[idx,key]=(adlt-hd if c in ('adj_def_epa','fei_def') else hd-adlt) if np.isfinite(hd) and np.isfinite(adlt) else np.nan
    print(season,'rows',len(df),'trend coverage',int(df.fpi_trend_diff.notna().sum()),int(df.adj_net_trend_diff.notna().sum()));return df

def fit(train,features,alpha):
    x=train[features].apply(pd.to_numeric,errors='coerce');mu=x.mean();x=x.fillna(mu);sd=x.std(ddof=0).replace(0.,1.);z=((x-mu)/sd).to_numpy(float);y=(train.actual-train.baseline).to_numpy(float);X=np.column_stack([np.ones(len(z)),z]);P=np.eye(X.shape[1])*alpha;P[0,0]=0.;b=np.linalg.solve(X.T@X+P,X.T@y);return {'features':features,'mu':mu,'sd':sd,'b':b}
def pred(df,m,cap):
    x=df[m['features']].apply(pd.to_numeric,errors='coerce').fillna(m['mu']);z=((x-m['mu'])/m['sd']).to_numpy(float);c=m['b'][0]+z@m['b'][1:];return df.baseline.to_numpy(float)+np.clip(c,-cap,cap)
def auc(s,l):
    s=np.asarray(s,float);l=np.asarray(l,int);n1=(l==1).sum();n0=(l==0).sum();
    if not n1 or not n0:return None
    r=pd.Series(s).rank(method='average').to_numpy();return float((r[l==1].sum()-n1*(n1+1)/2)/(n1*n0))
def met(df,p):
    p=np.asarray(p,float);e=p+df.market_home_spread.to_numpy(float);miss=df.actual.to_numpy(float)+df.market_home_spread.to_numpy(float);ok=np.isfinite(e)&np.isfinite(miss)&(np.abs(e)>1e-9)&(np.abs(miss)>1e-9);e=e[ok];miss=miss[ok];w=(e*miss>0).astype(int);rates=[]
    for t in TH:
        s=np.abs(e)>=t;n=int(s.sum());ww=int(w[s].sum());rates.append({'threshold':t,'n':n,'wins':ww,'losses':n-ww,'win_rate':ww/n if n else None})
    v=[r for r in rates if r['n']>=25];steps=sum(b['win_rate']>=a['win_rate'] for a,b in zip(v,v[1:]));viol=sum(max(0.,a['win_rate']-b['win_rate']) for a,b in zip(v,v[1:]));slope=v[-1]['win_rate']-v[0]['win_rate'] if len(v)>1 else -1.;high=next((r['win_rate'] for r in rates if r['threshold']==10 and r['n']>=25),None)
    return {'steps':steps,'possible':max(0,len(v)-1),'violation':viol,'slope':slope,'high':high,'auc':auc(np.abs(e),w),'mae':float(np.mean(np.abs(p-df.actual.to_numpy(float)))),'thresholds':rates}
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
    fs={s:build_season(s) for s in (*TRAIN,VALID,HOLD,FINAL)};df=pd.concat(fs.values(),ignore_index=True);tr=df[df.season.isin(TRAIN)];va=df[df.season==VALID];ho=df[df.season==HOLD];fi=df[df.season==FINAL];base={2024:met(va,va.baseline),2025:met(ho,ho.baseline),2026:met(fi,fi.baseline)};cand=[]
    for name,features in FAMILIES.items():
        usable=[f for f in features if f in df.columns and df[f].notna().any()]
        if not usable:continue
        for alpha in ALPHAS:
            m=fit(tr,usable,alpha)
            for cap in CAPS:cand.append({'family':name,'features':usable,'alpha':alpha,'cap':cap,'validation':met(va,pred(va,m,cap))})
    cand.sort(key=lambda r:key(r['validation']),reverse=True);ch=cand[0];m25=fit(pd.concat([tr,va]),ch['features'],ch['alpha']);r25=met(ho,pred(ho,m25,ch['cap']));m26=fit(pd.concat([tr,va,ho]),ch['features'],ch['alpha']);r26=met(fi,pred(fi,m26,ch['cap']))
    out={'market_predictor':False,'baseline':base,'chosen_on_2024':ch,'holdout_2025':r25,'holdout_2026':r26,'candidates':cand};(OUT/'cfb_adjusted_trend_edge_search.json').write_text(json.dumps(clean(out),indent=2,allow_nan=False))
    lines=['# CFB Opponent-Adjusted Trend Edge Search','',f"Chosen on 2024: {ch['family']} alpha={ch['alpha']} cap={ch['cap']}",'','| Season | Model | Steps | slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |','|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for yr,b,c in [(2024,base[2024],ch['validation']),(2025,base[2025],r25),(2026,base[2026],r26)]:
        for label,m in [('Baseline',b),('Adjusted trend',c)]:lines.append(f"| {yr} | {label} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} | {m['mae']:.3f} | "+' | '.join(rt(m,t) for t in TH)+' |')
    (OUT/'CFB_ADJUSTED_TREND_EDGE_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
