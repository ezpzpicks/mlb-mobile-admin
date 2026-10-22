"""Behavioral checks for event lineups and foul-time free-throw attribution."""
import unittest

import pandas as pd

from builders.nba_history import attach_box_history, forecast_minutes, prepare_event_history
from builders.nba_ratings import fit_rating_bundle


def event_fixture(count=2):
    events, lineups, possessions, schedule, boxes = [], [], [], [], []
    for number in range(1, count + 1):
        game = f"002250{number:04d}"
        home, away = list(range(11, 16)), list(range(21, 26))
        order = 0

        def add(period, clock, kind="other", score_home="", score_away=""):
            nonlocal order
            key = {"game_id": game, "period": period, "action_number": order + 1}
            events.append({**key, "order_index": order, "seconds_remaining": clock,
                "score_home": score_home, "score_away": score_away,
                "is_foul": kind == "foul", "is_free_throw": kind == "ft",
                "possession_number": period})
            lineups.append({**key, **{f"home_player_{i+1}": p for i, p in enumerate(home)},
                            **{f"away_player_{i+1}": p for i, p in enumerate(away)}})
            order += 1

        add(1, 720)
        add(1, 600, "foul")
        add(1, 600, "ft", "1", "0")
        add(1, 600, "sub")
        away[0] = 26
        add(1, 600, "ft", "2", "0")
        add(1, 0)
        for period in (2, 3, 4):
            add(period, 720)
            add(period, 0, score_home="2" if period == 4 else "",
                score_away="2" if period == 4 else "")
        possessions.extend({"game_id": game, "period": p, "possession_number": p,
                            "count_as_possession": True} for p in range(1, 5))
        schedule.append({"game_id": game, "game_date": f"2025-11-0{number}",
            "home_team_id": 1, "away_team_id": 2, "home_team_name": "Test Home",
            "away_team_name": "Test Away", "home_pts": 2, "away_pts": 2})
        for team, players in ((1, range(11, 16)), (2, range(21, 27))):
            for player in players:
                boxes.append({"game_id": game, "team_id": team, "person_id": player,
                    "first_name": "Test", "family_name": str(player),
                    "minutes": "2:00" if player == 21 else "46:00" if player == 26 else "48:00",
                    "plus_minus_points": -2 if player == 21 else 2 if player == 26 else 0})
    return [pd.DataFrame(x) for x in (events, lineups, possessions, schedule, boxes)]


def prepare(frames):
    return prepare_event_history(*frames, source="Synthetic event tests")


def add_box_stats(frames):
    b = frames[4]
    for column in ("field_goals_attempted", "free_throws_attempted", "rebounds_offensive", "turnovers", "points"):
        b[column] = 0
    b.loc[b.person_id == 11, ["free_throws_attempted", "points"]] = 2
    b.loc[b.person_id == 26, "field_goals_attempted"] = 1
    b.loc[b.person_id == 26, "points"] = 2
    return frames


