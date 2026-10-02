"""Evaluate internal power transforms by ATS edge ordering, not MAE."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_margin_power_transform_audit as pwr
base=pwr.base
TH=(2.,4.,6.,8.,10.); OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)

def auc(scores,labels):
    labels=np.asarray(labels,int);scores=np.asarray(scores,float);n1=int((labels==1).sum());n0=int((labels==0).sum())
    if not n1 or not n0:return None
    r=pd.Series(scores).rank(method='average').to_numpy();return float((r[labels==1].sum()-n1*(n1+1)/2)/(n1*n0))
def met(df):
    e=df.pred+df.market_home_spread;miss=df.actual+df.market_home_spread
    ok=np.isfinite(e)&np.isfinite(miss)&(np.abs(e)>1e-9)&(np.abs(miss)>1e-9);e=e[ok].to_numpy();miss=miss[ok].to_numpy();w=(e*miss>0).astype(int)
    rates=[]
    for t in TH:
        s=np.abs(e)>=t;n=int(s.sum());ww=int(w[s].sum());rates.append({'threshold':t,'n':n,'wins':ww,'losses':n-ww,'win_rate':ww/n if n else None})
    vr=[r for r in rates if r['n']>=25];mono=sum(b['win_rate']>=a['win_rate'] for a,b in zip(vr,vr[1:]));corr=float(np.corrcoef(e,miss)[0,1]) if len(e)>2 else None
    return {'auc':auc(np.abs(e),w),'corr':corr,'thresholds':rates,'monotonic_steps':int(mono),'monotonic_possible':max(0,len(vr)-1)}
def key(m):return (m['auc'] or -1,m['monotonic_steps'],m['corr'] or -1)
def fbs_ids(season):
    g=base.cfb._parse_games(base.cfb._espn_games_payload(season),season);m=g['Away Classification'].astype(str).str.lower().eq('fbs')&g['Home Classification'].astype(str).str.lower().eq('fbs');return set(g.loc[m,'Game ID'].astype(str))
def season(games,prior,mode,weight,ids):
    ps=pwr.summary(prior,mode);rows=[]
    for wk in sorted(pd.to_numeric(games.week,errors='coerce').dropna().astype(int).unique()):
        cur=pwr.summary(games[games.week<int(wk)],mode)
        for _,g in games[games.week==int(wk)].iterrows():
            if str(g.game_id) not in ids:continue
            a,h=pwr.scores(str(g.away_team),str(g.home_team),base.truthy(g.neutral),ps,cur,weight)
            rows.append({'actual':float(g.actual_margin),'pred':h-a,'market_home_spread':float(g.market_home_spread)})
    return pd.DataFrame(rows).dropna()
def main():
    cache={s:base.games_from_pbp(s) for s in (2023,2024,2025)};ids={s:fbs_ids(s) for s in (2024,2025)};rows=[]
    for mode in pwr.MODES:
        for weight in pwr.WEIGHTS:
            v=season(cache[2024],cache[2023],mode,weight,ids[2024]);m=met(v);rows.append({'mode':mode,'weight':weight,'validation':m})
    rows.sort(key=lambda r:key(r['validation']),reverse=True);chosen=rows[0]
    # Holdout results for validation-selected weight within each transform and overall winner.
    fam=[]
    for mode in pwr.MODES:
        best=max((r for r in rows if r['mode']==mode),key=lambda r:key(r['validation']));h=met(season(cache[2025],cache[2024],mode,best['weight'],ids[2025]));fam.append({**best,'holdout':h})
    control_v=next(r['validation'] for r in rows if r['mode']=='cap45' and r['weight']==1.0);control_h=met(season(cache[2025],cache[2024],'cap45',1.0,ids[2025]));picked=next(r for r in fam if r['mode']==chosen['mode'])
    out={'baseline':{'validation':control_v,'holdout':control_h},'chosen_on_2024':picked,'transforms':fam};(OUT/'cfb_power_edge_quality_search.json').write_text(json.dumps(out,indent=2,allow_nan=False))
    lines=['# CFB Power Transform Edge-Quality Search','',f"Baseline 2024 AUC {control_v['auc']:.3f}, corr {control_v['corr']:.3f}",f"Baseline 2025 AUC {control_h['auc']:.3f}, corr {control_h['corr']:.3f}",'','| Transform | Weight | 2024 AUC | 2025 AUC | 2025 corr |','|---|---:|---:|---:|---:|']
    for r in sorted(fam,key=lambda x:key(x['validation']),reverse=True):lines.append(f"| {r['mode']} | {r['weight']:.2f} | {r['validation']['auc']:.3f} | {r['holdout']['auc']:.3f} | {r['holdout']['corr']:.3f} |")
    lines+=['','## Selected fixed-threshold 2025 record','', '| Edge | Baseline | Candidate |','|---:|---:|---:|']
    for b,c in zip(control_h['thresholds'],picked['holdout']['thresholds']):lines.append(f"| {b['threshold']:.0f}+ | {b['wins']}-{b['losses']} ({100*b['win_rate']:.1f}%) | {c['wins']}-{c['losses']} ({100*c['win_rate']:.1f}%) |")
    (OUT/'CFB_POWER_EDGE_QUALITY_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
