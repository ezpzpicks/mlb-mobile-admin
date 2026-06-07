import streamlit as st
import pandas as pd
import math
import os
import requests
import json
import html
import gspread
from google.oauth2.service_account import Credentials
from datetime import date, datetime
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

LOGO_FILE = "ezpz_logo.png"
PAGE_ICON = LOGO_FILE if os.path.exists(LOGO_FILE) else None
st.set_page_config(page_title="MLB Mobile Admin", layout="centered", page_icon=PAGE_ICON)

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

    if os.path.exists(LOGO_FILE):
        st.image(LOGO_FILE, width=160)
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

if os.path.exists(LOGO_FILE):
    st.image(LOGO_FILE, width=170)

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


TRACKER_TAB = "bet_tracker"
SLATE_TAB = "daily_slate"
ODDS_SNAPSHOT_TAB = "odds_snapshot"
MATCHUP_DETAILS_TAB = "matchup_details_today"

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
    "Model %", "Implied %", "Edge %", "Result",
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
        "Result": "Pending",
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
    "Away Pitcher K + Grade",
    "Away Pitcher K Score",
    "Home Pitcher K + Grade",
    "Home Pitcher K Score",
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
    game_id="",
    game_label="",
    slate_date=None
):
    df = load_slate()
    save_date = str(slate_date or date.today())
    game_id_text = str(game_id).strip()

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
        "Away Pitcher K + Grade": away_pitcher_k_grade,
        "Away Pitcher K Score": away_pitcher_k_score,
        "Home Pitcher K + Grade": home_pitcher_k_grade,
        "Home Pitcher K Score": home_pitcher_k_score
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


def _safe_json_value(value, max_rows=30):
    """Convert Streamlit/pandas objects into Google-Sheets-safe JSON."""
    try:
        if isinstance(value, pd.DataFrame):
            if value.empty:
                return []
            return value.head(max_rows).fillna("").astype(str).to_dict("records")
    except Exception:
        pass

    if isinstance(value, dict):
        return {str(k): _safe_json_value(v, max_rows=max_rows) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_safe_json_value(v, max_rows=max_rows) for v in list(value)]

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    try:
        if isinstance(value, (int, float, str, bool)) or value is None:
            return value
    except Exception:
        pass

    return str(value)


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

    safe_details = _safe_json_value(details)
    details_json = json.dumps(safe_details, ensure_ascii=False)

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


def render_matchup_details():
    st.header("Saved Matchup Details")
    st.caption("Temporary same-day matchup snapshots. Old dates auto-clear the next time you save a matchup.")

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
            _detail_metric("Volatility", pdata.get("volatility", ""))
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
    bb = _to_number(row.get("Walks", row.get("Base on Balls", row.get("Batted Balls", 0))), 0)

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

def expected_strikeouts(pitcher, opponent, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df=None, team_pitch_type_df=None, return_details=False):
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

    # Opponent Leash: adjust projected innings based on the opponent's league-relative
    # ability to extend or shorten starts. This is separate from opponent K-rate and
    # is applied before final K projection.
    base_exp_ip = exp_ip
    opponent_leash = get_opponent_leash_details(opponent, base_exp_ip, k_per_bf, team_batting_rhp, team_batting_lhp)
    exp_ip = float(opponent_leash.get("adjusted_ip", base_exp_ip) or base_exp_ip)

    bf_per_start = exp_ip * 4.3
    # Keep the pitcher's own workload/leash multiplier based on his baseline IP,
    # not the opponent-adjusted IP, so the opponent boost is not double-counted.
    base_bf_per_start = base_exp_ip * 4.3
    leash_mult = min(1.08, max(0.92, base_bf_per_start / 24))

    base_projection = k_per_bf * bf_per_start * opp_mult * leash_mult * 0.96
    adjusted_projection, arsenal_details = apply_pitch_type_modifier(base_projection, pitcher, opponent, pitcher_arsenal_df, team_pitch_type_df)
    arsenal_details["opponent_leash"] = opponent_leash
    if return_details:
        arsenal_details["base_projection"] = round(base_projection, 2)
        arsenal_details["adjusted_projection"] = round(adjusted_projection, 2)
        return adjusted_projection, arsenal_details
    return adjusted_projection


