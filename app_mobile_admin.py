# --- Google Sheets Integrated Test Version ---
import streamlit as st
import pandas as pd
import requests
import gspread
import json
import os
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="MLB Mobile Admin - Google Sheets Test", layout="centered")

# ---------- ADMIN PASSWORD ----------
def require_admin_password():
    admin_password = os.environ.get("ADMIN_PASSWORD", "")

    if not admin_password:
        try:
            admin_password = st.secrets.get("ADMIN_PASSWORD", "")
        except Exception:
            admin_password = ""

    if not admin_password:
        admin_password = "admin"
        st.warning("No ADMIN_PASSWORD found. Temporary password is: admin")

    if st.session_state.get("admin_authenticated"):
        return True

    st.title("MLB Mobile Admin")
    st.caption("Google Sheets connection test")

    entered_password = st.text_input("Admin password", type="password")

    if st.button("Log in"):
        if entered_password == admin_password:
            st.session_state["admin_authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    st.stop()


require_admin_password()


# ---------- GOOGLE SHEETS CONNECTION ----------
def connect_to_sheets():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")

    if not creds_json:
        st.error("Missing GOOGLE_CREDENTIALS environment variable in Render.")
        st.stop()

    sheet_name = os.environ.get("GOOGLE_SHEET_NAME")

    if not sheet_name:
        st.error("Missing GOOGLE_SHEET_NAME environment variable in Render.")
        st.stop()

    try:
        creds_dict = json.loads(creds_json)
    except Exception as e:
        st.error(f"GOOGLE_CREDENTIALS is not valid JSON: {e}")
        st.stop()

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)

    return client.open(sheet_name)


def read_sheet(tab_name):
    try:
        sheet = connect_to_sheets().worksheet(tab_name)
        data = sheet.get_all_records()
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"Could not read tab '{tab_name}': {e}")
        return pd.DataFrame()


def write_sheet(tab_name, df):
    try:
        sheet = connect_to_sheets().worksheet(tab_name)
        sheet.clear()

        if df.empty:
            sheet.update([df.columns.values.tolist()])
        else:
            sheet.update([df.columns.values.tolist()] + df.values.tolist())

        return True
    except Exception as e:
        st.error(f"Could not write tab '{tab_name}': {e}")
        return False


# ---------- SIMPLE TEST UI ----------
st.title("MLB Mobile Admin")
st.subheader("Google Sheets Connection Test")

st.info(
    "Use Load Data first. Since you already moved your real data into Google Sheets, "
    "do not click Save Test Data unless you want to add a small test row."
)

tab = st.radio("Choose tab to test", ["bet_tracker", "daily_slate"], horizontal=True)

if st.button("Load Data"):
    data = read_sheet(tab)

    if data.empty:
        st.warning(f"No data found in {tab}, or the tab could not be read.")
    else:
        st.success(f"Loaded {len(data)} rows from {tab}.")
        st.dataframe(data, use_container_width=True, hide_index=True)


st.divider()

st.subheader("Optional Test Write")

st.caption("This will append one harmless test row to bet_tracker so you can confirm writing works.")

if st.button("Add Test Row to bet_tracker"):
    current = read_sheet("bet_tracker")

    test_row = {
        "Date": "TEST",
        "Bet Type": "Test",
        "Selection": "Google Sheets Test",
        "Market": "Test",
        "Odds/Line": "-110",
        "Model %": "65.1%",
        "Implied %": "",
        "Edge %": "",
        "Result": "Pending"
    }

    if current.empty:
        current = pd.DataFrame(columns=list(test_row.keys()))

    for col in test_row.keys():
        if col not in current.columns:
            current[col] = ""

    current = pd.concat([current, pd.DataFrame([test_row])], ignore_index=True)
    current = current[list(test_row.keys())]

    if write_sheet("bet_tracker", current):
        st.success("Test row added to bet_tracker.")
