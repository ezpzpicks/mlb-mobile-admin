"""Test robust fitting of the current CFB margin feature set for ATS edge ordering.
Market spread is evaluation-only; all coefficients fit to actual football margins.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_margin_robust_loss_audit as rb
base=rb.base
TRAIN=rb.TRAIN;VALID=rb.VALID;HOLDOUT=rb.HOLDOUT;TH=(2.,4.,6.,8.,10.);OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
CANDS=[('ols',None),('lad',None)]+[('quantile',q) for q in (.45,.475,.5,.525,.55)]+[('huber',t) for t in (.75,1.,1.345,1.75,2.5)]+[('tukey',c) for c in (3.,4.685,6.)]
def fbs_ids(season):
 g=base.cfb._parse_games(base.cfb._espn_games_payload(int(season)),int(season));
 if g is None or g.empty:return set()
 m=g['Away Classification'].astype(str).str.lower().eq('fbs')&g['Home Classification'].astype(str).str.lower().eq('fbs');return set(g.loc[m,'Game ID'].astype(str))
def rows():
 cache={s:base.games_from_pbp(s) for s in range(2020,2026)};ids={s:fbs_ids(s) for s in (*TRAIN,VALID,HOLDOUT)};out=[]
 for season in (*TRAIN,VALID,HOLDOUT):
  games=cache[season];prior=base.team_summary(cache.get(season-1,pd.DataFrame()))
  for wk in sorted(pd.to_numeric(games.week,errors='coerce').dropna().astype(int).unique()):
   cur=base.team_summary(games[games.week<int(wk)])
   for _,g in games[games.week==int(wk)].iterrows():
    if ids.get(season) and str(g.game_id) not in ids[season]:continue
    hs=float(g.market_home_spread) if pd.notna(g.market_home_spread) else np.nan
    if not np.isfinite(hs):continue
    a,h=str(g.away_team),str(g.home_team);pa,ph=base.stat(prior,a),base.stat(prior,h);ca,ch=base.stat(cur,a),base.stat(cur,h);ba,bh=base.blend_stats(pa,ca),base.blend_stats(ph,ch);home_ind=0. if base.truthy(g.neutral) else 1.
    out.append({'season':season,'actual':float(g.actual_margin),'market_home_spread':hs,'prior_ppg_diff':ph.ppg-pa.ppg,'prior_papg_diff':pa.papg-ph.papg,'prior_power_margin':2.*(ph.power-pa.power),'current_scoring_diff':(bh.ppg-ph.ppg)-(ba.ppg-pa.ppg),'current_allowed_diff':(ba.papg-pa.papg)-(bh.papg-ph.papg),'current_power_margin':2.*((bh.power-ba.power)-(ph.power-pa.power)),'home_indicator':home_ind})
 return pd.DataFrame(out)
def auc(s,l):
 l=np.asarray(l,int);s=np.asarray(s,float);n1=int((l==1).sum());n0=int((l==0).sum());
 if not n1 or not n0:return None
 r=pd.Series(s).rank(method='average').to_numpy();return float((r[l==1].sum()-n1*(n1+1)/2)/(n1*n0))
def met(df,p):
 e=np.asarray(p,float)+df.market_home_spread.to_numpy(float);miss=df.actual.to_numpy(float)+df.market_home_spread.to_numpy(float);ok=(np.abs(e)>1e-9)&(np.abs(miss)>1e-9);e=e[ok];miss=miss[ok];w=(e*miss>0).astype(int);rates=[]
 for t in TH:
  s=np.abs(e)>=t;n=int(s.sum());ww=int(w[s].sum());rates.append({'threshold':t,'n':n,'wins':ww,'losses':n-ww,'win_rate':ww/n if n else None})
 vr=[r for r in rates if r['n']>=25];mono=sum(b['win_rate']>=a['win_rate'] for a,b in zip(vr,vr[1:]));return {'auc':auc(np.abs(e),w),'corr':float(np.corrcoef(e,miss)[0,1]),'thresholds':rates,'monotonic_steps':int(mono),'monotonic_possible':max(0,len(vr)-1),'mae':float(np.mean(np.abs(np.asarray(p)-df.actual.to_numpy())))}
def key(m):return (m['auc'] or -1,m['monotonic_steps'],m['corr'] or -1)
def main():
 df=rows();tr=df[df.season.isin(TRAIN)];va=df[df.season==VALID];ho=df[df.season==HOLDOUT];bv=met(va,rb.baseline_pred(va));bh=met(ho,rb.baseline_pred(ho));res=[]
 for meth,tune in CANDS:
  p=rb.fit_predict(tr,va,meth,tune);m=met(va,p);res.append({'method':meth,'tuning':tune,'validation':m})
 res.sort(key=lambda r:key(r['validation']),reverse=True);chosen=res[0];tv=pd.concat([tr,va],ignore_index=True);full=[]
 for r in res:
  hm=met(ho,rb.fit_predict(tv,ho,r['method'],r['tuning']));full.append({**r,'holdout':hm,'validation_auc_gain':(r['validation']['auc'] or 0)-(bv['auc'] or 0),'holdout_auc_gain':(hm['auc'] or 0)-(bh['auc'] or 0)})
 chosen=full[0];out={'baseline':{'validation':bv,'holdout':bh},'chosen_on_2024':chosen,'methods':full};(OUT/'cfb_robust_loss_edge_quality_search.json').write_text(json.dumps(out,indent=2,allow_nan=False))
 lines=['# CFB Robust-Loss Edge-Quality Search','',f"Baseline 2024 AUC {bv['auc']:.3f}, corr {bv['corr']:.3f}",f"Baseline 2025 AUC {bh['auc']:.3f}, corr {bh['corr']:.3f}",'','| Method | Tuning | 2024 AUC | Δ | 2025 AUC | Δ | 2025 corr |','|---|---:|---:|---:|---:|---:|---:|']
 for r in full:lines.append(f"| {r['method']} | {'' if r['tuning'] is None else r['tuning']} | {r['validation']['auc']:.3f} | {r['validation_auc_gain']:+.3f} | {r['holdout']['auc']:.3f} | {r['holdout_auc_gain']:+.3f} | {r['holdout']['corr']:.3f} |")
 lines+=['','## 2024-selected candidate',f"- {chosen['method']} {chosen['tuning']}",'','| Edge | Baseline 2025 | Candidate 2025 |','|---:|---:|---:|']
 for b,c in zip(bh['thresholds'],chosen['holdout']['thresholds']):lines.append(f"| {b['threshold']:.0f}+ | {b['wins']}-{b['losses']} ({100*b['win_rate']:.1f}%) | {c['wins']}-{c['losses']} ({100*c['win_rate']:.1f}%) |")
 (OUT/'CFB_ROBUST_LOSS_EDGE_QUALITY_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
