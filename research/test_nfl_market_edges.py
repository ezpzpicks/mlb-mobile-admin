"""Settlement, chronology and probability integrity checks for NFL research."""
from __future__ import annotations

import unittest
import json
from pathlib import Path
import tempfile
import numpy as np
import pandas as pd

from research import nfl_market_edges as model
from research import nfl_saved_spread_audit as audit


def game_fixture() -> pd.DataFrame:
    return pd.DataFrame({"game_id": ["g1", "g2", "g3"], "season": [2025] * 3, "week": [1, 2, 3],
                         "gameday": ["2025-09-07", "2025-09-14", "2025-09-21"],
                         "home_team": ["BUF"] * 3, "away_team": ["KC"] * 3,
                         "market_margin": [3.0, 3.0, 3.0], "actual_residual": [1.0, -1.0, 0.0],
                         "home_odds": [-110.0] * 3, "away_odds": [-110.0] * 3})


def unit_fixture() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = {name: 0.0 for name in model.OFF + model.DEF}
    base.update({"off_scoring": 24.0, "def_scoring_allowed": 24.0, "off_success": 0.43,
                 "off_explosive": 0.105, "off_turnover": 0.024, "off_sacks_allowed": 0.067,
                 "off_red_zone": 0.56, "off_pace": 64, "def_pace": 64})
    common = {"game_id": "2025_01_KC_BUF", "season": 2025, "week": 1, "gameday": "2025-09-07",
              "market_margin": 3.5, "market_total": 44.5, "rating_current_weight": 0.0,
              "input_cutoff": "2025-09-07", "max_current_input_date": "2025-01-05"}
    home = {**base, **common, "side": "home", "team": "BUF", "points": 27.0, "production_core": 25.0,
            "off_scoring": 28.0, "off_pass_epa": 0.1, "def_pass_epa_edge": -0.1, "def_scoring_allowed": 24.0}
    away = {**base, **common, "side": "away", "team": "KC", "points": 21.0, "production_core": 22.0,
            "off_pass_epa": 0.1, "def_pass_epa_edge": 0.1, "def_scoring_allowed": 20.0}
    schedule = pd.DataFrame({"game_id": [common["game_id"]], "spread_line": [3.5], "home_score": [27.0],
                             "away_score": [21.0], "home_spread_odds": [-110.0], "away_spread_odds": [-110.0],
                             "home_rest": [7], "away_rest": [7], "location": ["Home"]})
    shares = pd.DataFrame({"season": [2025, 2025], "week": [1, 1], "team": ["BUF", "KC"], "pass_share": [0.7, 0.5]})
    return pd.DataFrame([home, away]), schedule, shares


