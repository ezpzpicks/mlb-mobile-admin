"""Test leakage-safe weekly ESPN FPI/opponent-adjusted efficiency as CFB margin inputs.

SportsDataverse publishes contemporaneous weekly ESPN FPI snapshots. We use only
snapshots from a prior week, exclude out-of-sequence/non-contemporaneous rows,
and never use a sportsbook line as a prediction feature.

Protocol:
- 2021-23 fit residual corrections to actual margin - existing football margin.
- 2024 selects feature family / ridge alpha / correction cap on fixed 2/4/6/8/10 ATS ordering.
- Refit chosen specification through 2024 -> confirm on 2025.
- Refit exact same specification through 2025 -> confirm on 2026, zero retuning.
"""
from __future__ import annotations
import io, json, math
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
import requests
import pyreadr
from research import cfb_margin_ensemble_edge_quality as ens

OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TRAIN=(2021,2022,2023);VALID=2024;HOLD=2025;FINAL=2026
TH=(2.,4.,6.,8.,10.);ALPHAS=(1.,4.,16.,64.,256.);CAPS=(2.,4.,6.,8.)
BASE_URL='https://github.com/sportsdataverse/sportsdataverse-data/releases/download'

# Higher is better for the efficiency fields below according to the ESPN/SportsDataverse definitions.
FAMILIES={
 'fpi':['fpi_diff'],
 'efficiency':['offefficiency_diff','defefficiency_diff','stefficiency_diff','totefficiency_diff'],
 'epa_components':['epaoffense_diff','epadefense_diff','epaspecialteams_diff'],
 'control':['gamecontrol_diff','adjavgingamewp_diff'],
 'sor_sos':['accomplishment_diff','avgsosrank_diff','topsosrank_diff'],
 'fpi_efficiency':['fpi_diff','offefficiency_diff','defefficiency_diff','stefficiency_diff','totefficiency_diff'],
 'fpi_control':['fpi_diff','gamecontrol_diff','adjavgingamewp_diff'],
 'all_adjusted':['fpi_diff','offefficiency_diff','defefficiency_diff','stefficiency_diff','totefficiency_diff','epaoffense_diff','epadefense_diff','epaspecialteams_diff','gamecontrol_diff','adjavgingamewp_diff','accomplishment_diff','avgsosrank_diff','topsosrank_diff'],
}
RAW=['fpi','offefficiency','defefficiency','stefficiency','totefficiency','epaoffense','epadefense','epaspecialteams','gamecontrol','adjavgingamewp','accomplishment','avgsosrank','topsosrank']


def norm(v):
    try:return ens.base.cfb._normalize_team(v)
    except Exception:return ' '.join(str(v or '').lower().replace('&','and').split())

def download_bytes(url):
    r=requests.get(url,timeout=90);r.raise_for_status();return r.content

def load_fpi(season):
    url=f'{BASE_URL}/cfb_fpi_weekly/cfb_fpi_weekly_{season}.rds'
    data=pyreadr.read_r(io.BytesIO(download_bytes(url)))
    df=next(iter(data.values())).copy()
    df.columns=[str(c).lower() for c in df.columns]
    for c in ('season','week','team_id',*RAW):
        if c in df.columns:df[c]=pd.to_numeric(df[c],errors='coerce')
    if 'snapshot_out_of_sequence' in df.columns:
        s=df.snapshot_out_of_sequence.astype(str).str.lower();df=df[~s.isin(['true','1','yes'])].copy()
    if 'snapshot_is_contemporaneous' in df.columns:
        s=df.snapshot_is_contemporaneous.astype(str).str.lower();df=df[s.isin(['true','1','yes'])].copy()
    return df

def load_game_team_ids(season):
    """Return game_id -> ESPN home/away team ids directly from the PBP asset.

    The SportsDataverse PBP schema contains home_team_id/away_team_id. Joining on
    those ids is materially safer than a provider-name crosswalk (Miami, USC,
    Louisiana, etc.) and removes name matching from this audit entirely.
    """
    path=ens.base.cfb._download_open_asset('cfbfastR_cfb_pbp',season,('play_by_play','pbp'))
    if path is None or not Path(path).exists():raise RuntimeError(f'PBP unavailable for {season}')
    aliases={
      'game_id':('game_id','id_game','gameId'),
      'home_team_id':('home_team_id','homeTeamId','home_id'),
      'away_team_id':('away_team_id','awayTeamId','away_id'),
    }
    frame=ens.base.cfb._read_open_parquet(Path(path),aliases)
    if frame is None or frame.empty:raise RuntimeError(f'No PBP team ids for {season}')
    frame=frame.copy();frame['game_id']=frame['game_id'].astype(str)
    for c in ('home_team_id','away_team_id'):frame[c]=pd.to_numeric(frame[c],errors='coerce')
    frame=frame.dropna(subset=['home_team_id','away_team_id'])
    return frame.groupby('game_id',as_index=False)[['home_team_id','away_team_id']].first()

