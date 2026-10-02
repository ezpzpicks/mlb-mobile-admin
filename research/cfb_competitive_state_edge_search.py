"""Test competitive-game-state CFB efficiency as incremental margin signal.

Raw season efficiency can be distorted by garbage time. This research uses classic
cfbfastR play-by-play and computes cumulative pregame efficiency only from plays
that were still competitive: pre-play win probability 10%-90% when available,
falling back to score differential <=17. Sportsbook lines are evaluation-only.

Protocol: 2021-23 fit, 2024 select family/alpha/cap on fixed 2/4/6/8/10 edge
ordering, 2025 confirm, then 2026 confirm with the exact frozen specification.
"""
from __future__ import annotations
import json, math
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
import polars as pl
from research import cfb_margin_ensemble_edge_quality as ens

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TRAIN=(2021,2022,2023);VALID=2024;HOLD=2025;FINAL=2026
TH=(2.,4.,6.,8.,10.);ALPHAS=(1.,4.,16.,64.,256.);CAPS=(2.,4.,6.,8.)
FAMILIES={
 'epa':['off_epa_diff','def_epa_edge'],
 'success':['off_success_diff','def_success_edge'],
 'pass_rush':['pass_epa_diff','rush_epa_diff','def_pass_epa_edge','def_rush_epa_edge'],
 'early_down':['early_epa_diff','def_early_epa_edge'],
 'explosive':['explosive_diff','def_explosive_edge'],
 'core_competitive':['off_epa_diff','def_epa_edge','off_success_diff','def_success_edge','pass_epa_diff','rush_epa_diff','early_epa_diff','def_early_epa_edge'],
 'all_competitive':['off_epa_diff','def_epa_edge','off_success_diff','def_success_edge','pass_epa_diff','rush_epa_diff','def_pass_epa_edge','def_rush_epa_edge','early_epa_diff','def_early_epa_edge','explosive_diff','def_explosive_edge'],
}

def norm(v):
    try:return ens.base.cfb._normalize_team(v)
    except Exception:return ' '.join(str(v or '').lower().split())

def first(names,*opts):
    lower={x.lower():x for x in names}
    for o in opts:
        if o in names:return o
        if o.lower() in lower:return lower[o.lower()]
    return None

def load_pbp(season):
    loader=getattr(ens.base.cfb,'_download_open_asset_now',None)
    path=loader('cfbfastR_cfb_pbp',season,('play_by_play','pbp')) if callable(loader) else ens.base.cfb._download_open_asset('cfbfastR_cfb_pbp',season,('play_by_play','pbp'))
    scan=pl.scan_parquet(str(path));names=set(scan.collect_schema().names())
    aliases={
      'week':('week',),'pos_team':('pos_team','posteam'),'def_team':('def_pos_team','defteam'),
      'epa':('EPA','epa'),'success':('success',),'pass':('pass',),'rush':('rush',),'down':('down',),
      'wp':('wp_before','wp'),'pos_score':('pos_team_score','posteam_score'),'def_score':('def_pos_team_score','defteam_score')
    }
    expr=[]
    for canon,opts in aliases.items():
        a=first(names,*opts)
        if a:expr.append(pl.col(a).alias(canon))
    df=scan.select(expr).collect(engine='streaming').to_pandas()
    for c in ('week','epa','success','pass','rush','down','wp','pos_score','def_score'):
        if c in df.columns:df[c]=pd.to_numeric(df[c],errors='coerce')
    df['pos_team']=df.get('pos_team',pd.Series('',index=df.index)).map(norm);df['def_team']=df.get('def_team',pd.Series('',index=df.index)).map(norm)
    return df

def competitive_mask(df):
    validplay=df.epa.notna() & df.pos_team.ne('') & df.def_team.ne('')
    if 'pass' in df.columns and 'rush' in df.columns:validplay &= (df['pass'].fillna(0).gt(0)|df['rush'].fillna(0).gt(0))
    wpok=pd.Series(False,index=df.index)
    if 'wp' in df.columns:wpok=df.wp.between(.10,.90,inclusive='both')
    scoreok=pd.Series(False,index=df.index)
    if 'pos_score' in df.columns and 'def_score' in df.columns:scoreok=(df.pos_score-df.def_score).abs().le(17)
    if 'wp' in df.columns:
        comp=wpok | (df.wp.isna() & scoreok)
    else:comp=scoreok
    return validplay & comp

