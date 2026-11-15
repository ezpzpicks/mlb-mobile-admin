"""Behavioral checks for a matchup-led point forecast and market integration."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from builders.cfb_matchup_projection import MODEL_VERSION,FIELD,project_from_factors,install_matchup_projection
from builders.cfb_matchup_grades import install_matchup_grading
from builders.cfb_market_relative_matchups import FIELD as COMPARISON_FIELD
from builders.cfb_market_relative_matchups import from_saved_row


NOW=datetime.now(timezone.utc)
KICKOFF=(NOW+timedelta(hours=3)).isoformat()


def factors():
    return {"away":{"team":"Away","offense":27.,"defense":27.,"tempo":12.,"special_teams":0.},
        "home":{"team":"Home","offense":34.,"defense":20.,"tempo":12.,"special_teams":0.},
        "observed_at":(NOW-timedelta(minutes=5)).isoformat(),"source_url":"https://pay.cfbupdate.com/sp-ratings",
        "population_sha256":"a"*64,"season":2026,
        "national":{m:{"mean":mean,"sd":sd,"teams":138} for m,mean,sd in
            (("offense",27.,7.),("defense",27.,7.),("tempo",12.,1.))}}


def project(f=None,**kwargs):
    return project_from_factors(f or factors(),as_of=NOW.isoformat(),kickoff=KICKOFF,**kwargs)


def fake_builder():
    legacy={"away_points":40.,"home_points":41.,"margin":1.,"total":81.,"possessions":15.,
        "regression_personnel_delta":0.,"total_regression_personnel_delta":0.,
        "regression_continuity_delta":0.,"total_regression_continuity_delta":0.}
    game={"Season":2026,"Week":12,"Game Time":KICKOFF,"Away Team":"Away","Home Team":"Home",
        "Home Spread":-3.5,"Total":48.5}
    env=SimpleNamespace(home_field=3.,weather_total_adjustment=0.)
    builder=SimpleNamespace(MODEL_VERSION=MODEL_VERSION,SLATE_COLUMNS=["Projected Away","Projected Home","Projected Margin","Projected Total"],
        TRACKER_COLUMNS=["Projected Away","Projected Home","Grade"],
        project_matchup=lambda *a,**kw:deepcopy(legacy),
        slate_row=lambda r:pd.DataFrame([{k:999 for k in ("Projected Away","Projected Home","Projected Margin","Projected Total")}]),
        tracker_rows=lambda r,*a,**kw:pd.DataFrame([{"Projected Away":999,"Projected Home":999,"Grade":r["total_market"]["grade"]}]))
    def evaluate():
        p=builder.project_matchup(game,None,None,None,env)
        return {"game":game,"projection":p,"simulation":{"home_mean":p["home_points"],"away_mean":p["away_points"]},
            "spread":{"pick":"Home -3.5","price_edge":.1,"ev":.1},
            "total_market":{"pick":"Over 48.5","price_edge":.1,"ev":.1},
            "moneyline":{"grade":"A ML"},
            "market_availability":{"spread":True,"total":True}}
    builder.evaluate_game=evaluate
    return builder,game,env


class MatchupProjectionTests(unittest.TestCase):
    def test_average_pairings_return_the_common_baseline(self):
        f=factors();f["home"].update(offense=27.,defense=27.)
        p=project(f)
        self.assertEqual((p["away_points"],p["home_points"],p["margin"],p["total"]),(27.,27.,0.,54.))

    def test_offense_and_opposing_defense_directly_change_the_score(self):
        f=factors();a=project(f)
        f["home"]["offense"]+=5;b=project(f)
        self.assertAlmostEqual(b["home_points"]-a["home_points"],5.)
        self.assertEqual(b["away_points"],a["away_points"])
        f["away"]["defense"]-=10;c=project(f)
        self.assertAlmostEqual(c["home_points"]-b["home_points"],-10.)

    def test_above_average_offense_can_be_suppressed_by_a_very_strong_defense(self):
        f=factors();f["home"]["offense"]=30.;f["away"]["defense"]=13.
        self.assertLess(project(f)["home_points"],27.)

    def test_slow_tempo_lowers_total_and_expected_favorite_margin(self):
        f=factors();normal=project(f)
        f["away"]["tempo"]=10.;f["home"]["tempo"]=10.;slow=project(f)
        self.assertLess(slow["total"],normal["total"])
        self.assertLess(slow["margin"],normal["margin"])
        # At a fixed +13.5, the underdog gains cover support through pace itself.
        self.assertGreater(13.5-slow["margin"],13.5-normal["margin"])

    def test_two_fast_scoring_pairings_raise_the_total(self):
        f=factors();f["away"].update(offense=40.,defense=35.,tempo=13.5)
        f["home"].update(offense=40.,defense=35.,tempo=13.5)
        self.assertGreater(project(f)["total"],90.)

    def test_weak_offenses_strong_defenses_and_slow_pace_support_low_scoring(self):
        f=factors()
        for side in ("away","home"):f[side].update(offense=17.,defense=20.,tempo=10.)
        self.assertLess(project(f)["total"],25.)

    def test_market_lines_and_results_are_not_projection_inputs(self):
        f=factors();a=project(f)
        f.update(home_line=-40.,total_line=80.,actual_home=77,result="Win")
        self.assertEqual(project(f),a)

    def test_live_overlays_are_added_once_and_do_not_inherit_an_old_base(self):
        p=project(adjustments={"home_field":3.,"personnel_margin":1.,"personnel_total":-2.,
            "continuity_margin":.5,"continuity_total":.2,"weather_total":-4.})
        base=project()
        self.assertAlmostEqual(p["margin"]-base["margin"],4.5)
        self.assertAlmostEqual(p["total"]-base["total"],-5.8)

    def test_swapping_teams_and_venue_changes_margin_sign_and_preserves_total(self):
        f=factors();f["away"]["special_teams"]=-.4;f["home"]["special_teams"]=.6
        a=project(f,adjustments={"home_field":3.})
        f["away"],f["home"]=f["home"],f["away"]
        b=project(f,adjustments={"home_field":-3.})
        self.assertAlmostEqual(a["margin"],-b["margin"])
        self.assertAlmostEqual(a["total"],b["total"])

    def test_clock_source_and_population_checks_fail_closed(self):
        for field,value in (("source_url","other"),("observed_at",KICKOFF),("population_sha256","bad")):
            f=factors();f[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):project(f)
        f=factors();f["national"]["tempo"]["teams"]=50
        with self.assertRaises(ValueError):project(f)

    def test_primary_builder_prices_and_saves_the_new_projection(self):
        builder,game,env=fake_builder()
        install_matchup_projection(builder);install_matchup_projection(builder);install_matchup_grading(builder)
        with patch("builders.cfb_sp_ratings.load_population",return_value={}),patch("builders.cfb_matchup_grades.collect_factors",return_value=factors()):
            r=builder.evaluate_game()
        p=r["projection"]
        self.assertTrue(p["matchup_projection_applied"])
        self.assertEqual((p["away_points"],p["home_points"],p["total"]),(18.5,35.5,54.))
        self.assertEqual(p["matchup_projection_legacy"]["total"],81.)
        self.assertEqual(r["matchup_grades"]["total"]["projected_total"],54.)
        self.assertEqual(r["matchup_grades"]["spread"]["projected_margin"],17.)
        row=builder.slate_row(r).iloc[0]
        self.assertEqual(row["Projected Total"],54.)
        self.assertEqual(row["Legacy Projected Total"],81.)
        self.assertEqual(json.loads(row[FIELD])["model_version"],MODEL_VERSION)
        self.assertEqual(json.loads(row[COMPARISON_FIELD])["projected_total"],54.)
        self.assertEqual(builder.tracker_rows(r).iloc[0]["Projected Home"],35.5)
        self.assertFalse(p["matchup_projection"]["calibrated_probabilities"])

    def test_new_builder_never_grades_a_legacy_fallback_as_a_new_projection(self):
        builder,_,_=fake_builder();install_matchup_projection(builder);install_matchup_grading(builder)
        with patch("builders.cfb_sp_ratings.load_population",side_effect=ValueError("unavailable")):
            r=builder.evaluate_game()
        self.assertFalse(r["projection"]["matchup_projection_applied"])
        self.assertEqual(r["projection"]["projection_engine"],"legacy_fallback")
        self.assertEqual(r["spread"]["grade"],"No Play")
        self.assertEqual(r["total_market"]["grade"],"No Play")
        self.assertEqual(r["moneyline"]["grade"],"No Play")
        self.assertEqual(r["projection"]["total"],81.)

    def test_neutral_site_zeroes_the_full_home_field_adjustment(self):
        builder,game,env=fake_builder();game["Neutral Site"]="True";env.home_field=8.
        install_matchup_projection(builder)
        with patch("builders.cfb_sp_ratings.load_population",return_value={}),patch("builders.cfb_matchup_grades.collect_factors",return_value=factors()):
            r=builder.project_matchup(game,None,None,None,env)
        self.assertEqual(r["margin"],14.)

    def test_old_regression_failure_does_not_replace_complete_matchup_inputs(self):
        builder,game,env=fake_builder()
        def broken(*args,**kwargs):raise ValueError("legacy regression unavailable")
        builder.project_matchup=broken;builder._rating_row=lambda *args:{"Previous Season Weight":0.}
        install_matchup_projection(builder)
        with patch("builders.cfb_sp_ratings.load_population",return_value={}),patch("builders.cfb_matchup_grades.collect_factors",return_value=factors()):
            r=builder.project_matchup(game,None,None,None,env)
        self.assertTrue(r["matchup_projection_applied"])
        self.assertEqual(r["total"],54.)
        self.assertIn("legacy_projection_error",r)

    def test_research_import_checks_the_actual_projection_hash_and_grade_reference(self):
        from builders.cfb_matchup_projection import PROJECTION_POLICY
        f=factors();p=project(f)
        g={"as_of":NOW.isoformat(),"kickoff":KICKOFF,"record_mode":"live","policy_version":PROJECTION_POLICY,
            "source_population_sha256":f["population_sha256"],"projection_model_version":MODEL_VERSION,
            "projection_sha256":p["sha256"],"projected_margin":p["margin"],"projected_total":p["total"],
            "home_line":-3.5,"line":48.5}
        row={"Date":NOW.date().isoformat(),"Game ID":"test","Away Team":"Away","Home Team":"Home","Season":"2026",
            "Model Version":MODEL_VERSION,"Projection Engine":MODEL_VERSION,"Grading Policy Version":PROJECTION_POLICY,
            "Matchup Factors JSON":json.dumps(f),"Matchup Spread JSON":json.dumps(g),"Matchup Total JSON":json.dumps(g),FIELD:json.dumps(p)}
        self.assertEqual(from_saved_row(row)["projected_total"],54.)
        row[FIELD]=json.dumps({**p,"total":99.})
        with self.assertRaises(ValueError):from_saved_row(row)


if __name__=="__main__":unittest.main()
