"""Offline, chronological NCAAM offense/defense/tempo weight study.

Ratings and national benchmarks are dated before the game. Models predict
efficiency and pace separately. Selection uses 2025 only; 2026 and current
2027 outcomes never choose a family or penalty. No live database writes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

BOUNDS = (-np.inf, -1.5, -1., 0., 1., 1.5, np.inf)
LEVELS = ('very_low', 'low', 'below_average', 'above_average', 'high', 'very_high')
LEVEL_Z = (-2., -1.25, -.5, .5, 1.25, 2.)


def level_basis(z, sd):
    """Six integrated slopes, continuous at the website's exact cutoffs.

    All columns are zero at the national mean. With six weights equal to one,
    their sum is exactly the original raw rating departure from that mean.
    """
    z, sd = np.broadcast_arrays(np.asarray(z, float), np.asarray(sd, float))
    cols = []
    for lo, hi in zip(BOUNDS[:-1], BOUNDS[1:]):
        if np.isneginf(lo):
            value = np.minimum(z, hi) - min(0., hi)
        elif np.isposinf(hi):
            value = np.maximum(z-lo, 0.) - max(-lo, 0.)
        else:
            value = np.clip(z-lo, 0., hi-lo) - np.clip(-lo, 0., hi-lo)
        cols.append(sd * value)
    return np.stack(cols, axis=-1)


def prepare(payload):
    games = pd.DataFrame(payload['games']).sort_values(['date', 'game_id']).reset_index(drop=True)
    if games.game_id.duplicated().any():
        raise ValueError('Duplicate physical game')
    if not (pd.to_datetime(games.ratings_date) < pd.to_datetime(games.date)).all():
        raise ValueError('Ratings must precede the game date')
    starts = games.groupby('season').date.min().to_dict()
    games['day'] = [int((date.fromisoformat(d)-date.fromisoformat(starts[s])).days) for d,s in zip(games.date,games.season)]
    games['stage'] = np.minimum(games.day // 7, 3)
    games['actual_total'] = games.away_points + games.home_points
    games['actual_margin'] = games.home_points - games.away_points
    games['actual_pace'] = (games.away_pace + games.home_pace)/2
    rows = []
    for g in games.to_dict('records'):
        for side, opposite, venue in (('away','home',-1.),('home','away',1.)):
            row = {k:g[k] for k in ('game_id','season','date','ratings_date','day','stage','overtime','neutral')}
            row.update(team=g[side+'_team'],opponent=g[opposite+'_team'],side=side,
                       venue=0. if g['neutral'] else venue,
                       points=g[side+'_points'],pp100=g[side+'_pp100'],
                       center=(g['mean_offense']+g['mean_defense'])/2)
            for m in ('offense','defense','tempo'):
                row['mean_'+m]=g['mean_'+m];row['sd_'+m]=g['sd_'+m]
                row['own_'+m]=g[side+'_'+m];row['opp_'+m]=g[opposite+'_'+m]
                row['own_'+m+'_z']=g[side+'_'+m+'_z'];row['opp_'+m+'_z']=g[opposite+'_'+m+'_z']
            rows.append(row)
    return games,pd.DataFrame(rows)


def controls(df, venue=False):
    result = {'intercept':np.ones(len(df))}
    if venue:
        result['venue']=df.venue.to_numpy(float)
    for stage in (1,2,3):
        result['week_'+str(stage+1)]=(df.stage.to_numpy()==stage).astype(float)
    return result


def design_eff(teams,family='linear',drop=None):
    cols=controls(teams,True)
    for prefix,metric in (('offense','own_offense'),('defense','opp_defense')):
        if prefix==drop:continue
        if family=='linear':
            cols[prefix]=teams[metric].to_numpy()-teams['mean_'+prefix].to_numpy()
        else:
            b=level_basis(teams[metric+'_z'],teams['sd_'+prefix])
            for j,level in enumerate(LEVELS):cols[prefix+'__'+level]=b[:,j]
    if family=='interaction' and drop is None:
        cols['offense_x_defense']=(teams.own_offense-teams.mean_offense).to_numpy()*(teams.opp_defense-teams.mean_defense).to_numpy()/teams.center.to_numpy()
    return np.column_stack(list(cols.values())),list(cols)


def design_pace(games,family='linear',drop=None):
    cols=controls(games)
    if drop!='tempo':
        if family=='linear':
            cols['tempo']=(games.away_tempo+games.home_tempo-2*games.mean_tempo).to_numpy()
        else:
            b=level_basis(games.away_tempo_z,games.sd_tempo)+level_basis(games.home_tempo_z,games.sd_tempo)
            for j,level in enumerate(LEVELS):cols['tempo__'+level]=b[:,j]
        cols['tempo_cross']=((games.away_tempo-games.mean_tempo)*(games.home_tempo-games.mean_tempo)/games.mean_tempo).to_numpy()
    return np.column_stack(list(cols.values())),list(cols)


def dyadic_covariance(X,residual,owners):
    """Sandwich covariance for rows sharing either school, across seasons.

    Multi-membership inclusion/exclusion: school clusters minus unordered
    school-pair clusters. This includes both score rows and repeated matchups.
    """
    school,pair=defaultdict(lambda:np.zeros(X.shape[1])),defaultdict(lambda:np.zeros(X.shape[1]))
    for score,(a,b) in zip(X*residual[:,None],owners):
        school[str(a)]+=score;school[str(b)]+=score
        pair[tuple(sorted((str(a),str(b))))]+=score
    meat=sum(np.outer(v,v) for v in school.values())-sum(np.outer(v,v) for v in pair.values())
    bread=np.linalg.pinv(X.T@X)
    covariance=bread@meat@bread
    n,p=X.shape;clusters=len(school)
    covariance*=((n-1)/max(n-p,1))*(clusters/max(clusters-1,1))
    covariance=(covariance+covariance.T)/2
    eig,vec=np.linalg.eigh(covariance)
    adjusted=bool(eig.min() < -1e-10)
    covariance=(vec*np.maximum(eig,0.))@vec.T
    return covariance,{'school_clusters':clusters,'pair_clusters':len(pair),'psd_adjustment':adjusted}


@dataclass
class Fit:
    coefficients:np.ndarray
    columns:list
    covariance:np.ndarray|None=None
    covariance_info:dict|None=None

    def predict(self,X):return X@self.coefficients


def fit_regression(X,y,columns,owners,penalty=0.,nonnegative=False,inference=False):
    prior=np.zeros(len(columns));penalize=np.zeros(len(columns))
    constrained=[]
    for i,c in enumerate(columns):
        if c.startswith(('offense','defense','tempo')):
            prior[i]=0. if c=='offense_x_defense' else 1.
            penalize[i]=1.
            if c!='offense_x_defense':constrained.append(i)
    if penalty or nonnegative:
        from scipy.optimize import lsq_linear
        aug=np.vstack((X,np.diag(np.sqrt(penalty*penalize))))
        target=np.r_[y,np.sqrt(penalty*penalize)*prior]
        lower=np.full(len(columns),-np.inf);upper=np.full(len(columns),np.inf)
        if nonnegative:lower[constrained]=0.
        coef=lsq_linear(aug,target,bounds=(lower,upper),tol=1e-10).x
    else:
        coef=np.linalg.lstsq(X,y,rcond=None)[0]
    covariance,info=(dyadic_covariance(X,y-X@coef,owners) if inference else (None,None))
    return Fit(coef,columns,covariance,info)


@dataclass
class Model:
    family:str
    penalty:float
    nonnegative:bool
    eff:Fit
    pace:Fit


def fit_model(games,teams,family='linear',penalty=0.,nonnegative=False,inference=False,drop=None):
    ex,ec=design_eff(teams,family,drop)
    px,pc=design_pace(games,family,drop)
    eff=fit_regression(ex,(teams.pp100-teams.center).to_numpy(),ec,teams[['team','opponent']].to_numpy(),penalty,nonnegative,inference)
    pace=fit_regression(px,(games.actual_pace-games.mean_tempo).to_numpy(),pc,games[['away_team','home_team']].to_numpy(),penalty,nonnegative,inference)
    return Model(family,penalty,nonnegative,eff,pace)


def predict_model(model,games,teams,drop=None):
    ex,_=design_eff(teams,model.family,drop);px,_=design_pace(games,model.family,drop)
    pace=games.mean_tempo.to_numpy()+model.pace.predict(px)
    efficiencies=teams.center.to_numpy()+model.eff.predict(ex)
    lookup=dict(zip(games.game_id,pace))
    score=efficiencies*np.array([lookup[g] for g in teams.game_id])/100
    team=teams[['game_id','side','points']].copy();team['prediction']=score
    pivot=team.pivot(index='game_id',columns='side',values='prediction')
    result=games[['game_id','season','date','day','overtime','actual_total','actual_margin','actual_pace']].copy()
    result['away']=result.game_id.map(pivot.away);result['home']=result.game_id.map(pivot.home)
    result['total']=result.away+result.home;result['margin']=result.home-result.away;result['pace']=pace
    return result,team


def predict_current(games,teams):
    pace=(games.away_tempo*games.home_tempo/games.mean_tempo).to_numpy()
    lookup=dict(zip(games.game_id,pace))
    ppp=(teams.center+teams.own_offense-teams.mean_offense+teams.opp_defense-teams.mean_defense).to_numpy()
    score=ppp*np.array([lookup[g] for g in teams.game_id])/100+teams.venue.to_numpy()*1.6
    team=teams[['game_id','side','points']].copy();team['prediction']=score
    pivot=team.pivot(index='game_id',columns='side',values='prediction')
    result=games[['game_id','season','date','day','overtime','actual_total','actual_margin','actual_pace']].copy()
    result['away']=result.game_id.map(pivot.away);result['home']=result.game_id.map(pivot.home)
    result['total']=result.away+result.home;result['margin']=result.home-result.away;result['pace']=pace
    return result,team


def summarize(result,team):
    ans={'games':len(result),'team_rows':len(team)}
    for field,target in (('total','actual_total'),('margin','actual_margin'),('pace','actual_pace')):
        e=(result[field]-result[target]).to_numpy()
        ans[field]={'mae':float(np.mean(abs(e))),'rmse':float(np.sqrt(np.mean(e**2))),'bias':float(np.mean(e))}
    e=(team.prediction-team.points).to_numpy()
    ans['team']={'mae':float(np.mean(abs(e))),'rmse':float(np.sqrt(np.mean(e**2))),'bias':float(np.mean(e))}
    return ans


def subset(games,teams,seasons,non_ot=True):
    g=games[games.season.isin(seasons)&(~games.overtime if non_ot else True)].copy().reset_index(drop=True)
    t=teams[teams.game_id.isin(g.game_id)].copy().reset_index(drop=True)
    return g,t


def bootstrap_difference(a,b,draws=1500):
    """Paired circular seven-date moving blocks, preserving whole game days.

    A single November has only four or five calendar weeks. Sampling those
    few whole weeks can let a one-game partial week dominate a replicate.
    Fixed-length, overlapping blocks preserve local dependence and day pairs
    without that partial-week artifact. These remain approximate small-period
    uncertainty intervals, not evidence of betting profitability.
    """
    joined=a[['game_id','date','total','actual_total']].merge(b[['game_id','total']],on='game_id',suffixes=('_a','_b'))
    joined['difference']=abs(joined.total_a-joined.actual_total)-abs(joined.total_b-joined.actual_total)
    chunks=[v.difference.to_numpy() for _,v in joined.groupby('date',sort=True)]
    length=min(7,len(chunks))
    rng=np.random.default_rng(20261115);means=[]
    for _ in range(draws):
        picked=[]
        while len(picked)<len(chunks):
            start=int(rng.integers(len(chunks)))
            picked.extend((start+j)%len(chunks) for j in range(length))
        means.append(np.concatenate([chunks[i] for i in picked[:len(chunks)]]).mean())
    return {'mae_difference_a_minus_b':float(joined.difference.mean()),
            'ci95':list(map(float,np.quantile(means,[.025,.975]))),'date_groups':len(chunks),
            'moving_block_length':length,'draws':draws}


def wald(fit,contrasts,target=None):
    from scipy.stats import chi2
    R=np.asarray(contrasts,float);q=np.zeros(R.shape[0]) if target is None else np.asarray(target,float)
    diff=R@fit.coefficients-q;v=R@fit.covariance@R.T
    rank=int(np.linalg.matrix_rank(v));stat=float(diff@np.linalg.pinv(v)@diff)
    return {'statistic':stat,'df':rank,'p':float(chi2.sf(stat,rank)) if rank else None}


def coefficient_rows(fit,kind):
    from scipy.stats import norm
    out=[]
    for i,name in enumerate(fit.columns):
        se=float(np.sqrt(max(fit.covariance[i,i],0)))
        beta=float(fit.coefficients[i]);p=float(2*norm.sf(abs(beta/se))) if se else 0.
        out.append({'component':kind,'term':name,'estimate':beta,'se':se,'low95':beta-1.96*se,'high95':beta+1.96*se,'p_zero':p})
    return out


def level_summaries(model):
    from scipy.stats import norm
    out=[];tests={}
    for prefix,fit in (('offense',model.eff),('defense',model.eff),('tempo',model.pace)):
        ix=[fit.columns.index(prefix+'__'+level) for level in LEVELS]
        group=np.eye(len(fit.columns))[ix]
        tests[prefix+'_all_zero']=wald(fit,group)
        tests[prefix+'_equal_levels']=wald(fit,group[1:]-group[0])
        for j,index in enumerate(ix):
            beta=float(fit.coefficients[index]);se=float(np.sqrt(max(fit.covariance[index,index],0)))
            p_one=float(2*norm.sf(abs((beta-1)/se))) if se else None
            out.append({'factor':prefix,'level':LEVELS[j],'z_min':None if not np.isfinite(BOUNDS[j]) else BOUNDS[j],
                'z_max':None if not np.isfinite(BOUNDS[j+1]) else BOUNDS[j+1],
                'weight':beta,'low95':beta-1.96*se,'high95':beta+1.96*se,'p_current_weight_1':p_one})
    # Benjamini-Hochberg correction for the 18 comparisons to the current unit weight.
    ordered=sorted(range(len(out)),key=lambda i:out[i]['p_current_weight_1'] or 0.)
    last=1.
    for rank in range(len(ordered),0,-1):
        i=ordered[rank-1];last=min(last,(out[i]['p_current_weight_1'] or 0.)*len(ordered)/rank)
        out[i]['q_current_weight_1']=last
    return out,tests


def serialize_model(model):
    return {'family':model.family,'penalty':model.penalty,'nonnegative':model.nonnegative,
        'efficiency':dict(zip(model.eff.columns,map(float,model.eff.coefficients))),
        'pace':dict(zip(model.pace.columns,map(float,model.pace.coefficients)))}


def team_features(df,school_index,pace=False):
    from scipy.sparse import coo_matrix
    row,col,val=[],[],[];n=len(school_index)
    for i,r in enumerate(df.to_dict('records')):
        if pace:
            for side in ('away','home'):
                s=r[side+'_team'];j=school_index.get(s)
                if j is not None:
                    row.extend((i,i));col.extend((j,j+n));val.extend((1.,r[side+'_tempo']-r['mean_tempo']))
        else:
            a,b=school_index.get(r['team']),school_index.get(r['opponent'])
            if a is not None:
                row.extend((i,i));col.extend((a,a+n));val.extend((1.,r['own_offense']-r['mean_offense']))
            if b is not None:
                row.append(i);col.append(b+2*n);val.append(r['opp_defense']-r['mean_defense'])
    return coo_matrix((val,(row,col)),shape=(len(df),(2 if pace else 3)*n)).tocsr()


def fit_team_adjustments(model,g,t,alpha):
    from sklearn.linear_model import Ridge
    names=sorted(set(t.team)|set(t.opponent));index={s:i for i,s in enumerate(names)}
    ex,_=design_eff(t,model.family);px,_=design_pace(g,model.family)
    eff=Ridge(alpha=alpha,fit_intercept=False,solver='lsqr',tol=1e-9).fit(team_features(t,index),t.pp100.to_numpy()-t.center.to_numpy()-model.eff.predict(ex))
    pace=Ridge(alpha=alpha,fit_intercept=False,solver='lsqr',tol=1e-9).fit(team_features(g,index,True),g.actual_pace.to_numpy()-g.mean_tempo.to_numpy()-model.pace.predict(px))
    return index,eff,pace


def predict_team_adjustments(model,adjustment,g,t):
    index,eff_fit,pace_fit=adjustment;ex,_=design_eff(t,model.family);px,_=design_pace(g,model.family)
    pace=g.mean_tempo.to_numpy()+model.pace.predict(px)+pace_fit.predict(team_features(g,index,True))
    pp100=t.center.to_numpy()+model.eff.predict(ex)+eff_fit.predict(team_features(t,index))
    lookup=dict(zip(g.game_id,pace));team=t[['game_id','side','points']].copy()
    team['prediction']=pp100*np.array([lookup[k] for k in t.game_id])/100
    pivot=team.pivot(index='game_id',columns='side',values='prediction');result=g[['game_id','season','date','day','overtime','actual_total','actual_margin','actual_pace']].copy()
    result['away']=result.game_id.map(pivot.away);result['home']=result.game_id.map(pivot.home)
    result['total']=result.away+result.home;result['margin']=result.home-result.away;result['pace']=pace
    return result,team


def run(dataset_path,output_dir):
    payload=json.loads(Path(dataset_path).read_text());games,teams=prepare(payload)
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    train_g,train_t=subset(games,teams,[2022,2023,2024]);val_g,val_t=subset(games,teams,[2025])
    specs=[('linear','linear',0.,False),('levels','levels',0.,False),('interaction','interaction',0.,False)]
    specs += [('levels_ridge_'+str(int(p)),'levels',p,True) for p in (10.,100.,1000.)]
    validation={};models={}
    baseline_val,bt=predict_current(val_g,val_t);validation['current_fixed']=summarize(baseline_val,bt)
    for name,family,penalty,nn in specs:
        model=fit_model(train_g,train_t,family,penalty,nn);models[name]=model
        pred,team=predict_model(model,val_g,val_t);validation[name]=summarize(pred,team)
        print(json.dumps({'event':'validation','model':name,'metrics':validation[name]}),flush=True)
    winner=min(models,key=lambda n:validation[n]['total']['mae'])
    frozen_spec=next(s for s in specs if s[0]==winner)
    _,family,penalty,nn=frozen_spec
    # Optional team slopes are shrunk to the league model and chosen only on validation.
    team_validation={}
    for alpha in (100.,1000.,10000.):
        adj=fit_team_adjustments(models[winner],train_g,train_t,alpha)
        pred,team=predict_team_adjustments(models[winner],adj,val_g,val_t)
        team_validation[str(int(alpha))]=summarize(pred,team)
    team_alpha=min(team_validation,key=lambda a:team_validation[a]['total']['mae'])
    use_team=team_validation[team_alpha]['total']['mae']<validation[winner]['total']['mae']
    fit_g,fit_t=subset(games,teams,[2022,2023,2024,2025]);test_g,test_t=subset(games,teams,[2026])
    locked=fit_model(fit_g,fit_t,family,penalty,nn)
    baseline_test,baseline_team=predict_current(test_g,test_t)
    selected_test,selected_team=predict_model(locked,test_g,test_t)
    test={'current_fixed':summarize(baseline_test,baseline_team),'selected_league':summarize(selected_test,selected_team)}
    team_adj=fit_team_adjustments(locked,fit_g,fit_t,float(team_alpha))
    team_test,team_scores=predict_team_adjustments(locked,team_adj,test_g,test_t)
    test['team_shrunk']=summarize(team_test,team_scores)
    chosen_test=team_test if use_team else selected_test
    chosen_scores=team_scores if use_team else selected_team
    early=set(test_g.loc[test_g.day<14,'game_id'])
    test['first14_current']=summarize(baseline_test[baseline_test.game_id.isin(early)],baseline_team[baseline_team.game_id.isin(early)])
    test['first14_selected']=summarize(chosen_test[chosen_test.game_id.isin(early)],chosen_scores[chosen_scores.game_id.isin(early)])
    # All six families are reported without using test to change the selection.
    for name,fam,p,positive in specs:
        model=fit_model(fit_g,fit_t,fam,p,positive);pr,tr=predict_model(model,test_g,test_t)
        test[name]=summarize(pr,tr)
    ablations={}
    for drop in ('offense','defense','tempo'):
        model=fit_model(fit_g,fit_t,family,penalty,nn,drop=drop)
        pred,team=predict_model(model,test_g,test_t,drop=drop)
        ablations[drop]={'metrics':summarize(pred,team),'vs_full':bootstrap_difference(pred,selected_test)}
    # Refit the frozen specification on all completed prior seasons for a
    # genuine current-season external check; current outcomes never fit it.
    past_g,past_t=subset(games,teams,[2022,2023,2024,2025,2026]);current_g,current_t=subset(games,teams,[2027])
    current_model=fit_model(past_g,past_t,family,penalty,nn)
    current_base,current_bt=predict_current(current_g,current_t)
    current_pred,current_pt=predict_model(current_model,current_g,current_t)
    current={'current_fixed':summarize(current_base,current_bt),'selected_league':summarize(current_pred,current_pt)}
    current_adj=fit_team_adjustments(current_model,past_g,past_t,float(team_alpha))
    cp,ct=predict_team_adjustments(current_model,current_adj,current_g,current_t)
    current['team_shrunk']=summarize(cp,ct)
    # Unpenalized coefficients answer the descriptive significance question.
    # Penalized prediction weights do not receive invalid OLS p-values.
    linear=fit_model(past_g,past_t,'linear',inference=True)
    levels=fit_model(past_g,past_t,'levels',inference=True)
    level_current,level_current_team=predict_model(levels,current_g,current_t)
    current['levels_descriptive']=summarize(level_current,level_current_team)
    coefficients=coefficient_rows(linear.eff,'efficiency_linear')+coefficient_rows(linear.pace,'pace_linear')
    coefficients+=coefficient_rows(levels.eff,'efficiency_levels')+coefficient_rows(levels.pace,'pace_levels')
    level_rows,level_tests=level_summaries(levels)
    contrast=np.zeros((1,len(linear.eff.columns)));contrast[0,linear.eff.columns.index('offense')]=1.;contrast[0,linear.eff.columns.index('defense')]=-1.
    equality=wald(linear.eff,contrast)
    for prefix,fit in (('offense',linear.eff),('defense',linear.eff),('tempo',linear.pace)):
        row=np.zeros((1,len(fit.columns)));row[0,fit.columns.index(prefix)]=1.
        level_tests[prefix+'_linear_zero']=wald(fit,row)
    counts=[]
    for factor,col in (('offense','own_offense_z'),('defense','opp_defense_z')):
        bins=np.searchsorted(np.array(BOUNDS[1:-1]),past_t[col],side='right')
        for j,level in enumerate(LEVELS):counts.append({'factor':factor,'level':level,'team_games':int((bins==j).sum())})
    for j,level in enumerate(LEVELS):
        z=np.r_[past_g.away_tempo_z,past_g.home_tempo_z];bins=np.searchsorted(np.array(BOUNDS[1:-1]),z,side='right')
        counts.append({'factor':'tempo','level':level,'team_games':int((bins==j).sum())})
    index,ef,pf=current_adj;n=len(index);team_table=[];tc=past_t.groupby('team').size().to_dict()
    for name,i in index.items():team_table.append({'team':name,'training_team_games':int(tc.get(name,0)),
        'efficiency_intercept':float(ef.coef_[i]),'offense_slope_adjustment':float(ef.coef_[i+n]),
        'defense_slope_adjustment':float(ef.coef_[i+2*n]),'pace_intercept':float(pf.coef_[i]),
        'tempo_slope_adjustment':float(pf.coef_[i+n])})
    report={'computed_at':datetime.now(timezone.utc).isoformat(),'scope':'November opening-month, Division I vs Division I',
        'source_dataset_sha256':hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest(),
        'games_by_season':{str(y):int((games.season==y).sum()) for y in sorted(games.season.unique())},
        'non_overtime_games_by_season':{str(y):int(((games.season==y)&~games.overtime).sum()) for y in sorted(games.season.unique())},
        'selection':{'training':[2022,2023,2024],'validation':2025,'test':2026,'external_current':2027,
            'primary_metric':'non-overtime total-score MAE','best_fitted_regression':winner,'frozen_spec':list(frozen_spec),
            'baseline_wins_validation':validation['current_fixed']['total']['mae']<=validation[winner]['total']['mae'],
            'team_alpha':float(team_alpha),'team_selected_within_regression_family_on_validation':use_team,
            'overall_validation_winner':'current_fixed' if validation['current_fixed']['total']['mae']<=min(validation[winner]['total']['mae'],team_validation[team_alpha]['total']['mae']) else ('team_shrunk' if use_team else winner)},
        'validation':validation,'team_validation':team_validation,'test':test,'current':current,
        'test_difference':bootstrap_difference(chosen_test,baseline_test),
        'current_difference':bootstrap_difference(cp if use_team else current_pred,current_base),
        'test_league_difference':bootstrap_difference(selected_test,baseline_test),
        'current_league_difference':bootstrap_difference(current_pred,current_base),
        'current_levels_difference':bootstrap_difference(level_current,current_base),
        'ablations':ablations,'linear_offense_vs_defense_equal':equality,'significance_tests':level_tests,
        'linear_covariance':{'efficiency':linear.eff.covariance_info,'pace':linear.pace.covariance_info},
        'level_covariance':{'efficiency':levels.eff.covariance_info,'pace':levels.pace.covariance_info},
        'coefficient_rows':coefficients,'level_weights':level_rows,'level_counts':counts,
        'selected_current_model':serialize_model(current_model),'descriptive_linear_model':serialize_model(linear),
        'descriptive_level_model':serialize_model(levels),'teams':team_table,
        'limits':['Predictive associations are not causal offense-versus-defense effects.',
            'Historical ratings are from the prior calendar day, not the exact intraday builder snapshot.',
            'Only November matchups are modeled; later-season extrapolation is unsupported.',
            'Primary fit and scoring evaluation exclude overtime; pace is per 40 minutes.',
            'Team adjustments pool school history across changing rosters/coaches and are strongly shrunk.',
            'Source-season changes and five early-season months limit inference about rare extremes.',
            'No betting profitability or A/B grading threshold is optimized or established.',
            'The existing fixed model won validation; the fitted candidates are research comparisons, not an automatically selected replacement.',
            'No production model, historical forecast, official selection or posting is changed.']}
    (output_dir/'results.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    pd.DataFrame(coefficients).to_csv(output_dir/'coefficients.csv',index=False)
    pd.DataFrame(level_rows).to_csv(output_dir/'level_weights.csv',index=False)
    pd.DataFrame(team_table).to_csv(output_dir/'team_adjustments.csv',index=False)
    predictions=pd.concat([baseline_test.assign(model='current_fixed'),selected_test.assign(model='selected_league'),team_test.assign(model='team_shrunk')])
    predictions.to_csv(output_dir/'heldout_predictions.csv',index=False)
    print(json.dumps({k:report[k] for k in ('games_by_season','selection','test','current','test_difference','current_difference','linear_offense_vs_defense_equal')},indent=2),flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();run(args.data,args.output)
