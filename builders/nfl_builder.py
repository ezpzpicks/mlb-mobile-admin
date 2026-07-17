"""EZPZ Picks NFL model builder.

Version 3.1 adds a rolling data-derived home-field model and fixes the mobile
player-prop card renderer while preserving the full opportunity/efficiency prop
engine, current-role transitions, matchup layers, and residual calibration.

Primary data source: nflverse through nflreadpy.
"""

from __future__ import annotations

from datetime import date, datetime
import gc
import hashlib
import html
import math
import os
import re
from typing import Any

import numpy as np
import pandas as pd
import requests
import streamlit as st

try:
    import polars as pl
except Exception:
    pl = None

# Keep nflreadpy's cache off the Streamlit process heap on small Render instances.
os.environ.setdefault("NFLREADPY_CACHE", "filesystem")
os.environ.setdefault("NFLREADPY_CACHE_DIR", "/tmp/nflreadpy_cache")
os.environ.setdefault("NFLREADPY_CACHE_DURATION", "21600")
os.environ.setdefault("NFLREADPY_VERBOSE", "False")
os.environ.setdefault("NFLREADPY_TIMEOUT", "45")

from shared.modeling import (
    american_implied_probability,
    clamp,
    expected_value_per_unit,
    probability_edge,
)
from shared.storage import read_sheet, sheets_ready, write_sheet

try:
    import nflreadpy as nfl
except Exception:
    nfl = None


MODEL_VERSION = "nfl-v3.1-auto-hfa-fixed-prop-tiles-2026-07-17"
DEFAULT_SEASON = 2026
DEFAULT_PRIOR_SEASON = DEFAULT_SEASON - 1
HOME_FIELD_LOOKBACK_SEASONS = 5
HOME_FIELD_PRIOR_POINTS = 1.5
HOME_FIELD_PRIOR_GAMES = 48.0

RATINGS_TAB = "nfl_team_ratings"
SLATE_TAB = "nfl_daily_slate"
TRACKER_TAB = "nfl_bet_tracker"
SCHEDULE_TAB = "nfl_schedule"
LINEUP_TAB = "nfl_lineup_snapshots"
MODEL_LOG_TAB = "nfl_model_change_log"
PROP_SLATE_TAB = "nfl_prop_projections"
PROP_TRACKER_TAB = "nfl_prop_tracker"
PROP_CALIBRATION_TAB = "nfl_prop_calibration"

RATING_COLUMNS = [
    "Team", "Season", "Projection Week", "Previous Season Weight", "Current Season Weight",
    "Power Rating", "Off EPA/Play", "Def EPA Edge", "Off Success Rate", "Def Success Edge",
    "Pass EPA/DB", "Pass Def EPA Edge", "Rush EPA/Play", "Rush Def EPA Edge",
    "Explosive Rate", "Explosive Def Edge", "Turnover Rate", "Takeaway Rate",
    "Sack Rate Allowed", "Sack/Pressure Edge", "Pace", "Points/Game", "Points Allowed/Game",
    "Red Zone TD Rate", "Red Zone Def Edge", "Offensive Plays", "Games",
    "QB Adjustment", "OL Adjustment", "Skill/Injury Adjustment", "Front Seven Adjustment",
    "Secondary Adjustment", "Special Teams", "Data Confidence", "Source", "Updated",
]

SLATE_COLUMNS = [
    "Date", "Season", "Week", "Game ID", "Game", "Away Team", "Home Team",
    "Projected Away", "Projected Home", "Projected Margin", "Projected Total",
    "Away Score Low", "Away Score High", "Home Score Low", "Home Score High",
    "Market Home Spread", "Market Total", "Away ML", "Home ML",
    "Spread Pick", "Spread Probability", "Spread Edge", "Spread Grade", "Spread Confluence",
    "Total Pick", "Total Probability", "Total Edge", "Total Grade", "Total Confluence",
    "ML Pick", "ML Probability", "ML Odds", "ML Edge", "ML Grade", "ML Confluence",
    "Reliability", "Data Confidence", "Personnel Confidence", "Previous Season Weight",
    "Current Season Weight", "Away Offensive Absence", "Away Defensive Absence",
    "Home Offensive Absence", "Home Defensive Absence", "Weather Adjustment",
    "Roof", "Temperature", "Wind", "Model Version", "Notes",
]

TRACKER_COLUMNS = [
    "Date", "Season", "Week", "Game ID", "Game", "Bet Type", "Selection", "Odds/Line",
    "Model Probability", "Implied Probability", "Edge", "Expected Value", "Grade",
    "Confluence", "Result", "Reliability", "Data Confidence", "Personnel Confidence",
    "Projected Away", "Projected Home", "Model Version", "Notes",
]

SCHEDULE_COLUMNS = [
    "Season", "Game Type", "Week", "Game Date", "Game Time", "Away Team", "Home Team",
    "Away Score", "Home Score", "Away Rest", "Home Rest", "Away ML", "Home ML",
    "Spread Line", "Total Line", "Roof", "Temperature", "Wind", "Surface", "Stadium", "Location", "Game ID",
]

LINEUP_COLUMNS = [
    "Date", "Season", "Week", "Game ID", "Team", "Unit", "Slot", "Player", "Position",
    "Depth Rank", "Injury Status", "Auto Play Probability", "Manual Play Probability",
    "Manual Role Share", "Base Impact", "Manual Impact", "Effective Play Probability", "Absence Cost",
    "Model Version",
]

MODEL_LOG_COLUMNS = ["Date", "Model Version", "Change"]

PROP_PROJECTION_COLUMNS = [
    "Date", "Season", "Week", "Game ID", "Game", "Team", "Opponent", "Home/Away",
    "Player", "Position", "Slot", "Market", "Raw Projection", "Calibration Adjustment",
    "Projection", "Fair Line", "Market Line", "Over Odds", "Under Odds", "Pick", "Pick Odds",
    "Model Probability", "Push Probability", "Implied Probability", "Probability Edge",
    "Projection Edge", "Expected Value", "Grade", "Reliability", "Role Confidence",
    "Route Confidence", "Matchup Confidence", "Data Confidence", "Calibration Sample",
    "Distribution", "Matchup Index", "Coverage Matchup", "Projected Team Plays",
    "Projected Dropbacks", "Expected Sacks", "Projected Pass Attempts", "Projected Rush Attempts",
    "Projected Routes", "Route Participation", "Targets Per Route", "Projected Player Attempts",
    "Projected Completions", "Projected Targets", "Projected Receptions", "Efficiency", "Line Source", "Confluence",
    "Model Version", "Notes",
]

PROP_TRACKER_COLUMNS = PROP_PROJECTION_COLUMNS + [
    "Result", "Actual Attempts", "Actual Completions", "Actual Targets", "Actual Receptions", "Actual Result",
    "Opportunity Error", "Efficiency Error", "Projection Residual",
]

PROP_CALIBRATION_COLUMNS = [
    "Date", "Season", "Week", "Game ID", "Player", "Position", "Market",
    "Projection", "Market Line", "Actual Result", "Projected Opportunity",
    "Actual Opportunity", "Projected Efficiency", "Actual Efficiency",
    "Opportunity Error", "Efficiency Error", "Projection Residual",
    "Opponent", "Role Confidence", "Reliability", "Model Version",
]

NFL_TEAMS = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB",
    "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
]

TEAM_ALIASES = {
    "ARZ": "ARI", "BLT": "BAL", "CLV": "CLE", "HST": "HOU", "OAK": "LV",
    "SD": "LAC", "STL": "LAR", "WSH": "WAS",
}

OFFENSE_SLOTS = [
    ("QB", ["QB"]),
    ("RB1", ["RB", "HB"]),
    ("RB2", ["RB", "HB", "FB"]),
    ("WR1", ["WR"]),
    ("WR2", ["WR"]),
    ("WR3", ["WR"]),
    ("TE", ["TE"]),
    ("LT", ["LT", "T"]),
    ("LG", ["LG", "G"]),
    ("C", ["C"]),
    ("RG", ["RG", "G"]),
    ("RT", ["RT", "T"]),
]

DEFENSE_SLOTS = [
    ("EDGE1", ["EDGE", "DE", "OLB"]),
    ("EDGE2", ["EDGE", "DE", "OLB"]),
    ("DT1", ["DT", "NT", "DL"]),
    ("DT2", ["DT", "NT", "DL"]),
    ("LB1", ["LB", "ILB", "MLB", "OLB"]),
    ("LB2", ["LB", "ILB", "MLB", "OLB"]),
    ("CB1", ["CB"]),
    ("CB2", ["CB"]),
    ("SLOT", ["CB", "NB", "DB"]),
    ("S1", ["S", "FS", "SS"]),
    ("S2", ["S", "FS", "SS"]),
]

POSITION_BASE_IMPACT = {
    "QB": 4.5,
    "RB1": 0.9, "RB2": 0.35,
    "WR1": 1.15, "WR2": 0.75, "WR3": 0.40,
    "TE": 0.65,
    "LT": 0.85, "LG": 0.45, "C": 0.55, "RG": 0.45, "RT": 0.75,
    "EDGE1": 0.90, "EDGE2": 0.65,
    "DT1": 0.55, "DT2": 0.35,
    "LB1": 0.50, "LB2": 0.35,
    "CB1": 0.85, "CB2": 0.60, "SLOT": 0.45,
    "S1": 0.55, "S2": 0.40,
}

STATUS_PROBABILITY = {
    "OUT": 0.0,
    "INACTIVE": 0.0,
    "INJURED RESERVE": 0.0,
    "SUSPENDED": 0.0,
    "NFI": 0.0,
    "IR": 0.0,
    "PUP": 0.0,
    "DOUBTFUL": 0.20,
    "QUESTIONABLE": 0.65,
    "PROBABLE": 0.92,
    "DID NOT PARTICIPATE": 0.72,
    "DNP": 0.72,
    "LIMITED": 0.85,
    "FULL": 0.98,
    "ACTIVE": 1.0,
    "HEALTHY": 1.0,
    "": 1.0,
}


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------

def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in [None, ""] or pd.isna(value):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _safe_text(value: Any) -> str:
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value or "").strip()


def _normalize_team(value: Any) -> str:
    team = _safe_text(value).upper()
    return TEAM_ALIASES.get(team, team)


def _normalize_name(value: Any) -> str:
    text = _safe_text(value).lower()
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _column(df: pd.DataFrame, *names: str, default: Any = 0) -> pd.Series:
    for name in names:
        if name in df.columns:
            return df[name]
    return pd.Series(default, index=df.index)


def _first_existing(row: pd.Series | dict[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        try:
            value = row.get(name, default)
        except Exception:
            value = default
        if _safe_text(value) not in ["", "nan", "None"]:
            return value
    return default


def _to_pandas(frame: Any) -> pd.DataFrame:
    if frame is None:
        return pd.DataFrame()
    if isinstance(frame, pd.DataFrame):
        return frame.copy()
    if hasattr(frame, "to_dicts"):
        return pd.DataFrame(frame.to_dicts())
    if hasattr(frame, "to_pandas"):
        return frame.to_pandas()
    return pd.DataFrame(frame)


def _team_row(ratings: pd.DataFrame, team: str, season: int | None = None, week: int | None = None) -> dict[str, Any]:
    if ratings is None or ratings.empty:
        return _neutral_rating(team, season or DEFAULT_SEASON, week or 1)
    out = ratings.copy()
    out["Team"] = out["Team"].astype(str).map(_normalize_team)
    out = out[out["Team"] == _normalize_team(team)]
    if season is not None and "Season" in out.columns:
        season_values = pd.to_numeric(out["Season"], errors="coerce")
        season_rows = out[season_values == int(season)]
        if not season_rows.empty:
            out = season_rows
    if week is not None and "Projection Week" in out.columns:
        week_values = pd.to_numeric(out["Projection Week"], errors="coerce")
        eligible = out[week_values <= int(week)]
        if not eligible.empty:
            out = eligible.sort_values("Projection Week")
    if out.empty:
        return _neutral_rating(team, season or DEFAULT_SEASON, week or 1)
    return out.iloc[-1].to_dict()


def _load_ratings() -> pd.DataFrame:
    return read_sheet(RATINGS_TAB, RATING_COLUMNS)


def _load_schedule_sheet() -> pd.DataFrame:
    return read_sheet(SCHEDULE_TAB, SCHEDULE_COLUMNS)


def _season_weight(projection_week: int, current_games: float | None = None) -> float:
    """Current-season weight for a projection made before the selected week.

    Week 1 is entirely prior-season based. The current season then receives more
    weight as real games are played, while retaining a small prior through the
    season to reduce overreaction to short samples.
    """
    week = max(1, int(projection_week or 1))
    curve = {
        1: 0.00, 2: 0.15, 3: 0.30, 4: 0.45, 5: 0.58, 6: 0.68,
        7: 0.76, 8: 0.82, 9: 0.86, 10: 0.89, 11: 0.91, 12: 0.92,
    }
    weight = curve.get(week, 0.93)
    if current_games is not None:
        games = max(0.0, float(current_games))
        sample_cap = min(0.93, games / (games + 4.7)) if games > 0 else 0.0
        weight = min(weight, sample_cap + (0.02 if games > 0 else 0.0))
    return round(clamp(weight, 0.0, 0.93), 4)


def _neutral_rating(team: str, season: int, week: int) -> dict[str, Any]:
    current_weight = _season_weight(week, 0)
    row = {column: 0.0 for column in RATING_COLUMNS}
    row.update({
        "Team": _normalize_team(team),
        "Season": season,
        "Projection Week": week,
        "Previous Season Weight": round(1 - current_weight, 4),
        "Current Season Weight": current_weight,
        "Off Success Rate": 0.43,
        "Explosive Rate": 0.105,
        "Turnover Rate": 0.024,
        "Takeaway Rate": 0.024,
        "Sack Rate Allowed": 0.067,
        "Pace": 64.0,
        "Points/Game": 22.5,
        "Points Allowed/Game": 22.5,
        "Red Zone TD Rate": 0.56,
        "Games": 0,
        "Data Confidence": 35.0,
        "Source": "Neutral fallback",
        "Updated": str(date.today()),
    })
    return row


def _seed_neutral_ratings(season: int = DEFAULT_SEASON, week: int = 1) -> pd.DataFrame:
    return pd.DataFrame([_neutral_rating(team, season, week) for team in NFL_TEAMS], columns=RATING_COLUMNS)


# -----------------------------------------------------------------------------
# nflverse loading
# -----------------------------------------------------------------------------

def _require_nflreadpy() -> None:
    if nfl is None:
        raise RuntimeError(
            "nflreadpy is not installed. Replace requirements.txt with the supplied NFL version and redeploy."
        )


@st.cache_data(ttl=21600, show_spinner=False)
def _load_schedule_live(season: int) -> pd.DataFrame:
    _require_nflreadpy()
    frame = _to_pandas(nfl.load_schedules(int(season)))
    if frame.empty:
        return pd.DataFrame(columns=SCHEDULE_COLUMNS)
    return _format_schedule(frame, int(season))


PBP_REQUIRED_COLUMNS = [
    "season_type", "week", "game_id", "play_id", "drive", "posteam", "defteam", "epa",
    "yards_gained", "success", "qb_dropback", "pass_attempt", "rush_attempt",
    "complete_pass", "incomplete_pass", "air_yards", "yards_after_catch", "pass_location",
    "run_location", "run_gap", "interception", "fumble_lost", "sack", "qb_hit",
    "qb_scramble", "designed_run", "touchdown", "yardline_100", "shotgun", "no_huddle",
    "passer_player_id", "passer_player_name", "receiver_player_id", "receiver_player_name",
    "rusher_player_id", "rusher_player_name",
]
PLAYER_STATS_REQUIRED_COLUMNS = [
    "season", "season_type", "week", "game_id", "opponent_team",
    "player_id", "gsis_id", "player_display_name", "player_name", "full_name",
    "recent_team", "team", "position", "position_group", "headshot_url",
    "attempts", "completions", "passing_epa", "passing_yards", "passing_tds",
    "interceptions", "sacks", "sack_yards", "passing_air_yards",
    "passing_yards_after_catch", "pacr", "dakota",
    "carries", "rushing_epa", "rushing_yards", "rushing_tds",
    "rushing_first_downs", "rushing_fumbles_lost",
    "targets", "receptions", "receiving_epa", "receiving_yards", "receiving_tds",
    "receiving_air_yards", "receiving_yards_after_catch", "receiving_first_downs",
    "target_share", "air_yards_share", "wopr", "racr",
]

SNAP_REQUIRED_COLUMNS = [
    "season", "week", "game_id", "team", "opponent", "player", "player_name",
    "pfr_player_id", "position", "offense_snaps", "offense_pct",
]

NGS_PASS_REQUIRED_COLUMNS = [
    "season", "season_type", "week", "player_display_name", "player_gsis_id",
    "team_abbr", "attempts", "avg_time_to_throw", "avg_completed_air_yards",
    "avg_intended_air_yards", "avg_air_yards_differential", "aggressiveness",
    "passer_rating", "completion_percentage", "expected_completion_percentage",
    "completion_percentage_above_expectation",
]

NGS_RUSH_REQUIRED_COLUMNS = [
    "season", "season_type", "week", "player_display_name", "player_gsis_id",
    "team_abbr", "efficiency", "percent_attempts_gte_eight_defenders",
    "avg_time_to_los", "rush_attempts", "rush_yards", "expected_rush_yards",
    "rush_yards_over_expected", "rush_yards_over_expected_per_att",
    "rush_pct_over_expected",
]

NGS_REC_REQUIRED_COLUMNS = [
    "season", "season_type", "week", "player_display_name", "player_gsis_id",
    "team_abbr", "avg_cushion", "avg_separation", "avg_intended_air_yards",
    "percent_share_of_intended_air_yards", "receptions", "targets",
    "catch_percentage", "yards", "rec_touchdowns", "avg_yac",
    "avg_expected_yac", "avg_yac_above_expectation",
]

FTN_REQUIRED_COLUMNS = [
    "nflverse_game_id", "nflverse_play_id", "season", "week", "is_play_action",
    "is_screen_pass", "is_rpo", "is_qb_out_of_pocket", "is_interception_worthy",
    "is_throw_away", "is_catchable_ball", "is_contested_ball", "is_created_reception",
    "is_drop", "is_qb_sneak", "n_blitzers", "n_pass_rushers", "is_qb_fault_sack",
]

PARTICIPATION_REQUIRED_COLUMNS = [
    "nflverse_game_id", "play_id", "possession_team", "defenders_in_box",
    "number_of_pass_rushers", "was_pressure", "route", "defense_man_zone_type",
    "defense_coverage_type",
]
DEPTH_REQUIRED_COLUMNS = [
    "team", "player_name", "full_name", "pos_abb", "position", "pos_rank",
    "depth_team", "pos_slot", "dt",
]
INJURY_REQUIRED_COLUMNS = [
    "season", "week", "team", "full_name", "player_name", "report_status",
    "practice_status", "report_primary_injury", "practice_primary_injury",
]


def _compact_to_pandas(frame: Any, columns: list[str]) -> pd.DataFrame:
    """Select only model columns before converting Polars to Pandas.

    This avoids keeping hundreds of unused nflverse columns in Streamlit memory.
    """
    if frame is None:
        return pd.DataFrame()
    try:
        available = set(frame.columns)
        selected = [column for column in columns if column in available]
        if selected and hasattr(frame, "select"):
            compact = frame.select(selected)
            del frame
            gc.collect()
            return compact.to_pandas()
    except Exception:
        pass
    out = _to_pandas(frame)
    selected = [column for column in columns if column in out.columns]
    return out[selected].copy() if selected else out


def _pbp_cache_path(season: int) -> str:
    return f"/tmp/ezpz_nfl_pbp_{int(season)}.parquet"


def _download_to_disk(url: str, destination: str) -> None:
    """Stream a large nflverse file to disk without holding it in app memory."""
    temp_path = f"{destination}.part"
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with requests.get(url, stream=True, timeout=120, allow_redirects=True) as response:
        response.raise_for_status()
        with open(temp_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    if not os.path.exists(temp_path) or os.path.getsize(temp_path) < 1024:
        raise RuntimeError("The nflverse play-by-play download was empty.")
    os.replace(temp_path, destination)


def _load_pbp_season(season: int) -> pd.DataFrame:
    """Load only the play-by-play columns used by the model.

    nflreadpy 0.1.x does not accept a ``columns=`` argument for ``load_pbp``.
    The previous builder therefore retried with the full 300+ column dataset,
    which could exhaust Render memory and then silently return neutral ratings.
    This loader streams the parquet asset to /tmp and lets Polars project only
    the required columns before conversion to Pandas.
    """
    season = int(season)
    cache_path = _pbp_cache_path(season)
    url = (
        "https://github.com/nflverse/nflverse-data/releases/download/pbp/"
        f"play_by_play_{season}.parquet"
    )
    try:
        if pl is None:
            raise RuntimeError("Polars is not installed.")
        if not os.path.exists(cache_path) or os.path.getsize(cache_path) < 1024:
            _download_to_disk(url, cache_path)
        lazy = pl.scan_parquet(cache_path)
        available = set(lazy.collect_schema().names())
        selected = [column for column in PBP_REQUIRED_COLUMNS if column in available]
        required_core = {"posteam", "defteam", "epa", "week", "game_id"}
        if not required_core.issubset(set(selected)):
            missing = sorted(required_core - set(selected))
            raise RuntimeError(f"Play-by-play file is missing required columns: {missing}")
        frame = lazy.select(selected).collect()
        output = frame.to_pandas()
        st.session_state[f"nfl_pbp_status_{season}"] = (
            f"Loaded {len(output):,} plays and {len(selected)} compact columns from nflverse."
        )
        st.session_state.pop(f"nfl_pbp_error_{season}", None)
        return output
    except Exception as exc:
        st.session_state[f"nfl_pbp_error_{season}"] = str(exc)
        st.session_state[f"nfl_pbp_status_{season}"] = "Play-by-play unavailable; schedule fallback used."
        return pd.DataFrame()


@st.cache_data(ttl=21600, show_spinner=False)
def _load_player_stats_season(season: int) -> pd.DataFrame:
    _require_nflreadpy()
    try:
        frame = nfl.load_player_stats(int(season), summary_level="week")
        return _compact_to_pandas(frame, PLAYER_STATS_REQUIRED_COLUMNS)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=21600, show_spinner=False)
def _load_depth_charts_season(season: int) -> pd.DataFrame:
    _require_nflreadpy()
    try:
        frame = nfl.load_depth_charts(int(season))
        return _compact_to_pandas(frame, DEPTH_REQUIRED_COLUMNS)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=21600, show_spinner=False)
def _load_injuries_season(season: int) -> pd.DataFrame:
    _require_nflreadpy()
    try:
        frame = nfl.load_injuries(int(season))
        return _compact_to_pandas(frame, INJURY_REQUIRED_COLUMNS)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=86400, show_spinner=False)
def _load_sleeper_players() -> pd.DataFrame:
    """Daily current-team, depth-order and injury fallback from Sleeper.

    Sleeper asks clients to cache this roughly 5 MB endpoint and call it no more
    than once per day. Only compact fields used by the model are retained.
    """
    try:
        response = requests.get("https://api.sleeper.app/v1/players/nfl", timeout=60)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return pd.DataFrame()
        rows: list[dict[str, Any]] = []
        for player_id, raw in payload.items():
            if not isinstance(raw, dict):
                continue
            team = _normalize_team(raw.get("team", ""))
            position = _safe_text(raw.get("position", "")).upper()
            first = _safe_text(raw.get("first_name", ""))
            last = _safe_text(raw.get("last_name", ""))
            full_name = _safe_text(raw.get("full_name", "")) or f"{first} {last}".strip()
            if not team or team not in NFL_TEAMS or not full_name or not position:
                continue
            rows.append({
                "player_id": _safe_text(raw.get("player_id", player_id)),
                "team": team,
                "full_name": full_name,
                "position": position,
                "depth_chart_order": _int(raw.get("depth_chart_order", 99), 99),
                "depth_chart_position": _safe_text(raw.get("depth_chart_position", "")),
                "status": _safe_text(raw.get("status", "")),
                "injury_status": _safe_text(raw.get("injury_status", "")),
                "practice_participation": _safe_text(raw.get("practice_participation", "")),
                "injury_body_part": _safe_text(raw.get("injury_body_part", "")),
                "injury_notes": _safe_text(raw.get("injury_notes", "")),
                "news_updated": _safe_text(raw.get("news_updated", "")),
            })
        return pd.DataFrame(rows)
    except Exception as exc:
        st.session_state["nfl_sleeper_error"] = str(exc)
        return pd.DataFrame()


@st.cache_data(ttl=21600, show_spinner=False)
def _load_snap_counts_season(season: int) -> pd.DataFrame:
    _require_nflreadpy()
    try:
        frame = nfl.load_snap_counts(int(season))
        return _compact_to_pandas(frame, SNAP_REQUIRED_COLUMNS)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=21600, show_spinner=False)
def _load_nextgen_season(season: int, stat_type: str) -> pd.DataFrame:
    _require_nflreadpy()
    required = {
        "passing": NGS_PASS_REQUIRED_COLUMNS,
        "rushing": NGS_RUSH_REQUIRED_COLUMNS,
        "receiving": NGS_REC_REQUIRED_COLUMNS,
    }.get(str(stat_type), [])
    try:
        frame = nfl.load_nextgen_stats(int(season), stat_type=str(stat_type))
        return _compact_to_pandas(frame, required)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=21600, show_spinner=False)
def _load_ftn_charting_season(season: int) -> pd.DataFrame:
    """Load live FTN charting. It normally posts within roughly 48 hours of games."""
    _require_nflreadpy()
    try:
        frame = nfl.load_ftn_charting(int(season))
        return _compact_to_pandas(frame, FTN_REQUIRED_COLUMNS)
    except Exception as exc:
        st.session_state[f"nfl_ftn_error_{int(season)}"] = str(exc)
        return pd.DataFrame()


@st.cache_data(ttl=21600, show_spinner=False)
def _load_participation_season(season: int) -> pd.DataFrame:
    """Load historical participation/coverage data when nflverse has published it."""
    _require_nflreadpy()
    try:
        frame = nfl.load_participation(int(season))
        return _compact_to_pandas(frame, PARTICIPATION_REQUIRED_COLUMNS)
    except Exception as exc:
        st.session_state[f"nfl_participation_error_{int(season)}"] = str(exc)
        return pd.DataFrame()


def _format_schedule(dataframe: pd.DataFrame, season: int) -> pd.DataFrame:
    if dataframe is None or dataframe.empty:
        return pd.DataFrame(columns=SCHEDULE_COLUMNS)
    df = dataframe.copy()
    if "season" in df.columns:
        df = df[pd.to_numeric(df["season"], errors="coerce") == int(season)].copy()
    output = pd.DataFrame(index=df.index)
    output["Season"] = _column(df, "season", default=season)
    output["Game Type"] = _column(df, "game_type", default="REG")
    output["Week"] = _column(df, "week", default="")
    output["Game Date"] = _column(df, "gameday", "game_date", default="")
    output["Game Time"] = _column(df, "gametime", "game_time", default="")
    output["Away Team"] = _column(df, "away_team", default="").map(_normalize_team)
    output["Home Team"] = _column(df, "home_team", default="").map(_normalize_team)
    output["Away Score"] = _column(df, "away_score", default="")
    output["Home Score"] = _column(df, "home_score", default="")
    output["Away Rest"] = _column(df, "away_rest", default="")
    output["Home Rest"] = _column(df, "home_rest", default="")
    output["Away ML"] = _column(df, "away_moneyline", "away_ml", default="")
    output["Home ML"] = _column(df, "home_moneyline", "home_ml", default="")
    output["Spread Line"] = _column(df, "spread_line", default="")
    output["Total Line"] = _column(df, "total_line", default="")
    output["Roof"] = _column(df, "roof", default="")
    output["Temperature"] = _column(df, "temp", "temperature", default="")
    output["Wind"] = _column(df, "wind", default="")
    output["Surface"] = _column(df, "surface", default="")
    output["Stadium"] = _column(df, "stadium", default="")
    output["Location"] = _column(df, "location", default="")
    output["Game ID"] = _column(df, "game_id", default="")
    output = output[SCHEDULE_COLUMNS].copy()
    output = output[output["Away Team"].astype(str).str.len() > 0]
    output = output[output["Home Team"].astype(str).str.len() > 0]
    return output.reset_index(drop=True)


@st.cache_data(ttl=21600, show_spinner=False)
def _load_schedule_csv_fallback(season: int) -> pd.DataFrame:
    """Load schedules/results from lightweight CSV mirrors when the package call fails."""
    urls = [
        "https://github.com/nflverse/nfldata/raw/master/data/games.csv",
        "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv",
    ]
    errors = []
    for url in urls:
        try:
            dataframe = pd.read_csv(url)
            formatted = _format_schedule(dataframe, int(season))
            if not formatted.empty:
                return formatted
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        st.session_state[f"nfl_schedule_error_{int(season)}"] = errors[-1]
    return pd.DataFrame(columns=SCHEDULE_COLUMNS)


