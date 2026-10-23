"""Public RotoWire NBA lineup HTML. No subscription data or private endpoints.

Lineup confirmation and availability are separate facts. Unlisted players stay
UNKNOWN, and the 'May Not Play' heading never turns Questionable into OUT.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from builders.nba_injuries import _team_key, name_key
from builders.nba_live import allocate_minutes, digest, prepare_live_package
from builders.nba_model import InputError, timestamp

URL = "https://www.rotowire.com/basketball/nba-lineups.php"
SCHEMA = "nba-rotowire-lineups-html-v1"
ET = ZoneInfo("America/New_York")
MAX_BYTES = 2_000_000
MAX_AGE = timedelta(minutes=60)
NAME_SUFFIX = re.compile(r"\s+(jr|sr|ii|iii|iv|v)\.?$", re.I)
TEAMS = dict(zip(
    "ATL BOS BKN CHA CHI CLE DAL DEN DET GSW HOU IND LAC LAL MEM MIA MIL MIN NOP NYK OKC ORL PHI PHX POR SAC SAS TOR UTA WAS".split(),
    ["Atlanta Hawks", "Boston Celtics", "Brooklyn Nets", "Charlotte Hornets", "Chicago Bulls", "Cleveland Cavaliers",
     "Dallas Mavericks", "Denver Nuggets", "Detroit Pistons", "Golden State Warriors", "Houston Rockets", "Indiana Pacers",
     "LA Clippers", "Los Angeles Lakers", "Memphis Grizzlies", "Miami Heat", "Milwaukee Bucks", "Minnesota Timberwolves",
     "New Orleans Pelicans", "New York Knicks", "Oklahoma City Thunder", "Orlando Magic", "Philadelphia 76ers", "Phoenix Suns",
     "Portland Trail Blazers", "Sacramento Kings", "San Antonio Spurs", "Toronto Raptors", "Utah Jazz", "Washington Wizards"]))
ALIASES = {"BRK": "BKN", "PHO": "PHX", "GS": "GSW", "NY": "NYK", "NO": "NOP", "NOR": "NOP", "SA": "SAS", "WSH": "WAS"}
STATUSES = {"out": "OUT", "o": "OUT", "ofs": "OUT", "outforseason": "OUT", "ques": "QUESTIONABLE", "quest": "QUESTIONABLE", "questionable": "QUESTIONABLE",
    "gtd": "QUESTIONABLE", "gametimedecision": "QUESTIONABLE", "doubt": "DOUBTFUL", "doubtful": "DOUBTFUL",
    "prob": "PROBABLE", "probable": "PROBABLE", "daytoday": "DAY_TO_DAY", "dtd": "DAY_TO_DAY"}


def _one(node, selector):
    values = node.select(selector)
    if len(values) != 1:
        raise InputError(f"RotoWire HTML needs exactly one {selector}.")
    return values[0]


def _roster_name(value):
    """Allow an omitted generational suffix, keeping full names and ambiguity."""
    text = str(value).strip()
    suffix = NAME_SUFFIX.search(text)
    return name_key(text[:suffix.start()] if suffix else text), suffix[1].lower() if suffix else ""


def parse_rotowire_html(html: str, *, fetched_at, source_url=URL) -> dict:
    observed = timestamp(fetched_at, "RotoWire observation")
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or parsed.netloc != "www.rotowire.com" or parsed.path != "/basketball/nba-lineups.php":
        raise InputError("RotoWire source must be the public NBA lineup page.")
    if len(html.encode()) > MAX_BYTES:
        raise InputError("RotoWire HTML exceeds the response size limit.")
    soup = BeautifulSoup(html, "html.parser")
    heading = _one(soup, ".page-title__secondary").get_text(" ", strip=True)
    match = re.fullmatch(r"Starting lineups for (\w+ \d{1,2}, \d{4})", heading)
    try:
        day = datetime.strptime(match[1], "%B %d, %Y").date() if match else None
    except ValueError:
        day = None
    if day is None:
        raise InputError("RotoWire page date is missing or invalid.")
    container = _one(soup, "div.lineups")
    cards = container.select("div.lineup.is-nba[data-lnum]")
    if not cards:
        raise InputError("RotoWire returned no NBA lineup cards; availability is unknown.")
    games, rejected, seen = [], [], set()
    for card in cards:
        try:
            clock = _one(card, ".lineup__time").get_text(" ", strip=True)
            tipoff = datetime.strptime(f"{day} {clock}", "%Y-%m-%d %I:%M %p ET").replace(tzinfo=ET)
            game = {"tipoff": tipoff.astimezone(timezone.utc).isoformat()}
            used_players = set()
            for side, css in (("away", "is-visit"), ("home", "is-home")):
                abbr = _one(card, f".lineup__team.{css} .lineup__abbr").get_text(strip=True)
                abbr = ALIASES.get(abbr, abbr)
                if abbr not in TEAMS:
                    raise InputError("RotoWire team abbreviation is unsupported.")
                listing = _one(card, f"ul.lineup__list.{css}")
                status = _one(listing, ".lineup__status")
                text, classes = status.get_text(" ", strip=True), status.get("class", [])
                confirmed = text == "Confirmed Lineup" and "is-confirmed" in classes
                if not confirmed and not (text == "Expected Lineup" and "is-expected" in classes):
                    raise InputError("RotoWire starting five is unavailable or unrecognized.")
                players, starters, injury_section = {}, [], False
                for row in listing.find_all("li", recursive=False):
                    if "lineup__title" in row.get("class", []):
                        if row.get_text(" ", strip=True).upper() != "MAY NOT PLAY":
                            raise InputError("RotoWire injury section changed.")
                        injury_section = True
                    if "lineup__player" not in row.get("class", []):
                        continue
                    link = _one(row, "a[href]")
                    identity = re.fullmatch(r"/basketball/player/[^/?#]+-([1-9]\d*)", link.get("href", ""))
                    name = link.get("title", "").strip()
                    if not identity or not name:
                        raise InputError("RotoWire needs an unambiguous full player name and its own player ID.")
                    injury = row.select(".lineup__inj")
                    if len(injury) > 1:
                        raise InputError("RotoWire player has conflicting injury labels.")
                    label = injury[0].get_text(" ", strip=True) if injury else ""
                    normalized = STATUSES.get(name_key(label)) if label else "UNKNOWN"
                    if normalized is None or ("has-injury-status" in row.get("class", []) and not label):
                        raise InputError("RotoWire injury designation is unrecognized.")
                    pid = "rotowire:" + identity[1]
                    entry = {"player_id": pid, "name": name, "status": normalized, "source_status": label,
                             "source_url": "https://www.rotowire.com" + link["href"]}
                    if pid in players and players[pid] != entry:
                        raise InputError("RotoWire reports conflicting facts for one player.")
                    players[pid] = entry
                    if not injury_section:
                        starters.append(pid)
                if len(starters) != 5 or len(set(starters)) != 5:
                    raise InputError("RotoWire starting lineup must contain five distinct players.")
                if any(players[pid]["status"] in {"OUT", "DOUBTFUL"} for pid in starters):
                    raise InputError("RotoWire starting five conflicts with an OUT/DOUBTFUL designation.")
                if used_players.intersection(players):
                    raise InputError("RotoWire assigns one player to both teams.")
                used_players.update(players)
                game[side] = {"team": TEAMS[abbr], "abbr": abbr, "confirmed": confirmed,
                              "starters": starters, "players": list(players.values())}
            key = (game["away"]["abbr"], game["home"]["abbr"])
            if key[0] == key[1] or key in seen:
                games = [g for g in games if (g["away"]["abbr"], g["home"]["abbr"]) != key]
                raise InputError("RotoWire matchup is duplicated or ambiguous.")
            seen.add(key)
            games.append(game)
        except (InputError, ValueError) as exc:
            rejected.append({"card": card.get("data-lnum"), "reason": str(exc)})
    return {"schema": SCHEMA, "provider": "RotoWire", "date": str(day), "source_url": source_url,
            "fetched_at": observed.isoformat(), "source_sha256": hashlib.sha256(html.encode()).hexdigest(),
            "games": games, "rejected": rejected, "tomorrow_available": bool(soup.select('a[href$="nba-lineups.php?date=tomorrow"]'))}


def fetch_rotowire_lineups(*, day=None, on_event=None) -> dict:
    """One public page per slate (two only for the advertised tomorrow page).

    No retries around access denials, challenges, subscription or login walls.
    A failed response remains a failure rather than an empty healthy roster.
    """
    def read(url):
        try:
            request = Request(url, headers={"Accept": "text/html", "User-Agent": "EZPZ-NBA-Lineups/1.0"})
            with urlopen(request, timeout=20) as response:
                raw = response.read(MAX_BYTES + 1)
                final_url = response.geturl()
                code = response.status
            if len(raw) > MAX_BYTES:
                raise InputError("RotoWire response exceeds the size limit.")
            report = parse_rotowire_html(raw.decode("utf-8"), fetched_at=datetime.now(timezone.utc), source_url=final_url)
            if on_event:
                on_event("rotowire_html_read", http_status=code, date=report["date"], games=len(report["games"]),
                         rejected=report["rejected"], source=url, source_sha256=report["source_sha256"])
            return report
        except InputError:
            raise
        except Exception as exc:
            raise InputError(f"RotoWire HTML could not be read ({type(exc).__name__}: {exc}).") from None
    report = read(URL)
    if day and report["date"] != day:
        if report["tomorrow_available"] and datetime.fromisoformat(day).date() == datetime.fromisoformat(report["date"]).date() + timedelta(days=1):
            report = read(URL + "?date=tomorrow")
        if report["date"] != day:
            raise InputError("RotoWire page belongs to another slate date; no availability was applied.")
    return report


def apply_rotowire_report(package, report, *, as_of):
    cutoff = timestamp(as_of, "RotoWire cutoff")
    observed = timestamp(report["fetched_at"], "RotoWire observation")
    if report.get("schema") != SCHEMA or not timedelta(0) <= cutoff - observed <= MAX_AGE:
        raise InputError("Refresh the RotoWire HTML snapshot before building (maximum age 60 minutes).")
    game = package["live_provenance"]["game"]
    if cutoff >= timestamp(game["tipoff"], "tipoff"):
        raise InputError("RotoWire availability must be applied before tipoff.")
    if report["date"] != str(timestamp(game["tipoff"], "tipoff").astimezone(ET).date()):
        raise InputError("RotoWire and the NBA matchup have different dates.")
    matches = [g for g in report["games"] if all(_team_key(g[s]["team"]) == _team_key(game[s]["name"]) for s in ("away", "home"))]
    if len(matches) != 1 or timestamp(matches[0]["tipoff"], "RotoWire tipoff") != timestamp(game["tipoff"], "tipoff"):
        raise InputError("RotoWire matchup, home/away order or tipoff does not match the checked NBA schedule.")
    output, lineup = deepcopy(package), matches[0]
    output.pop("injury_provenance", None)
    audit = {}
    for side in ("away", "home"):
        roster = output["live_provenance"]["rosters"][side]["players"]
        identities = output["live_provenance"]["identity"][side]
        names, suffix_names = {}, {}
        for entry in roster:
            pid = identities[entry["espn_athlete_id"]]
            names.setdefault(name_key(entry["name"]), []).append(pid)
            root, suffix = _roster_name(entry["name"])
            suffix_names.setdefault(root, []).append((pid, suffix, entry["name"]))
        source, mapped, ignored = lineup[side], {}, []
        for row in source["players"]:
            ids = names.get(name_key(row["name"]), [])
            if not ids:
                root, suffix = _roster_name(row["name"])
                variants = [(pid, full_name) for pid, roster_suffix, full_name in suffix_names.get(root, [])
                            if not suffix or not roster_suffix]
                ids = [pid for pid, _ in variants]
                if len(ids) == 1:
                    row = {**row, "roster_match": "Full name with an omitted generational suffix",
                           "roster_name": variants[0][1]}
            if not ids and row["status"] == "OUT" and row["player_id"] not in source["starters"]:
                ignored.append({**row, "reason": "Not on the checked current roster; already receives zero model minutes"})
                continue
            if len(ids) != 1:
                raise InputError(f"RotoWire player {row['name']} does not uniquely match the checked {game[side]['name']} roster.")
            if ids[0] in mapped:
                raise InputError("Multiple RotoWire IDs map to the same roster player.")
            mapped[ids[0]] = row
        starters = {pid for pid, row in mapped.items() if row["player_id"] in source["starters"]}
        plan = output["scenarios"][0][side]
        evidence = {p["player_id"]: p["weight"] for p in output["live_provenance"]["minute_audit"][side]}
        if any(evidence.get(pid, 0) <= 0 for pid in starters):
            raise InputError("A RotoWire starter has no checked minute evidence; review replacement minutes before building.")
        weights = {}
        current = set(identities.values())
        for player in plan["players"]:
            row = mapped.get(player["player_id"])
            player.update(status=row["status"] if row else "UNKNOWN", injury=None, starter=False)
            weights[player["player_id"]] = evidence.get(player["player_id"], 0) if player["player_id"] in current and player["status"] not in {"OUT", "DOUBTFUL"} else 0
        allocated = allocate_minutes(weights, {p["player_id"]: p["minutes_limit"] for p in plan["players"]})
        for player in plan["players"]:
            pid = player["player_id"]
            player.update(minutes=allocated[pid], starter=pid in starters,
                          assumption=("IN" if allocated[pid] else "OUT") if player["status"] not in {"AVAILABLE", "OUT"} else "")
        plan.update(injury_as_of=observed.isoformat(), injury_source="RotoWire public NBA lineup HTML; unlisted availability unknown",
                    lineup_as_of=observed.isoformat(), lineup_source="RotoWire " + ("confirmed" if source["confirmed"] else "expected") + " starting five",
                    report_complete=False, starters_confirmed=source["confirmed"])
        audit[side] = {"confirmed": source["confirmed"], "starter_ids": sorted(starters), "ignored_off_roster_out": ignored,
                       "players": [{"model_player_id": pid, **row} for pid, row in sorted(mapped.items())]}
    output.pop("availability_provenance", None)
    selected_report = {**deepcopy(report), "games": [deepcopy(lineup)]}
    output["availability_provenance"] = {"mode": "rotowire_html", "injury_verified": True, "provider": "RotoWire",
        "fetched_at": observed.isoformat(), "source_url": report["source_url"], "source_sha256": report["source_sha256"],
        "report": selected_report, "audit": audit, "report_complete": False,
        "note": "Source-observed labels only; unlisted players are unknown. No projected-minutes subscription data used."}
    return output


def prepare_rotowire_package(bundle, inputs, report, *, as_of):
    base = prepare_live_package(bundle, inputs, None, as_of=as_of, availability_mode="expected_rotation")
    return apply_rotowire_report(base, report, as_of=as_of)


def validate_rotowire_package(package, *, as_of):
    provenance = package.get("availability_provenance", {})
    if provenance.get("mode") != "rotowire_html" or provenance.get("injury_verified") is not True or package.get("injury_provenance"):
        raise InputError("RotoWire HTML needs its own source-observed availability provenance.")
    expected = apply_rotowire_report(package, provenance["report"], as_of=as_of)
    if digest(expected["availability_provenance"]) != digest(provenance):
        raise InputError("RotoWire availability provenance changed after source matching.")
    # Automatic scenario validation separately checks fixed minute allocation.
    for side in ("away", "home"):
        source = expected["scenarios"][0][side]
        actual = package["scenarios"][0][side]
        for field in ("injury_as_of", "injury_source", "lineup_as_of", "lineup_source", "report_complete", "starters_confirmed"):
            if actual[field] != source[field]:
                raise InputError("RotoWire lineup confirmation or source timestamp changed.")
        statuses = {p["player_id"]: (p["status"], p["starter"], p["injury"]) for p in source["players"]}
        if any((p["status"], p["starter"], p["injury"]) != statuses[p["player_id"]] for p in actual["players"]):
            raise InputError("RotoWire source statuses or starting five changed.")
