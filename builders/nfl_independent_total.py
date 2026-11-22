"""Frozen NFL tree total with a separate regression agreement requirement."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from builders.nfl_independent_spread import OFF, DEF, OFF_KEYS, DEF_KEYS, finite, feature_vector as spread_features
from builders.nfl_game_regression import simulate_game as regression_simulate

PARAMETER_PATH = Path(__file__).resolve().parents[1] / "config/nfl_independent_total_v1.json"
PARAMETERS = json.loads(PARAMETER_PATH.read_text())
VERSION = PARAMETERS["version"]
PARAMETER_HASH = hashlib.sha256(PARAMETER_PATH.read_bytes()).hexdigest()
SLATE_FIELDS = ("Total Model Version", "Total Model JSON")


def feature_vector(away: dict, home: dict, settings: dict) -> dict[str, float]:
    common = spread_features(away, home, settings)
    h = {**{k: finite(home.get(v)) for k, v in zip(OFF, OFF_KEYS)}, **{k: finite(away.get(v)) for k, v in zip(DEF, DEF_KEYS)}}
    a = {**{k: finite(away.get(v)) for k, v in zip(OFF, OFF_KEYS)}, **{k: finite(home.get(v)) for k, v in zip(DEF, DEF_KEYS)}}
    ph, pa = finite(home["Pass Play Share"]), finite(away["Pass Play Share"])
    result = {key + "_sum": h[key] + a[key] for key in OFF + DEF}
    def interactions(row, share):
        return {"pass_style": share * (row["off_pass_epa"] - row["def_pass_epa_edge"]),
            "rush_style": (1 - share) * (row["off_rush_epa"] - row["def_rush_epa_edge"]),
            "pressure": share * max(0., row["def_pressure_edge"]) * max(0., row["off_sacks_allowed"] - .067),
            "explosive": max(0., row["off_explosive"] - .105) * max(0., -row["def_explosive_edge"]),
            "red_zone": max(0., row["off_red_zone"] - .56) * max(0., -row["def_red_zone_edge"])}
    hi, ai = interactions(h, ph), interactions(a, pa)
    result.update({name + "_sum": hi[name] + ai[name] for name in hi})
    result.update({"rest_sum": float(np.clip(finite(settings["home_rest"]) + finite(settings["away_rest"]), 0, 40)),
        "neutral": common["neutral"], "season_weight": common["season_weight"], "pass_share_sum": ph + pa,
        "pace_mismatch": abs(h["off_pace"] - a["off_pace"]),
        **{key: common[key] for key in ("market_margin", "absolute_line", "market_total_centered", "hfa")}})
    return result


def predict_model(model: dict, features: dict[str, float]) -> float:
    """Plain JSON inference, with no fitting, pickle or sklearn dependency."""
    x = [finite(features.get(name)) for name in model["names"]]
    if "trees" in model:
        prediction = float(model["baseline"])
        for tree in model["trees"]:
            index = 0
            while not tree[index]["leaf"]:
                node = tree[index]
                index = node["left"] if x[node["feature"]] <= node["threshold"] else node["right"]
            prediction += tree[index]["value"]
    else:
        prediction = float(model["intercept"] + ((np.array(x) - np.array(model["mean"])) / np.array(model["scale"])) @ np.array(model["coefficient"]))
    return float(np.clip(features["market_total_centered"] + 45. + prediction, 25., 75.))


def combine_projection(projection: dict, away: dict, home: dict, settings: dict) -> dict:
    result = dict(projection)
    component = {"version": VERSION, "parameter_hash": PARAMETER_HASH, "minimum_edge": 4., "ready": False}
    try:
        features = feature_vector(away, home, settings)
        tree = predict_model(PARAMETERS["tree"], features)
        regression = predict_model(PARAMETERS["regression"], features)
        market = features["market_total_centered"] + 45.
        edge, confirmation = tree - market, regression - market
        margin = finite(projection.get("margin"))
        if tree < abs(margin):
            raise ValueError("Independent margin and total imply a negative score")
        component.update(ready=True, features=features, tree_total=tree, regression_total=regression,
            edge=edge, regression_edge=confirmation, agreement=edge * confirmation > 0,
            input_cutoff=str(home["Spread Input Cutoff"]), season=int(home["Season"]), week=int(home["Projection Week"]))
        adjustment = float(settings.get("manual_total_adjustment", 0.) or 0.)
        total = tree + adjustment
        if abs(margin) > total:
            raise ValueError("The manual total override implies a negative score")
        result.update(total=total, home_score=(total + margin) / 2., away_score=(total - margin) / 2.)
        if adjustment:
            component.update(ready=False, reason="Manual total override lies outside the frozen totals policy")
    except (ValueError, TypeError, KeyError, IndexError) as error:
        component.update(ready=False, reason=str(error))
    result["total_component"] = component
    return result


def grade_projection(projection: dict) -> str:
    component = projection.get("total_component", {})
    if component.get("version") != VERSION or component.get("parameter_hash") != PARAMETER_HASH or component.get("ready") is not True:
        return "No Play (total inputs unavailable)"
    try:
        tree, regression = finite(component["tree_total"]), finite(component["regression_total"])
        market = finite(component["features"]["market_total_centered"]) + 45.
        edge, confirmation = finite(component["edge"]), finite(component["regression_edge"])
        if not (math.isclose(finite(projection["total"]), tree, abs_tol=1e-9)
                and math.isclose(edge, tree - market, abs_tol=1e-9)
                and math.isclose(confirmation, regression - market, abs_tol=1e-9)
                and component.get("agreement") is (edge * confirmation > 0)):
            return "No Play (total inputs unavailable)"
    except (ValueError, TypeError, KeyError):
        return "No Play (total inputs unavailable)"
    return ("Over" if edge > 0 else "Under") if abs(edge) >= 4. and edge * confirmation > 0 else "Non-Edge Total"


def storage_version(base: str, projection: dict) -> str:
    return base + "|" + VERSION if projection.get("total_component", {}).get("version") == VERSION else base


def storage_fields(projection: dict) -> dict:
    component = projection.get("total_component")
    return {"Total Model Version": VERSION, "Total Model JSON": json.dumps(component, sort_keys=True, separators=(",", ":"))} if component else {}


def install_independent_total_layer(builder):
    if getattr(builder, "_INDEPENDENT_TOTAL_LAYER_INSTALLED", False):
        return
    base_project, base_simulate = builder._project_matchup, builder._simulate_game
    builder.SLATE_COLUMNS[:] = list(dict.fromkeys([*builder.SLATE_COLUMNS, *SLATE_FIELDS]))
    builder.TRACKER_COLUMNS[:] = list(dict.fromkeys([*builder.TRACKER_COLUMNS, *SLATE_FIELDS]))
    def project(away, home, away_lineup, home_lineup, settings):
        return combine_projection(base_project(away, home, away_lineup, home_lineup, settings), away, home, settings)
    def simulate(projection, *args, **kwargs):
        result = base_simulate(projection, *args, **kwargs)
        # A separate totals simulation must not keep the old total's Over odds.
        independent = regression_simulate(projection, *args, **kwargs)
        result["over"] = independent["over"]
        return result
    builder._project_matchup, builder._simulate_game = project, simulate
    builder._INDEPENDENT_TOTAL_LAYER_INSTALLED = True
