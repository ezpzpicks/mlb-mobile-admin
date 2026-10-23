"""Read the public RotoWire NFL odds tables, without executing their JavaScript.

Only the public comparison tables are consumed. Subscriber projections, grades,
downloads and authenticated endpoints are not used.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

URL = "https://www.rotowire.com/betting/nfl/player-props.php"
INACTIVES_URL = "https://www.rotowire.com/football/inactives.php"
MAX_BYTES = 12_000_000
BOOKS = ("draftkings", "fanduel", "hardrock", "mgm", "caesars", "betrivers", "thescore")
MARKETS = {"passyds": "Passing Yards", "rushyds": "Rushing Yards", "recyds": "Receiving Yards",
           "recs": "Receptions", "passatt": "Passing Attempts", "comp": "Passing Completions",
           "passtd": "Passing TDs", "intsthrown": "Interceptions", "anytd": "Anytime TD"}


class SourceError(ValueError):
    pass


def name_key(value):
    import unicodedata
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+(?:jr|sr|ii|iii|iv)$", "", " ".join(text.split()))


def team_key(value):
    from builders.nfl_covers import NFL_TEAM_NAMES, _norm
    raw = str(value or "").strip().upper()
    direct = {"ARZ": "ARI", "JAC": "JAX", "WSH": "WAS", "LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"}.get(raw, raw)
    if direct in NFL_TEAM_NAMES:
        return direct
    normalized = _norm(value)
    matches = [code for code, aliases in NFL_TEAM_NAMES.items() if normalized in {_norm(a) for a in aliases}
        or normalized in {_norm(code + " " + a) for a in aliases}]
    if len(matches) != 1:
        raise SourceError(f"Unrecognized or ambiguous NFL team: {value}")
    return matches[0]


def read_public_html(url):
    try:
        with urlopen(Request(url, headers={"User-Agent": "EZPZ-NFL-Props/1.0", "Accept": "text/html"}), timeout=25) as r:
            raw = r.read(MAX_BYTES + 1)
            status = r.status
        if len(raw) > MAX_BYTES:
            raise SourceError("RotoWire NFL page exceeds the size limit.")
        return raw.decode("utf-8"), status
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError(f"RotoWire public HTML unavailable ({type(exc).__name__}: {exc}).") from None


def _number(value, *, odds=False):
    if value in (None, ""):
        return None
    try:
        value = float(str(value).replace("−", "-"))
    except ValueError:
        raise SourceError("Invalid numeric prop quote.") from None
    if not math.isfinite(value) or (odds and (abs(value) < 100 or value != int(value))):
        raise SourceError("Invalid prop price.")
    return int(value) if odds else value


def parse_props_html(html, *, observed_at=None):
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one("h1")
    match = re.search(r"\bWeek\s+(\d{1,2})\b", heading.get_text(" ", strip=True) if heading else "", re.I)
    if not match or "NFL" not in heading.get_text():
        raise SourceError("RotoWire NFL week heading is missing; no odds applied.")
    week = int(match[1])
    quotes, seen, rejected = [], set(), []
    for script in soup.select("script"):
        text = script.get_text()
        code = re.search(r'const prop\s*=\s*"([a-z]+)"\s*;', text)
        if not code or code[1] not in MARKETS:
            continue
        code = code[1]
        source_week = re.search(r'const dayNFL\s*=\s*"(\d+)"', text)
        start = re.search(r"const settings\s*=\s*\{\s*container:\s*propID,\s*data:\s*", text)
        if not source_week or int(source_week[1]) != week or not start:
            raise SourceError("RotoWire comparison table has inconsistent week or layout.")
        try:
            rows, _ = json.JSONDecoder().raw_decode(text[start.end():])
        except (ValueError, TypeError):
            raise SourceError("RotoWire comparison-table data is malformed.") from None
        if not isinstance(rows, list) or len(rows) > 2000:
            raise SourceError("RotoWire comparison-table row count is invalid.")
        for row in rows:
            try:
                if not isinstance(row, dict):
                    raise SourceError("Invalid player row.")
                name, gid, pid = (str(row.get(k, "")).strip() for k in ("name", "gameID", "playerID"))
                if len(name.split()) < 2 or not gid.isdigit() or not pid.isdigit():
                    raise SourceError("Player full name or source identity is missing.")
                team, opp = team_key(row.get("team")), str(row.get("opp", "")).strip()
                away, home = (team, team_key(opp[1:])) if opp.startswith("@") else (team_key(opp), team)
                if away == home:
                    raise SourceError("A player cannot face his own team.")
                identity = (code, gid, team, name_key(name))
                if identity in seen:
                    raise SourceError("Duplicate player/market identity.")
                seen.add(identity)
                for book in BOOKS:
                    value = _number(row.get(f"{book}_{code}"), odds=code == "anytd")
                    if value is None:
                        continue
                    over = value if code == "anytd" else _number(row.get(f"{book}_{code}Over"), odds=True)
                    under = None if code == "anytd" else _number(row.get(f"{book}_{code}Under"), odds=True)
                    line = 0.5 if code == "anytd" else value
                    if line < 0 or (code != "anytd" and (over is None or under is None)):
                        continue
                    quotes.append({"player": name, "player_id": f"rotowire:{pid}", "source_game_id": gid,
                        "team": team, "away": away, "home": home, "market": MARKETS[code],
                        "book": book, "line": line, "over": over, "under": under})
            except SourceError as exc:
                rejected.append({"market": MARKETS[code], "player": str(row.get("name", "")) if isinstance(row, dict) else "", "reason": str(exc)})
                # Duplicated identities must not leave an earlier quote usable.
                if "Duplicate" in str(exc):
                    quotes = [q for q in quotes if not (q["market"] == MARKETS[code] and q["source_game_id"] == gid and q["team"] == team and name_key(q["player"]) == name_key(name))]
    if not quotes:
        raise SourceError("RotoWire public comparison tables contain no valid prices.")
    # The same source matchup must never be assigned to two distinct game IDs.
    pairs = {}
    for quote in quotes:
        pair = quote["away"], quote["home"]
        pairs.setdefault(pair, set()).add(quote["source_game_id"])
    bad = {pair for pair, ids in pairs.items() if len(ids) != 1}
    quotes = [q for q in quotes if (q["away"], q["home"]) not in bad]
    return {"week": week, "observed_at": (observed_at or datetime.now(timezone.utc)).isoformat(),
            "source": URL, "source_sha256": hashlib.sha256(html.encode()).hexdigest(), "quotes": quotes,
            "rejected": rejected, "ambiguous_games": sorted(bad)}


def fetch_props(*, on_event=None):
    html, status = read_public_html(URL)
    report = parse_props_html(html)
    if on_event:
        on_event("rotowire_props_read", http_status=status, week=report["week"], quotes=len(report["quotes"]),
                 games=len({q["source_game_id"] for q in report["quotes"]}), rejected=len(report["rejected"]), source_sha256=report["source_sha256"])
    return report


def apply_quotes(frame, game, report, *, now, books=BOOKS):
    import numpy as np
    observed = datetime.fromisoformat(report["observed_at"])
    if not timedelta(0) <= now - observed <= timedelta(minutes=30):
        raise SourceError("Refresh the RotoWire prop snapshot before building.")
    if int(game["Week"]) != report["week"]:
        raise SourceError("RotoWire props belong to a different NFL week.")
    away, home = team_key(game["Away Team"]), team_key(game["Home Team"])
    output = frame.copy()
    for col in ("Market Line", "Over Odds", "Under Odds"):
        output[col] = np.nan
    output["Line Source"] = ""
    matched = []
    for index, row in output.iterrows():
        market, slot = str(row["Market"]), str(row.get("Slot", ""))
        # Preserve the admin's published market surface. Count projections remain
        # descriptive until their wager markets are explicitly enabled.
        eligible = (market == "Anytime TD" or (market == "Passing Yards" and slot == "QB") or
                    (market == "Rushing Yards" and slot.startswith("RB")) or
                    (market == "Receiving Yards" and (slot.startswith(("RB", "WR")) or slot == "TE1")))
        if not eligible:
            continue
        candidates = [q for q in report["quotes"] if q["away"] == away and q["home"] == home
            and q["team"] == team_key(row["Team"]) and q["market"] == market and name_key(q["player"]) == name_key(row["Player"])]
        ids = {(q["player_id"], q["source_game_id"]) for q in candidates}
        if len(ids) > 1:
            raise SourceError(f"Ambiguous RotoWire identity for {row['Player']}.")
        selected = next((q for book in books for q in candidates if q["book"] == book), None)
        if not selected:
            continue
        output.at[index, "Market Line"] = selected["line"]
        output.at[index, "Over Odds"] = selected["over"]
        if selected["under"] is not None:
            output.at[index, "Under Odds"] = selected["under"]
        output.at[index, "Line Source"] = f"RotoWire public HTML / {selected['book']} / {report['observed_at']}"
        matched.append(selected)
    if not matched:
        raise SourceError("No RotoWire prop quotes uniquely match this game's modeled players.")
    return output, matched


def fetch_inactives(*, on_event=None):
    html, status = read_public_html(INACTIVES_URL)
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one(".page-title")
    year = re.search(r"\b(20\d{2})\b", title.get_text(" ", strip=True) if title else "")
    main = soup.select_one("main")
    if main is None or year is None:
        raise SourceError("RotoWire inactive-list layout or season is missing.")
    teams = {}
    for box in main.select(".col"):
        link = box.select_one('a[href*="/nfl-depth-charts/"]')
        if link is None:
            continue
        team = team_key(link.get_text(" ", strip=True))
        players = [a.get_text(" ", strip=True) for a in box.select('li a[href*="/football/player/"]')]
        if not 3 <= len(players) <= 10 or len({name_key(p) for p in players}) != len(players) or team in teams:
            raise SourceError("RotoWire inactive list is incomplete or duplicated.")
        teams[team] = players
    report = {"season": int(year[1]), "teams": teams, "observed_at": datetime.now(timezone.utc).isoformat(),
              "source": INACTIVES_URL, "source_sha256": hashlib.sha256(html.encode()).hexdigest()}
    if on_event:
        on_event("rotowire_inactives_read", http_status=status, teams=sorted(teams))
    return report
