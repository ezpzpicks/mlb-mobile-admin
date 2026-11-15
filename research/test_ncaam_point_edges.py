from copy import deepcopy
import json
import math
from pathlib import Path
import unittest

from builders.ncaam_matchup_projection import from_captured_inputs, project, MODEL_VERSION
from builders.ncaam_point_edges import capture_grades, load_policy

LOCK='2026-11-14T12:00:00+00:00'
TIP='2026-11-14T23:00:00+00:00'


def forecast(*,court=0.,away=None,home=None,impact=None,home_line=0.,total_line=150.):
    b={'season':2027,'team_count':365,'observed_at':'2026-11-14T11:00:00+00:00',
       'metrics':{m:{'mean':70. if m=='tempo' else 105.,'sd':3. if m=='tempo' else 8.} for m in ('offense','defense','tempo')}}
    a=away or {'team':'Away','offense':110.,'defense':108.,'tempo':70.}
    h=home or {'team':'Home','offense':115.,'defense':105.,'tempo':70.}
    url='https://barttorvik.com/2027_team_results.csv'
    p=from_captured_inputs(a,h,b,as_of=LOCK,tipoff=TIP,source_url=url,home_court_points=court,
        neutral_site=court==0,lineup_adjustment=impact)
    f={'recordId':'synthetic-point-forecast','gameId':'synthetic','modelVersion':MODEL_VERSION,'projection_engine':MODEL_VERSION,
       'lockedAt':LOCK,'tipoff':TIP,'phase':'pregame','away_team':'Away','home_team':'Home',
       'away_rating':a,'home_rating':h,'ratings_source_url':url,'ratings_observed_at':b['observed_at'],
       'home_court':{'season':2027,'fallback':False},'home_court_points':court,'neutral_site':court==0,'matchup_projection':p,
       'away_score':round(p['away_points'],1),'home_score':round(p['home_points'],1),
       'fair_home_spread':-p['margin'],'total':p['total'],
       'market':{'home_spread':home_line,'total':total_line,'as_of':LOCK,'source':'ScoresAndOdds','odds':-110}}
    return f,b


def grades(f,b,**kwargs):
    return capture_grades(f,[b],published_at=LOCK,**kwargs)


class ProjectionTests(unittest.TestCase):
    def test_shared_live_projector_keeps_the_old_forecast_as_comparison_only(self):
        from research.ncaam_test_support import complete_population
        from builders.ncaam_model import team_key
        ratings=complete_population({team_key('Away'):{'team':'Away','offense':110.,'defense':108.,'tempo':70.},
            team_key('Home'):{'team':'Home','offense':115.,'defense':105.,'tempo':70.}})
        bundle={'season':2027,'observed_at':'2026-11-14T11:00:00+00:00','source':'Synthetic Torvik fixture',
            'source_url':'https://barttorvik.com/2027_team_results.csv','league_efficiency':105.,'teams':ratings}
        game={'away':'Away','home':'Home','away_aliases':['Away'],'home_aliases':['Home'],
            'season':2027,'tipoff':TIP,'neutral_site':True}
        market={'home_spread':-2.,'total':150.,'as_of':LOCK}
        f=project(game,bundle,market,LOCK)
        self.assertEqual(f['projection_engine'],MODEL_VERSION)
        self.assertAlmostEqual(f['matchup_projection']['margin'],5.6)
        changed=project(game,bundle,{**market,'home_spread':10.,'total':170.},LOCK)
        self.assertEqual(f['matchup_projection'],changed['matchup_projection'])
        self.assertIn('margin_calibration',f['legacy_forecast'])

    def test_average_pairings_produce_the_national_baseline(self):
        a={'team':'Away','offense':105.,'defense':105.,'tempo':70.};h={**a,'team':'Home'}
        f,_=forecast(away=a,home=h)
        self.assertEqual((f['total'],f['fair_home_spread']),(147.,0.))

    def test_offense_faces_opposing_defense_in_points_per_100(self):
        f,_=forecast();a=deepcopy(f['away_rating']);h=deepcopy(f['home_rating']);h['offense']+=5
        changed,_=forecast(away=a,home=h)
        self.assertAlmostEqual(changed['matchup_projection']['home_points']-f['matchup_projection']['home_points'],3.5)
        a['defense']-=20;suppressed,_=forecast(away=a,home=h)
        self.assertLess(suppressed['matchup_projection']['home_points'],73.5)

    def test_slow_pace_lowers_total_and_expected_favorite_margin(self):
        f,_=forecast();a=deepcopy(f['away_rating']);h=deepcopy(f['home_rating']);a['tempo']=h['tempo']=60.
        slow,_=forecast(away=a,home=h)
        self.assertLess(slow['total'],f['total'])
        self.assertLess(-slow['fair_home_spread'],-f['fair_home_spread'])
        self.assertGreater(10+slow['fair_home_spread'],10+f['fair_home_spread'])

    def test_neutral_team_swap_preserves_total_and_reverses_margin(self):
        f,_=forecast();swapped,_=forecast(away={**f['home_rating'],'team':'Away'},home={**f['away_rating'],'team':'Home'})
        self.assertAlmostEqual(f['total'],swapped['total'])
        self.assertAlmostEqual(f['fair_home_spread'],-swapped['fair_home_spread'])

    def test_home_court_and_player_impact_are_applied_once(self):
        base,_=forecast();court,_=forecast(court=3.2)
        self.assertAlmostEqual(court['total'],base['total'])
        self.assertAlmostEqual(base['fair_home_spread']-court['fair_home_spread'],3.2)
        impact={'away':{'offense_change_per_100':2.,'defense_improvement_per_100':1.},
                'home':{'offense_change_per_100':-1.,'defense_improvement_per_100':3.}}
        f,_=forecast(court=3.2,impact=impact)
        self.assertAlmostEqual(f['matchup_projection']['away_points']-court['matchup_projection']['away_points'],-.7)
        self.assertAlmostEqual(f['matchup_projection']['home_points']-court['matchup_projection']['home_points'],-1.4)

    def test_lines_and_final_results_are_not_score_inputs(self):
        f,b=forecast();p=deepcopy(f['matchup_projection'])
        f['market'].update(home_spread=-15.,total=180.);f.update(actual_home=99,result='Win')
        self.assertEqual(grades(f,b)['total']['projected_total'],p['total'])
        self.assertEqual(f['matchup_projection'],p)


