"""EZPZ Picks NFL model builder.

Version 1 converts the original neutral/manual foundation into a functional,
lineup-aware NFL game model while keeping the same shared Streamlit, Google
Sheets, and multi-sport architecture used by the EZPZ admin app.

Primary data source: nflverse through nflreadpy.
"""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import math
import re
from typing import Any

import numpy as np
import pandas as pd
import requests
import streamlit as st

from shared.modeling import (
    american_implied_probability,
    clamp,
    expected_value_per_unit,
    normal_cdf,
    probability_edge,
)
from shared.storage import append_row, read_sheet, sheets_ready, write_sheet

try:
    import nflreadpy as nfl
except Exception:
    nfl = None


MODEL_VERSION = "nfl-v1.0-lineup-engine-2026-07-14"
DEFAULT_SEASON = 2026
DEFAULT_PRIOR_SEASON = DEFAULT_SEASON - 1

RATINGS_TAB = "nfl_team_ratings"
SLATE_TAB = "nfl_daily_slate"
TRACKER_TAB = "nfl_bet_tracker"
SCHEDULE_TAB = "nfl_schedule"
LINEUP_TAB = "nfl_lineup_snapshots"
MODEL_LOG_TAB = "nfl_model_change_log"

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
    "Base Impact", "Manual Impact", "Effective Play Probability", "Absence Cost",
    "Model Version",
]

MODEL_LOG_COLUMNS = ["Date", "Model Version", "Change"]

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
    "IR": 0.0,
    "PUP": 0.0,
    "DOUBTFUL": 0.20,
    "QUESTIONABLE": 0.65,
    "PROBABLE": 0.92,
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


@st.cache_data(ttl=21600, show_spinner=False)
def _load_pbp_season(season: int) -> pd.DataFrame:
    _require_nflreadpy()
    try:
        return _to_pandas(nfl.load_pbp(int(season)))
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=21600, show_spinner=False)
def _load_player_stats_season(season: int) -> pd.DataFrame:
    _require_nflreadpy()
    try:
        return _to_pandas(nfl.load_player_stats(int(season), summary_level="week"))
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=21600, show_spinner=False)
def _load_depth_charts_season(season: int) -> pd.DataFrame:
    _require_nflreadpy()
    try:
        return _to_pandas(nfl.load_depth_charts(int(season)))
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=10800, show_spinner=False)
def _load_injuries_season(season: int) -> pd.DataFrame:
    _require_nflreadpy()
    try:
        return _to_pandas(nfl.load_injuries(int(season)))
    except Exception:
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


