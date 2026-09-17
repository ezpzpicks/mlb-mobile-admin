"""Focused recovery hooks for the interactive CFB builder.

The full-slate/batch model can warm large SportsDataverse assets, but a normal
Streamlit widget rerun must never wait for a season play-by-play parquet download.
This module also surfaces the Covers personnel data already used by the model so
an admin can see which starters/injuries were applied.
"""
from __future__ import annotations

import re
import time
from typing import Any

import pandas as pd


def _install_nonblocking_interactive_totals(builder: Any) -> None:
    """Use already-cached PBP metrics on interactive reruns; never download inline.

    ``cfb_total_regression._load_pbp_metrics`` has a synchronous cache-miss fallback
    to ``_download_open_asset_now``. On a fresh Render instance that can hold the
    selected-matchup spinner while an entire season file downloads. The builder's
    normal ``_pbp_team_metrics`` path already queues missing assets in the background,
    and the totals context deliberately does not cache an all-empty transient miss.
    """
    if getattr(builder, "_EZPZ_CFB_INTERACTIVE_TOTALS_NONBLOCKING", False):
        return

    try:
        from builders import cfb_total_regression as totals
    except Exception:
        return

    def cached_pbp_metrics(cfb_builder: Any, season: int, week: int | None) -> pd.DataFrame:
        try:
            frame = cfb_builder._pbp_team_metrics(int(season), week)
        except Exception:
            frame = pd.DataFrame()
        return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()

    totals._load_pbp_metrics = cached_pbp_metrics
    builder._EZPZ_CFB_INTERACTIVE_TOTALS_NONBLOCKING = True


def _install_covers_team_directory_fix(covers: Any) -> None:
    """Recover team injury URLs from Covers' current overview-link directory.

    Covers' NCAAF injuries landing page currently links each team to its overview
    route (``.../teams/main/<slug>``), while the original CFB parser only accepted
    links that already ended in ``/injuries``. That left the team directory empty
    for most schools, even though the separate Covers weather endpoint continued to
    work. Resolve either route shape, then always request the canonical injury page.
    """
    if getattr(covers, "_EZPZ_CFB_TEAM_DIRECTORY_RECOVERY", False):
        return

    original_directory = covers._directory

    def directory(builder: Any) -> list[dict[str, str]]:
        now = time.time()
        try:
            with covers._LOCK:
                cached = covers._DIRECTORY
                if cached and now - float(cached[0]) <= float(covers.DIRECTORY_TTL) and cached[1]:
                    return list(cached[1])
        except Exception:
            pass

        try:
            html = covers._fetch_html(builder, covers.COVERS_INJURIES_URL, ttl=covers.DIRECTORY_TTL)
            soup = covers.BeautifulSoup(html, "html.parser")
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
                if not slug:
                    continue
                rows[slug] = {
                    "slug": slug,
                    "label": covers._clean_text(anchor.get_text(" ", strip=True)) or covers._slug_label(slug),
                    "slug_label": covers._slug_label(slug),
                    "url": f"{covers.COVERS_BASE}/sport/football/ncaaf/teams/main/{slug}/injuries",
                }
            if rows:
                result = list(rows.values())
                with covers._LOCK:
                    covers._DIRECTORY = (now, result)
                return result
        except Exception:
            pass

        # Preserve the original fail-open behavior if Covers changes again.
        return original_directory(builder)

    covers._directory = directory
    try:
        with covers._LOCK:
            if covers._DIRECTORY and not covers._DIRECTORY[1]:
                covers._DIRECTORY = None
    except Exception:
        pass
    covers._EZPZ_CFB_TEAM_DIRECTORY_RECOVERY = True


def _cached_covers_report(covers: Any, team: str) -> dict[str, Any]:
    """Return only an already-fetched Covers report; this helper never hits network."""
    key = covers._norm(team)
    now = time.time()
    try:
        with covers._LOCK:
            hit = covers._TEAM_REPORTS.get(key)
            if hit and now - float(hit[0]) <= float(covers.STALE_MAX_AGE):
                return dict(hit[1])
    except Exception:
        pass
    return {}


def _covers_lineup_frame(covers: Any, report: dict[str, Any]) -> pd.DataFrame:
    starters = list(report.get("starters", []) or [])
    injuries = list(report.get("injuries", []) or [])
    rows: list[dict[str, str]] = []

    for starter in starters:
        player = covers._clean_text(starter.get("player"))
        if not player:
            continue
        injury = covers._injury_for(injuries, player)
        rows.append({
            "Unit": covers._clean_text(starter.get("unit")) or "Starter",
            "Pos": covers._clean_text(starter.get("position")).upper(),
            "Player": player,
            "Role": "Starter - last game",
            "Injury": covers._clean_text((injury or {}).get("status")) or "—",
        })

    for injury in injuries:
        player = covers._clean_text(injury.get("player"))
        if not player:
            continue
        already_listed = any(
            covers._player_matches(player, covers._clean_text(starter.get("player")))
            for starter in starters
        )
        if already_listed:
            continue
        rows.append({
            "Unit": "Injury report",
            "Pos": covers._clean_text(injury.get("position")).upper(),
            "Player": player,
            "Role": "Depth / injury report",
            "Injury": covers._clean_text(injury.get("status")) or "Listed",
        })

    return pd.DataFrame(rows, columns=["Unit", "Pos", "Player", "Role", "Injury"])


def _install_covers_lineup_display(builder: Any) -> None:
    if getattr(builder, "_EZPZ_CFB_COVERS_LINEUP_DISPLAY", False):
        return

    try:
        from builders import cfb_covers as covers
    except Exception:
        return

    original_editor = builder._personnel_editor

    def personnel_editor(team: str, base: Any, season: int, week: int, game_id: str, key: str):
        # default_personnel() runs immediately before this editor and is wrapped by
        # cfb_covers, so a successful report should already be in memory. Reading the
        # cache here avoids a second Covers request on every +/- odds interaction.
        report = _cached_covers_report(covers, team)
        if report.get("ok"):
            frame = _covers_lineup_frame(covers, report)
            if not frame.empty:
                builder.st.markdown("**Covers starters + injuries**")
                builder.st.dataframe(frame, hide_index=True, use_container_width=True)
                builder.st.caption(
                    "Covers.com starters from the last game plus the current injury report. "
                    "Matched entries are already included in the personnel adjustments below."
                )
        return original_editor(team, base, season, week, game_id, key)

    builder._personnel_editor = personnel_editor
    builder._EZPZ_CFB_COVERS_LINEUP_DISPLAY = True


def install_interactive_recovery(builder: Any) -> None:
    """Install selected-matchup responsiveness plus Covers personnel recovery."""
    if getattr(builder, "_EZPZ_CFB_INTERACTIVE_RECOVERY", False):
        return
    _install_nonblocking_interactive_totals(builder)
    try:
        from builders import cfb_covers as covers
        _install_covers_team_directory_fix(covers)
    except Exception:
        pass
    _install_covers_lineup_display(builder)
    builder._EZPZ_CFB_INTERACTIVE_RECOVERY = True


__all__ = [
    "install_interactive_recovery",
    "_covers_lineup_frame",
    "_install_covers_team_directory_fix",
]
