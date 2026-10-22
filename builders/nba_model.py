"""Pregame NBA rotation model. Pure math; no network, storage or UI imports.

All estimates are research projections until calibrated on historical pregame
data. Player impact is an input in points per 100 possessions, not points/game.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any

MODEL_VERSION = "nba-v0.2-dated-ratings-2026-10-22"
STATUSES = {"AVAILABLE", "OUT", "PROBABLE", "QUESTIONABLE", "DOUBTFUL", "UNKNOWN"}
UNCERTAIN = STATUSES - {"AVAILABLE", "OUT"}


class InputError(ValueError):
    """Input cannot describe a valid, dated pregame projection."""


def number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise InputError(f"{name} must be a finite number.")
    try:
        output = float(value)
    except (TypeError, ValueError):
        raise InputError(f"{name} must be a finite number.") from None
    if not math.isfinite(output):
        raise InputError(f"{name} must be a finite number.")
    return output


def timestamp(value: Any, name: str) -> datetime:
    try:
        output = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise InputError(f"{name} must be an ISO timestamp with a timezone.") from None
    if output.tzinfo is None or output.utcoffset() is None:
        raise InputError(f"{name} must include a timezone.")
    return output.astimezone(timezone.utc)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{name} is required.")
    return value.strip()


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise InputError(f"{name} must be true or false.")
    return value


def _minutes(value: Any, name: str) -> float:
    output = number(value, name)
    if not 0 <= output <= 48:
        raise InputError(f"{name} must be between 0 and 48 regulation minutes.")
    return output


@dataclass(frozen=True)
class PlayerProfile:
    player_id: str
    name: str
    reference_minutes: float
    offensive_impact: float
    defensive_impact: float
    impact_sample_possessions: float | None = None
    impact_basis: str = "supplied"

    def __post_init__(self):
        for key in ("player_id", "name"):
            object.__setattr__(self, key, _text(getattr(self, key), key))
        object.__setattr__(self, "reference_minutes", _minutes(self.reference_minutes, f"{self.name} reference minutes"))
        for key in ("offensive_impact", "defensive_impact"):
            object.__setattr__(self, key, number(getattr(self, key), f"{self.name} {key}"))
        if self.impact_sample_possessions is not None:
            sample = number(self.impact_sample_possessions, f"{self.name} impact sample")
            if sample < 0:
                raise InputError("Impact sample cannot be negative.")
            object.__setattr__(self, "impact_sample_possessions", sample)
        if self.impact_basis not in {"supplied", "rapm", "prior_only"}:
            raise InputError("Unknown player impact basis.")


@dataclass(frozen=True)
class TeamProfile:
    team_id: str
    name: str
    offensive_rating: float
    defensive_rating: float
    pace: float
    ratings_as_of: datetime
    reference_window: str
    ratings_source: str
    impact_source: str
    impacts_as_of: datetime
    players: tuple[PlayerProfile, ...]

    def __post_init__(self):
        for key in ("team_id", "name", "reference_window", "ratings_source", "impact_source"):
            object.__setattr__(self, key, _text(getattr(self, key), key))
        for key in ("offensive_rating", "defensive_rating", "pace"):
            value = number(getattr(self, key), f"{self.name} {key}")
            if value <= 0:
                raise InputError(f"{self.name} {key} must be positive.")
            object.__setattr__(self, key, value)
        for key in ("ratings_as_of", "impacts_as_of"):
            object.__setattr__(self, key, timestamp(getattr(self, key), key))
        object.__setattr__(self, "players", tuple(self.players))
        ids = [p.player_id for p in self.players]
        if len(set(ids)) != len(ids):
            raise InputError(f"{self.name} has duplicate player IDs.")
        if not math.isclose(sum(p.reference_minutes for p in self.players), 240.0, abs_tol=0.01, rel_tol=0):
            raise InputError(f"{self.name} reference rotation must total 240 minutes.")


@dataclass(frozen=True)
class PlayerMinutes:
    player_id: str
    minutes: float
    status: str
    minutes_limit: float = 48.0
    assumption: str = ""
    starter: bool = False

    def __post_init__(self):
        object.__setattr__(self, "player_id", _text(self.player_id, "player_id"))
        _bool(self.starter, "starter")
        object.__setattr__(self, "minutes", _minutes(self.minutes, f"{self.player_id} minutes"))
        object.__setattr__(self, "minutes_limit", _minutes(self.minutes_limit, f"{self.player_id} minutes limit"))
        status = str(self.status).strip().upper()
        assumption = str(self.assumption).strip().upper()
        if status not in STATUSES:
            raise InputError(f"{self.player_id} has an unknown availability status.")
        if assumption not in {"", "IN", "OUT"}:
            raise InputError(f"{self.player_id} assumption must be IN, OUT or blank.")
        if status not in UNCERTAIN and assumption:
            raise InputError(f"{self.player_id}: assumptions apply only to unresolved statuses.")
        if self.minutes > self.minutes_limit:
            raise InputError(f"{self.player_id} exceeds the declared minutes limit.")
        if (status == "OUT" or assumption == "OUT") and self.minutes != 0:
            raise InputError(f"{self.player_id} is out but has projected minutes.")
        if assumption == "IN" and self.minutes == 0:
            raise InputError(f"{self.player_id} is assumed IN but has zero minutes.")
        if self.starter and self.minutes == 0:
            raise InputError(f"{self.player_id} is a starter but has zero projected minutes.")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "assumption", assumption)


@dataclass(frozen=True)
class RotationPlan:
    players: tuple[PlayerMinutes, ...]
    injury_as_of: datetime
    lineup_as_of: datetime
    injury_source: str
    lineup_source: str
    report_complete: bool
    starters_confirmed: bool

    def __post_init__(self):
        object.__setattr__(self, "players", tuple(self.players))
        for key in ("injury_as_of", "lineup_as_of"):
            object.__setattr__(self, key, timestamp(getattr(self, key), key))
        for key in ("injury_source", "lineup_source"):
            object.__setattr__(self, key, _text(getattr(self, key), key))
        for key in ("report_complete", "starters_confirmed"):
            _bool(getattr(self, key), key)
        ids = [p.player_id for p in self.players]
        if len(set(ids)) != len(ids):
            raise InputError("Projected rotation has duplicate player IDs.")
        if not math.isclose(sum(p.minutes for p in self.players), 240.0, abs_tol=0.01, rel_tol=0):
            raise InputError("Projected rotation must total 240 minutes; assign the replacement minutes explicitly.")
        starters = sum(p.starter for p in self.players)
        if (self.starters_confirmed and starters != 5) or starters not in {0, 5}:
            raise InputError("Declare exactly five starters, or leave all starters blank while the lineup is unconfirmed.")


@dataclass(frozen=True)
class GameContext:
    game_id: str
    tipoff: datetime
    as_of: datetime
    league_efficiency: float
    league_pace: float
    home_court_points: float
    max_lineup_age_minutes: float = 60.0

    def __post_init__(self):
        object.__setattr__(self, "game_id", _text(self.game_id, "game_id"))
        for key in ("tipoff", "as_of"):
            object.__setattr__(self, key, timestamp(getattr(self, key), key))
        if self.as_of >= self.tipoff:
            raise InputError("Projection cutoff must be before tipoff.")
        for key in ("league_efficiency", "league_pace", "max_lineup_age_minutes"):
            value = number(getattr(self, key), key)
            if value <= 0:
                raise InputError(f"{key} must be positive.")
            object.__setattr__(self, key, value)
        object.__setattr__(self, "home_court_points", number(self.home_court_points, "home_court_points"))


@dataclass(frozen=True)
class Market:
    home_spread: float | None
    total: float | None
    as_of: datetime
    source: str

    def __post_init__(self):
        for key in ("home_spread", "total"):
            if getattr(self, key) is not None:
                object.__setattr__(self, key, number(getattr(self, key), key))
        if self.total is not None and self.total <= 0:
            raise InputError("Market total must be positive.")
        object.__setattr__(self, "as_of", timestamp(self.as_of, "market as_of"))
        object.__setattr__(self, "source", _text(self.source, "market source"))


@dataclass(frozen=True)
class Scenario:
    name: str
    away: RotationPlan
    home: RotationPlan

    def __post_init__(self):
        object.__setattr__(self, "name", _text(self.name, "scenario name"))


def _dated(value: datetime, context: GameContext, name: str):
    if value > context.as_of:
        raise InputError(f"{name} is later than the projection cutoff.")


def _adjust(team: TeamProfile, plan: RotationPlan, context: GameContext) -> dict:
    _dated(team.ratings_as_of, context, f"{team.name} ratings")
    _dated(team.impacts_as_of, context, f"{team.name} player impacts")
    _dated(plan.injury_as_of, context, f"{team.name} injury report")
    _dated(plan.lineup_as_of, context, f"{team.name} lineup")
    profiles = {p.player_id: p for p in team.players}
    if set(profiles) != {p.player_id for p in plan.players}:
        raise InputError(f"{team.name}: the projected and reference rosters must contain the same player IDs; add replacements with zero reference minutes.")
    flags, changes = [], []
    if not plan.report_complete:
        flags.append(f"{team.name}: injury report incomplete")
    if not plan.starters_confirmed:
        flags.append(f"{team.name}: starting lineup unconfirmed")
    age_limit = timedelta(minutes=context.max_lineup_age_minutes)
    for label, value in (("injury report", plan.injury_as_of), ("lineup", plan.lineup_as_of)):
        if context.as_of - value > age_limit:
            flags.append(f"{team.name}: {label} older than {context.max_lineup_age_minutes:g} minutes")
    offense_delta = defense_delta = 0.0
    for entry in sorted(plan.players, key=lambda p: p.player_id):
        profile = profiles[entry.player_id]
        relevant = profile.reference_minutes > 0 or entry.minutes > 0
        if relevant and (profile.impact_basis == "prior_only" or profile.impact_sample_possessions == 0):
            flags.append(f"{team.name} {profile.name}: no fitted player sample; prior-only impact")
        if entry.status in UNCERTAIN and relevant:
            if not entry.assumption:
                raise InputError(f"{team.name} {profile.name}: choose an explicit IN or OUT scenario for {entry.status}.")
            flags.append(f"{team.name} {profile.name}: {entry.status}, assumed {entry.assumption}")
        minute_change = entry.minutes - profile.reference_minutes
        off = minute_change / 48.0 * profile.offensive_impact
        defense = minute_change / 48.0 * profile.defensive_impact
        offense_delta += off
        defense_delta += defense
        changes.append({"player_id": profile.player_id, "player": profile.name,
                        "reference_minutes": profile.reference_minutes, "minutes": entry.minutes,
                        "minutes_change": minute_change, "offense_change_per_100": off,
                        "defense_improvement_per_100": defense, "status": entry.status,
                        "assumption": entry.assumption, "minutes_limit": entry.minutes_limit,
                        "starter": entry.starter})
    offense = team.offensive_rating + offense_delta
    # A positive defensive impact means fewer points allowed, hence subtraction.
    defense = team.defensive_rating - defense_delta
    if min(offense, defense) <= 0:
        raise InputError(f"{team.name}: adjusted efficiency is nonpositive; check player impact units.")
    return {"team": team.name, "offensive_rating": offense, "defensive_rating": defense,
            "offense_change_per_100": offense_delta,
            "defense_improvement_per_100": defense_delta, "changes": changes, "flags": flags}


def _scores(away_off: float, away_def: float, home_off: float, home_def: float,
            possessions: float, context: GameContext) -> tuple[float, float]:
    away = away_off * home_def / context.league_efficiency * possessions / 100.0
    home = home_off * away_def / context.league_efficiency * possessions / 100.0
    away -= context.home_court_points / 2.0
    home += context.home_court_points / 2.0
    if not all(math.isfinite(x) and x > 0 for x in (away, home)):
        raise InputError("Projected score is invalid; check ratings, pace and court inputs.")
    return away, home


def _serial(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot encode {type(value).__name__}")


def _fingerprint(context, away, home, scenario, market):
    def canonical_team(team):
        item = asdict(team)
        item["players"] = sorted(item["players"], key=lambda p: p["player_id"])
        return item

    scenario_item = asdict(scenario)
    for side in ("away", "home"):
        scenario_item[side]["players"] = sorted(scenario_item[side]["players"], key=lambda p: p["player_id"])
    payload = {"version": MODEL_VERSION, "context": asdict(context),
               "away": canonical_team(away), "home": canonical_team(home),
               "scenario": scenario_item, "market": asdict(market) if market else None}
    encoded = json.dumps(payload, default=_serial, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def project_game(away: TeamProfile, home: TeamProfile, scenario: Scenario,
                 context: GameContext, market: Market | None = None) -> dict:
    if away.team_id == home.team_id:
        raise InputError("Away and home teams must differ.")
    active_away = {p.player_id for p in scenario.away.players if p.minutes > 0}
    active_home = {p.player_id for p in scenario.home.players if p.minutes > 0}
    if active_away & active_home:
        raise InputError("A player cannot appear on both teams in the same game.")
    if market:
        _dated(market.as_of, context, "Market snapshot")
    away_adjusted, home_adjusted = _adjust(away, scenario.away, context), _adjust(home, scenario.home, context)
    possessions = away.pace * home.pace / context.league_pace
    if not math.isfinite(possessions) or possessions <= 0:
        raise InputError("Projected possessions are invalid.")
    away_score, home_score = _scores(away_adjusted["offensive_rating"], away_adjusted["defensive_rating"],
                                     home_adjusted["offensive_rating"], home_adjusted["defensive_rating"],
                                     possessions, context)
    baseline_away, baseline_home = _scores(away.offensive_rating, away.defensive_rating,
                                          home.offensive_rating, home.defensive_rating, possessions, context)
    margin, total = home_score - away_score, home_score + away_score
    flags = away_adjusted["flags"] + home_adjusted["flags"]
    return {"scenario": scenario.name, "model_version": MODEL_VERSION,
            "game_id": context.game_id, "tipoff": context.tipoff.isoformat(),
            "as_of": context.as_of.isoformat(), "away_team": away.name, "home_team": home.name,
            "away_score": away_score, "home_score": home_score,
            "home_margin": margin, "fair_home_spread": -margin, "total": total,
            "possessions": possessions, "baseline_home_margin": baseline_home - baseline_away,
            "baseline_total": baseline_home + baseline_away,
            "lineup_margin_change": margin - (baseline_home - baseline_away),
            "lineup_total_change": total - (baseline_home + baseline_away),
            "home_spread_edge": margin + market.home_spread if market and market.home_spread is not None else None,
            "total_edge": total - market.total if market and market.total is not None else None,
            "market": asdict(market) if market else None, "default_spread_total_odds": -110,
            "away_rotation": away_adjusted, "home_rotation": home_adjusted,
            "lineup_ready": not flags, "flags": flags,
            "research_only": True, "eligible_for_posting": False,
            "snapshot_id": _fingerprint(context, away, home, scenario, market)}


def _direction(values, positive, negative):
    if any(v is None for v in values):
        return "No market line"
    if all(v > 0 for v in values):
        return positive
    if all(v < 0 for v in values):
        return negative
    return "Changes or reaches zero across scenarios"


def project_scenarios(away: TeamProfile, home: TeamProfile, scenarios: tuple[Scenario, ...],
                      context: GameContext, market: Market | None = None) -> dict:
    if not scenarios:
        raise InputError("At least one explicit rotation scenario is required.")
    if len({s.name for s in scenarios}) != len(scenarios):
        raise InputError("Scenario names must be unique.")
    # Scenarios vary the assumed participation/minutes, not the observed report.
    observed = None
    for scenario in scenarios:
        report = tuple((side, plan.injury_as_of, plan.injury_source, plan.report_complete,
                        tuple(sorted((p.player_id, p.status) for p in plan.players)))
                       for side, plan in (("away", scenario.away), ("home", scenario.home)))
        if observed is not None and report != observed:
            raise InputError("Scenarios must use the same observed injury report; vary assumptions and replacement minutes.")
        observed = report
    projections = [project_game(away, home, s, context, market) for s in scenarios]
    uncertain_ids = []
    for side, profile in (("away", away), ("home", home)):
        reference = {p.player_id: p.reference_minutes for p in profile.players}
        for p in getattr(scenarios[0], side).players:
            if p.status in UNCERTAIN and (reference[p.player_id] > 0 or
                    any(next(x for x in getattr(s, side).players if x.player_id == p.player_id).minutes > 0 for s in scenarios)):
                uncertain_ids.append((side, p.player_id))
    uncertain_ids.sort()
    combinations = set()
    for scenario in scenarios:
        plans = {side: {p.player_id: p for p in getattr(scenario, side).players} for side in ("away", "home")}
        states = tuple(plans[side][pid].assumption for side, pid in uncertain_ids)
        if any(state not in {"IN", "OUT"} for state in states):
            raise InputError("Every unresolved rotation player needs an explicit IN or OUT assumption in each scenario.")
        combinations.add(states)
    expected = 2 ** len(uncertain_ids)
    # No invented 50/50 weighting for Questionable. Display the scenario range.
    return {"model_version": MODEL_VERSION, "game_id": context.game_id,
            "as_of": context.as_of.isoformat(), "tipoff": context.tipoff.isoformat(),
            "away_team": away.name, "home_team": home.name, "scenarios": projections,
            "fair_home_spread_range": [min(p["fair_home_spread"] for p in projections), max(p["fair_home_spread"] for p in projections)],
            "total_range": [min(p["total"] for p in projections), max(p["total"] for p in projections)],
            "spread_direction": _direction([p["home_spread_edge"] for p in projections], home.name, away.name),
            "total_direction": _direction([p["total_edge"] for p in projections], "Over", "Under"),
            "scenario_coverage_complete": len(combinations) == expected,
            "scenario_combinations_entered": len(combinations), "scenario_combinations_required": expected,
            "research_only": True, "eligible_for_posting": False,
            "snapshot_id": hashlib.sha256("|".join(sorted(p["snapshot_id"] for p in projections)).encode()).hexdigest()}


def from_package(package: dict) -> tuple[TeamProfile, TeamProfile, tuple[Scenario, ...], GameContext, Market | None]:
    """Decode the explicit import format; missing metrics never become averages."""
    try:
        def team(item):
            return TeamProfile(**{**item, "players": tuple(PlayerProfile(**p) for p in item["players"])})

        def plan(item):
            return RotationPlan(**{**item, "players": tuple(PlayerMinutes(**p) for p in item["players"])})

        away, home = team(package["away"]), team(package["home"])
        scenarios = tuple(Scenario(name=s["name"], away=plan(s["away"]), home=plan(s["home"])) for s in package["scenarios"])
        context = GameContext(**package["context"])
        market = Market(**package["market"]) if package.get("market") else None
        return away, home, scenarios, context, market
    except InputError:
        raise
    except (KeyError, TypeError, AttributeError) as exc:
        raise InputError(f"Incomplete or invalid NBA input package: {exc}") from None


def evaluate_package(package: dict) -> dict:
    return project_scenarios(*from_package(package))


def snapshot_json(package: dict, result: dict) -> str:
    """Archive both inputs and outputs, including report times and scenarios."""
    return json.dumps({"inputs": package, "projection": result}, default=_serial,
                      sort_keys=True, indent=2, allow_nan=False)
