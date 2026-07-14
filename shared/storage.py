import json
import os
from typing import Iterable

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials


def _secret_or_env(name: str) -> str:
    value = os.environ.get(name, "")
    if value:
        return value
    try:
        return str(st.secrets.get(name, "") or "")
    except Exception:
        return ""


@st.cache_resource
def connect_to_sheets():
    credentials_json = _secret_or_env("GOOGLE_CREDENTIALS")
    sheet_name = _secret_or_env("GOOGLE_SHEET_NAME")
    if not credentials_json or not sheet_name:
        return None
    credentials = Credentials.from_service_account_info(
        json.loads(credentials_json),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    return gspread.authorize(credentials).open(sheet_name)


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
            st.warning("Google Sheets is not configured. Add GOOGLE_CREDENTIALS and GOOGLE_SHEET_NAME.")
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
