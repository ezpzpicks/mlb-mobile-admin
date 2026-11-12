"""Configurable factor-only NCAAM total grades; independent of model point edge.

The grading engine has no database/network writes. Production capture is in
ncaam_matchup_live.py; saved forecast replay is in research/.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics

METRICS = ("offense", "defense", "tempo")
RANK = {"Research": 0, "B": 1, "A": 2}
POLICY_PATH = Path(__file__).resolve().parents[1] / "config/ncaam_matchup_grades.json"


def stamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamps must include a timezone.")
    return parsed


def number(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a numeric input.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Numeric inputs must be finite.")
    return result


def policy_digest(policy):
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_policy(path=POLICY_PATH):
    policy = json.loads(Path(path).read_text())
    validate_policy(policy)
    return policy


def validate_policy(policy):
    if not policy.get("version") or not policy.get("profiles"):
        raise ValueError("Policy requires a version and profiles.")
    t = policy["thresholds"]
    if not 0 <= number(t["support_z"]) < number(t["extreme_z"]) < number(t["very_extreme_z"]):
        raise ValueError("Support, extreme and very-extreme thresholds must be ordered.")
    for name in ("max_benchmark_age_hours", "max_market_age_minutes"):
        if number(t[name]) <= 0:
            raise ValueError("Input age limits must be positive.")
    if number(t["opposing_matchup_tempo_z"]) <= 0:
        raise ValueError("Opposing tempo threshold must be positive.")
    m = policy["market_context"]
    if not 0 < number(m["low_quantile"]) < number(m["high_quantile"]) < 1:
        raise ValueError("Market quantiles must be strictly ordered within (0,1).")
    penalties = [m["downgrade_levels"], *policy["conflicts"].values()]
    if any(type(p) is not int or not 0 <= p <= 2 for p in penalties):
        raise ValueError("Downgrade levels must be integers from zero to two.")
    ids = set()
    for p in policy["profiles"]:
        if p["id"] in ids:
            raise ValueError("Duplicate profile ID.")
        ids.add(p["id"])
        if p["side"] not in ("Over", "Under") or p["grade"] not in ("A", "B"):
            raise ValueError("Invalid profile direction or grade.")
        if p["kind"] not in ("same_team_three", "tempo_supported", "extreme_pair_supported", "matchup_three"):
            raise ValueError("Unknown profile kind.")
        if type(p["enabled"]) is not bool or type(p["priority"]) is not int:
            raise ValueError("Profile enabled/priority fields have invalid types.")
        if p["kind"] == "tempo_supported" and p.get("tempo_threshold") not in ("extreme_z", "very_extreme_z"):
            raise ValueError("Tempo profile requires a named threshold.")


def population_benchmark(bundle):
    teams = list(bundle["teams"].values())
    if len(teams) < 300:
        raise ValueError("Incomplete Division I population.")
    result = {"observed_at": stamp(bundle["observed_at"]).isoformat(), "season": int(bundle["season"]), "team_count": len(teams), "metrics": {}}
    for metric in METRICS:
        values = [number(team[metric]) for team in teams]
        sd = statistics.pstdev(values)
        if sd <= 0:
            raise ValueError("Population standard deviation must be positive.")
        result["metrics"][metric] = {"mean": statistics.mean(values), "sd": sd}
    return result


def select_benchmark(benchmarks, as_of, season):
    cutoff = stamp(as_of)
    candidates = [b for b in benchmarks if int(b["season"]) == int(season) and stamp(b["observed_at"]) <= cutoff]
    if not candidates:
        raise ValueError("No dated national benchmark available before the forecast.")
    return deepcopy(max(candidates, key=lambda b: stamp(b["observed_at"])))


def quantile(values, q):
    values = sorted(number(v) for v in values)
    if not values:
        raise ValueError("No market reference quotes.")
    index = (len(values) - 1) * q
    lo = int(math.floor(index)); hi = int(math.ceil(index))
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


def market_reference(quotes, as_of, policy):
    cutoff = stamp(as_of)
    eligible = [q for q in quotes if stamp(q["observed_at"]) <= cutoff]
    if not eligible:
        raise ValueError("No market reference available before the forecast.")
    latest = {}
    for q in eligible:
        key = str(q["game_id"])
        if key not in latest or stamp(q["observed_at"]) > stamp(latest[key]["observed_at"]):
            latest[key] = q
    eligible = list(latest.values())
    values = [number(q["total"]) for q in eligible]
    low = quantile(values, policy["market_context"]["low_quantile"])
    high = quantile(values, policy["market_context"]["high_quantile"])
    if not low < high:
        raise ValueError("Degenerate market reference distribution.")
    return {"low_total": low, "high_total": high, "quote_count": len(eligible),
            "observed_at": max(stamp(q["observed_at"]) for q in eligible).isoformat()}


def _profile_matches(profile, z, thresholds):
    sign = 1 if profile["side"] == "Over" else -1
    directional = {loc: {m: sign * z[loc][m] for m in METRICS} for loc in ("away", "home")}
    support = thresholds["support_z"]
    paired = any(directional[loc]["offense"] > support and directional[opp]["defense"] > support
                 for loc, opp in (("away", "home"), ("home", "away")))
    mean_tempo = statistics.mean(directional[loc]["tempo"] for loc in directional)
    if profile["kind"] == "same_team_three":
        return any(all(directional[loc][m] > support for m in METRICS) for loc in directional)
    if profile["kind"] == "matchup_three":
        return all(max(directional[loc][m] for loc in directional) > support for m in METRICS)
    if profile["kind"] == "tempo_supported":
        return (max(directional[loc]["tempo"] for loc in directional) >= thresholds[profile["tempo_threshold"]]
                and mean_tempo > support and paired)
    if profile["kind"] == "extreme_pair_supported":
        return mean_tempo > support and any(
            directional[loc]["offense"] >= thresholds["extreme_z"]
            and directional[opp]["defense"] >= thresholds["extreme_z"]
            for loc, opp in (("away", "home"), ("home", "away")))
    raise ValueError("Unknown profile.")


def grade_matchup(away, home, line, benchmark, reference, *, as_of, tipoff, quote_observed_at, season, policy=None):
    """Grade both directions and return at most one selection, without scores or model edge."""
    policy = load_policy() if policy is None else deepcopy(policy)
    validate_policy(policy)
    cutoff, start = stamp(as_of), stamp(tipoff)
    observed, quote_time, reference_time = stamp(benchmark["observed_at"]), stamp(quote_observed_at), stamp(reference["observed_at"])
    t = policy["thresholds"]
    if not observed <= cutoff < start or (cutoff - observed).total_seconds() > 3600 * t["max_benchmark_age_hours"]:
        raise ValueError("National benchmark is future-dated, stale or post-tipoff.")
    if int(benchmark["season"]) != int(season) or int(benchmark["team_count"]) < 300:
        raise ValueError("Wrong-season or incomplete national population.")
    if not quote_time <= cutoff or (cutoff - quote_time).total_seconds() > 60 * t["max_market_age_minutes"]:
        raise ValueError("Total quote is future-dated or stale.")
    if reference_time > cutoff:
        raise ValueError("Market reference is from after the forecast.")
    total = number(line)
    low, high = number(reference["low_total"]), number(reference["high_total"])
    if not 50 < total < 300 or not 50 < low < high < 300:
        raise ValueError("Invalid total or market reference.")
    z = {}
    for location, rating in (("away", away), ("home", home)):
        z[location] = {}
        for metric in METRICS:
            value = number(rating[metric])
            if not (45 < value < 95 if metric == "tempo" else 60 < value < 150):
                raise ValueError("Team rating is outside plausible bounds.")
            b = benchmark["metrics"][metric]
            mean, sd = number(b["mean"]), number(b["sd"])
            if sd <= 0:
                raise ValueError("Invalid population standard deviation.")
            z[location][metric] = (value - mean) / sd
    tempos = [z[loc]["tempo"] for loc in z]
    sides = {}
    for side, sign in (("Over", 1), ("Under", -1)):
        matched = [p for p in policy["profiles"] if p["enabled"] and p["side"] == side and _profile_matches(p, z, t)]
        matched.sort(key=lambda p: (RANK[p["grade"]], p["priority"], p["id"]), reverse=True)
        conflicts = []
        if max(tempos) >= t["extreme_z"] and min(tempos) <= -t["extreme_z"]:
            conflicts.append({"id": "fast-versus-slow", "reason": "One extreme fast team faces one extreme slow team", "levels": policy["conflicts"]["fast_versus_slow_downgrade_levels"]})
        if sign * statistics.mean(tempos) <= -t["opposing_matchup_tempo_z"]:
            conflicts.append({"id": "opposing-matchup-tempo", "reason": "Average matchup tempo strongly opposes this direction", "levels": policy["conflicts"]["opposing_matchup_tempo_downgrade_levels"]})
        if (side == "Over" and total >= high) or (side == "Under" and total <= low):
            conflicts.append({"id": "market-total-headwind", "reason": "High total for an Over" if side == "Over" else "Low total for an Under", "levels": policy["market_context"]["downgrade_levels"]})
        base = matched[0]["grade"] if matched else "Research"
        rank = max(0, RANK[base] - sum(c["levels"] for c in conflicts))
        grade = next(g for g, value in RANK.items() if value == rank)
        sides[side] = {"grade": grade, "base_grade": base, "profile_id": matched[0]["id"] if matched else None,
                       "profile_label": matched[0]["label"] if matched else "No qualifying factor combination",
                       "matched_profiles": [p["id"] for p in matched], "conflicts": conflicts}
    over, under = RANK[sides["Over"]["grade"]], RANK[sides["Under"]["grade"]]
    selection = "Over" if over > under else "Under" if under > over else None
    grade = sides[selection]["grade"] if selection else "Research"
    reason = sides[selection]["profile_label"] if selection else ("Opposing directions have equal grades" if over else "No unconflicted A/B factor grade")
    return {"policy_version": policy["version"], "policy_sha256": policy_digest(policy),
            "side": selection, "grade": grade, "line": total, "reason": reason,
            "directions": sides, "z": z, "benchmark_observed_at": observed.isoformat(),
            "benchmark_age_hours": (cutoff-observed).total_seconds()/3600, "market_reference": deepcopy(reference),
            "as_of": cutoff.isoformat(), "tipoff": start.isoformat()}


def grade_saved_forecast(forecast, benchmarks, reference_quotes, policy=None):
    policy = load_policy() if policy is None else policy
    locked = forecast["lockedAt"]
    if stamp(forecast["ratings_observed_at"]) > stamp(locked):
        raise ValueError("Team ratings are from after forecast capture.")
    # ESPN season uses the ending year; November 2026 is season 2027.
    season = int(forecast["home_court"]["season"])
    benchmark = select_benchmark(benchmarks, locked, season)
    reference = market_reference(reference_quotes, locked, policy)
    market = forecast["market"]
    result = grade_matchup(forecast["away_rating"], forecast["home_rating"], market["total"], benchmark, reference,
                          as_of=locked, tipoff=forecast["tipoff"], quote_observed_at=market["as_of"], season=season, policy=policy)
    result["benchmark_exact_rating_timestamp"] = stamp(benchmark["observed_at"]) == stamp(forecast["ratings_observed_at"])
    return result
