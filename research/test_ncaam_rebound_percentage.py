import pytest

from builders.ncaam_rebounding import percentage_matchup_transfer
from builders.ncaam_matchup_projection import PRE_REBOUND_MODEL_VERSION
from research.test_ncaam_rebounding import profile, forecast
from research.test_ncaam_point_edges import LOCK


def edge_ratings(edge, gp=3):
    """Synthetic ratios encode an exact, signed home edge; no outcome input."""
    denominator = 1_000_000
    high = int(round(denominator*(.5+edge/200)))
    low = denominator-high
    return ({'team': 'Away', 'offense': 105., 'defense': 105., 'tempo': 70.,
             'rebounding': profile('Away', orb=low, opp_drb=high, opp_orb=high, drb=low, gp=gp)},
            {'team': 'Home', 'offense': 105., 'defense': 105., 'tempo': 70.,
             'rebounding': profile('Home', orb=high, opp_drb=low, opp_orb=low, drb=high, gp=gp)})


@pytest.mark.parametrize('edge,fraction,points85,points60', [
    (.1, .00075, .06375, .045), (2.5, .01875, 1.59375, 1.125),
    (10., .075, 6.375, 4.5), (17.5, .13125, 11.15625, 7.875),
    (20., .15, 12.75, 9.), (100., .75, 63.75, 45.)])
def test_user_percentage_curve_scales_with_the_favored_sides_projection(edge, fraction, points85, points60):
    a, h = edge_ratings(edge)
    for baseline, expected in [(85., points85), (60., points60)]:
        p = percentage_matchup_transfer(a, h, 2027, LOCK, away_points=100., home_points=baseline)
        assert p['home_edge_pp'] == pytest.approx(edge)
        assert p['requested_boost_fraction'] == pytest.approx(fraction)
        assert p['home_points'] == pytest.approx(expected)
        assert p['away_points'] == pytest.approx(-expected)
        assert p['home_points']+p['away_points'] == 0.
        assert p['score_floor_applied'] is False
        assert 'max_transfer_points' not in p


def test_85_to_60_example_becomes_97_75_to_47_25_with_the_identical_total():
    a, h = edge_ratings(20.)
    p = percentage_matchup_transfer(a, h, 2027, LOCK, away_points=60., home_points=85.)
    assert 85.+p['home_points'] == pytest.approx(97.75)
    assert 60.+p['away_points'] == pytest.approx(47.25)
    assert p['home_points']+p['away_points'] == 0.


def test_rebounding_favored_underdog_uses_its_own_lower_projection():
    a, h = edge_ratings(-20.)
    p = percentage_matchup_transfer(a, h, 2027, LOCK, away_points=60., home_points=85.)
    assert p['favored_side'] == 'away'
    assert p['favored_baseline_points'] == 60.
    assert p['away_points'] == pytest.approx(9.)
    assert p['home_points'] == pytest.approx(-9.)


@pytest.mark.parametrize('gp', [1, 2, 3, 12])
def test_percentage_curve_has_no_hidden_game_count_discount(gp):
    a, h = edge_ratings(20., gp)
    p = percentage_matchup_transfer(a, h, 2027, LOCK, away_points=60., home_points=85.)
    assert p['home_points'] == pytest.approx(12.75)
    assert p['sample_weight'] == 1.


def test_tiny_edges_are_nonzero_and_large_edges_continue_past_the_old_cap():
    previous = 0.
    for edge in [.001, .1, 2.49, 2.5, 2.51, 14.99, 15., 15.01, 17.5, 20., 30.]:
        a, h = edge_ratings(edge)
        p = percentage_matchup_transfer(a, h, 2027, LOCK, away_points=150., home_points=85.)
        assert p['home_points'] == pytest.approx(85*.0075*edge)
        assert p['home_points'] > previous
        previous = p['home_points']


