"""Non-blocking external-data enrichment for the interactive CFB builder.

The CFB build page should never wait on third-party network requests after the user
submits sportsbook lines. Automatic/background paths can still warm Covers and
open-data caches, while interactive reruns consume whatever is already available
and fall back cleanly until the warmup completes.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from typing import Any

import pandas as pd


def _install_nonblocking_totals(builder: Any) -> None:
    """Keep the independent totals model from synchronously downloading PBP files.

    ``cfb_total_regression`` added a blocking fallback that calls
    ``_download_open_asset_now`` whenever the PBP cache is empty. On a fresh Render
    instance that can require full prior/current-season downloads before the first
    projection is displayed. The normal builder loader already queues those same
    files on its background executor, so interactive projections should use that
    non-blocking path instead.
    """
    if getattr(builder, "_CFB_NONBLOCKING_TOTALS_INSTALLED", False):
        return

    try:
        from builders import cfb_total_regression as totals
    except Exception:
        return

    def nonblocking_pbp_metrics(cfb_builder: Any, season: int, week: int | None) -> pd.DataFrame:
        try:
            frame = cfb_builder._pbp_team_metrics(int(season), week)
        except Exception:
            frame = pd.DataFrame()
        return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()

    # _metric_context resolves _load_pbp_metrics at call time, so replacing this
    # helper is enough to remove the blocking download without changing the model,
    # coefficients, market calibration, or the background open-data warmer.
    totals._load_pbp_metrics = nonblocking_pbp_metrics
    builder._CFB_NONBLOCKING_TOTALS_INSTALLED = True


def install_nonblocking_covers(builder: Any, covers: Any) -> None:
    if getattr(builder, "_CFB_NONBLOCKING_COVERS_INSTALLED", False):
        return

    # The totals regression is installed before this hook in app_mobile_admin.py.
    # Patch its cache-miss behavior here so the first interactive projection cannot
    # be held up by a large SportsDataverse play-by-play download.
    _install_nonblocking_totals(builder)

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
                # wrapper runs. It will then read these same values from cache.
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
