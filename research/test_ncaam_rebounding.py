from copy import deepcopy
import csv
import io
import json
from pathlib import Path

import pytest

from builders.ncaam_rebounding import (INPUT_POLICY, parse_rebounding_inputs, matchup_transfer,
    percentage_matchup_transfer)
from builders.ncaam_matchup_projection import (from_captured_inputs, PRE_REBOUND_MODEL_VERSION,
    CAPPED_REBOUND_MODEL_VERSION, PERCENTAGE_REBOUND_MODEL_VERSION)
from builders.ncaam_point_edges import capture_grades
from builders.ncaam_matchup_grades import policy_digest
from research.test_ncaam_point_edges import forecast as current_forecast, grades, LOCK


def forecast(**kwargs):
    """Exercise archived rebound proofs without changing the live default."""
    return current_forecast(**{'model_version': PERCENTAGE_REBOUND_MODEL_VERSION, **kwargs})


def profile(team, *, orb=30, drb=70, opp_orb=30, opp_drb=70, gp=3):
    return {'team': team, 'season': 2027, 'policy': INPUT_POLICY,
        'observed_at': '2026-11-14T11:00:00+00:00', 'cutoff_date': '2026-11-14',
        'last_game_date': '2026-11-13', 'source_url': 'https://barttorvik.com/getgamestats.php?year=2027&csv=1',
        'source_sha256': 'a'*64, 'd1_games': gp,
        'orb': orb, 'drb': drb, 'opp_orb': opp_orb, 'opp_drb': opp_drb}


def ratings(*, gp=3, extreme=False, offense=105.):
    low, high = (20, 40) if extreme else (25, 35)
    return ({'team': 'Away', 'offense': offense, 'defense': 105., 'tempo': 70.,
             'rebounding': profile('Away', orb=low, opp_drb=100-low, opp_orb=high, drb=100-high, gp=gp)},
            {'team': 'Home', 'offense': offense, 'defense': 105., 'tempo': 70.,
             'rebounding': profile('Home', orb=high, opp_drb=100-high, opp_orb=low, drb=100-low, gp=gp)})


def paired_log(day='11/12/26', gid='game', *, orb=12, drb=18, opp_orb=6, opp_drb=28, opponent='Home'):
    box = [0]*35
    box[:4] = [day, 200, 'Away', opponent]
    box[10], box[11], box[18] = orb, drb, 80
    box[25], box[26], box[33], box[34] = opp_orb, opp_drb, 70, 70.
    out = io.StringIO(); writer = csv.writer(out)
    for own, other, venue, win, own_orb, own_drb, other_orb, other_drb in (
        ('Away', opponent, 'A', 'W', orb, drb, opp_orb, opp_drb),
        (opponent, 'Away', 'H', 'L', opp_orb, opp_drb, orb, drb)):
        row = ['']*31
        row[0], row[2], row[4], row[5], row[6] = day, own, other, venue, win+', 80-70'
        row[12], row[17] = 100*own_orb/(own_orb+other_drb), 100*other_orb/(other_orb+own_drb)
        row[22], row[23], row[24], row[29] = '2027', '70', gid, json.dumps(box)
        writer.writerow(row)
    return out.getvalue()


def test_rate_inputs_weight_opportunities_and_exclude_same_day_future_and_non_d1():
    text = (paired_log() + paired_log('11/13/26', 'second', orb=5, opp_drb=5)
        + paired_log('11/14/26', 'today') + paired_log('11/15/26', 'future')
        + paired_log(gid='non-d1', opponent='Other College'))
    result = parse_rebounding_inputs(text, 2027, LOCK, {'away': {'team': 'Away'}, 'home': {'team': 'Home'}})
    a = result['teams']['away']
    assert (a['d1_games'], a['orb'], a['opp_drb']) == (2, 17, 33)
    assert a['last_game_date'] == '2026-11-13'
    f = matchup_transfer({'team': 'Away', 'rebounding': a}, {'team': 'Home', 'rebounding': result['teams']['home']}, 2027, LOCK)
    assert f['inputs']['away']['orb_pct'] == pytest.approx(17/50)
    assert result['audit']['accepted_games'] == 2
    assert result['audit']['same_day_or_future_rows'] == 4
    assert result['audit']['outside_d1_rows'] == 2


def test_unpaired_or_conflicting_boxes_cannot_create_a_rebound_advantage():
    rows = list(csv.reader(io.StringIO(paired_log())))
    for sample in [rows[:1], [rows[0], deepcopy(rows[1])]]:
        if len(sample) == 2:
            box = json.loads(sample[1][29]); box[10] += 1; sample[1][29] = json.dumps(box)
        output = io.StringIO(); csv.writer(output).writerows(sample)
        result = parse_rebounding_inputs(output.getvalue(), 2027, LOCK, {'away': {}, 'home': {}})
        assert all(p['d1_games'] == 0 for p in result['teams'].values())


def test_duplicate_source_identity_is_rejected():
    with pytest.raises(ValueError, match='identity'):
        parse_rebounding_inputs(paired_log()*2, 2027, LOCK, {'away': {}, 'home': {}})


