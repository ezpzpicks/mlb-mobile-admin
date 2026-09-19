from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace

import pandas as pd

from builders import cfb_builder
from builders import cfb_runtime_guard


def _verified_ratings() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Team": f"Team {index}",
                "Advanced Data Available": True,
                "Roster Data Available": True,
                "Source": "ESPN/SportsDataverse full-input-verified snapshot",
                "FBS Games": 1,
            }
            for index in range(24)
        ]
    )


def _test_canonical_data_joins() -> None:
    with cfb_builder._TEAM_NAME_ALIASES_LOCK:
        cfb_builder._TEAM_NAME_ALIASES.clear()
    cfb_builder._register_team_alias("Auburn Tigers", "Auburn")

    current = pd.DataFrame(
        [
            {
                "team": "Auburn",
                "athlete_id": "1",
                "name": "Returning Quarterback",
                "position": "QB",
                "class": "SR",
                "experience_years": 4,
            }
        ]
    )
    previous = current.copy()
    roster = cfb_builder._roster_priors(
        2026,
        ["Auburn Tigers"],
        current=current,
        previous=previous,
    )
    assert roster.iloc[0]["Team"] == "Auburn", roster
    assert float(roster.iloc[0]["Returning Production"]) > 0.0, roster

    ratings = pd.DataFrame([{"Team": "Auburn", "Power Rating": 9.5}])
    matched = cfb_builder._rating_row(ratings, "Auburn Tigers")
    assert float(matched["Power Rating"]) == 9.5, matched

    original_teams = cfb_builder._espn_teams_payload
    original_games = cfb_builder._espn_games_payload
    original_bundle = cfb_builder._open_feature_bundle
    try:
        cfb_builder._espn_teams_payload = lambda season: []
        cfb_builder._espn_games_payload = lambda season: []
        cfb_builder._open_feature_bundle = lambda season, week: {
            "metrics": pd.DataFrame(
                [{"Team": "Auburn", "Advanced Plays": 150, "Returning Production": 0.8}]
            ),
            "pbp_available": True,
            "roster_available": True,
        }
        features, availability = cfb_builder._season_features(2026, 3)
    finally:
        cfb_builder._espn_teams_payload = original_teams
        cfb_builder._espn_games_payload = original_games
        cfb_builder._open_feature_bundle = original_bundle

    assert list(features["Team"]) == ["Auburn"], features
    assert availability["advanced"] is True
    assert availability["roster"] is True


def _test_nonblocking_strict_rebuild() -> None:
    release_build = threading.Event()
    ratings = _verified_ratings()

    with TemporaryDirectory() as directory:
        data_dir = Path(directory)
        for filename in (
            "cfbfastR_cfb_pbp_2026.parquet",
            "espn_cfb_rosters_2024.parquet",
            "espn_cfb_rosters_2025.parquet",
            "espn_cfb_rosters_2026.parquet",
        ):
            (data_dir / filename).write_bytes(b"x" * 2048)

        class FakeBuilder:
            OPEN_DATA_DIR = data_dir
            RATING_COLUMNS = list(ratings.columns)
            _EZPZ_CFB_RUNTIME_GUARD = False

            @staticmethod
            def _bool(value):
                return bool(value)

            @staticmethod
            def _num(value, default=0.0):
                try:
                    return float(value)
                except Exception:
                    return float(default)

            @staticmethod
            def _persistent_pbp_metrics_ready(season):
                return True

            @staticmethod
            def _download_open_asset(*args, **kwargs):
                raise AssertionError("all strict-gate assets should already be present")

            @staticmethod
            def _get_cached_ratings(season, week):
                return pd.DataFrame(columns=ratings.columns)

            @staticmethod
            def build_team_ratings(season, week):
                release_build.wait(timeout=5.0)
                return ratings.copy()

            @staticmethod
            def slate_row(result):
                return pd.DataFrame()

            @staticmethod
            def _render_build():
                return None

            @staticmethod
            def _clear_automatic_state():
                return None

            _auto_save_selected_projection = staticmethod(lambda result: None)
            _read_open_parquet = staticmethod(lambda path, aliases: pd.DataFrame())
            _parse_games = staticmethod(lambda payload, season: pd.DataFrame())
            _ensure_automatic_schedule = staticmethod(lambda *args, **kwargs: pd.DataFrame())
            _schedule_date_series = staticmethod(lambda frame: pd.Series(dtype="object"))
            _available_slate_dates = staticmethod(lambda frame: [])
            _default_slate_date = staticmethod(lambda frame, today=None: None)
            _current_cfb_season = staticmethod(lambda today=None: 2026)

        fake = FakeBuilder()
        original_streamlit = cfb_runtime_guard.st
        cfb_runtime_guard.st = SimpleNamespace(session_state={})
        try:
            with cfb_runtime_guard._RATINGS_BUILD_JOB_LOCK:
                cfb_runtime_guard._RATINGS_BUILD_JOBS.pop((2026, 3), None)
            cfb_runtime_guard.install_runtime_guard(fake)

            started = time.perf_counter()
            first = fake._ensure_automatic_ratings(2026, 3)
            request_seconds = time.perf_counter() - started
            assert first.empty
            assert request_seconds < 0.5, request_seconds
            assert "still preparing" in cfb_runtime_guard.st.session_state[
                "cfb_auto_ratings_warning"
            ].lower()

            release_build.set()
            deadline = time.time() + 5.0
            while time.time() < deadline:
                with cfb_runtime_guard._RATINGS_BUILD_JOB_LOCK:
                    job = cfb_runtime_guard._RATINGS_BUILD_JOBS[(2026, 3)]
                if job.done():
                    break
                time.sleep(0.01)
            completed = fake._ensure_automatic_ratings(2026, 3)
            assert len(completed) == len(ratings), completed
            assert fake._ratings_complete_for_build(completed, 3)
            assert "cfb_auto_ratings_warning" not in cfb_runtime_guard.st.session_state
        finally:
            release_build.set()
            cfb_runtime_guard.st = original_streamlit
            with cfb_runtime_guard._RATINGS_BUILD_JOB_LOCK:
                cfb_runtime_guard._RATINGS_BUILD_JOBS.pop((2026, 3), None)


def main() -> None:
    _test_canonical_data_joins()
    _test_nonblocking_strict_rebuild()
    print("CFB full-data readiness smoke test passed")


if __name__ == "__main__":
    main()
