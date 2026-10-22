"""ESPN source semantics, identity safety, dated inputs and replacement minutes."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import MagicMock, patch

from builders.nba_example import example_package
from builders.nba_injuries import apply_espn_report, fetch_espn_injuries, normalize_espn_feed
from builders.nba_model import InputError, PlayerMinutes, evaluate_package, snapshot_json
from builders.nba_ratings import add_roster_player

NOW = datetime(2026, 10, 22, 13, tzinfo=timezone.utc)


def matchup():
    p = example_package(NOW)
    p["is_demo"] = False
    return p


def source_fixture(status="Day-To-Day", return_date="2026-10-23", *, name="Fictional A Player 1"):
    return {"status": "success", "timestamp": "2026-10-22T12:45:00Z", "season": {"year": 2027},
        "injuries": [{"id": "1", "displayName": "Fictional Away", "injuries": [{"id": "-999",
            "status": status, "date": "2026-10-22T12:40:00Z", "shortComment": "Synthetic injury note.",
            "athlete": {"displayName": name, "team": {"id": "1", "displayName": "Fictional Away"},
                "links": [{"href": "https://www.espn.com/nba/player/_/id/12345/synthetic-player"}]},
            "details": {"type": "Ankle", "detail": "Sprain", "side": "Left", "returnDate": return_date}}]}]}


def report(status="Day-To-Day", return_date="2026-10-23", **kwargs):
    return normalize_espn_feed(source_fixture(status, return_date, **kwargs), fetched_at="2026-10-22T12:50:00Z")


class EspnParsingTests(unittest.TestCase):
    def test_estimated_return_and_update_are_preserved_with_the_espn_athlete_id(self):
        r = report()
        row = r["teams"][0]["injuries"][0]
        self.assertEqual(row["espn_athlete_id"], "12345")
        self.assertNotEqual(row["espn_athlete_id"], row["injury_id"])
        self.assertEqual(row["estimated_return_date"], "2026-10-23")
        self.assertEqual(row["model_status"], "DAY_TO_DAY")
        self.assertEqual(row["source_status"], "Day-To-Day")
        self.assertFalse(r["report_complete"])

    def test_missing_return_date_stays_unknown(self):
        self.assertIsNone(report(return_date=None)["teams"][0]["injuries"][0]["estimated_return_date"])

    def test_invalid_return_date_is_not_silently_guessed(self):
        with self.assertRaises(InputError):
            report(return_date="2026-02-30")

    def test_unknown_status_stays_unknown(self):
        row = report(status="New ESPN status")["teams"][0]["injuries"][0]
        self.assertEqual(row["model_status"], "UNKNOWN")
        self.assertTrue(row["status_unrecognized"])

    def test_injury_id_cannot_substitute_for_missing_athlete_identity(self):
        p = source_fixture()
        p["injuries"][0]["injuries"][0]["athlete"]["links"] = []
        with self.assertRaises(InputError):
            normalize_espn_feed(p, fetched_at=NOW)

    def test_conflicting_player_card_ids_are_rejected(self):
        p = source_fixture()
        p["injuries"][0]["injuries"][0]["athlete"]["id"] = "98765"
        with self.assertRaises(InputError):
            normalize_espn_feed(p, fetched_at=NOW)

    def test_duplicate_athlete_reports_are_rejected(self):
        p = source_fixture()
        p["injuries"][0]["injuries"].append(deepcopy(p["injuries"][0]["injuries"][0]))
        with self.assertRaises(InputError):
            normalize_espn_feed(p, fetched_at=NOW)

    def test_future_news_or_feed_timestamps_are_rejected(self):
        for field in ("feed", "news"):
            p = source_fixture()
            if field == "feed":
                p["timestamp"] = "2026-10-23T00:00:00Z"
            else:
                p["injuries"][0]["injuries"][0]["date"] = "2026-10-23T00:00:00Z"
            with self.assertRaises(InputError):
                normalize_espn_feed(p, fetched_at=NOW)

    def test_failed_or_changed_feed_does_not_become_an_empty_healthy_list(self):
        for payload in ({"status": "error", "injuries": []}, {"status": "success", "injuries": {}}, {}):
            with self.assertRaises(InputError):
                normalize_espn_feed(payload, fetched_at=NOW)

    def test_fetch_uses_one_bounded_league_request(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(source_fixture()).encode()
        with patch("builders.nba_injuries.urlopen", return_value=response) as request:
            r = fetch_espn_injuries()
        self.assertEqual(request.call_count, 1)
        self.assertEqual(r["injury_count"], 1)
        self.assertEqual(request.call_args.kwargs["timeout"], 15)

    def test_network_failure_is_visible(self):
        with patch("builders.nba_injuries.urlopen", side_effect=OSError("connection unavailable")):
            with self.assertRaisesRegex(InputError, "could not be refreshed"):
                fetch_espn_injuries()

    def test_oversized_response_is_rejected_before_parsing(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"x" * 4_000_001
        with patch("builders.nba_injuries.urlopen", return_value=response):
            with self.assertRaisesRegex(InputError, "size limit"):
                fetch_espn_injuries()


class EspnApplicationTests(unittest.TestCase):
    def test_day_to_day_keeps_both_explicit_scenarios_without_changing_minutes(self):
        p = matchup()
        q, audit = apply_espn_report(p, report())
        a, b = [s["away"]["players"][0] for s in q["scenarios"]]
        self.assertEqual((a["status"], a["assumption"], a["minutes"]), ("DAY_TO_DAY", "IN", 36))
        self.assertEqual((b["status"], b["assumption"], b["minutes"]), ("DAY_TO_DAY", "OUT", 0))
        self.assertEqual(len(audit["matched"]), 1)
        result = evaluate_package(q)
        self.assertFalse(result["scenarios"][0]["lineup_ready"])
        self.assertFalse(result["eligible_for_posting"])
        self.assertNotIn("injury", p["scenarios"][0]["away"]["players"][0])

    def test_out_zeroes_minutes_and_requires_explicit_replacements(self):
        q, audit = apply_espn_report(matchup(), report(status="Out"))
        self.assertEqual(q["scenarios"][0]["away"]["players"][0]["minutes"], 0)
        self.assertEqual(audit["pending_minutes_at_import"][0]["unassigned_minutes"], 36)
        self.assertEqual(sum(p["minutes"] for p in q["scenarios"][0]["away"]["players"]), 204)
        with self.assertRaisesRegex(InputError, "total 240"):
            evaluate_package(q)
        # A replacement can be added while the rotation is still being edited.
        q = add_roster_player(q, "away", "NEW", "Synthetic reserve")
        q["scenarios"][0]["away"]["players"][-1].update(minutes=36, assumption="IN")
        self.assertFalse(evaluate_package(q)["eligible_for_posting"])

    def test_a_passed_return_estimate_never_marks_an_out_player_available(self):
        q, _ = apply_espn_report(matchup(), report(status="Out", return_date="2026-10-21"))
        # Use the already balanced OUT scenario to inspect model warnings.
        q["scenarios"] = [q["scenarios"][1]]
        row = q["scenarios"][0]["away"]["players"][0]
        self.assertEqual(row["status"], "OUT")
        self.assertEqual(row["minutes"], 0)
        self.assertTrue(any("estimated return" in f for f in evaluate_package(q)["scenarios"][0]["flags"]))

    def test_missing_player_does_not_become_available_and_stale_out_is_cleared(self):
        q, _ = apply_espn_report(matchup(), report(status="Out"))
        r = report()
        r["teams"][0]["injuries"] = []
        q, _ = apply_espn_report(q, r)
        row = q["scenarios"][0]["away"]["players"][0]
        self.assertEqual(row["status"], "UNKNOWN")
        self.assertIsNone(row["injury"])
        self.assertEqual(row["minutes"], 0)

    def test_current_feed_cannot_be_used_at_a_prior_prediction_cutoff(self):
        p = matchup()
        p["context"]["as_of"] = "2026-10-22T12:00:00Z"
        with self.assertRaisesRegex(InputError, "retrospectively"):
            apply_espn_report(p, report())

    def test_season_mismatch_is_rejected(self):
        r = report()
        r["season_year"] = 2026
        with self.assertRaisesRegex(InputError, "different NBA seasons"):
            apply_espn_report(matchup(), r)

    def test_unmatched_espn_id_is_never_used_as_an_nba_id(self):
        q, audit = apply_espn_report(matchup(), report(name="Unmatched rookie"))
        self.assertEqual(len(audit["unmatched"]), 1)
        self.assertFalse(any(p["player_id"] == "12345" for p in q["away"]["players"]))

    def test_ambiguous_name_requires_a_reviewed_id_override(self):
        p = matchup()
        p["home"]["players"][0]["name"] = p["away"]["players"][0]["name"]
        _, audit = apply_espn_report(p, report())
        self.assertEqual(audit["unmatched"][0]["reason"], "ambiguous name")
        q, audit = apply_espn_report(p, report(), player_id_overrides={"12345": "A1"})
        self.assertEqual(len(audit["matched"]), 1)
        self.assertEqual(q["scenarios"][0]["away"]["players"][0]["status"], "DAY_TO_DAY")

    def test_team_disagreement_is_visible_and_not_applied_to_either_roster(self):
        p = source_fixture()
        p["injuries"][0]["injuries"][0]["athlete"]["team"] = {"id": "2", "displayName": "Fictional Home"}
        r = normalize_espn_feed(p, fetched_at=NOW)
        q, audit = apply_espn_report(matchup(), r)
        self.assertTrue(r["teams"][0]["injuries"][0]["team_conflict"])
        self.assertEqual(len(audit["unmatched"]), 1)
        self.assertEqual(q["scenarios"][0]["away"]["players"][0]["status"], "UNKNOWN")

    def test_traded_injured_player_reuses_nba_rating_without_using_espn_id(self):
        p = source_fixture(status="Out")
        p["injuries"][0].update(id="2", displayName="Fictional Home")
        p["injuries"][0]["injuries"][0]["athlete"]["team"] = {"id": "2", "displayName": "Fictional Home"}
        q, audit = apply_espn_report(matchup(), normalize_espn_feed(p, fetched_at=NOW))
        added = q["home"]["players"][-1]
        self.assertEqual((added["player_id"], added["reference_minutes"], added["offensive_impact"]), ("A1", 0, 6))
        self.assertEqual(len(audit["added_players"]), 1)
        self.assertEqual(len(audit["roster_conflicts"]), 1)
        self.assertTrue(all(s["away"]["players"][0]["minutes"] == 0 for s in q["scenarios"]))

    def test_injury_metadata_is_archived_and_changes_snapshot_identity(self):
        p, _ = apply_espn_report(matchup(), report())
        q, _ = apply_espn_report(matchup(), report(return_date="2026-10-24"))
        a, b = evaluate_package(p), evaluate_package(q)
        self.assertNotEqual(a["snapshot_id"], b["snapshot_id"])
        saved = json.loads(snapshot_json(p, a))
        self.assertEqual(saved["inputs"]["scenarios"][0]["away"]["players"][0]["injury"]["estimated_return_date"], "2026-10-23")

    def test_scenarios_cannot_use_different_injury_metadata(self):
        p, _ = apply_espn_report(matchup(), report())
        p["scenarios"][1]["away"]["players"][0]["injury"]["estimated_return_date"] = "2026-10-24"
        with self.assertRaisesRegex(InputError, "same observed injury report"):
            evaluate_package(p)

    def test_manual_status_disagreement_retains_source_and_is_flagged(self):
        p, _ = apply_espn_report(matchup(), report())
        p["scenarios"] = [p["scenarios"][0]]
        row = p["scenarios"][0]["away"]["players"][0]
        row.update(status="AVAILABLE", assumption="")
        result = evaluate_package(p)
        self.assertEqual(row["injury"]["source_status"], "Day-To-Day")
        self.assertTrue(any("edited status differs" in f for f in result["scenarios"][0]["flags"]))

    def test_model_rejects_injury_metadata_observed_after_prediction_cutoff(self):
        p, _ = apply_espn_report(matchup(), report())
        for scenario in p["scenarios"]:
            scenario["away"]["players"][0]["injury"]["observed_at"] = "2026-10-22T13:01:00Z"
        with self.assertRaisesRegex(InputError, "later than the projection cutoff"):
            evaluate_package(p)


if __name__ == "__main__":
    unittest.main()
