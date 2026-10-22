#!/usr/bin/env python3
"""Reuse the existing Render cron for isolated NBA and NCAAF build processes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CHILD_FLAG = "EZPZ_SCHEDULED_BUILDER_CHILD"


def main():
    env = dict(os.environ, **{CHILD_FLAG: "1"})
    failures = 0
    # The quick NBA job goes first so a cold NCAAF data build cannot delay a
    # near-tipoff NBA refresh. A failure in either process does not skip the other.
    jobs = [("NBA", "run_nba_builder.py", 480), ("NCAAF", "run_ncaaf_builder.py", 900)]
    for sport, script, timeout in jobs:
        try:
            result = subprocess.run([sys.executable, "-u", str(ROOT / "scripts" / script), "--commit"],
                                    env=env, timeout=timeout, check=False)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
        except OSError:
            code = 126
        failures += int(code != 0)
        print(json.dumps({"event": "scheduled_builder_finished", "sport": sport, "exit_code": code}), flush=True)
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
