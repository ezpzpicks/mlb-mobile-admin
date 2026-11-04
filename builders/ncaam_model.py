"""Dated, market-independent college basketball efficiency baseline.

The automatic model deliberately excludes the manual draft's overlapping raw
four-factor adjustments. Grades remain Research until a NCAAM-specific policy
has been evaluated; fixed normal scales are not presented as probabilities.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import io
import math
import re
import unicodedata
from copy import deepcopy
from zoneinfo import ZoneInfo

from builders.ncaam_margin_calibration import calibrate_margin

MODEL_VERSION = "ncaam-v0.6-team-home-court-2026-11-03"
GRADE_POLICY = "ncaam-research-unvalidated-v1"


def team_key(value):
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    value = value.replace("&", " and ")
    value = re.sub(r"\bsaint\b", "st", value)
    value = re.sub(r"\bstate\b", "st", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def timestamp(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Input timestamps must include a timezone.")
    return stamp


def parse_ratings(text, season, observed_at):
    """Torvik's headered, public current-season export; never generic defaults."""
    stamp = timestamp(observed_at)
    reader = csv.DictReader(io.StringIO(text))
    required = {"team", "adjoe", "adjde", "adjt", "record"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("Ratings export is missing adjusted efficiency/tempo columns.")
    teams = {}
    for row in reader:
        name = str(row["team"]).strip()
        values = [float(row[field]) for field in ("adjoe", "adjde", "adjt")]
        if not name or not all(math.isfinite(x) for x in values):
            raise ValueError("Invalid team rating.")
        if not (60 < values[0] < 150 and 60 < values[1] < 150 and 45 < values[2] < 95):
            raise ValueError(f"Ratings out of range for {name}.")
        key = team_key(name)
        if key in teams:
            raise ValueError(f"Ambiguous rating identity: {name}.")
        teams[key] = {"team": name, "offense": values[0], "defense": values[1], "tempo": values[2],
                      "record": row["record"], "conference": row.get("conf", "")}
        for field, source in [('strength_of_schedule', 'sos'), ('opponent_offense', 'Opp OE'), ('opponent_defense', 'Opp DE')]:
            try: value = float(row.get(source, ''))
            except (ValueError, TypeError): value = None
            teams[key][field] = value if value is not None and math.isfinite(value) else None
    if len(teams) < 300:
        raise ValueError("Incomplete Division I ratings export.")
    return {"season": int(season), "observed_at": stamp.isoformat(), "teams": teams,
            "league_efficiency": sum((r["offense"] + r["defense"]) / 2 for r in teams.values()) / len(teams),
            "source": "Bart Torvik adjusted ratings", "source_url": f"https://barttorvik.com/{int(season)}_team_results.csv"}


def find_rating(bundle, aliases):
    matches = {team_key(name): bundle["teams"][team_key(name)] for name in aliases
               if team_key(name) in bundle["teams"]}
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous dated team rating: " + ", ".join(aliases))
    return next(iter(matches.values()))


def prepare_season_ratings(current, previous, now, *, d1_games=None):
    """Retain Torvik's current ratings, including its own preseason prior.

    The completed season is context only. Never blend its team ratings into
    source ratings a second time, or derive a confidence weight from W-L.
    """
    cutoff = timestamp(now)
    if int(previous['season']) != int(current['season']) - 1 or timestamp(previous['observed_at']) > cutoff or timestamp(current['observed_at']) > cutoff:
        raise ValueError('Prior ratings must come from the completed preceding season.')
    if d1_games and (d1_games.get('season') != current['season'] or timestamp(d1_games['observed_at']) > cutoff
                    or any(not isinstance(n, int) or n < 0 for n in d1_games['counts'].values())):
        raise ValueError('D1 game counts are invalid, future-dated or from another season.')
    output = deepcopy(current)
    for key, team in output['teams'].items():
        prior = previous['teams'].get(key)
        games = d1_games['counts'].get(key, 0) if d1_games else None
        team.update(games_played=games, d1_games_played=games, game_count_basis='Verified D1 game logs' if d1_games else 'Unavailable',
                    current_weight=1.0, prior_weight=0.0, opponent_adjusted=True, source_preseason_prior=True,
                    prior_season=previous['season'], prior_available=bool(prior))
        team['prior_record'] = prior['record'] if prior else ''
        team['prior_competition'] = {field: prior.get(field) for field in ('strength_of_schedule', 'opponent_offense', 'opponent_defense')} if prior else None
        for field in ('offense', 'defense', 'tempo'):
            value = team[field]; team['current_'+field] = value
            team['prior_'+field] = prior[field] if prior else None
    output['league_efficiency'] = sum((t['offense']+t['defense'])/2 for t in output['teams'].values()) / len(output['teams'])
    output['prior_source_url'] = previous['source_url']
    output['prior_observed_at'] = previous['observed_at']
    output['prior_policy'] = 'torvik-source-preseason-prior-v1'
    output['d1_games_observed_at'] = d1_games['observed_at'] if d1_games else ''
    return output


def project(game, bundle, market, now, *, lineup_adjustment=None, home_court=None):
    now = timestamp(now)
    tipoff = timestamp(game["tipoff"])
    observed = timestamp(bundle["observed_at"])
    quote = timestamp(market["as_of"])
    if not observed <= now < tipoff or now - observed > timedelta(hours=30):
        raise ValueError("Ratings must be fresh and observed before this unstarted game.")
    if not quote <= now or now - quote > timedelta(minutes=90) or quote >= tipoff:
        raise ValueError("Market quote is stale, future-dated or post-tipoff.")
    if int(bundle["season"]) != int(game["season"]):
        raise ValueError("Ratings season does not match the scheduled game.")
    if type(game.get("neutral_site")) is not bool:
        raise ValueError("Venue must be verified before projecting home court.")
    away = find_rating(bundle, game["away_aliases"])
    home = find_rating(bundle, game["home_aliases"])
    if away["team"] == home["team"]:
        raise ValueError("Opposing teams resolved to the same rating.")
    # Adjusted ratings already account for opponent quality. Do not add SOS or
    # four-factor strength a second time. Source preseason priors are retained.
    efficiency = float(bundle["league_efficiency"])
    possessions = (away["tempo"] + home["tempo"]) / 2
    away_score = possessions * away["offense"] * home["defense"] / efficiency / 100
    home_score = possessions * home["offense"] * away["defense"] / efficiency / 100
    from builders.ncaam_home_court import POLICY as COURT_POLICY, PRIOR_POINTS, school_key
    day = tipoff.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    if home_court is None:
        home_court = {"policy": COURT_POLICY, "team_key": school_key(home["team"]), "season": game["season"],
                      "value": 0.0 if game["neutral_site"] else PRIOR_POINTS, "observed_at": now.isoformat(),
                      "cutoff_date": now.astimezone(ZoneInfo("America/New_York")).date().isoformat(),
                      "last_game_date": "", "source": "Neutral site" if game["neutral_site"] else "Fallback league prior; home sample unavailable",
                      "fallback": not game["neutral_site"]}
    if (home_court.get("policy") != COURT_POLICY or home_court.get("season") != game["season"]
        or home_court.get("team_key") != school_key(home["team"]) or timestamp(home_court["observed_at"]) > now
        or home_court["cutoff_date"] > day or home_court.get("last_game_date", "") >= home_court["cutoff_date"]
        or not math.isfinite(float(home_court["value"])) or not 0 <= float(home_court["value"]) <= 8
        or (game["neutral_site"] and float(home_court["value"]) != 0)):
        raise ValueError("Home-court estimate is invalid, future-dated or for another school.")
    calibration = calibrate_margin(home_score - away_score, season=game["season"], game_date=day,
        neutral_site=game["neutral_site"], home_court_points=home_court["value"])
    court = calibration["home_court_points"]
    # Preserve the independently projected total. The learned correction scales
    # the team-ratings margin, never the market gap or the player-impact delta.
    margin_shift = (calibration["calibrated_home_margin"] - (home_score - away_score)) / 2
    away_score -= margin_shift
    home_score += margin_shift
    baseline_away, baseline_home = away_score, home_score
    if lineup_adjustment:
        a, h = lineup_adjustment['away'], lineup_adjustment['home']
        values = [p[k] for p in (a,h) for k in ('offense_change_per_100','defense_improvement_per_100')]
        if not all(math.isfinite(float(v)) for v in values): raise ValueError('Player impact adjustments must be finite.')
        away_off = away['offense'] + a['offense_change_per_100']
        home_off = home['offense'] + h['offense_change_per_100']
        away_def = away['defense'] - a['defense_improvement_per_100']
        home_def = home['defense'] - h['defense_improvement_per_100']
        if min(away_off, home_off, away_def, home_def) <= 0: raise ValueError('Invalid player-adjusted efficiency.')
        away_score = possessions * away_off * home_def / efficiency / 100 - margin_shift
        home_score = possessions * home_off * away_def / efficiency / 100 + margin_shift
    home_spread = float(market["home_spread"])
    total_line = float(market["total"])
    if not math.isfinite(home_spread) or abs(home_spread) > 100 or not 50 < total_line < 300:
        raise ValueError("Invalid spread or total.")
    fair_spread = away_score - home_score
    total = away_score + home_score
    spread_edge = home_spread - fair_spread
    total_edge = total - total_line
    signed = lambda n: f"{n:+g}" if n else "0"
    return {"away_team": game["away"], "home_team": game["home"],
            "away_score": round(away_score, 1), "home_score": round(home_score, 1),
            "possessions": round(possessions, 2), "fair_home_spread": round(fair_spread, 2), "total": round(total, 2),
            "home_spread_edge": round(spread_edge, 2), "total_edge": round(total_edge, 2),
            "spread_pick": f"{game['home']} {signed(home_spread)}" if spread_edge >= 0 else f"{game['away']} {signed(-home_spread)}",
            "total_pick": f"{'Over' if total_edge >= 0 else 'Under'} {total_line:g}",
            "spread_grade": "Research", "total_grade": "Research", "grade_policy": GRADE_POLICY,
            "neutral_site": game["neutral_site"], "home_court_points": court,
            "home_court": home_court,
            "margin_calibration": calibration,
            "baseline_away_score": round(baseline_away, 2), "baseline_home_score": round(baseline_home, 2),
            "lineup_margin_change": round((home_score-away_score)-(baseline_home-baseline_away), 2),
            "lineup_total_change": round((home_score+away_score)-(baseline_home+baseline_away), 2),
            "market": dict(market, odds=-110), "ratings_observed_at": bundle["observed_at"],
            "ratings_source": bundle["source"], "ratings_source_url": bundle["source_url"],
            "away_rating": away, "home_rating": home, "league_efficiency": efficiency,
            "prior_policy": bundle.get('prior_policy', ''), "prior_source_url": bundle.get('prior_source_url', ''),
            "lineup_adjustment": lineup_adjustment,
            "availability_mode": "team_ratings", "injury_verified": False,
            "model_status": "Research: team-specific home court and opening strength calibration; betting grades remain unvalidated"}