def team_metrics(pbp,week):
    x=pbp[(pbp.week < int(week)) & competitive_mask(pbp)].copy()
    if x.empty:return {}
    x['is_pass']=x.get('pass',0).fillna(0).gt(0);x['is_rush']=x.get('rush',0).fillna(0).gt(0);x['is_early']=pd.to_numeric(x.get('down'),errors='coerce').le(2);x['explosive']=x.epa.gt(1.0)
    out={}
    teams=set(x.pos_team)|set(x.def_team)
    for team in teams:
        o=x[x.pos_team==team];d=x[x.def_team==team]
        def mean(frame,col='epa',mask=None):
            z=frame if mask is None else frame[mask];v=pd.to_numeric(z[col],errors='coerce').dropna();return float(v.mean()) if len(v) else np.nan
        out[team]={
          'off_epa':mean(o),'def_epa':mean(d),'off_success':mean(o,'success'),'def_success':mean(d,'success'),
          'pass_epa':mean(o,mask=o.is_pass),'rush_epa':mean(o,mask=o.is_rush),'def_pass_epa':mean(d,mask=d.is_pass),'def_rush_epa':mean(d,mask=d.is_rush),
          'early_epa':mean(o,mask=o.is_early),'def_early_epa':mean(d,mask=d.is_early),
          'explosive':mean(o,'explosive'),'def_explosive':mean(d,'explosive'),
          'plays':len(o)
        }
    return out

def build_season(season):
    games=ens.base.games_from_pbp(season);games=ens.attach_market_spread(games,ens.base.cfb);ids=ens.fbs_ids(season);pbp=load_pbp(season);prior=ens.base.team_summary(ens.base.games_from_pbp(season-1));rows=[]
    for wk in sorted(pd.to_numeric(games.week,errors='coerce').dropna().astype(int).unique()):
        cur=ens.base.team_summary(games[games.week<int(wk)]);tm=team_metrics(pbp,wk)
        for _,g in games[games.week==int(wk)].iterrows():
            if ids and str(g.game_id) not in ids:continue
            hs=float(g.market_home_spread) if pd.notna(g.market_home_spread) else np.nan
            if not np.isfinite(hs):continue
            a,h=str(g.away_team),str(g.home_team);ascore,hscore=ens.base.spread_scores(a,h,ens.base.truthy(g.neutral),prior,cur);am=tm.get(norm(a),{});hm=tm.get(norm(h),{})
            def v(m,k):
                try:return float(m.get(k,np.nan))
                except:return np.nan
            rows.append({'season':season,'week':wk,'game_id':str(g.game_id),'actual':float(g.actual_margin),'market_home_spread':hs,'baseline':float(hscore-ascore),
              'off_epa_diff':v(hm,'off_epa')-v(am,'off_epa'),'def_epa_edge':v(am,'def_epa')-v(hm,'def_epa'),
              'off_success_diff':v(hm,'off_success')-v(am,'off_success'),'def_success_edge':v(am,'def_success')-v(hm,'def_success'),
              'pass_epa_diff':v(hm,'pass_epa')-v(am,'pass_epa'),'rush_epa_diff':v(hm,'rush_epa')-v(am,'rush_epa'),
              'def_pass_epa_edge':v(am,'def_pass_epa')-v(hm,'def_pass_epa'),'def_rush_epa_edge':v(am,'def_rush_epa')-v(hm,'def_rush_epa'),
              'early_epa_diff':v(hm,'early_epa')-v(am,'early_epa'),'def_early_epa_edge':v(am,'def_early_epa')-v(hm,'def_early_epa'),
              'explosive_diff':v(hm,'explosive')-v(am,'explosive'),'def_explosive_edge':v(am,'def_explosive')-v(hm,'def_explosive')})
    df=pd.DataFrame(rows);print(season,'rows',len(df),'competitive feature coverage',int(df.off_epa_diff.notna().sum()));return df

def fit(train,features,alpha):
    x=train[features].apply(pd.to_numeric,errors='coerce');mu=x.mean();x=x.fillna(mu);sd=x.std(ddof=0).replace(0.,1.);z=((x-mu)/sd).to_numpy();y=(train.actual-train.baseline).to_numpy();X=np.column_stack([np.ones(len(z)),z]);P=np.eye(X.shape[1])*alpha;P[0,0]=0.;b=np.linalg.solve(X.T@X+P,X.T@y);return {'f':features,'mu':mu,'sd':sd,'b':b}
