"""Validated RB/WR opportunity x efficiency regression layer for EZPZ NFL.

Historical design:
- 2021-2023 discovery / p<0.05 backward selection
- 2024 coefficient-sign confirmation
- 2021-2024 final refit
- untouched 2025 holdout

The regression establishes the stable historical baseline. The production
builder's current depth-chart, injury/play-probability, route/role, matchup and
weather information is then retained as a capped live overlay so unexpected
same-week personnel changes are not ignored.

Final player-prop projections remain model-native: no post-projection residual
calibration is allowed to pull the regression output back toward a historical
center. Sportsbook lines are used only for probability/edge/EV comparison.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

MODEL_VERSION = "nfl-v4.3-model-native-player-props-2026-08-25"
RESEARCH_VERSION = "nfl-rb-wr-opportunity-efficiency-regression-2026-08-20"

RB_RUSH_OPP_INTERCEPT = 1.6934778404401407
RB_RUSH_OPP_COEFFICIENTS = {
    "raw_rb_carries": 0.342097086484109,
    "carries_avg3": 0.3118522121861481,
    "carry_share_avg3": 6.315645312414877,
    "team_rush_avg8": -0.09056666705251831,
    "opp_carries_avg8": 0.14015700893849362,
    "team_total": -0.06578821795950278,
}
RB_RUSH_EFF_INTERCEPT = 2.3170834243044025
RB_RUSH_EFF_COEFFICIENTS = {
    "raw_rb_ypc": 0.19228512390779248,
    "team_total": 0.04725670066213353,
    "team_spread": 0.045128674315713845,
}

RB_REC_OPP_INTERCEPT = 0.9138126685782093
RB_REC_OPP_COEFFICIENTS = {
    "raw_targets": 0.547853393829317,
    "targets_avg3": 0.21309421154633576,
    "team_targets_avg8": -0.027965748045495623,
    "opp_targets_avg8": 0.08760464015650735,
}
RB_REC_EFF_INTERCEPT = 5.084057746644786
RB_REC_EFF_COEFFICIENTS = {"raw_ypt": 0.10575900595918927}

WR_REC_OPP_INTERCEPT = -0.15535316389900333
WR_REC_OPP_COEFFICIENTS = {
    "raw_targets": 0.197192355940755,
    "targets_avg8": 0.45273761653622924,
    "target_share_avg3": 7.823119777439491,
    "team_total": 0.02608707180789477,
}
WR_REC_EFF_INTERCEPT = 3.827430151250454
WR_REC_EFF_COEFFICIENTS = {
    "raw_ypt": 0.14895607849862744,
    "air_yards_per_target8": 0.05749478767487837,
    "team_total": 0.09775151359040112,
    "team_spread": 0.11061710650626522,
    "home": 0.43298975092446274,
}

HOLDOUT_SUMMARY = {
    "rb_rushing_yards": {"baseline_mae": 25.237276394066175, "regression_mae": 23.857519496695403, "improvement_pct": 5.467138671489853},
    "rb_receiving_yards": {"baseline_mae": 14.443544694183766, "regression_mae": 13.30773051846806, "improvement_pct": 7.863818749237389},
    "wr_receiving_yards": {"baseline_mae": 26.316779000707708, "regression_mae": 25.31105105565945, "improvement_pct": 3.821622490431716},
    "rb_carries": {"baseline_mae": 4.2240153979038695, "regression_mae": 3.9571242242814644, "improvement_pct": 6.318423312444541},
    "rb_targets": {"baseline_mae": 1.6575331777679694, "regression_mae": 1.5945017059519366, "improvement_pct": 3.802727611215046},
    "wr_targets": {"baseline_mae": 2.285652709341656, "regression_mae": 2.2189746739368688, "improvement_pct": 2.917242638493081},
}

_CONTEXT_CACHE: dict[tuple[int, int, str, str, str, str], dict[str, float]] = {}


def _num(value: Any, default: float = np.nan) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def _linear(intercept: float, coefficients: dict[str, float], features: dict[str, float]) -> float:
    return float(intercept + sum(coef * _num(features.get(name), 0.0) for name, coef in coefficients.items()))


def project_rb_rushing(features: dict[str, float]) -> tuple[float, float]:
    carries = float(np.clip(_linear(RB_RUSH_OPP_INTERCEPT, RB_RUSH_OPP_COEFFICIENTS, features), 0.0, 35.0))
    ypc = float(np.clip(_linear(RB_RUSH_EFF_INTERCEPT, RB_RUSH_EFF_COEFFICIENTS, features), 2.0, 7.0))
    return carries, ypc


def project_rb_receiving(features: dict[str, float]) -> tuple[float, float]:
    targets = float(np.clip(_linear(RB_REC_OPP_INTERCEPT, RB_REC_OPP_COEFFICIENTS, features), 0.0, 15.0))
    ypt = float(np.clip(_linear(RB_REC_EFF_INTERCEPT, RB_REC_EFF_COEFFICIENTS, features), 2.5, 12.0))
    return targets, ypt


def project_wr_receiving(features: dict[str, float]) -> tuple[float, float]:
    targets = float(np.clip(_linear(WR_REC_OPP_INTERCEPT, WR_REC_OPP_COEFFICIENTS, features), 0.0, 20.0))
    ypt = float(np.clip(_linear(WR_REC_EFF_INTERCEPT, WR_REC_EFF_COEFFICIENTS, features), 3.0, 16.0))
    return targets, ypt


def _normalized_stats(nfl_builder: Any, season: int, through_week: int | None) -> pd.DataFrame:
    raw = nfl_builder._load_player_stats_season(int(season))
    if raw is None or raw.empty:
        return pd.DataFrame()
    df = raw.copy()
    if "season_type" in df.columns:
        df = df[df["season_type"].astype(str).str.upper() == "REG"].copy()
    if through_week is not None and "week" in df.columns:
        df = df[pd.to_numeric(df["week"], errors="coerce") <= int(through_week)].copy()
    if df.empty:
        return df
    df["season"] = int(season)
    df["week"] = pd.to_numeric(df.get("week"), errors="coerce")
    df = df[df["week"].notna()].copy()
    df["week"] = df["week"].astype(int)
    df["player_key"] = nfl_builder._player_name_column(df).map(nfl_builder._normalize_name)
    df["team"] = nfl_builder._player_team_column(df)
    df["opponent"] = nfl_builder._column(df, "opponent_team", default="").map(nfl_builder._normalize_team)
    df["position"] = nfl_builder._player_position_column(df).map(nfl_builder._position_group)
    for column in [
        "carries", "rushing_yards", "targets", "receiving_yards",
        "receiving_air_yards", "receiving_yards_after_catch",
    ]:
        df[column] = nfl_builder._numeric_frame_column(df, column)
    return df


def _ratio(frame: pd.DataFrame, numerator: str, denominator: str, default: float) -> float:
    den = float(pd.to_numeric(frame.get(denominator), errors="coerce").fillna(0).sum()) if not frame.empty else 0.0
    if den <= 0:
        return float(default)
    num = float(pd.to_numeric(frame.get(numerator), errors="coerce").fillna(0).sum())
    value = num / den
    return float(value) if math.isfinite(value) else float(default)


def _live_history_context(
    nfl_builder: Any,
    season: int,
    projection_week: int,
    player: str,
    team: str,
    opponent: str,
    position: str,
    team_total: float,
    home_away: str,
) -> dict[str, float]:
    key = (int(season), int(projection_week), nfl_builder._normalize_name(player), nfl_builder._normalize_team(team), nfl_builder._normalize_team(opponent), nfl_builder._position_group(position))
    cached = _CONTEXT_CACHE.get(key)
    if cached is not None:
        result = dict(cached)
        result["team_total"] = float(team_total)
        return result

    prior = _normalized_stats(nfl_builder, int(season) - 1, None)
    current = _normalized_stats(nfl_builder, int(season), max(0, int(projection_week) - 1)) if int(projection_week) > 1 else pd.DataFrame()
    history = pd.concat([prior, current], ignore_index=True) if not prior.empty or not current.empty else pd.DataFrame()
    if history.empty:
        return {"available": 0.0}
    history = history.sort_values(["season", "week"]).reset_index(drop=True)

    team_week = history.groupby(["season", "week", "team"], as_index=False).agg(
        team_rush=("carries", "sum"), team_targets=("targets", "sum")
    )
    history = history.merge(team_week, on=["season", "week", "team"], how="left")
    history["carry_share"] = history["carries"] / history["team_rush"].replace(0, np.nan)
    history["target_share_calc"] = history["targets"] / history["team_targets"].replace(0, np.nan)

    player_key = nfl_builder._normalize_name(player)
    player_hist = history[history["player_key"] == player_key].sort_values(["season", "week"]).copy()
    if len(player_hist) < 2:
        return {"available": 0.0, "player_games": float(len(player_hist))}

    last3 = player_hist.tail(3)
    last8 = player_hist.tail(8)
    current_team = nfl_builder._normalize_team(team)
    current_opp = nfl_builder._normalize_team(opponent)
    pos = nfl_builder._position_group(position)

    team_hist = team_week[team_week["team"] == current_team].sort_values(["season", "week"]).tail(8)
    defense_week = history[(history["opponent"] == current_opp) & (history["position"] == pos)].groupby(
        ["season", "week"], as_index=False
    ).agg(
        carries=("carries", "sum"), rushing_yards=("rushing_yards", "sum"),
        targets=("targets", "sum"), receiving_yards=("receiving_yards", "sum"),
    ).sort_values(["season", "week"]).tail(8)

    rb_ypc_prior = 4.25
    ypt_prior = 6.15 if pos == "RB" else 8.15
    ypc8 = _ratio(last8, "rushing_yards", "carries", rb_ypc_prior)
    ypt8 = _ratio(last8, "receiving_yards", "targets", ypt_prior)
    air_ypt8 = _ratio(last8, "receiving_air_yards", "targets", 8.5 if pos == "WR" else 2.0)
    opp_ypc8 = _ratio(defense_week, "rushing_yards", "carries", rb_ypc_prior)
    opp_ypt8 = _ratio(defense_week, "receiving_yards", "targets", ypt_prior)

    carry_share_avg8 = float(pd.to_numeric(last8["carry_share"], errors="coerce").mean())
    target_share_avg8 = float(pd.to_numeric(last8["target_share_calc"], errors="coerce").mean())
    carry_share_avg3 = float(pd.to_numeric(last3["carry_share"], errors="coerce").mean())
    target_share_avg3 = float(pd.to_numeric(last3["target_share_calc"], errors="coerce").mean())
    team_rush_avg8 = float(pd.to_numeric(team_hist["team_rush"], errors="coerce").mean()) if not team_hist.empty else 27.0
    team_targets_avg8 = float(pd.to_numeric(team_hist["team_targets"], errors="coerce").mean()) if not team_hist.empty else 34.0
    opp_carries_avg8 = float(pd.to_numeric(defense_week["carries"], errors="coerce").mean()) if not defense_week.empty else team_rush_avg8
    opp_targets_avg8 = float(pd.to_numeric(defense_week["targets"], errors="coerce").mean()) if not defense_week.empty else (7.0 if pos == "RB" else 18.0)

    schedule = nfl_builder._schedule_for_season(int(season))
    team_spread = 0.0
    home = 1.0 if str(home_away).lower() == "home" else 0.0
    if schedule is not None and not schedule.empty:
        frame = schedule.copy()
        frame["_week"] = pd.to_numeric(frame.get("Week"), errors="coerce")
        frame["_home"] = frame.get("Home Team", "").map(nfl_builder._normalize_team)
        frame["_away"] = frame.get("Away Team", "").map(nfl_builder._normalize_team)
        matches = frame[(frame["_week"] == int(projection_week)) & (
            ((frame["_home"] == current_team) & (frame["_away"] == current_opp)) |
            ((frame["_away"] == current_team) & (frame["_home"] == current_opp))
        )]
        if not matches.empty:
            game = matches.iloc[-1]
            home_spread = _num(game.get("Spread Line"), 0.0)
            if math.isfinite(home_spread):
                team_spread = home_spread if nfl_builder._normalize_team(game.get("Home Team", "")) == current_team else -home_spread
            home = 1.0 if nfl_builder._normalize_team(game.get("Home Team", "")) == current_team else 0.0

    context = {
        "available": 1.0,
        "player_games": float(len(player_hist)),
        "carries_avg3": float(pd.to_numeric(last3["carries"], errors="coerce").mean()),
        "targets_avg3": float(pd.to_numeric(last3["targets"], errors="coerce").mean()),
        "targets_avg8": float(pd.to_numeric(last8["targets"], errors="coerce").mean()),
        "carry_share_avg3": carry_share_avg3 if math.isfinite(carry_share_avg3) else 0.30,
        "carry_share_avg8": carry_share_avg8 if math.isfinite(carry_share_avg8) else 0.30,
        "target_share_avg3": target_share_avg3 if math.isfinite(target_share_avg3) else (0.10 if pos == "RB" else 0.20),
        "target_share_avg8": target_share_avg8 if math.isfinite(target_share_avg8) else (0.10 if pos == "RB" else 0.20),
        "team_rush_avg8": team_rush_avg8,
        "team_targets_avg8": team_targets_avg8,
        "opp_carries_avg8": opp_carries_avg8,
        "opp_targets_avg8": opp_targets_avg8,
        "ypc8": ypc8,
        "ypt8": ypt8,
        "air_yards_per_target8": air_ypt8,
        "opp_ypc8": opp_ypc8,
        "opp_ypt8": opp_ypt8,
        "team_spread": float(team_spread),
        "home": float(home),
    }
    context["raw_rb_carries"] = team_rush_avg8 * context["carry_share_avg8"]
    context["raw_rb_ypc"] = ypc8 * float(np.clip(opp_ypc8 / rb_ypc_prior, 0.82, 1.18))
    context["raw_targets"] = team_targets_avg8 * context["target_share_avg8"]
    context["raw_ypt"] = ypt8 * float(np.clip(opp_ypt8 / ypt_prior, 0.82, 1.18))
    _CONTEXT_CACHE[key] = dict(context)
    context["team_total"] = float(team_total)
    return context


def _row(rows: list[dict[str, Any]], market: str) -> dict[str, Any] | None:
    return next((item for item in rows if str(item.get("Market", "")) == market), None)


def _finish_count_row(nfl_builder: Any, row: dict[str, Any] | None, raw_value: float, field: str) -> None:
    if row is None:
        return
    projection = max(0.0, raw_value)
    row["Raw Projection"] = round(raw_value, 2)
    row["Calibration Adjustment"] = 0.0
    row["Projection"] = round(projection, 2)
    row["Fair Line"] = nfl_builder._fair_line(projection, str(row.get("Market", "")))
    row[field] = round(raw_value, 2)
    row["_sd"] = nfl_builder._prop_sd(str(row.get("Market", "")), projection, _num(row.get("Reliability"), 70.0))


def _finish_yards_row(
    nfl_builder: Any,
    row: dict[str, Any] | None,
    opportunity: float,
    efficiency: float,
    opportunity_field: str,
    reason: str,
) -> None:
    if row is None:
        return
    raw_projection = max(0.0, opportunity * efficiency)
    projection = raw_projection
    row["Raw Projection"] = round(raw_projection, 2)
    row["Calibration Adjustment"] = 0.0
    row["Projection"] = round(projection, 2)
    row["Fair Line"] = nfl_builder._fair_line(projection, str(row.get("Market", "")))
    row[opportunity_field] = round(opportunity, 2)
    row["Efficiency"] = round(efficiency, 3)
    row["Confluence"] = reason
    row["_sd"] = nfl_builder._prop_sd(str(row.get("Market", "")), projection, _num(row.get("Reliability"), 70.0))


def _live_multiplier(current_value: float, historical_raw: float, low: float, high: float) -> float:
    if current_value <= 0:
        return 0.0
    if not math.isfinite(historical_raw) or historical_raw <= 0.05:
        return 1.0
    return float(np.clip(current_value / historical_raw, low, high))


def _install_model_native_prop_projection(nfl_builder: Any) -> None:
    """Disable the legacy residual-calibration step for every NFL prop market."""
    def no_post_projection_adjustment(player: str, position: str, market: str, opponent: str) -> dict[str, float]:
        return {"adjustment": 0.0, "sample": 0, "residual_sd": np.nan}

    nfl_builder._prop_calibration_adjustment = no_post_projection_adjustment


def install_skill_prop_regression(nfl_builder: Any) -> None:
    # Apply this even on a Streamlit rerun where the regression wrapper may
    # already be installed. The final projection should always equal the core
    # model/regression output, never a residual-shrunk follow-up value.
    _install_model_native_prop_projection(nfl_builder)
    if getattr(nfl_builder, "_SKILL_PROP_REGRESSION_INSTALLED", False):
        return
    original = nfl_builder._project_player_markets

    def wrapped(
        player: str, position: str, slot: str, team: str, opponent: str, home_away: str,
        lineup: pd.DataFrame, profiles: pd.DataFrame, defense_profiles: pd.DataFrame,
        team_rating: dict[str, Any], opponent_rating: dict[str, Any], game_projection: dict[str, float],
        weather_adjustment: float, market_lines: dict[tuple[str, str], dict[str, Any]],
        role_context: dict[str, dict[str, Any]] | None = None,
        pregame_team_total: float | None = None,
        pregame_team_total_source: str = "EZPZ score fallback",
    ) -> list[dict[str, Any]]:
        rows = original(
            player, position, slot, team, opponent, home_away, lineup, profiles, defense_profiles,
            team_rating, opponent_rating, game_projection, weather_adjustment, market_lines,
            role_context, pregame_team_total, pregame_team_total_source,
        )
        pos = nfl_builder._position_group(position)
        if pos not in {"RB", "WR"}:
            return rows
        season = int(_num(team_rating.get("Season"), 0))
        week = int(_num(team_rating.get("Projection Week"), 0))
        team_total = _num(pregame_team_total, game_projection.get("home_score" if str(home_away).lower() == "home" else "away_score", 22.5))
        if season <= 0 or week <= 0:
            return rows
        context = _live_history_context(nfl_builder, season, week, player, team, opponent, pos, team_total, home_away)
        if context.get("available", 0.0) < 0.5:
            return rows

        if pos == "RB":
            old_rush = _row(rows, "Rushing Yards")
            old_carries = _num(old_rush.get("Projected Player Attempts"), 0.0) if old_rush else 0.0
            old_ypc = _num(old_rush.get("Efficiency"), context["raw_rb_ypc"]) if old_rush else context["raw_rb_ypc"]
            base_carries, base_ypc = project_rb_rushing(context)
            carry_overlay = _live_multiplier(old_carries, context["raw_rb_carries"], 0.0, 1.80)
            efficiency_overlay = _live_multiplier(old_ypc, context["raw_rb_ypc"], 0.90, 1.10)
            carries = float(np.clip(base_carries * carry_overlay, 0.0, 35.0))
            ypc = float(np.clip(base_ypc * efficiency_overlay, 2.0, 7.0))
            _finish_count_row(nfl_builder, _row(rows, "Rushing Attempts"), carries, "Projected Player Attempts")
            _finish_yards_row(
                nfl_builder, old_rush, carries, ypc, "Projected Player Attempts",
                f"v4.1 regression carries × YPC • 2025 holdout MAE -5.5% • live role/injury overlay {carry_overlay:.2f}x"
            )

            old_rec = _row(rows, "Receiving Yards")
            old_targets = _num(old_rec.get("Projected Targets"), 0.0) if old_rec else 0.0
            old_ypt = _num(old_rec.get("Efficiency"), context["raw_ypt"]) if old_rec else context["raw_ypt"]
            base_targets, base_ypt = project_rb_receiving(context)
            target_overlay = _live_multiplier(old_targets, context["raw_targets"], 0.0, 1.80)
            rec_eff_overlay = _live_multiplier(old_ypt, context["raw_ypt"], 0.90, 1.10)
            targets = float(np.clip(base_targets * target_overlay, 0.0, 15.0))
            ypt = float(np.clip(base_ypt * rec_eff_overlay, 2.5, 12.0))
            _finish_count_row(nfl_builder, _row(rows, "Targets"), targets, "Projected Targets")
            _finish_yards_row(
                nfl_builder, old_rec, targets, ypt, "Projected Targets",
                f"v4.1 regression targets × YPT • 2025 holdout MAE -7.9% • live role/injury overlay {target_overlay:.2f}x"
            )

        elif pos == "WR":
            old_rec = _row(rows, "Receiving Yards")
            old_targets = _num(old_rec.get("Projected Targets"), 0.0) if old_rec else 0.0
            old_ypt = _num(old_rec.get("Efficiency"), context["raw_ypt"]) if old_rec else context["raw_ypt"]
            base_targets, base_ypt = project_wr_receiving(context)
            target_overlay = _live_multiplier(old_targets, context["raw_targets"], 0.0, 1.80)
            rec_eff_overlay = _live_multiplier(old_ypt, context["raw_ypt"], 0.90, 1.10)
            targets = float(np.clip(base_targets * target_overlay, 0.0, 20.0))
            ypt = float(np.clip(base_ypt * rec_eff_overlay, 3.0, 16.0))
            _finish_count_row(nfl_builder, _row(rows, "Targets"), targets, "Projected Targets")
            _finish_yards_row(
                nfl_builder, old_rec, targets, ypt, "Projected Targets",
                f"v4.1 regression targets × YPT • 2025 holdout MAE -3.8% • live role/injury overlay {target_overlay:.2f}x"
            )
        return rows

    nfl_builder._project_player_markets = wrapped
    nfl_builder.MODEL_VERSION = MODEL_VERSION
    nfl_builder._SKILL_PROP_REGRESSION_INSTALLED = True
