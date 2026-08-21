"""CFB team-score regression v2 using SportsDataverse/cfbfastR historical PBP.

The first research pass exposed a real infrastructure limitation: ESPN's public
historical scoreboard returned no completed seasons in bare GitHub Actions.
This version instead reconstructs historical games from the same open
SportsDataverse/cfbfastR parquet family already used by the production CFB
builder.

Design
------
* Discovery: 2021-2023
* Confirmation: 2024 coefficient-sign stability
* Untouched holdout: 2025
* Sportsbook spread/total NEVER enter the predictors; archived lines are used
  only for diagnostic favorite-size buckets and ATS-direction checks.
* Every current-season feature is built using games from prior weeks only.
* Previous-season strength preserves large-margin information by neutralizing
  home field and winsorizing margins at +/-45 rather than v1.5's nonlinear
  compression/cap around 24 points.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import math
import os
import re
import unicodedata

import numpy as np
import pandas as pd
import statsmodels.api as sm

os.environ.setdefault("EZPZ_CFB_ALLOW_BLOCKING_OPEN_DATA", "1")
os.environ.setdefault("EZPZ_CFB_CACHE_SECONDS", "86400")

from builders import cfb_builder as cfb  # noqa: E402

RESULT_DIR = Path("research/results")
RESULT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON = RESULT_DIR / "cfb_team_score_regression_results_v2.json"
RESULT_MD = RESULT_DIR / "cfb_team_score_regression_summary_v2.md"

DISCOVERY = {2021, 2022, 2023}
CONFIRM = 2024
HOLDOUT = 2025
HFA = 2.0

# Compact, interpretable, pregame-only candidate set. We intentionally do not
# start with dozens of correlated PBP metrics because the first production
# problem is score/margin compression, not lack of feature count.
CANDIDATES = [
    "prior_own_ppg",
    "prior_opp_papg",
    "prior_power_gap",
    "current_own_scoring_delta",
    "current_opp_allowed_delta",
    "current_power_delta",
    "home_indicator",
]


def num(value: Any, default: float = np.nan) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "completed"}


def clean(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("&", "and")
    text = re.sub(r"[^A-Za-z0-9]+", " ", text).strip().lower()
    return " ".join(text.split())


def first_present(frame: pd.DataFrame, names: list[str], default: Any = np.nan) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return frame[name]
    return pd.Series(default, index=frame.index)


def load_pbp(season: int) -> pd.DataFrame:
    path = cfb._download_open_asset("cfbfastR_cfb_pbp", season, ("play_by_play", "pbp"))
    if path is None or not Path(path).exists():
        raise RuntimeError(f"SportsDataverse PBP unavailable for {season}")

    # Read only the small set needed to reconstruct games. Alias coverage is
    # intentionally broad because SportsDataverse schemas changed over time.
    aliases = {
        "season": ("season", "year"),
        "week": ("week",),
        "game_id": ("game_id", "id_game", "gameId"),
        "sequence": ("sequenceNumber", "sequence_number", "play_id", "id_play", "id"),
        "home_team": ("homeTeamName", "home_team", "homeTeam", "home"),
        "away_team": ("awayTeamName", "away_team", "awayTeam", "away"),
        "home_score": ("homeScore", "home_score", "home_points", "homePoints"),
        "away_score": ("awayScore", "away_score", "away_points", "awayPoints"),
        "completed": ("status_type_completed", "completed", "statusCompleted"),
        "neutral": ("neutral_site", "neutralSite"),
        "home_team_spread": ("homeTeamSpread", "home_team_spread", "spread_home"),
        "game_spread": ("gameSpread", "game_spread", "spread"),
        "home_favorite": ("homeFavorite", "home_favorite"),
        "spread_available": ("gameSpreadAvailable", "game_spread_available"),
        "total": ("overUnder", "over_under", "total"),
    }
    frame = cfb._read_open_parquet(Path(path), aliases)
    if frame is None or frame.empty:
        raise RuntimeError(f"No usable SportsDataverse PBP columns for {season}")
    return frame


def last_valid(series: pd.Series) -> Any:
    values = series.dropna()
    if values.empty:
        return np.nan
    # Empty strings are not useful identifiers/market values.
    if values.dtype == object or pd.api.types.is_string_dtype(values):
        values = values[values.astype(str).str.strip() != ""]
        if values.empty:
            return np.nan
    return values.iloc[-1]


def first_valid(series: pd.Series) -> Any:
    values = series.dropna()
    if values.empty:
        return np.nan
    if values.dtype == object or pd.api.types.is_string_dtype(values):
        values = values[values.astype(str).str.strip() != ""]
        if values.empty:
            return np.nan
    return values.iloc[0]


def games_from_pbp(season: int) -> pd.DataFrame:
    pbp = load_pbp(season).copy()
    pbp["game_id"] = first_present(pbp, ["game_id"]).astype(str)
    pbp = pbp[~pbp["game_id"].isin(["", "nan", "None"])].copy()
    if pbp.empty:
        raise RuntimeError(f"No game IDs in {season} PBP")

    if "sequence" in pbp.columns:
        pbp["_sequence_num"] = pd.to_numeric(pbp["sequence"], errors="coerce")
        pbp = pbp.sort_values(["game_id", "_sequence_num"], kind="stable")
    else:
        pbp["_sequence_num"] = np.arange(len(pbp), dtype=float)

    rows: list[dict[str, Any]] = []
    for game_id, group in pbp.groupby("game_id", sort=False):
        home = str(first_valid(first_present(group, ["home_team"], "")) or "").strip()
        away = str(first_valid(first_present(group, ["away_team"], "")) or "").strip()
        week = int(num(first_valid(first_present(group, ["week"])), 0))
        if not home or not away or week <= 0:
            continue

        home_score = num(last_valid(pd.to_numeric(first_present(group, ["home_score"]), errors="coerce")), np.nan)
        away_score = num(last_valid(pd.to_numeric(first_present(group, ["away_score"]), errors="coerce")), np.nan)
        if not math.isfinite(home_score) or not math.isfinite(away_score):
            continue

        completed_value = last_valid(first_present(group, ["completed"], True))
        if "completed" in group.columns and not truthy(completed_value):
            # Some older seasons omit/garble this flag; final finite score is the
            # primary completion criterion, but an explicit false is respected.
            text = str(completed_value).strip().lower()
            if text in {"false", "0", "no"}:
                continue

        neutral = truthy(first_valid(first_present(group, ["neutral"], False)))
        home_spread = num(first_valid(pd.to_numeric(first_present(group, ["home_team_spread"]), errors="coerce")), np.nan)
        raw_spread = num(first_valid(pd.to_numeric(first_present(group, ["game_spread"]), errors="coerce")), np.nan)
        home_fav = truthy(first_valid(first_present(group, ["home_favorite"], False)))
        if not math.isfinite(home_spread) and math.isfinite(raw_spread):
            home_spread = -abs(raw_spread) if home_fav else abs(raw_spread)
        total = num(first_valid(pd.to_numeric(first_present(group, ["total"]), errors="coerce")), np.nan)

        rows.append({
            "season": season,
            "week": week,
            "game_id": str(game_id),
            "away_team": away,
            "home_team": home,
            "neutral": neutral,
            "away_score": away_score,
            "home_score": home_score,
            "home_spread": home_spread,
            "total_line": total,
            "actual_margin": home_score - away_score,
            "actual_total": home_score + away_score,
        })

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError(f"No completed games reconstructed from {season} PBP")
    frame["game_key"] = [f"{season}-{w}-{gid}" for w, gid in zip(frame["week"], frame["game_id"])]
    return frame.sort_values(["week", "game_key"]).drop_duplicates("game_key").reset_index(drop=True)


@dataclass
class TeamStats:
    power: float = 0.0
    ppg: float = 28.0
    papg: float = 28.0
    games: int = 0


def team_summary(games: pd.DataFrame) -> dict[str, TeamStats]:
    if games is None or games.empty:
        return {}
    rows = games.to_dict("records")
    teams = sorted(set(games["away_team"].astype(str)) | set(games["home_team"].astype(str)))
    by_team: dict[str, list[dict[str, Any]]] = {team: [] for team in teams}
    for game in rows:
        by_team[str(game["away_team"])].append(game)
        by_team[str(game["home_team"])].append(game)

    power = {team: 0.0 for team in teams}
    for _ in range(30):
        updated: dict[str, float] = {}
        for team in teams:
            values: list[float] = []
            for game in by_team[team]:
                away = str(game["away_team"]); home = str(game["home_team"])
                margin = num(game["actual_margin"], 0.0)
                if not truthy(game.get("neutral", False)):
                    margin -= HFA
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
        scored: list[float] = []; allowed: list[float] = []
        for game in by_team[team]:
            if team == str(game["home_team"]):
                scored.append(num(game["home_score"], 0.0)); allowed.append(num(game["away_score"], 0.0))
            else:
                scored.append(num(game["away_score"], 0.0)); allowed.append(num(game["home_score"], 0.0))
        output[team] = TeamStats(
            power=power.get(team, 0.0),
            ppg=float(np.mean(scored)) if scored else 28.0,
            papg=float(np.mean(allowed)) if allowed else 28.0,
            games=len(scored),
        )
    return output


def stat(stats: dict[str, TeamStats], team: str) -> TeamStats:
    return stats.get(team, TeamStats())


def blend(prior: TeamStats, current: TeamStats) -> TeamStats:
    weight = current.games / (current.games + 4.0) if current.games else 0.0
    return TeamStats(
        power=(1.0 - weight) * prior.power + weight * current.power,
        ppg=(1.0 - weight) * prior.ppg + weight * current.ppg,
        papg=(1.0 - weight) * prior.papg + weight * current.papg,
        games=current.games,
    )


def build_team_rows(season: int, seasons: dict[int, pd.DataFrame]) -> pd.DataFrame:
    games = seasons[season]
    prior_stats = team_summary(seasons.get(season - 1, pd.DataFrame()))
    rows: list[dict[str, Any]] = []
    for week in sorted(games["week"].unique()):
        before = games[games["week"] < int(week)]
        current_stats = team_summary(before)
        for _, game in games[games["week"] == int(week)].iterrows():
            away = str(game["away_team"]); home = str(game["home_team"])
            neutral = truthy(game.get("neutral", False))
            pa, ph = stat(prior_stats, away), stat(prior_stats, home)
            ca, ch = stat(current_stats, away), stat(current_stats, home)
            ba, bh = blend(pa, ca), blend(ph, ch)

            common = {
                "season": season,
                "week": int(week),
                "game_key": game["game_key"],
                "away_team": away,
                "home_team": home,
                "neutral": neutral,
                "market_home_spread": num(game.get("home_spread"), np.nan),
                "market_total": num(game.get("total_line"), np.nan),
                "actual_away": num(game["away_score"]),
                "actual_home": num(game["home_score"]),
                "actual_margin": num(game["actual_margin"]),
                "actual_total": num(game["actual_total"]),
            }

            for is_home in (False, True):
                own_prior = ph if is_home else pa
                opp_prior = pa if is_home else ph
                own_blend = bh if is_home else ba
                opp_blend = ba if is_home else bh
                rows.append({
                    **common,
                    "side": "home" if is_home else "away",
                    "team": home if is_home else away,
                    "opponent": away if is_home else home,
                    "points": num(game["home_score"] if is_home else game["away_score"]),
                    "prior_own_ppg": own_prior.ppg,
                    "prior_opp_papg": opp_prior.papg,
                    "prior_power_gap": own_prior.power - opp_prior.power,
                    "current_own_scoring_delta": own_blend.ppg - own_prior.ppg,
                    "current_opp_allowed_delta": opp_blend.papg - opp_prior.papg,
                    "current_power_delta": (own_blend.power - opp_blend.power) - (own_prior.power - opp_prior.power),
                    "home_indicator": 1.0 if is_home and not neutral else 0.0,
                })
    return pd.DataFrame(rows)


def fit(frame: pd.DataFrame, features: list[str]):
    X = sm.add_constant(frame[features].astype(float), has_constant="add")
    return sm.OLS(frame["points"].astype(float), X).fit()


def backward_select(frame: pd.DataFrame) -> tuple[list[str], Any, list[dict[str, Any]]]:
    features = [f for f in CANDIDATES if frame[f].astype(float).std(ddof=0) > 1e-9]
    history: list[dict[str, Any]] = []
    while len(features) > 1:
        model = fit(frame, features)
        pvals = model.pvalues.drop("const", errors="ignore")
        worst = str(pvals.idxmax()); p = float(pvals.max())
        history.append({"features": list(features), "worst": worst, "worst_p": p})
        if p <= 0.05:
            break
        features.remove(worst)
    return features, fit(frame, features), history


def sign_confirm(discovery: pd.DataFrame, confirmation: pd.DataFrame, selected: list[str]):
    kept = list(selected)
    checks: list[dict[str, Any]] = []
    while len(kept) > 1:
        dm = fit(discovery, kept); cm = fit(confirmation, kept)
        flips: list[str] = []; checks = []
        for feature in kept:
            dcoef = float(dm.params[feature]); ccoef = float(cm.params[feature])
            same = bool(np.sign(dcoef) == np.sign(ccoef) or abs(dcoef) < 1e-12 or abs(ccoef) < 1e-12)
            checks.append({
                "feature": feature,
                "discovery_coefficient": dcoef,
                "discovery_p": float(dm.pvalues[feature]),
                "confirmation_coefficient": ccoef,
                "confirmation_p": float(cm.pvalues[feature]),
                "same_sign": same,
            })
            if not same:
                flips.append(feature)
        if not flips:
            return kept, dm, cm, checks
        drop = max(flips, key=lambda f: float(dm.pvalues[f]))
        kept.remove(drop)
    return kept, fit(discovery, kept), fit(confirmation, kept), checks


def predict_team(frame: pd.DataFrame, model: Any, features: list[str], label: str) -> pd.DataFrame:
    out = frame.copy()
    X = sm.add_constant(out[features].astype(float), has_constant="add")
    out[label] = np.asarray(model.predict(X), dtype=float)
    return out


def to_games(team_rows: pd.DataFrame, label: str) -> pd.DataFrame:
    keys = [
        "season", "week", "game_key", "away_team", "home_team", "neutral",
        "market_home_spread", "market_total", "actual_away", "actual_home",
        "actual_margin", "actual_total",
    ]
    base = team_rows[keys].drop_duplicates("game_key").set_index("game_key")
    away = team_rows[team_rows["side"] == "away"].set_index("game_key")[[label]].rename(columns={label: "pred_away"})
    home = team_rows[team_rows["side"] == "home"].set_index("game_key")[[label]].rename(columns={label: "pred_home"})
    out = base.join(away).join(home).reset_index()
    out["pred_margin"] = out["pred_home"] - out["pred_away"]
    out["pred_total"] = out["pred_home"] + out["pred_away"]
    return out


def score(frame: pd.DataFrame) -> dict[str, Any]:
    me = frame["pred_margin"] - frame["actual_margin"]
    te = frame["pred_total"] - frame["actual_total"]
    return {
        "n": int(len(frame)),
        "margin_mae": float(np.mean(np.abs(me))),
        "margin_rmse": float(np.sqrt(np.mean(np.square(me)))),
        "margin_bias": float(np.mean(me)),
        "total_mae": float(np.mean(np.abs(te))),
        "total_rmse": float(np.sqrt(np.mean(np.square(te)))),
        "total_bias": float(np.mean(te)),
    }


def scoring_baseline(team_rows: pd.DataFrame) -> pd.DataFrame:
    out = team_rows.copy()
    # Transparent scoring-only baseline. This is NOT claimed to be exact v1.5;
    # it isolates the compression issue by excluding opponent-adjusted power.
    out["baseline"] = (
        0.55 * (out["prior_own_ppg"] + out["current_own_scoring_delta"])
        + 0.45 * (out["prior_opp_papg"] + out["current_opp_allowed_delta"])
        + 1.5 * out["home_indicator"]
    )
    return to_games(out, "baseline")


def bucket_name(x: float) -> str:
    if x < 7: return "0-6.5"
    if x < 14: return "7-13.5"
    if x < 21: return "14-20.5"
    if x < 28: return "21-27.5"
    return "28+"


def favorite_buckets(frame: pd.DataFrame) -> dict[str, Any]:
    valid = frame[np.isfinite(pd.to_numeric(frame["market_home_spread"], errors="coerce"))].copy()
    if valid.empty:
        return {}
    valid["abs_spread"] = pd.to_numeric(valid["market_home_spread"], errors="coerce").abs()
    valid = valid[valid["abs_spread"] > 0].copy()
    valid["bucket"] = valid["abs_spread"].map(bucket_name)
    result: dict[str, Any] = {}
    for bucket in ["0-6.5", "7-13.5", "14-20.5", "21-27.5", "28+"]:
        sub = valid[valid["bucket"] == bucket].copy()
        if sub.empty:
            result[bucket] = {"n": 0}
            continue
        home_cover_model = (sub["pred_margin"] + sub["market_home_spread"]) > 0
        ats_value = sub["actual_margin"] + sub["market_home_spread"]
        non_push = ats_value.abs() > 1e-9
        ats_accuracy = float(np.mean(home_cover_model[non_push] == (ats_value[non_push] > 0))) if non_push.any() else np.nan
        result[bucket] = {
            "n": int(len(sub)),
            "margin_mae": float(np.mean(np.abs(sub["pred_margin"] - sub["actual_margin"]))),
            "margin_rmse": float(np.sqrt(np.mean(np.square(sub["pred_margin"] - sub["actual_margin"])))),
            "total_mae": float(np.mean(np.abs(sub["pred_total"] - sub["actual_total"]))),
            "ats_direction_accuracy": ats_accuracy,
            "model_mean_abs_margin": float(np.mean(np.abs(sub["pred_margin"]))),
            "actual_mean_abs_margin": float(np.mean(np.abs(sub["actual_margin"]))),
            "market_mean_abs_spread": float(np.mean(sub["abs_spread"])),
            "market_margin_mae": float(np.mean(np.abs((-sub["market_home_spread"]) - sub["actual_margin"]))),
        }
    return result


def favorite_side_bias(frame: pd.DataFrame) -> dict[str, Any]:
    valid = frame[np.isfinite(pd.to_numeric(frame["market_home_spread"], errors="coerce"))].copy()
    if valid.empty:
        return {}
    # Orient all margins from the market favorite's perspective.
    home_favorite = valid["market_home_spread"] < 0
    favorite_model_margin = np.where(home_favorite, valid["pred_margin"], -valid["pred_margin"])
    favorite_actual_margin = np.where(home_favorite, valid["actual_margin"], -valid["actual_margin"])
    market_favorite_margin = valid["market_home_spread"].abs().to_numpy()
    return {
        "n": int(len(valid)),
        "mean_model_favorite_margin": float(np.mean(favorite_model_margin)),
        "mean_actual_favorite_margin": float(np.mean(favorite_actual_margin)),
        "mean_market_favorite_margin": float(np.mean(market_favorite_margin)),
        "mean_model_minus_market": float(np.mean(favorite_model_margin - market_favorite_margin)),
        "mean_actual_minus_market": float(np.mean(favorite_actual_margin - market_favorite_margin)),
    }


def preseason_case(model: Any, features: list[str], prior_stats: dict[str, TeamStats], away: str, home: str) -> dict[str, Any]:
    pa, ph = stat(prior_stats, away), stat(prior_stats, home)
    rows = []
    for is_home in (False, True):
        own = ph if is_home else pa; opp = pa if is_home else ph
        values = {
            "prior_own_ppg": own.ppg,
            "prior_opp_papg": opp.papg,
            "prior_power_gap": own.power - opp.power,
            "current_own_scoring_delta": 0.0,
            "current_opp_allowed_delta": 0.0,
            "current_power_delta": 0.0,
            "home_indicator": 1.0 if is_home else 0.0,
        }
        X = sm.add_constant(pd.DataFrame([values])[features].astype(float), has_constant="add")
        rows.append(float(model.predict(X).iloc[0]))
    return {
        "away_team": away,
        "home_team": home,
        "projected_away": rows[0],
        "projected_home": rows[1],
        "projected_home_margin": rows[1] - rows[0],
        "projected_total": rows[1] + rows[0],
        "note": "Prior-only sanity case using 2025 performance; no 2026 roster/portal/injury overlay.",
    }


def usc_sjsu_case(model: Any, features: list[str], games_2025: pd.DataFrame) -> list[dict[str, Any]]:
    prior = team_summary(games_2025)
    teams = list(prior)
    usc = [t for t in teams if clean(t) in {"usc", "southern california", "southern california trojans"} or "southern california" in clean(t)]
    sjsu = [t for t in teams if "san jose state" in clean(t)]
    if not usc or not sjsu:
        return [{"status": "team names not resolved", "usc_matches": usc, "sjsu_matches": sjsu}]
    return [preseason_case(model, features, prior, sjsu[0], usc[0])]


def jsonable(value: Any) -> Any:
    if isinstance(value, dict): return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list): return [jsonable(v) for v in value]
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (np.floating,)): value = float(value)
    if isinstance(value, float): return value if math.isfinite(value) else None
    if isinstance(value, (np.bool_,)): return bool(value)
    return value


def main() -> None:
    print("Loading SportsDataverse/cfbfastR historical PBP...")
    seasons: dict[int, pd.DataFrame] = {}
    for season in range(2020, 2026):
        seasons[season] = games_from_pbp(season)
        lines = int(np.isfinite(pd.to_numeric(seasons[season]["home_spread"], errors="coerce")).sum())
        print(f"{season}: games={len(seasons[season])}, games_with_spread={lines}")

    parts = []
    for season in range(2021, 2026):
        frame = build_team_rows(season, seasons)
        parts.append(frame)
        print(f"{season}: pregame team rows={len(frame)}")
    all_rows = pd.concat(parts, ignore_index=True)

    discovery = all_rows[all_rows["season"].isin(DISCOVERY)].copy()
    confirmation = all_rows[all_rows["season"] == CONFIRM].copy()
    holdout = all_rows[all_rows["season"] == HOLDOUT].copy()

    selected, discovery_model, history = backward_select(discovery)
    selected, discovery_model, confirmation_model, sign_checks = sign_confirm(discovery, confirmation, selected)
    final_train = all_rows[all_rows["season"] <= CONFIRM].copy()
    final_model = fit(final_train, selected)

    predicted_team = predict_team(holdout, final_model, selected, "prediction")
    predicted_games = to_games(predicted_team, "prediction")
    regression_score = score(predicted_games)
    baseline_games = scoring_baseline(holdout)
    baseline_score = score(baseline_games)

    buckets = favorite_buckets(predicted_games)
    baseline_buckets = favorite_buckets(baseline_games)
    favorite_bias = favorite_side_bias(predicted_games)
    baseline_favorite_bias = favorite_side_bias(baseline_games)

    improvements = {
        "margin_mae_pct": 100.0 * (baseline_score["margin_mae"] - regression_score["margin_mae"]) / baseline_score["margin_mae"],
        "margin_rmse_pct": 100.0 * (baseline_score["margin_rmse"] - regression_score["margin_rmse"]) / baseline_score["margin_rmse"],
        "total_mae_pct": 100.0 * (baseline_score["total_mae"] - regression_score["total_mae"]) / baseline_score["total_mae"],
        "total_rmse_pct": 100.0 * (baseline_score["total_rmse"] - regression_score["total_rmse"]) / baseline_score["total_rmse"],
    }

    results = {
        "research_version": "cfb-team-score-regression-v2-2026-08-21",
        "design": {
            "data": "SportsDataverse/cfbfastR PBP reconstructed games",
            "discovery": "2021-2023",
            "confirmation": "2024 coefficient-sign confirmation",
            "holdout": "2025 untouched",
            "market_used_as_predictor": False,
            "current_season_features": "prior weeks only; blended with previous season via games/(games+4)",
            "power": "iterative opponent-adjusted neutral margin, winsorized at 45 points",
            "baseline": "scoring-only structural baseline; not claimed to be exact production v1.5",
        },
        "sample_sizes": {
            "discovery_team_rows": int(len(discovery)),
            "confirmation_team_rows": int(len(confirmation)),
            "holdout_team_rows": int(len(holdout)),
            "holdout_games": int(len(predicted_games)),
            "holdout_games_with_spread": int(np.isfinite(pd.to_numeric(predicted_games["market_home_spread"], errors="coerce")).sum()),
        },
        "selected_features": selected,
        "selection_history": history,
        "sign_confirmation": sign_checks,
        "final_coefficients": {k: float(v) for k, v in final_model.params.items()},
        "final_p_values": {k: float(v) for k, v in final_model.pvalues.items()},
        "holdout_regression": regression_score,
        "holdout_scoring_baseline": baseline_score,
        "improvement_vs_scoring_baseline": improvements,
        "favorite_size_buckets_regression": buckets,
        "favorite_size_buckets_baseline": baseline_buckets,
        "favorite_side_bias_regression": favorite_bias,
        "favorite_side_bias_baseline": baseline_favorite_bias,
        "usc_san_jose_state_prior_only_sanity_case": usc_sjsu_case(final_model, selected, seasons[2025]),
    }
    RESULT_JSON.write_text(json.dumps(jsonable(results), indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# CFB team-score regression v2",
        "",
        "SportsDataverse/cfbfastR historical PBP; discovery 2021-23, confirmation 2024, untouched 2025 holdout.",
        "Archived sportsbook lines are evaluation-only and never regression predictors.",
        "",
        f"Selected features: {', '.join(selected)}",
        "",
        "## 2025 holdout",
        f"- Games: {regression_score['n']}",
        f"- Regression margin MAE: {regression_score['margin_mae']:.3f}",
        f"- Baseline margin MAE: {baseline_score['margin_mae']:.3f}",
        f"- Margin MAE improvement: {improvements['margin_mae_pct']:.2f}%",
        f"- Regression margin RMSE: {regression_score['margin_rmse']:.3f}",
        f"- Baseline margin RMSE: {baseline_score['margin_rmse']:.3f}",
        f"- Regression total MAE: {regression_score['total_mae']:.3f}",
        f"- Baseline total MAE: {baseline_score['total_mae']:.3f}",
        f"- Total MAE improvement: {improvements['total_mae_pct']:.2f}%",
        f"- Regression total RMSE: {regression_score['total_rmse']:.3f}",
        f"- Baseline total RMSE: {baseline_score['total_rmse']:.3f}",
    ]
    b28 = buckets.get("28+", {})
    if b28.get("n", 0):
        lines += [
            "",
            "## 28+ favorites",
            f"- Games with archived spread: {b28['n']}",
            f"- Regression margin MAE: {b28['margin_mae']:.3f}",
            f"- ATS direction accuracy: {b28['ats_direction_accuracy']:.2%}",
            f"- Mean model absolute margin: {b28['model_mean_abs_margin']:.3f}",
            f"- Mean actual absolute margin: {b28['actual_mean_abs_margin']:.3f}",
            f"- Mean market spread: {b28['market_mean_abs_spread']:.3f}",
        ]
    lines += ["", "## USC-San Jose State prior-only sanity case", "```json", json.dumps(jsonable(results["usc_san_jose_state_prior_only_sanity_case"]), indent=2), "```"]
    RESULT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(RESULT_MD.read_text(encoding="utf-8"))
    print(f"Wrote {RESULT_JSON}")


if __name__ == "__main__":
    main()
