"""Read-only audit of captured NFL model ledger and saved slates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.stats import norm

from research.nfl_market_edges import implied, payout, week_bootstrap


def number(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        result = float(value)
        return result if np.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def team(value: Any) -> str:
    text = str(value).strip().upper()
    return {"LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR", "WSH": "WAS", "JAC": "JAX"}.get(text, text)


def identity(value: Any) -> str:
    parts = str(value).split("_")
    return "_".join(parts[:2] + [team(x) for x in parts[2:]]) if len(parts) == 4 else str(value)


def selection(value: Any) -> tuple[str, float] | None:
    text = str(value).strip().upper().replace("−", "-").replace("–", "-")
    match = re.fullmatch(r"([A-Z0-9]+)\s+([+-]?\d+(?:\.\d+)?)", text)
    return (team(match[1]), float(match[2])) if match else None


def outcome(signed: float) -> str:
    return "P" if abs(signed) < 1e-8 else "W" if signed > 0 else "L"


def result_code(value: Any) -> str:
    return {"WIN": "W", "WON": "W", "LOSS": "L", "LOST": "L", "PUSH": "P", "W": "W", "L": "L", "P": "P"}.get(str(value).strip().upper(), "")


def capture_timing(row: dict[str, Any], game: pd.Series) -> str:
    build = pd.to_datetime(row.get("Build At", ""), utc=True, errors="coerce")
    if pd.isna(build):
        return "unknown"
    scheduled = pd.to_datetime(str(game.gameday) + " " + str(game.gametime), errors="coerce")
    if pd.isna(scheduled):
        return "unknown"
    scheduled = scheduled.tz_localize(ZoneInfo("America/New_York")).tz_convert("UTC")
    return "pregame" if build <= scheduled else "after_kickoff"


def match_official(row: dict[str, Any], official: pd.DataFrame) -> str | None:
    game_id = identity(row.get("Game ID", ""))
    if game_id in official.index:
        return game_id
    # Some saved rows use ESPN numeric IDs. Match only a unique exact date and
    # canonical away/home pair; never a partial or fuzzy team-name match.
    date = str(row.get("Date") or row.get("Game Date") or "")[:10]
    away = team(row.get("Away Team", "")); home = team(row.get("Home Team", ""))
    candidates = official[official.gameday.eq(date) & official.away_team.map(team).eq(away) & official.home_team.map(team).eq(home)]
    return str(candidates.index[0]) if len(candidates) == 1 else None


def record(rows: pd.DataFrame) -> dict[str, Any]:
    counts = rows.result.value_counts() if len(rows) else {}
    w, l, p = (int(counts.get(x, 0)) for x in ["W", "L", "P"])
    units = float(rows.units.sum()) if len(rows) else 0.0
    decided = rows[rows.result.isin(["W", "L"])] if len(rows) else rows
    probabilities = decided.dropna(subset=["probability"]) if "probability" in decided else decided.iloc[:0]
    q = probabilities.probability.to_numpy(float) if len(probabilities) else np.array([])
    y = probabilities.result.eq("W").to_numpy(float) if len(probabilities) else np.array([])
    return {"n": len(rows), "wins": w, "losses": l, "pushes": p, "units": units,
            "roi": units / len(rows) if len(rows) else None,
            "win_rate_decided": w / (w + l) if w + l else None,
            "mean_probability_decided": float(q.mean()) if len(q) else None,
            "brier": float(np.square(q - y).mean()) if len(q) else None,
            "mean_quote_break_even": float(implied(rows.odds).mean()) if len(rows) else None,
            "roi_week_bootstrap_95pct": week_bootstrap(rows, rows.units.to_numpy()) if len(rows) else [None, None]}


def groups(rows: pd.DataFrame, column: str) -> dict[str, Any]:
    return {str(key): record(group) for key, group in rows.groupby(column, dropna=False)}


def settle_ledger(raw: list[dict[str, Any]], official: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    rows = []; errors = []; seen = set()
    for source in raw:
        if str(source.get("Bet Type", "")).lower() != "spread":
            continue
        game_id = identity(source.get("Game ID", "")); pick = selection(source.get("Selection", ""))
        if game_id in seen:
            errors.append({"game_id": game_id, "reason": "duplicate spread ledger entry"})
            continue
        seen.add(game_id)
        if game_id not in official.index or pick is None:
            errors.append({"game_id": game_id, "reason": "unmatched official game or invalid selection"})
            continue
        game = official.loc[game_id]; chosen_team, handicap = pick
        if chosen_team not in [team(game.home_team), team(game.away_team)]:
            errors.append({"game_id": game_id, "reason": "selected team is not in matchup"})
            continue
        odds = number(source.get("Odds/Line"))
        if odds is None or abs(odds) < 100:
            errors.append({"game_id": game_id, "reason": "missing or invalid saved odds"})
            continue
        pick_home = chosen_team == team(game.home_team)
        margin = float(game.home_score - game.away_score)
        signed = (margin if pick_home else -margin) + handicap
        result = outcome(signed); stored = result_code(source.get("Result"))
        if stored and stored != result:
            errors.append({"game_id": game_id, "reason": "stored result disagrees with official settlement", "stored": stored, "verified": result})
        probability = number(source.get("Model Probability"))
        if probability is not None and not 0 <= probability <= 1:
            raise ValueError("Saved probability is outside [0,1]")
        rows.append({"game_id": game_id, "season": int(game.season), "week": int(game.week), "gameday": game.gameday,
                     "grade": source.get("Grade", ""), "selection": source.get("Selection"), "pick_home": pick_home,
                     "favorite": handicap < 0, "handicap": handicap, "odds": odds, "probability": probability,
                     "recorded_implied_probability": number(source.get("Implied Probability")),
                     "quote_break_even": float(implied([odds])[0]), "recorded_edge": number(source.get("Edge")),
                     "recorded_expected_value": number(source.get("Expected Value")),
                     "result": result, "stored_result": stored,
                     "units": float(payout([odds])[0]) if result == "W" else -1.0 if result == "L" else 0.0,
                     "model_version": source.get("Model Version"), "capture_timing": capture_timing(source, game),
                     "period": "through_2026_10_19" if game.gameday <= "2026-10-19" else "after_2026_10_19"})
    return pd.DataFrame(rows), errors


def saved_forecasts(paths: list[Path], official: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    source_rows = {}; exclusions = []
    for path in paths:
        for row in json.loads(path.read_text()).get("slateToday", []):
            game_id = match_official(row, official) or identity(row.get("Game ID", ""))
            if game_id:
                score = lambda candidate: sum(number(candidate.get(field)) is not None for field in ["Projected Home", "Projected Away", "Projected Margin", "Projected Total"])
                if game_id not in source_rows or score(row) >= score(source_rows[game_id]):
                    source_rows[game_id] = row
    result = []
    for game_id, row in source_rows.items():
        if game_id not in official.index:
            exclusions.append({"game_id": game_id, "reason": "no completed official matchup"}); continue
        game = official.loc[game_id]
        home = number(row.get("Projected Home")); away = number(row.get("Projected Away"))
        margin = number(row.get("Projected Margin")); total = number(row.get("Projected Total")); line = number(row.get("Market Home Spread"))
        recovered_line = False
        pick = selection(row.get("Spread Pick", ""))
        if line is None and pick and pick[0] in [team(game.home_team), team(game.away_team)]:
            line = pick[1] if pick[0] == team(game.home_team) else -pick[1]
            recovered_line = True
        if any(x is None for x in [home, away, margin, total, line]) or home <= 0 or away <= 0 or total < 20 or not str(row.get("Model Version", "")).strip():
            exclusions.append({"game_id": game_id, "reason": "missing or invalid saved forecast/line"}); continue
        if abs((home - away) - margin) > 0.25 or abs((home + away) - total) > 0.25:
            exclusions.append({"game_id": game_id, "reason": "score, margin and total disagree"}); continue
        edge = margin + line
        if abs(edge) < 1e-8:
            exclusions.append({"game_id": game_id, "reason": "projection exactly on line; no directional pick"}); continue
        pick_home = edge > 0; actual_margin = float(game.home_score - game.away_score)
        result_code_value = outcome((actual_margin + line) * (1 if pick_home else -1))
        # The unattended builder explicitly assumes -110 game prices; manual
        # per-side fields are honored when present. Never use a closing price
        # from a different handicap to reprice this saved selection.
        source_odds = row.get("Home Spread Odds" if pick_home else "Away Spread Odds")
        odds = number(source_odds)
        price_assumed = odds is None
        odds = -110.0 if odds is None else odds
        result.append({"game_id": game_id, "season": int(game.season), "week": int(game.week), "gameday": game.gameday,
                       "grade": row.get("Spread Grade", ""), "model_version": row.get("Model Version"),
                       "pending_grade": "pending" in str(row.get("Spread Grade", "")).lower(),
                       "line_recovered_from_saved_selection": recovered_line,
                       "projected_margin": margin, "market_home_handicap": line, "edge": edge,
                       "absolute_edge": abs(edge), "actual_margin": actual_margin,
                       "pick_home": pick_home, "handicap": line if pick_home else -line,
                       "favorite": (line if pick_home else -line) < 0,
                       "probability": number(row.get("Spread Probability")), "odds": odds, "price_assumed": price_assumed,
                       "result": result_code_value, "units": float(payout([odds])[0]) if result_code_value == "W" else -1.0 if result_code_value == "L" else 0.0,
                       "capture_timing": capture_timing(row, game),
                       "period": "through_2026_10_19" if game.gameday <= "2026-10-19" else "after_2026_10_19"})
    return pd.DataFrame(result), exclusions


def threshold_checks(rows: pd.DataFrame) -> dict[str, Any]:
    development = rows[rows.gameday <= "2026-10-19"]
    later = rows[rows.gameday > "2026-10-19"]
    thresholds = [1.5, 2.5, 3.5]
    choices = {str(t): record(development[development.absolute_edge >= t]) for t in thresholds}
    eligible = [t for t in thresholds if choices[str(t)]["n"] >= 20 and choices[str(t)]["units"] > 0]
    selected = max(eligible, key=lambda t: choices[str(t)]["roi"]) if eligible else None
    return {"development": choices, "selected_on_early_sample": selected,
            "later_selected_rule": record(later[later.absolute_edge >= selected]) if selected is not None else None,
            "later_each_fixed_threshold": {str(t): record(later[later.absolute_edge >= t]) for t in thresholds}}


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--output-dir", default="artifacts/nfl_market_edges")
    parser.add_argument("--schedule", default="artifacts/nfl_team_weights/data/games.csv"); args = parser.parse_args()
    output = Path(args.output_dir); raw = output / "raw"
    schedule = pd.read_csv(args.schedule, low_memory=False)
    schedule = schedule[schedule.season.eq(2026) & schedule.game_type.eq("REG") & schedule.gameday.le("2026-11-21")].dropna(subset=["home_score", "away_score"])
    schedule["canonical_game_id"] = schedule.game_id.map(identity)
    official = schedule.set_index("canonical_game_id")
    board = json.loads((raw / "board.json").read_text())
    ledger, errors = settle_ledger(board["betTrackerRows"], official)
    forecasts, exclusions = saved_forecasts(sorted((raw / "slates").glob("*.json")), official)
    ledger.to_csv(output / "saved_spread_ledger_verified.csv", index=False)
    forecasts.to_csv(output / "saved_spread_forecasts_verified.csv", index=False)
    results = {"as_of": "2026-11-21", "official_completed_games": len(official), "ledger": record(ledger),
               "ledger_result_errors": errors, "ledger_by_grade": groups(ledger, "grade"),
               "ledger_by_period": groups(ledger, "period"), "ledger_by_capture_timing": groups(ledger, "capture_timing"),
               "ledger_by_favorite": groups(ledger, "favorite"), "ledger_by_home_side": groups(ledger, "pick_home"),
               "ledger_by_model_version": groups(ledger, "model_version"),
               "ledger_by_grade_and_period": groups(ledger.assign(group=ledger.grade + ":" + ledger.period), "group"),
               "ledger_expected_value_sum": float(ledger.recorded_expected_value.sum()),
               "ledger_recorded_50pct_implied_count": int(np.isclose(ledger.recorded_implied_probability, 0.5).sum()),
               "all_saved_forecasts": record(forecasts), "forecast_exclusions": exclusions,
               "forecasts_by_grade": groups(forecasts, "grade"), "forecasts_by_period": groups(forecasts, "period"),
               "forecasts_by_capture_timing": groups(forecasts, "capture_timing"),
               "forecasts_excluding_pending": record(forecasts[~forecasts.pending_grade]),
               "line_recovered_from_saved_selection_count": int(forecasts.line_recovered_from_saved_selection.sum()),
               "forecasts_by_grade_and_period": groups(forecasts.assign(group=forecasts.grade + ":" + forecasts.period), "group"),
               "forecast_threshold_checks": threshold_checks(forecasts),
               "forecast_threshold_checks_excluding_pending": threshold_checks(forecasts[~forecasts.pending_grade]),
               "closing_line_difference_mean_abs": float(((-forecasts.market_home_handicap).to_numpy() - official.loc[forecasts.game_id, "spread_line"].to_numpy()).__abs__().mean()),
               "notes": ["No live data was changed", "Ledger prices are recorded/model assumptions, not verified executed wagers", "Unknown capture times are not treated as demonstrable pregame snapshots", "Legacy schedule-only audit omits later games because its saved finals stop October 19"]}
    (output / "saved_audit_results.json").write_text(json.dumps(results, indent=2, allow_nan=False))
    print(json.dumps({key: results[key] for key in ["ledger", "ledger_result_errors", "ledger_by_grade", "ledger_by_grade_and_period", "all_saved_forecasts", "forecasts_by_period", "forecasts_by_capture_timing", "forecast_threshold_checks"]}, indent=2))


if __name__ == "__main__":
    main()
