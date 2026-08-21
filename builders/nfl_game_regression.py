"""Production NFL team-score regression layer.

Replaces the hand-tuned game score/margin/total projection with the validated
historical team-score regression while retaining the existing data pipeline,
progressive season weighting, rolling home-field model, lineup/injury overlays,
manual overrides, grading UI, and player-prop engine.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from builders.nfl_game_regression_coefficients import (
    MARGIN_RESIDUAL_SD,
    MARGIN_TOTAL_RESIDUAL_CORRELATION,
    MODEL_RESEARCH_VERSION,
    TEAM_SCORE_COEFFICIENTS,
    TEAM_SCORE_INTERCEPT,
    TOTAL_RESIDUAL_SD,
)

MODEL_VERSION = "nfl-v4.0-team-score-regression-2026-08-20"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def _matchup_features(offense: dict[str, Any], defense: dict[str, Any], weather_adjustment: float) -> dict[str, float]:
    return {
        "scoring_matchup": (
            0.50 * _num(offense.get("Points/Game"), 22.5)
            + 0.50 * _num(defense.get("Points Allowed/Game"), 22.5)
        ),
        "success_matchup": (
            _num(offense.get("Off Success Rate"), 0.43)
            - _num(defense.get("Def Success Edge"), 0.0)
        ),
        "weather_adjustment": _num(weather_adjustment, 0.0),
    }


def _team_score(features: dict[str, float]) -> float:
    return float(
        TEAM_SCORE_INTERCEPT
        + sum(float(coef) * _num(features.get(name), 0.0) for name, coef in TEAM_SCORE_COEFFICIENTS.items())
    )


def _rating_adjustments(row: dict[str, Any]) -> tuple[float, float]:
    offense = (
        _num(row.get("QB Adjustment"))
        + _num(row.get("OL Adjustment"))
        + _num(row.get("Skill/Injury Adjustment"))
    )
    defense = _num(row.get("Front Seven Adjustment")) + _num(row.get("Secondary Adjustment"))
    return offense, defense


def project_matchup(
    away: dict[str, Any],
    home: dict[str, Any],
    away_lineup: dict[str, float],
    home_lineup: dict[str, float],
    settings: dict[str, float],
) -> dict[str, float]:
    weather = _num(settings.get("weather_total_adjustment"), 0.0)
    away_features = _matchup_features(away, home, weather)
    home_features = _matchup_features(home, away, weather)

    # Base scores come entirely from the statistically selected team-score
    # variables. Home field remains a separate rolling ridge-regression model.
    away_points = _team_score(away_features)
    home_points = _team_score(home_features)

    home_field = _num(settings.get("home_field"), 0.0)
    home_points += home_field / 2.0
    away_points -= home_field / 2.0

    # These are explicit current-personnel/manual overlays that cannot be
    # reconstructed reliably in the historical training set.
    away_off_adj, away_def_adj = _rating_adjustments(away)
    home_off_adj, home_def_adj = _rating_adjustments(home)
    away_points += away_off_adj - home_def_adj
    home_points += home_off_adj - away_def_adj

    away_points -= _num(away_lineup.get("offense_absence"), 0.0)
    away_points += _num(home_lineup.get("defense_absence"), 0.0)
    home_points -= _num(home_lineup.get("offense_absence"), 0.0)
    home_points += _num(away_lineup.get("defense_absence"), 0.0)

    away_points += 0.20 * (_num(away.get("Special Teams")) - _num(home.get("Special Teams")))
    home_points += 0.20 * (_num(home.get("Special Teams")) - _num(away.get("Special Teams")))

    # Rest was tested historically and did not survive the significance/stability
    # screen, so the old automatic rest coefficient is intentionally not applied.
    manual_margin = _num(settings.get("manual_home_margin_adjustment"), 0.0)
    manual_total = _num(settings.get("manual_total_adjustment"), 0.0)
    home_points += manual_total / 2.0 + manual_margin / 2.0
    away_points += manual_total / 2.0 - manual_margin / 2.0

    home_points = float(np.clip(home_points, 6.0, 48.0))
    away_points = float(np.clip(away_points, 6.0, 48.0))
    projected_total = float(np.clip(home_points + away_points, 25.0, 75.0))
    projected_margin = float(np.clip(home_points - away_points, -31.0, 31.0))
    home_points = float((projected_total + projected_margin) / 2.0)
    away_points = float(projected_total - home_points)

    away_pass = _num(away.get("Pass EPA/DB")) - _num(home.get("Pass Def EPA Edge"))
    home_pass = _num(home.get("Pass EPA/DB")) - _num(away.get("Pass Def EPA Edge"))
    away_rush = _num(away.get("Rush EPA/Play")) - _num(home.get("Rush Def EPA Edge"))
    home_rush = _num(home.get("Rush EPA/Play")) - _num(away.get("Rush Def EPA Edge"))

    return {
        "away_score": round(away_points, 2),
        "home_score": round(home_points, 2),
        "margin": round(projected_margin, 2),
        "total": round(projected_total, 2),
        "pace_adjustment": 0.0,
        "away_pass_matchup": round(away_pass, 2),
        "home_pass_matchup": round(home_pass, 2),
        "away_rush_matchup": round(away_rush, 2),
        "home_rush_matchup": round(home_rush, 2),
        "away_regression_base": round(_team_score(away_features), 2),
        "home_regression_base": round(_team_score(home_features), 2),
        "research_version": MODEL_RESEARCH_VERSION,
    }


def simulate_game(
    projection: dict[str, float],
    home_spread: float,
    market_total: float,
    reliability: float,
    seed: int,
    simulations: int = 20000,
) -> dict[str, float]:
    # The 2025 residuals replace the former hand-set volatility/correlation.
    # Reliability only scales those observed residuals modestly.
    reliability_factor = float(np.clip(1.0 + (70.0 - float(reliability)) * 0.004, 0.92, 1.18))
    margin_sd = max(7.0, float(MARGIN_RESIDUAL_SD) * reliability_factor)
    total_sd = max(7.0, float(TOTAL_RESIDUAL_SD) * reliability_factor)
    correlation = float(np.clip(MARGIN_TOTAL_RESIDUAL_CORRELATION, -0.50, 0.50))

    rng = np.random.default_rng(seed)
    z_margin = rng.standard_normal(simulations)
    z_independent = rng.standard_normal(simulations)
    z_total = correlation * z_margin + math.sqrt(max(0.0, 1.0 - correlation ** 2)) * z_independent
    margins = float(projection["margin"]) + margin_sd * z_margin
    totals = float(projection["total"]) + total_sd * z_total
    home_scores = np.clip((totals + margins) / 2.0, 0.0, 65.0)
    away_scores = np.clip((totals - margins) / 2.0, 0.0, 65.0)
    actual_totals = home_scores + away_scores
    actual_margins = home_scores - away_scores
    return {
        "home_win": float(np.mean(actual_margins > 0)),
        "home_cover": float(np.mean(actual_margins + float(home_spread) > 0)),
        "over": float(np.mean(actual_totals > float(market_total))),
        "away_low": float(np.quantile(away_scores, 0.20)),
        "away_high": float(np.quantile(away_scores, 0.80)),
        "home_low": float(np.quantile(home_scores, 0.20)),
        "home_high": float(np.quantile(home_scores, 0.80)),
        "margin_sd": margin_sd,
        "total_sd": total_sd,
    }


def spread_confluence(
    pick_home: bool,
    spread_edge: float,
    away: dict[str, Any],
    home: dict[str, Any],
    away_lineup: dict[str, float],
    home_lineup: dict[str, float],
    reliability: float,
) -> tuple[int, list[str]]:
    away_features = _matchup_features(away, home, 0.0)
    home_features = _matchup_features(home, away, 0.0)
    scoring_home = home_features["scoring_matchup"] >= away_features["scoring_matchup"]
    success_home = home_features["success_matchup"] >= away_features["success_matchup"]
    home_qb = _num(home.get("QB Adjustment")) - _num(home_lineup.get("offense_absence"))
    away_qb = _num(away.get("QB Adjustment")) - _num(away_lineup.get("offense_absence"))
    health_home = (
        _num(home_lineup.get("offense_absence")) + _num(home_lineup.get("defense_absence"))
        <= _num(away_lineup.get("offense_absence")) + _num(away_lineup.get("defense_absence"))
    )
    checks = [
        (spread_edge >= 1.5, "Model edge"),
        (scoring_home == pick_home, "Scoring matchup"),
        (success_home == pick_home, "Success-rate matchup"),
        (((home_qb >= away_qb) == pick_home), "QB/personnel"),
        (health_home == pick_home, "Lineup health"),
        (reliability >= 62, "Reliability"),
    ]
    passed = [label for ok, label in checks if ok]
    return len(passed), passed


def total_confluence(
    over_pick: bool,
    total_edge: float,
    projection: dict[str, float],
    away: dict[str, Any],
    home: dict[str, Any],
    weather_adjustment: float,
    reliability: float,
) -> tuple[int, list[str]]:
    away_features = _matchup_features(away, home, weather_adjustment)
    home_features = _matchup_features(home, away, weather_adjustment)
    scoring_sum = away_features["scoring_matchup"] + home_features["scoring_matchup"]
    success_sum = away_features["success_matchup"] + home_features["success_matchup"]
    checks = [
        (total_edge >= 1.75, "Model edge"),
        (((scoring_sum >= 45.0) == over_pick), "Scoring matchup"),
        (((success_sum >= 0.86) == over_pick), "Success-rate matchup"),
        (((_num(weather_adjustment) >= -0.25) == over_pick), "Weather/environment"),
        (reliability >= 62, "Reliability"),
    ]
    passed = [label for ok, label in checks if ok]
    return len(passed), passed


def moneyline_confluence(
    pick_home: bool,
    price_edge: float,
    away: dict[str, Any],
    home: dict[str, Any],
    away_lineup: dict[str, float],
    home_lineup: dict[str, float],
    reliability: float,
) -> tuple[int, list[str]]:
    away_features = _matchup_features(away, home, 0.0)
    home_features = _matchup_features(home, away, 0.0)
    scoring_home = home_features["scoring_matchup"] >= away_features["scoring_matchup"]
    success_home = home_features["success_matchup"] >= away_features["success_matchup"]
    health_home = (
        _num(home_lineup.get("offense_absence")) + _num(home_lineup.get("defense_absence"))
        <= _num(away_lineup.get("offense_absence")) + _num(away_lineup.get("defense_absence"))
    )
    checks = [
        (price_edge >= 0.05, "Price edge"),
        (scoring_home == pick_home, "Scoring matchup"),
        (success_home == pick_home, "Success-rate matchup"),
        (health_home == pick_home, "Lineup health"),
        (reliability >= 64, "Reliability"),
    ]
    passed = [label for ok, label in checks if ok]
    return len(passed), passed


def install_regression_layer(nfl_builder: Any) -> None:
    if getattr(nfl_builder, "_GAME_REGRESSION_LAYER_INSTALLED", False):
        return
    nfl_builder._project_matchup = project_matchup
    nfl_builder._simulate_game = simulate_game
    nfl_builder._spread_confluence = spread_confluence
    nfl_builder._total_confluence = total_confluence
    nfl_builder._moneyline_confluence = moneyline_confluence
    nfl_builder.MODEL_VERSION = MODEL_VERSION
    nfl_builder._GAME_REGRESSION_LAYER_INSTALLED = True
