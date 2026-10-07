"""Opponent-adjusted scoring correction for the 2026 CFB totals model.

This layer measures scoring ability game-by-game relative to each opponent instead
of trusting raw season scoring averages. It fits a small recursive scoring network
from completed FBS-vs-FBS games before the projection week:

    points = league_mean + offense_strength + defense_allowance

The fitted offense/defense components are then compared with each team's raw
scoring/allowing averages. Only that *schedule adjustment* is applied to the
existing independent totals projection, so this layer does not double-count the
baseline pace/efficiency model.

Positive defense_allowance means a defense allows more scoring than an average
FBS defense after opponent adjustment. Negative means it suppresses scoring.

Every historical game remains an individual observation, with recency weighting
and empirical shrinkage toward the FBS mean. FCS/non-FBS games are intentionally
excluded from this correction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

MODEL_VERSION = "cfb-v2.8-game-residual-totals-2026-10-06"
ACTIVE_SEASON = 2026
MIN_PRIOR_FBS_GAMES = 1
RECENCY_HALF_LIFE_DAYS = 28.0
RIDGE_GAMES = 2.0
ITERATIONS = 12
HOME_FIELD_POINTS = 1.5
TOTAL_CORRECTION_CAP = 6.0
OFFENSE_RAMP_START = 3.0
OFFENSE_RAMP_SLOPE = 0.50
OFFENSE_RAMP_CAP = 1.50

_CONTEXT_CACHE: dict[tuple[int, int, str], dict[str, Any]] = {}


@dataclass
class TeamScheduleProfile:
    games: int = 0
    weight_sum: float = 0.0
    raw_ppg: float = 0.0
    raw_papg: float = 0.0
    adjusted_ppg: float = 0.0
    adjusted_papg: float = 0.0
    offense_strength: float = 0.0
    defense_allowance: float = 0.0
    offense_residual_std: float = 0.0
    defense_residual_std: float = 0.0


def _num(value: Any, default: float = np.nan) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "completed"}


def _canonicalizer(cfb_builder: Any):
    return getattr(cfb_builder, "_canonical_team_name", lambda value: str(value or "").strip())


def _weighted_mean(values: list[float], weights: list[float], default: float = 0.0) -> float:
    if not values:
        return float(default)
    arr = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    mask = np.isfinite(arr) & np.isfinite(w) & (w > 0)
    if not mask.any():
        return float(default)
    return float(np.average(arr[mask], weights=w[mask]))


def _weighted_std(values: list[float], weights: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _weighted_mean(values, weights, 0.0)
    arr = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    mask = np.isfinite(arr) & np.isfinite(w) & (w > 0)
    if mask.sum() < 2:
        return 0.0
    variance = np.average((arr[mask] - mean) ** 2, weights=w[mask])
    return float(math.sqrt(max(0.0, variance)))


def _read_prior_games(cfb_builder: Any, season: int, week: int, target_date: str = "") -> pd.DataFrame:
    try:
        frame = cfb_builder.read_sheet(cfb_builder.SCHEDULE_TAB, cfb_builder.SCHEDULE_COLUMNS)
    except Exception:
        return pd.DataFrame()
    if frame is None or frame.empty:
        return pd.DataFrame()

    required = {
        "Season", "Week", "Away Team", "Home Team", "Away Score", "Home Score",
        "Completed", "Away Classification", "Home Classification",
    }
    if not required.issubset(frame.columns):
        return pd.DataFrame()

    data = frame.copy()
    data["Season"] = pd.to_numeric(data["Season"], errors="coerce")
    data["Week"] = pd.to_numeric(data["Week"], errors="coerce")
    data["Away Score"] = pd.to_numeric(data["Away Score"], errors="coerce")
    data["Home Score"] = pd.to_numeric(data["Home Score"], errors="coerce")
    completed = data["Completed"].map(_truthy)
    away_fbs = data["Away Classification"].astype(str).str.strip().str.lower().eq("fbs")
    home_fbs = data["Home Classification"].astype(str).str.strip().str.lower().eq("fbs")
    game_dates = pd.to_datetime(data.get("Game Date", pd.Series(index=data.index, dtype=object)), errors="coerce")
    cutoff = pd.to_datetime(target_date, errors="coerce") if target_date else pd.NaT
    if pd.notna(cutoff):
        prior_mask = game_dates.dt.normalize().lt(cutoff.normalize())
    else:
        prior_mask = data["Week"].lt(int(week))
    mask = (
        data["Season"].eq(int(season))
        & prior_mask
        & completed
        & away_fbs
        & home_fbs
        & data["Away Score"].notna()
        & data["Home Score"].notna()
    )
    data = data.loc[mask].copy()
    if data.empty:
        return data

    canon = _canonicalizer(cfb_builder)
    data["Away Canon"] = data["Away Team"].map(canon)
    data["Home Canon"] = data["Home Team"].map(canon)
    data = data[(data["Away Canon"] != "") & (data["Home Canon"] != "")].copy()
    if pd.notna(cutoff):
        age_days = (cutoff.normalize() - game_dates.loc[data.index].dt.normalize()).dt.days.clip(lower=0)
        data["Recency Weight"] = np.power(0.5, age_days.astype(float) / RECENCY_HALF_LIFE_DAYS)
    else:
        # Seven days per week is only a fallback for malformed/missing dates.
        age_days = np.maximum(0.0, (float(week) - data["Week"].astype(float)) * 7.0)
        data["Recency Weight"] = np.power(0.5, age_days / RECENCY_HALF_LIFE_DAYS)
    return data


def _observations(games: pd.DataFrame) -> list[dict[str, Any]]:
    obs: list[dict[str, Any]] = []
    for _, row in games.iterrows():
        week = int(_num(row.get("Week"), 1.0))
        weight = max(0.05, _num(row.get("Recency Weight"), 1.0))
        away = str(row.get("Away Canon") or "")
        home = str(row.get("Home Canon") or "")
        away_score = _num(row.get("Away Score"), np.nan)
        home_score = _num(row.get("Home Score"), np.nan)
        if not away or not home or not math.isfinite(away_score) or not math.isfinite(home_score):
            continue
        obs.append({"team": away, "opp": home, "points": away_score, "week": week, "weight": weight, "home": False})
        obs.append({"team": home, "opp": away, "points": home_score, "week": week, "weight": weight, "home": True})
    return obs


def _fit_network(games: pd.DataFrame) -> dict[str, Any]:
    obs = _observations(games)
    if not obs:
        return {"league_mean": 28.0, "profiles": {}, "observations": []}

    league_mean = _weighted_mean(
        [float(o["points"]) for o in obs],
        [float(o["weight"]) for o in obs],
        28.0,
    )
    teams = sorted({str(o["team"]) for o in obs} | {str(o["opp"]) for o in obs})
    offense = {team: 0.0 for team in teams}
    defense_allow = {team: 0.0 for team in teams}

    by_offense: dict[str, list[dict[str, Any]]] = {team: [] for team in teams}
    by_defense: dict[str, list[dict[str, Any]]] = {team: [] for team in teams}
    for o in obs:
        by_offense[str(o["team"])].append(o)
        by_defense[str(o["opp"])].append(o)

    for _ in range(ITERATIONS):
        next_offense: dict[str, float] = {}
        for team in teams:
            rows = by_offense.get(team, [])
            vals = [
                float(r["points"])
                - (league_mean + defense_allow.get(str(r["opp"]), 0.0)
                   + (HOME_FIELD_POINTS if bool(r.get("home")) else -HOME_FIELD_POINTS))
                for r in rows
            ]
            weights = [float(r["weight"]) for r in rows]
            weight_sum = sum(weights)
            shrink = len(rows) / (len(rows) + RIDGE_GAMES) if rows else 0.0
            next_offense[team] = float(np.clip(_weighted_mean(vals, weights, 0.0) * shrink, -18.0, 18.0))

        next_defense: dict[str, float] = {}
        for team in teams:
            rows = by_defense.get(team, [])
            vals = [
                float(r["points"])
                - (league_mean + next_offense.get(str(r["team"]), 0.0)
                   + (HOME_FIELD_POINTS if bool(r.get("home")) else -HOME_FIELD_POINTS))
                for r in rows
            ]
            weights = [float(r["weight"]) for r in rows]
            weight_sum = sum(weights)
            shrink = len(rows) / (len(rows) + RIDGE_GAMES) if rows else 0.0
            next_defense[team] = float(np.clip(_weighted_mean(vals, weights, 0.0) * shrink, -18.0, 18.0))

        offense = next_offense
        defense_allow = next_defense

    profiles: dict[str, TeamScheduleProfile] = {}
    for team in teams:
        offensive_rows = by_offense.get(team, [])
        defensive_rows = by_defense.get(team, [])

        off_points = [float(r["points"]) for r in offensive_rows]
        off_weights = [float(r["weight"]) for r in offensive_rows]
        def_points = [float(r["points"]) for r in defensive_rows]
        def_weights = [float(r["weight"]) for r in defensive_rows]

        offense_residuals = [
            float(r["points"]) - (
                league_mean + defense_allow.get(str(r["opp"]), 0.0)
                + (HOME_FIELD_POINTS if bool(r.get("home")) else -HOME_FIELD_POINTS)
            )
            for r in offensive_rows
        ]
        defense_residuals = [
            float(r["points"]) - (
                league_mean + offense.get(str(r["team"]), 0.0)
                + (HOME_FIELD_POINTS if bool(r.get("home")) else -HOME_FIELD_POINTS)
            )
            for r in defensive_rows
        ]

        profiles[team] = TeamScheduleProfile(
            games=len(offensive_rows),
            weight_sum=float(sum(off_weights)),
            raw_ppg=_weighted_mean(off_points, off_weights, league_mean),
            raw_papg=_weighted_mean(def_points, def_weights, league_mean),
            adjusted_ppg=float(league_mean + offense.get(team, 0.0)),
            adjusted_papg=float(league_mean + defense_allow.get(team, 0.0)),
            offense_strength=float(offense.get(team, 0.0)),
            defense_allowance=float(defense_allow.get(team, 0.0)),
            offense_residual_std=_weighted_std(offense_residuals, off_weights),
            defense_residual_std=_weighted_std(defense_residuals, def_weights),
        )

    return {"league_mean": float(league_mean), "profiles": profiles, "observations": obs}


def _context(cfb_builder: Any, season: int, week: int, target_date: str = "") -> dict[str, Any]:
    key = (int(season), int(week), str(target_date or ""))
    cached = _CONTEXT_CACHE.get(key)
    if cached is not None:
        return cached
    games = _read_prior_games(cfb_builder, season, week, target_date)
    value = _fit_network(games)
    value["game_count"] = int(len(games))
    if not games.empty:
        _CONTEXT_CACHE[key] = value
    return value


def _profile_dict(profile: TeamScheduleProfile | None) -> dict[str, float]:
    if profile is None:
        return {}
    return {
        "games": float(profile.games),
        "weight_sum": float(profile.weight_sum),
        "raw_ppg": float(profile.raw_ppg),
        "raw_papg": float(profile.raw_papg),
        "adjusted_ppg": float(profile.adjusted_ppg),
        "adjusted_papg": float(profile.adjusted_papg),
        "offense_strength": float(profile.offense_strength),
        "defense_allowance": float(profile.defense_allowance),
        "offense_residual_std": float(profile.offense_residual_std),
        "defense_residual_std": float(profile.defense_residual_std),
    }


def _matchup_adjustments(
    away_profile: TeamScheduleProfile,
    home_profile: TeamScheduleProfile,
) -> tuple[float, float, float]:
    """Return schedule correction, offense ramp, and combined total adjustment.

    The schedule component exactly mirrors the validated rolling audit:
      away offense + home offense + away defense allowance + home defense allowance
    Positive defense allowance means the defense permits more scoring than average.

    That schedule component is capped at +/-6 points. A small smooth offensive
    acceleration is then added when combined adjusted offensive strength exceeds
    +3, rising 0.5 points per strength point and capped at +1.5.
    """
    raw_schedule = (
        away_profile.offense_strength
        + home_profile.offense_strength
        + away_profile.defense_allowance
        + home_profile.defense_allowance
    )
    schedule_correction = float(np.clip(raw_schedule, -TOTAL_CORRECTION_CAP, TOTAL_CORRECTION_CAP))
    combined_offense = away_profile.offense_strength + home_profile.offense_strength
    offense_ramp = float(np.clip(
        (combined_offense - OFFENSE_RAMP_START) * OFFENSE_RAMP_SLOPE,
        0.0,
        OFFENSE_RAMP_CAP,
    ))
    return schedule_correction, offense_ramp, schedule_correction + offense_ramp


def install_schedule_adjustment(cfb_builder: Any) -> None:
    """Install after independent totals and before the 2026 spread-regime layer."""
    if getattr(cfb_builder, "_OPPONENT_ADJUSTED_SCORING_LAYER_INSTALLED", False):
        return

    original_project_matchup = cfb_builder.project_matchup

    def project_matchup(game: pd.Series, ratings: pd.DataFrame, away_personnel: Any,
                        home_personnel: Any, environment: Any) -> dict[str, Any]:
        projection = original_project_matchup(game, ratings, away_personnel, home_personnel, environment)
        season = int(_num(game.get("Season"), getattr(cfb_builder, "DEFAULT_SEASON", ACTIVE_SEASON)))
        week = int(_num(game.get("Week"), 1.0))

        try:
            target_date = str(game.get("Game Date") or game.get("Date") or "")[:10]
            ctx = _context(cfb_builder, season, week, target_date)
            profiles: dict[str, TeamScheduleProfile] = ctx.get("profiles", {})
            canon = _canonicalizer(cfb_builder)
            away = canon(game.get("Away Team"))
            home = canon(game.get("Home Team"))
            away_profile = profiles.get(away)
            home_profile = profiles.get(home)

            if (
                away_profile is None or home_profile is None
                or away_profile.games < MIN_PRIOR_FBS_GAMES
                or home_profile.games < MIN_PRIOR_FBS_GAMES
            ):
                projection.update({
                    "schedule_adjustment_applied": False,
                    "schedule_adjustment_reason": "insufficient_prior_fbs_games",
                    "schedule_adjustment_game_count": int(ctx.get("game_count", 0)),
                    "schedule_adjustment_version": MODEL_VERSION,
                })
                return projection

            schedule_correction, offense_ramp, total_correction = _matchup_adjustments(
                away_profile, home_profile
            )

            old_away = _num(projection.get("away_points"), 28.0)
            old_home = _num(projection.get("home_points"), 28.0)
            old_total = _num(projection.get("total"), old_away + old_home)
            old_margin = _num(projection.get("margin"), old_home - old_away)

            # This layer is intentionally totals-only. The spread regression owns
            # margin, so apply the two matchup corrections to total and then derive
            # team scores algebraically around the unchanged margin.
            final_total = float(np.clip(old_total + total_correction, 14.0, 110.0))
            minimum_total = abs(old_margin) + 6.0
            final_total = max(final_total, minimum_total)
            new_home = (final_total + old_margin) / 2.0
            new_away = (final_total - old_margin) / 2.0

            projection.update({
                "away_points": float(new_away),
                "home_points": float(new_home),
                "margin": float(old_margin),
                "total": float(final_total),
                "schedule_adjustment_applied": True,
                "schedule_adjustment_base_total": float(old_total),
                "schedule_adjustment_raw_schedule_points": float(
                    away_profile.offense_strength + home_profile.offense_strength
                    + away_profile.defense_allowance + home_profile.defense_allowance
                ),
                "schedule_adjustment_capped_schedule_points": float(schedule_correction),
                "schedule_adjustment_combined_offense_strength": float(
                    away_profile.offense_strength + home_profile.offense_strength
                ),
                "schedule_adjustment_offense_ramp_points": float(offense_ramp),
                "schedule_adjustment_total_points": float(final_total - old_total),
                "schedule_adjustment_league_mean": float(ctx.get("league_mean", 28.0)),
                "schedule_adjustment_game_count": int(ctx.get("game_count", 0)),
                "schedule_adjustment_away_profile": _profile_dict(away_profile),
                "schedule_adjustment_home_profile": _profile_dict(home_profile),
                "schedule_adjustment_version": MODEL_VERSION,
            })
        except Exception as exc:
            projection.update({
                "schedule_adjustment_applied": False,
                "schedule_adjustment_reason": f"fallback_after_error:{type(exc).__name__}",
                "schedule_adjustment_error": str(exc),
                "schedule_adjustment_version": MODEL_VERSION,
            })
        return projection

    cfb_builder.project_matchup = project_matchup
    cfb_builder._OPPONENT_ADJUSTED_SCORING_LAYER_INSTALLED = True
