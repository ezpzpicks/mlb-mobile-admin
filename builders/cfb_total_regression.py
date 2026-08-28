"""Production CFB independent totals-regression layer.

The validated CFB spread regression owns projected scoring margin. This layer owns
projected game total and deliberately does not use the spread model's team-score
sum. The spread model's *margin* is allowed only as game-script context, matching
the 2021-23 training / 2024 selection / untouched 2025 holdout research design.

Final team scores are derived algebraically after both models have produced their
independent outputs:
    home = (total + home_margin) / 2
    away = (total - home_margin) / 2

The fitted normalization parameters are loaded from the committed research result
so production uses the exact model that was evaluated rather than rounded report
coefficients.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from builders import cfb_game_regression as spread_reg

MODEL_VERSION = "cfb-v2.3-independent-total-2026-08-28"
MODEL_RESEARCH_VERSION = "cfb-combined-spread-total-independent-2026-08-28"
MODEL_RESULT_PATH = (
    Path(__file__).resolve().parents[1]
    / "research"
    / "results"
    / "cfb_combined_spread_total_results.json"
)

METRIC_DEFAULTS = {
    "Possessions Per Game": 12.0,
    "Plays Per Game": 69.0,
    "EPA/PPA Offense": 0.0,
    "EPA/PPA Defense Raw": 0.0,
    "Success Rate Offense": 0.42,
    "Success Rate Defense Raw": 0.42,
    "Pass EPA/PPA": 0.0,
    "Pass Defense Raw": 0.0,
    "Rush EPA/PPA": 0.0,
    "Rush Defense Raw": 0.0,
    "Explosiveness Offense": 0.12,
    "Explosiveness Defense Raw": 0.12,
    "Finishing Drives Offense": 4.2,
    "Finishing Drives Defense Raw": 4.2,
    "Third Down Rate": 0.40,
    "Third Down Defense Raw": 0.40,
    "Red Zone TD Rate": 0.62,
    "Red Zone Defense Raw": 0.62,
    "Turnover Rate": 0.02,
    "Takeaway Rate": 0.02,
    "Sack Rate Allowed": 0.065,
    "Sack Rate Created": 0.065,
    "Yards Per Play": 5.7,
    "Yards Per Play Allowed": 5.7,
    "Line Yards Offense": 2.7,
    "Line Yards Defense Raw": 2.7,
    "Havoc Allowed": 0.16,
    "Havoc Created": 0.16,
}

_MODEL_CACHE: dict[str, Any] | None = None
_METRIC_CONTEXT_CACHE: dict[tuple[int, int], tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]] = {}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "completed"}


def _model() -> dict[str, Any]:
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    payload = json.loads(MODEL_RESULT_PATH.read_text())
    model = payload.get("clean_final_model")
    if not isinstance(model, dict) or not model.get("features"):
        raise RuntimeError("Committed independent CFB totals model parameters are unavailable")
    required = {"features", "intercept", "coef", "means", "stds"}
    missing = required - set(model)
    if missing:
        raise RuntimeError(f"Independent totals model is missing: {sorted(missing)}")
    _MODEL_CACHE = model
    return model


def _metric_lookup(frame: pd.DataFrame | None) -> dict[str, dict[str, float]]:
    if frame is None or frame.empty:
        return {}
    output: dict[str, dict[str, float]] = {}
    for _, row in frame.iterrows():
        team = spread_reg._normalize_team(row.get("Team"))
        if not team:
            continue
        values = {
            name: _num(row.get(name), default)
            for name, default in METRIC_DEFAULTS.items()
        }
        values["Advanced Plays"] = _num(row.get("Advanced Plays"), 0.0)
        output[team] = values
    return output


def _metric_context(cfb_builder: Any, season: int, week: int) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    key = (int(season), int(week))
    cached = _METRIC_CONTEXT_CACHE.get(key)
    if cached is not None:
        return cached

    # Open PBP is already part of the production CFB data stack. When a release is
    # temporarily unavailable, neutral defaults are intentional and mirror the
    # research treatment of seasons/weeks with no advanced coverage.
    try:
        prior_frame = cfb_builder._pbp_team_metrics(int(season) - 1, None)
    except Exception:
        prior_frame = pd.DataFrame()
    try:
        current_frame = cfb_builder._pbp_team_metrics(int(season), int(week))
    except Exception:
        current_frame = pd.DataFrame()

    value = (_metric_lookup(prior_frame), _metric_lookup(current_frame))
    _METRIC_CONTEXT_CACHE[key] = value
    return value


def _metric_games(values: dict[str, float] | None) -> float:
    values = values or {}
    plays = _num(values.get("Advanced Plays"), 0.0)
    plays_per_game = _num(values.get("Plays Per Game"), 69.0)
    return max(0.0, plays / max(1.0, plays_per_game))


def _blend_metric(prior: dict[str, float] | None, current: dict[str, float] | None, name: str) -> float:
    default = METRIC_DEFAULTS[name]
    prior_value = _num((prior or {}).get(name), default)
    current_value = _num((current or {}).get(name), prior_value)
    games = _metric_games(current)
    weight = games / (games + 4.0) if games > 0 else 0.0
    return (1.0 - weight) * prior_value + weight * current_value


def _matchup_mean(away_offense: float, home_defense: float, home_offense: float, away_defense: float) -> float:
    return float(np.mean([away_offense, home_defense, home_offense, away_defense]))


def _feature_row(cfb_builder: Any, game: pd.Series, game_script_margin: float) -> dict[str, float]:
    season = int(_num(game.get("Season"), getattr(cfb_builder, "DEFAULT_SEASON", 2026)))
    week = int(_num(game.get("Week"), 1.0))
    away = spread_reg._normalize_team(game.get("Away Team"))
    home = spread_reg._normalize_team(game.get("Home Team"))
    neutral = _truthy(game.get("Neutral Site", False))

    prior_stats, current_stats = spread_reg._context(cfb_builder, season, week)
    prior_metrics, current_metrics = _metric_context(cfb_builder, season, week)
    away_prior_metrics = prior_metrics.get(away)
    home_prior_metrics = prior_metrics.get(home)
    away_current_metrics = current_metrics.get(away)
    home_current_metrics = current_metrics.get(home)

    def am(name: str) -> float:
        return _blend_metric(away_prior_metrics, away_current_metrics, name)

    def hm(name: str) -> float:
        return _blend_metric(home_prior_metrics, home_current_metrics, name)

    away_stats = spread_reg._blend(
        spread_reg._stat(prior_stats, away),
        spread_reg._stat(current_stats, away),
    )
    home_stats = spread_reg._blend(
        spread_reg._stat(prior_stats, home),
        spread_reg._stat(current_stats, home),
    )

    away_possessions = am("Possessions Per Game")
    home_possessions = hm("Possessions Per Game")
    away_plays = am("Plays Per Game")
    home_plays = hm("Plays Per Game")

    expected_possessions = (
        0.58 * ((away_possessions + home_possessions) / 2.0)
        + 0.42 * (((away_plays + home_plays) / 2.0) / 5.75)
    )
    expected_possessions -= max(0.0, abs(game_script_margin) - 17.0) * 0.015
    expected_possessions = float(np.clip(expected_possessions, 9.0, 16.5))

    away_off_ppd = away_stats.ppg / max(8.0, away_possessions)
    home_off_ppd = home_stats.ppg / max(8.0, home_possessions)
    away_def_ppd = away_stats.papg / max(8.0, away_possessions)
    home_def_ppd = home_stats.papg / max(8.0, home_possessions)
    away_match_ppd = 0.58 * away_off_ppd + 0.42 * home_def_ppd
    home_match_ppd = 0.58 * home_off_ppd + 0.42 * away_def_ppd
    expected_combined_ppd = away_match_ppd + home_match_ppd
    structural_total = float(np.clip(expected_possessions * expected_combined_ppd, 20.0, 100.0))

    epa_matchup = _matchup_mean(
        am("EPA/PPA Offense"), hm("EPA/PPA Defense Raw"),
        hm("EPA/PPA Offense"), am("EPA/PPA Defense Raw"),
    )
    pass_matchup = _matchup_mean(
        am("Pass EPA/PPA"), hm("Pass Defense Raw"),
        hm("Pass EPA/PPA"), am("Pass Defense Raw"),
    )
    rush_matchup = _matchup_mean(
        am("Rush EPA/PPA"), hm("Rush Defense Raw"),
        hm("Rush EPA/PPA"), am("Rush Defense Raw"),
    )
    success_matchup = _matchup_mean(
        am("Success Rate Offense"), hm("Success Rate Defense Raw"),
        hm("Success Rate Offense"), am("Success Rate Defense Raw"),
    )
    explosive_matchup = _matchup_mean(
        am("Explosiveness Offense"), hm("Explosiveness Defense Raw"),
        hm("Explosiveness Offense"), am("Explosiveness Defense Raw"),
    )
    finishing_matchup = _matchup_mean(
        am("Finishing Drives Offense"), hm("Finishing Drives Defense Raw"),
        hm("Finishing Drives Offense"), am("Finishing Drives Defense Raw"),
    )
    third_down_matchup = _matchup_mean(
        am("Third Down Rate"), hm("Third Down Defense Raw"),
        hm("Third Down Rate"), am("Third Down Defense Raw"),
    )
    red_zone_matchup = _matchup_mean(
        am("Red Zone TD Rate"), hm("Red Zone Defense Raw"),
        hm("Red Zone TD Rate"), am("Red Zone Defense Raw"),
    )
    ypp_matchup = _matchup_mean(
        am("Yards Per Play"), hm("Yards Per Play Allowed"),
        hm("Yards Per Play"), am("Yards Per Play Allowed"),
    )
    line_yards_matchup = _matchup_mean(
        am("Line Yards Offense"), hm("Line Yards Defense Raw"),
        hm("Line Yards Offense"), am("Line Yards Defense Raw"),
    )
    turnover_pressure = float(np.mean([
        am("Turnover Rate"), hm("Turnover Rate"),
        am("Takeaway Rate"), hm("Takeaway Rate"),
    ]))
    sack_pressure = float(np.mean([
        am("Sack Rate Allowed"), hm("Sack Rate Allowed"),
        am("Sack Rate Created"), hm("Sack Rate Created"),
    ]))
    havoc_pressure = float(np.mean([
        am("Havoc Allowed"), hm("Havoc Allowed"),
        am("Havoc Created"), hm("Havoc Created"),
    ]))
    plays_per_possession = (
        (away_plays / max(8.0, away_possessions))
        + (home_plays / max(8.0, home_possessions))
    ) / 2.0

    abs_margin = abs(float(game_script_margin))
    features = {
        "structural_total": structural_total,
        "expected_possessions": expected_possessions,
        "expected_combined_ppd": expected_combined_ppd,
        "abs_spread_margin": abs_margin,
        "abs_spread_margin_sq": abs_margin ** 2,
        "week": float(week),
        "neutral": 1.0 if neutral else 0.0,
        "epa_matchup": epa_matchup,
        "pass_ppa_matchup": pass_matchup,
        "rush_ppa_matchup": rush_matchup,
        "success_matchup": success_matchup,
        "explosive_matchup": explosive_matchup,
        "finishing_matchup": finishing_matchup,
        "third_down_matchup": third_down_matchup,
        "red_zone_matchup": red_zone_matchup,
        "ypp_matchup": ypp_matchup,
        "turnover_pressure": turnover_pressure,
        "sack_pressure": sack_pressure,
        "havoc_pressure": havoc_pressure,
        "line_yards_matchup": line_yards_matchup,
        "plays_per_possession": plays_per_possession,
    }
    features.update({
        "poss_x_epa": expected_possessions * epa_matchup,
        "poss_x_success": expected_possessions * success_matchup,
        "poss_x_explosive": expected_possessions * explosive_matchup,
        "poss_x_finishing": expected_possessions * finishing_matchup,
        "poss_x_ypp": expected_possessions * ypp_matchup,
        "margin_x_possessions": abs_margin * expected_possessions,
    })
    return features


def _predict_base_total(features: dict[str, float]) -> tuple[float, dict[str, Any]]:
    model = _model()
    correction = _num(model.get("intercept"), 0.0)
    contributions: dict[str, float] = {}
    for name in model["features"]:
        value = _num(features.get(name), _num(model["means"].get(name), 0.0))
        mean = _num(model["means"].get(name), 0.0)
        std = abs(_num(model["stds"].get(name), 1.0)) or 1.0
        coefficient = _num(model["coef"].get(name), 0.0)
        contribution = coefficient * ((value - mean) / std)
        contributions[name] = contribution
        correction += contribution
    total = _num(features.get("structural_total"), 56.0) + correction
    return float(total), contributions


def _personnel_total_delta(away_personnel: Any, home_personnel: Any) -> float:
    away_effect = (
        _num(getattr(away_personnel, "offense_adjustment", 0.0))
        - _num(getattr(home_personnel, "defense_adjustment", 0.0))
        + _num(getattr(away_personnel, "kicker_adjustment", 0.0))
        + _num(getattr(away_personnel, "special_teams_adjustment", 0.0))
    )
    home_effect = (
        _num(getattr(home_personnel, "offense_adjustment", 0.0))
        - _num(getattr(away_personnel, "defense_adjustment", 0.0))
        + _num(getattr(home_personnel, "kicker_adjustment", 0.0))
        + _num(getattr(home_personnel, "special_teams_adjustment", 0.0))
    )
    return float(away_effect + home_effect)


def _continuity_total_delta(projection: dict[str, Any], away_personnel: Any, home_personnel: Any) -> float:
    away = projection.get("away", {})
    home = projection.get("home", {})
    prior_weight = (
        _num(away.get("Previous Season Weight"), 1.0)
        + _num(home.get("Previous Season Weight"), 1.0)
    ) / 2.0
    away_effect = (
        prior_weight * (_num(getattr(away_personnel, "qb_continuity", 0.50)) - 0.50) * 2.4
        + prior_weight * (
            (_num(getattr(away_personnel, "coaching_continuity", 0.75)) - 0.75)
            + (_num(getattr(away_personnel, "coordinator_continuity", 0.67)) - 0.67)
        )
    )
    home_effect = (
        prior_weight * (_num(getattr(home_personnel, "qb_continuity", 0.50)) - 0.50) * 2.4
        + prior_weight * (
            (_num(getattr(home_personnel, "coaching_continuity", 0.75)) - 0.75)
            + (_num(getattr(home_personnel, "coordinator_continuity", 0.67)) - 0.67)
        )
    )
    return float(away_effect + home_effect)


def install_total_regression(cfb_builder: Any) -> None:
    """Install independent totals after the spread layer and before market calibration."""
    if getattr(cfb_builder, "_TOTAL_REGRESSION_LAYER_INSTALLED", False):
        return

    original_project_matchup = cfb_builder.project_matchup

    def project_matchup(game: pd.Series, ratings: pd.DataFrame, away_personnel: Any,
                        home_personnel: Any, environment: Any) -> dict[str, Any]:
        projection = original_project_matchup(
            game, ratings, away_personnel, home_personnel, environment
        )
        legacy_total = _num(
            projection.get("total"),
            _num(projection.get("away_points"), 28.0) + _num(projection.get("home_points"), 28.0),
        )
        legacy_possessions = _num(projection.get("possessions"), 12.0)
        game_script_margin = _num(
            projection.get("regression_base_margin"),
            projection.get("margin", 0.0),
        )

        try:
            features = _feature_row(cfb_builder, game, game_script_margin)
            base_total, contributions = _predict_base_total(features)
            personnel_delta = _personnel_total_delta(away_personnel, home_personnel)
            continuity_delta = _continuity_total_delta(projection, away_personnel, home_personnel)
            weather_delta = _num(getattr(environment, "weather_total_adjustment", 0.0), 0.0)
            live_overlay = personnel_delta + continuity_delta + weather_delta
            projected_total = float(np.clip(base_total + live_overlay, 14.0, 110.0))

            # Preserve both independently modeled outputs exactly whenever possible.
            # A 6-point floor guarantees at least 3 projected points per team even
            # in an extreme spread/total combination.
            final_margin = float(np.clip(_num(projection.get("margin"), 0.0), -60.0, 60.0))
            minimum_total = abs(final_margin) + 6.0
            consistency_adjustment = max(0.0, minimum_total - projected_total)
            projected_total += consistency_adjustment
            home_points = (projected_total + final_margin) / 2.0
            away_points = (projected_total - final_margin) / 2.0

            projection.update({
                "away_points": float(away_points),
                "home_points": float(home_points),
                "margin": float(final_margin),
                "total": float(projected_total),
                "possessions": float(features["expected_possessions"]),
                "legacy_total_before_independent_regression": float(legacy_total),
                "legacy_possessions_before_independent_regression": float(legacy_possessions),
                "total_regression_base": float(base_total),
                "total_regression_personnel_delta": float(personnel_delta),
                "total_regression_continuity_delta": float(continuity_delta),
                "total_regression_weather_delta": float(weather_delta),
                "total_regression_live_overlay": float(live_overlay),
                "total_regression_consistency_adjustment": float(consistency_adjustment),
                "total_regression_features": features,
                "total_regression_contributions": contributions,
                "total_regression_source": "independent_pace_efficiency_interactions",
                "total_regression_research_version": MODEL_RESEARCH_VERSION,
                "total_regression_alpha": _num(_model().get("alpha"), 0.25),
                # Old calibration residuals were learned against the prior totals
                # engine and are intentionally not applied to the new model.
                "legacy_total_calibration": _num(projection.get("total_calibration"), 0.0),
                "total_calibration": 0.0,
            })
        except Exception as exc:
            # Production remains usable if the committed model file or open PBP is
            # temporarily unavailable. This should be visible in diagnostics rather
            # than breaking the entire CFB slate.
            projection.update({
                "total_regression_source": "legacy_fallback",
                "total_regression_error": str(exc),
                "total_regression_research_version": MODEL_RESEARCH_VERSION,
            })
        return projection

    cfb_builder.project_matchup = project_matchup
    cfb_builder._TOTAL_REGRESSION_LAYER_INSTALLED = True
