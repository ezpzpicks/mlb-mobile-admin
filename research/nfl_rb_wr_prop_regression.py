"""Leakage-safe RB/WR prop regression research for EZPZ NFL.

The goal is to put RB and WR yardage props on the same core formulation used by
QB passing yards and the MLB pitcher model:

    projected opportunity x projected efficiency = projected yards

Every feature is constructed from games completed before the game being
predicted. Discovery seasons are 2021-2023, 2024 is used for coefficient-sign
confirmation, and 2025 is an untouched out-of-sample holdout.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm

from builders import nfl_builder as nflb

DISCOVERY = [2021, 2022, 2023]
CONFIRMATION = [2024]
HOLDOUT = [2025]
LOAD_SEASONS = [2020, 2021, 2022, 2023, 2024, 2025]


def num(value: Any, default: float = np.nan) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def first_column(df: pd.DataFrame, names: list[str], default: str = "") -> pd.Series:
    for name in names:
        if name in df.columns:
            return df[name]
    return pd.Series(default, index=df.index)


def normalize_position(value: Any) -> str:
    text = str(value or "").upper().strip()
    if text in {"RB", "HB", "FB"}:
        return "RB"
    if text in {"WR"}:
        return "WR"
    return text


def normalize_player_stats(season: int) -> pd.DataFrame:
    raw = nflb._load_player_stats_season(int(season))
    if raw is None or raw.empty:
        raise RuntimeError(f"No nflverse player stats for {season}")
    df = raw.copy()
    if "season_type" in df.columns:
        df = df[df["season_type"].astype(str).str.upper() == "REG"].copy()
    df["season"] = int(season)
    df["week"] = pd.to_numeric(df.get("week"), errors="coerce")
    df = df[df["week"].notna()].copy()
    df["week"] = df["week"].astype(int)
    df["player"] = first_column(df, ["player_display_name", "player_name", "full_name"]).astype(str).str.strip()
    df["player_key"] = df["player"].map(nflb._normalize_name)
    df["team"] = first_column(df, ["recent_team", "team"]).map(nflb._normalize_team)
    df["opponent"] = first_column(df, ["opponent_team"]).map(nflb._normalize_team)
    df["position"] = first_column(df, ["position", "position_group"]).map(normalize_position)
    numeric_columns = [
        "attempts", "carries", "rushing_yards", "rushing_epa", "targets", "receptions",
        "receiving_yards", "receiving_epa", "receiving_air_yards", "receiving_yards_after_catch",
    ]
    for col in numeric_columns:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df[df["player_key"].str.len() > 0].copy()


def rolling_mean_shifted(frame: pd.DataFrame, group: list[str], value: str, window: int) -> pd.Series:
    return frame.groupby(group, sort=False)[value].transform(
        lambda s: s.shift(1).rolling(window=window, min_periods=1).mean()
    )


def rolling_ratio_shifted(
    frame: pd.DataFrame, group: list[str], numerator: str, denominator: str, window: int
) -> pd.Series:
    num_roll = frame.groupby(group, sort=False)[numerator].transform(
        lambda s: s.shift(1).rolling(window=window, min_periods=1).sum()
    )
    den_roll = frame.groupby(group, sort=False)[denominator].transform(
        lambda s: s.shift(1).rolling(window=window, min_periods=1).sum()
    )
    return num_roll / den_roll.replace(0, np.nan)


def add_game_context(players: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for season in sorted(players["season"].unique()):
        schedule = nflb._schedule_for_season(int(season), refresh=True)
        if schedule is None or schedule.empty:
            continue
        schedule = schedule.copy()
        if "Game Type" in schedule.columns:
            schedule = schedule[schedule["Game Type"].astype(str).str.upper() == "REG"].copy()
        for _, game in schedule.iterrows():
            week = int(num(game.get("Week"), 0))
            if week <= 0:
                continue
            home = nflb._normalize_team(game.get("Home Team", ""))
            away = nflb._normalize_team(game.get("Away Team", ""))
            spread = num(game.get("Spread Line"), 0.0)
            total = num(game.get("Total Line"), 45.0)
            if not math.isfinite(total):
                total = 45.0
            if not math.isfinite(spread):
                spread = 0.0
            rows.append({
                "season": int(season), "week": week, "team": home, "home": 1.0,
                "team_spread": spread, "game_total": total,
                "team_total": float(np.clip((total - spread) / 2.0, 6.0, 48.0)),
            })
            rows.append({
                "season": int(season), "week": week, "team": away, "home": 0.0,
                "team_spread": -spread, "game_total": total,
                "team_total": float(np.clip((total + spread) / 2.0, 6.0, 48.0)),
            })
    context = pd.DataFrame(rows).drop_duplicates(["season", "week", "team"], keep="last")
    return players.merge(context, on=["season", "week", "team"], how="left")


def build_dataset() -> pd.DataFrame:
    all_stats = pd.concat([normalize_player_stats(season) for season in LOAD_SEASONS], ignore_index=True)
    all_stats = all_stats.sort_values(["season", "week", "team", "player_key"]).reset_index(drop=True)

    # Weekly team opportunity pools.
    team_week = all_stats.groupby(["season", "week", "team"], as_index=False).agg(
        team_rush_attempts=("carries", "sum"),
        team_targets=("targets", "sum"),
    )
    all_stats = all_stats.merge(team_week, on=["season", "week", "team"], how="left")
    all_stats["carry_share"] = all_stats["carries"] / all_stats["team_rush_attempts"].replace(0, np.nan)
    all_stats["target_share_calc"] = all_stats["targets"] / all_stats["team_targets"].replace(0, np.nan)

    # Player lagged opportunity/efficiency features. Rolling history crosses the
    # season boundary, so Week 1 naturally inherits prior-season evidence.
    all_stats = all_stats.sort_values(["player_key", "season", "week"]).reset_index(drop=True)
    for window in [3, 8]:
        all_stats[f"carries_avg{window}"] = rolling_mean_shifted(all_stats, ["player_key"], "carries", window)
        all_stats[f"targets_avg{window}"] = rolling_mean_shifted(all_stats, ["player_key"], "targets", window)
        all_stats[f"carry_share_avg{window}"] = rolling_mean_shifted(all_stats, ["player_key"], "carry_share", window)
        all_stats[f"target_share_avg{window}"] = rolling_mean_shifted(all_stats, ["player_key"], "target_share_calc", window)
        all_stats[f"ypc{window}"] = rolling_ratio_shifted(all_stats, ["player_key"], "rushing_yards", "carries", window)
        all_stats[f"rush_epa_per_carry{window}"] = rolling_ratio_shifted(all_stats, ["player_key"], "rushing_epa", "carries", window)
        all_stats[f"ypt{window}"] = rolling_ratio_shifted(all_stats, ["player_key"], "receiving_yards", "targets", window)
        all_stats[f"rec_epa_per_target{window}"] = rolling_ratio_shifted(all_stats, ["player_key"], "receiving_epa", "targets", window)
        all_stats[f"air_yards_per_target{window}"] = rolling_ratio_shifted(all_stats, ["player_key"], "receiving_air_yards", "targets", window)
        all_stats[f"yac_per_reception{window}"] = rolling_ratio_shifted(all_stats, ["player_key"], "receiving_yards_after_catch", "receptions", window)

    # Team lagged opportunity pools.
    team_week = team_week.sort_values(["team", "season", "week"]).reset_index(drop=True)
    for window in [3, 8]:
        team_week[f"team_rush_avg{window}"] = rolling_mean_shifted(team_week, ["team"], "team_rush_attempts", window)
        team_week[f"team_targets_avg{window}"] = rolling_mean_shifted(team_week, ["team"], "team_targets", window)
    all_stats = all_stats.merge(
        team_week[["season", "week", "team", "team_rush_avg3", "team_rush_avg8", "team_targets_avg3", "team_targets_avg8"]],
        on=["season", "week", "team"], how="left"
    )

    # Position-specific opponent allowances, strictly lagged.
    skill = all_stats[all_stats["position"].isin(["RB", "WR"])].copy()
    defense_week = skill.groupby(["season", "week", "opponent", "position"], as_index=False).agg(
        allowed_carries=("carries", "sum"), allowed_rush_yards=("rushing_yards", "sum"),
        allowed_targets=("targets", "sum"), allowed_rec_yards=("receiving_yards", "sum"),
    ).rename(columns={"opponent": "defense"})
    defense_week = defense_week.sort_values(["defense", "position", "season", "week"]).reset_index(drop=True)
    defense_week["opp_carries_avg8"] = rolling_mean_shifted(defense_week, ["defense", "position"], "allowed_carries", 8)
    defense_week["opp_targets_avg8"] = rolling_mean_shifted(defense_week, ["defense", "position"], "allowed_targets", 8)
    defense_week["opp_ypc8"] = rolling_ratio_shifted(defense_week, ["defense", "position"], "allowed_rush_yards", "allowed_carries", 8)
    defense_week["opp_ypt8"] = rolling_ratio_shifted(defense_week, ["defense", "position"], "allowed_rec_yards", "allowed_targets", 8)
    skill = skill.merge(
        defense_week[["season", "week", "defense", "position", "opp_carries_avg8", "opp_targets_avg8", "opp_ypc8", "opp_ypt8"]],
        left_on=["season", "week", "opponent", "position"],
        right_on=["season", "week", "defense", "position"], how="left"
    )
    skill = add_game_context(skill)

    # Robust position priors are calculated only from the historical dataset at
    # this stage; they are used only to fill missing early-career lagged rates.
    rb_ypc_prior = skill.loc[(skill["position"] == "RB") & (skill["carries"] >= 3), "rushing_yards"].sum() / max(
        skill.loc[(skill["position"] == "RB") & (skill["carries"] >= 3), "carries"].sum(), 1
    )
    priors: dict[str, float] = {
        "RB_ypc": float(rb_ypc_prior),
        "RB_ypt": float(skill.loc[(skill["position"] == "RB") & (skill["targets"] >= 1), "receiving_yards"].sum() / max(skill.loc[(skill["position"] == "RB") & (skill["targets"] >= 1), "targets"].sum(), 1)),
        "WR_ypt": float(skill.loc[(skill["position"] == "WR") & (skill["targets"] >= 1), "receiving_yards"].sum() / max(skill.loc[(skill["position"] == "WR") & (skill["targets"] >= 1), "targets"].sum(), 1)),
    }
    for pos in ["RB", "WR"]:
        mask = skill["position"] == pos
        ypt_prior = priors[f"{pos}_ypt"]
        skill.loc[mask, "ypt8"] = skill.loc[mask, "ypt8"].fillna(ypt_prior)
        skill.loc[mask, "opp_ypt8"] = skill.loc[mask, "opp_ypt8"].fillna(ypt_prior)
    rb = skill["position"] == "RB"
    skill.loc[rb, "ypc8"] = skill.loc[rb, "ypc8"].fillna(priors["RB_ypc"])
    skill.loc[rb, "opp_ypc8"] = skill.loc[rb, "opp_ypc8"].fillna(priors["RB_ypc"])

    # Fill opportunity histories conservatively from the shorter rolling window.
    for long_col, short_col in [
        ("carries_avg8", "carries_avg3"), ("targets_avg8", "targets_avg3"),
        ("carry_share_avg8", "carry_share_avg3"), ("target_share_avg8", "target_share_avg3"),
        ("team_rush_avg8", "team_rush_avg3"), ("team_targets_avg8", "team_targets_avg3"),
    ]:
        skill[long_col] = pd.to_numeric(skill[long_col], errors="coerce").fillna(pd.to_numeric(skill[short_col], errors="coerce"))

    # Raw pregame formulas give us an apples-to-apples baseline. Regression then
    # calibrates opportunity and efficiency separately instead of directly fitting yards.
    league_rb_ypc = priors["RB_ypc"]
    skill["raw_rb_carries"] = skill["team_rush_avg8"] * skill["carry_share_avg8"]
    skill["raw_rb_ypc"] = skill["ypc8"] * np.clip(skill["opp_ypc8"] / league_rb_ypc, 0.82, 1.18)
    skill["raw_targets"] = skill["team_targets_avg8"] * skill["target_share_avg8"]
    skill["raw_ypt"] = np.nan
    for pos in ["RB", "WR"]:
        mask = skill["position"] == pos
        prior = priors[f"{pos}_ypt"]
        skill.loc[mask, "raw_ypt"] = skill.loc[mask, "ypt8"] * np.clip(skill.loc[mask, "opp_ypt8"] / prior, 0.82, 1.18)

    skill["actual_ypc"] = skill["rushing_yards"] / skill["carries"].replace(0, np.nan)
    skill["actual_ypt"] = skill["receiving_yards"] / skill["targets"].replace(0, np.nan)
    return skill.replace([np.inf, -np.inf], np.nan)


def fit(df: pd.DataFrame, target: str, features: list[str]):
    clean = df[[target] + features].dropna()
    x = sm.add_constant(clean[features].astype(float), has_constant="add")
    return sm.OLS(clean[target].astype(float), x).fit(cov_type="HC3")


def select_model(
    discovery: pd.DataFrame,
    confirmation: pd.DataFrame,
    target: str,
    candidates: list[str],
    forced: set[str],
) -> dict[str, Any]:
    features = list(dict.fromkeys(candidates))
    removed: list[dict[str, Any]] = []
    while len(features) > len(forced):
        model = fit(discovery, target, features)
        pvals = model.pvalues.drop(labels=["const"], errors="ignore")
        eligible = pvals.drop(labels=[f for f in forced if f in pvals.index], errors="ignore")
        if eligible.empty or float(eligible.max()) <= 0.05:
            break
        worst = str(eligible.idxmax())
        removed.append({"feature": worst, "reason": "p>0.05", "p_value": float(eligible[worst])})
        features.remove(worst)

    dm = fit(discovery, target, features)
    cm = fit(confirmation, target, features)
    unstable = [
        feature for feature in features
        if feature not in forced
        and feature in dm.params and feature in cm.params
        and np.sign(dm.params[feature]) != np.sign(cm.params[feature])
    ]
    if unstable and len(features) - len(unstable) >= len(forced):
        for feature in unstable:
            removed.append({"feature": feature, "reason": "2024 coefficient sign flip"})
        features = [f for f in features if f not in unstable]
        dm = fit(discovery, target, features)
        cm = fit(confirmation, target, features)

    final = fit(pd.concat([discovery, confirmation], ignore_index=True), target, features)
    return {
        "features": features,
        "removed": removed,
        "discovery_pvalues": {k: float(v) for k, v in dm.pvalues.items()},
        "discovery_coefficients": {k: float(v) for k, v in dm.params.items()},
        "confirmation_coefficients": {k: float(v) for k, v in cm.params.items()},
        "intercept": float(final.params.get("const", 0.0)),
        "coefficients": {k: float(v) for k, v in final.params.items() if k != "const"},
        "model": final,
    }


def predict(model_info: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    features = model_info["features"]
    x = sm.add_constant(frame[features].astype(float), has_constant="add")
    return np.asarray(model_info["model"].predict(x), float)


def metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    actual = np.asarray(actual, float); predicted = np.asarray(predicted, float)
    error = actual - predicted
    return {
        "n": int(len(actual)),
        "mae": float(np.mean(np.abs(error))),
        "rmse": float(np.sqrt(np.mean(error ** 2))),
        "bias": float(np.mean(predicted - actual)),
    }


def clean_model(info: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in info.items() if k != "model"}


def run_market(
    data: pd.DataFrame,
    position: str,
    kind: str,
    opportunity_target: str,
    efficiency_target: str,
    raw_opportunity: str,
    raw_efficiency: str,
    opportunity_candidates: list[str],
    efficiency_candidates: list[str],
    role_filter: str,
    min_efficiency_opportunity: float,
    opportunity_clip: tuple[float, float],
    efficiency_clip: tuple[float, float],
    actual_yards_col: str,
) -> dict[str, Any]:
    subset = data[data["position"] == position].copy()
    subset = subset[pd.to_numeric(subset[role_filter], errors="coerce").notna()].copy()
    if kind == "rush":
        subset = subset[subset[role_filter] >= 3.0]
    else:
        subset = subset[subset[role_filter] >= (1.5 if position == "RB" else 2.5)]

    discovery = subset[subset["season"].isin(DISCOVERY)].copy()
    confirmation = subset[subset["season"].isin(CONFIRMATION)].copy()
    holdout = subset[subset["season"].isin(HOLDOUT)].copy()

    opp_train_discovery = discovery.dropna(subset=[opportunity_target] + opportunity_candidates)
    opp_train_confirmation = confirmation.dropna(subset=[opportunity_target] + opportunity_candidates)
    eff_discovery = discovery[discovery[opportunity_target] >= min_efficiency_opportunity].dropna(subset=[efficiency_target] + efficiency_candidates)
    eff_confirmation = confirmation[confirmation[opportunity_target] >= min_efficiency_opportunity].dropna(subset=[efficiency_target] + efficiency_candidates)

    opportunity = select_model(opp_train_discovery, opp_train_confirmation, opportunity_target, opportunity_candidates, {raw_opportunity})
    efficiency = select_model(eff_discovery, eff_confirmation, efficiency_target, efficiency_candidates, {raw_efficiency})

    usable = holdout.dropna(subset=list(set(opportunity["features"] + efficiency["features"] + [raw_opportunity, raw_efficiency, actual_yards_col]))).copy()
    pred_opp = np.clip(predict(opportunity, usable), *opportunity_clip)
    pred_eff = np.clip(predict(efficiency, usable), *efficiency_clip)
    pred_yards = pred_opp * pred_eff
    raw_opp = np.clip(usable[raw_opportunity].to_numpy(float), *opportunity_clip)
    raw_eff = np.clip(usable[raw_efficiency].to_numpy(float), *efficiency_clip)
    raw_yards = raw_opp * raw_eff
    actual_yards = usable[actual_yards_col].to_numpy(float)

    new_metrics = metrics(actual_yards, pred_yards)
    baseline_metrics = metrics(actual_yards, raw_yards)
    improvement = 100.0 * (baseline_metrics["mae"] - new_metrics["mae"]) / max(baseline_metrics["mae"], 1e-9)
    return {
        "position": position,
        "kind": kind,
        "rows": {"discovery": int(len(discovery)), "confirmation": int(len(confirmation)), "holdout": int(len(usable))},
        "opportunity_model": clean_model(opportunity),
        "efficiency_model": clean_model(efficiency),
        "baseline_yards_metrics": baseline_metrics,
        "regression_yards_metrics": new_metrics,
        "mae_improvement_pct": float(improvement),
        "recommended": bool(new_metrics["mae"] < baseline_metrics["mae"]),
        "holdout_component_metrics": {
            "opportunity": metrics(usable[opportunity_target].to_numpy(float), pred_opp),
            "efficiency_on_volume_games": metrics(
                usable.loc[usable[opportunity_target] >= min_efficiency_opportunity, efficiency_target].dropna().to_numpy(float),
                np.clip(
                    predict(efficiency, usable.loc[usable[opportunity_target] >= min_efficiency_opportunity].dropna(subset=[efficiency_target] + efficiency["features"])),
                    *efficiency_clip,
                )[: len(usable.loc[usable[opportunity_target] >= min_efficiency_opportunity, efficiency_target].dropna())]
            ) if len(usable.loc[usable[opportunity_target] >= min_efficiency_opportunity, efficiency_target].dropna()) else {},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="artifacts/nfl_rb_wr_prop_regression")
    args = parser.parse_args()
    output = Path(args.output_dir); output.mkdir(parents=True, exist_ok=True)
    data = build_dataset()
    data.to_csv(output / "rb_wr_pregame_dataset.csv", index=False)

    common_context = ["team_total", "team_spread", "home"]
    results: dict[str, Any] = {
        "research_version": "nfl-rb-wr-opportunity-efficiency-regression-2026-08-20",
        "method": "2021-23 HC3 OLS backward selection p<0.05; 2024 coefficient-sign confirmation; final refit 2021-24; untouched 2025 holdout; all player/team/opponent features lagged before game",
    }

    results["rb_rushing_yards"] = run_market(
        data, "RB", "rush", "carries", "actual_ypc", "raw_rb_carries", "raw_rb_ypc",
        ["raw_rb_carries", "carries_avg3", "carries_avg8", "carry_share_avg3", "carry_share_avg8", "team_rush_avg3", "team_rush_avg8", "opp_carries_avg8"] + common_context,
        ["raw_rb_ypc", "ypc8", "rush_epa_per_carry8", "opp_ypc8"] + common_context,
        "raw_rb_carries", 3.0, (0.0, 35.0), (2.0, 7.0), "rushing_yards"
    )
    results["rb_receiving_yards"] = run_market(
        data, "RB", "receive", "targets", "actual_ypt", "raw_targets", "raw_ypt",
        ["raw_targets", "targets_avg3", "targets_avg8", "target_share_avg3", "target_share_avg8", "team_targets_avg3", "team_targets_avg8", "opp_targets_avg8"] + common_context,
        ["raw_ypt", "ypt8", "rec_epa_per_target8", "air_yards_per_target8", "yac_per_reception8", "opp_ypt8"] + common_context,
        "raw_targets", 1.0, (0.0, 15.0), (2.5, 12.0), "receiving_yards"
    )
    results["wr_receiving_yards"] = run_market(
        data, "WR", "receive", "targets", "actual_ypt", "raw_targets", "raw_ypt",
        ["raw_targets", "targets_avg3", "targets_avg8", "target_share_avg3", "target_share_avg8", "team_targets_avg3", "team_targets_avg8", "opp_targets_avg8"] + common_context,
        ["raw_ypt", "ypt8", "rec_epa_per_target8", "air_yards_per_target8", "yac_per_reception8", "opp_ypt8"] + common_context,
        "raw_targets", 2.0, (0.0, 20.0), (3.0, 16.0), "receiving_yards"
    )

    (output / "nfl_rb_wr_prop_regression_results.json").write_text(json.dumps(results, indent=2, sort_keys=True))
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
