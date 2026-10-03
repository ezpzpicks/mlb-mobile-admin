"""2026 early-season CFB spread-regime correction.

This layer is intentionally narrow:
- applies only to 2026 Week 5+
- changes only scoring margin, never the independently modeled total
- never uses a sportsbook line as a predictor
- uses the exact frozen Week-1-4 five-variable ridge model selected with
  week-blocked validation on 2026 games

Validated football inputs:
1. prior-week ESPN Game Control differential
2. preseason defensive returning-production differential
3. competitive-state offensive explosiveness differential
4. current-power movement from the production spread regression
5. prior-week offensive havoc differential

The correction is capped at +/-8 points. If any required external input is not
available, the layer fails closed and leaves the existing production projection
unchanged rather than applying a partial model.
"""
from __future__ import annotations

import math
from pathlib import Path
import threading
import time
from typing import Any

import numpy as np
import pandas as pd
import requests

from builders import cfb_game_regression as spread_reg

MODEL_VERSION = "cfb-v2.6-2026-regime-edge-2026-10-02"
MODEL_RESEARCH_VERSION = "cfb-2026-w1-4-base5-edge-safe-v1"
ACTIVE_SEASON = 2026
MIN_ACTIVE_WEEK = 5
CORRECTION_CAP = 8.0

FEATURES = (
    "gamecontrol_diff",
    "def_returning_diff",
    "explosive_diff",
    "current_power_margin",
    "havoc_off_diff",
)

# Frozen from research/results/cfb_2026_base5_frozen_params.json.
FEATURE_MEANS = {
    "gamecontrol_diff": 2.5072434782608695,
    "def_returning_diff": 0.043474568849501745,
    "explosive_diff": -0.012986484257947113,
    "current_power_margin": -1.1370206673719765,
    "havoc_off_diff": 0.008264319709601408,
}
FEATURE_STDS = {
    "gamecontrol_diff": 14.895739436306537,
    "def_returning_diff": 0.2169457181066098,
    "explosive_diff": 0.10976521571038278,
    "current_power_margin": 11.441119001645289,
    "havoc_off_diff": 0.050592311857964985,
}
MODEL_INTERCEPT = 0.33212597431128954
MODEL_COEFFICIENTS = {
    "gamecontrol_diff": 1.3608957338347005,
    "def_returning_diff": 1.54531877318752,
    "explosive_diff": 1.4673239916442995,
    "current_power_margin": -1.812176772806967,
    "havoc_off_diff": -1.4777889807750926,
}

ASSET_URLS = {
    "cfb_fpi_weekly": "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/cfb_fpi_weekly/cfb_fpi_weekly_{season}.parquet",
    "cfb_returning_production": "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/cfb_returning_production/cfb_returning_production_{season}.parquet",
    "cfb_team_summaries_weekly": "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/cfb_team_summaries_weekly/cfb_team_summaries_weekly_{season}.parquet",
}

_ASSET_LOCK = threading.Lock()
_FRAME_CACHE: dict[tuple[str, int, int, int], pd.DataFrame] = {}
_CONTEXT_CACHE: dict[tuple[int, int], dict[str, dict[Any, float]]] = {}


def _num(value: Any, default: float = np.nan) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def _truthy_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    numeric = pd.to_numeric(series, errors="coerce")
    text = series.astype(str).str.strip().str.lower()
    return (numeric.fillna(0) != 0) | text.isin({"true", "yes", "y", "1"})


def _ensure_small_asset(cfb_builder: Any, tag: str, season: int) -> Path | None:
    """Synchronously cache the three small regime inputs by deterministic URL."""
    base_dir = Path(getattr(cfb_builder, "OPEN_DATA_DIR", "/tmp/ezpz_cfb_cache/open_data"))
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / f"{tag}_{int(season)}.parquet"
    freshness = 21600 if int(season) >= ACTIVE_SEASON else 86400 * 30
    try:
        if path.exists() and path.stat().st_size > 1024 and time.time() - path.stat().st_mtime <= freshness:
            return path
    except Exception:
        pass

    url_template = ASSET_URLS.get(tag)
    if not url_template:
        return path if path.exists() else None

    with _ASSET_LOCK:
        try:
            if path.exists() and path.stat().st_size > 1024 and time.time() - path.stat().st_mtime <= freshness:
                return path
        except Exception:
            pass
        temp = path.with_suffix(".regime.tmp")
        try:
            with requests.get(
                url_template.format(season=int(season)),
                stream=True,
                timeout=(8, 60),
                headers={"User-Agent": "EZPZ-Picks-NCAAF/2.6 regime-cache"},
            ) as response:
                response.raise_for_status()
                with temp.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            if temp.stat().st_size < 1024:
                raise RuntimeError("regime input download was unexpectedly small")
            temp.replace(path)
            return path
        except Exception as exc:
            print(f"[cfb-regime] {tag} refresh failed: {type(exc).__name__}: {exc}")
            try:
                temp.unlink(missing_ok=True)
            except Exception:
                pass
            return path if path.exists() and path.stat().st_size > 1024 else None


