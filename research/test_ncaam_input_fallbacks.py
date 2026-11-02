from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv
import io
import json

import pytest

from builders.ncaam_game_counts import parse_game_counts
from builders.ncaam_model import prepare_season_ratings, team_key
from builders.ncaam_player_impact import POLICY, parse_player_bundle, lineup_adjustments
from builders.ncaam_rosters import parse_roster, valid_roster
from scripts import run_ncaam_builder as runner
from test_ncaam_lineups import rotation_inputs, NOW, URL

FIXTURE = Path(__file__).parent / 'fixtures'


def test_real_sparse_export_retains_player_identity_and_minutes_without_inventing_bpm():
    text = (FIXTURE/'torvik_2027_sparse_players.csv').read_text()
    with pytest.raises(ValueError, match='incomplete'):
        parse_player_bundle(text, 2027, NOW.isoformat())
    parsed = parse_player_bundle(text, 2027, NOW.isoformat(), allow_partial=True)
    assert len(parsed['players']) == 19
    assert all(p['offense'] is None and p['defense'] is None for p in parsed['players'])
    assert all(p['games'] == 1 and p['id'].startswith('torvik:') for p in parsed['players'])
    assert sum(p['weight'] for p in parsed['players'] if p['team']=='Notre Dame') == pytest.approx(200, abs=.1)


@pytest.mark.parametrize('failure', ['sparse', 'unavailable'])
def test_current_player_failure_does_not_block_completed_season_loading(monkeypatch, failure):
    _, _, inputs = rotation_inputs()
    ratings = {'season':2027,'observed_at':NOW.isoformat(),'source_url':URL,
               'teams':{'away':{'team':'Away','record':'0-0','offense':110,'defense':100,'tempo':68}}}
    previous = {**deepcopy(ratings),'season':2026}
    calls = []
    def cached(_db, dataset, key, url, parser, season, now, commit, *args, **kwargs):
        calls.append((dataset, key))
        if dataset == runner.store.RATINGS: return ratings
        if dataset == 'ncaam_prior_rating_inputs': return previous
        if dataset == 'ncaam_d1_game_counts': return {'season':2027,'observed_at':NOW.isoformat(),'counts':{'away':0}}
        if key == 'season-2026':
            return {'season':2026,'observed_at':NOW.isoformat(),'source_url':URL,'players':inputs['players'],'policy':POLICY}
        if failure == 'unavailable': raise ValueError('current source unavailable')
        return parser((FIXTURE/'torvik_2027_sparse_players.csv').read_text(), season, now.isoformat())
    monkeypatch.setattr(runner, 'cached_input', cached)
    teams, players = runner.load_model_inputs(None, '2026-11-01', 2027, NOW, False)
    assert len(players['prior_players']) == 12
    assert len(players['players']) == (19 if failure == 'sparse' else 0)
    assert players['profile_mode'] == ('current_with_prior_fallback' if failure == 'sparse' else 'prior_fallback')
    assert teams['teams']['away']['offense'] == 110
    assert calls.index(('ncaam_player_profiles','season-2026')) < calls.index(('ncaam_player_profiles','2026-11-01'))


def test_prior_only_profiles_can_price_an_out_player_on_a_verified_current_roster():
    game, lineup, bundle = rotation_inputs()
    bundle.update(prior_players=bundle['players'], players=[], prior_observed_at=NOW.isoformat(), profile_mode='prior_fallback')
    healthy = lineup_adjustments(game, lineup, bundle, NOW)
    assert healthy['away']['applied'] and healthy['away']['offense_change_per_100'] == 0
    lineup['away']['starters'][0]['status'] = 'OUT'
    changed = lineup_adjustments(game, lineup, bundle, NOW)
    assert changed['away']['offense_change_per_100'] == pytest.approx(-8)
    assert sum(p['minutes'] for p in changed['away']['players']) == pytest.approx(200)
    assert all(p['profileSeason'] == 2026 for p in changed['away']['players'])


def test_transfer_rating_follows_unique_player_to_verified_new_team():
    game, lineup, bundle = rotation_inputs()
    bundle.update(prior_players=deepcopy(bundle['players']), players=[], prior_observed_at=NOW.isoformat(), profile_mode='prior_fallback')
    bundle['prior_players'][0]['team'] = 'Former College'
    lineup['away']['starters'][0]['status'] = 'OUT'
    result = lineup_adjustments(game, lineup, bundle, NOW)
    assert result['away']['applied']
    assert result['away']['offense_change_per_100'] == pytest.approx(-8)
    assert len([p for p in result['away']['players'] if p['ratingId']=='Away0']) == 1


def test_missing_or_wrong_current_roster_keeps_source_baseline():
    game, lineup, bundle = rotation_inputs()
    lineup['away']['starters'][0]['status'] = 'OUT'
    bundle['rosters'][game['away_id']]['season'] = 2026
    result = lineup_adjustments(game, lineup, bundle, NOW)
    assert not result['away']['applied'] and result['away']['offense_change_per_100'] == 0
    assert 'membership' in result['away']['flags'][0]


def test_unrated_replacement_minutes_do_not_become_a_zero_impact_estimate():
    game, lineup, bundle = rotation_inputs()
    bundle['players'][-7]['offense'] = None
    bundle['players'][-7]['defense'] = None
    lineup['away']['starters'][0]['status'] = 'OUT'
    result = lineup_adjustments(game, lineup, bundle, NOW)
    assert not result['away']['applied']
    assert result['away']['offense_change_per_100'] == 0
    assert any('unrated' in flag for flag in result['away']['flags'])


