"""Explicit injury bypass retains roster, market, clock and tracking guards."""
from copy import deepcopy
from datetime import timedelta
import unittest

from builders.nba_automation import (EXPECTED_POLICY, EXPECTED_VERSIONS, POLICY,
    VERSIONS, automated_package)
from builders.nba_live import prepare_live_package
from builders.nba_market import apply_market
from builders.nba_model import InputError, evaluate_package
from builders.nba_tracking import lock_prediction
from nba_market_fixtures import market_rows
from test_nba_live import NOW, fixture


def prepared():
    bundle, inputs, _ = fixture()
    game = inputs["game"]
    package = prepare_live_package(bundle, inputs, None, as_of=NOW, availability_mode="expected_rotation")
    return apply_market(package, market_rows(game, NOW), game, NOW), game


def seal(package, game, *, policy=EXPECTED_POLICY, human=False):
    return lock_prediction(package, package["automation"]["primary_scenario"], game,
        locked_at=NOW, roster_reviewed=human, minutes_reviewed=human,
        review_note=package["automation"]["assumptions"], automation_policy=policy)


class ExpectedRotationTests(unittest.TestCase):
    def test_bypass_is_explicit_and_cannot_masquerade_as_an_injury_report(self):
        bundle, inputs, injury = fixture()
        for report, mode in ((None, "injury_feed"), (injury, "expected_rotation"), (None, "typo")):
            with self.subTest(mode=mode), self.assertRaises(InputError):
                prepare_live_package(bundle, inputs, report, as_of=NOW, availability_mode=mode)

    def test_missing_injuries_remain_missing_and_rotations_use_checked_current_rosters(self):
        package, _ = prepared()
        self.assertNotIn("injury_provenance", package)
        self.assertIs(package["availability_provenance"]["injury_verified"], False)
        for side in ("away", "home"):
            plan = package["scenarios"][0][side]
            self.assertIsNone(plan["injury_as_of"])
            self.assertFalse(plan["report_complete"])
            self.assertFalse(plan["starters_confirmed"])
            self.assertAlmostEqual(sum(p["minutes"] for p in plan["players"]), 240)
            current = set(package["live_provenance"]["identity"][side].values())
            for player in plan["players"]:
                self.assertEqual(player["status"], "UNKNOWN")
                self.assertIsNone(player["injury"])
                self.assertLessEqual(player["minutes"], player["minutes_limit"])
                if player["minutes"]:
                    self.assertIn(player["player_id"], current)

    def test_saved_forecast_is_flagged_and_uses_a_separate_version_with_market_grades(self):
        package, game = prepared()
        package = automated_package(package, "early")
        record = seal(package, game)
        self.assertEqual(record["model_version"], EXPECTED_VERSIONS["early"])
        self.assertNotIn(record["model_version"], VERSIONS.values())
        self.assertIs(record["selected"]["injury_verified"], False)
        self.assertEqual(record["selected"]["availability_mode"], "expected_rotation")
        self.assertFalse(record["selected"]["lineup_ready"])
        self.assertFalse(record["eligible_for_posting"])
        self.assertTrue(any("availability unchecked" in f for f in record["selected"]["flags"]))
        self.assertEqual(record["selected"]["market"]["source"], "ScoresAndOdds")
        for field in ("spread_grade", "total_grade"):
            self.assertIn(record["selected"][field], {"A", "B", "Lean", "Pass"})
        self.assertEqual(len(record["projection"]["scenarios"]), 1)

    def test_expected_and_injury_aware_policy_cannot_be_interchanged(self):
        package, game = prepared()
        package = automated_package(package, "early")
        with self.assertRaises(InputError):
            seal(package, game, policy=POLICY)
        with self.assertRaisesRegex(InputError, "dedicated"):
            seal(package, game, policy=None, human=True)
        with self.assertRaisesRegex(InputError, "human"):
            seal(package, game, human=True)

    def test_bypass_cannot_claim_clearance_confirmed_starters_or_a_report_timestamp(self):
        original, _ = prepared()
        for change in ("status", "timestamp", "complete", "verified"):
            package = deepcopy(original)
            plan = package["scenarios"][0]["away"]
            if change == "status": plan["players"][0]["status"] = "AVAILABLE"
            elif change == "timestamp": plan["injury_as_of"] = NOW.isoformat()
            elif change == "complete": plan["report_complete"] = True
            else: package["availability_provenance"]["injury_verified"] = True
            with self.subTest(change=change), self.assertRaises(InputError):
                automated_package(package, "early")

    def test_a_missing_report_cannot_be_marked_complete_in_the_model(self):
        package, _ = prepared()
        package["scenarios"][0]["away"]["report_complete"] = True
        with self.assertRaisesRegex(InputError, "cannot be marked complete"):
            evaluate_package(package)

    def test_bypass_still_rejects_stale_rosters_missing_markets_and_live_games(self):
        original, game = prepared()
        for change in ("roster", "market", "live"):
            package, current = deepcopy(original), deepcopy(game)
            if change == "roster":
                package["live_provenance"]["rosters"]["away"]["source"]["observed_at"] = (NOW - timedelta(days=2)).isoformat()
            elif change == "market": package["market_provenance"]["rows"] = []
            else: current.update(status="STATUS_IN_PROGRESS", state="in", period=1)
            package = automated_package(package, "early")
            with self.subTest(change=change), self.assertRaises(InputError):
                seal(package, current)


if __name__ == "__main__":
    unittest.main()
