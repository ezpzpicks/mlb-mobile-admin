import streamlit as st
import pandas as pd
import math
import os
import requests
import json
import html
import hashlib
import hmac
import re
import statistics
import urllib.parse
import xml.etree.ElementTree as ET
import gspread
from google.oauth2.service_account import Credentials
from datetime import date, datetime
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

MODEL_VERSION = "v15.1-k-data-health-repair-2026-07-17"
TRACKER_TAB = "bet_tracker"
SLATE_TAB = "daily_slate"
ODDS_SNAPSHOT_TAB = "odds_snapshot"
MATCHUP_DETAILS_TAB = "matchup_details_today"
GAME_PROJECTION_HISTORY_TAB = "game_projection_history"
MODEL_CHANGE_LOG_TAB = "model_change_log"

LOGO_FILE = "ezpz_logo.png"
PAGE_ICON = LOGO_FILE if os.path.exists(LOGO_FILE) else None

# -----------------------
# MOBILE ADMIN PASSWORD
# -----------------------

ADMIN_AUTH_QUERY_KEY = "ezpz_admin_auth"


def _get_admin_password():
    """Read the admin password from Streamlit secrets or Render environment variables."""
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

    return str(admin_password)


def _admin_auth_token(admin_password):
    """Create a stable signed token from the password.

    This lets the mobile admin stay logged in after Chrome/Android reloads
    the Streamlit session while you switch between the betting app and admin app.
    """
    secret_seed = os.environ.get("ADMIN_COOKIE_SECRET", "")
    if not secret_seed:
        try:
            secret_seed = st.secrets.get("ADMIN_COOKIE_SECRET", "")
        except Exception:
            secret_seed = ""

    # If you do not set ADMIN_COOKIE_SECRET, the password itself is still enough
    # to generate a private token. Setting ADMIN_COOKIE_SECRET later will simply
    # require one fresh login.
    base = f"{admin_password}|{secret_seed}|ezpz-mobile-admin-v1"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _get_query_param(name):
    try:
        value = st.query_params.get(name, "")
    except Exception:
        try:
            params = st.experimental_get_query_params()
            value = params.get(name, [""])
        except Exception:
            value = ""

    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _set_query_param(name, value):
    try:
        st.query_params[name] = value
    except Exception:
        try:
            st.experimental_set_query_params(**{name: value})
        except Exception:
            pass


def _clear_query_param(name):
    try:
        if name in st.query_params:
            del st.query_params[name]
    except Exception:
        try:
            params = st.experimental_get_query_params()
            params.pop(name, None)
            st.experimental_set_query_params(**params)
        except Exception:
            pass


def require_admin_password():
    """Password gate with persistent mobile login.

    The old version only used st.session_state, so Android/Chrome could forget
    the login whenever the Streamlit session was recreated in the background.
    This version also stores a signed token in the URL query params so the
    home-screen Chrome app can automatically re-authenticate after reloads.
    """
    admin_password = _get_admin_password()
    expected_token = _admin_auth_token(admin_password)
    existing_token = _get_query_param(ADMIN_AUTH_QUERY_KEY)

    # Normal Streamlit session auth.
    if st.session_state.get("admin_authenticated"):
        # Keep the persistent token in the URL in case Android recreates the session.
        if existing_token != expected_token:
            _set_query_param(ADMIN_AUTH_QUERY_KEY, expected_token)
        return True

    # Persistent mobile auth after a browser/app-switch reload.
    if existing_token and hmac.compare_digest(existing_token, expected_token):
        st.session_state["admin_authenticated"] = True
        return True

    if os.path.exists(LOGO_FILE):
        st.image(LOGO_FILE, width=160)
    st.title("MLB Model Mobile Admin")
    st.caption("Private editor for building matchups, saving plays, and updating results from your phone.")
    st.caption("Mobile login persistence is enabled, so switching apps should no longer kick you back here as often.")
    entered_password = st.text_input("Admin password", type="password")

    if st.button("Log in"):
        if entered_password == admin_password:
            st.session_state["admin_authenticated"] = True
            _set_query_param(ADMIN_AUTH_QUERY_KEY, expected_token)
            st.rerun()
        else:
            _clear_query_param(ADMIN_AUTH_QUERY_KEY)
            st.error("Incorrect password.")

    st.stop()


# Authentication and the sport header are provided by ezpz_admin.py.

st.markdown(
    """
    <style>
    /* Mobile-friendly spacing and table behavior */
    .block-container {padding-top: 1rem; padding-left: 0.8rem; padding-right: 0.8rem; max-width: 1100px;}

    /* Compact mobile metric cards: smaller, wrapped text so long values stay readable */
    div[data-testid="stMetric"] {
        background: #111827 !important;
        border: 1px solid #374151 !important;
        padding: 0.60rem 0.70rem !important;
        border-radius: 0.80rem !important;
        box-shadow: 0 1px 2px rgba(0,0,0,0.22);
        min-height: unset !important;
        gap: 0.20rem !important;
    }
    div[data-testid="stMetric"] * {
        color: #f9fafb !important;
    }
    div[data-testid="stMetricLabel"] {
        margin-bottom: 0.15rem !important;
    }
    div[data-testid="stMetricLabel"] p {
        color: #d1d5db !important;
        font-weight: 600 !important;
        font-size: 0.83rem !important;
        line-height: 1.08 !important;
        white-space: normal !important;
        overflow-wrap: anywhere !important;
        margin: 0 !important;
    }
    div[data-testid="stMetricValue"] {
        color: #ffffff !important;
        font-weight: 800 !important;
        font-size: clamp(1.35rem, 4.8vw, 2.2rem) !important;
        line-height: 1.03 !important;
        white-space: normal !important;
        overflow-wrap: anywhere !important;
        word-break: break-word !important;
    }
    div[data-testid="stMetricDelta"] {
        color: #d1d5db !important;
        font-size: 0.76rem !important;
        line-height: 1.05 !important;
        white-space: normal !important;
        overflow-wrap: anywhere !important;
    }
    .builder-metric-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.45rem;
        margin: 0.35rem 0 0.4rem;
    }
    .builder-metric-card {
        background: linear-gradient(135deg, #0f172a 0%, #111827 100%);
        border: 1px solid #243244;
        border-radius: 0.85rem;
        padding: 0.62rem 0.72rem;
        min-width: 0;
        box-shadow: 0 1px 2px rgba(0,0,0,0.20);
    }
    .builder-metric-card--wide {
        grid-column: span 2;
    }
    .builder-metric-label {
        color: #cbd5e1;
        font-size: 0.78rem;
        line-height: 1.08;
        margin-bottom: 0.18rem;
    }
    .builder-metric-value {
        color: #ffffff;
        font-weight: 850;
        font-size: 1.10rem;
        line-height: 1.08;
        white-space: normal;
        overflow-wrap: anywhere;
        word-break: break-word;
    }
    .builder-metric-value--big {
        font-size: 1.50rem;
    }
    .builder-note-compact div[data-testid="stAlert"] {
        padding-top: 0.45rem !important;
        padding-bottom: 0.45rem !important;
    }

    div[data-testid="stDataFrame"] {font-size: 0.85rem;}
    .stButton > button {width: 100%; border-radius: 0.75rem; font-weight: 700; min-height: 2.6rem;}
    div[data-testid="stRadio"] label {font-weight: 700 !important;}
    .ez-card {
        background: linear-gradient(135deg, #0f172a 0%, #111827 100%);
        border: 1px solid #243244;
        border-radius: 1.05rem;
        padding: 0.95rem;
        margin: 0.55rem 0;
        box-shadow: 0 8px 22px rgba(0,0,0,0.22);
    }
    .ez-card-green {border-left: 6px solid #22c55e;}
    .ez-card-yellow {border-left: 6px solid #f59e0b;}
    .ez-card-red {border-left: 6px solid #ef4444;}
    .ez-title {font-size: 1.02rem; font-weight: 850; color: #f9fafb; margin-bottom: 0.25rem;}
    .ez-sub {font-size: 0.82rem; color: #cbd5e1; margin-bottom: 0.45rem;}
    .ez-chip {display:inline-block; padding:0.22rem 0.50rem; border-radius:999px; font-size:0.72rem; font-weight:850; margin:0.12rem 0.15rem 0.12rem 0;}
    .ez-chip-green {background:#dcfce7; color:#166534;}
    .ez-chip-yellow {background:#fef3c7; color:#92400e;}
    .ez-chip-red {background:#fee2e2; color:#991b1b;}
    .ez-muted {color:#94a3b8; font-size:0.76rem;}
    .ez-kv {display:flex; justify-content:space-between; gap:0.6rem; border-top:1px solid #243244; padding-top:0.35rem; margin-top:0.35rem;}
    .ez-kv span:first-child {color:#94a3b8; font-size:0.76rem;}
    .ez-kv span:last-child {color:#f8fafc; font-weight:800; font-size:0.82rem; text-align:right;}
    .ez-hero {text-align:center; padding:0.3rem 0 0.7rem;}
    .ez-hero-title {font-size:1.35rem; font-weight:900; color:#f8fafc;}
    .ez-hero-sub {font-size:0.82rem; color:#cbd5e1;}
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



# -----------------------
# SEASON CONSTANTS
# -----------------------
# Keep these explicit for Render/mobile so live MLB/Savant helper defaults
# are defined before function declarations are evaluated.
MLB_SEASON = 2026
LAST_SEASON = MLB_SEASON - 1
CURRENT_YEAR = MLB_SEASON
LAST_YEAR = LAST_SEASON


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
    """Read a Google Sheet tab safely, even if the sheet has extra/blank/duplicate headers."""
    try:
        worksheet = get_or_create_worksheet(tab_name, columns)

        try:
            records = worksheet.get_all_records(expected_headers=columns)
            df = pd.DataFrame(records)
        except TypeError:
            # Older gspread versions may not support expected_headers.
            records = worksheet.get_all_records()
            df = pd.DataFrame(records)
        except Exception:
            # Fallback for sheets with duplicate/blank header cells.
            values = worksheet.get_all_values()
            if not values:
                return pd.DataFrame(columns=columns)

            header = [str(x).strip() for x in values[0]]
            data_rows = values[1:]
            rows = []
            for values_row in data_rows:
                row_dict = {}
                for col in columns:
                    if col in header:
                        idx = header.index(col)
                        row_dict[col] = values_row[idx] if idx < len(values_row) else ""
                    else:
                        row_dict[col] = ""
                # Keep non-empty rows only.
                if any(str(v).strip() for v in row_dict.values()):
                    rows.append(row_dict)
            df = pd.DataFrame(rows)

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


def _mlb_num(value, default=0.0):
    """Safely convert MLB Stats API numeric fields to float.

    The Stats API returns some values as strings, blanks, or placeholder dashes.
    Live model loaders use this for IP, SO, BB, ERA, batters faced, etc.
    """
    try:
        if value in [None, "", "-.---", "--"]:
            return default
        return float(str(value).replace("%", "").strip())
    except Exception:
        return default


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



def moneyline_grade(edge, confidence_score=None, confluence=None, risk_flags=None):
    """Grade moneylines using value, confidence, confluence, and risk flags.

    Uniform v12 selection logic:
    - Edge-only calls stay backward compatible for preview tables.
    - B Moneylines stay clean: risky A plays are not downgraded into B.
    - A Moneylines with monster edges need elite confluence or become Non-Edge.
    - Borderline 4-5% edges can qualify only with 4/4 confluence and strong confidence.
    """
    try:
        edge = float(edge or 0)
    except Exception:
        edge = 0.0

    # Keep old quick-preview behavior when the confidence layer is not supplied.
    if confidence_score is None:
        if edge >= 0.08:
            return "A Moneyline"
        elif edge >= 0.05:
            return "B Moneyline"
        return "Non-Edge Moneyline"

    try:
        confidence_score = float(confidence_score or 0)
    except Exception:
        confidence_score = 0.0
    try:
        confluence = int(confluence or 0)
    except Exception:
        confluence = 0

    risk_flags = [str(x) for x in (risk_flags or []) if str(x).strip()]
    has_major_risk = len(risk_flags) > 0

    if confluence < 3:
        return "Non-Edge Moneyline"

    # Tracker audit: extreme model edges have been less reliable than the clean
    # moderate-edge B bucket. Do not let inflated edges automatically become A.
    if edge > 0.16:
        if confluence >= 4 and confidence_score >= 82 and not has_major_risk:
            return "A Moneyline"
        return "Non-Edge Moneyline"

    if edge >= 0.08:
        if confidence_score >= 75 and not has_major_risk:
            return "A Moneyline"
        return "Non-Edge Moneyline"

    if edge >= 0.05:
        if confidence_score >= 65 and not has_major_risk:
            return "B Moneyline"
        return "Non-Edge Moneyline"

    # Optional borderline B bucket: only 4/4, high-confidence, no-risk setups.
    if edge >= 0.04:
        if confluence >= 4 and confidence_score >= 70 and not has_major_risk:
            return "B Moneyline"
        return "Non-Edge Moneyline"

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
# PITCHER RECENT FORM MEMORY
# -----------------------

RECENT_FORM_TAB = "pitcher_recent_form"
RECENT_FORM_COLUMNS = [
    "Date", "Game Key", "Pitcher", "Team", "Opponent", "Role", "Model Version",
    "Raw Projection", "Global Calibrated Projection", "Pitcher Adjustment", "Opponent Adjustment",
    "Projection", "Shadow Projection", "Line", "Odds", "Grade", "Reliability Score",
    "Expected Std Dev", "Selected Probability", "Market Implied Probability", "Price Edge",
    "Projected IP", "Projected Pitches", "Projected Batters Faced", "Projected K Rate",
    "Pitcher Archetype", "Archetype Shadow Adjustment", "Season K % Snapshot", "Season Whiff % Snapshot",
    "Arsenal Score", "Weapon Count", "Weather Source", "Data Health Score", "Data Health Notes",
    "Lineup Confirmed", "Lineup Hitters Found", "Opener", "Bulk Pitcher", "Bulk Confidence", "Bulk Source",
    "Actual Ks", "Actual IP", "Actual Pitches", "Actual Batters Faced", "Actual K Rate",
    "Projection Miss", "Raw Projection Miss", "Opportunity Error", "K Rate Error", "Early Exit",
    "Recent Form Applied", "Calibration Notes", "Updated Time ET"
]


def load_pitcher_recent_form():
    return read_sheet(RECENT_FORM_TAB, RECENT_FORM_COLUMNS)


def save_pitcher_recent_form(df):
    return write_sheet(RECENT_FORM_TAB, df, RECENT_FORM_COLUMNS)


def _blank_recent_form():
    return {
        "status": "No History",
        "avg_miss": "",
        "weighted_bias": "",
        "mean_miss": "",
        "mae": "",
        "rmse": "",
        "error_std": "",
        "direction_std": "",
        "accuracy": "No History",
        "consistency": "No History",
        "direction_reliability": "No History",
        "positive_share": "",
        "same_direction_share": "",
        "starts": 0,
        "direction_starts": 0,
        "note": "No completed recent-form rows yet.",
        "last3": [],
        "last5": [],
    }


def _recent_form_time_label():
    try:
        return eastern_now().strftime("%Y-%m-%d %I:%M %p ET")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %I:%M %p")


def _recent_form_status(avg_miss):
    try:
        avg_miss = float(avg_miss)
    except Exception:
        return "No History"
    if avg_miss >= 1.00:
        return "Very Hot"
    if avg_miss >= 0.40:
        return "Hot"
    if avg_miss <= -1.00:
        return "Very Cold"
    if avg_miss <= -0.40:
        return "Cold"
    return "Neutral"


def _recent_accuracy_label(mae, rmse, starts):
    """Describe absolute model accuracy without allowing misses to cancel out."""
    try:
        starts = int(starts or 0)
        mae = float(mae)
        rmse = float(rmse)
    except Exception:
        return "No History"
    if starts < 2:
        return "Limited Sample"
    if mae <= 0.75 and rmse <= 1.00:
        return "Excellent"
    if mae <= 1.25 and rmse <= 1.50:
        return "Good"
    if mae <= 1.75 and rmse <= 2.10:
        return "Average"
    return "Poor"


def _recent_consistency_label(error_std, starts):
    try:
        starts = int(starts or 0)
        error_std = float(error_std)
    except Exception:
        return "No History"
    if starts < 2:
        return "Limited Sample"
    if error_std <= 0.75:
        return "Stable"
    if error_std <= 1.50:
        return "Moderate"
    return "Volatile"


def _recent_direction_reliability(status, misses, error_std):
    """Judge whether hot/cold direction is repeatable, separate from raw accuracy.

    A pitcher can have poor absolute accuracy because he consistently beats the model.
    That is still useful directional evidence for an over, so this intentionally does
    not use MAE as the main gate.
    """
    misses = [float(x) for x in misses if pd.notna(x)]
    if len(misses) < 2:
        return "Limited Sample", 0.0, 0.0

    positive_share = sum(1 for x in misses if x > 0) / len(misses)
    negative_share = sum(1 for x in misses if x < 0) / len(misses)
    status_upper = str(status or "").upper()

    if "HOT" in status_upper and "COLD" not in status_upper:
        aligned_share = positive_share
    elif "COLD" in status_upper:
        aligned_share = negative_share
    else:
        return "Mixed", positive_share, max(positive_share, negative_share)

    try:
        error_std = float(error_std)
    except Exception:
        error_std = 99.0

    if aligned_share >= 0.99 and error_std <= 1.50:
        label = "Strong"
    elif aligned_share >= (2 / 3) and error_std <= 1.75:
        label = "Reliable"
    elif aligned_share >= (2 / 3):
        label = "Volatile"
    else:
        label = "Mixed"
    return label, positive_share, aligned_share


def _recent_form_display(recent_form):
    recent_form = recent_form or {}
    status = recent_form.get("status", "No History")
    starts = recent_form.get("starts", 0)
    bias = recent_form.get("weighted_bias", recent_form.get("avg_miss", ""))
    mae = recent_form.get("mae", "")
    reliability = recent_form.get("direction_reliability", "")
    if not starts:
        return "No History"
    try:
        base = f"{status} ({float(bias):+.2f} K weighted, L{int(recent_form.get('direction_starts', starts) or starts)})"
    except Exception:
        base = f"{status} (L{starts})"
    try:
        return f"{base} | MAE {float(mae):.2f} | {reliability}"
    except Exception:
        return base


def get_pitcher_recent_form_summary(pitcher, lookback=5):
    """Return separate direction and accuracy diagnostics for recent starts.

    Direction uses a recency-weighted last three starts (50/30/20). Accuracy and
    volatility use up to five starts so positive and negative misses cannot cancel.
    This does not directly change the projection; it feeds the grade/confidence layer.
    """
    try:
        df = load_pitcher_recent_form()
    except Exception:
        return _blank_recent_form()
    if df is None or df.empty:
        return _blank_recent_form()

    out = df.copy()
    out["_pitcher_norm"] = out["Pitcher"].astype(str).apply(normalize_name_for_match)
    target = normalize_name_for_match(pitcher)
    out = out[out["_pitcher_norm"] == target].copy()
    if out.empty:
        return _blank_recent_form()

    out["Projection Miss"] = pd.to_numeric(out["Projection Miss"], errors="coerce")
    out["Actual Ks"] = pd.to_numeric(out["Actual Ks"], errors="coerce")
    out["_date"] = pd.to_datetime(out["Date"], errors="coerce")

    # Render can run in UTC. Use Eastern date so evening app sessions do not
    # accidentally treat today's pending row as a completed prior-day result.
    try:
        today_cutoff = pd.to_datetime(today_et_string())
    except Exception:
        today_cutoff = pd.to_datetime(str(date.today()))
    out = out[out["_date"] < today_cutoff].copy()

    out = (
        out.dropna(subset=["Projection Miss", "Actual Ks"])
        .sort_values("_date", ascending=False)
        .head(max(5, int(lookback or 5)))
    )
    if out.empty:
        return _blank_recent_form()

    diagnostic_rows = out.head(int(lookback or 5)).copy()
    direction_rows = diagnostic_rows.head(3).copy()
    direction_misses = direction_rows["Projection Miss"].astype(float).tolist()
    all_misses = diagnostic_rows["Projection Miss"].astype(float).tolist()

    base_weights = [0.50, 0.30, 0.20]
    weights = base_weights[:len(direction_misses)]
    weight_total = sum(weights) or 1.0
    weighted_bias = sum(m * w for m, w in zip(direction_misses, weights)) / weight_total
    mean_miss = sum(all_misses) / len(all_misses)
    mae = sum(abs(x) for x in all_misses) / len(all_misses)
    rmse = math.sqrt(sum(x * x for x in all_misses) / len(all_misses))
    error_std = math.sqrt(sum((x - mean_miss) ** 2 for x in all_misses) / len(all_misses))
    direction_mean = sum(direction_misses) / len(direction_misses)
    direction_std = math.sqrt(
        sum((x - direction_mean) ** 2 for x in direction_misses) / len(direction_misses)
    )

    status = _recent_form_status(weighted_bias)
    accuracy = _recent_accuracy_label(mae, rmse, len(all_misses))
    consistency = _recent_consistency_label(error_std, len(all_misses))
    direction_reliability, positive_share, aligned_share = _recent_direction_reliability(
        status, direction_misses, direction_std
    )
    within_15 = sum(1 for x in all_misses if abs(x) <= 1.5) / len(all_misses)

    def _rows_payload(frame):
        rows = []
        for _, row in frame.iterrows():
            rows.append({
                "Date": str(row.get("Date", "")),
                "Opponent": str(row.get("Opponent", "")),
                "Projection": row.get("Projection", ""),
                "Actual Ks": row.get("Actual Ks", ""),
                "Miss": round(float(row.get("Projection Miss", 0)), 2),
            })
        return rows

    note = (
        f"{status}: weighted L3 bias {weighted_bias:+.2f} K; "
        f"L{len(all_misses)} MAE {mae:.2f}, RMSE {rmse:.2f}, error SD {error_std:.2f}; "
        f"direction {direction_reliability}, accuracy {accuracy}, consistency {consistency}."
    )
    return {
        "status": status,
        # Keep avg_miss for backward compatibility with saved/detail code.
        "avg_miss": round(weighted_bias, 2),
        "weighted_bias": round(weighted_bias, 2),
        "mean_miss": round(mean_miss, 2),
        "mae": round(mae, 2),
        "rmse": round(rmse, 2),
        "error_std": round(error_std, 2),
        "direction_std": round(direction_std, 2),
        "within_1_5_pct": round(within_15 * 100, 1),
        "accuracy": accuracy,
        "consistency": consistency,
        "direction_reliability": direction_reliability,
        "positive_share": round(positive_share, 3),
        "same_direction_share": round(aligned_share, 3),
        "starts": int(len(all_misses)),
        "direction_starts": int(len(direction_misses)),
        "note": note,
        "last3": _rows_payload(direction_rows),
        "last5": _rows_payload(diagnostic_rows),
    }


def apply_recent_form_to_k_grade(grade, recent_form):
    """Use recent results only as a conservative conflict warning.

    V14 removes all recent-form upgrades and removes the old Very Cold block on
    Over recommendations. The only live grade change retained is a reliable,
    Very Hot pitcher conflicting with an Under recommendation.
    """
    original = str(grade or "PASS").upper().strip()
    status = str((recent_form or {}).get("status", "No History")).upper()
    reliability = str((recent_form or {}).get("direction_reliability", "No History")).upper()
    starts = int((recent_form or {}).get("direction_starts", 0) or 0)
    bias = (recent_form or {}).get("weighted_bias", (recent_form or {}).get("avg_miss", ""))

    adjusted = original
    supported = starts >= 3 and reliability in ["STRONG", "RELIABLE"]
    if supported and status == "VERY HOT":
        if original == "LEAN UNDER":
            adjusted = "PASS"
        elif original == "UNDER":
            adjusted = "LEAN UNDER"
        elif original == "STRONG UNDER":
            adjusted = "UNDER"

    if adjusted == original:
        return grade, ""
    try:
        bias_text = f"{float(bias):+.2f}"
    except Exception:
        bias_text = str(bias)
    return adjusted, (
        f"Recent-form conflict guard: Very Hot ({bias_text} weighted K residual; "
        f"{reliability.title()}, L{starts}) changed {original} → {adjusted}. "
        "V14 never upgrades a play from recent results alone."
    )


def apply_recent_accuracy_to_k_grade(grade, recent_form, edge=0.0):
    """Reduce confidence in close plays when recent errors are large and directionless.

    This targets the cancellation problem: a near-zero weighted bias can look neutral
    even when the individual misses are extreme. Only neutral, poor, volatile samples
    are downgraded, so stable one-direction model bias can still support an override.
    """
    original = str(grade or "PASS").upper().strip()
    status = str((recent_form or {}).get("status", "No History")).upper()
    accuracy = str((recent_form or {}).get("accuracy", "No History")).upper()
    consistency = str((recent_form or {}).get("consistency", "No History")).upper()
    starts = int((recent_form or {}).get("starts", 0) or 0)
    try:
        edge_abs = abs(float(edge or 0.0))
    except Exception:
        edge_abs = 0.0

    if starts < 3 or status != "NEUTRAL" or accuracy != "POOR" or consistency != "VOLATILE":
        return grade, ""

    adjusted = original
    if original in ["LEAN OVER", "LEAN UNDER"]:
        adjusted = "PASS"
    elif original == "OVER" and edge_abs < 1.30:
        adjusted = "LEAN OVER"
    elif original == "UNDER" and edge_abs < 1.55:
        adjusted = "LEAN UNDER"

    if adjusted == original:
        return grade, ""

    mae = (recent_form or {}).get("mae", "")
    error_std = (recent_form or {}).get("error_std", "")
    try:
        diagnostics = f"MAE {float(mae):.2f}, error SD {float(error_std):.2f}"
    except Exception:
        diagnostics = "poor/volatile recent errors"
    return adjusted, f"Recent accuracy downgrade: Neutral direction but {diagnostics} changed {original} → {adjusted}."


def apply_six_inning_recent_form_override(grade, six_k, line, recent_form, hard_workload_risk=False):
    """V14 disables recent-form-created wagers.

    Six-inning pace remains a confirmation input inside the probability grade,
    but recent results can no longer turn PASS into a bet or upgrade a grade.
    """
    return grade, "", False


def adjust_k_score_for_recent_override(score, final_grade, recent_form, override_applied=False):
    """Keep the score conservative; recent form no longer creates score boosts."""
    try:
        score = float(score or 0.0)
    except Exception:
        score = 0.0
    status = str((recent_form or {}).get("status", "")).upper()
    accuracy = str((recent_form or {}).get("accuracy", "")).upper()
    consistency = str((recent_form or {}).get("consistency", "")).upper()
    if status == "NEUTRAL" and accuracy == "POOR" and consistency == "VOLATILE":
        score -= 8.0
    return round(min(100.0, max(0.0, score)), 1)



def apply_weapon_floor_to_k_grade(grade, arsenal_details):
    """Prevent under recommendations when a pitcher has 2+ true weapons.

    Multiple true weapons create strikeout ceiling paths, so the model should pass
    instead of recommending a lean/standard/strong under in those spots.
    """
    try:
        weapon_count = int((arsenal_details or {}).get("weapon_count", 0) or 0)
    except Exception:
        weapon_count = 0

    original = str(grade or "PASS").upper().strip()
    if weapon_count >= 2 and original in ["LEAN UNDER", "UNDER", "STRONG UNDER"]:
        return "PASS", f"Weapon floor: {weapon_count} true weapons found, so under grade changed {original} → PASS."
    return grade, ""

def record_pitcher_recent_form_start(game_date, game_key, pitcher, team, opponent, projection, line, grade, recent_note="", metadata=None):
    """Save every pitcher projection, including PASS and bulk-role projections."""
    if not pitcher or str(pitcher).upper() == "TBD":
        return False
    metadata = metadata or {}
    df = load_pitcher_recent_form()
    if df is None or df.empty:
        df = pd.DataFrame(columns=RECENT_FORM_COLUMNS)
    for col in RECENT_FORM_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    game_date = str(game_date)
    game_key = str(game_key or "")
    pitcher_norm = normalize_name_for_match(pitcher)
    role = str(metadata.get("role", "Starter") or "Starter")
    if not df.empty:
        existing = (
            (df["Date"].astype(str) == game_date) &
            (df["Pitcher"].astype(str).apply(normalize_name_for_match) == pitcher_norm) &
            (df["Role"].astype(str).str.upper() == role.upper())
        )
        if game_key:
            existing = existing & (df["Game Key"].astype(str) == game_key)
        df = df[~existing].copy()

    def _r(value, digits=3):
        try:
            if value in [None, ""]:
                return ""
            return round(float(value), digits)
        except Exception:
            return value

    new_row = {col: "" for col in RECENT_FORM_COLUMNS}
    new_row.update({
        "Date": game_date,
        "Game Key": game_key,
        "Pitcher": str(pitcher),
        "Team": str(team),
        "Opponent": str(opponent),
        "Role": role,
        "Model Version": str(metadata.get("model_version", MODEL_VERSION)),
        "Raw Projection": _r(metadata.get("raw_projection", projection), 2),
        "Global Calibrated Projection": _r(metadata.get("global_calibrated_projection", projection), 2),
        "Pitcher Adjustment": _r(metadata.get("pitcher_adjustment", 0), 2),
        "Opponent Adjustment": _r(metadata.get("opponent_adjustment", 0), 2),
        "Projection": _r(projection, 2),
        "Shadow Projection": _r(metadata.get("shadow_projection", ""), 2),
        "Line": line,
        "Odds": metadata.get("odds", ""),
        "Grade": str(grade),
        "Reliability Score": _r(metadata.get("reliability_score", ""), 1),
        "Expected Std Dev": _r(metadata.get("expected_std_dev", ""), 2),
        "Selected Probability": _r(metadata.get("selected_probability", ""), 4),
        "Market Implied Probability": _r(metadata.get("market_implied_probability", ""), 4),
        "Price Edge": _r(metadata.get("price_edge", ""), 4),
        "Projected IP": _r(metadata.get("projected_ip", ""), 2),
        "Projected Pitches": _r(metadata.get("projected_pitches", ""), 1),
        "Projected Batters Faced": _r(metadata.get("projected_bf", ""), 1),
        "Projected K Rate": metadata.get("projected_k_rate", ""),
        "Pitcher Archetype": metadata.get("pitcher_archetype", ""),
        "Archetype Shadow Adjustment": metadata.get("archetype_shadow_adjustment", ""),
        "Season K % Snapshot": metadata.get("season_k_pct_snapshot", ""),
        "Season Whiff % Snapshot": metadata.get("season_whiff_pct_snapshot", ""),
        "Arsenal Score": metadata.get("arsenal_score", ""),
        "Weapon Count": metadata.get("weapon_count", ""),
        "Weather Source": metadata.get("weather_source", ""),
        "Data Health Score": metadata.get("data_health_score", ""),
        "Data Health Notes": metadata.get("data_health_notes", ""),
        "Lineup Confirmed": metadata.get("lineup_confirmed", ""),
        "Lineup Hitters Found": metadata.get("lineup_hitters_found", ""),
        "Opener": metadata.get("opener", ""),
        "Bulk Pitcher": metadata.get("bulk_pitcher", ""),
        "Bulk Confidence": metadata.get("bulk_confidence", ""),
        "Bulk Source": metadata.get("bulk_source", ""),
        "Recent Form Applied": str(recent_note or ""),
        "Calibration Notes": str(metadata.get("calibration_notes", "")),
        "Updated Time ET": "",
    })
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    return save_pitcher_recent_form(df)



def _parse_boxscore_ip(value):
    try:
        text_value = str(value or "0").strip()
        if "." not in text_value:
            return float(text_value)
        whole, frac = text_value.split(".", 1)
        outs = int(frac[:1] or 0)
        return float(whole) + (outs / 3.0)
    except Exception:
        return 0.0


def _boxscore_pitcher_line(game_key, pitcher):
    """Return completed pitching line for projection-error decomposition."""
    try:
        if not game_key:
            return None
        response = requests.get(f"https://statsapi.mlb.com/api/v1/game/{game_key}/boxscore", timeout=20)
        response.raise_for_status()
        data = response.json()
        target = name_match_variants(pitcher)
        for side in ["home", "away"]:
            players = data.get("teams", {}).get(side, {}).get("players", {}) or {}
            for player_data in players.values():
                person = player_data.get("person", {}) or {}
                candidate_names = [person.get("fullName", ""), player_data.get("boxscoreName", ""), player_data.get("name", "")]
                candidates = set()
                for candidate in candidate_names:
                    candidates.update(name_match_variants(candidate))
                if not target.intersection(candidates):
                    continue
                pitching = player_data.get("stats", {}).get("pitching", {}) or {}
                if not pitching:
                    continue
                ip = _parse_boxscore_ip(pitching.get("inningsPitched", 0))
                bf = _mlb_num(pitching.get("battersFaced", 0), 0)
                pitches = _mlb_num(pitching.get("numberOfPitches", pitching.get("pitchesThrown", 0)), 0)
                ks = _mlb_num(pitching.get("strikeOuts", 0), 0)
                return {
                    "strikeouts": int(ks),
                    "innings_pitched": round(ip, 3),
                    "pitches": int(pitches),
                    "batters_faced": int(bf),
                    "k_rate": round((ks / bf), 4) if bf > 0 else "",
                }
        return None
    except Exception:
        return None


def _boxscore_pitcher_ks(game_key, pitcher):
    """Return a pitcher's strikeout total from an MLB boxscore.

    The recent-form sheet stores pitchers as "Last, First", while MLB's
    boxscore usually returns "First Last". Match against both formats so
    completed games can populate Actual Ks automatically.
    """
    try:
        if not game_key:
            return None
        url = f"https://statsapi.mlb.com/api/v1/game/{game_key}/boxscore"
        data = requests.get(url, timeout=20).json()

        def _name_variants(value):
            raw = str(value or "").strip()
            variants = set()
            if not raw:
                return variants
            variants.add(normalize_match_text(raw))
            variants.add(normalize_match_text(raw.replace(",", " ")))
            try:
                first_last = to_first_last(raw)
                variants.add(normalize_match_text(first_last))
                variants.add(normalize_match_text(first_last.replace(",", " ")))
            except Exception:
                pass
            try:
                last_first = to_last_first(raw)
                variants.add(normalize_match_text(last_first))
                variants.add(normalize_match_text(last_first.replace(",", " ")))
            except Exception:
                pass
            return {v for v in variants if v}

        target_variants = _name_variants(pitcher)
        if not target_variants:
            return None

        for side in ["home", "away"]:
            players = data.get("teams", {}).get(side, {}).get("players", {}) or {}
            for _, player_data in players.items():
                person = player_data.get("person", {}) or {}
                names = [
                    person.get("fullName", ""),
                    player_data.get("boxscoreName", ""),
                    player_data.get("name", ""),
                ]
                candidate_variants = set()
                for n in names:
                    candidate_variants.update(_name_variants(n))

                matched = False
                for tv in target_variants:
                    for cv in candidate_variants:
                        if tv and cv and (tv == cv or tv in cv or cv in tv):
                            matched = True
                            break
                    if matched:
                        break
                if not matched:
                    continue

                pitching = player_data.get("stats", {}).get("pitching", {}) or {}
                if "strikeOuts" in pitching:
                    return int(float(pitching.get("strikeOuts", 0) or 0))
        return None
    except Exception:
        return None

def _resolve_game_key_from_schedule(game_date, team, opponent):
    try:
        response = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId": 1, "date": str(game_date), "hydrate": "probablePitcher"},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        team_norm = normalize_match_text(team)
        opp_norm = normalize_match_text(opponent)
        for day in data.get("dates", []):
            for game in day.get("games", []):
                home = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
                away = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
                home_norm = normalize_match_text(home)
                away_norm = normalize_match_text(away)
                if team_norm in [home_norm, away_norm] and opp_norm in [home_norm, away_norm]:
                    return str(game.get("gamePk", ""))
    except Exception:
        return ""
    return ""


def update_pitcher_recent_form_actuals(auto_only=True):
    """Fill prior-date results and split misses into workload and K-rate errors."""
    df = load_pitcher_recent_form()
    if df is None or df.empty:
        return {"checked": 0, "updated": 0, "message": "No recent-form rows yet."}
    for col in RECENT_FORM_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    try:
        today_text = today_et_string()
    except Exception:
        today_text = str(date.today())
    checked = updated = 0
    out = df.copy()

    # Google Sheets can return blank result columns as Pandas' strict string dtype.
    # The updater then needs to place real ints/floats into those same cells. Cast
    # only the fields updated here to object so numeric results stay numeric while
    # blanks/text values remain supported. This avoids Pandas 3.x dtype errors such
    # as: Invalid value '3.0' for dtype 'str'.
    recent_result_columns = [
        "Game Key", "Actual Ks", "Actual IP", "Actual Pitches",
        "Actual Batters Faced", "Actual K Rate", "Projection Miss",
        "Raw Projection Miss", "Opportunity Error", "K Rate Error",
        "Early Exit", "Updated Time ET",
    ]
    for col in recent_result_columns:
        if col in out.columns:
            out[col] = out[col].astype(object)

    for idx, row in out.iterrows():
        row_date = str(row.get("Date", "")).strip()
        actual = str(row.get("Actual Ks", "")).strip()
        if not row_date or row_date >= today_text or actual not in ["", "nan", "None", "<NA>"]:
            continue
        checked += 1
        game_key = str(row.get("Game Key", "")).strip()
        if not game_key:
            game_key = _resolve_game_key_from_schedule(row_date, row.get("Team", ""), row.get("Opponent", ""))
            if game_key:
                out.at[idx, "Game Key"] = str(game_key)
        line = _boxscore_pitcher_line(game_key, row.get("Pitcher", ""))
        if not line:
            continue
        ks = int(line.get("strikeouts", 0))
        actual_ip = float(line.get("innings_pitched", 0) or 0)
        actual_pitches = int(line.get("pitches", 0) or 0)
        actual_bf = int(line.get("batters_faced", 0) or 0)
        actual_k_rate = line.get("k_rate", "")
        try:
            projection = float(row.get("Projection", 0) or 0)
            miss = round(ks - projection, 2)
        except Exception:
            miss = ""
        try:
            raw_projection = float(row.get("Raw Projection", row.get("Projection", 0)) or 0)
            raw_miss = round(ks - raw_projection, 2)
        except Exception:
            raw_miss = ""
        try:
            projected_bf = float(row.get("Projected Batters Faced", 0) or 0)
            opportunity_error = round(actual_bf - projected_bf, 1) if projected_bf > 0 else ""
        except Exception:
            opportunity_error = ""
        try:
            projected_k_rate = float(row.get("Projected K Rate", 0) or 0)
            k_rate_error = round(float(actual_k_rate) - projected_k_rate, 4) if actual_k_rate != "" and projected_k_rate > 0 else ""
        except Exception:
            k_rate_error = ""
        try:
            projected_ip = float(row.get("Projected IP", 0) or 0)
            early_exit = "TRUE" if projected_ip > 0 and actual_ip <= max(2.0, projected_ip - 1.5) and actual_pitches < 80 else "FALSE"
        except Exception:
            early_exit = ""
        updates = {
            "Actual Ks": ks, "Actual IP": round(actual_ip, 2), "Actual Pitches": actual_pitches,
            "Actual Batters Faced": actual_bf, "Actual K Rate": actual_k_rate,
            "Projection Miss": miss, "Raw Projection Miss": raw_miss,
            "Opportunity Error": opportunity_error, "K Rate Error": k_rate_error,
            "Early Exit": early_exit, "Updated Time ET": _recent_form_time_label(),
        }
        for col, val in updates.items():
            out.at[idx, col] = val
        updated += 1
    if updated:
        save_pitcher_recent_form(out)
    return {"checked": checked, "updated": updated, "message": f"Pitcher history checked {checked} rows and filled {updated} complete pitching lines."}


def maybe_auto_update_pitcher_recent_form():
    """Run once per app session/day so opening the app fills yesterday's completed K totals."""
    try:
        recent_form_today = today_et_string()
    except Exception:
        recent_form_today = str(date.today())
    key = f"recent_form_auto_update_done_{recent_form_today}"
    if st.session_state.get(key):
        return
    st.session_state[key] = True
    try:
        result = update_pitcher_recent_form_actuals(auto_only=True)
        game_result = update_game_projection_history_actuals()
        ensure_model_version_logged()
        if result.get("updated", 0) > 0:
            st.success(result.get("message", "Pitcher history updated."))
        if game_result.get("updated", 0) > 0:
            st.success(f"Updated {game_result.get('updated')} completed game projection rows.")
    except Exception:
        # Never block the builder if MLB API or Sheets has a temporary issue.
        pass



# -----------------------
# BET TRACKER
# -----------------------

TRACKER_COLUMNS = [
    "Date", "Bet Type", "Selection", "Market", "Odds/Line",
    "Model %", "Implied %", "Edge %", "Result",
    "Raw Projection", "Calibrated Projection", "Reliability Score", "Expected Std Dev",
    "Selected Probability", "Model Version", "Game Key", "Team", "Opponent", "Pitcher Role",
    "Favorite Pick", "Handpicked Record", "Favorite Rank", "Favorite Tag", "Favorite Notes"
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


def add_bet(bet_type, selection, market, odds_line="", model_pct="", implied_pct="", edge_pct="", metadata=None):
    df = load_tracker()

    metadata = metadata or {}
    new_row = {
        "Date": str(date.today()),
        "Bet Type": bet_type,
        "Selection": selection,
        "Market": market,
        "Odds/Line": odds_line,
        "Model %": model_pct,
        "Implied %": implied_pct,
        "Edge %": edge_pct,
        "Result": "Pending",
        "Raw Projection": metadata.get("raw_projection", ""),
        "Calibrated Projection": metadata.get("calibrated_projection", ""),
        "Reliability Score": metadata.get("reliability_score", ""),
        "Expected Std Dev": metadata.get("expected_std_dev", ""),
        "Selected Probability": metadata.get("selected_probability", ""),
        "Model Version": metadata.get("model_version", MODEL_VERSION),
        "Game Key": metadata.get("game_key", ""),
        "Team": metadata.get("team", ""),
        "Opponent": metadata.get("opponent", ""),
        "Pitcher Role": metadata.get("role", ""),
        "Favorite Pick": "",
        "Handpicked Record": "",
        "Favorite Rank": "",
        "Favorite Tag": "",
        "Favorite Notes": ""
    }

    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    save_tracker(df)


def extract_pitcher_from_k_play(play_text):
    """Pull pitcher name out of a saved K summary like 'Smith, John 5.80 (over) Line 4.5'."""
    import re
    text = str(play_text).strip()
    if not text:
        return ""
    match = re.match(r"^(.*?)(?=\s+\d+(?:\.\d+)?)", text)
    return match.group(1).strip() if match else text.split("(", 1)[0].strip()


def normalize_match_text(value):
    import re
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def name_match_variants(name):
    """Return normalized name variants for matching First Last and Last, First formats."""
    raw = str(name).strip()
    if not raw:
        return set()
    variants = {normalize_match_text(raw)}
    if "," in raw:
        last, first = raw.split(",", 1)
        variants.add(normalize_match_text(f"{first.strip()} {last.strip()}"))
        variants.add(normalize_match_text(f"{last.strip()} {first.strip()}"))
    else:
        parts = raw.split()
        if len(parts) >= 2:
            first = parts[0]
            last = " ".join(parts[1:])
            variants.add(normalize_match_text(f"{last} {first}"))
            variants.add(normalize_match_text(f"{last}, {first}"))
    return {v for v in variants if v}


def loose_name_match(a, b):
    """Flexible pitcher/team-name matching without allowing NRFI to cross-match pitcher props."""
    a_vars = name_match_variants(a)
    b_vars = name_match_variants(b)
    for av in a_vars:
        for bv in b_vars:
            if not av or not bv:
                continue
            if av == bv or av in bv or bv in av:
                return True
    return False


def get_best_play_category(play):
    """Return a strict category so handpicked matching cannot cross markets."""
    play_type = str(play.get("Play Type", "")).strip().upper()
    play_text = str(play.get("Play", "")).strip().upper()

    if "PITCHER" in play_type and ("K" in play_type or "STRIKEOUT" in play_type):
        return "PITCHER_K"
    if play_type == "PITCHER K" or play_type == "PITCHER STRIKEOUTS":
        return "PITCHER_K"
    if "MONEYLINE" in play_type:
        return "MONEYLINE"
    if "NRFI" in play_type or "YRFI" in play_type or "NRFI" in play_text or "YRFI" in play_text:
        return "NRFI_YRFI"
    return "OTHER"


def _normalized_team_aliases_for_match(team):
    """Return normalized team aliases for safer handpicked moneyline matching."""
    raw = str(team or "").strip()
    aliases = set()
    if raw:
        aliases.add(normalize_match_text(raw))

    try:
        for key in _team_keys(raw):
            aliases.add(normalize_match_text(key))
    except Exception:
        pass

    try:
        for canonical, names in TEAM_NAME_ALIASES_FOR_SAVANT.items():
            all_names = [canonical] + list(names)
            all_norm = {normalize_match_text(x) for x in all_names if str(x).strip()}
            raw_norm = normalize_match_text(raw)
            if raw_norm in all_norm:
                aliases.update(all_norm)
                break
    except Exception:
        pass

    return {a for a in aliases if a}


def tracker_row_matches_best_play(row, play):
    today = str(date.today())
    if str(row.get("Date", "")).strip() != today:
        return False

    bet_type = str(row.get("Bet Type", "")).strip().upper()
    market = str(row.get("Market", "")).strip().upper()
    selection = str(row.get("Selection", "")).strip()
    selection_norm = normalize_match_text(selection)

    play_text = str(play.get("Play", "")).strip()
    play_norm = normalize_match_text(play_text)
    game_norm = normalize_match_text(play.get("Game", ""))
    category = get_best_play_category(play)

    row_is_moneyline = "MONEYLINE" in bet_type or market == "MONEYLINE"
    row_is_nrfi = "NRFI" in bet_type or "YRFI" in bet_type or market in ["NRFI/YRFI", "NRFI", "YRFI"]
    row_is_pitcher_k = (
        market in ["PITCHER STRIKEOUTS", "PITCHER K", "PITCHER KS"] or
        "STRIKEOUT" in market or
        "PITCHER K" in market
    )

    # Moneyline rows: match same-day moneyline tracker rows using team aliases.
    # This fixes cases where the Best Plays card says "Dodgers" but the tracker row says
    # "Los Angeles Dodgers", or vice versa. Still prevents cross-market matching.
    if category == "MONEYLINE":
        if not row_is_moneyline or row_is_nrfi or row_is_pitcher_k or not selection_norm:
            return False

        candidate_values = [
            play_text,
            play.get("Team", ""),
            play.get("Selection", ""),
            play.get("Better ML", ""),
            play.get("Better ML Team", ""),
            play.get("Better Team", ""),
            play.get("Away Team", ""),
            play.get("Home Team", ""),
        ]

        candidate_aliases = set()
        for value in candidate_values:
            candidate_aliases.update(_normalized_team_aliases_for_match(value))

        if not candidate_aliases and play_norm:
            candidate_aliases.add(play_norm)

        for alias in candidate_aliases:
            if not alias:
                continue
            if selection_norm == alias or selection_norm in alias or alias in selection_norm:
                return True

        return False

    # NRFI/YRFI rows must match the game and must NEVER match a pitcher prop row.
    if category == "NRFI_YRFI":
        return (
            row_is_nrfi
            and not row_is_pitcher_k
            and selection_norm
            and game_norm
            and (selection_norm == game_norm or selection_norm in game_norm or game_norm in selection_norm)
        )

    # Pitcher K rows must match ONLY pitcher-strikeout tracker rows.
    # This prevents a same-game NRFI/YRFI row from ever being marked handpicked.
    if category == "PITCHER_K":
        if not row_is_pitcher_k or row_is_nrfi:
            return False

        pitcher_raw = extract_pitcher_from_k_play(play_text)
        pitcher = normalize_match_text(pitcher_raw)
        if not pitcher:
            return False

        # Match both "Last, First" and "First Last" and allow saved tracker rows
        # like "Joey Cantillo 4.5 / -110" or "Cantillo, Joey 3.69 (lean under)".
        return (
            selection_norm == pitcher
            or selection_norm.startswith(pitcher + " ")
            or loose_name_match(selection, pitcher_raw)
            or loose_name_match(selection, play_text)
        )

    return False

def mark_best_play_as_handpicked(play, favorite_rank="", favorite_tag="", favorite_notes=""):
    """Mark the matching bet_tracker row as handpicked for the public website."""
    tracker_df = load_tracker()
    if tracker_df.empty:
        return False, "No bet tracker rows found. Save the matchup bets first."

    for col in ["Favorite Pick", "Handpicked Record", "Favorite Rank", "Favorite Tag", "Favorite Notes"]:
        if col not in tracker_df.columns:
            tracker_df[col] = ""

    matches = []
    for idx, row in tracker_df.iterrows():
        if tracker_row_matches_best_play(row, play):
            matches.append(idx)

    if not matches:
        # If the slate play exists but the exact tracker row was never saved,
        # create a safe bet_tracker row and mark it. This fixes the old dead-end
        # where Best Plays could be shown but the Handpicked button could not
        # find a matching saved row.
        play_type = str(play.get("Play Type", "Handpicked")).strip() or "Handpicked"
        play_text = str(play.get("Play", "")).strip()
        category = get_best_play_category(play)
        if category == "PITCHER_K":
            selection = extract_pitcher_from_k_play(play_text) or play_text
            market = "Pitcher Strikeouts"
        elif category == "MONEYLINE":
            selection = play_text
            market = "Moneyline"
        elif category == "NRFI_YRFI":
            selection = str(play.get("Game", play_text)).strip()
            market = "NRFI/YRFI"
        else:
            selection = play_text
            market = play_type

        new_row = {
            "Date": str(date.today()),
            "Bet Type": play_type,
            "Selection": selection,
            "Market": market,
            "Odds/Line": str(play.get("Odds/Line", "")).strip(),
            "Model %": str(play.get("Score", "")).strip(),
            "Implied %": "",
            "Edge %": "",
            "Result": "Pending",
            "Favorite Pick": "TRUE",
            "Handpicked Record": "TRUE",
            "Favorite Rank": str(favorite_rank).strip(),
            "Favorite Tag": str(favorite_tag).strip().upper(),
            "Favorite Notes": str(favorite_notes).strip(),
        }
        tracker_df = pd.concat([tracker_df, pd.DataFrame([new_row])], ignore_index=True)
        if save_tracker(tracker_df):
            return True, "Created the tracker row and added it to EZPZ Handpicked Plays."
        return False, "Could not create/save the handpicked tracker row."

    # Prefer a pending row, but allow completed rows if you are handpicking after results are entered.
    pending_matches = [idx for idx in matches if str(tracker_df.loc[idx, "Result"]).strip().upper() == "PENDING"]
    target_idx = pending_matches[0] if pending_matches else matches[0]

    # Favorite Pick controls today's handpicked badge/list.
    # Handpicked Record is the permanent flag the public site can use for prior-day handpicked records.
    tracker_df.loc[target_idx, "Favorite Pick"] = "TRUE"
    tracker_df.loc[target_idx, "Handpicked Record"] = "TRUE"
    tracker_df.loc[target_idx, "Favorite Rank"] = str(favorite_rank).strip()
    tracker_df.loc[target_idx, "Favorite Tag"] = str(favorite_tag).strip().upper()
    tracker_df.loc[target_idx, "Favorite Notes"] = str(favorite_notes).strip()

    if save_tracker(tracker_df):
        return True, "Added to EZPZ Handpicked Plays."
    return False, "Could not save the handpicked update to Google Sheets."


def mark_tracker_row_as_handpicked(row_idx, favorite_rank="", favorite_tag="", favorite_notes=""):
    """Mark any saved bet_tracker row as handpicked, even if it is not a Best Play."""
    tracker_df = load_tracker()
    if tracker_df.empty or row_idx not in tracker_df.index:
        return False, "Could not find that saved bet in bet_tracker."

    for col in ["Favorite Pick", "Handpicked Record", "Favorite Rank", "Favorite Tag", "Favorite Notes"]:
        if col not in tracker_df.columns:
            tracker_df[col] = ""

    tracker_df.loc[row_idx, "Favorite Pick"] = "TRUE"
    tracker_df.loc[row_idx, "Handpicked Record"] = "TRUE"
    tracker_df.loc[row_idx, "Favorite Rank"] = str(favorite_rank).strip()
    tracker_df.loc[row_idx, "Favorite Tag"] = str(favorite_tag).strip().upper()
    tracker_df.loc[row_idx, "Favorite Notes"] = str(favorite_notes).strip()

    if save_tracker(tracker_df):
        return True, "Added saved bet to EZPZ Handpicked Plays."
    return False, "Could not save the handpicked update to Google Sheets."


def unmark_tracker_row_as_handpicked(row_idx):
    """Remove today's handpicked badge from a row while preserving completed history only if desired later."""
    tracker_df = load_tracker()
    if tracker_df.empty or row_idx not in tracker_df.index:
        return False, "Could not find that saved bet in bet_tracker."
    for col in ["Favorite Pick", "Favorite Rank", "Favorite Tag", "Favorite Notes"]:
        if col not in tracker_df.columns:
            tracker_df[col] = ""
    tracker_df.loc[row_idx, "Favorite Pick"] = ""
    tracker_df.loc[row_idx, "Favorite Rank"] = ""
    tracker_df.loc[row_idx, "Favorite Tag"] = ""
    tracker_df.loc[row_idx, "Favorite Notes"] = ""
    if save_tracker(tracker_df):
        return True, "Removed today's handpicked badge from this saved bet."
    return False, "Could not save the update to Google Sheets."


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

    winning_types = summary_df[summary_df["Wins"] > summary_df["Losses"]]["Bet Type"].tolist()
    if winning_types:
        green_completed = completed[completed["Bet Type"].isin(winning_types)].copy()
        green_wins = int((green_completed["Result"] == "Win").sum())
        green_losses = int((green_completed["Result"] == "Loss").sum())
        green_pushes = int((green_completed["Result"] == "Push").sum())
        green_decisions = green_wins + green_losses
        green_total_bets = green_wins + green_losses + green_pushes
        green_win_pct = (green_wins / green_decisions * 100) if green_decisions > 0 else 0

        green_units = 0.0
        for _, bet in green_completed.iterrows():
            green_units += profit_units_from_american_odds(bet.get("Odds/Line", ""), bet.get("Result", ""))

        green_roi = (green_units / green_decisions * 100) if green_decisions > 0 else 0

        st.divider()
        st.subheader("Green Bet Totals")
        green_totals_df = pd.DataFrame([{
            "Included Bet Types": len(winning_types),
            "Wins": green_wins,
            "Losses": green_losses,
            "Pushes": green_pushes,
            "Total Bets": green_total_bets,
            "Win %": round(green_win_pct, 1),
            "Units Won": round(green_units, 2),
            "ROI %": round(green_roi, 1)
        }])
        st.dataframe(
            green_totals_df.style.apply(lambda row: [bet_record_style_from_counts(green_wins, green_losses)] * len(row), axis=1),
            use_container_width=True,
            hide_index=True
        )
        st.caption("These totals include only completed bets from bet types with a winning record, meaning wins greater than losses.")
    else:
        st.info("No green/winning bet types yet, so Green Bet Totals are empty.")


def tracker_summary_dataframe(df):
    completed = df[df["Result"].isin(["Win", "Loss", "Push"])].copy() if df is not None and not df.empty else pd.DataFrame()
    if completed.empty:
        return pd.DataFrame(columns=["Bet Type", "Status", "Wins", "Losses", "Pushes", "Total Bets", "Win %", "Units Won", "ROI %"])
    rows = []
    for bet_type in sorted(completed["Bet Type"].dropna().unique()):
        sub = completed[completed["Bet Type"] == bet_type].copy()
        wins = int((sub["Result"] == "Win").sum())
        losses = int((sub["Result"] == "Loss").sum())
        pushes = int((sub["Result"] == "Push").sum())
        decisions = wins + losses
        total_bets = wins + losses + pushes
        win_pct = (wins / decisions * 100) if decisions > 0 else 0
        units_won = sum(profit_units_from_american_odds(b.get("Odds/Line", ""), b.get("Result", "")) for _, b in sub.iterrows())
        roi = (units_won / decisions * 100) if decisions > 0 else 0
        status = "WINNING" if wins > losses else "EVEN" if wins == losses else "LOSING"
        rows.append({
            "Bet Type": str(bet_type).upper(),
            "Status": status,
            "Wins": wins,
            "Losses": losses,
            "Pushes": pushes,
            "Total Bets": total_bets,
            "Win %": round(win_pct, 1),
            "Units Won": round(units_won, 2),
            "ROI %": round(roi, 1),
        })
    out = pd.DataFrame(rows)
    return out.sort_values(["Units Won", "Win %"], ascending=[False, False]).reset_index(drop=True)


def green_totals_from_summary(summary_df):
    if summary_df is None or summary_df.empty:
        return None
    green = summary_df[summary_df["Wins"] > summary_df["Losses"]].copy()
    if green.empty:
        return None
    wins = int(green["Wins"].sum())
    losses = int(green["Losses"].sum())
    pushes = int(green["Pushes"].sum())
    decisions = wins + losses
    total_bets = wins + losses + pushes
    units = float(green["Units Won"].sum())
    win_pct = (wins / decisions * 100) if decisions > 0 else 0
    roi = (units / decisions * 100) if decisions > 0 else 0
    return {
        "Types": int(len(green)),
        "Record": f"{wins}-{losses}-{pushes}",
        "Total Bets": total_bets,
        "Win %": round(win_pct, 1),
        "Units Won": round(units, 2),
        "ROI %": round(roi, 1),
    }


def esc(value):
    return html.escape(str(value))


def card_class_from_counts(wins, losses):
    try:
        wins = int(wins); losses = int(losses)
    except Exception:
        wins = losses = 0
    if wins > losses:
        return "ez-card-green"
    if wins == losses:
        return "ez-card-yellow"
    return "ez-card-red"


def render_record_cards(summary_df):
    if summary_df is None or summary_df.empty:
        st.info("No completed bets yet.")
        return
    for _, row in summary_df.iterrows():
        klass = card_class_from_counts(row.get("Wins", 0), row.get("Losses", 0))
        chip = "ez-chip-green" if row.get("Status") == "WINNING" else "ez-chip-yellow" if row.get("Status") == "EVEN" else "ez-chip-red"
        st.markdown(f"""
        <div class="ez-card {klass}">
            <div class="ez-title">{esc(row.get("Bet Type", ""))}</div>
            <span class="ez-chip {chip}">{esc(row.get("Status", ""))}</span>
            <div class="ez-kv"><span>Record</span><span>{row.get("Wins", 0)}-{row.get("Losses", 0)}-{row.get("Pushes", 0)}</span></div>
            <div class="ez-kv"><span>Win %</span><span>{row.get("Win %", 0)}%</span></div>
            <div class="ez-kv"><span>Units / ROI</span><span>{row.get("Units Won", 0)}u / {row.get("ROI %", 0)}%</span></div>
        </div>
        """, unsafe_allow_html=True)


def render_green_totals_card(summary_df):
    totals = green_totals_from_summary(summary_df)
    if not totals:
        st.info("No green/winning bet types yet.")
        return
    st.markdown(f"""
    <div class="ez-card ez-card-green">
        <div class="ez-title">GREEN BET TOTALS</div>
        <div class="ez-sub">Only bet types with a winning record are included.</div>
        <span class="ez-chip ez-chip-green">{totals["Types"]} GREEN TYPES</span>
        <div class="ez-kv"><span>Record</span><span>{totals["Record"]}</span></div>
        <div class="ez-kv"><span>Total Bets</span><span>{totals["Total Bets"]}</span></div>
        <div class="ez-kv"><span>Win %</span><span>{totals["Win %"]}%</span></div>
        <div class="ez-kv"><span>Units / ROI</span><span>{totals["Units Won"]}u / {totals["ROI %"]}%</span></div>
    </div>
    """, unsafe_allow_html=True)


def render_bet_tracker():
    st.header("Bet Tracker")

    tracker_df = load_tracker()
    tracker_df = sort_tracker_by_model_pct(tracker_df)
    summary_df = tracker_summary_dataframe(tracker_df)

    mode = st.radio(
        "Bet tracker action",
        ["Pending", "Completed", "Records", "Delete"],
        horizontal=True,
        label_visibility="collapsed",
        key="bet_tracker_mode"
    )

    if mode == "Pending":
        st.subheader("Pending Bets")
        pending = tracker_df[tracker_df["Result"] == "Pending"].copy() if not tracker_df.empty else pd.DataFrame()
        pending = sort_tracker_by_model_pct(pending)

        if pending.empty:
            st.info("No pending bets.")
            return

        st.caption("Tap Win, Loss, Push, or Delete. This is easier on your phone than editing a table.")
        for idx, bet in pending.iterrows():
            model = bet.get("Model %", "")
            edge = bet.get("Edge %", "")
            st.markdown(f"""
            <div class="ez-card">
                <div class="ez-title">{esc(str(bet.get("Bet Type", "")).upper())}</div>
                <div class="ez-sub">{esc(bet.get("Selection", ""))}</div>
                <span class="ez-chip ez-chip-green">{esc(bet.get("Market", ""))}</span>
                <div class="ez-kv"><span>Odds / Line</span><span>{esc(bet.get("Odds/Line", ""))}</span></div>
                <div class="ez-kv"><span>Model</span><span>{esc(model)}</span></div>
                <div class="ez-kv"><span>Edge</span><span>{esc(edge)}</span></div>
            </div>
            """, unsafe_allow_html=True)
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                if st.button("✅ Win", key=f"pending_win_{idx}"):
                    tracker_df.loc[idx, "Result"] = "Win"
                    save_tracker(tracker_df)
                    st.rerun()
            with c2:
                if st.button("❌ Loss", key=f"pending_loss_{idx}"):
                    tracker_df.loc[idx, "Result"] = "Loss"
                    save_tracker(tracker_df)
                    st.rerun()
            with c3:
                if st.button("➖ Push", key=f"pending_push_{idx}"):
                    tracker_df.loc[idx, "Result"] = "Push"
                    save_tracker(tracker_df)
                    st.rerun()
            with c4:
                if st.button("🗑️", key=f"pending_delete_{idx}"):
                    tracker_df = tracker_df.drop(index=idx)
                    save_tracker(tracker_df)
                    st.rerun()
            st.divider()

        with st.expander("Table editor fallback"):
            display_tracker_dataframe(pending)

    elif mode == "Completed":
        st.subheader("Completed Bets")
        completed = tracker_df[tracker_df["Result"].isin(["Win", "Loss", "Push"])].copy() if not tracker_df.empty else pd.DataFrame()
        completed = sort_tracker_by_model_pct(completed)
        if completed.empty:
            st.info("No completed bets yet.")
        else:
            display_tracker_dataframe(completed)

    elif mode == "Records":
        st.subheader("Green Bet Totals")
        render_green_totals_card(summary_df)
        st.divider()
        st.subheader("Bet Type Breakdown")
        render_record_cards(summary_df)
        with st.expander("Full tracker table"):
            display_tracker_dataframe(tracker_df)

    else:
        st.subheader("Delete Bets")
        st.caption("Use this for older mistakes. Pending bets can also be deleted from the Pending view.")
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
                key="delete_bets_editor_mobile"
            )
            if st.button("Delete Selected Bets", key="delete_selected_bets_mobile"):
                rows_to_delete = edited_delete_view.loc[edited_delete_view["Delete"] == True, "Row ID"].tolist()
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
    "Game Key",
    "Game Label",
    "Away Team",
    "Home Team",
    "Better ML",
    "ML Odds",
    "ML Grade",
    "NRFI Grade",
    "NRFI Score",
    "NRFI Probability",
    "NRFI Odds",
    "YRFI Score",
    "YRFI Probability",
    "YRFI Odds",
    "Away Pitcher K + Grade",
    "Away Pitcher K Score",
    "Home Pitcher K + Grade",
    "Home Pitcher K Score",
    "Total Runs Projection",
    "Total Runs Grade",
    "Total Runs Line",
    "Away Pitcher K Reliability", "Away Pitcher K Probability",
    "Home Pitcher K Reliability", "Home Pitcher K Probability",
    "Away Bulk Pitcher K + Grade", "Away Bulk Pitcher K Score", "Away Bulk Pitcher K Reliability",
    "Home Bulk Pitcher K + Grade", "Home Bulk Pitcher K Score", "Home Bulk Pitcher K Reliability",
    "Total Selected Probability", "Total Reliability", "Model Version",
    "Favorite Pick",
    "Handpicked Record",
    "Favorite Rank",
    "Favorite Tag",
    "Favorite Notes"
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
    home_pitcher_k_score,
    total_runs_projection_value="",
    total_runs_grade="",
    total_runs_line="",
    game_id="",
    game_label="",
    slate_date=None,
    nrfi_score_value="",
    nrfi_probability_value="",
    nrfi_odds_value="",
    away_k_reliability="", away_k_probability="", home_k_reliability="", home_k_probability="",
    away_bulk_summary="", away_bulk_score="", away_bulk_reliability="",
    home_bulk_summary="", home_bulk_score="", home_bulk_reliability="",
    total_selected_probability="", total_reliability=""
):
    df = load_slate()
    save_date = str(slate_date or date.today())
    game_id_text = str(game_id).strip()

    first_inning_grade = str(nrfi_grade or "").strip().upper()
    is_yrfi_play = "YRFI" in first_inning_grade

    # Save NRFI and YRFI metrics in separate columns. The public site interprets
    # a value under "NRFI Score" as an NRFI-side score and flips it for YRFI.
    # Keeping the selected YRFI score in a dedicated column prevents a second
    # inversion and ensures the public tile receives the correct EZPZ score.
    new_row = {
        "Date": save_date,
        "Game Key": game_id_text,
        "Game Label": game_label or f"{away_team} at {home_team}",
        "Away Team": away_team,
        "Home Team": home_team,
        "Better ML": better_ml,
        "ML Odds": ml_odds,
        "ML Grade": ml_grade,
        "NRFI Grade": nrfi_grade,
        "NRFI Score": "" if is_yrfi_play else nrfi_score_value,
        "NRFI Probability": "" if is_yrfi_play else nrfi_probability_value,
        "NRFI Odds": "" if is_yrfi_play else nrfi_odds_value,
        "YRFI Score": nrfi_score_value if is_yrfi_play else "",
        "YRFI Probability": nrfi_probability_value if is_yrfi_play else "",
        "YRFI Odds": nrfi_odds_value if is_yrfi_play else "",
        "Away Pitcher K + Grade": away_pitcher_k_grade,
        "Away Pitcher K Score": away_pitcher_k_score,
        "Home Pitcher K + Grade": home_pitcher_k_grade,
        "Home Pitcher K Score": home_pitcher_k_score,
        "Total Runs Projection": total_runs_projection_value,
        "Total Runs Grade": total_runs_grade,
        "Total Runs Line": total_runs_line,
        "Away Pitcher K Reliability": away_k_reliability,
        "Away Pitcher K Probability": away_k_probability,
        "Home Pitcher K Reliability": home_k_reliability,
        "Home Pitcher K Probability": home_k_probability,
        "Away Bulk Pitcher K + Grade": away_bulk_summary,
        "Away Bulk Pitcher K Score": away_bulk_score,
        "Away Bulk Pitcher K Reliability": away_bulk_reliability,
        "Home Bulk Pitcher K + Grade": home_bulk_summary,
        "Home Bulk Pitcher K Score": home_bulk_score,
        "Home Bulk Pitcher K Reliability": home_bulk_reliability,
        "Total Selected Probability": total_selected_probability,
        "Total Reliability": total_reliability,
        "Model Version": MODEL_VERSION
    }

    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    save_slate(df)



# -----------------------
# TEMPORARY MATCHUP DETAILS STORAGE
# -----------------------

MATCHUP_DETAILS_COLUMNS = [
    "Date",
    "Saved Time ET",
    "Game Key",
    "Game Label",
    "Away Team",
    "Home Team",
    "Away Pitcher",
    "Home Pitcher",
    "Summary",
    "Details JSON"
]


def load_matchup_details():
    return read_sheet(MATCHUP_DETAILS_TAB, MATCHUP_DETAILS_COLUMNS)


def save_matchup_details(df):
    return write_sheet(MATCHUP_DETAILS_TAB, df, MATCHUP_DETAILS_COLUMNS)


MATCHUP_DETAILS_CELL_CHAR_LIMIT = 45000


def _safe_json_value(value, max_rows=8, max_text_chars=300):
    """Convert Streamlit/pandas objects into compact Google-Sheets-safe JSON.

    Google Sheets has a hard 50,000-character limit per cell. Matchup details are
    stored in one JSON cell, so tables and long text values must be aggressively
    trimmed before save. The important diagnostic fields still remain visible on
    the Matchup Details page.
    """
    try:
        if isinstance(value, pd.DataFrame):
            if value.empty:
                return []
            return value.head(max_rows).fillna("").astype(str).to_dict("records")
    except Exception:
        pass

    if isinstance(value, dict):
        return {str(k): _safe_json_value(v, max_rows=max_rows, max_text_chars=max_text_chars) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_safe_json_value(v, max_rows=max_rows, max_text_chars=max_text_chars) for v in list(value)[:max_rows]]

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    try:
        if isinstance(value, str):
            text = value.strip()
            return text if len(text) <= max_text_chars else text[:max_text_chars] + "…"
        if isinstance(value, (int, float, bool)) or value is None:
            return value
    except Exception:
        pass

    text = str(value)
    return text if len(text) <= max_text_chars else text[:max_text_chars] + "…"


def _compact_rows(rows, max_rows=10):
    """Return a small list of dict rows for display tables."""
    try:
        if isinstance(rows, pd.DataFrame):
            rows = rows.fillna("").astype(str).to_dict("records")
        elif not isinstance(rows, list):
            return []
        return [_safe_json_value(r, max_rows=0, max_text_chars=120) for r in rows[:max_rows]]
    except Exception:
        return []


def _compact_arsenal_details(arsenal):
    if not isinstance(arsenal, dict):
        return {}
    keep = {}
    for key in [
        "status", "modifier", "score", "base_projection", "adjusted_projection",
        "weapon_count", "avg_weapon_advantage", "pitch_types_used"
    ]:
        if key in arsenal:
            keep[key] = _safe_json_value(arsenal.get(key), max_rows=4, max_text_chars=160)

    leash = arsenal.get("opponent_leash", {})
    if isinstance(leash, dict):
        keep["opponent_leash"] = {
            k: _safe_json_value(leash.get(k), max_rows=3, max_text_chars=120)
            for k in ["tier", "multiplier", "final_score", "z_score", "base_ip", "adjusted_ip", "ip_impact", "k_impact"]
            if k in leash
        }

    # These rows are the key arsenal/weapon diagnostic. Keep enough to debug,
    # but not enough to break the 50k Sheets cell limit.
    for row_key in ["details", "weapon_details", "detail_rows"]:
        if row_key in arsenal:
            compact = _compact_rows(arsenal.get(row_key), max_rows=12)
            if compact:
                keep["details"] = compact
                break
    return keep


def _compact_lineup_details(lineup):
    if not isinstance(lineup, dict):
        return {}
    keep = {}
    for key in [
        "source", "status", "hitters_found", "lineup_k_rate", "team_baseline_k_rate",
        "blended_k_rate", "hand_stack_multiplier", "pitch_type_multiplier", "pitch_type_matchup", "multiplier",
        "lineup_offense_score", "lineup_low_sample_hitters", "lineup_strength_status", "lineup_obp", "lineup_slg", "lineup_ops"
    ]:
        if key in lineup:
            keep[key] = _safe_json_value(lineup.get(key), max_rows=3, max_text_chars=160)

    hitters = lineup.get("hitters", [])
    compact_hitters = _compact_rows(hitters, max_rows=9)
    if compact_hitters:
        keep["hitters"] = compact_hitters
    return keep


def _compact_pitcher_details(pdata):
    if not isinstance(pdata, dict):
        return {}
    keep = {}
    for key in [
        "pitcher", "team", "opponent", "expected_ks", "raw_expected_ks", "six_ip_ks",
        "line", "odds", "edge", "variance", "volatility",
        "recent_form_note", "recent_accuracy_note", "six_inning_override_note", "weapon_floor_note",
        "k_context_note", "k_context", "grade", "k_score"
    ]:
        if key in pdata:
            keep[key] = _safe_json_value(pdata.get(key), max_rows=4, max_text_chars=240)
    if "recent_form" in pdata:
        keep["recent_form"] = _safe_json_value(pdata.get("recent_form"), max_rows=5, max_text_chars=180)
    keep["arsenal"] = _compact_arsenal_details(pdata.get("arsenal", {}))
    keep["lineup"] = _compact_lineup_details(pdata.get("lineup", {}))
    return keep


def _compact_matchup_details(details):
    """Keep the useful Matchup Details diagnostics while avoiding oversized JSON."""
    if not isinstance(details, dict):
        return _safe_json_value(details, max_rows=4, max_text_chars=160)

    compact = {}
    if "game_environment" in details:
        compact["game_environment"] = _safe_json_value(details.get("game_environment"), max_rows=4, max_text_chars=180)
    pitchers = details.get("pitchers", {})
    if isinstance(pitchers, dict):
        compact["pitchers"] = {
            "away": _compact_pitcher_details(pitchers.get("away", {})),
            "home": _compact_pitcher_details(pitchers.get("home", {})),
        }

    for section in ["moneyline", "nrfi", "total_runs"]:
        if section in details:
            compact[section] = _safe_json_value(details.get(section), max_rows=4, max_text_chars=180)
    return compact


def _matchup_details_json(details):
    """Serialize matchup details safely under the Google Sheets per-cell limit."""
    compact = _compact_matchup_details(details)
    details_json = json.dumps(compact, ensure_ascii=False)
    if len(details_json) <= MATCHUP_DETAILS_CELL_CHAR_LIMIT:
        return details_json

    # Emergency fallback: keep only the most important pitcher diagnostics.
    pitchers = compact.get("pitchers", {}) if isinstance(compact, dict) else {}
    emergency = {"pitchers": {}}
    for side in ["away", "home"]:
        pdata = pitchers.get(side, {}) if isinstance(pitchers, dict) else {}
        arsenal = pdata.get("arsenal", {}) if isinstance(pdata.get("arsenal", {}), dict) else {}
        emergency["pitchers"][side] = {
            "pitcher": pdata.get("pitcher", ""),
            "team": pdata.get("team", ""),
            "opponent": pdata.get("opponent", ""),
            "expected_ks": pdata.get("expected_ks", ""),
            "raw_expected_ks": pdata.get("raw_expected_ks", ""),
            "six_ip_ks": pdata.get("six_ip_ks", ""),
            "line": pdata.get("line", ""),
            "odds": pdata.get("odds", ""),
            "edge": pdata.get("edge", ""),
            "grade": pdata.get("grade", ""),
            "k_score": pdata.get("k_score", ""),
            "arsenal": {
                "status": arsenal.get("status", ""),
                "modifier": arsenal.get("modifier", ""),
                "score": arsenal.get("score", ""),
                "base_projection": arsenal.get("base_projection", ""),
                "adjusted_projection": arsenal.get("adjusted_projection", ""),
                "details": _compact_rows(arsenal.get("details", []), max_rows=6),
            },
        }
    return json.dumps(emergency, ensure_ascii=False)


def clean_old_matchup_details(details_date=None):
    """Physically remove old matchup detail rows so this temporary tab stays small."""
    today_text = str(details_date or date.today())
    df = load_matchup_details()
    if df.empty or "Date" not in df.columns:
        return
    keep = df[df["Date"].astype(str) == today_text].copy()
    if len(keep) != len(df):
        save_matchup_details(keep)


def add_matchup_detail_row(
    game_key,
    game_label,
    away_team,
    home_team,
    away_pitcher,
    home_pitcher,
    summary,
    details,
    details_date=None
):
    """Save a same-day matchup detail snapshot when the normal matchup save button is pressed."""
    save_date = str(details_date or date.today())

    # Auto-clear old dates before every save, so the sheet tab does not build up.
    clean_old_matchup_details(save_date)

    df = load_matchup_details()

    game_key_text = str(game_key or "").strip()
    game_label_text = str(game_label or f"{away_team} at {home_team}").strip()

    # Replace the same game if it is saved again today.
    if not df.empty:
        if "Date" in df.columns and "Game Key" in df.columns and game_key_text:
            df = df[~((df["Date"].astype(str) == save_date) & (df["Game Key"].astype(str) == game_key_text))].copy()
        elif "Date" in df.columns and "Game Label" in df.columns:
            df = df[~((df["Date"].astype(str) == save_date) & (df["Game Label"].astype(str) == game_label_text))].copy()

    details_json = _matchup_details_json(details)

    new_row = {
        "Date": save_date,
        "Saved Time ET": eastern_now().strftime("%Y-%m-%d %I:%M %p ET"),
        "Game Key": game_key_text,
        "Game Label": game_label_text,
        "Away Team": away_team,
        "Home Team": home_team,
        "Away Pitcher": away_pitcher,
        "Home Pitcher": home_pitcher,
        "Summary": summary,
        "Details JSON": details_json
    }

    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    return save_matchup_details(df)


def clear_todays_matchup_details(details_date=None):
    today_text = str(details_date or date.today())
    df = load_matchup_details()
    if df.empty:
        return True
    keep = df[df["Date"].astype(str) != today_text].copy()
    return save_matchup_details(keep)


def _detail_metric(label, value):
    st.markdown(f"""
    <div class="ez-kv"><span>{esc(label)}</span><span>{esc(value)}</span></div>
    """, unsafe_allow_html=True)


def _builder_metric_card_html(label, value, wide=False, big=False):
    value = "" if value is None else value
    classes = ["builder-metric-card"]
    if wide:
        classes.append("builder-metric-card--wide")
    value_class = "builder-metric-value builder-metric-value--big" if big else "builder-metric-value"
    return (
        f'<div class="{" ".join(classes)}">'
        f'<div class="builder-metric-label">{esc(label)}</div>'
        f'<div class="{value_class}">{esc(value)}</div>'
        f'</div>'
    )


def render_builder_metric_grid(items):
    """Render compact builder metric bubbles.

    Each item is a dict with keys: label, value, and optional wide/big booleans.
    This keeps mobile cards smaller and allows long values to wrap instead of clipping.
    """
    cells = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cells.append(
            _builder_metric_card_html(
                item.get("label", ""),
                item.get("value", ""),
                wide=bool(item.get("wide", False)),
                big=bool(item.get("big", False)),
            )
        )
    st.markdown(f'<div class="builder-metric-grid">{"".join(cells)}</div>', unsafe_allow_html=True)


def render_matchup_details():
    st.header("Saved Matchup Details")
    st.caption("Temporary same-day matchup snapshots. Old dates auto-clear the next time you save a matchup.")

    if st.button("Update Pitcher Recent Form Results", key="update_recent_form_results_details"):
        result = update_pitcher_recent_form_actuals(auto_only=False)
        st.success(result.get("message", "Recent form update complete."))

    today_text = str(date.today())
    clean_old_matchup_details(today_text)
    details_df = load_matchup_details()

    c1, c2 = st.columns([2, 1])
    with c1:
        st.info("These details are saved automatically when you press Save Matchup Summary.")
    with c2:
        if st.button("Clear Today's Details", key="clear_matchup_details_today"):
            if clear_todays_matchup_details(today_text):
                st.success("Today's matchup details cleared.")
                st.rerun()
            else:
                st.error("Could not clear today's matchup details.")

    if details_df.empty:
        st.info("No matchup details saved for today yet.")
        return

    today_df = details_df[details_df["Date"].astype(str) == today_text].copy()
    if today_df.empty:
        st.info("No matchup details saved for today yet.")
        return

    options = today_df["Game Label"].astype(str).tolist()
    selected = st.selectbox("Choose saved matchup", options, key="saved_matchup_detail_select")
    row = today_df[today_df["Game Label"].astype(str) == selected].iloc[0]

    st.subheader(row.get("Game Label", selected))
    st.caption(f"Saved: {row.get('Saved Time ET', '')}")

    try:
        details = json.loads(str(row.get("Details JSON", "{}") or "{}"))
    except Exception:
        details = {}

    st.markdown(f"""
    <div class="ez-card ez-card-green">
        <div class="ez-title">{esc(row.get("Game Label", selected))}</div>
        <div class="ez-sub">{esc(row.get("Away Pitcher", ""))} vs {esc(row.get("Home Pitcher", ""))}</div>
        <div class="ez-kv"><span>Summary</span><span>{esc(row.get("Summary", ""))}</span></div>
    </div>
    """, unsafe_allow_html=True)

    pitchers = details.get("pitchers", {}) if isinstance(details, dict) else {}
    for side_label in ["away", "home"]:
        pdata = pitchers.get(side_label, {}) if isinstance(pitchers, dict) else {}
        if not pdata:
            continue
        with st.expander(f"{side_label.title()} Pitcher: {pdata.get('pitcher', '')}", expanded=True):
            _detail_metric("Team", pdata.get("team", ""))
            _detail_metric("Opponent", pdata.get("opponent", ""))
            _detail_metric("Expected Ks", pdata.get("expected_ks", ""))
            _detail_metric("Projection Before Lineup", pdata.get("raw_expected_ks", ""))
            _detail_metric("6-IP Pace", pdata.get("six_ip_ks", ""))
            _detail_metric("Line / Odds", f"{pdata.get('line', '')} / {pdata.get('odds', '')}")
            _detail_metric("Edge", pdata.get("edge", ""))
            _detail_metric("Variance", pdata.get("variance", ""))
            _detail_metric("Volatility", pdata.get("volatility", ""))
            recent_form = pdata.get("recent_form", {}) if isinstance(pdata.get("recent_form", {}), dict) else {}
            _detail_metric("Recent Form", _recent_form_display(recent_form) if recent_form else "No History")
            if pdata.get("recent_form_note", ""):
                st.warning(pdata.get("recent_form_note", ""))
            if pdata.get("recent_accuracy_note", ""):
                st.warning(pdata.get("recent_accuracy_note", ""))
            if pdata.get("six_inning_override_note", ""):
                st.success(pdata.get("six_inning_override_note", ""))
            if pdata.get("weapon_floor_note", ""):
                st.warning(pdata.get("weapon_floor_note", ""))
            _detail_metric("Bet Grade", pdata.get("grade", ""))
            _detail_metric("K Score", pdata.get("k_score", ""))

            arsenal = pdata.get("arsenal", {}) if isinstance(pdata.get("arsenal", {}), dict) else {}
            if arsenal:
                leash = arsenal.get("opponent_leash", {}) if isinstance(arsenal.get("opponent_leash", {}), dict) else {}
                if leash:
                    st.markdown("**Opponent Leash**")
                    _detail_metric("Tier / Multiplier", f"{leash.get('tier', '')} / {leash.get('multiplier', '')}")
                    _detail_metric("Score / Z", f"{leash.get('final_score', '')} / {leash.get('z_score', '')}")
                    _detail_metric("IP Impact", f"{leash.get('base_ip', '')} → {leash.get('adjusted_ip', '')} ({leash.get('ip_impact', '')})")
                    _detail_metric("Approx K Impact", leash.get("k_impact", ""))
                st.markdown("**Pitch-Type Arsenal**")
                _detail_metric("Status", arsenal.get("status", ""))
                _detail_metric("Modifier", arsenal.get("modifier", ""))
                _detail_metric("Arsenal Score", arsenal.get("score", ""))
                _detail_metric("Base → Adjusted", f"{arsenal.get('base_projection', '')} → {arsenal.get('adjusted_projection', '')}")
                detail_rows = arsenal.get("details", [])
                if detail_rows:
                    st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)

            lineup = pdata.get("lineup", {}) if isinstance(pdata.get("lineup", {}), dict) else {}
            if lineup:
                st.markdown("**Lineup / Hitter Matchup**")
                _detail_metric("Source", lineup.get("source", ""))
                _detail_metric("Status", lineup.get("status", ""))
                _detail_metric("Hitters Found", lineup.get("hitters_found", ""))
                _detail_metric("Lineup K Rate", lineup.get("lineup_k_rate", ""))
                _detail_metric("Team Baseline K Rate", lineup.get("team_baseline_k_rate", ""))
                _detail_metric("Blended K Rate", lineup.get("blended_k_rate", ""))
                _detail_metric("Handedness Multiplier", lineup.get("hand_stack_multiplier", ""))
                _detail_metric("Pitch-Type Multiplier", lineup.get("pitch_type_multiplier", ""))
                ptm = lineup.get("pitch_type_matchup", {}) if isinstance(lineup.get("pitch_type_matchup", {}), dict) else {}
                if ptm:
                    _detail_metric("Hitter Arsenal Score", ptm.get("lineup_arsenal_score", ""))
                    _detail_metric("Hitter Arsenal Modifier", ptm.get("lineup_modifier", ""))
                    _detail_metric("Hitter Weapons", ptm.get("lineup_weapon_count", ""))
                    _detail_metric("Hitter Fallbacks", ptm.get("fallback_hitters", ""))
                _detail_metric("Total Lineup Multiplier", lineup.get("multiplier", ""))
                hitters = lineup.get("hitters", [])
                if hitters:
                    st.dataframe(pd.DataFrame(hitters), use_container_width=True, hide_index=True)

    moneyline = details.get("moneyline", {}) if isinstance(details, dict) else {}
    if moneyline:
        with st.expander("Moneyline Details", expanded=False):
            _detail_metric("Better ML", moneyline.get("better_team", ""))
            _detail_metric("Better ML Odds", moneyline.get("better_odds", ""))
            _detail_metric("Better ML Grade", moneyline.get("better_grade", ""))
            _detail_metric("Better ML Probability", moneyline.get("better_probability", ""))
            _detail_metric("Bullpen Context", moneyline.get("bullpen_context", ""))
            home = moneyline.get("home", {}) if isinstance(moneyline.get("home", {}), dict) else {}
            away = moneyline.get("away", {}) if isinstance(moneyline.get("away", {}), dict) else {}
            st.markdown("**Home**")
            for k, v in home.items():
                _detail_metric(str(k).replace("_", " ").title(), v)
            st.markdown("**Away**")
            for k, v in away.items():
                _detail_metric(str(k).replace("_", " ").title(), v)

    nrfi = details.get("nrfi", {}) if isinstance(details, dict) else {}
    if nrfi:
        with st.expander("NRFI/YRFI Details", expanded=False):
            for k, v in nrfi.items():
                _detail_metric(str(k).replace("_", " ").title(), v)

    with st.expander("Raw Saved JSON", expanded=False):
        st.json(details)


# -----------------------
# ODDS SNAPSHOT STORAGE
# -----------------------

ODDS_SNAPSHOT_COLUMNS = [
    "Date", "Snapshot Time ET", "Market", "Event ID", "Away Team", "Home Team",
    "Player", "Side", "Line", "Odds", "Book", "Source"
]


def eastern_now():
    if ZoneInfo is not None:
        return datetime.now(ZoneInfo("America/New_York"))
    return datetime.now()


def today_et_string():
    return eastern_now().strftime("%Y-%m-%d")


def get_odds_api_key():
    api_key = os.environ.get("THE_ODDS_API_KEY", "")
    if not api_key:
        try:
            api_key = st.secrets.get("THE_ODDS_API_KEY", "")
        except Exception:
            api_key = ""
    return str(api_key).strip()


def load_odds_snapshot():
    return read_sheet(ODDS_SNAPSHOT_TAB, ODDS_SNAPSHOT_COLUMNS)


def save_odds_snapshot(df):
    return write_sheet(ODDS_SNAPSHOT_TAB, df, ODDS_SNAPSHOT_COLUMNS)


def get_snapshot_for_date(snapshot_date=None):
    snapshot_date = snapshot_date or today_et_string()
    df = load_odds_snapshot()
    if df.empty:
        return df
    return df[df["Date"].astype(str) == str(snapshot_date)].copy()


def normalize_team_for_odds(name):
    name = str(name).strip()
    if name == "Oakland Athletics":
        return "Athletics"
    return name


def best_price(existing_price, new_price):
    try:
        if existing_price in [None, "", "nan"] or pd.isna(existing_price):
            return True
    except Exception:
        pass
    try:
        return float(new_price) > float(existing_price)
    except Exception:
        return False


def fetch_odds_snapshot_from_api(snapshot_date=None, include_pitcher_props=False):
    """Pull one daily MONEYLINE-only odds snapshot from The Odds API and return rows ready for Google Sheets.

    Credit-saving rule: this admin version only requests the h2h/moneyline market.
    Pitcher K lines/odds and NRFI/YRFI odds stay manual for now.
    """
    snapshot_date = snapshot_date or today_et_string()
    api_key = get_odds_api_key()
    if not api_key:
        st.error("Missing THE_ODDS_API_KEY. Add it in Render Environment variables first.")
        return pd.DataFrame(columns=ODDS_SNAPSHOT_COLUMNS)

    snap_time = eastern_now().strftime("%Y-%m-%d %I:%M %p ET")
    rows = []

    try:
        response = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds",
            params={"apiKey": api_key, "regions": "us", "markets": "h2h", "oddsFormat": "american", "dateFormat": "iso"},
            timeout=25
        )
        response.raise_for_status()
        games = response.json()
    except Exception as e:
        st.error(f"Could not fetch moneyline odds from The Odds API: {e}")
        return pd.DataFrame(columns=ODDS_SNAPSHOT_COLUMNS)

    for game in games:
        event_id = str(game.get("id", ""))
        home_team = normalize_team_for_odds(game.get("home_team", ""))
        away_team = normalize_team_for_odds(game.get("away_team", ""))
        best_by_team = {}
        for book in game.get("bookmakers", []):
            book_name = book.get("title", "")
            for market in book.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    team = normalize_team_for_odds(outcome.get("name", ""))
                    price = outcome.get("price", "")
                    if price == "":
                        continue
                    if team not in best_by_team or best_price(best_by_team[team].get("Odds"), price):
                        best_by_team[team] = {"Odds": price, "Book": book_name}
        for team, info in best_by_team.items():
            rows.append({
                "Date": snapshot_date, "Snapshot Time ET": snap_time, "Market": "MONEYLINE", "Event ID": event_id,
                "Away Team": away_team, "Home Team": home_team, "Player": "", "Side": team, "Line": "",
                "Odds": info.get("Odds", ""), "Book": info.get("Book", ""), "Source": "The Odds API"
            })

    # Moneyline-only snapshot: intentionally do not fetch pitcher props here.
    return pd.DataFrame(rows, columns=ODDS_SNAPSHOT_COLUMNS)


def ensure_odds_snapshot(snapshot_date=None, force_refresh=False, include_pitcher_props=False):
    snapshot_date = snapshot_date or today_et_string()
    existing_all = load_odds_snapshot()
    existing_today = pd.DataFrame(columns=ODDS_SNAPSHOT_COLUMNS)
    if not existing_all.empty:
        existing_today = existing_all[existing_all["Date"].astype(str) == str(snapshot_date)].copy()
    if not force_refresh and not existing_today.empty:
        return existing_today, False
    new_snapshot = fetch_odds_snapshot_from_api(snapshot_date, include_pitcher_props=include_pitcher_props)
    if new_snapshot.empty:
        return existing_today, False
    if existing_all.empty:
        combined = new_snapshot.copy()
    else:
        old_other_dates = existing_all[existing_all["Date"].astype(str) != str(snapshot_date)].copy()
        combined = pd.concat([old_other_dates, new_snapshot], ignore_index=True)
    save_odds_snapshot(combined)
    return new_snapshot, True


def get_moneyline_defaults_from_snapshot(snapshot_df, home_team, away_team):
    home_odds, away_odds = -110, -110
    if snapshot_df is None or snapshot_df.empty:
        return home_odds, away_odds
    ml = snapshot_df[snapshot_df["Market"].astype(str).str.upper() == "MONEYLINE"].copy()
    for _, row in ml.iterrows():
        side = normalize_team_for_odds(row.get("Side", ""))
        try:
            odds = int(float(row.get("Odds", "")))
        except Exception:
            continue
        if side == normalize_team_for_odds(home_team):
            home_odds = odds
        elif side == normalize_team_for_odds(away_team):
            away_odds = odds
    return home_odds, away_odds


def get_pitcher_k_snapshot_defaults(snapshot_df, pitcher_name, projected_k=None):
    """Manual default for pitcher props.

    We are intentionally NOT using The Odds API for pitcher K props right now to save credits.
    This keeps pitcher K line/odds entry manual in the matchup builder.
    """
    return {"line": 4.5, "odds": -110, "over_odds": "", "under_odds": "", "book": "", "found": False}


def render_odds_snapshot_admin():
    st.header("Odds Snapshot")
    st.caption("Pull moneyline odds once, save them to Google Sheets, then reuse the same moneylines all day across admin, public, and PC versions. Pitcher props and NRFI/YRFI stay manual to save credits.")
    snapshot_date = st.date_input("Snapshot date", value=date.today(), key="odds_snapshot_date").strftime("%Y-%m-%d")
    existing = get_snapshot_for_date(snapshot_date)
    if existing.empty:
        st.warning("No odds snapshot saved for this date yet.")
    else:
        snap_time = existing["Snapshot Time ET"].astype(str).replace("", pd.NA).dropna()
        snap_label = snap_time.iloc[0] if not snap_time.empty else "Saved"
        st.success(f"Snapshot loaded for {snapshot_date}: {len(existing)} rows. Last pull: {snap_label}")
        market_counts = existing["Market"].value_counts().reset_index()
        market_counts.columns = ["Market", "Rows"]
        st.dataframe(market_counts, use_container_width=True, hide_index=True)
    st.markdown("### Actions")
    if st.button("Fetch today's odds snapshot", key="fetch_odds_snapshot"):
        snapshot, pulled = ensure_odds_snapshot(snapshot_date, force_refresh=False, include_pitcher_props=False)
        if pulled:
            st.success(f"Fetched and saved {len(snapshot)} moneyline rows.")
        else:
            st.info("A snapshot already exists for this date, so no API credits were used.")
        st.rerun()
    st.caption("Use this only when you intentionally want updated lines. It spends API credits again and replaces today's saved snapshot.")
    if st.button("Refresh snapshot anyway", key="force_refresh_odds_snapshot"):
        snapshot, pulled = ensure_odds_snapshot(snapshot_date, force_refresh=True, include_pitcher_props=False)
        if pulled:
            st.success(f"Refreshed and saved {len(snapshot)} moneyline rows.")
        else:
            st.error("Could not refresh odds snapshot. Check THE_ODDS_API_KEY and API credits.")
        st.rerun()
    if not existing.empty:
        st.markdown("### Saved Snapshot Preview")
        show = existing[existing["Market"].astype(str).str.upper() == "MONEYLINE"].copy()
        st.dataframe(show, use_container_width=True, hide_index=True)

# -----------------------
# STRIKEOUT MODEL
# -----------------------


# -----------------------
# OPPONENT LEASH MULTIPLIER
# -----------------------
# This adjusts projected innings (not just K rate) for opponents that either
# let starters work deeper or force earlier exits. It is league-relative, so
# teams only earn an extreme multiplier when their blended score is a true
# outlier, instead of forcing a fixed top/bottom 3 every slate.
OPPONENT_LEASH_WEIGHTS = {
    "k_rate": 0.40,
    "contact": 0.25,
    "chase": 0.15,
    "bb_rate": 0.10,
    "swstr": 0.10,
}


def _safe_rate_from_row(row, df, aliases, default=None):
    """Read a percent/rate column when present; return default when absent."""
    try:
        col = _first_existing_col(df, aliases)
    except Exception:
        col = None
    if not col or row is None:
        return default
    try:
        val = row.get(col, default)
        return _to_rate(val, default if default is not None else 0.0)
    except Exception:
        return default


def _opponent_leash_raw_components(row, source_df):
    """Return the raw team inputs used for opponent leash scoring.

    Higher final score = better matchup for starter length/K opportunity.
    Falls back gracefully when public live tables do not include chase/contact.
    """
    ab = _to_number(row.get("At Bats", 0), 0)
    so = _to_number(row.get("Strikeouts", 0), 0)
    bb_raw = row.get("Walks", row.get("Base on Balls", None))
    # If walks are unavailable, use a neutral league-average estimate. The old
    # fallback accidentally treated Batted Balls as walks and could badly distort leash.
    bb = _to_number(bb_raw, ab * 0.085 if ab > 0 else 0)

    k_rate = so / ab if ab > 0 else 0.22
    bb_rate = bb / ab if ab > 0 else 0.085

    # True plate-discipline columns if available.
    contact = _safe_rate_from_row(row, source_df, ["Contact %", "Contact%", "Contact Rate"], None)
    chase = _safe_rate_from_row(row, source_df, ["Chase %", "O-Swing%", "Out of Zone %", "O-Zone%"], None)
    swstr = _safe_rate_from_row(row, source_df, ["SwStr %", "Swinging Strike %", "Whiff %", "Whiff%"], None)

    # Fallback proxies keep the model usable with MLB Stats API team tables.
    # Contact proxy is inverse of K tendency; chase/swstr remain neutral-ish unless present.
    contact_proxy = max(0.62, min(0.86, 0.78 - ((k_rate - 0.22) * 0.75)))
    if contact is None or contact <= 0:
        contact = contact_proxy
    if chase is None or chase <= 0:
        chase = 0.30 + ((k_rate - 0.22) * 0.35)
    if swstr is None or swstr <= 0:
        swstr = 0.115 + ((k_rate - 0.22) * 0.45)

    return {
        "k_rate": max(0.10, min(0.36, k_rate)),
        "bb_rate": max(0.03, min(0.16, bb_rate)),
        "contact": max(0.55, min(0.92, contact)),
        "chase": max(0.20, min(0.42, chase)),
        "swstr": max(0.06, min(0.20, swstr)),
    }


def _percentile(values, value):
    """Simple percentile rank from 0-100."""
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return 50.0
    below = sum(1 for v in vals if v < value)
    equal = sum(1 for v in vals if v == value)
    return ((below + 0.5 * equal) / len(vals)) * 100.0


@st.cache_data(ttl=60 * 60, show_spinner=False)
def build_opponent_leash_table(team_batting_rhp, team_batting_lhp):
    """Build a 30-team league-relative opponent leash table.

    Season score is built from available team batting/discipline inputs.
    Recent score uses recent columns if your data source has them; otherwise it
    falls back to season score. Final score = 70% season / 30% recent.
    Multipliers are assigned by z-score thresholds, not forced buckets.
    """
    frames = []
    for df in [team_batting_rhp, team_batting_lhp]:
        if df is not None and not df.empty and "Teams" in df.columns:
            frames.append(df.copy())
    if not frames:
        return pd.DataFrame(columns=["Team", "Season Score", "Recent Score", "Final Score", "Z-Score", "Multiplier", "Tier"])

    combined = pd.concat(frames, ignore_index=True)
    combined["_team_key"] = combined["Teams"].astype(str).apply(normalize_name_for_match)

    team_rows = []
    for team_key, group in combined.groupby("_team_key"):
        if not team_key:
            continue
        team_name = str(group.iloc[0].get("Teams", "")).strip()
        # Aggregate numeric inputs across RHP/LHP rows.
        agg = group.iloc[0].copy()
        for col in ["At Bats", "Strikeouts", "Walks", "Base on Balls", "Batted Balls"]:
            if col in group.columns:
                agg[col] = pd.to_numeric(group[col], errors="coerce").fillna(0).sum()
        comps = _opponent_leash_raw_components(agg, combined)
        team_rows.append({"Team": team_name, **comps})

    raw = pd.DataFrame(team_rows)
    if raw.empty:
        return pd.DataFrame(columns=["Team", "Season Score", "Recent Score", "Final Score", "Z-Score", "Multiplier", "Tier"])

    # Convert each input into league-relative percentiles. For contact and BB%, lower is better.
    k_vals = raw["k_rate"].tolist()
    bb_vals = raw["bb_rate"].tolist()
    contact_vals = raw["contact"].tolist()
    chase_vals = raw["chase"].tolist()
    swstr_vals = raw["swstr"].tolist()

    scores = []
    for _, row in raw.iterrows():
        k_score = _percentile(k_vals, row["k_rate"])
        contact_score = 100.0 - _percentile(contact_vals, row["contact"])
        chase_score = _percentile(chase_vals, row["chase"])
        bb_score = 100.0 - _percentile(bb_vals, row["bb_rate"])
        swstr_score = _percentile(swstr_vals, row["swstr"])
        season_score = (
            OPPONENT_LEASH_WEIGHTS["k_rate"] * k_score +
            OPPONENT_LEASH_WEIGHTS["contact"] * contact_score +
            OPPONENT_LEASH_WEIGHTS["chase"] * chase_score +
            OPPONENT_LEASH_WEIGHTS["bb_rate"] * bb_score +
            OPPONENT_LEASH_WEIGHTS["swstr"] * swstr_score
        )

        # Recent-form hook: if future data includes recent columns, wire them here.
        # Current live MLB team table is season-only, so recent = season fallback.
        recent_score = season_score
        final_score = (0.70 * season_score) + (0.30 * recent_score)
        scores.append({
            "Team": row["Team"],
            "K%": row["k_rate"],
            "BB%": row["bb_rate"],
            "Contact%": row["contact"],
            "Chase%": row["chase"],
            "SwStr%": row["swstr"],
            "Season Score": season_score,
            "Recent Score": recent_score,
            "Final Score": final_score,
        })

    out = pd.DataFrame(scores)
    mean = float(out["Final Score"].mean()) if not out.empty else 50.0
    std = float(out["Final Score"].std(ddof=0)) if len(out) > 1 else 0.0
    if std <= 0:
        out["Z-Score"] = 0.0
    else:
        out["Z-Score"] = (out["Final Score"] - mean) / std

    def bucket_from_z(z):
        z = float(z)
        if z >= 1.25:
            return 1.15, "Extreme Positive"
        if z >= 0.50:
            return 1.08, "Positive"
        if z > -0.50:
            return 1.00, "Neutral"
        if z > -1.25:
            return 0.93, "Negative"
        return 0.85, "Extreme Negative"

    buckets = out["Z-Score"].apply(bucket_from_z)
    out["Multiplier"] = buckets.apply(lambda x: x[0])
    out["Tier"] = buckets.apply(lambda x: x[1])
    return out.sort_values("Final Score", ascending=False).reset_index(drop=True)


def get_opponent_leash_details(opponent, base_exp_ip, k_per_bf, team_batting_rhp, team_batting_lhp):
    table = build_opponent_leash_table(team_batting_rhp, team_batting_lhp)
    neutral = {
        "opponent": opponent,
        "tier": "Neutral",
        "multiplier": 1.00,
        "season_score": 50.0,
        "recent_score": 50.0,
        "final_score": 50.0,
        "z_score": 0.0,
        "base_ip": round(float(base_exp_ip or 0), 2),
        "adjusted_ip": round(float(base_exp_ip or 0), 2),
        "ip_impact": 0.0,
        "k_impact": 0.0,
        "status": "Neutral - opponent leash table unavailable",
        "table": table,
    }
    if table is None or table.empty:
        return neutral
    temp = table.copy()
    temp["_team_key"] = temp["Team"].apply(normalize_name_for_match)
    keys = _team_keys(opponent)
    match = temp[temp["_team_key"].isin(keys)]
    if match.empty:
        neutral["status"] = "Neutral - opponent not matched in leash table"
        neutral["table"] = table
        return neutral
    row = match.iloc[0]
    mult = float(row.get("Multiplier", 1.0) or 1.0)
    base_ip = float(base_exp_ip or 0.0)
    adjusted_ip = max(2.5, min(7.25, base_ip * mult))
    ip_impact = adjusted_ip - base_ip
    # Approximate K impact from added/removed batters faced. k_per_bf is the pitcher's own K/BF baseline.
    k_impact = ip_impact * 4.3 * float(k_per_bf or 0.0)
    return {
        "opponent": opponent,
        "tier": row.get("Tier", "Neutral"),
        "multiplier": mult,
        "season_score": round(float(row.get("Season Score", 50.0)), 1),
        "recent_score": round(float(row.get("Recent Score", 50.0)), 1),
        "final_score": round(float(row.get("Final Score", 50.0)), 1),
        "z_score": round(float(row.get("Z-Score", 0.0)), 2),
        "base_ip": round(base_ip, 2),
        "adjusted_ip": round(adjusted_ip, 2),
        "ip_impact": round(ip_impact, 2),
        "k_impact": round(k_impact, 2),
        "k_pct": round(float(row.get("K%", 0.0)) * 100, 1),
        "bb_pct": round(float(row.get("BB%", 0.0)) * 100, 1),
        "contact_pct": round(float(row.get("Contact%", 0.0)) * 100, 1),
        "chase_pct": round(float(row.get("Chase%", 0.0)) * 100, 1),
        "swstr_pct": round(float(row.get("SwStr%", 0.0)) * 100, 1),
        "status": f"{row.get('Tier', 'Neutral')} opponent leash: z={float(row.get('Z-Score', 0.0)):+.2f}, multiplier {mult:.2f}",
        "table": table,
    }

def _pitcher_role_workload(df, pitcher):
    """Estimate starter workload without dividing all relief innings by a tiny GS count.

    Full-time starters can safely use IP/GS. Relievers and hybrid pitchers need a
    spot-start workload estimate because their season IP includes relief outings.
    """
    ip = float(get_value(df, "Player", pitcher, "IP", 0) or 0)
    games = float(get_value(df, "Player", pitcher, "G", 0) or 0)
    starts = float(get_value(df, "Player", pitcher, "GS", get_value(df, "Player", pitcher, "Games Started", 0)) or 0)
    start_share = starts / games if games > 0 else 0.0
    appearance_ip = ip / games if games > 0 else 0.0

    if starts >= 3 and start_share >= 0.50:
        estimated_ip = ip / starts
        source = "IP/GS for established starter"
    elif starts > 0:
        # Total season IP is contaminated by relief innings, so do not use IP/GS.
        estimated_ip = 4.15 + max(-0.35, min(0.75, (appearance_ip - 1.0) * 0.45))
        source = "hybrid/spot-start workload fallback"
    elif games > 0:
        estimated_ip = 4.00
        source = "reliever making probable start fallback"
    else:
        estimated_ip = 0.0
        source = "no workload history"

    if estimated_ip > 0:
        estimated_ip = max(2.75, min(7.10, estimated_ip))
    return {
        "ip": ip,
        "games": games,
        "starts": starts,
        "start_share": start_share,
        "appearance_ip": appearance_ip,
        "estimated_start_ip": estimated_ip,
        "source": source,
    }


def _blended_pitcher_start_ip(pitcher, pitcher_this_year, pitcher_last_year):
    current = _pitcher_role_workload(pitcher_this_year, pitcher)
    prior = _pitcher_role_workload(pitcher_last_year, pitcher)
    this_ip = float(current.get("estimated_start_ip", 0) or 0)
    last_ip = float(prior.get("estimated_start_ip", 0) or 0)

    if this_ip > 0 and last_ip > 0:
        estimate = (0.65 * this_ip) + (0.35 * last_ip)
    elif this_ip > 0:
        estimate = this_ip
    elif last_ip > 0:
        estimate = last_ip
    else:
        estimate = 5.0

    estimate = max(2.75, min(7.10, estimate))
    relief_or_hybrid = (
        float(current.get("start_share", 0) or 0) < 0.50
        or float(prior.get("start_share", 0) or 0) < 0.50
    )
    return estimate, current, prior, relief_or_hybrid


def expected_strikeouts(pitcher, opponent, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df=None, team_pitch_type_df=None, return_details=False):
    so_last = get_value(pitcher_last_year, "Player", pitcher, "SO", 0)
    so_this = get_value(pitcher_this_year, "Player", pitcher, "SO", 0)

    ip_last = get_value(pitcher_last_year, "Player", pitcher, "IP", 0)
    ip_this = get_value(pitcher_this_year, "Player", pitcher, "IP", 0)

    workload_ip, workload_this, workload_last, relief_or_hybrid = _blended_pitcher_start_ip(
        pitcher, pitcher_this_year, pitcher_last_year
    )
    g_last = float(workload_last.get("games", 0) or 0)
    g_this = float(workload_this.get("games", 0) or 0)
    gs_last = float(workload_last.get("starts", 0) or 0)
    gs_this = float(workload_this.get("starts", 0) or 0)
    ipg_last = float(workload_last.get("estimated_start_ip", 0) or 0)
    ipg_this = float(workload_this.get("estimated_start_ip", 0) or 0)

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

    relief_flag = 1 if relief_or_hybrid else 0

    if relief_flag == 1:
        k_per_bf = ((0.3 * so_last) + (0.7 * so_this)) / max(1, (0.3 * bf_last) + (0.7 * bf_this))
    else:
        k_per_bf = ((0.6 * so_last) + (0.4 * so_this)) / max(1, (0.6 * bf_last) + (0.4 * bf_this))

    exp_ip = float(workload_ip or 5.0)
    # A probable starter may pitch beyond six, but no workload input should ever
    # exceed a realistic starter ceiling before opponent leash is applied.
    exp_ip = max(2.75, min(7.10, exp_ip))

    # Opponent Leash adjusts innings only. Build the true 6-inning pace first,
    # then scale it evenly by adjusted expected innings:
    #   Expected Ks = 6-IP Pace * (Adjusted Expected IP / 6)
    # This prevents workload/leash from being applied as an extra nonlinear multiplier.
    base_exp_ip = exp_ip
    opponent_leash = get_opponent_leash_details(opponent, base_exp_ip, k_per_bf, team_batting_rhp, team_batting_lhp)
    exp_ip = float(opponent_leash.get("adjusted_ip", base_exp_ip) or base_exp_ip)
    ip_scale = max(0.0, float(exp_ip or 0.0)) / 6.0
    six_ip_bf = 6.0 * 4.3

    generic_six_projection = k_per_bf * six_ip_bf * opp_mult * 0.96
    arsenal_six_projection = k_per_bf * six_ip_bf * opp_obp_mult * 0.96
    adjusted_six_projection, arsenal_details = apply_pitch_type_modifier(arsenal_six_projection, pitcher, opponent, pitcher_arsenal_df, team_pitch_type_df)
    if int(arsenal_details.get("scored_count", 0) or 0) > 0:
        base_projection = arsenal_six_projection * ip_scale
        no_leash_projection = adjusted_six_projection * (max(0.0, float(base_exp_ip or 0.0)) / 6.0)
        adjusted_projection = max(0.0, adjusted_six_projection * ip_scale)

        # Weapon-aware leash cap:
        # 2+ true weapons mean the pitcher can beat a slightly shorter leash by getting Ks faster.
        # Keep leash in the model, but do not let it erase elite arsenal/weapon spots.
        try:
            weapon_count_for_leash = int(arsenal_details.get("weapon_count", 0) or 0)
        except Exception:
            weapon_count_for_leash = 0
        leash_penalty = adjusted_projection - no_leash_projection
        leash_penalty_cap = None
        if weapon_count_for_leash >= 3:
            leash_penalty_cap = -0.15
        elif weapon_count_for_leash == 2:
            leash_penalty_cap = -0.25
        if leash_penalty_cap is not None and leash_penalty < leash_penalty_cap:
            adjusted_projection = max(0.0, no_leash_projection + leash_penalty_cap)
            opponent_leash["raw_k_impact"] = opponent_leash.get("k_impact", 0.0)
            opponent_leash["k_impact"] = round(adjusted_projection - no_leash_projection, 2)
            opponent_leash["status"] = f"{opponent_leash.get('status', '')} | Weapon leash cap applied ({weapon_count_for_leash} weapons, max {leash_penalty_cap:+.2f} K)."
            opponent_leash["weapon_leash_cap"] = leash_penalty_cap
    else:
        # Fallback to the old generic opponent-K matchup when pitch-type data is unavailable.
        base_projection = generic_six_projection * ip_scale
        adjusted_projection = max(0.0, generic_six_projection * ip_scale)

    selected_six_projection = adjusted_six_projection if int(arsenal_details.get("scored_count", 0) or 0) > 0 else generic_six_projection
    absolute_expected_ceiling = max(0.0, float(selected_six_projection or 0.0) * (7.25 / 6.0))
    if absolute_expected_ceiling > 0 and adjusted_projection > absolute_expected_ceiling:
        adjusted_projection = absolute_expected_ceiling
        opponent_leash["status"] = f"{opponent_leash.get('status', '')} | Expected-K hard cap applied at 7.25 IP equivalent."
        opponent_leash["expected_k_hard_cap"] = round(absolute_expected_ceiling, 2)

    arsenal_details["workload"] = {
        "projected_start_ip": round(float(exp_ip or 0), 2),
        "current_role": workload_this,
        "prior_role": workload_last,
        "hybrid_or_reliever": bool(relief_or_hybrid),
        "status": "Hybrid/reliever workload protection active." if relief_or_hybrid else "Established-starter workload calculation.",
    }
    arsenal_details["opponent_leash"] = opponent_leash
    if return_details:
        arsenal_details["base_projection"] = round(base_projection, 2)
        arsenal_details["adjusted_projection"] = round(adjusted_projection, 2)
        arsenal_details["six_ip_projection"] = round(adjusted_six_projection if int(arsenal_details.get("scored_count", 0) or 0) > 0 else generic_six_projection, 2)
        arsenal_details["ip_scale"] = round(ip_scale, 3)
        return adjusted_projection, arsenal_details
    return adjusted_projection


def six_inning_strikeouts(pitcher, opponent, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df=None, team_pitch_type_df=None, return_details=False, game_location="neutral", umpire_context=None):
    so_last = get_value(pitcher_last_year, "Player", pitcher, "SO", 0)
    so_this = get_value(pitcher_this_year, "Player", pitcher, "SO", 0)

    ip_last = get_value(pitcher_last_year, "Player", pitcher, "IP", 0)
    ip_this = get_value(pitcher_this_year, "Player", pitcher, "IP", 0)

    g_last = get_value(pitcher_last_year, "Player", pitcher, "GS", get_value(pitcher_last_year, "Player", pitcher, "Games Started", get_value(pitcher_last_year, "Player", pitcher, "G", 0)))
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

    generic_base_projection = k_per_bf * (6 * 4.3) * opp_mult * 0.96
    arsenal_base_projection = k_per_bf * (6 * 4.3) * opp_obp_mult * 0.96
    adjusted_projection, arsenal_details = apply_pitch_type_modifier(arsenal_base_projection, pitcher, opponent, pitcher_arsenal_df, team_pitch_type_df)
    if int(arsenal_details.get("scored_count", 0) or 0) > 0:
        base_projection = arsenal_base_projection
        # Keep the displayed 6-IP pace on the exact same formula as Expected Ks.
        # Expected Ks now scales this value by adjusted expected IP / 6.
        adjusted_projection = max(0, adjusted_projection)
    else:
        # Fallback to the old generic opponent-K matchup when pitch-type data is unavailable.
        base_projection = generic_base_projection
        adjusted_projection = generic_base_projection
    if return_details:
        arsenal_details["base_projection"] = round(base_projection, 2)
        arsenal_details["adjusted_projection"] = round(adjusted_projection, 2)
        return adjusted_projection, arsenal_details
    return adjusted_projection


def _legacy_pitcher_ipg_v1(pitcher, pitcher_this_year, pitcher_last_year):
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


def _k_prop_win_number(line):
    """For half-strikeout props, this is the whole-number K total that decides the bet.

    Example: line 4.5 means 5 Ks wins the over and loses the under.
    """
    try:
        return math.ceil(float(line))
    except Exception:
        return 5


def _k_edge_score_bucket(edge):
    """Convert a positive true-edge cushion into the existing point scale."""
    edge = max(0.0, float(edge or 0.0))
    if edge >= 3:
        return 4
    elif edge >= 2:
        return 3
    elif edge >= 1.2:
        return 2
    elif edge >= 0.6:
        return 1
    else:
        return 0


def strikeout_bet_grade(exp_k, six_k, ipg_this, ipg_last, line, volatility):
    """Grade pitcher K props using true win-number cushion instead of raw half-line edge.

    Old logic compared projection to the sportsbook line, e.g. 5.2 vs 4.5 = +0.7.
    New logic compares overs/unders to the actual number that decides the bet:
      line 4.5 -> win_number 5
      over_edge  = projection - 5
      under_edge = 5 - projection

    Under thresholds are intentionally stricter because unders naturally benefit
    from the half-K buffer. High-line unders receive an additional cushion requirement.
    """
    win_number = _k_prop_win_number(line)
    over_edge = float(exp_k) - win_number
    under_edge = win_number - float(exp_k)
    dir_var = exp_k - six_k
    var_abs = abs(dir_var)

    avg_ip = ipg_this if ipg_this > 0 else ipg_last if ipg_last > 0 else 5

    over_base_score = _k_edge_score_bucket(over_edge)
    under_base_score = _k_edge_score_bucket(under_edge)

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

    # 6-IP is a pace projection, not another expected-K projection.
    # Keep it as a secondary agreement signal, but do not let it drive grades.
    if dir_var < -1.3:
        var_adj_over = -1
    elif dir_var < -0.7:
        var_adj_over = -1
    elif dir_var > 1.3:
        var_adj_over = 0 if (volatility == "High volatility" or avg_ip < 5.5) else 1
    elif var_abs <= 0.7:
        var_adj_over = 1
    else:
        var_adj_over = 0

    if dir_var > 1.3:
        var_adj_under = -1
    elif dir_var > 0.7:
        var_adj_under = -1
    elif dir_var < -1.3:
        var_adj_under = 0
    elif var_abs <= 0.7:
        var_adj_under = 1
    else:
        var_adj_under = 0

    total_over = over_base_score + vol_adj + ip_adj + var_adj_over
    total_under = under_base_score + vol_adj + ip_adj + var_adj_under

    # Recommended thresholds after tracker audit:
    # Overs are graded from true over cushion. Unders need more cushion because
    # they already benefit from the half-strikeout buffer.
    over_lean_req = 0.35
    over_req = 0.75
    strong_over_req = 1.30

    # Tracker audit showed high-line unders (6.5+) were the weak bucket.
    # Base under thresholds were also tightened because true-edge math was
    # producing too many Lean Unders. Keep 4.5/5.5 viable, but require
    # extra true-edge cushion for high-line unders where late-K risk is higher.
    line_float = float(line)
    if line_float >= 7.5:
        under_line_penalty = 0.55
    elif line_float >= 6.5:
        under_line_penalty = 0.40
    else:
        under_line_penalty = 0.0

    under_lean_req = 0.85 + under_line_penalty
    under_req = 1.35 + under_line_penalty
    strong_under_req = 1.75 + under_line_penalty

    # 6-inning confirmation guardrail:
    # The expected-K projection can be moved heavily by leash/expected innings.
    # To avoid plays that are only caused by expected IP, the 6-IP pace projection
    # must agree with the same side of the line. This is intentionally mild:
    #   Under 4.5 needs six_k <= 4.2
    #   Over 4.5 needs six_k >= 4.8
    # This keeps genuine expected-edge plays, but filters out Skenes-type cases
    # where expected Ks say under while the 6-IP pace is basically at/opposite the line.
    six_line_cushion = 0.30
    try:
        six_k_float = float(six_k)
    except Exception:
        six_k_float = float(exp_k)
    six_confirms_over = six_k_float >= (line_float + six_line_cushion)
    six_confirms_under = six_k_float <= (line_float - six_line_cushion)

    if over_edge >= strong_over_req and total_over >= 6 and six_confirms_over:
        return "STRONG OVER", over_edge
    if over_edge >= over_req and total_over >= 5 and six_confirms_over:
        return "OVER", over_edge
    if over_edge >= over_lean_req and total_over >= 3 and six_confirms_over:
        return "LEAN OVER", over_edge

    if under_edge >= strong_under_req and total_under >= 6 and six_confirms_under:
        return "STRONG UNDER", -under_edge
    if under_edge >= under_req and total_under >= 4 and six_confirms_under:
        return "UNDER", -under_edge
    if under_edge >= under_lean_req and total_under >= 2 and six_confirms_under:
        return "LEAN UNDER", -under_edge

    return "PASS", 0.0


# -----------------------


def pitcher_k_strength_score(exp_k, six_k, line, volatility, ipg_this, ipg_last):
    win_number = _k_prop_win_number(line)
    over_edge = float(exp_k) - win_number
    under_edge = win_number - float(exp_k)

    # Use true win-number cushion instead of raw half-line difference. Unders are
    # discounted slightly so the natural half-K buffer does not overinflate K Score.
    if over_edge >= under_edge:
        true_edge = max(0.0, over_edge)
    else:
        line_float = float(line)
        if line_float >= 7.5:
            under_line_penalty = 0.55
        elif line_float >= 6.5:
            under_line_penalty = 0.40
        else:
            under_line_penalty = 0.0
        true_edge = max(0.0, under_edge - 0.25 - under_line_penalty)

    avg_ip = ipg_this if ipg_this > 0 else ipg_last if ipg_last > 0 else 5
    agreement_gap = abs(exp_k - six_k)

    # Edge: biggest piece of the score, max 45 points
    edge_score = min(45, true_edge / 2.5 * 45)

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

    # Model agreement: Expected K and 6-IP pace being close adds confidence,
    # but this is now a secondary signal only. 6-IP pace is not a leash-based
    # projection, so it should not overdrive K Score. Max 8 points.
    if agreement_gap <= 0.4:
        confidence_score = 8
    elif agreement_gap <= 0.8:
        confidence_score = 6
    elif agreement_gap <= 1.2:
        confidence_score = 4
    else:
        confidence_score = 2

    total_score = edge_score + vol_score + ip_score + confidence_score
    return round(min(100, max(0, total_score)), 1)

# NRFI MODEL
# -----------------------

def _legacy_nrfi_probability_v1(home, away, hp, ap, pitcher_this_year, pitcher_last_year, nrfi_pitchers, nrfi_rhp, nrfi_lhp):
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
    """Legacy fallback: only track Elite NRFI or Elite YRFI. Middle buckets pass."""
    if score >= 88:
        return "ELITE NRFI"
    elif score <= 34:
        return "ELITE YRFI"
    return "PASS"


# -----------------------
# TOTAL RUNS MODEL
# -----------------------

TOTAL_RUN_EDGE_THRESHOLD = 1.50
TOTAL_RUN_CONFLUENCE_THRESHOLD = 3


def _total_float(value, default=0.0):
    try:
        if value is None or value == "" or str(value).lower() == "nan":
            return default
        return float(str(value).replace("%", "").strip())
    except Exception:
        return default


def _total_rate(value, default=0.0):
    val = _total_float(value, default)
    if val > 1:
        return val / 100.0
    return val


def _total_clip(value, low, high):
    return max(low, min(high, float(value)))


# -----------------------
# UNIFORM PARK + WEATHER + K-CONTEXT ENGINE
# -----------------------

PARK_ENVIRONMENT_PROFILES = {
    "Arizona Diamondbacks": {"park": "Chase Field", "run_factor": 1.02, "hr_factor": 1.05, "hit_factor": 1.01, "k_factor": 0.99, "roof": True},
    "Athletics": {"park": "Sutter Health Park", "run_factor": 1.02, "hr_factor": 1.03, "hit_factor": 1.00, "k_factor": 1.00, "roof": False},
    "Atlanta Braves": {"park": "Truist Park", "run_factor": 1.04, "hr_factor": 1.08, "hit_factor": 1.01, "k_factor": 0.99, "roof": False},
    "Baltimore Orioles": {"park": "Oriole Park at Camden Yards", "run_factor": 0.97, "hr_factor": 0.95, "hit_factor": 0.98, "k_factor": 1.01, "roof": False},
    "Boston Red Sox": {"park": "Fenway Park", "run_factor": 1.06, "hr_factor": 0.97, "hit_factor": 1.07, "k_factor": 0.98, "roof": False},
    "Chicago Cubs": {"park": "Wrigley Field", "run_factor": 1.00, "hr_factor": 1.02, "hit_factor": 1.00, "k_factor": 1.00, "roof": False},
    "Chicago White Sox": {"park": "Rate Field", "run_factor": 0.98, "hr_factor": 1.00, "hit_factor": 0.98, "k_factor": 1.01, "roof": False},
    "Cincinnati Reds": {"park": "Great American Ball Park", "run_factor": 1.08, "hr_factor": 1.17, "hit_factor": 1.02, "k_factor": 0.97, "roof": False},
    "Cleveland Guardians": {"park": "Progressive Field", "run_factor": 0.96, "hr_factor": 0.92, "hit_factor": 0.98, "k_factor": 1.02, "roof": False},
    "Colorado Rockies": {"park": "Coors Field", "run_factor": 1.26, "hr_factor": 1.18, "hit_factor": 1.18, "k_factor": 0.94, "roof": False},
    "Detroit Tigers": {"park": "Comerica Park", "run_factor": 0.97, "hr_factor": 0.94, "hit_factor": 1.02, "k_factor": 1.01, "roof": False},
    "Houston Astros": {"park": "Daikin Park", "run_factor": 1.00, "hr_factor": 1.04, "hit_factor": 1.00, "k_factor": 1.00, "roof": True},
    "Kansas City Royals": {"park": "Kauffman Stadium", "run_factor": 1.03, "hr_factor": 0.94, "hit_factor": 1.08, "k_factor": 0.98, "roof": False},
    "Los Angeles Angels": {"park": "Angel Stadium", "run_factor": 1.00, "hr_factor": 1.02, "hit_factor": 1.00, "k_factor": 1.00, "roof": False},
    "Los Angeles Dodgers": {"park": "Dodger Stadium", "run_factor": 1.02, "hr_factor": 1.05, "hit_factor": 0.99, "k_factor": 1.00, "roof": False},
    "Miami Marlins": {"park": "loanDepot park", "run_factor": 0.93, "hr_factor": 0.88, "hit_factor": 0.98, "k_factor": 1.03, "roof": True},
    "Milwaukee Brewers": {"park": "American Family Field", "run_factor": 1.00, "hr_factor": 1.04, "hit_factor": 1.00, "k_factor": 1.00, "roof": True},
    "Minnesota Twins": {"park": "Target Field", "run_factor": 0.99, "hr_factor": 1.02, "hit_factor": 0.99, "k_factor": 1.00, "roof": False},
    "New York Mets": {"park": "Citi Field", "run_factor": 0.96, "hr_factor": 0.91, "hit_factor": 0.98, "k_factor": 1.03, "roof": False},
    "New York Yankees": {"park": "Yankee Stadium", "run_factor": 1.02, "hr_factor": 1.13, "hit_factor": 0.96, "k_factor": 1.00, "roof": False},
    "Philadelphia Phillies": {"park": "Citizens Bank Park", "run_factor": 1.03, "hr_factor": 1.09, "hit_factor": 1.00, "k_factor": 0.99, "roof": False},
    "Pittsburgh Pirates": {"park": "PNC Park", "run_factor": 0.95, "hr_factor": 0.90, "hit_factor": 0.99, "k_factor": 1.03, "roof": False},
    "San Diego Padres": {"park": "Petco Park", "run_factor": 0.95, "hr_factor": 0.91, "hit_factor": 0.99, "k_factor": 1.04, "roof": False},
    "San Francisco Giants": {"park": "Oracle Park", "run_factor": 0.94, "hr_factor": 0.82, "hit_factor": 0.98, "k_factor": 1.04, "roof": False},
    "Seattle Mariners": {"park": "T-Mobile Park", "run_factor": 0.94, "hr_factor": 0.89, "hit_factor": 0.98, "k_factor": 1.04, "roof": True},
    "St. Louis Cardinals": {"park": "Busch Stadium", "run_factor": 0.98, "hr_factor": 0.91, "hit_factor": 1.00, "k_factor": 1.02, "roof": False},
    "Tampa Bay Rays": {"park": "George M. Steinbrenner Field", "run_factor": 0.96, "hr_factor": 0.94, "hit_factor": 1.00, "k_factor": 1.02, "roof": False},
    "Texas Rangers": {"park": "Globe Life Field", "run_factor": 1.04, "hr_factor": 1.09, "hit_factor": 1.01, "k_factor": 0.99, "roof": True},
    "Toronto Blue Jays": {"park": "Rogers Centre", "run_factor": 1.03, "hr_factor": 1.11, "hit_factor": 1.00, "k_factor": 0.99, "roof": True},
    "Washington Nationals": {"park": "Nationals Park", "run_factor": 1.01, "hr_factor": 1.02, "hit_factor": 1.00, "k_factor": 1.00, "roof": False},
}


MLB_PARK_WEATHER_LOCATIONS = {
    "Arizona Diamondbacks": {"city": "Phoenix, AZ", "latitude": 33.4455, "longitude": -112.0667, "timezone": "America/Phoenix"},
    "Athletics": {"city": "Sacramento, CA", "latitude": 38.5806, "longitude": -121.4944, "timezone": "America/Los_Angeles"},
    "Atlanta Braves": {"city": "Atlanta, GA", "latitude": 33.8908, "longitude": -84.4678, "timezone": "America/New_York"},
    "Baltimore Orioles": {"city": "Baltimore, MD", "latitude": 39.2839, "longitude": -76.6217, "timezone": "America/New_York"},
    "Boston Red Sox": {"city": "Boston, MA", "latitude": 42.3467, "longitude": -71.0972, "timezone": "America/New_York"},
    "Chicago Cubs": {"city": "Chicago, IL", "latitude": 41.9484, "longitude": -87.6553, "timezone": "America/Chicago"},
    "Chicago White Sox": {"city": "Chicago, IL", "latitude": 41.8300, "longitude": -87.6339, "timezone": "America/Chicago"},
    "Cincinnati Reds": {"city": "Cincinnati, OH", "latitude": 39.0979, "longitude": -84.5082, "timezone": "America/New_York"},
    "Cleveland Guardians": {"city": "Cleveland, OH", "latitude": 41.4962, "longitude": -81.6852, "timezone": "America/New_York"},
    "Colorado Rockies": {"city": "Denver, CO", "latitude": 39.7559, "longitude": -104.9942, "timezone": "America/Denver"},
    "Detroit Tigers": {"city": "Detroit, MI", "latitude": 42.3390, "longitude": -83.0485, "timezone": "America/Detroit"},
    "Houston Astros": {"city": "Houston, TX", "latitude": 29.7573, "longitude": -95.3555, "timezone": "America/Chicago"},
    "Kansas City Royals": {"city": "Kansas City, MO", "latitude": 39.0517, "longitude": -94.4803, "timezone": "America/Chicago"},
    "Los Angeles Angels": {"city": "Anaheim, CA", "latitude": 33.8003, "longitude": -117.8827, "timezone": "America/Los_Angeles"},
    "Los Angeles Dodgers": {"city": "Los Angeles, CA", "latitude": 34.0739, "longitude": -118.2400, "timezone": "America/Los_Angeles"},
    "Miami Marlins": {"city": "Miami, FL", "latitude": 25.7781, "longitude": -80.2197, "timezone": "America/New_York"},
    "Milwaukee Brewers": {"city": "Milwaukee, WI", "latitude": 43.0280, "longitude": -87.9712, "timezone": "America/Chicago"},
    "Minnesota Twins": {"city": "Minneapolis, MN", "latitude": 44.9817, "longitude": -93.2776, "timezone": "America/Chicago"},
    "New York Mets": {"city": "New York, NY", "latitude": 40.7571, "longitude": -73.8458, "timezone": "America/New_York"},
    "New York Yankees": {"city": "New York, NY", "latitude": 40.8296, "longitude": -73.9262, "timezone": "America/New_York"},
    "Philadelphia Phillies": {"city": "Philadelphia, PA", "latitude": 39.9061, "longitude": -75.1665, "timezone": "America/New_York"},
    "Pittsburgh Pirates": {"city": "Pittsburgh, PA", "latitude": 40.4469, "longitude": -80.0057, "timezone": "America/New_York"},
    "San Diego Padres": {"city": "San Diego, CA", "latitude": 32.7073, "longitude": -117.1566, "timezone": "America/Los_Angeles"},
    "San Francisco Giants": {"city": "San Francisco, CA", "latitude": 37.7786, "longitude": -122.3893, "timezone": "America/Los_Angeles"},
    "Seattle Mariners": {"city": "Seattle, WA", "latitude": 47.5914, "longitude": -122.3325, "timezone": "America/Los_Angeles"},
    "St. Louis Cardinals": {"city": "St. Louis, MO", "latitude": 38.6226, "longitude": -90.1928, "timezone": "America/Chicago"},
    "Tampa Bay Rays": {"city": "Tampa, FL", "latitude": 27.9802, "longitude": -82.5066, "timezone": "America/New_York"},
    "Texas Rangers": {"city": "Arlington, TX", "latitude": 32.7473, "longitude": -97.0838, "timezone": "America/Chicago"},
    "Toronto Blue Jays": {"city": "Toronto, ON", "latitude": 43.6414, "longitude": -79.3894, "timezone": "America/Toronto"},
    "Washington Nationals": {"city": "Washington, DC", "latitude": 38.8730, "longitude": -77.0074, "timezone": "America/New_York"},
}


def get_weather_location_for_home_team(home_team, venue_name=""):
    """Return the city/coordinate weather location for the home team.

    This intentionally uses city/stadium-area weather rather than a fragile stadium-only source.
    Manual overrides remain available in the builder for roof decisions or wind out/in context.
    """
    canonical = _canonical_team_name(home_team)
    loc = dict(MLB_PARK_WEATHER_LOCATIONS.get(canonical, {}))
    if not loc:
        loc = {"city": str(venue_name or canonical or home_team or "Unknown City"), "latitude": None, "longitude": None, "timezone": "UTC"}
    loc["home_team"] = canonical or str(home_team or "")
    loc["venue_name"] = str(venue_name or "")
    return loc


def _weather_compass_from_degrees(degrees):
    try:
        deg = float(degrees) % 360
    except Exception:
        return ""
    names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = int((deg + 11.25) // 22.5) % 16
    return names[idx]


def _target_local_weather_hour(game_time, game_date, timezone_str):
    """Return YYYY-MM-DDTHH:00 local hour used to match hourly forecast rows."""
    try:
        if game_time:
            text = str(game_time).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if ZoneInfo is not None and timezone_str:
                dt = dt.astimezone(ZoneInfo(str(timezone_str)))
            return dt.strftime("%Y-%m-%dT%H:00"), dt.strftime("%Y-%m-%d %I:%M %p")
    except Exception:
        pass
    fallback_date = str(game_date or date.today())[:10]
    return f"{fallback_date}T19:00", f"{fallback_date} 07:00 PM"



ODDSTRADER_WEATHER_URL = "https://www.oddstrader.com/mlb/weather/"

ODDSTRADER_TEAM_CODE_TO_CANONICAL = {
    "ARI": "Arizona Diamondbacks", "AZ": "Arizona Diamondbacks",
    "ATH": "Athletics", "OAK": "Athletics",
    "ATL": "Atlanta Braves", "BAL": "Baltimore Orioles", "BOS": "Boston Red Sox",
    "CHC": "Chicago Cubs", "CWS": "Chicago White Sox", "CHW": "Chicago White Sox",
    "CIN": "Cincinnati Reds", "CLE": "Cleveland Guardians", "COL": "Colorado Rockies",
    "DET": "Detroit Tigers", "HOU": "Houston Astros", "KC": "Kansas City Royals", "KCR": "Kansas City Royals", "KAN": "Kansas City Royals",
    "LAA": "Los Angeles Angels", "LAD": "Los Angeles Dodgers", "MIA": "Miami Marlins",
    "MIL": "Milwaukee Brewers", "MIN": "Minnesota Twins", "NYM": "New York Mets", "NYY": "New York Yankees",
    "PHI": "Philadelphia Phillies", "PIT": "Pittsburgh Pirates", "SD": "San Diego Padres", "SDP": "San Diego Padres",
    "SF": "San Francisco Giants", "SFG": "San Francisco Giants", "SEA": "Seattle Mariners", "STL": "St. Louis Cardinals",
    "TB": "Tampa Bay Rays", "TBR": "Tampa Bay Rays", "TEX": "Texas Rangers", "TOR": "Toronto Blue Jays",
    "WSH": "Washington Nationals", "WSN": "Washington Nationals", "WAS": "Washington Nationals",
}


def _oddstrader_team_code_pattern():
    import re
    return "(?:" + "|".join(re.escape(x) for x in sorted(ODDSTRADER_TEAM_CODE_TO_CANONICAL.keys(), key=len, reverse=True)) + ")"


def _oddstrader_codes_for_team(team):
    """Return the OddsTrader abbreviations that can represent a team name."""
    raw = str(team or "").strip().upper()
    codes = set()
    if raw in ODDSTRADER_TEAM_CODE_TO_CANONICAL:
        codes.add(raw)
    try:
        canonical = _canonical_team_name(team)
    except Exception:
        canonical = str(team or "").strip()
    for code, name in ODDSTRADER_TEAM_CODE_TO_CANONICAL.items():
        try:
            if normalize_match_text(name) == normalize_match_text(canonical):
                codes.add(code)
        except Exception:
            if str(name).lower() == str(canonical).lower():
                codes.add(code)
    return codes


def _oddstrader_selected_mmdd(game_date):
    try:
        return datetime.fromisoformat(str(game_date)[:10]).strftime("%m/%d")
    except Exception:
        try:
            return pd.to_datetime(game_date).strftime("%m/%d")
        except Exception:
            return ""


def _oddstrader_normalize_mmdd(value):
    try:
        parts = str(value or "").strip().split("/")
        if len(parts) == 2:
            return f"{int(parts[0]):02d}/{int(parts[1]):02d}"
    except Exception:
        pass
    return str(value or "").strip()


def _oddstrader_text_from_html(raw_html):
    """Convert the OddsTrader weather HTML into a searchable single-line text blob."""
    import re
    text = str(raw_html or "")
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    try:
        text = html.unescape(text)
    except Exception:
        pass
    text = text.replace("\u00a0", " ").replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    # The notification prompt can appear before every card and makes parsing noisier.
    text = text.replace("Personalize your notifications and get updates on the teams, players or events you care about most. GOT IT background Layer 1", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _oddstrader_delay_label(block_text):
    text = str(block_text or "")
    for label in [
        "Likely Postponed", "Postponed", "Likely Delay", "Possible Delay", "Delayed",
        "Weather Delay", "No Impact", "In Domed Stadium"
    ]:
        if label.lower() in text.lower():
            return label
    return ""


def _oddstrader_batting_pitching_impacts(block_text):
    import re
    text = str(block_text or "")
    batting = ""
    pitching = ""
    batting_match = re.search(r"\bBatting\s+(Ideal|Good|Normal|Poor|Very Poor)\b", text, flags=re.I)
    pitching_match = re.search(r"\bPitching\s+(Ideal|Good|Normal|Poor|Very Poor)\b", text, flags=re.I)
    if batting_match:
        batting = batting_match.group(1).title()
    if pitching_match:
        pitching = pitching_match.group(1).title()
    return batting, pitching


def _oddstrader_wind_direction_label(short_label):
    label = str(short_label or "").upper().strip()
    if label == "OUT":
        return "Out to OF"
    if label == "IN":
        return "In from OF"
    return "Neutral/Cross"


def _parse_oddstrader_weather_games(page_text):
    """Parse OddsTrader MLB weather cards from the public weather page text."""
    import re
    text = str(page_text or "")
    if not text:
        return []

    code_pat = _oddstrader_team_code_pattern()
    date_time_pat = r"\b(?:SUN|MON|TUE|WED|THU|FRI|SAT)\s+\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}\s+[AP]M\b"
    starts = list(re.finditer(date_time_pat, text, flags=re.I))
    games = []

    for i, start_match in enumerate(starts):
        start = start_match.start()
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        block = text[start:end].strip()
        dt_match = re.search(
            r"\b(?P<dow>SUN|MON|TUE|WED|THU|FRI|SAT)\s+(?P<mmdd>\d{1,2}/\d{1,2})\s+(?P<time>\d{1,2}:\d{2}\s+[AP]M)\b",
            block,
            flags=re.I,
        )
        if not dt_match:
            continue
        after_time = block[dt_match.end():].strip()
        code_matches = list(re.finditer(rf"\b({code_pat})\b", after_time, flags=re.I))
        if len(code_matches) < 2:
            continue

        away_code = code_matches[0].group(1).upper()
        home_code = code_matches[1].group(1).upper()
        away_team = ODDSTRADER_TEAM_CODE_TO_CANONICAL.get(away_code, away_code)
        home_team = ODDSTRADER_TEAM_CODE_TO_CANONICAL.get(home_code, home_code)

        delay = _oddstrader_delay_label(block)
        rain_match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*Rain", block, flags=re.I)
        temp_match = re.search(r"(\d+(?:\.\d+)?)\s*°", block)
        wind_match = re.search(r"\b([NSEW]{1,3})\s+(\d+(?:\.\d+)?)\s*mph\s*-\s*(OUT|IN|LR|RL)\b", block, flags=re.I)
        venue_match = re.search(r"\bAt\s+(.+?)\s*$", block, flags=re.I)
        batting_impact, pitching_impact = _oddstrader_batting_pitching_impacts(block)

        rain_pct = float(rain_match.group(1)) if rain_match else 0.0
        temperature = float(temp_match.group(1)) if temp_match else 72.0
        wind_compass = wind_match.group(1).upper() if wind_match else ""
        wind_speed = float(wind_match.group(2)) if wind_match else 0.0
        wind_short = wind_match.group(3).upper() if wind_match else ""
        roof_closed = "In Domed Stadium".lower() in block.lower()

        venue = venue_match.group(1).strip() if venue_match else ""
        # Keep venue readable if extra weather copy leaked into the row.
        venue = re.sub(r"\s+", " ", venue).strip()

        games.append({
            "date_label": dt_match.group("mmdd"),
            "day_label": dt_match.group("dow").upper(),
            "game_time_label": dt_match.group("time").upper(),
            "away_code": away_code,
            "home_code": home_code,
            "away_team": away_team,
            "home_team": home_team,
            "venue": venue,
            "delay_risk": delay or "Unknown",
            "rain_pct": round(rain_pct, 1),
            "temperature": round(temperature, 1),
            "wind_compass": wind_compass,
            "wind_speed": round(wind_speed, 1),
            "wind_direction_short": wind_short,
            "wind_direction_label": _oddstrader_wind_direction_label(wind_short),
            "batting_impact": batting_impact,
            "pitching_impact": pitching_impact,
            "roof_status": "Dome/Roof Closed" if roof_closed else "Open/Outdoor",
            "raw_block": block[:900],
        })

    return games


@st.cache_data(ttl=15 * 60, show_spinner=False)
def fetch_oddstrader_mlb_weather_page():
    """Fetch and parse OddsTrader's free MLB weather report once per cache window."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }
        response = requests.get(ODDSTRADER_WEATHER_URL, headers=headers, timeout=15)
        response.raise_for_status()
        page_text = _oddstrader_text_from_html(response.text)
        games = _parse_oddstrader_weather_games(page_text)
        return {
            "ok": bool(games),
            "source": "OddsTrader MLB Weather Report",
            "url": ODDSTRADER_WEATHER_URL,
            "games": games,
            "status": f"Loaded {len(games)} games from OddsTrader MLB Weather Report." if games else "OddsTrader page loaded but no weather rows were parsed.",
        }
    except Exception as e:
        return {
            "ok": False,
            "source": "OddsTrader MLB Weather Report",
            "url": ODDSTRADER_WEATHER_URL,
            "games": [],
            "status": f"OddsTrader weather unavailable: {e}",
        }


def fetch_oddstrader_game_weather(home_team, away_team="", venue_name="", game_time="", game_date=""):
    """Return one game's OddsTrader weather card if it can be matched safely.

    v3 matching fix:
    - OddsTrader sometimes labels the slate date differently than the app-selected MLB schedule date
      because of page refresh timing/time zones.
    - Do not throw away all parsed games just because the date label does not match.
    - Prefer same-date rows, but if no same-date match exists, match by teams across the loaded page.
    """
    report = fetch_oddstrader_mlb_weather_page()
    selected_mmdd = _oddstrader_selected_mmdd(game_date)
    home_codes = _oddstrader_codes_for_team(home_team)
    away_codes = _oddstrader_codes_for_team(away_team)
    venue_norm = normalize_match_text(venue_name)
    try:
        home_norm = normalize_match_text(_canonical_team_name(home_team))
    except Exception:
        home_norm = normalize_match_text(home_team)
    try:
        away_norm = normalize_match_text(_canonical_team_name(away_team))
    except Exception:
        away_norm = normalize_match_text(away_team)

    all_rows = list(report.get("games", []) or [])
    same_date_rows = []
    candidates = []

    def _team_name_match(target_norm, row_norm):
        if not target_norm or not row_norm:
            return False
        return target_norm == row_norm or target_norm in row_norm or row_norm in target_norm

    for row in all_rows:
        row_date = _oddstrader_normalize_mmdd(row.get("date_label", ""))
        date_matches = bool(selected_mmdd and row_date == selected_mmdd)
        if date_matches:
            same_date_rows.append(row)

        row_home_code = str(row.get("home_code", "")).upper()
        row_away_code = str(row.get("away_code", "")).upper()
        row_home_norm = normalize_match_text(row.get("home_team", ""))
        row_away_norm = normalize_match_text(row.get("away_team", ""))

        home_match = False
        if home_codes and row_home_code in home_codes:
            home_match = True
        elif _team_name_match(home_norm, row_home_norm):
            home_match = True

        if not home_match:
            continue

        score = 10
        if selected_mmdd:
            # Prefer exact date, but do not block the match if OddsTrader's label is off by slate timing.
            score += 4 if date_matches else 0

        if away_codes and row_away_code in away_codes:
            score += 6
        elif _team_name_match(away_norm, row_away_norm):
            score += 6
        elif away_norm:
            # If home matches but away does not, keep as a low-confidence backup only.
            score -= 5

        if venue_norm and venue_norm in normalize_match_text(row.get("venue", "")):
            score += 2

        candidates.append((score, date_matches, row))

    # If there is a same-date candidate, use it. Otherwise, allow the best team match from the full page.
    if not candidates:
        examples_source = same_date_rows if same_date_rows else all_rows
        examples = []
        for row in examples_source[:15]:
            date_part = _oddstrader_normalize_mmdd(row.get("date_label", ""))
            examples.append(f"{row.get('away_code', '?')}@{row.get('home_code', '?')} {date_part}".strip())
        parsed_text = ", ".join(examples) if examples else "none"
        return {
            "matched": False,
            "source": "OddsTrader MLB Weather Report",
            "status": f"{report.get('status', 'OddsTrader weather checked.')} No exact OddsTrader match for {away_team} at {home_team}. Parsed games: {parsed_text}. Using neutral/manual weather defaults.",
            "temperature": 72.0,
            "wind_speed": 0.0,
            "wind_direction_label": "Neutral/Cross",
            "rain_pct": "",
            "delay_risk": "",
            "roof_status": "Open/Outdoor",
        }

    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_date_match, row = candidates[0]

    # Require a real team-pair match unless the venue also helped. This avoids wrong weather if only a home code matched.
    if best_score < 11:
        examples = []
        for r in all_rows[:15]:
            date_part = _oddstrader_normalize_mmdd(r.get("date_label", ""))
            examples.append(f"{r.get('away_code', '?')}@{r.get('home_code', '?')} {date_part}".strip())
        parsed_text = ", ".join(examples) if examples else "none"
        return {
            "matched": False,
            "source": "OddsTrader MLB Weather Report",
            "status": f"{report.get('status', 'OddsTrader weather checked.')} OddsTrader only found a weak match for {away_team} at {home_team}, so neutral/manual weather defaults were used. Parsed games: {parsed_text}.",
            "temperature": 72.0,
            "wind_speed": 0.0,
            "wind_direction_label": "Neutral/Cross",
            "rain_pct": "",
            "delay_risk": "",
            "roof_status": "Open/Outdoor",
        }

    row = dict(row)
    rain = row.get("rain_pct", "")
    delay = row.get("delay_risk", "")
    batting = row.get("batting_impact", "")
    pitching = row.get("pitching_impact", "")
    wind_bits = []
    if row.get("wind_compass"):
        wind_bits.append(str(row.get("wind_compass")))
    if row.get("wind_direction_short"):
        wind_bits.append(str(row.get("wind_direction_short")))
    wind_text = " / ".join(wind_bits) if wind_bits else row.get("wind_direction_label", "Neutral/Cross")

    date_note = ""
    if selected_mmdd and not best_date_match:
        date_note = f" OddsTrader date label was {row.get('date_label', 'unknown')}, so this was matched by teams rather than date."

    row.update({
        "matched": True,
        "source": "OddsTrader MLB Weather Report",
        "city": row.get("venue", venue_name),
        "target_local_time": row.get("game_time_label", ""),
        "wind_direction_degrees": "",
        "status": (
            f"OddsTrader weather: matched {row.get('away_code', '?')}@{row.get('home_code', '?')}. "
            f"{delay or 'Status unknown'}, {rain}% rain, "
            f"{float(row.get('temperature', 72.0)):.0f}°F, wind {float(row.get('wind_speed', 0.0)):.0f} mph {wind_text}. "
            f"Batting {batting or 'N/A'}, Pitching {pitching or 'N/A'}." + date_note
        ),
    })
    return row


@st.cache_data(ttl=20 * 60, show_spinner=False)
def fetch_game_city_weather(home_team, venue_name="", game_time="", game_date="", away_team=""):
    """Auto-fill MLB game weather using OddsTrader first, then Open-Meteo as a fallback.

    OddsTrader is preferred because it is a free MLB-specific weather page with stadium/game cards,
    rain risk, roof/dome labels, wind direction relative to the field, and batting/pitching impact.
    If a matching OddsTrader row cannot be parsed, the model falls back to city weather from Open-Meteo.
    """
    oddstrader_weather = fetch_oddstrader_game_weather(
        home_team,
        away_team=away_team,
        venue_name=venue_name,
        game_time=game_time,
        game_date=game_date,
    )
    if oddstrader_weather.get("matched"):
        return oddstrader_weather

    loc = get_weather_location_for_home_team(home_team, venue_name)
    lat = loc.get("latitude")
    lon = loc.get("longitude")
    tz = loc.get("timezone") or "UTC"

    enable_city_fallback = str(os.environ.get("ENABLE_CITY_WEATHER_FALLBACK", "")).lower() in ["1", "true", "yes", "on"]
    if not enable_city_fallback:
        try:
            enable_city_fallback = bool(st.secrets.get("ENABLE_CITY_WEATHER_FALLBACK", False))
        except Exception:
            enable_city_fallback = False

    if not enable_city_fallback:
        return {
            "source": "Manual/Neutral",
            "status": f"{oddstrader_weather.get('status', '')} City weather fallback is disabled to avoid Open-Meteo rate limits. Using neutral defaults unless you manually override the fields below.",
            "temperature": 72.0,
            "wind_speed": 0.0,
            "wind_direction_degrees": "",
            "wind_compass": "",
            "wind_direction_label": "Neutral/Cross",
            "roof_status": oddstrader_weather.get("roof_status", "Open/Outdoor"),
            "city": loc.get("city", ""),
            "target_local_time": "",
            "rain_pct": oddstrader_weather.get("rain_pct", ""),
            "delay_risk": oddstrader_weather.get("delay_risk", ""),
        }

    if lat is None or lon is None:
        return {
            "source": "Manual/Neutral",
            "status": f"{oddstrader_weather.get('status', '')} No city coordinates available; using neutral weather defaults.",
            "temperature": 72.0,
            "wind_speed": 0.0,
            "wind_direction_degrees": "",
            "wind_compass": "",
            "wind_direction_label": "Neutral/Cross",
            "roof_status": oddstrader_weather.get("roof_status", "Open/Outdoor"),
            "city": loc.get("city", ""),
            "target_local_time": "",
            "rain_pct": oddstrader_weather.get("rain_pct", ""),
            "delay_risk": oddstrader_weather.get("delay_risk", ""),
        }

    target_hour, target_display = _target_local_weather_hour(game_time, game_date, tz)
    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "timezone": tz,
            "start_date": str(game_date or date.today())[:10],
            "end_date": str(game_date or date.today())[:10],
        }
        response = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=12)
        response.raise_for_status()
        data = response.json()
        hourly = data.get("hourly", {}) or {}
        times = hourly.get("time", []) or []
        temps = hourly.get("temperature_2m", []) or []
        winds = hourly.get("wind_speed_10m", []) or []
        wind_dirs = hourly.get("wind_direction_10m", []) or []
        if not times:
            raise ValueError("No hourly weather rows returned")

        if target_hour in times:
            idx = times.index(target_hour)
        else:
            target_dt = datetime.fromisoformat(target_hour)
            best = None
            for i, time_text in enumerate(times):
                try:
                    row_dt = datetime.fromisoformat(str(time_text))
                    diff = abs((row_dt - target_dt).total_seconds())
                    if best is None or diff < best[0]:
                        best = (diff, i)
                except Exception:
                    continue
            idx = best[1] if best else 0

        temp = float(temps[idx]) if idx < len(temps) and temps[idx] is not None else 72.0
        wind = float(winds[idx]) if idx < len(winds) and winds[idx] is not None else 0.0
        wind_deg = wind_dirs[idx] if idx < len(wind_dirs) else ""
        compass = _weather_compass_from_degrees(wind_deg)
        matched_time = str(times[idx]) if idx < len(times) else target_hour
        return {
            "source": "Open-Meteo city forecast",
            "status": f"{oddstrader_weather.get('status', '')} Fallback city weather from {loc.get('city', 'game city')} for about {target_display}: {temp:.0f}°F, wind {wind:.0f} mph {compass or ''}. Wind direction defaults to Neutral/Cross unless manually changed.",
            "temperature": round(temp, 1),
            "wind_speed": round(wind, 1),
            "wind_direction_degrees": wind_deg,
            "wind_compass": compass,
            "wind_direction_label": "Neutral/Cross",
            "roof_status": oddstrader_weather.get("roof_status", "Open/Outdoor"),
            "city": loc.get("city", ""),
            "target_local_time": target_display,
            "matched_forecast_hour": matched_time,
            "rain_pct": oddstrader_weather.get("rain_pct", ""),
            "delay_risk": oddstrader_weather.get("delay_risk", ""),
        }
    except Exception as e:
        return {
            "source": "Manual/Neutral",
            "status": f"{oddstrader_weather.get('status', '')} Fallback city weather unavailable for {loc.get('city', 'game city')}: {e}. Using neutral weather defaults.",
            "temperature": 72.0,
            "wind_speed": 0.0,
            "wind_direction_degrees": "",
            "wind_compass": "",
            "wind_direction_label": "Neutral/Cross",
            "roof_status": oddstrader_weather.get("roof_status", "Open/Outdoor"),
            "city": loc.get("city", ""),
            "target_local_time": target_display,
            "rain_pct": oddstrader_weather.get("rain_pct", ""),
            "delay_risk": oddstrader_weather.get("delay_risk", ""),
        }



def _canonical_team_name(team):
    raw = str(team or "").strip()
    if not raw:
        return ""
    try:
        raw_norm = normalize_match_text(raw)
        for canonical, aliases in TEAM_NAME_ALIASES_FOR_SAVANT.items():
            alias_norms = {normalize_match_text(x) for x in ([canonical] + list(aliases)) if str(x).strip()}
            if raw_norm in alias_norms:
                return canonical
    except Exception:
        pass
    return raw


def get_park_environment_profile(home_team, venue_name=""):
    canonical = _canonical_team_name(home_team)
    profile = dict(PARK_ENVIRONMENT_PROFILES.get(canonical, {}))
    if not profile:
        profile = {
            "park": str(venue_name or "Unknown Park"),
            "run_factor": 1.00,
            "hr_factor": 1.00,
            "hit_factor": 1.00,
            "k_factor": 1.00,
            "roof": False,
        }
    if venue_name:
        profile["park"] = str(venue_name)
    profile["home_team"] = canonical or str(home_team or "")
    return profile


def _legacy_build_game_environment_v1(home_team, venue_name="", temperature=72, wind_speed=0, wind_direction="Neutral/Cross", roof_status="Open/Outdoor"):
    """Create one uniform park/weather context used by totals, NRFI/YRFI, ML details, and pitcher Ks.

    This is intentionally not a Coors-only modifier. Every game receives a neutral
    or non-neutral context from the home park plus auto-filled or manually overridden weather.
    """
    profile = get_park_environment_profile(home_team, venue_name)
    try:
        temp = float(temperature if temperature is not None else 72)
    except Exception:
        temp = 72.0
    try:
        wind = max(0.0, float(wind_speed or 0))
    except Exception:
        wind = 0.0

    roof_text = str(roof_status or "").lower()
    wind_text = str(wind_direction or "Neutral/Cross")
    roof_closed = ("closed" in roof_text) or ("dome" in roof_text)

    park_run_adj = _total_clip((float(profile.get("run_factor", 1.0)) - 1.0) * 3.50, -0.55, 0.95)
    park_hr_adj = _total_clip((float(profile.get("hr_factor", 1.0)) - 1.0) * 1.15, -0.22, 0.28)
    park_hit_adj = _total_clip((float(profile.get("hit_factor", 1.0)) - 1.0) * 1.25, -0.15, 0.22)

    if roof_closed:
        temp_adj = 0.0
        wind_adj = 0.0
        weather_status = "Roof/dome closed: weather is neutralized."
    else:
        temp_adj = _total_clip((temp - 72.0) * 0.012, -0.24, 0.34)
        if "Out" in wind_text:
            wind_adj = _total_clip(wind * 0.025, 0.0, 0.35)
        elif "In" in wind_text:
            wind_adj = -_total_clip(wind * 0.022, 0.0, 0.30)
        else:
            wind_adj = 0.0
        weather_status = f"Outdoor/open weather: {temp:.0f}°F, wind {wind:.0f} mph, {wind_text}."

    weather_run_adj = _total_clip(temp_adj + wind_adj, -0.36, 0.52)
    total_run_adj = _total_clip(park_run_adj + park_hr_adj + park_hit_adj + weather_run_adj, -0.70, 1.15)

    k_park_adj = _total_clip((float(profile.get("k_factor", 1.0)) - 1.0) * 2.60, -0.22, 0.18)
    k_run_penalty = -_total_clip(max(0.0, total_run_adj - 0.25) * 0.14, 0.0, 0.18)
    k_weather_bonus = _total_clip(max(0.0, -total_run_adj - 0.20) * 0.08, 0.0, 0.08)
    k_projection_adjustment = _total_clip(k_park_adj + k_run_penalty + k_weather_bonus, -0.35, 0.25)

    run_environment_score = _total_clip(50 + (total_run_adj * 24.0) + ((float(profile.get("hr_factor", 1.0)) - 1.0) * 20.0), 0, 100)
    if run_environment_score >= 67 or total_run_adj >= 0.65:
        run_tag = "Extreme Hitter"
        early_hook_risk = "High"
    elif run_environment_score >= 58 or total_run_adj >= 0.30:
        run_tag = "Hitter Friendly"
        early_hook_risk = "Medium"
    elif run_environment_score <= 42 or total_run_adj <= -0.30:
        run_tag = "Pitcher Friendly"
        early_hook_risk = "Low"
    else:
        run_tag = "Neutral"
        early_hook_risk = "Low"

    return {
        "park": profile.get("park", venue_name or ""),
        "home_team": profile.get("home_team", str(home_team or "")),
        "run_factor": round(float(profile.get("run_factor", 1.0)), 3),
        "hr_factor": round(float(profile.get("hr_factor", 1.0)), 3),
        "hit_factor": round(float(profile.get("hit_factor", 1.0)), 3),
        "k_factor": round(float(profile.get("k_factor", 1.0)), 3),
        "temperature": round(temp, 1),
        "wind_speed": round(wind, 1),
        "wind_direction": wind_text,
        "roof_status": roof_status,
        "park_run_adjustment": round(park_run_adj + park_hr_adj + park_hit_adj, 2),
        "weather_run_adjustment": round(weather_run_adj, 2),
        "total_run_adjustment": round(total_run_adj, 2),
        "per_team_run_adjustment": round(total_run_adj / 2.0, 2),
        "k_projection_adjustment": round(k_projection_adjustment, 2),
        "run_environment_score": round(run_environment_score, 1),
        "run_environment_tag": run_tag,
        "early_hook_risk": early_hook_risk,
        "weather_status": weather_status,
        "status": f"{profile.get('park', venue_name or 'Park')}: {run_tag} environment, run adj {total_run_adj:+.2f}, K adj {k_projection_adjustment:+.2f}.",
    }


def classify_pitcher_archetype(pitcher, pitcher_this_year, pitcher_last_year, arsenal_details=None):
    """Classify pitcher K-prop shape so contact managers do not get treated like power-K arms."""
    try:
        inp = _pitcher_skill_inputs(pitcher, pitcher_this_year, pitcher_last_year)
    except Exception:
        inp = {"k_rate": 0.215, "ground_ball_rate": 0.43, "projected_ip": 5.3, "contact_score": 0.0}

    try:
        weapon_count = int((arsenal_details or {}).get("weapon_count", 0) or 0)
    except Exception:
        weapon_count = 0

    k_rate = float(inp.get("k_rate", 0.215) or 0.215)
    gb_rate = float(inp.get("ground_ball_rate", 0.43) or 0.43)
    projected_ip = float(inp.get("projected_ip", 5.3) or 5.3)

    if k_rate >= 0.265 or weapon_count >= 3:
        bucket = "Power K Arm"
    elif k_rate < 0.205 and gb_rate >= 0.47:
        bucket = "Pitch-to-Contact Innings Eater"
    elif k_rate < 0.225 and gb_rate >= 0.48:
        bucket = "Contact Manager"
    elif projected_ip < 5.0:
        bucket = "Volatile / Limited Leash"
    else:
        bucket = "Balanced Starter"

    return {
        "bucket": bucket,
        "k_rate": round(k_rate * 100, 1),
        "ground_ball_rate": round(gb_rate * 100, 1),
        "projected_ip": round(projected_ip, 2),
        "weapon_count": weapon_count,
        "status": f"{bucket}: K% {k_rate * 100:.1f}, GB% {gb_rate * 100:.1f}, weapons {weapon_count}.",
    }


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def recent_same_opponent_start_context(pitcher, pitcher_team, opponent, slate_date_text, lookback_days=45):
    """Find whether the pitcher recently faced the same opponent and flag early-hook risk.

    This is intentionally a small confidence layer. It does not force a play; it
    only warns when the same opponent/park profile recently shortened the starter.
    """
    try:
        slate_dt = pd.to_datetime(str(slate_date_text)).date()
    except Exception:
        slate_dt = date.today()
    start_dt = slate_dt - pd.Timedelta(days=int(lookback_days))
    end_dt = slate_dt - pd.Timedelta(days=1)

    try:
        response = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={
                "sportId": 1,
                "startDate": start_dt.strftime("%Y-%m-%d"),
                "endDate": end_dt.strftime("%Y-%m-%d"),
            },
            timeout=15,
        )
        response.raise_for_status()
        sched = response.json()
    except Exception as e:
        return {"status": f"Same-opponent check unavailable: {e}", "found": False, "risk_score": 0}

    pitcher_variants = name_match_variants(pitcher) if "name_match_variants" in globals() else {normalize_match_text(pitcher)}
    team_norms = {normalize_match_text(x) for x in _team_keys(pitcher_team)} if "_team_keys" in globals() else {normalize_match_text(pitcher_team)}
    opp_norms = {normalize_match_text(x) for x in _team_keys(opponent)} if "_team_keys" in globals() else {normalize_match_text(opponent)}

    candidates = []
    for day in sched.get("dates", []):
        for game in day.get("games", []):
            teams = game.get("teams", {}) or {}
            home_name = teams.get("home", {}).get("team", {}).get("name", "")
            away_name = teams.get("away", {}).get("team", {}).get("name", "")
            home_norm = normalize_match_text(home_name)
            away_norm = normalize_match_text(away_name)
            matchup_has_team = home_norm in team_norms or away_norm in team_norms
            matchup_has_opp = home_norm in opp_norms or away_norm in opp_norms
            if matchup_has_team and matchup_has_opp:
                candidates.append(game)

    if not candidates:
        return {"status": "No same-opponent start found in lookback window.", "found": False, "risk_score": 0}

    # Check newest first.
    candidates = sorted(candidates, key=lambda g: str(g.get("gameDate", "")), reverse=True)
    for game in candidates[:6]:
        game_pk = str(game.get("gamePk", ""))
        try:
            box = requests.get(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore", timeout=15).json()
        except Exception:
            continue

        for side in ["home", "away"]:
            players = box.get("teams", {}).get(side, {}).get("players", {}) or {}
            for _, pdata in players.items():
                names = [
                    (pdata.get("person", {}) or {}).get("fullName", ""),
                    pdata.get("boxscoreName", ""),
                    pdata.get("name", ""),
                ]
                name_norms = set()
                for n in names:
                    name_norms.update(name_match_variants(n) if "name_match_variants" in globals() else {normalize_match_text(n)})
                if not pitcher_variants.intersection(name_norms):
                    continue

                pitching = pdata.get("stats", {}).get("pitching", {}) or {}
                if not pitching:
                    continue
                outs = _mlb_num(pitching.get("outs", 0), 0)
                ip = outs / 3.0 if outs else _parse_mlb_innings_pitched(pitching.get("inningsPitched", 0), 0)
                ks = _mlb_num(pitching.get("strikeOuts", 0), 0)
                runs = _mlb_num(pitching.get("runs", pitching.get("earnedRuns", 0)), 0)
                walks = _mlb_num(pitching.get("baseOnBalls", 0), 0)

                risk_score = 0
                if ip < 5.0:
                    risk_score += 1
                if runs >= 4:
                    risk_score += 1
                if walks >= 3:
                    risk_score += 1
                risk = "High" if risk_score >= 2 else "Medium" if risk_score == 1 else "Low"
                game_date = str(game.get("gameDate", ""))[:10]
                return {
                    "found": True,
                    "risk": risk,
                    "risk_score": risk_score,
                    "date": game_date,
                    "game_pk": game_pk,
                    "ip": round(ip, 2),
                    "ks": int(ks),
                    "runs": int(runs),
                    "walks": int(walks),
                    "status": f"Same-opponent lookback: {game_date}, {ip:.1f} IP, {int(ks)} K, {int(runs)} R, {int(walks)} BB ({risk} risk).",
                }

    return {"status": "Same-opponent games found, but pitcher was not matched in boxscore.", "found": False, "risk_score": 0}


def build_pitcher_k_context(pitcher, pitcher_team, opponent, game_environment, pitcher_this_year, pitcher_last_year, arsenal_details=None, slate_date_text=None):
    archetype = classify_pitcher_archetype(pitcher, pitcher_this_year, pitcher_last_year, arsenal_details)
    same_opp = recent_same_opponent_start_context(
        pitcher,
        pitcher_team,
        opponent,
        str(slate_date_text or date.today()),
    )
    env = game_environment if isinstance(game_environment, dict) else build_game_environment("")
    base_adj = float(env.get("k_projection_adjustment", 0.0) or 0.0)

    # Contact managers are more fragile in hitter environments because the issue
    # is not only strikeout skill; it is early-exit risk when balls in play turn
    # into hits/runs.
    archetype_bucket = str(archetype.get("bucket", ""))
    if archetype_bucket in ["Contact Manager", "Pitch-to-Contact Innings Eater"] and env.get("early_hook_risk") in ["Medium", "High"]:
        base_adj -= 0.08 if env.get("early_hook_risk") == "Medium" else 0.14

    if same_opp.get("risk_score", 0) >= 2:
        base_adj -= 0.06

    final_adj = _total_clip(base_adj, -0.45, 0.25)

    return {
        "environment": env,
        "archetype": archetype,
        "same_opponent": same_opp,
        "k_projection_adjustment": round(final_adj, 2),
        "early_hook_risk": env.get("early_hook_risk", "Low"),
        "status": f"K context: {archetype.get('bucket')} | {env.get('run_environment_tag')} | adj {final_adj:+.2f} K | early-hook {env.get('early_hook_risk', 'Low')}.",
    }


def apply_k_context_projection(value, k_context):
    try:
        return max(0.0, float(value or 0.0) + float((k_context or {}).get("k_projection_adjustment", 0.0) or 0.0))
    except Exception:
        return value


def apply_k_context_to_grade(grade, k_context, edge=0.0, line=0.0):
    """Final K confidence/risk layer after projection, recent form, and weapon floor.

    This does not create new bets. It only downgrades risky over setups when the
    environment + pitcher archetype + same-opponent history make early exit more likely.
    """
    original = str(grade or "PASS").upper().strip()
    if original not in ["STRONG OVER", "OVER", "LEAN OVER"]:
        return grade, ""

    env = (k_context or {}).get("environment", {}) if isinstance(k_context, dict) else {}
    archetype = (k_context or {}).get("archetype", {}) if isinstance(k_context, dict) else {}
    same_opp = (k_context or {}).get("same_opponent", {}) if isinstance(k_context, dict) else {}

    risk_points = 0
    reasons = []

    if env.get("early_hook_risk") == "High":
        risk_points += 2
        reasons.append("high early-hook environment")
    elif env.get("early_hook_risk") == "Medium":
        risk_points += 1
        reasons.append("medium early-hook environment")

    bucket = str(archetype.get("bucket", ""))
    if bucket in ["Contact Manager", "Pitch-to-Contact Innings Eater"]:
        risk_points += 2
        reasons.append(bucket)
    elif bucket == "Volatile / Limited Leash":
        risk_points += 1
        reasons.append(bucket)

    if same_opp.get("risk_score", 0) >= 2:
        risk_points += 1
        reasons.append("recent same-opponent trouble")

    try:
        win_number = _k_prop_win_number(line)
    except Exception:
        win_number = 5
    if win_number >= 5 and bucket in ["Contact Manager", "Pitch-to-Contact Innings Eater"] and env.get("run_environment_tag") in ["Hitter Friendly", "Extreme Hitter"]:
        risk_points += 1
        reasons.append("5+ K requirement in hitter environment")

    if risk_points >= 4:
        downgrade = {"STRONG OVER": "OVER", "OVER": "LEAN OVER", "LEAN OVER": "PASS"}
    elif risk_points >= 3:
        downgrade = {"STRONG OVER": "OVER", "OVER": "LEAN OVER", "LEAN OVER": "PASS"}
    else:
        return grade, ""

    adjusted = downgrade.get(original, original)
    if adjusted != original:
        note = f"K context downgrade: {', '.join(reasons)} changed {original} → {adjusted}."
        return adjusted, note
    return grade, ""



def _team_total_offense_score(team, opposing_throw, team_hitting, team_batting_rhp, team_batting_lhp):
    """Run creation score for totals. Positive = more expected runs."""
    all_obp = clean_percent(get_value(team_hitting, "Teams", team, "Team On-Base %", 0.315))
    all_slg = clean_percent(get_value(team_hitting, "Teams", team, "Team Slugging %", 0.410))
    all_avg = clean_percent(get_value(team_hitting, "Teams", team, "Team Batting Avg.", 0.250))

    split_df = team_batting_lhp if str(opposing_throw).upper().startswith("L") else team_batting_rhp
    split_obp = clean_percent(get_value(split_df, "Teams", team, "On-Base %", all_obp or 0.315))
    split_slg = clean_percent(get_value(split_df, "Teams", team, "Slug %", all_slg or 0.410))
    split_avg = clean_percent(get_value(split_df, "Teams", team, "Batting Average", all_avg or 0.250))
    split_k = clean_percent(get_value(split_df, "Teams", team, "K%", 0.220))
    split_iso = clean_percent(get_value(split_df, "Teams", team, "ISO", max(0.120, split_slg - split_avg)))

    obp = 0.55 * all_obp + 0.45 * split_obp
    slg = 0.55 * all_slg + 0.45 * split_slg
    avg = 0.55 * all_avg + 0.45 * split_avg
    iso = split_iso if split_iso > 0 else max(0.120, slg - avg)

    score = (
        (obp - 0.315) * 19.0 +
        (slg - 0.410) * 10.0 +
        (avg - 0.250) * 5.0 +
        (iso - 0.160) * 5.5 -
        (split_k - 0.220) * 2.5
    )
    return round(_total_clip(score, -1.80, 1.80), 3)


def _pitcher_total_run_risk(pitcher, pitcher_this_year, pitcher_last_year):
    """Starter run-risk score for totals. Positive = more runs allowed risk."""
    xw_last = clean_percent(get_value(pitcher_last_year, "Player", pitcher, "xwOBA", 0))
    xw_this = clean_percent(get_value(pitcher_this_year, "Player", pitcher, "xwOBA", 0))
    if xw_last > 0 and xw_this > 0:
        xwoba = 0.55 * xw_last + 0.45 * xw_this
    elif xw_this > 0:
        xwoba = xw_this
    elif xw_last > 0:
        xwoba = xw_last
    else:
        xwoba = 0.320

    ip_last = _total_float(get_value(pitcher_last_year, "Player", pitcher, "IP", 0), 0)
    ip_this = _total_float(get_value(pitcher_this_year, "Player", pitcher, "IP", 0), 0)
    gs_last = max(1.0, _total_float(get_value(pitcher_last_year, "Player", pitcher, "Games Started", get_value(pitcher_last_year, "Player", pitcher, "GS", 1)), 1))
    gs_this = max(1.0, _total_float(get_value(pitcher_this_year, "Player", pitcher, "Games Started", get_value(pitcher_this_year, "Player", pitcher, "GS", 1)), 1))
    ip_per_start_last = ip_last / gs_last if ip_last > 0 else 5.3
    ip_per_start_this = ip_this / gs_this if ip_this > 0 else ip_per_start_last
    projected_ip = _total_clip((0.55 * ip_per_start_last + 0.45 * ip_per_start_this), 3.0, 7.2)

    so_last = _total_float(get_value(pitcher_last_year, "Player", pitcher, "SO", 0), 0)
    so_this = _total_float(get_value(pitcher_this_year, "Player", pitcher, "SO", 0), 0)
    ip_blend = max(1.0, 0.55 * ip_last + 0.45 * ip_this)
    k_per_9 = ((0.55 * so_last + 0.45 * so_this) / ip_blend) * 9.0

    bb_pct_last = clean_percent(get_value(pitcher_last_year, "Player", pitcher, "BB%", 0.085))
    bb_pct_this = clean_percent(get_value(pitcher_this_year, "Player", pitcher, "BB%", bb_pct_last or 0.085))
    bb_rate = 0.55 * bb_pct_last + 0.45 * bb_pct_this
    if bb_rate <= 0:
        bb_last = _total_float(get_value(pitcher_last_year, "Player", pitcher, "BB", 0), 0)
        bb_this = _total_float(get_value(pitcher_this_year, "Player", pitcher, "BB", 0), 0)
        bb_rate = ((0.55 * bb_last + 0.45 * bb_this) / max(1.0, ip_blend * 4.25))

    hr_last = _total_float(get_value(pitcher_last_year, "Player", pitcher, "HR", 0), 0)
    hr_this = _total_float(get_value(pitcher_this_year, "Player", pitcher, "HR", 0), 0)
    hr_per_9 = ((0.55 * hr_last + 0.45 * hr_this) / ip_blend) * 9.0 if (hr_last + hr_this) > 0 else 1.10

    score = (
        (xwoba - 0.320) * 17.0 +
        (bb_rate - 0.085) * 13.0 -
        (k_per_9 - 8.3) * 0.080 +
        (hr_per_9 - 1.10) * 0.240 +
        (5.5 - projected_ip) * 0.170
    )
    return round(_total_clip(score, -1.85, 1.85), 3)


def _bullpen_total_run_risk(team, bullpen_stats, bullpen_fatigue_df=None):
    """Bullpen run-risk score for totals. Positive = more late-game scoring risk."""
    risk = 0.0
    used = 0
    if bullpen_stats is not None and not bullpen_stats.empty:
        era = _get_bullpen_numeric(bullpen_stats, team, ["ERA", "Bullpen ERA"], None)
        xwoba = _get_bullpen_numeric(bullpen_stats, team, ["xwOBA", "Bullpen xwOBA"], None)
        whip = _get_bullpen_numeric(bullpen_stats, team, ["WHIP", "Bullpen WHIP"], None)
        bb_pct = _get_bullpen_numeric(bullpen_stats, team, ["BB%", "Walk %", "Bullpen BB%"], None)
        if era is not None and era > 0:
            risk += (era - 4.10) * 0.20
            used += 1
        if xwoba is not None and xwoba > 0:
            risk += (xwoba - 0.320) * 8.5
            used += 1
        if whip is not None and whip > 0:
            risk += (whip - 1.28) * 0.55
            used += 1
        if bb_pct is not None and bb_pct > 0:
            risk += (clean_percent(bb_pct) - 0.085) * 6.5
            used += 1
    if used == 0:
        risk = 0.0

    # Add a small fatigue layer if the same bullpen fatigue table exists.
    fatigue = _bullpen_fatigue_adjustment(team, bullpen_fatigue_df)
    risk += -fatigue * 0.05  # positive fatigue penalty in ML is negative pitching score; invert into run risk.
    return round(_total_clip(risk, -1.40, 1.40), 3)


def _defense_baserunning_total_modifier(scoring_team, fielding_team, team_hitting):
    """Small modifier. Positive = more runs for scoring_team.

    Works only if your available team table has useful columns; otherwise neutral.
    """
    mod = 0.0
    sb = _total_float(get_value(team_hitting, "Teams", scoring_team, "SB", 0), 0)
    cs = _total_float(get_value(team_hitting, "Teams", scoring_team, "CS", 0), 0)
    if sb > 0:
        sb_eff = sb / max(1.0, sb + cs)
        mod += _total_clip((sb_eff - 0.72) * 0.30, -0.08, 0.08)

    errors = _total_float(get_value(team_hitting, "Teams", fielding_team, "Errors", get_value(team_hitting, "Teams", fielding_team, "E", 0)), 0)
    if errors > 0:
        mod += _total_clip((errors - 35.0) * 0.004, -0.10, 0.10)

    return round(_total_clip(mod, -0.20, 0.20), 3)


def _legacy__count_total_confluence_v1(side, components):
    if side == "OVER":
        thresholds = {
            "offense": 0.35,
            "starter": 0.35,
            "bullpen": 0.25,
            "defense_baserunning": 0.08,
            "park_weather": 0.20,
            "raw_edge": 1.00,
        }
        return sum(1 for key, threshold in thresholds.items() if components.get(key, 0) >= threshold)
    if side == "UNDER":
        thresholds = {
            "offense": -0.35,
            "starter": -0.35,
            "bullpen": -0.25,
            "defense_baserunning": -0.08,
            "park_weather": -0.20,
            "raw_edge": -1.00,
        }
        return sum(1 for key, threshold in thresholds.items() if components.get(key, 0) <= threshold)
    return 0


def _legacy_total_runs_projection_v1(home, away, hp, ap, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, bullpen_stats=None, use_home_bullpen=False, use_away_bullpen=False, bullpen_fatigue_df=None, market_total=None, home_lineup_details=None, away_lineup_details=None, home_arsenal_details=None, away_arsenal_details=None, game_environment=None):
    """Project full-game total runs from the same v5 run engine used by moneylines.

    v6 change: totals no longer use a separate totals-only formula. The game total is
    derived from the moneyline engine's team run projections so moneyline and totals
    share the same baseball assumptions:
    - team baseline runs: 50% season R/G + 50% last-14 R/G, capped and league-shrunk
    - confirmed lineup adjustment
    - opposing starter-vs-lineup arsenal matchup
    - opponent leash / projected starter IP
    - bullpen exposure and bullpen run rate
    """
    try:
        _, _, ml_details = moneyline_probability(
            home,
            away,
            hp,
            ap,
            pitcher_this_year,
            pitcher_last_year,
            team_hitting,
            team_batting_rhp,
            team_batting_lhp,
            bullpen_stats=bullpen_stats,
            use_home_bullpen=use_home_bullpen,
            use_away_bullpen=use_away_bullpen,
            bullpen_fatigue_df=bullpen_fatigue_df,
            home_lineup_details=home_lineup_details,
            away_lineup_details=away_lineup_details,
            home_arsenal_details=home_arsenal_details,
            away_arsenal_details=away_arsenal_details,
            game_environment=game_environment,
            return_details=True,
        )
    except Exception as e:
        # Safe fallback keeps the app usable if the shared run engine has a missing input.
        market = _total_float(market_total, 0.0)
        fallback_total = market if market > 0 else 8.7
        return {
            "projected_total": round(fallback_total, 2),
            "raw_projected_total": round(fallback_total, 2),
            "market_total": market if market > 0 else "",
            "edge": 0.0,
            "side": "PASS",
            "grade": "PASS",
            "confluence": 0,
            "required_edge": TOTAL_RUN_EDGE_THRESHOLD,
            "required_confluence": TOTAL_RUN_CONFLUENCE_THRESHOLD,
            "away_projected_runs": "",
            "home_projected_runs": "",
            "status": f"Shared moneyline run engine fallback used: {e}",
            "engine": "moneyline_v5_shared_run_engine_fallback",
        }

    home_data = ml_details.get("home", {}) if isinstance(ml_details, dict) else {}
    away_data = ml_details.get("away", {}) if isinstance(ml_details, dict) else {}
    home_run_projection = home_data.get("run_projection", {}) if isinstance(home_data.get("run_projection", {}), dict) else {}
    away_run_projection = away_data.get("run_projection", {}) if isinstance(away_data.get("run_projection", {}), dict) else {}

    home_projected = _total_float(ml_details.get("home_projected_runs", home_data.get("projected_runs_scored", 0)), 0.0)
    away_projected = _total_float(ml_details.get("away_projected_runs", away_data.get("projected_runs_scored", 0)), 0.0)
    raw_total = home_projected + away_projected
    environment_details = ml_details.get("game_environment", {}) if isinstance(ml_details, dict) else {}
    park_weather_pressure = _total_float(ml_details.get("park_weather_total_adjustment", (environment_details or {}).get("total_run_adjustment", 0.0)), 0.0)

    market = _total_float(market_total, 0.0)
    projected_total = raw_total
    edge = projected_total - market if market > 0 else 0.0
    side = "OVER" if edge > 0 else "UNDER" if edge < 0 else "PASS"

    # Total confluence now uses the run-engine components rather than the old totals-only buckets.
    # Offense includes the team-specific baseline above/below league scoring environment plus split shape.
    home_base = _total_float(home_run_projection.get("base_runs", 4.35), 4.35)
    away_base = _total_float(away_run_projection.get("base_runs", 4.35), 4.35)
    baseline_pressure = (home_base + away_base) - 8.70
    offense_pressure = baseline_pressure + _total_float(home_run_projection.get("offense_adjustment", 0), 0) + _total_float(away_run_projection.get("offense_adjustment", 0), 0)
    lineup_pressure = _total_float(home_run_projection.get("lineup_adjustment", 0), 0) + _total_float(away_run_projection.get("lineup_adjustment", 0), 0)
    starter_pressure = _total_float(home_run_projection.get("opposing_starter_adjustment", 0), 0) + _total_float(away_run_projection.get("opposing_starter_adjustment", 0), 0)
    bullpen_pressure = _total_float(home_run_projection.get("opposing_bullpen_adjustment", 0), 0) + _total_float(away_run_projection.get("opposing_bullpen_adjustment", 0), 0)

    components = {
        "offense": offense_pressure,
        "starter": starter_pressure,
        "bullpen": bullpen_pressure,
        "defense_baserunning": lineup_pressure,
        "park_weather": park_weather_pressure,
        "raw_edge": projected_total - market if market > 0 else projected_total - 8.70,
    }
    confluence = _count_total_confluence(side, components)

    if side in ["OVER", "UNDER"] and abs(edge) >= TOTAL_RUN_EDGE_THRESHOLD and confluence >= TOTAL_RUN_CONFLUENCE_THRESHOLD:
        grade = f"TOTAL {side}"
    else:
        grade = "PASS"

    return {
        "projected_total": round(projected_total, 2),
        "raw_projected_total": round(raw_total, 2),
        "market_total": market if market > 0 else "",
        "edge": round(edge, 2),
        "side": side,
        "grade": grade,
        "confluence": int(confluence),
        "required_edge": TOTAL_RUN_EDGE_THRESHOLD,
        "required_confluence": TOTAL_RUN_CONFLUENCE_THRESHOLD,
        "away_projected_runs": round(away_projected, 2),
        "home_projected_runs": round(home_projected, 2),
        "away_offense_score": away_data.get("offense", {}).get("score", "") if isinstance(away_data.get("offense", {}), dict) else "",
        "home_offense_score": home_data.get("offense", {}).get("score", "") if isinstance(home_data.get("offense", {}), dict) else "",
        "away_starter_score": away_data.get("starter", {}).get("matchup_score", "") if isinstance(away_data.get("starter", {}), dict) else "",
        "home_starter_score": home_data.get("starter", {}).get("matchup_score", "") if isinstance(home_data.get("starter", {}), dict) else "",
        "away_bullpen_score": away_data.get("bullpen", {}).get("score", "") if isinstance(away_data.get("bullpen", {}), dict) else "",
        "home_bullpen_score": home_data.get("bullpen", {}).get("score", "") if isinstance(home_data.get("bullpen", {}), dict) else "",
        "away_run_projection": away_run_projection,
        "home_run_projection": home_run_projection,
        "game_environment": environment_details,
        "park_weather_adjustment": round(park_weather_pressure, 2),
        "moneyline_engine": ml_details,
        "confluence_components": {k: round(float(v), 3) for k, v in components.items()},
        "engine": "moneyline_v12_shared_run_environment_engine",
        "status": "Totals v7: projected total comes from the shared moneyline run engine plus uniform park/weather environment confluence.",
    }


def _legacy_nrfi_yrfi_grade_from_environment_v1(nrfi_prob, total_run_details=None):
    """Only allow Elite NRFI or YRFI when first-inning score and full-game run environment agree."""
    nrfi_score = nrfi_score_formula(nrfi_prob)
    yrfi_score = max(0, min(100, 50 + (0.485 - nrfi_prob) * 430))

    projected_total = _total_float((total_run_details or {}).get("projected_total", 8.5), 8.5)
    total_edge = _total_float((total_run_details or {}).get("edge", 0), 0)
    run_environment_score = max(0, min(100, 50 + ((projected_total - 8.5) * 9.0) + (total_edge * 4.0)))

    grade = "PASS"
    if nrfi_score >= 88 and run_environment_score <= 54 and projected_total <= 8.8:
        grade = "ELITE NRFI"
    elif yrfi_score >= 66 and run_environment_score >= 48 and projected_total >= 8.0:
        grade = "YRFI"

    return {
        "grade": grade,
        "nrfi_score": round(nrfi_score, 1),
        "yrfi_score": round(yrfi_score, 1),
        "run_environment_score": round(run_environment_score, 1),
        "projected_total": projected_total,
        "total_edge": total_edge,
    }


# -----------------------
# MONEYLINE MODEL
# -----------------------

def _find_first_existing_col(df, candidates):
    if df is None or df.empty:
        return None
    normalized = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        key = str(cand).strip().lower()
        if key in normalized:
            return normalized[key]
    return None


def _get_bullpen_numeric(bullpen_stats, team, candidates, default=0):
    if bullpen_stats is None or bullpen_stats.empty:
        return default
    team_col = _find_first_existing_col(bullpen_stats, ["Teams", "Team", "Tm", "Name"])
    value_col = _find_first_existing_col(bullpen_stats, candidates)
    if not team_col or not value_col:
        return default
    try:
        temp = bullpen_stats.copy()
        temp[team_col] = temp[team_col].replace(TEAM_ABBR_MAP).astype(str).str.strip()
        lookup_team = str(team).strip()
        row = temp[temp[team_col] == lookup_team]
        if row.empty:
            return default
        value = row.iloc[0][value_col]
        if pd.isna(value):
            return default
        return clean_percent(value) if "%" in str(value_col) or "rate" in str(value_col).lower() else float(str(value).replace("%", "").strip())
    except Exception:
        return default


def _bullpen_pitch_score(team, bullpen_stats):
    """Return a moneyline pitching score from team bullpen data.

    This version includes optional contact-suppression fields. If the bullpen
    sheet has Hard Hit %, Barrel %, GB%, HR/9, or HR columns, they now influence
    moneyline and total run projections. Missing columns simply fall back.
    """
    if bullpen_stats is None or bullpen_stats.empty:
        return None

    era = _get_bullpen_numeric(bullpen_stats, team, ["ERA", "era", "Bullpen ERA"], None)
    xwoba = _get_bullpen_numeric(bullpen_stats, team, ["xwOBA", "xwoba", "Bullpen xwOBA"], None)
    k_pct = _get_bullpen_numeric(bullpen_stats, team, ["K%", "SO%", "Strikeout %", "Bullpen K%"], None)
    bb_pct = _get_bullpen_numeric(bullpen_stats, team, ["BB%", "Walk %", "Bullpen BB%"], None)
    whip = _get_bullpen_numeric(bullpen_stats, team, ["WHIP", "Bullpen WHIP"], None)
    hard_hit = _get_bullpen_numeric(bullpen_stats, team, ["Hard Hit %", "HardHit%", "Bullpen Hard Hit %"], None)
    barrel = _get_bullpen_numeric(bullpen_stats, team, ["Barrel %", "Barrel%", "Bullpen Barrel %"], None)
    gb = _get_bullpen_numeric(bullpen_stats, team, ["GB%", "Ground Ball %", "Bullpen GB%"], None)
    hr9 = _get_bullpen_numeric(bullpen_stats, team, ["HR/9", "HR9", "Bullpen HR/9"], None)

    score = 0.0
    used = 0

    if xwoba is not None and xwoba > 0:
        score += (0.320 - xwoba) * 280
        used += 1
    if era is not None and era > 0:
        score += (4.10 - era) * 4.5
        used += 1
    if k_pct is not None and k_pct > 0:
        k_rate = clean_percent(k_pct)
        score += (k_rate - 0.225) * 80
        used += 1
    if bb_pct is not None and bb_pct > 0:
        bb_rate = clean_percent(bb_pct)
        score -= (bb_rate - 0.085) * 70
        used += 1
    if whip is not None and whip > 0:
        score += (1.28 - whip) * 10
        used += 1

    contact_used = False
    hard_rate = clean_percent(hard_hit) if hard_hit is not None and hard_hit > 0 else 0.395
    barrel_rate = clean_percent(barrel) if barrel is not None and barrel > 0 else 0.075
    gb_rate = clean_percent(gb) if gb is not None and gb > 0 else 0.430
    try:
        hr9_val = float(hr9) if hr9 is not None and float(hr9) > 0 else 1.10
    except Exception:
        hr9_val = 1.10
    if (hard_hit is not None and hard_hit > 0) or (barrel is not None and barrel > 0) or (gb is not None and gb > 0) or (hr9 is not None and hr9 > 0):
        score += _contact_suppression_score(hard_rate, barrel_rate, gb_rate, hr9_val)
        contact_used = True

    if used == 0 and not contact_used:
        return None
    return score

def _cap(value, low, high):
    try:
        return max(float(low), min(float(high), float(value)))
    except Exception:
        return 0.0



def _blend_pitcher_rate(pitcher, pitcher_this_year, pitcher_last_year, column_candidates, default=0.0, lower=0.0, upper=1.0):
    """Blend current/last-year pitcher rate fields using flexible column names.

    This is built for optional contact-suppression columns. If a column does not
    exist in the uploaded/live table, the model falls back safely instead of
    breaking the builder.
    """
    def _first_rate(df):
        if df is None or getattr(df, "empty", True):
            return 0.0
        for col in column_candidates:
            try:
                val = get_value(df, "Player", pitcher, col, 0)
                rate = clean_percent(val)
                if rate > 0:
                    return float(rate)
            except Exception:
                continue
        return 0.0

    last_val = _first_rate(pitcher_last_year)
    this_val = _first_rate(pitcher_this_year)
    if this_val > 0 and last_val > 0:
        blended = (0.60 * this_val) + (0.40 * last_val)
    elif this_val > 0:
        blended = this_val
    elif last_val > 0:
        blended = last_val
    else:
        blended = float(default)
    return _cap(blended, lower, upper)


def _blend_pitcher_hr_per_9(pitcher, pitcher_this_year, pitcher_last_year, default=1.10):
    """Blend HR/9 from either an explicit HR/9 column or HR and IP columns."""
    def _one(df):
        if df is None or getattr(df, "empty", True):
            return 0.0
        explicit = _blend_pitcher_rate(pitcher, df, pd.DataFrame(), ["HR/9", "HR9", "Home Runs Per 9"], 0.0, 0.0, 4.0)
        if explicit > 0:
            return explicit
        try:
            hr = _to_number(get_value(df, "Player", pitcher, "HR", 0), 0)
            ip = _to_number(get_value(df, "Player", pitcher, "IP", 0), 0)
            if hr > 0 and ip > 0:
                return (hr * 9.0) / ip
        except Exception:
            pass
        return 0.0

    last_val = _one(pitcher_last_year)
    this_val = _one(pitcher_this_year)
    if this_val > 0 and last_val > 0:
        blended = (0.60 * this_val) + (0.40 * last_val)
    elif this_val > 0:
        blended = this_val
    elif last_val > 0:
        blended = last_val
    else:
        blended = float(default)
    return _cap(blended, 0.25, 2.40)


def _contact_suppression_score(hard_hit_rate, barrel_rate, ground_ball_rate, hr_per_9):
    """Positive = run suppression; negative = contact danger.

    This adds the missing run-prevention pieces that strikeout models do not see:
    hard contact, barrels, ground balls, and home-run prevention.
    """
    hard_component = (0.395 - float(hard_hit_rate or 0.395)) * 26.0
    barrel_component = (0.075 - float(barrel_rate or 0.075)) * 85.0
    gb_component = (float(ground_ball_rate or 0.430) - 0.430) * 16.0
    hr_component = (1.10 - float(hr_per_9 or 1.10)) * 0.90
    return _cap(hard_component + barrel_component + gb_component + hr_component, -5.0, 5.0)

def _legacy__pitcher_skill_inputs_v1(pitcher, pitcher_this_year, pitcher_last_year):
    """Return blended starter inputs for the moneyline/run model.

    This version adds contact suppression so totals and moneylines do not rely
    only on xwOBA, strikeouts, walks, arsenal, and leash. The new fields are
    optional and safe: if your live/source table lacks Barrel %, GB%, or HR/9,
    the model falls back to league-average assumptions.
    """
    xw_last = clean_percent(get_value(pitcher_last_year, "Player", pitcher, "xwOBA", 0))
    xw_this = clean_percent(get_value(pitcher_this_year, "Player", pitcher, "xwOBA", 0))
    xwoba = 0.55 * xw_last + 0.45 * xw_this if xw_last > 0 and xw_this > 0 else xw_this if xw_this > 0 else xw_last
    if xwoba <= 0:
        xwoba = 0.320

    ip_last = _to_number(get_value(pitcher_last_year, "Player", pitcher, "IP", 0), 0)
    ip_this = _to_number(get_value(pitcher_this_year, "Player", pitcher, "IP", 0), 0)
    g_last = _to_number(get_value(pitcher_last_year, "Player", pitcher, "G", 0), 0)
    g_this = _to_number(get_value(pitcher_this_year, "Player", pitcher, "G", 0), 0)
    so_last = _to_number(get_value(pitcher_last_year, "Player", pitcher, "SO", 0), 0)
    so_this = _to_number(get_value(pitcher_this_year, "Player", pitcher, "SO", 0), 0)

    ipg_last = ip_last / g_last if g_last > 0 else 0
    ipg_this = ip_this / g_this if g_this > 0 else 0
    if ipg_this > 0 and ipg_last > 0:
        projected_ip = (0.65 * ipg_this) + (0.35 * ipg_last)
    elif ipg_this > 0:
        projected_ip = ipg_this
    elif ipg_last > 0:
        projected_ip = ipg_last
    else:
        projected_ip = 5.0
    projected_ip = _cap(projected_ip, 3.2, 7.2)

    bf_last = ip_last * 4.3
    bf_this = ip_this * 4.3
    k_rate_last = so_last / bf_last if bf_last > 0 else 0
    k_rate_this = so_this / bf_this if bf_this > 0 else 0
    if k_rate_this > 0 and k_rate_last > 0:
        k_rate = (0.60 * k_rate_this) + (0.40 * k_rate_last)
    else:
        k_rate = k_rate_this if k_rate_this > 0 else k_rate_last
    k_rate = _cap(k_rate if k_rate > 0 else 0.215, 0.10, 0.38)

    bb_last = clean_percent(get_value(pitcher_last_year, "Player", pitcher, "BB%", 0))
    bb_this = clean_percent(get_value(pitcher_this_year, "Player", pitcher, "BB%", 0))
    if bb_last <= 0:
        walks_last = _to_number(get_value(pitcher_last_year, "Player", pitcher, "BB", 0), 0)
        bb_last = walks_last / bf_last if bf_last > 0 else 0
    if bb_this <= 0:
        walks_this = _to_number(get_value(pitcher_this_year, "Player", pitcher, "BB", 0), 0)
        bb_this = walks_this / bf_this if bf_this > 0 else 0
    if bb_this > 0 and bb_last > 0:
        bb_rate = (0.60 * bb_this) + (0.40 * bb_last)
    else:
        bb_rate = bb_this if bb_this > 0 else bb_last
    bb_rate = _cap(bb_rate if bb_rate > 0 else 0.085, 0.025, 0.17)

    hard_hit_rate = _blend_pitcher_rate(
        pitcher, pitcher_this_year, pitcher_last_year,
        ["Hard Hit %", "HardHit%", "Hard Hit%", "hard_hit_percent", "hard_hit_pct"],
        default=0.395, lower=0.25, upper=0.58,
    )
    barrel_rate = _blend_pitcher_rate(
        pitcher, pitcher_this_year, pitcher_last_year,
        ["Barrel %", "Barrel%", "Barrels %", "barrel_percent", "barrels_percent", "barrel_batted_rate", "barrel_rate"],
        default=0.075, lower=0.025, upper=0.18,
    )
    ground_ball_rate = _blend_pitcher_rate(
        pitcher, pitcher_this_year, pitcher_last_year,
        ["GB%", "GB %", "Ground Ball %", "GroundBall%", "ground_ball_percent", "groundballs_percent", "gb_percent", "gb_rate"],
        default=0.430, lower=0.25, upper=0.62,
    )
    hr_per_9 = _blend_pitcher_hr_per_9(pitcher, pitcher_this_year, pitcher_last_year, default=1.10)
    contact_score = _contact_suppression_score(hard_hit_rate, barrel_rate, ground_ball_rate, hr_per_9)

    return {
        "xwoba": xwoba,
        "k_rate": k_rate,
        "bb_rate": bb_rate,
        "hard_hit_rate": hard_hit_rate,
        "barrel_rate": barrel_rate,
        "ground_ball_rate": ground_ball_rate,
        "hr_per_9": hr_per_9,
        "contact_score": contact_score,
        "projected_ip": projected_ip,
        "ipg_this": ipg_this,
        "ipg_last": ipg_last,
    }


def _parse_mlb_innings_pitched(value, default=0.0):
    """Convert MLB innings strings like 6.2 into true decimal innings (6 + 2/3)."""
    try:
        text = str(value).strip()
        if text in ["", "None", "nan", "--"]:
            return float(default)
        if "." in text:
            whole, frac = text.split(".", 1)
            whole_i = int(float(whole or 0))
            # Baseball notation: .1 = one out, .2 = two outs.
            if frac[:1] in ["1", "2"] and len(frac) == 1:
                return whole_i + (int(frac) / 3.0)
        return float(text)
    except Exception:
        return float(default)


@st.cache_data(ttl=60 * 60, show_spinner=False)
def _mlb_pitcher_start_run_log(player_id, season=MLB_SEASON):
    """Pull starter game logs from MLB Stats API and summarize RA/9.

    This is used by the shared moneyline/totals run engine. It intentionally uses
    actual runs allowed (RA), not only earned runs, because betting totals and
    moneylines care about scoreboard runs. If the API does not expose runs for a
    row, earned runs are used as a safe fallback.
    """
    try:
        player_id = str(player_id or "").replace(".0", "").strip()
        if not player_id:
            return {
                "starts": 0,
                "season_ra9": 4.35,
                "last5_ra9": 4.35,
                "home_ra9": 4.35,
                "away_ra9": 4.35,
                "home_starts": 0,
                "away_starts": 0,
                "status": "No MLBAM ID for starter game-log RA/9",
                "last5_rows": [],
            }

        response = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats",
            params={"stats": "gameLog", "group": "pitching", "season": str(season)},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        splits = (((data.get("stats") or [{}])[0]).get("splits") or [])
        rows = []
        for split in splits:
            stat = split.get("stat", {}) or {}
            ip = _parse_mlb_innings_pitched(stat.get("inningsPitched", 0), 0.0)
            if ip <= 0:
                continue

            gs_val = stat.get("gamesStarted", stat.get("gameStarted", stat.get("gamesStartedPitching", "")))
            is_start = False
            try:
                is_start = float(gs_val or 0) >= 1
            except Exception:
                is_start = False
            # Some game-log rows do not expose gamesStarted cleanly. For probable starters,
            # an IP workload of at least 3.0 is a practical fallback for starter history.
            if not is_start and ip < 3.0:
                continue

            runs = stat.get("runs", None)
            if runs in [None, "", "-"]:
                runs = stat.get("earnedRuns", 0)
            try:
                runs = float(runs or 0)
            except Exception:
                runs = 0.0

            is_home = split.get("isHome", None)
            venue_side = "home" if is_home is True else "away" if is_home is False else ""
            game_date = str(split.get("date", ""))
            rows.append({
                "date": game_date,
                "ip": float(ip),
                "runs": float(runs),
                "ra9": (float(runs) * 9.0 / float(ip)) if ip > 0 else 4.35,
                "home_away": venue_side,
                "opponent": ((split.get("opponent") or {}).get("name", "")),
            })

        rows = sorted(rows, key=lambda x: x.get("date", ""), reverse=True)
        if not rows:
            return {
                "starts": 0,
                "season_ra9": 4.35,
                "last5_ra9": 4.35,
                "home_ra9": 4.35,
                "away_ra9": 4.35,
                "home_starts": 0,
                "away_starts": 0,
                "status": "No starter game-log rows found; using fallback",
                "last5_rows": [],
            }

        def _weighted_ra9(subrows):
            ip_sum = sum(float(r.get("ip", 0) or 0) for r in subrows)
            runs_sum = sum(float(r.get("runs", 0) or 0) for r in subrows)
            return (runs_sum * 9.0 / ip_sum) if ip_sum > 0 else 4.35

        last5 = rows[:5]
        home_rows = [r for r in rows if r.get("home_away") == "home"]
        away_rows = [r for r in rows if r.get("home_away") == "away"]
        return {
            "starts": int(len(rows)),
            "season_ra9": round(float(_weighted_ra9(rows)), 3),
            "last5_ra9": round(float(_weighted_ra9(last5)), 3),
            "home_ra9": round(float(_weighted_ra9(home_rows)), 3) if home_rows else 4.35,
            "away_ra9": round(float(_weighted_ra9(away_rows)), 3) if away_rows else 4.35,
            "home_starts": int(len(home_rows)),
            "away_starts": int(len(away_rows)),
            "last5_rows": last5,
            "status": "Starter game-log RA/9 loaded from MLB Stats API",
        }
    except Exception as e:
        return {
            "starts": 0,
            "season_ra9": 4.35,
            "last5_ra9": 4.35,
            "home_ra9": 4.35,
            "away_ra9": 4.35,
            "home_starts": 0,
            "away_starts": 0,
            "status": f"Starter game-log fallback: {e}",
            "last5_rows": [],
        }


def _pitcher_mlbam_id(pitcher, pitcher_this_year, pitcher_last_year):
    for df in [pitcher_this_year, pitcher_last_year]:
        try:
            val = get_value(df, "Player", pitcher, "MLBAM ID", "")
            val = str(val or "").replace(".0", "").strip()
            if val and val.lower() not in ["nan", "none", "0"]:
                return val
        except Exception:
            pass
    return ""


def _starter_log_blended_ra9(pitcher, pitcher_this_year, pitcher_last_year, skill_run_rate, is_home_start=None):
    """Blend last-5 starter RA/9, season starter RA/9, home/away, and skill model.

    Design: keep totals and moneylines on the same engine while making today's
    expected pitching allocation the baseline instead of team runs/game.
    """
    pid = _pitcher_mlbam_id(pitcher, pitcher_this_year, pitcher_last_year)
    log = _mlb_pitcher_start_run_log(pid, MLB_SEASON) if pid else _mlb_pitcher_start_run_log("", MLB_SEASON)
    starts = int(log.get("starts", 0) or 0)
    last5 = float(log.get("last5_ra9", 4.35) or 4.35)
    season = float(log.get("season_ra9", 4.35) or 4.35)
    skill = float(skill_run_rate or 4.35)
    league = 4.35

    # Recent raw runs are useful, but noisy. Cap the last-5 impact so a short
    # hot/cold stretch cannot overpower season RA/9 plus the skill/contact model.
    last5_gap = _cap(last5 - season, -1.50, 1.50)
    adjusted_last5 = season + last5_gap

    if starts >= 10:
        blended = (0.50 * skill) + (0.30 * season) + (0.20 * adjusted_last5)
        blend = "10+ starts: 50% contact/skill RA/9 + 30% season starter RA/9 + 20% capped last-5 starter RA/9"
    elif starts >= 5:
        blended = (0.40 * skill) + (0.30 * season) + (0.30 * adjusted_last5)
        blend = "5-9 starts: 40% contact/skill RA/9 + 30% season starter RA/9 + 30% capped last-5 starter RA/9"
    elif starts > 0:
        blended = (0.45 * skill) + (0.30 * season) + (0.25 * league)
        blend = "<5 starts: 45% contact/skill RA/9 + 30% available starter RA/9 + 25% league baseline"
    else:
        blended = (0.65 * skill) + (0.35 * league)
        blend = "No start log: 65% contact/skill RA/9 + 35% league baseline"

    split_label = "none"
    split_starts = 0
    split_ra9 = None
    if is_home_start is True:
        split_starts = int(log.get("home_starts", 0) or 0)
        split_ra9 = float(log.get("home_ra9", blended) or blended)
        split_label = "home"
    elif is_home_start is False:
        split_starts = int(log.get("away_starts", 0) or 0)
        split_ra9 = float(log.get("away_ra9", blended) or blended)
        split_label = "away"

    if split_ra9 is not None and split_starts >= 3:
        blended = (0.75 * blended) + (0.25 * split_ra9)
        blend += f"; 25% {split_label} starter RA/9 split applied"
    elif split_ra9 is not None:
        blend += f"; {split_label} split shown but not applied (<3 starts)"

    return _cap(float(blended), 2.10, 7.20), {
        "mlbam_id": pid,
        "starts": starts,
        "last5_ra9": round(last5, 2),
        "capped_last5_ra9": round(adjusted_last5, 2),
        "recent_ra9_cap_used": round(last5_gap, 2),
        "season_starter_ra9": round(season, 2),
        "skill_model_ra9": round(skill, 2),
        "home_ra9": round(float(log.get("home_ra9", 4.35) or 4.35), 2),
        "away_ra9": round(float(log.get("away_ra9", 4.35) or 4.35), 2),
        "home_starts": int(log.get("home_starts", 0) or 0),
        "away_starts": int(log.get("away_starts", 0) or 0),
        "split_used": split_label if split_starts >= 3 else "none",
        "blended_ra9": round(float(blended), 2),
        "blend": blend,
        "last5_rows": log.get("last5_rows", []),
        "status": log.get("status", ""),
    }

def _moneyline_pitcher_profile(pitcher, pitcher_this_year, pitcher_last_year, arsenal_details=None, bullpen_override_score=None, is_home_start=None):
    """Run-based starter profile for the shared totals/moneyline engine.

    This version uses today's expected pitching allocation as the baseline:
    starter expected runs allowed over projected IP + bullpen remainder. Starter
    RA/9 is anchored heavily to last 5 starts when available, with season RA/9,
    home/away split, and the contact/xwOBA/K-BB/arsenal skill model as guardrails.
    """
    if bullpen_override_score is not None:
        score = _cap(float(bullpen_override_score or 0), -10, 10)
        run_rate = _cap(4.20 - (score * 0.16), 2.90, 5.80)
        projected_ip = 0.0
        return {
            "mode": "bullpen_game",
            "matchup_score": round(score, 2),
            "projected_ip": projected_ip,
            "starter_run_rate": round(run_rate, 2),
            "starter_runs_allowed": 0.0,
            "skill_score": round(score, 2),
            "arsenal_score": 0.0,
            "leash_ip_impact": 0.0,
            "starter_run_log": {"status": "Bullpen game: starter log not used"},
            "status": "Bullpen/opener game: starter innings removed and team bullpen context drives run prevention."
        }

    inp = _pitcher_skill_inputs(pitcher, pitcher_this_year, pitcher_last_year)

    contact_score = float(inp.get("contact_score", 0.0) or 0.0)
    skill_score = (
        (0.320 - inp["xwoba"]) * 110 +
        (inp["k_rate"] - 0.215) * 70 -
        (inp["bb_rate"] - 0.085) * 80 +
        contact_score
    )

    raw_arsenal = 0.0
    arsenal_score = 0.0
    leash_ip_impact = 0.0
    scored_pitches = 0
    try:
        if isinstance(arsenal_details, dict):
            raw_arsenal = float(arsenal_details.get("score", 0) or 0)
            scored_pitches = int(arsenal_details.get("scored_count", 0) or 0)
            arsenal_score = _cap(raw_arsenal * 0.42, -5.0, 5.0)
            leash = arsenal_details.get("opponent_leash", {}) if isinstance(arsenal_details.get("opponent_leash", {}), dict) else {}
            leash_ip_impact = _cap(float(leash.get("ip_impact", 0) or 0), -0.85, 0.85) if leash else 0.0
    except Exception:
        pass

    projected_ip = _cap(float(inp.get("projected_ip", 5.0) or 5.0) + leash_ip_impact, 3.0, 7.4)
    workload_score = (projected_ip - 5.3) * 1.8
    matchup_score = _cap(skill_score + arsenal_score + workload_score, -16, 16)

    # First build the pure skill/contact/arsenal RA/9. This is no longer the
    # whole baseline; it becomes the guardrail in the starter game-log blend.
    skill_run_rate = 4.35 - (matchup_score * 0.115)
    skill_run_rate += _cap((inp["bb_rate"] - 0.085) * 5.5, -0.25, 0.45)
    skill_run_rate -= _cap((inp["k_rate"] - 0.215) * 3.2, -0.35, 0.45)
    skill_run_rate -= _cap(contact_score * 0.105, -0.42, 0.42)
    skill_run_rate -= _cap(arsenal_score * 0.035, -0.22, 0.22)
    skill_run_rate = _cap(skill_run_rate, 2.35, 6.45)

    starter_run_rate, run_log_details = _starter_log_blended_ra9(
        pitcher,
        pitcher_this_year,
        pitcher_last_year,
        skill_run_rate,
        is_home_start=is_home_start,
    )
    starter_runs_allowed = starter_run_rate * (projected_ip / 9.0)

    return {
        "mode": "starter",
        "matchup_score": round(matchup_score, 2),
        "skill_score": round(skill_score, 2),
        "contact_suppression_score": round(contact_score, 2),
        "hard_hit_rate": round(inp.get("hard_hit_rate", 0.395) * 100, 1),
        "barrel_rate": round(inp.get("barrel_rate", 0.075) * 100, 1),
        "ground_ball_rate": round(inp.get("ground_ball_rate", 0.430) * 100, 1),
        "hr_per_9": round(inp.get("hr_per_9", 1.10), 2),
        "arsenal_score": round(arsenal_score, 2),
        "raw_arsenal_score": round(raw_arsenal, 2),
        "scored_pitch_types": scored_pitches,
        "workload_score": round(workload_score, 2),
        "leash_ip_impact": round(leash_ip_impact, 2),
        "projected_ip": round(projected_ip, 2),
        "starter_run_rate": round(starter_run_rate, 2),
        "skill_model_run_rate": round(skill_run_rate, 2),
        "starter_runs_allowed": round(starter_runs_allowed, 2),
        "starter_run_log": run_log_details,
        "xwoba": round(inp["xwoba"], 3),
        "k_rate": round(inp["k_rate"] * 100, 1),
        "bb_rate": round(inp["bb_rate"] * 100, 1),
        "status": "Starter RA/9 uses 55% last-5 starts, 25% season starts, 20% contact/skill when 5+ starts; leash allocates starter/bullpen innings."
    }



def _team_row_match(df, team):
    """Find a team row using full name, short name, abbreviation, or known aliases."""
    if df is None or df.empty:
        return None
    team_col = _find_first_existing_col(df, ["Teams", "Team", "Tm", "Name"])
    if not team_col:
        return None
    try:
        target_aliases = set()
        raw_team = str(team or "").strip()
        if raw_team:
            target_aliases.add(normalize_match_text(raw_team))
        try:
            for key in _team_keys(raw_team):
                target_aliases.add(normalize_match_text(key))
        except Exception:
            pass
        try:
            for canonical, names in TEAM_NAME_ALIASES_FOR_SAVANT.items():
                all_names = [canonical] + list(names)
                all_norm = {normalize_match_text(x) for x in all_names if str(x).strip()}
                if normalize_match_text(raw_team) in all_norm:
                    target_aliases.update(all_norm)
                    break
        except Exception:
            pass
        temp = df.copy()
        for _, row in temp.iterrows():
            row_team = str(row.get(team_col, "")).strip()
            row_aliases = {normalize_match_text(row_team)}
            try:
                for key in _team_keys(row_team):
                    row_aliases.add(normalize_match_text(key))
            except Exception:
                pass
            if target_aliases.intersection(row_aliases):
                return row
    except Exception:
        return None
    return None


def _team_numeric_from_row(row, candidates, default=0.0):
    if row is None:
        return default
    try:
        lower_map = {str(k).strip().lower(): k for k in row.index}
        for cand in candidates:
            key = str(cand).strip().lower()
            if key in lower_map:
                val = row.get(lower_map[key], default)
                if val is None or str(val).strip() == "":
                    continue
                return float(str(val).replace("%", "").strip())
    except Exception:
        pass
    return default


def _team_season_runs_from_table(team, team_hitting):
    """Prefer existing team table R/G if present; fallback to Runs/Games if available."""
    row = _team_row_match(team_hitting, team)
    if row is None:
        return 0.0
    rg = _team_numeric_from_row(row, ["R/G", "Runs/Game", "Runs Per Game", "Runs per Game", "Team Runs/Game"], 0.0)
    if rg > 0:
        return rg
    runs = _team_numeric_from_row(row, ["R", "Runs", "Team Runs"], 0.0)
    games = _team_numeric_from_row(row, ["G", "Games", "Games Played"], 0.0)
    if runs > 0 and games > 0:
        return runs / games
    return 0.0


@st.cache_data(ttl=60 * 60, show_spinner=False)
def _team_runs_per_game_from_schedule(team_name, start_date_str, end_date_str):
    """Official MLB Stats API fallback for team runs/game over a date range."""
    try:
        team_id = _team_id_from_name(team_name)
        if not team_id:
            return 0.0, 0, "Team ID unavailable"
        sched = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={
                "sportId": 1,
                "teamId": team_id,
                "startDate": start_date_str,
                "endDate": end_date_str,
            },
            timeout=20,
        ).json()
        runs = 0
        games = 0
        for day in sched.get("dates", []):
            for game in day.get("games", []):
                status = str(game.get("status", {}).get("abstractGameState", "")).lower()
                if status != "final":
                    continue
                teams = game.get("teams", {}) or {}
                side = None
                for possible in ["home", "away"]:
                    if int(teams.get(possible, {}).get("team", {}).get("id", -1)) == int(team_id):
                        side = possible
                        break
                if not side:
                    continue
                score = teams.get(side, {}).get("score", None)
                if score is None:
                    continue
                runs += int(score or 0)
                games += 1
        if games <= 0:
            return 0.0, 0, "No final games in range"
        return runs / games, games, "MLB schedule final scores"
    except Exception as e:
        return 0.0, 0, f"Schedule fallback unavailable: {e}"


def _moneyline_team_base_runs(team, team_hitting=None):
    """v7 baseline: 60% season R/G + 40% last-14-day R/G, capped, then 65/35 to league average.

    This keeps recent form in the model but reduces the upward bias that created
    too many Over days when hot offenses were double-counted by the market.
    """
    league_avg = 4.35
    season_rg = _team_season_runs_from_table(team, team_hitting)

    today_dt = date.today()
    season_start = date(MLB_SEASON, 3, 1)
    yesterday = today_dt - pd.Timedelta(days=1)
    recent_start = today_dt - pd.Timedelta(days=14)

    season_source = "team table"
    if season_rg <= 0:
        season_rg, season_games, season_status = _team_runs_per_game_from_schedule(
            team,
            season_start.strftime("%Y-%m-%d"),
            yesterday.strftime("%Y-%m-%d"),
        )
        season_source = season_status
    else:
        season_games = _team_numeric_from_row(_team_row_match(team_hitting, team), ["G", "Games", "Games Played"], 0.0)

    if season_rg <= 0:
        season_rg = league_avg
        season_source = "league average fallback"
        season_games = 0

    recent_rg, recent_games, recent_status = _team_runs_per_game_from_schedule(
        team,
        recent_start.strftime("%Y-%m-%d"),
        yesterday.strftime("%Y-%m-%d"),
    )
    if recent_rg <= 0 or recent_games < 5:
        recent_rg = season_rg
        recent_status = "season fallback; not enough recent final games"

    # Calibrated recent blend: still respects current form, but lowers the
    # tendency to project every hot-offense game over the market total.
    uncapped_recent_offense = (season_rg * 0.60) + (recent_rg * 0.40)
    capped_recent_offense = _cap(uncapped_recent_offense, season_rg - 0.55, season_rg + 0.55)

    # Shrink back toward league average so the baseline is strong but not the whole model.
    base_runs = (capped_recent_offense * 0.65) + (league_avg * 0.35)
    base_runs = _cap(base_runs, 3.15, 5.55)

    return base_runs, {
        "team": team,
        "base_runs": round(base_runs, 2),
        "league_avg_runs": round(league_avg, 2),
        "season_runs_per_game": round(float(season_rg), 2),
        "season_games": int(float(season_games or 0)) if str(season_games).replace('.', '', 1).isdigit() else season_games,
        "season_source": season_source,
        "last_14_runs_per_game": round(float(recent_rg), 2),
        "last_14_games": int(recent_games or 0),
        "last_14_source": recent_status,
        "uncapped_60_40_offense": round(float(uncapped_recent_offense), 2),
        "capped_60_40_offense": round(float(capped_recent_offense), 2),
        "recent_cap": "+/-0.55 runs from season R/G",
        "blend": "60% season R/G + 40% last-14 R/G, capped, then 65% team / 35% league average",
        "status": "Moneyline v11 calibrated team baseline applied",
    }


def _legacy__team_offensive_contact_quality_v1(team, team_hitting=None, split_df=None):
    """Offensive contact-quality score for totals/moneylines.

    Positive = offense creates dangerous contact; negative = weaker contact offense.
    Uses true team Hard Hit %, Barrel %, xwOBA, and ISO columns when available.
    If those are not present in the MLB team table, falls back to an ISO/SLG proxy
    from AVG/SLG so the layer still works without a separate Savant team file.
    """
    row = _team_row_match(split_df, team) if split_df is not None else None
    if row is None:
        row = _team_row_match(team_hitting, team)

    hard = _team_numeric_from_row(row, ["Hard Hit %", "HardHit%", "hard_hit_percent", "hard_hit_pct", "Team Hard Hit %"], 0.0)
    barrel = _team_numeric_from_row(row, ["Barrel %", "Barrel%", "barrel_batted_rate", "barrel_rate", "Team Barrel %"], 0.0)
    xwoba = _team_numeric_from_row(row, ["xwOBA", "xwoba", "Team xwOBA"], 0.0)
    iso = _team_numeric_from_row(row, ["ISO", "Iso", "isolated_power", "Team ISO"], 0.0)

    avg = _team_numeric_from_row(row, ["AVG", "Batting Average", "Team Batting Avg.", "Team AVG"], 0.250)
    slg = _team_numeric_from_row(row, ["SLG", "Slug %", "Slugging %", "Team Slugging %"], 0.410)
    obp = _team_numeric_from_row(row, ["OBP", "On-Base %", "Team On-Base %"], 0.315)

    # Normalize percent-looking values. Savant can export 42.5 or 0.425.
    hard_rate = clean_percent(hard) if hard and hard > 0 else 0.0
    barrel_rate = clean_percent(barrel) if barrel and barrel > 0 else 0.0
    if iso <= 0 and slg > 0 and avg > 0:
        iso = max(0.0, float(slg) - float(avg))

    score = 0.0
    used = []
    if hard_rate > 0:
        score += (hard_rate - 0.395) * 24.0
        used.append("hard_hit")
    if barrel_rate > 0:
        score += (barrel_rate - 0.075) * 78.0
        used.append("barrel")
    if xwoba and xwoba > 0:
        score += (float(xwoba) - 0.320) * 70.0
        used.append("xwoba")
    if iso and iso > 0:
        score += (float(iso) - 0.160) * 24.0
        used.append("iso")

    # If no true contact columns exist, use a conservative OPS/ISO proxy.
    source = "true contact columns"
    if not used:
        ops = float(obp or 0.315) + float(slg or 0.410)
        score = ((ops - 0.725) * 13.0) + ((float(iso or 0.160) - 0.160) * 18.0)
        used = ["ops_iso_proxy"]
        source = "OPS/ISO proxy; add team Savant contact columns for stronger signal"

    score = _cap(score, -6.0, 6.0)
    return score, {
        "score": round(score, 2),
        "hard_hit_rate": round(hard_rate * 100, 1) if hard_rate > 0 else "",
        "barrel_rate": round(barrel_rate * 100, 1) if barrel_rate > 0 else "",
        "xwoba": round(float(xwoba), 3) if xwoba and xwoba > 0 else "",
        "iso": round(float(iso), 3) if iso and iso > 0 else "",
        "avg": round(float(avg), 3) if avg else "",
        "obp": round(float(obp), 3) if obp else "",
        "slg": round(float(slg), 3) if slg else "",
        "source": source,
        "used": ", ".join(used),
        "status": "Offensive contact quality added to run projection",
    }


def _team_offense_component(team, opposing_throw, team_hitting, team_batting_rhp, team_batting_lhp):
    """Moneyline offense quality score plus details.

    v5 uses team runs/game for the actual run baseline, so this score is now a
    smaller matchup-shape adjustment from OBP/SLG/splits rather than the starting point.
    """
    score = _team_total_offense_score(team, opposing_throw, team_hitting, team_batting_rhp, team_batting_lhp)
    details = {
        "score": round(float(score), 2),
        "opposing_throw": opposing_throw,
        "status": "Split offense shape adjustment; team R/G baseline handled separately in v5.",
    }
    try:
        all_obp = clean_percent(get_value(team_hitting, "Teams", team, "Team On-Base %", 0.315))
        all_slg = clean_percent(get_value(team_hitting, "Teams", team, "Team Slugging %", 0.410))
        split_df = team_batting_lhp if str(opposing_throw).upper().startswith("L") else team_batting_rhp
        split_obp = clean_percent(get_value(split_df, "Teams", team, "On-Base %", all_obp or 0.315))
        split_slg = clean_percent(get_value(split_df, "Teams", team, "Slug %", all_slg or 0.410))
        details.update({
            "team_obp": round(all_obp, 3),
            "team_slg": round(all_slg, 3),
            "split_obp": round(split_obp, 3),
            "split_slg": round(split_slg, 3),
        })
    except Exception:
        pass
    return float(score), details


def _lineup_moneyline_component(lineup_details, team_offense_score):
    if not isinstance(lineup_details, dict):
        return 0.0, {"score": 0.0, "status": "No lineup details supplied"}
    hitters = int(lineup_details.get("hitters_found", 0) or 0)
    if hitters < 6:
        return 0.0, {"score": 0.0, "hitters_found": hitters, "status": "Lineup fallback; not enough confirmed hitter stats"}

    # Confidence/run adjustment now uses the actual confirmed hitters only.
    # Do not compare to team baseline here; low-sample hitters are reliability-shrunk
    # toward neutral and separately flagged in the confidence layer.
    lineup_score = float(lineup_details.get("lineup_offense_score", 0.0) or 0.0)
    k_mult = float(lineup_details.get("k_multiplier", 1.0) or 1.0)
    hand_mult = float(lineup_details.get("hand_stack_multiplier", 1.0) or 1.0)
    pitch_mult = float(lineup_details.get("pitch_type_multiplier", 1.0) or 1.0)
    low_sample_hitters = int(lineup_details.get("lineup_low_sample_hitters", 0) or 0)

    # lineup_score is already order-weighted and sample-adjusted, so keep this
    # component mostly direct. K/hand/pitch-type remain small diagnostics.
    score = (lineup_score * 0.80) - ((k_mult - 1.0) * 7) + ((hand_mult - 1.0) * 5) + ((1.0 - pitch_mult) * 4)
    score = _cap(score, -8, 8)
    return score, {
        "score": round(score, 2),
        "hitters_found": hitters,
        "lineup_obp": round(float(lineup_details.get("lineup_obp") or 0), 3) if lineup_details.get("lineup_obp") is not None else "",
        "lineup_slg": round(float(lineup_details.get("lineup_slg") or 0), 3) if lineup_details.get("lineup_slg") is not None else "",
        "lineup_ops": round(float(lineup_details.get("lineup_ops") or 0), 3) if lineup_details.get("lineup_ops") is not None else "",
        "lineup_only_score": round(lineup_score, 2),
        "low_sample_hitters": low_sample_hitters,
        "k_multiplier": round(k_mult, 3),
        "hand_stack_multiplier": round(hand_mult, 3),
        "pitch_type_multiplier": round(pitch_mult, 3),
        "status": lineup_details.get("lineup_strength_status", lineup_details.get("status", "Confirmed lineup applied")),
    }


def _legacy__bullpen_quality_component_v1(team, bullpen_stats, bullpen_fatigue_df=None):
    """Moneyline bullpen score using both quality and availability.

    Old logic leaned too heavily on fatigue, which made "bullpen score" feel like
    a usage-only grade. This version separates the two pieces:
    - Quality: season/current bullpen skill metrics (ERA, xwOBA, K%, BB%, WHIP,
      contact suppression when available)
    - Availability: recent workload/fatigue from the MLB boxscore-based fatigue table

    Final score keeps quality as the main driver while still penalizing tired or
    stressed bullpens.
    """
    raw_quality = _bullpen_pitch_score(team, bullpen_stats)
    data_status = "Bullpen quality metrics found."
    if raw_quality is None:
        raw_quality = 0.0
        data_status = "No dedicated bullpen quality metrics found; using neutral quality."

    quality_score = _cap(float(raw_quality or 0.0), -10, 10)
    fatigue_adj = _bullpen_fatigue_adjustment(team, bullpen_fatigue_df)
    availability_penalty = _cap(float(fatigue_adj or 0.0), -10, 0)

    # 70% bullpen skill/quality, 30% current availability/fatigue.
    quality_component = quality_score * 0.70
    availability_component = availability_penalty * 0.30
    score = _cap(quality_component + availability_component, -8, 8)

    # Convert final bullpen score into a late-inning run-prevention rate.
    run_rate = _cap(4.25 - (score * 0.145), 2.95, 5.95)

    if score >= 4:
        status = "Strong bullpen profile: quality is carrying the grade even after fatigue is considered."
    elif score >= 1.5:
        status = "Positive bullpen profile: above-average quality/availability mix."
    elif score <= -4:
        status = "Weak bullpen profile: poor quality and/or heavy fatigue creates late-game risk."
    elif score <= -1.5:
        status = "Negative bullpen profile: bullpen quality/availability is below average."
    else:
        status = "Neutral bullpen profile."

    return score, {
        "score": round(score, 2),
        "quality_score": round(quality_score, 2),
        "quality_component_70pct": round(quality_component, 2),
        "availability_penalty": round(availability_penalty, 2),
        "availability_component_30pct": round(availability_component, 2),
        "fatigue_adjustment": round(float(fatigue_adj or 0.0), 2),
        "bullpen_run_rate": round(run_rate, 2),
        "status": status,
        "data_status": data_status,
        "blend": "70% bullpen quality / 30% availability-fatigue",
    }


def _moneyline_confluence(team_components):
    positives = 0
    negatives = 0
    for value in team_components:
        try:
            v = float(value)
        except Exception:
            continue
        if v >= 1.5:
            positives += 1
        elif v <= -1.5:
            negatives += 1
    return positives, negatives


def _legacy__project_team_runs_v1(team, base_run_details, offense_score, lineup_score, offensive_contact_score, opposing_pitcher_profile, opposing_bullpen_details, home_field=False):
    """Project runs for one offense from today's pitching allocation.

    New shared run engine: starter expected RA over projected IP + bullpen RA over
    remaining innings is the baseline. Team offense/contact/lineup are modifiers,
    not the starting point, so moneylines and totals are driven by the pitchers
    expected to throw the innings today.
    """
    starter_ip = float(opposing_pitcher_profile.get("projected_ip", 0.0) or 0.0)
    starter_run_rate = float(opposing_pitcher_profile.get("starter_run_rate", 4.35) or 4.35)
    bullpen_ip = max(0.0, 9.0 - starter_ip)
    if starter_ip <= 0.1:
        bullpen_ip = 9.0
    bullpen_run_rate = float(opposing_bullpen_details.get("bullpen_run_rate", 4.25) or 4.25)

    starter_runs_component = starter_run_rate * (starter_ip / 9.0) if starter_ip > 0 else 0.0
    bullpen_runs_component = bullpen_run_rate * (bullpen_ip / 9.0)
    pitching_baseline_runs = starter_runs_component + bullpen_runs_component

    # Keep team quality as a modifier only. These caps are intentionally moderate
    # because the baseline is now today's opposing starter/bullpen, not team R/G.
    offense_adjustment = _cap(float(offense_score or 0) * 0.040, -0.30, 0.30)
    lineup_adjustment = _cap(float(lineup_score or 0) * 0.045, -0.38, 0.38)
    offensive_contact_adjustment = _cap(float(offensive_contact_score or 0) * 0.055, -0.33, 0.33)
    home_adjustment = 0.13 if home_field else 0.0

    projected_runs = pitching_baseline_runs + offense_adjustment + lineup_adjustment + offensive_contact_adjustment + home_adjustment
    projected_runs = _cap(projected_runs, 1.85, 7.55)

    if not isinstance(base_run_details, dict):
        base_run_details = {"status": "team baseline not used in pitcher-allocation engine"}

    return projected_runs, {
        "projected_runs": round(projected_runs, 2),
        "base_runs": round(pitching_baseline_runs, 2),
        "starter_bullpen_baseline_runs": round(pitching_baseline_runs, 2),
        "starter_runs_component": round(starter_runs_component, 2),
        "bullpen_runs_component": round(bullpen_runs_component, 2),
        "team_baseline": base_run_details,
        "offense_adjustment": round(offense_adjustment, 2),
        "lineup_adjustment": round(lineup_adjustment, 2),
        "offensive_contact_adjustment": round(offensive_contact_adjustment, 2),
        "home_field_adjustment": round(home_adjustment, 2),
        "opposing_starter_ip": round(starter_ip, 2),
        "opposing_starter_run_rate": round(starter_run_rate, 2),
        "opposing_starter_runs_component": round(starter_runs_component, 2),
        "opposing_bullpen_ip": round(bullpen_ip, 2),
        "opposing_bullpen_run_rate": round(bullpen_run_rate, 2),
        "opposing_bullpen_runs_component": round(bullpen_runs_component, 2),
        "engine": "pitcher_allocation_runs_allowed_v1",
        "status": "Baseline = opposing starter expected RA over leash IP + bullpen RA over remaining IP.",
    }


def _legacy_moneyline_probability_v1(home, away, hp, ap, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, bullpen_stats=None, use_home_bullpen=False, use_away_bullpen=False, bullpen_fatigue_df=None, home_lineup_details=None, away_lineup_details=None, home_arsenal_details=None, away_arsenal_details=None, game_environment=None, return_details=False, home_bulk_context=None, away_bulk_context=None):
    A_throws = get_value(pitcher_this_year, "Player", ap, "Throws", None)
    if A_throws is None:
        A_throws = get_value(pitcher_last_year, "Player", ap, "Throws", "R")
    H_throws = get_value(pitcher_this_year, "Player", hp, "Throws", None)
    if H_throws is None:
        H_throws = get_value(pitcher_last_year, "Player", hp, "Throws", "R")

    H_offense, H_offense_details = _team_offense_component(home, A_throws, team_hitting, team_batting_rhp, team_batting_lhp)
    A_offense, A_offense_details = _team_offense_component(away, H_throws, team_hitting, team_batting_rhp, team_batting_lhp)

    H_base_runs, H_base_details = _moneyline_team_base_runs(home, team_hitting)
    A_base_runs, A_base_details = _moneyline_team_base_runs(away, team_hitting)

    H_lineup, H_lineup_details = _lineup_moneyline_component(home_lineup_details, H_offense)
    A_lineup, A_lineup_details = _lineup_moneyline_component(away_lineup_details, A_offense)

    H_split_df = team_batting_lhp if str(A_throws).upper().startswith("L") else team_batting_rhp
    A_split_df = team_batting_lhp if str(H_throws).upper().startswith("L") else team_batting_rhp
    H_contact, H_contact_details = _team_offensive_contact_quality(home, team_hitting, H_split_df)
    A_contact, A_contact_details = _team_offensive_contact_quality(away, team_hitting, A_split_df)

    H_bullpen, H_bullpen_details = _bullpen_quality_component(home, bullpen_stats, bullpen_fatigue_df)
    A_bullpen, A_bullpen_details = _bullpen_quality_component(away, bullpen_stats, bullpen_fatigue_df)

    # Bullpen/opener games should use the full bullpen score, not quality-only.
    # That means both raw bullpen skill and current availability/fatigue matter.
    H_has_bulk = bool((home_bulk_context or {}).get("bulk_pitcher"))
    A_has_bulk = bool((away_bulk_context or {}).get("bulk_pitcher"))
    H_bullpen_override = H_bullpen if use_home_bullpen and not H_has_bulk else None
    A_bullpen_override = A_bullpen if use_away_bullpen and not A_has_bulk else None

    # Profiles are for the pitchers/defenses. The home pitcher profile affects AWAY projected runs,
    # and the away pitcher profile affects HOME projected runs.
    H_pitcher_profile = _moneyline_pitcher_profile(hp, pitcher_this_year, pitcher_last_year, home_arsenal_details, H_bullpen_override, is_home_start=True)
    A_pitcher_profile = _moneyline_pitcher_profile(ap, pitcher_this_year, pitcher_last_year, away_arsenal_details, A_bullpen_override, is_home_start=False)
    if use_home_bullpen and H_has_bulk:
        H_pitcher_profile = _bulk_game_pitching_profile(hp, home_bulk_context, pitcher_this_year, pitcher_last_year, home_arsenal_details, H_bullpen, is_home_start=True)
    if use_away_bullpen and A_has_bulk:
        A_pitcher_profile = _bulk_game_pitching_profile(ap, away_bulk_context, pitcher_this_year, pitcher_last_year, away_arsenal_details, A_bullpen, is_home_start=False)


    home_runs, home_run_details = _project_team_runs(home, H_base_details, H_offense, H_lineup, H_contact, A_pitcher_profile, A_bullpen_details, home_field=True)
    away_runs, away_run_details = _project_team_runs(away, A_base_details, A_offense, A_lineup, A_contact, H_pitcher_profile, H_bullpen_details, home_field=False)

    game_environment = game_environment if isinstance(game_environment, dict) else build_game_environment(home)
    env_total_adjustment = float(game_environment.get("total_run_adjustment", 0.0) or 0.0)
    env_team_adjustment = env_total_adjustment / 2.0
    raw_home_runs_before_environment = home_runs
    raw_away_runs_before_environment = away_runs
    home_runs = _total_clip(home_runs + env_team_adjustment, 1.65, 7.85)
    away_runs = _total_clip(away_runs + env_team_adjustment, 1.65, 7.85)
    home_run_details["park_weather_adjustment"] = round(env_team_adjustment, 2)
    away_run_details["park_weather_adjustment"] = round(env_team_adjustment, 2)
    home_run_details["projected_runs_before_environment"] = round(raw_home_runs_before_environment, 2)
    away_run_details["projected_runs_before_environment"] = round(raw_away_runs_before_environment, 2)
    home_run_details["projected_runs"] = round(home_runs, 2)
    away_run_details["projected_runs"] = round(away_runs, 2)

    run_diff = home_runs - away_runs

    # Convert expected run edge to win probability. This keeps normal MLB games spread out
    # enough to identify -120 to -160 favorites without creating wild 75% projections.
    home_win_prob = 1 / (1 + math.exp(-run_diff / 1.55))
    home_win_prob = _cap(home_win_prob, 0.27, 0.73)
    away_win_prob = 1 - home_win_prob

    H_components = [H_offense, H_lineup, H_contact, H_bullpen, H_pitcher_profile.get("matchup_score", 0)]
    A_components = [A_offense, A_lineup, A_contact, A_bullpen, A_pitcher_profile.get("matchup_score", 0)]
    H_pos, H_neg = _moneyline_confluence(H_components)
    A_pos, A_neg = _moneyline_confluence(A_components)

    # Keep a sanity guard against fake value: if the model likes a team but its own starter
    # profile is very negative, trim the win probability back toward 50 rather than grading a
    # pure market/lineup play as a confident ML.
    if run_diff > 0 and float(H_pitcher_profile.get("matchup_score", 0) or 0) < -3.0:
        home_win_prob = 0.50 + ((home_win_prob - 0.50) * 0.65)
        away_win_prob = 1 - home_win_prob
    elif run_diff < 0 and float(A_pitcher_profile.get("matchup_score", 0) or 0) < -3.0:
        away_win_prob = 0.50 + ((away_win_prob - 0.50) * 0.65)
        home_win_prob = 1 - away_win_prob

    details = {
        "score": round(run_diff, 2),
        "raw_score": round(run_diff, 2),
        "home_projected_runs": round(home_runs, 2),
        "away_projected_runs": round(away_runs, 2),
        "home_projected_runs_before_environment": round(raw_home_runs_before_environment, 2),
        "away_projected_runs_before_environment": round(raw_away_runs_before_environment, 2),
        "park_weather_total_adjustment": round(env_total_adjustment, 2),
        "game_environment": game_environment,
        "run_differential": round(run_diff, 2),
        "home": {
            "team": home,
            "total_component_score": round(H_offense + H_lineup + H_contact + H_bullpen + float(H_pitcher_profile.get("matchup_score", 0) or 0), 2),
            "projected_runs_scored": round(home_runs, 2),
            "projected_runs_allowed": round(away_runs, 2),
            "run_edge": round(run_diff, 2),
            "confluence_positive_buckets": H_pos,
            "confluence_negative_buckets": H_neg,
            "starter": H_pitcher_profile,
            "lineup": H_lineup_details,
            "offensive_contact": H_contact_details,
            "offense": H_offense_details,
            "baseline": H_base_details,
            "bullpen": H_bullpen_details,
            "run_projection": home_run_details,
        },
        "away": {
            "team": away,
            "total_component_score": round(A_offense + A_lineup + A_contact + A_bullpen + float(A_pitcher_profile.get("matchup_score", 0) or 0), 2),
            "projected_runs_scored": round(away_runs, 2),
            "projected_runs_allowed": round(home_runs, 2),
            "run_edge": round(-run_diff, 2),
            "confluence_positive_buckets": A_pos,
            "confluence_negative_buckets": A_neg,
            "starter": A_pitcher_profile,
            "lineup": A_lineup_details,
            "offensive_contact": A_contact_details,
            "offense": A_offense_details,
            "baseline": A_base_details,
            "bullpen": A_bullpen_details,
            "run_projection": away_run_details,
        },
        "status": "Moneyline v12 run model: shared pitcher-allocation run engine plus uniform park/weather environment. Park/weather moves projected runs and total/NRFI context; A/B moneyline selection is handled by the confidence and confluence layer.",
    }

    if return_details:
        return home_win_prob, away_win_prob, details
    return home_win_prob, away_win_prob


def _score_advantage_to_0_100(advantage, points_per_unit=2.5):
    """Convert a component advantage into a confidence score where 50 is neutral."""
    try:
        advantage = float(advantage or 0)
    except Exception:
        advantage = 0.0
    return round(_cap(50 + (advantage * float(points_per_unit)), 0, 100), 1)



def _legacy_moneyline_confidence_score_v1(team_key, edge, moneyline_details, odds=None):
    """Score the quality of a moneyline edge using edge + starter/bullpen/lineup confluence.

    Projection/win probability stays untouched. This is only a selection layer:
    - 40% edge value
    - 25% starter-vs-starter advantage
    - 20% bullpen-vs-bullpen advantage
    - 15% actual confirmed-lineup advantage

    Uniform v12 selection rules:
    - A risk failures become Non-Edge, never B.
    - Monster edges above 16% need 4/4 confluence and no risk flags.
    - Historically weak odds ranges require 4/4 confluence.
    """
    details = moneyline_details if isinstance(moneyline_details, dict) else {}
    team_key = str(team_key or "").lower().strip()
    opp_key = "away" if team_key == "home" else "home"
    team = details.get(team_key, {}) if isinstance(details.get(team_key, {}), dict) else {}
    opp = details.get(opp_key, {}) if isinstance(details.get(opp_key, {}), dict) else {}

    try:
        edge = float(edge or 0)
    except Exception:
        edge = 0.0
    edge_pct = edge * 100.0
    edge_score = round(_cap(50 + (edge_pct * 4.0), 0, 100), 1) if edge > 0 else 0.0

    starter_score = float((team.get("starter", {}) or {}).get("matchup_score", 0) or 0)
    opp_starter_score = float((opp.get("starter", {}) or {}).get("matchup_score", 0) or 0)
    starter_adv = starter_score - opp_starter_score
    starter_conf = _score_advantage_to_0_100(starter_adv, 2.5)

    bullpen_score = float((team.get("bullpen", {}) or {}).get("score", 0) or 0)
    opp_bullpen_score = float((opp.get("bullpen", {}) or {}).get("score", 0) or 0)
    bullpen_adv = bullpen_score - opp_bullpen_score
    bullpen_conf = _score_advantage_to_0_100(bullpen_adv, 3.0)

    lineup_score = float((team.get("lineup", {}) or {}).get("score", 0) or 0)
    opp_lineup_score = float((opp.get("lineup", {}) or {}).get("score", 0) or 0)
    lineup_adv = lineup_score - opp_lineup_score
    lineup_conf = _score_advantage_to_0_100(lineup_adv, 3.0)

    confidence = (edge_score * 0.40) + (starter_conf * 0.25) + (bullpen_conf * 0.20) + (lineup_conf * 0.15)
    confidence = round(_cap(confidence, 0, 100), 1)

    checks = {
        "edge": edge > 0,
        "starter": starter_adv > 0,
        "bullpen": bullpen_adv > 0,
        "lineup": lineup_adv > 0,
    }
    confluence = sum(1 for v in checks.values() if v)

    red_flags = []
    if edge >= 0.05 and starter_adv <= -3:
        red_flags.append(f"Starter disadvantage {starter_adv:+.1f}")
    if edge >= 0.05 and bullpen_adv <= -3:
        red_flags.append(f"Bullpen disadvantage {bullpen_adv:+.1f}")
    if edge >= 0.05 and lineup_adv <= -3:
        red_flags.append(f"Lineup disadvantage {lineup_adv:+.1f}")

    weak_odds_note = ""
    try:
        odds_value = int(float(str(odds).replace("+", "").strip()))
    except Exception:
        odds_value = None
    if odds_value is not None:
        weak_range = (-119 <= odds_value <= -101) or (120 <= odds_value <= 149)
        if weak_range and edge >= 0.05 and confluence < 4:
            weak_odds_note = f"Odds range risk: {odds_value:+d} is in a weaker tracker bucket and needs 4/4 confluence."
            red_flags.append(weak_odds_note)

    monster_edge_note = ""
    if edge > 0.16 and confluence < 4:
        monster_edge_note = f"Monster edge guardrail: {edge_pct:+.1f}% edge needs 4/4 confluence; failed A setups become Non-Edge, not B."
        red_flags.append(monster_edge_note)

    grade = moneyline_grade(edge, confidence, confluence, red_flags)

    confluence_note = ""
    if grade in ["A Moneyline", "B Moneyline"] and confluence < 3:
        confluence_note = f"Confluence filter: {confluence}/4 indicators agree, so this is downgraded to Non-Edge Moneyline."
        grade = "Non-Edge Moneyline"

    premium_note = ""
    if grade in ["A Moneyline", "B Moneyline"] and confluence == 4:
        premium_note = "Premium setup: 4/4 indicators agree."

    reason_lines = [
        f"{'✓' if checks['edge'] else '✗'} Edge: {edge_pct:+.1f}% (score {edge_score:.1f})",
        f"{'✓' if checks['starter'] else '✗'} Starter: {starter_adv:+.1f} ({starter_score:.1f} vs {opp_starter_score:.1f})",
        f"{'✓' if checks['bullpen'] else '✗'} Bullpen: {bullpen_adv:+.1f} ({bullpen_score:.1f} vs {opp_bullpen_score:.1f})",
        f"{'✓' if checks['lineup'] else '✗'} Lineup: {lineup_adv:+.1f} ({lineup_score:.1f} vs {opp_lineup_score:.1f})",
        f"Confluence: {confluence}/4",
    ]
    if confluence_note:
        reason_lines.append(confluence_note)
    if premium_note:
        reason_lines.append(premium_note)
    if red_flags:
        reason_lines.append("Risk flags: " + "; ".join(red_flags))
    if grade == "Non-Edge Moneyline" and edge >= 0.08:
        reason_lines.append("A-grade risk rule: failed A setups are removed as Non-Edge instead of being downgraded into the B Moneyline bucket.")

    return {
        "confidence_score": confidence,
        "grade": grade,
        "confluence": confluence,
        "checks": checks,
        "edge_score": edge_score,
        "starter_advantage": round(starter_adv, 2),
        "starter_score": starter_conf,
        "bullpen_advantage": round(bullpen_adv, 2),
        "bullpen_score": bullpen_conf,
        "lineup_advantage": round(lineup_adv, 2),
        "lineup_score": lineup_conf,
        "red_flags": red_flags,
        "reason_lines": reason_lines,
        "status": "Moneyline v12: 40% edge, 25% starter, 20% bullpen, 15% lineup. A/B require 3/4 confluence; monster edges and weaker odds buckets need 4/4. Failed A risks become Non-Edge, never B.",
    }


def apply_moneyline_k_confluence(team_key, confidence_result, selected_k_grade, selected_k_edge, opposing_k_grade, opposing_k_edge):
    """Use pitcher-K market agreement as a final ML trust layer.

    Positive: our starter has K-over pressure and the opposing starter has K-under pressure.
    Negative: our starter has K-under pressure and/or the opposing starter has K-over pressure.
    This protects B Moneylines by sending conflicted A/B setups to Non-Edge instead
    of downgrading risky A plays into B.
    """
    result = dict(confidence_result or {})
    reason_lines = list(result.get("reason_lines", []))
    red_flags = list(result.get("red_flags", []))
    grade = str(result.get("grade", "Non-Edge Moneyline"))

    def _dir(g):
        text = str(g or "").upper()
        if "OVER" in text:
            return "OVER"
        if "UNDER" in text:
            return "UNDER"
        return "PASS"

    own_dir = _dir(selected_k_grade)
    opp_dir = _dir(opposing_k_grade)
    try:
        own_edge = float(selected_k_edge or 0)
    except Exception:
        own_edge = 0.0
    try:
        opp_edge = float(opposing_k_edge or 0)
    except Exception:
        opp_edge = 0.0

    score = 0.0
    notes = []
    if own_dir == "OVER":
        score += 0.5
        notes.append(f"selected starter K pressure {selected_k_grade} ({own_edge:+.2f})")
    elif own_dir == "UNDER":
        score -= 1.0
        notes.append(f"selected starter K conflict {selected_k_grade} ({own_edge:+.2f})")

    if opp_dir == "UNDER":
        score += 0.5
        notes.append(f"opposing starter K weakness {opposing_k_grade} ({opp_edge:+.2f})")
    elif opp_dir == "OVER":
        score -= 0.75
        notes.append(f"opposing starter K pressure {opposing_k_grade} ({opp_edge:+.2f})")

    if score >= 1.0:
        old_conf = float(result.get("confidence_score", 0) or 0)
        result["confidence_score"] = round(_cap(old_conf + 4.0, 0, 100), 1)
        reason_lines.append("Cross-market K confluence: " + "; ".join(notes) + " → confidence +4.")
    elif score <= -1.0:
        flag = "Cross-market K conflict: " + "; ".join(notes)
        red_flags.append(flag)
        reason_lines.append(flag)
        if grade in ["A Moneyline", "B Moneyline"]:
            grade = "Non-Edge Moneyline"
            reason_lines.append("Cross-market rule: conflicted A/B moneyline becomes Non-Edge to keep B Moneylines clean.")

    result["grade"] = grade
    result["red_flags"] = red_flags
    result["reason_lines"] = reason_lines
    result["k_confluence_score"] = round(score, 2)
    result["k_confluence_status"] = "Positive supports ML; negative conflicts remove A/B plays instead of downgrading to B."
    return result

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
    if "ELITE YRFI" in upper:
        return "ELITE YRFI"
    if "YRFI" in upper:
        return "YRFI"

    # Game total grades. Check before generic pitcher OVER/UNDER labels.
    if "TOTAL OVER" in upper or "GAME TOTAL OVER" in upper:
        return "TOTAL OVER"
    if "TOTAL UNDER" in upper or "GAME TOTAL UNDER" in upper:
        return "TOTAL UNDER"

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
    cols = ["Game Label", "Away Team", "Home Team", "Better ML", "NRFI Grade", "NRFI Score", "YRFI Score", "Away Pitcher K + Grade", "Home Pitcher K + Grade"]
    return df[[col for col in cols if col in df.columns]].copy()


def k_summary_text(pitcher, projection, grade, line, odds=None):
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

    odds_text = "" if odds in [None, ""] else f" / {odds}"
    return f"{pitcher} {projection:.2f} ({side}) Line {line}{odds_text}"


def parse_k_summary_for_best_play_filters(text):
    """Extract side, projection, line, and odds from a saved pitcher K summary."""
    import re
    text = str(text).strip()
    upper = text.upper()

    side = ""
    if "UNDER" in upper:
        side = "UNDER"
    elif "OVER" in upper:
        side = "OVER"

    projection = None
    line = None
    odds = None

    # First number after the pitcher name is the model projection.
    projection_match = re.search(r"\s(\d+(?:\.\d+)?)\s*\(", text)
    if projection_match:
        try:
            projection = float(projection_match.group(1))
        except Exception:
            projection = None

    # Current format: "Line 5.5 / -110". Older rows may only end with "5.5".
    line_match = re.search(r"LINE\s+(\d+(?:\.\d+)?)", upper)
    if line_match:
        try:
            line = float(line_match.group(1))
        except Exception:
            line = None
    else:
        nums = re.findall(r"\d+(?:\.\d+)?", text)
        if len(nums) >= 2:
            try:
                line = float(nums[-1])
            except Exception:
                line = None

    odds_match = re.search(r"/\s*([+-]?\d{3,4})", text)
    if odds_match:
        try:
            odds = int(odds_match.group(1))
        except Exception:
            odds = None

    return {"side": side, "projection": projection, "line": line, "odds": odds}


def pitcher_k_best_play_eligible(text):
    """Best Plays-only filters for pitcher strikeout props.

    Full Slate remains unchanged. Handpicked can still override because this only
    controls the automatic Best Plays list.
    """
    info = parse_k_summary_for_best_play_filters(text)

    # Only tighten unders. Overs keep the existing model eligibility.
    if info.get("side") != "UNDER":
        return True

    odds = info.get("odds")
    projection = info.get("projection")
    line = info.get("line")

    # Exclude plus-money Under / Lean Under / Strong Under plays from automatic Best Plays.
    if odds is not None and odds > 0:
        return False

    # Require at least a 0.5 K cushion below the sportsbook line.
    # Example: line 5.5 needs projection <= 5.0.
    if projection is not None and line is not None and projection > (line - 0.5):
        return False

    return True


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
        elif "ELITE YRFI" in text:
            return 100
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
        game = row.get("Game Label", "") or f'{row["Away Team"]} at {row["Home Team"]}'

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
        if row["NRFI Grade"] in ["ELITE NRFI", "ELITE YRFI", "YRFI"]:
            is_yrfi_row = "YRFI" in str(row.get("NRFI Grade", "")).strip().upper()
            direct_first_inning_score = (
                row.get("YRFI Score", "") if is_yrfi_row else row.get("NRFI Score", "")
            )
            direct_first_inning_odds = (
                row.get("YRFI Odds", "") if is_yrfi_row else row.get("NRFI Odds", "")
            )
            saved_first_inning_score = safe_float(
                direct_first_inning_score,
                get_static_score(row["NRFI Grade"]),
            )
            best_rows.append({
                "Play Type": row["NRFI Grade"],
                "Game": game,
                "Play": row["NRFI Grade"],
                "Odds/Line": direct_first_inning_odds,
                "Score": round(saved_first_inning_score, 1),
            })

        # Game total plays
        total_grade = str(row.get("Total Runs Grade", "")).strip()
        if total_grade in ["TOTAL OVER", "TOTAL UNDER"]:
            best_rows.append({
                "Play Type": total_grade,
                "Game": game,
                "Play": f'{game} {total_grade.replace("TOTAL ", "")} {row.get("Total Runs Line", "")}',
                "Odds/Line": row.get("Total Runs Line", ""),
                "Score": 68
            })

        # Pitcher K plays: use true 0-100 K score saved from the model
        pitcher_k_columns = [
            ("Away Pitcher K + Grade", "Away Pitcher K Score", "Away Pitcher K Reliability"),
            ("Home Pitcher K + Grade", "Home Pitcher K Score", "Home Pitcher K Reliability"),
            ("Away Bulk Pitcher K + Grade", "Away Bulk Pitcher K Score", "Away Bulk Pitcher K Reliability"),
            ("Home Bulk Pitcher K + Grade", "Home Bulk Pitcher K Score", "Home Bulk Pitcher K Reliability"),
        ]

        for play_col, score_col, reliability_col in pitcher_k_columns:
            text = str(row.get(play_col, ""))
            upper_text = text.upper()

            if "PASS" not in upper_text and any(g in upper_text for g in [
                "STRONG OVER", "OVER", "LEAN OVER",
                "LEAN UNDER", "UNDER", "STRONG UNDER"
            ]):
                k_score = safe_float(row.get(score_col, 0), 0)
                reliability_value = safe_float(row.get(reliability_col, 0), 0)
                if reliability_value and reliability_value < 60:
                    continue

                # Backward compatibility for old saved rows without a K Score column/value
                if k_score == 0:
                    if "STRONG OVER" in upper_text or "STRONG UNDER" in upper_text:
                        k_score = 90
                    elif "LEAN" in upper_text:
                        k_score = 50
                    elif "OVER" in upper_text or "UNDER" in upper_text:
                        k_score = 70

                if not pitcher_k_best_play_eligible(text):
                    continue

                best_rows.append({
                    "Play Type": "Pitcher K",
                    "Game": game,
                    "Play": text,
                    "Odds/Line": extract_k_line(text),
                    "Score": k_score
                })

    if not best_rows:
        return pd.DataFrame(columns=["Play Type", "Game", "Play", "Odds/Line", "Score"])

    # Every first-inning play is independently qualified. Elite YRFI is not
    # capped by slate order, so late games are judged by the same threshold as early games.
    return pd.DataFrame(best_rows).sort_values(by="Score", ascending=False).reset_index(drop=True)


def sync_daily_yrfi_tracker_limit(slate_date=None, max_plays=None):
    """Deprecated compatibility wrapper. Elite YRFI has no daily ranking cap.

    New Elite YRFI rows are saved directly when each matchup is saved. This
    function remains only so an older button/callback cannot break after the
    upgrade; it intentionally does not delete, rank, or replace any plays.
    """
    day = str(slate_date or date.today())
    slate = load_slate()
    if slate is None or slate.empty:
        return {"kept": 0, "changed": False, "mode": "elite_only_no_cap"}
    grades = slate["NRFI Grade"].astype(str).str.upper()
    kept = int(((slate["Date"].astype(str) == day) & (grades == "ELITE YRFI")).sum())
    return {"kept": kept, "changed": False, "mode": "elite_only_no_cap"}


def render_slate_game_card(row, bet_type_record_styles=None, show_delete=False, row_id=None):
    bet_type_record_styles = bet_type_record_styles or {}
    game_label = row.get("Game Label", "") or f'{row.get("Away Team", "")} at {row.get("Home Team", "")}'
    plays = [
        ("MONEYLINE", row.get("Better ML", "")),
        ("NRFI/YRFI", row.get("NRFI Grade", "")),
        ("AWAY K", row.get("Away Pitcher K + Grade", "")),
        ("HOME K", row.get("Home Pitcher K + Grade", "")),
    ]
    chips = []
    for label, value in plays:
        value_text = str(value).strip()
        if not value_text or "PASS" in value_text.upper() or "NON-EDGE" in value_text.upper():
            continue
        key = normalize_bet_type_text(value_text)
        chip_class = "ez-chip-green" if key in bet_type_record_styles and "#d1fae5" in bet_type_record_styles[key] else "ez-chip-yellow"
        chips.append(f'<span class="ez-chip {chip_class}">{esc(label)}: {esc(value_text)}</span>')

    if not chips:
        chips.append('<span class="ez-chip ez-chip-yellow">NO QUALIFYING PLAYS</span>')

    st.markdown(f"""
    <div class="ez-card">
        <div class="ez-title">{esc(game_label)}</div>
        <div class="ez-sub">{esc(row.get("Away Team", ""))} at {esc(row.get("Home Team", ""))}</div>
        {''.join(chips)}
    </div>
    """, unsafe_allow_html=True)


def render_daily_slate():
    st.header("Daily Slate")

    slate_df = load_slate()
    tracker_df = load_tracker()
    bet_type_record_styles = build_bet_type_record_styles(tracker_df)
    today = str(date.today())

    view = st.radio(
        "Slate view",
        ["Today", "Best Plays", "Handpick Any", "By Date", "Delete"],
        horizontal=True,
        label_visibility="collapsed",
        key="slate_view_mode"
    )

    if slate_df.empty:
        st.info("No saved games yet.")
        return

    today_slate = slate_df[slate_df["Date"] == today].copy()

    if view == "Today":
        st.subheader("Today's Saved Games")
        if today_slate.empty:
            st.info("No games saved today yet.")
        else:
            for _, row in today_slate.iterrows():
                render_slate_game_card(row, bet_type_record_styles)

    elif view == "Best Plays":
        st.subheader("Best Plays")
        if today_slate.empty:
            st.info("No saved games today yet.")
        else:
            best_df = build_best_plays(today_slate)
            if best_df.empty:
                st.info("No best plays saved yet.")
            else:
                for play_idx, play in best_df.iterrows():
                    key = normalize_bet_type_text(play.get("Play Type", "")) or normalize_bet_type_text(play.get("Play", ""))
                    klass = "ez-card-green" if key in bet_type_record_styles and "#d1fae5" in bet_type_record_styles[key] else ""
                    st.markdown(f"""
                    <div class="ez-card {klass}">
                        <div class="ez-title">{esc(str(play.get("Play Type", "")).upper())}</div>
                        <div class="ez-sub">{esc(play.get("Game", ""))}</div>
                        <div class="ez-kv"><span>Play</span><span>{esc(play.get("Play", ""))}</span></div>
                        <div class="ez-kv"><span>Odds / Line</span><span>{esc(play.get("Odds/Line", ""))}</span></div>
                        <div class="ez-kv"><span>Score</span><span>{esc(play.get("Score", ""))}</span></div>
                    </div>
                    """, unsafe_allow_html=True)

                    with st.expander("⭐ Add to EZPZ Handpicked", expanded=False):
                        st.caption("Tap the button to mark this play as handpicked. No tier, tag, or notes are needed.")
                        if st.button("⭐ Add to Handpicked", key=f"add_handpicked_{play_idx}"):
                            ok, message = mark_best_play_as_handpicked(play)
                            if ok:
                                st.success(message)
                                st.rerun()
                            else:
                                st.error(message)
                    st.divider()

    elif view == "Handpick Any":
        st.subheader("Handpick Any Saved Bet")
        st.caption("Use this for plays you personally like, especially Lean Unders, even if they did not qualify for Best Plays. Save the bet first, then tap Add to Handpicked here.")

        today_tracker = tracker_df[tracker_df["Date"].astype(str) == today].copy() if tracker_df is not None and not tracker_df.empty else pd.DataFrame()
        if today_tracker.empty:
            st.info("No saved bets found for today. Run/save the matchup bets first, then come back here to handpick them.")
        else:
            if "Favorite Pick" not in today_tracker.columns:
                today_tracker["Favorite Pick"] = ""
            fav_col = today_tracker["Favorite Pick"].astype(str).str.upper()
            handpicked_now = today_tracker[fav_col == "TRUE"].copy()
            available = today_tracker[fav_col != "TRUE"].copy()

            if not handpicked_now.empty:
                st.markdown("**Already handpicked today**")
                for idx, bet in handpicked_now.iterrows():
                    st.markdown(f'''
                    <div class="ez-card ez-card-green">
                        <div class="ez-title">{esc(str(bet.get("Bet Type", "")).upper())}</div>
                        <div class="ez-sub">{esc(bet.get("Selection", ""))}</div>
                        <span class="ez-chip ez-chip-green">HANDPICKED</span>
                        <div class="ez-kv"><span>Market</span><span>{esc(bet.get("Market", ""))}</span></div>
                        <div class="ez-kv"><span>Odds / Line</span><span>{esc(bet.get("Odds/Line", ""))}</span></div>
                    </div>
                    ''', unsafe_allow_html=True)
                    if st.button("Remove today's badge", key=f"unhandpick_any_{idx}"):
                        ok, message = unmark_tracker_row_as_handpicked(idx)
                        if ok:
                            st.success(message)
                            st.rerun()
                        else:
                            st.error(message)
                st.divider()

            st.markdown("**Available saved bets**")
            if available.empty:
                st.info("Every saved bet today is already marked handpicked.")
            else:
                for idx, bet in available.iterrows():
                    bet_type = str(bet.get("Bet Type", "")).upper()
                    is_lean_under = "LEAN UNDER" in bet_type or "LEAN UNDER" in str(bet.get("Selection", "")).upper()
                    klass = "ez-card-green" if is_lean_under else ""
                    chip = '<span class="ez-chip ez-chip-green">LEAN UNDER</span>' if is_lean_under else ''
                    st.markdown(f'''
                    <div class="ez-card {klass}">
                        <div class="ez-title">{esc(bet_type)}</div>
                        <div class="ez-sub">{esc(bet.get("Selection", ""))}</div>
                        {chip}
                        <div class="ez-kv"><span>Market</span><span>{esc(bet.get("Market", ""))}</span></div>
                        <div class="ez-kv"><span>Odds / Line</span><span>{esc(bet.get("Odds/Line", ""))}</span></div>
                        <div class="ez-kv"><span>Model</span><span>{esc(bet.get("Model %", ""))}</span></div>
                    </div>
                    ''', unsafe_allow_html=True)
                    if st.button("⭐ Add to Handpicked", key=f"handpick_any_{idx}"):
                        ok, message = mark_tracker_row_as_handpicked(idx)
                        if ok:
                            st.success(message)
                            st.rerun()
                        else:
                            st.error(message)
                    st.divider()

    elif view == "By Date":
        st.subheader("View Saved Slate by Date")
        available_dates = sorted(slate_df["Date"].dropna().astype(str).unique(), reverse=True)
        default_index = available_dates.index(today) if today in available_dates else 0
        selected_date = st.selectbox("Choose a slate date", available_dates, index=default_index)
        selected_slate = slate_df[slate_df["Date"].astype(str) == selected_date].copy()
        if selected_slate.empty:
            st.info("No games saved for this date.")
        else:
            for _, row in selected_slate.iterrows():
                render_slate_game_card(row, bet_type_record_styles)
            with st.expander("Compact table"):
                display_compact_dataframe(compact_slate_view(selected_slate.drop(columns=["Date"])), bet_type_record_styles)

    else:
        st.subheader("Delete Saved Games")
        available_dates = sorted(slate_df["Date"].dropna().astype(str).unique(), reverse=True)
        default_index = available_dates.index(today) if today in available_dates else 0
        selected_date = st.selectbox("Choose date to delete from", available_dates, index=default_index, key="delete_slate_date_mobile")
        selected_slate = slate_df[slate_df["Date"].astype(str) == selected_date].copy()
        if selected_slate.empty:
            st.info("No games saved for this date.")
            return
        st.caption("Tap Delete under a card, or use the table editor fallback.")
        for idx, row in selected_slate.iterrows():
            render_slate_game_card(row, bet_type_record_styles)
            if st.button("Delete this game", key=f"delete_slate_card_{idx}"):
                slate_df = slate_df.drop(index=idx)
                save_slate(slate_df)
                st.success("Selected game deleted.")
                st.rerun()
            st.divider()
        with st.expander("Table delete fallback"):
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
                key=f"delete_slate_table_{selected_date}"
            )
            if st.button("Delete Selected Games", key="delete_selected_slate_table_mobile"):
                rows_to_delete = edited_delete_view.loc[edited_delete_view["Delete"] == True, "Row ID"].tolist()
                if rows_to_delete:
                    slate_df = slate_df.drop(index=rows_to_delete)
                    save_slate(slate_df)
                    st.success("Selected games deleted.")
                    st.rerun()
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
def _legacy_load_team_hitting_stats_live_v1():
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
def _legacy_load_team_batting_split_live_v1(split):
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


# TRUE PITCH-TYPE ARSENAL HELPERS
# -----------------------

PITCH_TYPE_ALIASES = {
    "4-SEAM": "4-Seam", "4-SEAM FASTBALL": "4-Seam", "FOUR-SEAM": "4-Seam", "FOUR-SEAM FASTBALL": "4-Seam", "FF": "4-Seam",
    "SINKER": "Sinker", "SI": "Sinker", "2-SEAM": "Sinker", "TWO-SEAM": "Sinker",
    "CUTTER": "Cutter", "FC": "Cutter",
    "SLIDER": "Slider", "SL": "Slider",
    "SWEEPER": "Sweeper", "ST": "Sweeper",
    "CURVEBALL": "Curveball", "CURVE": "Curveball", "CU": "Curveball", "KC": "Curveball", "KNUCKLE CURVE": "Curveball",
    "CHANGEUP": "Changeup", "CHANGE": "Changeup", "CH": "Changeup",
    "SPLITTER": "Splitter", "SPLIT-FINGER": "Splitter", "SPLIT FINGER": "Splitter", "FS": "Splitter",
    "KNUCKLEBALL": "Knuckleball", "KN": "Knuckleball",
}

TEAM_NAME_ALIASES_FOR_SAVANT = {
    "Arizona Diamondbacks": ["Arizona Diamondbacks", "D-backs", "Dbacks", "Diamondbacks", "ARI", "AZ"],
    "Athletics": ["Athletics", "Oakland Athletics", "Sacramento Athletics", "A's", "As", "OAK", "ATH"],
    "Atlanta Braves": ["Atlanta Braves", "Braves", "ATL"],
    "Baltimore Orioles": ["Baltimore Orioles", "Orioles", "BAL"],
    "Boston Red Sox": ["Boston Red Sox", "Red Sox", "BOS"],
    "Chicago Cubs": ["Chicago Cubs", "Cubs", "CHC"],
    "Chicago White Sox": ["Chicago White Sox", "Chi White Sox", "White Sox", "CWS", "CHW"],
    "Cincinnati Reds": ["Cincinnati Reds", "Reds", "CIN"],
    "Cleveland Guardians": ["Cleveland Guardians", "Guardians", "CLE"],
    "Colorado Rockies": ["Colorado Rockies", "Rockies", "COL"],
    "Detroit Tigers": ["Detroit Tigers", "Tigers", "DET"],
    "Houston Astros": ["Houston Astros", "Astros", "HOU"],
    "Kansas City Royals": ["Kansas City Royals", "KC Royals", "Royals", "KC", "KCR"],
    "Los Angeles Angels": ["Los Angeles Angels", "LA Angels", "Angels", "LAA"],
    "Los Angeles Dodgers": ["Los Angeles Dodgers", "LA Dodgers", "Dodgers", "LAD"],
    "Miami Marlins": ["Miami Marlins", "Marlins", "MIA"],
    "Milwaukee Brewers": ["Milwaukee Brewers", "Brewers", "MIL"],
    "Minnesota Twins": ["Minnesota Twins", "Twins", "MIN"],
    "New York Mets": ["New York Mets", "NY Mets", "Mets", "NYM"],
    "New York Yankees": ["New York Yankees", "NY Yankees", "Yankees", "NYY"],
    "Philadelphia Phillies": ["Philadelphia Phillies", "Phillies", "PHI"],
    "Pittsburgh Pirates": ["Pittsburgh Pirates", "Pirates", "PIT"],
    "San Diego Padres": ["San Diego Padres", "Padres", "SD", "SDP"],
    "San Francisco Giants": ["San Francisco Giants", "SF Giants", "Giants", "SF", "SFG"],
    "Seattle Mariners": ["Seattle Mariners", "Mariners", "SEA"],
    "St. Louis Cardinals": ["St. Louis Cardinals", "St Louis Cardinals", "Saint Louis Cardinals", "Cardinals", "STL"],
    "Tampa Bay Rays": ["Tampa Bay Rays", "TB Rays", "Rays", "TB", "TBR"],
    "Texas Rangers": ["Texas Rangers", "Rangers", "TEX"],
    "Toronto Blue Jays": ["Toronto Blue Jays", "Blue Jays", "TOR"],
    "Washington Nationals": ["Washington Nationals", "Nationals", "WSH", "WSN", "WAS"],
}

def _legacy__clean_col_name_v1(col):
    return str(col).replace("\n", " ").replace("  ", " ").strip()

def _legacy__find_col_v1(df, candidates):
    if df is None or df.empty:
        return None
    normalized = {_clean_col_name(c).lower(): c for c in df.columns}
    for cand in candidates:
        key = cand.lower()
        if key in normalized:
            return normalized[key]
    for c in df.columns:
        low = _clean_col_name(c).lower()
        if any(cand.lower() in low for cand in candidates):
            return c
    return None

def _legacy__to_rate_v1(value, default=0.0):
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    text = str(value).replace("%", "").replace("−", "-").strip()
    if text == "" or text.lower() in ["nan", "none", "--"]:
        return default
    try:
        num = float(text)
    except Exception:
        return default
    if abs(num) > 1:
        return num / 100.0
    return num



def _usage_to_rate(value, default=0.0):
    """Convert Savant pitch-usage percentage points into a decimal rate.

    IMPORTANT:
    Savant pitch usage is stored/displayed as percentage points:
      37.1 means 37.1% -> 0.371
      0.8 means 0.8%  -> 0.008

    Do not use the generic _to_rate() behavior here, because it treats decimals
    under 1 as already-rate values and would misread 0.8% as 80%.
    """
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    text = str(value).replace("%", "").replace("−", "-").strip()
    if text == "" or text.lower() in ["nan", "none", "--"]:
        return default
    try:
        num = float(text)
    except Exception:
        return default

    # Always treat pitch usage as percentage points.
    return num / 100.0

def _normalized_usage_rate(value, default=0.0):
    """Return a decimal usage rate from rows that may already be standardized.

    Raw Savant usage is converted once by _usage_to_rate().
    After that, values like 0.371 already mean 37.1%, so do not divide again.
    """
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    text = str(value).replace("%", "").replace("−", "-").strip()
    if text == "" or text.lower() in ["nan", "none", "--"]:
        return default
    try:
        num = float(text)
    except Exception:
        return default

    if abs(num) <= 1:
        return num
    return num / 100.0

def _legacy__to_number_v1(value, default=0.0):
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    text = str(value).replace("%", "").replace("−", "-").strip()
    if text == "" or text.lower() in ["nan", "none", "--"]:
        return default
    try:
        return float(text)
    except Exception:
        return default

def _legacy__canonical_pitch_type_v1(value):
    text = str(value).strip().upper()
    if not text or text in ["NAN", "NONE", "--"]:
        return ""
    return PITCH_TYPE_ALIASES.get(text, str(value).strip())

def _legacy__name_keys_v1(name):
    first_last = to_first_last(name)
    last_first = to_last_first(name)
    raw = str(name).strip()
    return {normalize_name_for_match(x) for x in [raw, first_last, last_first] if str(x).strip()}

def _legacy__team_keys_v1(team):
    """Return normalized aliases for a team across MLB API, Savant, odds, and UI labels.

    This intentionally does not rely on an exact dictionary key match. If the
    incoming value is already an alias like CHW, CWS, White Sox, STL, etc., it
    expands that value to the full alias set for the correct MLB team.
    """
    raw = str(team).strip()
    raw_key = normalize_name_for_match(raw)
    keys = {raw_key} if raw_key else set()
    for canonical, aliases in TEAM_NAME_ALIASES_FOR_SAVANT.items():
        alias_keys = {normalize_name_for_match(x) for x in ([canonical] + list(aliases)) if str(x).strip()}
        if raw_key in alias_keys:
            keys.update(alias_keys)
            break
    return keys

@st.cache_data(ttl=60 * 60)
def load_pitch_arsenal_stats_live(year=2026, stat_type="pitcher"):
    """Load Baseball Savant pitch-arsenal stats split by pitch type.

    stat_type='pitcher' returns pitcher pitch usage/whiff by pitch.
    stat_type='batter' is used as a team hitting weakness table vs pitch types.

    The app tries Savant's CSV export first, then falls back to the visible HTML
    table. If Savant is unavailable, this safely returns an empty dataframe and
    the model applies a neutral 0.00 K modifier.
    """
    base_url = (
        "https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats"
        f"?min=1&pitchType=&position=undefined&sort=4&sortDir=desc&team=&type={stat_type}&year={year}"
    )
    df = pd.DataFrame()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "text/csv,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        from io import StringIO
        response = requests.get(base_url + "&csv=true", headers=headers, timeout=15)
        response.raise_for_status()
        text = response.text.strip()
        if "," in text and "<html" not in text.lower() and "<!doctype" not in text.lower():
            df = pd.read_csv(StringIO(text))
    except Exception:
        df = pd.DataFrame()

    if df.empty:
        df = safe_read_first_table(base_url, label=f"Savant {stat_type} pitch arsenal")

    if df is None or df.empty:
        return pd.DataFrame(columns=["Name", "Team", "Pitch Type", "Usage", "Whiff", "Run Value", "Source Type"])

    df = df.copy()
    df.columns = [_clean_col_name(c) for c in df.columns]
    if "Rk." in df.columns:
        df = df.drop(columns=["Rk."])

    name_col = _find_col(df, ["Player", "Name", "player_name", "last_name, first_name", "Pitcher", "Batter"])
    team_col = _find_col(df, ["Team", "Tm", "team_name", "team"])
    pitch_col = _find_col(df, ["Pitch Type", "Pitch", "pitch_name", "pitch_type"])
    usage_col = _find_col(df, ["Usage", "Usage %", "Pitch %", "Pitches %", "pitch_usage", "%"])
    whiff_col = _find_col(df, ["whiff_percent", "Whiff %", "Whiff"])
    k_col = _find_col(df, ["k_percent", "K %", "K%", "K"])
    putaway_col = _find_col(df, ["put_away", "Put Away %", "PutAway %", "Put Away", "PutAway", "put_away_percent"])
    run_value_col = _find_col(df, ["Run Value", "RV", "run_value", "Pitching Run Value", "Batting Run Value"])
    pitches_col = _find_col(df, ["Pitches", "Total Pitches", "pitch_count", "#"])

    out = pd.DataFrame()
    out["Name"] = df[name_col].astype(str).str.strip() if name_col else ""
    out["Team"] = df[team_col].astype(str).str.strip() if team_col else ""
    out["Pitch Type"] = df[pitch_col].apply(_canonical_pitch_type) if pitch_col else ""
    out["Usage"] = df[usage_col].apply(lambda x: _usage_to_rate(x, 0.0)) if usage_col else 0.0
    out["Whiff"] = df[whiff_col].apply(lambda x: _to_rate(x, 0.0)) if whiff_col else 0.0
    out["K"] = df[k_col].apply(lambda x: _to_rate(x, 0.0)) if k_col else 0.0
    out["Put Away"] = df[putaway_col].apply(lambda x: _to_rate(x, 0.0)) if putaway_col else 0.0
    out["Run Value"] = df[run_value_col].apply(lambda x: _to_number(x, 0.0)) if run_value_col else 0.0
    out["Pitches"] = df[pitches_col].apply(lambda x: _to_number(x, 0.0)) if pitches_col else 0.0
    out["Source Type"] = stat_type

    # If Savant returns player rows without usage but with pitch counts, estimate usage inside each player/team.
    if out["Usage"].max() == 0 and out["Pitches"].max() > 0:
        group_key = "Name" if out["Name"].astype(str).str.strip().ne("").any() else "Team"
        totals = out.groupby(group_key)["Pitches"].transform("sum").replace(0, pd.NA)
        out["Usage"] = (out["Pitches"] / totals).fillna(0)

    out = out[out["Pitch Type"].astype(str).str.strip() != ""].copy()
    return out.reset_index(drop=True)

def _legacy__pitcher_arsenal_rows_v1(pitcher, pitcher_arsenal_df):
    if pitcher_arsenal_df is None or pitcher_arsenal_df.empty:
        return pd.DataFrame()
    keys = _name_keys(pitcher)
    df = pitcher_arsenal_df.copy()
    df["_name_key"] = df["Name"].apply(normalize_name_for_match)
    rows = df[df["_name_key"].isin(keys)].copy()
    if rows.empty:
        return rows
    for col in ["Usage", "K", "Whiff", "Put Away"]:
        if col not in rows.columns:
            rows[col] = 0.0
        rows[col] = rows[col].apply(lambda x: _to_rate(x, 0.0))
    rows = rows.sort_values("Usage", ascending=False)
    return rows

def _legacy__team_pitch_type_rows_v1(team, team_pitch_type_df):
    if team_pitch_type_df is None or team_pitch_type_df.empty:
        return pd.DataFrame()
    keys = _team_keys(team)
    df = team_pitch_type_df.copy()
    df["_team_key"] = df["Team"].apply(normalize_name_for_match)
    df["_name_key"] = df["Name"].apply(normalize_name_for_match)
    rows = df[df["_team_key"].isin(keys) | df["_name_key"].isin(keys)].copy()
    if rows.empty:
        return rows

    for col in ["K", "Whiff", "Put Away"]:
        if col not in rows.columns:
            rows[col] = 0.0
        rows[col] = rows[col].apply(lambda x: _to_rate(x, 0.0))
    if "Pitches" in rows.columns:
        rows["Pitches"] = rows["Pitches"].apply(lambda x: _to_number(x, 0.0))
    else:
        rows["Pitches"] = 0.0

    agg_rows = []
    for pitch, group in rows.groupby("Pitch Type", dropna=False):
        pitch = str(pitch).strip()
        if not pitch:
            continue
        weights = pd.to_numeric(group.get("Pitches", 0), errors="coerce").fillna(0)
        total_pitches = float(weights.sum())
        metric_values = {}
        for col in ["K", "Whiff", "Put Away"]:
            vals = pd.to_numeric(group[col], errors="coerce").fillna(0)
            if total_pitches > 0:
                metric_values[col] = float((vals * weights).sum() / total_pitches)
            else:
                metric_values[col] = float(vals.mean()) if len(vals) else 0.0
        agg_rows.append({
            "Name": str(team),
            "Team": str(team),
            "Pitch Type": pitch,
            "K": metric_values["K"],
            "Whiff": metric_values["Whiff"],
            "Put Away": metric_values["Put Away"],
            "Pitches": total_pitches,
            "Source Type": "batter_team_aggregate",
        })

    return pd.DataFrame(agg_rows)

def _legacy_pitch_type_arsenal_adjustment_v1(pitcher, opponent, pitcher_arsenal_df=None, team_pitch_type_df=None):
    """Return a pitch-type K modifier and detail rows for debugging.

    Revised arsenal logic:
    - Uses league average as the baseline for each pitch type.
    - Calculates one unified pitch matchup edge from K%, Whiff%, and Put Away%.
    - Counts weapons only when usage is meaningful and the full matchup edge is strong.
    - Makes arsenal a real projection driver instead of a small after-the-fact tweak.
    """
    pitcher_rows = _pitcher_arsenal_rows(pitcher, pitcher_arsenal_df)
    team_rows = _team_pitch_type_rows(opponent, team_pitch_type_df)

    empty_cols = [
        "Pitch", "Usage",
        "Pitcher K", "Opponent K", "League K", "K Edge",
        "Pitcher Whiff", "Opponent Whiff", "League Whiff", "Whiff Edge",
        "Pitcher Put Away", "Opponent Put Away", "League Put Away", "Put Away Edge",
        "Combined Edge", "Opp Weakness", "Pitch Quality", "Weapon", "Extreme", "Confluence",
        "Contribution", "Match Status"
    ]
    if pitcher_rows.empty or team_rows.empty:
        return {
            "modifier": 0.0,
            "score": 0.0,
            "weapon_count": 0,
            "weapon_bonus": 0.0,
            "weapon_usage": 0.0,
            "scored_count": 0,
            "status": "Neutral fallback - pitch type table not available/matched",
            "details": pd.DataFrame(columns=empty_cols),
        }

    league_pitch_whiff = team_pitch_type_df.groupby("Pitch Type")["Whiff"].mean().to_dict() if team_pitch_type_df is not None and not team_pitch_type_df.empty else {}
    league_pitch_k = team_pitch_type_df.groupby("Pitch Type")["K"].mean().to_dict() if team_pitch_type_df is not None and not team_pitch_type_df.empty and "K" in team_pitch_type_df.columns else {}
    league_pitch_putaway = team_pitch_type_df.groupby("Pitch Type")["Put Away"].mean().to_dict() if team_pitch_type_df is not None and not team_pitch_type_df.empty and "Put Away" in team_pitch_type_df.columns else {}
    team_lookup = team_rows.drop_duplicates("Pitch Type").set_index("Pitch Type").to_dict("index")

    details = []
    score = 0.0
    scored_usage = 0.0
    matched_usage = 0.0
    scored_count = 0
    weapon_count = 0
    weapon_usage = 0.0

    # Display labels stay in percentage-point language because that is how you review the matchup.
    def _edge_label(edge_rate, usage):
        if usage < 0.09:
            return "No"
        if edge_rate >= 0.10:
            return "Strong Weapon"
        if edge_rate >= 0.05:
            return "Weapon"
        return "No"

    for _, p_row in pitcher_rows.iterrows():
        pitch = p_row.get("Pitch Type", "")
        usage = _normalized_usage_rate(p_row.get("Usage", 0), 0.0)
        p_whiff = _to_rate(p_row.get("Whiff", 0), 0.0)
        p_k = _to_rate(p_row.get("K", 0), 0.0)
        p_putaway = _to_rate(p_row.get("Put Away", 0), 0.0)
        league_whiff = _to_rate(league_pitch_whiff.get(pitch, 0.24), 0.24)
        league_k = _to_rate(league_pitch_k.get(pitch, 0.22), 0.22)
        league_putaway = _to_rate(league_pitch_putaway.get(pitch, 0.20), 0.20)

        # Ignore sub-5% show-me pitches entirely. These tiny usage rows can be noisy/misread
        # and should never drive or display in a strikeout projection.
        if usage < 0.05:
            continue

        pitcher_k_delta = p_k - league_k
        pitcher_whiff_delta = p_whiff - league_whiff
        pitcher_putaway_delta = p_putaway - league_putaway

        if pitch not in team_lookup:
            details.append({
                "Pitch": pitch,
                "Usage": round(usage * 100, 1),
                "Pitcher K": round(p_k * 100, 1),
                "Opponent K": "No Match",
                "League K": round(league_k * 100, 1),
                "K Edge": "",
                "Pitcher Whiff": round(p_whiff * 100, 1),
                "Opponent Whiff": "No Match",
                "League Whiff": round(league_whiff * 100, 1),
                "Whiff Edge": "",
                "Pitcher Put Away": round(p_putaway * 100, 1),
                "Opponent Put Away": "No Match",
                "League Put Away": round(league_putaway * 100, 1),
                "Put Away Edge": "",
                "Combined Edge": "",
                "Opp Weakness": "",
                "Pitch Quality": round(pitcher_whiff_delta * 100, 1),
                "Weapon": "",
                "Extreme": "",
                "Confluence": "",
                "Contribution": "",
                "Match Status": "No opponent pitch-type match",
            })
            continue

        opp_whiff = _to_rate(team_lookup[pitch].get("Whiff", 0), 0.0)
        opp_k = _to_rate(team_lookup[pitch].get("K", 0), 0.0)
        opp_putaway = _to_rate(team_lookup[pitch].get("Put Away", 0), 0.0)
        opponent_k_delta = opp_k - league_k
        opponent_whiff_delta = opp_whiff - league_whiff
        opponent_putaway_delta = opp_putaway - league_putaway
        matched_usage += usage

        # Unified league-adjusted matchup edges, in rate form.
        # Example: pitcher slider K 43%, league 31%, opponent 27% => +12 + -4 = +8.
        k_edge = pitcher_k_delta + opponent_k_delta
        whiff_edge = pitcher_whiff_delta + opponent_whiff_delta
        putaway_edge = pitcher_putaway_delta + opponent_putaway_delta

        # K is the actual result, Whiff is the stability check, Put Away is the finishing check.
        combined_edge = (0.50 * k_edge) + (0.35 * whiff_edge) + (0.15 * putaway_edge)
        opp_weakness = opponent_k_delta
        pitch_quality = pitcher_k_delta

        weapon_label = _edge_label(combined_edge, usage)
        is_weapon = weapon_label in ["Weapon", "Strong Weapon"]
        if is_weapon:
            weapon_count += 1
            weapon_usage += usage

        if False and usage < 0.05:
            details.append({
                "Pitch": pitch,
                "Usage": round(usage * 100, 1),
                "Pitcher K": round(p_k * 100, 1),
                "Opponent K": round(opp_k * 100, 1),
                "League K": round(league_k * 100, 1),
                "K Edge": round(k_edge * 100, 1),
                "Pitcher Whiff": round(p_whiff * 100, 1),
                "Opponent Whiff": round(opp_whiff * 100, 1),
                "League Whiff": round(league_whiff * 100, 1),
                "Whiff Edge": round(whiff_edge * 100, 1),
                "Pitcher Put Away": round(p_putaway * 100, 1),
                "Opponent Put Away": round(opp_putaway * 100, 1),
                "League Put Away": round(league_putaway * 100, 1),
                "Put Away Edge": round(putaway_edge * 100, 1),
                "Combined Edge": round(combined_edge * 100, 1),
                "Opp Weakness": round(opp_weakness * 100, 1),
                "Pitch Quality": round(pitch_quality * 100, 1),
                "Weapon": weapon_label,
                "Extreme": "Below scoring floor" if is_weapon else "",
                "Confluence": "",
                "Contribution": "",
                "Match Status": "Matched - below 5% scoring floor",
            })
            continue

        confluence = (
            (pitch_quality > 0 and opp_weakness > 0) or
            (pitch_quality < 0 and opp_weakness < 0)
        )

        extreme_tags = []
        multiplier = 1.00
        if confluence:
            multiplier *= 1.15
        if combined_edge >= 0.10 and usage >= 0.09:
            multiplier *= 1.15
            extreme_tags.append("Strong Weapon")
        if combined_edge <= -0.10 and usage >= 0.09:
            multiplier *= 1.15
            extreme_tags.append("Major Negative")
        if pitcher_k_delta >= 0.10 and usage >= 0.09:
            extreme_tags.append("Elite Pitcher K")
        if opponent_k_delta >= 0.06 and usage >= 0.09:
            extreme_tags.append("Opponent K Weakness")
        if opponent_k_delta <= -0.06 and usage >= 0.09:
            extreme_tags.append("Opponent Handles Pitch")

        contribution = usage * combined_edge * multiplier
        score += contribution
        scored_usage += usage
        scored_count += 1

        details.append({
            "Pitch": pitch,
            "Usage": round(usage * 100, 1),
            "Pitcher K": round(p_k * 100, 1),
            "Opponent K": round(opp_k * 100, 1),
            "League K": round(league_k * 100, 1),
            "K Edge": round(k_edge * 100, 1),
            "Pitcher Whiff": round(p_whiff * 100, 1),
            "Opponent Whiff": round(opp_whiff * 100, 1),
            "League Whiff": round(league_whiff * 100, 1),
            "Whiff Edge": round(whiff_edge * 100, 1),
            "Pitcher Put Away": round(p_putaway * 100, 1),
            "Opponent Put Away": round(opp_putaway * 100, 1),
            "League Put Away": round(league_putaway * 100, 1),
            "Put Away Edge": round(putaway_edge * 100, 1),
            "Combined Edge": round(combined_edge * 100, 1),
            "Opp Weakness": round(opp_weakness * 100, 1),
            "Pitch Quality": round(pitch_quality * 100, 1),
            "Weapon": weapon_label,
            "Extreme": ", ".join(extreme_tags),
            "Confluence": "Yes" if confluence else "No",
            "Contribution": round(contribution * 100, 2),
            "Match Status": "Matched and scored",
        })

    if not details or scored_usage <= 0:
        detail_df = pd.DataFrame(details, columns=empty_cols) if details else pd.DataFrame(columns=empty_cols)
        return {
            "modifier": 0.0,
            "score": 0.0,
            "weapon_count": weapon_count,
            "weapon_bonus": 0.0,
            "weapon_usage": round(weapon_usage * 100, 1),
            "scored_count": 0,
            "status": "Neutral fallback - no matching pitch types above usage floor",
            "details": detail_df,
        }

    # Normalized score is displayed as percentage points. +8.0 means the weighted pitch mix is +8 points.
    normalized_score = max(-0.18, min(0.18, score / max(0.25, scored_usage)))

    # Projection impact: arsenal is now a main driver, but still capped to avoid one data table dominating everything.
    # Rough map before weapon handling: +5 edge ≈ +0.35 K, +10 edge ≈ +0.70 K, extreme +15 edge ≈ +1.05 K.
    base_modifier = normalized_score * 7.0

    # True weapons should be more than a display label. These are now hard projection floors,
    # not tiny tiebreaker bonuses. A pitcher with 2-3 real weapons against the confirmed lineup
    # should be pushed toward over consideration unless another major factor disagrees.
    weapon_bonus = 0.0
    weapon_modifier_floor = None
    if weapon_count >= 3 and weapon_usage >= 0.27:
        weapon_bonus = 1.60
        weapon_modifier_floor = 1.60
    elif weapon_count == 2 and weapon_usage >= 0.20:
        weapon_bonus = 0.95
        weapon_modifier_floor = 0.95
    elif weapon_count == 1 and weapon_usage >= 0.15:
        weapon_bonus = 0.35
        weapon_modifier_floor = 0.35

    # Weapon protection / floor:
    # If a pitcher has 2+ true weapons, the arsenal layer should not be allowed
    # to create a negative K modifier. Multiple real weapons make unders risky,
    # even if secondary pitches grade poorly.
    if weapon_count >= 2 and weapon_usage >= 0.20:
        base_modifier = max(base_modifier, 0.0)

    negative_profile_penalty = 0.0
    if weapon_count == 0 and normalized_score <= -0.05:
        negative_profile_penalty -= 0.15
    if weapon_count == 0 and normalized_score <= -0.10:
        negative_profile_penalty -= 0.15

    raw_modifier = base_modifier + weapon_bonus + negative_profile_penalty
    if weapon_modifier_floor is not None:
        raw_modifier = max(raw_modifier, weapon_modifier_floor)

    # Do not cap strong weapon matchups too tightly. We still keep a broad safety cap,
    # but 3+ true weapons should be allowed to fully express a large K-ceiling boost.
    if weapon_count >= 3 and weapon_usage >= 0.27:
        positive_cap = 4.00
    elif weapon_count == 2 and weapon_usage >= 0.20:
        positive_cap = 3.00
    else:
        positive_cap = 2.25
    modifier = max(-1.35, min(positive_cap, raw_modifier))

    detail_df = pd.DataFrame(details)
    if "Contribution" in detail_df.columns:
        detail_df["_sort_contribution"] = pd.to_numeric(detail_df["Contribution"], errors="coerce").fillna(-999)
        detail_df = detail_df.sort_values(["_sort_contribution", "Usage"], ascending=[False, False]).drop(columns=["_sort_contribution"]).reset_index(drop=True)

    coverage = round(min(100.0, matched_usage * 100), 1)
    return {
        "modifier": round(modifier, 2),
        "score": round(normalized_score * 100, 1),
        "weapon_count": int(weapon_count),
        "weapon_bonus": round(weapon_bonus, 2),
        "weapon_usage": round(weapon_usage * 100, 1),
        "scored_count": int(scored_count),
        "status": f"Pitch-type arsenal matched - {scored_count} scored pitches, {coverage}% arsenal usage matched, {weapon_count} true weapons (>=9% usage, +5 combined edge; sub-5% pitches ignored)",
        "details": detail_df,
    }

def _legacy_apply_pitch_type_modifier_v1(base_projection, pitcher, opponent, pitcher_arsenal_df=None, team_pitch_type_df=None):
    adj = pitch_type_arsenal_adjustment(pitcher, opponent, pitcher_arsenal_df, team_pitch_type_df)
    return max(0, base_projection + adj["modifier"]), adj

@st.cache_data(ttl=60 * 60)

def _legacy_load_team_hitting_stats_live_v2():
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
def _legacy_load_team_batting_split_live_v2(split):
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
def _legacy_load_pitcher_data_live_v1(year):
    url = f"https://baseballsavant.mlb.com/leaderboard/custom?year={year}&type=pitcher&filter=&min=1&selections=p_game%2Cp_formatted_ip%2Cpa%2Cstrikeout%2Ck_percent%2Cbb_percent%2Cp_era%2Cp_foul%2Cxwoba%2Chard_hit_percent%2Cbarrel_batted_rate%2Cgroundballs_percent%2Chome_run%2Cout_zone_percent%2Cpitch_count%2Cin_zone_percent%2Cwhiff_percent%2Cf_strike_percent&chart=false&x=pa&y=pa&r=no&chartType=beeswarm&sort=player_name&sortDir=asc"
    df = safe_read_first_table(url)
    if df.empty:
        return df
    if "Rk." in df.columns:
        df = df.drop(columns=["Rk."])

    # Baseball Savant custom leaderboards can return either pretty labels
    # ("Hard Hit %") or raw field names ("hard_hit_percent"). Normalize both
    # so contact suppression does not silently fall back to league-average defaults.
    def _norm_col(c):
        import re
        return re.sub(r"[^a-z0-9]+", "", str(c).lower())

    aliases = {
        "player": ["player", "playername", "lastfirstname"],
        "Year": ["year", "pyear"],
        "G": ["g", "game", "games", "pgame"],
        "IP": ["ip", "innings", "pformattedip", "formattedip"],
        "BF": ["bf", "pa", "battersfaced"],
        "SO": ["so", "k", "strikeout", "strikeouts"],
        "K%": ["k", "kpercent", "kpercentage", "strikeoutpercent", "strikeoutpercentage"],
        "BB%": ["bb", "bbpercent", "bbpercentage", "walkpercent", "walkpercentage"],
        "ERA": ["era", "pera"],
        "Foul": ["foul", "pfoul"],
        "xwOBA": ["xwoba"],
        "Hard Hit %": ["hardhit", "hardhitpercent", "hardhitpercentage", "hardhitpct", "hardhitrate"],
        "Barrel %": ["barrel", "barrelpercent", "barrelpercentage", "barrelpct", "barrelrate", "barrelbattedrate", "barrelsperbbe"],
        "GB%": ["gb", "gbpercent", "gbpercentage", "groundballpercent", "groundballpercentage", "groundballspercent", "groundballspercentage", "groundballrate", "groundballsrate"],
        "HR": ["hr", "homerun", "homeruns", "home_run"],
        "Out of Zone %": ["outofzone", "outofzonepercent", "outzonepercent"],
        "Pitches": ["pitches", "pitchcount"],
        "In Zone %": ["inzone", "inzonepercent"],
        "Whiff %": ["whiff", "whiffpercent", "whiffpercentage", "whiffpct"],
        "First Strike %": ["firststrike", "firststrikepercent", "fstrikepercent", "fstrikepct"],
    }

    rename = {}
    normalized_columns = {_norm_col(c): c for c in df.columns}
    for target, keys in aliases.items():
        for key in keys:
            source = normalized_columns.get(_norm_col(key))
            if source is not None and source not in rename:
                rename[source] = target
                break
    df = df.rename(columns=rename)

    keep = ["Player", "Year", "G", "IP", "BF", "SO", "K%", "BB%", "ERA", "Foul", "xwOBA", "Hard Hit %", "Barrel %", "GB%", "HR", "Out of Zone %", "Pitches", "In Zone %", "Whiff %", "First Strike %"]
    df = df[[c for c in keep if c in df.columns]]
    if "Player" in df.columns:
        df["Player"] = df["Player"].astype(str).str.strip()
    for c in df.columns:
        if c != "Player":
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=60 * 60)
def _legacy_load_pitcher_handedness_live_v1(year=2026):
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
def _legacy_load_nrfi_pitchers_live_v1():
    """Neutral NRFI pitcher fallback.

    The old mobile admin used Fangraphs here, but Fangraphs blocks scraping.
    Keep the NRFI model running with neutral first-inning rows when a dedicated
    source is unavailable.
    """
    return pd.DataFrame(columns=[
        "Season", "Name", "Player Name", "Games", "Plate Appearances", "ERA",
        "H", "R", "ER", "BB", "SO", "AVG", "OBP", "SLG", "wOBA"
    ])


@st.cache_data(ttl=60 * 60)
def _legacy_load_nrfi_team_split_live_v1(hand):
    """Neutral NRFI team split fallback without Fangraphs scraping."""
    teams = sorted(set(TEAM_ABBR_MAP.values()))
    return pd.DataFrame({
        "Teams": teams,
        "OBP": 0.320,
        "K%": 0.220,
        "wOBA": 0.320,
        "BB/K": 0.50,
        "ISO": 0.170,
    })


@st.cache_data(ttl=60 * 60)
def _legacy_load_team_strikeouts_live_v1():
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


def _normalize_mlb_team_name(name):
    name = str(name).strip()
    if name in ["Oakland Athletics", "Sacramento Athletics"]:
        return "Athletics"
    return TEAM_ABBR_MAP.get(name, name)


@st.cache_data(ttl=60 * 60)
def _mlb_team_stats(group="hitting", season=MLB_SEASON, stats_type="season"):
    """Official MLB Stats API team stats. This replaces the MLB.com HTML table path."""
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/teams/stats",
            params={"sportIds": 1, "group": group, "stats": stats_type, "season": season},
            timeout=30,
        )
        r.raise_for_status()
        rows = []
        for block in r.json().get("stats", []):
            for split in block.get("splits", []):
                team = split.get("team", {}) or {}
                stat = split.get("stat", {}) or {}
                row = {"Teams": _normalize_mlb_team_name(team.get("name", ""))}
                row.update(stat)
                rows.append(row)
        return pd.DataFrame(rows)
    except Exception as e:
        st.warning(f"Could not load MLB {group} team stats from Stats API: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=60 * 60)
def _legacy__mlb_all_player_pitching_stats_v1(season=MLB_SEASON):
    """Official MLB Stats API player pitching stats. Used as a MLB Stats API fallback for pitcher tables."""
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/stats",
            params={
                "stats": "season",
                "group": "pitching",
                "playerPool": "ALL",
                "season": season,
                "sportIds": 1,
                "limit": 5000,
            },
            timeout=40,
        )
        r.raise_for_status()
        rows = []
        for block in r.json().get("stats", []):
            for split in block.get("splits", []):
                player = split.get("player", {}) or {}
                stat = split.get("stat", {}) or {}
                name = player.get("fullName", "")
                if not name:
                    continue
                ip = _mlb_num(stat.get("inningsPitched", 0), 0)
                games = _mlb_num(stat.get("gamesPlayed", stat.get("games", 0)), 0)
                so = _mlb_num(stat.get("strikeOuts", 0), 0)
                bb = _mlb_num(stat.get("baseOnBalls", 0), 0)
                batters = _mlb_num(stat.get("battersFaced", 0), 0)
                k_pct = (so / batters) if batters > 0 else 0
                bb_pct = (bb / batters) if batters > 0 else 0
                rows.append({
                    "Player": to_last_first(name),
                    "Year": season,
                    "G": games,
                    "IP": ip,
                    "BF": batters if batters > 0 else ip * 4.3,
                    "SO": so,
                    "K%": k_pct,
                    "BB%": bb_pct,
                    "ERA": _mlb_num(stat.get("era", 0), 0),
                    "Foul": 0,
                    "xwOBA": 0.320,
                    "Hard Hit %": 0,
                    "Out of Zone %": 0,
                    "Pitches": 0,
                    "In Zone %": 0,
                    "Whiff %": 0,
                    "First Strike %": 0,
                    "Throws": "R",
                    "MLBAM ID": player.get("id", ""),
                })
        return pd.DataFrame(rows)
    except Exception as e:
        st.warning(f"Could not load MLB player pitching stats from Stats API: {e}")
        return pd.DataFrame(columns=["Player", "Year", "G", "IP", "BF", "SO", "K%", "BB%", "ERA", "xwOBA", "Throws"])


@st.cache_data(ttl=60 * 60)
def _mlb_player_handedness_lookup(season=MLB_SEASON):
    """Pull pitcher throwing hand from MLB people endpoint. If this fails, formulas fall back to R."""
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/sports/1/players",
            params={"season": season},
            timeout=40,
        )
        r.raise_for_status()
        rows = []
        for p in r.json().get("people", []):
            name = p.get("fullName", "")
            hand = ((p.get("pitchHand") or {}).get("code") or "R").strip().upper()
            if name:
                rows.append({"Player": to_last_first(name), "Throws": hand})
        return pd.DataFrame(rows)
    except Exception as e:
        st.warning(f"Could not load MLB pitcher handedness: {e}")
        return pd.DataFrame(columns=["Player", "Throws"])


def _neutral_nrfi_pitcher_table():
    return pd.DataFrame([{
        "Season": MLB_SEASON,
        "Name": "Neutral Baseline",
        "Player Name": "Neutral Baseline",
        "Games": 0,
        "Plate Appearances": 0,
        "ERA": 0,
        "H": 0,
        "R": 0,
        "ER": 0,
        "BB": 0,
        "SO": 0,
        "AVG": 0,
        "OBP": 0.320,
        "SLG": 0,
        "wOBA": 0.320,
    }])

# -----------------------
# TRUE PITCH-TYPE ARSENAL HELPERS
# -----------------------

PITCH_TYPE_ALIASES = {
    "4-SEAM": "4-Seam", "4-SEAM FASTBALL": "4-Seam", "FOUR-SEAM": "4-Seam", "FOUR-SEAM FASTBALL": "4-Seam", "FF": "4-Seam",
    "SINKER": "Sinker", "SI": "Sinker", "2-SEAM": "Sinker", "TWO-SEAM": "Sinker",
    "CUTTER": "Cutter", "FC": "Cutter",
    "SLIDER": "Slider", "SL": "Slider",
    "SWEEPER": "Sweeper", "ST": "Sweeper",
    "CURVEBALL": "Curveball", "CURVE": "Curveball", "CU": "Curveball", "KC": "Curveball", "KNUCKLE CURVE": "Curveball",
    "CHANGEUP": "Changeup", "CHANGE": "Changeup", "CH": "Changeup",
    "SPLITTER": "Splitter", "SPLIT-FINGER": "Splitter", "SPLIT FINGER": "Splitter", "FS": "Splitter",
    "KNUCKLEBALL": "Knuckleball", "KN": "Knuckleball",
}

TEAM_NAME_ALIASES_FOR_SAVANT = {
    "Arizona Diamondbacks": ["Arizona Diamondbacks", "D-backs", "Dbacks", "Diamondbacks", "ARI", "AZ"],
    "Athletics": ["Athletics", "Oakland Athletics", "Sacramento Athletics", "A's", "As", "OAK", "ATH"],
    "Atlanta Braves": ["Atlanta Braves", "Braves", "ATL"],
    "Baltimore Orioles": ["Baltimore Orioles", "Orioles", "BAL"],
    "Boston Red Sox": ["Boston Red Sox", "Red Sox", "BOS"],
    "Chicago Cubs": ["Chicago Cubs", "Cubs", "CHC"],
    "Chicago White Sox": ["Chicago White Sox", "Chi White Sox", "White Sox", "CWS", "CHW"],
    "Cincinnati Reds": ["Cincinnati Reds", "Reds", "CIN"],
    "Cleveland Guardians": ["Cleveland Guardians", "Guardians", "CLE"],
    "Colorado Rockies": ["Colorado Rockies", "Rockies", "COL"],
    "Detroit Tigers": ["Detroit Tigers", "Tigers", "DET"],
    "Houston Astros": ["Houston Astros", "Astros", "HOU"],
    "Kansas City Royals": ["Kansas City Royals", "KC Royals", "Royals", "KC", "KCR"],
    "Los Angeles Angels": ["Los Angeles Angels", "LA Angels", "Angels", "LAA"],
    "Los Angeles Dodgers": ["Los Angeles Dodgers", "LA Dodgers", "Dodgers", "LAD"],
    "Miami Marlins": ["Miami Marlins", "Marlins", "MIA"],
    "Milwaukee Brewers": ["Milwaukee Brewers", "Brewers", "MIL"],
    "Minnesota Twins": ["Minnesota Twins", "Twins", "MIN"],
    "New York Mets": ["New York Mets", "NY Mets", "Mets", "NYM"],
    "New York Yankees": ["New York Yankees", "NY Yankees", "Yankees", "NYY"],
    "Philadelphia Phillies": ["Philadelphia Phillies", "Phillies", "PHI"],
    "Pittsburgh Pirates": ["Pittsburgh Pirates", "Pirates", "PIT"],
    "San Diego Padres": ["San Diego Padres", "Padres", "SD", "SDP"],
    "San Francisco Giants": ["San Francisco Giants", "SF Giants", "Giants", "SF", "SFG"],
    "Seattle Mariners": ["Seattle Mariners", "Mariners", "SEA"],
    "St. Louis Cardinals": ["St. Louis Cardinals", "St Louis Cardinals", "Saint Louis Cardinals", "Cardinals", "STL"],
    "Tampa Bay Rays": ["Tampa Bay Rays", "TB Rays", "Rays", "TB", "TBR"],
    "Texas Rangers": ["Texas Rangers", "Rangers", "TEX"],
    "Toronto Blue Jays": ["Toronto Blue Jays", "Blue Jays", "TOR"],
    "Washington Nationals": ["Washington Nationals", "Nationals", "WSH", "WSN", "WAS"],
}

def _clean_col_name(col):
    return str(col).replace("\n", " ").replace("  ", " ").strip()

def _find_col(df, candidates):
    if df is None or df.empty:
        return None
    normalized = {_clean_col_name(c).lower(): c for c in df.columns}
    for cand in candidates:
        key = cand.lower()
        if key in normalized:
            return normalized[key]
    for c in df.columns:
        low = _clean_col_name(c).lower()
        if any(cand.lower() in low for cand in candidates):
            return c
    return None

def _to_rate(value, default=0.0):
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    text = str(value).replace("%", "").replace("−", "-").strip()
    if text == "" or text.lower() in ["nan", "none", "--"]:
        return default
    try:
        num = float(text)
    except Exception:
        return default
    if abs(num) > 1:
        return num / 100.0
    return num

def _to_number(value, default=0.0):
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    text = str(value).replace("%", "").replace("−", "-").strip()
    if text == "" or text.lower() in ["nan", "none", "--"]:
        return default
    try:
        return float(text)
    except Exception:
        return default

def _canonical_pitch_type(value):
    text = str(value).strip().upper()
    if not text or text in ["NAN", "NONE", "--"]:
        return ""
    return PITCH_TYPE_ALIASES.get(text, str(value).strip())

def _name_keys(name):
    first_last = to_first_last(name)
    last_first = to_last_first(name)
    raw = str(name).strip()
    return {normalize_name_for_match(x) for x in [raw, first_last, last_first] if str(x).strip()}

def _team_keys(team):
    """Return normalized aliases for a team across MLB API, Savant, odds, and UI labels.

    This intentionally does not rely on an exact dictionary key match. If the
    incoming value is already an alias like CHW, CWS, White Sox, STL, etc., it
    expands that value to the full alias set for the correct MLB team.
    """
    raw = str(team).strip()
    raw_key = normalize_name_for_match(raw)
    keys = {raw_key} if raw_key else set()
    for canonical, aliases in TEAM_NAME_ALIASES_FOR_SAVANT.items():
        alias_keys = {normalize_name_for_match(x) for x in ([canonical] + list(aliases)) if str(x).strip()}
        if raw_key in alias_keys:
            keys.update(alias_keys)
            break
    return keys

@st.cache_data(ttl=60 * 60)
def _legacy_load_pitch_arsenal_stats_live_unused(year=2026, stat_type="pitcher"):
    """Legacy unused loader kept for reference. Active loader is defined later.

    Load Baseball Savant pitch-arsenal stats split by pitch type.

    stat_type='pitcher' returns pitcher pitch usage/whiff by pitch.
    stat_type='batter' is used as a team hitting weakness table vs pitch types.

    The app tries Savant's CSV export first, then falls back to the visible HTML
    table. If Savant is unavailable, this safely returns an empty dataframe and
    the model applies a neutral 0.00 K modifier.
    """
    base_url = (
        "https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats"
        f"?min=1&pitchType=&position=undefined&sort=4&sortDir=desc&team=&type={stat_type}&year={year}"
    )
    df = pd.DataFrame()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "text/csv,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        from io import StringIO
        response = requests.get(base_url + "&csv=true", headers=headers, timeout=15)
        response.raise_for_status()
        text = response.text.strip()
        if "," in text and "<html" not in text.lower() and "<!doctype" not in text.lower():
            df = pd.read_csv(StringIO(text))
    except Exception:
        df = pd.DataFrame()

    if df.empty:
        df = safe_read_first_table(base_url, label=f"Savant {stat_type} pitch arsenal")

    if df is None or df.empty:
        return pd.DataFrame(columns=["Name", "Team", "Pitch Type", "Usage", "Whiff", "Run Value", "Source Type"])

    df = df.copy()
    df.columns = [_clean_col_name(c) for c in df.columns]
    if "Rk." in df.columns:
        df = df.drop(columns=["Rk."])

    name_col = _find_col(df, ["Player", "Name", "player_name", "last_name, first_name", "Pitcher", "Batter"])
    team_col = _find_col(df, ["Team", "Tm", "team_name", "team"])
    pitch_col = _find_col(df, ["Pitch Type", "Pitch", "pitch_name", "pitch_type"])
    usage_col = _find_col(df, ["Usage", "Usage %", "Pitch %", "Pitches %", "pitch_usage", "%"])
    whiff_col = _find_col(df, ["Whiff %", "Whiff", "whiff_percent"])
    run_value_col = _find_col(df, ["Run Value", "RV", "run_value", "Pitching Run Value", "Batting Run Value"])
    pitches_col = _find_col(df, ["Pitches", "Total Pitches", "pitch_count", "#"])

    out = pd.DataFrame()
    out["Name"] = df[name_col].astype(str).str.strip() if name_col else ""
    out["Team"] = df[team_col].astype(str).str.strip() if team_col else ""
    out["Pitch Type"] = df[pitch_col].apply(_canonical_pitch_type) if pitch_col else ""
    out["Usage"] = df[usage_col].apply(lambda x: _usage_to_rate(x, 0.0)) if usage_col else 0.0
    out["Whiff"] = df[whiff_col].apply(lambda x: _to_rate(x, 0.0)) if whiff_col else 0.0
    out["Run Value"] = df[run_value_col].apply(lambda x: _to_number(x, 0.0)) if run_value_col else 0.0
    out["Pitches"] = df[pitches_col].apply(lambda x: _to_number(x, 0.0)) if pitches_col else 0.0
    out["Source Type"] = stat_type

    # If Savant returns player rows without usage but with pitch counts, estimate usage inside each player/team.
    if out["Usage"].max() == 0 and out["Pitches"].max() > 0:
        group_key = "Name" if out["Name"].astype(str).str.strip().ne("").any() else "Team"
        totals = out.groupby(group_key)["Pitches"].transform("sum").replace(0, pd.NA)
        out["Usage"] = (out["Pitches"] / totals).fillna(0)

    out = out[out["Pitch Type"].astype(str).str.strip() != ""].copy()
    return out.reset_index(drop=True)

def _pitcher_arsenal_rows(pitcher, pitcher_arsenal_df):
    if pitcher_arsenal_df is None or pitcher_arsenal_df.empty:
        return pd.DataFrame()
    keys = _name_keys(pitcher)
    df = pitcher_arsenal_df.copy()
    df["_name_key"] = df["Name"].apply(normalize_name_for_match)
    rows = df[df["_name_key"].isin(keys)].copy()
    if rows.empty:
        return rows
    for col in ["Usage", "K", "Whiff", "Put Away"]:
        if col not in rows.columns:
            rows[col] = 0.0
        rows[col] = rows[col].apply(lambda x: _to_rate(x, 0.0))
    rows = rows.sort_values("Usage", ascending=False)
    return rows

def _team_pitch_type_rows(team, team_pitch_type_df):
    if team_pitch_type_df is None or team_pitch_type_df.empty:
        return pd.DataFrame()
    keys = _team_keys(team)
    df = team_pitch_type_df.copy()
    df["_team_key"] = df["Team"].apply(normalize_name_for_match)
    df["_name_key"] = df["Name"].apply(normalize_name_for_match)
    rows = df[df["_team_key"].isin(keys) | df["_name_key"].isin(keys)].copy()
    if rows.empty:
        return rows

    for col in ["K", "Whiff", "Put Away"]:
        if col not in rows.columns:
            rows[col] = 0.0
        rows[col] = rows[col].apply(lambda x: _to_rate(x, 0.0))
    if "Pitches" in rows.columns:
        rows["Pitches"] = rows["Pitches"].apply(lambda x: _to_number(x, 0.0))
    else:
        rows["Pitches"] = 0.0

    agg_rows = []
    for pitch, group in rows.groupby("Pitch Type", dropna=False):
        pitch = str(pitch).strip()
        if not pitch:
            continue
        weights = pd.to_numeric(group.get("Pitches", 0), errors="coerce").fillna(0)
        total_pitches = float(weights.sum())
        metric_values = {}
        for col in ["K", "Whiff", "Put Away"]:
            vals = pd.to_numeric(group[col], errors="coerce").fillna(0)
            if total_pitches > 0:
                metric_values[col] = float((vals * weights).sum() / total_pitches)
            else:
                metric_values[col] = float(vals.mean()) if len(vals) else 0.0
        agg_rows.append({
            "Name": str(team),
            "Team": str(team),
            "Pitch Type": pitch,
            "K": metric_values["K"],
            "Whiff": metric_values["Whiff"],
            "Put Away": metric_values["Put Away"],
            "Pitches": total_pitches,
            "Source Type": "batter_team_aggregate",
        })

    return pd.DataFrame(agg_rows)

def pitch_type_arsenal_adjustment(pitcher, opponent, pitcher_arsenal_df=None, team_pitch_type_df=None):
    """Return a pitch-type K modifier and detail rows for debugging.

    Revised arsenal logic:
    - Uses league average as the baseline for each pitch type.
    - Calculates one unified pitch matchup edge from K%, Whiff%, and Put Away%.
    - Counts weapons only when usage is meaningful and the full matchup edge is strong.
    - Makes arsenal a real projection driver instead of a small after-the-fact tweak.
    """
    pitcher_rows = _pitcher_arsenal_rows(pitcher, pitcher_arsenal_df)
    team_rows = _team_pitch_type_rows(opponent, team_pitch_type_df)

    empty_cols = [
        "Pitch", "Usage",
        "Pitcher K", "Opponent K", "League K", "K Edge",
        "Pitcher Whiff", "Opponent Whiff", "League Whiff", "Whiff Edge",
        "Pitcher Put Away", "Opponent Put Away", "League Put Away", "Put Away Edge",
        "Combined Edge", "Opp Weakness", "Pitch Quality", "Weapon", "Extreme", "Confluence",
        "Contribution", "Match Status"
    ]
    if pitcher_rows.empty or team_rows.empty:
        return {
            "modifier": 0.0,
            "score": 0.0,
            "weapon_count": 0,
            "weapon_bonus": 0.0,
            "weapon_usage": 0.0,
            "scored_count": 0,
            "status": "Neutral fallback - pitch type table not available/matched",
            "details": pd.DataFrame(columns=empty_cols),
        }

    league_pitch_whiff = team_pitch_type_df.groupby("Pitch Type")["Whiff"].mean().to_dict() if team_pitch_type_df is not None and not team_pitch_type_df.empty else {}
    league_pitch_k = team_pitch_type_df.groupby("Pitch Type")["K"].mean().to_dict() if team_pitch_type_df is not None and not team_pitch_type_df.empty and "K" in team_pitch_type_df.columns else {}
    league_pitch_putaway = team_pitch_type_df.groupby("Pitch Type")["Put Away"].mean().to_dict() if team_pitch_type_df is not None and not team_pitch_type_df.empty and "Put Away" in team_pitch_type_df.columns else {}
    team_lookup = team_rows.drop_duplicates("Pitch Type").set_index("Pitch Type").to_dict("index")

    details = []
    score = 0.0
    scored_usage = 0.0
    matched_usage = 0.0
    scored_count = 0
    weapon_count = 0
    weapon_usage = 0.0

    # Display labels stay in percentage-point language because that is how you review the matchup.
    def _edge_label(edge_rate, usage):
        if usage < 0.09:
            return "No"
        if edge_rate >= 0.10:
            return "Strong Weapon"
        if edge_rate >= 0.05:
            return "Weapon"
        return "No"

    for _, p_row in pitcher_rows.iterrows():
        pitch = p_row.get("Pitch Type", "")
        usage = _normalized_usage_rate(p_row.get("Usage", 0), 0.0)
        p_whiff = _to_rate(p_row.get("Whiff", 0), 0.0)
        p_k = _to_rate(p_row.get("K", 0), 0.0)
        p_putaway = _to_rate(p_row.get("Put Away", 0), 0.0)
        league_whiff = _to_rate(league_pitch_whiff.get(pitch, 0.24), 0.24)
        league_k = _to_rate(league_pitch_k.get(pitch, 0.22), 0.22)
        league_putaway = _to_rate(league_pitch_putaway.get(pitch, 0.20), 0.20)

        # Ignore sub-5% show-me pitches entirely. These tiny usage rows can be noisy/misread
        # and should never drive or display in a strikeout projection.
        if usage < 0.05:
            continue

        pitcher_k_delta = p_k - league_k
        pitcher_whiff_delta = p_whiff - league_whiff
        pitcher_putaway_delta = p_putaway - league_putaway

        if pitch not in team_lookup:
            details.append({
                "Pitch": pitch,
                "Usage": round(usage * 100, 1),
                "Pitcher K": round(p_k * 100, 1),
                "Opponent K": "No Match",
                "League K": round(league_k * 100, 1),
                "K Edge": "",
                "Pitcher Whiff": round(p_whiff * 100, 1),
                "Opponent Whiff": "No Match",
                "League Whiff": round(league_whiff * 100, 1),
                "Whiff Edge": "",
                "Pitcher Put Away": round(p_putaway * 100, 1),
                "Opponent Put Away": "No Match",
                "League Put Away": round(league_putaway * 100, 1),
                "Put Away Edge": "",
                "Combined Edge": "",
                "Opp Weakness": "",
                "Pitch Quality": round(pitcher_whiff_delta * 100, 1),
                "Weapon": "",
                "Extreme": "",
                "Confluence": "",
                "Contribution": "",
                "Match Status": "No opponent pitch-type match",
            })
            continue

        opp_whiff = _to_rate(team_lookup[pitch].get("Whiff", 0), 0.0)
        opp_k = _to_rate(team_lookup[pitch].get("K", 0), 0.0)
        opp_putaway = _to_rate(team_lookup[pitch].get("Put Away", 0), 0.0)
        opponent_k_delta = opp_k - league_k
        opponent_whiff_delta = opp_whiff - league_whiff
        opponent_putaway_delta = opp_putaway - league_putaway
        matched_usage += usage

        # Unified league-adjusted matchup edges, in rate form.
        # Example: pitcher slider K 43%, league 31%, opponent 27% => +12 + -4 = +8.
        k_edge = pitcher_k_delta + opponent_k_delta
        whiff_edge = pitcher_whiff_delta + opponent_whiff_delta
        putaway_edge = pitcher_putaway_delta + opponent_putaway_delta

        # K is the actual result, Whiff is the stability check, Put Away is the finishing check.
        combined_edge = (0.50 * k_edge) + (0.35 * whiff_edge) + (0.15 * putaway_edge)
        opp_weakness = opponent_k_delta
        pitch_quality = pitcher_k_delta

        weapon_label = _edge_label(combined_edge, usage)
        is_weapon = weapon_label in ["Weapon", "Strong Weapon"]
        if is_weapon:
            weapon_count += 1
            weapon_usage += usage

        if False and usage < 0.05:
            details.append({
                "Pitch": pitch,
                "Usage": round(usage * 100, 1),
                "Pitcher K": round(p_k * 100, 1),
                "Opponent K": round(opp_k * 100, 1),
                "League K": round(league_k * 100, 1),
                "K Edge": round(k_edge * 100, 1),
                "Pitcher Whiff": round(p_whiff * 100, 1),
                "Opponent Whiff": round(opp_whiff * 100, 1),
                "League Whiff": round(league_whiff * 100, 1),
                "Whiff Edge": round(whiff_edge * 100, 1),
                "Pitcher Put Away": round(p_putaway * 100, 1),
                "Opponent Put Away": round(opp_putaway * 100, 1),
                "League Put Away": round(league_putaway * 100, 1),
                "Put Away Edge": round(putaway_edge * 100, 1),
                "Combined Edge": round(combined_edge * 100, 1),
                "Opp Weakness": round(opp_weakness * 100, 1),
                "Pitch Quality": round(pitch_quality * 100, 1),
                "Weapon": weapon_label,
                "Extreme": "Below scoring floor" if is_weapon else "",
                "Confluence": "",
                "Contribution": "",
                "Match Status": "Matched - below 5% scoring floor",
            })
            continue

        confluence = (
            (pitch_quality > 0 and opp_weakness > 0) or
            (pitch_quality < 0 and opp_weakness < 0)
        )

        extreme_tags = []
        multiplier = 1.00
        if confluence:
            multiplier *= 1.15
        if combined_edge >= 0.10 and usage >= 0.09:
            multiplier *= 1.15
            extreme_tags.append("Strong Weapon")
        if combined_edge <= -0.10 and usage >= 0.09:
            multiplier *= 1.15
            extreme_tags.append("Major Negative")
        if pitcher_k_delta >= 0.10 and usage >= 0.09:
            extreme_tags.append("Elite Pitcher K")
        if opponent_k_delta >= 0.06 and usage >= 0.09:
            extreme_tags.append("Opponent K Weakness")
        if opponent_k_delta <= -0.06 and usage >= 0.09:
            extreme_tags.append("Opponent Handles Pitch")

        contribution = usage * combined_edge * multiplier
        score += contribution
        scored_usage += usage
        scored_count += 1

        details.append({
            "Pitch": pitch,
            "Usage": round(usage * 100, 1),
            "Pitcher K": round(p_k * 100, 1),
            "Opponent K": round(opp_k * 100, 1),
            "League K": round(league_k * 100, 1),
            "K Edge": round(k_edge * 100, 1),
            "Pitcher Whiff": round(p_whiff * 100, 1),
            "Opponent Whiff": round(opp_whiff * 100, 1),
            "League Whiff": round(league_whiff * 100, 1),
            "Whiff Edge": round(whiff_edge * 100, 1),
            "Pitcher Put Away": round(p_putaway * 100, 1),
            "Opponent Put Away": round(opp_putaway * 100, 1),
            "League Put Away": round(league_putaway * 100, 1),
            "Put Away Edge": round(putaway_edge * 100, 1),
            "Combined Edge": round(combined_edge * 100, 1),
            "Opp Weakness": round(opp_weakness * 100, 1),
            "Pitch Quality": round(pitch_quality * 100, 1),
            "Weapon": weapon_label,
            "Extreme": ", ".join(extreme_tags),
            "Confluence": "Yes" if confluence else "No",
            "Contribution": round(contribution * 100, 2),
            "Match Status": "Matched and scored",
        })

    if not details or scored_usage <= 0:
        detail_df = pd.DataFrame(details, columns=empty_cols) if details else pd.DataFrame(columns=empty_cols)
        return {
            "modifier": 0.0,
            "score": 0.0,
            "weapon_count": weapon_count,
            "weapon_bonus": 0.0,
            "weapon_usage": round(weapon_usage * 100, 1),
            "scored_count": 0,
            "status": "Neutral fallback - no matching pitch types above usage floor",
            "details": detail_df,
        }

    # Normalized score is displayed as percentage points. +8.0 means the weighted pitch mix is +8 points.
    normalized_score = max(-0.18, min(0.18, score / max(0.25, scored_usage)))

    # Projection impact: arsenal is now a main driver, but still capped to avoid one data table dominating everything.
    # Rough map before weapon handling: +5 edge ≈ +0.35 K, +10 edge ≈ +0.70 K, extreme +15 edge ≈ +1.05 K.
    base_modifier = normalized_score * 7.0

    # True weapons should be more than a display label. These are now hard projection floors,
    # not tiny tiebreaker bonuses. A pitcher with 2-3 real weapons against the confirmed lineup
    # should be pushed toward over consideration unless another major factor disagrees.
    weapon_bonus = 0.0
    weapon_modifier_floor = None
    if weapon_count >= 3 and weapon_usage >= 0.27:
        weapon_bonus = 1.60
        weapon_modifier_floor = 1.60
    elif weapon_count == 2 and weapon_usage >= 0.20:
        weapon_bonus = 0.95
        weapon_modifier_floor = 0.95
    elif weapon_count == 1 and weapon_usage >= 0.15:
        weapon_bonus = 0.35
        weapon_modifier_floor = 0.35

    # Weapon protection / floor:
    # If a pitcher has 2+ true weapons, the arsenal layer should not be allowed
    # to create a negative K modifier. Multiple real weapons make unders risky,
    # even if secondary pitches grade poorly.
    if weapon_count >= 2 and weapon_usage >= 0.20:
        base_modifier = max(base_modifier, 0.0)

    negative_profile_penalty = 0.0
    if weapon_count == 0 and normalized_score <= -0.05:
        negative_profile_penalty -= 0.15
    if weapon_count == 0 and normalized_score <= -0.10:
        negative_profile_penalty -= 0.15

    raw_modifier = base_modifier + weapon_bonus + negative_profile_penalty
    if weapon_modifier_floor is not None:
        raw_modifier = max(raw_modifier, weapon_modifier_floor)

    # Do not cap strong weapon matchups too tightly. We still keep a broad safety cap,
    # but 3+ true weapons should be allowed to fully express a large K-ceiling boost.
    if weapon_count >= 3 and weapon_usage >= 0.27:
        positive_cap = 4.00
    elif weapon_count == 2 and weapon_usage >= 0.20:
        positive_cap = 3.00
    else:
        positive_cap = 2.25
    modifier = max(-1.35, min(positive_cap, raw_modifier))

    detail_df = pd.DataFrame(details)
    if "Contribution" in detail_df.columns:
        detail_df["_sort_contribution"] = pd.to_numeric(detail_df["Contribution"], errors="coerce").fillna(-999)
        detail_df = detail_df.sort_values(["_sort_contribution", "Usage"], ascending=[False, False]).drop(columns=["_sort_contribution"]).reset_index(drop=True)

    coverage = round(min(100.0, matched_usage * 100), 1)
    return {
        "modifier": round(modifier, 2),
        "score": round(normalized_score * 100, 1),
        "weapon_count": int(weapon_count),
        "weapon_bonus": round(weapon_bonus, 2),
        "weapon_usage": round(weapon_usage * 100, 1),
        "scored_count": int(scored_count),
        "status": f"Pitch-type arsenal matched - {scored_count} scored pitches, {coverage}% arsenal usage matched, {weapon_count} true weapons (>=9% usage, +5 combined edge; sub-5% pitches ignored)",
        "details": detail_df,
    }

def apply_pitch_type_modifier(base_projection, pitcher, opponent, pitcher_arsenal_df=None, team_pitch_type_df=None):
    adj = pitch_type_arsenal_adjustment(pitcher, opponent, pitcher_arsenal_df, team_pitch_type_df)
    return max(0, base_projection + adj["modifier"]), adj

@st.cache_data(ttl=60 * 60)

def _clamp(value, low, high):
    try:
        value = float(value)
    except Exception:
        value = 0.0
    return max(low, min(high, value))


def _first_existing_col(df, candidates):
    if df is None or df.empty:
        return None
    lookup = {_clean_col_name(c).lower(): c for c in df.columns}
    for cand in candidates:
        key = cand.lower()
        if key in lookup:
            return lookup[key]
    for col in df.columns:
        low = _clean_col_name(col).lower()
        if any(cand.lower() in low for cand in candidates):
            return col
    return None


def _row_for_team(df, team):
    if df is None or df.empty or "Teams" not in df.columns:
        return None
    temp = df.copy()
    temp["_team_key"] = temp["Teams"].apply(normalize_name_for_match)
    keys = _team_keys(team)
    rows = temp[temp["_team_key"].isin(keys)]
    if rows.empty:
        rows = temp[temp["Teams"].astype(str).str.strip() == str(team).strip()]
    if rows.empty:
        return None
    return rows.iloc[0]


def _pitcher_row(df, pitcher):
    if df is None or df.empty or "Player" not in df.columns:
        return None
    keys = _name_keys(pitcher)
    temp = df.copy()
    temp["_name_key"] = temp["Player"].apply(normalize_name_for_match)
    rows = temp[temp["_name_key"].isin(keys)]
    if rows.empty:
        return None
    return rows.iloc[0]


def extreme_opponent_k_modifier(opponent, throws_hand, team_batting_rhp, team_batting_lhp):
    """Extra matchup layer for extreme team strikeout environments.

    The original opponent multiplier intentionally blends split K% back toward
    overall K%, which keeps normal matchups stable but can under-credit truly
    favorable K opponents. This small layer only moves projections when the
    opponent split K% is meaningfully high or low.
    """
    split_df = team_batting_lhp if throws_hand == "L" else team_batting_rhp
    row = _row_for_team(split_df, opponent)
    if row is None:
        return {"name": "Extreme Opponent K", "pct": 0.0, "label": "Neutral - team split row not matched"}

    ab = _to_number(row.get("At Bats", 0), 0)
    so = _to_number(row.get("Strikeouts", 0), 0)
    if ab <= 0 or so <= 0:
        return {"name": "Extreme Opponent K", "pct": 0.0, "label": "Neutral - missing split AB/SO"}

    k_rate = so / ab

    # This layer now has two pieces:
    # 1) a normal percent multiplier, and
    # 2) a direct K bucket bonus/penalty so extreme matchups can actually move
    #    a 4.5/5.5 line enough to matter.
    if k_rate >= 0.285:
        pct = 0.085
        direct_k = 0.65
        score_boost = 10.0
        tier = "Elite K target"
    elif k_rate >= 0.270:
        pct = 0.070
        direct_k = 0.50
        score_boost = 8.0
        tier = "Strong K target"
    elif k_rate >= 0.255:
        pct = 0.055
        direct_k = 0.35
        score_boost = 6.0
        tier = "Good K target"
    elif k_rate >= 0.240:
        pct = 0.035
        direct_k = 0.20
        score_boost = 3.0
        tier = "Slight K target"
    elif k_rate <= 0.175:
        pct = -0.085
        direct_k = -0.65
        score_boost = -10.0
        tier = "Extreme low-K opponent"
    elif k_rate <= 0.190:
        pct = -0.070
        direct_k = -0.50
        score_boost = -8.0
        tier = "Low-K opponent"
    elif k_rate <= 0.205:
        pct = -0.050
        direct_k = -0.35
        score_boost = -6.0
        tier = "Strong contact opponent"
    elif k_rate <= 0.215:
        pct = -0.030
        direct_k = -0.20
        score_boost = -3.0
        tier = "Slight low-K opponent"
    else:
        pct = 0.0
        direct_k = 0.0
        score_boost = 0.0
        tier = "Neutral K range"

    hand_label = "vs LHP" if throws_hand == "L" else "vs RHP"
    return {
        "name": "Extreme Opponent K",
        "pct": _clamp(pct, -0.085, 0.085),
        "direct_k": _clamp(direct_k, -EXTREME_MATCHUP_K_BONUS_CAP, EXTREME_MATCHUP_K_BONUS_CAP),
        "score_boost": score_boost,
        "label": f"{tier}: {k_rate * 100:.1f}% K {hand_label}",
    }


def opponent_contact_modifier(opponent, throws_hand, team_batting_rhp, team_batting_lhp):
    """Small opponent contact layer.

    Uses true contact/swinging-strike columns if your workbook has them.
    If not, it falls back to the already-loaded handedness split K rate.
    """
    split_df = team_batting_lhp if throws_hand == "L" else team_batting_rhp
    row = _row_for_team(split_df, opponent)
    if row is None:
        return {"name": "Opponent Contact", "pct": 0.0, "label": "Neutral - team contact row not matched"}

    contact_col = _first_existing_col(split_df, ["Contact %", "Contact%", "contact_percent"])
    zone_contact_col = _first_existing_col(split_df, ["Zone Contact %", "Z-Contact%", "Zone Contact", "z_contact"])
    chase_contact_col = _first_existing_col(split_df, ["Chase Contact %", "O-Contact%", "Chase Contact", "o_contact"])
    swstr_col = _first_existing_col(split_df, ["SwStr %", "Swinging Strike %", "Whiff %", "Whiff%"])
    foul_col = _first_existing_col(split_df, ["Foul %", "Foul", "Foul Ball %"])

    pieces = []
    labels = []
    if contact_col:
        contact = _to_rate(row.get(contact_col, 0.76), 0.76)
        pieces.append((0.76 - contact) / 0.10 * 0.035)
        labels.append(f"Contact {contact * 100:.1f}%")
    if zone_contact_col:
        zone_contact = _to_rate(row.get(zone_contact_col, 0.84), 0.84)
        pieces.append((0.84 - zone_contact) / 0.10 * 0.025)
        labels.append(f"Zone contact {zone_contact * 100:.1f}%")
    if chase_contact_col:
        chase_contact = _to_rate(row.get(chase_contact_col, 0.60), 0.60)
        pieces.append((0.60 - chase_contact) / 0.10 * 0.020)
        labels.append(f"Chase contact {chase_contact * 100:.1f}%")
    if swstr_col:
        swstr = _to_rate(row.get(swstr_col, 0.115), 0.115)
        pieces.append((swstr - 0.115) / 0.05 * 0.030)
        labels.append(f"SwStr/Whiff {swstr * 100:.1f}%")
    if foul_col:
        foul = _to_rate(row.get(foul_col, 0.27), 0.27)
        pieces.append((0.27 - foul) / 0.08 * 0.015)
        labels.append(f"Foul {foul * 100:.1f}%")

    if pieces:
        pct = _clamp(sum(pieces), -0.07, 0.07)
        status = ", ".join(labels)
    else:
        ab = _to_number(row.get("At Bats", 0), 0)
        so = _to_number(row.get("Strikeouts", 0), 0)
        k_rate = so / ab if ab > 0 else 0.22
        pct = _clamp((k_rate - 0.22) * 0.55, -0.055, 0.055)
        status = f"Fallback from split K% {k_rate * 100:.1f}%"

    return {"name": "Opponent Contact", "pct": pct, "label": status}


def recent_skill_modifier(pitcher, pitcher_this_year, pitcher_last_year):
    """Small recent/skill trend layer using only real populated pitcher skill fields.

    In the live Stats API/Savant fallback build, some advanced columns can exist but
    be populated as 0.0 because that source did not return the stat. Those zeros are
    missing data, not actual terrible whiff/chase/zone skills. This version ignores
    zero/placeholder advanced fields and uses a smaller K%-only adjustment when K%
    is the only reliable input available.
    """
    this_row = _pitcher_row(pitcher_this_year, pitcher)
    last_row = _pitcher_row(pitcher_last_year, pitcher)
    if this_row is None:
        return {"name": "Recent Skill", "pct": 0.0, "label": "Neutral - no current pitcher row"}

    this_ip = _to_number(this_row.get("IP", 0), 0)
    sample_scale = _clamp(this_ip / 35.0, 0.20, 1.0)

    def read_rate_from_row(row, source_df, aliases, default=None):
        if row is None or source_df is None or source_df.empty:
            return default
        col = _first_existing_col(source_df, aliases)
        if not col:
            return default
        raw = row.get(col, default)
        if raw is None:
            return default
        try:
            if pd.isna(raw):
                return default
        except Exception:
            pass
        return _to_rate(raw, default if default is not None else 0.0)

    pieces = []
    labels = []
    reliable_inputs = 0

    # K% is currently the most reliable live input. Keep it meaningful but do not
    # let K% alone create a huge recent-skill penalty/boost.
    cur_k = read_rate_from_row(this_row, pitcher_this_year, ["K%", "SO%", "Strikeout %"], None)
    prev_k = read_rate_from_row(last_row, pitcher_last_year, ["K%", "SO%", "Strikeout %"], 0.225)
    if cur_k is not None and 0.05 <= cur_k <= 0.45:
        edge = ((cur_k - 0.225) * 0.70) + ((cur_k - prev_k) * 0.30)
        pieces.append((edge / 0.070) * 0.022)
        labels.append(f"K% {cur_k * 100:.1f}%")
        reliable_inputs += 1

    cur_bb = read_rate_from_row(this_row, pitcher_this_year, ["BB%", "Walk %", "Base on Balls %"], None)
    prev_bb = read_rate_from_row(last_row, pitcher_last_year, ["BB%", "Walk %", "Base on Balls %"], 0.085)
    if cur_bb is not None and 0.01 <= cur_bb <= 0.20:
        edge = ((0.085 - cur_bb) * 0.70) + ((prev_bb - cur_bb) * 0.30)
        pieces.append((edge / 0.050) * 0.008)
        labels.append(f"BB% {cur_bb * 100:.1f}%")
        reliable_inputs += 1

    # xwOBA is ignored when it is just the neutral fallback (.320) or missing/zero.
    cur_xwoba = read_rate_from_row(this_row, pitcher_this_year, ["xwOBA", "xwoba"], None)
    prev_xwoba = read_rate_from_row(last_row, pitcher_last_year, ["xwOBA", "xwoba"], 0.320)
    if cur_xwoba is not None and 0.200 <= cur_xwoba <= 0.450 and abs(cur_xwoba - 0.320) > 0.006:
        edge = ((0.320 - cur_xwoba) * 0.70) + ((prev_xwoba - cur_xwoba) * 0.30)
        pieces.append((edge / 0.060) * 0.012)
        labels.append(f"xwOBA {cur_xwoba:.3f}")
        reliable_inputs += 1

    # Advanced columns count only when they are plausible, non-zero, non-placeholder values.
    advanced_specs = [
        (["Whiff %", "Whiff%", "Whiff"], 0.245, 0.060, 0.014, "Whiff", 0.10, 0.45),
        (["First Strike %", "First Strike%", "F-Strike%"], 0.610, 0.070, 0.007, "First strike", 0.40, 0.80),
        (["Out of Zone %", "O-Zone%", "Chase %"], 0.300, 0.060, 0.006, "Chase/O-zone", 0.15, 0.50),
        (["In Zone %", "Zone %", "Zone%"], 0.485, 0.070, 0.004, "Zone", 0.30, 0.65),
    ]
    skipped_missing = []
    for aliases, baseline, unit, weight, label, min_valid, max_valid in advanced_specs:
        col = _first_existing_col(pitcher_this_year, aliases)
        if not col:
            skipped_missing.append(label)
            continue
        raw = this_row.get(col, None)
        cur = _to_rate(raw, None)
        # Values like 0, 0.0, blank, or outside the valid range are treated as missing.
        if cur is None or cur <= 0 or not (min_valid <= cur <= max_valid):
            skipped_missing.append(label)
            continue
        last_col = _first_existing_col(pitcher_last_year, aliases) if pitcher_last_year is not None and not pitcher_last_year.empty else None
        prev = _to_rate(last_row.get(last_col, baseline), baseline) if last_row is not None and last_col else baseline
        edge = ((cur - baseline) * 0.60) + ((cur - prev) * 0.40)
        pieces.append((edge / unit) * weight)
        labels.append(f"{label} {cur * 100:.1f}%")
        reliable_inputs += 1

    if not pieces:
        return {"name": "Recent Skill", "pct": 0.0, "label": "Neutral - no reliable recent skill columns available"}

    raw_pct = sum(pieces) * sample_scale

    # If only K% is usable, keep the recent-skill layer small. This prevents a
    # pitcher from showing something like -8% just because whiff/zone fields are blank.
    if reliable_inputs <= 1 and labels and labels[0].startswith("K%"):
        pct = _clamp(raw_pct, -0.025, 0.025)
        status = ", ".join(labels) + " | K%-only; advanced fields missing ignored"
    else:
        pct = _clamp(raw_pct, -0.040, 0.040)
        status = ", ".join(labels)
        if skipped_missing:
            status += " | ignored missing: " + ", ".join(skipped_missing[:4])

    return {"name": "Recent Skill", "pct": pct, "label": status}

def home_away_modifier(pitcher, pitcher_this_year, pitcher_last_year, game_location="neutral"):
    """Small home/away split layer. Neutral unless the workbook has home/away K split columns."""
    if game_location not in ["home", "away"]:
        return {"name": "Home/Away", "pct": 0.0, "label": "Neutral - location not set"}

    row = _pitcher_row(pitcher_this_year, pitcher)
    source_df = pitcher_this_year
    if row is None:
        row = _pitcher_row(pitcher_last_year, pitcher)
        source_df = pitcher_last_year
    if row is None or source_df is None or source_df.empty:
        return {"name": "Home/Away", "pct": 0.0, "label": "Neutral - no pitcher row"}

    loc_prefixes = ["Home"] if game_location == "home" else ["Away", "Road"]
    opp_prefixes = ["Away", "Road"] if game_location == "home" else ["Home"]
    loc_kip_col = _first_existing_col(source_df, [f"{p} K/IP" for p in loc_prefixes] + [f"{p} SO/IP" for p in loc_prefixes])
    opp_kip_col = _first_existing_col(source_df, [f"{p} K/IP" for p in opp_prefixes] + [f"{p} SO/IP" for p in opp_prefixes])
    loc_kpct_col = _first_existing_col(source_df, [f"{p} K%" for p in loc_prefixes])
    opp_kpct_col = _first_existing_col(source_df, [f"{p} K%" for p in opp_prefixes])

    pieces = []
    labels = []
    if loc_kip_col and opp_kip_col:
        loc = _to_number(row.get(loc_kip_col, 0), 0)
        opp = _to_number(row.get(opp_kip_col, 0), 0)
        if loc > 0 and opp > 0:
            pieces.append((loc - opp) / 0.45 * 0.035)
            labels.append(f"{game_location.title()} K/IP {loc:.2f} vs split {opp:.2f}")
    if loc_kpct_col and opp_kpct_col:
        loc = _to_rate(row.get(loc_kpct_col, 0), 0)
        opp = _to_rate(row.get(opp_kpct_col, 0), 0)
        if loc > 0 and opp > 0:
            pieces.append((loc - opp) / 0.08 * 0.030)
            labels.append(f"{game_location.title()} K% {loc * 100:.1f}% vs split {opp * 100:.1f}%")

    if not pieces:
        return {"name": "Home/Away", "pct": 0.0, "label": "Neutral - no home/away split columns"}

    pct = _clamp(sum(pieces), -0.05, 0.05)
    return {"name": "Home/Away", "pct": pct, "label": "; ".join(labels)}


def umpire_modifier(umpire_context=None):
    """Optional game-day umpire layer with neutral fallback."""
    umpire_context = umpire_context or {}
    zone = str(umpire_context.get("zone", "Neutral")).strip().lower()
    called_strike = _to_rate(umpire_context.get("called_strike_pct", 0), 0)
    k_boost = 0.0
    label = "Neutral / not available"

    if called_strike > 0:
        k_boost += (called_strike - 0.165) / 0.025 * 0.035
        label = f"Called strike {called_strike * 100:.1f}%"
    elif "wide" in zone or "pitcher" in zone:
        k_boost += 0.035
        label = "Manual: wide / pitcher-friendly zone"
    elif "tight" in zone or "hitter" in zone:
        k_boost -= 0.035
        label = "Manual: tight / hitter-friendly zone"

    return {"name": "Umpire", "pct": _clamp(k_boost, -0.06, 0.06), "label": label}


def apply_small_k_modifiers(base_projection, pitcher, opponent, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, throws_hand="R", game_location="neutral", umpire_context=None, impact_scale=1.0):
    modifiers = [
        extreme_opponent_k_modifier(opponent, throws_hand, team_batting_rhp, team_batting_lhp),
        opponent_contact_modifier(opponent, throws_hand, team_batting_rhp, team_batting_lhp),
        recent_skill_modifier(pitcher, pitcher_this_year, pitcher_last_year),
        home_away_modifier(pitcher, pitcher_this_year, pitcher_last_year, game_location),
        umpire_modifier(umpire_context),
    ]

    raw_total = sum(m.get("pct", 0.0) for m in modifiers) * impact_scale
    capped_total = _clamp(raw_total, -SMALL_K_MODIFIER_CAP, SMALL_K_MODIFIER_CAP)

    raw_direct_k = sum(m.get("direct_k", 0.0) for m in modifiers) * impact_scale
    capped_direct_k = _clamp(raw_direct_k, -EXTREME_MATCHUP_K_BONUS_CAP, EXTREME_MATCHUP_K_BONUS_CAP)

    # If the opponent is an extreme K target and other matchup signals agree, add a
    # small confirmation bump. This is what helps separate a normal edge from a
    # true mismatch without making every high-K team an automatic over.
    extreme_direct = next((m.get("direct_k", 0.0) for m in modifiers if m.get("name") == "Extreme Opponent K"), 0.0)
    contact_pct = next((m.get("pct", 0.0) for m in modifiers if m.get("name") == "Opponent Contact"), 0.0)
    recent_pct = next((m.get("pct", 0.0) for m in modifiers if m.get("name") == "Recent Skill"), 0.0)
    confirmation_k = 0.0
    confirmation_score = 0.0
    if extreme_direct >= 0.35:
        if contact_pct >= 0.025:
            confirmation_k += 0.07
            confirmation_score += 0.8
        if recent_pct >= 0.025:
            confirmation_k += 0.05
            confirmation_score += 0.6
    elif extreme_direct <= -0.35:
        if contact_pct <= -0.025:
            confirmation_k -= 0.08
            confirmation_score -= 0.9
        if recent_pct <= -0.025:
            confirmation_k -= 0.06
            confirmation_score -= 0.7
    confirmation_k *= impact_scale
    confirmation_score *= impact_scale

    pct_adjusted_projection = base_projection * (1 + capped_total)
    total_direct_k = _clamp(capped_direct_k + confirmation_k, -EXTREME_MATCHUP_K_BONUS_CAP, EXTREME_MATCHUP_K_BONUS_CAP)
    raw_projection = pct_adjusted_projection + total_direct_k

    # Keep the new layer powerful enough to matter, but cap the total non-arsenal
    # change so one matchup cannot create an unrealistic projection.
    max_up = base_projection + TOTAL_SMALL_K_MOVEMENT_CAP
    max_down = max(0, base_projection - TOTAL_SMALL_K_MOVEMENT_CAP)
    final_projection = _clamp(raw_projection, max_down, max_up)

    # Anti-over-bias calibration:
    # new contextual layers can stack in the same direction, so positive K movement is
    # regressed slightly more than negative movement. This keeps extreme Over spots
    # alive but prevents normal high-K teams from becoming automatic Overs.
    final_k_movement = final_projection - base_projection
    if final_k_movement > 0:
        final_projection = base_projection + (final_k_movement * 0.72)
    elif final_k_movement < 0:
        final_projection = base_projection + (final_k_movement * 0.90)
    final_k_movement = final_projection - base_projection

    raw_score_boost = (sum(m.get("score_boost", 0.0) for m in modifiers) * impact_scale) + confirmation_score
    score_boost = _clamp(raw_score_boost, -MATCHUP_SCORE_BOOST_CAP, MATCHUP_SCORE_BOOST_CAP)

    detail_rows = []
    for m in modifiers:
        pct = float(m.get("pct", 0.0)) * impact_scale
        direct_k = float(m.get("direct_k", 0.0)) * impact_scale
        score_piece = float(m.get("score_boost", 0.0)) * impact_scale
        detail_rows.append({
            "Modifier": m.get("name", ""),
            "Impact %": round(pct * 100, 1),
            "Direct K": round(direct_k, 2),
            "Score Boost": round(score_piece, 1),
            "Status / Inputs": m.get("label", ""),
        })

    if abs(confirmation_k) > 0 or abs(confirmation_score) > 0:
        detail_rows.append({
            "Modifier": "Extreme Matchup Confirmation",
            "Impact %": 0.0,
            "Direct K": round(confirmation_k, 2),
            "Score Boost": round(confirmation_score, 1),
            "Status / Inputs": "Opponent K + contact/recent signals agree",
        })

    detail_rows.append({
        "Modifier": "Total K Modifier Movement",
        "Impact %": round(capped_total * 100, 1),
        "Direct K": round(total_direct_k, 2),
        "Score Boost": round(score_boost, 1),
        "Status / Inputs": f"Final movement {final_k_movement:+.2f} Ks | pct cap +/-{SMALL_K_MODIFIER_CAP * 100:.0f}% | total cap +/-{TOTAL_SMALL_K_MOVEMENT_CAP:.2f} K",
    })

    return final_projection, {
        "raw_total_pct": raw_total,
        "capped_total_pct": capped_total,
        "raw_direct_k": raw_direct_k,
        "capped_direct_k": capped_direct_k,
        "confirmation_k": confirmation_k,
        "total_direct_k": total_direct_k,
        "final_k_movement": final_k_movement,
        "score_boost": score_boost,
        "details": pd.DataFrame(detail_rows),
    }

@st.cache_data(ttl=60 * 60)
# Older HTML-table live loaders removed. Final Stats API loaders are defined below.

@st.cache_data(ttl=15 * 60)
def _legacy_pull_today_mlb_games_v1(game_date=None):
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
            game_number = game.get("gameNumber", 1)
            double_header = str(game.get("doubleHeader", "N")).upper()
            away_team_name = away.get("team", {}).get("name", "")
            home_team_name = home.get("team", {}).get("name", "")
            base_label = f"{away_team_name} at {home_team_name}"
            game_label = f"{base_label} - Game {game_number}" if double_header in ["Y", "S"] or int(game_number or 1) > 1 else base_label
            games.append({
                "game_pk": game.get("gamePk"),
                "game_key": str(game.get("gamePk", "")),
                "game_number": game_number,
                "double_header": double_header,
                "game_label": game_label,
                "game_time": game.get("gameDate"),
                "away_team": away_team_name,
                "home_team": home_team_name,
                # MLB API returns probable pitchers as First Last; your Excel tables use Last, First.
                "away_pitcher": to_last_first(away.get("probablePitcher", {}).get("fullName", "")),
                "home_pitcher": to_last_first(home.get("probablePitcher", {}).get("fullName", "")),
                "venue_name": (game.get("venue", {}) or {}).get("name", ""),
                "status": game.get("status", {}).get("detailedState", "")
            })
    return pd.DataFrame(games)




# -----------------------
# MLB CONFIRMED LINEUP K BLEND HELPERS
# -----------------------

@st.cache_data(ttl=5 * 60, show_spinner=False)
def fetch_mlb_confirmed_lineup(game_pk, side):
    """Return confirmed batting order from MLB boxscore for side='home' or 'away'."""
    try:
        if not game_pk:
            return []
        url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
        data = requests.get(url, timeout=12).json()
        team = data.get("teams", {}).get(side, {})
        players = team.get("players", {})
        lineup = []
        for _, player_block in players.items():
            order = str(player_block.get("battingOrder", "")).strip()
            if not order:
                continue
            person = player_block.get("person", {}) or {}
            try:
                sort_order = int(order)
            except Exception:
                sort_order = 999
            bat_side = (player_block.get("batSide", {}) or {}).get("code", "")
            if not bat_side:
                bat_side = (player_block.get("batSide", {}) or {}).get("description", "")
            lineup.append({
                "order": sort_order,
                "player_id": person.get("id"),
                "player": person.get("fullName", ""),
                "bat_side": str(bat_side).upper()[:1],
            })
        lineup = sorted(lineup, key=lambda x: x.get("order", 999))
        # MLB battingOrder is usually 100, 200, 300... Keep the first 9 hitters.
        return lineup[:9]
    except Exception:
        return []


@st.cache_data(ttl=60 * 60, show_spinner=False)
def fetch_mlb_player_k_profile(player_id, season=MLB_SEASON):
    """Return hitter season K profile from MLB Stats API.

    The AB/PA sample is used to decide whether the model should trust
    hitter-specific pitch-type data. Low-sample hitters fall back to the
    opponent team pitch-type numbers so one call-up or bench bat does not
    distort the arsenal matchup.
    """
    try:
        if not player_id:
            return {"k_rate": None, "pa": 0, "ab": 0, "so": 0}
        url = f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats"
        params = {"stats": "season", "group": "hitting", "season": season}
        data = requests.get(url, params=params, timeout=12).json()
        splits = data.get("stats", [{}])[0].get("splits", [])
        if not splits:
            return {"k_rate": None, "pa": 0, "ab": 0, "so": 0}
        stat = splits[0].get("stat", {}) or {}
        so = float(stat.get("strikeOuts", 0) or 0)
        pa = float(stat.get("plateAppearances", 0) or 0)
        ab = float(stat.get("atBats", 0) or 0)
        denom = pa if pa > 0 else ab
        k_rate = so / denom if denom > 0 else None
        if k_rate is not None and (k_rate <= 0 or k_rate > 0.60):
            k_rate = None
        return {"k_rate": k_rate, "pa": int(pa), "ab": int(ab), "so": int(so)}
    except Exception:
        return {"k_rate": None, "pa": 0, "ab": 0, "so": 0}


@st.cache_data(ttl=60 * 60, show_spinner=False)
def fetch_mlb_player_k_rate(player_id, season=MLB_SEASON):
    """Return hitter strikeout rate from MLB Stats API season hitting stats."""
    return fetch_mlb_player_k_profile(player_id, season).get("k_rate")



@st.cache_data(ttl=60 * 60, show_spinner=False)
def fetch_mlb_player_batting_profile(player_id, season=MLB_SEASON):
    """Return basic hitter profile from MLB Stats API season hitting stats.

    This lets the moneyline model compare today's confirmed lineup to the team's
    normal baseline without adding another paid data source. It is intentionally
    simple and safely returns None when the MLB API has no season row yet.
    """
    try:
        if not player_id:
            return None
        url = f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats"
        params = {"stats": "season", "group": "hitting", "season": season}
        data = requests.get(url, params=params, timeout=12).json()
        splits = data.get("stats", [{}])[0].get("splits", [])
        if not splits:
            return None
        stat = splits[0].get("stat", {}) or {}
        pa = _to_number(stat.get("plateAppearances", 0), 0)
        ab = _to_number(stat.get("atBats", 0), 0)
        denom = pa if pa > 0 else ab
        if denom <= 0:
            return None

        obp = _to_rate(stat.get("obp", 0), 0.0)
        slg = _to_rate(stat.get("slg", 0), 0.0)
        ops = _to_rate(stat.get("ops", 0), 0.0)
        avg = _to_rate(stat.get("avg", 0), 0.0)
        so = _to_number(stat.get("strikeOuts", 0), 0)
        k_rate = so / denom if denom > 0 else None

        # Keep tiny-sample bench players from acting like elite/awful hitters.
        sample_weight = max(0.25, min(1.0, denom / 120.0))
        obp = (sample_weight * obp) + ((1 - sample_weight) * 0.315) if obp > 0 else 0.315
        slg = (sample_weight * slg) + ((1 - sample_weight) * 0.410) if slg > 0 else 0.410
        avg = (sample_weight * avg) + ((1 - sample_weight) * 0.250) if avg > 0 else 0.250
        ops = obp + slg if ops <= 0 else ((sample_weight * ops) + ((1 - sample_weight) * 0.725))

        return {
            "pa": int(pa),
            "ab": int(ab),
            "sample_size": int(denom),
            "obp": max(0.240, min(0.430, obp)),
            "slg": max(0.280, min(0.650, slg)),
            "avg": max(0.160, min(0.360, avg)),
            "ops": max(0.520, min(1.050, ops)),
            "k_rate": max(0.06, min(0.45, k_rate)) if k_rate is not None else None,
        }
    except Exception:
        return None


def _team_baseline_k_rate_for_pitcher_hand(opponent, pitcher_hand, team_batting_rhp, team_batting_lhp):
    """Mirror the baseline opponent K logic used inside the pitcher K model."""
    ab_l = get_value(team_batting_lhp, "Teams", opponent, "At Bats", 0)
    ab_r = get_value(team_batting_rhp, "Teams", opponent, "At Bats", 0)
    so_l = get_value(team_batting_lhp, "Teams", opponent, "Strikeouts", 0)
    so_r = get_value(team_batting_rhp, "Teams", opponent, "Strikeouts", 0)

    if str(pitcher_hand).upper().startswith("L"):
        opp_ab_split = ab_l
        opp_so_split = so_l
    else:
        opp_ab_split = ab_r
        opp_so_split = so_r

    opp_ab_total = ab_l + ab_r
    opp_so_total = so_l + so_r
    opp_k_overall = opp_so_total / opp_ab_total if opp_ab_total > 0 else 0.22
    opp_k_split_raw = opp_so_split / opp_ab_split if opp_ab_split > 0 else opp_k_overall
    sample_weight = min(0.65, opp_ab_split / 700) if opp_ab_split > 0 else 0
    opp_k_split = ((1 - sample_weight) * opp_k_overall) + (sample_weight * opp_k_split_raw)
    return max(0.12, min(0.38, opp_k_split))


def _lineup_handedness_stack_multiplier(lineup, pitcher_hand):
    """Small K adjustment from lineup handedness stack vs pitcher handedness.

    Same-side hitters generally improve a pitcher's K environment; opposite-side
    stacks usually reduce it. Switch hitters are treated as batting opposite the
    pitcher. This layer is intentionally capped so raw hitter K% remains the main
    lineup signal.
    """
    pitcher_hand = str(pitcher_hand or "R").upper()[:1]
    counts = {"L": 0, "R": 0, "S": 0, "Unknown": 0}
    same_side = 0
    opposite_side = 0
    known = 0

    for hitter in lineup or []:
        side = str(hitter.get("bat_side", "") or "").upper()[:1]
        if side not in ["L", "R", "S"]:
            counts["Unknown"] += 1
            continue
        counts[side] += 1
        known += 1
        if side == "S":
            # Switch hitters usually choose the platoon advantage.
            opposite_side += 1
        elif side == pitcher_hand:
            same_side += 1
        else:
            opposite_side += 1

    if known < 6:
        return {
            "multiplier": 1.0,
            "same_side_pct": None,
            "same_side": same_side,
            "opposite_side": opposite_side,
            "known_bat_sides": known,
            "counts": counts,
            "status": "Not enough confirmed batting-side data for handedness stack adjustment.",
        }

    same_side_pct = same_side / known
    # Neutral is around half same-handed hitters. Each 10 percentage points away
    # from neutral is worth roughly 1% K movement, capped at +/-4%.
    raw_adj = (same_side_pct - 0.50) * 0.10
    capped_adj = max(-0.04, min(0.04, raw_adj))
    mult = 1.0 + capped_adj

    if capped_adj > 0.005:
        status = "Same-side stack boosts pitcher K environment."
    elif capped_adj < -0.005:
        status = "Opposite-side/switch stack reduces pitcher K environment."
    else:
        status = "Balanced handedness stack; neutral K adjustment."

    return {
        "multiplier": mult,
        "same_side_pct": same_side_pct,
        "same_side": same_side,
        "opposite_side": opposite_side,
        "known_bat_sides": known,
        "counts": counts,
        "status": status,
    }



def _hitter_pitch_type_rows(hitter_name, team_pitch_type_df):
    """Return Savant batter pitch-type rows for one confirmed lineup hitter.

    This powers the lineup-specific pitch-type matchup layer. If Savant only
    provides team rows or the hitter is missing, the function safely returns an
    empty dataframe and the model stays neutral.
    """
    if team_pitch_type_df is None or team_pitch_type_df.empty or "Name" not in team_pitch_type_df.columns:
        return pd.DataFrame()
    keys = _name_keys(hitter_name)
    df = team_pitch_type_df.copy()
    df["_name_key"] = df["Name"].apply(normalize_name_for_match)
    rows = df[df["_name_key"].isin(keys)].copy()
    if rows.empty:
        return rows
    rows["Whiff"] = rows["Whiff"].apply(lambda x: _to_rate(x, 0.0)) if "Whiff" in rows.columns else 0.0
    rows["Usage"] = rows["Usage"].apply(lambda x: _normalized_usage_rate(x, 0.0)) if "Usage" in rows.columns else 0.0
    return rows




def _lookup_pitch_row(rows, pitch):
    """Return a dict-like row for one pitch type from a dataframe or empty dict."""
    try:
        if rows is None or rows.empty:
            return {}
        match = rows[rows["Pitch Type"].astype(str) == str(pitch)]
        if match.empty:
            return {}
        return match.iloc[0].to_dict()
    except Exception:
        return {}


def _lineup_pitch_type_matchup_multiplier(lineup, pitcher, opponent, pitcher_arsenal_df, team_pitch_type_df, min_hitter_ab=25):
    """Confirmed-lineup hitter-by-hitter pitch-type matchup layer.

    For each meaningful pitch in the pitcher's arsenal, this compares the
    pitcher's pitch-specific K/Whiff/Put-Away profile to each confirmed hitter's
    pitch-specific K/Whiff/Put-Away profile. If a hitter is below the AB sample
    threshold or lacks a pitch row, that hitter/pitch falls back to the opponent
    team pitch-type number. This keeps the model lineup-specific without letting
    tiny hitter samples create fake weapons or fake negatives.
    """
    neutral = {
        "multiplier": 1.0,
        "status": "Neutral - hitter-level pitch-type data unavailable.",
        "hitters_with_pitch_data": 0,
        "hitters_using_team_fallback": 0,
        "min_hitter_ab": int(min_hitter_ab),
        "pitch_types_used": "",
        "lineup_arsenal_score": None,
        "team_fallback_rate": None,
        "lineup_weighted_whiff": None,
        "league_weighted_whiff": None,
        "hitter_weapon_count": 0,
        "detail_rows": pd.DataFrame(),
    }
    arsenal = _pitcher_arsenal_rows(pitcher, pitcher_arsenal_df)
    if arsenal.empty or team_pitch_type_df is None or team_pitch_type_df.empty:
        return neutral

    arsenal = arsenal.copy()
    arsenal["Usage"] = arsenal["Usage"].apply(lambda x: _normalized_usage_rate(x, 0.0))
    arsenal = arsenal[arsenal["Usage"] >= 0.05].sort_values("Usage", ascending=False).copy()
    if arsenal.empty:
        return neutral
    usage_sum = float(arsenal["Usage"].sum())
    if usage_sum <= 0:
        return neutral
    arsenal["Usage Wt"] = arsenal["Usage"] / usage_sum

    # League baselines across all batter pitch-type rows.
    temp = team_pitch_type_df.copy()
    for col in ["Whiff", "K", "Put Away"]:
        if col in temp.columns:
            temp[col] = temp[col].apply(lambda x: _to_rate(x, 0.0))
        else:
            temp[col] = 0.0
    league_pitch_whiff = temp[temp["Whiff"] > 0].groupby("Pitch Type")["Whiff"].mean().to_dict() if "Pitch Type" in temp.columns else {}
    league_pitch_k = temp[temp["K"] > 0].groupby("Pitch Type")["K"].mean().to_dict() if "Pitch Type" in temp.columns else {}
    league_pitch_putaway = temp[temp["Put Away"] > 0].groupby("Pitch Type")["Put Away"].mean().to_dict() if "Pitch Type" in temp.columns else {}

    team_rows = _team_pitch_type_rows(opponent, team_pitch_type_df)

    detail_rows = []
    hitter_scores = []
    team_fallback_hitters = set()
    hitter_weapon_tracker = {str(pt): 0 for pt in arsenal["Pitch Type"].astype(str).tolist()}

    for hitter in lineup or []:
        hname = hitter.get("player", "")
        profile = fetch_mlb_player_k_profile(hitter.get("player_id"), MLB_SEASON)
        hitter_ab = int(profile.get("ab", 0) or 0)
        hitter_pa = int(profile.get("pa", 0) or 0)
        hrows = _hitter_pitch_type_rows(hname, team_pitch_type_df)
        use_hitter_profile = hitter_ab >= int(min_hitter_ab) and not hrows.empty

        weighted_score = 0.0
        whiff_num = 0.0
        league_whiff_num = 0.0
        pitch_notes = []
        used = 0
        fallback_pitch_count = 0

        for _, prow in arsenal.iterrows():
            ptype = str(prow.get("Pitch Type", ""))
            wt = float(prow.get("Usage Wt", 0.0) or 0.0)
            if wt <= 0 or not ptype:
                continue

            league_k = _to_rate(league_pitch_k.get(ptype, 0.22), 0.22)
            league_whiff = _to_rate(league_pitch_whiff.get(ptype, 0.24), 0.24)
            league_putaway = _to_rate(league_pitch_putaway.get(ptype, 0.20), 0.20)

            p_k = _to_rate(prow.get("K", 0.0), 0.0)
            p_whiff = _to_rate(prow.get("Whiff", 0.0), 0.0)
            p_putaway = _to_rate(prow.get("Put Away", 0.0), 0.0)
            pitcher_k_delta = p_k - league_k
            pitcher_whiff_delta = p_whiff - league_whiff
            pitcher_putaway_delta = p_putaway - league_putaway

            source = "Hitter"
            row = {}
            if use_hitter_profile:
                row = _lookup_pitch_row(hrows, ptype)
                # If the hitter has a pitch row but it is only a handful of pitches, fall back to team.
                try:
                    if row and float(row.get("Pitches", 0) or 0) < 10:
                        row = {}
                except Exception:
                    pass
            if not row:
                row = _lookup_pitch_row(team_rows, ptype)
                source = "Team Fallback"
                fallback_pitch_count += 1

            if not row:
                continue

            h_k = _to_rate(row.get("K", 0.0), 0.0)
            h_whiff = _to_rate(row.get("Whiff", 0.0), 0.0)
            h_putaway = _to_rate(row.get("Put Away", 0.0), 0.0)
            if h_k <= 0 and h_whiff <= 0 and h_putaway <= 0:
                continue

            hitter_k_delta = h_k - league_k
            hitter_whiff_delta = h_whiff - league_whiff
            hitter_putaway_delta = h_putaway - league_putaway

            k_edge = pitcher_k_delta + hitter_k_delta
            whiff_edge = pitcher_whiff_delta + hitter_whiff_delta
            putaway_edge = pitcher_putaway_delta + hitter_putaway_delta
            combined_edge = (0.50 * k_edge) + (0.35 * whiff_edge) + (0.15 * putaway_edge)
            weighted_score += wt * combined_edge
            whiff_num += wt * h_whiff
            league_whiff_num += wt * league_whiff
            used += 1

            if source == "Hitter" and combined_edge >= 0.05:
                hitter_weapon_tracker[ptype] = hitter_weapon_tracker.get(ptype, 0) + 1

            pitch_notes.append(f"{ptype}: {combined_edge*100:+.1f} ({source})")

        if used > 0:
            if fallback_pitch_count > 0 or not use_hitter_profile:
                team_fallback_hitters.add(hname)
            hitter_scores.append(weighted_score)
            detail_rows.append({
                "Player": hname,
                "Bats": hitter.get("bat_side", ""),
                "AB": hitter_ab,
                "PA": hitter_pa,
                "Source": "Hitter" if use_hitter_profile and fallback_pitch_count == 0 else "Mixed/Team Fallback",
                "Pitches Matched": used,
                "Arsenal Score": round(weighted_score * 100, 1),
                "Arsenal Whiff Matchup": round(whiff_num * 100, 1) if whiff_num > 0 else "",
                "League Avg vs Mix": round(league_whiff_num * 100, 1) if league_whiff_num > 0 else "",
                "Diff": round((whiff_num - league_whiff_num) * 100, 1) if league_whiff_num > 0 else "",
                "Pitch Notes": "; ".join(pitch_notes[:5]),
            })

    if len(hitter_scores) < 6:
        neutral["hitters_with_pitch_data"] = len(hitter_scores)
        neutral["hitters_using_team_fallback"] = len(team_fallback_hitters)
        neutral["detail_rows"] = pd.DataFrame(detail_rows)
        neutral["status"] = "Confirmed lineup found, but not enough hitter/team pitch-type rows matched."
        neutral["pitch_types_used"] = ", ".join(arsenal["Pitch Type"].astype(str).tolist())
        return neutral

    lineup_score = sum(hitter_scores) / len(hitter_scores)
    lineup_whiff = None
    league_whiff = None
    if detail_rows:
        whiff_vals = [r.get("Arsenal Whiff Matchup") for r in detail_rows if isinstance(r.get("Arsenal Whiff Matchup"), (int, float))]
        lg_vals = [r.get("League Avg vs Mix") for r in detail_rows if isinstance(r.get("League Avg vs Mix"), (int, float))]
        if whiff_vals and lg_vals:
            lineup_whiff = sum(whiff_vals) / len(whiff_vals) / 100.0
            league_whiff = sum(lg_vals) / len(lg_vals) / 100.0

    hitter_weapon_count = sum(1 for _, count in hitter_weapon_tracker.items() if count >= 5)

    # Hitter-by-hitter arsenal is a meaningful lineup layer. Keep a downside safety cap,
    # but do not choke off strong weapon lineups because those are the exact matchups
    # that should create over candidates.
    adj = max(-0.08, lineup_score * 0.75)
    if hitter_weapon_count >= 3:
        adj += 0.040
        positive_cap = 0.18
    elif hitter_weapon_count == 2:
        adj += 0.025
        positive_cap = 0.15
    else:
        positive_cap = 0.12
    adj = max(-0.08, min(positive_cap, adj))
    mult = 1.0 + adj

    if adj > 0.015:
        status = "Confirmed lineup hitter-by-hitter arsenal matchup boosts pitcher K environment."
    elif adj < -0.015:
        status = "Confirmed lineup hitter-by-hitter arsenal matchup reduces pitcher K environment."
    else:
        status = "Confirmed lineup hitter-by-hitter arsenal matchup is near neutral."

    return {
        "multiplier": mult,
        "status": status,
        "hitters_with_pitch_data": len(hitter_scores),
        "hitters_using_team_fallback": len(team_fallback_hitters),
        "min_hitter_ab": int(min_hitter_ab),
        "pitch_types_used": ", ".join(arsenal["Pitch Type"].astype(str).tolist()),
        "lineup_arsenal_score": lineup_score,
        "team_fallback_rate": len(team_fallback_hitters) / max(1, len(hitter_scores)),
        "lineup_weighted_whiff": lineup_whiff,
        "league_weighted_whiff": league_whiff,
        "hitter_weapon_count": int(hitter_weapon_count),
        "detail_rows": pd.DataFrame(detail_rows),
    }

def build_lineup_k_blend_details(game_pk, side, opponent, pitcher_hand, team_batting_rhp, team_batting_lhp, pitcher="", pitcher_arsenal_df=None, team_pitch_type_df=None):
    """Build 70/30 lineup-to-team-baseline K blend details for display and projection adjustment."""
    baseline_k = _team_baseline_k_rate_for_pitcher_hand(opponent, pitcher_hand, team_batting_rhp, team_batting_lhp)
    lineup = fetch_mlb_confirmed_lineup(game_pk, side)

    details = {
        "source": "Team baseline fallback",
        "hitters_found": 0,
        "lineup_k_rate": None,
        "team_baseline_k_rate": baseline_k,
        "blended_k_rate": baseline_k,
        "lineup_weight": 0.0,
        "team_weight": 1.0,
        # Pitcher K projections no longer use a second confirmed-lineup multiplier.
        # The lineup/K and hitter pitch-type sections remain visible as diagnostics only
        # so they do not double-count against the arsenal projection.
        "multiplier": 1.0,
        "projection_multiplier": 1.0,
        "diagnostic_multiplier": 1.0,
        "k_multiplier": 1.0,
        "hand_stack_multiplier": 1.0,
        "hand_stack": {},
        "pitch_type_multiplier": 1.0,
        "pitch_type_matchup": {},
        "lineup_obp": None,
        "lineup_slg": None,
        "lineup_avg": None,
        "lineup_ops": None,
        "lineup_offense_score": 0.0,
        "lineup_strength_status": "No confirmed MLB lineup found yet.",
        "status": "No confirmed MLB lineup found yet.",
        "hitters": pd.DataFrame(),
    }

    if not lineup or len(lineup) < 8:
        return details

    hitter_rows = []
    k_rates = []
    lineup_profiles = []
    lineup_weighted_scores = []
    order_weights = {1: 0.12, 2: 0.13, 3: 0.16, 4: 0.16, 5: 0.13, 6: 0.10, 7: 0.08, 8: 0.06, 9: 0.06}

    def _hitter_reliability(sample):
        try:
            sample = float(sample or 0)
        except Exception:
            sample = 0.0
        if sample >= 100:
            return 1.00
        if sample >= 75:
            return 0.95
        if sample >= 50:
            return 0.88
        if sample >= 25:
            return 0.75
        if sample >= 15:
            return 0.60
        if sample >= 5:
            return 0.40
        return 0.20

    def _low_sample_penalty(sample):
        # V13: uncertainty is handled by reliability shrinkage and confidence flags.
        # Unknown/rookie hitters are not assumed to be bad hitters.
        return 0.0

    for hitter in lineup:
        raw_order = int(hitter.get("order", 0) or 0)
        order_num = raw_order // 100 if raw_order >= 100 else raw_order
        if order_num <= 0 or order_num > 9:
            order_num = len(hitter_rows) + 1

        k_rate = fetch_mlb_player_k_rate(hitter.get("player_id"), MLB_SEASON)
        profile = fetch_mlb_player_batting_profile(hitter.get("player_id"), MLB_SEASON)
        sample = float((profile or {}).get("sample_size", (profile or {}).get("pa", 0)) or 0)
        reliability = _hitter_reliability(sample)
        sample_penalty = _low_sample_penalty(sample)

        if profile:
            lineup_profiles.append(profile)
            hitter_score = ((profile.get("obp", 0.315) - 0.315) * 220 + (profile.get("slg", 0.410) - 0.410) * 160 + (profile.get("avg", 0.250) - 0.250) * 90)
            hitter_score = max(-12.0, min(12.0, hitter_score))
        else:
            hitter_score = 0.0

        weighted_hitter_score = (hitter_score * reliability) + sample_penalty
        order_weight = order_weights.get(int(order_num), 0.06)
        lineup_weighted_scores.append((weighted_hitter_score, order_weight))

        hitter_rows.append({
            "Order": order_num,
            "Player": hitter.get("player", ""),
            "Bats": hitter.get("bat_side", ""),
            "PA": int((profile or {}).get("pa", 0) or 0),
            "AB": int((profile or {}).get("ab", 0) or 0),
            "Reliability": round(reliability * 100, 0),
            "Sample Penalty": round(sample_penalty, 1),
            "Hitter Score": round(hitter_score, 1),
            "Weighted Score": round(weighted_hitter_score, 1),
            "K%": round(k_rate * 100, 1) if k_rate is not None else "",
            "OBP": round(profile.get("obp", 0), 3) if profile else "",
            "SLG": round(profile.get("slg", 0), 3) if profile else "",
            "OPS": round(profile.get("ops", 0), 3) if profile else "",
        })
        if k_rate is not None:
            k_rates.append(k_rate)

    if lineup_profiles or lineup_weighted_scores:
        lineup_obp = sum(p.get("obp", 0.315) for p in lineup_profiles) / len(lineup_profiles) if lineup_profiles else 0.315
        lineup_slg = sum(p.get("slg", 0.410) for p in lineup_profiles) / len(lineup_profiles) if lineup_profiles else 0.410
        lineup_avg = sum(p.get("avg", 0.250) for p in lineup_profiles) / len(lineup_profiles) if lineup_profiles else 0.250
        lineup_ops = sum(p.get("ops", 0.725) for p in lineup_profiles) / len(lineup_profiles) if lineup_profiles else 0.725
        total_weight = sum(w for _, w in lineup_weighted_scores) or 1.0
        lineup_offense_score = sum(score * wt for score, wt in lineup_weighted_scores) / total_weight
        lineup_offense_score = max(-10.0, min(10.0, lineup_offense_score))
        low_sample_hitters = sum(1 for row in hitter_rows if float(row.get("PA", 0) or row.get("AB", 0) or 0) < 25)
        details.update({
            "lineup_obp": lineup_obp,
            "lineup_slg": lineup_slg,
            "lineup_avg": lineup_avg,
            "lineup_ops": lineup_ops,
            "lineup_offense_score": lineup_offense_score,
            "lineup_low_sample_hitters": int(low_sample_hitters),
            "lineup_strength_status": f"Confirmed lineup-only offense score; {low_sample_hitters} hitter(s) below 25 PA/AB are regressed toward neutral and flagged as uncertain.",
        })

    if len(k_rates) < 6:
        details["source"] = "Team baseline fallback"
        details["hitters_found"] = len(k_rates)
        hand_stack = _lineup_handedness_stack_multiplier(lineup, pitcher_hand)
        details["hand_stack"] = hand_stack
        details["hand_stack_multiplier"] = hand_stack.get("multiplier", 1.0)
        pitch_type_matchup = _lineup_pitch_type_matchup_multiplier(lineup, pitcher, opponent, pitcher_arsenal_df, team_pitch_type_df)
        details["pitch_type_matchup"] = pitch_type_matchup
        details["pitch_type_multiplier"] = float(pitch_type_matchup.get("multiplier", 1.0) or 1.0)
        details["diagnostic_multiplier"] = float(details.get("pitch_type_multiplier", 1.0) or 1.0)
        details["projection_multiplier"] = 1.0
        details["multiplier"] = 1.0
        details["status"] = "Confirmed lineup found, but not enough hitter K% stats were available. Lineup multipliers are diagnostics only and are not applied to pitcher K projection."
        details["hitters"] = pd.DataFrame(hitter_rows)
        return details

    lineup_k = sum(k_rates) / len(k_rates)
    blended_k = (0.70 * lineup_k) + (0.30 * baseline_k)
    raw_multiplier = blended_k / baseline_k if baseline_k > 0 else 1.0
    k_multiplier = max(0.85, min(1.15, raw_multiplier))
    hand_stack = _lineup_handedness_stack_multiplier(lineup, pitcher_hand)
    hand_stack_multiplier = float(hand_stack.get("multiplier", 1.0) or 1.0)
    pitch_type_matchup = _lineup_pitch_type_matchup_multiplier(lineup, pitcher, opponent, pitcher_arsenal_df, team_pitch_type_df)
    pitch_type_multiplier = float(pitch_type_matchup.get("multiplier", 1.0) or 1.0)
    diagnostic_multiplier = max(0.80, min(1.20, k_multiplier * hand_stack_multiplier * pitch_type_multiplier))
    # IMPORTANT: do not apply this multiplier to pitcher K projections anymore.
    # It double-counted lineup/pitch-type information after the arsenal engine and
    # was dragging too many props toward unders. Keep it for diagnostics only.
    projection_multiplier = 1.0

    details.update({
        "source": "MLB confirmed lineup",
        "hitters_found": len(k_rates),
        "lineup_k_rate": lineup_k,
        "team_baseline_k_rate": baseline_k,
        "blended_k_rate": blended_k,
        "lineup_weight": 0.70,
        "team_weight": 0.30,
        "k_multiplier": k_multiplier,
        "hand_stack_multiplier": hand_stack_multiplier,
        "hand_stack": hand_stack,
        "pitch_type_multiplier": pitch_type_multiplier,
        "pitch_type_matchup": pitch_type_matchup,
        "diagnostic_multiplier": diagnostic_multiplier,
        "projection_multiplier": projection_multiplier,
        "multiplier": projection_multiplier,
        "status": "Confirmed MLB lineup found. Lineup K%, handedness, and hitter pitch-type multipliers are diagnostics only; no second lineup multiplier is applied to pitcher K projection.",
        "hitters": pd.DataFrame(hitter_rows),
    })
    return details


def apply_lineup_k_adjustment(projection, lineup_details):
    """Return the arsenal-adjusted projection without a second lineup multiplier.

    The confirmed-lineup section now provides diagnostics only. Applying its
    blended K% / handedness / hitter pitch-type multiplier after the arsenal
    layer was double-counting lineup effects and pushing too many pitcher props
    toward unders.
    """
    try:
        return max(0, float(projection) * float((lineup_details or {}).get("projection_multiplier", 1.0) or 1.0))
    except Exception:
        return projection
@st.cache_data(ttl=5 * 60)
def _legacy_pull_mlb_moneyline_odds_v1(api_key):
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



def _legacy_run_today_model_for_games_v1(today_games, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, nrfi_pitchers, nrfi_rhp, nrfi_lhp, pitcher_arsenal_df=None, team_pitch_type_df=None):
    rows = []
    if today_games is None or today_games.empty:
        return pd.DataFrame()
    for _, game in today_games.iterrows():
        away_team, home_team = game.get("away_team", ""), game.get("home_team", "")
        away_pitcher, home_pitcher = game.get("away_pitcher", ""), game.get("home_pitcher", "")
        if not away_pitcher or not home_pitcher:
            rows.append({"Away Team": away_team, "Home Team": home_team, "Away Pitcher": away_pitcher or "TBD", "Home Pitcher": home_pitcher or "TBD", "Status": "Missing probable pitcher"})
            continue
        home_k = expected_strikeouts(home_pitcher, away_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df)
        away_k = expected_strikeouts(away_pitcher, home_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df)
        home_k_6ip = six_inning_strikeouts(home_pitcher, away_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df)
        away_k_6ip = six_inning_strikeouts(away_pitcher, home_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df)
        nrfi_prob = nrfi_probability(home_team, away_team, home_pitcher, away_pitcher, pitcher_this_year, pitcher_last_year, nrfi_pitchers, nrfi_rhp, nrfi_lhp)
        nrfi_score = nrfi_score_formula(nrfi_prob)
        nrfi_grade = nrfi_bet_grade(nrfi_score)
        home_win_prob, away_win_prob = moneyline_probability(home_team, away_team, home_pitcher, away_pitcher, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp)
        home_ml_odds = game.get("home_ml_odds", None)
        away_ml_odds = game.get("away_ml_odds", None)
        home_implied, away_implied, home_fair_implied, away_fair_implied = _two_way_no_vig_probabilities(home_ml_odds, away_ml_odds)
        home_ml_edge = home_win_prob - home_fair_implied
        away_ml_edge = away_win_prob - away_fair_implied
        rows.append({
            "Away Team": away_team, "Home Team": home_team, "Away Pitcher": away_pitcher, "Home Pitcher": home_pitcher,
            "Away Expected K": round(away_k, 2), "Away 6-IP Pace": round(away_k_6ip, 2),
            "Home Expected K": round(home_k, 2), "Home 6-IP Pace": round(home_k_6ip, 2),
            "NRFI %": round(nrfi_prob * 100, 1), "NRFI Score": round(nrfi_score, 1), "NRFI Grade": nrfi_grade,
            "Home Win %": round(home_win_prob * 100, 1), "Away Win %": round(away_win_prob * 100, 1),
            "Home ML Odds": home_ml_odds, "Home Book": game.get("home_book", ""), "Home Implied %": round(home_implied * 100, 1), "Home ML Edge %": round(home_ml_edge * 100, 1), "Home ML Grade": moneyline_grade(home_ml_edge),
            "Away ML Odds": away_ml_odds, "Away Book": game.get("away_book", ""), "Away Implied %": round(away_implied * 100, 1), "Away ML Edge %": round(away_ml_edge * 100, 1), "Away ML Grade": moneyline_grade(away_ml_edge),
            "Status": game.get("status", "")
        })
    return pd.DataFrame(rows)




# -----------------------
# FINAL LIVE DATA OVERRIDES - NO HTML TABLE SCRAPING
# These definitions intentionally come after the older first-run helpers so they
# override any previous functions that tried to read MLB.com-style HTML tables.
# The Matchup Builder now uses MLB Stats API data first and neutral fallbacks second.
# -----------------------

@st.cache_data(ttl=60 * 60)
def _legacy_load_team_hitting_stats_live_v3():
    df = _mlb_team_stats("hitting", MLB_SEASON, "season")
    if df.empty:
        teams = _mlb_teams_lookup()
        names = teams["name"].tolist() if not teams.empty else list(TEAM_ABBR_MAP.values())
        names = sorted(set([n for n in names if n]))
        return pd.DataFrame({
            "Teams": names,
            "Hits": 250,
            "RBI's": 0,
            "Team Batting Avg.": 0.250,
            "Team On-Base %": 0.315,
            "Team Slugging %": 0.410,
        })
    out = pd.DataFrame({
        "Teams": df.get("Teams", "").astype(str).str.strip(),
        "Hits": pd.to_numeric(df.get("hits", 250), errors="coerce").fillna(250),
        "RBI's": pd.to_numeric(df.get("rbi", 0), errors="coerce").fillna(0),
        "Team Batting Avg.": pd.to_numeric(df.get("avg", 0.250), errors="coerce").fillna(0.250),
        "Team On-Base %": pd.to_numeric(df.get("obp", 0.315), errors="coerce").fillna(0.315),
        "Team Slugging %": pd.to_numeric(df.get("slg", 0.410), errors="coerce").fillna(0.410),
    })
    return out[out["Teams"].astype(str).str.strip() != ""].reset_index(drop=True)


@st.cache_data(ttl=60 * 60)
def _legacy_load_team_batting_split_live_v3(split):
    # MLB public API does not consistently expose clean team vs RHP/LHP splits.
    # Use the live team baseline as the split fallback so the model runs without Excel.
    # The confirmed-lineup layer still gives pitcher props matchup-specific adjustment once lineups post.
    df = _mlb_team_stats("hitting", MLB_SEASON, "season")
    if df.empty:
        teams = _mlb_teams_lookup()
        names = teams["name"].tolist() if not teams.empty else list(TEAM_ABBR_MAP.values())
        names = sorted(set([n for n in names if n]))
        return pd.DataFrame({
            "Teams": names,
            "Games": 0,
            "At Bats": 1000,
            "Hits": 250,
            "Batted Balls": 0,
            "Strikeouts": 220,
            "Batting Average": 0.250,
            "On-Base %": 0.315,
            "Slug %": 0.410,
        })
    out = pd.DataFrame({
        "Teams": df.get("Teams", "").astype(str).str.strip(),
        "Games": pd.to_numeric(df.get("gamesPlayed", 0), errors="coerce").fillna(0),
        "At Bats": pd.to_numeric(df.get("atBats", 1000), errors="coerce").fillna(1000),
        "Hits": pd.to_numeric(df.get("hits", 250), errors="coerce").fillna(250),
        "Batted Balls": pd.to_numeric(df.get("baseOnBalls", 0), errors="coerce").fillna(0),
        "Strikeouts": pd.to_numeric(df.get("strikeOuts", 220), errors="coerce").fillna(220),
        "Batting Average": pd.to_numeric(df.get("avg", 0.250), errors="coerce").fillna(0.250),
        "On-Base %": pd.to_numeric(df.get("obp", 0.315), errors="coerce").fillna(0.315),
        "Slug %": pd.to_numeric(df.get("slg", 0.410), errors="coerce").fillna(0.410),
    })
    return out[out["Teams"].astype(str).str.strip() != ""].reset_index(drop=True)




# -----------------------
# SAVANT CONTACT DEBUG / ROBUST NAME BACKFILL
# -----------------------

def _savant_name_keys(value):
    """Return several normalized name keys so Savant Last, First and MLB First Last both match."""
    raw = str(value or "").strip()
    if not raw:
        return set()
    keys = {normalize_match_text(raw), normalize_name_for_match(raw)}
    try:
        keys.add(normalize_match_text(to_first_last(raw)))
        keys.add(normalize_name_for_match(to_first_last(raw)))
    except Exception:
        pass
    try:
        keys.add(normalize_match_text(to_last_first(raw)))
        keys.add(normalize_name_for_match(to_last_first(raw)))
    except Exception:
        pass
    if "," in raw:
        try:
            last, first = raw.split(",", 1)
            keys.add(normalize_match_text(f"{first.strip()} {last.strip()}"))
            keys.add(normalize_name_for_match(f"{first.strip()} {last.strip()}"))
            keys.add(normalize_match_text(f"{last.strip()}, {first.strip()}"))
            keys.add(normalize_name_for_match(f"{last.strip()}, {first.strip()}"))
        except Exception:
            pass
    else:
        parts = raw.split()
        if len(parts) >= 2:
            first = parts[0]
            last = " ".join(parts[1:])
            keys.add(normalize_match_text(f"{last}, {first}"))
            keys.add(normalize_name_for_match(f"{last}, {first}"))
    return {k for k in keys if k}


def _backfill_savant_contact_by_name(out, savant):
    """Safety net after the ID merge.

    If the Savant custom leaderboard loaded correctly but Hard Hit/Barrel/xwOBA
    did not survive the ID merge or suffix handling, fill those fields directly
    by normalized player-name keys. This specifically fixes the issue where GB%
    populated from the batted-ball file but Hard Hit/Barrel stayed at defaults.
    """
    try:
        if out is None or getattr(out, "empty", True) or savant is None or getattr(savant, "empty", True):
            return out
        if "Player" not in out.columns or "Player" not in savant.columns:
            return out

        out = out.copy()
        sav_lookup = {}
        id_lookup = {}
        for _, srow in savant.iterrows():
            for key in _savant_name_keys(srow.get("Player", "")):
                sav_lookup[key] = srow
            sid = str(srow.get("MLBAM ID", "")).replace(".0", "").strip()
            if sid and sid.lower() not in ["nan", "none"]:
                id_lookup[sid] = srow

        def _needs_fill(col, cur):
            val = _to_number(cur, None)
            if val is None:
                return True
            if col == "xwOBA":
                return val <= 0 or abs(val - 0.320) < 0.00001
            if col in ["Hard Hit %", "Barrel %", "GB%"]:
                return val <= 0
            return val <= 0

        fill_cols = ["xwOBA", "Hard Hit %", "Barrel %", "GB%", "HR", "Whiff %", "First Strike %", "Out of Zone %", "In Zone %", "Pitches", "BBE"]
        for col in fill_cols:
            if col not in out.columns:
                out[col] = 0.320 if col == "xwOBA" else 0

        for idx, orow in out.iterrows():
            src = None
            oid = str(orow.get("MLBAM ID", "")).replace(".0", "").strip()
            if oid and oid in id_lookup:
                src = id_lookup[oid]
            if src is None:
                for key in _savant_name_keys(orow.get("Player", "")):
                    if key in sav_lookup:
                        src = sav_lookup[key]
                        break
            if src is None:
                continue
            for col in fill_cols:
                if col not in savant.columns:
                    continue
                val = _to_number(src.get(col, 0), 0)
                if val and _needs_fill(col, out.at[idx, col]):
                    out.at[idx, col] = val
        return out
    except Exception:
        return out


def _render_savant_contact_diagnostics(pitcher_df):
    """Sidebar debug panel to prove whether contact fields are actually populated."""
    try:
        with st.sidebar.expander("Savant contact diagnostics", expanded=False):
            if pitcher_df is None or getattr(pitcher_df, "empty", True):
                st.caption("Pitcher table is empty.")
                return
            df = pitcher_df.copy()
            rows = len(df)
            def _count_real(col, default=None):
                if col not in df.columns:
                    return 0
                vals = pd.to_numeric(df[col], errors="coerce")
                if default is None:
                    return int((vals.fillna(0) > 0).sum())
                return int((vals.notna() & (abs(vals - default) > 0.00001) & (vals > 0)).sum())
            st.caption(f"Rows loaded: {rows}")
            st.caption(f"Hard Hit populated: {_count_real('Hard Hit %')} / {rows}")
            st.caption(f"Barrel populated: {_count_real('Barrel %')} / {rows}")
            st.caption(f"GB populated: {_count_real('GB%')} / {rows}")
            st.caption(f"xwOBA non-default: {_count_real('xwOBA', 0.320)} / {rows}")
            cols = [c for c in ["Player", "MLBAM ID", "xwOBA", "Hard Hit %", "Barrel %", "GB%", "HR"] if c in df.columns]
            if cols:
                sample = df[cols].copy()
                for c in ["xwOBA", "Hard Hit %", "Barrel %", "GB%"]:
                    if c in sample.columns:
                        sample[c] = pd.to_numeric(sample[c], errors="coerce")
                st.dataframe(sample.head(12), use_container_width=True, hide_index=True)
    except Exception as e:
        try:
            st.sidebar.caption(f"Savant contact diagnostics unavailable: {e}")
        except Exception:
            pass

@st.cache_data(ttl=60 * 60)
def _savant_pitcher_batted_ball_stats(year):
    """Pull Baseball Savant batted-ball pitcher fields, especially gb_rate.

    Uses min=1, not qualified, so low-inning pitchers are included. The downloaded
    Savant batted-ball CSV uses columns like id, name, bbe, and gb_rate.
    """
    urls = [
        (
            "https://baseballsavant.mlb.com/leaderboard/batted-ball"
            f"?type=pitcher&season%5B%5D={year}&splitYear=1&min=1&minSplit=1&gameType%5B%5D=R&dateStart=&dateEnd=&batSide=&pitchHand=&csv=true"
        ),
        (
            "https://baseballsavant.mlb.com/leaderboard/batted-ball"
            f"?type=pitcher&season[]={year}&splitYear=1&min=1&minSplit=1&gameType[]=R&dateStart=&dateEnd=&batSide=&pitchHand=&csv=true"
        ),
    ]

    from io import StringIO
    raw = pd.DataFrame()
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"}
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=35)
            r.raise_for_status()
            text = r.text.strip()
            if not text or "<html" in text.lower() or "<!doctype" in text.lower() or "," not in text:
                continue
            raw = pd.read_csv(StringIO(text))
            if raw is not None and not raw.empty:
                break
        except Exception:
            continue

    if raw is None or raw.empty:
        # Local/dev fallback for the manually downloaded Baseball Savant
        # batted-ball.csv. The live app should normally use the URL above.
        try:
            if os.path.exists("batted-ball.csv"):
                raw = pd.read_csv("batted-ball.csv")
        except Exception:
            raw = pd.DataFrame()

    if raw is None or raw.empty:
        return pd.DataFrame(columns=["Player", "MLBAM ID", "GB%", "BBE"])

    raw.columns = [_clean_col_name(c) for c in raw.columns]
    name_col = _find_col(raw, ["name", "last_name, first_name", "player_name", "Player", "pitcher_name"])
    id_col = _find_col(raw, ["id", "player_id", "pitcher", "MLBAM ID"])
    gb_col = _find_col(raw, ["gb_rate", "ground_ball_rate", "groundballs_percent", "gb_percent", "GB%", "Ground Ball %"])
    bbe_col = _find_col(raw, ["bbe", "batted_ball_events", "Batted Ball Events", "batted_balls"])

    rows = []
    for _, row in raw.iterrows():
        raw_name = str(row.get(name_col, "")).strip() if name_col else ""
        if not raw_name or raw_name.lower() in ["nan", "none"]:
            continue
        gb = _to_rate(row.get(gb_col, 0.0), 0.0) if gb_col else 0.0
        rows.append({
            "Player": to_last_first(raw_name),
            "MLBAM ID": str(row.get(id_col, "")).strip() if id_col else "",
            "GB%": gb if gb is not None else 0.0,
            "BBE": _to_number(row.get(bbe_col, 0.0), 0.0) if bbe_col else 0.0,
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["_bbe_sort"] = pd.to_numeric(out.get("BBE", 0), errors="coerce").fillna(0)
    return out.sort_values("_bbe_sort", ascending=False).drop_duplicates("Player").drop(columns=["_bbe_sort"], errors="ignore").reset_index(drop=True)


@st.cache_data(ttl=60 * 60)
def _savant_pitcher_skill_stats(year):
    """Pull pitcher skill columns from Baseball Savant's Statcast leaderboard.

    MLB Stats API is reliable for IP/SO/K%/BB%, but it does not provide the
    whiff, chase, zone, first-strike, hard-hit, barrel, or xwOBA fields used in the
    Recent Skill section. This helper maps several possible Savant CSV column
    names so the app does not quietly turn real Savant stats into 0.0.
    """
    # Use the same Baseball Savant Custom Leaderboard CSV format as the manual
    # download you sent: last_name, first_name / player_id / hard_hit_percent /
    # barrel_batted_rate / xwoba. min=1 is intentional, NOT qualified.
    urls = [
        (
            "https://baseballsavant.mlb.com/leaderboard/custom"
            f"?year={year}&type=pitcher&filter=&min=1"
            "&selections=pa%2Ck_percent%2Cbb_percent%2Cwoba%2Cxwoba%2Csweet_spot_percent%2Cbarrel_batted_rate%2Chard_hit_percent%2Cavg_best_speed%2Cavg_hyper_speed%2Cwhiff_percent%2Cswing_percent"
            "&chart=false&x=pa&y=pa&r=no&chartType=beeswarm&sort=xwoba&sortDir=asc&csv=true"
        ),
        (
            "https://baseballsavant.mlb.com/leaderboard/custom"
            f"?year={year}&type=pitcher&filter=&min=1"
            "&selections=p_game,p_formatted_ip,pa,strikeout,k_percent,bb_percent,p_era,xwoba,hard_hit_percent,barrel_batted_rate,home_run,whiff_percent"
            "&chart=false&x=pa&y=pa&r=no&chartType=beeswarm&sort=player_name&sortDir=asc&csv=true"
        ),
    ]

    from io import StringIO
    raw = pd.DataFrame()
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"}
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=35)
            r.raise_for_status()
            text = r.text.strip()
            if not text or "<html" in text.lower() or "<!doctype" in text.lower() or "," not in text:
                continue
            raw = pd.read_csv(StringIO(text))
            if raw is not None and not raw.empty:
                break
        except Exception:
            continue

    if raw is None or raw.empty:
        # Local/dev fallback: lets the app work if the manually downloaded
        # Baseball Savant stats.csv is present beside the app file. Render will
        # normally use the live CSV URL above.
        try:
            if os.path.exists("stats.csv"):
                raw = pd.read_csv("stats.csv")
        except Exception:
            raw = pd.DataFrame()

    if raw is None or raw.empty:
        return pd.DataFrame(columns=[
            "Player", "MLBAM ID", "xwOBA", "Hard Hit %", "Barrel %", "GB%", "HR", "Out of Zone %",
            "In Zone %", "Whiff %", "First Strike %", "Pitches", "BBE"
        ])

    raw.columns = [_clean_col_name(c) for c in raw.columns]

    name_col = _find_col(raw, [
        "last_name, first_name", "player_name", "Player", "Name", "pitcher_name"
    ])
    id_col = _find_col(raw, ["player_id", "pitcher", "MLBAM ID", "id"] )

    def pick_rate(row, aliases, default=0.0):
        col = _find_col(raw, aliases)
        if not col:
            return default
        val = _to_rate(row.get(col, default), default)
        return val if val is not None else default

    def pick_number(row, aliases, default=0.0):
        col = _find_col(raw, aliases)
        if not col:
            return default
        val = _to_number(row.get(col, default), default)
        return val if val is not None else default

    rows = []
    for _, row in raw.iterrows():
        raw_name = str(row.get(name_col, "")).strip() if name_col else ""
        if not raw_name or raw_name.lower() in ["nan", "none"]:
            continue
        player_name = to_last_first(raw_name)
        rows.append({
            "Player": player_name,
            "MLBAM ID": str(row.get(id_col, "")).strip() if id_col else "",
            "xwOBA": pick_rate(row, ["xwoba", "xwOBA", "estimated_woba_using_speedangle"], 0.320),
            "Hard Hit %": pick_rate(row, ["hard_hit_percent", "hard_hit_pct", "hardhit_percent", "Hard Hit %", "HardHit%"], 0.0),
            "Barrel %": pick_rate(row, ["barrel_batted_rate", "barrel_percent", "barrels_percent", "barrel_rate", "Barrel %", "Barrel%", "Barrels %"], 0.0),
            "GB%": pick_rate(row, ["groundballs_percent", "ground_ball_percent", "gb_percent", "gb_rate", "Ground Ball %", "GB%", "GroundBall%"], 0.0),
            "HR": pick_number(row, ["home_run", "home_runs", "hr", "HR", "Home Runs"], 0.0),
            "Out of Zone %": pick_rate(row, ["oz_swing_percent", "o_swing_percent", "chase_percent", "Chase %", "O-Zone%", "Out of Zone %"], 0.0),
            "In Zone %": pick_rate(row, ["in_zone_percent", "zone_percent", "Zone %", "In Zone %"], 0.0),
            "Whiff %": pick_rate(row, ["whiff_percent", "Whiff %", "Whiff%"], 0.0),
            "First Strike %": pick_rate(row, ["f_strike_percent", "f_strike_pct", "First Strike %", "F-Strike%"], 0.0),
            "Pitches": pick_number(row, ["pitches", "Pitches", "pitch_count"], 0.0),
            "BBE": pick_number(row, ["bbe", "batted_ball_events", "Batted Ball Events"], 0.0),
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["_nonzero_skill_count"] = (
        (pd.to_numeric(out["Whiff %"], errors="coerce").fillna(0) > 0).astype(int) +
        (pd.to_numeric(out["First Strike %"], errors="coerce").fillna(0) > 0).astype(int) +
        (pd.to_numeric(out["Out of Zone %"], errors="coerce").fillna(0) > 0).astype(int) +
        (pd.to_numeric(out["In Zone %"], errors="coerce").fillna(0) > 0).astype(int)
    )
    out = out.sort_values("_nonzero_skill_count", ascending=False).drop_duplicates("Player")
    out = out.drop(columns=["_nonzero_skill_count"], errors="ignore").reset_index(drop=True)

    # Add Baseball Savant batted-ball leaderboard GB% when available.
    # The Statcast custom export has hard_hit_percent/barrel_batted_rate/xwoba,
    # but GB% lives in the batted-ball export as gb_rate. Keep min=1 so
    # rookies, recent callups, openers, and low-inning pitchers are not excluded.
    try:
        bb = _savant_pitcher_batted_ball_stats(year)
        if bb is not None and not bb.empty:
            if "MLBAM ID" in out.columns and "MLBAM ID" in bb.columns:
                out["MLBAM ID"] = out["MLBAM ID"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
                bb = bb.copy()
                bb["MLBAM ID"] = bb["MLBAM ID"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
                out = out.merge(bb.drop(columns=["Player"], errors="ignore"), on="MLBAM ID", how="left", suffixes=("", "_bb"))
            else:
                out["_name_key"] = out["Player"].apply(normalize_name_for_match)
                bb = bb.copy()
                bb["_name_key"] = bb["Player"].apply(normalize_name_for_match)
                out = out.merge(bb.drop(columns=["Player"], errors="ignore"), on="_name_key", how="left", suffixes=("", "_bb")).drop(columns=["_name_key"], errors="ignore")

            if "GB%_bb" in out.columns:
                base_gb = pd.to_numeric(out.get("GB%", 0), errors="coerce").fillna(0)
                bb_gb = pd.to_numeric(out["GB%_bb"], errors="coerce")
                out["GB%"] = bb_gb.where(bb_gb.notna() & (bb_gb > 0), base_gb)
                out = out.drop(columns=["GB%_bb"], errors="ignore")
            if "BBE" in out.columns and "BBE_bb" in out.columns:
                base_bbe = pd.to_numeric(out.get("BBE", 0), errors="coerce").fillna(0)
                bb_bbe = pd.to_numeric(out["BBE_bb"], errors="coerce")
                out["BBE"] = bb_bbe.where(bb_bbe.notna() & (bb_bbe > 0), base_bbe)
                out = out.drop(columns=["BBE_bb"], errors="ignore")
    except Exception:
        pass

    return out.reset_index(drop=True)


@st.cache_data(ttl=60 * 60)
def load_pitcher_data_live(year):
    df = _mlb_all_player_pitching_stats(year)
    if df is None or df.empty:
        df = pd.DataFrame(columns=["Player", "Year", "G", "IP", "BF", "SO", "K%", "BB%", "ERA", "xwOBA", "Throws"])

    savant = _savant_pitcher_skill_stats(year)
    if savant is None or savant.empty or "Player" not in df.columns:
        return df

    # Merge by MLBAM ID when available, otherwise by normalized player name.
    out = df.copy()
    merged = False
    if "MLBAM ID" in out.columns and "MLBAM ID" in savant.columns:
        # Pandas merge requires both join columns to have the same dtype.
        # MLB Stats API can return ids as strings while Savant returns ints,
        # so normalize both to clean strings before merging.
        out["MLBAM ID"] = out["MLBAM ID"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
        savant = savant.copy()
        savant["MLBAM ID"] = savant["MLBAM ID"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
        left_ids = out["MLBAM ID"]
        right_ids = savant["MLBAM ID"]
        if left_ids.ne("").any() and right_ids.ne("").any():
            out = out.merge(
                savant.drop(columns=["Player"], errors="ignore"),
                on="MLBAM ID",
                how="left",
                suffixes=("", "_savant"),
            )
            merged = True

    if not merged:
        out["_name_key"] = out["Player"].apply(normalize_name_for_match)
        sav = savant.copy()
        sav["_name_key"] = sav["Player"].apply(normalize_name_for_match)
        out = out.merge(
            sav.drop(columns=["Player"], errors="ignore"),
            on="_name_key",
            how="left",
            suffixes=("", "_savant"),
        ).drop(columns=["_name_key"], errors="ignore")

    skill_cols = ["xwOBA", "Hard Hit %", "Barrel %", "GB%", "HR", "Out of Zone %", "In Zone %", "Whiff %", "First Strike %", "Pitches", "BBE", "PA", "Pitches/PA", "K %", "BB %", "Swing %", "Zone %", "Zone Swing %", "Zone Contact %", "Chase %", "Chase Contact %", "Edge %", "First Pitch Swing %", "Called Strike %", "CSW %"]
    for col in skill_cols:
        sav_col = f"{col}_savant"
        if sav_col in out.columns:
            base = pd.to_numeric(out.get(col, 0), errors="coerce").fillna(0)
            adv = pd.to_numeric(out[sav_col], errors="coerce")
            # Keep real Savant values whenever present. This prevents Stats API placeholder
            # zeroes from showing in Recent Skill.
            out[col] = adv.where(adv.notna() & (adv != 0), base)
            out = out.drop(columns=[sav_col], errors="ignore")
        elif col not in out.columns:
            out[col] = 0.320 if col == "xwOBA" else 0

    return out


@st.cache_data(ttl=60 * 60)
def load_pitcher_handedness_live(year=MLB_SEASON):
    return _mlb_player_handedness_lookup(year)


@st.cache_data(ttl=60 * 60)
def _legacy_load_nrfi_pitchers_live_extra1():
    # Neutral first-inning table: NRFI formula will still use pitcher season xwOBA/K data.
    # This avoids a blocked blocked first-inning split dependency.
    return _neutral_nrfi_pitcher_table()


@st.cache_data(ttl=60 * 60)
def _legacy_load_nrfi_team_split_live_v2(hand):
    base = load_team_batting_split_live("vr" if str(hand).lower().startswith("r") else "vl")
    if base is None or base.empty:
        return pd.DataFrame(columns=["Teams", "OBP", "K%", "wOBA", "BB/K", "ISO"])
    ab = pd.to_numeric(base.get("At Bats", 0), errors="coerce").fillna(0)
    so = pd.to_numeric(base.get("Strikeouts", 0), errors="coerce").fillna(0)
    bb = pd.to_numeric(base.get("Batted Balls", 0), errors="coerce").fillna(0)
    obp = pd.to_numeric(base.get("On-Base %", 0.315), errors="coerce").fillna(0.315)
    slg = pd.to_numeric(base.get("Slug %", 0.410), errors="coerce").fillna(0.410)
    avg = pd.to_numeric(base.get("Batting Average", 0.250), errors="coerce").fillna(0.250)
    k_pct = (so / ab.replace(0, pd.NA)).fillna(0.22)
    iso = (slg - avg).fillna(0.160)
    # Simple wOBA proxy from OBP/SLG keeps NRFI formula directionally usable without first-inning splits.
    woba = ((obp * 0.70) + (slg * 0.30)).fillna(0.320)
    bbk = (bb / so.replace(0, pd.NA)).fillna(0.50)
    return pd.DataFrame({
        "Teams": base.get("Teams", "").astype(str).str.strip(),
        "OBP": obp,
        "K%": k_pct,
        "wOBA": woba,
        "BB/K": bbk,
        "ISO": iso,
    }).reset_index(drop=True)


@st.cache_data(ttl=60 * 60)
def load_team_strikeouts_live():
    base = load_team_batting_split_live("vr")
    if base is None or base.empty:
        return pd.DataFrame(columns=["Teams", "Strikeouts/Game"])
    games = pd.to_numeric(base.get("Games", 0), errors="coerce").replace(0, pd.NA)
    so = pd.to_numeric(base.get("Strikeouts", 0), errors="coerce").fillna(0)
    return pd.DataFrame({
        "Teams": base.get("Teams", "").astype(str).str.strip(),
        "Strikeouts/Game": (so / games).fillna(8.5),
    })



@st.cache_data(ttl=60 * 60)
def _mlb_teams_lookup():
    """Return MLB team id/name/abbreviation lookup from the official Stats API."""
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/teams",
            params={"sportId": 1, "activeStatus": "Yes"},
            timeout=25,
        )
        r.raise_for_status()
        teams = []
        for t in r.json().get("teams", []):
            name = t.get("name", "")
            if not name:
                continue
            if name in ["Oakland Athletics", "Sacramento Athletics"]:
                name = "Athletics"
            teams.append({
                "id": t.get("id"),
                "name": name,
                "abbr": t.get("abbreviation", ""),
                "club": t.get("clubName", ""),
            })
        return pd.DataFrame(teams)
    except Exception as e:
        st.warning(f"Could not load MLB teams list: {e}")
        return pd.DataFrame(columns=["id", "name", "abbr", "club"])

def _team_id_from_name(team_name):
    teams = _mlb_teams_lookup()
    if teams is None or teams.empty:
        return None
    name = _normalize_mlb_team_name(team_name)
    lookup = teams.copy()
    lookup["name_norm"] = lookup["name"].astype(str).apply(_normalize_mlb_team_name)
    row = lookup[lookup["name_norm"] == name]
    if row.empty:
        row = lookup[lookup["abbr"].astype(str).str.upper() == str(team_name).upper()]
    if row.empty:
        return None
    try:
        return int(row.iloc[0]["id"])
    except Exception:
        return None


def _parse_ip_to_float(value):
    try:
        text = str(value)
        if "." not in text:
            return float(text)
        whole, frac = text.split(".", 1)
        outs = int(frac[:1] or 0)
        return float(whole) + outs / 3.0
    except Exception:
        return 0.0


@st.cache_data(ttl=30 * 60, show_spinner=False)
def fetch_bullpen_fatigue_for_team(team_name, as_of_date_str=None):
    """Approximate bullpen fatigue using official MLB boxscores from recent games.

    It sums reliever pitches and innings over the previous three calendar days.
    The game starter is excluded when probablePitcher is available; if not, the
    first pitcher listed for that team is treated as the starter.
    """
    if as_of_date_str is None:
        as_of_date_str = date.today().strftime("%Y-%m-%d")
    team_id = _team_id_from_name(team_name)
    if not team_id:
        return {
            "Team": team_name,
            "1D Pitches": 0,
            "3D Pitches": 0,
            "3D Bullpen IP": 0.0,
            "Reliever Appearances": 0,
            "High-Stress Arms 1D": 0,
            "High-Stress Arms 3D": 0,
            "Back-to-Back Arms": 0,
            "Leverage/Fatigue Adj": 0.0,
            "Fatigue Adj": 0.0,
            "Status": "Team ID unavailable; no bullpen fatigue adjustment.",
        }

    try:
        end_dt = datetime.strptime(as_of_date_str, "%Y-%m-%d").date()
    except Exception:
        end_dt = date.today()
    start_dt = end_dt - pd.Timedelta(days=3)
    end_prev = end_dt - pd.Timedelta(days=1)

    total_pitches_1d = 0
    total_pitches_3d = 0
    total_ip_3d = 0.0
    reliever_apps = 0
    high_stress_1d = 0
    high_stress_3d = 0
    reliever_days = {}

    try:
        sched = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={
                "sportId": 1,
                "teamId": team_id,
                "startDate": start_dt.strftime("%Y-%m-%d"),
                "endDate": end_prev.strftime("%Y-%m-%d"),
                "hydrate": "probablePitcher",
            },
            timeout=20,
        ).json()
        for day in sched.get("dates", []):
            game_date = day.get("date", "")
            for game in day.get("games", []):
                if str(game.get("status", {}).get("abstractGameState", "")).lower() != "final":
                    continue
                game_pk = game.get("gamePk")
                teams = game.get("teams", {})
                side = None
                for possible in ["home", "away"]:
                    if int(teams.get(possible, {}).get("team", {}).get("id", -1)) == int(team_id):
                        side = possible
                        break
                if not side or not game_pk:
                    continue

                starter_id = (teams.get(side, {}).get("probablePitcher", {}) or {}).get("id")
                box = requests.get(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore", timeout=20).json()
                team_box = box.get("teams", {}).get(side, {})
                pitcher_ids = team_box.get("pitchers", []) or []
                if not starter_id and pitcher_ids:
                    starter_id = pitcher_ids[0]

                for pid in pitcher_ids:
                    if starter_id and int(pid) == int(starter_id):
                        continue
                    player = team_box.get("players", {}).get(f"ID{pid}", {})
                    pstat = (player.get("stats", {}) or {}).get("pitching", {}) or {}
                    pitches = int(pstat.get("numberOfPitches", 0) or 0)
                    ip = _parse_ip_to_float(pstat.get("inningsPitched", 0))
                    if pitches <= 0 and ip <= 0:
                        continue
                    reliever_apps += 1
                    total_pitches_3d += pitches
                    total_ip_3d += ip
                    reliever_days.setdefault(str(pid), set()).add(str(game_date))
                    if pitches >= 25:
                        high_stress_3d += 1
                    if str(game_date) == end_prev.strftime("%Y-%m-%d"):
                        total_pitches_1d += pitches
                        if pitches >= 20:
                            high_stress_1d += 1
    except Exception:
        return {
            "Team": team_name,
            "1D Pitches": 0,
            "3D Pitches": 0,
            "3D Bullpen IP": 0.0,
            "Reliever Appearances": 0,
            "High-Stress Arms 1D": 0,
            "High-Stress Arms 3D": 0,
            "Back-to-Back Arms": 0,
            "Leverage/Fatigue Adj": 0.0,
            "Fatigue Adj": 0.0,
            "Status": "Could not load recent boxscores; no bullpen fatigue adjustment.",
        }

    # Negative score adjustment for tired bullpens. This is intentionally modest:
    # about -1 to -6 moneyline score points depending on recent workload.
    fatigue = 0.0
    if total_pitches_1d >= 95:
        fatigue -= 3.0
    elif total_pitches_1d >= 65:
        fatigue -= 2.0
    elif total_pitches_1d >= 40:
        fatigue -= 1.0

    if total_pitches_3d >= 240:
        fatigue -= 3.0
    elif total_pitches_3d >= 180:
        fatigue -= 2.0
    elif total_pitches_3d >= 130:
        fatigue -= 1.0

    if total_ip_3d >= 13:
        fatigue -= 2.0
    elif total_ip_3d >= 9:
        fatigue -= 1.0

    back_to_back_arms = 0
    day_keys = sorted({d for days in reliever_days.values() for d in days})
    for _, days in reliever_days.items():
        days = sorted(days)
        for i in range(1, len(days)):
            try:
                d0 = datetime.strptime(days[i-1], "%Y-%m-%d").date()
                d1 = datetime.strptime(days[i], "%Y-%m-%d").date()
                if (d1 - d0).days == 1:
                    back_to_back_arms += 1
                    break
            except Exception:
                continue

    leverage_fatigue = 0.0
    if high_stress_1d >= 3:
        leverage_fatigue -= 2.0
    elif high_stress_1d >= 2:
        leverage_fatigue -= 1.25
    elif high_stress_1d >= 1:
        leverage_fatigue -= 0.5

    if high_stress_3d >= 6:
        leverage_fatigue -= 2.0
    elif high_stress_3d >= 4:
        leverage_fatigue -= 1.25
    elif high_stress_3d >= 2:
        leverage_fatigue -= 0.5

    if back_to_back_arms >= 3:
        leverage_fatigue -= 1.5
    elif back_to_back_arms >= 2:
        leverage_fatigue -= 1.0
    elif back_to_back_arms >= 1:
        leverage_fatigue -= 0.5

    # Keep this as a modest moneyline context layer. It should refine bullpen
    # game and late-game risk, not overpower starter/team projections.
    fatigue += leverage_fatigue
    fatigue = max(-9.0, min(0.0, fatigue))
    if fatigue <= -6:
        status = "Heavy recent bullpen usage and/or key-arm stress; moneyline pitching score downgraded."
    elif fatigue < 0:
        status = "Some recent bullpen usage/key-arm stress; small moneyline downgrade."
    else:
        status = "Normal recent bullpen usage; neutral adjustment."

    return {
        "Team": team_name,
        "1D Pitches": int(total_pitches_1d),
        "3D Pitches": int(total_pitches_3d),
        "3D Bullpen IP": round(total_ip_3d, 1),
        "Reliever Appearances": int(reliever_apps),
        "High-Stress Arms 1D": int(high_stress_1d),
        "High-Stress Arms 3D": int(high_stress_3d),
        "Back-to-Back Arms": int(back_to_back_arms),
        "Leverage/Fatigue Adj": round(leverage_fatigue, 1),
        "Fatigue Adj": round(fatigue, 1),
        "Status": status,
    }


def build_bullpen_fatigue_df(away_team, home_team, slate_date):
    return pd.DataFrame([
        fetch_bullpen_fatigue_for_team(away_team, slate_date),
        fetch_bullpen_fatigue_for_team(home_team, slate_date),
    ])


def _bullpen_fatigue_adjustment(team, bullpen_fatigue_df):
    if bullpen_fatigue_df is None or bullpen_fatigue_df.empty:
        return 0.0
    try:
        temp = bullpen_fatigue_df.copy()
        row = temp[temp["Team"].astype(str).str.strip() == str(team).strip()]
        if row.empty:
            return 0.0
        return float(row.iloc[0].get("Fatigue Adj", 0.0) or 0.0)
    except Exception:
        return 0.0





@st.cache_data(ttl=60 * 60)
def _legacy_load_bullpen_stats_live_v1():
    """Neutral bullpen composite fallback without Fangraphs scraping.

    Mobile moneyline still accepts bullpen_stats, but this avoids hard failures
    if no dedicated bullpen source is available in Render.
    """
    teams = sorted(set(TEAM_ABBR_MAP.values()))
    return pd.DataFrame({
        "Teams": teams,
        "era": 4.10,
        "K%": 0.225,
        "BB%": 0.085,
        "WHIP": 1.28,
        "xwOBA": 0.320,
    })


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
            away_name = away.get("team", {}).get("name", "")
            home_name = home.get("team", {}).get("name", "")
            game_pk = game.get("gamePk")
            game_number = game.get("gameNumber", "")
            double_header = str(game.get("doubleHeader", "N")).upper()
            game_label = f"{away_name} at {home_name}"
            try:
                game_number_int = int(game_number)
            except Exception:
                game_number_int = 0
            if double_header in ["Y", "S"] or game_number_int > 1:
                game_label = f"{game_label} (Game {game_number_int or game_number})"

            games.append({
                "game_pk": str(game_pk),
                "game_id": str(game_pk),
                "game_number": game_number,
                "game_label": game_label,
                "game_time": game.get("gameDate"),
                "away_team": away_name,
                "home_team": home_name,
                # MLB API returns probable pitchers as First Last; your Excel tables use Last, First.
                "away_pitcher": to_last_first(away.get("probablePitcher", {}).get("fullName", "")),
                "home_pitcher": to_last_first(home.get("probablePitcher", {}).get("fullName", "")),
                "venue_name": (game.get("venue", {}) or {}).get("name", ""),
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


def run_today_model_for_games(today_games, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, nrfi_pitchers, nrfi_rhp, nrfi_lhp, pitcher_arsenal_df=None, team_pitch_type_df=None):
    rows = []
    if today_games is None or today_games.empty:
        return pd.DataFrame()
    for _, game in today_games.iterrows():
        away_team, home_team = game.get("away_team", ""), game.get("home_team", "")
        away_pitcher, home_pitcher = game.get("away_pitcher", ""), game.get("home_pitcher", "")
        if not away_pitcher or not home_pitcher:
            rows.append({"Away Team": away_team, "Home Team": home_team, "Away Pitcher": away_pitcher or "TBD", "Home Pitcher": home_pitcher or "TBD", "Status": "Missing probable pitcher"})
            continue
        home_k = expected_strikeouts(home_pitcher, away_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df)
        away_k = expected_strikeouts(away_pitcher, home_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df)
        home_k_6ip = six_inning_strikeouts(home_pitcher, away_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df)
        away_k_6ip = six_inning_strikeouts(away_pitcher, home_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df)
        nrfi_prob = nrfi_probability(home_team, away_team, home_pitcher, away_pitcher, pitcher_this_year, pitcher_last_year, nrfi_pitchers, nrfi_rhp, nrfi_lhp)
        nrfi_score = nrfi_score_formula(nrfi_prob)
        nrfi_grade = nrfi_bet_grade(nrfi_score)
        home_win_prob, away_win_prob = moneyline_probability(home_team, away_team, home_pitcher, away_pitcher, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp)
        home_ml_odds = game.get("home_ml_odds", None)
        away_ml_odds = game.get("away_ml_odds", None)
        home_implied, away_implied, home_fair_implied, away_fair_implied = _two_way_no_vig_probabilities(home_ml_odds, away_ml_odds)
        home_ml_edge = home_win_prob - home_fair_implied
        away_ml_edge = away_win_prob - away_fair_implied
        rows.append({
            "Away Team": away_team, "Home Team": home_team, "Away Pitcher": away_pitcher, "Home Pitcher": home_pitcher,
            "Away Expected K": round(away_k, 2), "Away 6-IP Pace": round(away_k_6ip, 2),
            "Home Expected K": round(home_k, 2), "Home 6-IP Pace": round(home_k_6ip, 2),
            "NRFI %": round(nrfi_prob * 100, 1), "NRFI Score": round(nrfi_score, 1), "NRFI Grade": nrfi_grade,
            "Home Win %": round(home_win_prob * 100, 1), "Away Win %": round(away_win_prob * 100, 1),
            "Home ML Odds": home_ml_odds, "Home Book": game.get("home_book", ""), "Home Implied %": round(home_implied * 100, 1), "Home ML Edge %": round(home_ml_edge * 100, 1), "Home ML Grade": moneyline_grade(home_ml_edge),
            "Away ML Odds": away_ml_odds, "Away Book": game.get("away_book", ""), "Away Implied %": round(away_implied * 100, 1), "Away ML Edge %": round(away_ml_edge * 100, 1), "Away ML Grade": moneyline_grade(away_ml_edge),
            "Status": game.get("status", "")
        })
    return pd.DataFrame(rows)


def build_auto_pitcher_k_board(today_games, k_market, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df=None, team_pitch_type_df=None):
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
            exp_k = expected_strikeouts(pitcher, p["opponent"], pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df)
            six_k = six_inning_strikeouts(pitcher, p["opponent"], pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df)
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
                "Projection": round(exp_k, 2), "6-IP Pace": round(six_k, 2), "Line": line, "Edge": round(edge, 2),
                "Bet Side": bet_side, "Recommendation": grade, "Best Odds": best_odds, "Best Book": best_book,
                "Volatility": volatility, "K Score": k_score
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("K Score", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=1800, show_spinner="Loading live MLB/Savant model data...")
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
        # Arsenal data is intentionally lazy-loaded inside Matchup Builder.
        # This keeps tracker/records pages usable even when Savant is slow.
        "pitcher_arsenal_df": pd.DataFrame(),
        "team_pitch_type_df": pd.DataFrame(),
    }


def _norm_game_text(value):
    import re
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def get_saved_game_filters_for_date(selected_date):
    """Return saved game ids and matchup labels for hiding games already saved.

    Uses the public-site-compatible Game Key column and also falls back to
    Away/Home team matching so the
    game disappears even if the saved row came from a different app version.
    """
    slate_df = load_slate()
    slate_date = selected_date.strftime("%Y-%m-%d") if hasattr(selected_date, "strftime") else str(selected_date)
    saved_ids = set()
    saved_labels = set()
    saved_matchups = set()

    if slate_df is None or slate_df.empty or "Date" not in slate_df.columns:
        return saved_ids, saved_labels, saved_matchups

    saved_for_date = slate_df[slate_df["Date"].astype(str).str.strip() == slate_date].copy()
    if saved_for_date.empty:
        return saved_ids, saved_labels, saved_matchups

    for id_col in ["Game Key"]:
        if id_col in saved_for_date.columns:
            for value in saved_for_date[id_col].astype(str).tolist():
                value = str(value).strip()
                if value and value.lower() not in ["nan", "none"]:
                    saved_ids.add(value)

    if "Game Label" in saved_for_date.columns:
        for value in saved_for_date["Game Label"].astype(str).tolist():
            label = _norm_game_text(value)
            if label:
                saved_labels.add(label)

    if "Away Team" in saved_for_date.columns and "Home Team" in saved_for_date.columns:
        for _, row in saved_for_date.iterrows():
            away = _norm_game_text(row.get("Away Team", ""))
            home = _norm_game_text(row.get("Home Team", ""))
            if away and home:
                saved_matchups.add(f"{away} at {home}")

    return saved_ids, saved_labels, saved_matchups


def _legacy_render_auto_matchup_builder_v1(pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, nrfi_pitchers, nrfi_rhp, nrfi_lhp, pitcher_arsenal_df=None, team_pitch_type_df=None, bullpen_stats=None, bullpen_fatigue_df=None):
    maybe_auto_update_pitcher_recent_form()
    st.header("Daily Slate (Auto Live)")
    if pitcher_arsenal_df is None or pitcher_arsenal_df.empty:
        pitcher_arsenal_df = load_pitch_arsenal_stats_live(2026, "pitcher")
    if team_pitch_type_df is None or team_pitch_type_df.empty:
        team_pitch_type_df = load_pitch_arsenal_stats_live(2026, "batter")
    selected_date = st.date_input("Slate Date", value=date.today(), key="auto_slate_date")
    today_games = pull_today_mlb_games(selected_date.strftime("%Y-%m-%d"))
    if today_games.empty:
        st.info("No MLB games found for this date or schedule source did not load.")
        return
    odds_games = pull_mlb_moneyline_odds(odds_api_key) if odds_api_key else pd.DataFrame()
    if not odds_games.empty:
        today_games = today_games.merge(odds_games, on=["home_team", "away_team"], how="left")
    auto_model = run_today_model_for_games(today_games, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, nrfi_pitchers, nrfi_rhp, nrfi_lhp, pitcher_arsenal_df, team_pitch_type_df)
    st.subheader("Game Model Outputs")
    st.dataframe(styled_dataframe(auto_model), use_container_width=True)
    if odds_api_key and not odds_games.empty:
        k_props_raw = pull_today_pitcher_k_props(odds_api_key, odds_games)
        k_market = summarize_pitcher_k_market(k_props_raw)
        pitcher_k_board = build_auto_pitcher_k_board(today_games, k_market, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df)
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
        st.info("The Odds tab only fetches moneylines. Pitcher K props stay manual to save credits.")



def render_auto_matchup_builder(pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, nrfi_pitchers, nrfi_rhp, nrfi_lhp, pitcher_arsenal_df=None, team_pitch_type_df=None, bullpen_stats=None, bullpen_fatigue_df=None):
    maybe_auto_update_pitcher_recent_form()
    st.header("Auto Matchup Builder")
    st.caption("Teams and probable pitchers come from the MLB schedule. Odds/lines default from your saved daily odds snapshot when available, but you can still edit them manually.")

    if pitcher_arsenal_df is None or pitcher_arsenal_df.empty:
        pitcher_arsenal_df = load_pitch_arsenal_stats_live(2026, "pitcher")
    if team_pitch_type_df is None or team_pitch_type_df.empty:
        team_pitch_type_df = load_pitch_arsenal_stats_live(2026, "batter")

    if pitcher_arsenal_df.empty or team_pitch_type_df.empty:
        st.warning("Pitch-type arsenal tables did not load, so K projections will use neutral 0.00 arsenal modifiers today.")
    else:
        st.caption(f"Pitch-type arsenal data loaded: {len(pitcher_arsenal_df)} pitcher pitch rows and {len(team_pitch_type_df)} hitter/team pitch rows.")

    if bullpen_stats is None or bullpen_stats.empty:
        bullpen_stats = load_bullpen_stats_live()


    selected_date = st.date_input("Slate Date", value=date.today(), key="manual_auto_slate_date")
    today_games = pull_today_mlb_games(selected_date.strftime("%Y-%m-%d"))

    if today_games.empty:
        st.info("No MLB games found for this date, or the MLB schedule did not load.")
        return

    today_games = today_games.copy()
    # Defensive schedule-column normalization. Older cached MLB schedule rows may not
    # include venue_name after a deployment, so never assume optional MLB API fields exist.
    for _required_col in ["game_label", "game_time", "away_team", "home_team", "away_pitcher", "home_pitcher", "status"]:
        if _required_col not in today_games.columns:
            today_games[_required_col] = ""
    if "venue_name" not in today_games.columns:
        today_games["venue_name"] = today_games["home_team"].apply(lambda _team: get_park_environment_profile(_team).get("park", ""))
    today_games["Game"] = today_games.apply(lambda r: r.get("game_label", "") or f'{r["away_team"]} at {r["home_team"]}', axis=1)

    # Remove games already saved for the selected date from the matchup dropdown.
    slate_df = load_slate()
    slate_date = selected_date.strftime("%Y-%m-%d")
    odds_snapshot = get_snapshot_for_date(slate_date)
    if odds_snapshot.empty:
        st.info("No moneyline snapshot saved for this date yet. Go to the Odds tab and fetch it once, or enter moneylines manually below.")
    else:
        snap_time_values = odds_snapshot["Snapshot Time ET"].astype(str).replace("", pd.NA).dropna()
        snap_time_label = snap_time_values.iloc[0] if not snap_time_values.empty else "saved today"
        st.success(f"Using saved moneyline snapshot: {snap_time_label}. No API credits used in this builder.")

    saved_game_ids, saved_game_labels, saved_matchups = get_saved_game_filters_for_date(selected_date)

    if "game_id" not in today_games.columns:
        today_games["game_id"] = today_games.get("game_pk", "")
    today_games["game_id"] = today_games["game_id"].astype(str).str.strip()
    today_games["_norm_label"] = today_games["Game"].apply(_norm_game_text)
    today_games["_norm_matchup"] = (today_games["away_team"].apply(_norm_game_text) + " at " + today_games["home_team"].apply(_norm_game_text))

    unsaved_games = today_games[
        (~today_games["game_id"].isin(saved_game_ids)) &
        (~today_games["_norm_label"].isin(saved_game_labels)) &
        (~today_games["_norm_matchup"].isin(saved_matchups))
    ].copy()

    with st.expander("Today's MLB Schedule / Probable Pitchers", expanded=True):
        schedule_display_cols = ["game_label", "game_time", "away_team", "home_team", "venue_name", "away_pitcher", "home_pitcher", "status"]
        st.dataframe(
            today_games[[c for c in schedule_display_cols if c in today_games.columns]],
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
    game_key = str(game.get("game_pk", game.get("game_id", "")))
    game_label = game.get("game_label", "") or f"{away_team} at {home_team}"

    venue_name = str(game.get("venue_name", "") or get_park_environment_profile(home_team).get("park", ""))
    auto_weather = fetch_game_city_weather(
        home_team,
        venue_name=venue_name,
        game_time=str(game.get("game_time", "")),
        game_date=selected_date.strftime("%Y-%m-%d"),
        away_team=away_team,
    )
    auto_temp_default = float(auto_weather.get("temperature", 72.0) or 72.0)
    auto_wind_default = float(auto_weather.get("wind_speed", 0.0) or 0.0)
    auto_wind_direction_default = str(auto_weather.get("wind_direction_label", "Neutral/Cross") or "Neutral/Cross")

    with st.expander("Park + Weather Environment", expanded=False):
        park_profile = get_park_environment_profile(home_team, venue_name)
        st.caption("Uniform model layer: every park gets a park factor, and OddsTrader auto-fills MLB-specific stadium weather when available. Manual fields below can override the auto values.")
        st.caption(auto_weather.get("status", ""))
        env_col1, env_col2 = st.columns(2)
        with env_col1:
            environment_temperature = st.number_input(
                "Temperature (°F)",
                value=auto_temp_default,
                step=1.0,
                key=f"env_temp_{game.get('game_pk')}",
            )
            roof_options = ["Open/Outdoor", "Dome/Roof Closed", "Retractable Open", "Retractable Closed"]
            auto_roof_default = str(auto_weather.get("roof_status", "") or "")
            if auto_roof_default in roof_options:
                roof_index = roof_options.index(auto_roof_default)
            else:
                roof_index = 1 if park_profile.get("roof") else 0
            environment_roof_status = st.selectbox(
                "Roof / Dome Status",
                roof_options,
                index=roof_index,
                key=f"env_roof_{game.get('game_pk')}",
            )
        with env_col2:
            environment_wind_speed = st.number_input(
                "Wind Speed (mph)",
                value=auto_wind_default,
                step=1.0,
                key=f"env_wind_{game.get('game_pk')}",
            )
            wind_direction_options = ["Neutral/Cross", "Out to OF", "In from OF"]
            try:
                wind_direction_index = wind_direction_options.index(auto_wind_direction_default)
            except Exception:
                wind_direction_index = 0
            environment_wind_direction = st.selectbox(
                "Wind Direction",
                wind_direction_options,
                index=wind_direction_index,
                key=f"env_wind_dir_{game.get('game_pk')}",
            )

    game_environment = build_game_environment(
        home_team,
        venue_name=venue_name,
        temperature=environment_temperature,
        wind_speed=environment_wind_speed,
        wind_direction=environment_wind_direction,
        roof_status=environment_roof_status,
    )
    game_environment["auto_weather"] = auto_weather
    if str(auto_weather.get("source", "")).startswith("OddsTrader"):
        game_environment["weather_source"] = auto_weather.get("source", "")
        game_environment["rain_pct"] = auto_weather.get("rain_pct", "")
        game_environment["delay_risk"] = auto_weather.get("delay_risk", "")
        game_environment["batting_impact"] = auto_weather.get("batting_impact", "")
        game_environment["pitching_impact"] = auto_weather.get("pitching_impact", "")
    st.caption(f"Environment: {game_environment.get('status', '')} {game_environment.get('weather_status', '')}")
    if str(auto_weather.get("source", "")).startswith("OddsTrader"):
        st.caption(f"OddsTrader: {auto_weather.get('delay_risk', '')} | Rain {auto_weather.get('rain_pct', '')}% | Batting {auto_weather.get('batting_impact', 'N/A')} | Pitching {auto_weather.get('pitching_impact', 'N/A')}")

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

    if bullpen_fatigue_df is None or bullpen_fatigue_df.empty:
        bullpen_fatigue_df = build_bullpen_fatigue_df(away_team, home_team, selected_date.strftime("%Y-%m-%d"))


    # Pre-calculate projections so saved snapshot defaults can choose the correct K side odds.
    # Raw Expected K comes from pitcher/team/arsenal logic. Then the confirmed-lineup
    # layer applies the PC-style 70/30 lineup blend, handedness stack, and hitter
    # pitch-type matchup when MLB has lineups posted.
    home_k_raw, home_arsenal_details = expected_strikeouts(home_pitcher, away_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df, return_details=True)
    away_k_raw, away_arsenal_details = expected_strikeouts(away_pitcher, home_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df, return_details=True)

    home_throw = get_value(pitcher_this_year, "Player", home_pitcher, "Throws", None)
    if home_throw is None:
        home_throw = get_value(pitcher_last_year, "Player", home_pitcher, "Throws", "R")
    away_throw = get_value(pitcher_this_year, "Player", away_pitcher, "Throws", None)
    if away_throw is None:
        away_throw = get_value(pitcher_last_year, "Player", away_pitcher, "Throws", "R")

    home_lineup_details = build_lineup_k_blend_details(game_key, "away", away_team, home_throw, team_batting_rhp, team_batting_lhp, home_pitcher, pitcher_arsenal_df, team_pitch_type_df)
    away_lineup_details = build_lineup_k_blend_details(game_key, "home", home_team, away_throw, team_batting_rhp, team_batting_lhp, away_pitcher, pitcher_arsenal_df, team_pitch_type_df)
    home_k = apply_lineup_k_adjustment(home_k_raw, home_lineup_details)
    away_k = apply_lineup_k_adjustment(away_k_raw, away_lineup_details)
    # V15: save the controlled lineup rate adjustment inside the pitcher snapshot
    # so the under-confluence gate and historical audit can use the exact value.
    home_arsenal_details["lineup_rate_multiplier"] = float(home_lineup_details.get("projection_multiplier", 1.0) or 1.0)
    away_arsenal_details["lineup_rate_multiplier"] = float(away_lineup_details.get("projection_multiplier", 1.0) or 1.0)
    home_arsenal_details["lineup_k_rate"] = home_lineup_details.get("lineup_k_rate", "")
    away_arsenal_details["lineup_k_rate"] = away_lineup_details.get("lineup_k_rate", "")

    home_k_context = build_pitcher_k_context(
        home_pitcher,
        home_team,
        away_team,
        game_environment,
        pitcher_this_year,
        pitcher_last_year,
        home_arsenal_details,
        slate_date_text=slate_date,
    )
    away_k_context = build_pitcher_k_context(
        away_pitcher,
        away_team,
        home_team,
        game_environment,
        pitcher_this_year,
        pitcher_last_year,
        away_arsenal_details,
        slate_date_text=slate_date,
    )
    home_k_environment_adjustment = float(home_k_context.get("k_projection_adjustment", 0.0) or 0.0)
    away_k_environment_adjustment = float(away_k_context.get("k_projection_adjustment", 0.0) or 0.0)
    home_k = apply_k_context_projection(home_k, home_k_context)
    away_k = apply_k_context_projection(away_k, away_k_context)
    home_arsenal_details["game_environment"] = game_environment
    away_arsenal_details["game_environment"] = game_environment
    home_arsenal_details["k_context"] = home_k_context
    away_arsenal_details["k_context"] = away_k_context

    # V14 rolling calibration: first correct global compression, then apply small
    # pitcher-specific and opponent-specific residuals. Raw values remain saved.
    home_k_precalibration = float(home_k)
    away_k_precalibration = float(away_k)
    home_recent_form = get_pitcher_recent_form_summary(home_pitcher)
    away_recent_form = get_pitcher_recent_form_summary(away_pitcher)
    home_vol = strikeout_volatility(home_pitcher, pitcher_this_year, pitcher_last_year)
    away_vol = strikeout_volatility(away_pitcher, pitcher_this_year, pitcher_last_year)
    home_data_health = build_projection_data_health(home_lineup_details, home_arsenal_details, auto_weather)
    away_data_health = build_projection_data_health(away_lineup_details, away_arsenal_details, auto_weather)
    home_archetype = (home_k_context.get("archetype", {}) or {}).get("bucket", "")
    away_archetype = (away_k_context.get("archetype", {}) or {}).get("bucket", "")
    home_skill_snapshot = pitcher_skill_snapshot(home_pitcher, pitcher_this_year, pitcher_last_year, home_arsenal_details)
    away_skill_snapshot = pitcher_skill_snapshot(away_pitcher, pitcher_this_year, pitcher_last_year, away_arsenal_details)
    home_k_calibration = calibrate_pitcher_projection(home_k_precalibration, home_pitcher, away_team, home_vol, home_lineup_details, recent_form=home_recent_form, archetype=home_archetype, data_health=home_data_health)
    away_k_calibration = calibrate_pitcher_projection(away_k_precalibration, away_pitcher, home_team, away_vol, away_lineup_details, recent_form=away_recent_form, archetype=away_archetype, data_health=away_data_health)
    home_k = float(home_k_calibration["final_projection"])
    away_k = float(away_k_calibration["final_projection"])

    home_ml_default, away_ml_default = get_moneyline_defaults_from_snapshot(odds_snapshot, home_team, away_team)
    home_k_defaults = get_pitcher_k_snapshot_defaults(odds_snapshot, home_pitcher, projected_k=home_k)
    away_k_defaults = get_pitcher_k_snapshot_defaults(odds_snapshot, away_pitcher, projected_k=away_k)

    st.divider()
    st.subheader("Market Inputs")
    st.caption("Moneyline defaults come from the saved odds snapshot when available. Pitcher K and NRFI/YRFI inputs stay manual to save API credits.")

    input_col1, input_col2 = st.columns(2)
    with input_col1:
        home_k_line = st.number_input(f"{home_pitcher} K Line", value=float(home_k_defaults["line"]), step=0.5, key=f"home_k_{game.get('game_pk')}")
        home_k_odds = st.number_input(f"{home_pitcher} K Odds", value=int(home_k_defaults["odds"]), step=5, key=f"home_k_odds_{game.get('game_pk')}")
        home_ml_odds = st.number_input(f"{home_team} Moneyline Odds", value=int(home_ml_default), step=5, key=f"home_ml_{game.get('game_pk')}")
    with input_col2:
        away_k_line = st.number_input(f"{away_pitcher} K Line", value=float(away_k_defaults["line"]), step=0.5, key=f"away_k_{game.get('game_pk')}")
        away_k_odds = st.number_input(f"{away_pitcher} K Odds", value=int(away_k_defaults["odds"]), step=5, key=f"away_k_odds_{game.get('game_pk')}")
        away_ml_odds = st.number_input(f"{away_team} Moneyline Odds", value=int(away_ml_default), step=5, key=f"away_ml_{game.get('game_pk')}")

    # Compute the shared six-inning pace before opener controls so an opener can
    # be scaled to its announced workload without referencing an undefined value.
    home_k_6ip_raw = six_inning_strikeouts(home_pitcher, away_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df)
    away_k_6ip_raw = six_inning_strikeouts(away_pitcher, home_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df)
    home_k_6ip_precal = apply_k_context_projection(apply_lineup_k_adjustment(home_k_6ip_raw, home_lineup_details), home_k_context)
    away_k_6ip_precal = apply_k_context_projection(apply_lineup_k_adjustment(away_k_6ip_raw, away_lineup_details), away_k_context)

    st.markdown("### Opener / Planned Bulk Pitching Context")
    st.caption("When checked, the model searches recent news for a named bulk pitcher, lets you override it, and allocates opener + bulk + remaining bullpen separately. NRFI/YRFI still uses the opener.")
    bp_col1, bp_col2 = st.columns(2)
    with bp_col1:
        home_bulk_context = render_bulk_game_controls(home_team, home_pitcher, slate_date, game_key, "home")
    with bp_col2:
        away_bulk_context = render_bulk_game_controls(away_team, away_pitcher, slate_date, game_key, "away")
    use_home_bullpen = bool(home_bulk_context.get("enabled"))
    use_away_bullpen = bool(away_bulk_context.get("enabled"))

    # A listed opener is projected only for its announced opener workload.
    if use_home_bullpen:
        opener_ip = float(home_bulk_context.get("expected_opener_ip", 1.3) or 1.3)
        home_k = min(home_k, max(0.0, home_k_6ip_precal * opener_ip / 6.0))
        home_k_calibration["final_projection"] = round(home_k, 3)
        home_k_calibration["reliability"]["score"] = min(58.0, max(35.0, float(home_k_calibration["reliability"]["score"]) - 14.0))
    if use_away_bullpen:
        opener_ip = float(away_bulk_context.get("expected_opener_ip", 1.3) or 1.3)
        away_k = min(away_k, max(0.0, away_k_6ip_precal * opener_ip / 6.0))
        away_k_calibration["final_projection"] = round(away_k, 3)
        away_k_calibration["reliability"]["score"] = min(58.0, max(35.0, float(away_k_calibration["reliability"]["score"]) - 14.0))

    home_bulk_projection = build_bulk_pitcher_k_projection(
        home_bulk_context, away_team, game_key, "away", pitcher_this_year, pitcher_last_year,
        team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df,
        game_environment, home_team, slate_date
    ) if use_home_bullpen else None
    away_bulk_projection = build_bulk_pitcher_k_projection(
        away_bulk_context, home_team, game_key, "home", pitcher_this_year, pitcher_last_year,
        team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df,
        game_environment, away_team, slate_date
    ) if use_away_bullpen else None

    home_bulk_k_line = home_bulk_k_odds = away_bulk_k_line = away_bulk_k_odds = None
    if home_bulk_projection:
        st.markdown(f"**{home_team} bulk prop: {home_bulk_projection['pitcher']}**")
        c1, c2 = st.columns(2)
        with c1: home_bulk_k_line = st.number_input(f"{home_bulk_projection['pitcher']} K Line", value=max(0.5, round(home_bulk_projection['projection']*2)/2), step=0.5, key=f"home_bulk_k_line_{game_key}")
        with c2: home_bulk_k_odds = st.number_input(f"{home_bulk_projection['pitcher']} K Odds", value=-110, step=5, key=f"home_bulk_k_odds_{game_key}")
    if away_bulk_projection:
        st.markdown(f"**{away_team} bulk prop: {away_bulk_projection['pitcher']}**")
        c1, c2 = st.columns(2)
        with c1: away_bulk_k_line = st.number_input(f"{away_bulk_projection['pitcher']} K Line", value=max(0.5, round(away_bulk_projection['projection']*2)/2), step=0.5, key=f"away_bulk_k_line_{game_key}")
        with c2: away_bulk_k_odds = st.number_input(f"{away_bulk_projection['pitcher']} K Odds", value=-110, step=5, key=f"away_bulk_k_odds_{game_key}")

    total_input_col1, total_input_col2 = st.columns(2)
    with total_input_col1:
        total_runs_line = st.number_input("Game Total Line", value=8.5, step=0.5, key=f"total_runs_line_{game.get('game_pk')}")
    with total_input_col2:
        total_runs_odds = st.number_input("Game Total Odds", value=-110, step=5, key=f"total_runs_odds_{game.get('game_pk')}")
    nrfi_price_col, yrfi_price_col = st.columns(2)
    with nrfi_price_col:
        nrfi_odds = st.number_input("NRFI Odds", value=-110, step=5, key=f"nrfi_odds_{game.get('game_pk')}")
    with yrfi_price_col:
        yrfi_odds = st.number_input("YRFI Odds", value=-110, step=5, key=f"yrfi_odds_{game.get('game_pk')}")
    st.caption("Moneyline edges are graded against no-vig fair prices. Total and NRFI/YRFI grades require positive EV at the entered odds.")

    home_k_6ip = home_k_6ip_precal
    away_k_6ip = away_k_6ip_precal

    home_ipg_this, home_ipg_last = pitcher_ipg(home_pitcher, pitcher_this_year, pitcher_last_year)
    away_ipg_this, away_ipg_last = pitcher_ipg(away_pitcher, pitcher_this_year, pitcher_last_year)

    home_k_probs = k_market_probabilities(home_k, home_k_line, home_k_calibration["expected_std"])
    away_k_probs = k_market_probabilities(away_k, away_k_line, away_k_calibration["expected_std"])
    home_selected_prob = home_k_probs["over"] if home_k >= home_k_line else home_k_probs["under"]
    away_selected_prob = away_k_probs["over"] if away_k >= away_k_line else away_k_probs["under"]
    home_k_price_edge = home_selected_prob - american_odds_to_implied_prob(home_k_odds)
    away_k_price_edge = away_selected_prob - american_odds_to_implied_prob(away_k_odds)
    home_k_grade_raw, home_k_edge = strikeout_bet_grade(home_k, home_k_6ip, home_ipg_this, home_ipg_last, home_k_line, home_vol, odds=home_k_odds, reliability=home_k_calibration["reliability"]["score"], expected_std=home_k_calibration["expected_std"])
    away_k_grade_raw, away_k_edge = strikeout_bet_grade(away_k, away_k_6ip, away_ipg_this, away_ipg_last, away_k_line, away_vol, odds=away_k_odds, reliability=away_k_calibration["reliability"]["score"], expected_std=away_k_calibration["expected_std"])
    home_k_grade, home_recent_form_note = apply_recent_form_to_k_grade(home_k_grade_raw, home_recent_form)
    away_k_grade, away_recent_form_note = apply_recent_form_to_k_grade(away_k_grade_raw, away_recent_form)
    home_k_grade, home_recent_accuracy_note = apply_recent_accuracy_to_k_grade(
        home_k_grade, home_recent_form, home_k_edge
    )
    away_k_grade, away_recent_accuracy_note = apply_recent_accuracy_to_k_grade(
        away_k_grade, away_recent_form, away_k_edge
    )

    # A conservative expected-innings projection can hide an elite six-inning K pace.
    # Very Hot, directionally reliable recent performance can recover those overs,
    # while confirmed bullpen-game/opener situations remain blocked.
    home_k_grade, home_six_inning_override_note, home_six_inning_override = apply_six_inning_recent_form_override(
        home_k_grade, home_k_6ip, home_k_line, home_recent_form, hard_workload_risk=use_home_bullpen
    )
    away_k_grade, away_six_inning_override_note, away_six_inning_override = apply_six_inning_recent_form_override(
        away_k_grade, away_k_6ip, away_k_line, away_recent_form, hard_workload_risk=use_away_bullpen
    )

    home_weapon_floor_note = ""
    away_weapon_floor_note = ""
    home_k_grade, home_weapon_floor_note = apply_weapon_floor_to_k_grade(home_k_grade, home_arsenal_details)
    away_k_grade, away_weapon_floor_note = apply_weapon_floor_to_k_grade(away_k_grade, away_arsenal_details)

    home_k_context_note = ""
    away_k_context_note = ""
    home_k_grade, home_k_context_note = apply_k_context_to_grade(home_k_grade, home_k_context, home_k_edge, home_k_line)
    away_k_grade, away_k_context_note = apply_k_context_to_grade(away_k_grade, away_k_context, away_k_edge, away_k_line)

    home_k_score = pitcher_k_strength_score(home_k, home_k_6ip, home_k_line, home_vol, home_ipg_this, home_ipg_last, reliability=home_k_calibration["reliability"]["score"], selected_probability=home_selected_prob, price_edge=home_k_price_edge)
    away_k_score = pitcher_k_strength_score(away_k, away_k_6ip, away_k_line, away_vol, away_ipg_this, away_ipg_last, reliability=away_k_calibration["reliability"]["score"], selected_probability=away_selected_prob, price_edge=away_k_price_edge)

    def _grade_bulk_projection(pdata, line, odds):
        if not pdata or line is None or odds is None:
            return None
        cal = pdata["calibration"]
        probs = k_market_probabilities(pdata["projection"], line, cal["expected_std"])
        selected_prob = probs["over"] if pdata["projection"] >= line else probs["under"]
        price_edge = selected_prob - american_odds_to_implied_prob(odds)
        grade, edge = strikeout_bet_grade(pdata["projection"], pdata["six_ip"], pdata["expected_ip"], pdata["expected_ip"], line, pdata["volatility"], odds=odds, reliability=cal["reliability"]["score"], expected_std=cal["expected_std"])
        grade, recent_note = apply_recent_form_to_k_grade(grade, pdata["recent"])
        score = pitcher_k_strength_score(pdata["projection"], pdata["six_ip"], line, pdata["volatility"], pdata["expected_ip"], pdata["expected_ip"], reliability=cal["reliability"]["score"], selected_probability=selected_prob, price_edge=price_edge)
        pdata.update({"line": line, "odds": odds, "probabilities": probs, "selected_probability": selected_prob, "price_edge": price_edge, "grade": grade, "edge": edge, "score": score, "recent_note": recent_note})
        return pdata

    home_bulk_projection = _grade_bulk_projection(home_bulk_projection, home_bulk_k_line, home_bulk_k_odds)
    away_bulk_projection = _grade_bulk_projection(away_bulk_projection, away_bulk_k_line, away_bulk_k_odds)
    home_k_score = adjust_k_score_for_recent_override(
        home_k_score, home_k_grade, home_recent_form, override_applied=home_six_inning_override
    )
    away_k_score = adjust_k_score_for_recent_override(
        away_k_score, away_k_grade, away_recent_form, override_applied=away_six_inning_override
    )

    total_run_details = total_runs_projection(
        home_team,
        away_team,
        home_pitcher,
        away_pitcher,
        pitcher_this_year,
        pitcher_last_year,
        team_hitting,
        team_batting_rhp,
        team_batting_lhp,
        bullpen_stats=bullpen_stats,
        use_home_bullpen=use_home_bullpen,
        use_away_bullpen=use_away_bullpen,
        bullpen_fatigue_df=bullpen_fatigue_df,
        market_total=total_runs_line,
        total_odds=total_runs_odds,
        home_lineup_details=home_lineup_details,
        away_lineup_details=away_lineup_details,
        home_arsenal_details=home_arsenal_details,
        away_arsenal_details=away_arsenal_details,
        game_environment=game_environment,
        home_bulk_context=home_bulk_context,
        away_bulk_context=away_bulk_context
    )

    nrfi_prob, nrfi_model_details = nrfi_probability(
        home_team, away_team, home_pitcher, away_pitcher,
        pitcher_this_year, pitcher_last_year, nrfi_pitchers, nrfi_rhp, nrfi_lhp,
        home_lineup_details=home_lineup_details,
        away_lineup_details=away_lineup_details,
        game_environment=game_environment,
        return_details=True,
    )
    nrfi_environment = nrfi_yrfi_grade_from_environment(
        nrfi_prob,
        total_run_details,
        nrfi_odds=nrfi_odds,
        yrfi_odds=yrfi_odds,
        nrfi_details=nrfi_model_details,
    )
    nrfi_score = nrfi_environment["nrfi_score"]
    yrfi_score = nrfi_environment["yrfi_score"]
    nrfi_grade = nrfi_environment["grade"]
    selected_first_inning_score = (
        yrfi_score if "YRFI" in nrfi_grade else nrfi_score
    )
    selected_first_inning_probability = (
        (1.0 - nrfi_prob) if "YRFI" in nrfi_grade else nrfi_prob
    )
    selected_first_inning_odds = nrfi_environment.get("selected_odds", "")

    home_win_prob, away_win_prob, moneyline_details = moneyline_probability(
        home_team,
        away_team,
        home_pitcher,
        away_pitcher,
        pitcher_this_year,
        pitcher_last_year,
        team_hitting,
        team_batting_rhp,
        team_batting_lhp,
        bullpen_stats=bullpen_stats,
        use_home_bullpen=use_home_bullpen,
        use_away_bullpen=use_away_bullpen,
        bullpen_fatigue_df=bullpen_fatigue_df,
        home_lineup_details=home_lineup_details,
        away_lineup_details=away_lineup_details,
        home_arsenal_details=home_arsenal_details,
        away_arsenal_details=away_arsenal_details,
        game_environment=game_environment,
        return_details=True,
        home_bulk_context=home_bulk_context,
        away_bulk_context=away_bulk_context
    )

    home_implied, away_implied, home_fair_implied, away_fair_implied = _two_way_no_vig_probabilities(
        home_ml_odds, away_ml_odds
    )
    home_ml_edge = home_win_prob - home_fair_implied
    away_ml_edge = away_win_prob - away_fair_implied
    moneyline_details["market_pricing"] = {
        "home_raw_implied": round(home_implied, 4),
        "away_raw_implied": round(away_implied, 4),
        "home_no_vig_implied": round(home_fair_implied, 4),
        "away_no_vig_implied": round(away_fair_implied, 4),
        "hold": round((home_implied + away_implied) - 1.0, 4),
    }

    home_ml_confidence = moneyline_confidence_score("home", home_ml_edge, moneyline_details, odds=home_ml_odds)
    away_ml_confidence = moneyline_confidence_score("away", away_ml_edge, moneyline_details, odds=away_ml_odds)

    home_ml_confidence = apply_moneyline_k_confluence(
        "home",
        home_ml_confidence,
        home_k_grade,
        home_k_edge,
        away_k_grade,
        away_k_edge,
    )
    away_ml_confidence = apply_moneyline_k_confluence(
        "away",
        away_ml_confidence,
        away_k_grade,
        away_k_edge,
        home_k_grade,
        home_k_edge,
    )

    home_ml_grade = home_ml_confidence.get("grade", moneyline_grade(home_ml_edge))
    away_ml_grade = away_ml_confidence.get("grade", moneyline_grade(away_ml_edge))

    # Store the confidence layer inside the details object so it is visible in
    # the builder, saved matchup details, and any future public-page expansion.
    try:
        moneyline_details["home"]["confidence"] = home_ml_confidence
        moneyline_details["away"]["confidence"] = away_ml_confidence
        moneyline_details["confidence_status"] = "Moneyline v12: A=edge 8%+ with confidence/confluence and no risk flags; B=edge 5-8% clean confluence; failed A risks become Non-Edge, never B. Cross-market K confluence is applied after the base confidence score."
    except Exception:
        pass

    bullpen_context_note = []
    if use_home_bullpen:
        bullpen_context_note.append(f"{home_team}: bullpen context used for moneyline")
    if use_away_bullpen:
        bullpen_context_note.append(f"{away_team}: bullpen context used for moneyline")

    def _ml_grade_rank(grade):
        if grade == "A Moneyline":
            return 2
        if grade == "B Moneyline":
            return 1
        return 0

    home_candidate = {
        "team": home_team, "prob": home_win_prob, "odds": home_ml_odds, "grade": home_ml_grade,
        "edge": home_ml_edge, "confidence": home_ml_confidence.get("confidence_score", 0),
    }
    away_candidate = {
        "team": away_team, "prob": away_win_prob, "odds": away_ml_odds, "grade": away_ml_grade,
        "edge": away_ml_edge, "confidence": away_ml_confidence.get("confidence_score", 0),
    }
    ranked_candidates = sorted(
        [home_candidate, away_candidate],
        key=lambda x: (_ml_grade_rank(x.get("grade")), float(x.get("confidence", 0) or 0), float(x.get("edge", 0) or 0), float(x.get("prob", 0) or 0)),
        reverse=True,
    )
    best_candidate = ranked_candidates[0]
    better_ml_team, better_ml_prob, better_ml_odds, better_ml_grade = best_candidate["team"], best_candidate["prob"], best_candidate["odds"], best_candidate["grade"]

    st.divider()
    st.subheader("Strikeout Projections")
    col3, col4 = st.columns(2)
    with col3:
        st.markdown(f"### {home_pitcher}")
        render_builder_metric_grid([
            {"label": "Final K", "value": round(home_k, 2)},
            {"label": "Raw K", "value": round(home_k_precalibration, 2)},
            {"label": "Reliability", "value": f"{home_k_calibration['reliability']['score']}/100"},
            {"label": "Selected Prob", "value": f"{home_selected_prob*100:.1f}%"},
            {"label": "6-IP Pace", "value": round(home_k_6ip, 2)},
            {"label": "Line", "value": home_k_line},
            {"label": "Edge", "value": round(home_k_edge, 2)},
            {"label": "Variance (Proj - 6IP)", "value": round(home_k - home_k_6ip, 2)},
            {"label": "Volatility", "value": home_vol},
            {"label": "Recent Form", "value": _recent_form_display(home_recent_form), "wide": True},
            {"label": "Archetype", "value": (home_k_context.get("archetype", {}) or {}).get("bucket", "")},
            {"label": "Projected BF", "value": (home_arsenal_details.get("workload", {}) or {}).get("projected_bf", "")},
            {"label": "Matchup K Rate", "value": f"{float(home_arsenal_details.get('matchup_k_rate', 0) or 0)*100:.1f}%"},
            {"label": "Projected Pitches", "value": (home_arsenal_details.get("workload", {}) or {}).get("projected_pitches", "")},
            {"label": "Arsenal Rate", "value": f"{float((home_arsenal_details.get('rate_multipliers', {}) or {}).get('arsenal', 1.0))*100:.1f}%"},
            {"label": "Env K Adj", "value": f"{home_k_context.get('k_projection_adjustment', 0):+.2f}"},
            {"label": "Early Hook Risk", "value": home_k_context.get("early_hook_risk", "Low")},
            {"label": "Bet Grade", "value": home_k_grade, "wide": True, "big": True},
            {"label": "K Score", "value": home_k_score, "big": True},
        ])
        if home_recent_form_note:
            with st.container():
                st.markdown('<div class="builder-note-compact">', unsafe_allow_html=True)
                st.warning(home_recent_form_note)
                st.markdown('</div>', unsafe_allow_html=True)
        if home_recent_accuracy_note:
            with st.container():
                st.markdown('<div class="builder-note-compact">', unsafe_allow_html=True)
                st.warning(home_recent_accuracy_note)
                st.markdown('</div>', unsafe_allow_html=True)
        if home_six_inning_override_note:
            with st.container():
                st.markdown('<div class="builder-note-compact">', unsafe_allow_html=True)
                st.success(home_six_inning_override_note)
                st.markdown('</div>', unsafe_allow_html=True)
        if home_weapon_floor_note:
            with st.container():
                st.markdown('<div class="builder-note-compact">', unsafe_allow_html=True)
                st.warning(home_weapon_floor_note)
                st.markdown('</div>', unsafe_allow_html=True)
        if home_k_context_note:
            with st.container():
                st.markdown('<div class="builder-note-compact">', unsafe_allow_html=True)
                st.warning(home_k_context_note)
                st.markdown('</div>', unsafe_allow_html=True)
    with col4:
        st.markdown(f"### {away_pitcher}")
        render_builder_metric_grid([
            {"label": "Final K", "value": round(away_k, 2)},
            {"label": "Raw K", "value": round(away_k_precalibration, 2)},
            {"label": "Reliability", "value": f"{away_k_calibration['reliability']['score']}/100"},
            {"label": "Selected Prob", "value": f"{away_selected_prob*100:.1f}%"},
            {"label": "6-IP Pace", "value": round(away_k_6ip, 2)},
            {"label": "Line", "value": away_k_line},
            {"label": "Edge", "value": round(away_k_edge, 2)},
            {"label": "Variance (Proj - 6IP)", "value": round(away_k - away_k_6ip, 2)},
            {"label": "Volatility", "value": away_vol},
            {"label": "Recent Form", "value": _recent_form_display(away_recent_form), "wide": True},
            {"label": "Archetype", "value": (away_k_context.get("archetype", {}) or {}).get("bucket", "")},
            {"label": "Projected BF", "value": (away_arsenal_details.get("workload", {}) or {}).get("projected_bf", "")},
            {"label": "Matchup K Rate", "value": f"{float(away_arsenal_details.get('matchup_k_rate', 0) or 0)*100:.1f}%"},
            {"label": "Projected Pitches", "value": (away_arsenal_details.get("workload", {}) or {}).get("projected_pitches", "")},
            {"label": "Arsenal Rate", "value": f"{float((away_arsenal_details.get('rate_multipliers', {}) or {}).get('arsenal', 1.0))*100:.1f}%"},
            {"label": "Env K Adj", "value": f"{away_k_context.get('k_projection_adjustment', 0):+.2f}"},
            {"label": "Early Hook Risk", "value": away_k_context.get("early_hook_risk", "Low")},
            {"label": "Bet Grade", "value": away_k_grade, "wide": True, "big": True},
            {"label": "K Score", "value": away_k_score, "big": True},
        ])
        if away_recent_form_note:
            with st.container():
                st.markdown('<div class="builder-note-compact">', unsafe_allow_html=True)
                st.warning(away_recent_form_note)
                st.markdown('</div>', unsafe_allow_html=True)
        if away_recent_accuracy_note:
            with st.container():
                st.markdown('<div class="builder-note-compact">', unsafe_allow_html=True)
                st.warning(away_recent_accuracy_note)
                st.markdown('</div>', unsafe_allow_html=True)
        if away_six_inning_override_note:
            with st.container():
                st.markdown('<div class="builder-note-compact">', unsafe_allow_html=True)
                st.success(away_six_inning_override_note)
                st.markdown('</div>', unsafe_allow_html=True)
        if away_weapon_floor_note:
            with st.container():
                st.markdown('<div class="builder-note-compact">', unsafe_allow_html=True)
                st.warning(away_weapon_floor_note)
                st.markdown('</div>', unsafe_allow_html=True)
        if away_k_context_note:
            with st.container():
                st.markdown('<div class="builder-note-compact">', unsafe_allow_html=True)
                st.warning(away_k_context_note)
                st.markdown('</div>', unsafe_allow_html=True)


    with st.expander("V14 Calibration / Reliability", expanded=False):
        for pitcher_name, cal in [(home_pitcher, home_k_calibration), (away_pitcher, away_k_calibration)]:
            st.markdown(f"**{pitcher_name}**")
            st.write(cal.get("status", ""))
            st.write(f"Global fit: Actual K = {cal['global_fit']['intercept']:.2f} + {cal['global_fit']['slope']:.2f} × raw projection ({cal['global_fit']['sample']} samples)")
            st.write(f"Pitcher history: {cal['pitcher_history'].get('status', '')}")
            st.write(f"Opponent history: {cal['opponent_history'].get('status', '')}")
            st.write(f"Expected error SD: {cal['expected_std']:.2f} K | Reliability: {cal['reliability']['score']}/100")
            if cal['reliability'].get('reasons'): st.caption("; ".join(cal['reliability']['reasons']))
            st.divider()
        if home_bulk_projection:
            st.markdown(f"**Bulk: {home_bulk_projection['pitcher']}**")
            st.write(home_bulk_projection['calibration'].get('status',''))
        if away_bulk_projection:
            st.markdown(f"**Bulk: {away_bulk_projection['pitcher']}**")
            st.write(away_bulk_projection['calibration'].get('status',''))

    with st.expander("Recent Form Accuracy Diagnostics", expanded=False):
        st.caption("Direction uses weighted L3 (50/30/20). Accuracy and volatility use up to L5 so over- and under-predictions cannot cancel out.")
        for pitcher_name, recent in [(home_pitcher, home_recent_form), (away_pitcher, away_recent_form)]:
            st.markdown(f"**{pitcher_name}**")
            if not recent or not recent.get("starts", 0):
                st.write("No completed recent-form history.")
                continue
            d1, d2, d3 = st.columns(3)
            with d1:
                st.metric("Weighted Bias", f"{float(recent.get('weighted_bias', 0)):+.2f} K")
                st.metric("Direction", recent.get("direction_reliability", ""))
            with d2:
                st.metric("MAE", f"{float(recent.get('mae', 0)):.2f} K")
                st.metric("Accuracy", recent.get("accuracy", ""))
            with d3:
                st.metric("RMSE / Error SD", f"{float(recent.get('rmse', 0)):.2f} / {float(recent.get('error_std', 0)):.2f}")
                st.metric("Within 1.5 K", f"{float(recent.get('within_1_5_pct', 0)):.0f}%")
            recent_rows = recent.get("last5", [])
            if recent_rows:
                st.dataframe(pd.DataFrame(recent_rows), use_container_width=True, hide_index=True)
            st.divider()

    with st.expander("Pitcher K Context / Environment Risk", expanded=False):
        st.markdown(f"**{home_pitcher} vs {away_team}**")
        st.write(home_k_context.get("status", ""))
        st.write((home_k_context.get("archetype", {}) or {}).get("status", ""))
        st.write((home_k_context.get("same_opponent", {}) or {}).get("status", ""))
        st.write(f"Environment: {(home_k_context.get('environment', {}) or {}).get('status', '')}")
        st.markdown(f"**{away_pitcher} vs {home_team}**")
        st.write(away_k_context.get("status", ""))
        st.write((away_k_context.get("archetype", {}) or {}).get("status", ""))
        st.write((away_k_context.get("same_opponent", {}) or {}).get("status", ""))
        st.write(f"Environment: {(away_k_context.get('environment', {}) or {}).get('status', '')}")

    with st.expander("MLB Lineup K Diagnostic Details", expanded=True):
        st.caption("V15 uses a controlled batting-order-weighted lineup K-rate adjustment. The multiplier is capped and shrunk to prevent the old lineup/arsenal double counting.")

        def _fmt_pct(value):
            if value is None or value == "":
                return "—"
            try:
                return f"{float(value) * 100:.1f}%"
            except Exception:
                return "—"

        lineup_col1, lineup_col2 = st.columns(2)
        with lineup_col1:
            st.markdown(f"**{home_pitcher} vs {away_team} lineup**")
            st.write(f"Source: {home_lineup_details.get('source', '')}")
            st.write(f"Status: {home_lineup_details.get('status', '')}")
            st.write(f"Hitters with K% found: {home_lineup_details.get('hitters_found', 0)}")
            st.write(f"Lineup K%: {_fmt_pct(home_lineup_details.get('lineup_k_rate'))}")
            st.write(f"Team baseline K%: {_fmt_pct(home_lineup_details.get('team_baseline_k_rate'))}")
            st.write(f"Final blended K%: {_fmt_pct(home_lineup_details.get('blended_k_rate'))}")
            home_hand_stack = home_lineup_details.get("hand_stack", {}) or {}
            st.write(f"Handedness stack: {home_hand_stack.get('same_side', 0)} same-side / {home_hand_stack.get('opposite_side', 0)} opposite-side+switch")
            st.write(f"Handedness K multiplier: {float(home_lineup_details.get('hand_stack_multiplier', 1.0)):.3f}")
            home_pt = home_lineup_details.get("pitch_type_matchup", {}) or {}
            st.write(f"Pitch-type matchup: {home_pt.get('status', '—')}")
            if home_pt.get("lineup_weighted_whiff") is not None and home_pt.get("league_weighted_whiff") is not None:
                st.write(f"Lineup whiff vs pitcher mix: {float(home_pt.get('lineup_weighted_whiff')) * 100:.1f}% vs league {float(home_pt.get('league_weighted_whiff')) * 100:.1f}%")
            st.write(f"Pitch-type component (shrunk): {float(home_lineup_details.get('pitch_type_multiplier', 1.0)):.3f}")
            st.write(f"Active lineup K-rate multiplier: {float(home_lineup_details.get('diagnostic_multiplier', 1.0)):.3f}")
            st.write(f"Projection used: {home_k_raw:.2f} → final {home_k:.2f} (active lineup rate + environment)")
            hitter_df = home_lineup_details.get("hitters", pd.DataFrame())
            if hitter_df is not None and not hitter_df.empty:
                st.dataframe(hitter_df, use_container_width=True, hide_index=True)
            pt_detail = home_pt.get("detail_rows", pd.DataFrame())
            if pt_detail is not None and not pt_detail.empty:
                with st.expander(f"{home_pitcher} hitter pitch-type details", expanded=False):
                    st.dataframe(pt_detail, use_container_width=True, hide_index=True)

        with lineup_col2:
            st.markdown(f"**{away_pitcher} vs {home_team} lineup**")
            st.write(f"Source: {away_lineup_details.get('source', '')}")
            st.write(f"Status: {away_lineup_details.get('status', '')}")
            st.write(f"Hitters with K% found: {away_lineup_details.get('hitters_found', 0)}")
            st.write(f"Lineup K%: {_fmt_pct(away_lineup_details.get('lineup_k_rate'))}")
            st.write(f"Team baseline K%: {_fmt_pct(away_lineup_details.get('team_baseline_k_rate'))}")
            st.write(f"Final blended K%: {_fmt_pct(away_lineup_details.get('blended_k_rate'))}")
            away_hand_stack = away_lineup_details.get("hand_stack", {}) or {}
            st.write(f"Handedness stack: {away_hand_stack.get('same_side', 0)} same-side / {away_hand_stack.get('opposite_side', 0)} opposite-side+switch")
            st.write(f"Handedness K multiplier: {float(away_lineup_details.get('hand_stack_multiplier', 1.0)):.3f}")
            away_pt = away_lineup_details.get("pitch_type_matchup", {}) or {}
            st.write(f"Pitch-type matchup: {away_pt.get('status', '—')}")
            if away_pt.get("lineup_weighted_whiff") is not None and away_pt.get("league_weighted_whiff") is not None:
                st.write(f"Lineup whiff vs pitcher mix: {float(away_pt.get('lineup_weighted_whiff')) * 100:.1f}% vs league {float(away_pt.get('league_weighted_whiff')) * 100:.1f}%")
            st.write(f"Pitch-type component (shrunk): {float(away_lineup_details.get('pitch_type_multiplier', 1.0)):.3f}")
            st.write(f"Active lineup K-rate multiplier: {float(away_lineup_details.get('diagnostic_multiplier', 1.0)):.3f}")
            st.write(f"Projection used: {away_k_raw:.2f} → final {away_k:.2f} (active lineup rate + environment)")
            hitter_df = away_lineup_details.get("hitters", pd.DataFrame())
            if hitter_df is not None and not hitter_df.empty:
                st.dataframe(hitter_df, use_container_width=True, hide_index=True)
            pt_detail = away_pt.get("detail_rows", pd.DataFrame())
            if pt_detail is not None and not pt_detail.empty:
                with st.expander(f"{away_pitcher} hitter pitch-type details", expanded=False):
                    st.dataframe(pt_detail, use_container_width=True, hide_index=True)

    with st.expander("Pitch-Type Arsenal Modifier Details", expanded=False):
        st.markdown(f"**{home_pitcher} vs {away_team}**")
        st.write(f"Status: {home_arsenal_details.get('status', '')}")
        st.write(f"Base K: {home_arsenal_details.get('base_projection', '')} → Adjusted K: {home_arsenal_details.get('adjusted_projection', '')}")
        st.write(f"Arsenal modifier: {home_arsenal_details.get('modifier', 0)} Ks | Arsenal score: {home_arsenal_details.get('score', 0)}")
        home_detail_df = home_arsenal_details.get('details', pd.DataFrame())
        if isinstance(home_detail_df, pd.DataFrame) and not home_detail_df.empty:
            st.dataframe(home_detail_df, use_container_width=True, hide_index=True)

        st.markdown(f"**{away_pitcher} vs {home_team}**")
        st.write(f"Status: {away_arsenal_details.get('status', '')}")
        st.write(f"Base K: {away_arsenal_details.get('base_projection', '')} → Adjusted K: {away_arsenal_details.get('adjusted_projection', '')}")
        st.write(f"Arsenal modifier: {away_arsenal_details.get('modifier', 0)} Ks | Arsenal score: {away_arsenal_details.get('score', 0)}")
        away_detail_df = away_arsenal_details.get('details', pd.DataFrame())
        if isinstance(away_detail_df, pd.DataFrame) and not away_detail_df.empty:
            st.dataframe(away_detail_df, use_container_width=True, hide_index=True)

    if home_bulk_projection or away_bulk_projection:
        st.divider()
        st.subheader("Bulk Pitcher Projections")
        for pdata in [home_bulk_projection, away_bulk_projection]:
            if not pdata: continue
            st.markdown(f"### {pdata['pitcher']} ({pdata['team']} bulk)")
            render_builder_metric_grid([
                {"label":"Final K", "value":round(pdata['projection'],2)},
                {"label":"Expected IP", "value":round(pdata['expected_ip'],1)},
                {"label":"Line", "value":pdata.get('line','')},
                {"label":"Grade", "value":pdata.get('grade','PASS'), "wide":True, "big":True},
                {"label":"Reliability", "value":f"{pdata['calibration']['reliability']['score']}/100"},
                {"label":"Selected Prob", "value":f"{pdata.get('selected_probability',0)*100:.1f}%"},
                {"label":"Entry Inning", "value":(home_bulk_context if pdata['team']==home_team else away_bulk_context).get('expected_entry_inning','')},
                {"label":"K Score", "value":pdata.get('score','')},
            ])

    st.divider()
    st.subheader("NRFI Projection")
    col5, col6, col7 = st.columns(3)
    with col5:
        st.metric("NRFI %", f"{nrfi_prob * 100:.1f}%")
    with col6:
        st.metric("Selected Score", round(selected_first_inning_score, 1))
    with col7:
        st.metric("Bet Grade", nrfi_grade)
    st.caption(f"NRFI score {nrfi_score:.1f} | YRFI score {yrfi_score:.1f}")
    st.caption(
        f"NRFI edge/EV: {nrfi_environment.get('nrfi_edge', 0) * 100:+.1f}% / {nrfi_environment.get('nrfi_ev', 0):+.3f}u | "
        f"YRFI edge/EV: {nrfi_environment.get('yrfi_edge', 0) * 100:+.1f}% / {nrfi_environment.get('yrfi_ev', 0):+.3f}u"
    )
    st.caption(nrfi_environment.get("status", ""))
    with st.expander("First-Inning Model Diagnostics", expanded=False):
        st.write(f"Top-half run probability: {nrfi_model_details.get('top_half_run_probability', 0) * 100:.1f}%")
        st.write(f"Bottom-half run probability: {nrfi_model_details.get('bottom_half_run_probability', 0) * 100:.1f}%")
        st.write(f"{away_team} top four: {nrfi_model_details.get('away_top_four', {})}")
        st.write(f"{home_team} top four: {nrfi_model_details.get('home_top_four', {})}")
        st.write(f"{home_pitcher} first inning: {nrfi_model_details.get('home_pitcher_first_inning', {})}")
        st.write(f"{away_pitcher} first inning: {nrfi_model_details.get('away_pitcher_first_inning', {})}")

    st.divider()
    st.subheader("Total Runs Projection")
    total_col1, total_col2, total_col3, total_col4, total_col5, total_col6 = st.columns(6)
    with total_col1:
        st.metric("Projected Total", total_run_details.get("projected_total", ""))
    with total_col2:
        st.metric("Market Total", total_run_details.get("market_total", ""))
    with total_col3:
        st.metric("Edge", total_run_details.get("edge", ""))
    with total_col4:
        st.metric("Grade", total_run_details.get("grade", "PASS"))
    with total_col5:
        st.metric(f"{away_team} Runs", total_run_details.get("away_projected_runs", ""))
    with total_col6:
        st.metric(f"{home_team} Runs", total_run_details.get("home_projected_runs", ""))
    with st.expander("Total Runs Details", expanded=False):
        st.caption(total_run_details.get("status", ""))
        st.write(f"{away_team} projected runs: {total_run_details.get('away_projected_runs', '')}")
        st.write(f"{home_team} projected runs: {total_run_details.get('home_projected_runs', '')}")
        st.write(f"{away_team} starter score: {total_run_details.get('away_starter_score', '')}")
        st.write(f"{home_team} starter score: {total_run_details.get('home_starter_score', '')}")
        st.write(f"{away_team} bullpen score: {total_run_details.get('away_bullpen_score', '')}")
        st.write(f"{home_team} bullpen score: {total_run_details.get('home_bullpen_score', '')}")
        st.write(f"{away_team} offense score: {total_run_details.get('away_offense_score', '')}")
        st.write(f"{home_team} offense score: {total_run_details.get('home_offense_score', '')}")
        st.write(f"Park/weather adjustment: {total_run_details.get('park_weather_adjustment', 0)} runs")
        st.write(f"Environment: {(total_run_details.get('game_environment', {}) or {}).get('status', '')}")
        st.write(f"Total confluence: {total_run_details.get('confluence', 0)} / {total_run_details.get('required_confluence', 3)}")
        st.write(f"Required edge: {total_run_details.get('required_edge', 1.5)} runs")
        st.write(f"Confluence components: {total_run_details.get('confluence_components', {})}")
        st.write(f"Over / Under / Push: {total_run_details.get('over_probability', 0) * 100:.1f}% / {total_run_details.get('under_probability', 0) * 100:.1f}% / {total_run_details.get('push_probability', 0) * 100:.1f}%")
        st.write(f"Selected price edge / EV: {total_run_details.get('price_edge', 0) * 100:+.1f}% / {total_run_details.get('selected_ev', 0):+.3f}u per unit")

    st.divider()
    st.subheader("Moneyline Projection")
    if bullpen_context_note:
        st.info(" | ".join(bullpen_context_note))
    col8, col9 = st.columns(2)
    with col8:
        st.markdown(f"### {home_team}")
        home_ml_summary = moneyline_details.get("home", {}) or {}
        home_starter_summary = home_ml_summary.get("starter", {}) if isinstance(home_ml_summary.get("starter", {}), dict) else {}
        home_bullpen_summary = home_ml_summary.get("bullpen", {}) if isinstance(home_ml_summary.get("bullpen", {}), dict) else {}
        st.metric("Model Win %", f"{home_win_prob * 100:.1f}%")
        st.metric("Book Implied %", f"{home_implied * 100:.1f}%")
        st.caption(f"No-vig fair implied: {home_fair_implied * 100:.1f}%")
        st.metric("No-Vig Edge", f"{home_ml_edge * 100:.1f}%")
        st.metric("Confidence", f"{home_ml_confidence.get('confidence_score', 0)}/100")
        st.metric("Confluence", f"{home_ml_confidence.get('confluence', 0)}/4")
        st.metric("Starter Score", home_starter_summary.get("matchup_score", ""))
        st.metric("Bullpen Score", home_bullpen_summary.get("score", ""))
        st.metric("Lineup Score", (home_ml_summary.get("lineup", {}) or {}).get("score", "") if isinstance(home_ml_summary.get("lineup", {}), dict) else "")
        st.metric("Grade", home_ml_grade)
    with col9:
        st.markdown(f"### {away_team}")
        away_ml_summary = moneyline_details.get("away", {}) or {}
        away_starter_summary = away_ml_summary.get("starter", {}) if isinstance(away_ml_summary.get("starter", {}), dict) else {}
        away_bullpen_summary = away_ml_summary.get("bullpen", {}) if isinstance(away_ml_summary.get("bullpen", {}), dict) else {}
        st.metric("Model Win %", f"{away_win_prob * 100:.1f}%")
        st.metric("Book Implied %", f"{away_implied * 100:.1f}%")
        st.caption(f"No-vig fair implied: {away_fair_implied * 100:.1f}%")
        st.metric("No-Vig Edge", f"{away_ml_edge * 100:.1f}%")
        st.metric("Confidence", f"{away_ml_confidence.get('confidence_score', 0)}/100")
        st.metric("Confluence", f"{away_ml_confidence.get('confluence', 0)}/4")
        st.metric("Starter Score", away_starter_summary.get("matchup_score", ""))
        st.metric("Bullpen Score", away_bullpen_summary.get("score", ""))
        st.metric("Lineup Score", (away_ml_summary.get("lineup", {}) or {}).get("score", "") if isinstance(away_ml_summary.get("lineup", {}), dict) else "")
        st.metric("Grade", away_ml_grade)

    with st.expander("Moneyline Confidence + Shared Run Engine Details", expanded=True):
        st.caption(moneyline_details.get("confidence_status", ""))
        st.caption(moneyline_details.get("status", ""))
        st.write(f"Raw score: {moneyline_details.get('raw_score', '')} | Final score: {moneyline_details.get('score', '')}")
        st.markdown("**Why the moneyline grades were scored this way**")
        reason_cols = st.columns(2)
        with reason_cols[0]:
            st.markdown(f"**{home_team} confidence: {home_ml_confidence.get('confidence_score', 0)}/100 | {home_ml_confidence.get('confluence', 0)}/4**")
            for line in home_ml_confidence.get("reason_lines", []):
                st.write(line)
        with reason_cols[1]:
            st.markdown(f"**{away_team} confidence: {away_ml_confidence.get('confidence_score', 0)}/100 | {away_ml_confidence.get('confluence', 0)}/4**")
            for line in away_ml_confidence.get("reason_lines", []):
                st.write(line)
        st.divider()
        ml_cols = st.columns(2)
        with ml_cols[0]:
            st.markdown(f"**{home_team} components**")
            home_ml = moneyline_details.get("home", {}) or {}
            st.write(f"Total component score: {home_ml.get('total_component_score', '')}")
            st.write(f"Positive buckets: {home_ml.get('confluence_positive_buckets', '')}")
            st.write(f"Negative buckets: {home_ml.get('confluence_negative_buckets', '')}")
            for bucket in ["starter", "lineup", "offense", "bullpen"]:
                st.markdown(f"**{bucket.title()}**")
                info = home_ml.get(bucket, {}) or {}
                if isinstance(info, dict):
                    for k, v in info.items():
                        st.write(f"{str(k).replace('_', ' ').title()}: {v}")
        with ml_cols[1]:
            st.markdown(f"**{away_team} components**")
            away_ml = moneyline_details.get("away", {}) or {}
            st.write(f"Total component score: {away_ml.get('total_component_score', '')}")
            st.write(f"Positive buckets: {away_ml.get('confluence_positive_buckets', '')}")
            st.write(f"Negative buckets: {away_ml.get('confluence_negative_buckets', '')}")
            for bucket in ["starter", "lineup", "offense", "bullpen"]:
                st.markdown(f"**{bucket.title()}**")
                info = away_ml.get(bucket, {}) or {}
                if isinstance(info, dict):
                    for k, v in info.items():
                        st.write(f"{str(k).replace('_', ' ').title()}: {v}")

    st.divider()
    with st.expander("Data Health + Projection Reliability", expanded=False):
        def _render_pitcher_health_block(pitcher_name, data_health, calibration, arsenal_details, role_label="Starter"):
            health = data_health or {}
            cal = calibration or {}
            reliability = cal.get("reliability", {}) or {}
            arsenal = arsenal_details or {}
            discipline = arsenal.get("opponent_discipline", {}) or {}
            recent_pitch = arsenal.get("recent_pitch_profile", {}) or {}
            st.markdown(f"**{pitcher_name} — {role_label}**")
            health_cols = st.columns(2)
            with health_cols[0]:
                st.metric("Data Health", f"{health.get('score', 0)}/100")
            with health_cols[1]:
                st.metric("Projection Reliability", f"{reliability.get('score', 0)}/100")
            issues = health.get("issues", []) or []
            if issues:
                for issue in issues:
                    st.write(f"• {issue}")
            else:
                st.write("All primary model data sources are healthy.")
            reliability_reasons = reliability.get("reasons", []) or []
            if reliability_reasons:
                st.caption("Reliability factors: " + " | ".join(str(x) for x in reliability_reasons))
            st.caption(
                "Opponent discipline: "
                f"{discipline.get('source', 'Unavailable')}"
                + (f" | Team: {discipline.get('resolved_team')}" if discipline.get('resolved_team') else "")
                + (f" | Mapping: {discipline.get('team_mapping_source')}" if discipline.get('team_mapping_source') else "")
            )
            if recent_pitch.get("available"):
                st.caption(
                    "Recent pitch profile: "
                    f"shape change {float(recent_pitch.get('shape_change_inches', 0) or 0):.2f} in | "
                    f"release change {float(recent_pitch.get('release_change_inches', 0) or 0):.2f} in | "
                    f"velocity {float(recent_pitch.get('velocity_delta', 0) or 0):+.2f} mph"
                )
            else:
                st.caption(f"Recent pitch profile: {recent_pitch.get('status', 'Unavailable')}")

        _render_pitcher_health_block(home_pitcher, home_data_health, home_k_calibration, home_arsenal_details)
        st.divider()
        _render_pitcher_health_block(away_pitcher, away_data_health, away_k_calibration, away_arsenal_details)
        if home_bulk_projection:
            st.divider()
            _render_pitcher_health_block(
                home_bulk_projection['pitcher'],
                home_bulk_projection.get('data_health', {}),
                home_bulk_projection.get('calibration', {}),
                home_bulk_projection.get('arsenal', {}),
                "Bulk",
            )
        if away_bulk_projection:
            st.divider()
            _render_pitcher_health_block(
                away_bulk_projection['pitcher'],
                away_bulk_projection.get('data_health', {}),
                away_bulk_projection.get('calibration', {}),
                away_bulk_projection.get('arsenal', {}),
                "Bulk",
            )
        st.caption(f"Weather source: {auto_weather.get('source', 'Manual/fallback')} | Model version: {MODEL_VERSION}")

    qualifying_directions = []
    if better_ml_grade in ["A Moneyline", "B Moneyline"]:
        qualifying_directions.append(f"{better_ml_team} moneyline")
    if total_run_details.get("grade") in ["TOTAL OVER", "TOTAL UNDER"]:
        qualifying_directions.append(str(total_run_details.get("grade")))
    if nrfi_grade in ["ELITE NRFI", "ELITE YRFI", "YRFI"]:
        qualifying_directions.append(nrfi_grade)
    for pitcher_name, grade in [(home_pitcher, home_k_grade), (away_pitcher, away_k_grade)]:
        if str(grade).upper() != "PASS":
            qualifying_directions.append(f"{pitcher_name} {grade}")
    for pdata in [home_bulk_projection, away_bulk_projection]:
        if pdata and pdata.get("grade") != "PASS":
            qualifying_directions.append(f"{pdata['pitcher']} {pdata['grade']}")
    correlation_warning = ""
    if len(qualifying_directions) >= 3:
        correlation_warning = f"Correlation warning: {len(qualifying_directions)} qualifying plays share assumptions in this game — " + "; ".join(qualifying_directions)
        st.warning(correlation_warning)

    st.divider()
    st.subheader("Save")
    if st.button("Save Matchup Summary", key=f"save_auto_{game.get('game_pk')}"):
        ml_tag = ""
        if better_ml_grade == "A Moneyline":
            ml_tag = " [A]"
        elif better_ml_grade == "B Moneyline":
            ml_tag = " [B]"
        better_ml_text = f"{better_ml_team} ({better_ml_prob * 100:.1f}%){ml_tag}"
        away_k_summary = k_summary_text(away_pitcher, away_k, away_k_grade, away_k_line, away_k_odds)
        home_k_summary = k_summary_text(home_pitcher, home_k, home_k_grade, home_k_line, home_k_odds)

        add_slate_row(
            away_team,
            home_team,
            better_ml_text,
            better_ml_odds,
            better_ml_grade,
            nrfi_grade,
            away_k_summary,
            away_k_score,
            home_k_summary,
            home_k_score,
            total_run_details.get("projected_total", ""),
            total_run_details.get("grade", ""),
            total_runs_line,
            game_id=game_key,
            game_label=game_label,
            slate_date=slate_date,
            nrfi_score_value=round(selected_first_inning_score, 1),
            nrfi_probability_value=f"{selected_first_inning_probability * 100:.1f}%",
            nrfi_odds_value=selected_first_inning_odds,
            away_k_reliability=away_k_calibration["reliability"]["score"],
            away_k_probability=f"{away_selected_prob*100:.1f}%",
            home_k_reliability=home_k_calibration["reliability"]["score"],
            home_k_probability=f"{home_selected_prob*100:.1f}%",
            away_bulk_summary=(k_summary_text(away_bulk_projection['pitcher'], away_bulk_projection['projection'], away_bulk_projection['grade'], away_bulk_projection['line'], away_bulk_projection['odds']) if away_bulk_projection else ""),
            away_bulk_score=(away_bulk_projection.get('score','') if away_bulk_projection else ""),
            away_bulk_reliability=(away_bulk_projection['calibration']['reliability']['score'] if away_bulk_projection else ""),
            home_bulk_summary=(k_summary_text(home_bulk_projection['pitcher'], home_bulk_projection['projection'], home_bulk_projection['grade'], home_bulk_projection['line'], home_bulk_projection['odds']) if home_bulk_projection else ""),
            home_bulk_score=(home_bulk_projection.get('score','') if home_bulk_projection else ""),
            home_bulk_reliability=(home_bulk_projection['calibration']['reliability']['score'] if home_bulk_projection else ""),
            total_selected_probability=f"{total_run_details.get('selected_probability',0)*100:.1f}%",
            total_reliability=total_run_details.get('reliability',''),
        )

        away_recent_decision_note = " | ".join(
            x for x in [away_recent_form_note, away_recent_accuracy_note, away_six_inning_override_note] if str(x).strip()
        )
        home_recent_decision_note = " | ".join(
            x for x in [home_recent_form_note, home_recent_accuracy_note, home_six_inning_override_note] if str(x).strip()
        )
        def _pitcher_history_metadata(cal, arsenal, lineup, role, odds, selected_prob, price_edge, expected_ip, opener="", bulk_context=None, archetype="", skill_snapshot=None, data_health=None):
            workload = (arsenal or {}).get("workload", {}) or {}
            pitcher_disc = (arsenal or {}).get("pitcher_discipline", {}) or {}
            opponent_disc = (arsenal or {}).get("opponent_discipline", {}) or {}
            recent_pitch = (arsenal or {}).get("recent_pitch_profile", {}) or {}
            rate_mults = (arsenal or {}).get("rate_multipliers", {}) or {}
            projected_bf = float(workload.get("projected_bf", _projected_batters_faced(expected_ip)) or _projected_batters_faced(expected_ip))
            projected_pitches = float(workload.get("projected_pitches", _pitch_count_projection(expected_ip, role)) or _pitch_count_projection(expected_ip, role))
            skill_snapshot = skill_snapshot or {}
            data_health = data_health or cal.get("data_health", {}) or {}
            return {
                "role": role, "model_version": MODEL_VERSION, "raw_projection": cal.get("raw_projection"),
                "global_calibrated_projection": cal.get("global_projection"), "pitcher_adjustment": cal.get("pitcher_adjustment"),
                "opponent_adjustment": cal.get("opponent_adjustment"), "shadow_projection": cal.get("shadow_projection"),
                "odds": odds, "reliability_score": cal["reliability"]["score"], "expected_std_dev": cal.get("expected_std"),
                "selected_probability": selected_prob, "market_implied_probability": american_odds_to_implied_prob(odds), "price_edge": price_edge,
                "projected_ip": expected_ip, "projected_pitches": projected_pitches, "projected_bf": projected_bf,
                "projected_k_rate": (float(cal.get("final_projection",0))/projected_bf if projected_bf>0 else 0),
                "projection_architecture": (arsenal or {}).get("projection_architecture", K_MODEL_ARCHITECTURE),
                "projected_pitches_per_bf": workload.get("projected_pitches_per_bf", ""),
                "opponent_pitches_per_pa": workload.get("opponent_pitches_per_pa", ""),
                "pitcher_pitches_per_bf": workload.get("pitcher_pitches_per_bf", ""),
                "third_time_probability": workload.get("third_time_probability", ""),
                "base_k_rate": (arsenal or {}).get("base_k_rate", ""),
                "team_split_k_rate": (arsenal or {}).get("team_split_k_rate", ""),
                "lineup_k_rate": (lineup or {}).get("lineup_k_rate", (arsenal or {}).get("lineup_k_rate", "")),
                "matchup_k_rate": (arsenal or {}).get("matchup_k_rate", ""),
                "arsenal_rate_multiplier": rate_mults.get("arsenal", (arsenal or {}).get("k_rate_multiplier", "")),
                "lineup_rate_multiplier": (lineup or {}).get("projection_multiplier", (arsenal or {}).get("lineup_rate_multiplier", "")),
                "skill_rate_multiplier": rate_mults.get("pitcher_skill", ""),
                "opponent_discipline_multiplier": rate_mults.get("opponent_discipline", ""),
                "recent_pitch_multiplier": rate_mults.get("recent_pitch", ""),
                "pitcher_csw_pct": pitcher_disc.get("csw_pct", ""),
                "pitcher_called_strike_pct": pitcher_disc.get("called_strike_pct", ""),
                "pitcher_chase_pct": pitcher_disc.get("chase_pct", ""),
                "pitcher_zone_contact_pct": pitcher_disc.get("zone_contact_pct", ""),
                "pitcher_chase_contact_pct": pitcher_disc.get("chase_contact_pct", ""),
                "pitcher_first_strike_pct": pitcher_disc.get("first_strike_pct", ""),
                "opponent_whiff_pct": opponent_disc.get("whiff_pct", ""),
                "opponent_zone_contact_pct": opponent_disc.get("zone_contact_pct", ""),
                "opponent_chase_contact_pct": opponent_disc.get("chase_contact_pct", ""),
                "recent_velocity_delta": recent_pitch.get("velocity_delta", ""),
                "recent_csw_delta": recent_pitch.get("csw_delta", ""),
                "recent_whiff_delta": recent_pitch.get("whiff_delta", ""),
                "recent_usage_quality_shift": recent_pitch.get("usage_quality_shift", ""),
                "recent_shape_change": recent_pitch.get("shape_change_inches", ""),
                "recent_release_change": recent_pitch.get("release_change_inches", ""),
                "under_support_count": (arsenal or {}).get("under_support_count", ""),
                "under_support_notes": (arsenal or {}).get("under_support_notes", []),
                "projection_structural_std": (arsenal or {}).get("structural_std", ""),
                "pitcher_archetype": archetype,
                "archetype_shadow_adjustment": (cal.get("archetype_shadow", {}) or {}).get("adjustment", 0),
                "season_k_pct_snapshot": skill_snapshot.get("k_pct", ""), "season_whiff_pct_snapshot": skill_snapshot.get("whiff_pct", ""),
                "arsenal_score": skill_snapshot.get("arsenal_score", arsenal.get("score", "")), "weapon_count": skill_snapshot.get("weapon_count", arsenal.get("weapon_count", 0)),
                "weather_source": str(auto_weather.get("source", "") or ""), "data_health_score": data_health.get("score", ""),
                "data_health_notes": data_health.get("status", ""),
                "lineup_confirmed": "TRUE" if "confirmed" in str((lineup or {}).get("source","")).lower() else "FALSE",
                "lineup_hitters_found": (lineup or {}).get("hitters_found",0), "opener": opener,
                "bulk_pitcher": (bulk_context or {}).get("bulk_pitcher",""), "bulk_confidence": (bulk_context or {}).get("confidence",""),
                "bulk_source": (bulk_context or {}).get("source",""), "calibration_notes": cal.get("status","")
            }

        away_expected_ip = float((away_arsenal_details.get("workload",{}) or {}).get("projected_start_ip", away_ipg_this or away_ipg_last or 5.0) or 5.0)
        home_expected_ip = float((home_arsenal_details.get("workload",{}) or {}).get("projected_start_ip", home_ipg_this or home_ipg_last or 5.0) or 5.0)
        if use_away_bullpen: away_expected_ip = float(away_bulk_context.get("expected_opener_ip",1.3) or 1.3)
        if use_home_bullpen: home_expected_ip = float(home_bulk_context.get("expected_opener_ip",1.3) or 1.3)
        record_pitcher_recent_form_start(slate_date, game_key, away_pitcher, away_team, home_team, away_k, away_k_line, away_k_grade, away_recent_decision_note,
            _pitcher_history_metadata(away_k_calibration, away_arsenal_details, away_lineup_details, "Opener" if use_away_bullpen else "Starter", away_k_odds, away_selected_prob, away_k_price_edge, away_expected_ip, opener=away_pitcher if use_away_bullpen else "", bulk_context=away_bulk_context, archetype=away_archetype, skill_snapshot=away_skill_snapshot, data_health=away_data_health))
        record_pitcher_recent_form_start(slate_date, game_key, home_pitcher, home_team, away_team, home_k, home_k_line, home_k_grade, home_recent_decision_note,
            _pitcher_history_metadata(home_k_calibration, home_arsenal_details, home_lineup_details, "Opener" if use_home_bullpen else "Starter", home_k_odds, home_selected_prob, home_k_price_edge, home_expected_ip, opener=home_pitcher if use_home_bullpen else "", bulk_context=home_bulk_context, archetype=home_archetype, skill_snapshot=home_skill_snapshot, data_health=home_data_health))
        for pdata, bctx in [(away_bulk_projection, away_bulk_context), (home_bulk_projection, home_bulk_context)]:
            if not pdata: continue
            record_pitcher_recent_form_start(slate_date, game_key, pdata['pitcher'], pdata['team'], pdata['opponent'], pdata['projection'], pdata['line'], pdata['grade'], pdata.get('recent_note',''),
                _pitcher_history_metadata(pdata['calibration'], pdata['arsenal'], pdata['lineup'], "Bulk", pdata['odds'], pdata['selected_probability'], pdata['price_edge'], pdata['expected_ip'], opener=bctx.get('opener',''), bulk_context=bctx, archetype=pdata.get('archetype',''), skill_snapshot=pdata.get('skill_snapshot',{}), data_health=pdata.get('data_health',{})))

        matchup_details = {
            "game_environment": game_environment,
            "pitchers": {
                "away": {
                    "pitcher": away_pitcher,
                    "team": away_team,
                    "opponent": home_team,
                    "expected_ks": round(away_k, 2),
                    "raw_expected_ks": round(away_k_precalibration, 2),
                    "calibration": away_k_calibration,
                    "six_ip_ks": round(away_k_6ip, 2),
                    "line": away_k_line,
                    "odds": away_k_odds,
                    "edge": round(away_k_edge, 2),
                    "variance": round(away_k - away_k_6ip, 2),
                    "volatility": away_vol,
                    "recent_form": away_recent_form,
                    "recent_form_note": away_recent_form_note,
                    "recent_accuracy_note": away_recent_accuracy_note,
                    "six_inning_override_note": away_six_inning_override_note,
                    "weapon_floor_note": away_weapon_floor_note,
                    "k_context_note": away_k_context_note,
                    "k_context": away_k_context,
                    "grade": away_k_grade,
                    "k_score": away_k_score,
                    "arsenal": away_arsenal_details,
                    "lineup": away_lineup_details
                },
                "home": {
                    "pitcher": home_pitcher,
                    "team": home_team,
                    "opponent": away_team,
                    "expected_ks": round(home_k, 2),
                    "raw_expected_ks": round(home_k_precalibration, 2),
                    "calibration": home_k_calibration,
                    "six_ip_ks": round(home_k_6ip, 2),
                    "line": home_k_line,
                    "odds": home_k_odds,
                    "edge": round(home_k_edge, 2),
                    "variance": round(home_k - home_k_6ip, 2),
                    "volatility": home_vol,
                    "recent_form": home_recent_form,
                    "recent_form_note": home_recent_form_note,
                    "recent_accuracy_note": home_recent_accuracy_note,
                    "six_inning_override_note": home_six_inning_override_note,
                    "weapon_floor_note": home_weapon_floor_note,
                    "k_context_note": home_k_context_note,
                    "k_context": home_k_context,
                    "grade": home_k_grade,
                    "k_score": home_k_score,
                    "arsenal": home_arsenal_details,
                    "lineup": home_lineup_details
                }
            },
            "bulk_pitching": {"home": home_bulk_context, "away": away_bulk_context, "home_projection": home_bulk_projection, "away_projection": away_bulk_projection},
            "data_health": {"home": home_data_health, "away": away_data_health},
            "correlation_warning": correlation_warning,
            "model_version": MODEL_VERSION,
            "moneyline": {
                "better_team": better_ml_team,
                "better_probability": f"{better_ml_prob * 100:.1f}%",
                "better_odds": better_ml_odds,
                "better_grade": better_ml_grade,
                "bullpen_context": " | ".join(bullpen_context_note),
                "home": {
                    "team": home_team,
                    "model_win_pct": f"{home_win_prob * 100:.1f}%",
                    "book_implied_pct": f"{home_implied * 100:.1f}%",
                    "no_vig_implied_pct": f"{home_fair_implied * 100:.1f}%",
                    "edge_pct": f"{home_ml_edge * 100:.1f}%",
                    "confidence_score": home_ml_confidence.get("confidence_score", ""),
                    "confluence": f"{home_ml_confidence.get('confluence', 0)}/4",
                    "confidence_reasons": home_ml_confidence.get("reason_lines", []),
                    "grade": home_ml_grade,
                    "bullpen_game_checked": use_home_bullpen
                },
                "away": {
                    "team": away_team,
                    "model_win_pct": f"{away_win_prob * 100:.1f}%",
                    "book_implied_pct": f"{away_implied * 100:.1f}%",
                    "no_vig_implied_pct": f"{away_fair_implied * 100:.1f}%",
                    "edge_pct": f"{away_ml_edge * 100:.1f}%",
                    "confidence_score": away_ml_confidence.get("confidence_score", ""),
                    "confluence": f"{away_ml_confidence.get('confluence', 0)}/4",
                    "confidence_reasons": away_ml_confidence.get("reason_lines", []),
                    "grade": away_ml_grade,
                    "bullpen_game_checked": use_away_bullpen
                }
            },
            "nrfi": {
                "grade": nrfi_grade,
                "probability": f"{nrfi_prob * 100:.1f}%",
                "nrfi_score": round(nrfi_score, 1),
                "yrfi_score": round(yrfi_score, 1),
                "nrfi_odds": nrfi_odds,
                "yrfi_odds": yrfi_odds,
                "pricing": nrfi_environment,
                "first_inning_model": nrfi_model_details
            },
            "total_runs": total_run_details
        }

        add_matchup_detail_row(
            game_key,
            game_label,
            away_team,
            home_team,
            away_pitcher,
            home_pitcher,
            f"ML: {better_ml_team} {better_ml_grade} | Away K: {away_k_grade} {away_k_score} | Home K: {home_k_grade} {home_k_score} | {nrfi_grade}",
            matchup_details,
            details_date=slate_date
        )


        save_game_projection_history({
            "Date": slate_date, "Game Key": game_key, "Game Label": game_label, "Away Team": away_team, "Home Team": home_team, "Model Version": MODEL_VERSION,
            "Away Runs Projection": total_run_details.get("away_projected_runs",""), "Home Runs Projection": total_run_details.get("home_projected_runs",""),
            "Total Projection": total_run_details.get("projected_total",""), "Market Total": total_runs_line, "Total Grade": total_run_details.get("grade",""),
            "Total Selected Probability": total_run_details.get("selected_probability",""), "Home Win Probability": home_win_prob, "Away Win Probability": away_win_prob,
            "Better ML": better_ml_team, "ML Grade": better_ml_grade, "NRFI Probability": nrfi_prob, "YRFI Probability": 1-nrfi_prob,
            "First Inning Grade": nrfi_grade, "First Inning Selected Probability": selected_first_inning_probability,
            "Home Opener": home_pitcher if use_home_bullpen else "", "Home Bulk Pitcher": home_bulk_context.get("bulk_pitcher",""), "Home Bulk Confidence": home_bulk_context.get("confidence",""),
            "Away Opener": away_pitcher if use_away_bullpen else "", "Away Bulk Pitcher": away_bulk_context.get("bulk_pitcher",""), "Away Bulk Confidence": away_bulk_context.get("confidence","")
        })

        # Only add the higher model probability moneyline side to the Bet Tracker.
        # This prevents both teams from showing in Pending Bets.
        if better_ml_prob > 0.50 and better_ml_grade in ["A Moneyline", "B Moneyline"]:
            better_implied = home_implied if better_ml_team == home_team else away_implied
            better_fair_implied = home_fair_implied if better_ml_team == home_team else away_fair_implied
            better_edge = home_ml_edge if better_ml_team == home_team else away_ml_edge
            add_bet(
                better_ml_grade,
                better_ml_team,
                "Moneyline",
                better_ml_odds,
                f"{better_ml_prob * 100:.1f}%",
                f"{better_implied * 100:.1f}%",
                f"{better_edge * 100:.1f}%",
                metadata={"selected_probability": f"{better_ml_prob*100:.1f}%", "model_version": MODEL_VERSION, "game_key": game_key, "team": better_ml_team, "opponent": away_team if better_ml_team == home_team else home_team, "role": "Game"}
            )

        if nrfi_grade == "ELITE NRFI":
            add_bet(
                nrfi_grade, f"{away_team} at {home_team}", "NRFI/YRFI", nrfi_environment.get("selected_odds", nrfi_odds),
                f"{nrfi_prob * 100:.1f}%", f"{nrfi_environment.get('nrfi_implied', 0) * 100:.1f}%",
                f"Edge {nrfi_environment.get('nrfi_edge', 0) * 100:+.1f}%",
                metadata={"selected_probability": f"{nrfi_prob*100:.1f}%", "model_version": MODEL_VERSION, "game_key": game_key, "team": f"{away_team} at {home_team}", "role": "First Inning"}
            )
        if nrfi_grade == "ELITE YRFI":
            yrfi_prob_value = 1.0 - nrfi_prob
            add_bet(
                nrfi_grade, f"{away_team} at {home_team}", "NRFI/YRFI", nrfi_environment.get("selected_odds", yrfi_odds),
                f"{yrfi_prob_value * 100:.1f}%", f"{nrfi_environment.get('yrfi_implied', 0) * 100:.1f}%",
                f"Edge {nrfi_environment.get('yrfi_edge', 0) * 100:+.1f}%",
                metadata={"selected_probability": f"{yrfi_prob_value*100:.1f}%", "reliability_score": nrfi_environment.get("yrfi_score", ""), "model_version": MODEL_VERSION, "game_key": game_key, "team": f"{away_team} at {home_team}", "role": "First Inning"}
            )
        if total_run_details.get("grade") in ["TOTAL OVER", "TOTAL UNDER"]:
            add_bet(
                total_run_details.get("grade"),
                f"{away_team} at {home_team}",
                "Game Total",
                f"{total_runs_line} / {total_runs_odds}",
                f"{total_run_details.get('projected_total', '')}",
                "",
                f"Edge {total_run_details.get('edge', '')}",
                metadata={"selected_probability": f"{total_run_details.get('selected_probability',0)*100:.1f}%", "reliability_score": total_run_details.get('reliability',''), "model_version": MODEL_VERSION, "game_key": game_key, "team": f"{away_team} at {home_team}", "role": "Game Total"}
            )
        if home_k_grade != "PASS":
            add_bet(
                home_k_grade,
                f"{home_pitcher} {home_k_grade}",
                "Pitcher Strikeouts",
                f"{home_k_line} / {home_k_odds}",
                f"{home_k:.2f}",
                "",
                f"{home_k_edge:.2f}",
                metadata={"raw_projection":round(home_k_precalibration,2),"calibrated_projection":round(home_k,2),"reliability_score":home_k_calibration["reliability"]["score"],"expected_std_dev":home_k_calibration["expected_std"],"selected_probability":f"{home_selected_prob*100:.1f}%","model_version":MODEL_VERSION,"game_key":game_key,"team":home_team,"opponent":away_team,"role":"Opener" if use_home_bullpen else "Starter"}
            )
        if away_k_grade != "PASS":
            add_bet(
                away_k_grade,
                f"{away_pitcher} {away_k_grade}",
                "Pitcher Strikeouts",
                f"{away_k_line} / {away_k_odds}",
                f"{away_k:.2f}",
                "",
                f"{away_k_edge:.2f}",
                metadata={"raw_projection":round(away_k_precalibration,2),"calibrated_projection":round(away_k,2),"reliability_score":away_k_calibration["reliability"]["score"],"expected_std_dev":away_k_calibration["expected_std"],"selected_probability":f"{away_selected_prob*100:.1f}%","model_version":MODEL_VERSION,"game_key":game_key,"team":away_team,"opponent":home_team,"role":"Opener" if use_away_bullpen else "Starter"}
            )

        for pdata in [home_bulk_projection, away_bulk_projection]:
            if pdata and pdata.get("grade") != "PASS":
                add_bet(
                    pdata["grade"], f"{pdata['pitcher']} {pdata['grade']}", "Pitcher Strikeouts",
                    f"{pdata['line']} / {pdata['odds']}", f"{pdata['projection']:.2f}", "", f"{pdata['edge']:.2f}",
                    metadata={"raw_projection":round(pdata['raw_projection'],2),"calibrated_projection":round(pdata['projection'],2),"reliability_score":pdata['calibration']['reliability']['score'],"expected_std_dev":pdata['calibration']['expected_std'],"selected_probability":f"{pdata['selected_probability']*100:.1f}%","model_version":MODEL_VERSION,"game_key":game_key,"team":pdata['team'],"opponent":pdata['opponent'],"role":"Bulk"}
                )

        st.success("Matchup summary saved. Qualifying bets were added to Bet Tracker. Only independently qualified Elite YRFI plays pass; there is no daily top-two cap.")
        st.rerun()


def render_admin_home(live_ready=False):
    tracker_df = load_tracker()
    slate_df = load_slate()
    summary_df = tracker_summary_dataframe(tracker_df)
    green_totals = green_totals_from_summary(summary_df)
    pending_count = 0 if tracker_df.empty else int((tracker_df["Result"] == "Pending").sum())
    today = str(date.today())
    saved_today = 0 if slate_df.empty else int((slate_df["Date"].astype(str) == today).sum())
    odds_today = get_snapshot_for_date(today)
    odds_status = "YES" if not odds_today.empty else "NO"

    st.header("Admin Home")
    st.markdown(f"""
    <div class="ez-card ez-card-green">
        <div class="ez-title">TODAY'S CONTROL PANEL</div>
        <div class="ez-sub">Quick status for your phone workflow.</div>
        <div class="ez-kv"><span>Live model data loaded</span><span>{'YES' if live_ready else 'NO'}</span></div>
        <div class="ez-kv"><span>Games saved today</span><span>{saved_today}</span></div>
        <div class="ez-kv"><span>Moneyline snapshot saved</span><span>{odds_status}</span></div>
        <div class="ez-kv"><span>Pending bets</span><span>{pending_count}</span></div>
    </div>
    """, unsafe_allow_html=True)

    if green_totals:
        render_green_totals_card(summary_df)
    else:
        st.info("Green bet totals will appear here after completed winning bet types exist.")

    st.markdown("### Suggested workflow")
    st.markdown("1. Refresh/load live data → 2. Build Matchup → 3. Save plays → 4. Update Pending Bets")





# ============================================================================
# V14 CALIBRATION, PROJECTION HISTORY, AND OPENER/BULK HELPERS
# ============================================================================

GAME_PROJECTION_HISTORY_COLUMNS = [
    "Date", "Game Key", "Game Label", "Away Team", "Home Team", "Model Version",
    "Away Runs Projection", "Home Runs Projection", "Total Projection", "Market Total", "Total Grade",
    "Total Selected Probability", "Home Win Probability", "Away Win Probability", "Better ML", "ML Grade",
    "NRFI Probability", "YRFI Probability", "First Inning Grade", "First Inning Selected Probability",
    "Home Opener", "Home Bulk Pitcher", "Home Bulk Confidence", "Away Opener", "Away Bulk Pitcher", "Away Bulk Confidence",
    "Actual Away Runs", "Actual Home Runs", "Actual Total", "Actual Winner", "First Inning Runs",
    "Away Run Residual", "Home Run Residual", "Total Residual", "ML Correct", "First Inning Correct", "Updated Time ET"
]

MODEL_CHANGE_LOG_COLUMNS = ["Version", "Effective Date", "Changes", "Created Time ET"]


def _safe_float_or_none(value):
    try:
        if value in [None, "", "nan", "None"]:
            return None
        return float(value)
    except Exception:
        return None


def _history_before_today():
    df = load_pitcher_recent_form()
    if df is None or df.empty:
        return pd.DataFrame(columns=RECENT_FORM_COLUMNS)
    out = df.copy()
    for col in RECENT_FORM_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    out["_date"] = pd.to_datetime(out["Date"], errors="coerce")
    try:
        cutoff = pd.to_datetime(today_et_string())
    except Exception:
        cutoff = pd.to_datetime(str(date.today()))
    out = out[out["_date"] < cutoff].copy()
    out["_actual"] = pd.to_numeric(out["Actual Ks"], errors="coerce")
    out["_raw"] = pd.to_numeric(out["Raw Projection"], errors="coerce")
    out["_projection"] = pd.to_numeric(out["Projection"], errors="coerce")
    out["_global"] = pd.to_numeric(out["Global Calibrated Projection"], errors="coerce")
    out["_raw"] = out["_raw"].fillna(out["_projection"])
    out["_global"] = out["_global"].fillna(out["_projection"])
    return out.dropna(subset=["_actual", "_raw"])


@st.cache_data(ttl=300, show_spinner=False)
def get_global_k_calibration():
    """Rolling OLS calibration, guarded against unstable coefficient jumps."""
    hist = _history_before_today().sort_values("_date", ascending=False).head(300)
    if len(hist) < 60:
        return {"intercept": 1.83, "slope": 0.68, "sample": int(len(hist)), "source": "validated tracker fallback", "error_std": 2.10}
    x = hist["_raw"].astype(float).tolist()
    y = hist["_actual"].astype(float).tolist()
    mx = sum(x) / len(x); my = sum(y) / len(y)
    var = sum((v - mx) ** 2 for v in x)
    slope = (sum((a - mx) * (b - my) for a, b in zip(x, y)) / var) if var > 1e-9 else 0.68
    intercept = my - slope * mx
    slope = _cap(slope, 0.55, 0.88)
    intercept = _cap(intercept, 0.25, 2.75)
    errors = [b - (intercept + slope * a) for a, b in zip(x, y)]
    error_std = statistics.pstdev(errors) if len(errors) >= 2 else 2.10
    return {"intercept": round(intercept, 4), "slope": round(slope, 4), "sample": len(hist), "source": "rolling last 300 completed projections", "error_std": round(_cap(error_std, 1.4, 3.2), 3)}


def _residual_series(frame):
    residuals = []
    for _, row in frame.iterrows():
        actual = _safe_float_or_none(row.get("_actual"))
        baseline = _safe_float_or_none(row.get("_global"))
        if baseline is None:
            baseline = _safe_float_or_none(row.get("_projection"))
        if actual is not None and baseline is not None:
            residuals.append(actual - baseline)
    return residuals


def get_pitcher_residual_adjustment(pitcher):
    hist = _history_before_today()
    if hist.empty:
        return {"adjustment": 0.0, "sample": 0, "weighted_residual": 0.0, "status": "No pitcher history"}
    target = normalize_name_for_match(pitcher)
    rows = hist[hist["Pitcher"].astype(str).apply(normalize_name_for_match) == target].sort_values("_date", ascending=False).head(3)
    residuals = _residual_series(rows)
    if not residuals:
        return {"adjustment": 0.0, "sample": 0, "weighted_residual": 0.0, "status": "No completed pitcher residuals"}
    weights = [0.70, 0.20, 0.10][:len(residuals)]
    weighted = sum(r * w for r, w in zip(residuals, weights)) / max(0.01, sum(weights))
    multiplier = 0.15 if len(residuals) == 1 else 0.20 if len(residuals) == 2 else 0.25
    adjustment = _cap(weighted * multiplier, -0.50, 0.50)
    return {"adjustment": round(adjustment, 3), "sample": len(residuals), "weighted_residual": round(weighted, 3), "status": f"{len(residuals)} prior starts; {multiplier:.0%} shrink, ±0.50 cap"}


def get_opponent_residual_adjustment(opponent):
    hist = _history_before_today()
    if hist.empty:
        return {"adjustment": 0.0, "sample": 0, "mean_residual": 0.0, "status": "No opponent history"}
    aliases = _normalized_team_aliases_for_match(opponent)
    rows = hist[hist["Opponent"].astype(str).apply(lambda x: bool(_normalized_team_aliases_for_match(x).intersection(aliases)))]
    rows = rows.sort_values("_date", ascending=False).head(60)
    residuals = _residual_series(rows)
    n = len(residuals)
    if n < 10:
        return {"adjustment": 0.0, "sample": n, "mean_residual": round(sum(residuals)/n, 3) if n else 0.0, "status": "Shadow only until 10 completed opponent matchups"}
    mean_residual = sum(residuals) / n
    shrink = n / (n + 20.0)
    adjustment = _cap(mean_residual * shrink * 0.40, -0.35, 0.35)
    return {"adjustment": round(adjustment, 3), "sample": n, "mean_residual": round(mean_residual, 3), "status": f"{n} matchups; empirical-Bayes shrink {shrink:.2f}, ±0.35 cap"}


def get_archetype_residual_shadow(archetype):
    """Heavily shrunk archetype residual used only in the shadow projection."""
    bucket = str(archetype or "").strip()
    if not bucket:
        return {"archetype": bucket, "adjustment": 0.0, "sample": 0, "mean_residual": 0.0, "status": "No archetype history"}
    try:
        hist = _history_before_today()
    except Exception:
        hist = pd.DataFrame()
    if hist.empty or "Pitcher Archetype" not in hist.columns:
        return {"archetype": bucket, "adjustment": 0.0, "sample": 0, "mean_residual": 0.0, "status": "No archetype history"}
    rows = hist[hist["Pitcher Archetype"].astype(str).str.strip().str.lower() == bucket.lower()].copy()
    rows = rows.sort_values("_date", ascending=False).head(100)
    residuals = _residual_series(rows)
    n = len(residuals)
    if n < 15:
        return {"archetype": bucket, "adjustment": 0.0, "sample": n, "mean_residual": round(sum(residuals)/n, 3) if n else 0.0, "status": "Shadow only until 15 completed archetype starts"}
    mean_residual = sum(residuals) / n
    shrink = n / (n + 35.0)
    adjustment = _cap(mean_residual * shrink * 0.25, -0.25, 0.25)
    return {"archetype": bucket, "adjustment": round(adjustment, 3), "sample": n, "mean_residual": round(mean_residual, 3), "status": f"{n} archetype starts; shadow-only shrink {shrink:.2f}, ±0.25 cap"}


def build_projection_data_health(lineup_details, arsenal_details, auto_weather=None, bulk_context=None):
    """Return transparent source/fallback diagnostics and a 0-100 health score."""
    issues = []
    score = 100.0
    lineup = lineup_details or {}
    arsenal = arsenal_details or {}
    weather = auto_weather or {}
    hitters = int(lineup.get("hitters_found", 0) or 0)
    if hitters < 7:
        issues.append(f"Only {hitters} confirmed-lineup hitter K samples")
        score -= 12
    if "confirmed" not in str(lineup.get("source", "")).lower():
        issues.append("Lineup source is not explicitly confirmed")
        score -= 7
    arsenal_status = str(arsenal.get("status", "") or "").lower()
    arsenal_rows = arsenal.get("details")
    if "neutral" in arsenal_status or "did not load" in arsenal_status or (hasattr(arsenal_rows, "empty") and arsenal_rows.empty):
        issues.append("Pitch-arsenal matchup is using a neutral/fallback value")
        score -= 10
    weather_source = str(weather.get("source", "") or "")
    if weather and not weather_source.startswith("OddsTrader"):
        issues.append("Stadium weather is using a fallback/manual source")
        score -= 5
    if bulk_context and bulk_context.get("enabled"):
        confidence = str(bulk_context.get("confidence", "") or "").upper()
        if not bulk_context.get("bulk_pitcher"):
            issues.append("Opener game has no identified bulk pitcher")
            score -= 18
        elif confidence not in ["CONFIRMED", "HIGH"]:
            issues.append(f"Bulk pitcher confidence is {confidence.title() or 'unconfirmed'}")
            score -= 8
    return {"score": round(_cap(score, 35, 100), 1), "issues": issues, "status": "All primary sources healthy" if not issues else " | ".join(issues)}


def pitcher_skill_snapshot(pitcher, pitcher_this_year, pitcher_last_year, arsenal_details=None):
    """Save stable season-skill fields now so future pitch-quality trend tests have history."""
    k_pct = get_value(pitcher_this_year, "Player", pitcher, "K%", None)
    whiff_pct = get_value(pitcher_this_year, "Player", pitcher, "Whiff %", None)
    if k_pct in [None, ""]:
        k_pct = get_value(pitcher_last_year, "Player", pitcher, "K%", "")
    if whiff_pct in [None, ""]:
        whiff_pct = get_value(pitcher_last_year, "Player", pitcher, "Whiff %", "")
    arsenal = arsenal_details or {}
    return {
        "k_pct": clean_percent(k_pct) if k_pct not in [None, ""] else "",
        "whiff_pct": clean_percent(whiff_pct) if whiff_pct not in [None, ""] else "",
        "arsenal_score": arsenal.get("score", ""),
        "weapon_count": arsenal.get("weapon_count", 0),
    }


def _normal_cdf(value):
    return 0.5 * (1.0 + math.erf(float(value) / math.sqrt(2.0)))


def k_market_probabilities(projection, line, expected_std):
    try:
        projection = float(projection); line = float(line); expected_std = max(0.75, float(expected_std))
    except Exception:
        return {"over": 0.5, "under": 0.5, "push": 0.0}
    integer_line = abs(line - round(line)) < 1e-9
    if integer_line:
        lower = (line - 0.5 - projection) / expected_std
        upper = (line + 0.5 - projection) / expected_std
        under = _normal_cdf(lower)
        push = max(0.0, _normal_cdf(upper) - _normal_cdf(lower))
        over = max(0.0, 1.0 - under - push)
    else:
        over = 1.0 - _normal_cdf((line - projection) / expected_std)
        under = 1.0 - over
        push = 0.0
    return {"over": _cap(over, 0.01, 0.99), "under": _cap(under, 0.01, 0.99), "push": _cap(push, 0.0, 0.50)}


def pitcher_projection_reliability(pitcher, volatility, lineup_details, calibration, recent_form, role="Starter", bulk_confidence="", data_health=None):
    score = 82.0
    reasons = []
    if str(volatility) == "Medium volatility": score -= 7; reasons.append("medium workload volatility")
    elif str(volatility) == "High volatility": score -= 14; reasons.append("high workload volatility")
    hitters = int((lineup_details or {}).get("hitters_found", 0) or 0)
    source = str((lineup_details or {}).get("source", ""))
    if hitters < 7: score -= 10; reasons.append(f"only {hitters} lineup K samples")
    if "confirmed" not in source.lower(): score -= 5; reasons.append("lineup source not explicitly confirmed")
    if int((calibration or {}).get("sample", 0) or 0) < 100: score -= 4; reasons.append("limited global calibration sample")
    recent_starts = int((recent_form or {}).get("starts", 0) or 0)
    if recent_starts < 2: score -= 4; reasons.append("limited pitcher residual history")
    if str((recent_form or {}).get("consistency", "")).upper() == "VOLATILE": score -= 6; reasons.append("volatile historical projection errors")
    role_upper = str(role or "Starter").upper()
    if role_upper == "OPENER": score -= 14; reasons.append("opener workload uncertainty")
    elif role_upper == "BULK":
        score -= 7; reasons.append("bulk entry/workload uncertainty")
        if str(bulk_confidence).upper() not in ["CONFIRMED", "HIGH"]:
            score -= 6; reasons.append("bulk role not confirmed")
    for issue in (data_health or []):
        score -= 3
        reasons.append(str(issue))
    return {"score": round(_cap(score, 35, 95), 1), "reasons": reasons}


def calibrate_pitcher_projection(raw_projection, pitcher, opponent, volatility, lineup_details, recent_form=None, role="Starter", bulk_confidence="", archetype="", data_health=None):
    raw = max(0.0, float(raw_projection or 0.0))
    global_fit = get_global_k_calibration()
    global_projection = float(global_fit["intercept"]) + float(global_fit["slope"]) * raw
    pitcher_adj = get_pitcher_residual_adjustment(pitcher)
    opponent_adj = get_opponent_residual_adjustment(opponent)
    final = max(0.0, global_projection + pitcher_adj["adjustment"] + opponent_adj["adjustment"])
    recent = recent_form or get_pitcher_recent_form_summary(pitcher)
    recent_error = _safe_float_or_none((recent or {}).get("error_std"))
    global_error = float(global_fit.get("error_std", 2.10) or 2.10)
    if recent_error is not None and int((recent or {}).get("starts", 0) or 0) >= 3:
        expected_std = _cap((0.55 * global_error) + (0.45 * recent_error), 1.35, 3.20)
    else:
        expected_std = _cap(global_error, 1.50, 3.00)
    reliability = pitcher_projection_reliability(
        pitcher, volatility, lineup_details, global_fit, recent, role=role,
        bulk_confidence=bulk_confidence, data_health=(data_health or {}).get("issues", [])
    )
    archetype_shadow = get_archetype_residual_shadow(archetype)
    shadow = max(0.0, global_projection + _cap(float(pitcher_adj.get("weighted_residual", 0)) * 0.35, -0.70, 0.70) + opponent_adj["adjustment"] + archetype_shadow["adjustment"])
    return {
        "raw_projection": round(raw, 3), "global_projection": round(global_projection, 3),
        "pitcher_adjustment": round(float(pitcher_adj["adjustment"]), 3),
        "opponent_adjustment": round(float(opponent_adj["adjustment"]), 3),
        "final_projection": round(final, 3), "shadow_projection": round(shadow, 3),
        "expected_std": round(expected_std, 3), "reliability": reliability,
        "global_fit": global_fit, "pitcher_history": pitcher_adj, "opponent_history": opponent_adj,
        "archetype_shadow": archetype_shadow, "data_health": data_health or {"score": 100, "issues": [], "status": "All primary sources healthy"},
        "status": f"Raw {raw:.2f} → global {global_projection:.2f} → pitcher {pitcher_adj['adjustment']:+.2f} → opponent {opponent_adj['adjustment']:+.2f} = {final:.2f}; shadow {shadow:.2f}",
    }


def _pitch_count_projection(projected_ip, role="Starter"):
    ip = max(0.0, float(projected_ip or 0))
    pitches_per_ip = 15.8 if str(role).upper() == "STARTER" else 16.4
    return round(ip * pitches_per_ip, 1)


def _projected_batters_faced(projected_ip):
    return round(max(0.0, float(projected_ip or 0)) * 4.3, 1)


@st.cache_data(ttl=900, show_spinner=False)
def _active_pitchers_for_team(team_name, game_date_text=""):
    team_id = _team_id_from_name(team_name)
    if not team_id:
        return []
    try:
        response = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster",
            params={"rosterType": "active", "date": game_date_text or today_et_string()}, timeout=20,
        )
        response.raise_for_status()
        rows = []
        for entry in response.json().get("roster", []):
            pos = (entry.get("position") or {}).get("abbreviation", "")
            if pos != "P":
                continue
            person = entry.get("person", {}) or {}
            name = to_last_first(person.get("fullName", ""))
            if name:
                rows.append({"name": name, "id": person.get("id", "")})
        return rows
    except Exception:
        return []


@st.cache_data(ttl=600, show_spinner=False)
def detect_expected_bulk_pitcher(team_name, opener, game_date_text=""):
    """Search recent news text and match named active-roster pitchers.

    Google News RSS is free and does not require an API key. The result is always
    shown with confidence/source and can be overridden manually.
    """
    roster = _active_pitchers_for_team(team_name, game_date_text)
    opener_variants = name_match_variants(opener)
    candidates = [r for r in roster if not opener_variants.intersection(name_match_variants(r.get("name", "")))]
    query = f'"{team_name}" ("bulk relief" OR "bulk pitcher" OR "behind the opener" OR piggyback OR "primary pitcher")'
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        root = ET.fromstring(response.content)
        items = root.findall(".//item")[:20]
    except Exception as exc:
        return {"pitcher": "", "confidence": "Unknown", "source": "", "status": f"Automatic news search unavailable: {exc}", "roster": [r.get("name", "") for r in roster]}

    keyword_strength = {
        "bulk relief": 5, "bulk pitcher": 5, "behind the opener": 5,
        "primary pitcher": 4, "piggyback": 3, "long relief": 2, "follow the opener": 4,
    }
    best = None
    for item in items:
        title = html.unescape(item.findtext("title", ""))
        description = re.sub(r"<[^>]+>", " ", html.unescape(item.findtext("description", "")))
        source = item.findtext("source", "") or "Google News"
        link = item.findtext("link", "")
        combined = f"{title} {description}".lower()
        strength = max([value for key, value in keyword_strength.items() if key in combined] or [0])
        if strength <= 0:
            continue
        for roster_entry in candidates:
            player = roster_entry.get("name", "")
            variants = name_match_variants(player)
            mentioned = any(v and v in normalize_match_text(combined) for v in variants)
            if not mentioned:
                continue
            title_mentioned = any(v and v in normalize_match_text(title) for v in variants)
            score = strength + (3 if title_mentioned else 1)
            record = {"pitcher": player, "score": score, "source": source, "headline": title, "link": link}
            if best is None or score > best["score"]:
                best = record
    if not best:
        return {"pitcher": "", "confidence": "Unknown", "source": "Google News RSS", "status": "No active-roster bulk pitcher was explicitly identified in recent news. Select manually.", "roster": [r.get("name", "") for r in roster]}
    confidence = "Confirmed" if best["score"] >= 8 else "Likely" if best["score"] >= 6 else "Inferred"
    return {
        "pitcher": best["pitcher"], "confidence": confidence,
        "source": f"{best['source']}: {best['headline']}", "source_url": best.get("link", ""),
        "status": f"{confidence} bulk candidate found from recent news.",
        "roster": [r.get("name", "") for r in roster],
    }


def render_bulk_game_controls(team, opener, game_date_text, game_key, side_key):
    enabled = st.checkbox(f"{team} opener / planned bulk game", value=False, key=f"{side_key}_bullpen_game_{game_key}")
    context = {"enabled": enabled, "opener": opener, "bulk_pitcher": "", "confidence": "", "source": "", "expected_opener_ip": 1.3, "expected_bulk_ip": 4.7, "expected_entry_inning": 2}
    if not enabled:
        return context
    detection = detect_expected_bulk_pitcher(team, opener, game_date_text)
    roster = detection.get("roster", []) or []
    detected = detection.get("pitcher", "")
    options = ["(No bulk pitcher selected)"] + [p for p in roster if normalize_name_for_match(p) != normalize_name_for_match(opener)]
    default_index = options.index(detected) if detected in options else 0
    selected = st.selectbox(f"{team} expected bulk pitcher", options, index=default_index, key=f"{side_key}_bulk_pitcher_{game_key}")
    opener_ip = st.number_input(f"{team} expected opener IP", min_value=0.3, max_value=3.0, value=1.3, step=0.1, key=f"{side_key}_opener_ip_{game_key}")
    bulk_ip = st.number_input(f"{team} expected bulk IP", min_value=1.0, max_value=7.0, value=4.7, step=0.1, key=f"{side_key}_bulk_ip_{game_key}")
    entry = st.number_input(f"{team} expected bulk entry inning", min_value=1, max_value=6, value=2, step=1, key=f"{side_key}_bulk_entry_{game_key}")
    if detected:
        st.caption(f"Auto-detection: {detection.get('confidence')} — {detection.get('source', '')}")
    else:
        st.caption(detection.get("status", "No automatic candidate found."))
    selected_pitcher = "" if selected.startswith("(") else selected
    manual_override = bool(selected_pitcher and normalize_name_for_match(selected_pitcher) != normalize_name_for_match(detected))
    confidence = "Manual" if manual_override else detection.get("confidence", "Unknown")
    source = "Manual override" if manual_override else detection.get("source", "")
    context.update({
        "bulk_pitcher": selected_pitcher, "confidence": confidence, "source": source,
        "source_url": detection.get("source_url", ""), "expected_opener_ip": float(opener_ip),
        "expected_bulk_ip": float(bulk_ip), "expected_entry_inning": int(entry),
        "manual_override": manual_override, "detection_status": detection.get("status", ""),
    })
    return context


def _bulk_game_pitching_profile(opener, bulk_context, pitcher_this_year, pitcher_last_year, opener_arsenal=None, bullpen_score=None, is_home_start=None):
    bulk_context = bulk_context or {}
    bulk_pitcher = str(bulk_context.get("bulk_pitcher", "") or "")
    if not bulk_pitcher:
        return _moneyline_pitcher_profile(opener, pitcher_this_year, pitcher_last_year, opener_arsenal, bullpen_score, is_home_start=is_home_start)
    opener_profile = _moneyline_pitcher_profile(opener, pitcher_this_year, pitcher_last_year, opener_arsenal, None, is_home_start=is_home_start)
    bulk_profile = _moneyline_pitcher_profile(bulk_pitcher, pitcher_this_year, pitcher_last_year, None, None, is_home_start=is_home_start)
    opener_ip = _cap(float(bulk_context.get("expected_opener_ip", 1.3) or 1.3), 0.3, 3.0)
    bulk_ip = _cap(float(bulk_context.get("expected_bulk_ip", 4.7) or 4.7), 1.0, max(1.0, 8.5 - opener_ip))
    total_ip = _cap(opener_ip + bulk_ip, 1.5, 8.5)
    run_rate = ((float(opener_profile.get("starter_run_rate", 4.35)) * opener_ip) + (float(bulk_profile.get("starter_run_rate", 4.35)) * bulk_ip)) / total_ip
    matchup = ((float(opener_profile.get("matchup_score", 0)) * opener_ip) + (float(bulk_profile.get("matchup_score", 0)) * bulk_ip)) / total_ip
    return {
        "mode": "opener_bulk", "matchup_score": round(matchup, 2), "projected_ip": round(total_ip, 2),
        "starter_run_rate": round(run_rate, 2), "starter_runs_allowed": round(run_rate * total_ip / 9.0, 2),
        "opener": opener, "opener_projected_ip": round(opener_ip, 2), "opener_profile": opener_profile,
        "bulk_pitcher": bulk_pitcher, "bulk_projected_ip": round(bulk_ip, 2), "bulk_profile": bulk_profile,
        "bulk_confidence": bulk_context.get("confidence", ""), "bulk_source": bulk_context.get("source", ""),
        "expected_entry_inning": bulk_context.get("expected_entry_inning", ""),
        "status": f"Opener {opener} ({opener_ip:.1f} IP) + bulk {bulk_pitcher} ({bulk_ip:.1f} IP) + remaining bullpen; bulk is removed from generic bullpen innings.",
    }


def build_bulk_pitcher_k_projection(bulk_context, opponent, game_key, batting_side, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df, game_environment, team, slate_date_text):
    bulk_context = bulk_context or {}
    pitcher = str(bulk_context.get("bulk_pitcher", "") or "")
    if not pitcher:
        return None
    raw_six, arsenal = six_inning_strikeouts(pitcher, opponent, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df, return_details=True)
    throws = get_value(pitcher_this_year, "Player", pitcher, "Throws", get_value(pitcher_last_year, "Player", pitcher, "Throws", "R"))
    lineup = build_lineup_k_blend_details(game_key, batting_side, opponent, throws, team_batting_rhp, team_batting_lhp, pitcher, pitcher_arsenal_df, team_pitch_type_df)
    context = build_pitcher_k_context(pitcher, team, opponent, game_environment, pitcher_this_year, pitcher_last_year, arsenal, slate_date_text=slate_date_text)
    six_final = apply_k_context_projection(apply_lineup_k_adjustment(raw_six, lineup), context)
    expected_ip = _cap(float(bulk_context.get("expected_bulk_ip", 4.7) or 4.7), 1.0, 7.0)
    role_raw = max(0.0, six_final * expected_ip / 6.0)
    recent = get_pitcher_recent_form_summary(pitcher)
    vol = strikeout_volatility(pitcher, pitcher_this_year, pitcher_last_year)
    bulk_health = build_projection_data_health(lineup, arsenal, (game_environment or {}).get("auto_weather", {}), bulk_context)
    archetype = (context.get("archetype", {}) or {}).get("bucket", "")
    skill_snapshot = pitcher_skill_snapshot(pitcher, pitcher_this_year, pitcher_last_year, arsenal)
    cal = calibrate_pitcher_projection(role_raw, pitcher, opponent, vol, lineup, recent_form=recent, role="Bulk", bulk_confidence=bulk_context.get("confidence", ""), archetype=archetype, data_health=bulk_health)
    final = cal["final_projection"]
    projected_bf = _projected_batters_faced(expected_ip)
    return {
        "pitcher": pitcher, "team": team, "opponent": opponent, "role": "Bulk",
        "raw_projection": role_raw, "projection": final, "six_ip": six_final, "expected_ip": expected_ip,
        "projected_pitches": _pitch_count_projection(expected_ip, "Bulk"), "projected_bf": projected_bf,
        "projected_k_rate": final / projected_bf if projected_bf > 0 else 0,
        "arsenal": arsenal, "lineup": lineup, "context": context, "recent": recent,
        "volatility": vol, "calibration": cal, "data_health": bulk_health,
        "archetype": archetype, "skill_snapshot": skill_snapshot,
    }


def save_game_projection_history(row):
    df = read_sheet(GAME_PROJECTION_HISTORY_TAB, GAME_PROJECTION_HISTORY_COLUMNS)
    for col in GAME_PROJECTION_HISTORY_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    game_key = str(row.get("Game Key", "")); game_date = str(row.get("Date", ""))
    if not df.empty:
        df = df[~((df["Game Key"].astype(str) == game_key) & (df["Date"].astype(str) == game_date))].copy()
    clean = {col: row.get(col, "") for col in GAME_PROJECTION_HISTORY_COLUMNS}
    df = pd.concat([df, pd.DataFrame([clean])], ignore_index=True)
    return write_sheet(GAME_PROJECTION_HISTORY_TAB, df, GAME_PROJECTION_HISTORY_COLUMNS)


def _game_result_from_feed(game_key):
    try:
        response = requests.get(f"https://statsapi.mlb.com/api/v1.1/game/{game_key}/feed/live", timeout=25)
        response.raise_for_status(); data = response.json()
        status = (((data.get("gameData") or {}).get("status") or {}).get("detailedState", ""))
        if "Final" not in status:
            return None
        linescore = (data.get("liveData") or {}).get("linescore", {}) or {}
        teams = linescore.get("teams", {}) or {}
        away_runs = int(((teams.get("away") or {}).get("runs", 0)) or 0)
        home_runs = int(((teams.get("home") or {}).get("runs", 0)) or 0)
        first = (linescore.get("innings", [{}]) or [{}])[0]
        first_runs = int(((first.get("away") or {}).get("runs", 0)) or 0) + int(((first.get("home") or {}).get("runs", 0)) or 0)
        return {"away_runs": away_runs, "home_runs": home_runs, "first_inning_runs": first_runs}
    except Exception:
        return None


def update_game_projection_history_actuals():
    df = read_sheet(GAME_PROJECTION_HISTORY_TAB, GAME_PROJECTION_HISTORY_COLUMNS)
    if df is None or df.empty:
        return {"updated": 0}
    out = df.copy(); updated = 0
    try:
        today_text = today_et_string()
    except Exception:
        today_text = str(date.today())
    pending_indices = []
    for idx, row in out.iterrows():
        if str(row.get("Actual Total", "")).strip() not in ["", "nan", "None"]:
            continue
        row_date = str(row.get("Date", "")).strip()
        if not row_date or row_date >= today_text:
            continue
        pending_indices.append(idx)
    for idx in pending_indices[-50:]:
        row = out.loc[idx]
        result = _game_result_from_feed(str(row.get("Game Key", "")))
        if not result:
            continue
        away = result["away_runs"]; home = result["home_runs"]; total = away + home
        away_proj = _safe_float_or_none(row.get("Away Runs Projection")); home_proj = _safe_float_or_none(row.get("Home Runs Projection")); total_proj = _safe_float_or_none(row.get("Total Projection"))
        winner = str(row.get("Home Team", "")) if home > away else str(row.get("Away Team", ""))
        better_ml_text = str(row.get("Better ML", ""))
        first_grade = str(row.get("First Inning Grade", "")).upper()
        ml_correct = "TRUE" if winner and normalize_match_text(winner) in normalize_match_text(better_ml_text) else "FALSE"
        first_correct = "TRUE" if ((first_grade == "ELITE NRFI" and result["first_inning_runs"] == 0) or ("YRFI" in first_grade and result["first_inning_runs"] > 0)) else "FALSE" if first_grade in ["ELITE NRFI", "ELITE YRFI", "YRFI"] else ""
        values = {
            "Actual Away Runs": away, "Actual Home Runs": home, "Actual Total": total, "Actual Winner": winner,
            "First Inning Runs": result["first_inning_runs"],
            "Away Run Residual": round(away - away_proj, 2) if away_proj is not None else "",
            "Home Run Residual": round(home - home_proj, 2) if home_proj is not None else "",
            "Total Residual": round(total - total_proj, 2) if total_proj is not None else "",
            "ML Correct": ml_correct, "First Inning Correct": first_correct, "Updated Time ET": _recent_form_time_label(),
        }
        for col, val in values.items(): out.at[idx, col] = val
        updated += 1
    if updated: write_sheet(GAME_PROJECTION_HISTORY_TAB, out, GAME_PROJECTION_HISTORY_COLUMNS)
    return {"updated": updated}


def ensure_model_version_logged():
    try:
        df = read_sheet(MODEL_CHANGE_LOG_TAB, MODEL_CHANGE_LOG_COLUMNS)
        if not df.empty and MODEL_VERSION in df["Version"].astype(str).tolist():
            return
        row = {
            "Version": MODEL_VERSION, "Effective Date": today_et_string(),
            "Changes": "V15.1 data-health repair: restored opponent Savant plate-discipline team mapping through official MLB player IDs/rosters, added partial official MLB fallbacks and transparent failure reasons, separated Data Health from Projection Reliability, and exposed exact recent shape/release changes. V15 projection logic plus moneyline, totals, NRFI/YRFI, opener/bulk, and Elite YRFI behavior preserved.",
            "Created Time ET": _recent_form_time_label(),
        }
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        write_sheet(MODEL_CHANGE_LOG_TAB, df, MODEL_CHANGE_LOG_COLUMNS)
    except Exception:
        pass


# ============================================================================
# V13 FULL NON-PITCHER MODEL UPGRADE OVERRIDES
# Moneyline, totals, NRFI/YRFI, live splits, bullpens, pricing, and calibration.
# These final definitions intentionally replace legacy duplicate helpers above.
# ============================================================================

# Preserve selected v12 implementations so v13 can reuse their stable structure.
_v12_moneyline_probability = _legacy_moneyline_probability_v1
_v12_moneyline_confidence_score = _legacy_moneyline_confidence_score_v1
_v12_pitcher_skill_inputs = _legacy__pitcher_skill_inputs_v1


def _american_profit_multiplier(odds):
    """Profit returned per 1 unit risked, excluding returned stake."""
    try:
        odds = float(odds)
        if odds > 0:
            return odds / 100.0
        if odds < 0:
            return 100.0 / abs(odds)
    except Exception:
        pass
    return 100.0 / 110.0


def _two_way_no_vig_probabilities(home_odds, away_odds):
    """Return raw implied and normalized no-vig probabilities for a two-way market."""
    home_raw = american_odds_to_implied_prob(home_odds)
    away_raw = american_odds_to_implied_prob(away_odds)
    total = home_raw + away_raw
    if total > 0:
        return home_raw, away_raw, home_raw / total, away_raw / total
    return home_raw, away_raw, 0.5, 0.5


def _poisson_probabilities(mean_runs, max_runs=25):
    mean_runs = max(0.05, float(mean_runs or 0.05))
    probs = []
    p = math.exp(-mean_runs)
    probs.append(p)
    for k in range(1, int(max_runs) + 1):
        p = p * mean_runs / k
        probs.append(p)
    tail = max(0.0, 1.0 - sum(probs))
    probs[-1] += tail
    return probs


def _skellam_home_win_probability(home_runs, away_runs):
    """Win probability from two independent Poisson scoring distributions.

    Tied regulation outcomes are allocated 54% to the home team to approximate
    extra-inning/home-field resolution instead of treating ties as half a win.
    """
    hp = _poisson_probabilities(home_runs)
    ap = _poisson_probabilities(away_runs)
    home_win = 0.0
    tie = 0.0
    for h, ph in enumerate(hp):
        for a, pa in enumerate(ap):
            joint = ph * pa
            if h > a:
                home_win += joint
            elif h == a:
                tie += joint
    return _cap(home_win + (tie * 0.54), 0.24, 0.76), tie


def _poisson_total_market_probabilities(projected_total, market_total):
    """Calculate over, under, and push probabilities from the projected run mean."""
    lam = max(0.1, float(projected_total or 0.1))
    line = float(market_total or 0.0)
    probs = _poisson_probabilities(lam, max_runs=30)
    over = under = push = 0.0
    integer_line = abs(line - round(line)) < 1e-9
    for runs, prob in enumerate(probs):
        if integer_line and runs == int(round(line)):
            push += prob
        elif runs > line:
            over += prob
        else:
            under += prob
    return over, under, push


def _side_expected_value(win_prob, lose_prob, odds):
    return (float(win_prob or 0) * _american_profit_multiplier(odds)) - float(lose_prob or 0)


@st.cache_data(ttl=60 * 60, show_spinner=False)
def _v13_team_stats(group="hitting", stats_type="season", sit_code="", start_date="", end_date=""):
    """Official MLB team stats with optional situation and date filters."""
    try:
        params = {
            "sportIds": 1,
            "group": group,
            "stats": stats_type,
            "season": MLB_SEASON,
        }
        if sit_code:
            params["sitCodes"] = sit_code
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date
        response = requests.get("https://statsapi.mlb.com/api/v1/teams/stats", params=params, timeout=35)
        response.raise_for_status()
        rows = []
        for block in response.json().get("stats", []):
            for split in block.get("splits", []):
                team = split.get("team", {}) or {}
                stat = split.get("stat", {}) or {}
                row = {"Teams": _normalize_mlb_team_name(team.get("name", ""))}
                row.update(stat)
                rows.append(row)
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60 * 60)
def load_team_hitting_stats_live():
    """Live team offense table with actual run and baserunning fields."""
    df = _v13_team_stats("hitting", "season")
    if df.empty:
        df = _mlb_team_stats("hitting", MLB_SEASON, "season")
    if df is None or df.empty:
        teams = sorted(set(TEAM_ABBR_MAP.values()))
        return pd.DataFrame({
            "Teams": teams, "Games": 0, "Runs": 0, "R/G": 4.35,
            "Hits": 250, "RBI's": 0, "SB": 0, "CS": 0,
            "Team Batting Avg.": 0.250, "Team On-Base %": 0.315,
            "Team Slugging %": 0.410,
        })
    games = pd.to_numeric(df.get("gamesPlayed", 0), errors="coerce").fillna(0)
    runs = pd.to_numeric(df.get("runs", 0), errors="coerce").fillna(0)
    return pd.DataFrame({
        "Teams": df.get("Teams", "").astype(str).str.strip(),
        "Games": games,
        "Runs": runs,
        "R/G": (runs / games.replace(0, pd.NA)).fillna(4.35),
        "Hits": pd.to_numeric(df.get("hits", 250), errors="coerce").fillna(250),
        "RBI's": pd.to_numeric(df.get("rbi", 0), errors="coerce").fillna(0),
        "SB": pd.to_numeric(df.get("stolenBases", 0), errors="coerce").fillna(0),
        "CS": pd.to_numeric(df.get("caughtStealing", 0), errors="coerce").fillna(0),
        "Team Batting Avg.": pd.to_numeric(df.get("avg", 0.250), errors="coerce").fillna(0.250),
        "Team On-Base %": pd.to_numeric(df.get("obp", 0.315), errors="coerce").fillna(0.315),
        "Team Slugging %": pd.to_numeric(df.get("slg", 0.410), errors="coerce").fillna(0.410),
    }).query("Teams != ''").reset_index(drop=True)


@st.cache_data(ttl=60 * 60)
def load_team_batting_split_live(split):
    """Load true team vs-RHP/vs-LHP stats when MLB exposes the situation split.

    If the API does not return a credible split table, the function explicitly
    falls back to overall team offense rather than pretending the overall table
    is a true handedness split.
    """
    code = "vl" if str(split).lower().startswith("vl") else "vr"
    df = _v13_team_stats("hitting", "season", sit_code=code)
    overall = _v13_team_stats("hitting", "season")
    split_source = f"MLB situation split {code}"
    split_credible = df is not None and not df.empty and len(df) >= 20
    if split_credible and overall is not None and not overall.empty:
        ratios = []
        for _, row in df.iterrows():
            team_name = row.get("Teams", "")
            overall_row = _team_row_match(overall, team_name)
            split_ab = _team_numeric_from_row(row, ["atBats"], 0)
            overall_ab = _team_numeric_from_row(overall_row, ["atBats"], 0)
            if split_ab > 0 and overall_ab > 0:
                ratios.append(split_ab / overall_ab)
        # A true handedness split should be materially smaller than the overall sample.
        if ratios and (sum(ratios) / len(ratios)) > 0.93:
            split_credible = False
    if not split_credible:
        df = overall
        split_source = "overall-season fallback; MLB handedness split unavailable or sitCode ignored"
    if df is None or df.empty:
        teams = sorted(set(TEAM_ABBR_MAP.values()))
        return pd.DataFrame({
            "Teams": teams, "Games": 0, "At Bats": 1000, "Plate Appearances": 1100,
            "Hits": 250, "Walks": 85, "Strikeouts": 220,
            "Batting Average": 0.250, "On-Base %": 0.315, "Slug %": 0.410,
            "K%": 0.220, "BB%": 0.085, "ISO": 0.160,
            "Split Source": "neutral fallback",
        })
    ab = pd.to_numeric(df.get("atBats", 0), errors="coerce").fillna(0)
    hits = pd.to_numeric(df.get("hits", 0), errors="coerce").fillna(0)
    walks = pd.to_numeric(df.get("baseOnBalls", 0), errors="coerce").fillna(0)
    hbp = pd.to_numeric(df.get("hitByPitch", 0), errors="coerce").fillna(0)
    sf = pd.to_numeric(df.get("sacFlies", 0), errors="coerce").fillna(0)
    so = pd.to_numeric(df.get("strikeOuts", 0), errors="coerce").fillna(0)
    pa = ab + walks + hbp + sf
    avg = pd.to_numeric(df.get("avg", 0.250), errors="coerce").fillna(0.250)
    obp = pd.to_numeric(df.get("obp", 0.315), errors="coerce").fillna(0.315)
    slg = pd.to_numeric(df.get("slg", 0.410), errors="coerce").fillna(0.410)
    return pd.DataFrame({
        "Teams": df.get("Teams", "").astype(str).str.strip(),
        "Games": pd.to_numeric(df.get("gamesPlayed", 0), errors="coerce").fillna(0),
        "At Bats": ab,
        "Plate Appearances": pa,
        "Hits": hits,
        "Walks": walks,
        "Strikeouts": so,
        "Batting Average": avg,
        "On-Base %": obp,
        "Slug %": slg,
        "K%": (so / pa.replace(0, pd.NA)).fillna(0.220),
        "BB%": (walks / pa.replace(0, pd.NA)).fillna(0.085),
        "ISO": (slg - avg).fillna(0.160),
        "Split Source": split_source,
    }).query("Teams != ''").reset_index(drop=True)


@st.cache_data(ttl=60 * 60)
def load_nrfi_team_split_live(hand):
    base = load_team_batting_split_live("vr" if str(hand).lower().startswith("r") else "vl")
    if base is None or base.empty:
        return pd.DataFrame(columns=["Teams", "OBP", "K%", "wOBA", "BB/K", "ISO", "Split Source"])
    so = pd.to_numeric(base.get("Strikeouts", 0), errors="coerce").fillna(0)
    walks = pd.to_numeric(base.get("Walks", 0), errors="coerce").fillna(0)
    obp = pd.to_numeric(base.get("On-Base %", 0.315), errors="coerce").fillna(0.315)
    slg = pd.to_numeric(base.get("Slug %", 0.410), errors="coerce").fillna(0.410)
    avg = pd.to_numeric(base.get("Batting Average", 0.250), errors="coerce").fillna(0.250)
    k_pct = pd.to_numeric(base.get("K%", 0.220), errors="coerce").fillna(0.220)
    iso = pd.to_numeric(base.get("ISO", slg - avg), errors="coerce").fillna(0.160)
    woba_proxy = ((obp * 0.72) + (slg * 0.28)).fillna(0.320)
    return pd.DataFrame({
        "Teams": base.get("Teams", "").astype(str).str.strip(),
        "OBP": obp, "K%": k_pct, "wOBA": woba_proxy,
        "BB/K": (walks / so.replace(0, pd.NA)).fillna(0.50),
        "ISO": iso,
        "Split Source": base.get("Split Source", "").astype(str),
    }).reset_index(drop=True)


@st.cache_data(ttl=60 * 60)
def _mlb_all_player_pitching_stats(season=MLB_SEASON):
    """Player pitching table retaining games started for workload calculations."""
    try:
        response = requests.get(
            "https://statsapi.mlb.com/api/v1/stats",
            params={"stats": "season", "group": "pitching", "playerPool": "ALL", "season": season, "sportIds": 1, "limit": 5000},
            timeout=40,
        )
        response.raise_for_status()
        rows = []
        for block in response.json().get("stats", []):
            for split in block.get("splits", []):
                player = split.get("player", {}) or {}
                stat = split.get("stat", {}) or {}
                if not player.get("fullName"):
                    continue
                ip = _mlb_num(stat.get("inningsPitched", 0), 0)
                games = _mlb_num(stat.get("gamesPlayed", stat.get("games", 0)), 0)
                gs = _mlb_num(stat.get("gamesStarted", 0), 0)
                so = _mlb_num(stat.get("strikeOuts", 0), 0)
                bb = _mlb_num(stat.get("baseOnBalls", 0), 0)
                hr = _mlb_num(stat.get("homeRuns", 0), 0)
                bf = _mlb_num(stat.get("battersFaced", 0), 0)
                rows.append({
                    "Player": to_last_first(player.get("fullName", "")), "Year": season,
                    "G": games, "GS": gs, "Games Started": gs, "IP": ip,
                    "BF": bf if bf > 0 else ip * 4.3, "SO": so, "BB": bb, "HR": hr,
                    "K%": so / bf if bf > 0 else 0, "BB%": bb / bf if bf > 0 else 0,
                    "ERA": _mlb_num(stat.get("era", 0), 0), "WHIP": _mlb_num(stat.get("whip", 0), 0),
                    "xwOBA": 0.320, "Hard Hit %": 0, "Barrel %": 0, "GB%": 0,
                    "Out of Zone %": 0, "In Zone %": 0, "Whiff %": 0, "First Strike %": 0,
                    "Pitches": 0, "BBE": 0, "Throws": "R", "MLBAM ID": player.get("id", ""),
                })
        return pd.DataFrame(rows)
    except Exception as exc:
        st.warning(f"Could not load MLB player pitching stats from Stats API: {exc}")
        return pd.DataFrame(columns=["Player", "Year", "G", "GS", "Games Started", "IP", "BF", "SO", "BB", "HR", "K%", "BB%", "ERA", "xwOBA", "Throws", "MLBAM ID"])


def pitcher_ipg(pitcher, pitcher_this_year, pitcher_last_year):
    """Safe expected starter innings for grading and confidence layers.

    Prevents hybrid relievers from using total relief IP divided by one or two starts.
    """
    current = _pitcher_role_workload(pitcher_this_year, pitcher)
    prior = _pitcher_role_workload(pitcher_last_year, pitcher)
    return (
        float(current.get("estimated_start_ip", 0) or 0),
        float(prior.get("estimated_start_ip", 0) or 0),
    )


def _pitcher_skill_inputs(pitcher, pitcher_this_year, pitcher_last_year):
    """V12 skill inputs with starter workload based on IP/GS rather than IP/G."""
    out = dict(_v12_pitcher_skill_inputs(pitcher, pitcher_this_year, pitcher_last_year))
    this_ipg, last_ipg = pitcher_ipg(pitcher, pitcher_this_year, pitcher_last_year)
    if this_ipg > 0 and last_ipg > 0:
        projected_ip = (0.65 * this_ipg) + (0.35 * last_ipg)
    elif this_ipg > 0:
        projected_ip = this_ipg
    elif last_ipg > 0:
        projected_ip = last_ipg
    else:
        projected_ip = out.get("projected_ip", 5.0)
    out["projected_ip"] = _cap(projected_ip, 3.2, 7.2)
    out["ipg_this"] = this_ipg
    out["ipg_last"] = last_ipg
    out["workload_denominator"] = "GS when available; G fallback"
    return out


def _team_offensive_contact_quality(team, team_hitting=None, split_df=None):
    """Use independent contact metrics; heavily shrink the OPS/ISO fallback.

    ISO/SLG already appear in the offense layer, so they are not allowed to act
    like a second full-strength contact-quality signal.
    """
    row = _team_row_match(split_df, team) if split_df is not None else None
    if row is None:
        row = _team_row_match(team_hitting, team)
    hard = clean_percent(_team_numeric_from_row(row, ["Hard Hit %", "HardHit%", "hard_hit_percent", "Team Hard Hit %"], 0.0))
    barrel = clean_percent(_team_numeric_from_row(row, ["Barrel %", "Barrel%", "barrel_batted_rate", "Team Barrel %"], 0.0))
    xwoba = clean_percent(_team_numeric_from_row(row, ["xwOBA", "xwoba", "Team xwOBA"], 0.0))
    avg = clean_percent(_team_numeric_from_row(row, ["AVG", "Batting Average", "Team Batting Avg."], 0.250))
    slg = clean_percent(_team_numeric_from_row(row, ["SLG", "Slug %", "Team Slugging %"], 0.410))
    obp = clean_percent(_team_numeric_from_row(row, ["OBP", "On-Base %", "Team On-Base %"], 0.315))
    true_fields = []
    score = 0.0
    if hard > 0:
        score += (hard - 0.395) * 22.0
        true_fields.append("hard_hit")
    if barrel > 0:
        score += (barrel - 0.075) * 70.0
        true_fields.append("barrel")
    if xwoba > 0:
        score += (xwoba - 0.320) * 58.0
        true_fields.append("xwoba")
    if true_fields:
        score = _cap(score, -5.0, 5.0)
        source = "independent contact metrics"
    else:
        iso = max(0.0, slg - avg)
        ops = obp + slg
        score = _cap(((ops - 0.725) * 4.0) + ((iso - 0.160) * 5.0), -1.75, 1.75)
        source = "low-weight OPS/ISO fallback; intentionally shrunk to avoid double-counting offense"
    return score, {
        "score": round(score, 2), "hard_hit_rate": round(hard * 100, 1) if hard > 0 else "",
        "barrel_rate": round(barrel * 100, 1) if barrel > 0 else "", "xwoba": round(xwoba, 3) if xwoba > 0 else "",
        "avg": round(avg, 3), "obp": round(obp, 3), "slg": round(slg, 3),
        "source": source, "used": ", ".join(true_fields) if true_fields else "ops_iso_proxy_shrunk",
        "status": "Contact layer is independent when possible and conservative when only traditional offense is available.",
    }


def _bullpen_metric_score(era=4.10, k_rate=0.225, bb_rate=0.085, whip=1.28, xwoba=0.320):
    score = (
        (4.10 - float(era or 4.10)) * 1.60 +
        (float(k_rate or 0.225) - 0.225) * 24.0 +
        (0.085 - float(bb_rate or 0.085)) * 34.0 +
        (1.28 - float(whip or 1.28)) * 3.2 +
        (0.320 - float(xwoba or 0.320)) * 42.0
    )
    return _cap(score, -10.0, 10.0)


@st.cache_data(ttl=60 * 60)
def load_bullpen_stats_live():
    """Load reliever-only season and last-30-day team pitching metrics."""
    season = _v13_team_stats("pitching", "season", sit_code="rp")
    end_day = date.today() - pd.Timedelta(days=1)
    start_day = end_day - pd.Timedelta(days=29)
    recent = _v13_team_stats(
        "pitching", "byDateRange", sit_code="rp",
        start_date=start_day.strftime("%Y-%m-%d"), end_date=end_day.strftime("%Y-%m-%d"),
    )
    def _reliever_split_credible(df, recent_window=False):
        if df is None or df.empty or len(df) < 20:
            return False
        starts = [
            _team_numeric_from_row(row, ["gamesStarted"], 0)
            for _, row in df.iterrows()
        ]
        starts = [x for x in starts if x >= 0]
        # If the API ignored sitCodes=rp, team games started will resemble the schedule count.
        threshold = 5 if recent_window else 12
        return not starts or (sum(starts) / max(1, len(starts))) < threshold
    if not _reliever_split_credible(season, False):
        # Explicit fallback: team pitching is still better than identical neutral bullpens,
        # and the source label prevents it from masquerading as reliever-only data.
        season = _v13_team_stats("pitching", "season")
        season_source = "all-team pitching fallback; reliever split unavailable or sitCode ignored"
    else:
        season_source = "MLB reliever-only season stats"
    if not _reliever_split_credible(recent, True):
        recent = season.copy() if season is not None else pd.DataFrame()
        recent_source = "season fallback; reliever last-30 unavailable or sitCode ignored"
    else:
        recent_source = "MLB reliever-only last 30 days"
    teams = sorted(set(TEAM_ABBR_MAP.values()))
    rows = []
    for team in teams:
        srow = _team_row_match(season, team)
        rrow = _team_row_match(recent, team)
        def metrics(row):
            era = _team_numeric_from_row(row, ["era", "ERA"], 4.10)
            whip = _team_numeric_from_row(row, ["whip", "WHIP"], 1.28)
            so = _team_numeric_from_row(row, ["strikeOuts", "SO", "Strikeouts"], 0)
            bb = _team_numeric_from_row(row, ["baseOnBalls", "BB", "Walks"], 0)
            bf = _team_numeric_from_row(row, ["battersFaced", "BF"], 0)
            k_rate = so / bf if bf > 0 else 0.225
            bb_rate = bb / bf if bf > 0 else 0.085
            # MLB team pitching does not always expose xwOBA. Use a mild ERA/WHIP proxy.
            xwoba = _team_numeric_from_row(row, ["xwOBA", "xwoba"], 0)
            if xwoba <= 0:
                xwoba = _cap(0.320 + ((era - 4.10) * 0.006) + ((whip - 1.28) * 0.035), 0.275, 0.375)
            return era, k_rate, bb_rate, whip, xwoba
        se, sk, sb, sw, sx = metrics(srow)
        re, rk, rb, rw, rx = metrics(rrow)
        rows.append({
            "Teams": team,
            "Season ERA": se, "Season K%": sk, "Season BB%": sb, "Season WHIP": sw, "Season xwOBA": sx,
            "Recent ERA": re, "Recent K%": rk, "Recent BB%": rb, "Recent WHIP": rw, "Recent xwOBA": rx,
            "era": (se * 0.6875) + (re * 0.3125),
            "K%": (sk * 0.6875) + (rk * 0.3125),
            "BB%": (sb * 0.6875) + (rb * 0.3125),
            "WHIP": (sw * 0.6875) + (rw * 0.3125),
            "xwOBA": (sx * 0.6875) + (rx * 0.3125),
            "Season Source": season_source, "Recent Source": recent_source,
        })
    return pd.DataFrame(rows)


def _bullpen_quality_component(team, bullpen_stats, bullpen_fatigue_df=None):
    row = _team_row_match(bullpen_stats, team)
    if row is None:
        season_score = recent_score = 0.0
        data_status = "No bullpen metrics found; neutral quality fallback."
        season_metrics = recent_metrics = {}
    else:
        season_metrics = {
            "era": _team_numeric_from_row(row, ["Season ERA", "era", "ERA"], 4.10),
            "k": clean_percent(_team_numeric_from_row(row, ["Season K%", "K%"], 0.225)),
            "bb": clean_percent(_team_numeric_from_row(row, ["Season BB%", "BB%"], 0.085)),
            "whip": _team_numeric_from_row(row, ["Season WHIP", "WHIP"], 1.28),
            "xwoba": clean_percent(_team_numeric_from_row(row, ["Season xwOBA", "xwOBA"], 0.320)),
        }
        recent_metrics = {
            "era": _team_numeric_from_row(row, ["Recent ERA", "era", "ERA"], season_metrics["era"]),
            "k": clean_percent(_team_numeric_from_row(row, ["Recent K%", "K%"], season_metrics["k"])),
            "bb": clean_percent(_team_numeric_from_row(row, ["Recent BB%", "BB%"], season_metrics["bb"])),
            "whip": _team_numeric_from_row(row, ["Recent WHIP", "WHIP"], season_metrics["whip"]),
            "xwoba": clean_percent(_team_numeric_from_row(row, ["Recent xwOBA", "xwOBA"], season_metrics["xwoba"])),
        }
        season_score = _bullpen_metric_score(season_metrics["era"], season_metrics["k"], season_metrics["bb"], season_metrics["whip"], season_metrics["xwoba"])
        recent_score = _bullpen_metric_score(recent_metrics["era"], recent_metrics["k"], recent_metrics["bb"], recent_metrics["whip"], recent_metrics["xwoba"])
        data_status = f"{row.get('Season Source', '')}; {row.get('Recent Source', '')}"
    fatigue = _cap(float(_bullpen_fatigue_adjustment(team, bullpen_fatigue_df) or 0.0), -9.0, 0.0)
    score = _cap((season_score * 0.55) + (recent_score * 0.25) + (fatigue * 0.20), -8.0, 8.0)
    run_rate = _cap(4.20 - (score * 0.16), 2.85, 6.05)
    return score, {
        "score": round(score, 2), "season_quality_score": round(season_score, 2),
        "recent_quality_score": round(recent_score, 2), "fatigue_adjustment": round(fatigue, 2),
        "bullpen_run_rate": round(run_rate, 2), "season_metrics": season_metrics,
        "recent_metrics": recent_metrics, "data_status": data_status,
        "blend": "55% season reliever skill / 25% last-30 reliever skill / 20% availability-fatigue",
        "status": "Real bullpen quality and current availability are both applied.",
    }


def _project_team_runs(team, base_run_details, offense_score, lineup_score, offensive_contact_score, opposing_pitcher_profile, opposing_bullpen_details, home_field=False):
    """Blend today's pitching allocation with the team's calibrated scoring baseline."""
    starter_ip = float(opposing_pitcher_profile.get("projected_ip", 0.0) or 0.0)
    starter_rate = float(opposing_pitcher_profile.get("starter_run_rate", 4.35) or 4.35)
    bullpen_ip = 9.0 if starter_ip <= 0.1 else max(0.0, 9.0 - starter_ip)
    bullpen_rate = float(opposing_bullpen_details.get("bullpen_run_rate", 4.20) or 4.20)
    starter_runs = starter_rate * (starter_ip / 9.0) if starter_ip > 0 else 0.0
    bullpen_runs = bullpen_rate * (bullpen_ip / 9.0)
    pitching_runs = starter_runs + bullpen_runs
    team_base = float((base_run_details or {}).get("base_runs", 4.35) or 4.35)
    combined_baseline = (pitching_runs * 0.68) + (team_base * 0.32)

    offense_adj = _cap(float(offense_score or 0) * 0.035, -0.26, 0.26)
    lineup_adj = _cap(float(lineup_score or 0) * 0.045, -0.38, 0.38)
    contact_adj = _cap(float(offensive_contact_score or 0) * 0.045, -0.25, 0.25)
    home_adj = 0.13 if home_field else 0.0
    projected = _cap(combined_baseline + offense_adj + lineup_adj + contact_adj + home_adj, 1.70, 7.70)

    neutral_starter = 4.35 * (starter_ip / 9.0) if starter_ip > 0 else 0.0
    neutral_bullpen = 4.20 * (bullpen_ip / 9.0)
    return projected, {
        "projected_runs": round(projected, 2), "base_runs": round(team_base, 2),
        "team_baseline_runs": round(team_base, 2), "pitching_baseline_runs": round(pitching_runs, 2),
        "combined_baseline_runs": round(combined_baseline, 2), "pitching_weight": 0.68, "team_baseline_weight": 0.32,
        "starter_runs_component": round(starter_runs, 2), "bullpen_runs_component": round(bullpen_runs, 2),
        "opposing_starter_adjustment": round(starter_runs - neutral_starter, 3),
        "opposing_bullpen_adjustment": round(bullpen_runs - neutral_bullpen, 3),
        "team_baseline_adjustment": round(team_base - 4.35, 3),
        "team_baseline": base_run_details or {}, "offense_adjustment": round(offense_adj, 2),
        "lineup_adjustment": round(lineup_adj, 2), "offensive_contact_adjustment": round(contact_adj, 2),
        "home_field_adjustment": round(home_adj, 2), "opposing_starter_ip": round(starter_ip, 2),
        "opposing_starter_run_rate": round(starter_rate, 2), "opposing_starter_runs_component": round(starter_runs, 2),
        "opposing_bullpen_ip": round(bullpen_ip, 2), "opposing_bullpen_run_rate": round(bullpen_rate, 2),
        "opposing_bullpen_runs_component": round(bullpen_runs, 2),
        "engine": "v13_68pct_pitching_32pct_team_baseline",
        "status": "Projected runs blend today's starter/bullpen allocation with calibrated team season/recent scoring.",
    }


def build_game_environment(home_team, venue_name="", temperature=72, wind_speed=0, wind_direction="Neutral/Cross", roof_status="Open/Outdoor"):
    """Park run factor is primary; HR/hit factors are only small shape adjustments."""
    profile = get_park_environment_profile(home_team, venue_name)
    try:
        temp = float(temperature if temperature is not None else 72)
    except Exception:
        temp = 72.0
    try:
        wind = max(0.0, float(wind_speed or 0))
    except Exception:
        wind = 0.0
    roof_text = str(roof_status or "").lower()
    wind_text = str(wind_direction or "Neutral/Cross")
    closed = ("closed" in roof_text) or ("dome" in roof_text)
    park_run = _total_clip((float(profile.get("run_factor", 1.0)) - 1.0) * 3.50, -0.55, 0.95)
    hr_shape = _total_clip((float(profile.get("hr_factor", 1.0)) - 1.0) * 0.35, -0.08, 0.10)
    hit_shape = _total_clip((float(profile.get("hit_factor", 1.0)) - 1.0) * 0.30, -0.06, 0.08)
    if closed:
        weather = 0.0
        weather_status = "Roof/dome closed: weather neutralized."
    else:
        temp_adj = _total_clip((temp - 72.0) * 0.012, -0.24, 0.34)
        if "Out" in wind_text:
            wind_adj = _total_clip(wind * 0.025, 0.0, 0.35)
        elif "In" in wind_text:
            wind_adj = -_total_clip(wind * 0.022, 0.0, 0.30)
        else:
            wind_adj = 0.0
        weather = _total_clip(temp_adj + wind_adj, -0.36, 0.52)
        weather_status = f"Outdoor/open weather: {temp:.0f}°F, wind {wind:.0f} mph, {wind_text}."
    total_adj = _total_clip(park_run + hr_shape + hit_shape + weather, -0.70, 1.10)
    k_park = _total_clip((float(profile.get("k_factor", 1.0)) - 1.0) * 2.60, -0.22, 0.18)
    k_adj = _total_clip(k_park - max(0.0, total_adj - 0.25) * 0.14 + max(0.0, -total_adj - 0.20) * 0.08, -0.35, 0.25)
    env_score = _total_clip(50 + (total_adj * 24.0), 0, 100)
    tag = "Extreme Hitter" if env_score >= 67 else "Hitter Friendly" if env_score >= 58 else "Pitcher Friendly" if env_score <= 42 else "Neutral"
    return {
        "park": profile.get("park", venue_name or ""), "home_team": profile.get("home_team", str(home_team or "")),
        "run_factor": round(float(profile.get("run_factor", 1.0)), 3), "hr_factor": round(float(profile.get("hr_factor", 1.0)), 3),
        "hit_factor": round(float(profile.get("hit_factor", 1.0)), 3), "k_factor": round(float(profile.get("k_factor", 1.0)), 3),
        "temperature": round(temp, 1), "wind_speed": round(wind, 1), "wind_direction": wind_text, "roof_status": roof_status,
        "park_run_adjustment": round(park_run + hr_shape + hit_shape, 2), "park_primary_run_adjustment": round(park_run, 2),
        "park_secondary_shape_adjustment": round(hr_shape + hit_shape, 2), "weather_run_adjustment": round(weather, 2),
        "total_run_adjustment": round(total_adj, 2), "per_team_run_adjustment": round(total_adj / 2, 2),
        "k_projection_adjustment": round(k_adj, 2), "run_environment_score": round(env_score, 1),
        "run_environment_tag": tag, "early_hook_risk": "High" if tag == "Extreme Hitter" else "Medium" if tag == "Hitter Friendly" else "Low",
        "weather_status": weather_status,
        "status": f"{profile.get('park', venue_name or 'Park')}: {tag}, run adj {total_adj:+.2f}; overall run factor is primary and HR/hit factors are secondary.",
    }


def moneyline_probability(home, away, hp, ap, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, bullpen_stats=None, use_home_bullpen=False, use_away_bullpen=False, bullpen_fatigue_df=None, home_lineup_details=None, away_lineup_details=None, home_arsenal_details=None, away_arsenal_details=None, game_environment=None, return_details=False):
    """V13 wrapper fixes offensive-lineup orientation and uses Poisson/Skellam win probability."""
    # Builder lineup names are pitcher-facing: home_lineup_details is the AWAY offense
    # facing the home pitcher, and away_lineup_details is the HOME offense facing away pitcher.
    _, _, details = _v12_moneyline_probability(
        home, away, hp, ap, pitcher_this_year, pitcher_last_year,
        team_hitting, team_batting_rhp, team_batting_lhp,
        bullpen_stats=bullpen_stats, use_home_bullpen=use_home_bullpen,
        use_away_bullpen=use_away_bullpen, bullpen_fatigue_df=bullpen_fatigue_df,
        home_lineup_details=away_lineup_details, away_lineup_details=home_lineup_details,
        home_arsenal_details=home_arsenal_details, away_arsenal_details=away_arsenal_details,
        game_environment=game_environment, return_details=True,
    )
    home_runs = float(details.get("home_projected_runs", 4.35) or 4.35)
    away_runs = float(details.get("away_projected_runs", 4.35) or 4.35)
    home_prob, regulation_tie = _skellam_home_win_probability(home_runs, away_runs)
    away_prob = 1.0 - home_prob
    details["home_win_probability"] = round(home_prob, 4)
    details["away_win_probability"] = round(away_prob, 4)
    details["regulation_tie_probability"] = round(regulation_tie, 4)
    details["probability_engine"] = "Poisson team-run distributions / Skellam-style difference; ties allocated 54% home"
    details["lineup_orientation"] = "Corrected: home offense uses lineup facing away pitcher; away offense uses lineup facing home pitcher"
    details["status"] = "Moneyline v13: corrected confirmed-lineup orientation, 68/32 pitching/team scoring blend, real bullpen inputs, park de-duplication, and total-sensitive Poisson win probability."
    if return_details:
        return home_prob, away_prob, details
    return home_prob, away_prob


def moneyline_confidence_score(team_key, edge, moneyline_details, odds=None):
    """V12 confidence plus low-sample-lineup uncertainty protection."""
    result = dict(_v12_moneyline_confidence_score(team_key, edge, moneyline_details, odds=odds))
    team = (moneyline_details or {}).get(str(team_key).lower(), {}) or {}
    lineup = team.get("lineup", {}) or {}
    low_sample = int(lineup.get("low_sample_hitters", lineup.get("lineup_low_sample_hitters", 0)) or 0)
    if low_sample >= 4:
        checks = dict(result.get("checks", {}))
        if checks.get("lineup"):
            checks["lineup"] = False
        confluence = sum(1 for value in checks.values() if value)
        flags = list(result.get("red_flags", []))
        flags.append(f"Confirmed lineup uncertainty: {low_sample} hitters below 25 PA; lineup cannot count as premium confluence")
        confidence = max(0.0, float(result.get("confidence_score", 0) or 0) - 4.0)
        result["checks"] = checks
        result["confluence"] = confluence
        result["red_flags"] = flags
        result["confidence_score"] = round(confidence, 1)
        result["grade"] = moneyline_grade(edge, confidence, confluence, flags)
        result.setdefault("reason_lines", []).append(flags[-1])
    result["status"] = "Moneyline v13 confidence: no-vig edge, starter, real bullpen, confirmed lineup; low samples regress to neutral and cannot create premium confluence."
    return result


@st.cache_data(ttl=60 * 60)
def load_nrfi_pitchers_live():
    return pd.DataFrame([{
        "Status": "V13 first-inning pitcher profiles are fetched lazily from MLB play-by-play for the selected matchup.",
        "Source": "Official MLB play-by-play",
    }])


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def _pitcher_first_inning_profile(player_id, season=MLB_SEASON, max_starts=12):
    """Build first-inning pitcher performance from official MLB play-by-play."""
    neutral = {"starts": 0, "pa": 0, "obp": 0.320, "k_rate": 0.220, "run_half_probability": 0.270, "source": "neutral fallback"}
    try:
        pid = str(player_id or "").replace(".0", "").strip()
        if not pid:
            return neutral
        response = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{pid}/stats",
            params={"stats": "gameLog", "group": "pitching", "season": str(season)}, timeout=25,
        )
        response.raise_for_status()
        splits = (((response.json().get("stats") or [{}])[0]).get("splits") or [])
        games = []
        for split in splits:
            stat = split.get("stat", {}) or {}
            ip = _parse_mlb_innings_pitched(stat.get("inningsPitched", 0), 0)
            gs = _mlb_num(stat.get("gamesStarted", 0), 0)
            game_info = split.get("game") or {}
            game_pk = game_info.get("gamePk") or game_info.get("pk")
            if not game_pk:
                link_match = re.search(r"/game/(\d+)", str(game_info.get("link", "")))
                game_pk = link_match.group(1) if link_match else ""
            if game_pk and (gs >= 1 or ip >= 3.0):
                games.append((str(split.get("date", "")), str(game_pk)))
        games = sorted(games, reverse=True)[:int(max_starts)]
        pa = hits = walks = hbp = strikeouts = 0
        run_halves = starts = 0
        for _, game_pk in games:
            try:
                pbp = requests.get(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/playByPlay", timeout=25).json()
                inning_plays = []
                for play in pbp.get("allPlays", []):
                    about = play.get("about", {}) or {}
                    matchup = play.get("matchup", {}) or {}
                    play_pid = str(((matchup.get("pitcher") or {}).get("id") or ""))
                    if int(about.get("inning", 0) or 0) == 1 and play_pid == pid:
                        inning_plays.append(play)
                if not inning_plays:
                    continue
                starts += 1
                half_runs = 0
                for play in inning_plays:
                    pa += 1
                    event = str((play.get("result", {}) or {}).get("eventType", "")).lower()
                    if event in {"single", "double", "triple", "home_run"}:
                        hits += 1
                    elif event in {"walk", "intent_walk"}:
                        walks += 1
                    elif event == "hit_by_pitch":
                        hbp += 1
                    if "strikeout" in event:
                        strikeouts += 1
                    for runner in play.get("runners", []) or []:
                        if str(((runner.get("movement") or {}).get("end") or "")).lower() == "score":
                            half_runs += 1
                if half_runs > 0:
                    run_halves += 1
            except Exception:
                continue
        if starts <= 0 or pa <= 0:
            return neutral
        raw_obp = (hits + walks + hbp) / pa
        raw_k = strikeouts / pa
        raw_run_prob = run_halves / starts
        weight = min(0.70, starts / 15.0)
        return {
            "starts": starts, "pa": pa,
            "obp": ((1 - weight) * 0.320) + (weight * raw_obp),
            "k_rate": ((1 - weight) * 0.220) + (weight * raw_k),
            "run_half_probability": ((1 - weight) * 0.270) + (weight * raw_run_prob),
            "raw_obp": raw_obp, "raw_k_rate": raw_k, "raw_run_half_probability": raw_run_prob,
            "source": "MLB first-inning play-by-play, regressed to league baseline",
        }
    except Exception as exc:
        out = dict(neutral)
        out["source"] = f"neutral fallback: {exc}"
        return out


def _top_four_lineup_profile(lineup_details, team, split_df):
    fallback_obp = clean_percent(get_value(split_df, "Teams", team, "OBP", 0.320))
    fallback_k = clean_percent(get_value(split_df, "Teams", team, "K%", 0.220))
    fallback_iso = clean_percent(get_value(split_df, "Teams", team, "ISO", 0.160))
    fallback_slg = _cap(fallback_obp + fallback_iso + 0.09, 0.330, 0.520)
    rows = []
    if isinstance(lineup_details, dict):
        hitters = lineup_details.get("hitters", [])
        if isinstance(hitters, pd.DataFrame):
            rows = hitters.to_dict("records")
        elif isinstance(hitters, list):
            rows = hitters
    top = [r for r in rows if 1 <= int(float(r.get("Order", 99) or 99)) <= 4]
    if len(top) < 3:
        return {"obp": fallback_obp, "slg": fallback_slg, "k_rate": fallback_k, "iso": fallback_iso, "source": "team handedness split fallback", "hitters": len(top)}
    weights = {1: 1.15, 2: 1.12, 3: 1.18, 4: 1.10}
    total_w = 0.0
    obp = slg = k = 0.0
    used = 0
    for row in top:
        order = int(float(row.get("Order", 4) or 4))
        reliability = _cap(float(row.get("Reliability", 50) or 50) / 100.0, 0.20, 1.0)
        w = weights.get(order, 1.0) * reliability
        try:
            row_obp = float(row.get("OBP", fallback_obp) or fallback_obp)
            row_slg = float(row.get("SLG", fallback_slg) or fallback_slg)
            row_k = clean_percent(row.get("K%", fallback_k))
        except Exception:
            continue
        # Reliability shrinks unknown hitters toward the team split instead of penalizing them.
        row_obp = (reliability * row_obp) + ((1 - reliability) * fallback_obp)
        row_slg = (reliability * row_slg) + ((1 - reliability) * fallback_slg)
        row_k = (reliability * row_k) + ((1 - reliability) * fallback_k)
        obp += row_obp * w; slg += row_slg * w; k += row_k * w; total_w += w; used += 1
    if total_w <= 0:
        return {"obp": fallback_obp, "slg": fallback_slg, "k_rate": fallback_k, "iso": fallback_iso, "source": "team handedness split fallback", "hitters": 0}
    obp /= total_w; slg /= total_w; k /= total_w
    return {"obp": obp, "slg": slg, "k_rate": k, "iso": max(0.08, slg - 0.250), "source": "confirmed top four, sample-regressed", "hitters": used}


def nrfi_probability(home, away, hp, ap, pitcher_this_year, pitcher_last_year, nrfi_pitchers, nrfi_rhp, nrfi_lhp, home_lineup_details=None, away_lineup_details=None, game_environment=None, return_details=False):
    """First-inning model using top-four hitters and pitcher first-inning play-by-play."""
    h_throw = get_value(pitcher_this_year, "Player", hp, "Throws", get_value(pitcher_last_year, "Player", hp, "Throws", "R"))
    a_throw = get_value(pitcher_this_year, "Player", ap, "Throws", get_value(pitcher_last_year, "Player", ap, "Throws", "R"))
    away_split = nrfi_lhp if str(h_throw).upper().startswith("L") else nrfi_rhp
    home_split = nrfi_lhp if str(a_throw).upper().startswith("L") else nrfi_rhp
    # Pitcher-facing lineup orientation: home_lineup_details is away offense; vice versa.
    away_profile = _top_four_lineup_profile(home_lineup_details, away, away_split)
    home_profile = _top_four_lineup_profile(away_lineup_details, home, home_split)
    hp_first = _pitcher_first_inning_profile(_pitcher_mlbam_id(hp, pitcher_this_year, pitcher_last_year), MLB_SEASON)
    ap_first = _pitcher_first_inning_profile(_pitcher_mlbam_id(ap, pitcher_this_year, pitcher_last_year), MLB_SEASON)
    env_adj = float((game_environment or {}).get("total_run_adjustment", 0.0) or 0.0)

    def half_run_probability(offense, pitcher, home_half=False):
        base_logit = math.log(0.270 / 0.730)
        offense_signal = (
            (float(offense.get("obp", 0.320)) - 0.320) * 8.5 +
            (float(offense.get("slg", 0.410)) - 0.410) * 3.8 -
            (float(offense.get("k_rate", 0.220)) - 0.220) * 3.0
        )
        pitcher_signal = (
            (float(pitcher.get("run_half_probability", 0.270)) - 0.270) * 4.2 +
            (float(pitcher.get("obp", 0.320)) - 0.320) * 5.5 -
            (float(pitcher.get("k_rate", 0.220)) - 0.220) * 2.8
        )
        logit = base_logit + offense_signal + pitcher_signal + (0.04 if home_half else 0.0) + (env_adj * 0.11)
        return _cap(1.0 / (1.0 + math.exp(-logit)), 0.12, 0.46)

    top_run = half_run_probability(away_profile, hp_first, False)
    bottom_run = half_run_probability(home_profile, ap_first, True)
    nrfi = (1 - top_run) * (1 - bottom_run)
    details = {
        "nrfi_probability": nrfi, "yrfi_probability": 1 - nrfi,
        "top_half_run_probability": top_run, "bottom_half_run_probability": bottom_run,
        "away_top_four": away_profile, "home_top_four": home_profile,
        "home_pitcher_first_inning": hp_first, "away_pitcher_first_inning": ap_first,
        "environment_adjustment": env_adj,
        "status": "NRFI v13 uses confirmed top-four hitters, true handedness splits when available, first-inning pitcher play-by-play, and park/weather.",
    }
    return (nrfi, details) if return_details else nrfi


def nrfi_yrfi_grade_from_environment(nrfi_prob, total_run_details=None, nrfi_odds=-110, yrfi_odds=-110, nrfi_details=None):
    """Probability, first-inning shape, run environment, and price must all agree."""
    nrfi_prob = _cap(float(nrfi_prob or 0.5), 0.01, 0.99)
    yrfi_prob = 1.0 - nrfi_prob
    nrfi_score = nrfi_score_formula(nrfi_prob)
    yrfi_score = max(0, min(100, 50 + (0.485 - nrfi_prob) * 430))
    projected_total = _total_float((total_run_details or {}).get("projected_total", 8.5), 8.5)
    total_edge = _total_float((total_run_details or {}).get("edge", 0), 0)
    run_env = max(0, min(100, 50 + ((projected_total - 8.5) * 9.0) + (total_edge * 4.0)))
    nrfi_implied = american_odds_to_implied_prob(nrfi_odds)
    yrfi_implied = american_odds_to_implied_prob(yrfi_odds)
    nrfi_edge = nrfi_prob - nrfi_implied
    yrfi_edge = yrfi_prob - yrfi_implied
    nrfi_ev = _side_expected_value(nrfi_prob, yrfi_prob, nrfi_odds)
    yrfi_ev = _side_expected_value(yrfi_prob, nrfi_prob, yrfi_odds)
    top_half = float((nrfi_details or {}).get("top_half_run_probability", 0.0) or 0.0)
    bottom_half = float((nrfi_details or {}).get("bottom_half_run_probability", 0.0) or 0.0)
    yrfi_half_support = max(top_half, bottom_half) >= 0.30

    grade = "PASS"
    selected_odds = ""
    if nrfi_score >= 88 and run_env <= 54 and projected_total <= 8.8 and nrfi_edge >= 0.025 and nrfi_ev > 0:
        grade = "ELITE NRFI"; selected_odds = nrfi_odds
    elif (
        yrfi_score >= 72
        and run_env >= 55
        and projected_total >= 8.5
        and yrfi_edge >= 0.040
        and yrfi_ev >= 0.030
        and yrfi_half_support
    ):
        grade = "YRFI"; selected_odds = yrfi_odds
    return {
        "grade": grade, "nrfi_score": round(nrfi_score, 1), "yrfi_score": round(yrfi_score, 1),
        "run_environment_score": round(run_env, 1), "projected_total": projected_total, "total_edge": total_edge,
        "nrfi_probability": round(nrfi_prob, 4), "yrfi_probability": round(yrfi_prob, 4),
        "nrfi_implied": round(nrfi_implied, 4), "yrfi_implied": round(yrfi_implied, 4),
        "nrfi_edge": round(nrfi_edge, 4), "yrfi_edge": round(yrfi_edge, 4),
        "nrfi_ev": round(nrfi_ev, 4), "yrfi_ev": round(yrfi_ev, 4), "selected_odds": selected_odds,
        "top_half_run_probability": round(top_half, 4),
        "bottom_half_run_probability": round(bottom_half, 4),
        "yrfi_half_support": bool(yrfi_half_support),
        "status": "Elite NRFI keeps its existing standard. YRFI now requires score 72+, projected total 8.5+, run-environment score 55+, 4% price edge, 0.03u EV, and at least one half-inning run probability of 30%+.",
    }


def _count_total_confluence(side, components):
    thresholds = {
        "OVER": {"offense": 0.35, "starter": 0.25, "bullpen": 0.20, "lineup": 0.08, "park_weather": 0.20, "raw_edge": 1.00},
        "UNDER": {"offense": -0.35, "starter": -0.25, "bullpen": -0.20, "lineup": -0.08, "park_weather": -0.20, "raw_edge": -1.00},
    }
    if side not in thresholds:
        return 0
    if side == "OVER":
        return sum(1 for key, threshold in thresholds[side].items() if float(components.get(key, 0) or 0) >= threshold)
    return sum(1 for key, threshold in thresholds[side].items() if float(components.get(key, 0) or 0) <= threshold)


def total_runs_projection(home, away, hp, ap, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, bullpen_stats=None, use_home_bullpen=False, use_away_bullpen=False, bullpen_fatigue_df=None, market_total=None, total_odds=-110, home_lineup_details=None, away_lineup_details=None, home_arsenal_details=None, away_arsenal_details=None, game_environment=None):
    """Shared run projection with repaired confluence and odds-aware total EV."""
    try:
        _, _, ml = moneyline_probability(
            home, away, hp, ap, pitcher_this_year, pitcher_last_year,
            team_hitting, team_batting_rhp, team_batting_lhp,
            bullpen_stats=bullpen_stats, use_home_bullpen=use_home_bullpen,
            use_away_bullpen=use_away_bullpen, bullpen_fatigue_df=bullpen_fatigue_df,
            home_lineup_details=home_lineup_details, away_lineup_details=away_lineup_details,
            home_arsenal_details=home_arsenal_details, away_arsenal_details=away_arsenal_details,
            game_environment=game_environment, return_details=True,
        )
    except Exception as exc:
        market = _total_float(market_total, 0.0)
        fallback = market if market > 0 else 8.7
        return {"projected_total": fallback, "market_total": market or "", "edge": 0.0, "side": "PASS", "grade": "PASS", "status": f"Total fallback: {exc}"}
    home_data = ml.get("home", {}) or {}; away_data = ml.get("away", {}) or {}
    home_rp = home_data.get("run_projection", {}) or {}; away_rp = away_data.get("run_projection", {}) or {}
    home_runs = float(ml.get("home_projected_runs", 0) or 0); away_runs = float(ml.get("away_projected_runs", 0) or 0)
    projected = home_runs + away_runs
    market = _total_float(market_total, 0.0)
    edge = projected - market if market > 0 else 0.0
    side = "OVER" if edge > 0 else "UNDER" if edge < 0 else "PASS"
    park_weather = float(ml.get("park_weather_total_adjustment", 0) or 0)
    offense_pressure = (
        float(home_rp.get("team_baseline_adjustment", 0) or 0) + float(away_rp.get("team_baseline_adjustment", 0) or 0) +
        float(home_rp.get("offense_adjustment", 0) or 0) + float(away_rp.get("offense_adjustment", 0) or 0)
    )
    lineup_pressure = float(home_rp.get("lineup_adjustment", 0) or 0) + float(away_rp.get("lineup_adjustment", 0) or 0)
    starter_pressure = float(home_rp.get("opposing_starter_adjustment", 0) or 0) + float(away_rp.get("opposing_starter_adjustment", 0) or 0)
    bullpen_pressure = float(home_rp.get("opposing_bullpen_adjustment", 0) or 0) + float(away_rp.get("opposing_bullpen_adjustment", 0) or 0)
    components = {"offense": offense_pressure, "starter": starter_pressure, "bullpen": bullpen_pressure, "lineup": lineup_pressure, "park_weather": park_weather, "raw_edge": edge if market > 0 else projected - 8.7}
    confluence = _count_total_confluence(side, components)
    over_p = under_p = push_p = 0.0
    over_ev = under_ev = 0.0
    if market > 0:
        over_p, under_p, push_p = _poisson_total_market_probabilities(projected, market)
        over_ev = _side_expected_value(over_p, under_p, total_odds)
        under_ev = _side_expected_value(under_p, over_p, total_odds)
    selected_ev = over_ev if side == "OVER" else under_ev if side == "UNDER" else 0.0
    selected_prob = over_p if side == "OVER" else under_p if side == "UNDER" else 0.0
    implied = american_odds_to_implied_prob(total_odds)
    push_adjusted_break_even = implied * max(0.0, 1.0 - push_p)
    price_edge = selected_prob - push_adjusted_break_even
    grade = "PASS"
    if side in ["OVER", "UNDER"] and abs(edge) >= TOTAL_RUN_EDGE_THRESHOLD and confluence >= TOTAL_RUN_CONFLUENCE_THRESHOLD and selected_ev > 0 and price_edge >= 0.02:
        grade = f"TOTAL {side}"
    return {
        "projected_total": round(projected, 2), "raw_projected_total": round(projected, 2),
        "market_total": market if market > 0 else "", "edge": round(edge, 2), "side": side, "grade": grade,
        "confluence": confluence, "required_edge": TOTAL_RUN_EDGE_THRESHOLD, "required_confluence": TOTAL_RUN_CONFLUENCE_THRESHOLD,
        "away_projected_runs": round(away_runs, 2), "home_projected_runs": round(home_runs, 2),
        "over_probability": round(over_p, 4), "under_probability": round(under_p, 4), "push_probability": round(push_p, 4),
        "total_odds": total_odds, "market_implied_probability": round(implied, 4),
        "push_adjusted_break_even_probability": round(push_adjusted_break_even, 4),
        "selected_probability": round(selected_prob, 4), "price_edge": round(price_edge, 4),
        "over_ev": round(over_ev, 4), "under_ev": round(under_ev, 4), "selected_ev": round(selected_ev, 4),
        "away_offense_score": (away_data.get("offense", {}) or {}).get("score", ""), "home_offense_score": (home_data.get("offense", {}) or {}).get("score", ""),
        "away_starter_score": (away_data.get("starter", {}) or {}).get("matchup_score", ""), "home_starter_score": (home_data.get("starter", {}) or {}).get("matchup_score", ""),
        "away_bullpen_score": (away_data.get("bullpen", {}) or {}).get("score", ""), "home_bullpen_score": (home_data.get("bullpen", {}) or {}).get("score", ""),
        "away_run_projection": away_rp, "home_run_projection": home_rp,
        "game_environment": ml.get("game_environment", {}) or {}, "park_weather_adjustment": round(park_weather, 2),
        "moneyline_engine": ml, "confluence_components": {k: round(float(v), 3) for k, v in components.items()},
        "engine": "v13_shared_poisson_run_engine",
        "status": "Totals v13 uses the repaired starter/bullpen/lineup confluence buckets and requires positive EV at the entered price; integer totals include push probability.",
    }




# ============================================================================
# V14 FINAL MODEL OVERRIDES
# ============================================================================

_v13_strikeout_bet_grade = strikeout_bet_grade
_v13_pitcher_k_strength_score = pitcher_k_strength_score
_v13_moneyline_probability = moneyline_probability
_v13_total_runs_projection = total_runs_projection


def strikeout_bet_grade(exp_k, six_k, ipg_this, ipg_last, line, volatility, odds=-110, reliability=None, expected_std=None):
    """Probability- and reliability-based K grading with asymmetric thresholds."""
    if reliability is None or expected_std is None:
        return _v13_strikeout_bet_grade(exp_k, six_k, ipg_this, ipg_last, line, volatility)
    try:
        exp_k = float(exp_k); six_k = float(six_k); line = float(line); reliability = float(reliability); expected_std = float(expected_std)
    except Exception:
        return "PASS", 0.0
    probs = k_market_probabilities(exp_k, line, expected_std)
    implied = american_odds_to_implied_prob(odds)
    over_edge = exp_k - _k_prop_win_number(line)
    under_cushion = _k_prop_win_number(line) - exp_k
    six_confirms_over = six_k >= line + 0.25
    six_confirms_under = six_k <= line - 0.25
    if exp_k >= line:
        selected_prob = probs["over"]; price_edge = selected_prob - implied
        if selected_prob >= 0.70 and over_edge >= 1.30 and reliability >= 72 and price_edge >= 0.04 and six_confirms_over:
            return "STRONG OVER", over_edge
        if selected_prob >= 0.64 and over_edge >= 0.75 and reliability >= 65 and price_edge >= 0.025 and six_confirms_over:
            return "OVER", over_edge
        if selected_prob >= 0.59 and over_edge >= 0.35 and reliability >= 58 and price_edge >= 0.015 and six_confirms_over:
            return "LEAN OVER", over_edge
    else:
        selected_prob = probs["under"]; price_edge = selected_prob - implied
        low_projection_guard = exp_k < 3.5
        if selected_prob >= (0.74 if low_projection_guard else 0.71) and under_cushion >= 1.75 and reliability >= (74 if low_projection_guard else 70) and price_edge >= 0.04 and six_confirms_under:
            return "STRONG UNDER", -under_cushion
        if selected_prob >= (0.69 if low_projection_guard else 0.65) and under_cushion >= 1.35 and reliability >= (70 if low_projection_guard else 64) and price_edge >= 0.025 and six_confirms_under:
            return "UNDER", -under_cushion
        if selected_prob >= (0.66 if low_projection_guard else 0.60) and under_cushion >= 0.90 and reliability >= (66 if low_projection_guard else 59) and price_edge >= 0.015 and six_confirms_under:
            return "LEAN UNDER", -under_cushion
    return "PASS", 0.0


def pitcher_k_strength_score(exp_k, six_k, line, volatility, ipg_this, ipg_last, reliability=None, selected_probability=None, price_edge=None):
    base = float(_v13_pitcher_k_strength_score(exp_k, six_k, line, volatility, ipg_this, ipg_last) or 0)
    if reliability is None or selected_probability is None:
        return round(base, 1)
    rel = _cap(float(reliability), 0, 100)
    prob = _cap(float(selected_probability), 0, 1)
    p_edge = float(price_edge or 0)
    score = (0.45 * base) + (0.30 * rel) + (0.25 * _cap((prob - 0.50) * 200, 0, 100))
    if p_edge < 0.015: score -= 8
    return round(_cap(score, 0, 100), 1)


def _negative_binomial_probabilities(mean_runs, variance_factor=1.15, max_runs=25):
    mean_runs = max(0.05, float(mean_runs or 0.05)); vf = max(1.001, float(variance_factor or 1.15))
    variance = mean_runs * vf
    r = max(0.25, mean_runs * mean_runs / max(1e-6, variance - mean_runs))
    p = r / (r + mean_runs)
    probs = []
    for k in range(max_runs + 1):
        log_pmf = math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(p) + k * math.log(1 - p)
        probs.append(math.exp(log_pmf))
    probs[-1] += max(0.0, 1.0 - sum(probs))
    return probs


def _run_distribution(home_runs, away_runs, market_total=None, variance_factor=1.15):
    hp = _negative_binomial_probabilities(home_runs, variance_factor)
    ap = _negative_binomial_probabilities(away_runs, variance_factor)
    home_win = tie = over = under = push = 0.0
    line = _safe_float_or_none(market_total)
    for h, ph in enumerate(hp):
        for a, pa in enumerate(ap):
            joint = ph * pa
            if h > a: home_win += joint
            elif h == a: tie += joint
            if line is not None:
                total = h + a
                if abs(line - round(line)) < 1e-9 and total == int(round(line)): push += joint
                elif total > line: over += joint
                else: under += joint
    home_final = _cap(home_win + tie * 0.54, 0.24, 0.76)
    return {"home_win": home_final, "away_win": 1-home_final, "tie": tie, "over": over, "under": under, "push": push, "variance_factor": variance_factor}


def moneyline_probability(home, away, hp, ap, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, bullpen_stats=None, use_home_bullpen=False, use_away_bullpen=False, bullpen_fatigue_df=None, home_lineup_details=None, away_lineup_details=None, home_arsenal_details=None, away_arsenal_details=None, game_environment=None, return_details=False, home_bulk_context=None, away_bulk_context=None):
    """V14 run distribution plus explicit opener/bulk allocation."""
    _, _, details = _v12_moneyline_probability(
        home, away, hp, ap, pitcher_this_year, pitcher_last_year,
        team_hitting, team_batting_rhp, team_batting_lhp,
        bullpen_stats=bullpen_stats, use_home_bullpen=use_home_bullpen, use_away_bullpen=use_away_bullpen,
        bullpen_fatigue_df=bullpen_fatigue_df,
        home_lineup_details=away_lineup_details, away_lineup_details=home_lineup_details,
        home_arsenal_details=home_arsenal_details, away_arsenal_details=away_arsenal_details,
        game_environment=game_environment, return_details=True,
        home_bulk_context=home_bulk_context, away_bulk_context=away_bulk_context,
    )
    home_runs = float(details.get("home_projected_runs", 4.35) or 4.35); away_runs = float(details.get("away_projected_runs", 4.35) or 4.35)
    dist = _run_distribution(home_runs, away_runs, variance_factor=1.15)
    home_prob = dist["home_win"]; away_prob = dist["away_win"]
    details.update({
        "home_win_probability": round(home_prob, 4), "away_win_probability": round(away_prob, 4),
        "regulation_tie_probability": round(dist["tie"], 4), "probability_engine": "Overdispersed team-run distributions (negative binomial); ties allocated 54% home",
        "home_bulk_context": home_bulk_context or {}, "away_bulk_context": away_bulk_context or {},
        "model_version": MODEL_VERSION,
        "status": "Moneyline v14: confirmed-lineup run model, overdispersed run distribution, explicit opener+bulk+remaining-bullpen allocation, and extreme-edge confidence protection.",
    })
    return (home_prob, away_prob, details) if return_details else (home_prob, away_prob)


def total_runs_projection(home, away, hp, ap, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, bullpen_stats=None, use_home_bullpen=False, use_away_bullpen=False, bullpen_fatigue_df=None, market_total=None, total_odds=-110, home_lineup_details=None, away_lineup_details=None, home_arsenal_details=None, away_arsenal_details=None, game_environment=None, home_bulk_context=None, away_bulk_context=None):
    try:
        _, _, ml = moneyline_probability(
            home, away, hp, ap, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp,
            bullpen_stats=bullpen_stats, use_home_bullpen=use_home_bullpen, use_away_bullpen=use_away_bullpen,
            bullpen_fatigue_df=bullpen_fatigue_df, home_lineup_details=home_lineup_details, away_lineup_details=away_lineup_details,
            home_arsenal_details=home_arsenal_details, away_arsenal_details=away_arsenal_details, game_environment=game_environment,
            return_details=True, home_bulk_context=home_bulk_context, away_bulk_context=away_bulk_context,
        )
    except Exception as exc:
        market = _total_float(market_total, 0.0); fallback = market if market > 0 else 8.7
        return {"projected_total": fallback, "market_total": market or "", "edge": 0.0, "side": "PASS", "grade": "PASS", "status": f"Total fallback: {exc}"}
    home_data = ml.get("home", {}) or {}; away_data = ml.get("away", {}) or {}
    home_rp = home_data.get("run_projection", {}) or {}; away_rp = away_data.get("run_projection", {}) or {}
    home_runs = float(ml.get("home_projected_runs", 0) or 0); away_runs = float(ml.get("away_projected_runs", 0) or 0); projected = home_runs + away_runs
    market = _total_float(market_total, 0.0); edge = projected - market if market > 0 else 0.0
    side = "OVER" if edge > 0 else "UNDER" if edge < 0 else "PASS"
    park_weather = float(ml.get("park_weather_total_adjustment", 0) or 0)
    offense_pressure = float(home_rp.get("team_baseline_adjustment", 0) or 0)+float(away_rp.get("team_baseline_adjustment", 0) or 0)+float(home_rp.get("offense_adjustment", 0) or 0)+float(away_rp.get("offense_adjustment", 0) or 0)
    lineup_pressure = float(home_rp.get("lineup_adjustment", 0) or 0)+float(away_rp.get("lineup_adjustment", 0) or 0)
    starter_pressure = float(home_rp.get("opposing_starter_adjustment", 0) or 0)+float(away_rp.get("opposing_starter_adjustment", 0) or 0)
    bullpen_pressure = float(home_rp.get("opposing_bullpen_adjustment", 0) or 0)+float(away_rp.get("opposing_bullpen_adjustment", 0) or 0)
    components = {"offense": offense_pressure, "starter": starter_pressure, "bullpen": bullpen_pressure, "lineup": lineup_pressure, "park_weather": park_weather, "raw_edge": edge if market > 0 else projected-8.7}
    confluence = _count_total_confluence(side, components)
    dist = _run_distribution(home_runs, away_runs, market_total=market if market > 0 else None, variance_factor=1.15)
    over_p, under_p, push_p = dist["over"], dist["under"], dist["push"]
    over_ev = _side_expected_value(over_p, under_p, total_odds); under_ev = _side_expected_value(under_p, over_p, total_odds)
    selected_prob = over_p if side == "OVER" else under_p if side == "UNDER" else 0.0
    selected_ev = over_ev if side == "OVER" else under_ev if side == "UNDER" else 0.0
    implied = american_odds_to_implied_prob(total_odds); break_even = implied * max(0,1-push_p); price_edge = selected_prob-break_even
    required_edge = 1.50 if side == "OVER" else 1.25
    required_confluence = 4 if abs(edge) >= 2.0 else 3
    reliability = 84.0 - (7 if abs(edge) >= 2.0 else 0) - (5 if use_home_bullpen and not (home_bulk_context or {}).get("bulk_pitcher") else 0) - (5 if use_away_bullpen and not (away_bulk_context or {}).get("bulk_pitcher") else 0)
    grade = "PASS"
    if side in ["OVER","UNDER"] and abs(edge) >= required_edge and confluence >= required_confluence and selected_ev > 0 and price_edge >= (0.035 if abs(edge)>=2 else 0.02) and reliability >= 70:
        grade = f"TOTAL {side}"
    return {
        "projected_total": round(projected,2), "raw_projected_total": round(projected,2), "market_total": market if market>0 else "", "edge": round(edge,2), "side": side, "grade": grade,
        "confluence": confluence, "required_edge": required_edge, "required_confluence": required_confluence, "reliability": round(reliability,1),
        "away_projected_runs": round(away_runs,2), "home_projected_runs": round(home_runs,2), "over_probability": round(over_p,4), "under_probability": round(under_p,4), "push_probability": round(push_p,4),
        "total_odds": total_odds, "market_implied_probability": round(implied,4), "push_adjusted_break_even_probability": round(break_even,4), "selected_probability": round(selected_prob,4), "price_edge": round(price_edge,4),
        "over_ev": round(over_ev,4), "under_ev": round(under_ev,4), "selected_ev": round(selected_ev,4),
        "away_offense_score": (away_data.get("offense",{}) or {}).get("score",""), "home_offense_score": (home_data.get("offense",{}) or {}).get("score",""),
        "away_starter_score": (away_data.get("starter",{}) or {}).get("matchup_score",""), "home_starter_score": (home_data.get("starter",{}) or {}).get("matchup_score",""),
        "away_bullpen_score": (away_data.get("bullpen",{}) or {}).get("score",""), "home_bullpen_score": (home_data.get("bullpen",{}) or {}).get("score",""),
        "away_run_projection": away_rp, "home_run_projection": home_rp, "game_environment": ml.get("game_environment",{}) or {}, "park_weather_adjustment": round(park_weather,2),
        "moneyline_engine": ml, "confluence_components": {k:round(float(v),3) for k,v in components.items()}, "engine":"v14_overdispersed_shared_run_engine", "model_version":MODEL_VERSION,
        "status":"Totals v14 uses separate Over/Under edge thresholds, overdispersed run probabilities, positive EV, extreme-edge confirmation, and opener/bulk allocation.",
    }


# ============================================================================
# V15 STRIKEOUT MODEL OVERHAUL
# ============================================================================
# Architecture:
#   Expected Ks = Projected Batters Faced x Matchup-Adjusted K Rate
#
# The workload/opportunity engine and strikeout-rate engine are intentionally
# separated. Arsenal now adjusts K rate with asymmetric caps instead of adding
# large direct strikeout bonuses. Negative arsenal signals are deliberately
# damped until the new saved feature history validates them.
K_MODEL_ARCHITECTURE = "v15_opportunity_x_k_rate"
K_MODEL_OVERHAUL_DATE = "2026-07-13"

_K_OVERHAUL_TRACKING_COLUMNS = [
    "Projection Architecture", "Projected Pitches Per BF", "Opponent Pitches Per PA",
    "Pitcher Pitches Per BF", "Third Time Through Probability", "Base K Rate",
    "Team Split K Rate", "Lineup K Rate Snapshot", "Matchup K Rate",
    "Arsenal K Rate Multiplier", "Lineup K Rate Multiplier", "Skill K Rate Multiplier",
    "Opponent Discipline Multiplier", "Recent Pitch Trend Multiplier",
    "Pitcher CSW %", "Pitcher Called Strike %", "Pitcher Chase %",
    "Pitcher Zone Contact %", "Pitcher Chase Contact %", "Pitcher First Strike %",
    "Opponent Whiff %", "Opponent Zone Contact %", "Opponent Chase Contact %",
    "Recent Velocity Delta", "Recent CSW Delta", "Recent Whiff Delta",
    "Recent Usage Quality Shift", "Recent Shape Change", "Recent Release Change",
    "Under Support Count", "Under Support Notes", "Projection Structural Std",
    "Actual Pitches Per BF", "Pitch Count Error", "Pitches Per BF Error",
]
RECENT_FORM_COLUMNS = list(dict.fromkeys(list(RECENT_FORM_COLUMNS) + _K_OVERHAUL_TRACKING_COLUMNS))


def _k15_float(value, default=0.0):
    try:
        if value in [None, "", "nan", "None", "<NA>"] or pd.isna(value):
            return default
    except Exception:
        pass
    try:
        return float(str(value).replace("%", "").replace("−", "-").strip())
    except Exception:
        return default


def _k15_rate(value, default=0.0):
    val = _k15_float(value, default)
    if abs(val) > 1.0:
        val /= 100.0
    return val


def _k15_weighted(values):
    """Weighted average of (value, weight), excluding missing/invalid values."""
    valid = []
    for value, weight in values:
        try:
            value = float(value)
            weight = max(0.0, float(weight))
        except Exception:
            continue
        if weight > 0 and math.isfinite(value):
            valid.append((value, weight))
    if not valid:
        return None
    total = sum(weight for _, weight in valid)
    return sum(value * weight for value, weight in valid) / max(total, 1e-9)


# ---------------------------------------------------------------------------
# Extended Baseball Savant plate-discipline fields
# ---------------------------------------------------------------------------
_v14_savant_pitcher_skill_stats = _savant_pitcher_skill_stats


def _k15_canonical_team_label(value):
    """Return the model's canonical MLB team label for names, abbreviations, or aliases."""
    raw = str(value or "").strip()
    if not raw or raw.lower() in ["nan", "none", "<na>"]:
        return ""
    raw_key = normalize_name_for_match(raw)
    for canonical, aliases in TEAM_NAME_ALIASES_FOR_SAVANT.items():
        alias_keys = {
            normalize_name_for_match(x)
            for x in ([canonical] + list(aliases))
            if str(x).strip()
        }
        if raw_key in alias_keys:
            return canonical
    return raw


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def _k15_mlb_team_directory(year=MLB_SEASON):
    """Official MLB team ID/name directory used to resolve Savant team identifiers."""
    rows = []
    try:
        response = requests.get(
            "https://statsapi.mlb.com/api/v1/teams",
            params={"sportId": 1, "season": int(year)},
            timeout=30,
        )
        response.raise_for_status()
        for team in response.json().get("teams", []):
            team_id = str(team.get("id", "") or "").strip()
            aliases = [
                team.get("name", ""), team.get("teamName", ""), team.get("clubName", ""),
                team.get("shortName", ""), team.get("abbreviation", ""),
                team.get("locationName", ""), team.get("franchiseName", ""),
            ]
            canonical = _k15_canonical_team_label(team.get("name", ""))
            if not canonical:
                canonical = _normalize_mlb_team_name(team.get("name", ""))
            rows.append({
                "team_id": team_id,
                "canonical": canonical,
                "aliases": [str(x).strip() for x in aliases if str(x or "").strip()],
            })
    except Exception:
        rows = []
    return rows


def _k15_team_from_id_or_value(value="", team_id="", directory=None):
    """Resolve a direct Savant team value or numeric team ID to a canonical label."""
    directory = directory if directory is not None else _k15_mlb_team_directory(MLB_SEASON)
    id_text = str(team_id or "").replace(".0", "").strip()
    raw = str(value or "").strip()
    if not id_text and raw.replace(".0", "").isdigit():
        id_text = raw.replace(".0", "")
    if id_text:
        for item in directory or []:
            if str(item.get("team_id", "")) == id_text:
                return str(item.get("canonical", "") or "")
    direct = _k15_canonical_team_label(raw)
    if direct and direct != raw:
        return direct
    raw_key = normalize_name_for_match(raw)
    if raw_key:
        for item in directory or []:
            alias_keys = {
                normalize_name_for_match(x)
                for x in [item.get("canonical", "")] + list(item.get("aliases", []))
                if str(x).strip()
            }
            if raw_key in alias_keys:
                return str(item.get("canonical", "") or raw)
    return direct


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def _k15_mlb_player_team_lookup(year=MLB_SEASON):
    """Build MLBAM-ID/name-to-current-team mappings for Savant rows without a team column.

    Savant's custom player CSV does not consistently expose a team field. This
    combines the official active-roster directory with season hitting splits,
    preferring the active roster and using the largest season PA split only as a
    fallback for injured, optioned, or recently moved players.
    """
    directory = _k15_mlb_team_directory(year)
    id_lookup = {}
    name_lookup = {}

    # First preference: current official rosters, fetched in one hydrated request.
    try:
        response = requests.get(
            "https://statsapi.mlb.com/api/v1/teams",
            params={"sportId": 1, "season": int(year), "hydrate": "roster"},
            timeout=40,
        )
        response.raise_for_status()
        for team in response.json().get("teams", []):
            canonical = _k15_team_from_id_or_value(team.get("name", ""), team.get("id", ""), directory)
            roster_container = team.get("roster", {}) or {}
            roster = roster_container.get("roster", []) if isinstance(roster_container, dict) else roster_container
            for entry in roster or []:
                person = entry.get("person", {}) or {}
                player_id = str(person.get("id", "") or "").strip()
                full_name = str(person.get("fullName", "") or "").strip()
                if player_id and canonical:
                    id_lookup[player_id] = canonical
                if full_name and canonical:
                    name_lookup[normalize_name_for_match(full_name)] = canonical
                    name_lookup[normalize_name_for_match(to_last_first(full_name))] = canonical
    except Exception:
        pass

    # Second preference: season hitting splits. Keep the largest-PA team split.
    weighted_candidates = {}
    try:
        response = requests.get(
            "https://statsapi.mlb.com/api/v1/stats",
            params={
                "stats": "season", "group": "hitting", "playerPool": "ALL",
                "season": int(year), "sportIds": 1, "limit": 5000,
            },
            timeout=45,
        )
        response.raise_for_status()
        for block in response.json().get("stats", []):
            for split in block.get("splits", []):
                player = split.get("player", {}) or {}
                team = split.get("team", {}) or {}
                stat = split.get("stat", {}) or {}
                player_id = str(player.get("id", "") or "").strip()
                full_name = str(player.get("fullName", "") or "").strip()
                canonical = _k15_team_from_id_or_value(team.get("name", ""), team.get("id", ""), directory)
                pa = _k15_float(stat.get("plateAppearances", 0), 0.0)
                if pa <= 0:
                    pa = sum(_k15_float(stat.get(k, 0), 0.0) for k in ["atBats", "baseOnBalls", "hitByPitch", "sacFlies"])
                candidate_key = player_id or normalize_name_for_match(full_name)
                if candidate_key and canonical:
                    current = weighted_candidates.get(candidate_key)
                    if current is None or pa > current[0]:
                        weighted_candidates[candidate_key] = (pa, canonical, full_name, player_id)
    except Exception:
        pass

    for _, canonical, full_name, player_id in weighted_candidates.values():
        if player_id and player_id not in id_lookup:
            id_lookup[player_id] = canonical
        if full_name:
            for key in [normalize_name_for_match(full_name), normalize_name_for_match(to_last_first(full_name))]:
                if key and key not in name_lookup:
                    name_lookup[key] = canonical

    return {"by_id": id_lookup, "by_name": name_lookup, "source": "Official MLB rosters + season hitting splits"}


@st.cache_data(ttl=60 * 60, show_spinner=False)
def _k15_official_team_discipline_rows(year=MLB_SEASON):
    """Partial official MLB fallback for K%, BB%, and pitches per PA by team."""
    rows = []
    try:
        response = requests.get(
            "https://statsapi.mlb.com/api/v1/teams/stats",
            params={"sportIds": 1, "group": "hitting", "stats": "season", "season": int(year)},
            timeout=35,
        )
        response.raise_for_status()
        for block in response.json().get("stats", []):
            for split in block.get("splits", []):
                team = split.get("team", {}) or {}
                stat = split.get("stat", {}) or {}
                pa = _k15_float(stat.get("plateAppearances", 0), 0.0)
                if pa <= 0:
                    pa = sum(_k15_float(stat.get(k, 0), 0.0) for k in ["atBats", "baseOnBalls", "hitByPitch", "sacFlies"])
                strikeouts = _k15_float(stat.get("strikeOuts", 0), 0.0)
                walks = _k15_float(stat.get("baseOnBalls", 0), 0.0)
                pitches = _k15_float(stat.get("numberOfPitches", stat.get("pitches", 0)), 0.0)
                rows.append({
                    "Team": _k15_canonical_team_label(team.get("name", "")),
                    "PA": pa,
                    "K %": strikeouts / pa if pa > 0 else 0.0,
                    "BB %": walks / pa if pa > 0 else 0.0,
                    "Pitches/PA": pitches / pa if pa > 0 and pitches > 0 else 0.0,
                })
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame(rows)


@st.cache_data(ttl=60 * 60, show_spinner=False)
def _savant_extended_custom_rows(year, player_type="pitcher"):
    """Best-effort Savant custom leaderboard pull for plate-discipline inputs.

    Baseball Savant occasionally changes CSV headers. The loader therefore uses
    broad aliases and returns a neutral empty frame rather than breaking Build.
    """
    selections = [
        "pa", "k_percent", "bb_percent", "whiff_percent", "swing_percent",
        # Savant has used both descriptive and legacy abbreviated names for
        # zone/chase fields. Request both so a header change does not zero out
        # the opponent profile.
        "zone_percent", "zone_swing_percent", "z_swing_percent",
        "zone_contact_percent", "iz_contact_percent",
        "chase_percent", "oz_swing_percent",
        "chase_contact_percent", "oz_contact_percent", "edge_percent",
        "f_swing_percent", "f_strike_percent", "called_strike_percent",
        "csw_percent", "pitches", "b_total_pitches", "b_called_strike",
        "b_total_swinging_strike",
    ]
    url = (
        "https://baseballsavant.mlb.com/leaderboard/custom"
        f"?year={int(year)}&type={player_type}&filter=&min=1"
        "&selections=" + urllib.parse.quote(",".join(selections), safe="") +
        "&chart=false&x=pa&y=pa&r=no&chartType=beeswarm&sort=pa&sortDir=desc&csv=true"
    )
    try:
        from io import StringIO
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"},
            timeout=35,
        )
        response.raise_for_status()
        raw_text = response.text.strip()
        if not raw_text or "<html" in raw_text.lower() or "," not in raw_text:
            return pd.DataFrame()
        raw = pd.read_csv(StringIO(raw_text))
    except Exception:
        return pd.DataFrame()

    if raw is None or raw.empty:
        return pd.DataFrame()
    raw.columns = [_clean_col_name(c) for c in raw.columns]

    def col(*aliases):
        return _find_col(raw, list(aliases))

    name_col = col("last_name, first_name", "player_name", "Player", "Name")
    id_col = col("player_id", "pitcher", "batter", "MLBAM ID", "id")
    team_col = col(
        "team_name", "team", "Team", "Tm", "team_abbrev", "team_abbreviation",
        "club", "club_name", "current_team", "player_team", "team_name_alt"
    )
    team_id_col = col("team_id", "teamid", "club_id", "current_team_id")
    pa_col = col("pa", "plate_appearances", "PA")
    pitches_col = col("pitches", "pitch_count", "Pitches", "b_total_pitches", "total_pitches")
    called_strike_count_col = col("b_called_strike", "called_strikes", "called_strike_count")
    swinging_strike_count_col = col("b_total_swinging_strike", "swinging_strikes", "swinging_strike_count")
    team_directory = _k15_mlb_team_directory(year)
    player_team_lookup = _k15_mlb_player_team_lookup(year) if str(player_type).lower().startswith("batter") else {"by_id": {}, "by_name": {}}

    field_aliases = {
        "K %": ["k_percent", "K %", "K%"],
        "BB %": ["bb_percent", "BB %", "BB%"],
        "Whiff %": ["whiff_percent", "Whiff %", "Whiff%"],
        "Swing %": ["swing_percent", "Swing %", "Swing%"],
        "Zone %": ["zone_percent", "in_zone_percent", "Zone %", "In Zone %"],
        "Zone Swing %": ["zone_swing_percent", "iz_swing_percent", "Zone Swing %"],
        "Zone Contact %": ["zone_contact_percent", "iz_contact_percent", "In Zone Contact %", "Zone Contact %"],
        "Chase %": ["chase_percent", "oz_swing_percent", "o_swing_percent", "Chase %"],
        "Chase Contact %": ["chase_contact_percent", "oz_contact_percent", "Out of Zone Contact %", "Chase Contact %"],
        "Edge %": ["edge_percent", "Edge %"],
        "First Pitch Swing %": ["f_swing_percent", "first_pitch_swing_percent", "1st Pitch Swing %"],
        "First Strike %": ["f_strike_percent", "first_strike_percent", "First Strike %"],
        "Called Strike %": ["called_strike_percent", "called_strikes_percent", "Called Strike %"],
        "CSW %": ["csw_percent", "called_strike_whiff_percent", "CSW %"],
    }

    rows = []
    for _, row in raw.iterrows():
        name = str(row.get(name_col, "")).strip() if name_col else ""
        if not name or name.lower() in ["nan", "none"]:
            continue
        player_id = str(row.get(id_col, "")).replace(".0", "").strip() if id_col else ""
        direct_team = str(row.get(team_col, "")).strip() if team_col else ""
        direct_team_id = str(row.get(team_id_col, "")).replace(".0", "").strip() if team_id_col else ""
        resolved_team = _k15_team_from_id_or_value(direct_team, direct_team_id, team_directory)
        team_source = "Savant CSV team field" if resolved_team else ""
        if not resolved_team and str(player_type).lower().startswith("batter"):
            resolved_team = (player_team_lookup.get("by_id", {}) or {}).get(player_id, "")
            if resolved_team:
                team_source = "Official MLB player-ID team mapping"
        if not resolved_team and str(player_type).lower().startswith("batter"):
            lookup_names = [normalize_name_for_match(name), normalize_name_for_match(to_last_first(name))]
            for lookup_name in lookup_names:
                resolved_team = (player_team_lookup.get("by_name", {}) or {}).get(lookup_name, "")
                if resolved_team:
                    team_source = "Official MLB player-name team mapping"
                    break
        record = {
            "Player": to_last_first(name),
            "MLBAM ID": player_id,
            "Team": resolved_team,
            "Team Source": team_source,
            "PA": _k15_float(row.get(pa_col, 0), 0.0) if pa_col else 0.0,
            "Pitches": _k15_float(row.get(pitches_col, 0), 0.0) if pitches_col else 0.0,
        }
        for output, aliases in field_aliases.items():
            source_col = _find_col(raw, aliases)
            record[output] = _k15_rate(row.get(source_col, 0), 0.0) if source_col else 0.0
        record["Pitches/PA"] = record["Pitches"] / record["PA"] if record["PA"] > 0 else 0.0
        # Count-field fallbacks protect against Savant percentage-header changes.
        called_count = _k15_float(row.get(called_strike_count_col, 0), 0.0) if called_strike_count_col else 0.0
        swinging_strike_count = _k15_float(row.get(swinging_strike_count_col, 0), 0.0) if swinging_strike_count_col else 0.0
        if record["Called Strike %"] <= 0 and record["Pitches"] > 0 and called_count > 0:
            record["Called Strike %"] = called_count / record["Pitches"]
        if record["Whiff %"] <= 0 and record["Pitches"] > 0 and swinging_strike_count > 0 and record["Swing %"] > 0:
            estimated_swings = record["Pitches"] * record["Swing %"]
            record["Whiff %"] = swinging_strike_count / max(1.0, estimated_swings)
        # Savant Whiff% is whiffs/swings. Convert it to a per-pitch component
        # before combining with called-strike rate when a direct CSW field is absent.
        if record["CSW %"] <= 0:
            whiff_per_pitch = record["Whiff %"] * record["Swing %"]
            called = record["Called Strike %"]
            # Do not treat whiff-per-pitch alone as CSW when the called-strike
            # component is unavailable; the season profile will use a neutral
            # CSW baseline instead of creating a false negative signal.
            if called > 0:
                record["CSW %"] = max(0.0, min(0.50, called + whiff_per_pitch))
        rows.append(record)
    return pd.DataFrame(rows)


@st.cache_data(ttl=60 * 60, show_spinner=False)
def _savant_pitcher_skill_stats(year):
    base = _v14_savant_pitcher_skill_stats(year)
    extra = _savant_extended_custom_rows(year, "pitcher")
    if base is None or base.empty:
        return extra
    if extra is None or extra.empty:
        return base

    out = base.copy()
    ext = extra.copy()
    merged = False
    if "MLBAM ID" in out.columns and "MLBAM ID" in ext.columns:
        out["MLBAM ID"] = out["MLBAM ID"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
        ext["MLBAM ID"] = ext["MLBAM ID"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
        if out["MLBAM ID"].ne("").any() and ext["MLBAM ID"].ne("").any():
            out = out.merge(ext.drop(columns=["Player"], errors="ignore"), on="MLBAM ID", how="left", suffixes=("", "_ext"))
            merged = True
    if not merged:
        out["_k15_name"] = out["Player"].apply(normalize_name_for_match)
        ext["_k15_name"] = ext["Player"].apply(normalize_name_for_match)
        out = out.merge(ext.drop(columns=["Player"], errors="ignore"), on="_k15_name", how="left", suffixes=("", "_ext"))
        out = out.drop(columns=["_k15_name"], errors="ignore")

    for metric in [
        "PA", "Pitches", "Pitches/PA", "K %", "BB %", "Whiff %", "Swing %",
        "Zone %", "Zone Swing %", "Zone Contact %", "Chase %", "Chase Contact %",
        "Edge %", "First Pitch Swing %", "First Strike %", "Called Strike %", "CSW %",
    ]:
        ext_col = f"{metric}_ext"
        if ext_col in out.columns:
            base_values = pd.to_numeric(out.get(metric, 0), errors="coerce").fillna(0)
            ext_values = pd.to_numeric(out[ext_col], errors="coerce")
            out[metric] = ext_values.where(ext_values.notna() & (ext_values != 0), base_values)
            out = out.drop(columns=[ext_col], errors="ignore")
        elif metric not in out.columns:
            out[metric] = 0.0
    return out


@st.cache_data(ttl=60 * 60, show_spinner=False)
def _savant_team_discipline_stats(year=MLB_SEASON):
    raw = _savant_extended_custom_rows(year, "batter")
    if raw is None or raw.empty or "Team" not in raw.columns:
        return pd.DataFrame()
    raw = raw[raw["Team"].astype(str).str.strip().ne("")].copy()
    if raw.empty:
        return pd.DataFrame()
    raw["Team"] = raw["Team"].apply(_k15_canonical_team_label)
    metrics = [
        "K %", "BB %", "Whiff %", "Swing %", "Zone %", "Zone Swing %",
        "Zone Contact %", "Chase %", "Chase Contact %", "Edge %",
        "First Pitch Swing %", "First Strike %", "Called Strike %", "CSW %", "Pitches/PA",
    ]
    rows = []
    for team, group in raw.groupby("Team"):
        weights = pd.to_numeric(group.get("PA", 0), errors="coerce").fillna(0)
        if float(weights.sum()) <= 0:
            weights = pd.Series([1.0] * len(group), index=group.index)
        team_sources = sorted({str(x).strip() for x in group.get("Team Source", pd.Series(dtype=str)).tolist() if str(x).strip()})
        rec = {
            "Team": str(team),
            "PA": float(pd.to_numeric(group.get("PA", 0), errors="coerce").fillna(0).sum()),
            "Players Mapped": int(len(group)),
            "Team Mapping Source": " + ".join(team_sources) if team_sources else "Unknown",
        }
        for metric in metrics:
            vals = pd.to_numeric(group.get(metric, 0), errors="coerce").fillna(0)
            valid = vals > 0
            if valid.any():
                rec[metric] = float((vals[valid] * weights[valid]).sum() / max(1e-9, weights[valid].sum()))
            else:
                rec[metric] = 0.0
        rows.append(rec)
    return pd.DataFrame(rows)


def _k15_partial_team_discipline_fallback(team, baseline, year=MLB_SEASON, reason=""):
    """Use official team K/BB/pitch-count data when full Savant team aggregation is unavailable."""
    try:
        table = _k15_official_team_discipline_rows(year)
    except Exception:
        table = pd.DataFrame()
    target_keys = _team_keys(team)
    if table is not None and not table.empty:
        matches = table[table["Team"].apply(lambda value: bool(_team_keys(value).intersection(target_keys)))]
        if not matches.empty:
            row = matches.iloc[0]
            out = dict(baseline)
            for output, column in [("pa", "PA"), ("k_pct", "K %"), ("bb_pct", "BB %"), ("pitches_per_pa", "Pitches/PA")]:
                value = _k15_float(row.get(column, 0), 0.0)
                if value > 0:
                    out[output] = value
            out.update({
                "source": "Official MLB Stats API partial team discipline fallback",
                "availability": "partial",
                "available": False,
                "resolved_team": str(row.get("Team", team)),
                "fallback_reason": reason or "Full Savant team plate-discipline aggregation unavailable",
                "team_mapping_source": "Official MLB team aggregate",
            })
            return out
    out = dict(baseline)
    out.update({
        "source": "League-neutral fallback",
        "availability": "unavailable",
        "available": False,
        "resolved_team": _k15_canonical_team_label(team),
        "fallback_reason": reason or "Savant and official MLB team-discipline fallbacks unavailable",
        "team_mapping_source": "None",
    })
    return out


def _k15_team_discipline_profile(team, year=MLB_SEASON):
    baseline = {
        "source": "League-neutral fallback", "availability": "unavailable", "available": False,
        "resolved_team": _k15_canonical_team_label(team), "fallback_reason": "", "team_mapping_source": "",
        "pa": 0.0, "k_pct": 0.22, "bb_pct": 0.085,
        "whiff_pct": 0.245, "swing_pct": 0.47, "zone_pct": 0.49,
        "zone_contact_pct": 0.825, "chase_pct": 0.29, "chase_contact_pct": 0.60,
        "edge_pct": 0.40, "first_pitch_swing_pct": 0.30, "first_strike_pct": 0.61,
        "called_strike_pct": 0.16, "csw_pct": 0.275, "pitches_per_pa": 3.90,
    }
    try:
        table = _savant_team_discipline_stats(year)
    except Exception as exc:
        return _k15_partial_team_discipline_fallback(team, baseline, year, f"Savant team aggregation error: {exc}")
    if table is None or table.empty:
        return _k15_partial_team_discipline_fallback(
            team, baseline, year,
            "Savant batter rows were unavailable or could not be mapped to MLB teams",
        )
    target_keys = _team_keys(team)
    row = table[table["Team"].apply(lambda value: bool(_team_keys(value).intersection(target_keys)))]
    if row.empty:
        available_labels = ", ".join(sorted(table["Team"].astype(str).unique())[:8])
        return _k15_partial_team_discipline_fallback(
            team, baseline, year,
            f"No Savant team match for '{team}'" + (f"; sample labels: {available_labels}" if available_labels else ""),
        )
    row = row.iloc[0]
    mapping = {
        "pa": "PA", "k_pct": "K %", "bb_pct": "BB %", "whiff_pct": "Whiff %",
        "swing_pct": "Swing %", "zone_pct": "Zone %", "zone_contact_pct": "Zone Contact %",
        "chase_pct": "Chase %", "chase_contact_pct": "Chase Contact %", "edge_pct": "Edge %",
        "first_pitch_swing_pct": "First Pitch Swing %", "first_strike_pct": "First Strike %",
        "called_strike_pct": "Called Strike %", "csw_pct": "CSW %", "pitches_per_pa": "Pitches/PA",
    }
    out = dict(baseline)
    out.update({
        "source": "Baseball Savant batter custom leaderboard aggregated by team",
        "availability": "full",
        "available": True,
        "resolved_team": str(row.get("Team", team)),
        "fallback_reason": "",
        "team_mapping_source": str(row.get("Team Mapping Source", "Savant/MLB player mapping")),
        "players_mapped": int(_k15_float(row.get("Players Mapped", 0), 0)),
    })
    for key, col in mapping.items():
        val = _k15_float(row.get(col, 0), 0.0)
        if val > 0:
            out[key] = val
    return out


# ---------------------------------------------------------------------------
# Recent pitch usage, velocity, movement and execution
# ---------------------------------------------------------------------------
@st.cache_data(ttl=30 * 60, show_spinner=False)
def _k15_savant_pitch_log(player_id, year=MLB_SEASON, lookback_days=90):
    try:
        player_id = str(int(float(player_id)))
    except Exception:
        return pd.DataFrame()
    try:
        end_date = pd.Timestamp(eastern_now().date() if "eastern_now" in globals() else date.today())
    except Exception:
        end_date = pd.Timestamp(date.today())
    season_start = pd.Timestamp(year=int(year), month=3, day=1)
    start_date = max(season_start, end_date - pd.Timedelta(days=int(lookback_days)))
    params = {
        "all": "true", "type": "pitcher", "player_type": "pitcher",
        "hfSea": f"{int(year)}|", "hfGT": "R|",
        "player_lookup[]": player_id,
        "game_date_gt": start_date.strftime("%Y-%m-%d"),
        "game_date_lt": end_date.strftime("%Y-%m-%d"),
    }
    url = "https://baseballsavant.mlb.com/statcast_search/csv?" + urllib.parse.urlencode(params, doseq=True)
    try:
        from io import StringIO
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"}, timeout=40)
        response.raise_for_status()
        text = response.text.strip()
        if not text or "<html" in text.lower() or "," not in text:
            return pd.DataFrame()
        return pd.read_csv(StringIO(text))
    except Exception:
        return pd.DataFrame()


def _k15_pitch_event_rates(frame):
    if frame is None or frame.empty:
        return {
            "pitches": 0, "whiff_pct": 0.0, "called_strike_pct": 0.0, "csw_pct": 0.0,
            "chase_pct": 0.0, "zone_contact_pct": 0.0, "chase_contact_pct": 0.0,
            "first_strike_pct": 0.0,
        }
    df = frame.copy()
    description = df.get("description", pd.Series([""] * len(df), index=df.index)).astype(str).str.lower()
    swing_desc = {
        "swinging_strike", "swinging_strike_blocked", "foul", "foul_tip", "foul_bunt",
        "hit_into_play", "hit_into_play_no_out", "hit_into_play_score", "missed_bunt",
    }
    whiff_desc = {"swinging_strike", "swinging_strike_blocked", "missed_bunt"}
    contact_desc = swing_desc - whiff_desc
    swings = description.isin(swing_desc)
    whiffs = description.isin(whiff_desc)
    called = description.eq("called_strike")
    zone = pd.to_numeric(df.get("zone", pd.Series([pd.NA] * len(df), index=df.index)), errors="coerce")
    in_zone = zone.between(1, 9, inclusive="both")
    out_zone = zone.notna() & ~in_zone
    zone_swings = swings & in_zone
    chase_swings = swings & out_zone
    zone_contacts = description.isin(contact_desc) & in_zone
    chase_contacts = description.isin(contact_desc) & out_zone

    pitch_number = pd.to_numeric(df.get("pitch_number", pd.Series([pd.NA] * len(df), index=df.index)), errors="coerce")
    first = pitch_number.eq(1)
    first_strike_desc = called | whiffs | description.isin({"foul", "foul_tip", "foul_bunt", "hit_into_play", "hit_into_play_no_out", "hit_into_play_score"})
    return {
        "pitches": int(len(df)),
        "whiff_pct": float(whiffs.sum() / max(1, swings.sum())),
        "called_strike_pct": float(called.sum() / max(1, len(df))),
        "csw_pct": float((called.sum() + whiffs.sum()) / max(1, len(df))),
        "chase_pct": float(chase_swings.sum() / max(1, out_zone.sum())),
        "zone_contact_pct": float(zone_contacts.sum() / max(1, zone_swings.sum())),
        "chase_contact_pct": float(chase_contacts.sum() / max(1, chase_swings.sum())),
        "first_strike_pct": float((first & first_strike_desc).sum() / max(1, first.sum())),
    }


def _k15_player_id(pitcher, pitcher_this_year, pitcher_last_year):
    value = get_value(pitcher_this_year, "Player", pitcher, "MLBAM ID", "")
    if str(value).strip() in ["", "nan", "None"]:
        value = get_value(pitcher_last_year, "Player", pitcher, "MLBAM ID", "")
    try:
        return str(int(float(value)))
    except Exception:
        return ""


@st.cache_data(ttl=30 * 60, show_spinner=False)
def _k15_recent_pitch_profile_cached(player_id, year=MLB_SEASON):
    neutral = {
        "source": "Recent Savant pitch log unavailable", "available": False, "starts": 0,
        "rate_multiplier": 1.0, "velocity_delta": 0.0, "csw_delta": 0.0,
        "whiff_delta": 0.0, "called_strike_delta": 0.0, "usage_quality_shift": 0.0,
        "shape_change_inches": 0.0, "release_change_inches": 0.0,
        "recent_csw": 0.0, "baseline_csw": 0.0, "status": "Neutral recent pitch trend fallback.",
    }
    raw = _k15_savant_pitch_log(player_id, year, 90)
    if raw is None or raw.empty or "game_date" not in raw.columns:
        return neutral
    df = raw.copy()
    df["_game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    game_key_col = "game_pk" if "game_pk" in df.columns else "game_date"
    start_keys = (
        df[[game_key_col, "_game_date"]]
        .dropna(subset=["_game_date"])
        .drop_duplicates(game_key_col)
        .sort_values("_game_date", ascending=False)[game_key_col]
        .tolist()
    )
    if len(start_keys) < 2:
        neutral["starts"] = len(start_keys)
        neutral["status"] = "Fewer than two recent Savant starts; neutral trend applied."
        return neutral
    recent_keys = set(start_keys[:3])
    recent = df[df[game_key_col].isin(recent_keys)].copy()
    baseline = df[~df[game_key_col].isin(recent_keys)].copy()
    if baseline.empty or len(recent) < 40 or len(baseline) < 80:
        neutral["starts"] = len(start_keys)
        neutral["status"] = "Recent Savant sample is too small for a directional pitch trend."
        return neutral

    recent_rates = _k15_pitch_event_rates(recent)
    base_rates = _k15_pitch_event_rates(baseline)
    pitch_col = "pitch_type" if "pitch_type" in df.columns else None
    velocity_delta = csw_delta = whiff_delta = called_delta = usage_quality_shift = 0.0
    shape_change = release_change = 0.0
    total_recent = max(1, len(recent))

    if pitch_col:
        baseline_type_rates = {}
        for ptype, group in baseline.groupby(pitch_col):
            if str(ptype).strip() and len(group) >= 20:
                baseline_type_rates[str(ptype)] = _k15_pitch_event_rates(group).get("csw_pct", 0.0)
        recent_usage = recent[pitch_col].astype(str).value_counts(normalize=True).to_dict()
        base_usage = baseline[pitch_col].astype(str).value_counts(normalize=True).to_dict()
        quality_recent = sum(float(weight) * baseline_type_rates.get(str(ptype), base_rates["csw_pct"]) for ptype, weight in recent_usage.items())
        quality_base = sum(float(weight) * baseline_type_rates.get(str(ptype), base_rates["csw_pct"]) for ptype, weight in base_usage.items())
        usage_quality_shift = quality_recent - quality_base

        movement_rows = []
        release_rows = []
        velo_rows = []
        for ptype, rgroup in recent.groupby(pitch_col):
            bgroup = baseline[baseline[pitch_col].astype(str) == str(ptype)]
            if len(rgroup) < 8 or len(bgroup) < 20:
                continue
            weight = len(rgroup) / total_recent
            if "release_speed" in df.columns:
                rv = pd.to_numeric(rgroup["release_speed"], errors="coerce").mean()
                bv = pd.to_numeric(bgroup["release_speed"], errors="coerce").mean()
                if pd.notna(rv) and pd.notna(bv):
                    velo_rows.append((float(rv - bv), weight))
            if "pfx_x" in df.columns and "pfx_z" in df.columns:
                rx = pd.to_numeric(rgroup["pfx_x"], errors="coerce").mean()
                rz = pd.to_numeric(rgroup["pfx_z"], errors="coerce").mean()
                bx = pd.to_numeric(bgroup["pfx_x"], errors="coerce").mean()
                bz = pd.to_numeric(bgroup["pfx_z"], errors="coerce").mean()
                if all(pd.notna(v) for v in [rx, rz, bx, bz]):
                    movement_rows.append((math.sqrt((rx - bx) ** 2 + (rz - bz) ** 2) * 12.0, weight))
            if "release_pos_x" in df.columns and "release_pos_z" in df.columns:
                rx = pd.to_numeric(rgroup["release_pos_x"], errors="coerce").mean()
                rz = pd.to_numeric(rgroup["release_pos_z"], errors="coerce").mean()
                bx = pd.to_numeric(bgroup["release_pos_x"], errors="coerce").mean()
                bz = pd.to_numeric(bgroup["release_pos_z"], errors="coerce").mean()
                if all(pd.notna(v) for v in [rx, rz, bx, bz]):
                    release_rows.append((math.sqrt((rx - bx) ** 2 + (rz - bz) ** 2) * 12.0, weight))
        velocity_delta = _k15_weighted(velo_rows) or 0.0
        shape_change = _k15_weighted(movement_rows) or 0.0
        release_change = _k15_weighted(release_rows) or 0.0

    csw_delta = recent_rates["csw_pct"] - base_rates["csw_pct"]
    whiff_delta = recent_rates["whiff_pct"] - base_rates["whiff_pct"]
    called_delta = recent_rates["called_strike_pct"] - base_rates["called_strike_pct"]
    directional = (
        (velocity_delta * 0.010) +
        (csw_delta * 0.55) +
        (whiff_delta * 0.20) +
        (called_delta * 0.25) +
        (usage_quality_shift * 0.45)
    )
    multiplier = 1.0 + max(-0.05, min(0.05, directional))
    return {
        "source": "Baseball Savant pitch-level last 3 starts vs prior 90-day baseline",
        "available": True, "starts": len(start_keys), "rate_multiplier": round(multiplier, 4),
        "velocity_delta": round(velocity_delta, 3), "csw_delta": round(csw_delta, 4),
        "whiff_delta": round(whiff_delta, 4), "called_strike_delta": round(called_delta, 4),
        "usage_quality_shift": round(usage_quality_shift, 4),
        "shape_change_inches": round(shape_change, 3), "release_change_inches": round(release_change, 3),
        "recent_csw": round(recent_rates["csw_pct"], 4), "baseline_csw": round(base_rates["csw_pct"], 4),
        "status": (
            f"Recent pitch trend {multiplier:.3f}x: velo {velocity_delta:+.2f} mph, "
            f"CSW {csw_delta*100:+.1f} pts, whiff {whiff_delta*100:+.1f} pts, "
            f"usage quality {usage_quality_shift*100:+.1f} pts."
        ),
    }


def _k15_recent_pitch_profile(pitcher, pitcher_this_year, pitcher_last_year):
    player_id = _k15_player_id(pitcher, pitcher_this_year, pitcher_last_year)
    if not player_id:
        return _k15_recent_pitch_profile_cached("", MLB_SEASON)
    return _k15_recent_pitch_profile_cached(player_id, MLB_SEASON)


# ---------------------------------------------------------------------------
# Arsenal as a rate adjustment, not a direct strikeout award
# ---------------------------------------------------------------------------
def pitch_type_arsenal_adjustment(pitcher, opponent, pitcher_arsenal_df=None, team_pitch_type_df=None):
    pitcher_rows = _pitcher_arsenal_rows(pitcher, pitcher_arsenal_df)
    team_rows = _team_pitch_type_rows(opponent, team_pitch_type_df)
    columns = [
        "Pitch", "Usage", "Pitcher Pitches", "Opponent Pitches", "Sample Reliability",
        "Pitcher K", "Opponent K", "League K", "K Edge",
        "Pitcher Whiff", "Opponent Whiff", "League Whiff", "Whiff Edge",
        "Pitcher Put Away", "Opponent Put Away", "League Put Away", "Put Away Edge",
        "Combined Edge", "Weapon", "Contribution", "Match Status",
    ]
    neutral = {
        "modifier": 0.0, "k_rate_multiplier": 1.0, "k_rate_adjustment": 0.0,
        "score": 0.0, "weapon_count": 0, "weapon_bonus": 0.0, "weapon_usage": 0.0,
        "scored_count": 0, "coverage": 0.0,
        "status": "Neutral fallback - pitch-type matchup unavailable",
        "details": pd.DataFrame(columns=columns),
    }
    if pitcher_rows is None or pitcher_rows.empty or team_rows is None or team_rows.empty:
        return neutral

    all_team = team_pitch_type_df.copy() if team_pitch_type_df is not None else pd.DataFrame()
    for col in ["K", "Whiff", "Put Away"]:
        if col not in all_team.columns:
            all_team[col] = 0.0
        all_team[col] = all_team[col].apply(lambda x: _to_rate(x, 0.0))
    league_k = all_team[all_team["K"] > 0].groupby("Pitch Type")["K"].mean().to_dict() if not all_team.empty else {}
    league_whiff = all_team[all_team["Whiff"] > 0].groupby("Pitch Type")["Whiff"].mean().to_dict() if not all_team.empty else {}
    league_put = all_team[all_team["Put Away"] > 0].groupby("Pitch Type")["Put Away"].mean().to_dict() if not all_team.empty else {}
    lookup = team_rows.drop_duplicates("Pitch Type").set_index("Pitch Type").to_dict("index")

    detail = []
    total_score = total_usage = matched_usage = weapon_usage = 0.0
    weapons = scored = 0
    for _, prow in pitcher_rows.iterrows():
        pitch = str(prow.get("Pitch Type", "")).strip()
        usage = _normalized_usage_rate(prow.get("Usage", 0), 0.0)
        if not pitch or usage < 0.05:
            continue
        if pitch not in lookup:
            detail.append({"Pitch": pitch, "Usage": round(usage*100,1), "Match Status": "No opponent pitch-type match"})
            continue
        orow = lookup[pitch]
        p_k = _to_rate(prow.get("K", 0), 0.0); o_k = _to_rate(orow.get("K", 0), 0.0)
        p_w = _to_rate(prow.get("Whiff", 0), 0.0); o_w = _to_rate(orow.get("Whiff", 0), 0.0)
        p_p = _to_rate(prow.get("Put Away", 0), 0.0); o_p = _to_rate(orow.get("Put Away", 0), 0.0)
        lk = _to_rate(league_k.get(pitch, 0.22), 0.22)
        lw = _to_rate(league_whiff.get(pitch, 0.24), 0.24)
        lp = _to_rate(league_put.get(pitch, 0.20), 0.20)
        p_pitches = _k15_float(prow.get("Pitches", 0), 0.0)
        o_pitches = _k15_float(orow.get("Pitches", 0), 0.0)
        reliability = min(1.0, p_pitches / 160.0 if p_pitches > 0 else 0.55) * min(1.0, o_pitches / 650.0 if o_pitches > 0 else 0.55)
        reliability = max(0.25, reliability)
        k_edge = (p_k - lk) + (o_k - lk)
        whiff_edge = (p_w - lw) + (o_w - lw)
        put_edge = (p_p - lp) + (o_p - lp)
        combined = ((0.40 * k_edge) + (0.38 * whiff_edge) + (0.22 * put_edge)) * reliability
        contribution = usage * combined
        total_score += contribution
        total_usage += usage
        matched_usage += usage
        scored += 1
        weapon = usage >= 0.09 and combined >= 0.05
        if weapon:
            weapons += 1
            weapon_usage += usage
        detail.append({
            "Pitch": pitch, "Usage": round(usage*100,1), "Pitcher Pitches": int(p_pitches),
            "Opponent Pitches": int(o_pitches), "Sample Reliability": round(reliability*100,1),
            "Pitcher K": round(p_k*100,1), "Opponent K": round(o_k*100,1), "League K": round(lk*100,1),
            "K Edge": round(k_edge*100,1), "Pitcher Whiff": round(p_w*100,1),
            "Opponent Whiff": round(o_w*100,1), "League Whiff": round(lw*100,1),
            "Whiff Edge": round(whiff_edge*100,1), "Pitcher Put Away": round(p_p*100,1),
            "Opponent Put Away": round(o_p*100,1), "League Put Away": round(lp*100,1),
            "Put Away Edge": round(put_edge*100,1), "Combined Edge": round(combined*100,1),
            "Weapon": "Yes" if weapon else "No", "Contribution": round(contribution*100,2),
            "Match Status": "Matched and scored",
        })

    if scored == 0 or total_usage <= 0:
        neutral["details"] = pd.DataFrame(detail, columns=columns)
        return neutral
    normalized = max(-0.15, min(0.15, total_score / max(0.25, total_usage)))
    # Positive pitch-matchup information remains meaningful. Negative signals are
    # capped at only -5% until the new history validates arsenal-driven unders.
    relative_adjustment = normalized * 0.85
    relative_adjustment = max(-0.05, min(0.12, relative_adjustment))
    multiplier = 1.0 + relative_adjustment
    estimated_k_equivalent = relative_adjustment * 5.3
    detail_df = pd.DataFrame(detail)
    if not detail_df.empty and "Contribution" in detail_df.columns:
        detail_df = detail_df.sort_values("Contribution", ascending=False).reset_index(drop=True)
    coverage = min(100.0, matched_usage * 100.0)
    return {
        "modifier": round(estimated_k_equivalent, 2),
        "k_rate_multiplier": round(multiplier, 4),
        "k_rate_adjustment": round(relative_adjustment, 4),
        "score": round(normalized * 100, 1), "weapon_count": int(weapons),
        "weapon_bonus": 0.0, "weapon_usage": round(weapon_usage*100,1),
        "scored_count": int(scored), "coverage": round(coverage,1),
        "status": (
            f"V15 rate-based arsenal: {scored} pitches / {coverage:.1f}% usage matched; "
            f"score {normalized*100:+.1f}, K-rate multiplier {multiplier:.3f}. "
            "Positive cap +12%; negative cap -5%; no direct weapon-count bonus."
        ),
        "details": detail_df,
    }


def apply_pitch_type_modifier(base_projection, pitcher, opponent, pitcher_arsenal_df=None, team_pitch_type_df=None):
    details = pitch_type_arsenal_adjustment(pitcher, opponent, pitcher_arsenal_df, team_pitch_type_df)
    return max(0.0, float(base_projection or 0.0) * float(details.get("k_rate_multiplier", 1.0) or 1.0)), details


# ---------------------------------------------------------------------------
# Opportunity engine and K-rate engine
# ---------------------------------------------------------------------------
def _k15_pitcher_season_profile(pitcher, current, prior):
    def one(df):
        bf = _k15_float(get_value(df, "Player", pitcher, "BF", 0), 0.0)
        ip = _k15_float(get_value(df, "Player", pitcher, "IP", 0), 0.0)
        so = _k15_float(get_value(df, "Player", pitcher, "SO", 0), 0.0)
        gs = _k15_float(get_value(df, "Player", pitcher, "GS", get_value(df, "Player", pitcher, "Games Started", 0)), 0.0)
        games = _k15_float(get_value(df, "Player", pitcher, "G", 0), 0.0)
        pitches = _k15_float(get_value(df, "Player", pitcher, "Pitches", 0), 0.0)
        if bf <= 0 and ip > 0:
            bf = ip * 4.3
        return {"bf": bf, "ip": ip, "so": so, "gs": gs, "g": games, "pitches": pitches}
    cur = one(current); prev = one(prior)
    cur_rate = cur["so"] / cur["bf"] if cur["bf"] > 0 else None
    prev_rate = prev["so"] / prev["bf"] if prev["bf"] > 0 else None
    cur_weight = min(0.72, cur["bf"] / 250.0) if cur["bf"] > 0 else 0.0
    if cur_rate is not None and prev_rate is not None:
        base_rate = (cur_weight * cur_rate) + ((1.0-cur_weight) * prev_rate)
    elif cur_rate is not None:
        base_rate = (0.75 * cur_rate) + (0.25 * 0.22)
    elif prev_rate is not None:
        base_rate = (0.75 * prev_rate) + (0.25 * 0.22)
    else:
        base_rate = 0.22
    p_per_bf = _k15_weighted([
        (cur["pitches"] / cur["bf"], cur["bf"]) if cur["bf"] > 0 and cur["pitches"] > 0 else (0,0),
        (prev["pitches"] / prev["bf"], min(prev["bf"], 250)) if prev["bf"] > 0 and prev["pitches"] > 0 else (0,0),
    ])
    p_per_ip = _k15_weighted([
        (cur["pitches"] / cur["ip"], cur["ip"]) if cur["ip"] > 0 and cur["pitches"] > 0 else (0,0),
        (prev["pitches"] / prev["ip"], min(prev["ip"], 80)) if prev["ip"] > 0 and prev["pitches"] > 0 else (0,0),
    ])
    pitches_per_start = _k15_weighted([
        (cur["pitches"] / cur["gs"], cur["gs"]*1.5) if cur["gs"] > 0 and cur["pitches"] > 0 else (0,0),
        (prev["pitches"] / prev["gs"], min(prev["gs"], 12)) if prev["gs"] > 0 and prev["pitches"] > 0 else (0,0),
    ])
    return {
        "current": cur, "prior": prev, "base_k_rate": max(0.08, min(0.42, base_rate)),
        "pitches_per_bf": max(3.2, min(4.8, p_per_bf if p_per_bf is not None else 3.90)),
        "pitches_per_ip": max(13.5, min(19.5, p_per_ip if p_per_ip is not None else 15.8)),
        "pitches_per_start": max(45.0, min(112.0, pitches_per_start if pitches_per_start is not None else 0.0)) if pitches_per_start else 0.0,
    }


def _k15_pitcher_discipline_profile(pitcher, current, prior):
    metrics = {
        "whiff_pct": ("Whiff %", 0.245), "swing_pct": ("Swing %", 0.47),
        "called_strike_pct": ("Called Strike %", 0.16), "csw_pct": ("CSW %", 0.275),
        "chase_pct": ("Chase %", 0.29), "zone_contact_pct": ("Zone Contact %", 0.825),
        "chase_contact_pct": ("Chase Contact %", 0.60), "first_strike_pct": ("First Strike %", 0.61),
        "zone_pct": ("Zone %", 0.49), "edge_pct": ("Edge %", 0.40),
    }
    out = {"source": "Savant season skill blend"}
    current_sample = _k15_float(get_value(current, "Player", pitcher, "Pitches", 0), 0.0)
    prior_sample = _k15_float(get_value(prior, "Player", pitcher, "Pitches", 0), 0.0)
    for key, (column, baseline) in metrics.items():
        cv = _k15_rate(get_value(current, "Player", pitcher, column, 0), 0.0)
        pv = _k15_rate(get_value(prior, "Player", pitcher, column, 0), 0.0)
        value = _k15_weighted([
            (cv, max(1.0, min(current_sample, 800))) if cv > 0 else (0,0),
            (pv, max(1.0, min(prior_sample, 500))*0.55) if pv > 0 else (0,0),
        ])
        out[key] = value if value is not None and value > 0 else baseline
    if out["csw_pct"] <= 0:
        out["csw_pct"] = out["called_strike_pct"] + out["whiff_pct"] * out["swing_pct"]
    signal = (
        0.32 * (out["whiff_pct"] - 0.245) +
        0.22 * (out["csw_pct"] - 0.275) +
        0.14 * (out["chase_pct"] - 0.29) -
        0.14 * (out["zone_contact_pct"] - 0.825) -
        0.08 * (out["chase_contact_pct"] - 0.60) +
        0.10 * (out["first_strike_pct"] - 0.61)
    )
    out["rate_multiplier"] = 1.0 + max(-0.04, min(0.06, signal * 1.65))
    return out


def _k15_opponent_split_profile(opponent, pitcher_hand, team_batting_rhp, team_batting_lhp):
    ab_l = _k15_float(get_value(team_batting_lhp, "Teams", opponent, "At Bats", 0), 0.0)
    ab_r = _k15_float(get_value(team_batting_rhp, "Teams", opponent, "At Bats", 0), 0.0)
    so_l = _k15_float(get_value(team_batting_lhp, "Teams", opponent, "Strikeouts", 0), 0.0)
    so_r = _k15_float(get_value(team_batting_rhp, "Teams", opponent, "Strikeouts", 0), 0.0)
    overall = (so_l + so_r) / (ab_l + ab_r) if (ab_l + ab_r) > 0 else 0.22
    split_ab, split_so = (ab_l, so_l) if str(pitcher_hand).upper().startswith("L") else (ab_r, so_r)
    raw_split = split_so / split_ab if split_ab > 0 else overall
    sample_weight = min(0.75, split_ab / 900.0) if split_ab > 0 else 0.0
    split = ((1.0-sample_weight)*overall) + (sample_weight*raw_split)
    ratio = split / 0.22 if 0.22 > 0 else 1.0
    multiplier = max(0.88, min(1.14, ratio ** 0.58))
    return {
        "overall_k_rate": overall, "split_k_rate": split, "raw_split_k_rate": raw_split,
        "split_ab": split_ab, "sample_weight": sample_weight, "rate_multiplier": multiplier,
    }


def _k15_opponent_discipline_multiplier(profile):
    p = profile or {}
    signal = (
        0.22 * (_k15_float(p.get("whiff_pct"), 0.245) - 0.245) +
        0.12 * (_k15_float(p.get("chase_pct"), 0.29) - 0.29) -
        0.22 * (_k15_float(p.get("zone_contact_pct"), 0.825) - 0.825) -
        0.12 * (_k15_float(p.get("chase_contact_pct"), 0.60) - 0.60) +
        0.06 * (_k15_float(p.get("first_strike_pct"), 0.61) - 0.61)
    )
    return 1.0 + max(-0.04, min(0.05, signal * 1.75))


def _k15_workload_projection(pitcher, opponent, season_profile, base_k_rate, current, prior, team_batting_rhp, team_batting_lhp, discipline):
    base_ip, cur_role, prior_role, hybrid = _blended_pitcher_start_ip(pitcher, current, prior)
    leash = get_opponent_leash_details(opponent, base_ip, base_k_rate, team_batting_rhp, team_batting_lhp)
    projected_ip = max(2.5, min(7.15, _k15_float(leash.get("adjusted_ip"), base_ip)))
    p_per_ip = season_profile["pitches_per_ip"]
    historical_start_pitches = season_profile.get("pitches_per_start", 0.0)
    ip_based_pitches = projected_ip * p_per_ip
    if historical_start_pitches > 0:
        projected_pitches = (0.62 * historical_start_pitches) + (0.38 * ip_based_pitches)
    else:
        projected_pitches = ip_based_pitches
    if hybrid:
        projected_pitches = min(projected_pitches, 88.0)
    projected_pitches = max(40.0, min(112.0, projected_pitches))

    pitcher_ppbf = season_profile["pitches_per_bf"]
    opponent_pppa = max(3.35, min(4.55, _k15_float((discipline or {}).get("pitches_per_pa"), 3.90)))
    matchup_ppbf = max(3.30, min(4.70, (0.64*pitcher_ppbf) + (0.36*opponent_pppa)))
    projected_bf = projected_pitches / matchup_ppbf
    projected_bf = max(projected_ip*3.65, min(projected_ip*5.10, projected_bf, 31.0))
    third_time = 1.0 / (1.0 + math.exp(-(projected_bf - 19.2) / 2.1))
    pressure_score = ((opponent_pppa - 3.90) / 0.25) + ((_k15_float((discipline or {}).get("bb_pct"), 0.085) - 0.085) / 0.025)
    bf_sd = 3.1 + (0.8 if hybrid else 0.0) + (0.5 if abs(pressure_score) >= 1.5 else 0.0)
    return {
        "projected_start_ip": round(projected_ip, 2), "projected_pitches": round(projected_pitches,1),
        "projected_bf": round(projected_bf,1), "pitcher_pitches_per_bf": round(pitcher_ppbf,3),
        "opponent_pitches_per_pa": round(opponent_pppa,3), "projected_pitches_per_bf": round(matchup_ppbf,3),
        "third_time_probability": round(third_time,4), "workload_pressure_score": round(pressure_score,2),
        "bf_std": round(bf_sd,2), "current_role": cur_role, "prior_role": prior_role,
        "hybrid_or_reliever": bool(hybrid), "opponent_leash": leash,
        "status": (
            f"Opportunity engine: {projected_pitches:.1f} pitches / {matchup_ppbf:.2f} pitches per BF "
            f"= {projected_bf:.1f} projected BF; {projected_ip:.2f} IP, TTO3 {third_time*100:.0f}%."
        ),
    }


def expected_strikeouts(pitcher, opponent, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df=None, team_pitch_type_df=None, return_details=False):
    season = _k15_pitcher_season_profile(pitcher, pitcher_this_year, pitcher_last_year)
    hand = get_value(pitcher_this_year, "Player", pitcher, "Throws", None)
    if hand in [None, ""]:
        hand = get_value(pitcher_last_year, "Player", pitcher, "Throws", "R")
    split = _k15_opponent_split_profile(opponent, hand, team_batting_rhp, team_batting_lhp)
    discipline = _k15_team_discipline_profile(opponent, MLB_SEASON)
    pitcher_skill = _k15_pitcher_discipline_profile(pitcher, pitcher_this_year, pitcher_last_year)
    recent_pitch = _k15_recent_pitch_profile(pitcher, pitcher_this_year, pitcher_last_year)
    arsenal = pitch_type_arsenal_adjustment(pitcher, opponent, pitcher_arsenal_df, team_pitch_type_df)
    opponent_disc_mult = _k15_opponent_discipline_multiplier(discipline)

    base_rate = season["base_k_rate"]
    rate_multipliers = {
        "team_split": split["rate_multiplier"],
        "arsenal": _k15_float(arsenal.get("k_rate_multiplier"), 1.0),
        "pitcher_skill": _k15_float(pitcher_skill.get("rate_multiplier"), 1.0),
        "opponent_discipline": opponent_disc_mult,
        "recent_pitch": _k15_float(recent_pitch.get("rate_multiplier"), 1.0),
    }
    combined_mult = 1.0
    for value in rate_multipliers.values():
        combined_mult *= value
    combined_mult = max(0.76, min(1.30, combined_mult))
    matchup_rate = max(0.075, min(0.425, base_rate * combined_mult))
    workload = _k15_workload_projection(
        pitcher, opponent, season, base_rate, pitcher_this_year, pitcher_last_year,
        team_batting_rhp, team_batting_lhp, discipline,
    )
    projected_bf = _k15_float(workload.get("projected_bf"), 22.0)
    base_projection = projected_bf * base_rate
    projection = projected_bf * matchup_rate
    six_ip_projection = 25.8 * matchup_rate

    # Structural uncertainty combines event variance with BF and K-rate uncertainty.
    bf_sd = _k15_float(workload.get("bf_std"), 3.2)
    rate_sd = 0.030 + (0.008 if not recent_pitch.get("available") else 0.0)
    binomial_var = projected_bf * matchup_rate * (1.0-matchup_rate)
    structural_std = math.sqrt(max(0.25, binomial_var) + (matchup_rate*bf_sd)**2 + (projected_bf*rate_sd)**2)
    structural_std = max(1.35, min(3.10, structural_std))

    under_notes = []
    if split["split_k_rate"] <= 0.205:
        under_notes.append("opponent split K rate <=20.5%")
    if rate_multipliers["arsenal"] <= 0.985:
        under_notes.append("negative arsenal rate matchup")
    if rate_multipliers["pitcher_skill"] <= 0.995:
        under_notes.append("pitcher swing/strike skill below neutral")
    if rate_multipliers["opponent_discipline"] <= 0.99:
        under_notes.append("opponent contact discipline suppresses Ks")
    if rate_multipliers["recent_pitch"] <= 0.99:
        under_notes.append("recent pitch execution trend negative")
    if projected_bf <= 21.5 or workload.get("workload_pressure_score", 0) >= 1.25:
        under_notes.append("opportunity/workload pressure")

    arsenal.update({
        "base_projection": round(base_projection,2), "adjusted_projection": round(projection,2),
        "six_ip_projection": round(six_ip_projection,2), "ip_scale": round(workload["projected_start_ip"]/6.0,3),
        "workload": workload, "opponent_leash": workload.get("opponent_leash", {}),
        "projection_architecture": K_MODEL_ARCHITECTURE,
        "base_k_rate": round(base_rate,5), "team_split_k_rate": round(split["split_k_rate"],5),
        "matchup_k_rate": round(matchup_rate,5), "rate_multipliers": rate_multipliers,
        "pitcher_discipline": pitcher_skill, "opponent_discipline": discipline,
        "recent_pitch_profile": recent_pitch, "structural_std": round(structural_std,3),
        "under_support_count": len(under_notes), "under_support_notes": under_notes,
        "status": arsenal.get("status", "") + " | " + workload.get("status", "") +
                  f" | K-rate engine: base {base_rate*100:.1f}% → matchup {matchup_rate*100:.1f}%.",
    })
    # Display an actual matchup-specific K equivalent, but do not apply it directly.
    arsenal["modifier"] = round(projected_bf * base_rate * (rate_multipliers["arsenal"]-1.0), 2)
    if return_details:
        return max(0.0, projection), arsenal
    return max(0.0, projection)


def six_inning_strikeouts(pitcher, opponent, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df=None, team_pitch_type_df=None, return_details=False, game_location="neutral", umpire_context=None):
    _, details = expected_strikeouts(
        pitcher, opponent, pitcher_this_year, pitcher_last_year,
        team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df,
        return_details=True,
    )
    value = _k15_float(details.get("six_ip_projection"), 0.0)
    if return_details:
        clone = dict(details)
        clone["base_projection"] = round(25.8 * _k15_float(details.get("base_k_rate"), 0.22), 2)
        clone["adjusted_projection"] = round(value, 2)
        return value, clone
    return value


# ---------------------------------------------------------------------------
# Controlled confirmed-lineup adjustment
# ---------------------------------------------------------------------------
_v14_build_lineup_k_blend_details = build_lineup_k_blend_details


def build_lineup_k_blend_details(game_pk, side, opponent, pitcher_hand, team_batting_rhp, team_batting_lhp, pitcher="", pitcher_arsenal_df=None, team_pitch_type_df=None):
    details = _v14_build_lineup_k_blend_details(
        game_pk, side, opponent, pitcher_hand, team_batting_rhp, team_batting_lhp,
        pitcher, pitcher_arsenal_df, team_pitch_type_df,
    )
    hitters = details.get("hitters", pd.DataFrame())
    baseline = _k15_float(details.get("team_baseline_k_rate"), 0.22)
    weighted_k = None
    if isinstance(hitters, pd.DataFrame) and not hitters.empty and "K%" in hitters.columns:
        order_weights = {1:1.16, 2:1.14, 3:1.12, 4:1.10, 5:1.06, 6:1.00, 7:0.94, 8:0.88, 9:0.82}
        vals = []
        for _, row in hitters.iterrows():
            k = _k15_rate(row.get("K%", 0), 0.0)
            if k <= 0:
                continue
            order = int(_k15_float(row.get("Order", 9), 9))
            sample = _k15_float(row.get("PA", row.get("AB", 0)), 0.0)
            reliability = min(1.0, sample/90.0) if sample > 0 else 0.35
            regressed = (reliability*k) + ((1.0-reliability)*baseline)
            vals.append((regressed, order_weights.get(order, 0.82)))
        weighted_k = _k15_weighted(vals)
    if weighted_k is None:
        weighted_k = _k15_float(details.get("lineup_k_rate"), baseline)
    blended = (0.72*weighted_k) + (0.28*baseline)
    k_ratio = blended / baseline if baseline > 0 else 1.0
    k_mult = max(0.93, min(1.08, k_ratio ** 0.58))
    pitch_diag = _k15_float(details.get("pitch_type_multiplier"), 1.0)
    hand_diag = _k15_float(details.get("hand_stack_multiplier"), 1.0)
    pitch_mult = 1.0 + max(-0.04, min(0.05, (pitch_diag-1.0)*0.32))
    hand_mult = 1.0 + max(-0.02, min(0.02, (hand_diag-1.0)*0.25))
    projection_mult = max(0.90, min(1.10, k_mult*pitch_mult*hand_mult))
    confirmed = "confirmed" in str(details.get("source", "")).lower() and int(details.get("hitters_found",0) or 0) >= 7
    if not confirmed:
        projection_mult = 1.0 + ((projection_mult-1.0)*0.35)
    details.update({
        "lineup_k_rate": weighted_k, "blended_k_rate": blended,
        "k_multiplier": k_mult, "pitch_type_rate_multiplier": pitch_mult,
        "hand_stack_rate_multiplier": hand_mult, "projection_multiplier": projection_mult,
        "multiplier": projection_mult, "lineup_weight": 0.72 if confirmed else 0.25,
        "team_weight": 0.28 if confirmed else 0.75,
        "status": (
            f"V15 active lineup K-rate adjustment: batting-order weighted K {weighted_k*100:.1f}% "
            f"vs team baseline {baseline*100:.1f}%; controlled multiplier {projection_mult:.3f}."
        ),
    })
    return details


def apply_lineup_k_adjustment(projection, lineup_details):
    try:
        return max(0.0, float(projection) * float((lineup_details or {}).get("projection_multiplier", 1.0) or 1.0))
    except Exception:
        return projection


# ---------------------------------------------------------------------------
# New-model calibration and uncertainty: do not reuse old-model coefficients
# ---------------------------------------------------------------------------
def _history_before_today():
    df = load_pitcher_recent_form()
    if df is None or df.empty:
        return pd.DataFrame(columns=RECENT_FORM_COLUMNS)
    out = df.copy()
    for col in RECENT_FORM_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    out["_date"] = pd.to_datetime(out["Date"], errors="coerce")
    try:
        cutoff = pd.to_datetime(today_et_string())
    except Exception:
        cutoff = pd.to_datetime(str(date.today()))
    out = out[out["_date"] < cutoff].copy()
    # A new architecture cannot be calibrated with residuals generated by v14.
    if "Model Version" in out.columns:
        out = out[out["Model Version"].astype(str) == str(MODEL_VERSION)].copy()
    out["_actual"] = pd.to_numeric(out["Actual Ks"], errors="coerce")
    out["_raw"] = pd.to_numeric(out["Raw Projection"], errors="coerce")
    out["_projection"] = pd.to_numeric(out["Projection"], errors="coerce")
    out["_global"] = pd.to_numeric(out["Global Calibrated Projection"], errors="coerce")
    out["_raw"] = out["_raw"].fillna(out["_projection"])
    out["_global"] = out["_global"].fillna(out["_projection"])
    return out.dropna(subset=["_actual", "_raw"])


@st.cache_data(ttl=300, show_spinner=False)
def get_global_k_calibration():
    hist = _history_before_today().sort_values("_date", ascending=False).head(300)
    if len(hist) < 60:
        return {
            "intercept": 0.0, "slope": 1.0, "sample": int(len(hist)),
            "source": "V15 identity calibration until 60 completed starts",
            "error_std": 1.95,
        }
    x = hist["_raw"].astype(float).tolist(); y = hist["_actual"].astype(float).tolist()
    mx = sum(x)/len(x); my = sum(y)/len(y)
    variance = sum((v-mx)**2 for v in x)
    slope = sum((a-mx)*(b-my) for a,b in zip(x,y))/variance if variance > 1e-9 else 1.0
    intercept = my - slope*mx
    slope = max(0.75, min(1.15, slope)); intercept = max(-0.75, min(1.0, intercept))
    errors = [b-(intercept+slope*a) for a,b in zip(x,y)]
    return {
        "intercept": round(intercept,4), "slope": round(slope,4), "sample": len(hist),
        "source": "Rolling V15-only last 300 completed starts",
        "error_std": round(max(1.35, min(3.0, statistics.pstdev(errors) if len(errors)>1 else 1.95)),3),
    }


def get_pitcher_residual_adjustment(pitcher):
    hist = _history_before_today()
    if hist.empty:
        return {"adjustment":0.0,"sample":0,"weighted_residual":0.0,"status":"No V15 pitcher history"}
    target = normalize_name_for_match(pitcher)
    rows = hist[hist["Pitcher"].astype(str).apply(normalize_name_for_match)==target].sort_values("_date",ascending=False).head(5)
    residuals = _residual_series(rows)
    if len(residuals) < 3:
        return {"adjustment":0.0,"sample":len(residuals),"weighted_residual":round(sum(residuals)/len(residuals),3) if residuals else 0.0,"status":"Shadow only until 3 V15 starts"}
    weights = [0.40,0.25,0.18,0.10,0.07][:len(residuals)]
    weighted = sum(r*w for r,w in zip(residuals,weights))/sum(weights)
    adj = max(-0.35,min(0.35,weighted*0.20))
    return {"adjustment":round(adj,3),"sample":len(residuals),"weighted_residual":round(weighted,3),"status":f"{len(residuals)} V15 starts; 20% shrink, ±0.35 cap"}


def get_opponent_residual_adjustment(opponent):
    hist = _history_before_today()
    if hist.empty:
        return {"adjustment":0.0,"sample":0,"mean_residual":0.0,"status":"No V15 opponent history"}
    aliases = _normalized_team_aliases_for_match(opponent)
    rows = hist[hist["Opponent"].astype(str).apply(lambda x: bool(_normalized_team_aliases_for_match(x).intersection(aliases)))].sort_values("_date",ascending=False).head(80)
    residuals = _residual_series(rows); n=len(residuals)
    if n < 15:
        return {"adjustment":0.0,"sample":n,"mean_residual":round(sum(residuals)/n,3) if n else 0.0,"status":"Shadow only until 15 V15 opponent matchups"}
    mean=sum(residuals)/n; shrink=n/(n+30.0); adj=max(-0.25,min(0.25,mean*shrink*0.30))
    return {"adjustment":round(adj,3),"sample":n,"mean_residual":round(mean,3),"status":f"{n} V15 matchups; shrunk ±0.25 cap"}


_v14_build_projection_data_health = build_projection_data_health


def build_projection_data_health(lineup_details, arsenal_details, auto_weather=None, bulk_context=None):
    base = _v14_build_projection_data_health(lineup_details, arsenal_details, auto_weather, bulk_context)
    issues = list(base.get("issues", []))
    score = _k15_float(base.get("score"), 100.0)
    arsenal = arsenal_details or {}
    workload = arsenal.get("workload", {}) or {}
    discipline = arsenal.get("opponent_discipline", {}) or {}
    recent = arsenal.get("recent_pitch_profile", {}) or {}
    discipline_availability = str(discipline.get("availability", "")).lower()
    discipline_reason = str(discipline.get("fallback_reason", "") or "").strip()
    if discipline_availability == "partial":
        issue = "Opponent discipline is using a partial MLB fallback"
        if discipline_reason:
            issue += f" ({discipline_reason})"
        issues.append(issue)
        score -= 3
    elif discipline_availability != "full" or not discipline.get("available"):
        issue = "Opponent Savant plate-discipline profile unavailable"
        if discipline_reason:
            issue += f" ({discipline_reason})"
        issues.append(issue)
        score -= 6
    if not recent.get("available"):
        issues.append("Recent pitch shape/usage trend unavailable")
        score -= 5
    if _k15_float(arsenal.get("coverage"), 0.0) < 65:
        issues.append("Arsenal matchup coverage below 65%")
        score -= 5
    if _k15_float(workload.get("projected_bf"), 0.0) <= 0:
        issues.append("Opportunity engine BF projection unavailable")
        score -= 12
    shape_change = _k15_float(recent.get("shape_change_inches"), 0.0)
    release_change = _k15_float(recent.get("release_change_inches"), 0.0)
    if shape_change >= 1.75 or release_change >= 1.25:
        triggered = []
        if shape_change >= 1.75:
            triggered.append(f"shape {shape_change:.2f} in (threshold 1.75)")
        if release_change >= 1.25:
            triggered.append(f"release {release_change:.2f} in (threshold 1.25)")
        issues.append("Large recent pitch-profile change increases uncertainty: " + "; ".join(triggered))
        score -= 5
    base.update({
        "score": round(max(35.0,min(100.0,score)),1), "issues": list(dict.fromkeys(issues)),
        "status": "All V15 primary sources healthy" if not issues else " | ".join(list(dict.fromkeys(issues))),
        "projection_std": _k15_float(arsenal.get("structural_std"),1.95),
    })
    return base


def pitcher_projection_reliability(pitcher, volatility, lineup_details, calibration, recent_form, role="Starter", bulk_confidence="", data_health=None):
    score = 86.0; reasons=[]
    if str(volatility)=="Medium volatility": score-=6; reasons.append("medium workload volatility")
    elif str(volatility)=="High volatility": score-=13; reasons.append("high workload volatility")
    hitters=int((lineup_details or {}).get("hitters_found",0) or 0)
    if hitters<7: score-=8; reasons.append(f"only {hitters} confirmed lineup K samples")
    if "confirmed" not in str((lineup_details or {}).get("source","")).lower(): score-=4; reasons.append("lineup not confirmed")
    if int((calibration or {}).get("sample",0) or 0)<60: score-=3; reasons.append("V15 calibration sample building")
    recent_starts=int((recent_form or {}).get("starts",0) or 0)
    if recent_starts<2: score-=3; reasons.append("limited pitcher result history")
    if str((recent_form or {}).get("consistency","")).upper()=="VOLATILE": score-=5; reasons.append("volatile result residuals")
    role_upper=str(role or "Starter").upper()
    if role_upper=="OPENER": score-=15; reasons.append("opener workload uncertainty")
    elif role_upper=="BULK":
        score-=7; reasons.append("bulk workload uncertainty")
        if str(bulk_confidence).upper() not in ["CONFIRMED","HIGH"]: score-=6; reasons.append("bulk role not confirmed")
    for issue in (data_health or []):
        score-=2.5; reasons.append(str(issue))
    return {"score":round(max(35.0,min(95.0,score)),1),"reasons":reasons}


def calibrate_pitcher_projection(raw_projection, pitcher, opponent, volatility, lineup_details, recent_form=None, role="Starter", bulk_confidence="", archetype="", data_health=None):
    raw=max(0.0,float(raw_projection or 0.0)); global_fit=get_global_k_calibration()
    global_projection=float(global_fit["intercept"])+float(global_fit["slope"])*raw
    pitcher_adj=get_pitcher_residual_adjustment(pitcher); opponent_adj=get_opponent_residual_adjustment(opponent)
    final=max(0.0,global_projection+pitcher_adj["adjustment"]+opponent_adj["adjustment"])
    recent=recent_form or get_pitcher_recent_form_summary(pitcher)
    structural=_k15_float((data_health or {}).get("projection_std"),1.95)
    empirical=_k15_float(global_fit.get("error_std"),1.95)
    recent_error=_safe_float_or_none((recent or {}).get("error_std"))
    expected_std=(0.58*structural)+(0.42*empirical)
    if recent_error is not None and int((recent or {}).get("starts",0) or 0)>=3:
        expected_std=(0.48*expected_std)+(0.52*recent_error)
    expected_std=max(1.30,min(3.20,expected_std))
    reliability=pitcher_projection_reliability(
        pitcher,volatility,lineup_details,global_fit,recent,role=role,
        bulk_confidence=bulk_confidence,data_health=(data_health or {}).get("issues",[])
    )
    archetype_shadow=get_archetype_residual_shadow(archetype)
    shadow=max(0.0,global_projection+max(-0.55,min(0.55,float(pitcher_adj.get("weighted_residual",0))*0.30))+opponent_adj["adjustment"]+archetype_shadow["adjustment"])
    return {
        "raw_projection":round(raw,3),"global_projection":round(global_projection,3),
        "pitcher_adjustment":round(float(pitcher_adj["adjustment"]),3),
        "opponent_adjustment":round(float(opponent_adj["adjustment"]),3),
        "final_projection":round(final,3),"shadow_projection":round(shadow,3),
        "expected_std":round(expected_std,3),"reliability":reliability,
        "global_fit":global_fit,"pitcher_history":pitcher_adj,"opponent_history":opponent_adj,
        "archetype_shadow":archetype_shadow,"data_health":data_health or {},
        "status":f"V15 raw {raw:.2f} → calibration {global_projection:.2f} → pitcher {pitcher_adj['adjustment']:+.2f} → opponent {opponent_adj['adjustment']:+.2f} = {final:.2f}; SD {expected_std:.2f}",
    }


# ---------------------------------------------------------------------------
# Asymmetric over/under selection rules from the post-June-14 audit
# ---------------------------------------------------------------------------
def strikeout_bet_grade(exp_k, six_k, ipg_this, ipg_last, line, volatility, odds=-110, reliability=None, expected_std=None):
    try:
        exp_k=float(exp_k); six_k=float(six_k); line=float(line)
        reliability=float(reliability if reliability is not None else 55.0)
        expected_std=float(expected_std if expected_std is not None else 2.0)
    except Exception:
        return "PASS",0.0
    probs=k_market_probabilities(exp_k,line,expected_std); implied=american_odds_to_implied_prob(odds)
    over_cushion=exp_k-line; under_cushion=line-exp_k
    over_price=probs["over"]-implied; under_price=probs["under"]-implied
    six_over=six_k>=line+0.25; six_under=six_k<=line-0.35
    if over_cushion>=1.75 and probs["over"]>=0.70 and reliability>=72 and over_price>=0.04 and six_over:
        return "STRONG OVER",over_cushion
    if over_cushion>=1.25 and probs["over"]>=0.65 and reliability>=67 and over_price>=0.025 and six_over:
        return "OVER",over_cushion
    if over_cushion>=0.85 and probs["over"]>=0.60 and reliability>=62 and over_price>=0.015 and six_over:
        return "LEAN OVER",over_cushion
    high_line_penalty=0.20 if line>=6.5 else 0.0
    if under_cushion>=1.75+high_line_penalty and probs["under"]>=0.73 and reliability>=74 and under_price>=0.04 and six_under:
        return "STRONG UNDER",-under_cushion
    if under_cushion>=1.35+high_line_penalty and probs["under"]>=0.68 and reliability>=69 and under_price>=0.025 and six_under:
        return "UNDER",-under_cushion
    if under_cushion>=1.00+high_line_penalty and probs["under"]>=0.63 and reliability>=64 and under_price>=0.015 and six_under:
        return "LEAN UNDER",-under_cushion
    return "PASS",0.0


def apply_weapon_floor_to_k_grade(grade, arsenal_details):
    """V15 under-confluence gate; replaces the old weapon-count hard floor."""
    original=str(grade or "PASS").upper().strip()
    if original not in ["LEAN UNDER","UNDER","STRONG UNDER"]:
        return grade,""
    details=arsenal_details or {}
    support=int(details.get("under_support_count",0) or 0)
    notes=list(details.get("under_support_notes",[]) or [])
    lineup_mult=_k15_float(details.get("lineup_rate_multiplier"),1.0)
    if lineup_mult<=0.985 and "confirmed lineup suppresses K rate" not in notes:
        support+=1; notes.append("confirmed lineup suppresses K rate")
    weapons=int(details.get("weapon_count",0) or 0)
    arsenal_mult=_k15_float((details.get("rate_multipliers",{}) or {}).get("arsenal",details.get("k_rate_multiplier",1.0)),1.0)
    required=4 if original=="STRONG UNDER" else 3
    if weapons>=2 or arsenal_mult>1.02:
        required+=1
    if support<required:
        return "PASS",(
            f"V15 under gate: only {support}/{required} supporting factors "
            f"({'; '.join(notes) if notes else 'none'}). {original} → PASS."
        )
    return grade,f"V15 under confluence passed: {support}/{required} supports ({'; '.join(notes)})."


# ---------------------------------------------------------------------------
# Expanded feature snapshots and postgame error decomposition
# ---------------------------------------------------------------------------
_v14_record_pitcher_recent_form_start = record_pitcher_recent_form_start


def record_pitcher_recent_form_start(game_date, game_key, pitcher, team, opponent, projection, line, grade, recent_note="", metadata=None):
    metadata=metadata or {}
    ok=_v14_record_pitcher_recent_form_start(game_date,game_key,pitcher,team,opponent,projection,line,grade,recent_note,metadata)
    if not ok:
        return ok
    try:
        df=load_pitcher_recent_form()
        for col in RECENT_FORM_COLUMNS:
            if col not in df.columns: df[col]=""
        mask=(df["Date"].astype(str)==str(game_date)) & (df["Pitcher"].astype(str).apply(normalize_name_for_match)==normalize_name_for_match(pitcher))
        role=str(metadata.get("role","Starter") or "Starter")
        if "Role" in df.columns: mask=mask & (df["Role"].astype(str).str.upper()==role.upper())
        if str(game_key): mask=mask & (df["Game Key"].astype(str)==str(game_key))
        indexes=df.index[mask].tolist()
        if not indexes: return ok
        idx=indexes[-1]
        mapping={
            "Projection Architecture":"projection_architecture", "Projected Pitches Per BF":"projected_pitches_per_bf",
            "Opponent Pitches Per PA":"opponent_pitches_per_pa", "Pitcher Pitches Per BF":"pitcher_pitches_per_bf",
            "Third Time Through Probability":"third_time_probability", "Base K Rate":"base_k_rate",
            "Team Split K Rate":"team_split_k_rate", "Lineup K Rate Snapshot":"lineup_k_rate",
            "Matchup K Rate":"matchup_k_rate", "Arsenal K Rate Multiplier":"arsenal_rate_multiplier",
            "Lineup K Rate Multiplier":"lineup_rate_multiplier", "Skill K Rate Multiplier":"skill_rate_multiplier",
            "Opponent Discipline Multiplier":"opponent_discipline_multiplier", "Recent Pitch Trend Multiplier":"recent_pitch_multiplier",
            "Pitcher CSW %":"pitcher_csw_pct", "Pitcher Called Strike %":"pitcher_called_strike_pct",
            "Pitcher Chase %":"pitcher_chase_pct", "Pitcher Zone Contact %":"pitcher_zone_contact_pct",
            "Pitcher Chase Contact %":"pitcher_chase_contact_pct", "Pitcher First Strike %":"pitcher_first_strike_pct",
            "Opponent Whiff %":"opponent_whiff_pct", "Opponent Zone Contact %":"opponent_zone_contact_pct",
            "Opponent Chase Contact %":"opponent_chase_contact_pct", "Recent Velocity Delta":"recent_velocity_delta",
            "Recent CSW Delta":"recent_csw_delta", "Recent Whiff Delta":"recent_whiff_delta",
            "Recent Usage Quality Shift":"recent_usage_quality_shift", "Recent Shape Change":"recent_shape_change",
            "Recent Release Change":"recent_release_change", "Under Support Count":"under_support_count",
            "Under Support Notes":"under_support_notes", "Projection Structural Std":"projection_structural_std",
        }
        for column,key in mapping.items():
            value=metadata.get(key,"")
            if isinstance(value,(list,tuple,set)): value=" | ".join(str(x) for x in value)
            df.at[idx,column]=value
        save_pitcher_recent_form(df)
    except Exception:
        pass
    return ok


_v14_update_pitcher_recent_form_actuals = update_pitcher_recent_form_actuals


def update_pitcher_recent_form_actuals(auto_only=True):
    result=_v14_update_pitcher_recent_form_actuals(auto_only=auto_only)
    try:
        df=load_pitcher_recent_form()
        changed=False
        for col in RECENT_FORM_COLUMNS:
            if col not in df.columns: df[col]=""; changed=True
        for idx,row in df.iterrows():
            actual_pitches=_safe_float_or_none(row.get("Actual Pitches")); actual_bf=_safe_float_or_none(row.get("Actual Batters Faced"))
            projected_pitches=_safe_float_or_none(row.get("Projected Pitches")); projected_ppbf=_safe_float_or_none(row.get("Projected Pitches Per BF"))
            if actual_pitches is None or actual_bf is None or actual_bf<=0: continue
            actual_ppbf=actual_pitches/actual_bf
            df.at[idx,"Actual Pitches Per BF"]=round(actual_ppbf,3)
            if projected_pitches is not None: df.at[idx,"Pitch Count Error"]=round(actual_pitches-projected_pitches,1)
            if projected_ppbf is not None: df.at[idx,"Pitches Per BF Error"]=round(actual_ppbf-projected_ppbf,3)
            changed=True
        if changed: save_pitcher_recent_form(df)
    except Exception:
        pass
    return result


# Elite-only YRFI gate. Each game is judged independently; there is no daily quota.
_v13_nrfi_yrfi_grade_from_environment = nrfi_yrfi_grade_from_environment

def nrfi_yrfi_grade_from_environment(nrfi_prob, total_run_details=None, nrfi_odds=-110, yrfi_odds=-110, nrfi_details=None):
    result = dict(_v13_nrfi_yrfi_grade_from_environment(nrfi_prob, total_run_details, nrfi_odds, yrfi_odds, nrfi_details))
    if result.get("grade") == "YRFI":
        top_half = float(result.get("top_half_run_probability", 0) or 0)
        bottom_half = float(result.get("bottom_half_run_probability", 0) or 0)
        lower_half = min(top_half, bottom_half)
        elite_yrfi = (
            float(result.get("yrfi_score", 0) or 0) >= 88
            and float(result.get("yrfi_probability", 0) or 0) >= 0.60
            and float(result.get("projected_total", 0) or 0) >= 9.25
            and float(result.get("run_environment_score", 0) or 0) >= 66
            and float(result.get("yrfi_edge", 0) or 0) >= 0.070
            and float(result.get("yrfi_ev", 0) or 0) >= 0.070
            and max(top_half, bottom_half) >= 0.36
            and lower_half >= 0.24
        )
        if elite_yrfi:
            result["grade"] = "ELITE YRFI"
            result["selected_odds"] = yrfi_odds
        else:
            result["grade"] = "PASS"
            result["selected_odds"] = ""
    result["status"] = (
        "V14.1: Only Elite YRFI qualifies. It requires YRFI score 88+, calibrated probability 60%+, "
        "projected total 9.25+, run environment 66+, at least 7% price edge and 0.07u EV, "
        "with a 36%+ primary half-inning scoring path and at least 24% support from the other half. "
        "There is no daily top-two cap."
    )
    result["model_version"] = MODEL_VERSION
    return result


# -----------------------
# LIVE DATA DEFAULT MODE
# -----------------------

st.sidebar.header("Data Source")
st.sidebar.success("Live MLB/Savant model data mode is active. No Excel upload is required.")

if st.sidebar.button("Refresh Live Model Data", key="refresh_live_model_data_mobile"):
    try:
        load_all_live_data.clear()
    except Exception:
        pass
    st.cache_data.clear()
    st.rerun()

try:
    live_data = load_all_live_data()
except Exception as e:
    st.error(f"Could not load live model data: {e}")
    live_data = {}

team_hitting = live_data.get("team_hitting", pd.DataFrame())
team_batting_rhp = live_data.get("team_batting_rhp", pd.DataFrame())
team_batting_lhp = live_data.get("team_batting_lhp", pd.DataFrame())
pitcher_this_year = live_data.get("pitcher_this_year", pd.DataFrame())
pitcher_last_year = live_data.get("pitcher_last_year", pd.DataFrame())
nrfi_pitchers = live_data.get("nrfi_pitchers", pd.DataFrame())
nrfi_rhp = live_data.get("nrfi_rhp", pd.DataFrame())
nrfi_lhp = live_data.get("nrfi_lhp", pd.DataFrame())
pitcher_arsenal_df = live_data.get("pitcher_arsenal_df", pd.DataFrame())
team_pitch_type_df = live_data.get("team_pitch_type_df", pd.DataFrame())
bullpen_stats = live_data.get("bullpen_stats", pd.DataFrame())
bullpen_fatigue_df = live_data.get("bullpen_fatigue_df", pd.DataFrame())

_render_savant_contact_diagnostics(pitcher_this_year)

core_required = {
    "Team Hitting Stats": team_hitting,
    "Team Batting RHP": team_batting_rhp,
    "Team Batting LHP": team_batting_lhp,
    "Pitcher Data This Year": pitcher_this_year,
    "Pitcher Data Last Year": pitcher_last_year,
}
missing = [name for name, df in core_required.items() if df is None or getattr(df, "empty", True)]

if missing:
    st.warning("Some core live model data did not load: " + ", ".join(missing))
    st.info("Use the sidebar Refresh Live Model Data button. Optional NRFI/bullpen/pitch-type tables use fallbacks when unavailable.")

admin_page = st.radio(
    "Admin section",
    ["Home", "Build", "Odds", "Slate", "Bets", "Matchup Details", "Data"],
    horizontal=True,
    key="admin_main_nav"
)

if admin_page == "Home":
    render_admin_home(live_ready=not missing)

elif admin_page == "Build":
    if missing:
        st.error("Missing required live model data: " + ", ".join(missing))
        st.info("Refresh live model data from the sidebar, then try Build again.")
    else:
        render_auto_matchup_builder(
            pitcher_this_year,
            pitcher_last_year,
            team_hitting,
            team_batting_rhp,
            team_batting_lhp,
            nrfi_pitchers,
            nrfi_rhp,
            nrfi_lhp,
            pitcher_arsenal_df=pitcher_arsenal_df,
            team_pitch_type_df=team_pitch_type_df,
            bullpen_stats=bullpen_stats,
            bullpen_fatigue_df=bullpen_fatigue_df,
        )

elif admin_page == "Odds":
    render_odds_snapshot_admin()

elif admin_page == "Slate":
    render_daily_slate()

elif admin_page == "Bets":
    render_bet_tracker()

elif admin_page == "Matchup Details":
    render_matchup_details()

else:
    st.header("Live Data Preview")
    tables = {
        "Team Hitting Stats": team_hitting,
        "Team Batting RHP": team_batting_rhp,
        "Team Batting LHP": team_batting_lhp,
        "Pitcher Data This Year": pitcher_this_year,
        "Pitcher Data Last Year": pitcher_last_year,
        "NRFI Pitchers": nrfi_pitchers,
        "NRFI RHP": nrfi_rhp,
        "NRFI LHP": nrfi_lhp,
        "Pitcher Arsenal": pitcher_arsenal_df,
        "Team Pitch-Type Hitting": team_pitch_type_df,
        "Bullpen Stats": bullpen_stats,
        "Bullpen Fatigue": bullpen_fatigue_df,
    }
    choice = st.selectbox("Choose a live table to preview", list(tables.keys()))
    df = tables.get(choice, pd.DataFrame())
    if df is None or df.empty:
        st.info(f"{choice} is empty or unavailable. Optional tables may use neutral fallbacks.")
    else:
        st.dataframe(df.head(250), use_container_width=True)
