"""Current ESPN NCAAM model lines; never public betting split observations."""
from __future__ import annotations

from datetime import timedelta
from email.utils import parsedate_to_datetime
import math
import re

from builders.ncaam_model import timestamp


def response_observed_at(headers, received_at):
    """Keep HTTP cache age instead of relabeling an old response as fresh."""
    age = float(headers.get("Age", "0"))
    if not math.isfinite(age) or age < 0:
        raise ValueError("Invalid ESPN cache age.")
    observed = received_at - timedelta(seconds=age)
    if headers.get("Date"):
        server = parsedate_to_datetime(headers["Date"])
        if server.tzinfo is None or server > received_at + timedelta(minutes=5):
            raise ValueError("Invalid ESPN response timestamp.")
        observed = min(observed, server)
    return observed


def _number(value):
    if isinstance(value, bool) or value is None or not str(value).strip():
        raise ValueError("Missing ESPN market line.")
    value = float(str(value).replace("−", "-"))
    if not math.isfinite(value):
        raise ValueError("Invalid ESPN market line.")
    return value


def _total(value, side):
    match = re.fullmatch(rf"{side}\s*(\d+(?:\.\d+)?)", str(value or ""), re.I)
    if not match:
        raise ValueError("Missing current ESPN total side.")
    return _number(match[1])


def _quote(odds, game):
    provider = odds.get("provider", {})
    if not str(provider.get("id", "")).strip() or not str(provider.get("name", "")).strip():
        raise ValueError("ESPN bookmaker is unidentified.")
    for side in ("away", "home"):
        source_id = odds.get(side + "TeamOdds", {}).get("team", {}).get("id")
        if source_id is not None and str(source_id) != game[side + "_id"]:
            raise ValueError("ESPN odds team identity conflicts with the schedule.")
    if "pointSpread" in odds or "total" in odds:
        # ESPN calls its current pregame quote 'close'. Never replace a missing
        # current side with an opening quote or a side from a different book.
        spreads, totals = odds.get("pointSpread", {}), odds.get("total", {})
        home = _number(spreads.get("home", {}).get("close", {}).get("line"))
        away = _number(spreads.get("away", {}).get("close", {}).get("line"))
        over = _total(totals.get("over", {}).get("close", {}).get("line"), "o")
        under = _total(totals.get("under", {}).get("close", {}).get("line"), "u")
        if abs(home + away) > .001 or abs(over - under) > .001:
            raise ValueError("ESPN market sides are inconsistent.")
        if "overUnder" in odds and abs(_number(odds["overUnder"]) - over) > .001:
            raise ValueError("ESPN total summary conflicts with its current quote.")
        total = over
    else:
        # Older scoreboard responses have a favorite magnitude and total.
        # Favorite flags bind the sign to the home team, including road favorites.
        spread, total = _number(odds.get("spread")), _number(odds.get("overUnder"))
        if spread == 0:
            home = 0.0
        else:
            h = odds.get("homeTeamOdds", {}).get("favorite")
            a = odds.get("awayTeamOdds", {}).get("favorite")
            if type(h) is not bool or type(a) is not bool or h == a:
                raise ValueError("ESPN favorite orientation is ambiguous.")
            home = -abs(spread) if h else abs(spread)
    if abs(home) > 100 or not 50 < total < 300:
        raise ValueError("ESPN spread or total is out of range.")
    priority = provider.get("priority", 9999)
    priority = _number(priority)
    return home, total, str(provider["id"]), str(provider["name"]), priority


def parse_espn_ncaam_lines(payload, games, now, observed_at):
    """Reuse the verified scoreboard: stable event/team IDs, one complete book."""
    if observed_at.tzinfo is None or not observed_at <= now or now - observed_at > timedelta(minutes=90):
        raise ValueError("ESPN market observation is stale or future-dated.")
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("ESPN events are missing.")
    output, rejected = {}, []
    for game in games:
        if game["status"] != "STATUS_SCHEDULED" or timestamp(game["tipoff"]) <= now:
            continue
        try:
            matching = [e for e in events if str(e.get("id")) == game["game_id"]]
            if len(matching) != 1 or len(matching[0].get("competitions", [])) != 1:
                raise ValueError("ESPN market event identity is ambiguous.")
            comp = matching[0]["competitions"][0]
            sides = {p.get("homeAway"): str(p.get("team", {}).get("id", "")) for p in comp.get("competitors", [])}
            if sides != {"away": game["away_id"], "home": game["home_id"]} or timestamp(comp.get("date")) != timestamp(game["tipoff"]):
                raise ValueError("ESPN odds do not match the verified game.")
            quotes = []
            for odds in comp.get("odds", []):
                try:
                    quotes.append(_quote(odds, game))
                except (ValueError, TypeError, AttributeError):
                    continue
            if not quotes:
                raise ValueError("ESPN has no complete current spread and total.")
            books = {}
            for quote in quotes:
                if quote[2] in books and quote[:2] != books[quote[2]][:2]:
                    raise ValueError("ESPN has conflicting quotes from the same bookmaker.")
                books[quote[2]] = quote
            home, total, book_id, book_name, _ = min(books.values(), key=lambda q: (q[4], q[2]))
            output[game["game_id"]] = {"home_spread": home, "total": total, "as_of": observed_at.isoformat(),
                "source": "ESPN", "source_url": f"https://www.espn.com/mens-college-basketball/game/_/gameId/{game['game_id']}",
                "provider_id": book_id, "provider": book_name, "quote_kind": "current_pregame", "odds": -110}
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            rejected.append({"game_id": game["game_id"], "reason": str(exc)})
    return output, rejected
