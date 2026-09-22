"""Render startup hooks for the EZPZ admin.

Production data persistence is Turso-only. This module intentionally contains
no Google Sheets bootstrap, quota handling, or shared-workbook fallback logic.
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse


def _is_streamlit_runtime() -> bool:
    executable = os.path.basename(str(sys.argv[0] or "")).lower()
    return executable == "streamlit" or executable.startswith("streamlit-")


def _log_turso_env_visibility() -> None:
    """Log only Turso env key presence/length and safe URL hostname, never secret values."""
    names = (
        "TURSO_DATABASE_URL",
        "TURSO_URL",
        "turso_TURSO_DATABASE_URL",
        "DATABASE_URL",
        "TURSO_AUTH_TOKEN",
        "TURSO_DATABASE_AUTH_TOKEN",
        "turso_TURSO_AUTH_TOKEN",
        "TURSO_TOKEN",
        "DATABASE_AUTH_TOKEN",
    )
    details = []
    for name in names:
        present = name in os.environ
        value = str(os.environ.get(name, "") or "")
        details.append(f"{name}=present:{present},len:{len(value)}")
    print("Turso env visibility: " + " | ".join(details))

    for name in ("TURSO_DATABASE_URL", "TURSO_URL", "turso_TURSO_DATABASE_URL", "DATABASE_URL"):
        raw = str(os.environ.get(name, "") or "").strip()
        if not raw:
            continue
        normalized = "https://" + raw[len("libsql://") :] if raw.startswith("libsql://") else raw
        try:
            host = urlparse(normalized).netloc or "invalid-url"
        except Exception:
            host = "invalid-url"
        print(f"Turso URL target: key={name}, host={host}")
        break




if _is_streamlit_runtime():
    _log_turso_env_visibility()

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
