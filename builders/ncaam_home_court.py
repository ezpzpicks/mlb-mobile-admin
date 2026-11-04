"""Opponent-adjusted, sample-shrunk team home courts from prior D1 results.

Uses the NFL model's rolling ridge-regression approach. College team strength
is separate for each season so roster turnover is not treated as venue value.
Date-only results are usable from the following Eastern day, never the same day.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import date, datetime
from functools import lru_cache
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import numpy as np

from builders.ncaam_model import team_key, timestamp
from shared.ncaam_division_one import _NAMES, _TORVIK_NAMES

POLICY = "ncaam-team-home-court-2026-11-03-v1"
LOOKBACK_SEASONS = 3
SEASON_DECAY = 0.78
PRIOR_POINTS = 3.2
LEAGUE_PRIOR_GAMES = 48.0
# Strengths are nuisance controls, not predictions to shrink aggressively.
# NFL's 12-point penalty confounds high-strength college teams with home value.
TEAM_STRENGTH_RIDGE = 0.25
VENUE_PRIOR_GAMES = 40.0
MIN_TEAM_GAMES = 5
MIN_HISTORY_GAMES = 500
HISTORY_FILE = Path(__file__).resolve().parents[1] / "shared/data/ncaam-home-court-history.json.gz"

_LEGACY_NAMES = {"2837": ["Texas A&M Commerce"], "232": ["College of Charleston"],
                 "85": ["IUPUI"], "112358": ["LIU Brooklyn"], "2870": ["Fort Wayne"],
                 "309": ["Louisiana Lafayette"], "2598": ["St. Francis PA", "Saint Francis PA"]}
_SCHOOL_ALIASES = {team_key(alias): team_key(_TORVIK_NAMES.get(tid, name))
                   for tid, name in _NAMES.items()
                   for alias in [name, _TORVIK_NAMES.get(tid, name), *_LEGACY_NAMES.get(tid, [])]}


def school_key(name):
    """Exact, ESPN-ID-backed source aliases; never fuzzy school matching."""
    key = team_key(name)
    return _SCHOOL_ALIASES.get(key, key)


def parse_game_history(text, season, as_of_date, members=None):
    """Require matching opposing rows, agreeing finals and verified H/A or N/N.

    Only names in that season's D1 rating group qualify when membership is
    supplied. Reciprocal rows alone cannot repair a conflicting venue or score.
    """
    cutoff = date.fromisoformat(str(as_of_date))
    groups, audit = defaultdict(list), Counter()
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        if len(row) != 31 or row[22] != str(season):
            raise ValueError("Unsupported Torvik home-court game-log layout or season.")
        day = datetime.strptime(row[0], "%m/%d/%y").date()
        if day >= cutoff:
            audit["same_day_or_future_rows"] += 1
            continue
        team, opponent = school_key(row[2]), school_key(row[4])
        if members is not None and (team not in members or opponent not in members):
            audit["outside_d1_rows"] += 1
            continue
        score = re.fullmatch(r"([WL]),\s*(\d+)\s*[-–]\s*(\d+)(?:\s.*)?", row[6])
        if not row[24] or team == opponent or row[5] not in ("H", "A", "N") or not score:
            audit["invalid_identity_venue_or_final_rows"] += 1
            continue
        won, high, low = score.groups()
        high, low = int(high), int(low)
        if high <= low or low <= 0 or high > 200:
            audit["invalid_final_score_rows"] += 1
            continue
        own, other = (high, low) if won == "W" else (low, high)
        groups[row[24]].append((day.isoformat(), team, opponent, row[5], own, other))
    games = []
    for game_id, pair in groups.items():
        if len(pair) != 2:
            audit["unpaired_or_duplicate_games"] += 1
            continue
        a, b = pair
        if a[0] != b[0] or a[1:3] != b[2:0:-1] or a[4:] != b[5:3:-1]:
            audit["conflicting_identity_or_final_games"] += 1
            continue
        neutral = a[3] == b[3] == "N"
        if not neutral and {a[3], b[3]} != {"H", "A"}:
            audit["conflicting_venue_games"] += 1
            continue
        home, away = sorted(pair, key=lambda r: r[1]) if neutral else (a, b) if a[3] == "H" else (b, a)
        games.append({"game_id": f"{season}|{game_id}", "season": int(season), "date": home[0],
                      "home_team": home[1], "away_team": away[1], "neutral_site": neutral,
                      "margin": home[4] - away[4]})
    audit["accepted_games"] = len(games)
    return sorted(games, key=lambda g: (g["date"], g["game_id"])), dict(audit)


@lru_cache(maxsize=1)
def bundled_history():
    raw = HISTORY_FILE.read_bytes()
    data = json.loads(gzip.decompress(raw))
    if data.get("policy") != POLICY or data.get("columns") != ["game_id", "season", "date", "home_team", "away_team", "neutral_site", "margin"]:
        raise ValueError("Unsupported NCAAM home-court history snapshot.")
    return {**data, "games": [dict(zip(data["columns"], row)) for row in data["games"]],
            "sha256": hashlib.sha256(raw).hexdigest()}


def fit_home_courts(games, season, cutoff_date, observed_at):
    """Joint ridge: league home + team venue + separate team/season strengths.

    Block elimination keeps the dense solve near 365 teams, rather than a
    multi-season design matrix with thousands of strength columns. All priors
    and recency settings are fixed; no market price or line enters the fit.
    """
    cutoff = date.fromisoformat(str(cutoff_date))
    observed = timestamp(observed_at)
    if cutoff > observed.astimezone(ZoneInfo("America/New_York")).date():
        raise ValueError("Home-court cutoff cannot be later than its observation.")
    candidates, seen = [], set()
    for game in games:
        if not season - LOOKBACK_SEASONS <= game["season"] <= season or date.fromisoformat(game["date"]) >= cutoff:
            continue
        if (game["game_id"] in seen or game["home_team"] == game["away_team"]
            or type(game["neutral_site"]) is not bool or not np.isfinite(float(game["margin"]))
            or abs(float(game["margin"])) > 150):
            raise ValueError("Conflicting, duplicated or invalid home-court history.")
        seen.add(game["game_id"])
        candidates.append(game)
    counts = Counter((g["season"], g[side]) for g in candidates for side in ("home_team", "away_team"))
    records = [g for g in candidates if all(counts[g["season"], g[side]] >= MIN_TEAM_GAMES for side in ("home_team", "away_team"))]
    fallback = {"policy": POLICY, "season": season, "cutoff_date": cutoff.isoformat(), "observed_at": observed.isoformat(),
                "league_points": PRIOR_POINTS, "teams": {}, "games": len(records), "seasons": [],
                "last_game_date": max((g["date"] for g in records), default=""), "fallback": True}
    if len(records) < MIN_HISTORY_GAMES:
        return fallback
    teams = sorted({g[side] for g in records for side in ("home_team", "away_team")})
    venues = {team: i + 1 for i, team in enumerate(teams)}
    size = len(teams) + 1
    normal = np.diag([LEAGUE_PRIOR_GAMES] + [VENUE_PRIOR_GAMES] * len(teams))
    target = np.zeros(size)
    target[0] = LEAGUE_PRIOR_GAMES * PRIOR_POINTS
    blocks = []
    for year in sorted({g["season"] for g in records}):
        rows = [g for g in records if g["season"] == year]
        schools = sorted({g[side] for g in rows for side in ("home_team", "away_team")})
        index = {team: i for i, team in enumerate(schools)}
        home = np.array([index[g["home_team"]] for g in rows])
        away = np.array([index[g["away_team"]] for g in rows])
        venue = np.array([venues[g["home_team"]] for g in rows])
        real_home = np.array([not g["neutral_site"] for g in rows])
        y = np.array([g["margin"] for g in rows], dtype=float)
        weight = SEASON_DECAY ** (season - year)
        strength = np.eye(len(schools)) * TEAM_STRENGTH_RIDGE * weight
        b = np.zeros(len(schools))
        cross = np.zeros((len(schools), size))
        np.add.at(strength, (home, home), weight)
        np.add.at(strength, (away, away), weight)
        np.add.at(strength, (home, away), -weight)
        np.add.at(strength, (away, home), -weight)
        np.add.at(b, home, weight * y)
        np.add.at(b, away, -weight * y)
        h, a, v, outcome = home[real_home], away[real_home], venue[real_home], y[real_home]
        np.add.at(cross[:, 0], h, weight)
        np.add.at(cross[:, 0], a, -weight)
        np.add.at(cross, (h, v), weight)
        np.add.at(cross, (a, v), -weight)
        normal[0, 0] += weight * len(v)
        np.add.at(normal[0], v, weight)
        np.add.at(normal[:, 0], v, weight)
        np.add.at(normal, (v, v), weight)
        target[0] += weight * outcome.sum()
        np.add.at(target, v, weight * outcome)
        solved = np.linalg.solve(strength, np.column_stack((b, cross)))
        normal -= cross.T @ solved[:, 1:]
        target -= cross.T @ solved[:, 0]
        blocks.append((year, rows, weight))
    coefficients = np.linalg.solve(normal, target)
    if not np.all(np.isfinite(coefficients)):
        raise ValueError("Nonfinite fitted home-court coefficients.")
    league = float(np.clip(coefficients[0], 0, 8))
    samples = defaultdict(list)
    for _, rows, weight in blocks:
        for game in rows:
            if not game["neutral_site"]:
                samples[game["home_team"]].append(weight)
    details = {}
    for team, weights in samples.items():
        total = sum(weights)
        effective = total * total / sum(w * w for w in weights)
        adjustment = float(coefficients[venues[team]])
        details[team] = {"value": round(float(np.clip(league + adjustment, 0, 8)), 4),
                         "venue_adjustment": round(adjustment, 4), "actual_home_games": len(weights),
                         "weighted_home_games": round(total, 2), "effective_home_games": round(effective, 2),
                         "sample_support": round(total / (total + VENUE_PRIOR_GAMES), 4)}
    return {**fallback, "league_points": round(league, 4), "teams": details, "fallback": False,
            "seasons": sorted({g["season"] for g in records}),
            "method": "Recency-weighted joint ridge; separate season strengths; team venue shrinkage",
            "minimum_team_games": MIN_TEAM_GAMES, "venue_prior_games": VENUE_PRIOR_GAMES,
            "lookback_seasons": LOOKBACK_SEASONS}


def home_court_for(model, team, *, season, game_date, neutral_site):
    """Resolve a school explicitly; a missing venue uses the learned league prior."""
    key = school_key(team)
    if (model.get("policy") != POLICY or model.get("season") != season
        or date.fromisoformat(model["cutoff_date"]) > date.fromisoformat(str(game_date))
        or model.get("last_game_date", "") >= str(game_date)):
        raise ValueError("Wrong-season or postgame home-court estimate.")
    details = model["teams"].get(key)
    league = float(model["league_points"])
    value = 0.0 if neutral_site else float(details["value"]) if details else league
    return {"policy": POLICY, "team_key": key, "season": season, "value": value,
            "league_points": 0.0 if neutral_site else league,
            "venue_adjustment": 0.0 if neutral_site or not details else details["venue_adjustment"],
            "actual_home_games": 0 if neutral_site or not details else details["actual_home_games"],
            "effective_home_games": 0.0 if neutral_site or not details else details["effective_home_games"],
            "sample_support": 0.0 if neutral_site or not details else details["sample_support"],
            "cutoff_date": model["cutoff_date"], "last_game_date": model["last_game_date"],
            "observed_at": model["observed_at"], "history_games": model["games"], "seasons": model["seasons"],
            "source": "Neutral site" if neutral_site else "Team home-court ridge model" if details else "League prior; team home sample unavailable",
            "fallback": not bool(details) and not neutral_site}
