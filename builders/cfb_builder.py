"""EZPZ Picks college-football game model.

A single projected-score distribution drives spread, moneyline, and total.
The engine blends preseason priors with opponent-adjusted current-season data,
then applies matchup, personnel, venue, travel, rest, weather, and market layers.

Primary public data integrations: ESPN public feeds, SportsDataverse open releases, and official National Weather Service forecasts. No sports-data API keys or paid subscriptions are required.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus
from typing import Any, Iterable

import numpy as np
import pandas as pd
import requests
import streamlit as st

from shared.modeling import american_implied_probability, clamp, expected_value_per_unit
from shared.storage import read_sheet, sheets_ready, write_sheet

MODEL_VERSION = "cfb-v1.4-missing-stats-fix-no-key-2026-07-18"
DEFAULT_SEASON = 2026
DEFAULT_PRIOR_SEASON = 2025
SIMULATIONS = 30000
BATCH_SIMULATIONS = int(os.getenv("EZPZ_CFB_BATCH_SIMULATIONS", "8000"))
AUTO_SLATE_BATCH_SIZE = int(os.getenv("EZPZ_CFB_AUTO_SLATE_BATCH_SIZE", "3"))
ESPN_SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
ESPN_CORE_BASE = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football"
SPORTSDATAVERSE_RELEASE_API = "https://api.github.com/repos/sportsdataverse/sportsdataverse-data/releases/tags"
SPORTSDATAVERSE_DOWNLOAD_BASE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
CACHE_DIR = Path(os.getenv("EZPZ_CFB_CACHE_DIR", "/tmp/ezpz_cfb_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OPEN_DATA_DIR = Path(os.getenv("EZPZ_CFB_OPEN_DATA_DIR", str(CACHE_DIR / "open_data")))
OPEN_DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_SECONDS = int(os.getenv("EZPZ_CFB_CACHE_SECONDS", "21600"))
AUTO_RATINGS_MAX_AGE_SECONDS = int(os.getenv("EZPZ_CFB_RATINGS_MAX_AGE_SECONDS", "21600"))
ALLOW_BLOCKING_OPEN_DATA = os.getenv("EZPZ_CFB_ALLOW_BLOCKING_OPEN_DATA", "0").strip().lower() in {"1", "true", "yes"}
_OPEN_DATA_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ezpz-cfb-open-data")
_OPEN_DATA_JOBS: dict[str, Any] = {}
_OPEN_DATA_JOB_LOCK = threading.Lock()

RATINGS_TAB = "cfb_team_ratings"
SLATE_TAB = "cfb_daily_slate"
TRACKER_TAB = "cfb_bet_tracker"
SCHEDULE_TAB = "cfb_schedule"
PERSONNEL_TAB = "cfb_personnel_snapshots"
CALIBRATION_TAB = "cfb_calibration"
MODEL_LOG_TAB = "cfb_model_change_log"

RATING_COLUMNS = [
    "Team", "Conference", "Classification", "Season", "Projection Week",
    "Previous Season Weight", "Current Season Weight", "Preseason Rating",
    "Power Rating", "Offense Rating", "Defense Rating", "Special Teams Rating",
    "EPA/PPA Offense", "EPA/PPA Defense Edge", "Success Rate Offense",
    "Success Rate Defense Edge", "Pass EPA/PPA", "Pass Defense Edge",
    "Rush EPA/PPA", "Rush Defense Edge", "Explosiveness Offense",
    "Explosiveness Defense Edge", "Havoc Allowed", "Havoc Created",
    "Finishing Drives Offense", "Finishing Drives Defense Edge",
    "Field Position Offense", "Field Position Defense Edge", "Line Yards Offense",
    "Line Yards Defense Edge", "Power Success", "Stuff Rate Edge",
    "Standard Downs Offense", "Standard Downs Defense Edge",
    "Passing Downs Offense", "Passing Downs Defense Edge", "Points Per Drive",
    "Points Allowed Per Drive", "Points Per Game", "Points Allowed Per Game",
    "Yards Per Play", "Yards Per Play Allowed", "Third Down Rate",
    "Third Down Defense Edge", "Red Zone TD Rate", "Red Zone Defense Edge",
    "Turnover Rate", "Takeaway Rate", "Sack Rate Allowed", "Sack Rate Created",
    "Pace Seconds Per Play", "Plays Per Game", "Possessions Per Game",
    "SP+ Rating", "FPI Rating", "Elo Rating", "SRS Rating",
    "Returning Production", "Returning Passing", "Returning Receiving",
    "Returning Rushing", "Talent Rating", "Recruiting Rating", "Portal Rating",
    "QB Continuity", "Coaching Continuity", "Coordinator Continuity",
    "Games", "FBS Games", "Data Confidence", "Advanced Data Available",
    "Roster Data Available", "Source", "Updated",
]

SLATE_COLUMNS = [
    "Date", "Season", "Week", "Game ID", "Game", "Away Team", "Home Team",
    "Neutral Site", "Conference Game", "Projected Away", "Projected Home",
    "Projected Margin", "Projected Total", "Expected Possessions",
    "Away Score P10", "Away Score P90", "Home Score P10", "Home Score P90",
    "Opening Home Spread", "Opening Total", "Market Home Spread", "Market Total", "Away ML", "Home ML",
    "Spread Pick", "Spread Probability", "Spread Push Probability", "Spread Edge",
    "Spread Grade", "Spread Confluence", "Total Pick", "Total Probability",
    "Total Push Probability", "Total Edge", "Total Grade", "Total Confluence",
    "ML Pick", "ML Probability", "ML Odds", "ML Implied Probability", "ML Edge",
    "ML Expected Value", "ML Grade", "ML Confluence", "Reliability",
    "Data Confidence", "Personnel Confidence", "Weather Confidence",
    "Previous Season Weight", "Current Season Weight", "Home Field Advantage",
    "League HFA", "Venue HFA", "Travel Adjustment", "Time Zone Adjustment",
    "Altitude Adjustment", "Rest Adjustment", "Weather Adjustment",
    "Temperature", "Wind", "Precipitation Probability", "Roof", "Stadium",
    "Away QB Adjustment", "Home QB Adjustment", "Away Injury Adjustment",
    "Home Injury Adjustment", "Model Version", "Notes",
]

TRACKER_COLUMNS = [
    "Date", "Season", "Week", "Game ID", "Game", "Bet Type", "Selection",
    "Odds/Line", "Model Probability", "Push Probability", "Implied Probability",
    "Edge", "Expected Value", "Grade", "Confluence", "Result", "Units",
    "Closing Line", "Closing Line Value", "Reliability", "Data Confidence",
    "Personnel Confidence", "Projected Away", "Projected Home", "Actual Away",
    "Actual Home", "Margin Residual", "Total Residual", "Model Version", "Notes",
]

SCHEDULE_COLUMNS = [
    "Season", "Week", "Season Type", "Game Date", "Game Time", "Away Team",
    "Home Team", "Away Conference", "Home Conference", "Away Classification",
    "Home Classification", "Away Score", "Home Score", "Completed", "Neutral Site",
    "Conference Game", "Venue ID", "Stadium", "Location", "Latitude", "Longitude",
    "Elevation", "Capacity", "Roof", "Surface", "Away Rest", "Home Rest", "Away ML", "Home ML",
    "Opening Home Spread", "Opening Total", "Home Spread", "Total", "Line Provider", "Temperature", "Wind",
    "Precipitation Probability", "Weather Source", "Game ID",
]

PERSONNEL_COLUMNS = [
    "Date", "Season", "Week", "Game ID", "Team", "Expected QB", "QB Confirmed",
    "QB Continuity", "QB Adjustment", "OL Adjustment", "Skill Adjustment",
    "Defensive Line Adjustment", "Linebacker Adjustment", "Secondary Adjustment",
    "Kicker Adjustment", "Special Teams Adjustment", "Coaching Continuity",
    "Coordinator Continuity", "Availability Confidence", "Source", "Notes",
    "Model Version",
]

CALIBRATION_COLUMNS = [
    "Date", "Season", "Week", "Game ID", "Game", "Projected Away",
    "Projected Home", "Projected Margin", "Projected Total", "Actual Away",
    "Actual Home", "Actual Margin", "Actual Total", "Margin Residual",
    "Total Residual", "Projected Win Probability", "Home Won", "Market Home Spread",
    "Market Total", "Spread Closing Value", "Total Closing Value", "Reliability",
    "Model Version",
]

MODEL_LOG_COLUMNS = ["Date", "Model Version", "Change"]

NEUTRAL = {
    "EPA/PPA Offense": 0.0, "EPA/PPA Defense Edge": 0.0,
    "Success Rate Offense": 0.42, "Success Rate Defense Edge": 0.0,
    "Pass EPA/PPA": 0.0, "Pass Defense Edge": 0.0,
    "Rush EPA/PPA": 0.0, "Rush Defense Edge": 0.0,
    "Explosiveness Offense": 1.0, "Explosiveness Defense Edge": 0.0,
    "Havoc Allowed": 0.16, "Havoc Created": 0.16,
    "Finishing Drives Offense": 4.2, "Finishing Drives Defense Edge": 0.0,
    "Field Position Offense": 29.0, "Field Position Defense Edge": 0.0,
    "Line Yards Offense": 2.7, "Line Yards Defense Edge": 0.0,
    "Power Success": 0.68, "Stuff Rate Edge": 0.0,
    "Standard Downs Offense": 0.0, "Standard Downs Defense Edge": 0.0,
    "Passing Downs Offense": 0.0, "Passing Downs Defense Edge": 0.0,
    "Points Per Drive": 2.3, "Points Allowed Per Drive": 2.3,
    "Points Per Game": 28.0, "Points Allowed Per Game": 28.0,
    "Yards Per Play": 5.7, "Yards Per Play Allowed": 5.7,
    "Third Down Rate": 0.40, "Third Down Defense Edge": 0.0,
    "Red Zone TD Rate": 0.62, "Red Zone Defense Edge": 0.0,
    "Turnover Rate": 0.12, "Takeaway Rate": 0.12,
    "Sack Rate Allowed": 0.065, "Sack Rate Created": 0.065,
    "Pace Seconds Per Play": 27.5, "Plays Per Game": 69.0,
    "Possessions Per Game": 12.0, "SP+ Rating": 0.0, "FPI Rating": 0.0,
    "Elo Rating": 1500.0, "SRS Rating": 0.0, "Returning Production": 0.50,
    "Returning Passing": 0.50, "Returning Receiving": 0.50,
    "Returning Rushing": 0.50, "Talent Rating": 0.0, "Recruiting Rating": 0.0,
    "Portal Rating": 0.0, "QB Continuity": 0.50, "Coaching Continuity": 0.75,
    "Coordinator Continuity": 0.67,
}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        number = float(value)
        return number if math.isfinite(number) else float(default)
    except Exception:
        return float(default)


def _pct(value: Any, default: float = 0.0) -> float:
    number = _num(value, default)
    if abs(number) > 1.5:
        number /= 100.0
    return number


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "completed"}


def _text(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _dig(data: Any, *paths: str, default: Any = None) -> Any:
    for path in paths:
        current = data
        found = True
        for token in path.split("."):
            if isinstance(current, dict) and token in current:
                current = current[token]
            else:
                found = False
                break
        if found and current is not None:
            return current
    return default


def _first(data: dict[str, Any], names: Iterable[str], default: Any = None) -> Any:
    lower = {str(k).lower().replace("_", "").replace(" ", ""): v for k, v in data.items()}
    for name in names:
        key = name.lower().replace("_", "").replace(" ", "")
        if key in lower and lower[key] is not None:
            return lower[key]
    return default


def _numeric_series(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    """Return a numeric Series aligned to *frame*, even when a source column is absent.

    Public ESPN/open-data responses are not schema-stable across seasons and weeks.
    ``DataFrame.get(column, 0)`` returns a scalar when the column is missing; calling
    pandas Series methods such as ``replace`` or ``fillna`` on that scalar caused the
    automatic rating builder to fail before any projection could render.
    """
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce")
    return pd.Series(default, index=frame.index, dtype=float)


def _z(series: pd.Series | Iterable[Any] | Any, neutral: float = 0.0) -> pd.Series:
    if isinstance(series, pd.Series):
        values = pd.to_numeric(series, errors="coerce")
    else:
        try:
            values = pd.Series(series, dtype=float)
        except Exception:
            values = pd.Series(dtype=float)
    std = float(values.std(ddof=0)) if len(values) else float("nan")
    if not math.isfinite(std) or std < 1e-9:
        return pd.Series(neutral, index=values.index, dtype=float)
    return (values - float(values.mean())) / std


def _normalize_team(name: Any) -> str:
    return " ".join(_text(name).replace("&", "and").split())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _season_weights(week: int) -> tuple[float, float]:
    current = {0: 0.0, 1: 0.0, 2: 0.25, 3: 0.40, 4: 0.55, 5: 0.65, 6: 0.75}.get(int(week))
    if current is None:
        current = min(0.88, 0.75 + 0.01 * max(0, int(week) - 6))
    return round(1.0 - current, 3), round(current, 3)


def _safe_secret(name: str, default: str = "") -> str:
    value = ""
    try:
        value = _text(st.secrets.get(name, ""), "")
    except Exception:
        value = ""
    return _text(value or os.getenv(name, default), default)


def _cache_path(endpoint: str, params: dict[str, Any]) -> Path:
    raw = json.dumps([endpoint, sorted((str(k), str(v)) for k, v in params.items())], sort_keys=True)
    return CACHE_DIR / f"{hashlib.sha256(raw.encode()).hexdigest()}.json"



def _public_json_get(url: str, params: dict[str, Any] | None = None, *, optional: bool = False,
                     max_age: int = CACHE_SECONDS) -> Any:
    """Fetch a public JSON resource with disk caching and stale-cache fallback."""
    params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    path = _cache_path(url, params)
    if path.exists() and (time.time() - path.stat().st_mtime) <= max_age:
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    try:
        response = requests.get(
            url,
            params=params,
            headers={
                "Accept": "application/json",
                "User-Agent": "EZPZ-Picks-NCAAF/1.2 (public-data model; contact admin@ezpzpicks.com)",
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        path.write_text(json.dumps(payload))
        return payload
    except Exception:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        if optional:
            return {} if url.endswith(".json") else []
        raise


def _nested_items(payload: Any, path: list[str]) -> list[dict[str, Any]]:
    current = payload
    for token in path:
        if not isinstance(current, dict):
            return []
        current = current.get(token)
    return current if isinstance(current, list) else []


@st.cache_data(ttl=86400, show_spinner=False)
def _espn_team_index() -> dict[str, dict[str, Any]]:
    payload = _public_json_get(
        f"{ESPN_SITE_BASE}/teams",
        {"limit": 500, "groups": 80},
        optional=True,
        max_age=86400,
    )
    entries = _nested_items(payload, ["sports", "0", "leagues", "0", "teams"])
    if not entries and isinstance(payload, dict):
        try:
            entries = payload["sports"][0]["leagues"][0]["teams"]
        except Exception:
            entries = []
    output: dict[str, dict[str, Any]] = {}
    for entry in entries:
        team = entry.get("team") if isinstance(entry, dict) and isinstance(entry.get("team"), dict) else entry
        if not isinstance(team, dict):
            continue
        name = _normalize_team(_first(team, ["displayName", "shortDisplayName", "name", "location"]))
        if not name:
            continue
        output[name] = team
    return output


@st.cache_data(ttl=21600, show_spinner=False)
def _espn_teams_payload(season: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, team in _espn_team_index().items():
        venue = team.get("venue") if isinstance(team.get("venue"), dict) else {}
        address = venue.get("address") if isinstance(venue.get("address"), dict) else {}
        groups = team.get("groups") if isinstance(team.get("groups"), dict) else {}
        rows.append({
            "school": name,
            "conference": _text(_first(groups, ["name", "shortName", "abbreviation"], _first(team, ["conferenceName"], ""))),
            "classification": "fbs",
            "venueId": _text(_first(venue, ["id"], "")),
            "location": {
                "venueId": _text(_first(venue, ["id"], "")),
                "city": _text(_first(address, ["city"], "")),
                "state": _text(_first(address, ["state"], "")),
                "latitude": _num(_first(address, ["latitude"], np.nan), np.nan),
                "longitude": _num(_first(address, ["longitude"], np.nan), np.nan),
                "elevation": 0.0,
                "capacity": _num(_first(venue, ["capacity"], 0.0), 0.0),
                "timezone": "",
            },
            "espnId": _text(_first(team, ["id"], "")),
            "abbreviation": _text(_first(team, ["abbreviation"], "")),
        })
    return rows


def _espn_competitor(competition: dict[str, Any], side: str) -> dict[str, Any]:
    for competitor in competition.get("competitors", []) or []:
        if _text(competitor.get("homeAway")).lower() == side:
            return competitor
    competitors = competition.get("competitors", []) or []
    if not competitors:
        return {}
    return competitors[0] if side == "home" else competitors[-1]


def _team_name_from_competitor(competitor: dict[str, Any]) -> str:
    team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
    return _normalize_team(_first(team, ["displayName", "shortDisplayName", "name", "location"], ""))


def _parse_espn_line(odds: dict[str, Any], home: dict[str, Any], away: dict[str, Any]) -> dict[str, float | str]:
    home_team = home.get("team") if isinstance(home.get("team"), dict) else {}
    away_team = away.get("team") if isinstance(away.get("team"), dict) else {}
    home_names = {
        _text(_first(home_team, ["abbreviation"], "")).upper(),
        _text(_first(home_team, ["shortDisplayName", "name", "location"], "")).upper(),
        _text(_first(home_team, ["displayName"], "")).upper(),
    }
    away_names = {
        _text(_first(away_team, ["abbreviation"], "")).upper(),
        _text(_first(away_team, ["shortDisplayName", "name", "location"], "")).upper(),
        _text(_first(away_team, ["displayName"], "")).upper(),
    }

    def home_spread_from(obj: dict[str, Any]) -> float:
        if not isinstance(obj, dict):
            return np.nan
        home_odds = obj.get("homeTeamOdds") if isinstance(obj.get("homeTeamOdds"), dict) else {}
        away_odds = obj.get("awayTeamOdds") if isinstance(obj.get("awayTeamOdds"), dict) else {}
        explicit = _first(home_odds, ["spread", "line"], None)
        if explicit is not None:
            return _num(explicit, np.nan)
        details = _text(_first(obj, ["details", "spreadDetails"], ""))
        match = re.search(r"(.+?)\s+([+-]?\d+(?:\.\d+)?)$", details)
        if match:
            favorite = match.group(1).strip().upper()
            number = _num(match.group(2), np.nan)
            if any(name and (favorite == name or favorite in name or name in favorite) for name in home_names):
                return number
            if any(name and (favorite == name or favorite in name or name in favorite) for name in away_names):
                return -number
        spread = _num(_first(obj, ["spread"], np.nan), np.nan)
        if math.isfinite(spread):
            if _bool(home_odds.get("favorite")):
                return -abs(spread)
            if _bool(away_odds.get("favorite")):
                return abs(spread)
        return np.nan

    provider = odds.get("provider") if isinstance(odds.get("provider"), dict) else {}
    open_obj = odds.get("open") if isinstance(odds.get("open"), dict) else {}
    home_team_odds = odds.get("homeTeamOdds") if isinstance(odds.get("homeTeamOdds"), dict) else {}
    away_team_odds = odds.get("awayTeamOdds") if isinstance(odds.get("awayTeamOdds"), dict) else {}
    open_home = open_obj.get("homeTeamOdds") if isinstance(open_obj.get("homeTeamOdds"), dict) else {}
    open_away = open_obj.get("awayTeamOdds") if isinstance(open_obj.get("awayTeamOdds"), dict) else {}
    current_spread = home_spread_from(odds)
    opening_spread = home_spread_from(open_obj)
    current_total = _num(_first(odds, ["overUnder", "total"], np.nan), np.nan)
    opening_total = _num(_first(open_obj, ["overUnder", "total"], np.nan), np.nan)
    return {
        "provider": _text(_first(provider, ["name"], "ESPN public odds feed")),
        "home_spread": current_spread,
        "opening_home_spread": opening_spread if math.isfinite(opening_spread) else current_spread,
        "total": current_total,
        "opening_total": opening_total if math.isfinite(opening_total) else current_total,
        "home_ml": _num(_first(home_team_odds, ["moneyLine", "moneyline", "moneyLineOdds"], _first(open_home, ["moneyLine"], 0.0)), 0.0),
        "away_ml": _num(_first(away_team_odds, ["moneyLine", "moneyline", "moneyLineOdds"], _first(open_away, ["moneyLine"], 0.0)), 0.0),
    }


@st.cache_data(ttl=21600, show_spinner=False)
def _espn_events(season: int) -> list[dict[str, Any]]:
    """Load a season schedule without making week requests sequentially.

    ESPN sometimes returns only the active week for a season-wide scoreboard call.
    The fallback weeks are therefore fetched concurrently, keeping a cold start
    within a normal Streamlit request window.
    """
    events: dict[str, dict[str, Any]] = {}

    def fetch(params: dict[str, Any]) -> list[dict[str, Any]]:
        payload = _public_json_get(
            f"{ESPN_SITE_BASE}/scoreboard",
            params,
            optional=True,
            max_age=21600 if season >= date.today().year else 86400 * 30,
        )
        return payload.get("events", []) if isinstance(payload, dict) else []

    for season_type in (2, 3):
        for event in fetch({"dates": int(season), "limit": 1000, "groups": 80, "seasontype": season_type}):
            events[_text(event.get("id"))] = event

    if len(events) < 100:
        requests_to_make = [
            {"dates": int(season), "limit": 500, "groups": 80, "seasontype": season_type, "week": week}
            for season_type, max_week in ((2, 18), (3, 8))
            for week in range(0, max_week + 1)
        ]
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="ezpz-cfb-espn") as executor:
            futures = [executor.submit(fetch, params) for params in requests_to_make]
            for future in as_completed(futures):
                try:
                    for event in future.result():
                        events[_text(event.get("id"))] = event
                except Exception:
                    continue
    return list(events.values())


@st.cache_data(ttl=21600, show_spinner=False)
def _espn_games_payload(season: int) -> list[dict[str, Any]]:
    fbs_names = set(_espn_team_index())
    rows: list[dict[str, Any]] = []
    for event in _espn_events(season):
        competitions = event.get("competitions", []) or []
        if not competitions:
            continue
        comp = competitions[0]
        home = _espn_competitor(comp, "home")
        away = _espn_competitor(comp, "away")
        home_name = _team_name_from_competitor(home)
        away_name = _team_name_from_competitor(away)
        if not home_name or not away_name:
            continue
        home_team = home.get("team") if isinstance(home.get("team"), dict) else {}
        away_team = away.get("team") if isinstance(away.get("team"), dict) else {}
        venue = comp.get("venue") if isinstance(comp.get("venue"), dict) else {}
        address = venue.get("address") if isinstance(venue.get("address"), dict) else {}
        status = comp.get("status") if isinstance(comp.get("status"), dict) else event.get("status", {})
        status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
        season_obj = event.get("season") if isinstance(event.get("season"), dict) else {}
        week_obj = event.get("week") if isinstance(event.get("week"), dict) else {}
        season_type = _num(_first(season_obj.get("type", {}) if isinstance(season_obj.get("type"), dict) else season_obj, ["type", "id"], 2), 2)
        lines = comp.get("odds", []) or []
        line = _parse_espn_line(lines[0], home, away) if lines else {
            "provider": "", "home_spread": np.nan, "opening_home_spread": np.nan,
            "total": np.nan, "opening_total": np.nan, "home_ml": 0.0, "away_ml": 0.0,
        }
        home_conf = home.get("conference") if isinstance(home.get("conference"), dict) else {}
        away_conf = away.get("conference") if isinstance(away.get("conference"), dict) else {}
        completed = _bool(_first(status_type, ["completed"], False))
        rows.append({
            "id": _text(event.get("id")),
            "season": int(_num(_first(season_obj, ["year"], season), season)),
            "week": int(_num(_first(week_obj, ["number"], _dig(event, "week.number", default=0)), 0)),
            "seasonType": "postseason" if int(season_type) == 3 else "regular",
            "startDate": _text(_first(comp, ["date"], event.get("date", ""))),
            "awayTeam": away_name,
            "homeTeam": home_name,
            "awayConference": _text(_first(away_conf, ["name", "shortName", "abbreviation"], _first(away_team, ["conferenceName"], ""))),
            "homeConference": _text(_first(home_conf, ["name", "shortName", "abbreviation"], _first(home_team, ["conferenceName"], ""))),
            "awayClassification": "fbs" if away_name in fbs_names else "fcs",
            "homeClassification": "fbs" if home_name in fbs_names else "fcs",
            "awayPoints": _num(away.get("score"), np.nan) if completed else None,
            "homePoints": _num(home.get("score"), np.nan) if completed else None,
            "neutralSite": _bool(comp.get("neutralSite")),
            "conferenceGame": _bool(comp.get("conferenceCompetition")),
            "venueId": _text(_first(venue, ["id"], "")),
            "venue": _text(_first(venue, ["fullName", "name"], "")),
            "location": ", ".join(v for v in [_text(address.get("city")), _text(address.get("state"))] if v),
            "latitude": _num(_first(address, ["latitude"], np.nan), np.nan),
            "longitude": _num(_first(address, ["longitude"], np.nan), np.nan),
            "elevation": 0.0,
            "capacity": _num(_first(venue, ["capacity"], 0.0), 0.0),
            "roof": "Indoor/Dome" if _bool(_first(venue, ["indoor", "dome"], False)) else "Outdoor/Unknown",
            "surface": "Grass" if _bool(_first(venue, ["grass"], False)) else "",
            "lineProvider": line["provider"],
            "openingHomeSpread": line["opening_home_spread"],
            "openingTotal": line["opening_total"],
            "homeSpread": line["home_spread"],
            "total": line["total"],
            "homeMoneyline": line["home_ml"],
            "awayMoneyline": line["away_ml"],
        })
    return rows


@st.cache_data(ttl=21600, show_spinner=False)
def _espn_venues_payload(season: int) -> list[dict[str, Any]]:
    venues: dict[str, dict[str, Any]] = {}
    for game in _espn_games_payload(season):
        venue_id = _text(game.get("venueId"))
        if not venue_id:
            continue
        location = _text(game.get("location"))
        city, state = (location.split(",", 1) + [""])[:2] if location else ("", "")
        venues[venue_id] = {
            "id": venue_id,
            "name": _text(game.get("venue")),
            "city": city.strip(),
            "state": state.strip(),
            "latitude": _num(game.get("latitude"), np.nan),
            "longitude": _num(game.get("longitude"), np.nan),
            "elevation": _num(game.get("elevation"), 0.0),
            "capacity": _num(game.get("capacity"), 0.0),
            "dome": "indoor" in _text(game.get("roof")).lower() or "dome" in _text(game.get("roof")).lower(),
            "surface": _text(game.get("surface")),
        }
    return list(venues.values())


@st.cache_data(ttl=21600, show_spinner=False)
def _espn_lines_payload(season: int) -> list[dict[str, Any]]:
    rows = []
    for game in _espn_games_payload(season):
        rows.append({
            "id": game.get("id"),
            "homeTeam": game.get("homeTeam"),
            "awayTeam": game.get("awayTeam"),
            "lines": [{
                "provider": {"name": game.get("lineProvider", "ESPN public odds feed")},
                "spreadOpen": game.get("openingHomeSpread"),
                "overUnderOpen": game.get("openingTotal"),
                "spread": game.get("homeSpread"),
                "overUnder": game.get("total"),
                "homeMoneyline": game.get("homeMoneyline"),
                "awayMoneyline": game.get("awayMoneyline"),
            }],
        })
    return rows


def _release_asset_url(tag: str, season: int, preferred_tokens: tuple[str, ...]) -> str:
    payload = _public_json_get(
        f"{SPORTSDATAVERSE_RELEASE_API}/{tag}",
        optional=True,
        max_age=86400,
    )
    assets = payload.get("assets", []) if isinstance(payload, dict) else []
    candidates = []
    for asset in assets:
        name = _text(asset.get("name")).lower()
        if str(season) not in name or not name.endswith(".parquet"):
            continue
        score = sum(1 for token in preferred_tokens if token.lower() in name)
        candidates.append((score, _text(asset.get("browser_download_url"))))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    common_names = []
    if tag == "cfbfastR_cfb_pbp":
        common_names = [f"play_by_play_{season}.parquet"]
    elif tag == "espn_cfb_rosters":
        common_names = [f"rosters_{season}.parquet", f"roster_{season}.parquet", f"espn_cfb_rosters_{season}.parquet"]
    return f"{SPORTSDATAVERSE_DOWNLOAD_BASE}/{tag}/{common_names[0]}" if common_names else ""


def _download_open_asset_now(tag: str, season: int, preferred_tokens: tuple[str, ...]) -> Path | None:
    path = OPEN_DATA_DIR / f"{tag}_{season}.parquet"
    freshness = 21600 if season >= date.today().year else 86400 * 30
    if path.exists() and path.stat().st_size > 1024 and time.time() - path.stat().st_mtime <= freshness:
        return path
    url = _release_asset_url(tag, season, preferred_tokens)
    if not url:
        return path if path.exists() else None
    temp = path.with_suffix(".tmp")
    try:
        with requests.get(
            url,
            stream=True,
            timeout=(8, 180),
            headers={"User-Agent": "EZPZ-Picks-NCAAF/1.3 public-data cache warmer"},
        ) as response:
            response.raise_for_status()
            with temp.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        if temp.stat().st_size < 1024:
            raise RuntimeError("downloaded open-data file was unexpectedly small")
        temp.replace(path)
        return path
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass
        return path if path.exists() and path.stat().st_size > 1024 else None


def _queue_open_asset(tag: str, season: int, preferred_tokens: tuple[str, ...]) -> None:
    """Warm large SportsDataverse files in one background worker.

    The first projection uses fast ESPN result-based ratings immediately. Once
    the parquet cache arrives, a later rerun automatically upgrades ratings to
    the full advanced-variable version without any user setup step.
    """
    job_key = f"{tag}:{season}"
    with _OPEN_DATA_JOB_LOCK:
        existing = _OPEN_DATA_JOBS.get(job_key)
        if existing is not None and not existing.done():
            return
        _OPEN_DATA_JOBS[job_key] = _OPEN_DATA_EXECUTOR.submit(
            _download_open_asset_now, tag, season, preferred_tokens
        )


def _download_open_asset(tag: str, season: int, preferred_tokens: tuple[str, ...]) -> Path | None:
    path = OPEN_DATA_DIR / f"{tag}_{season}.parquet"
    freshness = 21600 if season >= date.today().year else 86400 * 30
    if path.exists() and path.stat().st_size > 1024:
        if time.time() - path.stat().st_mtime > freshness:
            _queue_open_asset(tag, season, preferred_tokens)
        return path
    if ALLOW_BLOCKING_OPEN_DATA:
        return _download_open_asset_now(tag, season, preferred_tokens)
    _queue_open_asset(tag, season, preferred_tokens)
    return None


def _advanced_cache_ready(season: int) -> bool:
    pbp = OPEN_DATA_DIR / f"cfbfastR_cfb_pbp_{season}.parquet"
    roster = OPEN_DATA_DIR / f"espn_cfb_rosters_{season}.parquet"
    return (pbp.exists() and pbp.stat().st_size > 1024) or (roster.exists() and roster.stat().st_size > 1024)


def _read_open_parquet(path: Path | None, requested_aliases: dict[str, tuple[str, ...]]) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        import polars as pl
        scan = pl.scan_parquet(str(path))
        names = set(scan.collect_schema().names())
        selected = []
        rename = {}
        for canonical, aliases in requested_aliases.items():
            found = next((alias for alias in aliases if alias in names), None)
            if found:
                selected.append(found)
                rename[found] = canonical
        if not selected:
            return pd.DataFrame()
        frame = scan.select(selected).collect()
        frame = frame.rename(rename)
        return pd.DataFrame(frame.to_dicts())
    except Exception:
        return pd.DataFrame()


def _open_pbp_frame(season: int) -> pd.DataFrame:
    path = _download_open_asset("cfbfastR_cfb_pbp", season, ("play_by_play", "pbp"))
    aliases = {
        "season": ("season",), "week": ("week",), "game_id": ("game_id", "id_game"),
        "offense": ("offense_play", "offense", "posteam"),
        "defense": ("defense_play", "defense", "defteam"),
        "epa": ("epa", "ppa"), "success": ("success",),
        "pass": ("pass", "pass_play", "qb_dropback"), "rush": ("rush", "rush_play"),
        "play_type": ("play_type", "plays_type_text"), "play_text": ("play_text", "plays_text", "text"),
        "yards": ("yards_gained", "stat_yardage", "plays_stat_yardage"),
        "down": ("down", "start_down"), "distance": ("distance", "start_distance"),
        "yards_to_goal": ("yards_to_goal", "start_yards_to_endzone"),
        "drive_id": ("drive_id",), "drive_points": ("drive_points", "drive_result_points"),
        "drive_start_yards_to_goal": ("drive_start_yards_to_goal",),
        "turnover": ("turnover",), "interception": ("interception", "interception_thrown"),
        "fumble_lost": ("fumble_lost",), "sack": ("sack",), "tfl": ("tfl", "tackle_for_loss"),
        "scoring_opp": ("scoring_opp", "scoring_opportunity"),
        "first_down": ("first_down",), "touchdown": ("touchdown",),
        "period": ("period",), "clock_minutes": ("clock_minutes",), "clock_seconds": ("clock_seconds",),
        "passer": ("passer_player_name", "passer", "passer_name"),
    }
    return _read_open_parquet(path, aliases)


def _series(frame: pd.DataFrame, column: str, default: Any = np.nan) -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series(default, index=frame.index)


def _bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    numeric = pd.to_numeric(series, errors="coerce")
    text = series.astype(str).str.lower()
    return (numeric.fillna(0) != 0) | text.isin(["true", "yes", "y", "1"])


def _line_yards_value(yards: float) -> float:
    if yards < 0:
        return 1.2 * yards
    if yards <= 4:
        return yards
    if yards <= 10:
        return 4.0 + 0.5 * (yards - 4.0)
    return 7.0


def _pbp_team_metrics(season: int, through_week: int | None) -> pd.DataFrame:
    frame = _open_pbp_frame(season).copy()
    if frame.empty:
        return pd.DataFrame()
    if "week" in frame.columns and through_week is not None:
        frame = frame[pd.to_numeric(frame["week"], errors="coerce") < int(through_week)]
    frame["offense"] = _series(frame, "offense", "").map(_normalize_team)
    frame["defense"] = _series(frame, "defense", "").map(_normalize_team)
    frame = frame[(frame["offense"] != "") & (frame["defense"] != "")].copy()
    if frame.empty:
        return frame
    play_type = _series(frame, "play_type", "").astype(str).str.lower()
    play_text = _series(frame, "play_text", "").astype(str).str.lower()
    yards = pd.to_numeric(_series(frame, "yards", 0.0), errors="coerce").fillna(0.0)
    down = pd.to_numeric(_series(frame, "down", np.nan), errors="coerce")
    distance = pd.to_numeric(_series(frame, "distance", np.nan), errors="coerce")
    ytg = pd.to_numeric(_series(frame, "yards_to_goal", np.nan), errors="coerce")
    epa = pd.to_numeric(_series(frame, "epa", np.nan), errors="coerce")
    is_pass = _bool_series(_series(frame, "pass", False)) | play_type.str.contains("pass|sack")
    is_rush = _bool_series(_series(frame, "rush", False)) | play_type.str.contains("rush|run")
    scrimmage = is_pass | is_rush | play_type.str.contains("penalty")
    frame = frame[scrimmage].copy()
    yards = yards.loc[frame.index]; down = down.loc[frame.index]; distance = distance.loc[frame.index]; ytg = ytg.loc[frame.index]
    epa = epa.loc[frame.index]; is_pass = is_pass.loc[frame.index]; is_rush = is_rush.loc[frame.index]
    if epa.notna().sum() < max(25, len(frame) * 0.25):
        # Transparent open-data proxy when the release lacks precomputed EPA.
        epa = (
            0.08 * yards
            + 0.75 * ((yards >= distance.fillna(99)) & distance.notna()).astype(float)
            - 0.40 * (yards < 0).astype(float)
            + 0.20 * (ytg <= 20).fillna(False).astype(float)
        )
    existing_success = _series(frame, "success", np.nan)
    success = _bool_series(existing_success) if existing_success.notna().any() else (
        ((down == 1) & (yards >= 0.50 * distance))
        | ((down == 2) & (yards >= 0.70 * distance))
        | (down >= 3) & (yards >= distance)
    )
    turnover = (
        _bool_series(_series(frame, "turnover", False))
        | _bool_series(_series(frame, "interception", False))
        | _bool_series(_series(frame, "fumble_lost", False))
        | play_text.str.contains("intercepted|fumble.*lost")
    )
    sack = _bool_series(_series(frame, "sack", False)) | play_type.str.contains("sack") | play_text.str.contains(" sacked")
    tfl = _bool_series(_series(frame, "tfl", False)) | ((yards < 0) & is_rush)
    touchdown = _bool_series(_series(frame, "touchdown", False)) | play_text.str.contains("touchdown")
    scoring_opp = _bool_series(_series(frame, "scoring_opp", False)) | (ytg <= 40).fillna(False)
    standard_down = (down == 1) | ((down == 2) & (distance <= 7)) | ((down >= 3) & (distance <= 4))
    power = is_rush & (down >= 3) & (distance <= 2)
    third = down == 3
    red_zone = ytg <= 20
    frame["_epa"] = epa
    frame["_success"] = success.astype(float)
    frame["_pass"] = is_pass.astype(float)
    frame["_rush"] = is_rush.astype(float)
    frame["_yards"] = yards
    frame["_turnover"] = turnover.astype(float)
    frame["_sack"] = sack.astype(float)
    frame["_tfl"] = tfl.astype(float)
    frame["_touchdown"] = touchdown.astype(float)
    frame["_scoring_opp"] = scoring_opp.astype(float)
    frame["_standard"] = standard_down.astype(float)
    frame["_power"] = power.astype(float)
    frame["_power_success"] = (power & (yards >= distance)).astype(float)
    frame["_third"] = third.astype(float)
    frame["_third_success"] = (third & (yards >= distance)).astype(float)
    frame["_red_zone"] = red_zone.astype(float)
    frame["_rz_td"] = (red_zone & touchdown).astype(float)
    frame["_line_yards"] = [_line_yards_value(float(v)) for v in yards]
    frame["_explosive"] = ((is_pass & (yards >= 20)) | (is_rush & (yards >= 10))).astype(float)
    frame["_field_start"] = 100.0 - pd.to_numeric(_series(frame, "drive_start_yards_to_goal", np.nan), errors="coerce")
    if "game_id" not in frame.columns:
        frame["game_id"] = frame.index.astype(str)
    if "drive_id" not in frame.columns:
        frame["drive_id"] = frame["game_id"].astype(str) + "-" + frame.groupby("game_id").cumcount().floordiv(6).astype(str)

    rows: list[dict[str, Any]] = []
    teams = sorted(set(frame["offense"]) | set(frame["defense"]))
    for team in teams:
        off = frame[frame["offense"] == team]
        deff = frame[frame["defense"] == team]
        if off.empty and deff.empty:
            continue
        games = max(1, off["game_id"].nunique())
        drives = max(1, off["drive_id"].astype(str).nunique())
        def mean(df: pd.DataFrame, col: str, fallback: float = np.nan, mask: pd.Series | None = None) -> float:
            values = pd.to_numeric(df.loc[mask, col] if mask is not None else df[col], errors="coerce") if col in df.columns else pd.Series(dtype=float)
            return float(values.mean()) if len(values) and math.isfinite(_num(values.mean(), np.nan)) else fallback
        off_pass = off["_pass"] > 0
        off_rush = off["_rush"] > 0
        def_pass = deff["_pass"] > 0
        def_rush = deff["_rush"] > 0
        scoring_drives = off.groupby("drive_id")["_scoring_opp"].max() if not off.empty else pd.Series(dtype=float)
        drive_points = pd.to_numeric(_series(off, "drive_points", np.nan), errors="coerce")
        finishing = float(drive_points[off["_scoring_opp"] > 0].mean()) if drive_points.notna().any() and (off["_scoring_opp"] > 0).any() else np.nan
        if not math.isfinite(_num(finishing, np.nan)):
            rz_rate = mean(off, "_rz_td", 0.62, off["_red_zone"] > 0)
            finishing = 3.0 + 3.2 * clamp(rz_rate, 0.0, 1.0)
        def_drive_points = pd.to_numeric(_series(deff, "drive_points", np.nan), errors="coerce")
        finishing_def = float(def_drive_points[deff["_scoring_opp"] > 0].mean()) if def_drive_points.notna().any() and (deff["_scoring_opp"] > 0).any() else np.nan
        if not math.isfinite(_num(finishing_def, np.nan)):
            rz_def = mean(deff, "_rz_td", 0.62, deff["_red_zone"] > 0)
            finishing_def = 3.0 + 3.2 * clamp(rz_def, 0.0, 1.0)
        rows.append({
            "Team": team,
            "EPA/PPA Offense": mean(off, "_epa", 0.0),
            "EPA/PPA Defense Raw": mean(deff, "_epa", 0.0),
            "Success Rate Offense": mean(off, "_success", 0.42),
            "Success Rate Defense Raw": mean(deff, "_success", 0.42),
            "Pass EPA/PPA": mean(off, "_epa", 0.0, off_pass),
            "Pass Defense Raw": mean(deff, "_epa", 0.0, def_pass),
            "Rush EPA/PPA": mean(off, "_epa", 0.0, off_rush),
            "Rush Defense Raw": mean(deff, "_epa", 0.0, def_rush),
            "Explosiveness Offense": mean(off, "_explosive", 0.12),
            "Explosiveness Defense Raw": mean(deff, "_explosive", 0.12),
            "Advanced Plays": len(off),
            "Advanced Drives": drives,
            "Line Yards Offense": mean(off, "_line_yards", 2.7, off_rush),
            "Line Yards Defense Raw": mean(deff, "_line_yards", 2.7, def_rush),
            "Power Success": mean(off, "_power_success", 0.68, off["_power"] > 0),
            "Stuff Rate Offense Raw": mean(off, "_tfl", 0.18, off_rush),
            "Stuff Rate Defense Raw": mean(deff, "_tfl", 0.18, def_rush),
            "Havoc Allowed": mean(off, "_turnover", 0.0) + mean(off, "_sack", 0.0) + mean(off, "_tfl", 0.0),
            "Havoc Created": mean(deff, "_turnover", 0.0) + mean(deff, "_sack", 0.0) + mean(deff, "_tfl", 0.0),
            "Standard Downs Offense": mean(off, "_epa", 0.0, off["_standard"] > 0),
            "Standard Downs Defense Raw": mean(deff, "_epa", 0.0, deff["_standard"] > 0),
            "Passing Downs Offense": mean(off, "_epa", 0.0, off["_standard"] <= 0),
            "Passing Downs Defense Raw": mean(deff, "_epa", 0.0, deff["_standard"] <= 0),
            "Finishing Drives Offense": finishing,
            "Finishing Drives Defense Raw": finishing_def,
            "Field Position Offense": mean(off, "_field_start", 29.0),
            "Field Position Defense Raw": mean(deff, "_field_start", 29.0),
            "Yards Per Play": mean(off, "_yards", 5.7),
            "Yards Per Play Allowed": mean(deff, "_yards", 5.7),
            "Third Down Rate": mean(off, "_third_success", 0.40, off["_third"] > 0),
            "Third Down Defense Raw": mean(deff, "_third_success", 0.40, deff["_third"] > 0),
            "Red Zone TD Rate": mean(off, "_rz_td", 0.62, off["_red_zone"] > 0),
            "Red Zone Defense Raw": mean(deff, "_rz_td", 0.62, deff["_red_zone"] > 0),
            "Turnover Rate": mean(off, "_turnover", 0.12),
            "Takeaway Rate": mean(deff, "_turnover", 0.12),
            "Sack Rate Allowed": mean(off, "_sack", 0.065, off_pass),
            "Sack Rate Created": mean(deff, "_sack", 0.065, def_pass),
            "Pace Seconds Per Play": 27.5,
            "Plays Per Game": len(off) / games,
            "Possessions Per Game": drives / games,
            "Open PBP Available": True,
        })
    return pd.DataFrame(rows)


def _open_roster_frame(season: int) -> pd.DataFrame:
    path = _download_open_asset("espn_cfb_rosters", season, ("roster", "espn"))
    aliases = {
        "team": ("team", "team_name", "school", "team_display_name"),
        "athlete_id": ("athlete_id", "id", "player_id"),
        "name": ("full_name", "athlete_full_name", "display_name", "name"),
        "position": ("position_abbreviation", "position", "position_name"),
        "class": ("class", "class_name", "experience", "experience_display_value"),
        "experience_years": ("experience_years", "years", "year"),
    }
    frame = _read_open_parquet(path, aliases)
    if frame.empty:
        return frame
    frame["team"] = frame["team"].map(_normalize_team)
    frame["name"] = _series(frame, "name", "").astype(str).str.strip()
    frame["position"] = _series(frame, "position", "").astype(str).str.upper().str.strip()
    return frame


def _class_number(value: Any) -> float:
    text = _text(value).upper().replace(" ", "")
    if text.isdigit():
        return clamp(float(text), 1.0, 6.0)
    mapping = {"FR": 1, "FRESHMAN": 1, "RSFR": 1.5, "SO": 2, "SOPHOMORE": 2, "RSSO": 2.5,
               "JR": 3, "JUNIOR": 3, "RSJR": 3.5, "SR": 4, "SENIOR": 4, "RSSR": 4.5,
               "GR": 5, "GRADUATE": 5}
    return float(mapping.get(text, 2.0))


def _roster_priors(season: int, teams: list[str]) -> pd.DataFrame:
    current = _open_roster_frame(season)
    previous = _open_roster_frame(season - 1)
    if current.empty:
        return pd.DataFrame()
    current["key"] = _series(current, "athlete_id", "").astype(str)
    current.loc[current["key"].isin(["", "nan", "None"]), "key"] = current["name"].str.lower()
    previous = previous.copy()
    if not previous.empty:
        previous["key"] = _series(previous, "athlete_id", "").astype(str)
        previous.loc[previous["key"].isin(["", "nan", "None"]), "key"] = previous["name"].str.lower()
    rows = []
    position_weight = {"QB": 3.0, "RB": 1.5, "WR": 1.5, "TE": 1.3, "OL": 1.5, "OT": 1.5, "OG": 1.5, "C": 1.5,
                       "DL": 1.4, "DE": 1.4, "DT": 1.4, "EDGE": 1.5, "LB": 1.3, "CB": 1.3, "S": 1.3, "DB": 1.3,
                       "K": 0.7, "P": 0.6}
    for team in teams:
        cur = current[current["team"] == team].copy()
        prev = previous[previous["team"] == team].copy() if not previous.empty else pd.DataFrame()
        if cur.empty:
            continue
        cur["class_num"] = [_class_number(v) for v in _series(cur, "class", _series(cur, "experience_years", 2.0))]
        cur["weight"] = [position_weight.get(_text(p).upper(), 1.0) for p in cur["position"]]
        prev_keys = set(prev["key"].astype(str)) if not prev.empty else set()
        cur["returning"] = cur["key"].astype(str).isin(prev_keys).astype(float)
        total_weight = float(cur["weight"].sum()) or 1.0
        returning = float((cur["returning"] * cur["weight"]).sum() / total_weight)
        def pos_return(prefixes: tuple[str, ...], fallback: float = 0.50) -> float:
            mask = cur["position"].astype(str).str.startswith(prefixes)
            if not mask.any():
                return fallback
            weights = cur.loc[mask, "weight"]
            return float((cur.loc[mask, "returning"] * weights).sum() / max(1e-9, weights.sum()))
        qb_cur = cur[cur["position"].str.startswith("QB")]
        qb_cont = pos_return(("QB",), 0.35)
        experience = float(np.average(cur["class_num"], weights=cur["weight"])) if len(cur) else 2.0
        newcomers = cur[cur["returning"] == 0]
        experienced_newcomers = newcomers[newcomers["class_num"] >= 2.5]
        portal = (len(experienced_newcomers) - max(0, len(prev) - int(cur["returning"].sum()))) / max(10.0, len(cur))
        underclass = cur[cur["class_num"] <= 2.0]
        recruiting = len(underclass) / max(1.0, len(cur)) + 0.08 * float(underclass["class_num"].mean() if len(underclass) else 1.5)
        rows.append({
            "Team": team,
            "Returning Production": returning,
            "Returning Passing": qb_cont,
            "Returning Receiving": pos_return(("WR", "TE"), returning),
            "Returning Rushing": pos_return(("RB",), returning),
            "Talent Rating": experience + math.log(max(1, len(cur))) / 4.0,
            "Recruiting Rating": recruiting,
            "Portal Rating": portal,
            "QB Continuity": qb_cont,
            "Roster Data Available": True,
        })
    return pd.DataFrame(rows)


def _open_power_ratings(games: pd.DataFrame, through_week: int | None) -> pd.DataFrame:
    strength = _result_strength(games, through_week)
    if strength.empty:
        return strength
    out = strength.copy()
    out["SRS Rating"] = pd.to_numeric(out["Result Power"], errors="coerce").fillna(0.0)
    out["SP+ Rating"] = out["SRS Rating"]
    out["FPI Rating"] = 0.85 * out["SRS Rating"]
    out["Elo Rating"] = 1500.0 + 18.0 * out["SRS Rating"]
    out["WEPA Rating"] = out["SRS Rating"]
    return out


def _open_feature_bundle(season: int, through_week: int | None) -> dict[str, Any]:
    games = _parse_games(_espn_games_payload(season), season)
    results = _result_strength(games, through_week)
    pbp = _pbp_team_metrics(season, through_week)
    fbs_teams = sorted(set(_espn_team_index()) | set(results.get("Team", [])) | set(pbp.get("Team", [])))
    roster = _roster_priors(season, fbs_teams)
    ratings = _open_power_ratings(games, through_week)
    merged = pd.DataFrame({"Team": fbs_teams})
    for feature in [results, pbp, roster, ratings]:
        if feature is not None and not feature.empty:
            merged = _merge_feature(merged, feature)
    return {"games": games, "metrics": merged, "pbp_available": not pbp.empty, "roster_available": not roster.empty}


def _bundle_payload(endpoint: str, season: int, through_week: int | None) -> list[dict[str, Any]]:
    bundle = _open_feature_bundle(season, through_week)
    frame = bundle["metrics"].copy()
    if frame.empty:
        return []
    rows: list[dict[str, Any]] = []
    if endpoint == "stats/season/advanced":
        for _, row in frame.iterrows():
            rows.append({
                "team": row["Team"],
                "offense": {
                    "successRate": row.get("Success Rate Offense"), "explosiveness": row.get("Explosiveness Offense"),
                    "plays": row.get("Advanced Plays"), "drives": row.get("Advanced Drives"),
                    "lineYards": row.get("Line Yards Offense"), "powerSuccess": row.get("Power Success"),
                    "stuffRate": row.get("Stuff Rate Offense Raw"), "havoc": {"total": row.get("Havoc Allowed")},
                    "standardDowns": {"ppa": row.get("Standard Downs Offense")},
                    "passingDowns": {"ppa": row.get("Passing Downs Offense")},
                    "pointsPerOpportunity": row.get("Finishing Drives Offense"),
                    "fieldPosition": {"averageStart": row.get("Field Position Offense")},
                },
                "defense": {
                    "successRate": row.get("Success Rate Defense Raw"), "explosiveness": row.get("Explosiveness Defense Raw"),
                    "lineYards": row.get("Line Yards Defense Raw"), "stuffRate": row.get("Stuff Rate Defense Raw"),
                    "havoc": {"total": row.get("Havoc Created")},
                    "standardDowns": {"ppa": row.get("Standard Downs Defense Raw")},
                    "passingDowns": {"ppa": row.get("Passing Downs Defense Raw")},
                    "pointsPerOpportunity": row.get("Finishing Drives Defense Raw"),
                    "fieldPosition": {"averageStart": row.get("Field Position Defense Raw")},
                },
            })
    elif endpoint == "ppa/teams":
        for _, row in frame.iterrows():
            rows.append({"team": row["Team"], "offense": {"overall": {"average": row.get("EPA/PPA Offense")}, "passing": {"average": row.get("Pass EPA/PPA")}, "rushing": {"average": row.get("Rush EPA/PPA")}}, "defense": {"overall": {"average": row.get("EPA/PPA Defense Raw")}, "passing": {"average": row.get("Pass Defense Raw")}, "rushing": {"average": row.get("Rush Defense Raw")}}})
    elif endpoint == "stats/season/havoc":
        rows = [{"team": row["Team"], "total": row.get("Havoc Created"), "havocAllowed": row.get("Havoc Allowed")} for _, row in frame.iterrows()]
    elif endpoint == "stats/season":
        stat_lookup = {
            "pointsPerGame": "Points Per Game", "yardsPerPlay": "Yards Per Play", "games": "Games",
            "plays": "Advanced Plays", "thirdDownConversions": None, "thirdDownAttempts": None,
            "redZoneTouchdowns": None, "redZoneAttempts": None, "turnovers": None,
            "sacksAllowed": None, "sacks": None,
        }
        for _, row in frame.iterrows():
            values = {
                "pointsPerGame": row.get("Points Per Game"), "yardsPerPlay": row.get("Yards Per Play"),
                "games": row.get("Games"), "plays": row.get("Advanced Plays"),
            }
            plays = _num(row.get("Advanced Plays"), 0.0)
            third_att = max(0.0, plays * 0.18)
            values.update({
                "thirdDownAttempts": third_att,
                "thirdDownConversions": third_att * _num(row.get("Third Down Rate"), 0.40),
                "redZoneAttempts": max(0.0, _num(row.get("Advanced Drives"), 0.0) * 0.32),
                "redZoneTouchdowns": max(0.0, _num(row.get("Advanced Drives"), 0.0) * 0.32 * _num(row.get("Red Zone TD Rate"), 0.62)),
                "turnovers": plays * _num(row.get("Turnover Rate"), 0.12),
                "sacksAllowed": plays * _num(row.get("Sack Rate Allowed"), 0.065),
                "sacks": plays * _num(row.get("Sack Rate Created"), 0.065),
            })
            for stat, value in values.items():
                rows.append({"team": row["Team"], "statName": stat, "statValue": value})
    elif endpoint == "player/returning":
        rows = [{"team": row["Team"], "returningProduction": row.get("Returning Production"), "passing": row.get("Returning Passing"), "receiving": row.get("Returning Receiving"), "rushing": row.get("Returning Rushing")} for _, row in frame.iterrows()]
    elif endpoint == "talent":
        rows = [{"school": row["Team"], "talent": row.get("Talent Rating")} for _, row in frame.iterrows()]
    elif endpoint == "recruiting/teams":
        rows = [{"team": row["Team"], "points": row.get("Recruiting Rating")} for _, row in frame.iterrows()]
    elif endpoint == "player/portal":
        rows = [{"destination": row["Team"], "rating": max(0.0, _num(row.get("Portal Rating"), 0.0))} for _, row in frame.iterrows()]
    elif endpoint == "ratings/sp":
        rows = [{"team": row["Team"], "rating": row.get("SP+ Rating"), "offense": {"rating": row.get("EPA/PPA Offense"), "pace": row.get("Pace Seconds Per Play")}, "defense": {"rating": -_num(row.get("EPA/PPA Defense Raw"), 0.0)}, "specialTeams": {"rating": 0.0}} for _, row in frame.iterrows()]
    elif endpoint == "ratings/srs":
        rows = [{"team": row["Team"], "rating": row.get("SRS Rating")} for _, row in frame.iterrows()]
    elif endpoint == "ratings/elo":
        rows = [{"team": row["Team"], "elo": row.get("Elo Rating")} for _, row in frame.iterrows()]
    elif endpoint == "ratings/fpi":
        rows = [{"team": row["Team"], "fpi": row.get("FPI Rating")} for _, row in frame.iterrows()]
    elif endpoint == "wepa/team/season":
        rows = [{"team": row["Team"], "wepa": row.get("WEPA Rating")} for _, row in frame.iterrows()]
    return rows


def _free_data_get(endpoint: str, params: dict[str, Any] | None = None, *, optional: bool = False,
              max_age: int = CACHE_SECONDS) -> list[dict[str, Any]]:
    """Compatibility dispatcher backed entirely by free, no-key public sources."""
    params = params or {}
    season = int(_num(params.get("year"), _current_cfb_season() if "_current_cfb_season" in globals() else date.today().year))
    through_week = int(_num(params.get("endWeek"), 0)) + 1 if params.get("endWeek") is not None else None
    try:
        if endpoint == "games":
            return _espn_games_payload(season)
        if endpoint == "venues":
            return _espn_venues_payload(season)
        if endpoint == "teams/fbs":
            return _espn_teams_payload(season)
        if endpoint == "lines":
            return _espn_lines_payload(season)
        if endpoint == "games/weather":
            return []
        if endpoint in {"stats/season/advanced", "ppa/teams", "stats/season/havoc", "stats/season", "player/returning", "talent", "recruiting/teams", "player/portal", "ratings/sp", "ratings/srs", "ratings/elo", "ratings/fpi", "wepa/team/season"}:
            return _bundle_payload(endpoint, season, through_week)
        if endpoint == "coaches":
            return []
        if endpoint == "roster":
            team = _normalize_team(params.get("team"))
            roster = _open_roster_frame(season)
            subset = roster[roster["team"] == team] if not roster.empty else pd.DataFrame()
            return [{"name": row.get("name"), "position": row.get("position"), "year": row.get("class"), "id": row.get("athlete_id")} for _, row in subset.iterrows()]
        if endpoint == "player/usage":
            return []
        return []
    except Exception:
        if optional:
            return []
        raise



def _sheet(tab: str, columns: list[str]) -> pd.DataFrame:
    cache_key = f"cfb_sheet_cache::{tab}"
    try:
        cached = st.session_state.get(cache_key)
        if isinstance(cached, pd.DataFrame):
            frame = cached.copy()
        else:
            frame = read_sheet(tab, columns)
            if frame is None or frame.empty:
                frame = pd.DataFrame(columns=columns)
            st.session_state[cache_key] = frame.copy()
        for col in columns:
            if col not in frame.columns:
                frame[col] = ""
        return frame[columns].copy()
    except Exception:
        return pd.DataFrame(columns=columns)


def _write(tab: str, frame: pd.DataFrame, columns: list[str]) -> None:
    output = frame.copy()
    for col in columns:
        if col not in output.columns:
            output[col] = ""
    output = output[columns].fillna("")
    write_sheet(tab, output, columns)
    try:
        st.session_state[f"cfb_sheet_cache::{tab}"] = output.copy()
    except Exception:
        pass


def _upsert(tab: str, incoming: pd.DataFrame, columns: list[str], keys: list[str]) -> None:
    if incoming is None or incoming.empty:
        return
    existing = _sheet(tab, columns)
    combined = pd.concat([existing, incoming], ignore_index=True)
    if not combined.empty:
        combined = combined.drop_duplicates(subset=keys, keep="last")
    _write(tab, combined, columns)


def _american_ev(probability: float, odds: float) -> float:
    probability = clamp(probability, 0.001, 0.999)
    try:
        return float(expected_value_per_unit(probability, odds))
    except Exception:
        profit = odds / 100.0 if odds > 0 else 100.0 / abs(odds)
        return probability * profit - (1.0 - probability)


def _no_vig(home_odds: float, away_odds: float) -> tuple[float, float]:
    hp = american_implied_probability(home_odds) if home_odds else 0.0
    ap = american_implied_probability(away_odds) if away_odds else 0.0
    if hp > 0 and ap > 0:
        total = hp + ap
        return hp / total, ap / total
    if hp > 0:
        return hp, 1.0 - hp
    if ap > 0:
        return 1.0 - ap, ap
    return 0.5, 0.5


@dataclass
class Personnel:
    expected_qb: str = "Unconfirmed"
    qb_confirmed: bool = False
    qb_continuity: float = 0.50
    qb_adjustment: float = 0.0
    ol_adjustment: float = 0.0
    skill_adjustment: float = 0.0
    dl_adjustment: float = 0.0
    linebacker_adjustment: float = 0.0
    secondary_adjustment: float = 0.0
    kicker_adjustment: float = 0.0
    special_teams_adjustment: float = 0.0
    coaching_continuity: float = 0.75
    coordinator_continuity: float = 0.67
    availability_confidence: float = 45.0
    source: str = "Manual confirmation required"
    notes: str = ""

    @property
    def offense_adjustment(self) -> float:
        return self.qb_adjustment + self.ol_adjustment + self.skill_adjustment

    @property
    def defense_adjustment(self) -> float:
        return self.dl_adjustment + self.linebacker_adjustment + self.secondary_adjustment

    @property
    def injury_adjustment(self) -> float:
        return self.offense_adjustment + self.defense_adjustment + self.kicker_adjustment


@dataclass
class Environment:
    home_field: float = 1.5
    league_hfa: float = 1.5
    venue_hfa: float = 0.0
    travel_adjustment: float = 0.0
    timezone_adjustment: float = 0.0
    altitude_adjustment: float = 0.0
    rest_adjustment: float = 0.0
    weather_total_adjustment: float = 0.0
    weather_home_adjustment: float = 0.0
    temperature: float = 72.0
    wind: float = 5.0
    precipitation_probability: float = 0.0
    weather_confidence: float = 50.0
    roof: str = "Outdoor/Unknown"
    stadium: str = ""
    notes: str = ""

# ---------------------------------------------------------------------------
# public-feed parsing and feature assembly
# ---------------------------------------------------------------------------

def _parse_teams(payload: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in payload:
        location = item.get("location") or {}
        rows.append({
            "Team": _normalize_team(_first(item, ["school", "team", "name"])),
            "Conference": _text(_first(item, ["conference"])),
            "Classification": _text(_first(item, ["classification"], "fbs")).lower(),
            "Venue ID": _first(location, ["venueId", "venue_id", "id"], _first(item, ["venueId"])),
            "City": _text(_first(location, ["city"])),
            "State": _text(_first(location, ["state"])),
            "Latitude": _num(_first(location, ["latitude"]), np.nan),
            "Longitude": _num(_first(location, ["longitude"]), np.nan),
            "Elevation": _num(_first(location, ["elevation"]), 0.0),
            "Capacity": _num(_first(location, ["capacity"]), 0.0),
            "Timezone": _text(_first(location, ["timezone"])),
        })
    frame = pd.DataFrame(rows)
    return frame.drop_duplicates("Team") if not frame.empty else frame


def _parse_venues(payload: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in payload:
        location = item.get("location") or {}
        dome = _bool(_first(item, ["dome", "indoor"])); roof = "Indoor/Dome" if dome else "Outdoor/Unknown"
        rows.append({
            "Venue ID": _text(_first(item, ["id", "venueId", "venue_id"])),
            "Venue Stadium": _text(_first(item, ["name", "stadium"])),
            "Venue City": _text(_first(item, ["city"], _first(location, ["city"]))),
            "Venue State": _text(_first(item, ["state"], _first(location, ["state"]))),
            "Venue Latitude": _num(_first(item, ["latitude"], _first(location, ["latitude"])), np.nan),
            "Venue Longitude": _num(_first(item, ["longitude"], _first(location, ["longitude"])), np.nan),
            "Venue Elevation": _num(_first(item, ["elevation"], _first(location, ["elevation"])), 0.0),
            "Venue Capacity": _num(_first(item, ["capacity"]), 0.0),
            "Venue Roof": roof,
            "Venue Surface": _text(_first(item, ["grass", "surface"])),
        })
    return pd.DataFrame(rows).drop_duplicates("Venue ID") if rows else pd.DataFrame()



def _parse_games(payload: list[dict[str, Any]], season: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in payload:
        start = _text(_first(item, ["startDate", "start_date", "startTime"])).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(start) if start else None
        except Exception:
            dt = None
        away = _normalize_team(_first(item, ["awayTeam", "away_team", "away"])); home = _normalize_team(_first(item, ["homeTeam", "home_team", "home"]))
        away_score = _first(item, ["awayPoints", "awayScore", "away_points"])
        home_score = _first(item, ["homePoints", "homeScore", "home_points"])
        completed = away_score is not None and home_score is not None
        rows.append({
            "Season": int(_num(_first(item, ["season", "year"]), season)),
            "Week": int(_num(_first(item, ["week"]), 0)),
            "Season Type": _text(_first(item, ["seasonType", "season_type"], "regular")),
            "Game Date": dt.date().isoformat() if dt else (start[:10] if start else ""),
            "Game Time": dt.isoformat() if dt else start,
            "Away Team": away, "Home Team": home,
            "Away Conference": _text(_first(item, ["awayConference", "away_conference"])),
            "Home Conference": _text(_first(item, ["homeConference", "home_conference"])),
            "Away Classification": _text(_first(item, ["awayClassification", "away_classification"], "fbs")).lower(),
            "Home Classification": _text(_first(item, ["homeClassification", "home_classification"], "fbs")).lower(),
            "Away Score": _num(away_score, np.nan), "Home Score": _num(home_score, np.nan),
            "Completed": completed,
            "Neutral Site": _bool(_first(item, ["neutralSite", "neutral_site"])),
            "Conference Game": _bool(_first(item, ["conferenceGame", "conference_game"])),
            "Venue ID": _first(item, ["venueId", "venue_id"]),
            "Stadium": _text(_first(item, ["venue", "stadium"])),
            "Location": _text(_first(item, ["location"])),
            "Latitude": _num(_first(item, ["latitude"]), np.nan),
            "Longitude": _num(_first(item, ["longitude"]), np.nan),
            "Elevation": _num(_first(item, ["elevation"]), 0.0),
            "Capacity": _num(_first(item, ["capacity"]), 0.0),
            "Roof": _text(_first(item, ["roof"], "Outdoor/Unknown")),
            "Surface": _text(_first(item, ["surface"], "")),
            "Away Rest": 7.0, "Home Rest": 7.0,
            "Away ML": _num(_first(item, ["awayMoneyline", "awayML"], 0.0), 0.0),
            "Home ML": _num(_first(item, ["homeMoneyline", "homeML"], 0.0), 0.0),
            "Opening Home Spread": _num(_first(item, ["openingHomeSpread"], np.nan), np.nan),
            "Opening Total": _num(_first(item, ["openingTotal"], np.nan), np.nan),
            "Home Spread": _num(_first(item, ["homeSpread"], np.nan), np.nan),
            "Total": _num(_first(item, ["total"], np.nan), np.nan),
            "Line Provider": _text(_first(item, ["lineProvider"], "")),
            "Temperature": np.nan, "Wind": np.nan,
            "Precipitation Probability": np.nan, "Weather Source": "",
            "Game ID": _text(_first(item, ["id", "gameId", "game_id"], f"{season}-{away}-{home}-{start}")),
        })
    return pd.DataFrame(rows, columns=SCHEDULE_COLUMNS)



def _parse_lines(payload: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for game in payload:
        game_id = _text(_first(game, ["id", "gameId", "game_id"])); home = _normalize_team(_first(game, ["homeTeam", "home_team"])); away = _normalize_team(_first(game, ["awayTeam", "away_team"]))
        providers = _first(game, ["lines", "providers"], []) or []
        if isinstance(providers, dict): providers = [providers]
        for line in providers:
            provider_obj = line.get("provider") if isinstance(line.get("provider"), dict) else {}
            provider = _text(_first(provider_obj, ["name"], _first(line, ["provider"], "")))
            rows.append({
                "Game ID": game_id, "Home Team": home, "Away Team": away,
                "Provider": provider,
                "Opening Home Spread": _num(_first(line, ["spreadOpen", "spread_open", "openingSpread"]), np.nan),
                "Opening Total": _num(_first(line, ["overUnderOpen", "over_under_open", "openingTotal"]), np.nan),
                "Home Spread": _num(_first(line, ["spread", "homeSpread", "home_spread"]), np.nan),
                "Total": _num(_first(line, ["overUnder", "over_under", "total"]), np.nan),
                "Home ML": _num(_first(line, ["homeMoneyline", "homeMoneyLine", "home_ml"]), 0.0),
                "Away ML": _num(_first(line, ["awayMoneyline", "awayMoneyLine", "away_ml"]), 0.0),
            })
    return pd.DataFrame(rows)


def _parse_weather(payload: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in payload:
        weather = item.get("weather") if isinstance(item.get("weather"), dict) else item
        rows.append({
            "Game ID": _text(_first(item, ["id", "gameId", "game_id"])),
            "Temperature": _num(_first(weather, ["temperature", "temp"]), np.nan),
            "Wind": _num(_first(weather, ["windSpeed", "wind_speed", "wind"]), np.nan),
            "Precipitation Probability": _pct(_first(weather, ["precipitationProbability", "precipitation_probability", "precipitation"]), np.nan),
            "Weather Source": "free public college-football",
        })
    return pd.DataFrame(rows)


def _compute_rest(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty: return frame
    output = frame.sort_values(["Game Time", "Week"]).copy()
    last: dict[str, datetime] = {}
    for idx, row in output.iterrows():
        try: dt = datetime.fromisoformat(_text(row["Game Time"]).replace("Z", "+00:00"))
        except Exception: dt = None
        for side in ("Away", "Home"):
            team = row[f"{side} Team"]
            rest = 7.0
            if dt and team in last: rest = max(0.0, min(30.0, (dt - last[team]).total_seconds() / 86400.0))
            output.at[idx, f"{side} Rest"] = round(rest, 1)
            if dt: last[team] = dt
    return output.sort_values(["Week", "Game Time"])


def _schedule_for_season(season: int, preferred_provider: str = "") -> pd.DataFrame:
    games = _parse_games(_free_data_get("games", {"year": season}), season)
    if games.empty: return games
    venues = _parse_venues(_free_data_get("venues", {"year": season}, optional=True, max_age=86400 * 30))
    if not venues.empty:
        games["Venue ID"] = games["Venue ID"].astype(str)
        games = games.merge(venues, on="Venue ID", how="left")
        games["Stadium"] = games["Stadium"].where(games["Stadium"].astype(str).str.len() > 0, games["Venue Stadium"].fillna(""))
        for destination, source in [("Latitude", "Venue Latitude"), ("Longitude", "Venue Longitude"), ("Elevation", "Venue Elevation"), ("Capacity", "Venue Capacity")]:
            games[destination] = pd.to_numeric(games[source], errors="coerce").combine_first(pd.to_numeric(games[destination], errors="coerce"))
        games["Roof"] = games["Venue Roof"].fillna(games["Roof"]); games["Surface"] = games["Venue Surface"].fillna(games["Surface"])
        venue_location = games["Venue City"].fillna("").astype(str) + ", " + games["Venue State"].fillna("").astype(str)
        games["Location"] = games["Location"].where(games["Location"].astype(str).str.len() > 0, venue_location)
        games = games.drop(columns=[c for c in games.columns if c.startswith("Venue ") and c != "Venue ID"], errors="ignore")
    teams = _parse_teams(_free_data_get("teams/fbs", {"year": season}, optional=True))
    if not teams.empty:
        meta = teams[["Team", "Venue ID", "City", "State", "Latitude", "Longitude", "Elevation", "Capacity"]].copy()
        home_meta = meta.rename(columns={"Team": "Home Team", "City": "Venue City", "State": "Venue State", "Venue ID": "Team Venue ID"})
        games = games.merge(home_meta, on="Home Team", how="left", suffixes=("", "_meta"))
        for col in ["Latitude", "Longitude", "Elevation", "Capacity"]:
            games[col] = pd.to_numeric(games.get(f"{col}_meta", games[col]), errors="coerce").fillna(games[col])
        games["Location"] = games["Location"].where(games["Location"].astype(str).str.len() > 0, games.get("Venue City", "").astype(str) + ", " + games.get("Venue State", "").astype(str))
        drop = [c for c in games.columns if c.endswith("_meta") or c in {"Venue City", "Venue State", "Team Venue ID"}]
        games = games.drop(columns=drop, errors="ignore")
    lines = _parse_lines(_free_data_get("lines", {"year": season}, optional=True))
    if not lines.empty:
        if preferred_provider:
            preferred = lines[lines["Provider"].str.lower() == preferred_provider.lower()]
            if not preferred.empty: lines = pd.concat([preferred, lines]).drop_duplicates("Game ID", keep="first")
        lines = lines.drop_duplicates("Game ID", keep="last")
        games = games.merge(lines[["Game ID", "Opening Home Spread", "Opening Total", "Home Spread", "Total", "Home ML", "Away ML", "Provider"]], on="Game ID", how="left", suffixes=("", "_line"))
        for col in ["Opening Home Spread", "Opening Total", "Home Spread", "Total", "Home ML", "Away ML"]:
            games[col] = pd.to_numeric(games.get(f"{col}_line"), errors="coerce").combine_first(pd.to_numeric(games[col], errors="coerce"))
        games["Line Provider"] = games.get("Provider", "").fillna("")
        games = games.drop(columns=[c for c in games.columns if c.endswith("_line") or c == "Provider"], errors="ignore")
    weather = _parse_weather(_free_data_get("games/weather", {"year": season}, optional=True))
    if not weather.empty:
        games = games.merge(weather, on="Game ID", how="left", suffixes=("", "_weather"))
        for col in ["Temperature", "Wind", "Precipitation Probability"]:
            games[col] = pd.to_numeric(games.get(f"{col}_weather"), errors="coerce").combine_first(pd.to_numeric(games[col], errors="coerce"))
        games["Weather Source"] = games.get("Weather Source_weather", "").fillna(games["Weather Source"])
        games = games.drop(columns=[c for c in games.columns if c.endswith("_weather")], errors="ignore")
    return _compute_rest(games.reindex(columns=SCHEDULE_COLUMNS))


def _flatten_advanced(payload: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in payload:
        team = _normalize_team(_first(item, ["team", "school"])); conference = _text(_first(item, ["conference"])); offense = item.get("offense") or {}; defense = item.get("defense") or {}
        def val(side: dict[str, Any], *names: str, default=np.nan):
            return _num(_dig(side, *names, default=default), default)
        rows.append({
            "Team": team, "Conference": conference,
            "Success Rate Offense": val(offense, "successRate", "success_rate"),
            "Success Rate Defense Raw": val(defense, "successRate", "success_rate"),
            "Explosiveness Offense": val(offense, "explosiveness"),
            "Explosiveness Defense Raw": val(defense, "explosiveness"),
            "Advanced Plays": val(offense, "plays"),
            "Advanced Drives": val(offense, "drives"),
            "Line Yards Offense": val(offense, "lineYards", "line_yards", "rushing.lineYards", "rushing.line_yards"),
            "Line Yards Defense Raw": val(defense, "lineYards", "line_yards", "rushing.lineYards", "rushing.line_yards"),
            "Power Success": val(offense, "powerSuccess", "power_success", "rushing.powerSuccess", "rushing.power_success"),
            "Stuff Rate Offense Raw": val(offense, "stuffRate", "stuff_rate", "rushing.stuffRate", "rushing.stuff_rate"),
            "Stuff Rate Defense Raw": val(defense, "stuffRate", "stuff_rate", "rushing.stuffRate", "rushing.stuff_rate"),
            "Havoc Allowed": val(offense, "havoc.total", "havoc"),
            "Havoc Created": val(defense, "havoc.total", "havoc"),
            "Standard Downs Offense": val(offense, "standardDowns.ppa", "standard_downs.ppa", "standardDownsPpa"),
            "Standard Downs Defense Raw": val(defense, "standardDowns.ppa", "standard_downs.ppa", "standardDownsPpa"),
            "Passing Downs Offense": val(offense, "passingDowns.ppa", "passing_downs.ppa", "passingDownsPpa"),
            "Passing Downs Defense Raw": val(defense, "passingDowns.ppa", "passing_downs.ppa", "passingDownsPpa"),
            "Finishing Drives Offense": val(offense, "pointsPerOpportunity", "points_per_opportunity", "scoringOpportunities.pointsPerOpportunity"),
            "Finishing Drives Defense Raw": val(defense, "pointsPerOpportunity", "points_per_opportunity", "scoringOpportunities.pointsPerOpportunity"),
            "Field Position Offense": val(offense, "fieldPosition.averageStart", "field_position.average_start", "fieldPosition"),
            "Field Position Defense Raw": val(defense, "fieldPosition.averageStart", "field_position.average_start", "fieldPosition"),
        })
    return pd.DataFrame(rows)


def _flatten_ppa(payload: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in payload:
        offense = item.get("offense") or {}; defense = item.get("defense") or {}
        rows.append({
            "Team": _normalize_team(_first(item, ["team", "school"])),
            "EPA/PPA Offense": _num(_dig(offense, "overall.average", "overall.ppa", "overall", "ppa", default=np.nan), np.nan),
            "EPA/PPA Defense Raw": _num(_dig(defense, "overall.average", "overall.ppa", "overall", "ppa", default=np.nan), np.nan),
            "Pass EPA/PPA": _num(_dig(offense, "passing.average", "passing.ppa", "passing", default=np.nan), np.nan),
            "Pass Defense Raw": _num(_dig(defense, "passing.average", "passing.ppa", "passing", default=np.nan), np.nan),
            "Rush EPA/PPA": _num(_dig(offense, "rushing.average", "rushing.ppa", "rushing", default=np.nan), np.nan),
            "Rush Defense Raw": _num(_dig(defense, "rushing.average", "rushing.ppa", "rushing", default=np.nan), np.nan),
        })
    return pd.DataFrame(rows)


def _flatten_havoc(payload: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in payload:
        rows.append({
            "Team": _normalize_team(_first(item, ["team", "school"])),
            "Havoc Created": _num(_first(item, ["total", "havoc", "defensiveHavoc"]), np.nan),
            "Havoc Allowed": _num(_first(item, ["havocAllowed", "offensiveHavoc", "allowed"]), np.nan),
        })
    return pd.DataFrame(rows)


def _flatten_returning(payload: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in payload:
        rows.append({
            "Team": _normalize_team(_first(item, ["team", "school"])),
            "Returning Production": _pct(_first(item, ["percentPPA", "percentagePPA", "returningPPA", "totalPPA", "total", "returningProduction"]), np.nan),
            "Returning Passing": _pct(_first(item, ["percentPassingPPA", "percentagePassingPPA", "totalPassingPPA", "passingPPA", "passing"]), np.nan),
            "Returning Receiving": _pct(_first(item, ["percentReceivingPPA", "percentageReceivingPPA", "totalReceivingPPA", "receivingPPA", "receiving"]), np.nan),
            "Returning Rushing": _pct(_first(item, ["percentRushingPPA", "percentageRushingPPA", "totalRushingPPA", "rushingPPA", "rushing"]), np.nan),
        })
    return pd.DataFrame(rows)


def _flatten_sp(payload: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in payload:
        offense = item.get("offense") or {}; defense = item.get("defense") or {}; special = item.get("specialTeams") or item.get("special_teams") or {}
        rows.append({
            "Team": _normalize_team(_first(item, ["team", "school"])),
            "SP+ Rating": _num(_first(item, ["rating", "sp"]), np.nan),
            "SP Offense Rating": _num(_first(offense, ["rating"]), np.nan),
            "SP Defense Rating": _num(_first(defense, ["rating"]), np.nan),
            "SP Special Teams Rating": _num(_first(special, ["rating", "overall"]), np.nan),
            "SP Pace": _num(_first(offense, ["pace"]), np.nan),
        })
    return pd.DataFrame(rows)


def _coach_map(payload: list[dict[str, Any]], year: int) -> dict[str, str]:
    output: dict[str, str] = {}
    for item in payload:
        name = (f"{_text(_first(item, ['firstName', 'first_name']))} {_text(_first(item, ['lastName', 'last_name']))}").strip() or _text(_first(item, ["name", "coach"]))
        direct_team = _normalize_team(_first(item, ["team", "school"])); seasons = item.get("seasons") or []
        if direct_team: output[direct_team] = name
        for season_row in seasons if isinstance(seasons, list) else []:
            school = _normalize_team(_first(season_row, ["school", "team"])); start = int(_num(_first(season_row, ["year", "startYear", "start_year"]), year)); end = int(_num(_first(season_row, ["year", "endYear", "end_year"]), year))
            if school and start <= year <= end: output[school] = name
    return output


def _flatten_coaching(year: int) -> pd.DataFrame:
    current = _coach_map(_free_data_get("coaches", {"year": year}, optional=True), year)
    previous = _coach_map(_free_data_get("coaches", {"year": year - 1}, optional=True), year - 1)
    teams = set(current) | set(previous); rows = []
    for team in teams:
        same = bool(current.get(team) and previous.get(team) and current.get(team) == previous.get(team))
        rows.append({"Team": team, "Head Coach": current.get(team, ""), "Coaching Continuity": 1.0 if same else 0.30 if current.get(team) else np.nan})
    return pd.DataFrame(rows)


def _simple_metric(payload: list[dict[str, Any]], column: str, names: list[str]) -> pd.DataFrame:
    rows = []
    for item in payload:
        rows.append({"Team": _normalize_team(_first(item, ["team", "school"])), column: _num(_first(item, names), np.nan)})
    return pd.DataFrame(rows)


def _flatten_portal(payload: list[dict[str, Any]]) -> pd.DataFrame:
    # Portal value is net incoming minus outgoing player quality. The endpoint is
    # player-level, so aggregate rather than letting one duplicate team row win.
    values: dict[str, float] = {}
    for item in payload:
        rating = _num(_first(item, ["rating", "stars", "points"]), 0.0)
        destination = _normalize_team(_first(item, ["destination", "newTeam", "toTeam", "team"]))
        origin = _normalize_team(_first(item, ["origin", "previousTeam", "fromTeam"]))
        if destination:
            values[destination] = values.get(destination, 0.0) + rating
        if origin:
            values[origin] = values.get(origin, 0.0) - rating
    return pd.DataFrame([{"Team": team, "Portal Rating": value} for team, value in values.items()])


def _merge_feature(base: pd.DataFrame, feature: pd.DataFrame) -> pd.DataFrame:
    if feature is None or feature.empty or "Team" not in feature.columns: return base
    feature = feature.drop_duplicates("Team", keep="last")
    overlap = [c for c in feature.columns if c in base.columns and c != "Team"]
    if overlap: feature = feature.drop(columns=overlap)
    return base.merge(feature, on="Team", how="left")

STAT_MAP = {
    "points": "Points", "pointsPerGame": "Points Per Game", "totalYards": "Total Yards",
    "yardsPerPlay": "Yards Per Play", "thirdDownConversions": "Third Down Conversions",
    "thirdDownAttempts": "Third Down Attempts", "redZoneTouchdowns": "Red Zone Touchdowns",
    "redZoneAttempts": "Red Zone Attempts", "turnovers": "Turnovers", "fumblesLost": "Fumbles Lost",
    "interceptions": "Interceptions Thrown", "sacksAllowed": "Sacks Allowed",
    "sacks": "Sacks Created", "plays": "Plays", "games": "Games",
}


def _flatten_season_stats(payload: list[dict[str, Any]]) -> pd.DataFrame:
    by_team: dict[str, dict[str, Any]] = {}
    for item in payload:
        team = _normalize_team(_first(item, ["team", "school"])); stat = _text(_first(item, ["statName", "stat_name", "stat"])); value = _num(_first(item, ["statValue", "stat_value", "value"]), np.nan)
        if not team: continue
        row = by_team.setdefault(team, {"Team": team})
        row[STAT_MAP.get(stat, stat)] = value
    frame = pd.DataFrame(by_team.values())
    if frame.empty: return frame
    games = _numeric_series(frame, "Games", np.nan).replace(0, np.nan)
    if "Points" in frame and "Points Per Game" not in frame: frame["Points Per Game"] = pd.to_numeric(frame["Points"], errors="coerce") / games
    if "Total Yards" in frame and "Plays" in frame: frame["Yards Per Play"] = pd.to_numeric(frame["Total Yards"], errors="coerce") / _numeric_series(frame, "Plays", np.nan).replace(0, np.nan)
    if "Third Down Conversions" in frame and "Third Down Attempts" in frame: frame["Third Down Rate"] = pd.to_numeric(frame["Third Down Conversions"], errors="coerce") / _numeric_series(frame, "Third Down Attempts", np.nan).replace(0, np.nan)
    if "Red Zone Touchdowns" in frame and "Red Zone Attempts" in frame: frame["Red Zone TD Rate"] = pd.to_numeric(frame["Red Zone Touchdowns"], errors="coerce") / _numeric_series(frame, "Red Zone Attempts", np.nan).replace(0, np.nan)
    if "Turnovers" in frame and "Plays" in frame: frame["Turnover Rate"] = _numeric_series(frame, "Turnovers", np.nan) / _numeric_series(frame, "Plays", np.nan).replace(0, np.nan)
    if "Sacks Allowed" in frame and "Plays" in frame: frame["Sack Rate Allowed"] = _numeric_series(frame, "Sacks Allowed", np.nan) / _numeric_series(frame, "Plays", np.nan).replace(0, np.nan)
    if "Sacks Created" in frame and "Plays" in frame: frame["Sack Rate Created"] = _numeric_series(frame, "Sacks Created", np.nan) / _numeric_series(frame, "Plays", np.nan).replace(0, np.nan)
    return frame


@st.cache_data(ttl=21600, show_spinner=False)
def _result_strength(games: pd.DataFrame, through_week: int | None = None) -> pd.DataFrame:
    if games.empty: return pd.DataFrame(columns=["Team", "Result Power", "Points Per Game", "Points Allowed Per Game", "Games", "FBS Games"])
    done = games[games["Completed"].map(_bool)].copy()
    if through_week is not None: done = done[pd.to_numeric(done["Week"], errors="coerce") < int(through_week)]
    done = done[pd.notna(done["Away Score"]) & pd.notna(done["Home Score"])]
    teams = sorted(set(done["Away Team"]) | set(done["Home Team"]))
    if not teams: return pd.DataFrame(columns=["Team", "Result Power", "Points Per Game", "Points Allowed Per Game", "Games", "FBS Games"])
    power = {team: 0.0 for team in teams}
    # Iterative opponent-adjusted scoring margin with score-margin compression.
    for _ in range(18):
        updated = {}
        for team in teams:
            values = []
            subset = done[(done["Away Team"] == team) | (done["Home Team"] == team)]
            for _, game in subset.iterrows():
                home = game["Home Team"]; away = game["Away Team"]
                margin = _num(game["Home Score"]) - _num(game["Away Score"])
                compressed = math.copysign(min(24.0, abs(margin) ** 0.82 * 1.65), margin) if margin else 0.0
                opponent = away if team == home else home
                team_margin = compressed if team == home else -compressed
                values.append(team_margin + power.get(opponent, 0.0))
            updated[team] = float(np.mean(values)) if values else 0.0
        center = float(np.mean(list(updated.values()))) if updated else 0.0
        power = {k: clamp(v - center, -35.0, 35.0) for k, v in updated.items()}
    rows = []
    for team in teams:
        subset = done[(done["Away Team"] == team) | (done["Home Team"] == team)]
        scored = []; allowed = []; fbs_games = 0
        for _, game in subset.iterrows():
            home_side = team == game["Home Team"]
            scored.append(_num(game["Home Score"] if home_side else game["Away Score"]))
            allowed.append(_num(game["Away Score"] if home_side else game["Home Score"]))
            opponent_class = game["Away Classification"] if home_side else game["Home Classification"]
            if _text(opponent_class).lower() == "fbs": fbs_games += 1
        rows.append({"Team": team, "Result Power": power[team], "Points Per Game": np.mean(scored), "Points Allowed Per Game": np.mean(allowed), "Games": len(subset), "FBS Games": fbs_games})
    return pd.DataFrame(rows)



def _season_features(season: int, through_week: int | None = None) -> tuple[pd.DataFrame, dict[str, bool]]:
    teams = _parse_teams(_espn_teams_payload(season))
    schedule = _parse_games(_espn_games_payload(season), season)
    names = sorted(set(teams.get("Team", [])) | set(schedule.get("Away Team", [])) | set(schedule.get("Home Team", [])))
    base = pd.DataFrame({"Team": names})
    if not teams.empty:
        base = base.merge(teams[[c for c in ["Team", "Conference", "Classification"] if c in teams.columns]].drop_duplicates("Team"), on="Team", how="left")
    bundle = _open_feature_bundle(season, through_week)
    metrics = bundle["metrics"]
    frame = _merge_feature(base, metrics)
    games = _numeric_series(frame, "Games", np.nan).replace(0, np.nan)
    drives = _numeric_series(frame, "Advanced Drives", np.nan)
    plays = _numeric_series(frame, "Advanced Plays", np.nan)
    if "Plays Per Game" not in frame:
        frame["Plays Per Game"] = plays / games
    if "Possessions Per Game" not in frame:
        frame["Possessions Per Game"] = drives / games
    if "Points Per Drive" not in frame:
        frame["Points Per Drive"] = _numeric_series(frame, "Points Per Game", np.nan) / _numeric_series(frame, "Possessions Per Game", np.nan).replace(0, np.nan)
    if "Points Allowed Per Drive" not in frame:
        frame["Points Allowed Per Drive"] = _numeric_series(frame, "Points Allowed Per Game", np.nan) / _numeric_series(frame, "Possessions Per Game", np.nan).replace(0, np.nan)
    if "Pace Seconds Per Play" not in frame:
        frame["Pace Seconds Per Play"] = 27.5
    if "QB Continuity" not in frame:
        frame["QB Continuity"] = _numeric_series(frame, "Returning Passing", np.nan)
    availability = {
        "advanced": bool(bundle.get("pbp_available")),
        "roster": bool(bundle.get("roster_available")),
        "market": True,
    }
    return frame, availability



def _opponent_edge_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    pairs = [
        ("EPA/PPA Defense Raw", "EPA/PPA Defense Edge", True),
        ("Success Rate Defense Raw", "Success Rate Defense Edge", True),
        ("Pass Defense Raw", "Pass Defense Edge", True),
        ("Rush Defense Raw", "Rush Defense Edge", True),
        ("Explosiveness Defense Raw", "Explosiveness Defense Edge", True),
        ("Finishing Drives Defense Raw", "Finishing Drives Defense Edge", True),
        ("Field Position Defense Raw", "Field Position Defense Edge", True),
        ("Line Yards Defense Raw", "Line Yards Defense Edge", True),
        ("Standard Downs Defense Raw", "Standard Downs Defense Edge", True),
        ("Passing Downs Defense Raw", "Passing Downs Defense Edge", True),
    ]
    for source, target, lower_better in pairs:
        if source in out:
            league = pd.to_numeric(out[source], errors="coerce").median()
            sign = -1.0 if lower_better else 1.0
            out[target] = sign * (pd.to_numeric(out[source], errors="coerce") - league)
    if "Stuff Rate Defense Raw" in out:
        league = pd.to_numeric(out["Stuff Rate Defense Raw"], errors="coerce").median()
        out["Stuff Rate Edge"] = pd.to_numeric(out["Stuff Rate Defense Raw"], errors="coerce") - league
    if "Third Down Defense Raw" in out:
        league = pd.to_numeric(out["Third Down Defense Raw"], errors="coerce").median()
        out["Third Down Defense Edge"] = league - pd.to_numeric(out["Third Down Defense Raw"], errors="coerce")
    if "Red Zone Defense Raw" in out:
        league = pd.to_numeric(out["Red Zone Defense Raw"], errors="coerce").median()
        out["Red Zone Defense Edge"] = league - pd.to_numeric(out["Red Zone Defense Raw"], errors="coerce")
    return out


def _fill_neutral(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column, fallback in NEUTRAL.items():
        if column not in out: out[column] = fallback
        values = pd.to_numeric(out[column], errors="coerce")
        median = values.median()
        out[column] = values.fillna(median if math.isfinite(_num(median, np.nan)) else fallback)
    for column in ["Games", "FBS Games"]:
        if column not in out: out[column] = 0
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0)
    return out


def _prior_components(frame: pd.DataFrame) -> pd.DataFrame:
    out = _fill_neutral(_opponent_edge_columns(frame))
    performance = (
        0.24 * _z(out.get("WEPA Rating", out["Result Power"] if "Result Power" in out else pd.Series(0, index=out.index))) +
        0.18 * _z(out.get("SP+ Rating", pd.Series(0, index=out.index))) +
        0.10 * _z(out.get("FPI Rating", pd.Series(0, index=out.index))) +
        0.08 * _z(out.get("SRS Rating", pd.Series(0, index=out.index))) +
        0.15 * _z(out["EPA/PPA Offense"]) + 0.15 * _z(out["EPA/PPA Defense Edge"]) +
        0.05 * _z(out["Success Rate Offense"]) + 0.05 * _z(out["Success Rate Defense Edge"])
    ) * 8.0
    returning = (
        0.40 * _z(out["Returning Production"]) + 0.25 * _z(out["Returning Passing"]) +
        0.18 * _z(out["Returning Receiving"]) + 0.17 * _z(out["Returning Rushing"])
    ) * 5.0
    roster = (0.58 * _z(out["Talent Rating"]) + 0.27 * _z(out["Recruiting Rating"]) + 0.15 * _z(out["Portal Rating"])) * 5.0
    continuity = (0.50 * _z(out["QB Continuity"]) + 0.30 * _z(out["Coaching Continuity"]) + 0.20 * _z(out["Coordinator Continuity"])) * 4.0
    out["Preseason Rating"] = 0.55 * performance + 0.20 * returning + 0.15 * roster + 0.10 * continuity
    return out


def _current_components(frame: pd.DataFrame) -> pd.DataFrame:
    out = _fill_neutral(_opponent_edge_columns(frame))
    score_offense = _z(pd.to_numeric(out.get("Points Per Game", 28.0), errors="coerce")) * 7.0
    score_defense = -_z(pd.to_numeric(out.get("Points Allowed Per Game", 28.0), errors="coerce")) * 7.0
    result_power = _z(pd.to_numeric(out.get("Result Power", 0.0), errors="coerce")) * 8.0
    advanced_off = (
        0.24 * _z(out["EPA/PPA Offense"]) + 0.16 * _z(out["Success Rate Offense"]) +
        0.13 * _z(out["Pass EPA/PPA"]) + 0.09 * _z(out["Rush EPA/PPA"]) +
        0.09 * _z(out["Explosiveness Offense"]) + 0.08 * _z(out["Finishing Drives Offense"]) +
        0.05 * _z(out["Field Position Offense"]) + 0.05 * _z(out["Line Yards Offense"]) +
        0.04 * _z(out["Third Down Rate"]) + 0.04 * _z(out["Red Zone TD Rate"]) -
        0.02 * _z(out["Turnover Rate"]) - 0.01 * _z(out["Sack Rate Allowed"])
    ) * 9.0
    advanced_def = (
        0.25 * _z(out["EPA/PPA Defense Edge"]) + 0.17 * _z(out["Success Rate Defense Edge"]) +
        0.13 * _z(out["Pass Defense Edge"]) + 0.09 * _z(out["Rush Defense Edge"]) +
        0.09 * _z(out["Explosiveness Defense Edge"]) + 0.08 * _z(out["Finishing Drives Defense Edge"]) +
        0.05 * _z(out["Havoc Created"]) + 0.04 * _z(out["Stuff Rate Edge"]) +
        0.04 * _z(out["Third Down Defense Edge"]) + 0.03 * _z(out["Red Zone Defense Edge"]) +
        0.03 * _z(out["Takeaway Rate"])
    ) * 9.0
    # Score-based components make the model usable immediately. Advanced PBP
    # automatically takes over more of the rating after its background cache is ready.
    has_advanced = any(col in frame.columns and pd.to_numeric(frame[col], errors="coerce").notna().any()
                       for col in ["Advanced Plays", "EPA/PPA Defense Raw", "Pass Defense Raw"])
    advanced_weight = 0.78 if has_advanced else 0.20
    off = advanced_weight * advanced_off + (1.0 - advanced_weight) * score_offense
    defense = advanced_weight * advanced_def + (1.0 - advanced_weight) * score_defense
    special = (0.60 * _z(out.get("SP Special Teams Rating", pd.Series(0, index=out.index))) + 0.25 * _z(out["Field Position Offense"]) + 0.15 * _z(out.get("Kicking Rating", pd.Series(0, index=out.index)))) * 2.0
    out["Offense Rating"] = off
    out["Defense Rating"] = defense
    out["Special Teams Rating"] = special
    out["Current Power"] = 0.37 * off + 0.37 * defense + 0.06 * special + 0.20 * result_power
    return out


def build_team_ratings(season: int, week: int) -> pd.DataFrame:
    prior_frame, prior_avail = _season_features(season - 1, None)
    current_frame, current_avail = _season_features(season, week)
    # Previous-season performance forms the 55% performance prior, while the
    # upcoming season's returning production, talent, recruiting, portal, and
    # continuity inputs form the roster/continuity portions of the preseason prior.
    prior_input = prior_frame.copy()
    roster_columns = [
        "Returning Production", "Returning Passing", "Returning Receiving",
        "Returning Rushing", "Talent Rating", "Recruiting Rating", "Portal Rating",
        "QB Continuity", "Coaching Continuity", "Coordinator Continuity",
    ]
    current_lookup = current_frame.set_index("Team") if not current_frame.empty else pd.DataFrame()
    if not current_lookup.empty:
        for column in roster_columns:
            if column in current_lookup.columns:
                mapping = current_lookup[column].to_dict()
                prior_input[column] = prior_input["Team"].map(mapping).combine_first(prior_input.get(column, pd.Series(index=prior_input.index, dtype=float)))
    prior = _prior_components(prior_input)
    current = _current_components(current_frame)
    prior_weight, current_weight = _season_weights(week)
    teams = sorted(set(prior["Team"]) | set(current["Team"]))
    rows = []
    for team in teams:
        prow = prior[prior["Team"] == team].iloc[-1].to_dict() if team in set(prior["Team"]) else {"Team": team}
        crow = current[current["Team"] == team].iloc[-1].to_dict() if team in set(current["Team"]) else {"Team": team}
        merged = {**{k: NEUTRAL.get(k, 0.0) for k in NEUTRAL}, **prow, **crow}
        preseason = _num(prow.get("Preseason Rating"), 0.0)
        current_power = _num(crow.get("Current Power"), preseason)
        games = _num(crow.get("Games"), 0.0); fbs_games = _num(crow.get("FBS Games"), 0.0)
        sample_factor = clamp(fbs_games / 6.0, 0.0, 1.0)
        effective_current = current_weight * sample_factor
        effective_prior = 1.0 - effective_current
        data_conf = 34.0 + 28.0 * sample_factor + (14.0 if current_avail["advanced"] else 0.0) + (10.0 if current_avail["roster"] else 0.0) + min(10.0, fbs_games * 1.5)
        row = {
            "Team": team, "Conference": _text(merged.get("Conference")), "Classification": _text(merged.get("Classification", "fbs")),
            "Season": season, "Projection Week": week, "Previous Season Weight": round(effective_prior, 3), "Current Season Weight": round(effective_current, 3),
            "Preseason Rating": round(preseason, 3), "Power Rating": round(effective_prior * preseason + effective_current * current_power, 3),
            "Offense Rating": round(_num(crow.get("Offense Rating"), preseason), 3), "Defense Rating": round(_num(crow.get("Defense Rating"), preseason), 3),
            "Special Teams Rating": round(_num(crow.get("Special Teams Rating"), 0.0), 3),
            "Games": int(games), "FBS Games": int(fbs_games), "Data Confidence": round(clamp(data_conf, 20.0, 98.0), 1),
            "Advanced Data Available": bool(current_avail["advanced"]), "Roster Data Available": bool(current_avail["roster"]),
            "Source": "Free ESPN results with automatic SportsDataverse advanced-cache upgrade",
            "Updated": _now(),
        }
        for col in RATING_COLUMNS:
            if col not in row and col in merged: row[col] = merged[col]
        rows.append(row)
    output = pd.DataFrame(rows)
    for col in RATING_COLUMNS:
        if col not in output: output[col] = ""
    output = output[RATING_COLUMNS].sort_values("Power Rating", ascending=False)
    _upsert(RATINGS_TAB, output, RATING_COLUMNS, ["Team", "Season", "Projection Week"])
    return output

# ---------------------------------------------------------------------------
# Venue, travel, rest, weather, and personnel
# ---------------------------------------------------------------------------

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    if not all(math.isfinite(x) for x in [lat1, lon1, lat2, lon2]): return 0.0
    radius = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(max(1e-12, 1 - a)))


def _timezone_bucket(longitude: float) -> int:
    if not math.isfinite(longitude): return 0
    if longitude > -82.5: return -5
    if longitude > -97.5: return -6
    if longitude > -112.5: return -7
    return -8


@st.cache_data(ttl=86400, show_spinner=False)
def _historical_hfa_context(season: int, lookback: int = 1) -> tuple[float, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for year in range(season - lookback, season):
        games = _parse_games(_espn_games_payload(year), year)
        if games.empty:
            continue
        strength = _result_strength(games)
        powers = dict(zip(strength.get("Team", []), pd.to_numeric(strength.get("Result Power", []), errors="coerce")))
        done = games[games["Completed"].map(_bool) & ~games["Neutral Site"].map(_bool)]
        for _, game in done.iterrows():
            actual = _num(game["Home Score"]) - _num(game["Away Score"])
            expected_neutral = _num(powers.get(game["Home Team"], 0.0)) - _num(powers.get(game["Away Team"], 0.0))
            rows.append({
                "Home Team": _text(game["Home Team"]),
                "Venue ID": _text(game["Venue ID"]),
                "Residual": clamp(actual - expected_neutral, -28.0, 28.0),
            })
    frame = pd.DataFrame(rows)
    raw = float(pd.to_numeric(frame.get("Residual"), errors="coerce").mean()) if not frame.empty else 1.5
    league = (48.0 * 1.5 + len(frame) * raw) / (48.0 + len(frame))
    return round(clamp(league, 0.25, 3.0), 3), frame


@st.cache_data(ttl=86400, show_spinner=False)
def _historical_hfa(season: int, home_team: str, venue_id: str, lookback: int = 1) -> tuple[float, float, int, int]:
    league, frame = _historical_hfa_context(season, lookback)
    if frame.empty:
        return league, 0.0, 0, 0
    venue_rows = frame[frame["Venue ID"].astype(str) == str(venue_id)] if venue_id else pd.DataFrame()
    team_rows = frame[frame["Home Team"].astype(str) == str(home_team)]
    target = venue_rows if not venue_rows.empty else team_rows
    raw = float(pd.to_numeric(target.get("Residual"), errors="coerce").mean()) if not target.empty else league
    venue = (40.0 * league + len(target) * raw) / (40.0 + len(target))
    return league, round(clamp(venue - league, -2.5, 3.5), 3), len(frame), len(target)



@st.cache_data(ttl=86400 * 30, show_spinner=False)
def _team_home_coordinates(season: int) -> dict[str, tuple[float, float, float, str]]:
    """Build a no-geocoder coordinate map from ESPN-listed home venues.

    Current-season coordinates are preferred; prior-season home venues fill gaps.
    Missing teams remain unknown instead of being sent to a restricted geocoder.
    """
    coordinates: dict[str, tuple[float, float, float, str]] = {}
    for year in (season, season - 1):
        try:
            games = _espn_games_payload(year)
        except Exception:
            games = []
        for game in games:
            if _bool(game.get("neutralSite")):
                continue
            team = _normalize_team(game.get("homeTeam"))
            lat = _num(game.get("latitude"), np.nan)
            lon = _num(game.get("longitude"), np.nan)
            if not team or not math.isfinite(lat) or not math.isfinite(lon):
                continue
            elevation = _num(game.get("elevation"), 0.0)
            label = _text(game.get("location")) or _text(game.get("venue"))
            coordinates.setdefault(team, (lat, lon, elevation, label))
    return coordinates



def _nws_weather(latitude: float, longitude: float, game_time: str) -> dict[str, float | str]:
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        return {}
    try:
        target = datetime.fromisoformat(game_time.replace("Z", "+00:00"))
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        horizon_days = (target.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds() / 86400.0
        # NWS hourly forecasts are not meaningful outside roughly seven days.
        if horizon_days > 7.5 or horizon_days < -1.0:
            return {}
    except Exception:
        target = datetime.now(timezone.utc)
    cache = _cache_path("open_weather", {"lat": round(latitude, 3), "lon": round(longitude, 3), "game": game_time[:13]})
    if cache.exists() and time.time() - cache.stat().st_mtime < 7200:
        try:
            return json.loads(cache.read_text())
        except Exception:
            pass
    try:
        headers = {"User-Agent": "EZPZ Picks college football model contact=admin@ezpzpicks.com"}
        point_response = requests.get(f"https://api.weather.gov/points/{latitude:.4f},{longitude:.4f}", headers=headers, timeout=(4, 8))
        point_response.raise_for_status()
        point = point_response.json()
        hourly_url = _dig(point, "properties.forecastHourly", default="")
        if not hourly_url:
            return {}
        hourly_response = requests.get(hourly_url, headers=headers, timeout=(4, 8))
        hourly_response.raise_for_status()
        periods = _dig(hourly_response.json(), "properties.periods", default=[]) or []
        selected = min(periods, key=lambda p: abs((datetime.fromisoformat(_text(p.get("startTime")).replace("Z", "+00:00")) - target).total_seconds())) if periods else {}
        if selected:
            wind = _text(selected.get("windSpeed", "0")).split()[0]
            precip = _dig(selected, "probabilityOfPrecipitation.value", default=0)
            result = {"temperature": _num(selected.get("temperature"), np.nan), "wind": _num(wind, np.nan), "precipitation": _pct(precip, np.nan), "source": "National Weather Service hourly (free/open)"}
            cache.write_text(json.dumps(result))
            return result
    except Exception:
        pass
    return {}



def _weather_adjustment(temperature: float, wind: float, precip: float, roof: str) -> tuple[float, float, str]:
    if "dome" in roof.lower() or "indoor" in roof.lower() or "closed" in roof.lower(): return 0.0, 0.0, "Indoor environment"
    total = 0.0; home = 0.0; notes = []
    if wind >= 25: total -= 6.0; home += 0.35; notes.append("extreme wind")
    elif wind >= 20: total -= 4.0; home += 0.20; notes.append("strong wind")
    elif wind >= 15: total -= 2.0; notes.append("moderate wind")
    if temperature <= 20: total -= 3.0; home += 0.15; notes.append("extreme cold")
    elif temperature <= 32: total -= 1.5; notes.append("freezing temperature")
    elif temperature >= 95: total -= 0.8; notes.append("extreme heat")
    if precip >= 0.70: total -= 2.5; notes.append("high precipitation risk")
    elif precip >= 0.40: total -= 1.2; notes.append("precipitation risk")
    return total, home, ", ".join(notes) if notes else "Normal outdoor conditions"



def build_environment(game: pd.Series, season: int, manual_roof: str | None = None) -> Environment:
    roof = _text(manual_roof or game.get("Roof") or "Outdoor/Unknown")
    if _bool(game.get("Neutral Site")):
        return Environment(home_field=0.0, league_hfa=0.0, weather_confidence=60.0, roof=roof, stadium=_text(game.get("Stadium")), notes="Neutral site: no home-field advantage")
    league, venue_component, _, venue_n = _historical_hfa(season, _text(game.get("Home Team")), _text(game.get("Venue ID")))
    home_lat = _num(game.get("Latitude"), np.nan); home_lon = _num(game.get("Longitude"), np.nan)
    elevation = _num(game.get("Elevation"), 0.0)
    location = _text(game.get("Location")) or _text(game.get("Stadium"))
    coordinate_map = _team_home_coordinates(season)
    if not math.isfinite(home_lat) or not math.isfinite(home_lon):
        home_coords = coordinate_map.get(_normalize_team(game.get("Home Team")))
        if home_coords:
            home_lat, home_lon, home_elevation, home_label = home_coords
            elevation = elevation or home_elevation
            location = home_label or location
    away_coords = coordinate_map.get(_normalize_team(game.get("Away Team")))
    if away_coords:
        away_lat, away_lon = away_coords[0], away_coords[1]
    else:
        away_lat, away_lon = np.nan, np.nan
    coordinates_confirmed = all(math.isfinite(v) for v in [away_lat, away_lon, home_lat, home_lon])
    miles = _haversine(away_lat, away_lon, home_lat, home_lon) if coordinates_confirmed else 0.0
    travel = -clamp(max(0.0, miles - 300.0) / 1000.0 * 0.35, 0.0, 0.75) if coordinates_confirmed else 0.0
    zones = abs(_timezone_bucket(home_lon) - _timezone_bucket(away_lon)) if coordinates_confirmed else 0
    timezone_adj = -min(0.45, zones * 0.15) if coordinates_confirmed else 0.0
    altitude = min(0.55, max(0.0, elevation - 2500.0) / 5000.0 * 0.55)
    rest_diff = _num(game.get("Home Rest"), 7.0) - _num(game.get("Away Rest"), 7.0)
    rest = clamp(rest_diff * 0.12, -0.75, 0.75)
    capacity = _num(game.get("Capacity"), 0.0)
    crowd = clamp((capacity - 45000.0) / 70000.0 * 0.25, -0.08, 0.25) if capacity else 0.0
    home_field = clamp(league + venue_component + altitude + rest + crowd - travel - timezone_adj, -1.0, 8.5)

    temp = _num(game.get("Temperature"), np.nan); wind = _num(game.get("Wind"), np.nan); precip = _pct(game.get("Precipitation Probability"), np.nan)
    source = _text(game.get("Weather Source")); confidence = 72.0 if source else 35.0
    if not all(math.isfinite(v) for v in [temp, wind, precip]):
        weather = _nws_weather(home_lat, home_lon, _text(game.get("Game Time")))
        temp = _num(weather.get("temperature"), temp)
        wind = _num(weather.get("wind"), wind)
        precip = _pct(weather.get("precipitation"), precip)
        if weather:
            source = _text(weather.get("source")); confidence = 85.0
    weather_confirmed = all(math.isfinite(v) for v in [temp, wind, precip])
    if weather_confirmed:
        total_weather, home_weather, weather_note = _weather_adjustment(temp, wind, precip, roof)
    else:
        # Unknown weather is neutral, not an assumed normal forecast.
        total_weather, home_weather, weather_note = 0.0, 0.0, "Weather unconfirmed; no adjustment applied"
        confidence = min(confidence, 35.0)
    display_temp = temp if math.isfinite(temp) else np.nan
    display_wind = wind if math.isfinite(wind) else np.nan
    display_precip = precip if math.isfinite(precip) else np.nan
    travel_note = f"{round(miles)} travel miles; {zones} time zones" if coordinates_confirmed else "travel coordinates unconfirmed; no travel/time-zone adjustment"
    notes = f"{travel_note}; venue sample {venue_n}; {weather_note}; weather source {source or 'NWS unavailable/manual'}"
    return Environment(round(home_field + home_weather, 3), league, venue_component, travel, timezone_adj, altitude, rest, total_weather, home_weather, display_temp, display_wind, display_precip, confidence, roof, _text(game.get("Stadium")), notes)



def _personnel_from_sheet(season: int, week: int, game_id: str, team: str) -> Personnel:
    frame = _sheet(PERSONNEL_TAB, PERSONNEL_COLUMNS)
    if frame.empty: return Personnel()
    subset = frame[(frame["Season"].astype(str) == str(season)) & (frame["Game ID"].astype(str) == str(game_id)) & (frame["Team"].astype(str) == str(team))]
    if subset.empty:
        subset = frame[(frame["Season"].astype(str) == str(season)) & (frame["Week"].astype(str) == str(week)) & (frame["Team"].astype(str) == str(team))]
    if subset.empty: return Personnel()
    row = subset.iloc[-1]
    return Personnel(
        _text(row.get("Expected QB"), "Unconfirmed"), _bool(row.get("QB Confirmed")), _pct(row.get("QB Continuity"), 0.50),
        _num(row.get("QB Adjustment")), _num(row.get("OL Adjustment")), _num(row.get("Skill Adjustment")),
        _num(row.get("Defensive Line Adjustment")), _num(row.get("Linebacker Adjustment")), _num(row.get("Secondary Adjustment")),
        _num(row.get("Kicker Adjustment")), _num(row.get("Special Teams Adjustment")), _pct(row.get("Coaching Continuity"), 0.75),
        _pct(row.get("Coordinator Continuity"), 0.67), _num(row.get("Availability Confidence"), 45.0),
        _text(row.get("Source"), "Saved manual snapshot"), _text(row.get("Notes")),
    )


def _personnel_editor(team: str, base: Personnel, season: int, week: int, game_id: str, key: str) -> Personnel:
    st.markdown(f"**{team} personnel**")
    c1, c2 = st.columns([2, 1])
    with c1: qb = st.text_input("Expected starting QB", value=base.expected_qb, key=f"{key}_qb")
    with c2: confirmed = st.checkbox("QB confirmed", value=base.qb_confirmed, key=f"{key}_confirmed")
    qb_cont = st.slider("QB continuity", 0, 100, int(base.qb_continuity * 100), key=f"{key}_qbcont") / 100
    availability = st.slider("Availability confidence", 0, 100, int(base.availability_confidence), key=f"{key}_availability")
    a, b, c = st.columns(3)
    with a:
        qb_adj = st.number_input("QB points", -10.0, 10.0, float(base.qb_adjustment), 0.25, key=f"{key}_qba")
        ol_adj = st.number_input("Offensive line", -6.0, 6.0, float(base.ol_adjustment), 0.25, key=f"{key}_ola")
        skill_adj = st.number_input("RB/WR/TE", -5.0, 5.0, float(base.skill_adjustment), 0.25, key=f"{key}_skilla")
    with b:
        dl_adj = st.number_input("Defensive line", -5.0, 5.0, float(base.dl_adjustment), 0.25, key=f"{key}_dla")
        lb_adj = st.number_input("Linebackers", -4.0, 4.0, float(base.linebacker_adjustment), 0.25, key=f"{key}_lba")
        sec_adj = st.number_input("Secondary", -5.0, 5.0, float(base.secondary_adjustment), 0.25, key=f"{key}_seca")
    with c:
        kick_adj = st.number_input("Kicker", -3.0, 3.0, float(base.kicker_adjustment), 0.25, key=f"{key}_kicka")
        st_adj = st.number_input("Special teams", -3.0, 3.0, float(base.special_teams_adjustment), 0.25, key=f"{key}_sta")
        coach = st.slider("Coaching continuity", 0, 100, int(base.coaching_continuity * 100), key=f"{key}_coach") / 100
    coord = st.slider("Coordinator continuity", 0, 100, int(base.coordinator_continuity * 100), key=f"{key}_coord") / 100
    notes = st.text_area("Personnel notes", value=base.notes, key=f"{key}_notes", height=70)
    return Personnel(qb, confirmed, qb_cont, qb_adj, ol_adj, skill_adj, dl_adj, lb_adj, sec_adj, kick_adj, st_adj, coach, coord, availability, "Manual builder confirmation", notes)


def personnel_row(personnel: Personnel, team: str, season: int, week: int, game_id: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "Date": date.today().isoformat(), "Season": season, "Week": week, "Game ID": game_id, "Team": team,
        "Expected QB": personnel.expected_qb, "QB Confirmed": personnel.qb_confirmed, "QB Continuity": personnel.qb_continuity,
        "QB Adjustment": personnel.qb_adjustment, "OL Adjustment": personnel.ol_adjustment, "Skill Adjustment": personnel.skill_adjustment,
        "Defensive Line Adjustment": personnel.dl_adjustment, "Linebacker Adjustment": personnel.linebacker_adjustment,
        "Secondary Adjustment": personnel.secondary_adjustment, "Kicker Adjustment": personnel.kicker_adjustment,
        "Special Teams Adjustment": personnel.special_teams_adjustment, "Coaching Continuity": personnel.coaching_continuity,
        "Coordinator Continuity": personnel.coordinator_continuity, "Availability Confidence": personnel.availability_confidence,
        "Source": personnel.source, "Notes": personnel.notes, "Model Version": MODEL_VERSION,
    }]).reindex(columns=PERSONNEL_COLUMNS)


def save_personnel(personnel: Personnel, team: str, season: int, week: int, game_id: str) -> None:
    _upsert(
        PERSONNEL_TAB,
        personnel_row(personnel, team, season, week, game_id),
        PERSONNEL_COLUMNS,
        ["Season", "Game ID", "Team"],
    )

# ---------------------------------------------------------------------------
# Unified projected-score and simulation engine
# ---------------------------------------------------------------------------

def _rating_row(ratings: pd.DataFrame, team: str) -> dict[str, Any]:
    if ratings.empty or team not in set(ratings["Team"]):
        row = {"Team": team, "Power Rating": 0.0, "Offense Rating": 0.0, "Defense Rating": 0.0, "Special Teams Rating": 0.0, "Data Confidence": 20.0, "Games": 0, "FBS Games": 0, "Previous Season Weight": 1.0, "Current Season Weight": 0.0}
        row.update(NEUTRAL); return row
    return ratings[ratings["Team"] == team].iloc[-1].to_dict()


def _calibration_adjustments() -> tuple[float, float, int]:
    frame = _sheet(CALIBRATION_TAB, CALIBRATION_COLUMNS)
    if frame.empty: return 0.0, 0.0, 0
    frame = frame[frame["Model Version"].astype(str).str.startswith("cfb-v1")]
    margin = pd.to_numeric(frame.get("Margin Residual"), errors="coerce").dropna().tail(200)
    total = pd.to_numeric(frame.get("Total Residual"), errors="coerce").dropna().tail(200)
    n = min(len(margin), len(total)) if len(margin) and len(total) else max(len(margin), len(total))
    weight = min(0.65, n / 120.0)
    margin_adj = clamp(float(margin.mean()) * weight if len(margin) else 0.0, -2.0, 2.0)
    total_adj = clamp(float(total.mean()) * weight if len(total) else 0.0, -3.0, 3.0)
    return margin_adj, total_adj, n


def _matchup_components(offense: dict[str, Any], defense: dict[str, Any]) -> dict[str, float]:
    return {
        "overall": 0.60 * (_num(offense.get("Offense Rating")) - _num(defense.get("Defense Rating"))),
        "pass": 10.0 * (_num(offense.get("Pass EPA/PPA")) - _num(defense.get("Pass Defense Edge"))),
        "rush": 8.0 * (_num(offense.get("Rush EPA/PPA")) - _num(defense.get("Rush Defense Edge"))),
        "success": 22.0 * ((_pct(offense.get("Success Rate Offense"), 0.42) - 0.42) - _num(defense.get("Success Rate Defense Edge"))),
        "explosive": 1.8 * ((_num(offense.get("Explosiveness Offense"), 1.0) - 1.0) - _num(defense.get("Explosiveness Defense Edge"))),
        "line": 1.2 * ((_num(offense.get("Line Yards Offense"), 2.7) - 2.7) - _num(defense.get("Line Yards Defense Edge"))),
        "havoc": 9.0 * ((_num(defense.get("Havoc Created"), 0.16) - 0.16) + (_num(offense.get("Havoc Allowed"), 0.16) - 0.16)) * -1.0,
        "finishing": 0.75 * ((_num(offense.get("Finishing Drives Offense"), 4.2) - 4.2) - _num(defense.get("Finishing Drives Defense Edge"))),
        "field_position": 0.16 * ((_num(offense.get("Field Position Offense"), 29.0) - 29.0) - _num(defense.get("Field Position Defense Edge"))),
        "third_down": 8.0 * ((_pct(offense.get("Third Down Rate"), 0.40) - 0.40) - _num(defense.get("Third Down Defense Edge"))),
        "red_zone": 5.0 * ((_pct(offense.get("Red Zone TD Rate"), 0.62) - 0.62) - _num(defense.get("Red Zone Defense Edge"))),
        "turnover": -8.0 * ((_pct(offense.get("Turnover Rate"), 0.12) - 0.12) - (_pct(defense.get("Takeaway Rate"), 0.12) - 0.12)),
        "sack": -7.0 * ((_pct(offense.get("Sack Rate Allowed"), 0.065) - 0.065) + (_pct(defense.get("Sack Rate Created"), 0.065) - 0.065)),
    }


def _expected_possessions(away: dict[str, Any], home: dict[str, Any], market_spread: float) -> float:
    away_poss = _num(away.get("Possessions Per Game"), 12.0); home_poss = _num(home.get("Possessions Per Game"), 12.0)
    away_plays = _num(away.get("Plays Per Game"), 69.0); home_plays = _num(home.get("Plays Per Game"), 69.0)
    away_pace = _num(away.get("Pace Seconds Per Play"), 27.5); home_pace = _num(home.get("Pace Seconds Per Play"), 27.5)
    possessions = 0.42 * (away_poss + home_poss) / 2 + 0.38 * ((away_plays + home_plays) / 2 / 5.75) + 0.20 * (27.5 / max(20.0, (away_pace + home_pace) / 2) * 12.0)
    # Large favorites often shorten late games, while close games maintain normal tempo.
    possessions -= max(0.0, abs(market_spread) - 17.0) * 0.018
    return clamp(possessions, 9.0, 16.5)


def _project_points(offense: dict[str, Any], defense: dict[str, Any], possessions: float) -> tuple[float, dict[str, float]]:
    components = _matchup_components(offense, defense)
    scoring_average = 0.55 * _num(offense.get("Points Per Game"), 28.0) + 0.45 * _num(defense.get("Points Allowed Per Game"), 28.0)
    drive_average = (0.58 * _num(offense.get("Points Per Drive"), 2.3) + 0.42 * _num(defense.get("Points Allowed Per Drive"), 2.3)) * possessions
    base = 0.46 * scoring_average + 0.54 * drive_average
    adjustment = sum(components.values())
    points = base + clamp(adjustment, -13.0, 13.0)
    return clamp(points, 7.0, 58.0), components


def project_matchup(game: pd.Series, ratings: pd.DataFrame, away_personnel: Personnel,
                    home_personnel: Personnel, environment: Environment) -> dict[str, Any]:
    away = _rating_row(ratings, _text(game["Away Team"])); home = _rating_row(ratings, _text(game["Home Team"]))
    market_spread = _num(game.get("Home Spread"), 0.0)
    possessions = _expected_possessions(away, home, market_spread)
    away_points, away_components = _project_points(away, home, possessions)
    home_points, home_components = _project_points(home, away, possessions)

    # Personnel is expressed in team points. Defensive absences are entered as negative,
    # therefore subtracting the opponent's defensive adjustment raises expected scoring.
    away_points += away_personnel.offense_adjustment - home_personnel.defense_adjustment + away_personnel.kicker_adjustment + away_personnel.special_teams_adjustment
    home_points += home_personnel.offense_adjustment - away_personnel.defense_adjustment + home_personnel.kicker_adjustment + home_personnel.special_teams_adjustment
    away_points -= environment.home_field / 2.0
    home_points += environment.home_field / 2.0
    away_points += environment.weather_total_adjustment / 2.0
    home_points += environment.weather_total_adjustment / 2.0

    # Continuity matters most early and fades as current-season evidence accumulates.
    prior_weight = (_num(away.get("Previous Season Weight"), 1.0) + _num(home.get("Previous Season Weight"), 1.0)) / 2
    away_points += prior_weight * (away_personnel.qb_continuity - 0.50) * 2.4
    home_points += prior_weight * (home_personnel.qb_continuity - 0.50) * 2.4
    away_points += prior_weight * ((away_personnel.coaching_continuity - 0.75) + (away_personnel.coordinator_continuity - 0.67)) * 1.0
    home_points += prior_weight * ((home_personnel.coaching_continuity - 0.75) + (home_personnel.coordinator_continuity - 0.67)) * 1.0

    margin_adj, total_adj, calibration_n = _calibration_adjustments()
    away_points += total_adj / 2.0 - margin_adj / 2.0
    home_points += total_adj / 2.0 + margin_adj / 2.0
    away_points = clamp(away_points, 3.0, 65.0); home_points = clamp(home_points, 3.0, 65.0)
    return {
        "away": away, "home": home, "away_points": away_points, "home_points": home_points,
        "margin": home_points - away_points, "total": home_points + away_points,
        "possessions": possessions, "away_components": away_components, "home_components": home_components,
        "calibration_n": calibration_n, "margin_calibration": margin_adj, "total_calibration": total_adj,
    }


def _drive_probabilities(points_per_possession: float, turnover_rate: float, explosive: float) -> np.ndarray:
    ppp = clamp(points_per_possession, 0.55, 4.6)
    turnover = clamp(turnover_rate, 0.06, 0.22)
    # Shares create realistic 2/3/6/7-point increments while preserving target mean.
    td_share = clamp(0.70 + 0.05 * (explosive - 1.0), 0.62, 0.79)
    fg_share = 0.96 - td_share
    td = ppp * td_share / 7.0
    fg = ppp * fg_share / 3.0
    defensive_td = clamp(turnover * 0.028, 0.001, 0.008)
    safety = clamp(turnover * 0.012, 0.0005, 0.004)
    td = max(0.0, td - defensive_td)
    empty = max(0.02, 1.0 - td - fg - defensive_td - safety)
    probs = np.array([empty, fg, td, defensive_td, safety], dtype=float)
    return probs / probs.sum()


def simulate_game(projection: dict[str, Any], seed: str, simulations: int = SIMULATIONS) -> dict[str, Any]:
    digest = hashlib.sha256(f"{seed}-{MODEL_VERSION}".encode()).hexdigest()
    rng = np.random.default_rng(int(digest[:16], 16))
    poss_mean = projection["possessions"]
    shared_tempo = rng.normal(0.0, 0.8, simulations)
    away_possessions = np.clip(np.rint(poss_mean + shared_tempo + rng.normal(0, 0.55, simulations)), 7, 20).astype(int)
    home_possessions = np.clip(np.rint(poss_mean + shared_tempo + rng.normal(0, 0.55, simulations)), 7, 20).astype(int)
    away_ppp = projection["away_points"] / poss_mean; home_ppp = projection["home_points"] / poss_mean
    away_rating = projection["away"]; home_rating = projection["home"]
    away_probs = _drive_probabilities(away_ppp, _pct(away_rating.get("Turnover Rate"), 0.12), _num(away_rating.get("Explosiveness Offense"), 1.0))
    home_probs = _drive_probabilities(home_ppp, _pct(home_rating.get("Turnover Rate"), 0.12), _num(home_rating.get("Explosiveness Offense"), 1.0))
    values = np.array([0, 3, 7, 6, 2])
    away_scores = np.zeros(simulations, dtype=int); home_scores = np.zeros(simulations, dtype=int)
    for drive in range(20):
        away_scores += np.where(drive < away_possessions, rng.choice(values, simulations, p=away_probs), 0)
        home_scores += np.where(drive < home_possessions, rng.choice(values, simulations, p=home_probs), 0)
    # Tune distribution means to the deterministic score without destroying football increments.
    away_shift = projection["away_points"] - float(away_scores.mean()); home_shift = projection["home_points"] - float(home_scores.mean())
    away_scores = np.maximum(0, np.rint(away_scores + away_shift)).astype(int)
    home_scores = np.maximum(0, np.rint(home_scores + home_shift)).astype(int)
    ties = away_scores == home_scores
    while ties.any():
        count = int(ties.sum()); winner_home = rng.random(count) < 0.5; ot_points = rng.choice([3, 7], count, p=[0.48, 0.52])
        home_scores[ties] += np.where(winner_home, ot_points, 0); away_scores[ties] += np.where(winner_home, 0, ot_points)
        ties = away_scores == home_scores
    margins = home_scores - away_scores; totals = home_scores + away_scores
    return {
        "away_scores": away_scores, "home_scores": home_scores, "margins": margins, "totals": totals,
        "away_mean": float(away_scores.mean()), "home_mean": float(home_scores.mean()),
        "away_p10": float(np.percentile(away_scores, 10)), "away_p90": float(np.percentile(away_scores, 90)),
        "home_p10": float(np.percentile(home_scores, 10)), "home_p90": float(np.percentile(home_scores, 90)),
        "home_win": float(np.mean(margins > 0)), "away_win": float(np.mean(margins < 0)),
    }


def _prob_with_push(values: np.ndarray, threshold: float, over: bool = True) -> tuple[float, float]:
    diff = values + threshold if over else threshold - values
    win = float(np.mean(diff > 1e-9)); push = float(np.mean(np.abs(diff) <= 1e-9))
    conditional = win / max(1e-9, 1.0 - push)
    return conditional, push


def _spread_market(sim: dict[str, Any], home_spread: float, home: str, away: str) -> dict[str, Any]:
    home_prob, push = _prob_with_push(sim["margins"], home_spread, True)
    if home_prob >= 0.5:
        return {"pick": f"{home} {home_spread:+g}", "team": home, "probability": home_prob, "push": push, "model_edge_points": float(np.mean(sim["margins"])) + home_spread}
    away_line = -home_spread; return {"pick": f"{away} {away_line:+g}", "team": away, "probability": 1.0 - home_prob, "push": push, "model_edge_points": -(float(np.mean(sim["margins"])) + home_spread)}


def _total_market(sim: dict[str, Any], total_line: float) -> dict[str, Any]:
    over_prob, push = _prob_with_push(sim["totals"], -total_line, True)
    model_total = float(np.mean(sim["totals"]))
    if over_prob >= 0.5: return {"pick": f"Over {total_line:g}", "probability": over_prob, "push": push, "model_edge_points": model_total - total_line}
    return {"pick": f"Under {total_line:g}", "probability": 1.0 - over_prob, "push": push, "model_edge_points": total_line - model_total}


def _moneyline_market(sim: dict[str, Any], home: str, away: str, home_odds: float, away_odds: float) -> dict[str, Any]:
    home_market, away_market = _no_vig(home_odds, away_odds)
    options = []
    if home_odds:
        options.append({"pick": home, "probability": sim["home_win"], "odds": home_odds, "implied": home_market, "edge": sim["home_win"] - home_market, "ev": _american_ev(sim["home_win"], home_odds)})
    if away_odds:
        options.append({"pick": away, "probability": sim["away_win"], "odds": away_odds, "implied": away_market, "edge": sim["away_win"] - away_market, "ev": _american_ev(sim["away_win"], away_odds)})
    if not options:
        winner = home if sim["home_win"] >= sim["away_win"] else away; probability = max(sim["home_win"], sim["away_win"])
        return {"pick": winner, "probability": probability, "odds": 0.0, "implied": 0.5, "edge": probability - 0.5, "ev": 0.0}
    return max(options, key=lambda x: x["ev"])


def _confluence(projection: dict[str, Any], pick_team: str, home_team: str, market: str, direction: str = "") -> tuple[int, list[str]]:
    side = "home" if pick_team == home_team else "away"; other = "away" if side == "home" else "home"
    rating = projection[side]; opponent = projection[other]; components = projection[f"{side}_components"]
    supports: list[str] = []
    if _num(rating.get("Power Rating")) > _num(opponent.get("Power Rating")): supports.append("overall power")
    if components["pass"] > 0.35: supports.append("passing matchup")
    if components["rush"] > 0.30: supports.append("rushing matchup")
    if components["success"] > 0.25: supports.append("success-rate matchup")
    if components["explosive"] > 0.15: supports.append("explosiveness")
    if components["line"] + components["havoc"] > 0.20: supports.append("trenches/havoc")
    if components["finishing"] + components["red_zone"] > 0.20: supports.append("finishing drives")
    if market == "total":
        pace_fast = projection["possessions"] > 12.3
        hc, ac = projection["home_components"], projection["away_components"]
        both_offense = hc["overall"] > 0 and ac["overall"] > 0
        both_defense = hc["overall"] < 0 and ac["overall"] < 0
        explosive_signal = hc["explosive"] + ac["explosive"]
        finishing_signal = hc["finishing"] + ac["finishing"] + hc["red_zone"] + ac["red_zone"]
        efficiency_signal = hc["success"] + ac["success"] + hc["pass"] + ac["pass"]
        disruption_signal = hc["havoc"] + ac["havoc"] + hc["sack"] + ac["sack"]
        supports = []
        if direction == "over" and pace_fast: supports.append("fast expected pace")
        if direction == "under" and not pace_fast: supports.append("slow expected pace")
        if direction == "over" and both_offense: supports.append("both offenses supported")
        if direction == "under" and both_defense: supports.append("both defenses supported")
        if direction == "over" and explosive_signal > 0.25: supports.append("explosive-play matchup")
        if direction == "under" and explosive_signal < -0.25: supports.append("explosives suppressed")
        if direction == "over" and finishing_signal > 0.25: supports.append("finishing-drive matchup")
        if direction == "under" and finishing_signal < -0.25: supports.append("finishing drives suppressed")
        if direction == "over" and efficiency_signal > 0.40: supports.append("down-to-down efficiency")
        if direction == "under" and efficiency_signal < -0.40: supports.append("efficiency suppressed")
        if direction == "over" and disruption_signal > 0.35: supports.append("short-field/turnover potential")
        if direction == "under" and disruption_signal < -0.35: supports.append("drive disruption")
        if direction == "over" and projection["total"] >= 58: supports.append("high scoring baseline")
        if direction == "under" and projection["total"] <= 48: supports.append("low scoring baseline")
    return min(6, len(supports)), supports


def reliability_score(game: pd.Series, projection: dict[str, Any], simulation: dict[str, Any], away_personnel: Personnel,
                      home_personnel: Personnel, environment: Environment) -> tuple[float, dict[str, float], list[str]]:
    away = projection["away"]; home = projection["home"]
    data = (_num(away.get("Data Confidence"), 20.0) + _num(home.get("Data Confidence"), 20.0)) / 2
    personnel = (away_personnel.availability_confidence + home_personnel.availability_confidence) / 2
    if away_personnel.qb_confirmed and home_personnel.qb_confirmed: personnel = min(100.0, personnel + 10.0)
    sample = min(100.0, ((_num(away.get("FBS Games")) + _num(home.get("FBS Games"))) / 12.0) * 100.0)
    schedule_quality = 80.0
    if _text(game.get("Away Classification")).lower() != "fbs" or _text(game.get("Home Classification")).lower() != "fbs": schedule_quality = 30.0
    weather = environment.weather_confidence
    deterministic_margin = projection["margin"]; simulated_margin = simulation["home_mean"] - simulation["away_mean"]
    deterministic_total = projection["total"]; simulated_total = simulation["home_mean"] + simulation["away_mean"]
    agreement = 100.0 - min(100.0, abs(deterministic_margin - simulated_margin) * 12.0 + abs(deterministic_total - simulated_total) * 5.0)
    continuity = 100.0 * np.mean([away_personnel.qb_continuity, home_personnel.qb_continuity, away_personnel.coaching_continuity, home_personnel.coaching_continuity, away_personnel.coordinator_continuity, home_personnel.coordinator_continuity])
    parts = {"data": data, "personnel": personnel, "sample": sample, "opponent_quality": schedule_quality, "weather_venue": weather, "model_agreement": agreement, "continuity": continuity}
    score = 0.28 * data + 0.24 * personnel + 0.14 * sample + 0.08 * schedule_quality + 0.10 * weather + 0.10 * agreement + 0.06 * continuity
    reasons = []
    if not away_personnel.qb_confirmed or not home_personnel.qb_confirmed: reasons.append("starting quarterback not fully confirmed")
    if data < 60: reasons.append("advanced data coverage is incomplete")
    if sample < 50: reasons.append("small current-season FBS sample")
    if schedule_quality < 50: reasons.append("FCS/non-FBS opponent increases uncertainty")
    if weather < 60: reasons.append("weather is not firmly confirmed")
    if continuity < 55: reasons.append("major roster/coaching continuity uncertainty")
    return round(clamp(score, 20.0, 98.0), 1), {k: round(v, 1) for k, v in parts.items()}, reasons


def _grade_spread(probability: float, point_edge: float, reliability: float, confluence: int) -> str:
    if probability >= 0.57 and point_edge >= 4.0 and reliability >= 72 and confluence >= 4: return "A Spread"
    if probability >= 0.545 and point_edge >= 2.5 and reliability >= 62 and confluence >= 3: return "B Spread"
    return "No Play"


def _grade_total(probability: float, point_edge: float, reliability: float, confluence: int) -> str:
    if probability >= 0.57 and point_edge >= 5.5 and reliability >= 72 and confluence >= 4: return "A Total"
    if probability >= 0.545 and point_edge >= 3.5 and reliability >= 62 and confluence >= 3: return "B Total"
    return "No Play"


def _grade_ml(probability_edge: float, ev: float, reliability: float, confluence: int) -> str:
    if ev >= 0.08 and probability_edge >= 0.06 and reliability >= 72 and confluence >= 4: return "A Moneyline"
    if ev >= 0.05 and probability_edge >= 0.04 and reliability >= 62 and confluence >= 3: return "B Moneyline"
    return "No Play"


def evaluate_game(game: pd.Series, ratings: pd.DataFrame, away_personnel: Personnel, home_personnel: Personnel,
                  environment: Environment, market_home_spread: float, market_total: float,
                  away_ml: float, home_ml: float, market_availability: dict[str, bool] | None = None,
                  simulations: int = SIMULATIONS) -> dict[str, Any]:
    market_availability = market_availability or {"spread": True, "total": True, "moneyline": True}
    game = game.copy(); game["Home Spread"] = market_home_spread; game["Total"] = market_total; game["Away ML"] = away_ml; game["Home ML"] = home_ml
    projection = project_matchup(game, ratings, away_personnel, home_personnel, environment)
    simulation = simulate_game(projection, _text(game.get("Game ID"), f"{game['Away Team']}-{game['Home Team']}"), simulations=simulations)
    spread = _spread_market(simulation, market_home_spread, game["Home Team"], game["Away Team"])
    total = _total_market(simulation, market_total)
    moneyline = _moneyline_market(simulation, game["Home Team"], game["Away Team"], home_ml, away_ml)
    reliability, reliability_parts, reliability_reasons = reliability_score(game, projection, simulation, away_personnel, home_personnel, environment)
    spread_conf, spread_support = _confluence(projection, spread["team"], game["Home Team"], "spread")
    total_direction = "over" if total["pick"].startswith("Over") else "under"
    total_conf, total_support = _confluence(projection, game["Home Team"], game["Home Team"], "total", total_direction)
    ml_conf, ml_support = _confluence(projection, moneyline["pick"], game["Home Team"], "moneyline")
    spread["grade"] = _grade_spread(spread["probability"], spread["model_edge_points"], reliability, spread_conf)
    total["grade"] = _grade_total(total["probability"], total["model_edge_points"], reliability, total_conf)
    moneyline["grade"] = _grade_ml(moneyline["edge"], moneyline["ev"], reliability, ml_conf)
    if not market_availability.get("spread", False): spread["grade"] = "No Play"
    if not market_availability.get("total", False): total["grade"] = "No Play"
    if not market_availability.get("moneyline", False): moneyline["grade"] = "No Play"
    spread.update({"confluence": spread_conf, "support": spread_support})
    total.update({"confluence": total_conf, "support": total_support})
    moneyline.update({"confluence": ml_conf, "support": ml_support})
    return {"projection": projection, "simulation": simulation, "spread": spread, "total_market": total, "moneyline": moneyline, "reliability": reliability, "reliability_parts": reliability_parts, "reliability_reasons": reliability_reasons, "environment": environment, "away_personnel": away_personnel, "home_personnel": home_personnel, "game": game, "market_availability": market_availability}


def _result_notes(result: dict[str, Any]) -> str:
    notes = list(result["reliability_reasons"])
    notes.extend([result["environment"].notes])
    if result["projection"]["calibration_n"] < 30: notes.append("calibration sample is still developing")
    missing = [name for name, available in result.get("market_availability", {}).items() if not available]
    if missing: notes.append("missing confirmed market data: " + ", ".join(missing))
    return "; ".join(x for x in notes if x)


def slate_row(result: dict[str, Any]) -> pd.DataFrame:
    game = result["game"]; p = result["projection"]; sim = result["simulation"]; spread = result["spread"]; total = result["total_market"]; ml = result["moneyline"]; env = result["environment"]
    away_p = result["away_personnel"]; home_p = result["home_personnel"]
    row = {
        "Date": _text(game.get("Game Date"), date.today().isoformat()), "Season": int(_num(game.get("Season"), DEFAULT_SEASON)), "Week": int(_num(game.get("Week"), 0)),
        "Game ID": _text(game.get("Game ID")), "Game": f"{game['Away Team']} @ {game['Home Team']}", "Away Team": game["Away Team"], "Home Team": game["Home Team"],
        "Neutral Site": _bool(game.get("Neutral Site")), "Conference Game": _bool(game.get("Conference Game")),
        "Projected Away": round(sim["away_mean"], 2), "Projected Home": round(sim["home_mean"], 2), "Projected Margin": round(sim["home_mean"] - sim["away_mean"], 2), "Projected Total": round(sim["home_mean"] + sim["away_mean"], 2),
        "Expected Possessions": round(p["possessions"], 2), "Away Score P10": sim["away_p10"], "Away Score P90": sim["away_p90"], "Home Score P10": sim["home_p10"], "Home Score P90": sim["home_p90"],
        "Opening Home Spread": _num(game.get("Opening Home Spread"), np.nan), "Opening Total": _num(game.get("Opening Total"), np.nan), "Market Home Spread": _num(game.get("Home Spread")), "Market Total": _num(game.get("Total")), "Away ML": _num(game.get("Away ML")), "Home ML": _num(game.get("Home ML")),
        "Spread Pick": spread["pick"], "Spread Probability": round(spread["probability"], 4), "Spread Push Probability": round(spread["push"], 4), "Spread Edge": round(spread["model_edge_points"], 2), "Spread Grade": spread["grade"], "Spread Confluence": spread["confluence"],
        "Total Pick": total["pick"], "Total Probability": round(total["probability"], 4), "Total Push Probability": round(total["push"], 4), "Total Edge": round(total["model_edge_points"], 2), "Total Grade": total["grade"], "Total Confluence": total["confluence"],
        "ML Pick": ml["pick"], "ML Probability": round(ml["probability"], 4), "ML Odds": ml["odds"], "ML Implied Probability": round(ml["implied"], 4), "ML Edge": round(ml["edge"], 4), "ML Expected Value": round(ml["ev"], 4), "ML Grade": ml["grade"], "ML Confluence": ml["confluence"],
        "Reliability": result["reliability"], "Data Confidence": round((result["reliability_parts"]["data"]), 1), "Personnel Confidence": round((away_p.availability_confidence + home_p.availability_confidence) / 2, 1), "Weather Confidence": env.weather_confidence,
        "Previous Season Weight": round((p["away"].get("Previous Season Weight", 1.0) + p["home"].get("Previous Season Weight", 1.0)) / 2, 3), "Current Season Weight": round((p["away"].get("Current Season Weight", 0.0) + p["home"].get("Current Season Weight", 0.0)) / 2, 3),
        "Home Field Advantage": env.home_field, "League HFA": env.league_hfa, "Venue HFA": env.venue_hfa, "Travel Adjustment": env.travel_adjustment, "Time Zone Adjustment": env.timezone_adjustment, "Altitude Adjustment": env.altitude_adjustment, "Rest Adjustment": env.rest_adjustment, "Weather Adjustment": env.weather_total_adjustment,
        "Temperature": env.temperature, "Wind": env.wind, "Precipitation Probability": env.precipitation_probability, "Roof": env.roof, "Stadium": env.stadium,
        "Away QB Adjustment": away_p.qb_adjustment, "Home QB Adjustment": home_p.qb_adjustment, "Away Injury Adjustment": away_p.injury_adjustment, "Home Injury Adjustment": home_p.injury_adjustment,
        "Model Version": MODEL_VERSION, "Notes": _result_notes(result),
    }
    return pd.DataFrame([row]).reindex(columns=SLATE_COLUMNS)


def tracker_rows(result: dict[str, Any], include_no_plays: bool = False) -> pd.DataFrame:
    slate = slate_row(result).iloc[0]; rows = []
    markets = [
        ("Spread", slate["Spread Pick"], _parse_selection_line(str(slate["Spread Pick"]), slate["Market Home Spread"]), slate["Spread Probability"], slate["Spread Push Probability"], 0.5, slate["Spread Probability"] - 0.5, _american_ev(slate["Spread Probability"], -110), slate["Spread Grade"], slate["Spread Confluence"]),
        ("Total", slate["Total Pick"], slate["Market Total"], slate["Total Probability"], slate["Total Push Probability"], 0.5, slate["Total Probability"] - 0.5, _american_ev(slate["Total Probability"], -110), slate["Total Grade"], slate["Total Confluence"]),
        ("Moneyline", slate["ML Pick"], slate["ML Odds"], slate["ML Probability"], 0.0, slate["ML Implied Probability"], slate["ML Edge"], slate["ML Expected Value"], slate["ML Grade"], slate["ML Confluence"]),
    ]
    for bet_type, selection, line, probability, push, implied, edge, ev, grade, confluence in markets:
        if not include_no_plays and grade == "No Play": continue
        rows.append({
            "Date": slate["Date"], "Season": slate["Season"], "Week": slate["Week"], "Game ID": slate["Game ID"], "Game": slate["Game"], "Bet Type": bet_type, "Selection": selection, "Odds/Line": line,
            "Model Probability": probability, "Push Probability": push, "Implied Probability": implied, "Edge": edge, "Expected Value": ev, "Grade": grade, "Confluence": confluence,
            "Result": "Pending", "Units": 0.0, "Closing Line": "", "Closing Line Value": "", "Reliability": slate["Reliability"], "Data Confidence": slate["Data Confidence"], "Personnel Confidence": slate["Personnel Confidence"],
            "Projected Away": slate["Projected Away"], "Projected Home": slate["Projected Home"], "Actual Away": "", "Actual Home": "", "Margin Residual": "", "Total Residual": "", "Model Version": MODEL_VERSION, "Notes": slate["Notes"],
        })
    return pd.DataFrame(rows, columns=TRACKER_COLUMNS)


def save_result(result: dict[str, Any], include_no_plays: bool = False) -> tuple[int, int]:
    slate = slate_row(result); tracker = tracker_rows(result, include_no_plays)
    _upsert(SLATE_TAB, slate, SLATE_COLUMNS, ["Date", "Game ID"])
    if not tracker.empty: _upsert(TRACKER_TAB, tracker, TRACKER_COLUMNS, ["Date", "Game ID", "Bet Type"])
    save_personnel(result["away_personnel"], result["game"]["Away Team"], int(result["game"]["Season"]), int(result["game"]["Week"]), result["game"]["Game ID"])
    save_personnel(result["home_personnel"], result["game"]["Home Team"], int(result["game"]["Season"]), int(result["game"]["Week"]), result["game"]["Game ID"])
    return len(slate), len(tracker)

# ---------------------------------------------------------------------------
# Automated personnel candidates, settlement, and weekly batch processing
# ---------------------------------------------------------------------------

@st.cache_data(ttl=21600, show_spinner=False)

@st.cache_data(ttl=21600, show_spinner=False)
def _auto_qb_candidate(team: str, season: int) -> tuple[str, float, str]:
    team = _normalize_team(team)
    current = _open_roster_frame(season)
    previous = _open_roster_frame(season - 1)
    qbs = current[(current["team"] == team) & current["position"].astype(str).str.startswith("QB")].copy() if not current.empty else pd.DataFrame()
    if not qbs.empty:
        qbs["class_num"] = [_class_number(v) for v in _series(qbs, "class", _series(qbs, "experience_years", 2.0))]
        prior_names = set(previous[(previous["team"] == team) & previous["position"].astype(str).str.startswith("QB")]["name"].astype(str).str.lower()) if not previous.empty else set()
        qbs["returning"] = qbs["name"].astype(str).str.lower().isin(prior_names).astype(int)
        qbs = qbs.sort_values(["returning", "class_num"], ascending=False)
        name = _text(qbs.iloc[0].get("name"), "Unconfirmed")
        confidence = 64.0 if int(qbs.iloc[0].get("returning", 0)) else 50.0
        return name, confidence, "Free ESPN/SportsDataverse roster candidate; starter remains unconfirmed"

    # Live ESPN roster is the fallback when the bulk open roster release is unavailable.
    index = _espn_team_index()
    meta = index.get(team, {})
    team_id = _text(meta.get("id"))
    if team_id:
        payload = _public_json_get(f"{ESPN_SITE_BASE}/teams/{team_id}/roster", {"season": season}, optional=True, max_age=21600)
        candidates: list[tuple[float, str]] = []
        stack = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                position = item.get("position") if isinstance(item.get("position"), dict) else {}
                pos = _text(_first(position, ["abbreviation", "name"], _first(item, ["position"], ""))).upper()
                name = _text(_first(item, ["fullName", "displayName", "name"], ""))
                if pos == "QB" and name:
                    experience = item.get("experience") if isinstance(item.get("experience"), dict) else {}
                    candidates.append((_class_number(_first(experience, ["displayValue", "years"], 2.0)), name))
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1], 45.0, "Free live ESPN roster candidate; starter remains unconfirmed"
    return "Unconfirmed", 30.0, "No free roster candidate was available"



def default_personnel(team: str, rating: dict[str, Any], season: int, week: int, game_id: str, live_candidate: bool = True) -> Personnel:
    saved = _personnel_from_sheet(season, week, game_id, team)
    if saved.expected_qb != "Unconfirmed" or saved.source != "Manual confirmation required": return saved
    if not live_candidate:
        return Personnel(
            expected_qb="Unconfirmed", qb_confirmed=False,
            qb_continuity=_pct(rating.get("QB Continuity"), 0.50),
            coaching_continuity=_pct(rating.get("Coaching Continuity"), 0.75),
            coordinator_continuity=_pct(rating.get("Coordinator Continuity"), 0.67),
            availability_confidence=32.0,
            source="Automatic slate fast path; live starter lookup deferred to selected matchup",
        )
    qb, confidence, source = _auto_qb_candidate(team, season)
    return Personnel(
        expected_qb=qb, qb_confirmed=False, qb_continuity=_pct(rating.get("QB Continuity"), 0.50),
        coaching_continuity=_pct(rating.get("Coaching Continuity"), 0.75), coordinator_continuity=_pct(rating.get("Coordinator Continuity"), 0.67),
        availability_confidence=confidence, source=source,
    )


def sync_schedule(season: int, preferred_provider: str = "") -> pd.DataFrame:
    schedule = _schedule_for_season(season, preferred_provider)
    if not schedule.empty: _upsert(SCHEDULE_TAB, schedule, SCHEDULE_COLUMNS, ["Season", "Game ID"])
    return schedule


def _parse_selection_line(selection: str, fallback: float) -> float:
    try: return float(selection.split()[-1])
    except Exception: return float(fallback)


def settle_results(season: int) -> tuple[int, int]:
    schedule = sync_schedule(season)
    completed = schedule[schedule["Completed"].map(_bool)] if not schedule.empty else pd.DataFrame()
    tracker = _sheet(TRACKER_TAB, TRACKER_COLUMNS)
    slate = _sheet(SLATE_TAB, SLATE_COLUMNS)
    if tracker.empty or completed.empty: return 0, 0
    settled = 0; calibration_rows = []
    result_map = {str(row["Game ID"]): row for _, row in completed.iterrows()}
    for idx, bet in tracker.iterrows():
        if _text(bet.get("Result")).lower() not in {"", "pending"}: continue
        game = result_map.get(_text(bet.get("Game ID")))
        if game is None: continue
        away_score = _num(game["Away Score"]); home_score = _num(game["Home Score"]); margin = home_score - away_score; total = home_score + away_score
        bet_type = _text(bet["Bet Type"]); selection = _text(bet["Selection"]); outcome = "Loss"
        if bet_type == "Moneyline":
            selected_home = selection == _text(game["Home Team"]); selected_away = selection == _text(game["Away Team"])
            won = (selected_home and home_score > away_score) or (selected_away and away_score > home_score)
            outcome = "Win" if won else "Loss"
        elif bet_type == "Spread":
            line = _parse_selection_line(selection, _num(bet["Odds/Line"]))
            selected_home = selection.startswith(_text(game["Home Team"]))
            selected_margin = margin if selected_home else -margin
            value = selected_margin + line
            outcome = "Win" if value > 0 else "Push" if abs(value) < 1e-9 else "Loss"
        elif bet_type == "Total":
            line = _parse_selection_line(selection, _num(bet["Odds/Line"])); value = total - line
            if selection.lower().startswith("under"): value *= -1
            outcome = "Win" if value > 0 else "Push" if abs(value) < 1e-9 else "Loss"
        closing_line: float | str = ""; closing_value: float | str = ""
        if bet_type == "Spread":
            selected_home = selection.startswith(_text(game["Home Team"]))
            closing_line = _num(game.get("Home Spread"), np.nan) if selected_home else -_num(game.get("Home Spread"), np.nan)
            bet_line = _parse_selection_line(selection, _num(bet["Odds/Line"]))
            if math.isfinite(_num(closing_line, np.nan)): closing_value = round(bet_line - float(closing_line), 3)
        elif bet_type == "Total":
            closing_line = _num(game.get("Total"), np.nan); bet_line = _parse_selection_line(selection, _num(bet["Odds/Line"]))
            if math.isfinite(_num(closing_line, np.nan)):
                closing_value = round((float(closing_line) - bet_line) if selection.lower().startswith("over") else (bet_line - float(closing_line)), 3)
        elif bet_type == "Moneyline":
            selected_home = selection == _text(game["Home Team"]); closing_line = _num(game.get("Home ML" if selected_home else "Away ML"), 0.0)
            close_home, close_away = _no_vig(_num(game.get("Home ML")), _num(game.get("Away ML")))
            closing_implied = close_home if selected_home else close_away
            closing_value = round(closing_implied - _num(bet.get("Implied Probability"), 0.5), 4) if closing_line else ""
        tracker.at[idx, "Result"] = outcome; tracker.at[idx, "Actual Away"] = away_score; tracker.at[idx, "Actual Home"] = home_score
        tracker.at[idx, "Closing Line"] = closing_line; tracker.at[idx, "Closing Line Value"] = closing_value
        tracker.at[idx, "Margin Residual"] = margin - (_num(bet["Projected Home"]) - _num(bet["Projected Away"])); tracker.at[idx, "Total Residual"] = total - (_num(bet["Projected Home"]) + _num(bet["Projected Away"])); settled += 1
    _write(TRACKER_TAB, tracker, TRACKER_COLUMNS)

    existing_cal = _sheet(CALIBRATION_TAB, CALIBRATION_COLUMNS)
    processed = set(existing_cal["Game ID"].astype(str)) if not existing_cal.empty else set()
    for _, row in slate[slate["Season"].astype(str) == str(season)].iterrows():
        game = result_map.get(_text(row["Game ID"]))
        if game is None or _text(row["Game ID"]) in processed: continue
        away_score = _num(game["Away Score"]); home_score = _num(game["Home Score"]); actual_margin = home_score - away_score; actual_total = home_score + away_score
        projected_margin = _num(row["Projected Margin"]); projected_total = _num(row["Projected Total"])
        home_probability = 1.0 - _num(row["ML Probability"], 0.5) if row["ML Pick"] == row["Away Team"] else _num(row["ML Probability"], 0.5)
        calibration_rows.append({
            "Date": row["Date"], "Season": season, "Week": row["Week"], "Game ID": row["Game ID"], "Game": row["Game"],
            "Projected Away": row["Projected Away"], "Projected Home": row["Projected Home"], "Projected Margin": projected_margin, "Projected Total": projected_total,
            "Actual Away": away_score, "Actual Home": home_score, "Actual Margin": actual_margin, "Actual Total": actual_total,
            "Margin Residual": actual_margin - projected_margin, "Total Residual": actual_total - projected_total,
            "Projected Win Probability": home_probability, "Home Won": int(home_score > away_score), "Market Home Spread": row["Market Home Spread"], "Market Total": row["Market Total"],
            "Spread Closing Value": round(_num(row["Market Home Spread"]) - _num(game.get("Home Spread")), 3) if math.isfinite(_num(game.get("Home Spread"), np.nan)) else "",
            "Total Closing Value": round(_num(game.get("Total")) - _num(row["Market Total"]), 3) if math.isfinite(_num(game.get("Total"), np.nan)) else "",
            "Reliability": row["Reliability"], "Model Version": row["Model Version"],
        })
    if calibration_rows: _upsert(CALIBRATION_TAB, pd.DataFrame(calibration_rows), CALIBRATION_COLUMNS, ["Season", "Game ID"])
    return settled, len(calibration_rows)


def run_week(
    season: int,
    week: int,
    preferred_provider: str = "",
    *,
    schedule: pd.DataFrame | None = None,
    ratings: pd.DataFrame | None = None,
    save_graded_plays: bool = False,
    save_personnel_snapshots: bool = False,
    simulations: int = BATCH_SIMULATIONS,
) -> tuple[pd.DataFrame, list[str]]:
    """Run every scheduled game in a week from the same automatic data context.

    Weekly automatic refreshes always update the projection slate. Graded tracker
    rows are only written when explicitly requested so an automatic refresh cannot
    replace a user's saved selection, result, or unit amount.
    """
    schedule = schedule.copy() if isinstance(schedule, pd.DataFrame) else sync_schedule(season, preferred_provider)
    games = schedule[
        (pd.to_numeric(schedule["Week"], errors="coerce") == int(week))
        & ~schedule["Completed"].map(_bool)
    ].copy() if not schedule.empty else pd.DataFrame()
    ratings = ratings.copy() if isinstance(ratings, pd.DataFrame) else build_team_ratings(season, week)

    slate_frames: list[pd.DataFrame] = []
    tracker_frames: list[pd.DataFrame] = []
    personnel_frames: list[pd.DataFrame] = []
    errors: list[str] = []

    for _, game in games.iterrows():
        try:
            away_rating = _rating_row(ratings, game["Away Team"])
            home_rating = _rating_row(ratings, game["Home Team"])
            away_p = default_personnel(game["Away Team"], away_rating, season, week, game["Game ID"], live_candidate=False)
            home_p = default_personnel(game["Home Team"], home_rating, season, week, game["Game ID"], live_candidate=False)
            env = build_environment(game, season)
            spread_available = math.isfinite(_num(game.get("Home Spread"), np.nan))
            total_available = math.isfinite(_num(game.get("Total"), np.nan))
            moneyline_available = bool(_num(game.get("Away ML"), 0.0) and _num(game.get("Home ML"), 0.0))
            spread = _num(game["Home Spread"], 0.0)
            total = _num(game["Total"], 56.0)
            result = evaluate_game(
                game, ratings, away_p, home_p, env, spread, total,
                _num(game["Away ML"]), _num(game["Home ML"]),
                {"spread": spread_available, "total": total_available, "moneyline": moneyline_available},
                simulations=simulations,
            )
            slate_frames.append(slate_row(result))
            if save_graded_plays:
                tracker = tracker_rows(result, False)
                if not tracker.empty:
                    tracker_frames.append(tracker)
            if save_personnel_snapshots:
                personnel_frames.extend([
                    personnel_row(away_p, game["Away Team"], season, week, game["Game ID"]),
                    personnel_row(home_p, game["Home Team"], season, week, game["Game ID"]),
                ])
        except Exception as exc:
            errors.append(f"{game.get('Away Team')} @ {game.get('Home Team')}: {exc}")

    slate = pd.concat(slate_frames, ignore_index=True) if slate_frames else pd.DataFrame(columns=SLATE_COLUMNS)
    if not slate.empty:
        _upsert(SLATE_TAB, slate, SLATE_COLUMNS, ["Date", "Game ID"])
    if tracker_frames:
        tracker = pd.concat(tracker_frames, ignore_index=True)
        _upsert(TRACKER_TAB, tracker, TRACKER_COLUMNS, ["Date", "Game ID", "Bet Type"])
    if personnel_frames:
        personnel = pd.concat(personnel_frames, ignore_index=True)
        _upsert(PERSONNEL_TAB, personnel, PERSONNEL_COLUMNS, ["Season", "Game ID", "Team"])
    return slate, errors


# ---------------------------------------------------------------------------
# Streamlit presentation
# ---------------------------------------------------------------------------

def _percent(value: float) -> str: return f"{100 * value:.1f}%"


def _grade_class(grade: str) -> str:
    if grade.startswith("A"): return "grade-a"
    if grade.startswith("B"): return "grade-b"
    return "grade-pass"


def _inject_styles() -> None:
    st.markdown("""
    <style>
    .cfb-score {background:linear-gradient(135deg,#071528,#102a52);border:1px solid #214d87;border-radius:18px;padding:18px;text-align:center;margin:8px 0 16px;box-shadow:0 12px 35px rgba(0,0,0,.22)}
    .cfb-score .teams{font-size:1.05rem;color:#a9c7ec}.cfb-score .score{font-size:2.25rem;font-weight:850;color:#fff;letter-spacing:.02em}.cfb-score .sub{color:#84a8d5;font-size:.9rem}
    .market-card{border:1px solid #254c7d;border-radius:16px;padding:14px;background:#0b1c33;min-height:190px}.market-card h4{margin:0 0 7px}.market-card .pick{font-size:1.2rem;font-weight:800}.market-card .metric{color:#bdd3ef;margin-top:5px}.market-card .why{color:#83a8d4;font-size:.82rem;margin-top:8px}
    .grade-a{color:#65ef9c}.grade-b{color:#63b8ff}.grade-pass{color:#a4afbf}.small-note{font-size:.83rem;color:#8fa9ca}
    </style>
    """, unsafe_allow_html=True)


def _market_card(title: str, market: dict[str, Any], edge_label: str) -> None:
    grade = market["grade"]; support = ", ".join(market.get("support", [])) or "No strong multi-factor agreement"
    ev = f"<div class='metric'>EV: {market['ev']*100:.1f}%</div>" if "ev" in market else ""
    st.markdown(f"""<div class='market-card'><h4>{title}</h4><div class='pick'>{market['pick']}</div><div class='{_grade_class(grade)}'><b>{grade}</b></div><div class='metric'>Probability: {_percent(market['probability'])}</div><div class='metric'>{edge_label}</div>{ev}<div class='metric'>Confluence: {market['confluence']}/6</div><div class='why'>{support}</div></div>""", unsafe_allow_html=True)


def _display_result(result: dict[str, Any]) -> None:
    game = result["game"]; sim = result["simulation"]; p = result["projection"]
    st.markdown(f"""<div class='cfb-score'><div class='teams'>{game['Away Team']} at {game['Home Team']}</div><div class='score'>{sim['away_mean']:.1f} — {sim['home_mean']:.1f}</div><div class='sub'>Model spread: {game['Home Team']} {-(sim['home_mean']-sim['away_mean']):+.1f} &nbsp; • &nbsp; Total {sim['away_mean']+sim['home_mean']:.1f} &nbsp; • &nbsp; {p['possessions']:.1f} possessions/team</div></div>""", unsafe_allow_html=True)
    st.progress(result["reliability"] / 100, text=f"Reliability {result['reliability']:.0f}/100")
    cols = st.columns(3)
    with cols[0]: _market_card("Spread", result["spread"], f"Model advantage: {result['spread']['model_edge_points']:.1f} points")
    with cols[1]: _market_card("Total", result["total_market"], f"Model advantage: {result['total_market']['model_edge_points']:.1f} points")
    with cols[2]: _market_card("Moneyline", result["moneyline"], f"Probability edge: {result['moneyline']['edge']*100:.1f}%")
    with st.expander("Projection diagnostics", expanded=False):
        a, b = st.columns(2)
        with a:
            st.markdown("**Away matchup adjustments**"); st.dataframe(pd.DataFrame(result["projection"]["away_components"].items(), columns=["Factor", "Points"]), hide_index=True, use_container_width=True)
        with b:
            st.markdown("**Home matchup adjustments**"); st.dataframe(pd.DataFrame(result["projection"]["home_components"].items(), columns=["Factor", "Points"]), hide_index=True, use_container_width=True)
        st.markdown("**Reliability components**"); st.dataframe(pd.DataFrame(result["reliability_parts"].items(), columns=["Component", "Score"]), hide_index=True, use_container_width=True)
        st.caption(result["environment"].notes)


def _current_cfb_season(today: date | None = None) -> int:
    today = today or date.today()
    return today.year - 1 if today.month <= 2 else today.year


def _schedule_date_series(schedule: pd.DataFrame) -> pd.Series:
    if schedule is None or schedule.empty:
        return pd.Series(dtype="object")
    return pd.to_datetime(
        schedule.get("Game Date", pd.Series(index=schedule.index, dtype=str)),
        errors="coerce",
    ).dt.date


def _available_slate_dates(schedule: pd.DataFrame) -> list[date]:
    dates = _schedule_date_series(schedule).dropna()
    return sorted(set(dates.tolist()))


def _default_slate_date(schedule: pd.DataFrame, today: date | None = None) -> date | None:
    dates = _available_slate_dates(schedule)
    if not dates:
        return None
    today = today or date.today()
    if today in dates:
        return today
    future = [game_date for game_date in dates if game_date >= today]
    return future[0] if future else dates[-1]


def _default_projection_week(schedule: pd.DataFrame, today: date | None = None) -> int:
    slate_date = _default_slate_date(schedule, today)
    if slate_date is None:
        weeks = pd.to_numeric(schedule.get("Week"), errors="coerce").dropna()
        return int(weeks.max()) if len(weeks) else 1
    mask = _schedule_date_series(schedule) == slate_date
    weeks = pd.to_numeric(schedule.loc[mask, "Week"], errors="coerce").dropna()
    return int(weeks.iloc[0]) if len(weeks) else 1


def _get_cached_ratings(season: int, week: int) -> pd.DataFrame:
    frame = _sheet(RATINGS_TAB, RATING_COLUMNS)
    subset = frame[
        (frame["Season"].astype(str) == str(season))
        & (frame["Projection Week"].astype(str) == str(week))
    ]
    return subset.copy()


def _ratings_are_fresh(frame: pd.DataFrame, max_age_seconds: int = AUTO_RATINGS_MAX_AGE_SECONDS) -> bool:
    if frame is None or frame.empty or "Updated" not in frame.columns:
        return False
    try:
        season = int(pd.to_numeric(frame.get("Season"), errors="coerce").dropna().iloc[0])
        advanced_saved = frame.get("Advanced Data Available", pd.Series(False, index=frame.index)).map(_bool).any()
        roster_saved = frame.get("Roster Data Available", pd.Series(False, index=frame.index)).map(_bool).any()
        if _advanced_cache_ready(season) and not (advanced_saved or roster_saved):
            return False
    except Exception:
        pass
    updated = pd.to_datetime(frame["Updated"], errors="coerce", utc=True).dropna()
    if updated.empty:
        return False
    age = datetime.now(timezone.utc) - updated.max().to_pydatetime()
    return age.total_seconds() <= max_age_seconds



def _ensure_automatic_schedule(season: int, provider: str = "", force: bool = False) -> pd.DataFrame:
    provider_key = hashlib.sha1(provider.strip().lower().encode()).hexdigest()[:8]
    session_key = f"cfb_auto_schedule_{season}_{provider_key}"
    cached = st.session_state.get(session_key)
    if not force and isinstance(cached, pd.DataFrame) and not cached.empty:
        return cached.copy()
    stored = _sheet(SCHEDULE_TAB, SCHEDULE_COLUMNS)
    stored = stored[stored["Season"].astype(str) == str(season)].copy()
    # A prior automatic sync is the fastest reliable cold-start source. Manual
    # market boxes remain editable, and Force Refresh still obtains a full feed.
    if not force and not stored.empty:
        st.session_state[session_key] = stored.copy()
        return stored
    try:
        schedule = sync_schedule(season, provider)
        if not schedule.empty:
            st.session_state[session_key] = schedule.copy()
            return schedule
    except Exception as exc:
        st.session_state["cfb_auto_schedule_warning"] = str(exc)
    if not stored.empty:
        st.session_state[session_key] = stored.copy()
    return stored




def _ensure_automatic_ratings(season: int, week: int, force: bool = False) -> pd.DataFrame:
    session_key = f"cfb_auto_ratings_{season}_{week}"
    cached = st.session_state.get(session_key)
    if not force and isinstance(cached, pd.DataFrame) and not cached.empty and _ratings_are_fresh(cached):
        return cached.copy()
    saved = _get_cached_ratings(season, week)
    if not force and _ratings_are_fresh(saved):
        st.session_state[session_key] = saved.copy()
        return saved
    try:
        ratings = build_team_ratings(season, week)
        if not ratings.empty:
            st.session_state[session_key] = ratings.copy()
            return ratings
    except Exception as exc:
        st.session_state["cfb_auto_ratings_warning"] = str(exc)
    if not saved.empty:
        st.session_state[session_key] = saved.copy()
    return saved



def _ensure_automatic_week_slate(
    season: int,
    week: int,
    provider: str,
    schedule: pd.DataFrame,
    ratings: pd.DataFrame,
    *,
    force: bool = False,
) -> tuple[pd.DataFrame, list[str]]:
    """Populate the entire weekly projection slate once per Streamlit session."""
    session_key = f"cfb_auto_week_slate_{MODEL_VERSION}_{season}_{week}"
    cached = st.session_state.get(session_key)
    if not force and isinstance(cached, pd.DataFrame):
        return cached.copy(), list(st.session_state.get(f"{session_key}_errors", []))
    if schedule.empty or ratings.empty:
        return pd.DataFrame(columns=SLATE_COLUMNS), []

    slate, errors = run_week(
        season,
        week,
        provider,
        schedule=schedule,
        ratings=ratings,
        save_graded_plays=False,
    )
    st.session_state[session_key] = slate.copy()
    st.session_state[f"{session_key}_errors"] = list(errors)
    return slate, errors



def _auto_save_selected_projection(result: dict[str, Any]) -> None:
    """Persist the visible projection once without touching the bet tracker."""
    try:
        row = slate_row(result)
        fingerprint_fields = [
            result["game"].get("Game ID"), result["game"].get("Home Spread"),
            result["game"].get("Total"), result["game"].get("Away ML"),
            result["game"].get("Home ML"), result["reliability"], MODEL_VERSION,
        ]
        fingerprint = hashlib.sha1(repr(fingerprint_fields).encode()).hexdigest()[:16]
        key = f"cfb_auto_saved_selected::{fingerprint}"
        if st.session_state.get(key):
            return
        _upsert(SLATE_TAB, row, SLATE_COLUMNS, ["Date", "Game ID"])
        st.session_state[key] = True
    except Exception as exc:
        st.session_state["cfb_auto_selected_save_warning"] = str(exc)


def _ensure_automatic_day_slate_incremental(
    season: int,
    slate_date: date,
    provider: str,
    schedule: pd.DataFrame,
    ratings: pd.DataFrame,
) -> tuple[int, int, list[str]]:
    """Build a date in short automatic batches so no request hits Render timeout."""
    day_games = schedule[
        (_schedule_date_series(schedule) == slate_date)
        & ~schedule["Completed"].map(_bool)
    ].copy()
    if day_games.empty or ratings.empty:
        return 0, 0, []
    existing = _sheet(SLATE_TAB, SLATE_COLUMNS)
    completed_ids = set(
        existing[
            (existing["Season"].astype(str) == str(season))
            & (existing["Date"].astype(str) == slate_date.isoformat())
            & (existing["Model Version"].astype(str) == MODEL_VERSION)
        ]["Game ID"].astype(str)
    ) if not existing.empty else set()
    pending = day_games[~day_games["Game ID"].astype(str).isin(completed_ids)].copy()
    total = len(day_games)
    done = total - len(pending)
    if pending.empty:
        return done, total, []
    batch = pending.head(max(1, AUTO_SLATE_BATCH_SIZE)).copy()
    week_values = pd.to_numeric(batch["Week"], errors="coerce").dropna()
    week = int(week_values.iloc[0]) if len(week_values) else _default_projection_week(schedule)
    _, errors = run_week(
        season, week, provider,
        schedule=batch,
        ratings=ratings,
        save_graded_plays=False,
        save_personnel_snapshots=False,
        simulations=BATCH_SIMULATIONS,
    )
    return min(total, done + len(batch)), total, errors


def _settle_automatically_once(season: int) -> None:
    session_key = f"cfb_auto_settlement_{season}_{date.today().isoformat()}"
    if st.session_state.get(session_key):
        return
    st.session_state[session_key] = True
    if not sheets_ready():
        return
    try:
        settled, calibrated = settle_results(season)
        st.session_state["cfb_auto_settlement_summary"] = (settled, calibrated)
    except Exception as exc:
        st.session_state["cfb_auto_settlement_warning"] = str(exc)



def _clear_automatic_state() -> None:
    for key in list(st.session_state):
        if str(key).startswith(("cfb_auto_", "cfb_last_")):
            del st.session_state[key]
    for cached_function in [_auto_qb_candidate, _espn_events, _espn_games_payload, _espn_teams_payload, _espn_venues_payload, _espn_lines_payload, _result_strength, _historical_hfa_context, _historical_hfa, _team_home_coordinates]:
        try:
            cached_function.clear()
        except Exception:
            pass
    for path in CACHE_DIR.glob("*.json"):
        try:
            path.unlink()
        except Exception:
            pass
    # Keep downloaded parquet files; deleting them would recreate the original
    # slow-start problem after every refresh.

def _render_build() -> None:
    st.subheader("NCAAF Automatic Slate + Game Builder")
    st.caption("Opening this page loads the slate and selected matchup first. Large open-data files warm in the background, and the Slate page fills the date automatically in short timeout-safe batches.")

    auto_season = _current_cfb_season()
    with st.expander("Slate controls and manual refresh", expanded=False):
        season = int(st.number_input(
            "Season", 2020, 2035,
            int(st.session_state.get("cfb_build_season_value", auto_season)),
            1, key="cfb_season_auto",
        ))
        st.session_state["cfb_build_season_value"] = season
        provider = st.text_input(
            "Preferred sportsbook provider",
            value=str(st.session_state.get("cfb_provider_value", "")),
            placeholder="Optional, e.g. DraftKings",
            key="cfb_provider_auto",
        )
        st.session_state["cfb_provider_value"] = provider
        force_refresh = st.button("Force refresh all automatic NCAAF data", use_container_width=True, key="cfb_force_refresh")
        if force_refresh:
            _clear_automatic_state()
            st.rerun()

    schedule = _ensure_automatic_schedule(season, provider)
    if schedule.empty:
        warning = st.session_state.get("cfb_auto_schedule_warning", "")
        st.error(f"The free public college-football schedule could not be loaded. {warning}".strip())
        st.caption("No API key is required. Force refresh from Automation after a temporary ESPN or network outage.")
        return

    eligible = schedule.copy()
    eligible = eligible[eligible["Away Team"].astype(str).ne("") & eligible["Home Team"].astype(str).ne("")]
    slate_dates = _available_slate_dates(eligible)
    default_date = _default_slate_date(eligible)
    if not slate_dates or default_date is None:
        st.warning("The schedule loaded, but it did not contain usable game dates.")
        return
    default_index = slate_dates.index(default_date) if default_date in slate_dates else 0
    slate_date = st.selectbox(
        "Slate date",
        slate_dates,
        index=default_index,
        format_func=lambda value: value.strftime("%A, %B %d, %Y").replace(" 0", " "),
        key=f"cfb_slate_date_{season}",
    )
    day_schedule = eligible[_schedule_date_series(eligible) == slate_date].copy()
    day_schedule = day_schedule.sort_values(["Game Time", "Away Team", "Home Team"])
    if day_schedule.empty:
        st.warning("No games were found on the selected date.")
        return
    week = int(pd.to_numeric(day_schedule["Week"], errors="coerce").dropna().iloc[0])

    ratings = _ensure_automatic_ratings(season, week)
    if ratings.empty:
        warning = st.session_state.get("cfb_auto_ratings_warning", "")
        st.error(f"Automatic team ratings could not be built. {warning}".strip())
        return

    # The selected matchup renders first. The full date is filled automatically
    # in short batches on the Slate page instead of blocking this page.

    labels = [
        f"{row['Away Team']} @ {row['Home Team']} — {_text(row.get('Game Time')) or 'time TBD'}"
        for _, row in day_schedule.iterrows()
    ]
    selected_label = st.selectbox("Game", labels, index=0, key=f"cfb_game_{season}_{slate_date}")
    game = day_schedule.iloc[labels.index(selected_label)].copy()
    st.markdown(f"**{len(day_schedule)} game{'s' if len(day_schedule) != 1 else ''} on this slate** • Week {week}")

    c1, c2, c3, c4 = st.columns(4)
    spread_default = _num(game.get("Home Spread"), 0.0)
    total_default = _num(game.get("Total"), 56.0)
    if not math.isfinite(spread_default): spread_default = 0.0
    if not math.isfinite(total_default): total_default = 56.0
    market_key = _text(game.get("Game ID"), f"{season}_{week}_{game['Away Team']}_{game['Home Team']}")
    with c1: market_spread = st.number_input("Home spread", -60.0, 60.0, float(spread_default), 0.5, key=f"spread_{market_key}")
    with c2: market_total = st.number_input("Total", 20.0, 120.0, float(total_default), 0.5, key=f"total_{market_key}")
    with c3: away_ml = st.number_input("Away moneyline", -5000.0, 5000.0, float(_num(game.get("Away ML"), 0.0)), 5.0, key=f"aml_{market_key}")
    with c4: home_ml = st.number_input("Home moneyline", -5000.0, 5000.0, float(_num(game.get("Home ML"), 0.0)), 5.0, key=f"hml_{market_key}")

    roof_options = ["Outdoor/Unknown", "Retractable roof open", "Retractable roof closed", "Indoor/Dome"]
    auto_roof = _text(game.get("Roof"), "Outdoor/Unknown")
    roof_index = roof_options.index(auto_roof) if auto_roof in roof_options else 0
    roof = st.selectbox("Stadium environment", roof_options, index=roof_index, key=f"roof_{market_key}")

    away_rating = _rating_row(ratings, game["Away Team"])
    home_rating = _rating_row(ratings, game["Home Team"])
    away_base = default_personnel(game["Away Team"], away_rating, season, week, game["Game ID"])
    home_base = default_personnel(game["Home Team"], home_rating, season, week, game["Game ID"])
    with st.expander("Quarterbacks, injuries, and continuity", expanded=False):
        left, right = st.columns(2)
        with left:
            away_personnel = _personnel_editor(game["Away Team"], away_base, season, week, game["Game ID"], f"away_{market_key}")
        with right:
            home_personnel = _personnel_editor(game["Home Team"], home_base, season, week, game["Game ID"], f"home_{market_key}")
        st.caption("The model automatically proposes quarterback candidates and continuity. Manual injury or confirmed-starter overrides remain available, and uncertainty lowers reliability instead of being guessed.")

    try:
        environment = build_environment(game, season, roof)
    except Exception as exc:
        environment = Environment(roof=roof, stadium=_text(game.get("Stadium")), notes=f"Environment fallback: {exc}")
    with st.expander("Home field, travel, rest, and weather", expanded=False):
        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Home-field advantage", f"{environment.home_field:+.2f}")
        h2.metric("League component", f"{environment.league_hfa:+.2f}")
        h3.metric("Venue component", f"{environment.venue_hfa:+.2f}")
        h4.metric("Weather total", f"{environment.weather_total_adjustment:+.2f}")
        st.caption(environment.notes)
        manual_hfa = st.checkbox("Override calculated home-field advantage", key=f"hfa_override_{market_key}")
        if manual_hfa:
            environment.home_field = st.number_input("Manual home-field points", -5.0, 10.0, float(environment.home_field), 0.25, key=f"hfa_value_{market_key}")

    spread_available = math.isfinite(_num(game.get("Home Spread"), np.nan)) or market_spread != 0.0
    total_available = math.isfinite(_num(game.get("Total"), np.nan)) or market_total != 56.0
    moneyline_available = bool(away_ml and home_ml)
    with st.spinner("Updating the selected matchup projection..."):
        result = evaluate_game(
            game, ratings, away_personnel, home_personnel, environment,
            market_spread, market_total, away_ml, home_ml,
            {"spread": spread_available, "total": total_available, "moneyline": moneyline_available},
        )
    st.session_state["cfb_last_result"] = result
    st.session_state["cfb_last_game_id"] = game["Game ID"]
    _display_result(result)
    _auto_save_selected_projection(result)

    a, b = st.columns(2)
    with a:
        if st.button("Save projection and graded plays", type="primary", use_container_width=True, key=f"cfb_save_{market_key}"):
            _, plays = save_result(result, False)
            st.success(f"Saved the projection and {plays} graded play(s) to the tracker.")
    with b:
        if st.button("Save all three markets for shadow testing", use_container_width=True, key=f"cfb_shadow_{market_key}"):
            _, plays = save_result(result, True)
            st.success(f"Saved the projection and {plays} market rows.")

def _render_slate() -> None:
    st.subheader("Automatic Daily Slate")
    season = _current_cfb_season()
    provider = str(st.session_state.get("cfb_provider_value", ""))
    schedule = _ensure_automatic_schedule(season, provider)
    if schedule.empty:
        st.info("The public schedule is temporarily unavailable.")
        return
    dates = _available_slate_dates(schedule)
    default_date = _default_slate_date(schedule)
    if not dates or default_date is None:
        st.info("No usable slate dates are available.")
        return
    date_index = dates.index(default_date) if default_date in dates else 0
    slate_date = st.selectbox(
        "Slate date", dates, index=date_index,
        format_func=lambda value: value.strftime("%A, %B %d, %Y").replace(" 0", " "),
        key=f"cfb_slate_batch_date_{season}",
    )
    day = schedule[_schedule_date_series(schedule) == slate_date].copy()
    week_values = pd.to_numeric(day.get("Week"), errors="coerce").dropna()
    week = int(week_values.iloc[0]) if len(week_values) else _default_projection_week(schedule)
    ratings = _ensure_automatic_ratings(season, week)
    if ratings.empty:
        st.info("Automatic ratings are temporarily unavailable.")
        return

    done, total, errors = _ensure_automatic_day_slate_incremental(season, slate_date, provider, schedule, ratings)
    if total:
        st.progress(done / total, text=f"Automatic slate population: {done}/{total} games")
    if errors:
        with st.expander(f"{len(errors)} matchup(s) used partial fallbacks", expanded=False):
            st.code("\n".join(errors))

    frame = _sheet(SLATE_TAB, SLATE_COLUMNS)
    view = frame[
        (frame["Season"].astype(str) == str(season))
        & (frame["Date"].astype(str) == slate_date.isoformat())
        & (frame["Model Version"].astype(str) == MODEL_VERSION)
    ].copy() if not frame.empty else pd.DataFrame(columns=SLATE_COLUMNS)
    if view.empty:
        st.info("The first timeout-safe projection batch is being created automatically.")
    else:
        best_only = st.checkbox("Show graded plays only", value=False)
        if best_only:
            view = view[(view["Spread Grade"] != "No Play") | (view["Total Grade"] != "No Play") | (view["ML Grade"] != "No Play")]
        columns = ["Game", "Projected Away", "Projected Home", "Projected Margin", "Projected Total", "Spread Pick", "Spread Probability", "Spread Grade", "Total Pick", "Total Probability", "Total Grade", "ML Pick", "ML Probability", "ML Grade", "Reliability"]
        st.dataframe(view[columns], hide_index=True, use_container_width=True)

    if done < total:
        time.sleep(0.15)
        st.rerun()
    _settle_automatically_once(season)


def _render_tracker() -> None:
    st.subheader("Bet Tracker")
    frame = _sheet(TRACKER_TAB, TRACKER_COLUMNS)
    if frame.empty: st.info("No tracked plays yet."); return
    settled = frame[frame["Result"].isin(["Win", "Loss", "Push"])].copy()
    wins = int((settled["Result"] == "Win").sum()); losses = int((settled["Result"] == "Loss").sum()); pushes = int((settled["Result"] == "Push").sum())
    c1, c2, c3, c4 = st.columns(4); c1.metric("Wins", wins); c2.metric("Losses", losses); c3.metric("Pushes", pushes); c4.metric("Win rate", f"{wins/max(1,wins+losses):.1%}")
    st.dataframe(frame.sort_values(["Date", "Game"], ascending=False), hide_index=True, use_container_width=True)


def _render_ratings() -> None:
    st.subheader("Team Ratings")
    frame = _sheet(RATINGS_TAB, RATING_COLUMNS)
    if frame.empty:
        season = _current_cfb_season()
        schedule = _ensure_automatic_schedule(season, str(st.session_state.get("cfb_provider_value", "")))
        week = _default_projection_week(schedule) if not schedule.empty else 1
        frame = _ensure_automatic_ratings(season, week)
    if frame.empty: st.info("Automatic ratings are temporarily unavailable. Check the diagnostics on the Automation page."); return
    season = st.selectbox("Season", sorted(frame["Season"].astype(str).unique(), reverse=True), key="ratings_season")
    weeks = sorted(frame[frame["Season"].astype(str) == str(season)]["Projection Week"].astype(str).unique(), key=lambda x: _num(x))
    week = st.selectbox("Projection week", weeks, index=max(0, len(weeks)-1), key="ratings_week")
    view = frame[(frame["Season"].astype(str) == str(season)) & (frame["Projection Week"].astype(str) == str(week))].copy()
    view["Rank"] = pd.to_numeric(view["Power Rating"], errors="coerce").rank(ascending=False, method="min").astype("Int64")
    show = ["Rank", "Team", "Conference", "Power Rating", "Offense Rating", "Defense Rating", "Special Teams Rating", "Preseason Rating", "Previous Season Weight", "Current Season Weight", "FBS Games", "Data Confidence"]
    st.dataframe(view.sort_values("Power Rating", ascending=False)[show], hide_index=True, use_container_width=True)


def _render_schedule() -> None:
    st.subheader("Schedule and Market Feed")
    frame = _sheet(SCHEDULE_TAB, SCHEDULE_COLUMNS)
    if frame.empty:
        frame = _ensure_automatic_schedule(_current_cfb_season(), str(st.session_state.get("cfb_provider_value", "")))
    if frame.empty: st.info("The automatic schedule is temporarily unavailable. Check the diagnostics on the Automation page."); return
    season = st.selectbox("Season", sorted(frame["Season"].astype(str).unique(), reverse=True), key="schedule_season")
    view = frame[frame["Season"].astype(str) == str(season)]
    st.dataframe(view.sort_values(["Week", "Game Time"]), hide_index=True, use_container_width=True)


def _render_personnel() -> None:
    st.subheader("Personnel Snapshots")
    frame = _sheet(PERSONNEL_TAB, PERSONNEL_COLUMNS)
    if frame.empty: st.info("No quarterback/injury snapshots have been saved."); return
    st.dataframe(frame.sort_values(["Season", "Week", "Team"], ascending=[False, False, True]), hide_index=True, use_container_width=True)


def _render_calibration() -> None:
    st.subheader("Calibration and Residuals")
    frame = _sheet(CALIBRATION_TAB, CALIBRATION_COLUMNS)
    if frame.empty: st.info("Calibration populates after projected games are completed and settled."); return
    margin = pd.to_numeric(frame["Margin Residual"], errors="coerce"); total = pd.to_numeric(frame["Total Residual"], errors="coerce")
    c1, c2, c3 = st.columns(3); c1.metric("Games", len(frame)); c2.metric("Margin MAE", f"{margin.abs().mean():.2f}"); c3.metric("Total MAE", f"{total.abs().mean():.2f}")
    chart = frame[["Date", "Margin Residual", "Total Residual"]].copy(); chart["Margin Residual"] = pd.to_numeric(chart["Margin Residual"], errors="coerce"); chart["Total Residual"] = pd.to_numeric(chart["Total Residual"], errors="coerce")
    st.line_chart(chart.set_index("Date"))
    st.dataframe(frame.sort_values("Date", ascending=False), hide_index=True, use_container_width=True)



def _render_setup() -> None:
    st.subheader("Automation and Free Data Health")
    c1, c2, c3 = st.columns(3)
    c1.metric("Public college feed", "No key required")
    c2.metric("Google Sheets", "Connected" if sheets_ready() else "Not configured")
    c3.metric("Automatic mode", "Active")
    st.caption("No daily setup sequence and no API credential are required. Build renders the selected game first; Slate fills the selected date automatically in small batches that stay below Render timeout limits.")

    if st.button("Force refresh automatic NCAAF data", type="primary", use_container_width=True, key="cfb_setup_force_refresh"):
        _clear_automatic_state()
        st.success("Automatic caches were cleared. The next Build or Slate load will rebuild everything from free public sources.")
    if st.button("Settle completed games now", use_container_width=True, key="cfb_manual_settle"):
        try:
            settled, calibration = settle_results(_current_cfb_season())
            st.success(f"Settled {settled} bets and added {calibration} calibration games.")
        except Exception as exc:
            st.error(str(exc))

    schedule_warning = st.session_state.get("cfb_auto_schedule_warning")
    ratings_warning = st.session_state.get("cfb_auto_ratings_warning")
    settlement_warning = st.session_state.get("cfb_auto_settlement_warning")
    if schedule_warning or ratings_warning or settlement_warning:
        with st.expander("Automatic refresh diagnostics", expanded=False):
            if schedule_warning: st.code(f"Schedule: {schedule_warning}")
            if ratings_warning: st.code(f"Ratings: {ratings_warning}")
            if settlement_warning: st.code(f"Settlement: {settlement_warning}")

    st.markdown("### Free, no-key sources")
    st.markdown(
        "- ESPN public feeds: schedules, scores, team metadata, rosters, and sportsbook lines when ESPN publishes them.\n"
        "- SportsDataverse GitHub releases: open college-football play-by-play with EPA and advanced-play fields.\n"
        "- National Weather Service: official open hourly U.S. forecasts.\n"
        "- Venue coordinates: ESPN-listed stadium coordinates and prior-season home venues; missing coordinates remain unconfirmed.\n"
        "- Google Sheets remains your private storage layer; it is not a paid sports-data dependency."
    )
    st.markdown("### Model safeguards")
    st.markdown(
        "- All three markets come from the same simulated score distribution.\n"
        "- If ESPN does not publish a betting line, the builder leaves the line blank for a manual override instead of inventing it.\n"
        "- Recruiting, transfer, and returning-production inputs are transparent roster/program proxies from free data—not paid proprietary rankings.\n"
        "- Missing play-by-play, unconfirmed quarterbacks, incomplete injuries, extreme weather, and small samples lower reliability.\n"
        "- Automatic weekly refreshes update the slate but do not overwrite your bet tracker or unit amounts."
    )


def render() -> None:
    _inject_styles()
    page = st.sidebar.radio("College Football", ["Build", "Slate", "Tracker", "Team Ratings", "Schedule", "Personnel", "Calibration", "Automation"], key="cfb_page")
    st.caption(f"{MODEL_VERSION} • Fast automatic spread, moneyline, and totals score distribution")
    if page == "Build": _render_build()
    elif page == "Slate": _render_slate()
    elif page == "Tracker": _render_tracker()
    elif page == "Team Ratings": _render_ratings()
    elif page == "Schedule": _render_schedule()
    elif page == "Personnel": _render_personnel()
    elif page == "Calibration": _render_calibration()
    else: _render_setup()


if __name__ == "__main__":
    render()
