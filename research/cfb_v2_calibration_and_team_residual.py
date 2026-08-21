"""Calibrate CFB v2 spread probabilities, grading gates, and team-specific residuals.

Selection discipline:
- Base CFB v2 features are fixed from the prior validated research.
- 2024 is the out-of-sample calibration/confirmation season.
- 2025 is a benchmark only and never selects thresholds or residual features.
- Sportsbook lines are used only to evaluate cover probabilities/grades, never as score predictors.
- Team-specific factors are season-level pregame roster/continuity values from the production CFB pipeline.
"""
from __future__ import annotations

from pathlib import Path
from statistics import NormalDist
from typing import Any
import json
import math

import numpy as np
import pandas as pd
import statsmodels.api as sm

from builders import cfb_builder as cfb
from research import cfb_team_score_regression_v2 as base

RESULT_DIR = Path("research/results")
RESULT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON = RESULT_DIR / "cfb_v2_calibration_team_residual.json"
RESULT_MD = RESULT_DIR / "cfb_v2_calibration_team_residual.md"

BASE_FEATURES = list(base.CANDIDATES)
FACTOR_SOURCE_COLUMNS = {
    "returning_production_gap": "Returning Production",
    "qb_continuity_gap": "QB Continuity",
    "returning_receiving_gap": "Returning Receiving",
    "returning_rushing_gap": "Returning Rushing",
    "talent_gap": "Talent Rating",
    "recruiting_gap": "Recruiting Rating",
    "portal_gap": "Portal Rating",
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _fit_team(rows: pd.DataFrame, seasons: set[int]):
    train = rows[rows["season"].isin(seasons)].copy()
    return base.fit(train, BASE_FEATURES)


def _predict_games(rows: pd.DataFrame, season: int, model: Any) -> pd.DataFrame:
    target = rows[rows["season"] == season].copy()
    pred_team = base.predict_team(target, model, BASE_FEATURES, "prediction")
    return base.to_games(pred_team, "prediction")


def _score_margin(frame: pd.DataFrame, column: str = "pred_margin") -> dict[str, float]:
    err = pd.to_numeric(frame[column], errors="coerce") - pd.to_numeric(frame["actual_margin"], errors="coerce")
    err = err.dropna()
    return {
        "n": int(len(err)),
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(np.square(err)))),
        "bias": float(np.mean(err)),
    }


def _normal_prob(edge: float, sigma: float) -> float:
    return float(NormalDist().cdf(abs(float(edge)) / max(1e-9, float(sigma))))


def _priced_games(frame: pd.DataFrame, sigma: float) -> pd.DataFrame:
    out = frame.copy()
    out["market_home_spread"] = pd.to_numeric(out["market_home_spread"], errors="coerce")
    out = out[np.isfinite(out["market_home_spread"])].copy()
    out["home_edge"] = pd.to_numeric(out["pred_margin"], errors="coerce") + out["market_home_spread"]
    out["model_side_home"] = out["home_edge"] >= 0
    out["point_edge"] = out["home_edge"].abs()
    out["model_probability"] = [_normal_prob(v, sigma) for v in out["home_edge"]]
    ats = pd.to_numeric(out["actual_margin"], errors="coerce") + out["market_home_spread"]
    out["push"] = ats.abs() <= 1e-9
    out["won"] = np.where(out["model_side_home"], ats > 0, ats < 0)
    return out


def _probability_bins(frame: pd.DataFrame) -> list[dict[str, Any]]:
    bins = [(0.50, 0.525), (0.525, 0.55), (0.55, 0.575), (0.575, 0.60), (0.60, 0.65), (0.65, 1.01)]
    rows: list[dict[str, Any]] = []
    for low, high in bins:
        sub = frame[(frame["model_probability"] >= low) & (frame["model_probability"] < high) & ~frame["push"]]
        rows.append({
            "range": f"{low:.3f}-{high:.3f}",
            "n": int(len(sub)),
            "mean_model_probability": float(sub["model_probability"].mean()) if len(sub) else np.nan,
            "win_rate": float(sub["won"].mean()) if len(sub) else np.nan,
            "mean_point_edge": float(sub["point_edge"].mean()) if len(sub) else np.nan,
        })
    return rows


def _wilson_lower(wins: int, n: int, z: float = 1.2815515655446004) -> float:
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1.0 + z * z / n
    center = p + z * z / (2.0 * n)
    radius = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)
    return float((center - radius) / denom)