def pred(df,m,cap):
    x=df[m['f']].apply(pd.to_numeric,errors='coerce').fillna(m['mu']);z=((x-m['mu'])/m['sd']).to_numpy();c=m['b'][0]+z@m['b'][1:];return df.baseline.to_numpy()+np.clip(c,-cap,cap)
def auc(s,l):
    s=np.asarray(s,float);l=np.asarray(l,int);n1=(l==1).sum();n0=(l==0).sum()
    if not n1 or not n0:return None
    r=pd.Series(s).rank(method='average').to_numpy();return float((r[l==1].sum()-n1*(n1+1)/2)/(n1*n0))
def met(df,p):
    p=np.asarray(p,float);e=p+df.market_home_spread.to_numpy();miss=df.actual.to_numpy()+df.market_home_spread.to_numpy();ok=np.isfinite(e)&np.isfinite(miss)&(np.abs(e)>1e-9)&(np.abs(miss)>1e-9);e=e[ok];miss=miss[ok];w=(e*miss>0).astype(int);rates=[]
    for t in TH:
        s=np.abs(e)>=t;n=int(s.sum());ww=int(w[s].sum());rates.append({'threshold':t,'n':n,'wins':ww,'losses':n-ww,'win_rate':ww/n if n else None})
    v=[r for r in rates if r['n']>=25];steps=sum(b['win_rate']>=a['win_rate'] for a,b in zip(v,v[1:]));viol=sum(max(0,a['win_rate']-b['win_rate']) for a,b in zip(v,v[1:]));slope=v[-1]['win_rate']-v[0]['win_rate'] if len(v)>1 else -1;high=next((r['win_rate'] for r in rates if r['threshold']==10 and r['n']>=25),None)
    return {'steps':steps,'possible':max(0,len(v)-1),'violation':viol,'slope':slope,'high':high,'auc':auc(np.abs(e),w),'mae':float(np.mean(np.abs(p-df.actual.to_numpy()))),'thresholds':rates}
def key(m):return (m['steps'],-m['violation'],m['slope'],m['high'] if m['high'] is not None else -1,m['auc'] if m['auc'] is not None else -1)
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
      for alpha in ALPHAS:
        m=fit(tr,features,alpha)
        for cap in CAPS:cand.append({'family':name,'features':features,'alpha':alpha,'cap':cap,'validation':met(va,pred(va,m,cap))})
    cand.sort(key=lambda r:key(r['validation']),reverse=True);ch=cand[0];m25=fit(pd.concat([tr,va]),ch['features'],ch['alpha']);r25=met(ho,pred(ho,m25,ch['cap']));m26=fit(pd.concat([tr,va,ho]),ch['features'],ch['alpha']);r26=met(fi,pred(fi,m26,ch['cap']))
    out={'market_predictor':False,'competitive_definition':'wp_before 0.10-0.90; if WP missing abs pre-play score diff <=17','baseline':base,'chosen_on_2024':ch,'holdout_2025':r25,'holdout_2026':r26,'candidates':cand};(OUT/'cfb_competitive_state_edge_search.json').write_text(json.dumps(clean(out),indent=2,allow_nan=False))
    lines=['# CFB Competitive-State Efficiency Edge Search','',f"Chosen on 2024: {ch['family']} alpha={ch['alpha']} cap={ch['cap']}",'','| Season | Model | Steps | slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |','|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for yr,b,c in [(2024,base[2024],ch['validation']),(2025,base[2025],r25),(2026,base[2026],r26)]:
      for label,m in [('Baseline',b),('Competitive-state',c)]:lines.append(f"| {yr} | {label} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} | {m['mae']:.3f} | "+' | '.join(rt(m,t) for t in TH)+' |')
    lines+=['','## Top candidates','','| Family | Alpha | Cap | Steps | slope | 10+ | AUC | MAE |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in cand[:15]:
      m=r['validation'];lines.append(f"| {r['family']} | {r['alpha']:.0f} | {r['cap']:.0f} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {100*(m['high'] or 0):.1f}% | {m['auc']:.3f} | {m['mae']:.3f} |")
    (OUT/'CFB_COMPETITIVE_STATE_EDGE_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
