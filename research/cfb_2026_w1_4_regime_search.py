"""2026-only early-season CFB margin/edge search.

Goal: learn the 2026 regime without using later-season or historical outcomes to
choose the correction. Sportsbook spreads are evaluation-only, never predictors.

Protocol:
- Build leakage-safe pregame football features for 2026.
- Candidate specifications are evaluated by expanding walk-forward predictions:
  train Week 1 -> predict Week 2; train Weeks 1-2 -> predict Week 3.
- Only candidates that do not worsen walk-forward MAE versus the production-core
  baseline are eligible; among those, choose fixed 2/4/6/8/10 edge ordering.
- Refit the chosen spec on Weeks 1-3 and open Week 4 once as the hard holdout.
- Report pooled out-of-sample Weeks 2-4 predictions.

No production code is changed by this research script.
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from research import cfb_combined_adjusted_edge_search as combo
from research import cfb_power_confirmation_edge_search as power
from research import cfb_returning_production_edge_search as ret
from research import cfb_turnover_luck_edge_search as tov

OUT=Path('research/results'); OUT.mkdir(parents=True,exist_ok=True)
TH=(2.,4.,6.,8.,10.)
ALPHAS=(4.,16.,64.,256.,1024.)
CAPS=(2.,4.,6.,8.,10.)

FPI_CONTROL=['gamecontrol_diff','adjavgingamewp_diff']
ADJ_NET=['adj_net_diff','fei_net_diff','net_z_diff']
ADJ_OD=['adj_off_epa_diff','adj_def_epa_diff','fei_off_diff','fei_def_diff']
COMP_CORE=['off_epa_diff','def_epa_edge','off_success_diff','def_success_edge','pass_epa_diff','rush_epa_diff','early_epa_diff','def_early_epa_edge']
COMP_EXP=['explosive_diff','def_explosive_edge']
BASIC=['prior_power_margin','current_power_margin','current_scoring_diff','current_allowed_diff']
RETURNING=['off_returning_diff','def_returning_diff','overall_returning_diff','n_returning_diff','avg_overall_returning']
TURNOVER=['turnover_margin_diff','expected_turnover_margin_diff','turnover_luck_diff','expected_turnovers_off_diff','expected_turnovers_def_diff']
INTER=['power_x_prior','power_x_scoring','power_x_allowed','power_x_gamecontrol','power_x_ingamewp','power_x_adjnet','power_x_feinet','power_x_netz','power_x_comp_epa','power_x_comp_success','power_x_comp_early','power_reversal_mag','power_scoring_conflict_mag','power_quality_conflict_mag']

FAMILIES={
 'basic':BASIC,
 'quality_compact':FPI_CONTROL+ADJ_NET+COMP_CORE,
 'quality_full':FPI_CONTROL+ADJ_NET+ADJ_OD+COMP_CORE+COMP_EXP,
 'returning':RETURNING,
 'turnover':TURNOVER,
 'basic_returning':BASIC+RETURNING,
 'basic_turnover':BASIC+TURNOVER,
 'quality_returning':FPI_CONTROL+ADJ_NET+COMP_CORE+RETURNING,
 'quality_turnover':FPI_CONTROL+ADJ_NET+COMP_CORE+TURNOVER,
 'quality_returning_turnover':FPI_CONTROL+ADJ_NET+COMP_CORE+RETURNING+TURNOVER,
 'power_confirmation':BASIC+FPI_CONTROL+ADJ_NET+COMP_CORE+INTER,
 'power_confirmation_returning':BASIC+FPI_CONTROL+ADJ_NET+COMP_CORE+INTER+RETURNING,
 'all_early':BASIC+FPI_CONTROL+ADJ_NET+ADJ_OD+COMP_CORE+COMP_EXP+INTER+RETURNING+TURNOVER,
}

def safe(s): return pd.to_numeric(s,errors='coerce')
def signconflict(a,b):
    a=safe(a); b=safe(b)
    return np.where(np.isfinite(a)&np.isfinite(b)&(a*b<0),np.abs(a),0.0)

def build_2026():
    # power.build_season gives baseline + opponent/game-state quality + core component interactions.
    df=power.build_season(2026).copy()

    # Preseason returning production by ESPN team id.
    rp=ret.load_returning(2026)
    for idx,row in df.iterrows():
        try: ai=float(row.away_team_id); hi=float(row.home_team_id)
        except Exception: continue
        if ai not in rp.index or hi not in rp.index: continue
        a=rp.loc[ai]; h=rp.loc[hi]
        for c in ret.RAW:
            av=float(a[c]) if c in a.index and pd.notna(a[c]) else np.nan
            hv=float(h[c]) if c in h.index and pd.notna(h[c]) else np.nan
            df.loc[idx,f'{c}_diff']=hv-av if np.isfinite(av) and np.isfinite(hv) else np.nan
        ao=float(a.overall_returning) if pd.notna(a.overall_returning) else np.nan
        ho=float(h.overall_returning) if pd.notna(h.overall_returning) else np.nan
        df.loc[idx,'avg_overall_returning']=(ao+ho)/2 if np.isfinite(ao) and np.isfinite(ho) else np.nan

    # Weekly turnover luck through prior week only.
    wk=tov.load_weekly(2026)
    for idx,row in df.iterrows():
        a=tov.prior(wk,row.away_team_id,row.week); h=tov.prior(wk,row.home_team_id,row.week)
        if a is None or h is None: continue
        for c in tov.RAW:
            av=float(a[c]) if c in a.index and pd.notna(a[c]) else np.nan
            hv=float(h[c]) if c in h.index and pd.notna(h[c]) else np.nan
            df.loc[idx,f'{c}_diff']=hv-av if np.isfinite(av) and np.isfinite(hv) else np.nan

    # Recompute useful interactions after the joins.
    p=safe(df.current_power_margin); prior=safe(df.prior_power_margin); sc=safe(df.current_scoring_diff); al=safe(df.current_allowed_diff)
    gc=safe(df.get('gamecontrol_diff')); iwp=safe(df.get('adjavgingamewp_diff')); an=safe(df.get('adj_net_diff')); fn=safe(df.get('fei_net_diff')); nz=safe(df.get('net_z_diff'))
    cepa=safe(df.get('off_epa_diff'))+safe(df.get('def_epa_edge')); csucc=safe(df.get('off_success_diff'))+safe(df.get('def_success_edge')); cearly=safe(df.get('early_epa_diff'))+safe(df.get('def_early_epa_edge'))
    df['power_x_prior']=p*prior; df['power_x_scoring']=p*sc; df['power_x_allowed']=p*al
    df['power_x_gamecontrol']=p*gc; df['power_x_ingamewp']=p*iwp; df['power_x_adjnet']=p*an; df['power_x_feinet']=p*fn; df['power_x_netz']=p*nz
    df['power_x_comp_epa']=p*cepa; df['power_x_comp_success']=p*csucc; df['power_x_comp_early']=p*cearly
    df['power_reversal_mag']=signconflict(p,prior); df['power_scoring_conflict_mag']=signconflict(p,sc)
    quality=pd.concat([gc.rename('gc'),an.rename('an'),fn.rename('fn'),cepa.rename('cepa')],axis=1).mean(axis=1,skipna=True)
    df['power_quality_conflict_mag']=signconflict(p,quality)
    df['week']=safe(df.week).astype('Int64')
    return df[df.week.between(1,4,inclusive='both')].copy()

def fit(train,features,alpha):
    x=train[features].apply(pd.to_numeric,errors='coerce'); mu=x.mean(); x=x.fillna(mu)
    sd=x.std(ddof=0).replace(0.,1.); z=((x-mu)/sd).fillna(0.).to_numpy(float)
    y=(safe(train.actual)-safe(train.baseline)).to_numpy(float)
    X=np.column_stack([np.ones(len(z)),z]); P=np.eye(X.shape[1])*alpha; P[0,0]=0.
    b=np.linalg.solve(X.T@X+P,X.T@y)
    return {'features':features,'mu':mu,'sd':sd,'b':b}

def pred(df,m,cap):
    x=df[m['features']].apply(pd.to_numeric,errors='coerce').fillna(m['mu'])
    z=((x-m['mu'])/m['sd']).fillna(0.).to_numpy(float)
    c=m['b'][0]+z@m['b'][1:]
    return safe(df.baseline).to_numpy(float)+np.clip(c,-cap,cap)

def auc(s,l):
    s=np.asarray(s,float); l=np.asarray(l,int); n1=int((l==1).sum()); n0=int((l==0).sum())
    if not n1 or not n0:return None
    r=pd.Series(s).rank(method='average').to_numpy(); return float((r[l==1].sum()-n1*(n1+1)/2)/(n1*n0))

def metrics(df,p,min_bucket=8):
    p=np.asarray(p,float); actual=safe(df.actual).to_numpy(float); spread=safe(df.market_home_spread).to_numpy(float)
    e=p+spread; miss=actual+spread
    ok=np.isfinite(e)&np.isfinite(miss)&np.isfinite(actual)&(np.abs(e)>1e-9)&(np.abs(miss)>1e-9)
    e=e[ok]; miss=miss[ok]; actual=actual[ok]; p=p[ok]; w=(e*miss>0).astype(int)
    rates=[]
    for t in TH:
        s=np.abs(e)>=t; n=int(s.sum()); ww=int(w[s].sum())
        rates.append({'threshold':t,'n':n,'wins':ww,'losses':n-ww,'win_rate':ww/n if n else None})
    v=[r for r in rates if r['n']>=min_bucket]
    steps=sum(b['win_rate']>=a['win_rate'] for a,b in zip(v,v[1:]))
    viol=sum(max(0.,a['win_rate']-b['win_rate']) for a,b in zip(v,v[1:]))
    slope=v[-1]['win_rate']-v[0]['win_rate'] if len(v)>1 else -1.
    high=next((r['win_rate'] for r in rates if r['threshold']==10 and r['n']>=min_bucket),None)
    return {'n':int(ok.sum()),'mae':float(np.mean(np.abs(p-actual))),'steps':steps,'possible':max(0,len(v)-1),'violation':viol,'slope':slope,'high':high,'auc':auc(np.abs(e),w),'corr':float(np.corrcoef(e,miss)[0,1]) if len(e)>2 else None,'thresholds':rates}

def rt(m,t):
    r=next(x for x in m['thresholds'] if x['threshold']==t)
    return f"{r['wins']}-{r['losses']} ({100*r['win_rate']:.1f}%)" if r['n'] else '0-0'

def rolling_eval(df,features,alpha,cap,test_weeks=(2,3)):
    parts=[]
    for tw in test_weeks:
        tr=df[df.week<int(tw)].copy(); te=df[df.week==int(tw)].copy()
        if tr.empty or te.empty: continue
        m=fit(tr,features,alpha); q=te.copy(); q['_pred']=pred(te,m,cap); parts.append(q)
    if not parts:return None,None
    oof=pd.concat(parts,ignore_index=True); return metrics(oof,oof._pred.to_numpy(float)),oof

def baseline_for(df,weeks):
    q=df[df.week.isin(list(weeks))].copy(); return metrics(q,safe(q.baseline).to_numpy(float)),q

def selkey(m,base_mae):
    # Prediction accuracy is a hard guardrail. Within candidates that meet it,
    # optimize edge ordering; AUC/correlation and MAE break ties.
    mae_ok = m['mae'] <= base_mae + 1e-9
    return (1 if mae_ok else 0,m['steps'],-m['violation'],m['slope'],m['high'] if m['high'] is not None else -1.,m['auc'] if m['auc'] is not None else -1.,m['corr'] if m['corr'] is not None else -9.,-m['mae'])

def clean(v):
    if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
    if isinstance(v,list):return [clean(x) for x in v]
    if isinstance(v,(np.integer,)):return int(v)
    if isinstance(v,(np.floating,float)):return float(v) if math.isfinite(float(v)) else None
    return v

def main():
    df=build_2026(); counts={int(w):int((df.week==w).sum()) for w in sorted(df.week.dropna().astype(int).unique())}
    base_dev,_=baseline_for(df,(2,3)); candidates=[]
    for name,features in FAMILIES.items():
        usable=[f for f in features if f in df.columns and df[f].notna().any()]
        if not usable:continue
        for alpha in ALPHAS:
            for cap in CAPS:
                m,_=rolling_eval(df,usable,alpha,cap,(2,3))
                if m is not None:candidates.append({'family':name,'features':usable,'alpha':alpha,'cap':cap,'dev':m})
    candidates.sort(key=lambda r:selkey(r['dev'],base_dev['mae']),reverse=True)
    chosen=candidates[0]

    # Hard Week 4 holdout: specification is frozen before this point.
    tr=df[df.week<4].copy(); w4=df[df.week==4].copy(); model=fit(tr,chosen['features'],chosen['alpha']); p4=pred(w4,model,chosen['cap'])
    hold=metrics(w4,p4); base4=metrics(w4,safe(w4.baseline).to_numpy(float))

    # Fair pooled W2-4 walk-forward predictions for the frozen chosen spec.
    _,dev_oof=rolling_eval(df,chosen['features'],chosen['alpha'],chosen['cap'],(2,3))
    q4=w4.copy();q4['_pred']=p4
    pooled=pd.concat([dev_oof,q4],ignore_index=True); pooled_m=metrics(pooled,pooled._pred.to_numpy(float)); base_pool=metrics(pooled,safe(pooled.baseline).to_numpy(float))

    out={'protocol':{'season':2026,'selection_weeks':[2,3],'hard_holdout_week':4,'market_predictor':False,'selection_guardrail':'candidate walk-forward MAE must be <= baseline before edge-order ranking'},'counts':counts,'baseline_dev':base_dev,'chosen':chosen,'week4_baseline':base4,'week4_challenger':hold,'pooled_w2_4_baseline':base_pool,'pooled_w2_4_challenger':pooled_m,'candidates':candidates[:50]}
    (OUT/'cfb_2026_w1_4_regime_search.json').write_text(json.dumps(clean(out),indent=2,allow_nan=False))
    lines=['# CFB 2026 Weeks 1-4 Regime Search','',f"Game counts: {counts}",'',f"Chosen from W2-3 walk-forward only: **{chosen['family']}**, alpha={chosen['alpha']:.0f}, cap={chosen['cap']:.0f}",'','| Window | Model | N | MAE | Steps | slope | AUC | 2+ | 4+ | 6+ | 8+ | 10+ |','|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for label,b,c in [('W2-3 selection',base_dev,chosen['dev']),('W4 hard holdout',base4,hold),('W2-4 pooled OOS',base_pool,pooled_m)]:
        for model_name,m in [('Baseline',b),('Challenger',c)]:
            lines.append(f"| {label} | {model_name} | {m['n']} | {m['mae']:.3f} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} | "+' | '.join(rt(m,t) for t in TH)+' |')
    lines+=['','## Top current-year candidates','','| Family | Alpha | Cap | MAE | Steps | slope | 10+ | AUC |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in candidates[:20]:
        m=r['dev']; lines.append(f"| {r['family']} | {r['alpha']:.0f} | {r['cap']:.0f} | {m['mae']:.3f} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {100*(m['high'] or 0):.1f}% | {m['auc']:.3f} |")
    (OUT/'CFB_2026_W1_4_REGIME_SEARCH.md').write_text('\n'.join(lines)+'\n'); print('\n'.join(lines))

if __name__=='__main__': main()
