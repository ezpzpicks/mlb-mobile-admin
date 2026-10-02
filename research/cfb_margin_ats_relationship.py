"""Analyze CFB margin MAE versus ATS correctness.

Evaluation only: sportsbook spread is never a model input. For each game,
compare the fixed production margin projection against the market-implied home
margin, then grade the side implied by their disagreement.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from builders import cfb_builder as cb
from builders import cfb_game_regression as spread_reg
from research.cfb_total_regression import completed_games, num

SEASONS = (2024, 2025)
RESULTS_DIR = Path("research/results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON = RESULTS_DIR / "cfb_margin_ats_relationship_results.json"
RESULT_MD = RESULTS_DIR / "CFB_MARGIN_ATS_RELATIONSHIP_RESULTS.md"


def make_row(game: pd.Series) -> dict[str, Any]:
    away_base, home_base, _, _ = spread_reg._regression_base(cb, game)
    model_margin = float(home_base - away_base)
    actual_margin = num(game.get("Home Score"), np.nan) - num(game.get("Away Score"), np.nan)
    home_spread = num(game.get("Home Spread"), np.nan)
    market_margin = -home_spread if math.isfinite(home_spread) else np.nan
    edge = model_margin - market_margin if math.isfinite(market_margin) else np.nan
    ats_margin_home = actual_margin + home_spread if math.isfinite(home_spread) else np.nan
    if not math.isfinite(edge) or abs(edge) < 1e-9 or not math.isfinite(ats_margin_home):
        result = "push_or_no_pick"
    elif abs(ats_margin_home) < 1e-9:
        result = "push"
    else:
        pick_home = edge > 0
        home_covered = ats_margin_home > 0
        result = "win" if pick_home == home_covered else "loss"
    return {
        "season": int(num(game.get("Season"), 0)),
        "week": int(num(game.get("Week"), 0)),
        "game_id": str(game.get("Game ID", "")),
        "game": f"{game.get('Away Team')} @ {game.get('Home Team')}",
        "home_spread": home_spread,
        "market_margin": market_margin,
        "model_margin": model_margin,
        "actual_margin": actual_margin,
        "edge": edge,
        "abs_edge": abs(edge) if math.isfinite(edge) else np.nan,
        "model_error": model_margin - actual_margin,
        "market_error": market_margin - actual_margin if math.isfinite(market_margin) else np.nan,
        "model_abs_error": abs(model_margin - actual_margin),
        "market_abs_error": abs(market_margin - actual_margin) if math.isfinite(market_margin) else np.nan,
        "pick": "home" if math.isfinite(edge) and edge > 0 else ("away" if math.isfinite(edge) and edge < 0 else "none"),
        "ats_result": result,
    }


def record(frame: pd.DataFrame) -> dict[str, Any]:
    f = frame[frame["ats_result"].isin(["win", "loss", "push"])].copy()
    wins = int((f["ats_result"] == "win").sum())
    losses = int((f["ats_result"] == "loss").sum())
    pushes = int((f["ats_result"] == "push").sum())
    decisions = wins + losses
    return {
        "bets": int(len(f)), "wins": wins, "losses": losses, "pushes": pushes,
        "win_rate": wins / decisions if decisions else None,
        "roi_at_-110": ((wins * (100.0 / 110.0)) - losses) / decisions if decisions else None,
        "model_mae": float(f["model_abs_error"].mean()) if len(f) else None,
        "market_mae": float(f["market_abs_error"].mean()) if len(f) else None,
        "avg_abs_edge": float(f["abs_edge"].mean()) if len(f) else None,
    }


def summarize_edges(frame: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for threshold in [0, 1, 2, 3, 4, 5, 7, 10, 14]:
        s = frame[frame["abs_edge"] >= threshold]
        out.append({"threshold": threshold, **record(s)})
    return out


def summarize_bins(frame: pd.DataFrame) -> list[dict[str, Any]]:
    bins = [(0,1),(1,2),(2,3),(3,4),(4,5),(5,7),(7,10),(10,14),(14,1e9)]
    out=[]
    for lo, hi in bins:
        s=frame[(frame["abs_edge"]>=lo)&(frame["abs_edge"]<hi)]
        out.append({"edge_bin": f"{lo:g}-{hi:g}" if hi < 1e8 else f"{lo:g}+", **record(s)})
    return out


def summarize_error_direction(frame: pd.DataFrame) -> list[dict[str, Any]]:
    # Post-result diagnostic: tells us which direction the model tended to miss
    # and whether its pregame side selection was still correct.
    definitions = {
        "model_underestimated_home_margin": frame["model_error"] < 0,
        "model_overestimated_home_margin": frame["model_error"] > 0,
        "model_closer_than_market": frame["model_abs_error"] < frame["market_abs_error"],
        "market_closer_than_model": frame["market_abs_error"] < frame["model_abs_error"],
        "model_and_market_within_1_mae": (frame["model_abs_error"] - frame["market_abs_error"]).abs() <= 1,
        "model_and_market_within_3_mae": (frame["model_abs_error"] - frame["market_abs_error"]).abs() <= 3,
    }
    out=[]
    for name, mask in definitions.items():
        out.append({"segment": name, **record(frame[mask])})
    return out


def summarize_pick_direction(frame: pd.DataFrame) -> list[dict[str, Any]]:
    out=[]
    for label, mask in {
        "model_more_home_than_market": frame["edge"] > 0,
        "model_more_away_than_market": frame["edge"] < 0,
        "market_home_favorite": frame["home_spread"] < 0,
        "market_home_dog": frame["home_spread"] > 0,
        "pick_market_favorite": ((frame["home_spread"] < 0)&(frame["edge"] > 0)) | ((frame["home_spread"] > 0)&(frame["edge"] < 0)),
        "pick_market_underdog": ((frame["home_spread"] < 0)&(frame["edge"] < 0)) | ((frame["home_spread"] > 0)&(frame["edge"] > 0)),
    }.items():
        out.append({"segment": label, **record(frame[mask])})
    return out


def summarize_mae_buckets(frame: pd.DataFrame) -> list[dict[str, Any]]:
    # Post-result diagnostic only: not a pregame filter. Helps answer whether
    # ATS correctness survives even when absolute margin error is large.
    out=[]
    for lo,hi in [(0,3),(3,7),(7,10),(10,14),(14,21),(21,1e9)]:
        s=frame[(frame["model_abs_error"]>=lo)&(frame["model_abs_error"]<hi)]
        out.append({"model_abs_error_bin": f"{lo:g}-{hi:g}" if hi<1e8 else f"{lo:g}+", **record(s)})
    return out


def main() -> None:
    rows=[]
    coverage={}
    for season in SEASONS:
        games=completed_games(season)
        season_rows=[]
        for _,game in games.iterrows():
            try:
                season_rows.append(make_row(game))
            except Exception as exc:
                print(f"skip {season} {game.get('Game ID')}: {exc}")
        sf=pd.DataFrame(season_rows)
        rows.extend(season_rows)
        coverage[str(season)]={
            "fbs_games": int(len(games)),
            "with_market_spread": int(pd.to_numeric(sf.get("home_spread"), errors="coerce").notna().sum()) if len(sf) else 0,
        }
        print(season, coverage[str(season)])
    frame=pd.DataFrame(rows)
    frame=frame[np.isfinite(pd.to_numeric(frame["home_spread"], errors="coerce"))].copy()
    frame=frame[np.isfinite(pd.to_numeric(frame["edge"], errors="coerce"))].copy()

    result={
        "coverage": coverage,
        "overall": record(frame),
        "by_season": {str(s): record(frame[frame["season"]==s]) for s in SEASONS},
        "edge_thresholds": summarize_edges(frame),
        "edge_bins": summarize_bins(frame),
        "pick_direction": summarize_pick_direction(frame),
        "error_direction_post_result": summarize_error_direction(frame),
        "model_absolute_error_buckets_post_result": summarize_mae_buckets(frame),
    }
    RESULT_JSON.write_text(json.dumps(result, indent=2, allow_nan=False))

    lines=[
        "# CFB Margin MAE vs ATS Relationship",
        "",
        "Sportsbook spread is evaluation-only; it is never used to generate the model margin.",
        "",
        f"Overall: {result['overall']['wins']}-{result['overall']['losses']}-{result['overall']['pushes']} | win rate {100*result['overall']['win_rate']:.2f}% | model MAE {result['overall']['model_mae']:.3f} | market MAE {result['overall']['market_mae']:.3f}",
        "",
        "## By season",
    ]
    for s,r in result["by_season"].items():
        if r["win_rate"] is not None:
            lines.append(f"- {s}: {r['wins']}-{r['losses']}-{r['pushes']} ({100*r['win_rate']:.2f}%), model MAE {r['model_mae']:.3f}, market MAE {r['market_mae']:.3f}")
    lines += ["", "## Edge thresholds", "", "| | Record | Win% | Model MAE | Market MAE |", "|---:|---:|---:|---:|---:|"]
    for r in result["edge_thresholds"]:
        wr="—" if r["win_rate"] is None else f"{100*r['win_rate']:.1f}%"
        lines.append(f"| {r['threshold']}+ | {r['wins']}-{r['losses']}-{r['pushes']} | {wr} | {r['model_mae'] or 0:.2f} | {r['market_mae'] or 0:.2f} |")
    lines += ["", "## Edge bins", "", "| Edge | Record | Win% |", "|---|---:|---:|"]
    for r in result["edge_bins"]:
        wr="—" if r["win_rate"] is None else f"{100*r['win_rate']:.1f}%"
        lines.append(f"| {r['edge_bin']} | {r['wins']}-{r['losses']}-{r['pushes']} | {wr} |")
    lines += ["", "## Pick direction", ""]
    for r in result["pick_direction"]:
        wr="—" if r["win_rate"] is None else f"{100*r['win_rate']:.1f}%"
        lines.append(f"- {r['segment']}: {r['wins']}-{r['losses']}-{r['pushes']} ({wr})")
    lines += ["", "## Post-result error diagnostics", ""]
    for r in result["error_direction_post_result"]:
        wr="—" if r["win_rate"] is None else f"{100*r['win_rate']:.1f}%"
        lines.append(f"- {r['segment']}: {r['wins']}-{r['losses']}-{r['pushes']} ({wr}), model MAE {r['model_mae'] or 0:.2f}, market MAE {r['market_mae'] or 0:.2f}")
    lines += ["", "## ATS record by realized model absolute-error bucket", ""]
    for r in result["model_absolute_error_buckets_post_result"]:
        wr="—" if r["win_rate"] is None else f"{100*r['win_rate']:.1f}%"
        lines.append(f"- {r['model_abs_error_bin']}: {r['wins']}-{r['losses']}-{r['pushes']} ({wr})")
    RESULT_MD.write_text("\n".join(lines)+"\n")
    print(RESULT_MD.read_text())


if __name__ == "__main__":
    main()
