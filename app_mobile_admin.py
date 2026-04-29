# --- Google Sheets Integrated Version ---
import streamlit as st
import pandas as pd
import requests
import gspread
import json
import os
from google.oauth2.service_account import Credentials

# ---------- GOOGLE SHEETS CONNECTION ----------
def connect_to_sheets():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    creds_dict = json.loads(creds_json)

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)

    client = gspread.authorize(creds)
    sheet_name = os.environ.get("GOOGLE_SHEET_NAME")

    return client.open(sheet_name)

def read_sheet(tab_name):
    try:
        sheet = connect_to_sheets().worksheet(tab_name)
        data = sheet.get_all_records()
        return pd.DataFrame(data)
    except:
        return pd.DataFrame()

def write_sheet(tab_name, df):
    sheet = connect_to_sheets().worksheet(tab_name)
    sheet.clear()
    sheet.update([df.columns.values.tolist()] + df.values.tolist())

# ---------- SIMPLE TEST UI ----------
st.title("MLB Mobile Admin (Google Sheets Connected)")

st.subheader("Test Save Data")

df = pd.DataFrame({
    "Team": ["Yankees"],
    "Win%": [65.1]
})

if st.button("Save Test Data"):
    write_sheet("bet_tracker", df)
    st.success("Saved to Google Sheets!")

if st.button("Load Data"):
    data = read_sheet("bet_tracker")
    st.write(data)
