from copy import deepcopy
import unittest

from builders.ncaam_matchup_grades import (
    grade_matchup, grade_saved_forecast, load_policy, market_reference, select_benchmark,
)
from research.replay_ncaam_matchup_grades import latest_forecasts, settle

NOW = "2026-11-11T17:00:00+00:00"
TIP = "2026-11-11T19:00:00+00:00"


class MatchupGradeTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()
        self.benchmark = {"season": 2027, "team_count": 365, "observed_at": "2026-11-11T16:00:00+00:00",
                          "metrics": {"offense": {"mean": 105, "sd": 5}, "defense": {"mean": 105, "sd": 5}, "tempo": {"mean": 71, "sd": 2}}}
        self.reference = {"low_total": 140, "high_total": 170, "quote_count": 100, "observed_at": "2026-11-10T22:00:00+00:00"}
        self.over = {"offense": 120, "defense": 115, "tempo": 76}
        self.neutral = {"offense": 110, "defense": 108, "tempo": 71}
        self.under = {"offense": 100, "defense": 95, "tempo": 66}

    def calculate(self, away=None, home=None, line=155, **changes):
        kwargs = {"as_of": NOW, "tipoff": TIP, "quote_observed_at": NOW, "season": 2027, "policy": self.policy}
        kwargs.update(changes)
        return grade_matchup(away or self.over, home or self.neutral, line, self.benchmark, self.reference, **kwargs)

    def test_three_factors_select_correct_direction(self):
        over = self.calculate()
        self.assertEqual((over["side"], over["grade"]), ("Over", "A"))
        under = self.calculate(self.under, self.neutral)
        self.assertEqual((under["side"], under["grade"]), ("Under", "A"))
        self.assertEqual(under["directions"]["Under"]["profile_id"], "under-same-team-three")

    def test_price_changes_grade_without_changing_team_ratings(self):
        high = self.calculate(line=175)
        self.assertEqual((high["side"], high["grade"]), ("Over", "B"))
        self.assertIn("market-total-headwind", [c["id"] for c in high["directions"]["Over"]["conflicts"]])
        low = self.calculate(self.under, self.neutral, line=135)
        self.assertEqual((low["side"], low["grade"]), ("Under", "B"))

    def test_opposing_profiles_are_not_counted_as_two_bets(self):
        result = self.calculate(self.over, self.under)
        self.assertIsNone(result["side"])
        self.assertEqual(result["grade"], "Research")
        self.assertEqual(result["reason"], "Opposing directions have equal grades")

    def test_no_average_level_signal_is_not_graded(self):
        average = {"offense": 105, "defense": 105, "tempo": 71}
        result = self.calculate(average, average)
        self.assertIsNone(result["side"])
        self.assertEqual(result["grade"], "Research")

    def test_profiles_and_price_penalties_are_configurable(self):
        self.policy["market_context"]["downgrade_levels"] = 0
        self.assertEqual(self.calculate(line=175)["grade"], "A")
        for p in self.policy["profiles"]:
            p["enabled"] = False
        self.assertEqual(self.calculate()["grade"], "Research")

    def test_future_stale_and_wrong_season_inputs_rejected(self):
        for field, value in [("as_of", TIP), ("quote_observed_at", "2026-11-11T17:01:00+00:00"),
                             ("quote_observed_at", "2026-11-11T14:00:00+00:00"), ("season", 2026)]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.calculate(**{field: value})
        self.benchmark["observed_at"] = "2026-11-11T17:01:00+00:00"
        with self.assertRaises(ValueError): self.calculate()
        self.benchmark["observed_at"] = "2026-11-09T17:00:00+00:00"
        with self.assertRaises(ValueError): self.calculate()

    def test_invalid_ratings_and_policy_rejected(self):
        with self.assertRaises(ValueError): self.calculate({**self.over, "tempo": float("nan")})
        self.policy["thresholds"]["very_extreme_z"] = 0.5
        with self.assertRaises(ValueError): self.calculate()

    def test_benchmark_selection_cannot_look_ahead(self):
        future = deepcopy(self.benchmark); future["observed_at"] = "2026-11-11T18:00:00+00:00"
        result = select_benchmark([future, self.benchmark], NOW, 2027)
        self.assertEqual(result["observed_at"], self.benchmark["observed_at"])

    def test_reference_excludes_future_quotes_and_deduplicates(self):
        quotes = [
            {"game_id": "1", "total": 140, "observed_at": "2026-11-10T17:00:00+00:00"},
            {"game_id": "1", "total": 150, "observed_at": "2026-11-10T18:00:00+00:00"},
            {"game_id": "2", "total": 160, "observed_at": "2026-11-10T18:00:00+00:00"},
            {"game_id": "3", "total": 290, "observed_at": "2026-11-11T18:00:00+00:00"},
        ]
        ref = market_reference(quotes, NOW, self.policy)
        self.assertEqual(ref["quote_count"], 2)
        self.assertEqual((ref["low_total"], ref["high_total"]), (152.5, 157.5))

    def test_forecast_model_projection_and_results_do_not_affect_grade(self):
        forecast = {"lockedAt": NOW, "tipoff": TIP, "ratings_observed_at": self.benchmark["observed_at"],
                    "home_court": {"season": 2027}, "away_rating": self.over, "home_rating": self.neutral,
                    "market": {"total": 155, "as_of": NOW}, "total": 100, "total_edge": -55, "totalResult": "LOSS"}
        quotes = [{"game_id": str(i), "total": value, "observed_at": "2026-11-10T18:00:00+00:00"} for i, value in enumerate([130, 140, 160, 170, 180])]
        baseline = grade_saved_forecast(forecast, [self.benchmark], quotes, self.policy)
        changed = grade_saved_forecast({**forecast, "total": 250, "total_edge": 95, "totalResult": "WIN"}, [self.benchmark], quotes, self.policy)
        self.assertEqual(baseline, changed)

    def test_replay_uses_latest_pregame_capture_not_postgame_update(self):
        game = {"Game ID": "1", "Game Time": TIP}
        before = {"gameId": "1", "lockedAt": NOW, "tipoff": TIP, "recordId": "before", "totalResult": "WIN"}
        later = {**before, "lockedAt": "2026-11-11T18:00:00+00:00", "recordId": "later", "totalResult": "LOSS"}
        after = {**before, "lockedAt": "2026-11-11T20:00:00+00:00", "recordId": "after"}
        payload = {"ncaamMarket": {"date": "2026-11-11", "games": [game], "forecasts": [after, later, before]}}
        _, selected, rejected = latest_forecasts(payload, "2026-11-12T00:00:00+00:00")
        self.assertEqual(selected["1"]["recordId"], "later")
        self.assertEqual(rejected["unavailable_or_not_pregame"], 1)

    def test_settlement_preserves_selection_and_excludes_pending(self):
        frozen = {"rows": [{"game_id": "1", "away_team_id": "10", "home_team_id": "20", "side": "Over", "grade": "A", "line": 150, "as_of": NOW}]}
        original = deepcopy(frozen)
        event = {"id": "1", "status": {"type": {"name": "STATUS_FINAL", "completed": True}}, "competitions": [{"date": TIP, "competitors": [
            {"homeAway": "away", "team": {"id": "10"}, "score": "80"},
            {"homeAway": "home", "team": {"id": "20"}, "score": "75"}]}]}
        final = settle(frozen, {"events": [event]}, "digest", {}, "2026-11-12T00:00:00+00:00")
        self.assertEqual((final["summary"]["wins"], final["summary"]["pending"]), (1, 0))
        self.assertEqual(frozen, original)
        event["status"]["type"] = {"name": "STATUS_IN_PROGRESS", "completed": False}
        live = settle(frozen, {"events": [event]}, "digest", {}, "2026-11-12T00:00:00+00:00")
        self.assertEqual((live["summary"]["wins"], live["summary"]["pending"]), (0, 1))
        event["competitions"][0]["competitors"][0]["team"]["id"] = "wrong"
        with self.assertRaises(ValueError):
            settle(frozen, {"events": [event]}, "digest", {}, "2026-11-12T00:00:00+00:00")


if __name__ == "__main__": unittest.main()
