"""Market provenance, no-line save guards and shared grade sign conventions."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import unittest

from builders.nba_market import apply_market, market_for_game, market_comparison, MARGIN_SIGMA, TOTAL_SIGMA
from builders.nba_model import InputError, Market, evaluate_package
from builders.nba_automation import automated_package, POLICY, NOTE
from builders.nba_tracking import lock_prediction
from test_nba_live import NOW, live_package
from nba_market_fixtures import market_rows


class NbaMarketTests(unittest.TestCase):
    def setUp(self):
        self.package, self.game = live_package()
        self.rows = market_rows(self.game, NOW)

    def test_full_market_pair_is_bound_to_game_with_raw_provenance_and_default_odds(self):
        p = apply_market(self.package, self.rows, self.game, NOW)
        self.assertEqual(p["market"]["home_spread"], -3)
        self.assertEqual(p["market"]["total"], 230)
        self.assertEqual(p["market_provenance"]["rows"], self.rows)
        self.assertEqual(p["market_provenance"]["assumed_odds"], -110)
        self.assertIsNone(self.package["market"])
        projected = evaluate_package(p)["scenarios"][0]
        self.assertEqual(projected["home_spread_edge"], projected["home_margin"] - 3)
        self.assertEqual(projected["total_edge"], projected["total"] - 230)

    def test_missing_duplicate_and_inconsistent_lines_cannot_become_a_market(self):
        for change in ("missing", "duplicate", "spread", "total", "source", "policy", "capture", "status", "negative"):
            rows = deepcopy(self.rows)
            if change == "missing": rows.pop()
            elif change == "duplicate": rows[1] = deepcopy(rows[0])
            elif change == "spread": rows[0]["Line"] = "4"
            elif change == "total": rows[2]["Line"] = "231"
            elif change == "negative": rows[2]["Line"] = rows[3]["Line"] = "-1"
            elif change == "source": rows[0]["Source"] = "another source"
            elif change == "policy": rows[0]["Market Policy"] = "old policy"
            elif change == "capture": rows[0]["Updated At"] = (NOW-timedelta(minutes=1)).isoformat()
            else: rows[0]["Snapshot Status"] = "FINAL_PREGAME"
            with self.subTest(change=change), self.assertRaises(InputError):
                market_for_game(rows, self.game, NOW)

    def test_wrong_identity_date_kickoff_and_future_quote_are_rejected(self):
        for change in ("id", "date", "team", "tipoff", "future", "stale"):
            rows = deepcopy(self.rows)
            for row in rows:
                if change == "id": row["Game ID"] = "999"
                elif change == "date": row["Date"] = "2026-10-23"
                elif change == "team": row["Away Team"] = "Wrong Team"
                elif change == "tipoff": row["Game Time"] = (NOW+timedelta(hours=3)).isoformat()
                elif change == "future": row["Updated At"] = (NOW+timedelta(minutes=1)).isoformat()
                else: row["Updated At"] = (NOW-timedelta(minutes=36)).isoformat()
            with self.subTest(change=change), self.assertRaises(InputError): market_for_game(rows, self.game, NOW)

    def test_countdown_requires_a_fresh_live_quote_and_valid_final_window(self):
        now = NOW + timedelta(minutes=90)
        for row in self.rows: row["Updated At"] = (now-timedelta(minutes=11)).isoformat()
        with self.assertRaisesRegex(InputError, "90-minute"): market_for_game(self.rows, self.game, now)
        now = NOW + timedelta(minutes=115)
        for row in self.rows:
            row["Snapshot Status"] = "FINAL_PREGAME"
            row["Updated At"] = (NOW+timedelta(minutes=103)).isoformat()  # 17 minutes before tipoff
        self.assertEqual(market_for_game(self.rows, self.game, now)[1]["snapshot_status"], "FINAL_PREGAME")
        for row in self.rows: row["Updated At"] = (NOW+timedelta(minutes=95)).isoformat()
        with self.assertRaisesRegex(InputError, "10–20"): market_for_game(self.rows, self.game, now)

    def test_changed_market_cannot_be_locked_under_the_automatic_policy(self):
        p = apply_market(self.package, self.rows, self.game, NOW)
        p = automated_package(p, "early")
        p["market"]["total"] += 1
        with self.assertRaisesRegex(InputError, "original complete"):
            lock_prediction(p, p["scenarios"][0]["name"], self.game, locked_at=NOW,
                roster_reviewed=False, minutes_reviewed=False, review_note=NOTE, automation_policy=POLICY)

    def test_shared_grades_use_validation_not_test_errors_and_show_research_status(self):
        report = json.loads((Path(__file__).parent / "nba_results_2025_26/nba_validation.json").read_text())
        metrics = report["selected"]["metrics"]["lineup"]
        self.assertEqual(MARGIN_SIGMA, metrics["margin_rmse"])
        self.assertEqual(TOTAL_SIGMA, metrics["total_rmse"])
        market = Market(-3, 230, NOW, "ScoresAndOdds")
        stronger = market_comparison(9, 240, "Away", "Home", market)
        self.assertEqual(stronger["spread_pick"], "Home -3")
        self.assertEqual(stronger["total_pick"], "Over 230")
        self.assertEqual(stronger["spread_grade"], "A")
        self.assertFalse(stronger["probabilities_calibrated"])
        opposite = market_comparison(-4, 220, "Away", "Home", market)
        self.assertEqual(opposite["spread_pick"], "Away +3")
        self.assertEqual(opposite["total_pick"], "Under 230")
        self.assertEqual(market_comparison(3, 230, "Away", "Home", market)["spread_grade"], "Pass")
        self.assertEqual(market_comparison(3, 230, "Away", "Home", None)["spread_grade"], "No market line")


if __name__ == "__main__": unittest.main()