def test_prior_fallback_cache_can_be_older_than_current_stats_but_not_future_dated():
    game, lineup, bundle = rotation_inputs()
    prior_time=(NOW-timedelta(days=5)).isoformat()
    bundle.update(prior_players=bundle['players'], players=[], prior_observed_at=prior_time,
                  observed_at=prior_time, profile_mode='prior_fallback')
    assert lineup_adjustments(game, lineup, bundle, NOW)['away']['applied']
    bundle['prior_observed_at']=(NOW+timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match='future'):
        lineup_adjustments(game, lineup, bundle, NOW)


def game_log(team, opponent, game_id, day='10/30/26', season='2027'):
    row=['']*31
    row[0],row[2],row[4],row[6],row[22],row[24]=day,team,opponent,'W, 80-70',season,game_id
    out=io.StringIO();csv.writer(out).writerow(row);return out.getvalue()


def test_d1_count_excludes_non_members_same_day_and_future_results():
    text=(game_log('Away','Home','d1')+game_log('Away','Non-D1 College','lower')
          +game_log('Home','Away','today','11/01/26')+game_log('Home','Away','future','11/02/26'))
    result=parse_game_counts(text,2027,NOW.isoformat(),{'away':{},'home':{}})
    assert result['counts']=={'away':1,'home':0}
    current={'season':2027,'observed_at':NOW.isoformat(),'source_url':URL,'teams':{
        'away':{'team':'Away','record':'10-0','offense':112,'defense':100,'tempo':69}}}
    previous={**deepcopy(current),'season':2026}
    team=prepare_season_ratings(current,previous,NOW.isoformat(),d1_games=result)['teams']['away']
    assert team['d1_games_played']==1 and team['current_weight']==1 and team['offense']==112
    assert current['teams']['away']['record']=='10-0'
    with pytest.raises(ValueError): parse_game_counts(game_log('Away','Home','d1',season='2026'),2027,NOW.isoformat(),current['teams'])
    with pytest.raises(ValueError): parse_game_counts(game_log('Away','Home','d1')*2,2027,NOW.isoformat(),current['teams'])


def test_roster_schema_requires_matching_season_team_and_unique_players():
    payload=json.loads((FIXTURE/'espn_ncaam_roster_87_2027.json').read_text())
    roster=parse_roster(payload,'87',2027,NOW.isoformat())
    assert len(roster['players'])==16 and valid_roster(roster,'87',2027,NOW)
    assert not valid_roster(roster,'222',2027,NOW)
    assert not valid_roster(roster,'87',2027,NOW+timedelta(days=3))
    with pytest.raises(ValueError): parse_roster(payload,'87',2026,NOW.isoformat())
    payload['athletes'].append(payload['athletes'][0])
    with pytest.raises(ValueError): parse_roster(payload,'87',2027,NOW.isoformat())


def test_fresh_cached_rosters_avoid_provider_calls_and_evaluate_mode_never_writes(monkeypatch):
    game,lineup,bundle=rotation_inputs()
    monkeypatch.setattr(runner.store,'rows_for_keys',lambda *_a:[{'Details JSON':json.dumps(r)} for r in bundle['rosters'].values()])
    def no_call(*_a,**_kw): raise AssertionError('Fresh roster cache should be reused')
    monkeypatch.setattr(runner,'get',no_call)
    monkeypatch.setattr(runner.store,'upsert',no_call)
    assert set(runner.load_rosters(None,[game],{game['game_id']:lineup},2027,NOW,False))=={game['away_id'],game['home_id']}


def test_legacy_player_policy_cannot_mask_new_sparse_input_handling(monkeypatch):
    monkeypatch.setattr(runner.store,'rows',lambda *_a:[{'Details JSON':json.dumps({'season':2027,'observed_at':NOW.isoformat(),'policy':'old'})}])
    calls=[]
    monkeypatch.setattr(runner,'get',lambda *_a,**_kw:(calls.append(True) or ('source',NOW)))
    result=runner.cached_input(None,'players','day',URL,lambda *_a:{'season':2027,'policy':POLICY,'observed_at':NOW.isoformat()},2027,NOW,False,expected_policy=POLICY)
    assert result['policy']==POLICY and len(calls)==1


def test_roster_outage_cooldown_survives_cold_runs_without_claiming_freshness(monkeypatch):
    game,lineup,_=rotation_inputs()
    saved,requests=[],[]
    class Clock(datetime):
        tick=NOW
        @classmethod
        def now(cls,tz=None): return cls.tick.astimezone(tz) if tz else cls.tick.replace(tzinfo=None)
    monkeypatch.setattr(runner,'datetime',Clock)
    monkeypatch.setattr(runner.store,'rows_for_keys',lambda *_a:saved)
    def fail(*_a,**_kw):
        requests.append(True)
        raise ValueError('Roster provider unavailable')
    monkeypatch.setattr(runner,'get',fail)
    monkeypatch.setattr(runner.store,'upsert',lambda _db,_dataset,row:saved.append(row))
    assert runner.load_rosters(None,[game],{game['game_id']:lineup},2027,NOW,True)=={}
    assert len(requests)==2 and len(saved)==2
    assert all(not row['Updated At'] and json.loads(row['Details JSON']) is None for row in saved)
    Clock.tick=NOW+timedelta(minutes=5)
    fresh={**lineup,'observedAt':Clock.tick.isoformat()}
    assert runner.load_rosters(None,[game],{game['game_id']:fresh},2027,Clock.tick,True)=={}
    assert len(requests)==2
    Clock.tick=NOW+timedelta(minutes=65)
    fresh={**lineup,'observedAt':Clock.tick.isoformat()}
    assert runner.load_rosters(None,[game],{game['game_id']:fresh},2027,Clock.tick,True)=={}
    assert len(requests)==4
