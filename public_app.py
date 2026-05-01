import os
import json
import re
import html
import base64
from urllib.parse import quote
from datetime import date, datetime
from zoneinfo import ZoneInfo

import gspread
import pandas as pd
import requests
import streamlit as st
from google.oauth2.service_account import Credentials

try:
    from PIL import Image
except Exception:
    Image = None

LOGO_FILE = "ezpz_logo.png"
TRACKER_TAB = "bet_tracker"
SLATE_TAB = "daily_slate"

TRACKER_COLUMNS = [
    "Date", "Bet Type", "Selection", "Market", "Odds/Line",
    "Model %", "Implied %", "Edge %", "Result"
]

SLATE_COLUMNS = [
    "Date", "Game ID", "Game Label", "Away Team", "Home Team", "Better ML",
    "ML Odds", "ML Grade", "NRFI Grade", "Away Pitcher K + Grade",
    "Away Pitcher K Score", "Home Pitcher K + Grade", "Home Pitcher K Score"
]

TEAM_LOGO_ABBR = {
    "Arizona Diamondbacks": "ari", "Atlanta Braves": "atl", "Baltimore Orioles": "bal",
    "Boston Red Sox": "bos", "Chicago Cubs": "chc", "Chicago White Sox": "cws",
    "Cincinnati Reds": "cin", "Cleveland Guardians": "cle", "Colorado Rockies": "col",
    "Detroit Tigers": "det", "Houston Astros": "hou", "Kansas City Royals": "kc",
    "Los Angeles Angels": "laa", "Los Angeles Dodgers": "lad", "Miami Marlins": "mia",
    "Milwaukee Brewers": "mil", "Minnesota Twins": "min", "New York Mets": "nym",
    "New York Yankees": "nyy", "Athletics": "ath", "Oakland Athletics": "ath",
    "Philadelphia Phillies": "phi", "Pittsburgh Pirates": "pit", "San Diego Padres": "sd",
    "Seattle Mariners": "sea", "San Francisco Giants": "sf", "St. Louis Cardinals": "stl",
    "Tampa Bay Rays": "tb", "Texas Rangers": "tex", "Toronto Blue Jays": "tor",
    "Washington Nationals": "wsh",
}

if os.path.exists(LOGO_FILE) and Image is not None:
    try:
        page_icon = Image.open(LOGO_FILE)
    except Exception:
        page_icon = "⚾"
else:
    page_icon = "⚾"
