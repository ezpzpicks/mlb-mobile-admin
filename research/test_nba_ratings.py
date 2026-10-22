"""Synthetic data checks for ingestion, fitting and genuinely prior-only inputs."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import unittest

import numpy as np
import pandas as pd

from builders.nba_history import History, OFF, DEF, _box_minutes, forecast_minutes, prepare_history
from builders.nba_model import InputError, evaluate_package
from builders.nba_ratings import add_roster_player, fit_rating_bundle, package_from_bundle, validate_bundle
from builders.nba_example import example_package


def source_fixture(games=18):
    possessions, schedule, boxes = [], [], []
    pairs = [(1, 2), (3, 4), (2, 3), (4, 1), (1, 3), (2, 4)]
    for game_index in range(games):
        home, away = pairs[game_index % len(pairs)]
        game_id = f"00225{game_index+1:05d}"
        game_day = date(2025, 11, 1) + timedelta(days=2*game_index)
        units = {team: [str(team*100+i) for i in range(1, 7)] for team in (home, away)}
        pm = {player: 0 for unit in units.values() for player in unit}
        points = {home: 0, away: 0}
        for possession in range(120):
            offense, defense = (home, away) if possession % 2 == 0 else (away, home)
            off = [p for i, p in enumerate(units[offense]) if i != (possession//2) % 6]
            df = [p for i, p in enumerate(units[defense]) if i != ((possession//2)+game_index) % 6]
            scored = int(str(offense*100+1) in off) + int(str(defense*100+2) not in df)
            scored += int(possession % 7 == 0)
            points[offense] += scored
            for player in off:
                pm[player] += scored
            for player in df:
                pm[player] -= scored
            row = {"game_id": game_id, "period": 4, "possession_number": possession,
                   "offense_team_id": offense, "defense_team_id": defense, "points": scored,
                   "count_as_possession": True, "lineup_source": "synthetic test"}
            row.update(dict(zip(OFF, off)))
            row.update(dict(zip(DEF, df)))
            possessions.append(row)
        schedule.append({"game_id": game_id, "game_date": str(game_day),
            "home_team_id": home, "home_team_name": f"Test Team {home}", "home_pts": points[home],
            "away_team_id": away, "away_team_name": f"Test Team {away}", "away_pts": points[away]})
        for team in (home, away):
            for player in units[team]:
                boxes.append({"game_id": game_id, "team_id": team, "person_id": player,
                    "minutes": "40:00", "plus_minus_points": pm[player],
                    "first_name": "Test", "family_name": player})
    return pd.DataFrame(possessions), pd.DataFrame(schedule), pd.DataFrame(boxes)


def prepared():
    return prepare_history(*source_fixture(), source="Synthetic test inputs")


class RosterAdditionTests(unittest.TestCase):
    def test_newcomer_has_zero_prior_and_no_automatically_assigned_minutes(self):
        p = example_package()
        q = add_roster_player(p, "home", "NEW-PLAYER", "Synthetic newcomer")
        new = q["home"]["players"][-1]
        self.assertEqual(new["reference_minutes"], 0)
        self.assertEqual(new["offensive_impact"], 0)
        self.assertEqual(new["defensive_impact"], 0)
        self.assertEqual(new["impact_basis"], "prior_only")
        self.assertEqual(new["impact_sample_possessions"], 0)
        for s in q["scenarios"]:
            self.assertEqual(s["home"]["players"][-1]["minutes"], 0)
            self.assertFalse(s["home"]["report_complete"])
        self.assertFalse(any(x["player_id"] == "NEW-PLAYER" for x in p["home"]["players"]))

    def test_traded_player_reuses_his_dated_impact_with_zero_new_team_reference_minutes(self):
        p = example_package()
        old = p["away"]["players"][0]
        q = add_roster_player(p, "home", old["player_id"], "")
        new = q["home"]["players"][-1]
        self.assertEqual(new["offensive_impact"], old["offensive_impact"])
        self.assertEqual(new["defensive_impact"], old["defensive_impact"])
        self.assertEqual(new["reference_minutes"], 0)
        self.assertAlmostEqual(sum(p["reference_minutes"] for p in q["home"]["players"]), 240)

    def test_duplicate_or_unnamed_new_player_is_rejected(self):
        p = example_package()
        with self.assertRaises(InputError):
            add_roster_player(p, "away", p["away"]["players"][0]["player_id"], "duplicate")
        with self.assertRaises(InputError):
            add_roster_player(p, "away", "NEW-PLAYER", "")


class HistoryChecks(unittest.TestCase):
    def test_a_trade_does_not_seed_the_same_player_for_both_teams(self):
        h = prepared()
        last = h.box.loc[(h.box.game_date == date(2025, 12, 3)) & (h.box.team_id == "3")].iloc[0].copy()
        last["person_id"], last["minutes"], last["minutes_value"] = "201", "", 0.0
        last["plus_minus_points"], last["player_name"] = 0, "Test 201"
        h.box = pd.concat([h.box, last.to_frame().T], ignore_index=True)
        later = forecast_minutes(h, "2", "2025-12-05T00:00:00Z")
        earlier = forecast_minutes(h, "2", "2025-12-03T00:00:00Z")
        self.assertNotIn("201", later)
        self.assertIn("201", earlier)
        self.assertAlmostEqual(sum(later.values()), 240)
        self.assertTrue(all(m <= 48 for m in later.values()))
    def test_complete_games_pass_and_points_are_preserved(self):
        p, g, b = source_fixture()
        h = prepare_history(p, g, b, source="test")
        self.assertEqual(h.audit["accepted_games"], len(g))
        self.assertEqual(h.observations.points.sum(), p.points.sum())
        self.assertEqual(h.observations.possessions.sum(), len(p))

    def test_a_score_mismatch_excludes_the_entire_game(self):
        p, g, b = source_fixture()
        g.loc[0, "home_pts"] += 1
        h = prepare_history(p, g, b, source="test")
        self.assertEqual(h.audit["accepted_games"], len(g)-1)
        self.assertEqual(h.audit["exclusion_reasons"]["score_mismatch"], 1)

    def test_lineup_player_credit_is_checked_against_box_scores(self):
        p, g, b = source_fixture()
        b.loc[0, "plus_minus_points"] += 10
        h = prepare_history(p, g, b, source="test")
        self.assertEqual(h.audit["accepted_games"], len(g)-1)
        self.assertIn("player_plus_minus_mismatch", h.audit["exclusion_reasons"])

    def test_duplicate_possessions_are_not_silently_deduplicated(self):
        p, g, b = source_fixture()
        with self.assertRaisesRegex(InputError, "keys are duplicated"):
            prepare_history(pd.concat([p, p.iloc[:1]]), g, b, source="test")

    def test_missing_dates_cannot_be_inferred_from_game_ids(self):
        p, g, b = source_fixture()
        with self.assertRaisesRegex(InputError, "missing from the schedule"):
            prepare_history(p, g.iloc[1:], b, source="test")

    def test_game_with_duplicated_on_floor_player_is_rejected(self):
        p, g, b = source_fixture()
        p.loc[0, OFF[1]] = p.loc[0, OFF[0]]
        h = prepare_history(p, g, b, source="test")
        self.assertNotIn(g.iloc[0].game_id, set(h.games.game_id))

    def test_box_minutes_must_cover_the_whole_team_game(self):
        p, g, b = source_fixture()
        b.loc[0, "minutes"] = "10:00"
        h = prepare_history(p, g, b, source="test")
        self.assertIn("box_minutes_incomplete", h.audit["exclusion_reasons"])

    def test_minute_rollover_preserves_seconds_and_invalid_seconds_fail(self):
        self.assertEqual(_box_minutes("30:60"), 31)
        with self.assertRaises(InputError):
            _box_minutes("30:90")

    def test_availability_uses_next_day_noon_eastern(self):
        h = prepared()
        row = h.games.sort_values("game_date").iloc[0]
        self.assertEqual(row.available_at.isoformat(), "2025-11-02T17:00:00+00:00")

    def test_minutes_forecast_does_not_use_future_box_scores(self):
        h = prepared()
        cutoff = datetime(2025, 11, 20, tzinfo=timezone.utc)
        first = forecast_minutes(h, "1", cutoff)
        h.box.loc[h.box.available_at > cutoff, "minutes_value"] = 999
        self.assertEqual(first, forecast_minutes(h, "1", cutoff))
        self.assertAlmostEqual(sum(first.values()), 240)

    def test_overtime_minutes_are_normalized_to_regulation(self):
        h = prepared()
        h.box["minutes_value"] *= 53/48
        values = forecast_minutes(h, "1", datetime(2025, 12, 20, tzinfo=timezone.utc))
        self.assertAlmostEqual(sum(values.values()), 240)
        self.assertTrue(all(0 <= v <= 48 for v in values.values()))


class RatingChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history = prepared()
        cls.cutoff = datetime(2025, 11, 20, tzinfo=timezone.utc)
        cls.bundle = fit_rating_bundle(cls.history, cls.cutoff, player_ridge=100, team_ridge=100)

    def test_fit_exports_valid_separate_offense_and_defense(self):
        validate_bundle(self.bundle)
        self.assertEqual(len(self.bundle["teams"]), 4)
        self.assertEqual(len(self.bundle["player_impacts"]), 24)
        self.assertTrue(all(math_value > 0 for math_value in [self.bundle["league_efficiency"], self.bundle["league_pace"]]))
        self.assertTrue(all("defensive_impact" in p and "offensive_impact" in p for p in self.bundle["player_impacts"]))

    def test_reference_rotation_and_ratings_use_the_same_sample(self):
        for team in self.bundle["teams"]:
            self.assertAlmostEqual(sum(p["reference_minutes"] for p in team["players"]), 240)
            self.assertTrue(all(p["impact_sample_possessions"] > 0 for p in team["players"]))

    def test_future_results_do_not_change_fitted_ratings(self):
        h = deepcopy(self.history)
        h.observations.loc[h.observations.available_at > self.cutoff, "points"] = 99999
        altered = fit_rating_bundle(h, self.cutoff, player_ridge=100, team_ridge=100)
        self.assertEqual(self.bundle, altered)

    def test_newer_prior_is_rejected(self):
        prior = deepcopy(self.bundle)
        prior["as_of"] = "2025-12-01T00:00:00+00:00"
        with self.assertRaisesRegex(InputError, "prior bundle cannot be newer"):
            fit_rating_bundle(self.history, self.cutoff, prior_bundle=prior)

    def test_future_rating_bundle_cannot_predict_an_earlier_game(self):
        with self.assertRaisesRegex(InputError, "fitted after"):
            package_from_bundle(self.bundle, "1", "2", game_id="test", as_of="2025-11-01T00:00:00Z", tipoff="2025-11-01T20:00:00Z")

    def test_generated_package_never_invents_current_availability(self):
        package = package_from_bundle(self.bundle, "1", "2", game_id="test",
            as_of="2025-11-25T00:00:00Z", tipoff="2025-11-25T20:00:00Z", history=self.history)
        for side in ("away", "home"):
            plan = package["scenarios"][0][side]
            self.assertFalse(plan["report_complete"])
            self.assertFalse(plan["starters_confirmed"])
            self.assertTrue(all(p["status"] == "UNKNOWN" for p in plan["players"]))
        result = evaluate_package(package)
        self.assertFalse(result["scenarios"][0]["lineup_ready"])
        self.assertFalse(result["eligible_for_posting"])

    def test_no_market_line_is_created_when_seeding_a_matchup(self):
        package = package_from_bundle(self.bundle, "1", "2", game_id="test",
            as_of="2025-11-25T00:00:00Z", tipoff="2025-11-25T20:00:00Z")
        self.assertIsNone(package["market"])

    def test_unobserved_player_prior_is_flagged(self):
        package = package_from_bundle(self.bundle, "1", "2", game_id="test",
            as_of="2025-11-25T00:00:00Z", tipoff="2025-11-25T20:00:00Z")
        player = package["away"]["players"][0]
        player.update(impact_basis="prior_only", impact_sample_possessions=0)
        flags = evaluate_package(package)["scenarios"][0]["flags"]
        self.assertTrue(any("no fitted player sample" in f for f in flags))

    def test_invalid_penalty_is_rejected(self):
        with self.assertRaisesRegex(InputError, "finite and positive"):
            fit_rating_bundle(self.history, self.cutoff, player_ridge=0)


if __name__ == "__main__":
    unittest.main()
