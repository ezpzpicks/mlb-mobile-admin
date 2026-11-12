from copy import deepcopy
import json
import sqlite3
import unittest

from builders.ncaam_matchup_live import capture_grade, grade_storage_row, load_reference_quotes
from shared import ncaam_auto_storage as store


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.benchmark = {'season':2027,'team_count':365,'observed_at':'2026-11-11T16:00:00+00:00',
            'metrics':{m:{'mean':105 if m!='tempo' else 71,'sd':5 if m!='tempo' else 2} for m in ('offense','defense','tempo')}}
        self.forecast = {'recordId':'capture-1','gameId':'1','away_team':'Away','home_team':'Home',
            'lockedAt':'2026-11-11T17:00:00+00:00','tipoff':'2026-11-11T19:00:00+00:00','home_court':{'season':2027},
            'ratings_observed_at':self.benchmark['observed_at'],'away_rating':{'offense':120,'defense':115,'tempo':76},
            'home_rating':{'offense':110,'defense':108,'tempo':71},'market':{'total':155,'as_of':'2026-11-11T17:00:00+00:00'}}
        self.references = [{'game_id':str(i),'total':v,'observed_at':'2026-11-10T18:00:00+00:00'} for i,v in enumerate([130,140,160,170,180])]

    def test_live_capture_is_pretipoff_and_replay_is_explicit(self):
        grade = capture_grade(self.forecast,[self.benchmark],self.references,published_at='2026-11-11T17:00:01+00:00')
        self.assertEqual((grade['record_mode'],grade['side'],grade['grade']),('live','Over','A'))
        self.assertEqual(grade['forecast_record_id'],'capture-1')
        for published in ('2026-11-11T16:59:59+00:00','2026-11-11T19:00:00+00:00'):
            with self.assertRaises(ValueError):
                capture_grade(self.forecast,[self.benchmark],self.references,published_at=published)
        replay = capture_grade(self.forecast,[self.benchmark],self.references,mode='replay',published_at='2026-11-12T01:00:00+00:00')
        self.assertEqual(replay['record_mode'],'replay')
        self.assertEqual(replay['side'],grade['side'])
        self.assertEqual(replay['policy_sha256'],grade['policy_sha256'])
        row = grade_storage_row(self.forecast,replay,'2026-11-11')
        self.assertTrue(row['Selection'].startswith('capture-1|'))
        self.assertEqual(json.loads(row['Details JSON'])['record_mode'],'replay')

    def test_missing_and_future_inputs_do_not_create_a_grade(self):
        with self.assertRaises(ValueError): capture_grade(self.forecast,[],self.references,mode='replay')
        with self.assertRaises(ValueError): capture_grade(self.forecast,[self.benchmark],[],mode='replay')


class StorageTests(unittest.TestCase):
    def setUp(self):
        connection = sqlite3.connect(':memory:'); connection.row_factory = sqlite3.Row
        connection.executescript('''CREATE TABLE dataset_rows(sport,dataset,row_index,payload_json,source_hash,date_key,game_key,game,market,selection,result,snapshot_time,imported_at, PRIMARY KEY(sport,dataset,row_index));
            CREATE TABLE dataset_manifest(sport,dataset,source_workbook,source_worksheet,headers_json,row_count,imported_at,source_kind, PRIMARY KEY(sport,dataset));''')
        class DB:
            _sql_text = staticmethod(lambda v: "'"+str(v).replace("'","''")+"'")
            _execute = staticmethod(connection.execute)
            _result_rows = staticmethod(lambda cursor:[dict(row) for row in cursor.fetchall()])
            @staticmethod
            def _pipeline(requests):
                try: connection.executescript(requests[0]['sql'])
                except Exception:
                    connection.rollback(); raise
        self.db, self.connection = DB, connection

    def test_forecast_and_grade_are_atomic_immutable_and_isolated(self):
        row = {'Date':'2026-11-11','Game Key':'1','Selection':'capture-1','Market':'Forecast','Details JSON':'original'}
        grade = {**row,'Market':'Total','Selection':'capture-1|policy','Details JSON':'grade'}
        entries = [(store.FORECASTS,row,True),(store.MATCHUP_GRADES,grade,True)]
        store.upsert_many(self.db,entries); store.upsert_many(self.db,entries)
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM dataset_rows').fetchone()[0],2)
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM dataset_manifest').fetchone()[0],2)
        self.assertEqual(json.loads(self.connection.execute('SELECT payload_json FROM dataset_rows WHERE dataset=?',(store.FORECASTS,)).fetchone()[0])['Details JSON'],'original')
        self.connection.execute("CREATE TRIGGER reject_grade BEFORE INSERT ON dataset_rows WHEN NEW.dataset='ncaam_matchup_total_grades' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            store.upsert_many(self.db,[(d,{**r,'Selection':r['Selection']+'new'},immutable) for d,r,immutable in entries])
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM dataset_rows').fetchone()[0],2)

    def test_market_reference_uses_only_prior_dates_and_last_valid_pregame_capture(self):
        def insert(day,game,lock,total,observed=None,tip='2026-11-10T22:00:00+00:00'):
            f={'gameId':game,'lockedAt':lock,'tipoff':tip,'recordId':lock,'modelVersion':'v1','market':{'total':total,'as_of':observed or lock}}
            store.upsert(self.db,store.FORECASTS,{'Date':day,'Game Key':game,'Selection':lock,'Details JSON':json.dumps(f)},immutable=True)
        insert('2026-11-10','1','2026-11-10T18:00:00+00:00',140)
        insert('2026-11-10','1','2026-11-10T19:00:00+00:00',150)
        insert('2026-11-10','1','2026-11-10T23:00:00+00:00',250)
        insert('2026-11-10','2','2026-11-10T18:00:00+00:00',160,observed='2026-11-11T18:00:00+00:00')
        insert('2026-11-11','3','2026-11-11T18:00:00+00:00',290)
        result=load_reference_quotes(self.db,'2026-11-11','2026-11-11T17:00:00+00:00')
        self.assertEqual(len(result),1); self.assertEqual((result[0]['game_id'],result[0]['total']),('1',150))


if __name__ == '__main__': unittest.main()
