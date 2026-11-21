"""Frozen, research-only NFL market-relative cover probability investigation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit, logit
from scipy.stats import norm
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

OFF = ("off_scoring", "off_pass_epa", "off_rush_epa", "off_success", "off_explosive",
       "off_turnover", "off_sacks_allowed", "off_red_zone", "off_pace")
DEF = ("def_scoring_allowed", "def_pass_epa_edge", "def_rush_epa_edge", "def_success_edge",
       "def_explosive_edge", "def_takeaway", "def_pressure_edge", "def_red_zone_edge", "def_pace")
MARKET = ("market_margin", "absolute_line", "market_total", "integer_line", "key_three", "key_seven", "market_logit")
ADDITIVE = MARKET + tuple(name + "_diff" for name in OFF + DEF) + ("rest_edge", "neutral", "pass_share_diff", "season_weight")
INTERACTIONS = ("pass_matchup_style", "rush_matchup_style", "pressure_vulnerability", "explosive_vulnerability", "red_zone_vulnerability")
MATCHUP = ADDITIVE + INTERACTIONS
FAMILIES = ("market_logit", "stats_logit", "matchup_logit", "residual_ridge")
PARAMETERS = {family: ((8.0, 32.0, 128.0) if family == "residual_ridge" else (0.01, 0.1, 1.0)) for family in FAMILIES}
THRESHOLDS = (0.02, 0.04, 0.06)
EPS = 1e-6


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def implied(odds: Any) -> np.ndarray:
    odds = np.asarray(odds, dtype=float)
    if not np.isfinite(odds).all() or (np.abs(odds) < 100).any():
        raise ValueError("Missing or invalid American price")
    absolute = np.abs(odds)
    return np.where(odds < 0, absolute / (absolute + 100), 100 / (absolute + 100))


def payout(odds: Any) -> np.ndarray:
    odds = np.asarray(odds, dtype=float)
    implied(odds)
    return np.where(odds < 0, 100 / np.abs(odds), odds / 100)


def blend_share(counts: pd.DataFrame, prior: pd.Series, week: int, team: str, weight: float) -> float:
    eligible = counts[(counts.week < week) & counts.team.eq(team)]
    denominator = float(eligible.valid.sum())
    if weight > 0 and denominator <= 0:
        raise ValueError("A weighted current-season pass share has no earlier-week sample")
    current = float(eligible.dropbacks.sum()) / denominator if denominator else float(prior[team])
    return float((1 - weight) * prior[team] + weight * current)


def pass_shares(units: pd.DataFrame, schedule: pd.DataFrame, cache: Path, output: Path) -> pd.DataFrame:
    captured = output / "pass_share_features.csv"
    if captured.exists():
        return pd.read_csv(captured)
    by_season = {}; full = {}; sources = []
    for season in range(int(units.season.min()) - 1, int(units.season.max()) + 1):
        path = cache / f"pbp_{season}.parquet"
        pbp = pd.read_parquet(path, columns=["season_type", "week", "game_id", "posteam", "epa", "qb_dropback", "rush_attempt"])
        eligible = schedule[(schedule.season == season) & schedule.game_type.eq("REG")].dropna(subset=["home_score", "away_score"])
        pbp = pbp[pbp.game_id.isin(set(eligible.game_id)) & pbp.season_type.eq("REG") & pbp.epa.notna()].copy()
        pbp["team"] = pbp.posteam.replace({"LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"})
        pbp["dropbacks"] = pd.to_numeric(pbp.qb_dropback, errors="coerce").fillna(0).gt(0).astype(int)
        pbp["valid"] = (pbp.dropbacks.gt(0) | pd.to_numeric(pbp.rush_attempt, errors="coerce").fillna(0).gt(0)).astype(int)
        pbp = pbp[pbp.valid.eq(1) & pbp.team.notna()]
        counts = pbp.groupby(["week", "team"], as_index=False)[["dropbacks", "valid"]].sum()
        by_season[season] = counts
        totals = counts.groupby("team")[["dropbacks", "valid"]].sum()
        full[season] = totals.dropbacks / totals.valid
        sources.append({"season": season, "file": str(path), "sha256": sha(path)})
    unique = units[["season", "week", "team", "rating_current_weight"]].drop_duplicates()
    rows = []
    for row in unique.itertuples():
        rows.append({"season": row.season, "week": row.week, "team": row.team,
                     "pass_share": blend_share(by_season[row.season], full[row.season - 1], int(row.week), row.team, row.rating_current_weight)})
    result = pd.DataFrame(rows)
    if result.duplicated(["season", "week", "team"]).any() or not result.pass_share.between(0, 1).all():
        raise ValueError("Invalid blended pass-share snapshot")
    result.to_csv(captured, index=False)
    (output / "pass_share_manifest.json").write_text(json.dumps(sources, indent=2))
    return result


def features_from_units(units: pd.DataFrame, schedule: pd.DataFrame, shares: pd.DataFrame) -> pd.DataFrame:
    if not (pd.to_datetime(units.max_current_input_date) < pd.to_datetime(units.input_cutoff)).all():
        raise ValueError("Pregame input chronology violation")
    if units.duplicated(["game_id", "side"]).any():
        raise ValueError("Duplicated game-side inputs")
    home = units[units.side.eq("home")].set_index("game_id")
    away = units[units.side.eq("away")].set_index("game_id").reindex(home.index)
    if len(home) * 2 != len(units) or away.team.isna().any():
        raise ValueError("Incomplete home/away pair")
    games = home[["season", "week", "gameday", "market_margin", "market_total"]].copy()
    games["home_team"] = home.team; games["away_team"] = away.team
    games["actual_margin"] = home.points - away.points
    games["actual_residual"] = games.actual_margin - games.market_margin
    games["home_cover"] = np.where(games.actual_residual > 0, 1.0, np.where(games.actual_residual < 0, 0.0, np.nan))
    games["production_margin"] = home.production_core - away.production_core
    source = schedule.set_index("game_id").reindex(games.index)
    if not np.allclose(source.spread_line, games.market_margin, equal_nan=False):
        raise ValueError("Market-line sign/join differs from the frozen source")
    if not np.allclose(source.home_score - source.away_score, games.actual_margin):
        raise ValueError("Official outcomes differ from the captured source")
    games["home_odds"] = pd.to_numeric(source.home_spread_odds)
    games["away_odds"] = pd.to_numeric(source.away_spread_odds)
    h = implied(games.home_odds); a = implied(games.away_odds)
    games["market_probability"] = h / (h + a)
    games["market_logit"] = logit(games.market_probability)
    games["absolute_line"] = games.market_margin.abs()
    games["integer_line"] = np.isclose(games.market_margin, np.round(games.market_margin)).astype(float)
    games["key_three"] = games.absolute_line.eq(3).astype(float)
    games["key_seven"] = games.absolute_line.eq(7).astype(float)
    games["rest_edge"] = (pd.to_numeric(source.home_rest) - pd.to_numeric(source.away_rest)).clip(-14, 14)
    games["neutral"] = source.location.str.lower().eq("neutral").astype(float)
    games["season_weight"] = 0.5 * (home.rating_current_weight + away.rating_current_weight)
    for name in OFF:
        games[name + "_diff"] = home[name] - away[name]
    for name in DEF:
        # A row's defensive fields describe its opponent; reverse the pair to
        # get the home unit minus the away unit.
        games[name + "_diff"] = away[name] - home[name]
    joined_h = home.reset_index().merge(shares, on=["season", "week", "team"], validate="many_to_one").set_index("game_id").reindex(games.index)
    joined_a = away.reset_index().merge(shares, on=["season", "week", "team"], validate="many_to_one").set_index("game_id").reindex(games.index)
    ph = joined_h.pass_share; pa = joined_a.pass_share
    games["pass_share_diff"] = ph - pa
    games["pass_matchup_style"] = ph * (home.off_pass_epa - home.def_pass_epa_edge) - pa * (away.off_pass_epa - away.def_pass_epa_edge)
    games["rush_matchup_style"] = (1 - ph) * (home.off_rush_epa - home.def_rush_epa_edge) - (1 - pa) * (away.off_rush_epa - away.def_rush_epa_edge)
    pressure_h = ph * home.def_pressure_edge.clip(lower=0) * (home.off_sacks_allowed - 0.067).clip(lower=0)
    pressure_a = pa * away.def_pressure_edge.clip(lower=0) * (away.off_sacks_allowed - 0.067).clip(lower=0)
    games["pressure_vulnerability"] = pressure_a - pressure_h
    games["explosive_vulnerability"] = (home.off_explosive - 0.105).clip(lower=0) * (-home.def_explosive_edge).clip(lower=0) - (away.off_explosive - 0.105).clip(lower=0) * (-away.def_explosive_edge).clip(lower=0)
    games["red_zone_vulnerability"] = (home.off_red_zone - 0.56).clip(lower=0) * (-home.def_red_zone_edge).clip(lower=0) - (away.off_red_zone - 0.56).clip(lower=0) * (-away.def_red_zone_edge).clip(lower=0)
    if not np.isfinite(games[list(MATCHUP)].to_numpy(float)).all():
        raise ValueError("Nonfinite model inputs")
    return games.reset_index().sort_values(["season", "week", "game_id"]).reset_index(drop=True)


def family_features(family: str) -> tuple[str, ...]:
    return MARKET if family == "market_logit" else ADDITIVE if family == "stats_logit" else MATCHUP


def fit_base(train: pd.DataFrame, family: str, parameter: float) -> dict[str, Any]:
    names = family_features(family)
    if family == "residual_ridge":
        model = make_pipeline(StandardScaler(), Ridge(alpha=parameter)).fit(train[list(names)], train.actual_residual)
        sigma = float(train.actual_residual.std())
    else:
        decided = train.dropna(subset=["home_cover"])
        model = make_pipeline(StandardScaler(), LogisticRegression(C=parameter, max_iter=3000, random_state=20261121)).fit(decided[list(names)], decided.home_cover.astype(int))
        sigma = None
    return {"family": family, "parameter": parameter, "names": names, "model": model, "sigma": sigma}


def raw_probability(fitted: dict[str, Any], data: pd.DataFrame) -> np.ndarray:
    model = fitted["model"]; x = data[list(fitted["names"])]
    p = norm.cdf(model.predict(x) / fitted["sigma"]) if fitted["family"] == "residual_ridge" else model.predict_proba(x)[:, 1]
    return np.clip(p, EPS, 1 - EPS)


def fit_calibration(raw: np.ndarray, outcome: pd.Series) -> dict[str, float]:
    mask = outcome.notna().to_numpy(); x = logit(np.clip(np.asarray(raw)[mask], EPS, 1 - EPS)); y = outcome.to_numpy(float)[mask]
    def loss(theta: np.ndarray) -> float:
        a, b = theta; score = a + b * x
        return float(np.mean(np.logaddexp(0, score) - y * score) + 0.01 * (a * a + (b - 1) ** 2))
    result = minimize(loss, [0.0, 1.0], method="L-BFGS-B", bounds=[(-2, 2), (0, 5)], options={"ftol": 1e-12})
    if not result.success:
        raise RuntimeError(f"Calibration failed: {result.message}")
    return {"intercept": float(result.x[0]), "slope": float(result.x[1]), "games": int(mask.sum())}


def calibrate(raw: np.ndarray, calibration: dict[str, float]) -> np.ndarray:
    return np.clip(expit(calibration["intercept"] + calibration["slope"] * logit(np.clip(raw, EPS, 1 - EPS))), EPS, 1 - EPS)


def probability_metrics(data: pd.DataFrame, p: np.ndarray) -> dict[str, Any]:
    mask = data.home_cover.notna().to_numpy(); y = data.home_cover.to_numpy(float)[mask]; q = np.asarray(p)[mask]
    return {"games": len(data), "decided_games": int(mask.sum()), "pushes": int((~mask).sum()),
            "log_loss": float(log_loss(y, q, labels=[0, 1])), "brier": float(brier_score_loss(y, q)),
            "mean_home_probability": float(q.mean()), "actual_home_cover_rate": float(y.mean())}


def select_bets(data: pd.DataFrame, p: np.ndarray, threshold: float | None) -> pd.DataFrame:
    q = np.asarray(p, dtype=float)
    if q.shape != (len(data),) or not np.isfinite(q).all() or (q < 0).any() or (q > 1).any():
        raise ValueError("Invalid probability vector")
    h = q - implied(data.home_odds); a = 1 - q - implied(data.away_odds)
    pick_home = h >= a
    edge = np.maximum(h, a)
    selected = np.zeros(len(data), dtype=bool) if threshold is None else edge >= threshold - 1e-12
    result = data.loc[selected, ["game_id", "season", "week", "gameday", "home_team", "away_team", "actual_residual", "market_margin"]].copy()
    result["pick_home"] = pick_home[selected]
    result["probability"] = np.where(pick_home, q, 1 - q)[selected]
    result["probability_edge"] = edge[selected]
    result["odds"] = np.where(pick_home, data.home_odds, data.away_odds)[selected]
    result["handicap"] = np.where(pick_home, -data.market_margin, data.market_margin)[selected]
    signed = result.actual_residual.to_numpy(float) * np.where(result.pick_home, 1, -1)
    result["result"] = np.where(np.abs(signed) < 1e-8, "P", np.where(signed > 0, "W", "L"))
    result["units"] = np.where(result.result.eq("W"), payout(result.odds), np.where(result.result.eq("L"), -1, 0))
    return result


def economic_metrics(bets: pd.DataFrame) -> dict[str, Any]:
    counts = bets.result.value_counts() if len(bets) else {}
    w = int(counts.get("W", 0)); l = int(counts.get("L", 0)); p = int(counts.get("P", 0)); n = len(bets)
    units = float(bets.units.sum()) if n else 0.0
    return {"n": n, "wins": w, "losses": l, "pushes": p, "units": units,
            "win_rate_decided": w / (w + l) if w + l else None, "roi": units / n if n else None,
            "mean_probability": float(bets.probability.mean()) if n else None,
            "mean_probability_edge": float(bets.probability_edge.mean()) if n else None}


def week_bootstrap(data: pd.DataFrame, values: np.ndarray, statistic: str = "mean", repetitions: int = 5000) -> list[float]:
    frame = data[["season", "week"]].copy(); frame["value"] = np.asarray(values)
    grouped = frame.groupby(["season", "week"]).value.agg(["sum", "count"])
    if grouped.empty:
        return [None, None]
    rng = np.random.default_rng(20261121)
    sample = rng.integers(len(grouped), size=(repetitions, len(grouped)))
    sums = grouped["sum"].to_numpy()[sample].sum(axis=1)
    draws = sums / grouped["count"].to_numpy()[sample].sum(axis=1) if statistic == "mean" else sums
    return [float(v) for v in np.quantile(draws, [0.025, 0.975])]


def calibration_bins(data: pd.DataFrame, p: np.ndarray) -> list[dict[str, Any]]:
    frame = data[["home_cover"]].copy(); frame["p"] = p
    frame = frame.dropna()
    frame["bin"] = pd.cut(frame.p, [0, 0.4, 0.45, 0.5, 0.55, 0.6, 1.0], include_lowest=True)
    return [{"bin": str(key), "n": len(group), "mean_probability": float(group.p.mean()), "actual_rate": float(group.home_cover.mean())}
            for key, group in frame.groupby("bin", observed=True)]


def simple_side_baselines(data: pd.DataFrame) -> dict[str, Any]:
    answer = {}
    for name in ["home", "away", "favorite", "underdog"]:
        eligible = data if name in ["home", "away"] else data[data.market_margin.ne(0)]
        choose_home = np.ones(len(eligible), dtype=bool) if name == "home" else np.zeros(len(eligible), dtype=bool) if name == "away" else eligible.market_margin.to_numpy() > 0 if name == "favorite" else eligible.market_margin.to_numpy() < 0
        bets = select_bets(eligible, np.where(choose_home, 1.0, 0.0), 0.0)
        # These are forced-side benchmarks, not 100%-confidence forecasts.
        bets["probability"] = np.where(choose_home, eligible.market_probability, 1 - eligible.market_probability)
        bets["probability_edge"] = bets.probability.to_numpy() - implied(bets.odds)
        answer[name] = economic_metrics(bets)
    return answer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default="artifacts/nfl_team_weights")
    parser.add_argument("--output-dir", default="artifacts/nfl_market_edges")
    parser.add_argument("--pbp-cache", default="/workspace/scratch/d17b457ae013/nfl-pbp-cache")
    args = parser.parse_args()
    inputs = Path(args.input_dir); output = Path(args.output_dir); output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((inputs / "data_manifest.json").read_text())
    dataset = inputs / "data/pregame_team_dataset.csv"
    if sha(dataset) != manifest["dataset_sha256"]:
        raise ValueError("Input dataset hash mismatch")
    if sha(inputs / "data/games.csv") != manifest["sources"][0]["sha256"]:
        raise ValueError("Schedule snapshot hash mismatch")
    data_path = output / "game_features.csv"
    snapshot_path = output / "feature_snapshot_manifest.json"
    if data_path.exists():
        if snapshot_path.exists() and json.loads(snapshot_path.read_text())["sha256"] != sha(data_path):
            raise ValueError("Captured game-feature hash mismatch")
        data = pd.read_csv(data_path)
    else:
        units = pd.read_csv(dataset); schedule = pd.read_csv(inputs / "data/games.csv", low_memory=False)
        schedule = schedule[schedule.gameday <= manifest["as_of"]].copy()
        shares = pass_shares(units, schedule, Path(args.pbp_cache), output)
        data = features_from_units(units, schedule, shares); data.to_csv(data_path, index=False)
    if not snapshot_path.exists():
        snapshot_path.write_text(json.dumps({"sha256": sha(data_path), "input_dataset_sha256": sha(dataset)}, indent=2))
    train = data[data.season.isin([2021, 2022])]
    tune = data[data.season.eq(2023)]
    refit = data[data.season.isin([2021, 2022, 2023])]
    calibration = data[data.season.eq(2024) & data.week.le(9)]
    policy = data[data.season.eq(2024) & data.week.ge(10)]
    tuning = {}; chosen = {}; fitted = {}; calibrations = {}
    for family in FAMILIES:
        print("Fitting", family, flush=True)
        scores = []
        for parameter in PARAMETERS[family]:
            model = fit_base(train, family, parameter)
            scores.append({"parameter": parameter, **probability_metrics(tune, raw_probability(model, tune))})
        best = min(scores, key=lambda row: row["log_loss"])
        tuning[family] = scores; chosen[family] = best
        fitted[family] = fit_base(refit, family, best["parameter"])
        calibrations[family] = fit_calibration(raw_probability(fitted[family], calibration), calibration.home_cover)
    primary = min(chosen, key=lambda family: chosen[family]["log_loss"])
    q_policy = calibrate(raw_probability(fitted[primary], policy), calibrations[primary])
    policy_choices = [{"threshold": threshold, **economic_metrics(select_bets(policy, q_policy, threshold))} for threshold in THRESHOLDS]
    qualifying = [row for row in policy_choices if row["n"] >= 30 and row["units"] > 0]
    threshold = max(qualifying, key=lambda row: row["roi"])["threshold"] if qualifying else None
    results = {"as_of": manifest["as_of"], "input_sha256": sha(dataset), "feature_dataset_sha256": sha(data_path),
               "research_source_sha256": sha(Path(__file__)), "primary_family_selected_2023": primary,
               "selected_threshold_2024": threshold, "policy_candidates_2024": policy_choices,
               "tuning_2023": tuning, "chosen_parameters": chosen, "calibration_2024_first_nine_weeks": calibrations,
               "market_baseline_2023": probability_metrics(tune, tune.market_probability.to_numpy()),
               "splits": {"train_2021_22": len(train), "tune_2023": len(tune), "calibration_2024_weeks_1_9": len(calibration), "policy_2024_weeks_10_18": len(policy)},
               "feature_sets": {family: list(family_features(family)) for family in FAMILIES}, "evaluation": {}}
    model_details = {}
    for family, fit in fitted.items():
        scaler, model = fit["model"].steps[0][1], fit["model"].steps[1][1]
        model_details[family] = {"parameter": fit["parameter"], "features": list(fit["names"]),
                                 "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
                                 "coefficients": np.asarray(model.coef_).reshape(-1).tolist(),
                                 "intercept": float(np.asarray(model.intercept_).reshape(-1)[0]), "sigma": fit["sigma"],
                                 "calibration": calibrations[family]}
    (output / "frozen_models.json").write_text(json.dumps(model_details, indent=2))
    all_forecasts = []
    for season in [2025, 2026]:
        test = data[data.season.eq(season)].copy()
        forecast = test.copy(); forecast["prob_market"] = test.market_probability
        season_result = {"market_probability": probability_metrics(test, test.market_probability.to_numpy())}
        decided = test.home_cover.notna().to_numpy(); outcome = test.home_cover.to_numpy(float)[decided]
        market_loss = -(outcome * np.log(test.market_probability.to_numpy()[decided]) + (1 - outcome) * np.log1p(-test.market_probability.to_numpy()[decided]))
        for family in FAMILIES:
            raw = raw_probability(fitted[family], test); q = calibrate(raw, calibrations[family])
            forecast["prob_" + family] = q; forecast["raw_prob_" + family] = raw
            fixed = select_bets(test, q, 0.02)
            policy_bets = select_bets(test, q, threshold) if family == primary else None
            loss = -(outcome * np.log(q[decided]) + (1 - outcome) * np.log1p(-q[decided]))
            row = {"calibrated": probability_metrics(test, q), "raw": probability_metrics(test, raw),
                   "fixed_2pp_policy": economic_metrics(fixed), "calibration_bins": calibration_bins(test, q),
                   "log_loss_difference_minus_market": float(np.mean(loss - market_loss)),
                   "paired_week_log_loss_difference_95pct": week_bootstrap(test.loc[decided], loss - market_loss),
                   "fixed_2pp_roi_95pct": week_bootstrap(fixed, fixed.units.to_numpy()) if len(fixed) else [None, None]}
            fixed.to_csv(output / f"bets_{season}_{family}_fixed_2pp.csv", index=False)
            if policy_bets is not None:
                row["selected_policy"] = economic_metrics(policy_bets)
                row["selected_policy_roi_95pct"] = week_bootstrap(policy_bets, policy_bets.units.to_numpy()) if len(policy_bets) else [None, None]
                policy_bets.to_csv(output / f"bets_{season}_selected_policy.csv", index=False)
            season_result[family] = row
        results["evaluation"][str(season)] = season_result
        forecast.to_csv(output / f"forecasts_{season}.csv", index=False)
        all_forecasts.append(forecast)
        print("Evaluation", season, json.dumps(season_result), flush=True)
    (output / "results.json").write_text(json.dumps(results, indent=2, allow_nan=False))
    pooled = {}
    for family in FAMILIES:
        combined = pd.concat([pd.read_csv(output / f"bets_{season}_{family}_fixed_2pp.csv") for season in [2025, 2026]], ignore_index=True)
        pooled[family] = {**economic_metrics(combined), "roi_95pct": week_bootstrap(combined, combined.units.to_numpy()) if len(combined) else [None, None]}
    (output / "pooled_and_simple_baselines.json").write_text(json.dumps({"pooled_fixed_2pp": pooled,
        "simple_baselines": {str(season): simple_side_baselines(data[data.season.eq(season)]) for season in [2025, 2026]}}, indent=2, allow_nan=False))
    print(json.dumps({"primary": primary, "threshold": threshold, "splits": results["splits"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
