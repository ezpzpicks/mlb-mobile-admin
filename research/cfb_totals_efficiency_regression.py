"""Leakage-safe CFB totals-specific regression.

This is deliberately different from the spread regression.  The fixed spread
model contributes only game-script information; total scoring is modeled from
pace/possessions and offensive/defensive scoring efficiency.

Protocol
--------
* Discovery/training: 2021-2023
* Validation/model + ridge selection: 2024
* Untouched holdout: 2025
* Sportsbook spread/total are NEVER predictors.
* Archived market total is evaluation-only.
* Current-season PBP features use prior weeks only.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import math
import os

import numpy as np
import pandas as pd

os.environ.setdefault("EZPZ_CFB_ALLOW_BLOCKING_OPEN_DATA", "1")
os.environ.setdefault("EZPZ_CFB_CACHE_SECONDS", "86400")

from builders import cfb_builder as cfb  # noqa: E402
from builders import cfb_game_regression as spread_reg  # noqa: E402

RESULTS_DIR = Path("research/results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON = RESULTS_DIR / "cfb_totals_efficiency_regression_results.json"
RESULT_MD = RESULTS_DIR / "CFB_TOTALS_EFFICIENCY_REGRESSION.md"

TRAIN_SEASONS = (2021, 2022, 2023)
VALIDATION_SEASON = 2024
HOLDOUT_SEASON = 2025
HFA = 2.0

METRIC_DEFAULTS = {
    "Possessions Per Game": 12.0,
    "Plays Per Game": 69.0,
    "EPA/PPA Offense": 0.0,
    "EPA/PPA Defense Raw": 0.0,
    "Success Rate Offense": 0.42,
    "Success Rate Defense Raw": 0.42,
    "Pass EPA/PPA": 0.0,
    "Pass Defense Raw": 0.0,
    "Rush EPA/PPA": 0.0,
    "Rush Defense Raw": 0.0,
    "Explosiveness Offense": 0.12,
    "Explosiveness Defense Raw": 0.12,
    "Finishing Drives Offense": 4.2,
    "Finishing Drives Defense Raw": 4.2,
    "Third Down Rate": 0.40,
    "Third Down Defense Raw": 0.40,
    "Red Zone TD Rate": 0.62,
    "Red Zone Defense Raw": 0.62,
    "Turnover Rate": 0.02,
    "Takeaway Rate": 0.02,
    "Sack Rate Allowed": 0.065,
    "Sack Rate Created": 0.065,
    "Yards Per Play": 5.7,
    "Yards Per Play Allowed": 5.7,
    "Line Yards Offense": 2.7,
    "Line Yards Defense Raw": 2.7,
    "Havoc Allowed": 0.16,
    "Havoc Created": 0.16,
}

BASE_FEATURES = [
    "structural_total",
    "spread_score_sum",
    "expected_possessions",
    "expected_combined_ppd",
    "abs_spread_margin",
    "abs_spread_margin_sq",
    "week",
    "neutral",
]

EFFICIENCY_FEATURES = [
    "epa_matchup",
    "pass_ppa_matchup",
    "rush_ppa_matchup",
    "success_matchup",
    "explosive_matchup",
    "finishing_matchup",
    "third_down_matchup",
    "red_zone_matchup",
    "ypp_matchup",
    "turnover_pressure",
    "sack_pressure",
    "havoc_pressure",
    "line_yards_matchup",
    "plays_per_possession",
]

INTERACTION_FEATURES = [
    "poss_x_epa",
    "poss_x_success",
    "poss_x_explosive",
    "poss_x_finishing",
    "poss_x_ypp",
    "margin_x_possessions",
]

VARIANTS = {
    "pace_ppd": BASE_FEATURES,
    "pace_efficiency": BASE_FEATURES + EFFICIENCY_FEATURES,
    "pace_efficiency_interactions": BASE_FEATURES + EFFICIENCY_FEATURES + INTERACTION_FEATURES,
}

ALPHAS = [0.25, 1.0, 4.0, 16.0, 64.0, 256.0, 1024.0]


def num(value: Any, default: float = np.nan) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "completed"}


def first_present(frame: pd.DataFrame, names: list[str], default: Any = np.nan) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return frame[name]
    return pd.Series(default, index=frame.index)


def first_valid(series: pd.Series) -> Any:
    values = series.dropna()
    if values.empty:
        return np.nan
    if values.dtype == object or pd.api.types.is_string_dtype(values):
        values = values[values.astype(str).str.strip() != ""]
        if values.empty:
            return np.nan
    return values.iloc[0]


def last_valid(series: pd.Series) -> Any:
    values = series.dropna()
    if values.empty:
        return np.nan
    if values.dtype == object or pd.api.types.is_string_dtype(values):
        values = values[values.astype(str).str.strip() != ""]
        if values.empty:
            return np.nan
    return values.iloc[-1]


def load_game_columns(season: int) -> pd.DataFrame:
    path = cfb._download_open_asset("cfbfastR_cfb_pbp", season, ("play_by_play", "pbp"))
    if path is None or not Path(path).exists():
        raise RuntimeError(f"SportsDataverse PBP unavailable for {season}")
    aliases = {
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
        "total": ("overUnder", "over_under", "total"),
    }
    frame = cfb._read_open_parquet(Path(path), aliases)
    if frame is None or frame.empty:
        raise RuntimeError(f"No usable PBP game columns for {season}")
    return frame


def games_from_pbp(season: int) -> pd.DataFrame:
    pbp = load_game_columns(season).copy()
    pbp["game_id"] = first_present(pbp, ["game_id"]).astype(str)
    pbp = pbp[~pbp["game_id"].isin(["", "nan", "None"])].copy()
    if "sequence" in pbp.columns:
        pbp["_sequence"] = pd.to_numeric(pbp["sequence"], errors="coerce")
        pbp = pbp.sort_values(["game_id", "_sequence"], kind="stable")

    rows: list[dict[str, Any]] = []
    for game_id, group in pbp.groupby("game_id", sort=False):
        home = cfb._normalize_team(first_valid(first_present(group, ["home_team"], "")))
        away = cfb._normalize_team(first_valid(first_present(group, ["away_team"], "")))
        week = int(num(first_valid(first_present(group, ["week"])), 0))
        home_score = num(last_valid(pd.to_numeric(first_present(group, ["home_score"]), errors="coerce")), np.nan)
        away_score = num(last_valid(pd.to_numeric(first_present(group, ["away_score"]), errors="coerce")), np.nan)
        if not home or not away or week <= 0 or not math.isfinite(home_score) or not math.isfinite(away_score):
            continue
        completed_value = last_valid(first_present(group, ["completed"], True))
        if "completed" in group.columns and str(completed_value).strip().lower() in {"false", "0", "no"}:
            continue
        neutral = truthy(first_valid(first_present(group, ["neutral"], False)))
        home_spread = num(first_valid(pd.to_numeric(first_present(group, ["home_team_spread"]), errors="coerce")), np.nan)
        raw_spread = num(first_valid(pd.to_numeric(first_present(group, ["game_spread"]), errors="coerce")), np.nan)
        home_fav = truthy(first_valid(first_present(group, ["home_favorite"], False)))
        if not math.isfinite(home_spread) and math.isfinite(raw_spread):
            home_spread = -abs(raw_spread) if home_fav else abs(raw_spread)
        total_line = num(first_valid(pd.to_numeric(first_present(group, ["total"]), errors="coerce")), np.nan)
        rows.append({
            "season": int(season), "week": week, "game_id": str(game_id),
            "away_team": away, "home_team": home, "neutral": neutral,
            "away_score": away_score, "home_score": home_score,
            "actual_total": away_score + home_score,
            "actual_margin": home_score - away_score,
            "market_total": total_line, "market_home_spread": home_spread,
        })
    out = pd.DataFrame(rows)
    if out.empty:
        espn = cfb._parse_games(cfb._espn_games_payload(int(season)), int(season))
        if espn is not None and not espn.empty:
            mask = espn["Completed"].map(cfb._bool)
            mask &= pd.to_numeric(espn["Away Score"], errors="coerce").notna()
            mask &= pd.to_numeric(espn["Home Score"], errors="coerce").notna()
            mask &= pd.to_numeric(espn["Week"], errors="coerce").fillna(0).gt(0)
            espn = espn.loc[mask].copy()
            out = pd.DataFrame({
                "season": int(season),
                "week": pd.to_numeric(espn["Week"], errors="coerce").astype(int),
                "game_id": espn["Game ID"].astype(str),
                "away_team": espn["Away Team"].map(cfb._normalize_team),
                "home_team": espn["Home Team"].map(cfb._normalize_team),
                "neutral": espn["Neutral Site"].map(cfb._bool),
                "away_score": pd.to_numeric(espn["Away Score"], errors="coerce"),
                "home_score": pd.to_numeric(espn["Home Score"], errors="coerce"),
                "market_total": pd.to_numeric(espn.get("Total"), errors="coerce"),
                "market_home_spread": pd.to_numeric(espn.get("Home Spread"), errors="coerce"),
            })
            out["actual_total"] = out["away_score"] + out["home_score"]
            out["actual_margin"] = out["home_score"] - out["away_score"]
        if out.empty:
            raise RuntimeError(f"No completed games reconstructed for {season}")
    # Evaluation-only market enrichment. PBP line fields are not stable across
    # SportsDataverse seasons, so fill missing archived totals/spreads from the
    # repo's ESPN historical schedule feed. These fields never enter predictors.
    if not out.empty:
        try:
            espn_market = cfb._parse_games(cfb._espn_games_payload(int(season)), int(season))
        except Exception as exc:
            print(f"ESPN market enrichment failed for {season}: {exc}")
            espn_market = pd.DataFrame()
        if espn_market is not None and not espn_market.empty:
            market = pd.DataFrame({
                "_game_id": espn_market["Game ID"].astype(str),
                "_week": pd.to_numeric(espn_market["Week"], errors="coerce"),
                "_away": espn_market["Away Team"].map(cfb._normalize_team),
                "_home": espn_market["Home Team"].map(cfb._normalize_team),
                "_espn_total": pd.to_numeric(espn_market.get("Total"), errors="coerce"),
                "_espn_spread": pd.to_numeric(espn_market.get("Home Spread"), errors="coerce"),
            })
            market.loc[market["_espn_total"] <= 0, "_espn_total"] = np.nan
            market = market.drop_duplicates("_game_id")
            total_by_id = market.set_index("_game_id")["_espn_total"]
            spread_by_id = market.set_index("_game_id")["_espn_spread"]
            out_ids = out["game_id"].astype(str)
            current_total = pd.to_numeric(out.get("market_total"), errors="coerce")
            current_spread = pd.to_numeric(out.get("market_home_spread"), errors="coerce")
            id_total = out_ids.map(total_by_id)
            id_spread = out_ids.map(spread_by_id)
            out["market_total"] = current_total.where(current_total > 0).fillna(id_total)
            out["market_home_spread"] = current_spread.fillna(id_spread)

            # Team/week fallback protects against historical ID formatting changes.
            market["_key"] = market["_week"].fillna(0).astype(int).astype(str) + "|" + market["_away"] + "|" + market["_home"]
            total_by_key = market.drop_duplicates("_key").set_index("_key")["_espn_total"]
            spread_by_key = market.drop_duplicates("_key").set_index("_key")["_espn_spread"]
            out_key = pd.to_numeric(out["week"], errors="coerce").fillna(0).astype(int).astype(str) + "|" + out["away_team"].astype(str) + "|" + out["home_team"].astype(str)
            out["market_total"] = pd.to_numeric(out["market_total"], errors="coerce").fillna(out_key.map(total_by_key))
            out["market_home_spread"] = pd.to_numeric(out["market_home_spread"], errors="coerce").fillna(out_key.map(spread_by_key))
        found = int(pd.to_numeric(out.get("market_total"), errors="coerce").notna().sum())
        print(f"{season}: archived market totals recovered for {found}/{len(out)} games")
    return out.sort_values(["week", "game_id"]).drop_duplicates("game_id").reset_index(drop=True)


@dataclass
class TeamStats:
    power: float = 0.0
    ppg: float = 28.0
    papg: float = 28.0
    games: int = 0


def team_summary(games: pd.DataFrame) -> dict[str, TeamStats]:
    if games is None or games.empty:
        return {}
    teams = sorted(set(games["away_team"].astype(str)) | set(games["home_team"].astype(str)))
    rows = games.to_dict("records")
    by_team = {team: [] for team in teams}
    for game in rows:
        by_team[str(game["away_team"])].append(game)
        by_team[str(game["home_team"])].append(game)
    power = {team: 0.0 for team in teams}
    for _ in range(30):
        updated = {}
        for team in teams:
            values = []
            for game in by_team[team]:
                away, home = str(game["away_team"]), str(game["home_team"])
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
    result = {}
    for team in teams:
        scored, allowed = [], []
        for game in by_team[team]:
            home_side = team == str(game["home_team"])
            scored.append(num(game["home_score"] if home_side else game["away_score"], 28.0))
            allowed.append(num(game["away_score"] if home_side else game["home_score"], 28.0))
        result[team] = TeamStats(power.get(team, 0.0), float(np.mean(scored)), float(np.mean(allowed)), len(scored))
    return result


def stat(stats: dict[str, TeamStats], team: str) -> TeamStats:
    return stats.get(team, TeamStats())


def blend_stats(prior: TeamStats, current: TeamStats) -> TeamStats:
    w = current.games / (current.games + 4.0) if current.games else 0.0
    return TeamStats(
        (1 - w) * prior.power + w * current.power,
        (1 - w) * prior.ppg + w * current.ppg,
        (1 - w) * prior.papg + w * current.papg,
        current.games,
    )


def spread_scores(away: str, home: str, neutral: bool,
                  prior_stats: dict[str, TeamStats], current_stats: dict[str, TeamStats]) -> tuple[float, float]:
    pa, ph = stat(prior_stats, away), stat(prior_stats, home)
    ca, ch = stat(current_stats, away), stat(current_stats, home)
    ba, bh = blend_stats(pa, ca), blend_stats(ph, ch)
    af = {
        "prior_own_ppg": pa.ppg,
        "prior_opp_papg": ph.papg,
        "prior_power_gap": pa.power - ph.power,
        "current_own_scoring_delta": ba.ppg - pa.ppg,
        "current_opp_allowed_delta": bh.papg - ph.papg,
        "current_power_delta": (ba.power - bh.power) - (pa.power - ph.power),
        "home_indicator": 0.0,
    }
    hf = {
        "prior_own_ppg": ph.ppg,
        "prior_opp_papg": pa.papg,
        "prior_power_gap": ph.power - pa.power,
        "current_own_scoring_delta": bh.ppg - ph.ppg,
        "current_opp_allowed_delta": ba.papg - pa.papg,
        "current_power_delta": (bh.power - ba.power) - (ph.power - pa.power),
        "home_indicator": 0.0 if neutral else 1.0,
    }
    def score(features: dict[str, float]) -> float:
        return float(spread_reg.TEAM_SCORE_INTERCEPT + sum(
            spread_reg.TEAM_SCORE_COEFFICIENTS[name] * num(features.get(name), 0.0)
            for name in spread_reg.TEAM_SCORE_COEFFICIENTS
        ))
    return score(af), score(hf)


def metric_lookup(frame: pd.DataFrame) -> dict[str, dict[str, float]]:
    if frame is None or frame.empty:
        return {}
    out: dict[str, dict[str, float]] = {}
    for _, row in frame.iterrows():
        team = cfb._normalize_team(row.get("Team"))
        if not team:
            continue
        out[team] = {name: num(row.get(name), default) for name, default in METRIC_DEFAULTS.items()}
        out[team]["Advanced Plays"] = num(row.get("Advanced Plays"), 0.0)
    return out


def metric_games(values: dict[str, float]) -> float:
    plays = num(values.get("Advanced Plays"), 0.0)
    ppg = num(values.get("Plays Per Game"), 69.0)
    return max(0.0, plays / max(1.0, ppg))


def blend_metric(prior: dict[str, float] | None, current: dict[str, float] | None, name: str) -> float:
    default = METRIC_DEFAULTS[name]
    p = num((prior or {}).get(name), default)
    c = num((current or {}).get(name), p)
    n = metric_games(current or {})
    w = n / (n + 4.0) if n > 0 else 0.0
    return (1.0 - w) * p + w * c


def matchup_mean(a_off: float, h_def: float, h_off: float, a_def: float) -> float:
    return float(np.mean([a_off, h_def, h_off, a_def]))


def build_feature_row(game: pd.Series, prior_stats: dict[str, TeamStats], current_stats: dict[str, TeamStats],
                      prior_metrics: dict[str, dict[str, float]], current_metrics: dict[str, dict[str, float]]) -> dict[str, Any]:
    away, home = str(game["away_team"]), str(game["home_team"])
    neutral = truthy(game.get("neutral", False))
    a_prev, h_prev = prior_metrics.get(away), prior_metrics.get(home)
    a_cur, h_cur = current_metrics.get(away), current_metrics.get(home)

    def am(name: str) -> float: return blend_metric(a_prev, a_cur, name)
    def hm(name: str) -> float: return blend_metric(h_prev, h_cur, name)

    away_spread_score, home_spread_score = spread_scores(away, home, neutral, prior_stats, current_stats)
    spread_margin = home_spread_score - away_spread_score
    spread_score_sum = home_spread_score + away_spread_score

    a_stats = blend_stats(stat(prior_stats, away), stat(current_stats, away))
    h_stats = blend_stats(stat(prior_stats, home), stat(current_stats, home))
    a_poss, h_poss = am("Possessions Per Game"), hm("Possessions Per Game")
    a_plays, h_plays = am("Plays Per Game"), hm("Plays Per Game")
    expected_possessions = 0.58 * ((a_poss + h_poss) / 2.0) + 0.42 * (((a_plays + h_plays) / 2.0) / 5.75)
    expected_possessions -= max(0.0, abs(spread_margin) - 17.0) * 0.015
    expected_possessions = float(np.clip(expected_possessions, 9.0, 16.5))

    a_off_ppd = a_stats.ppg / max(8.0, a_poss)
    h_off_ppd = h_stats.ppg / max(8.0, h_poss)
    a_def_ppd = a_stats.papg / max(8.0, a_poss)
    h_def_ppd = h_stats.papg / max(8.0, h_poss)
    away_match_ppd = 0.58 * a_off_ppd + 0.42 * h_def_ppd
    home_match_ppd = 0.58 * h_off_ppd + 0.42 * a_def_ppd
    combined_ppd = away_match_ppd + home_match_ppd
    structural_total = float(np.clip(expected_possessions * combined_ppd, 20.0, 100.0))

    epa_matchup = matchup_mean(am("EPA/PPA Offense"), hm("EPA/PPA Defense Raw"), hm("EPA/PPA Offense"), am("EPA/PPA Defense Raw"))
    pass_matchup = matchup_mean(am("Pass EPA/PPA"), hm("Pass Defense Raw"), hm("Pass EPA/PPA"), am("Pass Defense Raw"))
    rush_matchup = matchup_mean(am("Rush EPA/PPA"), hm("Rush Defense Raw"), hm("Rush EPA/PPA"), am("Rush Defense Raw"))
    success_matchup = matchup_mean(am("Success Rate Offense"), hm("Success Rate Defense Raw"), hm("Success Rate Offense"), am("Success Rate Defense Raw"))
    explosive_matchup = matchup_mean(am("Explosiveness Offense"), hm("Explosiveness Defense Raw"), hm("Explosiveness Offense"), am("Explosiveness Defense Raw"))
    finishing_matchup = matchup_mean(am("Finishing Drives Offense"), hm("Finishing Drives Defense Raw"), hm("Finishing Drives Offense"), am("Finishing Drives Defense Raw"))
    third_matchup = matchup_mean(am("Third Down Rate"), hm("Third Down Defense Raw"), hm("Third Down Rate"), am("Third Down Defense Raw"))
    rz_matchup = matchup_mean(am("Red Zone TD Rate"), hm("Red Zone Defense Raw"), hm("Red Zone TD Rate"), am("Red Zone Defense Raw"))
    ypp_matchup = matchup_mean(am("Yards Per Play"), hm("Yards Per Play Allowed"), hm("Yards Per Play"), am("Yards Per Play Allowed"))
    line_matchup = matchup_mean(am("Line Yards Offense"), hm("Line Yards Defense Raw"), hm("Line Yards Offense"), am("Line Yards Defense Raw"))
    turnover_pressure = float(np.mean([am("Turnover Rate"), hm("Turnover Rate"), am("Takeaway Rate"), hm("Takeaway Rate")]))
    sack_pressure = float(np.mean([am("Sack Rate Allowed"), hm("Sack Rate Allowed"), am("Sack Rate Created"), hm("Sack Rate Created")]))
    havoc_pressure = float(np.mean([am("Havoc Allowed"), hm("Havoc Allowed"), am("Havoc Created"), hm("Havoc Created")]))
    plays_per_possession = ((a_plays / max(8.0, a_poss)) + (h_plays / max(8.0, h_poss))) / 2.0

    row = {
        "season": int(game["season"]), "week": float(game["week"]), "game_id": str(game["game_id"]),
        "game": f"{away} @ {home}", "away_team": away, "home_team": home,
        "actual_total": num(game["actual_total"]), "market_total": num(game.get("market_total"), np.nan),
        "structural_total": structural_total, "spread_score_sum": spread_score_sum,
        "expected_possessions": expected_possessions, "expected_combined_ppd": combined_ppd,
        "abs_spread_margin": abs(spread_margin), "abs_spread_margin_sq": abs(spread_margin) ** 2,
        "neutral": 1.0 if neutral else 0.0,
        "epa_matchup": epa_matchup, "pass_ppa_matchup": pass_matchup, "rush_ppa_matchup": rush_matchup,
        "success_matchup": success_matchup, "explosive_matchup": explosive_matchup,
        "finishing_matchup": finishing_matchup, "third_down_matchup": third_matchup,
        "red_zone_matchup": rz_matchup, "ypp_matchup": ypp_matchup,
        "turnover_pressure": turnover_pressure, "sack_pressure": sack_pressure,
        "havoc_pressure": havoc_pressure, "line_yards_matchup": line_matchup,
        "plays_per_possession": plays_per_possession,
    }
    row.update({
        "poss_x_epa": expected_possessions * epa_matchup,
        "poss_x_success": expected_possessions * success_matchup,
        "poss_x_explosive": expected_possessions * explosive_matchup,
        "poss_x_finishing": expected_possessions * finishing_matchup,
        "poss_x_ypp": expected_possessions * ypp_matchup,
        "margin_x_possessions": abs(spread_margin) * expected_possessions,
    })
    return row


def build_dataset() -> pd.DataFrame:
    seasons_needed = range(min(TRAIN_SEASONS), HOLDOUT_SEASON + 1)
    games_cache = {season: games_from_pbp(season) for season in seasons_needed}
    rows: list[dict[str, Any]] = []
    original_open_pbp = cfb._open_pbp_frame

    for season in (*TRAIN_SEASONS, VALIDATION_SEASON, HOLDOUT_SEASON):
        games = games_cache[season]
        prior_games = games_cache.get(season - 1, pd.DataFrame())
        prior_stats = team_summary(prior_games)
        prior_pbp = original_open_pbp(season - 1) if season - 1 in games_cache else pd.DataFrame()
        current_pbp = original_open_pbp(season)

        def cached_open_pbp(year: int) -> pd.DataFrame:
            if int(year) == int(season):
                return current_pbp
            if int(year) == int(season - 1):
                return prior_pbp
            return original_open_pbp(year)

        cfb._open_pbp_frame = cached_open_pbp
        try:
            prior_metric_frame = cfb._pbp_team_metrics(season - 1, None) if not prior_pbp.empty else pd.DataFrame()
            prior_metrics = metric_lookup(prior_metric_frame)
            print(f"season {season}: {len(games)} games; prior advanced teams={len(prior_metrics)}")
            for week in sorted(pd.to_numeric(games["week"], errors="coerce").dropna().astype(int).unique()):
                before = games[games["week"] < int(week)]
                current_stats = team_summary(before)
                current_metrics = metric_lookup(cfb._pbp_team_metrics(season, int(week)))
                subset = games[games["week"] == int(week)]
                for _, game in subset.iterrows():
                    rows.append(build_feature_row(game, prior_stats, current_stats, prior_metrics, current_metrics))
                print(f"  week {week}: {len(subset)} games; advanced teams={len(current_metrics)}")
        finally:
            cfb._open_pbp_frame = original_open_pbp

    frame = pd.DataFrame(rows)
    all_features = sorted(set(sum(VARIANTS.values(), [])))
    for col in all_features + ["actual_total", "market_total"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["actual_total", "structural_total"]).reset_index(drop=True)
    return frame


def fit_ridge(frame: pd.DataFrame, features: list[str], alpha: float) -> dict[str, Any]:
    x = frame[features].astype(float).replace([np.inf, -np.inf], np.nan)
    means = x.mean(axis=0)
    x = x.fillna(means)
    stds = x.std(axis=0, ddof=0).replace(0.0, 1.0)
    z = ((x - means) / stds).to_numpy(float)
    y = (frame["actual_total"] - frame["structural_total"]).to_numpy(float)
    design = np.column_stack([np.ones(len(z)), z])
    penalty = np.eye(design.shape[1]) * float(alpha)
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    return {
        "features": list(features), "alpha": float(alpha), "intercept": float(beta[0]),
        "coef": {name: float(value) for name, value in zip(features, beta[1:])},
        "means": {name: float(means[name]) for name in features},
        "stds": {name: float(stds[name]) for name in features},
    }


def predict(frame: pd.DataFrame, model: dict[str, Any]) -> np.ndarray:
    cols = []
    for name in model["features"]:
        values = pd.to_numeric(frame[name], errors="coerce").to_numpy(float)
        values = np.where(np.isfinite(values), values, model["means"][name])
        cols.append((values - model["means"][name]) / (model["stds"][name] or 1.0))
    z = np.column_stack(cols)
    beta = np.array([model["coef"][name] for name in model["features"]], dtype=float)
    correction = float(model["intercept"]) + z @ beta
    return frame["structural_total"].to_numpy(float) + correction


def metrics(actual: np.ndarray, pred: np.ndarray) -> dict[str, Any]:
    err = pred - actual
    denom = float(np.sum((actual - actual.mean()) ** 2))
    return {
        "n": int(len(actual)),
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "bias": float(np.mean(err)),
        "r2": float(1.0 - np.sum(err ** 2) / denom) if denom > 0 else 0.0,
        "corr": float(np.corrcoef(actual, pred)[0, 1]) if len(actual) > 2 else 0.0,
    }


def evaluate(frame: pd.DataFrame, pred: np.ndarray) -> dict[str, Any]:
    actual = frame["actual_total"].to_numpy(float)
    out = {
        "spread_score_sum": metrics(actual, frame["spread_score_sum"].to_numpy(float)),
        "pace_ppd_structural": metrics(actual, frame["structural_total"].to_numpy(float)),
        "totals_regression": metrics(actual, pred),
    }
    mask = frame["market_total"].notna().to_numpy()
    if mask.any():
        out["market_total"] = metrics(actual[mask], frame.loc[mask, "market_total"].to_numpy(float))
    return out


def betting_table(frame: pd.DataFrame, pred: np.ndarray) -> list[dict[str, Any]]:
    line = frame["market_total"].to_numpy(float)
    actual = frame["actual_total"].to_numpy(float)
    edge = pred - line
    valid = np.isfinite(line)
    rows = []
    for threshold in range(1, 13):
        selected = valid & (np.abs(edge) >= threshold)
        idx = np.where(selected)[0]
        wins = losses = pushes = overs = unders = 0
        for j in idx:
            over = edge[j] > 0
            overs += int(over); unders += int(not over)
            if abs(actual[j] - line[j]) < 1e-9:
                pushes += 1
            elif (actual[j] > line[j]) == over:
                wins += 1
            else:
                losses += 1
        decisions = wins + losses
        wr = wins / decisions if decisions else None
        roi = ((wins * (100 / 110)) - losses) / decisions if decisions else None
        rows.append({"threshold": threshold, "bets": int(len(idx)), "wins": wins, "losses": losses,
                     "pushes": pushes, "win_rate": wr, "roi_at_-110": roi, "overs": overs, "unders": unders})
    return rows


def side_record(frame: pd.DataFrame, pred: np.ndarray, threshold: float) -> dict[str, Any]:
    line = frame["market_total"].to_numpy(float)
    actual = frame["actual_total"].to_numpy(float)
    edge = pred - line
    valid = np.isfinite(line) & (np.abs(edge) >= threshold)
    out = {}
    for label, over in (("over", True), ("under", False)):
        idx = np.where(valid & ((edge > 0) == over))[0]
        wins = losses = pushes = 0
        for j in idx:
            if abs(actual[j] - line[j]) < 1e-9:
                pushes += 1
            elif (actual[j] > line[j]) == over:
                wins += 1
            else:
                losses += 1
        decisions = wins + losses
        out[label] = {"bets": int(len(idx)), "wins": wins, "losses": losses, "pushes": pushes,
                      "win_rate": wins / decisions if decisions else None}
    return out


def choose_grades(validation: list[dict[str, Any]]) -> dict[str, int | None]:
    # Selected on 2024 only.  Minimum samples prevent tiny-edge overfitting.
    b = next((r["threshold"] for r in validation if r["bets"] >= 80 and r["win_rate"] is not None and r["win_rate"] >= 0.535), None)
    a = next((r["threshold"] for r in validation if r["bets"] >= 40 and r["win_rate"] is not None and r["win_rate"] >= 0.555 and (b is None or r["threshold"] >= b)), None)
    return {"B": b, "A": a}


def pct(value: Any) -> str:
    if value is None or not math.isfinite(num(value, np.nan)):
        return "—"
    return f"{100 * float(value):.1f}%"


def nan_to_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: nan_to_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [nan_to_none(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main() -> None:
    data = build_dataset()
    train = data[data["season"].isin(TRAIN_SEASONS)].copy()
    valid = data[data["season"] == VALIDATION_SEASON].copy()
    holdout = data[data["season"] == HOLDOUT_SEASON].copy()
    print(f"dataset rows={len(data)} train={len(train)} valid={len(valid)} holdout={len(holdout)}")

    selection = []
    best = None
    for variant, features in VARIANTS.items():
        for alpha in ALPHAS:
            model = fit_ridge(train, features, alpha)
            pred = predict(valid, model)
            score = metrics(valid["actual_total"].to_numpy(float), pred)
            row = {"variant": variant, "alpha": alpha, **score}
            selection.append(row)
            if best is None or (row["mae"], row["rmse"], len(features)) < (best["mae"], best["rmse"], len(VARIANTS[best["variant"]])):
                best = row
    assert best is not None
    chosen_features = VARIANTS[best["variant"]]
    validation_model = fit_ridge(train, chosen_features, best["alpha"])
    validation_pred = predict(valid, validation_model)

    final_train = pd.concat([train, valid], ignore_index=True)
    final_model = fit_ridge(final_train, chosen_features, best["alpha"])
    holdout_pred = predict(holdout, final_model)

    validation_bets = betting_table(valid, validation_pred)
    holdout_bets = betting_table(holdout, holdout_pred)
    grades = choose_grades(validation_bets)
    grade_holdout = {}
    for grade, threshold in grades.items():
        if threshold is None:
            grade_holdout[grade] = None
        else:
            row = next(r for r in holdout_bets if r["threshold"] == threshold)
            grade_holdout[grade] = {**row, "sides": side_record(holdout, holdout_pred, threshold)}

    validation_eval = evaluate(valid, validation_pred)
    holdout_eval = evaluate(holdout, holdout_pred)
    baseline = holdout_eval["pace_ppd_structural"]["mae"]
    regression = holdout_eval["totals_regression"]["mae"]
    spread_base = holdout_eval["spread_score_sum"]["mae"]

    results = {
        "protocol": {
            "train": list(TRAIN_SEASONS), "validation": VALIDATION_SEASON, "holdout": HOLDOUT_SEASON,
            "sportsbook_predictors_used": False,
            "spread_model_role": "fixed model projection used only as game-script feature",
            "total_design": "pace/possessions x matchup scoring efficiency with advanced PBP residual regression",
        },
        "dataset": {"rows": len(data), "train": len(train), "validation": len(valid), "holdout": len(holdout),
                    "holdout_with_market_total": int(holdout["market_total"].notna().sum())},
        "selection_2024": selection,
        "chosen": best,
        "validation_metrics": validation_eval,
        "holdout_metrics": holdout_eval,
        "holdout_mae_improvement_vs_pace_ppd_pct": 100.0 * (baseline - regression) / baseline,
        "holdout_mae_improvement_vs_spread_score_sum_pct": 100.0 * (spread_base - regression) / spread_base,
        "validation_betting_by_edge": validation_bets,
        "holdout_betting_by_edge": holdout_bets,
        "grade_thresholds_selected_on_2024": grades,
        "grade_thresholds_2025_holdout": grade_holdout,
        "final_model": final_model,
    }
    RESULT_JSON.write_text(json.dumps(nan_to_none(results), indent=2, allow_nan=False))

    h = holdout_eval
    lines = [
        "# CFB totals-specific pace/efficiency regression",
        "",
        "Sportsbook spreads/totals are evaluation-only and never predictors. The fixed spread model is used only for game-script/margin context.",
        "",
        f"- Training: {', '.join(map(str, TRAIN_SEASONS))}",
        f"- Validation/model selection: {VALIDATION_SEASON}",
        f"- Untouched holdout: {HOLDOUT_SEASON}",
        f"- Holdout games: {len(holdout)}",
        f"- Holdout games with archived market total: {int(holdout['market_total'].notna().sum())}",
        f"- Chosen variant: {best['variant']}",
        f"- Chosen ridge alpha: {best['alpha']}",
        "",
        "## 2025 holdout accuracy",
        "",
        f"- Fixed spread-regression team-score sum: MAE {h['spread_score_sum']['mae']:.3f}, RMSE {h['spread_score_sum']['rmse']:.3f}, corr {h['spread_score_sum']['corr']:.3f}",
        f"- Pace x points-per-drive structural baseline: MAE {h['pace_ppd_structural']['mae']:.3f}, RMSE {h['pace_ppd_structural']['rmse']:.3f}, corr {h['pace_ppd_structural']['corr']:.3f}",
        f"- Totals-specific regression: MAE {h['totals_regression']['mae']:.3f}, RMSE {h['totals_regression']['rmse']:.3f}, corr {h['totals_regression']['corr']:.3f}",
        f"- MAE improvement vs pace/PPD baseline: {100*(baseline-regression)/baseline:.2f}%",
        f"- MAE improvement vs spread-score sum: {100*(spread_base-regression)/spread_base:.2f}%",
    ]
    if "market_total" in h:
        lines.append(f"- Archived market total: MAE {h['market_total']['mae']:.3f}, RMSE {h['market_total']['rmse']:.3f}, corr {h['market_total']['corr']:.3f}")
    lines.extend(["", "## 2025 O/U record by absolute model edge", "", "| Edge | Bets | W-L-P | Win rate | ROI @ -110 | O/U count |", "|---:|---:|---:|---:|---:|---:|"])
    for r in holdout_bets:
        lines.append(f"| {r['threshold']}+ | {r['bets']} | {r['wins']}-{r['losses']}-{r['pushes']} | {pct(r['win_rate'])} | {pct(r['roi_at_-110'])} | {r['overs']}/{r['unders']} |")
    lines.extend(["", "## Grade thresholds selected on 2024 only", ""])
    for grade in ("B", "A"):
        threshold = grades[grade]
        if threshold is None:
            lines.append(f"- {grade}: no edge threshold met the pre-set 2024 validation standard.")
        else:
            r = grade_holdout[grade]
            lines.append(f"- {grade}: {threshold}+ points. 2025 holdout {r['wins']}-{r['losses']}-{r['pushes']} ({pct(r['win_rate'])}); Over {r['sides']['over']['wins']}-{r['sides']['over']['losses']}, Under {r['sides']['under']['wins']}-{r['sides']['under']['losses']}.")
    lines.extend(["", "## Standardized final coefficients", "", f"- Residual intercept: {final_model['intercept']:+.4f}"])
    for name, value in sorted(final_model["coef"].items(), key=lambda kv: abs(kv[1]), reverse=True):
        lines.append(f"- {name}: {value:+.4f}")
    RESULT_MD.write_text("\n".join(lines) + "\n")
    print(RESULT_MD.read_text())


if __name__ == "__main__":
    main()
