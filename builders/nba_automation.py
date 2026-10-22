"""Fixed, explicitly conditional rotation policy for scheduled NBA research.

Automated early and near-tipoff forecasts have separate tracking versions. No
human acknowledgement, confirmed starting five, market line or probability is
invented. The original manual model/version remains independently tracked.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

from builders.nba_live import allocate_minutes, digest
from builders.nba_model import InputError, MODEL_VERSION, timestamp

POLICY = "nba-auto-market-v2"
PHASES = ("early", "pregame")
VERSIONS = {phase: f"{MODEL_VERSION}+{POLICY}-{phase}" for phase in PHASES}
EXPECTED_POLICY = "nba-auto-expected-rotation-v1"
EXPECTED_VERSIONS = {phase: f"{MODEL_VERSION}+{EXPECTED_POLICY}-{phase}" for phase in PHASES}
FINAL_WINDOW = timedelta(minutes=45)
MIN_LEAD = timedelta(minutes=3)
MAX_ALTERNATIVES = 6
NOTE = ("Automatic research forecast; no human roster or minute review. "
        "OUT/DOUBTFUL players excluded from the primary rotation; other unresolved "
        "players with minute evidence assumed IN. Prior 10 current-season games, "
        "falling back to dated historical minute seeds; proportional allocation "
        "to 240 minutes with individual caps. Single-player alternatives are "
        "sensitivity checks, not calibrated probabilities or full joint coverage. "
        "Starters remain unconfirmed and estimated return dates never clear an injury.")
EXPECTED_NOTE = ("Temporary expected-rotation forecast; injury feed bypassed by user choice. "
    "Availability unchecked and starters unconfirmed. Current-roster players with checked "
    "minute evidence are assumed IN; prior 10 current-season games fall back to historical "
    "minute seeds, allocated proportionally to 240 minutes with individual caps. "
    "No human review, injury clearance or public-posting eligibility is claimed.")


def expected_rotation(package):
    return package.get("availability_provenance", {}).get("mode") == "expected_rotation"


def phase_for_game(game, now):
    remaining = timestamp(game["tipoff"], "tipoff") - timestamp(now, "run time")
    if (game["status"] != "STATUS_SCHEDULED" or game["state"] != "pre" or
            game["completed"] or not game["time_valid"] or remaining < MIN_LEAD):
        return None
    return "pregame" if remaining <= FINAL_WINDOW else "early"


def _scenarios(package):
    original = package["scenarios"][0]
    primary = deepcopy(original)
    expected = expected_rotation(package)
    primary["name"] = ("Expected rotation: injury feed bypassed; evidenced roles assumed IN" if expected else
        "Automatic primary: OUT/DOUBTFUL out; other unresolved minute roles IN")
    weights, candidates = {}, []
    names = {p["player_id"]: p["name"] for side in ("away", "home") for p in package[side]["players"]}
    for side in ("away", "home"):
        evidence = {p["player_id"]: float(p["weight"]) for p in package["live_provenance"]["minute_audit"][side]}
        plan = primary[side]
        weights[side] = {p["player_id"]: evidence.get(p["player_id"], 0.0)
                         if p["status"] not in {"OUT", "DOUBTFUL"} else 0.0 for p in plan["players"]}
        allocated = allocate_minutes(weights[side], {p["player_id"]: p["minutes_limit"] for p in plan["players"]})
        for player in plan["players"]:
            pid = player["player_id"]
            player["minutes"] = allocated[pid]
            player["starter"] = False
            player["assumption"] = ("IN" if allocated[pid] else "OUT") if player["status"] not in {"OUT", "AVAILABLE"} else ""
            if player["status"] in {"QUESTIONABLE", "DAY_TO_DAY", "PROBABLE", "DOUBTFUL"} and evidence.get(pid, 0) > 0:
                candidates.append((evidence[pid], side, pid, evidence[pid]))
        plan.update(report_complete=False, starters_confirmed=False,
                    lineup_source=("Checked current rosters and minute evidence; expected rotation, injury feed bypassed"
                        if expected else "ESPN inputs; fixed automatic conditional rotation policy " + POLICY))
    scenarios, omitted = [primary], []
    for _, side, pid, evidence in sorted(candidates, key=lambda c: (-c[0], c[1], c[2]))[:MAX_ALTERNATIVES]:
        alternative = deepcopy(primary)
        active = next(p for p in primary[side]["players"] if p["player_id"] == pid)["minutes"] > 0
        other_weights = dict(weights[side]); other_weights[pid] = 0.0 if active else evidence
        try:
            allocated = allocate_minutes(other_weights, {p["player_id"]: p["minutes_limit"] for p in primary[side]["players"]})
        except InputError as exc:
            omitted.append({"player_id": pid, "reason": str(exc)})
            continue
        alternative["name"] = f"Sensitivity only: {names[pid]} {'OUT' if active else 'IN'}"
        for player in alternative[side]["players"]:
            player["minutes"] = allocated[player["player_id"]]
            if player["status"] not in {"OUT", "AVAILABLE"}:
                player["assumption"] = "IN" if player["minutes"] else "OUT"
        scenarios.append(alternative)
    return scenarios, omitted, max(0, len(candidates) - MAX_ALTERNATIVES)


def automated_package(package, phase):
    if phase not in PHASES or not package.get("live_provenance"):
        raise InputError("Automatic NBA preparation needs a live matchup and a supported phase.")
    output = deepcopy(package)
    expected = expected_rotation(output)
    if expected:
        if output.get("injury_provenance") or output["availability_provenance"].get("injury_verified") is not False:
            raise InputError("Expected rotations cannot claim injury verification.")
        for scenario in output["scenarios"]:
            for side in ("away", "home"):
                plan = scenario[side]
                if (plan["injury_as_of"] is not None or plan["report_complete"] or plan["starters_confirmed"]
                        or any(p["status"] != "UNKNOWN" or p.get("injury") is not None for p in plan["players"])):
                    raise InputError("Expected rotations require explicitly unchecked availability.")
    scenarios, omitted, truncated = _scenarios(output)
    output["scenarios"] = scenarios
    policy, versions, note = (EXPECTED_POLICY, EXPECTED_VERSIONS, EXPECTED_NOTE) if expected else (POLICY, VERSIONS, NOTE)
    output["automation"] = {"policy": policy, "phase": phase, "tracking_version": versions[phase],
        "primary_scenario": scenarios[0]["name"], "scenarios_sha256": digest(scenarios),
        "assumptions": note, "omitted_sensitivity_checks": omitted,
        "truncated_sensitivity_checks": truncated, "human_reviewed": False}
    return output


def validate_automated_package(package, game, now, policy):
    automatic = package.get("automation") or {}
    phase = automatic.get("phase")
    required_policy = EXPECTED_POLICY if expected_rotation(package) else POLICY
    if policy != required_policy or phase not in PHASES or phase_for_game(game, now) != phase:
        raise InputError("Automatic NBA policy or pregame build window does not match.")
    expected = automated_package(package, phase)
    if automatic != expected["automation"] or package["scenarios"] != expected["scenarios"]:
        raise InputError("Automatic NBA rotations were changed after applying the fixed policy.")
    from builders.nba_market import market_for_game
    provenance = package.get("market_provenance") or {}
    expected_market, _ = market_for_game(provenance.get("rows", []), game, now)
    if package.get("market") != expected_market:
        raise InputError("Automatic NBA tracking requires the original complete ScoresAndOdds market snapshot.")
    return expected["automation"]["tracking_version"]
