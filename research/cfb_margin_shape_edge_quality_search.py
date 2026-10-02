"""Test football-only transformations of the independent CFB margin projection.

No sportsbook number enters a transform. 2024 closing spreads are used only to
select which predeclared transform best orders ATS edge; 2025 is untouched.
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_totals_efficiency_regression as base
from research.cfb_historical_markets import attach_market_spread
OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)
TH=(2.,4.,6.,8.,10.)

def fbs_ids(season):
    g=base.cfb._parse_games(base.cfb._espn_games_payload(int(season)),int(season))
    if g is None or g.empty:return set()
    m=g['Away Classification'].astype(str).str.lower().eq('fbs')&g['Home Classification'].astype(str).str.lower().eq('fbs')
    return set(g.loc[m,'Game ID'].astype(str))

def season_rows(season:int):
    games=attach_market_spread(base.games_from_pbp(season),base.cfb);prior=base.team_summary(base.games_from_pbp(season-1));ids=fbs_ids(season);rows=[]
    for wk in sorted(pd.to_numeric(games.week,errors='coerce').dropna().astype(int).unique()):
        cur=base.team_summary(games[games.week<int(wk)])
        for _,g in games[games.week==int(wk)].iterrows():
            if ids and str(g.game_id) not in ids:continue
            hs=float(g.market_home_spread) if pd.notna(g.market_home_spread) else np.nan
            if not np.isfinite(hs):continue
            a,h=str(g.away_team),str(g.home_team);ascore,hscore=base.spread_scores(a,h,base.truthy(g.neutral),prior,cur)
            rows.append({'game_id':str(g.game_id),'actual':float(g.actual_margin),'pm':float(hscore-ascore),'market_home_spread':hs})
    return pd.DataFrame(rows)

def auc(scores,labels):
    scores=np.asarray(scores,float);labels=np.asarray(labels,int);n1=int((labels==1).sum());n0=int((labels==0).sum())
    if not n1 or not n0:return None
    ranks=pd.Series(scores).rank(method='average').to_numpy();return float((ranks[labels==1].sum()-n1*(n1+1)/2)/(n1*n0))
def met(df,p):
    p=np.asarray(p,float);e=p+df.market_home_spread.to_numpy(float);miss=df.actual.to_numpy(float)+df.market_home_spread.to_numpy(float);ok=(np.abs(e)>1e-9)&(np.abs(miss)>1e-9);e=e[ok];miss=miss[ok];w=(e*miss>0).astype(int);rates=[]
    for t in TH:
        s=np.abs(e)>=t;n=int(s.sum());ww=int(w[s].sum());rates.append({'threshold':t,'n':n,'wins':ww,'losses':n-ww,'win_rate':ww/n if n else None})
    vr=[r for r in rates if r['n']>=25];mono=sum(b['win_rate']>=a['win_rate'] for a,b in zip(vr,vr[1:]));return {'auc':auc(np.abs(e),w),'corr':float(np.corrcoef(e,miss)[0,1]),'thresholds':rates,'monotonic_steps':int(mono),'monotonic_possible':max(0,len(vr)-1),'mae':float(np.mean(np.abs(p-df.actual.to_numpy(float))))}
def key(m):return (m['auc'] or -1,m['monotonic_steps'],m['corr'] or -1)
def transform(pm,spec):
    p=np.asarray(pm,float);typ=spec['type']
    if typ=='scale':return p*spec['scale']+spec.get('intercept',0.)
    if typ=='power':
        anchor=10.;return np.sign(p)*anchor*np.power(np.abs(p)/anchor,spec['exponent'])
    if typ=='cap':return np.clip(p,-spec['cap'],spec['cap'])
    if typ=='tail_scale':
        cut=spec['cut'];s=spec['scale'];return np.where(np.abs(p)<=cut,p,np.sign(p)*(cut+(np.abs(p)-cut)*s))
    raise ValueError(typ)
def main():
    va=season_rows(2024);ho=season_rows(2025);bv=met(va,va.pm);bh=met(ho,ho.pm);specs=[{'type':'scale','scale':s,'intercept':b} for s in (.75,.85,.9,.95,1.,1.05,1.1,1.2,1.3) for b in (-2.,-1.,0.,1.,2.)]
    specs += [{'type':'power','exponent':p} for p in (.75,.85,.9,.95,1.05,1.1,1.2)]
    specs += [{'type':'cap','cap':c} for c in (14.,17.,20.,24.,28.,35.)]
    specs += [{'type':'tail_scale','cut':cut,'scale':s} for cut in (7.,10.,14.,17.,21.) for s in (.4,.6,.8,1.,1.2)]
    rows=[]
    for spec in specs:
        m=met(va,transform(va.pm,spec));rows.append({'spec':spec,'validation':m})
    rows.sort(key=lambda r:key(r['validation']),reverse=True);chosen=rows[0]
    for r in rows:r['holdout']=met(ho,transform(ho.pm,r['spec']));r['validation_auc_gain']=(r['validation']['auc'] or 0)-(bv['auc'] or 0);r['holdout_auc_gain']=(r['holdout']['auc'] or 0)-(bh['auc'] or 0)
    chosen=rows[0];out={'baseline':{'validation':bv,'holdout':bh},'chosen_on_2024':chosen,'candidates':rows};(OUT/'cfb_margin_shape_edge_quality_search.json').write_text(json.dumps(out,indent=2,allow_nan=False))
    lines=['# CFB Independent Margin-Shape Edge Search','',f"Games: 2024 {len(va)}, 2025 {len(ho)}",f"Baseline 2024 AUC {bv['auc']:.3f}, corr {bv['corr']:.3f}",f"Baseline 2025 AUC {bh['auc']:.3f}, corr {bh['corr']:.3f}",'','| Transform | 2024 AUC | Δ | 2025 AUC | Δ | 2025 corr | 2025 MAE |','|---|---:|---:|---:|---:|---:|---:|']
    for r in rows[:20]:lines.append(f"| `{r['spec']}` | {r['validation']['auc']:.3f} | {r['validation_auc_gain']:+.3f} | {r['holdout']['auc']:.3f} | {r['holdout_auc_gain']:+.3f} | {r['holdout']['corr']:.3f} | {r['holdout']['mae']:.3f} |")
    lines+=['','## 2024-selected candidate',f"- `{chosen['spec']}`",'','| Edge | Baseline 2025 | Candidate 2025 |','|---:|---:|---:|']
    for b,c in zip(bh['thresholds'],chosen['holdout']['thresholds']):lines.append(f"| {b['threshold']:.0f}+ | {b['wins']}-{b['losses']} ({100*b['win_rate']:.1f}%) | {c['wins']}-{c['losses']} ({100*c['win_rate']:.1f}%) |")
    (OUT/'CFB_MARGIN_SHAPE_EDGE_QUALITY_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