def test_extreme_transfer_stops_only_at_the_opponents_zero_score_floor():
    a, h = edge_ratings(100.)
    p = percentage_matchup_transfer(a, h, 2027, LOCK, away_points=60., home_points=85.)
    assert p['requested_transfer_points'] == pytest.approx(63.75)
    assert p['applied_transfer_points'] == 60.
    assert p['score_floor_applied'] is True
    assert (85.+p['home_points'], 60.+p['away_points']) == (145., 0.)


def test_projection_accepts_zero_floor_and_preserves_the_pre_transfer_total():
    a, h = edge_ratings(100.)
    # Court contributes -4/+4; neither offense qualifies for the extra bonus.
    a['offense'], h['offense'] = 64/.7, 81/.7
    old, _ = forecast(away=a, home=h, court=8., model_version=PRE_REBOUND_MODEL_VERSION)
    new, b = forecast(away=a, home=h, court=8.)
    assert old['matchup_projection']['away_points'] == pytest.approx(60.)
    assert old['matchup_projection']['home_points'] == pytest.approx(85.)
    assert new['matchup_projection']['away_points'] == 0.
    assert new['matchup_projection']['home_points'] == pytest.approx(145.)
    assert new['total'] == old['total']
    from builders.ncaam_point_edges import capture_grades
    assert capture_grades(new, [b], published_at=LOCK)['total']['projected_total'] == old['total']


def test_large_percentage_boost_has_no_artificial_post_transfer_200_point_cap():
    a, h = edge_ratings(100.)
    for rating in (a, h): rating.update(offense=130., defense=135., tempo=80.)
    old, _ = forecast(away=a, home=h, model_version=PRE_REBOUND_MODEL_VERSION)
    new, _ = forecast(away=a, home=h)
    before = old['matchup_projection']['home_points']
    assert new['matchup_projection']['home_points'] == pytest.approx(before*1.75)
    assert new['matchup_projection']['home_points'] > 200.
    assert new['matchup_projection']['away_points'] >= 0.
    assert new['total'] == old['total']


def test_swapping_teams_uses_the_same_favored_baseline_and_reverses_the_transfer():
    a, h = edge_ratings(20.)
    first = percentage_matchup_transfer(a, h, 2027, LOCK, away_points=60., home_points=85.)
    second = percentage_matchup_transfer(h, a, 2027, LOCK, away_points=85., home_points=60.)
    assert first['home_points'] == second['away_points']
    assert first['away_points'] == second['home_points']


def test_equal_rebound_matchups_leave_both_scores_unchanged():
    a, h = edge_ratings(0.)
    p = percentage_matchup_transfer(a, h, 2027, LOCK, away_points=60., home_points=85.)
    assert p['favored_side'] is None
    assert p['home_points'] == p['away_points'] == 0.


@pytest.mark.parametrize('bad', [-1., float('nan'), float('inf'), True])
def test_invalid_baseline_points_cannot_create_a_transfer(bad):
    a, h = edge_ratings(20.)
    p = percentage_matchup_transfer(a, h, 2027, LOCK, away_points=60., home_points=bad)
    assert p['status'] == 'unavailable'
    assert p['home_points'] == p['away_points'] == 0.


def test_percentage_is_applied_once_after_existing_player_court_and_offense_effects():
    a, h = edge_ratings(20.)
    h['offense'] = 125.
    impact = {'away': {'offense_change_per_100': 2.}, 'home': {'defense_improvement_per_100': 1.}}
    before, _ = forecast(away=a, home=h, court=3.2, impact=impact, model_version=PRE_REBOUND_MODEL_VERSION)
    after, _ = forecast(away=a, home=h, court=3.2, impact=impact)
    expected = before['matchup_projection']['home_points']*.15
    assert after['matchup_projection']['home_points']-before['matchup_projection']['home_points'] == pytest.approx(expected)
    assert before['matchup_projection']['away_points']-after['matchup_projection']['away_points'] == pytest.approx(expected)
    assert after['total'] == before['total']
