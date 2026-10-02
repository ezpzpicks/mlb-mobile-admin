"""Test prior/current-season maturity blending for monotonic ATS edge quality.

Production uses weight = games/(games+4). This research varies only that football
parameter. No sportsbook line enters prediction; 2024 closing spreads select k by
fixed 2/4/6/8/10 progression and 2025 is untouched confirmation.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from builders import cfb_game_regression as gr
from research import cfb_totals_efficiency_regression as base
from research.cfb_historical_markets import attach_market_spread
OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
KS=(1.,2.,3.,4.,5.,6.,8.,10.,12.,16.)
TH=(2.,4.,6.,8.,10.)

def fbs_ids(season):
 g=base.cfb._parse_games(base.cfb._espn_games_payload(int(season)),int(season))
 if g is None or g.empty:return set()
 m=g['Away Classification'].astype(str).str.lower().eq('fbs')&g['Home Classification'].astype(str).str.lower().eq('fbs');return set(g.loc[m,'Game ID'].astype(str))
def blend(prior,current,k):
 w=current.games/(current.games+k) if current.games else 0.;return base.TeamStats(power=(1-w)*prior.power+w*current.power,ppg=(1-w)*prior.ppg+w*current.ppg,papg=(1-w)*prior.papg+w*current.papg,games=current.games)
def score(ownp,oppp,ownc,oppc,home,k):
 ob=blend(ownp,ownc,k);xb=blend(oppp,oppc,k);prior_gap=ownp.power-oppp.power
 f={'prior_own_ppg':ownp.ppg,'prior_opp_papg':oppp.papg,'prior_power_gap':prior_gap,'current_own_scoring_delta':ob.ppg-ownp.ppg,'current_opp_allowed_delta':xb.papg-oppp.papg,'current_power_delta':(ob.power-xb.power)-prior_gap,'home_indicator':home}
 return float(gr.TEAM_SCORE_INTERCEPT+sum(gr.TEAM_SCORE_COEFFICIENTS[n]*float(f[n]) for n in gr.TEAM_SCORE_COEFFICIENTS))
def season_rows(season):
 games=attach_market_spread(base.games_from_pbp(season),base.cfb);prior=base.team_summary(base.games_from_pbp(season-1));ids=fbs_ids(season);rows=[]
 for wk in sorted(pd.to_numeric(games.week,errors='coerce').dropna().astype(int).unique()):
  cur=base.team_summary(games[games.week<int(wk)])
  for _,g in games[games.week==int(wk)].iterrows():
   if ids and str(g.game_id) not in ids:continue
   hs=float(g.market_home_spread) if pd.notna(g.market_home_spread) else np.nan
   if not np.isfinite(hs):continue
   a,h=str(g.away_team),str(g.home_team);ap=base.stat(prior,a);hp=base.stat(prior,h);ac=base.stat(cur,a);hc=base.stat(cur,h);neutral=base.truthy(g.neutral);row={'actual':float(g.actual_margin),'market_home_spread':hs}
   for k in KS:row[f'k_{k:g}']=score(hp,ap,hc,ac,0. if neutral else 1.,k)-score(ap,hp,ac,hc,0.,k)
   rows.append(row)
 return pd.DataFrame(rows)
def auc(s,l):
 l=np.asarray(l,int);s=np.asarray(s,float);n1=int((l==1).sum());n0=int((l==0).sum());
 if not n1 or not n0:return None
 r=pd.Series(s).rank(method='average').to_numpy();return float((r[l==1].sum()-n1*(n1+1)/2)/(n1*n0))
def met(df,p):
 p=np.asarray(p,float);e=p+df.market_home_spread.to_numpy(float);miss=df.actual.to_numpy(float)+df.market_home_spread.to_numpy(float);ok=(np.abs(e)>1e-9)&(np.abs(miss)>1e-9);e=e[ok];miss=miss[ok];w=(e*miss>0).astype(int);rates=[]
 for t in TH:
  s=np.abs(e)>=t;n=int(s.sum());ww=int(w[s].sum());rates.append({'threshold':t,'n':n,'wins':ww,'losses':n-ww,'win_rate':ww/n if n else None})
 valid=[r for r in rates if r['n']>=25];steps=sum(b['win_rate']>=a['win_rate'] for a,b in zip(valid,valid[1:]));viol=sum(max(0.,a['win_rate']-b['win_rate']) for a,b in zip(valid,valid[1:]));slope=valid[-1]['win_rate']-valid[0]['win_rate'] if len(valid)>1 else -1.;high=next((r['win_rate'] for r in rates if r['threshold']==10. and r['n']>=25),None)
 return {'auc':auc(np.abs(e),w),'corr':float(np.corrcoef(e,miss)[0,1]),'mae':float(np.mean(np.abs(p-df.actual.to_numpy(float)))),'thresholds':rates,'monotonic_steps':int(steps),'monotonic_possible':max(0,len(valid)-1),'violation_sum':float(viol),'slope_2_to_10':float(slope),'high_edge_win_rate':high}
def key(m):return (m['monotonic_steps'],-m['violation_sum'],m['slope_2_to_10'],m['high_edge_win_rate'] or -1.,m['auc'] or -1.)
def main():
 va=season_rows(2024);ho=season_rows(2025);rows=[]
 for k in KS:rows.append({'k':k,'validation':met(va,va[f'k_{k:g}'])})
 rows.sort(key=lambda r:key(r['validation']),reverse=True)
 for r in rows:r['holdout']=met(ho,ho[f"k_{r['k']:g}"])
 basev=next(r['validation'] for r in rows if r['k']==4.);baseh=next(r['holdout'] for r in rows if r['k']==4.);chosen=rows[0]
 out={'baseline_k4':{'validation':basev,'holdout':baseh},'chosen_on_2024':chosen,'candidates':rows};(OUT/'cfb_margin_maturity_edge_search.json').write_text(json.dumps(out,indent=2,allow_nan=False))
 lines=['# CFB Season-Maturity Edge Search','',f"Production k=4 — 2024 {basev['monotonic_steps']}/{basev['monotonic_possible']} steps, slope {100*basev['slope_2_to_10']:+.1f}pp; 2025 {baseh['monotonic_steps']}/{baseh['monotonic_possible']} steps, slope {100*baseh['slope_2_to_10']:+.1f}pp",'','| k | 2024 steps | slope | 10+ | 2025 steps | slope | 10+ | AUC | MAE |','|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
 for r in rows:
  v,h=r['validation'],r['holdout'];lines.append(f"| {r['k']:g} | {v['monotonic_steps']}/{v['monotonic_possible']} | {100*v['slope_2_to_10']:+.1f}pp | {100*v['high_edge_win_rate']:.1f}% | {h['monotonic_steps']}/{h['monotonic_possible']} | {100*h['slope_2_to_10']:+.1f}pp | {100*h['high_edge_win_rate']:.1f}% | {h['auc']:.3f} | {h['mae']:.2f} |")
 lines+=['','## 2024-selected k',f"- k={chosen['k']:g}",'','| Edge | k=4 baseline 2025 | Candidate 2025 |','|---:|---:|---:|']
 for b,c in zip(baseh['thresholds'],chosen['holdout']['thresholds']):lines.append(f"| {b['threshold']:.0f}+ | {b['wins']}-{b['losses']} ({100*b['win_rate']:.1f}%) | {c['wins']}-{c['losses']} ({100*c['win_rate']:.1f}%) |")
 (OUT/'CFB_MARGIN_MATURITY_EDGE_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
