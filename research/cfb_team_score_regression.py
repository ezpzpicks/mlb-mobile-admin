"""Leakage-safe college-football team-score regression research.

Research design
---------------
* Discovery: 2021-2023
* Confirmation: 2024
* Untouched holdout: 2025
* Primary sample: completed FBS-vs-FBS games with pregame information only.
* Sportsbook spreads/totals are NEVER predictors. They are used only for
  evaluation buckets and market-comparison diagnostics.

The purpose is to replace the hand-weighted CFB v1.5 scoring core with the
same general philosophy used by the NFL game regression: estimate each team's
score from stable pregame strength/scoring features, then derive margin and
total. Current-game personnel/weather/venue can remain live overlays later.
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

# Make the public/open-data path usable in GitHub Actions for the optional
# preseason roster/talent enrichment. The regression still runs if that layer
# is unavailable.
os.environ.setdefault("EZPZ_CFB_ALLOW_BLOCKING_OPEN_DATA", "1")
os.environ.setdefault("EZPZ_CFB_CACHE_SECONDS", "86400")

from builders import cfb_builder as cfb  # noqa: E402

RESULT_DIR = Path("research/results")
RESULT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON = RESULT_DIR / "cfb_team_score_regression_results.json"
RESULT_MD = RESULT_DIR / "cfb_team_score_regression_summary.md"

DISCOVERY_SEASONS = {2021, 2022, 2023}
CONFIRMATION_SEASON = 2024
HOLDOUT_SEASON = 2025
ALL_FEATURE_SEASONS = [2021, 2022, 2023, 2024, 2025]
HFA_NEUTRAL_PRIOR = 2.0

CANDIDATES = [
    "prior_scoring_matchup",
    "prior_power_gap",
    "current_scoring_delta",
    "current_power_delta",
    "builder_preseason_gap",
    "talent_gap",
    "returning_gap",
    "qb_continuity_gap",
    "home_indicator",
]


def num(value: Any, default: float = np.nan) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "completed"}


def clean_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("&", "and")
    text = re.sub(r"[^A-Za-z0-9]+", " ", text).strip().lower()
    return " ".join(text.split())


def column(frame: pd.DataFrame, names: list[str], default: Any = np.nan) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return frame[name]
    return pd.Series(default, index=frame.index)


def completed_games(season: int) -> pd.DataFrame:
    payload = cfb._espn_games_payload(season)
    frame = cfb._parse_games(payload, season).copy()
    if frame.empty:
        return frame

    completed = column(frame, ["Completed", "completed"], True).map(boolish)
    away_score = pd.to_numeric(column(frame, ["Away Score", "away_score"]), errors="coerce")
    home_score = pd.to_numeric(column(frame, ["Home Score", "home_score"]), errors="coerce")
    week = pd.to_numeric(column(frame, ["Week", "week"]), errors="coerce")
    frame = frame[completed & away_score.notna() & home_score.notna() & week.notna()].copy()
    if frame.empty:
        return frame

    frame["Away Score"] = pd.to_numeric(column(frame, ["Away Score", "away_score"]), errors="coerce")
    frame["Home Score"] = pd.to_numeric(column(frame, ["Home Score", "home_score"]), errors="coerce")
    frame["Week"] = pd.to_numeric(column(frame, ["Week", "week"]), errors="coerce").astype(int)
    frame["Away Team"] = column(frame, ["Away Team", "away_team"]).astype(str)
    frame["Home Team"] = column(frame, ["Home Team", "home_team"]).astype(str)
    frame["Neutral Site"] = column(frame, ["Neutral Site", "neutral_site"], False).map(boolish)

    away_class = column(frame, ["Away Classification", "away_classification"], "fbs").astype(str).str.lower()
    home_class = column(frame, ["Home Classification", "home_classification"], "fbs").astype(str).str.lower()
    # ESPN occasionally leaves classifications blank. Keep blank rows when the
    # teams themselves are on the FBS schedule, but explicitly exclude known FCS.
    keep = (~away_class.str.contains("fcs")) & (~home_class.str.contains("fcs"))
    frame = frame[keep].copy()

    for target, names in [
        ("Home Spread", ["Home Spread", "home_spread", "spread"]),
        ("Total", ["Total", "total"]),
        ("Opening Home Spread", ["Opening Home Spread", "opening_home_spread"]),
        ("Opening Total", ["Opening Total", "opening_total"]),
    ]:
        frame[target] = pd.to_numeric(column(frame, names), errors="coerce")

    frame["Season"] = season
    frame["Game Key"] = [
        f"{season}-{int(w)}-{clean_name(a)}-{clean_name(h)}"
        for w, a, h in zip(frame["Week"], frame["Away Team"], frame["Home Team"])
    ]
    return frame.sort_values(["Week", "Game Key"]).reset_index(drop=True)


@dataclass
class TeamStats:
    power: float = 0.0
    ppg: float = 28.0
    papg: float = 28.0
    games: int = 0


def team_summary(games: pd.DataFrame) -> dict[str, TeamStats]:
    """Opponent-adjusted result strength without the v1.5 24-point compression.

    Margin is winsorized at 45 points rather than transformed/capped at 24.
    Home field is removed before the iterative SRS-like adjustment. The purpose
    is to preserve meaningful information in large FBS mismatches while still
    preventing 70-point outliers from dominating.
    """
    if games is None or games.empty:
        return {}

    teams = sorted(set(games["Away Team"].astype(str)) | set(games["Home Team"].astype(str)))
    power = {team: 0.0 for team in teams}
    game_rows = games.to_dict("records")

    for _ in range(30):
        updated: dict[str, float] = {}
        for team in teams:
            values: list[float] = []
            for game in game_rows:
                away = str(game["Away Team"])
                home = str(game["Home Team"])
                if team not in {away, home}:
                    continue
                raw_margin = num(game["Home Score"], 0.0) - num(game["Away Score"], 0.0)
                if not boolish(game.get("Neutral Site", False)):
                    raw_margin -= HFA_NEUTRAL_PRIOR
                neutral_margin = float(np.clip(raw_margin, -45.0, 45.0))
                if team == home:
                    opponent = away
                    team_margin = neutral_margin
                else:
                    opponent = home
                    team_margin = -neutral_margin
                values.append(team_margin + power.get(opponent, 0.0))
            updated[team] = float(np.mean(values)) if values else 0.0
        center = float(np.mean(list(updated.values()))) if updated else 0.0
        power = {team: float(np.clip(value - center, -45.0, 45.0)) for team, value in updated.items()}

    out: dict[str, TeamStats] = {}
    for team in teams:
        scored: list[float] = []
        allowed: list[float] = []
        for game in game_rows:
            away = str(game["Away Team"])
            home = str(game["Home Team"])
            if team == home:
                scored.append(num(game["Home Score"], 0.0))
                allowed.append(num(game["Away Score"], 0.0))
            elif team == away:
                scored.append(num(game["Away Score"], 0.0))
                allowed.append(num(game["Home Score"], 0.0))
        n = len(scored)
        out[team] = TeamStats(
            power=power.get(team, 0.0),
            ppg=float(np.mean(scored)) if scored else 28.0,
            papg=float(np.mean(allowed)) if allowed else 28.0,
            games=n,
        )
    return out


def preseason_builder_features(season: int) -> dict[str, dict[str, float]]:
    """Optional preseason enrichment from the existing CFB public-data pipeline."""
    try:
        ratings = cfb.build_team_ratings(season, 1)
    except Exception as exc:
        print(f"Preseason enrichment unavailable for {season}: {exc}")
        return {}
    if ratings is None or ratings.empty:
        return {}
    output: dict[str, dict[str, float]] = {}
    for _, row in ratings.iterrows():
        output[clean_name(row.get("Team"))] = {
            "builder_preseason": num(row.get("Preseason Rating"), 0.0),
            "talent": num(row.get("Talent Rating"), 0.0),
            "returning": num(row.get("Returning Production"), 0.50),
            "qb_continuity": num(row.get("QB Continuity"), 0.50),
        }
    return output


def lookup(stats: dict[str, TeamStats], team: str) -> TeamStats:
    return stats.get(team, TeamStats())


def blend(prior: TeamStats, current: TeamStats) -> TeamStats:
    # Smooth current-season evidence in rather than abruptly replacing the prior.
    w = current.games / (current.games + 4.0) if current.games > 0 else 0.0
    return TeamStats(
        power=(1.0 - w) * prior.power + w * current.power,
        ppg=(1.0 - w) * prior.ppg + w * current.ppg,
        papg=(1.0 - w) * prior.papg + w * current.papg,
        games=current.games,
    )


def build_season_rows(
    season: int,
    games_by_season: dict[int, pd.DataFrame],
    preseason: dict[int, dict[str, dict[str, float]]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    games = games_by_season[season]
    prior_stats = team_summary(games_by_season.get(season - 1, pd.DataFrame()))
    preseason_map = preseason.get(season, {})
    team_rows: list[dict[str, Any]] = []
    game_rows: list[dict[str, Any]] = []

    for week in sorted(games["Week"].unique()):
        pre_games = games[games["Week"] < int(week)].copy()
        current_stats = team_summary(pre_games)
        week_games = games[games["Week"] == int(week)]

        for _, game in week_games.iterrows():
            away = str(game["Away Team"])
            home = str(game["Home Team"])
            neutral = boolish(game.get("Neutral Site", False))

            prior_away = lookup(prior_stats, away)
            prior_home = lookup(prior_stats, home)
            curr_away = lookup(current_stats, away)
            curr_home = lookup(current_stats, home)
            blend_away = blend(prior_away, curr_away)
            blend_home = blend(prior_home, curr_home)

            pre_away = preseason_map.get(clean_name(away), {})
            pre_home = preseason_map.get(clean_name(home), {})

            actual_away = num(game["Away Score"])
            actual_home = num(game["Home Score"])
            common = {
                "season": season,
                "week": int(week),
                "game_key": game["Game Key"],
                "away_team": away,
                "home_team": home,
                "neutral": neutral,
                "market_home_spread": num(game.get("Home Spread"), np.nan),
                "market_total": num(game.get("Total"), np.nan),
                "actual_away": actual_away,
                "actual_home": actual_home,
                "actual_margin": actual_home - actual_away,
                "actual_total": actual_home + actual_away,
            }

            # Two rows per game; a single team-score equation is then used for
            # both home and away teams.
            for side in ("away", "home"):
                is_home = side == "home"
                own = home if is_home else away
                opp = away if is_home else home
                own_prior = prior_home if is_home else prior_away
                opp_prior = prior_away if is_home else prior_home
                own_blend = blend_home if is_home else blend_away
                opp_blend = blend_away if is_home else blend_home
                own_pre = pre_home if is_home else pre_away
                opp_pre = pre_away if is_home else pre_home

                prior_scoring = 0.55 * own_prior.ppg + 0.45 * opp_prior.papg
                blended_scoring = 0.55 * own_blend.ppg + 0.45 * opp_blend.papg
                prior_power_gap = own_prior.power - opp_prior.power
                blended_power_gap = own_blend.power - opp_blend.power

                team_rows.append({
                    **common,
                    "side": side,
                    "team": own,
                    "opponent": opp,
                    "points": actual_home if is_home else actual_away,
                    "prior_scoring_matchup": prior_scoring,
                    "prior_power_gap": prior_power_gap,
                    "current_scoring_delta": blended_scoring - prior_scoring,
                    "current_power_delta": blended_power_gap - prior_power_gap,
                    "builder_preseason_gap": num(own_pre.get("builder_preseason"), 0.0) - num(opp_pre.get("builder_preseason"), 0.0),
                    "talent_gap": num(own_pre.get("talent"), 0.0) - num(opp_pre.get("talent"), 0.0),
                    "returning_gap": num(own_pre.get("returning"), 0.50) - num(opp_pre.get("returning"), 0.50),
                    "qb_continuity_gap": num(own_pre.get("qb_continuity"), 0.50) - num(opp_pre.get("qb_continuity"), 0.50),
                    "home_indicator": 1.0 if is_home and not neutral else 0.0,
                })

            game_rows.append(common)

    return pd.DataFrame(team_rows), pd.DataFrame(game_rows)


def fit_ols(frame: pd.DataFrame, features: list[str]):
    X = frame[features].astype(float)
    X = sm.add_constant(X, has_constant="add")
    y = frame["points"].astype(float)
    return sm.OLS(y, X).fit()


def backward_select(discovery: pd.DataFrame, candidates: list[str]) -> tuple[list[str], Any, list[dict[str, Any]]]:
    usable = [f for f in candidates if f in discovery.columns and discovery[f].astype(float).std(ddof=0) > 1e-8]
    history: list[dict[str, Any]] = []
    while len(usable) > 1:
        model = fit_ols(discovery, usable)
        pvals = model.pvalues.drop(labels=["const"], errors="ignore")
        worst_name = str(pvals.idxmax())
        worst_p = float(pvals.max())
        history.append({"features": list(usable), "worst": worst_name, "worst_p": worst_p})
        if worst_p <= 0.05:
            return usable, model, history
        usable.remove(worst_name)
    model = fit_ols(discovery, usable)
    return usable, model, history


def require_sign_confirmation(
    discovery: pd.DataFrame,
    confirmation: pd.DataFrame,
    selected: list[str],
) -> tuple[list[str], Any, Any, list[dict[str, Any]]]:
    kept = list(selected)
    checks: list[dict[str, Any]] = []
    while len(kept) > 1:
        dmodel = fit_ols(discovery, kept)
        cmodel = fit_ols(confirmation, kept)
        flips: list[str] = []
        checks = []
        for feature in kept:
            dcoef = float(dmodel.params[feature])
            ccoef = float(cmodel.params[feature])
            same = np.sign(dcoef) == np.sign(ccoef) or abs(dcoef) < 1e-10 or abs(ccoef) < 1e-10
            checks.append({
                "feature": feature,
                "discovery_coefficient": dcoef,
                "discovery_p": float(dmodel.pvalues[feature]),
                "confirmation_coefficient": ccoef,
                "confirmation_p": float(cmodel.pvalues[feature]),
                "same_sign": bool(same),
            })
            if not same:
                flips.append(feature)
        if not flips:
            return kept, dmodel, cmodel, checks
        # Remove the least significant discovery feature among sign-flippers.
        drop = max(flips, key=lambda f: float(dmodel.pvalues[f]))
        kept.remove(drop)
    return kept, fit_ols(discovery, kept), fit_ols(confirmation, kept), checks


def predict_team_rows(frame: pd.DataFrame, model: Any, features: list[str], label: str) -> pd.DataFrame:
    out = frame.copy()
    X = sm.add_constant(out[features].astype(float), has_constant="add")
    out[label] = np.asarray(model.predict(X), dtype=float)
    return out


def game_predictions(team_frame: pd.DataFrame, label: str) -> pd.DataFrame:
    keys = [
        "season", "week", "game_key", "away_team", "home_team", "neutral",
        "market_home_spread", "market_total", "actual_away", "actual_home",
        "actual_margin", "actual_total",
    ]
    base = team_frame[keys].drop_duplicates("game_key").set_index("game_key")
    away = team_frame[team_frame["side"] == "away"].set_index("game_key")[[label]].rename(columns={label: "pred_away"})
    home = team_frame[team_frame["side"] == "home"].set_index("game_key")[[label]].rename(columns={label: "pred_home"})
    out = base.join(away).join(home).reset_index()
    out["pred_margin"] = out["pred_home"] - out["pred_away"]
    out["pred_total"] = out["pred_home"] + out["pred_away"]
    return out


def metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    margin_err = frame["pred_margin"] - frame["actual_margin"]
    total_err = frame["pred_total"] - frame["actual_total"]
    return {
        "n": int(len(frame)),
        "margin_mae": float(np.mean(np.abs(margin_err))),
        "margin_rmse": float(np.sqrt(np.mean(np.square(margin_err)))),
        "margin_bias": float(np.mean(margin_err)),
        "total_mae": float(np.mean(np.abs(total_err))),
        "total_rmse": float(np.sqrt(np.mean(np.square(total_err)))),
        "total_bias": float(np.mean(total_err)),
    }


def simple_structural_baseline(team_frame: pd.DataFrame) -> pd.DataFrame:
    """A transparent approximation of the v1.5 compression problem.

    It mirrors the score-matchup center the current model leans on and adds a
    standard home-field split, but intentionally does not use the new power-gap
    regression. This is not labeled as the exact production v1.5 benchmark.
    """
    out = team_frame.copy()
    out["structural_baseline"] = (
        out["prior_scoring_matchup"]
        + out["current_scoring_delta"]
        + 1.5 * out["home_indicator"]
    )
    return game_predictions(out, "structural_baseline")


def bucket_label(abs_spread: float) -> str:
    if abs_spread < 7:
        return "0-6.5"
    if abs_spread < 14:
        return "7-13.5"
    if abs_spread < 21:
        return "14-20.5"
    if abs_spread < 28:
        return "21-27.5"
    return "28+"


def spread_buckets(frame: pd.DataFrame) -> dict[str, Any]:
    valid = frame[np.isfinite(pd.to_numeric(frame["market_home_spread"], errors="coerce"))].copy()
    valid = valid[pd.to_numeric(valid["market_home_spread"], errors="coerce") != 0].copy()
    if valid.empty:
        return {}
    valid["abs_spread"] = pd.to_numeric(valid["market_home_spread"], errors="coerce").abs()
    valid["bucket"] = valid["abs_spread"].map(bucket_label)
    rows: dict[str, Any] = {}
    for bucket in ["0-6.5", "7-13.5", "14-20.5", "21-27.5", "28+"]:
        sub = valid[valid["bucket"] == bucket].copy()
        if sub.empty:
            rows[bucket] = {"n": 0}
            continue
        # ATS direction from independent model vs entered/archived line.
        model_home_cover = (sub["pred_margin"] + sub["market_home_spread"]) > 0
        actual_home_cover_value = sub["actual_margin"] + sub["market_home_spread"]
        non_push = actual_home_cover_value.abs() > 1e-9
        ats = float(np.mean(model_home_cover[non_push] == (actual_home_cover_value[non_push] > 0))) if non_push.any() else np.nan
        rows[bucket] = {
            "n": int(len(sub)),
            "margin_mae": float(np.mean(np.abs(sub["pred_margin"] - sub["actual_margin"]))),
            "total_mae": float(np.mean(np.abs(sub["pred_total"] - sub["actual_total"]))),
            "ats_direction_accuracy": ats,
            "market_margin_mae": float(np.mean(np.abs((-sub["market_home_spread"]) - sub["actual_margin"]))),
            "model_mean_abs_margin": float(np.mean(np.abs(sub["pred_margin"]))),
            "actual_mean_abs_margin": float(np.mean(np.abs(sub["actual_margin"]))),
            "market_mean_abs_spread": float(np.mean(sub["abs_spread"])),
        }
    return rows


def exact_v15_holdout(games_2025: pd.DataFrame) -> tuple[pd.DataFrame | None, str]:
    """Best-effort benchmark using the current production builder itself.

    We keep current-game personnel neutral. Historical venue/HFA is retained;
    old-game weather is neutral because forecasts are intentionally unavailable.
    This path is allowed to fail without invalidating the new regression run.
    """
    rows: list[dict[str, Any]] = []
    try:
        for week in sorted(games_2025["Week"].unique()):
            ratings = cfb.build_team_ratings(2025, int(week))
            week_games = games_2025[games_2025["Week"] == int(week)]
            for _, game in week_games.iterrows():
                away = cfb._rating_row(ratings, str(game["Away Team"]))
                home = cfb._rating_row(ratings, str(game["Home Team"]))
                market_spread = num(game.get("Home Spread"), 0.0)
                possessions = cfb._expected_possessions(away, home, market_spread)
                away_points, _ = cfb._project_points(away, home, possessions)
                home_points, _ = cfb._project_points(home, away, possessions)
                try:
                    env = cfb.build_environment(game, 2025)
                    away_points -= num(env.home_field, 0.0) / 2.0
                    home_points += num(env.home_field, 0.0) / 2.0
                    away_points += num(env.weather_total_adjustment, 0.0) / 2.0
                    home_points += num(env.weather_total_adjustment, 0.0) / 2.0
                except Exception:
                    if not boolish(game.get("Neutral Site", False)):
                        away_points -= HFA_NEUTRAL_PRIOR / 2.0
                        home_points += HFA_NEUTRAL_PRIOR / 2.0
                rows.append({
                    "game_key": game["Game Key"],
                    "pred_away": away_points,
                    "pred_home": home_points,
                    "pred_margin": home_points - away_points,
                    "pred_total": home_points + away_points,
                })
        return pd.DataFrame(rows), "ok"
    except Exception as exc:
        return None, f"unavailable: {type(exc).__name__}: {exc}"


def case_study_2026(
    final_model: Any,
    features: list[str],
    games_by_season: dict[int, pd.DataFrame],
    preseason: dict[int, dict[str, dict[str, float]]],
) -> list[dict[str, Any]]:
    """Project USC-San Jose State if ESPN has the 2026 matchup posted."""
    try:
        games_2026_all = cfb._parse_games(cfb._espn_games_payload(2026), 2026).copy()
        if games_2026_all.empty:
            return []
        games_2026_all["Away Team"] = column(games_2026_all, ["Away Team", "away_team"]).astype(str)
        games_2026_all["Home Team"] = column(games_2026_all, ["Home Team", "home_team"]).astype(str)
        games_2026_all["Week"] = pd.to_numeric(column(games_2026_all, ["Week", "week"]), errors="coerce").fillna(1).astype(int)
        games_2026_all["Neutral Site"] = column(games_2026_all, ["Neutral Site", "neutral_site"], False).map(boolish)
        games_2026_all["Home Spread"] = pd.to_numeric(column(games_2026_all, ["Home Spread", "home_spread", "spread"]), errors="coerce")
        games_2026_all["Total"] = pd.to_numeric(column(games_2026_all, ["Total", "total"]), errors="coerce")
        names = games_2026_all["Away Team"].map(clean_name) + " " + games_2026_all["Home Team"].map(clean_name)
        mask = names.str.contains("usc") & names.str.contains("san jose state")
        target_games = games_2026_all[mask].copy()
        if target_games.empty:
            return []

        prior_stats = team_summary(games_by_season[2025])
        preseason_map = preseason.get(2026, {})
        output: list[dict[str, Any]] = []
        for _, game in target_games.iterrows():
            away = str(game["Away Team"]); home = str(game["Home Team"])
            neutral = boolish(game.get("Neutral Site", False))
            pa = lookup(prior_stats, away); ph = lookup(prior_stats, home)
            ba, bh = pa, ph  # no 2026 games yet for the preseason case
            prea = preseason_map.get(clean_name(away), {}); preh = preseason_map.get(clean_name(home), {})
            rows = []
            for side in ("away", "home"):
                is_home = side == "home"
                own_prior = ph if is_home else pa; opp_prior = pa if is_home else ph
                own_blend = bh if is_home else ba; opp_blend = ba if is_home else bh
                own_pre = preh if is_home else prea; opp_pre = prea if is_home else preh
                prior_scoring = 0.55 * own_prior.ppg + 0.45 * opp_prior.papg
                blended_scoring = 0.55 * own_blend.ppg + 0.45 * opp_blend.papg
                row = {
                    "prior_scoring_matchup": prior_scoring,
                    "prior_power_gap": own_prior.power - opp_prior.power,
                    "current_scoring_delta": blended_scoring - prior_scoring,
                    "current_power_delta": (own_blend.power - opp_blend.power) - (own_prior.power - opp_prior.power),
                    "builder_preseason_gap": num(own_pre.get("builder_preseason"), 0.0) - num(opp_pre.get("builder_preseason"), 0.0),
                    "talent_gap": num(own_pre.get("talent"), 0.0) - num(opp_pre.get("talent"), 0.0),
                    "returning_gap": num(own_pre.get("returning"), 0.50) - num(opp_pre.get("returning"), 0.50),
                    "qb_continuity_gap": num(own_pre.get("qb_continuity"), 0.50) - num(opp_pre.get("qb_continuity"), 0.50),
                    "home_indicator": 1.0 if is_home and not neutral else 0.0,
                }
                X = sm.add_constant(pd.DataFrame([row])[features].astype(float), has_constant="add")
                rows.append(float(final_model.predict(X).iloc[0]))
            pred_away, pred_home = rows[0], rows[1]
            output.append({
                "away_team": away,
                "home_team": home,
                "week": int(game.get("Week", 1)),
                "market_home_spread": num(game.get("Home Spread"), np.nan),
                "market_total": num(game.get("Total"), np.nan),
                "projected_away": pred_away,
                "projected_home": pred_home,
                "projected_home_margin": pred_home - pred_away,
                "projected_total": pred_home + pred_away,
            })
        return output
    except Exception as exc:
        return [{"error": f"2026 case study unavailable: {type(exc).__name__}: {exc}"}]


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
        return value if math.isfinite(value) else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return value


def main() -> None:
    print("Loading ESPN historical schedules...")
    games_by_season = {season: completed_games(season) for season in range(2020, 2026)}
    for season, frame in games_by_season.items():
        print(f"{season}: {len(frame)} completed primary-sample games")

    print("Loading optional preseason roster/talent features...")
    preseason: dict[int, dict[str, dict[str, float]]] = {}
    for season in [2021, 2022, 2023, 2024, 2025, 2026]:
        preseason[season] = preseason_builder_features(season)
        print(f"{season}: preseason enrichment teams={len(preseason[season])}")

    team_parts: list[pd.DataFrame] = []
    game_parts: list[pd.DataFrame] = []
    for season in ALL_FEATURE_SEASONS:
        team_rows, game_rows = build_season_rows(season, games_by_season, preseason)
        team_parts.append(team_rows)
        game_parts.append(game_rows)
        print(f"Built {season}: team rows={len(team_rows)}, games={len(game_rows)}")

    all_team = pd.concat(team_parts, ignore_index=True)
    discovery = all_team[all_team["season"].isin(DISCOVERY_SEASONS)].copy()
    confirmation = all_team[all_team["season"] == CONFIRMATION_SEASON].copy()
    holdout = all_team[all_team["season"] == HOLDOUT_SEASON].copy()

    selected, discovery_model, selection_history = backward_select(discovery, CANDIDATES)
    selected, discovery_model, confirmation_model, sign_checks = require_sign_confirmation(
        discovery, confirmation, selected
    )
    train_2021_24 = all_team[all_team["season"] <= CONFIRMATION_SEASON].copy()
    final_model = fit_ols(train_2021_24, selected)

    holdout_pred_team = predict_team_rows(holdout, final_model, selected, "regression_prediction")
    holdout_games = game_predictions(holdout_pred_team, "regression_prediction")
    regression_metrics = metrics(holdout_games)
    buckets = spread_buckets(holdout_games)

    structural_games = simple_structural_baseline(holdout)
    structural_metrics = metrics(structural_games)
    structural_buckets = spread_buckets(structural_games)

    exact_frame, exact_status = exact_v15_holdout(games_by_season[HOLDOUT_SEASON])
    exact_metrics: dict[str, Any] | None = None
    exact_buckets: dict[str, Any] | None = None
    if exact_frame is not None and not exact_frame.empty:
        exact_games = holdout_games.drop(columns=["pred_away", "pred_home", "pred_margin", "pred_total"]).merge(
            exact_frame, on="game_key", how="inner"
        )
        if not exact_games.empty:
            exact_metrics = metrics(exact_games)
            exact_buckets = spread_buckets(exact_games)

    case_studies = case_study_2026(final_model, selected, games_by_season, preseason)

    coeffs = {name: float(value) for name, value in final_model.params.items()}
    pvals = {name: float(value) for name, value in final_model.pvalues.items()}
    improvements = {
        "vs_structural_margin_mae_pct": 100.0 * (structural_metrics["margin_mae"] - regression_metrics["margin_mae"]) / structural_metrics["margin_mae"],
        "vs_structural_total_mae_pct": 100.0 * (structural_metrics["total_mae"] - regression_metrics["total_mae"]) / structural_metrics["total_mae"],
    }
    if exact_metrics:
        improvements.update({
            "vs_v15_margin_mae_pct": 100.0 * (exact_metrics["margin_mae"] - regression_metrics["margin_mae"]) / exact_metrics["margin_mae"],
            "vs_v15_total_mae_pct": 100.0 * (exact_metrics["total_mae"] - regression_metrics["total_mae"]) / exact_metrics["total_mae"],
        })

    results = {
        "research_version": "cfb-team-score-regression-research-2026-08-21",
        "design": {
            "discovery": "2021-2023",
            "confirmation": "2024 sign confirmation",
            "holdout": "2025 untouched",
            "market_as_predictor": False,
            "primary_sample": "completed FBS/FBS-like schedule games; explicit FCS rows excluded",
            "current_season_blend": "games/(games+4)",
            "power_method": "iterative opponent-adjusted neutral margin, winsorized at 45 rather than v1.5 compressed/capped at 24",
        },
        "sample_sizes": {
            "discovery_team_rows": int(len(discovery)),
            "confirmation_team_rows": int(len(confirmation)),
            "holdout_team_rows": int(len(holdout)),
            "holdout_games": int(len(holdout_games)),
        },
        "selected_features": selected,
        "selection_history": selection_history,
        "sign_confirmation": sign_checks,
        "final_coefficients": coeffs,
        "final_p_values": pvals,
        "holdout_regression": regression_metrics,
        "holdout_structural_baseline": structural_metrics,
        "holdout_v15_exact": exact_metrics,
        "holdout_v15_status": exact_status,
        "improvements": improvements,
        "favorite_size_buckets_regression": buckets,
        "favorite_size_buckets_structural": structural_buckets,
        "favorite_size_buckets_v15": exact_buckets,
        "case_studies_2026": case_studies,
    }

    RESULT_JSON.write_text(json.dumps(jsonable(results), indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# CFB team-score regression research",
        "",
        "Discovery: 2021-2023. Confirmation: 2024. Untouched holdout: 2025.",
        "Sportsbook spread/total were evaluation-only and never model predictors.",
        "",
        f"Selected features: {', '.join(selected)}",
        "",
        "## 2025 holdout",
        f"- Regression margin MAE: {regression_metrics['margin_mae']:.3f}",
        f"- Regression margin RMSE: {regression_metrics['margin_rmse']:.3f}",
        f"- Regression total MAE: {regression_metrics['total_mae']:.3f}",
        f"- Regression total RMSE: {regression_metrics['total_rmse']:.3f}",
        f"- Structural baseline margin MAE: {structural_metrics['margin_mae']:.3f}",
        f"- Structural baseline total MAE: {structural_metrics['total_mae']:.3f}",
    ]
    if exact_metrics:
        lines += [
            f"- Current v1.5 benchmark margin MAE: {exact_metrics['margin_mae']:.3f}",
            f"- Current v1.5 benchmark total MAE: {exact_metrics['total_mae']:.3f}",
        ]
    else:
        lines += [f"- Current v1.5 benchmark: {exact_status}"]
    if buckets.get("28+", {}).get("n", 0):
        b = buckets["28+"]
        lines += [
            "",
            "## 28+ market favorites",
            f"- Games: {b['n']}",
            f"- Regression margin MAE: {b['margin_mae']:.3f}",
            f"- Regression ATS direction accuracy: {b['ats_direction_accuracy']:.3%}",
            f"- Mean model absolute margin: {b['model_mean_abs_margin']:.3f}",
            f"- Mean actual absolute margin: {b['actual_mean_abs_margin']:.3f}",
            f"- Mean market spread: {b['market_mean_abs_spread']:.3f}",
        ]
    if case_studies:
        lines += ["", "## 2026 case study", "```json", json.dumps(jsonable(case_studies), indent=2), "```"]
    RESULT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(RESULT_MD.read_text(encoding="utf-8"))
    print(f"Wrote {RESULT_JSON}")


if __name__ == "__main__":
    main()
