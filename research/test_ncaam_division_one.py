"""Schedule membership must be decided before markets, ratings and lineup work."""
from copy import deepcopy
import unittest

from scripts.run_ncaam_builder import parse_slate
from shared.ncaam_division_one import division_one_matchup, _ROSTER


class DivisionOneScheduleTests(unittest.TestCase):
    def event(self, game_id, away="87", home="222"):
        return {"id": game_id, "season": {"year": 2027, "type": 2}, "competitions": [{
            "date": "2026-11-02T23:00:00Z", "timeValid": True, "neutralSite": True,
            "status": {"type": {"name": "STATUS_SCHEDULED"}}, "competitors": [
                {"homeAway": "away", "team": {"id": away, "displayName": "Notre Dame", "location": "Notre Dame"}},
                {"homeAway": "home", "team": {"id": home, "displayName": "Villanova", "location": "Villanova"}},
            ]}]}

    def test_verified_membership_and_season(self):
        self.assertEqual(len(_ROSTER["teams"]), 365)
        for team in ["87", "222", "88", "2511", "2815"]:
            self.assertTrue(division_one_matchup(team, "2", 2027))
        self.assertFalse(division_one_matchup("42", "87", 2027))
        self.assertFalse(division_one_matchup("87", "42", 2027))
        self.assertFalse(division_one_matchup("87", "222", 2028))

    def test_non_d1_either_side_and_unknown_ids_are_excluded_despite_display_name(self):
        payload = {"events": [self.event("1"), self.event("2", away="42"), self.event("3", home="42"),
                              self.event("4", away="999999")]}
        games = parse_slate(payload, "2026-11-02")
        self.assertEqual([game["game_id"] for game in games], ["1"])
        self.assertEqual(parse_slate({"events": payload["events"][1:]}, "2026-11-02"), [])

    def test_new_d1_teams_remain_eligible(self):
        for team in ["88", "2511", "2815"]:
            self.assertEqual(len(parse_slate({"events": [self.event("1", away=team)]}, "2026-11-02")), 1)

    def test_exhibition_is_still_excluded(self):
        event = deepcopy(self.event("1"))
        event["season"]["type"] = 1
        self.assertEqual(parse_slate({"events": [event]}, "2026-11-02"), [])


if __name__ == "__main__":
    unittest.main()
