"""Pregame matchup descriptions and paired market edges, without preference grades.

The existing point forecast supplies the edge. SP+ and pace are retained as
separate explanatory features; adding them again to that edge would double count
information until an incremental effect has been established out of sample.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math

SCHEMA_VERSION = "ncaaf-market-relative-2026-11-14-v1"
FIELD = "Market Relative Matchup JSON"
SOURCE_URL = "https://pay.cfbupdate.com/sp-ratings"


def finite(value):
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        raise ValueError("Missing numeric input")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite input")
    return result


def instant(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Capture clocks require a timezone")
    return result.astimezone(timezone.utc)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def market_band(market, line):
    value = abs(line) if market == "Spread" else line
    bounds = (3.5, 7.5, 14.5, 20.0) if market == "Spread" else (40., 50., 60., 70.)
    for bound in bounds:
        if value < bound:
            return f"<{bound:g}"
    return f">={bounds[-1]:g}"


def comparison(factors, *, projected_margin, projected_total, home_line, total_line,
               as_of, kickoff, odds=None, model_version="", policy_version=""):
    """One snapshot, all four alternatives, and no grade or outcome selection.

    Defense is the original SP+ points-allowed rating: higher allows scoring.
    Pairings are continuous national z scores, not 1–5 confidence ratings.
    """
    observed, clock, start = map(instant, (factors["observed_at"], as_of, kickoff))
    if not observed <= clock < start or (clock - observed).total_seconds() > 8 * 86400:
        raise ValueError("Factors and decision must be contemporaneous and pregame")
    if factors.get("source_url") != SOURCE_URL:
        raise ValueError("Published SP+ source identity required")
    population = str(factors.get("population_sha256", ""))
    if len(population) != 64 or any(c not in "0123456789abcdef" for c in population):
        raise ValueError("Captured population identity required")
    margin, total, spread, line = map(finite, (projected_margin, projected_total, home_line, total_line))
    if not 0 < total < 160 or not 0 < line < 160 or abs(spread) >= 100:
        raise ValueError("Invalid point forecast or market")
    z, names = {}, {}
    for side in ("away", "home"):
        names[side] = str(factors[side]["team"]).strip()
        if not names[side]:
            raise ValueError("Team identity required")
        z[side] = {}
        for metric in ("offense", "defense", "tempo"):
            benchmark = factors["national"][metric]
            sd = finite(benchmark["sd"])
            if sd <= 0 or finite(benchmark["teams"]) < (100 if metric == "tempo" else 125):
                raise ValueError("Incomplete or invalid national benchmark")
            z[side][metric] = (finite(factors[side][metric]) - finite(benchmark["mean"])) / sd
    pace = (z["away"]["tempo"] + z["home"]["tempo"]) / 2
    # Same units on both sides. No favorite-only bonus or forced weak-offense penalty.
    attacks = {s: (z[s]["offense"] + z[o]["defense"]) / 2
               for s, o in (("away", "home"), ("home", "away"))}
    prices = odds or {}
    alternatives = []
    for side, opponent in (("home", "away"), ("away", "home")):
        selected_line = spread if side == "home" else -spread
        role = "Favorite" if selected_line < 0 else "Underdog" if selected_line > 0 else "Pick'em"
        alternatives.append({"market": "Spread", "side": side, "team": names[side],
            "role": role, "line": selected_line,
            "edge_points": (margin if side == "home" else -margin) + selected_line,
            "features": {"offense_vs_defense_z": attacks[side],
                "defense_vs_offense_z": -attacks[opponent],
                "mean_tempo_z": pace,
                "role_tempo_z": pace * (1 if selected_line < 0 else -1 if selected_line > 0 else 0)}})
    for side, sign in (("Over", 1), ("Under", -1)):
        alternatives.append({"market": "Total", "side": side, "role": side, "line": line,
            "edge_points": sign * (total - line),
            "features": {"away_offense_vs_home_defense_z": sign * attacks["away"],
                "home_offense_vs_away_defense_z": sign * attacks["home"],
                "mean_tempo_z": sign * pace}})
    for candidate in alternatives:
        key = candidate["side"]
        price = finite(prices.get(key, -110))
        if not price.is_integer() or abs(price) < 100:
            raise ValueError("Invalid American odds")
        candidate.update(odds=int(price), odds_assumed=key not in prices,
            market_band=market_band(candidate["market"], candidate["line"]),
            projection_support=candidate["edge_points"] > 0,
            probability=None, win_rate=None, calibrated=False)
    payload = {"schema_version": SCHEMA_VERSION, "status": "Research",
        "as_of": clock.isoformat(), "kickoff": start.isoformat(),
        "factor_observed_at": observed.isoformat(), "population_sha256": population,
        "model_version": str(model_version), "grading_policy_version": str(policy_version),
        "forecast_basis": "Existing point model; no additional matchup adjustment fitted",
        "projected_home_margin": margin, "projected_total": total,
        "home_line": spread, "total_line": line, "teams": names, "z": z,
        "alternatives": alternatives}
    payload["capture_sha256"] = fingerprint(payload)
    return payload


def from_saved_row(row):
    """Reconstruct only a verified original, paired pregame quote.

    Updated board quotes are intentionally not substituted. Historical rows can
    receive research descriptions, never backdated new grades or publication.
    """
    if row.get(FIELD):
        saved = json.loads(row[FIELD])
        check = {k: v for k, v in saved.items() if k != "capture_sha256"}
        if saved.get("schema_version") != SCHEMA_VERSION or saved.get("capture_sha256") != fingerprint(check):
            raise ValueError("Market-relative capture integrity check failed")
    f, sg, tg = [json.loads(row[name]) for name in
                 ("Matchup Factors JSON", "Matchup Spread JSON", "Matchup Total JSON")]
    clocks = []
    for g in (sg, tg):
        if g.get("record_mode") != "live" or g.get("source_population_sha256") != f.get("population_sha256"):
            raise ValueError("Original live pregame factor capture required")
        if g.get("policy_version") != row.get("Grading Policy Version"):
            raise ValueError("Saved grading policy mismatch")
        for name in ("as_of", "published_at"):
            value = g.get(name, g.get("as_of"))
            clocks.append(instant(value))
            if clocks[-1] >= instant(g["kickoff"]):
                raise ValueError("After-start capture is ineligible")
    if instant(sg["kickoff"]) != instant(tg["kickoff"]):
        raise ValueError("Paired kickoff mismatch")
    if row.get("Season") and int(finite(row["Season"])) != int(finite(f["season"])):
        raise ValueError("Saved factor season mismatch")
    for side in ("away", "home"):
        if str(row[f"{side.title()} Team"]).strip() != f[side]["team"]:
            raise ValueError("Saved team identity mismatch")
    prices = {side: row[field] for side, field in (("home", "Home Spread Odds"),
        ("away", "Away Spread Odds"), ("Over", "Total Over Odds"), ("Under", "Total Under Odds"))
        if row.get(field) is not None and str(row[field]).strip()}
    result = comparison(f, projected_margin=sg["projected_margin"], projected_total=tg["projected_total"],
        home_line=sg["home_line"], total_line=tg["line"], as_of=max(clocks).isoformat(),
        kickoff=sg["kickoff"], odds=prices, model_version=row.get("Model Version", ""),
        policy_version=row.get("Grading Policy Version", ""))
    result.update(game_id=str(row["Game ID"]), date=str(row["Date"])[:10],
                  game=f"{f['away']['team']} @ {f['home']['team']}")
    result["capture_sha256"] = fingerprint({k: v for k, v in result.items() if k != "capture_sha256"})
    return result
