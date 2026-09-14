"""Render startup hooks for the EZPZ admin.

Production data persistence is Turso-only. This module intentionally contains
no Google Sheets bootstrap, quota handling, or shared-workbook fallback logic.
"""

from __future__ import annotations

import os
import sys


def _is_streamlit_runtime() -> bool:
    executable = os.path.basename(str(sys.argv[0] or "")).lower()
    return executable == "streamlit" or executable.startswith("streamlit-")


if _is_streamlit_runtime():
    try:
        from shared import storage

        initialized = storage.initialize_sport_workbooks(("NFL", "CFB", "CBB"))
        print(
            "Turso sport storage startup ready: "
            + ", ".join(f"{sport}={value}" for sport, value in sorted(initialized.items()))
        )
    except Exception as exc:
        # Keep the UI bootable so the storage error is visible in the app/logs.
        # There is deliberately no Google Sheets fallback.
        print(f"Turso sport storage startup failed: {exc}")

    try:
        from shared.mlb_builder_resume import install_mlb_builder_resume

        install_mlb_builder_resume()
        print("MLB builder recovery checkpointing ready")
    except Exception as exc:
        print(f"MLB builder recovery checkpointing failed: {exc}")