def _schedule_for_season(season: int, refresh: bool = False) -> pd.DataFrame:
    season = int(season)
    if refresh:
        _load_schedule_live.clear()
        _load_schedule_csv_fallback.clear()
    try:
        live = _load_schedule_live(season)
        if not live.empty:
            st.session_state[f"nfl_schedule_status_{season}"] = f"Loaded {len(live)} games through nflreadpy."
            return live
    except Exception as exc:
        st.session_state[f"nfl_schedule_error_{season}"] = str(exc)

    fallback = _load_schedule_csv_fallback(season)
    if not fallback.empty:
        st.session_state[f"nfl_schedule_status_{season}"] = f"Loaded {len(fallback)} games from the schedule CSV fallback."
        return fallback

    sheet = _load_schedule_sheet()
    if not sheet.empty:
        season_values = pd.to_numeric(sheet["Season"], errors="coerce")
        saved = sheet[season_values == season].copy().reset_index(drop=True)
        if not saved.empty:
            st.session_state[f"nfl_schedule_status_{season}"] = f"Loaded {len(saved)} saved schedule rows from Google Sheets."
            return saved
    return pd.DataFrame(columns=SCHEDULE_COLUMNS)


# -----------------------------------------------------------------------------
# Automated team ratings
# -----------------------------------------------------------------------------

def _long_scores(schedule: pd.DataFrame) -> pd.DataFrame:
    if schedule is None or schedule.empty:
        return pd.DataFrame(columns=["team", "game_id", "week", "points_for", "points_against"])
    df = schedule.copy()
    away = pd.DataFrame({
        "team": df["Away Team"].map(_normalize_team),
        "game_id": df["Game ID"],
        "week": pd.to_numeric(df["Week"], errors="coerce"),
        "points_for": pd.to_numeric(df["Away Score"], errors="coerce"),
        "points_against": pd.to_numeric(df["Home Score"], errors="coerce"),
    })
    home = pd.DataFrame({
        "team": df["Home Team"].map(_normalize_team),
        "game_id": df["Game ID"],
        "week": pd.to_numeric(df["Week"], errors="coerce"),
        "points_for": pd.to_numeric(df["Home Score"], errors="coerce"),
        "points_against": pd.to_numeric(df["Away Score"], errors="coerce"),
    })
    out = pd.concat([away, home], ignore_index=True)
    return out.dropna(subset=["points_for", "points_against"])


def _schedule_metrics(schedule: pd.DataFrame, through_week: int | None = None) -> pd.DataFrame:
    long = _long_scores(schedule)
    if through_week is not None and not long.empty:
        long = long[pd.to_numeric(long["week"], errors="coerce") <= int(through_week)].copy()
    if long.empty:
        return pd.DataFrame()
    grouped = long.groupby("team", as_index=False).agg(
        **{
            "Points/Game": ("points_for", "mean"),
            "Points Allowed/Game": ("points_against", "mean"),
            "Games": ("game_id", "nunique"),
        }
    )
    return grouped.set_index("team")


def _prepare_pbp(pbp: pd.DataFrame, through_week: int | None = None) -> pd.DataFrame:
    if pbp is None or pbp.empty:
        return pd.DataFrame()
    df = pbp.copy()
    if "season_type" in df.columns:
        df = df[df["season_type"].astype(str).str.upper() == "REG"].copy()
    if through_week is not None and "week" in df.columns:
        df = df[pd.to_numeric(df["week"], errors="coerce") <= int(through_week)].copy()
    df["posteam"] = _column(df, "posteam", default="").map(_normalize_team)
    df["defteam"] = _column(df, "defteam", default="").map(_normalize_team)
    df["epa_value"] = pd.to_numeric(_column(df, "epa", default=np.nan), errors="coerce")
    df["yards_value"] = pd.to_numeric(_column(df, "yards_gained", default=0), errors="coerce").fillna(0)
    df["success_value"] = pd.to_numeric(_column(df, "success", default=np.nan), errors="coerce")
    df["success_value"] = df["success_value"].fillna((df["epa_value"] > 0).astype(float))
    df["pass_flag"] = (
        pd.to_numeric(_column(df, "qb_dropback", "pass_attempt", default=0), errors="coerce").fillna(0) > 0
    ).astype(int)
    df["rush_flag"] = (
        pd.to_numeric(_column(df, "rush_attempt", default=0), errors="coerce").fillna(0) > 0
    ).astype(int)
    df["valid_play"] = ((df["pass_flag"] == 1) | (df["rush_flag"] == 1)).astype(int)
    df["explosive_value"] = (
        ((df["pass_flag"] == 1) & (df["yards_value"] >= 20))
        | ((df["rush_flag"] == 1) & (df["yards_value"] >= 10))
    ).astype(int)
    interception = pd.to_numeric(_column(df, "interception", default=0), errors="coerce").fillna(0)
    fumble_lost = pd.to_numeric(_column(df, "fumble_lost", default=0), errors="coerce").fillna(0)
    df["turnover_value"] = ((interception > 0) | (fumble_lost > 0)).astype(int)
    df["sack_value"] = (
        pd.to_numeric(_column(df, "sack", default=0), errors="coerce").fillna(0) > 0
    ).astype(int)
    df["touchdown_value"] = (
        pd.to_numeric(_column(df, "touchdown", default=0), errors="coerce").fillna(0) > 0
    ).astype(int)
    df["yardline_value"] = pd.to_numeric(_column(df, "yardline_100", default=np.nan), errors="coerce")
    df = df[
        (df["posteam"].astype(str).str.len() > 0)
        & (df["defteam"].astype(str).str.len() > 0)
        & (df["valid_play"] == 1)
        & df["epa_value"].notna()
    ].copy()
    return df


