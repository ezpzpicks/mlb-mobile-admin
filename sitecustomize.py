"""Render runtime hook for football database bootstrap.

Python imports ``sitecustomize`` during interpreter startup when this repository
is on ``PYTHONPATH``. The hook runs only for Streamlit and installs a safe
fallback for environments where the Google service account cannot own new Drive
files: NFL/CFB use isolated prefixed tabs inside the already-authorized shared
workbook unless a dedicated sport Sheet ID/name is explicitly configured.
"""

from __future__ import annotations

import os
import sys
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
        credentials_json = storage._secret_or_env("GOOGLE_CREDENTIALS")
        if not credentials_json:
            return {}

        client = storage._authorized_client(credentials_json)
        initialized: dict[str, str] = {}

        for requested_sport in sports:
            sport = _canonical_sport(requested_sport)
            config = storage_database_config(sport)
            sheet_id = str(config.get("sheet_id", "") or "")
            sheet_name = str(config.get("sheet_name", "") or "")
            prefix = str(config.get("namespace", "") or "")
            if not (sheet_id or sheet_name):
                continue

            try:
                workbook = client.open_by_key(sheet_id) if sheet_id else client.open(sheet_name)
            except Exception as exc:
                initialized[sport] = f"unavailable:{type(exc).__name__}"
                continue

            for tab_name, columns in storage._BOOTSTRAP_PUBLIC_TABS.items():
                physical_name = f"{prefix}{tab_name}" if prefix else tab_name
                try:
                    worksheet = workbook.worksheet(physical_name)
                except storage.gspread.WorksheetNotFound:
                    worksheet = workbook.add_worksheet(
                        title=physical_name,
                        rows=2000,
                        cols=max(20, len(columns) + 5),
                    )
                if columns:
                    values = worksheet.get_all_values()
                    if not values:
                        worksheet.update([list(columns)])

            initialized[sport] = str(getattr(workbook, "id", "") or sheet_id or sheet_name)

        return initialized

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
