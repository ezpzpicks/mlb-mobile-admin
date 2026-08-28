"""Evaluate the final CFB architecture: independent spread margin + totals regression.

The totals regression is selected without the spread model's score-sum output.
The fixed spread model may contribute only projected margin/game-script context.
Final projected scores are then derived algebraically:
    home = (total + home_margin) / 2
    away = (total - home_margin) / 2

Protocol remains leakage-safe:
* train 2021-2023
* select variant/ridge alpha on 2024
* untouched 2025 holdout
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research import cfb_totals_efficiency_regression as base

RESULTS_DIR = Path("research/results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON = RESULTS_DIR / "cfb_combined_spread_total_results.json"
RESULT_MD = RESULTS_DIR / "CFB_COMBINED_SPREAD_TOTAL_RESULTS.md"

# Wrap the existing leakage-safe feature builder only to preserve signed margin
# and actual team scores for the final score-combination evaluation.
_original_build_feature_row = base.build_feature_row


def _build_feature_row_with_scores(game, prior_stats, current_stats, prior_metrics, current_metrics):
    row = _original_build_feature_row(game, prior_stats, current_stats, prior_metrics, current_metrics)
    away = str(game["away_team"])
    home = str(game["home_team"])
    neutral = base.truthy(game.get("neutral", False))
    away_spread_score, home_spread_score = base.spread_scores(
        away, home, neutral, prior_stats, current_stats
    )
    row["spread_margin"] = float(home_spread_score - away_spread_score)
    row["actual_margin"] = float(base.num(game.get("actual_margin"), 0.0))
    row["actual_home"] = float(base.num(game.get("home_score"), 0.0))
    row["actual_away"] = float(base.num(game.get("away_score"), 0.0))
    row["spread_projected_home"] = float(home_spread_score)
    row["spread_projected_away"] = float(away_spread_score)
    return row


base.build_feature_row = _build_feature_row_with_scores

CLEAN_BASE_FEATURES = [f for f in base.BASE_FEATURES if f != "spread_score_sum"]
CLEAN_VARIANTS = {
    "independent_pace_ppd": CLEAN_BASE_FEATURES,
    "independent_pace_efficiency": CLEAN_BASE_FEATURES + base.EFFICIENCY_FEATURES,
    "independent_pace_efficiency_interactions": CLEAN_BASE_FEATURES + base.EFFICIENCY_FEATURES + base.INTERACTION_FEATURES,
}


def choose_model(train: pd.DataFrame, valid: pd.DataFrame, variants: dict[str, list[str]]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    selection: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    best_model: dict[str, Any] | None = None
    for variant, features in variants.items():
        for alpha in base.ALPHAS:
            model = base.fit_ridge(train, features, alpha)
            pred = base.predict(valid, model)
            score = base.metrics(valid["actual_total"].to_numpy(float), pred)
            row = {"variant": variant, "alpha": float(alpha), "feature_count": len(features), **score}
            selection.append(row)
            if best is None or (row["mae"], row["rmse"], row["feature_count"]) < (best["mae"], best["rmse"], best["feature_count"]):
                best = row
                best_model = model
    assert best is not None and best_model is not None
    return best, best_model, selection


def score_metrics(frame: pd.DataFrame, total_pred: np.ndarray, margin_pred: np.ndarray) -> dict[str, Any]:
    actual_home = frame["actual_home"].to_numpy(float)
    actual_away = frame["actual_away"].to_numpy(float)
    pred_home = (total_pred + margin_pred) / 2.0
    pred_away = (total_pred - margin_pred) / 2.0
    home_err = pred_home - actual_home
    away_err = pred_away - actual_away
    stacked_abs = np.concatenate([np.abs(home_err), np.abs(away_err)])
    stacked_sq = np.concatenate([home_err ** 2, away_err ** 2])
    both_within_3 = (np.abs(home_err) <= 3.0) & (np.abs(away_err) <= 3.0)
    both_within_7 = (np.abs(home_err) <= 7.0) & (np.abs(away_err) <= 7.0)
    return {
        "games": int(len(frame)),
        "team_score_mae": float(stacked_abs.mean()),
        "team_score_rmse": float(np.sqrt(stacked_sq.mean())),
        "home_score_mae": float(np.abs(home_err).mean()),
        "away_score_mae": float(np.abs(away_err).mean()),
        "home_bias": float(home_err.mean()),
        "away_bias": float(away_err.mean()),
        "both_teams_within_3_pct": float(100.0 * both_within_3.mean()),
        "both_teams_within_7_pct": float(100.0 * both_within_7.mean()),
    }


def scalar_metrics(actual: np.ndarray, pred: np.ndarray) -> dict[str, Any]:
    return base.metrics(actual.astype(float), pred.astype(float))


def nan_to_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: nan_to_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [nan_to_none(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main() -> None:
    data = base.build_dataset()
    for col in [
        "spread_margin", "actual_margin", "actual_home", "actual_away",
        "spread_projected_home", "spread_projected_away",
    ]:
        data[col] = pd.to_numeric(data[col], errors="coerce")
    data = data.dropna(subset=["spread_margin", "actual_margin", "actual_home", "actual_away"]).reset_index(drop=True)

    train = data[data["season"].isin(base.TRAIN_SEASONS)].copy()
    valid = data[data["season"] == base.VALIDATION_SEASON].copy()
    holdout = data[data["season"] == base.HOLDOUT_SEASON].copy()
    print(f"dataset rows={len(data)} train={len(train)} valid={len(valid)} holdout={len(holdout)}")

    clean_best, _, clean_selection = choose_model(train, valid, CLEAN_VARIANTS)
    legacy_best, _, legacy_selection = choose_model(train, valid, base.VARIANTS)

    train_plus_valid = pd.concat([train, valid], ignore_index=True)
    clean_features = CLEAN_VARIANTS[clean_best["variant"]]
    legacy_features = base.VARIANTS[legacy_best["variant"]]
    clean_final = base.fit_ridge(train_plus_valid, clean_features, clean_best["alpha"])
    legacy_final = base.fit_ridge(train_plus_valid, legacy_features, legacy_best["alpha"])

    clean_total = base.predict(holdout, clean_final)
    legacy_total = base.predict(holdout, legacy_final)
    spread_total = holdout["spread_score_sum"].to_numpy(float)
    spread_margin = holdout["spread_margin"].to_numpy(float)
    actual_total = holdout["actual_total"].to_numpy(float)
    actual_margin = holdout["actual_margin"].to_numpy(float)

    total_metrics = {
        "spread_score_sum": scalar_metrics(actual_total, spread_total),
        "legacy_totals_with_spread_score_sum": scalar_metrics(actual_total, legacy_total),
        "independent_totals_without_spread_score_sum": scalar_metrics(actual_total, clean_total),
    }
    margin_metrics = {
        "fixed_spread_margin": scalar_metrics(actual_margin, spread_margin),
    }
    team_score_metrics = {
        "spread_model_original_team_scores": score_metrics(holdout, spread_total, spread_margin),
        "legacy_total_plus_fixed_spread_margin": score_metrics(holdout, legacy_total, spread_margin),
        "independent_total_plus_fixed_spread_margin": score_metrics(holdout, clean_total, spread_margin),
    }

    clean_mae = total_metrics["independent_totals_without_spread_score_sum"]["mae"]
    legacy_mae = total_metrics["legacy_totals_with_spread_score_sum"]["mae"]
    spread_sum_mae = total_metrics["spread_score_sum"]["mae"]
    clean_team_mae = team_score_metrics["independent_total_plus_fixed_spread_margin"]["team_score_mae"]
    spread_team_mae = team_score_metrics["spread_model_original_team_scores"]["team_score_mae"]

    # Decompose errors to see whether margin and total are contributing largely
    # independent information on the holdout.
    total_error = clean_total - actual_total
    margin_error = spread_margin - actual_margin
    error_corr = float(np.corrcoef(total_error, margin_error)[0, 1]) if len(holdout) > 2 else 0.0

    results = {
        "protocol": {
            "train": list(base.TRAIN_SEASONS),
            "validation": base.VALIDATION_SEASON,
            "holdout": base.HOLDOUT_SEASON,
            "architecture": "fixed spread margin + independent totals regression -> algebraic team scores",
            "score_formula": {
                "home": "(projected_total + projected_home_margin) / 2",
                "away": "(projected_total - projected_home_margin) / 2",
            },
            "sportsbook_predictors_used": False,
            "spread_score_sum_allowed_in_independent_total_model": False,
            "spread_margin_allowed_as_game_script_feature": True,
        },
        "dataset": {"rows": len(data), "train": len(train), "validation": len(valid), "holdout": len(holdout)},
        "clean_selection_2024": clean_selection,
        "legacy_selection_2024": legacy_selection,
        "clean_chosen": clean_best,
        "legacy_chosen": legacy_best,
        "holdout_total_metrics": total_metrics,
        "holdout_margin_metrics": margin_metrics,
        "holdout_team_score_metrics": team_score_metrics,
        "holdout_error_correlation_total_vs_margin": error_corr,
        "independent_total_mae_change_vs_legacy_pct": 100.0 * (legacy_mae - clean_mae) / legacy_mae,
        "independent_total_mae_improvement_vs_spread_sum_pct": 100.0 * (spread_sum_mae - clean_mae) / spread_sum_mae,
        "combined_team_score_mae_improvement_vs_spread_team_scores_pct": 100.0 * (spread_team_mae - clean_team_mae) / spread_team_mae,
        "clean_final_model": clean_final,
    }
    RESULT_JSON.write_text(json.dumps(nan_to_none(results), indent=2, allow_nan=False))

    cm = total_metrics["independent_totals_without_spread_score_sum"]
    lm = total_metrics["legacy_totals_with_spread_score_sum"]
    sm = total_metrics["spread_score_sum"]
    mm = margin_metrics["fixed_spread_margin"]
    cteam = team_score_metrics["independent_total_plus_fixed_spread_margin"]
    steam = team_score_metrics["spread_model_original_team_scores"]
    lteam = team_score_metrics["legacy_total_plus_fixed_spread_margin"]

    lines = [
        "# CFB combined spread + independent totals evaluation",
        "",
        "The spread model owns projected margin. The totals model owns projected total and does not receive the spread model's team-score sum. Projected margin is retained only as game-script context.",
        "",
        f"- Training: {', '.join(map(str, base.TRAIN_SEASONS))}",
        f"- Validation/model selection: {base.VALIDATION_SEASON}",
        f"- Untouched holdout: {base.HOLDOUT_SEASON}",
        f"- Holdout games: {len(holdout)}",
        f"- Clean chosen variant: {clean_best['variant']} (alpha={clean_best['alpha']})",
        f"- Legacy chosen variant: {legacy_best['variant']} (alpha={legacy_best['alpha']})",
        "",
        "## 2025 total accuracy",
        "",
        f"- Spread-model team-score sum: MAE {sm['mae']:.3f}, RMSE {sm['rmse']:.3f}, corr {sm['corr']:.3f}",
        f"- Legacy totals regression (includes spread score sum): MAE {lm['mae']:.3f}, RMSE {lm['rmse']:.3f}, corr {lm['corr']:.3f}",
        f"- Independent totals regression: MAE {cm['mae']:.3f}, RMSE {cm['rmse']:.3f}, corr {cm['corr']:.3f}",
        f"- Independent vs legacy MAE change: {100.0*(legacy_mae-clean_mae)/legacy_mae:+.2f}% (positive means independent is better)",
        f"- Independent vs spread-score-sum MAE improvement: {100.0*(spread_sum_mae-clean_mae)/spread_sum_mae:.2f}%",
        "",
        "## Fixed spread model margin accuracy",
        "",
        f"- Margin MAE: {mm['mae']:.3f}",
        f"- Margin RMSE: {mm['rmse']:.3f}",
        f"- Margin correlation: {mm['corr']:.3f}",
        "",
        "## Final algebraic team-score accuracy",
        "",
        "Formula: Home = (Total + Margin)/2; Away = (Total - Margin)/2.",
        "",
        f"- Original spread-model team scores: team-score MAE {steam['team_score_mae']:.3f}, RMSE {steam['team_score_rmse']:.3f}",
        f"- Legacy total + fixed margin: team-score MAE {lteam['team_score_mae']:.3f}, RMSE {lteam['team_score_rmse']:.3f}",
        f"- Independent total + fixed margin: team-score MAE {cteam['team_score_mae']:.3f}, RMSE {cteam['team_score_rmse']:.3f}",
        f"- Independent combined improvement vs original spread team scores: {100.0*(spread_team_mae-clean_team_mae)/spread_team_mae:.2f}%",
        f"- Independent combined home-score MAE: {cteam['home_score_mae']:.3f}",
        f"- Independent combined away-score MAE: {cteam['away_score_mae']:.3f}",
        f"- Both projected team scores within 3 points: {cteam['both_teams_within_3_pct']:.1f}% of games",
        f"- Both projected team scores within 7 points: {cteam['both_teams_within_7_pct']:.1f}% of games",
        "",
        "## Error independence",
        "",
        f"- Correlation between independent total error and fixed spread-margin error: {error_corr:.3f}",
        "",
        "A low correlation supports treating margin and scoring environment as separate prediction problems.",
        "",
        "## Independent totals standardized coefficients",
        "",
        f"- Residual intercept: {clean_final['intercept']:+.4f}",
    ]
    for name, value in sorted(clean_final["coef"].items(), key=lambda kv: abs(kv[1]), reverse=True):
        lines.append(f"- {name}: {value:+.4f}")
    RESULT_MD.write_text("\n".join(lines) + "\n")
    print(RESULT_MD.read_text())


if __name__ == "__main__":
    main()
