"""Render runtime hook for one-time sport database bootstrap.

Python imports ``sitecustomize`` during interpreter startup when this repository
is on ``PYTHONPATH``. Restrict the side effect to the Streamlit console process
so build tools, research scripts, and local Python commands remain untouched.
"""

from __future__ import annotations

import os
import sys


def _is_streamlit_runtime() -> bool:
    executable = os.path.basename(str(sys.argv[0] or "")).lower()
    return executable == "streamlit" or executable.startswith("streamlit-")


if _is_streamlit_runtime():
    try:
        from shared.storage import initialize_sport_workbooks

        initialized = initialize_sport_workbooks(("NFL", "CFB"))
        print(
            "Football database startup bootstrap ready: "
            + ", ".join(f"{sport}={value}" for sport, value in sorted(initialized.items()))
        )
    except Exception as exc:
        # Do not prevent the admin from starting if Google is temporarily
        # unavailable. The app-level bootstrap will retry on the next session.
        print(f"Football database startup bootstrap failed: {exc}")