def _read_asset(cfb_builder: Any, tag: str, season: int, aliases: dict[str, tuple[str, ...]]) -> pd.DataFrame:
    path = _ensure_small_asset(cfb_builder, tag, season)
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        stat = path.stat()
        key = (tag, int(season), int(stat.st_size), int(stat.st_mtime_ns))
        cached = _FRAME_CACHE.get(key)
        if cached is not None:
            return cached.copy()
        frame = cfb_builder._read_open_parquet(path, aliases)
        if frame is not None and not frame.empty:
            _FRAME_CACHE[key] = frame.copy()
        return frame if frame is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _team_ids(cfb_builder: Any) -> dict[str, float]:
    canonicalizer = getattr(cfb_builder, "_canonical_team_name", spread_reg._normalize_team)
    output: dict[str, float] = {}
    try:
        teams = cfb_builder._espn_team_index()
    except Exception:
        teams = {}
    for name, raw in (teams or {}).items():
        team = canonicalizer(name)
        team_id = _num((raw or {}).get("id"), np.nan) if isinstance(raw, dict) else np.nan
        if team and math.isfinite(team_id):
            output[team] = team_id
    return output


def _latest_by_team(frame: pd.DataFrame, week: int, value_col: str) -> dict[float, float]:
    if frame is None or frame.empty or "team_id" not in frame.columns or "week" not in frame.columns:
        return {}
    work = frame.copy()
    work["team_id"] = pd.to_numeric(work["team_id"], errors="coerce")
    work["week"] = pd.to_numeric(work["week"], errors="coerce")
    work[value_col] = pd.to_numeric(work.get(value_col), errors="coerce")
    work = work[work["team_id"].notna() & work["week"].notna() & (work["week"] < int(week))]
    if work.empty:
        return {}
    sort_cols = ["team_id", "week"]
    if "run_date_time_key" in work.columns:
        sort_cols.append("run_date_time_key")
    work = work.sort_values(sort_cols).drop_duplicates("team_id", keep="last")
    return {
        float(row.team_id): float(getattr(row, value_col))
        for row in work.itertuples(index=False)
        if math.isfinite(_num(getattr(row, value_col), np.nan))
    }


def _gamecontrol(cfb_builder: Any, season: int, week: int) -> dict[float, float]:
    frame = _read_asset(
        cfb_builder,
        "cfb_fpi_weekly",
        season,
        {
            "team_id": ("team_id",),
            "week": ("week",),
            "gamecontrol": ("gamecontrol", "game_control"),
            "run_date_time_key": ("run_date_time_key",),
            "snapshot_out_of_sequence": ("snapshot_out_of_sequence",),
            "snapshot_is_contemporaneous": ("snapshot_is_contemporaneous",),
        },
    )
    if frame.empty:
        return {}
    if "snapshot_out_of_sequence" in frame.columns:
        frame = frame[~_truthy_series(frame["snapshot_out_of_sequence"])].copy()
    if "snapshot_is_contemporaneous" in frame.columns:
        frame = frame[_truthy_series(frame["snapshot_is_contemporaneous"])].copy()
    return _latest_by_team(frame, week, "gamecontrol")


def _def_returning(cfb_builder: Any, season: int) -> dict[float, float]:
    frame = _read_asset(
        cfb_builder,
        "cfb_returning_production",
        season,
        {
            "team_id": ("team_id",),
            "def_returning": ("def_returning", "defense_returning"),
        },
    )
    if frame.empty:
        return {}
    frame["team_id"] = pd.to_numeric(frame.get("team_id"), errors="coerce")
    frame["def_returning"] = pd.to_numeric(frame.get("def_returning"), errors="coerce")
    frame = frame[frame["team_id"].notna() & frame["def_returning"].notna()].drop_duplicates("team_id")
    return {float(r.team_id): float(r.def_returning) for r in frame.itertuples(index=False)}


def _havoc_offense(cfb_builder: Any, season: int, week: int) -> dict[float, float]:
    frame = _read_asset(
        cfb_builder,
        "cfb_team_summaries_weekly",
        season,
        {
            "team_id": ("pos_team_id", "team_id"),
            "week": ("through_week", "week"),
            "havoc_off": ("havoc_off",),
        },
    )
    return _latest_by_team(frame, week, "havoc_off")