def fpi_lookup(season):
    df=load_fpi(season)
    if 'team_id' not in df.columns:raise RuntimeError(f'FPI {season} missing team_id')
    df['team_id']=pd.to_numeric(df.team_id,errors='coerce');df['week']=pd.to_numeric(df.week,errors='coerce')
    df=df[df.team_id.notna() & df.week.notna()].copy()
    sort_cols=['team_id','week']+(['run_date_time_key'] if 'run_date_time_key' in df.columns else [])
    df=df.sort_values(sort_cols).drop_duplicates(['team_id','week'],keep='last')
    return df

def prior_snapshot(fpi,team_id,week):
    try:tid=float(team_id)
    except Exception:return None
    rows=fpi[(fpi.team_id==tid)&(fpi.week < int(week))]
    if rows.empty:return None
    return rows.sort_values('week').iloc[-1]

def build_season(season):
    games=ens.base.games_from_pbp(season);games=ens.attach_market_spread(games,ens.base.cfb)
    game_ids=load_game_team_ids(season)
    games=games.copy();games['game_id']=games.game_id.astype(str);games=games.merge(game_ids,on='game_id',how='left')
    ids=ens.fbs_ids(season)
    game_rows=[]
    prior=ens.base.team_summary(ens.base.games_from_pbp(season-1))
    for wk in sorted(pd.to_numeric(games.week,errors='coerce').dropna().astype(int).unique()):
        cur=ens.base.team_summary(games[games.week<int(wk)])
        for _,g in games[games.week==int(wk)].iterrows():
            if ids and str(g.game_id) not in ids:continue
            hs=float(g.market_home_spread) if pd.notna(g.market_home_spread) else np.nan
            if not np.isfinite(hs):continue
            a,h=str(g.away_team),str(g.home_team);ascore,hscore=ens.base.spread_scores(a,h,ens.base.truthy(g.neutral),prior,cur)
            game_rows.append({'season':season,'week':int(wk),'game_id':str(g.game_id),'away_team':a,'home_team':h,'away_team_id':g.away_team_id,'home_team_id':g.home_team_id,'actual':float(g.actual_margin),'market_home_spread':hs,'baseline':float(hscore-ascore)})
    df=pd.DataFrame(game_rows)
    fpi=fpi_lookup(season);matched=0
    for idx,row in df.iterrows():
        a=prior_snapshot(fpi,row.away_team_id,row.week);h=prior_snapshot(fpi,row.home_team_id,row.week)
        if a is None or h is None:continue
        matched+=1
        for col in RAW:
            av=float(a[col]) if col in a.index and pd.notna(a[col]) else np.nan
            hv=float(h[col]) if col in h.index and pd.notna(h[col]) else np.nan
            # For SOS ranks lower rank = harder/better; reverse sign so positive always favors home.
            if col in ('avgsosrank','topsosrank'):
                df.loc[idx,f'{col}_diff']=av-hv if np.isfinite(av) and np.isfinite(hv) else np.nan
            else:
                df.loc[idx,f'{col}_diff']=hv-av if np.isfinite(av) and np.isfinite(hv) else np.nan
    print(season,'rows',len(df),'matched prior weekly ratings',matched)
    return df

def fit_ridge(train,features,alpha):
    x=train[features].apply(pd.to_numeric,errors='coerce');mu=x.mean();x=x.fillna(mu);sd=x.std(ddof=0).replace(0.,1.);z=((x-mu)/sd).to_numpy(float)
    y=(train.actual-train.baseline).to_numpy(float);X=np.column_stack([np.ones(len(z)),z]);P=np.eye(X.shape[1])*alpha;P[0,0]=0.;b=np.linalg.solve(X.T@X+P,X.T@y)
    return {'features':features,'mu':mu,'sd':sd,'b':b}

def predict(df,m,cap):
    x=df[m['features']].apply(pd.to_numeric,errors='coerce').fillna(m['mu']);z=((x-m['mu'])/m['sd']).to_numpy(float);c=m['b'][0]+z@m['b'][1:];c=np.clip(c,-cap,cap);return df.baseline.to_numpy(float)+c

def auc(s,l):
    s=np.asarray(s,float);l=np.asarray(l,int);n1=int((l==1).sum());n0=int((l==0).sum())
    if not n1 or not n0:return None
    r=pd.Series(s).rank(method='average').to_numpy();return float((r[l==1].sum()-n1*(n1+1)/2)/(n1*n0))
