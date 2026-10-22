"""Fit dated NBA team and player ratings, then build manual matchup inputs.

Fitting imports SciPy lazily and is an offline research step. Consuming a saved
JSON ratings bundle requires only the normal builder dependencies.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from builders.nba_history import History, forecast_minutes, prior_history
from builders.nba_model import InputError, TeamProfile, PlayerProfile, timestamp

RATINGS_SCHEMA = "nba-ratings-v1"


def _fit_effects(observations, ridge: float, *, teams=False, prior=None):
    if not math.isfinite(ridge) or ridge <= 0:
        raise InputError("Ridge penalty must be finite and positive.")
    try:
        from scipy.sparse import csr_matrix
        from scipy.linalg import solve
    except ImportError:
        raise InputError("Offline rating fitting needs the optional NBA research dependencies.") from None
    if teams:
        rows = observations.groupby(["game_id", "offense_team_id", "defense_team_id", "home_offense"], as_index=False).agg(
            points=("points", "sum"), possessions=("possessions", "sum"))
        offense = [(x,) for x in rows.offense_team_id]
        defense = [(x,) for x in rows.defense_team_id]
    else:
        rows = observations
        offense, defense = list(rows.offense_players), list(rows.defense_players)
    entities = sorted({p for unit in offense + defense for p in unit})
    index = {entity: i for i, entity in enumerate(entities)}
    n = len(entities)
    off_count, def_count = len(offense[0]), len(defense[0])
    indices = np.array([[*[index[p] for p in off], *[n + index[p] for p in df], 2*n, 2*n+1]
                        for off, df in zip(offense, defense)], dtype=np.int32)
    values = np.tile([*[1.0]*off_count, *[-1.0]*def_count, 1.0, 0.0], (len(rows), 1))
    values[:, -1] = rows.home_offense.to_numpy(float)
    design = csr_matrix((values.ravel(), (np.repeat(np.arange(len(rows)), indices.shape[1]), indices.ravel())),
                        shape=(len(rows), 2*n+2))
    weights = rows.possessions.to_numpy(float)
    if (weights <= 0).any() or not np.isfinite(weights).all():
        raise InputError("Fitting needs positive finite possession weights.")
    gram = (design.T @ design.multiply(weights[:, None])).toarray()
    rhs = np.asarray(design.T @ (rows.points.to_numpy(float) * 100)).ravel()
    penalty = np.r_[np.full(2*n, ridge), 0.0, 0.0]
    gram.flat[::gram.shape[0]+1] += penalty
    if prior:
        for entity, i in index.items():
            old = prior.get(entity, {})
            for offset, key in ((0, "offensive_impact"), (n, "defensive_impact")):
                value = float(old.get(key, 0.0))
                if not math.isfinite(value):
                    raise InputError("Prior player impacts must be finite.")
                rhs[offset+i] += ridge * value
    try:
        beta = solve(gram, rhs, assume_a="pos", check_finite=False)
    except np.linalg.LinAlgError:
        raise InputError("The rating fit is singular; check game/venue coverage.") from None
    if not np.isfinite(beta).all():
        raise InputError("Rating fit returned nonfinite coefficients.")
    samples = np.bincount(indices[:, :off_count].ravel(), weights=np.repeat(weights, off_count), minlength=n)
    samples += np.bincount((indices[:, off_count:off_count+def_count]-n).ravel(),
                           weights=np.repeat(weights, def_count), minlength=n)
    return {"entities": entities, "offense": {p: float(beta[i]) for p, i in index.items()},
            "defense": {p: float(beta[n+i]) for p, i in index.items()},
            "sample_possessions": {p: float(samples[i]) for p, i in index.items()},
            "league_efficiency": float(beta[2*n]), "home_offense_per_100": float(beta[2*n+1]),
            "ridge_penalty": ridge}


def fit_rating_bundle(history: History, as_of, *, player_ridge=1500.0,
                      team_ridge=100.0, prior_bundle=None, minutes_window=5) -> dict:
    cutoff = timestamp(as_of, "rating cutoff")
    training = prior_history(history, cutoff)
    team_obs = training.team_observations if training.team_observations is not None else training.observations
    games = training.team_games if training.team_games is not None else training.games
    boxes = training.team_box if training.team_box is not None else training.box
    if training.games.game_id.nunique() < 2:
        raise InputError("At least two verified games are required to fit ratings.")
    prior = None
    if prior_bundle is not None:
        validate_bundle(prior_bundle)
        if timestamp(prior_bundle["as_of"], "prior cutoff") > cutoff:
            raise InputError("A prior bundle cannot be newer than the training cutoff.")
        if str(prior_bundle["data_through"]) >= str(games.game_date.min()):
            raise InputError("A prior bundle must use an earlier, nonoverlapping historical sample.")
        prior = {p["player_id"]: p for p in prior_bundle["player_impacts"]}
    player_fit = _fit_effects(training.observations, float(player_ridge), prior=prior)
    team_fit = _fit_effects(team_obs, float(team_ridge), teams=True)
    obs = training.observations
    names = boxes.drop_duplicates("person_id").set_index("person_id").player_name.to_dict()
    latest = max(team_obs.available_at).isoformat()
    impact_date = max(obs.available_at).isoformat()
    first_day, last_day = str(games.game_date.min()), str(games.game_date.max())
    league_pace = float(games.possessions.sum() / games.game_minutes.sum() * 48)
    global_impacts = {}
    for player in sorted(set(boxes.person_id)):
        previous = (prior or {}).get(player, {})
        has_sample = player in player_fit["offense"]
        global_impacts[player] = {"player_id": player, "name": names.get(player, f"NBA player {player}"),
            "offensive_impact": player_fit["offense"].get(player, float(previous.get("offensive_impact", 0.0))),
            "defensive_impact": player_fit["defense"].get(player, float(previous.get("defensive_impact", 0.0))),
            "impact_sample_possessions": player_fit["sample_possessions"].get(player, 0.0),
            "impact_basis": "rapm" if has_sample else "prior_only"}
    teams = []
    for team_id in team_fit["entities"]:
        team_games = games.loc[(games.home_team_id == team_id) | (games.away_team_id == team_id)]
        home = team_games.loc[team_games.home_team_id == team_id]
        name = home.home_team_name.iloc[0] if not home.empty else team_games.away_team_name.iloc[0]
        team_boxes = boxes.loc[(boxes.team_id == team_id) & boxes.game_id.isin(team_games.game_id)].copy()
        weights = team_games.set_index("game_id").possessions
        game_minutes = team_boxes.groupby("game_id").minutes_value.sum()
        team_boxes["weighted_minutes"] = team_boxes.minutes_value * 240 / team_boxes.game_id.map(game_minutes) * team_boxes.game_id.map(weights)
        reference = team_boxes.groupby("person_id").weighted_minutes.sum() / weights.sum()
        players = [{**global_impacts[player], "reference_minutes": float(minutes)}
                   for player, minutes in reference.items()]
        profile = {"team_id": team_id, "name": name,
                   "offensive_rating": team_fit["league_efficiency"] + team_fit["offense"][team_id],
                   "defensive_rating": team_fit["league_efficiency"] - team_fit["defense"][team_id],
                   "pace": float(team_games.possessions.sum() / team_games.game_minutes.sum() * 48),
                   "ratings_as_of": latest, "impacts_as_of": impact_date,
                   "reference_window": f"{len(team_games)} checked box games, {team_games.game_date.min()} to {team_games.game_date.max()}; possession-weighted, OT-normalized 240 minutes",
                   "ratings_source": f"EZPZ opponent-adjusted team ridge {team_ridge:g}; {history.source}",
                   "impact_source": f"EZPZ offensive/defensive RAPM ridge {player_ridge:g}; {len(training.games)} event-verified games, box-estimated possession units",
                   "players": players}
        profile["rotation_seed"] = {"as_of": cutoff.isoformat(), "window": minutes_window,
            "source": f"Mean of prior {minutes_window} verified games; latest known box roster; normalized and capped at 48",
            "minutes": forecast_minutes(training, team_id, cutoff, minutes_window)}
        teams.append(profile)
    all_impacts = list(global_impacts.values())
    bundle = {"schema": RATINGS_SCHEMA, "as_of": cutoff.isoformat(), "data_through": last_day,
              "first_game_date": first_day, "data_available_at": latest,
              "league_efficiency": team_fit["league_efficiency"], "league_pace": league_pace,
              "home_court_points": 2.0 * team_fit["home_offense_per_100"] * league_pace / 100.0,
              "training_games": len(games), "training_observation_groups": len(obs),
              "player_training_games": len(training.games),
              "player_ridge": player_ridge, "team_ridge": team_ridge,
              "player_impacts": all_impacts, "teams": teams, "source": history.source,
              "availability_policy": history.audit["availability_policy"],
              "quality_audit": {"team_training_games": len(games), "player_training_games": len(training.games),
                  "player_plus_minus_tolerance": history.audit.get("plus_minus_tolerance"),
                  "minute_tolerance_seconds": history.audit.get("minute_tolerance_seconds"),
                  "possession_units": history.audit.get("possession_units", "Source-counted possessions")},
              "minutes_window": minutes_window,
              "research_only": True, "eligible_for_posting": False}
    validate_bundle(bundle)
    return bundle


def validate_bundle(bundle: dict):
    try:
        if bundle["schema"] != RATINGS_SCHEMA:
            raise InputError("Unsupported NBA ratings bundle format.")
        cutoff = timestamp(bundle["as_of"], "bundle cutoff")
        if timestamp(bundle["data_available_at"], "bundle data availability") > cutoff:
            raise InputError("Bundle data was not available at its cutoff.")
        if bundle.get("research_only") is not True or bundle.get("eligible_for_posting") is not False:
            raise InputError("NBA ratings bundles must remain research-only.")
        first = datetime.fromisoformat(bundle["first_game_date"]).date()
        last = datetime.fromisoformat(bundle["data_through"]).date()
        if first > last or last > cutoff.date():
            raise InputError("Bundle game dates do not agree with its cutoff.")
        impact_ids = []
        for impact in bundle["player_impacts"]:
            player = PlayerProfile(**{**impact, "reference_minutes": 0.0})
            impact_ids.append(player.player_id)
        if len(set(impact_ids)) != len(impact_ids):
            raise InputError("Player impact IDs are duplicated in the ratings bundle.")
        global_impacts = {p["player_id"]: p for p in bundle["player_impacts"]}
        ids = []
        for item in bundle["teams"]:
            profile = {k: v for k, v in item.items() if k != "rotation_seed"}
            team = TeamProfile(**{**profile, "players": tuple(PlayerProfile(**p) for p in item["players"])})
            if team.ratings_as_of > cutoff or team.impacts_as_of > cutoff:
                raise InputError("Bundle contains ratings newer than its cutoff.")
            ids.append(team.team_id)
            for player in team.players:
                global_player = global_impacts.get(player.player_id)
                if global_player is None or any(not math.isclose(getattr(player, key), float(global_player[key]), abs_tol=1e-10)
                    for key in ("offensive_impact", "defensive_impact")):
                    raise InputError("Team player impacts disagree with the global ratings sample.")
            seed = item.get("rotation_seed")
            if seed is not None:
                if timestamp(seed["as_of"], "rotation seed cutoff") > cutoff:
                    raise InputError("Rotation seed is newer than the ratings cutoff.")
                minutes = seed["minutes"]
                player_ids = {p.player_id for p in team.players}
                if not set(minutes).issubset(player_ids) or any(not math.isfinite(float(v)) or not 0 <= float(v) <= 48 for v in minutes.values()):
                    raise InputError("Historical rotation seed has invalid players or minutes.")
                if not math.isclose(sum(float(v) for v in minutes.values()), 240, abs_tol=0.01):
                    raise InputError("Historical rotation seed must total 240 minutes.")
        if len(ids) < 2 or len(set(ids)) != len(ids):
            raise InputError("Ratings bundle needs at least two distinct teams.")
        for key in ("league_efficiency", "league_pace"):
            if not math.isfinite(float(bundle[key])) or float(bundle[key]) <= 0:
                raise InputError(f"Bundle {key} must be positive.")
        if not math.isfinite(float(bundle["home_court_points"])):
            raise InputError("Bundle home-court adjustment must be finite.")
        evidence = bundle.get("research_evaluation")
        if evidence is not None:
            if not isinstance(evidence, dict) or not isinstance(evidence.get("holdout_games"), int) or evidence["holdout_games"] < 1:
                raise InputError("Research evaluation must identify its held-out sample.")
            for key in ("baseline_margin_rmse", "lineup_margin_rmse"):
                if not math.isfinite(float(evidence[key])) or float(evidence[key]) < 0:
                    raise InputError("Research evaluation errors must be finite and nonnegative.")
            if not isinstance(evidence["lineup_beats_team_margin_rmse"], bool):
                raise InputError("Research evaluation comparison must be a boolean.")
    except InputError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise InputError(f"Invalid NBA ratings bundle: {exc}") from None


def package_from_bundle(bundle: dict, away_id: str, home_id: str, *, game_id: str,
                        tipoff, as_of, history: History | None = None, window: int = 5) -> dict:
    validate_bundle(bundle)
    cutoff = timestamp(as_of, "projection cutoff")
    if timestamp(bundle["as_of"], "ratings cutoff") > cutoff:
        raise InputError("Ratings were fitted after the projection cutoff.")
    teams = {t["team_id"]: t for t in bundle["teams"]}
    if str(away_id) == str(home_id) or str(away_id) not in teams or str(home_id) not in teams:
        raise InputError("Choose two distinct teams present in the ratings bundle.")
    impacts = {p["player_id"]: p for p in bundle["player_impacts"]}
    profiles, plans = {}, {}
    for side, team_id in (("away", str(away_id)), ("home", str(home_id))):
        team = deepcopy(teams[team_id])
        seed = team.pop("rotation_seed", None)
        minutes = (forecast_minutes(history, team_id, cutoff, window) if history is not None else
                   seed["minutes"] if seed is not None else {p["player_id"]: p["reference_minutes"] for p in team["players"]})
        existing = {p["player_id"] for p in team["players"]}
        for player in set(minutes) - existing:
            effect = impacts.get(player, {"player_id": player, "name": f"NBA player {player}",
                "offensive_impact": 0.0, "defensive_impact": 0.0,
                "impact_sample_possessions": 0.0, "impact_basis": "prior_only"})
            team["players"].append({**effect, "reference_minutes": 0.0})
        profiles[side] = team
        plans[side] = {"players": [{"player_id": p["player_id"], "minutes": minutes.get(p["player_id"], 0.0),
                           "status": "UNKNOWN", "assumption": "IN" if minutes.get(p["player_id"], 0.0) > 0 else "OUT",
                           "minutes_limit": 48.0, "starter": False} for p in team["players"]],
                       "injury_as_of": team["ratings_as_of"], "lineup_as_of": team["ratings_as_of"],
                       "injury_source": "NO CURRENT REPORT: historical rotation seed only",
                       "lineup_source": f"Prior {window} verified games; latest known box roster" if history is not None else
                           seed["source"] if seed is not None else "Historical reference rotation; edit for current lineup",
                       "report_complete": False, "starters_confirmed": False}
    return {"is_demo": False, "context": {"game_id": game_id,
                "tipoff": timestamp(tipoff, "tipoff").isoformat(), "as_of": cutoff.isoformat(),
                "league_efficiency": bundle["league_efficiency"], "league_pace": bundle["league_pace"],
                "home_court_points": bundle["home_court_points"], "max_lineup_age_minutes": 60.0},
            "away": profiles["away"], "home": profiles["home"], "market": None,
            "scenarios": [{"name": "Historical rotation seed — verify current news", **plans}],
            "rating_provenance": {"schema": bundle["schema"], "as_of": bundle["as_of"],
                "data_through": bundle["data_through"], "source": bundle["source"],
                "training_games": bundle["training_games"], "player_ridge": bundle["player_ridge"],
                "team_ridge": bundle["team_ridge"], "research_evaluation": deepcopy(bundle.get("research_evaluation"))}}


def add_roster_player(package: dict, side: str, player_id: str, name: str, *, bundle=None) -> dict:
    """Add a current roster member with zero baseline/current minutes.

    Reuse a dated fitted value for a trade; a new player receives an explicit
    zero prior. Replacement minutes are still assigned manually.
    """
    from dataclasses import asdict
    # This is an editor operation: an injury import may have left replacement
    # minutes unassigned. The projection engine validates the complete rotation
    # on Compare, while this operation validates the new profile and identity.
    if not isinstance(package, dict) or not all(k in package for k in ("away", "home", "context", "scenarios")):
        raise InputError("A matchup is required before adding a roster player.")
    if side not in {"away", "home"}:
        raise InputError("Choose the away or home roster.")
    player_id = str(player_id).strip()
    if any(p["player_id"] == player_id for p in package[side]["players"]):
        raise InputError("That player is already in this team's reference roster.")
    known = {}
    if bundle is not None:
        validate_bundle(bundle)
        if timestamp(bundle["as_of"], "ratings cutoff") > timestamp(package["context"]["as_of"], "projection cutoff"):
            raise InputError("Player ratings are newer than the projection cutoff.")
        known = {p["player_id"]: p for p in bundle["player_impacts"]}
    if player_id not in known:
        known.update({p["player_id"]: {k: v for k, v in p.items() if k != "reference_minutes"}
                      for other in ("away", "home") for p in package[other]["players"]})
    effect = known.get(player_id, {"player_id": player_id, "name": name,
        "offensive_impact": 0.0, "defensive_impact": 0.0,
        "impact_sample_possessions": 0.0, "impact_basis": "prior_only"})
    player = PlayerProfile(**{**effect, "reference_minutes": 0.0})
    updated = deepcopy(package)
    updated[side]["players"].append(asdict(player))
    if player_id in known:
        # All copied profiles must share the source and cutoff of the input
        # from which the added player's estimate was obtained.
        if bundle is not None and player_id in {p["player_id"] for p in bundle["player_impacts"]}:
            updated[side]["impact_source"] += f"; added player {player_id} from {bundle['source']}"
            newest = max(timestamp(updated[side]["impacts_as_of"], "player impact cutoff"),
                         timestamp(bundle["data_available_at"], "added impact cutoff"))
        else:
            other = next(t for t in ("away", "home") if any(p["player_id"] == player_id for p in package[t]["players"]))
            updated[side]["impact_source"] += f"; added player {player_id} from {package[other]['impact_source']}"
            newest = max(timestamp(updated[side]["impacts_as_of"], "player impact cutoff"),
                         timestamp(package[other]["impacts_as_of"], "added impact cutoff"))
        updated[side]["impacts_as_of"] = newest.isoformat()
    for scenario in updated["scenarios"]:
        plan = scenario[side]
        plan["players"].append({"player_id": player_id, "minutes": 0.0, "minutes_limit": 48.0,
            "status": "UNKNOWN", "assumption": "OUT", "starter": False})
        plan["report_complete"] = False
        plan["starters_confirmed"] = False
    return updated


def evaluate_games(history: History, bundle: dict, game_ids, *, window=5) -> dict:
    """Pregame date-based check. Uses prior minutes, never target-game minutes.

