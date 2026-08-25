"""Shared EZPZ helpers plus narrowly scoped MLB runtime compatibility hooks."""

import runpy
from pathlib import Path

from .mlb_k_runtime_patch import run_mlb_builder_with_locked_k_regression


# app_mobile_admin.py imports the standard runpy module before importing this
# package. Patching the module attribute here therefore updates that same module
# object without requiring a large edit to the admin entrypoint. Only the MLB
# builder path is intercepted; every other runpy call behaves normally.
if not getattr(runpy, "_ezpz_v164_k_patch_installed", False):
    _ezpz_original_run_path = runpy.run_path

    def _ezpz_run_path(path_name, init_globals=None, run_name=None):
        path = Path(path_name)
        if path.name == "mlb_builder.py" and path.parent.name == "builders":
            if init_globals:
                raise RuntimeError("V16.4 MLB runtime patch does not accept custom init_globals.")
            return run_mlb_builder_with_locked_k_regression(path)
        return _ezpz_original_run_path(
            path_name,
            init_globals=init_globals,
            run_name=run_name,
        )

    runpy.run_path = _ezpz_run_path
    runpy._ezpz_v164_k_patch_installed = True
