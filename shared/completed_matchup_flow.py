"""Cross-sport builder completion flow.

MLB already removes a matchup from its builder after the matchup summary is
saved. This module gives the same behavior to CFB, NFL, and CBB without treating
background/automatic slate rows as manually completed games.

A tiny ``builder_completed`` worksheet records only explicit successful builder
saves. The game/team selectors then hide those saved matchups, and a save
confirmation triggers one Streamlit rerun so the next unsaved matchup appears
immediately.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import re
from typing import Any, Iterable

import pandas as pd
import streamlit as st


COMPLETION_TAB = "builder_completed"
COMPLETION_COLUMNS = [
    "Sport", "Date", "Away Team", "Home Team", "Game Label", "Saved At",
]
_SUPPORTED_SPORTS = {"CFB", "NFL", "CBB"}
_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")

# CFB's automatic Slate population also writes daily_slate, so daily_slate by
# itself cannot tell us whether the user actually finished a matchup in Build.
# A manual CFB save also writes both personnel snapshots, while the automatic
# batch explicitly runs with save_personnel_snapshots=False. Joining those two
# tabs gives us a safe backfill for matchups saved before builder_completed was
# introduced or when its tiny marker write was lost to a transient Sheets quota.
_CFB_SLATE_TAB = "daily_slate"
_CFB_SLATE_COLUMNS = ["Date", "Game ID", "Away Team", "Home Team"]
_CFB_PERSONNEL_TAB = "personnel_snapshots"
_CFB_PERSONNEL_COLUMNS = ["Game ID", "Team"]


def _sport() -> str:
    value = str(st.session_state.get("selected_sport", "") or "").strip().upper()
    if value == "NCAAF":
        return "CFB"
    if value == "NCAAM":
        return "CBB"
    return value


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _pair_key(away: Any, home: Any) -> str:
    return f"{_norm(away)}|{_norm(home)}"


def _date_from_key(key: Any, fallback: str | None = None) -> str:
    match = _DATE_RE.search(str(key or ""))
    if match:
        return match.group(1)
    return str(fallback or date.today().isoformat())


def _parse_schedule_option(sport: str, option: Any) -> tuple[str, str] | None:
    prefix = str(option or "").split(" — ", 1)[0].strip()
    if sport == "CFB" and " @ " in prefix:
        away, home = prefix.split(" @ ", 1)
        return away.strip(), home.strip()
    if sport == "NFL" and " at " in prefix:
        away, home = prefix.split(" at ", 1)
        return away.strip(), home.strip()
    return None


def _cache_key(sport: str) -> str:
    return f"_ezpz_builder_completed_rows::{sport}"


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=COMPLETION_COLUMNS)


def _completion_frame(sport: str) -> pd.DataFrame:
    key = _cache_key(sport)
    cached = st.session_state.get(key)
    if isinstance(cached, pd.DataFrame):
        return cached.copy()

    frame = _empty_frame()
    try:
        # Import lazily so this module can be installed before app_mobile_admin
        # sets the active sport workbook for the current page.
        from shared.storage import get_storage_sport, read_sheet

        active_storage = str(get_storage_sport() or "").strip().upper()
        if active_storage == sport:
            loaded = read_sheet(COMPLETION_TAB, COMPLETION_COLUMNS)
            if isinstance(loaded, pd.DataFrame):
                frame = loaded.copy()
    except Exception:
        frame = _empty_frame()

    for column in COMPLETION_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    frame = frame[COMPLETION_COLUMNS].fillna("")
    st.session_state[key] = frame.copy()
    return frame


def _cfb_manual_pairs(date_text: str) -> set[str]:
    """Recover explicit CFB Build saves without mistaking auto-slate rows for saves."""
    cache_key = f"_ezpz_cfb_manual_save_pairs::{date_text}"
    cached = st.session_state.get(cache_key)
    if isinstance(cached, set):
        return set(cached)
    if isinstance(cached, (list, tuple)):
        return set(str(value) for value in cached)

    try:
        from shared import storage

        if str(storage.get_storage_sport() or "").strip().upper() != "CFB":
            return set()
        cooldown = getattr(storage, "_quota_cooldown_active", None)
        if callable(cooldown) and cooldown():
            # Do not cache an empty fallback while Google is actively throttling;
            # the next rerun after the cooldown should get another chance.
            return set()

        slate = storage.read_sheet(_CFB_SLATE_TAB, _CFB_SLATE_COLUMNS)
        personnel = storage.read_sheet(_CFB_PERSONNEL_TAB, _CFB_PERSONNEL_COLUMNS)
        if callable(cooldown) and cooldown():
            return set()
        if slate.empty or personnel.empty:
            st.session_state[cache_key] = set()
            return set()

        manual_ids = {
            str(value or "").strip()
            for value in personnel.get("Game ID", pd.Series(dtype=str)).tolist()
            if str(value or "").strip()
        }
        if not manual_ids:
            st.session_state[cache_key] = set()
            return set()

        view = slate[
            slate.get("Date", pd.Series(index=slate.index, dtype=str)).astype(str).str.strip().eq(str(date_text))
            & slate.get("Game ID", pd.Series(index=slate.index, dtype=str)).astype(str).str.strip().isin(manual_ids)
        ]
        pairs = {
            _pair_key(row.get("Away Team", ""), row.get("Home Team", ""))
            for _, row in view.iterrows()
            if _norm(row.get("Away Team", "")) and _norm(row.get("Home Team", ""))
        }
        st.session_state[cache_key] = set(pairs)
        return pairs
    except Exception:
        return set()


def _completed_pairs(sport: str, date_text: str) -> set[str]:
    frame = _completion_frame(sport)
    pairs: set[str] = set()
    if not frame.empty:
        sport_mask = frame["Sport"].astype(str).str.upper().eq(sport)
        date_mask = frame["Date"].astype(str).str.strip().eq(str(date_text))
        view = frame[sport_mask & date_mask]
        pairs = {
            _pair_key(row.get("Away Team", ""), row.get("Home Team", ""))
            for _, row in view.iterrows()
            if _norm(row.get("Away Team", "")) and _norm(row.get("Home Team", ""))
        }

    if sport == "CFB":
        pairs.update(_cfb_manual_pairs(date_text))
    return pairs


def _mark_completed(active: dict[str, Any]) -> bool:
    sport = str(active.get("sport", "") or "").upper()
    if sport not in _SUPPORTED_SPORTS:
        return False
    date_text = str(active.get("date", "") or date.today().isoformat())
    away = str(active.get("away", "") or "").strip()
    home = str(active.get("home", "") or "").strip()
    if not away or not home:
        return False

    frame = _completion_frame(sport)
    pair = _pair_key(away, home)
    if not frame.empty:
        same = (
            frame["Sport"].astype(str).str.upper().eq(sport)
            & frame["Date"].astype(str).str.strip().eq(date_text)
            & frame.apply(
                lambda row: _pair_key(row.get("Away Team", ""), row.get("Home Team", "")) == pair,
                axis=1,
            )
        )
        frame = frame.loc[~same].copy()

    row = {
        "Sport": sport,
        "Date": date_text,
        "Away Team": away,
        "Home Team": home,
        "Game Label": str(active.get("label", "") or f"{away} at {home}"),
        "Saved At": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    output = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
    output = output[COMPLETION_COLUMNS].fillna("").astype(str)

    # Update the in-session state first so the matchup disappears even if Google
    # has a transient write problem. A successful write makes the behavior persist
    # across browser refreshes and new Streamlit sessions.
    st.session_state[_cache_key(sport)] = output.copy()
    if sport == "CFB":
        cfb_pairs_key = f"_ezpz_cfb_manual_save_pairs::{date_text}"
        recovered = st.session_state.get(cfb_pairs_key)
        recovered_pairs = set(recovered) if isinstance(recovered, (set, list, tuple)) else set()
        recovered_pairs.add(pair)
        st.session_state[cfb_pairs_key] = recovered_pairs
    try:
        from shared.storage import get_storage_sport, write_sheet

        active_storage = str(get_storage_sport() or "").strip().upper()
        if active_storage != sport:
            return False
        return bool(write_sheet(COMPLETION_TAB, output, COMPLETION_COLUMNS))
    except Exception:
        return False


def _set_active(sport: str, date_text: str, away: str, home: str, label: str) -> None:
    st.session_state[f"_ezpz_active_matchup::{sport}"] = {
        "sport": sport,
        "date": str(date_text),
        "away": str(away),
        "home": str(home),
        "label": str(label),
    }


def _success_is_primary_save(sport: str, message: str) -> bool:
    text = str(message or "")
    if sport == "CFB":
        return text.startswith("Saved the projection and") and "graded play" in text
    if sport == "NFL":
        return text.startswith("Saved the game and") and "Tracker additions:" in text
    if sport == "CBB":
        return "College basketball projection saved." in text
    return False


def _clear_invalid_widget_value(key: Any, options: list[Any]) -> None:
    if not key:
        return
    key = str(key)
    try:
        current = st.session_state.get(key)
        if current is not None and current not in options:
            st.session_state.pop(key, None)
    except Exception:
        pass


def install_completed_matchup_flow() -> None:
    """Patch only the builder selectors/save confirmations that need this UX."""
    if getattr(st, "_ezpz_completed_matchup_flow_installed", False):
        return

    original_selectbox = st.selectbox
    original_success = st.success

    def _show_flash() -> None:
        message = st.session_state.pop("_ezpz_completed_matchup_flash", None)
        if message:
            original_success(str(message))

    def selectbox(label: str, options: Iterable[Any], *args: Any, **kwargs: Any):
        sport = _sport()
        key = kwargs.get("key")
        option_list = list(options) if sport in _SUPPORTED_SPORTS else options

        # CFB scheduled game selector.
        if sport == "CFB" and str(key or "").startswith("cfb_game_") and str(label) == "Game":
            _show_flash()
            date_text = _date_from_key(key)
            completed = _completed_pairs(sport, date_text)
            filtered: list[Any] = []
            for option in option_list:
                parsed = _parse_schedule_option(sport, option)
                if parsed is None or _pair_key(*parsed) not in completed:
                    filtered.append(option)
            if not filtered:
                original_success("All CFB matchups on this slate have been saved.")
                st.stop()
            _clear_invalid_widget_value(key, filtered)
            selected = original_selectbox(label, filtered, *args, **kwargs)
            parsed = _parse_schedule_option(sport, selected)
            if parsed:
                _set_active(sport, date_text, parsed[0], parsed[1], str(selected))
            return selected

        # NFL scheduled game selector.
        if sport == "NFL" and str(key or "").startswith("nfl_scheduled_game_") and str(label) == "Game":
            _show_flash()
            date_text = _date_from_key(key)
            completed = _completed_pairs(sport, date_text)
            filtered = []
            for option in option_list:
                parsed = _parse_schedule_option(sport, option)
                if parsed is None or _pair_key(*parsed) not in completed:
                    filtered.append(option)
            if not filtered:
                original_success("All NFL matchups on this slate have been saved.")
                st.stop()
            _clear_invalid_widget_value(key, filtered)
            selected = original_selectbox(label, filtered, *args, **kwargs)
            parsed = _parse_schedule_option(sport, selected)
            if parsed:
                _set_active(sport, date_text, parsed[0], parsed[1], str(selected))
            return selected

        # CBB currently uses away/home team selectors rather than an automatic
        # scheduled-game dropdown. Hide away teams that have no unsaved opponent
        # left today, then hide already-saved opponents for the selected away team.
        if sport == "CBB" and str(key or "") == "cbb_away":
            _show_flash()
            date_text = date.today().isoformat()
            completed = _completed_pairs(sport, date_text)
            teams = [str(value) for value in option_list]
            filtered = [
                away for away in teams
                if any(home != away and _pair_key(away, home) not in completed for home in teams)
            ]
            if not filtered:
                original_success("All available CBB matchups have been saved for today.")
                st.stop()
            _clear_invalid_widget_value(key, filtered)
            selected = original_selectbox(label, filtered, *args, **kwargs)
            st.session_state["_ezpz_cbb_selected_away"] = str(selected)
            return selected

        if sport == "CBB" and str(key or "") == "cbb_home":
            date_text = date.today().isoformat()
            away = str(st.session_state.get("_ezpz_cbb_selected_away", "") or "")
            completed = _completed_pairs(sport, date_text)
            filtered = [
                option for option in option_list
                if not away or _pair_key(away, option) not in completed
            ]
            if not filtered:
                original_success(f"All remaining opponents for {away or 'this team'} have been saved today.")
                st.stop()
            _clear_invalid_widget_value(key, filtered)
            selected = original_selectbox(label, filtered, *args, **kwargs)
            if away:
                _set_active(sport, date_text, away, str(selected), f"{away} at {selected}")
            return selected

        return original_selectbox(label, options, *args, **kwargs)

    def success(body: Any, *args: Any, **kwargs: Any):
        rendered = original_success(body, *args, **kwargs)
        sport = _sport()
        if sport not in _SUPPORTED_SPORTS or not _success_is_primary_save(sport, str(body)):
            return rendered

        active = st.session_state.get(f"_ezpz_active_matchup::{sport}")
        if not isinstance(active, dict):
            return rendered

        persisted = _mark_completed(active)
        message = str(body)
        if not persisted:
            message += " The matchup was removed for this session, but the completion marker could not be persisted."
        st.session_state["_ezpz_completed_matchup_flash"] = message
        st.rerun()
        return rendered

    st.selectbox = selectbox
    st.success = success
    st._ezpz_completed_matchup_flow_installed = True
