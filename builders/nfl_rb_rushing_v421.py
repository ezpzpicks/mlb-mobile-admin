"""Guarded NFL RB rushing-yards calibration layered on v4.20.

v4.21 changes only RB rushing yards:
- RB1: remove the extra live carry-role multiplier and trust the regression carry baseline
- RB2: remove the live carry-role multiplier and use 50% of the regression carry baseline
- preserve the existing YPC/efficiency and matchup adjustments exactly

The v4.19 WR1/WR2 receiving calibration, v4.20 RB receiving target hybrid,
unified grading, and all other markets remain unchanged.
"""
from __future__ import annotations

import math
from typing import Any

from builders import nfl_slot_matchups as slot_matchups
from builders import nfl_wr_receiving_v419 as v420  # installs v4.19/v4.20 first

MODEL_VERSION = "nfl-v4.21-rb-rushing-carry-calibration-2026-10-01"
RB2_CARRY_BASELINE_SCALE = 0.50


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else float(default)
    except Exception:
        return float(default)


def _row(rows: list[dict[str, Any]], market: str) -> dict[str, Any] | None:
    return next((row for row in rows if str(row.get("Market", "") or "") == market), None)


def _install() -> None:
    if getattr(slot_matchups, "_RB_RUSHING_V421_PATCHED", False):
        return

    # At import time these already include the v4.19 WR and v4.20 RB receiving patches.
    original_apply = slot_matchups._apply_slot_overlay
    original_install = slot_matchups.install_slot_matchup_layer

    def patched_apply(
        nfl_builder: Any,
        rows: list[dict[str, Any]],
        season: int,
        projection_week: int,
        opponent: str,
        slot: str,
        player_profile: dict[str, Any] | None = None,
        profiles_frame: Any = None,
    ) -> list[dict[str, Any]]:
        exact_slot = slot_matchups._slot(slot)
        pre_rush = _row(rows, "Rushing Yards")

        role_overlay = 1.0
        should_adjust = exact_slot in {"RB1", "RB2"} and pre_rush is not None
        if should_adjust:
            role_overlay = v420._live_role_overlay(pre_rush)
            if role_overlay <= 0.01:
                role_overlay = 1.0

        # Run the full existing v4.20 path first. This preserves every current
        # matchup and YPC/efficiency adjustment. We then change only the carry
        # component by the exact factor validated in the historical audit.
        result = original_apply(
            nfl_builder, rows, season, projection_week, opponent, slot,
            player_profile, profiles_frame,
        )

        if not should_adjust:
            return result

        row = _row(result, "Rushing Yards")
        if row is None:
            return result

        post_carries = max(0.0, _num(row.get("Projected Player Attempts"), 0.0))
        post_projection = max(0.0, _num(row.get("Projection"), 0.0))
        if post_carries <= 0.0 or post_projection <= 0.0:
            return result

        slot_scale = RB2_CARRY_BASELINE_SCALE if exact_slot == "RB2" else 1.0
        carry_factor = slot_scale / role_overlay
        new_carries = max(0.0, post_carries * carry_factor)
        new_projection = max(0.0, post_projection * carry_factor)

        row["Projected Player Attempts"] = round(new_carries, 2)
        # Efficiency, Matchup Index, and every matchup-derived field are left
        # untouched. Scaling projection by the same factor changes opportunity only.
        row["Raw Projection"] = round(new_projection, 2)
        row["Calibration Adjustment"] = 0.0
        row["Projection"] = round(new_projection, 2)
        row["Fair Line"] = nfl_builder._fair_line(new_projection, "Rushing Yards")
        row["_sd"] = nfl_builder._prop_sd(
            "Rushing Yards", new_projection, _num(row.get("Reliability"), 70.0)
        )

        note = (
            f"v4.21 RB rushing carry calibration: removed live carry-role overlay "
            f"{role_overlay:.2f}x; {exact_slot} regression carry baseline scale "
            f"{slot_scale:.2f}x; carries {post_carries:.2f} -> {new_carries:.2f}; "
            f"YPC and matchup unchanged"
        )
        previous = str(row.get("Confluence", "") or "").strip()
        row["Confluence"] = f"{previous} • {note}".strip(" •")
        return result

    def patched_install(nfl_builder: Any) -> None:
        original_install(nfl_builder)
        nfl_builder.MODEL_VERSION = MODEL_VERSION

    slot_matchups._apply_slot_overlay = patched_apply
    slot_matchups.install_slot_matchup_layer = patched_install
    slot_matchups._RB_RUSHING_V421_PATCHED = True


_install()
