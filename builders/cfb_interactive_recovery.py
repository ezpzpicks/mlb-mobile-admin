"""Focused recovery hooks for the interactive CFB builder.

The full-slate/batch model can warm large SportsDataverse assets, but a normal
Streamlit widget rerun must never wait for a season play-by-play parquet download.
This module also surfaces the Covers personnel data already used by the model so
an admin can see which starters/injuries were applied.
"""
from __future__ import annotations

import hashlib
import json
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


def _storage_metadata(row: dict[str, str]) -> dict[str, str]:
    def first(*names: str) -> str:
        for name in names:
            value = row.get(name)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    return {
        "date_key": first("Date", "date", "Record Date"),
        "game_key": first("Game Key", "gameKey", "Game ID", "gameId"),
        "game": first("Game", "game", "Game Label"),
        "market": first("Market", "market", "Bet Type", "Play Type"),
        "selection": first("Selection", "selection", "Play", "Side"),
        "result": first("Result", "result", "Status"),
        "snapshot_time": first("Snapshot Time ET", "snapshotTime", "Locked At", "Result Updated"),
    }


def _normalized_records(frame: pd.DataFrame | None, columns: list[str]) -> list[dict[str, str]]:
    if frame is None or frame.empty:
        return []
    output = frame.copy()
    for column in columns:
        if column not in output.columns:
            output[column] = ""
    output = output[columns].fillna("").astype(str)
    return [
        {column: str(value or "") for column, value in record.items()}
        for record in output.to_dict(orient="records")
    ]


def _json_key_path(column: str) -> str:
    safe = str(column).replace("\\", "\\\\").replace('"', '\\"')
    return f'$."{safe}"'


