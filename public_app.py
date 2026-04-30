import os
import json
import re
from datetime import date, datetime

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials


# -----------------------
# PAGE CONFIG + BRANDING
# -----------------------

LOGO_FILE = "ezpz_logo.png"

page_icon = LOGO_FILE if os.path.exists(LOGO_FILE) else "⚾"
st.set_page_config(
    page_title="EZPZ Picks",
    page_icon=page_icon,
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    :root {
        --ezpz-bg: #050814;
        --ezpz-card: #0f172a;
        --ezpz-card-2: #111827;
        --ezpz-border: rgba(148, 163, 184, 0.22);
        --ezpz-text: #f8fafc;
        --ezpz-muted: #cbd5e1;
        --ezpz-blue: #38bdf8;
        --ezpz-blue-2: #2563eb;
        --ezpz-green: #22c55e;
        --ezpz-yellow: #facc15;
        --ezpz-red: #ef4444;
    }

    .stApp {
        background:
            radial-gradient(circle at top left, rgba(37, 99, 235, 0.28), transparent 34%),
            radial-gradient(circle at top right, rgba(14, 165, 233, 0.16), transparent 30%),
            linear-gradient(180deg, #020617 0%, #0f172a 100%);
        color: var(--ezpz-text);
    }

    .block-container {
        padding-top: 1.1rem;
        padding-left: 0.75rem;
        padding-right: 0.75rem;
        max-width: 840px;
    }

    h1, h2, h3, p, span, div {
        font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    [data-testid="stHeader"] {background: transparent;}
    [data-testid="stToolbar"] {display: none;}

    .hero {
        padding: 1.1rem 1rem;
        border: 1px solid var(--ezpz-border);
        border-radius: 1.4rem;
        background: linear-gradient(135deg, rgba(15, 23, 42, 0.96), rgba(30, 41, 59, 0.78));
        box-shadow: 0 18px 60px rgba(0,0,0,0.28);
        margin-bottom: 1rem;
    }

    .hero-title {
        font-size: 2rem;
        font-weight: 900;
        letter-spacing: -0.055em;
        margin: 0;
        line-height: 1.05;
        background: linear-gradient(90deg, #f8fafc, #38bdf8);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }

    .hero-subtitle {
        margin-top: 0.45rem;
        color: var(--ezpz-muted);
        font-size: 0.96rem;
        line-height: 1.35;
    }

    .badge-row {
        display: flex;
        gap: 0.45rem;
        flex-wrap: wrap;
        margin-top: 0.8rem;
    }

    .badge {
        padding: 0.28rem 0.55rem;
        border-radius: 999px;
        font-size: 0.74rem;
        font-weight: 800;
        letter-spacing: 0.02em;
        border: 1px solid rgba(56, 189, 248, 0.32);
        color: #e0f2fe;
        background: rgba(14, 165, 233, 0.12);
    }

    .section-title {
        font-size: 1.2rem;
        font-weight: 900;
        letter-spacing: -0.02em;
        margin: 1.25rem 0 0.65rem;
        color: #f8fafc;
    }

    .pick-card, .game-card, .record-card, .info-card {
        border: 1px solid var(--ezpz-border);
        border-radius: 1.15rem;
        padding: 0.92rem;
        margin-bottom: 0.72rem;
        background: rgba(15, 23, 42, 0.88);
        box-shadow: 0 10px 30px rgba(0,0,0,0.20);
    }

    .pick-card {
        position: relative;
        overflow: hidden;
    }

    .pick-card:before {
        content: "";
        position: absolute;
        left: 0;
        top: 0;
        bottom: 0;
        width: 5px;
        background: var(--accent, #38bdf8);
    }

    .card-topline {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.5rem;
    }

    .rank-pill {
        min-width: 2.1rem;
        height: 2.1rem;
        border-radius: 999px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-weight: 900;
        background: rgba(56, 189, 248, 0.14);
        color: #7dd3fc;
        border: 1px solid rgba(56, 189, 248, 0.28);
    }

    .grade-pill {
        padding: 0.25rem 0.55rem;
        border-radius: 999px;
        font-weight: 900;
        font-size: 0.72rem;
        background: rgba(148, 163, 184, 0.12);
        border: 1px solid rgba(148, 163, 184, 0.22);
        color: #e2e8f0;
        white-space: nowrap;
    }

    .pick-title {
        font-size: 1.02rem;
        font-weight: 900;
        line-height: 1.25;
        margin: 0.55rem 0 0.25rem;
        color: #ffffff;
    }

    .muted {
        color: var(--ezpz-muted);
        font-size: 0.84rem;
        line-height: 1.35;
    }

    .tiny {
        color: #94a3b8;
        font-size: 0.74rem;
    }

    .metric-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.6rem;
        margin-bottom: 0.75rem;
    }

    .mini-metric {
        border: 1px solid var(--ezpz-border);
        border-radius: 1rem;
        padding: 0.75rem;
        background: rgba(2, 6, 23, 0.42);
    }

    .mini-label {
        color: #94a3b8;
        font-size: 0.72rem;
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }

    .mini-value {
        color: #ffffff;
        font-size: 1.15rem;
        font-weight: 950;
        margin-top: 0.15rem;
    }

    .game-title {
        color: #ffffff;
        font-size: 1rem;
        font-weight: 900;
        margin-bottom: 0.35rem;
    }

    .game-lines {
        display: grid;
        grid-template-columns: 1fr;
        gap: 0.42rem;
        margin-top: 0.65rem;
    }

    .line-chip {
        border-radius: 0.8rem;
        padding: 0.55rem 0.65rem;
        background: rgba(30, 41, 59, 0.78);
        border: 1px solid rgba(148, 163, 184, 0.17);
        color: #e5e7eb;
        font-size: 0.82rem;
        line-height: 1.3;
    }

    .good {color: #86efac;}
    .warn {color: #fde68a;}
    .bad {color: #fca5a5;}

    .footer-note {
        color: #94a3b8;
        font-size: 0.74rem;
        line-height: 1.35;
        margin-top: 1.25rem;
        padding-bottom: 2rem;
    }

    div[data-testid="stDataFrame"] {
        border-radius: 1rem;
        overflow: hidden;
        border: 1px solid rgba(148, 163, 184, 0.18);
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 0.35rem;
        overflow-x: auto;
    }

    .stTabs [data-baseweb="tab"] {
        border-radius: 999px;
        background: rgba(15, 23, 42, 0.88);
        border: 1px solid rgba(148, 163, 184, 0.18);
        padding: 0.55rem 0.7rem;
    }

    @media (max-width: 520px) {
        .hero-title {font-size: 1.72rem;}
        .metric-grid {grid-template-columns: repeat(2, minmax(0, 1fr));}
        .pick-card, .game-card, .record-card, .info-card {padding: 0.82rem;}
        .block-container {padding-left: 0.55rem; padding-right: 0.55rem;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# -----------------------
# GOOGLE SHEETS STORAGE
# -----------------------

TRACKER_TAB = "bet_tracker"
SLATE_TAB = "daily_slate"

TRACKER_COLUMNS = [
    "Date", "Bet Type", "Selection", "Market", "Odds/Line",
    "Model %", "Implied %", "Edge %", "Result"
]

SLATE_COLUMNS = [
    "Date",
    "Game ID",
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
    "Home Pitcher K Score"
]

LEGACY_SLATE_COLUMNS = [
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


def get_google_credentials_json():
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
        st.error("Missing GOOGLE_CREDENTIALS environment variable.")
        st.stop()

    if not sheet_name:
        st.error("Missing GOOGLE_SHEET_NAME environment variable.")
        st.stop()

    try:
        creds_dict = json.loads(creds_json)
    except Exception as e:
        st.error(f"GOOGLE_CREDENTIALS is not valid JSON: {e}")
        st.stop()

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open(sheet_name)


@st.cache_data(ttl=60)
def read_sheet(tab_name, columns):
    try:
        workbook = connect_to_sheets()
        worksheet = workbook.worksheet(tab_name)
        records = worksheet.get_all_records()
        df = pd.DataFrame(records)

        if df.empty:
            return pd.DataFrame(columns=columns)

        # Backward/forward compatibility: add any newer columns that do not exist yet.
        for col in columns:
            if col not in df.columns:
                df[col] = ""

        # Keep extra existing columns out of the public view.
        return df[columns].copy()
    except gspread.WorksheetNotFound:
        return pd.DataFrame(columns=columns)
    except Exception as e:
        st.error(f"Could not read Google Sheet tab '{tab_name}': {e}")
        return pd.DataFrame(columns=columns)


def load_tracker():
    return read_sheet(TRACKER_TAB, TRACKER_COLUMNS)


def load_slate():
    # First try the newer doubleheader-safe columns. If your sheet is still legacy,
    # Game ID and Game Label will be blank, and the app will build a display label.
    df = read_sheet(SLATE_TAB, SLATE_COLUMNS)
    if df.empty:
        return df

    for col in SLATE_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df["Game Label"] = df.apply(make_game_label, axis=1)
    return df


# -----------------------
# HELPERS
# -----------------------


def today_str():
    return str(date.today())


def safe_text(value, default=""):
    text = str(value).strip()
    if text.lower() in ["nan", "none", "nat"]:
        return default
    return text if text else default


def safe_float(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        text = str(value).replace("%", "").replace("+", "").strip()
        if not text:
            return default
        return float(text)
    except Exception:
        return default


def parse_american_odds(value):
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
    result = safe_text(result)
    if result == "Push":
        return 0.0
    if result == "Loss":
        return -1.0
    if result != "Win":
        return 0.0

    parsed_odds = parse_american_odds(odds)
    if parsed_odds is None:
        parsed_odds = -110
    if parsed_odds > 0:
        return parsed_odds / 100
    return 100 / abs(parsed_odds)


def format_signed_units(value):
    value = safe_float(value)
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}u"


def make_game_label(row):
    existing = safe_text(row.get("Game Label", ""))
    if existing:
        return existing

    away = safe_text(row.get("Away Team", "Away"))
    home = safe_text(row.get("Home Team", "Home"))
    game_id = safe_text(row.get("Game ID", ""))

    # If Game ID has a game number suffix from the admin app, display it.
    # Examples supported: 123456-G1, 123456_Game2, gamePk 123456 game 1.
    upper = game_id.upper()
    game_num = ""
    for pattern in [r"G(?:AME)?\s*([12])", r"[-_ ]([12])$"]:
        match = re.search(pattern, upper)
        if match:
            game_num = match.group(1)
            break

    suffix = f" · Game {game_num}" if game_num else ""
    return f"{away} at {home}{suffix}"


def is_play_text(value):
    upper = safe_text(value).upper()
    return bool(upper and "PASS" not in upper and "NON-EDGE" not in upper and "NO LINE" not in upper)


def normalize_bet_type_text(value):
    upper = safe_text(value).upper()

    if "PASS" in upper or "NON-EDGE" in upper or "NO LINE" in upper:
        return None

    if "A MONEYLINE" in upper or "[A]" in upper:
        return "A MONEYLINE"
    if "B MONEYLINE" in upper or "[B]" in upper:
        return "B MONEYLINE"

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

    if "STRONG OVER" in upper:
        return "STRONG OVER"
    if "STRONG UNDER" in upper:
        return "STRONG UNDER"
    if "LEAN OVER" in upper:
        return "LEAN OVER"
    if "LEAN UNDER" in upper:
        return "LEAN UNDER"
    if "OVER" in upper:
        return "OVER"
    if "UNDER" in upper:
        return "UNDER"

    return safe_text(value).upper() or None


def grade_accent(value):
    upper = safe_text(value).upper()
    if any(x in upper for x in ["ELITE", "STRONG", "A MONEYLINE"]):
        return "#22c55e"
    if any(x in upper for x in ["LEAN", "B MONEYLINE", "YRFI"]):
        return "#facc15"
    if any(x in upper for x in ["OVER", "UNDER", "NRFI"]):
        return "#38bdf8"
    return "#64748b"


def extract_k_line(k_summary):
    text = safe_text(k_summary)
    if "Line" not in text:
        return ""
    line_part = text.split("Line", 1)[1].strip()
    line_part = line_part.replace("/", "").strip()
    line_part = line_part.split("(", 1)[0].strip()
    return f"Line {line_part}" if line_part else ""


# -----------------------
# RECORDS + BEST PLAYS
# -----------------------


def build_record_summary(tracker_df):
    if tracker_df is None or tracker_df.empty:
        return pd.DataFrame()

    completed = tracker_df[tracker_df["Result"].isin(["Win", "Loss", "Push"])].copy()
    if completed.empty:
        return pd.DataFrame()

    rows = []
    for bet_type in sorted(completed["Bet Type"].dropna().unique()):
        sub = completed[completed["Bet Type"] == bet_type].copy()
        wins = int((sub["Result"] == "Win").sum())
        losses = int((sub["Result"] == "Loss").sum())
        pushes = int((sub["Result"] == "Push").sum())
        decisions = wins + losses
        total_bets = wins + losses + pushes
        win_pct = (wins / decisions * 100) if decisions else 0.0
        units_won = sum(
            profit_units_from_american_odds(bet.get("Odds/Line", ""), bet.get("Result", ""))
            for _, bet in sub.iterrows()
        )
        roi = (units_won / decisions * 100) if decisions else 0.0

        status = "Winning" if wins > losses else "Even" if wins == losses else "Losing"
        rows.append({
            "Bet Type": safe_text(bet_type),
            "Record Status": status,
            "Wins": wins,
            "Losses": losses,
            "Pushes": pushes,
            "Total Bets": total_bets,
            "Win %": round(win_pct, 1),
            "Units Won": round(units_won, 2),
            "ROI %": round(roi, 1),
        })

    return pd.DataFrame(rows).sort_values(["Record Status", "Win %"], ascending=[True, False]).reset_index(drop=True)


def build_green_totals(summary_df):
    if summary_df is None or summary_df.empty:
        return {
            "Wins": 0,
            "Losses": 0,
            "Pushes": 0,
            "Total Bets": 0,
            "Win %": 0.0,
            "Units Won": 0.0,
            "ROI %": 0.0,
        }

    green = summary_df[summary_df["Wins"] > summary_df["Losses"]].copy()
    if green.empty:
        return {
            "Wins": 0,
            "Losses": 0,
            "Pushes": 0,
            "Total Bets": 0,
            "Win %": 0.0,
            "Units Won": 0.0,
            "ROI %": 0.0,
        }

    wins = int(green["Wins"].sum())
    losses = int(green["Losses"].sum())
    pushes = int(green["Pushes"].sum())
    decisions = wins + losses
    total_bets = wins + losses + pushes
    units_won = float(green["Units Won"].sum())
    win_pct = (wins / decisions * 100) if decisions else 0.0
    roi = (units_won / decisions * 100) if decisions else 0.0

    return {
        "Wins": wins,
        "Losses": losses,
        "Pushes": pushes,
        "Total Bets": total_bets,
        "Win %": round(win_pct, 1),
        "Units Won": round(units_won, 2),
        "ROI %": round(roi, 1),
    }


def build_overall_totals(summary_df):
    if summary_df is None or summary_df.empty:
        return build_green_totals(summary_df)

    wins = int(summary_df["Wins"].sum())
    losses = int(summary_df["Losses"].sum())
    pushes = int(summary_df["Pushes"].sum())
    decisions = wins + losses
    total_bets = wins + losses + pushes
    units_won = float(summary_df["Units Won"].sum())
    win_pct = (wins / decisions * 100) if decisions else 0.0
    roi = (units_won / decisions * 100) if decisions else 0.0

    return {
        "Wins": wins,
        "Losses": losses,
        "Pushes": pushes,
        "Total Bets": total_bets,
        "Win %": round(win_pct, 1),
        "Units Won": round(units_won, 2),
        "ROI %": round(roi, 1),
    }


def build_best_plays(today_slate):
    def static_score(play_type, play_text=""):
        text = f"{play_type} {play_text}".upper()
        if "ELITE NRFI" in text:
            return 100
        if "STRONG NRFI" in text:
            return 85
        if "A MONEYLINE" in text:
            return 80
        if "B MONEYLINE" in text:
            return 65
        if "YRFI" in text:
            return 60
        return 0

    best_rows = []

    if today_slate is None or today_slate.empty:
        return pd.DataFrame(columns=["Play Type", "Game", "Play", "Odds/Line", "Score"])

    for _, row in today_slate.iterrows():
        game = make_game_label(row)

        ml_grade = safe_text(row.get("ML Grade", ""))
        if ml_grade in ["A Moneyline", "B Moneyline"]:
            best_rows.append({
                "Play Type": ml_grade,
                "Game": game,
                "Play": safe_text(row.get("Better ML", "")),
                "Odds/Line": safe_text(row.get("ML Odds", "")),
                "Score": static_score(ml_grade),
            })

        nrfi_grade = safe_text(row.get("NRFI Grade", ""))
        if nrfi_grade in ["ELITE NRFI", "STRONG NRFI", "YRFI"]:
            best_rows.append({
                "Play Type": nrfi_grade,
                "Game": game,
                "Play": nrfi_grade,
                "Odds/Line": "",
                "Score": static_score(nrfi_grade),
            })

        for play_col, score_col in [
            ("Away Pitcher K + Grade", "Away Pitcher K Score"),
            ("Home Pitcher K + Grade", "Home Pitcher K Score"),
        ]:
            text = safe_text(row.get(play_col, ""))
            upper = text.upper()
            if "PASS" not in upper and any(g in upper for g in [
                "STRONG OVER", "OVER", "LEAN OVER", "LEAN UNDER", "UNDER", "STRONG UNDER"
            ]):
                k_score = safe_float(row.get(score_col, 0), 0)
                if k_score == 0:
                    if "STRONG" in upper:
                        k_score = 90
                    elif "LEAN" in upper:
                        k_score = 50
                    elif "OVER" in upper or "UNDER" in upper:
                        k_score = 70

                best_rows.append({
                    "Play Type": "Pitcher K",
                    "Game": game,
                    "Play": text,
                    "Odds/Line": extract_k_line(text),
                    "Score": k_score,
                })

    if not best_rows:
        return pd.DataFrame(columns=["Play Type", "Game", "Play", "Odds/Line", "Score"])

    return pd.DataFrame(best_rows).sort_values("Score", ascending=False).reset_index(drop=True)


# -----------------------
# UI RENDERING
# -----------------------


def render_hero():
    if os.path.exists(LOGO_FILE):
        left, right = st.columns([0.22, 0.78], vertical_alignment="center")
        with left:
            st.image(LOGO_FILE, use_container_width=True)
        with right:
            st.markdown(
                """
                <div class="hero">
                    <div class="hero-title">EZPZ Picks</div>
                    <div class="hero-subtitle">Algorithm-based MLB betting board built for quick mobile viewing.</div>
                    <div class="badge-row">
                        <span class="badge">MLB Model</span>
                        <span class="badge">Best Plays</span>
                        <span class="badge">Records + ROI</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            """
            <div class="hero">
                <div class="hero-title">EZPZ Picks</div>
                <div class="hero-subtitle">Algorithm-based MLB betting board built for quick mobile viewing.</div>
                <div class="badge-row">
                    <span class="badge">MLB Model</span>
                    <span class="badge">Best Plays</span>
                    <span class="badge">Records + ROI</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_metric_grid(overall, green):
    st.markdown(
        f"""
        <div class="metric-grid">
            <div class="mini-metric">
                <div class="mini-label">Overall Record</div>
                <div class="mini-value">{overall['Wins']}-{overall['Losses']}-{overall['Pushes']}</div>
            </div>
            <div class="mini-metric">
                <div class="mini-label">Overall Units</div>
                <div class="mini-value {'good' if overall['Units Won'] >= 0 else 'bad'}">{format_signed_units(overall['Units Won'])}</div>
            </div>
            <div class="mini-metric">
                <div class="mini-label">Green Bets</div>
                <div class="mini-value">{green['Wins']}-{green['Losses']}-{green['Pushes']}</div>
            </div>
            <div class="mini-metric">
                <div class="mini-label">Green ROI</div>
                <div class="mini-value {'good' if green['ROI %'] >= 0 else 'bad'}">{green['ROI %']:.1f}%</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_pick_card(rank, row):
    play_type = safe_text(row.get("Play Type", "Pick"))
    game = safe_text(row.get("Game", ""))
    play = safe_text(row.get("Play", ""))
    odds = safe_text(row.get("Odds/Line", ""))
    score = safe_float(row.get("Score", 0), 0)
    accent = grade_accent(f"{play_type} {play}")

    odds_html = f"<span class='tiny'> · {odds}</span>" if odds else ""
    st.markdown(
        f"""
        <div class="pick-card" style="--accent:{accent};">
            <div class="card-topline">
                <span class="rank-pill">#{rank}</span>
                <span class="grade-pill">{play_type} · Score {score:.0f}</span>
            </div>
            <div class="pick-title">{play}{odds_html}</div>
            <div class="muted">{game}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_game_card(row):
    game = make_game_label(row)
    better_ml = safe_text(row.get("Better ML", ""), "No ML play")
    ml_grade = safe_text(row.get("ML Grade", ""))
    ml_odds = safe_text(row.get("ML Odds", ""))
    nrfi = safe_text(row.get("NRFI Grade", ""), "No NRFI/YRFI play")
    away_k = safe_text(row.get("Away Pitcher K + Grade", ""), "No away K play")
    home_k = safe_text(row.get("Home Pitcher K + Grade", ""), "No home K play")

    ml_line = f"{better_ml}"
    if ml_grade:
        ml_line += f" · {ml_grade}"
    if ml_odds:
        ml_line += f" · {ml_odds}"

    st.markdown(
        f"""
        <div class="game-card">
            <div class="game-title">{game}</div>
            <div class="game-lines">
                <div class="line-chip"><strong>Moneyline:</strong> {ml_line}</div>
                <div class="line-chip"><strong>NRFI/YRFI:</strong> {nrfi}</div>
                <div class="line-chip"><strong>Away K:</strong> {away_k}</div>
                <div class="line-chip"><strong>Home K:</strong> {home_k}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_green_totals_card(green):
    st.markdown(
        f"""
        <div class="record-card">
            <div class="game-title">Green Bet Totals</div>
            <div class="muted">Only bet types with a winning completed record are included.</div>
            <div class="game-lines">
                <div class="line-chip"><strong>Record:</strong> {green['Wins']}-{green['Losses']}-{green['Pushes']} · {green['Total Bets']} total bets</div>
                <div class="line-chip"><strong>Win %:</strong> {green['Win %']:.1f}%</div>
                <div class="line-chip"><strong>Units:</strong> {format_signed_units(green['Units Won'])}</div>
                <div class="line-chip"><strong>ROI:</strong> {green['ROI %']:.1f}%</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_records_table(summary_df):
    if summary_df.empty:
        st.info("No completed bets yet.")
        return

    view = summary_df.copy()
    view["Record"] = view.apply(lambda r: f"{int(r['Wins'])}-{int(r['Losses'])}-{int(r['Pushes'])}", axis=1)
    view["Units"] = view["Units Won"].apply(format_signed_units)
    view["Win %"] = view["Win %"].map(lambda x: f"{safe_float(x):.1f}%")
    view["ROI %"] = view["ROI %"].map(lambda x: f"{safe_float(x):.1f}%")
    view = view[["Bet Type", "Record Status", "Record", "Win %", "Units", "ROI %"]]
    st.dataframe(view, use_container_width=True, hide_index=True)


def render_how_it_works():
    st.markdown(
        """
        <div class="info-card">
            <div class="game-title">How EZPZ Picks Works</div>
            <div class="muted">
                Picks are generated from a private MLB model using team hitting, pitcher stats,
                handedness splits, NRFI indicators, pitcher strikeout projections, odds, edge,
                volatility, and record tracking. The public board shows the final plays only —
                not the full formula logic.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main():
    render_hero()

    tracker_df = load_tracker()
    slate_df = load_slate()
    current_date = today_str()

    summary_df = build_record_summary(tracker_df)
    overall = build_overall_totals(summary_df)
    green = build_green_totals(summary_df)

    render_metric_grid(overall, green)

    tabs = st.tabs(["Today", "Slate", "Records", "About"])

    with tabs[0]:
        st.markdown('<div class="section-title">Today\'s Best Plays</div>', unsafe_allow_html=True)
        today_slate = slate_df[slate_df["Date"].astype(str) == current_date].copy() if not slate_df.empty else pd.DataFrame()
        best_df = build_best_plays(today_slate)

        if best_df.empty:
            st.info("No public picks have been saved for today yet.")
        else:
            for idx, row in best_df.head(10).iterrows():
                render_pick_card(idx + 1, row)

        st.markdown(f"<div class='tiny'>Last refreshed: {datetime.now().strftime('%b %d, %Y %-I:%M %p') if os.name != 'nt' else datetime.now().strftime('%b %d, %Y %I:%M %p')}</div>", unsafe_allow_html=True)

    with tabs[1]:
        st.markdown('<div class="section-title">Today\'s Slate</div>', unsafe_allow_html=True)
        today_slate = slate_df[slate_df["Date"].astype(str) == current_date].copy() if not slate_df.empty else pd.DataFrame()
        if today_slate.empty:
            st.info("No games saved for today yet.")
        else:
            for _, row in today_slate.iterrows():
                render_game_card(row)

        st.markdown('<div class="section-title">Past Saved Slates</div>', unsafe_allow_html=True)
        if slate_df.empty:
            st.info("No saved slates yet.")
        else:
            dates = sorted(slate_df["Date"].dropna().astype(str).unique(), reverse=True)
            selected_date = st.selectbox("Choose a date", dates, index=dates.index(current_date) if current_date in dates else 0)
            selected_slate = slate_df[slate_df["Date"].astype(str) == selected_date]
            for _, row in selected_slate.iterrows():
                render_game_card(row)

    with tabs[2]:
        st.markdown('<div class="section-title">Public Record</div>', unsafe_allow_html=True)
        render_green_totals_card(green)
        render_records_table(summary_df)

    with tabs[3]:
        st.markdown('<div class="section-title">About</div>', unsafe_allow_html=True)
        render_how_it_works()
        st.markdown(
            """
            <div class="footer-note">
            For entertainment and informational purposes only. Betting involves risk.
            Use your own judgment and never bet more than you can afford to lose.
            </div>
            """,
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    main()