def six_inning_strikeouts(pitcher, opponent, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df=None, team_pitch_type_df=None, return_details=False, game_location="neutral", umpire_context=None):
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

    base_projection = k_per_bf * (6 * 4.3) * opp_mult * 0.96
    # Use a smaller impact on normalized 6-IP projection so the modifier mostly changes
    # real expected K while preserving agreement/volatility behavior.
    arsenal_projection, arsenal_details = apply_pitch_type_modifier(base_projection, pitcher, opponent, pitcher_arsenal_df, team_pitch_type_df)
    arsenal_projection = base_projection + ((arsenal_projection - base_projection) * 0.60)
    adjusted_projection, modifier_details = apply_small_k_modifiers(
        arsenal_projection,
        pitcher,
        opponent,
        pitcher_this_year,
        pitcher_last_year,
        team_batting_rhp,
        team_batting_lhp,
        throws_hand=throws_hand,
        game_location=game_location,
        umpire_context=umpire_context,
        impact_scale=0.60,
    )
    adjusted_projection = max(0, adjusted_projection)
    if return_details:
        arsenal_details["base_projection"] = round(base_projection, 2)
        arsenal_details["arsenal_projection"] = round(arsenal_projection, 2)
        arsenal_details["adjusted_projection"] = round(adjusted_projection, 2)
        arsenal_details["small_modifiers"] = modifier_details
        return adjusted_projection, arsenal_details
    return adjusted_projection


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

    if over_edge >= strong_over_req and total_over >= 6:
        return "STRONG OVER", over_edge
    if over_edge >= over_req and total_over >= 5:
        return "OVER", over_edge
    if over_edge >= over_lean_req and total_over >= 3:
        return "LEAN OVER", over_edge

    if under_edge >= strong_under_req and total_under >= 6:
        return "STRONG UNDER", -under_edge
    if under_edge >= under_req and total_under >= 4:
        return "UNDER", -under_edge
    if under_edge >= under_lean_req and total_under >= 2:
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

    This is used only when the matchup checkbox is enabled. NRFI and pitcher K
    projections still use the listed probable pitchers. The helper accepts many
    possible column names so it works with either your Excel bullpen sheet or a
    live table if those columns are available.
    """
    if bullpen_stats is None or bullpen_stats.empty:
        return None

    era = _get_bullpen_numeric(bullpen_stats, team, ["ERA", "era", "Bullpen ERA"], None)
    xwoba = _get_bullpen_numeric(bullpen_stats, team, ["xwOBA", "xwoba", "Bullpen xwOBA"], None)
    k_pct = _get_bullpen_numeric(bullpen_stats, team, ["K%", "SO%", "Strikeout %", "Bullpen K%"], None)
    bb_pct = _get_bullpen_numeric(bullpen_stats, team, ["BB%", "Walk %", "Bullpen BB%"], None)
    whip = _get_bullpen_numeric(bullpen_stats, team, ["WHIP", "Bullpen WHIP"], None)

    score = 0.0
    used = 0

    if xwoba is not None and xwoba > 0:
        score += (0.320 - xwoba) * 280
        used += 1
    if era is not None and era > 0:
        score += (4.10 - era) * 4.5
        used += 1
    if k_pct is not None and k_pct > 0:
        # clean_percent() turns 24.5 into .245 but leaves .245 as-is.
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

    if used == 0:
        return None
    return score


def moneyline_probability(home, away, hp, ap, pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, bullpen_stats=None, use_home_bullpen=False, use_away_bullpen=False, bullpen_fatigue_df=None):
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

    H_starter_pitch = ((0.32 - H_xwoba) * 280 + H_k_rate * 11 - H_bb_rate * 7)
    A_starter_pitch = ((0.32 - A_xwoba) * 280 + A_k_rate * 11 - A_bb_rate * 7)

    H_bullpen_pitch = _bullpen_pitch_score(home, bullpen_stats) if use_home_bullpen else None
    A_bullpen_pitch = _bullpen_pitch_score(away, bullpen_stats) if use_away_bullpen else None

    # Checkbox behavior: only the selected team's moneyline pitching profile
    # switches to bullpen data. NRFI and pitcher props never call this override.
    H_pitch = H_bullpen_pitch if H_bullpen_pitch is not None else H_starter_pitch
    A_pitch = A_bullpen_pitch if A_bullpen_pitch is not None else A_starter_pitch

    # Bullpen fatigue is a team pitching-context layer for moneylines.
    # It is especially relevant when bullpen override is checked, but still
    # modestly applies to normal games because late-inning availability matters.
    H_pitch += _bullpen_fatigue_adjustment(home, bullpen_fatigue_df)
    A_pitch += _bullpen_fatigue_adjustment(away, bullpen_fatigue_df)

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
    cols = ["Game Label", "Away Team", "Home Team", "Better ML", "NRFI Grade", "Away Pitcher K + Grade", "Home Pitcher K + Grade"]
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
        if row["NRFI Grade"] in ["ELITE NRFI", "STRONG NRFI", "NRFI", "YRFI"]:
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

    best_df = pd.DataFrame(best_rows)
    return best_df.sort_values(by="Score", ascending=False).reset_index(drop=True)


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
def load_pitch_arsenal_stats_live(year=2026, stat_type="pitcher"):
    """Load Baseball Savant pitch-arsenal stats split by pitch type.

    Pulls K%, Whiff%, and Put Away% from the same Savant arsenal table so the
    K-prop arsenal model can grade each pitch from outcome, swing-and-miss,
    and finishing ability instead of whiff alone.
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

    base_cols = ["Name", "Team", "Pitch Type", "Usage", "K", "Whiff", "Put Away", "Run Value", "Pitches", "Source Type"]
    if df is None or df.empty:
        return pd.DataFrame(columns=base_cols)

    df = df.copy()
    df.columns = [_clean_col_name(c) for c in df.columns]
    if "Rk." in df.columns:
        df = df.drop(columns=["Rk."])

    name_col = _find_col(df, ["Player", "Name", "player_name", "last_name, first_name", "Pitcher", "Batter"])
    team_col = _find_col(df, ["Team", "Tm", "team_name", "team"])
    pitch_col = _find_col(df, ["Pitch Type", "Pitch", "pitch_name", "pitch_type"])
    usage_col = _find_col(df, ["Usage", "Usage %", "Pitch %", "Pitches %", "pitch_usage", "%"])
    k_col = _find_col(df, ["k_percent", "K %", "K%", "Strikeout %", "SO %", "SO%"])
    whiff_col = _find_col(df, ["whiff_percent", "Whiff %", "Whiff"])
    putaway_col = _find_col(df, ["put_away", "put_away_percent", "Put Away %", "PutAway %", "Put Away%", "PutAway%", "Put Away", "PutAway"])
    run_value_col = _find_col(df, ["Run Value", "RV", "run_value", "Pitching Run Value", "Batting Run Value"])
    pitches_col = _find_col(df, ["Pitches", "Total Pitches", "pitch_count", "#"])

    out = pd.DataFrame()
    out["Name"] = df[name_col].astype(str).str.strip() if name_col else ""
    out["Team"] = df[team_col].astype(str).str.strip() if team_col else ""
    out["Pitch Type"] = df[pitch_col].apply(_canonical_pitch_type) if pitch_col else ""
    out["Usage"] = df[usage_col].apply(lambda x: _to_rate(x, 0.0)) if usage_col else 0.0
    out["K"] = df[k_col].apply(lambda x: _to_rate(x, 0.0)) if k_col else 0.0
    out["Whiff"] = df[whiff_col].apply(lambda x: _to_rate(x, 0.0)) if whiff_col else 0.0
    out["Put Away"] = df[putaway_col].apply(lambda x: _to_rate(x, 0.0)) if putaway_col else 0.0
    out["Run Value"] = df[run_value_col].apply(lambda x: _to_number(x, 0.0)) if run_value_col else 0.0
    out["Pitches"] = df[pitches_col].apply(lambda x: _to_number(x, 0.0)) if pitches_col else 0.0
    out["Source Type"] = stat_type

    if out["Usage"].max() == 0 and out["Pitches"].max() > 0:
        group_key = "Name" if out["Name"].astype(str).str.strip().ne("").any() else "Team"
        totals = out.groupby(group_key)["Pitches"].transform("sum").replace(0, pd.NA)
        out["Usage"] = (out["Pitches"] / totals).fillna(0)

    out = out[out["Pitch Type"].astype(str).str.strip() != ""].copy()
    for col in base_cols:
        if col not in out.columns:
            out[col] = "" if col in ["Name", "Team", "Pitch Type", "Source Type"] else 0.0
    return out[base_cols].reset_index(drop=True)

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

    Arsenal score now blends three Savant pitch-type edges:
    - K edge: 50% weight because pitcher strikeouts are the target outcome.
    - Whiff edge: 30% weight to confirm bat-missing quality.
    - Put Away edge: 20% weight to reward finishing ability in two-strike spots.
    Each edge is still league-relative: (pitcher - league) - (opponent - league).
    """
    pitcher_rows = _pitcher_arsenal_rows(pitcher, pitcher_arsenal_df)
    team_rows = _team_pitch_type_rows(opponent, team_pitch_type_df)

    empty_cols = [
        "Pitch", "Usage",
        "Pitcher K", "Opponent K", "League K", "K Edge",
        "Pitcher Whiff", "Opponent Whiff", "League Whiff", "Whiff Edge",
        "Pitcher Put Away", "Opponent Put Away", "League Put Away", "Put Away Edge",
        "Combined Edge", "Weapon", "Extreme", "Confluence",
        "Contribution", "Match Status"
    ]
    if pitcher_rows.empty or team_rows.empty:
        return {
            "modifier": 0.0,
            "score": 0.0,
            "weapon_count": 0,
            "weapon_bonus": 0.0,
            "status": "Neutral fallback - pitch type table not available/matched",
            "details": pd.DataFrame(columns=empty_cols),
        }

    if team_pitch_type_df is not None and not team_pitch_type_df.empty:
        league_pitch_metrics = {}
        for metric in ["K", "Whiff", "Put Away"]:
            if metric in team_pitch_type_df.columns:
                temp = team_pitch_type_df.copy()
                temp[metric] = temp[metric].apply(lambda x: _to_rate(x, 0.0))
                league_pitch_metrics[metric] = temp.groupby("Pitch Type")[metric].mean().to_dict()
            else:
                league_pitch_metrics[metric] = {}
    else:
        league_pitch_metrics = {"K": {}, "Whiff": {}, "Put Away": {}}

    team_lookup = team_rows.drop_duplicates("Pitch Type").set_index("Pitch Type").to_dict("index")

    details = []
    score = 0.0
    scored_usage = 0.0
    matched_usage = 0.0
    scored_count = 0
    weapon_count = 0
    weapon_usage = 0.0

    defaults = {"K": 0.22, "Whiff": 0.24, "Put Away": 0.20}
    edge_scales = {"K": 0.08, "Whiff": 0.08, "Put Away": 0.08}
    weights = {"K": 0.50, "Whiff": 0.30, "Put Away": 0.20}

    for _, p_row in pitcher_rows.iterrows():
        pitch = p_row.get("Pitch Type", "")
        usage = _to_rate(p_row.get("Usage", 0), 0.0)

        p_k = _to_rate(p_row.get("K", 0), 0.0)
        p_whiff = _to_rate(p_row.get("Whiff", 0), 0.0)
        p_putaway = _to_rate(p_row.get("Put Away", 0), 0.0)

        league_k = _to_rate(league_pitch_metrics.get("K", {}).get(pitch, defaults["K"]), defaults["K"])
        league_whiff = _to_rate(league_pitch_metrics.get("Whiff", {}).get(pitch, defaults["Whiff"]), defaults["Whiff"])
        league_putaway = _to_rate(league_pitch_metrics.get("Put Away", {}).get(pitch, defaults["Put Away"]), defaults["Put Away"])

        if usage < 0.01:
            continue

        p_k_delta = p_k - league_k
        p_whiff_delta = p_whiff - league_whiff
        p_putaway_delta = p_putaway - league_putaway

        if pitch not in team_lookup:
            combined_pitch_quality = (
                weights["K"] * max(-1.0, min(1.0, p_k_delta / edge_scales["K"])) +
                weights["Whiff"] * max(-1.0, min(1.0, p_whiff_delta / edge_scales["Whiff"])) +
                weights["Put Away"] * max(-1.0, min(1.0, p_putaway_delta / edge_scales["Put Away"]))
            )
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
                "Combined Edge": round(combined_pitch_quality, 2),
                "Weapon": "",
                "Extreme": "",
                "Confluence": "",
                "Contribution": "",
                "Match Status": "No opponent pitch-type match",
            })
            continue

        opp_row = team_lookup[pitch]
        opp_k = _to_rate(opp_row.get("K", 0), 0.0)
        opp_whiff = _to_rate(opp_row.get("Whiff", 0), 0.0)
        opp_putaway = _to_rate(opp_row.get("Put Away", 0), 0.0)
        matched_usage += usage

        opp_k_delta = opp_k - league_k
        opp_whiff_delta = opp_whiff - league_whiff
        opp_putaway_delta = opp_putaway - league_putaway

        raw_k_edge = p_k_delta + opp_k_delta
        raw_whiff_edge = p_whiff_delta + opp_whiff_delta
        raw_putaway_edge = p_putaway_delta + opp_putaway_delta

        k_edge = max(-1.0, min(1.0, raw_k_edge / edge_scales["K"]))
        whiff_edge = max(-1.0, min(1.0, raw_whiff_edge / edge_scales["Whiff"]))
        putaway_edge = max(-1.0, min(1.0, raw_putaway_edge / edge_scales["Put Away"]))

        combined_edge = (weights["K"] * k_edge) + (weights["Whiff"] * whiff_edge) + (weights["Put Away"] * putaway_edge)

        # Weapon = a pitch used enough to matter with above-average strikeout or whiff traits.
        is_weapon = usage >= 0.05 and (
            p_k_delta >= 0.04 or p_whiff_delta >= 0.04 or p_putaway_delta >= 0.04 or
            p_k >= 0.34 or p_whiff >= 0.38 or p_putaway >= 0.30
        )
        if is_weapon:
            weapon_count += 1
            weapon_usage += usage

        confluence = (
            (p_k_delta > 0 and opp_k_delta > 0) or
            (p_whiff_delta > 0 and opp_whiff_delta > 0) or
            (p_putaway_delta > 0 and opp_putaway_delta > 0) or
            (p_k_delta < 0 and opp_k_delta < 0 and p_whiff_delta < 0 and opp_whiff_delta < 0)
        )

        if usage < 0.05:
            details.append({
                "Pitch": pitch,
                "Usage": round(usage * 100, 1),
                "Pitcher K": round(p_k * 100, 1),
                "Opponent K": round(opp_k * 100, 1),
                "League K": round(league_k * 100, 1),
                "K Edge": round(raw_k_edge * 100, 1),
                "Pitcher Whiff": round(p_whiff * 100, 1),
                "Opponent Whiff": round(opp_whiff * 100, 1),
                "League Whiff": round(league_whiff * 100, 1),
                "Whiff Edge": round(raw_whiff_edge * 100, 1),
                "Pitcher Put Away": round(p_putaway * 100, 1),
                "Opponent Put Away": round(opp_putaway * 100, 1),
                "League Put Away": round(league_putaway * 100, 1),
                "Put Away Edge": round(raw_putaway_edge * 100, 1),
                "Combined Edge": round(combined_edge, 2),
                "Weapon": "Yes" if is_weapon else "No",
                "Extreme": "Weapon only - below scoring floor" if is_weapon else "",
                "Confluence": "Yes" if confluence else "No",
                "Contribution": "",
                "Match Status": "Matched - below 5% usage floor",
            })
            continue

        extreme_tags = []
        multiplier = 1.00

        if confluence:
            multiplier *= 1.25

        if p_k_delta >= 0.06 and usage >= 0.05:
            multiplier *= 1.25
            extreme_tags.append("Elite Pitcher K")
        if p_whiff_delta >= 0.07 and usage >= 0.05:
            multiplier *= 1.20
            extreme_tags.append("Elite Pitcher Whiff")
        if p_putaway_delta >= 0.06 and usage >= 0.05:
            multiplier *= 1.15
            extreme_tags.append("Elite Put Away")

        if opp_k_delta >= 0.04 and usage >= 0.05:
            multiplier *= 1.20
            extreme_tags.append("Opponent K Weakness")
        if opp_whiff_delta >= 0.04 and usage >= 0.05:
            multiplier *= 1.15
            extreme_tags.append("Opponent Whiff Weakness")
        if opp_putaway_delta >= 0.04 and usage >= 0.05:
            multiplier *= 1.10
            extreme_tags.append("Opponent Put Away Weakness")

        if raw_k_edge >= 0.08 and raw_whiff_edge >= 0.04 and usage >= 0.05:
            multiplier *= 1.30
            extreme_tags.append("Ceiling Confluence")

        if raw_k_edge <= -0.08 and raw_whiff_edge <= -0.04 and usage >= 0.07:
            multiplier *= 1.20
            extreme_tags.append("Negative Confluence")

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
            "K Edge": round(raw_k_edge * 100, 1),
            "Pitcher Whiff": round(p_whiff * 100, 1),
            "Opponent Whiff": round(opp_whiff * 100, 1),
            "League Whiff": round(league_whiff * 100, 1),
            "Whiff Edge": round(raw_whiff_edge * 100, 1),
            "Pitcher Put Away": round(p_putaway * 100, 1),
            "Opponent Put Away": round(opp_putaway * 100, 1),
            "League Put Away": round(league_putaway * 100, 1),
            "Put Away Edge": round(raw_putaway_edge * 100, 1),
            "Combined Edge": round(combined_edge, 2),
            "Weapon": "Yes" if is_weapon else "No",
            "Extreme": ", ".join(extreme_tags),
            "Confluence": "Yes" if confluence else "No",
            "Contribution": round(contribution, 3),
            "Match Status": "Matched and scored",
        })

    if not details or scored_usage <= 0:
        detail_df = pd.DataFrame(details, columns=empty_cols) if details else pd.DataFrame(columns=empty_cols)
        return {
            "modifier": 0.0,
            "score": 0.0,
            "weapon_count": weapon_count,
            "weapon_bonus": 0.0,
            "status": "Neutral fallback - no matching pitch types above usage floor",
            "details": detail_df,
        }

    normalized_score = max(-1.0, min(1.0, score / max(0.25, scored_usage)))

    if weapon_count >= 4:
        weapon_bonus = 1.05
    elif weapon_count == 3:
        weapon_bonus = 0.85
    elif weapon_count == 2:
        weapon_bonus = 0.55
    elif weapon_count == 1:
        weapon_bonus = 0.20
    else:
        weapon_bonus = 0.0

    if weapon_usage < 0.12:
        weapon_bonus *= 0.70
    elif weapon_usage < 0.20:
        weapon_bonus *= 0.90

    if weapon_count >= 2 and weapon_usage >= 0.25:
        weapon_bonus += 0.20
    if weapon_count >= 3 and weapon_usage >= 0.25:
        weapon_bonus += 0.25
    if weapon_count >= 3 and weapon_usage >= 0.35:
        weapon_bonus += 0.15

    base_modifier = max(-1.20, min(1.35, normalized_score * 1.40))

    negative_profile_penalty = 0.0
    if weapon_count == 0 and normalized_score <= -0.25:
        negative_profile_penalty -= 0.25
    if weapon_count == 0 and normalized_score <= -0.40:
        negative_profile_penalty -= 0.15

    modifier = max(-1.25, min(1.75, base_modifier + weapon_bonus + negative_profile_penalty))

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
        "status": f"Pitch-type arsenal matched - {scored_count} scored pitches, {coverage}% arsenal usage matched, {weapon_count} weapons; blend 50% K / 30% Whiff / 20% Put Away",
        "details": detail_df,
    }

def apply_pitch_type_modifier(base_projection, pitcher, opponent, pitcher_arsenal_df=None, team_pitch_type_df=None):
    adj = pitch_type_arsenal_adjustment(pitcher, opponent, pitcher_arsenal_df, team_pitch_type_df)
    return max(0, base_projection + adj["modifier"]), adj

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
def load_nrfi_team_split_live(hand):
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
def _mlb_all_player_pitching_stats(season=MLB_SEASON):
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
        return pd.DataFrame(columns=["Name", "Team", "Pitch Type", "Usage", "K", "Whiff", "Put Away", "Run Value", "Pitches", "Source Type"])

    df = df.copy()
    df.columns = [_clean_col_name(c) for c in df.columns]
    if "Rk." in df.columns:
        df = df.drop(columns=["Rk."])

    name_col = _find_col(df, ["Player", "Name", "player_name", "last_name, first_name", "Pitcher", "Batter"])
    team_col = _find_col(df, ["Team", "Tm", "team_name", "team"])
    pitch_col = _find_col(df, ["Pitch Type", "Pitch", "pitch_name", "pitch_type"])
    usage_col = _find_col(df, ["Usage", "Usage %", "Pitch %", "Pitches %", "pitch_usage", "%"])
    k_col = _find_col(df, ["k_percent", "K %", "K%", "Strikeout %", "SO %", "SO%"])
    whiff_col = _find_col(df, ["whiff_percent", "Whiff %", "Whiff"])
    putaway_col = _find_col(df, ["put_away", "put_away_percent", "Put Away %", "PutAway %", "Put Away%", "PutAway%", "Put Away", "PutAway"])
    run_value_col = _find_col(df, ["Run Value", "RV", "run_value", "Pitching Run Value", "Batting Run Value"])
    pitches_col = _find_col(df, ["Pitches", "Total Pitches", "pitch_count", "#"])

    out = pd.DataFrame()
    out["Name"] = df[name_col].astype(str).str.strip() if name_col else ""
    out["Team"] = df[team_col].astype(str).str.strip() if team_col else ""
    out["Pitch Type"] = df[pitch_col].apply(_canonical_pitch_type) if pitch_col else ""
    out["Usage"] = df[usage_col].apply(lambda x: _to_rate(x, 0.0)) if usage_col else 0.0
    out["K"] = df[k_col].apply(lambda x: _to_rate(x, 0.0)) if k_col else 0.0
    out["Whiff"] = df[whiff_col].apply(lambda x: _to_rate(x, 0.0)) if whiff_col else 0.0
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

    Arsenal score now blends three Savant pitch-type edges:
    - K edge: 50% weight because pitcher strikeouts are the target outcome.
    - Whiff edge: 30% weight to confirm bat-missing quality.
    - Put Away edge: 20% weight to reward finishing ability in two-strike spots.
    Each edge is still league-relative: (pitcher - league) - (opponent - league).
    """
    pitcher_rows = _pitcher_arsenal_rows(pitcher, pitcher_arsenal_df)
    team_rows = _team_pitch_type_rows(opponent, team_pitch_type_df)

    empty_cols = [
        "Pitch", "Usage",
        "Pitcher K", "Opponent K", "League K", "K Edge",
        "Pitcher Whiff", "Opponent Whiff", "League Whiff", "Whiff Edge",
        "Pitcher Put Away", "Opponent Put Away", "League Put Away", "Put Away Edge",
        "Combined Edge", "Weapon", "Extreme", "Confluence",
        "Contribution", "Match Status"
    ]
    if pitcher_rows.empty or team_rows.empty:
        return {
            "modifier": 0.0,
            "score": 0.0,
            "weapon_count": 0,
            "weapon_bonus": 0.0,
            "status": "Neutral fallback - pitch type table not available/matched",
            "details": pd.DataFrame(columns=empty_cols),
        }

    if team_pitch_type_df is not None and not team_pitch_type_df.empty:
        league_pitch_metrics = {}
        for metric in ["K", "Whiff", "Put Away"]:
            if metric in team_pitch_type_df.columns:
                temp = team_pitch_type_df.copy()
                temp[metric] = temp[metric].apply(lambda x: _to_rate(x, 0.0))
                league_pitch_metrics[metric] = temp.groupby("Pitch Type")[metric].mean().to_dict()
            else:
                league_pitch_metrics[metric] = {}
    else:
        league_pitch_metrics = {"K": {}, "Whiff": {}, "Put Away": {}}

    team_lookup = team_rows.drop_duplicates("Pitch Type").set_index("Pitch Type").to_dict("index")

    details = []
    score = 0.0
    scored_usage = 0.0
    matched_usage = 0.0
    scored_count = 0
    weapon_count = 0
    weapon_usage = 0.0

    defaults = {"K": 0.22, "Whiff": 0.24, "Put Away": 0.20}
    edge_scales = {"K": 0.08, "Whiff": 0.08, "Put Away": 0.08}
    weights = {"K": 0.50, "Whiff": 0.30, "Put Away": 0.20}

    for _, p_row in pitcher_rows.iterrows():
        pitch = p_row.get("Pitch Type", "")
        usage = _to_rate(p_row.get("Usage", 0), 0.0)

        p_k = _to_rate(p_row.get("K", 0), 0.0)
        p_whiff = _to_rate(p_row.get("Whiff", 0), 0.0)
        p_putaway = _to_rate(p_row.get("Put Away", 0), 0.0)

        league_k = _to_rate(league_pitch_metrics.get("K", {}).get(pitch, defaults["K"]), defaults["K"])
        league_whiff = _to_rate(league_pitch_metrics.get("Whiff", {}).get(pitch, defaults["Whiff"]), defaults["Whiff"])
        league_putaway = _to_rate(league_pitch_metrics.get("Put Away", {}).get(pitch, defaults["Put Away"]), defaults["Put Away"])

        if usage < 0.01:
            continue

        p_k_delta = p_k - league_k
        p_whiff_delta = p_whiff - league_whiff
        p_putaway_delta = p_putaway - league_putaway

        if pitch not in team_lookup:
            combined_pitch_quality = (
                weights["K"] * max(-1.0, min(1.0, p_k_delta / edge_scales["K"])) +
                weights["Whiff"] * max(-1.0, min(1.0, p_whiff_delta / edge_scales["Whiff"])) +
                weights["Put Away"] * max(-1.0, min(1.0, p_putaway_delta / edge_scales["Put Away"]))
            )
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
                "Combined Edge": round(combined_pitch_quality, 2),
                "Weapon": "",
                "Extreme": "",
                "Confluence": "",
                "Contribution": "",
                "Match Status": "No opponent pitch-type match",
            })
            continue

        opp_row = team_lookup[pitch]
        opp_k = _to_rate(opp_row.get("K", 0), 0.0)
        opp_whiff = _to_rate(opp_row.get("Whiff", 0), 0.0)
        opp_putaway = _to_rate(opp_row.get("Put Away", 0), 0.0)
        matched_usage += usage

        opp_k_delta = opp_k - league_k
        opp_whiff_delta = opp_whiff - league_whiff
        opp_putaway_delta = opp_putaway - league_putaway

        raw_k_edge = p_k_delta + opp_k_delta
        raw_whiff_edge = p_whiff_delta + opp_whiff_delta
        raw_putaway_edge = p_putaway_delta + opp_putaway_delta

        k_edge = max(-1.0, min(1.0, raw_k_edge / edge_scales["K"]))
        whiff_edge = max(-1.0, min(1.0, raw_whiff_edge / edge_scales["Whiff"]))
        putaway_edge = max(-1.0, min(1.0, raw_putaway_edge / edge_scales["Put Away"]))

        combined_edge = (weights["K"] * k_edge) + (weights["Whiff"] * whiff_edge) + (weights["Put Away"] * putaway_edge)

        # Weapon = a pitch used enough to matter with above-average strikeout or whiff traits.
        is_weapon = usage >= 0.05 and (
            p_k_delta >= 0.04 or p_whiff_delta >= 0.04 or p_putaway_delta >= 0.04 or
            p_k >= 0.34 or p_whiff >= 0.38 or p_putaway >= 0.30
        )
        if is_weapon:
            weapon_count += 1
            weapon_usage += usage

        confluence = (
            (p_k_delta > 0 and opp_k_delta > 0) or
            (p_whiff_delta > 0 and opp_whiff_delta > 0) or
            (p_putaway_delta > 0 and opp_putaway_delta > 0) or
            (p_k_delta < 0 and opp_k_delta < 0 and p_whiff_delta < 0 and opp_whiff_delta < 0)
        )

        if usage < 0.05:
            details.append({
                "Pitch": pitch,
                "Usage": round(usage * 100, 1),
                "Pitcher K": round(p_k * 100, 1),
                "Opponent K": round(opp_k * 100, 1),
                "League K": round(league_k * 100, 1),
                "K Edge": round(raw_k_edge * 100, 1),
                "Pitcher Whiff": round(p_whiff * 100, 1),
                "Opponent Whiff": round(opp_whiff * 100, 1),
                "League Whiff": round(league_whiff * 100, 1),
                "Whiff Edge": round(raw_whiff_edge * 100, 1),
                "Pitcher Put Away": round(p_putaway * 100, 1),
                "Opponent Put Away": round(opp_putaway * 100, 1),
                "League Put Away": round(league_putaway * 100, 1),
                "Put Away Edge": round(raw_putaway_edge * 100, 1),
                "Combined Edge": round(combined_edge, 2),
                "Weapon": "Yes" if is_weapon else "No",
                "Extreme": "Weapon only - below scoring floor" if is_weapon else "",
                "Confluence": "Yes" if confluence else "No",
                "Contribution": "",
                "Match Status": "Matched - below 5% usage floor",
            })
            continue

        extreme_tags = []
        multiplier = 1.00

        if confluence:
            multiplier *= 1.25

        if p_k_delta >= 0.06 and usage >= 0.05:
            multiplier *= 1.25
            extreme_tags.append("Elite Pitcher K")
        if p_whiff_delta >= 0.07 and usage >= 0.05:
            multiplier *= 1.20
            extreme_tags.append("Elite Pitcher Whiff")
        if p_putaway_delta >= 0.06 and usage >= 0.05:
            multiplier *= 1.15
            extreme_tags.append("Elite Put Away")

        if opp_k_delta >= 0.04 and usage >= 0.05:
            multiplier *= 1.20
            extreme_tags.append("Opponent K Weakness")
        if opp_whiff_delta >= 0.04 and usage >= 0.05:
            multiplier *= 1.15
            extreme_tags.append("Opponent Whiff Weakness")
        if opp_putaway_delta >= 0.04 and usage >= 0.05:
            multiplier *= 1.10
            extreme_tags.append("Opponent Put Away Weakness")

        if raw_k_edge >= 0.08 and raw_whiff_edge >= 0.04 and usage >= 0.05:
            multiplier *= 1.30
            extreme_tags.append("Ceiling Confluence")

        if raw_k_edge <= -0.08 and raw_whiff_edge <= -0.04 and usage >= 0.07:
            multiplier *= 1.20
            extreme_tags.append("Negative Confluence")

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
            "K Edge": round(raw_k_edge * 100, 1),
            "Pitcher Whiff": round(p_whiff * 100, 1),
            "Opponent Whiff": round(opp_whiff * 100, 1),
            "League Whiff": round(league_whiff * 100, 1),
            "Whiff Edge": round(raw_whiff_edge * 100, 1),
            "Pitcher Put Away": round(p_putaway * 100, 1),
            "Opponent Put Away": round(opp_putaway * 100, 1),
            "League Put Away": round(league_putaway * 100, 1),
            "Put Away Edge": round(raw_putaway_edge * 100, 1),
            "Combined Edge": round(combined_edge, 2),
            "Weapon": "Yes" if is_weapon else "No",
            "Extreme": ", ".join(extreme_tags),
            "Confluence": "Yes" if confluence else "No",
            "Contribution": round(contribution, 3),
            "Match Status": "Matched and scored",
        })

    if not details or scored_usage <= 0:
        detail_df = pd.DataFrame(details, columns=empty_cols) if details else pd.DataFrame(columns=empty_cols)
        return {
            "modifier": 0.0,
            "score": 0.0,
            "weapon_count": weapon_count,
            "weapon_bonus": 0.0,
            "status": "Neutral fallback - no matching pitch types above usage floor",
            "details": detail_df,
        }

    normalized_score = max(-1.0, min(1.0, score / max(0.25, scored_usage)))

    if weapon_count >= 4:
        weapon_bonus = 1.05
    elif weapon_count == 3:
        weapon_bonus = 0.85
    elif weapon_count == 2:
        weapon_bonus = 0.55
    elif weapon_count == 1:
        weapon_bonus = 0.20
    else:
        weapon_bonus = 0.0

    if weapon_usage < 0.12:
        weapon_bonus *= 0.70
    elif weapon_usage < 0.20:
        weapon_bonus *= 0.90

    if weapon_count >= 2 and weapon_usage >= 0.25:
        weapon_bonus += 0.20
    if weapon_count >= 3 and weapon_usage >= 0.25:
        weapon_bonus += 0.25
    if weapon_count >= 3 and weapon_usage >= 0.35:
        weapon_bonus += 0.15

    base_modifier = max(-1.20, min(1.35, normalized_score * 1.40))

    negative_profile_penalty = 0.0
    if weapon_count == 0 and normalized_score <= -0.25:
        negative_profile_penalty -= 0.25
    if weapon_count == 0 and normalized_score <= -0.40:
        negative_profile_penalty -= 0.15

    modifier = max(-1.25, min(1.75, base_modifier + weapon_bonus + negative_profile_penalty))

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
        "status": f"Pitch-type arsenal matched - {scored_count} scored pitches, {coverage}% arsenal usage matched, {weapon_count} weapons; blend 50% K / 30% Whiff / 20% Put Away",
        "details": detail_df,
    }

