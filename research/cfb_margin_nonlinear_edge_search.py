"""Nonlinear football-only CFB margin residual search.

Goal: test whether conditional interactions among leakage-safe pregame football
statistics improve ATS edge ordering without using sportsbook lines as predictors.

Protocol
--------
* Train nonlinear residual models on 2021-2023 actual margins only.
* Select model/hyperparameters/correction cap on 2024 using fixed 2/4/6/8/10
  ATS edge progression. The market spread is evaluation-only.
* Freeze that specification.
* Refit on 2021-2024 and test untouched 2025.
* Refit the exact same specification on 2021-2025 and test 2026 with zero retuning.

The prediction target is actual_margin - existing independent spread_margin, so
all challengers are incremental corrections to the production-style football
projection. Sportsbook spread/total are never model features.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import (
    ExtraTreesRegressor,
    GradientBoostingRegressor,
    HistGradientBoostingRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from research import cfb_margin_edge_quality_search as eq

base = eq.base
base.METRIC_DEFAULTS.setdefault("Power Success", 0.68)
# Extend the same leakage-safe weekly builder one season farther for the final
# frozen 2026 confirmation.
base.HOLDOUT_SEASON = 2026

OUT = Path("research/results")
OUT.mkdir(parents=True, exist_ok=True)
THRESHOLDS = (2.0, 4.0, 6.0, 8.0, 10.0)
TRAIN = (2021, 2022, 2023)
VALID = 2024
HOLDOUT = 2025
FINAL = 2026
CAPS = (2.0, 4.0, 6.0, 8.0)

# All features below are built from pregame football information only.  The
# advanced/balance/context modules imported by eq chain their build_feature_row
# wrappers, so the union is available in one leakage-safe data set.
FEATURES: list[str] = sorted(
    set(base.BASE_FEATURES)
    | set(base.EFFICIENCY_FEATURES)
    | set(base.INTERACTION_FEATURES)
    | {f for fs in eq.FAMILIES.values() for f in fs}
    | {
        "prior_ppg_diff",
        "prior_papg_diff",
        "prior_power_margin",
        "current_scoring_diff",
        "current_allowed_diff",
        "current_power_margin",
        "home_indicator",
    }
)

# Keep the grid intentionally small and regularized.  We are testing whether
# conditional relationships exist, not performing an unconstrained parameter hunt.
MODEL_SPECS: list[dict[str, Any]] = [
    {"kind": "hgb", "depth": 2, "leaf": 30, "lr": 0.035, "iters": 140, "l2": 10.0},
    {"kind": "hgb", "depth": 3, "leaf": 30, "lr": 0.035, "iters": 140, "l2": 15.0},
    {"kind": "hgb", "depth": 2, "leaf": 20, "lr": 0.05, "iters": 120, "l2": 20.0},
    {"kind": "gbr", "depth": 1, "leaf": 30, "lr": 0.04, "iters": 160, "loss": "huber"},
    {"kind": "gbr", "depth": 2, "leaf": 30, "lr": 0.03, "iters": 140, "loss": "huber"},
    {"kind": "gbr", "depth": 2, "leaf": 40, "lr": 0.04, "iters": 120, "loss": "squared_error"},
    {"kind": "extra", "depth": 5, "leaf": 20, "trees": 350, "max_features": 0.70},
    {"kind": "extra", "depth": 7, "leaf": 20, "trees": 350, "max_features": 0.70},
    {"kind": "extra", "depth": 6, "leaf": 30, "trees": 350, "max_features": 1.00},
]


def finite(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    if isinstance(value, tuple):
        return [finite(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        x = float(value)
        return x if math.isfinite(x) else None
    return value


def fbs_ids(season: int) -> set[str]:
    try:
        games = base.cfb._parse_games(base.cfb._espn_games_payload(int(season)), int(season))
        if games is None or games.empty:
            return set()
        mask = (
            games["Away Classification"].astype(str).str.lower().eq("fbs")
            & games["Home Classification"].astype(str).str.lower().eq("fbs")
        )
        return set(games.loc[mask, "Game ID"].astype(str))
    except Exception as exc:
        print("FBS filter failed", season, exc)
        return set()


def make_model(spec: dict[str, Any]) -> Pipeline:
    kind = spec["kind"]
    if kind == "hgb":
        reg = HistGradientBoostingRegressor(
            max_depth=int(spec["depth"]),
            min_samples_leaf=int(spec["leaf"]),
            learning_rate=float(spec["lr"]),
            max_iter=int(spec["iters"]),
            l2_regularization=float(spec["l2"]),
            random_state=20261002,
        )
    elif kind == "gbr":
        reg = GradientBoostingRegressor(
            max_depth=int(spec["depth"]),
            min_samples_leaf=int(spec["leaf"]),
            learning_rate=float(spec["lr"]),
            n_estimators=int(spec["iters"]),
            loss=str(spec["loss"]),
            random_state=20261002,
        )
    elif kind == "extra":
        reg = ExtraTreesRegressor(
            n_estimators=int(spec["trees"]),
            max_depth=int(spec["depth"]),
            min_samples_leaf=int(spec["leaf"]),
            max_features=float(spec["max_features"]),
            random_state=20261002,
            n_jobs=-1,
        )
    else:
        raise ValueError(kind)
    return Pipeline([("impute", SimpleImputer(strategy="median")), ("model", reg)])


def prepare(frame: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    out = frame.reindex(columns=features).copy()
    for col in out.columns:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.replace([np.inf, -np.inf], np.nan)
    return out


def auc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    scores = np.asarray(scores, float)
    labels = np.asarray(labels, int)
    n1 = int((labels == 1).sum())
    n0 = int((labels == 0).sum())
    if not n1 or not n0:
        return None
    ranks = pd.Series(scores).rank(method="average").to_numpy(float)
    return float((ranks[labels == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def edge_metrics(frame: pd.DataFrame, pred: np.ndarray) -> dict[str, Any]:
    pred = np.asarray(pred, float)
    actual = pd.to_numeric(frame["actual_margin"], errors="coerce").to_numpy(float)
    hs = pd.to_numeric(frame["market_home_spread"], errors="coerce").to_numpy(float)
    edge = pred + hs
    miss = actual + hs
    valid = np.isfinite(edge) & np.isfinite(miss) & (np.abs(edge) > 1e-9) & (np.abs(miss) > 1e-9)
    e = edge[valid]
    m = miss[valid]
    win = (e * m > 0).astype(int)
    rates: list[dict[str, Any]] = []
    for threshold in THRESHOLDS:
        sel = np.abs(e) >= threshold
        n = int(sel.sum())
        w = int(win[sel].sum())
        rates.append({
            "threshold": threshold,
            "n": n,
            "wins": w,
            "losses": n - w,
            "win_rate": (w / n) if n else None,
        })
    usable = [r for r in rates if r["n"] >= 25 and r["win_rate"] is not None]
    steps = sum(b["win_rate"] >= a["win_rate"] for a, b in zip(usable, usable[1:]))
    violation = sum(max(0.0, a["win_rate"] - b["win_rate"]) for a, b in zip(usable, usable[1:]))
    slope = usable[-1]["win_rate"] - usable[0]["win_rate"] if len(usable) >= 2 else -1.0
    high = next((r["win_rate"] for r in rates if r["threshold"] == 10.0 and r["n"] >= 25), None)
    return {
        "n": int(len(e)),
        "auc": auc(np.abs(e), win),
        "corr": float(np.corrcoef(e, m)[0, 1]) if len(e) > 2 else None,
        "mae": float(np.nanmean(np.abs(pred - actual))),
        "monotonic_steps": int(steps),
        "monotonic_possible": max(0, len(usable) - 1),
        "violation_sum": float(violation),
        "slope_2_to_10": float(slope),
        "high_edge_win_rate": high,
        "thresholds": rates,
    }


def selection_key(m: dict[str, Any]) -> tuple:
    # Match the user's stated objective: larger edge should mean stronger record.
    return (
        m["monotonic_steps"],
        -m["violation_sum"],
        m["slope_2_to_10"],
        m["high_edge_win_rate"] if m["high_edge_win_rate"] is not None else -1.0,
        m["auc"] if m["auc"] is not None else -1.0,
        m["corr"] if m["corr"] is not None else -1.0,
    )


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, spec: dict[str, Any], cap: float, features: list[str]) -> np.ndarray:
    x_train = prepare(train, features)
    x_test = prepare(test, features)
    y = (
        pd.to_numeric(train["actual_margin"], errors="coerce")
        - pd.to_numeric(train["spread_margin"], errors="coerce")
    ).to_numpy(float)
    model = make_model(spec)
    model.fit(x_train, y)
    correction = np.asarray(model.predict(x_test), float)
    correction = np.clip(correction, -float(cap), float(cap))
    return pd.to_numeric(test["spread_margin"], errors="coerce").to_numpy(float) + correction


def spec_name(spec: dict[str, Any]) -> str:
    parts = [str(spec["kind"])]
    for key in sorted(k for k in spec if k != "kind"):
        parts.append(f"{key}={spec[key]}")
    return ",".join(parts)


def main() -> None:
    print("building 2021-2026 leakage-safe dataset")
    df = base.build_dataset().copy()
    df = eq.attach_market_spread(df, base.cfb)
    for col in ("market_home_spread", "actual_margin", "spread_margin"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    ids = {season: fbs_ids(season) for season in (*TRAIN, VALID, HOLDOUT, FINAL)}
    if all(ids.values()):
        df = df[df.apply(lambda r: str(r.game_id) in ids.get(int(r.season), set()), axis=1)].copy()
    df = df.dropna(subset=["market_home_spread", "actual_margin", "spread_margin"]).reset_index(drop=True)

    # Drop feature columns that are entirely absent.  Missing values inside an
    # otherwise usable pregame feature are median-imputed inside each training fit.
    features = [f for f in FEATURES if f in df.columns and pd.to_numeric(df[f], errors="coerce").notna().any()]
    excluded = sorted(set(FEATURES) - set(features))
    print("feature_count", len(features), "excluded", excluded)

    tr = df[df.season.isin(TRAIN)].copy()
    va = df[df.season == VALID].copy()
    ho = df[df.season == HOLDOUT].copy()
    final = df[df.season == FINAL].copy()
    print("rows", len(tr), len(va), len(ho), len(final))
    if min(len(tr), len(va), len(ho), len(final)) == 0:
        raise RuntimeError("One or more protocol seasons have zero rows")

    baseline = {
        "2024": edge_metrics(va, va.spread_margin.to_numpy(float)),
        "2025": edge_metrics(ho, ho.spread_margin.to_numpy(float)),
        "2026": edge_metrics(final, final.spread_margin.to_numpy(float)),
    }

    candidates: list[dict[str, Any]] = []
    for spec in MODEL_SPECS:
        for cap in CAPS:
            pred = fit_predict(tr, va, spec, cap, features)
            met = edge_metrics(va, pred)
            candidates.append({"spec": spec, "cap": cap, "validation": met})
            print("candidate", spec_name(spec), "cap", cap, "key", selection_key(met))
    candidates.sort(key=lambda r: selection_key(r["validation"]), reverse=True)
    chosen = candidates[0]
    print("chosen", spec_name(chosen["spec"]), "cap", chosen["cap"])

    train_25 = pd.concat([tr, va], ignore_index=True)
    pred_25 = fit_predict(train_25, ho, chosen["spec"], chosen["cap"], features)
    chosen_25 = edge_metrics(ho, pred_25)

    # Zero retuning: same model family/hyperparameters/cap; only allow the model
    # to learn from the newly completed 2025 season before forecasting 2026.
    train_26 = pd.concat([tr, va, ho], ignore_index=True)
    pred_26 = fit_predict(train_26, final, chosen["spec"], chosen["cap"], features)
    chosen_26 = edge_metrics(final, pred_26)

    result = {
        "protocol": {
            "train": list(TRAIN),
            "validation_selection": VALID,
            "holdout_1": HOLDOUT,
            "holdout_2": FINAL,
            "market_predictor": False,
            "target": "actual_margin - independent spread_margin",
            "fixed_edge_thresholds": list(THRESHOLDS),
            "selection": "2024 monotonic steps, violations, 2-to-10 slope, 10+ WR, AUC, corr",
            "2026_retuned": False,
        },
        "rows": {"train": len(tr), "2024": len(va), "2025": len(ho), "2026": len(final)},
        "features": features,
        "excluded_features": excluded,
        "baseline": baseline,
        "chosen_on_2024": {
            "spec": chosen["spec"],
            "cap": chosen["cap"],
            "validation": chosen["validation"],
            "holdout_2025": chosen_25,
            "holdout_2026": chosen_26,
        },
        "candidates_2024": candidates,
    }
    (OUT / "cfb_margin_nonlinear_edge_search.json").write_text(json.dumps(finite(result), indent=2, allow_nan=False))

    def rate_text(m: dict[str, Any], threshold: float) -> str:
        r = next(x for x in m["thresholds"] if x["threshold"] == threshold)
        return f"{r['wins']}-{r['losses']} ({100*r['win_rate']:.1f}%)" if r["win_rate"] is not None else "n/a"

    lines = [
        "# CFB Nonlinear Margin Edge Search",
        "",
        f"Rows: train={len(tr)}, 2024={len(va)}, 2025={len(ho)}, 2026={len(final)}",
        f"Football-only feature count: {len(features)}",
        f"Chosen on 2024: `{spec_name(chosen['spec'])}`, correction cap={chosen['cap']:.0f}",
        "",
        "Sportsbook spreads were used only to evaluate fixed ATS edge checkpoints and never entered the prediction feature matrix.",
        "",
        "| Season | Model | Steps | 2→10 slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for year, base_m, cand_m in [
        (2024, baseline["2024"], chosen["validation"]),
        (2025, baseline["2025"], chosen_25),
        (2026, baseline["2026"], chosen_26),
    ]:
        for label, m in (("Baseline", base_m), ("Nonlinear", cand_m)):
            lines.append(
                f"| {year} | {label} | {m['monotonic_steps']}/{m['monotonic_possible']} | "
                f"{100*m['slope_2_to_10']:+.1f}pp | {m['auc']:.3f} | {m['mae']:.3f} | "
                + " | ".join(rate_text(m, t) for t in THRESHOLDS)
                + " |"
            )
    lines += ["", "## Top 2024-selected candidates", "", "| Model | Cap | Steps | slope | 10+ | AUC | MAE |", "|---|---:|---:|---:|---:|---:|---:|"]
    for row in candidates[:12]:
        m = row["validation"]
        lines.append(
            f"| {spec_name(row['spec'])} | {row['cap']:.0f} | {m['monotonic_steps']}/{m['monotonic_possible']} | "
            f"{100*m['slope_2_to_10']:+.1f}pp | {100*(m['high_edge_win_rate'] or 0):.1f}% | {m['auc']:.3f} | {m['mae']:.3f} |"
        )
    (OUT / "CFB_MARGIN_NONLINEAR_EDGE_SEARCH.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
