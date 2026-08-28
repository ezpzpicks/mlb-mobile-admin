"""Backtest the production CFB point projections against 2025 closing lines.

Research-only script. It does not write to Google Sheets and does not change
production grading. The output is intended to choose point-edge gates for A/B
spread, Over, and Under plays while keeping Reliability as a separate live gate.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

# Historical research needs the open parquet files synchronously.
os.environ.setdefault("EZPZ_CFB_ALLOW_BLOCKING_OPEN_DATA", "1")
os.environ.setdefault("EZPZ_CFB_CACHE_DIR", "/tmp/ezpz_cfb_backtest_cache")

import numpy as np
import pandas as pd
import polars as pl

from builders import cfb_builder
from builders.cfb_game_regression import install_regression_layer

SEASON = 2025
OUT_DIR = Path("research/results")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _noop_storage() -> None:
    """Keep this historical research completely outside the live workbook."""
    cfb_builder._sheet = lambda tab, columns: pd.DataFrame(columns=columns)
    cfb_builder._write = lambda *args, **kwargs: None
    cfb_builder._upsert = lambda *args, **kwargs: None
    cfb_builder._calibration_adjustments = lambda: (0.0, 0.0, 0)


def finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except Exception:
        return False


def number(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def norm_team(value: Any) -> str:
    try:
        return cfb_builder._normalize_team(value)
    except Exception:
        return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def same_team(a: Any, b: Any) -> bool:
    aa, bb = norm_team(a), norm_team(b)
    if not aa or not bb:
        return False
    if aa == bb:
        return True
    aset = {x for x in aa.split() if len(x) > 2}
    bset = {x for x in bb.split() if len(x) > 2}
    return bool(aset and bset and len(aset & bset) >= min(2, len(aset), len(bset)))


def first_present(names: set[str], *candidates: str) -> str | None:
    for candidate in candidates:
        if candidate in names:
            return candidate
    lower = {name.lower(): name for name in names}
    for candidate in candidates:
        if candidate.lower() in lower:
            return lower[candidate.lower()]
    return None


def load_pbp_markets(season: int) -> pd.DataFrame:
    """Get one closing spread/total row per game from the same open PBP release.

    The classic cfbfastR parquet carries closing provider spread + total on each
    play. We deliberately read only market/identity columns here.
    """
    path = cfb_builder._download_open_asset_now(
        "cfbfastR_cfb_pbp", season, ("play_by_play", "pbp")
    )
    if path is None or not path.exists():
        raise RuntimeError(f"Could not download cfbfastR PBP for {season}")

    scan = pl.scan_parquet(str(path))
    names = set(scan.collect_schema().names())
    aliases = {
        "game_id": ("game_id", "id_game"),
        "week": ("week",),
        "home_team": ("home_team", "homeTeamName", "homeTeam"),
        "away_team": ("away_team", "awayTeamName", "awayTeam"),
        "home_team_spread": ("home_team_spread", "homeTeamSpread"),
        "home_favorite": ("home_favorite", "homeFavorite"),
        "spread": ("spread", "game_spread", "gameSpread"),
        "formatted_spread": ("formatted_spread", "formattedSpread"),
        "over_under": ("over_under", "overUnder", "game_over_under"),
    }
    selected: list[pl.Expr] = []
    for canonical, candidates in aliases.items():
        actual = first_present(names, *candidates)
        if actual:
            selected.append(pl.col(actual).alias(canonical))
    if not any(expr.meta.output_name() == "game_id" for expr in selected):
        raise RuntimeError("PBP market file did not contain a game id")
    frame = scan.select(selected).collect().to_pandas()

    # Every play repeats the same pregame market. Keep the first useful value.
    def first_valid(series: pd.Series) -> Any:
        for value in series:
            if value is None:
                continue
            if isinstance(value, float) and math.isnan(value):
                continue
            if str(value).strip() not in {"", "nan", "None"}:
                return value
        return np.nan

    grouped = frame.groupby("game_id", as_index=False).agg(first_valid)
    grouped["game_id"] = grouped["game_id"].astype(str).str.replace(r"\.0$", "", regex=True)
    return grouped


def derive_home_spread(row: pd.Series, home_team: str, away_team: str) -> float:
    direct = number(row.get("home_team_spread"))
    if math.isfinite(direct):
        return direct

    spread = number(row.get("spread"))
    formatted = str(row.get("formatted_spread") or "").strip()
    if formatted:
        match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*$", formatted)
        if match:
            line = float(match.group(1))
            favorite = formatted[: match.start()].strip()
            if same_team(favorite, home_team):
                return -abs(line)
            if same_team(favorite, away_team):
                return abs(line)

    favorite = row.get("home_favorite")
    if math.isfinite(spread) and favorite is not None and str(favorite).lower() not in {"nan", "none", ""}:
        is_home = bool(favorite) if isinstance(favorite, (bool, np.bool_)) else str(favorite).strip().lower() in {"1", "true", "yes", "y"}
        return -abs(spread) if is_home else abs(spread)
    return math.nan


def market_lookup(markets: pd.DataFrame) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_match: dict[str, dict[str, Any]] = {}
    for _, row in markets.iterrows():
        item = row.to_dict()
        gid = str(item.get("game_id") or "").replace(".0", "")
        if gid:
            by_id[gid] = item
        away, home = norm_team(item.get("away_team")), norm_team(item.get("home_team"))
        if away and home:
            by_match[f"{away}|{home}"] = item
    return by_id, by_match


def baseline_personnel(rating: pd.Series) -> Any:
    p = cfb_builder.Personnel()
    p.expected_qb = "Historical baseline"
    p.qb_confirmed = False
    p.qb_continuity = cfb_builder._pct(rating.get("QB Continuity"), 0.50)
    p.coaching_continuity = cfb_builder._pct(rating.get("Coaching Continuity"), 0.75)
    p.coordinator_continuity = cfb_builder._pct(rating.get("Coordinator Continuity"), 0.67)
    # Confidence does not change the projected score; it is a separate grading gate.
    p.availability_confidence = 32.0
    p.source = "Historical baseline; no postgame injury/QB hindsight"
    return p


def grade_result(model_side: str, actual_value: float) -> str:
    if abs(actual_value) <= 1e-9:
        return "P"
    if model_side in {"HOME", "OVER"}:
        return "W" if actual_value > 0 else "L"
    return "W" if actual_value < 0 else "L"


def wilson_low(wins: int, losses: int, z: float = 1.96) -> float:
    n = wins + losses
    if n <= 0:
        return math.nan
    p = wins / n
    denom = 1.0 + z * z / n
    center = p + z * z / (2 * n)
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return (center - spread) / denom


def summarize(frame: pd.DataFrame, market: str, scope: str) -> list[dict[str, Any]]:
    subset = frame[frame["market"] == market].copy()
    if scope == "FBS-FBS":
        subset = subset[subset["fbs_fbs"]]
    rows: list[dict[str, Any]] = []
    for cutoff in np.arange(0.5, 12.01, 0.5):
        s = subset[subset["edge"] >= cutoff]
        wins = int((s["result"] == "W").sum())
        losses = int((s["result"] == "L").sum())
        pushes = int((s["result"] == "P").sum())
        decisions = wins + losses
        units = wins * (100.0 / 110.0) - losses
        roi = units / max(1, decisions) * 100.0
        first = s[s["week"] <= 7]
        second = s[s["week"] > 7]
        def split_roi(x: pd.DataFrame) -> float:
            w = int((x["result"] == "W").sum()); l = int((x["result"] == "L").sum())
            return (w * (100.0 / 110.0) - l) / max(1, w + l) * 100.0
        rows.append({
            "scope": scope,
            "market": market,
            "edge_cutoff": round(float(cutoff), 1),
            "bets": int(len(s)),
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "win_pct": round(100.0 * wins / decisions, 2) if decisions else math.nan,
            "roi_pct_at_minus_110": round(roi, 2),
            "wilson_95_low_pct": round(100.0 * wilson_low(wins, losses), 2) if decisions else math.nan,
            "weeks_0_7_roi_pct": round(split_roi(first), 2),
            "weeks_8_plus_roi_pct": round(split_roi(second), 2),
        })
    return rows


def choose_thresholds(table: pd.DataFrame, market: str, scope: str = "FBS-FBS") -> dict[str, Any]:
    """Conservative screen; final recommendation still belongs in human review."""
    view = table[(table["market"] == market) & (table["scope"] == scope)].copy()
    # B: enough sample to matter, breakeven-beating win rate, positive full-season ROI.
    b = view[(view["bets"] >= 80) & (view["win_pct"] >= 52.4) & (view["roi_pct_at_minus_110"] > 0)]
    # A: stronger result + useful sample; do not demand statistical significance from one season.
    a = view[(view["bets"] >= 45) & (view["win_pct"] >= 54.5) & (view["roi_pct_at_minus_110"] >= 4.0)]
    b_pick = b.sort_values("edge_cutoff").iloc[0].to_dict() if not b.empty else None
    a_pick = a.sort_values("edge_cutoff").iloc[0].to_dict() if not a.empty else None
    return {"B_screen": b_pick, "A_screen": a_pick}


def main() -> None:
    _noop_storage()
    install_regression_layer(cfb_builder)

    # Warm the exact historical files used by the current ratings engine.
    for year in (SEASON - 1, SEASON):
        cfb_builder._download_open_asset_now("cfbfastR_cfb_pbp", year, ("play_by_play", "pbp"))
        cfb_builder._download_open_asset_now("espn_cfb_rosters", year, ("roster", "espn"))

    schedule = cfb_builder._parse_games(cfb_builder._espn_games_payload(SEASON), SEASON)
    schedule = schedule[schedule["Completed"].map(cfb_builder._bool)].copy()
    if schedule.empty:
        raise RuntimeError("No completed 2025 schedule rows were available")

    markets = load_pbp_markets(SEASON)
    by_id, by_match = market_lookup(markets)

    output: list[dict[str, Any]] = []
    ratings_cache: dict[int, pd.DataFrame] = {}
    for week in sorted(pd.to_numeric(schedule["Week"], errors="coerce").fillna(0).astype(int).unique()):
        weekly_games = schedule[pd.to_numeric(schedule["Week"], errors="coerce").fillna(0).astype(int) == week]
        print(f"Week {week}: {len(weekly_games)} completed games", flush=True)
        ratings = cfb_builder.build_team_ratings(SEASON, int(week))
        ratings_cache[int(week)] = ratings
        rating_map = {norm_team(row["Team"]): row for _, row in ratings.iterrows()}

        for _, game in weekly_games.iterrows():
            away = str(game.get("Away Team") or "").strip(); home = str(game.get("Home Team") or "").strip()
            away_key, home_key = norm_team(away), norm_team(home)
            if away_key not in rating_map or home_key not in rating_map:
                continue
            gid = str(game.get("Game ID") or "").replace(".0", "")
            market = by_id.get(gid) or by_match.get(f"{away_key}|{home_key}") or {}

            # Prefer the schedule's ESPN closing line when present; otherwise use
            # the repeated cfbfastR pregame market from the PBP parquet.
            home_spread = number(game.get("Home Spread"))
            if not math.isfinite(home_spread):
                home_spread = derive_home_spread(pd.Series(market), home, away)
            total_line = number(game.get("Total"))
            if not math.isfinite(total_line):
                total_line = number(market.get("over_under"))
            if not math.isfinite(home_spread) and not math.isfinite(total_line):
                continue

            away_rating = rating_map[away_key]; home_rating = rating_map[home_key]
            away_p = baseline_personnel(away_rating); home_p = baseline_personnel(home_rating)
            environment = cfb_builder.build_environment(game, SEASON)
            projection = cfb_builder.project_matchup(game, ratings, away_p, home_p, environment)
            proj_margin = number(projection.get("margin"))
            proj_total = number(projection.get("total"))
            actual_margin = number(game.get("Home Score")) - number(game.get("Away Score"))
            actual_total = number(game.get("Home Score")) + number(game.get("Away Score"))
            classifications = {str(game.get("Away Classification") or "").lower(), str(game.get("Home Classification") or "").lower()}
            fbs_fbs = classifications <= {"fbs", ""} and "fbs" in classifications
            if classifications == {""}:
                # Ratings are built around FBS membership; when the source omits
                # classification, require both teams to exist in the weekly table.
                fbs_fbs = away_key in rating_map and home_key in rating_map

            common = {
                "season": SEASON, "week": int(week), "game_id": gid,
                "away_team": away, "home_team": home,
                "actual_away": number(game.get("Away Score")), "actual_home": number(game.get("Home Score")),
                "projected_away": round(number(projection.get("away_points")), 3),
                "projected_home": round(number(projection.get("home_points")), 3),
                "projected_margin": round(proj_margin, 3), "projected_total": round(proj_total, 3),
                "fbs_fbs": bool(fbs_fbs),
                "data_confidence": round((number(away_rating.get("Data Confidence"), 0) + number(home_rating.get("Data Confidence"), 0)) / 2.0, 2),
            }

            if math.isfinite(home_spread) and math.isfinite(proj_margin):
                raw = proj_margin + home_spread
                side = "HOME" if raw >= 0 else "AWAY"
                cover_margin = actual_margin + home_spread
                output.append({**common, "market": "Spread", "line": home_spread,
                               "pick_side": side, "edge": abs(raw),
                               "result": grade_result(side, cover_margin)})

            if math.isfinite(total_line) and math.isfinite(proj_total):
                raw = proj_total - total_line
                side = "OVER" if raw >= 0 else "UNDER"
                output.append({**common, "market": "Over" if side == "OVER" else "Under", "line": total_line,
                               "pick_side": side, "edge": abs(raw),
                               "result": grade_result(side, actual_total - total_line)})

    results = pd.DataFrame(output)
    if results.empty:
        raise RuntimeError("Backtest produced no lined games")
    results["edge"] = pd.to_numeric(results["edge"], errors="coerce")
    results = results[np.isfinite(results["edge"])].copy()
    results.to_csv(OUT_DIR / "cfb_2025_game_edges.csv", index=False)

    summaries: list[dict[str, Any]] = []
    for scope in ("All", "FBS-FBS"):
        for market in ("Spread", "Over", "Under"):
            summaries.extend(summarize(results, market, scope))
    summary = pd.DataFrame(summaries)
    summary.to_csv(OUT_DIR / "cfb_2025_edge_thresholds.csv", index=False)

    screens = {market: choose_thresholds(summary, market) for market in ("Spread", "Over", "Under")}
    payload = {
        "season": SEASON,
        "method": "Current production point projection; leakage-safe weekly ratings; closing lines; no postgame QB/injury/weather hindsight; Reliability analyzed separately.",
        "games_with_spread": int((results["market"] == "Spread").sum()),
        "totals_with_line": int(results["market"].isin(["Over", "Under"]).sum()),
        "fbs_fbs_spreads": int(((results["market"] == "Spread") & results["fbs_fbs"]).sum()),
        "fbs_fbs_totals": int((results["market"].isin(["Over", "Under"]) & results["fbs_fbs"]).sum()),
        "conservative_screens": screens,
    }
    (OUT_DIR / "cfb_2025_grade_backtest.json").write_text(json.dumps(payload, indent=2, default=str))

    md = [
        "# 2025 CFB point-edge grade backtest",
        "",
        payload["method"],
        "",
        f"Spread rows: **{payload['games_with_spread']}** (FBS-FBS {payload['fbs_fbs_spreads']})",
        f"Total rows: **{payload['totals_with_line']}** (FBS-FBS {payload['fbs_fbs_totals']})",
        "",
        "## Conservative threshold screens",
        "",
        "These screens are not automatically promoted to production. B requires >=80 bets, >=52.4% wins and positive ROI. A requires >=45 bets, >=54.5% wins and >=4% ROI. Reliability remains a separate live-data gate.",
        "",
    ]
    for market, values in screens.items():
        md.append(f"### {market}")
        for label in ("B_screen", "A_screen"):
            value = values[label]
            if value is None:
                md.append(f"- {label}: no 2025 cutoff passed the screen")
            else:
                md.append(f"- {label}: >= **{value['edge_cutoff']:.1f} points** — {int(value['wins'])}-{int(value['losses'])}-{int(value['pushes'])}, {value['win_pct']:.2f}% wins, {value['roi_pct_at_minus_110']:.2f}% ROI, n={int(value['bets'])}")
        md.append("")
    md.extend([
        "## Files",
        "",
        "- `cfb_2025_game_edges.csv`: every model-vs-closing-line decision.",
        "- `cfb_2025_edge_thresholds.csv`: every 0.5-point cutoff, split by market and FBS-FBS/all games.",
        "- `cfb_2025_grade_backtest.json`: machine-readable summary.",
    ])
    (OUT_DIR / "CFB_2025_GRADE_BACKTEST.md").write_text("\n".join(md) + "\n")
    print(json.dumps(payload, indent=2, default=str), flush=True)


if __name__ == "__main__":
    main()
