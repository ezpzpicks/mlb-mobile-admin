"""Render runtime hook for football database bootstrap.

Python imports ``sitecustomize`` during interpreter startup when this repository
is on ``PYTHONPATH``. The hook runs only for Streamlit and installs a safe
fallback for environments where the Google service account cannot own new Drive
files: NFL/CFB use isolated prefixed tabs inside the already-authorized shared
workbook unless a dedicated sport Sheet ID/name is explicitly configured.

The bootstrap is intentionally process-cached and uses one worksheet-list read
per workbook. Streamlit reruns the entrypoint for every widget interaction, so a
bootstrap that re-read every worksheet on every rerun could consume Google's
per-user Sheets quota before the MLB builder itself ran.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Iterable


def _is_streamlit_runtime() -> bool:
    executable = os.path.basename(str(sys.argv[0] or "")).lower()
    return executable == "streamlit" or executable.startswith("streamlit-")


def _canonical_sport(value: str | None) -> str:
    sport = str(value or "").strip().upper()
    if sport == "NCAAF":
        return "CFB"
    if sport == "NCAAM":
        return "CBB"
    return sport


def _install_shared_container_fallback(storage) -> None:
    original_config = storage.storage_database_config
    original_name = storage.storage_database_name
    original_get_or_create = storage.get_or_create_worksheet

    # ``app_mobile_admin.py`` calls initialize_sport_workbooks at the top of the
    # Streamlit script, which means once per widget rerun. Keep the result in the
    # imported storage module so only the first call in this process touches
    # Google Sheets. A lock also prevents duplicate initialization if two threads
    # reach startup at the same time.
    bootstrap_cache: dict[tuple[str, ...], dict[str, str]] = {}
    bootstrap_lock = threading.Lock()

    def storage_database_config(sport: str | None = None):
        selected = _canonical_sport(
            sport if sport is not None else storage.get_storage_sport()
        )
        config = dict(original_config(sport))
        config.setdefault("namespace", "")

        if selected not in {"NFL", "CFB"}:
            return config

        # Any explicit sport-specific ID/name means a true dedicated workbook
        # was configured; keep the original unprefixed behavior in that case.
        explicit_id = storage._sport_setting(selected, "ID")
        explicit_name = storage._sport_setting(selected, "NAME")
        if explicit_id or explicit_name:
            return config

        shared_id = storage._secret_or_env("GOOGLE_SHEET_ID")
        shared_name = storage._secret_or_env("GOOGLE_SHEET_NAME")
        if not (shared_id or shared_name):
            return config

        return {
            "sport": selected,
            "sheet_id": shared_id,
            "sheet_name": shared_name,
            "namespace": f"{selected.lower()}_",
        }

    def storage_database_name(sport: str | None = None) -> str:
        config = storage_database_config(sport)
        namespace = str(config.get("namespace", "") or "")
        if namespace:
            container = str(config.get("sheet_name") or config.get("sheet_id") or "shared workbook")
            return f"{config['sport']} namespace in {container}"
        return original_name(sport)

    def get_or_create_worksheet(tab_name: str, columns: Iterable[str]):
        config = storage_database_config()
        prefix = str(config.get("namespace", "") or "")
        physical_name = str(tab_name)
        if prefix and not physical_name.startswith(prefix):
            physical_name = f"{prefix}{physical_name}"
        return original_get_or_create(physical_name, columns)

    def initialize_sport_workbooks(sports: Iterable[str] = ("NFL", "CFB")) -> dict[str, str]:
        requested = tuple(
            sport
            for sport in (_canonical_sport(value) for value in sports)
            if sport in {"NFL", "CFB", "CBB"}
        )
        if not requested:
            return {}

        cached = bootstrap_cache.get(requested)
        if cached is not None:
            return dict(cached)

        with bootstrap_lock:
            cached = bootstrap_cache.get(requested)
            if cached is not None:
                return dict(cached)

            credentials_json = storage._secret_or_env("GOOGLE_CREDENTIALS")
            if not credentials_json:
                bootstrap_cache[requested] = {}
                return {}

            client = storage._authorized_client(credentials_json)
            initialized: dict[str, str] = {}

            # NFL and CFB can intentionally point at the same shared workbook.
            # Group by workbook so we fetch its worksheet metadata only once.
            workbook_groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
            for sport in requested:
                config = storage_database_config(sport)
                sheet_id = str(config.get("sheet_id", "") or "")
                sheet_name = str(config.get("sheet_name", "") or "")
                prefix = str(config.get("namespace", "") or "")
                if not (sheet_id or sheet_name):
                    continue
                workbook_groups.setdefault((sheet_id, sheet_name), []).append((sport, prefix))

            for (sheet_id, sheet_name), sport_entries in workbook_groups.items():
                try:
                    workbook = client.open_by_key(sheet_id) if sheet_id else client.open(sheet_name)

                    # One metadata request replaces repeated workbook.worksheet()
                    # + get_all_values() calls for every bootstrap tab.
                    existing = {
                        str(getattr(ws, "title", "") or ""): ws
                        for ws in workbook.worksheets()
                    }

                    for sport, prefix in sport_entries:
                        for tab_name, columns in storage._BOOTSTRAP_PUBLIC_TABS.items():
                            physical_name = f"{prefix}{tab_name}" if prefix else tab_name
                            worksheet = existing.get(physical_name)
                            if worksheet is None:
                                worksheet = workbook.add_worksheet(
                                    title=physical_name,
                                    rows=2000,
                                    cols=max(20, len(columns) + 5),
                                )
                                existing[physical_name] = worksheet
                                # A newly created worksheet is empty by definition,
                                # so write the header directly without a read first.
                                if columns:
                                    worksheet.update([list(columns)])

                        initialized[sport] = str(
                            getattr(workbook, "id", "") or sheet_id or sheet_name
                        )
                except Exception as exc:
                    for sport, _prefix in sport_entries:
                        initialized[sport] = f"unavailable:{type(exc).__name__}"

            bootstrap_cache[requested] = dict(initialized)
            return dict(initialized)

    storage.storage_database_config = storage_database_config
    storage.storage_database_name = storage_database_name
    storage.get_or_create_worksheet = get_or_create_worksheet
    storage.initialize_sport_workbooks = initialize_sport_workbooks


if _is_streamlit_runtime():
    try:
        from shared import storage

        _install_shared_container_fallback(storage)
        initialized = storage.initialize_sport_workbooks(("NFL", "CFB"))
        print(
            "Football database startup bootstrap ready: "
            + ", ".join(f"{sport}={value}" for sport, value in sorted(initialized.items()))
        )
    except Exception as exc:
        # Do not prevent the admin from starting if Google is temporarily
        # unavailable. The app-level storage functions can still retry later.
        print(f"Football database startup bootstrap failed: {exc}")
