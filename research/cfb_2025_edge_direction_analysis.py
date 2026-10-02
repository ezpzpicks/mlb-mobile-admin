"""Analyze 2025 CFB model-vs-market edge direction versus ATS results.

Uses the previously generated leakage-safe 2025 game-edge backtest file.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import pandas as pd
import numpy as np

SOURCE = "https://raw.githubusercontent.com/ezpzpicks/mlb-mobile-admin/research/cfb-2025-grade-backtest/research/results/cfb_2025_game_edges.csv"
OUT = Path("research/results/cfb_2025_edge_direction_analysis.json")
OUT_MD = Path("research/results/CFB_2025_EDGE_DIRECTION_ANALYSIS.md")
OUT.parent.mkdir(parents=True, exist_ok=True)


def rec(df: pd.DataFrame):
    d=df[df.result.isin(["W","L","P"])].copy()
    w=int((d.result=="W").sum()); l=int((d.result=="L").sum()); p=int((d.result=="P").sum())
    n=w+l
    return {
        "bets":len(d),"wins":w,"losses":l,"pushes":p,
        "win_rate":w/n if n else None,
        "model_mae":float(d.model_abs_error.mean()) if len(d) else None,
        "market_mae":float(d.market_abs_error.mean()) if len(d) else None,
        "mean_edge":float(d.edge.mean()) if len(d) else None,
        "mean_abs_edge":float(d.edge.abs().mean()) if len(d) else None,
        "mean_realized_market_miss":float(d.realized_market_miss.mean()) if len(d) else None,
    }


df=pd.read_csv(SOURCE)
df=df[(df.market=="Spread") & (df.fbs_fbs==True)].copy()
df["actual_margin"]=df.actual_home-df.actual_away
# line is home spread. Market-implied home margin is negative of home spread.
df["market_margin"]=-df.line
df["model_error"]=df.projected_margin-df.actual_margin
df["market_error"]=df.market_margin-df.actual_margin
df["model_abs_error"]=df.model_error.abs()
df["market_abs_error"]=df.market_error.abs()
# The amount/direction the closing market missed the actual margin.
df["realized_market_miss"]=df.actual_margin-df.market_margin
# Reconstruct signed model edge from model margin vs market margin. Positive=home, negative=away.
df["signed_edge"]=df.projected_margin-df.market_margin
# Verify it matches stored pick direction/absolute edge.
df["sign_correct"]=(np.sign(df.signed_edge)==np.sign(df.realized_market_miss))
df.loc[df.realized_market_miss.abs()<1e-9,"sign_correct"]=np.nan

results={}
results["overall"]=rec(df)
results["edge_market_miss_corr"]=float(df[["signed_edge","realized_market_miss"]].corr().iloc[0,1])
results["mae_gap_model_minus_market"]=float(df.model_abs_error.mean()-df.market_abs_error.mean())
results["by_result"]={k:rec(df[df.result==k]) for k in ["W","L","P"]}
results["by_edge_threshold"]=[]
for t in [0,0.5,1,1.5,2,3,4,4.5,5,5.5,6,7,8,9,10,10.5,11,12,14,16,20]:
    s=df[df.edge>=t]
    r=rec(s); r["threshold"]=t
    if len(s):
        r["edge_market_miss_corr"]=float(s[["signed_edge","realized_market_miss"]].corr().iloc[0,1]) if len(s)>2 else None
    results["by_edge_threshold"].append(r)
results["by_edge_bin"]=[]
for lo,hi in [(0,.5),(.5,1),(1,1.5),(1.5,2),(2,3),(3,4),(4,5),(5,6),(6,7),(7,8),(8,10),(10,12),(12,16),(16,20),(20,1e9)]:
    s=df[(df.edge>=lo)&(df.edge<hi)]
    r=rec(s); r["bin"]=f"{lo:g}-{hi:g}" if hi<1e8 else f"{lo:g}+"
    results["by_edge_bin"].append(r)
results["by_signed_direction"]={
    "home_edge":rec(df[df.signed_edge>0]),
    "away_edge":rec(df[df.signed_edge<0]),
}
results["by_market_role"]={
    "pick_favorite":rec(df[((df.line<0)&(df.signed_edge>0))|((df.line>0)&(df.signed_edge<0))]),
    "pick_underdog":rec(df[((df.line<0)&(df.signed_edge<0))|((df.line>0)&(df.signed_edge>0))]),
}
# Post-result diagnostics only; not valid pregame filters.
results["post_result_error_direction"]={
    "model_underpredicted_home_margin":rec(df[df.model_error<0]),
    "model_overpredicted_home_margin":rec(df[df.model_error>0]),
    "model_closer_than_market":rec(df[df.model_abs_error<df.market_abs_error]),
    "market_closer_than_model":rec(df[df.market_abs_error<df.model_abs_error]),
    "mae_within_1_point":rec(df[(df.model_abs_error-df.market_abs_error).abs()<=1]),
    "mae_within_3_points":rec(df[(df.model_abs_error-df.market_abs_error).abs()<=3]),
}
results["post_result_model_error_bins"]=[]
for lo,hi in [(0,3),(3,7),(7,10),(10,14),(14,21),(21,28),(28,1e9)]:
    s=df[(df.model_abs_error>=lo)&(df.model_abs_error<hi)]
    r=rec(s); r["bin"]=f"{lo:g}-{hi:g}" if hi<1e8 else f"{lo:g}+"
    results["post_result_model_error_bins"].append(r)

OUT.write_text(json.dumps(results,indent=2,allow_nan=False))

def pct(x): return "—" if x is None else f"{100*x:.1f}%"
lines=[
    "# 2025 CFB edge-direction vs ATS analysis","",
    f"FBS-FBS spread decisions: {results['overall']['bets']}",
    f"Overall record: {results['overall']['wins']}-{results['overall']['losses']}-{results['overall']['pushes']} ({pct(results['overall']['win_rate'])})",
    f"Model margin MAE: {results['overall']['model_mae']:.3f}",
    f"Market-implied margin MAE: {results['overall']['market_mae']:.3f}",
    f"MAE gap (model-market): {results['mae_gap_model_minus_market']:+.3f}",
    f"Correlation(model edge, realized market miss): {results['edge_market_miss_corr']:.3f}",
    "","## Edge thresholds","","| Edge | Record | Win% | Model MAE | Market MAE | Edge↔market-miss corr |","|---:|---:|---:|---:|---:|---:|"
]
for r in results["by_edge_threshold"]:
    if not r["bets"]: continue
    corr=r.get("edge_market_miss_corr")
    lines.append(f"| {r['threshold']:g}+ | {r['wins']}-{r['losses']}-{r['pushes']} | {pct(r['win_rate'])} | {r['model_mae']:.2f} | {r['market_mae']:.2f} | {corr:.3f} |" if corr is not None else f"| {r['threshold']:g}+ | {r['wins']}-{r['losses']}-{r['pushes']} | {pct(r['win_rate'])} | {r['model_mae']:.2f} | {r['market_mae']:.2f} | — |")
lines += ["","## Edge bins","","| Edge bin | Record | Win% | Model MAE | Market MAE |","|---|---:|---:|---:|---:|"]
for r in results["by_edge_bin"]:
    if not r["bets"]: continue
    lines.append(f"| {r['bin']} | {r['wins']}-{r['losses']}-{r['pushes']} | {pct(r['win_rate'])} | {r['model_mae']:.2f} | {r['market_mae']:.2f} |")
lines += ["","## Direction / role"]
for group in ["by_signed_direction","by_market_role"]:
    for k,r in results[group].items():
        lines.append(f"- {k}: {r['wins']}-{r['losses']}-{r['pushes']} ({pct(r['win_rate'])})")
lines += ["","## Post-result diagnostics (not pregame filters)"]
for k,r in results["post_result_error_direction"].items():
    lines.append(f"- {k}: {r['wins']}-{r['losses']}-{r['pushes']} ({pct(r['win_rate'])}), model MAE {r['model_mae']:.2f}, market MAE {r['market_mae']:.2f}")
OUT_MD.write_text("\n".join(lines)+"\n")
print(OUT_MD.read_text())