def _competitive_explosive(cfb_builder: Any, season: int, week: int) -> dict[str, float]:
    path = None
    try:
        path = cfb_builder._download_open_asset_now("cfbfastR_cfb_pbp", int(season), ("play_by_play", "pbp"))
    except Exception:
        try:
            path = cfb_builder._download_open_asset("cfbfastR_cfb_pbp", int(season), ("play_by_play", "pbp"))
        except Exception:
            path = None
    if path is None:
        return {}
    frame = cfb_builder._read_open_parquet(
        Path(path),
        {
            "week": ("week",),
            "offense": ("offense_play", "offense", "posteam", "pos_team"),
            "defense": ("defense_play", "defense", "defteam", "def_pos_team"),
            "epa": ("EPA", "epa", "ppa"),
            "pass": ("pass", "pass_play", "qb_dropback"),
            "rush": ("rush", "rush_play"),
            "wp": ("wp_before", "wp"),
            "pos_score": ("pos_team_score", "posteam_score"),
            "def_score": ("def_pos_team_score", "defteam_score"),
        },
    )
    if frame is None or frame.empty:
        return {}
    canonicalizer = getattr(cfb_builder, "_canonical_team_name", spread_reg._normalize_team)
    for col in ("week", "epa", "pass", "rush", "wp", "pos_score", "def_score"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["offense"] = frame.get("offense", pd.Series("", index=frame.index)).map(canonicalizer)
    frame["defense"] = frame.get("defense", pd.Series("", index=frame.index)).map(canonicalizer)
    valid = (
        frame.get("week", pd.Series(np.nan, index=frame.index)).lt(int(week))
        & frame.get("epa", pd.Series(np.nan, index=frame.index)).notna()
        & frame["offense"].astype(str).ne("")
        & frame["defense"].astype(str).ne("")
    )
    if "pass" in frame.columns and "rush" in frame.columns:
        valid &= frame["pass"].fillna(0).gt(0) | frame["rush"].fillna(0).gt(0)
    score_ok = pd.Series(False, index=frame.index)
    if "pos_score" in frame.columns and "def_score" in frame.columns:
        score_ok = (frame["pos_score"] - frame["def_score"]).abs().le(17)
    if "wp" in frame.columns:
        comp = frame["wp"].between(0.10, 0.90, inclusive="both") | (frame["wp"].isna() & score_ok)
    else:
        comp = score_ok
    work = frame[valid & comp].copy()
    if work.empty:
        return {}
    work["_explosive"] = work["epa"].gt(1.0).astype(float)
    rates = work.groupby("offense")["_explosive"].mean()
    return {str(team): float(value) for team, value in rates.items() if math.isfinite(_num(value, np.nan))}


def _context(cfb_builder: Any, season: int, week: int) -> dict[str, dict[Any, float]]:
    key = (int(season), int(week))
    cached = _CONTEXT_CACHE.get(key)
    if cached is not None:
        return cached
    value = {
        "ids": _team_ids(cfb_builder),
        "gamecontrol": _gamecontrol(cfb_builder, season, week),
        "def_returning": _def_returning(cfb_builder, season),
        "havoc_off": _havoc_offense(cfb_builder, season, week),
        "explosive": _competitive_explosive(cfb_builder, season, week),
    }
    # Cache only complete contexts. A transient download/read failure must be able
    # to recover on the next Streamlit rerun.
    if all(value[name] for name in ("ids", "gamecontrol", "def_returning", "havoc_off", "explosive")):
        _CONTEXT_CACHE[key] = value
    return value


def _feature_row(cfb_builder: Any, game: pd.Series, projection: dict[str, Any]) -> dict[str, float] | None:
    season = int(_num(game.get("Season"), getattr(cfb_builder, "DEFAULT_SEASON", ACTIVE_SEASON)))
    week = int(_num(game.get("Week"), 1.0))
    if season != ACTIVE_SEASON or week < MIN_ACTIVE_WEEK:
        return None

    canonicalizer = getattr(cfb_builder, "_canonical_team_name", spread_reg._normalize_team)
    away = canonicalizer(game.get("Away Team"))
    home = canonicalizer(game.get("Home Team"))
    ctx = _context(cfb_builder, season, week)
    away_id = ctx.get("ids", {}).get(away)
    home_id = ctx.get("ids", {}).get(home)
    if not math.isfinite(_num(away_id, np.nan)) or not math.isfinite(_num(home_id, np.nan)):
        return None

    away_features = projection.get("regression_away_features") or {}
    home_features = projection.get("regression_home_features") or {}
    current_power_margin = _num(home_features.get("current_power_delta"), np.nan) - _num(
        away_features.get("current_power_delta"), np.nan
    )

    raw = {
        "gamecontrol_diff": _num(ctx.get("gamecontrol", {}).get(home_id), np.nan) - _num(ctx.get("gamecontrol", {}).get(away_id), np.nan),
        "def_returning_diff": _num(ctx.get("def_returning", {}).get(home_id), np.nan) - _num(ctx.get("def_returning", {}).get(away_id), np.nan),
        "explosive_diff": _num(ctx.get("explosive", {}).get(home), np.nan) - _num(ctx.get("explosive", {}).get(away), np.nan),
        "current_power_margin": current_power_margin,
        "havoc_off_diff": _num(ctx.get("havoc_off", {}).get(home_id), np.nan) - _num(ctx.get("havoc_off", {}).get(away_id), np.nan),
    }
    if not all(math.isfinite(_num(raw.get(name), np.nan)) for name in FEATURES):
        return None
    return {name: float(raw[name]) for name in FEATURES}


def _predict_correction(features: dict[str, float]) -> tuple[float, dict[str, float]]:
    contributions: dict[str, float] = {}
    correction = float(MODEL_INTERCEPT)
    for name in FEATURES:
        value = _num(features.get(name), FEATURE_MEANS[name])
        z = (value - FEATURE_MEANS[name]) / FEATURE_STDS[name]
        contribution = MODEL_COEFFICIENTS[name] * z
        contributions[name] = float(contribution)
        correction += contribution
    return float(np.clip(correction, -CORRECTION_CAP, CORRECTION_CAP)), contributions


def install_regime_adjustment(cfb_builder: Any) -> None:
    """Install after the spread and independent-total layers, before calibration."""
    if getattr(cfb_builder, "_REGIME_2026_LAYER_INSTALLED", False):
        return

    original_project_matchup = cfb_builder.project_matchup

    def project_matchup(game: pd.Series, ratings: pd.DataFrame, away_personnel: Any,
                        home_personnel: Any, environment: Any) -> dict[str, Any]:
        projection = original_project_matchup(game, ratings, away_personnel, home_personnel, environment)
        season = int(_num(game.get("Season"), getattr(cfb_builder, "DEFAULT_SEASON", ACTIVE_SEASON)))
        week = int(_num(game.get("Week"), 1.0))
        if season != ACTIVE_SEASON or week < MIN_ACTIVE_WEEK:
            projection.update({
                "regime_2026_applied": False,
                "regime_2026_reason": "outside_active_2026_week5_plus_window",
                "regime_2026_research_version": MODEL_RESEARCH_VERSION,
            })
            return projection

        try:
            features = _feature_row(cfb_builder, game, projection)
            if features is None:
                projection.update({
                    "regime_2026_applied": False,
                    "regime_2026_reason": "required_football_inputs_unavailable",
                    "regime_2026_research_version": MODEL_RESEARCH_VERSION,
                })
                return projection

            correction, contributions = _predict_correction(features)
            old_margin = _num(projection.get("margin"), 0.0)
            projected_total = _num(
                projection.get("total"),
                _num(projection.get("away_points"), 28.0) + _num(projection.get("home_points"), 28.0),
            )
            max_margin = max(0.0, min(60.0, projected_total - 6.0))
            final_margin = float(np.clip(old_margin + correction, -max_margin, max_margin))
            applied_correction = final_margin - old_margin
            home_points = (projected_total + final_margin) / 2.0
            away_points = (projected_total - final_margin) / 2.0

            projection.update({
                "away_points": float(away_points),
                "home_points": float(home_points),
                "margin": float(final_margin),
                "total": float(projected_total),
                "regime_2026_applied": True,
                "regime_2026_base_margin": float(old_margin),
                "regime_2026_correction": float(applied_correction),
                "regime_2026_raw_correction": float(correction),
                "regime_2026_features": features,
                "regime_2026_contributions": contributions,
                "regime_2026_alpha": 4.0,
                "regime_2026_cap": float(CORRECTION_CAP),
                "regime_2026_research_version": MODEL_RESEARCH_VERSION,
            })
        except Exception as exc:
            projection.update({
                "regime_2026_applied": False,
                "regime_2026_reason": f"fallback_after_error:{type(exc).__name__}",
                "regime_2026_error": str(exc),
                "regime_2026_research_version": MODEL_RESEARCH_VERSION,
            })
        return projection

    cfb_builder.project_matchup = project_matchup
    cfb_builder._REGIME_2026_LAYER_INSTALLED = True
