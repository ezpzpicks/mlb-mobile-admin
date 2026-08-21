import json
import os
from typing import Iterable

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials


_ACTIVE_SPORT = ""
_DEFAULT_DATABASE_NAMES = {
    "NFL": "NFL Model Database",
    "CFB": "CFB Model Database",
    "NCAAF": "CFB Model Database",
    "CBB": "CBB Model Database",
    "NCAAM": "CBB Model Database",
}


def _secret_or_env(name: str) -> str:
    value = os.environ.get(name, "")
    if value:
        return value
    try:
        return str(st.secrets.get(name, "") or "")
    except Exception:
        return ""


def _canonical_sport(sport: str | None) -> str:
    value = str(sport or "").strip().upper()
    if value == "NCAAF":
        return "CFB"
    if value == "NCAAM":
        return "CBB"
    return value


def set_storage_sport(sport: str | None) -> str:
    """Select the workbook used by the shared non-MLB storage layer.

    MLB keeps its long-standing direct storage implementation and generic
    GOOGLE_SHEET_NAME contract. NFL/CFB/CBB use separate workbooks so each
    sport owns its own daily_slate, bet_tracker, trend history, and model data.
    """
    global _ACTIVE_SPORT
    _ACTIVE_SPORT = _canonical_sport(sport)
    return _ACTIVE_SPORT


def get_storage_sport() -> str:
    return _ACTIVE_SPORT


def _sport_setting(sport: str, suffix: str) -> str:
    sport = _canonical_sport(sport)
    names: list[str] = []
    if sport == "CFB":
        names.extend([f"CFB_GOOGLE_SHEET_{suffix}", f"NCAAF_GOOGLE_SHEET_{suffix}"])
    elif sport == "CBB":
        names.extend([f"CBB_GOOGLE_SHEET_{suffix}", f"NCAAM_GOOGLE_SHEET_{suffix}"])
    elif sport:
        names.append(f"{sport}_GOOGLE_SHEET_{suffix}")
    for name in names:
        value = _secret_or_env(name)
        if value:
            return value
    return ""


def storage_database_config(sport: str | None = None) -> dict[str, str]:
    """Return the resolved workbook contract without making a network request."""
    selected = _canonical_sport(sport if sport is not None else _ACTIVE_SPORT)

    if selected in {"NFL", "CFB", "CBB"}:
        sheet_id = _sport_setting(selected, "ID")
        sheet_name = _sport_setting(selected, "NAME") or _DEFAULT_DATABASE_NAMES[selected]
        return {"sport": selected, "sheet_id": sheet_id, "sheet_name": sheet_name}

    # Backwards-compatible/default path. MLB's existing builder still reads its
    # own GOOGLE_SHEET_NAME directly, but keeping this fallback avoids breaking
    # older shared-storage callers and local utilities.
    sheet_id = _sport_setting(selected, "ID") if selected else ""
    sheet_name = _sport_setting(selected, "NAME") if selected else ""
    return {
        "sport": selected,
        "sheet_id": sheet_id or _secret_or_env("GOOGLE_SHEET_ID"),
        "sheet_name": sheet_name or _secret_or_env("GOOGLE_SHEET_NAME"),
    }


def storage_database_name(sport: str | None = None) -> str:
    config = storage_database_config(sport)
    return config["sheet_name"] or config["sheet_id"]


@st.cache_resource(show_spinner=False)
def _authorized_client(credentials_json: str):
    credentials = Credentials.from_service_account_info(
        json.loads(credentials_json),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    return gspread.authorize(credentials)


@st.cache_resource(show_spinner=False)
def _connect_to_sheets_for(credentials_json: str, sport: str, sheet_id: str, sheet_name: str):
    if not credentials_json or not (sheet_id or sheet_name):
        return None
    client = _authorized_client(credentials_json)
    if sheet_id:
        return client.open_by_key(sheet_id)
    try:
        return client.open(sheet_name)
    except gspread.SpreadsheetNotFound:
        # Football databases are intentionally created on first use. This makes
        # the model/admin the database owner instead of requiring a manual Sheet
        # setup before the season starts.
        if sport in {"NFL", "CFB", "CBB"}:
            return client.create(sheet_name)
        raise


def connect_to_sheets():
    credentials_json = _secret_or_env("GOOGLE_CREDENTIALS")
    config = storage_database_config()
    if not credentials_json or not (config["sheet_id"] or config["sheet_name"]):
        return None
    return _connect_to_sheets_for(
        credentials_json,
        config["sport"],
        config["sheet_id"],
        config["sheet_name"],
    )


def sheets_ready() -> bool:
    try:
        return connect_to_sheets() is not None
    except Exception:
        return False


def get_or_create_worksheet(tab_name: str, columns: Iterable[str]):
    workbook = connect_to_sheets()
    if workbook is None:
        return None
    columns = list(columns)
    try:
        worksheet = workbook.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        worksheet = workbook.add_worksheet(
            title=tab_name,
            rows=2000,
            cols=max(20, len(columns) + 5),
        )
        if columns:
            worksheet.update([columns])
    return worksheet


def read_sheet(tab_name: str, columns: Iterable[str]) -> pd.DataFrame:
    columns = list(columns)
    try:
        worksheet = get_or_create_worksheet(tab_name, columns)
        if worksheet is None:
            return pd.DataFrame(columns=columns)
        values = worksheet.get_all_values()
        if not values:
            return pd.DataFrame(columns=columns)
        header = [str(x).strip() for x in values[0]]
        rows = []
        for source_row in values[1:]:
            row = {}
            for column in columns:
                if column in header:
                    idx = header.index(column)
                    row[column] = source_row[idx] if idx < len(source_row) else ""
                else:
                    row[column] = ""
            if any(str(value).strip() for value in row.values()):
                rows.append(row)
        return pd.DataFrame(rows, columns=columns)
    except Exception as exc:
        st.error(f"Could not read Google Sheets tab '{tab_name}': {exc}")
        return pd.DataFrame(columns=columns)


def write_sheet(tab_name: str, dataframe: pd.DataFrame, columns: Iterable[str]) -> bool:
    columns = list(columns)
    try:
        worksheet = get_or_create_worksheet(tab_name, columns)
        if worksheet is None:
            st.warning(
                "Google Sheets is not configured. Add GOOGLE_CREDENTIALS and the sport database setting."
            )
            return False
        out = dataframe.copy() if dataframe is not None else pd.DataFrame(columns=columns)
        for column in columns:
            if column not in out.columns:
                out[column] = ""
        out = out[columns].fillna("").astype(str)
        worksheet.clear()
        worksheet.update([columns] + out.values.tolist())
        return True
    except Exception as exc:
        st.error(f"Could not write Google Sheets tab '{tab_name}': {exc}")
        return False


def append_row(tab_name: str, row: dict, columns: Iterable[str]) -> bool:
    columns = list(columns)
    dataframe = read_sheet(tab_name, columns)
    payload = {column: row.get(column, "") for column in columns}
    dataframe = pd.concat([dataframe, pd.DataFrame([payload])], ignore_index=True)
    return write_sheet(tab_name, dataframe, columns)
