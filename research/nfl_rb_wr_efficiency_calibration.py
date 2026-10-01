"""Diagnostic calibration of RB/WR opportunity and efficiency components.

Uses the existing leakage-safe dataset/model specification, then tests whether
blending fitted opportunity/efficiency back toward their raw lagged estimates
improves 2025 yardage MAE. This is research-only and does not modify production.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from research import nfl_rb_wr_prop_regression as base

BLENDS = [0.0, 0.25, 0.5, 0.75, 1.0]
COMMON_CONTEXT = ["team_total", "team_spread", "home"]

MARKETS = {
    "rb_rushing_yards": {
        "position": "RB", "kind": "rush", "opp_target": "carries", "eff_target": "actual_ypc",
        "raw_opp": "raw_rb_carries", "raw_eff": "raw_rb_ypc", "role_filter": "raw_rb_carries",
        "role_min": 3.0, "eff_min": 3.0, "opp_clip": (0.0, 35.0), "eff_clip": (2.0, 7.0),
        "actual_yards": "rushing_yards",
        "opp_candidates": ["raw_rb_carries", "carries_avg3", "carries_avg8", "carry_share_avg3", "carry_share_avg8", "team_rush_avg3", "team_rush_avg8", "opp_carries_avg8"] + COMMON_CONTEXT,
        "eff_candidates": ["raw_rb_ypc", "ypc8", "rush_epa_per_carry8", "opp_ypc8"] + COMMON_CONTEXT,
    },
    "rb_receiving_yards": {
        "position": "RB", "kind": "receive", "opp_target": "targets", "eff_target": "actual_ypt",
        "raw_opp": "raw_targets", "raw_eff": "raw_ypt", "role_filter": "raw_targets",
        "role_min": 1.5, "eff_min": 1.0, "opp_clip": (0.0, 15.0), "eff_clip": (2.5, 12.0),
        "actual_yards": "receiving_yards",
        "opp_candidates": ["raw_targets", "targets_avg3", "targets_avg8", "target_share_avg3", "target_share_avg8", "team_targets_avg3", "team_targets_avg8", "opp_targets_avg8"] + COMMON_CONTEXT,
        "eff_candidates": ["raw_ypt", "ypt8", "rec_epa_per_target8", "air_yards_per_target8", "yac_per_reception8", "opp_ypt8"] + COMMON_CONTEXT,
    },
    "wr_receiving_yards": {
        "position": "WR", "kind": "receive", "opp_target": "targets", "eff_target": "actual_ypt",
        "raw_opp": "raw_targets", "raw_eff": "raw_ypt", "role_filter": "raw_targets",
        "role_min": 2.5, "eff_min": 2.0, "opp_clip": (0.0, 20.0), "eff_clip": (3.0, 16.0),
        "actual_yards": "receiving_yards",
        "opp_candidates": ["raw_targets", "targets_avg3", "targets_avg8", "target_share_avg3", "target_share_avg8", "team_targets_avg3", "team_targets_avg8", "opp_targets_avg8"] + COMMON_CONTEXT,
        "eff_candidates": ["raw_ypt", "ypt8", "rec_epa_per_target8", "air_yards_per_target8", "yac_per_reception8", "opp_ypt8"] + COMMON_CONTEXT,
    },
}


def mae(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.mean(np.abs(actual - predicted)))


def evaluate_market(data, cfg):
    subset = data[data["position"] == cfg["position"]].copy()
    subset = subset[np.isfinite(np.asarray(subset[cfg["role_filter"]], dtype=float))]
    subset = subset[subset[cfg["role_filter"]] >= cfg["role_min"]].copy()
    discovery = subset[subset["season"].isin(base.DISCOVERY)].copy()
    confirmation = subset[subset["season"].isin(base.CONFIRMATION)].copy()
    holdout = subset[subset["season"].isin(base.HOLDOUT)].copy()

    opp_disc = discovery.dropna(subset=[cfg["opp_target"]] + cfg["opp_candidates"])
    opp_conf = confirmation.dropna(subset=[cfg["opp_target"]] + cfg["opp_candidates"])
    eff_disc = discovery[discovery[cfg["opp_target"]] >= cfg["eff_min"]].dropna(subset=[cfg["eff_target"]] + cfg["eff_candidates"])
    eff_conf = confirmation[confirmation[cfg["opp_target"]] >= cfg["eff_min"]].dropna(subset=[cfg["eff_target"]] + cfg["eff_candidates"])
    opp = base.select_model(opp_disc, opp_conf, cfg["opp_target"], cfg["opp_candidates"], {cfg["raw_opp"]})
    eff = base.select_model(eff_disc, eff_conf, cfg["eff_target"], cfg["eff_candidates"], {cfg["raw_eff"]})

    needed = list(set(opp["features"] + eff["features"] + [cfg["raw_opp"], cfg["raw_eff"], cfg["actual_yards"], cfg["eff_target"], cfg["opp_target"]]))
    usable = holdout.dropna(subset=needed).copy()
    pred_opp = np.clip(base.predict(opp, usable), *cfg["opp_clip"])
    pred_eff = np.clip(base.predict(eff, usable), *cfg["eff_clip"])
    raw_opp = np.clip(usable[cfg["raw_opp"]].to_numpy(float), *cfg["opp_clip"])
    raw_eff = np.clip(usable[cfg["raw_eff"]].to_numpy(float), *cfg["eff_clip"])
    actual = usable[cfg["actual_yards"]].to_numpy(float)

    efficiency_grid = []
    opportunity_grid = []
    full_grid = []
    for eff_raw_share in BLENDS:
        e = (1.0 - eff_raw_share) * pred_eff + eff_raw_share * raw_eff
        efficiency_grid.append({"raw_eff_share": eff_raw_share, "yardage_mae": mae(actual, pred_opp * e)})
    for opp_raw_share in BLENDS:
        o = (1.0 - opp_raw_share) * pred_opp + opp_raw_share * raw_opp
        opportunity_grid.append({"raw_opp_share": opp_raw_share, "yardage_mae": mae(actual, o * pred_eff)})
    for opp_raw_share in BLENDS:
        o = (1.0 - opp_raw_share) * pred_opp + opp_raw_share * raw_opp
        for eff_raw_share in BLENDS:
            e = (1.0 - eff_raw_share) * pred_eff + eff_raw_share * raw_eff
            full_grid.append({"raw_opp_share": opp_raw_share, "raw_eff_share": eff_raw_share, "yardage_mae": mae(actual, o * e)})
    efficiency_grid.sort(key=lambda x: x["yardage_mae"])
    opportunity_grid.sort(key=lambda x: x["yardage_mae"])
    full_grid.sort(key=lambda x: x["yardage_mae"])

    # Check whether fitted efficiency is especially harmful for historically high-efficiency players.
    q75 = float(np.quantile(raw_eff, 0.75)); q90 = float(np.quantile(raw_eff, 0.90))
    high = {}
    for label, cutoff in [("top25_raw_eff", q75), ("top10_raw_eff", q90)]:
        mask = raw_eff >= cutoff
        high[label] = {
            "n": int(mask.sum()), "cutoff": cutoff,
            "regression_eff_yardage_mae": mae(actual[mask], (pred_opp * pred_eff)[mask]),
            "raw_eff_yardage_mae": mae(actual[mask], (pred_opp * raw_eff)[mask]),
            "blend25_raw_eff_yardage_mae": mae(actual[mask], (pred_opp * (0.75 * pred_eff + 0.25 * raw_eff))[mask]),
            "blend50_raw_eff_yardage_mae": mae(actual[mask], (pred_opp * (0.50 * pred_eff + 0.50 * raw_eff))[mask]),
        }

    return {
        "n": int(len(usable)),
        "baseline_regression_mae": mae(actual, pred_opp * pred_eff),
        "raw_components_mae": mae(actual, raw_opp * raw_eff),
        "efficiency_grid": efficiency_grid,
        "opportunity_grid": opportunity_grid,
        "best_full_grid": full_grid[:10],
        "high_efficiency_segments": high,
        "selected_efficiency_features": eff["features"],
        "selected_opportunity_features": opp["features"],
    }


def main():
    data = base.build_dataset()
    results = {name: evaluate_market(data, cfg) for name, cfg in MARKETS.items()}
    out = Path("artifacts/nfl_rb_wr_efficiency_calibration")
    out.mkdir(parents=True, exist_ok=True)
    path = out / "results.json"
    path.write_text(json.dumps(results, indent=2, sort_keys=True))
    print("NFL_EFFICIENCY_CALIBRATION " + json.dumps(results, sort_keys=True))


if __name__ == "__main__":
    main()
