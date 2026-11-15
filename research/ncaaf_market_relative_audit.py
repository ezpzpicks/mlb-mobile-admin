"""Audit frozen NCAAF alternatives and test incremental factors chronologically.

Usage: python -m research.ncaaf_market_relative_audit --slates saved.json ...
       --outcomes outcomes.json --output audit.json
Outcomes: [{date, game_id, away_team, home_team, away_score, home_score,
            observed_at}]. observed_at is when a FINAL result was first observed;
it must never be backdated to kickoff. Missing outcomes remain missing.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import numpy as np

from builders.cfb_market_relative_matchups import finite, from_saved_row, instant


def frozen_games(payloads):
    """Earliest valid snapshot per date/game, independent of grade or result."""
    selected, rejected = {}, []
    for payload in payloads:
        rows = payload if isinstance(payload, list) else payload.get("slateToday", [])
        for row in rows:
            identity = (str(row.get("Date", ""))[:10], str(row.get("Game ID", "")))
            try:
                if not all(identity):
                    raise ValueError("Date/game identity missing")
                game = from_saved_row(row)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                rejected.append({"date": identity[0], "game_id": identity[1], "reason": str(exc)})
                continue
            old = selected.get(identity)
            if old is not None and old["teams"] != game["teams"]:
                raise ValueError("Conflicting team identities for the same game")
            if old is None or instant(game["as_of"]) < instant(old["as_of"]):
                selected[identity] = game
    return sorted(selected.values(), key=lambda g: (g["kickoff"], g["game_id"])), rejected


def join_outcomes(games, outcomes):
    index = {}
    for outcome in outcomes:
        identity = (str(outcome["date"])[:10], str(outcome["game_id"]))
        previous = index.get(identity)
        if previous and any(previous[k] != outcome[k] for k in ("away_score", "home_score", "away_team", "home_team")):
            raise ValueError("Conflicting final scores or team identities")
        if previous is None or instant(outcome["observed_at"]) < instant(previous["observed_at"]):
            index[identity] = outcome
    joined = []
    for game in games:
        outcome = index.get((game["date"], game["game_id"]))
        if outcome is None:
            continue
        if outcome["away_team"] != game["teams"]["away"] or outcome["home_team"] != game["teams"]["home"]:
            raise ValueError("Outcome team identity mismatch")
        observed = instant(outcome["observed_at"])
        if observed <= instant(game["kickoff"]):
            raise ValueError("A final score cannot be observed before kickoff")
        away, home = map(finite, (outcome["away_score"], outcome["home_score"]))
        if min(away, home) < 0 or not away.is_integer() or not home.is_integer():
            raise ValueError("Final scores must be nonnegative integers")
        joined.append({**game, "outcome_observed_at": observed.isoformat(),
                       "actual_margin": home - away, "actual_total": home + away})
    return joined


def settle(candidate, game):
    if candidate["market"] == "Spread":
        margin = game["actual_margin"] * (1 if candidate["side"] == "home" else -1) + candidate["line"]
    else:
        margin = (game["actual_total"] - candidate["line"]) * (1 if candidate["side"] == "Over" else -1)
    result = "Win" if margin > 1e-8 else "Loss" if margin < -1e-8 else "Push"
    price = candidate["odds"]
    profit = (100 / -price if price < 0 else price / 100) if result == "Win" else -1 if result == "Loss" else 0
    return result, profit


def record(values):
    counts = Counter(result for result, _ in values)
    n = counts["Win"] + counts["Loss"]
    p = counts["Win"] / n if n else None
    interval = None
    if n:
        z = 1.959963984540054
        center = (p + z*z/(2*n)) / (1 + z*z/n)
        half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
        interval = [round(100*(center-half), 1), round(100*(center+half), 1)]
    profit = sum(units for _, units in values)
    return {"wins": counts["Win"], "losses": counts["Loss"], "pushes": counts["Push"],
        "bets": len(values), "win_pct": round(100*p, 2) if p is not None else None,
        "wilson_95_pct": interval, "units": round(profit, 4),
        "roi_pct": round(100*profit/len(values), 2) if values else None}


def baseline_records(games):
    """Fixed existing forecast, not a fitted or validated new matchup policy."""
    groups = {}
    for game in games:
        for candidate in game["alternatives"]:
            if candidate["edge_points"] <= 0:
                continue
            result = settle(candidate, game)
            role = candidate["role"]
            groups.setdefault(role, []).append(result)
            groups.setdefault(f"{role} | line {candidate['market_band']}", []).append(result)
    return {key: record(values) for key, values in sorted(groups.items())}


def features(game, market, extended):
    """Market-aware baseline versus the SAME baseline plus matchup factors.

    One row per game per market; opposing bets are not independent observations.
    Train-only standardization and a fixed ridge penalty prevent test-date tuning.
    """
    z = game["z"]
    pace = (z["home"]["tempo"] + z["away"]["tempo"]) / 2
    if market == "Spread":
        edge = game["projected_home_margin"] + game["home_line"]
        values = [edge, game["home_line"]]
        if extended:
            offense = z["home"]["offense"] - z["away"]["offense"]
            defense = z["away"]["defense"] - z["home"]["defense"]
            values += [offense, defense, pace, pace*edge]
    else:
        edge = game["projected_total"] - game["total_line"]
        values = [edge, game["total_line"]]
        if extended:
            scoring = sum(z[s][m] for s in ("away", "home") for m in ("offense", "defense")) / 4
            values += [scoring, pace, pace*scoring]
    return values


def target(game, market):
    return game["actual_margin"] + game["home_line"] if market == "Spread" else game["actual_total"] - game["total_line"]


def ridge_predict(training, test, market, extended, penalty=10.):
    x = np.asarray([features(g, market, extended) for g in training], dtype=float)
    y = np.asarray([target(g, market) for g in training], dtype=float)
    mean, sd = x.mean(axis=0), x.std(axis=0)
    sd[sd < 1e-8] = 1
    design = np.column_stack([np.ones(len(x)), (x-mean)/sd])
    regularizer = np.eye(design.shape[1]) * penalty
    regularizer[0, 0] = 0
    coef = np.linalg.solve(design.T @ design + regularizer, design.T @ y)
    query = np.r_[1., (np.asarray(features(test, market, extended))-mean)/sd]
    return float(query @ coef)


def walk_forward(games, *, min_training_games=50, min_training_dates=3):
    """Require final results OBSERVED before each held-out pregame decision."""
    if min_training_games < 2 or min_training_dates < 1:
        raise ValueError("Invalid training floor")
    predictions, unavailable = [], Counter()
    for game in sorted(games, key=lambda g: (g["as_of"], g["game_id"])):
        training = [g for g in games if (g["date"], g["game_id"]) != (game["date"], game["game_id"])
            and g["model_version"] == game["model_version"]
            and instant(g["as_of"]) < instant(game["as_of"])
            and instant(g["outcome_observed_at"]) < instant(game["as_of"])]
        if len(training) < min_training_games or len({g["date"] for g in training}) < min_training_dates:
            unavailable["Insufficient earlier, settled, time-verified training games"] += 1
            continue
        for market in ("Spread", "Total"):
            baseline = ridge_predict(training, game, market, False)
            extended = ridge_predict(training, game, market, True)
            truth = target(game, market)
            predictions.append({"date": game["date"], "game_id": game["game_id"],
                "market": market, "training_games": len(training),
                "baseline_residual": baseline, "extended_residual": extended, "actual_residual": truth,
                "baseline_absolute_error": abs(truth-baseline), "extended_absolute_error": abs(truth-extended)})
    summary = {}
    by_identity = {(g["date"],g["game_id"]):g for g in games}
    for market in ("Spread", "Total"):
        rows = [p for p in predictions if p["market"] == market]
        result = {"held_out_games": len(rows), "baseline_mae": None, "extended_mae": None,
                  "mae_improvement": None, "baseline_by_role": {}, "extended_by_role": {}}
        if rows:
            for model in ("baseline", "extended"):
                result[f"{model}_mae"] = round(float(np.mean([p[f"{model}_absolute_error"] for p in rows])), 4)
                groups = {}
                for prediction in rows:
                    game = by_identity[(prediction["date"], prediction["game_id"])]
                    value = prediction[f"{model}_residual"]
                    if abs(value) < 1e-8:
                        continue
                    side = ("home" if value > 0 else "away") if market == "Spread" else ("Over" if value > 0 else "Under")
                    candidate = next(c for c in game["alternatives"] if c["market"] == market and c["side"] == side)
                    groups.setdefault(candidate["role"], []).append(settle(candidate, game))
                result[f"{model}_by_role"] = {k:record(v) for k,v in groups.items()}
            result["mae_improvement"] = round(result["baseline_mae"]-result["extended_mae"],4)
        summary[market] = result
    return {"status": "Exploratory out-of-sample comparison" if predictions else "Collecting pregame history",
        "minimum_training_games": min_training_games, "minimum_training_dates": min_training_dates,
        "unavailable_games": dict(unavailable), "summary": summary, "predictions": predictions,
        "promoted": False, "calibrated_probabilities": False}


def audit(payloads, outcomes):
    games, rejected = frozen_games(payloads)
    settled = join_outcomes(games, outcomes)
    edges = {}
    for role in ("Favorite", "Underdog", "Pick'em", "Over", "Under"):
        candidates = [c for g in games for c in g["alternatives"] if c["role"] == role]
        edges[role] = {"alternatives": len(candidates), "positive_projection_edge": sum(c["edge_points"] > 0 for c in candidates)}
    return {"generated_at": datetime.now(timezone.utc).isoformat(),
        "basis": "Original paired pregame quotes; existing point forecasts; new matchup adjustment is research only",
        "games": len(games), "dates": sorted({g["date"] for g in games}),
        "alternatives": sum(len(g["alternatives"]) for g in games),
        "settled_games_with_verified_identity": len(settled), "missing_final_scores": len(games)-len(settled),
        "coverage_warning": "Partial result coverage must not be used to promote a betting policy" if len(settled) != len(games) else None,
        "edge_counts": edges, "fixed_forecast_results_partial": baseline_records(settled),
        "walk_forward": walk_forward(settled), "rejected": rejected,
        "frozen_games": games}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slates", nargs="+", required=True)
    parser.add_argument("--outcomes", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = audit([json.loads(Path(p).read_text()) for p in args.slates], json.loads(Path(args.outcomes).read_text()))
    Path(args.output).write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k not in ("frozen_games", "rejected", "fixed_forecast_results_partial")},indent=2))


if __name__ == "__main__":
    main()
