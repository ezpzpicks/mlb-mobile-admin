"""Focused recovery hooks for the interactive CFB builder.

The full-slate/batch model can warm large SportsDataverse assets, but a normal
Streamlit widget rerun must never wait for a season play-by-play parquet download.
This module also surfaces the Covers personnel data already used by the model so
an admin can see which starters/injuries were applied.
"""
from __future__ import annotations

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


def _install_fast_save(builder: Any) -> None:
    """Batch one explicit CFB save into a single Turso write transaction.

    The normal save path updates the slate, tracker, two personnel snapshots, and
    the Covers starter/injury history. Those are separate logical datasets, and
    committing each one independently can make a single button click feel hung.
    Turso already supports queued dataset replacements, so collect the whole save
    and commit it once. Interactive reruns remain read-only until Save is pressed.
    """
    if getattr(builder, "_EZPZ_CFB_FAST_SAVE", False):
        return

    try:
        from shared.turso_storage import batch_dataset_writes
    except Exception:
        return

    original_save_result = builder.save_result

    def save_result(*args: Any, **kwargs: Any):
        started = time.perf_counter()
        with batch_dataset_writes():
            result = original_save_result(*args, **kwargs)
        print(f"[cfb-save] batched persistence completed in {time.perf_counter() - started:.3f}s")
        return result

    builder.save_result = save_result

    # The legacy helper persisted the visible matchup during normal Streamlit
    # reruns. That means clicking Save could perform an automatic slate write and
    # then immediately perform the explicit multi-dataset save. The automatic
    # slate builder has its own persistence path, so selected-matchup edits should
    # stay read-only until the user presses Save.
    if hasattr(builder, "_auto_save_selected_projection"):
        builder._auto_save_selected_projection = lambda _result: None

    builder._EZPZ_CFB_FAST_SAVE = True


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
    """Install selected-matchup responsiveness, fast saves, and Covers lineup fixes."""
    if getattr(builder, "_EZPZ_CFB_INTERACTIVE_RECOVERY", False):
        return
    _install_nonblocking_interactive_totals(builder)
    _install_covers_lineup_display(builder)
    _install_fast_save(builder)
    builder._EZPZ_CFB_INTERACTIVE_RECOVERY = True


__all__ = ["install_interactive_recovery", "_covers_lineup_frame", "_install_fast_save"]
