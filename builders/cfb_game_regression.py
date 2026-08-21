"""Production CFB spread/margin regression layer.

Uses the validated 2021-23 discovery / 2024 confirmation / untouched 2025 holdout
team-score regression for the mean scoring margin while preserving the existing
CFB total model, simulation framework, market grading, personnel inputs, and
venue/weather pipeline.

Sportsbook spread/total are never regression predictors.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
import pandas as pd

MODEL_VERSION = "cfb-v2.0-team-score-regression-2026-08-21"
MODEL_RESEARCH_VERSION = "cfb-team-score-regression-v2-2026-08-21"
HFA_RESEARCH_BASELINE = 2.0

TEAM_SCORE_INTERCEPT = 11.240443677221451
TEAM_SCORE_COEFFICIENTS = {
    "prior_own_ppg": 0.26670808584236927,
    "prior_opp_papg": 0.22597582571150315,
    "prior_power_gap": 0.3024181814544948,
    "current_own_scoring_delta": 0.38741952777838656,
    "current_opp_allowed_delta": 0.2716376410485099,
    "current_power_delta": 0.3569282298069327,
    "home_indicator": 4.804668259201711,
}

_CONTEXT_CACHE: dict[tuple[int, int], tuple[dict[str, "TeamStats"], dict[str, "TeamStats"]]] = {}


@dataclass(frozen=True)
class TeamStats:
    power: float = 0.0
    ppg: float = 28.0
    papg: float = 28.0
    games: int = 0


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


def _normalize_team(value: Any) -> str:
    return " ".join(str(value or "").replace("&", "and").split())


def _completed_schedule(cfb_builder: Any, season: int, through_week: int | None = None) -> pd.DataFrame:
    """Load completed games using the production CFB public-data stack.

    ESPN is attempted first because it is already cached by the production builder.
    The normal schedule loader is the fallback. Only completed games strictly before
    ``through_week`` are retained, matching the leakage-safe research construction.
    """
    frame = pd.DataFrame()
    try:
        frame = cfb_builder._parse_games(cfb_builder._espn_games_payload(int(season)), int(season))
    except Exception:
        frame = pd.DataFrame()
    if frame.empty or "Completed" not in frame.columns or not frame["Completed"].map(_truthy).any():
        try:
            frame = cfb_builder._schedule_for_season(int(season))
        except Exception:
            frame = pd.DataFrame()
    if frame.empty:
        return frame
    done = frame[frame["Completed"].map(_truthy)].copy()
    done = done[pd.notna(done["Away Score"]) & pd.notna(done["Home Score"])].copy()
    if through_week is not None:
        done = done[pd.to_numeric(done["Week"], errors="coerce") < int(through_week)].copy()
    return done


def _fallback_summary(cfb_builder: Any, season: int, through_week: int | None = None) -> dict[str, TeamStats]:
    """Fallback to production season features if the game schedule is unavailable."""
    try:
        frame, _ = cfb_builder._season_features(int(season), through_week)
    except Exception:
        return {}
    if frame is None or frame.empty:
        return {}
    out: dict[str, TeamStats] = {}
    for _, row in frame.iterrows():
        team = _normalize_team(row.get("Team"))
        if not team:
            continue
        out[team] = TeamStats(
            power=_num(row.get("Result Power"), 0.0),
            ppg=_num(row.get("Points Per Game"), 28.0),
            papg=_num(row.get("Points Allowed Per Game"), 28.0),
            games=int(max(0.0, _num(row.get("Games"), 0.0))),
        )
    return out


def _team_summary(cfb_builder: Any, season: int, through_week: int | None = None) -> dict[str, TeamStats]:
    games = _completed_schedule(cfb_builder, season, through_week)
    if games.empty:
        return _fallback_summary(cfb_builder, season, through_week)

    teams = sorted(set(games["Away Team"].astype(str)) | set(games["Home Team"].astype(str)))
    by_team: dict[str, list[dict[str, Any]]] = {team: [] for team in teams}
    rows = games.to_dict("records")
    for game in rows:
        away = _normalize_team(game.get("Away Team"))
        home = _normalize_team(game.get("Home Team"))
        if away in by_team:
            by_team[away].append(game)
        if home in by_team:
            by_team[home].append(game)

    # Exact power construction used by the validated research: neutralize a 2-point
    # generic HFA, preserve large CFB mismatches up to +/-45, then opponent-adjust.
    power = {team: 0.0 for team in teams}
    for _ in range(30):
        updated: dict[str, float] = {}
        for team in teams:
            values: list[float] = []
            for game in by_team[team]:
                away = _normalize_team(game.get("Away Team"))
                home = _normalize_team(game.get("Home Team"))
                margin = _num(game.get("Home Score"), 0.0) - _num(game.get("Away Score"), 0.0)
                if not _truthy(game.get("Neutral Site", False)):
                    margin -= HFA_RESEARCH_BASELINE
                margin = float(np.clip(margin, -45.0, 45.0))
                if team == home:
                    opponent, team_margin = away, margin
                else:
                    opponent, team_margin = home, -margin
                values.append(team_margin + power.get(opponent, 0.0))
            updated[team] = float(np.mean(values)) if values else 0.0
        center = float(np.mean(list(updated.values()))) if updated else 0.0
        power = {team: float(np.clip(value - center, -45.0, 45.0)) for team, value in updated.items()}

    output: dict[str, TeamStats] = {}
    for team in teams:
        scored: list[float] = []
        allowed: list[float] = []
        for game in by_team[team]:
            home_side = team == _normalize_team(game.get("Home Team"))
            scored.append(_num(game.get("Home Score") if home_side else game.get("Away Score"), 0.0))
            allowed.append(_num(game.get("Away Score") if home_side else game.get("Home Score"), 0.0))
        output[team] = TeamStats(
            power=power.get(team, 0.0),
            ppg=float(np.mean(scored)) if scored else 28.0,
            papg=float(np.mean(allowed)) if allowed else 28.0,
            games=len(scored),
        )
    return output


def _context(cfb_builder: Any, season: int, week: int) -> tuple[dict[str, TeamStats], dict[str, TeamStats]]:
    key = (int(season), int(week))
    cached = _CONTEXT_CACHE.get(key)
    if cached is not None:
        return cached
    prior = _team_summary(cfb_builder, int(season) - 1, None)
    current = _team_summary(cfb_builder, int(season), int(week))
    _CONTEXT_CACHE[key] = (prior, current)
    return prior, current


def _stat(stats: dict[str, TeamStats], team: str) -> TeamStats:
    return stats.get(_normalize_team(team), TeamStats())


def _blend(prior: TeamStats, current: TeamStats) -> TeamStats:
    weight = current.games / (current.games + 4.0) if current.games else 0.0
    return TeamStats(
        power=(1.0 - weight) * prior.power + weight * current.power,
        ppg=(1.0 - weight) * prior.ppg + weight * current.ppg,
        papg=(1.0 - weight) * prior.papg + weight * current.papg,
        games=current.games,
    )


def _team_features(
    own_prior: TeamStats,
    opp_prior: TeamStats,
    own_current: TeamStats,
    opp_current: TeamStats,
    home_indicator: float,
) -> dict[str, float]:
    own_blend = _blend(own_prior, own_current)
    opp_blend = _blend(opp_prior, opp_current)
    prior_gap = own_prior.power - opp_prior.power
    return {
        "prior_own_ppg": own_prior.ppg,
        "prior_opp_papg": opp_prior.papg,
        "prior_power_gap": prior_gap,
        "current_own_scoring_delta": own_blend.ppg - own_prior.ppg,
        "current_opp_allowed_delta": opp_blend.papg - opp_prior.papg,
        "current_power_delta": (own_blend.power - opp_blend.power) - prior_gap,
        "home_indicator": float(home_indicator),
    }


def _team_score(features: dict[str, float]) -> float:
    return float(
        TEAM_SCORE_INTERCEPT
        + sum(float(coef) * _num(features.get(name), 0.0) for name, coef in TEAM_SCORE_COEFFICIENTS.items())
    )


def _regression_base(cfb_builder: Any, game: pd.Series) -> tuple[float, float, dict[str, float], dict[str, float]]:
    season = int(_num(game.get("Season"), getattr(cfb_builder, "DEFAULT_SEASON", 2026)))
    week = int(_num(game.get("Week"), 1.0))
    away_team = _normalize_team(game.get("Away Team"))
    home_team = _normalize_team(game.get("Home Team"))
    prior, current = _context(cfb_builder, season, week)

    away_prior, home_prior = _stat(prior, away_team), _stat(prior, home_team)
    away_current, home_current = _stat(current, away_team), _stat(current, home_team)
    neutral = _truthy(game.get("Neutral Site", False))

    away_features = _team_features(away_prior, home_prior, away_current, home_current, 0.0)
    home_features = _team_features(home_prior, away_prior, home_current, away_current, 0.0 if neutral else 1.0)
    return _team_score(away_features), _team_score(home_features), away_features, home_features


def _personnel_margin(away_personnel: Any, home_personnel: Any) -> float:
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
    return home_effect - away_effect


def _continuity_margin(legacy_projection: dict[str, Any], away_personnel: Any, home_personnel: Any) -> float:
    away = legacy_projection.get("away", {})
    home = legacy_projection.get("home", {})
    prior_weight = (_num(away.get("Previous Season Weight"), 1.0) + _num(home.get("Previous Season Weight"), 1.0)) / 2.0
    away_cont = (
        prior_weight * (_num(getattr(away_personnel, "qb_continuity", 0.50)) - 0.50) * 2.4
        + prior_weight * (
            (_num(getattr(away_personnel, "coaching_continuity", 0.75)) - 0.75)
            + (_num(getattr(away_personnel, "coordinator_continuity", 0.67)) - 0.67)
        )
    )
    home_cont = (
        prior_weight * (_num(getattr(home_personnel, "qb_continuity", 0.50)) - 0.50) * 2.4
        + prior_weight * (
            (_num(getattr(home_personnel, "coaching_continuity", 0.75)) - 0.75)
            + (_num(getattr(home_personnel, "coordinator_continuity", 0.67)) - 0.67)
        )
    )
    return home_cont - away_cont


def install_regression_layer(cfb_builder: Any) -> None:
    if getattr(cfb_builder, "_GAME_REGRESSION_LAYER_INSTALLED", False):
        return

    original_project_matchup = cfb_builder.project_matchup

    def project_matchup(game: pd.Series, ratings: pd.DataFrame, away_personnel: Any,
                        home_personnel: Any, environment: Any) -> dict[str, Any]:
        # The existing engine remains the total-model source. We replace only the
        # mean margin, because the holdout showed a strong margin improvement but
        # no statistically useful total improvement.
        legacy = original_project_matchup(game, ratings, away_personnel, home_personnel, environment)
        away_base, home_base, away_features, home_features = _regression_base(cfb_builder, game)
        regression_margin = home_base - away_base

        neutral = _truthy(game.get("Neutral Site", False))
        # The research regression already contains a generic home indicator. Add
        # only the game-specific venue/travel/rest/weather deviation from the
        # production league HFA so average HFA is not double-counted.
        venue_delta = 0.0 if neutral else _num(getattr(environment, "home_field", 0.0)) - _num(getattr(environment, "league_hfa", 0.0))
        personnel_delta = _personnel_margin(away_personnel, home_personnel)
        continuity_delta = _continuity_margin(legacy, away_personnel, home_personnel)
        final_margin = regression_margin + venue_delta + personnel_delta + continuity_delta

        projected_total = _num(legacy.get("total"), _num(legacy.get("away_points")) + _num(legacy.get("home_points")))
        final_margin = float(np.clip(final_margin, -60.0, 60.0))
        home_points = (projected_total + final_margin) / 2.0
        away_points = (projected_total - final_margin) / 2.0
        if away_points < 3.0:
            away_points = 3.0
            home_points = max(3.0, away_points + final_margin)
        if home_points < 3.0:
            home_points = 3.0
            away_points = max(3.0, home_points - final_margin)
        away_points = float(np.clip(away_points, 3.0, 65.0))
        home_points = float(np.clip(home_points, 3.0, 65.0))

        legacy.update({
            "away_points": away_points,
            "home_points": home_points,
            "margin": home_points - away_points,
            "total": home_points + away_points,
            "regression_base_away": away_base,
            "regression_base_home": home_base,
            "regression_base_margin": regression_margin,
            "regression_venue_delta": venue_delta,
            "regression_personnel_delta": personnel_delta,
            "regression_continuity_delta": continuity_delta,
            "regression_away_features": away_features,
            "regression_home_features": home_features,
            "regression_research_version": MODEL_RESEARCH_VERSION,
        })
        return legacy

    cfb_builder.project_matchup = project_matchup
    cfb_builder.MODEL_VERSION = MODEL_VERSION
    cfb_builder._GAME_REGRESSION_LAYER_INSTALLED = True
