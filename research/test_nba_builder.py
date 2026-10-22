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


class FakeStreamlit(ModuleType):
    def __init__(self, *, load_demo=False, submit=False, invalid_minutes=False, uploaded=None):
        super().__init__("streamlit")
        self.session_state = {}
        self.load_demo = load_demo
        self.submit = submit
        self.invalid_minutes = invalid_minutes
        self.uploaded = uploaded
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
        return value

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
        return self.submit

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


if __name__ == "__main__":
    unittest.main()
