"""Persistent recovery for the MLB mobile Matchup Builder.

Streamlit keeps widget state in the active browser/server session. If Android
recreates the session, Render replaces the process, or the websocket drops while
a matchup is being worked on, the default behavior is to return to the default
admin page and default builder inputs.

This module mirrors only the small amount of *user-entered builder state* into
query parameters. The URL therefore becomes a lightweight client-side
checkpoint that survives a fresh Streamlit session without creating more Google
Sheets reads/writes. Expensive model/source calls keep using their normal caches
and simply resume for the same matchup.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

import streamlit as st


_SECTION_PARAM = "ezpz_section"
_BUILD_DATE_PARAM = "ezpz_build_date"
_BUILD_GAME_PARAM = "ezpz_build_game"
_WIDGET_PARAM_PREFIX = "ezpz_bw_"

# Builder controls are all game-scoped. Keeping the prefixes explicit prevents
# unrelated admin widgets (and password fields) from ever being mirrored.
_BUILDER_KEY_PREFIXES = (
    "env_",
    "home_k_",
    "away_k_",
    "home_ml_",
    "away_ml_",
    "home_bulk_",
    "away_bulk_",
    "home_bullpen_",
    "away_bullpen_",
    "home_opener_",
    "away_opener_",
    "total_",
    "nrfi_",
    "yrfi_",
)


def _qp_text(name: str) -> str:
    try:
        value = st.query_params.get(name, "")
    except Exception:
        return ""
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "")


def _qp_set(name: str, value: Any) -> None:
    text = str(value if value is not None else "")
    try:
        if _qp_text(name) != text:
            st.query_params[name] = text
    except Exception:
        pass


def _qp_delete(name: str) -> None:
    try:
        if name in st.query_params:
            del st.query_params[name]
    except Exception:
        pass


def _builder_widget_param(key: str) -> str:
    digest = hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:12]
    return f"{_WIDGET_PARAM_PREFIX}{digest}"


def _is_builder_widget_key(key: Any) -> bool:
    text = str(key or "")
    return bool(text) and text.startswith(_BUILDER_KEY_PREFIXES)


def _clear_builder_widget_params() -> None:
    try:
        names = [str(name) for name in list(st.query_params.keys())]
    except Exception:
        return
    for name in names:
        if name.startswith(_WIDGET_PARAM_PREFIX):
            _qp_delete(name)


def _encode_widget_value(value: Any) -> str:
    try:
        return json.dumps(value, separators=(",", ":"))
    except Exception:
        return json.dumps(str(value), separators=(",", ":"))


def _decode_widget_value(raw: str, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        value = json.loads(raw)
    except Exception:
        return fallback

    try:
        if isinstance(fallback, bool):
            return bool(value)
        if isinstance(fallback, int) and not isinstance(fallback, bool):
            return int(round(float(value)))
        if isinstance(fallback, float):
            return float(value)
        if isinstance(fallback, str):
            return str(value)
    except Exception:
        return fallback
    return value


def _restore_kwarg_value(kwargs: dict, key: str, name: str = "value") -> None:
    """Restore a keyed widget's value only for a brand-new Streamlit session.

    Once Streamlit already has the widget key in session_state, its live frontend
    value must win. This avoids fighting normal user edits on every rerun.
    """
    if not _is_builder_widget_key(key):
        return
    try:
        if key in st.session_state:
            return
    except Exception:
        pass
    if name not in kwargs:
        return
    param = _builder_widget_param(key)
    raw = _qp_text(param)
    if raw:
        kwargs[name] = _decode_widget_value(raw, kwargs[name])


def _save_keyed_widget_value(key: Any, value: Any) -> None:
    if not _is_builder_widget_key(key):
        return
    _qp_set(_builder_widget_param(str(key)), _encode_widget_value(value))


def install_mlb_builder_resume() -> None:
    """Install small Streamlit wrappers that make MLB builds resume after reloads."""
    if getattr(st, "_ezpz_mlb_builder_resume_installed", False):
        return

    original_radio = st.radio
    original_date_input = st.date_input
    original_selectbox = st.selectbox
    original_number_input = st.number_input
    original_checkbox = st.checkbox
    original_text_input = st.text_input

    def radio(label, options, *args, **kwargs):
        # Persist the MLB admin section so a replacement Streamlit session opens
        # directly back to Build instead of Home.
        if str(label) == "Admin section":
            option_list = list(options)
            saved = _qp_text(_SECTION_PARAM)
            if not saved and _qp_text(_BUILD_GAME_PARAM):
                saved = "Build"
            key = kwargs.get("key")
            try:
                has_live_state = bool(key) and key in st.session_state
            except Exception:
                has_live_state = False
            if not has_live_state and saved in option_list and len(args) == 0:
                kwargs["index"] = option_list.index(saved)
            result = original_radio(label, options, *args, **kwargs)
            _qp_set(_SECTION_PARAM, result)
            return result
        return original_radio(label, options, *args, **kwargs)

    def date_input(label, *args, **kwargs):
        key = str(kwargs.get("key", "") or "")
        if key == "manual_auto_slate_date":
            saved = _qp_text(_BUILD_DATE_PARAM)
            try:
                has_live_state = key in st.session_state
            except Exception:
                has_live_state = False
            if saved and not has_live_state and "value" in kwargs:
                try:
                    kwargs["value"] = date.fromisoformat(saved)
                except Exception:
                    pass
            result = original_date_input(label, *args, **kwargs)
            try:
                _qp_set(_BUILD_DATE_PARAM, result.isoformat())
            except Exception:
                _qp_set(_BUILD_DATE_PARAM, result)
            return result
        return original_date_input(label, *args, **kwargs)

    def selectbox(label, options, *args, **kwargs):
        option_list = list(options)
        key = str(kwargs.get("key", "") or "")

        if str(label) == "Choose Game":
            saved = _qp_text(_BUILD_GAME_PARAM)
            if saved and saved not in option_list:
                # Most commonly this means the recovered game was successfully
                # saved and therefore disappeared from the unsaved-game list.
                _qp_delete(_BUILD_GAME_PARAM)
                _clear_builder_widget_params()
                saved = ""

            try:
                has_live_state = bool(key) and key in st.session_state
            except Exception:
                has_live_state = False
            if saved in option_list and not has_live_state and len(args) == 0:
                kwargs["index"] = option_list.index(saved)

            result = original_selectbox(label, options, *args, **kwargs)
            result_text = str(result or "")
            if saved and result_text != saved:
                # The user intentionally moved to another matchup. Do not carry
                # old K lines/weather/bulk inputs into the new game.
                _clear_builder_widget_params()
            _qp_set(_BUILD_GAME_PARAM, result_text)

            if (
                saved
                and result_text == saved
                and not st.session_state.get("_ezpz_builder_resume_notice_shown", False)
            ):
                st.info(
                    "Recovered your in-progress matchup. The Build page, slate date, game, "
                    "and manual builder inputs were restored automatically."
                )
                st.session_state["_ezpz_builder_resume_notice_shown"] = True
            return result

        if _is_builder_widget_key(key):
            param = _builder_widget_param(key)
            saved_raw = _qp_text(param)
            try:
                has_live_state = key in st.session_state
            except Exception:
                has_live_state = False
            if saved_raw and not has_live_state and len(args) == 0:
                saved_value = _decode_widget_value(saved_raw, "")
                if saved_value in option_list:
                    kwargs["index"] = option_list.index(saved_value)
            result = original_selectbox(label, options, *args, **kwargs)
            _save_keyed_widget_value(key, result)
            return result

        return original_selectbox(label, options, *args, **kwargs)

    def number_input(label, *args, **kwargs):
        key = str(kwargs.get("key", "") or "")
        _restore_kwarg_value(kwargs, key, "value")
        result = original_number_input(label, *args, **kwargs)
        _save_keyed_widget_value(key, result)
        return result

    def checkbox(label, *args, **kwargs):
        key = str(kwargs.get("key", "") or "")
        _restore_kwarg_value(kwargs, key, "value")
        result = original_checkbox(label, *args, **kwargs)
        _save_keyed_widget_value(key, result)
        return result

    def text_input(label, *args, **kwargs):
        key = str(kwargs.get("key", "") or "")
        _restore_kwarg_value(kwargs, key, "value")
        result = original_text_input(label, *args, **kwargs)
        _save_keyed_widget_value(key, result)
        return result

    st.radio = radio
    st.date_input = date_input
    st.selectbox = selectbox
    st.number_input = number_input
    st.checkbox = checkbox
    st.text_input = text_input
    st._ezpz_mlb_builder_resume_installed = True
