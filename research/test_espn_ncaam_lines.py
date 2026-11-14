from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from builders.ncaam_model import MODEL_VERSION, team_key
from scripts import run_ncaam_builder as runner
from shared.espn_ncaam_lines import parse_espn_ncaam_lines, response_observed_at
from shared.ncaaf_auto_builder import MarketGame

NOW = datetime(2026, 11, 1, 6, tzinfo=timezone.utc)
FIXTURE = json.loads((Path(__file__).parent / 'fixtures/espn_ncaam_odds_2026_11_01.json').read_text())


def parse(payload=None, observed=NOW, games=None):
    payload = payload or deepcopy(FIXTURE)
    return parse_espn_ncaam_lines(payload, games or runner.parse_slate(payload, '2026-11-01'), NOW, observed)


def test_real_scoreboard_fills_missing_liberty_market_with_bookmaker_and_no_splits():
    markets, rejected = parse()
    assert not rejected
    assert markets['401920409']['home_spread'] == -6.5
    assert markets['401920409']['total'] == 155.5
    assert markets['401920982']['home_spread'] == -7.5
    for market in markets.values():
        assert market['source'] == 'ESPN' and market['provider'] == 'DraftKings'
        assert market['quote_kind'] == 'current_pregame' and market['odds'] == -110
        assert not any('bets' in key.lower() or 'money' in key.lower() for key in market)


@pytest.mark.parametrize('home,away', [('+6.5', '-6.5'), ('0', '0')])
def test_signed_home_quote_handles_road_favorites_and_pick_em(home, away):
    payload = deepcopy(FIXTURE)
    odds = payload['events'][1]['competitions'][0]['odds'][0]
    odds['pointSpread']['home']['close']['line'] = home
    odds['pointSpread']['away']['close']['line'] = away
    assert parse(payload)[0]['401920409']['home_spread'] == float(home)


@pytest.mark.parametrize('change', ['opening_only', 'inconsistent_spread', 'inconsistent_total', 'wrong_team',
                                  'missing_provider', 'moneyline_only', 'one_market_only', 'nan'])
def test_bad_or_partial_quote_cannot_replace_current_lines(change):
    payload = deepcopy(FIXTURE)
    comp = payload['events'][1]['competitions'][0]
    odds = comp['odds'][0]
    if change == 'opening_only':
        for market in ['pointSpread', 'total']:
            for side in odds[market].values():
                side.pop('close', None)
    elif change == 'inconsistent_spread':
        odds['pointSpread']['away']['close']['line'] = '+7.5'
    elif change == 'inconsistent_total':
        odds['total']['under']['close']['line'] = 'u156.5'
    elif change == 'wrong_team':
        odds['homeTeamOdds']['team']['id'] = '999'
    elif change == 'missing_provider':
        odds.pop('provider')
    elif change == 'moneyline_only':
        comp['odds'] = [{'provider': odds['provider'], 'moneyline': {'home': '-300'}}]
    elif change == 'one_market_only':
        odds.pop('total')
    else:
        odds['overUnder'] = 'nan'
    markets, rejected = parse(payload)
    assert set(markets) == {'401920982'}
    assert len(rejected) == 1


def test_partial_books_cannot_be_combined_and_conflicting_same_book_is_rejected():
    payload = deepcopy(FIXTURE)
    comp = payload['events'][1]['competitions'][0]
    original = comp['odds'][0]
    other = deepcopy(original)
    original['total'].pop('under')
    other['provider']['id'] = '101'
    other['pointSpread'].pop('home')
    comp['odds'].append(other)
    assert '401920409' not in parse(payload)[0]
    payload = deepcopy(FIXTURE)
    comp = payload['events'][1]['competitions'][0]
    other = deepcopy(comp['odds'][0])
    other['pointSpread']['home']['close']['line'] = '-5.5'
    other['pointSpread']['away']['close']['line'] = '+5.5'
    comp['odds'].append(other)
    assert '401920409' not in parse(payload)[0]


def test_legacy_summary_requires_verified_favorite_orientation():
    payload = deepcopy(FIXTURE)
    odds = payload['events'][1]['competitions'][0]['odds'][0]
    odds.pop('pointSpread'); odds.pop('total')
    odds['homeTeamOdds']['favorite'], odds['awayTeamOdds']['favorite'] = False, True
    assert parse(payload)[0]['401920409']['home_spread'] == 6.5
    odds['homeTeamOdds']['favorite'] = True
    assert '401920409' not in parse(payload)[0]


def test_stale_future_started_unverified_and_conflicting_event_ids_are_excluded():
    for observed in [NOW - timedelta(minutes=91), NOW + timedelta(seconds=1)]:
        with pytest.raises(ValueError, match='stale|future'):
            parse(observed=observed)
    payload = deepcopy(FIXTURE)
    games = runner.parse_slate(payload, '2026-11-01')
    for status in ['STATUS_FINAL', 'STATUS_IN_PROGRESS', 'UNKNOWN_TIME']:
        assert parse(payload, games=[{**game, 'status': status} for game in games])[0] == {}
    payload['events'].append(deepcopy(payload['events'][1]))
    assert '401920409' not in parse_espn_ncaam_lines(payload, games, NOW, NOW)[0]