def apply_pitch_type_modifier(base_projection, pitcher, opponent, pitcher_arsenal_df=None, team_pitch_type_df=None):
    adj = pitch_type_arsenal_adjustment(pitcher, opponent, pitcher_arsenal_df, team_pitch_type_df)
    return max(0, base_projection + adj["modifier"]), adj


# -----------------------
# SMALL PITCHER K MODIFIERS
# -----------------------

SMALL_K_MODIFIER_CAP = 0.10  # percent-based non-arsenal movement is hard-capped at +/-10% to avoid over-heavy props
EXTREME_MATCHUP_K_BONUS_CAP = 0.50  # direct K movement for true extreme matchup buckets
TOTAL_SMALL_K_MOVEMENT_CAP = 0.70  # total non-arsenal K movement cap after pct + direct bonus
MATCHUP_SCORE_BOOST_CAP = 7.0  # extra EZPZ score boost/penalty from matchup confirmation

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
def fetch_mlb_player_k_rate(player_id, season=MLB_SEASON):
    """Return hitter strikeout rate from MLB Stats API season hitting stats."""
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
        so = float(stat.get("strikeOuts", 0) or 0)
        pa = float(stat.get("plateAppearances", 0) or 0)
        ab = float(stat.get("atBats", 0) or 0)
        denom = pa if pa > 0 else ab
        if denom <= 0:
            return None
        rate = so / denom
        if rate <= 0 or rate > 0.60:
            return None
        return rate
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
    rows["Usage"] = rows["Usage"].apply(lambda x: _to_rate(x, 0.0)) if "Usage" in rows.columns else 0.0
    return rows