@pytest.mark.parametrize('bad', ['box_date', 'tie', 'reciprocal_points'])
def test_wrong_date_tied_or_conflicting_final_boxes_are_unavailable(bad):
    rows = list(csv.reader(io.StringIO(paired_log())))
    if bad == 'box_date':
        for row in rows:
            box = json.loads(row[29]); box[0] = '11/14/26'; row[29] = json.dumps(box)
    elif bad == 'tie':
        for row in rows:
            box = json.loads(row[29]); box[33] = 80; row[29] = json.dumps(box); row[6] = 'L, 80-80'
    else:
        box = json.loads(rows[1][29]); box[33] = 71; rows[1][29] = json.dumps(box); rows[1][6] = 'L, 80-71'
    out = io.StringIO(); csv.writer(out).writerows(rows)
    result = parse_rebounding_inputs(out.getvalue(), 2027, LOCK, {'away': {}, 'home': {}})
    assert all(p['d1_games'] == 0 for p in result['teams'].values())


@pytest.mark.parametrize('offense', [75., 105., 125.])
def test_archived_v11_ten_pp_edge_transfers_two_points_for_every_offense_type(offense):
    a, h = ratings(offense=offense)
    old, _ = forecast(away=a, home=h, model_version=PRE_REBOUND_MODEL_VERSION)
    new, _ = forecast(away=a, home=h, model_version=CAPPED_REBOUND_MODEL_VERSION)
    reb = new['matchup_projection']['rebounding_adjustment']
    assert reb['home_edge_pp'] == pytest.approx(10.)
    assert reb['home_points'] == pytest.approx(2.)
    assert reb['away_points'] == pytest.approx(-2.)
    assert new['total'] == old['total']
    assert new['matchup_projection']['home_points']-old['matchup_projection']['home_points'] == pytest.approx(2.)
    assert new['matchup_projection']['away_points']-old['matchup_projection']['away_points'] == pytest.approx(-2.)
    assert old['fair_home_spread']-new['fair_home_spread'] == pytest.approx(4.)


@pytest.mark.parametrize('gp,expected', [(1, 1.), (2, 2.), (3, 3.), (8, 3.)])
def test_cap_is_three_points_per_team_and_small_samples_attenuate_the_cap(gp, expected):
    a, h = ratings(gp=gp, extreme=True)
    result = matchup_transfer(a, h, 2027, LOCK)
    assert result['home_points'] == pytest.approx(expected)
    assert result['away_points'] == pytest.approx(-expected)
    assert result['home_points']+result['away_points'] == 0.


def test_swapping_neutral_teams_reverses_the_transfer_without_changing_total():
    a, h = ratings()
    first, _ = forecast(away=a, home=h)
    second, _ = forecast(away=h, home=a)
    assert first['total'] == second['total']
    assert first['fair_home_spread'] == pytest.approx(-second['fair_home_spread'])


@pytest.mark.parametrize('bad', ['missing', 'future', 'stale', 'season', 'same_day', 'zero_games', 'identity', 'nan'])
def test_unavailable_or_invalid_inputs_are_neutral_without_blocking_the_score(bad):
    a, h = ratings(); p = h['rebounding']
    if bad == 'missing': del h['rebounding']
    elif bad == 'future': p['observed_at'] = '2026-11-14T12:01:00+00:00'
    elif bad == 'stale': p.update(observed_at='2026-11-12T11:00:00+00:00', cutoff_date='2026-11-12', last_game_date='2026-11-11')
    elif bad == 'season': p['season'] = 2026
    elif bad == 'same_day': p['last_game_date'] = '2026-11-14'
    elif bad == 'zero_games': p['d1_games'] = 0
    elif bad == 'identity': p['team'] = 'Another School'
    elif bad == 'nan': p['orb'] = float('nan')
    old, _ = forecast(away=a, home=h, model_version=PRE_REBOUND_MODEL_VERSION)
    new, _ = forecast(away=a, home=h)
    assert new['total'] == old['total'] and new['fair_home_spread'] == old['fair_home_spread']
    assert new['matchup_projection']['rebounding_adjustment']['status'] == 'unavailable'


def test_existing_home_court_players_and_offense_bonus_remain_single_applications():
    a, h = ratings(offense=125.)
    impact = {'away': {'offense_change_per_100': 2.}, 'home': {'defense_improvement_per_100': 1.}}
    old, _ = forecast(away=a, home=h, impact=impact, court=3.2, model_version=PRE_REBOUND_MODEL_VERSION)
    new, _ = forecast(away=a, home=h, impact=impact, court=3.2)
    assert new['total'] == old['total']
    for side in ['away', 'home']:
        before = old['matchup_projection']['adjustments'][side]; after = new['matchup_projection']['adjustments'][side]
        for field in ['offense_bonus', 'player_points', 'home_court_points']:
            assert before[field] == after[field]