class MarketEdgeIntegrityTests(unittest.TestCase):
    def test_american_prices_and_even_money_without_division_warning(self):
        with np.errstate(divide="raise", invalid="raise"):
            np.testing.assert_allclose(model.implied([-110, 120, -100, 100]), [110 / 210, 100 / 220, 0.5, 0.5])
            np.testing.assert_allclose(model.payout([-110, 120, -100, 100]), [100 / 110, 1.2, 1.0, 1.0])
        for values in [[0], [99], [np.nan], [np.inf]]:
            with self.assertRaises(ValueError): model.implied(values)

    def test_correct_home_and_away_handicaps_and_push_refund(self):
        data = game_fixture()
        home = model.select_bets(data, np.array([0.65] * 3), 0.02)
        self.assertEqual(home.result.tolist(), ["W", "L", "P"])
        np.testing.assert_allclose(home.handicap, [-3.0] * 3)
        np.testing.assert_allclose(home.units, [100 / 110, -1, 0])
        away = model.select_bets(data, np.array([0.35] * 3), 0.02)
        self.assertEqual(away.result.tolist(), ["L", "W", "P"])
        np.testing.assert_allclose(away.handicap, [3.0] * 3)
        metrics = model.economic_metrics(home)
        self.assertEqual((metrics["wins"], metrics["losses"], metrics["pushes"]), (1, 1, 1))
        self.assertAlmostEqual(metrics["win_rate_decided"], 0.5)

    def test_bet_gate_uses_price_break_even_not_no_vig_fifty_percent(self):
        data = game_fixture().iloc[:1]
        self.assertEqual(len(model.select_bets(data, np.array([0.54]), 0.02)), 0)
        self.assertEqual(len(model.select_bets(data, np.array([0.55]), 0.02)), 1)

    def test_no_qualifying_rule_passes_every_game(self):
        bets = model.select_bets(game_fixture(), np.array([0.9, 0.1, 0.9]), None)
        self.assertEqual(len(bets), 0)
        self.assertEqual(model.economic_metrics(bets)["units"], 0)

    def test_invalid_probability_is_rejected(self):
        for values in [np.array([1.1] * 3), np.array([np.nan] * 3), np.array([0.5])]:
            with self.assertRaises(ValueError): model.select_bets(game_fixture(), values, 0.02)

    def test_current_week_passes_do_not_enter_own_week_forecast(self):
        counts = pd.DataFrame({"week": [1, 2], "team": ["BUF", "BUF"], "valid": [100, 100], "dropbacks": [60, 90]})
        prior = pd.Series({"BUF": 0.5})
        before = model.blend_share(counts, prior, 2, "BUF", 0.15)
        changed = counts.copy(); changed.loc[1, "dropbacks"] = 0
        self.assertAlmostEqual(before, 0.515)
        self.assertEqual(before, model.blend_share(changed, prior, 2, "BUF", 0.15))
        self.assertNotEqual(model.blend_share(counts, prior, 3, "BUF", 0.3), model.blend_share(changed, prior, 3, "BUF", 0.3))
        self.assertEqual(model.blend_share(counts.iloc[:0], prior, 1, "BUF", 0.0), 0.5)

    def test_unit_roles_and_style_interactions_are_not_swapped(self):
        units, schedule, shares = unit_fixture()
        row = model.features_from_units(units, schedule, shares).iloc[0]
        self.assertEqual(row.off_scoring_diff, 4.0)
        self.assertEqual(row.def_scoring_allowed_diff, -4.0)
        self.assertAlmostEqual(row.pass_matchup_style, 0.14)
        self.assertEqual(row.actual_residual, 2.5)
        self.assertEqual(row.home_cover, 1.0)

    def test_future_inputs_and_mismatched_line_are_rejected(self):
        units, schedule, shares = unit_fixture()
        bad = units.copy(); bad.loc[0, "max_current_input_date"] = "2025-09-07"
        with self.assertRaisesRegex(ValueError, "chronology"):
            model.features_from_units(bad, schedule, shares)
        schedule.loc[0, "spread_line"] *= -1
        with self.assertRaisesRegex(ValueError, "sign"):
            model.features_from_units(units, schedule, shares)

    def test_calibration_is_monotone_and_does_not_see_later_labels(self):
        raw = np.array([0.3, 0.4, 0.5, 0.6, 0.7] * 4)
        y = pd.Series([0, 0, 1, 1, 1] * 4, dtype=float)
        calibration = model.fit_calibration(raw, y)
        self.assertGreaterEqual(calibration["slope"], 0)
        result = model.calibrate(np.array([0.1, 0.5, 0.9]), calibration)
        self.assertTrue(np.all(np.diff(result) >= 0))
        self.assertTrue(np.all((result > 0) & (result < 1)))

    def test_frozen_base_prediction_ignores_evaluation_outcomes(self):
        rng = np.random.default_rng(7)
        data = pd.DataFrame(rng.normal(size=(120, len(model.MARKET))), columns=model.MARKET)
        data["actual_residual"] = rng.normal(size=120)
        data["home_cover"] = rng.integers(0, 2, size=120)
        fitted = model.fit_base(data.iloc[:80], "market_logit", 0.1)
        before = model.raw_probability(fitted, data.iloc[80:])
        changed = data.iloc[80:].copy(); changed["home_cover"] = 1 - changed.home_cover; changed["actual_residual"] += 1000
        np.testing.assert_allclose(before, model.raw_probability(fitted, changed), rtol=0, atol=1e-14)
        scaler = fitted["model"].steps[0][1]
        np.testing.assert_allclose(scaler.mean_, data.iloc[:80][list(model.MARKET)].mean().to_numpy())

    def test_missing_audit_number_is_not_silently_zero(self):
        self.assertIsNone(audit.number("")); self.assertIsNone(audit.number(None))
        self.assertEqual(audit.number("0"), 0.0)

    def test_saved_ledger_is_settled_independently_and_mismatch_reported(self):
        schedule = pd.DataFrame({"game_id": ["2025_01_KC_BUF"], "home_team": ["BUF"], "away_team": ["KC"],
                                 "home_score": [24], "away_score": [21], "season": [2025], "week": [1],
                                 "gameday": ["2025-09-07"], "gametime": ["13:00"]}).set_index("game_id")
        raw = [{"Bet Type": "Spread", "Game ID": "2025_01_KC_BUF", "Selection": "BUF -3", "Odds/Line": "-110", "Result": "Win", "Model Probability": "0.6"}]
        rows, errors = audit.settle_ledger(raw, schedule)
        self.assertEqual(rows.iloc[0].result, "P")
        self.assertEqual(rows.iloc[0].units, 0.0)
        self.assertEqual(len(errors), 1)
        self.assertIn("disagrees", errors[0]["reason"])
        self.assertEqual(rows.iloc[0].capture_timing, "unknown")

    def test_capture_timing_and_aliases(self):
        game = pd.Series({"gameday": "2026-11-15", "gametime": "13:00"})
        self.assertEqual(audit.capture_timing({"Build At": "2026-11-15T17:00:00Z"}, game), "pregame")
        self.assertEqual(audit.capture_timing({"Build At": "2026-11-15T19:00:00Z"}, game), "after_kickoff")
        self.assertEqual(audit.identity("2026_01_SF_LA"), "2026_01_SF_LAR")
        self.assertEqual(audit.selection("LA −3.5"), ("LAR", -3.5))

    def test_numeric_saved_id_matches_only_exact_date_and_team_pair(self):
        official = pd.DataFrame({"game_id": ["2025_01_KC_BUF"], "gameday": ["2025-09-07"],
                                 "home_team": ["BUF"], "away_team": ["KC"]}).set_index("game_id")
        row = {"Game ID": "1234567", "Date": "2025-09-07", "Away Team": "KC", "Home Team": "BUF"}
        self.assertEqual(audit.match_official(row, official), "2025_01_KC_BUF")
        row["Date"] = "2025-09-14"
        self.assertIsNone(audit.match_official(row, official))

    def test_saved_selection_recovers_line_and_invalid_duplicate_cannot_replace_forecast(self):
        official = pd.DataFrame({"game_id": ["2025_01_KC_BUF"], "gameday": ["2025-09-07"],
                                 "home_team": ["BUF"], "away_team": ["KC"], "home_score": [24], "away_score": [21],
                                 "season": [2025], "week": [1], "gametime": ["13:00"]}).set_index("game_id")
        row = {"Game ID": "1234567", "Date": "2025-09-07", "Away Team": "KC", "Home Team": "BUF",
               "Projected Home": "24", "Projected Away": "22", "Projected Margin": "2", "Projected Total": "46",
               "Spread Pick": "KC +3.5", "Spread Grade": "A Spread", "Spread Probability": "0.6", "Model Version": "test"}
        bad = {"Game ID": "2025_01_KC_BUF", "Projected Home": ""}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "slate.json"
            path.write_text(json.dumps({"slateToday": [row, bad]}))
            records, exclusions = audit.saved_forecasts([path], official)
        self.assertEqual(len(records), 1)
        self.assertEqual(exclusions, [])
        self.assertEqual(records.iloc[0].market_home_handicap, -3.5)
        self.assertEqual(records.iloc[0].result, "W")
        self.assertTrue(records.iloc[0].line_recovered_from_saved_selection)


if __name__ == "__main__":
    unittest.main()
