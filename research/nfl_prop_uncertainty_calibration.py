"""Check NFL yardage probability SD calibration on the 2025 holdout.

This is research-only. It compares the live generic SD formula with empirical
residual dispersion from the existing leakage-safe RB/WR regression models.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from research import nfl_rb_wr_prop_regression as base
from research.nfl_rb_wr_efficiency_calibration import MARKETS


def current_sd(market_name: str, projection: np.ndarray) -> np.ndarray:
    base_sd = 23.0 if market_name == "rb_rushing_yards" else 25.0
    return np.maximum(base_sd, projection * 0.34)


def build_predictions(data, cfg):
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
    needed = list(set(opp["features"] + eff["features"] + [cfg["actual_yards"]]))
    usable = holdout.dropna(subset=needed).copy()
    pred_opp = np.clip(base.predict(opp, usable), *cfg["opp_clip"])
    pred_eff = np.clip(base.predict(eff, usable), *cfg["eff_clip"])
    projection = pred_opp * pred_eff
    actual = usable[cfg["actual_yards"]].to_numpy(float)
    return projection, actual


def coverage(residual: np.ndarray, sd: np.ndarray, multiplier: float) -> dict:
    scaled = sd * multiplier
    z = np.abs(residual) / np.maximum(scaled, 1e-9)
    return {
        "sd_multiplier": float(multiplier),
        "mean_sd": float(np.mean(scaled)),
        "rms_standardized_residual": float(np.sqrt(np.mean((residual / np.maximum(scaled, 1e-9)) ** 2))),
        "coverage_1sd": float(np.mean(z <= 1.0)),
        "coverage_90": float(np.mean(z <= 1.6448536269514722)),
        "coverage_95": float(np.mean(z <= 1.959963984540054)),
        "gaussian_nll_no_constant": float(np.mean(np.log(np.maximum(scaled, 1e-9)) + 0.5 * (residual / np.maximum(scaled, 1e-9)) ** 2)),
    }


def evaluate(name, data, cfg):
    projection, actual = build_predictions(data, cfg)
    residual = actual - projection
    abs_resid = np.abs(residual)
    sd = current_sd(name, projection)
    mle_scale = float(np.sqrt(np.mean((residual / np.maximum(sd, 1e-9)) ** 2)))
    fixed_rmse = float(np.sqrt(np.mean(residual ** 2)))
    fixed = np.full_like(sd, fixed_rmse)
    q = {str(p): float(np.quantile(abs_resid, p)) for p in [0.50, 0.68, 0.80, 0.90, 0.95]}
    quantiles = np.quantile(projection, [0, .25, .5, .75, 1])
    bins=[]
    for i in range(4):
        lo,hi=float(quantiles[i]),float(quantiles[i+1])
        mask=(projection>=lo)&(projection<hi if i<3 else projection<=hi)
        rr=residual[mask]; pp=projection[mask]
        bins.append({"lo":lo,"hi":hi,"n":int(mask.sum()),"mean_projection":float(np.mean(pp)),"mae":float(np.mean(np.abs(rr))),"rmse":float(np.sqrt(np.mean(rr**2)))})
    return {
        "n": int(len(actual)), "mae": float(np.mean(abs_resid)), "rmse": fixed_rmse,
        "abs_residual_quantiles": q,
        "current_formula": coverage(residual, sd, 1.0),
        "mle_scaled_current_formula": coverage(residual, sd, mle_scale),
        "mle_scale_for_current_formula": mle_scale,
        "fixed_rmse_sd": coverage(residual, fixed, 1.0),
        "projection_quartiles": bins,
    }


def main():
    data=base.build_dataset()
    results={name:evaluate(name,data,cfg) for name,cfg in MARKETS.items()}
    out=Path("artifacts/nfl_prop_uncertainty_calibration");out.mkdir(parents=True,exist_ok=True)
    (out/"results.json").write_text(json.dumps(results,indent=2,sort_keys=True))
    print("NFL_UNCERTAINTY_CALIBRATION "+json.dumps(results,sort_keys=True))

if __name__=="__main__":
    main()
