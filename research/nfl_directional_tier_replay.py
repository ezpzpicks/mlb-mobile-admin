"""Compare symmetric and reversed-positive WR tiers on the same pregame baselines.

Saved-calibration mode uses recoverable v4.19 receiving inputs. Core-regression
mode is a sensitivity analysis: it rebuilds the current regression core with
saved game forecasts, without reconstructing historical live efficiency overlays.
Prepared profiles must exclude current-season NGS week-0 season summaries and
all observations from the projected week or later. This script never writes to
production storage and does not refit model coefficients or choose weights.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

from builders import nfl_builder as builder, nfl_slot_matchups as slots
from builders import nfl_skill_prop_regression as regression
import shared.auth  # noqa: F401; the complete production wrapper chain
from builders.nfl_wr_receiving_v419 import _wr_matchup_factors
from research.nfl_prop_directional_scoring import directional_report, finite_number


def metrics(rows: list[dict], field: str) -> dict:
    if not rows:
        return {"n": 0}
    errors = np.array([row[field] - row["actual"] for row in rows])
    return {
        "n": len(rows), "mae": round(float(np.abs(errors).mean()), 3),
        "rmse": round(float(np.sqrt(np.mean(errors ** 2))), 3),
        "bias": round(float(errors.mean()), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs-dir", type=Path, required=True)
    parser.add_argument("--baseline", choices=("saved-calibration", "core-regression"), required=True)
    parser.add_argument("--weeks", type=int, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.inputs_dir
    slots.install_slot_matchup_layer(builder)
    assert getattr(builder, "_UNIFIED_PROP_GRADING_INSTALLED", False)
    stored = json.loads((source / "positive-tier-historical-inputs.json").read_text())
    lineups = pd.DataFrame([item["row"] for item in stored if item["dataset"] == "lineup_snapshots"])
    schedule = pd.DataFrame(json.loads((source / "nfl-saved-inputs.json").read_text())["schedule"])
    builder.read_sheet = lambda tab, columns=None: lineups.copy() if tab == builder.LINEUP_TAB else pd.DataFrame(columns=columns)
    builder._schedule_for_season = lambda season, *args, **kwargs: schedule.copy()
    slots.clear_slot_matchup_cache()
    stats = pd.read_pickle(source / "positive-tier-actual-player-stats.pkl")
    stats = stats.loc[stats["season_type"].astype(str).str.upper().eq("REG")].copy()
    stats["_name"] = builder._player_name_column(stats).map(builder._normalize_name)
    stats["_team"] = builder._player_team_column(stats).map(builder._normalize_team)
    actuals = stats.groupby(["week", "_team", "_name"])["receiving_yards"].sum().to_dict()
    game_rows = json.loads((source / "nfl-public-data.json").read_text())["betTrackerRows"]
    game_forecasts = {
        str(row["Game ID"]): row for row in game_rows
        if str(row.get("Season")) == "2026" and row.get("Projected Away") and row.get("Projected Home")
    }
    profiles_by_week = {week: pd.read_pickle(source / f"positive-tier-profiles-w{week}.pkl") for week in args.weeks}
    original_benchmarks = slots._benchmark_slots
    benchmark_cache = {}

    def cached_benchmarks(module, profiles):
        key = id(profiles)
        if key not in benchmark_cache:
            benchmark_cache[key] = original_benchmarks(module, profiles)
        return benchmark_cache[key]

    slots._benchmark_slots = cached_benchmarks
    directional_weight = slots._directional_tier_weight
    results, exclusions = [], []
    projections = [item["row"] for item in stored if item["dataset"] == "prop_projections"]
    for old in projections:
        week = int(old["Week"])
        if week not in args.weeks or old.get("Market") != "Receiving Yards" or old.get("Slot") not in ("WR1", "WR2"):
            continue
        identity = {key: old[key] for key in ("Week", "Game ID", "Player", "Team", "Slot")}
        actual_key = (week, builder._normalize_team(old["Team"]), builder._normalize_name(old["Player"]))
        if actual_key not in actuals:
            exclusions.append({**identity, "reason": "No matching official player/team/week stat record"})
            continue
        profiles = profiles_by_week[week]
        player = builder._profile_lookup(profiles, old["Player"])
        if not player:
            exclusions.append({**identity, "reason": "Missing pregame player profile"})
            continue
        if args.baseline == "saved-calibration":
            match = re.search(r"YPT ([0-9.]+) ->", str(old.get("Confluence", "")))
            if not match or "v4.19 WR1/WR2 receiving calibration" not in str(old.get("Confluence", "")):
                exclusions.append({**identity, "reason": "Saved receiving baseline is not recoverable"})
                continue
            targets, efficiency = float(old["Projected Targets"]), float(match.group(1))
        else:
            forecast = game_forecasts.get(str(old["Game ID"]))
            if not forecast:
                exclusions.append({**identity, "reason": "Missing saved pregame game-score forecast"})
                continue
            team_total = float(forecast["Projected Home" if str(old["Home/Away"]).lower() == "home" else "Projected Away"])
            context = regression._live_history_context(
                builder, 2026, week, old["Player"], old["Team"], old["Opponent"],
                "WR", team_total, old["Home/Away"],
            )
            if context.get("available", 0.0) < 0.5:
                exclusions.append({**identity, "reason": "Insufficient pregame regression history"})
                continue
            targets, efficiency = regression.project_wr_receiving(context)
        catch_rate = float(old["Projected Receptions"]) / float(old["Projected Targets"]) if float(old["Projected Targets"]) > 0 else 0.65
        pre = copy.deepcopy(old)
        pre.update({
            "Projection": targets * efficiency, "Raw Projection": targets * efficiency,
            "Calibration Adjustment": 0.0, "Projected Targets": targets,
            "Projected Receptions": targets * catch_rate, "Targets Per Route": 0.25,
            "Efficiency": efficiency, "Matchup Index": 1.0,
            "Confluence": "v4.4 regression targets × YPT • live role overlay 1.00x",
        })
        try:
            slots._directional_tier_weight = lambda factor, weight: float(weight)
            current = slots._apply_slot_overlay(builder, [copy.deepcopy(pre)], 2026, week, old["Opponent"], old["Slot"], player, profiles)[0]
            slots._directional_tier_weight = directional_weight
            candidate = slots._apply_slot_overlay(builder, [copy.deepcopy(pre)], 2026, week, old["Opponent"], old["Slot"], player, profiles)[0]
            factors = _wr_matchup_factors(builder, 2026, week, old["Opponent"], old["Slot"], player, profiles)
        finally:
            slots._directional_tier_weight = directional_weight
        # Reprice both rows under the same installed production evaluator. Do not
        # reuse saved probabilities/grades from the older historical model.
        evaluated = builder._evaluate_prop_rows(pd.DataFrame([current, candidate]))
        current_priced, candidate_priced = evaluated.iloc[0], evaluated.iloc[1]
        results.append({
            **identity, "actual": float(actuals[actual_key]),
            "baseline": round(float(targets * (0.2 * efficiency + 0.8 * 8.15)), 3),
            "current": float(current["Projection"]), "candidate": float(candidate["Projection"]),
            "current_factor": float(current["Matchup Index"]), "candidate_factor": float(candidate["Matchup Index"]),
            "target_adjustment": factors["target_adjustment"], "yardage_adjustment": factors["slot_adjustment"],
            "opportunity_base_factor": factors["opportunity_base_factor"], "efficiency_base_factor": factors["efficiency_base_factor"],
            "market_line": finite_number(old.get("Market Line")),
            "over_odds": finite_number(old.get("Over Odds")), "under_odds": finite_number(old.get("Under Odds")),
            "line_source": str(old.get("Line Source", "")),
            "current_grade": str(current_priced["Grade"]), "candidate_grade": str(candidate_priced["Grade"]),
            "current_probability_edge": finite_number(current_priced["Probability Edge"]),
            "candidate_probability_edge": finite_number(candidate_priced["Probability Edge"]),
        })
    groups = {"all": results}
    for slot in ("WR1", "WR2"):
        groups[slot] = [row for row in results if row["Slot"] == slot]
    for week in args.weeks:
        groups[f"week_{week}"] = [row for row in results if int(row["Week"]) == week]
    groups["positive_yardage_residual"] = [row for row in results if row["yardage_adjustment"] > 0]
    groups["negative_yardage_residual"] = [row for row in results if row["yardage_adjustment"] < 0]
    summary = {
        group: {policy: metrics(rows, policy) for policy in ("current", "candidate")}
        for group, rows in groups.items()
    }
    report = {
        "baseline_method": args.baseline, "weeks": args.weeks,
        "summary": summary, "exclusions": exclusions, "rows": results,
        "directional": directional_report(results),
        "negative_residual_but_candidate_boost": sum(row["yardage_adjustment"] < 0 and row["candidate_factor"] > 1.0 for row in results),
        "mixed_component_signs": sum((row["opportunity_base_factor"]-1)*(row["efficiency_base_factor"]-1) < 0 for row in results),
        "limitations": [
            "Retrospective comparison; no weights were optimized or refit and no untouched prospective validation was run.",
            "Historical profiles use weekly NGS rows only for 2026; season-summary week-0 rows are excluded to prevent look-ahead.",
            "Unmatched official player/team/week stat records are excluded; absence is not silently treated as zero yards.",
            "Stored receiving regression inputs are rounded, so saved-calibration results are approximate.",
            "Directional scoring uses saved manual market lines; projection ties are no picks and actual ties are pushes.",
            "Both versions are repriced with the same current simulator and unchanged Strong/Regular gates; historical grades are not reused.",
            "Saved rows have dates but no immutable intraday line history; lines are not independently verified closing prices.",
        ],
    }
    if args.baseline == "core-regression":
        report["limitations"].append("Sensitivity analysis uses saved game-score forecasts and omits historical live efficiency overlays; it is not a full production rebuild.")
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps({
        "baseline_method": args.baseline, "weeks": args.weeks,
        "directional_summary": report["directional"]["summary"]["all"],
        "changed_calls": {key: value for key, value in report["directional"]["changed_calls"].items() if key != "rows"},
        "publishable_changes": report["directional"]["publishable_changes"],
    }), flush=True)
    print(json.dumps({"exclusions": len(exclusions)}), flush=True)


if __name__ == "__main__":
    main()
