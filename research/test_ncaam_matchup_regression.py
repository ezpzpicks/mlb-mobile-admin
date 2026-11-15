import csv
from copy import deepcopy
import io
import json

import numpy as np
import pytest

from research.ncaam_regression_data import parse_game_logs
from research.ncaam_matchup_regression import level_basis, prepare, design_eff, design_pace, fit_regression, dyadic_covariance


def logs(day='11/14/26',minutes=200.,possessions=70.):
    rows=[]
    for own,opponent,venue,result,score in [('Away','Home','A','L, 77-70',70),('Home','Away','H','W, 77-70',77)]:
        row=['0']*31;row[0]=day;row[2]=own;row[4]=opponent;row[5]=venue;row[6]=result
        row[9]=str(score/possessions*100);row[22]='2027';row[23]=str(possessions*200/minutes);row[24]='synthetic-game'
        box=[0]*35;box[1]=minutes;box[34]=possessions;row[29]=json.dumps(box);rows.append(row)
    return rows


def encode(rows):
    stream=io.StringIO();csv.writer(stream).writerows(rows);return stream.getvalue().encode()


def game_payload():
    r={'game_id':'fixture','season':2027,'date':'2026-11-14','ratings_date':'2026-11-13','neutral':False,'overtime':False}
    for metric,mean,sd in [('offense',105.,7.),('defense',105.,5.),('tempo',70.,2.)]:
        r['mean_'+metric]=mean;r['sd_'+metric]=sd
        for side,z in [('away',-.7),('home',1.7)]:
            r[side+'_'+metric]=mean+sd*z;r[side+'_'+metric+'_z']=z
    for side,score in [('away',70.),('home',77.)]:
        r[side+'_team']=side;r[side+'_points']=score;r[side+'_pp100']=score/70*100
        r[side+'_pace']=70.;r[side+'_possessions']=70.;r[side+'_minutes']=200.
    return {'games':[r]}


def test_raw_box_possessions_and_overtime_are_not_confused_with_a_score_column():
    rows=logs(minutes=225.,possessions=75.);parsed,audit=parse_game_logs(encode(rows),2027)
    assert len(parsed)==1 and not audit
    assert parsed[0]['overtime'] is True
    assert parsed[0]['home']['possessions']==75.
    assert parsed[0]['home']['pace']==pytest.approx(75*200/225)
    assert parsed[0]['home']['pp100']==pytest.approx(77/75*100)


def test_postgame_adjusted_ratings_are_never_used_as_predictors():
    rows=logs();first=parse_game_logs(encode(rows),2027)
    for row in rows:row[7]='99999';row[8]='-99999'
    assert parse_game_logs(encode(rows),2027)==first
    assert parse_game_logs(encode(logs(day='11/15/26')),2027)[0]==[]


def test_reciprocal_results_and_previous_day_ratings_are_required():
    rows=logs();rows[1][6]='W, 78-70'
    assert not parse_game_logs(encode(rows),2027)[0]
    p=game_payload();p['games'][0]['ratings_date']='2026-11-14'
    with pytest.raises(ValueError,match='precede'):prepare(p)


def test_piecewise_weights_one_recover_the_original_rating_units_without_jumps():
    z=np.array([-3.,-1.5,-1.,-.5,0.,.5,1.,1.5,3.]);b=level_basis(z,7.)
    np.testing.assert_allclose(b.sum(axis=1),z*7,atol=1e-12)
    np.testing.assert_array_equal(level_basis(0.,7.),np.zeros(6))
    for knot in (-1.5,-1.,0.,1.,1.5):
        assert np.max(abs(level_basis(knot-1e-10,7)-level_basis(knot+1e-10,7)))<1e-8


def test_predictor_matrices_ignore_outcomes_and_pace_is_symmetric():
    p=game_payload();g,t=prepare(p);ex,ec=design_eff(t,'levels');px,pc=design_pace(g,'levels')
    p['games'][0].update(away_points=200.,home_points=1.,away_pp100=300.,home_pp100=1.,away_pace=90.,home_pace=90.)
    ng,nt=prepare(p);np.testing.assert_array_equal(design_eff(nt,'levels')[0],ex);np.testing.assert_array_equal(design_pace(ng,'levels')[0],px)
    swapped=g.copy()
    for m in ('tempo','tempo_z'):
        swapped['away_'+m],swapped['home_'+m]=g['home_'+m],g['away_'+m]
    np.testing.assert_allclose(design_pace(swapped,'levels')[0],px)


def test_shared_school_covariance_matches_brute_force_dependence_pairs():
    rng=np.random.default_rng(7);owners=[('a','b'),('b','a'),('a','c'),('c','a'),('d','e'),('e','d'),('a','b'),('b','a')]
    X=np.column_stack((np.ones(8),rng.normal(size=8)));y=X@np.array([2.,.7])+rng.normal(size=8)
    f=fit_regression(X,y,['intercept','offense'],owners);res=y-X@f.coefficients
    cov,info=dyadic_covariance(X,res,owners);score=X*res[:,None];meat=np.zeros((2,2))
    for i in range(8):
        for j in range(8):
            if set(owners[i])&set(owners[j]):meat+=np.outer(score[i],score[j])
    bread=np.linalg.pinv(X.T@X);expected=bread@meat@bread*((8-1)/(8-2))*(5/4)
    eig,vec=np.linalg.eigh((expected+expected.T)/2);expected=(vec*np.maximum(eig,0))@vec.T
    np.testing.assert_allclose(cov,expected,atol=1e-12)
    np.testing.assert_allclose(f.coefficients,np.linalg.lstsq(X,y,rcond=None)[0])
