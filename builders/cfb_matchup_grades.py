"""Projection-aligned NCAAF matchup grades from published SP+ and real pace.

The point model is unchanged. This replaces its edge-only A/B ladder for new
decisions and records the exact published population, pace benchmark and policy.
No result, final score or historical win percentage is a grading input.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics

from builders.cfb_sp_ratings import load_population, stamp, team_key

POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "ncaaf_matchup_grades.json"
SLATE_FIELDS = ["Grading Policy Version", "Matchup Factors JSON", "Matchup Spread JSON", "Matchup Total JSON"]


def number(value):
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        raise ValueError("Missing numeric matchup input")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Non-finite matchup input")
    return value


def load_policy():
    policy = json.loads(POLICY_PATH.read_text())
    t = policy["thresholds"]
    if not 0 < t["strong_z"] < t["extreme_z"] <= 3:
        raise ValueError("Invalid matchup support thresholds")
    if t["minimum_tempo_teams"] < 100 or t["spread_max_market_line"] > 20:
        raise ValueError("Invalid population/market guardrails")
    return policy


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def benchmark(values):
    values = [number(v) for v in values]
    sd = statistics.pstdev(values)
    if len(values) < 2 or sd < 1e-6:
        raise ValueError("A non-degenerate population is required")
    return {"mean": statistics.mean(values), "sd": sd, "teams": len(values)}


def collect_factors(builder, game, population):
    from builders import cfb_total_regression as totals
    season, week = int(number(game["Season"])), int(number(game["Week"]))
    prior, current = totals._metric_context(builder, season, week)
    # Map real PBP records to the published FBS population, excluding FCS teams.
    previous = {team_key(k): v for k, v in prior.items()}
    present = {team_key(k): v for k, v in current.items()}
    tempos = {}
    for key in population["teams"]:
        p, c = previous.get(key), present.get(key)
        def real(values):
            try:
                return values and number(values.get("Advanced Plays")) > 0 and all(
                    number(values.get(name)) > 0 for name in ("Possessions Per Game", "Plays Per Game"))
            except (TypeError, ValueError):
                return False
        p, c = (p if real(p) else None), (c if real(c) else None)
        if p is None and c is None:
            continue
        def blend(name):
            if p is not None and c is not None:
                return totals._blend_metric(p, c, name)
            return number((p if p is not None else c)[name])
        possessions = blend("Possessions Per Game")
        plays = blend("Plays Per Game")
        tempo = .58 * possessions + .42 * plays / 5.75
        if 7 <= tempo <= 20 and 30 <= plays <= 110 and 5 <= possessions <= 22:
            tempos[key] = {"tempo": tempo, "possessions": possessions, "plays": plays}
    policy = load_policy()
    if len(tempos) < policy["thresholds"]["minimum_tempo_teams"]:
        raise ValueError(f"Incomplete real FBS pace population: {len(tempos)} teams")
    national = {
        "offense": benchmark(r["offense"] for r in population["teams"].values()),
        "defense": benchmark(r["defense"] for r in population["teams"].values()),
        "tempo": benchmark(r["tempo"] for r in tempos.values()),
    }
    sides = {}
    for side in ("away", "home"):
        name = str(game[f"{side.title()} Team"])
        key = team_key(name)
        if key not in population["teams"] or key not in tempos:
            raise ValueError(f"Published SP+ or real pace unavailable for {name}")
        sides[side] = {**deepcopy(population["teams"][key]), **tempos[key], "team": name}
    return {**sides, "national": national, "source": population["source"],
        "source_url": population["source_url"], "season": season,
        "observed_at": population["observed_at"], "population_sha256": population["population_sha256"],
        "source_update_label": population.get("source_update_label", ""),
        "pace_source": "SportsDataverse play-by-play; current season through the prior week, blended with the previous season",
        "pace_through_week": week - 1,
        "pace_population_sha256": digest(tempos)}


def grade_matchup(factors, *, projected_margin, projected_total, home_line, total_line,
                  as_of, kickoff, policy=None):
    policy = deepcopy(policy or load_policy())
    t = policy["thresholds"]
    clock, start, observed = stamp(as_of), stamp(kickoff), stamp(factors["observed_at"])
    if not observed <= clock < start:
        raise ValueError("Matchup ratings must be observed and graded before kickoff")
    if (clock - observed).total_seconds() > t["maximum_population_age_days"] * 86400:
        raise ValueError("Published SP+ population is stale")
    if factors["national"]["tempo"]["teams"] < t["minimum_tempo_teams"]:
        raise ValueError("Incomplete tempo population")
    if any(factors["national"][m]["teams"] < 125 for m in ("offense", "defense")):
        raise ValueError("Incomplete published SP+ population")
    margin, total = number(projected_margin), number(projected_total)
    spread, line = number(home_line), number(total_line)
    if not (0 < total < 160 and 0 < line < 160 and abs(spread) < 100):
        raise ValueError("Invalid projection or paired market line")
    z = {}
    for side in ("away", "home"):
        z[side] = {}
        for metric in ("offense", "defense", "tempo"):
            b = factors["national"][metric]
            if number(b["sd"]) <= 0:
                raise ValueError("Invalid national benchmark")
            z[side][metric] = (number(factors[side][metric]) - number(b["mean"])) / number(b["sd"])
    mean_tempo = (z["away"]["tempo"] + z["home"]["tempo"]) / 2
    common = {"policy_version": policy["version"], "policy_sha256": digest(policy),
        "status": policy["status"], "as_of": clock.isoformat(), "kickoff": start.isoformat(),
        "published_at": clock.isoformat(), "record_mode": "live", "z": z,
        "mean_tempo_z": mean_tempo, "source_population_sha256": factors["population_sha256"]}
    output = {}
    gap = total - line
    direction = 1 if gap > 0 else -1 if gap < 0 else 0
    # In SP+, high offense and high defensive points allowed favor scoring.
    pairs = [(z["away"]["offense"] + z["home"]["defense"]) / 2,
             (z["home"]["offense"] + z["away"]["defense"]) / 2]
    triple = any(all(direction * z[side][m] >= t["strong_z"] for m in ("offense", "defense", "tempo"))
                 for side in ("away", "home"))
    tempo_support = direction * mean_tempo
    paired_support = [direction * p for p in pairs]
    total_grade, reason = "Research", "The projected direction lacks offense–defense–tempo agreement"
    if direction and tempo_support > 0:
        if triple:
            total_grade, reason = "A", "One team supplies strong offense, defensive scoring allowance and tempo support"
        elif min(paired_support) > 0:
            total_grade, reason = "B", "Both offenses facing the opposing defenses and matchup tempo support the projection"
        elif max(paired_support) >= t["strong_z"] and min(paired_support) >= -t["strong_z"] and tempo_support >= t["extreme_z"]:
            total_grade, reason = "B", "Strong scoring pairing and extreme supporting matchup tempo"
    if direction == 0:
        reason = "Projection equals the market total"
    output["total"] = {**deepcopy(common), "grade": total_grade,
        "side": ("Over" if direction > 0 else "Under") if direction else None,
        "line": line, "edge": abs(gap), "projected_total": total,
        "reason": reason, "pairing_support_z": paired_support}

    cover_gap = margin + spread
    side = "home" if cover_gap > 0 else "away" if cover_gap < 0 else None
    spread_grade, reason = "Research", "Projection equals the market spread"
    offense_advantage = defense_advantage = role_tempo = 0.0
    selected_line = None
    role = "Pick'em"
    if side:
        opponent = "away" if side == "home" else "home"
        offense_advantage = z[side]["offense"] - z[opponent]["offense"]
        defense_advantage = z[opponent]["defense"] - z[side]["defense"]
        selected_line = spread if side == "home" else -spread
        role = "Favorite" if selected_line < 0 else "Underdog" if selected_line > 0 else "Pick'em"
        role_tempo = mean_tempo * (1 if selected_line < 0 else -1 if selected_line > 0 else 0)
        reason = "The projected cover side lacks a relative offense or defense advantage"
        if abs(spread) >= t["spread_max_market_line"]:
            reason = "20+ point spreads remain projection-only"
        elif role_tempo <= -t["extreme_z"]:
            reason = "Extreme matchup tempo conflicts with the selected favorite/underdog role"
        elif min(offense_advantage, defense_advantage) >= t["strong_z"]:
            spread_grade, reason = "A", "Strong relative offense and defense support the projected cover side"
        elif max(offense_advantage, defense_advantage) > 0:
            spread_grade, reason = "B", "A relative offense or defense advantage supports the projected cover side"
    output["spread"] = {**deepcopy(common), "grade": spread_grade, "side": side,
        "selection_team": factors[side]["team"] if side else None, "line": selected_line,
        "home_line": spread, "edge": abs(cover_gap), "projected_margin": margin, "role": role,
        "offense_advantage_z": offense_advantage, "defense_advantage_z": defense_advantage,
        "role_tempo_support_z": role_tempo, "reason": reason}
    return output


def apply_price_and_direction(grade, market, expected_pick, available):
    grade = deepcopy(grade)
    if not available:
        grade.update(grade="Research", reason="Confirmed market unavailable")
    elif market.get("pick") != expected_pick:
        grade.update(grade="Research", reason="Price-selected side opposes the point projection")
    elif number(market.get("price_edge")) <= 0 or number(market.get("ev")) <= 0:
        grade.update(grade="Research", reason="Saved side price lacks positive model edge or expected value")
    return grade


def install_matchup_grading(builder):
    if getattr(builder, "_SP_MATCHUP_GRADES_INSTALLED", False):
        return
    evaluate, slate_row, tracker_rows = builder.evaluate_game, builder.slate_row, builder.tracker_rows
    builder.SLATE_COLUMNS = list(dict.fromkeys([*builder.SLATE_COLUMNS, *SLATE_FIELDS]))
    builder.TRACKER_COLUMNS = list(dict.fromkeys([*builder.TRACKER_COLUMNS, "Grading Policy Version"]))

    def wrapped_evaluate(*args, **kwargs):
        result = evaluate(*args, **kwargs)
        game, p, sim = result["game"], result["projection"], result["simulation"]
        policy = load_policy()
        try:
            population = load_population(int(number(game["Season"])))
            factors = collect_factors(builder, game, population)
            as_of = datetime.now(timezone.utc).isoformat()
            grades = grade_matchup(factors, projected_margin=number(sim["home_mean"]) - number(sim["away_mean"]),
                projected_total=number(sim["home_mean"]) + number(sim["away_mean"]),
                home_line=game["Home Spread"], total_line=game["Total"], as_of=as_of,
                kickoff=game["Game Time"], policy=policy)
            for market_name, result_name in (("spread", "spread"), ("total", "total_market")):
                grade = grades[market_name]
                if market_name == "spread":
                    expected = f"{grade['selection_team']} {grade['line']:+g}" if grade["side"] else ""
                else:
                    expected = f"{grade['side']} {grade['line']:g}" if grade["side"] else ""
                grades[market_name] = apply_price_and_direction(grade, result[result_name], expected,
                    result.get("market_availability", {}).get(market_name, False))
            p["matchup_factors"] = factors
            result["matchup_factors"] = factors
        except Exception as exc:
            grades = {kind: {"grade": "Research", "side": None, "reason": f"Matchup inputs unavailable: {exc}",
                "policy_version": policy["version"], "policy_sha256": digest(policy), "status": "provisional"}
                for kind in ("spread", "total")}
            result["matchup_factors"] = {"unavailable": str(exc), "source": "Published SP+"}
        for kind, field in (("spread", "spread"), ("total", "total_market")):
            grade = grades[kind]
            result[field]["grade"] = f"{grade['grade']} {kind.title()}" if grade["grade"] in ("A", "B") else "No Play"
            result[field]["support"] = [grade["reason"], "Provisional SP+ matchup support; projection agreement and saved-price checks required"]
            p[f"matchup_{kind}"] = grade
        result["matchup_grades"] = grades
        result["grading_policy_version"] = policy["version"]
        return result

    def wrapped_slate(result):
        frame = slate_row(result)
        if not frame.empty:
            values = {"Grading Policy Version": result.get("grading_policy_version", ""),
                "Matchup Factors JSON": json.dumps(result.get("matchup_factors", {}), allow_nan=False),
                "Matchup Spread JSON": json.dumps(result.get("matchup_grades", {}).get("spread", {}), allow_nan=False),
                "Matchup Total JSON": json.dumps(result.get("matchup_grades", {}).get("total", {}), allow_nan=False)}
            for key, value in values.items():
                frame.at[frame.index[0], key] = value
        return frame.reindex(columns=builder.SLATE_COLUMNS)

    def wrapped_tracker(result, *args, **kwargs):
        frame = tracker_rows(result, *args, **kwargs)
        if not frame.empty:
            frame["Grading Policy Version"] = result.get("grading_policy_version", "")
        return frame.reindex(columns=builder.TRACKER_COLUMNS)

    builder.evaluate_game, builder.slate_row, builder.tracker_rows = wrapped_evaluate, wrapped_slate, wrapped_tracker
    builder._SP_MATCHUP_GRADES_INSTALLED = True
