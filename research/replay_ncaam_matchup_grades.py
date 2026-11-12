#!/usr/bin/env python3
"""Offline candidate grading and separately invoked outcome settlement.

The grade command reads saved pregame inputs; it does not read the scoreboard.
The settle command cannot change a frozen selection. No production writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from builders.ncaam_matchup_grades import (
    POLICY_PATH, grade_saved_forecast, load_policy, policy_digest, population_benchmark, stamp,
)

ET = ZoneInfo("America/New_York")


def read(path):
    raw = Path(path).read_bytes()
    return json.loads(raw), {"file": Path(path).name, "sha256": hashlib.sha256(raw).hexdigest()}


def latest_forecasts(payload, as_of):
    market = payload["ncaamMarket"]
    day = market["date"]
    games = {str(g["Game ID"]): g for g in market["games"]}
    chosen = {}; rejected = Counter()
    for f in market["forecasts"]:
        try:
            game_id = str(f["gameId"]); locked = stamp(f["lockedAt"]); tip = stamp(f["tipoff"])
            if game_id not in games or not locked < tip or locked > stamp(as_of):
                rejected["unavailable_or_not_pregame"] += 1; continue
            if tip.astimezone(ET).date().isoformat() != day or locked >= stamp(games[game_id]["Game Time"]):
                rejected["date_or_scheduled_tipoff_mismatch"] += 1; continue
            order = (locked, f.get("modelVersion", "") + "|" + f.get("recordId", ""))
            if game_id not in chosen or order > chosen[game_id][0]:
                chosen[game_id] = (order, f)
        except (KeyError, TypeError, ValueError):
            rejected["malformed_forecast"] += 1
    return games, {key: value[1] for key, value in chosen.items()}, dict(rejected)


def grade_slate(public_path, benchmark_path, reference_paths, policy_path, as_of):
    public, public_source = read(public_path)
    cache, cache_source = read(benchmark_path)
    policy = load_policy(policy_path)
    benchmarks = [population_benchmark(item["bundle"]) for item in cache["snapshots"]]
    games, forecasts, rejected = latest_forecasts(public, as_of)
    day = public["ncaamMarket"]["date"]
    quotes = []; sources = []
    for path in reference_paths:
        reference_public, source = read(path)
        if reference_public["ncaamMarket"]["date"] >= day:
            raise ValueError("Market reference must use earlier game dates only.")
        _, previous, _ = latest_forecasts(reference_public, as_of)
        sources.append(source)
        for game_id, f in previous.items():
            quotes.append({"game_id": game_id, "total": f["market"]["total"], "observed_at": f["market"]["as_of"]})
    rows = []
    for game_id, game in sorted(games.items(), key=lambda item: (item[1]["Game Time"], item[0])):
        f = forecasts.get(game_id)
        base = {"game_id": game_id, "date": day, "matchup": game["Game"], "tipoff": game["Game Time"],
                "away_team_id": str(game["Away Team ID"]), "home_team_id": str(game["Home Team ID"])}
        if f is None:
            rows.append({**base, "side": None, "grade": "Research", "reason": "No valid saved pregame forecast", "input_error": True})
            continue
        try:
            grade = grade_saved_forecast(f, benchmarks, quotes, policy)
        except (KeyError, TypeError, ValueError) as exc:
            rows.append({**base, "side": None, "grade": "Research", "reason": str(exc), "input_error": True})
            continue
        gap = float(f["total"]) - float(f["market"]["total"])
        old_grade = "A" if abs(gap) >= 11 else "B" if 7.5 <= abs(gap) <= 10.9 else "Research"
        rows.append({**base, **grade, "record_id": f["recordId"], "model_version": f["modelVersion"],
                     "ratings_observed_at": f["ratings_observed_at"], "away_rating": {m: f["away_rating"][m] for m in ("offense", "defense", "tempo")},
                     "home_rating": {m: f["home_rating"][m] for m in ("offense", "defense", "tempo")},
                     "model_context": {"projection": f["total"], "signed_edge": round(gap, 8),
                                       "side": "Over" if gap >= 0 else "Under", "hypothetical_old_edge_grade": old_grade}, "input_error": False})
    return {"policy_version": policy["version"], "policy_sha256": policy_digest(policy), "policy": policy,
            "test_type": "Retrospective replay; this date was included in earlier exploratory analysis",
            "game_date_et": day, "input_as_of_utc": stamp(as_of).isoformat(),
            "sources": {"public_board": public_source, "national_cache": cache_source, "prior_market_inputs": sources},
            "grading_does_not_read": ["final scores", "stored total results", "model total edge"],
            "rejected_forecast_rows": rejected, "rows": rows}


def record(rows):
    c = Counter(r["result"] for r in rows)
    settled = c["W"] + c["L"] + c["P"]
    decisions = c["W"] + c["L"]
    units = c["W"] / 1.1 - c["L"]
    return {"plays": len(rows), "wins": c["W"], "losses": c["L"], "pushes": c["P"], "pending": c["Pending"],
            "win_pct": 100 * c["W"] / decisions if decisions else None,
            "net_units_at_minus_110": units, "roi_pct": 100 * units / settled if settled else None}


def settle(frozen, scoreboard, grades_sha, scoreboard_source, results_as_of):
    events = {str(e["id"]): e for e in scoreboard["events"]}
    rows = []
    for saved in frozen["rows"]:
        row = dict(saved); event = events.get(row["game_id"])
        actual = None; status = "Unavailable"
        if event:
            comp = event["competitions"][0]
            competitors = {c["homeAway"]: c for c in comp["competitors"]}
            if (str(competitors["away"]["team"]["id"]) != row["away_team_id"]
                    or str(competitors["home"]["team"]["id"]) != row["home_team_id"]):
                raise ValueError("ESPN home/away team identity mismatch.")
            if row.get("as_of") and stamp(row["as_of"]) >= stamp(comp["date"]):
                raise ValueError("Forecast capture is not before ESPN scheduled tipoff.")
            state = event["status"]["type"]
            status = state["name"]
            if state.get("completed"):
                actual = float(competitors["away"]["score"]) + float(competitors["home"]["score"])
        row["actual_total"] = actual; row["score_status"] = status
        if row["grade"] not in ("A", "B") or row["side"] is None:
            row["result"] = "Not selected"
        elif actual is None:
            row["result"] = "Pending"
        else:
            margin = (actual-row["line"]) * (1 if row["side"] == "Over" else -1)
            row["result"] = "W" if margin > 0 else "L" if margin < 0 else "P"
        rows.append(row)
    selected = [r for r in rows if r["grade"] in ("A", "B")]
    assert len({r["game_id"] for r in selected}) == len(selected)
    return {**{k: v for k, v in frozen.items() if k != "rows"},
            "frozen_grades_sha256": grades_sha, "scoreboard_source": scoreboard_source,
            "results_as_of_utc": stamp(results_as_of).isoformat(), "summary": record(selected),
            "by_grade": {grade: record([r for r in selected if r["grade"] == grade]) for grade in ("A", "B")},
            "by_direction": {side: record([r for r in selected if r["side"] == side]) for side in ("Over", "Under")},
            "by_grade_direction": {grade+" "+side: record([r for r in selected if r["grade"] == grade and r["side"] == side]) for grade in ("A", "B") for side in ("Over", "Under")},
            "grade_counts": dict(Counter(r["grade"] for r in rows)), "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    grade = modes.add_parser("grade")
    grade.add_argument("--public", required=True); grade.add_argument("--benchmark-cache", required=True)
    grade.add_argument("--reference-public", nargs="+", required=True); grade.add_argument("--policy", default=str(POLICY_PATH))
    grade.add_argument("--as-of", required=True); grade.add_argument("--output", required=True)
    result = modes.add_parser("settle")
    result.add_argument("--grades", required=True); result.add_argument("--scoreboard", required=True)
    result.add_argument("--as-of", required=True); result.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.mode == "grade":
        output = grade_slate(args.public, args.benchmark_cache, args.reference_public, args.policy, args.as_of)
        print(json.dumps({"policy_sha256": output["policy_sha256"], "grade_counts": dict(Counter(r["grade"] for r in output["rows"])), "input_errors": sum(r["input_error"] for r in output["rows"])}))
    else:
        frozen, source = read(args.grades); scoreboard, board_source = read(args.scoreboard)
        output = settle(frozen, scoreboard, source["sha256"], board_source, args.as_of)
        print(json.dumps({"summary": output["summary"], "by_grade": output["by_grade"], "by_grade_direction": output["by_grade_direction"]}))
    Path(args.output).write_text(json.dumps(output, indent=2, allow_nan=False)+"\n")


if __name__ == "__main__": main()
