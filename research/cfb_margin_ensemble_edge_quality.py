"""Test football-only margin ensembles for stronger ATS edge ordering.

Alternative margin regressions are trained on actual margins only. 2024 selects
subset/ridge/blend weight by edge AUC; 2025 is untouched confirmation. No market
line is ever a regression predictor or ensemble input.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_totals_efficiency_regression as base
from research.cfb_historical_markets import attach_market_spread
OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TRAIN=(2021,2022,2023);VALID=2024;HOLD=2025;TH=(2.,4.,6.,8.,10.);ALPHAS=(1.,16.,64.);WEIGHTS=(.1,.2,.3,.4,.5,.6,.7)
FEATURES={
 'prior_core':['prior_ppg_diff','prior_papg_diff','prior_power_margin','home_indicator'],
 'power_core':['prior_power_margin','current_power_margin','home_indicator'],
 'scoring_core':['prior_ppg_diff','prior_papg_diff','current_scoring_diff','current_allowed_diff','home_indicator'],
 'current_core':['current_scoring_diff','current_allowed_diff','current_power_margin','home_indicator'],
 'no_power':['prior_ppg_diff','prior_papg_diff','current_scoring_diff','current_allowed_diff','home_indicator'],
 'full':['prior_ppg_diff','prior_papg_diff','prior_power_margin','current_scoring_diff','current_allowed_diff','current_power_margin','home_indicator'],
}
def fbs_ids(season):
 g=base.cfb._parse_games(base.cfb._espn_games_payload(int(season)),int(season));
 if g is None or g.empty:return set()
 m=g['Away Classification'].astype(str).str.lower().eq('fbs')&g['Home Classification'].astype(str).str.lower().eq('fbs');return set(g.loc[m,'Game ID'].astype(str))
def season_rows(season):
 games=attach_market_spread(base.games_from_pbp(season),base.cfb);prior=base.team_summary(base.games_from_pbp(season-1));ids=fbs_ids(season);rows=[]
 for wk in sorted(pd.to_numeric(games.week,errors='coerce').dropna().astype(int).unique()):
  cur=base.team_summary(games[games.week<int(wk)])
  for _,g in games[games.week==int(wk)].iterrows():
   if ids and str(g.game_id) not in ids:continue
   hs=float(g.market_home_spread) if pd.notna(g.market_home_spread) else np.nan
   if not np.isfinite(hs):continue
   a,h=str(g.away_team),str(g.home_team);pa,ph=base.stat(prior,a),base.stat(prior,h);ca,ch=base.stat(cur,a),base.stat(cur,h);ba,bh=base.blend_stats(pa,ca),base.blend_stats(ph,ch);home_ind=0. if base.truthy(g.neutral) else 1.;ascore,hscore=base.spread_scores(a,h,base.truthy(g.neutral),prior,cur)
   rows.append({'season':season,'actual':float(g.actual_margin),'market_home_spread':hs,'baseline':float(hscore-ascore),'prior_ppg_diff':ph.ppg-pa.ppg,'prior_papg_diff':pa.papg-ph.papg,'prior_power_margin':2.*(ph.power-pa.power),'current_scoring_diff':(bh.ppg-ph.ppg)-(ba.ppg-pa.ppg),'current_allowed_diff':(ba.papg-pa.papg)-(bh.papg-ph.papg),'current_power_margin':2.*((bh.power-ba.power)-(ph.power-pa.power)),'home_indicator':home_ind})
 return pd.DataFrame(rows)
def fit(train,fs,alpha):
 x=train[fs].astype(float);mu=x.mean();sd=x.std(ddof=0).replace(0.,1.);z=((x-mu)/sd).to_numpy();X=np.column_stack([np.ones(len(z)),z]);y=train.actual.to_numpy(float);P=np.eye(X.shape[1])*alpha;P[0,0]=0.;b=np.linalg.solve(X.T@X+P,X.T@y);return {'fs':fs,'mu':mu,'sd':sd,'b':b}
def pred(df,m):
 z=((df[m['fs']]-m['mu'])/m['sd']).to_numpy();return m['b'][0]+z@m['b'][1:]
def auc(s,l):
 l=np.asarray(l,int);s=np.asarray(s,float);n1=int((l==1).sum());n0=int((l==0).sum());
 if not n1 or not n0:return None
 r=pd.Series(s).rank(method='average').to_numpy();return float((r[l==1].sum()-n1*(n1+1)/2)/(n1*n0))
def met(df,p):
 p=np.asarray(p,float);e=p+df.market_home_spread.to_numpy(float);miss=df.actual.to_numpy(float)+df.market_home_spread.to_numpy(float);ok=(np.abs(e)>1e-9)&(np.abs(miss)>1e-9);e=e[ok];miss=miss[ok];w=(e*miss>0).astype(int);rates=[]
 for t in TH:
  s=np.abs(e)>=t;n=int(s.sum());ww=int(w[s].sum());rates.append({'threshold':t,'n':n,'wins':ww,'losses':n-ww,'win_rate':ww/n if n else None})
 vr=[r for r in rates if r['n']>=25];mono=sum(b['win_rate']>=a['win_rate'] for a,b in zip(vr,vr[1:]));return {'auc':auc(np.abs(e),w),'corr':float(np.corrcoef(e,miss)[0,1]),'thresholds':rates,'monotonic_steps':int(mono),'monotonic_possible':max(0,len(vr)-1),'mae':float(np.mean(np.abs(p-df.actual.to_numpy(float))))}
def key(m):return (m['auc'] or -1,m['monotonic_steps'],m['corr'] or -1)
def main():
 frames=[season_rows(s) for s in (*TRAIN,VALID,HOLD)];df=pd.concat(frames,ignore_index=True);tr=df[df.season.isin(TRAIN)];va=df[df.season==VALID];ho=df[df.season==HOLD];bv=met(va,va.baseline);bh=met(ho,ho.baseline);cands=[]
 for name,fs in FEATURES.items():
  for alpha in ALPHAS:
   m=fit(tr,fs,alpha);ap=pred(va,m)
   for w in WEIGHTS:
    pp=(1-w)*va.baseline.to_numpy(float)+w*ap;mm=met(va,pp);cands.append({'family':name,'alpha':alpha,'weight':w,'validation':mm})
 cands.sort(key=lambda r:key(r['validation']),reverse=True);tv=pd.concat([tr,va],ignore_index=True);results=[]
 for r in cands:
  m=fit(tv,FEATURES[r['family']],r['alpha']);ap=pred(ho,m);hp=(1-r['weight'])*ho.baseline.to_numpy(float)+r['weight']*ap;hm=met(ho,hp);results.append({**r,'holdout':hm,'validation_auc_gain':(r['validation']['auc'] or 0)-(bv['auc'] or 0),'holdout_auc_gain':(hm['auc'] or 0)-(bh['auc'] or 0)})
 chosen=results[0];out={'baseline':{'validation':bv,'holdout':bh},'chosen_on_2024':chosen,'candidates':results};(OUT/'cfb_margin_ensemble_edge_quality.json').write_text(json.dumps(out,indent=2,allow_nan=False))
 lines=['# CFB Football-Only Margin Ensemble Edge Search','',f"Baseline 2024 AUC {bv['auc']:.3f}, corr {bv['corr']:.3f}",f"Baseline 2025 AUC {bh['auc']:.3f}, corr {bh['corr']:.3f}",'','| Family | Alpha | Blend | 2024 AUC | Δ | 2025 AUC | Δ | 2025 corr |','|---|---:|---:|---:|---:|---:|---:|---:|']
 for r in results[:20]:lines.append(f"| {r['family']} | {r['alpha']:.0f} | {r['weight']:.1f} | {r['validation']['auc']:.3f} | {r['validation_auc_gain']:+.3f} | {r['holdout']['auc']:.3f} | {r['holdout_auc_gain']:+.3f} | {r['holdout']['corr']:.3f} |")
 lines+=['','## 2024-selected candidate',f"- {chosen['family']} alpha={chosen['alpha']} blend={chosen['weight']}",'','| Edge | Baseline 2025 | Candidate 2025 |','|---:|---:|---:|']
 for b,c in zip(bh['thresholds'],chosen['holdout']['thresholds']):lines.append(f"| {b['threshold']:.0f}+ | {b['wins']}-{b['losses']} ({100*b['win_rate']:.1f}%) | {c['wins']}-{c['losses']} ({100*c['win_rate']:.1f}%) |")
 (OUT/'CFB_MARGIN_ENSEMBLE_EDGE_QUALITY.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
