"""Non-blocking Covers enrichment for the interactive CFB builder.

The CFB build page should never wait on third-party HTML requests after the user
submits sportsbook lines.  The automatic slate path still warms current Covers
personnel and weather data before it evaluates games; interactive reruns consume
only those warmed in-memory values (or the existing persisted CFB personnel/base
weather fallbacks) so projections render immediately even after a cold deploy.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from typing import Any

import pandas as pd


def install_nonblocking_covers(builder: Any, covers: Any) -> None:
    if getattr(builder, "_CFB_NONBLOCKING_COVERS_INSTALLED", False):
        return

    # Preserve the real network-backed functions for the automatic slate warmer.
    network_team_report = covers._team_report
    network_weather_cards = covers._weather_cards
    covered_run_week = builder.run_week

    def cached_team_report(_builder: Any, team: str) -> dict[str, Any]:
        key = covers._norm(team)
        now = time.time()
        with covers._LOCK:
            hit = covers._TEAM_REPORTS.get(key)
            if hit and now - hit[0] <= covers.TEAM_TTL:
                return dict(hit[1])
        return {
            "team": team,
            "injuries": [],
            "starters": [],
            "url": "",
            "ok": False,
        }

    def cached_weather_cards(_builder: Any, league: str = "ncaaf") -> list[dict[str, Any]]:
        league_key = "nfl" if covers._clean_text(league).lower() == "nfl" else "ncaaf"
        now = time.time()
        with covers._LOCK:
            hit = covers._WEATHER.get(league_key)
            if hit and now - hit[0] <= covers.WEATHER_TTL:
                return list(hit[1])
        return []

    # The wrappers installed by cfb_covers resolve these names at call time.
    # Swapping them to cache-only readers removes all synchronous Covers HTTP
    # requests from normal Streamlit widget reruns and the Apply Lines & Odds path.
    covers._team_report = cached_team_report
    covers._weather_cards = cached_weather_cards

    def run_week(*args: Any, **kwargs: Any):
        schedule = kwargs.get("schedule")
        if isinstance(schedule, pd.DataFrame) and not schedule.empty:
            teams = list(dict.fromkeys(
                str(team).strip()
                for team in list(schedule.get("Away Team", [])) + list(schedule.get("Home Team", []))
                if str(team).strip()
            ))
            if teams:
                # Warm both teams concurrently before the existing Covers/run_week
                # wrapper runs.  It will then read these same values from cache.
                with ThreadPoolExecutor(
                    max_workers=min(6, len(teams)),
                    thread_name_prefix="ezpz-covers-cfb-warm",
                ) as pool:
                    futures = [pool.submit(network_team_report, builder, team) for team in teams]
                    for future in as_completed(futures):
                        try:
                            future.result()
                        except Exception:
                            pass
            try:
                network_weather_cards(builder, "ncaaf")
            except Exception:
                pass

        return covered_run_week(*args, **kwargs)

    builder.run_week = run_week
    builder._CFB_NONBLOCKING_COVERS_INSTALLED = True


__all__ = ["install_nonblocking_covers"]