class PointGradeTests(unittest.TestCase):
    def test_saved_replay_is_reproducible_without_results_or_current_ratings(self):
        from research.replay_ncaam_point_model import replay
        f,b=forecast();g=grades(f,b);f.update(matchup_spread=g['spread'],matchup_total=g['total'],result='Win')
        first=replay({'today':'2026-11-14','ncaamMarket':{'games':[{}],'forecasts':[f,f]}})
        second=replay(first)
        self.assertEqual(first['errors'],[]);self.assertEqual(second['errors'],[])
        self.assertEqual(len(first['games']),1)
        self.assertEqual(first['games'][0]['matchup_projection'],second['games'][0]['matchup_projection'])
        self.assertEqual(first['ranked_candidates'],second['ranked_candidates'])
        self.assertNotIn('result',first['games'][0])

    def test_exact_cutoffs_for_every_market_and_direction(self):
        for edge in (0.,4.999,5.,6.999,7.,7.999,8.,9.999,10.,12.):
            for sign in (-1,1):
                f,b=forecast();p=f['matchup_projection']
                f['market'].update(home_spread=sign*edge-p['margin'],total=p['total']-sign*edge)
                result=grades(f,b)
                for market in ('spread','total'):
                    a,b_min=(7.,5.) if market=='spread' else (10.,8.)
                    tier='A' if edge>=a else 'B' if edge>=b_min else 'Research'
                    with self.subTest(edge=edge,sign=sign,market=market):
                        self.assertEqual(result[market]['grade'],tier)

    def test_relative_strength_and_confidence_do_not_add_a_second_bonus(self):
        for off,defense in ((140.,70.),(75.,140.)):
            f,b=forecast(home={'team':'Home','offense':off,'defense':defense,'tempo':70.})
            f['market']['total']=f['total']-8.
            first=grades(f,b)['total'];f.update(injury_verified=True,starters_confirmed=True)
            self.assertEqual((first['grade'],grades(f,b)['total']['grade']),('B','B'))

    def test_projection_only_and_price_holds_preserve_the_numerical_grade(self):
        f,b=forecast(home_line=-25.);g=grades(f,b)['spread']
        self.assertEqual((g['edge_grade'],g['grade'],g['eligible']),('A','Research',False))
        f,b=forecast();f['market']['total']=f['total']-10.;f['market']['odds']=-500
        g=grades(f,b)['total'];self.assertEqual((g['edge_grade'],g['grade']),('A','Research'))

    def test_source_hash_benchmark_and_publication_clocks_fail_closed(self):
        f,b=forecast()
        for field,value in (('fair_home_spread',99.),('ratings_source_url','other')):
            bad=deepcopy(f);bad[field]=value
            with self.assertRaises(ValueError):grades(bad,b)
        bad=deepcopy(f);bad['matchup_projection']['total']+=1
        with self.assertRaises(ValueError):grades(bad,b)
        bad=deepcopy(f);bad['away_rating']['offense']+=1
        with self.assertRaises(ValueError):grades(bad,b)
        for published in ('2026-11-14T11:59:59+00:00',TIP):
            with self.assertRaises(ValueError):capture_grades(f,[b],published_at=published)
        replay=capture_grades(f,[b],mode='replay',published_at='2026-11-15T01:00:00+00:00')
        self.assertEqual(replay['total']['record_mode'],'replay')


if __name__=='__main__':unittest.main()
