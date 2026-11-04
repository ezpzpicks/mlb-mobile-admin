from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
import io
import json

import pytest

from builders.ncaam_home_court import (
    POLICY, bundled_history, fit_home_courts, home_court_for, parse_game_history, school_key,
)
from builders.ncaam_model import project
from scripts import run_ncaam_builder as runner

NOW = datetime(2026, 11, 3, 20, tzinfo=timezone.utc)


def log(team, opponent, site, final, game_id='one', day='11/01/26'):
    row = [''] * 31
    row[0], row[2], row[4], row[5], row[6], row[22], row[24] = day, team, opponent, site, final, '2027', game_id
    out = io.StringIO()
    csv.writer(out).writerow(row)
    return out.getvalue()


def synthetic_games():
    strength = {'strong': 20, 'weak': -15, 'other': 0, 'fourth': -5}
    courts = {'strong': 1, 'weak': 6, 'other': 3, 'fourth': 3}
    return [{'game_id': f'{season}|{home}|{away}|{repeat}', 'season': season, 'date': f'{season}-02-01',
             'home_team': home, 'away_team': away, 'neutral_site': False,
             'margin': strength[home] - strength[away] + courts[home]}
            for season in (2024, 2025, 2026) for home in strength for away in strength if home != away
            for repeat in range(30)]


@pytest.fixture(scope='module')
def court_model():
    return fit_home_courts(synthetic_games(), 2027, '2026-11-03', NOW.isoformat())


def test_school_effect_is_separate_from_strength_and_opponent_quality(court_model):
    # The stronger team deliberately has the weaker home court. A raw average
    # home winning margin would rank these schools in the opposite order.
    assert court_model['teams']['weak']['value'] > court_model['teams']['strong']['value'] + 2
    assert court_model['teams']['strong']['value'] < court_model['league_points']
    assert court_model['teams']['weak']['value'] > court_model['league_points']
    assert court_model['teams']['weak']['actual_home_games'] == 270


def test_small_home_samples_are_shrunk_more_than_large_samples():
    rows = synthetic_games()
    small = fit_home_courts([r for r in rows if not r['game_id'].endswith(tuple(f'|{n}' for n in range(2, 30)))],
                           2027, '2026-11-03', NOW.isoformat())
    # Retain sufficient balanced background games while reducing only Weak's
    # observed home samples. The league fallback must not invent its venue edge.
    reduced = [r for r in rows if r['home_team'] != 'weak' or r['game_id'].endswith(('|0', '|1'))]
    result = fit_home_courts(reduced, 2027, '2026-11-03', NOW.isoformat())
    full = fit_home_courts(rows, 2027, '2026-11-03', NOW.isoformat())
    assert small['fallback']
    assert result['teams']['weak']['sample_support'] < full['teams']['weak']['sample_support']
    assert abs(result['teams']['weak']['venue_adjustment']) < abs(full['teams']['weak']['venue_adjustment'])


def test_neutral_and_unknown_school_values_are_explicit(court_model):
    neutral = home_court_for(court_model, 'weak', season=2027, game_date='2026-11-03', neutral_site=True)
    assert neutral['value'] == 0 and neutral['venue_adjustment'] == 0 and neutral['actual_home_games'] == 0
    missing = home_court_for(court_model, 'new school', season=2027, game_date='2026-11-03', neutral_site=False)
    assert missing['value'] == court_model['league_points']
    assert missing['actual_home_games'] == 0 and missing['fallback']


def test_same_day_future_and_neutral_games_cannot_create_home_samples(court_model):
    rows = synthetic_games()
    future = {**rows[0], 'game_id': 'future', 'season': 2027, 'date': '2026-11-03', 'margin': 100}
    result = fit_home_courts([*rows, future], 2027, '2026-11-03', NOW.isoformat())
    assert result == court_model
    neutral = {**rows[0], 'game_id': 'neutral', 'neutral_site': True, 'margin': 35}
    with_neutral = fit_home_courts([*rows, neutral], 2027, '2026-11-03', NOW.isoformat())
    assert with_neutral['teams']['strong']['actual_home_games'] == court_model['teams']['strong']['actual_home_games']
    with pytest.raises(ValueError, match='duplicated'):
        fit_home_courts([*rows, rows[0]], 2027, '2026-11-03', NOW.isoformat())
    with pytest.raises(ValueError, match='later'):
        fit_home_courts(rows, 2027, '2026-11-04', NOW.isoformat())
    wrong = {**court_model, 'cutoff_date': '2026-11-04'}
    with pytest.raises(ValueError):
        home_court_for(wrong, 'weak', season=2027, game_date='2026-11-03', neutral_site=False)


def test_parser_requires_reciprocal_identity_scores_and_venue():
    home = log('Home', 'Away', 'H', 'W, 80-70')
    away = log('Away', 'Home', 'A', 'L, 80-70')
    games, audit = parse_game_history(home + away, 2027, '2026-11-03', {'home', 'away'})
    assert len(games) == 1 and games[0]['margin'] == 10 and not games[0]['neutral_site']
    assert parse_game_history(home, 2027, '2026-11-03')[0] == []
    assert parse_game_history(home + away + away, 2027, '2026-11-03')[0] == []
    assert parse_game_history(home + away.replace('80-70', '80-71'), 2027, '2026-11-03')[0] == []
    assert parse_game_history(home + log('Away', 'Home', 'N', 'L, 80-70'), 2027, '2026-11-03')[0] == []
    assert parse_game_history(home + away, 2027, '2026-11-01')[0] == []
    assert parse_game_history(home + away, 2027, '2026-11-03', {'home'})[0] == []
    neutral = home.replace(',H,', ',N,') + away.replace(',A,', ',N,')
    assert parse_game_history(neutral, 2027, '2026-11-03')[0][0]['neutral_site']