st.set_page_config(page_title="EZPZ Picks", page_icon=page_icon, layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
<style>
:root {--bg:#050814;--card:#0f172a;--border:rgba(148,163,184,.22);--muted:#cbd5e1;--blue:#38bdf8;--green:#22c55e;--yellow:#facc15;--red:#ef4444;}
.stApp {background: radial-gradient(circle at top left, rgba(37,99,235,.30), transparent 34%), radial-gradient(circle at top right, rgba(14,165,233,.16), transparent 30%), linear-gradient(180deg,#020617 0%,#0f172a 100%); color:#f8fafc;}
.block-container {padding-top:1rem; padding-left:.65rem; padding-right:.65rem; max-width:860px;}
[data-testid="stHeader"] {background:transparent;} [data-testid="stToolbar"] {display:none;}
h1,h2,h3,p,span,div {font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
.logo-wrap {display:flex;justify-content:center;margin:.25rem 0 .75rem;} .updated-top{margin-top:.55rem;color:#94a3b8;font-size:.76rem;font-weight:800;} .logo-wrap img{border-radius:1.1rem;box-shadow:0 12px 32px rgba(0,0,0,.35);}
.hero {padding:1.1rem 1rem;border:1px solid var(--border);border-radius:1.35rem;background:linear-gradient(135deg,rgba(15,23,42,.96),rgba(30,41,59,.78));box-shadow:0 18px 60px rgba(0,0,0,.28);margin-bottom:1rem;}
.hero-title {font-size:2rem;font-weight:950;letter-spacing:-.055em;line-height:1.05;background:linear-gradient(90deg,#f8fafc,#38bdf8);-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
.hero-subtitle {margin-top:.45rem;color:var(--muted);font-size:.96rem;line-height:1.35;}
.badge-row {display:flex;gap:.45rem;flex-wrap:wrap;margin-top:.8rem}.badge {padding:.28rem .55rem;border-radius:999px;font-size:.74rem;font-weight:850;border:1px solid rgba(56,189,248,.32);color:#e0f2fe;background:rgba(14,165,233,.12)}
.metric-grid {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.6rem;margin:.8rem 0 .9rem}.mini {border:1px solid var(--border);border-radius:1rem;padding:.75rem;background:rgba(2,6,23,.42)}.mini.green {border-color:rgba(34,197,94,.35);background:linear-gradient(135deg,rgba(20,83,45,.35),rgba(15,23,42,.86))}.mini-label {color:#94a3b8;font-size:.71rem;font-weight:850;text-transform:uppercase;letter-spacing:.04em}.mini-value {font-size:1.15rem;font-weight:950;margin-top:.15rem;color:#fff}.good{color:#86efac}.bad{color:#fca5a5}.warn{color:#fde68a}
.section-title {font-size:1.2rem;font-weight:950;letter-spacing:-.02em;margin:1.2rem 0 .65rem;color:#f8fafc}.pick-card,.game-card,.record-card,.info-card {border:1px solid var(--border);border-radius:1.15rem;padding:.92rem;margin-bottom:.72rem;background:rgba(15,23,42,.88);box-shadow:0 10px 30px rgba(0,0,0,.2)}.pick-card {position:relative;overflow:hidden}.pick-card:before{content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:#22c55e}
.card-top {display:flex;align-items:center;justify-content:space-between;gap:.5rem}.rank {min-width:2.1rem;height:2.1rem;border-radius:999px;display:inline-flex;align-items:center;justify-content:center;font-weight:950;background:rgba(34,197,94,.16);color:#86efac;border:1px solid rgba(34,197,94,.35)}.pill {padding:.25rem .55rem;border-radius:999px;font-weight:900;font-size:.72rem;background:rgba(34,197,94,.14);border:1px solid rgba(34,197,94,.28);color:#bbf7d0;white-space:nowrap;text-transform:uppercase}.pick-title,.game-title{font-size:1.02rem;font-weight:950;line-height:1.25;color:#fff}.muted{color:var(--muted);font-size:.84rem;line-height:1.35}.tiny{color:#94a3b8;font-size:.74rem}.footer-note{color:#94a3b8;font-size:.74rem;line-height:1.35;margin-top:1.25rem;padding-bottom:2rem}
.team-row{display:flex;align-items:center;justify-content:space-between;gap:.6rem;margin:.55rem 0 .65rem}.team-side{display:flex;align-items:center;gap:.45rem;min-width:0;flex:1}.team-side.home{justify-content:flex-end;text-align:right}.team-logo{width:34px;height:34px;object-fit:contain;filter:drop-shadow(0 7px 12px rgba(0,0,0,.25));flex:0 0 auto}.team-name{color:#fff;font-size:.82rem;font-weight:850;line-height:1.15;overflow:hidden;text-overflow:ellipsis}.vs{flex:0 0 auto;padding:.22rem .42rem;border-radius:999px;font-size:.68rem;font-weight:900;color:#bfdbfe;background:rgba(37,99,235,.14);border:1px solid rgba(96,165,250,.22)}
.pick-media{display:flex;align-items:center;gap:.65rem;margin:.55rem 0 .25rem}.headshot{width:48px;height:48px;object-fit:cover;object-position:center top;transform:scale(1.12);border-radius:999px;background:rgba(30,41,59,.9);border:1px solid rgba(148,163,184,.24);flex:0 0 auto}.lines{display:grid;grid-template-columns:1fr;gap:.42rem;margin-top:.65rem}.chip{border-radius:.8rem;padding:.55rem .65rem;background:rgba(30,41,59,.78);border:1px solid rgba(148,163,184,.17);color:#e5e7eb;font-size:.82rem;line-height:1.3}.chip.green-prop{background:linear-gradient(135deg,rgba(20,83,45,.55),rgba(22,101,52,.22));border-color:rgba(34,197,94,.48);box-shadow:inset 0 0 0 1px rgba(134,239,172,.08)}.k-detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.42rem;margin-top:.55rem}.k-detail{border-radius:.75rem;background:rgba(2,6,23,.38);border:1px solid rgba(148,163,184,.16);padding:.48rem .55rem}.k-label{display:block;color:#94a3b8;font-size:.64rem;font-weight:900;text-transform:uppercase;letter-spacing:.04em}.k-value{display:block;color:#f8fafc;font-size:.9rem;font-weight:950;margin-top:.08rem}.breakdown-grid{display:grid;grid-template-columns:1fr;gap:.55rem}.breakdown-card{border:1px solid rgba(148,163,184,.18);background:rgba(15,23,42,.86);border-radius:1rem;padding:.75rem}.breakdown-card.green{border-color:rgba(34,197,94,.42);background:linear-gradient(135deg,rgba(20,83,45,.32),rgba(15,23,42,.86))}.breakdown-card.yellow{border-color:rgba(250,204,21,.34)}.breakdown-card.red{border-color:rgba(239,68,68,.34)}.breakdown-title{font-weight:950;color:#fff;text-transform:uppercase}.breakdown-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.4rem;margin-top:.55rem}.game-card .pick-title{font-size:.78rem!important;line-height:1.18}.game-card .k-detail-grid{gap:.30rem;margin-top:.42rem}.game-card .k-detail{padding:.32rem .40rem;border-radius:.58rem}.game-card .k-label{font-size:.52rem}.game-card .k-value{font-size:.68rem;line-height:1.12}.game-card .chip{font-size:.76rem;padding:.46rem .55rem}/* Premium centered EZPZ tab navigation */
.stTabs {margin-top:.25rem;}
.stTabs [data-baseweb="tab-list"]{justify-content:center;gap:.58rem;overflow-x:auto;padding:.42rem .48rem;margin:.15rem auto .7rem;width:fit-content;max-width:100%;border:1px solid rgba(56,189,248,.16);border-radius:999px;background:linear-gradient(135deg,rgba(2,6,23,.70),rgba(15,23,42,.54));box-shadow:0 12px 34px rgba(0,0,0,.20), inset 0 1px 0 rgba(255,255,255,.04);scrollbar-width:none;}
.stTabs [data-baseweb="tab-list"]::-webkit-scrollbar{display:none;}
.stTabs [data-baseweb="tab"]{position:relative;min-width:auto;height:42px;border-radius:999px;padding:.55rem .95rem;background:rgba(15,23,42,.72);border:1px solid rgba(148,163,184,.16);color:#cbd5e1;font-weight:900;letter-spacing:.01em;transition:transform .18s ease, background .18s ease, border-color .18s ease, box-shadow .18s ease, color .18s ease;}
.stTabs [data-baseweb="tab"] p{font-size:.86rem;font-weight:900;}
.stTabs [data-baseweb="tab"]:hover{transform:translateY(-1px);color:#f8fafc;background:rgba(14,165,233,.14);border-color:rgba(56,189,248,.45);box-shadow:0 8px 22px rgba(14,165,233,.14);}
.stTabs [aria-selected="true"]{color:#ffffff!important;background:linear-gradient(135deg,#1d4ed8 0%,#2563eb 42%,#38bdf8 100%)!important;border-color:rgba(125,211,252,.45)!important;box-shadow:0 10px 28px rgba(37,99,235,.42), inset 0 1px 0 rgba(255,255,255,.22)!important;transform:translateY(-1px) scale(1.015);}
.stTabs [aria-selected="true"]:after{content:"";position:absolute;inset:-2px;z-index:-1;border-radius:999px;background:linear-gradient(135deg,rgba(56,189,248,.42),rgba(37,99,235,.08));filter:blur(9px);opacity:.75;animation:ezpzTabGlow 2.8s ease-in-out infinite;}
.stTabs [data-baseweb="tab-highlight"]{display:none;}
.stTabs [data-baseweb="tab-border"]{display:none;}
@keyframes ezpzTabGlow{0%,100%{opacity:.45;transform:scale(.98)}50%{opacity:.85;transform:scale(1.04)}}

@media(max-width:520px){.hero-title{font-size:1.72rem}.pick-card,.game-card,.record-card,.info-card{padding:.82rem}.block-container{padding-left:.55rem;padding-right:.55rem}.team-logo{width:30px;height:30px}.team-name{font-size:.76rem}.stTabs [data-baseweb="tab-list"]{justify-content:flex-start;width:100%;border-radius:1.2rem;padding:.36rem;gap:.38rem}.stTabs [data-baseweb="tab"]{height:40px;padding:.48rem .72rem}.stTabs [data-baseweb="tab"] p{font-size:.80rem}}

/* Centered premium layout fixes */
.logo-wrap {
  display:flex !important;
  justify-content:center !important;
  align-items:center !important;
  width:100% !important;
  margin:.25rem auto .85rem !important;
  text-align:center !important;
}
.logo-wrap > div,
.logo-wrap img {
  margin-left:auto !important;
  margin-right:auto !important;
}

.hero {
  max-width:640px !important;
  margin-left:auto !important;
  margin-right:auto !important;
  text-align:center !important;
}
.hero-title,
.hero-subtitle,
.updated-top {
  text-align:center !important;
}
.badge-row {
  justify-content:center !important;
}

.metric-grid {
  max-width:640px !important;
  margin-left:auto !important;
  margin-right:auto !important;
}

.stTabs [data-baseweb="tab-list"]{
  justify-content:center !important;
  margin-left:auto !important;
  margin-right:auto !important;
  width:max-content !important;
}
.stTabs [data-baseweb="tab-panel"] {
  width:100%;
}

@media(max-width:520px){
  .stTabs [data-baseweb="tab-list"]{
    justify-content:center !important;
    width:fit-content !important;
    max-width:100% !important;
    margin-left:auto !important;
    margin-right:auto !important;
  }
}



/* True centered premium logo */
.logo-premium {
  width:100% !important;
  display:flex !important;
  justify-content:center !important;
  align-items:center !important;
  margin:.35rem auto 1rem !important;
  text-align:center !important;
}

.logo-glow-ring {
  display:inline-flex !important;
  align-items:center !important;
  justify-content:center !important;
  padding:7px;
  border-radius:1.55rem;
  background:
    linear-gradient(135deg, rgba(29,78,216,.95), rgba(56,189,248,.95)),
    radial-gradient(circle at top left, rgba(125,211,252,.45), transparent 38%);
  box-shadow:
    0 0 26px rgba(56,189,248,.38),
    0 14px 46px rgba(37,99,235,.36),
    inset 0 1px 0 rgba(255,255,255,.26);
  position:relative;
  overflow:visible;
}

.logo-glow-ring:before {
  content:"";
  position:absolute;
  inset:-10px;
  border-radius:1.9rem;
  background:linear-gradient(135deg, rgba(56,189,248,.34), rgba(37,99,235,.08));
  filter:blur(14px);
  opacity:.75;
  z-index:0;
}

.logo-glow-ring img {
  position:relative;
  z-index:1;
  width:150px !important;
  height:auto !important;
  display:block !important;
  border-radius:1.22rem;
  box-shadow:0 8px 24px rgba(0,0,0,.28);
}

.logo-glow-ring:hover {
  transform:translateY(-1px) scale(1.015);
  transition:transform .22s ease, box-shadow .22s ease;
  box-shadow:
    0 0 36px rgba(56,189,248,.55),
    0 18px 58px rgba(37,99,235,.46),
    inset 0 1px 0 rgba(255,255,255,.34);
}

</style>
""", unsafe_allow_html=True)


def safe_text(value, default=""):
    text = str(value).strip()
    if text.lower() in ["nan", "none", "nat"]:
        return default
    return text if text else default


def esc(value):
    return html.escape(safe_text(value))


def image_to_base64(path):
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return ""


def safe_float(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        text = str(value).replace("%", "").replace("+", "").strip()
        return float(text) if text else default
    except Exception:
        return default


APP_TZ = ZoneInfo("America/New_York")


def today_str():
    # Keep the public board on the Eastern Time slate until midnight ET.
    return datetime.now(APP_TZ).strftime("%Y-%m-%d")


def normalize_date_key(value):
    # Handles Google Sheets dates whether they come in as 2026-04-30,
    # 4/30/2026, datetime-like values, or plain strings.
    text = safe_text(value)
    if not text:
        return ""
    try:
        parsed = pd.to_datetime(text, errors="coerce")
        if pd.isna(parsed):
            return text
        return parsed.strftime("%Y-%m-%d")
    except Exception:
        return text


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
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open(sheet_name)


@st.cache_data(ttl=60)
def read_sheet(tab_name, columns):
    try:
        worksheet = connect_to_sheets().worksheet(tab_name)
        df = pd.DataFrame(worksheet.get_all_records())
        if df.empty:
            return pd.DataFrame(columns=columns)
        for col in columns:
            if col not in df.columns:
                df[col] = ""
        return df[columns].copy()
    except gspread.WorksheetNotFound:
        return pd.DataFrame(columns=columns)
    except Exception as e:
        st.error(f"Could not read Google Sheet tab '{tab_name}': {e}")
        return pd.DataFrame(columns=columns)


def load_tracker():
    return read_sheet(TRACKER_TAB, TRACKER_COLUMNS)


def load_slate():
    df = read_sheet(SLATE_TAB, SLATE_COLUMNS)
    if df.empty:
        df = pd.DataFrame(columns=SLATE_COLUMNS)
        df["Date Key"] = ""
        return df
    for col in SLATE_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df["Date Key"] = df["Date"].apply(normalize_date_key)
    df["Game Label"] = df.apply(make_game_label, axis=1)
    return df


def parse_american_odds(value):
    text = safe_text(value)
    matches = re.findall(r"[+-]?\d+", text)
    if not matches:
        return None
    try:
        odds = int(matches[-1])
    except Exception:
        return None
    return None if -99 < odds < 100 else odds


def profit_units_from_american_odds(odds, result):
    result = safe_text(result)
    if result == "Push":
        return 0.0
    if result == "Loss":
        return -1.0
    if result != "Win":
        return 0.0
    parsed = parse_american_odds(odds) or -110
    return parsed / 100 if parsed > 0 else 100 / abs(parsed)


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
    upper = game_id.upper()
    game_num = ""
    for pattern in [r"G(?:AME)?\s*([12])", r"[-_ ]([12])$"]:
        match = re.search(pattern, upper)
        if match:
            game_num = match.group(1)
            break
    suffix = f" · Game {game_num}" if game_num else ""
    return f"{away} at {home}{suffix}"


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
    return upper or None


def grade_accent(value):
    return "#22c55e"

def extract_k_line(k_summary):
    text = safe_text(k_summary)
    if not text:
        return ""
    line_match = re.search(r"\bLine\s*([-+]?\d+(?:\.\d+)?)", text, flags=re.I)
    if line_match:
        return f"Line {line_match.group(1)}"
    trailing_match = re.search(r"\)\s*([-+]?\d+(?:\.\d+)?)\s*$", text)
    if trailing_match:
        return f"Line {trailing_match.group(1)}"
    return ""


def display_bet_type(value):
    return safe_text(value).upper()


def to_first_last(name):
    name = safe_text(name)
    if not name or name.upper() == "TBD":
        return name
    if "," not in name:
        return name
    last, first = name.split(",", 1)
    return f"{first.strip()} {last.strip()}"


def normalize_person_name(name):
    return re.sub(r"[^a-z ]", "", safe_text(name).lower()).strip()


def extract_pitcher_name_from_k_play(text):
    text = safe_text(text)
    match = re.match(r"^(.+?)\s+[-+]?\d+(?:\.\d+)?\s*\(", text)
    if match:
        return match.group(1).strip()
    return re.sub(r"\b(Line|Odds)\b.*$", "", text, flags=re.I).strip()


def extract_k_details(text):
    text = safe_text(text)
    out = {"pitcher": "", "projection": "", "bet_type": "", "line": ""}
    match = re.match(r"^(.+?)\s+([-+]?\d+(?:\.\d+)?)\s*\(([^)]+)\)\s*(.*)$", text)
    if match:
        out["pitcher"] = match.group(1).strip()
        out["projection"] = match.group(2).strip()
        out["bet_type"] = match.group(3).strip().upper()
        rest = match.group(4).strip()
    else:
        out["pitcher"] = extract_pitcher_name_from_k_play(text)
        out["bet_type"] = display_bet_type(normalize_bet_type_text(text) or "PITCHER K")
        rest = text
    line_match = re.search(r"Line\s*([-+]?\d+(?:\.\d+)?)", rest, flags=re.I)
    if line_match:
        out["line"] = line_match.group(1)
    else:
        out["line"] = extract_k_line(text).replace("Line", "").strip()
    return out

def team_logo_url(team):
    abbr = TEAM_LOGO_ABBR.get(safe_text(team), "")
    return f"https://a.espncdn.com/i/teamlogos/mlb/500/{abbr}.png" if abbr else ""


def team_logo_img(team):
    url = team_logo_url(team)
    return f'<img class="team-logo" src="{url}" alt="{esc(team)} logo">' if url else ""


@st.cache_data(ttl=60 * 60 * 24)
def find_mlb_player_id(player_name):
    raw_name = safe_text(player_name)
    name = to_first_last(raw_name)
    if not name or name.upper() == "TBD":
        return ""
    target = normalize_person_name(name)
    raw_target = normalize_person_name(raw_name)
    try:
        response = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/search?names={quote(name)}&sportIds=1",
            timeout=5,
        )
        response.raise_for_status()
        people = response.json().get("people", [])
        if not people:
            return ""
        for person in people:
            full_name = normalize_person_name(person.get("fullName", ""))
            last_first = normalize_person_name(person.get("lastFirstName", ""))
            if full_name == target or last_first == raw_target:
                return str(person.get("id", ""))
        tokens = target.split()
        if len(tokens) >= 2:
            first, last = tokens[0], tokens[-1]
            for person in people:
                full_tokens = normalize_person_name(person.get("fullName", "")).split()
                if len(full_tokens) >= 2 and full_tokens[0] == first and full_tokens[-1] == last:
                    return str(person.get("id", ""))
        return ""
    except Exception:
        return ""

def pitcher_headshot_url(player_name):
    player_id = find_mlb_player_id(player_name)
    return f"https://img.mlbstatic.com/mlb-photos/image/upload/w_120,q_auto:best/v1/people/{player_id}/headshot/67/current" if player_id else ""


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
        total = wins + losses + pushes
        units = sum(profit_units_from_american_odds(b.get("Odds/Line", ""), b.get("Result", "")) for _, b in sub.iterrows())
        rows.append({
            "Bet Type": safe_text(bet_type),
            "Record Status": "Winning" if wins > losses else "Even" if wins == losses else "Losing",
            "Wins": wins, "Losses": losses, "Pushes": pushes, "Total Bets": total,
            "Win %": round((wins / decisions * 100) if decisions else 0, 1),
            "Units Won": round(units, 2),
            "ROI %": round((units / decisions * 100) if decisions else 0, 1),
        })
    return pd.DataFrame(rows).sort_values(["Record Status", "Win %"], ascending=[True, False]).reset_index(drop=True)


def empty_totals():
    return {"Wins": 0, "Losses": 0, "Pushes": 0, "Total Bets": 0, "Win %": 0.0, "Units Won": 0.0, "ROI %": 0.0}


def build_totals(summary_df, green_only=False):
    if summary_df is None or summary_df.empty:
        return empty_totals()
    df = summary_df[summary_df["Wins"] > summary_df["Losses"]].copy() if green_only else summary_df.copy()
    if df.empty:
        return empty_totals()
    wins = int(df["Wins"].sum()); losses = int(df["Losses"].sum()); pushes = int(df["Pushes"].sum())
    decisions = wins + losses; total = wins + losses + pushes; units = float(df["Units Won"].sum())
    return {"Wins": wins, "Losses": losses, "Pushes": pushes, "Total Bets": total, "Win %": round((wins / decisions * 100) if decisions else 0, 1), "Units Won": round(units, 2), "ROI %": round((units / decisions * 100) if decisions else 0, 1)}


def build_green_bet_type_set(summary_df):
    if summary_df is None or summary_df.empty:
        return set()
    green = summary_df[summary_df["Wins"] > summary_df["Losses"]].copy()
    return {normalize_bet_type_text(x) for x in green["Bet Type"].dropna().tolist() if normalize_bet_type_text(x)}


def build_best_plays(today_slate, green_bet_types):
    def static_score(play_type, play_text=""):
        text = f"{play_type} {play_text}".upper()
        if "ELITE NRFI" in text: return 100
        if "STRONG NRFI" in text: return 85
        if "A MONEYLINE" in text: return 80
        if "B MONEYLINE" in text: return 65
        if "YRFI" in text: return 60
        return 0
    rows = []
    if today_slate is None or today_slate.empty or not green_bet_types:
        return pd.DataFrame(columns=["Play Type", "Game", "Play", "Odds/Line", "Score"])
    for _, row in today_slate.iterrows():
        game = make_game_label(row)
        ml_grade = safe_text(row.get("ML Grade", ""))
        if ml_grade in ["A Moneyline", "B Moneyline"] and normalize_bet_type_text(ml_grade) in green_bet_types:
            rows.append({"Play Type": display_bet_type(ml_grade), "Game": game, "Play": safe_text(row.get("Better ML", "")), "Odds/Line": safe_text(row.get("ML Odds", "")), "Score": static_score(ml_grade)})
        nrfi = safe_text(row.get("NRFI Grade", ""))
        if nrfi in ["ELITE NRFI", "STRONG NRFI", "YRFI"] and normalize_bet_type_text(nrfi) in green_bet_types:
            rows.append({"Play Type": display_bet_type(nrfi), "Game": game, "Play": nrfi, "Odds/Line": "", "Score": static_score(nrfi)})
        for play_col, score_col in [("Away Pitcher K + Grade", "Away Pitcher K Score"), ("Home Pitcher K + Grade", "Home Pitcher K Score")]:
            text = safe_text(row.get(play_col, "")); upper = text.upper(); bet_type = normalize_bet_type_text(text)
            if bet_type in green_bet_types and "PASS" not in upper:
                k_score = safe_float(row.get(score_col, 0), 0)
                if k_score == 0:
                    k_score = 90 if "STRONG" in upper else 50 if "LEAN" in upper else 70
                rows.append({"Play Type": display_bet_type(bet_type), "Game": game, "Play": text, "Odds/Line": extract_k_line(text), "Score": k_score})
    if not rows:
        return pd.DataFrame(columns=["Play Type", "Game", "Play", "Odds/Line", "Score"])
    return pd.DataFrame(rows).sort_values("Score", ascending=False).reset_index(drop=True)


def parse_teams_from_game(game):
    text = safe_text(game).split(" · Game", 1)[0]
    if " at " in text:
        return [safe_text(x) for x in text.split(" at ", 1)]
    return "", ""


def matchup_strip(game):
    away, home = parse_teams_from_game(game)
    if not away or not home:
        return ""
    return f'<div class="team-row"><div class="team-side">{team_logo_img(away)}<span class="team-name">{esc(away)}</span></div><span class="vs">AT</span><div class="team-side home"><span class="team-name">{esc(home)}</span>{team_logo_img(home)}</div></div>'


def render_hero():
    if os.path.exists(LOGO_FILE):
        logo_b64 = image_to_base64(LOGO_FILE)
        if logo_b64:
            st.markdown(f"""
            <div class="logo-premium">
              <div class="logo-glow-ring">
                <img src="data:image/png;base64,{logo_b64}" alt="EZPZ Picks logo">
              </div>
            </div>
            """, unsafe_allow_html=True)
    last_updated = datetime.now(APP_TZ).strftime('%b %d, %Y %I:%M %p ET')
    st.markdown(f"""
    <div class="hero"><div class="hero-title">EZPZ Picks</div><div class="hero-subtitle">Algorithm-based MLB betting board focused on green/winning bet types.</div><div class="updated-top">Last updated: {last_updated}</div><div class="badge-row"><span class="badge">Green Bets First</span><span class="badge">Best Plays</span><span class="badge">Units + ROI</span></div></div>
    """, unsafe_allow_html=True)


def render_metrics(overall, green):
    st.markdown(f"""
    <div class="metric-grid">
      <div class="mini green"><div class="mini-label">Green Bet Record</div><div class="mini-value">{green['Wins']}-{green['Losses']}-{green['Pushes']}</div></div>
      <div class="mini green"><div class="mini-label">Green Units</div><div class="mini-value {'good' if green['Units Won'] >= 0 else 'bad'}">{format_signed_units(green['Units Won'])}</div></div>
      <div class="mini green"><div class="mini-label">Green ROI</div><div class="mini-value {'good' if green['ROI %'] >= 0 else 'bad'}">{green['ROI %']:.1f}%</div></div>
      <div class="mini"><div class="mini-label">Overall Record</div><div class="mini-value">{overall['Wins']}-{overall['Losses']}-{overall['Pushes']}</div></div>
    </div>
    """, unsafe_allow_html=True)


def render_pick_card(rank, row):
    play_type = display_bet_type(row.get("Play Type", "Pick"))
    game = safe_text(row.get("Game", ""))
    play = safe_text(row.get("Play", ""))
    odds = safe_text(row.get("Odds/Line", ""))
    score = safe_float(row.get("Score", 0), 0)

    is_k_play = bool(re.search(r"\d", play)) and any(x in play.upper() for x in ["OVER", "UNDER", "LEAN", "STRONG"])
    if is_k_play:
        details = extract_k_details(play)
        pitcher = details.get("pitcher", "")
        url = pitcher_headshot_url(pitcher)
        headshot_html = f'<img class="headshot" src="{url}" alt="{esc(pitcher)} headshot">' if url else ""
        line_value = details.get("line", "") or extract_k_line(odds).replace("Line", "").strip() or odds.replace("Line", "").strip()
        projection_value = details.get("projection", "")
        title = f"""
        <div class="pick-media">{headshot_html}<div>
          <div class="pick-title">{esc(pitcher)}</div>
          <div class="muted">{esc(game)}</div>
        </div></div>
        <div class="k-detail-grid">
          <div class="k-detail"><span class="k-label">Bet Type</span><span class="k-value">{esc(play_type)}</span></div>
          <div class="k-detail"><span class="k-label">Line</span><span class="k-value">{esc(line_value)}</span></div>
          <div class="k-detail"><span class="k-label">Projected Ks</span><span class="k-value">{esc(projection_value)}</span></div>
          <div class="k-detail"><span class="k-label">Score</span><span class="k-value">{score:.0f}</span></div>
        </div>
        """
    else:
        odds_html = f"<span class='tiny'> · {esc(odds)}</span>" if odds else ""
        title = f'<div class="pick-title">{esc(play)}{odds_html}</div><div class="muted">{esc(game)}</div>'

    st.markdown(f"""
    <div class="pick-card"><div class="card-top"><span class="rank">#{rank}</span><span class="pill">{esc(play_type)} · SCORE {score:.0f}</span></div>{matchup_strip(game)}{title}</div>
    """, unsafe_allow_html=True)

def is_green_play(value, green_bet_types):
    key = normalize_bet_type_text(value)
    return bool(key and key in green_bet_types)


def prop_chip(label, value, should_highlight=False):
    cls = "chip green-prop" if should_highlight else "chip"
    return f'<div class="{cls}"><strong>{esc(label)}:</strong> {esc(value)}</div>'


def pitcher_prop_chip(label, value, score_value="", should_highlight=False):
    text = safe_text(value)
    if not text or text.lower().startswith("no "):
        return prop_chip(label, text or "No play", should_highlight)
    details = extract_k_details(text)
    bet_type = display_bet_type(details.get("bet_type") or normalize_bet_type_text(text) or label)
    pitcher = details.get("pitcher", "")
    projection = details.get("projection", "")
    line_value = details.get("line", "")
    score = safe_float(score_value, 0)
    score_html = f'<div class="k-detail"><span class="k-label">Score</span><span class="k-value">{score:.0f}</span></div>' if score > 0 else ""
    cls = "chip green-prop" if should_highlight else "chip"
    return f"""<div class="{cls}">
      <div class="pick-title" style="font-size:.88rem;">{esc(label)}: {esc(pitcher)}</div>
      <div class="k-detail-grid">
        <div class="k-detail"><span class="k-label">Bet Type</span><span class="k-value">{esc(bet_type)}</span></div>
        <div class="k-detail"><span class="k-label">Line</span><span class="k-value">{esc(line_value)}</span></div>
        <div class="k-detail"><span class="k-label">Projected Ks</span><span class="k-value">{esc(projection)}</span></div>
        {score_html}
      </div>
    </div>"""


def render_game_card(row, green_bet_types=None):
    green_bet_types = green_bet_types or set()
    game = make_game_label(row)
    better_ml = safe_text(row.get("Better ML", ""), "No ML play")
    ml_grade_raw = safe_text(row.get("ML Grade", ""))
    ml_grade = display_bet_type(ml_grade_raw)
    ml_odds = safe_text(row.get("ML Odds", ""))
    nrfi_raw = safe_text(row.get("NRFI Grade", "No NRFI/YRFI play"))
    nrfi = display_bet_type(nrfi_raw)
    away_k = safe_text(row.get("Away Pitcher K + Grade", ""), "No away K play")
    home_k = safe_text(row.get("Home Pitcher K + Grade", ""), "No home K play")
    ml_line = better_ml + (f" · {ml_grade}" if ml_grade else "") + (f" · {ml_odds}" if ml_odds else "")
    chips = "".join([
        prop_chip("Moneyline", ml_line, is_green_play(ml_grade_raw, green_bet_types)),
        prop_chip("NRFI/YRFI", nrfi, is_green_play(nrfi_raw, green_bet_types)),
        pitcher_prop_chip("Away K", away_k, row.get("Away Pitcher K Score", ""), is_green_play(away_k, green_bet_types)),
        pitcher_prop_chip("Home K", home_k, row.get("Home Pitcher K Score", ""), is_green_play(home_k, green_bet_types)),
    ])
    st.markdown(f"""
    <div class="game-card"><div class="game-title">{esc(game)}</div>{matchup_strip(game)}<div class="lines">{chips}</div></div>
    """, unsafe_allow_html=True)

def render_green_totals_card(green):
    st.markdown(f"""
    <div class="record-card"><div class="game-title">Green Bet Totals</div><div class="muted">Only bet types with a winning completed record are included.</div><div class="lines"><div class="chip"><strong>Record:</strong> {green['Wins']}-{green['Losses']}-{green['Pushes']} · {green['Total Bets']} bets</div><div class="chip"><strong>Win %:</strong> {green['Win %']:.1f}%</div><div class="chip"><strong>Units:</strong> {format_signed_units(green['Units Won'])}</div><div class="chip"><strong>ROI:</strong> {green['ROI %']:.1f}%</div></div></div>
    """, unsafe_allow_html=True)


def render_overall_card(overall):
    st.markdown(f"""
    <div class="record-card"><div class="game-title">Overall Record</div><div class="muted">All completed tracked plays.</div><div class="lines"><div class="chip"><strong>Record:</strong> {overall['Wins']}-{overall['Losses']}-{overall['Pushes']} · {overall['Total Bets']} bets</div><div class="chip"><strong>Win %:</strong> {overall['Win %']:.1f}%</div><div class="chip"><strong>Units:</strong> {format_signed_units(overall['Units Won'])}</div><div class="chip"><strong>ROI:</strong> {overall['ROI %']:.1f}%</div></div></div>
    """, unsafe_allow_html=True)


def render_records_table(summary_df):
    if summary_df.empty:
        st.info("No completed bets yet.")
        return
    summary_df = summary_df.copy().sort_values("Units Won", ascending=False).reset_index(drop=True)
    st.markdown('<div class="breakdown-grid">', unsafe_allow_html=True)
    for _, r in summary_df.iterrows():
        status = safe_text(r.get("Record Status", ""))
        cls = "green" if status == "Winning" else "yellow" if status == "Even" else "red"
        bet_type = display_bet_type(r.get("Bet Type", ""))
        record = f"{int(r['Wins'])}-{int(r['Losses'])}-{int(r['Pushes'])}"
        units = format_signed_units(r.get("Units Won", 0))
        win_pct = f"{safe_float(r.get('Win %', 0)):.1f}%"
        roi = f"{safe_float(r.get('ROI %', 0)):.1f}%"
        st.markdown(f"""
        <div class="breakdown-card {cls}">
          <div class="card-top"><div class="breakdown-title">{esc(bet_type)}</div><span class="pill">{esc(status.upper())}</span></div>
          <div class="breakdown-row">
            <div class="k-detail"><span class="k-label">Record</span><span class="k-value">{record}</span></div>
            <div class="k-detail"><span class="k-label">Win %</span><span class="k-value">{win_pct}</span></div>
            <div class="k-detail"><span class="k-label">Units</span><span class="k-value">{units}</span></div>
          </div>
          <div class="tiny" style="margin-top:.45rem;">ROI: {roi} · Total Bets: {int(r.get('Total Bets', 0))}</div>
        </div>
        """, unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

def main():
    render_hero()
    tracker_df = load_tracker(); slate_df = load_slate(); current_date = today_str()
    summary_df = build_record_summary(tracker_df); overall = build_totals(summary_df, green_only=False); green = build_totals(summary_df, green_only=True); green_bet_types = build_green_bet_type_set(summary_df)
    render_metrics(overall, green)
    tabs = st.tabs(["🔥 Today", "📋 Slate", "📊 Records", "ℹ️ About"])

    with tabs[0]:
        st.markdown('<div class="section-title">Today\'s Green Best Plays</div>', unsafe_allow_html=True)
        today_slate = slate_df[slate_df["Date Key"].astype(str) == current_date].copy() if not slate_df.empty else pd.DataFrame()
        best_df = build_best_plays(today_slate, green_bet_types)
        if best_df.empty:
            st.info("No green-bet best plays are available yet. Plays only appear here when their bet type has a winning completed record.")
        else:
            for idx, row in best_df.head(10).iterrows():
                render_pick_card(idx + 1, row)

    with tabs[1]:
        st.markdown('<div class="section-title">Today\'s Slate</div>', unsafe_allow_html=True)
        today_slate = slate_df[slate_df["Date Key"].astype(str) == current_date].copy() if not slate_df.empty else pd.DataFrame()
        if today_slate.empty:
            st.info("No games saved for today yet.")
        else:
            for _, row in today_slate.iterrows():
                render_game_card(row, green_bet_types)
        st.markdown('<div class="section-title">Past Saved Slates</div>', unsafe_allow_html=True)
        if not slate_df.empty:
            dates = sorted(slate_df["Date Key"].dropna().astype(str).unique(), reverse=True)
            selected = st.selectbox("Choose a date", dates, index=dates.index(current_date) if current_date in dates else 0)
            for _, row in slate_df[slate_df["Date Key"].astype(str) == selected].iterrows():
                render_game_card(row, green_bet_types)

    with tabs[2]:
        st.markdown('<div class="section-title">Green Record</div>', unsafe_allow_html=True)
        render_green_totals_card(green)
        with st.expander("Show overall record"):
            render_overall_card(overall)
        st.markdown('<div class="section-title">Bet Type Breakdown</div>', unsafe_allow_html=True)
        render_records_table(summary_df)

    with tabs[3]:
        st.markdown('<div class="section-title">About EZPZ Picks</div>', unsafe_allow_html=True)
        st.markdown("""
        <div class="info-card"><div class="game-title">How It Works</div><div class="muted">EZPZ Picks uses a private MLB model built around team hitting, pitcher stats, handedness splits, NRFI indicators, pitcher strikeout projections, odds, edge, volatility, and tracked results. The public board emphasizes green bet types — categories that are currently winning in the completed record.</div></div>
        <div class="footer-note">For entertainment and informational purposes only. Betting involves risk. Use your own judgment and never bet more than you can afford to lose.</div>
        """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()