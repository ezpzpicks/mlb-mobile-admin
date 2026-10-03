"""Test leakage-safe weekly turnover luck / expected-turnover signal for CFB margins.

SportsDataverse weekly team summaries expose actual turnover margin, expected
turnover margin, and turnover_luck = 5 * (actual - expected). We use only the
latest through_week < game week. Sportsbook lines are evaluation-only.
"""
from __future__ import annotations
import io,json,math
from pathlib import Path
import numpy as np
import pandas as pd
import pyreadr
from research import cfb_power_confirmation_edge_search as power
from research import cfb_weekly_fpi_edge_search as fpi

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TRAIN=(2021,2022,2023);VALID=2024;HOLD=2025;FINAL=2026
TH=(2.,4.,6.,8.,10.);ALPHAS=(1.,4.,16.,64.,256.);CAPS=(2.,4.,6.,8.)
RAW=['turnover_margin','expected_turnover_margin','turnover_luck','expected_turnovers_off','expected_turnovers_def','turnovers_off','turnovers_def']
FAMILIES={
 'luck_only':['turnover_luck_diff'],
 'expected_only':['expected_turnover_margin_diff','expected_turnovers_off_diff','expected_turnovers_def_diff'],
 'turnover_context':['turnover_margin_diff','expected_turnover_margin_diff','turnover_luck_diff','expected_turnovers_off_diff','expected_turnovers_def_diff'],
 'luck_power':['turnover_luck_diff','current_power_margin','power_x_turnover_luck','power_turnover_luck_conflict_mag'],
 'turnover_power_context':['turnover_margin_diff','expected_turnover_margin_diff','turnover_luck_diff','current_power_margin','current_scoring_diff','power_x_turnover_luck','power_x_expected_turnover','power_turnover_luck_conflict_mag'],
}

def load_weekly(season):
    url=f'{fpi.BASE_URL}/cfb_team_summaries_weekly/cfb_team_summaries_weekly_{season}.rds';data=pyreadr.read_r(io.BytesIO(fpi.download_bytes(url)));df=next(iter(data.values())).copy();df.columns=[str(c).lower() for c in df.columns]
    idc='pos_team_id' if 'pos_team_id' in df.columns else ('team_id' if 'team_id' in df.columns else None)
    if idc is None or 'through_week' not in df.columns:raise RuntimeError(f'{season} summaries missing id/through_week: {list(df.columns)[:20]}')
    df['team_id']=pd.to_numeric(df[idc],errors='coerce');df['week']=pd.to_numeric(df.through_week,errors='coerce')
    for c in RAW:
        if c in df.columns:df[c]=pd.to_numeric(df[c],errors='coerce')
    df=df[df.team_id.notna()&df.week.notna()].copy();return df.sort_values(['team_id','week']).drop_duplicates(['team_id','week'],keep='last')
def prior(df,tid,week):
    try:tid=float(tid)
    except:return None
    x=df[(df.team_id==tid)&(df.week<int(week))];return None if x.empty else x.sort_values('week').iloc[-1]
def build_season(season):
    df=power.build_season(season).copy();wk=load_weekly(season);matched=0
    for idx,row in df.iterrows():
        a=prior(wk,row.away_team_id,row.week);h=prior(wk,row.home_team_id,row.week)
        if a is None or h is None:continue
        matched+=1
        for c in RAW:
            av=float(a[c]) if c in a.index and pd.notna(a[c]) else np.nan;hv=float(h[c]) if c in h.index and pd.notna(h[c]) else np.nan;df.loc[idx,f'{c}_diff']=hv-av if np.isfinite(av) and np.isfinite(hv) else np.nan
    p=pd.to_numeric(df.current_power_margin,errors='coerce');luck=pd.to_numeric(df.get('turnover_luck_diff'),errors='coerce');exp=pd.to_numeric(df.get('expected_turnover_margin_diff'),errors='coerce')
    df['power_x_turnover_luck']=p*luck;df['power_x_expected_turnover']=p*exp;df['power_turnover_luck_conflict_mag']=np.where(np.isfinite(p)&np.isfinite(luck)&(p*luck<0),np.abs(p),0.)
    print(season,'rows',len(df),'turnover matched',matched,'luck coverage',int(df.turnover_luck_diff.notna().sum()));return df

def fit(train,features,alpha):
    x=train[features].apply(pd.to_numeric,errors='coerce');mu=x.mean();x=x.fillna(mu);sd=x.std(ddof=0).replace(0.,1.);z=((x-mu)/sd).to_numpy(float);y=(train.actual-train.baseline).to_numpy(float);X=np.column_stack([np.ones(len(z)),z]);P=np.eye(X.shape[1])*alpha;P[0,0]=0.;b=np.linalg.solve(X.T@X+P,X.T@y);return {'features':features,'mu':mu,'sd':sd,'b':b}
def pred(df,m,cap):
    x=df[m['features']].apply(pd.to_numeric,errors='coerce').fillna(m['mu']);z=((x-m['mu'])/m['sd']).to_numpy(float);c=m['b'][0]+z@m['b'][1:];return df.baseline.to_numpy(float)+np.clip(c,-cap,cap)
def met(df,p):return power.met(df,p)
def key(m):return power.key(m)
def rt(m,t):return power.rt(m,t)
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
    out={'market_predictor':False,'chosen_on_2024':ch,'baseline':base,'holdout_2025':r25,'holdout_2026':r26,'candidates':cand};(OUT/'cfb_turnover_luck_edge_search.json').write_text(json.dumps(clean(out),indent=2,allow_nan=False))
    lines=['# CFB Turnover Luck Edge Search','',f"Chosen on 2024: {ch['family']} alpha={ch['alpha']} cap={ch['cap']}",'','| Season | Model | Steps | slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |','|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for yr,b,q in [(2024,base[2024],ch['validation']),(2025,base[2025],r25),(2026,base[2026],r26)]:
        for label,m in [('Baseline',b),('Turnover luck',q)]:lines.append(f"| {yr} | {label} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} | {m['mae']:.3f} | "+' | '.join(rt(m,t) for t in TH)+' |')
    lines+=['','## Top 2024 candidates','','| Family | Alpha | Cap | Steps | slope | 10+ | AUC | MAE |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in cand[:20]:
        m=r['validation'];lines.append(f"| {r['family']} | {r['alpha']:.0f} | {r['cap']:.0f} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {100*(m['high'] or 0):.1f}% | {m['auc']:.3f} | {m['mae']:.3f} |")
    (OUT/'CFB_TURNOVER_LUCK_EDGE_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