def test_total_grades_and_edges_are_identical_even_at_exact_thresholds():
    a, h = ratings()
    for edge in [-10., -8., 0., 7.999999999, 8., 9.999999999, 10.]:
        old, b = forecast(away=a, home=h, model_version=PRE_REBOUND_MODEL_VERSION)
        new, _ = forecast(away=a, home=h)
        old['market']['total'] = new['market']['total'] = old['total']-edge
        before, after = grades(old, b)['total'], grades(new, b)['total']
        for field in ['edge', 'grade', 'projected_side', 'projected_total', 'line']:
            assert before[field] == after[field]


def test_rehashed_transfers_and_changed_input_profiles_are_rejected_by_grading():
    a, h = ratings(); f, b = forecast(away=a, home=h)
    for kind in ['transfer', 'profile']:
        bad = deepcopy(f); p = bad['matchup_projection']
        if kind == 'transfer':
            p['rebounding_adjustment']['home_points'] += 1.
            p['rebounding_adjustment']['away_points'] -= 1.
        else: bad['home_rating']['rebounding']['orb'] += 1
        p['sha256'] = policy_digest({k: v for k, v in p.items() if k != 'sha256'})
        with pytest.raises(ValueError): capture_grades(bad, [b], published_at=LOCK)


def test_old_v10_scores_proofs_and_replay_remain_unchanged():
    from research.replay_ncaam_point_model import replay
    a, h = ratings(); old, b = forecast(away=a, home=h, model_version=PRE_REBOUND_MODEL_VERSION)
    assert 'rebounding_adjustment' not in old['matchup_projection']
    g = grades(old, b); old.update(matchup_total=g['total'], matchup_spread=g['spread'])
    result = replay({'model_version': PRE_REBOUND_MODEL_VERSION, 'games': [old]})
    assert result['errors'] == []
    assert result['games'][0]['matchup_projection'] == old['matchup_projection']


def test_builder_preparation_retains_dated_profiles_without_applying_a_live_rebound_bonus():
    from builders.ncaam_game_counts import parse_game_counts
    from builders.ncaam_model import prepare_season_ratings
    from builders.ncaam_matchup_projection import project
    from research.ncaam_test_support import complete_population
    a, h = ratings()
    for team in [a, h]: del team['rebounding']; team['record'] = '3-0'
    population = complete_population({'away': a, 'home': h})
    for team in population.values(): team.setdefault('record', '0-0')
    current = {'season': 2027, 'observed_at': '2026-11-14T11:00:00+00:00',
        'teams': population, 'source': 'Synthetic dated ratings', 'source_url': 'https://barttorvik.com/2027_team_results.csv'}
    prior = {**deepcopy(current), 'season': 2026}
    logs = ''.join(paired_log(f'11/{day}/26', str(day)) for day in [10, 11, 12])
    counts = parse_game_counts(logs, 2027, current['observed_at'], population)
    bundle = prepare_season_ratings(current, prior, LOCK, d1_games=counts)
    game = {'away': 'Away', 'home': 'Home', 'away_aliases': ['Away'], 'home_aliases': ['Home'],
        'season': 2027, 'tipoff': '2026-11-14T23:00:00+00:00', 'neutral_site': True}
    f = project(game, bundle, {'home_spread': 0., 'total': 150., 'as_of': LOCK}, LOCK)
    assert 'rebounding_adjustment' not in f['matchup_projection']
    assert f['away_rating']['rebounding']['d1_games'] == 3
    assert current['teams']['away'].get('rebounding') is None


def test_frozen_real_games_keep_totals_and_the_tarleton_direction_honest():
    path = Path(__file__).parent/'fixtures/ncaam_rebounding_examples_2026_11_16.json'
    data = json.loads(path.read_text())
    for game in data['games']:
        kwargs = {k: game[k] for k in ['as_of', 'tipoff', 'source_url', 'home_court_points', 'neutral_site', 'lineup_adjustment']}
        old = from_captured_inputs(game['away'], game['home'], game['benchmark'], model_version=PRE_REBOUND_MODEL_VERSION, **kwargs)
        new = from_captured_inputs(game['away'], game['home'], game['benchmark'],
            model_version=PERCENTAGE_REBOUND_MODEL_VERSION, **kwargs)
        assert new['total'] == old['total']
        assert new['rebounding_adjustment']['status'] == game['expected_status']
        archived = from_captured_inputs(game['away'], game['home'], game['benchmark'],
            model_version=CAPPED_REBOUND_MODEL_VERSION, **kwargs)
        assert all(archived[k] == value for k, value in game['legacy_v11_expected'].items())
        if game['game_id'] == '401927364':
            assert new['home_points'] > old['home_points']  # Drake
            assert new['away_points'] < old['away_points']  # Tarleton


def test_archived_rebound_forecast_replays_twice_without_applying_the_transfer_twice():
    from research.replay_ncaam_point_model import replay
    a, h = ratings(); f, b = forecast(away=a, home=h)
    g = grades(f, b); f.update(matchup_total=g['total'], matchup_spread=g['spread'])
    first = replay({'model_version': PERCENTAGE_REBOUND_MODEL_VERSION, 'games': [f]})
    second = replay(first)
    assert first['errors'] == second['errors'] == []
    assert first['games'][0]['matchup_projection'] == f['matchup_projection']
    assert second['games'][0]['matchup_projection'] == f['matchup_projection']