def metrics(df,p):
    p=np.asarray(p,float);e=p+df.market_home_spread.to_numpy(float);miss=df.actual.to_numpy(float)+df.market_home_spread.to_numpy(float);ok=np.isfinite(e)&np.isfinite(miss)&(np.abs(e)>1e-9)&(np.abs(miss)>1e-9);e=e[ok];miss=miss[ok];w=(e*miss>0).astype(int);rates=[]
    for t in TH:
        s=np.abs(e)>=t;n=int(s.sum());ww=int(w[s].sum());rates.append({'threshold':t,'n':n,'wins':ww,'losses':n-ww,'win_rate':ww/n if n else None})
    v=[r for r in rates if r['n']>=25];steps=sum(b['win_rate']>=a['win_rate'] for a,b in zip(v,v[1:]));viol=sum(max(0.,a['win_rate']-b['win_rate']) for a,b in zip(v,v[1:]));slope=v[-1]['win_rate']-v[0]['win_rate'] if len(v)>1 else -1.;high=next((r['win_rate'] for r in rates if r['threshold']==10 and r['n']>=25),None)
    return {'n':len(e),'steps':steps,'possible':max(0,len(v)-1),'violation':viol,'slope':slope,'high':high,'auc':auc(np.abs(e),w),'corr':float(np.corrcoef(e,miss)[0,1]) if len(e)>2 else None,'mae':float(np.mean(np.abs(p-df.actual.to_numpy(float)))),'thresholds':rates}
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
    frames={s:build_season(s) for s in (*TRAIN,VALID,HOLD,FINAL)};allf=pd.concat(frames.values(),ignore_index=True)
    tr=allf[allf.season.isin(TRAIN)].copy();va=allf[allf.season==VALID].copy();ho=allf[allf.season==HOLD].copy();fi=allf[allf.season==FINAL].copy()
    baseline={2024:metrics(va,va.baseline),2025:metrics(ho,ho.baseline),2026:metrics(fi,fi.baseline)}
    cand=[]
    for fam,features in FAMILIES.items():
        usable=[f for f in features if f in allf.columns and allf[f].notna().any()]
        if not usable:continue
        for alpha in ALPHAS:
            m=fit_ridge(tr,usable,alpha)
            for cap in CAPS:
                met=metrics(va,predict(va,m,cap));cand.append({'family':fam,'features':usable,'alpha':alpha,'cap':cap,'validation':met})
    if not cand:raise RuntimeError('No weekly rating features matched any historical games')
    cand.sort(key=lambda x:key(x['validation']),reverse=True);chosen=cand[0]
    m25=fit_ridge(pd.concat([tr,va]),chosen['features'],chosen['alpha']);p25=predict(ho,m25,chosen['cap']);r25=metrics(ho,p25)
    m26=fit_ridge(pd.concat([tr,va,ho]),chosen['features'],chosen['alpha']);p26=predict(fi,m26,chosen['cap']);r26=metrics(fi,p26)
    out={'protocol':{'market_predictor':False,'fpi_snapshot_rule':'latest valid snapshot with snapshot week < game week; joined directly by ESPN team id','train':list(TRAIN),'select':VALID,'holdout':HOLD,'final':FINAL},'baseline':baseline,'chosen_on_2024':chosen,'holdout_2025':r25,'holdout_2026':r26,'candidates':cand}
    (OUT/'cfb_weekly_fpi_edge_search.json').write_text(json.dumps(clean(out),indent=2,allow_nan=False))
    lines=['# CFB Weekly Opponent-Adjusted FPI Edge Search','',f"Chosen on 2024: {chosen['family']} alpha={chosen['alpha']} cap={chosen['cap']}",'','| Season | Model | Steps | slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |','|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for yr,b,c in [(2024,baseline[2024],chosen['validation']),(2025,baseline[2025],r25),(2026,baseline[2026],r26)]:
        for label,m in [('Baseline',b),('FPI layer',c)]:lines.append(f"| {yr} | {label} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} | {m['mae']:.3f} | "+' | '.join(rt(m,t) for t in TH)+' |')
    lines+=['','## Top 2024 candidates','','| Family | Alpha | Cap | Steps | slope | 10+ | AUC | MAE |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in cand[:15]:
        mm=r['validation'];lines.append(f"| {r['family']} | {r['alpha']:.0f} | {r['cap']:.0f} | {mm['steps']}/{mm['possible']} | {100*mm['slope']:+.1f}pp | {100*(mm['high'] or 0):.1f}% | {mm['auc']:.3f} | {mm['mae']:.3f} |")
    (OUT/'CFB_WEEKLY_FPI_EDGE_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
