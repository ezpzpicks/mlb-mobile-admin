from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from builders.ncaam_model import team_key
from builders.ncaam_player_impact import POLICY
from scripts import run_ncaam_builder as runner
from scripts import run_scheduled_builders as dispatcher

NOW=datetime(2026,11,1,6,tzinfo=timezone.utc)
SOURCE=json.loads((Path(__file__).parent/'fixtures/espn_ncaam_odds_2026_11_01.json').read_text())


def test_fresh_inputs_are_validated_after_downloads_finish(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None): return (NOW+timedelta(seconds=10)).astimezone(tz)
    monkeypatch.setattr(runner,'datetime',Clock)
    def cached(_db,dataset,key,url,parser,season,*_a,**_kw):
        observed=(NOW+timedelta(seconds=1)).isoformat()
        if dataset in [runner.store.RATINGS,'ncaam_prior_rating_inputs']:
            return {'season':season,'observed_at':observed,'source_url':url,
                    'teams':{'away':{'team':'Away','record':'0-0','offense':112,'defense':100,'tempo':68}}}
        if dataset=='ncaam_d1_game_counts': return {'season':2027,'observed_at':observed,'counts':{'away':0}}
        return {'season':season,'observed_at':observed,'source_url':url,'players':[],'policy':POLICY}
    monkeypatch.setattr(runner,'cached_input',cached)
    teams,_=runner.load_model_inputs(None,'2026-11-01',2027,NOW,False)
    assert teams['teams']['away']['offense']==112
    assert teams['d1_games_observed_at']>(NOW.isoformat())


def test_default_builder_saves_all_119_games_and_repeat_is_idempotent(monkeypatch):
    # A synthetic capacity fixture reuses two verified schools; unique physical
    # event IDs exercise the same production save path beyond the old 100 cap.
    payload={'events':[]}
    for i in range(119):
        event=deepcopy(SOURCE['events'][0]);event['id']=str(900000000+i)
        payload['events'].append(event)
    games=runner.parse_slate(payload,'2026-11-01')
    bundle={'season':2027,'observed_at':NOW.isoformat(),'source':'Test','source_url':'https://example.test',
            'league_efficiency':105,'teams':{team_key(game[side]):{'team':game[side],'offense':110,'defense':100,'tempo':68}
                                           for game in games for side in ['away','home']}}
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None): return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(runner,'datetime',Clock)
    monkeypatch.setattr(runner,'get',lambda *_a,**_kw:(json.dumps(payload),NOW))
    monkeypatch.setattr(runner,'load_model_inputs',lambda *_a:(bundle,None))
    monkeypatch.setattr(runner,'load_lineups',lambda *_a:{})
    from shared import turso_storage,ncaaf_auto_builder
    monkeypatch.setattr(turso_storage,'is_turso_ready',lambda:True)
    monkeypatch.setattr(ncaaf_auto_builder,'eligible_markets',lambda *_a:([],[]))
    saved=[]
    monkeypatch.setattr(runner.store,'rows',lambda _db,dataset,_day:saved if dataset==runner.store.FORECASTS else [])
    monkeypatch.setattr(runner.store,'upsert',lambda _db,_dataset,row,**_kw:saved.append(row))
    monkeypatch.setenv('NCAAM_AUTO_BUILDER_ENABLED','1')
    assert runner.main(['--commit'])==0
    assert len(saved)==119
    ids={json.loads(row['Details JSON'])['gameId'] for row in saved}
    assert ids=={game['game_id'] for game in games}
    assert runner.main(['--commit'])==0 and len(saved)==119


def test_dispatcher_explicitly_supplies_full_ncaam_slate_limit(monkeypatch):
    monkeypatch.setenv('NCAAM_AUTO_BUILDER_ENABLED','1')
    monkeypatch.delenv('NFL_AUTO_BUILDER_ENABLED',raising=False)
    monkeypatch.delenv('NFL_AUTO_DIAGNOSTIC_UNTIL',raising=False)
    calls=[]
    monkeypatch.setattr(dispatcher.subprocess,'run',lambda args,**_kw:(calls.append(args) or SimpleNamespace(returncode=0)))
    assert dispatcher.main()==0
    ncaam=next(args for args in calls if args[2].endswith('run_ncaam_builder.py'))
    assert ncaam[3:]==['--commit','--limit','200']
    ncaaf=next(args for args in calls if args[2].endswith('run_ncaaf_builder.py'))
    assert ncaaf[3:]==['--commit','--limit','100']
    nba=next(args for args in calls if args[2].endswith('run_nba_builder.py'))
    assert nba[3:]==['--commit']
    with pytest.raises(SystemExit): runner.main(['--limit','201'])
