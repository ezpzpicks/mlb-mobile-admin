"""Evaluate the fixed 25% generic-HFA challenger on every completed 2026 FBS-vs-FBS game available.

No tuning is performed on 2026. The challenger was selected previously from 2024.
Sportsbook spreads are used only after the independent football projection is made,
for ATS grading and fixed edge checkpoints.
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

from research import cfb_margin_component_weight_edge_search as cw

OUT = Path("research/results")
OUT.mkdir(parents=True, exist_ok=True)
SEASON = 2026
HFA_MULT = 0.25
THRESHOLDS = (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0)


def grade(df: pd.DataFrame, pred: np.ndarray) -> dict:
    pred = np.asarray(pred, float)
    actual = df.actual.to_numpy(float)
    market_spread = df.market_home_spread.to_numpy(float)
    edge = pred + market_spread
    market_miss = actual + market_spread

    # Exclude exact no-edge projections and pushes for ATS win-rate calculation.
    base_ok = np.isfinite(edge) & np.isfinite(market_miss)
    active = base_ok & (np.abs(edge) > 1e-9)
    settled = active & (np.abs(market_miss) > 1e-9)

    e = edge[settled]
    miss = market_miss[settled]
    wins = e * miss > 0

    thresholds = []
    for t in THRESHOLDS:
        sel = np.abs(e) >= t if t > 0 else np.ones(len(e), dtype=bool)
        n = int(sel.sum())
        w = int(wins[sel].sum())
        thresholds.append({
            "threshold": t,
            "n": n,
            "wins": w,
            "losses": n - w,
            "win_rate": (w / n) if n else None,
        })

    pushes = int((active & (np.abs(market_miss) <= 1e-9)).sum())
    no_edge = int((base_ok & (np.abs(edge) <= 1e-9)).sum())
    return {
        "games_with_market": int(base_ok.sum()),
        "graded_bets": int(settled.sum()),
        "pushes": pushes,
        "no_edge": no_edge,
        "mae": float(np.mean(np.abs(pred[base_ok] - actual[base_ok]))) if base_ok.any() else None,
        "thresholds": thresholds,
    }


def main() -> None:
    df = cw.ens.season_rows(SEASON)
    if df.empty:
        raise RuntimeError("No 2026 FBS-vs-FBS rows with market spread were recovered")

    baseline_pred = cw.reconstruct(df)
    challenger_pred = cw.reconstruct(df, {"home_indicator": HFA_MULT})

    baseline = grade(df, baseline_pred)
    challenger = grade(df, challenger_pred)

    out = {
        "season": SEASON,
        "population": "completed FBS-vs-FBS games with recovered closing/home spread",
        "n_rows": int(len(df)),
        "production_home_indicator_coefficient": float(cw.COMP["home_indicator"]),
        "challenger_home_indicator_multiplier": HFA_MULT,
        "challenger_home_indicator_coefficient": float(cw.COMP["home_indicator"] * HFA_MULT),
        "baseline": baseline,
        "challenger": challenger,
    }
    (OUT / "cfb_hfa25_2026_full_season_audit.json").write_text(json.dumps(out, indent=2, allow_nan=False))

    lines = [
        "# 2026 Full-Season HFA25 ATS Audit",
        "",
        f"Completed FBS-vs-FBS games with recovered spread: {len(df)}",
        f"Production generic home coefficient: {cw.COMP['home_indicator']:.4f}",
        f"Challenger generic home coefficient: {cw.COMP['home_indicator'] * HFA_MULT:.4f}",
        "",
        "| Edge | Production | HFA25 challenger |",
        "|---:|---:|---:|",
    ]
    for b, c in zip(baseline["thresholds"], challenger["thresholds"]):
        label = "All" if b["threshold"] == 0 else f"{b['threshold']:.0f}+"
        br = "n/a" if b["win_rate"] is None else f"{b['wins']}-{b['losses']} ({100*b['win_rate']:.1f}%)"
        cr = "n/a" if c["win_rate"] is None else f"{c['wins']}-{c['losses']} ({100*c['win_rate']:.1f}%)"
        lines.append(f"| {label} | {br} | {cr} |")
    lines += [
        "",
        f"Production pushes: {baseline['pushes']}; challenger pushes: {challenger['pushes']}",
        f"Production no-edge games: {baseline['no_edge']}; challenger no-edge games: {challenger['no_edge']}",
        f"Production margin MAE: {baseline['mae']:.3f}",
        f"Challenger margin MAE: {challenger['mae']:.3f}",
    ]
    (OUT / "CFB_HFA25_2026_FULL_SEASON_AUDIT.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
