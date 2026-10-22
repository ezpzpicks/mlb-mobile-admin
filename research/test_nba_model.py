"""Behavioral checks for dated NBA rotation scenarios, not betting backtests."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest

from builders.nba_example import example_package
from builders.nba_model import InputError, evaluate_package, snapshot_json


NOW = datetime(2026, 10, 22, 13, tzinfo=timezone.utc)


def baseline():
    package = example_package(NOW)
    package["scenarios"] = [package["scenarios"][0]]
    for side in ("away", "home"):
        plan = package["scenarios"][0][side]
        plan["starters_confirmed"] = True
        for player in plan["players"]:
            player.update(status="AVAILABLE", assumption="")
    return package


def remove_star(package):
    plan = package["scenarios"][0]["away"]
    plan["players"][0].update(status="OUT", assumption="", minutes=0, starter=False)
    plan["players"][5]["starter"] = True
    for index in (5, 6, 7):
        plan["players"][index]["minutes"] += 12
    return package


class RotationMathTests(unittest.TestCase):
    def test_unchanged_rotation_keeps_baseline(self):
        projection = evaluate_package(baseline())["scenarios"][0]
        self.assertEqual(projection["lineup_margin_change"], 0)
        self.assertEqual(projection["lineup_total_change"], 0)
        self.assertEqual(projection["total"], 230)
        self.assertEqual(projection["home_margin"], 2)

    def test_missing_scorer_and_replacement_minutes_change_fair_spread(self):
        package = example_package(NOW)
        result = evaluate_package(package)
        plays, sits = result["scenarios"]
        self.assertLess(sits["away_score"], plays["away_score"])
        self.assertGreater(sits["home_score"], plays["home_score"])
        self.assertAlmostEqual(sits["lineup_margin_change"], 5.25)
        self.assertAlmostEqual(sits["lineup_total_change"], -3.75)

    def test_offense_only_absence_reduces_only_own_scoring(self):
        package = baseline()
        package["away"]["players"][0]["defensive_impact"] = 0
        before = evaluate_package(package)["scenarios"][0]
        after = evaluate_package(remove_star(package))["scenarios"][0]
        self.assertAlmostEqual(before["away_score"] - after["away_score"], 4.5)
        self.assertEqual(before["home_score"], after["home_score"])

    def test_defender_absence_increases_opponent_scoring(self):
        package = baseline()
        package["away"]["players"][0].update(offensive_impact=0, defensive_impact=4)
        before = evaluate_package(package)["scenarios"][0]
        after = evaluate_package(remove_star(package))["scenarios"][0]
        self.assertEqual(before["away_score"], after["away_score"])
        self.assertAlmostEqual(after["home_score"] - before["home_score"], 3)
        self.assertGreater(after["total"], before["total"])

    def test_better_replacement_reduces_injury_cost(self):
        ordinary = remove_star(baseline())
        better = deepcopy(ordinary)
        better["away"]["players"][5]["offensive_impact"] = 4
        self.assertGreater(evaluate_package(better)["scenarios"][0]["away_score"],
                           evaluate_package(ordinary)["scenarios"][0]["away_score"])

    def test_minutes_limit_has_partial_injury_effect(self):
        package = baseline()
        package["scenarios"][0]["away"]["players"][0].update(minutes=20, minutes_limit=20)
        package["scenarios"][0]["away"]["players"][5]["minutes"] += 16
        limited = evaluate_package(package)["scenarios"][0]
        out = evaluate_package(remove_star(baseline()))["scenarios"][0]
        self.assertGreater(limited["lineup_margin_change"], 0)
        self.assertLess(limited["lineup_margin_change"], out["lineup_margin_change"])

    def test_no_double_penalty_for_player_already_absent_from_baseline(self):
        package = remove_star(baseline())
        package["away"]["players"][0]["reference_minutes"] = 0
        for index in (5, 6, 7):
            package["away"]["players"][index]["reference_minutes"] += 12
        projection = evaluate_package(package)["scenarios"][0]
        self.assertEqual(projection["lineup_margin_change"], 0)
        self.assertEqual(projection["lineup_total_change"], 0)

    def test_bench_player_minutes_matter(self):
        package = baseline()
        package["away"]["players"][5]["offensive_impact"] = 4
        plan = package["scenarios"][0]["away"]["players"]
        plan[5]["minutes"] += 12
        plan[6]["minutes"] -= 12
        self.assertGreater(evaluate_package(package)["scenarios"][0]["away_score"],
                           evaluate_package(baseline())["scenarios"][0]["away_score"])

    def test_home_court_changes_margin_not_total(self):
        home = baseline()
        neutral = deepcopy(home)
        neutral["context"]["home_court_points"] = 0
        hp, np = evaluate_package(home)["scenarios"][0], evaluate_package(neutral)["scenarios"][0]
        self.assertEqual(hp["total"], np["total"])
        self.assertAlmostEqual(hp["home_margin"] - np["home_margin"], 2)

    def test_faster_pace_increases_total(self):
        slow, fast = baseline(), baseline()
        slow["away"]["pace"] = slow["home"]["pace"] = 95
        fast["away"]["pace"] = fast["home"]["pace"] = 105
        self.assertGreater(evaluate_package(fast)["scenarios"][0]["total"],
                           evaluate_package(slow)["scenarios"][0]["total"])

    def test_neutral_side_swap_changes_margin_sign(self):
        package = remove_star(baseline())
        package["context"]["home_court_points"] = 0
        first = evaluate_package(package)["scenarios"][0]
        package["away"], package["home"] = package["home"], package["away"]
        s = package["scenarios"][0]
        s["away"], s["home"] = s["home"], s["away"]
        swapped = evaluate_package(package)["scenarios"][0]
        self.assertAlmostEqual(first["away_score"], swapped["home_score"])
        self.assertAlmostEqual(first["home_margin"], -swapped["home_margin"])
        self.assertAlmostEqual(first["total"], swapped["total"])


class InputValidationTests(unittest.TestCase):
    def assert_invalid(self, package, message):
        with self.assertRaisesRegex(InputError, message):
            evaluate_package(package)

    def test_out_player_cannot_have_minutes(self):
        package = baseline()
        package["scenarios"][0]["away"]["players"][0]["status"] = "OUT"
        self.assert_invalid(package, "out but has projected minutes")

    def test_minutes_cannot_exceed_cap(self):
        package = baseline()
        package["scenarios"][0]["away"]["players"][0]["minutes_limit"] = 20
        self.assert_invalid(package, "exceeds.*minutes limit")

    def test_vacated_minutes_must_be_explicitly_replaced(self):
        package = baseline()
        package["scenarios"][0]["away"]["players"][0].update(status="OUT", minutes=0, starter=False)
        self.assert_invalid(package, "Projected rotation must total 240")

    def test_reference_minutes_must_total_240(self):
        package = baseline()
        package["away"]["players"][0]["reference_minutes"] = 0
        self.assert_invalid(package, "reference rotation must total 240")

    def test_minutes_cannot_exceed_regulation(self):
        package = baseline()
        package["scenarios"][0]["away"]["players"][0]["minutes"] = 49
        self.assert_invalid(package, "between 0 and 48")

    def test_questionable_requires_explicit_scenario(self):
        package = baseline()
        package["scenarios"][0]["away"]["players"][0]["status"] = "QUESTIONABLE"
        self.assert_invalid(package, "choose an explicit IN or OUT")

    def test_probable_is_not_silently_treated_as_confirmed(self):
        package = baseline()
        package["scenarios"][0]["away"]["players"][0]["status"] = "PROBABLE"
        self.assert_invalid(package, "choose an explicit IN or OUT")

    def test_scenarios_cannot_change_observed_injury_report(self):
        package = example_package(NOW)
        package["scenarios"][1]["away"]["players"][0].update(status="OUT", assumption="")
        self.assert_invalid(package, "same observed injury report")

    def test_duplicate_player_ids_rejected(self):
        package = baseline()
        package["away"]["players"][1]["player_id"] = "A1"
        self.assert_invalid(package, "duplicate player IDs")

    def test_player_cannot_appear_on_both_teams(self):
        package = baseline()
        package["home"]["players"][0]["player_id"] = "A1"
        package["scenarios"][0]["home"]["players"][0]["player_id"] = "A1"
        self.assert_invalid(package, "cannot appear on both teams")

    def test_traded_player_can_remain_in_old_reference_sample_at_zero_current_minutes(self):
        package = remove_star(baseline())
        package["home"]["players"][0]["player_id"] = "A1"
        package["scenarios"][0]["home"]["players"][0]["player_id"] = "A1"
        self.assertTrue(evaluate_package(package)["research_only"])

    def test_unknown_replacement_must_be_added_to_reference_roster(self):
        package = baseline()
        package["scenarios"][0]["away"]["players"][0]["player_id"] = "new-player"
        self.assert_invalid(package, "same player IDs")

    def test_missing_impact_does_not_become_zero(self):
        package = baseline()
        del package["away"]["players"][0]["offensive_impact"]
        self.assert_invalid(package, "Incomplete or invalid")

    def test_nan_infinity_and_boolean_metrics_rejected(self):
        for value in (float("nan"), float("inf"), True):
            with self.subTest(value=value):
                package = baseline()
                package["away"]["offensive_rating"] = value
                self.assert_invalid(package, "finite number")

    def test_blank_sources_rejected(self):
        package = baseline()
        package["away"]["impact_source"] = " "
        self.assert_invalid(package, "impact_source is required")

    def test_future_ratings_impacts_reports_and_lines_rejected(self):
        future = (NOW + timedelta(minutes=1)).isoformat()
        for section, field in (("away", "ratings_as_of"), ("home", "impacts_as_of"), ("market", "as_of")):
            with self.subTest(section=section, field=field):
                package = baseline()
                package[section][field] = future
                self.assert_invalid(package, "later than the projection cutoff")
        for field in ("injury_as_of", "lineup_as_of"):
            package = baseline()
            package["scenarios"][0]["away"][field] = future
            self.assert_invalid(package, "later than the projection cutoff")

    def test_naive_datetimes_rejected(self):
        package = baseline()
        package["context"]["as_of"] = "2026-10-22T13:00:00"
        self.assert_invalid(package, "include a timezone")

    def test_post_tipoff_projection_rejected(self):
        package = baseline()
        package["context"]["tipoff"] = package["context"]["as_of"]
        self.assert_invalid(package, "before tipoff")

    def test_boolean_strings_do_not_count_as_confirmations(self):
        package = baseline()
        package["scenarios"][0]["away"]["starters_confirmed"] = "false"
        self.assert_invalid(package, "must be true or false")

    def test_scenario_names_must_be_unique(self):
        package = example_package(NOW)
        package["scenarios"][1]["name"] = package["scenarios"][0]["name"]
        self.assert_invalid(package, "names must be unique")

    def test_confirmed_lineup_requires_five_named_starters(self):
        package = baseline()
        package["scenarios"][0]["away"]["players"][0]["starter"] = False
        self.assert_invalid(package, "exactly five starters")


class NewsAndArchiveTests(unittest.TestCase):
    def test_stale_reports_keep_projection_conditional(self):
        package = baseline()
        old = (NOW - timedelta(hours=2)).isoformat()
        package["scenarios"][0]["away"].update(injury_as_of=old, lineup_as_of=old)
        projection = evaluate_package(package)["scenarios"][0]
        self.assertFalse(projection["lineup_ready"])
        self.assertTrue(any("older than" in f for f in projection["flags"]))

    def test_incomplete_report_does_not_mean_no_injuries(self):
        package = baseline()
        package["scenarios"][0]["away"]["report_complete"] = False
        projection = evaluate_package(package)["scenarios"][0]
        self.assertFalse(projection["lineup_ready"])
        self.assertTrue(any("incomplete" in f for f in projection["flags"]))

    def test_unconfirmed_starters_are_flagged(self):
        package = baseline()
        package["scenarios"][0]["away"]["starters_confirmed"] = False
        self.assertFalse(evaluate_package(package)["scenarios"][0]["lineup_ready"])

    def test_questionable_scenarios_are_conditional_even_with_complete_report(self):
        result = evaluate_package(example_package(NOW))
        self.assertTrue(all(not p["lineup_ready"] for p in result["scenarios"]))

    def test_spread_and_total_direction_flip_across_scenarios(self):
        result = evaluate_package(example_package(NOW))
        self.assertIn("Changes", result["spread_direction"])
        self.assertIn("Changes", result["total_direction"])
        self.assertEqual(result["fair_home_spread_range"], [-7.25, -2.0])
        self.assertNotIn("blended_probability", result)

    def test_single_questionable_scenario_is_not_complete_coverage(self):
        package = example_package(NOW)
        package["scenarios"] = package["scenarios"][:1]
        result = evaluate_package(package)
        self.assertFalse(result["scenario_coverage_complete"])
        self.assertEqual(result["scenario_combinations_required"], 2)

    def test_in_and_out_cover_one_uncertain_player(self):
        self.assertTrue(evaluate_package(example_package(NOW))["scenario_coverage_complete"])

    def test_multiple_uncertain_players_require_joint_combinations(self):
        package = example_package(NOW)
        for scenario in package["scenarios"]:
            scenario["away"]["players"][1].update(status="QUESTIONABLE", assumption="IN")
        result = evaluate_package(package)
        self.assertFalse(result["scenario_coverage_complete"])
        self.assertEqual(result["scenario_combinations_required"], 4)
        self.assertEqual(result["scenario_combinations_entered"], 2)

    def test_market_zero_is_valid_and_favors_home_in_both_scenarios(self):
        package = example_package(NOW)
        package["market"]["home_spread"] = 0
        result = evaluate_package(package)
        self.assertEqual(result["spread_direction"], package["home"]["name"])

    def test_missing_market_does_not_invent_a_line(self):
        package = baseline()
        package["market"] = None
        result = evaluate_package(package)
        self.assertEqual(result["spread_direction"], "No market line")
        self.assertIsNone(result["scenarios"][0]["home_spread_edge"])

    def test_foundation_is_never_eligible_for_posting(self):
        result = evaluate_package(baseline())
        self.assertTrue(result["scenarios"][0]["lineup_ready"])
        self.assertTrue(result["research_only"])
        self.assertFalse(result["eligible_for_posting"])
        self.assertFalse(result["scenarios"][0]["eligible_for_posting"])

    def test_snapshot_preserves_inputs_reports_and_outputs(self):
        package = example_package(NOW)
        result = evaluate_package(package)
        archive = json.loads(snapshot_json(package, result))
        self.assertEqual(archive["inputs"]["scenarios"][1]["away"]["players"][0]["assumption"], "OUT")
        self.assertEqual(archive["inputs"]["scenarios"][0]["away"]["injury_as_of"], NOW.isoformat())
        self.assertEqual(archive["projection"]["snapshot_id"], result["snapshot_id"])

    def test_row_order_does_not_change_snapshot_identity(self):
        package = example_package(NOW)
        first = evaluate_package(package)
        for side in ("away", "home"):
            package[side]["players"].reverse()
            for scenario in package["scenarios"]:
                scenario[side]["players"].reverse()
        package["scenarios"].reverse()
        self.assertEqual(evaluate_package(package)["snapshot_id"], first["snapshot_id"])

    def test_inputs_are_not_mutated(self):
        package = example_package(NOW)
        original = deepcopy(package)
        evaluate_package(package)
        self.assertEqual(package, original)


if __name__ == "__main__":
    unittest.main()