def _direct_dataset_requests(
    turso: Any,
    sport: str,
    dataset: str,
    frame: pd.DataFrame | None,
    columns: list[str],
    keys: list[str],
    saved_at: str,
) -> tuple[list[dict[str, Any]], int]:
    """Build small keyed UPDATE/INSERT statements without reading a whole dataset.

    The old compatibility path reconstructed and diffed every logical dataset before
    a save. If Turso was slow, each full-state read could retry for up to two minutes;
    a CFB save touches several datasets, so one button press could sit on a spinner for
    many minutes. This path updates only the keyed rows being saved.
    """
    records = _normalized_records(frame, columns)
    if not records:
        return [], 0

    q = turso._sql_text
    sport_sql = q(sport)
    dataset_sql = q(dataset)
    requests: list[dict[str, Any]] = []

    for row in records:
        key_predicates: list[str] = []
        for key in keys:
            key_predicates.append(
                f"json_extract(payload_json,{q(_json_key_path(key))})={q(row.get(key, ''))}"
            )
        where = (
            f"sport={sport_sql} AND dataset={dataset_sql}"
            + (" AND " + " AND ".join(key_predicates) if key_predicates else "")
        )

        payload = json.dumps(row, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        metadata = _storage_metadata(row)
        assignments = ",".join(
            [
                f"payload_json={q(payload)}",
                f"source_hash={q(digest)}",
                f"date_key={q(metadata['date_key'])}",
                f"game_key={q(metadata['game_key'])}",
                f"game={q(metadata['game'])}",
                f"market={q(metadata['market'])}",
                f"selection={q(metadata['selection'])}",
                f"result={q(metadata['result'])}",
                f"snapshot_time={q(metadata['snapshot_time'])}",
                f"imported_at={q(saved_at)}",
            ]
        )
        requests.append(
            {
                "type": "execute",
                "stmt": {"sql": f"UPDATE dataset_rows SET {assignments} WHERE {where}", "args": []},
            }
        )

        # `changes()` is scoped to the immediately preceding UPDATE on this Turso
        # stream. If no keyed row existed, append one at the next row index. This
        # keeps updates O(saved rows), rather than O(entire historical dataset).
        insert_sql = (
            "INSERT INTO dataset_rows "
            "(sport,dataset,row_index,payload_json,source_hash,date_key,game_key,game,market,selection,result,snapshot_time,imported_at) "
            "SELECT "
            f"{sport_sql},{dataset_sql},"
            f"COALESCE((SELECT MAX(row_index)+1 FROM dataset_rows WHERE sport={sport_sql} AND dataset={dataset_sql}),1),"
            f"{q(payload)},{q(digest)},{q(metadata['date_key'])},{q(metadata['game_key'])},"
            f"{q(metadata['game'])},{q(metadata['market'])},{q(metadata['selection'])},"
            f"{q(metadata['result'])},{q(metadata['snapshot_time'])},{q(saved_at)} "
            "WHERE changes()=0"
        )
        requests.append({"type": "execute", "stmt": {"sql": insert_sql, "args": []}})

    headers_json = json.dumps([str(column) for column in columns], separators=(",", ":"))
    manifest_sql = (
        "INSERT OR REPLACE INTO dataset_manifest "
        "(sport,dataset,source_workbook,source_worksheet,headers_json,row_count,imported_at,source_kind) "
        "SELECT "
        f"{sport_sql},{dataset_sql},'admin-turso-native',{dataset_sql},{q(headers_json)},"
        f"(SELECT COUNT(*) FROM dataset_rows WHERE sport={sport_sql} AND dataset={dataset_sql}),"
        f"{q(saved_at)},'turso'"
    )
    requests.append({"type": "execute", "stmt": {"sql": manifest_sql, "args": []}})
    return requests, len(records)


def _merge_session_cache(builder: Any, tab: str, columns: list[str], incoming: pd.DataFrame, keys: list[str]) -> None:
    """Keep the builder's local cache coherent after a direct keyed save."""
    if incoming is None or incoming.empty:
        return
    try:
        cache_key = f"cfb_sheet_cache::{tab}"
        existing = builder.st.session_state.get(cache_key)
        if isinstance(existing, pd.DataFrame):
            combined = pd.concat([existing.reindex(columns=columns), incoming.reindex(columns=columns)], ignore_index=True)
            if keys:
                combined = combined.drop_duplicates(subset=keys, keep="last")
            builder.st.session_state[cache_key] = combined.reindex(columns=columns).fillna("")
    except Exception:
        pass


def _save_result_direct(builder: Any, result: dict[str, Any], include_no_plays: bool = False) -> tuple[int, int]:
    """Persist one selected matchup with one bounded Turso round trip.

    This deliberately bypasses the legacy DataFrame-replacement compatibility path.
    The selected matchup already has every projection in memory; saving it should
    only update those rows, not reread and diff six historical datasets.
    """
    from shared import turso_storage as turso

    started = time.perf_counter()
    sport = "NCAAF"
    game = result["game"]
    season = int(game["Season"])
    week = int(game["Week"])
    game_id = str(game["Game ID"])

    slate = builder.slate_row(result)
    tracker = builder.tracker_rows(result, include_no_plays)
    personnel = pd.concat(
        [
            builder.personnel_row(result["away_personnel"], game["Away Team"], season, week, game_id),
            builder.personnel_row(result["home_personnel"], game["Home Team"], season, week, game_id),
        ],
        ignore_index=True,
    )

    specs: list[tuple[str, pd.DataFrame, list[str], list[str]]] = [
        (builder.SLATE_TAB, slate, list(builder.SLATE_COLUMNS), ["Date", "Game ID"]),
        (builder.PERSONNEL_TAB, personnel, list(builder.PERSONNEL_COLUMNS), ["Season", "Game ID", "Team"]),
    ]
    if not tracker.empty:
        specs.insert(1, (builder.TRACKER_TAB, tracker, list(builder.TRACKER_COLUMNS), ["Date", "Game ID", "Bet Type"]))

    covers = None
    starter_pending: list[dict[str, Any]] = []
    injury_pending: list[dict[str, Any]] = []
    try:
        from builders import cfb_covers as covers_module

        covers = covers_module
        starter_pending = list(covers._pending(builder, "starters"))
        injury_pending = list(covers._pending(builder, "injuries"))
        if starter_pending:
            specs.append(
                (
                    covers.STARTER_HISTORY_TAB,
                    pd.DataFrame(starter_pending).reindex(columns=covers.STARTER_HISTORY_COLUMNS),
                    list(covers.STARTER_HISTORY_COLUMNS),
                    ["Season", "Week", "Team", "Unit", "Position", "Player"],
                )
            )
        if injury_pending:
            specs.append(
                (
                    covers.INJURY_HISTORY_TAB,
                    pd.DataFrame(injury_pending).reindex(columns=covers.INJURY_HISTORY_COLUMNS),
                    list(covers.INJURY_HISTORY_COLUMNS),
                    ["Observed Date", "Team", "Player", "Status"],
                )
            )
    except Exception:
        covers = None

    saved_at = pd.Timestamp.utcnow().isoformat()
    requests: list[dict[str, Any]] = [
        {"type": "execute", "stmt": {"sql": "BEGIN IMMEDIATE", "args": []}}
    ]
    row_counts: dict[str, int] = {}
    for tab, frame, columns, keys in specs:
        tab_requests, count = _direct_dataset_requests(turso, sport, tab, frame, columns, keys, saved_at)
        requests.extend(tab_requests)
        row_counts[tab] = count
    requests.append({"type": "execute", "stmt": {"sql": "COMMIT", "args": []}})

    print(
        "[cfb-save] direct persistence prepared: "
        + ", ".join(f"{tab}={count}" for tab, count in row_counts.items())
    )

    # Interactive saves must never be allowed to spin for many minutes. Normal
    # Turso writes finish in well under this window; if the service is unhealthy,
    # two short attempts surface an error instead of holding the Streamlit session.
    turso._pipeline(requests, timeout=12.0, max_attempts=2)

    for tab, frame, columns, keys in specs:
        try:
            turso._invalidate_dataset_cache(sport, tab)
            turso._mark_dataset_known(sport, tab)
        except Exception:
            pass
        _merge_session_cache(builder, tab, columns, frame, keys)

    if covers is not None:
        try:
            if starter_pending:
                covers._pending(builder, "starters").clear()
            if injury_pending:
                covers._pending(builder, "injuries").clear()
        except Exception:
            pass

    print(f"[cfb-save] direct persistence completed in {time.perf_counter() - started:.3f}s")
    return len(slate), len(tracker)


def _install_fast_save(builder: Any) -> None:
    """Replace the selected-matchup save with the bounded keyed Turso path."""
    if getattr(builder, "_EZPZ_CFB_FAST_SAVE", False):
        return

    def save_result(result: dict[str, Any], include_no_plays: bool = False):
        return _save_result_direct(builder, result, include_no_plays)

    builder.save_result = save_result

    # Selected-matchup widget reruns should remain read-only until the explicit
    # save button is pressed. The automatic full-slate builder has its own save
    # path, so disabling this legacy helper does not disable slate persistence.
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
    """Install selected-matchup responsiveness, lineup display, and fast saves.

    Covers team resolution now lives only in builders/cfb_covers.py. Do not
    monkey-patch that resolver at runtime.
    """
    if getattr(builder, "_EZPZ_CFB_INTERACTIVE_RECOVERY", False):
        return
    _install_nonblocking_interactive_totals(builder)
    _install_covers_lineup_display(builder)
    _install_fast_save(builder)
    builder._EZPZ_CFB_INTERACTIVE_RECOVERY = True


__all__ = [
    "install_interactive_recovery",
    "_covers_lineup_frame",
    "_install_fast_save",
    "_save_result_direct",
]
