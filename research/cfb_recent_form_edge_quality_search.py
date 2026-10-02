"""Test recent-form football variables for ATS edge ordering, not MAE.

Football-only residual coefficients are fit on 2021-23 actual margins. 2024
selects family/ridge/cap by edge AUC at fixed edge checkpoints; 2025 confirms.
Sportsbook spread is evaluation-only and is never a regression feature.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_margin_recent_form_residual as rf
base=rf.base
TRAIN=rf.TRAIN; VALID=rf.VALID; HOLDOUT=rf.HOLDOUT
ALPHAS=rf.ALPHAS; CAPS=rf.CAPS; TH=(2.,4.,6.,8.,10.)
OUT=Path('research/results');OUT.mkdir(parents=True,exist_ok=True)

def fbs_ids(season):
    g=base.cfb._parse_games(base.cfb._espn_games_payload(int(season)),int(season))
    if g is None or g.empty:return set()
    m=g['Away Classification'].astype(str).str.lower().eq('fbs') & g['Home Classification'].astype(str).str.lower().eq('fbs')
    return set(g.loc[m,'Game ID'].astype(str))

def auc(scores,labels):
    labels=np.asarray(labels,int); scores=np.asarray(scores,float); n1=int((labels==1).sum());n0=int((labels==0).sum())
    if not n1 or not n0:return None
    ranks=pd.Series(scores).rank(method='average').to_numpy();return float((ranks[labels==1].sum()-n1*(n1+1)/2)/(n1*n0))
def met(df,p):
    e=np.asarray(p,float)+df.market_home_spread.to_numpy(float); miss=df.actual.to_numpy(float)+df.market_home_spread.to_numpy(float)
    ok=np.isfinite(e)&np.isfinite(miss)&(np.abs(e)>1e-9)&(np.abs(miss)>1e-9);e=e[ok];miss=miss[ok];w=(e*miss>0).astype(int)
    rates=[]
    for t in TH:
        s=np.abs(e)>=t;n=int(s.sum());ww=int(w[s].sum());rates.append({'threshold':t,'n':n,'wins':ww,'losses':n-ww,'win_rate':ww/n if n else None})
    vr=[r for r in rates if r['n']>=25];mono=sum(b['win_rate']>=a['win_rate'] for a,b in zip(vr,vr[1:]));corr=float(np.corrcoef(e,miss)[0,1]) if len(e)>2 else None
    return {'auc':auc(np.abs(e),w),'corr':corr,'thresholds':rates,'monotonic_steps':int(mono),'monotonic_possible':max(0,len(vr)-1),'mae':float(np.mean(np.abs(np.asarray(p,float)-df.actual.to_numpy(float))))}
def key(m):return (m['auc'] or -1,m['monotonic_steps'],m['corr'] or -1)

def build():
    cache={s:base.games_from_pbp(s) for s in range(min(TRAIN),HOLDOUT+1)};ids={s:fbs_ids(s) for s in (*TRAIN,VALID,HOLDOUT)};rows=[];zero={'n':0,'recent_margin_delta':0.,'recent_off_delta':0.,'recent_def_delta':0.,'volatility':0.,'home_split':0.,'away_split':0.,'recent_sos':0.,'power_resid':0.}
    for season in (*TRAIN,VALID,HOLDOUT):
        games=cache[season];prior_games=cache.get(season-1,pd.DataFrame());prior_stats=base.team_summary(prior_games);teams=set(games.home_team)|set(games.away_team);prior_prof={tm:rf.profile(prior_games,tm,prior_stats) for tm in teams}
        for week in sorted(pd.to_numeric(games.week,errors='coerce').dropna().astype(int).unique()):
            before=games[games.week<int(week)];cur=base.team_summary(before);cur_prof={tm:rf.profile(before,tm,cur) for tm in teams}
            for _,g in games[games.week==int(week)].iterrows():
                if ids.get(season) and str(g.game_id) not in ids[season]:continue
                hs=float(g.market_home_spread) if pd.notna(g.market_home_spread) else np.nan
                if not np.isfinite(hs):continue
                a,h=str(g.away_team),str(g.home_team);ap=rf.blend(prior_prof.get(a,zero),cur_prof.get(a,zero));hp=rf.blend(prior_prof.get(h,zero),cur_prof.get(h,zero));ascore,hscore=base.spread_scores(a,h,base.truthy(g.neutral),prior_stats,cur);pm=float(hscore-ascore);vol=.5*(hp['volatility']+ap['volatility']);sgn=1. if pm>=0 else -1.
                rows.append({'season':season,'game_id':str(g.game_id),'pm':pm,'actual':float(g.actual_margin),'market_home_spread':hs,'recent_margin_diff':hp['recent_margin_delta']-ap['recent_margin_delta'],'recent_off_diff':hp['recent_off_delta']-ap['recent_off_delta'],'recent_def_diff':hp['recent_def_delta']-ap['recent_def_delta'],'split_edge':hp['home_split']-ap['away_split'],'recent_sos_diff':hp['recent_sos']-ap['recent_sos'],'power_resid_diff':hp['power_resid']-ap['power_resid'],'signed_volatility':sgn*vol,'margin_x_volatility':pm*vol,'signed_margin_sq':sgn*pm*pm})
    return pd.DataFrame(rows)
def main():
    df=build();tr=df[df.season.isin(TRAIN)];va=df[df.season==VALID];ho=df[df.season==HOLDOUT];bv=met(va,va.pm);bh=met(ho,ho.pm);selected=[]
    for name,fs in rf.FAMILIES.items():
        best=None
        for a in ALPHAS:
            model=rf.fit(tr,fs,a)
            for cap in CAPS:
                m=met(va,rf.pred(va,model,cap));r={'family':name,'features':fs,'alpha':a,'cap':cap,'validation':m}
                if best is None or key(m)>key(best['validation']):best=r
        selected.append(best)
    selected.sort(key=lambda r:key(r['validation']),reverse=True);tv=pd.concat([tr,va],ignore_index=True);res=[]
    for r in selected:
        h=met(ho,rf.pred(ho,rf.fit(tv,r['features'],r['alpha']),r['cap']));res.append({**r,'holdout':h,'validation_auc_gain':(r['validation']['auc'] or 0)-(bv['auc'] or 0),'holdout_auc_gain':(h['auc'] or 0)-(bh['auc'] or 0)})
    chosen=res[0]
    out={'baseline':{'validation':bv,'holdout':bh},'chosen_on_2024':chosen,'families':res};(OUT/'cfb_recent_form_edge_quality_search.json').write_text(json.dumps(out,indent=2,allow_nan=False))
    lines=['# CFB Recent-Form Edge-Quality Search','',f"Baseline 2024 AUC {bv['auc']:.3f}, corr {bv['corr']:.3f}",f"Baseline 2025 AUC {bh['auc']:.3f}, corr {bh['corr']:.3f}",'','| Family | 2024 AUC | Δ | 2025 AUC | Δ | 2025 corr | 2025 MAE |','|---|---:|---:|---:|---:|---:|---:|']
    for r in res:lines.append(f"| {r['family']} | {r['validation']['auc']:.3f} | {r['validation_auc_gain']:+.3f} | {r['holdout']['auc']:.3f} | {r['holdout_auc_gain']:+.3f} | {r['holdout']['corr']:.3f} | {r['holdout']['mae']:.3f} |")
    lines+=['','## 2024-selected candidate',f"- {chosen['family']} alpha={chosen['alpha']} cap={chosen['cap']}",'','| Edge | Baseline 2025 | Candidate 2025 |','|---:|---:|---:|']
    for b,c in zip(bh['thresholds'],chosen['holdout']['thresholds']):lines.append(f"| {b['threshold']:.0f}+ | {b['wins']}-{b['losses']} ({100*b['win_rate']:.1f}%) | {c['wins']}-{c['losses']} ({100*c['win_rate']:.1f}%) |")
    (OUT/'CFB_RECENT_FORM_EDGE_QUALITY_SEARCH.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
if __name__=='__main__':main()
