"""NFL-specific Covers.com weather integration for the EZPZ NFL builder.

This module is intentionally independent from the CFB Covers personnel/weather
integration. It only supplies NFL game weather defaults from Covers and leaves
all NFL personnel, ratings, regression, and manual sportsbook logic untouched.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import json
import math
import re
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

COVERS_NFL_WEATHER_URL = "https://www.covers.com/sport/football/nfl/weather"
WEATHER_TTL = 30 * 60
STALE_MAX_AGE = 48 * 60 * 60
CACHE_DIR = Path("/tmp/ezpz_nfl_covers_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = CACHE_DIR / "weather.json"

_LOCK = threading.RLock()
_MEMORY_WEATHER: tuple[float, list[dict[str, Any]]] | None = None

NFL_TEAM_NAMES: dict[str, tuple[str, ...]] = {
    "ARI": ("ari", "arizona", "cardinals", "arizona cardinals"),
    "ATL": ("atl", "atlanta", "falcons", "atlanta falcons"),
    "BAL": ("bal", "baltimore", "ravens", "baltimore ravens"),
    "BUF": ("buf", "buffalo", "bills", "buffalo bills"),
    "CAR": ("car", "carolina", "panthers", "carolina panthers"),
    "CHI": ("chi", "chicago", "bears", "chicago bears"),
    "CIN": ("cin", "cincinnati", "bengals", "cincinnati bengals"),
    "CLE": ("cle", "cleveland", "browns", "cleveland browns"),
    "DAL": ("dal", "dallas", "cowboys", "dallas cowboys"),
    "DEN": ("den", "denver", "broncos", "denver broncos"),
    "DET": ("det", "detroit", "lions", "detroit lions"),
    "GB": ("gb", "green bay", "packers", "green bay packers"),
    "HOU": ("hou", "houston", "texans", "houston texans"),
    "IND": ("ind", "indianapolis", "colts", "indianapolis colts"),
    "JAX": ("jax", "jac", "jacksonville", "jaguars", "jacksonville jaguars"),
    "KC": ("kc", "kansas city", "chiefs", "kansas city chiefs"),
    "LAC": ("lac", "la chargers", "los angeles chargers", "chargers"),
    "LAR": ("lar", "la rams", "los angeles rams", "rams"),
    "LV": ("lv", "las vegas", "raiders", "las vegas raiders"),
    "MIA": ("mia", "miami", "dolphins", "miami dolphins"),
    "MIN": ("min", "minnesota", "vikings", "minnesota vikings"),
    "NE": ("ne", "new england", "patriots", "new england patriots"),
    "NO": ("no", "new orleans", "saints", "new orleans saints"),
    "NYG": ("nyg", "ny giants", "new york giants", "giants"),
    "NYJ": ("nyj", "ny jets", "new york jets", "jets"),
    "PHI": ("phi", "philadelphia", "eagles", "philadelphia eagles"),
    "PIT": ("pit", "pittsburgh", "steelers", "pittsburgh steelers"),
    "SEA": ("sea", "seattle", "seahawks", "seattle seahawks"),
    "SF": ("sf", "san francisco", "49ers", "san francisco 49ers", "niners"),
    "TB": ("tb", "tampa bay", "buccaneers", "bucs", "tampa bay buccaneers"),
    "TEN": ("ten", "tennessee", "titans", "tennessee titans"),
    "WAS": ("was", "wsh", "washington", "commanders", "washington commanders"),
}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _norm(value: Any) -> str:
    text = _clean_text(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _candidate_score(query: str, candidate: str) -> float:
    q, c = _norm(query), _norm(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    qset, cset = set(q.split()), set(c.split())
    if qset.issubset(cset):
        return 0.96
    if cset.issubset(qset):
        return 0.93
    overlap = len(qset & cset) / max(1, len(qset | cset))
    sequence = SequenceMatcher(None, q, c).ratio()
    return 0.58 * sequence + 0.42 * overlap


def _team_code(value: Any) -> str:
    normalized = _norm(value)
    if not normalized:
        return ""
    upper = _clean_text(value).upper()
    direct = {"ARZ": "ARI", "JAC": "JAX", "WSH": "WAS"}.get(upper, upper)
    if direct in NFL_TEAM_NAMES:
        return direct
    best_code, best_score = "", 0.0
    for code, aliases in NFL_TEAM_NAMES.items():
        score = max(_candidate_score(normalized, alias) for alias in aliases)
        if score > best_score:
            best_code, best_score = code, score
    return best_code if best_score >= 0.82 else ""


def _condition(text: str, pop: float) -> str:
    normalized = _norm(text)
    heavy = any(term in normalized for term in ("heavy", "storm", "thunder", "downpour"))
    if any(term in normalized for term in ("snow", "flurr", "sleet", "wintry", "ice")):
        return "Heavy Snow" if heavy or pop >= 0.70 else "Snow"
    if any(term in normalized for term in ("rain", "shower", "drizzle", "thunder")):
        return "Heavy Rain" if heavy or pop >= 0.70 else "Rain"
    if math.isfinite(pop) and pop >= 0.65:
        return "Rain"
    return "None"


def _parse_weather_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def parse_block(matchup: str, text: str) -> None:
        if "@" not in matchup:
            return
        away_raw, home_raw = [_clean_text(part) for part in matchup.split("@", 1)]
        away, home = _team_code(away_raw), _team_code(home_raw)
        if not away or not home or away == home:
            return
        temp_match = re.search(r"(-?\d+(?:\.\d+)?)\s*°\s*F", text, re.I)
        wind_match = re.search(r"(\d+(?:\.\d+)?)\s*Mph\b", text, re.I)
        pop_match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:P\s*\.?\s*O\s*\.?\s*P\.?|Precip)", text, re.I)
        temp = float(temp_match.group(1)) if temp_match else math.nan
        wind = float(wind_match.group(1)) if wind_match else math.nan
        pop = float(pop_match.group(1)) / 100.0 if pop_match else math.nan
        if not (math.isfinite(temp) or math.isfinite(wind) or math.isfinite(pop)):
            return
        key = (away, home)
        if key in seen:
            return
        seen.add(key)
        output.append({
            "away": away,
            "home": home,
            "temperature": temp,
            "wind": wind,
            "precipitation_probability": pop,
            "precipitation": _condition(text, pop),
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


def _read_disk_cache(now: float) -> list[dict[str, Any]]:
    if not CACHE_FILE.exists() or now - CACHE_FILE.stat().st_mtime > STALE_MAX_AGE:
        return []
    try:
        payload = json.loads(CACHE_FILE.read_text())
        cards = payload.get("cards", [])
        return cards if isinstance(cards, list) else []
    except Exception:
        return []


def _weather_cards() -> list[dict[str, Any]]:
    global _MEMORY_WEATHER
    now = time.time()
    with _LOCK:
        if _MEMORY_WEATHER and now - _MEMORY_WEATHER[0] <= WEATHER_TTL:
            return list(_MEMORY_WEATHER[1])

    if CACHE_FILE.exists() and now - CACHE_FILE.stat().st_mtime <= WEATHER_TTL:
        cached = _read_disk_cache(now)
        if cached:
            with _LOCK:
                _MEMORY_WEATHER = (now, cached)
            return list(cached)

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; EZPZ-Picks-NFL/4.2; +https://ezpzpicks.com)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
        "Cache-Control": "no-cache",
    }
    try:
        response = requests.get(COVERS_NFL_WEATHER_URL, headers=headers, timeout=(4, 12))
        response.raise_for_status()
        if len(response.text) < 500:
            raise RuntimeError("Covers returned an unexpectedly small NFL weather page")
        cards = _parse_weather_html(response.text)
        if cards:
            try:
                CACHE_FILE.write_text(json.dumps({"fetched": now, "cards": cards}))
            except Exception:
                pass
        else:
            cards = _read_disk_cache(now)
    except Exception:
        cards = _read_disk_cache(now)

    with _LOCK:
        _MEMORY_WEATHER = (now, cards)
    return list(cards)


def _match_weather(away: Any, home: Any) -> dict[str, Any]:
    away_code, home_code = _team_code(away), _team_code(home)
    if not away_code or not home_code:
        return {}
    for card in _weather_cards():
        if card.get("away") == away_code and card.get("home") == home_code:
            return dict(card)
    return {}


def install_covers_weather(builder: Any) -> None:
    """Install the NFL-only Covers weather pathway on the NFL builder."""
    if getattr(builder, "_EZPZ_NFL_COVERS_WEATHER", False):
        return

    original_schedule_defaults = builder._schedule_defaults

    def schedule_defaults(row: pd.Series | None) -> dict[str, Any]:
        defaults = dict(original_schedule_defaults(row))
        if row is None:
            return defaults
        try:
            away = row.get("Away Team", "")
            home = row.get("Home Team", "")
            weather = _match_weather(away, home)
            if not weather:
                return defaults

            temp = float(weather.get("temperature", math.nan))
            wind = float(weather.get("wind", math.nan))
            precip = _clean_text(weather.get("precipitation", "None")) or "None"
            if math.isfinite(temp):
                defaults["temperature"] = temp
            if math.isfinite(wind):
                defaults["wind"] = max(0.0, wind)
            defaults["precipitation"] = precip if precip in {"None", "Rain", "Heavy Rain", "Snow", "Heavy Snow"} else "None"
            defaults["weather_source"] = "Covers.com"

            game_id = _clean_text(defaults.get("game_id") or row.get("Game ID") or "")
            market_key = re.sub(r"[^A-Za-z0-9]+", "_", game_id)
            if market_key:
                session = builder.st.session_state
                temp_key = f"nfl_temp_{market_key}"
                wind_key = f"nfl_wind_{market_key}"
                precip_key = f"nfl_precip_{market_key}"
                if math.isfinite(temp) and temp_key not in session:
                    session[temp_key] = float(temp)
                if math.isfinite(wind) and wind_key not in session:
                    session[wind_key] = float(max(0.0, wind))
                if precip_key not in session:
                    session[precip_key] = defaults["precipitation"]
        except Exception:
            return defaults
        return defaults

    builder._schedule_defaults = schedule_defaults
    builder._EZPZ_NFL_COVERS_WEATHER = True


__all__ = ["install_covers_weather", "_parse_weather_html", "_team_code", "_match_weather"]