def _red_zone_rates(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    if df.empty or "game_id" not in df.columns or "drive" not in df.columns:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    rz = df[df["yardline_value"].notna() & (df["yardline_value"] <= 20)].copy()
    if rz.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    drive = rz.groupby(["game_id", "drive", "posteam", "defteam"], as_index=False).agg(
        red_zone_td=("touchdown_value", "max")
    )
    offense = drive.groupby("posteam")["red_zone_td"].mean()
    defense_allowed = drive.groupby("defteam")["red_zone_td"].mean()
    return offense, defense_allowed


def _season_team_metrics(pbp: pd.DataFrame, schedule: pd.DataFrame, through_week: int | None = None) -> pd.DataFrame:
    df = _prepare_pbp(pbp, through_week)
    schedule_stats = _schedule_metrics(schedule, through_week)
    teams = sorted(set(NFL_TEAMS) | set(schedule_stats.index.tolist() if not schedule_stats.empty else []))
    if df.empty:
        # Do not return 32 identical neutral rows. When the large play-by-play
        # source is unavailable, use completed schedule scoring as a transparent
        # lower-confidence proxy until the compact PBP download succeeds.
        league_ppg = (
            float(pd.to_numeric(schedule_stats.get("Points/Game"), errors="coerce").mean())
            if not schedule_stats.empty else 22.5
        )
        if not math.isfinite(league_ppg):
            league_ppg = 22.5
        rows = []
        for team in teams:
            ppg = _num(schedule_stats.loc[team, "Points/Game"], league_ppg) if team in schedule_stats.index else league_ppg
            papg = _num(schedule_stats.loc[team, "Points Allowed/Game"], league_ppg) if team in schedule_stats.index else league_ppg
            games = _num(schedule_stats.loc[team, "Games"], 0) if team in schedule_stats.index else 0
            off_proxy = (ppg - league_ppg) / 65.0
            def_proxy = (league_ppg - papg) / 65.0
            success_proxy = clamp(0.43 + (ppg - league_ppg) / 180.0, 0.34, 0.52)
            def_success_proxy = clamp((league_ppg - papg) / 180.0, -0.08, 0.08)
            explosive_proxy = clamp(0.105 + (ppg - league_ppg) / 300.0, 0.07, 0.15)
            explosive_def_proxy = clamp((league_ppg - papg) / 300.0, -0.04, 0.04)
            red_zone_proxy = clamp(0.56 + (ppg - league_ppg) / 100.0, 0.38, 0.72)
            red_zone_def_proxy = clamp((league_ppg - papg) / 100.0, -0.18, 0.18)
            rows.append({
                "Team": team,
                "Power Rating": 0.65 * (ppg - papg) + 0.20 * (ppg - league_ppg) + 0.20 * (league_ppg - papg),
                "Off EPA/Play": off_proxy,
                "Def EPA Edge": def_proxy,
                "Off Success Rate": success_proxy,
                "Def Success Edge": def_success_proxy,
                "Pass EPA/DB": off_proxy * 1.12,
                "Pass Def EPA Edge": def_proxy * 1.12,
                "Rush EPA/Play": off_proxy * 0.72 - 0.03,
                "Rush Def EPA Edge": def_proxy * 0.72,
                "Explosive Rate": explosive_proxy,
                "Explosive Def Edge": explosive_def_proxy,
                "Turnover Rate": 0.024,
                "Takeaway Rate": 0.024,
                "Sack Rate Allowed": 0.067,
                "Sack/Pressure Edge": 0.0,
                "Points/Game": ppg,
                "Points Allowed/Game": papg,
                "Games": games,
                "Pace": 64.0,
                "Red Zone TD Rate": red_zone_proxy,
                "Red Zone Def Edge": red_zone_def_proxy,
                "Offensive Plays": games * 64.0,
                "Data Confidence": clamp(38 + games * 2.2, 38, 75),
            })
        result = pd.DataFrame(rows).set_index("Team")
        if not result.empty:
            result["Power Rating"] = result["Power Rating"] - result["Power Rating"].mean()
        result.attrs["source"] = "schedule scoring fallback (EPA fields are scoring-derived estimates)"
        return result

    league_epa = df["epa_value"].mean()
    league_success = df["success_value"].mean()
    league_explosive = df["explosive_value"].mean()
    league_turnover = df["turnover_value"].mean()
    league_sack = df.loc[df["pass_flag"] == 1, "sack_value"].mean()

    offense = df.groupby("posteam").agg(
        **{
            "Off EPA/Play": ("epa_value", "mean"),
            "Off Success Rate": ("success_value", "mean"),
            "Explosive Rate": ("explosive_value", "mean"),
            "Turnover Rate": ("turnover_value", "mean"),
            "Offensive Plays": ("valid_play", "sum"),
        }
    )
    offense["Pass EPA/DB"] = df[df["pass_flag"] == 1].groupby("posteam")["epa_value"].mean()
    offense["Rush EPA/Play"] = df[df["rush_flag"] == 1].groupby("posteam")["epa_value"].mean()
    offense["Sack Rate Allowed"] = df[df["pass_flag"] == 1].groupby("posteam")["sack_value"].mean()

    defense = df.groupby("defteam").agg(
        epa_allowed=("epa_value", "mean"),
        success_allowed=("success_value", "mean"),
        explosive_allowed=("explosive_value", "mean"),
        **{"Takeaway Rate": ("turnover_value", "mean")},
    )
    defense["Def EPA Edge"] = league_epa - defense["epa_allowed"]
    defense["Def Success Edge"] = league_success - defense["success_allowed"]
    defense["Explosive Def Edge"] = league_explosive - defense["explosive_allowed"]
    defense["Pass Def EPA Edge"] = league_epa - df[df["pass_flag"] == 1].groupby("defteam")["epa_value"].mean()
    defense["Rush Def EPA Edge"] = league_epa - df[df["rush_flag"] == 1].groupby("defteam")["epa_value"].mean()
    defense["Sack/Pressure Edge"] = df[df["pass_flag"] == 1].groupby("defteam")["sack_value"].mean() - league_sack

    game_col = "game_id" if "game_id" in df.columns else None
    if game_col:
        pace = df.groupby(["posteam", game_col])["valid_play"].sum().groupby("posteam").mean().rename("Pace")
    else:
        pace = pd.Series(64.0, index=offense.index, name="Pace")

    rz_off, rz_def_allowed = _red_zone_rates(df)
    league_rz = float(rz_off.mean()) if not rz_off.empty else 0.56

    metrics = offense.join(defense, how="outer")
    metrics = metrics.join(pace, how="left")
    metrics["Red Zone TD Rate"] = rz_off
    metrics["Red Zone Def Edge"] = league_rz - rz_def_allowed
    metrics = metrics.join(schedule_stats, how="outer")

    for team in teams:
        if team not in metrics.index:
            metrics.loc[team] = np.nan

    defaults = {
        "Off EPA/Play": league_epa,
        "Def EPA Edge": 0.0,
        "Off Success Rate": league_success,
        "Def Success Edge": 0.0,
        "Pass EPA/DB": league_epa,
        "Pass Def EPA Edge": 0.0,
        "Rush EPA/Play": league_epa,
        "Rush Def EPA Edge": 0.0,
        "Explosive Rate": league_explosive,
        "Explosive Def Edge": 0.0,
        "Turnover Rate": league_turnover,
        "Takeaway Rate": league_turnover,
        "Sack Rate Allowed": league_sack,
        "Sack/Pressure Edge": 0.0,
        "Pace": 64.0,
        "Points/Game": 22.5,
        "Points Allowed/Game": 22.5,
        "Red Zone TD Rate": league_rz,
        "Red Zone Def Edge": 0.0,
        "Offensive Plays": 0.0,
        "Games": 0.0,
    }
    for col, fallback in defaults.items():
        if col not in metrics.columns:
            metrics[col] = fallback
        metrics[col] = pd.to_numeric(metrics[col], errors="coerce").fillna(fallback)

    point_diff = metrics["Points/Game"] - metrics["Points Allowed/Game"]
    power = (
        0.48 * point_diff
        + 24.0 * ((metrics["Off EPA/Play"] - league_epa) + metrics["Def EPA Edge"])
        + 8.0 * ((metrics["Off Success Rate"] - league_success) + metrics["Def Success Edge"])
        + 10.0 * ((metrics["Explosive Rate"] - league_explosive) + metrics["Explosive Def Edge"])
        + 8.0 * ((metrics["Takeaway Rate"] - league_turnover) - (metrics["Turnover Rate"] - league_turnover))
    )
    metrics["Power Rating"] = power - power.mean()
    metrics["Data Confidence"] = (
        34 + np.sqrt(metrics["Offensive Plays"].clip(lower=0)) * 2.0 + metrics["Games"].clip(lower=0) * 1.5
    ).clip(lower=35, upper=98)
    metrics = metrics.sort_index()
    metrics.attrs["source"] = "nflverse compact play-by-play"
    return metrics


def _blend_team_metrics(
    prior: pd.DataFrame,
    current: pd.DataFrame,
    season: int,
    projection_week: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    metric_columns = [
        "Power Rating", "Off EPA/Play", "Def EPA Edge", "Off Success Rate", "Def Success Edge",
        "Pass EPA/DB", "Pass Def EPA Edge", "Rush EPA/Play", "Rush Def EPA Edge",
        "Explosive Rate", "Explosive Def Edge", "Turnover Rate", "Takeaway Rate",
        "Sack Rate Allowed", "Sack/Pressure Edge", "Pace", "Points/Game", "Points Allowed/Game",
        "Red Zone TD Rate", "Red Zone Def Edge", "Offensive Plays", "Games", "Data Confidence",
    ]
    teams = sorted(set(NFL_TEAMS) | set(prior.index.tolist() if not prior.empty else []) | set(current.index.tolist() if not current.empty else []))
    for team in teams:
        prior_row = prior.loc[team] if not prior.empty and team in prior.index else pd.Series(dtype=float)
        current_row = current.loc[team] if not current.empty and team in current.index else pd.Series(dtype=float)
        current_games = _num(current_row.get("Games", 0), 0)
        current_weight = _season_weight(projection_week, current_games)
        if current_games <= 0:
            current_weight = 0.0
        previous_weight = 1.0 - current_weight
        row: dict[str, Any] = {
            "Team": team,
            "Season": int(season),
            "Projection Week": int(projection_week),
            "Previous Season Weight": round(previous_weight, 4),
            "Current Season Weight": round(current_weight, 4),
        }
        for col in metric_columns:
            prior_default = _neutral_rating(team, season, projection_week).get(col, 0.0)
            p = _num(prior_row.get(col, prior_default), prior_default)
            c = _num(current_row.get(col, p), p)
            row[col] = round(previous_weight * p + current_weight * c, 6)
        for col in [
            "QB Adjustment", "OL Adjustment", "Skill/Injury Adjustment", "Front Seven Adjustment",
            "Secondary Adjustment", "Special Teams",
        ]:
            row[col] = 0.0
        prior_conf = _num(prior_row.get("Data Confidence", 80), 80)
        current_conf = _num(current_row.get("Data Confidence", 0), 0)
        row["Data Confidence"] = round(clamp(previous_weight * prior_conf + current_weight * current_conf, 35, 98), 1)
        prior_source = str(prior.attrs.get("source", "prior-season source")) if prior is not None else "prior-season source"
        current_source = str(current.attrs.get("source", "current-season source")) if current is not None and not current.empty else "no current-season games"
        row["Source"] = f"{prior_source}; {season - 1}/{season} progressive blend; {current_source}"
        row["Updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        rows.append(row)
    return pd.DataFrame(rows, columns=RATING_COLUMNS)


def _ratings_quality(ratings: pd.DataFrame) -> dict[str, Any]:
    if ratings is None or ratings.empty:
        return {"valid": False, "message": "No ratings were created."}
    power_unique = pd.to_numeric(ratings.get("Power Rating"), errors="coerce").round(4).nunique(dropna=True)
    scoring_unique = pd.to_numeric(ratings.get("Points/Game"), errors="coerce").round(3).nunique(dropna=True)
    epa_unique = pd.to_numeric(ratings.get("Off EPA/Play"), errors="coerce").round(5).nunique(dropna=True)
    valid = len(ratings) >= 30 and power_unique >= 8 and max(scoring_unique, epa_unique) >= 8
    return {
        "valid": bool(valid),
        "teams": int(len(ratings)),
        "unique_power": int(power_unique),
        "unique_scoring": int(scoring_unique),
        "unique_off_epa": int(epa_unique),
        "message": (
            f"{len(ratings)} teams • {power_unique} unique power ratings • "
            f"{scoring_unique} unique scoring rates • {epa_unique} unique offensive EPA values"
        ),
    }


def _build_automated_ratings(season: int, projection_week: int) -> pd.DataFrame:
    prior_season = int(season) - 1
    prior_pbp = _load_pbp_season(prior_season)
    prior_schedule = _schedule_for_season(prior_season)
    prior_metrics = _season_team_metrics(prior_pbp, prior_schedule, through_week=None)

    current_through_week = max(0, int(projection_week) - 1)
    current_pbp = _load_pbp_season(int(season)) if current_through_week > 0 else pd.DataFrame()
    current_schedule = _schedule_for_season(int(season))
    current_metrics = _season_team_metrics(
        current_pbp,
        current_schedule,
        through_week=current_through_week if current_through_week > 0 else 0,
    )
    if current_through_week <= 0:
        current_metrics = pd.DataFrame()
    ratings = _blend_team_metrics(prior_metrics, current_metrics, int(season), int(projection_week))
    quality = _ratings_quality(ratings)
    st.session_state["nfl_last_rating_build"] = {
        "prior_season": prior_season,
        "prior_pbp_rows": int(len(prior_pbp)),
        "prior_schedule_games": int(len(prior_schedule)),
        "prior_source": str(prior_metrics.attrs.get("source", "unknown")),
        "current_season": int(season),
        "current_through_week": int(current_through_week),
        "current_pbp_rows": int(len(current_pbp)),
        "current_schedule_games": int(len(current_schedule)),
        "quality": quality,
    }
    return ratings


# -----------------------------------------------------------------------------
# Player values, depth charts, and injury-aware lineups
# -----------------------------------------------------------------------------

def _player_name_column(df: pd.DataFrame) -> pd.Series:
    return _column(df, "player_display_name", "player_name", "full_name", default="")


def _player_id_column(df: pd.DataFrame) -> pd.Series:
    return _column(df, "player_id", "gsis_id", default="")


def _player_team_column(df: pd.DataFrame) -> pd.Series:
    return _column(df, "recent_team", "team", default="").map(_normalize_team)


def _player_position_column(df: pd.DataFrame) -> pd.Series:
    return _column(df, "position", "position_group", default="").astype(str).str.upper()


def _aggregate_player_stats(df: pd.DataFrame, through_week: int | None = None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "season_type" in out.columns:
        out = out[out["season_type"].astype(str).str.upper() == "REG"].copy()
    if through_week is not None and "week" in out.columns:
        out = out[pd.to_numeric(out["week"], errors="coerce") <= int(through_week)].copy()
    out["player_name_norm"] = _player_name_column(out).map(_normalize_name)
    out["player_name_display"] = _player_name_column(out).astype(str)
    out["player_id_norm"] = _player_id_column(out).astype(str)
    out["team_norm"] = _player_team_column(out)
    out["position_norm"] = _player_position_column(out)
    if out.empty:
        return pd.DataFrame()

    numeric_candidates = [
        "attempts", "passing_epa", "passing_yards", "passing_tds", "interceptions",
        "carries", "rushing_epa", "rushing_yards", "rushing_tds",
        "targets", "receptions", "receiving_epa", "receiving_yards", "receiving_tds",
        "fantasy_points", "fantasy_points_ppr",
    ]
    for col in numeric_candidates:
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    if "week" not in out.columns:
        out["week"] = 1
    key = "player_id_norm"
    out.loc[out[key].isin(["", "nan", "None"]), key] = out["player_name_norm"]
    grouped = out.groupby(key, as_index=False).agg(
        player_name=("player_name_display", "last"),
        player_name_norm=("player_name_norm", "last"),
        team=("team_norm", "last"),
        position=("position_norm", "last"),
        games=("week", "nunique"),
        attempts=("attempts", "sum"),
        passing_epa=("passing_epa", "sum"),
        passing_yards=("passing_yards", "sum"),
        passing_tds=("passing_tds", "sum"),
        interceptions=("interceptions", "sum"),
        carries=("carries", "sum"),
        rushing_epa=("rushing_epa", "sum"),
        rushing_yards=("rushing_yards", "sum"),
        rushing_tds=("rushing_tds", "sum"),
        targets=("targets", "sum"),
        receptions=("receptions", "sum"),
        receiving_epa=("receiving_epa", "sum"),
        receiving_yards=("receiving_yards", "sum"),
        receiving_tds=("receiving_tds", "sum"),
    )
    grouped["total_epa"] = grouped["passing_epa"] + grouped["rushing_epa"] + grouped["receiving_epa"]
    grouped["opportunities"] = grouped["attempts"] + grouped["carries"] + grouped["targets"]
    grouped["epa_per_opportunity"] = grouped["total_epa"] / grouped["opportunities"].replace(0, np.nan)
    grouped["epa_per_game"] = grouped["total_epa"] / grouped["games"].replace(0, np.nan)
    grouped = grouped.fillna(0)
    return grouped


def _position_group(position: str) -> str:
    pos = _safe_text(position).upper()
    if pos == "QB":
        return "QB"
    if pos in ["RB", "HB", "FB"]:
        return "RB"
    if pos in ["WR"]:
        return "WR"
    if pos in ["TE"]:
        return "TE"
    return pos


def _assign_player_values(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    out["group"] = out["position"].map(_position_group)
    out["value_points"] = 0.0
    for group in ["QB", "RB", "WR", "TE"]:
        mask = out["group"] == group
        subset = out.loc[mask]
        if subset.empty:
            continue
        metric = subset["epa_per_game"].astype(float)
        mean = float(metric.mean())
        std = float(metric.std(ddof=0)) or 1.0
        z = (metric - mean) / std
        if group == "QB":
            volume = (subset["attempts"] / subset["games"].replace(0, np.nan)).fillna(0)
            value = 3.2 + 1.25 * z + 0.018 * (volume - 25)
            value = value.clip(1.0, 7.0)
        elif group == "RB":
            volume = ((subset["carries"] + subset["targets"]) / subset["games"].replace(0, np.nan)).fillna(0)
            value = 0.55 + 0.28 * z + 0.018 * (volume - 8)
            value = value.clip(0.15, 1.65)
        elif group == "WR":
            volume = (subset["targets"] / subset["games"].replace(0, np.nan)).fillna(0)
            value = 0.55 + 0.30 * z + 0.035 * (volume - 4)
            value = value.clip(0.15, 1.80)
        else:
            volume = (subset["targets"] / subset["games"].replace(0, np.nan)).fillna(0)
            value = 0.40 + 0.24 * z + 0.025 * (volume - 3)
            value = value.clip(0.10, 1.25)
        out.loc[mask, "value_points"] = value
    return out


def _blend_player_values(season: int, projection_week: int) -> dict[str, float]:
    prior = _assign_player_values(_aggregate_player_stats(_load_player_stats_season(season - 1)))
    current = _assign_player_values(
        _aggregate_player_stats(_load_player_stats_season(season), through_week=max(0, projection_week - 1))
    ) if projection_week > 1 else pd.DataFrame()

    values: dict[str, float] = {}
    prior_map = {
        _normalize_name(row.get("player_name", "")): _num(row.get("value_points", 0), 0)
        for _, row in prior.iterrows()
        if _normalize_name(row.get("player_name", ""))
    }
    current_map = {
        _normalize_name(row.get("player_name", "")): _num(row.get("value_points", 0), 0)
        for _, row in current.iterrows()
        if _normalize_name(row.get("player_name", ""))
    }
    current_weight = _season_weight(projection_week, max(0, projection_week - 1))
    for name in set(prior_map) | set(current_map):
        p = prior_map.get(name, current_map.get(name, 0.0))
        c = current_map.get(name, p)
        values[name] = round((1 - current_weight) * p + current_weight * c, 3)
    return values


def _latest_depth_chart(season: int) -> pd.DataFrame:
    """Return a current depth chart, prioritizing Sleeper for skill roles.

    nflverse retains the more detailed OL/defensive position labels. Sleeper is
    used as the current-team/current-order authority for QB/RB/WR/TE, which is
    especially important for trades and backup-to-starter promotions.
    """
    depth = _load_depth_charts_season(season)
    if depth.empty and season > 2001:
        depth = _load_depth_charts_season(season - 1)

    if depth.empty:
        out = pd.DataFrame(columns=["team_norm", "player_name_display", "pos_norm", "pos_rank_num", "pos_slot_num"] )
    else:
        out = depth.copy()
        out["team_norm"] = _column(out, "team", default="").map(_normalize_team)
        out["player_name_display"] = _column(out, "player_name", "full_name", default="").astype(str)
        out["pos_norm"] = _column(out, "pos_abb", "position", default="").astype(str).str.upper()
        out["pos_rank_num"] = pd.to_numeric(_column(out, "pos_rank", "depth_team", default=99), errors="coerce").fillna(99)
        out["pos_slot_num"] = pd.to_numeric(_column(out, "pos_slot", default=99), errors="coerce").fillna(99)
        if "dt" in out.columns:
            out["dt_parsed"] = pd.to_datetime(out["dt"], errors="coerce", utc=True)
            latest = out.groupby("team_norm")["dt_parsed"].transform("max")
            current = out[(out["dt_parsed"] == latest) | out["dt_parsed"].isna()].copy()
            if not current.empty:
                out = current

    sleeper = _load_sleeper_players()
    if not sleeper.empty:
        skill = sleeper[sleeper["position"].astype(str).str.upper().isin(["QB", "RB", "HB", "FB", "WR", "TE"])].copy()
        if not skill.empty:
            skill_out = pd.DataFrame({
                "team_norm": skill["team"].map(_normalize_team),
                "player_name_display": skill["full_name"].astype(str),
                "pos_norm": skill["position"].astype(str).str.upper().replace({"HB": "RB"}),
                "pos_rank_num": pd.to_numeric(skill["depth_chart_order"], errors="coerce").fillna(99),
                "pos_slot_num": pd.to_numeric(skill["depth_chart_order"], errors="coerce").fillna(99),
                "depth_source": "Sleeper daily players",
            })
            teams = set(skill_out["team_norm"].dropna().astype(str))
            if not out.empty:
                existing_skill = out["pos_norm"].astype(str).str.upper().isin(["QB", "RB", "HB", "FB", "WR", "TE"])
                replace_mask = existing_skill & out["team_norm"].astype(str).isin(teams)
                out = out.loc[~replace_mask].copy()
            out = pd.concat([out, skill_out], ignore_index=True, sort=False)

    if out.empty:
        return out
    out["player_name_display"] = out["player_name_display"].astype(str)
    out["name_norm"] = out["player_name_display"].map(_normalize_name)
    out = out[(out["team_norm"].astype(str).str.len() > 0) & (out["name_norm"].astype(str).str.len() > 0)].copy()
    out = out.sort_values(["team_norm", "pos_norm", "pos_rank_num", "pos_slot_num"])
    return out.drop_duplicates(["team_norm", "name_norm"], keep="first").reset_index(drop=True)


def _injury_lookup(season: int, week: int) -> dict[tuple[str, str], dict[str, Any]]:
    """Merge historical nflverse reports with a daily Sleeper status fallback."""
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    injuries = _load_injuries_season(season)
    if not injuries.empty:
        out = injuries.copy()
        out["team_norm"] = _column(out, "team", default="").map(_normalize_team)
        out["name_norm"] = _column(out, "full_name", "player_name", default="").map(_normalize_name)
        if "week" in out.columns:
            week_values = pd.to_numeric(out["week"], errors="coerce")
            eligible = out[week_values <= int(week)].copy()
            if not eligible.empty:
                out = eligible
                latest_week = out.groupby(["team_norm", "name_norm"])["week"].transform("max")
                out = out[pd.to_numeric(out["week"], errors="coerce") == pd.to_numeric(latest_week, errors="coerce")]
        for _, row in out.iterrows():
            key = (_normalize_team(row.get("team_norm", "")), _normalize_name(row.get("name_norm", "")))
            status = _safe_text(_first_existing(row, "report_status", "practice_status", default="")).upper()
            lookup[key] = {
                "status": status.title() if status else "Healthy",
                "injury": _safe_text(_first_existing(row, "report_primary_injury", "practice_primary_injury", default="")),
                "auto_probability": _status_probability(status),
                "source": "nflverse injury report",
            }

    sleeper = _load_sleeper_players()
    if not sleeper.empty:
        for _, row in sleeper.iterrows():
            team = _normalize_team(row.get("team", ""))
            name = _normalize_name(row.get("full_name", ""))
            if not team or not name:
                continue
            injury_status = _safe_text(row.get("injury_status", ""))
            practice = _safe_text(row.get("practice_participation", ""))
            roster_status = _safe_text(row.get("status", ""))
            status = injury_status or practice or roster_status or "Healthy"
            if roster_status.upper() == "ACTIVE" and not injury_status and not practice:
                status = "Healthy"
            injury = _safe_text(row.get("injury_body_part", "")) or _safe_text(row.get("injury_notes", ""))
            lookup[(team, name)] = {
                "status": status.title(),
                "injury": injury,
                "auto_probability": _status_probability(status),
                "source": "Sleeper daily players",
            }
    return lookup


def _status_probability(status: Any) -> float:
    text = _safe_text(status).upper()
    for key, probability in STATUS_PROBABILITY.items():
        if key and key in text:
            return probability
    return STATUS_PROBABILITY.get(text, 1.0)


def _slot_player(
    depth: pd.DataFrame,
    team: str,
    position_options: list[str],
    occurrence: int,
    used_names: set[str],
) -> tuple[str, str, int]:
    if depth is None or depth.empty:
        return "", position_options[0], occurrence + 1
    team_rows = depth[depth["team_norm"] == _normalize_team(team)].copy()
    if team_rows.empty:
        return "", position_options[0], occurrence + 1
    rows = team_rows[team_rows["pos_norm"].isin([p.upper() for p in position_options])].copy()
    if rows.empty:
        return "", position_options[0], occurrence + 1
    rows = rows.sort_values(["pos_rank_num", "pos_slot_num", "player_name_display"])
    available = rows[~rows["player_name_display"].map(_normalize_name).isin(used_names)]
    if available.empty:
        available = rows
    # Used names already remove earlier selections, so take the best remaining player.
    row = available.iloc[0]
    player = _safe_text(row.get("player_name_display", ""))
    return player, _safe_text(row.get("pos_norm", position_options[0])), _int(row.get("pos_rank_num", occurrence + 1), occurrence + 1)


def _auto_lineup(
    team: str,
    season: int,
    week: int,
    depth: pd.DataFrame,
    injury_lookup: dict[tuple[str, str], dict[str, Any]],
    player_values: dict[str, float],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    used: set[str] = set()
    position_seen: dict[str, int] = {}
    for unit, slot_specs in [("Offense", OFFENSE_SLOTS), ("Defense", DEFENSE_SLOTS)]:
        for slot, positions in slot_specs:
            family = positions[0]
            occurrence = position_seen.get(family, 0)
            player, position, depth_rank = _slot_player(depth, team, positions, occurrence, used)
            position_seen[family] = occurrence + 1
            if player:
                used.add(_normalize_name(player))
            injury = injury_lookup.get((_normalize_team(team), _normalize_name(player)), {})
            status = injury.get("status", "Healthy") if player else "Unknown"
            auto_probability = injury.get("auto_probability", 1.0 if player else 0.75)
            base = POSITION_BASE_IMPACT.get(slot, 0.35)
            player_value = player_values.get(_normalize_name(player), 0.0)
            if slot == "QB" and player_value > 0:
                base = player_value
            elif slot.startswith("RB") and player_value > 0:
                base = max(base, player_value * (1.0 if slot == "RB1" else 0.45))
            elif slot.startswith("WR") and player_value > 0:
                multiplier = {"WR1": 1.0, "WR2": 0.75, "WR3": 0.50}.get(slot, 1.0)
                base = max(base, player_value * multiplier)
            elif slot == "TE" and player_value > 0:
                base = max(base, player_value)
            if depth_rank > 1:
                base *= max(0.45, 1.0 - 0.18 * (depth_rank - 1))
            rows.append({
                "Unit": unit,
                "Slot": slot,
                "Player": player or "TBD",
                "Position": position,
                "Depth Rank": depth_rank,
                "Injury Status": status,
                "Auto Play Probability": round(auto_probability, 2),
                "Manual Play Probability": np.nan,
                "Manual Role Share": np.nan,
                "Base Impact": round(base, 2),
                "Manual Impact": 0.0,
            })
    return pd.DataFrame(rows)


def _finalize_lineup(lineup: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    if lineup is None or lineup.empty:
        return pd.DataFrame(), {"offense_absence": 0.0, "defense_absence": 0.0, "confidence": 30.0}
    out = lineup.copy()
    auto_prob = pd.to_numeric(out.get("Auto Play Probability", 1.0), errors="coerce").fillna(1.0)
    manual_prob = pd.to_numeric(out.get("Manual Play Probability", np.nan), errors="coerce")
    status_prob = out.get("Injury Status", pd.Series("Healthy", index=out.index)).map(_status_probability)
    effective = auto_prob.copy()
    effective = np.minimum(effective, status_prob)
    manual_mask = manual_prob.notna()
    effective.loc[manual_mask] = manual_prob.loc[manual_mask]
    effective = effective.clip(0.0, 1.0)
    base = pd.to_numeric(out.get("Base Impact", 0.0), errors="coerce").fillna(0.0)
    manual_impact = pd.to_numeric(out.get("Manual Impact", 0.0), errors="coerce").fillna(0.0)
    impact = (base + manual_impact).clip(lower=0.0)
    out["Effective Play Probability"] = effective.round(3)
    out["Absence Cost"] = ((1.0 - effective) * impact).round(3)
    offense_absence = float(out.loc[out["Unit"].astype(str) == "Offense", "Absence Cost"].sum())
    defense_absence = float(out.loc[out["Unit"].astype(str) == "Defense", "Absence Cost"].sum())
    player_known = (~out["Player"].astype(str).str.upper().isin(["", "TBD", "UNKNOWN"])).mean()
    certainty = (1.0 - (effective * (1.0 - effective)).mean() * 4.0)
    confidence = clamp(35 + 40 * player_known + 25 * certainty, 25, 100)
    return out, {
        "offense_absence": round(offense_absence, 3),
        "defense_absence": round(defense_absence, 3),
        "confidence": round(float(confidence), 1),
    }


# -----------------------------------------------------------------------------
# Projection, simulation, and grades
# -----------------------------------------------------------------------------

def _weather_adjustment(roof: str, temperature: float, wind: float, precipitation: str) -> float:
    roof_text = _safe_text(roof).lower()
    if roof_text in ["dome", "closed"]:
        return 0.0
    adjustment = 0.0
    if wind >= 12:
        adjustment -= 0.12 * (wind - 10)
    if wind >= 20:
        adjustment -= 1.0
    if temperature <= 32:
        adjustment -= 0.7
    if temperature <= 20:
        adjustment -= 0.8
    precip = _safe_text(precipitation).lower()
    if precip == "rain":
        adjustment -= 0.8
    elif precip == "heavy rain":
        adjustment -= 1.6
    elif precip == "snow":
        adjustment -= 0.7
    elif precip == "heavy snow":
        adjustment -= 1.5
    return round(clamp(adjustment, -5.0, 1.0), 2)


def _rating_adjustments(row: dict[str, Any]) -> tuple[float, float]:
    offense = (
        _num(row.get("QB Adjustment", 0))
        + _num(row.get("OL Adjustment", 0))
        + _num(row.get("Skill/Injury Adjustment", 0))
    )
    defense = _num(row.get("Front Seven Adjustment", 0)) + _num(row.get("Secondary Adjustment", 0))
    return offense, defense


def _project_matchup(
    away: dict[str, Any],
    home: dict[str, Any],
    away_lineup: dict[str, float],
    home_lineup: dict[str, float],
    settings: dict[str, float],
) -> dict[str, float]:
    league_ppg = np.mean([
        _num(away.get("Points/Game", 22.5), 22.5),
        _num(home.get("Points/Game", 22.5), 22.5),
        _num(away.get("Points Allowed/Game", 22.5), 22.5),
        _num(home.get("Points Allowed/Game", 22.5), 22.5),
    ])

    away_base = (
        0.50 * _num(away.get("Points/Game", league_ppg), league_ppg)
        + 0.32 * _num(home.get("Points Allowed/Game", league_ppg), league_ppg)
        + 0.18 * league_ppg
    )
    home_base = (
        0.50 * _num(home.get("Points/Game", league_ppg), league_ppg)
        + 0.32 * _num(away.get("Points Allowed/Game", league_ppg), league_ppg)
        + 0.18 * league_ppg
    )

    away_pass = 13.0 * (_num(away.get("Pass EPA/DB")) - _num(home.get("Pass Def EPA Edge")))
    home_pass = 13.0 * (_num(home.get("Pass EPA/DB")) - _num(away.get("Pass Def EPA Edge")))
    away_rush = 7.5 * (_num(away.get("Rush EPA/Play")) - _num(home.get("Rush Def EPA Edge")))
    home_rush = 7.5 * (_num(home.get("Rush EPA/Play")) - _num(away.get("Rush Def EPA Edge")))

    league_success = 0.43
    away_success = 6.0 * ((_num(away.get("Off Success Rate", league_success)) - league_success) - _num(home.get("Def Success Edge")))
    home_success = 6.0 * ((_num(home.get("Off Success Rate", league_success)) - league_success) - _num(away.get("Def Success Edge")))

    league_explosive = 0.105
    away_explosive = 10.0 * ((_num(away.get("Explosive Rate", league_explosive)) - league_explosive) - _num(home.get("Explosive Def Edge")))
    home_explosive = 10.0 * ((_num(home.get("Explosive Rate", league_explosive)) - league_explosive) - _num(away.get("Explosive Def Edge")))

    league_turnover = 0.024
    away_turnover = -12.0 * ((_num(away.get("Turnover Rate", league_turnover)) - league_turnover) + (_num(home.get("Takeaway Rate", league_turnover)) - league_turnover))
    home_turnover = -12.0 * ((_num(home.get("Turnover Rate", league_turnover)) - league_turnover) + (_num(away.get("Takeaway Rate", league_turnover)) - league_turnover))

    league_sack = 0.067
    away_sack = -8.0 * ((_num(away.get("Sack Rate Allowed", league_sack)) - league_sack) + _num(home.get("Sack/Pressure Edge")))
    home_sack = -8.0 * ((_num(home.get("Sack Rate Allowed", league_sack)) - league_sack) + _num(away.get("Sack/Pressure Edge")))

    away_rating_offense, away_rating_defense = _rating_adjustments(away)
    home_rating_offense, home_rating_defense = _rating_adjustments(home)

    away_points = away_base + away_pass + away_rush + away_success + away_explosive + away_turnover + away_sack
    home_points = home_base + home_pass + home_rush + home_success + home_explosive + home_turnover + home_sack

    away_points += away_rating_offense - home_rating_defense
    home_points += home_rating_offense - away_rating_defense
    away_points += 0.20 * (_num(away.get("Special Teams")) - _num(home.get("Special Teams")))
    home_points += 0.20 * (_num(home.get("Special Teams")) - _num(away.get("Special Teams")))

    away_points -= _num(away_lineup.get("offense_absence"))
    away_points += _num(home_lineup.get("defense_absence"))
    home_points -= _num(home_lineup.get("offense_absence"))
    home_points += _num(away_lineup.get("defense_absence"))

    pace = (_num(away.get("Pace", 64), 64) + _num(home.get("Pace", 64), 64)) / 2
    pace_total_adjustment = float(clamp((pace - 64.0) * 0.34, -4.0, 4.0))
    total_adjustment = (
        pace_total_adjustment
        + settings.get("weather_total_adjustment", 0.0)
        + settings.get("manual_total_adjustment", 0.0)
    )
    away_points += total_adjustment / 2
    home_points += total_adjustment / 2

    home_field = settings.get("home_field", HOME_FIELD_PRIOR_POINTS)
    home_points += home_field / 2
    away_points -= home_field / 2

    rest_edge = settings.get("home_rest_edge", 0.0)
    home_points += rest_edge / 2
    away_points -= rest_edge / 2

    manual_margin = settings.get("manual_home_margin_adjustment", 0.0)
    home_points += manual_margin / 2
    away_points -= manual_margin / 2

    current_margin = home_points - away_points
    target_margin = (
        _num(home.get("Power Rating")) - _num(away.get("Power Rating"))
        + home_field + rest_edge + manual_margin
        + away_lineup.get("offense_absence", 0.0) - home_lineup.get("offense_absence", 0.0)
        + home_lineup.get("defense_absence", 0.0) - away_lineup.get("defense_absence", 0.0)
    )
    margin_correction = 0.38 * (target_margin - current_margin)
    home_points += margin_correction / 2
    away_points -= margin_correction / 2

    away_points = clamp(away_points, 6.0, 48.0)
    home_points = clamp(home_points, 6.0, 48.0)
    projected_total = clamp(away_points + home_points, 25.0, 75.0)
    projected_margin = clamp(home_points - away_points, -31.0, 31.0)
    home_points = (projected_total + projected_margin) / 2
    away_points = projected_total - home_points

    return {
        "away_score": round(float(away_points), 2),
        "home_score": round(float(home_points), 2),
        "margin": round(float(projected_margin), 2),
        "total": round(float(projected_total), 2),
        "pace_adjustment": round(float(clamp(pace_total_adjustment, -4.0, 4.0)), 2),
        "away_pass_matchup": round(float(away_pass), 2),
        "home_pass_matchup": round(float(home_pass), 2),
        "away_rush_matchup": round(float(away_rush), 2),
        "home_rush_matchup": round(float(home_rush), 2),
    }


def _simulation_seed(game_id: str, away: str, home: str, season: int, week: int) -> int:
    raw = f"{MODEL_VERSION}|{game_id}|{away}|{home}|{season}|{week}"
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8], 16)


def _simulate_game(
    projection: dict[str, float],
    home_spread: float,
    market_total: float,
    reliability: float,
    seed: int,
    simulations: int = 20000,
) -> dict[str, float]:
    margin_mean = projection["margin"]
    total_mean = projection["total"]
    margin_sd = 12.8 + (100 - reliability) * 0.035
    total_sd = 10.8 + (100 - reliability) * 0.032
    rng = np.random.default_rng(seed)
    z_margin = rng.standard_normal(simulations)
    z_independent = rng.standard_normal(simulations)
    correlation = 0.08
    z_total = correlation * z_margin + math.sqrt(1 - correlation**2) * z_independent
    margins = margin_mean + margin_sd * z_margin
    totals = total_mean + total_sd * z_total
    home_scores = np.clip((totals + margins) / 2, 0, 65)
    away_scores = np.clip((totals - margins) / 2, 0, 65)
    actual_totals = home_scores + away_scores
    actual_margins = home_scores - away_scores
    return {
        "home_win": float(np.mean(actual_margins > 0)),
        "home_cover": float(np.mean(actual_margins + home_spread > 0)),
        "over": float(np.mean(actual_totals > market_total)),
        "away_low": float(np.quantile(away_scores, 0.20)),
        "away_high": float(np.quantile(away_scores, 0.80)),
        "home_low": float(np.quantile(home_scores, 0.20)),
        "home_high": float(np.quantile(home_scores, 0.80)),
        "margin_sd": margin_sd,
        "total_sd": total_sd,
    }


def _reliability(
    away: dict[str, Any],
    home: dict[str, Any],
    away_lineup: dict[str, float],
    home_lineup: dict[str, float],
    selected_week: int,
    manual_mode: bool,
) -> tuple[float, float, float]:
    data_confidence = (
        _num(away.get("Data Confidence", 40), 40) + _num(home.get("Data Confidence", 40), 40)
    ) / 2
    personnel_confidence = (
        _num(away_lineup.get("confidence", 40), 40) + _num(home_lineup.get("confidence", 40), 40)
    ) / 2
    early_season_penalty = max(0.0, (5 - int(selected_week)) * 2.5)
    manual_penalty = 4.0 if manual_mode else 0.0
    reliability = 0.56 * data_confidence + 0.44 * personnel_confidence - early_season_penalty - manual_penalty
    return (
        round(clamp(reliability, 35, 96), 1),
        round(clamp(data_confidence, 35, 98), 1),
        round(clamp(personnel_confidence, 25, 100), 1),
    )


def _grade_spread(probability: float, edge_points: float, reliability: float, confluence: int) -> str:
    if probability >= 0.585 and edge_points >= 2.5 and reliability >= 70 and confluence >= 4:
        return "A Spread"
    if probability >= 0.555 and edge_points >= 1.5 and reliability >= 62 and confluence >= 3:
        return "B Spread"
    return "Non-Edge Spread"


def _grade_total(probability: float, edge_points: float, reliability: float, confluence: int) -> str:
    if probability >= 0.585 and edge_points >= 3.0 and reliability >= 70 and confluence >= 4:
        return "Strong Over" if probability >= 0.5 else "Strong Under"
    if probability >= 0.555 and edge_points >= 1.75 and reliability >= 62 and confluence >= 3:
        return "Over" if probability >= 0.5 else "Under"
    return "Non-Edge Total"


def _grade_total_direction(selection: str, probability: float, edge_points: float, reliability: float, confluence: int) -> str:
    direction = "Over" if selection.startswith("Over") else "Under"
    if probability >= 0.585 and edge_points >= 3.0 and reliability >= 70 and confluence >= 4:
        return f"Strong {direction}"
    if probability >= 0.555 and edge_points >= 1.75 and reliability >= 62 and confluence >= 3:
        return direction
    return "Non-Edge Total"


def _grade_moneyline(probability: float, price_edge: float, reliability: float, confluence: int) -> str:
    if price_edge >= 0.08 and probability >= 0.57 and reliability >= 72 and confluence >= 4:
        return "A Moneyline"
    if price_edge >= 0.05 and probability >= 0.54 and reliability >= 64 and confluence >= 3:
        return "B Moneyline"
    return "Non-Edge Moneyline"


def _spread_confluence(
    pick_home: bool,
    spread_edge: float,
    away: dict[str, Any],
    home: dict[str, Any],
    away_lineup: dict[str, float],
    home_lineup: dict[str, float],
    reliability: float,
) -> tuple[int, list[str]]:
    checks: list[tuple[bool, str]] = []
    checks.append((spread_edge >= 1.5, "Model edge"))
    power_home = _num(home.get("Power Rating")) > _num(away.get("Power Rating"))
    checks.append((power_home == pick_home, "Power rating"))
    home_qb = _num(home.get("QB Adjustment")) - home_lineup.get("offense_absence", 0)
    away_qb = _num(away.get("QB Adjustment")) - away_lineup.get("offense_absence", 0)
    checks.append(((home_qb >= away_qb) == pick_home, "QB/personnel"))
    home_health = -home_lineup.get("offense_absence", 0) - home_lineup.get("defense_absence", 0)
    away_health = -away_lineup.get("offense_absence", 0) - away_lineup.get("defense_absence", 0)
    checks.append(((home_health >= away_health) == pick_home, "Lineup health"))
    checks.append((reliability >= 62, "Reliability"))
    passed = [label for ok, label in checks if ok]
    return len(passed), passed


def _total_confluence(
    over_pick: bool,
    total_edge: float,
    projection: dict[str, float],
    away: dict[str, Any],
    home: dict[str, Any],
    weather_adjustment: float,
    reliability: float,
) -> tuple[int, list[str]]:
    pace = (_num(away.get("Pace", 64), 64) + _num(home.get("Pace", 64), 64)) / 2
    offense_signal = (
        _num(away.get("Off EPA/Play")) + _num(home.get("Off EPA/Play"))
        - _num(away.get("Def EPA Edge")) - _num(home.get("Def EPA Edge"))
    )
    checks = [
        (total_edge >= 1.75, "Model edge"),
        (((pace >= 64.5) == over_pick), "Pace"),
        (((offense_signal >= 0) == over_pick), "Efficiency matchup"),
        (((weather_adjustment >= -0.5) == over_pick), "Weather/environment"),
        (reliability >= 62, "Reliability"),
    ]
    passed = [label for ok, label in checks if ok]
    return len(passed), passed


def _moneyline_confluence(
    pick_home: bool,
    price_edge: float,
    away: dict[str, Any],
    home: dict[str, Any],
    away_lineup: dict[str, float],
    home_lineup: dict[str, float],
    reliability: float,
) -> tuple[int, list[str]]:
    power_home = _num(home.get("Power Rating")) > _num(away.get("Power Rating"))
    offense_home = _num(home.get("Off EPA/Play")) > _num(away.get("Off EPA/Play"))
    defense_home = _num(home.get("Def EPA Edge")) > _num(away.get("Def EPA Edge"))
    health_home = (
        home_lineup.get("offense_absence", 0) + home_lineup.get("defense_absence", 0)
        <= away_lineup.get("offense_absence", 0) + away_lineup.get("defense_absence", 0)
    )
    checks = [
        (price_edge >= 0.05, "Price edge"),
        (power_home == pick_home, "Power rating"),
        (offense_home == pick_home, "Offense"),
        (defense_home == pick_home, "Defense"),
        (health_home == pick_home, "Lineup health"),
        (reliability >= 64, "Reliability"),
    ]
    passed = [label for ok, label in checks if ok]
    return len(passed), passed


# -----------------------------------------------------------------------------
# Rendering helpers
# -----------------------------------------------------------------------------

def _inject_clean_builder_styles() -> None:
    st.markdown(
        """
        <style>
          .nfl-clean-grid {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:8px 0 12px;}
          .nfl-bubble {border:1px solid rgba(148,163,184,.28);background:rgba(15,23,42,.58);border-radius:16px;padding:13px 14px;min-width:0;}
          .nfl-bubble--wide {grid-column:1/-1;}
          .nfl-bubble-label {font-size:.76rem;letter-spacing:.04em;text-transform:uppercase;opacity:.66;margin-bottom:4px;}
          .nfl-bubble-value {font-size:1.24rem;font-weight:750;line-height:1.18;overflow-wrap:anywhere;}
          .nfl-bubble-sub {font-size:.80rem;opacity:.67;margin-top:5px;line-height:1.35;}
          .nfl-market-card {border:1px solid rgba(148,163,184,.28);background:rgba(15,23,42,.58);border-radius:17px;padding:15px;margin:10px 0;}
          .nfl-market-card--a {border-color:rgba(34,197,94,.62);box-shadow:0 0 0 1px rgba(34,197,94,.09) inset;}
          .nfl-market-card--b {border-color:rgba(250,204,21,.58);box-shadow:0 0 0 1px rgba(250,204,21,.08) inset;}
          .nfl-market-title {font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;opacity:.64;margin-bottom:3px;}
          .nfl-market-projection {font-size:1.28rem;font-weight:760;line-height:1.2;}
          .nfl-grade-row {display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:10px;}
          .nfl-pill {display:inline-flex;border-radius:999px;padding:4px 9px;font-size:.75rem;font-weight:720;border:1px solid rgba(148,163,184,.30);}
          .nfl-pill--a {background:rgba(34,197,94,.16);border-color:rgba(34,197,94,.45);}
          .nfl-pill--b {background:rgba(250,204,21,.14);border-color:rgba(250,204,21,.42);}
          .nfl-graded-pick {font-size:1rem;font-weight:720;margin-top:8px;}
          .nfl-explanation {font-size:.84rem;opacity:.76;line-height:1.42;margin-top:7px;}
          .nfl-player-card {border:1px solid rgba(148,163,184,.25);background:rgba(15,23,42,.48);border-radius:18px;padding:14px;margin:10px 0;}
          .nfl-player-name {font-size:1.08rem;font-weight:760;}
          .nfl-player-meta {font-size:.78rem;opacity:.62;margin-top:2px;}
          .nfl-prop-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:8px;margin-top:11px;width:100%;}
          .nfl-prop-tile {border:1px solid rgba(148,163,184,.20);background:rgba(2,6,23,.32);border-radius:13px;padding:10px;min-width:0;overflow:hidden;}
          .nfl-prop-tile--a {border-color:rgba(34,197,94,.55);}
          .nfl-prop-tile--b {border-color:rgba(250,204,21,.50);}
          .nfl-prop-label {font-size:.73rem;opacity:.64;line-height:1.2;overflow-wrap:anywhere;}
          .nfl-prop-value {font-size:1.12rem;font-weight:760;margin-top:3px;}
          .nfl-prop-grade {font-size:.72rem;font-weight:730;margin-top:7px;}
          .nfl-prop-pick {font-size:.78rem;margin-top:3px;}
          .nfl-prop-why {font-size:.72rem;opacity:.69;line-height:1.32;margin-top:4px;}
          @media (max-width:640px) {
            .nfl-clean-grid {grid-template-columns:repeat(2,minmax(0,1fr));}
            .nfl-prop-grid {grid-template-columns:repeat(2,minmax(0,1fr));}
            .nfl-bubble,.nfl-market-card,.nfl-player-card {padding:12px;}
            .nfl-bubble-value {font-size:1.12rem;}
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _metric_cards(away: str, home: str, projection: dict[str, float], reliability: float) -> None:
    margin_team = home if projection["margin"] >= 0 else away
    margin_value = abs(projection["margin"])
    st.markdown(
        f"""
        <div class="nfl-clean-grid">
          <div class="nfl-bubble"><div class="nfl-bubble-label">{html.escape(away)} score</div><div class="nfl-bubble-value">{projection['away_score']:.1f}</div></div>
          <div class="nfl-bubble"><div class="nfl-bubble-label">{html.escape(home)} score</div><div class="nfl-bubble-value">{projection['home_score']:.1f}</div></div>
          <div class="nfl-bubble"><div class="nfl-bubble-label">Projected margin</div><div class="nfl-bubble-value">{html.escape(margin_team)} by {margin_value:.1f}</div></div>
          <div class="nfl-bubble"><div class="nfl-bubble-label">Projected total</div><div class="nfl-bubble-value">{projection['total']:.1f}</div></div>
          <div class="nfl-bubble nfl-bubble--wide"><div class="nfl-bubble-label">NFL reliability</div><div class="nfl-bubble-value">{reliability:.0f}/100</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _is_graded_game_play(grade: str) -> bool:
    return grade in [
        "A Spread", "B Spread", "A Moneyline", "B Moneyline",
        "Strong Over", "Strong Under", "Over", "Under",
    ]


def _is_graded_prop(grade: str) -> bool:
    return grade in ["A Prop", "B Prop"]


def _market_card(
    title: str, projection_text: str, selection: str, probability: float, edge: str,
    grade: str, confluence: int, reasons: list[str],
) -> None:
    graded = _is_graded_game_play(grade)
    card_class = "nfl-market-card--a" if grade.startswith("A ") or grade.startswith("Strong") else "nfl-market-card--b" if graded else ""
    details = ""
    if graded:
        pill_class = "nfl-pill--a" if grade.startswith("A ") or grade.startswith("Strong") else "nfl-pill--b"
        reason_text = " • ".join(reasons) if reasons else "Model thresholds and reliability support the play"
        details = f"""
          <div class="nfl-grade-row"><span class="nfl-pill {pill_class}">{html.escape(grade)}</span><span class="nfl-pill">{confluence}/5+ confluence</span></div>
          <div class="nfl-graded-pick">Bet: {html.escape(selection)}</div>
          <div class="nfl-explanation">{probability:.1%} model probability • {html.escape(edge)} edge<br>{html.escape(reason_text)}</div>
        """
    st.markdown(
        f"""
        <div class="nfl-market-card {card_class}">
          <div class="nfl-market-title">{html.escape(title)} projection</div>
          <div class="nfl-market-projection">{html.escape(projection_text)}</div>
          {details}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_prop_projection_cards(evaluated: pd.DataFrame) -> None:
    """Render compact player cards without Markdown interpreting nested HTML as code.

    Streamlit can treat indented HTML after blank lines as a Markdown code block.
    Build each card as one compact, non-indented HTML string so mobile browsers
    consistently render every projection as a tile instead of showing raw tags.
    """
    if evaluated is None or evaluated.empty:
        return
    teams = list(dict.fromkeys(evaluated["Team"].astype(str).tolist()))
    tabs = st.tabs(teams) if len(teams) > 1 else [st.container()]
    slot_order = {"QB": 0, "RB1": 1, "RB2": 2, "WR1": 3, "WR2": 4, "WR3": 5, "TE": 6}
    market_order = {
        "Passing Attempts": 0, "Passing Completions": 1, "Passing Yards": 2, "Passing TDs": 3, "Interceptions": 4,
        "Rushing Attempts": 5, "Rushing Yards": 6,
        "Targets": 7, "Receptions": 8, "Receiving Yards": 9,
    }
    for tab, team in zip(tabs, teams):
        with tab:
            team_rows = evaluated[evaluated["Team"].astype(str) == team].copy()
            players = (
                team_rows[["Player", "Slot"]].drop_duplicates()
                .assign(_order=lambda frame: frame["Slot"].map(slot_order).fillna(99))
                .sort_values(["_order", "Player"])["Player"].tolist()
            )
            for player in players:
                player_rows = team_rows[team_rows["Player"].astype(str) == str(player)].copy()
                player_rows["_market_order"] = player_rows["Market"].map(market_order).fillna(99)
                player_rows = player_rows.sort_values("_market_order")
                position = _safe_text(player_rows.iloc[0].get("Position", ""))
                slot = _safe_text(player_rows.iloc[0].get("Slot", ""))
                tiles: list[str] = []
                for _, row in player_rows.iterrows():
                    grade = _safe_text(row.get("Grade", ""))
                    graded = _is_graded_prop(grade)
                    tile_class = "nfl-prop-tile--a" if grade == "A Prop" else "nfl-prop-tile--b" if grade == "B Prop" else ""
                    graded_html = ""
                    if graded:
                        probability = _num(row.get("Model Probability", 0), 0)
                        edge_value = _num(row.get("Probability Edge", 0), 0)
                        graded_html = (
                            f'<div class="nfl-prop-grade">{html.escape(grade)}</div>'
                            f'<div class="nfl-prop-pick">{html.escape(_safe_text(row.get("Pick", "")))} • {probability:.1%}</div>'
                            f'<div class="nfl-prop-why">Edge {edge_value:+.1%} • {html.escape(_safe_text(row.get("Confluence", "")))}</div>'
                        )
                    tiles.append(
                        f'<div class="nfl-prop-tile {tile_class}">'
                        f'<div class="nfl-prop-label">{html.escape(_safe_text(row.get("Market", "")))}</div>'
                        f'<div class="nfl-prop-value">{_num(row.get("Projection", 0), 0):.1f}</div>'
                        f'{graded_html}</div>'
                    )
                card_html = (
                    '<div class="nfl-player-card">'
                    f'<div class="nfl-player-name">{html.escape(str(player))}</div>'
                    f'<div class="nfl-player-meta">{html.escape(team)} • {html.escape(slot or position)}</div>'
                    f'<div class="nfl-prop-grid">{"".join(tiles)}</div>'
                    '</div>'
                )
                st.markdown(card_html, unsafe_allow_html=True)


def _team_adjustment_inputs(prefix: str, row: dict[str, Any]) -> dict[str, Any]:
    st.markdown(f"**{prefix} manual rating overrides**")
    row["QB Adjustment"] = st.number_input(
        f"{prefix} QB adjustment", value=_num(row.get("QB Adjustment", 0)), step=0.25,
        key=f"nfl_{prefix}_qb_adjustment",
        help="Points added or removed for QB quality beyond the automated team/player prior.",
    )
    row["OL Adjustment"] = st.number_input(
        f"{prefix} offensive-line adjustment", value=_num(row.get("OL Adjustment", 0)), step=0.25,
        key=f"nfl_{prefix}_ol_adjustment",
    )
    row["Skill/Injury Adjustment"] = st.number_input(
        f"{prefix} skill-position adjustment", value=_num(row.get("Skill/Injury Adjustment", 0)), step=0.25,
        key=f"nfl_{prefix}_skill_adjustment",
    )
    row["Front Seven Adjustment"] = st.number_input(
        f"{prefix} front-seven adjustment", value=_num(row.get("Front Seven Adjustment", 0)), step=0.25,
        key=f"nfl_{prefix}_front_adjustment",
    )
    row["Secondary Adjustment"] = st.number_input(
        f"{prefix} secondary adjustment", value=_num(row.get("Secondary Adjustment", 0)), step=0.25,
        key=f"nfl_{prefix}_secondary_adjustment",
    )
    row["Special Teams"] = st.number_input(
        f"{prefix} special-teams adjustment", value=_num(row.get("Special Teams", 0)), step=0.10,
        key=f"nfl_{prefix}_st_adjustment",
    )
    return row


def _lineup_editor(
    team: str,
    season: int,
    week: int,
    game_key: str,
    depth: pd.DataFrame,
    injury_lookup: dict[tuple[str, str], dict[str, Any]],
    player_values: dict[str, float],
    seed_lineup: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    lineup = seed_lineup.copy() if seed_lineup is not None and not seed_lineup.empty else _auto_lineup(
        team, season, week, depth, injury_lookup, player_values
    )
    if "Manual Role Share" not in lineup.columns:
        lineup["Manual Role Share"] = np.nan
    visible_columns = [
        "Unit", "Slot", "Player", "Position", "Injury Status",
        "Manual Play Probability", "Manual Role Share", "Manual Impact",
    ]
    edited = st.data_editor(
        lineup,
        use_container_width=True,
        hide_index=True,
        key=f"nfl_lineup_{game_key}_{team}",
        column_order=visible_columns,
        disabled=["Unit", "Slot"],
        column_config={
            "Unit": st.column_config.TextColumn("Unit", disabled=True, width="small"),
            "Slot": st.column_config.TextColumn("Slot", disabled=True, width="small"),
            "Player": st.column_config.TextColumn("Player", width="medium"),
            "Position": st.column_config.TextColumn("Pos", width="small"),
            "Injury Status": st.column_config.SelectboxColumn(
                "Status",
                options=["Healthy", "Active", "Full", "Limited", "Questionable", "Doubtful", "Out", "IR", "PUP", "Unknown"],
                width="medium",
            ),
            "Manual Play Probability": st.column_config.NumberColumn(
                "Play % override", min_value=0.0, max_value=1.0, step=0.05, format="%.2f",
                help="Optional. Leave blank to use the injury-report probability.",
            ),
            "Manual Role Share": st.column_config.NumberColumn(
                "Role % override", min_value=0.0, max_value=1.0, step=0.01, format="%.2f",
                help="Optional. QB = team pass-attempt share; RB = team rush share; WR/TE = team target share.",
            ),
            "Manual Impact": st.column_config.NumberColumn(
                "Extra game pts", min_value=-3.0, max_value=5.0, step=0.05, format="%.2f",
            ),
        },
    )
    final, summary = _finalize_lineup(edited)
    st.caption(
        f"Absence adjustment: offense −{summary['offense_absence']:.2f}; "
        f"defense +{summary['defense_absence']:.2f} opponent points • personnel confidence {summary['confidence']:.0f}/100. "
        "Role % is optional; the model otherwise reallocates work from the current depth-chart slot."
    )
    return final, summary



def _is_neutral_schedule_row(row: pd.Series | dict[str, Any] | None) -> bool:
    if row is None:
        return False
    location = _safe_text(row.get("Location", "")).lower()
    game_type = _safe_text(row.get("Game Type", "")).upper()
    return "neutral" in location or game_type == "SB"


@st.cache_data(ttl=21600, show_spinner=False)
def _rolling_home_field_model(season: int, projection_week: int) -> dict[str, Any]:
    """Estimate modern NFL home-field value from recent completed games.

    A weighted ridge regression separates the league home indicator from team
    strength (home-team coefficient minus away-team coefficient). The league
    estimate is shrunk toward a conservative prior, and small home-team/venue
    residuals are heavily shrunk so one unusual season cannot dominate.
    """
    season = int(season)
    projection_week = max(1, int(projection_week))
    records: list[dict[str, Any]] = []
    first_season = max(1999, season - HOME_FIELD_LOOKBACK_SEASONS)

    for data_season in range(first_season, season + 1):
        schedule = _schedule_for_season(data_season)
        if schedule is None or schedule.empty:
            continue
        frame = schedule.copy()
        if "Game Type" in frame.columns:
            frame = frame[frame["Game Type"].astype(str).str.upper() == "REG"].copy()
        if data_season == season and "Week" in frame.columns:
            frame = frame[pd.to_numeric(frame["Week"], errors="coerce") < projection_week].copy()
        frame["_home_score"] = pd.to_numeric(frame.get("Home Score"), errors="coerce")
        frame["_away_score"] = pd.to_numeric(frame.get("Away Score"), errors="coerce")
        frame = frame.dropna(subset=["_home_score", "_away_score"])
        if frame.empty:
            continue
        age = max(0, season - data_season)
        season_weight = 0.78 ** age
        for _, game in frame.iterrows():
            home_team = _normalize_team(game.get("Home Team", ""))
            away_team = _normalize_team(game.get("Away Team", ""))
            if not home_team or not away_team or home_team == away_team:
                continue
            records.append({
                "season": data_season,
                "home_team": home_team,
                "away_team": away_team,
                "home_indicator": 0.0 if _is_neutral_schedule_row(game) else 1.0,
                "margin": _num(game.get("_home_score"), 0) - _num(game.get("_away_score"), 0),
                "weight": season_weight,
            })

    fallback = {
        "league_points": HOME_FIELD_PRIOR_POINTS,
        "raw_league_points": HOME_FIELD_PRIOR_POINTS,
        "team_adjustments": {},
        "games": 0,
        "effective_games": 0.0,
        "rmse": 13.5,
        "confidence": 40.0,
        "seasons": "fallback prior",
    }
    if len(records) < 96:
        return fallback

    teams = sorted({record["home_team"] for record in records} | {record["away_team"] for record in records})
    team_index = {team: idx for idx, team in enumerate(teams)}
    n_games = len(records)
    design = np.zeros((n_games, 1 + len(teams)), dtype=float)
    outcome = np.zeros(n_games, dtype=float)
    weights = np.ones(n_games, dtype=float)

    for idx, record in enumerate(records):
        design[idx, 0] = record["home_indicator"]
        design[idx, 1 + team_index[record["home_team"]]] = 1.0
        design[idx, 1 + team_index[record["away_team"]]] = -1.0
        outcome[idx] = record["margin"]
        weights[idx] = record["weight"]

    root_weight = np.sqrt(np.clip(weights, 0.05, None))
    weighted_design = design * root_weight[:, None]
    weighted_outcome = outcome * root_weight
    penalty = np.eye(design.shape[1], dtype=float) * 12.0
    penalty[0, 0] = 0.35
    try:
        beta = np.linalg.solve(weighted_design.T @ weighted_design + penalty, weighted_design.T @ weighted_outcome)
    except np.linalg.LinAlgError:
        beta = np.linalg.lstsq(weighted_design.T @ weighted_design + penalty, weighted_design.T @ weighted_outcome, rcond=None)[0]

    effective_games = float(weights.sum())
    raw_home_field = float(clamp(beta[0], -0.5, 4.0))
    league_points = (
        raw_home_field * effective_games + HOME_FIELD_PRIOR_POINTS * HOME_FIELD_PRIOR_GAMES
    ) / (effective_games + HOME_FIELD_PRIOR_GAMES)
    league_points = float(clamp(league_points, 0.5, 3.0))

    strength_component = design[:, 1:] @ beta[1:]
    venue_residual = outcome - (league_points * design[:, 0] + strength_component)
    team_adjustments: dict[str, float] = {}
    for team in teams:
        mask = np.array([
            record["home_team"] == team and record["home_indicator"] > 0.5
            for record in records
        ], dtype=bool)
        if not mask.any():
            continue
        team_weight = weights[mask]
        weighted_residual = float(np.sum(team_weight * venue_residual[mask]))
        # Roughly two seasons of home games are required before a venue effect
        # can materially move the league baseline.
        adjustment = weighted_residual / (float(team_weight.sum()) + 18.0)
        team_adjustments[team] = round(float(clamp(adjustment, -0.45, 0.45)), 3)

    fitted = league_points * design[:, 0] + strength_component
    rmse = math.sqrt(float(np.average((outcome - fitted) ** 2, weights=weights)))
    confidence = float(clamp(45.0 + 1.35 * math.sqrt(effective_games), 45.0, 98.0))
    used_seasons = sorted({record["season"] for record in records})
    return {
        "league_points": round(league_points, 3),
        "raw_league_points": round(raw_home_field, 3),
        "team_adjustments": team_adjustments,
        "games": n_games,
        "effective_games": round(effective_games, 1),
        "rmse": round(rmse, 2),
        "confidence": round(confidence, 1),
        "seasons": f"{used_seasons[0]}–{used_seasons[-1]}" if used_seasons else "fallback prior",
    }


def _automatic_home_field_advantage(
    season: int,
    projection_week: int,
    home_team: str,
    schedule_row: pd.Series | dict[str, Any] | None,
) -> dict[str, Any]:
    if _is_neutral_schedule_row(schedule_row):
        return {
            "value": 0.0, "league_points": 0.0, "venue_adjustment": 0.0,
            "games": 0, "confidence": 100.0, "source": "Neutral-site game",
        }
    model = _rolling_home_field_model(int(season), int(projection_week))
    league_points = _num(model.get("league_points"), HOME_FIELD_PRIOR_POINTS)
    venue_adjustment = _num(model.get("team_adjustments", {}).get(_normalize_team(home_team), 0.0), 0.0)
    value = float(clamp(league_points + venue_adjustment, 0.5, 3.0))
    source = (
        f"Rolling {model.get('seasons', 'recent')} ridge model • "
        f"{_int(model.get('games', 0), 0):,} completed games • "
        f"league {league_points:.2f} + venue {venue_adjustment:+.2f}"
    )
    return {
        "value": round(value, 2),
        "league_points": round(league_points, 2),
        "venue_adjustment": round(venue_adjustment, 2),
        "games": _int(model.get("games", 0), 0),
        "confidence": _num(model.get("confidence"), 40.0),
        "source": source,
    }


def _schedule_defaults(row: pd.Series | None) -> dict[str, Any]:
    if row is None:
        return {
            "away_ml": 110, "home_ml": -130, "home_spread": -2.5, "total": 44.5,
            "roof": "outdoors", "temperature": 70.0, "wind": 6.0,
            "away_rest": 7.0, "home_rest": 7.0, "game_id": "test", "home_field": HOME_FIELD_PRIOR_POINTS,
        }
    spread_line = _num(row.get("Spread Line", 2.5), 2.5)
    # nflverse uses a positive number when the home team is favored; betting notation is the inverse.
    home_spread = -spread_line
    neutral_site = _safe_text(row.get("Location", "")).lower() == "neutral"
    return {
        "away_ml": _int(row.get("Away ML", 110), 110),
        "home_ml": _int(row.get("Home ML", -130), -130),
        "home_spread": home_spread,
        "total": _num(row.get("Total Line", 44.5), 44.5),
        "roof": _safe_text(row.get("Roof", "outdoors")) or "outdoors",
        "temperature": _num(row.get("Temperature", 70), 70),
        "wind": _num(row.get("Wind", 6), 6),
        "away_rest": _num(row.get("Away Rest", 7), 7),
        "home_rest": _num(row.get("Home Rest", 7), 7),
        "game_id": _safe_text(row.get("Game ID", "test")) or "test",
        "home_field": 0.0 if neutral_site else HOME_FIELD_PRIOR_POINTS,
    }


def _save_lineups(
    away_lineup: pd.DataFrame,
    home_lineup: pd.DataFrame,
    away_team: str,
    home_team: str,
    season: int,
    week: int,
    game_id: str,
) -> None:
    rows = []
    for team, frame in [(away_team, away_lineup), (home_team, home_lineup)]:
        for _, row in frame.iterrows():
            rows.append({
                "Date": str(date.today()), "Season": season, "Week": week, "Game ID": game_id,
                "Team": team, "Unit": row.get("Unit", ""), "Slot": row.get("Slot", ""),
                "Player": row.get("Player", ""), "Position": row.get("Position", ""),
                "Depth Rank": row.get("Depth Rank", ""), "Injury Status": row.get("Injury Status", ""),
                "Auto Play Probability": row.get("Auto Play Probability", ""),
                "Manual Play Probability": row.get("Manual Play Probability", ""),
                "Manual Role Share": row.get("Manual Role Share", ""),
                "Base Impact": row.get("Base Impact", ""), "Manual Impact": row.get("Manual Impact", ""),
                "Effective Play Probability": row.get("Effective Play Probability", ""),
                "Absence Cost": row.get("Absence Cost", ""), "Model Version": MODEL_VERSION,
            })
    if rows:
        existing = read_sheet(LINEUP_TAB, LINEUP_COLUMNS)
        if existing is None or existing.empty:
            output = pd.DataFrame(rows, columns=LINEUP_COLUMNS)
        else:
            existing = existing.copy()
            mask = ~(
                (existing["Game ID"].astype(str) == str(game_id))
                & (pd.to_numeric(existing["Season"], errors="coerce") == int(season))
                & (pd.to_numeric(existing["Week"], errors="coerce") == int(week))
            )
            output = pd.concat([existing[mask], pd.DataFrame(rows)], ignore_index=True)
        write_sheet(LINEUP_TAB, output, LINEUP_COLUMNS)


# -----------------------------------------------------------------------------
# Automatic slate initialization and player-prop engine
# -----------------------------------------------------------------------------

NFL_TEAM_NAMES = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}

PROP_MARKET_API_KEYS = {
    "Passing Attempts": "player_pass_attempts",
    "Passing Completions": "player_pass_completions",
    "Passing Yards": "player_pass_yds",
    "Passing TDs": "player_pass_tds",
    "Interceptions": "player_pass_interceptions",
    "Rushing Attempts": "player_rush_attempts",
    "Rushing Yards": "player_rush_yds",
    "Receptions": "player_receptions",
    "Receiving Yards": "player_reception_yds",
}
API_KEY_TO_PROP_MARKET = {value: key for key, value in PROP_MARKET_API_KEYS.items()}

POSITION_EFFICIENCY_PRIORS = {
    "QB": {"completion_rate": 0.645, "pass_ypa": 7.05, "rush_ypc": 4.6},
    "RB": {"catch_rate": 0.755, "rush_ypc": 4.25, "yards_per_target": 6.15, "targets_per_route": 0.235},
    "WR": {"catch_rate": 0.635, "yards_per_target": 8.15, "targets_per_route": 0.205},
    "TE": {"catch_rate": 0.680, "yards_per_target": 7.45, "targets_per_route": 0.190},
}


def _current_nfl_season() -> int:
    try:
        if nfl is not None and hasattr(nfl, "get_current_season"):
            return int(nfl.get_current_season())
    except Exception:
        pass
    today = date.today()
    return today.year - 1 if today.month <= 2 else today.year


def _schedule_date_series(schedule: pd.DataFrame) -> pd.Series:
    if schedule is None or schedule.empty:
        return pd.Series(dtype="datetime64[ns]")
    return pd.to_datetime(schedule.get("Game Date", pd.Series(index=schedule.index, dtype=str)), errors="coerce").dt.date


def _default_slate_date(schedule: pd.DataFrame, today: date | None = None) -> date | None:
    if schedule is None or schedule.empty:
        return None
    today = today or date.today()
    dates = _schedule_date_series(schedule).dropna()
    if dates.empty:
        return None
    unique_dates = sorted(set(dates.tolist()))
    if today in unique_dates:
        return today
    future = [game_date for game_date in unique_dates if game_date >= today]
    return future[0] if future else unique_dates[-1]


def _available_slate_dates(schedule: pd.DataFrame) -> list[date]:
    dates = _schedule_date_series(schedule).dropna()
    return sorted(set(dates.tolist()))


def _ratings_match_context(ratings: pd.DataFrame, season: int, week: int) -> bool:
    if ratings is None or ratings.empty:
        return False
    season_values = pd.to_numeric(ratings.get("Season"), errors="coerce")
    week_values = pd.to_numeric(ratings.get("Projection Week"), errors="coerce")
    context = ratings[(season_values == int(season)) & (week_values == int(week))]
    return bool(not context.empty and _ratings_quality(context).get("valid"))


def _ensure_automated_ratings(season: int, week: int) -> pd.DataFrame:
    session_key = f"nfl_auto_ratings_{int(season)}_{int(week)}"
    cached = st.session_state.get(session_key)
    if isinstance(cached, pd.DataFrame) and not cached.empty:
        return cached

    saved = _load_ratings()
    if _ratings_match_context(saved, season, week):
        context = saved[
            (pd.to_numeric(saved["Season"], errors="coerce") == int(season))
            & (pd.to_numeric(saved["Projection Week"], errors="coerce") == int(week))
        ].copy()
        st.session_state[session_key] = context
        return context

    try:
        with st.spinner("Initializing NFL schedule, play-by-play and progressive team ratings..."):
            built = _build_automated_ratings(int(season), int(week))
        quality = _ratings_quality(built)
        if quality.get("valid"):
            st.session_state[session_key] = built
            if sheets_ready():
                write_sheet(RATINGS_TAB, built, RATING_COLUMNS)
            return built
        st.warning("Automated team data loaded, but validation found limited team separation. The best saved ratings are being used.")
    except Exception as exc:
        st.warning(f"Automated ratings could not fully refresh; using the best available saved data: {exc}")

    if saved is not None and not saved.empty:
        st.session_state[session_key] = saved
        return saved
    neutral = _seed_neutral_ratings(int(season), int(week))
    st.session_state[session_key] = neutral
    return neutral


def _numeric_frame_column(df: pd.DataFrame, name: str) -> pd.Series:
    return pd.to_numeric(df[name], errors="coerce").fillna(0.0) if name in df.columns else pd.Series(0.0, index=df.index)



def _bool_numeric(series: pd.Series) -> pd.Series:
    if series is None:
        return pd.Series(dtype=float)
    if pd.api.types.is_bool_dtype(series):
        return series.astype(float)
    text = series.astype(str).str.strip().str.lower()
    mapped = text.map({"true": 1.0, "false": 0.0, "1": 1.0, "0": 0.0, "yes": 1.0, "no": 0.0})
    numeric = pd.to_numeric(series, errors="coerce")
    return mapped.fillna(numeric).fillna(0.0)


@st.cache_data(ttl=21600, show_spinner=False)
def _season_charting_features(season: int, through_week: int | None = None) -> dict[str, pd.DataFrame]:
    """Create compact player charting and defensive scheme profiles.

    FTN provides in-season charting. Participation coverage is historical and is
    used when available; missing live coverage is handled by shrinkage rather than
    fabricated precision.
    """
    pbp = _load_pbp_season(int(season))
    if pbp is None or pbp.empty:
        return {"players": pd.DataFrame(), "defenses": pd.DataFrame()}
    df = pbp.copy()
    if "season_type" in df.columns:
        df = df[df["season_type"].astype(str).str.upper() == "REG"].copy()
    if through_week is not None and "week" in df.columns:
        df = df[pd.to_numeric(df["week"], errors="coerce") <= int(through_week)].copy()
    if df.empty:
        return {"players": pd.DataFrame(), "defenses": pd.DataFrame()}
    df["game_key"] = _column(df, "game_id", default="").astype(str)
    df["play_key"] = pd.to_numeric(_column(df, "play_id", default=np.nan), errors="coerce")
    df["posteam"] = _column(df, "posteam", default="").map(_normalize_team)
    df["defteam"] = _column(df, "defteam", default="").map(_normalize_team)
    df["pass_flag"] = (_numeric_frame_column(df, "pass_attempt") > 0).astype(float)
    df["rush_flag"] = (_numeric_frame_column(df, "rush_attempt") > 0).astype(float)
    df["complete_flag"] = (_numeric_frame_column(df, "complete_pass") > 0).astype(float)
    df["sack_flag"] = (_numeric_frame_column(df, "sack") > 0).astype(float)
    df["hit_flag"] = (_numeric_frame_column(df, "qb_hit") > 0).astype(float)
    df["scramble_flag"] = (_numeric_frame_column(df, "qb_scramble") > 0).astype(float)
    df["yards"] = _numeric_frame_column(df, "yards_gained")
    df["air"] = pd.to_numeric(_column(df, "air_yards", default=np.nan), errors="coerce")
    df["epa_value"] = pd.to_numeric(_column(df, "epa", default=np.nan), errors="coerce")
    df["passer_norm"] = _column(df, "passer_player_name", default="").map(_normalize_name)
    df["receiver_norm"] = _column(df, "receiver_player_name", default="").map(_normalize_name)
    df["rusher_norm"] = _column(df, "rusher_player_name", default="").map(_normalize_name)

    ftn = _load_ftn_charting_season(int(season))
    if ftn is not None and not ftn.empty:
        fc = ftn.copy()
        if through_week is not None and "week" in fc.columns:
            fc = fc[pd.to_numeric(fc["week"], errors="coerce") <= int(through_week)].copy()
        fc["game_key"] = _column(fc, "nflverse_game_id", default="").astype(str)
        fc["play_key"] = pd.to_numeric(_column(fc, "nflverse_play_id", default=np.nan), errors="coerce")
        keep = ["game_key", "play_key"] + [c for c in FTN_REQUIRED_COLUMNS if c in fc.columns and c not in ["nflverse_game_id", "nflverse_play_id", "season", "week"]]
        fc = fc[keep].drop_duplicates(["game_key", "play_key"])
        df = df.merge(fc, on=["game_key", "play_key"], how="left")

    part = _load_participation_season(int(season))
    if part is not None and not part.empty:
        pc = part.copy()
        pc["game_key"] = _column(pc, "nflverse_game_id", default="").astype(str)
        pc["play_key"] = pd.to_numeric(_column(pc, "play_id", default=np.nan), errors="coerce")
        keep = ["game_key", "play_key"] + [c for c in PARTICIPATION_REQUIRED_COLUMNS if c in pc.columns and c not in ["nflverse_game_id", "play_id", "possession_team"]]
        pc = pc[keep].drop_duplicates(["game_key", "play_key"])
        df = df.merge(pc, on=["game_key", "play_key"], how="left")

    for col in ["is_interception_worthy", "is_throw_away", "is_catchable_ball", "is_contested_ball", "is_created_reception", "is_drop", "is_qb_fault_sack", "was_pressure"]:
        if col in df.columns:
            df[col] = _bool_numeric(df[col])
        else:
            df[col] = 0.0
    df["blitz_flag"] = (pd.to_numeric(_column(df, "n_blitzers", default=0), errors="coerce").fillna(0) >= 5).astype(float)
    df["pressure_flag"] = np.maximum.reduce([df["hit_flag"].to_numpy(), df["sack_flag"].to_numpy(), df["was_pressure"].to_numpy()])
    man_zone = _column(df, "defense_man_zone_type", default="").astype(str).str.upper()
    df["man_flag"] = man_zone.str.contains("MAN", na=False).astype(float)
    df["zone_flag"] = man_zone.str.contains("ZONE", na=False).astype(float)
    df["deep_flag"] = (df["air"] >= 15).fillna(False).astype(float)

    player_frames = []
    rec = df[(df["pass_flag"] > 0) & (df["receiver_norm"].str.len() > 0)].copy()
    if not rec.empty:
        rec["man_yards"] = rec["yards"] * rec["man_flag"]
        rec["zone_yards"] = rec["yards"] * rec["zone_flag"]
        rec["man_catches"] = rec["complete_flag"] * rec["man_flag"]
        rec["zone_catches"] = rec["complete_flag"] * rec["zone_flag"]
        rg = rec.groupby("receiver_norm", as_index=False).agg(
            chart_targets=("pass_flag", "sum"), catchable_targets=("is_catchable_ball", "sum"),
            contested_targets=("is_contested_ball", "sum"), drops=("is_drop", "sum"),
            created_receptions=("is_created_reception", "sum"), deep_targets=("deep_flag", "sum"),
            man_targets=("man_flag", "sum"), zone_targets=("zone_flag", "sum"),
            man_yards=("man_yards", "sum"), zone_yards=("zone_yards", "sum"),
            man_catches=("man_catches", "sum"), zone_catches=("zone_catches", "sum"),
        ).rename(columns={"receiver_norm": "player_name_norm"})
        rg["catchable_target_rate"] = rg["catchable_targets"] / rg["chart_targets"].replace(0, np.nan)
        rg["contested_target_rate"] = rg["contested_targets"] / rg["chart_targets"].replace(0, np.nan)
        rg["drop_rate"] = rg["drops"] / rg["catchable_targets"].replace(0, np.nan)
        rg["created_reception_rate"] = rg["created_receptions"] / rg["chart_targets"].replace(0, np.nan)
        rg["deep_target_rate"] = rg["deep_targets"] / rg["chart_targets"].replace(0, np.nan)
        rg["man_ypt"] = rg["man_yards"] / rg["man_targets"].replace(0, np.nan)
        rg["zone_ypt"] = rg["zone_yards"] / rg["zone_targets"].replace(0, np.nan)
        rg["man_catch_rate"] = rg["man_catches"] / rg["man_targets"].replace(0, np.nan)
        rg["zone_catch_rate"] = rg["zone_catches"] / rg["zone_targets"].replace(0, np.nan)
        player_frames.append(rg)

    qb = df[(df["pass_flag"] > 0) & (df["passer_norm"].str.len() > 0)].copy()
    if not qb.empty:
        qg = qb.groupby("passer_norm", as_index=False).agg(
            chart_pass_attempts=("pass_flag", "sum"), interception_worthy=("is_interception_worthy", "sum"),
            throwaways=("is_throw_away", "sum"), qb_fault_sacks=("is_qb_fault_sack", "sum"),
            pressured_dropbacks=("pressure_flag", "sum"), deep_attempts=("deep_flag", "sum"),
        ).rename(columns={"passer_norm": "player_name_norm"})
        qg["interception_worthy_rate"] = qg["interception_worthy"] / qg["chart_pass_attempts"].replace(0, np.nan)
        qg["throwaway_rate"] = qg["throwaways"] / qg["chart_pass_attempts"].replace(0, np.nan)
        qg["qb_fault_sack_rate"] = qg["qb_fault_sacks"] / qg["chart_pass_attempts"].replace(0, np.nan)
        qg["pressure_rate_faced"] = qg["pressured_dropbacks"] / qg["chart_pass_attempts"].replace(0, np.nan)
        qg["deep_attempt_rate"] = qg["deep_attempts"] / qg["chart_pass_attempts"].replace(0, np.nan)
        player_frames.append(qg)

    rush = df[(df["rush_flag"] > 0) & (df["rusher_norm"].str.len() > 0)].copy()
    if not rush.empty:
        rush["explosive_rush"] = (rush["yards"] >= 10).astype(float)
        rush["stuffed_rush"] = (rush["yards"] <= 0).astype(float)
        rush["outside_rush"] = _column(rush, "run_location", default="").astype(str).str.lower().isin(["left", "right"]).astype(float)
        ug = rush.groupby("rusher_norm", as_index=False).agg(
            chart_carries=("rush_flag", "sum"), explosive_rushes=("explosive_rush", "sum"),
            stuffed_rushes=("stuffed_rush", "sum"), outside_rushes=("outside_rush", "sum"),
        ).rename(columns={"rusher_norm": "player_name_norm"})
        ug["explosive_rush_rate"] = ug["explosive_rushes"] / ug["chart_carries"].replace(0, np.nan)
        ug["stuff_rate"] = ug["stuffed_rushes"] / ug["chart_carries"].replace(0, np.nan)
        ug["outside_rush_rate"] = ug["outside_rushes"] / ug["chart_carries"].replace(0, np.nan)
        player_frames.append(ug)

    players = pd.DataFrame()
    for frame in player_frames:
        players = frame if players.empty else players.merge(frame, on="player_name_norm", how="outer")

    pass_def = df[(df["pass_flag"] > 0) & (df["defteam"].str.len() > 0)].copy()
    defenses = pd.DataFrame()
    if not pass_def.empty:
        pass_def["deep_epa"] = pass_def["epa_value"] * pass_def["deep_flag"]
        pass_def["short_epa"] = pass_def["epa_value"] * (1.0 - pass_def["deep_flag"])
        pass_def["deep_yards"] = pass_def["yards"] * pass_def["deep_flag"]
        pass_def["short_yards"] = pass_def["yards"] * (1.0 - pass_def["deep_flag"])
        pass_def["explosive_pass"] = (pass_def["yards"] >= 20).astype(float)
        defenses = pass_def.groupby("defteam", as_index=False).agg(
            charted_pass_plays=("pass_flag", "sum"), pressure_rate=("pressure_flag", "mean"),
            blitz_rate=("blitz_flag", "mean"), sack_rate=("sack_flag", "mean"),
            man_rate=("man_flag", "mean"), zone_rate=("zone_flag", "mean"),
            deep_epa_sum=("deep_epa", "sum"), short_epa_sum=("short_epa", "sum"),
            deep_plays=("deep_flag", "sum"), deep_yards=("deep_yards", "sum"),
            short_yards=("short_yards", "sum"), explosive_pass_rate=("explosive_pass", "mean"),
        ).rename(columns={"defteam": "defense"})
        defenses["deep_epa_allowed"] = defenses["deep_epa_sum"] / defenses["deep_plays"].replace(0, np.nan)
        defenses["short_plays"] = defenses["charted_pass_plays"] - defenses["deep_plays"]
        defenses["short_epa_allowed"] = defenses["short_epa_sum"] / defenses["short_plays"].replace(0, np.nan)
        defenses["deep_yards_per_play"] = defenses["deep_yards"] / defenses["deep_plays"].replace(0, np.nan)
        defenses["short_yards_per_play"] = defenses["short_yards"] / defenses["short_plays"].replace(0, np.nan)
        defenses["scheme_confidence"] = (35 + np.sqrt(defenses["charted_pass_plays"].clip(lower=0)) * 4.0).clip(35, 96)

    rush_def = df[(df["rush_flag"] > 0) & (df["defteam"].str.len() > 0)].copy()
    if not rush_def.empty:
        box = pd.to_numeric(_column(rush_def, "defenders_in_box", default=np.nan), errors="coerce")
        rush_def["stacked_box"] = (box >= 8).astype(float).where(box.notna(), np.nan)
        rush_def["rush_stuff"] = (rush_def["yards"] <= 0).astype(float)
        rush_def["rush_explosive"] = (rush_def["yards"] >= 10).astype(float)
        rd = rush_def.groupby("defteam", as_index=False).agg(
            box_rate=("stacked_box", "mean"), defensive_stuff_rate=("rush_stuff", "mean"),
            rush_explosive_allowed=("rush_explosive", "mean"),
        ).rename(columns={"defteam": "defense"})
        defenses = rd if defenses.empty else defenses.merge(rd, on="defense", how="outer")
    return {"players": players.replace([np.inf, -np.inf], np.nan), "defenses": defenses.replace([np.inf, -np.inf], np.nan)}


@st.cache_data(ttl=21600, show_spinner=False)
@st.cache_data(ttl=21600, show_spinner=False)
def _season_player_profiles(season: int, through_week: int | None = None) -> pd.DataFrame:
    stats = _load_player_stats_season(int(season))
    if stats is None or stats.empty:
        return pd.DataFrame()
    df = stats.copy()
    if "season_type" in df.columns:
        df = df[df["season_type"].astype(str).str.upper() == "REG"].copy()
    if through_week is not None and "week" in df.columns:
        df = df[pd.to_numeric(df["week"], errors="coerce") <= int(through_week)].copy()
    if df.empty:
        return pd.DataFrame()
    df["player_name"] = _player_name_column(df).astype(str)
    df["player_name_norm"] = df["player_name"].map(_normalize_name)
    df["team"] = _player_team_column(df)
    df["position"] = _player_position_column(df).map(_position_group)
    df["opponent"] = _column(df, "opponent_team", default="").map(_normalize_team)
    numeric = [
        "attempts", "completions", "passing_yards", "passing_tds", "interceptions", "sacks",
        "passing_air_yards", "passing_yards_after_catch", "carries", "rushing_yards", "rushing_tds",
        "targets", "receptions", "receiving_yards", "receiving_tds", "receiving_air_yards",
        "receiving_yards_after_catch", "passing_epa", "rushing_epa", "receiving_epa",
    ]
    for column in numeric:
        df[column] = _numeric_frame_column(df, column)
    df = df[df["player_name_norm"].str.len() > 0].copy()
    if df.empty:
        return pd.DataFrame()

    snap = _load_snap_counts_season(int(season))
    if snap is not None and not snap.empty:
        sd = snap.copy()
        if through_week is not None and "week" in sd.columns:
            sd = sd[pd.to_numeric(sd["week"], errors="coerce") <= int(through_week)].copy()
        sd["player_name_norm"] = _column(sd, "player", "player_name", default="").map(_normalize_name)
        sd["team"] = _column(sd, "team", default="").map(_normalize_team)
        sd["week_num"] = pd.to_numeric(_column(sd, "week", default=np.nan), errors="coerce")
        sd["offense_snaps"] = pd.to_numeric(_column(sd, "offense_snaps", default=np.nan), errors="coerce")
        sd["snap_share"] = pd.to_numeric(_column(sd, "offense_pct", default=np.nan), errors="coerce")
        sd.loc[sd["snap_share"] > 1.5, "snap_share"] /= 100.0
        sd = sd.groupby(["player_name_norm", "team", "week_num"], as_index=False).agg(
            offense_snaps=("offense_snaps", "mean"), snap_share=("snap_share", "mean")
        )
        df["week_num"] = pd.to_numeric(_column(df, "week", default=np.nan), errors="coerce")
        df = df.merge(sd, on=["player_name_norm", "team", "week_num"], how="left")
    else:
        df["offense_snaps"] = np.nan
        df["snap_share"] = np.nan
        df["week_num"] = pd.to_numeric(_column(df, "week", default=np.nan), errors="coerce")

    team_week = df.groupby(["team", "week_num"], as_index=False).agg(
        team_pass_attempts=("attempts", "sum"), team_sacks=("sacks", "sum"), team_carries=("carries", "sum"),
        team_targets=("targets", "sum"), team_offense_snaps=("offense_snaps", "max"),
    )
    team_week["team_dropbacks"] = team_week["team_pass_attempts"] + team_week["team_sacks"]
    fallback_plays = team_week["team_dropbacks"] + team_week["team_carries"]
    team_week["team_offense_snaps"] = team_week["team_offense_snaps"].where(team_week["team_offense_snaps"] > 0, fallback_plays)
    df = df.merge(team_week, on=["team", "week_num"], how="left")
    df["dropback_rate"] = df["team_dropbacks"] / df["team_offense_snaps"].replace(0, np.nan)
    route_factor = df["position"].map({"RB": 0.68, "WR": 0.97, "TE": 0.82}).fillna(0.0)
    tprr_prior = df["position"].map({"RB": 0.235, "WR": 0.205, "TE": 0.190}).fillna(np.nan)
    snap_routes = df["offense_snaps"] * df["dropback_rate"].clip(0.35, 0.82) * route_factor
    target_routes = df["targets"] / tprr_prior
    target_routes = target_routes.clip(lower=0)
    target_routes = np.minimum(target_routes, df["offense_snaps"].fillna(target_routes))
    df["estimated_routes"] = np.where(
        snap_routes.notna() & target_routes.notna(), 0.72 * snap_routes + 0.28 * target_routes,
        snap_routes.fillna(target_routes),
    )
    df["route_participation"] = df["estimated_routes"] / df["team_dropbacks"].replace(0, np.nan)
    df["targets_per_route"] = df["targets"] / df["estimated_routes"].replace(0, np.nan)

    max_week = pd.to_numeric(df["week_num"], errors="coerce").max()
    df["recent_weight"] = np.exp(-0.32 * (max_week - df["week_num"].fillna(max_week)))
    recent_rows = []
    recent_metrics = ["attempts", "carries", "targets", "snap_share", "route_participation", "targets_per_route", "estimated_routes"]
    for name, group in df.groupby("player_name_norm"):
        weights = group["recent_weight"].to_numpy(dtype=float)
        record = {"player_name_norm": name}
        for metric in recent_metrics:
            vals = pd.to_numeric(group[metric], errors="coerce").to_numpy(dtype=float)
            valid = np.isfinite(vals) & np.isfinite(weights)
            record[f"recent_{metric if metric not in ['attempts','carries','targets','estimated_routes'] else metric + '_pg'}"] = float(np.average(vals[valid], weights=weights[valid])) if valid.any() and weights[valid].sum() > 0 else np.nan
        recent_rows.append(record)
    recent = pd.DataFrame(recent_rows)

    grouped = df.groupby("player_name_norm", as_index=False).agg(
        player_name=("player_name", "last"), team=("team", "last"), position=("position", "last"),
        games=("week_num", "nunique"), attempts=("attempts", "sum"), completions=("completions", "sum"),
        passing_yards=("passing_yards", "sum"), passing_tds=("passing_tds", "sum"), interceptions=("interceptions", "sum"),
        sacks=("sacks", "sum"), passing_air_yards=("passing_air_yards", "sum"),
        passing_yac=("passing_yards_after_catch", "sum"), carries=("carries", "sum"),
        rushing_yards=("rushing_yards", "sum"), rushing_tds=("rushing_tds", "sum"), targets=("targets", "sum"),
        receptions=("receptions", "sum"), receiving_yards=("receiving_yards", "sum"),
        receiving_tds=("receiving_tds", "sum"), receiving_air_yards=("receiving_air_yards", "sum"),
        receiving_yac=("receiving_yards_after_catch", "sum"), passing_epa=("passing_epa", "sum"),
        rushing_epa=("rushing_epa", "sum"), receiving_epa=("receiving_epa", "sum"),
        snap_share=("snap_share", "mean"), route_participation=("route_participation", "mean"),
        targets_per_route=("targets_per_route", "mean"), estimated_routes=("estimated_routes", "sum"),
    )
    grouped = grouped.merge(recent, on="player_name_norm", how="left")
    games = grouped["games"].replace(0, np.nan)
    for total, output in [
        ("attempts", "attempts_pg"), ("completions", "completions_pg"), ("passing_yards", "passing_yards_pg"),
        ("passing_tds", "passing_tds_pg"), ("interceptions", "interceptions_pg"), ("carries", "carries_pg"),
        ("rushing_yards", "rushing_yards_pg"), ("targets", "targets_pg"), ("receptions", "receptions_pg"),
        ("receiving_yards", "receiving_yards_pg"), ("estimated_routes", "estimated_routes_pg"),
    ]:
        grouped[output] = grouped[total] / games
    grouped["completion_rate"] = grouped["completions"] / grouped["attempts"].replace(0, np.nan)
    grouped["pass_ypa"] = grouped["passing_yards"] / grouped["attempts"].replace(0, np.nan)
    grouped["pass_td_rate"] = grouped["passing_tds"] / grouped["attempts"].replace(0, np.nan)
    grouped["interception_rate"] = grouped["interceptions"] / grouped["attempts"].replace(0, np.nan)
    grouped["rush_ypc"] = grouped["rushing_yards"] / grouped["carries"].replace(0, np.nan)
    grouped["catch_rate"] = grouped["receptions"] / grouped["targets"].replace(0, np.nan)
    grouped["yards_per_target"] = grouped["receiving_yards"] / grouped["targets"].replace(0, np.nan)
    grouped["yards_per_reception"] = grouped["receiving_yards"] / grouped["receptions"].replace(0, np.nan)
    grouped["adot"] = grouped["receiving_air_yards"] / grouped["targets"].replace(0, np.nan)
    grouped["yac_per_reception"] = grouped["receiving_yac"] / grouped["receptions"].replace(0, np.nan)
    team_totals = grouped.groupby("team", as_index=False).agg(
        team_attempts=("attempts_pg", "sum"), team_carries=("carries_pg", "sum"), team_targets=("targets_pg", "sum")
    )
    grouped = grouped.merge(team_totals, on="team", how="left")
    grouped["attempt_share"] = grouped["attempts_pg"] / grouped["team_attempts"].replace(0, np.nan)
    grouped["carry_share"] = grouped["carries_pg"] / grouped["team_carries"].replace(0, np.nan)
    grouped["target_share"] = grouped["targets_pg"] / grouped["team_targets"].replace(0, np.nan)

    for stat_type, columns in [
        ("passing", ["completion_percentage_above_expectation", "avg_intended_air_yards", "avg_time_to_throw"]),
        ("rushing", ["rush_yards_over_expected_per_att", "percent_attempts_gte_eight_defenders", "avg_time_to_los"]),
        ("receiving", ["avg_separation", "avg_yac_above_expectation", "avg_intended_air_yards"]),
    ]:
        ngs = _load_nextgen_season(int(season), stat_type)
        if ngs is None or ngs.empty:
            continue
        nd = ngs.copy()
        if through_week is not None and "week" in nd.columns:
            nd = nd[pd.to_numeric(nd["week"], errors="coerce") <= int(through_week)].copy()
        nd["player_name_norm"] = _column(nd, "player_display_name", default="").map(_normalize_name)
        usable = [column for column in columns if column in nd.columns]
        for column in usable:
            nd[column] = pd.to_numeric(nd[column], errors="coerce")
        if usable:
            summary = nd.groupby("player_name_norm", as_index=False)[usable].mean()
            grouped = grouped.merge(summary.rename(columns={c: f"ngs_{stat_type}_{c}" for c in usable}), on="player_name_norm", how="left")

    chart = _season_charting_features(int(season), through_week).get("players", pd.DataFrame())
    if chart is not None and not chart.empty:
        grouped = grouped.merge(chart, on="player_name_norm", how="left")
    return grouped.replace([np.inf, -np.inf], np.nan)


def _position_prior(position: str, metric: str, default: float) -> float:
    return _num(POSITION_EFFICIENCY_PRIORS.get(_position_group(position), {}).get(metric, default), default)


def _blend_value(prior: dict[str, Any], current: dict[str, Any], metric: str, weight: float, default: float = 0.0) -> float:
    p = _num(prior.get(metric, default), default)
    c = _num(current.get(metric, p), p)
    return (1.0 - weight) * p + weight * c


@st.cache_data(ttl=21600, show_spinner=False)
def _blended_player_profiles(season: int, projection_week: int) -> pd.DataFrame:
    prior_df = _season_player_profiles(int(season) - 1, None)
    current_df = _season_player_profiles(int(season), max(0, int(projection_week) - 1)) if int(projection_week) > 1 else pd.DataFrame()
    prior_map = {row["player_name_norm"]: row.to_dict() for _, row in prior_df.iterrows()} if not prior_df.empty else {}
    current_map = {row["player_name_norm"]: row.to_dict() for _, row in current_df.iterrows()} if not current_df.empty else {}
    role_metrics = [
        "attempts_pg", "completions_pg", "passing_yards_pg", "passing_tds_pg", "interceptions_pg",
        "carries_pg", "rushing_yards_pg", "targets_pg", "receptions_pg", "receiving_yards_pg",
        "attempt_share", "carry_share", "target_share", "snap_share", "estimated_routes_pg",
        "route_participation", "targets_per_route",
    ]
    efficiency_metrics = [
        "completion_rate", "pass_ypa", "pass_td_rate", "interception_rate", "rush_ypc", "catch_rate",
        "yards_per_target", "yards_per_reception", "adot", "yac_per_reception",
        "ngs_passing_completion_percentage_above_expectation", "ngs_passing_avg_intended_air_yards",
        "ngs_passing_avg_time_to_throw", "ngs_rushing_rush_yards_over_expected_per_att",
        "ngs_rushing_percent_attempts_gte_eight_defenders", "ngs_rushing_avg_time_to_los",
        "ngs_receiving_avg_separation", "ngs_receiving_avg_yac_above_expectation",
        "ngs_receiving_avg_intended_air_yards", "catchable_target_rate", "contested_target_rate",
        "drop_rate", "created_reception_rate", "deep_target_rate", "man_ypt", "zone_ypt",
        "man_catch_rate", "zone_catch_rate", "interception_worthy_rate", "throwaway_rate",
        "qb_fault_sack_rate", "pressure_rate_faced", "deep_attempt_rate", "explosive_rush_rate",
        "stuff_rate", "outside_rush_rate",
    ]
    total_metrics = ["attempts", "completions", "passing_yards", "passing_tds", "interceptions", "carries", "rushing_yards", "targets", "receptions", "receiving_yards", "chart_targets", "man_targets", "zone_targets"]
    rows = []
    for name in sorted(set(prior_map) | set(current_map)):
        prior, current = prior_map.get(name, {}), current_map.get(name, {})
        current_games, prior_games = _num(current.get("games", 0), 0), _num(prior.get("games", 0), 0)
        role_weight = 0.0 if current_games <= 0 else clamp(0.38 + 0.12 * current_games, 0.38, 0.92)
        efficiency_weight = 0.0 if current_games <= 0 else min(_season_weight(projection_week, current_games), current_games / (current_games + 7.0))
        position = _safe_text(current.get("position", prior.get("position", "")))
        row = {
            "player_name_norm": name, "player_name": _safe_text(current.get("player_name", prior.get("player_name", name.title()))),
            "team": _normalize_team(current.get("team", prior.get("team", ""))), "position": _position_group(position),
            "games": round((1 - role_weight) * prior_games + role_weight * current_games, 2),
            "prior_games": prior_games, "current_games": current_games, "role_weight": round(role_weight, 3),
            "efficiency_weight": round(efficiency_weight, 3),
        }
        for metric in role_metrics:
            p = _num(prior.get(metric, np.nan), np.nan)
            recent_key = f"recent_{metric}"
            c = _num(current.get(recent_key, current.get(metric, np.nan)), np.nan)
            row[f"prior_{metric}"] = p
            row[f"current_{metric}"] = c
            if not math.isfinite(p): p = c
            if not math.isfinite(c): c = p
            row[metric] = (1 - role_weight) * p + role_weight * c if math.isfinite(p) else np.nan
        for metric in efficiency_metrics:
            p = _num(prior.get(metric, np.nan), np.nan)
            c = _num(current.get(metric, p), p)
            if not math.isfinite(p): p = c
            if not math.isfinite(c): c = p
            row[metric] = (1 - efficiency_weight) * p + efficiency_weight * c if math.isfinite(p) else np.nan
        for metric in total_metrics:
            row[metric] = _num(prior.get(metric, 0), 0) + _num(current.get(metric, 0), 0)
        rows.append(row)
    return pd.DataFrame(rows)


@st.cache_data(ttl=21600, show_spinner=False)
def _defense_position_profiles(season: int, projection_week: int) -> pd.DataFrame:
    def season_frame(target_season: int, through_week: int | None) -> pd.DataFrame:
        stats = _load_player_stats_season(target_season)
        if stats is None or stats.empty or "opponent_team" not in stats.columns:
            return pd.DataFrame()
        df = stats.copy()
        if "season_type" in df.columns:
            df = df[df["season_type"].astype(str).str.upper() == "REG"].copy()
        if through_week is not None and "week" in df.columns:
            df = df[pd.to_numeric(df["week"], errors="coerce") <= int(through_week)].copy()
        if df.empty:
            return pd.DataFrame()
        df["defense"] = _column(df, "opponent_team", default="").map(_normalize_team)
        df["position"] = _player_position_column(df).map(_position_group)
        for column in ["attempts", "passing_yards", "carries", "rushing_yards", "targets", "receptions", "receiving_yards", "receiving_air_yards", "receiving_yards_after_catch"]:
            df[column] = _numeric_frame_column(df, column)
        weekly = df.groupby(["defense", "position", "week"], as_index=False).agg(
            attempts=("attempts", "sum"), passing_yards=("passing_yards", "sum"), carries=("carries", "sum"),
            rushing_yards=("rushing_yards", "sum"), targets=("targets", "sum"), receptions=("receptions", "sum"),
            receiving_yards=("receiving_yards", "sum"), receiving_air_yards=("receiving_air_yards", "sum"),
            receiving_yac=("receiving_yards_after_catch", "sum"),
        )
        return weekly.groupby(["defense", "position"], as_index=False).mean(numeric_only=True)

    prior = season_frame(int(season) - 1, None)
    current = season_frame(int(season), max(0, int(projection_week) - 1)) if int(projection_week) > 1 else pd.DataFrame()
    prior_map = {(row["defense"], row["position"]): row.to_dict() for _, row in prior.iterrows()} if not prior.empty else {}
    current_map = {(row["defense"], row["position"]): row.to_dict() for _, row in current.iterrows()} if not current.empty else {}
    current_weight = _season_weight(projection_week, max(0, projection_week - 1))
    rows = []
    metrics = ["attempts", "passing_yards", "carries", "rushing_yards", "targets", "receptions", "receiving_yards", "receiving_air_yards", "receiving_yac"]
    for key in sorted(set(prior_map) | set(current_map)):
        p, c = prior_map.get(key, {}), current_map.get(key, {})
        row = {"defense": key[0], "position": key[1]}
        for metric in metrics:
            row[metric] = _blend_value(p, c, metric, current_weight, 0.0)
        rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    for position in out["position"].unique():
        mask = out["position"] == position
        for metric in metrics:
            league = pd.to_numeric(out.loc[mask, metric], errors="coerce").replace(0, np.nan).mean()
            out.loc[mask, f"{metric}_index"] = (out.loc[mask, metric] / league).clip(0.70, 1.30) if league and math.isfinite(league) else 1.0

    prior_scheme = _season_charting_features(int(season) - 1, None).get("defenses", pd.DataFrame())
    current_scheme = _season_charting_features(int(season), max(0, int(projection_week) - 1)).get("defenses", pd.DataFrame()) if int(projection_week) > 1 else pd.DataFrame()
    pmap = {row["defense"]: row.to_dict() for _, row in prior_scheme.iterrows()} if prior_scheme is not None and not prior_scheme.empty else {}
    cmap = {row["defense"]: row.to_dict() for _, row in current_scheme.iterrows()} if current_scheme is not None and not current_scheme.empty else {}
    scheme_metrics = ["pressure_rate", "blitz_rate", "sack_rate", "man_rate", "zone_rate", "deep_epa_allowed", "short_epa_allowed", "deep_yards_per_play", "short_yards_per_play", "explosive_pass_rate", "box_rate", "defensive_stuff_rate", "rush_explosive_allowed", "scheme_confidence"]
    scheme_rows = []
    for defense in set(out["defense"].tolist()) | set(pmap) | set(cmap):
        p, c = pmap.get(defense, {}), cmap.get(defense, {})
        row = {"defense": defense}
        live_weight = current_weight if c else 0.0
        for metric in scheme_metrics:
            row[metric] = _blend_value(p, c, metric, live_weight, np.nan)
        scheme_rows.append(row)
    scheme = pd.DataFrame(scheme_rows)
    out = out.merge(scheme, on="defense", how="left")
    # Turn scheme values into favorable-to-offense indices (>1 means easier matchup).
    for metric, output_col, inverse in [
        ("deep_epa_allowed", "deep_pass_index", False), ("short_epa_allowed", "short_pass_index", False),
        ("pressure_rate", "pressure_index", True), ("defensive_stuff_rate", "stuff_index", True),
        ("rush_explosive_allowed", "rush_explosive_index", False),
    ]:
        values = pd.to_numeric(out[metric], errors="coerce") if metric in out.columns else pd.Series(np.nan, index=out.index)
        league = values.mean()
        if math.isfinite(_num(league, np.nan)):
            if inverse:
                out[output_col] = (1.0 - (values - league) * 2.5).clip(0.78, 1.22)
            else:
                scale = 1.5 if "epa" in metric else 2.0
                out[output_col] = (1.0 + (values - league) * scale).clip(0.78, 1.22)
        else:
            out[output_col] = 1.0
    out["man_rate"] = pd.to_numeric(out.get("man_rate", 0.35), errors="coerce").fillna(0.35).clip(0.05, 0.80)
    out["zone_rate"] = pd.to_numeric(out.get("zone_rate", 0.65), errors="coerce").fillna(0.65).clip(0.15, 0.95)
    total_cov = out["man_rate"] + out["zone_rate"]
    out["man_rate"] /= total_cov
    out["zone_rate"] /= total_cov
    out["scheme_confidence"] = pd.to_numeric(out.get("scheme_confidence", 45), errors="coerce").fillna(45).clip(30, 96)
    return out.replace([np.inf, -np.inf], np.nan).fillna({c: 1.0 for c in out.columns if c.endswith("_index")})


def _profile_lookup(profiles: pd.DataFrame, player: str) -> dict[str, Any]:
    if profiles is None or profiles.empty:
        return {}
    name = _normalize_name(player)
    rows = profiles[profiles["player_name_norm"] == name]
    return rows.iloc[0].to_dict() if not rows.empty else {}


def _defense_profile_lookup(defense_profiles: pd.DataFrame, defense: str, position: str) -> dict[str, Any]:
    if defense_profiles is None or defense_profiles.empty:
        return {}
    rows = defense_profiles[
        (defense_profiles["defense"].astype(str).map(_normalize_team) == _normalize_team(defense))
        & (defense_profiles["position"].astype(str) == _position_group(position))
    ]
    return rows.iloc[0].to_dict() if not rows.empty else {}


def _team_usage_context(profiles: pd.DataFrame, team: str, rating: dict[str, Any]) -> dict[str, float]:
    subset = profiles[profiles["team"].astype(str).map(_normalize_team) == _normalize_team(team)].copy() if profiles is not None and not profiles.empty else pd.DataFrame()
    qb_attempts = 34.0
    carries = 27.0
    if not subset.empty:
        qb = subset[subset["position"] == "QB"]
        if not qb.empty:
            qb_attempts = max(20.0, float(pd.to_numeric(qb["attempts_pg"], errors="coerce").max()))
        carries_value = float(pd.to_numeric(subset["carries_pg"], errors="coerce").sum())
        if math.isfinite(carries_value) and carries_value > 10:
            carries = carries_value
    plays = clamp(_num(rating.get("Pace", 64), 64), 56, 72)
    pass_rate = clamp(qb_attempts / max(qb_attempts + carries, 1), 0.48, 0.69)
    return {"plays": float(plays), "pass_rate": float(pass_rate), "pass_attempts": qb_attempts, "carries": carries}


def _lineup_player_probability(lineup: pd.DataFrame, player: str) -> float:
    if lineup is None or lineup.empty:
        return 0.85
    rows = lineup[lineup["Player"].astype(str).map(_normalize_name) == _normalize_name(player)]
    return clamp(_num(rows.iloc[0].get("Effective Play Probability", 0.85), 0.85), 0.0, 1.0) if not rows.empty else 0.85


def _lineup_slot(lineup: pd.DataFrame, player: str) -> str:
    if lineup is None or lineup.empty:
        return ""
    rows = lineup[lineup["Player"].astype(str).map(_normalize_name) == _normalize_name(player)]
    return _safe_text(rows.iloc[0].get("Slot", "")) if not rows.empty else ""


def _role_defaults(position: str, slot: str) -> dict[str, float]:
    pos = _position_group(position)
    if pos == "QB":
        return {"attempt_share": 0.985, "carry_share": 0.18, "target_share": 0.0, "snap_share": 0.99, "route_participation": 0.0, "targets_per_route": 0.0}
    if pos == "RB":
        starter = slot == "RB1"
        return {"carry_share": 0.62 if starter else 0.27, "target_share": 0.12 if starter else 0.07, "snap_share": 0.64 if starter else 0.34, "route_participation": 0.50 if starter else 0.27, "targets_per_route": 0.235}
    if pos == "WR":
        rank = {"WR1": 0, "WR2": 1, "WR3": 2}.get(slot, 2)
        return {"target_share": [0.24, 0.19, 0.14][rank], "snap_share": [0.92, 0.85, 0.73][rank], "route_participation": [0.91, 0.83, 0.68][rank], "targets_per_route": [0.235, 0.205, 0.185][rank]}
    return {"target_share": 0.16, "snap_share": 0.79, "route_participation": 0.72, "targets_per_route": 0.19}




def _manual_role_share(lineup: pd.DataFrame, player: str) -> float:
    if lineup is None or lineup.empty or "Manual Role Share" not in lineup.columns:
        return math.nan
    rows = lineup[lineup["Player"].astype(str).map(_normalize_name) == _normalize_name(player)]
    if rows.empty:
        return math.nan
    value = _num(rows.iloc[0].get("Manual Role Share", np.nan), np.nan)
    return value if math.isfinite(value) and 0.0 <= value <= 1.0 else math.nan


def _expected_role_metric(
    profile: dict[str, Any], team: str, position: str, slot: str, metric: str, default: float,
) -> tuple[float, str, bool]:
    """Project a current role from depth-chart slot and prior/current usage.

    Depth-chart role is intentionally allowed to move faster than efficiency. A player
    promoted from RB2 to RB1, for example, receives an RB1 opportunity anchor instead
    of carrying last season's committee share unchanged into Week 1.
    """
    current_games = _num(profile.get("current_games", 0), 0)
    prior_value = _num(profile.get(f"prior_{metric}", profile.get(metric, np.nan)), np.nan)
    current_value = _num(profile.get(f"current_{metric}", np.nan), np.nan)
    blended_value = _num(profile.get(metric, np.nan), np.nan)
    profile_team = _normalize_team(profile.get("team", ""))
    top_slot = slot in ["QB", "RB1", "WR1", "TE"]

    if current_games > 0 and math.isfinite(current_value) and current_value > 0:
        current_weight = clamp(0.55 + 0.10 * max(0.0, current_games - 1.0), 0.55, 0.92)
        value = current_weight * current_value + (1.0 - current_weight) * default
        note = "Current-season usage weighted above prior role"
        transition = abs(current_value - default) >= max(0.04, default * 0.25)
        return value, note, transition

    historical = prior_value if math.isfinite(prior_value) and prior_value > 0 else blended_value
    if not math.isfinite(historical) or historical <= 0:
        return default, "Depth-chart role prior", True

    changed_team = bool(profile_team and profile_team != _normalize_team(team))
    promoted = bool(top_slot and historical < default * 0.78)
    demoted = bool(not top_slot and historical > default * 1.22)
    if changed_team:
        anchor_weight = 0.90
        note = "New-team depth-chart role replaces prior-team workload"
    elif promoted:
        anchor_weight = 0.86
        note = "Promoted starter role replaces last season's smaller workload"
    elif demoted:
        anchor_weight = 0.78
        note = "Lower depth-chart role reduces last season's workload"
    elif top_slot:
        anchor_weight = 0.64
        note = "Starter role blended with prior usage"
    else:
        anchor_weight = 0.56
        note = "Depth-chart role blended with prior usage"
    value = anchor_weight * default + (1.0 - anchor_weight) * historical
    return value, note, changed_team or promoted or demoted


def _expected_lineup_roles(lineup: pd.DataFrame, profiles: pd.DataFrame, team: str) -> dict[str, dict[str, Any]]:
    if lineup is None or lineup.empty:
        return {}
    skill = lineup[(lineup["Unit"].astype(str) == "Offense") & (lineup["Slot"].astype(str).isin(["QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE"])) & (~lineup["Player"].astype(str).str.upper().isin(["", "TBD", "UNKNOWN"]))].copy()
    roles: dict[str, dict[str, Any]] = {}
    for _, row in skill.iterrows():
        player, slot = _safe_text(row.get("Player", "")), _safe_text(row.get("Slot", ""))
        name = _normalize_name(player)
        profile = _profile_lookup(profiles, player)
        pos = _position_group(row.get("Position", profile.get("position", "")))
        defaults = _role_defaults(pos, slot)
        role: dict[str, Any] = {"slot": slot, "position": pos, "play_probability": _lineup_player_probability(lineup, player), "manual": False, "transition": False, "role_note": "Depth-chart role prior"}
        notes, transition = [], False
        for metric in ["attempt_share", "carry_share", "target_share", "snap_share", "route_participation", "targets_per_route"]:
            default = _num(defaults.get(metric, 0.0), 0.0)
            if default <= 0:
                role[metric] = 0.0
                continue
            value, note, changed = _expected_role_metric(profile, team, pos, slot, metric, default)
            role[metric] = value
            if note not in notes: notes.append(note)
            transition = transition or changed
        manual = _manual_role_share(lineup, player)
        if math.isfinite(manual):
            role["manual"] = True
            if pos == "QB":
                role["attempt_share"] = clamp(manual, 0.50, 1.0)
            elif pos == "RB":
                base = max(_num(defaults.get("carry_share", 0.35), 0.35), 0.05)
                scale = clamp(manual / base, 0.45, 1.70)
                role["carry_share"] = clamp(manual, 0.02, 0.92)
                role["target_share"] = clamp(_num(role.get("target_share", 0.08)) * scale, 0.015, 0.30)
                role["snap_share"] = clamp(_num(role.get("snap_share", 0.50)) * math.sqrt(scale), 0.15, 0.98)
                role["route_participation"] = clamp(_num(role.get("route_participation", 0.40)) * math.sqrt(scale), 0.10, 0.80)
            else:
                base = max(_num(defaults.get("target_share", 0.15), 0.15), 0.04)
                scale = clamp(manual / base, 0.45, 1.70)
                role["target_share"] = clamp(manual, 0.02, 0.40)
                role["snap_share"] = clamp(_num(role.get("snap_share", 0.70)) * math.sqrt(scale), 0.25, 1.0)
                role["route_participation"] = clamp(_num(role.get("route_participation", 0.70)) * math.sqrt(scale), 0.20, 0.98)
            notes, transition = ["Manual role-share override"], True
        role["transition"] = transition
        role["role_note"] = " • ".join(notes[:2]) if notes else "Depth-chart role prior"
        roles[name] = role

    rb_names = [n for n, r in roles.items() if r.get("position") == "RB"]
    active_rb = [n for n in rb_names if _num(roles[n].get("play_probability", 1), 1) >= 0.50]
    if active_rb:
        backfield_share = 0.88
        locked = [n for n in active_rb if bool(roles[n].get("manual", False))]
        unlocked = [n for n in active_rb if n not in locked]
        locked_total = sum(clamp(_num(roles[n].get("carry_share", 0), 0), 0, backfield_share) for n in locked)
        if locked_total > backfield_share and locked_total > 0:
            for n in locked: roles[n]["carry_share"] = backfield_share * _num(roles[n].get("carry_share", 0), 0) / locked_total
            locked_total = backfield_share
        remaining = max(0.0, backfield_share - locked_total)
        weight = sum(max(0.01, _num(roles[n].get("carry_share", 0), 0)) for n in unlocked)
        if unlocked and weight > 0:
            for n in unlocked: roles[n]["carry_share"] = remaining * max(0.01, _num(roles[n].get("carry_share", 0), 0)) / weight

    target_names = [n for n, r in roles.items() if r.get("position") in ["RB", "WR", "TE"] and _num(r.get("play_probability", 1), 1) >= 0.50]
    if target_names:
        for n in target_names:
            role = roles[n]
            route_weight = _num(role.get("route_participation", 0), 0) * _num(role.get("targets_per_route", 0), 0)
            role["target_share"] = 0.55 * _num(role.get("target_share", 0), 0) + 0.45 * route_weight
        listed_share = 0.92
        locked = [n for n in target_names if bool(roles[n].get("manual", False)) and roles[n].get("position") in ["WR", "TE"]]
        unlocked = [n for n in target_names if n not in locked]
        locked_total = sum(clamp(_num(roles[n].get("target_share", 0), 0), 0, listed_share) for n in locked)
        if locked_total > listed_share and locked_total > 0:
            for n in locked: roles[n]["target_share"] = listed_share * _num(roles[n].get("target_share", 0), 0) / locked_total
            locked_total = listed_share
        remaining = max(0.0, listed_share - locked_total)
        weight = sum(max(0.005, _num(roles[n].get("target_share", 0), 0)) for n in unlocked)
        if unlocked and weight > 0:
            for n in unlocked: roles[n]["target_share"] = remaining * max(0.005, _num(roles[n].get("target_share", 0), 0)) / weight
    return roles

def _regressed_rate(raw: float, volume: float, prior: float, prior_volume: float) -> float:
    raw = raw if math.isfinite(raw) and raw > 0 else prior
    volume = max(0.0, volume)
    return (raw * volume + prior * prior_volume) / max(volume + prior_volume, 1.0)



def _prop_sd(market: str, projection: float, reliability: float) -> float:
    base = {"Passing Attempts": 5.2, "Passing Completions": 4.2, "Passing Yards": 55.0, "Passing TDs": 1.0, "Interceptions": 0.68, "Rushing Attempts": 4.0, "Rushing Yards": 23.0, "Targets": 2.5, "Receptions": 1.9, "Receiving Yards": 25.0}.get(market, max(1.0, projection * 0.32))
    if market in ["Rushing Yards", "Receiving Yards"]:
        base = max(base, projection * 0.34)
    return float(base * (1.0 + max(0.0, 74.0 - reliability) / 145.0))


def _fair_line(projection: float, market: str) -> float:
    return round(math.floor(projection) + 0.5, 1) if market in ["Passing TDs", "Interceptions", "Receptions", "Targets", "Passing Attempts", "Passing Completions", "Rushing Attempts"] else round(round(projection * 2) / 2, 1)


@st.cache_data(ttl=900, show_spinner=False)
def _prop_calibration_data() -> pd.DataFrame:
    if not sheets_ready():
        return pd.DataFrame(columns=PROP_CALIBRATION_COLUMNS)
    try:
        df = read_sheet(PROP_CALIBRATION_TAB, PROP_CALIBRATION_COLUMNS)
        if df is None or df.empty:
            return pd.DataFrame(columns=PROP_CALIBRATION_COLUMNS)
        for col in ["Projection", "Actual Result", "Projection Residual", "Opportunity Error", "Efficiency Error"]:
            if col in df.columns: df[col] = pd.to_numeric(df[col], errors="coerce")
        return df[df["Actual Result"].notna() & df["Projection Residual"].notna()].copy()
    except Exception:
        return pd.DataFrame(columns=PROP_CALIBRATION_COLUMNS)


def _prop_calibration_adjustment(player: str, position: str, market: str, opponent: str) -> dict[str, float]:
    df = _prop_calibration_data()
    if df is None or df.empty:
        return {"adjustment": 0.0, "sample": 0, "residual_sd": np.nan}
    market_rows = df[df["Market"].astype(str) == str(market)].copy()
    if market_rows.empty:
        return {"adjustment": 0.0, "sample": 0, "residual_sd": np.nan}
    market_rows["player_norm"] = market_rows["Player"].astype(str).map(_normalize_name)
    market_rows["position_norm"] = market_rows["Position"].astype(str).map(_position_group)
    market_rows["opponent_norm"] = market_rows["Opponent"].astype(str).map(_normalize_team)
    def shrunk(rows: pd.DataFrame, prior_n: float) -> tuple[float, int]:
        vals = pd.to_numeric(rows.get("Projection Residual"), errors="coerce").dropna()
        n = len(vals)
        return ((float(vals.mean()) * n / (n + prior_n)) if n else 0.0, n)
    g, ng = shrunk(market_rows, 45)
    p, np_ = shrunk(market_rows[market_rows["position_norm"] == _position_group(position)], 28)
    pl, npl = shrunk(market_rows[market_rows["player_norm"] == _normalize_name(player)], 12)
    op, nop = shrunk(market_rows[(market_rows["opponent_norm"] == _normalize_team(opponent)) & (market_rows["position_norm"] == _position_group(position))], 22)
    adjustment = 0.42 * g + 0.25 * p + 0.20 * pl + 0.13 * op
    caps = {"Passing Attempts": 2.2, "Passing Completions": 1.8, "Passing Yards": 18.0, "Passing TDs": 0.25, "Interceptions": 0.20, "Rushing Attempts": 2.0, "Rushing Yards": 10.0, "Targets": 1.3, "Receptions": 0.85, "Receiving Yards": 10.0}
    adjustment = clamp(adjustment, -caps.get(market, 8.0), caps.get(market, 8.0)) if ng >= 8 else 0.0
    residual_sd = float(pd.to_numeric(market_rows["Projection Residual"], errors="coerce").std(ddof=1)) if ng >= 15 else np.nan
    return {"adjustment": float(adjustment), "sample": int(ng), "residual_sd": residual_sd}


def _odds_profit(odds: int) -> float:
    return odds / 100.0 if odds > 0 else 100.0 / abs(odds or -110)


def _expected_value_with_push(win_probability: float, lose_probability: float, odds: int) -> float:
    return win_probability * _odds_profit(odds) - lose_probability


def _draw_negative_binomial(rng: np.random.Generator, mean: float, dispersion: float, size: int) -> np.ndarray:
    if mean <= 0:
        return np.zeros(size, dtype=int)
    k = max(0.5, float(dispersion))
    p = k / (k + mean)
    return rng.negative_binomial(k, p, size=size)


def _simulate_prop_distribution(item: dict[str, Any], draws: int = 12000) -> tuple[np.ndarray, str]:
    market = _safe_text(item.get("Market", ""))
    projection = max(0.0, _num(item.get("Projection", 0), 0))
    reliability = _num(item.get("Reliability", 65), 65)
    play_probability = clamp(_num(item.get("_play_probability", 1.0), 1.0), 0.02, 1.0)
    seed_text = f"{MODEL_VERSION}|{item.get('Team','')}|{item.get('Player','')}|{market}|{projection:.4f}"
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16) % (2**32 - 1)
    rng = np.random.default_rng(seed)
    active = rng.random(draws) < play_probability
    active_projection = projection / play_probability
    attempts = max(0.0, _num(item.get("Projected Player Attempts", 0), 0) / play_probability)
    targets = max(0.0, _num(item.get("Projected Targets", 0), 0) / play_probability)
    receptions = max(0.0, _num(item.get("Projected Receptions", 0), 0) / play_probability)
    efficiency = max(0.0, _num(item.get("Efficiency", 0), 0))
    low_conf = 1.0 + max(0.0, 75.0 - reliability) / 120.0

    if market == "Passing Attempts":
        samples = _draw_negative_binomial(rng, active_projection, 48 / low_conf, draws).astype(float)
        distribution = "Negative binomial attempts"
    elif market == "Passing Completions":
        a = _draw_negative_binomial(rng, attempts, 48 / low_conf, draws)
        comp_p = clamp(active_projection / max(attempts, 0.25), 0.35, 0.82)
        concentration = clamp(35 + reliability, 65, 130)
        beta_p = rng.beta(max(0.5, comp_p * concentration), max(0.5, (1 - comp_p) * concentration), size=draws)
        samples = rng.binomial(a, beta_p).astype(float)
        distribution = "Attempt count + beta-binomial completions"
    elif market == "Rushing Attempts":
        samples = _draw_negative_binomial(rng, active_projection, 14 / low_conf, draws).astype(float)
        distribution = "Negative binomial carries"
    elif market == "Targets":
        samples = _draw_negative_binomial(rng, active_projection, 10 / low_conf, draws).astype(float)
        distribution = "Negative binomial targets"
    elif market in ["Passing TDs", "Interceptions"]:
        dispersion = 4.2 if market == "Passing TDs" else 2.8
        samples = _draw_negative_binomial(rng, active_projection, dispersion / low_conf, draws).astype(float)
        distribution = "Poisson-gamma count"
    elif market == "Receptions":
        t = _draw_negative_binomial(rng, targets, 10 / low_conf, draws)
        catch_p = clamp(receptions / max(targets, 0.25), 0.30, 0.94)
        concentration = clamp(22 + reliability * 0.85, 35, 105)
        beta_p = rng.beta(max(0.5, catch_p * concentration), max(0.5, (1 - catch_p) * concentration), size=draws)
        samples = rng.binomial(t, beta_p).astype(float)
        distribution = "Target count + beta-binomial catches"
    elif market == "Passing Yards":
        a = _draw_negative_binomial(rng, attempts, 48 / low_conf, draws)
        ypa = np.maximum(2.8, rng.normal(efficiency, 1.20 * low_conf, size=draws))
        samples = np.maximum(0, a * ypa + rng.normal(0, np.sqrt(np.maximum(a, 1)) * 2.2 * low_conf, size=draws))
        samples = np.rint(samples)
        distribution = "Attempt-volume compound passing yards"
    elif market == "Rushing Yards":
        a = _draw_negative_binomial(rng, attempts, 13 / low_conf, draws)
        ypc = np.maximum(0.5, rng.normal(efficiency, 1.05 * low_conf, size=draws))
        samples = np.maximum(0, a * ypc + rng.normal(0, np.sqrt(np.maximum(a, 1)) * 1.8 * low_conf, size=draws))
        samples = np.rint(samples)
        distribution = "Carry-volume compound rushing yards"
    elif market == "Receiving Yards":
        t = _draw_negative_binomial(rng, targets, 10 / low_conf, draws)
        catch_p = clamp(receptions / max(targets, 0.25), 0.25, 0.94)
        concentration = clamp(20 + reliability * 0.80, 32, 100)
        beta_p = rng.beta(max(0.5, catch_p * concentration), max(0.5, (1 - catch_p) * concentration), size=draws)
        catches = rng.binomial(t, beta_p)
        ypr_mean = active_projection / max(receptions / play_probability, 0.35)
        ypr_sd = max(2.5, ypr_mean * 0.38 * low_conf)
        shape = max(1.2, (ypr_mean / ypr_sd) ** 2)
        scale = max(0.1, ypr_sd**2 / max(ypr_mean, 0.1))
        game_ypr = rng.gamma(shape, scale, size=draws)
        samples = np.rint(np.maximum(0, catches * game_ypr))
        distribution = "Targets + beta-binomial catches + gamma YPR"
    else:
        sd = _prop_sd(market, projection, reliability)
        samples = np.maximum(0, rng.normal(active_projection, sd, size=draws))
        distribution = "Normal fallback"
    samples = samples * active.astype(float)
    return samples, distribution


def _role_confidence(profile: dict[str, Any], play_probability: float, slot: str, market: str) -> float:
    games = _num(profile.get("games", 0), 0)
    snap = _num(profile.get("snap_share", np.nan), np.nan)
    route = _num(profile.get("route_participation", np.nan), np.nan)
    defaults = _role_defaults(profile.get("position", ""), slot)
    if not math.isfinite(snap) or snap <= 0: snap = defaults.get("snap_share", 0.65)
    if not math.isfinite(route) or route <= 0: route = defaults.get("route_participation", 0.0)
    sample = clamp(34 + games * 4.2, 34, 91)
    route_component = route if _position_group(profile.get("position", "")) in ["RB", "WR", "TE"] else snap
    confidence = 0.40 * sample + 27 * clamp(snap, 0, 1) + 14 * clamp(route_component, 0, 1) + 19 * play_probability
    if bool(profile.get("role_transition", False)): confidence -= 4
    return round(clamp(confidence, 30, 96), 1)


def _project_player_markets(
    player: str, position: str, slot: str, team: str, opponent: str, home_away: str,
    lineup: pd.DataFrame, profiles: pd.DataFrame, defense_profiles: pd.DataFrame,
    team_rating: dict[str, Any], opponent_rating: dict[str, Any], game_projection: dict[str, float],
    weather_adjustment: float, market_lines: dict[tuple[str, str], dict[str, Any]],
    role_context: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    profile = _profile_lookup(profiles, player)
    pos = _position_group(position or profile.get("position", ""))
    if pos not in ["QB", "RB", "WR", "TE"]: return []
    defaults = _role_defaults(pos, slot)
    role = (role_context or {}).get(_normalize_name(player), {})
    play_probability = _num(role.get("play_probability", _lineup_player_probability(lineup, player)), 0.85)
    role_note = _safe_text(role.get("role_note", ""))
    role_profile = {**profile, "snap_share": role.get("snap_share", profile.get("snap_share", np.nan)), "route_participation": role.get("route_participation", profile.get("route_participation", np.nan)), "role_transition": bool(role.get("transition", False))}
    defense = _defense_profile_lookup(defense_profiles, opponent, pos)
    context = _team_usage_context(profiles, team, team_rating)
    team_score = game_projection["home_score"] if home_away == "Home" else game_projection["away_score"]
    opponent_score = game_projection["away_score"] if home_away == "Home" else game_projection["home_score"]
    margin = team_score - opponent_score
    plays = clamp(context["plays"] + (game_projection["total"] - 45.0) * 0.10, 55, 74)
    weather_pass_penalty = min(0.04, max(0.0, -weather_adjustment) * 0.0055)
    pass_rate = clamp(context["pass_rate"] - 0.0055 * margin - weather_pass_penalty, 0.43, 0.72)
    dropbacks = plays * pass_rate
    league_sack = 0.067
    expected_sack_rate = clamp(
        league_sack + 0.55 * (_num(team_rating.get("Sack Rate Allowed", league_sack), league_sack) - league_sack)
        + 0.65 * _num(opponent_rating.get("Sack/Pressure Edge", 0), 0)
        - 0.004 * _num(team_rating.get("OL Adjustment", 0), 0)
        + 0.004 * _num(opponent_rating.get("Front Seven Adjustment", 0), 0), 0.035, 0.125
    )
    expected_sacks = dropbacks * expected_sack_rate
    team_pass_attempts = dropbacks - expected_sacks
    team_rush_attempts = plays * (1.0 - pass_rate) * 0.97
    pass_matchup_factor = clamp(1.0 + 0.65 * (_num(team_rating.get("Pass EPA/DB")) - _num(opponent_rating.get("Pass Def EPA Edge"))), 0.84, 1.16)
    rush_matchup_factor = clamp(1.0 + 0.62 * (_num(team_rating.get("Rush EPA/Play")) - _num(opponent_rating.get("Rush Def EPA Edge"))), 0.84, 1.16)
    weather_factor = clamp(1.0 + weather_adjustment / 38.0, 0.84, 1.02)
    pressure_index = _num(defense.get("pressure_index", 1.0), 1.0)
    pressure_efficiency_factor = clamp(1.0 - 0.10 * (1.0 / max(pressure_index, 0.7) - 1.0), 0.90, 1.06)

    rows: list[dict[str, Any]] = []
    games = _num(profile.get("games", 0), 0)
    data_confidence = clamp(39 + games * 4.5 + _num(team_rating.get("Data Confidence", 50)) * 0.30, 38, 96)
    matchup_confidence = clamp(_num(defense.get("scheme_confidence", 45), 45), 30, 96)

    def add_market(market: str, raw_projection: float, matchup_index: float, attempts: float = 0.0, completions: float = 0.0, targets: float = 0.0, receptions: float = 0.0, routes: float = 0.0, route_participation: float = 0.0, tprr: float = 0.0, efficiency: float = 0.0, coverage_matchup: float = 1.0, reason: str = "") -> None:
        calibration = _prop_calibration_adjustment(player, pos, market, opponent)
        projection = max(0.0, raw_projection + calibration["adjustment"])
        if raw_projection > 0 and market in ["Passing Yards", "Rushing Yards", "Receiving Yards"]:
            efficiency *= projection / raw_projection
        role_conf = _role_confidence({**role_profile, "position": pos}, play_probability, slot, market)
        route_conf = clamp(35 + 48 * clamp(route_participation, 0, 1) + min(17, games * 2.0), 35, 96) if pos in ["RB", "WR", "TE"] else role_conf
        cal_conf = clamp(35 + min(55, calibration["sample"] * 1.8), 35, 90)
        reliability = clamp(0.38 * role_conf + 0.22 * route_conf + 0.20 * data_confidence + 0.12 * matchup_confidence + 0.08 * cal_conf, 35, 95)
        key = (_normalize_name(player), market)
        market_data = market_lines.get(key, {})
        rows.append({
            "Team": team, "Opponent": opponent, "Home/Away": home_away, "Player": player, "Position": pos, "Slot": slot,
            "Market": market, "Raw Projection": round(max(0.0, raw_projection), 2), "Calibration Adjustment": round(calibration["adjustment"], 2),
            "Projection": round(projection, 2), "Fair Line": _fair_line(projection, market), "Market Line": market_data.get("line", np.nan),
            "Over Odds": market_data.get("over_odds", -110), "Under Odds": market_data.get("under_odds", -110), "Line Source": market_data.get("source", ""),
            "Role Confidence": round(role_conf, 1), "Route Confidence": round(route_conf, 1), "Matchup Confidence": round(matchup_confidence, 1),
            "Data Confidence": round(data_confidence, 1), "Calibration Sample": int(calibration["sample"]), "Reliability": round(reliability, 1),
            "Distribution": "Pending evaluation", "Matchup Index": round(matchup_index, 3), "Coverage Matchup": round(coverage_matchup, 3),
            "Projected Team Plays": round(plays, 1), "Projected Dropbacks": round(dropbacks, 1), "Expected Sacks": round(expected_sacks, 2),
            "Projected Pass Attempts": round(team_pass_attempts, 1), "Projected Rush Attempts": round(team_rush_attempts, 1),
            "Projected Routes": round(routes, 2), "Route Participation": round(route_participation, 3), "Targets Per Route": round(tprr, 3),
            "Projected Player Attempts": round(attempts, 2), "Projected Completions": round(completions, 2), "Projected Targets": round(targets, 2), "Projected Receptions": round(receptions, 2),
            "Efficiency": round(efficiency, 3), "Confluence": f"{reason}{' • ' + role_note if role_note else ''}",
            "_sd": _prop_sd(market, projection, reliability), "_play_probability": play_probability,
        })

    if pos == "QB":
        attempt_share = _num(role.get("attempt_share", profile.get("attempt_share", defaults["attempt_share"])), defaults["attempt_share"])
        pass_attempts = team_pass_attempts * clamp(attempt_share, 0.78, 1.0) * play_probability
        comp_prior = _position_prior(pos, "completion_rate", 0.645)
        completion_rate = _regressed_rate(_num(profile.get("completion_rate", comp_prior), comp_prior), _num(profile.get("attempts", 0)), comp_prior, 190)
        cpoe = _num(profile.get("ngs_passing_completion_percentage_above_expectation", 0), 0)
        if abs(cpoe) > 1: cpoe /= 100.0
        throwaway = _num(profile.get("throwaway_rate", 0.045), 0.045)
        completion_rate = clamp(completion_rate + 0.16 * cpoe - 0.04 * (throwaway - 0.045) - 0.07 * _num(opponent_rating.get("Pass Def EPA Edge")) - weather_pass_penalty * 0.35, 0.50, 0.77)
        pass_ypa = _regressed_rate(_num(profile.get("pass_ypa", 7.05), 7.05), _num(profile.get("attempts", 0)), 7.05, 230)
        deep_rate = clamp(_num(profile.get("deep_attempt_rate", 0.18), 0.18), 0.08, 0.32)
        depth_index = deep_rate * _num(defense.get("deep_pass_index", 1.0), 1.0) + (1 - deep_rate) * _num(defense.get("short_pass_index", 1.0), 1.0)
        pass_yards_index = _num(defense.get("passing_yards_index", 1.0), 1.0)
        adjusted_ypa = clamp(pass_ypa * pass_matchup_factor * pressure_efficiency_factor * (0.58 + 0.22 * pass_yards_index + 0.20 * depth_index) * weather_factor, 5.0, 9.6)
        completions = pass_attempts * completion_rate
        pass_yards = pass_attempts * adjusted_ypa
        pass_td_rate = _regressed_rate(_num(profile.get("pass_td_rate", 0.045), 0.045), _num(profile.get("attempts", 0)), 0.045, 270)
        passing_tds = pass_attempts * pass_td_rate * clamp(team_score / 22.5, 0.68, 1.42)
        int_rate = _regressed_rate(_num(profile.get("interception_rate", 0.024), 0.024), _num(profile.get("attempts", 0)), 0.024, 290)
        int_worthy = _num(profile.get("interception_worthy_rate", int_rate * 1.55), int_rate * 1.55)
        blended_int_rate = 0.65 * int_rate + 0.35 * clamp(int_worthy * 0.62, 0.008, 0.06)
        interceptions = pass_attempts * blended_int_rate * clamp(1.0 + 8 * (_num(opponent_rating.get("Takeaway Rate", 0.024)) - 0.024), 0.74, 1.38)
        qb_carry_share = _num(role.get("carry_share", profile.get("carry_share", defaults["carry_share"])), defaults["carry_share"])
        rush_attempts = max(_num(profile.get("carries_pg", 3.5), 3.5), team_rush_attempts * clamp(qb_carry_share, 0.04, 0.32))
        rush_attempts *= play_probability * clamp(1.0 + _num(opponent_rating.get("Sack/Pressure Edge", 0)) * 1.5 - margin * 0.004, 0.78, 1.28)
        qb_ypc = _regressed_rate(_num(profile.get("rush_ypc", 4.6), 4.6), _num(profile.get("carries", 0)), 4.6, 75)
        rush_index = _num(defense.get("rushing_yards_index", 1.0), 1.0)
        qb_rush_ypc = clamp(qb_ypc * rush_matchup_factor * (0.76 + 0.24 * rush_index), 2.2, 7.5)
        add_market("Passing Attempts", pass_attempts, _num(defense.get("attempts_index", 1.0), 1.0), attempts=pass_attempts, efficiency=1.0, reason="Projected dropbacks minus sacks • game script • weather • starter probability")
        add_market("Passing Completions", completions, _num(defense.get("attempts_index", 1.0), 1.0), attempts=pass_attempts, completions=completions, efficiency=completion_rate, reason="Attempts × charting/coverage-adjusted completion probability")
        add_market("Passing Yards", pass_yards, pass_yards_index, attempts=pass_attempts, efficiency=adjusted_ypa, coverage_matchup=depth_index, reason="Attempts × adjusted YPA • pressure/front matchup • depth profile • weather")
        add_market("Passing TDs", passing_tds, pass_yards_index, attempts=pass_attempts, efficiency=pass_td_rate, reason="Pass volume • scoring environment • regressed TD rate")
        add_market("Interceptions", interceptions, _num(opponent_rating.get("Takeaway Rate", 0.024), 0.024) / 0.024, attempts=pass_attempts, efficiency=blended_int_rate, reason="Attempts • INT-worthy charting • opponent takeaways")
        add_market("Rushing Attempts", rush_attempts, rush_index, attempts=rush_attempts, efficiency=1.0, reason="Designed usage • pressure/scramble environment • game script")
        add_market("Rushing Yards", rush_attempts * qb_rush_ypc, rush_index, attempts=rush_attempts, efficiency=qb_rush_ypc, reason="Rush attempts × adjusted YPC • pressure • run defense")

    elif pos == "RB":
        carry_share = _num(role.get("carry_share", profile.get("carry_share", defaults["carry_share"])), defaults["carry_share"])
        carries = team_rush_attempts * clamp(carry_share, 0.06, 0.82) * play_probability
        rush_prior = _position_prior(pos, "rush_ypc", 4.25)
        raw_ypc = _regressed_rate(_num(profile.get("rush_ypc", rush_prior), rush_prior), _num(profile.get("carries", 0)), rush_prior, 125)
        ryoe = _num(profile.get("ngs_rushing_rush_yards_over_expected_per_att", 0), 0)
        player_box = _num(profile.get("ngs_rushing_percent_attempts_gte_eight_defenders", 20), 20)
        if player_box > 1.5: player_box /= 100.0
        defense_box = _num(defense.get("box_rate", 0.25), 0.25)
        stuff_index = _num(defense.get("stuff_index", 1.0), 1.0)
        explosive_index = _num(defense.get("rush_explosive_index", 1.0), 1.0)
        rush_index = _num(defense.get("rushing_yards_index", 1.0), 1.0)
        box_factor = clamp(1.0 - 0.20 * (0.55 * player_box + 0.45 * defense_box - 0.25), 0.90, 1.06)
        ypc = clamp((raw_ypc + 0.32 * ryoe) * rush_matchup_factor * (0.52 + 0.22 * rush_index + 0.14 * stuff_index + 0.12 * explosive_index) * box_factor, 2.8, 6.4)
        rushing_yards = carries * ypc
        route_part = clamp(_num(role.get("route_participation", profile.get("route_participation", defaults["route_participation"])), defaults["route_participation"]), 0.08, 0.82)
        tprr = clamp(_num(role.get("targets_per_route", profile.get("targets_per_route", defaults["targets_per_route"])), defaults["targets_per_route"]), 0.10, 0.38)
        routes = dropbacks * route_part * play_probability
        target_share = _num(role.get("target_share", profile.get("target_share", defaults["target_share"])), defaults["target_share"])
        targets = 0.58 * (team_pass_attempts * clamp(target_share, 0.02, 0.28) * play_probability) + 0.42 * (routes * tprr)
        catch_prior = _position_prior(pos, "catch_rate", 0.755)
        catch_rate = _regressed_rate(_num(profile.get("catch_rate", catch_prior), catch_prior), _num(profile.get("targets", 0)), catch_prior, 85)
        catchable = _num(profile.get("catchable_target_rate", 0.79), 0.79)
        drop = _num(profile.get("drop_rate", 0.06), 0.06)
        catch_rate = clamp(0.72 * catch_rate + 0.28 * catchable * (1 - drop), 0.55, 0.91)
        receptions = targets * catch_rate
        ypt_prior = _position_prior(pos, "yards_per_target", 6.15)
        ypt = _regressed_rate(_num(profile.get("yards_per_target", ypt_prior), ypt_prior), _num(profile.get("targets", 0)), ypt_prior, 95)
        rec_index = _num(defense.get("receiving_yards_index", 1.0), 1.0)
        short_index = _num(defense.get("short_pass_index", 1.0), 1.0)
        adjusted_ypt = clamp(ypt * (0.84 + 0.16 * pass_matchup_factor) * (0.55 + 0.25 * rec_index + 0.20 * short_index) * weather_factor, 4.0, 9.0)
        receiving_yards = targets * adjusted_ypt
        add_market("Rushing Attempts", carries, _num(defense.get("carries_index", 1.0), 1.0), attempts=carries, efficiency=1.0, reason="Team rush volume • current backfield share • game script")
        add_market("Rushing Yards", rushing_yards, rush_index, attempts=carries, efficiency=ypc, coverage_matchup=box_factor, reason="Carries × adjusted YPC • OL/front • box rate • NGS RYOE")
        add_market("Targets", targets, _num(defense.get("targets_index", 1.0), 1.0), targets=targets, routes=routes, route_participation=route_part, tprr=tprr, efficiency=1.0, reason="Estimated routes × TPRR blended with team target share")
        add_market("Receptions", receptions, _num(defense.get("receptions_index", 1.0), 1.0), targets=targets, receptions=receptions, routes=routes, route_participation=route_part, tprr=tprr, efficiency=catch_rate, reason="Target distribution × charting-adjusted catch probability")
        add_market("Receiving Yards", receiving_yards, rec_index, targets=targets, receptions=receptions, routes=routes, route_participation=route_part, tprr=tprr, efficiency=adjusted_ypt, coverage_matchup=short_index, reason="Targets × adjusted YPT • checkdown/short coverage matchup")

    else:
        route_part = clamp(_num(role.get("route_participation", profile.get("route_participation", defaults["route_participation"])), defaults["route_participation"]), 0.20, 0.98)
        tprr = clamp(_num(role.get("targets_per_route", profile.get("targets_per_route", defaults["targets_per_route"])), defaults["targets_per_route"]), 0.08, 0.36)
        routes = dropbacks * route_part * play_probability
        target_share = _num(role.get("target_share", profile.get("target_share", defaults["target_share"])), defaults["target_share"])
        targets = 0.56 * (team_pass_attempts * clamp(target_share, 0.04, 0.38) * play_probability) + 0.44 * (routes * tprr)
        catch_prior = _position_prior(pos, "catch_rate", 0.64)
        catch_rate = _regressed_rate(_num(profile.get("catch_rate", catch_prior), catch_prior), _num(profile.get("targets", 0)), catch_prior, 105)
        separation = _num(profile.get("ngs_receiving_avg_separation", 2.9), 2.9)
        catchable = _num(profile.get("catchable_target_rate", 0.72), 0.72)
        drop = _num(profile.get("drop_rate", 0.07), 0.07)
        contested = _num(profile.get("contested_target_rate", 0.16), 0.16)
        man_rate, zone_rate = _num(defense.get("man_rate", 0.35), 0.35), _num(defense.get("zone_rate", 0.65), 0.65)
        man_targets, zone_targets = _num(profile.get("man_targets", 0), 0), _num(profile.get("zone_targets", 0), 0)
        base_ypt = _position_prior(pos, "yards_per_target", 8.0)
        man_ypt = _regressed_rate(_num(profile.get("man_ypt", base_ypt), base_ypt), man_targets, base_ypt, 35)
        zone_ypt = _regressed_rate(_num(profile.get("zone_ypt", base_ypt), base_ypt), zone_targets, base_ypt, 35)
        coverage_factor = clamp((man_rate * man_ypt + zone_rate * zone_ypt) / max(base_ypt, 0.1), 0.84, 1.18)
        man_catch = _regressed_rate(_num(profile.get("man_catch_rate", catch_prior), catch_prior), man_targets, catch_prior, 35)
        zone_catch = _regressed_rate(_num(profile.get("zone_catch_rate", catch_prior), catch_prior), zone_targets, catch_prior, 35)
        coverage_catch = man_rate * man_catch + zone_rate * zone_catch
        catch_rate = clamp(0.55 * catch_rate + 0.20 * coverage_catch + 0.18 * catchable * (1 - drop) + 0.012 * (separation - 2.9) - 0.035 * (contested - 0.16) - weather_pass_penalty * 0.28, 0.44, 0.82)
        receptions = targets * catch_rate
        ypt = _regressed_rate(_num(profile.get("yards_per_target", base_ypt), base_ypt), _num(profile.get("targets", 0)), base_ypt, 115)
        adot = _num(profile.get("ngs_receiving_avg_intended_air_yards", profile.get("adot", 9.0 if pos == "WR" else 7.0)), 9.0 if pos == "WR" else 7.0)
        deep_weight = clamp((adot - 5.0) / 12.0, 0.05, 0.85)
        depth_index = deep_weight * _num(defense.get("deep_pass_index", 1.0), 1.0) + (1 - deep_weight) * _num(defense.get("short_pass_index", 1.0), 1.0)
        rec_index = _num(defense.get("receiving_yards_index", 1.0), 1.0)
        yac_oe = _num(profile.get("ngs_receiving_avg_yac_above_expectation", 0), 0)
        adjusted_ypt = clamp((ypt + 0.18 * yac_oe) * pass_matchup_factor * pressure_efficiency_factor * (0.46 + 0.20 * rec_index + 0.18 * depth_index + 0.16 * coverage_factor) * weather_factor, 5.0, 13.0)
        receiving_yards = targets * adjusted_ypt
        add_market("Targets", targets, _num(defense.get("targets_index", 1.0), 1.0), targets=targets, routes=routes, route_participation=route_part, tprr=tprr, efficiency=1.0, coverage_matchup=coverage_factor, reason="Estimated routes × TPRR • current role • man/zone tendency")
        add_market("Receptions", receptions, _num(defense.get("receptions_index", 1.0), 1.0), targets=targets, receptions=receptions, routes=routes, route_participation=route_part, tprr=tprr, efficiency=catch_rate, coverage_matchup=coverage_factor, reason="Target distribution × catchability/drop/separation • man/zone coverage")
        add_market("Receiving Yards", receiving_yards, rec_index, targets=targets, receptions=receptions, routes=routes, route_participation=route_part, tprr=tprr, efficiency=adjusted_ypt, coverage_matchup=coverage_factor, reason="Targets × adjusted YPT • depth/YAC • coverage shell • pressure/weather")
    return rows


def _secret_value(*names: str) -> str:
    for name in names:
        try:
            value = st.secrets.get(name, "")
            if value:
                return str(value)
        except Exception:
            pass
        value = os.environ.get(name, "")
        if value:
            return str(value)
    return ""


@st.cache_data(ttl=900, show_spinner=False)
def _odds_api_prop_lines(api_key: str, away_team: str, home_team: str) -> dict[tuple[str, str], dict[str, Any]]:
    if not api_key:
        return {}
    base = "https://api.the-odds-api.com/v4"
    try:
        events_response = requests.get(f"{base}/sports/americanfootball_nfl/events", params={"apiKey": api_key}, timeout=25)
        events_response.raise_for_status()
        events = events_response.json()
        away_name, home_name = NFL_TEAM_NAMES.get(away_team, away_team), NFL_TEAM_NAMES.get(home_team, home_team)
        event = next((item for item in events if item.get("away_team") == away_name and item.get("home_team") == home_name), None)
        if not event:
            return {}
        markets = ",".join(PROP_MARKET_API_KEYS.values())
        odds_response = requests.get(
            f"{base}/sports/americanfootball_nfl/events/{event.get('id')}/odds",
            params={"apiKey": api_key, "regions": "us", "markets": markets, "oddsFormat": "american"},
            timeout=35,
        )
        odds_response.raise_for_status()
        payload = odds_response.json()
        observations: dict[tuple[str, str], dict[str, list[float]]] = {}
        for bookmaker in payload.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                internal_market = API_KEY_TO_PROP_MARKET.get(market.get("key", ""))
                if not internal_market:
                    continue
                for outcome in market.get("outcomes", []):
                    player = _normalize_name(outcome.get("description", ""))
                    if not player or outcome.get("point") is None:
                        continue
                    key = (player, internal_market)
                    bucket = observations.setdefault(key, {"line": [], "over_odds": [], "under_odds": []})
                    bucket["line"].append(float(outcome.get("point")))
                    side = str(outcome.get("name", "")).lower()
                    if side == "over":
                        bucket["over_odds"].append(float(outcome.get("price", -110)))
                    elif side == "under":
                        bucket["under_odds"].append(float(outcome.get("price", -110)))
        output = {}
        for key, bucket in observations.items():
            if not bucket["line"]:
                continue
            output[key] = {
                "line": round(float(np.median(bucket["line"])), 1),
                "over_odds": int(round(float(np.median(bucket["over_odds"])))) if bucket["over_odds"] else -110,
                "under_odds": int(round(float(np.median(bucket["under_odds"])))) if bucket["under_odds"] else -110,
                "source": "Odds API consensus",
            }
        return output
    except Exception as exc:
        st.session_state["nfl_prop_odds_error"] = str(exc)
        return {}


def _grade_prop(probability: float, probability_edge_value: float, reliability: float, direction: str, role_confidence: float, market: str) -> str:
    under_penalty = 0.012 if direction == "Under" else 0.0
    count_penalty = 0.006 if market in ["Passing TDs", "Interceptions"] else 0.0
    if probability >= 0.60 + under_penalty + count_penalty and probability_edge_value >= 0.055 and reliability >= 75 and role_confidence >= 76:
        return "A Prop"
    if probability >= 0.57 + under_penalty + count_penalty and probability_edge_value >= 0.035 and reliability >= 68 and role_confidence >= 68:
        return "B Prop"
    if probability >= 0.54 and probability_edge_value >= 0.015 and reliability >= 60:
        return "Lean"
    return "Non-Edge Prop"


def _evaluate_prop_rows(rows: pd.DataFrame) -> pd.DataFrame:
    if rows is None or rows.empty: return pd.DataFrame()
    output = []
    for _, row in rows.iterrows():
        item = row.to_dict()
        projection = _num(item.get("Projection", 0), 0)
        samples, distribution = _simulate_prop_distribution(item)
        item["Distribution"] = distribution
        item["Fair Line"] = _fair_line(float(np.median(samples)), _safe_text(item.get("Market", "")))
        item["_sd"] = float(np.std(samples, ddof=1))
        line = _num(item.get("Market Line", np.nan), np.nan)
        if not (math.isfinite(line) and line >= 0):
            item.update({"Pick": "Projection only", "Pick Odds": np.nan, "Model Probability": np.nan, "Push Probability": np.nan, "Implied Probability": np.nan, "Probability Edge": np.nan, "Projection Edge": np.nan, "Expected Value": np.nan, "Grade": "No market line", "Track": False})
            output.append(item); continue
        p_over = float(np.mean(samples > line)); p_under = float(np.mean(samples < line)); p_push = max(0.0, 1.0 - p_over - p_under)
        over_odds, under_odds = _int(item.get("Over Odds", -110), -110), _int(item.get("Under Odds", -110), -110)
        ev_over = _expected_value_with_push(p_over, p_under, over_odds)
        ev_under = _expected_value_with_push(p_under, p_over, under_odds)
        direction = "Over" if ev_over >= ev_under else "Under"
        probability = p_over if direction == "Over" else p_under
        lose_probability = p_under if direction == "Over" else p_over
        odds = over_odds if direction == "Over" else under_odds
        raw_over = american_implied_probability(over_odds); raw_under = american_implied_probability(under_odds)
        over_novig = raw_over / max(raw_over + raw_under, 1e-9)
        implied = over_novig if direction == "Over" else 1.0 - over_novig
        conditional_probability = probability / max(1.0 - p_push, 1e-9)
        probability_edge_value = conditional_probability - implied
        grade = _grade_prop(conditional_probability, probability_edge_value, _num(item.get("Reliability", 50), 50), direction, _num(item.get("Role Confidence", 50), 50), _safe_text(item.get("Market", "")))
        item.update({
            "Line Source": _safe_text(item.get("Line Source", "")) or "Manual market line", "Pick": f"{direction} {line:.1f}", "Pick Odds": odds,
            "Model Probability": round(conditional_probability, 4), "Push Probability": round(p_push, 4), "Implied Probability": round(implied, 4),
            "Probability Edge": round(probability_edge_value, 4), "Projection Edge": round(abs(projection - line), 2),
            "Expected Value": round(_expected_value_with_push(probability, lose_probability, odds), 4), "Grade": grade, "Track": grade in ["A Prop", "B Prop"],
        })
        output.append(item)
    return pd.DataFrame(output)


def _build_game_prop_rows(
    away_team: str, home_team: str, away_lineup: pd.DataFrame, home_lineup: pd.DataFrame,
    profiles: pd.DataFrame, defense_profiles: pd.DataFrame, away_rating: dict[str, Any],
    home_rating: dict[str, Any], projection: dict[str, float], weather_adjustment: float,
    market_lines: dict[tuple[str, str], dict[str, Any]],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    away_roles = _expected_lineup_roles(away_lineup, profiles, away_team)
    home_roles = _expected_lineup_roles(home_lineup, profiles, home_team)
    for team, opponent, home_away, lineup, rating, opponent_rating, team_roles in [
        (away_team, home_team, "Away", away_lineup, away_rating, home_rating, away_roles),
        (home_team, away_team, "Home", home_lineup, home_rating, away_rating, home_roles),
    ]:
        skill = lineup[
            (lineup["Unit"].astype(str) == "Offense")
            & (lineup["Slot"].astype(str).isin(["QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE"]))
            & (~lineup["Player"].astype(str).str.upper().isin(["", "TBD", "UNKNOWN"]))
        ] if lineup is not None and not lineup.empty else pd.DataFrame()
        for _, player_row in skill.iterrows():
            rows.extend(_project_player_markets(
                _safe_text(player_row.get("Player", "")), _safe_text(player_row.get("Position", "")),
                _safe_text(player_row.get("Slot", "")), team, opponent, home_away, lineup, profiles,
                defense_profiles, rating, opponent_rating, projection, weather_adjustment, market_lines, team_roles,
            ))
    return pd.DataFrame(rows)


def _replace_game_rows(tab: str, columns: list[str], rows: list[dict[str, Any]], game_id: str, slate_date: str) -> bool:
    existing = read_sheet(tab, columns)
    new_rows = pd.DataFrame(rows)
    for column in columns:
        if column not in new_rows.columns:
            new_rows[column] = ""
    new_rows = new_rows[columns]
    if existing is not None and not existing.empty:
        mask = ~((existing["Game ID"].astype(str) == str(game_id)) & (existing["Date"].astype(str) == str(slate_date)))
        output = pd.concat([existing[mask], new_rows], ignore_index=True)
    else:
        output = new_rows
    return bool(write_sheet(tab, output, columns))




def _upsert_rows(tab: str, columns: list[str], rows: list[dict[str, Any]], key_columns: list[str]) -> bool:
    if not rows:
        return True
    existing = read_sheet(tab, columns)
    new_rows = pd.DataFrame(rows)
    for column in columns:
        if column not in new_rows.columns:
            new_rows[column] = ""
    new_rows = new_rows[columns]
    if existing is None or existing.empty:
        output = new_rows
    else:
        output = existing.copy()
        for row in rows:
            mask = pd.Series(True, index=output.index)
            for column in key_columns:
                if column not in output.columns:
                    mask &= False
                    continue
                mask &= output[column].astype(str) == str(row.get(column, ""))
            output = output[~mask].copy()
        output = pd.concat([output, new_rows], ignore_index=True)
    return bool(write_sheet(tab, output, columns))


def _game_tracker_rows(
    slate_date: str, season: int, week: int, game_id: str, game: str,
    spread_pick: str, spread_probability: float, spread_grade: str, spread_confluence: int,
    total_pick: str, total_probability: float, total_grade: str, total_confluence: int,
    ml_pick: str, ml_probability: float, ml_odds: int, ml_grade: str, ml_confluence: int,
    reliability: float, data_confidence: float, personnel_confidence: float,
    projected_away: float, projected_home: float, notes: str,
) -> list[dict[str, Any]]:
    candidates = [
        ("Spread", spread_pick, -110, spread_probability, spread_grade, spread_confluence),
        ("Total", total_pick, -110, total_probability, total_grade, total_confluence),
        ("Moneyline", ml_pick, ml_odds, ml_probability, ml_grade, ml_confluence),
    ]
    rows = []
    for bet_type, selection, odds, probability, grade, confluence in candidates:
        if not _is_graded_game_play(grade):
            continue
        implied = american_implied_probability(odds)
        rows.append({
            "Date": slate_date, "Season": season, "Week": week, "Game ID": game_id, "Game": game,
            "Bet Type": bet_type, "Selection": selection, "Odds/Line": odds,
            "Model Probability": round(probability, 4), "Implied Probability": round(implied, 4),
            "Edge": round(probability - implied, 4), "Expected Value": round(expected_value_per_unit(probability, odds), 4),
            "Grade": grade, "Confluence": confluence, "Result": "Pending", "Reliability": reliability,
            "Data Confidence": data_confidence, "Personnel Confidence": personnel_confidence,
            "Projected Away": round(projected_away, 1), "Projected Home": round(projected_home, 1),
            "Model Version": MODEL_VERSION, "Notes": notes,
        })
    return rows


def _graded_prop_tracker_rows(
    evaluated: pd.DataFrame, slate_date: str, season: int, week: int,
    game_id: str, game: str, notes: str,
) -> list[dict[str, Any]]:
    if evaluated is None or evaluated.empty:
        return []
    graded = evaluated[evaluated["Grade"].astype(str).isin(["A Prop", "B Prop"])].copy()
    rows = _prop_rows_for_storage(graded, slate_date, season, week, game_id, game, notes)
    for item in rows:
        item.update({
            "Result": "Pending", "Actual Attempts": "", "Actual Completions": "", "Actual Targets": "", "Actual Receptions": "",
            "Actual Result": "", "Opportunity Error": "", "Efficiency Error": "", "Projection Residual": "",
        })
    return rows

def _prop_rows_for_storage(
    evaluated: pd.DataFrame, slate_date: str, season: int, week: int, game_id: str,
    game: str, notes: str,
) -> list[dict[str, Any]]:
    rows = []
    if evaluated is None or evaluated.empty:
        return rows
    for _, row in evaluated.iterrows():
        item = row.to_dict()
        item.update({
            "Date": slate_date, "Season": season, "Week": week, "Game ID": game_id,
            "Game": game, "Model Version": MODEL_VERSION, "Notes": notes,
        })
        item.pop("_sd", None)
        item.pop("_play_probability", None)
        item.pop("Track", None)
        rows.append(item)
    return rows



def _actual_market_values(stat_row: dict[str, Any], market: str) -> tuple[float | None, float | None, float | None]:
    attempts = _num(stat_row.get("attempts", 0), 0)
    carries = _num(stat_row.get("carries", 0), 0)
    targets = _num(stat_row.get("targets", 0), 0)
    receptions = _num(stat_row.get("receptions", 0), 0)
    values = {
        "Passing Attempts": attempts,
        "Passing Completions": _num(stat_row.get("completions", 0), 0),
        "Passing Yards": _num(stat_row.get("passing_yards", 0), 0),
        "Passing TDs": _num(stat_row.get("passing_tds", 0), 0),
        "Interceptions": _num(stat_row.get("interceptions", 0), 0),
        "Rushing Attempts": carries,
        "Rushing Yards": _num(stat_row.get("rushing_yards", 0), 0),
        "Targets": targets,
        "Receptions": receptions,
        "Receiving Yards": _num(stat_row.get("receiving_yards", 0), 0),
    }
    if market not in values:
        return None, None, None
    actual = values[market]
    if market in ["Passing Attempts", "Passing Completions", "Passing Yards", "Passing TDs", "Interceptions"]:
        opportunity = attempts
        efficiency = actual / attempts if attempts > 0 and market != "Passing Attempts" else (1.0 if market == "Passing Attempts" else 0.0)
    elif market in ["Rushing Attempts", "Rushing Yards"]:
        opportunity = carries
        efficiency = actual / carries if carries > 0 and market != "Rushing Attempts" else (1.0 if market == "Rushing Attempts" else 0.0)
    else:
        opportunity = targets
        if market == "Receptions":
            efficiency = receptions / targets if targets > 0 else 0.0
        elif market == "Receiving Yards":
            efficiency = actual / targets if targets > 0 else 0.0
        else:
            efficiency = 1.0
    return float(actual), float(opportunity), float(efficiency)


def _projected_opportunity_for_market(row: dict[str, Any], market: str) -> float:
    if market in ["Passing Attempts", "Passing Completions", "Passing Yards", "Passing TDs", "Interceptions", "Rushing Attempts", "Rushing Yards"]:
        return _num(row.get("Projected Player Attempts", 0), 0)
    return _num(row.get("Projected Targets", 0), 0)


def _bet_result_from_actual(pick: str, line: float, actual: float) -> str:
    if abs(actual - line) < 1e-9:
        return "Push"
    direction = _safe_text(pick).split(" ", 1)[0].lower()
    if direction == "over":
        return "Win" if actual > line else "Loss"
    if direction == "under":
        return "Win" if actual < line else "Loss"
    return ""


def _auto_update_prop_tracker() -> tuple[int, str]:
    if not sheets_ready():
        return 0, "Google Sheets is not configured."
    tracker = read_sheet(PROP_TRACKER_TAB, PROP_TRACKER_COLUMNS)
    if tracker is None or tracker.empty:
        return 0, "No prop tracker rows yet."
    for column in [
        "Result", "Actual Attempts", "Actual Completions", "Actual Targets", "Actual Receptions", "Actual Result",
        "Opportunity Error", "Efficiency Error", "Projection Residual",
    ]:
        if column in tracker.columns:
            tracker[column] = tracker[column].astype(object)
    updated_rows = 0
    calibration_rows: list[dict[str, Any]] = []
    season_cache: dict[int, pd.DataFrame] = {}

    for index, row in tracker.iterrows():
        if _safe_text(row.get("Actual Result", "")):
            continue
        market = _safe_text(row.get("Market", ""))
        season = _int(row.get("Season", 0), 0)
        week = _int(row.get("Week", 0), 0)
        if season <= 0 or week <= 0:
            continue
        if season not in season_cache:
            stats = _load_player_stats_season(season)
            if stats is None or stats.empty:
                season_cache[season] = pd.DataFrame()
            else:
                stats = stats.copy()
                stats["player_name_norm"] = _player_name_column(stats).map(_normalize_name)
                stats["team_norm"] = _player_team_column(stats)
                season_cache[season] = stats
        stats = season_cache[season]
        if stats.empty:
            continue
        player_name = _normalize_name(row.get("Player", ""))
        team = _normalize_team(row.get("Team", ""))
        matches = stats[
            (stats["player_name_norm"] == player_name)
            & (pd.to_numeric(stats.get("week"), errors="coerce") == week)
        ].copy()
        if team:
            team_matches = matches[matches["team_norm"] == team]
            if not team_matches.empty:
                matches = team_matches
        if matches.empty:
            continue
        stat_row = matches.iloc[-1].to_dict()
        actual, actual_opportunity, actual_efficiency = _actual_market_values(stat_row, market)
        if actual is None:
            continue
        projection = _num(row.get("Projection", 0), 0)
        market_line = _num(row.get("Market Line", np.nan), np.nan)
        projected_opportunity = _projected_opportunity_for_market(row.to_dict(), market)
        projected_efficiency = _num(row.get("Efficiency", 0), 0)
        if market.startswith("Passing") or market == "Interceptions":
            tracker.at[index, "Actual Attempts"] = _num(stat_row.get("attempts", 0), 0)
        elif market.startswith("Rushing"):
            tracker.at[index, "Actual Attempts"] = _num(stat_row.get("carries", 0), 0)
        else:
            tracker.at[index, "Actual Attempts"] = ""
        tracker.at[index, "Actual Completions"] = _num(stat_row.get("completions", 0), 0)
        tracker.at[index, "Actual Targets"] = _num(stat_row.get("targets", 0), 0)
        tracker.at[index, "Actual Receptions"] = _num(stat_row.get("receptions", 0), 0)
        tracker.at[index, "Actual Result"] = round(actual, 3)
        tracker.at[index, "Opportunity Error"] = round(actual_opportunity - projected_opportunity, 3)
        tracker.at[index, "Efficiency Error"] = round(actual_efficiency - projected_efficiency, 4)
        tracker.at[index, "Projection Residual"] = round(actual - projection, 3)
        if math.isfinite(market_line):
            tracker.at[index, "Result"] = _bet_result_from_actual(_safe_text(row.get("Pick", "")), market_line, actual)
        calibration_rows.append({
            "Date": str(date.today()), "Season": season, "Week": week, "Game ID": row.get("Game ID", ""),
            "Player": row.get("Player", ""), "Position": row.get("Position", ""), "Market": market,
            "Projection": projection, "Market Line": market_line, "Actual Result": actual,
            "Projected Opportunity": round(projected_opportunity, 3), "Actual Opportunity": round(actual_opportunity, 3),
            "Projected Efficiency": round(projected_efficiency, 4), "Actual Efficiency": round(actual_efficiency, 4),
            "Opportunity Error": round(actual_opportunity - projected_opportunity, 3),
            "Efficiency Error": round(actual_efficiency - projected_efficiency, 4),
            "Projection Residual": round(actual - projection, 3), "Opponent": row.get("Opponent", ""),
            "Role Confidence": row.get("Role Confidence", ""), "Reliability": row.get("Reliability", ""),
            "Model Version": row.get("Model Version", MODEL_VERSION),
        })
        updated_rows += 1

    if updated_rows:
        write_sheet(PROP_TRACKER_TAB, tracker, PROP_TRACKER_COLUMNS)
        existing = read_sheet(PROP_CALIBRATION_TAB, PROP_CALIBRATION_COLUMNS)
        new_calibration = pd.DataFrame(calibration_rows, columns=PROP_CALIBRATION_COLUMNS)
        if existing is not None and not existing.empty:
            combined = pd.concat([existing, new_calibration], ignore_index=True)
            dedupe_columns = ["Season", "Week", "Game ID", "Player", "Market", "Model Version"]
            combined = combined.drop_duplicates(subset=dedupe_columns, keep="last")
        else:
            combined = new_calibration
        write_sheet(PROP_CALIBRATION_TAB, combined, PROP_CALIBRATION_COLUMNS)
    return updated_rows, f"Updated {updated_rows} completed prop result(s)."


def _render_prop_tracker() -> None:
    st.subheader("NFL Player Prop Tracker")
    try:
        updated, message = _auto_update_prop_tracker()
        if updated:
            st.success(message)
    except Exception as exc:
        st.warning(f"Automatic prop-result update could not finish: {exc}")
    dataframe = read_sheet(PROP_TRACKER_TAB, PROP_TRACKER_COLUMNS)
    if dataframe is not None and not dataframe.empty:
        st.dataframe(dataframe.iloc[::-1], use_container_width=True, hide_index=True)
    else:
        st.info("No tracked player props yet.")


def _load_automatic_bundle(season: int, week: int) -> dict[str, Any]:
    key = f"nfl_auto_bundle_{int(season)}_{int(week)}"
    existing = st.session_state.get(key)
    if isinstance(existing, dict) and existing:
        return existing
    with st.spinner("Loading lineups, current roles, snap/route estimates, charting and player prop data..."):
        profiles = _blended_player_profiles(int(season), int(week))
        bundle = {
            "depth": _latest_depth_chart(int(season)),
            "injuries": _injury_lookup(int(season), int(week)),
            "player_values": _blend_player_values(int(season), int(week)),
            "profiles": profiles,
            "defense_profiles": _defense_position_profiles(int(season), int(week)),
        }
    st.session_state[key] = bundle
    return bundle


# -----------------------------------------------------------------------------
# App pages
# -----------------------------------------------------------------------------

def _render_build() -> None:
    _inject_clean_builder_styles()
    st.subheader("NFL Automated Slate + Prop Builder")
    st.caption("Opening this page automatically resolves the slate, ratings, current roles, estimated routes, charting/coverage matchups and calibrated player projections.")

    auto_season = _current_nfl_season()
    with st.expander("Slate controls and test mode", expanded=False):
        season = int(st.number_input(
            "Season", min_value=1999, max_value=2032, value=int(st.session_state.get("nfl_build_season_value", auto_season)),
            step=1, key="nfl_build_season_auto",
        ))
        st.session_state["nfl_build_season_value"] = season
        mode = st.radio("Matchup mode", ["Scheduled Slate", "Test Matchup"], horizontal=True, key="nfl_matchup_mode_auto")
        if st.button("Force refresh automatic NFL data", use_container_width=True, key="nfl_force_refresh"):
            for cached_loader in [
                _load_schedule_live, _load_schedule_csv_fallback, _load_player_stats_season,
                _load_depth_charts_season, _load_injuries_season, _load_sleeper_players, _load_snap_counts_season,
                _load_nextgen_season, _load_ftn_charting_season, _load_participation_season,
                _season_charting_features, _season_player_profiles, _blended_player_profiles,
                _defense_position_profiles, _prop_calibration_data, _rolling_home_field_model,
            ]:
                try:
                    cached_loader.clear()
                except Exception:
                    pass
            for key in list(st.session_state):
                if str(key).startswith(("nfl_auto_bundle_", "nfl_auto_ratings_", "nfl_lineup_")):
                    del st.session_state[key]
            st.rerun()

    schedule = _schedule_for_season(season)
    if schedule is not None and not schedule.empty and sheets_ready():
        schedule_save_key = f"nfl_schedule_auto_saved_{season}"
        if not st.session_state.get(schedule_save_key):
            try:
                write_sheet(SCHEDULE_TAB, schedule, SCHEDULE_COLUMNS)
                st.session_state[schedule_save_key] = True
            except Exception:
                pass

    valid_types = ["REG", "POST", "WC", "DIV", "CON", "SB", "PRE"]
    eligible_schedule = schedule[schedule["Game Type"].astype(str).str.upper().isin(valid_types)].copy() if schedule is not None and not schedule.empty else pd.DataFrame()
    manual_mode = mode == "Test Matchup"
    selected_schedule_row: pd.Series | None = None

    if not manual_mode and not eligible_schedule.empty:
        slate_dates = _available_slate_dates(eligible_schedule)
        default_date = _default_slate_date(eligible_schedule)
        default_index = slate_dates.index(default_date) if default_date in slate_dates else 0
        slate_date = st.selectbox(
            "Slate date", slate_dates, index=default_index, format_func=lambda value: value.strftime("%A, %B %-d, %Y"),
            key=f"nfl_slate_date_{season}",
        )
        date_mask = _schedule_date_series(eligible_schedule) == slate_date
        day_schedule = eligible_schedule[date_mask].copy()
        if day_schedule.empty:
            st.warning("No games were found on the selected date. Test Matchup mode is available in Slate controls.")
            manual_mode = True
        else:
            day_schedule = day_schedule.sort_values(["Game Time", "Away Team", "Home Team"])
            labels = [
                f"{row['Away Team']} at {row['Home Team']} — {_safe_text(row.get('Game Time', '')) or 'time TBD'}"
                for _, row in day_schedule.iterrows()
            ]
            selected_label = st.selectbox("Game", labels, index=0, key=f"nfl_scheduled_game_{season}_{slate_date}")
            selected_schedule_row = day_schedule.iloc[labels.index(selected_label)]
            week = _int(selected_schedule_row.get("Week", 1), 1)
            slate_date_str = str(slate_date)
            st.markdown(f"**{len(day_schedule)} game{'s' if len(day_schedule) != 1 else ''} on this slate** • Week {week}")
    elif not manual_mode:
        st.warning("The automatic NFL schedule is currently unavailable, so Test Matchup mode is active.")
        manual_mode = True

    if manual_mode:
        slate_date_str = str(date.today())
        week = int(st.number_input("Projection week", min_value=1, max_value=22, value=1, step=1, key="nfl_test_week_auto"))

    ratings = _ensure_automated_ratings(season, week)
    teams = sorted(set(NFL_TEAMS) | set(ratings.get("Team", pd.Series(dtype=str)).astype(str).map(_normalize_team).tolist()))

    if manual_mode:
        c1, c2 = st.columns(2)
        with c1:
            away_team = st.selectbox("Away team", teams, index=0, key="nfl_test_away_auto")
        with c2:
            home_options = [team for team in teams if team != away_team]
            home_team = st.selectbox("Home team", home_options, index=min(1, len(home_options) - 1), key="nfl_test_home_auto")
        game_id = f"TEST-{season}-{week}-{away_team}-{home_team}"
    else:
        away_team = _normalize_team(selected_schedule_row.get("Away Team", ""))
        home_team = _normalize_team(selected_schedule_row.get("Home Team", ""))
        game_id = _safe_text(selected_schedule_row.get("Game ID", "")) or f"{season}_{week}_{away_team}_{home_team}"

    away_rating = _team_row(ratings, away_team, season, week)
    home_rating = _team_row(ratings, home_team, season, week)

    with st.expander("Automated team ratings and manual overrides", expanded=False):
        st.dataframe(pd.DataFrame([
            {
                "Team": away_team, "Power": _num(away_rating.get("Power Rating")),
                "Off EPA": _num(away_rating.get("Off EPA/Play")), "Def EPA Edge": _num(away_rating.get("Def EPA Edge")),
                "Pass EPA": _num(away_rating.get("Pass EPA/DB")), "Rush EPA": _num(away_rating.get("Rush EPA/Play")),
                "Pace": _num(away_rating.get("Pace", 64)), "PPG": _num(away_rating.get("Points/Game", 22.5)),
                "Data Confidence": _num(away_rating.get("Data Confidence", 35)),
            },
            {
                "Team": home_team, "Power": _num(home_rating.get("Power Rating")),
                "Off EPA": _num(home_rating.get("Off EPA/Play")), "Def EPA Edge": _num(home_rating.get("Def EPA Edge")),
                "Pass EPA": _num(home_rating.get("Pass EPA/DB")), "Rush EPA": _num(home_rating.get("Rush EPA/Play")),
                "Pace": _num(home_rating.get("Pace", 64)), "PPG": _num(home_rating.get("Points/Game", 22.5)),
                "Data Confidence": _num(home_rating.get("Data Confidence", 35)),
            },
        ]), use_container_width=True, hide_index=True)
        ca, ch = st.columns(2)
        with ca:
            away_rating = _team_adjustment_inputs("Away", away_rating)
        with ch:
            home_rating = _team_adjustment_inputs("Home", home_rating)

    defaults = _schedule_defaults(selected_schedule_row)
    market_key = re.sub(r"[^A-Za-z0-9]+", "_", game_id)
    hfa_info = _automatic_home_field_advantage(season, week, home_team, selected_schedule_row)
    automatic_home_field = _num(hfa_info.get("value"), HOME_FIELD_PRIOR_POINTS)
    st.markdown("### Market and game environment")
    c1, c2, c3 = st.columns(3)
    with c1:
        home_spread = st.number_input("Home spread", value=float(defaults["home_spread"]), step=0.5, key=f"nfl_home_spread_{market_key}")
        home_ml = st.number_input("Home moneyline", value=int(defaults["home_ml"]), step=5, key=f"nfl_home_ml_{market_key}")
    with c2:
        market_total = st.number_input("Game total", value=float(defaults["total"]), step=0.5, key=f"nfl_total_{market_key}")
        away_ml = st.number_input("Away moneyline", value=int(defaults["away_ml"]), step=5, key=f"nfl_away_ml_{market_key}")
    with c3:
        st.metric("Automatic home field", f"{automatic_home_field:.1f} pts")
        precipitation = st.selectbox("Precipitation", ["None", "Rain", "Heavy Rain", "Snow", "Heavy Snow"], key=f"nfl_precip_{market_key}")
    st.caption(_safe_text(hfa_info.get("source", "Rolling home-field model")))

    roof_options = ["outdoors", "dome", "closed", "open"]
    roof_default = defaults["roof"].lower() if defaults["roof"].lower() in roof_options else "outdoors"
    c4, c5, c6 = st.columns(3)
    with c4:
        roof = st.selectbox("Roof", roof_options, index=roof_options.index(roof_default), key=f"nfl_roof_{market_key}")
    with c5:
        temperature = st.number_input("Temperature °F", value=float(defaults["temperature"]), step=1.0, key=f"nfl_temp_{market_key}")
    with c6:
        wind = st.number_input("Wind mph", value=float(defaults["wind"]), min_value=0.0, step=1.0, key=f"nfl_wind_{market_key}")

    away_rest = _num(defaults["away_rest"], 7)
    home_rest = _num(defaults["home_rest"], 7)
    automatic_rest_edge = clamp((home_rest - away_rest) * 0.18, -1.5, 1.5)
    with st.expander("Advanced matchup adjustments", expanded=False):
        override_home_field = st.checkbox(
            "Override automatic home-field value", value=False, key=f"nfl_override_hfa_{market_key}",
            help="Leave off to use the rolling league plus venue estimate. Turn on only for a known site-specific exception.",
        )
        if override_home_field:
            home_field = st.number_input(
                "Manual home-field points", value=float(automatic_home_field), step=0.1,
                min_value=0.0, max_value=5.0, key=f"nfl_hfa_{market_key}",
            )
        else:
            home_field = automatic_home_field
        manual_home_margin_adjustment = st.number_input("Manual home matchup adjustment", value=0.0, step=0.25, key=f"nfl_manual_margin_{market_key}")
        manual_total_adjustment = st.number_input("Manual total adjustment", value=0.0, step=0.25, key=f"nfl_manual_total_{market_key}")
        rest_adjustment = st.number_input("Home rest/travel adjustment", value=float(automatic_rest_edge), step=0.1, key=f"nfl_rest_{market_key}")
        notes = st.text_area("Notes", key=f"nfl_notes_{market_key}", placeholder="QB/practice status, role change, offensive-line change, coaching tendency...")

    bundle = _load_automatic_bundle(season, week)
    depth = bundle.get("depth", pd.DataFrame())
    injury_lookup = bundle.get("injuries", {})
    player_values = bundle.get("player_values", {})
    profiles = bundle.get("profiles", pd.DataFrame())
    defense_profiles = bundle.get("defense_profiles", pd.DataFrame())

    away_seed_lineup = _auto_lineup(away_team, season, week, depth, injury_lookup, player_values)
    home_seed_lineup = _auto_lineup(home_team, season, week, depth, injury_lookup, player_values)

    with st.expander("Lineups, injuries and role overrides", expanded=False):
        st.caption("Current QB/RB/WR/TE teams and depth order use the daily Sleeper player feed, with nflverse depth charts retained for detailed line positions. Injury status is merged automatically; manual play probability remains the final override.")
        if st.session_state.get("nfl_sleeper_error"):
            st.warning("The daily Sleeper roster/injury fallback is unavailable on this run. Verify current teams, starters and play probabilities manually before saving.")
        away_tab, home_tab = st.tabs([f"{away_team} lineup", f"{home_team} lineup"])
        with away_tab:
            away_lineup, away_lineup_summary = _lineup_editor(
                away_team, season, week, market_key, pd.DataFrame(), {}, {}, away_seed_lineup
            )
        with home_tab:
            home_lineup, home_lineup_summary = _lineup_editor(
                home_team, season, week, market_key, pd.DataFrame(), {}, {}, home_seed_lineup
            )

    weather_total_adjustment = _weather_adjustment(roof, temperature, wind, precipitation)
    reliability, data_confidence, personnel_confidence = _reliability(
        away_rating, home_rating, away_lineup_summary, home_lineup_summary, week, manual_mode
    )
    projection = _project_matchup(
        away_rating, home_rating, away_lineup_summary, home_lineup_summary,
        {
            "home_field": home_field, "home_rest_edge": rest_adjustment,
            "weather_total_adjustment": weather_total_adjustment,
            "manual_total_adjustment": manual_total_adjustment,
            "manual_home_margin_adjustment": manual_home_margin_adjustment,
        },
    )
    simulation = _simulate_game(
        projection, home_spread, market_total, reliability,
        _simulation_seed(game_id, away_team, home_team, season, week),
    )

    home_cover = simulation["home_cover"]
    spread_edge_home = projection["margin"] + home_spread
    if home_cover >= 0.5:
        spread_pick, spread_probability, spread_edge, spread_pick_home = f"{home_team} {home_spread:+.1f}", home_cover, spread_edge_home, True
    else:
        spread_pick, spread_probability, spread_edge, spread_pick_home = f"{away_team} {-home_spread:+.1f}", 1 - home_cover, -spread_edge_home, False
    spread_confluence, spread_reasons = _spread_confluence(
        spread_pick_home, spread_edge, away_rating, home_rating, away_lineup_summary, home_lineup_summary, reliability
    )
    spread_grade = _grade_spread(spread_probability, spread_edge, reliability, spread_confluence)

    over_probability = simulation["over"]
    if over_probability >= 0.5:
        total_pick, total_probability, total_edge, over_pick = f"Over {market_total:.1f}", over_probability, projection["total"] - market_total, True
    else:
        total_pick, total_probability, total_edge, over_pick = f"Under {market_total:.1f}", 1 - over_probability, market_total - projection["total"], False
    total_confluence, total_reasons = _total_confluence(
        over_pick, total_edge, projection, away_rating, home_rating, weather_total_adjustment, reliability
    )
    total_grade = _grade_total_direction(total_pick, total_probability, total_edge, reliability, total_confluence)

    home_win = simulation["home_win"]
    if home_win >= 0.5:
        ml_pick, ml_probability, ml_odds, ml_pick_home = home_team, home_win, home_ml, True
    else:
        ml_pick, ml_probability, ml_odds, ml_pick_home = away_team, 1 - home_win, away_ml, False
    ml_edge = probability_edge(ml_probability, ml_odds)
    ml_confluence, ml_reasons = _moneyline_confluence(
        ml_pick_home, ml_edge, away_rating, home_rating, away_lineup_summary, home_lineup_summary, reliability
    )
    ml_grade = _grade_moneyline(ml_probability, ml_edge, reliability, ml_confluence)

    st.divider()
    _metric_cards(away_team, home_team, projection, reliability)
    margin_team = home_team if projection["margin"] >= 0 else away_team
    margin_text = f"{margin_team} by {abs(projection['margin']):.1f}"
    _market_card("Spread", margin_text, spread_pick, spread_probability, f"{spread_edge:+.1f} pts", spread_grade, spread_confluence, spread_reasons)
    _market_card("Total", f"{projection['total']:.1f} points", total_pick, total_probability, f"{total_edge:+.1f} pts", total_grade, total_confluence, total_reasons)
    _market_card("Moneyline", f"{ml_pick} {ml_probability:.1%} win probability", ml_pick, ml_probability, f"{ml_edge:+.1%}", ml_grade, ml_confluence, ml_reasons)

    game = f"{away_team} at {home_team}"
    slate_row = {
        "Date": slate_date_str, "Season": season, "Week": week, "Game ID": game_id, "Game": game,
        "Away Team": away_team, "Home Team": home_team, "Projected Away": round(projection["away_score"], 1),
        "Projected Home": round(projection["home_score"], 1), "Projected Margin": round(projection["margin"], 1),
        "Projected Total": round(projection["total"], 1), "Away Score Low": round(simulation["away_low"], 1),
        "Away Score High": round(simulation["away_high"], 1), "Home Score Low": round(simulation["home_low"], 1),
        "Home Score High": round(simulation["home_high"], 1), "Market Home Spread": home_spread,
        "Market Total": market_total, "Away ML": away_ml, "Home ML": home_ml,
        "Spread Pick": spread_pick, "Spread Probability": round(spread_probability, 4), "Spread Edge": round(spread_edge, 2),
        "Spread Grade": spread_grade, "Spread Confluence": spread_confluence, "Total Pick": total_pick,
        "Total Probability": round(total_probability, 4), "Total Edge": round(total_edge, 2), "Total Grade": total_grade,
        "Total Confluence": total_confluence, "ML Pick": ml_pick, "ML Probability": round(ml_probability, 4),
        "ML Odds": ml_odds, "ML Edge": round(ml_edge, 4), "ML Grade": ml_grade, "ML Confluence": ml_confluence,
        "Reliability": reliability, "Data Confidence": data_confidence, "Personnel Confidence": personnel_confidence,
        "Previous Season Weight": away_rating.get("Previous Season Weight", ""), "Current Season Weight": away_rating.get("Current Season Weight", ""),
        "Away Offensive Absence": away_lineup_summary["offense_absence"], "Away Defensive Absence": away_lineup_summary["defense_absence"],
        "Home Offensive Absence": home_lineup_summary["offense_absence"], "Home Defensive Absence": home_lineup_summary["defense_absence"],
        "Weather Adjustment": weather_total_adjustment, "Roof": roof, "Temperature": temperature, "Wind": wind,
        "Model Version": MODEL_VERSION, "Notes": notes,
    }

    st.markdown("### Automatic player props")
    odds_api_key = _secret_value("THE_ODDS_API_KEY", "ODDS_API_KEY")
    market_lines = _odds_api_prop_lines(odds_api_key, away_team, home_team) if odds_api_key else {}
    if odds_api_key and market_lines:
        st.caption(f"Loaded {len(market_lines)} consensus player market lines. Edit any line or price below to override it.")
    elif odds_api_key:
        st.caption("No matching player lines are posted yet. Projections are ready and market lines can be entered below when available.")
    else:
        st.caption("Projections load automatically. Add THE_ODDS_API_KEY to Streamlit secrets for automatic sportsbook lines, or enter lines manually below.")

    prop_base = _build_game_prop_rows(
        away_team, home_team, away_lineup, home_lineup, profiles, defense_profiles,
        away_rating, home_rating, projection, weather_total_adjustment, market_lines,
    )
    evaluated_props = pd.DataFrame()
    if prop_base.empty:
        st.info("No skill-position players were resolved. Open Lineups, injuries and role overrides to correct the QB/RB/WR/TE card.")
    else:
        with st.expander("Sportsbook prop lines and prices", expanded=False):
            st.caption("Only use this section when automatic player lines are unavailable or need an override.")
            input_columns = [
                "Team", "Player", "Market", "Projection", "Market Line", "Over Odds", "Under Odds", "Line Source",
            ]
            prop_inputs = st.data_editor(
                prop_base,
                use_container_width=True,
                hide_index=True,
                key=f"nfl_prop_inputs_{market_key}",
                column_order=input_columns,
                disabled=[column for column in input_columns if column not in ["Market Line", "Over Odds", "Under Odds"]],
                column_config={
                    "Projection": st.column_config.NumberColumn(format="%.1f"),
                    "Market Line": st.column_config.NumberColumn("Sportsbook Line", min_value=0.0, step=0.5, format="%.1f"),
                    "Over Odds": st.column_config.NumberColumn(step=5),
                    "Under Odds": st.column_config.NumberColumn(step=5),
                },
            )
        evaluated_props = _evaluate_prop_rows(prop_inputs)
        _render_prop_projection_cards(evaluated_props)

    st.divider()
    st.caption("This single action saves the game, lineup snapshot and every prop projection. Only qualifying graded bets are placed in the trackers.")
    if st.button("Save Game & Graded Plays", type="primary", use_container_width=True, key=f"nfl_save_everything_{market_key}"):
        prop_projection_rows = _prop_rows_for_storage(
            evaluated_props, slate_date_str, season, week, game_id, game, notes
        )
        game_tracker_rows = _game_tracker_rows(
            slate_date_str, season, week, game_id, game,
            spread_pick, spread_probability, spread_grade, spread_confluence,
            total_pick, total_probability, total_grade, total_confluence,
            ml_pick, ml_probability, ml_odds, ml_grade, ml_confluence,
            reliability, data_confidence, personnel_confidence,
            projection["away_score"], projection["home_score"], notes,
        )
        prop_tracker_rows = _graded_prop_tracker_rows(
            evaluated_props, slate_date_str, season, week, game_id, game, notes
        )

        game_saved = _replace_game_rows(SLATE_TAB, SLATE_COLUMNS, [slate_row], game_id, slate_date_str)
        props_saved = _replace_game_rows(
            PROP_SLATE_TAB, PROP_PROJECTION_COLUMNS, prop_projection_rows, game_id, slate_date_str
        )
        game_tracker_saved = _replace_game_rows(
            TRACKER_TAB, TRACKER_COLUMNS, game_tracker_rows, game_id, slate_date_str
        )
        prop_tracker_saved = _replace_game_rows(
            PROP_TRACKER_TAB, PROP_TRACKER_COLUMNS, prop_tracker_rows, game_id, slate_date_str
        )
        if game_saved:
            _save_lineups(away_lineup, home_lineup, away_team, home_team, season, week, game_id)

        if all([game_saved, props_saved, game_tracker_saved, prop_tracker_saved]):
            st.success(
                f"Saved the game and {len(prop_projection_rows)} prop projections. "
                f"Tracker additions: {len(game_tracker_rows)} game bet(s) and {len(prop_tracker_rows)} prop bet(s)."
            )
        else:
            st.error("One or more Google Sheets updates did not finish. Check the Sheets connection and try the single save button again.")


def _render_ratings() -> None:
    st.subheader("NFL Team Ratings")
    st.caption("Automated nflverse ratings remain editable. Manual adjustments are measured in projected points.")
    ratings = _load_ratings()
    if ratings.empty:
        ratings = _seed_neutral_ratings()
    uploaded = st.file_uploader("Import NFL team ratings CSV", type=["csv"], key="nfl_ratings_csv")
    if uploaded is not None:
        imported = pd.read_csv(uploaded)
        for column in RATING_COLUMNS:
            if column not in imported.columns:
                imported[column] = ""
        ratings = imported[RATING_COLUMNS]
    edited = st.data_editor(ratings, use_container_width=True, hide_index=True, num_rows="dynamic", key="nfl_ratings_editor")
    if st.button("Save NFL Ratings", type="primary", use_container_width=True):
        edited["Updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        if write_sheet(RATINGS_TAB, edited, RATING_COLUMNS):
            st.success("NFL ratings saved.")


def _render_setup() -> None:
    st.subheader("NFL Data Diagnostics")
    st.caption("The Build page now refreshes these sources automatically. Use this page only for diagnostics, cache resets or manual overrides.")
    st.metric("Google Sheets", "Connected" if sheets_ready() else "Not configured")
    st.metric("nflreadpy", "Installed" if nfl is not None else "Missing from deployment")
    st.caption("NFL data now streams the PBP parquet to /tmp and reads only the model columns, preventing the neutral-rating fallback caused by full-file memory pressure.")

    if st.button("Clear NFL download cache", use_container_width=True):
        try:
            if nfl is not None and hasattr(nfl, "clear_cache"):
                nfl.clear_cache()
            for season_to_clear in range(1999, 2031):
                cache_file = _pbp_cache_path(season_to_clear)
                partial_file = f"{cache_file}.part"
                for path_to_clear in [cache_file, partial_file]:
                    if os.path.exists(path_to_clear):
                        os.remove(path_to_clear)
            try:
                _load_sleeper_players.clear()
            except Exception:
                pass
            for key in list(st.session_state):
                if str(key).startswith(("nfl_live_lineup_source_", "nfl_pbp_", "nfl_schedule_", "nfl_auto_bundle_", "nfl_auto_ratings_", "nfl_sleeper_")):
                    del st.session_state[key]
            st.success("NFL download, compact PBP, daily roster/injury, and lineup-session caches cleared.")
        except Exception as exc:
            st.warning(f"Could not fully clear the NFL cache: {exc}")

    c1, c2 = st.columns(2)
    with c1:
        season = st.number_input("Rating season", min_value=1999, max_value=2030, value=DEFAULT_SEASON, step=1, key="nfl_setup_season")
    with c2:
        projection_week = st.number_input("Build ratings for week", min_value=1, max_value=22, value=1, step=1, key="nfl_setup_week")

    if st.button("Build / Refresh Automated NFL Ratings", type="primary", use_container_width=True):
        try:
            with st.spinner("Downloading compact NFL data and building progressive team ratings..."):
                ratings = _build_automated_ratings(int(season), int(projection_week))
            diagnostics = st.session_state.get("nfl_last_rating_build", {})
            quality = diagnostics.get("quality", _ratings_quality(ratings))
            st.caption(
                f"Prior season: {diagnostics.get('prior_pbp_rows', 0):,} PBP rows, "
                f"{diagnostics.get('prior_schedule_games', 0)} schedule games • "
                f"Source: {diagnostics.get('prior_source', 'unknown')}"
            )
            if not quality.get("valid"):
                st.error(
                    "The source did not produce real team separation, so these ratings were NOT saved. "
                    + str(quality.get("message", ""))
                )
                prior_error = st.session_state.get(f"nfl_pbp_error_{int(season) - 1}", "")
                schedule_error = st.session_state.get(f"nfl_schedule_error_{int(season) - 1}", "")
                if prior_error:
                    st.code(f"PBP source error: {prior_error}")
                if schedule_error:
                    st.code(f"Schedule source error: {schedule_error}")
                st.dataframe(ratings, use_container_width=True, hide_index=True)
            elif write_sheet(RATINGS_TAB, ratings, RATING_COLUMNS):
                log = read_sheet(MODEL_LOG_TAB, MODEL_LOG_COLUMNS)
                new_log = pd.DataFrame([{
                    "Date": str(date.today()), "Model Version": MODEL_VERSION,
                    "Change": (
                        f"Built {season} Week {projection_week} ratings using {season - 1}/{season} progressive blend. "
                        f"Validation: {quality.get('message', '')}."
                    ),
                }])
                output = pd.concat([log, new_log], ignore_index=True) if log is not None and not log.empty else new_log
                write_sheet(MODEL_LOG_TAB, output, MODEL_LOG_COLUMNS)
                st.success(f"Saved differentiated automated ratings for all {len(ratings)} teams.")
                st.info(str(quality.get("message", "Ratings validation passed.")))
                st.dataframe(ratings, use_container_width=True, hide_index=True)
        except Exception as exc:
            st.error(f"Could not build NFL ratings: {exc}")

    if st.button("Sync NFL Schedule", use_container_width=True):
        try:
            with st.spinner("Loading the nflverse schedule..."):
                schedule = _schedule_for_season(int(season), refresh=True)
            if schedule.empty:
                st.error("The schedule source returned no games for this season.")
            elif write_sheet(SCHEDULE_TAB, schedule, SCHEDULE_COLUMNS):
                st.success(f"Saved {len(schedule)} games for the {season} season.")
        except Exception as exc:
            st.error(f"Could not sync the NFL schedule: {exc}")

    st.markdown("### What this version automatically uses")
    st.write(
        "Previous-season and current-season play-by-play, EPA, success rate, pass/rush splits, explosive plays, "
        "turnovers, sacks, pace, scoring, red-zone performance, schedules, current-team/depth-order data, injury and practice status, "
        "offensive snap shares, estimated route participation, targets per route, Next Gen passing/rushing/receiving metrics, "
        "FTN charting, historical participation/coverage tendencies, opponent positional allowances and rolling residual calibration."
    )
    st.info(
        "Week 1 is 100% prior season. Current-season weight rises to roughly 15% in Week 2, 30% in Week 3, "
        "45% in Week 4, and eventually 90%+ while retaining a small prior-season stabilizer."
    )


def _table(tab: str, columns: list[str], title: str) -> None:
    st.subheader(title)
    dataframe = read_sheet(tab, columns)
    if dataframe is not None and not dataframe.empty:
        st.dataframe(dataframe.iloc[::-1], use_container_width=True, hide_index=True)
    else:
        st.info("No rows yet.")


def render() -> None:
    st.caption("NFL v3.1 automated slate • data-derived home field • in-depth QB/RB/WR/TE props")
    page = st.radio(
        "NFL section",
        ["Build", "Prop Slate", "Prop Tracker", "Slate", "Tracker", "Team Ratings", "Schedule", "Lineups", "Setup"],
        horizontal=True,
        key="nfl_nav",
    )
    if page == "Build":
        _render_build()
    elif page == "Prop Slate":
        _table(PROP_SLATE_TAB, PROP_PROJECTION_COLUMNS, "NFL Player Prop Projections")
    elif page == "Prop Tracker":
        _render_prop_tracker()
    elif page == "Slate":
        _table(SLATE_TAB, SLATE_COLUMNS, "NFL Daily Slate")
    elif page == "Tracker":
        _table(TRACKER_TAB, TRACKER_COLUMNS, "NFL Bet Tracker")
    elif page == "Team Ratings":
        _render_ratings()
    elif page == "Schedule":
        _table(SCHEDULE_TAB, SCHEDULE_COLUMNS, "NFL Schedule")
    elif page == "Lineups":
        _table(LINEUP_TAB, LINEUP_COLUMNS, "NFL Lineup Snapshots")
    else:
        _render_setup()
