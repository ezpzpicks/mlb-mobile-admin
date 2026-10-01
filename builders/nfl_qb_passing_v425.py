"""Guarded QB Passing Yards efficiency-matchup neutralization layered on v4.24.

v4.25 changes only QB Passing Yards:
- preserve the existing calibrated pass-attempt opportunity projection;
- preserve any opportunity-side matchup adjustment;
- remove only the Passing Yards efficiency/YPA matchup multiplier;
- leave the underlying calibrated/base YPA estimate unchanged apart from reversing
  the matchup overlay already applied by the slot layer.

This module is imported by the existing v4.24 loader after v4.19-v4.24 are installed.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from builders import nfl_slot_matchups as slot_matchups

MODEL_VERSION = "nfl-v4.25-qb-passing-efficiency-matchup-neutral-2026-10-01"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else float(default)
    except Exception:
        return float(default)


def _row(rows: list[dict[str, Any]], market: str) -> dict[str, Any] | None:
    return next((row for row in rows if str(row.get("Market", "") or "") == market), None)


def _matchup_factor(
    nfl_builder: Any,
    pre_row: dict[str, Any] | None,
    season: int,
    projection_week: int,
    opponent: str,
    slot: str,
    market: str,
) -> float:
    if pre_row is None or projection_week <= 1:
        return 1.0
    profile = slot_matchups.slot_matchup_profile(
        nfl_builder, season, projection_week, opponent, slot, market
    )
    slot_factor = slot_matchups._factor(profile)
    factor, _, _, _ = slot_matchups._combined_matchup_factor(pre_row, market, slot_factor)
    return max(0.01, _num(factor, 1.0))


def _qb_passing_components(
    nfl_builder: Any,
    pre_rows: list[dict[str, Any]],
    season: int,
    projection_week: int,
    opponent: str,
    exact_slot: str,
    player_profile: dict[str, Any] | None,
    profiles_frame: Any,
) -> tuple[float, float]:
    """Reconstruct the exact opportunity and efficiency factors used by v4.18."""
    if exact_slot != "QB":
        return 1.0, 1.0

    pre_attempts = _row(pre_rows, "Passing Attempts")
    pre_yards = _row(pre_rows, "Passing Yards")
    if pre_yards is None:
        return 1.0, 1.0

    pass_attempt_factor = _matchup_factor(
        nfl_builder, pre_attempts, season, projection_week,
        opponent, exact_slot, "Passing Attempts"
    )
    passing_yards_factor = _matchup_factor(
        nfl_builder, pre_yards, season, projection_week,
        opponent, exact_slot, "Passing Yards"
    )

    base_opp_factor = max(0.01, pass_attempt_factor)
    base_eff_factor = max(0.01, passing_yards_factor / base_opp_factor)

    profiles = profiles_frame if profiles_frame is not None else pd.DataFrame()
    variable_tiers = slot_matchups._yardage_variable_tiers(
        nfl_builder,
        profiles,
        player_profile or {},
        exact_slot,
        "Passing Yards",
    )
    opp_factor = slot_matchups._variable_scaled_matchup_factor(
        base_opp_factor, variable_tiers.get("opportunity")
    )
    eff_factor = slot_matchups._variable_scaled_matchup_factor(
        base_eff_factor, variable_tiers.get("efficiency")
    )
    return max(0.01, _num(opp_factor, 1.0)), max(0.01, _num(eff_factor, 1.0))


def _neutralize_qb_passing_efficiency_matchup(
    nfl_builder: Any,
    rows: list[dict[str, Any]],
    exact_slot: str,
    opportunity_factor: float,
    efficiency_factor: float,
) -> None:
    if exact_slot != "QB":
        return
    if not math.isfinite(efficiency_factor) or efficiency_factor <= 0.05:
        return
    if abs(efficiency_factor - 1.0) < 0.0005:
        return

    row = _row(rows, "Passing Yards")
    if row is None:
        return

    old_projection = max(0.0, _num(row.get("Projection"), 0.0))
    old_efficiency = max(0.0, _num(row.get("Efficiency"), 0.0))
    if old_projection <= 0.0:
        return

    # Reverse only the efficiency-side matchup adjustment. Projected Player
    # Attempts already contains any opportunity-side matchup and stays unchanged.
    new_projection = max(0.0, old_projection / efficiency_factor)
    new_efficiency = (
        max(0.0, old_efficiency / efficiency_factor)
        if old_efficiency > 0.0 else old_efficiency
    )

    if old_efficiency > 0.0:
        row["Efficiency"] = round(new_efficiency, 3)
    row["Raw Projection"] = round(new_projection, 2)
    row["Calibration Adjustment"] = 0.0
    row["Projection"] = round(new_projection, 2)
    row["Fair Line"] = nfl_builder._fair_line(new_projection, "Passing Yards")
    row["_sd"] = nfl_builder._prop_sd(
        "Passing Yards", new_projection, _num(row.get("Reliability"), 70.0)
    )
    row["Matchup Index"] = round(opportunity_factor, 3)

    previous = str(row.get("Confluence", "") or "").strip()
    note = (
        f"v4.25 QB passing efficiency matchup neutralization: reversed YPA matchup "
        f"{efficiency_factor:.3f}x; preserved opportunity matchup {opportunity_factor:.3f}x "
        f"and projected pass attempts; Efficiency {old_efficiency:.3f} -> {new_efficiency:.3f}; "
        f"Passing Yards {old_projection:.2f} -> {new_projection:.2f}"
    )
    row["Confluence"] = f"{previous} • {note}".strip(" •")


def _install() -> None:
    if getattr(slot_matchups, "_QB_PASSING_V425_PATCHED", False):
        return

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
        pre_rows = [dict(row) for row in rows]
        opportunity_factor, efficiency_factor = _qb_passing_components(
            nfl_builder,
            pre_rows,
            season,
            projection_week,
            opponent,
            exact_slot,
            player_profile,
            profiles_frame,
        )
        result = original_apply(
            nfl_builder,
            rows,
            season,
            projection_week,
            opponent,
            slot,
            player_profile,
            profiles_frame,
        )
        _neutralize_qb_passing_efficiency_matchup(
            nfl_builder,
            result,
            exact_slot,
            opportunity_factor,
            efficiency_factor,
        )
        return result

    def patched_install(nfl_builder: Any) -> None:
        original_install(nfl_builder)
        nfl_builder.MODEL_VERSION = MODEL_VERSION

    slot_matchups._apply_slot_overlay = patched_apply
    slot_matchups.install_slot_matchup_layer = patched_install
    slot_matchups._QB_PASSING_V425_PATCHED = True


_install()