def _lineup_pitch_type_matchup_multiplier(lineup, pitcher, pitcher_arsenal_df, team_pitch_type_df):
    """Small K adjustment from the actual confirmed hitters vs the pitcher's arsenal.

    Existing arsenal logic compares pitcher pitch mix to team pitch-type weakness.
    This adds a second, smaller layer when confirmed lineups are posted and
    Savant has hitter-level pitch-type rows. It answers: do today's actual 1-9
    hitters whiff more or less than average against the pitch types this pitcher
    actually throws?
    """
    neutral = {
        "multiplier": 1.0,
        "status": "Neutral - hitter-level pitch-type data unavailable.",
        "hitters_with_pitch_data": 0,
        "pitch_types_used": "",
        "lineup_weighted_whiff": None,
        "league_weighted_whiff": None,
        "detail_rows": pd.DataFrame(),
    }
    arsenal = _pitcher_arsenal_rows(pitcher, pitcher_arsenal_df)
    if arsenal.empty or team_pitch_type_df is None or team_pitch_type_df.empty:
        return neutral

    arsenal = arsenal.copy()
    arsenal["Usage"] = arsenal["Usage"].apply(lambda x: _to_rate(x, 0.0))
    arsenal = arsenal[arsenal["Usage"] > 0].sort_values("Usage", ascending=False).head(5)
    if arsenal.empty:
        return neutral

    # Focus on the pitch types that meaningfully define the arsenal.
    arsenal = arsenal[arsenal["Usage"] >= 0.08].copy()
    if arsenal.empty:
        return neutral
    usage_sum = arsenal["Usage"].sum()
    if usage_sum <= 0:
        return neutral
    arsenal["Usage Wt"] = arsenal["Usage"] / usage_sum

    league_pitch_whiff = {}
    if "Pitch Type" in team_pitch_type_df.columns and "Whiff" in team_pitch_type_df.columns:
        temp = team_pitch_type_df.copy()
        temp["Whiff"] = temp["Whiff"].apply(lambda x: _to_rate(x, 0.0))
        league_pitch_whiff = temp[temp["Whiff"] > 0].groupby("Pitch Type")["Whiff"].mean().to_dict()

    detail_rows = []
    hitter_scores = []
    for hitter in lineup or []:
        hname = hitter.get("player", "")
        hrows = _hitter_pitch_type_rows(hname, team_pitch_type_df)
        if hrows.empty:
            continue
        whiff_num = 0.0
        league_num = 0.0
        used = 0
        for _, prow in arsenal.iterrows():
            ptype = prow.get("Pitch Type", "")
            wt = float(prow.get("Usage Wt", 0.0) or 0.0)
            match = hrows[hrows["Pitch Type"] == ptype]
            if match.empty:
                continue
            h_whiff = _to_rate(match.iloc[0].get("Whiff", 0.0), 0.0)
            l_whiff = _to_rate(league_pitch_whiff.get(ptype, 0.0), 0.0)
            if h_whiff <= 0 or l_whiff <= 0:
                continue
            whiff_num += wt * h_whiff
            league_num += wt * l_whiff
            used += 1
        if used > 0 and league_num > 0:
            hitter_scores.append((whiff_num, league_num))
            detail_rows.append({
                "Player": hname,
                "Bats": hitter.get("bat_side", ""),
                "Arsenal Whiff Matchup": round(whiff_num * 100, 1),
                "League Avg vs Mix": round(league_num * 100, 1),
                "Diff": round((whiff_num - league_num) * 100, 1),
            })

    if len(hitter_scores) < 5:
        neutral["hitters_with_pitch_data"] = len(hitter_scores)
        neutral["detail_rows"] = pd.DataFrame(detail_rows)
        neutral["status"] = "Confirmed lineup found, but not enough hitter-level pitch-type rows matched."
        neutral["pitch_types_used"] = ", ".join(arsenal["Pitch Type"].astype(str).tolist())
        return neutral

    lineup_whiff = sum(x[0] for x in hitter_scores) / len(hitter_scores)
    league_whiff = sum(x[1] for x in hitter_scores) / len(hitter_scores)
    diff = lineup_whiff - league_whiff

    # About 1% projection movement per 3 points of whiff edge, capped at +/-3%.
    adj = max(-0.03, min(0.03, diff / 3.0))
    mult = 1.0 + adj
    if adj > 0.006:
        status = "Today's lineup whiffs more than average against this pitcher's actual mix."
    elif adj < -0.006:
        status = "Today's lineup makes more contact than average against this pitcher's actual mix."
    else:
        status = "Today's lineup is near neutral against this pitcher's actual mix."

    return {
        "multiplier": mult,
        "status": status,
        "hitters_with_pitch_data": len(hitter_scores),
        "pitch_types_used": ", ".join(arsenal["Pitch Type"].astype(str).tolist()),
        "lineup_weighted_whiff": lineup_whiff,
        "league_weighted_whiff": league_whiff,
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
        "multiplier": 1.0,
        "k_multiplier": 1.0,
        "hand_stack_multiplier": 1.0,
        "hand_stack": {},
        "pitch_type_multiplier": 1.0,
        "pitch_type_matchup": {},
        "status": "No confirmed MLB lineup found yet.",
        "hitters": pd.DataFrame(),
    }

    if not lineup or len(lineup) < 8:
        return details

    hitter_rows = []
    k_rates = []
    for hitter in lineup:
        k_rate = fetch_mlb_player_k_rate(hitter.get("player_id"), MLB_SEASON)
        hitter_rows.append({
            "Order": int(hitter.get("order", 0) or 0) // 100 if int(hitter.get("order", 0) or 0) >= 100 else hitter.get("order", ""),
            "Player": hitter.get("player", ""),
            "Bats": hitter.get("bat_side", ""),
            "K%": round(k_rate * 100, 1) if k_rate is not None else "",
        })
        if k_rate is not None:
            k_rates.append(k_rate)

    if len(k_rates) < 6:
        details["source"] = "Team baseline fallback"
        details["hitters_found"] = len(k_rates)
        hand_stack = _lineup_handedness_stack_multiplier(lineup, pitcher_hand)
        details["hand_stack"] = hand_stack
        details["hand_stack_multiplier"] = hand_stack.get("multiplier", 1.0)
        pitch_type_matchup = _lineup_pitch_type_matchup_multiplier(lineup, pitcher, pitcher_arsenal_df, team_pitch_type_df)
        details["pitch_type_matchup"] = pitch_type_matchup
        details["pitch_type_multiplier"] = float(pitch_type_matchup.get("multiplier", 1.0) or 1.0)
        details["status"] = "Confirmed lineup found, but not enough hitter K% stats were available."
        details["hitters"] = pd.DataFrame(hitter_rows)
        return details

    lineup_k = sum(k_rates) / len(k_rates)
    blended_k = (0.70 * lineup_k) + (0.30 * baseline_k)
    raw_multiplier = blended_k / baseline_k if baseline_k > 0 else 1.0
    k_multiplier = max(0.85, min(1.15, raw_multiplier))
    hand_stack = _lineup_handedness_stack_multiplier(lineup, pitcher_hand)
    hand_stack_multiplier = float(hand_stack.get("multiplier", 1.0) or 1.0)
    pitch_type_matchup = _lineup_pitch_type_matchup_multiplier(lineup, pitcher, pitcher_arsenal_df, team_pitch_type_df)
    pitch_type_multiplier = float(pitch_type_matchup.get("multiplier", 1.0) or 1.0)
    multiplier = max(0.80, min(1.20, k_multiplier * hand_stack_multiplier * pitch_type_multiplier))

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
        "multiplier": multiplier,
        "status": "Confirmed MLB lineup found and applied at 70% lineup / 30% team baseline, handedness stack, and hitter pitch-type matchup.",
        "hitters": pd.DataFrame(hitter_rows),
    })
    return details


