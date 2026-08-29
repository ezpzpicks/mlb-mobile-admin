"""Targeted nonlinear refinement for CFB margin compression.

Design:
- Uses the exact SportsDataverse/cfbfastR game reconstruction validated in v2.
- Keeps the seven v2 linear predictors fixed.
- Learns nonlinear power-gap thresholds from 2021-2023 discovery only.
- Chooses a nonlinear form using 2024 confirmation only.
- Opens the untouched 2025 holdout only after the form is chosen.
- Sportsbook spread/total are evaluation-only and never model predictors.

The nonlinear terms are odd-symmetric in the team power gap. That means a
large strength gap can lift the stronger team's expected points while reducing
the weaker team's expected points by a similar amount, widening margin without
mechanically inflating the game total.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import math

import numpy as np
import pandas as pd
import statsmodels.api as sm

from research import cfb_team_score_regression_v2 as v2
from research import cfb_team_score_regression_runner as exact

RESULT_DIR = Path("research/results")
RESULT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON = RESULT_DIR / "cfb_team_score_regression_results_v3_nonlinear.json"
RESULT_MD = RESULT_DIR / "cfb_team_score_regression_summary_v3_nonlinear.md"

BASE_FEATURES = [
    "prior_own_ppg",
    "prior_opp_papg",
    "prior_power_gap",
    "current_own_scoring_delta",
    "current_opp_allowed_delta",
    "current_power_delta",
    "home_indicator",
]


def _signed_tail(values: pd.Series, threshold: float) -> pd.Series:
    arr = pd.to_numeric(values, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    return pd.Series(np.sign(arr) * np.maximum(np.abs(arr) - threshold, 0.0), index=values.index)


def _add_nonlinear_terms(frame: pd.DataFrame, thresholds: dict[str, float]) -> pd.DataFrame:
    out = frame.copy()
    gap = pd.to_numeric(out["prior_power_gap"], errors="coerce").fillna(0.0)
    # Scale the signed square only for numerical conditioning; scaling does not
    # change fitted predictions or statistical significance.
    out["prior_power_gap_signed_square"] = gap * gap.abs() / 10.0
    for label, threshold in thresholds.items():
        out[f"prior_power_gap_tail_{label}"] = _signed_tail(gap, threshold)
    return out


def _predict_games(frame: pd.DataFrame, fitted: Any, features: list[str]) -> pd.DataFrame:
    predicted_team = v2.predict_team(frame, fitted, features, "prediction")
    return v2.to_games(predicted_team, "prediction")


def _fit_score(train: pd.DataFrame, test: pd.DataFrame, features: list[str]) -> tuple[Any, pd.DataFrame, dict[str, Any]]:
    fitted = v2.fit(train, features)
    games = _predict_games(test, fitted, features)
    return fitted, games, v2.score(games)


def _same_sign(a: float, b: float) -> bool:
    if abs(a) < 1e-12 or abs(b) < 1e-12:
        return True
    return bool(np.sign(a) == np.sign(b))


def _variant_confirmation(
    discovery: pd.DataFrame,
    confirmation: pd.DataFrame,
    feature: str | None,
) -> dict[str, Any]:
    features = list(BASE_FEATURES) + ([feature] if feature else [])
    dm, _, discovery_score = _fit_score(discovery, discovery, features)
    cm = v2.fit(confirmation, features)
    _, confirmation_games, confirmation_score = _fit_score(discovery, confirmation, features)

    if feature is None:
        eligible = True
        dcoef = dp = ccoef = cp = None
        same_sign = True
    else:
        dcoef = float(dm.params[feature])
        dp = float(dm.pvalues[feature])
        ccoef = float(cm.params[feature])
        cp = float(cm.pvalues[feature])
        same_sign = _same_sign(dcoef, ccoef)
        # Match the v2 philosophy: discovery significance + 2024 sign stability.
        eligible = bool(dp <= 0.05 and same_sign)

    return {
        "feature": feature,
        "features": features,
        "eligible": eligible,
        "discovery_margin_mae": discovery_score["margin_mae"],
        "confirmation_margin_mae": confirmation_score["margin_mae"],
        "confirmation_margin_rmse": confirmation_score["margin_rmse"],
        "confirmation_total_mae": confirmation_score["total_mae"],
        "discovery_coefficient": dcoef,
        "discovery_p": dp,
        "confirmation_coefficient": ccoef,
        "confirmation_p": cp,
        "same_sign": same_sign,
        "confirmation_28_plus": v2.favorite_buckets(confirmation_games).get("28+", {}),
    }


def _case_values(prior: dict[str, v2.TeamStats], away: str, home: str, thresholds: dict[str, float], features: list[str]) -> pd.DataFrame:
    pa, ph = v2.stat(prior, away), v2.stat(prior, home)
    rows: list[dict[str, float]] = []
    for is_home in (False, True):
        own = ph if is_home else pa
        opp = pa if is_home else ph
        gap = own.power - opp.power
        values: dict[str, float] = {
            "prior_own_ppg": own.ppg,
            "prior_opp_papg": opp.papg,
            "prior_power_gap": gap,
            "current_own_scoring_delta": 0.0,
            "current_opp_allowed_delta": 0.0,
            "current_power_delta": 0.0,
            "home_indicator": 1.0 if is_home else 0.0,
            "prior_power_gap_signed_square": gap * abs(gap) / 10.0,
        }
        for label, threshold in thresholds.items():
            values[f"prior_power_gap_tail_{label}"] = math.copysign(max(abs(gap) - threshold, 0.0), gap) if gap else 0.0
        rows.append({key: float(values[key]) for key in features})
    return pd.DataFrame(rows)


def _usc_sjsu_case(fitted: Any, features: list[str], thresholds: dict[str, float], games_2025: pd.DataFrame) -> list[dict[str, Any]]:
    prior = v2.team_summary(games_2025)
    teams = list(prior)
    usc = [t for t in teams if v2.clean(t) in {"usc", "southern california", "southern california trojans"} or "southern california" in v2.clean(t)]
    sjsu = [t for t in teams if "san jose state" in v2.clean(t)]
    if not usc or not sjsu:
        return [{"status": "team names not resolved", "usc_matches": usc, "sjsu_matches": sjsu}]
    away, home = sjsu[0], usc[0]
    X = sm.add_constant(_case_values(prior, away, home, thresholds, features), has_constant="add")
    preds = np.asarray(fitted.predict(X), dtype=float)
    return [{
        "away_team": away,
        "home_team": home,
        "projected_away": float(preds[0]),
        "projected_home": float(preds[1]),
        "projected_home_margin": float(preds[1] - preds[0]),
        "projected_total": float(preds[1] + preds[0]),
        "note": "Prior-only sanity case using 2025 performance; no 2026 roster/portal/injury overlay.",
    }]


def _pct_improvement(old: float, new: float) -> float:
    return 100.0 * (old - new) / old if old else float("nan")


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


def main() -> None:
    print("Loading exact SportsDataverse/cfbfastR historical games for nonlinear refinement...")
    seasons: dict[int, pd.DataFrame] = {}
    for season in range(2020, 2026):
        seasons[season] = exact.games_from_pbp_exact(season)
        print(f"{season}: games={len(seasons[season])}")

    raw_parts: list[pd.DataFrame] = []
    for season in range(2021, 2026):
        raw_parts.append(v2.build_team_rows(season, seasons))
    raw = pd.concat(raw_parts, ignore_index=True)

    discovery_raw = raw[raw["season"].isin(v2.DISCOVERY)].copy()
    abs_gap = pd.to_numeric(discovery_raw["prior_power_gap"], errors="coerce").abs().dropna()
    thresholds = {
        "q70": float(abs_gap.quantile(0.70)),
        "q80": float(abs_gap.quantile(0.80)),
        "q90": float(abs_gap.quantile(0.90)),
    }
    print("Discovery power-gap thresholds:", thresholds)

    all_rows = _add_nonlinear_terms(raw, thresholds)
    discovery = all_rows[all_rows["season"].isin(v2.DISCOVERY)].copy()
    confirmation = all_rows[all_rows["season"] == v2.CONFIRM].copy()
    holdout = all_rows[all_rows["season"] == v2.HOLDOUT].copy()

    candidate_features = [
        None,
        "prior_power_gap_signed_square",
        "prior_power_gap_tail_q70",
        "prior_power_gap_tail_q80",
        "prior_power_gap_tail_q90",
    ]
    variant_results = [_variant_confirmation(discovery, confirmation, feature) for feature in candidate_features]

    eligible = [item for item in variant_results if item["eligible"]]
    if not eligible:
        raise RuntimeError("No eligible CFB nonlinear variant, including base")
    chosen = min(eligible, key=lambda item: item["confirmation_margin_mae"])
    chosen_feature = chosen["feature"]
    chosen_features = list(chosen["features"])
    print(f"Chosen on 2024 confirmation only: {chosen_feature or 'linear_base'}")

    final_train = all_rows[all_rows["season"] <= v2.CONFIRM].copy()
    base_model, base_games, base_score = _fit_score(final_train, holdout, BASE_FEATURES)
    chosen_model, chosen_games, chosen_score = _fit_score(final_train, holdout, chosen_features)

    base_buckets = v2.favorite_buckets(base_games)
    chosen_buckets = v2.favorite_buckets(chosen_games)
    base_bias = v2.favorite_side_bias(base_games)
    chosen_bias = v2.favorite_side_bias(chosen_games)

    base_case = _usc_sjsu_case(base_model, BASE_FEATURES, thresholds, seasons[2025])
    chosen_case = _usc_sjsu_case(chosen_model, chosen_features, thresholds, seasons[2025])

    results = {
        "research_version": "cfb-team-score-regression-v3-nonlinear-2026-08-21",
        "design": {
            "data": "SportsDataverse/cfbfastR reconstructed games",
            "discovery": "2021-2023",
            "confirmation": "2024 only; nonlinear form chosen before holdout",
            "holdout": "2025 untouched until form selection",
            "market_used_as_predictor": False,
            "base_features": BASE_FEATURES,
            "nonlinear_candidates": [f for f in candidate_features if f],
            "thresholds_learned_from": "absolute prior_power_gap quantiles in 2021-2023 discovery only",
            "selection_rule": "discovery p<=0.05 + same coefficient sign in 2024; choose lowest 2024 margin MAE; linear base always eligible",
        },
        "thresholds": thresholds,
        "variant_confirmation_results": variant_results,
        "chosen_feature": chosen_feature,
        "chosen_features": chosen_features,
        "final_coefficients": {k: float(v) for k, v in chosen_model.params.items()},
        "final_p_values": {k: float(v) for k, v in chosen_model.pvalues.items()},
        "holdout_base_linear": base_score,
        "holdout_chosen": chosen_score,
        "holdout_improvement_vs_v2_linear": {
            "margin_mae_pct": _pct_improvement(base_score["margin_mae"], chosen_score["margin_mae"]),
            "margin_rmse_pct": _pct_improvement(base_score["margin_rmse"], chosen_score["margin_rmse"]),
            "total_mae_pct": _pct_improvement(base_score["total_mae"], chosen_score["total_mae"]),
            "total_rmse_pct": _pct_improvement(base_score["total_rmse"], chosen_score["total_rmse"]),
        },
        "favorite_size_buckets_base_linear": base_buckets,
        "favorite_size_buckets_chosen": chosen_buckets,
        "favorite_side_bias_base_linear": base_bias,
        "favorite_side_bias_chosen": chosen_bias,
        "usc_san_jose_state_base_linear": base_case,
        "usc_san_jose_state_chosen": chosen_case,
    }
    RESULT_JSON.write_text(json.dumps(_jsonable(results), indent=2, sort_keys=True), encoding="utf-8")

    b21_base = base_buckets.get("21-27.5", {})
    b21_new = chosen_buckets.get("21-27.5", {})
    b28_base = base_buckets.get("28+", {})
    b28_new = chosen_buckets.get("28+", {})
    lines = [
        "# CFB team-score regression v3 nonlinear refinement",
        "",
        "Nonlinear form selected using 2021-23 discovery + 2024 confirmation only; 2025 remained untouched until selection.",
        "Archived sportsbook lines are evaluation-only and never predictors.",
        "",
        f"Chosen nonlinear feature: {chosen_feature or 'none (linear base retained)'}",
        f"Discovery thresholds: q70={thresholds['q70']:.3f}, q80={thresholds['q80']:.3f}, q90={thresholds['q90']:.3f}",
        "",
        "## 2025 holdout",
        f"- Linear v2 margin MAE: {base_score['margin_mae']:.3f}",
        f"- Chosen margin MAE: {chosen_score['margin_mae']:.3f}",
        f"- Margin MAE improvement vs v2 linear: {_pct_improvement(base_score['margin_mae'], chosen_score['margin_mae']):.2f}%",
        f"- Linear v2 margin RMSE: {base_score['margin_rmse']:.3f}",
        f"- Chosen margin RMSE: {chosen_score['margin_rmse']:.3f}",
        f"- Linear v2 total MAE: {base_score['total_mae']:.3f}",
        f"- Chosen total MAE: {chosen_score['total_mae']:.3f}",
    ]
    if b21_base.get("n") and b21_new.get("n"):
        lines += [
            "",
            "## 21-27.5 favorites",
            f"- Games: {b21_new['n']}",
            f"- Linear margin MAE: {b21_base['margin_mae']:.3f}",
            f"- Chosen margin MAE: {b21_new['margin_mae']:.3f}",
            f"- Linear mean abs margin: {b21_base['model_mean_abs_margin']:.3f}",
            f"- Chosen mean abs margin: {b21_new['model_mean_abs_margin']:.3f}",
            f"- Actual mean abs margin: {b21_new['actual_mean_abs_margin']:.3f}",
        ]
    if b28_base.get("n") and b28_new.get("n"):
        lines += [
            "",
            "## 28+ favorites",
            f"- Games: {b28_new['n']}",
            f"- Linear margin MAE: {b28_base['margin_mae']:.3f}",
            f"- Chosen margin MAE: {b28_new['margin_mae']:.3f}",
            f"- Linear mean abs margin: {b28_base['model_mean_abs_margin']:.3f}",
            f"- Chosen mean abs margin: {b28_new['model_mean_abs_margin']:.3f}",
            f"- Actual mean abs margin: {b28_new['actual_mean_abs_margin']:.3f}",
            f"- Market mean spread: {b28_new['market_mean_abs_spread']:.3f}",
            f"- Chosen ATS direction accuracy: {b28_new['ats_direction_accuracy']:.2%}",
        ]
    lines += [
        "",
        "## USC-San Jose State prior-only sanity case",
        "### Linear v2",
        "```json",
        json.dumps(_jsonable(base_case), indent=2),
        "```",
        "### Chosen refinement",
        "```json",
        json.dumps(_jsonable(chosen_case), indent=2),
        "```",
    ]
    RESULT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(RESULT_MD.read_text(encoding="utf-8"))
    print(f"Wrote {RESULT_JSON}")


if __name__ == "__main__":
    main()
