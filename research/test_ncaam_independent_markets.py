"""Independent total/margin contracts and saved-proof integrity."""
from copy import deepcopy
import math
import pytest

from builders.ncaam_matchup_projection import (
    MODEL_VERSION, MIRRORED_DEFENSE_MODEL_VERSION, ADJUSTED_TEMPO_MODEL_VERSION,
    from_captured_inputs, project,
)
from builders.ncaam_overtime import overtime_allowance
from builders.ncaam_matchup_grades import policy_digest
from research.test_ncaam_point_edges import forecast, grades, LOCK, TIP


@pytest.mark.parametrize('away,home,court', [
    ({'team':'Away','offense':93.,'defense':117.,'tempo':70.},
     {'team':'Home','offense':117.,'defense':93.,'tempo':70.},0.),
    ({'team':'Away','offense':104.,'defense':106.,'tempo':65.},
     {'team':'Home','offense':120.,'defense':110.,'tempo':74.},3.2),
    ({'team':'Away','offense':117.,'defense':93.,'tempo':70.},
     {'team':'Home','offense':121.,'defense':89.,'tempo':70.},0.),
])
def test_independent_total_and_margin_recombine_without_carrying_elite_extras_into_total(away,home,court):
    old,_=forecast(away=away,home=home,court=court,model_version=MIRRORED_DEFENSE_MODEL_VERSION)
    core,_=forecast(away=away,home=home,court=court,model_version=ADJUSTED_TEMPO_MODEL_VERSION)
    new,b=forecast(away=away,home=home,court=court,model_version=MODEL_VERSION)
    p=new['matchup_projection']
    ot=overtime_allowance(core['total'],-core['fair_home_spread'],home_court=court,neutral_site=court==0)
    assert new['fair_home_spread']==old['fair_home_spread']
    assert new['total']==ot['total_including_expected_overtime']
    assert p['market_models']['total']['offense_extras_applied'] is False
    assert p['market_models']['total']['defense_extras_applied'] is False
    assert p['home_points']+p['away_points']==pytest.approx(new['total'])
    assert p['home_points']-p['away_points']==pytest.approx(-new['fair_home_spread'])
    new_grades=grades(new,b);old_grades=grades(old,b)
    for k in ['grade','side','line','edge','fair_home_spread']:
        assert new_grades['spread'][k]==old_grades['spread'][k]
    assert new_grades['total']['projected_total']==new['total']
    old_before=deepcopy(old)
    forecast(away=away,home=home,court=court,model_version=MODEL_VERSION)
    assert old==old_before


def test_neutral_side_swap_and_quote_changes_preserve_the_independent_score_contract():
    a={'team':'Away','offense':120.,'defense':93.,'tempo':68.}
    h={'team':'Home','offense':107.,'defense':112.,'tempo':74.}
    first,b=forecast(away=a,home=h,model_version=MODEL_VERSION)
    swapped,_=forecast(away={**h,'team':'Away'},home={**a,'team':'Home'},model_version=MODEL_VERSION)
    assert first['total']==swapped['total']
    assert first['fair_home_spread']==-swapped['fair_home_spread']
    changed,_=forecast(away=a,home=h,home_line=-30.,total_line=200.,model_version=MODEL_VERSION)
    assert first['matchup_projection']==changed['matchup_projection']
    first['actual_home_score']=1;first['actual_away_score']=999
    assert grades(first,b)['total']['projected_total']==changed['total']


def test_live_projector_measures_personnel_effect_including_its_overtime_change():
    from research.ncaam_test_support import complete_population
    from builders.ncaam_model import team_key
    ratings=complete_population({team_key('Away'):{'team':'Away','offense':110.,'defense':108.,'tempo':70.},
        team_key('Home'):{'team':'Home','offense':115.,'defense':105.,'tempo':70.}})
    bundle={'season':2027,'observed_at':'2026-11-14T11:00:00+00:00','source':'Synthetic Torvik fixture',
            'source_url':'https://barttorvik.com/2027_team_results.csv','league_efficiency':105.,'teams':ratings}
    game={'away':'Away','home':'Home','away_aliases':['Away'],'home_aliases':['Home'],
          'season':2027,'tipoff':TIP,'neutral_site':True}
    market={'home_spread':-2.,'total':150.,'as_of':LOCK}
    base=project(game,bundle,market,LOCK)
    impact={'away':{'offense_change_per_100':-2.,'defense_improvement_per_100':1.},
            'home':{'offense_change_per_100':1.,'defense_improvement_per_100':0.}}
    revised=project(game,bundle,market,LOCK,lineup_adjustment=impact)
    assert revised['baseline_away_score']==base['matchup_projection']['away_points']
    assert revised['baseline_home_score']==base['matchup_projection']['home_points']
    assert revised['lineup_total_change']==revised['total']-base['total']
    assert revised['lineup_margin_change']==base['fair_home_spread']-revised['fair_home_spread']


@pytest.mark.parametrize('mutation', ['missing','bonus_in_total','wrong_spread','doubled_ot','parameters','spliced_versions'])
def test_rehashed_mismatched_market_components_cannot_be_graded(mutation):
    f,b=forecast(home={'team':'Home','offense':125.,'defense':93.,'tempo':70.},model_version=MODEL_VERSION)
    p=f['matchup_projection']
    if mutation=='missing':del p['market_models']
    elif mutation=='bonus_in_total':p['market_models']['total']['offense_extras_applied']=True
    elif mutation=='wrong_spread':p['market_models']['spread']['margin']+=1
    elif mutation=='doubled_ot':p['market_models']['total']['overtime']['expected_extra_total_points']*=2
    elif mutation=='parameters':p['market_models']['total']['overtime']['parameters']['intercept']=0.
    else:p['market_models']['spread']['model_version']=ADJUSTED_TEMPO_MODEL_VERSION
    p['sha256']=policy_digest({k:v for k,v in p.items() if k!='sha256'})
    with pytest.raises(ValueError,match='captured inputs'):grades(f,b)


def test_overtime_decreases_with_disparity_and_rejects_invalid_venues():
    close=overtime_allowance(150.,3.2,home_court=3.2,neutral_site=False)
    blowout=overtime_allowance(150.,40.,home_court=3.2,neutral_site=False)
    assert 0<=blowout['expected_extra_total_points']<close['expected_extra_total_points']<2
    for total,margin,court,neutral in [(math.nan,0,0,True),(150,0,1,True),(150,0,0,None),(150,0,True,False)]:
        with pytest.raises(ValueError):overtime_allowance(total,margin,home_court=court,neutral_site=neutral)
