"""Research-only diagnostic: test whether spread probability/confluence gates add value on 2025 holdout."""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from statistics import NormalDist

os.environ.setdefault("EZPZ_CFB_ALLOW_BLOCKING_OPEN_DATA", "1")
os.environ.setdefault("EZPZ_CFB_CACHE_DIR", "/tmp/ezpz_cfb_gate_diag_cache")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import pandas as pd

import cfb_2025_grade_backtest as bt
from builders import cfb_builder
from builders.cfb_game_regression import install_regression_layer
from builders.cfb_market_calibration import MARGIN_RESIDUAL_SD

OUT = ROOT / "research" / "results" / "cfb_2025_gate_diagnostics.json"


def pct(w: int, l: int) -> float | None:
    return round(100.0 * w / (w + l), 2) if w + l else None


def roi(w: int, l: int) -> float | None:
    return round(100.0 * (w * (100.0 / 110.0) - l) / (w + l), 2) if w + l else None


def summarize(df: pd.DataFrame, edge_cut: float, conf_cut: int) -> dict:
    base = df[(df["fbs_fbs"]) & (df["edge"] >= edge_cut)].copy()
    gated = base[base["confluence"] >= conf_cut].copy()
    def one(x: pd.DataFrame) -> dict:
        w = int((x.result == "W").sum()); l = int((x.result == "L").sum()); p = int((x.result == "P").sum())
        return {"bets": int(len(x)), "wins": w, "losses": l, "pushes": p, "win_pct": pct(w,l), "roi_pct_at_minus_110": roi(w,l)}
    return {"edge_cutoff": edge_cut, "confluence_cutoff": conf_cut, "all_edge_qualifiers": one(base), "after_confluence_gate": one(gated)}


def main() -> None:
    bt._noop_storage()
    install_regression_layer(cfb_builder)
    season = bt.SEASON
    for year in (season - 1, season):
        cfb_builder._download_open_asset_now("cfbfastR_cfb_pbp", year, ("play_by_play", "pbp"))
        cfb_builder._download_open_asset_now("espn_cfb_rosters", year, ("roster", "espn"))

    schedule = cfb_builder._parse_games(cfb_builder._espn_games_payload(season), season)
    schedule = schedule[schedule["Completed"].map(cfb_builder._bool)].copy()
    markets = bt.load_pbp_markets(season)
    by_id, by_match = bt.market_lookup(markets)
    rows = []

    for week in sorted(pd.to_numeric(schedule["Week"], errors="coerce").fillna(0).astype(int).unique()):
        games = schedule[pd.to_numeric(schedule["Week"], errors="coerce").fillna(0).astype(int) == week]
        ratings = cfb_builder.build_team_ratings(season, int(week))
        rating_map = {bt.norm_team(r["Team"]): r for _, r in ratings.iterrows()}
        for _, game in games.iterrows():
            away = str(game.get("Away Team") or "").strip(); home = str(game.get("Home Team") or "").strip()
            ak, hk = bt.norm_team(away), bt.norm_team(home)
            if ak not in rating_map or hk not in rating_map:
                continue
            gid = str(game.get("Game ID") or "").replace(".0", "")
            market = by_id.get(gid) or by_match.get(f"{ak}|{hk}") or {}
            home_spread = bt.number(game.get("Home Spread"))
            if not math.isfinite(home_spread):
                home_spread = bt.derive_home_spread(pd.Series(market), home, away)
            if not math.isfinite(home_spread):
                continue
            ap = bt.baseline_personnel(rating_map[ak]); hp = bt.baseline_personnel(rating_map[hk])
            env = cfb_builder.build_environment(game, season)
            proj = cfb_builder.project_matchup(game, ratings, ap, hp, env)
            margin = bt.number(proj.get("margin"))
            if not math.isfinite(margin):
                continue
            raw = margin + home_spread
            side = "HOME" if raw >= 0 else "AWAY"
            pick_team = home if side == "HOME" else away
            conf, supports = cfb_builder._confluence(proj, pick_team, home, "spread")
            actual_margin = bt.number(game.get("Home Score")) - bt.number(game.get("Away Score"))
            result = bt.grade_result(side, actual_margin + home_spread)
            classes = {str(game.get("Away Classification") or "").lower(), str(game.get("Home Classification") or "").lower()}
            fbs = classes <= {"fbs", ""} and "fbs" in classes
            if classes == {""}:
                fbs = ak in rating_map and hk in rating_map
            edge = abs(raw)
            # With the calibrated Gaussian margin distribution, probability is essentially
            # a monotonic transform of point edge (apart from impossible-score tail clipping).
            prob = NormalDist().cdf(edge / MARGIN_RESIDUAL_SD)
            rows.append({"week": int(week), "game_id": gid, "away": away, "home": home, "fbs_fbs": bool(fbs),
                         "edge": edge, "probability": prob, "confluence": int(conf), "supports": supports, "result": result})

    df = pd.DataFrame(rows)
    fbs = df[df.fbs_fbs].copy()
    by_conf = {}
    for score in sorted(fbs.confluence.dropna().astype(int).unique()):
        x = fbs[fbs.confluence == score]
        w = int((x.result == "W").sum()); l = int((x.result == "L").sum()); p = int((x.result == "P").sum())
        by_conf[str(score)] = {"bets": int(len(x)), "wins": w, "losses": l, "pushes": p, "win_pct": pct(w,l), "roi_pct_at_minus_110": roi(w,l)}

    payload = {
        "season": season,
        "margin_residual_sd": MARGIN_RESIDUAL_SD,
        "probability_at_6_edge": NormalDist().cdf(6.0 / MARGIN_RESIDUAL_SD),
        "probability_at_9_5_edge": NormalDist().cdf(9.5 / MARGIN_RESIDUAL_SD),
        "note": "Probability is downstream of regression margin/market edge; confluence is a separate legacy matchup heuristic and is not a regression predictor.",
        "B_edge_6_conf_3": summarize(df, 6.0, 3),
        "A_edge_9_5_conf_4": summarize(df, 9.5, 4),
        "confluence_score_all_fbs_fbs": by_conf,
    }
    OUT.write_text(json.dumps(payload, indent=2))
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