class EventHistoryTests(unittest.TestCase):
    def test_substitution_between_free_throws_keeps_foul_lineup_point_credit(self):
        h = prepare(event_fixture())
        self.assertEqual(h.audit["accepted_games"], 2)
        self.assertEqual(h.audit["plus_minus_tolerance"], 0)
        self.assertEqual(h.audit["maximum_accepted_player_minute_error_seconds"], 0)
        home = h.observations.loc[h.observations.offense_team_id == "1"]
        old = home.defense_players.map(lambda unit: "21" in unit)
        incoming = home.defense_players.map(lambda unit: "26" in unit)
        self.assertEqual(home.loc[old, "points"].sum(), 4)
        self.assertEqual(home.loc[incoming, "points"].sum(), 0)
        self.assertEqual(h.observations.points.sum(), 8)

    def test_fractional_exposure_preserves_the_original_whole_game_count(self):
        frames = event_fixture()
        h = prepare(frames)
        self.assertAlmostEqual(h.observations.possessions.sum(), frames[2].count_as_possession.sum())
        self.assertEqual(h.games.possessions.tolist(), [2, 2])

    def test_missing_event_lineup_excludes_the_entire_game(self):
        frames = event_fixture()
        frames[1] = frames[1].iloc[1:]
        h = prepare(frames)
        self.assertEqual(h.audit["accepted_games"], 1)
        self.assertIn("missing_event_lineup", h.audit["exclusion_reasons"])

    def test_box_minute_disagreement_excludes_the_entire_game(self):
        frames = event_fixture()
        frames[4].loc[0, "minutes"] = "47:00"
        h = prepare(frames)
        self.assertEqual(h.audit["accepted_games"], 1)
        self.assertIn("event_player_minutes_mismatch", h.audit["exclusion_reasons"])

    def test_even_one_point_of_box_plus_minus_error_excludes_a_game(self):
        frames = event_fixture()
        frames[4].loc[0, "plus_minus_points"] = 1
        h = prepare(frames)
        self.assertEqual(h.audit["accepted_games"], 1)
        self.assertIn("player_plus_minus_mismatch", h.audit["exclusion_reasons"])

    def test_negative_score_revision_is_rejected(self):
        frames = event_fixture()
        frames[0].loc[4, "score_home"] = "0"
        h = prepare(frames)
        self.assertEqual(h.audit["accepted_games"], 1)
        self.assertIn("negative_score_change", h.audit["exclusion_reasons"])

    def test_two_copies_of_the_same_action_number_can_match_without_row_loss(self):
        frames = event_fixture()
        for position in (0, 1):
            frames[position] = pd.concat([frames[position].iloc[:1], frames[position]], ignore_index=True)
        frames[0]["order_index"] = frames[0].groupby("game_id").cumcount()
        self.assertEqual(prepare(frames).audit["accepted_games"], 2)

    def test_different_lineups_for_a_repeated_key_are_ambiguous(self):
        frames = event_fixture()
        for position in (0, 1):
            frames[position] = pd.concat([frames[position].iloc[:1], frames[position]], ignore_index=True)
        frames[0]["order_index"] = frames[0].groupby("game_id").cumcount()
        frames[1].loc[0, "home_player_1"] = 99
        h = prepare(frames)
        self.assertEqual(h.audit["accepted_games"], 1)
        self.assertIn("ambiguous_event_lineup", h.audit["exclusion_reasons"])


class IndependentTeamHistoryTests(unittest.TestCase):
    def test_bad_event_lineups_do_not_remove_valid_team_scores_or_prior_minutes(self):
        frames = add_box_stats(event_fixture())
        frames[1].loc[0, "home_player_1"] = 99
        h = attach_box_history(prepare(frames), frames[3], frames[4])
        self.assertEqual(len(h.games), 1)
        self.assertEqual(len(h.team_games), 2)
        self.assertAlmostEqual(h.observations.possessions.sum(), 1.88)
        self.assertEqual(sum(forecast_minutes(h, "1", "2025-11-04T00:00:00Z").values()), 240)

    def test_corrupted_box_points_exclude_a_team_game_and_its_player_samples(self):
        frames = add_box_stats(event_fixture())
        base = prepare(frames)
        frames[4].loc[0, "points"] += 1
        h = attach_box_history(base, frames[3], frames[4])
        self.assertEqual(len(h.team_games), 1)
        self.assertEqual(len(h.games), 1)
        self.assertEqual(h.audit["team_excluded_games"], 1)

    def test_a_player_without_checked_lineup_evidence_keeps_an_explicit_zero_prior(self):
        frames = add_box_stats(event_fixture(3))
        game = frames[3].iloc[2].game_id
        # A newcomer appears in the broad team boxes; that game's event
        # lineups fail the independent PM gate. Do not invent his RAPM value.
        frames[4].loc[(frames[4].game_id == game) & (frames[4].person_id == 26), "person_id"] = 27
        for column in [f"away_player_{i}" for i in range(1, 6)]:
            frames[1].loc[(frames[1].game_id == game) & (frames[1][column] == 26), column] = 27
        frames[4].loc[(frames[4].game_id == game) & (frames[4].person_id == 11), "plus_minus_points"] = 1
        h = attach_box_history(prepare(frames), frames[3], frames[4])
        b = fit_rating_bundle(h, "2025-11-10T00:00:00Z")
        player = next(p for p in b["player_impacts"] if p["player_id"] == "27")
        self.assertEqual(player["impact_basis"], "prior_only")
        self.assertEqual(player["impact_sample_possessions"], 0)
        self.assertEqual(player["offensive_impact"], 0)
        self.assertEqual(b["training_games"], 3)
        self.assertEqual(b["player_training_games"], 2)


if __name__ == "__main__":
    unittest.main()
