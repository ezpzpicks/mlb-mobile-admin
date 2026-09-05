"""Runtime safety guards for the interactive CFB builder.

The college builder is rerun by Streamlit on every widget interaction. These
patches keep those reruns read-mostly, avoid unnecessary Google Sheets writes,
and keep large SportsDataverse parquet reads below the Render memory ceiling.
"""
from __future__ import annotations

import copy
from datetime import datetime
import math
import threading
import time
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st


_SHEETS_LOCK = threading.Lock()


def _quota_error(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    text = str(exc or "").lower()
    return status == 429 or "429" in text or "quota" in text or "rate limit" in text


def _shared_storage_cooling_down() -> bool:
    try:
        from shared import storage

        checker = getattr(storage, "_quota_cooldown_active", None)
        return bool(callable(checker) and checker())
    except Exception:
        return False


def _start_shared_storage_cooldown() -> None:
    try:
        from shared import storage

        starter = getattr(storage, "_start_quota_cooldown", None)
        if callable(starter):
            starter()
    except Exception:
        pass


def _retry(operation):
    delays = (0.0, 2.0, 5.0, 12.0)
    last_exc: Exception | None = None
    for attempt, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        try:
            with _SHEETS_LOCK:
                return operation()
        except Exception as exc:  # pragma: no cover - network behavior
            last_exc = exc
            if not _quota_error(exc) or attempt == len(delays) - 1:
                raise
    if last_exc is not None:
        raise last_exc
    return None


def install_runtime_guard(cfb_builder: Any) -> None:
    """Install idempotent production guards on the already-imported CFB module."""
    if getattr(cfb_builder, "_EZPZ_CFB_RUNTIME_GUARD", False):
        return

    # ------------------------------------------------------------------
    # 1) Google Sheets: interactive edits are read-only until Save is pressed.
    # ------------------------------------------------------------------
    # The old helper upserted daily_slate after every +/- click. Streamlit reruns
    # on every widget change, so that behavior could generate a burst of full
    # worksheet writes and hit Google's 429 quota. Explicit Save buttons and the
    # automatic Slate batch still use the normal persistence paths.
    def _no_auto_save_selected_projection(result: dict[str, Any]) -> None:
        return None

    cfb_builder._auto_save_selected_projection = _no_auto_save_selected_projection

    # CFB writes are full-table snapshots. One update is sufficient; clearing the
    # worksheet first doubled request volume. Upsert tables normally grow or keep
    # the same row count. When a snapshot becomes shorter, clear only the stale
    # trailing rows with a single batch_clear after the main update.
    def _guarded_write_sheet(tab_name: str, dataframe: pd.DataFrame, columns: Iterable[str]) -> bool:
        columns = list(columns)
        if _shared_storage_cooling_down():
            # shared.storage already displayed the quota/cooldown explanation.
            # Do not misreport an active cooldown as missing credentials.
            return False
        try:
            worksheet = cfb_builder.get_or_create_worksheet(tab_name, columns)
            if worksheet is None:
                if _shared_storage_cooling_down():
                    return False
                st.warning(
                    "Google Sheets is not configured. Add GOOGLE_CREDENTIALS and the sport database setting."
                )
                return False

            out = dataframe.copy() if dataframe is not None else pd.DataFrame(columns=columns)
            for column in columns:
                if column not in out.columns:
                    out[column] = ""
            out = out[columns].fillna("").astype(str)
            values = [columns] + out.values.tolist()

            # Read only the used-row count so a rare shrinking snapshot can remove
            # stale rows without paying for clear()+update() on every normal save.
            previous_rows = 0
            try:
                previous_rows = len(_retry(lambda: worksheet.col_values(1)))
            except Exception as exc:
                if _quota_error(exc):
                    _start_shared_storage_cooldown()
                    return False
                previous_rows = 0

            _retry(lambda: worksheet.update(values))

            new_rows = len(values)
            if previous_rows > new_rows:
                try:
                    _retry(lambda: worksheet.batch_clear([f"A{new_rows + 1}:ZZ{previous_rows}"]))
                except Exception as exc:
                    if _quota_error(exc):
                        _start_shared_storage_cooldown()
                    # Stale trailing rows are preferable to turning a successful
                    # model save into a visible error during a quota burst.
                    pass
            return True
        except Exception as exc:
            if _quota_error(exc):
                _start_shared_storage_cooldown()
                return False
            st.error(f"Could not write Google Sheets tab '{tab_name}': {exc}")
            return False

    cfb_builder.write_sheet = _guarded_write_sheet

    # ------------------------------------------------------------------
    # 2) Save-path type safety and MLB-matching button copy.
    # ------------------------------------------------------------------
    # Google Sheets snapshots are string-backed, so rating rows loaded from the
    # workbook can carry season weights such as "1.0". The original slate_row()
    # averaged those values before coercing them, which produced a str/int
    # TypeError only when Save was pressed. Coerce the two persistence fields at
    # the boundary while leaving the live projection object otherwise unchanged.
    original_slate_row = cfb_builder.slate_row

    def _safe_slate_row(result: dict[str, Any]) -> pd.DataFrame:
        safe_result = copy.copy(result)
        projection = copy.copy(result.get("projection", {}))
        for side in ("away", "home"):
            rating = projection.get(side)
            if not isinstance(rating, dict):
                continue
            safe_rating = copy.copy(rating)
            safe_rating["Previous Season Weight"] = cfb_builder._num(
                safe_rating.get("Previous Season Weight"), 1.0
            )
            safe_rating["Current Season Weight"] = cfb_builder._num(
                safe_rating.get("Current Season Weight"), 0.0
            )
            projection[side] = safe_rating
        safe_result["projection"] = projection

        # The market-calibration layer appends additional slate columns after this
        # guard is installed. Missing extra columns are initially created as NaN,
        # which makes pandas infer float64. Newer pandas versions reject assigning
        # a bool (ATS Calibration Proven=False) into that float64 slot. Return an
        # object-backed one-row persistence frame so the later market layer can
        # safely add booleans, ints, floats, and text before Sheets stringifies it.
        return original_slate_row(safe_result).astype(object)

    cfb_builder.slate_row = _safe_slate_row

    # Keep the CFB builder wording consistent with the MLB builder without
    # replacing Streamlit's global button function. The label is a direct string
    # constant in _render_build(), so changing that one function's code constant
    # is isolated to this page and survives normal Streamlit reruns.
    try:
        render_build = cfb_builder._render_build
        code = render_build.__code__
        old_label = "Save projection and graded plays"
        new_label = "Save matchup summary"
        constants = tuple(new_label if value == old_label else value for value in code.co_consts)
        if constants != code.co_consts:
            render_build.__code__ = code.replace(co_consts=constants)
    except Exception:
        pass

    # ------------------------------------------------------------------
    # 3) Ratings: do not rebuild giant advanced data during +/- interactions.
    # ------------------------------------------------------------------
    # Once a valid week snapshot has been saved, it is the interactive builder's
    # source for that week. A newly downloaded parquet file no longer makes that
    # snapshot "stale" in the middle of a user session. A new week still has no
    # saved snapshot and therefore builds normally once.
    def _ensure_automatic_ratings(season: int, week: int, force: bool = False) -> pd.DataFrame:
        session_key = f"cfb_auto_ratings_{season}_{week}"
        cached = st.session_state.get(session_key)
        if isinstance(cached, pd.DataFrame) and not cached.empty and not force:
            return cached.copy()

        saved = cfb_builder._get_cached_ratings(season, week)
        if isinstance(saved, pd.DataFrame) and not saved.empty:
            st.session_state[session_key] = saved.copy()
            return saved.copy()

        try:
            ratings = cfb_builder.build_team_ratings(season, week)
            if isinstance(ratings, pd.DataFrame) and not ratings.empty:
                st.session_state[session_key] = ratings.copy()
                st.session_state.pop("cfb_auto_ratings_warning", None)
                return ratings.copy()
        except Exception as exc:
            st.session_state["cfb_auto_ratings_warning"] = str(exc)

        return pd.DataFrame(columns=cfb_builder.RATING_COLUMNS)

    cfb_builder._ensure_automatic_ratings = _ensure_automatic_ratings

    # ------------------------------------------------------------------
    # 4) Lower-memory parquet conversion for the occasional weekly rebuild.
    # ------------------------------------------------------------------
    # The previous `pd.DataFrame(polars_frame.to_dicts())` materialized a Python
    # dictionary for every play while the Polars frame was still resident. With
    # CFB play-by-play that temporary duplication can be hundreds of MB. Building
    # pandas columns directly from Polars arrays avoids that row-dictionary copy.
    def _read_open_parquet_low_memory(path: Any, requested_aliases: dict[str, tuple[str, ...]]) -> pd.DataFrame:
        if path is None or not path.exists():
            return pd.DataFrame()
        try:
            import polars as pl

            scan = pl.scan_parquet(str(path))
            names = set(scan.collect_schema().names())
            selected: list[str] = []
            rename: dict[str, str] = {}
            for canonical, aliases in requested_aliases.items():
                found = next((alias for alias in aliases if alias in names), None)
                if found:
                    selected.append(found)
                    rename[found] = canonical
            if not selected:
                return pd.DataFrame()

            lazy = scan.select(selected)
            try:
                frame = lazy.collect(engine="streaming")
            except TypeError:
                frame = lazy.collect(streaming=True)
            frame = frame.rename(rename)

            data: dict[str, Any] = {}
            for column in frame.columns:
                series = frame[column]
                try:
                    data[column] = series.to_numpy()
                except Exception:
                    data[column] = series.to_list()
            return pd.DataFrame(data, copy=False)
        except Exception:
            return pd.DataFrame()

    cfb_builder._read_open_parquet = _read_open_parquet_low_memory

    # ------------------------------------------------------------------
    # 5) Slate dates and kickoff times are always Eastern Time.
    # ------------------------------------------------------------------
    # ESPN/SportsDataverse commonly return UTC timestamps. The core builder used
    # dt.date() before timezone conversion, so late-night Eastern kickoffs could
    # land on the following day's slate. Normalize both newly parsed schedules
    # and cached schedule frames to America/New_York before any slate filtering.
    eastern_tz = ZoneInfo("America/New_York")

    def _normalize_schedule_eastern(frame: Any) -> Any:
        if frame is None or getattr(frame, "empty", True):
            return frame
        output = frame.copy()
        if "Game Time" not in output.columns:
            return output

        parsed = pd.to_datetime(output["Game Time"], errors="coerce", utc=True)
        valid = parsed.notna()
        if not bool(valid.any()):
            return output

        eastern_times = parsed.loc[valid].dt.tz_convert(eastern_tz)
        output.loc[valid, "Game Time"] = eastern_times.map(lambda value: value.isoformat())
        output.loc[valid, "Game Date"] = eastern_times.dt.date.map(lambda value: value.isoformat())
        return output

    original_parse_games = cfb_builder._parse_games

    def _parse_games_eastern(payload: list[dict[str, Any]], season: int) -> pd.DataFrame:
        return _normalize_schedule_eastern(original_parse_games(payload, season))

    cfb_builder._parse_games = _parse_games_eastern

    original_ensure_automatic_schedule = cfb_builder._ensure_automatic_schedule

    def _ensure_automatic_schedule_eastern(*args, **kwargs):
        return _normalize_schedule_eastern(original_ensure_automatic_schedule(*args, **kwargs))

    cfb_builder._ensure_automatic_schedule = _ensure_automatic_schedule_eastern

    def _schedule_date_series_eastern(schedule: pd.DataFrame) -> pd.Series:
        if schedule is None or schedule.empty:
            return pd.Series(dtype="object")
        normalized = _normalize_schedule_eastern(schedule)
        return pd.to_datetime(
            normalized.get("Game Date", pd.Series(index=normalized.index, dtype=str)),
            errors="coerce",
        ).dt.date

    cfb_builder._schedule_date_series = _schedule_date_series_eastern

    def _eastern_today():
        return datetime.now(eastern_tz).date()

    def _current_cfb_season_eastern(today=None) -> int:
        today = today or _eastern_today()
        return today.year - 1 if today.month <= 2 else today.year

    cfb_builder._current_cfb_season = _current_cfb_season_eastern

    def _default_slate_date_eastern(schedule: pd.DataFrame, today=None):
        dates = cfb_builder._available_slate_dates(schedule)
        if not dates:
            return None
        today = today or _eastern_today()
        if today in dates:
            return today
        future = [game_date for game_date in dates if game_date >= today]
        return future[0] if future else dates[-1]

    cfb_builder._default_slate_date = _default_slate_date_eastern

    cfb_builder._EZPZ_CFB_RUNTIME_GUARD = True
