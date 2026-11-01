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

MODEL_VERSION = "ncaam-v0.2-efficiency-baseline-2026-11-01"
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


def project(game, bundle, market, now):
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
    away_score = possessions * (away["offense"] + home["defense"] - efficiency) / 100
    home_score = possessions * (home["offense"] + away["defense"] - efficiency) / 100
    court = 0.0 if game["neutral_site"] else 3.2  # provisional baseline, versioned
    away_score -= court / 2
    home_score += court / 2
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
            "market": dict(market, odds=-110), "ratings_observed_at": bundle["observed_at"],
            "ratings_source": bundle["source"], "ratings_source_url": bundle["source_url"],
            "away_rating": away, "home_rating": home, "league_efficiency": efficiency,
            "availability_mode": "team_ratings", "injury_verified": False,
            "model_status": "Research baseline; NCAAM grades and uncertainty await chronological validation"}
