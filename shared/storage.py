from __future__ import annotations

from typing import Iterable

import pandas as pd
import streamlit as st

from shared.public_contract import (
    ALL_GAME_TRENDS_COLUMNS,
    ALL_GAME_TRENDS_TAB,
    ODDS_SNAPSHOT_COLUMNS,
    ODDS_SNAPSHOT_TAB,
    PUBLIC_SLATE_TAB,
    PUBLIC_SPLIT_COLUMNS,
    PUBLIC_SPLIT_TAB,
    PUBLIC_TRACKER_TAB,
)
from shared.turso_storage import (
    append_dataset_rows,
    dataset_exists,
    is_turso_ready,
    read_dataset,
    replace_dataset,
)


_ACTIVE_SPORT = ""
_TURSO_SPORT = {
    "NFL": "NFL",
    "CFB": "NCAAF",
    "NCAAF": "NCAAF",
    "CBB": "NCAAM",
    "NCAAM": "NCAAM",
}

_BOOTSTRAP_PUBLIC_TABS = {
    PUBLIC_SLATE_TAB: ["Date", "Game", "Away Team", "Home Team"],
    PUBLIC_TRACKER_TAB: ["Date", "Game", "Bet Type", "Selection", "Odds/Line", "Result"],
    ALL_GAME_TRENDS_TAB: ALL_GAME_TRENDS_COLUMNS,
    PUBLIC_SPLIT_TAB: PUBLIC_SPLIT_COLUMNS,
    ODDS_SNAPSHOT_TAB: ODDS_SNAPSHOT_COLUMNS,
}

# Once a Render process has verified a sport's permanent datasets, Streamlit
# reruns should not repeat those existence checks on every widget change.
_INITIALIZED_DATASET_SPORTS: set[str] = set()

# Builders historically emulated spreadsheets by constructing an entire DataFrame
# and calling write_sheet. Some of those helpers remove an existing keyed row and
# append its replacement to the bottom. A positional database diff interprets that
# harmless reorder as hundreds/thousands of changed rows. These identities let the
# compatibility layer preserve each existing record's storage position while still
# appending genuinely new records.
_IDENTITY_CANDIDATES: tuple[tuple[str, ...], ...] = (
    ("Candidate ID",),
    ("Season", "Projection Week", "Team"),
    ("Observed Date", "Season", "Week", "Team", "Unit", "Position", "Player"),
    ("Observed Date", "Team", "Player", "Status"),
    ("Season", "Week", "Game ID", "Team"),
    ("Season", "Game ID", "Team"),
    ("Date", "Game ID", "Bet Type", "Selection"),
    ("Date", "Game ID", "Market", "Selection"),
    ("Date", "Game Key", "Market", "Selection"),
    ("Snapshot Time ET", "Date", "Game", "Market", "Selection"),
    ("Date", "Game", "Bet Type", "Selection"),
    ("Date", "Game", "Market", "Selection"),
    ("Season", "Game ID"),
    ("Date", "Game ID"),
    ("Team", "Season", "Week"),
    ("Team", "Season"),
    ("Date", "Pitcher", "Bet Type", "Selection"),
    ("Date", "Player", "Bet Type", "Selection"),
)


def _canonical_sport(sport: str | None) -> str:
    value = str(sport or "").strip().upper()
    if value == "NCAAF":
        return "CFB"
    if value == "NCAAM":
        return "CBB"
    return value


def _dataset_sport(sport: str | None = None) -> str:
    selected = _canonical_sport(sport if sport is not None else _ACTIVE_SPORT)
    return _TURSO_SPORT.get(selected, selected)


def _require_turso() -> None:
    if not is_turso_ready():
        raise RuntimeError(
            "Turso is not configured or reachable. Google Sheets is no longer a production fallback."
        )


def set_storage_sport(sport: str | None) -> str:
    global _ACTIVE_SPORT
    _ACTIVE_SPORT = _canonical_sport(sport)
    return _ACTIVE_SPORT


def get_storage_sport() -> str:
    return _ACTIVE_SPORT