def apply_lineup_k_adjustment(projection, lineup_details):
    try:
        return max(0, float(projection) * float(lineup_details.get("multiplier", 1.0)))
    except Exception:
        return projection
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
        home_implied = american_odds_to_implied_prob(home_ml_odds)
        away_implied = american_odds_to_implied_prob(away_ml_odds)
        home_ml_edge = home_win_prob - home_implied
        away_ml_edge = away_win_prob - away_implied
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
def load_team_hitting_stats_live():
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
def load_team_batting_split_live(split):
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


@st.cache_data(ttl=60 * 60)
def _savant_pitcher_skill_stats(year):
    """Pull pitcher skill columns from Baseball Savant's Statcast leaderboard.

    MLB Stats API is reliable for IP/SO/K%/BB%, but it does not provide the
    whiff, chase, zone, first-strike, hard-hit, or xwOBA fields used in the
    Recent Skill section. This helper maps several possible Savant CSV column
    names so the app does not quietly turn real Savant stats into 0.0.
    """
    urls = [
        (
            "https://baseballsavant.mlb.com/leaderboard/statcast"
            f"?type=pitcher&year={year}&position=&team=&min=10&sort=4&sortDir=desc&csv=true"
        ),
        (
            "https://baseballsavant.mlb.com/leaderboard/statcast"
            f"?type=pitcher&year={year}&position=&team=&min=0&sort=4&sortDir=desc&csv=true"
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
        return pd.DataFrame(columns=[
            "Player", "MLBAM ID", "xwOBA", "Hard Hit %", "Out of Zone %",
            "In Zone %", "Whiff %", "First Strike %", "Pitches"
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
            "Hard Hit %": pick_rate(row, ["hard_hit_percent", "hard_hit_pct", "Hard Hit %", "HardHit%"], 0.0),
            "Out of Zone %": pick_rate(row, ["oz_swing_percent", "o_swing_percent", "chase_percent", "Chase %", "O-Zone%", "Out of Zone %"], 0.0),
            "In Zone %": pick_rate(row, ["in_zone_percent", "zone_percent", "Zone %", "In Zone %"], 0.0),
            "Whiff %": pick_rate(row, ["whiff_percent", "Whiff %", "Whiff%"], 0.0),
            "First Strike %": pick_rate(row, ["f_strike_percent", "f_strike_pct", "First Strike %", "F-Strike%"], 0.0),
            "Pitches": pick_number(row, ["pitches", "Pitches", "pitch_count"], 0.0),
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
    return out.drop(columns=["_nonzero_skill_count"], errors="ignore").reset_index(drop=True)


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

    skill_cols = ["xwOBA", "Hard Hit %", "Out of Zone %", "In Zone %", "Whiff %", "First Strike %", "Pitches"]
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
def load_nrfi_pitchers_live():
    # Neutral first-inning table: NRFI formula will still use pitcher season xwOBA/K data.
    # This avoids a blocked blocked first-inning split dependency.
    return _neutral_nrfi_pitcher_table()


@st.cache_data(ttl=60 * 60)
def load_nrfi_team_split_live(hand):
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
def load_bullpen_stats_live():
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
        home_implied = american_odds_to_implied_prob(home_ml_odds)
        away_implied = american_odds_to_implied_prob(away_ml_odds)
        home_ml_edge = home_win_prob - home_implied
        away_ml_edge = away_win_prob - away_implied
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


def render_auto_matchup_builder(pitcher_this_year, pitcher_last_year, team_hitting, team_batting_rhp, team_batting_lhp, nrfi_pitchers, nrfi_rhp, nrfi_lhp, pitcher_arsenal_df=None, team_pitch_type_df=None, bullpen_stats=None, bullpen_fatigue_df=None):
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
        st.dataframe(
            today_games[["game_label", "game_time", "away_team", "home_team", "away_pitcher", "home_pitcher", "status"]],
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

    st.markdown("### Moneyline Pitching Context")
    st.caption("Use these only for bullpen/opener games. Moneylines switch to team bullpen context; NRFI and pitcher props still use the listed starters.")
    bp_col1, bp_col2 = st.columns(2)
    with bp_col1:
        use_home_bullpen = st.checkbox(
            f"{home_team} bullpen game / opener",
            value=False,
            key=f"home_bullpen_game_{game.get('game_pk')}"
        )
    with bp_col2:
        use_away_bullpen = st.checkbox(
            f"{away_team} bullpen game / opener",
            value=False,
            key=f"away_bullpen_game_{game.get('game_pk')}"
        )

    nrfi_odds = st.number_input("NRFI/YRFI Odds", value=-110, step=5, key=f"nrfi_odds_{game.get('game_pk')}")
    st.caption("Pitcher K lines/odds and NRFI/YRFI odds are manual for now. They are saved in Bet Tracker for ROI/Units calculations, but they do not show on Daily Slate.")

    home_k_6ip_raw = six_inning_strikeouts(home_pitcher, away_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df)
    away_k_6ip_raw = six_inning_strikeouts(away_pitcher, home_team, pitcher_this_year, pitcher_last_year, team_batting_rhp, team_batting_lhp, pitcher_arsenal_df, team_pitch_type_df)
    home_k_6ip = apply_lineup_k_adjustment(home_k_6ip_raw, home_lineup_details)
    away_k_6ip = apply_lineup_k_adjustment(away_k_6ip_raw, away_lineup_details)

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
        team_batting_lhp,
        bullpen_stats=bullpen_stats,
        use_home_bullpen=use_home_bullpen,
        use_away_bullpen=use_away_bullpen,
        bullpen_fatigue_df=bullpen_fatigue_df
    )

    home_implied = american_odds_to_implied_prob(home_ml_odds)
    away_implied = american_odds_to_implied_prob(away_ml_odds)
    home_ml_edge = home_win_prob - home_implied
    away_ml_edge = away_win_prob - away_implied
    home_ml_grade = moneyline_grade(home_ml_edge)
    away_ml_grade = moneyline_grade(away_ml_edge)

    bullpen_context_note = []
    if use_home_bullpen:
        bullpen_context_note.append(f"{home_team}: bullpen context used for moneyline")
    if use_away_bullpen:
        bullpen_context_note.append(f"{away_team}: bullpen context used for moneyline")

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
        st.metric("6-IP Pace", round(home_k_6ip, 2))
        st.metric("Line", home_k_line)
        st.metric("Edge", round(home_k_edge, 2))
        st.metric("Volatility", home_vol)
        st.metric("Bet Grade", home_k_grade)
        st.metric("K Score", home_k_score)
    with col4:
        st.markdown(f"### {away_pitcher}")
        st.metric("Expected K", round(away_k, 2))
        st.metric("6-IP Pace", round(away_k_6ip, 2))
        st.metric("Line", away_k_line)
        st.metric("Edge", round(away_k_edge, 2))
        st.metric("Volatility", away_vol)
        st.metric("Bet Grade", away_k_grade)
        st.metric("K Score", away_k_score)

    with st.expander("MLB Lineup K Blend Details", expanded=True):
        st.caption("Pitcher props use confirmed MLB lineups when posted. If no confirmed lineup is available yet, the model stays on team baseline fallback. Applied blend: 70% confirmed lineup K% / 30% team baseline K%, plus capped handedness-stack and hitter pitch-type matchup adjustments.")

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
            st.write(f"Pitch-type K multiplier: {float(home_lineup_details.get('pitch_type_multiplier', 1.0)):.3f}")
            st.write(f"Total lineup multiplier: {float(home_lineup_details.get('multiplier', 1.0)):.3f}")
            st.write(f"Projection before lineup: {home_k_raw:.2f} → after lineup: {home_k:.2f}")
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
            st.write(f"Pitch-type K multiplier: {float(away_lineup_details.get('pitch_type_multiplier', 1.0)):.3f}")
            st.write(f"Total lineup multiplier: {float(away_lineup_details.get('multiplier', 1.0)):.3f}")
            st.write(f"Projection before lineup: {away_k_raw:.2f} → after lineup: {away_k:.2f}")
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
    if bullpen_context_note:
        st.info(" | ".join(bullpen_context_note))
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
        away_k_summary = k_summary_text(away_pitcher, away_k, away_k_grade, away_k_line, away_k_odds)
        home_k_summary = k_summary_text(home_pitcher, home_k, home_k_grade, home_k_line, home_k_odds)

        add_slate_row(away_team, home_team, better_ml_text, better_ml_odds, better_ml_grade, nrfi_grade, away_k_summary, away_k_score, home_k_summary, home_k_score, game_id=game_key, game_label=game_label, slate_date=slate_date)

        matchup_details = {
            "pitchers": {
                "away": {
                    "pitcher": away_pitcher,
                    "team": away_team,
                    "opponent": home_team,
                    "expected_ks": round(away_k, 2),
                    "raw_expected_ks": round(away_k_raw, 2),
                    "six_ip_ks": round(away_k_6ip, 2),
                    "line": away_k_line,
                    "odds": away_k_odds,
                    "edge": round(away_k_edge, 2),
                    "volatility": away_vol,
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
                    "raw_expected_ks": round(home_k_raw, 2),
                    "six_ip_ks": round(home_k_6ip, 2),
                    "line": home_k_line,
                    "odds": home_k_odds,
                    "edge": round(home_k_edge, 2),
                    "volatility": home_vol,
                    "grade": home_k_grade,
                    "k_score": home_k_score,
                    "arsenal": home_arsenal_details,
                    "lineup": home_lineup_details
                }
            },
            "moneyline": {
                "better_team": better_ml_team,
                "better_probability": f"{better_ml_prob * 100:.1f}%",
                "better_odds": better_ml_odds,
                "better_grade": better_ml_grade,
                "bullpen_context": " | ".join(bullpen_context_note),
                "home": {
                    "team": home_team,
                    "model_win_pct": f"{home_win_prob * 100:.1f}%",
                    "implied_pct": f"{home_implied * 100:.1f}%",
                    "edge_pct": f"{home_ml_edge * 100:.1f}%",
                    "grade": home_ml_grade,
                    "bullpen_game_checked": use_home_bullpen
                },
                "away": {
                    "team": away_team,
                    "model_win_pct": f"{away_win_prob * 100:.1f}%",
                    "implied_pct": f"{away_implied * 100:.1f}%",
                    "edge_pct": f"{away_ml_edge * 100:.1f}%",
                    "grade": away_ml_grade,
                    "bullpen_game_checked": use_away_bullpen
                }
            },
            "nrfi": {
                "grade": nrfi_grade,
                "probability": f"{nrfi_prob * 100:.1f}%",
                "score": round(nrfi_score, 1),
                "odds": nrfi_odds
            }
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

        if nrfi_grade in ["ELITE NRFI", "STRONG NRFI", "NRFI", "YRFI"]:
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

        st.success("Matchup summary saved. Qualifying bets were added to Bet Tracker, and this game will be removed from the Build list.")
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
