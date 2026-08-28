"""Leakage-safe CFB totals regression research.

Goal
----
Keep the validated CFB spread/margin regression fixed and test whether a
separate totals-specific residual regression can improve score-total accuracy.

Protocol
--------
* Discovery/training: 2021-2023
* Validation/model selection: 2024
* Untouched holdout: 2025
* Sportsbook spread/total are NEVER regression predictors.
* Market totals are used only after prediction to grade historical O/U picks.

The feature set is intentionally compact and uses only information available
before each game through the production CFB public-data stack plus the fixed
spread regression's leakage-safe team-score features.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from builders import cfb_builder as cb
from builders import cfb_game_regression as spread_reg

TRAIN_SEASONS = (2021, 2022, 2023)
VALIDATION_SEASON = 2024
HOLDOUT_SEASON = 2025
RESULTS_DIR = Path("research/results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

FEATURES = [
    "reg_total",
    "reg_abs_margin",
    "reg_abs_margin_sq",
    "prior_scoring_sum",
    "prior_allowed_sum",
    "current_scoring_delta_sum",
    "current_allowed_delta_sum",
    "current_power_delta_sum",
    "prior_power_gap_abs",
    "score_balance",
    "week",
    "week_sqrt",
    "neutral",
]


def num(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def completed_games(season: int) -> pd.DataFrame:
    frame = cb._parse_games(cb._espn_games_payload(int(season)), int(season))
    if frame.empty:
        return frame
    mask = frame["Completed"].map(cb._bool)
    mask &= pd.to_numeric(frame["Away Score"], errors="coerce").notna()
    mask &= pd.to_numeric(frame["Home Score"], errors="coerce").notna()
    # The production model is primarily an FBS-vs-FBS model. Restricting research
    # to the same population also avoids FCS prior-data sparsity distorting totals.
    mask &= frame["Away Classification"].astype(str).str.lower().eq("fbs")
    mask &= frame["Home Classification"].astype(str).str.lower().eq("fbs")
    mask &= pd.to_numeric(frame["Week"], errors="coerce").fillna(0).gt(0)
    return frame.loc[mask].copy().sort_values(["Week", "Game Date", "Game ID"])


def make_row(game: pd.Series) -> dict[str, Any]:
    away_base, home_base, af, hf = spread_reg._regression_base(cb, game)
    reg_total = away_base + home_base
    reg_margin = home_base - away_base
    week = max(1.0, num(game.get("Week"), 1.0))
    actual_away = num(game.get("Away Score"), np.nan)
    actual_home = num(game.get("Home Score"), np.nan)
    actual_total = actual_away + actual_home
    market_total = num(game.get("Total"), np.nan)

    row = {
        "season": int(num(game.get("Season"), 0)),
        "week": week,
        "game_id": str(game.get("Game ID", "")),
        "game": f"{game.get('Away Team')} @ {game.get('Home Team')}",
        "away_team": str(game.get("Away Team", "")),
        "home_team": str(game.get("Home Team", "")),
        "actual_total": actual_total,
        "actual_margin": actual_home - actual_away,
        "market_total": market_total,
        "reg_total": reg_total,
        "reg_margin": reg_margin,
        "reg_abs_margin": abs(reg_margin),
        "reg_abs_margin_sq": abs(reg_margin) ** 2,
        "prior_scoring_sum": num(af.get("prior_own_ppg"), 28.0) + num(hf.get("prior_own_ppg"), 28.0),
        "prior_allowed_sum": num(af.get("prior_opp_papg"), 28.0) + num(hf.get("prior_opp_papg"), 28.0),
        "current_scoring_delta_sum": num(af.get("current_own_scoring_delta")) + num(hf.get("current_own_scoring_delta")),
        "current_allowed_delta_sum": num(af.get("current_opp_allowed_delta")) + num(hf.get("current_opp_allowed_delta")),
        "current_power_delta_sum": num(af.get("current_power_delta")) + num(hf.get("current_power_delta")),
        "prior_power_gap_abs": abs(num(af.get("prior_power_gap"))),
        "score_balance": abs(home_base - away_base) / max(1.0, reg_total),
        "week_sqrt": math.sqrt(week),
        "neutral": 1.0 if cb._bool(game.get("Neutral Site", False)) else 0.0,
    }
    return row


def build_dataset() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for season in (*TRAIN_SEASONS, VALIDATION_SEASON, HOLDOUT_SEASON):
        games = completed_games(season)
        print(f"season {season}: {len(games)} completed FBS-v-FBS games")
        for i, (_, game) in enumerate(games.iterrows(), 1):
            try:
                rows.append(make_row(game))
            except Exception as exc:
                print(f"skip {season} {game.get('Game ID')}: {exc}")
            if i % 100 == 0:
                print(f"  built {i}/{len(games)}")
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("CFB total-regression dataset is empty")
    for col in FEATURES + ["actual_total", "market_total", "reg_margin"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["actual_total", "reg_total"])
    return frame.reset_index(drop=True)


def fit_ridge(train: pd.DataFrame, alpha: float) -> dict[str, Any]:
    x = train[FEATURES].astype(float).copy()
    means = x.mean(axis=0)
    stds = x.std(axis=0, ddof=0).replace(0.0, 1.0)
    z = ((x - means) / stds).to_numpy(dtype=float)
    # Model the residual around the fixed spread-regression team-score sum. This
    # preserves its useful scoring level while letting totals-specific structure
    # correct systematic misses.
    y = (train["actual_total"] - train["reg_total"]).to_numpy(dtype=float)
    design = np.column_stack([np.ones(len(z)), z])
    penalty = np.eye(design.shape[1]) * float(alpha)
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    return {
        "alpha": float(alpha),
        "intercept": float(beta[0]),
        "coef": {name: float(value) for name, value in zip(FEATURES, beta[1:])},
        "means": {name: float(means[name]) for name in FEATURES},
        "stds": {name: float(stds[name]) for name in FEATURES},
    }


def predict(frame: pd.DataFrame, model: dict[str, Any]) -> np.ndarray:
    zcols = []
    for name in FEATURES:
        mean = model["means"][name]
        std = model["stds"][name] or 1.0
        zcols.append((frame[name].astype(float).to_numpy() - mean) / std)
    z = np.column_stack(zcols)
    beta = np.array([model["coef"][name] for name in FEATURES], dtype=float)
    residual = float(model["intercept"]) + z @ beta
    return frame["reg_total"].to_numpy(dtype=float) + residual


def metrics(actual: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    err = pred - actual
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    bias = float(np.mean(err))
    denom = float(np.sum((actual - actual.mean()) ** 2))
    r2 = float(1.0 - np.sum(err ** 2) / denom) if denom > 0 else 0.0
    corr = float(np.corrcoef(actual, pred)[0, 1]) if len(actual) > 2 else 0.0
    return {"n": int(len(actual)), "mae": mae, "rmse": rmse, "bias": bias, "r2": r2, "corr": corr}


def eval_frame(frame: pd.DataFrame, model: dict[str, Any] | None = None) -> dict[str, Any]:
    actual = frame["actual_total"].to_numpy(dtype=float)
    baseline = frame["reg_total"].to_numpy(dtype=float)
    out: dict[str, Any] = {"spread_regression_team_score_sum": metrics(actual, baseline)}
    if model is not None:
        out["totals_residual_regression"] = metrics(actual, predict(frame, model))
    market_mask = frame["market_total"].notna().to_numpy()
    if market_mask.any():
        market = frame.loc[market_mask, "market_total"].to_numpy(dtype=float)
        out["market_total"] = metrics(actual[market_mask], market)
    return out


def betting_table(frame: pd.DataFrame, pred: np.ndarray) -> list[dict[str, Any]]:
    line = frame["market_total"].to_numpy(dtype=float)
    actual = frame["actual_total"].to_numpy(dtype=float)
    valid = np.isfinite(line)
    rows: list[dict[str, Any]] = []
    for threshold in range(1, 9):
        edge = pred - line
        selected = valid & (np.abs(edge) >= float(threshold))
        idx = np.where(selected)[0]
        wins = losses = pushes = overs = unders = 0
        for j in idx:
            pick_over = edge[j] > 0
            overs += int(pick_over)
            unders += int(not pick_over)
            if abs(actual[j] - line[j]) < 1e-9:
                pushes += 1
            elif (actual[j] > line[j]) == pick_over:
                wins += 1
            else:
                losses += 1
        decisions = wins + losses
        win_rate = wins / decisions if decisions else float("nan")
        roi_110 = (wins * (100 / 110) - losses) / decisions if decisions else float("nan")
        rows.append({
            "threshold": threshold,
            "bets": int(len(idx)),
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "win_rate": win_rate,
            "roi_at_-110": roi_110,
            "overs": overs,
            "unders": unders,
        })
    return rows


def side_record(frame: pd.DataFrame, pred: np.ndarray, threshold: float) -> dict[str, Any]:
    line = frame["market_total"].to_numpy(dtype=float)
    actual = frame["actual_total"].to_numpy(dtype=float)
    edge = pred - line
    valid = np.isfinite(line) & (np.abs(edge) >= threshold)
    out: dict[str, Any] = {}
    for label, pick_over in (("over", True), ("under", False)):
        idx = np.where(valid & ((edge > 0) == pick_over))[0]
        wins = losses = pushes = 0
        for j in idx:
            if abs(actual[j] - line[j]) < 1e-9:
                pushes += 1
            elif (actual[j] > line[j]) == pick_over:
                wins += 1
            else:
                losses += 1
        n = wins + losses
        out[label] = {
            "bets": int(len(idx)), "wins": wins, "losses": losses, "pushes": pushes,
            "win_rate": wins / n if n else None,
        }
    return out


def select_grade_thresholds(validation_table: list[dict[str, Any]]) -> dict[str, int | None]:
    # Thresholds are selected ONLY on 2024. 2025 is not examined here.
    b = next((r["threshold"] for r in validation_table if r["bets"] >= 40 and r["win_rate"] >= 0.54), None)
    a = next((r["threshold"] for r in validation_table if r["bets"] >= 25 and r["win_rate"] >= 0.56 and (b is None or r["threshold"] >= b)), None)
    return {"B": b, "A": a}


def top_residual_correlations(frame: pd.DataFrame) -> list[dict[str, Any]]:
    residual = frame["actual_total"] - frame["reg_total"]
    rows = []
    for feature in FEATURES:
        values = pd.to_numeric(frame[feature], errors="coerce")
        mask = residual.notna() & values.notna()
        corr = float(np.corrcoef(values[mask], residual[mask])[0, 1]) if mask.sum() > 3 else 0.0
        rows.append({"feature": feature, "corr_with_baseline_total_residual": corr})
    return sorted(rows, key=lambda x: abs(x["corr_with_baseline_total_residual"]), reverse=True)


def pct(x: Any) -> str:
    return "—" if x is None or not math.isfinite(float(x)) else f"{100*float(x):.1f}%"


def main() -> None:
    data = build_dataset()
    train = data[data["season"].isin(TRAIN_SEASONS)].copy()
    valid = data[data["season"] == VALIDATION_SEASON].copy()
    holdout = data[data["season"] == HOLDOUT_SEASON].copy()
    if min(len(train), len(valid), len(holdout)) == 0:
        raise RuntimeError(f"missing split: train={len(train)} valid={len(valid)} holdout={len(holdout)}")

    alpha_grid = [0.0, 0.25, 1.0, 4.0, 16.0, 64.0, 256.0]
    alpha_scores = []
    for alpha in alpha_grid:
        model = fit_ridge(train, alpha)
        pred = predict(valid, model)
        m = metrics(valid["actual_total"].to_numpy(float), pred)
        alpha_scores.append({"alpha": alpha, **m})
    best_alpha = min(alpha_scores, key=lambda r: (r["mae"], r["rmse"]))["alpha"]

    # Refit with 2024 included only after alpha selection. 2025 remains untouched.
    train_plus_valid = pd.concat([train, valid], ignore_index=True)
    final_model = fit_ridge(train_plus_valid, best_alpha)

    validation_model = fit_ridge(train, best_alpha)
    valid_pred = predict(valid, validation_model)
    holdout_pred = predict(holdout, final_model)

    validation_bets = betting_table(valid, valid_pred)
    holdout_bets = betting_table(holdout, holdout_pred)
    grades = select_grade_thresholds(validation_bets)
    grade_holdout = {}
    for grade, threshold in grades.items():
        if threshold is None:
            grade_holdout[grade] = None
        else:
            row = next(r for r in holdout_bets if r["threshold"] == threshold)
            grade_holdout[grade] = {**row, "sides": side_record(holdout, holdout_pred, float(threshold))}

    results = {
        "protocol": {
            "train_seasons": list(TRAIN_SEASONS),
            "validation_season": VALIDATION_SEASON,
            "holdout_season": HOLDOUT_SEASON,
            "sportsbook_predictors_used": False,
            "spread_model_status": "fixed production CFB team-score margin regression",
            "feature_count": len(FEATURES),
            "features": FEATURES,
        },
        "dataset": {
            "rows": int(len(data)),
            "train": int(len(train)),
            "validation": int(len(valid)),
            "holdout": int(len(holdout)),
            "holdout_with_market_total": int(holdout["market_total"].notna().sum()),
        },
        "alpha_selection_on_2024": alpha_scores,
        "best_alpha": best_alpha,
        "validation_metrics": eval_frame(valid, validation_model),
        "holdout_metrics": eval_frame(holdout, final_model),
        "validation_betting_by_edge": validation_bets,
        "holdout_betting_by_edge": holdout_bets,
        "grade_thresholds_selected_on_2024": grades,
        "grade_thresholds_2025_holdout": grade_holdout,
        "train_residual_correlations": top_residual_correlations(train),
        "holdout_residual_correlations": top_residual_correlations(holdout),
        "final_model": final_model,
    }

    out_json = RESULTS_DIR / "cfb_total_regression_results.json"
    out_json.write_text(json.dumps(results, indent=2, allow_nan=False))

    h = results["holdout_metrics"]
    base = h["spread_regression_team_score_sum"]
    reg = h["totals_residual_regression"]
    improvement = (base["mae"] - reg["mae"]) / base["mae"] if base["mae"] else 0.0
    lines = [
        "# CFB Totals Regression — 2025 Holdout",
        "",
        "The spread/margin regression was held fixed. Sportsbook lines were not regression predictors.",
        "",
        f"- Training: {', '.join(map(str, TRAIN_SEASONS))}",
        f"- Validation/model selection: {VALIDATION_SEASON}",
        f"- Untouched holdout: {HOLDOUT_SEASON}",
        f"- Holdout games: {len(holdout)}",
        f"- Holdout games with ESPN market total: {int(holdout['market_total'].notna().sum())}",
        f"- Selected ridge alpha: {best_alpha}",
        "",
        "## Holdout accuracy",
        "",
        f"- Fixed spread-regression team-score sum: MAE {base['mae']:.3f}, RMSE {base['rmse']:.3f}, corr {base['corr']:.3f}",
        f"- Totals-specific residual regression: MAE {reg['mae']:.3f}, RMSE {reg['rmse']:.3f}, corr {reg['corr']:.3f}",
        f"- MAE improvement: {100*improvement:.2f}%",
        "",
        "## 2025 O/U record by absolute model edge",
        "",
        "| Edge | Bets | W-L-P | Win rate | ROI @ -110 | Over/Under count |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for r in holdout_bets:
        lines.append(
            f"| {r['threshold']}+ | {r['bets']} | {r['wins']}-{r['losses']}-{r['pushes']} | {pct(r['win_rate'])} | {pct(r['roi_at_-110'])} | {r['overs']}/{r['unders']} |"
        )
    lines.extend(["", "## Grade thresholds chosen on 2024 only", ""])
    for grade in ("B", "A"):
        threshold = grades[grade]
        if threshold is None:
            lines.append(f"- {grade}: no threshold met the pre-set validation standard.")
        else:
            r = grade_holdout[grade]
            lines.append(f"- {grade}: {threshold}+ points. 2025 holdout {r['wins']}-{r['losses']}-{r['pushes']} ({pct(r['win_rate'])}).")
    lines.extend(["", "## Largest baseline-residual relationships in 2025", ""])
    for r in results["holdout_residual_correlations"][:6]:
        lines.append(f"- {r['feature']}: r={r['corr_with_baseline_total_residual']:.3f}")
    lines.extend(["", "## Standardized final coefficients", ""])
    lines.append(f"- Intercept residual correction: {final_model['intercept']:.4f}")
    for name, value in sorted(final_model["coef"].items(), key=lambda kv: abs(kv[1]), reverse=True):
        lines.append(f"- {name}: {value:+.4f}")

    out_md = RESULTS_DIR / "CFB_TOTAL_REGRESSION_RESULTS.md"
    out_md.write_text("\n".join(lines) + "\n")
    print(out_md.read_text())


if __name__ == "__main__":
    main()
