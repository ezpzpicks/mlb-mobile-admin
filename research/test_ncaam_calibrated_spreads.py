"""The approved frozen B-only spread model and the independent total contract."""
from copy import deepcopy
import pytest

from builders.ncaam_matchup_projection import MODEL_VERSION, INDEPENDENT_MODEL_VERSION, MIRRORED_DEFENSE_MODEL_VERSION
from builders.ncaam_spread_calibration import PARAMETERS, B_ONLY_SELECTION
from builders.ncaam_matchup_grades import policy_digest
from builders.ncaam_point_edges import load_policy
from research.test_ncaam_point_edges import forecast, grades


@pytest.mark.parametrize('court,impact,away,home', [
    (0., None, None, None),
    (3.2, {'away': {'offense_change_per_100': -2., 'defense_improvement_per_100': 1.},
           'home': {'offense_change_per_100': 1., 'defense_improvement_per_100': 0.}}, None, None),
    (0., None, {'team': 'Away', 'offense': 117., 'defense': 93., 'tempo': 70.},
     {'team': 'Home', 'offense': 105., 'defense': 108., 'tempo': 70.}),
])
def test_frozen_strength_scale_preserves_court_personnel_and_the_entire_total_component(court, impact, away, home):
    kwargs=dict(court=court, impact=impact, away=away, home=home)
    old, _ = forecast(**kwargs, model_version=INDEPENDENT_MODEL_VERSION)
    new, b = forecast(**kwargs, model_version=MODEL_VERSION)
    p = new['matchup_projection']; raw = -old['fair_home_spread']
    player = p['adjustments']['home']['player_points']-p['adjustments']['away']['player_points']
    expected = 0.8308142655650089*(raw-court-player)+court+player
    assert p['margin'] == expected
    assert p['market_models']['spread']['calibration']['parameters'] == PARAMETERS
    assert p['market_models']['spread']['calibration']['source_model_version'] == MIRRORED_DEFENSE_MODEL_VERSION
    assert p['market_models']['total'] == old['matchup_projection']['market_models']['total']
    assert new['total'] == old['total']
    assert p['away_points']+p['home_points'] == pytest.approx(p['total'])
    assert p['home_points']-p['away_points'] == pytest.approx(expected)
    assert grades(new,b)['spread']['policy']['spread_selection'] == B_ONLY_SELECTION


@pytest.mark.parametrize('gap,expected', [(-20.,'Research'),(-7.,'Research'),(-6.999,'B'),(-5.,'B'),
    (-4.999,'Research'),(4.999,'Research'),(5.,'B'),(6.999,'B'),(7.,'Research'),(20.,'Research')])
def test_only_the_exact_B_band_qualifies_in_either_direction(gap, expected):
    f,b=forecast(model_version=MODEL_VERSION)
    f['market']['home_spread']=gap-f['matchup_projection']['margin']
    g=grades(f,b)['spread']
    assert g['grade']==expected
    assert g['eligible']==(expected=='B')
    assert g['side']==(('home' if gap>0 else 'away') if expected=='B' else None)
    assert g['edge_grade']==('A' if abs(gap)>=7 else 'B' if abs(gap)>=5 else 'Research')


def test_the_window_does_not_override_price_or_total_eligibility():
    f,b=forecast(model_version=MODEL_VERSION)
    f['market']['home_spread']=6-f['matchup_projection']['margin']
    f['market']['total']=f['total']-11
    assert grades(f,b)['spread']['grade']=='B'
    assert grades(f,b)['total']['grade']=='A'
    f['market']['odds']=-120
    assert grades(f,b)['spread']['grade']=='Research'
    assert grades(f,b)['total']['grade']=='Research'


@pytest.mark.parametrize('field', ['scale','court','player','neutral','source_margin','source_version'])
def test_rehashing_cannot_change_or_double_the_calibration(field):
    f,b=forecast(court=3.2,model_version=MODEL_VERSION)
    p=f['matchup_projection'];c=p['market_models']['spread']['calibration']
    if field=='scale':c['parameters']['scale']=1.
    elif field=='court':c['home_court_points']*=2
    elif field=='player':c['player_margin']+=1
    elif field=='neutral':c['neutral_margin']+=1
    elif field=='source_margin':c['source_margin']+=1
    else:c['source_model_version']=INDEPENDENT_MODEL_VERSION
    p['sha256']=policy_digest({k:v for k,v in p.items() if k!='sha256'})
    with pytest.raises(ValueError,match='captured inputs'):grades(f,b)


def test_the_selection_policy_cannot_be_widened_or_borrowed_by_old_forecasts():
    f,b=forecast(model_version=MODEL_VERSION)
    policy=load_policy();policy['spread_selection']={**B_ONLY_SELECTION,'maximum_point_edge_exclusive':8}
    with pytest.raises(ValueError,match='frozen B-only'):grades(f,b,policy=policy)
    old,b=forecast(model_version=INDEPENDENT_MODEL_VERSION)
    policy['spread_selection']=B_ONLY_SELECTION
    with pytest.raises(ValueError,match='archived'):grades(old,b,policy=policy)
    assert 'spread_selection' not in grades(old,b)['spread']['policy']


def test_neutral_side_swap_reverses_margin_and_keeps_the_total():
    a={'team':'Away','offense':120.,'defense':93.,'tempo':68.}
    h={'team':'Home','offense':107.,'defense':112.,'tempo':74.}
    f,_=forecast(away=a,home=h,model_version=MODEL_VERSION)
    swapped,_=forecast(away={**h,'team':'Away'},home={**a,'team':'Home'},model_version=MODEL_VERSION)
    assert f['total']==swapped['total']
    assert f['fair_home_spread']==-swapped['fair_home_spread']
