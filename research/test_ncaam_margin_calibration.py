"""Behavioral checks for calibrated margins and existing publication boundaries."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from builders.ncaam_margin_calibration import calibrate_margin, fitted_margin
from builders.ncaam_model import MODEL_VERSION, project


class MarginCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 3, 20, tzinfo=timezone.utc)
        self.game = {"away": "Away", "home": "Home", "away_aliases": ["Away"], "home_aliases": ["Home"],
                     "season": 2027, "neutral_site": False, "tipoff": (self.now + timedelta(hours=1)).isoformat()}
        self.bundle = {"season": 2027, "observed_at": self.now.isoformat(), "source": "Fixture", "source_url": "fixture",
                       "league_efficiency": 105, "teams": {
                           "away": {"team": "Away", "offense": 97, "defense": 112, "tempo": 70},
                           "home": {"team": "Home", "offense": 120, "defense": 95, "tempo": 70}}}
        self.market = {"home_spread": -25.5, "total": 150.5, "as_of": self.now.isoformat()}

    def calculate(self, *, game=None, bundle=None, market=None, adjustment=None):
        return project(game or self.game, bundle or self.bundle, market or self.market,
                       self.now.isoformat(), lineup_adjustment=adjustment)

    def test_large_mismatch_expands_both_home_and_away_strength(self):
        home = calibrate_margin(25, season=2027, game_date="2026-11-03", neutral_site=False)
        away = calibrate_margin(-25, season=2027, game_date="2026-11-03", neutral_site=False)
        self.assertGreater(home["calibrated_home_margin"], home["source_home_margin"])
        self.assertLess(away["calibrated_home_margin"], away["source_home_margin"])
        self.assertGreater(abs(fitted_margin(-25, neutral_site=True)), 25)
        self.assertAlmostEqual(fitted_margin(25, neutral_site=True), -fitted_margin(-25, neutral_site=True))
        self.assertEqual(fitted_margin(0, neutral_site=True), 0)

    def test_only_evaluated_dates_activate_and_eastern_date_controls_window(self):
        for season, day in [(2026, "2025-11-03"), (2027, "2026-10-31"), (2027, "2026-11-15"), (2028, "2027-11-03")]:
            with self.subTest(season=season, day=day):
                result = calibrate_margin(25, season=season, game_date=day, neutral_site=False)
                self.assertFalse(result["active"])
                self.assertAlmostEqual(result["calibrated_home_margin"], 28.2)
        now = datetime(2026, 11, 15, 2, tzinfo=timezone.utc)
        result = project({**self.game, "tipoff": (now + timedelta(hours=1)).isoformat()},
                         {**self.bundle, "observed_at": now.isoformat()}, {**self.market, "as_of": now.isoformat()}, now.isoformat())
        self.assertEqual(result["margin_calibration"]["game_date"], "2026-11-14")
        self.assertTrue(result["margin_calibration"]["active"])

    def test_scores_and_total_stay_consistent_and_market_independent(self):
        result = self.calculate()
        # Source score total: 70 possessions * (97*95 + 120*112) / 105 / 100.
        self.assertAlmostEqual(result["total"], 151.03, places=2)
        self.assertAlmostEqual(result["home_score"] + result["away_score"], result["total"], delta=0.11)
        self.assertAlmostEqual(result["away_score"] - result["home_score"], result["fair_home_spread"], delta=0.11)
        changed = self.calculate(market={**self.market, "home_spread": 8.5, "total": 180.5})
        for key in ["home_score", "away_score", "fair_home_spread", "total", "possessions", "margin_calibration"]:
            self.assertEqual(result[key], changed[key])
        self.assertEqual(result["lineup_margin_change"], 0)
        self.assertEqual(result["spread_grade"], "Research")
        self.assertEqual(result["total_grade"], "Research")
        self.assertNotIn("spread_probability_estimate", result)
        self.assertIn("v0.5", MODEL_VERSION)

    def test_injury_effect_is_not_multiplied_by_team_margin_calibration(self):
        adjustment = {"away": {"offense_change_per_100": 0, "defense_improvement_per_100": 0},
                      "home": {"offense_change_per_100": -5, "defense_improvement_per_100": 0}}
        adjusted = self.calculate(adjustment=adjustment)
        expected_loss = 70 * 5 * 112 / 105 / 100
        self.assertAlmostEqual(adjusted["lineup_margin_change"], -expected_loss, places=2)
        self.assertAlmostEqual(adjusted["lineup_total_change"], -expected_loss, places=2)
        self.assertEqual(adjusted["margin_calibration"], self.calculate()["margin_calibration"])

    def test_neutral_team_swap_reverses_margin_and_preserves_total(self):
        neutral = {**self.game, "neutral_site": True}
        original = self.calculate(game=neutral)
        swapped = deepcopy(self.bundle)
        swapped["teams"]["away"], swapped["teams"]["home"] = swapped["teams"]["home"], swapped["teams"]["away"]
        swapped["teams"]["away"]["team"] = "Away"
        swapped["teams"]["home"]["team"] = "Home"
        result = self.calculate(game=neutral, bundle=swapped)
        self.assertEqual(result["total"], original["total"])
        self.assertEqual(result["fair_home_spread"], -original["fair_home_spread"])


if __name__ == "__main__":
    unittest.main()
