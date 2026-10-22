"""Prospective NBA shadow-study protocol. Pure validation and scoring.

One first locked forecast per game/model version. Results are separate source
observations; no function here rewrites a prediction or chooses a scenario later.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import json
import math

from builders.nba_live import digest
from builders.nba_model import InputError, MODEL_VERSION, _serial, evaluate_package, timestamp

EXPERIMENT_ID = "nba-2026-27-prospective-v1"
LOCK_SCHEMA = "nba-prospective-lock-v1"
RESULT_SCHEMA = "nba-prospective-result-v1"
MAX_LOCK_AGE = timedelta(minutes=60)


def _seal(record):
    record = json.loads(json.dumps(record, default=_serial, allow_nan=False))
    record["record_sha256"] = digest(record)
    return record


def verify_record(record: dict, schema: str):
    if record.get("schema") != schema:
        raise InputError("Unsupported NBA tracking record.")
    unhashed = {k: v for k, v in record.items() if k != "record_sha256"}
    if digest(unhashed) != record.get("record_sha256"):
        raise InputError("NBA tracking record fingerprint does not match its contents.")


def _same_game(package: dict, game: dict):
    origin = package["live_provenance"]["game"]
    if game["game_id"] != package["context"]["game_id"] or game["season_year"] != origin["season_year"]:
        raise InputError("Live verification belongs to a different NBA game or season.")
    for side in ("away", "home"):
        if game[side]["espn_team_id"] != origin[side]["espn_team_id"] or game[side]["name"] != origin[side]["name"]:
            raise InputError("Live verification teams do not match the forecast.")


def lock_prediction(package: dict, scenario_name: str, game: dict, *, locked_at,
                    roster_reviewed: bool, minutes_reviewed: bool, review_note: str,
                    automation_policy: str | None = None) -> dict:
    """Recompute and seal the explicitly selected forecast before server tipoff.

    ``game`` must come from a fresh source request, not the user's editable
    context. Wall-clock time is supplied by the server at the final save step.
    """
    if package.get("is_demo") or not package.get("live_provenance"):
        raise InputError("Live tracking requires a source-verified game and current rosters; fictional/import-only examples cannot be locked.")
    now = timestamp(locked_at, "server lock timestamp")
    cutoff = timestamp(package["context"]["as_of"], "forecast cutoff")
    tipoff = timestamp(package["context"]["tipoff"], "forecast tipoff")
    _same_game(package, game)
    if (game["status"] != "STATUS_SCHEDULED" or game["state"] != "pre" or game["completed"] or
            game["period"] != 0 or not game["time_valid"]):
        raise InputError("The game has started, is postponed, or has no verified start time; no pregame lock was saved.")
    if timestamp(game["tipoff"], "verified tipoff") != tipoff:
        raise InputError("Scheduled tipoff changed. Rebuild and review the forecast before locking it.")
    if not cutoff <= now < tipoff or now - cutoff > MAX_LOCK_AGE:
        raise InputError("A live lock needs a cutoff within the last 60 minutes and must be saved before scheduled tipoff.")
    observed = timestamp(game["source"]["observed_at"], "live verification time")
    if not timedelta(0) <= now - observed <= timedelta(minutes=2):
        raise InputError("Refresh game status immediately before locking the prediction.")
    tracking_version, review_method = MODEL_VERSION, "manual"
    if automation_policy is not None:
        from builders.nba_automation import validate_automated_package
        tracking_version = validate_automated_package(package, game, now, automation_policy)
        if roster_reviewed is not False or minutes_reviewed is not False:
            raise InputError("An automated forecast cannot claim human roster or minute review.")
        if scenario_name != package["automation"]["primary_scenario"]:
            raise InputError("Automatic tracking must use the fixed primary scenario, not a sensitivity alternative.")
        review_method = "automated"
    elif roster_reviewed is not True or minutes_reviewed is not True:
        raise InputError("Review both current rosters and projected minutes, and record the outstanding assumptions.")
    if not isinstance(review_note, str) or not review_note.strip():
        raise InputError("Record the outstanding forecast assumptions.")
    provenance = package["live_provenance"]
    if provenance.get("conflicts"):
        raise InputError("Current roster and injury sources disagree. Resolve the reported conflicts before locking.")
    injury = package.get("injury_provenance")
    if not injury or now - timestamp(injury["feed_as_of"], "injury source update") > MAX_LOCK_AGE:
        raise InputError("Refresh the ESPN injury source before locking (maximum age 60 minutes).")
    if timestamp(injury["fetched_at"], "injury observation") > cutoff:
        raise InputError("Injury snapshot was observed after the forecast cutoff.")
    if injury.get("audit", {}).get("unmatched"):
        raise InputError("Resolve the selected teams' conflicting or unmatched ESPN injury identities before locking.")
    known = set()
    for side in ("away", "home"):
        roster = provenance["rosters"][side]
        if now - timestamp(roster["source"].get("feed_as_of", roster["source"]["observed_at"]), "roster update") > timedelta(days=1):
            raise InputError("Refresh current rosters before locking (maximum age one day).")
        if timestamp(roster["source"]["observed_at"], "roster observation") > cutoff:
            raise InputError("Roster snapshot was observed after the forecast cutoff.")
        ids = set(provenance["identity"][side].values())
        if known & ids:
            raise InputError("Current rosters assign one athlete to both teams.")
        known |= ids
        for scenario in package["scenarios"]:
            if any(p["minutes"] > 0 and p["player_id"] not in ids for p in scenario[side]["players"]):
                raise InputError("A player with projected minutes is absent from the checked current roster.")
        for item in provenance["recent"][side]["games"]:
            if timestamp(item["game"]["tipoff"], "minute evidence game") >= cutoff or timestamp(item["game"]["source"]["observed_at"], "minute evidence observation") > cutoff:
                raise InputError("Recent-minute evidence must have been observed before the forecast cutoff.")
    projection = evaluate_package(package)
    selected = [p for p in projection["scenarios"] if p["scenario"] == scenario_name]
    if len(selected) != 1:
        raise InputError("Select exactly one scenario before locking. A scenario cannot be selected after the result.")
    key = digest({"experiment": EXPERIMENT_ID, "version": tracking_version, "game_id": game["game_id"]})
    return _seal({"schema": LOCK_SCHEMA, "experiment_id": EXPERIMENT_ID,
        "record_id": key, "model_version": tracking_version, "core_model_version": MODEL_VERSION,
        "game_id": game["game_id"],
        "season_year": game["season_year"], "locked_at": now.isoformat(), "cutoff": cutoff.isoformat(),
        "tipoff": tipoff.isoformat(), "selected_scenario": scenario_name,
        "selected": selected[0], "inputs": deepcopy(package), "projection": projection,
        "verification": deepcopy(game), "roster_reviewed": roster_reviewed, "minutes_reviewed": minutes_reviewed,
        "review_method": review_method, "automation_policy": automation_policy,
        "review_note": review_note.strip()[:4000], "research_only": True, "eligible_for_posting": False})


def result_observation(game: dict, box_minutes: dict | None = None, *, box_error: str = "") -> dict:
    """Keep source observations, including non-final statuses, separate from forecasts."""
    payload = {"schema": RESULT_SCHEMA, "game_id": game["game_id"],
        "observed_at": game["source"]["observed_at"], "game": deepcopy(game),
        "box_minutes": deepcopy(box_minutes), "box_error": box_error}
    payload["observation_id"] = digest(payload)
    return _seal(payload)


def unique_predictions(records: list[dict]) -> list[dict]:
    """Reject contradictory copies rather than choosing a more favorable forecast."""
    unique = {}
    for record in records:
        verify_record(record, LOCK_SCHEMA)
        key = (record["experiment_id"], record["model_version"], record["game_id"])
        if key in unique and unique[key]["record_sha256"] != record["record_sha256"]:
            raise InputError("Conflicting locked forecasts for the same game and version; review the tracking ledger.")
        unique[key] = record
    return sorted(unique.values(), key=lambda p: (p["locked_at"], p["record_id"]))


def _grade(direction, actual):
    if direction == 0:
        return "PASS"
    if abs(actual) < 1e-9:
        return "PUSH"
    return "WIN" if direction * actual > 0 else "LOSS"


def grade_prediction(record: dict, observation: dict | None) -> dict:
    verify_record(record, LOCK_SCHEMA)
    selected = record["selected"]
    row = {"record_id": record["record_id"], "game_id": record["game_id"],
           "model_version": record["model_version"], "locked_at": record["locked_at"],
           "game": f"{selected['away_team']} at {selected['home_team']}",
           "fair_home_spread": selected["fair_home_spread"], "forecast_total": selected["total"],
           "scenario": record["selected_scenario"], "conditional": not selected["lineup_ready"],
           "spread_pick": selected.get("spread_pick", ""), "total_pick": selected.get("total_pick", ""),
           "spread_grade": selected.get("spread_grade", "No market line"), "total_grade": selected.get("total_grade", "No market line"),
           "status": "PENDING", "spread_result": "NO LINE", "total_result": "NO LINE"}
    if observation is None:
        return row
    verify_record(observation, RESULT_SCHEMA)
    game = observation["game"]
    if observation["game_id"] != record["game_id"]:
        raise InputError("Result observation belongs to a different NBA game.")
    _same_game(record["inputs"], game)
    row["source_status"] = game["status"]
    if not game["completed"] or game["status"] != "STATUS_FINAL":
        row["status"] = game["status"]
        return row
    final_tipoff = timestamp(game["tipoff"], "result start time")
    if timestamp(record["locked_at"], "lock time") >= final_tipoff or timestamp(observation["observed_at"], "result observation") <= final_tipoff:
        row["status"] = "EXCLUDED: start-time conflict"
        return row
    actual_margin = game["home"]["score"] - game["away"]["score"]
    actual_total = game["home"]["score"] + game["away"]["score"]
    row.update(status="FINAL", actual_away=game["away"]["score"], actual_home=game["home"]["score"],
        actual_home_margin=actual_margin, actual_total=actual_total,
        margin_error=selected["home_margin"] - actual_margin,
        total_error=selected["total"] - actual_total,
        baseline_margin_error=selected["baseline_home_margin"] - actual_margin,
        baseline_total_error=selected["baseline_total"] - actual_total,
        winner_correct=selected["home_margin"] * actual_margin > 0,
        overtime=game["period"] > 4)
    market = selected.get("market") or {}
    if market.get("home_spread") is not None:
        row["spread_result"] = _grade(selected["home_margin"] + market["home_spread"], actual_margin + market["home_spread"])
    if market.get("total") is not None:
        row["total_result"] = _grade(selected["total"] - market["total"], actual_total - market["total"])
    boxes = observation.get("box_minutes")
    if boxes:
        minute_errors = []
        for side in ("away", "home"):
            actual = {p["espn_athlete_id"]: p["minutes"] for p in boxes.get(game[side]["espn_team_id"], [])}
            projected = {p["player_id"]: p["minutes"] for p in selected[side + "_rotation"]["changes"]}
            for eid, pid in record["inputs"]["live_provenance"]["identity"][side].items():
                if projected.get(pid, 0) > 0 or actual.get(eid, 0) > 0:
                    minute_errors.append(abs(projected.get(pid, 0) - actual.get(eid, 0)))
        row["rotation_minutes_mae"] = sum(minute_errors) / len(minute_errors) if minute_errors else None
        row["rotation_players_compared"] = len(minute_errors)
    return row


def tracking_report(records: list[dict], observations: list[dict]) -> dict:
    records = unique_predictions(records)
    latest = {}
    for observation in observations:
        verify_record(observation, RESULT_SCHEMA)
        key = observation["game_id"]
        if key not in latest or timestamp(observation["observed_at"], "result time") > timestamp(latest[key]["observed_at"], "result time"):
            latest[key] = observation
    rows = [grade_prediction(r, latest.get(r["game_id"])) for r in records]
    groups = []
    for version in sorted({r["model_version"] for r in rows}):
        sample = [r for r in rows if r["model_version"] == version]
        finals = [r for r in sample if r["status"] == "FINAL"]
        group = {"model_version": version, "locked_games": len(sample), "graded_games": len(finals),
                 "pending_or_excluded": len(sample) - len(finals),
                 "conditional_games": sum(r["conditional"] for r in finals)}
        for prefix in ("", "baseline_"):
            for target in ("margin", "total"):
                values = [r[prefix + target + "_error"] for r in finals]
                group[prefix + target + "_mae"] = sum(abs(v) for v in values) / len(values) if values else None
                group[prefix + target + "_rmse"] = math.sqrt(sum(v * v for v in values) / len(values)) if values else None
                group[prefix + target + "_bias"] = sum(values) / len(values) if values else None
        group["winner_accuracy"] = sum(r["winner_correct"] for r in finals) / len(finals) if finals else None
        for market in ("spread", "total"):
            counts = {s.lower(): sum(r[market + "_result"] == s for r in finals) for s in ("WIN", "LOSS", "PUSH", "PASS", "NO LINE")}
            group[market + "_record"] = counts
            decisions = counts["win"] + counts["loss"]
            group[market + "_win_rate"] = counts["win"] / decisions if decisions else None
        groups.append(group)
    return {"experiment_id": EXPERIMENT_ID, "policy": "First pregame lock per game and model version; selected scenario fixed before tipoff; all locked games retained; final scores include overtime; versions reported separately; no assumed odds or ROI.",
            "summary": groups, "games": rows}


def export_ledger(records, observations):
    return json.dumps({"predictions": unique_predictions(records), "observations": observations,
                       "report": tracking_report(records, observations)}, sort_keys=True, indent=2, allow_nan=False)
