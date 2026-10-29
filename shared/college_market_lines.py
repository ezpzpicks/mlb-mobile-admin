"""College-only market lines. Covers consensus percentages are never imported.

One current-date scoreboard request supplies all missing matchups. These quotes
are model inputs, never public betting split rows or historical line movement.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import math
import re

from shared.ncaaf_auto_builder import (
    ET, MarketGame, eligible_markets, pair_key, protected_matchup, team_key, timestamp,
)

_LEAGUES = {"NCAAF": "ncaaf", "CFB": "ncaaf", "NCAAB": "ncaab",
            "NCAAM": "ncaab", "CBB": "ncaab"}
MAX_AGE_MINUTES = 90


def covers_league(sport):
    try:
        return _LEAGUES[str(sport).upper()]
    except KeyError:
        raise ValueError("Covers model-line fallback is restricted to NCAAF and NCAAB.") from None


def _spread(text):
    text = text.strip().replace("−", "-")
    if text.lower() in {"pk", "pick", "pick'em", "pick’em"}:
        return 0.0
    if not re.fullmatch(r"[+-]\d+(?:\.\d+)?", text):
        raise ValueError("missing or ambiguous spread")
    value = float(text)
    if not math.isfinite(value) or abs(value) > 100:
        raise ValueError("invalid spread")
    return value


def parse_covers_markets(html, sport, now, *, captured_at=None):
    """Read complete pregame cards; reject stale, partial or ambiguous quotes."""
    from bs4 import BeautifulSoup

    league = covers_league(sport)
    local = now.astimezone(ET)
    captured = captured_at or now
    if captured.tzinfo is None or captured > now + timedelta(minutes=5) or now - captured > timedelta(minutes=MAX_AGE_MINUTES):
        raise ValueError("Covers page is stale or has an invalid observation time.")
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one("#challenge-form, #cf-challenge-running"):
        raise ValueError("Covers returned a challenge instead of market data.")
    found, rejected, ambiguous = {}, [], set()
    for card in soup.select(f"article.pregamebox[id^='{league}-']"):
        label = str(card.get("id", ""))
        try:
            heading = card.select_one(".gamebox-header strong")
            label = heading.get_text(" ", strip=True) if heading else label
            away, home = label.split(" @ ")
            # Headlines use ambiguous display abbreviations such as JSU. The
            # linked team logos carry full names (Jacksonville State), even when
            # unquoted data-team attributes are truncated by the HTML parser.
            names = [logo.get("alt", "").strip()
                     for logo in card.select(".gamebox-team-anchor img[alt]")]
            if len(names) == 2 and all(names):
                away, home = names
            # The neutral-site marker and rankings are not part of team identity.
            clean = lambda name: re.sub(r"\s*\((?:N|\d+)\)\s*", " ", name).strip()
            away, home = clean(away), clean(home)
            if not away or not home or team_key(away) == team_key(home):
                raise ValueError("missing or ambiguous teams")
            date_node = card.select_one(".preGame-status span")
            time_node = card.select_one(".preGame-time")
            if not date_node or not time_node:
                raise ValueError("missing kickoff")
            day = datetime.strptime(date_node.get_text(" ", strip=True) + f" {local.year}", "%A, %b %d %Y").date()
            if day != local.date():
                continue
            clock = time_node.get_text(" ", strip=True)
            if not clock.endswith(" ET"):
                raise ValueError("kickoff timezone is unverified")
            kickoff = datetime.strptime(f"{day.isoformat()} {clock[:-3]}", "%Y-%m-%d %I:%M %p").replace(tzinfo=ET)
            if kickoff <= now:
                continue
            odds = card.select_one(".team_odds_and_consensus")
            sides = odds.select(":scope > .team-consensus") if odds else []
            total_node = odds.select_one(":scope > .team-overunder") if odds else None
            if len(sides) != 2 or not total_node:
                raise ValueError("waiting for both spreads and a total")
            # Only direct text nodes are market lines. Nested strong tags are
            # Covers user-consensus votes, not sportsbook ticket/money splits.
            away_spread, home_spread = [_spread("".join(side.find_all(string=True, recursive=False))) for side in sides]
            total_match = re.fullmatch(r"o/u\s+(\d+(?:\.\d+)?)", total_node.get_text(" ", strip=True), re.I)
            if abs(away_spread + home_spread) > 0.001 or not total_match:
                raise ValueError("inconsistent spread pair or total")
            total = float(total_match.group(1))
            if not 0 < total < 300:
                raise ValueError("invalid total")
            game_id = card["id"].removeprefix(league + "-")
            if not game_id.isdigit():
                raise ValueError("invalid Covers game ID")
            kind = "football/ncaaf" if league == "ncaaf" else "basketball/ncaab"
            market = MarketGame(day.isoformat(), away, home, kickoff, captured,
                                home_spread, total, None, None, f"covers:{league}:{game_id}",
                                "Covers", f"https://www.covers.com/sport/{kind}/matchup/{game_id}")
            if market.pair in found:
                ambiguous.add(market.pair)
            found[market.pair] = market
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append({"game": label, "reason": str(exc)})
    for pair in ambiguous:
        market = found.pop(pair)
        rejected.append({"game": f"{market.away} @ {market.home}", "reason": "duplicate Covers matchup"})
    return sorted(found.values(), key=lambda market: market.kickoff), rejected


def fetch_covers_markets(sport, now):
    """Fetch once per slate, with bounded time/size and no stale-cache fallback."""
    import requests

    league = covers_league(sport)  # Reject professional sports before any I/O.
    day = now.astimezone(ET).date().isoformat()
    url = f"https://www.covers.com/sports/{league}/matchups?selectedDate={day}"
    response = requests.get(url, timeout=(8, 16), headers={
        "User-Agent": "Mozilla/5.0 (compatible; EZPZPicks/1.0)",
        "Accept": "text/html", "Cache-Control": "no-cache",
    })
    response.raise_for_status()
    if len(response.content) > 5_000_000:
        raise ValueError("Covers scoreboard exceeds the expected size.")
    observed = datetime.now(timezone.utc)
    age = float(response.headers.get("Age", "0"))
    if not math.isfinite(age) or age < 0:
        raise ValueError("Invalid Covers cache age.")
    captured = observed - timedelta(seconds=age)
    if response.headers.get("Date"):
        server_time = parsedate_to_datetime(response.headers["Date"])
        if server_time.tzinfo is None or server_time > observed + timedelta(minutes=5):
            raise ValueError("Invalid Covers response timestamp.")
        captured = min(captured, server_time)
    return parse_covers_markets(response.text, sport, now, captured_at=captured)


def missing_schedule_games(markets, schedule, protected, now):
    """Find unsaved scheduled NCAAF games before requesting fallback lines."""
    day = now.astimezone(ET).date().isoformat()
    covered = {market.pair for market in markets}
    missing = []
    for row in schedule:
        kickoff = timestamp(row.get("Game Time"))
        if not kickoff or kickoff.astimezone(ET).date().isoformat() != day:
            continue
        local = kickoff.astimezone(ET)
        placeholder = (local.hour, local.minute, local.second, local.microsecond) == (0, 0, 0, 0)
        if (not placeholder and kickoff <= now) or str(row.get("Completed", "")).lower() in {"1", "true", "yes"}:
            continue
        if not row.get("Game ID") or not row.get("Season") or not row.get("Week") or not all(pair_key(row)):
            continue
        shell = MarketGame(day, row["Away Team"], row["Home Team"], kickoff, now, 0, 0, None, None, "")
        if shell.pair not in covered and not protected_matchup(shell, row, protected):
            missing.append(row)
    return missing


def find_college_market(sport, rows, away, home, now, *, covers_loader=None):
    """Manual college builder: use retained SAO first, then a Covers quote."""
    covers_league(sport)
    pair = team_key(away), team_key(home)
    markets, _ = eligible_markets(rows, now)
    matches = [market for market in markets if market.pair == pair]
    if not matches:
        markets, _ = (covers_loader or fetch_covers_markets)(sport, now)
        matches = [market for market in markets if market.pair == pair]
    if len(matches) != 1:
        raise ValueError("No complete, current pregame spread and total found for this matchup.")
    return matches[0]
