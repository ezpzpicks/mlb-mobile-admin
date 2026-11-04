#!/usr/bin/env python3
"""Offline replay of the frozen v0.5 correction; never tunes or writes live data.

Inputs are the independently verified November 2 replay's games.json and saved
NCAAM public-data JSON responses. One latest pregame forecast per game is used.
Historical results are baseline-component replays; live results preserve saved
player adjustments and exact quoted markets. All ATS comparisons are hypothetical.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from builders.ncaam_margin_calibration import (
    HOME_COURT_POINTS, NEUTRAL_MARGIN_SCALE, POLICY, REPLAY_SHA256, fitted_margin,
)
MODEL_VERSION = "ncaam-v0.5-opening-margin-calibration-2026-11-03"

BASELINE_VERSION = "ncaam-v0.4-source-prior-player-fallback-2026-11-02"


def stamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Replay timestamps must have a timezone.")
    return parsed


def summarize(rows, field):
    def section(group):
        errors = [r[field] - r["actual"] for r in group]
        bias = [(1 if r["line"] < 0 else -1) * (r["actual"] - r[field]) for r in group if r["line"]]
        records = {}
        for threshold in (0, 3, 5):
            counts = Counter()
            for row in group:
                edge = row[field] + row["line"]
                if abs(edge) < 1e-9 or abs(edge) < threshold - 1e-9:
                    continue
                covered = (1 if edge > 0 else -1) * (row["actual"] + row["line"])
                counts["wins" if covered > 0 else "losses" if covered < 0 else "pushes"] += 1
            units = counts["wins"] * 100 / 110 - counts["losses"]
            bets = sum(counts.values())
            records[str(threshold)] = {**{k: counts[k] for k in ("wins", "losses", "pushes")},
                "bets": bets, "units": units, "roi_pct": 100 * units / bets if bets else None}
        return {"games": len(group), "margin_mae": statistics.mean(map(abs, errors)) if errors else None,
            "margin_rmse": math.sqrt(statistics.mean(e * e for e in errors)) if errors else None,
            "favorite_underprediction": statistics.mean(bias) if bias else None, "ats_by_edge_threshold": records}
    return {"all": section(rows), "favorite_20_plus": section([r for r in rows if abs(r["line"]) >= 20])}


def evaluate(historical_path, live_paths):
    raw = historical_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != REPLAY_SHA256:
        raise ValueError("Historical data differs from the independently verified frozen replay.")
    historical = json.loads(raw)
    training = [r for r in historical if r["season"] == 2024]
    # Reproduce the two-column least-squares fit using training games only.
    xx = sum(r["neutral_margin"] ** 2 for r in training)
    xh = sum(r["neutral_margin"] * r["court_indicator"] for r in training)
    hh = sum(r["court_indicator"] ** 2 for r in training)
    xy = sum(r["neutral_margin"] * r["actual_margin"] for r in training)
    hy = sum(r["court_indicator"] * r["actual_margin"] for r in training)
    determinant = xx * hh - xh * xh
    fitted = ((xy * hh - hy * xh) / determinant, (hy * xx - xy * xh) / determinant)
    if len(training) != 600 or not all(abs(a - b) < 1e-10 for a, b in zip(fitted, (NEUTRAL_MARGIN_SCALE, HOME_COURT_POINTS))):
        raise ValueError("Frozen coefficients no longer reproduce the original training-only fit.")
    comparisons = {}
    for season in (2025, 2026):
        rows = [{"actual": r["actual_margin"], "line": r["home_spread"], "baseline": r["baseline_margin"],
                 "calibrated": fitted_margin(r["neutral_margin"], neutral_site=r["neutral_site"])}
                for r in historical if r["season"] == season]
        comparisons[str(season)] = {field: summarize(rows, field) for field in ("baseline", "calibrated")}

    live_rows, seen, rejected, inputs = [], set(), Counter(), []
    for path in live_paths:
        raw = path.read_bytes()
        data = json.loads(raw)["ncaamMarket"]
        cutoff = stamp(data["signals"]["asOf"])
        inputs.append({"filename": path.name, "sha256": hashlib.sha256(raw).hexdigest(), "as_of": cutoff.isoformat()})
        games = {g["Game ID"]: g for g in data["games"]}
        latest = {}
        for f in data["forecasts"]:
            if f["modelVersion"] != BASELINE_VERSION or f["phase"] != "pregame" or stamp(f["lockedAt"]) >= stamp(f["tipoff"]):
                continue
            if f["gameId"] not in latest or stamp(f["lockedAt"]) > stamp(latest[f["gameId"]]["lockedAt"]):
                latest[f["gameId"]] = f
        for game_id, f in latest.items():
            g = games[game_id]
            if g["Completed"] != "TRUE" or g["Status"] != "STATUS_FINAL":
                rejected["unfinished"] += 1
                continue
            if (g["Source"] != "ESPN NCAAM schedule; ScoresAndOdds market lines"
                or (g["Away Team"], g["Home Team"]) != (f["away_team"], f["home_team"])
                or stamp(g["Game Time"]) != stamp(f["tipoff"])
                or not stamp(f["tipoff"]) < stamp(g["Result Updated"]) <= cutoff):
                rejected["changed_game_identity_or_unverified_result"] += 1
                continue
            if game_id in seen:
                raise ValueError("Duplicate live game across input snapshots.")
            seen.add(game_id)
            # Preserve the original v0.5 experiment when the live model changes.
            # Reconstruct the exact source margin before saved-output rounding;
            # availability contributes its original unscaled margin delta.
            away, home = f["away_rating"], f["home_rating"]
            pace = (away['tempo'] + home['tempo']) / 2
            efficiency = f['league_efficiency']
            source_away = pace * away['offense'] * home['defense'] / efficiency / 100
            source_home = pace * home['offense'] * away['defense'] / efficiency / 100
            adjustment = f.get('lineup_adjustment')
            player_margin = 0.0
            if adjustment:
                a, h = adjustment['away'], adjustment['home']
                adjusted_away = pace * (away['offense'] + a['offense_change_per_100']) * (home['defense'] - h['defense_improvement_per_100']) / efficiency / 100
                adjusted_home = pace * (home['offense'] + h['offense_change_per_100']) * (away['defense'] - a['defense_improvement_per_100']) / efficiency / 100
                player_margin = (adjusted_home - adjusted_away) - (source_home - source_away)
            revised_margin = round(fitted_margin(source_home - source_away, neutral_site=f['neutral_site']) + player_margin, 2)
            live_rows.append({"game_id": game_id, "date": data["date"], "away": f["away_team"], "home": f["home_team"],
                "actual": float(g["Home Score"]) - float(g["Away Score"]), "line": float(f["market"]["home_spread"]),
                "baseline": -f["fair_home_spread"], "calibrated": revised_margin})
    live = {day: {field: summarize([r for r in live_rows if r["date"] == day], field)
                 for field in ("baseline", "calibrated")} for day in sorted({r["date"] for r in live_rows})}
    live["combined"] = {field: summarize(live_rows, field) for field in ("baseline", "calibrated")}
    return {"policy": "ncaam-opening-margin-2026-11-03-v1", "new_model_version": MODEL_VERSION, "baseline_model_version": BASELINE_VERSION,
        "historical_sha256": REPLAY_SHA256, "training_games": len(training), "training_season": 2024,
        "frozen_coefficients": {"neutral_margin_scale": fitted[0], "home_court_points": fitted[1]},
        "historical": comparisons, "live": live, "live_exclusions": dict(rejected), "live_inputs": inputs,
        "live_game_comparisons": live_rows,
        "limits": ["Research projections only; no official betting policy or A/B grades.",
                   "No tuning on later historical seasons or the current-season check.",
                   "Historical replay does not reconstruct availability; live replay preserves saved player deltas.",
                   "Use is restricted to the evaluated opening fortnight; later phases retain baseline.",
                   "ATS is hypothetical at the original saved quote with assumed -110 prices; current-season inputs are a retrospective replay."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical", type=Path, required=True)
    parser.add_argument("--live", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.historical, args.live)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"policy": report["policy"], "live": report["live"]["combined"], "excluded": report["live_exclusions"]}, indent=2))


if __name__ == "__main__":
    main()