The schedule gives dates, not historical tipoff timestamps, so each forecast
uses midnight ET at the start of that date and the stated conservative lag.
"""
    from builders.nba_model import evaluate_package

    rows, skipped = [], []
    game_history = history.team_games if history.team_games is not None else history.games
    games = game_history.loc[game_history.game_id.isin(set(game_ids))].sort_values(["game_date", "game_id"])
    for game in games.itertuples(index=False):
        # Midnight ET is before that day's slate; no same-day outcomes enter.
        cutoff = datetime.combine(game.game_date, datetime.min.time(), tzinfo=ZoneInfo("America/New_York"))
        try:
            package = package_from_bundle(bundle, game.away_team_id, game.home_team_id,
                game_id=game.game_id, as_of=cutoff, tipoff=cutoff.replace(hour=12), history=history, window=window)
            projection = evaluate_package(package)["scenarios"][0]
        except InputError as exc:
            skipped.append({"game_id": game.game_id, "reason": str(exc)})
            continue
        actual_margin, actual_total = game.home_pts - game.away_pts, game.home_pts + game.away_pts
        rows.append({"game_id": game.game_id, "game_date": str(game.game_date),
                     "actual_margin": actual_margin, "actual_total": actual_total,
                     "baseline_margin": projection["baseline_home_margin"], "baseline_total": projection["baseline_total"],
                     "lineup_margin": projection["home_margin"], "lineup_total": projection["total"]})
    if not rows:
        raise InputError("No held-out games could be evaluated.")
    frame = pd.DataFrame(rows)
    metrics = {}
    for label in ("baseline", "lineup"):
        metrics[label] = {}
        for target in ("margin", "total"):
            error = frame[f"{label}_{target}"] - frame[f"actual_{target}"]
            metrics[label][f"{target}_mae"] = float(error.abs().mean())
            metrics[label][f"{target}_rmse"] = float(np.sqrt((error**2).mean()))
    return {"games": len(rows), "skipped": skipped, "minutes_window": window,
            "metrics": metrics, "predictions": rows,
            "forecast_policy": "Midnight ET date cutoff; only games available by next-day-noon policy; no current injury archive",
            "betting_records_available": False}
