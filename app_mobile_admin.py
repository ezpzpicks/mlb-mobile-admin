import streamlit as st
import pandas as pd
import math
import os
import requests
import json
import gspread
from google.oauth2.service_account import Credentials
from datetime import date

st.set_page_config(page_title="MLB Mobile Admin", layout="centered")

# -----------------------
# MOBILE ADMIN PASSWORD
# -----------------------

def require_admin_password():
    """Simple password gate for the mobile/admin website.

    Set ADMIN_PASSWORD in Streamlit secrets or as a Render environment variable.
    Local fallback password is 'admin' so you can test immediately.
    """
    admin_password = ""
    try:
        admin_password = st.secrets.get("ADMIN_PASSWORD", "")
    except Exception:
        admin_password = "Ryan2628$"

    if not admin_password:
        admin_password = os.environ.get("ADMIN_PASSWORD", "")

    if not admin_password:
        admin_password = "admin"
        st.warning("No ADMIN_PASSWORD secret found. Temporary local password is: admin")

    if st.session_state.get("admin_authenticated"):
        return True

    st.title("MLB Model Mobile Admin")
    st.caption("Private editor for building matchups, saving plays, and updating results from your phone.")
    entered_password = st.text_input("Admin password", type="password")

    if st.button("Log in"):
        if entered_password == admin_password:
            st.session_state["admin_authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    st.stop()


require_admin_password()

st.title("MLB Model Mobile Admin")
st.caption("Phone-friendly admin version. Your PC app can stay separate and untouched.")
st.caption("Storage: Google Sheets database")

st.markdown(
    """
    <style>
    /* Mobile-friendly spacing and table behavior */
    .block-container {padding-top: 1rem; padding-left: 0.8rem; padding-right: 0.8rem; max-width: 1100px;}

    /* Fix mobile metric cards: previous white background made the metric text invisible in dark mode */
    div[data-testid="stMetric"] {
        background: #111827 !important;
        border: 1px solid #374151 !important;
        padding: 0.85rem !important;
        border-radius: 0.85rem !important;
        box-shadow: 0 1px 2px rgba(0,0,0,0.25);
    }
    div[data-testid="stMetric"] * {
        color: #f9fafb !important;
    }
    div[data-testid="stMetricLabel"] p {
        color: #d1d5db !important;
        font-weight: 600 !important;
    }
    div[data-testid="stMetricValue"] {
        color: #ffffff !important;
        font-weight: 800 !important;
    }
    div[data-testid="stMetricDelta"] {
        color: #d1d5db !important;
    }

    div[data-testid="stDataFrame"] {font-size: 0.85rem;}
    .stButton > button {width: 100%; border-radius: 0.75rem; font-weight: 600;}
    @media (max-width: 768px) {
        .block-container {padding-left: 0.45rem; padding-right: 0.45rem;}
        h1 {font-size: 1.45rem !important;}
        h2, h3 {font-size: 1.1rem !important;}
        div[data-testid="stDataFrame"] {font-size: 0.78rem;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)


TRACKER_TAB = "bet_tracker"
SLATE_TAB = "daily_slate"


# -----------------------
# GOOGLE SHEETS STORAGE
# -----------------------

def get_google_credentials_json():
    """Read service account JSON from Render env vars or Streamlit secrets."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS", "")

    if not creds_json:
        try:
            creds_json = st.secrets.get("GOOGLE_CREDENTIALS", "")
        except Exception:
            creds_json = ""

    return creds_json


def get_google_sheet_name():
    sheet_name = os.environ.get("GOOGLE_SHEET_NAME", "")

    if not sheet_name:
        try:
            sheet_name = st.secrets.get("GOOGLE_SHEET_NAME", "")
        except Exception:
            sheet_name = ""

    return sheet_name


@st.cache_resource
def connect_to_sheets():
    creds_json = get_google_credentials_json()
    sheet_name = get_google_sheet_name()

    if not creds_json:
        st.error("Missing GOOGLE_CREDENTIALS environment variable in Render.")
        st.stop()

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


def get_or_create_worksheet(tab_name, columns=None):
    workbook = connect_to_sheets()

    try:
        worksheet = workbook.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        worksheet = workbook.add_worksheet(title=tab_name, rows=1000, cols=max(20, len(columns or [])))
        if columns:
            worksheet.update([columns])

    return worksheet


def read_sheet(tab_name, columns):
    try:
        worksheet = get_or_create_worksheet(tab_name, columns)
        records = worksheet.get_all_records()
        df = pd.DataFrame(records)

        for col in columns:
            if col not in df.columns:
                df[col] = ""

        if df.empty:
            df = pd.DataFrame(columns=columns)

        return df[columns]
    except Exception as e:
        st.error(f"Could not read Google Sheet tab '{tab_name}': {e}")
        return pd.DataFrame(columns=columns)


def write_sheet(tab_name, df, columns):
    try:
        worksheet = get_or_create_worksheet(tab_name, columns)

        out = df.copy() if df is not None else pd.DataFrame(columns=columns)
        for col in columns:
            if col not in out.columns:
                out[col] = ""
        out = out[columns]
        out = out.fillna("").astype(str)

        worksheet.clear()
        values = [columns] + out.values.tolist()
        worksheet.update(values)
        return True
    except Exception as e:
        st.error(f"Could not write Google Sheet tab '{tab_name}': {e}")
        return False


# -----------------------
# BASIC HELPERS
# -----------------------

def clean_percent(value):
    if pd.isna(value):
        return 0
    if isinstance(value, str):
        value = value.replace("%", "").strip()
        try:
            value = float(value)
        except:
            return 0
    if value > 1:
        return value / 100
    return value


def get_value(df, lookup_col, lookup_value, return_col, default=0):
    try:
        temp = df.copy()
        temp[lookup_col] = temp[lookup_col].astype(str).str.strip()
        lookup_value = str(lookup_value).strip()
        row = temp[temp[lookup_col] == lookup_value]

        if row.empty:
            return default

        value = row.iloc[0][return_col]

        if pd.isna(value):
            return default

        return value
    except:
        return default


def american_odds_to_implied_prob(odds):
    try:
        odds = float(odds)
        if odds < 0:
            return abs(odds) / (abs(odds) + 100)
        elif odds > 0:
            return 100 / (odds + 100)
        else:
            return 0
    except:
        return 0


def moneyline_grade(edge):
    if edge >= 0.08:
        return "A Moneyline"
    elif edge >= 0.05:
        return "B Moneyline"
    else:
        return "Non-Edge Moneyline"


def extract_k_line(k_summary):
    text = str(k_summary)
    if "Line" not in text:
        return ""
    line_part = text.split("Line", 1)[1].strip()
    line_part = line_part.replace("/", "").strip()
    line_part = line_part.split("(", 1)[0].strip()
    return f"Line {line_part}" if line_part else ""


def to_last_first(name):
    """Convert MLB/API name format from 'First Last' to your Excel format 'Last, First'."""
    name = str(name).strip()
    if not name or name.lower() in ["nan", "none"]:
        return ""
    if name.upper() == "TBD":
        return "TBD"
    if "," in name:
        return name
    parts = name.split()
    if len(parts) <= 1:
        return name
    first = parts[0]
    last = " ".join(parts[1:])
    return f"{last}, {first}"


def to_first_last(name):
    """Convert your Excel name format 'Last, First' back to 'First Last' for odds prop matching."""
    name = str(name).strip()
    if not name or name.lower() in ["nan", "none"]:
        return ""
    if name.upper() == "TBD":
        return "TBD"
    if "," not in name:
        return name
    last, first = name.split(",", 1)
    return f"{first.strip()} {last.strip()}"


def normalize_name_for_match(name):
    return str(name).lower().replace(".", "").replace("'", "").strip()


# -----------------------
# BET TRACKER
# -----------------------

TRACKER_COLUMNS = [
    "Date", "Bet Type", "Selection", "Market", "Odds/Line",
    "Model %", "Implied %", "Edge %", "Result"
]


def load_tracker():
    return read_sheet(TRACKER_TAB, TRACKER_COLUMNS)


def save_tracker(df):
    return write_sheet(TRACKER_TAB, df, TRACKER_COLUMNS)


def pct_to_float(value):
    try:
        text = str(value).replace("%", "").strip()
        if text == "" or text.lower() == "nan":
            return 0.0
        return float(text)
    except Exception:
        return 0.0


def parse_american_odds(value):
    """Pull American odds out of Odds/Line.
    Works for values like -110, +125, "4.5 / -110", or "Line 4.5 Odds -110".
    """
    import re
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    text = str(value).strip()
    if not text:
        return None
    matches = re.findall(r"[+-]?\d+", text)
    if not matches:
        return None
    try:
        odds = int(matches[-1])
    except Exception:
        return None
    if -99 < odds < 100:
        return None
    return odds


def profit_units_from_american_odds(odds, result):
    """Assumes 1 unit risked per tracked bet."""
    if result == "Push":
        return 0.0
    if result == "Loss":
        return -1.0
    if result != "Win":
        return 0.0
    odds = parse_american_odds(odds)
    if odds is None:
        odds = -110
    if odds > 0:
        return odds / 100
    return 100 / abs(odds)


def sort_tracker_by_model_pct(df):
    if df is None or df.empty or "Model %" not in df.columns:
        return df
    out = df.copy()
    out["_model_pct_sort"] = out["Model %"].apply(pct_to_float)
    out = out.sort_values("_model_pct_sort", ascending=False).drop(columns=["_model_pct_sort"])
    return out


def styled_tracker_dataframe(df):
    if df is None or df.empty:
        return df

    def row_style(row):
        pct = pct_to_float(row.get("Model %", 0))

        if pct >= 65:
            style = "background-color: #d1fae5; color: #065f46; font-weight: bold;"
        elif pct >= 58:
            style = "background-color: #fef3c7; color: #92400e; font-weight: bold;"
        elif pct > 50:
            style = "background-color: #e0f2fe; color: #075985; font-weight: bold;"
        else:
            style = ""

        return [style] * len(row)

    return df.style.apply(row_style, axis=1)


def display_tracker_dataframe(df):
    if df is None or df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
        return
    df = sort_tracker_by_model_pct(df)
    column_config = {col: st.column_config.TextColumn(col, width="small") for col in df.columns}
    for col in ["Selection", "Market", "Bet Type"]:
        if col in column_config:
            column_config[col] = st.column_config.TextColumn(col, width="medium")
    st.dataframe(styled_tracker_dataframe(df), use_container_width=True, hide_index=True, column_config=column_config)


def add_bet(bet_type, selection, market, odds_line="", model_pct="", implied_pct="", edge_pct=""):
    df = load_tracker()

    new_row = {
        "Date": str(date.today()),
        "Bet Type": bet_type,
        "Selection": selection,
        "Market": market,
        "Odds/Line": odds_line,
        "Model %": model_pct,
        "Implied %": implied_pct,
        "Edge %": edge_pct,
        "Result": "Pending"
    }

    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    save_tracker(df)


def display_record_summary(df):
    completed = df[df["Result"].isin(["Win", "Loss", "Push"])].copy()

    if completed.empty:
        st.info("No completed bets yet.")
        return

    rows = []

    for bet_type in sorted(completed["Bet Type"].dropna().unique()):
        sub = completed[completed["Bet Type"] == bet_type].copy()
        wins = (sub["Result"] == "Win").sum()
        losses = (sub["Result"] == "Loss").sum()
        pushes = (sub["Result"] == "Push").sum()
        total_decisions = wins + losses
        total_bets = wins + losses + pushes
        win_pct = (wins / total_decisions * 100) if total_decisions > 0 else 0

        units_won = 0.0
        for _, bet in sub.iterrows():
            units_won += profit_units_from_american_odds(bet.get("Odds/Line", ""), bet.get("Result", ""))

        risked_units = total_decisions
        roi = (units_won / risked_units * 100) if risked_units > 0 else 0

        if wins > losses:
            record_status = "Winning"
        elif wins == losses:
            record_status = "Even"
        else:
            record_status = "Losing"

        rows.append({
            "Bet Type": bet_type,
            "Record Status": record_status,
            "Wins": wins,
            "Losses": losses,
            "Pushes": pushes,
            "Total Bets": total_bets,
            "Win %": round(win_pct, 1),
            "Units Won": round(units_won, 2),
            "ROI %": round(roi, 1)
        })

    summary_df = pd.DataFrame(rows)
    summary_df = summary_df.sort_values(["Record Status", "Win %"], ascending=[True, False]).reset_index(drop=True)

    def color_summary_row(row):
        wins = int(row.get("Wins", 0))
        losses = int(row.get("Losses", 0))
        return [bet_record_style_from_counts(wins, losses)] * len(row)

    st.dataframe(summary_df.style.apply(color_summary_row, axis=1), use_container_width=True, hide_index=True)


def render_bet_tracker():
    st.header("Bet Tracker")

    tracker_df = load_tracker()
    tracker_df = sort_tracker_by_model_pct(tracker_df)

    subtab1, subtab2, subtab3, subtab4 = st.tabs([
        "Pending Bets", "Completed Bets", "Record Summary", "Delete Bets"
    ])

    with subtab1:
        st.subheader("Pending Bets")
        st.caption("Pending bets are sorted by highest model win/projection %. Use Delete to remove mistakes from today's slate.")

        pending = tracker_df[tracker_df["Result"] == "Pending"].copy()
        pending = sort_tracker_by_model_pct(pending)

        if pending.empty:
            st.info("No pending bets.")
        else:
            pending_editor = pending.copy()
            pending_editor.insert(0, "Delete", False)
            pending_editor.insert(1, "Row ID", pending_editor.index)

            edited_pending = st.data_editor(
                pending_editor,
                use_container_width=True,
                num_rows="fixed",
                column_config={
                    "Delete": st.column_config.CheckboxColumn("Delete"),
                    "Row ID": st.column_config.NumberColumn("Row ID", disabled=True),
                    "Result": st.column_config.SelectboxColumn(
                        "Result",
                        options=["Pending", "Win", "Loss", "Push"],
                        required=True
                    )
                },
                disabled=[col for col in pending_editor.columns if col not in ["Delete", "Result"]],
                key="pending_editor_tracker"
            )

            if st.button("Update / Delete Pending Bets", key="update_or_delete_pending_results"):
                rows_to_delete = edited_pending.loc[edited_pending["Delete"] == True, "Row ID"].tolist()

                if rows_to_delete:
                    tracker_df = tracker_df.drop(index=rows_to_delete)

                edited_no_helper = edited_pending.drop(columns=["Delete", "Row ID"])
                edited_no_helper.index = edited_pending["Row ID"]
                edited_no_helper = edited_no_helper.drop(index=rows_to_delete, errors="ignore")
                tracker_df.update(edited_no_helper)

                save_tracker(tracker_df)
                st.success("Pending bets updated.")
                st.rerun()

            st.divider()
            display_tracker_dataframe(pending)

    with subtab2:
        st.subheader("Completed Bets")
        completed = tracker_df[tracker_df["Result"].isin(["Win", "Loss", "Push"])].copy()
        completed = sort_tracker_by_model_pct(completed)

        if completed.empty:
            st.info("No completed bets yet.")
        else:
            display_tracker_dataframe(completed)

    with subtab3:
        st.subheader("Record Summary")
        display_record_summary(tracker_df)

        st.divider()
        st.subheader("Full Tracker File")
        display_tracker_dataframe(tracker_df)

    with subtab4:
        st.subheader("Delete Bets")
        st.caption("Check Delete for any old or unwanted bet, then click Delete Selected Bets.")

        if tracker_df.empty:
            st.info("No bets to delete.")
        else:
            delete_view = sort_tracker_by_model_pct(tracker_df).copy()
            delete_view.insert(0, "Delete", False)
            delete_view.insert(1, "Row ID", delete_view.index)

            edited_delete_view = st.data_editor(
                delete_view,
                use_container_width=True,
                num_rows="fixed",
                column_config={
                    "Delete": st.column_config.CheckboxColumn("Delete"),
                    "Row ID": st.column_config.NumberColumn("Row ID", disabled=True)
                },
                disabled=[col for col in delete_view.columns if col != "Delete"],
                key="delete_bets_editor"
            )

            if st.button("Delete Selected Bets", key="delete_selected_bets"):
                rows_to_delete = edited_delete_view.loc[
                    edited_delete_view["Delete"] == True, "Row ID"
                ].tolist()

                if rows_to_delete:
                    tracker_df = tracker_df.drop(index=rows_to_delete)
                    save_tracker(tracker_df)
                    st.success("Selected bets deleted.")
                    st.rerun()
                else:
                    st.info("No bets selected for deletion.")


# -----------------------
# DAILY SLATE
# -----------------------

SLATE_COLUMNS = [
    "Date",
    "Away Team",
    "Home Team",
    "Better ML",
    "ML Odds",
    "ML Grade",
    "NRFI Grade",
    "Away Pitcher K + Grade",
    "Away Pitcher K Score",
    "Home Pitcher K + Grade",
    "Home Pitcher K Score"
]


def load_slate():
    return read_sheet(SLATE_TAB, SLATE_COLUMNS)


def save_slate(df):
    return write_sheet(SLATE_TAB, df, SLATE_COLUMNS)


def add_slate_row(
    away_team,
    home_team,
    better_ml,
    ml_odds,
    ml_grade,
    nrfi_grade,
    away_pitcher_k_grade,
    away_pitcher_k_score,
    home_pitcher_k_grade,
    home_pitcher_k_score
):
    df = load_slate()

    new_row = {
        "Date": str(date.today()),
        "Away Team": away_team,
        "Home Team": home_team,
        "Better ML": better_ml,
        "ML Odds": ml_odds,
        "ML Grade": ml_grade,
        "NRFI Grade": nrfi_grade,
        "Away Pitcher K + Grade": away_pitcher_k_grade,
        "Away Pitcher K Score": away_pitcher_k_score,
        "Home Pitcher K + Grade": home_pitcher_k_grade,
        "Home Pitcher K Score": home_pitcher_k_score
    }

    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    save_slate(df)


# -----------------------
# STRIKEOUT MODEL
# -----------------------

def expected_strikeouts(pitcher, opponent, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp):
    so_last = get_value(pitcher_last_year, "Player", pitcher, "SO", 0)
    so_this = get_value(pitcher_this_year, "Player", pitcher, "SO", 0)

    ip_last = get_value(pitcher_last_year, "Player", pitcher, "IP", 0)
    ip_this = get_value(pitcher_this_year, "Player", pitcher, "IP", 0)

    g_last = get_value(pitcher_last_year, "Player", pitcher, "G", 0)
    g_this = get_value(pitcher_this_year, "Player", pitcher, "G", 0)

    ipg_last = ip_last / g_last if g_last > 0 else 0
    ipg_this = ip_this / g_this if g_this > 0 else 0

    throws_hand = get_value(pitcher_this_year, "Player", pitcher, "Throws", None)
    if throws_hand is None:
        throws_hand = get_value(pitcher_last_year, "Player", pitcher, "Throws", "R")

    ab_l = get_value(team_batting_lhp, "Teams", opponent, "At Bats", 0)
    ab_r = get_value(team_batting_rhp, "Teams", opponent, "At Bats", 0)

    so_l = get_value(team_batting_lhp, "Teams", opponent, "Strikeouts", 0)
    so_r = get_value(team_batting_rhp, "Teams", opponent, "Strikeouts", 0)

    obp_l = clean_percent(get_value(team_batting_lhp, "Teams", opponent, "On-Base %", 0.32))
    obp_r = clean_percent(get_value(team_batting_rhp, "Teams", opponent, "On-Base %", 0.32))

    if throws_hand == "L":
        opp_ab_split = ab_l
        opp_so_split = so_l
        opp_obp_split = obp_l
    else:
        opp_ab_split = ab_r
        opp_so_split = so_r
        opp_obp_split = obp_r

    opp_ab_total = ab_l + ab_r
    opp_so_total = so_l + so_r

    opp_k_overall = opp_so_total / opp_ab_total if opp_ab_total > 0 else 0.22
    opp_k_split_raw = opp_so_split / opp_ab_split if opp_ab_split > 0 else opp_k_overall

    sample_weight = min(0.65, opp_ab_split / 700) if opp_ab_split > 0 else 0
    opp_k_split = ((1 - sample_weight) * opp_k_overall) + (sample_weight * opp_k_split_raw)
    split_edge = opp_k_split_raw - opp_k_overall

    opp_k_rate_mult = min(1.12, max(0.88, 1 + ((opp_k_split - 0.22) * 2.25)))
    hand_edge_mult = min(1.12, max(0.88, 1 + (split_edge * 3 * sample_weight)))
    opp_obp_mult = min(1.04, max(0.96, 1 - ((opp_obp_split - 0.32) * 0.7)))

    opp_mult = opp_k_rate_mult * hand_edge_mult * opp_obp_mult

    bf_last = ip_last * 4.3
    bf_this = ip_this * 4.3

    relief_flag = 1 if g_last > 0 and ipg_last < 3.5 else 0

    if relief_flag == 1:
        k_per_bf = ((0.3 * so_last) + (0.7 * so_this)) / max(1, (0.3 * bf_last) + (0.7 * bf_this))
    else:
        k_per_bf = ((0.6 * so_last) + (0.4 * so_this)) / max(1, (0.6 * bf_last) + (0.4 * bf_this))

    if ip_last > 0 and g_last > 0 and ip_this > 0 and g_this > 0:
        exp_ip = (0.2 * ipg_last + 0.8 * ipg_this) if relief_flag == 1 else (0.35 * ipg_last + 0.65 * ipg_this)
    elif ip_this > 0 and g_this > 0:
        exp_ip = ipg_this
    elif ip_last > 0 and g_last > 0:
        exp_ip = ipg_last
    else:
        exp_ip = 5

    bf_per_start = exp_ip * 4.3
    leash_mult = min(1.08, max(0.92, bf_per_start / 24))

    return k_per_bf * bf_per_start * opp_mult * leash_mult * 0.96


def six_inning_strikeouts(pitcher, opponent, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp):
    so_last = get_value(pitcher_last_year, "Player", pitcher, "SO", 0)
    so_this = get_value(pitcher_this_year, "Player", pitcher, "SO", 0)

    ip_last = get_value(pitcher_last_year, "Player", pitcher, "IP", 0)
    ip_this = get_value(pitcher_this_year, "Player", pitcher, "IP", 0)

    g_last = get_value(pitcher_last_year, "Player", pitcher, "G", 0)
    ipg_last = ip_last / g_last if g_last > 0 else 0

    throws_hand = get_value(pitcher_this_year, "Player", pitcher, "Throws", None)
    if throws_hand is None:
        throws_hand = get_value(pitcher_last_year, "Player", pitcher, "Throws", "R")

    ab_l = get_value(team_batting_lhp, "Teams", opponent, "At Bats", 0)
    ab_r = get_value(team_batting_rhp, "Teams", opponent, "At Bats", 0)

    so_l = get_value(team_batting_lhp, "Teams", opponent, "Strikeouts", 0)
    so_r = get_value(team_batting_rhp, "Teams", opponent, "Strikeouts", 0)

    obp_l = clean_percent(get_value(team_batting_lhp, "Teams", opponent, "On-Base %", 0.32))
    obp_r = clean_percent(get_value(team_batting_rhp, "Teams", opponent, "On-Base %", 0.32))

    if throws_hand == "L":
        opp_ab_split = ab_l
        opp_so_split = so_l
        opp_obp_split = obp_l
    else:
        opp_ab_split = ab_r
        opp_so_split = so_r
        opp_obp_split = obp_r

    opp_ab_total = ab_l + ab_r
    opp_so_total = so_l + so_r

    opp_k_overall = opp_so_total / opp_ab_total if opp_ab_total > 0 else 0.22
    opp_k_split_raw = opp_so_split / opp_ab_split if opp_ab_split > 0 else opp_k_overall

    sample_weight = min(0.65, opp_ab_split / 700) if opp_ab_split > 0 else 0
    opp_k_split = ((1 - sample_weight) * opp_k_overall) + (sample_weight * opp_k_split_raw)
    split_edge = opp_k_split_raw - opp_k_overall

    opp_k_rate_mult = min(1.12, max(0.88, 1 + ((opp_k_split - 0.22) * 2.25)))
    hand_edge_mult = min(1.12, max(0.88, 1 + (split_edge * 3 * sample_weight)))
    opp_obp_mult = min(1.04, max(0.96, 1 - ((opp_obp_split - 0.32) * 0.7)))

    opp_mult = opp_k_rate_mult * hand_edge_mult * opp_obp_mult

    bf_last = ip_last * 4.3
    bf_this = ip_this * 4.3

    relief_flag = 1 if g_last > 0 and ipg_last < 3.5 else 0

    if relief_flag == 1:
        k_per_bf = ((0.3 * so_last) + (0.7 * so_this)) / max(1, (0.3 * bf_last) + (0.7 * bf_this))
    else:
        k_per_bf = ((0.6 * so_last) + (0.4 * so_this)) / max(1, (0.6 * bf_last) + (0.4 * bf_this))

    return k_per_bf * (6 * 4.3) * opp_mult * 0.96


def pitcher_ipg(pitcher, pitcher_this_year, pitcher_last_year):
    ip_last = get_value(pitcher_last_year, "Player", pitcher, "IP", 0)
    g_last = get_value(pitcher_last_year, "Player", pitcher, "G", 0)

    ip_this = get_value(pitcher_this_year, "Player", pitcher, "IP", 0)
    g_this = get_value(pitcher_this_year, "Player", pitcher, "G", 0)

    ipg_last = ip_last / g_last if g_last > 0 else 0
    ipg_this = ip_this / g_this if g_this > 0 else 0

    return ipg_this, ipg_last


def strikeout_volatility(pitcher, pitcher_this_year, pitcher_last_year):
    ip_last = get_value(pitcher_last_year, "Player", pitcher, "IP", 0)
    g_last = get_value(pitcher_last_year, "Player", pitcher, "G", 0)

    ip_this = get_value(pitcher_this_year, "Player", pitcher, "IP", 0)
    g_this = get_value(pitcher_this_year, "Player", pitcher, "G", 0)

    ipg_last = ip_last / g_last if g_last > 0 else 0
    ipg_this = ip_this / g_this if g_this > 0 else 0

    sample_score = (
        (2 if g_this >= 6 else 1 if g_this >= 3 else 0) +
        (2 if g_last >= 15 else 1 if g_last >= 8 else 0)
    )

    workload_change = abs(ipg_this - ipg_last)

    if g_this == 0 or ipg_this < 4.2 or sample_score <= 1:
        return "High volatility"

    if (
        (ipg_this < 5.2 and sample_score <= 2) or
        (workload_change >= 1.3 and ipg_this < 5.5) or
        sample_score <= 2
    ):
        return "Medium volatility"

    return "Low volatility"


def strikeout_bet_grade(exp_k, six_k, ipg_this, ipg_last, line, volatility):
    edge = exp_k - line
    abs_edge = abs(edge)
    dir_var = exp_k - six_k
    var_abs = abs(dir_var)

    avg_ip = ipg_this if ipg_this > 0 else ipg_last if ipg_last > 0 else 5

    if abs_edge >= 3:
        base_score = 4
    elif abs_edge >= 2:
        base_score = 3
    elif abs_edge >= 1.2:
        base_score = 2
    elif abs_edge >= 0.6:
        base_score = 1
    else:
        base_score = 0

    if volatility == "Low volatility":
        vol_adj = 1
    elif volatility == "Medium volatility":
        vol_adj = 0
    else:
        vol_adj = -1

    if avg_ip >= 6.1:
        ip_adj = 2
    elif avg_ip >= 5.5:
        ip_adj = 1
    elif avg_ip >= 4.5:
        ip_adj = 0
    else:
        ip_adj = -1

    if dir_var < -1.3:
        var_adj_over = -2
    elif dir_var < -0.7:
        var_adj_over = -1
    elif dir_var > 1.3:
        var_adj_over = -1 if (volatility == "High volatility" or avg_ip < 5.5) else 1
    elif var_abs <= 0.7:
        var_adj_over = 1
    else:
        var_adj_over = 0

    if dir_var > 1.3:
        var_adj_under = -2
    elif dir_var > 0.7:
        var_adj_under = -1
    elif dir_var < -1.3:
        var_adj_under = 1 if avg_ip < 5.5 else 0
    elif var_abs <= 0.7:
        var_adj_under = 1
    else:
        var_adj_under = 0

    total_over = base_score + vol_adj + ip_adj + var_adj_over
    total_under = base_score + vol_adj + ip_adj + var_adj_under

    if edge >= 0.6:
        if total_over >= 6:
            return "STRONG OVER", edge
        elif total_over >= 4:
            return "OVER", edge
        elif total_over >= 2:
            return "LEAN OVER", edge
        else:
            return "PASS", edge

    if edge <= -0.6:
        if total_under >= 6:
            return "STRONG UNDER", edge
        elif total_under >= 4:
            return "UNDER", edge
        elif total_under >= 2:
            return "LEAN UNDER", edge
        else:
            return "PASS", edge

    return "PASS", edge


# -----------------------


def pitcher_k_strength_score(exp_k, six_k, line, volatility, ipg_this, ipg_last):
    edge = exp_k - line
    abs_edge = abs(edge)

    avg_ip = ipg_this if ipg_this > 0 else ipg_last if ipg_last > 0 else 5
    agreement_gap = abs(exp_k - six_k)

    # Edge: biggest piece of the score, max 45 points
    edge_score = min(45, abs_edge / 2.5 * 45)

    # Volatility: lower volatility gets more credit, max 20 points
    if volatility == "Low volatility":
        vol_score = 20
    elif volatility == "Medium volatility":
        vol_score = 12
    else:
        vol_score = 4

    # IP expectation/workload: max 20 points
    if avg_ip >= 6.2:
        ip_score = 20
    elif avg_ip >= 5.7:
        ip_score = 16
    elif avg_ip >= 5.2:
        ip_score = 11
    elif avg_ip >= 4.7:
        ip_score = 6
    else:
        ip_score = 2

    # Model agreement: expected K and 6-inning K being close adds confidence, max 15 points
    if agreement_gap <= 0.4:
        confidence_score = 15
    elif agreement_gap <= 0.8:
        confidence_score = 11
    elif agreement_gap <= 1.2:
        confidence_score = 7
    else:
        confidence_score = 3

    total_score = edge_score + vol_score + ip_score + confidence_score
    return round(min(100, max(0, total_score)), 1)

# NRFI MODEL
# -----------------------

def nrfi_probability(home, away, hp, ap, pitcher_this_year, pitcher_last_year, nrfi_pitchers, nrfi_rhp, nrfi_lhp):
    h_throw = get_value(pitcher_this_year, "Player", hp, "Throws", None)
    if h_throw is None:
        h_throw = get_value(pitcher_last_year, "Player", hp, "Throws", "R")

    a_throw = get_value(pitcher_this_year, "Player", ap, "Throws", None)
    if a_throw is None:
        a_throw = get_value(pitcher_last_year, "Player", ap, "Throws", "R")

    away_split = nrfi_lhp if h_throw == "L" else nrfi_rhp
    home_split = nrfi_lhp if a_throw == "L" else nrfi_rhp

    A_OBP = clean_percent(get_value(away_split, "Teams", away, "OBP", 0.32))
    A_K = clean_percent(get_value(away_split, "Teams", away, "K%", 0.22))
    A_WOBA = clean_percent(get_value(away_split, "Teams", away, "wOBA", 0.32))
    A_BBK = get_value(away_split, "Teams", away, "BB/K", 0.5)
    A_ISO = clean_percent(get_value(away_split, "Teams", away, "ISO", 0.17))

    H_OBP = clean_percent(get_value(home_split, "Teams", home, "OBP", 0.32))
    H_K = clean_percent(get_value(home_split, "Teams", home, "K%", 0.22))
    H_WOBA = clean_percent(get_value(home_split, "Teams", home, "wOBA", 0.32))
    H_BBK = get_value(home_split, "Teams", home, "BB/K", 0.5)
    H_ISO = clean_percent(get_value(home_split, "Teams", home, "ISO", 0.17))

    def pitcher_nrfi_adjustments(pitcher):
        xw_last = clean_percent(get_value(pitcher_last_year, "Player", pitcher, "xwOBA", 0))
        xw_this = clean_percent(get_value(pitcher_this_year, "Player", pitcher, "xwOBA", 0))

        if xw_last > 0 and xw_this > 0:
            xwoba = 0.55 * xw_last + 0.45 * xw_this
        elif xw_this > 0:
            xwoba = xw_this
        else:
            xwoba = xw_last

        ip_last = get_value(pitcher_last_year, "Player", pitcher, "IP", 0)
        ip_this = get_value(pitcher_this_year, "Player", pitcher, "IP", 0)

        so_last = get_value(pitcher_last_year, "Player", pitcher, "SO", 0)
        so_this = get_value(pitcher_this_year, "Player", pitcher, "SO", 0)

        ip_blend = max(1, 0.55 * ip_last + 0.45 * ip_this)
        k_rate = (0.55 * so_last + 0.45 * so_this) / ip_blend

        temp_nrfi = nrfi_pitchers.copy()
        temp_nrfi["Player Name"] = temp_nrfi["Player Name"].astype(str).str.strip()
        temp_nrfi["Season"] = pd.to_numeric(temp_nrfi["Season"], errors="coerce")

        rows = temp_nrfi[
            (temp_nrfi["Player Name"] == str(pitcher).strip()) &
            (temp_nrfi["Season"].isin([2025, 2026]))
        ]

        pa = pd.to_numeric(rows["Plate Appearances"], errors="coerce").fillna(0).sum()
        first_so = pd.to_numeric(rows["SO"], errors="coerce").fillna(0).sum()
        first_r = pd.to_numeric(rows["R"], errors="coerce").fillna(0).sum()

        if pa == 0:
            first_woba = xwoba
            first_obp = 0.32
            first_k_rate = k_rate
            first_r_rate = 0.11
            first_weight = 0
            r_weight = 0
        else:
            pa_series = pd.to_numeric(rows["Plate Appearances"], errors="coerce").fillna(0)
            woba_series = rows["wOBA"].apply(clean_percent)
            obp_series = rows["OBP"].apply(clean_percent)

            first_woba = (pa_series * woba_series).sum() / pa
            first_obp = (pa_series * obp_series).sum() / pa
            first_k_rate = first_so / pa
            first_r_rate = first_r / pa
            first_weight = min(0.45, pa / 260)
            r_weight = min(0.3, pa / 300)

        r_adj = (1 - r_weight) * 0.11 + r_weight * first_r_rate
        k_adj = (1 - first_weight) * k_rate + first_weight * (first_k_rate * 4.3)
        woba_adj = (1 - first_weight) * xwoba + first_weight * first_woba
        obp_adj = (1 - first_weight) * 0.32 + first_weight * first_obp

        return woba_adj, obp_adj, k_adj, r_adj

    HP_woba_adj, HP_obp_adj, HP_k_adj, HP_r_adj = pitcher_nrfi_adjustments(hp)
    AP_woba_adj, AP_obp_adj, AP_k_adj, AP_r_adj = pitcher_nrfi_adjustments(ap)

    top_score = (
        (A_OBP - 0.32) * 6 +
        (A_WOBA - 0.32) * 4.2 +
        (A_ISO - 0.17) * 1.5 -
        (A_K - 0.22) * 3.2 +
        (A_BBK - 0.5) * 1.2 +
        (HP_woba_adj - 0.32) * 6.5 +
        (HP_obp_adj - 0.32) * 2.5 -
        (HP_k_adj - 1) * 0.9 +
        (HP_r_adj - 0.11) * 3
    )

    bot_score = (
        (H_OBP - 0.32) * 6 +
        (H_WOBA - 0.32) * 4.2 +
        (H_ISO - 0.17) * 1.5 -
        (H_K - 0.22) * 3.2 +
        (H_BBK - 0.5) * 1.2 +
        (AP_woba_adj - 0.32) * 6.5 +
        (AP_obp_adj - 0.32) * 2.5 -
        (AP_k_adj - 1) * 0.9 +
        (AP_r_adj - 0.11) * 3 +
        0.03
    )

    top_no_run = 1 / (1 + math.exp(-(1.05 - (top_score * 0.35))))
    bot_no_run = 1 / (1 + math.exp(-(1.05 - (bot_score * 0.35))))

    return top_no_run * bot_no_run


def nrfi_score_formula(nrfi_prob):
    return max(0, min(100, 50 + (nrfi_prob - 0.515) * 450))


def nrfi_bet_grade(score):
    if score >= 82:
        return "ELITE NRFI"
    elif score >= 74:
        return "STRONG NRFI"
    elif score >= 65:
        return "NRFI"
    elif score >= 58:
        return "LEAN NRFI"
    elif score <= 35:
        return "YRFI"
    else:
        return "PASS"


# -----------------------
# MONEYLINE MODEL
# -----------------------

def moneyline_probability(home, away, hp, ap, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp):
    H_OBP_all = clean_percent(get_value(team_hitting, "Teams", home, "Team On-Base %", 0.315))
    H_SLG_all = clean_percent(get_value(team_hitting, "Teams", home, "Team Slugging %", 0.410))
    H_BAT_all = clean_percent(get_value(team_hitting, "Teams", home, "Team Batting Avg.", 0.250))

    A_OBP_all = clean_percent(get_value(team_hitting, "Teams", away, "Team On-Base %", 0.315))
    A_SLG_all = clean_percent(get_value(team_hitting, "Teams", away, "Team Slugging %", 0.410))
    A_BAT_all = clean_percent(get_value(team_hitting, "Teams", away, "Team Batting Avg.", 0.250))

    A_throws = get_value(pitcher_this_year, "Player", ap, "Throws", None)
    if A_throws is None:
        A_throws = get_value(pitcher_last_year, "Player", ap, "Throws", "R")

    H_throws = get_value(pitcher_this_year, "Player", hp, "Throws", None)
    if H_throws is None:
        H_throws = get_value(pitcher_last_year, "Player", hp, "Throws", "R")

    home_split = team_batting_lhp if A_throws == "L" else team_batting_rhp
    away_split = team_batting_lhp if H_throws == "L" else team_batting_rhp

    H_OBP_split = clean_percent(get_value(home_split, "Teams", home, "On-Base %", 0.315))
    H_SLG_split = clean_percent(get_value(home_split, "Teams", home, "Slug %", 0.410))
    H_BAT_split = clean_percent(get_value(home_split, "Teams", home, "Batting Average", 0.250))

    A_OBP_split = clean_percent(get_value(away_split, "Teams", away, "On-Base %", 0.315))
    A_SLG_split = clean_percent(get_value(away_split, "Teams", away, "Slug %", 0.410))
    A_BAT_split = clean_percent(get_value(away_split, "Teams", away, "Batting Average", 0.250))

    H_OBP = 0.65 * H_OBP_all + 0.35 * H_OBP_split
    H_SLG = 0.65 * H_SLG_all + 0.35 * H_SLG_split
    H_BAT = 0.65 * H_BAT_all + 0.35 * H_BAT_split

    A_OBP = 0.65 * A_OBP_all + 0.35 * A_OBP_split
    A_SLG = 0.65 * A_SLG_all + 0.35 * A_SLG_split
    A_BAT = 0.65 * A_BAT_all + 0.35 * A_BAT_split

    H_team = ((H_OBP - 0.315) * 220 + (H_SLG - 0.410) * 160 + (H_BAT - 0.250) * 110)
    A_team = ((A_OBP - 0.315) * 220 + (A_SLG - 0.410) * 160 + (A_BAT - 0.250) * 110)

    H_xw_last = clean_percent(get_value(pitcher_last_year, "Player", hp, "xwOBA", 0))
    H_xw_this = clean_percent(get_value(pitcher_this_year, "Player", hp, "xwOBA", 0))
    A_xw_last = clean_percent(get_value(pitcher_last_year, "Player", ap, "xwOBA", 0))
    A_xw_this = clean_percent(get_value(pitcher_this_year, "Player", ap, "xwOBA", 0))

    H_xwoba = 0.55 * H_xw_last + 0.45 * H_xw_this if H_xw_last > 0 and H_xw_this > 0 else H_xw_this if H_xw_this > 0 else H_xw_last
    A_xwoba = 0.55 * A_xw_last + 0.45 * A_xw_this if A_xw_last > 0 and A_xw_this > 0 else A_xw_this if A_xw_this > 0 else A_xw_last

    H_ip_last = get_value(pitcher_last_year, "Player", hp, "IP", 0)
    H_ip_this = get_value(pitcher_this_year, "Player", hp, "IP", 0)
    A_ip_last = get_value(pitcher_last_year, "Player", ap, "IP", 0)
    A_ip_this = get_value(pitcher_this_year, "Player", ap, "IP", 0)

    H_k_last = get_value(pitcher_last_year, "Player", hp, "SO", 0)
    H_k_this = get_value(pitcher_this_year, "Player", hp, "SO", 0)
    A_k_last = get_value(pitcher_last_year, "Player", ap, "SO", 0)
    A_k_this = get_value(pitcher_this_year, "Player", ap, "SO", 0)

    H_bb_last = clean_percent(get_value(pitcher_last_year, "Player", hp, "BB%", 0))
    H_bb_this = clean_percent(get_value(pitcher_this_year, "Player", hp, "BB%", 0))
    A_bb_last = clean_percent(get_value(pitcher_last_year, "Player", ap, "BB%", 0))
    A_bb_this = clean_percent(get_value(pitcher_this_year, "Player", ap, "BB%", 0))

    H_ip_blend = max(1, H_ip_last * 0.55 + H_ip_this * 0.45)
    A_ip_blend = max(1, A_ip_last * 0.55 + A_ip_this * 0.45)

    H_k_rate = (H_k_last * 0.55 + H_k_this * 0.45) / H_ip_blend
    A_k_rate = (A_k_last * 0.55 + A_k_this * 0.45) / A_ip_blend

    H_bb_rate = (H_bb_last * 0.55 + H_bb_this * 0.45) / H_ip_blend
    A_bb_rate = (A_bb_last * 0.55 + A_bb_this * 0.45) / A_ip_blend

    H_pitch = ((0.32 - H_xwoba) * 280 + H_k_rate * 11 - H_bb_rate * 7)
    A_pitch = ((0.32 - A_xwoba) * 280 + A_k_rate * 11 - A_bb_rate * 7)

    score = (H_team + H_pitch) - (A_team + A_pitch) + 3.5

    home_win_prob = 1 / (1 + math.exp(-score / 42))
    away_win_prob = 1 - home_win_prob

    return home_win_prob, away_win_prob



# -----------------------
# DAILY SLATE DISPLAY HELPERS
# -----------------------

def bet_record_style_from_counts(wins, losses):
    """Green for winning bet-type records, yellow for exactly even, red for losing."""
    try:
        wins = int(wins)
    except Exception:
        wins = 0
    try:
        losses = int(losses)
    except Exception:
        losses = 0

    if wins > losses:
        return "background-color: #d1fae5; color: #065f46; font-weight: bold;"
    elif wins == losses:
        return "background-color: #fef3c7; color: #92400e; font-weight: bold;"
    else:
        return "background-color: #fee2e2; color: #991b1b; font-weight: bold;"


def normalize_bet_type_text(value):
    """Pull the specific tracked bet type out of saved-slate/best-play text."""
    upper = str(value).upper()

    if "PASS" in upper or "NON-EDGE" in upper or "NO LINE" in upper:
        return None

    # Moneyline saved slate cells usually use compact tags.
    if "A MONEYLINE" in upper or "[A]" in upper:
        return "A MONEYLINE"
    if "B MONEYLINE" in upper or "[B]" in upper:
        return "B MONEYLINE"

    # NRFI/YRFI grades.
    if "ELITE NRFI" in upper:
        return "ELITE NRFI"
    if "STRONG NRFI" in upper:
        return "STRONG NRFI"
    if "LEAN NRFI" in upper:
        return "LEAN NRFI"
    if "NRFI" in upper:
        return "NRFI"
    if "YRFI" in upper:
        return "YRFI"

    # Pitcher K grades. Check strongest/lean versions before plain OVER/UNDER.
    if "STRONG OVER" in upper or "(STRONG OVER)" in upper:
        return "STRONG OVER"
    if "STRONG UNDER" in upper or "(STRONG UNDER)" in upper:
        return "STRONG UNDER"
    if "LEAN OVER" in upper or "(LEAN OVER)" in upper:
        return "LEAN OVER"
    if "LEAN UNDER" in upper or "(LEAN UNDER)" in upper:
        return "LEAN UNDER"
    if "OVER" in upper or "(OVER)" in upper:
        return "OVER"
    if "UNDER" in upper or "(UNDER)" in upper:
        return "UNDER"

    return None


def build_bet_type_record_styles(tracker_df):
    """Create a lookup: normalized bet type -> green/yellow/red style from completed results."""
    record_styles = {}

    if tracker_df is None or tracker_df.empty:
        return record_styles

    if "Bet Type" not in tracker_df.columns or "Result" not in tracker_df.columns:
        return record_styles

    completed = tracker_df[tracker_df["Result"].isin(["Win", "Loss", "Push"])].copy()
    if completed.empty:
        return record_styles

    for bet_type in completed["Bet Type"].dropna().unique():
        key = normalize_bet_type_text(bet_type)
        if not key:
            continue

        sub = completed[completed["Bet Type"] == bet_type]
        wins = (sub["Result"] == "Win").sum()
        losses = (sub["Result"] == "Loss").sum()
        record_styles[key] = bet_record_style_from_counts(wins, losses)

    return record_styles


def style_value_cell(value, bet_type_record_styles=None):
    bet_type_record_styles = bet_type_record_styles or {}
    key = normalize_bet_type_text(value)

    if not key:
        return ""

    # Main requested behavior: use that specific bet type's completed record.
    # Winning record = green, exactly even = yellow, losing record = red.
    if key in bet_type_record_styles:
        return bet_type_record_styles[key]

    # No completed record yet for this specific bet type: leave it uncolored.
    return ""


def styled_dataframe(df, bet_type_record_styles=None):
    if df.empty:
        return df

    def style_table(data):
        return pd.DataFrame(
            [[style_value_cell(value, bet_type_record_styles) for value in row] for row in data.to_numpy()],
            index=data.index,
            columns=data.columns
        )

    return df.style.apply(style_table, axis=None)


def display_compact_dataframe(df, bet_type_record_styles=None):
    if df is None or df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
        return

    column_config = {col: st.column_config.TextColumn(col, width="small") for col in df.columns}
    for col in ["Game", "Better ML", "Away Pitcher K + Grade", "Home Pitcher K + Grade", "Play", "Selection", "Market"]:
        if col in column_config:
            column_config[col] = st.column_config.TextColumn(col, width="medium")

    st.dataframe(
        styled_dataframe(df, bet_type_record_styles),
        use_container_width=True,
        hide_index=True,
        column_config=column_config
    )

def compact_slate_view(df):
    if df is None or df.empty:
        return df
    cols = ["Away Team", "Home Team", "Better ML", "NRFI Grade", "Away Pitcher K + Grade", "Home Pitcher K + Grade"]
    return df[[col for col in cols if col in df.columns]].copy()


def k_summary_text(pitcher, projection, grade, line):
    grade_text = str(grade).upper()
    if grade_text == "PASS":
        side = "pass"
    elif "STRONG OVER" in grade_text:
        side = "strong over"
    elif "LEAN OVER" in grade_text:
        side = "lean over"
    elif "OVER" in grade_text:
        side = "over"
    elif "STRONG UNDER" in grade_text:
        side = "strong under"
    elif "LEAN UNDER" in grade_text:
        side = "lean under"
    elif "UNDER" in grade_text:
        side = "under"
    else:
        side = "pass"
    return f"{pitcher} {projection:.2f} ({side}) {line}"


def build_best_plays(today_slate):

    def get_static_score(play_type, play_text=""):
        text = f"{play_type} {play_text}"

        if "ELITE NRFI" in text:
            return 100
        elif "STRONG NRFI" in text:
            return 85
        elif "A Moneyline" in text:
            return 80
        elif "B Moneyline" in text:
            return 65
        elif "YRFI" in text:
            return 60
        else:
            return 0

    def safe_float(value, default=0):
        try:
            if pd.isna(value):
                return default
            return float(value)
        except:
            return default

    best_rows = []

    for _, row in today_slate.iterrows():
        game = f'{row["Away Team"]} at {row["Home Team"]}'

        # Moneyline plays
        if row["ML Grade"] in ["A Moneyline", "B Moneyline"]:
            best_rows.append({
                "Play Type": row["ML Grade"],
                "Game": game,
                "Play": row["Better ML"],
                "Odds/Line": row["ML Odds"],
                "Score": get_static_score(row["ML Grade"])
            })

        # NRFI / YRFI plays
        if row["NRFI Grade"] in ["ELITE NRFI", "STRONG NRFI", "YRFI"]:
            best_rows.append({
                "Play Type": row["NRFI Grade"],
                "Game": game,
                "Play": row["NRFI Grade"],
                "Odds/Line": "",
                "Score": get_static_score(row["NRFI Grade"])
            })

        # Pitcher K plays: use true 0-100 K score saved from the model
        pitcher_k_columns = [
            ("Away Pitcher K + Grade", "Away Pitcher K Score"),
            ("Home Pitcher K + Grade", "Home Pitcher K Score")
        ]

        for play_col, score_col in pitcher_k_columns:
            text = str(row.get(play_col, ""))
            upper_text = text.upper()

            if "PASS" not in upper_text and any(g in upper_text for g in [
                "STRONG OVER", "OVER", "LEAN OVER",
                "LEAN UNDER", "UNDER", "STRONG UNDER"
            ]):
                k_score = safe_float(row.get(score_col, 0), 0)

                # Backward compatibility for old saved rows without a K Score column/value
                if k_score == 0:
                    if "STRONG OVER" in upper_text or "STRONG UNDER" in upper_text:
                        k_score = 90
                    elif "LEAN" in upper_text:
                        k_score = 50
                    elif "OVER" in upper_text or "UNDER" in upper_text:
                        k_score = 70

                best_rows.append({
                    "Play Type": "Pitcher K",
                    "Game": game,
                    "Play": text,
                    "Odds/Line": extract_k_line(text),
                    "Score": k_score
                })

    if not best_rows:
        return pd.DataFrame(columns=["Play Type", "Game", "Play", "Odds/Line", "Score"])

    best_df = pd.DataFrame(best_rows)
    return best_df.sort_values(by="Score", ascending=False).reset_index(drop=True)

def render_daily_slate():
    st.header("Daily Slate")

    slate_df = load_slate()
    tracker_df = load_tracker()
    bet_type_record_styles = build_bet_type_record_styles(tracker_df)
    today = str(date.today())

    today_slate = slate_df[slate_df["Date"] == today].copy()

    st.subheader("Best Plays")

    if today_slate.empty:
        st.info("No saved games today yet.")
    else:
        best_df = build_best_plays(today_slate)
        if best_df.empty:
            st.info("No best plays saved yet.")
        else:
            display_compact_dataframe(best_df, bet_type_record_styles)

    st.divider()

    st.subheader("Today's Saved Games")

    if today_slate.empty:
        st.info("No games saved today yet.")
    else:
        display_compact_dataframe(compact_slate_view(today_slate.drop(columns=["Date"])), bet_type_record_styles)

    st.divider()

    st.subheader("View Saved Slate by Date")

    if slate_df.empty:
        st.info("No saved games yet.")
        return

    available_dates = sorted(slate_df["Date"].dropna().astype(str).unique(), reverse=True)
    default_index = available_dates.index(today) if today in available_dates else 0

    selected_date = st.selectbox("Choose a slate date", available_dates, index=default_index)
    selected_slate = slate_df[slate_df["Date"].astype(str) == selected_date].copy()

    display_compact_dataframe(compact_slate_view(selected_slate.drop(columns=["Date"])), bet_type_record_styles)

    st.divider()

    st.subheader("Delete Saved Games")
    st.caption("Use this if you saved a game by mistake. Check Delete for any row, then click Delete Selected Games.")

    delete_view = selected_slate.copy()
    delete_view.insert(0, "Delete", False)
    delete_view.insert(1, "Row ID", delete_view.index)

    edited_delete_view = st.data_editor(
        delete_view,
        use_container_width=True,
        num_rows="fixed",
        column_config={
            "Delete": st.column_config.CheckboxColumn("Delete"),
            "Row ID": st.column_config.NumberColumn("Row ID", disabled=True)
        },
        disabled=[col for col in delete_view.columns if col != "Delete"],
        key=f"delete_slate_{selected_date}"
    )

    if st.button("Delete Selected Games"):
        rows_to_delete = edited_delete_view.loc[edited_delete_view["Delete"] == True, "Row ID"].tolist()
        if rows_to_delete:
            slate_df = slate_df.drop(index=rows_to_delete)
            save_slate(slate_df)
            st.success("Selected games deleted. Refresh the app if the table does not update immediately.")
        else:
            st.info("No games selected for deletion.")



# -----------------------
# APP
# -----------------------

# NOTE: This first-run live version keeps your existing model's old column names
# so the current formulas do not need to be rewritten yet.

TEAM_ABBR_MAP = {
    "LAA": "Los Angeles Angels", "BAL": "Baltimore Orioles", "BOS": "Boston Red Sox",
    "CHW": "Chicago White Sox", "CLE": "Cleveland Guardians", "DET": "Detroit Tigers",
    "KCR": "Kansas City Royals", "MIN": "Minnesota Twins", "NYY": "New York Yankees",
    "ATH": "Athletics", "OAK": "Athletics", "SEA": "Seattle Mariners", "TBR": "Tampa Bay Rays",
    "TEX": "Texas Rangers", "TOR": "Toronto Blue Jays", "ARI": "Arizona Diamondbacks",
    "ATL": "Atlanta Braves", "CHC": "Chicago Cubs", "CIN": "Cincinnati Reds",
    "COL": "Colorado Rockies", "MIA": "Miami Marlins", "HOU": "Houston Astros",
    "LAD": "Los Angeles Dodgers", "MIL": "Milwaukee Brewers", "WSN": "Washington Nationals",
    "NYM": "New York Mets", "PHI": "Philadelphia Phillies", "PIT": "Pittsburgh Pirates",
    "STL": "St. Louis Cardinals", "SDP": "San Diego Padres", "SFG": "San Francisco Giants"
}

TEAMRANKINGS_TEAM_MAP = {
    "San Diego": "San Diego Padres", "Toronto": "Toronto Blue Jays", "Kansas City": "Kansas City Royals",
    "Arizona": "Arizona Diamondbacks", "NY Mets": "New York Mets", "Houston": "Houston Astros",
    "St. Louis": "St. Louis Cardinals", "Washington": "Washington Nationals", "Chi Cubs": "Chicago Cubs",
    "Philadelphia": "Philadelphia Phillies", "Cleveland": "Cleveland Guardians", "Milwaukee": "Milwaukee Brewers",
    "Sacramento": "Athletics", "Oakland": "Athletics", "Texas": "Texas Rangers", "Miami": "Miami Marlins",
    "Minnesota": "Minnesota Twins", "LA Dodgers": "Los Angeles Dodgers", "Chi Sox": "Chicago White Sox",
    "SF Giants": "San Francisco Giants", "Tampa Bay": "Tampa Bay Rays", "Atlanta": "Atlanta Braves",
    "Pittsburgh": "Pittsburgh Pirates", "Baltimore": "Baltimore Orioles", "NY Yankees": "New York Yankees",
    "Cincinnati": "Cincinnati Reds", "Detroit": "Detroit Tigers", "Seattle": "Seattle Mariners",
    "Boston": "Boston Red Sox", "Colorado": "Colorado Rockies", "LA Angels": "Los Angeles Angels"
}


def safe_read_first_table(url, table_index=0, label="table"):
    """
    Browser-style web table loader for first-run live sources.

    Power Query uses Web.BrowserContents, but plain pandas.read_html(url) can fail
    because some sites block Python's default user agent. This function requests
    the page with browser headers first, then gives the HTML to pandas.
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        from io import StringIO
        tables = pd.read_html(StringIO(response.text))
        if not tables or table_index >= len(tables):
            st.warning(f"Could not load {label}: no HTML tables found.")
            return pd.DataFrame()
        return tables[table_index].copy()
    except ImportError:
        st.error(
            "Python is missing a package needed to read web tables. "
            "Run: pip install lxml html5lib beautifulsoup4"
        )
        return pd.DataFrame()
    except Exception as e:
        st.warning(f"Could not load {label}: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=60 * 60)
def load_team_hitting_stats_live():
    df = safe_read_first_table("https://www.mlb.com/stats/team/batting-average")
    if df.empty:
        return df
    df = df.rename(columns={
        "Team": "Teams", "H": "Hits", "RBI": "RBI's",
        "AVG": "Team Batting Avg.", "OBP": "Team On-Base %", "SLG": "Team Slugging %"
    })
    keep = ["Teams", "Hits", "RBI's", "Team Batting Avg.", "Team On-Base %", "Team Slugging %"]
    df = df[[c for c in keep if c in df.columns]]
    if "Teams" in df.columns:
        df["Teams"] = df["Teams"].astype(str).str.strip()
    for c in df.columns:
        if c != "Teams":
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=60 * 60)
def load_team_batting_split_live(split):
    df = safe_read_first_table(f"https://www.mlb.com/stats/team/batting-average?split={split}")
    if df.empty:
        return df
    df = df.rename(columns={
        "Team": "Teams", "G": "Games", "AB": "At Bats", "H": "Hits", "BB": "Batted Balls",
        "SO": "Strikeouts", "AVG": "Batting Average", "OBP": "On-Base %", "SLG": "Slug %"
    })
    keep = ["Teams", "Games", "At Bats", "Hits", "Batted Balls", "Strikeouts", "Batting Average", "On-Base %", "Slug %"]
    df = df[[c for c in keep if c in df.columns]]
    if "Teams" in df.columns:
        df["Teams"] = df["Teams"].astype(str).str.strip()
    for c in df.columns:
        if c != "Teams":
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=60 * 60)
def load_pitcher_data_live(year):
    url = f"https://baseballsavant.mlb.com/leaderboard/custom?year={year}&type=pitcher&filter=&min=1&selections=p_game%2Cp_formatted_ip%2Cpa%2Cstrikeout%2Ck_percent%2Cbb_percent%2Cp_era%2Cp_foul%2Cxwoba%2Chard_hit_percent%2Cout_zone_percent%2Cpitch_count%2Cin_zone_percent%2Cwhiff_percent%2Cf_strike_percent&chart=false&x=pa&y=pa&r=no&chartType=beeswarm&sort=player_name&sortDir=asc"
    df = safe_read_first_table(url)
    if df.empty:
        return df
    if "Rk." in df.columns:
        df = df.drop(columns=["Rk."])
    keep = ["Player", "Year", "G", "IP", "BF", "SO", "K%", "BB%", "ERA", "Foul", "xwOBA", "Hard Hit %", "Out of Zone %", "Pitches", "In Zone %", "Whiff %", "First Strike %"]
    df = df[[c for c in keep if c in df.columns]]
    if "Player" in df.columns:
        df["Player"] = df["Player"].astype(str).str.strip()
    for c in df.columns:
        if c != "Player":
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=60 * 60)
def load_pitcher_handedness_live(year=2026):
    url = f"https://baseballsavant.mlb.com/leaderboard/custom?year={year}&type=pitcher&filter=&min=1&selections=pitch_hand&chart=false&x=pitch_hand&y=pitch_hand&r=no&chartType=beeswarm&sort=player_name&sortDir=asc"
    df = safe_read_first_table(url)
    if df.empty:
        return df
    if "Rk." in df.columns:
        df = df.drop(columns=["Rk."])
    keep = ["Player", "Throws"]
    df = df[[c for c in keep if c in df.columns]]
    if "Player" in df.columns:
        df["Player"] = df["Player"].astype(str).str.strip()
    if "Throws" in df.columns:
        df["Throws"] = df["Throws"].astype(str).str.strip()
    return df


def attach_pitcher_handedness(pitcher_df, handedness_df):
    if pitcher_df is None or pitcher_df.empty or handedness_df is None or handedness_df.empty:
        return pitcher_df
    if "Throws" in pitcher_df.columns:
        return pitcher_df
    return pitcher_df.merge(handedness_df, on="Player", how="left")


@st.cache_data(ttl=60 * 60)
def load_nrfi_pitchers_live():
    url = "https://www.fangraphs.com/leaders/splits-leaderboards?splitArr=44&splitArrPitch=&autoPt=false&splitTeams=false&statType=player&statgroup=1&startDate=2025-03-01&endDate=2026-11-01&players=&filter=&groupBy=season&wxTemperature=&wxPressure=&wxAirDensity=&wxElevation=&wxWindSpeed=&position=P&sort=1,1&pageitems=2000000000&pg=0"
    df = safe_read_first_table(url)
    if df.empty:
        return df
    df = df.drop(columns=[c for c in ["#", "Tm"] if c in df.columns])
    if "Name" in df.columns:
        def to_last_first(name):
            parts = str(name).strip().split()
            if len(parts) <= 1:
                return str(name).strip()
            return f"{' '.join(parts[1:])}, {parts[0]}"
        df["Player Name"] = df["Name"].apply(to_last_first)
    df = df.rename(columns={"G": "Games", "TBF": "Plate Appearances"})
    df = df.drop(columns=[c for c in ["2B", "3B", "HR", "HBP", "IBB"] if c in df.columns])
    keep = ["Season", "Name", "Player Name", "Games", "Plate Appearances", "ERA", "H", "R", "ER", "BB", "SO", "AVG", "OBP", "SLG", "wOBA"]
    df = df[[c for c in keep if c in df.columns]]
    for c in ["Name", "Player Name"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()
    for c in df.columns:
        if c not in ["Name", "Player Name"]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=60 * 60)
def load_nrfi_team_split_live(hand):
    split_arr = "1,19,20,21,22" if hand == "lhp" else "19,20,21,22,2"
    url = f"https://www.fangraphs.com/leaders/splits-leaderboards?splitArr={split_arr}&splitArrPitch=&autoPt=false&splitTeams=false&statType=team&statgroup=2&startDate=2026-03-01&endDate=2026-11-01&players=&filter=&groupBy=season&wxTemperature=&wxPressure=&wxAirDensity=&wxElevation=&wxWindSpeed=&position=B&sort=23,1"
    df = safe_read_first_table(url)
    if df.empty:
        return df
    if "#" in df.columns:
        df = df.drop(columns=["#"])
    df = df.rename(columns={"Tm": "Teams", "PA": "Plate Appearances"})
    if "Teams" in df.columns:
        df["Teams"] = df["Teams"].replace(TEAM_ABBR_MAP).astype(str).str.strip()
    for c in df.columns:
        if c != "Teams":
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=60 * 60)
def load_team_strikeouts_live():
    df = safe_read_first_table("https://www.teamrankings.com/mlb/stat/strikeouts-per-game")
    if df.empty:
        return df
    if len(df.columns) >= 4:
        df = df.rename(columns={df.columns[1]: "Teams", df.columns[2]: "teamso", df.columns[3]: "teamso3"})
    df = df[[c for c in ["Teams", "teamso", "teamso3"] if c in df.columns]]
    if "Teams" in df.columns:
        df["Teams"] = df["Teams"].replace(TEAMRANKINGS_TEAM_MAP).astype(str).str.strip()
    for c in ["teamso", "teamso3"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=60 * 60)
def load_bullpen_stats_live():
    url = "https://www.fangraphs.com/leaders/major-league?pos=all&lg=all&qual=0&type=8&season=2026&month=0&season1=2026&ind=0&team=0%2Cts&rost=0&age=0&filter=&players=0&stats=rel"
    df = safe_read_first_table(url)
    if df.empty:
        return df
    df = df.rename(columns={"Team": "Teams", "ERAERA - Earned Run Average ((ER*9)/IP)": "era"})
    if "ERA" in df.columns and "era" not in df.columns:
        df = df.rename(columns={"ERA": "era"})
    keep = ["Teams", "era"]
    df = df[[c for c in keep if c in df.columns]]
    if "Teams" in df.columns:
        df["Teams"] = df["Teams"].replace(TEAM_ABBR_MAP).replace({"Pitsburgh Pirates": "Pittsburgh Pirates"}).astype(str).str.strip()
    if "era" in df.columns:
        df["era"] = pd.to_numeric(df["era"], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=15 * 60)
def pull_today_mlb_games(game_date=None):
    if game_date is None:
        game_date = date.today().strftime("%Y-%m-%d")
    try:
        response = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId": 1, "date": game_date, "hydrate": "probablePitcher"},
            timeout=20
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        st.warning(f"Could not pull MLB schedule: {e}")
        return pd.DataFrame()
    games = []
    for day in data.get("dates", []):
        for game in day.get("games", []):
            teams = game.get("teams", {})
            away = teams.get("away", {})
            home = teams.get("home", {})
            games.append({
                "game_pk": game.get("gamePk"),
                "game_time": game.get("gameDate"),
                "away_team": away.get("team", {}).get("name", ""),
                "home_team": home.get("team", {}).get("name", ""),
                # MLB API returns probable pitchers as First Last; your Excel tables use Last, First.
                "away_pitcher": to_last_first(away.get("probablePitcher", {}).get("fullName", "")),
                "home_pitcher": to_last_first(home.get("probablePitcher", {}).get("fullName", "")),
                "status": game.get("status", {}).get("detailedState", "")
            })
    return pd.DataFrame(games)


@st.cache_data(ttl=5 * 60)
def pull_mlb_moneyline_odds(api_key):
    if not api_key:
        return pd.DataFrame()
    try:
        response = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds",
            params={"apiKey": api_key, "regions": "us", "markets": "h2h", "oddsFormat": "american"},
            timeout=20
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        st.warning(f"Could not pull moneyline odds: {e}")
        return pd.DataFrame()
    rows = []
    for game in data:
        home_team = game.get("home_team", "")
        away_team = game.get("away_team", "")
        if home_team == "Oakland Athletics": home_team = "Athletics"
        if away_team == "Oakland Athletics": away_team = "Athletics"
        best_home_odds, best_away_odds = None, None
        best_home_book, best_away_book = "", ""
        for book in game.get("bookmakers", []):
            book_name = book.get("title", "")
            for market in book.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    name = outcome.get("name", "")
                    if name == "Oakland Athletics": name = "Athletics"
                    price = outcome.get("price", None)
                    if price is None:
                        continue
                    if name == home_team and (best_home_odds is None or price > best_home_odds):
                        best_home_odds, best_home_book = price, book_name
                    if name == away_team and (best_away_odds is None or price > best_away_odds):
                        best_away_odds, best_away_book = price, book_name
        rows.append({
            "odds_event_id": game.get("id"), "home_team": home_team, "away_team": away_team,
            "home_ml_odds": best_home_odds, "home_book": best_home_book,
            "away_ml_odds": best_away_odds, "away_book": best_away_book
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=5 * 60)
def pull_pitcher_k_props_for_event(api_key, event_id):
    if not api_key or not event_id:
        return pd.DataFrame()
    try:
        response = requests.get(
            f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds",
            params={"apiKey": api_key, "regions": "us", "markets": "pitcher_strikeouts", "oddsFormat": "american"},
            timeout=20
        )
        if response.status_code != 200:
            return pd.DataFrame()
        data = response.json()
    except Exception:
        return pd.DataFrame()
    rows = []
    for book in data.get("bookmakers", []):
        book_name = book.get("title", "")
        for market in book.get("markets", []):
            if market.get("key") != "pitcher_strikeouts":
                continue
            for outcome in market.get("outcomes", []):
                rows.append({
                    "odds_event_id": event_id,
                    "book": book_name,
                    "player": outcome.get("description", ""),
                    "side": outcome.get("name", ""),
                    "line": outcome.get("point", None),
                    "odds": outcome.get("price", None)
                })
    return pd.DataFrame(rows)


def pull_today_pitcher_k_props(api_key, odds_games_df):
    if odds_games_df is None or odds_games_df.empty:
        return pd.DataFrame(columns=["odds_event_id", "book", "player", "side", "line", "odds"])
    all_props = []
    for _, row in odds_games_df.iterrows():
        props = pull_pitcher_k_props_for_event(api_key, row.get("odds_event_id"))
        if not props.empty:
            props["home_team"] = row.get("home_team", "")
            props["away_team"] = row.get("away_team", "")
            all_props.append(props)
    if not all_props:
        return pd.DataFrame(columns=["odds_event_id", "home_team", "away_team", "book", "player", "side", "line", "odds"])
    return pd.concat(all_props, ignore_index=True)


def summarize_pitcher_k_market(k_props_df):
    if k_props_df is None or k_props_df.empty:
        return pd.DataFrame(columns=["odds_event_id", "player", "k_line", "best_over_odds", "best_over_book", "best_under_odds", "best_under_book"])
    grouped = []
    k_props_df = k_props_df.copy()
    k_props_df["line"] = pd.to_numeric(k_props_df["line"], errors="coerce")
    k_props_df["odds"] = pd.to_numeric(k_props_df["odds"], errors="coerce")
    for (event_id, player, line), sub in k_props_df.dropna(subset=["line"]).groupby(["odds_event_id", "player", "line"]):
        over_rows = sub[sub["side"].astype(str).str.lower() == "over"]
        under_rows = sub[sub["side"].astype(str).str.lower() == "under"]
        best_over = over_rows.sort_values("odds", ascending=False).head(1)
        best_under = under_rows.sort_values("odds", ascending=False).head(1)
        grouped.append({
            "odds_event_id": event_id, "player": player, "k_line": line,
            "best_over_odds": None if best_over.empty else best_over.iloc[0]["odds"],
            "best_over_book": "" if best_over.empty else best_over.iloc[0]["book"],
            "best_under_odds": None if best_under.empty else best_under.iloc[0]["odds"],
            "best_under_book": "" if best_under.empty else best_under.iloc[0]["book"],
        })
    return pd.DataFrame(grouped)


def run_today_model_for_games(today_games, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, nrfi_pitchers, nrfi_rhp, nrfi_lhp):
    rows = []
    if today_games is None or today_games.empty:
        return pd.DataFrame()
    for _, game in today_games.iterrows():
        away_team, home_team = game.get("away_team", ""), game.get("home_team", "")
        away_pitcher, home_pitcher = game.get("away_pitcher", ""), game.get("home_pitcher", "")
        if not away_pitcher or not home_pitcher:
            rows.append({"Away Team": away_team, "Home Team": home_team, "Away Pitcher": away_pitcher or "TBD", "Home Pitcher": home_pitcher or "TBD", "Status": "Missing probable pitcher"})
            continue
        home_k = expected_strikeouts(home_pitcher, away_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp)
        away_k = expected_strikeouts(away_pitcher, home_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp)
        home_k_6ip = six_inning_strikeouts(home_pitcher, away_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp)
        away_k_6ip = six_inning_strikeouts(away_pitcher, home_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp)
        nrfi_prob = nrfi_probability(home_team, away_team, home_pitcher, away_pitcher, pitcher_this_year, pitcher_last_year, nrfi_pitchers, nrfi_rhp, nrfi_lhp)
        nrfi_score = nrfi_score_formula(nrfi_prob)
        nrfi_grade = nrfi_bet_grade(nrfi_score)
        home_win_prob, away_win_prob = moneyline_probability(home_team, away_team, home_pitcher, away_pitcher, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp)
        home_ml_odds = game.get("home_ml_odds", None)
        away_ml_odds = game.get("away_ml_odds", None)
        home_implied = american_odds_to_implied_prob(home_ml_odds)
        away_implied = american_odds_to_implied_prob(away_ml_odds)
        home_ml_edge = home_win_prob - home_implied
        away_ml_edge = away_win_prob - away_implied
        rows.append({
            "Away Team": away_team, "Home Team": home_team, "Away Pitcher": away_pitcher, "Home Pitcher": home_pitcher,
            "Away Expected K": round(away_k, 2), "Away 6-IP K": round(away_k_6ip, 2),
            "Home Expected K": round(home_k, 2), "Home 6-IP K": round(home_k_6ip, 2),
            "NRFI %": round(nrfi_prob * 100, 1), "NRFI Score": round(nrfi_score, 1), "NRFI Grade": nrfi_grade,
            "Home Win %": round(home_win_prob * 100, 1), "Away Win %": round(away_win_prob * 100, 1),
            "Home ML Odds": home_ml_odds, "Home Book": game.get("home_book", ""), "Home Implied %": round(home_implied * 100, 1), "Home ML Edge %": round(home_ml_edge * 100, 1), "Home ML Grade": moneyline_grade(home_ml_edge),
            "Away ML Odds": away_ml_odds, "Away Book": game.get("away_book", ""), "Away Implied %": round(away_implied * 100, 1), "Away ML Edge %": round(away_ml_edge * 100, 1), "Away ML Grade": moneyline_grade(away_ml_edge),
            "Status": game.get("status", "")
        })
    return pd.DataFrame(rows)


def build_auto_pitcher_k_board(today_games, k_market, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp):
    rows = []
    if today_games is None or today_games.empty:
        return pd.DataFrame()
    if k_market is None:
        k_market = pd.DataFrame()
    for _, game in today_games.iterrows():
        game_label = f'{game.get("away_team", "")} at {game.get("home_team", "")}'
        pitchers = [
            {"pitcher": game.get("away_pitcher", ""), "team": game.get("away_team", ""), "opponent": game.get("home_team", "")},
            {"pitcher": game.get("home_pitcher", ""), "team": game.get("home_team", ""), "opponent": game.get("away_team", "")},
        ]
        for p in pitchers:
            pitcher = p["pitcher"]
            if not pitcher or pitcher == "TBD":
                continue
            market_rows = pd.DataFrame()
            if not k_market.empty and "player" in k_market.columns:
                # Odds API player props usually use First Last, while model pitchers are Last, First.
                pitcher_for_odds = normalize_name_for_match(to_first_last(pitcher))
                market_rows = k_market[
                    k_market["player"].astype(str).apply(lambda x: normalize_name_for_match(x)) == pitcher_for_odds
                ]
            if market_rows.empty:
                rows.append({"Game": game_label, "Pitcher": pitcher, "Team": p["team"], "Opponent": p["opponent"], "Projection": "", "Line": "No K line found", "Edge": "", "Recommendation": "NO LINE", "Best Odds": "", "Best Book": "", "K Score": 0})
                continue
            exp_k = expected_strikeouts(pitcher, p["opponent"], pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp)
            six_k = six_inning_strikeouts(pitcher, p["opponent"], pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp)
            ipg_this, ipg_last = pitcher_ipg(pitcher, pitcher_this_year, pitcher_last_year)
            volatility = strikeout_volatility(pitcher, pitcher_this_year, pitcher_last_year)
            best_market, best_abs_edge = None, -999
            for _, m in market_rows.iterrows():
                line = pd.to_numeric(m.get("k_line"), errors="coerce")
                if pd.isna(line):
                    continue
                abs_edge = abs(exp_k - line)
                if abs_edge > best_abs_edge:
                    best_abs_edge = abs_edge
                    best_market = m
            if best_market is None:
                continue
            line = float(best_market["k_line"])
            grade, edge = strikeout_bet_grade(exp_k, six_k, ipg_this, ipg_last, line, volatility)
            k_score = pitcher_k_strength_score(exp_k, six_k, line, volatility, ipg_this, ipg_last)
            if edge >= 0:
                best_odds, best_book, bet_side = best_market.get("best_over_odds"), best_market.get("best_over_book"), "Over"
            else:
                best_odds, best_book, bet_side = best_market.get("best_under_odds"), best_market.get("best_under_book"), "Under"
            rows.append({
                "Game": game_label, "Pitcher": pitcher, "Team": p["team"], "Opponent": p["opponent"],
                "Projection": round(exp_k, 2), "6-IP Projection": round(six_k, 2), "Line": line, "Edge": round(edge, 2),
                "Bet Side": bet_side, "Recommendation": grade, "Best Odds": best_odds, "Best Book": best_book,
                "Volatility": volatility, "K Score": k_score
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("K Score", ascending=False).reset_index(drop=True)


def load_all_live_data():
    team_hitting = load_team_hitting_stats_live()
    team_batting_rhp = load_team_batting_split_live("vr")
    team_batting_lhp = load_team_batting_split_live("vl")
    pitcher_this_year = load_pitcher_data_live(2026)
    pitcher_last_year = load_pitcher_data_live(2025)
    pitcher_handedness = load_pitcher_handedness_live(2026)
    pitcher_this_year = attach_pitcher_handedness(pitcher_this_year, pitcher_handedness)
    pitcher_last_year = attach_pitcher_handedness(pitcher_last_year, pitcher_handedness)
    return {
        "team_hitting": team_hitting,
        "team_batting_rhp": team_batting_rhp,
        "team_batting_lhp": team_batting_lhp,
        "pitcher_this_year": pitcher_this_year,
        "pitcher_last_year": pitcher_last_year,
        "pitcher_handedness": pitcher_handedness,
        "nrfi_pitchers": load_nrfi_pitchers_live(),
        "nrfi_rhp": load_nrfi_team_split_live("rhp"),
        "nrfi_lhp": load_nrfi_team_split_live("lhp"),
        "bullpen_stats": load_bullpen_stats_live(),
        "team_strikeouts": load_team_strikeouts_live(),
    }


def render_auto_matchup_builder(pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, nrfi_pitchers, nrfi_rhp, nrfi_lhp):
    st.header("Daily Slate (Auto Live)")
    selected_date = st.date_input("Slate Date", value=date.today(), key="auto_slate_date")
    today_games = pull_today_mlb_games(selected_date.strftime("%Y-%m-%d"))
    if today_games.empty:
        st.info("No MLB games found for this date or schedule source did not load.")
        return
    odds_games = pull_mlb_moneyline_odds(odds_api_key) if odds_api_key else pd.DataFrame()
    if not odds_games.empty:
        today_games = today_games.merge(odds_games, on=["home_team", "away_team"], how="left")
    auto_model = run_today_model_for_games(today_games, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, nrfi_pitchers, nrfi_rhp, nrfi_lhp)
    st.subheader("Game Model Outputs")
    st.dataframe(styled_dataframe(auto_model), use_container_width=True)
    if odds_api_key and not odds_games.empty:
        k_props_raw = pull_today_pitcher_k_props(odds_api_key, odds_games)
        k_market = summarize_pitcher_k_market(k_props_raw)
        pitcher_k_board = build_auto_pitcher_k_board(today_games, k_market, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp)
        st.subheader("Auto Pitcher K Betting Board")
        st.dataframe(styled_dataframe(pitcher_k_board), use_container_width=True)
        if not pitcher_k_board.empty:
            best_k_plays = pitcher_k_board[pitcher_k_board["Recommendation"].isin(["STRONG OVER", "OVER", "LEAN OVER", "LEAN UNDER", "UNDER", "STRONG UNDER"])].copy()
            best_k_plays = best_k_plays.sort_values("K Score", ascending=False)
            st.subheader("Best Pitcher K Plays")
            st.dataframe(styled_dataframe(best_k_plays), use_container_width=True)
        with st.expander("Raw Pitcher K Market"):
            st.dataframe(k_market, use_container_width=True)
    else:
        st.info("Enter an Odds API key in the sidebar to load moneyline odds and pitcher K props.")



def render_auto_matchup_builder(pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, nrfi_pitchers, nrfi_rhp, nrfi_lhp):
    st.header("Auto Matchup Builder")
    st.caption("Teams and probable pitchers come from the MLB schedule. You manually enter odds and strikeout lines.")

    selected_date = st.date_input("Slate Date", value=date.today(), key="manual_auto_slate_date")
    today_games = pull_today_mlb_games(selected_date.strftime("%Y-%m-%d"))

    if today_games.empty:
        st.info("No MLB games found for this date, or the MLB schedule did not load.")
        return

    today_games = today_games.copy()
    today_games["Game"] = today_games["away_team"] + " at " + today_games["home_team"]

    # Remove games already saved for the selected date from the matchup dropdown.
    slate_df = load_slate()
    slate_date = selected_date.strftime("%Y-%m-%d")
    saved_games = set()
    if not slate_df.empty and "Date" in slate_df.columns:
        saved_for_date = slate_df[slate_df["Date"].astype(str) == slate_date].copy()
        if not saved_for_date.empty:
            saved_games = set((saved_for_date["Away Team"].astype(str) + " at " + saved_for_date["Home Team"].astype(str)).tolist())

    unsaved_games = today_games[~today_games["Game"].isin(saved_games)].copy()

    with st.expander("Today's MLB Schedule / Probable Pitchers", expanded=True):
        st.dataframe(
            today_games[["game_time", "away_team", "home_team", "away_pitcher", "home_pitcher", "status"]],
            use_container_width=True
        )

    game_options = unsaved_games["Game"].tolist()
    if not game_options:
        st.success("All games for this slate date have already been saved.")
        return

    selected_game = st.selectbox("Choose Game", game_options)
    game = unsaved_games[unsaved_games["Game"] == selected_game].iloc[0]

    away_team = game["away_team"]
    home_team = game["home_team"]
    away_pitcher = game["away_pitcher"] or "TBD"
    home_pitcher = game["home_pitcher"] or "TBD"

    st.divider()
    st.subheader("Selected Matchup")

    col_a, col_h = st.columns(2)
    with col_a:
        st.markdown(f"### Away: {away_team}")
        st.write(f"**Pitcher:** {away_pitcher}")
    with col_h:
        st.markdown(f"### Home: {home_team}")
        st.write(f"**Pitcher:** {home_pitcher}")

    if away_pitcher == "TBD" or home_pitcher == "TBD":
        st.warning("One or both probable pitchers are TBD. You can still view the game, but model projections need both pitchers.")
        return

    st.divider()
    st.subheader("Manual Market Inputs")

    input_col1, input_col2 = st.columns(2)
    with input_col1:
        home_k_line = st.number_input(f"{home_pitcher} K Line", value=4.5, step=0.5, key=f"home_k_{game.get('game_pk')}")
        home_k_odds = st.number_input(f"{home_pitcher} K Odds", value=-110, step=5, key=f"home_k_odds_{game.get('game_pk')}")
        home_ml_odds = st.number_input(f"{home_team} Moneyline Odds", value=-110, step=5, key=f"home_ml_{game.get('game_pk')}")
    with input_col2:
        away_k_line = st.number_input(f"{away_pitcher} K Line", value=4.5, step=0.5, key=f"away_k_{game.get('game_pk')}")
        away_k_odds = st.number_input(f"{away_pitcher} K Odds", value=-110, step=5, key=f"away_k_odds_{game.get('game_pk')}")
        away_ml_odds = st.number_input(f"{away_team} Moneyline Odds", value=-110, step=5, key=f"away_ml_{game.get('game_pk')}")

    nrfi_odds = st.number_input("NRFI/YRFI Odds", value=-110, step=5, key=f"nrfi_odds_{game.get('game_pk')}")
    st.caption("Pitcher K odds and NRFI/YRFI odds are saved in Bet Tracker for ROI/Units calculations, but they do not show on Daily Slate.")

    home_k = expected_strikeouts(home_pitcher, away_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp)
    away_k = expected_strikeouts(away_pitcher, home_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp)

    home_k_6ip = six_inning_strikeouts(home_pitcher, away_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp)
    away_k_6ip = six_inning_strikeouts(away_pitcher, home_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp)

    home_vol = strikeout_volatility(home_pitcher, pitcher_this_year, pitcher_last_year)
    away_vol = strikeout_volatility(away_pitcher, pitcher_this_year, pitcher_last_year)

    home_ipg_this, home_ipg_last = pitcher_ipg(home_pitcher, pitcher_this_year, pitcher_last_year)
    away_ipg_this, away_ipg_last = pitcher_ipg(away_pitcher, pitcher_this_year, pitcher_last_year)

    home_k_grade, home_k_edge = strikeout_bet_grade(home_k, home_k_6ip, home_ipg_this, home_ipg_last, home_k_line, home_vol)
    away_k_grade, away_k_edge = strikeout_bet_grade(away_k, away_k_6ip, away_ipg_this, away_ipg_last, away_k_line, away_vol)

    home_k_score = pitcher_k_strength_score(home_k, home_k_6ip, home_k_line, home_vol, home_ipg_this, home_ipg_last)
    away_k_score = pitcher_k_strength_score(away_k, away_k_6ip, away_k_line, away_vol, away_ipg_this, away_ipg_last)

    nrfi_prob = nrfi_probability(home_team, away_team, home_pitcher, away_pitcher, pitcher_this_year, pitcher_last_year, nrfi_pitchers, nrfi_rhp, nrfi_lhp)
    nrfi_score = nrfi_score_formula(nrfi_prob)
    nrfi_grade = nrfi_bet_grade(nrfi_score)

    home_win_prob, away_win_prob = moneyline_probability(
        home_team,
        away_team,
        home_pitcher,
        away_pitcher,
        pitcher_this_year,
        pitcher_last_year,
        team_hitting,
        team_batting_rhp,
        team_batting_lhp
    )

    home_implied = american_odds_to_implied_prob(home_ml_odds)
    away_implied = american_odds_to_implied_prob(away_ml_odds)
    home_ml_edge = home_win_prob - home_implied
    away_ml_edge = away_win_prob - away_implied
    home_ml_grade = moneyline_grade(home_ml_edge)
    away_ml_grade = moneyline_grade(away_ml_edge)

    if home_win_prob >= away_win_prob:
        better_ml_team, better_ml_prob, better_ml_odds, better_ml_grade = home_team, home_win_prob, home_ml_odds, home_ml_grade
    else:
        better_ml_team, better_ml_prob, better_ml_odds, better_ml_grade = away_team, away_win_prob, away_ml_odds, away_ml_grade

    st.divider()
    st.subheader("Strikeout Projections")
    col3, col4 = st.columns(2)
    with col3:
        st.markdown(f"### {home_pitcher}")
        st.metric("Expected K", round(home_k, 2))
        st.metric("6-Inning K", round(home_k_6ip, 2))
        st.metric("Line", home_k_line)
        st.metric("Edge", round(home_k_edge, 2))
        st.metric("Volatility", home_vol)
        st.metric("Bet Grade", home_k_grade)
        st.metric("K Score", home_k_score)
    with col4:
        st.markdown(f"### {away_pitcher}")
        st.metric("Expected K", round(away_k, 2))
        st.metric("6-Inning K", round(away_k_6ip, 2))
        st.metric("Line", away_k_line)
        st.metric("Edge", round(away_k_edge, 2))
        st.metric("Volatility", away_vol)
        st.metric("Bet Grade", away_k_grade)
        st.metric("K Score", away_k_score)

    st.divider()
    st.subheader("NRFI Projection")
    col5, col6, col7 = st.columns(3)
    with col5:
        st.metric("NRFI %", f"{nrfi_prob * 100:.1f}%")
    with col6:
        st.metric("Score", round(nrfi_score, 1))
    with col7:
        st.metric("Bet Grade", nrfi_grade)

    st.divider()
    st.subheader("Moneyline Projection")
    col8, col9 = st.columns(2)
    with col8:
        st.markdown(f"### {home_team}")
        st.metric("Model Win %", f"{home_win_prob * 100:.1f}%")
        st.metric("Manual Implied %", f"{home_implied * 100:.1f}%")
        st.metric("Edge", f"{home_ml_edge * 100:.1f}%")
        st.metric("Grade", home_ml_grade)
    with col9:
        st.markdown(f"### {away_team}")
        st.metric("Model Win %", f"{away_win_prob * 100:.1f}%")
        st.metric("Manual Implied %", f"{away_implied * 100:.1f}%")
        st.metric("Edge", f"{away_ml_edge * 100:.1f}%")
        st.metric("Grade", away_ml_grade)

    st.divider()
    st.subheader("Save")
    if st.button("Save Matchup Summary", key=f"save_auto_{game.get('game_pk')}"):
        ml_tag = ""
        if better_ml_grade == "A Moneyline":
            ml_tag = " [A]"
        elif better_ml_grade == "B Moneyline":
            ml_tag = " [B]"
        better_ml_text = f"{better_ml_team} ({better_ml_prob * 100:.1f}%){ml_tag}"
        away_k_summary = k_summary_text(away_pitcher, away_k, away_k_grade, away_k_line)
        home_k_summary = k_summary_text(home_pitcher, home_k, home_k_grade, home_k_line)

        add_slate_row(away_team, home_team, better_ml_text, better_ml_odds, better_ml_grade, nrfi_grade, away_k_summary, away_k_score, home_k_summary, home_k_score)

        # Only add the higher model probability moneyline side to the Bet Tracker.
        # This prevents both teams from showing in Pending Bets.
        if better_ml_prob > 0.50:
            better_implied = home_implied if better_ml_team == home_team else away_implied
            better_edge = home_ml_edge if better_ml_team == home_team else away_ml_edge
            add_bet(
                better_ml_grade,
                better_ml_team,
                "Moneyline",
                better_ml_odds,
                f"{better_ml_prob * 100:.1f}%",
                f"{better_implied * 100:.1f}%",
                f"{better_edge * 100:.1f}%"
            )

        if nrfi_grade in ["ELITE NRFI", "STRONG NRFI", "YRFI"]:
            add_bet(
                nrfi_grade,
                f"{away_team} at {home_team}",
                "NRFI/YRFI",
                nrfi_odds,
                f"{nrfi_prob * 100:.1f}%",
                "",
                f"Score {nrfi_score:.1f}"
            )
        if home_k_grade != "PASS":
            add_bet(
                home_k_grade,
                f"{home_pitcher} {home_k_grade}",
                "Pitcher Strikeouts",
                f"{home_k_line} / {home_k_odds}",
                f"{home_k:.2f}",
                "",
                f"{home_k_edge:.2f}"
            )
        if away_k_grade != "PASS":
            add_bet(
                away_k_grade,
                f"{away_pitcher} {away_k_grade}",
                "Pitcher Strikeouts",
                f"{away_k_line} / {away_k_odds}",
                f"{away_k:.2f}",
                "",
                f"{away_k_edge:.2f}"
            )

        st.success("Matchup summary saved. Qualifying bets were added to Bet Tracker.")
        st.rerun()

st.sidebar.header("Data Source")
st.sidebar.caption(
    "Hybrid mode uses your Excel workbook for model stats, then pulls today's MLB schedule and probable pitchers live. Odds and K lines are manual for now."
)

st.info(
    "Hybrid mode: upload your current Excel workbook. "
    "The app will use your Excel sheets for stats, then automatically pull today's games and probable pitchers. "
    "You will manually enter moneyline odds and strikeout lines."
)

uploaded_file = st.file_uploader("Upload your current MLB Excel file", type=["xlsx"], key="hybrid_upload")

tabs = st.tabs(["Auto Matchup Builder", "Saved Slate", "Bet Tracker", "Data Preview"])

if uploaded_file:
    sheets = pd.read_excel(uploaded_file, sheet_name=None)

    team_hitting = sheets.get("Team Hitting Stats")
    team_batting_rhp = sheets.get("Team Batting RHP")
    team_batting_lhp = sheets.get("Team Batting LHP")
    pitcher_this_year = sheets.get("Pitcher Data This Year (2026)")
    pitcher_last_year = sheets.get("Pitcher Data Last Year (2025)")
    nrfi_pitchers = sheets.get("NRFI Pitchers")
    nrfi_rhp = sheets.get("NRFI RHP")
    nrfi_lhp = sheets.get("NRFI LHP")

    with tabs[0]:
        required = {
            "Team Hitting Stats": team_hitting,
            "Team Batting RHP": team_batting_rhp,
            "Team Batting LHP": team_batting_lhp,
            "Pitcher Data This Year (2026)": pitcher_this_year,
            "Pitcher Data Last Year (2025)": pitcher_last_year,
            "NRFI Pitchers": nrfi_pitchers,
            "NRFI RHP": nrfi_rhp,
            "NRFI LHP": nrfi_lhp,
        }
        missing = [name for name, df in required.items() if df is None or df.empty]
        if missing:
            st.error("Missing required sheets: " + ", ".join(missing))
        else:
            render_auto_matchup_builder(
                pitcher_this_year,
                pitcher_last_year,
                team_hitting,
                team_batting_rhp,
                team_batting_lhp,
                nrfi_pitchers,
                nrfi_rhp,
                nrfi_lhp
            )

    with tabs[1]:
        render_daily_slate()

    with tabs[2]:
        render_bet_tracker()

    with tabs[3]:
        st.header("Data Preview")
        sheet_name = st.selectbox("Choose a sheet to preview", list(sheets.keys()))
        st.dataframe(sheets[sheet_name], use_container_width=True)

else:
    with tabs[0]:
        st.warning("Upload your current MLB Excel workbook to run the auto matchup builder.")
    with tabs[1]:
        render_daily_slate()
    with tabs[2]:
        render_bet_tracker()
    with tabs[3]:
        st.info("Upload your workbook to preview sheets.")