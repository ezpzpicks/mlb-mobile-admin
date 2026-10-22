"""Bounded ScoresAndOdds reads and insert-once NCAAF builder persistence.

No scraper or raw odds archive is used here. Model evaluation is read-only;
only the explicit atomic save can change permanent data.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import re
import unicodedata
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
AUTO_TAB = "ncaaf_auto_builder_runs"
ASSUMED_ODDS = -110
PRICE_NOTE = "Automated NCAAF: ScoresAndOdds lines; all spread/total prices assumed -110."
_ALIASES = {
    "appalachian state": "app state", "sam houston state": "sam houston",
    "louisiana lafayette": "louisiana", "uconn": "connecticut",
    "umass": "massachusetts", "ole miss": "mississippi",
    "nc state": "north carolina state", "usc": "southern california",
    "southern miss": "southern mississippi", "ucf": "central florida",
    "utep": "texas el paso", "utsa": "texas san antonio",
    "unlv": "nevada las vegas", "lsu": "louisiana state",
    "byu": "brigham young", "smu": "southern methodist", "tcu": "texas christian",
    "fiu": "florida international", "fau": "florida atlantic", "hawai i": "hawaii",
}


def team_key(value) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9]+", " ", value.lower().replace("&", "and")).strip()
    return _ALIASES.get(value, value)


def pair_key(row) -> tuple[str, str]:
    return team_key(row.get("Away Team")), team_key(row.get("Home Team"))


def timestamp(value) -> datetime | None:
    value = str(value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=ET) if parsed.tzinfo is None else parsed
    except ValueError:
        pass
    # The collector stores Intl.DateTimeFormat timestamps with EDT/EST suffixes.
    value = re.sub(r"\s+(?:EDT|EST|ET)$", "", value)
    for fmt in ("%m/%d/%Y, %I:%M:%S %p", "%m/%d/%Y, %I:%M %p"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=ET)
        except ValueError:
            continue
    return None


def number(value) -> float | None:
    try:
        parsed = float(str(value).strip().replace("−", "-"))
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class MarketGame:
    date: str
    away: str
    home: str
    kickoff: datetime
    captured: datetime
    home_spread: float
    total: float
    opening_home_spread: float | None
    opening_total: float | None
    source_key: str

    @property
    def pair(self):
        return team_key(self.away), team_key(self.home)

    def summary(self):
        return {"game": f"{self.away} @ {self.home}", "date": self.date,
                "kickoff": self.kickoff.astimezone(ET).isoformat(),
                "home_spread": self.home_spread, "total": self.total,
                "assumed_odds": ASSUMED_ODDS, "captured": self.captured.isoformat()}


def eligible_markets(rows, now: datetime, max_age_minutes: int = 90):
    """Require a complete, fresh, consistent pair of spread and total sides."""
    day = now.astimezone(ET).date().isoformat()
    groups = defaultdict(list)
    for row in rows:
        if (str(row.get("Date")) == day
                and str(row.get("Source", "")).lower() == "scoresandodds"
                and str(row.get("Snapshot Status", "")) in {"LIVE", "FINAL_PREGAME"}):
            groups[pair_key(row)].append(row)
    output, skipped = [], []
    for pair, sides in groups.items():
        label = f"{sides[0].get('Away Team')} @ {sides[0].get('Home Team')}"
        try:
            selected = {}
            for row in sides:
                market = str(row.get("Market", ""))
                side = team_key(row.get("Selection")) if market == "Spread" else str(row.get("Side") or row.get("Selection", "")).lower()
                key = (market, side)
                stamp = timestamp(row.get("Updated At"))
                previous = selected.get(key)
                if stamp and (not previous or stamp > timestamp(previous.get("Updated At"))):
                    selected[key] = row
            away = selected.get(("Spread", pair[0]))
            home = selected.get(("Spread", pair[1]))
            over, under = selected.get(("Total", "over")), selected.get(("Total", "under"))
            required = [away, home, over, under]
            if not pair[0] or not pair[1] or pair[0] == pair[1] or not all(required):
                raise ValueError("waiting for both spread sides and both total sides")
            lines = [number(row.get("Line")) for row in required]
            if any(line is None for line in lines) or abs(lines[0] + lines[1]) > 0.001 or abs(lines[2] - lines[3]) > 0.001 or lines[2] <= 0:
                raise ValueError("inconsistent or missing market lines")
            kickoffs = [timestamp(row.get("Game Time")) for row in required]
            if not all(kickoffs) or len({k.astimezone(timezone.utc) for k in kickoffs}) != 1:
                raise ValueError("kickoff is missing or inconsistent")
            kickoff = kickoffs[0]
            if kickoff <= now:
                raise ValueError("game has started")
            if kickoff.astimezone(ET).date().isoformat() != day:
                raise ValueError("game date differs from Eastern kickoff date")
            stamps = [timestamp(row.get("Updated At")) for row in required]
            if not all(stamps) or any(stamp > now + timedelta(minutes=5) or now - stamp > timedelta(minutes=max_age_minutes) for stamp in stamps):
                raise ValueError("market snapshot is stale or unverified")
            if max(stamps) - min(stamps) > timedelta(minutes=5):
                raise ValueError("market sides were captured at different times")
            output.append(MarketGame(day, away["Away Team"], away["Home Team"], kickoff,
                                     min(stamps), lines[1], lines[2], number(home.get("Opening Line")),
                                     number(over.get("Opening Line")), str(home.get("Game Key", ""))))
        except ValueError as exc:
            skipped.append({"game": label, "reason": str(exc)})
    return sorted(output, key=lambda game: game.kickoff), skipped


class Database:
    """Collect actual Turso row counters and block incidental model writes."""
    def __init__(self, turso):
        self.turso = turso
        self.original_pipeline = turso._pipeline
        self.rows_read = self.rows_written = self.statements = self.blocked_writes = 0
        self._write_scope = ContextVar("ncaaf_auto_builder_write_scope", default=False)
        turso._pipeline = self.pipeline

    @contextmanager
    def writes(self):
        token = self._write_scope.set(True)
        try:
            yield
        finally:
            self._write_scope.reset(token)

    def pipeline(self, requests, timeout=30.0, max_attempts=4):
        statements = [request.get("stmt", {}).get("sql", "").strip() for request in requests if request.get("type") == "execute"]
        if not self._write_scope.get() and any(not sql.upper().startswith(("SELECT ", "PRAGMA ", "EXPLAIN ")) for sql in statements):
            self.blocked_writes += 1
            raise RuntimeError("Scheduled model evaluation is read-only; use the explicit save transaction.")
        results = self.original_pipeline(requests, timeout=timeout, max_attempts=max_attempts)
        self.statements += len(statements)
        for result in results:
            counters = result.get("response", {}).get("result", {})
            self.rows_read += int(counters.get("rows_read", 0) or 0)
            self.rows_written += int(counters.get("rows_written", 0) or 0)
        return results

    def query(self, sql):
        result = self.pipeline([{"type": "execute", "stmt": {"sql": sql, "args": []}}])
        return self.turso._result_rows(result[0])

    def rows(self, dataset: str, day: str | None = None, compact=False):
        q = self.turso._sql_text
        column = "json_remove(payload_json,'$.\"Details JSON\"')" if compact else "payload_json"
        sql = f"SELECT {column} AS payload_json FROM dataset_rows WHERE sport='NCAAF' AND dataset={q(dataset)}"
        if day:
            sql += f" AND date_key={q(day)}"
        return [json.loads(row["payload_json"]) for row in self.query(sql)]

    def usage(self):
        return {"rows_read": self.rows_read, "rows_written": self.rows_written,
                "statements": self.statements, "blocked_incidental_writes": self.blocked_writes}


def match_schedule(market: MarketGame, rows):
    matches = []
    for row in rows:
        kickoff = timestamp(row.get("Game Time"))
        if pair_key(row) != market.pair or not kickoff:
            continue
        if kickoff.astimezone(ET).date().isoformat() != market.date:
            continue
        if str(row.get("Completed", "")).lower() in {"1", "true", "yes"}:
            continue
        if abs((kickoff - market.kickoff).total_seconds()) > 30 * 60:
            continue
        if row.get("Game ID") and number(row.get("Season")) and number(row.get("Week")):
            matches.append(row)
    ids = {str(row["Game ID"]) for row in matches}
    if len(ids) != 1:
        raise ValueError("no unique verified schedule matchup")
    return matches[-1]


def protected_matchup(market, schedule, protected_rows):
    game_id = str(schedule.get("Game ID", ""))
    for row in protected_rows:
        if game_id and str(row.get("Game ID", "")) == game_id:
            return True
        if str(row.get("Date", "")) == market.date and pair_key(row) == market.pair:
            return True
    return False


def save_once(db: Database, builder, result, market: MarketGame, now=None):
    """Atomically protect saved/manual games, then save only this matchup's rows."""
    from shared.cfb_auto_persistence import save_once_transaction
    return save_once_transaction(db, builder, result, market, now=now)
