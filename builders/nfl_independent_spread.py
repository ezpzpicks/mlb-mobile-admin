"""Frozen market-relative NFL spread component; retain the existing total.

The single qualifying grade is B Spread at a 3.5-point disagreement. A larger
disagreement is not promoted to A. The formula was fitted through 2023, and
the selective policy was approved for forward use on November 22, 2026.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

PARAMETER_PATH = Path(__file__).resolve().parents[1] / "config/nfl_independent_spread_v1.json"
PARAMETERS = json.loads(PARAMETER_PATH.read_text())
VERSION = PARAMETERS["version"]
PARAMETER_HASH = hashlib.sha256(PARAMETER_PATH.read_bytes()).hexdigest()
RATING_FIELDS = ("Pass Play Share", "Spread Input Season", "Spread Input Week", "Spread Input Cutoff", "Spread Input Error")
SLATE_FIELDS = ("Spread Model Version", "Spread Model JSON")
OFF_KEYS = ("Points/Game", "Pass EPA/DB", "Rush EPA/Play", "Off Success Rate", "Explosive Rate",
            "Turnover Rate", "Sack Rate Allowed", "Red Zone TD Rate", "Pace")
DEF_KEYS = ("Points Allowed/Game", "Pass Def EPA Edge", "Rush Def EPA Edge", "Def Success Edge",
            "Explosive Def Edge", "Takeaway Rate", "Sack/Pressure Edge", "Red Zone Def Edge", "Pace")
OFF = ("off_scoring", "off_pass_epa", "off_rush_epa", "off_success", "off_explosive",
       "off_turnover", "off_sacks_allowed", "off_red_zone", "off_pace")
DEF = ("def_scoring_allowed", "def_pass_epa_edge", "def_rush_epa_edge", "def_success_edge",
       "def_explosive_edge", "def_takeaway", "def_pressure_edge", "def_red_zone_edge", "def_pace")


def finite(value: Any) -> float:
    if value is None or str(value).strip() == "":
        raise ValueError("Missing independent spread input")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite independent spread input")
    return result


def canonical(team: Any) -> str:
    value = str(team).strip().upper()
    return {"LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR", "WSH": "WAS", "JAC": "JAX"}.get(value, value)


def canonical_game(value: Any) -> str:
    parts = str(value).split("_")
    return "_".join(parts[:2] + [canonical(x) for x in parts[2:]]) if len(parts) == 4 else str(value)


def complete_schedule(builder, season: int, through_week: int | None) -> pd.DataFrame:
    frame = builder._schedule_for_season(season).copy()
    if frame.empty:
        raise ValueError("NFL schedule is unavailable for independent spread inputs")
    frame = frame[frame["Game Type"].astype(str).str.upper().eq("REG")]
    if through_week is not None:
        frame = frame[pd.to_numeric(frame["Week"], errors="coerce").le(through_week)]
    scores = frame[["Away Score", "Home Score"]].apply(pd.to_numeric, errors="coerce")
    frame = frame[scores.notna().all(axis=1)].copy()
    for col in ["Away Team", "Home Team"]:
        frame[col] = frame[col].map(canonical)
    if not set(frame["Away Team"]).union(frame["Home Team"]).issubset(set(builder.NFL_TEAMS)):
        raise ValueError("Schedule team identities are unavailable for the spread model")
    frame["_canonical_id"] = [f"{season}_{int(float(w)):02d}_{a}_{h}" for w, a, h in zip(frame["Week"], frame["Away Team"], frame["Home Team"])]
    return frame


def passing_counts(builder, season: int, through_week: int | None) -> tuple[pd.DataFrame, bool]:
    expected = complete_schedule(builder, season, through_week)
    if expected.empty:
        return pd.DataFrame(columns=["dropbacks", "valid"]), False
    required = {"season_type", "week", "game_id", "posteam", "epa", "qb_dropback", "rush_attempt"}
    refreshed = False
    for attempt in range(2):
        pbp = builder._load_pbp_season(season)
        okay = required.issubset(pbp.columns)
        if okay:
            pbp = pbp[pbp.season_type.eq("REG") & pbp.epa.notna()].copy()
            pbp["_canonical_id"] = pbp.game_id.map(canonical_game)
            if through_week is not None:
                pbp = pbp[pd.to_numeric(pbp.week, errors="coerce").le(through_week)]
            wanted = set(expected._canonical_id)
            missing = wanted - set(pbp._canonical_id)
            if not missing:
                pbp = pbp[pbp._canonical_id.isin(wanted)].copy()
                pbp["team"] = pbp.posteam.map(canonical)
                pbp["dropbacks"] = pd.to_numeric(pbp.qb_dropback, errors="coerce").fillna(0).gt(0).astype(int)
                pbp["valid"] = (pbp.dropbacks.gt(0) | pd.to_numeric(pbp.rush_attempt, errors="coerce").fillna(0).gt(0)).astype(int)
                pbp = pbp[pbp.valid.eq(1) & pbp.team.isin(builder.NFL_TEAMS)]
                return pbp.groupby("team")[["dropbacks", "valid"]].sum(), refreshed
        if attempt == 0:
            # An old /tmp PBP file must not masquerade as the previous week.
            url = f"https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet"
            builder._download_to_disk(url, builder._pbp_cache_path(season))
            refreshed = True
    raise ValueError("Complete earlier-week NFL play-by-play is unavailable")


def add_pass_shares(builder, ratings: pd.DataFrame, season: int, week: int,
                    prior: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    output = ratings.copy()
    schedule = builder._schedule_for_season(season)
    week_schedule = schedule[pd.to_numeric(schedule["Week"], errors="coerce").eq(week)]
    if week_schedule.empty:
        raise ValueError("The projection week's schedule cutoff is unavailable")
    cutoff = str(week_schedule["Game Date"].min())[:10]
    previous = complete_schedule(builder, season, max(0, week - 1))
    if not previous.empty and not str(previous["Game Date"].max())[:10] < cutoff:
        raise ValueError("Earlier-week input date crosses the frozen week cutoff")
    shares = []
    for row in output.to_dict("records"):
        team = canonical(row["Team"])
        weight = finite(row["Current Season Weight"])
        if not 0 <= weight <= 1 or team not in prior.index or prior.loc[team, "valid"] <= 0:
            raise ValueError("A prior-season passing-share sample is missing")
        p = float(prior.loc[team, "dropbacks"] / prior.loc[team, "valid"])
        c = p
        if weight > 0:
            if team not in current.index or current.loc[team, "valid"] <= 0:
                raise ValueError("A weighted current-season passing-share sample is missing")
            c = float(current.loc[team, "dropbacks"] / current.loc[team, "valid"])
        shares.append((1 - weight) * p + weight * c)
    output["Pass Play Share"] = shares
    output["Spread Input Season"], output["Spread Input Week"] = int(season), int(week)
    output["Spread Input Cutoff"], output["Spread Input Error"] = cutoff, ""
    return output


def feature_vector(away: dict, home: dict, settings: dict) -> dict[str, float]:
    if not settings.get("spread_market_ready", False):
        raise ValueError("Matching spread and total quotes are required")
    for row in [away, home]:
        if str(row.get("Spread Input Error", "")).strip():
            raise ValueError(str(row["Spread Input Error"]))
        if finite(row.get("Spread Input Season")) != finite(row.get("Season")) or finite(row.get("Spread Input Week")) != finite(row.get("Projection Week")):
            raise ValueError("Spread inputs do not match the projection week")
        if not str(row.get("Spread Input Cutoff", "")).strip():
            raise ValueError("Spread input cutoff is unavailable")
    if finite(home.get("Season")) != finite(away.get("Season")) or finite(home.get("Projection Week")) != finite(away.get("Projection Week")):
        raise ValueError("The two team rating weeks disagree")
    if home.get("Spread Input Cutoff") != away.get("Spread Input Cutoff"):
        raise ValueError("The two team input cutoffs disagree")
    if finite(home.get("Season")) != finite(settings.get("season")) or finite(home.get("Projection Week")) != finite(settings.get("week")):
        raise ValueError("Team ratings do not match the scheduled game week")
    if date.fromisoformat(str(home["Spread Input Cutoff"])[:10]) > date.fromisoformat(str(settings.get("game_date", ""))[:10]):
        raise ValueError("Team inputs were not available on the game date")
    ph, pa = finite(home.get("Pass Play Share")), finite(away.get("Pass Play Share"))
    if not 0 <= ph <= 1 or not 0 <= pa <= 1:
        raise ValueError("Invalid passing shares")
    h = {**{k: finite(home.get(v)) for k, v in zip(OFF, OFF_KEYS)}, **{k: finite(away.get(v)) for k, v in zip(DEF, DEF_KEYS)}}
    a = {**{k: finite(away.get(v)) for k, v in zip(OFF, OFF_KEYS)}, **{k: finite(home.get(v)) for k, v in zip(DEF, DEF_KEYS)}}
    features = {key + "_diff": h[key] - a[key] for key in OFF + DEF}
    def interactions(row, share):
        return {"pass_style": share * (row["off_pass_epa"] - row["def_pass_epa_edge"]),
            "rush_style": (1 - share) * (row["off_rush_epa"] - row["def_rush_epa_edge"]),
            "pressure": share * max(0., row["def_pressure_edge"]) * max(0., row["off_sacks_allowed"] - .067),
            "explosive": max(0., row["off_explosive"] - .105) * max(0., -row["def_explosive_edge"]),
            "red_zone": max(0., row["off_red_zone"] - .56) * max(0., -row["def_red_zone_edge"])}
    hi, ai = interactions(h, ph), interactions(a, pa)
    features.update({name + "_diff": hi[name] - ai[name] for name in hi})
    margin = -finite(settings.get("market_home_spread"))
    features.update({"rest_diff": float(np.clip(finite(settings.get("home_rest")) - finite(settings.get("away_rest")), -14, 14)),
        "neutral": float(bool(settings.get("neutral_site"))),
        "season_weight": .5 * (finite(home.get("Current Season Weight")) + finite(away.get("Current Season Weight"))),
        "pass_share_diff": ph - pa, "market_margin": margin, "absolute_line": abs(margin),
        "market_total_centered": finite(settings.get("market_total")) - 45., "hfa": finite(settings.get("home_field"))})
    if finite(settings.get("market_total")) <= 0:
        raise ValueError("Invalid market total")
    return features


def forecast_margin(features: dict[str, float]) -> float:
    x = np.array([finite(features.get(name)) for name in PARAMETERS["names"]])
    correction = float(PARAMETERS["intercept"] + ((x - np.array(PARAMETERS["mean"])) / np.array(PARAMETERS["scale"])) @ np.array(PARAMETERS["coefficient"]))
    return float(np.clip(features["market_margin"] + correction, -31., 31.))


def grade_projection(projection: dict) -> str:
    component = projection.get("spread_component", {})
    if component.get("version") != VERSION or component.get("parameter_hash") != PARAMETER_HASH or component.get("ready") is not True:
        return "No Play (spread inputs unavailable)"
    try:
        edge = finite(component.get("edge"))
        if not math.isclose(finite(projection.get("margin")), finite(component.get("margin")), abs_tol=1e-9):
            return "No Play (spread inputs unavailable)"
        if not math.isclose(edge, finite(component["margin"]) - finite(component["features"]["market_margin"]), abs_tol=1e-9):
            return "No Play (spread inputs unavailable)"
    except (ValueError, KeyError, TypeError):
        return "No Play (spread inputs unavailable)"
    return "B Spread" if abs(edge) >= PARAMETERS["minimum_edge"] else "Non-Edge Spread"


def storage_version(base: str, projection: dict) -> str:
    return base + "|" + VERSION if projection.get("spread_component", {}).get("version") == VERSION else base


def storage_fields(projection: dict) -> dict:
    component = projection.get("spread_component")
    return {"Spread Model Version": VERSION, "Spread Model JSON": json.dumps(component, sort_keys=True, separators=(",", ":"))} if component else {}


def combine_projection(base: dict, away: dict, home: dict, settings: dict) -> dict:
    result = dict(base)
    component = {"version": VERSION, "parameter_hash": PARAMETER_HASH, "minimum_edge": 3.5, "qualified_grade": "B Spread", "ready": False}
    try:
        features = feature_vector(away, home, settings)
        margin = forecast_margin(features)
        total = finite(base.get("total"))
        if abs(margin) > total:
            raise ValueError("Independent margin and total imply a negative score")
        component.update(ready=True, features=features, margin=margin, edge=margin - features["market_margin"],
                         input_cutoff=str(home["Spread Input Cutoff"]), season=int(home["Season"]), week=int(home["Projection Week"]))
        result.update(margin=margin, home_score=(total + margin) / 2., away_score=(total - margin) / 2.)
        adjustment = float(settings.get("manual_home_margin_adjustment", 0.) or 0.)
        if adjustment:
            result.update(margin=margin + adjustment, home_score=(total + margin + adjustment) / 2., away_score=(total - margin - adjustment) / 2.)
            component.update(ready=False, reason="Manual margin override lies outside the frozen spread policy")
    except (ValueError, KeyError, TypeError) as error:
        component["reason"] = str(error)
    result["spread_component"] = component
    result["legacy_projection"] = dict(base)
    return result


def install_independent_spread_layer(builder):
    if getattr(builder, "_INDEPENDENT_SPREAD_LAYER_INSTALLED", False):
        return
    base_project, base_simulate = builder._project_matchup, builder._simulate_game
    base_ratings, base_props = builder._build_automated_ratings, builder._build_game_prop_rows
    base_ensure = builder._ensure_automated_ratings
    builder.RATING_COLUMNS[:] = list(dict.fromkeys([*builder.RATING_COLUMNS, *RATING_FIELDS]))
    builder.SLATE_COLUMNS[:] = list(dict.fromkeys([*builder.SLATE_COLUMNS, *SLATE_FIELDS]))
    builder.TRACKER_COLUMNS[:] = list(dict.fromkeys([*builder.TRACKER_COLUMNS, *SLATE_FIELDS]))
    def ratings(season, week):
        output = base_ratings(season, week)
        try:
            prior, refreshed_p = passing_counts(builder, int(season) - 1, None)
            current, refreshed_c = passing_counts(builder, int(season), max(0, int(week) - 1))
            if refreshed_p or refreshed_c:
                output = base_ratings(season, week)
            return add_pass_shares(builder, output, int(season), int(week), prior, current)
        except Exception as error:
            output = output.copy()
            output["Pass Play Share"] = np.nan
            output["Spread Input Error"] = str(error)
            return output
    def project(away, home, away_lineup, home_lineup, settings):
        return combine_projection(base_project(away, home, away_lineup, home_lineup, settings), away, home, settings)
    def ensure(season, week):
        output = base_ensure(season, week)
        if "Pass Play Share" not in output or pd.to_numeric(output["Pass Play Share"], errors="coerce").isna().any():
            output = ratings(season, week)
            builder.st.session_state[f"nfl_auto_ratings_{int(season)}_{int(week)}"] = output
        return output
    def simulate(projection, *args, **kwargs):
        current = base_simulate(projection, *args, **kwargs)
        original = base_simulate(projection.get("legacy_projection", projection), *args, **kwargs)
        current["over"] = original["over"]
        return current
    def props(*args, **kwargs):
        values = list(args)
        if len(values) > 8:
            values[8] = values[8].get("legacy_projection", values[8])
        elif "projection" in kwargs:
            kwargs["projection"] = kwargs["projection"].get("legacy_projection", kwargs["projection"])
        return base_props(*values, **kwargs)
    builder._build_automated_ratings, builder._project_matchup = ratings, project
    builder._ensure_automated_ratings = ensure
    builder._simulate_game, builder._build_game_prop_rows = simulate, props
    builder._INDEPENDENT_SPREAD_LAYER_INSTALLED = True
