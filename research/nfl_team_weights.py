"""Research only: separate, rolling NFL offensive and defensive sensitivities.

No production installation, tracker updates, or pick publication occurs here.
The production rating definitions and actual sample-capped season curve are
reused. Team adjustments are estimated jointly from each scoring observation,
with separate offense and opposing-defense blocks. All parameters and scalers
are learned before the forecast season; within-season updates use earlier weeks.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pandas as pd
import requests
from scipy import sparse
from scipy.sparse.linalg import lsqr
from sklearn.linear_model import Ridge

logging.getLogger("streamlit").setLevel(logging.ERROR)
from builders import nfl_builder as nflb
from builders.nfl_game_regression import project_matchup

OFF_FEATURES = (
    "off_scoring", "off_pass_epa", "off_rush_epa", "off_success",
    "off_explosive", "off_turnover", "off_sacks_allowed", "off_red_zone", "off_pace",
)
DEF_FEATURES = (
    "def_scoring_allowed", "def_pass_epa_edge", "def_rush_epa_edge", "def_success_edge",
    "def_explosive_edge", "def_takeaway", "def_pressure_edge", "def_red_zone_edge", "def_pace",
)
FEATURES = OFF_FEATURES + DEF_FEATURES
TEAMS = tuple(nflb.NFL_TEAMS)
TEAM_INDEX = {team: i for i, team in enumerate(TEAMS)}
GROUP_FEATURES = {
    "intercept": ((), ()),
    "core": (("off_scoring", "off_success"), ("def_scoring_allowed", "def_success_edge")),
    "full": (OFF_FEATURES, DEF_FEATURES),
}
ALPHAS = (8.0, 32.0, 128.0)
GLOBAL_ALPHA = 10.0
DATA_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_schedules(cache: Path, as_of: date) -> tuple[dict[int, pd.DataFrame], dict[str, Any]]:
    path = cache / "games.csv"
    if not path.exists():
        response = requests.get(DATA_URL, timeout=60)
        response.raise_for_status()
        path.write_bytes(response.content)
    raw = pd.read_csv(path, low_memory=False)
    raw = raw[(raw.game_type == "REG") & (raw.gameday <= as_of.isoformat())].copy()
    # Unplayed, abandoned, and future games cannot enter either metrics or HFA.
    raw = raw.dropna(subset=["home_score", "away_score"])
    schedules = {}
    for season, frame in raw.groupby("season"):
        schedules[int(season)] = nflb._format_schedule(frame.copy(), int(season))
    return schedules, {"url": DATA_URL, "file": str(path), "sha256": sha256(path)}


def matchup(off: dict[str, Any], defense: dict[str, Any]) -> dict[str, float]:
    n = nflb._num
    return {
        "off_scoring": n(off.get("Points/Game"), 22.5),
        "off_pass_epa": n(off.get("Pass EPA/DB")),
        "off_rush_epa": n(off.get("Rush EPA/Play")),
        "off_success": n(off.get("Off Success Rate"), 0.43),
        "off_explosive": n(off.get("Explosive Rate"), 0.105),
        "off_turnover": n(off.get("Turnover Rate"), 0.024),
        "off_sacks_allowed": n(off.get("Sack Rate Allowed"), 0.067),
        "off_red_zone": n(off.get("Red Zone TD Rate"), 0.56),
        "off_pace": n(off.get("Pace"), 64),
        "def_scoring_allowed": n(defense.get("Points Allowed/Game"), 22.5),
        "def_pass_epa_edge": n(defense.get("Pass Def EPA Edge")),
        "def_rush_epa_edge": n(defense.get("Rush Def EPA Edge")),
        "def_success_edge": n(defense.get("Def Success Edge")),
        "def_explosive_edge": n(defense.get("Explosive Def Edge")),
        "def_takeaway": n(defense.get("Takeaway Rate"), 0.024),
        "def_pressure_edge": n(defense.get("Sack/Pressure Edge")),
        "def_red_zone_edge": n(defense.get("Red Zone Def Edge")),
        "def_pace": n(defense.get("Pace"), 64),
    }


def build_dataset(cache: Path, as_of: date, first_season: int = 2021) -> tuple[pd.DataFrame, dict[str, Any]]:
    schedules, schedule_source = read_schedules(cache, as_of)
    last_season = max(year for year in schedules if year <= as_of.year)
    pbp: dict[int, pd.DataFrame] = {}
    full: dict[int, pd.DataFrame] = {}
    sources = [schedule_source]
    for season in range(first_season - 1, last_season + 1):
        print(f"Loading play-by-play {season}", flush=True)
        frame = nflb._load_pbp_season(season)
        if frame.empty:
            raise RuntimeError(f"Missing real PBP for {season}: {nflb.st.session_state.get(f'nfl_pbp_error_{season}', '')}")
        eligible_ids = set(schedules[season]["Game ID"].astype(str))
        frame = frame[frame.game_id.astype(str).isin(eligible_ids)].copy()
        if frame.empty:
            raise RuntimeError(f"No completed-game PBP in {season}")
        pbp[season] = frame
        full[season] = nflb._season_team_metrics(frame, schedules[season])
        path = Path(nflb._pbp_cache_path(season))
        sources.append({"season": season, "file": str(path), "sha256": sha256(path),
                        "rows": len(frame), "games": frame.game_id.nunique(),
                        "url": f"https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet"})

    rows = []
    rating_rows = []
    coverage = []
    # HFA reads this fixed, completed-game snapshot only. No live storage calls.
    with patch.object(nflb, "_schedule_for_season", side_effect=lambda season, *a, **k: schedules.get(int(season), pd.DataFrame())):
        nflb._rolling_home_field_model.clear()
        for season in range(first_season, last_season + 1):
            schedule = schedules[season]
            for week in sorted(pd.to_numeric(schedule.Week).astype(int).unique()):
                games = schedule[pd.to_numeric(schedule.Week) == week]
                earliest_date = pd.to_datetime(games["Game Date"]).min().date()
                earlier = schedule[(pd.to_numeric(schedule.Week) < week) & (pd.to_datetime(schedule["Game Date"]).dt.date < earliest_date)].copy()
                current_pbp = pbp[season][pbp[season].game_id.astype(str).isin(set(earlier["Game ID"].astype(str)))]
                current = nflb._season_team_metrics(current_pbp, earlier, through_week=week - 1) if week > 1 else pd.DataFrame()
                if not earlier.empty:
                    missing = set(earlier["Game ID"].astype(str)) - set(current_pbp.game_id.astype(str))
                    if missing:
                        raise RuntimeError(f"PBP missing for prior completed games: {sorted(missing)}")
                ratings = nflb._blend_team_metrics(full[season - 1], current, season, week)
                by_team = {str(row.Team): row.to_dict() for _, row in ratings.iterrows()}
                counts = earlier["Away Team"].value_counts().add(earlier["Home Team"].value_counts(), fill_value=0).to_dict()
                max_input_date = pd.to_datetime(earlier["Game Date"]).max().date().isoformat() if len(earlier) else f"{season}-01-01"
                for team, rating in by_team.items():
                    rating_rows.append({"season": season, "week": week, "team": team,
                                        "completed_current_games": int(counts.get(team, 0)), **rating})
                for _, game in games.iterrows():
                    away_team = str(game["Away Team"]); home_team = str(game["Home Team"])
                    away = by_team[away_team]; home = by_team[home_team]
                    hfa = nflb._automatic_home_field_advantage(season, week, home_team, game)["value"]
                    # Historical observed weather is not a pregame forecast. Use
                    # neutral weather/personnel/manual overlays for every model.
                    baseline = project_matchup(away, home, {}, {}, {"home_field": hfa, "weather_total_adjustment": 0.0})
                    for side, team, opponent, rating, opposing, points, sign in [
                        ("away", away_team, home_team, away, home, float(game["Away Score"]), -1.0),
                        ("home", home_team, away_team, home, away, float(game["Home Score"]), 1.0),
                    ]:
                        rows.append({
                            "season": season, "week": week, "game_id": str(game["Game ID"]),
                            "gameday": str(game["Game Date"]), "input_cutoff": earliest_date.isoformat(),
                            "max_current_input_date": max_input_date,
                            "team": team, "opponent": opponent, "side": side,
                            "points": points, "target": points - sign * hfa / 2.0,
                            "hfa_offset": sign * hfa / 2.0,
                            "production_core": baseline[f"{side}_score"],
                            "market_margin": nflb._num(game.get("Spread Line"), np.nan),
                            "market_total": nflb._num(game.get("Total Line"), np.nan),
                            "current_games": int(counts.get(team, 0)),
                            "opponent_current_games": int(counts.get(opponent, 0)),
                            "rating_current_weight": float(rating["Current Season Weight"]),
                            **matchup(rating, opposing),
                        })
                print(f"Built {season} week {week}: {len(games)} games", flush=True)
            coverage.append({"season": season, "games": len(schedule)})
    data = pd.DataFrame(rows).sort_values(["season", "week", "game_id", "side"]).reset_index(drop=True)
    validate_dataset(data)
    pd.DataFrame(rating_rows).to_csv(cache / "weekly_ratings.csv", index=False)
    return data, {"as_of": as_of.isoformat(), "sources": sources, "coverage": coverage,
                  "forecast_policy": "Whole NFL week frozen before first game; earlier-week completed games only; neutral weather and personnel overlays"}


def validate_dataset(data: pd.DataFrame) -> None:
    if data.empty or data.duplicated(["game_id", "side"]).any():
        raise ValueError("Empty dataset or duplicated game-side")
    pairs = data.groupby("game_id")
    if not pairs.size().eq(2).all() or not pairs.side.nunique().eq(2).all():
        raise ValueError("Each game must have one home and one away observation")
    if not (pd.to_datetime(data.max_current_input_date) < pd.to_datetime(data.input_cutoff)).all():
        raise ValueError("Pregame input leakage")
    if not np.isfinite(data[list(FEATURES) + ["target"]].to_numpy(float)).all():
        raise ValueError("Missing or nonfinite regressors")
    if not set(data.team).issubset(TEAM_INDEX):
        raise ValueError("Unknown franchise code")
    expected = np.array([nflb._season_weight(w, g) for w, g in zip(data.week, data.current_games)])
    if not np.allclose(data.rating_current_weight, expected, atol=1e-4):
        raise ValueError("Season weighting differs from production")


@dataclass
class LeagueFit:
    mean: np.ndarray
    scale: np.ndarray
    intercept: float
    coefficients: np.ndarray

    @classmethod
    def fit(cls, frame: pd.DataFrame) -> "LeagueFit":
        raw = frame[list(FEATURES)].to_numpy(float)
        mean = raw.mean(axis=0); scale = raw.std(axis=0)
        scale[scale < 1e-8] = 1.0
        model = Ridge(alpha=GLOBAL_ALPHA).fit((raw - mean) / scale, frame.target.to_numpy(float))
        return cls(mean, scale, float(model.intercept_), model.coef_.copy())

    def x(self, frame: pd.DataFrame) -> np.ndarray:
        return (frame[list(FEATURES)].to_numpy(float) - self.mean) / self.scale

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return self.intercept + self.x(frame) @ self.coefficients


def profile_basis(frame: pd.DataFrame, league: LeagueFit, kind: str, role: int = 0) -> np.ndarray:
    indices = [FEATURES.index(feature) for feature in GROUP_FEATURES[kind][role]]
    return np.column_stack([np.ones(len(frame)), league.x(frame)[:, indices]])


def joint_design(frame: pd.DataFrame, basis: tuple[np.ndarray, np.ndarray] | np.ndarray) -> sparse.csr_matrix:
    if isinstance(basis, np.ndarray):
        basis = (basis, basis)
    n, q = basis[0].shape
    if basis[1].shape != (n, q):
        raise ValueError("Offense and defense basis dimensions disagree")
    # Every observed score enters once, with one offense and one defense block.
    offense = np.array([TEAM_INDEX[team] for team in frame.team])
    defense = np.array([TEAM_INDEX[team] for team in frame.opponent]) + len(TEAMS)
    columns = np.concatenate([(offense[:, None] * q + np.arange(q)).ravel(),
                              (defense[:, None] * q + np.arange(q)).ravel()])
    row_indices = np.tile(np.repeat(np.arange(n), q), 2)
    values = np.concatenate([basis[0].ravel(), basis[1].ravel()])
    return sparse.csr_matrix((values, (row_indices, columns)), shape=(n, 2 * len(TEAMS) * q))


def fit_profiles(frame: pd.DataFrame, league: LeagueFit, kind: str, alpha: float) -> np.ndarray:
    q = 1 + len(GROUP_FEATURES[kind][0])
    if frame.empty:
        return np.zeros((2, len(TEAMS), q))
    basis = tuple(profile_basis(frame, league, kind, role) for role in [0, 1])
    design = joint_design(frame, basis)
    residual = frame.target.to_numpy(float) - league.predict(frame)
    solved = lsqr(design, residual, damp=np.sqrt(alpha), atol=1e-9, btol=1e-9, iter_lim=5000)
    if solved[1] not in (0, 1, 2, 4, 5):
        raise RuntimeError(f"Profile solver failed: {solved[1]}")
    return solved[0].reshape(2, len(TEAMS), q)


def season_weights(frame: pd.DataFrame, column: str) -> np.ndarray:
    return np.array([nflb._season_weight(int(w), int(g)) for w, g in zip(frame.week, frame[column])])


def rolling_profile_prediction(frame: pd.DataFrame, league: LeagueFit, kind: str,
                               prior: np.ndarray, current: np.ndarray) -> np.ndarray:
    answer = league.predict(frame)
    for role, team_column, games_column in [(0, "team", "current_games"), (1, "opponent", "opponent_current_games")]:
        basis = profile_basis(frame, league, kind, role)
        indices = np.array([TEAM_INDEX[team] for team in frame[team_column]])
        weights = season_weights(frame, games_column)
        profiles = prior[role, indices] * (1 - weights[:, None]) + current[role, indices] * weights[:, None]
        answer += np.einsum("ij,ij->i", basis, profiles)
    return answer


def independent_profiles(frame: pd.DataFrame, league: LeagueFit) -> tuple[np.ndarray, np.ndarray]:
    residual = frame.target.to_numpy(float) - league.predict(frame)
    coefficients = np.zeros((2, len(TEAMS), 1 + len(OFF_FEATURES)))
    ranks = np.zeros((2, len(TEAMS)), dtype=int)
    for role, column in [(0, "team"), (1, "opponent")]:
        basis = profile_basis(frame, league, "full", role)
        for team, idx in TEAM_INDEX.items():
            mask = frame[column].eq(team).to_numpy()
            if mask.any():
                coefficients[role, idx], _, ranks[role, idx], _ = np.linalg.lstsq(basis[mask], residual[mask], rcond=1e-8)
    return coefficients, ranks


def independent_prediction(frame: pd.DataFrame, league: LeagueFit, prior: np.ndarray, current: np.ndarray) -> np.ndarray:
    # Independent offense and defense regressions each predict the full residual;
    # average them, rather than attributing the same scoring residual twice.
    return league.predict(frame) + 0.5 * (rolling_profile_prediction(frame, league, "full", prior, current) - league.predict(frame))


def bound_predictions(frame: pd.DataFrame, base: np.ndarray) -> np.ndarray:
    scores = np.clip(base + frame.hfa_offset.to_numpy(float), 6.0, 48.0)
    result = pd.Series(scores, index=frame.index)
    for _, pair in frame.groupby("game_id", sort=False):
        if len(pair) != 2:
            raise ValueError("Incomplete forecast pair")
        home = pair.index[pair.side.eq("home")][0]; away = pair.index[pair.side.eq("away")][0]
        total = np.clip(result.loc[home] + result.loc[away], 25.0, 75.0)
        margin = np.clip(result.loc[home] - result.loc[away], -31.0, 31.0)
        result.loc[home] = (total + margin) / 2.0
        result.loc[away] = (total - margin) / 2.0
    return result.to_numpy(float)


def replay(data: pd.DataFrame, season: int, configurations: list[tuple[str, float]],
           include_independent: bool = True) -> tuple[pd.DataFrame, dict[str, Any]]:
    train = data[data.season < season]
    if train.empty:
        raise ValueError("No preseason league training data")
    league = LeagueFit.fit(train)
    prior_rows = train[train.season == season - 1]
    prior = {(kind, alpha): fit_profiles(prior_rows, league, kind, alpha) for kind, alpha in configurations}
    prior_independent, prior_rank = independent_profiles(prior_rows, league)
    outputs = []
    latest = {}
    season_rows = data[data.season == season]
    for week in sorted(season_rows.week.unique()):
        target = season_rows[season_rows.week == week].copy()
        current_rows = season_rows[season_rows.week < week]
        if len(current_rows) and current_rows.week.max() >= week:
            raise ValueError("Forecast includes its own week")
        target["production"] = target.production_core
        target["league"] = bound_predictions(target, league.predict(target))
        for kind, alpha in configurations:
            current = fit_profiles(current_rows, league, kind, alpha)
            column = f"{kind}_{alpha:g}"
            target[column] = bound_predictions(target, rolling_profile_prediction(target, league, kind, prior[(kind, alpha)], current))
            latest[column] = {"prior": prior[(kind, alpha)], "current": current, "kind": kind, "alpha": alpha}
        if include_independent:
            current_independent, ranks = independent_profiles(current_rows, league)
            target["independent"] = bound_predictions(target, independent_prediction(target, league, prior_independent, current_independent))
            latest["independent_ranks"] = ranks
        outputs.append(target)
    latest.update({"league": league, "week": int(season_rows.week.max()), "prior_ranks": prior_rank})
    return pd.concat(outputs).sort_index(), latest


def game_predictions(frame: pd.DataFrame, model: str) -> pd.DataFrame:
    home = frame[frame.side == "home"].set_index("game_id")
    away = frame[frame.side == "away"].set_index("game_id").reindex(home.index)
    return pd.DataFrame({"season": home.season, "week": home.week, "gameday": home.gameday,
                         "actual_margin": home.points - away.points, "actual_total": home.points + away.points,
                         "predicted_margin": home[model] - away[model], "predicted_total": home[model] + away[model],
                         "market_margin": home.market_margin, "market_total": home.market_total})


def metrics(frame: pd.DataFrame, model: str) -> dict[str, Any]:
    games = game_predictions(frame, model)
    result: dict[str, Any] = {"games": len(games), "team_score_mae": float(np.abs(frame.points - frame[model]).mean())}
    for target in ["margin", "total"]:
        error = games[f"predicted_{target}"] - games[f"actual_{target}"]
        result[f"{target}_mae"] = float(np.abs(error).mean())
        result[f"{target}_rmse"] = float(np.sqrt(np.square(error).mean()))
        result[f"{target}_bias"] = float(error.mean())
    result["selection_loss"] = 0.5 * (result["margin_mae"] + result["total_mae"])
    return result


def betting_record(games: pd.DataFrame, market: str, threshold: float) -> dict[str, Any]:
    clean = games.dropna(subset=[f"market_{market}"]).copy()
    edge = clean[f"predicted_{market}"] - clean[f"market_{market}"]
    clean = clean[edge.abs() >= threshold]
    edge = edge.reindex(clean.index)
    outcome = (clean[f"actual_{market}"] - clean[f"market_{market}"]) * np.sign(edge)
    wins = int((outcome > 1e-8).sum()); losses = int((outcome < -1e-8).sum()); pushes = int((outcome.abs() <= 1e-8).sum())
    profit = wins * (100.0 / 110.0) - losses
    return {"n": len(clean), "wins": wins, "losses": losses, "pushes": pushes,
            "win_rate_excluding_pushes": wins / (wins + losses) if wins + losses else None,
            "assumed_price": -110, "profit_units": profit,
            "roi": profit / len(clean) if len(clean) else None}


def paired_week_interval(frame: pd.DataFrame, candidate: str, baseline: str,
                         target: str, replicates: int = 5000) -> list[float]:
    a = game_predictions(frame, candidate); b = game_predictions(frame, baseline)
    differences = np.abs(a[f"predicted_{target}"] - a[f"actual_{target}"]) - np.abs(b[f"predicted_{target}"] - b[f"actual_{target}"])
    weekly = differences.groupby(a.week).agg(["sum", "count"])
    rng = np.random.default_rng(20261121)
    indices = rng.integers(len(weekly), size=(replicates, len(weekly)))
    draws = weekly["sum"].to_numpy()[indices].sum(axis=1) / weekly["count"].to_numpy()[indices].sum(axis=1)
    return [float(x) for x in np.quantile(draws, [0.025, 0.975])]


def export_profiles(data: pd.DataFrame, latest: dict[str, Any], column: str, season: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    spec = latest[column]; league = latest["league"]; week = latest["week"]
    previous = data[(data.season == season - 1)]
    current_rows = data[(data.season == season) & (data.week < week)]
    counts = current_rows.team.value_counts()
    rows = []; summaries = []
    for role, role_name in [(0, "offense"), (1, "defense")]:
        for team, team_index in TEAM_INDEX.items():
            games = int(counts.get(team, 0)); weight = nflb._season_weight(week, games)
            delta = spec["prior"][role, team_index] * (1 - weight) + spec["current"][role, team_index] * weight
            for j, feature in enumerate(["intercept"] + list(GROUP_FEATURES[spec["kind"]][role])):
                feature_index = FEATURES.index(feature) if feature != "intercept" else None
                scale = league.scale[feature_index] if feature_index is not None else 1.0
                baseline = league.coefficients[feature_index] if feature_index is not None else league.intercept
                rows.append({"season": season, "projection_week": week, "team": team, "unit": role_name,
                             "feature": feature, "current_games": games,
                             "prior_games": int(previous.team.eq(team).sum()),
                             "current_weight": weight, "prior_weight": 1 - weight,
                             "league_coefficient_standardized": float(baseline),
                             "team_adjustment_standardized": float(delta[j]),
                             "unit_coefficient_standardized": float(baseline + delta[j]) if feature_index is not None else float(delta[j]),
                             "unit_coefficient_raw_units": float((baseline + delta[j]) / scale) if feature_index is not None else float(delta[j]),
                             "team_adjustment_raw_units": float(delta[j] / scale),
                             "feature_mean": float(league.mean[feature_index]) if feature_index is not None else 0.0,
                             "feature_scale": float(scale),
                             "prior_adjustment_standardized": float(spec["prior"][role, team_index, j]),
                             "current_adjustment_standardized": float(spec["current"][role, team_index, j])})
            slopes = delta[1:]
            top = np.argsort(np.abs(slopes))[::-1][:2]
            names = GROUP_FEATURES[spec["kind"]][role]
            summaries.append({"team": team, "unit": role_name, "current_games": games, "current_weight": weight,
                              "intercept_adjustment": float(delta[0]),
                              "largest_sensitivity_1": names[top[0]] if len(top) else "",
                              "sensitivity_1_points_per_sd": float(slopes[top[0]]) if len(top) else 0.0,
                              "largest_sensitivity_2": names[top[1]] if len(top) > 1 else "",
                              "sensitivity_2_points_per_sd": float(slopes[top[1]]) if len(top) > 1 else 0.0})
    return pd.DataFrame(rows), pd.DataFrame(summaries)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default="2026-11-21")
    parser.add_argument("--output-dir", default="artifacts/nfl_team_weights")
    parser.add_argument("--rebuild-data", action="store_true")
    args = parser.parse_args()
    output = Path(args.output_dir); output.mkdir(parents=True, exist_ok=True)
    cache = output / "data"; cache.mkdir(exist_ok=True)
    dataset_path = cache / "pregame_team_dataset.csv"
    manifest_path = output / "data_manifest.json"
    if dataset_path.exists() and not args.rebuild_data:
        data = pd.read_csv(dataset_path)
        manifest = json.loads(manifest_path.read_text())
        if manifest["as_of"] != args.as_of:
            raise ValueError("Cached data has a different as-of date; use --rebuild-data")
        if manifest.get("dataset_sha256") and sha256(dataset_path) != manifest["dataset_sha256"]:
            raise ValueError("Dataset hash differs from the captured input snapshot")
        validate_dataset(data)
    else:
        data, manifest = build_dataset(cache, date.fromisoformat(args.as_of))
        data.to_csv(dataset_path, index=False)
    manifest.update({"dataset_sha256": sha256(dataset_path), "feature_columns": list(FEATURES),
                     "research_source_sha256": sha256(Path(__file__)),
                     "production_builder_sha256": sha256(Path(nflb.__file__)),
                     "production_regression_sha256": sha256(Path(project_matchup.__code__.co_filename))})
    manifest_path.write_text(json.dumps(manifest, indent=2))

    configurations = [(kind, alpha) for kind in GROUP_FEATURES for alpha in ALPHAS]
    validation, _ = replay(data, 2024, configurations, include_independent=False)
    losses = {"league": metrics(validation, "league")}
    for kind, alpha in configurations:
        column = f"{kind}_{alpha:g}"; losses[column] = metrics(validation, column)
    selected = min(losses, key=lambda key: losses[key]["selection_loss"])
    best_full = min((f"full_{alpha:g}" for alpha in ALPHAS), key=lambda key: losses[key]["selection_loss"])
    best_intercept = min((f"intercept_{alpha:g}" for alpha in ALPHAS), key=lambda key: losses[key]["selection_loss"])
    selected_columns = list(dict.fromkeys([selected, best_full, best_intercept]))
    test_configurations = [(kind, alpha) for kind, alpha in configurations if f"{kind}_{alpha:g}" in selected_columns]
    results: dict[str, Any] = {
        "as_of": args.as_of, "selected_on_2024": selected, "best_full_on_2024": best_full,
        "best_intercept_on_2024": best_intercept, "validation_2024": losses, "evaluation": {},
        "method": {"features": list(FEATURES), "penalty_grid": list(ALPHAS), "global_ridge_alpha": GLOBAL_ALPHA,
                   "selection_metric": "mean of margin MAE and total MAE",
                   "global_training": "All dataset seasons before the forecast season; standardizer fitted there only",
                   "team_prior": "Prior-season profile estimated jointly with ridge toward league weights",
                   "team_current": "Current-season profile from earlier weeks, blended using exact production _season_weight(week, completed_games)",
                   "offense_defense": "Separate own-offense and opposing-defense inputs and profile blocks, jointly fit; each unit coefficient = its league coefficient + its team adjustment; each observed score enters once",
                   "independent": "Separate unpenalized offense and defense residual regressions; predictions averaged; diagnostic only",
                   "production_comparison": "Active frozen v4 regression core, rolling HFA; neutral weather/personnel/manual overlays for all models",
                   "lines": "nflverse retrospective market lines; historical price availability time not verified; -110 assumption; no live grading filters",
                   "inference": "Predictive associations, not causal effects; paired week bootstrap intervals are approximate"},
    }
    latest = None
    all_predictions = []
    for season in sorted(year for year in data.season.unique() if year >= 2025):
        predictions, latest = replay(data, int(season), test_configurations)
        model_columns = list(dict.fromkeys(["production", "league", "independent"] + selected_columns))
        assessment = {}
        for model in model_columns:
            assessment[model] = metrics(predictions, model)
            games = game_predictions(predictions, model)
            assessment[model]["betting"] = {f"{market}_{threshold:g}": betting_record(games, market, threshold)
                                            for market, thresholds in [("margin", [1.5, 2.5, 3.5]), ("total", [1.75, 3.0, 4.0])] for threshold in thresholds}
        assessment["paired_full_minus_league_95pct"] = {target: paired_week_interval(predictions, best_full, "league", target) for target in ["margin", "total"]}
        assessment["paired_full_minus_production_95pct"] = {target: paired_week_interval(predictions, best_full, "production", target) for target in ["margin", "total"]}
        results["evaluation"][str(season)] = assessment
        predictions.to_csv(output / f"predictions_{season}.csv", index=False)
        all_predictions.append(predictions)
        print(f"Evaluation {season}: {json.dumps(assessment)}", flush=True)
    if latest is None:
        raise ValueError("No evaluation seasons")
    current_season = int(data.season.max())
    coefficients, summary = export_profiles(data, latest, best_full, current_season)
    coefficients.to_csv(output / "team_offense_defense_coefficients.csv", index=False)
    summary.to_csv(output / "team_offense_defense_summary.csv", index=False)
    results["profile_snapshot"] = {"season": current_season, "week": latest["week"], "model": best_full,
                                    "profiles": len(summary), "coefficient_rows": len(coefficients),
                                    "current_weight_min": float(summary.current_weight.min()),
                                    "current_weight_max": float(summary.current_weight.max())}
    (output / "results.json").write_text(json.dumps(results, indent=2, allow_nan=False))
    print(json.dumps({key: results[key] for key in ["selected_on_2024", "best_full_on_2024", "profile_snapshot"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
