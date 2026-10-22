"""ESPN NBA injury source. Network access happens only on an explicit fetch.

Return dates are estimates. This feed is not a complete official team report,
and an omitted player is never inferred AVAILABLE. ESPN and NBA IDs differ.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from http.client import responses as http_reasons
import json
import re
import time
import unicodedata
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from builders.nba_model import GameContext, InjuryInfo, InputError, PlayerProfile, STATUSES, TeamProfile, timestamp

ESPN_PAGE = "https://www.espn.com/nba/injuries"
ESPN_FEED = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries?limit=1000"
INJURY_SCHEMA = "nba-espn-injuries-v1"
MAX_RESPONSE_BYTES = 4_000_000
FETCH_ATTEMPTS = 3
RETRY_DELAYS = (2, 5)
MAX_RETRY_AFTER = 15
RETRY_HTTP = {403, 408, 429, 500, 502, 503, 504}


def _http_diagnostic(error):
    """Keep useful rejection evidence without logging bodies, cookies or tokens."""
    headers = error.headers or {}
    safe_headers = {}
    for name in ("Content-Type", "Server", "Retry-After", "X-Cache", "X-Amz-Cf-Id", "CF-Ray", "X-Request-Id"):
        value = headers.get(name)
        if value is not None:
            safe_headers[name.lower()] = re.sub(r"[\x00-\x1f\x7f]", " ", str(value))[:200]
    try:
        prefix = error.read(4096)
    except OSError:
        prefix = b""
    finally:
        error.close()
    body = prefix.decode("utf-8", errors="replace").lower()
    hints = [name for name, markers in (
        ("access_denied", ("access denied", "accessdenied", "permission to access")),
        ("rate_limit", ("too many requests", "rate limit")),
        ("security_challenge", ("captcha", "verify you are human", "automated requests", "bot detection")),
        ("upstream_error", ("bad gateway", "service unavailable", "gateway timeout")),
    ) if any(marker in body for marker in markers)]
    return {"http_status": error.code, "response_headers": safe_headers,
            "response_hints": hints, "response_prefix_sha256": hashlib.sha256(prefix).hexdigest() if prefix else None}


def _retry_delay(attempt, headers):
    """Honor short server cooldowns; defer long ones to a later scheduled run."""
    value = headers.get("retry-after")
    delay = RETRY_DELAYS[attempt - 1]
    if value:
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                seconds = 0
        if seconds > MAX_RETRY_AFTER:
            return None
        delay = max(delay, seconds)
    return delay


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"ESPN {label} is missing.")
    return value.strip()


def _id(value, label):
    text = str(value)
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise InputError(f"ESPN {label} must be a positive integer ID.")
    return text


def name_key(value):
    """Exact full-name matching after accents/punctuation, never fuzzy names."""
    return "".join(c for c in unicodedata.normalize("NFKD", value).casefold()
                   if c.isalnum() and not unicodedata.combining(c))


def _team_key(value):
    key = name_key(value)
    return {"laclippers": "losangelesclippers", "lalakers": "losangeleslakers"}.get(key, key)


def _athlete_identity(athlete):
    # The current injuries endpoint omits athlete.id. Its ESPN player-card
    # links contain the ID; injury.id is a different (often negative) ID.
    ids, urls = set(), []
    if athlete.get("id") is not None:
        ids.add(_id(athlete["id"], "athlete ID"))
    for link in athlete.get("links", []):
        url = link.get("href", "")
        parsed = urlparse(url)
        if parsed.scheme == "https" and parsed.hostname in {"www.espn.com", "espn.com"} and parsed.path.startswith("/nba/player/"):
            match = re.search(r"/id/([1-9][0-9]*)(?:/|$)", parsed.path)
            if match:
                ids.add(match.group(1))
                urls.append(url)
    if len(ids) != 1:
        raise InputError("ESPN athlete identity is missing or conflicting; injury IDs cannot be used as athlete IDs.")
    athlete_id = next(iter(ids))
    return athlete_id, urls[0] if urls else f"https://www.espn.com/nba/player/_/id/{athlete_id}"


def _return_date(value):
    if value is None or str(value).strip().lower() in {"", "unknown", "tbd", "n/a"}:
        return None
    try:
        # Keep ESPN's calendar day, including if it supplies an ISO timestamp;
        # do not shift an estimated midnight date into the prior US evening.
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except (ValueError, TypeError):
        raise InputError("ESPN supplied an invalid estimated return date.") from None


def _status(value):
    raw = value.strip().upper().replace("-", "_").replace(" ", "_")
    return {"DAY_TO_DAY": "DAY_TO_DAY", "OUT": "OUT", "QUESTIONABLE": "QUESTIONABLE",
        "DOUBTFUL": "DOUBTFUL", "PROBABLE": "PROBABLE", "AVAILABLE": "AVAILABLE",
        "ACTIVE": "AVAILABLE", "SUSPENDED": "OUT", "INJURED_RESERVE": "OUT",
        "OUT_FOR_SEASON": "OUT"}.get(raw, "UNKNOWN")


def normalize_espn_feed(payload: dict, *, fetched_at, source_sha256=None) -> dict:
    """Extract facts from ESPN's successful league response without inferring health."""
    observed = timestamp(fetched_at, "ESPN fetch timestamp")
    try:
        if payload["status"] != "success" or not isinstance(payload["injuries"], list):
            raise InputError("ESPN did not return a successful NBA injuries response.")
        feed_time = timestamp(payload["timestamp"], "ESPN feed timestamp")
        if feed_time > observed:
            raise InputError("ESPN feed timestamp is newer than the observation time.")
        season_year = int(payload["season"]["year"])
        if not 1900 < season_year < 2200 or len(payload["injuries"]) > 30:
            raise InputError("Unexpected ESPN NBA season or team coverage.")
        digest = source_sha256 or hashlib.sha256(json.dumps(payload, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise InputError("Invalid ESPN response fingerprint.")
        teams, seen_teams, seen_players = [], set(), set()
        for group in payload["injuries"]:
            team_id = _id(group["id"], "team ID")
            team_name = _text(group["displayName"], "team name")
            if team_id in seen_teams or not isinstance(group["injuries"], list):
                raise InputError("ESPN injury team groups are duplicated or malformed.")
            seen_teams.add(team_id)
            injuries = []
            for item in group["injuries"]:
                athlete, details = item["athlete"], item.get("details") or {}
                athlete_id, profile_url = _athlete_identity(athlete)
                if athlete_id in seen_players:
                    raise InputError("ESPN lists an athlete more than once; resolve conflicting reports before applying it.")
                seen_players.add(athlete_id)
                athlete_team = athlete.get("team") or {}
                team_conflict = athlete_team.get("id") is not None and str(athlete_team["id"]) != team_id
                reported = timestamp(item["date"], "ESPN injury update") if item.get("date") else None
                if reported is not None and reported > observed:
                    raise InputError("ESPN injury update is newer than the observation time.")
                source_status = _text(item["status"], "injury status")
                model_status = _status(source_status)
                injuries.append({"injury_id": str(item.get("id", "")), "espn_athlete_id": athlete_id,
                    "player_name": _text(athlete["displayName"], "player name"),
                    "source_status": source_status, "model_status": model_status,
                    "reported_at": reported.isoformat() if reported else None,
                    "estimated_return_date": _return_date(details.get("returnDate")),
                    "injury_type": str(details.get("type") or ""),
                    "detail": " ".join(str(details.get(k) or "") for k in ("side", "detail")).strip(),
                    "comment": str(item.get("shortComment") or "")[:1000], "profile_url": profile_url,
                    "status_unrecognized": model_status == "UNKNOWN", "team_conflict": team_conflict,
                    "athlete_team_name": str(athlete_team.get("displayName") or ""),
                    "athlete_team_id": str(athlete_team.get("id") or "")})
            teams.append({"espn_team_id": team_id, "name": team_name, "injuries": injuries})
        if len(seen_players) > 1000:
            raise InputError("ESPN returned an unexpectedly large injury list.")
        return {"schema": INJURY_SCHEMA, "provider": "ESPN", "source_url": ESPN_PAGE,
            "feed_url": ESPN_FEED, "fetched_at": observed.isoformat(), "feed_as_of": feed_time.isoformat(),
            "source_sha256": digest, "season_year": season_year, "teams": teams,
            "injury_count": len(seen_players), "estimated_return_count": sum(
                bool(p["estimated_return_date"]) for t in teams for p in t["injuries"]),
            "report_complete": False}
    except InputError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise InputError(f"ESPN injury response changed or is incomplete: {exc}") from None


def fetch_espn_injuries(*, on_event=None) -> dict:
    """Fetch one fresh league snapshot, with at most three timed attempts.

    Retry transient HTTP/transport failures, preserving the same URL and client
    identity. Never reuse a stale report or treat an unsuccessful feed as empty.
    The scheduler supplies an event callback for safe HTTP rejection evidence.
    """
    def emit(event, **details):
        if on_event is not None:
            on_event(event, source="ESPN injuries", url=ESPN_FEED, **details)

    for attempt in range(1, FETCH_ATTEMPTS + 1):
        request = Request(ESPN_FEED, headers={"Accept": "application/json",
            "User-Agent": "EZPZ-Picks-NBA-Research/0.3 (+https://ezpzpicks.com)"})
        diagnostic = {}
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise InputError("ESPN injury response exceeded the size limit.")
            report = normalize_espn_feed(json.loads(raw), fetched_at=datetime.now(timezone.utc),
                source_sha256=hashlib.sha256(raw).hexdigest())
        except HTTPError as exc:
            diagnostic = _http_diagnostic(exc)
            kind = "http_rejection"
            reason = f"HTTP {exc.code} {http_reasons.get(exc.code, 'error')}"
            retryable = exc.code in RETRY_HTTP and "security_challenge" not in diagnostic["response_hints"]
        except OSError as exc:
            kind, reason, retryable = "transport_error", type(exc).__name__, True
            diagnostic = {"exception_type": type(exc).__name__,
                          "reason_type": type(getattr(exc, "reason", exc)).__name__}
        except (InputError, ValueError) as exc:
            emit("injury_response_rejected", attempt=attempt, failure_kind="validation_error",
                 error=str(exc)[:500])
            if isinstance(exc, InputError):
                raise
            raise InputError("ESPN injuries could not be refreshed: invalid JSON response.") from None
        else:
            emit("injury_fetch_succeeded", attempt=attempt, recovered=attempt > 1,
                 feed_as_of=report["feed_as_of"], injury_count=report["injury_count"],
                 estimated_return_count=report["estimated_return_count"])
            return report
        delay = _retry_delay(attempt, diagnostic.get("response_headers", {})) if retryable and attempt < FETCH_ATTEMPTS else None
        emit("injury_request_failed", attempt=attempt, max_attempts=FETCH_ATTEMPTS,
             failure_kind=kind, error=reason, retrying=delay is not None,
             retry_in_seconds=delay, **diagnostic)
        if delay is None:
            raise InputError(f"ESPN injuries could not be refreshed after {attempt} attempt(s): {reason}.") from None
        time.sleep(delay)


def _info(report, row):
    return {"provider": "ESPN", "espn_athlete_id": row["espn_athlete_id"],
        "source_status": row["source_status"], "model_status": row["model_status"], "source_url": row["profile_url"],
        "observed_at": report["fetched_at"], "feed_as_of": report["feed_as_of"],
        "source_sha256": report["source_sha256"], "reported_at": row["reported_at"],
        "estimated_return_date": row["estimated_return_date"], "injury_type": row["injury_type"],
        "detail": row["detail"]}


def apply_espn_report(package: dict, report: dict, *, projection_as_of=None, bundle=None,
                      player_id_overrides=None) -> tuple[dict, dict]:
    """Apply to editable rotations. OUT minutes become unassigned, never refilled.

    The caller must compare/evaluate after assigning all replacement minutes.
    An ID override is an explicit reviewed ESPN athlete ID -> internal NBA ID.
    """
    from builders.nba_ratings import add_roster_player, validate_bundle
    updated = deepcopy(package)
    if report.get("schema") != INJURY_SCHEMA or report.get("provider") != "ESPN":
        raise InputError("Unsupported NBA injury source snapshot.")
    if projection_as_of is not None:
        updated["context"]["as_of"] = timestamp(projection_as_of, "new projection cutoff").isoformat()
    context = GameContext(**updated["context"])
    observed, feed_time = timestamp(report["fetched_at"], "ESPN observation"), timestamp(report["feed_as_of"], "ESPN feed")
    if max(observed, feed_time) > context.as_of:
        raise InputError("ESPN snapshot was observed after the projection cutoff; it cannot be used retrospectively.")
    from zoneinfo import ZoneInfo
    game_day = context.tipoff.astimezone(ZoneInfo("America/New_York"))
    season_year = game_day.year + (game_day.month >= 7)
    if report["season_year"] != season_year:
        raise InputError("ESPN injury snapshot and matchup belong to different NBA seasons.")
    candidates, known_ids = {}, defaultdict(set)
    if bundle is not None:
        validate_bundle(bundle)
        if timestamp(bundle["as_of"], "rating cutoff") > context.as_of:
            raise InputError("Player rating bundle is newer than the projection cutoff.")
        candidates.update({p["player_id"]: p for p in bundle["player_impacts"]})
    for side in ("away", "home"):
        TeamProfile(**{**updated[side], "players": tuple(PlayerProfile(**p) for p in updated[side]["players"])})
        candidates.update({p["player_id"]: p for p in updated[side]["players"]})
        for scenario in updated["scenarios"]:
            for p in scenario[side]["players"]:
                info = p.get("injury")
                if info and info.get("provider") == "ESPN":
                    known_ids[info["espn_athlete_id"]].add(p["player_id"])
    names = defaultdict(set)
    for player in candidates.values():
        names[name_key(player["name"])].add(player["player_id"])
    overrides = player_id_overrides or {}
    if any(pid not in candidates for pid in overrides.values()):
        raise InputError("An ESPN ID override must identify an existing NBA player profile.")
    groups = {_team_key(t["name"]): t for t in report["teams"]}
    if len(groups) != len(report["teams"]):
        raise InputError("ESPN team names are ambiguous.")
    resolved, unmatched = {}, []
    for group in report["teams"]:
        for row in group["injuries"]:
            info = InjuryInfo(**_info(report, row))
            if info.reported_at is not None and info.reported_at > context.as_of:
                raise InputError("ESPN news is newer than the projection cutoff.")
            if row.get("team_conflict"):
                unmatched.append({"team": group["name"], "other_team": row["athlete_team_name"],
                    "player": row["player_name"], "espn_athlete_id": row["espn_athlete_id"],
                    "reason": f"ESPN group and athlete team disagree ({row['athlete_team_name']}); verify roster"})
                continue
            ids = ({overrides[row["espn_athlete_id"]]} if row["espn_athlete_id"] in overrides else
                   known_ids.get(row["espn_athlete_id"]) or names.get(name_key(row["player_name"]), set()))
            if len(ids) == 1:
                pid = next(iter(ids))
                if pid in resolved:
                    raise InputError("Multiple ESPN players matched the same NBA ID.")
                resolved[pid] = (group, row)
            else:
                unmatched.append({"team": group["name"], "player": row["player_name"],
                    "espn_athlete_id": row["espn_athlete_id"], "reason": "ambiguous name" if ids else "no NBA ID match"})
    audit = {"matched": [], "added_players": [], "roster_conflicts": [], "unmatched": [], "pending_minutes_at_import": []}
    selected_keys = {_team_key(updated[s]["name"]) for s in ("away", "home")}
    audit["unmatched"] = [r for r in unmatched if _team_key(r["team"]) in selected_keys or
                          _team_key(r.get("other_team", "")) in selected_keys]
    for side in ("away", "home"):
        team_key = _team_key(updated[side]["name"])
        own = {pid: row for pid, (g, row) in resolved.items() if _team_key(g["name"]) == team_key}
        for pid, row in own.items():
            if pid not in {p["player_id"] for p in updated[side]["players"]}:
                updated = add_roster_player(updated, side, pid, row["player_name"], bundle=bundle)
                audit["added_players"].append({"team": updated[side]["name"], "player": row["player_name"], "player_id": pid})
            audit["matched"].append({"team": updated[side]["name"], "player": row["player_name"],
                "player_id": pid, "espn_athlete_id": row["espn_athlete_id"]})
        conflicts = {p["player_id"]: resolved[p["player_id"]][0]["name"] for p in updated[side]["players"]
                     if p["player_id"] in resolved and p["player_id"] not in own}
        audit["roster_conflicts"].extend({"team": updated[side]["name"], "player_id": pid,
            "espn_team": other} for pid, other in conflicts.items())
        for scenario in updated["scenarios"]:
            plan = scenario[side]
            for player in plan["players"]:
                pid = player["player_id"]
                player["starter"] = False
                if pid in own:
                    row = own[pid]
                    if row["model_status"] not in STATUSES:
                        raise InputError("ESPN snapshot contains an unsupported model status.")
                    player["status"], player["injury"] = row["model_status"], _info(report, row)
                    if player["status"] == "OUT":
                        player["minutes"], player["assumption"] = 0.0, ""
                    elif player["status"] == "AVAILABLE":
                        player["assumption"] = ""
                    else:
                        player["assumption"] = player.get("assumption") if player.get("assumption") in {"IN", "OUT"} else ("IN" if player["minutes"] > 0 else "OUT")
                else:
                    # No listing is not a health clearance. Remove stale ESPN
                    # status/metadata while keeping explicit rotation hypotheses.
                    player["status"], player["injury"] = "UNKNOWN", None
                    if pid in conflicts:
                        player["minutes"] = 0.0
                    player["assumption"] = "IN" if player["minutes"] > 0 else "OUT"
            plan.update(injury_as_of=report["feed_as_of"], injury_source="ESPN NBA injuries; unlisted players unverified",
                report_complete=False, starters_confirmed=False)
            missing = 240.0 - sum(float(p["minutes"]) for p in plan["players"])
            if abs(missing) > 0.01:
                audit["pending_minutes_at_import"].append({"scenario": scenario["name"], "team": updated[side]["name"],
                                                "unassigned_minutes": missing})
    updated["injury_provenance"] = {k: report[k] for k in ("schema", "provider", "source_url", "feed_url",
        "fetched_at", "feed_as_of", "source_sha256", "season_year")}
    updated["injury_provenance"].update(teams=[g for k, g in groups.items() if k in selected_keys], audit=deepcopy(audit))
    return updated, audit
