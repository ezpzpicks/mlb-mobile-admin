"""Test whether current-power movement needs confirmation from independent football quality signals.

No sportsbook line is used as a predictor. We build only pregame football features,
fit residual margin corrections to actual margins, select the specification on 2024
fixed 2/4/6/8/10 edge ordering, then confirm unchanged on 2025 and 2026.
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_combined_adjusted_edge_search as combo
from research import cfb_margin_ensemble_edge_quality as ens

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TRAIN=(2021,2022,2023);VALID=2024;HOLD=2025;FINAL=2026
TH=(2.,4.,6.,8.,10.);ALPHAS=(1.,4.,16.,64.,256.,1024.);CAPS=(2.,4.,6.,8.)

BASIC=['prior_power_margin','current_power_margin','current_scoring_diff','current_allowed_diff']
FPI=['gamecontrol_diff','adjavgingamewp_diff']
ADJ=['adj_net_diff','fei_net_diff','net_z_diff']
COMP=['off_epa_diff','def_epa_edge','off_success_diff','def_success_edge','early_epa_diff','def_early_epa_edge']
INTER=['power_x_prior','power_x_scoring','power_x_allowed','power_x_gamecontrol','power_x_ingamewp','power_x_adjnet','power_x_feinet','power_x_netz','power_x_comp_epa','power_x_comp_success','power_x_comp_early','power_reversal_mag','power_scoring_conflict_mag','power_quality_conflict_mag']

FAMILIES={
 'power_basic_interactions':BASIC+['power_x_prior','power_x_scoring','power_x_allowed','power_reversal_mag','power_scoring_conflict_mag'],
 'power_fpi_confirmation':BASIC+FPI+['power_x_scoring','power_x_gamecontrol','power_x_ingamewp','power_reversal_mag'],
 'power_adjusted_confirmation':BASIC+ADJ+['power_x_scoring','power_x_adjnet','power_x_feinet','power_x_netz','power_reversal_mag'],
 'power_competitive_confirmation':BASIC+COMP+['power_x_scoring','power_x_comp_epa','power_x_comp_success','power_x_comp_early','power_reversal_mag'],
 'power_all_confirmation':BASIC+FPI+ADJ+COMP+INTER,
 'quality_confirmation_only':FPI+ADJ+COMP+['power_x_gamecontrol','power_x_adjnet','power_x_feinet','power_x_comp_epa','power_quality_conflict_mag'],
}

def component_map(season:int)->pd.DataFrame:
    games=ens.base.games_from_pbp(season);games=ens.attach_market_spread(games,ens.base.cfb);ids=ens.fbs_ids(season);prior=ens.base.team_summary(ens.base.games_from_pbp(season-1));rows=[]
    for wk in sorted(pd.to_numeric(games.week,errors='coerce').dropna().astype(int).unique()):
        cur=ens.base.team_summary(games[games.week<int(wk)])
        for _,g in games[games.week==int(wk)].iterrows():
            if ids and str(g.game_id) not in ids:continue
            hs=float(g.market_home_spread) if pd.notna(g.market_home_spread) else np.nan
            if not np.isfinite(hs):continue
            a,h=str(g.away_team),str(g.home_team)
            pa,ph=ens.base.stat(prior,a),ens.base.stat(prior,h);ca,ch=ens.base.stat(cur,a),ens.base.stat(cur,h);ba,bh=ens.base.blend_stats(pa,ca),ens.base.blend_stats(ph,ch)
            rows.append({'game_id':str(g.game_id),
                'prior_power_margin':2.*(ph.power-pa.power),
                'current_power_margin':2.*((bh.power-ba.power)-(ph.power-pa.power)),
                'current_scoring_diff':(bh.ppg-ph.ppg)-(ba.ppg-pa.ppg),
                'current_allowed_diff':(ba.papg-pa.papg)-(bh.papg-ph.papg)})
    return pd.DataFrame(rows).drop_duplicates('game_id')

def safe(s):return pd.to_numeric(s,errors='coerce')
def signconflict(a,b):
    a=safe(a);b=safe(b);return np.where(np.isfinite(a)&np.isfinite(b)&(a*b<0),np.abs(a),0.0)

def build_season(season:int)->pd.DataFrame:
    df=combo.build_season(season).merge(component_map(season),on='game_id',how='left')
    p=safe(df.current_power_margin);prior=safe(df.prior_power_margin);sc=safe(df.current_scoring_diff);al=safe(df.current_allowed_diff)
    gc=safe(df.get('gamecontrol_diff'));iwp=safe(df.get('adjavgingamewp_diff'));an=safe(df.get('adj_net_diff'));fn=safe(df.get('fei_net_diff'));nz=safe(df.get('net_z_diff'))
    cepa=safe(df.get('off_epa_diff'))+safe(df.get('def_epa_edge'));csucc=safe(df.get('off_success_diff'))+safe(df.get('def_success_edge'));cearly=safe(df.get('early_epa_diff'))+safe(df.get('def_early_epa_edge'))
    df['power_x_prior']=p*prior;df['power_x_scoring']=p*sc;df['power_x_allowed']=p*al;df['power_x_gamecontrol']=p*gc;df['power_x_ingamewp']=p*iwp;df['power_x_adjnet']=p*an;df['power_x_feinet']=p*fn;df['power_x_netz']=p*nz;df['power_x_comp_epa']=p*cepa;df['power_x_comp_success']=p*csucc;df['power_x_comp_early']=p*cearly
    df['power_reversal_mag']=signconflict(p,prior);df['power_scoring_conflict_mag']=signconflict(p,sc)
    quality=pd.concat([gc.rename('gc'),an.rename('an'),fn.rename('fn'),cepa.rename('cepa')],axis=1).mean(axis=1,skipna=True)
    df['power_quality_conflict_mag']=signconflict(p,quality)
    print(season,'rows',len(df),'interaction coverage',int(df.power_x_adjnet.notna().sum()));return df

def fit(train,features,alpha):
    x=train[features].apply(pd.to_numeric,errors='coerce');mu=x.mean();x=x.fillna(mu);sd=x.std(ddof=0).replace(0.,1.);z=((x-mu)/sd).to_numpy(float);y=(train.actual-train.baseline).to_numpy(float);X=np.column_stack([np.ones(len(z)),z]);P=np.eye(X.shape[1])*alpha;P[0,0]=0.;b=np.linalg.solve(X.T@X+P,X.T@y);return {'features':features,'mu':mu,'sd':sd,'b':b}
def pred(df,m,cap):
    x=df[m['features']].apply(pd.to_numeric,errors='coerce').fillna(m['mu']);z=((x-m['mu'])/m['sd']).to_numpy(float);c=m['b'][0]+z@m['b'][1:];return df.baseline.to_numpy(float)+np.clip(c,-cap,cap)
def auc(s,l):
    s=np.asarray(s,float);l=np.asarray(l,int);n1=(l==1).sum();n0=(l==0).sum()
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
    out={'market_predictor':False,'baseline':base,'chosen_on_2024':ch,'holdout_2025':r25,'holdout_2026':r26,'candidates':cand};(OUT/'cfb_power_confirmation_edge_search.json').write_text(json.dumps(clean(out),indent=2,allow_nan=False))
    lines=['# CFB Power Confirmation Interaction Edge Search','',f"Chosen on 2024: {ch['family']} alpha={ch['alpha']} cap={ch['cap']}",'','| Season | Model | Steps | slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |','|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for yr,b,c in [(2024,base[2024],ch['validation']),(2025,base[2025],r25),(2026,base[2026],r26)]:
        for label,m in [('Baseline',b),('Power confirmation',c)]:lines.append(f"| {yr} | {label} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} | {m['mae']:.3f} | "+' | '.join(rt(m,t) for t in TH)+' |')
    lines+=['','## Top 2024 candidates','','| Family | Alpha | Cap | Steps | slope | 10+ | AUC | MAE |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in cand[:20]:
        mm=r['validation'];lines.append(f"| {r['family']} | {r['alpha']:.0f} | {r['cap']:.0f} | {mm['steps']}/{mm['possible']} | {100*mm['slope']:+.1f}pp | {100*(mm['high'] or 0):.1f}% | {mm['auc']:.3f} | {mm['mae']:.3f} |")
    (OUT/'CFB_POWER_CONFIRMATION_EDGE_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
