from copy import deepcopy

import pytest

from builders.ncaam_matchup_projection import (
    MIRRORED_DEFENSE_MODEL_VERSION as MODEL_VERSION, OFFENSE_ONLY_MODEL_VERSION, ADJUSTED_TEMPO_MODEL_VERSION,
)
from builders.ncaam_matchup_grades import policy_digest
from research.test_ncaam_point_edges import forecast, grades


@pytest.mark.parametrize('offense_z,deduction', [
    (-2., 15.), (-1.5, 15.), (-1.499, 8.), (-1., 8.), (-.999, 5.),
    (0., 5.), (.5, 5.), (1., 3.), (1.499, 3.), (1.5, 1.), (2., 1.),
])
def test_elite_defense_suppresses_only_the_opponent_at_each_mirrored_offense_boundary(offense_z, deduction):
    away = {'team': 'Away', 'offense': 105. + 8 * offense_z, 'defense': 105., 'tempo': 70.}
    home = {'team': 'Home', 'offense': 110., 'defense': 93., 'tempo': 70.}
    impact = {'away': {'offense_change_per_100': 2.}, 'home': {'defense_improvement_per_100': 1.}}
    args = dict(away=away, home=home, court=3.2, impact=impact)
    old, _ = forecast(**args, model_version=OFFENSE_ONLY_MODEL_VERSION)
    new, benchmark = forecast(**args)
    p = new['matchup_projection']
    assert p['model_version'] == MODEL_VERSION
    proof = p['adjustments']['away']['opponent_defense_deduction']
    assert proof['qualified'] is True
    assert proof['points'] == deduction
    assert p['home_points'] == old['matchup_projection']['home_points']
    assert p['away_points'] == old['matchup_projection']['away_points'] - deduction
    assert new['total'] == pytest.approx(old['total'] - deduction)
    assert new['fair_home_spread'] == pytest.approx(old['fair_home_spread'] - deduction)
    assert p['possessions'] == old['matchup_projection']['possessions']
    assert 'rebounding_adjustment' not in p
    assert grades(new, benchmark)['total']['projected_total'] == new['total']


@pytest.mark.parametrize('defense_z', [-1.499999, -1., 0., 1.5])
def test_nonelite_defenses_do_not_change_the_offense_only_projection(defense_z):
    home = {'team': 'Home', 'offense': 125., 'defense': 105. + 8 * defense_z, 'tempo': 70.}
    old, _ = forecast(home=home, model_version=OFFENSE_ONLY_MODEL_VERSION)
    new, _ = forecast(home=home)
    assert new['total'] == old['total']
    assert new['fair_home_spread'] == old['fair_home_spread']
    assert not new['matchup_projection']['adjustments']['away']['opponent_defense_deduction']['qualified']


def test_offense_bonus_and_defensive_deduction_stack_once_without_transferring_points():
    away = {'team': 'Away', 'offense': 93., 'defense': 117., 'tempo': 70.}
    home = {'team': 'Home', 'offense': 117., 'defense': 93., 'tempo': 70.}
    original, _ = forecast(away=away, home=home, model_version=ADJUSTED_TEMPO_MODEL_VERSION)
    offense_only, _ = forecast(away=away, home=home, model_version=OFFENSE_ONLY_MODEL_VERSION)
    new, _ = forecast(away=away, home=home)
    p = new['matchup_projection']
    assert p['adjustments']['home']['offense_bonus']['points'] == 15.
    assert p['adjustments']['away']['opponent_defense_deduction']['points'] == 15.
    assert p['home_points'] == original['matchup_projection']['home_points'] + 15.
    assert p['away_points'] == original['matchup_projection']['away_points'] - 15.
    assert new['total'] == original['total']
    assert original['fair_home_spread'] - new['fair_home_spread'] == pytest.approx(30.)
    assert offense_only['fair_home_spread'] - new['fair_home_spread'] == pytest.approx(15.)
    swapped, _ = forecast(away={**home, 'team': 'Away'}, home={**away, 'team': 'Home'})
    assert new['total'] == swapped['total']
    assert new['fair_home_spread'] == -swapped['fair_home_spread']


def test_two_elite_defenses_reduce_both_scores_and_regrade_the_total():
    away = {'team': 'Away', 'offense': 117., 'defense': 93., 'tempo': 70.}
    home = {'team': 'Home', 'offense': 121., 'defense': 89., 'tempo': 70.}
    old, _ = forecast(away=away, home=home, model_version=OFFENSE_ONLY_MODEL_VERSION)
    new, benchmark = forecast(away=away, home=home)
    assert new['total'] == old['total'] - 2.
    assert new['fair_home_spread'] == old['fair_home_spread']
    for side in ('away', 'home'):
        assert new['matchup_projection'][side + '_points'] == old['matchup_projection'][side + '_points'] - 1.
    new['market']['total'] = old['total'] + 8.
    g = grades(new, benchmark)['total']
    assert (g['grade'], g['side'], g['edge']) == ('A', 'Under', 10.)


@pytest.mark.parametrize('change', ['missing', 'policy', 'reverse', 'double', 'rebound'])
def test_rehashed_missing_or_altered_defense_proofs_cannot_create_grades(change):
    away = {'team': 'Away', 'offense': 93., 'defense': 117., 'tempo': 70.}
    home = {'team': 'Home', 'offense': 117., 'defense': 93., 'tempo': 70.}
    new, benchmark = forecast(away=away, home=home)
    bad = deepcopy(new)
    p = bad['matchup_projection']
    if change == 'missing': del p['adjustments']['away']['opponent_defense_deduction']
    elif change == 'policy': p['defense_deduction_policy']['points_by_opponent_offense']['very_weak'] = 30.
    elif change == 'rebound': p['adjustments']['away']['rebounding_points'] = 0.
    else:
        p['adjustments']['away']['opponent_defense_deduction']['points'] *= -1 if change == 'reverse' else 2
    p['sha256'] = policy_digest({k: v for k, v in p.items() if k != 'sha256'})
    with pytest.raises(ValueError, match='captured inputs'):
        grades(bad, benchmark)


def test_archived_offense_only_forecasts_cannot_silently_inherit_defensive_deductions():
    away = {'team': 'Away', 'offense': 93., 'defense': 117., 'tempo': 70.}
    home = {'team': 'Home', 'offense': 117., 'defense': 93., 'tempo': 70.}
    old, benchmark = forecast(away=away, home=home, model_version=OFFENSE_ONLY_MODEL_VERSION)
    original = deepcopy(old)
    forecast(away=away, home=home)
    assert old == original
    assert 'defense_deduction_policy' not in old['matchup_projection']
    assert grades(old, benchmark)['total']['projection_model_version'] == OFFENSE_ONLY_MODEL_VERSION
