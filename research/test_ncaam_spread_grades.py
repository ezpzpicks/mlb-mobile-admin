from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from builders.ncaam_spread_grades import capture_spread_grade, grade_saved_spread, load_spread_policy
from scripts.refresh_ncaam_spread_grades import refresh
from research.test_ncaam_matchup_live import StorageTests
from shared import ncaam_auto_storage as store


class SpreadGradeTests(unittest.TestCase):
    def setUp(self):
        self.benchmark={'season':2027,'team_count':365,'observed_at':'2026-11-12T11:00:00Z',
            'metrics':{m:{'mean':105 if m!='tempo' else 71,'sd':5 if m!='tempo' else 2} for m in ('offense','defense','tempo')}}
        self.forecast={'recordId':'capture-1','gameId':'1','modelVersion':'test','away_team':'Away','home_team':'Home',
            'lockedAt':'2026-11-12T12:00:00Z','tipoff':'2026-11-12T20:00:00Z','home_court':{'season':2027},
            'ratings_observed_at':self.benchmark['observed_at'],'market':{'home_spread':-5.5,'as_of':'2026-11-12T12:00:00Z'},
            'fair_home_spread':-7.5,'home_rating':{'offense':110,'defense':100,'tempo':71},
            'away_rating':{'offense':108,'defense':107,'tempo':71}}

    def grade(self,**patches):
        return grade_saved_spread({**self.forecast,**patches},[self.benchmark])

    def test_cover_edge_window_has_exact_inclusive_and_exclusive_boundaries(self):
        for edge,expected in [(0,'Research'),(.99,'Research'),(1,'B'),(1.99,'B'),(2,'A'),(3.99,'A'),(4,'Research'),(8,'Research')]:
            with self.subTest(edge=edge):
                g=self.grade(fair_home_spread=-5.5-edge)
                self.assertEqual(g['grade'],expected)
                self.assertEqual(g['side'],'home' if expected!='Research' else None)

    def test_projected_losing_underdog_can_cover_and_never_flips_to_winning_team(self):
        g=self.grade(fair_home_spread=-3.5)
        self.assertEqual((g['projected_side'],g['line'],g['projected_margin'],g['edge']),('away',5.5,-3.5,2))
        self.assertEqual((g['grade'],g['role']),('Research','Underdog'))
        self.forecast['away_rating']={'offense':110,'defense':100,'tempo':71}
        self.forecast['home_rating']={'offense':108,'defense':107,'tempo':71}
        g=self.grade(fair_home_spread=-3.5)
        self.assertEqual((g['grade'],g['side'],g['selection_team']),('A','away','Away'))

    def test_defense_and_opposing_tempo_boundaries_work_for_both_roles(self):
        for role,fair,picked in [('Favorite',-7.5,'home_rating'),('Underdog',-3.5,'away_rating')]:
            for z,expected in [(-1,'B'),(-.999,'A'),(0,'A'),(1,'A')]:
                with self.subTest(role=role,z=z):
                    f=deepcopy(self.forecast);f['fair_home_spread']=fair
                    for loc in ('home_rating','away_rating'):f[loc]['tempo']=71+2*(z if role=='Favorite' else -z)
                    f[picked]['defense']=100;f[picked]['offense']=110
                    opponent='away_rating' if picked=='home_rating' else 'home_rating'
                    f[opponent]['defense']=107;f[opponent]['offense']=108
                    self.assertEqual(grade_saved_spread(f,[self.benchmark])['grade'],expected)
        self.forecast['home_rating']['defense']=100.0001
        self.assertEqual(self.grade()['grade'],'B')

    def test_three_factor_A_needs_three_points_both_advantages_and_role_tempo_fit(self):
        self.forecast['home_rating']['defense']=102
        self.forecast['home_rating']['tempo']=73
        self.assertEqual(self.grade(fair_home_spread=-8.5)['grade'],'A')
        self.assertEqual(self.grade(fair_home_spread=-8.49)['grade'],'B')
        self.forecast['home_rating']['tempo']=71
        self.assertEqual(self.grade(fair_home_spread=-8.5)['grade'],'B')
        self.forecast['home_rating']['tempo']=73;self.forecast['home_rating']['offense']=108
        self.assertEqual(self.grade(fair_home_spread=-8.5)['grade'],'B')
        self.forecast['home_rating']['defense']=107
        self.assertEqual(self.grade(fair_home_spread=-8.5)['grade'],'Research')

    def test_both_directions_and_grade_are_independent_of_final_scores_or_legacy_edges(self):
        g=self.grade()
        changed=self.grade(home_spread_edge=-98,spread_pick='Away +5.5',spread_grade='Research',spreadResult='LOSS',home_score=0,away_score=999)
        self.assertEqual(g,changed)
        policy=load_spread_policy();policy['edge_window']['maximum_exclusive']=2
        self.assertEqual(grade_saved_spread(self.forecast,[self.benchmark],policy)['grade'],'Research')

    def test_future_stale_incomplete_and_wrong_season_inputs_fail_closed(self):
        for change in [{'lockedAt':self.forecast['tipoff']},{'ratings_observed_at':'2026-11-12T12:00:01Z'},
                {'ratings_observed_at':'2026-11-10T11:00:00Z'}, {'market':{'home_spread':-5.5,'as_of':'2026-11-12T10:00:00Z'}}]:
            with self.subTest(change=change),self.assertRaises(ValueError):self.grade(**change)
        for change in [{'observed_at':'2026-11-12T12:00:01Z'},{'observed_at':'2026-11-10T11:00:00Z'},{'season':2026},{'team_count':299}]:
            with self.subTest(change=change),self.assertRaises(ValueError):grade_saved_spread(self.forecast,[{**self.benchmark,**change}])

    def test_live_publication_and_reviewed_refresh_preserve_source_captures(self):
        for pub in ('2026-11-12T11:59:59Z','2026-11-12T20:00:00Z'):
            with self.assertRaises(ValueError):capture_spread_grade(self.forecast,[self.benchmark],published_at=pub)
        self.assertEqual(capture_spread_grade(self.forecast,[self.benchmark],mode='replay',published_at='2026-11-13T01:00:00Z')['record_mode'],'replay')
        f={**self.forecast,'matchup_total':{'national_benchmark':self.benchmark}}
        later={**f,'recordId':'capture-2','lockedAt':'2026-11-12T12:01:00Z'}
        closed={**f,'gameId':'closed','tipoff':'2026-11-12T11:00:00Z'}
        future={**f,'recordId':'future','lockedAt':'2026-11-12T12:03:00Z'}
        original=deepcopy(f)
        with patch('scripts.refresh_ncaam_spread_grades.store.rows',return_value=[{'Details JSON':json.dumps(x)} for x in (f,later,closed,future)]),patch('scripts.refresh_ncaam_spread_grades.store.upsert_many') as save:
            r=refresh(None,'2026-11-12',now='2026-11-12T12:02:00Z')
            self.assertEqual((r['rows'],r['grades']),(1,{'A':1}));save.assert_not_called()
        self.assertEqual(f,original)


class SpreadAtomicStorageTests(StorageTests):
    def test_forecast_total_and_spread_commit_as_one_immutable_transaction(self):
        base={'Date':'2026-11-12','Game Key':'1','Selection':'capture-1','Details JSON':'source'}
        entries=[(dataset,{**base,'Market':market},True) for dataset,market in ((store.FORECASTS,'Forecast'),(store.MATCHUP_GRADES,'Total'),(store.SPREAD_GRADES,'Spread'))]
        store.upsert_many(self.db,entries);store.upsert_many(self.db,entries)
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM dataset_rows').fetchone()[0],3)
        self.connection.execute("CREATE TRIGGER reject_spread BEFORE INSERT ON dataset_rows WHEN NEW.dataset='ncaam_matchup_spread_grades' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(Exception):store.upsert_many(self.db,[(d,{**r,'Selection':'new'},i) for d,r,i in entries])
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM dataset_rows').fetchone()[0],3)


if __name__=='__main__':unittest.main()
