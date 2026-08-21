"""Team-score regression validation for the EZPZ NFL game model.

Builds the same leakage-safe pregame dataset as v2, then models each team's
adjusted points separately. The rolling production home-field estimate is an
offset, not a fitted feature. Current-game market prices are evaluation-only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm

from research import nfl_game_regression_v2 as base

DISCOVERY = base.DISCOVERY
CONFIRMATION = base.CONFIRMATION
HOLDOUT = base.HOLDOUT

FEATURES = [
    "scoring_matchup", "pass_matchup", "rush_matchup", "success_matchup",
    "explosive_matchup", "turnover_pressure", "sack_pressure", "red_zone_matchup",
    "pace_mean", "weather_adjustment", "rest_score_offset",
]


def _side_values(row: pd.Series, side: str) -> dict[str, float]:
    sign = 1.0 if side == "home" else -1.0
    values: dict[str, float] = {}
    for short, stem in [
        ("scoring_matchup", "scoring_matchup"),
        ("pass_matchup", "pass_matchup"),
        ("rush_matchup", "rush_matchup"),
        ("success_matchup", "success_matchup"),
        ("explosive_matchup", "explosive_matchup"),
        ("turnover_pressure", "turnover_pressure"),
        ("sack_pressure", "sack_pressure"),
        ("red_zone_matchup", "red_zone_matchup"),
    ]:
        total = float(row[f"{stem}_sum"])
        diff = float(row[f"{stem}_diff"])
        values[short] = 0.5 * (total + sign * diff)
    values["pace_mean"] = float(row["pace_mean"])
    values["weather_adjustment"] = float(row["weather_adjustment"])
    values["rest_score_offset"] = sign * float(row["rest_edge"]) / 2.0
    return values


def team_dataset(games: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for game_index, row in games.reset_index(drop=True).iterrows():
        margin = float(row["actual_margin"])
        total = float(row["actual_total"])
        hfa = float(row["hfa_points"])
        home_points = (total + margin) / 2.0
        away_points = (total - margin) / 2.0
        home = {
            "game_index": game_index, "season": int(row["season"]), "side": "home",
            "target_points_no_hfa": home_points - hfa / 2.0,
            **_side_values(row, "home"),
        }
        away = {
            "game_index": game_index, "season": int(row["season"]), "side": "away",
            "target_points_no_hfa": away_points + hfa / 2.0,
            **_side_values(row, "away"),
        }
        rows.extend([home, away])
    return pd.DataFrame(rows)


def fit(df: pd.DataFrame, features: list[str]):
    clean = df[["target_points_no_hfa"] + features].replace([np.inf, -np.inf], np.nan).dropna()
    x = sm.add_constant(clean[features].astype(float), has_constant="add")
    return sm.OLS(clean["target_points_no_hfa"].astype(float), x).fit(cov_type="HC3")


def select(discovery: pd.DataFrame, confirmation: pd.DataFrame) -> tuple[Any, list[str], list[dict[str, Any]], Any, Any]:
    selected = list(FEATURES)
    removed: list[dict[str, Any]] = []
    while len(selected) > 1:
        model = fit(discovery, selected)
        pvals = model.pvalues.drop(labels=["const"], errors="ignore")
        if pvals.empty or float(pvals.max()) <= 0.05:
            break
        worst = str(pvals.idxmax())
        removed.append({"feature": worst, "p_value": float(pvals[worst])})
        selected.remove(worst)
    dm = fit(discovery, selected)
    cm = fit(confirmation, selected)
    unstable = [f for f in selected if np.sign(dm.params.get(f, 0.0)) != np.sign(cm.params.get(f, 0.0))]
    if unstable and len(selected) - len(unstable) >= 1:
        for feature in unstable:
            removed.append({"feature": feature, "reason": "confirmation sign flip"})
        selected = [f for f in selected if f not in unstable]
        dm = fit(discovery, selected)
        cm = fit(confirmation, selected)
    final = fit(pd.concat([discovery, confirmation], ignore_index=True), selected)
    return final, selected, removed, dm, cm


def predict_games(games: pd.DataFrame, teams: pd.DataFrame, model: Any, selected: list[str]) -> tuple[np.ndarray, np.ndarray]:
    pred = model.predict(sm.add_constant(teams[selected].astype(float), has_constant="add")).to_numpy()
    teams = teams.copy(); teams["pred_no_hfa"] = pred
    margins: list[float] = []; totals: list[float] = []
    for game_index, row in games.reset_index(drop=True).iterrows():
        pair = teams[teams["game_index"] == game_index]
        home_base = float(pair.loc[pair["side"] == "home", "pred_no_hfa"].iloc[0])
        away_base = float(pair.loc[pair["side"] == "away", "pred_no_hfa"].iloc[0])
        hfa = float(row["hfa_points"])
        home = home_base + hfa / 2.0
        away = away_base - hfa / 2.0
        margins.append(home - away); totals.append(home + away)
    return np.asarray(margins), np.asarray(totals)


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--output-dir", default="artifacts/nfl_team_score_regression"); args = parser.parse_args()
    output = Path(args.output_dir); output.mkdir(parents=True, exist_ok=True)
    games = base.build_dataset()
    teams = team_dataset(games)
    discovery = teams[teams["season"].isin(DISCOVERY)].copy()
    confirmation = teams[teams["season"].isin(CONFIRMATION)].copy()
    holdout_games = games[games["season"].isin(HOLDOUT)].copy().reset_index(drop=True)
    holdout_teams = team_dataset(holdout_games)

    model, selected, removed, dm, cm = select(discovery, confirmation)
    margin_pred, total_pred = predict_games(holdout_games, holdout_teams, model, selected)
    margin_metrics = base.metrics(holdout_games["actual_margin"], margin_pred)
    total_metrics = base.metrics(holdout_games["actual_total"], total_pred)
    baseline_margin = base.metrics(holdout_games["actual_margin"], holdout_games["existing_margin"].to_numpy(float))
    baseline_total = base.metrics(holdout_games["actual_total"], holdout_games["existing_total"].to_numpy(float))
    margin_residual = holdout_games["actual_margin"].to_numpy(float) - margin_pred
    total_residual = holdout_games["actual_total"].to_numpy(float) - total_pred
    params = {k: float(v) for k, v in model.params.items()}; intercept = params.pop("const", 0.0)
    results = {
        "research_version": "nfl-team-score-regression-2026-08-20",
        "method": "Pregame team-score OLS; rolling HFA as offset; 2021-23 p<0.05 selection; 2024 sign confirmation; refit 2021-24; 2025 out-of-sample evaluation; no market predictors",
        "selected_features": selected,
        "removed": removed,
        "intercept": intercept,
        "coefficients": params,
        "discovery_pvalues": {k: float(v) for k, v in dm.pvalues.items()},
        "discovery_coefficients": {k: float(v) for k, v in dm.params.items()},
        "confirmation_coefficients": {k: float(v) for k, v in cm.params.items()},
        "sign_stability": {f: bool(np.sign(dm.params[f]) == np.sign(cm.params[f])) for f in selected},
        "holdout": {"margin": margin_metrics, "total": total_metrics},
        "existing_model_baseline": {"margin": baseline_margin, "total": baseline_total},
        "edge_accuracy": base.edge_accuracy(holdout_games, margin_pred, total_pred),
        "existing_edge_accuracy": base.edge_accuracy(holdout_games, holdout_games["existing_margin"].to_numpy(float), holdout_games["existing_total"].to_numpy(float)),
        "residual_distribution": {
            "margin_sd": float(np.std(margin_residual, ddof=1)),
            "total_sd": float(np.std(total_residual, ddof=1)),
            "correlation": float(np.corrcoef(margin_residual, total_residual)[0, 1]),
        },
    }
    (output / "nfl_team_score_regression_results.json").write_text(json.dumps(results, indent=2, sort_keys=True))
    generated = f'''"""Generated by research/nfl_team_score_regression.py. Do not hand edit."""\nMODEL_RESEARCH_VERSION = {results["research_version"]!r}\nTEAM_SCORE_INTERCEPT = {intercept!r}\nTEAM_SCORE_COEFFICIENTS = {params!r}\nMARGIN_RESIDUAL_SD = {results["residual_distribution"]["margin_sd"]!r}\nTOTAL_RESIDUAL_SD = {results["residual_distribution"]["total_sd"]!r}\nMARGIN_TOTAL_RESIDUAL_CORRELATION = {results["residual_distribution"]["correlation"]!r}\nHOLDOUT_EDGE_SUMMARY = {results["edge_accuracy"]!r}\n'''
    (output / "nfl_team_score_regression_generated.py").write_text(generated)
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__": main()
