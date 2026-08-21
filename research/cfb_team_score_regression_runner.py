"""Run CFB regression research against the exact cfbfastR parquet schema.

This shim keeps the statistical design in cfb_team_score_regression_v2 unchanged
while replacing only the historical game reconstruction layer with the actual
SportsDataverse/cfbfastR field names observed in CI.
"""
from __future__ import annotations

from pathlib import Path
import math

import numpy as np
import pandas as pd

from builders import cfb_builder as cfb
from research import cfb_team_score_regression_v2 as model


PBP_COLUMNS = [
    "year", "season", "week", "game_id", "game_play_number",
    "home", "away", "neutral_site", "completed",
    "pos_team", "def_pos_team", "pos_team_score", "def_pos_team_score",
    "team", "opponent", "team_score", "opponent_score",
    "spread", "formatted_spread", "over_under",
]


def _load_exact_pbp(season: int) -> pd.DataFrame:
    path = cfb._download_open_asset("cfbfastR_cfb_pbp", season, ("play_by_play", "pbp"))
    if path is None or not Path(path).exists():
        raise RuntimeError(f"SportsDataverse PBP unavailable for {season}")
    import pyarrow.parquet as pq

    available = set(pq.read_schema(path).names)
    columns = [col for col in PBP_COLUMNS if col in available]
    frame = pd.read_parquet(path, columns=columns)
    if frame.empty:
        raise RuntimeError(f"SportsDataverse PBP empty for {season}")
    return frame


def _last_valid(series: pd.Series):
    values = series.dropna()
    if values.empty:
        return np.nan
    if values.dtype == object or pd.api.types.is_string_dtype(values):
        values = values[values.astype(str).str.strip() != ""]
        if values.empty:
            return np.nan
    return values.iloc[-1]


def _first_valid(series: pd.Series):
    values = series.dropna()
    if values.empty:
        return np.nan
    if values.dtype == object or pd.api.types.is_string_dtype(values):
        values = values[values.astype(str).str.strip() != ""]
        if values.empty:
            return np.nan
    return values.iloc[0]


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "completed"}


def _num(value, default=np.nan) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)


def _team_final_score(group: pd.DataFrame, team_name: str) -> float:
    values: list[float] = []
    if {"pos_team", "pos_team_score"}.issubset(group.columns):
        mask = group["pos_team"].astype(str) == team_name
        values.extend(pd.to_numeric(group.loc[mask, "pos_team_score"], errors="coerce").dropna().tolist())
    if {"def_pos_team", "def_pos_team_score"}.issubset(group.columns):
        mask = group["def_pos_team"].astype(str) == team_name
        values.extend(pd.to_numeric(group.loc[mask, "def_pos_team_score"], errors="coerce").dropna().tolist())
    if {"team", "team_score"}.issubset(group.columns):
        mask = group["team"].astype(str) == team_name
        values.extend(pd.to_numeric(group.loc[mask, "team_score"], errors="coerce").dropna().tolist())
    if {"opponent", "opponent_score"}.issubset(group.columns):
        mask = group["opponent"].astype(str) == team_name
        values.extend(pd.to_numeric(group.loc[mask, "opponent_score"], errors="coerce").dropna().tolist())
    return float(max(values)) if values else float("nan")


def games_from_pbp_exact(season: int) -> pd.DataFrame:
    pbp = _load_exact_pbp(season).copy()
    pbp["game_id"] = pbp["game_id"].astype(str)
    pbp = pbp[~pbp["game_id"].isin(["", "nan", "None"])].copy()
    if "game_play_number" in pbp.columns:
        pbp["_seq"] = pd.to_numeric(pbp["game_play_number"], errors="coerce")
        pbp = pbp.sort_values(["game_id", "_seq"], kind="stable")

    rows: list[dict] = []
    for game_id, group in pbp.groupby("game_id", sort=False):
        home = str(_first_valid(group["home"]) if "home" in group.columns else "").strip()
        away = str(_first_valid(group["away"]) if "away" in group.columns else "").strip()
        week = int(_num(_first_valid(group["week"]) if "week" in group.columns else 0, 0))
        if not home or not away or week <= 0:
            continue

        home_score = _team_final_score(group, home)
        away_score = _team_final_score(group, away)
        if not math.isfinite(home_score) or not math.isfinite(away_score):
            continue

        if "completed" in group.columns:
            completed_value = _last_valid(group["completed"])
            if str(completed_value).strip().lower() in {"false", "0", "no"}:
                continue

        neutral = _truthy(_first_valid(group["neutral_site"]) if "neutral_site" in group.columns else False)
        home_spread = _num(_first_valid(pd.to_numeric(group["spread"], errors="coerce")) if "spread" in group.columns else np.nan)
        total_line = _num(_first_valid(pd.to_numeric(group["over_under"], errors="coerce")) if "over_under" in group.columns else np.nan)
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
            "total_line": total_line,
            "actual_margin": home_score - away_score,
            "actual_total": home_score + away_score,
        })

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError(f"No completed games reconstructed from {season} PBP")
    frame["game_key"] = [f"{season}-{w}-{gid}" for w, gid in zip(frame["week"], frame["game_id"])]
    frame = frame.sort_values(["week", "game_key"]).drop_duplicates("game_key").reset_index(drop=True)
    print(
        f"RECONSTRUCTED {season}: games={len(frame)} "
        f"spread_coverage={frame['home_spread'].notna().mean():.1%} "
        f"total_coverage={frame['total_line'].notna().mean():.1%}"
    )
    return frame


def main() -> None:
    model.games_from_pbp = games_from_pbp_exact
    model.main()


if __name__ == "__main__":
    main()
