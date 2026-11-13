"""Published SP+ inputs, with complete-population and observation-time checks.

These are Bill Connelly's ratings as published by CFB Update, not the legacy
free-data dispatcher's similarly named SRS/EPA proxy fields. A fetched population
may be used only for decisions made after it was observed. It is never a source
of historical pregame ratings for a retrospective build.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import unicodedata
from urllib.request import Request, urlopen

SOURCE_URL = "https://pay.cfbupdate.com/sp-ratings"
SOURCE_NAME = "Bill Connelly SP+ via CFB Update"
CACHE_SECONDS = 21600
MAX_SNAPSHOT_AGE_SECONDS = 8 * 86400
_MEMORY: dict[int, dict] = {}
_RETRY_AFTER: dict[int, datetime] = {}


def stamp(value):
    d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("An observation timestamp must include its timezone")
    return d.astimezone(timezone.utc)


def team_key(value):
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    value = re.sub(r"[^a-z0-9]+", " ", value.replace("&", "and")).strip()
    aliases = {"miami oh": "miami ohio", "miami fl": "miami", "app state": "appalachian state",
        "hawai i": "hawaii", "ul monroe": "louisiana monroe", "ole miss": "mississippi",
        "uconn": "connecticut", "umass": "massachusetts", "lsu": "louisiana state",
        "usc": "southern california", "ucf": "central florida", "byu": "brigham young",
        "tcu": "texas christian", "smu": "southern methodist", "utsa": "texas san antonio",
        "utep": "texas el paso", "unlv": "nevada las vegas", "nc state": "north carolina state",
        "fiu": "florida international", "fau": "florida atlantic"}
    return aliases.get(value, value)


class _Table(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        if tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join(" ".join(self.cell).split()))
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def parse_population(html, season, observed_at):
    observed = stamp(observed_at)
    # The selected dataset must declare the requested season, not merely list
    # that year somewhere in a navigation menu.
    heading = re.search(r'"temporalCoverage"\s*:\s*"(\d{4})"', html)
    if heading is None or int(heading[1]) != int(season):
        raise ValueError("Published SP+ season does not match the requested season")
    table = _Table()
    table.feed(html)
    header = ["Team", "SP+ Rating", "Offense", "Defense", "Special Teams"]
    active, teams = False, {}
    for row in table.rows:
        if row == header:
            active = True
            continue
        if not active or len(row) != 5:
            continue
        m = re.fullmatch(r"\d+\.\s+(.+?)\s+\(\d+[-–]\d+(?:[-–]\d+)?\)", row[0])
        if not m:
            continue  # Ads are not data rows.
        values = []
        for cell in row[1:]:
            v = re.fullmatch(r"([+−-]?\d+(?:\.\d+)?)\s*(?:\(\d+\))?", cell)
            if not v:
                raise ValueError("Malformed SP+ rating value")
            values.append(float(v[1].replace("−", "-")))
        overall, offense, defense, special = values
        if not (-70 < overall < 70 and 0 <= offense < 90 and 0 <= defense < 90 and abs(special) < 15):
            raise ValueError("SP+ rating outside its expected scale")
        key = team_key(m[1])
        if key in teams:
            raise ValueError("Duplicate SP+ team")
        teams[key] = {"team": m[1], "offense": offense, "defense": defense,
                      "overall": overall, "special_teams": special}
    if not 125 <= len(teams) <= 150:
        raise ValueError(f"Incomplete SP+ population: {len(teams)} teams")
    update = re.search(r"Updated\s+([^<]{1,45})</span>", html, re.I)
    rating_hash = hashlib.sha256(json.dumps(teams, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"source": SOURCE_NAME, "source_url": SOURCE_URL, "season": int(season),
            "observed_at": observed.isoformat(), "population_sha256": rating_hash,
            "source_update_label": update[1].strip() if update else "Publication time unavailable",
            "teams": teams}


def usable(population, season, as_of, max_age=MAX_SNAPSHOT_AGE_SECONDS):
    try:
        age = (stamp(as_of) - stamp(population["observed_at"])).total_seconds()
        return (population["season"] == int(season) and 0 <= age <= max_age
                and 125 <= len(population["teams"]) <= 150)
    except (KeyError, TypeError, ValueError):
        return False


def load_population(season, *, as_of=None, cache_dir=None):
    now = datetime.now(timezone.utc)
    decision = as_of or now.isoformat()
    cached = _MEMORY.get(int(season))
    if cached and usable(cached, season, decision, CACHE_SECONDS):
        return cached
    directory = Path(cache_dir or os.getenv("EZPZ_CFB_CACHE_DIR", "/tmp/ezpz_cfb_cache"))
    path = directory / f"published_sp_{int(season)}.json"
    try:
        cached = json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    if cached and usable(cached, season, decision, CACHE_SECONDS):
        _MEMORY[int(season)] = cached
        return cached
    # Fetching today's ratings cannot reconstruct a past decision.
    if (now - stamp(decision)).total_seconds() > 300:
        if cached and usable(cached, season, decision):
            return cached
        raise ValueError("No observed pregame SP+ snapshot exists for this historical decision")
    if _RETRY_AFTER.get(int(season), now) > now:
        if cached and usable(cached, season, decision):
            return cached
        raise ValueError("Published SP+ source temporarily unavailable; retrying on the next build")
    try:
        request = Request(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; EZPZ-Picks/1.0)"})
        with urlopen(request, timeout=20) as response:
            html = response.read(4_000_001)
        if len(html) > 4_000_000:
            raise ValueError("SP+ response unexpectedly large")
        captured = datetime.now(timezone.utc).isoformat()
        population = parse_population(html.decode("utf-8"), season, captured)
        # The caller freezes its decision after input collection. The returned
        # observed_at is never backdated to the start of a network request.
        directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(population, separators=(",", ":"), allow_nan=False))
        temporary.replace(path)
        _MEMORY[int(season)] = population
        return population
    except Exception:
        from datetime import timedelta
        _RETRY_AFTER[int(season)] = datetime.now(timezone.utc) + timedelta(minutes=2)
        if cached and usable(cached, season, datetime.now(timezone.utc).isoformat()):
            return cached
        raise
