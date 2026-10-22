"""Explicit ESPN reads for prospective NBA research. No scheduler or persistence.

ESPN identities are namespaced; they are never interpreted as NBA Stats IDs.
Recent minutes are a rotation suggestion, not a confirmed starting lineup.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from builders.nba_injuries import _team_key, apply_espn_report, name_key
from builders.nba_model import InputError, _serial, number, timestamp
from builders.nba_ratings import add_roster_player, package_from_bundle

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/"
ET = ZoneInfo("America/New_York")
MAX_BYTES = 6_000_000
MINUTES_WINDOW = 10


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False, default=_serial).encode()).hexdigest()


def source_get(path: str) -> tuple[dict, dict]:
    """Bounded public request; errors propagate and cannot imply healthy players."""
    url = BASE + path
    try:
        request = Request(url, headers={"User-Agent": "EZPZ-NBA-Research/0.4 (public NBA data)"})
        with urlopen(request, timeout=15) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise InputError("ESPN NBA response exceeds the size limit.")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("expected an object")
    except InputError:
        raise
    except Exception as exc:
        raise InputError(f"ESPN NBA request failed ({type(exc).__name__}); no new inputs were applied.") from None
    observed = datetime.now(timezone.utc)
    provenance = {"url": url, "observed_at": observed.isoformat(),
                  "source_sha256": hashlib.sha256(raw).hexdigest()}
    if payload.get("timestamp"):
        updated = timestamp(payload["timestamp"], "ESPN source timestamp")
        if updated > observed:
            raise InputError("ESPN source is newer than its observation time.")
        provenance["feed_as_of"] = updated.isoformat()
    return payload, provenance


def _id(value):
    value = str(value)
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise InputError("ESPN game, team and athlete IDs must be positive integers.")
    return value


def normalize_game(event: dict, source: dict) -> dict:
    try:
        competitions = event["competitions"]
        if len(competitions) != 1:
            raise InputError("Expected one NBA competition per event.")
        c = competitions[0]
        season = event["season"]
        season_type = int(season.get("type", (event.get("seasonType") or {}).get("type", 0)))
        if season_type != 2:
            raise InputError("Live NBA tracking currently accepts regular-season games only.")
        teams = {t["homeAway"]: t for t in c["competitors"]}
        if len(c["competitors"]) != 2 or set(teams) != {"away", "home"}:
            raise InputError("NBA game must have one away and one home team.")
        status = c.get("status", event.get("status"))["type"]
        tipoff = timestamp(c.get("date") or event.get("date"), "scheduled tipoff")
        if int(season["year"]) != tipoff.astimezone(ET).year + (tipoff.astimezone(ET).month >= 7):
            raise InputError("NBA game date and season disagree.")
        line_periods = {len(t.get("linescores", [])) for t in teams.values()}
        period = int(c.get("status", event.get("status", {})).get("period", 0))
        if status["completed"] is True and period == 0 and len(line_periods) == 1:
            period = next(iter(line_periods))
        output = {"schema": "nba-live-game-v1", "game_id": _id(event["id"]),
                  "season_year": int(season["year"]), "tipoff": tipoff.isoformat(),
                  "status": str(status["name"]), "state": str(status["state"]),
                  "completed": status["completed"] is True,
                  "period": period,
                  "time_valid": c.get("timeValid", event.get("timeValid", False)) is True,
                  "neutral_site": c.get("neutralSite", False) is True,
                  "source": deepcopy(source)}
        for side in ("away", "home"):
            t = teams[side]
            score = t.get("score")
            if isinstance(score, dict):
                score = score.get("value")
            score = number(score, "final score") if output["completed"] else None
            if score is not None and (score <= 0 or not score.is_integer()):
                raise InputError("Invalid final NBA score.")
            output[side] = {"espn_team_id": _id(t["team"]["id"]),
                            "name": str(t["team"]["displayName"]), "score": score}
            if score is not None and t.get("linescores"):
                values = [number(p.get("value", p.get("displayValue")), "period score") for p in t["linescores"]]
                if len(values) != period or sum(values) != score:
                    raise InputError("Final NBA period scores do not reconcile.")
        if output["away"]["espn_team_id"] == output["home"]["espn_team_id"]:
            raise InputError("NBA teams must differ.")
        if output["completed"] and (output["status"] != "STATUS_FINAL" or
                output["away"]["score"] == output["home"]["score"] or output["period"] < 4):
            raise InputError("Only a verified final score can settle an NBA prediction.")
        timestamp(source["observed_at"], "game observation")
        return output
    except InputError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise InputError(f"Malformed ESPN NBA game: {exc}") from None


def fetch_slate(day: str) -> dict:
    day = date.fromisoformat(day)
    payload, source = source_get(f"scoreboard?dates={day:%Y%m%d}&limit=1000")
    if not isinstance(payload.get("events"), list):
        raise InputError("ESPN did not return an NBA slate.")
    games, excluded = [], []
    for event in payload["events"]:
        try:
            games.append(normalize_game(event, source))
        except InputError as exc:
            excluded.append({"game_id": str(event.get("id", "")), "reason": str(exc)})
    if len({g["game_id"] for g in games}) != len(games):
        raise InputError("ESPN NBA slate has duplicate game IDs.")
    return {"date": str(day), "source": source, "games": games, "excluded": excluded}


def fetch_game(game_id: str) -> tuple[dict, dict]:
    payload, source = source_get(f"summary?event={_id(game_id)}")
    game = normalize_game(payload["header"], source)
    if game["game_id"] != str(game_id):
        raise InputError("ESPN returned a different NBA game.")
    return game, payload


def normalize_roster(payload: dict, source: dict, expected_team: dict, season_year: int) -> dict:
    try:
        if payload.get("status") != "success" or int(payload["season"]["year"]) != season_year:
            raise InputError("ESPN roster season is missing or mismatched.")
        team = payload["team"]
        if _id(team["id"]) != expected_team["espn_team_id"] or _team_key(team["displayName"]) != _team_key(expected_team["name"]):
            raise InputError("ESPN roster belongs to a different team.")
        players = []
        for athlete in payload["athletes"]:
            if not isinstance(athlete["displayName"], str) or not athlete["displayName"].strip():
                raise InputError("Roster athlete name is missing.")
            if (athlete.get("team") or {}).get("id") not in (None, team["id"], int(team["id"])):
                raise InputError("ESPN roster and athlete team disagree.")
            players.append({"espn_athlete_id": _id(athlete["id"]), "name": athlete["displayName"],
                            "roster_status": (athlete.get("status") or {}).get("name", "Unknown")})
        if not 5 <= len(players) <= 40 or len({p["espn_athlete_id"] for p in players}) != len(players):
            raise InputError("ESPN roster coverage is incomplete or duplicated.")
        return {"espn_team_id": _id(team["id"]), "name": team["displayName"],
                "season_year": season_year, "players": players, "source": deepcopy(source)}
    except InputError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise InputError(f"Malformed ESPN NBA roster: {exc}") from None


def _minutes(value):
    value = str(value).strip()
    if ":" in value:
        parts = value.split(":")
        if len(parts) != 2 or not re.fullmatch(r"\d+", parts[0]) or not re.fullmatch(r"[0-5]\d", parts[1]):
            raise InputError("Invalid box-score minutes.")
        return float(parts[0]) + float(parts[1]) / 60
    return number(value, "box-score minutes")


def checked_box_minutes(payload: dict, game: dict) -> dict:
    """Accept full, score-reconciled boxes; normalize overtime to 240 minutes."""
    if not game["completed"] or game["status"] != "STATUS_FINAL":
        raise InputError("Recent minutes require a completed regular-season game.")
    output = {}
    expected_minutes = 240 + max(0, game["period"] - 4) * 25
    for side in ("away", "home"):
        tid = game[side]["espn_team_id"]
        boxes = [t for t in payload["boxscore"]["players"] if str(t["team"]["id"]) == tid]
        if len(boxes) != 1 or len(boxes[0]["statistics"]) != 1:
            raise InputError("Ambiguous NBA team box score.")
        group = boxes[0]["statistics"][0]
        labels = group.get("labels", group.get("names", []))
        if labels.count("MIN") != 1 or labels.count("PTS") != 1:
            raise InputError("Box score has no unique minutes and points columns.")
        mi, pi = labels.index("MIN"), labels.index("PTS")
        rows = []
        for entry in group["athletes"]:
            stats = entry.get("stats", [])
            dnp = entry.get("didNotPlay") is True
            minutes = 0.0 if dnp else _minutes(stats[mi])
            points = 0.0 if dnp else number(stats[pi], "player points")
            if not 0 <= minutes <= 48 + max(0, game["period"] - 4) * 5 or points < 0:
                raise InputError("Invalid NBA box-score player values.")
            rows.append({"espn_athlete_id": _id(entry["athlete"]["id"]),
                         "name": entry["athlete"]["displayName"],
                         "minutes": minutes * 240 / expected_minutes, "points": points,
                         "did_not_play": dnp, "reason": str(entry.get("reason", ""))})
        # ESPN often rounds each displayed minute to a whole minute. Up to one
        # minute per participant is allowed, then rescale the team to exactly 240.
        total_minutes = sum(p["minutes"] for p in rows)
        if len({p["espn_athlete_id"] for p in rows}) != len(rows) or len(rows) < 5:
            raise InputError("Duplicated or incomplete player box score.")
        if abs(total_minutes - 240) > max(2, len(rows) / 2 + 0.1) or abs(sum(p["points"] for p in rows) - game[side]["score"]) > 0.01:
            raise InputError("NBA player minutes/points do not reconcile with the final game.")
        for p in rows:
            p["minutes"] *= 240 / total_minutes
        output[tid] = rows
    return output


def fetch_recent_minutes(team: dict, season_year: int, *, as_of, cache=None) -> dict:
    cutoff = timestamp(as_of, "recent-minute cutoff")
    payload, source = source_get(f"teams/{_id(team['espn_team_id'])}/schedule?season={int(season_year)}&seasontype=2")
    if int(payload["season"]["year"]) != season_year or str(payload["team"]["id"]) != team["espn_team_id"]:
        raise InputError("Recent-minute schedule belongs to another team or season.")
    games = [normalize_game(e, source) for e in payload["events"]]
    eligible = sorted((g for g in games if g["completed"] and timestamp(g["tipoff"], "prior game") < cutoff),
                      key=lambda g: g["tipoff"], reverse=True)[:MINUTES_WINDOW]
    observations, rejected = [], []
    cache = cache if cache is not None else {}
    for g in eligible:
        try:
            if g["game_id"] not in cache:
                final, raw = fetch_game(g["game_id"])
                cache[g["game_id"]] = (final, checked_box_minutes(raw, final))
            final, boxes = cache[g["game_id"]]
            if final["season_year"] != season_year or timestamp(final["tipoff"], "prior game") >= cutoff:
                raise InputError("Recent box score is not a past game in this season.")
            observations.append({"game": final, "players": deepcopy(boxes[team["espn_team_id"]])})
        except (InputError, KeyError, TypeError, ValueError, IndexError) as exc:
            rejected.append({"game_id": g["game_id"], "reason": str(exc)})
    return {"espn_team_id": team["espn_team_id"], "source": source,
            "window": MINUTES_WINDOW, "games": observations, "rejected": rejected}


def fetch_matchup_inputs(game: dict, *, team_cache=None, box_cache=None) -> dict:
    """Explicit user operation: two rosters, two schedules, up to 20 unique boxes."""
    rosters, recent = {}, {}
    cache = box_cache if box_cache is not None else {}
    team_cache = team_cache if team_cache is not None else {}
    cutoff = datetime.now(timezone.utc)
    for side in ("away", "home"):
        t = game[side]
        key = (t["espn_team_id"], game["season_year"])
        if key not in team_cache:
            raw, source = source_get(f"teams/{_id(t['espn_team_id'])}/roster?season={game['season_year']}")
            team_cache[key] = (normalize_roster(raw, source, t, game["season_year"]),
                              fetch_recent_minutes(t, game["season_year"], as_of=cutoff, cache=cache))
        rosters[side], recent[side] = deepcopy(team_cache[key])
    return {"game": deepcopy(game), "rosters": rosters, "recent": recent}


def allocate_minutes(weights: dict, caps: dict) -> dict:
    """Proportional 240-minute suggestion with explicit caps and no new players."""
    weights = {p: number(w, "minute weight") for p, w in weights.items()}
    caps = {p: min(48.0, max(0.0, number(caps[p], "minutes cap"))) for p in weights}
    if any(w < 0 for w in weights.values()):
        raise InputError("Minute weights cannot be negative.")
    active = {p for p in weights if weights[p] > 0 and caps[p] > 0}
    if sum(caps[p] for p in active) < 240 - 1e-8:
        raise InputError("Not enough positive minute weights/capacity to fill 240. Add replacement minutes manually.")
    result, remaining = {p: 0.0 for p in weights}, 240.0
    while active:
        denominator = sum(weights[p] for p in active)
        pinned = {p for p in active if remaining * weights[p] / denominator >= caps[p]}
        if not pinned:
            for p in active:
                result[p] = remaining * weights[p] / denominator
            break
        for p in pinned:
            result[p] = caps[p]
            remaining -= caps[p]
        active -= pinned
    return result


def prepare_live_package(bundle: dict, inputs: dict, injury_report: dict | None, *, as_of,
                         availability_mode="injury_feed") -> dict:
    """Preserve fitted reference rotations, reconcile current rosters, suggest minutes.

    All unlisted players stay UNKNOWN. Recent-game starters are never copied as
    confirmed starters. New identities receive a clearly namespaced zero prior.
    """
    if availability_mode not in {"injury_feed", "expected_rotation"}:
        raise InputError("Unsupported NBA availability mode.")
    expected = availability_mode == "expected_rotation"
    if expected and injury_report is not None:
        raise InputError("Expected-rotation mode cannot apply or claim an injury report.")
    if not expected and injury_report is None:
        raise InputError("A fresh ESPN injury report is required in injury-feed mode.")
    game, cutoff = inputs["game"], timestamp(as_of, "live projection cutoff")
    if game["status"] != "STATUS_SCHEDULED" or game["state"] != "pre" or not game["time_valid"]:
        raise InputError("Choose a scheduled NBA game with a confirmed start time.")
    teams = defaultdict(list)
    for t in bundle["teams"]:
        teams[_team_key(t["name"])].append(t["team_id"])
    team_ids = {}
    for side in ("away", "home"):
        matches = teams[_team_key(game[side]["name"])]
        if len(matches) != 1:
            raise InputError("Live team does not match one rated NBA team.")
        team_ids[side] = matches[0]
    package = package_from_bundle(bundle, team_ids["away"], team_ids["home"],
        game_id=game["game_id"], tipoff=game["tipoff"], as_of=cutoff)
    if game["neutral_site"]:
        package["context"]["home_court_points"] = 0.0
    candidates = defaultdict(list)
    for p in bundle["player_impacts"]:
        candidates[name_key(p["name"])].append(p["player_id"])
    historical_minutes = {}
    for t in bundle["teams"]:
        for pid, minutes in (t.get("rotation_seed") or {}).get("minutes", {p['player_id']: p['reference_minutes'] for p in t['players']}).items():
            historical_minutes[pid] = max(historical_minutes.get(pid, 0.0), float(minutes))
    identity, audit, observed_sources, used_players = {}, {}, [game["source"]], set()
    for side in ("away", "home"):
        roster, recent = inputs["rosters"][side], inputs["recent"][side]
        if roster["espn_team_id"] != game[side]["espn_team_id"] or roster["season_year"] != game["season_year"]:
            raise InputError("Current roster and scheduled matchup disagree.")
        observed_sources.extend([roster["source"], recent["source"]])
        for item in recent["games"]:
            observed_sources.append(item["game"]["source"])
            if timestamp(item["game"]["tipoff"], "past minutes") >= cutoff:
                raise InputError("Future or target-game minutes cannot seed a live prediction.")
        audit[side] = []
        identity[side] = {}
        for p in roster["players"]:
            ids = candidates[name_key(p["name"])]
            if len(ids) > 1:
                raise InputError(f"Ambiguous rated identity for {p['name']}; review IDs before preparing this game.")
            pid = ids[0] if ids else "espn:" + p["espn_athlete_id"]
            if pid in used_players:
                raise InputError("An athlete appears twice or on both current rosters.")
            used_players.add(pid)
            identity[side][p["espn_athlete_id"]] = pid
            if pid not in {x["player_id"] for x in package[side]["players"]}:
                package = add_roster_player(package, side, pid, p["name"], bundle=bundle)
            rows = [next((r for r in item["players"] if r["espn_athlete_id"] == p["espn_athlete_id"]), None)
                    for item in recent["games"]]
            if any(r is not None for r in rows):
                weight = sum(r["minutes"] if r else 0.0 for r in rows) / len(rows)
                basis = f"Mean of {len(rows)} checked current-season games (absences count zero)"
            else:
                weight = historical_minutes.get(pid, 0.0)
                basis = "Historical prior-team minute seed" if weight else "No minute evidence; manual assignment required"
            audit[side].append({**p, "player_id": pid, "weight": weight, "basis": basis,
                                "identity_basis": "unique full-name match to NBA Stats" if ids else "ESPN namespace; no NBA Stats match"})
        roster_ids = set(identity[side].values())
        plan = package["scenarios"][0][side]
        by_pid = {p["player_id"]: p for p in plan["players"]}
        for pid, entry in by_pid.items():
            entry.update(minutes=0.0, status="UNKNOWN", assumption="OUT", starter=False)
        for p in audit[side]:
            by_pid[p["player_id"]]["minutes"] = p["weight"]
            by_pid[p["player_id"]]["assumption"] = "IN" if p["weight"] else "OUT"
        plan.update(lineup_as_of=cutoff.isoformat(),
            lineup_source="ESPN current roster + checked prior-game minutes; proportional suggestion, requires review",
            report_complete=False, starters_confirmed=False)
    for source in observed_sources:
        if timestamp(source["observed_at"], "source observation") > cutoff:
            raise InputError("A live input was observed after the projection cutoff.")
    conflicts = []
    for group in injury_report["teams"] if injury_report is not None else []:
        for side in ("away", "home"):
            if _team_key(group["name"]) == _team_key(game[side]["name"]):
                for row in group["injuries"]:
                    if row.get("team_conflict") or row["espn_athlete_id"] not in identity[side]:
                        conflicts.append(f"{group['name']} {row['player_name']}: injury listing and current roster disagree")
    overrides = {eid: pid for identities in identity.values() for eid, pid in identities.items()}
    if injury_report is not None:
        package, _ = apply_espn_report(package, injury_report, bundle=bundle, player_id_overrides=overrides)
    else:
        package["availability_provenance"] = {"mode": "expected_rotation", "injury_verified": False,
            "prepared_at": cutoff.isoformat(), "source": "Checked current rosters and prior-game minute evidence",
            "note": "Temporary expected rotation; injury feed bypassed by user choice. Availability unchecked."}
        for side in ("away", "home"):
            plan = package["scenarios"][0][side]
            plan.update(injury_as_of=None, injury_source="Injury feed bypassed; availability unchecked",
                        report_complete=False, starters_confirmed=False)
            for player in plan["players"]:
                player.update(status="UNKNOWN", injury=None, starter=False)
    # Roster membership takes precedence for rotation allocation. Historical
    # departed players remain in the reference sample, with zero current minutes.
    for side in ("away", "home"):
        plan = package["scenarios"][0][side]
        current = set(identity[side].values())
        weights = {p["player_id"]: p["minutes"] if p["player_id"] in current and p["status"] != "OUT" else 0.0
                   for p in plan["players"]}
        allocated = allocate_minutes(weights, {p["player_id"]: p["minutes_limit"] for p in plan["players"]})
        for p in plan["players"]:
            p["minutes"] = allocated[p["player_id"]]
            p["assumption"] = ("IN" if p["minutes"] else "OUT") if p["status"] not in {"OUT", "AVAILABLE"} else ""
    package["scenarios"][0]["name"] = ("Expected rotation — injury feed bypassed" if expected else
        "Pregame rotation — review minutes and unresolved IN/OUT assumptions")
    package["live_provenance"] = {"game": deepcopy(game), "rosters": deepcopy(inputs["rosters"]),
        "recent": deepcopy(inputs["recent"]), "identity": identity, "minute_audit": audit,
        "rating_bundle_sha256": digest(bundle), "prepared_at": cutoff.isoformat(), "conflicts": conflicts,
        "minutes_policy": "Prior 10 current-season games; fallback to historical player minute seeds; OUT removed; proportional 240-minute allocation with 48-minute cap. Manual review required."}
    return package
