"""ESPN source semantics, identity safety, dated inputs and replacement minutes."""
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from builders.nba_example import example_package
from builders.nba_injuries import ESPN_FEED, apply_espn_report, fetch_espn_injuries, normalize_espn_feed
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

    def test_successful_fetch_uses_one_bounded_league_request(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(source_fixture()).encode()
        with patch("builders.nba_injuries.urlopen", return_value=response) as request:
            r = fetch_espn_injuries()
        self.assertEqual(request.call_count, 1)
        self.assertEqual(r["injury_count"], 1)
        self.assertEqual(request.call_args.kwargs["timeout"], 15)

    def test_network_failure_is_visible(self):
        with patch("builders.nba_injuries.urlopen", side_effect=OSError("connection unavailable")), \
                patch("builders.nba_injuries.time.sleep"):
            with self.assertRaisesRegex(InputError, "could not be refreshed"):
                fetch_espn_injuries()


class EspnRecoveryTests(unittest.TestCase):
    def response(self, raw=None):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(source_fixture()).encode() if raw is None else raw
        return response

    def error(self, code, *, headers=None, body=b"<html>Access Denied</html>"):
        return HTTPError(ESPN_FEED, code, "source error", headers or {}, BytesIO(body))

    def fetch(self, outcomes):
        events = []
        with patch("builders.nba_injuries.urlopen", side_effect=outcomes) as request, \
                patch("builders.nba_injuries.time.sleep") as sleep:
            result = fetch_espn_injuries(on_event=lambda name, **details: events.append((name, details)))
        return result, events, request, sleep

    def test_http_rejections_recover_without_changing_endpoint_or_client_identity(self):
        result, events, request, sleep = self.fetch([
            self.error(403, headers={"Server": "example-edge", "Content-Type": "text/html"}),
            self.error(503), self.response()])
        self.assertEqual(result["injury_count"], 1)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 5])
        self.assertEqual({call.args[0].full_url for call in request.call_args_list}, {ESPN_FEED})
        self.assertEqual(len({call.args[0].get_header("User-agent") for call in request.call_args_list}), 1)
        failures = [details for name, details in events if name == "injury_request_failed"]
        self.assertEqual([item["http_status"] for item in failures], [403, 503])
        self.assertEqual(failures[0]["response_hints"], ["access_denied"])
        self.assertEqual(events[-1][0], "injury_fetch_succeeded")
        self.assertTrue(events[-1][1]["recovered"])
        self.assertEqual(events[-1][1]["attempt"], 3)

    def test_repeated_403_is_bounded_and_never_becomes_an_empty_injury_report(self):
        events = []
        with patch("builders.nba_injuries.urlopen", side_effect=[self.error(403) for _ in range(3)]) as request, \
                patch("builders.nba_injuries.time.sleep") as sleep:
            with self.assertRaisesRegex(InputError, "after 3 attempt.*HTTP 403"):
                fetch_espn_injuries(on_event=lambda name, **details: events.append((name, details)))
        self.assertEqual(request.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertFalse(events[-1][1]["retrying"])
        self.assertFalse(any(name == "injury_fetch_succeeded" for name, _ in events))

    def test_network_timeout_can_recover_and_is_distinct_from_an_http_rejection(self):
        result, events, _, _ = self.fetch([URLError(TimeoutError("synthetic timeout")), self.response()])
        self.assertEqual(result["injury_count"], 1)
        self.assertEqual(events[0][1]["failure_kind"], "transport_error")
        self.assertEqual(events[0][1]["reason_type"], "TimeoutError")
        self.assertNotIn("http_status", events[0][1])

    def test_short_retry_after_is_honored(self):
        _, _, _, sleep = self.fetch([self.error(429, headers={"Retry-After": "12"}), self.response()])
        sleep.assert_called_once_with(12)

    def test_long_cooldown_is_deferred_instead_of_retried_early(self):
        events = []
        with patch("builders.nba_injuries.urlopen", side_effect=self.error(429, headers={"Retry-After": "120"})) as request, \
                patch("builders.nba_injuries.time.sleep") as sleep:
            with self.assertRaisesRegex(InputError, "after 1 attempt.*HTTP 429"):
                fetch_espn_injuries(on_event=lambda name, **details: events.append((name, details)))
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()
        self.assertFalse(events[0][1]["retrying"])

    def test_authentication_and_missing_endpoint_errors_are_not_retried(self):
        for code in (401, 404):
            with self.subTest(code=code), \
                    patch("builders.nba_injuries.urlopen", side_effect=self.error(code)) as request, \
                    patch("builders.nba_injuries.time.sleep") as sleep:
                with self.assertRaisesRegex(InputError, f"HTTP {code}"):
                    fetch_espn_injuries()
                self.assertEqual(request.call_count, 1)
                sleep.assert_not_called()

    def test_explicit_security_challenge_is_reported_without_repeated_attempts(self):
        events = []
        with patch("builders.nba_injuries.urlopen", side_effect=self.error(403, body=b"Verify you are human: CAPTCHA")) as request:
            with self.assertRaises(InputError):
                fetch_espn_injuries(on_event=lambda name, **details: events.append((name, details)))
        self.assertEqual(request.call_count, 1)
        self.assertIn("security_challenge", events[0][1]["response_hints"])
        self.assertFalse(events[0][1]["retrying"])

    def test_diagnostics_omit_response_body_cookies_and_unapproved_headers(self):
        secret = "synthetic-value-that-must-not-appear"
        _, events, _, _ = self.fetch([self.error(403,
            headers={"Set-Cookie": secret, "Authorization": secret, "X-Request-Id": "trace-123", "Server": "edge\nextra"},
            body=("Access Denied " + secret).encode()), self.response()])
        encoded = json.dumps(events)
        self.assertNotIn(secret, encoded)
        headers = events[0][1]["response_headers"]
        self.assertEqual(headers["x-request-id"], "trace-123")
        self.assertEqual(headers["server"], "edge extra")
        self.assertTrue(events[0][1]["response_prefix_sha256"])

    def test_invalid_json_and_invalid_feed_are_validation_errors_without_retries(self):
        for raw in (b"<html>unexpected success body</html>", b'{"status":"error","injuries":[]}'):
            events = []
            with self.subTest(raw=raw), \
                    patch("builders.nba_injuries.urlopen", return_value=self.response(raw)) as request, \
                    patch("builders.nba_injuries.time.sleep") as sleep:
                with self.assertRaises(InputError):
                    fetch_espn_injuries(on_event=lambda name, **details: events.append((name, details)))
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()
            self.assertEqual(events[-1][0], "injury_response_rejected")
            self.assertEqual(events[-1][1]["failure_kind"], "validation_error")

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
