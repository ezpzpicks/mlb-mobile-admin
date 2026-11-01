from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import re
import pytest
from builders.ncaam_lineups import parse_lineups, fingerprint, URL
from builders.ncaam_player_impact import parse_player_bundle, allocate_minutes, lineup_adjustments, POLICY
from builders.ncaam_model import blend_season_ratings, project
from scripts.run_ncaam_builder import parse_slate

NOW = datetime(2026, 11, 1, 6, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / 'fixtures'
HTML = (FIXTURE / 'ncaam_rotowire_2026_11_01.html').read_text()
GAMES = parse_slate(json.loads((FIXTURE / 'espn_ncaam_odds_2026_11_01.json').read_text()), '2026-11-01')


def test_real_rotowire_uses_card_time_dedupes_slots_and_never_confirms_college():
    lineups, _, rejected = parse_lineups(HTML, GAMES, NOW.isoformat())
    assert not rejected and len(lineups) == 1
    lineup = lineups[0]
    assert lineup['gameId'] == '401920982'
    assert len(lineup['away']['starters']) == 5 and lineup['away']['complete']
    assert len(lineup['home']['starters']) == 4 and not lineup['home']['complete']
    assert not lineup['away']['confirmed'] and not lineup['home']['confirmed']
    assert lineup['away']['starters'][-1]['name'] == 'Logan Duncomb'
    assert lineup['away']['starters'][-1]['status'] == 'QUESTIONABLE'
    assert [p['name'] for p in lineup['home']['injuries']] == ['Matthew Hodge', 'Nico Onyekwere']
    assert lineup['home']['starters'][-1]['position'] == 'PF/C'
    assert fingerprint(lineup) == fingerprint({**lineup, 'observedAt': '2026-11-01T06:05:00Z'})


@pytest.mark.parametrize('bad', ['SUN 11/01 9:31 AM ET', 'MON 11/02 9:30 AM ET', 'MON 11/01 9:30 AM ET'])
def test_wrong_card_tipoff_or_day_never_binds(bad):
    modified = re.sub(r'(<div class="lineup__time">).*?(</div>)', lambda m: m[1]+bad+m[2], HTML, count=1, flags=re.S)
    assert modified != HTML
    assert parse_lineups(modified, GAMES, NOW.isoformat())[0] == []


def test_duplicate_games_conflicting_statuses_and_cross_team_ids_are_rejected():
    assert parse_lineups(HTML + HTML, GAMES, NOW.isoformat())[0] == []
    assert parse_lineups(HTML.replace('>GTD<', '>OUT<'), GAMES, NOW.isoformat())[0] == []
    with pytest.raises(ValueError): parse_lineups(HTML, GAMES, NOW.isoformat(), 'https://other.test/lineups')


def test_profile_schema_and_tiny_sample_extremes():
    def row(pid=1, minutes=50, obpm=4, dbpm=2):
        values = ['']*67
        for index, value in {0:'Player One',1:'School',3:30,4:minutes,31:2027,32:pid,53:obpm+dbpm,55:obpm,56:dbpm,64:'Guard'}.items(): values[index] = str(value)
        return ','.join(values)
    bundle = parse_player_bundle(row()+'\n'+row(2, .1, 600, 100), 2027, NOW.isoformat(), minimum_players=1)
    assert len(bundle['players']) == 1 and bundle['excluded_small_samples'] == 1
    with pytest.raises(ValueError): parse_player_bundle('<html>verification</html>', 2027, NOW.isoformat(), minimum_players=1)
    with pytest.raises(ValueError): parse_player_bundle(row(), 2026, NOW.isoformat(), minimum_players=1)


def rotation_inputs():
    game = {**GAMES[0], 'away_aliases':['Away'], 'home_aliases':['Home'], 'away':'Away', 'home':'Home'}
    players = []
    for side in ['Away', 'Home']:
        for i in range(6): players.append({'id':side+str(i), 'name':side+' Player '+str(i), 'team':side, 'weight':30 if i else 50, 'offense':8 if i==0 else 0, 'defense':4 if i==0 else 0, 'games':30})
    lineup = {'sport':'NCAAM','gameId':game['game_id'],'date':game['date'],'tipoff':game['tipoff'],'observedAt':NOW.isoformat(),'sourceUrl':URL}
    for side in ['away','home']:
        lineup[side] = {'team':game[side], 'confirmed':False, 'complete':True, 'starters':[{'id':game[side]+str(i), 'name':game[side]+' Player '+str(i), 'status':'QUESTIONABLE' if i==0 else 'UNKNOWN'} for i in range(5)],'injuries':[]}
    bundle = {'season':2027,'observed_at':NOW.isoformat(),'policy':POLICY,'source':'Test','source_url':URL,'players':players}
    return game, lineup, bundle


def test_out_star_changes_offense_defense_and_replacements_without_moving_market():
    game, lineup, inputs = rotation_inputs()
    healthy = lineup_adjustments(game, lineup, inputs, NOW)
    assert healthy['away']['offense_change_per_100'] == 0
    lineup['away']['starters'][0]['status'] = 'OUT'
    injured = lineup_adjustments(game, lineup, inputs, NOW)
    assert injured['away']['offense_change_per_100'] == -8
    assert injured['away']['defense_improvement_per_100'] == -4
    players = injured['away']['players']
    assert sum(p['minutes'] for p in players) == pytest.approx(200)
    assert all(0 <= p['minutes'] <= 40 for p in players) and players[0]['minutes'] == 0
    ratings = {'season':2027,'observed_at':NOW.isoformat(),'source':'Test','source_url':URL,'league_efficiency':105,'teams':{s.lower():{'team':s,'offense':110,'defense':100,'tempo':68} for s in ['Away','Home']}}
    market = {'home_spread':-3,'total':145,'as_of':NOW.isoformat()}
    before = project(game, ratings, market, NOW.isoformat(), lineup_adjustment=healthy)
    after = project(game, ratings, market, NOW.isoformat(), lineup_adjustment=injured)
    assert after['away_score'] < before['away_score'] and after['home_score'] > before['home_score']
    assert after['market'] == before['market']


def test_questionable_in_base_case_out_scenario_and_stale_reports_unknown():
    game, lineup, inputs = rotation_inputs()
    result = lineup_adjustments(game, lineup, inputs, NOW)
    assert result['away']['offense_change_per_100'] == 0
    assert result['scenarios'][0]['away']['offense_change_per_100'] == -8
    lineup['observedAt'] = (NOW-timedelta(minutes=36)).isoformat()
    result = lineup_adjustments(game, lineup, inputs, NOW)
    assert not result['scenarios'] and 'unchecked' in result['away']['flags'][0]
    with pytest.raises(ValueError): lineup_adjustments(game, None, {**inputs,'observed_at':(NOW+timedelta(seconds=1)).isoformat()}, NOW)
    with pytest.raises(ValueError): allocate_minutes({'one':100,'two':10})


def test_completed_season_prior_opponent_adjustment_and_season_transition():
    current = {'season':2027,'observed_at':NOW.isoformat(),'source_url':URL,'teams':{'away':{'team':'Away','offense':100,'defense':110,'tempo':65,'record':'0-0'}}}
    prior = {**deepcopy(current),'season':2026}
    prior['teams']['away'].update(offense=120,defense=95,tempo=73,record='25-8')
    blended = blend_season_ratings(current, prior, NOW.isoformat())['teams']['away']
    assert (blended['offense'],blended['defense'],blended['tempo']) == (120,95,73)
    assert blended['prior_weight'] == 1 and blended['games_played'] == 0 and blended['opponent_adjusted']
    current['teams']['away']['record'] = '7-3'
    blended = blend_season_ratings(current, prior, NOW.isoformat())['teams']['away']
    assert blended['current_weight'] == .5 and blended['offense'] == 110
    assert current['teams']['away']['offense'] == 100
    with pytest.raises(ValueError): blend_season_ratings(current, {**prior,'season':2025}, NOW.isoformat())


def test_departure_changes_prior_rotation_even_without_injury_report():
    game, _, inputs = rotation_inputs()
    old = deepcopy(inputs['players'])
    inputs['players'] = [p for p in inputs['players'] if p['id'] != 'Away0']
    inputs.update(prior_players=old, prior_observed_at=NOW.isoformat(), team_current_weights={'away':0,'home':0})
    result = lineup_adjustments(game, None, inputs, NOW)
    assert result['away']['offense_change_per_100'] == -8
    assert sum(p['referenceMinutes'] for p in result['away']['players']) == pytest.approx(200)
    assert sum(p['minutes'] for p in result['away']['players']) == pytest.approx(200)