def storage_database_config(sport: str | None = None) -> dict[str, str]:
    selected = _canonical_sport(sport if sport is not None else _ACTIVE_SPORT)
    dataset_sport = _dataset_sport(selected)
    return {
        "sport": selected,
        "dataset_sport": dataset_sport,
        "sheet_id": f"turso:{dataset_sport}" if dataset_sport else "",
        "sheet_name": f"Turso {dataset_sport}" if dataset_sport else "Turso",
        "namespace": "",
    }


def storage_database_name(sport: str | None = None) -> str:
    return storage_database_config(sport)["sheet_name"]


def storage_database_identity(sport: str | None = None) -> str:
    dataset_sport = _dataset_sport(sport)
    return f"turso:{dataset_sport}" if dataset_sport else "turso"


def _normalize_frame(dataframe: pd.DataFrame | None, columns: list[str]) -> pd.DataFrame:
    out = dataframe.copy() if dataframe is not None else pd.DataFrame(columns=columns)
    for column in columns:
        if column not in out.columns:
            out[column] = ""
    if columns:
        out = out[columns]
    return out.fillna("").astype(str)


def _identity_key(row: pd.Series, identity_columns: tuple[str, ...]) -> tuple[str, ...] | None:
    values = tuple(str(row.get(column, "") or "").strip() for column in identity_columns)
    if not values or any(not value for value in values):
        return None
    return values


def _identity_columns(existing: pd.DataFrame, incoming: pd.DataFrame) -> tuple[str, ...] | None:
    if existing is None or incoming is None or existing.empty or incoming.empty:
        return None
    existing_columns = set(existing.columns)
    incoming_columns = set(incoming.columns)
    for candidate in _IDENTITY_CANDIDATES:
        if not set(candidate).issubset(existing_columns) or not set(candidate).issubset(incoming_columns):
            continue
        existing_keys = [key for _, row in existing.iterrows() if (key := _identity_key(row, candidate)) is not None]
        incoming_keys = [key for _, row in incoming.iterrows() if (key := _identity_key(row, candidate)) is not None]
        if not incoming_keys:
            continue
        # Only use a key when it is genuinely unique. Ambiguous keys fall back to
        # the original incoming order rather than risking the wrong record match.
        if len(existing_keys) != len(set(existing_keys)) or len(incoming_keys) != len(set(incoming_keys)):
            continue
        return candidate
    return None