def _threshold_grid(frame: pd.DataFrame) -> list[dict[str, Any]]:
    nonpush = frame[~frame["push"]].copy()
    results = []
    for probability in [0.525, 0.54, 0.55, 0.56, 0.57, 0.58, 0.60]:
        for edge in [1.0, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0]:
            sub = nonpush[(nonpush["model_probability"] >= probability) & (nonpush["point_edge"] >= edge)]
            wins = int(sub["won"].sum()) if len(sub) else 0
            n = int(len(sub))
            results.append({
                "probability": probability,
                "point_edge": edge,
                "n": n,
                "wins": wins,
                "win_rate": float(wins / n) if n else np.nan,
                "wilson80_lower": _wilson_lower(wins, n),
            })
    return results


def _choose_thresholds(grid: list[dict[str, Any]]) -> dict[str, Any]:
    # Selection is entirely from 2024. Favor sample size and conservative lower bounds;
    # never select using 2025 benchmark performance.
    viable_b = [r for r in grid if r["n"] >= 50 and r["win_rate"] >= 0.535 and r["wilson80_lower"] >= 0.50]
    viable_a = [r for r in grid if r["n"] >= 30 and r["win_rate"] >= 0.56 and r["wilson80_lower"] >= 0.515]

    def choose(rows: list[dict[str, Any]], fallback: dict[str, float]) -> dict[str, Any]:
        if not rows:
            return {**fallback, "source": "conservative fallback; no 2024 candidate met stability gate"}
        # Prefer the largest sample among strong lower-bound candidates, then the
        # lower probability/edge gate to avoid a tiny overfit tail.
        rows = sorted(rows, key=lambda r: (-r["wilson80_lower"], -r["n"], r["probability"], r["point_edge"]))
        chosen = dict(rows[0])
        chosen["source"] = "selected on 2024 only"
        return chosen

    return {
        "B": choose(viable_b, {"probability": 0.55, "point_edge": 2.5}),
        "A": choose(viable_a, {"probability": 0.58, "point_edge": 4.0}),
    }


def _zscore_map(frame: pd.DataFrame, column: str) -> dict[str, float]:
    if frame.empty or column not in frame.columns:
        return {}
    values = pd.to_numeric(frame[column], errors="coerce")
    mean = float(values.mean()) if values.notna().any() else 0.0
    std = float(values.std(ddof=0)) if values.notna().any() else 0.0
    if not math.isfinite(std) or std < 1e-9:
        return {str(team): 0.0 for team in frame["Team"]}
    return {
        str(team): float((value - mean) / std) if math.isfinite(float(value)) else 0.0
        for team, value in zip(frame["Team"], values.fillna(mean))
    }


