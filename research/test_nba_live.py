"""Source contracts and live pregame inputs, using synthetic games only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import MagicMock, patch

from builders.nba_injuries import normalize_espn_feed
from builders.nba_live import (allocate_minutes, checked_box_minutes, digest, fetch_recent_minutes,
    normalize_game, normalize_roster, prepare_live_package, source_get)
from builders.nba_model import InputError, evaluate_package
import test_nba_builder as builder_tests
from test_nba_builder import FakeStreamlit, load_builder

NOW = datetime(2026, 10, 22, 13, tzinfo=timezone.utc)


def source(now=NOW):
    return {"url": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/test-fixture",
            "observed_at": now.isoformat(), "source_sha256": "a" * 64}


def event_fixture(*, final=False, overtime=False, now=NOW):
    period = 5 if overtime else 4
    c = {"id": "123456", "date": (now + timedelta(hours=2)).isoformat(), "timeValid": True,
         "neutralSite": False, "status": {"type": {"name": "STATUS_FINAL" if final else "STATUS_SCHEDULED",
         "state": "post" if final else "pre", "completed": final}},
         "competitors": [{"homeAway": side, "team": {"id": tid, "displayName": "Fictional " + title},
             "score": str(points), "linescores": ([{"displayValue": "20"}] * period if points == period * 20 else
                       [{"displayValue": "20"}] * (period - 1) + [{"displayValue": "25"}]) if final else []}
             for side, tid, title, points in (("away", "1", "Away", period * 20), ("home", "2", "Home", period * 20 + 5))]}
    return {"id": "123456", "date": c["date"], "timeValid": True,
            "season": {"year": 2027, "type": 2}, "competitions": [c]}


def fixture(now=NOW):
    bundle = builder_tests.BuilderFlowTests.ratings_fixture()
    game = normalize_game(event_fixture(now=now), source(now))
    rosters, recent = {}, {}
    for side, team in zip(("away", "home"), bundle["teams"]):
        players = [{"espn_athlete_id": str((1 if side == 'away' else 100) + i), "name": p["name"],
                    "roster_status": "Active"} for i, p in enumerate(team["players"])]
        rosters[side] = {"espn_team_id": game[side]["espn_team_id"], "name": game[side]["name"],
                         "season_year": 2027, "players": players, "source": source(now)}
        recent[side] = {"espn_team_id": game[side]["espn_team_id"], "source": source(now),
                        "window": 10, "games": [], "rejected": []}
    injuries = normalize_espn_feed({"status": "success", "timestamp": now.isoformat(),
        "season": {"year": 2027}, "injuries": []}, fetched_at=now)
    inputs = {"game": game, "rosters": rosters, "recent": recent}
    return bundle, inputs, injuries


def live_package(now=NOW):
    bundle, inputs, injury = fixture(now)
    return prepare_live_package(bundle, inputs, injury, as_of=now), inputs["game"]


def box_fixture(event, *, overtime=False):
    game = normalize_game(event, source())
    minutes = 53 if overtime else 48
    players = []
    for side in ("away", "home"):
        rows = [{"athlete": {"id": str(10 + i + (100 if side == 'home' else 0)), "displayName": f"Test {side} {i}"},
                 "stats": [str(minutes), str(20 + (5 if side == 'home' and i == 0 else 0))],
                 "didNotPlay": False} for i in range(5)]
        if overtime:
            for row in rows:
                row["stats"][1] = str(int(row["stats"][1]) + 0)
        else:
            # Regulation fixture is 80-85, not 100-105.
            for row in rows:
                row["stats"][1] = str(16 + (5 if side == 'home' and row is rows[0] else 0))
        players.append({"team": {"id": game[side]["espn_team_id"]}, "statistics": [{"labels": ["MIN", "PTS"], "athletes": rows}]})
    return {"boxscore": {"players": players}}


class LiveSourceTests(unittest.TestCase):
    def test_final_summary_can_derive_period_from_reconciled_linescores(self):
        e = event_fixture(final=True)
        e.pop("date")  # Summary header has competition.date only.
        game = normalize_game(e, source())
        self.assertEqual(game["period"], 4)
        self.assertEqual(game["home"]["score"], 85)

    def test_bad_score_periods_and_preseason_are_rejected(self):
        for change in ("score", "period", "season"):
            e = event_fixture(final=True)
            if change == "score": e["competitions"][0]["competitors"][0]["score"] = "99"
            elif change == "period": e["competitions"][0]["competitors"][0]["linescores"].pop()
            else: e["season"]["type"] = 1
            with self.assertRaises(InputError): normalize_game(e, source())

    def test_roster_identity_season_and_duplicates_are_checked(self):
        _, inputs, _ = fixture()
        expected = inputs["game"]["away"]
        raw = {"status": "success", "season": {"year": 2027},
               "team": {"id": "1", "displayName": "Fictional Away"},
               "athletes": [{"id": str(i + 1), "displayName": f"Player {i}"} for i in range(8)]}
        self.assertEqual(len(normalize_roster(raw, source(), expected, 2027)["players"]), 8)
        for change in ("team", "season", "duplicate"):
            p = deepcopy(raw)
            if change == "team": p["team"]["id"] = "2"
            elif change == "season": p["season"]["year"] = 2026
            else: p["athletes"].append(p["athletes"][0])
            with self.assertRaises(InputError): normalize_roster(p, source(), expected, 2027)

    def test_full_box_reconciles_and_overtime_normalizes_to_240(self):
        for ot in (False, True):
            event = event_fixture(final=True, overtime=ot)
            game = normalize_game(event, source())
            boxes = checked_box_minutes(box_fixture(event, overtime=ot), game)
            self.assertAlmostEqual(sum(x["minutes"] for x in boxes["1"]), 240)
            self.assertTrue(all(x["minutes"] <= 48 for x in boxes["1"]))
            raw = box_fixture(event, overtime=ot)
            raw["boxscore"]["players"][0]["statistics"][0]["athletes"][0]["stats"][1] = "99"
            with self.assertRaises(InputError): checked_box_minutes(raw, game)

    def test_network_failure_and_oversized_response_do_not_return_empty_data(self):
        with patch("builders.nba_live.urlopen", side_effect=OSError("offline")):
            with self.assertRaises(InputError): source_get("scoreboard")
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"x" * 6_000_001
        with patch("builders.nba_live.urlopen", return_value=response):
            with self.assertRaises(InputError): source_get("scoreboard")

    def test_target_game_cannot_enter_prior_minutes(self):
        bundle, inputs, injury = fixture()
        inputs["recent"]["away"]["games"] = [{"game": inputs["game"], "players": []}]
        with self.assertRaisesRegex(InputError, "target-game"):
            prepare_live_package(bundle, inputs, injury, as_of=NOW)

    def test_current_rosters_keep_baseline_and_never_confirm_starters_or_health(self):
        package, _ = live_package()
        projection = evaluate_package(package)
        for side in ("away", "home"):
            self.assertAlmostEqual(sum(p["reference_minutes"] for p in package[side]["players"]), 240)
            plan = package["scenarios"][0][side]
            self.assertAlmostEqual(sum(p["minutes"] for p in plan["players"]), 240)
            self.assertTrue(all(p["status"] == "UNKNOWN" for p in plan["players"]))
            self.assertFalse(plan["starters_confirmed"])
            self.assertFalse(any(p["starter"] for p in plan["players"]))
        self.assertFalse(projection["eligible_for_posting"])

    def test_newcomer_uses_espn_namespace_and_departed_player_retains_reference_only(self):
        bundle, inputs, injury = fixture()
        roster = inputs["rosters"]["away"]["players"]
        old_id = bundle["teams"][0]["players"][0]["player_id"]
        roster[0].update(espn_athlete_id="12345", name="Synthetic rookie")
        package = prepare_live_package(bundle, inputs, injury, as_of=NOW)
        new = next(p for p in package["away"]["players"] if p["player_id"] == "espn:12345")
        self.assertEqual(new["impact_basis"], "prior_only")
        self.assertEqual(new["offensive_impact"], 0)
        old = next(p for p in package["away"]["players"] if p["player_id"] == old_id)
        self.assertGreater(old["reference_minutes"], 0)
        plan = {p["player_id"]: p for p in package["scenarios"][0]["away"]["players"]}
        self.assertEqual(plan[old_id]["minutes"], 0)
        self.assertEqual(plan[new["player_id"]]["minutes"], 0)

    def test_duplicate_current_identity_blocks_preparation(self):
        bundle, inputs, injury = fixture()
        inputs["rosters"]["home"]["players"][0] = inputs["rosters"]["away"]["players"][0]
        with self.assertRaisesRegex(InputError, "both current rosters"):
            prepare_live_package(bundle, inputs, injury, as_of=NOW)

    def test_out_player_is_excluded_before_explicit_minute_suggestion(self):
        bundle, inputs, _ = fixture()
        raw = {"status":"success", "timestamp":NOW.isoformat(), "season":{"year":2027},
               "injuries":[{"id":"1", "displayName":"Fictional Away", "injuries":[{
                   "id":"-1", "status":"Out", "date":NOW.isoformat(),
                   "athlete":{"id":"1", "displayName":"Fictional A Player 1", "team":{"id":"1"}},
                   "details":{"returnDate":"2026-10-23"}}]}]}
        injury = normalize_espn_feed(raw, fetched_at=NOW)
        p = prepare_live_package(bundle, inputs, injury, as_of=NOW)
        plan = p['scenarios'][0]['away']['players']
        self.assertEqual(next(x for x in plan if x['player_id']=='A1')['minutes'], 0)
        self.assertAlmostEqual(sum(x['minutes'] for x in plan), 240)
        self.assertFalse(p['live_provenance']['conflicts'])

    def test_injury_listing_absent_from_current_roster_is_an_explicit_conflict(self):
        bundle, inputs, _ = fixture()
        raw = {"status":"success", "timestamp":NOW.isoformat(), "season":{"year":2027},
               "injuries":[{"id":"1", "displayName":"Fictional Away", "injuries":[{
                   "id":"-1", "status":"Out", "date":NOW.isoformat(),
                   "athlete":{"id":"9999", "displayName":"Synthetic departed player", "team":{"id":"1"}},
                   "details":{}}]}]}
        injury = normalize_espn_feed(raw, fetched_at=NOW)
        p = prepare_live_package(bundle, inputs, injury, as_of=NOW)
        self.assertTrue(p['live_provenance']['conflicts'])

    def test_proportional_minutes_respect_caps_and_insufficient_capacity_blocks(self):
        weights = {str(i): 100 if i == 0 else 10 for i in range(8)}
        caps = {p: 48 for p in weights}
        caps['0'] = 30
        minutes = allocate_minutes(weights, caps)
        self.assertAlmostEqual(sum(minutes.values()), 240)
        self.assertEqual(minutes['0'], 30)
        with self.assertRaises(InputError): allocate_minutes({'1': 20}, {'1': 48})

    def test_ordinary_builder_render_performs_no_live_network_or_tracking_io(self):
        st = FakeStreamlit()
        builder = load_builder(st)
        with patch("builders.nba_live.source_get") as request, patch("shared.nba_tracking_storage._backend") as db:
            builder.render()
        request.assert_not_called()
        db.assert_not_called()


if __name__ == "__main__":
    unittest.main()