def test_exact_legacy_aliases_preserve_school_identity():
    assert school_key('IUPUI') == school_key('IU Indy')
    assert school_key('Texas A&M Commerce') == school_key('East Texas A&M')
    assert school_key('College of Charleston') == school_key('Charleston')
    assert school_key('Charleston Southern') != school_key('Charleston')


def test_projection_applies_different_team_values_and_preserves_totals(court_model):
    bundle = {'season': 2027, 'observed_at': NOW.isoformat(), 'source': 'fixture', 'source_url': 'fixture',
              'league_efficiency': 105, 'teams': {'away': {'team': 'Away', 'offense': 110, 'defense': 100, 'tempo': 70},
                                                'weak': {'team': 'weak', 'offense': 110, 'defense': 100, 'tempo': 70}}}
    game = {'away': 'Away', 'home': 'weak', 'away_aliases': ['Away'], 'home_aliases': ['weak'],
            'season': 2027, 'neutral_site': False, 'tipoff': (NOW + timedelta(hours=1)).isoformat()}
    market = {'home_spread': -3.5, 'total': 145.5, 'as_of': NOW.isoformat()}
    ctx = home_court_for(court_model, 'weak', season=2027, game_date='2026-11-03', neutral_site=False)
    baseline = project(game, bundle, market, NOW.isoformat())
    fitted = project(game, bundle, market, NOW.isoformat(), home_court=ctx)
    assert fitted['home_court_points'] == ctx['value'] != baseline['home_court_points']
    assert fitted['total'] == baseline['total'] and fitted['lineup_margin_change'] == 0
    assert fitted['spread_grade'] == 'Research' and 'spread_probability_estimate' not in fitted
    assert fitted['fair_home_spread'] < baseline['fair_home_spread']
    later = NOW + timedelta(days=20)
    later_game = {**game, 'tipoff':(later+timedelta(hours=1)).isoformat()}
    after_opening = project(later_game,{**bundle,'observed_at':later.isoformat()},
                            {**market,'as_of':later.isoformat()},later.isoformat(),home_court=ctx)
    assert not after_opening['margin_calibration']['active']
    assert after_opening['home_court_points'] == ctx['value']
    for patch in [{'team_key': 'other'}, {'season': 2026}, {'observed_at': (NOW + timedelta(seconds=1)).isoformat()},
                  {'value': float('nan')}, {'cutoff_date': '2026-11-04'}]:
        with pytest.raises(ValueError):
            project(game, bundle, market, NOW.isoformat(), home_court={**ctx, **patch})


def test_cache_reuses_evidence_and_evaluate_never_writes(monkeypatch, court_model):
    history = {'games': synthetic_games(), 'sha256': 'fixture', 'sources':[{'season':y} for y in (2024,2025,2026)]}
    monkeypatch.setattr(runner, 'bundled_history', lambda: history)
    saved = []
    monkeypatch.setattr(runner.store, 'rows', lambda *_a: saved)
    monkeypatch.setattr(runner.store, 'upsert', lambda _db, _dataset, row: saved.append(row))
    model = runner.load_home_court_model(object(), '2026-11-03', 2027, None, NOW, False)
    assert not saved
    runner.load_home_court_model(object(), '2026-11-03', 2027, None, NOW, True)
    assert len(saved) == 1
    monkeypatch.setattr(runner, 'fit_home_courts', lambda *_a: (_ for _ in ()).throw(AssertionError('Unchanged evidence must reuse its fit')))
    again = runner.load_home_court_model(object(), '2026-11-03', 2027, None, NOW + timedelta(minutes=5), True)
    assert again['input_digest'] == model['input_digest'] and len(saved) == 1


def test_changing_season_strengths_do_not_become_venue_value():
    rows = synthetic_games()
    # Reverse the two teams' quality in one season; their home advantages stay
    # fixed. Each season must have its own opponent-strength nuisance controls.
    reversed_rows = deepcopy(rows)
    deltas = {'strong':-35,'weak':35,'other':0,'fourth':0}
    for row in reversed_rows:
        if row['season'] == 2025:
            row['margin'] += deltas[row['home_team']] - deltas[row['away_team']]
    original = fit_home_courts(rows,2027,'2026-11-03',NOW.isoformat())
    changed = fit_home_courts(reversed_rows,2027,'2026-11-03',NOW.isoformat())
    assert changed['teams']['weak']['value'] > changed['teams']['strong']['value']
    assert changed['teams']['weak']['value'] == pytest.approx(original['teams']['weak']['value'],abs=.1)


def test_prior_snapshot_is_complete_and_keeps_only_past_seasons():
    data = bundled_history()
    assert len(data['games']) > 28000
    assert len({g['game_id'] for g in data['games']}) == len(data['games'])
    assert {g['season'] for g in data['games']} == set(range(2022, 2027))
    assert max(g['date'] for g in data['games']) < '2026-07-01'


def test_missing_completed_season_requires_an_explicit_snapshot_update(monkeypatch):
    monkeypatch.setattr(runner,'bundled_history',lambda:{'sources':[{'season':2025},{'season':2026}],
                                                     'games':synthetic_games(),'sha256':'incomplete'})
    with pytest.raises(ValueError,match='annual update'):
        runner.load_home_court_model(None,'2026-11-03',2027,None,NOW,False)