def _schedule_for_season(season: int, refresh: bool = False) -> pd.DataFrame:
    if refresh:
        _load_schedule_live.clear()
    try:
        live = _load_schedule_live(int(season))
        if not live.empty:
            return live
    except Exception:
        pass
    sheet = _load_schedule_sheet()
    if sheet.empty:
        return pd.DataFrame(columns=SCHEDULE_COLUMNS)
    season_values = pd.to_numeric(sheet["Season"], errors="coerce")
    return sheet[season_values == int(season)].copy().reset_index(drop=True)


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
        rows = []
        for team in teams:
            ppg = _num(schedule_stats.loc[team, "Points/Game"], 22.5) if team in schedule_stats.index else 22.5
            papg = _num(schedule_stats.loc[team, "Points Allowed/Game"], 22.5) if team in schedule_stats.index else 22.5
            games = _num(schedule_stats.loc[team, "Games"], 0) if team in schedule_stats.index else 0
            rows.append({
                "Team": team, "Power Rating": ppg - papg, "Points/Game": ppg,
                "Points Allowed/Game": papg, "Games": games, "Pace": 64.0,
                "Off Success Rate": 0.43, "Explosive Rate": 0.105,
                "Turnover Rate": 0.024, "Takeaway Rate": 0.024,
                "Sack Rate Allowed": 0.067, "Red Zone TD Rate": 0.56,
                "Data Confidence": clamp(30 + games * 4, 30, 85),
            })
        return pd.DataFrame(rows).set_index("Team")

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
    return metrics.sort_index()


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
        row["Source"] = f"nflverse {season - 1}/{season} progressive blend"
        row["Updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        rows.append(row)
    return pd.DataFrame(rows, columns=RATING_COLUMNS)


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
    return _blend_team_metrics(prior_metrics, current_metrics, int(season), int(projection_week))


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
    depth = _load_depth_charts_season(season)
    if depth.empty and season > 2001:
        depth = _load_depth_charts_season(season - 1)
    if depth.empty:
        return depth
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
    return out


def _injury_lookup(season: int, week: int) -> dict[tuple[str, str], dict[str, Any]]:
    injuries = _load_injuries_season(season)
    if injuries.empty:
        return {}
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
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for _, row in out.iterrows():
        key = (_normalize_team(row.get("team_norm", "")), _normalize_name(row.get("name_norm", "")))
        status = _safe_text(_first_existing(row, "report_status", "practice_status", default="")).upper()
        lookup[key] = {
            "status": status.title() if status else "Healthy",
            "injury": _safe_text(_first_existing(row, "report_primary_injury", "practice_primary_injury", default="")),
            "auto_probability": _status_probability(status),
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

    home_field = settings.get("home_field", 1.6)
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

def _metric_cards(away: str, home: str, projection: dict[str, float], reliability: float) -> None:
    st.markdown(
        f"""
        <div class="builder-metric-grid">
          <div class="builder-metric-card">
            <div class="builder-metric-label">{away} projected score</div>
            <div class="builder-metric-value builder-metric-value--big">{projection['away_score']:.1f}</div>
          </div>
          <div class="builder-metric-card">
            <div class="builder-metric-label">{home} projected score</div>
            <div class="builder-metric-value builder-metric-value--big">{projection['home_score']:.1f}</div>
          </div>
          <div class="builder-metric-card">
            <div class="builder-metric-label">Projected margin</div>
            <div class="builder-metric-value">{home} {projection['margin']:+.1f}</div>
          </div>
          <div class="builder-metric-card">
            <div class="builder-metric-label">Projected total</div>
            <div class="builder-metric-value">{projection['total']:.1f}</div>
          </div>
          <div class="builder-metric-card builder-metric-card--wide">
            <div class="builder-metric-label">NFL reliability</div>
            <div class="builder-metric-value">{reliability:.0f}/100</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _grade_chip(grade: str) -> str:
    grade_upper = grade.upper()
    if grade_upper.startswith("A ") or grade_upper.startswith("STRONG"):
        css = "ez-chip-green"
    elif grade_upper.startswith("B ") or grade_upper in ["OVER", "UNDER"]:
        css = "ez-chip-yellow"
    else:
        css = "ez-chip-red"
    return f'<span class="ez-chip {css}">{grade}</span>'


def _market_card(title: str, selection: str, probability: float, edge: str, grade: str, confluence: int, reasons: list[str]) -> None:
    border = "ez-card-green" if grade.startswith("A ") or grade.startswith("Strong") else "ez-card-yellow" if grade.startswith("B ") or grade in ["Over", "Under"] else "ez-card-red"
    reason_text = " • ".join(reasons) if reasons else "No supporting confluence checks"
    st.markdown(
        f"""
        <div class="ez-card {border}">
          <div class="ez-title">{title}: {selection}</div>
          <div class="ez-sub">Model probability {probability:.1%} • Edge {edge}</div>
          {_grade_chip(grade)}
          <span class="ez-chip ez-chip-yellow">{confluence}/5+ confluence</span>
          <div class="ez-kv"><span>Supporting factors</span><span>{reason_text}</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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
) -> tuple[pd.DataFrame, dict[str, float]]:
    lineup = _auto_lineup(team, season, week, depth, injury_lookup, player_values)
    edited = st.data_editor(
        lineup,
        use_container_width=True,
        hide_index=True,
        key=f"nfl_lineup_{game_key}_{team}",
        column_config={
            "Unit": st.column_config.TextColumn("Unit", disabled=True, width="small"),
            "Slot": st.column_config.TextColumn("Slot", disabled=True, width="small"),
            "Player": st.column_config.TextColumn("Player", width="medium"),
            "Position": st.column_config.TextColumn("Pos", width="small"),
            "Depth Rank": st.column_config.NumberColumn("Depth", min_value=1, max_value=10, step=1, width="small"),
            "Injury Status": st.column_config.SelectboxColumn(
                "Status",
                options=["Healthy", "Active", "Full", "Limited", "Questionable", "Doubtful", "Out", "IR", "PUP", "Unknown"],
                width="medium",
            ),
            "Auto Play Probability": st.column_config.NumberColumn("Auto Play %", min_value=0.0, max_value=1.0, step=0.05, format="%.2f"),
            "Manual Play Probability": st.column_config.NumberColumn("Manual Play %", min_value=0.0, max_value=1.0, step=0.05, format="%.2f"),
            "Base Impact": st.column_config.NumberColumn("Base Pts", min_value=0.0, max_value=8.0, step=0.05, format="%.2f"),
            "Manual Impact": st.column_config.NumberColumn("Extra Pts", min_value=-3.0, max_value=5.0, step=0.05, format="%.2f"),
        },
    )
    final, summary = _finalize_lineup(edited)
    st.caption(
        f"Estimated absence cost: offense −{summary['offense_absence']:.2f} points; "
        f"defense allows opponent +{summary['defense_absence']:.2f} points. Personnel confidence {summary['confidence']:.0f}/100."
    )
    return final, summary


def _schedule_defaults(row: pd.Series | None) -> dict[str, Any]:
    if row is None:
        return {
            "away_ml": 110, "home_ml": -130, "home_spread": -2.5, "total": 44.5,
            "roof": "outdoors", "temperature": 70.0, "wind": 6.0,
            "away_rest": 7.0, "home_rest": 7.0, "game_id": "test", "home_field": 1.6,
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
        "home_field": 0.0 if neutral_site else 1.6,
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
# App pages
# -----------------------------------------------------------------------------

def _render_build() -> None:
    st.subheader("NFL Matchup Builder")
    st.caption("Auto-loaded schedule or editable test matchup • progressive season blend • lineup/injury simulation")

    top1, top2 = st.columns(2)
    with top1:
        season = st.number_input("Season", min_value=1999, max_value=2030, value=DEFAULT_SEASON, step=1, key="nfl_build_season")
    schedule = _schedule_for_season(int(season))
    regular = schedule[schedule["Game Type"].astype(str).str.upper().isin(["REG", "POST", "WC", "DIV", "CON", "SB"])].copy() if not schedule.empty else schedule
    week_options = sorted(pd.to_numeric(regular.get("Week", pd.Series(dtype=float)), errors="coerce").dropna().astype(int).unique().tolist())
    if not week_options:
        week_options = list(range(1, 19))
    with top2:
        week = st.selectbox("Projection week", week_options, index=0, key="nfl_build_week")

    mode = st.radio(
        "Matchup mode",
        ["Scheduled Slate", "Test Matchup"],
        horizontal=True,
        key="nfl_matchup_mode",
    )

    selected_schedule_row: pd.Series | None = None
    manual_mode = mode == "Test Matchup"
    if not manual_mode:
        week_schedule = regular[pd.to_numeric(regular["Week"], errors="coerce") == int(week)].copy() if not regular.empty else pd.DataFrame()
        if week_schedule.empty:
            st.warning("No schedule rows were found for this week. Test Matchup mode is available below.")
            manual_mode = True
        else:
            labels = [f"{row['Away Team']} at {row['Home Team']} — {row['Game Date']}" for _, row in week_schedule.iterrows()]
            selected_label = st.selectbox("Game", labels, key="nfl_scheduled_game")
            selected_schedule_row = week_schedule.iloc[labels.index(selected_label)]

    ratings = _load_ratings()
    if ratings.empty:
        st.warning("No saved NFL ratings were found. Use Setup → Build/Refresh Automated Ratings before trusting projections.")
        ratings = _seed_neutral_ratings(int(season), int(week))

    teams = sorted(set(NFL_TEAMS) | set(ratings.get("Team", pd.Series(dtype=str)).astype(str).map(_normalize_team).tolist()))
    if manual_mode:
        c1, c2 = st.columns(2)
        with c1:
            away_team = st.selectbox("Away team", teams, index=0, key="nfl_test_away")
        with c2:
            home_options = [team for team in teams if team != away_team]
            home_team = st.selectbox("Home team", home_options, index=min(1, len(home_options) - 1), key="nfl_test_home")
        game_id = f"TEST-{season}-{week}-{away_team}-{home_team}"
    else:
        away_team = _normalize_team(selected_schedule_row.get("Away Team", ""))
        home_team = _normalize_team(selected_schedule_row.get("Home Team", ""))
        game_id = _safe_text(selected_schedule_row.get("Game ID", "")) or f"{season}_{week}_{away_team}_{home_team}"
        if st.checkbox("Override scheduled teams for testing", key="nfl_override_scheduled_teams"):
            c1, c2 = st.columns(2)
            with c1:
                away_team = st.selectbox("Replacement away team", teams, index=teams.index(away_team) if away_team in teams else 0, key="nfl_override_away")
            with c2:
                home_options = [team for team in teams if team != away_team]
                home_team = st.selectbox("Replacement home team", home_options, index=home_options.index(home_team) if home_team in home_options else 0, key="nfl_override_home")
            game_id = f"TEST-{season}-{week}-{away_team}-{home_team}"
            manual_mode = True

    away_rating = _team_row(ratings, away_team, int(season), int(week))
    home_rating = _team_row(ratings, home_team, int(season), int(week))

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
    st.markdown("### Market and game environment")
    c1, c2, c3 = st.columns(3)
    with c1:
        home_spread = st.number_input("Home spread", value=float(defaults["home_spread"]), step=0.5, key=f"nfl_home_spread_{market_key}")
        home_ml = st.number_input("Home moneyline", value=int(defaults["home_ml"]), step=5, key=f"nfl_home_ml_{market_key}")
    with c2:
        market_total = st.number_input("Game total", value=float(defaults["total"]), step=0.5, key=f"nfl_total_{market_key}")
        away_ml = st.number_input("Away moneyline", value=int(defaults["away_ml"]), step=5, key=f"nfl_away_ml_{market_key}")
    with c3:
        home_field = st.number_input("Home-field points", value=float(defaults.get("home_field", 1.6)), step=0.1, key=f"nfl_hfa_{market_key}")
        precipitation = st.selectbox("Precipitation", ["None", "Rain", "Heavy Rain", "Snow", "Heavy Snow"], key=f"nfl_precip_{market_key}")

    c4, c5, c6 = st.columns(3)
    with c4:
        roof = st.selectbox("Roof", ["outdoors", "dome", "closed", "open"], index=["outdoors", "dome", "closed", "open"].index(defaults["roof"].lower()) if defaults["roof"].lower() in ["outdoors", "dome", "closed", "open"] else 0, key=f"nfl_roof_{market_key}")
    with c5:
        temperature = st.number_input("Temperature °F", value=float(defaults["temperature"]), step=1.0, key=f"nfl_temp_{market_key}")
    with c6:
        wind = st.number_input("Wind mph", value=float(defaults["wind"]), min_value=0.0, step=1.0, key=f"nfl_wind_{market_key}")

    away_rest = _num(defaults["away_rest"], 7)
    home_rest = _num(defaults["home_rest"], 7)
    automatic_rest_edge = clamp((home_rest - away_rest) * 0.18, -1.5, 1.5)

    with st.expander("Advanced matchup adjustments", expanded=False):
        manual_home_margin_adjustment = st.number_input("Manual home matchup adjustment", value=0.0, step=0.25, key=f"nfl_manual_margin_{market_key}")
        manual_total_adjustment = st.number_input("Manual total adjustment", value=0.0, step=0.25, key=f"nfl_manual_total_{market_key}")
        rest_adjustment = st.number_input("Home rest/travel adjustment", value=float(automatic_rest_edge), step=0.1, key=f"nfl_rest_{market_key}")
        notes = st.text_area(
            "Notes",
            key=f"nfl_notes_{market_key}",
            placeholder="QB/practice status, offensive-line change, travel, coaching tendency, matchup note...",
        )

    st.markdown("### Expected lineups and injuries")
    try:
        depth = _latest_depth_chart(int(season))
        injury_lookup = _injury_lookup(int(season), int(week))
        player_values = _blend_player_values(int(season), int(week))
    except Exception as exc:
        depth = pd.DataFrame()
        injury_lookup = {}
        player_values = {}
        st.info(f"Live player data is not available yet, so editable generic lineup slots are being used: {exc}")

    away_tab, home_tab = st.tabs([f"{away_team} lineup", f"{home_team} lineup"])
    with away_tab:
        away_lineup, away_lineup_summary = _lineup_editor(
            away_team, int(season), int(week), market_key, depth, injury_lookup, player_values
        )
    with home_tab:
        home_lineup, home_lineup_summary = _lineup_editor(
            home_team, int(season), int(week), market_key, depth, injury_lookup, player_values
        )

    weather_total_adjustment = _weather_adjustment(roof, temperature, wind, precipitation)
    reliability, data_confidence, personnel_confidence = _reliability(
        away_rating, home_rating, away_lineup_summary, home_lineup_summary, int(week), manual_mode
    )
    projection = _project_matchup(
        away_rating,
        home_rating,
        away_lineup_summary,
        home_lineup_summary,
        {
            "home_field": home_field,
            "home_rest_edge": rest_adjustment,
            "weather_total_adjustment": weather_total_adjustment,
            "manual_total_adjustment": manual_total_adjustment,
            "manual_home_margin_adjustment": manual_home_margin_adjustment,
        },
    )
    simulation = _simulate_game(
        projection,
        home_spread,
        market_total,
        reliability,
        _simulation_seed(game_id, away_team, home_team, int(season), int(week)),
    )

    home_cover = simulation["home_cover"]
    spread_edge_home = projection["margin"] + home_spread
    if home_cover >= 0.5:
        spread_pick = f"{home_team} {home_spread:+.1f}"
        spread_probability = home_cover
        spread_edge = spread_edge_home
        spread_pick_home = True
    else:
        spread_pick = f"{away_team} {-home_spread:+.1f}"
        spread_probability = 1 - home_cover
        spread_edge = -spread_edge_home
        spread_pick_home = False
    spread_confluence, spread_reasons = _spread_confluence(
        spread_pick_home, spread_edge, away_rating, home_rating, away_lineup_summary, home_lineup_summary, reliability
    )
    spread_grade = _grade_spread(spread_probability, spread_edge, reliability, spread_confluence)

    over_probability = simulation["over"]
    if over_probability >= 0.5:
        total_pick = f"Over {market_total:.1f}"
        total_probability = over_probability
        total_edge = projection["total"] - market_total
        over_pick = True
    else:
        total_pick = f"Under {market_total:.1f}"
        total_probability = 1 - over_probability
        total_edge = market_total - projection["total"]
        over_pick = False
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
    st.caption(
        f"Middle 60% score range: {away_team} {simulation['away_low']:.0f}–{simulation['away_high']:.0f}; "
        f"{home_team} {simulation['home_low']:.0f}–{simulation['home_high']:.0f}. "
        f"Season blend: {100 * _num(away_rating.get('Previous Season Weight', 1)):.0f}% prior / "
        f"{100 * _num(away_rating.get('Current Season Weight', 0)):.0f}% current."
    )

    _market_card("Spread", spread_pick, spread_probability, f"{spread_edge:+.1f} pts", spread_grade, spread_confluence, spread_reasons)
    _market_card("Total", total_pick, total_probability, f"{total_edge:+.1f} pts", total_grade, total_confluence, total_reasons)
    _market_card("Moneyline", ml_pick, ml_probability, f"{ml_edge:+.1%}", ml_grade, ml_confluence, ml_reasons)

    st.markdown("### Matchup explanation")
    explanation = pd.DataFrame([
        {"Factor": f"{away_team} passing matchup", "Point effect": projection["away_pass_matchup"]},
        {"Factor": f"{home_team} passing matchup", "Point effect": projection["home_pass_matchup"]},
        {"Factor": f"{away_team} rushing matchup", "Point effect": projection["away_rush_matchup"]},
        {"Factor": f"{home_team} rushing matchup", "Point effect": projection["home_rush_matchup"]},
        {"Factor": "Pace effect on total", "Point effect": projection["pace_adjustment"]},
        {"Factor": "Weather effect on total", "Point effect": weather_total_adjustment},
        {"Factor": f"{away_team} offensive absences", "Point effect": -away_lineup_summary["offense_absence"]},
        {"Factor": f"{home_team} offensive absences", "Point effect": -home_lineup_summary["offense_absence"]},
        {"Factor": f"{away_team} defensive absences (opponent benefit)", "Point effect": away_lineup_summary["defense_absence"]},
        {"Factor": f"{home_team} defensive absences (opponent benefit)", "Point effect": home_lineup_summary["defense_absence"]},
    ])
    st.dataframe(explanation, use_container_width=True, hide_index=True)

    game = f"{away_team} at {home_team}"
    slate_row = {
        "Date": str(date.today()), "Season": season, "Week": week, "Game ID": game_id,
        "Game": game, "Away Team": away_team, "Home Team": home_team,
        "Projected Away": round(projection["away_score"], 1), "Projected Home": round(projection["home_score"], 1),
        "Projected Margin": round(projection["margin"], 1), "Projected Total": round(projection["total"], 1),
        "Away Score Low": round(simulation["away_low"], 1), "Away Score High": round(simulation["away_high"], 1),
        "Home Score Low": round(simulation["home_low"], 1), "Home Score High": round(simulation["home_high"], 1),
        "Market Home Spread": home_spread, "Market Total": market_total, "Away ML": away_ml, "Home ML": home_ml,
        "Spread Pick": spread_pick, "Spread Probability": round(spread_probability, 4), "Spread Edge": round(spread_edge, 2),
        "Spread Grade": spread_grade, "Spread Confluence": spread_confluence,
        "Total Pick": total_pick, "Total Probability": round(total_probability, 4), "Total Edge": round(total_edge, 2),
        "Total Grade": total_grade, "Total Confluence": total_confluence,
        "ML Pick": ml_pick, "ML Probability": round(ml_probability, 4), "ML Odds": ml_odds,
        "ML Edge": round(ml_edge, 4), "ML Grade": ml_grade, "ML Confluence": ml_confluence,
        "Reliability": reliability, "Data Confidence": data_confidence, "Personnel Confidence": personnel_confidence,
        "Previous Season Weight": away_rating.get("Previous Season Weight", ""),
        "Current Season Weight": away_rating.get("Current Season Weight", ""),
        "Away Offensive Absence": away_lineup_summary["offense_absence"],
        "Away Defensive Absence": away_lineup_summary["defense_absence"],
        "Home Offensive Absence": home_lineup_summary["offense_absence"],
        "Home Defensive Absence": home_lineup_summary["defense_absence"],
        "Weather Adjustment": weather_total_adjustment, "Roof": roof, "Temperature": temperature,
        "Wind": wind, "Model Version": MODEL_VERSION, "Notes": notes,
    }

    if st.button("Save NFL Projection to Daily Slate", type="primary", use_container_width=True):
        if append_row(SLATE_TAB, slate_row, SLATE_COLUMNS):
            _save_lineups(away_lineup, home_lineup, away_team, home_team, int(season), int(week), game_id)
            st.success("NFL projection and lineup snapshot saved.")

    spread_selected = st.checkbox(
        f"{spread_grade} — {spread_pick}",
        value=spread_grade in ["A Spread", "B Spread"],
        key=f"nfl_spread_pick_{market_key}",
    )
    total_selected = st.checkbox(
        f"{total_grade} — {total_pick}",
        value=total_grade in ["Strong Over", "Strong Under", "Over", "Under"],
        key=f"nfl_total_pick_{market_key}",
    )
    ml_selected = st.checkbox(
        f"{ml_grade} — {ml_pick}",
        value=ml_grade in ["A Moneyline", "B Moneyline"],
        key=f"nfl_ml_pick_{market_key}",
    )
    if st.button("Send NFL Plays to Tracker", use_container_width=True):
        plays = []
        if spread_selected:
            plays.append(("Spread", spread_pick, -110, spread_probability, spread_grade, spread_confluence))
        if total_selected:
            plays.append(("Total", total_pick, -110, total_probability, total_grade, total_confluence))
        if ml_selected:
            plays.append(("Moneyline", ml_pick, ml_odds, ml_probability, ml_grade, ml_confluence))
        sent = 0
        for bet_type, selection, odds, probability, grade, confluence in plays:
            implied = american_implied_probability(odds)
            row = {
                "Date": str(date.today()), "Season": season, "Week": week, "Game ID": game_id, "Game": game,
                "Bet Type": bet_type, "Selection": selection, "Odds/Line": odds,
                "Model Probability": round(probability, 4), "Implied Probability": round(implied, 4),
                "Edge": round(probability - implied, 4), "Expected Value": round(expected_value_per_unit(probability, odds), 4),
                "Grade": grade, "Confluence": confluence, "Result": "Pending", "Reliability": reliability,
                "Data Confidence": data_confidence, "Personnel Confidence": personnel_confidence,
                "Projected Away": round(projection["away_score"], 1), "Projected Home": round(projection["home_score"], 1),
                "Model Version": MODEL_VERSION, "Notes": notes,
            }
            sent += int(bool(append_row(TRACKER_TAB, row, TRACKER_COLUMNS)))
        st.success(f"Sent {sent} NFL play(s) to the tracker.") if sent else st.info("No plays were selected.")


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
    st.subheader("NFL Data Setup")
    st.caption("Run this once now and again before each NFL week after the prior week is complete.")
    st.metric("Google Sheets", "Connected" if sheets_ready() else "Not configured")
    st.metric("nflreadpy", "Installed" if nfl is not None else "Missing from deployment")

    c1, c2 = st.columns(2)
    with c1:
        season = st.number_input("Rating season", min_value=1999, max_value=2030, value=DEFAULT_SEASON, step=1, key="nfl_setup_season")
    with c2:
        projection_week = st.number_input("Build ratings for week", min_value=1, max_value=22, value=1, step=1, key="nfl_setup_week")

    if st.button("Build / Refresh Automated NFL Ratings", type="primary", use_container_width=True):
        try:
            with st.spinner("Building progressive team ratings from nflverse play-by-play and schedules..."):
                ratings = _build_automated_ratings(int(season), int(projection_week))
            if write_sheet(RATINGS_TAB, ratings, RATING_COLUMNS):
                log = read_sheet(MODEL_LOG_TAB, MODEL_LOG_COLUMNS)
                new_log = pd.DataFrame([{
                    "Date": str(date.today()), "Model Version": MODEL_VERSION,
                    "Change": f"Built {season} Week {projection_week} ratings using {season - 1}/{season} progressive blend.",
                }])
                output = pd.concat([log, new_log], ignore_index=True) if log is not None and not log.empty else new_log
                write_sheet(MODEL_LOG_TAB, output, MODEL_LOG_COLUMNS)
                st.success(f"Saved automated ratings for all {len(ratings)} teams.")
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
        "turnovers, sacks, pace, scoring, red-zone performance, schedules, depth charts, player production, and injury reports."
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
    st.caption("NFL v1 lineup engine • spreads, moneylines and totals • props follow after game-market calibration")
    page = st.radio(
        "NFL section",
        ["Build", "Setup", "Team Ratings", "Slate", "Tracker", "Schedule", "Lineups"],
        horizontal=True,
        key="nfl_nav",
    )
    if page == "Build":
        _render_build()
    elif page == "Setup":
        _render_setup()
    elif page == "Team Ratings":
        _render_ratings()
    elif page == "Slate":
        _table(SLATE_TAB, SLATE_COLUMNS, "NFL Daily Slate")
    elif page == "Tracker":
        _table(TRACKER_TAB, TRACKER_COLUMNS, "NFL Bet Tracker")
    elif page == "Schedule":
        _table(SCHEDULE_TAB, SCHEDULE_COLUMNS, "NFL Schedule")
    else:
        _table(LINEUP_TAB, LINEUP_COLUMNS, "NFL Lineup Snapshots")
