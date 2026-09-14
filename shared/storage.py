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
from shared.turso_storage import is_turso_ready, read_dataset, replace_dataset


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


def _ensure_dataset(tab_name: str, columns: list[str], sport: str | None = None) -> None:
    _require_turso()
    dataset_sport = _dataset_sport(sport)
    if not dataset_sport:
        raise RuntimeError("No active sport is selected for Turso storage.")
    current = read_dataset(dataset_sport, tab_name, columns)
    if current is None:
        replace_dataset(dataset_sport, tab_name, pd.DataFrame(columns=columns), columns)


def initialize_sport_workbooks(sports: Iterable[str] = ("NFL", "CFB")) -> dict[str, str]:
    """Initialize the permanent Turso datasets used by each sport.

    The legacy function name is kept so existing builder imports remain stable.
    It no longer creates or touches Google workbooks.
    """
    if not is_turso_ready():
        return {}
    initialized: dict[str, str] = {}
    for sport in sports:
        canonical = _canonical_sport(sport)
        dataset_sport = _dataset_sport(canonical)
        if dataset_sport not in {"NFL", "NCAAF", "NCAAM"}:
            continue
        for tab_name, columns in _BOOTSTRAP_PUBLIC_TABS.items():
            current = read_dataset(dataset_sport, tab_name, list(columns))
            if current is None:
                replace_dataset(
                    dataset_sport,
                    tab_name,
                    pd.DataFrame(columns=list(columns)),
                    list(columns),
                )
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
        if dataframe is None:
            replace_dataset(dataset_sport, tab_name, pd.DataFrame(columns=columns), columns)
            return pd.DataFrame(columns=columns)
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
        replace_dataset(dataset_sport, tab_name, out, columns)
        return True
    except Exception as exc:
        st.error(f"Could not write Turso dataset '{dataset_sport}/{tab_name}': {exc}")
        return False


def append_row(tab_name: str, row: dict, columns: Iterable[str]) -> bool:
    columns = list(columns)
    dataframe = read_sheet(tab_name, columns)
    payload = {column: row.get(column, "") for column in columns}
    dataframe = pd.concat([dataframe, pd.DataFrame([payload])], ignore_index=True)
    return write_sheet(tab_name, dataframe, columns)


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
