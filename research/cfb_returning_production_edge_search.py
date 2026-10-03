"""Test preseason returning production as a football-only CFB margin input.

The hypothesis is structural: prior-season power should be less trustworthy when
roster continuity is low or differs sharply between teams. Returning-production
values are season-level preseason inputs; market lines are evaluation-only.
"""
from __future__ import annotations
import io,json,math
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_power_confirmation_edge_search as power
from research import cfb_weekly_fpi_edge_search as fpi

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TRAIN=(2021,2022,2023);VALID=2024;HOLD=2025;FINAL=2026
TH=(2.,4.,6.,8.,10.);ALPHAS=(1.,4.,16.,64.,256.);CAPS=(2.,4.,6.,8.)
RAW=['off_returning','def_returning','overall_returning','n_returning']
FAMILIES={
 'returning_diff':['off_returning_diff','def_returning_diff','overall_returning_diff','n_returning_diff'],
 'prior_power_continuity':['prior_power_margin','overall_returning_diff','avg_overall_returning','prior_power_x_avg_returning','prior_power_x_returning_diff'],
 'power_continuity':['prior_power_margin','current_power_margin','overall_returning_diff','avg_overall_returning','prior_power_x_avg_returning','prior_power_x_returning_diff','current_power_x_returning_diff'],
 'full_returning_power':['off_returning_diff','def_returning_diff','overall_returning_diff','n_returning_diff','avg_overall_returning','prior_power_margin','current_power_margin','prior_power_x_avg_returning','prior_power_x_returning_diff','current_power_x_returning_diff'],
}

def load_returning(season):
    url=f'{fpi.BASE_URL}/cfb_returning_production/cfb_returning_production_{season}.parquet';df=pd.read_parquet(io.BytesIO(fpi.download_bytes(url)));df.columns=[str(c).lower() for c in df.columns]
    df['team_id']=pd.to_numeric(df.team_id,errors='coerce')
    for c in RAW:
        if c in df.columns:df[c]=pd.to_numeric(df[c],errors='coerce')
    return df[df.team_id.notna()].drop_duplicates('team_id').set_index('team_id')
def build_season(season):
    df=power.build_season(season).copy();rp=load_returning(season);matched=0
    for idx,row in df.iterrows():
        try:ai=float(row.away_team_id);hi=float(row.home_team_id)
        except:continue
        if ai not in rp.index or hi not in rp.index:continue
        a=rp.loc[ai];h=rp.loc[hi];matched+=1
        for c in RAW:
            av=float(a[c]) if c in a.index and pd.notna(a[c]) else np.nan;hv=float(h[c]) if c in h.index and pd.notna(h[c]) else np.nan;df.loc[idx,f'{c}_diff']=hv-av if np.isfinite(av) and np.isfinite(hv) else np.nan
        ao=float(a.overall_returning) if pd.notna(a.overall_returning) else np.nan;ho=float(h.overall_returning) if pd.notna(h.overall_returning) else np.nan;df.loc[idx,'avg_overall_returning']=(ao+ho)/2 if np.isfinite(ao) and np.isfinite(ho) else np.nan
    p0=pd.to_numeric(df.prior_power_margin,errors='coerce');pc=pd.to_numeric(df.current_power_margin,errors='coerce');rd=pd.to_numeric(df.get('overall_returning_diff'),errors='coerce');avg=pd.to_numeric(df.get('avg_overall_returning'),errors='coerce')
    df['prior_power_x_avg_returning']=p0*avg;df['prior_power_x_returning_diff']=p0*rd;df['current_power_x_returning_diff']=pc*rd
    print(season,'rows',len(df),'returning matched',matched);return df

def fit(train,features,alpha):
    x=train[features].apply(pd.to_numeric,errors='coerce');mu=x.mean();x=x.fillna(mu);sd=x.std(ddof=0).replace(0.,1.);z=((x-mu)/sd).to_numpy(float);y=(train.actual-train.baseline).to_numpy(float);X=np.column_stack([np.ones(len(z)),z]);P=np.eye(X.shape[1])*alpha;P[0,0]=0.;b=np.linalg.solve(X.T@X+P,X.T@y);return {'features':features,'mu':mu,'sd':sd,'b':b}
def pred(df,m,cap):
    x=df[m['features']].apply(pd.to_numeric,errors='coerce').fillna(m['mu']);z=((x-m['mu'])/m['sd']).to_numpy(float);corr=m['b'][0]+z@m['b'][1:];return df.baseline.to_numpy(float)+np.clip(corr,-cap,cap)
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
    out={'market_predictor':False,'chosen_on_2024':ch,'baseline':base,'holdout_2025':r25,'holdout_2026':r26,'candidates':cand};(OUT/'cfb_returning_production_edge_search.json').write_text(json.dumps(clean(out),indent=2,allow_nan=False))
    lines=['# CFB Returning Production Edge Search','',f"Chosen on 2024: {ch['family']} alpha={ch['alpha']} cap={ch['cap']}",'','| Season | Model | Steps | slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |','|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for yr,b,q in [(2024,base[2024],ch['validation']),(2025,base[2025],r25),(2026,base[2026],r26)]:
        for label,m in [('Baseline',b),('Returning production',q)]:lines.append(f"| {yr} | {label} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} | {m['mae']:.3f} | "+' | '.join(rt(m,t) for t in TH)+' |')
    lines+=['','## Top 2024 candidates','','| Family | Alpha | Cap | Steps | slope | 10+ | AUC | MAE |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in cand[:20]:
        m=r['validation'];lines.append(f"| {r['family']} | {r['alpha']:.0f} | {r['cap']:.0f} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {100*(m['high'] or 0):.1f}% | {m['auc']:.3f} | {m['mae']:.3f} |")
    (OUT/'CFB_RETURNING_PRODUCTION_EDGE_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
