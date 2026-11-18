from copy import deepcopy

import pytest

from builders.ncaam_matchup_projection import (
    MODEL_VERSION, PRE_REBOUND_MODEL_VERSION, PERCENTAGE_REBOUND_MODEL_VERSION,
)
from builders.ncaam_matchup_grades import policy_digest
from research.test_ncaam_point_edges import forecast, grades
from research.test_ncaam_rebound_percentage import edge_ratings


@pytest.mark.parametrize('home_line', [-41.5, -15.5, 1.5, 32.5])
@pytest.mark.parametrize('rebound_edge', [-20., 20.])
def test_live_scores_keep_offense_players_court_and_totals_without_rebounds(home_line, rebound_edge):
    away, home = edge_ratings(rebound_edge)
    home['offense'] = 125.
    impact = {'away': {'offense_change_per_100': 2.}, 'home': {'defense_improvement_per_100': 1.}}
    args = dict(away=away, home=home, court=3.2, impact=impact, home_line=home_line)
    current, benchmark = forecast(**args)
    baseline, _ = forecast(**args, model_version=PRE_REBOUND_MODEL_VERSION)
    archived, _ = forecast(**args, model_version=PERCENTAGE_REBOUND_MODEL_VERSION)
    assert current['modelVersion'] == MODEL_VERSION == 'ncaam-v0.13-offense-only-2026-11-17'
    p = current['matchup_projection']
    assert 'rebounding_adjustment' not in p
    assert p['adjustments']['home']['offense_bonus']['points'] == 5.
    for side in ('away', 'home'):
        assert 'rebounding_points' not in p['adjustments'][side]
        assert p[side + '_points'] == baseline['matchup_projection'][side + '_points']
        assert p['adjustments'][side] == baseline['matchup_projection']['adjustments'][side]
    assert current['fair_home_spread'] == baseline['fair_home_spread']
    assert current['fair_home_spread'] != archived['fair_home_spread']
    assert current['total'] == baseline['total'] == archived['total']
    for edge in (-10., -8., 0., 7.999999999, 8., 9.999999999, 10.):
        current['market']['total'] = archived['market']['total'] = current['total'] - edge
        live_total, old_total = grades(current, benchmark)['total'], grades(archived, benchmark)['total']
        for field in ('edge', 'grade', 'projected_side', 'projected_total', 'line'):
            assert live_total[field] == old_total[field]
        assert not any('Rebounding inputs unavailable' in flag for flag in live_total['confidence_flags'])


def test_live_proof_rejects_reintroduced_rebound_adjustment_even_after_rehashing():
    away, home = edge_ratings(20.)
    current, benchmark = forecast(away=away, home=home)
    archived, _ = forecast(away=away, home=home, model_version=PERCENTAGE_REBOUND_MODEL_VERSION)
    bad = deepcopy(current)
    p, old = bad['matchup_projection'], archived['matchup_projection']
    p['rebounding_adjustment'] = deepcopy(old['rebounding_adjustment'])
    for side in ('away', 'home'):
        p['adjustments'][side]['rebounding_points'] = old['adjustments'][side]['rebounding_points']
        p[side + '_points'] = old[side + '_points']
    p['margin'] = old['margin']
    bad['fair_home_spread'] = -p['margin']
    p['sha256'] = policy_digest({k: v for k, v in p.items() if k != 'sha256'})
    with pytest.raises(ValueError, match='captured inputs'):
        grades(bad, benchmark)
