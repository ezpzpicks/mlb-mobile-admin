"""Recover missing CFB schedule finals from ESPN's week-specific scoreboards.

The season-wide scoreboard can contain only the active week, and the season
schedule fallback can retain pregame rows. Match final scores by ESPN game ID;
leave existing settled scores, market fields, and future games untouched.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any, Callable

import pandas as pd


def _finished(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes", "completed"}


def _score(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _scoreboard_final(event: dict[str, Any]) -> tuple[float, float] | None:
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    competition = competitions[0]
    status = competition.get("status") or event.get("status") or {}
    if not _finished((status.get("type") or {}).get("completed")):
        return None
    competitors = {
        str(row.get("homeAway", "")).lower(): row
        for row in competition.get("competitors", [])
    }
    away = _score(competitors.get("away", {}).get("score"))
    home = _score(competitors.get("home", {}).get("score"))
    if away is None or home is None:
        return None
    return away, home


def preserve_saved_finals(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    """Do not replace a verified stored final with a stale pregame feed row."""
    if existing is None or existing.empty or incoming is None or incoming.empty:
        return incoming
    required = {"Season", "Game ID", "Completed", "Away Score", "Home Score"}
    if not required.issubset(existing.columns) or not required.issubset(incoming.columns):
        return incoming

    finals: dict[tuple[str, str], tuple[float, float]] = {}
    for _, row in existing.iterrows():
        away, home = _score(row["Away Score"]), _score(row["Home Score"])
        if _finished(row["Completed"]) and away is not None and home is not None:
            finals[(str(row["Season"]), str(row["Game ID"]))] = away, home

    repaired = incoming.copy()
    for index, row in repaired.iterrows():
        key = str(row["Season"]), str(row["Game ID"])
        if key not in finals:
            continue
        if _finished(row["Completed"]) and _score(row["Away Score"]) is not None and _score(row["Home Score"]) is not None:
            continue
        repaired.at[index, "Away Score"], repaired.at[index, "Home Score"] = finals[key]
        repaired.at[index, "Completed"] = True
    return repaired


def missing_final_updates(
    schedule: pd.DataFrame,
    seasons: set[int],
    today: date,
    fetch_week: Callable[[int, int, int], list[dict[str, Any]]],
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Return only rows with newly verified finals and a small coverage summary.

    `fetch_week(season, season_type, week)` fetches ESPN events. It can fail for
    one week without preventing other weeks from being recovered.
    """
    if schedule is None or schedule.empty:
        return pd.DataFrame(columns=schedule.columns if schedule is not None else []), {"candidates": 0, "updated": 0, "weeks": 0}

    required = {"Season", "Week", "Season Type", "Game Date", "Game ID", "Completed", "Away Score", "Home Score"}
    if not required.issubset(schedule.columns):
        return schedule.iloc[0:0].copy(), {"candidates": 0, "updated": 0, "weeks": 0}

    dates = pd.to_datetime(schedule["Game Date"], errors="coerce").dt.date
    season_numbers = pd.to_numeric(schedule["Season"], errors="coerce")
    weeks = pd.to_numeric(schedule["Week"], errors="coerce")
    settled = schedule["Completed"].map(_finished)
    away_scores = pd.to_numeric(schedule["Away Score"], errors="coerce")
    home_scores = pd.to_numeric(schedule["Home Score"], errors="coerce")
    candidate_mask = (
        season_numbers.isin(seasons)
        & dates.map(lambda day: day is not None and pd.notna(day) and day < today)
        & (~settled | away_scores.isna() | home_scores.isna())
        & weeks.between(0, 18)
        & schedule["Game ID"].astype(str).str.strip().ne("")
    )
    pending = schedule.loc[candidate_mask].copy()
    if pending.empty:
        return pending, {"candidates": 0, "updated": 0, "weeks": 0}

    def season_type(value: Any) -> int:
        return 3 if str(value).strip().lower() in {"3", "postseason", "post season"} else 2

    wanted: dict[str, list[Any]] = {}
    requests: set[tuple[int, int, int]] = set()
    for index, row in pending.iterrows():
        game_id = str(row["Game ID"]).strip()
        wanted.setdefault(game_id, []).append(index)
        requests.add((int(season_numbers.loc[index]), season_type(row["Season Type"]), int(weeks.loc[index])))

    finals: dict[str, tuple[float, float]] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(requests))) as executor:
        futures = {executor.submit(fetch_week, *request): request for request in requests}
        for future in as_completed(futures):
            try:
                events = future.result() or []
            except Exception:
                continue
            for event in events:
                game_id = str(event.get("id", "")).strip()
                if game_id in wanted:
                    score = _scoreboard_final(event)
                    if score is not None:
                        finals[game_id] = score

    updates = []
    for game_id, indexes in wanted.items():
        if game_id not in finals:
            continue
        away, home = finals[game_id]
        for index in indexes:
            row = schedule.loc[index].copy()
            row["Away Score"] = away
            row["Home Score"] = home
            row["Completed"] = True
            updates.append(row)
    changed = pd.DataFrame(updates, columns=schedule.columns)
    return changed, {"candidates": len(pending), "updated": len(changed), "weeks": len(requests)}
