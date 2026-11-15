"""Market sensitivity, paired alternatives and chronological isolation."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from builders.cfb_market_relative_matchups import FIELD, SOURCE_URL, comparison, from_saved_row
from builders.cfb_matchup_grades import install_matchup_grading
from research.ncaaf_market_relative_audit import audit, frozen_games, join_outcomes, walk_forward

START = datetime(2026, 10, 1, 16, tzinfo=timezone.utc)


def factors():
    return {"away":{"team":"Away","offense":20.,"defense":35.,"tempo":11.},
        "home":{"team":"Home","offense":40.,"defense":17.,"tempo":13.},
        "observed_at":(START-timedelta(hours=1)).isoformat(),"source_url":SOURCE_URL,
        "season":2026,"population_sha256":"a"*64,
        "national":{m:{"mean":mean,"sd":sd,"teams":138} for m,mean,sd in
                    (("offense",27.,7.),("defense",27.,7.),("tempo",12.,1.))}}


def context(**kwargs):
    values = dict(projected_margin=14.,projected_total=42.,home_line=-10.5,total_line=49.5,
        as_of=START.isoformat(),kickoff=(START+timedelta(hours=3)).isoformat())
    values.update(kwargs)
    return comparison(factors(),**values)


def candidate(game,side):
    return next(c for c in game["alternatives"] if c["side"]==side)


def saved_row():
    f=factors()
    common={"as_of":START.isoformat(),"published_at":START.isoformat(),
        "kickoff":(START+timedelta(hours=3)).isoformat(),"record_mode":"live",
        "source_population_sha256":f["population_sha256"],"policy_version":"test"}
    return {"Date":"2026-10-01","Game ID":"1","Away Team":"Away","Home Team":"Home","Season":"2026",
        "Grading Policy Version":"test","Model Version":"frozen-model",
        "Matchup Factors JSON":json.dumps(f),
        "Matchup Spread JSON":json.dumps({**common,"projected_margin":14.,"home_line":-10.5}),
        "Matchup Total JSON":json.dumps({**common,"projected_total":42.,"line":49.5})}


def historical_game(i):
    day=START+timedelta(days=i)
    f=factors();f["observed_at"]=(day-timedelta(hours=1)).isoformat()
    g=comparison(f,projected_margin=14.+i/10,projected_total=42.+i/10,
        home_line=-10.5,total_line=49.5,as_of=day.isoformat(),kickoff=(day+timedelta(hours=3)).isoformat())
    return {**g,"date":day.date().isoformat(),"game_id":str(i),
        "outcome_observed_at":(day+timedelta(hours=7)).isoformat(),
        "actual_margin":12.+i%3,"actual_total":40.+i%7}


class MarketRelativeTests(unittest.TestCase):
    def test_line_changes_reverse_the_bet_without_changing_the_matchup(self):
        a,b=context(),context(home_line=-20.5,total_line=38.5)
        self.assertEqual(candidate(a,"home")["edge_points"],3.5)
        self.assertEqual(candidate(b,"home")["edge_points"],-6.5)
        self.assertEqual(candidate(a,"Under")["edge_points"],7.5)
        self.assertEqual(candidate(b,"Under")["edge_points"],-3.5)
        self.assertEqual(candidate(a,"home")["features"],candidate(b,"home")["features"])

    def test_weaker_underdog_can_have_positive_edge(self):
        g=context(home_line=-20.5)
        dog=candidate(g,"away")
        self.assertEqual((dog["role"],dog["edge_points"]),("Underdog",6.5))
        self.assertLess(dog["features"]["offense_vs_defense_z"],0)
        self.assertLess(dog["features"]["defense_vs_offense_z"],0)
        self.assertTrue(dog["projection_support"])

    def test_both_market_sides_are_exact_opposites_and_no_win_rate_is_invented(self):
        g=context()
        for a,b in (("home","away"),("Over","Under")):
            self.assertEqual(candidate(g,a)["edge_points"],-candidate(g,b)["edge_points"])
        for key,value in candidate(g,"Over")["features"].items():
            self.assertEqual(value,-candidate(g,"Under")["features"][key])
        for c in g["alternatives"]:
            self.assertIsNone(c["probability"]);self.assertIsNone(c["win_rate"])
            self.assertFalse(c["calibrated"])

    def test_team_swap_preserves_the_physical_bet(self):
        a=context();f=factors();f["home"],f["away"]=f["away"],f["home"]
        b=comparison(f,projected_margin=-14,projected_total=42,home_line=10.5,total_line=49.5,
            as_of=START.isoformat(),kickoff=(START+timedelta(hours=3)).isoformat())
        for key in ("edge_points","features","line","role"):
            self.assertEqual(candidate(a,"home")[key],candidate(b,"away")[key])
        self.assertEqual(candidate(a,"Over")["edge_points"],candidate(b,"Over")["edge_points"])

    def test_tempo_uses_the_same_scale_for_favorites_and_underdogs(self):
        g=context();a=candidate(g,"home")["features"]["role_tempo_z"]
        self.assertEqual(a,-candidate(g,"away")["features"]["role_tempo_z"])
        f=factors();f["home"]["tempo"]=10;f["away"]["tempo"]=11
        slow=comparison(f,projected_margin=14,projected_total=42,home_line=-10.5,total_line=49.5,
            as_of=START.isoformat(),kickoff=(START+timedelta(hours=3)).isoformat())
        self.assertGreater(candidate(slow,"away")["features"]["role_tempo_z"],0)
        self.assertLess(candidate(slow,"home")["features"]["role_tempo_z"],0)

    def test_clocks_sources_and_numeric_inputs_fail_closed(self):
        for kw in ({"as_of":(START+timedelta(hours=3)).isoformat()}, {"as_of":"2026-10-01T16:00:00"},
                   {"as_of":(START+timedelta(days=9)).isoformat(),"kickoff":(START+timedelta(days=10)).isoformat()},
                   {"projected_margin":float("nan")},{"home_line":True},{"odds":{"home":-90}}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):context(**kw)

    def test_original_paired_lines_are_used_even_if_a_board_quote_changes(self):
        row=saved_row();a=from_saved_row(row)
        row.update({"Market Home Spread":"-28.5","Market Total":"70.5","Result":"Win","Actual Home":"99"})
        self.assertEqual(from_saved_row(row),a)
        self.assertEqual(a["home_line"],-10.5)
        row[FIELD]=json.dumps({**a,"projected_total":99})
        with self.assertRaises(ValueError):from_saved_row(row)

    def test_saved_publication_population_and_team_checks_are_enforced(self):
        for field,value in (("Home Team","Different"),("Season","2025"),("Grading Policy Version","other")):
            row=saved_row();row[field]=value
            with self.assertRaises(ValueError):from_saved_row(row)
        row=saved_row();g=json.loads(row["Matchup Total JSON"])
        g["published_at"]=g["kickoff"];row["Matchup Total JSON"]=json.dumps(g)
        with self.assertRaises(ValueError):from_saved_row(row)

    def test_duplicate_snapshots_do_not_multiply_the_game_count(self):
        games,rejected=frozen_games([{"slateToday":[saved_row(),saved_row()]}])
        self.assertEqual((len(games),len(rejected)),(1,0))

    def test_ambiguous_or_pregame_final_scores_are_rejected(self):
        g=from_saved_row(saved_row())
        outcome={"date":g["date"],"game_id":g["game_id"],"away_team":"Away","home_team":"Home",
            "away_score":21,"home_score":28,"observed_at":START.isoformat()}
        with self.assertRaises(ValueError):join_outcomes([g],[outcome])
        outcome.update(observed_at=(START+timedelta(hours=7)).isoformat(),home_team="Wrong")
        with self.assertRaises(ValueError):join_outcomes([g],[outcome])

    def test_walk_forward_never_uses_a_test_result_or_a_later_observation(self):
        games=[historical_game(i) for i in range(8)]
        result=walk_forward(games,min_training_games=3,min_training_dates=3)
        self.assertEqual(result["summary"]["Spread"]["held_out_games"],5)
        first=deepcopy(result["predictions"][:2])
        games[3]["actual_margin"]=999;games[3]["actual_total"]=999
        changed=walk_forward(games,min_training_games=3,min_training_dates=3)
        for old,new in zip(first,changed["predictions"][:2]):
            for key in ("baseline_residual","extended_residual","training_games"):
                self.assertEqual(old[key],new[key])
        for g in games:g["outcome_observed_at"]=(START+timedelta(days=20)).isoformat()
        self.assertEqual(walk_forward(games,min_training_games=3,min_training_dates=3)["predictions"],[])

    def test_favorite_and_over_counts_are_not_forced_to_half(self):
        result=audit([{"slateToday":[saved_row()]}],[])
        self.assertEqual(result["edge_counts"]["Favorite"]["positive_projection_edge"],1)
        self.assertEqual(result["edge_counts"]["Underdog"]["positive_projection_edge"],0)
        self.assertEqual(result["edge_counts"]["Under"]["positive_projection_edge"],1)
        self.assertEqual(result["alternatives"],4)

    def test_changed_point_model_does_not_inherit_old_training_results(self):
        games=[historical_game(i) for i in range(8)]
        games[-1]["model_version"]="changed-model"
        result=walk_forward(games,min_training_games=3,min_training_dates=3)
        self.assertFalse(any(p["game_id"]==games[-1]["game_id"] for p in result["predictions"]))

    def test_builder_saves_all_four_alternatives_without_changing_grades_or_forecasts(self):
        start=datetime.now(timezone.utc);kickoff=(start+timedelta(hours=3)).isoformat()
        f=factors();f["observed_at"]=(start-timedelta(minutes=5)).isoformat()
        result={"game":{"Season":2026,"Week":12,"Game Time":kickoff,"Home Spread":-10.5,"Total":49.5},
            "projection":{"margin":14.,"total":42.},"simulation":{"away_mean":14.,"home_mean":28.},
            "spread":{"pick":"Home -10.5","price_edge":.1,"ev":.1},
            "total_market":{"pick":"Under 49.5","price_edge":.1,"ev":.1},
            "market_availability":{"spread":True,"total":True}}
        builder=SimpleNamespace(SLATE_COLUMNS=[],TRACKER_COLUMNS=[],MODEL_VERSION="test",
            evaluate_game=lambda:deepcopy(result),slate_row=lambda r:pd.DataFrame([{"Game ID":"1"}]),
            tracker_rows=lambda r:pd.DataFrame())
        install_matchup_grading(builder)
        with patch("builders.cfb_matchup_grades.load_population",return_value={}),patch("builders.cfb_matchup_grades.collect_factors",return_value=f):
            evaluated=builder.evaluate_game()
        self.assertEqual(evaluated["simulation"],result["simulation"])
        self.assertEqual(evaluated["projection"]["margin"],14.)
        snapshot=json.loads(builder.slate_row(evaluated).iloc[0][FIELD])
        self.assertEqual(len(snapshot["alternatives"]),4)
        self.assertEqual(snapshot["status"],"Research")
        self.assertTrue(all(c["probability"] is None for c in snapshot["alternatives"]))


if __name__=="__main__":
    unittest.main()
