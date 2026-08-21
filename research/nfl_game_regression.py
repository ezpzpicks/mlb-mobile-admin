"""Regression research for EZPZ NFL game projections.

Uses the production NFL builder's own nflverse loading, team-metric definitions,
and progressive prior/current-season blending. Current-game betting prices are
never model predictors; they are used only for untouched 2025 holdout evaluation.
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
SEASONS = DISCOVERY + CONFIRMATION + HOLDOUT

MARGIN_FEATURES = [
    "scoring_matchup_diff", "point_diff_strength_diff", "pass_matchup_diff",
    "rush_matchup_diff", "success_matchup_diff", "explosive_matchup_diff",
    "turnover_pressure_diff", "sack_pressure_diff", "red_zone_matchup_diff",
    "rest_edge", "home_field",
]
TOTAL_FEATURES = [
    "scoring_matchup_sum", "pass_matchup_sum", "rush_matchup_sum",
    "success_matchup_sum", "explosive_matchup_sum", "turnover_pressure_sum",
    "sack_pressure_sum", "red_zone_matchup_sum", "pace_mean",
    "weather_adjustment",
]


def num(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def schedule_for(season: int) -> pd.DataFrame:
    schedule = nflb._schedule_for_season(int(season), refresh=True)
    if schedule is None or schedule.empty:
        raise RuntimeError(f"No schedule for {season}")
    out = schedule.copy()
    if "Game Type" in out.columns:
        out = out[out["Game Type"].astype(str).str.upper() == "REG"].copy()
    out["Week"] = pd.to_numeric(out["Week"], errors="coerce")
    out["Away Score"] = pd.to_numeric(out["Away Score"], errors="coerce")
    out["Home Score"] = pd.to_numeric(out["Home Score"], errors="coerce")
    return out


def matchup(off: dict[str, Any], opp: dict[str, Any]) -> dict[str, float]:
    return {
        "scoring": 0.50 * num(off.get("Points/Game"), 22.5) + 0.50 * num(opp.get("Points Allowed/Game"), 22.5),
        "point_diff_strength": num(off.get("Points/Game"), 22.5) - num(off.get("Points Allowed/Game"), 22.5),
        "pass": num(off.get("Pass EPA/DB")) - num(opp.get("Pass Def EPA Edge")),
        "rush": num(off.get("Rush EPA/Play")) - num(opp.get("Rush Def EPA Edge")),
        "success": num(off.get("Off Success Rate"), 0.43) - num(opp.get("Def Success Edge")),
        "explosive": num(off.get("Explosive Rate"), 0.105) - num(opp.get("Explosive Def Edge")),
        "turnover_pressure": num(off.get("Turnover Rate"), 0.024) + num(opp.get("Takeaway Rate"), 0.024),
        "sack_pressure": num(off.get("Sack Rate Allowed"), 0.067) + num(opp.get("Sack/Pressure Edge")),
        "red_zone": num(off.get("Red Zone TD Rate"), 0.56) - num(opp.get("Red Zone Def Edge")),
    }


def weather_adjustment(row: pd.Series) -> float:
    return float(nflb._weather_adjustment(
        str(row.get("Roof", "") or ""),
        num(row.get("Temperature"), 70.0),
        num(row.get("Wind"), 6.0),
        "",
    ))


def precompute_metrics() -> tuple[dict[int, pd.DataFrame], dict[int, pd.DataFrame], dict[tuple[int, int], pd.DataFrame]]:
    schedules: dict[int, pd.DataFrame] = {}
    pbp: dict[int, pd.DataFrame] = {}
    full: dict[int, pd.DataFrame] = {}
    weekly: dict[tuple[int, int], pd.DataFrame] = {}
    for season in range(2020, 2026):
        print(f"Loading {season} nflverse data...")
        schedules[season] = schedule_for(season)
        pbp[season] = nflb._load_pbp_season(season)
        if pbp[season] is None or pbp[season].empty:
            raise RuntimeError(f"No PBP for {season}: {nflb.st.session_state.get(f'nfl_pbp_error_{season}', 'unknown error')}")
        full[season] = nflb._season_team_metrics(pbp[season], schedules[season], through_week=None)
    for season in SEASONS:
        weeks = sorted(int(v) for v in schedules[season]["Week"].dropna().unique())
        for week in weeks:
            current = (
                nflb._season_team_metrics(pbp[season], schedules[season], through_week=week - 1)
                if week > 1 else pd.DataFrame()
            )
            weekly[(season, week)] = nflb._blend_team_metrics(full[season - 1], current, season, week)
    return schedules, full, weekly


def build_dataset() -> pd.DataFrame:
    schedules, _, weekly = precompute_metrics()
    rows: list[dict[str, Any]] = []
    for season in SEASONS:
        games = schedules[season].dropna(subset=["Away Score", "Home Score"]).copy()
        for _, game in games.iterrows():
            week = int(game["Week"])
            ratings = weekly.get((season, week))
            if ratings is None or ratings.empty:
                continue
            away_team = nflb._normalize_team(game.get("Away Team", ""))
            home_team = nflb._normalize_team(game.get("Home Team", ""))
            away = nflb._team_row(ratings, away_team, season, week)
            home = nflb._team_row(ratings, home_team, season, week)
            am = matchup(away, home)
            hm = matchup(home, away)
            location = str(game.get("Location", "") or "").strip().lower()
            home_field = 0.0 if location == "neutral" else 1.0
            away_rest = num(game.get("Away Rest"), 7.0)
            home_rest = num(game.get("Home Rest"), 7.0)
            row = {
                "season": season, "week": week, "game_id": str(game.get("Game ID", "")),
                "away_team": away_team, "home_team": home_team,
                "actual_margin": num(game["Home Score"]) - num(game["Away Score"]),
                "actual_total": num(game["Home Score"]) + num(game["Away Score"]),
                "market_margin": num(game.get("Spread Line"), np.nan),
                "market_total": num(game.get("Total Line"), np.nan),
                "scoring_matchup_diff": hm["scoring"] - am["scoring"],
                "point_diff_strength_diff": hm["point_diff_strength"] - am["point_diff_strength"],
                "pass_matchup_diff": hm["pass"] - am["pass"],
                "rush_matchup_diff": hm["rush"] - am["rush"],
                "success_matchup_diff": hm["success"] - am["success"],
                "explosive_matchup_diff": hm["explosive"] - am["explosive"],
                "turnover_pressure_diff": hm["turnover_pressure"] - am["turnover_pressure"],
                "sack_pressure_diff": hm["sack_pressure"] - am["sack_pressure"],
                "red_zone_matchup_diff": hm["red_zone"] - am["red_zone"],
                "rest_edge": float(nflb.clamp((home_rest - away_rest) * 0.18, -1.5, 1.5)), "home_field": home_field,
                "scoring_matchup_sum": hm["scoring"] + am["scoring"],
                "pass_matchup_sum": hm["pass"] + am["pass"],
                "rush_matchup_sum": hm["rush"] + am["rush"],
                "success_matchup_sum": hm["success"] + am["success"],
                "explosive_matchup_sum": hm["explosive"] + am["explosive"],
                "turnover_pressure_sum": hm["turnover_pressure"] + am["turnover_pressure"],
                "sack_pressure_sum": hm["sack_pressure"] + am["sack_pressure"],
                "red_zone_matchup_sum": hm["red_zone"] + am["red_zone"],
                "pace_mean": 0.5 * (num(home.get("Pace"), 64) + num(away.get("Pace"), 64)),
                "weather_adjustment": weather_adjustment(game),
            }
            rows.append(row)
    return pd.DataFrame(rows)


def fit(df: pd.DataFrame, target: str, features: list[str]):
    clean = df[[target] + features].replace([np.inf, -np.inf], np.nan).dropna()
    x = sm.add_constant(clean[features].astype(float), has_constant="add")
    return sm.OLS(clean[target].astype(float), x).fit(cov_type="HC3")


def backward_select(df: pd.DataFrame, target: str, candidates: list[str], forced: set[str] | None = None):
    forced = forced or set()
    features = list(candidates)
    removed = []
    while len(features) > 1:
        model = fit(df, target, features)
        pvals = model.pvalues.drop(labels=["const"], errors="ignore")
        eligible = pvals.drop(labels=[f for f in forced if f in pvals.index], errors="ignore")
        if eligible.empty or float(eligible.max()) <= 0.05:
            return model, features, removed
        worst = str(eligible.idxmax())
        removed.append({"feature": worst, "p_value": float(eligible[worst])})
        features.remove(worst)
    return fit(df, target, features), features, removed


def metrics(y: pd.Series, pred: np.ndarray) -> dict[str, float]:
    actual = y.to_numpy(float); predicted = np.asarray(pred, float); err = actual - predicted
    return {
        "n": int(len(actual)), "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err ** 2))), "bias": float(np.mean(predicted - actual)),
        "r2": float(1 - np.sum(err ** 2) / np.sum((actual - actual.mean()) ** 2)),
    }


def target_result(discovery: pd.DataFrame, confirmation: pd.DataFrame, holdout: pd.DataFrame, target: str, candidates: list[str], forced: set[str] | None = None):
    dm, selected, removed = backward_select(discovery, target, candidates, forced)
    cm = fit(confirmation, target, selected)
    fm = fit(pd.concat([discovery, confirmation], ignore_index=True), target, selected)
    pred = fm.predict(sm.add_constant(holdout[selected].astype(float), has_constant="add")).to_numpy()
    params = {k: float(v) for k, v in fm.params.items()}
    return {
        "selected_features": selected,
        "removed": removed,
        "discovery_pvalues": {k: float(v) for k, v in dm.pvalues.items()},
        "discovery_coefficients": {k: float(v) for k, v in dm.params.items()},
        "confirmation_coefficients": {k: float(v) for k, v in cm.params.items()},
        "sign_stability": {f: bool(np.sign(dm.params[f]) == np.sign(cm.params[f])) for f in selected},
        "intercept": params.pop("const", 0.0), "coefficients": params,
        "holdout_metrics": metrics(holdout[target], pred), "pred": pred,
    }


def edge_accuracy(holdout: pd.DataFrame, margin_pred: np.ndarray, total_pred: np.ndarray) -> dict[str, Any]:
    out = holdout.copy().reset_index(drop=True); out["mp"] = margin_pred; out["tp"] = total_pred
    answer: dict[str, Any] = {}
    spread = out[pd.to_numeric(out["market_margin"], errors="coerce").notna()].copy()
    if not spread.empty:
        spread["edge"] = spread["mp"] - spread["market_margin"]
        for threshold in [1.5, 2.5, 3.5]:
            picks = spread[spread["edge"].abs() >= threshold]
            if len(picks):
                home_cover = picks["actual_margin"] > picks["market_margin"]
                answer[f"spread_{threshold}"] = {"n": int(len(picks)), "accuracy": float(np.mean(np.where(picks["edge"] > 0, home_cover, ~home_cover)))}
    totals = out[pd.to_numeric(out["market_total"], errors="coerce").notna()].copy()
    if not totals.empty:
        totals["edge"] = totals["tp"] - totals["market_total"]
        for threshold in [1.75, 3.0, 4.0]:
            picks = totals[totals["edge"].abs() >= threshold]
            if len(picks):
                over = picks["actual_total"] > picks["market_total"]
                answer[f"total_{threshold}"] = {"n": int(len(picks)), "accuracy": float(np.mean(np.where(picks["edge"] > 0, over, ~over)))}
    return answer


def strip_predictions(value: Any) -> Any:
    if isinstance(value, dict): return {k: strip_predictions(v) for k, v in value.items() if k != "pred"}
    if isinstance(value, list): return [strip_predictions(v) for v in value]
    if isinstance(value, np.generic): return value.item()
    return value


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--output-dir", default="artifacts/nfl_game_regression"); args = parser.parse_args()
    output = Path(args.output_dir); output.mkdir(parents=True, exist_ok=True)
    data = build_dataset(); data.to_csv(output / "pregame_regression_dataset.csv", index=False)
    discovery = data[data["season"].isin(DISCOVERY)].copy(); confirmation = data[data["season"].isin(CONFIRMATION)].copy(); holdout = data[data["season"].isin(HOLDOUT)].copy()
    margin = target_result(discovery, confirmation, holdout, "actual_margin", MARGIN_FEATURES, {"home_field"})
    total = target_result(discovery, confirmation, holdout, "actual_total", TOTAL_FEATURES)
    mr = holdout["actual_margin"].to_numpy(float) - margin["pred"]; tr = holdout["actual_total"].to_numpy(float) - total["pred"]
    results = {
        "research_version": "nfl-game-regression-2026-08-20",
        "method": "Production nflverse metrics; 2021-23 HC3 OLS backward selection p<0.05; 2024 confirmation; final refit 2021-24; untouched 2025 holdout; no market predictors",
        "rows": {"discovery": len(discovery), "confirmation": len(confirmation), "holdout": len(holdout)},
        "margin": margin, "total": total,
        "residual_distribution": {"margin_sd": float(np.std(mr, ddof=1)), "total_sd": float(np.std(tr, ddof=1)), "correlation": float(np.corrcoef(mr, tr)[0, 1])},
        "edge_accuracy": edge_accuracy(holdout, margin["pred"], total["pred"]),
    }
    clean = strip_predictions(results); (output / "nfl_game_regression_results.json").write_text(json.dumps(clean, indent=2, sort_keys=True))
    generated = f'''"""Generated by research/nfl_game_regression.py. Do not hand edit."""\nMODEL_RESEARCH_VERSION = {clean["research_version"]!r}\nMARGIN_INTERCEPT = {clean["margin"]["intercept"]!r}\nMARGIN_COEFFICIENTS = {clean["margin"]["coefficients"]!r}\nTOTAL_INTERCEPT = {clean["total"]["intercept"]!r}\nTOTAL_COEFFICIENTS = {clean["total"]["coefficients"]!r}\nMARGIN_RESIDUAL_SD = {clean["residual_distribution"]["margin_sd"]!r}\nTOTAL_RESIDUAL_SD = {clean["residual_distribution"]["total_sd"]!r}\nMARGIN_TOTAL_RESIDUAL_CORRELATION = {clean["residual_distribution"]["correlation"]!r}\nHOLDOUT_SUMMARY = {clean["edge_accuracy"]!r}\n'''
    (output / "nfl_game_regression_generated.py").write_text(generated)
    print(json.dumps(clean, indent=2, sort_keys=True))


if __name__ == "__main__": main()