def test_cache_age_and_server_clock_are_preserved():
    observed = response_observed_at({'Age': '5401', 'Date': format_datetime(NOW)}, NOW)
    with pytest.raises(ValueError, match='stale'):
        parse(observed=observed)
    old = NOW - timedelta(minutes=10)
    assert response_observed_at({'Date': format_datetime(old)}, NOW) == old
    for value in ['-1', 'nan', 'inf']:
        with pytest.raises(ValueError):
            response_observed_at({'Age': value}, NOW)


@pytest.mark.parametrize('mode', ['espn', 'sao', 'covers'])
def test_runner_source_priority_reuses_one_request_and_saves_only_immutable_forecasts(monkeypatch, mode):
    from research.ncaam_test_support import atomic_grade_storage, complete_population
    payload = deepcopy(FIXTURE)
    if mode == 'covers':
        payload['events'][1]['competitions'][0]['odds'] = []
    games = runner.parse_slate(payload, '2026-11-01')
    bundle = {'season': 2027, 'observed_at': NOW.isoformat(), 'source': 'Test dated ratings', 'source_url': 'https://example.test',
              'league_efficiency': 105, 'teams': complete_population({team_key(game[side]): {'team': game[side], 'offense': 110, 'defense': 100, 'tempo': 68}
                                                 for game in games for side in ['away', 'home']})}
    saved, requests, covers = [], [], []
    class Clock(datetime):
        offset = timedelta()
        @classmethod
        def now(cls, tz=None): return (NOW+cls.offset).astimezone(tz) if tz else (NOW+cls.offset).replace(tzinfo=None)
    monkeypatch.setattr(runner, 'datetime', Clock)
    def get(url, *, with_observation=False):
        requests.append(url)
        assert with_observation and '/scoreboard?' in url
        return json.dumps(payload), NOW
    monkeypatch.setattr(runner, 'get', get)
    monkeypatch.setattr(runner, 'load_model_inputs', lambda *_a: (bundle, None))
    monkeypatch.setattr(runner, 'load_lineups', lambda *_a: {})
    monkeypatch.setenv('NCAAM_AUTO_BUILDER_ENABLED', '1')
    from shared import turso_storage, ncaaf_auto_builder, college_market_lines
    monkeypatch.setattr(turso_storage, 'is_turso_ready', lambda: True)
    monkeypatch.setattr(runner.store, 'rows', lambda _db, dataset, _day: saved if dataset == runner.store.FORECASTS else
                        [{'Details JSON': json.dumps(bundle)}] if dataset == runner.store.RATINGS else [])
    annotations = atomic_grade_storage(monkeypatch, runner, saved, Clock)
    def market(game, source):
        return MarketGame(game['date'], game['away'], game['home'], datetime.fromisoformat(game['tipoff']), NOW,
                          -4.5, 150, None, None, game['game_id'], source, 'https://example.test')
    monkeypatch.setattr(ncaaf_auto_builder, 'eligible_markets', lambda *_a: ([market(games[0], 'ScoresAndOdds')] if mode == 'sao' else [], []))
    def load_covers(*_a):
        covers.append(True)
        assert mode == 'covers', 'Complete ESPN quotes should avoid a Covers request'
        return [market(games[1], 'Covers')], []
    monkeypatch.setattr(college_market_lines, 'fetch_covers_markets', load_covers)
    args = SimpleNamespace(commit=True, evaluate=False, limit=100)
    assert runner.run(args) == 0
    forecasts = {json.loads(row['Details JSON'])['gameId']: json.loads(row['Details JSON']) for row in saved}
    assert len(forecasts) == 2 and len(requests) == 1
    assert len(annotations) == 4
    assert forecasts['401920982']['market']['source'] == ('ScoresAndOdds' if mode == 'sao' else 'ESPN')
    assert forecasts['401920409']['market']['source'] == ('Covers' if mode == 'covers' else 'ESPN')
    assert all(f['spread_grade'] == 'Research' and f['modelVersion'] == MODEL_VERSION for f in forecasts.values())
    assert bool(covers) == (mode == 'covers')
    assert runner.run(args) == 0 and len(saved) == 2
    # Material A -> B -> A revisions must restore A as the newest calculation,
    # while identical observations do not add a forecast or duplicate records.
    target = bundle['teams'][team_key(games[0]['home'])]
    target['offense'] = 115
    Clock.offset = timedelta(seconds=1)
    # The changed national population is part of both games' grade inputs,
    # so both retain new immutable captures even if one score is unchanged.
    assert runner.run(args) == 0 and len(saved) == 4
    target['offense'] = 110
    Clock.offset = timedelta(seconds=2)
    assert runner.run(args) == 0 and len(saved) == 6
    restored = next(json.loads(row['Details JSON']) for row in reversed(saved)
                    if json.loads(row['Details JSON'])['gameId'] == games[0]['game_id'])
    assert restored['home_score'] == forecasts[games[0]['game_id']]['home_score']
    assert restored['inputDigest'] == forecasts[games[0]['game_id']]['inputDigest']
    assert restored['recordId'] != forecasts[games[0]['game_id']]['recordId']
    assert runner.run(args) == 0 and len(saved) == 6
