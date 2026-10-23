"""Headless builder flow tests; storage is mocked and no live data is fetched."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import importlib
import json
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from builders.nba_example import example_package
from builders.nba_model import evaluate_package
from builders.nba_injuries import apply_espn_report
from test_nba_injuries import matchup, report


class FakeStreamlit(ModuleType):
    def __init__(self, *, load_demo=False, submit=False, invalid_minutes=False, uploaded=None, values=None):
        super().__init__("streamlit")
        self.session_state = {}
        self.load_demo = load_demo
        self.submit = submit
        self.invalid_minutes = invalid_minutes
        self.uploaded = uploaded
        self.values = values or {}
        self.errors, self.warnings = [], []
        self.column_config = SimpleNamespace(NumberColumn=lambda *a, **k: None,
            SelectboxColumn=lambda *a, **k: None, CheckboxColumn=lambda *a, **k: None)

    def expander(self, *args, **kwargs):
        return nullcontext()

    def form(self, *args, **kwargs):
        return nullcontext()

    def button(self, label, **kwargs):
        return label == "Load fictional demo" and self.load_demo

    def file_uploader(self, *args, **kwargs):
        return self.uploaded

    def text_input(self, label, *, value="", **kwargs):
        return self.values.get(label, value)

    def selectbox(self, label, options, *, index=0, **kwargs):
        return options[index]

    def number_input(self, label, *, value=0, **kwargs):
        return value

    def checkbox(self, label, *, value=False, **kwargs):
        return value

    def data_editor(self, frame, **kwargs):
        output = frame.copy()
        if self.invalid_minutes and "minutes" in output.columns:
            output.loc[0, "minutes"] = -1
        return output

    def form_submit_button(self, *args, **kwargs):
        return self.submit and args[0] != "Add roster player"

    def error(self, message):
        self.errors.append(str(message))

    def warning(self, message):
        self.warnings.append(str(message))

    def __getattr__(self, name):
        if name in {"caption", "subheader", "info", "write", "markdown", "dataframe", "download_button", "success"}:
            return lambda *args, **kwargs: None
        raise AttributeError(name)


def load_builder(st):
    missing = object()
    previous = sys.modules.get("streamlit", missing)
    sys.modules["streamlit"] = st
    try:
        sys.modules.pop("builders.nba_builder", None)
        return importlib.import_module("builders.nba_builder")
    finally:
        if previous is missing:
            sys.modules.pop("streamlit", None)
        else:
            sys.modules["streamlit"] = previous


class BuilderFlowTests(unittest.TestCase):
    def test_ordinary_rerender_does_not_fetch_espn(self):
        st = FakeStreamlit(load_demo=True, submit=True)
        builder = load_builder(st)
        with patch("builders.nba_injuries.fetch_espn_injuries") as fetch:
            builder.render()
        fetch.assert_not_called()

    def test_explicit_espn_fetch_is_one_league_request_without_storage(self):
        st = FakeStreamlit()
        st.button = lambda label, **kwargs: label == "Fetch latest ESPN injuries"
        builder = load_builder(st)
        with patch("builders.nba_injuries.fetch_espn_injuries", return_value=report()) as fetch:
            with patch.dict(sys.modules, {"shared.storage": None}):
                builder._espn_inputs(st.session_state.setdefault(builder.STATE_KEY, {}))
        fetch.assert_called_once()
        self.assertEqual(st.session_state[builder.STATE_KEY]["espn_report"]["injury_count"], 1)
        self.assertFalse(st.errors)

    def test_espn_refresh_failure_keeps_the_old_timestamp_and_clears_result(self):
        from builders.nba_model import InputError
        st = FakeStreamlit()
        st.button = lambda label, **kwargs: label == "Fetch latest ESPN injuries"
        builder = load_builder(st)
        old = report()
        st.session_state[builder.STATE_KEY] = {"saved_ids": [], "package": matchup(), "espn_report": old,
                                             "result": {"old": "projection"}}
        with patch("builders.nba_injuries.fetch_espn_injuries", side_effect=InputError("feed unavailable")):
            builder._espn_inputs(st.session_state[builder.STATE_KEY])
        self.assertIs(st.session_state[builder.STATE_KEY]["espn_report"], old)
        self.assertIsNone(st.session_state[builder.STATE_KEY]["result"])
        self.assertTrue(any("original timestamp" in error for error in st.errors))

    def test_applying_out_report_shows_replacement_minutes_and_invalidates_old_result(self):
        st = FakeStreamlit()
        st.button = lambda label, **kwargs: label == "Use ESPN injuries in this matchup and update cutoff"
        builder = load_builder(st)
        st.session_state[builder.STATE_KEY] = {"saved_ids": [], "package": matchup(), "espn_report": report(status="Out"),
                                             "result": {"old": "projection"}}
        with patch.dict(sys.modules, {"shared.storage": None}):
            builder._espn_inputs(st.session_state[builder.STATE_KEY])
        state = st.session_state[builder.STATE_KEY]
        self.assertIsNone(state["result"])
        self.assertEqual(state["package"]["scenarios"][0]["away"]["players"][0]["minutes"], 0)
        self.assertTrue(any("36 minutes need assignment" in w for w in st.warnings))
        self.assertFalse(st.errors)

    def test_espn_return_date_survives_rotation_editor_and_projection(self):
        st = FakeStreamlit(submit=True)
        builder = load_builder(st)
        package, _ = apply_espn_report(matchup(), report())
        st.session_state[builder.STATE_KEY] = {"saved_ids": [], "package": package, "espn_report": report()}
        with patch("builders.nba_injuries.fetch_espn_injuries") as fetch:
            builder.render()
        state = st.session_state[builder.STATE_KEY]
        self.assertFalse(st.errors)
        self.assertIsNotNone(state["result"])
        self.assertEqual(state["result_package"]["scenarios"][0]["away"]["players"][0]["injury"]["estimated_return_date"], "2026-10-23")
        self.assertFalse(state["result"]["eligible_for_posting"])
        fetch.assert_not_called()

    def test_post_tipoff_espn_import_cannot_leave_an_old_projection_displayed(self):
        st = FakeStreamlit()
        st.button = lambda label, **kwargs: label == "Use ESPN injuries in this matchup and update cutoff"
        builder = load_builder(st)
        old = matchup()
        r = report()
        r["fetched_at"] = "2026-10-22T16:00:00Z"  # After the fixture's 15:00 tipoff.
        st.session_state[builder.STATE_KEY] = {"saved_ids": [], "package": old, "espn_report": r,
                                             "result": {"old": "projection"}}
        builder._espn_inputs(st.session_state[builder.STATE_KEY])
        state = st.session_state[builder.STATE_KEY]
        self.assertIsNone(state["result"])
        self.assertIs(state["package"], old)
        self.assertTrue(any("before tipoff" in error for error in st.errors))

    def test_empty_builder_needs_no_database_or_network(self):
        st = FakeStreamlit()
        builder = load_builder(st)
        with patch.dict(sys.modules, {"shared.storage": None}):
            builder.render()
        self.assertIsNone(st.session_state[builder.STATE_KEY].get("result"))
        self.assertFalse(st.errors)

    def test_demo_can_be_edited_and_evaluated_without_database(self):
        st = FakeStreamlit(load_demo=True, submit=True)
        builder = load_builder(st)
        with patch.dict(sys.modules, {"shared.storage": None}):
            builder.render()
        result = st.session_state[builder.STATE_KEY]["result"]
        self.assertEqual(len(result["scenarios"]), 2)
        self.assertTrue(result["scenario_coverage_complete"])
        self.assertFalse(result["eligible_for_posting"])
        self.assertTrue(any("Fictional example" in w for w in st.warnings))
        self.assertFalse(st.errors)

    def test_invalid_edited_rotation_does_not_leave_a_valid_result(self):
        st = FakeStreamlit(load_demo=True, submit=True, invalid_minutes=True)
        builder = load_builder(st)
        builder.render()
        self.assertIsNone(st.session_state[builder.STATE_KEY]["result"])
        self.assertTrue(any("between 0 and 48" in error for error in st.errors))

    def test_bad_import_is_reported_without_replacing_existing_inputs(self):
        upload = SimpleNamespace(getvalue=lambda: b"not valid JSON")
        st = FakeStreamlit(uploaded=upload)
        builder = load_builder(st)
        builder.render()
        self.assertTrue(st.errors)
        self.assertNotIn("package", st.session_state[builder.STATE_KEY])

    def test_explicit_save_uses_only_the_nba_research_dataset(self):
        st = FakeStreamlit()
        builder = load_builder(st)
        package = example_package(datetime(2026, 10, 22, 13, tzinfo=timezone.utc))
        package["is_demo"] = False
        result = evaluate_package(package)
        storage = ModuleType("shared.storage")
        active, calls = [], []

        @contextmanager
        def sport_storage(sport):
            active.append(sport)
            try:
                yield
            finally:
                active.pop()

        def append_row(tab, row, columns):
            calls.append((active[-1], tab, row, columns))
            return True

        storage.sport_storage = sport_storage
        storage.append_row = append_row
        with patch.dict(sys.modules, {"shared.storage": storage}):
            self.assertTrue(builder._save_snapshot(package, result))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ("NBA", "nba_research_snapshots"))
        archived = json.loads(calls[0][2]["Payload JSON"])
        self.assertEqual(archived["inputs"]["context"]["game_id"], package["context"]["game_id"])
        self.assertFalse(archived["projection"]["eligible_for_posting"])
        self.assertEqual(active, [])

    def test_demo_save_is_rejected_before_any_database_import(self):
        st = FakeStreamlit()
        builder = load_builder(st)
        package = example_package(datetime(2026, 10, 22, 13, tzinfo=timezone.utc))
        with patch.dict(sys.modules, {"shared.storage": None}):
            self.assertFalse(builder._save_snapshot(package, evaluate_package(package)))

    @staticmethod
    def ratings_fixture():
        p = example_package(datetime(2026, 10, 22, 13, tzinfo=timezone.utc))
        impacts = [{k: v for k, v in player.items() if k != "reference_minutes"}
                   for side in ("away", "home") for player in p[side]["players"]]
        return {"schema": "nba-ratings-v1", "as_of": p["context"]["as_of"],
            "data_available_at": p["away"]["ratings_as_of"], "first_game_date": "2026-10-01",
            "data_through": "2026-10-21", "research_only": True, "eligible_for_posting": False,
            "player_impacts": impacts, "teams": [p["away"], p["home"]],
            "league_efficiency": 115, "league_pace": 100, "home_court_points": 2,
            "training_games": 20, "player_training_games": 10, "source": "Synthetic ratings tests",
            "player_ridge": 1500, "team_ridge": 100}

    def test_ratings_import_does_not_fetch_data_or_mark_historical_news_current(self):
        bundle = self.ratings_fixture()
        upload = SimpleNamespace(getvalue=lambda: json.dumps(bundle).encode())
        st = FakeStreamlit(uploaded=upload)
        builder = load_builder(st)
        with patch.dict(sys.modules, {"shared.storage": None}):
            builder.render()
        self.assertEqual(st.session_state[builder.STATE_KEY]["ratings"]["schema"], "nba-ratings-v1")
        self.assertIsNone(st.session_state[builder.STATE_KEY].get("package"))
        self.assertTrue(any("Historical ratings" in w for w in st.warnings))
        self.assertFalse(st.errors)

    def test_ratings_matchup_can_be_created_and_projected_without_a_database(self):
        upload = SimpleNamespace(getvalue=lambda: json.dumps(self.ratings_fixture()).encode())
        st = FakeStreamlit(uploaded=upload, submit=True, values={"NBA game ID": "synthetic-matchup",
            "Projection cutoff (ISO timestamp with timezone)": "2026-10-22T14:00:00Z",
            "Scheduled tipoff (ISO timestamp with timezone)": "2026-10-22T23:00:00Z"})
        builder = load_builder(st)
        with patch.dict(sys.modules, {"shared.storage": None}):
            builder.render()
        state = st.session_state[builder.STATE_KEY]
        self.assertFalse(st.errors, st.errors)
        self.assertFalse(state["result"]["eligible_for_posting"])
        self.assertFalse(state["result"]["scenarios"][0]["lineup_ready"])
        self.assertFalse(state["package"]["scenarios"][0]["away"]["report_complete"])
        self.assertTrue(all(p["status"] == "UNKNOWN" for p in state["package"]["scenarios"][0]["away"]["players"]))

    def test_invalid_bundle_cannot_replace_a_valid_matchup(self):
        st = FakeStreamlit()
        builder = load_builder(st)
        state = {"package": example_package()}
        original = state["package"]
        bundle = self.ratings_fixture()
        bundle["data_available_at"] = "2027-01-01T00:00:00Z"
        with self.assertRaises(ValueError):
            builder._import_json(json.dumps(bundle).encode(), state)
        self.assertIs(state["package"], original)

    def test_manual_roster_addition_does_not_invent_minutes_or_current_report(self):
        st = FakeStreamlit(load_demo=True, values={"Player NBA ID": "NEW-PLAYER",
            "Player name (required for a newcomer)": "Synthetic newcomer"})
        st.form_submit_button = lambda label, **kwargs: label == "Add roster player"
        builder = load_builder(st)
        with patch.dict(sys.modules, {"shared.storage": None}):
            builder.render()
        package = st.session_state[builder.STATE_KEY]["package"]
        self.assertFalse(st.errors)
        self.assertEqual(package["away"]["players"][-1]["impact_basis"], "prior_only")
        self.assertEqual(package["scenarios"][0]["away"]["players"][-1]["minutes"], 0)
        self.assertFalse(package["scenarios"][0]["away"]["report_complete"])


if __name__ == "__main__":
    unittest.main()