def _preserve_existing_order(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    identity_columns = _identity_columns(existing, incoming)
    if identity_columns is None:
        return incoming

    incoming_by_key: dict[tuple[str, ...], int] = {}
    for index, row in incoming.iterrows():
        key = _identity_key(row, identity_columns)
        if key is not None:
            incoming_by_key[key] = int(index)

    ordered_indices: list[int] = []
    used: set[int] = set()
    for _, row in existing.iterrows():
        key = _identity_key(row, identity_columns)
        index = incoming_by_key.get(key) if key is not None else None
        if index is None or index in used:
            continue
        ordered_indices.append(index)
        used.add(index)

    for index in incoming.index:
        numeric_index = int(index)
        if numeric_index not in used:
            ordered_indices.append(numeric_index)

    if len(ordered_indices) != len(incoming):
        return incoming
    return incoming.loc[ordered_indices].reset_index(drop=True)


def _ensure_dataset(tab_name: str, columns: list[str], sport: str | None = None) -> None:
    _require_turso()
    dataset_sport = _dataset_sport(sport)
    if not dataset_sport:
        raise RuntimeError("No active sport is selected for Turso storage.")
    if not dataset_exists(dataset_sport, tab_name):
        replace_dataset(dataset_sport, tab_name, pd.DataFrame(columns=columns), columns)


def initialize_sport_workbooks(sports: Iterable[str] = ("NFL", "CFB")) -> dict[str, str]:
    """Initialize the permanent Turso datasets used by each sport.

    Existence is checked through the one-row manifest instead of loading each
    complete dataset. A verified sport is remembered for the lifetime of the
    Render process so Streamlit widget reruns do not repeat bootstrap I/O.
    """
    if not is_turso_ready():
        return {}
    initialized: dict[str, str] = {}
    for sport in sports:
        canonical = _canonical_sport(sport)
        dataset_sport = _dataset_sport(canonical)
        if dataset_sport not in {"NFL", "NCAAF", "NCAAM"}:
            continue

        if dataset_sport not in _INITIALIZED_DATASET_SPORTS:
            for tab_name, columns in _BOOTSTRAP_PUBLIC_TABS.items():
                if not dataset_exists(dataset_sport, tab_name):
                    replace_dataset(
                        dataset_sport,
                        tab_name,
                        pd.DataFrame(columns=list(columns)),
                        list(columns),
                    )
            _INITIALIZED_DATASET_SPORTS.add(dataset_sport)

        initialized[canonical] = f"turso:{dataset_sport}"
    return initialized


def connect_to_sheets():
    """Removed storage backend compatibility hook.

    Returning None is intentional: production persistence is Turso-only.
    New code must use read_sheet/write_sheet rather than a worksheet object.
    """
    return None


def sheets_ready() -> bool:
    """Historical function name retained for builder compatibility."""
    return is_turso_ready()


def get_or_create_worksheet(tab_name: str, columns: Iterable[str]):
    """Historical bootstrap hook retained for builder compatibility.

    It ensures the Turso dataset exists and deliberately returns None so no new
    code can depend on a Google/gspread worksheet object.
    """
    columns = list(columns)
    try:
        _ensure_dataset(tab_name, columns)
    except Exception as exc:
        st.error(f"Could not initialize Turso dataset '{tab_name}': {exc}")
    return None


def read_sheet(tab_name: str, columns: Iterable[str]) -> pd.DataFrame:
    columns = list(columns)
    dataset_sport = _dataset_sport()
    if not dataset_sport:
        return pd.DataFrame(columns=columns)
    try:
        _require_turso()
        dataframe = read_dataset(dataset_sport, tab_name, columns)
        return _normalize_frame(dataframe, columns)
    except Exception as exc:
        st.error(f"Could not read Turso dataset '{dataset_sport}/{tab_name}': {exc}")
        return pd.DataFrame(columns=columns)


def write_sheet(tab_name: str, dataframe: pd.DataFrame, columns: Iterable[str]) -> bool:
    columns = list(columns)
    dataset_sport = _dataset_sport()
    if not dataset_sport:
        st.error("No active sport is selected for Turso storage.")
        return False
    try:
        _require_turso()
        out = _normalize_frame(dataframe, columns)

        # Preserve stable keyed rows at their existing indexes. read_dataset uses
        # the short builder cache, so on normal Streamlit reruns this no longer
        # causes another network read before the differential write.
        if len(out) > 1 and any(set(candidate).issubset(columns) for candidate in _IDENTITY_CANDIDATES):
            existing = _normalize_frame(read_dataset(dataset_sport, tab_name, columns), columns)
            if not existing.empty:
                out = _preserve_existing_order(existing, out)

        replace_dataset(dataset_sport, tab_name, out, columns)
        return True
    except Exception as exc:
        st.error(f"Could not write Turso dataset '{dataset_sport}/{tab_name}': {exc}")
        return False


def append_row(tab_name: str, row: dict, columns: Iterable[str]) -> bool:
    columns = list(columns)
    dataset_sport = _dataset_sport()
    if not dataset_sport:
        st.error("No active sport is selected for Turso storage.")
        return False
    try:
        _require_turso()
        payload = {column: row.get(column, "") for column in columns}
        append_dataset_rows(dataset_sport, tab_name, [payload], columns)
        return True
    except Exception as exc:
        st.error(f"Could not append Turso dataset '{dataset_sport}/{tab_name}': {exc}")
        return False


class sport_storage:
    """Context manager for temporarily selecting a sport storage namespace."""

    def __init__(self, sport: str):
        self.sport = sport
        self.previous = ""

    def __enter__(self):
        self.previous = get_storage_sport()
        set_storage_sport(self.sport)
        return self

    def __exit__(self, exc_type, exc, tb):
        set_storage_sport(self.previous)
        return False
