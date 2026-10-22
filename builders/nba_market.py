"""Date-bounded ScoresAndOdds lines and explicit research market grades."""
from __future__ import annotations

from copy import deepcopy
import json
import math

from builders.nba_model import InputError, timestamp
from shared.market_grading import market_grade
from shared.ncaaf_auto_builder import eligible_markets, team_key

MARKET_POLICY = "nba-scoresandodds-v1"
GRADE_POLICY = "shared-market-grade-nba-validation-normal-v1"
# Frozen January–February validation errors of the selected ridge=15000,
# ten-game model, 399 games. These are not the March–April test errors.
# Source: research/nba_results_2025_26/nba_validation.json, selected.metrics.lineup.
MARGIN_SIGMA = 15.366295320255823
TOTAL_SIGMA = 19.366926088529077


def read_market_rows(day):
    from shared import turso_storage as db
    if not db.is_turso_ready():
        raise InputError("ScoresAndOdds market storage is not configured.")
    return [json.loads(r["payload_json"]) for r in db._result_rows(db._execute(
        "SELECT payload_json FROM dataset_rows WHERE sport='NBA' AND dataset='weekly_market_trends' "
        "AND date_key=" + db._sql_text(day) + " ORDER BY row_index"))]


def market_for_game(rows, game, now):
    now = timestamp(now, "market observation time")
    selected = [r for r in rows if str(r.get("Game ID")) == game["game_id"]]
    if len(selected) != 4 or any(r.get("Market Policy") != MARKET_POLICY for r in selected):
        raise InputError("Waiting for complete ScoresAndOdds spread and total pairs for this NBA game.")
    if len({r.get("Snapshot Status") for r in selected}) != 1 or len({r.get("Updated At") for r in selected}) != 1:
        raise InputError("ScoresAndOdds sides were not captured in the same NBA snapshot.")
    markets, skipped = eligible_markets(selected, now, max_age_minutes=35)
    if len(markets) != 1:
        raise InputError("NBA market lines are unavailable or stale: " + "; ".join(s["reason"] for s in skipped))
    market = markets[0]
    if (market.pair != (team_key(game["away"]["name"]), team_key(game["home"]["name"]))
            or market.kickoff != timestamp(game["tipoff"], "NBA scheduled tipoff")
            or market.captured > now):
        raise InputError("ScoresAndOdds NBA teams, start time or capture time do not match the live game.")
    lead = (market.kickoff - now).total_seconds() / 60
    age = (now - market.captured).total_seconds() / 60
    status = selected[0]["Snapshot Status"]
    if status == "FINAL_PREGAME":
        final_lead = (market.kickoff - market.captured).total_seconds() / 60
        if not 10 <= final_lead <= 20:
            raise InputError("ScoresAndOdds NBA final snapshot is outside the 10–20 minute window.")
    elif lead <= 90 and age > 10:
        raise InputError("Refresh ScoresAndOdds NBA lines inside the 90-minute countdown.")
    return {"home_spread": market.home_spread, "total": market.total,
            "as_of": market.captured.isoformat(), "source": "ScoresAndOdds"}, {
        "policy": MARKET_POLICY, "game_id": game["game_id"], "snapshot_status": status,
        "captured_at": market.captured.isoformat(), "source_url": selected[0].get("Source URL", ""),
        "assumed_odds": -110, "rows": deepcopy(selected)}


def apply_market(package, rows, game, now):
    market, provenance = market_for_game(rows, game, now)
    output = deepcopy(package)
    output["market"], output["market_provenance"] = market, provenance
    return output


def market_comparison(home_margin, projected_total, away_name, home_name, market):
    if market is None or market.home_spread is None or market.total is None:
        return {"spread_pick": "No market line", "total_pick": "No market line",
                "spread_grade": "No market line", "total_grade": "No market line"}
    spread_edge, total_edge = home_margin + market.home_spread, projected_total - market.total
    normal = lambda edge, sigma: 0.5 * (1 + math.erf(abs(edge) / (sigma * math.sqrt(2))))
    spread_probability, total_probability = normal(spread_edge, MARGIN_SIGMA), normal(total_edge, TOTAL_SIGMA)
    return {"spread_pick": f"{home_name} {market.home_spread:+g}" if spread_edge > 0 else
                f"{away_name} {-market.home_spread:+g}" if spread_edge < 0 else "Pass",
            "total_pick": f"{'Over' if total_edge > 0 else 'Under'} {market.total:g}" if total_edge else "Pass",
            "spread_grade": market_grade(spread_edge, spread_probability),
            "total_grade": market_grade(total_edge, total_probability),
            "spread_probability_estimate": spread_probability, "total_probability_estimate": total_probability,
            "grade_policy": GRADE_POLICY,
            "grade_basis": "Shared A/B/Lean/Pass thresholds; normal approximation from 399 pre-test NBA validation games",
            "probabilities_calibrated": False, "market_grades_research_only": True}
