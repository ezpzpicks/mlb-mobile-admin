"""An explicit SP+ offense/defense/pace scoring baseline, priced afterwards.

This is EZPZ's provisional construction from published ratings, not Bill
Connelly's proprietary game projection and not a fitted conversion of 1–5
preference ratings into points. No market line or actual result is an input.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json

from builders.cfb_market_relative_matchups import SOURCE_URL, finite, fingerprint, instant

MODEL_VERSION = "cfb-v3.0-sp-matchup-projection-2026-11-14"
PROJECTION_POLICY = "ncaaf-sp-projection-2026-11-14-v4"
FIELD = "Matchup Projection JSON"
SLATE_FIELDS = [FIELD, "Projection Engine", "Projection Status",
                "Legacy Projected Away", "Legacy Projected Home",
                "Legacy Projected Margin", "Legacy Projected Total"]


def project_from_factors(factors, *, as_of, kickoff, adjustments=None):
    observed, clock, start = map(instant, (factors["observed_at"], as_of, kickoff))
    if not observed <= clock < start or (clock-observed).total_seconds() > 8*86400:
        raise ValueError("Projection inputs must be contemporaneous and pregame")
    population = str(factors.get("population_sha256", ""))
    if factors.get("source_url") != SOURCE_URL or len(population) != 64 or any(c not in "0123456789abcdef" for c in population):
        raise ValueError("Published SP+ source and captured population required")
    national = factors["national"]
    for metric in ("offense", "defense", "tempo"):
        b = national[metric]
        if finite(b["sd"]) <= 0 or finite(b["teams"]) < (100 if metric == "tempo" else 125):
            raise ValueError("Incomplete or degenerate national population")
    mean_off, mean_def, mean_tempo = [finite(national[k]["mean"]) for k in ("offense", "defense", "tempo")]
    if not 0 < mean_off < 90 or not 0 < mean_def < 90 or not 7 <= mean_tempo <= 20:
        raise ValueError("Invalid population scale")
    for side in ("away", "home"):
        if not str(factors[side].get("team", "")).strip():
            raise ValueError("Team identity missing")
        if not 7 <= finite(factors[side]["tempo"]) <= 20:
            raise ValueError("Invalid team tempo")
        for metric in ("offense", "defense"):
            if not 0 <= finite(factors[side][metric]) <= 90:
                raise ValueError("Invalid adjusted scoring rating")
    center = (mean_off+mean_def)/2
    tempo = (finite(factors["away"]["tempo"])+finite(factors["home"]["tempo"]))/2
    scale = tempo/mean_tempo
    pairings = {}
    for side, opponent in (("away", "home"), ("home", "away")):
        offense = finite(factors[side]["offense"])-mean_off
        allowed = finite(factors[opponent]["defense"])-mean_def
        # Common point baseline makes an average offense/defense pair average.
        neutral = center+offense+allowed
        pairings[side] = {"baseline_points": center, "offense_points": offense,
            "opponent_defense_points": allowed, "neutral_pace_points": neutral,
            "pace_adjusted_points": max(0., neutral)*scale}
    supplied = adjustments or {}
    adj = {k:finite(supplied.get(k,0.)) for k in
           ("home_field", "personnel_margin", "personnel_total", "continuity_margin", "continuity_total", "weather_total")}
    special = {s:finite(factors[s].get("special_teams",0.)) for s in ("away", "home")}
    if any(abs(v) > 10 for v in special.values()):
        raise ValueError("Invalid special-teams scale")
    special_margin = (special["home"]-special["away"])*scale
    margin_overlay = adj["home_field"]+special_margin+adj["personnel_margin"]+adj["continuity_margin"]
    total_overlay = adj["personnel_total"]+adj["continuity_total"]+adj["weather_total"]
    away_raw = pairings["away"]["pace_adjusted_points"]+(total_overlay-margin_overlay)/2
    home_raw = pairings["home"]["pace_adjusted_points"]+(total_overlay+margin_overlay)/2
    away, home = max(0.,away_raw), max(0.,home_raw)
    if not 0 < home+away < 160:
        raise ValueError("Implausible total from matchup inputs")
    payload = {"model_version":MODEL_VERSION,"status":"provisional",
        "data_as_of":clock.isoformat(),"factor_observed_at":observed.isoformat(),"kickoff":start.isoformat(),
        "source_population_sha256":population,"baseline_points_per_team":center,
        "national_tempo":mean_tempo,"expected_tempo":tempo,"tempo_scale":scale,
        "pace_basis":"Captured possessions/plays tempo proxy relative to its FBS mean",
        "pairings":pairings,"adjustments":adj,"special_teams_margin":special_margin,
        "score_floor_adjustment":{"away":away-away_raw,"home":home-home_raw},
        "away_points":away,"home_points":home,"margin":home-away,"total":home+away,
        "calibrated_probabilities":False,"preference_ratings_used_as_point_coefficients":False}
    payload["sha256"] = fingerprint(payload)
    return payload


def install_matchup_projection(builder):
    """Replace the score baseline before simulation/market pricing, once only."""
    if getattr(builder,"_MATCHUP_PROJECTION_INSTALLED",False):
        return
    original_project, original_slate, original_tracker = builder.project_matchup, builder.slate_row, builder.tracker_rows
    builder.SLATE_COLUMNS = list(dict.fromkeys([*builder.SLATE_COLUMNS,*SLATE_FIELDS]))

    def project(game, ratings, away_personnel, home_personnel, environment):
        # Retain old output for comparison and its explicit live point overlays;
        # none of its learned score, schedule/regime correction or old residual
        # calibration is added to the new published-rating baseline.
        try:
            result = original_project(game,ratings,away_personnel,home_personnel,environment)
        except Exception as exc:
            # A failed legacy regression is not a reason to abandon a complete
            # published-rating projection. The drive engine still needs the
            # ordinary team rows for its turnover/explosiveness context.
            result = {"away":builder._rating_row(ratings,str(game["Away Team"])),
                "home":builder._rating_row(ratings,str(game["Home Team"])),
                "legacy_projection_error":str(exc)}
        legacy = {k:deepcopy(result.get(k)) for k in ("away_points","home_points","margin","total","possessions")}
        try:
            from builders.cfb_sp_ratings import load_population
            from builders.cfb_matchup_grades import collect_factors
            population = load_population(int(finite(game["Season"])))
            factors = collect_factors(builder,game,population)
            for side in ("away","home"):
                if factors[side]["team"] != str(game[f"{side.title()} Team"]):
                    raise ValueError("Published factor team identity mismatch")
            neutral = str(game.get("Neutral Site",False)).strip().lower() in ("true","yes","1")
            from builders.cfb_game_regression import _personnel_margin,_continuity_margin
            from builders.cfb_total_regression import _personnel_total_delta,_continuity_total_delta
            adjustments = {"home_field":0. if neutral else finite(environment.home_field),
                "personnel_margin":_personnel_margin(away_personnel,home_personnel),
                "personnel_total":_personnel_total_delta(away_personnel,home_personnel),
                "continuity_margin":_continuity_margin(result,away_personnel,home_personnel),
                "continuity_total":_continuity_total_delta(result,away_personnel,home_personnel),
                "weather_total":finite(environment.weather_total_adjustment)}
            projection = project_from_factors(factors,as_of=datetime.now(timezone.utc).isoformat(),
                kickoff=game["Game Time"],adjustments=adjustments)
            result.update({k:projection[k] for k in ("away_points","home_points","margin","total")})
            result.update(possessions=projection["expected_tempo"],matchup_factors=factors,
                matchup_projection=projection,matchup_projection_applied=True,
                matchup_projection_legacy=legacy,projection_engine=MODEL_VERSION,
                projection_status="Provisional matchup projection",margin_calibration=0.,total_calibration=0.)
        except Exception as exc:
            result.update(matchup_projection_applied=False,matchup_projection_legacy=legacy,
                matchup_projection={"status":"unavailable","reason":str(exc)},
                projection_engine="legacy_fallback",projection_status="Matchup inputs unavailable; legacy forecast only")
        return result

    def slate(result):
        frame = original_slate(result).astype(object)
        if frame.empty:
            return frame.reindex(columns=builder.SLATE_COLUMNS)
        p = result["projection"]
        legacy = p.get("matchup_projection_legacy",{})
        values = {FIELD:json.dumps(p.get("matchup_projection",{}),allow_nan=False),
            "Projection Engine":p.get("projection_engine",""),"Projection Status":p.get("projection_status","")}
        values.update({"Legacy Projected "+label:legacy.get(key,"") for label,key in
            (("Away","away_points"),("Home","home_points"),("Margin","margin"),("Total","total"))})
        if p.get("matchup_projection_applied"):
            values.update({label:round(finite(p[key]),4) for label,key in
                (("Projected Away","away_points"),("Projected Home","home_points"),
                 ("Projected Margin","margin"),("Projected Total","total"))})
        for key,value in values.items():frame.at[frame.index[0],key]=value
        return frame.reindex(columns=builder.SLATE_COLUMNS).astype(object)

    def tracker(result,*args,**kwargs):
        frame = original_tracker(result,*args,**kwargs)
        p = result["projection"]
        if not frame.empty and p.get("matchup_projection_applied"):
            for label,key in (("Projected Away","away_points"),("Projected Home","home_points")):
                if label in frame.columns:frame[label]=round(finite(p[key]),4)
        return frame

    builder.project_matchup, builder.slate_row, builder.tracker_rows = project,slate,tracker
    builder._MATCHUP_PROJECTION_INSTALLED = True