def _factor_frame_for_season(season: int, games: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    teams = sorted(set(games["away_team"].astype(str)) | set(games["home_team"].astype(str)))
    roster = cfb._roster_priors(season, teams)
    if roster is None or roster.empty:
        return pd.DataFrame(), {name: 0.0 for name in FACTOR_SOURCE_COLUMNS}
    maps = {name: _zscore_map(roster, source) for name, source in FACTOR_SOURCE_COLUMNS.items()}
    coverage = {
        name: float(pd.to_numeric(roster.get(source), errors="coerce").notna().mean()) if source in roster.columns else 0.0
        for name, source in FACTOR_SOURCE_COLUMNS.items()
    }
    rows = []
    for _, game in games.iterrows():
        home = str(game["home_team"]); away = str(game["away_team"])
        row = {"game_key": game["game_key"], "season": season}
        for name, mapping in maps.items():
            row[name] = float(mapping.get(home, 0.0) - mapping.get(away, 0.0))
        rows.append(row)
    return pd.DataFrame(rows), coverage


def _robust_fit(frame: pd.DataFrame, features: list[str]):
    X = sm.add_constant(frame[features].astype(float), has_constant="add")
    return sm.OLS(frame["margin_residual"].astype(float), X).fit(cov_type="HC3")


def _select_residual_features(discovery: pd.DataFrame, confirmation: pd.DataFrame, candidates: list[str]):
    kept = [f for f in candidates if discovery[f].astype(float).std(ddof=0) > 1e-9]
    history = []
    while kept:
        model = _robust_fit(discovery, kept)
        pvals = model.pvalues.drop("const", errors="ignore")
        worst = str(pvals.idxmax()); worst_p = float(pvals.max())
        history.append({"features": list(kept), "worst": worst, "worst_p": worst_p})
        if worst_p <= 0.05:
            break
        kept.remove(worst)
    if not kept:
        return [], None, None, history, []

    checks = []
    while kept:
        dm = _robust_fit(discovery, kept); cm = _robust_fit(confirmation, kept)
        checks = []
        bad = []
        for feature in kept:
            dcoef = float(dm.params[feature]); ccoef = float(cm.params[feature])
            same_sign = bool(np.sign(dcoef) == np.sign(ccoef))
            confirm_ok = float(cm.pvalues[feature]) <= 0.10
            checks.append({
                "feature": feature,
                "discovery_coefficient": dcoef,
                "discovery_p": float(dm.pvalues[feature]),
                "confirmation_coefficient": ccoef,
                "confirmation_p": float(cm.pvalues[feature]),
                "same_sign": same_sign,
                "confirmation_p_le_0_10": confirm_ok,
            })
            if not same_sign or not confirm_ok:
                bad.append(feature)
        if not bad:
            return kept, dm, cm, history, checks
        # Drop the least convincing discovery feature first.
        drop = max(bad, key=lambda f: float(dm.pvalues[f]))
        kept.remove(drop)
    return [], None, None, history, checks


def _apply_residual(frame: pd.DataFrame, model: Any, features: list[str], label: str) -> pd.DataFrame:
    out = frame.copy()
    if model is None or not features:
        out[label] = 0.0
        out["pred_margin_with_residual"] = out["pred_margin"]
        return out
    X = sm.add_constant(out[features].astype(float), has_constant="add")
    out[label] = np.asarray(model.predict(X), dtype=float)
    # Keep an overlay from a season-level roster model from overwhelming the
    # statistically validated base score model.
    out[label] = out[label].clip(-6.0, 6.0)
    out["pred_margin_with_residual"] = out["pred_margin"] + out[label]
    return out


def _merge_factors(predictions: dict[int, pd.DataFrame], season_games: dict[int, pd.DataFrame]):
    out = {}
    coverage = {}
    for season, frame in predictions.items():
        factors, cov = _factor_frame_for_season(season, season_games[season])
        coverage[str(season)] = cov
        merged = frame.merge(factors.drop(columns=["season"], errors="ignore"), on="game_key", how="left") if not factors.empty else frame.copy()
        for factor in FACTOR_SOURCE_COLUMNS:
            if factor not in merged.columns:
                merged[factor] = 0.0
            merged[factor] = pd.to_numeric(merged[factor], errors="coerce").fillna(0.0)
        out[season] = merged
    return out, coverage


def main() -> None:
    seasons = {season: base.games_from_pbp(season) for season in range(2020, 2026)}
    team_rows = pd.concat([base.build_team_rows(season, seasons) for season in range(2021, 2026)], ignore_index=True)

    # Out-of-fold discovery predictions avoid fitting the residual layer to the
    # same base-model errors that were used to estimate those errors.
    predictions: dict[int, pd.DataFrame] = {}
    discovery_seasons = {2021, 2022, 2023}
    for target in sorted(discovery_seasons):
        model = _fit_team(team_rows, discovery_seasons - {target})
        predictions[target] = _predict_games(team_rows, target, model)
    model_2024 = _fit_team(team_rows, discovery_seasons)
    predictions[2024] = _predict_games(team_rows, 2024, model_2024)
    model_2025 = _fit_team(team_rows, discovery_seasons | {2024})
    predictions[2025] = _predict_games(team_rows, 2025, model_2025)

    # Calibrate spread volatility strictly from 2024 out-of-sample residuals.
    residual_2024 = pd.to_numeric(predictions[2024]["actual_margin"], errors="coerce") - pd.to_numeric(predictions[2024]["pred_margin"], errors="coerce")
    margin_sigma = float(residual_2024.std(ddof=1))
    robust_sigma = float(1.4826 * np.median(np.abs(residual_2024 - np.median(residual_2024))))
    margin_sigma = max(10.0, margin_sigma)

    priced_2024 = _priced_games(predictions[2024], margin_sigma)
    priced_2025 = _priced_games(predictions[2025], margin_sigma)
    grid = _threshold_grid(priced_2024)
    thresholds = _choose_thresholds(grid)

    # Build team-specific season-level residual factors and validate on 2024.
    merged, factor_coverage = _merge_factors(predictions, seasons)
    discovery_games = pd.concat([merged[s] for s in sorted(discovery_seasons)], ignore_index=True)
    confirmation_games = merged[2024].copy()
    benchmark_games = merged[2025].copy()
    for frame in [discovery_games, confirmation_games, benchmark_games]:
        frame["margin_residual"] = pd.to_numeric(frame["actual_margin"], errors="coerce") - pd.to_numeric(frame["pred_margin"], errors="coerce")

    candidates = [name for name in FACTOR_SOURCE_COLUMNS if np.mean([factor_coverage[str(s)].get(name, 0.0) for s in discovery_seasons]) >= 0.60]
    selected, discovery_resid_model, confirmation_resid_model, selection_history, sign_checks = _select_residual_features(discovery_games, confirmation_games, candidates)

    confirmation_with_overlay = _apply_residual(confirmation_games, confirmation_resid_model, selected, "residual_adjustment")
    confirmation_base_score = _score_margin(confirmation_games, "pred_margin")
    confirmation_overlay_score = _score_margin(confirmation_with_overlay, "pred_margin_with_residual")
    validation_improvement = 100.0 * (confirmation_base_score["mae"] - confirmation_overlay_score["mae"]) / confirmation_base_score["mae"] if confirmation_base_score["mae"] else 0.0

    production_resid_model = None
    production_coefficients = {}
    benchmark_overlay_score = _score_margin(benchmark_games, "pred_margin")
    benchmark_with_overlay = benchmark_games.copy()
    if selected and validation_improvement > 0:
        production_training = pd.concat([discovery_games, confirmation_games], ignore_index=True)
        production_resid_model = _robust_fit(production_training, selected)
        production_coefficients = {name: float(production_resid_model.params[name]) for name in selected}
        production_coefficients["const"] = float(production_resid_model.params.get("const", 0.0))
        benchmark_with_overlay = _apply_residual(benchmark_games, production_resid_model, selected, "residual_adjustment")
        benchmark_overlay_score = _score_margin(benchmark_with_overlay, "pred_margin_with_residual")

    def gate_eval(priced: pd.DataFrame, gate: dict[str, Any]) -> dict[str, Any]:
        sub = priced[(priced["model_probability"] >= float(gate["probability"])) & (priced["point_edge"] >= float(gate["point_edge"])) & ~priced["push"]]
        return {
            "n": int(len(sub)),
            "win_rate": float(sub["won"].mean()) if len(sub) else np.nan,
            "mean_probability": float(sub["model_probability"].mean()) if len(sub) else np.nan,
            "mean_point_edge": float(sub["point_edge"].mean()) if len(sub) else np.nan,
        }

    results = {
        "research_version": "cfb-v2-calibration-team-residual-2026-08-21",
        "calibration": {
            "source": "2024 out-of-sample residuals from 2021-23 trained base CFB v2",
            "margin_residual_sd": margin_sigma,
            "margin_robust_sigma_mad": robust_sigma,
            "margin_residual_mean": float(residual_2024.mean()),
            "probability_model": "Normal CDF of absolute spread point edge / calibrated residual SD",
            "2024_probability_bins": _probability_bins(priced_2024),
            "2025_benchmark_probability_bins": _probability_bins(priced_2025),
        },
        "grading": {
            "selection_season": 2024,
            "thresholds": thresholds,
            "2024_selected_gate_results": {name: gate_eval(priced_2024, gate) for name, gate in thresholds.items()},
            "2025_benchmark_gate_results": {name: gate_eval(priced_2025, gate) for name, gate in thresholds.items()},
            "grid": grid,
        },
        "team_specific_residual": {
            "candidate_factor_coverage": factor_coverage,
            "candidates_after_coverage": candidates,
            "selected_features": selected,
            "selection_history": selection_history,
            "sign_confirmation": sign_checks,
            "production_coefficients": production_coefficients,
            "adjustment_cap_points": 6.0,
            "2024_base": confirmation_base_score,
            "2024_with_confirmed_overlay": confirmation_overlay_score,
            "2024_mae_improvement_pct": validation_improvement,
            "2025_base_benchmark": _score_margin(benchmark_games, "pred_margin"),
            "2025_with_overlay_benchmark": benchmark_overlay_score,
        },
        "base_benchmarks": {
            "2024": _score_margin(predictions[2024], "pred_margin"),
            "2025": _score_margin(predictions[2025], "pred_margin"),
        },
    }
    RESULT_JSON.write_text(json.dumps(_jsonable(results), indent=2, sort_keys=True))

    lines = [
        "# CFB v2 calibration + team-specific residual research",
        "",
        f"- 2024 calibrated margin residual SD: **{margin_sigma:.3f}** points (robust MAD sigma {robust_sigma:.3f}).",
        f"- B spread gate selected on 2024: **p >= {thresholds['B']['probability']:.3f}, edge >= {thresholds['B']['point_edge']:.1f}**.",
        f"- A spread gate selected on 2024: **p >= {thresholds['A']['probability']:.3f}, edge >= {thresholds['A']['point_edge']:.1f}**.",
        f"- Residual features surviving discovery + 2024 confirmation: **{', '.join(selected) if selected else 'none'}**.",
        f"- 2024 residual-overlay MAE improvement: **{validation_improvement:.2f}%**.",
    ]
    if selected and production_resid_model is not None:
        b0 = _score_margin(benchmark_games, "pred_margin")["mae"]
        b1 = benchmark_overlay_score["mae"]
        lines.append(f"- 2025 benchmark residual-overlay MAE: **{b0:.3f} -> {b1:.3f}**.")
    RESULT_MD.write_text("\n".join(lines) + "\n")
    print(json.dumps(_jsonable(results), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
