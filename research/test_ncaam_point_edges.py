from copy import deepcopy
import json
import math
from pathlib import Path
import unittest

from builders.ncaam_matchup_projection import (from_captured_inputs, project, MODEL_VERSION,
    LEGACY_POINT_MODEL_VERSION, ADJUSTED_TEMPO_MODEL_VERSION,
    INITIAL_OFFENSE_BONUS_MODEL_VERSION, POINT_MODEL_VERSIONS)
from builders.ncaam_point_edges import capture_grades, load_policy

LOCK='2026-11-14T12:00:00+00:00'
TIP='2026-11-14T23:00:00+00:00'


def forecast(*,court=0.,away=None,home=None,impact=None,home_line=0.,total_line=150.,model_version=MODEL_VERSION):
    b={'season':2027,'team_count':365,'observed_at':'2026-11-14T11:00:00+00:00',
       'metrics':{m:{'mean':70. if m=='tempo' else 105.,'sd':3. if m=='tempo' else 8.} for m in ('offense','defense','tempo')}}
    a=away or {'team':'Away','offense':110.,'defense':108.,'tempo':70.}
    h=home or {'team':'Home','offense':115.,'defense':105.,'tempo':70.}
    url='https://barttorvik.com/2027_team_results.csv'
    p=from_captured_inputs(a,h,b,as_of=LOCK,tipoff=TIP,source_url=url,home_court_points=court,
        neutral_site=court==0,lineup_adjustment=impact,model_version=model_version)
    f={'recordId':'synthetic-point-forecast','gameId':'synthetic','modelVersion':model_version,'projection_engine':model_version,
       'lockedAt':LOCK,'tipoff':TIP,'phase':'pregame','away_team':'Away','home_team':'Home',
       'away_rating':a,'home_rating':h,'ratings_source_url':url,'ratings_observed_at':b['observed_at'],
       'home_court':{'season':2027,'fallback':False},'home_court_points':court,'neutral_site':court==0,'matchup_projection':p,
       'lineup_adjustment':impact or {},
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
        self.assertAlmostEqual(f['matchup_projection']['margin'],f['matchup_projection']['possessions']*.08+5.)
        changed=project(game,bundle,{**market,'home_spread':10.,'total':170.},LOCK)
        self.assertEqual(f['matchup_projection'],changed['matchup_projection'])
        self.assertIn('margin_calibration',f['legacy_forecast'])

    def test_average_pairings_produce_the_national_baseline(self):
        a={'team':'Away','offense':105.,'defense':105.,'tempo':70.};h={**a,'team':'Home'}
        f,_=forecast(away=a,home=h)
        self.assertEqual((f['total'],f['fair_home_spread']),(147.,0.))

    def test_adjusted_tempo_against_an_average_opponent_is_not_halved(self):
        for pace in (60., 80.):
            a={'team':'Away','offense':105.,'defense':105.,'tempo':pace}
            h={**a,'team':'Home','tempo':70.}
            f,_=forecast(away=a,home=h)
            self.assertEqual(f['matchup_projection']['possessions'],pace)
        for a_pace,h_pace in ((60.,65.),(75.,80.)):
            a={'team':'Away','offense':105.,'defense':105.,'tempo':a_pace}
            h={**a,'team':'Home','tempo':h_pace}
            f,_=forecast(away=a,home=h)
            pace=f['matchup_projection']['possessions']
            self.assertLess(pace,min(a_pace,h_pace)) if a_pace<70 else self.assertGreater(pace,max(a_pace,h_pace))

    def test_unsupported_combined_pace_is_rejected_instead_of_clamped(self):
        a={'team':'Away','offense':105.,'defense':105.,'tempo':50.}
        with self.assertRaises(ValueError):forecast(away=a,home={**a,'team':'Home'})

    def test_original_point_model_remains_reproducible(self):
        a={'team':'Away','offense':105.,'defense':105.,'tempo':60.}
        h={**a,'team':'Home','tempo':65.}
        f,b=forecast(away=a,home=h,model_version=LEGACY_POINT_MODEL_VERSION)
        self.assertEqual(f['matchup_projection']['possessions'],62.5)
        self.assertNotIn('tempo_model',f['matchup_projection'])
        self.assertEqual(grades(f,b)['total']['projection_model_version'],LEGACY_POINT_MODEL_VERSION)
        from research.replay_ncaam_point_model import replay
        g=grades(f,b);f.update(matchup_spread=g['spread'],matchup_total=g['total'])
        r=replay({'model_version':LEGACY_POINT_MODEL_VERSION,'games':[f]})
        self.assertEqual(r['errors'],[])
        self.assertEqual(r['games'][0]['matchup_projection'],f['matchup_projection'])

    def test_user_examples_change_scores_without_using_market_or_outcomes(self):
        samples=json.loads((Path(__file__).parent/'fixtures/ncaam_tempo_examples_2026_11_15.json').read_text())['games']
        for f in samples:
            kwargs={'as_of':f['lockedAt'],'tipoff':f['tipoff'],'source_url':f['ratings_source_url'],
                'home_court_points':f['home_court_points'],'neutral_site':f['neutral_site'],'lineup_adjustment':f['lineup_adjustment']}
            old=from_captured_inputs(f['away_rating'],f['home_rating'],f['benchmark'],model_version=LEGACY_POINT_MODEL_VERSION,**kwargs)
            revised=from_captured_inputs(f['away_rating'],f['home_rating'],f['benchmark'],**kwargs)
            self.assertAlmostEqual(old['total'],f['total'])
            for side in ('away','home'):
                self.assertEqual(old['pairings'][side]['expected_efficiency'],revised['pairings'][side]['expected_efficiency'])
            if 'Harvard' in f['away_team']:
                self.assertAlmostEqual(revised['total'],136.20,places=2)
                self.assertLess(revised['total']-f['market']['total'],8)
                self.assertGreater(revised['total'],f['market']['total'])
            else:
                self.assertAlmostEqual(revised['total'],153.65,places=2)
                self.assertEqual(revised['adjustments']['home']['offense_bonus']['points'],5)
                self.assertEqual(revised['adjustments']['away']['offense_bonus']['points'],0)
            f['market']['total']=250.;f['actual_total']=1.
            self.assertEqual(revised,from_captured_inputs(f['away_rating'],f['home_rating'],f['benchmark'],**kwargs))

    def test_offense_faces_opposing_defense_in_points_per_100(self):
        f,_=forecast();a=deepcopy(f['away_rating']);h=deepcopy(f['home_rating']);h['offense']+=5
        changed,_=forecast(away=a,home=h)
        self.assertAlmostEqual(changed['matchup_projection']['home_points']-f['matchup_projection']['home_points'],8.5)
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

    def test_progressive_bonus_matches_each_agreed_defensive_tier_and_boundary(self):
        # Inclusive extreme boundaries match the site's factor labels.
        for defense_z, points in ((-2,1),(-1.5,1),(-1.25,3),(-1,3),(-.5,5),
                                   (0,5),(.5,5),(1,8),(1.25,8),(1.5,15),(2,15)):
            with self.subTest(defense_z=defense_z):
                a={'team':'Away','offense':100.,'defense':105.+8*defense_z,'tempo':70.}
                h={'team':'Home','offense':117.,'defense':105.,'tempo':70.}
                impact={'away':{'offense_change_per_100':2.},'home':{'defense_improvement_per_100':1.}}
                old,_=forecast(away=a,home=h,court=3.2,impact=impact,model_version=ADJUSTED_TEMPO_MODEL_VERSION)
                new,b=forecast(away=a,home=h,court=3.2,impact=impact)
                self.assertAlmostEqual(new['matchup_projection']['home_points']-old['matchup_projection']['home_points'],points)
                self.assertEqual(new['matchup_projection']['away_points'],old['matchup_projection']['away_points'])
                self.assertAlmostEqual(new['total']-old['total'],points)
                self.assertAlmostEqual(old['fair_home_spread']-new['fair_home_spread'],points)
                self.assertEqual(new['matchup_projection']['possessions'],old['matchup_projection']['possessions'])
                self.assertEqual(grades(new,b)['total']['projected_total'],new['total'])

    def test_nonqualifying_and_very_weak_offenses_have_no_extra_adjustment(self):
        for offense in (75.,93.,105.,116.999999):
            h={'team':'Home','offense':offense,'defense':105.,'tempo':70.}
            old,_=forecast(home=h,model_version=ADJUSTED_TEMPO_MODEL_VERSION)
            new,_=forecast(home=h)
            self.assertEqual(new['total'],old['total'])
            self.assertEqual(new['matchup_projection']['adjustments']['home']['offense_bonus']['points'],0)
            self.assertFalse(new['matchup_projection']['adjustments']['home']['offense_bonus']['qualified'])

    def test_two_qualifying_offenses_each_receive_their_own_opposing_defense_bonus(self):
        a={'team':'Away','offense':120.,'defense':94.,'tempo':72.}
        h={'team':'Home','offense':125.,'defense':110.,'tempo':70.}
        old,_=forecast(away=a,home=h,model_version=ADJUSTED_TEMPO_MODEL_VERSION)
        new,_=forecast(away=a,home=h)
        self.assertEqual(new['matchup_projection']['adjustments']['away']['offense_bonus']['points'],5)
        self.assertEqual(new['matchup_projection']['adjustments']['home']['offense_bonus']['points'],3)
        self.assertAlmostEqual(new['total']-old['total'],8)
        swapped,_=forecast(away={**h,'team':'Away'},home={**a,'team':'Home'})
        self.assertAlmostEqual(new['total'],swapped['total'])
        self.assertAlmostEqual(new['fair_home_spread'],-swapped['fair_home_spread'])

    def test_frozen_nebraska_michigan_and_harvard_examples_match_the_agreed_preview(self):
        data=json.loads((Path(__file__).parent/'fixtures/ncaam_progressive_offense_examples_2026_11_15.json').read_text())
        for f in data['games']:
            kw={k:f[k] for k in ('tipoff','home_court_points','neutral_site','lineup_adjustment')}
            kw.update(as_of=f['lockedAt'],source_url=f['ratings_source_url'])
            old=from_captured_inputs(f['away_rating'],f['home_rating'],f['benchmark'],model_version=ADJUSTED_TEMPO_MODEL_VERSION,**kw)
            new=from_captured_inputs(f['away_rating'],f['home_rating'],f['benchmark'],
                model_version=INITIAL_OFFENSE_BONUS_MODEL_VERSION,**kw)
            for side in ('away','home'):
                self.assertAlmostEqual(new[side+'_points'],f['expected'][side+'_points'],places=8)
                self.assertEqual(new['adjustments'][side]['offense_bonus']['points'],f['expected'][side+'_bonus'])
                self.assertAlmostEqual(new[side+'_points']-old[side+'_points'],f['expected'][side+'_bonus'])
            archived=from_captured_inputs(f['away_rating'],f['home_rating'],f['benchmark'],model_version=ADJUSTED_TEMPO_MODEL_VERSION,**kw)
            self.assertEqual(old,archived)
            self.assertNotIn('offense_bonus_policy',archived)
            self.assertNotIn('offense_bonus',archived['adjustments']['home'])

    def test_revised_tiers_preserve_original_bonus_forecasts_and_replays(self):
        from research.replay_ncaam_point_model import replay
        for defense,original,revised in ((117.,10.,15.),(113.,9.,8.),(109.,8.,5.)):
            a={'team':'Away','offense':100.,'defense':defense,'tempo':70.}
            h={'team':'Home','offense':117.,'defense':105.,'tempo':70.}
            old,b=forecast(away=a,home=h,model_version=INITIAL_OFFENSE_BONUS_MODEL_VERSION)
            frozen=deepcopy(old)
            new,_=forecast(away=a,home=h)
            self.assertEqual(old['matchup_projection']['adjustments']['home']['offense_bonus']['points'],original)
            self.assertAlmostEqual(new['total']-old['total'],revised-original)
            self.assertAlmostEqual(old['fair_home_spread']-new['fair_home_spread'],revised-original)
            self.assertEqual(new['matchup_projection']['away_points'],old['matchup_projection']['away_points'])
            g=grades(old,b)
            result=replay({'model_version':INITIAL_OFFENSE_BONUS_MODEL_VERSION,
                'games':[{**old,'matchup_total':g['total'],'matchup_spread':g['spread']}]})
            self.assertEqual(result['errors'],[])
            self.assertEqual(result['games'][0]['matchup_projection'],frozen['matchup_projection'])
            self.assertEqual(old,frozen)


class PointGradeTests(unittest.TestCase):
    def test_missing_or_rehashed_progressive_bonus_changes_cannot_be_graded(self):
        from builders.ncaam_matchup_grades import policy_digest
        for version in (INITIAL_OFFENSE_BONUS_MODEL_VERSION,MODEL_VERSION):
            f,b=forecast(home={'team':'Home','offense':117.,'defense':105.,'tempo':70.},model_version=version)
            for change in ('missing','double','policy'):
                bad=deepcopy(f);p=bad['matchup_projection']
                if change=='missing':del p['adjustments']['home']['offense_bonus']
                elif change=='double':
                    bonus=p['adjustments']['home']['offense_bonus']['points']
                    p['adjustments']['home']['offense_bonus']['points']*=2
                    for key in ('home_points','margin','total'):p[key]+=bonus
                    bad['total']+=bonus;bad['fair_home_spread']-=bonus
                else:p['offense_bonus_policy']['points_by_opponent_defense']['below_average']=99
                p['sha256']=policy_digest({k:v for k,v in p.items() if k!='sha256'})
                with self.assertRaises(ValueError):grades(bad,b)

    def test_both_production_adapters_route_each_point_model_to_its_saved_score(self):
        from builders.ncaam_matchup_live import capture_grade
        from builders.ncaam_spread_grades import capture_spread_grade
        for version in POINT_MODEL_VERSIONS:
            f,b=forecast(model_version=version)
            policy=load_policy()
            total=capture_grade(f,[b],[],policy=policy,published_at=LOCK)
            spread=capture_spread_grade(f,[b],policy=policy,published_at=LOCK)
            self.assertEqual(total,grades(f,b)['total'])
            self.assertEqual(spread,grades(f,b)['spread'])
            self.assertEqual(total['projection_model_version'],version)

    def test_replay_keeps_game_ids_separate_from_pace_log_join_keys(self):
        from research.evaluate_ncaam_tempo import evaluate
        from tempfile import TemporaryDirectory
        import csv
        f,b=forecast();f.update(phase='pregame',matchup_total={'national_benchmark':b})
        game={'Game ID':f['gameId'],'Date':'2026-11-14','Completed':'TRUE','Status':'STATUS_FINAL',
            'Source':'ESPN NCAAM schedule; ScoresAndOdds market lines','Away Team':f['away_team'],
            'Home Team':f['home_team'],'Game Time':f['tipoff'],'Result Updated':'2026-11-15T01:00:00Z',
            'Away Score':'70','Home Score':'75'}
        with TemporaryDirectory() as directory:
            p=Path(directory)/'2026-11-14.json';logs=Path(directory)/'logs.csv'
            p.write_text(json.dumps({'ncaamMarket':{'date':'2026-11-14','forecasts':[f],'games':[game]}}))
            with logs.open('w') as handle:
                writer=csv.writer(handle)
                for team,opponent in (('Away','Home'),('Home','Away')):
                    row=['']*31;row[0]='11/14/26';row[2]=team;row[4]=opponent
                    row[22]='2027';row[23]='70.5';row[24]='synthetic-paired-game';writer.writerow(row)
            result=evaluate([p],logs)
            self.assertEqual(result['all']['games'],1)
            self.assertEqual(result['pace']['all']['games'],1)
            self.assertEqual(result['games'][0]['actual'],145)

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

    def test_large_spreads_use_the_existing_edge_tiers_and_keep_price_holds(self):
        self.assertIsNone(load_policy()['thresholds']['spread_max_market_line'])
        for line in (-35.5,-25.,-20.,20.,25.,35.5):
            f,b=forecast(home_line=line);g=grades(f,b)['spread']
            self.assertEqual((g['edge_grade'],g['grade'],g['eligible']),('A','A',True))
            self.assertNotIn('projection-only',' '.join(g['confidence_flags']))
        f,b=forecast(home_line=-25.);policy=load_policy()
        policy['thresholds']['spread_max_market_line']=20.
        legacy=grades(f,b,policy=policy)['spread']
        self.assertEqual((legacy['edge_grade'],legacy['grade'],legacy['eligible']),('A','Research',False))
        f['market']['odds']=-500
        self.assertEqual(grades(f,b)['spread']['grade'],'Research')
        f,b=forecast();f['market']['total']=f['total']-10.;f['market']['odds']=-500
        g=grades(f,b)['total'];self.assertEqual((g['edge_grade'],g['grade']),('A','Research'))

    def test_reviewed_refresh_regrades_only_latest_unstarted_point_captures(self):
        from unittest.mock import patch
        from scripts.refresh_ncaam_spread_grades import refresh
        f,b=forecast(home_line=-25.);f['matchup_spread']={'national_benchmark':b}
        original=deepcopy(f)
        later={**f,'recordId':'later','lockedAt':'2026-11-14T12:01:00+00:00'}
        later['matchup_projection']=deepcopy(f['matchup_projection'])
        later['matchup_projection']['as_of']=later['lockedAt']
        from builders.ncaam_matchup_grades import policy_digest
        later['matchup_projection']['sha256']=policy_digest({k:v for k,v in later['matchup_projection'].items() if k!='sha256'})
        closed={**f,'gameId':'closed','tipoff':'2026-11-14T11:00:00+00:00'}
        legacy={**f,'gameId':'legacy','modelVersion':'legacy'}
        rows=[{'Details JSON':json.dumps(x)} for x in (f,later,closed,legacy)]
        with patch('scripts.refresh_ncaam_spread_grades.store.rows',return_value=rows),patch('scripts.refresh_ncaam_spread_grades.store.upsert_many') as save:
            result=refresh(None,'2026-11-14',now='2026-11-14T12:02:00+00:00',point_model=True)
            self.assertEqual((result['rows'],result['grades']),(1,{'A':1}))
            save.assert_not_called()
        self.assertEqual(f,original)

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
