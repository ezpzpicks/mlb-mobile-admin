"""Covers.com personnel and weather overlays for the CFB model.

The layer is additive and fail-open: if Covers is unavailable, ambiguous, or
changes markup, the existing CFB personnel/environment logic remains in force.
It stores compact weekly starter/injury observations so QB continuity and
return-from-injury inference improve as the season progresses.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from difflib import SequenceMatcher
import hashlib
import json
import math
import re
import threading
import time
from typing import Any
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

COVERS_BASE = "https://www.covers.com"
COVERS_INJURIES_URL = f"{COVERS_BASE}/sport/football/ncaaf/injuries"
COVERS_WEATHER_URL = f"{COVERS_BASE}/sport/ncaaf/weather"
COVERS_NFL_WEATHER_URL = f"{COVERS_BASE}/sport/football/nfl/weather"

STARTER_HISTORY_TAB = "covers_starter_history"
STARTER_HISTORY_COLUMNS = [
    "Observed Date", "Season", "Week", "Game ID", "Team", "Unit", "Position",
    "Player", "Source URL", "Model Version",
]
INJURY_HISTORY_TAB = "covers_injury_history"
INJURY_HISTORY_COLUMNS = [
    "Observed Date", "Season", "Week", "Game ID", "Team", "Player", "Position",
    "Status", "Detail", "Source URL", "Model Version",
]

DIRECTORY_TTL = 6 * 60 * 60
TEAM_TTL = 30 * 60
WEATHER_TTL = 30 * 60
STALE_MAX_AGE = 48 * 60 * 60
STARTER_FLUSH_SIZE = 300
INJURY_FLUSH_SIZE = 150

POSITION_POINTS = {
    "QB": 4.50, "RB": 0.90, "FB": 0.45, "WR": 0.75, "TE": 0.55,
    "OT": 0.45, "OG": 0.45, "OL": 0.45, "C": 0.45,
    "DL": 0.45, "DE": 0.45, "DT": 0.45, "NT": 0.45,
    "LB": 0.40, "ILB": 0.40, "OLB": 0.40,
    "CB": 0.45, "S": 0.45, "DB": 0.45, "K": 0.30, "P": 0.15,
}

# ESPN/open-data short names that are not reliably recoverable from the Covers
# mascot slug with fuzzy matching alone. Ambiguous Miami variants stay explicit.
_ALIAS_TO_SLUG = {
    "ole miss": "mississippi-rebels",
    "app state": "appalachian-state-mountaineers",
    "nc state": "nc-state-wolfpack",
    "pitt": "pittsburgh-panthers",
    "southern miss": "southern-miss-golden-eagles",
    "la tech": "louisiana-tech-bulldogs",
    "fiu": "florida-international-panthers",
    "fau": "florida-atlantic-owls",
    "ulm": "louisiana-monroe-warhawks",
    "utep": "utep-miners",
    "ucf": "ucf-knights",
    "usc": "usc-trojans",
    "uconn": "connecticut-huskies",
    "umass": "massachusetts-minutemen",
    "utsa": "utsa-roadrunners",
    "smu": "smu-mustangs",
    "tcu": "tcu-horned-frogs",
    "byu": "byu-cougars",
    "lsu": "lsu-tigers",
    "ucla": "ucla-bruins",
    "hawai i": "hawaii-rainbow-warriors",
    "miami fl": "miami-hurricanes",
    "miami florida": "miami-hurricanes",
    "miami oh": "miami-oh-redhawks",
    "miami ohio": "miami-oh-redhawks",
}

_LOCK = threading.RLock()
_MEMORY_HTML: dict[str, tuple[float, str]] = {}
_TEAM_REPORTS: dict[str, tuple[float, dict[str, Any]]] = {}
_DIRECTORY: tuple[float, list[dict[str, str]]] | None = None
_WEATHER: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_FALLBACK_PENDING: dict[str, list[dict[str, Any]]] = {"starters": [], "injuries": []}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _norm(value: Any) -> str:
    text = _clean_text(value).lower().replace("&", " and ")
    text = re.sub(r"\bst[.]?\b", "state", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _rating_classification(rating: Any) -> str:
    if not isinstance(rating, dict):
        return "unknown"
    value = _clean_text(rating.get("Classification", "")).lower()
    return value if value in {"fbs", "fcs"} else "unknown"


def _authoritative_classification(builder: Any, team: str, rating: Any) -> str:
    """Resolve FBS/FCS from the live ESPN FBS directory before cached ratings.

    Older persisted rating snapshots can contain a stale/default FBS label for
    an FCS opponent. A complete ESPN FBS directory is authoritative here: a
    positive match is FBS, while a missing team is FCS. If the directory is
    unavailable or truncated, fall back to the rating and keep the gate closed
    for anything still unknown.
    """
    try:
        index = builder._espn_team_index()
        if isinstance(index, dict):
            canonicalize = getattr(builder, "_canonical_team_name", None)

            def key(value: Any) -> str:
                if callable(canonicalize):
                    try:
                        value = canonicalize(value)
                    except Exception:
                        pass
                return _norm(value)

            names = {key(name) for name in index if key(name)}
            target = key(team)
            if target and target in names:
                return "fbs"
            if target and len(names) >= 100:
                return "fcs"
    except Exception:
        pass
    return _rating_classification(rating)


def _slug_label(slug: str) -> str:
    return " ".join(slug.replace("-", " ").split())


def _cache_file(builder: Any, key: str):
    digest = hashlib.sha256(f"covers:{key}".encode()).hexdigest()
    return builder.CACHE_DIR / f"covers_{digest}.json"


def _fetch_html(builder: Any, url: str, *, ttl: int) -> str:
    now = time.time()
    with _LOCK:
        hit = _MEMORY_HTML.get(url)
        if hit and now - hit[0] <= ttl:
            return hit[1]
    path = _cache_file(builder, url)
    if path.exists() and now - path.stat().st_mtime <= ttl:
        try:
            html = str(json.loads(path.read_text()).get("html", ""))
            if html:
                with _LOCK:
                    _MEMORY_HTML[url] = (now, html)
                return html
        except Exception:
            pass
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; EZPZ-Picks-CFB/2.4; +https://ezpzpicks.com)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
        "Cache-Control": "no-cache",
    }
    try:
        response = requests.get(url, headers=headers, timeout=(4, 12))
        response.raise_for_status()
        html = response.text
        if len(html) < 500:
            raise RuntimeError("Covers returned an unexpectedly small page")
        try:
            path.write_text(json.dumps({"fetched": now, "html": html}))
        except Exception:
            pass
        with _LOCK:
            _MEMORY_HTML[url] = (now, html)
        return html
    except Exception:
        if path.exists() and now - path.stat().st_mtime <= STALE_MAX_AGE:
            try:
                html = str(json.loads(path.read_text()).get("html", ""))
                if html:
                    with _LOCK:
                        _MEMORY_HTML[url] = (now, html)
                    return html
            except Exception:
                pass
        raise


def _candidate_score(query: str, candidate: str) -> float:
    q, c = _norm(query), _norm(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    qt, ct = q.split(), c.split()
    qset, cset = set(qt), set(ct)
    if qset.issubset(cset):
        base = 0.86 if len(qt) == 1 else 0.93
        return min(0.99, base + 0.05 * len(qt) / max(1, len(ct)))
    if cset.issubset(qset):
        return min(0.96, 0.89 + 0.04 * len(ct) / max(1, len(qt)))
    overlap = len(qset & cset) / max(1, len(qset | cset))
    sequence = SequenceMatcher(None, q, c).ratio()
    return 0.56 * sequence + 0.44 * overlap


def _directory(builder: Any) -> list[dict[str, str]]:
    global _DIRECTORY
    now = time.time()
    with _LOCK:
        if _DIRECTORY and now - _DIRECTORY[0] <= DIRECTORY_TTL:
            return list(_DIRECTORY[1])
    soup = BeautifulSoup(_fetch_html(builder, COVERS_INJURIES_URL, ttl=DIRECTORY_TTL), "html.parser")
    # Covers now mixes team overview links (.../<slug>) with direct injury
    # links (.../<slug>/injuries). Extract the team slug from either shape and
    # always canonicalize to the injury endpoint. Keeping this in the core
    # resolver avoids relying on a later Streamlit monkey-patch.
    pattern = re.compile(
        r"/sport/football/ncaaf/teams/main/([^/?#]+)(?:/injuries)?(?:[/?#].*)?$",
        re.I,
    )
    rows: dict[str, dict[str, str]] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        match = pattern.search(href)
        if not match:
            continue
        slug = match.group(1).strip("/")
        rows[slug] = {
            "slug": slug,
            "label": _clean_text(anchor.get_text(" ", strip=True)) or _slug_label(slug),
            "slug_label": _slug_label(slug),
            "url": f"{COVERS_BASE}/sport/football/ncaaf/teams/main/{slug}/injuries",
        }
    directory = list(rows.values())
    with _LOCK:
        _DIRECTORY = (now, directory)
    return directory


def _espn_directory_aliases(builder: Any, team: str) -> list[str]:
    """Return mascot-bearing ESPN names for an exact canonical team match.

    The builder normally identifies schools by location (for example, "Utah"
    or "Oregon"), while Covers uses mascot-bearing labels/slugs ("Utah Utes",
    "Oregon Ducks"). Resolving through ESPN first prevents base-name schools
    from colliding with State/Tech/etc. variants in fuzzy matching.
    """
    try:
        index = builder._espn_team_index()
    except Exception:
        return []
    if not isinstance(index, dict) or not index:
        return []

    canonicalize = getattr(builder, "_canonical_team_name", None)

    def key(value: Any) -> str:
        if callable(canonicalize):
            try:
                value = canonicalize(value)
            except Exception:
                pass
        return _norm(value)

    target = key(team)
    if not target:
        return []

    matched: dict[str, Any] | None = None
    for location, payload in index.items():
        if key(location) == target:
            matched = payload if isinstance(payload, dict) else {}
            break
    if matched is None:
        return []

    aliases: list[str] = []
    for field in ("displayName", "shortDisplayName", "name", "location"):
        value = _clean_text(matched.get(field, ""))
        if value and _norm(value) not in {_norm(alias) for alias in aliases}:
            aliases.append(value)

    location = _clean_text(matched.get("location", ""))
    mascot = _clean_text(matched.get("name", ""))
    combined = _clean_text(f"{location} {mascot}")
    if combined and _norm(combined) not in {_norm(alias) for alias in aliases}:
        aliases.insert(0, combined)

    # Prefer aliases that actually add identifying information beyond the
    # builder's short location name. "Utah Utes" should be tried before "Utah".
    aliases.sort(key=lambda value: (len(_norm(value).split()), len(value)), reverse=True)
    return aliases


def _covers_team_url(slug: str) -> str:
    slug = _clean_text(slug).strip().strip("/")
    return (
        f"{COVERS_BASE}/sport/football/ncaaf/teams/main/{slug}/injuries"
        if slug
        else ""
    )


def _slugify_team_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _clean_text(value).lower()).strip("-")


def _direct_team_urls(builder: Any, team: str) -> list[str]:
    """Build deterministic Covers URLs before touching the landing directory.

    ESPN supplies mascot-bearing team identities (for example Georgia Bulldogs
    versus Georgia State Panthers). Covers' normal team slugs follow those full
    identities for the state/base-name collision cases. Explicit legacy aliases
    remain first because a handful of schools use non-literal Covers slugs.
    """
    urls: list[str] = []

    def add_slug(slug: str) -> None:
        url = _covers_team_url(slug)
        if url and url not in urls:
            urls.append(url)

    alias_slug = _ALIAS_TO_SLUG.get(_norm(team))
    if alias_slug:
        add_slug(alias_slug)

    canonical_team: Any = team
    canonicalize = getattr(builder, "_canonical_team_name", None)
    if callable(canonicalize):
        try:
            canonical_team = canonicalize(team)
        except Exception:
            canonical_team = team
    target_tokens = set(_norm(canonical_team).split())

    for alias in _espn_directory_aliases(builder, team):
        alias_norm = _norm(alias)
        alias_tokens = set(alias_norm.split())
        # A bare location such as "Georgia" is not a useful direct Covers slug.
        # Only use ESPN identities that add mascot/identity information and still
        # contain the full canonical school name.
        if not alias_norm or alias_norm == _norm(canonical_team):
            continue
        if target_tokens and not target_tokens.issubset(alias_tokens):
            continue
        add_slug(_slugify_team_label(alias))

    return urls


def _team_url_candidates(builder: Any, team: str) -> list[str]:
    """Return ordered, de-duplicated Covers injury URLs for one school."""
    urls = _direct_team_urls(builder, team)

    def add(url: str) -> None:
        if url and url not in urls:
            urls.append(url)

    # Directory matching remains a verification/fallback path, but a temporary
    # landing-page failure must no longer block deterministic ESPN-derived URLs.
    try:
        directory = _directory(builder)
    except Exception:
        directory = []

    espn_aliases = _espn_directory_aliases(builder, team)
    if directory and espn_aliases:
        scored = []
        for row in directory:
            score = max(
                max(_candidate_score(alias, row["label"]), _candidate_score(alias, row["slug_label"]))
                for alias in espn_aliases
            )
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        if scored and scored[0][0] >= 0.90:
            if len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.025:
                add(scored[0][1]["url"])

    if directory:
        scored = []
        for row in directory:
            score = max(_candidate_score(team, row["label"]), _candidate_score(team, row["slug_label"]))
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        if scored and scored[0][0] >= 0.83:
            if len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.025:
                add(scored[0][1]["url"])

    return urls


def _team_url(builder: Any, team: str) -> str:
    candidates = _team_url_candidates(builder, team)
    return candidates[0] if candidates else ""


def _headers(table: Any) -> list[str]:
    first = table.find("tr") if table else None
    return [_norm(cell.get_text(" ", strip=True)) for cell in first.find_all(["th", "td"])] if first else []


def _rows(table: Any) -> list[list[str]]:
    output = []
    for tr in table.find_all("tr"):
        cells = [_clean_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
        if cells:
            output.append(cells)
    return output


def _parse_team_report(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    team = _clean_text(h1.get_text(" ", strip=True) if h1 else "")
    injuries: list[dict[str, str]] = []
    starters: list[dict[str, str]] = []
    starter_table_index = 0
    for table in soup.find_all("table"):
        headers = _headers(table)
        header_set = set(headers)
        rows = _rows(table)
        if {"player", "pos", "status"}.issubset(header_set):
            pidx, xidx, sidx = headers.index("player"), headers.index("pos"), headers.index("status")
            for cells in rows[1:]:
                if len(cells) <= max(pidx, xidx, sidx):
                    continue
                player, position, status = cells[pidx], cells[xidx].upper(), cells[sidx]
                if player and position and status:
                    extras = [cell for i, cell in enumerate(cells) if i not in {pidx, xidx, sidx}]
                    injuries.append({"player": player, "position": position, "status": status, "detail": " ".join(extras)})
        elif {"pos", "player"}.issubset(header_set) and "status" not in header_set:
            unit = "Offense" if starter_table_index == 0 else "Defense"
            starter_table_index += 1
            xidx, pidx = headers.index("pos"), headers.index("player")
            for cells in rows[1:]:
                if len(cells) > max(xidx, pidx) and cells[xidx] and cells[pidx]:
                    starters.append({"unit": unit, "position": cells[xidx].upper(), "player": cells[pidx]})
    if not starters:
        visible = [_clean_text(value) for value in soup.stripped_strings]
        try:
            start = next(i for i, value in enumerate(visible) if "starters - last game" in value.lower())
        except StopIteration:
            start = -1
        if start >= 0:
            unit = ""
            tokens = set(POSITION_POINTS) | {"LS"}
            chunk = visible[start:start + 190]
            i = 0
            while i < len(chunk):
                token = chunk[i]
                if token.lower() == "offense":
                    unit = "Offense"
                elif token.lower() == "defense":
                    unit = "Defense"
                elif token.upper() in tokens and i + 2 < len(chunk):
                    number, player = chunk[i + 1], chunk[i + 2]
                    if unit and re.fullmatch(r"\d{1,3}", number or ""):
                        starters.append({"unit": unit, "position": token.upper(), "player": player})
                        i += 2
                i += 1
    return {"team": team, "injuries": injuries, "starters": starters, "url": url, "ok": bool(injuries or starters)}


def _team_report(builder: Any, team: str) -> dict[str, Any]:
    key, now = _norm(team), time.time()
    with _LOCK:
        hit = _TEAM_REPORTS.get(key)
        if hit and now - hit[0] <= TEAM_TTL:
            return dict(hit[1])

    # Try deterministic identity URLs first, then directory-derived fallbacks.
    # One bad/missing candidate must not prevent the next verified identity from
    # being attempted.
    try:
        urls = _team_url_candidates(builder, team)
    except Exception:
        urls = []

    for url in urls:
        try:
            report = _parse_team_report(_fetch_html(builder, url, ttl=TEAM_TTL), url)
        except Exception:
            continue
        if not report.get("ok"):
            continue
        with _LOCK:
            _TEAM_REPORTS[key] = (now, report)
        return dict(report)

    return {"team": team, "injuries": [], "starters": [], "url": "", "ok": False}


def _parse_weather_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def parse_block(matchup: str, text: str) -> None:
        if "@" not in matchup:
            return
        away, home = [_clean_text(part) for part in matchup.split("@", 1)]
        if not away or not home:
            return
        temp = re.search(r"(-?\d+(?:\.\d+)?)\s*°\s*F", text, re.I)
        wind = re.search(r"(\d+(?:\.\d+)?)\s*Mph\b", text, re.I)
        pop = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:P\s*\.?\s*O\s*\.?\s*P\.?|Precip)", text, re.I)
        if not (temp or wind or pop):
            return
        key = (_norm(away), _norm(home))
        if key in seen:
            return
        seen.add(key)
        output.append({
            "away": away, "home": home,
            "temperature": float(temp.group(1)) if temp else math.nan,
            "wind": float(wind.group(1)) if wind else math.nan,
            "precipitation": float(pop.group(1)) / 100.0 if pop else math.nan,
        })

    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5"]):
        matchup = _clean_text(heading.get_text(" ", strip=True))
        if " @ " not in f" {matchup} ":
            continue
        node, block = heading, ""
        for _ in range(6):
            node = node.parent
            if node is None:
                break
            candidate = _clean_text(node.get_text(" ", strip=True))
            if len(candidate) > 5000:
                break
            block = candidate
            if "Mph" in candidate or "P.O.P" in candidate or "Humidity" in candidate:
                break
        parse_block(matchup, block)
    if output:
        return output
    lines = [_clean_text(value) for value in soup.stripped_strings]
    starts = [i for i, value in enumerate(lines) if " @ " in f" {value} " and len(value) < 120]
    for offset, start in enumerate(starts):
        end = starts[offset + 1] if offset + 1 < len(starts) else min(len(lines), start + 160)
        parse_block(lines[start], " ".join(lines[start:end]))
    return output


def _weather_cards(builder: Any, league: str = "ncaaf") -> list[dict[str, Any]]:
    league_key = "nfl" if _clean_text(league).lower() == "nfl" else "ncaaf"
    now = time.time()
    with _LOCK:
        cached = _WEATHER.get(league_key)
        if cached and now - cached[0] <= WEATHER_TTL:
            return list(cached[1])
    url = COVERS_NFL_WEATHER_URL if league_key == "nfl" else COVERS_WEATHER_URL
    try:
        cards = _parse_weather_html(_fetch_html(builder, url, ttl=WEATHER_TTL))
    except Exception:
        cards = []
    with _LOCK:
        _WEATHER[league_key] = (now, cards)
    return list(cards)


def _match_weather(builder: Any, away: str, home: str, league: str = "ncaaf") -> dict[str, Any]:
    scored = [
        ((_candidate_score(away, card.get("away", "")) + _candidate_score(home, card.get("home", ""))) / 2.0, card)
        for card in _weather_cards(builder, league)
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or scored[0][0] < 0.86:
        return {}
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.02:
        return {}
    return dict(scored[0][1])


def _player_signature(name: str) -> tuple[str, str]:
    tokens = [t for t in _norm(name).split() if t not in {"jr", "sr", "ii", "iii", "iv"}]
    if not tokens:
        return "", ""
    return (tokens[0][0] if tokens[0] else ""), tokens[-1]


def _player_matches(a: str, b: str) -> bool:
    ai, alast = _player_signature(a)
    bi, blast = _player_signature(b)
    return bool(alast and alast == blast and (not ai or not bi or ai == bi))


def _severity(status: str, detail: str = "") -> float:
    text = f"{status} {detail}".lower()
    if any(term in text for term in ("cleared", "available", "will play", "expected to play", "probable")):
        return 0.12
    if any(term in text for term in ("season-ending", "remainder of season", "remainder of the season", "out", "suspended", "inactive", "will miss", "shut down")):
        return 1.0
    if "doubtful" in text:
        return 0.82
    if any(term in text for term in ("questionable", "game-time", "game time", "unclear", "remains to be seen")):
        return 0.40
    return 0.30


def _pending(builder: Any, key: str) -> list[dict[str, Any]]:
    try:
        state_key = f"cfb_covers_pending_{key}"
        values = builder.st.session_state.get(state_key)
        if not isinstance(values, list):
            values = []
            builder.st.session_state[state_key] = values
        return values
    except Exception:
        return _FALLBACK_PENDING[key]


def _history(builder: Any, tab: str, columns: list[str]) -> pd.DataFrame:
    try:
        frame = builder._sheet(tab, columns)
        return frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame(columns=columns)
    except Exception:
        return pd.DataFrame(columns=columns)


def _buffer(builder: Any, report: dict[str, Any], team: str, season: int, week: int, game_id: str) -> None:
    observed = date.today().isoformat()
    version = str(getattr(builder, "MODEL_VERSION", ""))
    starters, injuries = _pending(builder, "starters"), _pending(builder, "injuries")
    starter_keys = {(str(x.get("Season")), str(x.get("Week")), _norm(x.get("Team")), x.get("Unit"), x.get("Position"), _norm(x.get("Player"))) for x in starters}
    injury_keys = {(x.get("Observed Date"), _norm(x.get("Team")), _norm(x.get("Player")), x.get("Status")) for x in injuries}
    for row in report.get("starters", []):
        item = {
            "Observed Date": observed, "Season": season, "Week": week, "Game ID": game_id,
            "Team": team, "Unit": row.get("unit", ""), "Position": _clean_text(row.get("position")).upper(),
            "Player": row.get("player", ""), "Source URL": report.get("url", ""), "Model Version": version,
        }
        key = (str(season), str(week), _norm(team), item["Unit"], item["Position"], _norm(item["Player"]))
        if key not in starter_keys:
            starters.append(item); starter_keys.add(key)
    for row in report.get("injuries", []):
        item = {
            "Observed Date": observed, "Season": season, "Week": week, "Game ID": game_id,
            "Team": team, "Player": row.get("player", ""), "Position": row.get("position", ""),
            "Status": row.get("status", ""), "Detail": row.get("detail", ""),
            "Source URL": report.get("url", ""), "Model Version": version,
        }
        key = (observed, _norm(team), _norm(item["Player"]), item["Status"])
        if key not in injury_keys:
            injuries.append(item); injury_keys.add(key)


def _write_pending(builder: Any, key: str, tab: str, columns: list[str], dedupe: list[str]) -> bool:
    pending = _pending(builder, key)
    if not pending:
        return True
    incoming = pd.DataFrame(pending).reindex(columns=columns)
    combined = pd.concat([_history(builder, tab, columns), incoming], ignore_index=True)
    combined = combined.drop_duplicates(subset=dedupe, keep="last")
    try:
        success = bool(builder.write_sheet(tab, combined, columns))
    except Exception:
        success = False
    if success:
        try:
            builder.st.session_state[f"cfb_sheet_cache::{tab}"] = combined.copy()
        except Exception:
            pass
        pending.clear()
    return success


def _flush_history(builder: Any, *, force: bool = False) -> None:
    starters, injuries = _pending(builder, "starters"), _pending(builder, "injuries")
    if not force and len(starters) < STARTER_FLUSH_SIZE and len(injuries) < INJURY_FLUSH_SIZE:
        return
    _write_pending(builder, "starters", STARTER_HISTORY_TAB, STARTER_HISTORY_COLUMNS, ["Season", "Week", "Team", "Unit", "Position", "Player"])
    _write_pending(builder, "injuries", INJURY_HISTORY_TAB, INJURY_HISTORY_COLUMNS, ["Observed Date", "Team", "Player", "Status"])


def _prior_rows(builder: Any, tab: str, columns: list[str], team: str, season: int, week: int) -> pd.DataFrame:
    frame = _history(builder, tab, columns)
    if frame.empty:
        return frame
    subset = frame[(frame["Season"].astype(str) == str(season)) & (frame["Team"].map(_norm) == _norm(team))].copy()
    return subset[pd.to_numeric(subset.get("Week"), errors="coerce") < int(week)].copy()


def _modal_prior_qb(builder: Any, team: str, season: int, week: int) -> tuple[str, int, int]:
    history = _prior_rows(builder, STARTER_HISTORY_TAB, STARTER_HISTORY_COLUMNS, team, season, week)
    if history.empty:
        return "", 0, 0
    qbs = history[history["Position"].astype(str).str.upper() == "QB"].drop_duplicates(subset=["Week"], keep="last")
    names = [_clean_text(value) for value in qbs.get("Player", []) if _clean_text(value)]
    if not names:
        return "", 0, 0
    player, count = Counter(names).most_common(1)[0]
    return player, count, len(names)


def _previous_injury_status(builder: Any, team: str, player: str, season: int, week: int) -> str:
    history = _prior_rows(builder, INJURY_HISTORY_TAB, INJURY_HISTORY_COLUMNS, team, season, week)
    if history.empty:
        return ""
    matched = history[history["Player"].map(lambda value: _player_matches(str(value), player))].copy()
    if matched.empty:
        return ""
    matched["_week"] = pd.to_numeric(matched["Week"], errors="coerce").fillna(-1)
    matched = matched.sort_values(["_week", "Observed Date"])
    return _clean_text(matched.iloc[-1].get("Status"))


def _injury_for(injuries: list[dict[str, str]], player: str) -> dict[str, str] | None:
    return next((row for row in injuries if _player_matches(str(row.get("player", "")), player)), None)


def _starter_names(report: dict[str, Any]) -> list[str]:
    return [_clean_text(row.get("player")) for row in report.get("starters", []) if _clean_text(row.get("player"))]


def _historical_starter_names(builder: Any, team: str, season: int, week: int) -> list[str]:
    history = _prior_rows(builder, STARTER_HISTORY_TAB, STARTER_HISTORY_COLUMNS, team, season, week)
    return [_clean_text(value) for value in history.get("Player", []) if _clean_text(value)] if not history.empty else []


def _personnel_overlay(builder: Any, base: Any, report: dict[str, Any], team: str, season: int, week: int) -> Any:
    if not report.get("ok"):
        return base
    source = _clean_text(getattr(base, "source", "")).lower()
    expected_base = _clean_text(getattr(base, "expected_qb", ""))
    if ("manual" in source or "saved" in source) and expected_base not in {"", "Unconfirmed"}:
        return base

    starters = report.get("starters", [])
    injuries = report.get("injuries", [])
    current_qb = next((_clean_text(row.get("player")) for row in starters if str(row.get("position", "")).upper() == "QB"), "")
    modal_qb, modal_count, qb_samples = _modal_prior_qb(builder, team, season, week)
    expected_qb = current_qb or expected_base or "Unconfirmed"
    qb_confirmed = bool(current_qb)
    notes: list[str] = []
    if current_qb:
        notes.append(f"Covers last-game QB: {current_qb}")

    if modal_qb and modal_qb != current_qb:
        modal_injury = _injury_for(injuries, modal_qb)
        previous_status = _previous_injury_status(builder, team, modal_qb, season, week)
        if modal_injury is None and modal_count >= 2 and _severity(previous_status) >= 0.40:
            expected_qb, qb_confirmed = modal_qb, False
            notes.append(f"Return candidate: {modal_qb} was the starter in {modal_count} prior observations and is no longer on the injury list")
        elif modal_injury is not None:
            notes.append(f"Prior primary QB {modal_qb}: {modal_injury.get('status', '')}")

    expected_injury = _injury_for(injuries, expected_qb)
    if expected_injury and _severity(expected_injury.get("status", ""), expected_injury.get("detail", "")) >= 0.25:
        qb_confirmed = False
        notes.append(f"{expected_qb}: {expected_injury.get('status', '')}")

    base_continuity = float(getattr(base, "qb_continuity", 0.50) or 0.50)
    if qb_samples:
        stability = modal_count / max(1, qb_samples)
        hist_weight = 0.55 if qb_samples >= 3 else 0.45
        qb_continuity = hist_weight * stability + (1.0 - hist_weight) * base_continuity
        if current_qb and modal_qb and current_qb != modal_qb:
            qb_continuity *= 0.72
    else:
        qb_continuity = base_continuity
    qb_continuity = max(0.10, min(1.0, qb_continuity))

    starter_names = _starter_names(report)
    historical_names = _historical_starter_names(builder, team, season, week)
    qb_adj = ol_adj = skill_adj = dl_adj = lb_adj = sec_adj = kicker_adj = uncertainty = 0.0
    for injury in injuries:
        position = _clean_text(injury.get("position")).upper()
        player = _clean_text(injury.get("player"))
        if position not in POSITION_POINTS or not player:
            continue
        sev = _severity(injury.get("status", ""), injury.get("detail", ""))
        was_starter = any(_player_matches(player, name) for name in starter_names + historical_names)
        role_weight = 1.0 if was_starter else 0.35
        points = -POSITION_POINTS[position] * sev * role_weight
        uncertainty += (1.0 if position == "QB" else 0.4) * (1.0 - abs(sev - 0.5) * 2.0) * role_weight
        if position == "QB": qb_adj += points
        elif position in {"RB", "FB", "WR", "TE"}: skill_adj += points
        elif position in {"OT", "OG", "OL", "C"}: ol_adj += points
        elif position in {"DL", "DE", "DT", "NT"}: dl_adj += points
        elif position in {"LB", "ILB", "OLB"}: lb_adj += points
        elif position in {"CB", "S", "DB"}: sec_adj += points
        elif position == "K": kicker_adj += points

    if modal_qb and modal_qb != current_qb and modal_count >= 2:
        lost_qb = _injury_for(injuries, modal_qb)
        if lost_qb:
            qb_adj = min(qb_adj, -POSITION_POINTS["QB"] * _severity(lost_qb.get("status", ""), lost_qb.get("detail", "")))

    qb_adj = max(-5.0, min(0.0, qb_adj))
    skill_adj = max(-2.25, min(0.0, skill_adj))
    ol_adj = max(-1.50, min(0.0, ol_adj))
    dl_adj = max(-1.50, min(0.0, dl_adj))
    lb_adj = max(-1.20, min(0.0, lb_adj))
    sec_adj = max(-1.50, min(0.0, sec_adj))
    kicker_adj = max(-0.50, min(0.0, kicker_adj))

    availability = 88.0 if starters else 68.0
    availability -= min(18.0, uncertainty * 5.0)
    if expected_qb == "Unconfirmed":
        availability = min(availability, 55.0)
    if not qb_confirmed:
        availability = min(availability, 76.0)
    availability = max(35.0, min(96.0, availability))

    skill_notes = [f"{row.get('player')} {row.get('status')}" for row in injuries if str(row.get("position", "")).upper() in {"QB", "RB", "WR", "TE"}]
    if skill_notes:
        notes.append("Skill injuries: " + "; ".join(skill_notes[:6]))

    return builder.Personnel(
        expected_qb=expected_qb,
        qb_confirmed=qb_confirmed,
        qb_continuity=round(qb_continuity, 3),
        qb_adjustment=round(qb_adj, 3),
        ol_adjustment=round(ol_adj, 3),
        skill_adjustment=round(skill_adj, 3),
        dl_adjustment=round(dl_adj, 3),
        linebacker_adjustment=round(lb_adj, 3),
        secondary_adjustment=round(sec_adj, 3),
        kicker_adjustment=round(kicker_adj, 3),
        special_teams_adjustment=float(getattr(base, "special_teams_adjustment", 0.0) or 0.0),
        coaching_continuity=float(getattr(base, "coaching_continuity", 0.75) or 0.75),
        coordinator_continuity=float(getattr(base, "coordinator_continuity", 0.67) or 0.67),
        availability_confidence=round(availability, 1),
        source="Covers.com injuries + last-game starters",
        notes="; ".join(notes),
    )


def _prefetch(builder: Any, teams: list[str]) -> None:
    unique = list(dict.fromkeys(_clean_text(team) for team in teams if _clean_text(team)))
    if not unique:
        return
    with ThreadPoolExecutor(max_workers=min(6, len(unique)), thread_name_prefix="ezpz-covers-cfb") as pool:
        futures = [pool.submit(_team_report, builder, team) for team in unique]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                pass


def install_covers_layer(builder: Any, league: str = "ncaaf") -> None:
    """Install the shared Covers weather layer, plus CFB personnel overlays."""
    weather_league = "nfl" if _clean_text(league).lower() == "nfl" else "ncaaf"
    layer_key = "_EZPZ_NFL_COVERS_WEATHER_LAYER" if weather_league == "nfl" else "_EZPZ_CFB_COVERS_LAYER"
    if getattr(builder, layer_key, False):
        return

    builder.COVERS_STARTER_HISTORY_TAB = STARTER_HISTORY_TAB
    builder.COVERS_STARTER_HISTORY_COLUMNS = STARTER_HISTORY_COLUMNS
    builder.COVERS_INJURY_HISTORY_TAB = INJURY_HISTORY_TAB
    builder.COVERS_INJURY_HISTORY_COLUMNS = INJURY_HISTORY_COLUMNS

    original_default_personnel = builder.default_personnel
    original_build_environment = builder.build_environment
    original_run_week = builder.run_week
    original_incremental = builder._ensure_automatic_day_slate_incremental
    original_save_result = builder.save_result
    original_clear = builder._clear_automatic_state

    def default_personnel(team: str, rating: dict[str, Any], season: int, week: int, game_id: str, live_candidate: bool = True):
        base = original_default_personnel(team, rating, season, week, game_id, live_candidate)
        classification = _authoritative_classification(builder, team, rating)
        if classification == "fcs":
            try:
                # Covers does not reliably carry FCS depth charts/injuries. Keep
                # the FCS team's available ratings/roster/QB context, but do not
                # invent injury penalties. Lower confidence so reliability still
                # reflects the missing live personnel source.
                base.qb_adjustment = 0.0
                base.ol_adjustment = 0.0
                base.skill_adjustment = 0.0
                base.dl_adjustment = 0.0
                base.linebacker_adjustment = 0.0
                base.secondary_adjustment = 0.0
                base.kicker_adjustment = 0.0
                base.availability_confidence = min(
                    float(getattr(base, "availability_confidence", 45.0) or 45.0),
                    55.0,
                )
                prior_source = _clean_text(getattr(base, "source", ""))
                prior_notes = _clean_text(getattr(base, "notes", ""))
                base.source = f"{prior_source}; FCS opponent — Covers depth chart/injuries not required".strip("; ")
                base.notes = (
                    f"{prior_notes}; FCS Covers exemption: unknown injury effects set to zero; "
                    "available ratings/roster inputs retained."
                ).strip("; ")
                setattr(base, "_covers_personnel_ready", True)
                setattr(base, "_covers_personnel_exempt", True)
                setattr(base, "_covers_personnel_source", "")
            except Exception:
                pass
            return base
        try:
            setattr(base, "_covers_personnel_ready", False)
        except Exception:
            pass
        try:
            report = _team_report(builder, team)
            if not report.get("ok"):
                return base
            result = _personnel_overlay(builder, base, report, team, season, week)
            try:
                setattr(result, "_covers_personnel_ready", True)
                setattr(result, "_covers_personnel_source", str(report.get("url", "") or ""))
            except Exception:
                pass
            _buffer(builder, report, team, season, week, game_id)
            _flush_history(builder, force=False)
            return result
        except Exception:
            return base

    def build_environment(game: pd.Series, season: int, manual_roof: str | None = None):
        base = original_build_environment(game, season, manual_roof)
        try:
            setattr(base, "_covers_weather_ready", False)
        except Exception:
            pass
        try:
            away_team = str(game.get("Away Team", ""))
            home_team = str(game.get("Home Team", ""))
            card = _match_weather(builder, away_team, home_team) if weather_league == "ncaaf" else _match_weather(builder, away_team, home_team, weather_league)
            if not card:
                return base
            values = [float(card.get("temperature", math.nan)), float(card.get("wind", math.nan)), float(card.get("precipitation", math.nan))]
            if sum(math.isfinite(value) for value in values) < 2:
                return base
            temp = values[0] if math.isfinite(values[0]) else float(getattr(base, "temperature", math.nan))
            wind = values[1] if math.isfinite(values[1]) else float(getattr(base, "wind", math.nan))
            precip = values[2] if math.isfinite(values[2]) else float(getattr(base, "precipitation_probability", math.nan))
            if not all(math.isfinite(value) for value in (temp, wind, precip)):
                return base
            total_weather, home_weather, note = builder._weather_adjustment(temp, wind, precip, str(getattr(base, "roof", "")))
            non_weather_hfa = float(getattr(base, "home_field", 0.0)) - float(getattr(base, "weather_home_adjustment", 0.0))
            base.temperature = temp
            base.wind = wind
            base.precipitation_probability = precip
            base.weather_total_adjustment = total_weather
            base.weather_home_adjustment = home_weather
            base.home_field = round(non_weather_hfa + home_weather, 3)
            base.weather_confidence = max(float(getattr(base, "weather_confidence", 0.0)), 92.0)
            try:
                setattr(base, "_covers_weather_ready", True)
            except Exception:
                pass
            prior = _clean_text(getattr(base, "notes", ""))
            base.notes = f"{prior}; Covers weather: {note} ({temp:.1f}F, wind {wind:.1f} mph, POP {precip:.0%})".strip("; ")
        except Exception:
            pass
        return base

    def run_week(*args, **kwargs):
        schedule = kwargs.get("schedule")
        if not isinstance(schedule, pd.DataFrame):
            # run_week(season, week, provider, *, schedule=...) currently makes
            # schedule keyword-only, but keep this defensive for future refactors.
            schedule = None
        try:
            if isinstance(schedule, pd.DataFrame) and not schedule.empty:
                _prefetch(builder, list(schedule.get("Away Team", [])) + list(schedule.get("Home Team", [])))
        except Exception:
            pass
        result = original_run_week(*args, **kwargs)
        _flush_history(builder, force=False)
        return result

    def incremental(*args, **kwargs):
        result = original_incremental(*args, **kwargs)
        try:
            done, total = int(result[0]), int(result[1])
            if total and done >= total:
                _flush_history(builder, force=True)
        except Exception:
            pass
        return result

    def save_result(*args, **kwargs):
        result = original_save_result(*args, **kwargs)
        _flush_history(builder, force=True)
        return result

    def clear_automatic_state() -> None:
        original_clear()
        global _DIRECTORY
        with _LOCK:
            _MEMORY_HTML.clear()
            _TEAM_REPORTS.clear()
            _DIRECTORY = None
            _WEATHER.clear()
        _pending(builder, "starters").clear()
        _pending(builder, "injuries").clear()

    builder.build_environment = build_environment
    if weather_league == "ncaaf":
        builder.default_personnel = default_personnel
        builder.run_week = run_week
        builder._ensure_automatic_day_slate_incremental = incremental
        # Persist history on the explicit Save action, never on the runtime guard's
        # automatic projection helper. This preserves the CFB anti-quota behavior.
        builder.save_result = save_result
        builder._clear_automatic_state = clear_automatic_state
        builder._EZPZ_CFB_COVERS_LAYER = True
    else:
        original_clear_nfl = original_clear
        def clear_nfl_weather_state() -> None:
            original_clear_nfl()
            with _LOCK:
                _WEATHER.pop(weather_league, None)
        builder._clear_automatic_state = clear_nfl_weather_state
        setattr(builder, layer_key, True)


__all__ = [
    "install_covers_layer", "_parse_team_report", "_parse_weather_html",
    "_candidate_score", "_player_matches", "_severity",
]
