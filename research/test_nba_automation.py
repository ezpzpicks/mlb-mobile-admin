"""Scheduled policy, first-lock persistence, overnight settlement and isolation."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from builders.nba_automation import NOTE, POLICY, VERSIONS, automated_package, phase_for_game
from builders.nba_live import digest, prepare_live_package
from builders.nba_model import InputError, MODEL_VERSION, evaluate_package
from builders.nba_tracking import lock_prediction, result_observation, tracking_report
from scripts import run_nba_builder as runner
from scripts import run_scheduled_builders as dispatch
from shared import nba_tracking_storage as store
from test_nba_live import NOW, fixture, live_package
from nba_market_fixtures import market_rows
from builders.nba_market import apply_market


def automatic_record(now=NOW, *, phase="early"):
    package, game = live_package(now)
    if phase == "pregame":
        game["tipoff"] = (now + timedelta(minutes=30)).isoformat()
        package["context"]["tipoff"] = game["tipoff"]
        package["live_provenance"]["game"]["tipoff"] = game["tipoff"]
    package = apply_market(package, market_rows(game, now), game, now)
    package = automated_package(package, phase)
    return lock_prediction(package, package["scenarios"][0]["name"], game, locked_at=now,
        roster_reviewed=False, minutes_reviewed=False, review_note=NOTE, automation_policy=POLICY)


class AutomaticPolicyTests(unittest.TestCase):
    def test_automatic_records_do_not_claim_human_review_and_keep_manual_version_separate(self):
        automatic = automatic_record()
        self.assertFalse(automatic["roster_reviewed"])
        self.assertFalse(automatic["minutes_reviewed"])
        self.assertEqual(automatic["review_method"], "automated")
        self.assertEqual(automatic["core_model_version"], MODEL_VERSION)
        self.assertEqual(automatic["model_version"], VERSIONS["early"])
        self.assertFalse(automatic["eligible_for_posting"])
        self.assertFalse(automatic["selected"]["lineup_ready"])
        self.assertEqual(automatic["selected"]["market"]["source"], "ScoresAndOdds")
        other = automatic_record(phase="pregame")
        self.assertNotEqual(automatic["record_id"], other["record_id"])
        self.assertEqual(len(tracking_report([automatic, other], [])["summary"]), 2)

    def test_manual_acknowledgements_cannot_be_replaced_by_arbitrary_policy(self):
        package, game = live_package()
        for policy in (None, "invented-policy"):
            with self.assertRaises(InputError):
                lock_prediction(package, package["scenarios"][0]["name"], game, locked_at=NOW,
                    roster_reviewed=False, minutes_reviewed=False, review_note=NOTE, automation_policy=policy)
        package = apply_market(package, market_rows(game, NOW), game, NOW)
        package = automated_package(package, "early")
        with self.assertRaisesRegex(InputError, "human"):
            lock_prediction(package, package["scenarios"][0]["name"], game, locked_at=NOW,
                roster_reviewed=True, minutes_reviewed=True, review_note=NOTE, automation_policy=POLICY)

    def test_changed_automatic_minutes_or_phase_cannot_be_locked(self):
        package, game = live_package()
        package = automated_package(package, "early")
        package["scenarios"][0]["away"]["players"][0]["minutes"] += 1
        with self.assertRaisesRegex(InputError, "rotations were changed"):
            lock_prediction(package, package["scenarios"][0]["name"], game, locked_at=NOW,
                roster_reviewed=False, minutes_reviewed=False, review_note=NOTE, automation_policy=POLICY)
        with self.assertRaises(InputError): automatic_record(phase="invented")

    def test_out_and_doubtful_are_zero_and_named_uncertainty_has_sensitivity_scenarios(self):
        package, _ = live_package()
        # Synthetic status change solely tests the fixed rotation policy.
        players = package["scenarios"][0]["away"]["players"]
        players[0]["status"], players[1]["status"], players[2]["status"] = "OUT", "DOUBTFUL", "QUESTIONABLE"
        p = automated_package(package, "early")
        base = p["scenarios"][0]["away"]
        self.assertEqual(base["players"][0]["minutes"], 0)
        self.assertEqual(base["players"][1]["minutes"], 0)
        self.assertAlmostEqual(sum(x["minutes"] for x in base["players"]), 240)
        self.assertGreater(base["players"][2]["minutes"], 0)
        self.assertGreater(len(p["scenarios"]), 1)
        for scenario in p["scenarios"]:
            for side in ("away", "home"):
                self.assertFalse(scenario[side]["starters_confirmed"])
                self.assertTrue(all(x["minutes"] <= x["minutes_limit"] for x in scenario[side]["players"]))
        result = evaluate_package(p)
        self.assertFalse(result["eligible_for_posting"])
        self.assertTrue(any(s["away"]["players"][2]["minutes"] == 0 for s in p["scenarios"][1:]))

    def test_window_rejects_live_postponed_unknown_times_and_too_late_starts(self):
        _, game = live_package()
        self.assertEqual(phase_for_game(game, NOW), "early")
        self.assertEqual(phase_for_game(game, NOW + timedelta(minutes=90)), "pregame")
        self.assertIsNone(phase_for_game(game, NOW + timedelta(minutes=118)))
        self.assertIsNone(phase_for_game(game, NOW + timedelta(hours=2)))
        for changes in ({"status":"STATUS_POSTPONED"}, {"state":"in"}, {"time_valid":False}):
            self.assertIsNone(phase_for_game({**game, **changes}, NOW))

    def test_automatic_tracking_cannot_select_a_sensitivity_alternative(self):
        package, game = live_package()
        package["scenarios"][0]["away"]["players"][0]["status"] = "QUESTIONABLE"
        package = apply_market(package, market_rows(game, NOW), game, NOW)
        package = automated_package(package, "early")
        self.assertGreater(len(package["scenarios"]), 1)
        with self.assertRaisesRegex(InputError, "primary scenario"):
            lock_prediction(package, package["scenarios"][1]["name"], game, locked_at=NOW,
                roster_reviewed=False, minutes_reviewed=False, review_note=NOTE, automation_policy=POLICY)


class ScheduledPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.tipoff = self.now + timedelta(hours=2)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.sql, self.events = [], []
        def execute(sql):
            self.sql.append(sql)
            return [dict(r) for r in self.conn.execute(sql).fetchall()]
        self.db = SimpleNamespace(_execute=execute, _result_rows=lambda r:r,
            _sql_text=lambda value: "'" + str(value).replace("'", "''") + "'")
        self.stack = ExitStack()
        self.stack.enter_context(patch("shared.nba_tracking_storage._backend", return_value=self.db))
        self.stack.enter_context(patch("shared.turso_storage.is_turso_ready", return_value=True))
        self.stack.enter_context(patch.dict(os.environ, {"NBA_AUTO_BUILDER_ENABLED":"1"}))
        self.stack.enter_context(patch.object(runner, "emit", side_effect=lambda event, **kw:self.events.append((event, kw))))
        self.stack.enter_context(patch.object(runner, "seeds", return_value=[]))
        self.args = SimpleNamespace(commit=True, evaluate=False, ignore_window=True, limit=16)

    def tearDown(self):
        self.stack.close()
        self.conn.close()

    def run_at(self, now, *, final=False, bad_roster=False, missing_market=False):
        bundle, inputs, injury = fixture(now)
        game = inputs["game"]
        game["tipoff"] = self.tipoff.isoformat()
        if final:
            game.update(completed=True, status="STATUS_FINAL", state="post", period=4)
            game["away"]["score"], game["home"]["score"] = 110, 115
        with ExitStack() as stack:
            stack.enter_context(patch.object(runner, "clock", return_value=now))
            stack.enter_context(patch.object(runner, "fetch_slate", return_value={"games":[game], "excluded":[]}))
            inputs_call = stack.enter_context(patch.object(runner, "fetch_matchup_inputs", return_value=inputs,
                side_effect=InputError("synthetic roster failure") if bad_roster else None))
            injuries_call = stack.enter_context(patch.object(runner, "fetch_espn_injuries", return_value=injury))
            game_call = stack.enter_context(patch.object(runner, "fetch_game", return_value=(game, {})))
            stack.enter_context(patch.object(runner, "read_market_rows", return_value=[] if missing_market else market_rows(game, now)))
            stack.enter_context(patch.object(runner, "checked_box_minutes", return_value={}))
            # Use the small synthetic rating bundle instead of production files.
            from pathlib import Path
            read_text = Path.read_text
            def fixture_read(path, *a, **kw):
                import json
                return json.dumps(bundle) if path.name == "nba_ratings.json" else read_text(path, *a, **kw)
            stack.enter_context(patch("pathlib.Path.read_text", fixture_read))
            result = runner.run(self.args)
        return result, inputs_call.call_count, injuries_call.call_count, game_call.call_count

    def test_first_and_pregame_locks_persist_once_and_repeat_ticks_fetch_no_rosters_or_injuries(self):
        self.assertEqual(self.run_at(self.now)[:3], (0, 1, 1))
        first = store.load_ledger()[0][0]
        self.assertEqual(self.run_at(self.now + timedelta(minutes=30))[:3], (0, 0, 0))
        self.assertEqual(store.load_ledger()[0], [first])
        self.assertEqual(self.run_at(self.now + timedelta(minutes=90))[:3], (0, 1, 1))
        records = store.load_ledger()[0]
        self.assertEqual(len(records), 2)
        self.assertEqual({r["model_version"] for r in records}, set(VERSIONS.values()))
        self.assertIn(first, records)
        self.assertEqual(self.run_at(self.now + timedelta(minutes=100))[:3], (0, 0, 0))
        self.assertFalse(any("dataset_rows" in sql or sql.startswith(("UPDATE ", "DELETE ")) for sql in self.sql))

    def test_final_score_settles_both_versions_once_and_then_stops_result_polling(self):
        self.run_at(self.now)
        self.run_at(self.now + timedelta(minutes=90))
        self.assertEqual(self.run_at(self.now + timedelta(hours=5), final=True)[:3], (0, 0, 0))
        records, observations = store.load_ledger()
        self.assertEqual(len(observations), 1)
        report = tracking_report(records, observations)
        self.assertTrue(all(g["graded_games"] == 1 for g in report["summary"]))
        self.assertEqual(self.run_at(self.now + timedelta(hours=6), final=True), (0, 0, 0, 0))

    def test_plan_and_evaluate_do_not_create_tables_or_write(self):
        self.args.commit = False
        self.assertEqual(self.run_at(self.now)[:3], (0, 0, 0))
        self.assertEqual(list(self.conn.execute("SELECT name FROM sqlite_master")), [])
        self.args.evaluate = True
        self.assertEqual(self.run_at(self.now)[:3], (0, 1, 1))
        self.assertEqual(list(self.conn.execute("SELECT name FROM sqlite_master")), [])

    def test_missing_enable_flag_blocks_before_a_write_and_bad_input_is_logged(self):
        with patch.dict(os.environ, {"NBA_AUTO_BUILDER_ENABLED":"0"}):
            with self.assertRaises(InputError): self.run_at(self.now)
        self.assertFalse(self.sql)
        self.assertEqual(self.run_at(self.now, bad_roster=True)[0], 1)
        self.assertEqual(store.load_ledger(), ([], []))
        self.assertTrue(any(e == "game_failed" for e, _ in self.events))

    def test_window_uses_eastern_daylight_and_standard_time(self):
        self.args.ignore_window = False
        for now in (datetime(2026, 10, 22, 12, tzinfo=timezone.utc),
                    datetime(2026, 11, 22, 13, tzinfo=timezone.utc)):
            with patch.object(runner, "clock", return_value=now), patch.object(runner, "fetch_slate") as fetch:
                self.assertEqual(runner.run(self.args), 0)
                fetch.assert_not_called()

    def test_result_refetch_does_not_write_identical_facts_but_saves_a_correction(self):
        self.run_at(self.now)
        self.run_at(self.now + timedelta(hours=5), final=True)
        previous = store.load_ledger()[1][0]
        game = deepcopy(previous["game"])
        game["source"]["observed_at"] = (self.now + timedelta(hours=6)).isoformat()
        self.assertFalse(store.save_observation_if_changed(result_observation(game, {})))
        game["home"]["score"] += 1
        self.assertTrue(store.save_observation_if_changed(result_observation(game, {})))
        self.assertEqual(len(store.load_ledger()[1]), 2)

    def test_indexed_reads_use_game_version_and_recent_time_filters(self):
        self.run_at(self.now)
        self.assertEqual(store.prediction_keys(["123456"], [VERSIONS["early"]]), {("123456", VERSIONS["early"])})
        store.pending_game_ids((self.now - timedelta(days=7)).isoformat())
        plans = self.conn.execute("EXPLAIN QUERY PLAN SELECT game_id FROM nba_research_predictions WHERE locked_at>='2026-01-01'").fetchall()
        self.assertTrue(any("nba_research_predictions_by_time" in str(tuple(p)) for p in plans))

    def test_missing_market_waits_without_loading_rosters_or_saving_a_forecast(self):
        self.assertEqual(self.run_at(self.now, missing_market=True)[:3], (0, 0, 0))
        self.assertEqual(store.load_ledger()[0], [])
        self.assertTrue(any(event == "waiting_for_market" for event, _ in self.events))


class SchedulerIsolationTests(unittest.TestCase):
    def test_nba_failure_or_timeout_does_not_skip_ncaaf_and_children_cannot_recurse(self):
        import subprocess
        for first in (SimpleNamespace(returncode=1), subprocess.TimeoutExpired("NBA", 480), OSError("synthetic process failure")):
            with patch.object(dispatch.subprocess, "run", side_effect=[first, SimpleNamespace(returncode=0)]) as run:
                self.assertEqual(dispatch.main(), 1)
                self.assertEqual(run.call_count, 2)
                self.assertTrue(run.call_args_list[0].args[0][2].endswith("run_nba_builder.py"))
                self.assertTrue(run.call_args_list[1].args[0][2].endswith("run_ncaaf_builder.py"))
                self.assertEqual(run.call_args_list[1].kwargs["env"][dispatch.CHILD_FLAG], "1")

    def test_healthy_children_finish_successfully(self):
        with patch.object(dispatch.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(dispatch.main(), 0)


if __name__ == "__main__":
    unittest.main()
